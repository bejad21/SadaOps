# Security Policy & Subprocessor Disclosure

## Supported Scope
SadaOps executes a defense-in-depth guardrail pipeline across voice STT, LLM planning, four decoupled FastMCP tool servers, PostgreSQL persistence, and spoken TTS output. For full technical details see [docs/security.md](docs/security.md).

## Third-Party Subprocessors
When configured with live API keys in local `.env` (never committed to source control), audio and text leave the local Docker environment only to the following providers:

| Subprocessor | Purpose | Data Sent | Local / Offline Fallback |
|---|---|---|---|
| **Gladia** (`solaria-1`, default) / **Speechmatics** (`ar_en` / `multi`) | Bilingual Arabic + English streaming & batch STT | 16 kHz PCM audio frames + static `SADAOPS_ADDITIONAL_VOCAB` / `GLADIA_CUSTOM_SPELLING` hints | `faster-whisper` (`small` / `base.en`) locally on CPU (plus Groq `whisper-large-v3-turbo` fallback) |
| **Groq** (`openai/gpt-oss-20b` fast, `120b` Supervisor) | Scoped domain & multi-step tool planning + bilingual responses | Text transcripts, connected schema hints, scoped tool schemas, bounded tool results | Any OpenAI-compatible local endpoint |
| **Cartesia** (`sonic-3`) | Low-latency bilingual TTS (`250.8 ms` mean TTFB) | Post-scrubbing spoken text (secrets redacted before synthesis) | `edge-tts` (`en-US-AndrewMultilingualNeural` / `ar-EG-SalmaNeural`) |
| **GitHub REST API** *(optional, off by default)* | Live PRs, CI run logs, issue lookup/creation when `GITHUB_MODE=live` | Repo identifier (`resolve_connected_repo`) & approved issue payload | Offline `FixtureGitHubAdapter` (`GITHUB_MODE=fixture`) |

**Local-only components & data stores** (never shared externally): LiveKit `TurnDetector(version="v1-mini")` + Silero VAD (local ONNX inference), PostgreSQL (`ops_incidents`, `service_metrics`, `deployments`, `calendar_events`, `pending_actions`), Qdrant (`sadaops_runbooks`), Mailpit SMTP sandbox, Jaeger OTLP traces, and `--network=none` Python Docker sandbox (`sadaops-python-sandbox`).

## Controls Summary
- **Least-Privilege SQL (`ops_readonly`) & AST Firewall:** `sqlglot` rejects multi-statement queries, non-`SELECT` AST nodes, unauthorized tables (`pending_actions`, `calendar_events`, `pg_*`), dangerous functions (`pg_sleep`, `pg_read_file`), and enforces `LIMIT <= 50`.
- **Parameter-Locked HITL Gate & Inline Re-Lock:** External write tools (`send_email`, `create_calendar_event`, `create_issue`) carry MCP `ToolAnnotations`, lock a SHA-256 hash (`compute_payload_hash`) inside PostgreSQL `pending_actions` with single-use row locking (`SELECT ... FOR UPDATE`), support atomic operator edits via `update_pending_payload()` (`/api/hitl/update` + RPC) with full guardrail re-validation & hash re-lock, enforce 120-second default TTL expiry, reject explicit external `owner/repo` exfiltration slugs (`attacker/exfil-repo`), and require out-of-band cross-turn voice or UI confirmation.
- **Untrusted Data Framing:** External tool outputs (`get_issue_details`, `get_ci_logs`, `search_inbox`, `search_runbooks`) are wrapped with `untrusted_external_data=True` and scanned for English & Arabic prompt-injection patterns.
- **TTS Secret Scrubber (`ScrubbedTTS`):** Redacts PEM keys, GitHub/Slack/API tokens, JWTs, Bearer headers, DSN & HTTPS userinfo credentials, `key=secret` assignments, and Shannon high-entropy tokens (`>= 4.0` bits/char) before audio synthesis and DataChannel transcript publishing.

## Reporting a Vulnerability & Secret Hygiene
- `.env`, `.env.*.local`, `credentials.json`, `eval/cache/`, and binary audio/dataset artifacts are excluded via `.gitignore`.
- Never paste API keys into GitHub issues, pull requests, logs, or audio benchmarks. Report security issues privately to the repository maintainer before public disclosure.
