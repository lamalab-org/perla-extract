"""Planning, explicit stage selection, and dispatch must use the same policy."""

import json
from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest
from pydantic import BaseModel

from perla_extract.study_extraction.client import ModelBudgetExceeded, ModelClient
from perla_extract.study_extraction.configuration import ExtractionConfig
from perla_extract.study_extraction.deployment import (
    DeploymentSettings,
    strict_response_schema,
)


class Answer(BaseModel):
    value: int


@pytest.mark.parametrize("missing", ["model", "max_output_tokens"])
def test_unconfigured_client_requires_explicit_request_settings(
    tmp_path, monkeypatch, missing
):
    client = ModelClient(output_dir=tmp_path / "out", cache_dir=tmp_path / "cache")
    monkeypatch.setattr(
        client, "_live", lambda *_: pytest.fail("unconfigured request dispatched")
    )
    options = {"model": "test/model", "max_output_tokens": 10}
    options.pop(missing)
    with pytest.raises(
        ValueError, match="configured policy or explicit model and max_output_tokens"
    ):
        client.complete(
            stage="enrich",
            kind="enrichment",
            slug="answer",
            system="Instructions",
            prompt="Evidence",
            response_model=Answer,
            **options,
        )
    assert client.calls == []


def complete(client, **options):
    return client.complete(
        stage="enrich",
        kind="renamed_claim_repair",
        slug="answer",
        model="ignored/model",
        system="Instructions",
        prompt="Evidence",
        response_model=Answer,
        max_output_tokens=9999,
        reasoning_effort="high",
        **options,
    )


def test_explicit_policy_controls_dispatch_independently_of_call_label(
    tmp_path, monkeypatch
):
    config = ExtractionConfig(
        pdf=Path("paper.pdf"),
        deployments={
            "enrich": DeploymentSettings(
                model="openai/enricher",
                base_url="http://localhost:8000/v1",
                max_input_tokens=2000,
                max_output_tokens=23,
                reasoning_effort="omit",
            ),
            "read": DeploymentSettings(model="wrong/reader"),
            "repair": DeploymentSettings(model="wrong/repairer"),
        },
    )
    client = ModelClient(
        output_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        temperature=0.75,
        stage_policies=config.stage_policies(),
    )
    requests = []

    def respond(body, _failure):
        requests.append(body)
        return {"value": 3}, {
            "cost": 0.2,
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        }

    monkeypatch.setattr(client, "_live", respond)
    assert complete(client).value == 3
    assert requests[0]["model"] == "openai/enricher"
    assert requests[0]["api_base"] == "http://localhost:8000/v1"
    assert requests[0]["max_tokens"] == 23
    assert "reasoning_effort" not in requests[0]
    assert "temperature" not in requests[0]
    policy = config.stage_policy("enrich")
    planned = policy.estimate(
        "Instructions", "Evidence", strict_response_schema(Answer)
    )
    assert client.calls[0]["attempts"][0]["input_estimate"] == planned
    assert client.calls[0]["stage"] == "enrich"
    assert (
        client.budget_status()["reported_cost_usd"] == client.calls[0]["usage"]["cost"]
    )


def test_policy_limit_blocks_dispatch_using_the_planner_estimate(tmp_path, monkeypatch):
    config = ExtractionConfig(
        pdf=Path("paper.pdf"),
        assembly_max_input_tokens=1000,
        deployments={
            "enrich": DeploymentSettings(context_tokens=100, max_output_tokens=25)
        },
    )
    policy = config.stage_policy("enrich")
    assert policy.input_limit() == 75
    planned = policy.estimate(
        "Instructions", "Evidence", strict_response_schema(Answer)
    )
    assert policy.limit_issues(planned["estimated_input_tokens"])
    client = ModelClient(
        output_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        stage_policies=config.stage_policies(),
    )
    monkeypatch.setattr(
        client, "_live", lambda *_: pytest.fail("oversized request dispatched")
    )
    with pytest.raises(ModelBudgetExceeded, match="context_tokens"):
        complete(client)
    assert client.calls[0]["input_estimate"] == planned
    assert client.budget_status()["provider_requests"] == 0


def test_resolved_reader_and_inherited_repair_limits_have_one_source():
    config = ExtractionConfig(
        pdf=Path("paper.pdf"),
        single_call_max_input_tokens=1000,
        claim_window_input_tokens=500,
        assembly_max_input_tokens=700,
        deployments={
            "read": DeploymentSettings(context_tokens=1200, max_output_tokens=300),
            "default": DeploymentSettings(
                model="openai/local",
                base_url="http://localhost:8000/v1",
                max_input_tokens=900,
                max_output_tokens=200,
            ),
            "repair": DeploymentSettings(max_output_tokens=100),
        },
    )
    assert config.stage_policy("read", claim_mode="single").input_limit() == 900
    assert config.stage_policy("read", claim_mode="windowed").input_limit() == 500
    repair = config.stage_policy("repair")
    assert repair.model == "openai/local"
    assert repair.deployment.base_url == "http://localhost:8000/v1"
    assert repair.max_output_tokens == 100
    assert repair.input_limit() == 700
    assert all(
        config.stage_policy(stage).input_limit() == 700
        for stage in ("assemble", "refine", "enrich")
    )


def test_policy_cache_replay_retains_origin_without_new_charges(tmp_path, monkeypatch):
    config = ExtractionConfig(pdf=Path("paper.pdf"))
    options = {
        "cache_dir": tmp_path / "cache",
        "stage_policies": config.stage_policies(),
    }
    first = ModelClient(output_dir=tmp_path / "first", **options)
    monkeypatch.setattr(
        first, "_live", lambda *_: ({"value": 4}, {"cost": 0.2, "total_tokens": 12})
    )
    complete(first)
    replay = ModelClient(output_dir=tmp_path / "replay", **options)
    monkeypatch.setattr(
        replay, "_live", lambda *_: pytest.fail("cache replay dispatched")
    )
    assert (
        replay.complete(
            stage="enrich",
            kind="renamed_claim_repair",
            slug="answer",
            system="Instructions",
            prompt="Evidence",
            response_model=Answer,
        ).value
        == 4
    )
    assert replay.calls[0]["cached_response_usage"]["cost"] == 0.2
    assert replay.budget_status()["reported_cost_usd"] == 0
    assert replay.budget_status()["provider_requests"] == 0
    serialized = json.dumps(replay.calls)
    assert "started_at" not in serialized
    assert "finalized" not in serialized


def test_invalid_json_failure_artifact_cannot_erase_captured_usage(
    tmp_path, monkeypatch
):
    import perla_extract.study_extraction.client as implementation

    original_write = implementation.write_json_atomic

    def write(path, payload):
        if path.name.endswith("attempt-1.failure.json"):
            raise OSError("diagnostic disk unavailable")
        return original_write(path, payload)

    response = SimpleNamespace(
        model_dump=lambda: {
            "choices": [
                {"finish_reason": "stop", "message": {"content": "invalid JSON"}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        },
        _hidden_params={"response_cost": 0.25},
    )
    monkeypatch.setattr(implementation, "write_json_atomic", write)
    monkeypatch.setattr(litellm, "completion", lambda **_: response)
    client = ModelClient(
        output_dir=tmp_path / "out", cache_dir=tmp_path / "cache", heartbeat_seconds=0
    )
    with pytest.raises(OSError, match="diagnostic disk unavailable"):
        complete(client)
    assert client.calls[0]["usage"]["cost"] == 0.25
    assert client.calls[0]["usage"]["total_tokens"] == 12
    assert client.budget_status()["reported_cost_usd"] == 0.25
    assert client.budget_status()["cost_tracking_complete"] is True
    assert client.budget_status()["provider_requests"] == 1
