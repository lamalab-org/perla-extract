"""One logging policy for the study extractor's CLI and library modules."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def _stderr(message: object) -> None:
    """Resolve stderr at write time so Click and test runners can capture logs."""

    sys.stderr.write(str(message))


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    log_file: Path | None = None,
) -> None:
    """Send readable or machine-parseable progress logs to stderr.

    Keeping logs on stderr leaves the command's JSON report on stdout safe for
    shell pipelines. Loguru also provides serialized JSON when the CLI is run by
    an orchestrator.
    """

    logger.remove()
    logger.add(
        _stderr,
        level=level.upper(),
        serialize=json_output,
        colorize=not json_output and sys.stderr.isatty(),
        format="<green>{time:HH:mm:ss}</green> <level>{level: <8}</level> {message}",
    )
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(log_file, level=level.upper(), serialize=True)


__all__ = ["configure_logging", "logger"]
