"""One logging policy for the study extractor's CLI and library modules."""

from __future__ import annotations

import sys

from loguru import logger


def _stderr(message: object) -> None:
    """Resolve stderr at write time so Click and test runners can capture logs."""

    sys.stderr.write(str(message))


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
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


__all__ = ["configure_logging", "logger"]
