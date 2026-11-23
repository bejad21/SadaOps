# Evaluation Methodology & Benchmark Analysis (`docs/evaluation.md`)

All metrics in [`eval/results/summary.md`](../eval/results/summary.md) are computed deterministically offline from saved JSONL outputs using [`eval/report.py`](../eval/report.py) and [`eval/metrics.py`](../eval/metrics.py) (`bootstrap_ci(..., n_resamples=2000, confidence=0.95, seed=42)`).

## 3-Layer Evaluation Design

1. **Layer 1 (`eval/asr_codeswitch/run_asr_eval.py`)**:
   - **Dataset:** 300 real wild Arabic-English code-switched clips streamed from `MohamedRashad/arabic-english-code-switching` (`0.4s`–`25s`, mono 16kHz PCM).
   - **Baseline Comparison:** Local `faster-whisper` (`small`, CPU `int8`) vs. Speechmatics Batch (`language="ar_en"`, `operating_point="enhanced"`).
   - **Result:** Speechmatics reduced WER from `0.791` to `0.478` (`-31.3` pts) and improved latency from `70.7s` p50 to `2.03s` p50. General-domain English token recall remains `20.7%` without session-specific vocabulary hints.

2. **Layer 2 (`eval/voice_agent_bench/run_vab_eval.py`)**:
   - **Dataset:** 150 stratified items (`25` per subset, `seed=42`) from `krutrim-ai-labs/VoiceAgentBench` across `single_tool`, `single_tool_retrieval`, `multi_turn`, `parallel_tool`, `seqdep_tool`, and `safety`.
   - **Pipeline:** Audio transcribed locally with `faster-whisper` (`base.en`), BFCL Pythonic schemas sanitized (`dict`/`list(str)`/`float` -> JSON Schema Draft 2020-12), executed against `openai/gpt-oss-20b` (`GROQ_API_KEY` + `GROQ_API_KEY_2` round-robin), `0` API errors.
   - **Ablation Impact (1-turn baseline -> parallel prompting + 3-step loop):**
     - Overall Tool Name Match: `58.0% -> 68.0%` (`Exact Match: 34.0% -> 37.3%`, `Arg F1: 0.533 -> 0.565`).
     - `parallel_tool`: `0.0% -> 56.0%` (`Arg F1: 0.464 -> 0.630`).
     - `seqdep_tool`: `0.0% -> 4.0%` strict (`9/25 = 36%` actually invoked prerequisite lookup tools and reached the terminal dependent tool).
     - `safety`: `100.0%` (`25/25` harmful spoken prompts refused without tool execution).

3. **Layer 3 (`eval/ops_scenarios/run_ops_eval.py`)**:
   - **Dataset:** 60 reproducible bilingual SadaOps scenarios (`20` EN multi-tool and `20` AR code-switching executing live audio -> STT -> Groq -> 4 MCP stdio servers; plus `10` deterministic `barge_in` harness probes and `10` deterministic `security` guardrail probes).
   - **Baseline (Speechmatics + CPU `faster-whisper` + Unscoped `20B`):**
     - `ar_codeswitch` (`live_audio_llm_mcp`): **65.0%** (`13/20`, 45.0%–85.0% CI, `2016.0 ms` STT p50).
     - `en_multi_tool` (`live_audio_llm_mcp`): **90.0%** (`18/20`, 75.0%–100.0% CI, `5453.5 ms` STT p50).
     - `barge_in`: **100.0%** (`10/10`) | `security`: **100.0%** (`10/10`).
     - **Overall Baseline:** **85.0% (`51/60`, 75.0%–93.3% CI)**. *(Previous pre-hardening `93.3%` baseline used `any()` on multi-tool cases and did not flag fixture error payloads.)*
   - **Upgraded Stack (`--upgraded`: Gladia `solaria-1` + Scoped `20B`/`120B` Supervisor + Connected Env Awareness -> [`eval/results/ops_scenarios_upgraded.jsonl`](../eval/results/ops_scenarios_upgraded.jsonl)):**
     - `ar_codeswitch`: **90.0%** (`18/20`, 75.0%–100.0% CI, `2531.0 ms` STT p50) — `+25.0` pts over baseline.
     - `en_multi_tool`: **100.0%** (`20/20`, 100.0%–100.0% CI, `2547.0 ms` STT p50) — **2.1x faster** English STT than baseline.
     - `barge_in`: **100.0%** (`10/10`) | `security`: **100.0%** (`10/10`).
     - **Overall Upgraded:** **96.7% (`58/60`, 91.7%–100.0% CI)** *(remaining 2 failures: synthetic `edge-tts` distortions in `ar-11` and `ar-12`)*.

4. **TTS TTFB Ablation (20 turns: 10 EN + 10 AR)**:
   - `edge-tts`: `4134.4 ms` mean TTFB (`2898.0 ms` p50 / `8094.8 ms` p95).
   - `cartesia-sonic-3`: `250.8 ms` mean TTFB (`218.5 ms` p50 / `279.3 ms` p95) — **16.5x faster** first-byte synthesis.

5. **Interactive Unit, Browser & Live WebRTC Verification**:
   - Python unit/integration/security suite (`pytest -v`): **103 passed** (API, inline HITL Postgres/Mailpit, Google OAuth & Gmail/Calendar, GitHub multi-repo/commits/CI, orchestrator routing, adapter turn handling, concurrency/barge-in, 20 red-team probes, SQL AST guard, all MCP `ToolAnnotations`, and eval metrics).
   - Frontend (`cd web && npm test -- --run && npx playwright test`): **11** Vitest unit tests + Playwright live Cockpit E2E passing against Postgres & Mailpit.
   - Live sequential 3-scenario WebRTC browser suite (single persistent worker): EN multi-tool voice approval, AR/EN RTL voice rejection, and UI text chat + inline HITL edit + Mailpit delivery verified end-to-end.

## Reproducing Offline Report
```bash
python -m eval.ops_scenarios.run_ops_eval --upgraded --skip-tts-ablation
python -m eval.report
```
