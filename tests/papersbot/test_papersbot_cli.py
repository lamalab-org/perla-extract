from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from loguru import logger

from perla_extract.papersbot import cli


class _Result:
    """Provide only the serialized result boundary required by the CLI."""

    @staticmethod
    def model_dump(*, mode: str) -> dict[str, str]:
        assert mode == "json"
        return {"status": "complete"}


def test_cli_writes_structured_log_file(monkeypatch, tmp_path: Path):
    def run_stub(*args, **kwargs):
        del args, kwargs
        logger.info("papersbot CLI test event")
        return _Result()

    monkeypatch.setattr(cli, "run_papersbot", run_stub)
    log_file = tmp_path / "logs" / "papersbot.jsonl"

    result = CliRunner().invoke(
        cli.main,
        [
            str(tmp_path / "papers"),
            "--no-rss",
            "--no-openalex",
            "--log-file",
            str(log_file),
        ],
    )

    assert result.exit_code == 0
    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["text"].endswith("papersbot CLI test event\n")
    assert json.loads(result.stdout) == {"status": "complete"}
