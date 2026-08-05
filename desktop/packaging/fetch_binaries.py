#!/usr/bin/env python3
"""
Fetch the maintainer's own pre-packaged bundled binaries for a release build.

Rather than rebuilding each engine from upstream (fragile — Scripps downtime,
changing apt package names,...), this downloads the exact ``bin/<platform>/`` set
you already validated, packaged as ONE archive per platform and hosted on your
own storage / download host.

Expected layout on the host (each archive has a top-level ``<platform>/`` dir):

    <BASE>/bin-windows.tar.gz     ->  bin/windows/...
    <BASE>/bin-linux.tar.gz       ->  bin/linux/...   (with or without ADFRsuite;
    <BASE>/bin-mac.tar.gz         ->  bin/mac/...       build_release.py prunes
                                                        ADFRsuite at stage time)

Use **.tar.gz** for linux/mac so executable bits are preserved (zip does not
preserve Unix permissions reliably).

Configure via environment (defaults shown):
    LADOCK_BIN_BASE_URL   base URL            (default: https://ladock.ladeep.id/bin)
    LADOCK_BIN_TOKEN      optional bearer token for private/authenticated storage

Usage:
    python packaging/fetch_binaries.py <platform>   # windows | linux | mac
    python packaging/fetch_binaries.py all
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

_BIN = Path(__file__).resolve().parent.parent / "bin"
# `or default` so an empty env var (e.g. an unset CI secret) falls back cleanly.
_BASE = (os.environ.get("LADOCK_BIN_BASE_URL") or "https://ladock.ladeep.id/bin").rstrip("/")
_TOKEN = (os.environ.get("LADOCK_BIN_TOKEN") or "").strip()


def _download(url: str, dest: Path) -> None:
    print(f"  downloading {url}")
    req = urllib.request.Request(url)
    if _TOKEN:
        req.add_header("Authorization", f"Bearer {_TOKEN}")
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    print(f"    -> {dest.stat().st_size / 1024 / 1024:.1f} MB")


def _extract(archive: Path, into: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(into)
    else:
        with tarfile.open(archive) as t:
            t.extractall(into)


def fetch_platform(platform: str) -> None:
    print(f"\n== {platform} ==")
    dest = _BIN / platform
    if dest.is_dir() and any(dest.iterdir()):
        print(f"  [ok] bin/{platform} already present - skipping")
        return
    _BIN.mkdir(parents=True, exist_ok=True)

    last_err: Exception | None = None
    for ext in (".tar.gz", ".tar.xz", ".zip"):
        url = f"{_BASE}/bin-{platform}{ext}"
        archive = _BIN / f"bin-{platform}{ext}"
        try:
            _download(url, archive)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  - not found ({exc})")
            continue
        print(f"  extracting {archive.name}...")
        _extract(archive, _BIN)
        archive.unlink(missing_ok=True)
        if not (dest.is_dir() and any(dest.iterdir())):
            raise SystemExit(
                f"Archive extracted but bin/{platform}/ is empty - the archive "
                f"must contain a top-level '{platform}/' folder.")
        print(f"  [ok] bin/{platform} ready")
        return

    raise SystemExit(
        f"Could not fetch bin/{platform} from {_BASE} "
        f"(tried .tar.gz/.tar.xz/.zip). Last error: {last_err}\n"
        f"Host your validated binaries as <BASE>/bin-{platform}.tar.gz, or set "
        f"LADOCK_BIN_BASE_URL / LADOCK_BIN_TOKEN.")


def main() -> None:
    platforms = sys.argv[1:] or ["all"]
    if "all" in platforms:
        platforms = ["windows", "linux", "mac"]
    for p in platforms:
        if p not in ("windows", "linux", "mac"):
            raise SystemExit(f"Unknown platform '{p}' (windows|linux|mac|all)")
        fetch_platform(p)
    print("\nDone.")


if __name__ == "__main__":
    main()
