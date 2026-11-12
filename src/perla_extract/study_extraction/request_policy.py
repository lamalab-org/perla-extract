"""Resolved options shared by request planning and provider dispatch.

Configuration owns inheritance. A resolved policy has no fallback decisions left:
its output reservation, input cap and generation options describe one stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .deployment import (
    DeploymentSettings,
    RequestEstimate,
    estimate_request_input,
    request_limit_issues,
)


@dataclass(frozen=True)
class ResolvedStagePolicy:
    """Transport, generation settings and limits selected for one named stage."""

    stage: str
    model: str
    deployment: DeploymentSettings
    max_output_tokens: int
    reasoning_effort: str | None = None
    temperature: float | None = None

    @classmethod
    def resolve(
        cls,
        stage: str,
        *,
        model: str,
        deployment: DeploymentSettings,
        max_output_tokens: int,
        reasoning_effort: str | None,
        temperature: float | None,
    ) -> ResolvedStagePolicy:
        """Apply deployment generation overrides once, after stage inheritance."""
        return cls(
            stage=stage,
            model=deployment.model or model,
            deployment=deployment,
            max_output_tokens=deployment.max_output_tokens or max_output_tokens,
            reasoning_effort=deployment.reasoning_effort
            if deployment.reasoning_effort is not None
            else reasoning_effort,
            temperature=deployment.temperature
            if deployment.temperature is not None
            else temperature,
        )

    def input_limit(self) -> int | None:
        """Return the input allowance after reserving the resolved output cap."""
        limits = [self.deployment.max_input_tokens]
        if self.deployment.context_tokens is not None:
            limits.append(self.deployment.context_tokens - self.max_output_tokens)
        declared = [limit for limit in limits if limit is not None]
        return min(declared) if declared else None

    def limit_issues(
        self, input_tokens: int, output_tokens: int | None = None
    ) -> list[str]:
        return request_limit_issues(
            input_tokens,
            self.max_output_tokens if output_tokens is None else output_tokens,
            self.deployment,
        )

    def estimate(
        self,
        system: str,
        prompt: Any,
        schema: dict,
        *,
        messages: list[dict] | None = None,
        components: dict[str, object] | None = None,
    ) -> RequestEstimate:
        """Size the final response schema and messages, including correction turns."""
        return estimate_request_input(
            system,
            prompt,
            schema,
            messages=messages,
            tokenizer=self.deployment.tokenizer,
            components=components,
        )

    def identity(self) -> dict[str, object]:
        """Record resolved settings without exposing credential values."""
        return {
            "stage": self.stage,
            "model": self.model,
            "deployment": self.deployment.identity(),
            "max_output_tokens": self.max_output_tokens,
            "reasoning_effort": self.reasoning_effort,
            "temperature": self.temperature,
        }
