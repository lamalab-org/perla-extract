"""Click command for device-centered extraction of a paper and its supplement."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click

from .configuration import DEFAULT_EXTRACTION_MODEL, default, resolve_config
from .deployment import REASONING_LEVELS
from .logging import configure_logging, logger
from .source import available_parsers
from .workflow import run_extraction


def _load_env(path: Path | None) -> None:
    """Load provider credentials without choosing a provider in application code.

    Existing process variables win, matching normal command-line expectations. The
    small loader intentionally supports only the ``NAME=VALUE`` form needed for model
    provider keys; LiteLLM interprets the variables for the selected model prefix.
    """

    if path is None:
        return
    if not path.is_file():
        raise FileNotFoundError(f"environment file does not exist: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        name = key.strip().removeprefix("export ").strip()
        if name:
            os.environ.setdefault(name, value.strip().strip('"').strip("'"))


def extract_study(
    pdf: str | Path | None = None,
    *,
    config_file: str | Path | None = None,
    env_file: str | Path | None = None,
    **options: Any,
) -> dict[str, object]:
    """Apply shared configuration and execute one paper in a fresh output directory.

    Keyword settings override the TOML file. The same defaults and validation apply
    to Python, CLI and cohort runs. Explicit missing environment files fail before
    parsing; automatic discovery of .env.local is optional. The returned report
    describes the saved artifacts, scientific checks and resource accounting.
    """
    discovered = Path(".env.local")
    _load_env(
        Path(env_file) if env_file else discovered if discovered.is_file() else None
    )
    config = resolve_config(
        pdf, config_file=Path(config_file) if config_file else None, **options
    )
    return run_extraction(config)


EXISTING_FILE = click.Path(
    path_type=Path, exists=True, dir_okay=False, readable=True, resolve_path=True
)
OUTPUT_DIRECTORY = click.Path(path_type=Path, file_okay=False, resolve_path=True)


@click.command(context_settings={"show_default": True})
@click.option(
    "--config",
    "config_file",
    type=EXISTING_FILE,
    help="TOML settings; explicit options override the file.",
)
@click.option(
    "--pdf", type=EXISTING_FILE, help="Main paper PDF; may also be set in TOML."
)
@click.option("--supplement", type=EXISTING_FILE, help="Supplementary PDF.")
@click.option("--output-dir", type=OUTPUT_DIRECTORY, default=default("output_dir"))
@click.option(
    "--model",
    default=DEFAULT_EXTRACTION_MODEL,
    help="Select the default deployment; stage model overrides remain explicit in TOML.",
)
@click.option(
    "--base-url",
    help="Default model endpoint; specify with --model when selecting a new endpoint.",
)
@click.option(
    "--api-key-env", help="Environment variable containing the default endpoint key."
)
@click.option("--reasoning-effort", type=click.Choice(REASONING_LEVELS), default="omit")
@click.option(
    "--claims/--no-claims",
    "use_claim_ledger",
    default=default("use_claim_ledger"),
    help="Read source statements before building the linked study.",
)
@click.option(
    "--claim-recall-passes",
    type=click.IntRange(min=1, max=3),
    default=default("claim_recall_passes"),
    help="Number of source readings per window; repeated statements are consolidated.",
)
@click.option(
    "--enrichment/--no-enrichment",
    "use_enrichment",
    default=default("use_enrichment"),
    help="Interpret composition sites and processing roles in separate audited calls.",
)
@click.option(
    "--refinement/--no-refinement",
    "use_refinement",
    default=default("use_refinement"),
    help="Request a complete draft revision against the selected evidence.",
)
@click.option(
    "--targeted-repair/--no-targeted-repair",
    "use_targeted_repair",
    default=default("use_targeted_repair"),
    help="Retry only audit-visible gaps using their parser text and table evidence.",
)
@click.option(
    "--parser", type=click.Choice(available_parsers()), default=default("parser")
)
@click.option(
    "--claim-mode",
    type=click.Choice(("auto", "single", "windowed")),
    default=default("claim_mode"),
)
@click.option(
    "--single-call-max-input-tokens",
    type=click.IntRange(min=1),
    default=default("single_call_max_input_tokens"),
)
@click.option(
    "--claim-window-input-tokens",
    type=click.IntRange(min=1),
    default=default("claim_window_input_tokens"),
)
@click.option(
    "--assembly-max-input-tokens",
    type=click.IntRange(min=1),
    default=default("assembly_max_input_tokens"),
    help="Default input cap for assembly, revision, repair and enrichment; stage deployments may lower it.",
)
@click.option(
    "--max-output-tokens",
    type=click.IntRange(min=1),
    default=None,
    help="Set stages.default.max_output_tokens; otherwise use the documented per-stage defaults.",
)
@click.option(
    "--max-model-calls",
    type=click.IntRange(min=1),
    default=None,
    help="Stop before sending more than this many provider requests, including retries.",
)
@click.option(
    "--max-cost-usd",
    type=click.FloatRange(min=0, min_open=True),
    default=None,
    help="Stop before the next call once adapter-reported cost reaches this amount.",
)
@click.option("--temperature", type=float, default=None)
@click.option(
    "--heartbeat-seconds",
    type=click.FloatRange(min=0),
    default=default("heartbeat_seconds"),
)
@click.option(
    "--timeout-seconds",
    type=click.FloatRange(min=0, min_open=True),
    default=default("timeout_seconds"),
)
@click.option(
    "--document-cache-dir", type=OUTPUT_DIRECTORY, default=default("document_cache_dir")
)
@click.option(
    "--model-cache-dir", type=OUTPUT_DIRECTORY, default=default("model_cache_dir")
)
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
@click.option(
    "--max-total-tokens",
    type=click.IntRange(min=1),
    help="Provider token budget, also usable for self-hosted models.",
)
@click.option(
    "--max-elapsed-seconds",
    type=click.FloatRange(min=0, min_open=True),
    help="Stop starting requests after this elapsed-time budget.",
)
@click.option(
    "--langfuse/--no-langfuse",
    default=False,
    help="Export run/stage/attempt traces using environment credentials.",
)
@click.option("--json-logs", is_flag=True, help="Write structured JSON logs to stderr.")
@click.pass_context
def main(ctx: click.Context, log_level: str, json_logs: bool, **options: Any) -> None:
    """Extract devices, performance, processing, and stability from one study."""

    configure_logging(level=log_level, json_output=json_logs)
    logger.info("Starting study extraction")
    try:
        explicit = {
            key: value
            for key, value in options.items()
            if ctx.get_parameter_source(key)
            not in {click.core.ParameterSource.DEFAULT, None}
        }
        # Keep Click defaults out of the merge: only user-supplied values override TOML.
        report = extract_study(**explicit)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    logger.info("Study extraction finished with status={}", report["status"])
    click.echo(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] == "failed":
        raise click.exceptions.Exit(1)


if __name__ == "__main__":
    main()
