# build.ps1 — build the standalone exe and the Windows installer.
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating venv..."
    python -m venv .venv
}

Write-Host "Installing dependencies + PyInstaller..."
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt pyinstaller

if (-not (Test-Path "assets\signature_overlay.ico")) {
    Write-Host "Generating icon..."
    & $py assets\gen_icon.py
}

Write-Host "Building standalone exe (PyInstaller)..."
& $py -m PyInstaller --noconfirm --clean WhatsThatSignature.spec

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Host "Inno Setup not found. Install it with:"
    Write-Host "    winget install JRSoftware.InnoSetup"
    Write-Host "(The standalone exe is ready in dist\WhatsThatSignature\.)"
    exit 1
}

Write-Host "Compiling installer (Inno Setup)..."
& $iscc installer\WhatsThatSignature.iss

Write-Host ""
Write-Host "Done."
Write-Host "  Standalone app : dist\WhatsThatSignature\WhatsThatSignature.exe"
Write-Host "  Installer       : installer\Output\WhatsThatSignature-Setup-1.0.0.exe"
