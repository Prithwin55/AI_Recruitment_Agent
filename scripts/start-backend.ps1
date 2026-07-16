# Starts the recruiter-facing backend API (FastAPI) on http://localhost:8000
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
& "$root\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000 --app-dir "$root\backend"
