# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for LADOCK Desktop (one-dir build).

Driven by packaging/build_release.py, which sets:
  LADOCK_STAGE   — the staged distribution dir (source + selected bin/)
  LADOCK_VERSION — windows | windows-hybrid | linux | mac

Run via:  pyinstaller packaging/ladock.spec --noconfirm
(usually on CI — see .github/workflows/build-installers.yml)

NOTE: freezing PySide6-WebEngine + RDKit + Meeko is finicky and typically needs
a round or two of iteration on the real runner (missing hidden imports / data).
"""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

STAGE = Path(os.environ.get("LADOCK_STAGE", ".")).resolve()
VERSION = os.environ.get("LADOCK_VERSION", "windows")

# Bundle resources at the app root so core.resources.resource_root() finds them.
datas = [
    (str(STAGE / "bin"), "bin"),
    (str(STAGE / "config"), "config"),
    (str(STAGE / "gui" / "assets"), "gui/assets"),
    (str(STAGE / "LADOCK_VERSION.txt"), "."),
]
binaries = []
hiddenimports = collect_submodules("meeko") + collect_submodules("rdkit")

for pkg in ("rdkit", "meeko", "gemmi"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

block_cipher = None

a = Analysis(
    [str(STAGE / "main.py")],
    pathex=[str(STAGE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtQuick3D"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LADOCK",
    console=False,
    icon=str(STAGE / "gui" / "assets" / "ladock.ico")
        if (STAGE / "gui" / "assets" / "ladock.ico").exists() else None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="LADOCK",
)
