# Starts the AI/ML service (FastAPI) on http://localhost:8100 — resume scoring + realtime interview engine
# NOTE: must run as a single worker process — interview turn-taking state lives in-process per session.
# Reads ai_service/.env. No build step — just runs ai_service/main.py.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
& "$root\.venv\Scripts\python.exe" "$root\ai_service\main.py"
