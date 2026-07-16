# Starts the AI/ML service (FastAPI) on http://localhost:8100 — resume scoring + realtime interview engine
# NOTE: must run as a single worker process — interview turn-taking state lives in-process per session.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
& "$root\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8100 --app-dir "$root\ai_service"
