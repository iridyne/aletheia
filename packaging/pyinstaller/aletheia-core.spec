# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder specification for the packaged Aletheia runtime.

The output intentionally remains a console-capable executable. The Rust tray starts it
without a console window on Windows, while terminal users keep normal stdout/stderr for
`aletheia` CLI commands.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]

hiddenimports = collect_submodules("aletheia")
# These packages contain runtime-selected backends/plugins which static import analysis
# does not always discover. Keep the list narrow and validate the real frozen payload in
# CI instead of collecting every installed development package.
for package in ("keyring.backends", "ccxt", "yfinance", "feedparser"):
    hiddenimports.extend(collect_submodules(package))

_datas = [
    (str(ROOT / "aletheia" / "dashboard_static"), "aletheia/dashboard_static"),
]
for package in ("certifi", "keyring", "ccxt"):
    _datas.extend(collect_data_files(package, include_py_files=False))

analysis = Analysis(
    [str(ROOT / "scripts" / "release" / "pyinstaller_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=_datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "hypothesis", "ruff", "tkinter"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="aletheia-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="aletheia-core",
)
