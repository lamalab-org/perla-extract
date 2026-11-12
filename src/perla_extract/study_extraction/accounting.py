"""Own logical-call and provider-attempt accounting independently of artifact I/O.

Attempt records are registered before response files are written. All cost and token
views derive from those records; cache origins never become charges for this run.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from .deployment import RequestEstimate


def aggregate_usage(records: list[dict]) -> dict:
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
    usage = aggregate_usage([attempt["usage"] for attempt in attempts])
    # Old callers/fixtures can still supply one aggregate per logical call.
    if not attempts:
        usage = aggregate_usage(
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


@dataclass
class AttemptRecord:
    """One provider attempt, retained before writing its response artifacts."""

    attempt_id: str
    attempt_index: int
    status: str
    request_sha256: str
    input_estimate: RequestEstimate
    usage: dict[str, Any]
    elapsed_seconds: float
    response_received: bool
    error: str | None
    response_artifact: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CallRecord:
    """A logical call with explicit lifecycle state and derived usage totals."""

    kind: str
    slug: str
    stage: str
    call_id: str
    run_id: str
    stage_id: str | None
    request_sha256: str
    cache_key_sha256: str
    model: str
    reasoning_effort: str | None
    temperature: float | None
    deployment: dict[str, object]
    started_at: float
    attempts: list[AttemptRecord] = field(default_factory=list)
    status: str = "running"
    cache_hit: bool = False
    error: str | None = None
    validation_repair: bool = False
    cached_response_usage: dict[str, Any] | None = None
    cache_origin: object = None
    cache_write_error: str | None = None
    input_estimate: RequestEstimate | None = None
    elapsed_seconds: float | None = None
    finalized: bool = False

    @property
    def usage(self) -> dict[str, Any]:
        return aggregate_usage([attempt.usage for attempt in self.attempts])

    def finish(self) -> None:
        self.elapsed_seconds = round(time.monotonic() - self.started_at, 6)
        self.finalized = True

    def to_dict(self) -> dict[str, Any]:
        """Keep the existing artifact keys while internal state remains typed."""
        result: dict[str, Any] = {
            "kind": self.kind,
            "slug": self.slug,
            "stage": self.stage,
            "call_id": self.call_id,
            "run_id": self.run_id,
            "stage_id": self.stage_id,
            "request_sha256": self.request_sha256,
            "cache_key_sha256": self.cache_key_sha256,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "temperature": self.temperature,
            "deployment": self.deployment,
            "deployment_verification": "operator_declared_unverified",
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "attempt_count": len(self.attempts),
            "cache_hit": self.cache_hit,
            "status": self.status,
        }
        for name in ("error", "cache_write_error", "input_estimate", "elapsed_seconds"):
            if (value := getattr(self, name)) is not None:
                result[name] = value
        if self.cache_hit:
            result["cached_response_usage"] = self.cached_response_usage or {}
            result["cache_origin"] = self.cache_origin
        elif self.attempts or self.finalized:
            result["usage"] = self.usage
            result["validation_repair"] = self.validation_repair
        return result


class CallAccounting:
    """Keep one source of observed charges and explicit pre-dispatch reservations."""

    def __init__(self) -> None:
        self.records: list[CallRecord] = []
        # Reservations include requests blocked by local I/O before dispatch, matching
        # the conservative provider-request budget. Paid usage comes from attempts.
        self.provider_requests = 0

    @property
    def calls(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.records]

    @property
    def attempts(self) -> list[AttemptRecord]:
        return [attempt for record in self.records for attempt in record.attempts]

    @property
    def known_cost_usd(self) -> float:
        return sum(float(attempt.usage.get("cost") or 0) for attempt in self.attempts)

    @property
    def known_tokens(self) -> int:
        return sum(
            int(attempt.usage.get("total_tokens") or 0) for attempt in self.attempts
        )

    @property
    def cost_tracking_complete(self) -> bool:
        return all(attempt.usage.get("cost") is not None for attempt in self.attempts)

    @property
    def token_tracking_complete(self) -> bool:
        # Token-budget enforcement needs observed totals; reporting additionally
        # checks the prompt/completion breakdown through aggregate_usage().
        return all(
            attempt.usage.get("total_tokens") is not None for attempt in self.attempts
        )

    def reserve_request(self) -> None:
        self.provider_requests += 1

    def record_attempt(self, call: CallRecord, attempt: AttemptRecord) -> None:
        call.attempts.append(attempt)

    @contextmanager
    def call(
        self, record: CallRecord, *, finalize: Callable[[CallRecord], None]
    ) -> Iterator[CallRecord]:
        """Preserve paid work if sizing, validation, or local persistence raises."""
        self.records.append(record)
        try:
            yield record
        except BaseException as error:
            if not record.finalized:
                record.status = (
                    "interrupted"
                    if isinstance(error, (KeyboardInterrupt, SystemExit))
                    else "failed"
                )
                record.error = (
                    f"{type(error).__name__}: logical call did not complete; "
                    "inspect preserved artifacts"
                )
                try:
                    finalize(record)
                except Exception:
                    # A full/unavailable disk may be the original failure. The run
                    # boundary can still summarize the already retained records.
                    pass
            raise
