from __future__ import annotations

import os
import shutil
from pathlib import Path

from core.wsl_backend import is_windows_host, wsl_available


_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_BIN = _ROOT / "bin"
_ADFR_BIN = _BUNDLED_BIN / "ADFRsuite-1.0" / "bin"


_TOOL_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "vina": {
        "windows": ["vina_1.2.7_win.exe", "vina.exe", "vina"],
        "linux": ["vina_1.2.7_linux_x86_64", "vina"],
    },
    "autodock4": {
        "windows": ["autodock4.exe", "autodock4"],
        "linux": ["autodock4"],
    },
    "autogrid4": {
        "windows": ["autogrid4.exe", "autogrid4"],
        "linux": ["autogrid4"],
    },
    "autodock_gpu": {
        "windows": ["autodock_gpu.exe", "autodockgpu.exe", "autodock_gpu"],
        "linux": ["adgpu-v1.6_linux_x64_cuda12_128wi", "autodock_gpu"],
    },
    "adfr": {
        "windows": ["adfr.bat", "adfr.exe", "adfr"],
        "linux": ["adfr"],
    },
    "agfr": {
        "windows": ["agfr.bat", "agfr.exe", "agfr"],
        "linux": ["agfr"],
    },
}


def _preferred_platform(use_wsl_backend: bool = False) -> str:
    if is_windows_host():
        return "linux" if use_wsl_backend and wsl_available() else "windows"
    return "linux"


def _secondary_platform(use_wsl_backend: bool = False) -> str:
    preferred = _preferred_platform(use_wsl_backend)
    return "windows" if preferred == "linux" else "linux"


def _adfr_candidate_path(name: str) -> Path:
    return _ADFR_BIN / name


def _bundled_candidate_path(name: str) -> Path:
    return _BUNDLED_BIN / name


def _candidate_paths_for_platform(key: str, platform_name: str) -> list[str]:
    names = _TOOL_CANDIDATES.get(key, {}).get(platform_name, [])
    paths: list[str] = []
    for name in names:
        suffix = Path(name).suffix.lower()
        should_check_bundled = platform_name != "windows" or suffix in {".exe", ".bat", ".cmd"}
        if should_check_bundled:
            if key in {"adfr", "agfr"}:
                candidate = _adfr_candidate_path(name)
            else:
                candidate = _bundled_candidate_path(name)
            paths.append(str(candidate))
        which = shutil.which(name)
        if which:
            paths.append(which)
    return paths


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        norm = os.path.normcase(item)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(item)
    return out


def _find_existing_preferred_candidate(key: str, use_wsl_backend: bool = False) -> str:
    preferred = _preferred_platform(use_wsl_backend)
    for candidate in _candidate_paths_for_platform(key, preferred):
        if os.path.isfile(candidate) or shutil.which(candidate):
            return candidate
    return ""


def _normalize_override(key: str, override: str, use_wsl_backend: bool = False) -> str:
    override = (override or "").strip()
    if not override:
        return ""

    preferred = _preferred_platform(use_wsl_backend)
    basename = Path(override).name.lower()
    preferred_names = {name.lower() for name in _TOOL_CANDIDATES.get(key, {}).get(preferred, [])}
    other_names = {
        name.lower()
        for platform_name, names in _TOOL_CANDIDATES.get(key, {}).items()
        if platform_name != preferred
        for name in names
    }

    if basename in preferred_names:
        return override

    preferred_candidate = _find_existing_preferred_candidate(key, use_wsl_backend)
    if not preferred_candidate:
        return override

    if basename in other_names:
        return preferred_candidate

    if preferred == "linux" and basename.endswith((".exe", ".bat", ".cmd")):
        return preferred_candidate

    if preferred == "windows" and not Path(override).suffix:
        for name in _TOOL_CANDIDATES.get(key, {}).get("linux", []):
            if basename == name.lower():
                return preferred_candidate

    return override


def iter_tool_candidates(key: str, use_wsl_backend: bool = False) -> list[str]:
    preferred = _preferred_platform(use_wsl_backend)
    secondary = _secondary_platform(use_wsl_backend)
    return _dedupe(
        _candidate_paths_for_platform(key, preferred)
        + _candidate_paths_for_platform(key, secondary)
    )


def resolve_tool_path(key: str, override: str = "", use_wsl_backend: bool = False) -> str:
    override = _normalize_override(key, override, use_wsl_backend)
    if override:
        return override

    preferred = _preferred_platform(use_wsl_backend)
    for candidate in _candidate_paths_for_platform(key, preferred):
        if os.path.isfile(candidate):
            return candidate
        if shutil.which(candidate):
            return candidate

    fallback_names = _TOOL_CANDIDATES.get(key, {}).get(preferred) or [key]
    return fallback_names[-1]


def mgltools_candidates(use_wsl_backend: bool = False) -> list[str]:
    preferred = _preferred_platform(use_wsl_backend)
    candidates: list[Path] = [_BUNDLED_BIN / "MGLTools-1.5.6"]
    if preferred == "windows":
        candidates.extend(
            [
                Path.home() / "MGLTools",
                Path.home() / "mgltools",
                Path("C:/MGLTools"),
                Path("C:/Program Files/MGLTools"),
                Path("C:/Program Files (x86)/MGLTools"),
            ]
        )
    else:
        candidates.extend(
            [
                _BUNDLED_BIN / "MGLTools-1.5.6",
                Path.home() / "MGLTools",
                Path.home() / "mgltools",
                Path("/opt/MGLTools"),
                Path("/usr/local/MGLTools"),
                Path("/usr/lib/MGLTools"),
            ]
        )
    return _dedupe([str(path) for path in candidates])


def resolve_mgltools_dir(override: str = "", use_wsl_backend: bool = False) -> str:
    if override and override.strip():
        return override.strip()

    for candidate in mgltools_candidates(use_wsl_backend=use_wsl_backend):
        if os.path.isdir(candidate):
            return candidate
    return ""


def adfrsuite_candidates(use_wsl_backend: bool = False) -> list[str]:
    preferred = _preferred_platform(use_wsl_backend)
    candidates: list[Path] = [_BUNDLED_BIN / "ADFRsuite-1.0"]
    if preferred == "windows":
        candidates.extend(
            [
                Path.home() / "ADFRsuite",
                Path("C:/ADFRsuite"),
                Path("C:/Program Files/ADFRsuite"),
            ]
        )
    else:
        candidates.extend(
            [
                _BUNDLED_BIN / "ADFRsuite-1.0",
                Path.home() / "ADFRsuite",
                Path("/opt/ADFRsuite"),
                Path("/usr/local/ADFRsuite"),
            ]
        )
    return _dedupe([str(path) for path in candidates])


def resolve_adfrsuite_dir(override: str = "", use_wsl_backend: bool = False) -> str:
    if override and override.strip():
        return override.strip()

    for candidate in adfrsuite_candidates(use_wsl_backend=use_wsl_backend):
        if os.path.isdir(candidate):
            return candidate
    return ""
