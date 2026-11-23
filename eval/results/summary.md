# SadaOps Evaluation Report (Reproducible 3-Layer Summary)

## Layer 1: Bilingual Code-Switching STT (`MohamedRashad/arabic-english-code-switching`)

| Provider | Clips | WER (95% CI) | CER (95% CI) | EN Token Recall | Latency p50 / p95 |
|---|---:|---|---|---:|---|
| `speechmatics` | 300 | 0.478 (0.445–0.510) | 0.339 (0.311–0.368) | 20.7% (15.7%–25.8%) | 2032.0 ms / 4546.9 ms |
| `faster_whisper` | 300 | 0.791 (0.758–0.828) | 0.518 (0.487–0.551) | 11.2% (7.4%–15.4%) | 70679.5 ms / 106887.8 ms |

## Layer 2: Spoken Tool Calling (`krutrim-ai-labs/VoiceAgentBench`)

| Subset | Items | Tool Name Match (95% CI) | Exact Match | Arg F1 |
|---|---:|---|---|---:|
| `multi_turn` | 25 | 80.0% (64.0%–96.0%) | 40.0% (24.0%–60.0%) | 0.520 |
| `parallel_tool` | 25 | 56.0% (36.0%–76.0%) | 20.0% (8.0%–36.0%) | 0.630 |
| `safety` | 25 | 100.0% (100.0%–100.0%) | 100.0% (100.0%–100.0%) | 1.000 |
| `seqdep_tool` | 25 | 4.0% (0.0%–12.0%) | 0.0% (0.0%–0.0%) | 0.077 |
| `single_tool` | 25 | 84.0% (68.0%–96.0%) | 32.0% (12.0%–52.0%) | 0.632 |
| `single_tool_retrieval` | 25 | 84.0% (68.0%–96.0%) | 32.0% (16.0%–52.0%) | 0.533 |
| **Overall** | **150** | **68.0% (60.7%–75.3%)** | **37.3% (30.0%–44.7%)** | **0.565** |

## Layer 3: End-to-End Bilingual SadaOps Suite (4 Live MCP Servers + HITL + Security)

| Category | Scenarios | Pass Rate (95% CI) |
|---|---:|---|
| `ar_codeswitch` | 20 | 65.0% (45.0%–85.0%) |
| `barge_in` | 10 | 100.0% (100.0%–100.0%) |
| `en_multi_tool` | 20 | 90.0% (75.0%–100.0%) |
| `security` | 10 | 100.0% (100.0%–100.0%) |
| **Overall** | **60** | **85.0% (75.0%–93.3%)** |

## TTS Time-to-First-Byte (TTFB) Ablation (20 Bilingual Turns)

| TTS Provider | Turns | Mean TTFB (95% CI) | p50 / p95 TTFB |
|---|---:|---|---|
| `edge-tts` | 20 | 4134.4 ms (3092.9–5317.2 ms) | 2898.0 ms / 8094.8 ms |
| `cartesia-sonic-3` | 20 | 250.8 ms (196.0–348.5 ms) | 218.5 ms / 279.3 ms |
