"""Locate bundled resources (bin/, gui/assets/, config/) consistently whether
running from source or as a PyInstaller-frozen app.

From source, resources sit under the ``desktop/`` directory. When frozen,
PyInstaller unpacks them to ``sys._MEIPASS`` (one-file) or places them next to
the executable (one-dir); ``__file__`` no longer points at the real tree, so
resource paths must be resolved through :func:`resource_root`.
"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """Directory that contains the bundled ``bin/``, ``gui/`` and ``config/``."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) if base else Path(sys.executable).resolve().parent
    # core/resources.py → parent is core/, parent.parent is desktop/
    return Path(__file__).resolve().parent.parent
