"""Provider-neutral model calls with reproducible local artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar, cast

import litellm
from pydantic import BaseModel, ValidationError

from .artifacts import write_json_atomic
from .deployment import (
    DeploymentSettings,
    deployment_stage,
    estimate_request_input,
    request_limit_issues,
    strict_response_schema,
)
from .logging import logger
from .observability import EventJournal
from .progress import heartbeat

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
CallFunction = TypeVar("CallFunction", bound=Callable[..., Any])
MessageContent = str | list[dict[str, object]]
VALIDATION_REPAIR_VERSION = 1
RETRYABLE_ERRORS = (
    litellm.RateLimitError,
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.InternalServerError,
    litellm.ServiceUnavailableError,
)
MODEL_ERRORS = tuple(litellm.exceptions.LITELLM_EXCEPTION_TYPES) + (
    litellm.exceptions.LiteLLMUnknownProvider,
)


class ModelCallError(RuntimeError):
    """Report a failed call after preserving its inspectable response."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        usage: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.usage = usage


class ModelBudgetExceeded(ModelCallError):
    """Stop before a provider request would exceed a configured known budget."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


def _strict_schema(
    model: type[BaseModel], schema: dict[str, object] | None = None
) -> dict:
    """Compatibility name for the shared strict response-schema builder."""
    return strict_response_schema(model, schema)


def _canonical(value: object) -> bytes:
    """Serialize complete requests deterministically for content-addressed caching."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _request_artifact(body: dict) -> dict:
    """Keep image request provenance without duplicating base64 pixels on disk."""

    artifact = deepcopy(body)

    def redact(value: object) -> None:
        if isinstance(value, dict):
            image_url = value.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url")
                if isinstance(url, str) and url.startswith("data:image/"):
                    image_url["url"] = (
                        "embedded-image:sha256="
                        f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}"
                        f";encoded_characters={len(url)}"
                    )
            for child in value.values():
                redact(child)
        elif isinstance(value, list):
            for child in value:
                redact(child)

    redact(artifact)
    return artifact


def _validation_repair_request(
    body: dict,
    result: object,
    errors: list[dict[str, Any]],
) -> dict:
    """Ask the model to correct its own invalid structured response once.

    Strict provider schemas catch structural mistakes, while Pydantic also enforces
    scientific relationships that JSON Schema cannot express. Replaying the same
    request does not explain those failures. This follow-up retains the original
    evidence, shows the rejected response and only the machine-readable validation
    errors, and still requires the original response schema.
    """

    repaired = deepcopy(body)
    messages = list(repaired["messages"])
    messages.extend(
        [
            {
                "role": "assistant",
                "content": json.dumps(
                    result, ensure_ascii=False, separators=(",", ":")
                ),
            },
            {
                "role": "user",
                "content": (
                    "The preceding JSON failed local Pydantic validation. Correct only "
                    "the reported validation problems, preserve supported atomic values "
                    "and evidence, and return the complete corrected JSON object with no "
                    "explanation. Validation errors:\n"
                    + json.dumps(errors, ensure_ascii=False, separators=(",", ":"))
                ),
            },
        ]
    )
    repaired["messages"] = messages
    return repaired


def _aggregate_usage(records: list[dict]) -> dict:
    """Sum known attempt usage without replacing unavailable totals with certainty."""
    if not records:
        return {}
    result: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        result[key] = sum(int(record.get(key) or 0) for record in records)
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        details = [record.get(key) for record in records]
        names = {name for item in details if isinstance(item, dict) for name in item}
        result[key] = {
            name: sum(
                item[name]
                for item in details
                if isinstance(item, dict) and isinstance(item.get(name), (int, float))
            )
            if any(
                isinstance(item, dict) and isinstance(item.get(name), (int, float))
                for item in details
            )
            else None
            for name in names
        }
        result[key + "_complete"] = {
            name: all(
                isinstance(item, dict) and isinstance(item.get(name), (int, float))
                for item in details
            )
            for name in names
        }
    result["cost"] = round(sum(float(record.get("cost") or 0) for record in records), 8)
    result["cost_tracking_complete"] = all(
        record.get("cost") is not None for record in records
    )
    result["total_cost_usd"] = (
        result["cost"] if result["cost_tracking_complete"] else None
    )
    result["token_tracking_complete"] = all(
        all(
            record.get(k) is not None
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        for record in records
    )
    result["latency_seconds"] = round(
        sum(float(record.get("latency_seconds") or 0) for record in records), 6
    )
    result["provider_responses"] = sum(
        bool(record.get("response_received", True)) for record in records
    )
    for name in (
        "response_model",
        "provider",
        "finish_reason",
        "response_id",
        "cost_source",
    ):
        values = list(
            dict.fromkeys(
                record[name] for record in records if record.get(name) is not None
            )
        )
        result[name + "s"] = values
        result[name] = values[0] if len(values) == 1 else None
    return result


def summarize_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize this execution; cached origin usage never becomes a new charge."""
    attempts = [attempt for call in calls for attempt in call.get("attempts", [])]
    usage = _aggregate_usage([attempt["usage"] for attempt in attempts])
    # Old callers/fixtures can still supply one aggregate per logical call.
    if not attempts:
        usage = _aggregate_usage(
            [
                call["usage"]
                for call in calls
                if isinstance(call.get("usage"), dict) and call["usage"]
            ]
        )
    return {
        "logical_calls": len(calls),
        "live_calls": sum(
            not call.get("cache_hit") and int(call.get("attempt_count", 1)) > 0
            for call in calls
        ),
        "cache_hits": sum(bool(call.get("cache_hit")) for call in calls),
        "blocked_calls": sum(call.get("status") == "blocked" for call in calls),
        "provider_attempts": sum(int(call.get("attempt_count", 0)) for call in calls),
        "responses_received": usage.get("provider_responses", 0),
        **{
            key: usage.get(key, 0)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
        },
        "total_cost_usd": usage.get("total_cost_usd", 0.0),
        "cost_tracking_complete": usage.get("cost_tracking_complete", True),
        "token_tracking_complete": usage.get("token_tracking_complete", True),
        **{
            key: value
            for key, value in usage.items()
            if "_details" in key or key == "cost_sources"
        },
    }


def _preserve_call_accounting(function: CallFunction) -> CallFunction:
    """Finalize registered work even when planning or artifact I/O raises.

    Attempts enter the in-memory run record before any post-response disk writes.
    This boundary covers exceptions between attempts too, such as a tokenizer failing
    while sizing a validation repair. A cache or disk failure cannot erase paid work.
    """

    @wraps(function)
    def wrapped(self: ModelClient, *args: Any, **kwargs: Any) -> Any:
        previous = len(self.calls)
        try:
            return function(self, *args, **kwargs)
        except BaseException as error:
            for record in self.calls[previous:]:
                if "_started" not in record:
                    continue
                record.update(
                    status="interrupted"
                    if isinstance(error, (KeyboardInterrupt, SystemExit))
                    else "failed",
                    error=f"{type(error).__name__}: logical call did not complete; inspect preserved artifacts",
                    usage=_aggregate_usage(
                        [attempt["usage"] for attempt in record["attempts"]]
                    ),
                    attempt_count=len(record["attempts"]),
                )
                try:
                    self._record_call(record)
                except Exception:
                    # The original error may itself be a full or unavailable disk.
                    # Accounting is already in memory for the workflow's failure report.
                    pass
            raise

    return cast(CallFunction, wrapped)


class ModelClient:
    """Execute validated calls with request limits and durable per-attempt accounting.

    One application retry is allowed. Local cache replay creates no provider attempt.
    Credentials are resolved at the transport boundary and never enter artifacts or
    cache identity. Optional tracing observes the same events as the local journal.
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        output_dir: Path,
        heartbeat_seconds: float = 20,
        timeout_seconds: float = 600,
        temperature: float | None = 0,
        max_model_calls: int | None = None,
        max_cost_usd: float | None = None,
        max_total_tokens: int | None = None,
        max_elapsed_seconds: float | None = None,
        deployments: dict[str, DeploymentSettings] | None = None,
        journal: EventJournal | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.output_dir = Path(output_dir)
        self.heartbeat_seconds = heartbeat_seconds
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_model_calls = max_model_calls
        self.max_cost_usd = max_cost_usd
        self.max_total_tokens = max_total_tokens
        self.max_elapsed_seconds = max_elapsed_seconds
        self.deployments = deployments or {}
        self.journal = journal or EventJournal(self.output_dir)
        self.calls: list[dict[str, Any]] = []
        self._provider_requests = 0
        self._known_cost_usd = 0.0
        self._known_tokens = 0
        self._cost_tracking_complete = True
        self._token_tracking_complete = True
        self._started = getattr(self.journal, "started_at", time.monotonic())
        self._active_deployment = DeploymentSettings()
        for name in (
            "max_model_calls",
            "max_cost_usd",
            "max_total_tokens",
            "max_elapsed_seconds",
        ):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")

    def _reserve_provider_request(
        self, estimated_input_tokens: int = 0, output_tokens: int = 0
    ) -> None:
        if (
            self.max_model_calls is not None
            and self._provider_requests >= self.max_model_calls
        ):
            raise ModelBudgetExceeded(
                f"model-call budget exhausted ({self.max_model_calls} provider requests)"
            )
        if (
            self.max_elapsed_seconds is not None
            and time.monotonic() - self._started >= self.max_elapsed_seconds
        ):
            raise ModelBudgetExceeded("elapsed-time budget exhausted")
        if self.max_cost_usd is not None and self._provider_requests:
            if not self._cost_tracking_complete:
                raise ModelBudgetExceeded(
                    "cost budget cannot be enforced because cost is unavailable"
                )
            if self._known_cost_usd >= self.max_cost_usd:
                raise ModelBudgetExceeded(
                    f"reported cost budget exhausted (${self.max_cost_usd:.4f})"
                )
        if self.max_total_tokens is not None:
            if not self._token_tracking_complete:
                raise ModelBudgetExceeded(
                    "token budget cannot be enforced because token usage is unavailable"
                )
            if (
                self._known_tokens + estimated_input_tokens + output_tokens
                > self.max_total_tokens
            ):
                raise ModelBudgetExceeded(
                    "token budget exhausted by observed usage plus estimated input and reserved output"
                )
        self._provider_requests += 1

    def _record_provider_cost(self, usage: dict) -> None:
        if usage.get("cost") is None:
            self._cost_tracking_complete = False
        else:
            self._known_cost_usd += float(usage["cost"])
        if usage.get("total_tokens") is None:
            self._token_tracking_complete = False
        else:
            self._known_tokens += int(usage["total_tokens"])

    def budget_status(self) -> dict[str, object]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_cost_usd": self.max_cost_usd,
            "max_total_tokens": self.max_total_tokens,
            "max_elapsed_seconds": self.max_elapsed_seconds,
            "provider_requests": self._provider_requests,
            "reported_cost_usd": round(self._known_cost_usd, 8),
            "total_cost_usd": round(self._known_cost_usd, 8)
            if self._cost_tracking_complete
            else None,
            "cost_tracking_complete": self._cost_tracking_complete,
            "observed_total_tokens": self._known_tokens,
            "token_tracking_complete": self._token_tracking_complete,
            "cost_limit_policy": "stop_after_observed_limit; not a prepaid ceiling",
            "token_limit_policy": "observed usage plus estimated input and output reservation",
            "elapsed_seconds": round(time.monotonic() - self._started, 6),
            "observed_token_limit_exceeded": self.max_total_tokens is not None
            and self._known_tokens > self.max_total_tokens,
            "elapsed_limit_exceeded": self.max_elapsed_seconds is not None
            and time.monotonic() - self._started > self.max_elapsed_seconds,
        }

    def _record_call(self, record: dict[str, Any]) -> None:
        record["elapsed_seconds"] = round(time.monotonic() - record.pop("_started"), 6)
        # Attempt details already have canonical attempt.finished events.
        summary = {key: value for key, value in record.items() if key != "attempts"}
        summary["attempt_ids"] = [
            attempt["attempt_id"] for attempt in record["attempts"]
        ]
        self.journal.emit("call.finished", **summary)

    def _request(
        self,
        *,
        model: str,
        system: str,
        prompt: MessageContent,
        schema: dict,
        max_output_tokens: int,
        reasoning_effort: str | None,
    ) -> dict:
        body: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "study_extraction",
                    "strict": True,
                    "schema": schema,
                },
            },
            "seed": 0,
            "max_tokens": max_output_tokens,
            "stream": False,
            "timeout": self.timeout_seconds,
            "num_retries": 0,
            "drop_params": False,
        }
        if reasoning_effort not in {None, "omit"}:
            body["reasoning_effort"] = reasoning_effort
        if self.temperature is not None:
            body["temperature"] = self.temperature
        return body

    def _live(self, body: dict, failure_path: Path) -> tuple[dict, dict]:
        """Capture provider metadata before decoding; preserve every terminal error."""
        transport = dict(body)
        if self._active_deployment.api_key_env:
            value = os.environ.get(self._active_deployment.api_key_env)
            if not value:
                raise ModelCallError(
                    f"Missing credential environment variable {self._active_deployment.api_key_env}",
                    retryable=False,
                )
            transport["api_key"] = value
        started = time.monotonic()
        try:
            with heartbeat(
                failure_path.stem.removesuffix(".failure"), self.heartbeat_seconds
            ):
                response = litellm.completion(**transport)
        except MODEL_ERRORS as exc:
            error_message = str(exc)
            secret = transport.get("api_key")
            if secret:
                error_message = error_message.replace(str(secret), "[redacted]")
            details = {
                "error_type": type(exc).__name__,
                "error": error_message,
                "status_code": getattr(exc, "status_code", None),
                "model": body["model"],
                "latency_seconds": round(time.monotonic() - started, 6),
            }
            write_json_atomic(failure_path, details)
            raise ModelCallError(
                f"Model provider request failed: {error_message}; see {failure_path}",
                retryable=isinstance(exc, RETRYABLE_ERRORS),
                usage={
                    "response_received": False,
                    "latency_seconds": details["latency_seconds"],
                    "cost": None,
                    "cost_source": "unknown",
                    "error_type": type(exc).__name__,
                    "status_code": details["status_code"],
                },
            ) from exc
        payload = response.model_dump()
        choices = payload.get("choices")
        choice = (
            choices[0]
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else {}
        )
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        usage = dict(payload["usage"]) if isinstance(payload.get("usage"), dict) else {}
        hidden = getattr(response, "_hidden_params", {})
        cost = hidden.get("response_cost")
        usage.update(
            {
                "response_received": True,
                "response_id": payload.get("id"),
                "response_model": payload.get("model"),
                "provider": hidden.get("custom_llm_provider"),
                "cost": cost,
                "cost_source": "adapter_reported_unspecified"
                if cost is not None
                else "unknown",
                "finish_reason": choice.get("finish_reason"),
                "latency_seconds": round(time.monotonic() - started, 6),
            }
        )
        try:
            if not isinstance(content, (str, bytes, bytearray)):
                raise TypeError("response content is not JSON text")
            result = json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            write_json_atomic(
                failure_path,
                {
                    "finish_reason": choice.get("finish_reason"),
                    "usage": usage,
                    "content": content,
                },
            )
            raise ModelCallError(
                f"Model returned invalid JSON; see {failure_path}",
                retryable=choice.get("finish_reason") != "length",
                usage=usage,
            ) from exc
        return result, usage

    @_preserve_call_accounting
    def complete(
        self,
        *,
        kind: str,
        slug: str,
        model: str,
        system: str,
        prompt: MessageContent,
        response_model: type[ResponseModel],
        max_output_tokens: int,
        reasoning_effort: str | None,
        request_schema: dict[str, object] | None = None,
        decode: Callable[[object], object] | None = None,
        validate: Callable[[ResponseModel], None] | None = None,
        validation_contract: str | None = None,
        prompt_components: dict[str, object] | None = None,
    ) -> ResponseModel:
        call_started = time.monotonic()
        if (validate is None) != (validation_contract is None):
            raise ValueError(
                "validate and validation_contract must be provided together"
            )
        if Path(slug).name != slug:
            raise ValueError("call slug must be a filename component")
        deployment = self.deployments.get(
            kind, self.deployments.get(deployment_stage(kind), DeploymentSettings())
        )
        if isinstance(deployment, dict):
            deployment = DeploymentSettings(**deployment)
        self._active_deployment = deployment
        schema = _strict_schema(response_model, request_schema)
        body = self._request(
            model=deployment.model or model,
            system=system,
            prompt=prompt,
            schema=schema,
            max_output_tokens=deployment.max_output_tokens or max_output_tokens,
            reasoning_effort=deployment.reasoning_effort
            if deployment.reasoning_effort is not None
            else reasoning_effort,
        )
        if deployment.base_url:
            body["api_base"] = deployment.base_url
        if deployment.temperature is not None:
            body["temperature"] = deployment.temperature
        if deployment.top_p is not None:
            body["top_p"] = deployment.top_p
        extra: dict[str, object] = {}
        if deployment.top_k is not None:
            extra["top_k"] = deployment.top_k
        if deployment.chat_template_kwargs:
            extra["chat_template_kwargs"] = deployment.chat_template_kwargs
        if extra:
            body["extra_body"] = extra
        identity = deployment.identity()
        request_hash = hashlib.sha256(
            _canonical({"request": body, "deployment": identity})
        ).hexdigest()
        cache_key = hashlib.sha256(
            _canonical(
                {
                    "request_sha256": request_hash,
                    "validation_repair_version": VALIDATION_REPAIR_VERSION,
                    "validation_contract": validation_contract,
                }
            )
        ).hexdigest()
        request_path = self.output_dir / "requests" / f"{slug}.request.json"
        cache_path = self.cache_dir / f"{cache_key}.json"
        write_json_atomic(request_path, _request_artifact(body))
        call_id = uuid.uuid4().hex
        record: dict[str, Any] = {
            "_started": call_started,
            "kind": kind,
            "slug": slug,
            "call_id": call_id,
            "run_id": self.journal.run_id,
            "stage_id": self.journal.stage_id,
            "request_sha256": request_hash,
            "cache_key_sha256": cache_key,
            "model": body["model"],
            "reasoning_effort": body.get("reasoning_effort"),
            "temperature": body.get("temperature"),
            "deployment": identity,
            "deployment_verification": "operator_declared_unverified",
            "attempts": [],
            "attempt_count": 0,
            "cache_hit": False,
            "status": "running",
        }
        self.calls.append(record)
        log = logger.bind(
            run_id=self.journal.run_id,
            call_id=call_id,
            stage_id=self.journal.stage_id,
            kind=kind,
        )
        self.journal.emit(
            "call.started",
            call_id=call_id,
            kind=kind,
            model=body["model"],
            request_sha256=request_hash,
            cache_key_sha256=cache_key,
        )
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                validated = response_model.model_validate(cached["result"])
                if validate:
                    validate(validated)
            except (
                OSError,
                TypeError,
                json.JSONDecodeError,
                KeyError,
                ValidationError,
                ValueError,
            ):
                log.warning("Ignoring invalid model cache entry {}", cache_path.name)
            else:
                record.update(
                    cache_hit=True,
                    status="local_cache_hit",
                    attempt_count=0,
                    cached_response_usage=cached.get("usage", {}),
                    cache_origin=cached.get("origin"),
                )
                self._record_call(record)
                log.info("{}: local response cache hit", kind)
                return validated
        current_body = body
        last_error: ModelCallError | None = None
        validation_repair = False
        for attempt_index in range(1, 3):
            attempt_id = f"{call_id}.{attempt_index}"
            attempt_body = deepcopy(current_body)
            components = dict(prompt_components or {})
            if len(attempt_body["messages"]) > 2:
                components["validation_repair_messages"] = attempt_body["messages"][2:]
            estimate = estimate_request_input(
                system,
                prompt,
                schema,
                messages=attempt_body["messages"],
                tokenizer=deployment.tokenizer,
                components=components,
            )
            try:
                input_tokens = int(estimate["estimated_input_tokens"])
                if estimate["contains_images"] and (
                    deployment.max_input_tokens
                    or deployment.context_tokens
                    or self.max_total_tokens
                ):
                    raise ModelBudgetExceeded(
                        "Configured token limits require a provider-specific image token estimator; text estimates cannot bound images"
                    )
                limit_issues = request_limit_issues(
                    input_tokens, int(attempt_body["max_tokens"]), deployment
                )
                if limit_issues:
                    raise ModelBudgetExceeded("; ".join(limit_issues))
                if deployment.api_key_env and not os.environ.get(
                    deployment.api_key_env
                ):
                    raise ModelBudgetExceeded(
                        f"Missing credential environment variable {deployment.api_key_env}"
                    )
                if self.max_elapsed_seconds is not None:
                    remaining = self.max_elapsed_seconds - (
                        time.monotonic() - self._started
                    )
                    attempt_body["timeout"] = min(
                        float(attempt_body["timeout"]), max(0.001, remaining)
                    )
                self._reserve_provider_request(
                    input_tokens, int(attempt_body["max_tokens"])
                )
            except ModelBudgetExceeded as exc:
                last_error = exc
                record["status"] = "blocked"
                record["input_estimate"] = estimate
                break
            attempt_path = (
                self.output_dir
                / "requests"
                / f"{slug}.attempt-{attempt_index}.request.json"
            )
            failure_path = (
                self.output_dir
                / "requests"
                / f"{slug}.attempt-{attempt_index}.failure.json"
            )
            response_path = (
                self.output_dir
                / "requests"
                / f"{slug}.attempt-{attempt_index}.response.json"
            )
            write_json_atomic(attempt_path, _request_artifact(attempt_body))
            attempt_hash = hashlib.sha256(
                _canonical({"request": attempt_body, "deployment": identity})
            ).hexdigest()
            self.journal.emit(
                "attempt.started",
                call_id=call_id,
                attempt_id=attempt_id,
                attempt_index=attempt_index,
                kind=kind,
                model=body["model"],
                request_sha256=attempt_hash,
                artifact=str(attempt_path.relative_to(self.output_dir)),
                input_estimate=estimate,
                max_output_tokens=attempt_body["max_tokens"],
                retry_reason="validation_repair"
                if validation_repair
                else "provider_retry"
                if attempt_index > 1
                else None,
            )
            log.info(
                "{}: attempt {}/2, model={}, input≈{}, output cap={}",
                kind,
                attempt_index,
                body["model"],
                input_tokens,
                attempt_body["max_tokens"],
            )
            started = time.monotonic()
            raw_result: object = None
            usage: dict = {}
            outcome = "failed"
            validation_errors = None
            try:
                raw_result, usage = self._live(attempt_body, failure_path)
                usage.setdefault("response_received", True)
                decoded_result = decode(raw_result) if decode else raw_result
                validated = response_model.model_validate(decoded_result)
                if validate:
                    validate(validated)
                outcome = "success"
            except (TypeError, ValueError) as exc:
                outcome = "validation_rejected"
                validation_errors = (
                    exc.errors(include_url=False, include_context=False)
                    if isinstance(exc, ValidationError)
                    else [{"type": type(exc).__name__, "message": str(exc)}]
                )
                write_json_atomic(
                    failure_path,
                    {
                        "validation_errors": validation_errors,
                        "result": raw_result,
                        "usage": usage,
                    },
                )
                last_error = ModelCallError(
                    f"Model output failed Pydantic validation; see {failure_path}"
                )
                if attempt_index == 1:
                    current_body = _validation_repair_request(
                        body, raw_result, validation_errors
                    )
                    write_json_atomic(
                        self.output_dir
                        / "requests"
                        / f"{slug}.validation-repair.request.json",
                        _request_artifact(current_body),
                    )
                    validation_repair = True
            except ModelCallError as exc:
                last_error = exc
                usage = dict(exc.usage or {})
                usage.setdefault("response_received", False)
                outcome = (
                    "invalid_response"
                    if usage["response_received"]
                    else "provider_error"
                )
            except BaseException as exc:
                outcome = (
                    "interrupted"
                    if isinstance(exc, (KeyboardInterrupt, SystemExit))
                    else "unexpected_error"
                )
                usage.setdefault("response_received", False)
                last_error = ModelCallError(
                    f"Interrupted or unexpected model error: {type(exc).__name__}",
                    retryable=False,
                )
                raise
            finally:
                elapsed = round(time.monotonic() - started, 6)
                usage.setdefault("latency_seconds", elapsed)
                usage.setdefault("cost", None)
                usage.setdefault(
                    "cost_source",
                    "adapter_reported_unspecified"
                    if usage["cost"] is not None
                    else "unknown",
                )
                self._record_provider_cost(usage)
                attempt_record = {
                    "attempt_id": attempt_id,
                    "attempt_index": attempt_index,
                    "status": outcome,
                    "request_sha256": attempt_hash,
                    "input_estimate": estimate,
                    "usage": usage,
                    "elapsed_seconds": elapsed,
                    "response_received": bool(usage.get("response_received")),
                    "error": str(last_error)
                    if outcome != "success" and last_error
                    else None,
                    "response_artifact": str(
                        response_path.relative_to(self.output_dir)
                    ),
                }
                record["attempts"].append(attempt_record)
                record["attempt_count"] = len(record["attempts"])
                record["usage"] = _aggregate_usage(
                    [a["usage"] for a in record["attempts"]]
                )
                write_json_atomic(
                    response_path,
                    {
                        "result": raw_result,
                        "usage": usage,
                        "validation_errors": validation_errors,
                        "status": outcome,
                    },
                )
                self.journal.emit(
                    "attempt.finished",
                    call_id=call_id,
                    kind=kind,
                    model=body["model"],
                    **attempt_record,
                )
                if outcome in {"interrupted", "unexpected_error"}:
                    record.update(
                        cache_hit=False,
                        status=outcome,
                        error=str(last_error),
                        usage=_aggregate_usage(
                            [a["usage"] for a in record["attempts"]]
                        ),
                        attempt_count=len(record["attempts"]),
                        validation_repair=validation_repair,
                    )
                    self._record_call(record)
            if outcome == "success":
                combined = _aggregate_usage([a["usage"] for a in record["attempts"]])
                try:
                    write_json_atomic(
                        cache_path,
                        {
                            "request_sha256": request_hash,
                            "cache_key_sha256": cache_key,
                            "result": validated.model_dump(mode="json"),
                            "usage": combined,
                            "origin": {
                                "run_id": self.journal.run_id,
                                "call_id": call_id,
                                "attempt_ids": [
                                    a["attempt_id"] for a in record["attempts"]
                                ],
                            },
                        },
                    )
                except OSError as error:
                    record["cache_write_error"] = type(error).__name__
                    log.warning(
                        "{}: response validated but cache write failed ({})",
                        kind,
                        type(error).__name__,
                    )
                    self.journal.emit(
                        "cache.write_failed",
                        call_id=call_id,
                        error_type=type(error).__name__,
                    )
                record.update(
                    cache_hit=False,
                    status="success",
                    usage=combined,
                    attempt_count=len(record["attempts"]),
                    validation_repair=validation_repair,
                )
                self._record_call(record)
                log.info(
                    "{}: complete, {} attempt(s), {} tokens",
                    kind,
                    len(record["attempts"]),
                    combined["total_tokens"],
                )
                return validated
            if attempt_index == 1 and last_error and last_error.retryable:
                log.warning("{}: retrying after {}", kind, outcome)
                if not validation_repair:
                    pause = 2.0
                    if self.max_elapsed_seconds is not None:
                        pause = min(
                            pause,
                            max(
                                0,
                                self.max_elapsed_seconds
                                - (time.monotonic() - self._started),
                            ),
                        )
                    time.sleep(pause)
            else:
                break
        record.update(
            cache_hit=False,
            error=str(last_error),
            usage=_aggregate_usage([a["usage"] for a in record["attempts"]]),
            attempt_count=len(record["attempts"]),
            validation_repair=validation_repair,
        )
        if record["status"] == "running":
            record["status"] = "failed"
        self._record_call(record)
        log.error("{}: {}", kind, last_error)
        raise last_error or ModelCallError("Unknown model-call failure")
