"""Core evaluation metrics: bilingual normalization, WER/CER, tool matching, bootstrap CIs."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from typing import Any

import numpy as np

# Match Arabic diacritics (tashkeel) and elongation marks (tatweel).
_AR_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_PUNCT_RE = re.compile(r"[^\w\s\u0600-\u06FF]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
_LATIN_WORD_RE = re.compile(r"\b[a-z][a-z0-9_-]*\b")


def normalize_bilingual_text(text: str) -> str:
    """Normalize English and Arabic text fairly before WER/CER calculation.

    - Normalizes Unicode (NFKC) and lowercases Latin characters
    - Removes Arabic tashkeel/tatweel
    - Unifies Alef variants (أ/إ/آ -> ا), Alef Maqsura (ى -> ي), Ta Marbuta (ة -> ه)
    - Strips punctuation and collapses whitespace
    """
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text).lower()
    out = _AR_DIACRITICS_RE.sub("", out)
    out = (
        out.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
    )
    out = _PUNCT_RE.sub(" ", out)
    out = _WHITESPACE_RE.sub(" ", out).strip()
    return out


def _levenshtein(seq_a: list[str] | str, seq_b: list[str] | str) -> int:
    n, m = len(seq_a), len(seq_b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        ca = seq_a[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ca == seq_b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[m]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Compute normalized Word Error Rate (WER) on bilingual text."""
    ref_norm = normalize_bilingual_text(reference)
    hyp_norm = normalize_bilingual_text(hypothesis)
    ref_words = ref_norm.split()
    hyp_words = hyp_norm.split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return round(_levenshtein(ref_words, hyp_words) / len(ref_words), 4)


def char_error_rate(reference: str, hypothesis: str) -> float:
    """Compute normalized Character Error Rate (CER) on bilingual text."""
    ref_norm = normalize_bilingual_text(reference).replace(" ", "")
    hyp_norm = normalize_bilingual_text(hypothesis).replace(" ", "")
    if not ref_norm:
        return 0.0 if not hyp_norm else 1.0
    return round(_levenshtein(ref_norm, hyp_norm) / len(ref_norm), 4)


def english_token_recall(reference: str, hypothesis: str) -> float | None:
    """Measure recall of Latin/English words embedded inside a reference utterance.

    Returns None if the reference contains no English words.
    """
    ref_tokens = set(_LATIN_WORD_RE.findall(normalize_bilingual_text(reference)))
    if not ref_tokens:
        return None
    hyp_tokens = set(_LATIN_WORD_RE.findall(normalize_bilingual_text(hypothesis)))
    hits = len(ref_tokens & hyp_tokens)
    return round(hits / len(ref_tokens), 4)


def bootstrap_ci(
    values: list[float],
    *,
    n_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Return (mean, ci_low, ci_high) using deterministic percentile bootstrap."""
    if not values:
        return 0.0, 0.0, 0.0
    arr = np.asarray(values, dtype=float)
    mean = float(np.mean(arr))
    if len(arr) == 1:
        return round(mean, 4), round(mean, 4), round(mean, 4)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(arr), size=(n_resamples, len(arr)))
    samples = np.mean(arr[indices], axis=1)
    alpha = (1.0 - confidence) / 2.0
    low = float(np.quantile(samples, alpha))
    high = float(np.quantile(samples, 1.0 - alpha))
    return round(mean, 4), round(low, 4), round(high, 4)


def _normalize_call_list(raw: Any) -> list[tuple[str, dict[str, Any]]]:
    """Normalize various tool-call representations into [(name, args_dict), ...]."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    out: list[tuple[str, dict[str, Any]]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if "function" in item and isinstance(item["function"], dict):
            name = str(item["function"].get("name", ""))
            args = item["function"].get("arguments", {})
        elif "name" in item or "tool" in item:
            name = str(item.get("name") or item.get("tool") or "")
            args = item.get("arguments", item.get("args", {}))
        elif len(item) == 1:
            # VoiceAgentBench format: {"func_name": {"arg": ["val"]}}
            only_key, only_val = next(iter(item.items()))
            name = str(only_key)
            args = only_val if isinstance(only_val, dict) else {}
        else:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
        if name:
            norm_args = {
                str(k): _norm_val(v, unwrap_single=True)
                for k, v in args.items()
                if v is not None
            }
            out.append((name, norm_args))
    return out


def _norm_val(val: Any, unwrap_single: bool = False) -> Any:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("true", "false"):
            return s
        try:
            return float(s)
        except ValueError:
            return s
    if isinstance(val, list):
        if unwrap_single and len(val) == 1:
            return _norm_val(val[0], unwrap_single=True)
        return [_norm_val(v, unwrap_single=unwrap_single) for v in val]
    if isinstance(val, dict):
        return {str(k): _norm_val(v, unwrap_single=unwrap_single) for k, v in val.items()}
    return val


def score_tool_calls(expected: Any, predicted: Any) -> dict[str, Any]:
    """Compare expected vs predicted tool calls.

    Returns dict with `tool_name_match` (bool), `exact_match` (bool), and `arg_f1` (float).
    """
    exp_list = _normalize_call_list(expected)
    pred_list = _normalize_call_list(predicted)

    if not exp_list and not pred_list:
        return {"tool_name_match": True, "exact_match": True, "arg_f1": 1.0}
    if not exp_list or not pred_list:
        return {"tool_name_match": False, "exact_match": False, "arg_f1": 0.0}

    exp_names = sorted(n for n, _ in exp_list)
    pred_names = sorted(n for n, _ in pred_list)
    name_match = exp_names == pred_names

    exp_pairs = Counter(
        (n, k, json.dumps(v, sort_keys=True)) for n, a in exp_list for k, v in a.items()
    )
    pred_pairs = Counter(
        (n, k, json.dumps(v, sort_keys=True)) for n, a in pred_list for k, v in a.items()
    )

    if not exp_pairs and not pred_pairs:
        arg_f1 = 1.0 if name_match else 0.0
    elif not exp_pairs or not pred_pairs:
        arg_f1 = 0.0
    else:
        overlap = sum((exp_pairs & pred_pairs).values())
        prec = overlap / sum(pred_pairs.values())
        rec = overlap / sum(exp_pairs.values())
        arg_f1 = round((2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0, 4)

    exact = name_match and exp_pairs == pred_pairs
    return {"tool_name_match": name_match, "exact_match": exact, "arg_f1": arg_f1}
