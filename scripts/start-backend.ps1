# Starts the recruiter-facing backend API (FastAPI) on http://localhost:8000
# Reads backend/.env. No build step — just runs backend/main.py.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
& "$root\.venv\Scripts\python.exe" "$root\backend\main.py"
