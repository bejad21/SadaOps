"""Pluggable Streaming STT adapter factory (Speechmatics, Gladia EN/AR + Groq Whisper fallback)."""

from __future__ import annotations

from typing import Any

from livekit.agents import stt
from livekit.plugins import gladia, groq, silero, speechmatics

from agent.config import AgentSettings

# Domain-specific DevOps & SadaOps terms so Speechmatics ar_en/multi preserves Latin tokens
# instead of transliterating them phonetically into Arabic script.
SADAOPS_ADDITIONAL_VOCAB: list[dict[str, Any]] = [
    {"content": "SadaOps"},
    {"content": "GitHub", "sounds_like": ["get hub", "kitab"]},
    {"content": "runbook", "sounds_like": ["run book"]},
    {"content": "runbooks", "sounds_like": ["run books"]},
    {"content": "Redis"},
    {"content": "Postgres"},
    {"content": "PostgreSQL"},
    {"content": "Qdrant"},
    {"content": "Docker"},
    {"content": "SQL"},
    {"content": "PRs", "sounds_like": ["P R s"]},
    {"content": "open PRs"},
    {"content": "CI"},
    {"content": "sev1", "sounds_like": ["sev one"]},
    {"content": "sev2", "sounds_like": ["sev two"]},
    {"content": "metrics"},
    {"content": "service metrics"},
    {"content": "deployments"},
    {"content": "calendar"},
    {"content": "inbox"},
    {"content": "issue"},
    {"content": "auth-gateway"},
    {"content": "billing-worker"},
    {"content": "vector-sync"},
    {"content": "ops_incidents"},
    {"content": "bejad21/sadaops-demo"},
    {"content": "ops-leads@example.com"},
    {"content": "INC-1041"},
]

GLADIA_CUSTOM_SPELLING: dict[str, list[str]] = {
    "GitHub": ["G-Tub", "kitab", "get hub"],
    "SadaOps": ["Sada Ops"],
    "ops-leads@example.com": ["opsleads.example.com"],
    "bejad21/sadaops-demo": ["Bajad 21", "Bajad", "Bejad 21", "sat ops demo", "setups demo"],
    "auth-gateway": ["auth gateway"],
    "billing-worker": ["billing worker"],
    "INC-1041": ["Inc. 1041", "Inc 1041"],
}


def build_stt(
    settings: AgentSettings,
    language: str = "multi",
    vad: Any = None,
) -> stt.STT:
    """Build the configured LiveKit STT plugin.

    Speechmatics with `language="multi"` handles Arabic + English code-switching
    natively while `additional_vocab` boosts Latin DevOps/service entity accuracy.
    When a Groq API key is configured, wraps Speechmatics in a LiveKit
    `stt.FallbackAdapter` with Groq Whisper (`whisper-large-v3-turbo`,
    `detect_language=True`) so worker-capacity or transient Speechmatics WS errors
    fail over seamlessly without crashing the voice session.
    """
    groq_key = (settings.groq_api_key or settings.groq_api_key_2 or "").split(",")[0].strip()

    if settings.stt_provider == "speechmatics":
        if not settings.speechmatics_api_key:
            raise ValueError("SPEECHMATICS_API_KEY is required when STT_PROVIDER=speechmatics")
        vocab_entries = [
            speechmatics.AdditionalVocabEntry(
                content=item["content"],
                sounds_like=item.get("sounds_like"),
            )
            for item in SADAOPS_ADDITIONAL_VOCAB
        ]
        primary_stt: stt.STT = speechmatics.STT(
            api_key=settings.speechmatics_api_key,
            language=language,
            include_partials=True,
            additional_vocab=vocab_entries,
            vad=None,
        )
    elif settings.stt_provider == "gladia":
        if not settings.gladia_api_key:
            raise ValueError("GLADIA_API_KEY is required when STT_PROVIDER=gladia")
        langs = ["ar", "en"] if language == "multi" else [language]
        primary_stt = gladia.STT(
            api_key=settings.gladia_api_key,
            model="solaria-1",
            languages=langs,
            code_switching=True,
            interim_results=True,
            endpointing=0.6,
            region="eu-west",
            pre_processing_audio_enhancer=False,
            pre_processing_speech_threshold=0.78,
            custom_vocabulary=[item["content"] for item in SADAOPS_ADDITIONAL_VOCAB],
            custom_spelling=GLADIA_CUSTOM_SPELLING,
        )
    else:
        raise NotImplementedError(
            f"STT provider '{settings.stt_provider}' live plugin is scheduled for Phase 4 baseline."
        )

    if groq_key:
        fallback_stt = groq.STT(
            model="whisper-large-v3-turbo",
            api_key=groq_key,
            detect_language=True,
            prompt=", ".join(item["content"] for item in SADAOPS_ADDITIONAL_VOCAB),
        )
        return stt.FallbackAdapter(
            [primary_stt, fallback_stt],
            vad=vad if vad is not None else silero.VAD.load(),
            attempt_timeout=10.0,
            max_retry_per_stt=1,
            retry_interval=5.0,
        )
    return primary_stt
