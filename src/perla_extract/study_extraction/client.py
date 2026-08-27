"""Provider-neutral model calls with reproducible local artifacts."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from .artifacts import write_json_atomic
from .logging import logger
from .progress import heartbeat

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
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

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelBudgetExceeded(ModelCallError):
    """Stop before a provider request would exceed a configured known budget."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


def _strict_schema(
    model: type[BaseModel], schema: dict[str, object] | None = None
) -> dict:
    """Close every object because strict structured-output APIs require it."""

    schema = deepcopy(schema) if schema is not None else model.model_json_schema()

    def close(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if value.get("type") == "object" and isinstance(properties, dict):
                value["required"] = list(properties)
                value["additionalProperties"] = False
            for item in value.values():
                close(item)
        elif isinstance(value, list):
            for item in value:
                close(item)

    close(schema)
    return schema


def _canonical(value: object) -> bytes:
    """Serialize complete requests deterministically for content-addressed caching."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _validation_repair_request(
    body: dict,
    result: object,
    errors: list[dict[str, object]],
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
                "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
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
    """Account for every paid response, including a rejected first response."""

    if not records:
        return {}
    usage = dict(records[-1])
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        usage[key] = sum(int(record.get(key, 0) or 0) for record in records)
    usage["cost"] = round(
        sum(float(record.get("cost", 0) or 0) for record in records), 8
    )
    usage["latency_seconds"] = round(
        sum(float(record.get("latency_seconds", 0) or 0) for record in records), 3
    )
    usage["provider_responses"] = len(records)
    return usage


class ModelClient:
    """Keep scientific call guarantees independent of the selected LLM provider.

    LiteLLM translates the provider-prefixed model name and normalizes transport
    errors. PERLA deliberately retains the policies that affect reproducibility:
    complete-request hashing, Pydantic validation before cache admission, preserved
    failure artifacts, and one bounded application-level retry. Failed responses stay
    beside the run artifacts so provider errors cannot become silent data loss.
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
    ) -> None:
        self.cache_dir = cache_dir
        self.output_dir = output_dir
        self.heartbeat_seconds = heartbeat_seconds
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_model_calls = max_model_calls
        self.max_cost_usd = max_cost_usd
        self.calls: list[dict[str, object]] = []
        self._provider_requests = 0
        self._known_cost_usd = 0.0
        self._cost_tracking_complete = True

    def _reserve_provider_request(self) -> None:
        """Enforce limits immediately before a paid request leaves the process.

        Provider cost is known only after a response, so ``max_cost_usd`` prevents the
        *next* request once reported spend reaches the limit. If a provider omits cost,
        a requested monetary limit fails closed before another request is sent.
        """

        if (
            self.max_model_calls is not None
            and self._provider_requests >= self.max_model_calls
        ):
            raise ModelBudgetExceeded(
                f"model-call budget exhausted ({self.max_model_calls} provider requests)"
            )
        if self.max_cost_usd is not None and self._provider_requests:
            if not self._cost_tracking_complete:
                raise ModelBudgetExceeded(
                    "cost budget cannot be enforced because the provider omitted cost"
                )
            if self._known_cost_usd >= self.max_cost_usd:
                raise ModelBudgetExceeded(
                    f"reported cost budget exhausted (${self.max_cost_usd:.4f})"
                )
        self._provider_requests += 1

    def _record_provider_cost(self, usage: dict) -> None:
        """Track provider-reported spend without substituting an estimated price."""

        cost = usage.get("cost")
        if cost is None:
            self._cost_tracking_complete = False
            return
        self._known_cost_usd += float(cost)

    def budget_status(self) -> dict[str, object]:
        """Expose limits and observed provider accounting in the run report."""

        return {
            "max_model_calls": self.max_model_calls,
            "max_cost_usd": self.max_cost_usd,
            "provider_requests": self._provider_requests,
            "reported_cost_usd": round(self._known_cost_usd, 8),
            "cost_tracking_complete": self._cost_tracking_complete,
        }

    def _request(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: dict,
        max_output_tokens: int,
        reasoning_effort: str | None,
    ) -> dict:
        """Build the complete request so every scientific setting enters the cache key."""

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
        }
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        if self.temperature is not None:
            body["temperature"] = self.temperature
        return body

    def _live(self, body: dict, failure_path: Path) -> tuple[dict, dict]:
        """Call LiteLLM while keeping slow requests and failures observable."""

        try:
            with heartbeat(
                failure_path.stem.removesuffix(".failure"), self.heartbeat_seconds
            ) as started:
                response = litellm.completion(**body)
        except MODEL_ERRORS as exc:
            write_json_atomic(
                failure_path,
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "status_code": getattr(exc, "status_code", None),
                    "model": body["model"],
                },
            )
            raise ModelCallError(
                f"Model provider request failed: {exc}; see {failure_path}",
                retryable=isinstance(exc, RETRYABLE_ERRORS),
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
        try:
            result = json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            write_json_atomic(
                failure_path,
                {
                    "finish_reason": choice.get("finish_reason"),
                    "usage": payload.get("usage", {}),
                    "content": content,
                },
            )
            raise ModelCallError(
                f"Model returned invalid JSON; see {failure_path}",
                retryable=choice.get("finish_reason") != "length",
            ) from exc
        usage_payload = payload.get("usage")
        usage = dict(usage_payload) if isinstance(usage_payload, dict) else {}
        hidden = getattr(response, "_hidden_params", {})
        usage.update(
            {
                "response_model": payload.get("model"),
                "provider": hidden.get("custom_llm_provider"),
                "cost": hidden.get("response_cost"),
                "finish_reason": choice.get("finish_reason"),
                "latency_seconds": round(time.monotonic() - started, 3),
            }
        )
        return result, usage

    def complete(
        self,
        *,
        kind: str,
        slug: str,
        model: str,
        system: str,
        prompt: str,
        response_model: type[ResponseModel],
        max_output_tokens: int,
        reasoning_effort: str | None,
        request_schema: dict[str, object] | None = None,
        decode: Callable[[object], object] | None = None,
    ) -> ResponseModel:
        """Return a schema-valid response from cache or a bounded live request.

        Cache entries are validated again on read. Invalid JSON, schema failures, and
        transport errors are written under ``requests/`` before ``ModelCallError`` is
        raised, so downstream code never receives an unvalidated partial object.
        """

        schema = _strict_schema(response_model, request_schema)
        body = self._request(
            model=model,
            system=system,
            prompt=prompt,
            schema=schema,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
        request_hash = hashlib.sha256(_canonical(body)).hexdigest()
        cache_key = hashlib.sha256(
            _canonical(
                {
                    "request_sha256": request_hash,
                    "validation_repair_version": VALIDATION_REPAIR_VERSION,
                }
            )
        ).hexdigest()
        request_path = self.output_dir / "requests" / f"{slug}.request.json"
        cache_path = self.cache_dir / f"{cache_key}.json"
        failure_path = self.output_dir / "requests" / f"{slug}.failure.json"
        write_json_atomic(request_path, body)
        logger.info(
            "Prepared model call {} (model={}, request={})",
            kind,
            body["model"],
            request_hash[:12],
        )
        record: dict[str, object] = {
            "kind": kind,
            "slug": slug,
            "request_sha256": request_hash,
            "cache_key_sha256": cache_key,
            "model": body["model"],
            "reasoning_effort": reasoning_effort,
            "temperature": self.temperature,
        }
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                validated = response_model.model_validate(cached["result"])
            except (
                OSError,
                TypeError,
                json.JSONDecodeError,
                KeyError,
                ValidationError,
            ):
                logger.warning("Ignoring invalid model cache entry {}", cache_path.name)
            else:
                failure_path.unlink(missing_ok=True)
                record.update(
                    {
                        "cache_hit": True,
                        "cached_response_usage": cached.get("usage", {}),
                    }
                )
                self.calls.append(record)
                logger.info("Model cache hit for {} ({})", kind, request_hash[:12])
                return validated

        last_error: ModelCallError | None = None
        current_body = body
        attempt_usage: list[dict] = []
        validation_repair = False
        for attempt in range(1, 3):
            raw_result: object = None
            try:
                logger.info(
                    "Calling model provider for {} (attempt {}/2)", kind, attempt
                )
                self._reserve_provider_request()
                raw_result, usage = self._live(current_body, failure_path)
                self._record_provider_cost(usage)
                attempt_usage.append(usage)
                decoded_result = decode(raw_result) if decode else raw_result
                validated = response_model.model_validate(decoded_result)
            except (TypeError, ValueError) as exc:
                validation_errors = (
                    exc.errors(include_url=False)
                    if isinstance(exc, ValidationError)
                    else [{"type": type(exc).__name__, "message": str(exc)}]
                )
                write_json_atomic(
                    failure_path,
                    {
                        "validation_errors": validation_errors,
                        "result": raw_result,
                    },
                )
                last_error = ModelCallError(
                    f"Model output failed Pydantic validation; see {failure_path}"
                )
                if attempt == 1:
                    current_body = _validation_repair_request(
                        body, raw_result, validation_errors
                    )
                    write_json_atomic(
                        self.output_dir
                        / "requests"
                        / f"{slug}.validation-repair.request.json",
                        current_body,
                    )
                    validation_repair = True
            except ModelCallError as exc:
                last_error = exc
            else:
                combined_usage = _aggregate_usage(attempt_usage)
                write_json_atomic(
                    cache_path,
                    {
                        "request_sha256": request_hash,
                        "cache_key_sha256": cache_key,
                        "result": validated.model_dump(mode="json"),
                        "usage": combined_usage,
                    },
                )
                record.update(
                    {
                        "cache_hit": False,
                        "usage": combined_usage,
                        "attempt_count": attempt,
                        "validation_repair": validation_repair,
                    }
                )
                failure_path.unlink(missing_ok=True)
                self.calls.append(record)
                logger.info(
                    "Completed {} in {}s ({} tokens)",
                    kind,
                    combined_usage.get("latency_seconds", "unknown"),
                    combined_usage.get("total_tokens", "unknown"),
                )
                return validated
            if attempt == 1 and last_error and last_error.retryable:
                if validation_repair:
                    logger.warning("{}; requesting one validation-aware repair", last_error)
                else:
                    logger.warning("{}; retrying once", last_error)
                    time.sleep(2)
            else:
                break
        record.update(
            {
                "cache_hit": False,
                "error": str(last_error),
                "usage": _aggregate_usage(attempt_usage),
                "attempt_count": min(2, len(attempt_usage) or 1),
                "validation_repair": validation_repair,
            }
        )
        self.calls.append(record)
        logger.error("Model call {} failed: {}", kind, last_error)
        raise last_error or ModelCallError("Unknown model-call failure")
