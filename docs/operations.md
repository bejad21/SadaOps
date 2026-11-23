# Operations Guide & Engineering Postmortems (`docs/operations.md`)

## Local Service Ports & Health Checks

| Service | Container / Command | URL / Port | Purpose |
|---|---|---|---|
| LiveKit SFU | `sadaops-livekit` | `ws://localhost:7880` | WebRTC SFU + DataChannels |
| PostgreSQL 16 | `sadaops-postgres` | `localhost:5432` | Ops tables, Calendar, HITL `pending_actions`, `ops_readonly` role |
| Qdrant | `sadaops-qdrant` | `http://localhost:6333` | Bilingual runbook embeddings (`sadaops_runbooks`) |
| Jaeger OTLP | `sadaops-jaeger` | `:4318` (OTLP), `http://localhost:16686` (UI) | Turn waterfall traces (`sadaops-agent`) |
| Mailpit | `sadaops-mailpit` | `:1025` (SMTP), `http://localhost:8025` (UI) | Local safe email capture |
| FastAPI API | `uvicorn api.app:app --port 8000` | `http://localhost:8000/health` | Token minting + HITL endpoints |
| React Cockpit | `cd web && npm run dev` | `http://localhost:5173` | EN/AR RTL operator console |

## Live Integrations Configuration

- **GitHub Multi-Repo Mode (`mcp_servers/github_mcp`)**: Set `GITHUB_MODE=live`, `GITHUB_TOKEN` (with `repo` scope for private repos), and `GITHUB_ALLOWED_REPOS=*` (with `GITHUB_REPO=` empty) to list all accessible repositories (`list_repos`), inspect commits (`list_commits`), inspect recent workflow runs and failing job/step logs (`get_ci_logs`), resolve short repo names against `/user/repos`, and propose HITL-gated `create_issue` actions across any accessible repo.
- **Google Workspace (`mcp_servers/workspace_mcp`)**: Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env` and connect via the Cockpit UI or `/api/integrations/google/auth-url`. Supports all-time exact Gmail `INBOX` / `UNREAD` counts (`since_hours=0`), top-sender analytics (`analyze_inbox`), reading email bodies (`read_email`), and listing/creating/updating/deleting Calendar events (with `ALLOWED_EMAIL_DOMAINS` env override for external recipients).
- **Restart Note**: After changing `.env` integration variables, restart both `uvicorn api.app:app` and `python -m agent.main dev` so both the control plane and voice worker load the updated environment.

---

## Real Bugs Found & Fixed During Build & Benchmarking

1. **Speechmatics `agent_stt` Schema vs. `/v2` & Batch APIs (`Phase 4`)**
   - *Symptom:* `speechmatics.agent_stt` rejected `StartRecognition` payloads even on plain `"en"`.
   - *Root Cause & Fix:* Switched offline eval transcription to Speechmatics Batch REST API (`POST /v2/jobs`, `language="ar_en"`, `operating_point="enhanced"`), achieving `2.03s` p50 latency across 300 real clips, added `SADAOPS_ADDITIONAL_VOCAB` (`27` domain entries) so Arabic sentences retain Latin DevOps identifiers (`Runbooks`, `Redis`, `CI`, `Sev1`), and set `vad=None` in live `speechmatics.STT` to eliminate duplicate Silero VAD instances and enable `manual_flush`.

2. **Groq Free-Tier `8,000` TPM Saturation (`Phase 4` & Audit Remediation)**
   - *Symptom:* Multi-turn tool chains (`2–3` turns per scenario with full JSON tool schemas and `1500`-char payloads) triggered `429 Too Many Requests` (`llm_ttft_ms` spiked up to `707s` during unbounded `Retry-After` waits).
   - *Root Cause & Fix:* Added `load_groq_api_keys()`, multi-key round-robin scheduling, zero-sleep initial `429` cross-key failover, and a strict `max_backoff_s = 5.0` cap in [`agent/pipeline/llm_router.py`](../agent/pipeline/llm_router.py), wired LiveKit `FallbackAdapter` in [`agent/main.py`](../agent/main.py), and trimmed eval payloads (`[:450]` chars) — eliminating all `429` stalls across the 60-scenario Layer 3 run (max `484 ms` TTFT) and 150 `VoiceAgentBench` runs (`0` API errors).

3. **BFCL / `VoiceAgentBench` Non-Standard JSON Schema Types (`Phase 4`)**
   - *Symptom:* Groq returned `400 Bad Request` complaining parameter types did not match Draft 2020-12 JSON Schema (e.g., `QueryMeeting.attendees` had `"type": "list(str)"`, plus `"dict"`, `"int"`, `"float"`, `"str"`, `"optional"`, and dotted names like `"flight.book"`).
   - *Root Cause & Fix:* Built `_sanitize_json_schema()` and dotted-name reversible mapping (`__` <-> `.`) in [`eval/voice_agent_bench/run_vab_eval.py`](../eval/voice_agent_bench/run_vab_eval.py), achieving `0` schema/HTTP errors across all `150` items.

4. **MCP 2.3.0 AnyIO Task-Scope Constraint (`Phase 2`)**
   - *Symptom:* Exiting `stdio_client` across different coroutines triggered AnyIO cancel-scope RuntimeErrors.
   - *Root Cause & Fix:* Updated `MCPServerPool` in [`agent/pipeline/tool_router.py`](../agent/pipeline/tool_router.py) to host each `MCPServer` inside a dedicated `asyncio.Task` so enter/exit stay bound to one task, and normalized `CallToolResult.structured_content["result"]`.

5. **`email-validator` `.local` TLD Rejection (`Phase 2–3`)**
   - *Symptom:* Pydantic `EmailStr` rejected fixture/test addresses ending in `.local` (`EmailUndeliverableError`).
   - *Root Cause & Fix:* Standardized internal fixtures and test policies on RFC-reserved `.example` / `example.com` domains (`DEFAULT_ALLOWED_EMAIL_DOMAINS`).

6. **Windows Host CPU Spikes Blocking Sequential Worker Dispatch (`Post-Upgrade E2E`)**
   - *Symptom:* Even with `load_threshold=float("inf")` on `AgentServer`, LiveKit Go Server rejected every other WebRTC room (`no servers available`) whenever local Windows CPU load (`_DefaultLoadCalc.get_load`) crossed `0.7`.
   - *Root Cause & Fix:* Replaced default host-CPU `load_fnc` with job-count saturation `_worker_load` in [`agent/main.py`](../agent/main.py), prewarmed heavy SSL/OpenAI/JSONSchema imports in `prewarm()`, and added `@session.on("close")` immediate room shutdown so all 4 MCP subprocesses tear down on client disconnect instead of lingering for LiveKit's 20s departure timeout.

7. **LiveKit Cloud Turn-Model `401` & Groq Qwen OTPM `429` (`Upgrade 2–3`)**
   - *Symptom:* Default `MultilingualModel()` hit LiveKit Inference Cloud (`401 Unauthorized` without cloud credentials), and switching `LLM_FALLBACK_MODEL` to `qwen/qwen3.8-27b` exceeded Groq's `1000` OTPM cap when `max_completion_tokens` was unset.
   - *Root Cause & Fix:* Switched `build_turn_handling()` to offline local `inference.TurnDetector(version="v1-mini")` (`python -m livekit.agents download-files`), restored `LLM_FALLBACK_MODEL=openai/gpt-oss-120b`, and set `max_completion_tokens=600` on live `groq.LLM` instances.

8. **Split Gladia Transcripts Dropping Multi-Domain Tool Scopes (`Upgrade 1–3`)**
   - *Symptom:* LiveKit `on_user_turn_completed` fired before a late trailing STT fragment (`"Example.com, summarizing the incident."`) arrived, narrowing the second route to `ops_db` only; similarly, `gladia.STT.recognize()` placed sequential utterances across `ev.alternatives[0..n]`.
   - *Root Cause & Fix:* Raised dynamic `min_delay` to `1.0s`, updated `SadaOpsAgent.on_user_turn_completed()` to combine consecutive unanswered user messages until an assistant reply finishes, and joined all `ev.alternatives` segments in [`eval/ops_scenarios/run_ops_eval.py`](../eval/ops_scenarios/run_ops_eval.py).

9. **Unqualified ASR Repo Names (`"Bajad"`) & Guessed SQL Column Names (`Layer 3 Hardening`)**
   - *Symptom:* Gladia transcribed `"bejad21/sadaops-demo"` as `"Bajad"`, causing GitHub repo validation failures, and `run_readonly_sql` guessed non-existent columns (`deployment_time`, `incident_id`).
   - *Root Cause & Fix:* Added `resolve_connected_repo()` in [`agent/safety/prompt_guard.py`](../agent/safety/prompt_guard.py) (resolving empty or single-token ASR repo strings to `bejad21/sadaops-demo` while keeping explicit external `owner/repo` exfiltration slugs strictly blocked), expanded `GLADIA_CUSTOM_SPELLING` and phonetic EN/AR patterns in [`agent/pipeline/orchestrator.py`](../agent/pipeline/orchestrator.py), and documented the exact connected GitHub repo + PostgreSQL schemas in [`mcp_servers/ops_db_mcp/server.py`](../mcp_servers/ops_db_mcp/server.py) and `SYSTEM_INSTRUCTIONS` — raising Layer 3 accuracy from `85.0%` to **`96.7%`** (`58/60`).
