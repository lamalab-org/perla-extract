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


def test_cli_can_be_configured_entirely_from_environment(monkeypatch, tmp_path: Path):
    received = {}

    def run_stub(download_dir, **kwargs):
        received["download_dir"] = download_dir
        received.update(kwargs)
        return _Result()

    monkeypatch.setattr(cli, "run_papersbot", run_stub)
    result = CliRunner().invoke(
        cli.main,
        [],
        env={
            "PAPERSBOT_DOWNLOAD_DIR": str(tmp_path / "papers"),
            "PAPERSBOT_STATE_DIR": str(tmp_path / "state"),
            "PAPERSBOT_RSS": "false",
            "PAPERSBOT_OPENALEX": "false",
            "PAPERSBOT_MAX_ATTEMPTS": "7",
            "ZOTERO_GROUP_ID": "6651379",
            "ZOTERO_API_KEY": "not-written-to-output",
            "ZOTERO_PDF_POLICY": "",
        },
    )

    assert result.exit_code == 0
    assert received["download_dir"] == tmp_path / "papers"
    assert received["state_dir"] == tmp_path / "state"
    assert received["rss_enabled"] is False
    assert received["openalex_enabled"] is False
    assert received["max_attempts"] == 7
    assert received["zotero_group_id"] == "6651379"
    assert received["zotero_pdf_policy"] == "never"
    assert "not-written-to-output" not in result.output
