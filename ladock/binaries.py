#!/usr/bin/env python3
"""Fetch the pre-packaged engine binaries into ``ladock/bin/<platform>/``.

The docking engines (Vina, AutoDock4/AutoGrid4, AutoDock-GPU, MGLTools) are far
too large to ship inside the wheel, so ``pip install ladock`` installs the code
and this command pulls the binaries afterwards::

    ladock-fetch-binaries                    # the platform you are running on
    ladock-fetch-binaries linux mac          # explicit
    ladock-fetch-binaries all
    ladock-fetch-binaries --fix-permissions  # repair a tree that lost +x

Rather than rebuilding each engine from upstream (fragile — Scripps downtime,
changing apt package names, …), this downloads the exact ``bin/<platform>/`` set
the maintainer already validated, packaged as ONE archive per platform.

Expected layout on the host (each archive has a top-level ``<platform>/`` dir)::

    <BASE>/bin-windows.tar.gz     ->  bin/windows/...
    <BASE>/bin-linux.tar.gz       ->  bin/linux/...   (with or without ADFRsuite;
    <BASE>/bin-mac.tar.gz         ->  bin/mac/...       build_release.py prunes
                                                        ADFRsuite at stage time)

Use **.tar.gz** for linux/mac so executable bits are preserved (zip does not
preserve Unix permissions reliably).

Configure via environment (defaults shown):
    LADOCK_BIN_BASE_URL   base URL   (default: https://ladock.ladeep.id/bin)
    LADOCK_BIN_TOKEN      optional bearer token for private storage
    LADOCK_BIN            install binaries here instead of inside the package
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from ladock.paths import bin_root, platform_name

PLATFORMS = ("windows", "linux", "mac")

# Default to the GitHub release assets: they need no separate web host, no
# certificate to keep alive, and they are already public. `or default` so an
# empty env var (e.g. an unset CI secret) falls back cleanly.
_DEFAULT_BASE = "https://github.com/laode-aman-ung/LADOCK/releases/download/v0.3.0"
_BASE = (os.environ.get("LADOCK_BIN_BASE_URL") or _DEFAULT_BASE).rstrip("/")
_TOKEN = (os.environ.get("LADOCK_BIN_TOKEN") or "").strip()


def _download(url: str, dest: Path) -> None:
    print(f"  downloading {url}")
    req = urllib.request.Request(url)
    if _TOKEN:
        req.add_header("Authorization", f"Bearer {_TOKEN}")
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    print(f"    -> {dest.stat().st_size / 1024 / 1024:.1f} MB")


def _is_executable_payload(path: Path) -> bool:
    """True for ELF/Mach-O binaries and shebang scripts — things meant to run."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    return head[:4] == b"\x7fELF" or head[:2] == b"#!" or head[:4] in (
        b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit little-endian
        b"\xca\xfe\xba\xbe",  # Mach-O universal
    )


def restore_exec_bits(root: Path) -> int:
    """Re-apply the executable bit to every runnable file under ``root``.

    Zip archives do not carry Unix permissions, so a tree extracted from one
    (or copied through a filesystem that drops the bit) leaves every engine
    non-executable and docking fails with a bare "Permission denied".
    """
    fixed = 0
    for path in root.rglob("*"):
        if not path.is_file() or os.access(path, os.X_OK):
            continue
        if not _is_executable_payload(path):
            continue
        try:
            mode = path.stat().st_mode
            path.chmod(mode | 0o111)
            fixed += 1
        except OSError:
            pass
    return fixed


def _extract(archive: Path, into: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(into)
        return
    with tarfile.open(archive) as t:
        # filter="data" (3.12+) refuses absolute/parent paths and other members
        # that would write outside `into`.
        if sys.version_info >= (3, 12):
            t.extractall(into, filter="data")
        else:
            t.extractall(into)


def fetch_platform(platform: str, dest_root: Path | None = None) -> None:
    root = dest_root or bin_root()
    print(f"\n== {platform} ==")
    dest = root / platform
    if dest.is_dir() and any(dest.iterdir()):
        print(f"  [ok] {dest} already present - skipping")
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(
            f"Cannot create {root}: {exc}\n"
            f"Set LADOCK_BIN to a writable directory (e.g. ~/.local/share/ladock/bin) "
            f"and re-run.") from exc

    last_err: Exception | None = None
    for ext in (".tar.gz", ".tar.xz", ".zip"):
        url = f"{_BASE}/bin-{platform}{ext}"
        archive = root / f"bin-{platform}{ext}"
        try:
            _download(url, archive)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  - not found ({exc})")
            continue
        print(f"  extracting {archive.name}...")
        _extract(archive, root)
        archive.unlink(missing_ok=True)
        if os.name != "nt":
            fixed = restore_exec_bits(dest)
            if fixed:
                print(f"  restored the executable bit on {fixed} file(s)")
        if not (dest.is_dir() and any(dest.iterdir())):
            raise SystemExit(
                f"Archive extracted but {dest} is empty - the archive "
                f"must contain a top-level '{platform}/' folder.")
        print(f"  [ok] {dest} ready")
        return

    raise SystemExit(
        f"Could not fetch bin/{platform} from {_BASE} "
        f"(tried .tar.gz/.tar.xz/.zip). Last error: {last_err}\n"
        f"Host your validated binaries as <BASE>/bin-{platform}.tar.gz, or set "
        f"LADOCK_BIN_BASE_URL / LADOCK_BIN_TOKEN.")


def binaries_present(platform: str | None = None) -> bool:
    target = bin_root() / (platform or platform_name())
    return target.is_dir() and any(target.iterdir())


def ensure_platform_binaries(quiet: bool = False) -> bool:
    """Download this platform's engines if they are not there yet.

    Called before docking so ``pip install ladock`` is followed straight by
    ``ladock-cli`` — no separate fetch step for the user to know about. It is a
    no-op once the binaries exist, and a failure here is not fatal: the caller
    still reports the missing engine in its own terms.
    """
    if binaries_present():
        return True
    if not quiet:
        print("Docking engines are not installed yet — downloading them once "
              f"into {bin_root()} …", flush=True)
    try:
        fetch_platform(platform_name())
    except SystemExit as exc:
        if not quiet:
            print(f"  Could not download the engines: {exc}", flush=True)
        return False
    except Exception as exc:  # noqa: BLE001 - never block docking on this
        if not quiet:
            print(f"  Could not download the engines: {exc}", flush=True)
        return False
    return binaries_present()


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if "--fix-permissions" in args:
        # Repair an existing tree in place — for binaries that arrived by zip,
        # USB stick, or a filesystem that dropped the executable bit.
        root = bin_root()
        if not root.is_dir():
            raise SystemExit(f"No binaries at {root}. Run `ladock-fetch-binaries` first.")
        print(f"Restored the executable bit on {restore_exec_bits(root)} file(s) in {root}")
        return 0
    # No argument = "make THIS machine work", which is what someone who just ran
    # `pip install ladock` wants; downloading all three platforms is a build-time
    # need, so it stays opt-in via `all`.
    platforms = args or [platform_name()]
    if "all" in platforms:
        platforms = list(PLATFORMS)
    for p in platforms:
        if p not in PLATFORMS:
            raise SystemExit(f"Unknown platform '{p}' ({'|'.join(PLATFORMS)}|all)")
        fetch_platform(p)
    print(f"\nDone. Binaries in {bin_root()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
