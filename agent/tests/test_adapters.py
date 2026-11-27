"""Unit tests for Phase 1 STT and TTS adapters."""

from __future__ import annotations

import pytest

from agent.config import AgentSettings
from agent.pipeline.stt_adapter import build_stt
from agent.pipeline.tool_router import build_mcp_server_env
from agent.pipeline.tts_adapter import EdgeTTS, ScrubbedTTS, build_tts, pick_bilingual_voice


def test_pick_bilingual_voice_detects_arabic_vs_english() -> None:
    assert pick_bilingual_voice("Hello SadaOps", "en-voice", "ar-voice") == "en-voice"
    assert pick_bilingual_voice("أهلا صدى أوبس", "en-voice", "ar-voice") == "ar-voice"
    assert pick_bilingual_voice("شوفلي auth-gateway", "en-voice", "ar-voice") == "ar-voice"


def test_build_tts_selects_edge_or_cartesia() -> None:
    edge_settings = AgentSettings(tts_provider="edge")
    edge_plugin = build_tts(edge_settings)
    assert isinstance(edge_plugin, ScrubbedTTS)
    assert isinstance(edge_plugin.inner, EdgeTTS)

    cartesia_settings = AgentSettings(tts_provider="cartesia", cartesia_api_key="test-key")
    plugin = build_tts(cartesia_settings)
    assert isinstance(plugin, ScrubbedTTS)
    assert plugin.inner.__class__.__name__ == "FallbackAdapter"
    assert len(plugin.inner._tts_instances) == 2


def test_build_stt_requires_key() -> None:
    with pytest.raises(ValueError):
        build_stt(
            AgentSettings(
                _env_file=None,
                stt_provider="speechmatics",
                speechmatics_api_key="",
            )
        )

    stt_plugin = build_stt(
        AgentSettings(
            _env_file=None,
            stt_provider="speechmatics",
            speechmatics_api_key="test-key",
            groq_api_key="",
            groq_api_key_2="",
        )
    )
    assert stt_plugin.__class__.__name__ == "STT"
    assert stt_plugin._vad is None
    assert stt_plugin.capabilities.manual_flush is True

    fallback_stt = build_stt(
        AgentSettings(
            _env_file=None,
            stt_provider="speechmatics",
            speechmatics_api_key="test-key",
            groq_api_key="gsk_test_fallback",
        )
    )
    assert fallback_stt.__class__.__name__ == "FallbackAdapter"
    assert [eng.__class__.__name__ for eng in fallback_stt._stt_instances] == [
        "STT",
        "StreamAdapter",
    ]


def test_build_stt_gladia_requires_key_and_configures_ar_en() -> None:
    with pytest.raises(ValueError):
        build_stt(
            AgentSettings(
                _env_file=None,
                stt_provider="gladia",
                gladia_api_key="",
            )
        )

    gladia_plugin = build_stt(
        AgentSettings(
            _env_file=None,
            stt_provider="gladia",
            gladia_api_key="gladia_test_key",
            groq_api_key="",
            groq_api_key_2="",
        )
    )
    assert gladia_plugin.__class__.__name__ == "STT"
    assert gladia_plugin.capabilities.streaming is True
    assert gladia_plugin.capabilities.interim_results is True

    gladia_fallback = build_stt(
        AgentSettings(
            _env_file=None,
            stt_provider="gladia",
            gladia_api_key="gladia_test_key",
            groq_api_key="gsk_test_fallback",
        )
    )
    assert gladia_fallback.__class__.__name__ == "FallbackAdapter"
    assert [eng.__class__.__name__ for eng in gladia_fallback._stt_instances] == [
        "STT",
        "StreamAdapter",
    ]


def test_scrubbed_tts_forwards_metrics_and_mcp_env_isolates_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = ScrubbedTTS(EdgeTTS())
    seen: list[object] = []
    wrapper.on("metrics_collected", lambda ev: seen.append(ev))
    wrapper.inner.emit("metrics_collected", {"ttfb": 0.12})
    assert len(seen) == 1

    monkeypatch.setenv("GITHUB_MODE", "live")
    monkeypatch.setenv("GITHUB_TOKEN", "gh_test_token")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_super_secret")
    gh_env = build_mcp_server_env("github")
    assert gh_env.get("GITHUB_MODE") == "live"
    assert gh_env.get("GITHUB_TOKEN") == "gh_test_token"
    assert "GROQ_API_KEY" not in gh_env


def test_build_turn_handling_uses_local_model_and_cough_resume() -> None:
    import warnings

    from agent.main import build_turn_handling

    settings = AgentSettings(_env_file=None, enable_turn_detector=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        th = build_turn_handling(settings)

    lk_warnings = [w for w in caught if "livekit" in str(w.message).lower()]
    assert lk_warnings == []
    assert th["turn_detection"].model == "turn-detector-v1-mini"
    assert th["interruption"]["mode"] == "vad"
    assert th["interruption"]["resume_false_interruption"] is True
    assert th["interruption"]["false_interruption_timeout"] == 2.0
    assert th["preemptive_generation"]["enabled"] is False
