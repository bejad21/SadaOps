"""Pluggable Streaming TTS adapter factory (Cartesia Sonic-3 + $0 Edge-TTS + ScrubbedTTS)."""

from __future__ import annotations

import re
import uuid
from typing import Any

import edge_tts
from livekit.agents import APIConnectOptions, tts
from livekit.plugins import cartesia

from agent.config import AgentSettings
from agent.safety.tts_scrubber import scrub_for_tts

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_PEM_END_RE = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")


def pick_bilingual_voice(text: str, voice_en: str, voice_ar: str) -> str:
    """Choose the Arabic or English voice depending on whether Arabic script is present."""
    return voice_ar if _ARABIC_RE.search(text) else voice_en


class _EdgeChunkedStream(tts.ChunkedStream):
    """Synthesizes speech via edge-tts and feeds MP3 bytes into LiveKit AudioEmitter."""

    def __init__(
        self,
        *,
        tts_plugin: EdgeTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts_plugin, input_text=input_text, conn_options=conn_options)
        self._tts_plugin = tts_plugin

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        voice = pick_bilingual_voice(
            self.input_text,
            self._tts_plugin.voice_en,
            self._tts_plugin.voice_ar,
        )
        request_id = uuid.uuid4().hex
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=24000,
            num_channels=1,
            mime_type="audio/mp3",
        )
        if not re.search(r"[\w\u0600-\u06FF]", self.input_text or ""):
            output_emitter.flush()
            return
        comm = edge_tts.Communicate(self.input_text, voice=voice)
        async for chunk in comm.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                output_emitter.push(chunk["data"])
        output_emitter.flush()


class EdgeTTS(tts.TTS):
    """Zero-cost bilingual TTS plugin backed by edge-tts for dev & bulk evaluation."""

    def __init__(
        self,
        voice_en: str = "en-US-AndrewMultilingualNeural",
        voice_ar: str = "ar-EG-SalmaNeural",
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1,
        )
        self.voice_en = voice_en
        self.voice_ar = voice_ar

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions | None = None,
    ) -> _EdgeChunkedStream:
        """Synthesize a complete utterance via EdgeTTS."""
        return _EdgeChunkedStream(
            tts_plugin=self,
            input_text=text,
            conn_options=conn_options or APIConnectOptions(),
        )


class _ScrubbedSynthesizeStream:
    """Proxy around SynthesizeStream that stream-scrubs incrementally across token boundaries."""

    _TAIL_GUARD_CHARS = 96
    _MAX_PEM_HOLD_CHARS = 16384

    def __init__(self, inner_stream: tts.SynthesizeStream, wrapper: ScrubbedTTS) -> None:
        self._inner = inner_stream
        self._wrapper = wrapper
        self._pending = ""

    def _emit_scrubbed(self, text: str) -> None:
        if not text:
            return
        clean, cats = scrub_for_tts(text)
        if cats:
            self._wrapper.last_redacted_categories = cats
        if clean:
            self._inner.push_text(clean)

    def push_text(self, token: str) -> None:
        """Buffer streaming LLM tokens and emit secret-scrubbed chunks."""
        if not token:
            return
        self._pending += token
        if len(self._pending) <= self._TAIL_GUARD_CHARS:
            return

        pem_begin = self._pending.find("-----BEGIN")
        if pem_begin != -1 and len(self._pending) < self._MAX_PEM_HOLD_CHARS:
            pem_end_match = _PEM_END_RE.search(self._pending, pem_begin)
            if pem_end_match is None:
                # Hold open PEM blocks until the full END footer arrives so headers never leak.
                safe_before = max(0, pem_begin - self._TAIL_GUARD_CHARS)
                if safe_before <= 0:
                    return
                head, self._pending = self._pending[:safe_before], self._pending[safe_before:]
                self._emit_scrubbed(head)
                return
            # Flush through the complete PEM footer as one atomic unit so regex matches cleanly.
            split_at = pem_end_match.end()
            head, self._pending = self._pending[:split_at], self._pending[split_at:]
            self._emit_scrubbed(head)
            return

        safe_end = len(self._pending) - self._TAIL_GUARD_CHARS

        ws_idx = max(self._pending.rfind(" ", 0, safe_end), self._pending.rfind("\n", 0, safe_end))
        split_at = ws_idx + 1 if ws_idx > 0 else safe_end
        head, self._pending = self._pending[:split_at], self._pending[split_at:]
        self._emit_scrubbed(head)

    def _drain_scrubbed(self) -> None:
        if not self._pending:
            return
        remaining = self._pending
        self._pending = ""
        self._emit_scrubbed(remaining)

    def flush(self) -> None:
        """Scrub and flush any buffered text to the inner TTS stream."""
        self._drain_scrubbed()
        self._inner.flush()

    def end_input(self) -> None:
        """Drain remaining buffered text and signal end of input."""
        self._drain_scrubbed()
        self._inner.end_input()

    async def aclose(self) -> None:
        """Close the wrapped TTS synthesis stream."""
        await self._inner.aclose()

    async def __aenter__(self) -> _ScrubbedSynthesizeStream:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        return await self._inner.__aexit__(exc_type, exc, tb)

    def __aiter__(self) -> Any:
        return self._inner.__aiter__()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class ScrubbedTTS(tts.TTS):
    """TTS wrapper that redacts secrets and forwards LiveKit metrics/error events."""

    def __init__(self, inner: tts.TTS) -> None:
        super().__init__(
            capabilities=inner.capabilities,
            sample_rate=inner.sample_rate,
            num_channels=inner.num_channels,
        )
        self.inner = inner
        self.last_redacted_categories: list[str] = []
        self._on_inner_metrics = lambda ev: self.emit("metrics_collected", ev)
        self._on_inner_error = lambda ev: self.emit("error", ev)
        self.inner.on("metrics_collected", self._on_inner_metrics)
        self.inner.on("error", self._on_inner_error)

    @property
    def model(self) -> str:
        """Return the underlying TTS model identifier."""
        return self.inner.model

    @property
    def provider(self) -> str:
        """Return the underlying TTS provider name."""
        return self.inner.provider

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions | None = None,
    ) -> tts.ChunkedStream:
        """Scrub secrets from `text` and delegate chunked synthesis."""
        clean_text, cats = scrub_for_tts(text)
        self.last_redacted_categories = cats
        return self.inner.synthesize(
            clean_text,
            conn_options=conn_options or APIConnectOptions(),
        )

    def stream(
        self,
        *,
        conn_options: APIConnectOptions | None = None,
    ) -> Any:
        """Return a streaming TTS wrapper that scrubs tokens across boundaries."""
        inner_stream = self.inner.stream(conn_options=conn_options or APIConnectOptions())
        return _ScrubbedSynthesizeStream(inner_stream, self)

    def prewarm(self) -> None:
        """Forward prewarm to the underlying TTS plugin."""
        self.inner.prewarm()

    async def aclose(self) -> None:
        """Detach event listeners and close the underlying TTS plugin."""
        self.inner.off("metrics_collected", self._on_inner_metrics)
        self.inner.off("error", self._on_inner_error)
        await self.inner.aclose()


def build_tts(settings: AgentSettings) -> ScrubbedTTS:
    """Build secret-scrubbed Cartesia Sonic-3 (multilingual AR/EN voice) or EdgeTTS plugin."""
    if settings.tts_provider == "cartesia":
        if not settings.cartesia_api_key:
            raise ValueError("CARTESIA_API_KEY is required when TTS_PROVIDER=cartesia")
        # Sonic-3 handles bilingual Arabic + English with `voice=cartesia_voice_ar` (Raed)
        # while `language=None` enables automatic multilingual speech synthesis.
        inner: tts.TTS = cartesia.TTS(
            api_key=settings.cartesia_api_key,
            model="sonic-3",
            voice=settings.cartesia_voice_ar or settings.cartesia_voice_en,
            language=None,
        )
    else:
        inner = EdgeTTS(
            voice_en=settings.edge_voice_en,
            voice_ar=settings.edge_voice_ar,
        )
    return ScrubbedTTS(inner=inner)
