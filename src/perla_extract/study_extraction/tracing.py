"""Optional Langfuse view of canonical local events; no automatic instrumentation.

The model client owns attempts, cache hits and validation outcomes. Summaries are
always visible; prompt and response capture is an explicit debugging option.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class LangfuseAdapter:
    """Map sequential run/stage/attempt boundaries onto SDK observations.

    Content capture reads the same credential-free artifacts used for local auditing.
    It never reads environment files or changes model requests. The journal isolates
    exporter failures from the extraction result.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        output_dir: Path | None = None,
        capture_content: bool = False,
    ) -> None:
        if client is None:
            if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get(
                "LANGFUSE_SECRET_KEY"
            ):
                raise RuntimeError(
                    "Langfuse tracing requires LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY; extraction can continue with local events"
                )
            try:
                from langfuse import Langfuse
            except ImportError as exc:
                raise RuntimeError(
                    "Langfuse tracing requires perla-extract[tracing]"
                ) from exc
            # The SDK appends the OTLP path verbatim. A trailing slash can send
            # exports to //api/... even when its separate auth check succeeds.
            base_url = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get(
                "LANGFUSE_HOST"
            )
            client = Langfuse(base_url=base_url.rstrip("/") if base_url else None)
        self.client = client
        self.output_dir = Path(output_dir).resolve() if output_dir else None
        self.capture_content = capture_content
        self._observations: dict[str, tuple[Any, Any]] = {}
        self._stack: list[str] = []

    def _artifact(self, relative: object) -> dict:
        if (
            not self.capture_content
            or self.output_dir is None
            or not isinstance(relative, str)
        ):
            return {}
        path = (self.output_dir / relative).resolve()
        if not path.is_relative_to(self.output_dir):
            return {}
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _input(self, event: dict[str, object]) -> dict:
        if event["event"] == "attempt.started":
            request = self._artifact(event.get("artifact"))
            if request:
                # Whitelist model input, not transport credentials or HTTP headers.
                return {
                    key: request[key]
                    for key in ("messages", "response_format")
                    if key in request
                }
        summary: dict[str, object] = {
            key: event[key]
            for key in (
                "paper",
                "supplement",
                "models",
                "readings",
                "dry_run",
                "stage",
                "kind",
                "model",
                "attempt_index",
                "input_estimate",
                "max_output_tokens",
                "artifact",
            )
            if key in event
        }
        return summary or {"operation": str(event["event"]).split(".")[0]}

    def _output(self, event: dict[str, object]) -> dict:
        summary: dict[str, object] = {
            key: event[key]
            for key in (
                "status",
                "errors",
                "error",
                "error_type",
                "decisions",
                "fallback",
                "attempt_count",
                "cache_hit",
                "usage",
                "elapsed_seconds",
                "response_artifact",
            )
            if key in event
        }
        if event["event"] == "attempt.finished" and self.capture_content:
            relative = event.get("response_artifact")
            response = self._artifact(relative)
            if response.get("result") is not None:
                return {
                    "response": response["result"],
                    "status": event.get("status"),
                    "validation_errors": response.get("validation_errors", []),
                }
            if isinstance(relative, str):
                failure = self._artifact(
                    relative.removesuffix(".response.json") + ".failure.json"
                )
                if "content" in failure:
                    summary["response"] = failure["content"]
                    summary["finish_reason"] = failure.get("finish_reason")
        elif event["event"] == "run.finished":
            extraction = self._artifact("extraction.json")
            if extraction:
                summary["extraction"] = extraction
        return summary or {"status": "finished"}

    @staticmethod
    def _name(event: dict[str, object]) -> str:
        boundary = str(event["event"]).split(".")[0]
        if boundary == "stage":
            return f"Stage: {event.get('stage', 'unknown')}"
        if boundary == "call":
            return f"Call: {event.get('kind', 'model')}"
        if boundary == "attempt":
            return (
                f"Attempt {event.get('attempt_index', 1)}: {event.get('kind', 'model')}"
            )
        return str(event.get("kind") or event.get("paper") or "PERLA extraction")

    def close(self) -> None:
        """Restore parent contexts if export fails midway through an execution."""
        opened = list(self._observations.values())
        self._observations.clear()
        self._stack.clear()
        for manager, _ in reversed(opened):
            try:
                manager.__exit__(None, None, None)
            except Exception:
                pass

    def handle(self, event: dict[str, object]) -> None:
        kind = str(event["event"])
        prefix = kind.split(".", 1)[0]
        identity = str(event.get(f"{prefix}_id") or event["run_id"])
        if kind in {"run.started", "stage.started", "call.started", "attempt.started"}:
            generation = kind == "attempt.started"
            options: dict[str, object] = {
                "name": self._name(event),
                "as_type": "generation" if generation else "span",
                "metadata": event,
                "input": self._input(event),
            }
            if generation:
                options["model"] = event.get("model")
            manager = self.client.start_as_current_observation(**options)
            observation = manager.__enter__()
            self._observations[identity] = manager, observation
            self._stack.append(identity)
        elif kind in {
            "run.finished",
            "stage.finished",
            "call.finished",
            "attempt.finished",
        }:
            # A parent failure must close interrupted children without implying success.
            while identity in self._stack and self._stack[-1] != identity:
                child = self._stack.pop()
                child_manager, child_observation = self._observations.pop(child)
                try:
                    child_observation.update(
                        level="ERROR",
                        metadata={"status": "interrupted", "ended_with_parent": True},
                        output={"status": "interrupted", "ended_with_parent": True},
                    )
                finally:
                    child_manager.__exit__(None, None, None)
            opened = self._observations.pop(identity, None)
            if identity in self._stack:
                self._stack.remove(identity)
            if opened:
                manager, observation = opened
                values: dict[str, object] = {
                    "metadata": event,
                    "output": self._output(event),
                }
                if event.get("status") in {
                    "failed",
                    "blocked",
                    "interrupted",
                    "unexpected_error",
                    "provider_error",
                    "invalid_response",
                    "validation_rejected",
                }:
                    values["level"] = "ERROR"
                elif event.get("status") in {
                    "partial",
                    "needs_review",
                    "complete_needs_review",
                }:
                    values["level"] = "WARNING"
                if kind == "call.finished" and event.get("status") == "local_cache_hit":
                    values["name"] = "Local response cache hit"
                if kind == "attempt.finished":
                    usage = event.get("usage") or {}
                    if isinstance(usage, dict):
                        usage_details = {
                            target: usage[source]
                            for source, target in (
                                ("prompt_tokens", "input"),
                                ("completion_tokens", "output"),
                                ("total_tokens", "total"),
                            )
                            if usage.get(source) is not None
                        }
                        for detail_key, source, target in (
                            ("prompt_tokens_details", "cached_tokens", "input_cached"),
                            (
                                "completion_tokens_details",
                                "reasoning_tokens",
                                "output_reasoning",
                            ),
                        ):
                            details = usage.get(detail_key)
                            if (
                                isinstance(details, dict)
                                and details.get(source) is not None
                            ):
                                usage_details[target] = details[source]
                        if usage_details:
                            values["usage_details"] = usage_details
                        if usage.get("cost") is not None:
                            values["cost_details"] = {"total": usage["cost"]}
                    if event.get("status") not in {"success", "complete"}:
                        values["level"] = "ERROR"
                try:
                    observation.update(**values)
                finally:
                    manager.__exit__(None, None, None)
            if kind == "run.finished":
                self.client.flush()


def create_langfuse_adapter(
    *,
    enabled: bool = False,
    client: Any = None,
    output_dir: Path | None = None,
    capture_content: bool = False,
) -> LangfuseAdapter | None:
    """Construct telemetry only when explicitly enabled by run configuration."""
    if not enabled:
        return None
    import litellm

    callbacks: list[Any] = []
    for name in ("callbacks", "success_callback", "failure_callback"):
        configured = getattr(litellm, name, None) or []
        callbacks.extend([configured] if isinstance(configured, str) else configured)
    if any(isinstance(item, str) and "langfuse" in item.lower() for item in callbacks):
        raise RuntimeError(
            "Disable LiteLLM Langfuse callbacks before enabling PERLA tracing; two instrumentation paths would duplicate attempts"
        )
    return LangfuseAdapter(
        client, output_dir=output_dir, capture_content=capture_content
    )
