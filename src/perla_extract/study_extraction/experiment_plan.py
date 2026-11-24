"""Validate an experiment and resolve every run before creating output.

The plan records unique provider attempts. Paired comparisons may refer to the
same baseline attempt; they never schedule a second copy of that extraction.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from .comparison import _settings
from .comparison_study import ComparisonStudy, FactorContrast, same_setting
from .configuration import (
    ExtractionConfig,
    merge_deployments,
    parse_settings,
    resolve_config,
    tomllib,
)
from .models import StrictModel
from .reference_dataset import verify_dataset
from .reference_io import load_reference
from .run_artifacts import file_digest

_MANAGED = {
    "pdf",
    "supplement",
    "output_dir",
    "document_cache_dir",
    "model_cache_dir",
    "dry_run",
    "refresh_document_cache",
}


class Paper(StrictModel):
    pdf: str
    reference: str
    supplement: str | None = None


class CampaignBudget(StrictModel):
    """Admission limit, including money and unresolved reservations from prior work."""

    max_cost_usd: float = Field(gt=0, allow_inf_nan=False)
    prior_known_cost_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    prior_unknown_reservation_usd: float = Field(default=0, ge=0, allow_inf_nan=False)


class ExperimentSettings(StrictModel):
    output: str
    config: str | None = None
    repeats: int = Field(default=1, ge=1)
    repeat_subset: bool = False
    order_seed: int = 0
    extraction: dict[str, Any] = Field(default_factory=dict)
    stages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tracing: dict[str, Any] = Field(default_factory=dict)
    control: dict[str, Any] | None = None
    treatment: dict[str, Any] | None = None
    arms: dict[str, dict[str, Any]] = Field(default_factory=dict)
    baseline: str = "control"
    papers: list[Paper] = Field(default_factory=list)
    dataset: str | None = None
    sources: str | None = None
    budget: CampaignBudget | None = None

    @model_validator(mode="after")
    def complete_design(self):
        if self.arms:
            if self.control is not None or self.treatment is not None:
                raise ValueError("Use [arms.NAME] or [control]/[treatment], not both")
            if len(self.arms) < 2 or self.baseline not in self.arms:
                raise ValueError("Declare at least two arms and name their baseline")
        elif (
            self.control is None or self.treatment is None or self.baseline != "control"
        ):
            raise ValueError("Declare both [control] and [treatment], or named arms")
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]*", name) for name in self.methods):
            raise ValueError(
                "Arm names must start with a lowercase letter and use a-z, 0-9, _ or -"
            )
        if bool(self.papers) == bool(self.dataset):
            raise ValueError("Use either [[papers]] or dataset with sources")
        if self.dataset is not None and not self.sources:
            raise ValueError("dataset requires a sources folder")
        if self.sources and not self.dataset:
            raise ValueError("sources requires dataset")
        if self.repeat_subset and (not self.dataset or self.repeats != 1):
            raise ValueError("repeat_subset requires a dataset and repeats = 1")
        return self

    @property
    def methods(self) -> dict[str, dict[str, Any]]:
        return self.arms or {
            "control": self.control or {},
            "treatment": self.treatment or {},
        }


@dataclass(frozen=True)
class Attempt:
    paper_id: str
    repeat: int
    arm: str
    reference: Path
    config: ExtractionConfig
    reservation_usd: float | None = None

    @property
    def key(self) -> str:
        return f"{self.paper_id}/{self.arm}/{self.repeat}"


@dataclass(frozen=True)
class Experiment:
    source: Path
    output: Path
    studies: dict[str, ComparisonStudy]
    attempts: list[Attempt]
    inputs: dict[Path, str]
    settings: ExperimentSettings
    repeat_papers: list[str]
    resume: bool = False
    dataset_role: str | None = None

    @property
    def study(self) -> ComparisonStudy:
        return next(iter(self.studies.values()))

    @property
    def legacy(self) -> bool:
        return not self.settings.arms

    def ordered_attempts(self) -> list[Attempt]:
        """Shuffle papers and arms within each repeat; finish primary runs first."""
        rng = random.Random(self.settings.order_seed)
        result = []
        for repeat in sorted({a.repeat for a in self.attempts}):
            papers = list(
                dict.fromkeys(a.paper_id for a in self.attempts if a.repeat == repeat)
            )
            rng.shuffle(papers)
            for paper in papers:
                block = [
                    a
                    for a in self.attempts
                    if a.repeat == repeat and a.paper_id == paper
                ]
                rng.shuffle(block)
                result.extend(block)
        return result


def _path(directory: Path, value: str) -> Path:
    if not value.strip():
        raise ValueError("Input and output paths must not be empty")
    return (directory / value).resolve()


def _arm_settings(settings: ExperimentSettings, arm: str, directory: Path) -> dict:
    base = {}
    if settings.config:
        method_path = _path(directory, settings.config)
        shared = tomllib.loads(method_path.read_text(encoding="utf-8"))
        if set(shared) - {"extraction", "stages", "tracing"}:
            raise ValueError("config must be an ordinary extraction configuration")
        base = parse_settings(shared, method_path.parent)
    overrides = dict(settings.methods[arm])
    overrides.pop("reservation_usd", None)
    arm_stages = overrides.pop("stages", {})
    if not isinstance(arm_stages, dict):
        raise ValueError(f"{arm}.stages must contain stage tables")
    options = settings.extraction | overrides
    if forbidden := _MANAGED & (base.keys() | options.keys()):
        raise ValueError(
            f"Put sources in [[papers]]; run paths and caches are automatic: {', '.join(sorted(forbidden))}"
        )
    shared = parse_settings(
        {"extraction": options, "stages": settings.stages, "tracing": settings.tracing},
        directory,
    )
    arm_options = parse_settings({"stages": arm_stages}, directory)
    deployments = merge_deployments(
        base.get("deployments", {}), shared.get("deployments", {})
    )
    deployments = merge_deployments(deployments, arm_options.get("deployments", {}))
    return base | shared | {"deployments": deployments}


def _contrasts(left: ExtractionConfig, right: ExtractionConfig) -> dict:
    a, b = (
        _settings({"scientific_settings": c.scientific_settings()})
        for c in (left, right)
    )
    factors = {
        name: FactorContrast(control=a.get(name), treatment=b.get(name))
        for name in a.keys() | b.keys()
        if not same_setting(a.get(name), b.get(name))
    }
    if not factors:
        raise ValueError(
            "Control and treatment are identical; change an enabled setting in the candidate arm"
        )
    return factors


def prepare(path: Path, *, resume: bool = False) -> Experiment:
    """Verify the entire source/reference roster without parsing or model requests."""
    path = path.resolve()
    settings = ExperimentSettings.model_validate(
        tomllib.loads(path.read_text(encoding="utf-8"))
    )
    output = _path(path.parent, settings.output)
    if output.exists() and not resume:
        raise ValueError(
            f"Output already exists: {output}; use --resume or choose a new output"
        )
    if resume and not (output / "plan.json").is_file():
        raise ValueError(f"No frozen experiment to resume: {output}")
    methods = {
        arm: _arm_settings(settings, arm, path.parent) for arm in settings.methods
    }
    inputs = {path: file_digest(path)}
    if settings.config:
        config_path = _path(path.parent, settings.config)
        inputs[config_path] = file_digest(config_path)
    papers = settings.papers
    repeat_papers = []
    dataset_role = None
    if settings.dataset:
        dataset_path = _path(path.parent, settings.dataset)
        value, resolved = verify_dataset(
            dataset_path, _path(path.parent, settings.sources)
        )
        dataset_role = value["role"]
        papers = [
            Paper.model_validate({k: v for k, v in row.items() if k != "paper_id"})
            for row in resolved
        ]
        repeat_papers = (
            [r["paper_id"] for r in value["papers"] if r["repeat_check"]]
            if settings.repeat_subset
            else []
        )
        if settings.repeat_subset and not repeat_papers:
            raise ValueError("The dataset has no selected repeatability papers")
        for source in (
            dataset_path,
            _path(dataset_path.parent, value["reference_release"]),
        ):
            inputs[source] = file_digest(source)
    reservations = {}
    for arm, method in settings.methods.items():
        reservation = method.get("reservation_usd")
        if reservation is not None:
            if (
                isinstance(reservation, bool)
                or not isinstance(reservation, (int, float))
                or not 0 < reservation < float("inf")
            ):
                raise ValueError(
                    f"{arm}.reservation_usd must be a finite positive number"
                )
        if settings.budget is not None and reservation is None:
            raise ValueError(f"A capped experiment requires {arm}.reservation_usd")
        reservations[arm] = reservation
    attempts = []
    paper_ids = []
    splits = set()
    seen_sources: set[str] = set()
    for index, paper in enumerate(papers, 1):
        pdf = _path(path.parent, paper.pdf)
        supplement = _path(path.parent, paper.supplement) if paper.supplement else None
        reference = _path(path.parent, paper.reference)
        _, benchmark = load_reference(reference)
        if benchmark is None or not benchmark.source_sha256:
            raise ValueError(
                f"{reference}: use a finalized reference directory with source PDF hashes"
            )
        if benchmark.paper_id in paper_ids:
            raise ValueError(f"Duplicate reference paper: {benchmark.paper_id}")
        paper_ids.append(benchmark.paper_id)
        splits.add(benchmark.split)
        sources = [s for s in (pdf, supplement) if s is not None]
        if any(not s.is_file() for s in sources):
            raise ValueError(
                f"{benchmark.paper_id}: source files missing. Set pdf/supplement to the original article and SI; the reference contains answers, not PDFs."
            )
        hashes = [file_digest(s) for s in sources]
        if sorted(hashes) != sorted(benchmark.source_sha256):
            raise ValueError(
                f"{benchmark.paper_id}: PDF/SI bytes do not match the reference sources. Reference: {reference}; pdf: {pdf}; supplement: {supplement or 'none'}"
            )
        if seen_sources.intersection(hashes):
            raise ValueError(
                f"{benchmark.paper_id}: source PDF reused by another paper"
            )
        seen_sources.update(hashes)
        for source in [
            *sources,
            reference / "manifest.json",
            reference / "ground_truth.json",
        ]:
            inputs[source] = file_digest(source)
        count = 2 if benchmark.paper_id in repeat_papers else settings.repeats
        for repeat in range(1, count + 1):
            for arm, method in methods.items():
                run = output / "runs" / f"paper-{index:03d}" / f"{arm}-{repeat}"
                config = resolve_config(
                    **method,
                    pdf=pdf,
                    supplement=supplement,
                    output_dir=run,
                    document_cache_dir=output / "cache/documents",
                    model_cache_dir=run / "model-cache",
                )
                attempts.append(
                    Attempt(
                        benchmark.paper_id,
                        repeat,
                        arm,
                        reference,
                        config,
                        reservations[arm],
                    )
                )
    if len(splits) > 1:
        raise ValueError(
            "Use one reference split per experiment; keep development and test papers separate"
        )
    first = {
        a.arm: a.config
        for a in attempts
        if a.paper_id == paper_ids[0] and a.repeat == 1
    }
    studies = {
        arm: ComparisonStudy(
            paper_ids=paper_ids,
            repeats=settings.repeats,
            factors=_contrasts(first[settings.baseline], first[arm]),
        )
        for arm in methods
        if arm != settings.baseline
    }
    return Experiment(
        path,
        output,
        studies,
        attempts,
        inputs,
        settings,
        repeat_papers,
        resume,
        dataset_role,
    )
