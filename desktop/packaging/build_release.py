#!/usr/bin/env python3
"""
LADOCK release builder — stage a per-version distribution and (optionally) freeze
it with PyInstaller. Native OS installers are then produced by the per-OS scripts
in packaging/<os>/ (driven by .github/workflows/build-installers.yml on CI).

Versions
--------
  windows          Vina / Vinardo only (bin/windows)               ~small
  windows-hybrid   + Linux engines dispatched to WSL (bin/linux)    ~medium
  linux            all engines native (bin/linux)                   ~medium
  mac              Vina / Vinardo only (bin/mac)                    ~small

By default the bulky, currently-unused ADFRsuite (~500 MB, only ADFR/AGFR +
obabel-fallback) is excluded. Pass --with-adfrsuite to include it.

Usage
-----
  python packaging/build_release.py <version> [--stage-only] [--with-adfrsuite]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_DESKTOP = Path(__file__).resolve().parent.parent          # desktop/
_BIN = _DESKTOP / "bin"
_BUILD = _DESKTOP / "build"                                # staging + dist root

# Which bin/<platform> folders each version needs.
_VERSION_PLATFORMS: dict[str, list[str]] = {
    "windows":        ["windows"],
    "windows-hybrid": ["windows", "linux"],
    "linux":          ["linux"],
    "mac":            ["mac"],
}

# Source packages copied into every distribution.
_SRC_DIRS = ["app", "core", "data", "engine", "gui", "config"]
_SRC_FILES = ["main.py", "ladock_entry.py", "pyproject.toml",
              "requirements.txt", "README.md"]

# Sub-trees pruned from bin/linux unless --with-adfrsuite.
_LINUX_HEAVY_OPTIONAL = ["ADFRsuite-1.0"]


def _copytree(src: Path, dst: Path, ignore=None):
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore,
                    symlinks=True)


def stage(version: str, with_adfrsuite: bool) -> Path:
    """Assemble build/stage-<version>/ with source + the right bin subset."""
    if version not in _VERSION_PLATFORMS:
        raise SystemExit(f"Unknown version '{version}'. "
                         f"Choose from: {', '.join(_VERSION_PLATFORMS)}")
    stage_dir = _BUILD / f"stage-{version}"
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    # Source
    for name in _SRC_DIRS:
        src = _DESKTOP / name
        if src.is_dir():
            _copytree(src, stage_dir / name,
                      ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in _SRC_FILES:
        src = _DESKTOP / name
        if src.is_file():
            shutil.copy2(src, stage_dir / name)

    # Binaries (only the platforms this version needs)
    bin_out = stage_dir / "bin"
    bin_out.mkdir(exist_ok=True)
    for platform_name in _VERSION_PLATFORMS[version]:
        src = _BIN / platform_name
        if not src.is_dir():
            print(f"  ! missing bin/{platform_name} — skipping "
                  f"(fetch binaries before building a real release)")
            continue
        drop = set() if with_adfrsuite else set(_LINUX_HEAVY_OPTIONAL)
        ign = shutil.ignore_patterns(*drop) if drop else None
        _copytree(src, bin_out / platform_name, ignore=ign)

    _write_manifest(stage_dir, version)
    print(f"Staged: {stage_dir}  ({_dir_size_mb(stage_dir):.0f} MB)")
    return stage_dir


def _write_manifest(stage_dir: Path, version: str):
    engines = {
        "windows": "Vina, Vinardo",
        "windows-hybrid": "Vina, Vinardo, AD4, AD-GPU (AD4/AD-GPU via WSL)",
        "linux": "Vina, Vinardo, AD4, AD-GPU",
        "mac": "Vina, Vinardo",
    }[version]
    (stage_dir / "LADOCK_VERSION.txt").write_text(
        f"LADOCK Desktop — {version} build\nScoring functions: {engines}\n",
        encoding="utf-8")


def _dir_size_mb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total / (1024 * 1024)


def freeze(stage_dir: Path, version: str) -> Path:
    """Run PyInstaller on the staged app. Produces build/dist-<version>/."""
    try:
        import PyInstaller  # noqa: F401
    except Exception:
        raise SystemExit(
            "PyInstaller is not installed. `pip install pyinstaller`, or run this "
            "on CI (see .github/workflows/build-installers.yml).")
    dist = _BUILD / f"dist-{version}"
    work = _BUILD / f"work-{version}"
    spec = Path(__file__).resolve().parent / "ladock.spec"
    env = dict(os.environ, LADOCK_STAGE=str(stage_dir), LADOCK_VERSION=version)
    cmd = [sys.executable, "-m", "PyInstaller", str(spec),
           "--noconfirm", "--distpath", str(dist), "--workpath", str(work)]
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)
    print(f"Frozen app: {dist}")
    return dist


def main():
    ap = argparse.ArgumentParser(description="LADOCK release builder")
    ap.add_argument("version", choices=list(_VERSION_PLATFORMS))
    ap.add_argument("--stage-only", action="store_true",
                    help="assemble files but do not freeze with PyInstaller")
    ap.add_argument("--with-adfrsuite", action="store_true",
                    help="include the bulky ADFRsuite (ADFR/AGFR + obabel)")
    args = ap.parse_args()

    stage_dir = stage(args.version, args.with_adfrsuite)
    if not args.stage_only:
        freeze(stage_dir, args.version)


if __name__ == "__main__":
    main()
