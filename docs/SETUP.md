# Setup

## Prerequisites

- **Node.js LTS** — frontend build tooling.
- **Python 3.11 or 3.12** — the backend and ai_service are pinned to this range. A repo-root `.venv` (created with `py -3.12 -m venv .venv`) is used by both services; the system's default Python may be newer and isn't used directly, since some native-wheel dependencies (e.g. `azure-cognitiveservices-speech`) lag behind brand-new Python releases.

## First-time install

```powershell
# Python deps (shared package + backend + ai_service), from repo root
.venv\Scripts\pip install -e .\shared
.venv\Scripts\pip install -r backend\requirements.txt
.venv\Scripts\pip install -r ai_service\requirements.txt

# Frontend deps
cd frontend
npm install
cd ..
```

## Configuration

Copy `.env.example` to `.env` in the repo root and fill in real values. Both `backend` and `ai_service` read this single root `.env` — see `shared/shared/config.py`.

Required for full functionality:
- `ANTHROPIC_API_KEY` — Claude, used for resume scoring, the live interview conversation, and post-interview scoring.
- `DEEPGRAM_API_KEY` — English interview STT/TTS.
- `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` — Arabic-Omani (`ar-OM`) interview STT/TTS.
- `SMTP_*` — candidate interview-link emails. Works with any SMTP provider (Gmail app password, SendGrid SMTP relay, Office365, etc).
- `GOOGLE_SERVICE_ACCOUNT_FILE` / `GOOGLE_CALENDAR_ID` — Google Calendar invites (optional; email still sends without this — see Calendar gotcha below).

The app runs without any of these set — you just won't be able to exercise the features that depend on them (Phase 1 scoring, live interviews, emails, calendar invites) until they're filled in.

### Google Calendar gotcha

A Google service account can create events with attendees and trigger invite emails (`sendUpdates=all`) on any calendar it's been shared "Make changes to events" access to — it does **not** need domain-wide delegation for that. However, some Google Workspace organizations restrict service accounts from inviting *external* attendees without domain-wide delegation and admin consent. If calendar invites fail to reach candidates in your org, that's the likely cause — the fallback is a recruiter-authorized OAuth calendar connection instead of a service account (not yet built; ask for it if you hit this wall). Calendar failures never block the candidate email — the join link still sends regardless, since email is the load-bearing notification.

## Running in dev

```powershell
# All three services, each in its own window:
scripts\start-all.ps1

# Or individually:
scripts\start-backend.ps1      # http://localhost:8000
scripts\start-ai-service.ps1   # http://localhost:8100 (single worker — see note in the script)
scripts\start-frontend.ps1     # http://localhost:5173
```

On first backend startup, a default recruiter account is seeded and printed to the console:

```
email:    admin@company.local  (DEFAULT_ADMIN_EMAIL in .env)
password: ChangeMe123!         (DEFAULT_ADMIN_PASSWORD in .env)
```

You'll be required to change this password on first login.

### Super admin panel (`/admin`)

Separate from recruiter login. Open **http://localhost:5173/admin** and sign in with:

```
username: admin              (ADMIN_USERNAME in .env)
password: <your password>    (ADMIN_PASSWORD in .env)
```

Login stays **disabled** until `ADMIN_PASSWORD` is set to a non-empty value.

Use this panel to:

1. **Create an organization** — subdomain (slug), name, and a default recruiter email.
2. Hand the recruiter the one-time password and workspace URL (`http://{slug}.localhost:5173` in dev).
3. View system-wide usage & ₹ cost (date-filterable).

Recruiter accounts log in only on their workspace host — they cannot create other users. Provisioning is super-admin only.

The usage dashboard shows cost for:

| Service | What is metered | Cost formula (env rate) | USD reference → default ₹ at 86/$ |
|---------|-----------------|-------------------------|-----------------------------------|
| AI agent (Claude) | input + output tokens | tokens/1e6 × `PRICE_INR_LLM_PER_MTOK_*` | $3 / $15 per 1M in/out → ₹258 / ₹1290 |
| STT | audio minutes | minutes × `PRICE_INR_STT_PER_MINUTE` | $0.0154/min → ₹1.3244 |
| TTS | characters spoken | chars/1000 × `PRICE_INR_TTS_PER_1K_CHARS` | $0.10/1k chars → ₹8.60 |
| OCR | vision-parsed resume pages | pages/1000 × `PRICE_INR_OCR_PER_1K_PAGES` | $1.50/1k pages → ₹129 |

Rates are applied at **read time** — changing a `PRICE_INR_*` value re-prices all history. Usage rows are written as resume scoring and interviews run (empty dashboard = nothing metered yet).

## Data storage

Everything is stored on the local filesystem under `storage/` (no S3/MinIO, per product requirement): `storage/app.db` (SQLite), `storage/resumes/`, `storage/recordings/`, `storage/transcripts/`. This directory is git-ignored.
