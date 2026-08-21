"""Command-line interface for incremental literature discovery."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import click

from perla_extract.study_extraction.logging import configure_logging

from .bot import run_papersbot


def _environment_flag(name: str) -> bool:
    """Read an explicit automation opt-in without treating any nonempty value as true."""

    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@click.command(context_settings={"show_default": True})
@click.argument(
    "download_dir",
    type=click.Path(path_type=Path, file_okay=False),
    default="downloaded_papers",
)
@click.option(
    "--state-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=".papersbot-state",
)
@click.option(
    "--feed",
    "feeds",
    multiple=True,
    help="Feed URL to check. Repeat to override the packaged feed list.",
)
@click.option(
    "--feeds-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True),
    help="Comment-friendly file of feed URLs.",
)
@click.option(
    "--selection-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True),
    help="JSON relevance policy. Defaults to the packaged perovskite-device policy.",
)
@click.option(
    "--unpaywall-email",
    default=lambda: os.environ.get("UNPAYWALL_EMAIL"),
    help="Email required by Unpaywall; OpenAlex remains available without it.",
)
@click.option(
    "--openalex-email",
    default=lambda: os.environ.get("OPENALEX_EMAIL") or os.environ.get("OA_EMAIL"),
    help="Contact email sent with OpenAlex requests.",
)
@click.option(
    "--rss/--no-rss",
    default=True,
    help="Use journal feeds for low-latency discovery.",
)
@click.option(
    "--openalex/--no-openalex",
    "openalex_enabled",
    default=True,
    help="Use configured OpenAlex topics for completeness and backfill.",
)
@click.option(
    "--openalex-start-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Override the incremental OpenAlex start date (YYYY-MM-DD).",
)
@click.option(
    "--openalex-end-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Override the OpenAlex end date (YYYY-MM-DD).",
)
@click.option(
    "--zotero-group-id",
    default=lambda: os.environ.get("ZOTERO_GROUP_ID"),
    help="Zotero group ID to ingest. ZOTERO_GROUP_ID is the environment fallback.",
)
@click.option(
    "--zotero-collection-key",
    default=lambda: os.environ.get("ZOTERO_COLLECTION_KEY"),
    help="Optionally limit Zotero ingestion to one journal-club collection.",
)
@click.option(
    "--zotero-output-collection-key",
    default=lambda: os.environ.get("ZOTERO_OUTPUT_COLLECTION_KEY"),
    help="Optional separate collection for records created by the bot.",
)
@click.option(
    "--zotero-api-key",
    default=lambda: os.environ.get("ZOTERO_API_KEY"),
    show_default=False,
    help="API key for private-group reads or writes. Never stored in bot artifacts.",
)
@click.option(
    "--zotero-save/--no-zotero-save",
    default=lambda: _environment_flag("ZOTERO_SAVE"),
    help="Mirror discovered papers and non-destructive PERLA status tags.",
)
@click.option(
    "--zotero-curated/--no-zotero-curated",
    default=lambda: _environment_flag("ZOTERO_CURATED"),
    help="Treat every item in the configured Zotero collection as human-approved.",
)
@click.option(
    "--zotero-pdf-policy",
    type=click.Choice(("never", "research-group")),
    default=lambda: os.environ.get("ZOTERO_PDF_POLICY", "never"),
    help="Upload PDFs only to a verified private research group.",
)
@click.option("--max-attempts", type=click.IntRange(min=1), default=4)
@click.option("--request-timeout", type=click.FloatRange(min=1), default=30.0)
@click.option(
    "--log-level",
    type=click.Choice(("DEBUG", "INFO", "WARNING", "ERROR"), case_sensitive=False),
    default="INFO",
)
@click.option("--json-logs", is_flag=True, help="Write structured JSON logs to stderr.")
@click.option(
    "--log-file",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Also write structured JSONL logs to this file.",
)
def main(
    download_dir: Path,
    state_dir: Path,
    feeds: tuple[str, ...],
    feeds_file: Path | None,
    selection_file: Path | None,
    unpaywall_email: str | None,
    openalex_email: str | None,
    rss: bool,
    openalex_enabled: bool,
    openalex_start_date: datetime | None,
    openalex_end_date: datetime | None,
    zotero_group_id: str | None,
    zotero_collection_key: str | None,
    zotero_output_collection_key: str | None,
    zotero_api_key: str | None,
    zotero_save: bool,
    zotero_curated: bool,
    zotero_pdf_policy: str,
    max_attempts: int,
    request_timeout: float,
    log_level: str,
    json_logs: bool,
    log_file: Path | None,
) -> None:
    """Discover papers and retrieve open or group-supplied PDFs."""

    configure_logging(level=log_level, json_output=json_logs, log_file=log_file)
    try:
        result = run_papersbot(
            download_dir,
            state_dir=state_dir,
            feeds=feeds or None,
            feeds_file=feeds_file,
            selection_file=selection_file,
            unpaywall_email=unpaywall_email,
            openalex_email=openalex_email,
            rss_enabled=rss,
            openalex_enabled=openalex_enabled,
            openalex_start_date=(
                openalex_start_date.date() if openalex_start_date else None
            ),
            openalex_end_date=(openalex_end_date.date() if openalex_end_date else None),
            zotero_group_id=zotero_group_id,
            zotero_collection_key=zotero_collection_key,
            zotero_output_collection_key=zotero_output_collection_key,
            zotero_api_key=zotero_api_key,
            zotero_save=zotero_save,
            zotero_curated=zotero_curated,
            zotero_pdf_policy=zotero_pdf_policy,
            max_attempts=max_attempts,
            request_timeout=request_timeout,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
