# Setup & Optional Integrations

This guide covers full local setup and optional **GitHub** and **Google Workspace** integrations.

## 1. Environment Variables (`.env`)

Copy the template and add your keys:

```bash
cp .env.example .env
```

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

## 2. Connecting GitHub (All Repositories)

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

## 3. Connecting Google Workspace (Gmail & Calendar)

By default, emails go to local **Mailpit** (`http://localhost:8025`) and calendar events use local **PostgreSQL**. To connect a real Google account:

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **Gmail API** and **Google Calendar API**.
2. Create an **OAuth 2.0 Client ID** (Web application) with redirect URI:
   ```text
   http://127.0.0.1:8000/api/integrations/google/callback
   ```
3. Add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to `.env`, then restart `uvicorn` and `python -m agent.main dev`.
4. Open the Cockpit UI (`http://localhost:5173`) and click **Connect Google** (or visit `http://127.0.0.1:8000/api/integrations/google/auth-url`).
5. *(Optional)* Set `ALLOWED_EMAIL_DOMAINS=example.com,yourcompany.com` in `.env` to allow sending emails to additional domains.
