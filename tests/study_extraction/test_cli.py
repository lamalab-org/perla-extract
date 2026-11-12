"""Tests for command-line option translation."""

import json
import os
from pathlib import Path

from click.testing import CliRunner

from perla_extract.study_extraction import cli
from perla_extract.study_extraction.workflow import ExtractionConfig


def test_omit_reasoning_leaves_parameter_out(monkeypatch, tmp_path: Path) -> None:
    """Translate the CLI sentinel to None for APIs without reasoning support."""

    captured = {}

    def fake_run(config):
        captured["config"] = config
        return {"status": "complete"}

    monkeypatch.setattr(cli, "run_extraction", fake_run)

    result = cli.extract_study(
        pdf=str(tmp_path / "paper.pdf"),
        reasoning_effort="omit",
        dry_run=True,
    )

    assert result == {"status": "complete"}
    assert captured["config"].reasoning_effort is None


def test_env_file_loads_any_provider_key_without_overwriting_process_env(
    monkeypatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENROUTER_API_KEY=from-file\nOPENAI_API_KEY=direct-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-process")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "run_extraction", lambda config: {"status": "complete"})

    cli.extract_study(pdf=tmp_path / "paper.pdf", env_file=env_file, dry_run=True)

    assert os.environ["OPENROUTER_API_KEY"] == "from-process"
    assert os.environ["OPENAI_API_KEY"] == "direct-key"


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
    assert "openai/gpt-5.2" in result.stdout
    assert "--json-logs" in result.stdout


def test_python_and_cli_resolve_the_same_scientific_defaults(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(
        cli,
        "run_extraction",
        lambda config: captured.append(config) or {"status": "complete"},
    )
    pdf = tmp_path / "paper.pdf"
    pdf.touch()
    cli.extract_study(pdf)
    result = CliRunner().invoke(cli.main, ["--pdf", str(pdf)])
    assert result.exit_code == 0, result.output
    assert captured[0].scientific_settings() == captured[1].scientific_settings()
    assert (
        captured[0].scientific_settings()
        == ExtractionConfig(pdf=pdf).scientific_settings()
    )


def test_explicit_cli_settings_override_toml_without_resetting_other_options(
    monkeypatch, tmp_path
):
    captured = []
    monkeypatch.setattr(
        cli,
        "run_extraction",
        lambda config: captured.append(config) or {"status": "complete"},
    )
    (tmp_path / "paper.pdf").touch()
    config = tmp_path / "method.toml"
    config.write_text("""[extraction]
pdf = "paper.pdf"
model = "openai/local-model"
claim_recall_passes = 1
use_refinement = false
[stages.read]
model = "openai/local-reader"
base_url = "http://localhost:8000/v1"
""")
    result = CliRunner().invoke(
        cli.main, ["--config", str(config), "--claim-recall-passes", "3"]
    )
    assert result.exit_code == 0, result.output
    resolved = captured[0]
    assert resolved.pdf == tmp_path / "paper.pdf"
    assert resolved.claim_recall_passes == 3
    assert not resolved.use_refinement
    assert resolved.effective_model("read") == "openai/local-reader"
    assert resolved.effective_model("assemble") == "openai/local-model"


def test_explicit_missing_env_file_is_an_error(tmp_path):
    result = CliRunner().invoke(cli.main, ["--env-file", str(tmp_path / "missing")])
    assert result.exit_code != 0
    assert "environment file does not exist" in result.output
