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


class _PartialResult:
    """Represent a completed run whose ledger contains recoverable errors."""

    @staticmethod
    def model_dump(*, mode: str) -> dict[str, str]:
        assert mode == "json"
        return {"status": "complete_with_errors"}


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
            "PAPERSBOT_REQUEST_RETRIES": "5",
            "OPENALEX_API_KEY": "openalex-secret",
            "ZOTERO_GROUP_ID": "6651379",
            "ZOTERO_API_KEY": "not-written-to-output",
        },
    )

    assert result.exit_code == 0
    assert received["download_dir"] == tmp_path / "papers"
    assert received["state_dir"] == tmp_path / "state"
    assert received["rss_enabled"] is False
    assert received["openalex_enabled"] is False
    assert received["max_attempts"] == 7
    assert received["request_retries"] == 5
    assert received["openalex_api_key"] == "openalex-secret"
    assert received["zotero_group_id"] == "6651379"
    assert "not-written-to-output" not in result.output
    assert "openalex-secret" not in result.output


def test_scheduled_mode_exits_nonzero_after_writing_a_partial_result(monkeypatch):
    monkeypatch.setattr(cli, "run_papersbot", lambda *args, **kwargs: _PartialResult())

    result = CliRunner().invoke(
        cli.main,
        ["--no-rss", "--no-openalex"],
        env={"PAPERSBOT_FAIL_ON_PARTIAL": "true"},
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout) == {"status": "complete_with_errors"}
