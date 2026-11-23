"""Groq streaming LLM router with 429 rate-limit backoff and model fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def load_groq_api_keys(primary: str = "") -> list[str]:
    """Collect deduplicated Groq API keys from primary arg and GROQ_API_KEY[_N] env vars."""
    candidates: list[str] = []
    if primary:
        candidates.extend(primary.split(","))
    for env_name in ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3"):
        val = os.getenv(env_name, "")
        if val:
            candidates.extend(val.split(","))
    seen: list[str] = []
    for raw in candidates:
        k = raw.strip()
        if k and k not in seen:
            seen.append(k)
    return seen


class LLMProviderError(RuntimeError):
    """Raised when both primary and fallback LLM models fail."""


class StreamInterruptedError(LLMProviderError):
    """Raised when a stream disconnects after already emitting chunks to the caller."""


@dataclass(frozen=True)
class LLMStreamChunk:
    """Normalized streaming delta emitted by the Groq router."""

    text: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    model_used: str = ""


class GroqLLMRouter:
    """Async Groq OpenAI-compatible router with rate-limit backoff and failover."""

    def __init__(
        self,
        api_key: str | Sequence[str],
        primary_model: str = "openai/gpt-oss-20b",
        fallback_model: str = "openai/gpt-oss-120b",
        timeout_s: float = 15.0,
        max_retries: int = 3,
        base_backoff_s: float = 0.5,
        max_backoff_s: float = 5.0,
        sleep_fn: Callable[[float], Any] = asyncio.sleep,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        raw_keys = [api_key] if isinstance(api_key, str) else list(api_key)
        expanded = [k.strip() for item in raw_keys for k in item.split(",") if k.strip()]
        if not expanded:
            raise ValueError("At least one Groq API key is required.")
        self.api_keys = list(dict.fromkeys(expanded))
        self.api_key = self.api_keys[0]
        self._key_cursor = 0
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.base_backoff_s = base_backoff_s
        self.max_backoff_s = max_backoff_s
        self._sleep = sleep_fn
        self._external_client = client
        self._base_url = "https://api.groq.com/openai/v1/chat/completions"

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream chat completion chunks, failing over from primary to fallback model."""
        models_to_try = [self.primary_model]
        if self.fallback_model and self.fallback_model != self.primary_model:
            models_to_try.append(self.fallback_model)

        last_exc: Exception | None = None
        for model in models_to_try:
            try:
                async for chunk in self._stream_with_retries(
                    model=model,
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                ):
                    yield chunk
                return
            except StreamInterruptedError:
                raise
            except Exception as exc:
                last_exc = exc

        raise LLMProviderError(f"All Groq models failed: {last_exc}") from last_exc

    async def _stream_with_retries(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
    ) -> AsyncIterator[LLMStreamChunk]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        total_attempts = max(self.max_retries, len(self.api_keys) * 2)
        start_idx = self._key_cursor % len(self.api_keys)
        self._key_cursor = (self._key_cursor + 1) % len(self.api_keys)

        for attempt in range(total_attempts):
            active_key = self.api_keys[(start_idx + attempt) % len(self.api_keys)]
            headers = {
                "Authorization": f"Bearer {active_key}",
                "Content-Type": "application/json",
            }
            yielded_any = False
            try:
                async for chunk in self._execute_stream(headers, payload, model):
                    yielded_any = True
                    yield chunk
                return
            except httpx.HTTPStatusError as exc:
                if yielded_any:
                    raise StreamInterruptedError(
                        f"Stream interrupted mid-response on {model}: {exc}"
                    ) from exc
                status = exc.response.status_code
                if status in (429, 500, 502, 503, 504) and attempt < total_attempts - 1:
                    # When multiple API keys exist and 429 occurs, switch keys immediately
                    # on the first pass before waiting on rate-limit backoff.
                    if (
                        status == 429
                        and len(self.api_keys) > 1
                        and attempt < len(self.api_keys) - 1
                    ):
                        continue
                    retry_after = exc.response.headers.get("retry-after")
                    raw_delay = (
                        float(retry_after)
                        if retry_after and retry_after.replace(".", "", 1).isdigit()
                        else self.base_backoff_s * (2**min(attempt, 4))
                    )
                    delay = min(raw_delay, self.max_backoff_s)
                    await self._sleep(delay)
                    continue
                raise
            except (httpx.TransportError, httpx.StreamError) as exc:
                if yielded_any:
                    raise StreamInterruptedError(
                        f"Stream disconnected mid-response on {model}: {exc}"
                    ) from exc
                if attempt >= total_attempts - 1:
                    raise
                delay = min(self.base_backoff_s * (2**min(attempt, 4)), self.max_backoff_s)
                await self._sleep(delay)
                continue
            except Exception as exc:
                if yielded_any:
                    raise StreamInterruptedError(
                        f"Stream failed mid-response on {model}: {exc}"
                    ) from exc
                raise

    async def _execute_stream(
        self,
        headers: dict[str, str],
        payload: dict[str, Any],
        model: str,
    ) -> AsyncIterator[LLMStreamChunk]:
        owns_client = self._external_client is None
        client = self._external_client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            async with client.stream("POST", self._base_url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    resp.raise_for_status()

                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Skipping malformed SSE frame from %s: %r",
                            model,
                            data_str[:120],
                        )
                        continue
                    if not isinstance(data, dict):
                        continue
                    choice = (data.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    yield LLMStreamChunk(
                        text=delta.get("content") or "",
                        tool_calls=delta.get("tool_calls"),
                        finish_reason=choice.get("finish_reason"),
                        model_used=model,
                    )
        finally:
            if owns_client:
                await client.aclose()
