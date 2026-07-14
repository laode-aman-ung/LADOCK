#!/usr/bin/env python3
"""
Fetch bundled binaries for a LADOCK release build.

Downloads docking engines and tools from official sources into
desktop/bin/<platform>/.  Run this on CI before building an installer.

Usage:
    python packaging/fetch_binaries.py <platform>   # windows | linux | mac
    python packaging/fetch_binaries.py all           # all three
"""

import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

_BIN = Path(__file__).resolve().parent.parent / "bin"

# ── URLs ────────────────────────────────────────────────────────────────────
VINA_BASE = "https://github.com/ccsb-scripps/AutoDock-Vina/releases/download/v1.2.7"
VINA_FILES = {
    "windows": ["vina_1.2.7_win.exe", "vina_split_1.2.7_win.exe"],
    "linux":   ["vina_1.2.7_linux_x86_64", "vina_split_1.2.7_linux_x86_64"],
    "mac":     ["vina_1.2.7_mac_x86_64", "vina_split_1.2.7_mac_x86_64"],
}

ADGPU_BASE = "https://github.com/ccsb-scripps/AutoDock-GPU/releases/download/v1.6"
ADGPU_FILES = {
    "linux": ["adgpu-v1.6_linux_x64_cuda12_128wi", "adgpu_analysis-v1.6_linux_x64"],
}

MGLTOOLS_URL = (
    "http://mgltools.scripps.edu/downloads/downloads/tars/releases/"
    "REL1.5.6/mgltools_x86_64Linux2_1.5.6.tar.gz"
)


def _download(url: str, dest: Path) -> None:
    import urllib.request
    print(f"  ↓ {url}")
    urllib.request.urlretrieve(url, dest)
    print(f"    → {dest}  ({dest.stat().st_size / 1024 / 1024:.1f} MB)")


def _chmodx(path: Path) -> None:
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── Vina ─────────────────────────────────────────────────────────────────────
def fetch_vina(platform: str) -> None:
    dest = ensure_dir(_BIN / platform)
    for fname in VINA_FILES[platform]:
        url = f"{VINA_BASE}/{fname}"
        out = dest / fname
        if out.exists():
            print(f"  ✓ {fname} already exists")
            continue
        _download(url, out)
        if platform != "windows":
            _chmodx(out)


# ── AutoDock-GPU (Linux only) ────────────────────────────────────────────────
def fetch_adgpu() -> None:
    dest = ensure_dir(_BIN / "linux")
    for fname in ADGPU_FILES["linux"]:
        url = f"{ADGPU_BASE}/{fname}"
        out = dest / fname
        if out.exists():
            print(f"  ✓ {fname} already exists")
            continue
        _download(url, out)
        _chmodx(out)


# ── AutoDock4 + AutoGrid4 (Linux only, via apt on Ubuntu) ────────────────────
def fetch_autodock4() -> None:
    dest = ensure_dir(_BIN / "linux")
    # Ensure universe repo is available
    subprocess.run(["sudo", "add-apt-repository", "-y", "universe"],
                   capture_output=True)
    subprocess.run(["sudo", "apt-get", "update"], capture_output=True)
    for exe in ("autodock4", "autogrid4"):
        out = dest / exe
        if out.exists():
            print(f"  ✓ {exe} already exists")
            continue
        try:
            subprocess.run(["sudo", "apt-get", "install", "-y", exe],
                           check=True, capture_output=True)
            path = shutil.which(exe)
            if path:
                shutil.copy2(path, out)
                print(f"  ✓ {exe} copied from {path}")
        except Exception as e:
            print(f"  ! apt-get {exe} failed: {e}")
            print(f"  ! place {exe} manually in {dest}")


# ── MGLTools 1.5.6 (Linux only) ──────────────────────────────────────────────
def fetch_mgltools() -> None:
    dest = ensure_dir(_BIN / "linux")
    mgldir = dest / "MGLTools-1.5.6"
    if mgldir.exists():
        print(f"  ✓ MGLTools-1.5.6 already exists")
        return
    tarball = dest / "mgltools_x86_64Linux2_1.5.6.tar.gz"
    if not tarball.exists():
        _download(MGLTOOLS_URL, tarball)
    print("  Extracting MGLTools (this may take a while)…")
    tarfile.open(tarball).extractall(path=dest)
    # The tarball creates mgltools_x86_64Linux2_1.5.6/ — rename
    extracted = dest / "mgltools_x86_64Linux2_1.5.6"
    if extracted.exists():
        extracted.rename(mgldir)
    tarball.unlink()
    # The install.sh script sets up internal links — run it
    installer = mgldir / "install.sh"
    if installer.exists():
        subprocess.run(["bash", str(installer)], cwd=str(mgldir),
                       check=True, capture_output=True)
    print(f"  ✓ MGLTools-1.5.6  ({sum(f.stat().st_size for f in mgldir.rglob('*') if f.is_file()) / 1024 / 1024:.0f} MB)")


# ── Platform dispatch ────────────────────────────────────────────────────────
def fetch_platform(platform: str) -> None:
    print(f"\n── {platform} ──")
    fetch_vina(platform)
    if platform == "linux":
        fetch_adgpu()
        fetch_autodock4()
        fetch_mgltools()


def main():
    platforms = sys.argv[1:]
    if not platforms or "all" in platforms:
        platforms = ["windows", "linux", "mac"]
    for p in platforms:
        fetch_platform(p)
    print("\nDone.")


if __name__ == "__main__":
    main()
