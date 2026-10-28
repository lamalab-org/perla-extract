"""Prepare paired extraction commands without reading references into model prompts.

The existing extraction and scoring CLIs do the work. This module only freezes
inputs and settings, randomizes execution order, and keeps every planned run visible.
"""

from __future__ import annotations

import hashlib
import json
import random
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Literal

import click
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]


class Arm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_recall_passes: int = Field(default=2, ge=1, le=3)
    use_claim_ledger: bool = True
    use_refinement: bool = True
    claim_model: Literal["main", "reader"] = "main"


class Study(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9-]+$")
    factor: Literal[
        "claim_recall_passes", "use_claim_ledger", "use_refinement", "claim_model"
    ]
    arms: dict[str, Arm] = Field(min_length=2, max_length=2)


class Paper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    pdf: Path
    supplement: Path | None = None
    truth: Path


def digest(path: Path) -> str:
    """Hash bytes in bounded chunks, including large supporting-information PDFs."""

    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def check_inputs(plan: dict) -> None:
    """Refuse changed sources, references, or checkout before a prepared script runs."""

    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if (
        revision != plan["revision"]
        or subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            text=True,
        ).strip()
    ):
        raise ValueError("Use the clean checkout revision recorded in plan.json")
    for filename, expected in plan["input_sha256"].items():
        if digest(Path(filename)) != expected:
            raise ValueError(f"Input changed since preparation: {filename}")


def prepare(
    study_path: Path,
    cohort_path: Path,
    output: Path,
    model: str,
    reader_model: str | None,
    max_cost: float,
    repeats: int,
    seed: int,
) -> dict:
    """Freeze a two-arm study; preparation never invokes an extraction provider."""

    study = Study.model_validate_json(study_path.read_text())
    if set(study.arms) != {"control", "treatment"}:
        raise ValueError("Arms must be named control and treatment")
    a, b = (study.arms[key].model_dump() for key in ("control", "treatment"))
    if {key for key in a if a[key] != b[key]} != {study.factor}:
        raise ValueError("Arms must differ only in the declared factor")
    if any(arm.claim_model == "reader" for arm in study.arms.values()):
        if not reader_model or reader_model == model:
            raise ValueError("Choose an explicit, distinct --reader-model")
    papers = [
        Paper.model_validate(item) for item in json.loads(cohort_path.read_text())
    ]
    if not papers or len({p.paper_id for p in papers}) != len(papers):
        raise ValueError("Cohort must contain distinct papers")
    inputs = {str(p.resolve()): digest(p) for p in (study_path, cohort_path)}
    for paper in papers:
        for field in ("pdf", "supplement", "truth"):
            path = getattr(paper, field)
            if path is not None:
                setattr(paper, field, (cohort_path.parent / path).resolve())
        for path in (paper.pdf, paper.supplement):
            if path is not None:
                inputs[str(path)] = digest(path)
        # Frozen references remain local to scoring, never part of extract commands.
        for filename in ("ground_truth.json", "manifest.json"):
            path = paper.truth / filename
            inputs[str(path)] = digest(path)
    output = output.resolve()
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    prefix = [sys.executable, "-m"]
    jobs = []
    rng = random.Random(seed)
    for repeat in range(1, repeats + 1):
        for paper in papers:
            order = list(study.arms)
            rng.shuffle(order)
            for arm_name in order:
                arm = study.arms[arm_name]
                run = output / "runs" / f"repeat-{repeat}" / arm_name / paper.paper_id
                extract = prefix + [
                    "perla_extract.study_extraction.cli",
                    "--pdf",
                    str(paper.pdf),
                    "--output-dir",
                    str(run),
                    "--model",
                    model,
                    "--claim-model",
                    reader_model if arm.claim_model == "reader" else model,
                    "--refinement-model",
                    model,
                    "--repair-model",
                    model,
                    "--enrichment-model",
                    model,
                    "--parser",
                    "docling",
                    "--reasoning-effort",
                    "omit",
                    "--claim-mode",
                    "auto",
                    "--claim-recall-passes",
                    str(arm.claim_recall_passes),
                    "--claims" if arm.use_claim_ledger else "--no-claims",
                    "--refinement" if arm.use_refinement else "--no-refinement",
                    "--targeted-repair",
                    "--enrichment",
                    "--max-model-calls",
                    "14",
                    "--max-cost-usd",
                    str(max_cost),
                    "--single-call-max-input-tokens",
                    "90000",
                    "--claim-window-input-tokens",
                    "60000",
                    "--assembly-max-input-tokens",
                    "180000",
                    "--max-output-tokens",
                    "80000",
                    "--claim-max-output-tokens",
                    "30000",
                    "--repair-max-output-tokens",
                    "30000",
                    "--enrichment-max-output-tokens",
                    "20000",
                    "--document-cache-dir",
                    str(output / "document-cache"),
                    "--model-cache-dir",
                    str(run / "model-cache"),
                ]
                if paper.supplement is not None:
                    extract += ["--supplement", str(paper.supplement)]
                score = prefix + [
                    "perla_extract.study_extraction.evaluation_cli",
                    "--truth",
                    str(paper.truth),
                    "--prediction",
                    str(run),
                    "--output",
                    str(run / "evaluation.json"),
                    "--fail-on-scoring-issues",
                ]
                jobs.append(
                    {
                        "paper_id": paper.paper_id,
                        "arm": arm_name,
                        "repeat": repeat,
                        "extract": extract,
                        "score": score,
                    }
                )
    aggregates = []
    for repeat in range(1, repeats + 1):
        for arm in study.arms:
            command = prefix + ["perla_extract.study_extraction.evaluation_dataset_cli"]
            for job in jobs:
                if job["repeat"] == repeat and job["arm"] == arm:
                    command += ["--report", job["score"][-2]]
            command += [
                "--output",
                str(output / f"repeat-{repeat}-{arm}.json"),
                "--fail-on-scoring-issues",
            ]
            aggregates.append(command)
    plan = {
        "study": study.model_dump(),
        "revision": revision,
        "seed": seed,
        "input_sha256": inputs,
        "jobs": jobs,
        "aggregates": aggregates,
        "max_cost_per_run": max_cost,
        "repeats": repeats,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    header = [
        "#!/usr/bin/env bash",
        "set -eu",
        f"cd {shlex.quote(str(ROOT))}",
        "export PYTHONPATH=.:src",
        shlex.join(
            prefix + ["examples.ablations.prepare", "check", str(output / "plan.json")]
        ),
    ]
    for stage in ("extract", "score"):
        commands = [job[stage] for job in jobs]
        if stage == "score":
            commands += aggregates
        guard = (
            []
            if stage == "score"
            else [
                f"test ! -e {shlex.quote(str(output / 'runs'))} || "
                "{ echo 'Runs already exist; use a new output directory.' >&2; exit 1; }"
            ]
        )
        lines = header + guard + ["failed=0"]
        lines += [shlex.join(command) + " || failed=1" for command in commands]
        lines += ['exit "$failed"']
        (output / f"{stage}.sh").write_text("\n".join(lines) + "\n")
    return plan


@click.group()
def main() -> None:
    """Prepare or check a local ablation plan; no paid calls are made here."""


@main.command("plan")
@click.option(
    "--study", "study_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option(
    "--cohort",
    "cohort_path",
    type=click.Path(path_type=Path, exists=True),
    required=True,
)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--model", required=True)
@click.option("--reader-model")
@click.option("--max-cost", type=click.FloatRange(min=0, min_open=True), required=True)
@click.option("--repeats", type=click.IntRange(min=1), default=1)
@click.option("--seed", type=int, default=0)
def plan_command(**kwargs) -> None:
    try:
        plan = prepare(**kwargs)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        f"Prepared {len(plan['jobs'])} runs; no model calls made. Inspect plan.json before running extract.sh."
    )


@main.command("check")
@click.argument("path", type=click.Path(path_type=Path, exists=True))
def check_command(path: Path) -> None:
    try:
        check_inputs(json.loads(path.read_text()))
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error


if __name__ == "__main__":
    main()
