<#
  autoapply setup for Windows 11.
  Run from the repo root in PowerShell:
      powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
  (-ExecutionPolicy Bypass applies to this one invocation only; nothing is changed system-wide.)
#>
[CmdletBinding()]
param([switch]$UseEdge)  # use installed Microsoft Edge instead of downloading Chromium

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Test-Command($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

Write-Host "==> Checking prerequisites" -ForegroundColor Cyan
if (-not (Test-Command git)) {
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
}
if (-not (Test-Command uv)) {
    winget install --id astral-sh.uv -e --source winget --accept-package-agreements --accept-source-agreements
    # Refresh PATH for this session so `uv` resolves without reopening the terminal.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "==> Installing Python + dependencies (uv manages its own Python)" -ForegroundColor Cyan
uv python install 3.12
uv sync

if ($UseEdge) {
    Write-Host "==> Using Microsoft Edge: set  browser.channel: msedge  in config\config.yaml" -ForegroundColor Yellow
} else {
    Write-Host "==> Downloading Playwright's Chromium" -ForegroundColor Cyan
    uv run playwright install chromium
}

Write-Host "==> Creating config files" -ForegroundColor Cyan
uv run autoapply init

if (Test-Path .git) {
    Write-Host "==> Installing git pre-commit hooks (secret + resume guard)" -ForegroundColor Cyan
    uv run --with pre-commit pre-commit install
}

uv run autoapply doctor
Write-Host @"

Done. Notes:
  * Toast notifications appear in the Windows notification center; keep Focus Assist off while running.
  * If 'scripts are disabled on this system' appears for later scripts, either keep using
    -ExecutionPolicy Bypass per run, or allow local scripts for your user only:
        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
"@ -ForegroundColor Green
