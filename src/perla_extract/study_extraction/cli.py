"""Click command for device-centered extraction of a paper and its supplement."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from .logging import configure_logging, logger
from .source import available_parsers
from .workflow import (
    DEFAULT_EXTRACTION_MODEL,
    DEFAULT_INVENTORY_MODEL,
    ExtractionConfig,
    run_extraction,
)

REASONING_LEVELS = ("omit", "none", "minimal", "low", "medium", "high")


def _load_env(path: Path | None) -> None:
    """Load provider credentials without choosing a provider in application code.

    Existing process variables win, matching normal command-line expectations. The
    small loader intentionally supports only the ``NAME=VALUE`` form needed for model
    provider keys; LiteLLM interprets the variables for the selected model prefix.
    """

    if path is None or not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        name = key.strip().removeprefix("export ").strip()
        if name:
            os.environ.setdefault(name, value.strip().strip('"').strip("'"))


def _reasoning(value: str) -> str | None:
    """Translate the CLI-only ``omit`` sentinel into an absent API parameter."""

    return None if value == "omit" else value


def extract_study(
    pdf: str | Path,
    supplement: str | Path | None = None,
    output_dir: str | Path = "study_extraction",
    model: str = DEFAULT_EXTRACTION_MODEL,
    reasoning_effort: str = "omit",
    use_inventory: bool = True,
    inventory_model: str | None = DEFAULT_INVENTORY_MODEL,
    inventory_max_output_tokens: int = 20_000,
    use_enrichment: bool = True,
    enrichment_model: str | None = None,
    enrichment_max_output_tokens: int = 20_000,
    use_refinement: bool = True,
    refinement_model: str | None = None,
    parser: str = "docling",
    mode: str = "auto",
    single_call_max_input_tokens: int = 90_000,
    window_input_tokens: int = 60_000,
    max_output_tokens: int = 80_000,
    temperature: float | None = None,
    heartbeat_seconds: float = 20,
    timeout_seconds: float = 600,
    document_cache_dir: str | Path = ".perla-cache/documents",
    model_cache_dir: str | Path = ".perla-cache/models",
    refresh_document_cache: bool = False,
    reduced_export: bool = False,
    dry_run: bool = False,
    env_file: str | Path | None = None,
) -> dict[str, object]:
    """Run the artifact-producing extraction workflow from Python.

    This is the programmatic counterpart of ``perla-extract``: it writes the rich
    extraction, validation, provenance, report, and compatibility artifacts to
    ``output_dir`` and returns the final report. LiteLLM resolves credentials for the
    provider prefix in ``model``; ``dry_run`` parses and caches documents without
    requiring any provider credential.
    """

    _load_env(Path(env_file) if env_file else Path(".env.local"))
    config = ExtractionConfig(
        pdf=Path(pdf),
        supplement=Path(supplement) if supplement else None,
        output_dir=Path(output_dir),
        model=model,
        reasoning_effort=_reasoning(reasoning_effort),
        use_inventory=use_inventory,
        inventory_model=inventory_model,
        inventory_max_output_tokens=inventory_max_output_tokens,
        use_enrichment=use_enrichment,
        enrichment_model=enrichment_model,
        enrichment_max_output_tokens=enrichment_max_output_tokens,
        use_refinement=use_refinement,
        refinement_model=refinement_model,
        parser=parser,
        mode=mode,
        single_call_max_input_tokens=single_call_max_input_tokens,
        window_input_tokens=window_input_tokens,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        heartbeat_seconds=heartbeat_seconds,
        timeout_seconds=timeout_seconds,
        document_cache_dir=Path(document_cache_dir),
        model_cache_dir=Path(model_cache_dir),
        refresh_document_cache=refresh_document_cache,
        reduced_export=reduced_export,
        dry_run=dry_run,
    )
    return run_extraction(config)


EXISTING_FILE = click.Path(
    path_type=Path, exists=True, dir_okay=False, readable=True, resolve_path=True
)
OUTPUT_DIRECTORY = click.Path(path_type=Path, file_okay=False, resolve_path=True)


@click.command(context_settings={"show_default": True})
@click.option("--pdf", type=EXISTING_FILE, required=True, help="Main paper PDF.")
@click.option("--supplement", type=EXISTING_FILE, help="Supplementary PDF.")
@click.option("--output-dir", type=OUTPUT_DIRECTORY, default="study_extraction")
@click.option(
    "--model",
    default=DEFAULT_EXTRACTION_MODEL,
    help="LiteLLM provider-prefixed model name.",
)
@click.option("--reasoning-effort", type=click.Choice(REASONING_LEVELS), default="omit")
@click.option(
    "--inventory/--no-inventory",
    "use_inventory",
    default=True,
    help="Run an independent record inventory for routing and recall review.",
)
@click.option(
    "--inventory-model",
    default=DEFAULT_INVENTORY_MODEL,
    help="LiteLLM model for the compact inventory.",
)
@click.option(
    "--inventory-max-output-tokens", type=click.IntRange(min=1), default=20_000
)
@click.option(
    "--enrichment/--no-enrichment",
    "use_enrichment",
    default=True,
    help="Interpret composition sites and processing roles in separate audited calls.",
)
@click.option(
    "--enrichment-model",
    default=None,
    help="LiteLLM model for enrichment; defaults to --model.",
)
@click.option(
    "--enrichment-max-output-tokens", type=click.IntRange(min=1), default=20_000
)
@click.option(
    "--refinement/--no-refinement",
    "use_refinement",
    default=True,
    help="Re-read the evidence to correct omissions and unsupported draft records.",
)
@click.option(
    "--refinement-model",
    default=None,
    help="LiteLLM model for the quality pass; defaults to --model.",
)
@click.option("--parser", type=click.Choice(available_parsers()), default="docling")
@click.option(
    "--mode", type=click.Choice(("auto", "single", "windowed")), default="auto"
)
@click.option(
    "--single-call-max-input-tokens", type=click.IntRange(min=1), default=90_000
)
@click.option("--window-input-tokens", type=click.IntRange(min=1), default=60_000)
@click.option("--max-output-tokens", type=click.IntRange(min=1), default=80_000)
@click.option("--temperature", type=float, default=None)
@click.option("--heartbeat-seconds", type=click.FloatRange(min=0), default=20.0)
@click.option(
    "--timeout-seconds", type=click.FloatRange(min=0, min_open=True), default=600.0
)
@click.option(
    "--document-cache-dir", type=OUTPUT_DIRECTORY, default=".perla-cache/documents"
)
@click.option("--model-cache-dir", type=OUTPUT_DIRECTORY, default=".perla-cache/models")
@click.option("--refresh-document-cache", is_flag=True)
@click.option(
    "--reduced-export",
    is_flag=True,
    help="Also write the historical reduced PERLA compatibility files.",
)
@click.option("--dry-run", is_flag=True, help="Parse and plan without calling a model.")
@click.option("--env-file", type=click.Path(path_type=Path, dir_okay=False))
@click.option(
    "--log-level",
    type=click.Choice(("DEBUG", "INFO", "WARNING", "ERROR"), case_sensitive=False),
    default="INFO",
)
@click.option("--json-logs", is_flag=True, help="Write structured JSON logs to stderr.")
def main(log_level: str, json_logs: bool, **options: object) -> None:
    """Extract devices, performance, processing, and stability from one study."""

    configure_logging(level=log_level, json_output=json_logs)
    logger.info("Starting study extraction")
    try:
        report = extract_study(**options)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    logger.info("Study extraction finished with status={}", report["status"])
    click.echo(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] == "failed":
        raise click.exceptions.Exit(1)


if __name__ == "__main__":
    main()
