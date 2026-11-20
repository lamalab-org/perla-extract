"""Resolve extraction settings once for Python, command-line and batch runs.

TOML files describe ordinary extraction options and optional stage deployments.
Explicit invocation options override the file. Defaults and model inheritance live
here so a new transport or interface cannot silently select a different method.
"""

from __future__ import annotations

import hashlib
import math
import platform
from dataclasses import asdict, dataclass, field, fields, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .deployment import DeploymentSettings
from .request_policy import ResolvedStagePolicy

DEFAULT_EXTRACTION_MODEL = "openai/gpt-5.2"
STAGES = ("read", "assemble", "refine", "repair", "enrich")
DEFAULT_OUTPUT_TOKENS = {
    "read": 30_000,
    "assemble": 80_000,
    "refine": 80_000,
    "repair": 30_000,
    "enrich": 20_000,
}
_DEPLOYMENT_SHORTCUTS = {
    "model",
    "base_url",
    "api_key_env",
    "reasoning_effort",
    "temperature",
    "max_output_tokens",
}
_REMOVED_SETTINGS = {
    "claim_model": "read.model",
    "refinement_model": "refine.model",
    "repair_model": "repair.model",
    "enrichment_model": "enrich.model",
    "claim_max_output_tokens": "read.max_output_tokens",
    "repair_max_output_tokens": "repair.max_output_tokens",
    "enrichment_max_output_tokens": "enrich.max_output_tokens",
}
_PATHS = {"pdf", "supplement", "output_dir", "document_cache_dir", "model_cache_dir"}
_OPERATIONAL = _PATHS | {
    "heartbeat_seconds",
    "dry_run",
    "refresh_document_cache",
    "langfuse",
    "langfuse_capture_content",
}


def _validate_deployments(deployments: dict[str, DeploymentSettings]) -> None:
    if not isinstance(deployments, dict):
        raise ValueError(
            "deployments must map default or stage names to DeploymentSettings"
        )
    if unknown := set(deployments) - {"default", *STAGES}:
        raise ValueError(f"unknown stages: {', '.join(sorted(unknown))}")
    for stage, deployment in deployments.items():
        if not isinstance(deployment, DeploymentSettings):
            raise ValueError("each deployment must be a DeploymentSettings instance")
        if (deployment.base_url or deployment.api_key_env) and not deployment.model:
            raise ValueError(
                f"stages.{stage}: specify model together with base_url or api_key_env"
            )


def _override_deployment(
    base: DeploymentSettings, declared: DeploymentSettings
) -> DeploymentSettings:
    if declared.model is not None:
        return declared
    return replace(
        base,
        **{
            item.name: getattr(declared, item.name)
            for item in fields(declared)
            if getattr(declared, item.name) is not None
            and getattr(declared, item.name) != {}
        },
    )


def merge_deployments(
    base: dict[str, DeploymentSettings], overrides: dict[str, DeploymentSettings]
) -> dict[str, DeploymentSettings]:
    """Apply the same model-selection rule to file, cohort and invocation settings."""
    _validate_deployments(base)
    _validate_deployments(overrides)
    return base | {
        stage: _override_deployment(base.get(stage, DeploymentSettings()), declared)
        for stage, declared in overrides.items()
    }


@dataclass(frozen=True)
class ExtractionConfig:
    """The applied method and its operational limits for one immutable run.

    Every stage uses the default deployment unless it selects its own model.
    Settings-only stage tables tune that default without repeating its connection.
    Paths locate inputs and artifacts; content hashes bind their scientific identity.
    """

    pdf: Path
    supplement: Path | None = None
    output_dir: Path = Path("study_extraction")
    use_claim_ledger: bool = True
    claim_recall_passes: int = 2
    use_enrichment: bool = True
    use_refinement: bool = True
    use_targeted_repair: bool = True
    parser: str = "docling"
    # Rebuild numeric tables printed inside figures from PyMuPDF words within
    # Docling's figure regions, or RapidOCR words for image figures (blocks of kind
    # "figure_table"). Docling only.
    figure_table_parsing: bool = False
    # Resolution at which figure_table_parsing renders image figures for OCR. Higher
    # values can recover small subscripts (Voc) at the cost of OCR time.
    figure_table_ocr_dpi: int = 300
    claim_mode: str = "auto"
    single_call_max_input_tokens: int = 90_000
    claim_window_input_tokens: int = 60_000
    assembly_max_input_tokens: int = 180_000
    max_model_calls: int | None = None
    max_cost_usd: float | None = None
    max_total_tokens: int | None = None
    max_elapsed_seconds: float | None = None
    heartbeat_seconds: float = 20
    timeout_seconds: float = 600
    document_cache_dir: Path = Path(".perla-cache/documents")
    model_cache_dir: Path = Path(".perla-cache/models")
    refresh_document_cache: bool = False
    reduced_export: bool = False
    dry_run: bool = False
    deployments: dict[str, DeploymentSettings] = field(default_factory=dict)
    langfuse: bool = False
    langfuse_capture_content: bool = False

    def __post_init__(self) -> None:
        for name in _PATHS:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value))
        if self.claim_mode not in {"auto", "single", "windowed"}:
            raise ValueError("claim_mode must be auto, single or windowed")
        if self.parser not in {"docling", "pymupdf"}:
            raise ValueError("parser must be docling or pymupdf")
        if self.figure_table_parsing is True and self.parser != "docling":
            raise ValueError("figure_table_parsing requires parser = \"docling\"")
        if type(self.figure_table_ocr_dpi) is not int or not 72 <= self.figure_table_ocr_dpi <= 1200:
            raise ValueError("figure_table_ocr_dpi must be an integer from 72 to 1200")
        for name in (
            "use_claim_ledger",
            "use_enrichment",
            "use_refinement",
            "use_targeted_repair",
            "refresh_document_cache",
            "figure_table_parsing",
            "reduced_export",
            "dry_run",
            "langfuse",
            "langfuse_capture_content",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be true or false")
        integers = (
            "claim_recall_passes",
            "single_call_max_input_tokens",
            "claim_window_input_tokens",
            "assembly_max_input_tokens",
            "max_model_calls",
            "max_total_tokens",
        )
        optional = {"max_model_calls", "max_total_tokens"}
        for name in integers:
            value = getattr(self, name)
            if value is None and name in optional:
                continue
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.claim_recall_passes > 3:
            raise ValueError("claim_recall_passes must be between 1 and 3")
        for name in (
            "max_cost_usd",
            "max_elapsed_seconds",
            "timeout_seconds",
            "heartbeat_seconds",
        ):
            value = getattr(self, name)
            if value is None and name in {
                "max_cost_usd",
                "max_elapsed_seconds",
            }:
                continue
            minimum_inclusive = name == "heartbeat_seconds"
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or (value == 0 and not minimum_inclusive)
            ):
                raise ValueError(
                    f"{name} must be a finite {'nonnegative' if minimum_inclusive else 'positive'} number"
                )
        _validate_deployments(self.deployments)

    def effective_model(self, stage: str) -> str:
        """Return the model that will receive this stage's request."""
        return self.effective_deployment(stage).model or DEFAULT_EXTRACTION_MODEL

    def effective_deployment(self, stage: str) -> DeploymentSettings:
        """Selecting a model starts a separate deployment; tuning inherits the default.

        Endpoints, credentials, tokenizer and weight identity never follow a model
        switch implicitly. Repair follows the same rule as every other stage.
        """
        if stage not in STAGES:
            raise ValueError(f"unknown stage: {stage}")
        base = self.deployments.get("default", DeploymentSettings())
        base = replace(base, model=base.model or DEFAULT_EXTRACTION_MODEL)
        declared = self.deployments.get(stage, DeploymentSettings())
        return _override_deployment(base, declared)

    def stage_policy(
        self, stage: str, *, claim_mode: str | None = None
    ) -> ResolvedStagePolicy:
        """Resolve the exact limits and generation options used by one stage.

        The historical assembly input cap applies to every non-reader stage. Reader
        planning resolves both single and windowed policies before selecting a mode.
        """
        deployment = self.effective_deployment(stage)
        mode = claim_mode or self.claim_mode
        input_limit = self.assembly_max_input_tokens
        if stage == "read":
            input_limit = (
                self.claim_window_input_tokens
                if mode == "windowed"
                else self.single_call_max_input_tokens
            )
        if deployment.max_input_tokens is not None:
            input_limit = min(input_limit, deployment.max_input_tokens)
        deployment = replace(deployment, max_input_tokens=input_limit)
        return ResolvedStagePolicy.resolve(
            stage,
            model=self.effective_model(stage),
            deployment=deployment,
            max_output_tokens=DEFAULT_OUTPUT_TOKENS[stage],
            reasoning_effort=None,
            temperature=None,
        )

    def stage_policies(
        self, claim_mode: str | None = None
    ) -> dict[str, ResolvedStagePolicy]:
        """Resolve dispatch policies after the reader planner has chosen its mode."""
        return {
            stage: self.stage_policy(stage, claim_mode=claim_mode) for stage in STAGES
        }

    def scientific_settings(self) -> dict[str, Any]:
        """Settings needed to reproduce an attempted method, independent of file locations."""
        result = {
            key: value
            for key, value in asdict(self).items()
            if key not in _OPERATIONAL | {"deployments"}
        }
        result["effective_models"] = {
            stage: self.effective_model(stage) for stage in STAGES
        }
        result["deployments"] = {}
        for stage in STAGES:
            policy = self.stage_policy(stage)
            result["deployments"][stage] = {
                **self.effective_deployment(stage).identity(),
                "max_output_tokens": policy.max_output_tokens,
                "reasoning_effort": None
                if policy.reasoning_effort == "omit"
                else policy.reasoning_effort,
            }
        return result


def implementation_sha256() -> str:
    """Bind resume to the installed extraction implementation, including validators."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_versions() -> dict[str, str]:
    """Record versions that can change model transport, schema or quantity handling."""
    versions = {"python": platform.python_version()}
    for package in ("litellm", "pydantic", "pint"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not_installed"
    return versions


def default(name: str) -> Any:
    """Return a scalar command-line default from the shared configuration."""
    return ExtractionConfig.__dataclass_fields__[name].default


def load_settings(path: Path | None) -> dict[str, Any]:
    """Load TOML with paths relative to its directory and reject misspelled keys."""
    if path is None:
        return {}
    path = Path(path)
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    return parse_settings(document, path.parent)


def parse_settings(document: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Validate settings from a standalone method or an experiment's arm tables."""
    unknown = set(document) - {"extraction", "stages", "tracing"}
    if unknown:
        raise ValueError(
            f"unknown configuration sections: {', '.join(sorted(unknown))}"
        )
    options = document.get("extraction", {})
    if not isinstance(options, dict):
        raise ValueError("[extraction] must be a table")
    options = dict(options)
    for key in options:
        if key in _REMOVED_SETTINGS or key in _DEPLOYMENT_SHORTCUTS:
            target = _REMOVED_SETTINGS.get(key, f"default.{key}")
            raise ValueError(
                f"extraction.{key} has moved to stages.{target}; use one deployment table"
            )
    allowed = {f.name for f in fields(ExtractionConfig)} - {
        "deployments",
        "langfuse",
        "langfuse_capture_content",
    }
    unknown = set(options) - allowed
    if unknown:
        raise ValueError(f"unknown extraction settings: {', '.join(sorted(unknown))}")
    for key in _PATHS & options.keys():
        if not isinstance(options[key], str):
            raise ValueError(f"extraction.{key} must be a path string")
        value = Path(options[key])
        options[key] = value if value.is_absolute() else (directory / value).resolve()
    stages = document.get("stages", {})
    if not isinstance(stages, dict) or set(stages) - {"default", *STAGES}:
        raise ValueError(f"stages must be tables named default, {', '.join(STAGES)}")
    stages = dict(stages)
    for stage, settings in stages.items():
        if not isinstance(settings, dict):
            raise ValueError(f"stages.{stage} must be a table")
        settings = stages[stage] = dict(settings)
        tokenizer = settings.get("tokenizer")
        if isinstance(tokenizer, str) and tokenizer not in {
            "estimate",
            "utf8_div_3",
            "utf8_bytes",
        }:
            location = Path(tokenizer)
            settings["tokenizer"] = str(
                location if location.is_absolute() else (directory / location).resolve()
            )
    try:
        options["deployments"] = {
            key: DeploymentSettings(**value) for key, value in stages.items()
        }
    except TypeError as error:
        raise ValueError(f"invalid stage configuration: {error}") from error
    _validate_deployments(options["deployments"])
    tracing = document.get("tracing", {})
    if not isinstance(tracing, dict) or set(tracing) - {"langfuse", "capture_content"}:
        raise ValueError("[tracing] accepts langfuse and capture_content booleans")
    options["langfuse"] = tracing.get("langfuse", False)
    if not isinstance(options["langfuse"], bool):
        raise ValueError("tracing.langfuse must be true or false")
    options["langfuse_capture_content"] = tracing.get("capture_content", False)
    if not isinstance(options["langfuse_capture_content"], bool):
        raise ValueError("tracing.capture_content must be true or false")
    return options


def resolve_config(
    pdf: str | Path | None = None, *, config_file: Path | None = None, **overrides: Any
) -> ExtractionConfig:
    """Apply invocation overrides to file settings, then validate the resulting method."""
    options = load_settings(config_file)
    for key in overrides:
        if key in _REMOVED_SETTINGS:
            raise ValueError(
                f"{key} has moved to stages.{_REMOVED_SETTINGS[key]}; use a deployment table"
            )
    deployment_options = {
        key: overrides.pop(key) for key in _DEPLOYMENT_SHORTCUTS if key in overrides
    }
    options.update(overrides)
    if deployment_options:
        options["deployments"] = merge_deployments(
            options.get("deployments", {}),
            {"default": DeploymentSettings(**deployment_options)},
        )
    if pdf is not None:
        options["pdf"] = pdf
    if "pdf" not in options:
        raise ValueError("provide --pdf or extraction.pdf in the configuration file")
    try:
        return ExtractionConfig(**options)
    except TypeError as error:
        raise ValueError(f"invalid extraction configuration: {error}") from error
