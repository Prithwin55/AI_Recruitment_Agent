# Starts the recruiter portal / candidate meeting frontend on http://localhost:5173
$ErrorActionPreference = "Stop"

# Refresh PATH from the registry in case this shell predates a Node.js install
# (installers update the registry, not already-running processes' PATH).
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "npm still not found after refreshing PATH. Install Node.js LTS, then open a brand-new terminal (or restart VS Code) so it picks up the updated PATH."
    exit 1
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\frontend"
npm run dev
