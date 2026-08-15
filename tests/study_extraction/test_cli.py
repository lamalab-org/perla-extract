"""Tests for command-line option translation."""

import json
import inspect
from pathlib import Path

from click.testing import CliRunner

from perla_extract.study_extraction import cli
from perla_extract.study_extraction.workflow import ExtractionConfig


def test_omit_reasoning_leaves_parameter_out(monkeypatch, tmp_path: Path) -> None:
    """Translate the CLI sentinel to None for APIs without reasoning support."""

    captured = {}

    def fake_run(config, api_key):
        captured["config"] = config
        captured["api_key"] = api_key
        return {"status": "complete"}

    monkeypatch.setattr(cli, "run_extraction", fake_run)

    result = cli.extract_study(
        pdf=str(tmp_path / "paper.pdf"),
        reasoning_effort="omit",
        dry_run=True,
    )

    assert result == {"status": "complete"}
    assert captured["config"].reasoning_effort is None
    assert captured["api_key"] is None


def test_click_command_keeps_report_and_logs_separate(
    monkeypatch, tmp_path: Path
) -> None:
    """Keep stdout valid JSON while operational logs remain on stderr."""

    paper = tmp_path / "paper.pdf"
    paper.touch()
    monkeypatch.setattr(
        cli, "extract_study", lambda **options: {"status": "dry_run", "options": 1}
    )

    result = CliRunner().invoke(
        cli.main,
        ["--pdf", str(paper), "--dry-run", "--json-logs"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "dry_run"
    log_records = [json.loads(line) for line in result.stderr.splitlines()]
    assert [record["record"]["message"] for record in log_records] == [
        "Starting study extraction",
        "Study extraction finished with status=dry_run",
    ]


def test_click_help_exposes_frontier_default() -> None:
    """Make the supported frontier path discoverable without reading code."""

    result = CliRunner().invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "openai/gpt-5.6-sol" in result.stdout
    assert "--json-logs" in result.stdout


def test_public_entry_points_share_scientific_defaults() -> None:
    """Catch drift without adding another configuration abstraction."""

    names = {
        "model",
        "reasoning_effort",
        "parser",
        "mode",
        "single_call_max_input_tokens",
        "window_input_tokens",
        "max_output_tokens",
        "provider_sort",
        "temperature",
        "heartbeat_seconds",
        "timeout_seconds",
        "document_cache_dir",
        "model_cache_dir",
    }
    python_defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(cli.extract_study).parameters.items()
        if name in names
    }
    config_defaults = {
        name: field.default
        for name, field in ExtractionConfig.__dataclass_fields__.items()
        if name in names
    }
    click_defaults = {
        parameter.name: parameter.default
        for parameter in cli.main.params
        if parameter.name in names
    }

    def comparable(name, value):
        return Path(value).parts if name.endswith("_dir") else value

    assert {key: comparable(key, value) for key, value in python_defaults.items()} == {
        key: comparable(key, value) for key, value in config_defaults.items()
    }
    assert {key: comparable(key, value) for key, value in click_defaults.items()} == {
        key: comparable(key, value) for key, value in config_defaults.items()
    }
