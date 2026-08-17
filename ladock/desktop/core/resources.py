"""Locate the desktop app's bundled resources (``gui/assets/``, ``config/``).

Kept as a thin wrapper so the many desktop modules that already import
:func:`resource_root` keep working; the actual layout rules live in
:mod:`ladock.paths`, shared with the CLI.
"""
from __future__ import annotations

from pathlib import Path

from ladock.paths import bin_root, desktop_resource_root


def resource_root() -> Path:
    """Directory that contains the bundled ``gui/`` and ``config/``."""
    return desktop_resource_root()


__all__ = ["resource_root", "bin_root"]
