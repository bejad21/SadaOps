"""Typed configuration for the SadaOps LiveKit voice agent worker."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Environment-backed voice agent worker settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secretsecretsecretsecretsecret32"

    speechmatics_api_key: str = ""
    gladia_api_key: str = ""
    groq_api_key: str = ""
    groq_api_key_2: str = ""
    cartesia_api_key: str = ""

    stt_provider: Literal["speechmatics", "gladia", "faster_whisper"] = "speechmatics"
    tts_provider: Literal["edge", "cartesia"] = "edge"

    llm_model: str = "openai/gpt-oss-20b"
    llm_fallback_model: str = "openai/gpt-oss-120b"
    llm_timeout_s: float = 15.0
    llm_max_retries: int = 3

    enable_turn_detector: bool = True
    false_interruption_timeout_s: float = 2.0
    resume_false_interruption: bool = True
    enable_supervisor_routing: bool = True

    # Default Cartesia voices verified against /voices
    cartesia_voice_en: str = "47c38ca4-5f35-497b-b1a3-415245fb35e1"  # Daniel - Modern Assistant
    cartesia_voice_ar: str = "f9f9f472-6247-4403-9abe-616f7c407c63"  # Raed - Workflow Analyst
    edge_voice_en: str = "en-US-AndrewMultilingualNeural"
    edge_voice_ar: str = "ar-EG-SalmaNeural"

    otel_exporter_otlp_endpoint: str = "http://127.0.0.1:4318"
    otel_service_name: str = "sadaops-agent"


@lru_cache
def get_agent_settings() -> AgentSettings:
    """Return cached AgentSettings instance."""
    return AgentSettings()
