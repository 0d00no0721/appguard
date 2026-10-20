# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for AppGuard 0.4.0."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
WEB = ROOT / "web"
ICON = ROOT / "appguard.ico"
ENTRY = ROOT / "packaging" / "entry.py"
VERSION_FILE = ROOT / "packaging" / "version_info.txt"

hiddenimports = [
    "appguard_launcher",
    "appguard_web",
    "win_tray",
    "win32com.client",
    "pythoncom",
    "pywintypes",
    "win32timezone",
    "win32file",
    "win32pipe",
    "win32security",
    "win32api",
    "win32event",
]
hiddenimports += collect_submodules("appguard")

a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC), str(TOOLS)],
    binaries=[],
    datas=[(str(WEB), "web"), (str(ICON), ".")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "tests", "ui", "matplotlib", "numpy", "pandas", "pytest",
              "pystray", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AppGuard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON),
    version=str(VERSION_FILE),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AppGuard",
)
