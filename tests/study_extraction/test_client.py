import http.client
import json
import urllib.request

import pytest

from perla_extract.study_extraction.client import (
    ModelCallError,
    OpenRouterClient,
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
    client = OpenRouterClient(
        api_key="test-key",
        cache_dir=cache,
        output_dir=output,
        provider_sort="none",
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

    second_client = OpenRouterClient(
        api_key=None,
        cache_dir=cache,
        output_dir=output,
        provider_sort="none",
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
    client = OpenRouterClient(
        api_key="test",
        cache_dir=cache,
        output_dir=tmp_path / "output",
        provider_sort="none",
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

    replacement = OpenRouterClient(
        api_key="test",
        cache_dir=cache,
        output_dir=tmp_path / "output",
        provider_sort="none",
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

    client = OpenRouterClient(
        api_key="test",
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


def test_incomplete_http_response_becomes_retryable_model_error(tmp_path, monkeypatch):
    """Turn a truncated response into an inspectable model-call failure."""

    class BrokenResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            raise http.client.IncompleteRead(b'{"choices":')

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: BrokenResponse(),
    )
    client = OpenRouterClient(
        api_key="test",
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        heartbeat_seconds=0,
    )
    failure_path = tmp_path / "out/requests/broken.failure.json"

    with pytest.raises(ModelCallError) as caught:
        client._live({}, failure_path)

    assert caught.value.retryable is True
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["partial_response"] == '{"choices":'
