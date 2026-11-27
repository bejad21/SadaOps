# Setup & Optional Integrations

This guide walks through setting up SadaOps from a fresh clone, plus optional **GitHub** and **Google Workspace** integrations.

## 1. Fresh Clone & Local Setup

### Prerequisites
- **Docker Desktop** (running)
- **Python 3.11+**
- **Node.js 20+**

### Step-by-Step

1. **Clone the repository and create `.env`**
   ```bash
   git clone https://github.com/bejad21/SadaOps.git
   cd SadaOps
   cp .env.example .env
   ```
   Edit `.env` and add at minimum `GROQ_API_KEY` and `GLADIA_API_KEY` (or `SPEECHMATICS_API_KEY`).

2. **Build the Python sandbox image and start Docker services**
   ```bash
   docker build -t sadaops-python-sandbox:latest infra/sandbox
   docker compose -f infra/docker-compose.yml up -d --wait
   ```
   This starts LiveKit (`:7880`), PostgreSQL (`:5432`), Qdrant (`:6333`), Jaeger (`:16686`), and Mailpit (`:8025`).

3. **Create a Python virtual environment and install dependencies**
   ```bash
   python -m venv .venv
   # Windows PowerShell: .\.venv\Scripts\Activate.ps1
   # macOS / Linux:      source .venv/bin/activate
   pip install -r requirements.txt
   python -m livekit.agents download-files
   ```

4. **Install frontend dependencies**
   ```bash
   cd web && npm ci && cd ..
   ```

5. **Launch all 3 services (3 terminals, with `.venv` active in Terminals 1 & 2)**
   ```bash
   # Terminal 1: FastAPI control plane
   uvicorn api.app:app --reload --port 8000

   # Terminal 2: LiveKit Voice & MCP agent worker
   python -m agent.main dev

   # Terminal 3: React Cockpit UI
   cd web && npm run dev
   ```

6. **Verify it is running**
   - Open **http://localhost:5173** and confirm **Infra Healthy** is green.
   - Optional health check: `http://localhost:8000/health`

---

## 2. Environment Variables (`.env`)

### Required Keys

| Variable | Purpose | Where to Get It |
|---|---|---|
| `GROQ_API_KEY` | LLM tool planning & fallback Whisper STT | [Groq Console](https://console.groq.com/) |
| `GLADIA_API_KEY` *(or `SPEECHMATICS_API_KEY`)* | Bilingual Arabic + English speech-to-text | [Gladia](https://www.gladia.io/) / [Speechmatics](https://www.speechmatics.com/) |

### Optional Keys

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY_2` | empty | Second Groq key for automatic `429` rate-limit failover |
| `CARTESIA_API_KEY` | empty | Ultra-low-latency TTS (`TTS_PROVIDER=cartesia`; defaults to free `edge`) |
| `GITHUB_MODE` / `GITHUB_TOKEN` | `fixture` | Live GitHub access across your repos (`GITHUB_MODE=live`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | empty | Live Gmail & Google Calendar OAuth2 |

---

## 3. Connecting GitHub (All Repositories)

By default, SadaOps runs in offline `fixture` mode. To connect your real GitHub account:

1. Create a GitHub Personal Access Token (classic) with the **`repo`** scope (and `read:user` if needed).
2. Update `.env`:
   ```env
   GITHUB_MODE=live
   GITHUB_TOKEN=ghp_your_token_here
   GITHUB_REPO=
   GITHUB_ALLOWED_REPOS=*
   ```
   - Leave `GITHUB_REPO=` empty and set `GITHUB_ALLOWED_REPOS=*` to query all repositories your token can access.
   - To restrict access to a single repository, set `GITHUB_REPO=owner/repo` and `GITHUB_ALLOWED_REPOS=owner/repo`.
3. Restart `uvicorn` and `python -m agent.main dev`.

---

## 4. Connecting Google Workspace (Gmail & Calendar)

By default, emails go to local **Mailpit** (`http://localhost:8025`) and calendar events use local **PostgreSQL**. To connect a real Google account:

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **Gmail API** and **Google Calendar API**.
2. Create an **OAuth 2.0 Client ID** (Web application) with redirect URI:
   ```text
   http://127.0.0.1:8000/api/integrations/google/callback
   ```
3. Add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to `.env`, then restart `uvicorn` and `python -m agent.main dev`.
4. Open the Cockpit UI (`http://localhost:5173`) and click **Connect Google** (or visit `http://127.0.0.1:8000/api/integrations/google/auth-url`).
5. *(Optional)* Set `ALLOWED_EMAIL_DOMAINS=example.com,yourcompany.com` in `.env` to allow sending emails to additional domains.
