# Starts backend, ai_service, and frontend each in their own PowerShell window.
$ErrorActionPreference = "Stop"
$scripts = Split-Path -Parent $MyInvocation.MyCommand.Path

Start-Process powershell -ArgumentList "-NoExit", "-File", "$scripts\start-backend.ps1"
Start-Process powershell -ArgumentList "-NoExit", "-File", "$scripts\start-ai-service.ps1"
Start-Process powershell -ArgumentList "-NoExit", "-File", "$scripts\start-frontend.ps1"

Write-Host "Started backend (http://localhost:8000), ai_service (http://localhost:8100), frontend (http://localhost:5173) in separate windows."
