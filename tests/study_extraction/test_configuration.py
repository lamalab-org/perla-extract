"""Configuration errors must fail before parsing or paying for a model request."""

from pathlib import Path

import pytest

from perla_extract.study_extraction.configuration import (
    ExtractionConfig,
    merge_deployments,
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
        {"langfuse_capture_content": "false"},
        {"model": ""},
        {"deployments": {"read": {"model": "example"}}},
    ],
)
def test_invalid_settings_fail_before_execution(options):
    with pytest.raises(ValueError):
        resolve_config(pdf=Path("paper.pdf"), **options)


def test_every_stage_uses_default_including_repair():
    shared = DeploymentSettings(
        model="openai/local",
        base_url="http://localhost:8000/v1",
        api_key_env="LOCAL_KEY",
        max_output_tokens=4096,
    )
    config = ExtractionConfig(
        pdf=Path("paper.pdf"),
        deployments={
            "default": shared,
            "refine": DeploymentSettings(model="provider/refiner"),
        },
    )
    assert config.effective_deployment("repair") == shared
    assert config.effective_model("refine") == "provider/refiner"
    assert config.effective_deployment("refine").base_url is None
    assert config.effective_deployment("refine").api_key_env is None
    assert config.effective_deployment("refine").max_output_tokens is None
    assert all(
        config.effective_deployment(stage) == shared
        for stage in ("read", "assemble", "repair", "enrich")
    )


def test_toml_relative_paths_and_explicit_overrides(tmp_path):
    path = tmp_path / "method.toml"
    path.write_text(
        '[extraction]\npdf="paper.pdf"\n[stages.default]\nmodel="provider/base"\n[stages.read]\ntokenizer="tokenizer.json"\n'
    )
    config = resolve_config(config_file=path, model="provider/override")
    assert config.pdf == tmp_path / "paper.pdf"
    assert config.effective_model("assemble") == "provider/override"
    assert config.deployments["read"].tokenizer == str(tmp_path / "tokenizer.json")


@pytest.mark.parametrize(
    "text",
    [
        '[extraction]\nmodle="typo"',
        '[stages.unknown]\nmodel="x"',
        '[tracing]\nlangfuse="false"',
        '[tracing]\ncapture_content="false"',
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
            "default": refine,
            "repair": DeploymentSettings(max_output_tokens=512),
        },
    )
    repair = config.effective_deployment("repair")
    assert config.effective_model("repair") == "openai/local"
    assert repair.base_url == refine.base_url
    assert repair.api_key_env == "LOCAL_KEY"
    assert repair.max_output_tokens == 512


def test_tracing_capture_is_opt_in_and_not_a_scientific_factor(tmp_path):
    path = tmp_path / "method.toml"
    path.write_text("[tracing]\nlangfuse=true\ncapture_content=true\n")
    plain = resolve_config("paper.pdf")
    traced = resolve_config("paper.pdf", config_file=path)
    assert not plain.langfuse_capture_content
    assert traced.langfuse and traced.langfuse_capture_content
    assert plain.scientific_settings() == traced.scientific_settings()


def test_model_switch_drops_default_provider_identity_and_credentials(tmp_path):
    path = tmp_path / "method.toml"
    path.write_text("""[stages.default]
model = "openai/local"
base_url = "http://localhost:8000/v1"
api_key_env = "LOCAL_KEY"
revision = "local-weights"
tokenizer = "local-tokenizer.json"
context_tokens = 32768
reasoning_effort = "high"
[stages.read]
model = "openrouter/reader"
[stages.repair]
max_output_tokens = 512
reasoning_effort = "omit"
""")
    config = resolve_config("paper.pdf", config_file=path)
    assert config.effective_deployment("read") == DeploymentSettings(
        model="openrouter/reader"
    )
    repair = config.effective_deployment("repair")
    assert repair.model == "openai/local"
    assert repair.api_key_env == "LOCAL_KEY"
    assert repair.reasoning_effort == "omit"
    assert repair.max_output_tokens == 512
    switched = resolve_config("paper.pdf", config_file=path, model="anthropic/other")
    assert switched.effective_deployment("assemble") == DeploymentSettings(
        model="anthropic/other"
    )
    assert switched.effective_model("read") == "openrouter/reader"


@pytest.mark.parametrize("stage", ["default", "read", "repair"])
@pytest.mark.parametrize(
    "field,value",
    [("base_url", "http://localhost:8000/v1"), ("api_key_env", "LOCAL_KEY")],
)
def test_transport_override_requires_an_explicit_model(tmp_path, stage, field, value):
    path = tmp_path / "method.toml"
    path.write_text(f'[stages.{stage}]\n{field} = "{value}"\n')
    with pytest.raises(ValueError, match="specify model together"):
        resolve_config("paper.pdf", config_file=path)


@pytest.mark.parametrize(
    "key,target",
    [
        ("model", "default.model"),
        ("claim_model", "read.model"),
        ("refinement_model", "refine.model"),
        ("repair_model", "repair.model"),
        ("enrichment_model", "enrich.model"),
        ("max_output_tokens", "default.max_output_tokens"),
    ],
)
def test_duplicate_model_settings_report_their_replacement(tmp_path, key, target):
    path = tmp_path / "old.toml"
    path.write_text(f'[extraction]\n{key} = "old-setting"\n')
    with pytest.raises(ValueError, match=f"has moved to stages.{target}"):
        resolve_config("paper.pdf", config_file=path)


def test_cohort_and_cli_merge_use_the_same_deployment_rules(tmp_path):
    shared = DeploymentSettings(
        model="openai/local",
        base_url="http://localhost:8000/v1",
        api_key_env="LOCAL_KEY",
    )
    tuned = merge_deployments(
        {"default": shared}, {"default": DeploymentSettings(max_output_tokens=512)}
    )
    assert tuned["default"].api_key_env == "LOCAL_KEY"
    assert tuned["default"].max_output_tokens == 512
    switched = merge_deployments(
        tuned, {"default": DeploymentSettings(model="openrouter/other")}
    )
    assert switched["default"].api_key_env is None
    path = tmp_path / "method.toml"
    path.write_text(
        '[stages.default]\nmodel="openai/local"\nbase_url="http://localhost:8000/v1"\napi_key_env="LOCAL_KEY"\n'
    )
    with pytest.raises(ValueError, match="specify model together"):
        resolve_config(
            "paper.pdf", config_file=path, base_url="https://other.example/v1"
        )


def test_equivalent_output_and_reasoning_defaults_have_one_scientific_identity():
    plain = ExtractionConfig(pdf=Path("paper.pdf"))
    explicit = ExtractionConfig(
        pdf=Path("paper.pdf"),
        deployments={
            "read": DeploymentSettings(max_output_tokens=30000, reasoning_effort="omit")
        },
    )
    assert plain.scientific_settings() == explicit.scientific_settings()


def test_direct_defaults_match_cli_and_only_active_settings_are_compared():
    from dataclasses import replace

    from perla_extract.study_extraction.comparison import _settings

    config = resolve_config("paper.pdf")
    assert config.max_model_calls == 1
    assert not any(
        (
            config.use_claim_ledger,
            config.use_refinement,
            config.use_targeted_repair,
            config.use_enrichment,
        )
    )
    assert set(config.scientific_settings()["effective_models"]) == {"assemble"}
    changed = replace(
        config,
        claim_recall_passes=3,
        deployments={"read": DeploymentSettings(model="provider/unused")},
    )
    assert _settings(
        {"scientific_settings": config.scientific_settings()}
    ) == _settings({"scientific_settings": changed.scientific_settings()})
    enabled = replace(changed, use_claim_ledger=True, max_model_calls=5)
    assert "read" in enabled.scientific_settings()["effective_models"]
    assert enabled.scientific_settings()["claim_recall_passes"] == 3
