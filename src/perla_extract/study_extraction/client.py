"""Provider-neutral model calls with reproducible local artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from .accounting import (
    AttemptRecord,
    CallAccounting,
    CallRecord,
)
from .accounting import (
    summarize_calls as summarize_calls,
)
from .artifacts import write_json_atomic
from .deployment import (
    DeploymentSettings,
    RequestEstimate,
    strict_response_schema,
)
from .logging import logger
from .observability import EventJournal
from .progress import heartbeat
from .request_policy import ResolvedStagePolicy

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
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


class _ResponseArtifactError(OSError):
    """Carry already captured provider usage across a failed diagnostic write."""

    def __init__(self, error: OSError, usage: dict) -> None:
        super().__init__(*error.args)
        self.usage = usage


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


@dataclass(frozen=True)
class _PreparedCall:
    policy: ResolvedStagePolicy
    body: dict[str, Any]
    schema: dict
    record: CallRecord
    cache_path: Path


@dataclass(frozen=True)
class _PreparedAttempt:
    body: dict[str, Any]
    attempt_id: str
    index: int
    request_sha256: str
    estimate: RequestEstimate
    failure_path: Path
    response_path: Path


@dataclass
class _AttemptOutcome(Generic[ResponseModel]):
    status: str = "failed"
    value: ResponseModel | None = None
    raw_result: object = None
    validation_errors: list[dict[str, Any]] | None = None
    error: ModelCallError | None = None


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
        stage_policies: dict[str, ResolvedStagePolicy] | None = None,
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
        self.stage_policies = stage_policies or {}
        self.journal = journal or EventJournal(self.output_dir)
        self.accounting = CallAccounting()
        self._started = getattr(self.journal, "started_at", time.monotonic())
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
            and self.accounting.provider_requests >= self.max_model_calls
        ):
            raise ModelBudgetExceeded(
                f"model-call budget exhausted ({self.max_model_calls} provider requests)"
            )
        if (
            self.max_elapsed_seconds is not None
            and time.monotonic() - self._started >= self.max_elapsed_seconds
        ):
            raise ModelBudgetExceeded("elapsed-time budget exhausted")
        if self.max_cost_usd is not None and self.accounting.provider_requests:
            if not self.accounting.cost_tracking_complete:
                raise ModelBudgetExceeded(
                    "cost budget cannot be enforced because cost is unavailable"
                )
            if self.accounting.known_cost_usd >= self.max_cost_usd:
                raise ModelBudgetExceeded(
                    f"reported cost budget exhausted (${self.max_cost_usd:.4f})"
                )
        if self.max_total_tokens is not None:
            if not self.accounting.token_tracking_complete:
                raise ModelBudgetExceeded(
                    "token budget cannot be enforced because token usage is unavailable"
                )
            if (
                self.accounting.known_tokens + estimated_input_tokens + output_tokens
                > self.max_total_tokens
            ):
                raise ModelBudgetExceeded(
                    "token budget exhausted by observed usage plus estimated input and reserved output"
                )
        self.accounting.reserve_request()

    def budget_status(self) -> dict[str, object]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_cost_usd": self.max_cost_usd,
            "max_total_tokens": self.max_total_tokens,
            "max_elapsed_seconds": self.max_elapsed_seconds,
            "provider_requests": self.accounting.provider_requests,
            "reported_cost_usd": round(self.accounting.known_cost_usd, 8),
            "total_cost_usd": round(self.accounting.known_cost_usd, 8)
            if self.accounting.cost_tracking_complete
            else None,
            "cost_tracking_complete": self.accounting.cost_tracking_complete,
            "observed_total_tokens": self.accounting.known_tokens,
            "token_tracking_complete": self.accounting.token_tracking_complete,
            "cost_limit_policy": "stop_after_observed_limit; not a prepaid ceiling",
            "token_limit_policy": "observed usage plus estimated input and output reservation",
            "elapsed_seconds": round(time.monotonic() - self._started, 6),
            "observed_token_limit_exceeded": self.max_total_tokens is not None
            and self.accounting.known_tokens > self.max_total_tokens,
            "elapsed_limit_exceeded": self.max_elapsed_seconds is not None
            and time.monotonic() - self._started > self.max_elapsed_seconds,
        }

    @property
    def calls(self) -> list[dict[str, Any]]:
        """Return the artifact-compatible view of this run's typed call records."""
        return self.accounting.calls

    def _record_call(self, record: CallRecord) -> None:
        record.finish()
        summary = record.to_dict()
        summary.pop("attempts")
        summary["attempt_ids"] = [attempt.attempt_id for attempt in record.attempts]
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
            try:
                write_json_atomic(
                    failure_path,
                    {
                        "finish_reason": choice.get("finish_reason"),
                        "usage": usage,
                        "content": content,
                    },
                )
            except OSError as error:
                raise _ResponseArtifactError(error, usage) from error
            raise ModelCallError(
                f"Model returned invalid JSON; see {failure_path}",
                retryable=choice.get("finish_reason") != "length",
                usage=usage,
            ) from exc
        return result, usage

    def _policy(
        self,
        stage: str,
        *,
        model: str | None,
        max_output_tokens: int | None,
        reasoning_effort: str | None,
    ) -> ResolvedStagePolicy:
        if stage in self.stage_policies:
            return self.stage_policies[stage]
        if model is None or max_output_tokens is None:
            raise ValueError(
                f"stage {stage!r} needs a configured policy or explicit model and max_output_tokens"
            )
        deployment = self.deployments.get(stage, DeploymentSettings())
        if isinstance(deployment, dict):
            deployment = DeploymentSettings(**deployment)
        return ResolvedStagePolicy.resolve(
            stage,
            model=model,
            deployment=deployment,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            temperature=self.temperature,
        )

    def _prepare_call(
        self,
        *,
        policy: ResolvedStagePolicy,
        kind: str,
        slug: str,
        system: str,
        prompt: MessageContent,
        response_model: type[BaseModel],
        request_schema: dict[str, object] | None,
        validation_contract: str | None,
    ) -> _PreparedCall:
        started = time.monotonic()
        schema = _strict_schema(response_model, request_schema)
        body = self._request(
            model=policy.model,
            system=system,
            prompt=prompt,
            schema=schema,
            max_output_tokens=policy.max_output_tokens,
            reasoning_effort=policy.reasoning_effort,
        )
        deployment = policy.deployment
        if deployment.base_url:
            body["api_base"] = deployment.base_url
        body.pop("temperature", None)
        if policy.temperature is not None:
            body["temperature"] = policy.temperature
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
        write_json_atomic(
            self.output_dir / "requests" / f"{slug}.request.json",
            _request_artifact(body),
        )
        record = CallRecord(
            kind=kind,
            slug=slug,
            stage=policy.stage,
            call_id=uuid.uuid4().hex,
            run_id=self.journal.run_id,
            stage_id=self.journal.stage_id,
            request_sha256=request_hash,
            cache_key_sha256=cache_key,
            model=body["model"],
            reasoning_effort=body.get("reasoning_effort"),
            temperature=body.get("temperature"),
            deployment=identity,
            started_at=started,
        )
        return _PreparedCall(
            policy, body, schema, record, self.cache_dir / f"{cache_key}.json"
        )

    def _replay_cache(
        self,
        call: _PreparedCall,
        response_model: type[ResponseModel],
        validate: Callable[[ResponseModel], None] | None,
    ) -> ResponseModel | None:
        if not call.cache_path.exists():
            return None
        try:
            cached = json.loads(call.cache_path.read_text(encoding="utf-8"))
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
            logger.warning(
                "Ignoring invalid model cache entry {}", call.cache_path.name
            )
            return None
        call.record.cache_hit = True
        call.record.status = "local_cache_hit"
        call.record.cached_response_usage = cached.get("usage", {})
        call.record.cache_origin = cached.get("origin")
        self._record_call(call.record)
        logger.info("{}: local response cache hit", call.record.kind)
        return validated

    def _reserve_attempt(
        self, policy: ResolvedStagePolicy, body: dict, estimate: RequestEstimate
    ) -> None:
        deployment = policy.deployment
        if estimate["contains_images"] and (
            deployment.max_input_tokens
            or deployment.context_tokens
            or self.max_total_tokens
        ):
            raise ModelBudgetExceeded(
                "Configured token limits require a provider-specific image token estimator; text estimates cannot bound images"
            )
        issues = policy.limit_issues(
            estimate["estimated_input_tokens"], int(body["max_tokens"])
        )
        if issues:
            raise ModelBudgetExceeded("; ".join(issues))
        if deployment.api_key_env and not os.environ.get(deployment.api_key_env):
            raise ModelBudgetExceeded(
                f"Missing credential environment variable {deployment.api_key_env}"
            )
        if self.max_elapsed_seconds is not None:
            remaining = self.max_elapsed_seconds - (time.monotonic() - self._started)
            body["timeout"] = min(float(body["timeout"]), max(0.001, remaining))
        self._reserve_provider_request(
            estimate["estimated_input_tokens"], int(body["max_tokens"])
        )

    def _prepare_attempt(
        self,
        call: _PreparedCall,
        current_body: dict,
        index: int,
        *,
        system: str,
        prompt: MessageContent,
        prompt_components: dict[str, object] | None,
    ) -> _PreparedAttempt:
        body = deepcopy(current_body)
        components = dict(prompt_components or {})
        if len(body["messages"]) > 2:
            components["validation_repair_messages"] = body["messages"][2:]
        estimate = call.policy.estimate(
            system,
            prompt,
            call.schema,
            messages=body["messages"],
            components=components,
        )
        try:
            self._reserve_attempt(call.policy, body, estimate)
        except ModelBudgetExceeded:
            call.record.status = "blocked"
            call.record.input_estimate = estimate
            raise
        stem = f"{call.record.slug}.attempt-{index}"
        request_path = self.output_dir / "requests" / f"{stem}.request.json"
        write_json_atomic(request_path, _request_artifact(body))
        attempt = _PreparedAttempt(
            body=body,
            attempt_id=f"{call.record.call_id}.{index}",
            index=index,
            request_sha256=hashlib.sha256(
                _canonical({"request": body, "deployment": call.record.deployment})
            ).hexdigest(),
            estimate=estimate,
            failure_path=self.output_dir / "requests" / f"{stem}.failure.json",
            response_path=self.output_dir / "requests" / f"{stem}.response.json",
        )
        self.journal.emit(
            "attempt.started",
            call_id=call.record.call_id,
            attempt_id=attempt.attempt_id,
            attempt_index=index,
            kind=call.record.kind,
            model=call.record.model,
            request_sha256=attempt.request_sha256,
            artifact=str(request_path.relative_to(self.output_dir)),
            input_estimate=estimate,
            max_output_tokens=body["max_tokens"],
            retry_reason="validation_repair"
            if call.record.validation_repair
            else "provider_retry"
            if index > 1
            else None,
        )
        logger.info(
            "{}: attempt {}/2, model={}, input≈{}, output cap={}",
            call.record.kind,
            index,
            call.record.model,
            estimate["estimated_input_tokens"],
            body["max_tokens"],
        )
        return attempt

    def _transport_body(self, call: _PreparedCall, body: dict) -> dict:
        """Resolve credentials only in the transient copy passed to the transport."""
        transport = dict(body)
        name = call.policy.deployment.api_key_env
        if name:
            secret = os.environ.get(name)
            if not secret:
                raise ModelCallError(
                    f"Missing credential environment variable {name}", retryable=False
                )
            transport["api_key"] = secret
        return transport

    def _retain_attempt(
        self,
        call: _PreparedCall,
        attempt: _PreparedAttempt,
        outcome: _AttemptOutcome,
        usage: dict,
        started: float,
    ) -> None:
        """Retain observed charges before any response-artifact or journal writes."""
        elapsed = round(time.monotonic() - started, 6)
        usage.setdefault("latency_seconds", elapsed)
        usage.setdefault("cost", None)
        usage.setdefault(
            "cost_source",
            "adapter_reported_unspecified" if usage["cost"] is not None else "unknown",
        )
        record = AttemptRecord(
            attempt_id=attempt.attempt_id,
            attempt_index=attempt.index,
            status=outcome.status,
            request_sha256=attempt.request_sha256,
            input_estimate=attempt.estimate,
            usage=usage,
            elapsed_seconds=elapsed,
            response_received=bool(usage.get("response_received")),
            error=str(outcome.error)
            if outcome.status != "success" and outcome.error
            else None,
            response_artifact=str(attempt.response_path.relative_to(self.output_dir)),
        )
        self.accounting.record_attempt(call.record, record)
        write_json_atomic(
            attempt.response_path,
            {
                "result": outcome.raw_result,
                "usage": usage,
                "validation_errors": outcome.validation_errors,
                "status": outcome.status,
            },
        )
        self.journal.emit(
            "attempt.finished",
            call_id=call.record.call_id,
            kind=call.record.kind,
            model=call.record.model,
            **record.to_dict(),
        )
        if outcome.status in {"interrupted", "unexpected_error"}:
            call.record.status = outcome.status
            call.record.error = str(outcome.error)
            self._record_call(call.record)

    def _execute_attempt(
        self,
        call: _PreparedCall,
        attempt: _PreparedAttempt,
        response_model: type[ResponseModel],
        decode: Callable[[object], object] | None,
        validate: Callable[[ResponseModel], None] | None,
    ) -> _AttemptOutcome[ResponseModel]:
        started = time.monotonic()
        usage: dict = {}
        outcome: _AttemptOutcome[ResponseModel] = _AttemptOutcome()
        try:
            outcome.raw_result, usage = self._live(
                self._transport_body(call, attempt.body), attempt.failure_path
            )
            usage.setdefault("response_received", True)
            decoded = decode(outcome.raw_result) if decode else outcome.raw_result
            outcome.value = response_model.model_validate(decoded)
            if validate:
                validate(outcome.value)
            outcome.status = "success"
        except (TypeError, ValueError) as error:
            outcome.status = "validation_rejected"
            outcome.validation_errors = (
                error.errors(include_url=False, include_context=False)
                if isinstance(error, ValidationError)
                else [{"type": type(error).__name__, "message": str(error)}]
            )
            write_json_atomic(
                attempt.failure_path,
                {
                    "validation_errors": outcome.validation_errors,
                    "result": outcome.raw_result,
                    "usage": usage,
                },
            )
            outcome.error = ModelCallError(
                f"Model output failed Pydantic validation; see {attempt.failure_path}"
            )
        except ModelCallError as error:
            outcome.error = error
            usage = dict(error.usage or {})
            usage.setdefault("response_received", False)
            outcome.status = (
                "invalid_response" if usage["response_received"] else "provider_error"
            )
        except BaseException as error:
            if isinstance(error, _ResponseArtifactError):
                usage = error.usage
            outcome.status = (
                "interrupted"
                if isinstance(error, (KeyboardInterrupt, SystemExit))
                else "unexpected_error"
            )
            usage.setdefault("response_received", False)
            outcome.error = ModelCallError(
                f"Interrupted or unexpected model error: {type(error).__name__}",
                retryable=False,
            )
            raise
        finally:
            self._retain_attempt(call, attempt, outcome, usage, started)
        return outcome

    def _cache_success(self, call: _PreparedCall, value: BaseModel) -> None:
        record = call.record
        try:
            write_json_atomic(
                call.cache_path,
                {
                    "request_sha256": record.request_sha256,
                    "cache_key_sha256": record.cache_key_sha256,
                    "result": value.model_dump(mode="json"),
                    "usage": record.usage,
                    "origin": {
                        "run_id": record.run_id,
                        "call_id": record.call_id,
                        "attempt_ids": [
                            attempt.attempt_id for attempt in record.attempts
                        ],
                    },
                },
            )
        except OSError as error:
            record.cache_write_error = type(error).__name__
            logger.warning(
                "{}: response validated but cache write failed ({})",
                record.kind,
                type(error).__name__,
            )
            self.journal.emit(
                "cache.write_failed",
                call_id=record.call_id,
                error_type=type(error).__name__,
            )
        record.status = "success"
        self._record_call(record)
        logger.info(
            "{}: complete, {} attempt(s), {} tokens",
            record.kind,
            len(record.attempts),
            record.usage["total_tokens"],
        )

    def _retry_delay(self) -> None:
        pause = 2.0
        if self.max_elapsed_seconds is not None:
            pause = min(
                pause,
                max(0, self.max_elapsed_seconds - (time.monotonic() - self._started)),
            )
        time.sleep(pause)

    def _complete_live(
        self,
        call: _PreparedCall,
        *,
        system: str,
        prompt: MessageContent,
        response_model: type[ResponseModel],
        decode: Callable[[object], object] | None,
        validate: Callable[[ResponseModel], None] | None,
        prompt_components: dict[str, object] | None,
    ) -> ResponseModel:
        body = call.body
        last_error: ModelCallError | None = None
        for index in range(1, 3):
            try:
                attempt = self._prepare_attempt(
                    call,
                    body,
                    index,
                    system=system,
                    prompt=prompt,
                    prompt_components=prompt_components,
                )
            except ModelBudgetExceeded as error:
                last_error = error
                break
            outcome = self._execute_attempt(
                call, attempt, response_model, decode, validate
            )
            last_error = outcome.error
            if outcome.status == "success":
                assert outcome.value is not None
                self._cache_success(call, outcome.value)
                return outcome.value
            if index == 2 or last_error is None or not last_error.retryable:
                break
            logger.warning("{}: retrying after {}", call.record.kind, outcome.status)
            if outcome.validation_errors is not None:
                body = _validation_repair_request(
                    call.body, outcome.raw_result, outcome.validation_errors
                )
                write_json_atomic(
                    self.output_dir
                    / "requests"
                    / f"{call.record.slug}.validation-repair.request.json",
                    _request_artifact(body),
                )
                call.record.validation_repair = True
            else:
                self._retry_delay()
        call.record.error = str(last_error)
        if call.record.status == "running":
            call.record.status = "failed"
        self._record_call(call.record)
        logger.error("{}: {}", call.record.kind, last_error)
        raise last_error or ModelCallError("Unknown model-call failure")

    def complete(
        self,
        *,
        kind: str,
        slug: str,
        model: str | None = None,
        system: str,
        prompt: MessageContent,
        response_model: type[ResponseModel],
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        stage: str = "assemble",
        request_schema: dict[str, object] | None = None,
        decode: Callable[[object], object] | None = None,
        validate: Callable[[ResponseModel], None] | None = None,
        validation_contract: str | None = None,
        prompt_components: dict[str, object] | None = None,
    ) -> ResponseModel:
        """Resolve one explicit stage, replay a valid cache, or run at most two attempts.

        Configured stage policies are authoritative. Scalar options support direct
        clients without an ExtractionConfig; call-kind labels never select a stage.
        """
        if (validate is None) != (validation_contract is None):
            raise ValueError(
                "validate and validation_contract must be provided together"
            )
        if Path(slug).name != slug:
            raise ValueError("call slug must be a filename component")
        policy = self._policy(
            stage,
            model=model,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
        call = self._prepare_call(
            policy=policy,
            kind=kind,
            slug=slug,
            system=system,
            prompt=prompt,
            response_model=response_model,
            request_schema=request_schema,
            validation_contract=validation_contract,
        )
        with self.accounting.call(call.record, finalize=self._record_call):
            with logger.contextualize(
                run_id=call.record.run_id,
                call_id=call.record.call_id,
                stage_id=call.record.stage_id,
                kind=kind,
            ):
                self.journal.emit(
                    "call.started",
                    call_id=call.record.call_id,
                    kind=kind,
                    stage=stage,
                    model=policy.model,
                    request_sha256=call.record.request_sha256,
                    cache_key_sha256=call.record.cache_key_sha256,
                )
                cached = self._replay_cache(call, response_model, validate)
                if cached is not None:
                    return cached
                return self._complete_live(
                    call,
                    system=system,
                    prompt=prompt,
                    response_model=response_model,
                    decode=decode,
                    validate=validate,
                    prompt_components=prompt_components,
                )
