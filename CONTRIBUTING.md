# Contributing to SadaOps

Thanks for contributing! Keep changes focused, well-tested, and aligned with the project's safety and style standards.

## Development Setup

```bash
# 1. Start local infrastructure (LiveKit, Postgres, Qdrant, Jaeger, Mailpit)
docker build -t sadaops-python-sandbox:latest infra/sandbox
docker compose -f infra/docker-compose.yml up -d --wait

# 2. Install Python dependencies & download local VAD/turn-detector models
python -m venv .venv
# Windows: .venv\Scripts\pip install -r requirements.txt && .venv\Scripts\python -m livekit.agents download-files
# Linux/macOS: .venv/bin/pip install -r requirements.txt && .venv/bin/python -m livekit.agents download-files

# 3. Install frontend dependencies
cd web && npm ci && cd ..
```

## Running Checks Before Opening a PR

```bash
# Python lint & PEP 257 docstring checks
ruff check .
ruff check --select D --ignore D104,D100,D103,D102,D101,D105,D107 .

# Backend unit & integration tests
pytest -v

# Frontend unit tests, typecheck & E2E tests
cd web
npm test -- --run
npx tsc --noEmit
npx playwright test
```

## Coding & Documentation Guidelines

- **Python:** Follow PEP 8, PEP 257 docstrings (Google style `Args:` / `Returns:` where non-obvious), and explain *why* in `#` comments rather than narrating *what* the code does.
- **Safety:** Never bypass `HITLGate`, `sql_guard.py`, `prompt_guard.py`, or `tts_scrubber.py` controls, and never commit `.env` or real credentials.
- **Docs:** Update `README.md` or `docs/` if you add environment variables, tools, or setup steps.
