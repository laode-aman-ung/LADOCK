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

# Bundle resources at the app root, which is what ladock.paths resolves to when
# frozen: bin_root() -> _MEIPASS/bin, desktop_resource_root() -> _MEIPASS.
_DESKTOP_PKG = STAGE / "ladock" / "desktop"
datas = [
    (str(STAGE / "bin"), "bin"),
    (str(_DESKTOP_PKG / "config"), "config"),
    (str(_DESKTOP_PKG / "gui" / "assets"), "gui/assets"),
    (str(STAGE / "LADOCK_VERSION.txt"), "."),
]
binaries = []
hiddenimports = (collect_submodules("meeko") + collect_submodules("rdkit")
                 + collect_submodules("ladock"))

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
    [str(STAGE / "ladock_main.py")],
    pathex=[str(STAGE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # LADOCK has no database, no cloud client and no docs build, but a rich
    # conda environment drags all three into the graph through pandas' optional
    # dependencies. Excluding them shrinks the bundle and, in the sqlalchemy
    # case, is required: PyInstaller's hook imports it to enumerate dialects,
    # and SQLAlchemy < 2.0.31 cannot be imported at all under Python 3.13.
    # LADOCK needs Qt, RDKit, Meeko, NumPy, pandas and SciPy — nothing else. A
    # rich conda environment drags in far more through optional dependencies and
    # PyInstaller hooks; on the first successful build that was 4.7 GB of CUDA,
    # PyTorch, JAX, Arrow and OpenCV in a 6.4 GB app. Excluding them is not an
    # optimisation, it is the difference between a shippable installer and one
    # nobody can download. sqlalchemy additionally *must* go: its hook imports
    # it, and SQLAlchemy < 2.0.31 cannot be imported at all under Python 3.13.
    excludes=[
        "tkinter", "PySide6.QtQuick3D",
        "sqlalchemy", "botocore", "boto3", "sphinx", "IPython", "notebook",
        "torch", "triton", "jax", "jaxlib", "nvidia", "pyarrow", "cv2",
        "tensorflow", "matplotlib", "sklearn", "numba", "dask", "zmq",
    ],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LADOCK",
    console=False,
    icon=str(_DESKTOP_PKG / "gui" / "assets" / "ladock.ico")
        if (_DESKTOP_PKG / "gui" / "assets" / "ladock.ico").exists() else None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="LADOCK",
)
