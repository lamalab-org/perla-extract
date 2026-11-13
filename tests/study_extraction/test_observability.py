"""Exercise execution/accounting outcomes without provider or telemetry services."""

import inspect
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import litellm
import pytest
from pydantic import BaseModel

from perla_extract.study_extraction.client import (
    ModelBudgetExceeded,
    ModelCallError,
    ModelClient,
    summarize_calls,
)
from perla_extract.study_extraction.deployment import (
    DeploymentSettings,
    estimate_request_input,
    strict_response_schema,
)
from perla_extract.study_extraction.logging import logger
from perla_extract.study_extraction.observability import EventJournal, read_events
from perla_extract.study_extraction.tracing import (
    LangfuseAdapter,
    create_langfuse_adapter,
)


class Answer(BaseModel):
    value: int


def response(
    value, *, identifier="answer", reasoning=2, cached=3, cost=0.01, usage_missing=False
):
    payload = {
        "id": identifier,
        "model": "resolved-model",
        "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
        ],
        "usage": {}
        if usage_missing
        else {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }
    return SimpleNamespace(
        model_dump=lambda: payload,
        _hidden_params={"custom_llm_provider": "test", "response_cost": cost},
    )


def client(tmp_path, **options):
    return ModelClient(
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "out",
        heartbeat_seconds=0,
        **options,
    )


def complete(model_client, *, slug="record", prompt="Evidence"):
    return model_client.complete(
        kind="complete_study",
        slug=slug,
        model="test/model",
        system="System instructions",
        prompt=prompt,
        response_model=Answer,
        max_output_tokens=10,
        reasoning_effort="high",
    )


def test_validation_attempts_keep_usage_and_rejected_response(tmp_path, monkeypatch):
    responses = iter(
        [
            response({"value": "invalid"}, identifier="first", reasoning=11, cached=30),
            response({"value": 7}, identifier="second", reasoning=17, cached=40),
        ]
    )
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: next(responses))
    model_client = client(tmp_path)
    assert complete(model_client).value == 7
    call = model_client.calls[0]
    assert call["usage"]["completion_tokens_details"]["reasoning_tokens"] == 28
    assert call["usage"]["prompt_tokens_details"]["cached_tokens"] == 70
    assert call["usage"]["response_ids"] == ["first", "second"]
    assert call["usage"]["total_tokens"] == 300
    assert call["usage"]["cost"] == 0.02
    assert (tmp_path / "out/requests/record.attempt-1.failure.json").exists()
    attempts = [
        event
        for event in read_events(model_client.journal.path)
        if event["event"] == "attempt.finished"
    ]
    assert [event["status"] for event in attempts] == ["validation_rejected", "success"]
    assert attempts[0]["request_sha256"] != attempts[1]["request_sha256"]


def test_timeout_then_success_preserves_failure_and_counts_responses(
    tmp_path, monkeypatch
):
    attempts = 0

    def send(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise litellm.Timeout(
                "synthetic timeout", model="test/model", llm_provider="test"
            )
        return response({"value": 1})

    monkeypatch.setattr(litellm, "completion", send)
    monkeypatch.setattr(
        "perla_extract.study_extraction.client.time.sleep", lambda _: None
    )
    model_client = client(tmp_path)
    complete(model_client)
    usage = summarize_calls(model_client.calls)
    assert usage["provider_attempts"] == 2
    assert usage["responses_received"] == 1
    assert usage["cost_tracking_complete"] is False
    assert usage["total_cost_usd"] is None
    assert model_client.calls[0]["attempts"][0]["usage"]["error_type"] == "Timeout"
    assert (tmp_path / "out/requests/record.attempt-1.failure.json").exists()


def test_budget_block_is_not_a_live_call(tmp_path, monkeypatch):
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: response({"value": 1}))
    model_client = client(tmp_path, max_model_calls=1)
    complete(model_client)
    with pytest.raises(ModelBudgetExceeded):
        complete(model_client, slug="blocked", prompt="Different evidence")
    usage = summarize_calls(model_client.calls)
    assert usage["logical_calls"] == 2
    assert usage["live_calls"] == usage["provider_attempts"] == 1
    assert usage["blocked_calls"] == 1


def test_cache_replay_has_origin_but_no_new_attempt_or_charge(tmp_path, monkeypatch):
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: response({"value": 1}))
    original = client(tmp_path)
    complete(original)
    monkeypatch.setattr(
        litellm, "completion", lambda **kwargs: pytest.fail("cache replay sent request")
    )
    replay = client(tmp_path)
    complete(replay, slug="replay")
    usage = summarize_calls(replay.calls)
    assert usage["cache_hits"] == 1
    assert usage["provider_attempts"] == usage["total_tokens"] == usage["cost"] == 0
    assert replay.calls[0]["cache_origin"]["run_id"] == original.journal.run_id


def test_deployment_identity_and_credentials_are_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ENDPOINT_KEY", "test-secret-never-save")
    sent = []

    def send(**kwargs):
        sent.append(kwargs)
        return response({"value": 1})

    monkeypatch.setattr(litellm, "completion", send)
    settings = dict(
        model="openai/self-hosted",
        base_url="http://localhost:8000/v1",
        api_key_env="TEST_ENDPOINT_KEY",
        quantization="awq",
        chat_template="qwen",
        chat_template_kwargs={"enable_thinking": False},
        reasoning_effort="omit",
        top_k=20,
    )
    first = client(
        tmp_path,
        deployments={"assemble": DeploymentSettings(**settings, revision="r1")},
    )
    complete(first)
    second = client(
        tmp_path,
        deployments={"assemble": DeploymentSettings(**settings, revision="r2")},
    )
    complete(second, slug="second")
    assert len(sent) == 2
    assert sent[0]["api_base"] == "http://localhost:8000/v1"
    assert sent[0]["api_key"] == "test-secret-never-save"
    assert "reasoning_effort" not in sent[0]
    assert sent[0]["extra_body"] == {
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert first.calls[0]["cache_key_sha256"] != second.calls[0]["cache_key_sha256"]
    for artifact in tmp_path.rglob("*.json*"):
        assert "test-secret-never-save" not in artifact.read_text()


@pytest.mark.parametrize(
    ("model", "base_url", "host"),
    [
        ("openrouter/qwen/qwen3-32b", None, "openrouter.ai"),
        ("openai/qwen/qwen3-32b", "https://openrouter.ai/api/v1", "openrouter.ai"),
        (
            "openai/alias-code",
            "https://api.blablador.fz-juelich.de/v1",
            "api.blablador.fz-juelich.de",
        ),
    ],
)
def test_provider_http_contract_and_accounting(
    tmp_path, monkeypatch, model, base_url, host
):
    """Exercise LiteLLM serialization with synthetic HTTP responses and no network."""
    monkeypatch.setenv("TRANSPORT_TEST_KEY", "secret-never-save")
    sent = []
    router = host == "openrouter.ai"

    def send(self, request, **kwargs):
        assert request.url.host == host
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer secret-never-save"
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "fixture-answer",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                **({"provider": "fixture-backend"} if router else {}),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"value":7}'},
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 5,
                    "total_tokens": 105,
                    **({"cost": 0.0123} if router else {}),
                },
            },
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    model_client = client(
        tmp_path,
        deployments={
            "assemble": DeploymentSettings(
                model=model,
                base_url=base_url,
                api_key_env="TRANSPORT_TEST_KEY",
                reasoning_effort="omit",
            )
        },
    )
    assert complete(model_client).value == 7
    assert len(sent) == 1
    assert sent[0]["model"] == model.split("/", 1)[1]
    assert sent[0].get("provider") == ({"require_parameters": True} if router else None)
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    assert "reasoning_effort" not in sent[0]
    usage = summarize_calls(model_client.calls)
    assert usage["upstream_providers"] == (["fixture-backend"] if router else [])
    assert usage["total_cost_usd"] == (0.0123 if router else None)
    assert usage["cost_tracking_complete"] is router
    assert usage["total_tokens"] == 105
    saved = json.loads((tmp_path / "out/requests/record.request.json").read_text())
    if router:
        assert saved["extra_body"]["provider"]["require_parameters"] is True
    else:
        assert "extra_body" not in saved
    for artifact in tmp_path.rglob("*.json*"):
        assert "secret-never-save" not in artifact.read_text()


def test_retry_request_is_checked_against_context_limit(tmp_path, monkeypatch):
    schema = strict_response_schema(Answer)
    initial = estimate_request_input("System instructions", "Evidence", schema)[
        "estimated_input_tokens"
    ]
    count = 0

    def send(**kwargs):
        nonlocal count
        count += 1
        return response({"value": "invalid" * 200})

    monkeypatch.setattr(litellm, "completion", send)
    model_client = client(
        tmp_path,
        deployments={"assemble": DeploymentSettings(context_tokens=initial + 11)},
    )
    with pytest.raises(ModelBudgetExceeded, match="context_tokens"):
        complete(model_client)
    assert count == 1
    assert model_client.calls[0]["status"] == "blocked"
    assert model_client.calls[0]["usage"]["total_tokens"] == 150


@pytest.mark.parametrize("reported", [0.0, 0.0123])
def test_provider_reported_cost_takes_precedence_over_adapter_estimate(
    tmp_path, monkeypatch, reported
):
    payload = response({"value": 7}).model_dump()
    payload["usage"]["cost"] = reported
    monkeypatch.setattr(
        litellm,
        "completion",
        lambda **kwargs: SimpleNamespace(
            model_dump=lambda: payload,
            _hidden_params={"response_cost": 1.0},
        ),
    )
    model_client = client(tmp_path)
    complete(model_client)
    usage = summarize_calls(model_client.calls)
    assert usage["total_cost_usd"] == reported
    assert usage["cost_sources"] == ["provider_reported_usage"]
    assert usage["cost_tracking_complete"]


def test_input_limit_includes_system_and_response_schema(tmp_path, monkeypatch):
    estimate = estimate_request_input(
        "System instructions", "Evidence", strict_response_schema(Answer)
    )
    model_client = client(
        tmp_path,
        deployments={
            "assemble": DeploymentSettings(
                max_input_tokens=estimate["estimated_input_tokens"] - 1
            )
        },
    )
    monkeypatch.setattr(
        litellm, "completion", lambda **kwargs: pytest.fail("overlimit request sent")
    )
    with pytest.raises(ModelBudgetExceeded, match="max_input_tokens"):
        complete(model_client)
    assert model_client.budget_status()["provider_requests"] == 0
    assert model_client.calls[0]["input_estimate"]["components"]["system"] > 0
    assert model_client.calls[0]["input_estimate"]["components"]["response_schema"] > 0


def test_token_budget_checks_before_and_after_usage(tmp_path, monkeypatch):
    model_client = client(tmp_path, max_total_tokens=1)
    monkeypatch.setattr(
        litellm,
        "completion",
        lambda **kwargs: pytest.fail("token overbudget request sent"),
    )
    with pytest.raises(ModelBudgetExceeded, match="token budget"):
        complete(model_client)
    assert model_client.budget_status()["provider_requests"] == 0
    # Missing usage on a self-hosted endpoint must not be interpreted as free work.
    model_client = client(tmp_path / "missing", max_total_tokens=10_000)
    monkeypatch.setattr(
        litellm,
        "completion",
        lambda **kwargs: response({"value": 1}, cost=None, usage_missing=True),
    )
    complete(model_client)
    assert model_client.budget_status()["token_tracking_complete"] is False
    assert model_client.budget_status()["total_cost_usd"] is None
    with pytest.raises(ModelBudgetExceeded, match="token usage is unavailable"):
        complete(model_client, slug="second", prompt="Other evidence")


def test_elapsed_budget_prevents_send(tmp_path, monkeypatch):
    model_client = client(tmp_path, max_elapsed_seconds=1)
    model_client._started -= 2
    monkeypatch.setattr(
        litellm, "completion", lambda **kwargs: pytest.fail("expired run sent request")
    )
    with pytest.raises(ModelBudgetExceeded, match="elapsed-time"):
        complete(model_client)
    assert model_client.budget_status()["provider_requests"] == 0


def test_stage_interruption_and_partial_journal_tail(tmp_path):
    journal = EventJournal(tmp_path)
    journal.emit("run.started")
    with pytest.raises(KeyboardInterrupt):
        with journal.stage("read"):
            raise KeyboardInterrupt()
    events = read_events(journal.path)
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert events[-1]["status"] == "interrupted"
    assert events[1]["stage_id"] == events[2]["stage_id"]
    with journal.path.open("a") as stream:
        stream.write('{"interrupted":')
    assert read_events(journal.path) == events


def test_telemetry_failure_cannot_change_model_result(tmp_path, monkeypatch):
    class Broken:
        def handle(self, event):
            raise RuntimeError("telemetry unavailable")

    journal = EventJournal(tmp_path / "out", adapter=Broken())
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: response({"value": 42}))
    model_client = client(tmp_path, journal=journal)
    assert complete(model_client).value == 42
    events = read_events(journal.path)
    assert sum(event["event"] == "telemetry.failed" for event in events) == 1
    assert events[-1]["event"] == "call.finished"


class FakeLangfuse:
    def __init__(self):
        self.opened = []
        self.updated = []
        self.closed = []
        self.flushed = False
        self.parents = []
        self.active = []

    @contextmanager
    def start_as_current_observation(self, **options):
        self.opened.append(options)
        self.parents.append(
            (options["metadata"]["event"], self.active[-1] if self.active else None)
        )
        self.active.append(options["metadata"]["event"])
        try:
            yield SimpleNamespace(update=lambda **values: self.updated.append(values))
        finally:
            self.active.pop()
            self.closed.append(options["name"])

    def flush(self):
        self.flushed = True


def test_langfuse_has_one_generation_per_attempt_and_no_generation_for_cache(
    tmp_path, monkeypatch
):
    sdk = FakeLangfuse()
    journal = EventJournal(tmp_path / "out", adapter=LangfuseAdapter(sdk))
    journal.emit("run.started")
    model_client = client(tmp_path, journal=journal)
    responses = iter([response({"value": "invalid"}), response({"value": 5})])
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: next(responses))
    with journal.stage("assemble"):
        complete(model_client)
        complete(model_client, slug="cached")
    journal.emit("run.finished", status="complete")
    assert sum(item["as_type"] == "generation" for item in sdk.opened) == 2
    assert any(item.get("name") == "Local response cache hit" for item in sdk.updated)
    assert sum("usage_details" in update for update in sdk.updated) == 2
    assert len(sdk.opened) == len(sdk.closed)
    assert all(
        parent == "call.started"
        for event, parent in sdk.parents
        if event == "attempt.started"
    )
    assert sdk.flushed
    assert all(item["input"] for item in sdk.opened)
    assert all(item["output"] for item in sdk.updated)
    assert "Stage: assemble" in sdk.closed
    assert "Call: complete_study" in sdk.closed
    assert "System instructions" not in json.dumps(sdk.opened)
    assert '"response":' not in json.dumps(sdk.updated)
    assert create_langfuse_adapter(enabled=False) is None


@pytest.mark.parametrize("invalid", [{"value": "invalid"}, None])
def test_langfuse_content_capture_preserves_rejected_and_repaired_responses(
    tmp_path, monkeypatch, invalid
):
    sdk = FakeLangfuse()
    output = tmp_path / "out"
    journal = EventJournal(
        output, adapter=LangfuseAdapter(sdk, output_dir=output, capture_content=True)
    )
    monkeypatch.setenv("TEST_ENDPOINT_KEY", "secret-never-export")
    model_client = client(
        tmp_path,
        journal=journal,
        deployments={"assemble": DeploymentSettings(api_key_env="TEST_ENDPOINT_KEY")},
    )
    responses = iter([response(invalid), response({"value": 5})])
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: next(responses))
    journal.emit("run.started", paper="paper.pdf")
    with journal.stage("assemble"):
        complete(model_client)
    (output / "extraction.json").write_text('{"value": 5}')
    journal.emit("run.finished", status="complete")
    inputs = [item["input"] for item in sdk.opened if item["as_type"] == "generation"]
    assert inputs[0]["messages"][0]["content"] == "System instructions"
    assert inputs[0]["messages"][1]["content"] == "Evidence"
    assert inputs[0]["response_format"]["json_schema"]["strict"] is True
    assert len(inputs[1]["messages"]) > len(inputs[0]["messages"])
    outputs = [item["output"] for item in sdk.updated if "usage_details" in item]
    assert outputs[0]["response"] == invalid
    assert outputs[0]["validation_errors"]
    assert outputs[1]["response"] == {"value": 5}
    assert sdk.updated[-1]["output"]["extraction"] == {"value": 5}
    assert "secret-never-export" not in json.dumps(sdk.opened + sdk.updated)


def test_langfuse_content_capture_includes_invalid_json(tmp_path, monkeypatch):
    sdk = FakeLangfuse()
    output = tmp_path / "out"
    journal = EventJournal(
        output, adapter=LangfuseAdapter(sdk, output_dir=output, capture_content=True)
    )
    payload = response({"value": 1}).model_dump()
    payload["choices"][0] = {
        "finish_reason": "length",
        "message": {"content": '{"value": '},
    }
    monkeypatch.setattr(
        litellm,
        "completion",
        lambda **kwargs: SimpleNamespace(model_dump=lambda: payload),
    )
    with pytest.raises(ModelCallError, match="invalid JSON"):
        complete(client(tmp_path, journal=journal))
    exported = next(item for item in sdk.updated if "usage_details" in item)
    assert exported["output"]["response"] == '{"value": '
    assert exported["output"]["finish_reason"] == "length"
    assert exported["level"] == "ERROR"


def test_langfuse_capture_does_not_read_outside_run(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (tmp_path / "private.json").write_text('{"messages": "private"}')
    sdk = FakeLangfuse()
    adapter = LangfuseAdapter(sdk, output_dir=output, capture_content=True)
    journal = EventJournal(output, adapter=adapter)
    journal.emit("attempt.started", attempt_id="test", artifact="../private.json")
    journal.emit("attempt.finished", attempt_id="test", status="failed")
    assert "messages" not in sdk.opened[0]["input"]


def test_installed_langfuse_sdk_accepts_adapter_arguments():
    sdk = pytest.importorskip("langfuse")
    from langfuse._client.span import LangfuseGeneration

    assert {"name", "as_type", "metadata", "model", "input"} <= set(
        inspect.signature(sdk.Langfuse.start_as_current_observation).parameters
    )
    assert {"usage_details", "cost_details", "metadata", "level", "output"} <= set(
        inspect.signature(LangfuseGeneration.update).parameters
    )


@pytest.mark.parametrize("url_variable", ["LANGFUSE_BASE_URL", "LANGFUSE_HOST"])
def test_langfuse_exports_to_normalized_url(tmp_path, monkeypatch, url_variable):
    sdk = pytest.importorskip("langfuse")
    import gzip

    import requests
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.sdk.trace import TracerProvider

    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.setenv(url_variable, "https://tracing.example.test/")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-" + url_variable)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-secret")
    sdk_client = sdk.Langfuse
    monkeypatch.setattr(
        sdk,
        "Langfuse",
        lambda **options: sdk_client(**options, tracer_provider=TracerProvider()),
    )
    sent = []

    def send(self, request, **kwargs):
        body = request.body
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        sent.append((request.url, ExportTraceServiceRequest.FromString(body)))
        response = requests.Response()
        response.status_code = 200
        response._content = b""
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    adapter = LangfuseAdapter()
    try:
        journal = EventJournal(tmp_path, adapter=adapter)
        journal.emit("run.started")
        with journal.stage("connection_check"):
            pass
        journal.emit("run.finished", status="complete")
        assert len(sent) == 1
        url, request = sent[0]
        assert url == "https://tracing.example.test/api/public/otel/v1/traces"
        spans = [
            span
            for resource in request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        ]
        assert len(spans) == 2
        assert len({span.trace_id for span in spans}) == 1
        for span in spans:
            attributes = {item.key: item.value.string_value for item in span.attributes}
            assert json.loads(attributes["langfuse.observation.input"])
            assert json.loads(attributes["langfuse.observation.output"])
        assert not any(
            e["event"] == "telemetry.failed" for e in read_events(journal.path)
        )
    finally:
        adapter.client.shutdown()


@pytest.mark.parametrize(
    "options",
    [
        {"base_url": "https://secret@localhost/v1"},
        {"base_url": "http://localhost/v1?api_key=secret"},
        {"top_k": 5},
        {"chat_template_kwargs": {"enable_thinking": True}},
    ],
)
def test_unsupported_or_credential_bearing_deployments_fail_explicitly(options):
    with pytest.raises(ValueError):
        DeploymentSettings(**options)


def test_console_progress_keeps_execution_ids(tmp_path, monkeypatch):
    records = []
    sink = logger.add(lambda message: records.append(message.record))
    try:
        journal = EventJournal(tmp_path / "out")
        monkeypatch.setattr(
            litellm, "completion", lambda **kwargs: response({"value": 1})
        )
        model_client = client(tmp_path, journal=journal)
        with journal.stage("assemble") as outcome:
            complete(model_client)
        progress = [record for record in records if record["extra"].get("call_id")]
        assert progress
        assert all(record["extra"]["run_id"] == journal.run_id for record in progress)
        assert all(
            record["extra"]["stage_id"] == outcome.stage_id for record in progress
        )
    finally:
        logger.remove(sink)


def test_explicit_tracing_rejects_duplicate_auto_instrumentation(monkeypatch):
    monkeypatch.setattr(litellm, "success_callback", ["langfuse_otel"])
    with pytest.raises(RuntimeError, match="two instrumentation paths"):
        create_langfuse_adapter(enabled=True, client=FakeLangfuse())


def test_explicit_tracing_with_missing_credentials_has_actionable_error(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LANGFUSE_PUBLIC_KEY"):
        LangfuseAdapter()


@pytest.mark.parametrize(
    "options",
    [
        {"context_tokens": True},
        {"max_output_tokens": 2.5},
        {"temperature": float("nan")},
        {"top_p": float("inf")},
        {"reasoning_effort": "unknown"},
        {"revision": " "},
        {"chat_template_kwargs": {"x": float("nan")}},
    ],
)
def test_invalid_deployment_values_are_rejected(options):
    with pytest.raises(ValueError):
        DeploymentSettings(**options)


def test_unexpected_transport_failure_remains_in_call_accounting(tmp_path, monkeypatch):
    model_client = client(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic transport failure")

    monkeypatch.setattr(model_client, "_live", fail)
    with pytest.raises(RuntimeError, match="synthetic transport"):
        complete(model_client)
    assert len(model_client.calls) == 1
    assert model_client.calls[0]["status"] == "unexpected_error"
    usage = summarize_calls(model_client.calls)
    assert usage["provider_attempts"] == 1
    assert usage["cost_tracking_complete"] is False
    assert read_events(model_client.journal.path)[-1]["event"] == "call.finished"


def test_provider_error_cannot_leak_endpoint_credentials(tmp_path, monkeypatch):
    secret = "synthetic-sensitive-api-key"
    monkeypatch.setenv("TEST_ENDPOINT_KEY", secret)

    def fail(**kwargs):
        raise litellm.BadRequestError(
            f"error echoed credential {secret}", model="test/model", llm_provider="test"
        )

    monkeypatch.setattr(litellm, "completion", fail)
    model_client = client(
        tmp_path,
        deployments={
            "assemble": DeploymentSettings(
                base_url="http://localhost:8000/v1", api_key_env="TEST_ENDPOINT_KEY"
            )
        },
    )
    with pytest.raises(RuntimeError) as caught:
        complete(model_client)
    assert secret not in str(caught.value)
    for artifact in tmp_path.rglob("*.json*"):
        assert secret not in artifact.read_text()


def test_cache_write_failure_retains_valid_result_and_paid_work(tmp_path, monkeypatch):
    import perla_extract.study_extraction.client as implementation

    original = implementation.write_json_atomic

    def write(path, payload):
        if path.parent == tmp_path / "cache":
            raise OSError("synthetic cache write failure")
        return original(path, payload)

    monkeypatch.setattr(implementation, "write_json_atomic", write)
    monkeypatch.setattr(
        litellm, "completion", lambda **kwargs: response({"value": 7}, cost=0.25)
    )
    model_client = client(tmp_path)
    assert complete(model_client).value == 7
    assert len(model_client.calls) == 1
    assert model_client.calls[0]["cache_write_error"] == "OSError"
    assert model_client.calls[0]["status"] == "success"
    usage = summarize_calls(model_client.calls)
    assert usage["cost"] == 0.25
    assert usage["total_tokens"] == 150
    assert usage["provider_attempts"] == 1


def test_response_artifact_failure_cannot_erase_accounting(tmp_path, monkeypatch):
    import perla_extract.study_extraction.client as implementation

    original = implementation.write_json_atomic

    def write(path, payload):
        if path.name.endswith(".response.json"):
            raise OSError("synthetic response write failure")
        return original(path, payload)

    monkeypatch.setattr(implementation, "write_json_atomic", write)
    monkeypatch.setattr(
        litellm, "completion", lambda **kwargs: response({"value": 7}, cost=0.25)
    )
    model_client = client(tmp_path)
    with pytest.raises(OSError, match="response write"):
        complete(model_client)
    assert len(model_client.calls) == 1
    assert model_client.calls[0]["status"] == "failed"
    usage = summarize_calls(model_client.calls)
    assert usage["cost"] == 0.25
    assert usage["total_tokens"] == 150
    assert usage["provider_attempts"] == 1
    assert read_events(model_client.journal.path)[-1]["event"] == "call.finished"


def test_retry_planning_failure_preserves_first_paid_attempt(tmp_path, monkeypatch):
    import perla_extract.study_extraction.request_policy as implementation

    original = implementation.estimate_request_input
    estimates = 0

    def estimate(*args, **kwargs):
        nonlocal estimates
        estimates += 1
        if estimates == 2:
            raise ValueError("synthetic tokenizer failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(implementation, "estimate_request_input", estimate)
    monkeypatch.setattr(
        litellm,
        "completion",
        lambda **kwargs: response({"value": "invalid"}, cost=0.25),
    )
    model_client = client(tmp_path)
    with pytest.raises(ValueError, match="tokenizer failure"):
        complete(model_client)
    assert len(model_client.calls) == 1
    assert model_client.calls[0]["status"] == "failed"
    usage = summarize_calls(model_client.calls)
    assert usage["cost"] == 0.25
    assert usage["total_tokens"] == 150
    assert usage["provider_attempts"] == 1
