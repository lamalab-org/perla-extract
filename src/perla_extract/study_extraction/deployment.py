"""Deployment identity and input sizing shared by planning and actual requests.

Revision, quantization and chat-template names describe the server the operator
configured; they do not reconfigure or verify its loaded weights. These declarations
enter cache identity so different deployments never silently share results.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from pydantic import BaseModel

REASONING_LEVELS = {"omit", "none", "minimal", "low", "medium", "high", "xhigh"}


class RequestEstimate(TypedDict):
    estimated_input_tokens: int
    method: str
    framing_allowance_tokens: int
    serialized_bytes: int
    components: dict[str, int]
    contains_images: bool


@dataclass(frozen=True)
class DeploymentSettings:
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    revision: str | None = None
    quantization: str | None = None
    chat_template: str | None = None
    chat_template_kwargs: dict[str, object] = field(default_factory=dict)
    context_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None
    tokenizer: str | None = None
    top_p: float | None = None
    top_k: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "context_tokens",
            "max_input_tokens",
            "max_output_tokens",
            "top_k",
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{name} must be a positive integer")
        if self.top_p is not None and (
            type(self.top_p) not in {int, float} or not 0 < self.top_p <= 1
        ):
            raise ValueError("top_p must be in (0, 1]")
        if self.temperature is not None and (
            type(self.temperature) not in {int, float}
            or not math.isfinite(self.temperature)
            or self.temperature < 0
        ):
            raise ValueError("temperature must be a finite nonnegative number")
        for name in (
            "model",
            "base_url",
            "revision",
            "quantization",
            "chat_template",
            "reasoning_effort",
            "tokenizer",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a nonempty string")
        if self.api_key_env is not None and (
            not isinstance(self.api_key_env, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env)
        ):
            raise ValueError("api_key_env must name an environment variable")
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in REASONING_LEVELS
        ):
            raise ValueError(
                "reasoning_effort must be omit, none, minimal, low, medium, high, or xhigh"
            )
        if not isinstance(self.chat_template_kwargs, dict) or any(
            not isinstance(key, str) for key in self.chat_template_kwargs
        ):
            raise ValueError("chat_template_kwargs must be a table with string keys")
        try:
            json.dumps(self.chat_template_kwargs, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "chat_template_kwargs must contain JSON-compatible finite values"
            ) from exc
        if (self.chat_template_kwargs or self.top_k is not None) and not self.base_url:
            raise ValueError(
                "chat_template_kwargs and top_k require an explicit self-hosted base_url"
            )
        if self.base_url:
            from urllib.parse import urlsplit

            parsed = urlsplit(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("base_url must be an HTTP(S) endpoint")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(
                    "base_url must not contain credentials, query parameters, or fragments; use api_key_env"
                )

    def identity(self) -> dict[str, object]:
        value = asdict(self)
        # The name is useful for configuration but is not scientific identity.
        value.pop("api_key_env")
        if self.tokenizer and Path(self.tokenizer).is_file():
            import hashlib

            value["tokenizer_sha256"] = hashlib.sha256(
                Path(self.tokenizer).read_bytes()
            ).hexdigest()
        return value


def deployment_stage(kind: str) -> str:
    if "claim" in kind:
        return "read"
    if "refinement" in kind:
        return "refine"
    if "repair" in kind:
        return "repair"
    if "enrichment" in kind:
        return "enrich"
    return "assemble"


def request_limit_issues(
    estimated_input_tokens: int,
    reserved_output_tokens: int,
    deployment: DeploymentSettings,
) -> list[str]:
    """Apply the same declared text-request limits in planning and execution."""
    issues = []
    if (
        deployment.max_input_tokens is not None
        and estimated_input_tokens > deployment.max_input_tokens
    ):
        issues.append(
            f"request input estimate {estimated_input_tokens} exceeds deployment max_input_tokens={deployment.max_input_tokens}"
        )
    if (
        deployment.context_tokens is not None
        and estimated_input_tokens + reserved_output_tokens > deployment.context_tokens
    ):
        issues.append(
            f"request input estimate plus output reservation exceeds context_tokens={deployment.context_tokens}"
        )
    return issues


def strict_response_schema(
    model: type[BaseModel], schema: dict[str, object] | None = None
) -> dict:
    """Close objects and require nullable fields for strict provider responses."""
    result = deepcopy(schema) if schema is not None else model.model_json_schema()

    def close(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if value.get("type") == "object" and isinstance(properties, dict):
                value["required"] = list(properties)
                value["additionalProperties"] = False
            for child in value.values():
                close(child)
        elif isinstance(value, list):
            for child in value:
                close(child)

    close(result)
    return result


def estimate_request_input(
    system: str,
    prompt: Any,
    schema: dict,
    *,
    messages: list[dict] | None = None,
    tokenizer: str | None = None,
    components: dict[str, object] | None = None,
) -> RequestEstimate:
    """Size the serialized messages and response schema, including repair turns.

    The default UTF-8/3 estimate is a planning heuristic, not provider billing or a
    context-window guarantee. ``utf8_bytes`` supplies a deliberately loose text bound.
    A local tokenizer.json uses its tokenizer without downloading model files; message
    and provider-specific schema framing can still differ, so a 64-token allowance is
    reported explicitly. Images need provider-specific sizing and are flagged.
    """
    messages = (
        messages
        if messages is not None
        else [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
    )
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "study_extraction", "strict": True, "schema": schema},
    }
    payload = {"messages": messages, "response_format": response_format}

    def encode(value):
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )

    serialized = encode(payload)
    method = tokenizer or "utf8_div_3"
    if method in {"estimate", "utf8_div_3"}:

        def count(text):
            return math.ceil(len(text.encode("utf-8")) / 3)

        method = "utf8_div_3"
    elif method == "utf8_bytes":

        def count(text):
            return len(text.encode("utf-8"))
    else:
        path = Path(method)
        if not path.is_file():
            raise ValueError(
                "tokenizer must be 'estimate', 'utf8_bytes', or a local tokenizer.json; remote tokenizer downloads are not performed"
            )
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise ValueError(
                "A local tokenizer.json requires the optional tokenizers package"
            ) from exc
        local = Tokenizer.from_file(str(path))

        def count(text):
            return len(local.encode(text, add_special_tokens=False).ids)

        method = "local_tokenizer_json"
    return {
        "estimated_input_tokens": count(serialized) + 64,
        "method": method,
        "framing_allowance_tokens": 64,
        "serialized_bytes": len(serialized.encode("utf-8")),
        "components": {
            "messages": count(encode(messages)),
            "response_schema": count(encode(response_format)),
            "system": count(encode([m for m in messages if m.get("role") == "system"])),
            **{
                name: count(encode(value)) for name, value in (components or {}).items()
            },
        },
        "contains_images": '"image_url"' in serialized,
    }
