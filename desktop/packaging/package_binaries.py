#!/usr/bin/env python3
"""
Package your local, validated ``desktop/bin/<platform>/`` into archives to host
for CI. Upload the results to your download host so ``fetch_binaries.py`` can
pull them (LADOCK_BIN_BASE_URL) — this replaces re-downloading engines from
upstream.

For linux/mac the executable bit is forced on every file, so the archives are
correct even when packaged from Windows (where the source has no Unix perms).

Output: ``desktop/build/bin-archives/bin-<platform>.tar.gz``

Usage:
    python packaging/package_binaries.py                 # all present platforms
    python packaging/package_binaries.py linux mac
"""
from __future__ import annotations

import sys
import tarfile
from pathlib import Path

_DESKTOP = Path(__file__).resolve().parent.parent
_BIN = _DESKTOP / "bin"
_OUT = _DESKTOP / "build" / "bin-archives"


def _exec_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    # Force rwxr-xr-x so engines are runnable after extraction on Linux/macOS,
    # regardless of the packaging host. An exec bit on data files is harmless.
    ti.mode = 0o755
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    return ti


def package(platform: str) -> None:
    src = _BIN / platform
    if not src.is_dir():
        print(f"  ! bin/{platform} not found - skipping")
        return
    _OUT.mkdir(parents=True, exist_ok=True)
    out = _OUT / f"bin-{platform}.tar.gz"
    filt = _exec_filter if platform in ("linux", "mac") else None
    print(f"  packaging bin/{platform} -> {out.name} ...")
    with tarfile.open(out, "w:gz") as t:
        t.add(src, arcname=platform, filter=filt)
    print(f"    -> {out.stat().st_size / 1024 / 1024:.0f} MB")


def main() -> None:
    platforms = sys.argv[1:] or ["windows", "linux", "mac"]
    for p in platforms:
        if p not in ("windows", "linux", "mac"):
            raise SystemExit(f"Unknown platform '{p}' (windows|linux|mac)")
        package(p)
    print(f"\nArchives in {_OUT}")
    print("Upload them to your bin host (served at LADOCK_BIN_BASE_URL, "
          "default https://ladock.ladeep.id/bin).")


if __name__ == "__main__":
    main()
