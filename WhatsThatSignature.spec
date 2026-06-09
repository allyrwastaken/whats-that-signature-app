# -*- mode: python ; coding: utf-8 -*-
# Build:  python -m PyInstaller WhatsThatSignature.spec
import os
from PyInstaller.utils.hooks import collect_all

# winsdk (PyWinRT) loads its WinRT submodules dynamically — collect everything
# so the Windows OCR APIs are present in the frozen build.
datas, binaries, hiddenimports = [], [], []
for pkg in ("winsdk", "pystray"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Ship the icon so the app window can use it on its title bar / taskbar.
datas += [("assets/signature_overlay.ico", "assets")]

a = Analysis(
    ["overlay.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WhatsThatSignature",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # off: avoids antivirus false positives
    console=False,                  # windowed; diagnostics go to overlay.log
    icon="assets/signature_overlay.ico",
    uac_admin=False,                # app self-elevates at runtime (see ensure_elevated)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="WhatsThatSignature",
)
