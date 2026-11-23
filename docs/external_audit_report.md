# SadaOps Independent Adversarial Code Review & Architecture Audit

- **Repository:** `C:\Users\bigoa\.gemini\antigravity\scratch\SadaOps`
- **Audit Scope:** Line-by-line inspection of all source, config, test, evaluation, infrastructure, CI, and documentation files (`agent/`, `api/`, `mcp_servers/`, `web/`, `eval/`, `infra/`, `.github/workflows/ci.yml`, `pyproject.toml`, `requirements.txt`, `README.md`, `SECURITY.md`, `docs/*`) plus read-only runtime verification against the project `.venv` (`livekit-agents==1.8.13`, `livekit-plugins-speechmatics==1.8.13`, `mcp==2.3.0`, `sqlglot==30.33.0`).
- **Constraints Observed:** Strictly read-only audit; zero existing project files modified; `.env` was never read, printed, or referenced.
- **Publish Readiness Verdict:** **NOT READY TO PUBLISH AS-IS.** While the modular 4-MCP separation, PostgreSQL row-locked HITL design, Docker sandbox flags, and React 19 RTL cockpit are well structured, **6 Critical blockers** prevent the live voice pipeline from executing tools or exporting telemetry, create a complete single-turn HITL self-approval bypass, drop MCP configuration env vars, allow SQL AST firewall bypasses, and guarantee CI failure on push.

---

## Summary Matrix

| Severity | Count | Key Themes |
|---|---:|---|
| **Critical** | 6 | Live MCP pool immediately closed after `session.start()`; LLM self-approval HITL bypass & broken `classify_voice_decision`; MCP stdio env stripping; `ScrubbedTTS` event drop & streaming buffering; `sql_guard.py` XML/function bypasses; CI missing Postgres service |
| **Normal** | 14 | Live `GroqLLMRouter` bypass + unbounded `Retry-After`; inflated/flawed Layer 3 eval; filler chat context pollution; duplicate Speechmatics VAD; `TurnManager` / `CancelledError` wiring gaps; UI HITL fixture/failure gaps; blocking sync DB in event loop; write-policy & GitHub URL gaps; Workspace query/race/seed gaps; waterfall math omitting MCP time; DataChannel transcript secret leak & scrubber gaps; cross-platform/dependency gaps; Docker/Qdrant leaks & races; React multi-session/security gaps |
| **Low** | 4 | Docker `0.0.0.0` bind & `:latest` tags; Cartesia hardcoded AR voice; docs/config drift (TTL, filler delay, vocab count, fallback name, Mermaid, unused JWT metadata); dead code & minor hygiene |

---

## Critical Findings

### CRIT-1: `agent/main.py` shuts down all 4 MCP servers immediately after `session.start()` returns — every MCP tool is broken in live voice sessions
- **File & Lines:** `agent/main.py:240-244, 400-411` and `agent/pipeline/tool_router.py:140-152`
- **Symbol / Code Quote:**
  ```python
  # agent/main.py:240-244
  mcp_pool = MCPServerPool()
  await mcp_pool.start()

  try:
      await ctx.connect()
  ...
  # agent/main.py:400-411
      await session.start(
          room=ctx.room,
          agent=SadaOpsAgent(
              router=router,
              get_context_state=_get_context_state,
              hitl_gate=hitl_gate,
              mcp_caller=mcp_pool.call_tool,
              emit_event=_emit_dc,
          ),
      )
  finally:
      await mcp_pool.close()
  ```
- **Root-Cause Reasoning:** Verified in `livekit-agents==1.8.13` (`livekit.agents.voice.agent_session.AgentSession.start`): when `capture_run=False` (the default), `await session.start(...)` starts background audio/activity tasks, registers shutdown hooks via `job_ctx.add_shutdown_callback(...)`, sets `self._started = True`, and **returns `None` immediately** rather than blocking for the lifetime of the WebRTC call. Control falls straight into `finally: await mcp_pool.close()`, which sets all stop events, terminates all 4 MCP stdio subprocesses, and executes `self._sessions.clear()` (`agent/pipeline/tool_router.py:147`). For the entire live voice session, every tool call hits `MCPServerPool.call_tool()` and immediately fails with `KeyError("Unknown or uninitialized MCP tool: ...")`. This went undetected because no live human microphone LiveKit session was tested and unit/eval suites invoke `MCPServerPool` directly.
- **How to Reproduce / Verify:**
  1. Inspect `AgentSession.start` in `.venv` (`capture_run: bool = False` returns `run_state` which is `None` without awaiting room disconnect).
  2. Start `uvicorn api.app:app`, `python -m agent.main dev`, and `web`; connect and speak any tool query (e.g., `"Check sev1 incidents"`). `ToolExecutionPanel` displays `status: error` with `Unknown or uninitialized MCP tool: get_recent_incidents`.
- **Recommended Fix:** Register MCP cleanup on LiveKit's job shutdown lifecycle instead of a `try/finally` around `session.start()`:
  ```python
  mcp_pool = MCPServerPool()
  await mcp_pool.start()
  ctx.add_shutdown_callback(mcp_pool.close)
  try:
      await ctx.connect()
      ...
      await session.start(...)
  except BaseException:
      await mcp_pool.close()
      raise
  ```

---

### CRIT-2: Complete HITL security bypass via LLM-callable `confirm_pending_action`, plus dead & broken `classify_voice_decision()`
- **File & Lines:** `agent/main.py:194-223`, `agent/safety/hitl_gate.py:21-51, 97-106`, `agent/main.py:264-273`
- **Symbol / Code Quote:**
  ```python
  # agent/main.py:194-205
  @function_tool
  async def confirm_pending_action(self, ctx: RunContext, action_id: str) -> str:
      """Execute a previously locked HITL pending action after explicit operator approval."""
      pending = self._hitl_gate.get_action(action_id)
      if pending is None:
          return json.dumps({"error": f"Action '{action_id}' not found."})
      try:
          executed = await self._hitl_gate.approve_and_execute(
              action_id,
              pending.payload_hash,
              self._mcp_caller,
          )
  ```
  ```python
  # agent/safety/hitl_gate.py:97-106
  def classify_voice_decision(text: str) -> Literal["approve", "reject", "none"]:
      """Classify short bilingual operator confirmation or rejection utterances."""
      cleaned = (text or "").strip().lower()
      if not cleaned:
          return "none"
      if any(phrase in cleaned for phrase in _REJECT_PHRASES):
          return "reject"
      if any(phrase in cleaned for phrase in _APPROVE_PHRASES):
          return "approve"
      return "none"
  ```
- **Root-Cause Reasoning:**
  1. **Single-turn autonomous self-approval:** `confirm_pending_action` is registered as an LLM `@function_tool`. `AgentSession` defaults to `max_tool_steps=3` (`livekit-agents==1.8.13`). In a single turn with **zero operator confirmation**, an indirect prompt injection (inside a GitHub issue, CI log, email, or runbook) or an over-eager model can: (step 1) read untrusted input, (step 2) call `send_email` / `create_issue` / `create_calendar_event` to an allowlisted domain (`ops-leads@example.com`, `bejad21/sadaops-demo`), obtaining `action_id` from the tool result, and (step 3) immediately call `confirm_pending_action(action_id)` to execute the action autonomously.
  2. **Zero verification inside `confirm_pending_action`:** It never checks `pending.session_id == self._router.session_id` (allowing cross-session execution), never checks that a new operator turn occurred after `pending.created_at`, never checks `classify_voice_decision(last_user_text)`, and reads `pending.payload_hash` straight from PostgreSQL itself rather than requiring an externally held hash.
  3. **Dead & broken voice classifier:** `classify_voice_decision()` is never imported or called anywhere outside `agent/tests/test_security_phase3.py`. Worse, it uses naive substring checks (`phrase in cleaned`), where `"no"` matches `"now"`, `"know"`, `"note"`, `"yes"` matches `"eyes"`, and Arabic `"لا"` matches `"خلال"`, `"بلاغ"`, `"سلام"`. Because `_REJECT_PHRASES` runs first, `"Yes, send it now"`, `"Confirm it now"`, and `"تمام خلال دقيقة ابعته"` all classify as `"reject"`, while `"eyes on the dashboard"` classifies as `"approve"`.
- **How to Reproduce / Verify:**
  - Call `SadaOpsAgent.send_email(...)` followed immediately by `SadaOpsAgent.confirm_pending_action(action_id)` without any user speech between calls; the email executes.
  - Run `python -c "from agent.safety.hitl_gate import classify_voice_decision; print([(s, classify_voice_decision(s)) for s in ['Yes, send it now', 'تمام خلال دقيقة ابعته', 'eyes on the dashboard']])"`:
    - `'Yes, send it now' -> 'reject'`
    - `'تمام خلال دقيقة ابعته' -> 'reject'`
    - `'eyes on the dashboard' -> 'approve'`
- **Recommended Fix:**
  - Remove `confirm_pending_action` and `reject_pending_action` as LLM-callable `@function_tool`s completely (or enforce a code-level turn barrier outside LLM tool execution).
  - Handle voice confirmations deterministically in `_on_user_transcript` **outside** the LLM tool loop: only when `session_id` has an active pending action whose `created_turn_id != current_turn_id`, match `text` using word-boundary regexes (checking multi-word negations like `"don't send"` / `"مش موافق"` before single-word approvals), execute via `approve_and_execute` or `reject_action`, emit `hitl_resolved`, and inject the outcome into chat context. For high-risk writes, consider requiring authenticated UI approval only.

---

### CRIT-3: `MCPServerPool` strips all custom environment variables from all 4 MCP child processes (`GITHUB_MODE=live`, `GITHUB_TOKEN`, `DATABASE_URL`, `QDRANT_URL`, `MAILPIT_*` silently dropped)
- **File & Lines:** `agent/pipeline/tool_router.py:104-108`, `mcp_servers/ops_db_mcp/server.py:11-12`, `mcp_servers/github_mcp/server.py:11-12`, `mcp_servers/workspace_mcp/server.py:15-16`, `mcp_servers/knowledge_sandbox_mcp/server.py:11-12`
- **Symbol / Code Quote:**
  ```python
  # agent/pipeline/tool_router.py:104-108
  params = StdioServerParameters(
      command=self.python_executable,
      args=["-m", module],
      cwd=str(REPO_ROOT),
  )
  ```
- **Root-Cause Reasoning:** Verified in `mcp==2.3.0` (`mcp.client.stdio.stdio_client` and `get_default_environment()`): the MCP SDK starts subprocesses with `env=get_default_environment() | (server.env or {})`. On Windows, `DEFAULT_INHERITED_ENV_VARS` only allows `['APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'LOCALAPPDATA', 'PATH', 'PATHEXT', 'PROCESSOR_ARCHITECTURE', 'SYSTEMDRIVE', 'SYSTEMROOT', 'TEMP', 'USERNAME', 'USERPROFILE']` (and similarly minimal baseline vars on Linux/macOS). Because `StdioServerParameters` is constructed with `env=None` and none of the 4 MCP `server.py` files call `load_dotenv()`, **all `.env` / shell variables are stripped** before the child Python process starts. As a result:
  - `GITHUB_MODE=live`, `GITHUB_TOKEN`, and `GITHUB_REPO` are wiped, so `github_mcp` silently falls back to `FixtureGitHubAdapter` (`mode: "fixture"`) even when configured for live GitHub.
  - Custom `DATABASE_URL`, `OPS_READONLY_DATABASE_URL`, `QDRANT_URL`, `MAILPIT_SMTP_HOST`, `MAILPIT_SMTP_PORT`, `MAILPIT_API_URL`, and `DOCKER_HOST` are wiped, silently falling back to `localhost` defaults.
- **How to Reproduce / Verify:** Set `GITHUB_MODE=live` and `GITHUB_TOKEN=test` in the parent process, start `MCPServerPool()`, and invoke `pool.call_tool("create_issue", {"title": "Test", "body": "Test"})`: it still uses `FixtureGitHubAdapter` and returns `"mode": "fixture"`.
- **Recommended Fix:** Pass an explicit, least-privilege per-server `env` dictionary into `StdioServerParameters(..., env=...)` inside `MCPServerPool._run_server_session` (forwarding only the env vars each domain needs — e.g. `OPS_READONLY_DATABASE_URL` to `ops_db`, `GITHUB_*` to `github`, `DATABASE_URL`/`MAILPIT_*` to `workspace`, `QDRANT_URL`/`DOCKER_HOST`/`HF_TOKEN` to `knowledge`, while keeping `GROQ_API_KEY`, `SPEECHMATICS_API_KEY`, `CARTESIA_API_KEY`, and `LIVEKIT_API_SECRET` isolated from MCP subprocesses). Also call `load_dotenv()` in each server's `if __name__ == "__main__":` entrypoint for standalone CLI use.

---

### CRIT-4: `ScrubbedTTS` drops LiveKit `metrics_collected` / `error` events (breaking Jaeger spans & React Latency Waterfall Panel) AND kills Cartesia streaming TTFB
- **File & Lines:** `agent/pipeline/tts_adapter.py:86-179`, `agent/main.py:308-314, 394-398`
- **Symbol / Code Quote:**
  ```python
  # agent/pipeline/tts_adapter.py:94-115
  class _ScrubbedSynthesizeStream:
      def push_text(self, token: str) -> None:
          if token:
              self._buffer.append(token)
      ...
      def flush(self) -> None:
          self._drain_scrubbed()
          self._inner.flush()

      def end_input(self) -> None:
          self._drain_scrubbed()
          self._inner.end_input()
  ```
  ```python
  # agent/main.py:310-313
  if active_record.t_tts_ttfb_ms is not None or active_record.interrupted:
      export_turn_span(tracer, active_record)
      asyncio.create_task(_publish_event(ctx, active_record.to_waterfall_dict()))
      active_record = None
  ```
- **Root-Cause Reasoning:** Verified against `livekit-agents==1.8.13` (`AgentActivity`, `tts.ChunkedStream`, `tts.SynthesizeStream`, `tts.StreamAdapter`, `Agent.default.tts_node`):
  1. **Swallowed `metrics_collected` and `error` events:** `AgentActivity` subscribes to `self.tts.on("metrics_collected", self._on_metrics_collected)` and `self.tts.on("error", self._on_error)` on the outer `ScrubbedTTS` instance. However, `ChunkedStream` and `SynthesizeStream` emit those events on `self._tts` (which is `self.inner`, e.g., `cartesia.TTS` or `EdgeTTS`). Unlike LiveKit's own `tts.StreamAdapter.__init__`, `ScrubbedTTS.__init__` never wires `self.inner.on("metrics_collected", ...)` or `self.inner.on("error", ...)` to `self.emit(...)`. Consequently, `AgentSession` never receives `TTSMetrics`. Because `agent/main.py:310` gates Jaeger span export and DataChannel waterfall publication on `if active_record.t_tts_ttfb_ms is not None or active_record.interrupted:`, **every normal (uninterrupted) live turn produces 0 Jaeger spans and 0 UI latency waterfall cards**, and `active_record` is never cleared at turn end.
  2. **Broken streaming TTS latency:** `Agent.default.tts_node` pushes LLM text chunks via `stream.push_text(chunk)` and calls `stream.end_input()` only after the full LLM response finishes (never calling `stream.flush()` mid-stream). Because `_ScrubbedSynthesizeStream.push_text()` buffers every token until `flush()` / `end_input()`, Cartesia Sonic-3 receives zero characters until the entire LLM completion finishes, defeating incremental streaming synthesis and adding full LLM generation time onto TTS TTFB.
- **How to Reproduce / Verify:** Attach a `"metrics_collected"` listener to `ScrubbedTTS(EdgeTTS())` and synthesize text; only `wrapper.inner` emits `"metrics_collected"`, while the wrapper emits nothing. Call `_ScrubbedSynthesizeStream.push_text("Hello. ")` and inspect `self._inner` — nothing is pushed until `end_input()`.
- **Recommended Fix:**
  - In `ScrubbedTTS.__init__`, forward `"metrics_collected"` and `"error"` from `self.inner` via `self.emit(...)` (and detach listeners in `aclose()`).
  - In `_ScrubbedSynthesizeStream.push_text()`, stream-scrub incrementally: hold back only the trailing incomplete token/window (e.g., last 64 chars or back to the last whitespace/punctuation before any partial prefix like `Bearer ` or `password=`) and push completed scrubbed prefixes to `self._inner.push_text(...)` immediately so Cartesia Sonic-3 starts synthesizing on the first sentence/phrase while still catching secrets split across token boundaries.

---

### CRIT-5: `sql_guard.py` AST firewall is bypassable via PostgreSQL XML/dynamic-SQL/system functions and lacks `statement_timeout`
- **File & Lines:** `mcp_servers/ops_db_mcp/sql_guard.py:11-23, 66-89`, `mcp_servers/ops_db_mcp/service.py:79-84`
- **Symbol / Code Quote:**
  ```python
  # mcp_servers/ops_db_mcp/sql_guard.py:11-23
  BLOCKED_FUNCTIONS: frozenset[str] = frozenset(
      {
          "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_sleep",
          "lo_import", "lo_export", "dblink", "dblink_exec", "query_to_xml",
      }
  )
  ```
- **Root-Cause Reasoning:** `sql_guard.py` uses a 9-function denylist plus `func_name.startswith("pg_")` instead of a function allowlist, and does not require an allowlisted table to appear in the query. Verified directly against `validate_and_rewrite_sql()` in `.venv` (`sqlglot==30.33.0`):
  - **Dynamic SQL execution inside Postgres:** `SELECT query_to_xml_and_xmlschema('SELECT * FROM pg_catalog.pg_roles', true, false, '')` and `SELECT * FROM ops_incidents WHERE query_to_xml_and_xmlschema('SELECT pg_sleep(5)', true, false, '') IS NOT NULL` are **ALLOWED**. PostgreSQL evaluates the inner SQL string at runtime, completely bypassing `ALLOWED_TABLES`, `FORBIDDEN_NODES`, and `BLOCKED_FUNCTIONS`.
  - **Catalog / metadata disclosure:** `SELECT table_to_xml('pg_catalog.pg_roles', true, false, '')`, `SELECT table_to_xmlschema(...)`, `SELECT schema_to_xml('information_schema', true, false, '')`, `SELECT database_to_xml(true, false, '')`, and `SELECT cursor_to_xml(...)` are **ALLOWED**, exposing `pg_catalog` and `information_schema` metadata readable by `ops_readonly`.
  - **System/DoS functions:** `dblink_connect`, `lo_get`, `lo_put`, `set_config('statement_timeout', '0', false)`, `current_setting('data_directory')`, `inet_server_addr()`, `version()`, `repeat('A', 100000000)`, `generate_series(1, 10000000)`, `xmltable(...)`, and self-cartesian cross joins (`SELECT * FROM ops_incidents, (SELECT * FROM ops_incidents) sub`) are all **ALLOWED**.
  - **No query timeout:** `OpsDBService._fetch_dicts()` executes queries without `SET LOCAL statement_timeout` or `SET LOCAL TRANSACTION READ ONLY`, leaving Postgres vulnerable to CPU/memory exhaustion. (DB role grants on `ops_readonly` still prevent reading `pending_actions` and `calendar_events`, capping blast radius to catalog disclosure and DB DoS rather than table exfiltration.)
- **How to Reproduce / Verify:** Run `validate_and_rewrite_sql("SELECT query_to_xml_and_xmlschema('SELECT * FROM pg_catalog.pg_roles', true, false, '')")` and `validate_and_rewrite_sql("SELECT repeat('A', 100000000)")` — both return normalized SQL without raising `SQLValidationError`.
- **Recommended Fix:**
  1. Replace the denylist with an explicit safe SQL function **allowlist** (`count`, `sum`, `avg`, `min`, `max`, `coalesce`, `nullif`, `round`, `lower`, `upper`, `trim`, `substring`, `length`, `abs`, `ceil`, `floor`, `date_trunc`, `extract`, `now`, `to_char`, `greatest`, `least`, `cast`) and reject any `exp.Anonymous` or unlisted `exp.Func`.
  2. Require at least one allowlisted `exp.Table` (`ops_incidents`, `service_metrics`, `deployments`) in `root`.
  3. Inside `OpsDBService._fetch_dicts()`, wrap execution in a transaction with `SET LOCAL TRANSACTION READ ONLY` and `SET LOCAL statement_timeout = '3000ms'`.

---

### CRIT-6: GitHub Actions CI is guaranteed to fail out of the box (`agent/tests` connects to live Postgres with no Postgres container)
- **File & Lines:** `.github/workflows/ci.yml:9-25`, `agent/tests/test_concurrency_bargein.py:68-104`, `agent/tests/test_security_phase3.py:19-35, 207-208`
- **Symbol / Code Quote:**
  ```yaml
  # .github/workflows/ci.yml:23-24
  - name: Run unit test suite
    run: pytest -q api/tests/test_api_phase0.py agent/tests mcp_servers/tests/test_sql_guard.py eval/tests -k "not test_github_fixture" && pytest -q mcp_servers/tests/test_github_workspace.py -k "not test_workspace_calendar_overlap_and_mailpit_delivery"
  ```
- **Root-Cause Reasoning:** The `python-tests` job runs on `ubuntu-latest` with no `services: postgres:` container, yet runs all of `agent/tests`. Two tests in `agent/tests` connect to PostgreSQL directly:
  - `agent/tests/test_concurrency_bargein.py:68`: `test_simultaneous_voice_and_ui_approval_race_executes_once` calls `HITLGate()` -> `psycopg.connect(...)`.
  - `agent/tests/test_security_phase3.py:30, 19`: `test_20_red_team_attacks_blocked_with_controls_on_vs_negative_control` calls `HITLGate()` and `_cleanup_session()`, which even hardcodes `dsn = "postgresql://sadaops:sadaops_dev_pass@localhost:5432/sadaops"`.
  Both tests crash on `ubuntu-latest` with `psycopg.OperationalError: connection failed: connection refused`. Additionally, `-k "not test_github_fixture"` matches zero tests in the first `pytest` command (a dead filter).
- **How to Reproduce / Verify:** Stop local Docker Postgres and run the exact command from `.github/workflows/ci.yml:24`: both Postgres-backed tests in `agent/tests` fail immediately.
- **Recommended Fix:** Add a `postgres:16-alpine` service container to `.github/workflows/ci.yml` initialized with `infra/postgres/init.sql` (which also allows running `api/tests/test_hitl_api.py` in CI), OR tag DB-dependent tests with `@pytest.mark.integration` and run `-m "not integration"` in the unit-test step. Remove the dead `-k "not test_github_fixture"` filter.

---

## Normal Findings

### NORM-1: Live `agent/main.py` bypasses `GroqLLMRouter` (no dual-key rotation, 429 failover, or 120b fallback) + unbounded `Retry-After` & mid-stream retry bug in `GroqLLMRouter`
- **File & Lines:** `agent/main.py:16, 267`, `agent/config.py:23, 29-32`, `agent/pipeline/llm_router.py:88-101, 129-157`
- **Symbol / Code Quote:**
  ```python
  # agent/main.py:267
  llm=groq.LLM(model=settings.llm_model, api_key=settings.groq_api_key),
  ```
- **Root-Cause Reasoning:**
  1. `agent/main.py` instantiates LiveKit's stock `groq.LLM` using only a single `settings.groq_api_key` and `settings.llm_model`. `AgentSettings` (`agent/config.py`) does not load `GROQ_API_KEY_2`, and `settings.llm_fallback_model`, `settings.llm_timeout_s`, and `settings.llm_max_retries` are never referenced in `agent/main.py`. `GroqLLMRouter` (`agent/pipeline/llm_router.py`) is only used by `eval/`, directly contradicting `README.md:16` and `docs/architecture.md:11`.
  2. Inside `GroqLLMRouter._stream_with_retries` (`llm_router.py:144-151`), `retry_after` is parsed via `float(retry_after)` with no upper bound — causing `ops_scenarios.jsonl` `ar-12` to block `707,625 ms` (~11.8 min) on a single `429` wait. Also, if `_execute_stream` yields >=1 chunks and then raises `RemoteProtocolError`/`ReadTimeout`, `_stream_with_retries` retries from scratch, mixing partial chunks from two responses.
- **How to Reproduce / Verify:** Search `agent/main.py` for `GroqLLMRouter` or `load_groq_api_keys` (0 matches); inspect `eval/results/ops_scenarios.jsonl` line 32 (`"scenario_id": "ar-12", "llm_ttft_ms": 707625.0`).
- **Recommended Fix:** Implement a LiveKit `llm.LLM` adapter wrapping `GroqLLMRouter` (or LiveKit's `FallbackAdapter` with dual `groq.LLM` instances across `load_groq_api_keys()` and `llm_fallback_model`). Cap `Retry-After` sleep (e.g., `<= 5.0s` before failing over) and only retry/failover in `GroqLLMRouter` when zero chunks have been emitted yet.

---

### NORM-2: Layer 3 E2E benchmark (`93.3% = 56/60`) is inflated by non-LLM mocks, `any()` vs `all()` scoring, unchecked tool errors/args, and disabled write limits
- **File & Lines:** `eval/ops_scenarios/run_ops_eval.py:322-323, 468-481, 487-620`, `eval/results/ops_scenarios.jsonl:13, 17, 20, 24-34, 39, 41-60`
- **Symbol / Code Quote:**
  ```python
  # eval/ops_scenarios/run_ops_eval.py:322-323
  for c, ac in zip(calls, assistant_calls, strict=True):
      ops_router.reset_turn_writes()
      t_res = await ops_router.execute(...)
  ...
  # eval/ops_scenarios/run_ops_eval.py:612
  expected_hit = any(t in executed_names for t in sc.expected_tools)
  ```
- **Root-Cause Reasoning:**
  1. **20/60 scenarios never test the LLM (`llm_ttft_ms: 0.0`):** `barge_in` (`barge-01`..`barge-10`) runs `_slow_tool` + hardcoded `hist`; `security` (`sec-01`..`sec-10`) hardcodes tool names/args without ever feeding `transcript` to `llm_router`. Moreover, `sec-10` has `expected_tools=["get_ci_logs"]`, `injected_tool_output=None`, and `tts_secret_probe=...`, matching none of the `if/elif` branches at lines 553–588 so `tool_ok` stays `True` without invoking any tool.
  2. **`any()` instead of `all()` hides missing tools:** On multi-tool scenarios (`en-13`, `ar-13`, `ar-14`), only 1 of 2 expected tools ran yet line 612 marked all three `passed: true` (`ar-13` executed only `search_runbooks`; `ar-14` executed only `get_ci_logs`; `en-13` executed only `get_issue_details`). Using `all()`, strict pass rate on the 40 real LLM scenarios drops from `36/40` (`90%`) to at most `33/40` (`82.5%`).
  3. **No argument or returned-error validation:** `FixtureGitHubAdapter` returns `{"error": ...}` as a regular JSON dict (`mcp_servers/github_mcp/adapter.py:99,105`), which `MCPServerPool.call_tool` does not flag as `is_error`. Thus `ar-04` (`transcript` heard `8000` instead of `8891`, returning `"CI run 8000 not found"`) and `ar-07` (`transcript` heard `620 في 85` instead of `1.85`) both scored `passed: true`. `en-17`/`en-20` transcripts contained `T10HZ`/`T14HZ`.
  4. **Write-cap control disabled in eval:** Line 323 calls `reset_turn_writes()` per tool call instead of once per scenario, disabling `max_writes_per_turn=1`.
  5. **Eval STT differs from live STT & saved JSONL still has 429 spikes:** `run_ops_eval.py:468-480` uses `faster-whisper small` for EN and Speechmatics Batch `ar_en` for AR (whereas live `agent/main.py` uses Speechmatics Agent STT `multi`), and `eval/results/ops_scenarios.jsonl` contains `124.9s–707.6s` `llm_ttft_ms` spikes (`ar-05`, `ar-06`, `ar-08`, `ar-10`, `ar-11`, `ar-12`), contradicting `docs/operations.md:25`.
- **How to Reproduce / Verify:** Run a quick inspection over `eval/results/ops_scenarios.jsonl` checking `en-13`, `ar-13`, `ar-14`, `ar-04`, `ar-05`–`ar-12`, and `barge-*`/`sec-*`.
- **Recommended Fix:** Change line 612 to `all(t in executed_names for t in sc.expected_tools)`, check tool output dicts for `"error"` and validate key arguments, move `reset_turn_writes()` before `_run_ops_agent_turns`, run `security` scenarios through the real LLM prompt first, disclose exact LLM-tested vs deterministic-unit subsets clearly in `docs/evaluation.md` and `README.md`, and re-run to regenerate clean JSONL baselines.

---

### NORM-3: Spoken fillers pollute LLM `ChatContext` & transcript UI and keep playing after tool completion
- **File & Lines:** `agent/main.py:278-282`, `agent/pipeline/tool_router.py:302-322, 350-352, 378-380`
- **Symbol / Code Quote:**
  ```python
  # agent/main.py:278-282
  async def _speak_filler(phrase: str) -> None:
      try:
          await session.say(phrase, allow_interruptions=True)
      except Exception as exc:
          logger.debug("Spoken filler skipped: %s", exc)
  ```
- **Root-Cause Reasoning:** Verified in `livekit-agents==1.8.13` (`AgentSession.say` defaults to `add_to_chat_ctx: bool = True`): calling `session.say(phrase, allow_interruptions=True)` appends every filler phrase (`"Checking the ops database now."`, etc.) into the LLM's persistent `ChatContext` and triggers `conversation_item_added` (rendering filler messages as assistant turns in the React transcript panel). Furthermore, `session.say()` schedules a `SpeechHandle` asynchronously; `await session.say(...)` awaits its playout, so cancelling `filler_task` (`tool_router.py:321`) once the `SpeechHandle` is scheduled does not cleanly interrupt already-queued audio unless `handle.interrupt()` is invoked or `add_to_chat_ctx=False` + explicit handle cancellation is used.
- **How to Reproduce / Verify:** Inspect `AgentSession.say` signature in `livekit-agents==1.8.13` (`add_to_chat_ctx: bool = True`) and `agent/main.py:280`.
- **Recommended Fix:** Pass `add_to_chat_ctx=False` to `session.say(...)`, store the returned `SpeechHandle`, and interrupt it in a `finally:` / cancellation block if the tool finishes or errors before filler playout completes (or disable overlapping `say()` while `AgentSession` already owns speech scheduling during a `function_tool`).

---

### NORM-4: Live Speechmatics STT loads a duplicate Silero VAD and disables `AgentSession` manual flush (`manual_flush=False`)
- **File & Lines:** `agent/pipeline/stt_adapter.py:61-66`, `agent/main.py:264-266`
- **Symbol / Code Quote:**
  ```python
  # agent/pipeline/stt_adapter.py:61-66
  return speechmatics.STT(
      api_key=settings.speechmatics_api_key,
      language=language,
      include_partials=True,
      additional_vocab=vocab_entries,
  )
  ```
- **Root-Cause Reasoning:** Verified in `livekit-plugins-speechmatics==1.8.13` (`speechmatics.STT.__init__`): when `turn_detection_mode=EXTERNAL` (default) and `vad` is omitted (`NOT_GIVEN`), the plugin loads a second `silero.VAD` instance internally (`s._vad`) and sets `STTCapabilities(..., manual_flush=False)` because `_supports_stream_flush` returns `True` **only** when `local_vad is None`. Meanwhile `agent/main.py:265` also passes `vad=silero.VAD.load()` to `AgentSession`, resulting in two competing Silero VAD models and preventing `AgentSession` from flushing the STT stream on endpointing. Additionally, `docs/operations.md:19-21` noted `speechmatics.agent_stt` issues on `"en"` during batch eval, and live `linden-1` with `language="multi"` was not yet tested end-to-end against the real-time WebSocket service.
- **How to Reproduce / Verify:** Run `build_stt(AgentSettings(stt_provider="speechmatics", speechmatics_api_key="test"))` in `.venv`: `s._vad` is a `silero.VAD` instance and `s.capabilities.manual_flush` is `False`.
- **Recommended Fix:** Pass `vad=None` in `speechmatics.STT(...)` when paired with `AgentSession(vad=silero.VAD.load(), ...)` so `manual_flush=True` and a single VAD drives endpointing; verify live WebSocket recognition with `language="multi"` on a real audio stream.

---

### NORM-5: Live `TurnManager` / `DualSpeedToolRouter` barge-in disconnect, swallowed `CancelledError`, and async event races
- **File & Lines:** `agent/main.py:297-328`, `agent/pipeline/tool_router.py:312-316, 350-377`, `agent/pipeline/turn_manager.py:30-86`
- **Symbol / Code Quote:**
  ```python
  # agent/main.py:321-324
  if new_state == "speaking" and turn_manager.state.value in ("thinking", "speaking"):
      if active_record is not None:
          active_record.interrupted = True
      asyncio.create_task(turn_manager.handle_barge_in())
  ```
  ```python
  # agent/pipeline/tool_router.py:350-377
  except asyncio.CancelledError:
      ...
      return MCPToolResult(..., error="cancelled by user interruption")
  ```
- **Root-Cause Reasoning:**
  1. `reconcile_interrupted_history()` (`turn_manager.py:96-139`) is never imported or called anywhere in `agent/main.py` (contradicting `README.md:75` and `docs/architecture.md:13,27`). `AgentSession` maintains its own `ChatContext` internally and never receives `TurnManager`'s repair.
  2. In `DualSpeedToolRouter.execute()` (`tool_router.py:350`), `except asyncio.CancelledError:` catches outer task cancellation (e.g. if LiveKit `AgentActivity` cancels a tool task) and returns an `MCPToolResult` instead of cancelling `call_task` and re-raising `CancelledError`.
  3. `agent/main.py:305-327` schedules `turn_manager.start_thinking()`, `start_speaking()`, `finish_turn()`, and `handle_barge_in()` via detached `asyncio.create_task()` calls without ordering guarantees, and reads `turn_manager.state.value` without locking. When `finish_turn()` executes `self.clear_cancel_hooks()` (`turn_manager.py:54`) slightly late, it can wipe cancel hooks registered for the next turn; and on `user_state_changed`, `old_state == "speaking" and new_state == "listening"` (`agent/main.py:325-326`) overwrites `active_record` right when `user_input_transcribed` may need it.
- **How to Reproduce / Verify:** Search `agent/main.py` for `reconcile_interrupted_history` (0 matches); cancel the coroutine running `DualSpeedToolRouter.execute()` externally and observe that `call_task` keeps running and `CancelledError` is suppressed.
- **Recommended Fix:** On `CancelledError` inside `DualSpeedToolRouter.execute()`, cancel `call_task`, await it safely, and re-raise if outer-cancelled (while only returning cancelled result if triggered by `turn_manager` hook while outer task stays alive, or let LiveKit handle tool interruption directly); sequence `TurnManager` transitions through a per-session queue or synchronous state transitions rather than unordered `create_task`s; and update docs to reflect how `AgentSession` vs `TurnManager` actually manage chat context.

---

### NORM-6: UI-approved `create_issue` silently dropped in fixture mode, UI approvals never notify live voice session, and failed executions strand Postgres rows in `'approved'`
- **File & Lines:** `api/app.py:164-175, 207-223`, `agent/safety/hitl_gate.py:211-279`, `infra/postgres/init.sql:70`
- **Symbol / Code Quote:**
  ```python
  # api/app.py:172-174
  if tool_name == "create_issue":
      adapter = build_github_adapter()
      return await adapter.create_issue(CreateIssueInput(**payload))
  ```
  ```python
  # agent/safety/hitl_gate.py:274-278
  claimed = await asyncio.to_thread(self._claim_for_execution, action_id, expected_payload_hash)
  result = await executor(claimed.tool_name, claimed.payload)
  return await asyncio.to_thread(self._mark_executed, action_id, result)
  ```
- **Root-Cause Reasoning:**
  1. When `GITHUB_MODE=fixture` (default), `api/app.py:173` constructs a fresh `FixtureGitHubAdapter()` inside `execute_locked_write_tool()`, so an issue approved in the React UI is created in an ephemeral Python dict and discarded immediately — `github_mcp` never sees it.
  2. Approving or rejecting via `POST /api/hitl/approve` or `/reject` updates Postgres only; it neither publishes `hitl_resolved` to LiveKit DataChannel nor notifies the active voice `AgentSession`, so the voice agent doesn't know the operator approved via UI.
  3. If `executor(...)` raises an exception (`CalendarConflictError`, `ValidationError`, SMTP timeout, GitHub HTTP error), `_mark_executed` never runs. The row remains permanently stuck in `status = 'approved'` with `result_json = NULL` (`init.sql:70` CHECK constraint does not even allow `'failed'`), and any subsequent UI retry hits `409 Conflict`.
- **How to Reproduce / Verify:** Propose a `create_calendar_event` for an occupied time slot via `/api/hitl/propose` and approve it via `/api/hitl/approve`: FastAPI returns HTTP 500, and Postgres shows `status = 'approved'` forever.
- **Recommended Fix:** Persist fixture issues in Postgres (or route `api/app.py` to shared state), add `'failed'` to `pending_actions.status` CHECK constraint and record failure details in `approve_and_execute` on exception, and emit a LiveKit server-API `send_data` notification (or have `agent/main.py` poll/LISTEN on Postgres notifications) when UI resolves an action.

---

### NORM-7: Synchronous `psycopg.connect` calls block the live WebRTC asyncio event loop
- **File & Lines:** `agent/pipeline/tool_router.py:231`, `agent/main.py:197, 217`, `agent/safety/hitl_gate.py:179, 283, 319, 344`
- **Symbol / Code Quote:**
  ```python
  # agent/pipeline/tool_router.py:231
  pending = self._hitl_gate.propose_action(...)
  # agent/main.py:197, 217
  pending = self._hitl_gate.get_action(action_id)
  rejected = self._hitl_gate.reject_action(action_id)
  ```
- **Root-Cause Reasoning:** While `approve_and_execute` correctly wraps DB calls in `asyncio.to_thread`, `propose_action`, `get_action`, `reject_action`, and `list_pending` open blocking `psycopg.connect(..., connect_timeout=5)` TCP connections directly on the main asyncio loop when called from `DualSpeedToolRouter.execute()` or `SadaOpsAgent`. Any Postgres hiccup stalls audio frame forwarding, VAD, and STT/TTS across the entire LiveKit worker process.
- **How to Reproduce / Verify:** Introduce latency/block on port `5432` while running an async loop calling `DualSpeedToolRouter.execute("send_email", ...)`; all concurrent asyncio tasks freeze for up to 5 seconds.
- **Recommended Fix:** Wrap `propose_action`, `get_action`, and `reject_action` calls in `await asyncio.to_thread(...)` (or migrate `HITLGate` to `psycopg.AsyncConnectionPool`).

---

### NORM-8: Write-policy bypasses (`_extract_email_domain`, missing secret checks on calendar/labels, HTTP 500 on bad duration) & GitHub URL path injection
- **File & Lines:** `agent/safety/prompt_guard.py:79-83, 108-141`, `mcp_servers/github_mcp/adapter.py:155, 176, 191, 216, 235`
- **Symbol / Code Quote:**
  ```python
  # agent/safety/prompt_guard.py:79-83
  def _extract_email_domain(address: str) -> str:
      cleaned = address.strip().lower()
      if "@" not in cleaned:
          return ""
      return cleaned.rsplit("@", 1)[1]
  ```
- **Root-Cause Reasoning:**
  1. `_extract_email_domain("victim@evil.com, ops-leads@example.com")` returns `"example.com"`, passing `DEFAULT_ALLOWED_EMAIL_DOMAINS`. Likewise `"evil@evil.com\r\nBcc: victim@evil.com@example.com"` returns `"example.com"`. Because `CalendarEventInput.attendees` (`workspace_mcp/service.py:22`) is `list[str]` rather than `list[EmailStr]`, malformed/multi-recipient strings pass straight into `calendar_events`.
  2. `validate_write_policy` skips `contains_secret()` on `create_calendar_event` (`title`, `notes`, `location`) and `create_issue` (`labels`) — verified `"ghp_1234567890abcdefghijklmnop"` in calendar `title`/`notes` and issue `labels` passes `validate_write_policy` unchecked.
  3. Line 137 runs `int(payload.get("duration_minutes", 30))`, raising `ValueError` instead of `PolicyViolationError` if passed a non-numeric string (causing an uncaught HTTP 500 in `api/app.py:203`).
  4. `LiveGitHubAdapter` interpolates `repo` directly into `https://api.github.com/repos/{target}/...` with zero `owner/repo` regex validation and zero allowlist check on read methods (`list_open_prs`, `get_ci_logs`, `get_issue_details`), allowing URL path traversal (e.g., `../../user/repos`) authenticated with `GITHUB_TOKEN`.
- **How to Reproduce / Verify:** Run `validate_write_policy("send_email", {"to": "victim@evil.com, ops-leads@example.com", "subject": "hi", "body": "ok"})` and `validate_write_policy("create_calendar_event", {"title": "ghp_1234567890abcdefghijklmnop", "start_iso": "2030-01-01T10:00:00Z", "duration_minutes": 30})` — both succeed.
- **Recommended Fix:** Validate email addresses strictly with `email_validator` / single-address regex in `_extract_email_domain` (reject whitespace, commas, CR/LF, and multiple `@`); enforce `EmailStr` on `CalendarEventInput.attendees`; run `contains_secret()` recursively over the entire serialized write payload; coerce `duration_minutes` safely and raise `PolicyViolationError`; and enforce `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$` + repo allowlist in `LiveGitHubAdapter`.

---

### NORM-9: `workspace_mcp` URL query injection, calendar TOCTOU double-booking race, spanning-event/slot bugs, and broken `.local` seed data
- **File & Lines:** `mcp_servers/workspace_mcp/service.py:66-75, 85-147, 166-175`, `infra/postgres/init.sql:112-124`
- **Symbol / Code Quote:**
  ```python
  # mcp_servers/workspace_mcp/service.py:168-172
  url = (
      f"{self.mailpit_api_url}/api/v1/search?query={query}&limit={limit}"
      if query.strip()
      else f"{self.mailpit_api_url}/api/v1/messages?limit={limit}"
  )
  ```
- **Root-Cause Reasoning:**
  1. `search_inbox` embeds raw `query` into the URL string rather than passing `params={"query": query, "limit": limit}`, corrupting queries containing spaces/`&`/`#` and allowing query-parameter injection.
  2. `create_calendar_event` runs an unlocked `SELECT` then `INSERT` (`service.py:113-139`), permitting concurrent bookings to overlap.
  3. `list_events(day_iso)` checks `start_time >= day_start AND start_time < day_end` instead of overlap (`end_time > day_start AND start_time < day_end`), missing events that span across UTC midnight; `find_free_slots` does not validate `duration_minutes >= 5` (if `duration_minutes <= 0`, it emits invalid zero/negative slots) and uses `day_dt.replace(hour=9)` without `.astimezone(UTC)`.
  4. `infra/postgres/init.sql:112-124` still seeds `@sadaops.local` addresses despite `docs/operations.md:35-37` claiming internal fixtures were standardized on `.example` / `example.com`.
- **How to Reproduce / Verify:** Call `search_inbox(query="a&limit=100")` (injects `limit`) or inspect `init.sql:112` (`bejad@sadaops.local`).
- **Recommended Fix:** Use `client.get(endpoint, params=...)` in `search_inbox`; enforce a Postgres `EXCLUDE USING gist (tstzrange(start_time, end_time) WITH &&)` constraint (or `SERIALIZABLE`/advisory lock) in `create_calendar_event`; fix overlap filtering and `duration_minutes` bounds in `list_events`/`find_free_slots`; update `init.sql` emails to `@sadaops.example`.

---

### NORM-10: Latency waterfall math omits MCP execution time from `mouth_to_ear_ms` and overwrites multi-tool durations
- **File & Lines:** `agent/main.py:386-399`, `agent/pipeline/telemetry.py:54-67, 71-99`
- **Symbol / Code Quote:**
  ```python
  # agent/main.py:391-396
  elif isinstance(m, metrics.LLMMetrics):
      anchor = active_record.t_stt_final_ms or active_record.t0_vad_end_ms
      active_record.mark_llm_ttft(anchor + max(0.0, m.ttft * 1000.0))
  elif isinstance(m, metrics.TTSMetrics):
      anchor = active_record.t_llm_ttft_ms or active_record.t0_vad_end_ms
      active_record.mark_tts_ttfb(anchor + max(0.0, m.ttfb * 1000.0))
  ```
- **Root-Cause Reasoning:** `t_tts_ttfb_ms` is synthesized as `t_llm_ttft_ms + tts_ttfb`, completely omitting MCP execution duration (`t_mcp_end_ms - t_mcp_start_ms`), LLM tool-generation duration, and the second LLM call after tool results return. Consequently `mouth_to_ear_ms = t_tts_ttfb_ms - t0_vad_end_ms` under-reports real latency on every tool-calling turn. Additionally, `mark_mcp_start`/`mark_mcp_end` overwrite previous tool timings rather than summing sequential tool calls.
- **How to Reproduce / Verify:** Simulate a turn with 150ms STT, 100ms LLM TTFT, 2000ms MCP tool call, and 200ms TTS TTFB using `agent/main.py:386-396`: `mouth_to_ear_ms` computes as `450 ms` instead of `>= 2450 ms`.
- **Recommended Fix:** Measure `t_tts_ttfb_ms` using actual monotonic clock (`time.monotonic() * 1000.0`) when TTS audio begins (or anchor TTS TTFB on `max(t_llm_ttft_ms, t_mcp_end_ms or 0.0)` plus post-tool LLM TTFT), and accumulate `total_mcp_ms` across multiple tool calls per turn.

---

### NORM-11: Assistant transcripts sent over DataChannel bypass `scrub_for_tts()` + regex/entropy gaps in `tts_scrubber.py`
- **File & Lines:** `agent/main.py:355-375`, `agent/safety/tts_scrubber.py:13-84`
- **Symbol / Code Quote:**
  ```python
  # agent/main.py:356-374
  def _on_item_added(ev: Any) -> None:
      item = getattr(ev, "item", None)
      ...
      text = getattr(item, "text_content", "") or ""
      ...
      asyncio.create_task(_publish_event(ctx, {"type": "transcript", ..., "text": text, ...}))
  ```
- **Root-Cause Reasoning:**
  1. Even when `ScrubbedTTS` redacts a secret from spoken audio, `_on_item_added` sends the raw, unscrubbed `item.text_content` straight over the WebRTC DataChannel to the browser UI.
  2. Verified directly against `scrub_for_tts()` in `.venv`: the scrubber misses:
     - `https://user:SuperSecretPass99@api.example.com/v1`, `mysql://admin:SuperSecretPass99@db.local/prod`, `mongodb+srv://...` (only `postgres/postgresql/redis` DSNs match, and lines 81–83 disable the entropy detector when `http://`/`https://` precedes a token — so `https://K9mPqR7xL2vN8wZ4tY6bC3hJ5fD1sA0eG@example.com` also passes unredacted!).
     - PEM private keys (`-----BEGIN RSA PRIVATE KEY-----...`).
     - Key names like `secret_key: MySuperSecretValue123` or `LIVEKIT_API_SECRET=secretsecretsecretsecretsecret32` (low lowercase/uppercase diversity fails `_is_high_entropy_secret` and key regex omits `*_secret` / `*_token`).
     - Slack tokens (`xoxb-...`), short `<16`-char `gsk_`/`Bearer` tokens.
- **How to Reproduce / Verify:** Run `scrub_for_tts("https://user:SuperSecretPass99@api.example.com/v1")` and `scrub_for_tts("LIVEKIT_API_SECRET=secretsecretsecretsecretsecret32")` — both return `[]` categories.
- **Recommended Fix:** Pass assistant transcript `text` through `scrub_for_tts(text)[0]` in `_on_item_added` before publishing to DataChannel; broaden `tts_scrubber.py` DSN schemes (`https?://[^:\s/]+:[^@\s]+@`, `mysql://`, `mongodb(?:\+srv)?://`, `amqp://`), add PEM private key & `xox[bpas]-` patterns, match `[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|KEY)` prefixes, and apply URL-credential redaction explicitly.

---

### NORM-12: Cross-platform portability & dependency manifest gaps (`playwright.config.ts`, `requirements.txt`, `api/requirements.txt`, `faster_whisper` unimplemented)
- **File & Lines:** `web/playwright.config.ts:13`, `requirements.txt:1-27`, `api/requirements.txt:1-11`, `agent/pipeline/stt_adapter.py:68-70`, `agent/config.py:26`
- **Symbol / Code Quote:**
  ```ts
  // web/playwright.config.ts:13
  command: ".venv\\Scripts\\python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000",
  ```
- **Root-Cause Reasoning:**
  1. `web/playwright.config.ts:13` hardcodes Windows-only `.venv\\Scripts\\python.exe`, failing on Linux/macOS (`.venv/bin/python`).
  2. Root `requirements.txt` omits `faster-whisper`, `datasets`, `av`, and `huggingface_hub` imported by `eval/` scripts, causing `ModuleNotFoundError` on fresh installs when reproducing benchmarks.
  3. `api/requirements.txt` omits `email-validator`, even though `api/app.py:28` imports `SendEmailInput` (`EmailStr`).
  4. `STT_PROVIDER=faster_whisper` is accepted by `AgentSettings` (`agent/config.py:26`) and documented in `.env.example:46` and `SECURITY.md:11`, yet `build_stt` unconditionally raises `NotImplementedError`.
- **How to Reproduce / Verify:** Check `requirements.txt` for `faster-whisper` (absent), `api/requirements.txt` for `email-validator` (absent), and `web/playwright.config.ts:13` on Linux.
- **Recommended Fix:** Select `.venv/bin/python` vs `.venv\\Scripts\\python.exe` based on `process.platform === "win32"` in `playwright.config.ts`; add `email-validator>=2.2.0` to `api/requirements.txt` and `eval/requirements.txt` (or document `[eval]` extras in `pyproject.toml`); implement or clearly mark `faster_whisper` as offline-eval-only rather than exposing a broken live toggle.

---

### NORM-13: Docker sandbox & Qdrant resource leaks, unbounded container log read OOM risk, and cold-start Qdrant race
- **File & Lines:** `mcp_servers/knowledge_sandbox_mcp/service.py:54-173`
- **Symbol / Code Quote:**
  ```python
  # mcp_servers/knowledge_sandbox_mcp/service.py:134-139
  stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
  stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
  return {
      "exit_code": exit_code,
      "stdout": stdout[:4000],
      "stderr": stderr[:2000],
      ...
  ```
- **Root-Cause Reasoning:**
  1. `container.logs(...)` pulls the entire container output buffer into the host Python process memory before truncating to `[:4000]` — an adversarial snippet like `print("A" * 500_000_000)` can OOM-kill the MCP server.
  2. `mem_limit="128m"` is set without `memswap_limit="128m"`, permitting swap memory expansion.
  3. `docker.from_env()` is created on every `run_python_calc` call and never `.close()`d; similarly `search_runbooks` opens two unclosed `QdrantClient(...)` instances per query (`ensure_runbooks_indexed()` + `search_runbooks()`), leaking sockets/file descriptors.
  4. `ensure_runbooks_indexed()` has a check-then-create race (concurrent `create_collection` throws `409`) and skips re-upserting updated runbooks whenever `points_count >= len(RUNBOOKS)`.
- **How to Reproduce / Verify:** Inspect `mcp_servers/knowledge_sandbox_mcp/service.py:56, 87, 115, 134-138`.
- **Recommended Fix:** Cap container log size via `log_config` or stream-read capped bytes (`stream=True` stopping after 8KB), pass `memswap_limit="128m"`, reuse/close persistent `docker.DockerClient` and `QdrantClient` instances, handle `UnexpectedResponse`/`409` idempotently in `ensure_runbooks_indexed()`, and key re-indexing on a content hash rather than `>= len(RUNBOOKS)`.

---

### NORM-14: React Cockpit shared room collision, unfiltered HITL polling, missing WebRTC error handling, and unauthenticated DataChannel trust
- **File & Lines:** `web/src/App.tsx:57-81, 115-162, 253, 287-305`, `web/src/components/LatencyWaterfallPanel.tsx:32`, `web/src/types.ts:3`
- **Symbol / Code Quote:**
  ```tsx
  // web/src/App.tsx:146
  room_name: "sadaops-main",
  ```
  ```tsx
  // web/src/App.tsx:117
  const res = await fetch(`${API_BASE}/api/hitl/pending`);
  ```
- **Root-Cause Reasoning:**
  1. Every browser tab joins the same hardcoded `"sadaops-main"` room, causing concurrent operators to collide on audio and DataChannels.
  2. `/api/hitl/pending` is fetched without `session_id` (showing pending actions from any session/test) and has no periodic refresh/expiry countdown, leaving expired items visible until clicked (which then errors with HTTP 400).
  3. `<LiveKitRoom>` has no `onError` handler to show microphone-denied or WebRTC connection errors.
  4. `useDataChannel` accepts JSON from any room participant without verifying participant identity (`participant?.isAgent`) or topic.
  5. `LatencyWaterfallPanel` uses `key={wf.turn_id}` (`turn-1`, `turn-2`), which collides on reconnect when `turn_counter` resets to 0; `App.tsx` renders two separate state pills (`Agent: {agentState}` vs `useVoiceAssistant().state`), and `AgentState` in `types.ts:3` omits `"initializing"`.
- **How to Reproduce / Verify:** Inspect `web/src/App.tsx:117, 146, 253, 288-295` and `LatencyWaterfallPanel.tsx:32`.
- **Recommended Fix:** Generate or allow configuring a unique per-session `room_name` and filter `/api/hitl/pending?session_id=...` with periodic TTL refresh; add `onError` to `<LiveKitRoom>`; verify sender identity/metadata on `useDataChannel`; key waterfalls with a unique session/index key; unify the agent-state pill and add `"initializing"` to `AgentState`.

---

## Low Findings

### LOW-1: `infra/docker-compose.yml` binds all 5 dev services to `0.0.0.0` with default credentials and unpinned `:latest` tags
- **File & Lines:** `infra/docker-compose.yml:5-82`
- **Symbol / Code Quote:** `ports: - "5432:5432"`, `image: livekit/livekit-server:latest`, `image: qdrant/qdrant:latest`, `image: jaegertracing/all-in-one:latest`, `image: axllent/mailpit:latest`
- **Root-Cause Reasoning:** Publishing Postgres, Qdrant (no auth), LiveKit (`devkey`), Jaeger, and Mailpit on `0.0.0.0` exposes unauthenticated/default-password local stores on shared Wi-Fi/LANs, and `:latest` image tags risk breaking reproducibility.
- **How to Reproduce / Verify:** Inspect `infra/docker-compose.yml` port mappings and image tags.
- **Recommended Fix:** Bind dev ports to `127.0.0.1:` (e.g. `"127.0.0.1:5432:5432"`) and pin explicit minor/patch versions for LiveKit, Qdrant, Jaeger, and Mailpit.

### LOW-2: Live Cartesia TTS ignores `cartesia_voice_en` and always uses `cartesia_voice_ar`
- **File & Lines:** `agent/pipeline/tts_adapter.py:181-197`, `agent/config.py:35-36`
- **Symbol / Code Quote:**
  ```python
  # agent/pipeline/tts_adapter.py:186-191
  inner: tts.TTS = cartesia.TTS(
      api_key=settings.cartesia_api_key,
      model="sonic-3",
      voice=settings.cartesia_voice_ar,
      language=None,
  )
  ```
- **Root-Cause Reasoning:** Unlike `EdgeTTS` (which uses `pick_bilingual_voice()` per turn), live `build_tts(settings)` hardcodes `voice=settings.cartesia_voice_ar` and never uses `settings.cartesia_voice_en` (contradicting `docs/architecture.md:12`).
- **How to Reproduce / Verify:** Inspect `agent/pipeline/tts_adapter.py:186-191`; `cartesia_voice_en` is unused in `build_tts()`.
- **Recommended Fix:** Implement per-turn/per-sentence `voice` switching in a Cartesia wrapper (or `update_options(voice=...)` when Arabic vs English script is detected) using `pick_bilingual_voice()`, or document that Sonic-3 uses a single multilingual Arabic-capable voice (`Raed`).

### LOW-3: Documentation and config drift across 6 files
- **File & Lines:** `SECURITY.md:20`, `docs/security.md:10`, `docs/architecture.md:10, 14`, `mcp_servers/knowledge_sandbox_mcp/runbooks_data.py:68`, `README.md:12`, `api/app.py:128`
- **Symbol / Code Quote:**
  - `docs/security.md:10` / `SECURITY.md:20`: claims `300s` / `5-minute` TTL, whereas `agent/safety/hitl_gate.py:162` and `api/app.py:147` default to `120` seconds.
  - `docs/architecture.md:14`: claims filler delay `>=1.2s`, whereas `DualSpeedToolRouter` (`tool_router.py:180`) defaults to `0.35` seconds.
  - `docs/architecture.md:10`: claims `28` entries in `SADAOPS_ADDITIONAL_VOCAB`, whereas `stt_adapter.py:13-41` contains `27` entries.
  - `runbooks_data.py:68`: still mentions `qwen/qwen3.8-27b` instead of `openai/gpt-oss-120b`.
  - `README.md:12`: Mermaid routes browser `DataChannel Events` through `FastAPI` instead of directly through `LiveKit SFU`.
  - `api/app.py:128`: builds `{"language":"{req.language}"}` via f-string (should use `json.dumps`) and `agent/main.py` never reads participant metadata.
- **How to Reproduce / Verify:** Compare cited lines directly against `hitl_gate.py:162`, `tool_router.py:180`, `len(SADAOPS_ADDITIONAL_VOCAB) == 27`, `.env.example:50`, `agent/main.py:230`, and `api/app.py:128`.
- **Recommended Fix:** Align TTL/delay/vocab numbers, runbook text, Mermaid edges, and `json.dumps` metadata serialization across code and docs.

### LOW-4: Dead code, minor evaluation/SQL/GitHub schema bugs, and repo hygiene
- **File & Lines:** `eval/asr_codeswitch/run_asr_eval.py:126-178, 210-225`, `eval/voice_agent_bench/run_vab_eval.py:197-199`, `eval/metrics.py:205-206`, `mcp_servers/ops_db_mcp/service.py:18-19`, `mcp_servers/ops_db_mcp/sql_guard.py:70-78`, `mcp_servers/github_mcp/adapter.py:90-106`, `.gitignore`, `web/index.html:2`
- **Root-Cause Reasoning:**
  1. `transcribe_speechmatics_rt` (`run_asr_eval.py:126-178`) is dead code and calls `connect()` then `start_session()` (double-starting the session since `AgentSttAsyncClient.connect()` already invokes `start_session()`), while `transcribe_speechmatics_batch` (`run_asr_eval.py:210-225`) falls through after 60 poll iterations without verifying `status == "done"`.
  2. `run_vab_eval.py:197-199` copies `"tool"` history messages without `tool_call_id` (invalid in OpenAI/Groq chat schemas if present).
  3. `eval/metrics.py:205-206` flattens `(tool_name, arg_key, val)` across all parallel calls into a set, losing per-call grouping when the same tool is called twice with different args.
  4. `IncidentQuery` (`ops_db_mcp/service.py:18-19`) uses case-sensitive regexes (rejecting `"SEV1"`), and `sql_guard.py:70-78` rejects legitimate `WITH cte AS (...) SELECT * FROM cte` queries because `cte` is parsed as `exp.Table` not in `ALLOWED_TABLES`.
  5. `FixtureGitHubAdapter` (`github_mcp/adapter.py:90-106`) ignores the `repo` argument in `get_ci_logs`/`get_issue_details` and returns different keys (`ci_status`/`log_excerpt`) from `LiveGitHubAdapter` (`url`/`jobs`).
  6. `web/tsconfig.tsbuildinfo` is untracked and not listed in `.gitignore`, and `web/index.html:2` leaves `<html lang="en">` static when switching to Arabic.
- **How to Reproduce / Verify:** Inspect each referenced line range; run `validate_and_rewrite_sql("WITH cte AS (SELECT * FROM ops_incidents) SELECT * FROM cte")` (raises `SQLValidationError`).
- **Recommended Fix:** Remove or fix `transcribe_speechmatics_rt` and check `status == "done"` after polling; normalize `IncidentQuery` casing and whitelist CTE alias names in `sql_guard.py`; align `FixtureGitHubAdapter` / `LiveGitHubAdapter` schemas; add `*.tsbuildinfo` to `.gitignore` and sync `document.documentElement.lang` in `App.tsx`.

---

## Areas Inspected & Verified Solid

1. **Decoupled 4-MCP Process Isolation & AnyIO Task Scoping (`agent/pipeline/tool_router.py:88-148`):** Hosting each `stdio_client` inside its own `asyncio.Task` cleanly avoids AnyIO cancel-scope cross-task teardown errors while keeping Ops DB, GitHub, Workspace, and Sandbox isolated.
2. **PostgreSQL Atomic HITL Locking & SHA-256 Binding (`agent/safety/hitl_gate.py:86-94, 155-315`):** Canonical JSON payload hashing (`compute_payload_hash`), idempotency keys, and `SELECT ... FOR UPDATE` row locking prevent double-execution races (`test_simultaneous_voice_and_ui_approval_race_executes_once`).
3. **Database Privilege Separation (`infra/postgres/init.sql:5-11, 77-79`):** `ops_readonly` has `SELECT` exclusively on `ops_incidents`, `service_metrics`, and `deployments`, protecting `pending_actions` and `calendar_events` at the PostgreSQL layer.
4. **Docker Sandbox Container Hardening (`mcp_servers/knowledge_sandbox_mcp/service.py:118-130`, `infra/sandbox/Dockerfile`):** Runs as non-root `sandbox` user with `network_mode="none"`, `read_only=True`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, `pids_limit=32`, `nano_cpus=500_000_000`, `mem_limit="128m"`, and guaranteed `container.remove(force=True)` cleanup.
5. **Bilingual Evaluation Metrics & Bootstrap CIs (`eval/metrics.py`, `eval/report.py`):** Arabic/English normalization (`NFKC`, tashkeel/tatweel removal, Alef/Yaa/Ta-Marbuta unification), `english_token_recall`, deterministic `seed=42` bootstrap CIs, and `LiveGitHubAdapter` `MockTransport` parity tests are solid.
6. **React 19 RTL & Bidi UX (`web/src/i18n/strings.ts`, `web/src/components/*`):** First-strong-character Bidi direction detection (`detectTextDirection`), clean EN/AR RTL layout flip, and Vitest + Playwright test coverage on the UI/Postgres/Mailpit flow are well executed.
7. **Secret Hygiene (`.gitignore`, `.env.example`):** `.env`, audio caches, and binary artifacts are properly ignored; no real credentials exist in tracked files.

---

## Ordered Remediation Roadmap Before Publishing

1. **Phase A — Fix Release Blockers (CRIT-1 through CRIT-6) + 1 Live Voice Smoke Test:**
   - Move `mcp_pool.close` to `ctx.add_shutdown_callback(...)` (`CRIT-1`).
   - Remove LLM-callable `confirm_pending_action` / enforce out-of-band cross-turn voice or UI-only HITL confirmation and fix word-boundary matching in `classify_voice_decision()` (`CRIT-2`).
   - Pass explicit least-privilege `env` dicts in `MCPServerPool` (`CRIT-3`).
   - Forward `metrics_collected` / `error` and stream-scrub incrementally in `ScrubbedTTS` (`CRIT-4`).
   - Convert `sql_guard.py` to an explicit SQL function allowlist + mandatory table check + `statement_timeout` (`CRIT-5`).
   - Add Postgres service container (or integration mark split) in `.github/workflows/ci.yml` (`CRIT-6`).
   - Run one real human microphone WebRTC test in browser and capture live transcript + tool + Jaeger waterfall verification.
2. **Phase B — Fix Live Parity, Security Hardening & Honest Evals (`NORM-1` through `NORM-14`):**
   - Wire `GroqLLMRouter` into `agent/main.py` (`NORM-1`), fix `add_to_chat_ctx=False` fillers (`NORM-3`), pass `vad=None` to Speechmatics STT (`NORM-4`), fix `CancelledError`/barge-in wiring (`NORM-5`), unblock sync `psycopg` calls (`NORM-7`), fix `_extract_email_domain`/secret coverage/GitHub URL validation (`NORM-8`), fix `search_inbox`/calendar races/seed emails (`NORM-9`), fix waterfall math (`NORM-10`) and DataChannel transcript scrubbing (`NORM-11`), fix sandbox log OOM & client leaks (`NORM-13`), fix cross-platform Playwright/requirements (`NORM-12`) and React room/session isolation (`NORM-14`), then re-run and honestly report Layer 3 (`NORM-2`).
3. **Phase C — Polish (`LOW-1` through `LOW-4`):**
   - Bind Docker ports to `127.0.0.1` and pin image tags, align Cartesia voice config and documentation numbers, and clean up dead eval helpers and `.gitignore`.
