"""Unit tests for eval/metrics.py to prevent silent metric or normalization bugs."""

from __future__ import annotations

from eval.metrics import (
    bootstrap_ci,
    char_error_rate,
    english_token_recall,
    normalize_bilingual_text,
    score_tool_calls,
    word_error_rate,
)


def test_normalize_bilingual_text_unifies_arabic_and_latin() -> None:
    raw = "أهلاً! شُوفلي الـ Auth-Gateway على الإيميل ده."
    norm = normalize_bilingual_text(raw)
    assert "اهلا" in norm
    assert "auth gateway" in norm
    assert "الايميل" in norm


def test_wer_and_cer_edge_cases() -> None:
    assert word_error_rate("Check auth gateway", "check auth-gateway!") == 0.0
    assert word_error_rate("one two three", "one two four") == round(1 / 3, 4)
    assert char_error_rate("أحمد", "احمد") == 0.0
    assert word_error_rate("", "") == 0.0


def test_english_token_recall_in_code_switching() -> None:
    ref = "افتح ال github وشوف ال latency بتاع auth-gateway"
    hyp = "افتح ال github وشوف ال latency بتاع اوث جيتواي"
    # Reference contains four Latin tokens ("github", "latency", "auth", "gateway"); two match.
    assert english_token_recall(ref, hyp) == 0.5
    assert english_token_recall("شوفلي المشكلة دي", "شوفلي المشكلة دي") is None


def test_bootstrap_ci_deterministic_bounds() -> None:
    mean, lo, hi = bootstrap_ci([0.8, 0.9, 1.0, 0.85, 0.95], seed=42)
    assert lo <= mean <= hi
    assert (mean, lo, hi) == bootstrap_ci([0.8, 0.9, 1.0, 0.85, 0.95], seed=42)


def test_score_tool_calls_single_parallel_and_refusal() -> None:
    s1 = score_tool_calls(
        [{"name": "get_recent_incidents", "arguments": {"service": "auth-gateway"}}],
        [
            {
                "function": {
                    "name": "get_recent_incidents",
                    "arguments": '{"service": "Auth-Gateway"}',
                }
            }
        ],
    )
    assert s1 == {"tool_name_match": True, "exact_match": True, "arg_f1": 1.0}

    # VoiceAgentBench encodes arguments as single-element lists inside a dict keyed by tool name.
    s_vab = score_tool_calls(
        [{"find_restaurants": {"location": ["Bandra,Mumbai"], "number": ["5"]}}],
        [{"name": "find_restaurants", "arguments": '{"location": "Bandra,Mumbai", "number": "5"}'}],
    )
    assert s_vab == {"tool_name_match": True, "exact_match": True, "arg_f1": 1.0}

    s_safe = score_tool_calls([], [])
    assert s_safe["exact_match"] is True

    s_bad = score_tool_calls(
        [{"name": "get_ci_logs", "arguments": {"run_id": 8891}}],
        [{"name": "list_open_prs", "arguments": {"repo": "bejad21/sadaops-demo"}}],
    )
    assert s_bad["tool_name_match"] is False
    assert s_bad["exact_match"] is False
