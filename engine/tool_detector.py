"""
engine/tool_detector.py — Auto-detect external docking tool binaries.
AutoDock4, AutoGrid4, ADFR, AGFR and MGLTools are bundled in bin/ and always preferred.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.tool_paths import (
    iter_tool_candidates,
    mgltools_candidates,
    resolve_tool_path,
)

# Bundled binaries shipped with LADOCK
_LADOCK_ROOT = Path(__file__).parent.parent
_BUNDLED_BIN = _LADOCK_ROOT / "bin"

# Tools that are bundled (always available inside bin/)
_BUNDLED_KEYS = {"autodock4", "autogrid4", "adfr", "agfr", "mgltools", "autodock_gpu"}

# Exported so GUI panels can check bundled status without re-importing private names
BUNDLED_KEYS: frozenset[str] = frozenset(_BUNDLED_KEYS)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@dataclass
class ToolInfo:
    key: str                   # internal key
    label: str                 # human-readable name
    binary: str                # default binary / script name
    version_flag: str = "--version"
    description: str = ""
    found_path: Optional[str] = None
    version: Optional[str] = None
    available: bool = False


_TOOLS: list[ToolInfo] = [
    ToolInfo("vina",         "AutoDock Vina [bundled]",    "vina",
             "--version",
             "AutoDock Vina v1.2.7 — bundled in bin/"),
    ToolInfo("autodock4",    "AutoDock 4 [bundled]",       "autodock4",
             "--version",
             "AutoDock 4 docking engine — bundled in bin/"),
    ToolInfo("autogrid4",    "AutoGrid 4 [bundled]",       "autogrid4",
             "--version",
             "AutoGrid 4 grid generation — bundled in bin/"),
    ToolInfo("autodock_gpu", "AutoDock-GPU [bundled]",     "autodock_gpu",
             "--version",
             "AutoDock-GPU v1.6 — bundled in bin/"),
    ToolInfo("adfr",         "ADFR [bundled]",             "adfr",
             "--version",
             "AutoDockFR docking engine — bundled in bin/ADFRsuite-1.0/"),
    ToolInfo("agfr",         "AGFR [bundled]",             "agfr",
             "--version",
             "AutoGrid FR grid preparation — bundled in bin/ADFRsuite-1.0/"),
    ToolInfo("mgltools",     "MGLTools [bundled]",         "prepare_receptor4.py",
             "-h",
             "MGLTools suite — bundled in bin/MGLTools-1.5.6/"),
]


# ---------------------------------------------------------------------------
# Install URLs shown in UI
# ---------------------------------------------------------------------------

INSTALL_URLS: dict[str, str] = {
    "autodock4":   "https://autodock.scripps.edu/downloads/",
    "autogrid4":   "https://autodock.scripps.edu/downloads/",
    "autodock_gpu":"https://github.com/ccsb-scripps/AutoDock-GPU/releases",
    "adfr":        "https://ccsb.scripps.edu/adfr/downloads/",
    "agfr":        "https://ccsb.scripps.edu/adfr/downloads/",
    "mgltools":    "https://ccsb.scripps.edu/mgltools/downloads/",
}

# pip-installable tools
PIP_INSTALL: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _get_version(path: str, flag: str) -> Optional[str]:
    """Run `path flag` and return first stdout/stderr line, or None on failure."""
    try:
        r = subprocess.run(
            [path, flag],
            capture_output=True, text=True, timeout=5
        )
        out = (r.stdout + r.stderr).strip()
        return out.split("\n")[0][:80] if out else None
    except Exception:
        return None


def _find_binary(binary: str, key: str) -> Optional[str]:
    """Look up binary: bundled bin/ paths first, then PATH."""
    for candidate in iter_tool_candidates(key):
        if os.path.isfile(candidate):
            return candidate
    return None


def _find_mgltools() -> Optional[str]:
    """Try to find MGLTools installation directory — bundled bin/ first."""
    for candidate in mgltools_candidates():
        c = Path(candidate)
        if (c / "MGLToolsPckgs").exists():
            return str(c)
        if (c / "bin" / "prepare_receptor4.py").exists():
            return str(c)
    return None


def detect_all() -> dict[str, ToolInfo]:
    """Detect all tools and return mapping key → ToolInfo."""
    results: dict[str, ToolInfo] = {}
    for tool in _TOOLS:
        t = ToolInfo(**tool.__dict__)   # shallow copy
        if t.key == "mgltools":
            d = _find_mgltools()
            if d:
                t.found_path = d
                t.available = True
                t.version = "found"
        else:
            found = _find_binary(t.binary, t.key)
            if found:
                t.found_path = found
                t.available = True
                t.version = _get_version(found, t.version_flag)
        results[t.key] = t
    return results


def detect_one(key: str) -> Optional[ToolInfo]:
    all_tools = {t.key: t for t in _TOOLS}
    if key not in all_tools:
        return None
    t = ToolInfo(**all_tools[key].__dict__)
    found = _find_binary(t.binary, t.key)
    if found:
        t.found_path = found
        t.available = True
        t.version = _get_version(found, t.version_flag)
    return t


def get_tool_path(key: str, override: str = "") -> str:
    """Return usable path for tool: use override if given, else auto-detect."""
    return resolve_tool_path(key, override=override)
