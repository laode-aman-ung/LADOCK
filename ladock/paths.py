"""Single source of truth for where LADOCK's bundled files live.

Both front-ends need the same answer to "where is ``bin/``?", and the answer
differs between a source checkout, a pip install, and a PyInstaller-frozen app.
Resolving it in one place keeps the CLI and the desktop app from drifting apart
(they previously had two copies of this logic, and the CLI's copy still pointed
at a directory that no longer exists).

Layout, from source and when pip-installed::

    ladock/
    ├── bin/<platform>/     engine binaries + MGLTools (fetched separately)
    └── desktop/
        ├── config/         example inputs
        └── gui/assets/     viewer HTML/JS

When frozen, PyInstaller unpacks ``bin/``, ``config/`` and ``gui/assets/`` to
``sys._MEIPASS`` (one-file) or next to the executable (one-dir), so both roots
collapse onto that directory.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent


def _frozen_root() -> Path | None:
    """The unpack directory of a PyInstaller build, or None when running normally."""
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(sys.executable).resolve().parent


def bin_root() -> Path:
    """Directory holding the per-platform engine binaries (``bin/<platform>/``).

    Order: ``LADOCK_BIN`` wins, then the frozen bundle, then wherever the
    binaries already are, and finally the first writable place to put them.
    That last step matters for ``pip install``: site-packages is often read-only
    (system Python, root-owned virtualenvs), and the engines are downloaded
    after installation — so they fall back to the user's cache directory
    instead of failing.
    """
    override = os.environ.get("LADOCK_BIN", "").strip()
    if override:
        return Path(override)
    frozen = _frozen_root()
    if frozen is not None:
        return frozen / "bin"
    packaged = PACKAGE_ROOT / "bin"
    if packaged.is_dir():
        return packaged
    user_owned = cache_root() / "bin"
    if user_owned.is_dir():
        return user_owned
    return packaged if os.access(PACKAGE_ROOT, os.W_OK) else user_owned


def platform_name(use_wsl_backend: bool = False) -> str:
    """Which ``bin/<platform>`` subtree applies to the current host.

    With the WSL backend active on Windows, the Linux-only engines are dispatched
    through ``wsl.exe`` and therefore resolve to the Linux binaries.
    """
    if os.name == "nt":
        return "linux" if use_wsl_backend else "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def platform_bin(use_wsl_backend: bool = False) -> Path:
    return bin_root() / platform_name(use_wsl_backend)


def cache_root() -> Path:
    """Where LADOCK keeps regenerable data that is expensive to recompute.

    Currently the AutoGrid4 map cache. Overridden by ``LADOCK_CACHE``; otherwise
    it follows the platform convention, so it lands somewhere the user's backup
    and cleanup tools already understand.
    """
    override = os.environ.get("LADOCK_CACHE", "").strip()
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "ladock" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ladock"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    return (Path(xdg) if xdg else Path.home() / ".cache") / "ladock"


def desktop_resource_root() -> Path:
    """Directory containing the desktop app's ``config/`` and ``gui/assets/``."""
    frozen = _frozen_root()
    if frozen is not None:
        return frozen
    return PACKAGE_ROOT / "desktop"
