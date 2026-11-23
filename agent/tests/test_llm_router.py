"""Unit tests for GroqLLMRouter backoff and model failover."""

from __future__ import annotations

import httpx
import pytest

from agent.pipeline.llm_router import GroqLLMRouter, LLMProviderError


@pytest.mark.asyncio
async def test_groq_router_retries_429_and_succeeds() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": "0.1"}, text="rate limited")
        sse = (
            'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse)

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = GroqLLMRouter(
            api_key="test-key",
            max_retries=3,
            sleep_fn=fake_sleep,
            client=client,
        )
        chunks = [c async for c in router.stream_chat([{"role": "user", "content": "hi"}])]

    assert attempts == 2
    assert sleeps == [0.1]
    assert "".join(c.text for c in chunks) == "Hello"


@pytest.mark.asyncio
async def test_groq_router_fails_over_on_timeout_to_fallback_model() -> None:
    models_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        if "openai/gpt-oss-20b" in body:
            models_seen.append("primary")
            raise httpx.ReadTimeout("timed out")
        models_seen.append("fallback")
        sse = (
            'data: {"choices":[{"delta":{"content":"Fallback ok"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse)

    async def no_sleep(_sec: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = GroqLLMRouter(
            api_key="test-key",
            primary_model="openai/gpt-oss-20b",
            fallback_model="qwen/qwen3.8-27b",
            max_retries=2,
            sleep_fn=no_sleep,
            client=client,
        )
        chunks = [c async for c in router.stream_chat([{"role": "user", "content": "hi"}])]

    assert models_seen == ["primary", "primary", "fallback"]
    assert "".join(c.text for c in chunks) == "Fallback ok"
    assert chunks[0].model_used == "qwen/qwen3.8-27b"


@pytest.mark.asyncio
async def test_groq_router_raises_when_all_models_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    async def no_sleep(_sec: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = GroqLLMRouter(api_key="test-key", max_retries=2, sleep_fn=no_sleep, client=client)
        with pytest.raises(LLMProviderError):
            _ = [c async for c in router.stream_chat([{"role": "user", "content": "hi"}])]


@pytest.mark.asyncio
async def test_groq_router_rotates_multiple_keys_and_fails_over_on_429() -> None:
    auth_headers: list[str] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        auth_headers.append(auth)
        if auth == "Bearer key-1":
            return httpx.Response(429, headers={"retry-after": "5"}, text="tpm limit")
        sse = (
            'data: {"choices":[{"delta":{"content":"Key 2 ok"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse)

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = GroqLLMRouter(
            api_key=["key-1", "key-2"],
            max_retries=2,
            sleep_fn=fake_sleep,
            client=client,
        )
        chunks = [c async for c in router.stream_chat([{"role": "user", "content": "hi"}])]

    assert auth_headers == ["Bearer key-1", "Bearer key-2"]
    assert sleeps == []
    assert "".join(c.text for c in chunks) == "Key 2 ok"


@pytest.mark.asyncio
async def test_groq_router_caps_huge_retry_after() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": "900"}, text="rate limit")
        sse = (
            'data: {"choices":[{"delta":{"content":"Recovered fast"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse)

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = GroqLLMRouter(
            api_key="single-key",
            max_retries=2,
            max_backoff_s=5.0,
            sleep_fn=fake_sleep,
            client=client,
        )
        chunks = [c async for c in router.stream_chat([{"role": "user", "content": "hi"}])]

    assert sleeps == [5.0]
    assert "".join(c.text for c in chunks) == "Recovered fast"


class _MockAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, items: list[bytes | BaseException]) -> None:
        self._items = items

    async def __aiter__(self):
        for item in self._items:
            if isinstance(item, BaseException):
                raise item
            yield item


@pytest.mark.asyncio
async def test_groq_router_halts_on_mid_stream_drop_and_skips_malformed_sse() -> None:
    from agent.pipeline.llm_router import StreamInterruptedError

    def drop_handler(request: httpx.Request) -> httpx.Response:
        model = request.content.decode("utf-8")
        if "primary" in model:
            return httpx.Response(
                200,
                stream=_MockAsyncByteStream(
                    [
                        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
                        b"data: {BAD_JSON}\n\n",
                        b"data: null\n\n",
                        httpx.ReadError("socket reset", request=request),
                    ]
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError("Must never fail over to fallback model after yielding chunks")

    async with httpx.AsyncClient(transport=httpx.MockTransport(drop_handler)) as client:
        router = GroqLLMRouter(
            api_key="test-key",
            primary_model="primary-model",
            fallback_model="fallback-model",
            max_retries=2,
            client=client,
        )
        emitted: list[str] = []
        with pytest.raises(StreamInterruptedError):
            async for chunk in router.stream_chat([{"role": "user", "content": "hi"}]):
                if chunk.text:
                    emitted.append(chunk.text)
        assert emitted == ["Hello"]

    def bad_frame_handler(_request: httpx.Request) -> httpx.Response:
        sse = (
            "data: {CORRUPT_FRAME}\n\n"
            "data: null\n\n"
            'data: {"choices":[{"delta":{"content":"Clean"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(bad_frame_handler)) as client2:
        router2 = GroqLLMRouter(api_key="test-key", max_retries=1, client=client2)
        chunks2 = [c.text async for c in router2.stream_chat([{"role": "user", "content": "hi"}])]
        assert "".join(chunks2) == "Clean"
