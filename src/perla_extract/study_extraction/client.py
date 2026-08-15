"""Small cached OpenRouter client for schema-constrained extraction calls."""

from __future__ import annotations

import hashlib
import http.client
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .logging import logger
from .progress import heartbeat

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class ModelCallError(RuntimeError):
    """Report a failed call after preserving its inspectable response."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def _strict_schema(model: type[BaseModel]) -> dict:
    """Make every object property required as strict OpenAI providers demand."""

    schema = model.model_json_schema()

    def close(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
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


def _atomic_json(path: Path, value: object) -> None:
    """Prevent interrupted writes from becoming valid-looking cache entries."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


def _model_name(model: str, provider_sort: str) -> str:
    """Normalize LiteLLM-style names and optionally request OpenRouter Exacto."""

    if model.startswith("openrouter/"):
        model = model.removeprefix("openrouter/")
    if provider_sort == "quality" and not model.endswith(":exacto"):
        return f"{model}:exacto"
    return model


class OpenRouterClient:
    """Execute validated model calls with caching, heartbeats, and bounded retries."""

    def __init__(
        self,
        *,
        api_key: str | None,
        cache_dir: Path,
        output_dir: Path,
        heartbeat_seconds: float = 20,
        timeout_seconds: float = 600,
        provider_sort: str = "quality",
        temperature: float | None = 0,
    ) -> None:
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.output_dir = output_dir
        self.heartbeat_seconds = heartbeat_seconds
        self.timeout_seconds = timeout_seconds
        self.provider_sort = provider_sort
        self.temperature = temperature
        self.calls: list[dict[str, object]] = []

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

        provider: dict[str, object] = {"require_parameters": True}
        if self.provider_sort not in {"quality", "none"}:
            provider["sort"] = self.provider_sort
        body: dict[str, object] = {
            "model": _model_name(model, self.provider_sort),
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
            "provider": provider,
            "seed": 0,
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        if reasoning_effort is not None:
            body["reasoning"] = {"effort": reasoning_effort}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        return body

    def _live(self, body: dict, failure_path: Path) -> tuple[dict, dict]:
        """Send one request while logging progress during slow provider responses."""

        if not self.api_key:
            raise ModelCallError(
                "OPENROUTER_API_KEY is not set and no cached response exists"
            )
        request = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/lamalab-org/perla-extract",
                "X-OpenRouter-Title": "PERLA study extractor",
            },
            method="POST",
        )
        try:
            with heartbeat(
                failure_path.stem.removesuffix(".failure"), self.heartbeat_seconds
            ) as started, urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            response_text = exc.read().decode("utf-8", errors="replace")
            _atomic_json(
                failure_path,
                {
                    "http_status": exc.code,
                    "reason": str(exc.reason),
                    "response": response_text,
                },
            )
            raise ModelCallError(
                f"OpenRouter HTTP {exc.code}; see {failure_path}",
                retryable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except http.client.IncompleteRead as exc:
            _atomic_json(
                failure_path,
                {
                    "error": str(exc),
                    "partial_response": exc.partial.decode("utf-8", errors="replace"),
                },
            )
            raise ModelCallError(
                f"OpenRouter response was incomplete; see {failure_path}",
                retryable=True,
            ) from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            http.client.HTTPException,
        ) as exc:
            _atomic_json(failure_path, {"error": str(exc)})
            raise ModelCallError(
                f"OpenRouter request failed: {exc}; see {failure_path}"
            ) from exc
        choice = payload.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content")
        try:
            result = json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            _atomic_json(
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
        usage = dict(payload.get("usage", {}))
        usage.update(
            {
                "response_model": payload.get("model"),
                "provider": payload.get("provider"),
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
    ) -> ResponseModel:
        """Return only locally Pydantic-validated cached or live output."""

        schema = _strict_schema(response_model)
        body = self._request(
            model=model,
            system=system,
            prompt=prompt,
            schema=schema,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
        request_hash = hashlib.sha256(_canonical(body)).hexdigest()
        request_path = self.output_dir / "requests" / f"{slug}.request.json"
        cache_path = self.cache_dir / f"{request_hash}.json"
        failure_path = self.output_dir / "requests" / f"{slug}.failure.json"
        _atomic_json(request_path, body)
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
            "model": body["model"],
            "reasoning_effort": reasoning_effort,
            "temperature": self.temperature,
        }
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                validated = response_model.model_validate(cached["result"])
            except (json.JSONDecodeError, KeyError, ValidationError):
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
        for attempt in range(1, 3):
            try:
                logger.info("Calling OpenRouter for {} (attempt {}/2)", kind, attempt)
                raw_result, usage = self._live(body, failure_path)
                validated = response_model.model_validate(raw_result)
            except ValidationError as exc:
                _atomic_json(
                    failure_path,
                    {
                        "validation_errors": exc.errors(include_url=False),
                        "result": raw_result,
                    },
                )
                last_error = ModelCallError(
                    f"Model output failed Pydantic validation; see {failure_path}"
                )
            except ModelCallError as exc:
                last_error = exc
            else:
                _atomic_json(
                    cache_path,
                    {
                        "request_sha256": request_hash,
                        "result": validated.model_dump(mode="json"),
                        "usage": usage,
                    },
                )
                record.update({"cache_hit": False, "usage": usage})
                failure_path.unlink(missing_ok=True)
                self.calls.append(record)
                logger.info(
                    "Completed {} in {}s ({} tokens)",
                    kind,
                    usage.get("latency_seconds", "unknown"),
                    usage.get("total_tokens", "unknown"),
                )
                return validated
            if attempt == 1 and last_error and last_error.retryable:
                logger.warning("{}; retrying once", last_error)
                time.sleep(2)
            else:
                break
        record.update({"cache_hit": False, "error": str(last_error)})
        self.calls.append(record)
        logger.error("Model call {} failed: {}", kind, last_error)
        raise last_error or ModelCallError("Unknown model-call failure")
