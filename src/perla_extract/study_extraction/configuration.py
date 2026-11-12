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

DEFAULT_EXTRACTION_MODEL = "openai/gpt-5.2"
DEFAULT_CLAIM_MODEL = None
STAGES = ("read", "assemble", "refine", "repair", "enrich")
_PATHS = {"pdf", "supplement", "output_dir", "document_cache_dir", "model_cache_dir"}
_OPERATIONAL = _PATHS | {
    "heartbeat_seconds",
    "dry_run",
    "refresh_document_cache",
    "langfuse",
}


@dataclass(frozen=True)
class ExtractionConfig:
    """The applied method and its operational limits for one immutable run.

    A missing stage model inherits the main model; repair can inherit the explicit
    refinement override. Deployment settings take precedence over these shortcuts.
    Paths locate inputs and artifacts; content hashes bind their scientific identity.
    """

    pdf: Path
    supplement: Path | None = None
    output_dir: Path = Path("study_extraction")
    model: str = DEFAULT_EXTRACTION_MODEL
    reasoning_effort: str | None = None
    use_claim_ledger: bool = True
    claim_model: str | None = None
    claim_max_output_tokens: int = 30_000
    claim_recall_passes: int = 2
    use_enrichment: bool = True
    enrichment_model: str | None = None
    enrichment_max_output_tokens: int = 20_000
    use_refinement: bool = True
    refinement_model: str | None = None
    use_targeted_repair: bool = True
    repair_model: str | None = None
    repair_max_output_tokens: int = 30_000
    parser: str = "docling"
    claim_mode: str = "auto"
    single_call_max_input_tokens: int = 90_000
    claim_window_input_tokens: int = 60_000
    assembly_max_input_tokens: int = 180_000
    max_output_tokens: int = 80_000
    max_model_calls: int | None = None
    max_cost_usd: float | None = None
    max_total_tokens: int | None = None
    max_elapsed_seconds: float | None = None
    temperature: float | None = None
    heartbeat_seconds: float = 20
    timeout_seconds: float = 600
    document_cache_dir: Path = Path(".perla-cache/documents")
    model_cache_dir: Path = Path(".perla-cache/models")
    refresh_document_cache: bool = False
    reduced_export: bool = False
    dry_run: bool = False
    deployments: dict[str, DeploymentSettings] = field(default_factory=dict)
    langfuse: bool = False

    def __post_init__(self) -> None:
        for name in _PATHS:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value))
        for name in (
            "model",
            "claim_model",
            "enrichment_model",
            "refinement_model",
            "repair_model",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a nonempty string")
        if self.model is None:
            raise ValueError("model must be a nonempty string")
        if self.reasoning_effort not in {
            None,
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        }:
            raise ValueError(
                "reasoning_effort must be none, minimal, low, medium or high"
            )
        if self.claim_mode not in {"auto", "single", "windowed"}:
            raise ValueError("claim_mode must be auto, single or windowed")
        if self.parser not in {"docling", "pymupdf"}:
            raise ValueError("parser must be docling or pymupdf")
        for name in (
            "use_claim_ledger",
            "use_enrichment",
            "use_refinement",
            "use_targeted_repair",
            "refresh_document_cache",
            "reduced_export",
            "dry_run",
            "langfuse",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be true or false")
        integers = (
            "claim_recall_passes",
            "claim_max_output_tokens",
            "enrichment_max_output_tokens",
            "repair_max_output_tokens",
            "single_call_max_input_tokens",
            "claim_window_input_tokens",
            "assembly_max_input_tokens",
            "max_output_tokens",
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
            "temperature",
        ):
            value = getattr(self, name)
            if value is None and name in {
                "max_cost_usd",
                "max_elapsed_seconds",
                "temperature",
            }:
                continue
            minimum_inclusive = name in {"heartbeat_seconds", "temperature"}
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
        if not isinstance(self.deployments, dict):
            raise ValueError(
                "deployments must be a mapping of stage names to DeploymentSettings"
            )
        unknown = set(self.deployments) - set(STAGES)
        if unknown:
            raise ValueError(f"unknown stages: {', '.join(sorted(unknown))}")
        if any(
            not isinstance(value, DeploymentSettings)
            for value in self.deployments.values()
        ):
            raise ValueError("each deployment must be a DeploymentSettings instance")

    def effective_model(self, stage: str) -> str:
        """Return the model that will receive this stage's request."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage: {stage}")
        deployment = self.effective_deployment(stage)
        if deployment and deployment.model:
            return deployment.model
        overrides = {
            "read": self.claim_model,
            "assemble": self.model,
            "refine": self.refinement_model,
            "repair": self.repair_model or self.refinement_model,
            "enrich": self.enrichment_model,
        }
        if stage == "repair" and not self.repair_model:
            refinement = self.deployments.get("refine")
            if refinement and refinement.model:
                return refinement.model
        return overrides[stage] or self.model

    def effective_deployment(self, stage: str) -> DeploymentSettings:
        """Resolve a stage's transport and generation options together.

        A repair stage without its own model or endpoint inherits refinement settings,
        including its endpoint and limits. Supplying repair_model opts out; configure
        the repair deployment explicitly when it needs another endpoint. Partial repair
        limits override inherited limits without dropping the transport.
        """
        if stage not in STAGES:
            raise ValueError(f"unknown stage: {stage}")
        declared = self.deployments.get(stage, DeploymentSettings())
        if (
            stage == "repair"
            and not self.repair_model
            and "refine" in self.deployments
            and declared.model is None
            and declared.base_url is None
        ):
            overrides = {
                item.name: getattr(declared, item.name)
                for item in fields(declared)
                if getattr(declared, item.name) is not None
                and getattr(declared, item.name) != {}
            }
            return replace(self.deployments["refine"], **overrides)
        return declared

    def scientific_settings(self) -> dict[str, Any]:
        """Settings needed to reproduce an attempted method, independent of file locations."""
        result = {
            key: value for key, value in asdict(self).items() if key not in _OPERATIONAL
        }
        result["effective_models"] = {
            stage: self.effective_model(stage) for stage in STAGES
        }
        result["effective_deployments"] = {
            stage: self.effective_deployment(stage).identity() for stage in STAGES
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
    unknown = set(document) - {"extraction", "stages", "tracing"}
    if unknown:
        raise ValueError(
            f"unknown configuration sections: {', '.join(sorted(unknown))}"
        )
    options = document.get("extraction", {})
    if not isinstance(options, dict):
        raise ValueError("[extraction] must be a table")
    options = dict(options)
    allowed = {f.name for f in fields(ExtractionConfig)} - {"deployments", "langfuse"}
    unknown = set(options) - allowed
    if unknown:
        raise ValueError(f"unknown extraction settings: {', '.join(sorted(unknown))}")
    for key in _PATHS & options.keys():
        if not isinstance(options[key], str):
            raise ValueError(f"extraction.{key} must be a path string")
        value = Path(options[key])
        options[key] = value if value.is_absolute() else (path.parent / value).resolve()
    stages = document.get("stages", {})
    if not isinstance(stages, dict) or set(stages) - set(STAGES):
        raise ValueError(f"stages must be tables named {', '.join(STAGES)}")
    for stage, settings in stages.items():
        if not isinstance(settings, dict):
            raise ValueError(f"stages.{stage} must be a table")
        tokenizer = settings.get("tokenizer")
        if isinstance(tokenizer, str) and tokenizer not in {
            "estimate",
            "utf8_div_3",
            "utf8_bytes",
        }:
            location = Path(tokenizer)
            settings["tokenizer"] = str(
                location
                if location.is_absolute()
                else (path.parent / location).resolve()
            )
    try:
        options["deployments"] = {
            key: DeploymentSettings(**value) for key, value in stages.items()
        }
    except TypeError as error:
        raise ValueError(f"invalid stage configuration: {error}") from error
    tracing = document.get("tracing", {})
    if not isinstance(tracing, dict) or set(tracing) - {"langfuse"}:
        raise ValueError("[tracing] accepts only langfuse = true or false")
    options["langfuse"] = tracing.get("langfuse", False)
    if not isinstance(options["langfuse"], bool):
        raise ValueError("tracing.langfuse must be true or false")
    return options


def resolve_config(
    pdf: str | Path | None = None, *, config_file: Path | None = None, **overrides: Any
) -> ExtractionConfig:
    """Apply invocation overrides to file settings, then validate the resulting method."""
    options = load_settings(config_file)
    options.update(overrides)
    if pdf is not None:
        options["pdf"] = pdf
    if "pdf" not in options:
        raise ValueError("provide --pdf or extraction.pdf in the configuration file")
    if options.get("reasoning_effort") == "omit":
        options["reasoning_effort"] = None
    try:
        return ExtractionConfig(**options)
    except TypeError as error:
        raise ValueError(f"invalid extraction configuration: {error}") from error
