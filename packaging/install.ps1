<#
  autoapply installer for Windows 11 (release bundle).
  Right-click the folder > Open in Terminal, then:
      powershell -ExecutionPolicy Bypass -File .\install.ps1
  -SkipBrowser   don't download Chromium now (run `autoapply install-browser` later)
#>
[CmdletBinding()]
param([switch]$SkipBrowser)

# Native tools (uv) write progress to stderr; check exit codes instead of using "Stop",
# which turns that stderr into terminating errors on Windows PowerShell 5.1.
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$wheel = Get-ChildItem -Path $here -Filter "autoapply-*.whl" | Select-Object -First 1
if (-not $wheel) { throw "No autoapply wheel found next to install.ps1" }
Write-Host "==> autoapply installer ($($wheel.Name))" -ForegroundColor Cyan

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> Installing uv (Python package manager; it also provides Python itself)" -ForegroundColor Cyan
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv installation failed" }
}

Write-Host "==> Installing autoapply as an isolated tool" -ForegroundColor Cyan
uv tool install --force --python 3.12 $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "uv tool install failed" }
uv tool update-shell 2>$null | Out-Null
$bin = (uv tool dir --bin).Trim()
$env:Path = "$bin;$env:Path"

if (-not $SkipBrowser) {
    Write-Host "==> Downloading Chromium for Playwright" -ForegroundColor Cyan
    autoapply install-browser
    if ($LASTEXITCODE -ne 0) { throw "Chromium download failed" }
}

Write-Host "==> Creating your workspace ($env:USERPROFILE\autoapply)" -ForegroundColor Cyan
autoapply init
if ($LASTEXITCODE -ne 0) { throw "autoapply init failed" }

Write-Host "==> Adding autoapply to the Start menu and desktop" -ForegroundColor Cyan
$gui = Join-Path $bin "autoapply-gui.exe"
if (-not (Test-Path $gui)) { throw "autoapply-gui.exe was not installed" }
$shell = New-Object -ComObject WScript.Shell
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "autoapply.lnk"
$desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "autoapply.lnk"
foreach ($path in @($startMenu, $desktop)) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $gui
    $lnk.WorkingDirectory = Join-Path $env:USERPROFILE "autoapply"
    $lnk.Description = "Job application assistant"
    $lnk.Save()
}

Write-Host @"

Installed. Open "autoapply" from the Start menu or your desktop.
Start on the "Your profile" page: add your resume and fill in your details.
Command line is still available: autoapply --help (open a NEW terminal first).
Upgrade: run a newer bundle's install.ps1.
Uninstall: uv tool uninstall autoapply, then delete the two autoapply shortcuts.
"@ -ForegroundColor Green
