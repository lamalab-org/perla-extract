"""Optional Langfuse view of canonical local events; no automatic instrumentation.

The model client owns attempts, cache hits and validation outcomes. Installing this
adapter does not enable LiteLLM callbacks, change requests or upload full documents.
"""

from __future__ import annotations

import os
from typing import Any


class LangfuseAdapter:
    """Map sequential run/stage/attempt boundaries onto SDK observations.

    Only metadata, normalized usage and explicitly known costs are exported. Prompt and
    response text remain in local artifacts. The journal isolates adapter failures.
    """

    def __init__(self, client: Any = None) -> None:
        if client is None:
            if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get(
                "LANGFUSE_SECRET_KEY"
            ):
                raise RuntimeError(
                    "Langfuse tracing requires LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY; extraction can continue with local events"
                )
            try:
                from langfuse import get_client
            except ImportError as exc:
                raise RuntimeError(
                    "Langfuse tracing requires perla-extract[tracing]"
                ) from exc
            client = get_client()
        self.client = client
        self._observations: dict[str, tuple[Any, Any]] = {}
        self._stack: list[str] = []

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
                "name": str(
                    event.get("stage") or event.get("kind") or "PERLA extraction"
                ),
                "as_type": "generation" if generation else "span",
                "metadata": event,
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
                    )
                finally:
                    child_manager.__exit__(None, None, None)
            opened = self._observations.pop(identity, None)
            if identity in self._stack:
                self._stack.remove(identity)
            if opened:
                manager, observation = opened
                values: dict[str, object] = {"metadata": event}
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
    *, enabled: bool = False, client: Any = None
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
    return LangfuseAdapter(client)
