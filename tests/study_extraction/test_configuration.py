"""Configuration errors must fail before parsing or paying for a model request."""

from pathlib import Path

import pytest

from perla_extract.study_extraction.configuration import (
    ExtractionConfig,
    resolve_config,
)
from perla_extract.study_extraction.deployment import DeploymentSettings


@pytest.mark.parametrize(
    "options",
    [
        {"claim_recall_passes": 1.5},
        {"max_model_calls": True},
        {"max_total_tokens": 0},
        {"timeout_seconds": float("inf")},
        {"temperature": float("nan")},
        {"use_refinement": "false"},
        {"langfuse": 1},
        {"model": ""},
        {"deployments": {"read": {"model": "example"}}},
    ],
)
def test_invalid_settings_fail_before_execution(options):
    with pytest.raises(ValueError):
        ExtractionConfig(pdf=Path("paper.pdf"), **options)


def test_repair_inherits_transport_with_model():
    refine = DeploymentSettings(
        model="openai/local",
        base_url="http://localhost:8000/v1",
        max_output_tokens=4096,
    )
    config = ExtractionConfig(pdf=Path("paper.pdf"), deployments={"refine": refine})
    assert config.effective_model("repair") == "openai/local"
    assert config.effective_deployment("repair") == refine
    separate = ExtractionConfig(
        pdf=Path("paper.pdf"),
        deployments={"refine": refine},
        repair_model="provider/other",
    )
    assert separate.effective_model("repair") == "provider/other"
    assert separate.effective_deployment("repair").base_url is None


def test_toml_relative_paths_and_explicit_overrides(tmp_path):
    path = tmp_path / "method.toml"
    path.write_text(
        '[extraction]\npdf="paper.pdf"\nmodel="provider/base"\n[stages.read]\ntokenizer="tokenizer.json"\n'
    )
    config = resolve_config(config_file=path, model="provider/override")
    assert config.pdf == tmp_path / "paper.pdf"
    assert config.model == "provider/override"
    assert config.deployments["read"].tokenizer == str(tmp_path / "tokenizer.json")


@pytest.mark.parametrize(
    "text",
    [
        '[extraction]\nmodle="typo"',
        '[stages.unknown]\nmodel="x"',
        '[tracing]\nlangfuse="false"',
    ],
)
def test_unknown_or_mistyped_file_settings_fail(tmp_path, text):
    path = tmp_path / "method.toml"
    path.write_text(text)
    with pytest.raises(ValueError):
        resolve_config("paper.pdf", config_file=path)


def test_partial_repair_limits_keep_inherited_self_hosted_transport():
    refine = DeploymentSettings(
        model="openai/local",
        base_url="http://localhost:8000/v1",
        api_key_env="LOCAL_KEY",
        max_output_tokens=4096,
    )
    config = ExtractionConfig(
        pdf=Path("paper.pdf"),
        deployments={
            "refine": refine,
            "repair": DeploymentSettings(max_output_tokens=512),
        },
    )
    repair = config.effective_deployment("repair")
    assert config.effective_model("repair") == "openai/local"
    assert repair.base_url == refine.base_url
    assert repair.api_key_env == "LOCAL_KEY"
    assert repair.max_output_tokens == 512
