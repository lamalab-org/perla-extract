"""Command-line interface for incremental literature discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from perla_extract.study_extraction.logging import configure_logging

from .bot import run_papersbot


@click.command(context_settings={"show_default": True})
@click.argument(
    "download_dir", type=click.Path(path_type=Path, file_okay=False), default="downloaded_papers"
)
@click.option(
    "--state-dir", type=click.Path(path_type=Path, file_okay=False), default=".papersbot-state"
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
@click.option("--max-attempts", type=click.IntRange(min=1), default=4)
@click.option("--request-timeout", type=click.FloatRange(min=1), default=30.0)
@click.option(
    "--log-level",
    type=click.Choice(("DEBUG", "INFO", "WARNING", "ERROR"), case_sensitive=False),
    default="INFO",
)
@click.option("--json-logs", is_flag=True, help="Write structured JSON logs to stderr.")
def main(
    download_dir: Path,
    state_dir: Path,
    feeds: tuple[str, ...],
    feeds_file: Path | None,
    selection_file: Path | None,
    unpaywall_email: str | None,
    max_attempts: int,
    request_timeout: float,
    log_level: str,
    json_logs: bool,
) -> None:
    """Find relevant feed entries and download their open-access PDFs."""

    configure_logging(level=log_level, json_output=json_logs)
    try:
        result = run_papersbot(
            download_dir,
            state_dir=state_dir,
            feeds=feeds or None,
            feeds_file=feeds_file,
            selection_file=selection_file,
            unpaywall_email=unpaywall_email,
            max_attempts=max_attempts,
            request_timeout=request_timeout,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
