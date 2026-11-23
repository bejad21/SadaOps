"""Offline reproducible reporter generating summary tables + 95% bootstrap CIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from eval.metrics import bootstrap_ci

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "eval" / "results"
SUMMARY_PATH = RESULTS_DIR / "summary.md"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _fmt_ci(ci: tuple[float, float, float], pct: bool = False) -> str:
    m, lo, hi = ci
    if pct:
        return f"{m * 100:.1f}% ({lo * 100:.1f}%–{hi * 100:.1f}%)"
    return f"{m:.3f} ({lo:.3f}–{hi:.3f})"


def _pcts(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    arr = np.asarray(values, dtype=float)
    return round(float(np.percentile(arr, 50)), 1), round(float(np.percentile(arr, 95)), 1)


def generate_report() -> str:
    """Read saved Layer 1-3 JSONL results and build Markdown summary tables."""
    lines: list[str] = ["# SadaOps Evaluation Report (Reproducible 3-Layer Summary)\n"]

    # Layer 1: Bilingual Code-Switching ASR
    asr_rows = _read_jsonl(RESULTS_DIR / "asr_codeswitch.jsonl")
    if asr_rows:
        lines.append(
            "## Layer 1: Bilingual Code-Switching STT "
            "(`MohamedRashad/arabic-english-code-switching`)\n"
        )
        lines.append(
            "| Provider | Clips | WER (95% CI) | CER (95% CI) | "
            "EN Token Recall | Latency p50 / p95 |"
        )
        lines.append("|---|---:|---|---|---:|---|")
        for provider in ("speechmatics", "faster_whisper"):
            valid = [
                r[provider]
                for r in asr_rows
                if isinstance(r.get(provider), dict) and "wer" in r[provider]
            ]
            if not valid:
                continue
            wer_ci = bootstrap_ci([v["wer"] for v in valid])
            cer_ci = bootstrap_ci([v["cer"] for v in valid])
            recalls = [v["en_recall"] for v in valid if v.get("en_recall") is not None]
            rec_ci = bootstrap_ci(recalls) if recalls else (0.0, 0.0, 0.0)
            p50, p95 = _pcts([v["latency_ms"] for v in valid])
            lines.append(
                f"| `{provider}` | {len(valid)} | {_fmt_ci(wer_ci)} | {_fmt_ci(cer_ci)} | "
                f"{_fmt_ci(rec_ci, pct=True)} | {p50} ms / {p95} ms |"
            )
        lines.append("")

    # Layer 2: VoiceAgentBench
    vab_rows = _read_jsonl(RESULTS_DIR / "voice_agent_bench.jsonl")
    if vab_rows:
        lines.append("## Layer 2: Spoken Tool Calling (`krutrim-ai-labs/VoiceAgentBench`)\n")
        lines.append("| Subset | Items | Tool Name Match (95% CI) | Exact Match | Arg F1 |")
        lines.append("|---|---:|---|---|---:|")
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for r in vab_rows:
            by_cat.setdefault(str(r.get("category", "unknown")), []).append(r)
        for cat, items in sorted(by_cat.items()):
            tm = bootstrap_ci([1.0 if i.get("tool_name_match") else 0.0 for i in items])
            em = bootstrap_ci([1.0 if i.get("exact_match") else 0.0 for i in items])
            af1 = bootstrap_ci([float(i.get("arg_f1", 0.0)) for i in items])
            lines.append(
                f"| `{cat}` | {len(items)} | {_fmt_ci(tm, pct=True)} | "
                f"{_fmt_ci(em, pct=True)} | {af1[0]:.3f} |"
            )
        tm_all = bootstrap_ci([1.0 if i.get("tool_name_match") else 0.0 for i in vab_rows])
        em_all = bootstrap_ci([1.0 if i.get("exact_match") else 0.0 for i in vab_rows])
        af1_all = bootstrap_ci([float(i.get("arg_f1", 0.0)) for i in vab_rows])
        lines.append(
            f"| **Overall** | **{len(vab_rows)}** | **{_fmt_ci(tm_all, pct=True)}** | "
            f"**{_fmt_ci(em_all, pct=True)}** | **{af1_all[0]:.3f}** |"
        )
        lines.append("")

    # Layer 3: 60 E2E Scenarios
    ops_rows = _read_jsonl(RESULTS_DIR / "ops_scenarios.jsonl")
    if ops_rows:
        lines.append(
            "## Layer 3: End-to-End Bilingual SadaOps Suite "
            "(4 Live MCP Servers + HITL + Security)\n"
        )
        lines.append("| Category | Scenarios | Pass Rate (95% CI) |")
        lines.append("|---|---:|---|")
        by_cat_ops: dict[str, list[dict[str, Any]]] = {}
        for r in ops_rows:
            by_cat_ops.setdefault(str(r.get("category", "unknown")), []).append(r)
        for cat, items in sorted(by_cat_ops.items()):
            pr = bootstrap_ci([1.0 if i.get("passed") else 0.0 for i in items])
            lines.append(f"| `{cat}` | {len(items)} | {_fmt_ci(pr, pct=True)} |")
        pr_all = bootstrap_ci([1.0 if i.get("passed") else 0.0 for i in ops_rows])
        lines.append(f"| **Overall** | **{len(ops_rows)}** | **{_fmt_ci(pr_all, pct=True)}** |")
        lines.append("")

    # TTS Latency Ablation
    tts_path = RESULTS_DIR / "tts_latency.json"
    if tts_path.exists():
        tts_data = json.loads(tts_path.read_text(encoding="utf-8"))
        lines.append("## TTS Time-to-First-Byte (TTFB) Ablation (20 Bilingual Turns)\n")
        lines.append("| TTS Provider | Turns | Mean TTFB (95% CI) | p50 / p95 TTFB |")
        lines.append("|---|---:|---|---|")
        for label, vals, ci in (
            ("edge-tts", tts_data.get("edge_tts_ttfb_ms", []), tts_data.get("edge_tts_ci")),
            ("cartesia-sonic-3", tts_data.get("cartesia_ttfb_ms", []), tts_data.get("cartesia_ci")),
        ):
            if vals and ci:
                p50, p95 = _pcts(vals)
                lines.append(
                    f"| `{label}` | {len(vals)} | {ci[0]:.1f} ms ({ci[1]:.1f}–{ci[2]:.1f} ms) | "
                    f"{p50} ms / {p95} ms |"
                )
        lines.append("")

    report_text = "\n".join(lines)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(report_text, encoding="utf-8")
    return report_text


if __name__ == "__main__":
    print(generate_report())
