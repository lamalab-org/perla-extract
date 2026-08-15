import json
from types import SimpleNamespace

import litellm
import pytest

from perla_extract.study_extraction.client import (
    ModelClient,
    ModelCallError,
    _strict_schema,
)
from perla_extract.study_extraction.models import Paper, StudyExtraction


def empty_result() -> dict:
    return StudyExtraction(
        paper=Paper(title=None, doi=None),
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    ).model_dump(mode="json")


def test_only_validated_model_results_enter_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    output = tmp_path / "output"
    client = ModelClient(
        cache_dir=cache,
        output_dir=output,
    )
    monkeypatch.setattr(client, "_live", lambda body, failure: (empty_result(), {}))
    first = client.complete(
        kind="test",
        slug="test",
        model="test/model",
        system="system",
        prompt="prompt",
        response_model=StudyExtraction,
        max_output_tokens=100,
        reasoning_effort="none",
    )

    second_client = ModelClient(
        cache_dir=cache,
        output_dir=output,
    )
    monkeypatch.setattr(
        second_client,
        "_live",
        lambda body, failure: (_ for _ in ()).throw(AssertionError("cache missed")),
    )
    second = second_client.complete(
        kind="test",
        slug="test_cached",
        model="test/model",
        system="system",
        prompt="prompt",
        response_model=StudyExtraction,
        max_output_tokens=100,
        reasoning_effort="none",
    )

    assert first == second
    assert second_client.calls[0]["cache_hit"] is True


def test_provider_schema_requires_nullable_defaulted_fields():
    schema = _strict_schema(StudyExtraction)
    family = schema["$defs"]["DeviceFamily"]
    assert "absorber_formula" in family["required"]
    assert family["additionalProperties"] is False


def test_invalid_model_cache_is_replaced(tmp_path, monkeypatch):
    """Treat a partial cache write as a miss instead of aborting extraction."""

    cache = tmp_path / "cache"
    client = ModelClient(
        cache_dir=cache,
        output_dir=tmp_path / "output",
    )
    monkeypatch.setattr(client, "_live", lambda body, failure: (empty_result(), {}))
    client.complete(
        kind="test",
        slug="first",
        model="test/model",
        system="system",
        prompt="prompt",
        response_model=StudyExtraction,
        max_output_tokens=100,
        reasoning_effort=None,
    )
    next(cache.glob("*.json")).write_text("{", encoding="utf-8")

    replacement = ModelClient(
        cache_dir=cache,
        output_dir=tmp_path / "output",
    )
    live_calls = 0

    def complete_live(body, failure):
        nonlocal live_calls
        live_calls += 1
        return empty_result(), {}

    monkeypatch.setattr(replacement, "_live", complete_live)
    replacement.complete(
        kind="test",
        slug="second",
        model="test/model",
        system="system",
        prompt="prompt",
        response_model=StudyExtraction,
        max_output_tokens=100,
        reasoning_effort=None,
    )

    assert live_calls == 1
    assert replacement.calls[0]["cache_hit"] is False


def test_length_truncation_is_not_retried(tmp_path, monkeypatch):
    """A deterministic output-limit failure must not trigger another paid call."""

    client = ModelClient(
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
    )
    attempts = 0

    def truncated(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise ModelCallError("length", retryable=False)

    monkeypatch.setattr(client, "_live", truncated)
    with pytest.raises(ModelCallError, match="length"):
        client.complete(
            kind="test",
            slug="test",
            model="test/model",
            system="system",
            prompt="prompt",
            response_model=StudyExtraction,
            max_output_tokens=1,
            reasoning_effort=None,
        )
    assert attempts == 1


def test_litellm_timeout_becomes_retryable_model_error(tmp_path, monkeypatch):
    """Keep normalized provider failures inspectable without provider-specific code."""

    def time_out(**_kwargs):
        raise litellm.Timeout("slow", model="test/model", llm_provider="test")

    monkeypatch.setattr(litellm, "completion", time_out)
    client = ModelClient(
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        heartbeat_seconds=0,
    )
    failure_path = tmp_path / "out/requests/broken.failure.json"

    with pytest.raises(ModelCallError) as caught:
        client._live({"model": "test/model"}, failure_path)

    assert caught.value.retryable is True
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["error_type"] == "Timeout"


def test_litellm_request_preserves_schema_and_provider_prefix(tmp_path):
    client = ModelClient(
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        timeout_seconds=42,
        temperature=None,
    )

    request = client._request(
        model="openrouter/openai/example",
        system="system",
        prompt="prompt",
        schema={"type": "object"},
        max_output_tokens=123,
        reasoning_effort="low",
    )

    assert request["model"] == "openrouter/openai/example"
    assert request["reasoning_effort"] == "low"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["timeout"] == 42
    assert "temperature" not in request


def test_litellm_response_is_normalized_to_result_and_usage(tmp_path, monkeypatch):
    payload = {
        "model": "provider-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps(empty_result())},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    response = SimpleNamespace(
        model_dump=lambda: payload,
        _hidden_params={"custom_llm_provider": "test", "response_cost": 0.01},
    )
    monkeypatch.setattr(litellm, "completion", lambda **_kwargs: response)
    client = ModelClient(
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        heartbeat_seconds=0,
    )

    result, usage = client._live({"model": "test/model"}, tmp_path / "failure.json")

    assert result == empty_result()
    assert usage["provider"] == "test"
    assert usage["cost"] == 0.01
    assert usage["total_tokens"] == 15
