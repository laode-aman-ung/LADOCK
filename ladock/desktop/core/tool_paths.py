from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ladock.desktop.core.wsl_backend import is_windows_host, wsl_available
from ladock.paths import bin_root


_BUNDLED_BIN = bin_root()


def _platform_bin(platform_name: str) -> Path:
    """Per-platform bundled binaries live in bin/<platform>/."""
    return _BUNDLED_BIN / platform_name


_TOOL_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "vina": {
        "windows": ["vina_1.2.7_win.exe", "vina.exe", "vina"],
        "linux": ["vina_1.2.7_linux_x86_64", "vina"],
        "mac": ["vina_1.2.7_mac_x86_64", "vina"],
    },
    "autodock4": {
        "windows": ["autodock4.exe", "autodock4"],
        "linux": ["autodock4"],
        "mac": ["autodock4"],
    },
    "autogrid4": {
        "windows": ["autogrid4.exe", "autogrid4"],
        "linux": ["autogrid4"],
        "mac": ["autogrid4"],
    },
    "autodock_gpu": {
        "windows": ["autodock_gpu.exe", "autodockgpu.exe", "autodock_gpu"],
        "linux": ["adgpu-v1.6_linux_x64_cuda12_128wi", "autodock_gpu"],
        "mac": ["autodock_gpu"],
    },
    "adfr": {
        "windows": ["adfr.bat", "adfr.exe", "adfr"],
        "linux": ["adfr"],
        "mac": ["adfr"],
    },
    "agfr": {
        "windows": ["agfr.bat", "agfr.exe", "agfr"],
        "linux": ["agfr"],
        "mac": ["agfr"],
    },
}


def _preferred_platform(use_wsl_backend: bool = False) -> str:
    # Windows is pure-native by default. In HYBRID mode (use_wsl_backend) the
    # Windows GUI dispatches the Linux-only engines (AD4/AD-GPU/AutoGrid4/MGLTools)
    # to WSL, so those binaries resolve to bin/linux/ and run via wsl.exe.
    if is_windows_host():
        return "linux" if use_wsl_backend and wsl_available() else "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def preferred_tool_candidates(key: str, use_wsl_backend: bool = False) -> list[str]:
    """Candidate paths for the active platform (host, or Linux under hybrid WSL).

    Never falls back to another platform's binaries — a Linux-only tool must not
    look "available" on native Windows unless hybrid WSL mode is enabled.
    """
    return _dedupe(
        _candidate_paths_for_platform(key, _preferred_platform(use_wsl_backend)))


def tool_available(key: str, use_wsl_backend: bool = False) -> bool:
    """True when *key* resolves to a usable binary on the active platform.

    Under hybrid WSL mode the bundled Linux binaries sit on the Windows disk
    (bin/linux/…) so ``os.path.isfile`` still finds them; they run via wsl.exe."""
    for candidate in preferred_tool_candidates(key, use_wsl_backend):
        if os.path.isfile(candidate) or shutil.which(candidate):
            return True
    return False


def _first_existing(key: str, use_wsl_backend: bool = False) -> str:
    for candidate in preferred_tool_candidates(key, use_wsl_backend):
        if os.path.isfile(candidate):
            return candidate
    return ""


def autodock_gpu_runnable(path: str = "", use_wsl_backend: bool = False) -> bool:
    """True when the AutoDock-GPU binary exists AND its CUDA runtime resolves.

    Verified with ``ldd`` (no execution). Under hybrid WSL mode the binary is a
    Linux ELF, so ``ldd`` is run inside WSL against the translated /mnt path.
    """
    import shlex
    p = (path or "").strip()
    if not (p and os.path.isfile(p)):
        p = _first_existing("autodock_gpu", use_wsl_backend)
    if not p:
        return False

    if use_wsl_backend and is_windows_host() and wsl_available():
        from ladock.desktop.core.wsl_backend import windows_to_wsl_path, wsl_executable
        try:
            result = subprocess.run(
                [wsl_executable(), "bash", "-lc",
                 f"ldd {shlex.quote(windows_to_wsl_path(p))}"],
                capture_output=True, text=True, timeout=25)
        except Exception:  # noqa: BLE001
            return True
        return "not found" not in (result.stdout + result.stderr)

    ldd = shutil.which("ldd")
    if not ldd:
        return True
    try:
        result = subprocess.run(
            [ldd, p], capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return True
    return "not found" not in (result.stdout + result.stderr)


def _secondary_platform(use_wsl_backend: bool = False) -> str:
    preferred = _preferred_platform(use_wsl_backend)
    return "windows" if preferred == "linux" else "linux"


def _adfr_candidate_paths(name: str, platform_name: str) -> list[Path]:
    """Where an ADFRsuite binary can live: the bundled tree first, then any
    external installation found by :func:`adfrsuite_candidates`.

    Looking only inside ``bin/<platform>/ADFRsuite-1.0`` meant a user who had
    installed ADFRsuite the normal way (the Scripps installer puts it in the home
    directory) still saw ADFR and AGFR reported as missing.
    """
    paths = [_platform_bin(platform_name) / "ADFRsuite-1.0" / "bin" / name]
    for suite in adfrsuite_candidates():
        paths.append(Path(suite) / "bin" / name)
    return paths


def _bundled_candidate_path(name: str, platform_name: str) -> Path:
    return _platform_bin(platform_name) / name


def _candidate_paths_for_platform(key: str, platform_name: str) -> list[str]:
    names = _TOOL_CANDIDATES.get(key, {}).get(platform_name, [])
    paths: list[str] = []
    for name in names:
        suffix = Path(name).suffix.lower()
        should_check_bundled = platform_name != "windows" or suffix in {".exe", ".bat", ".cmd"}
        if should_check_bundled:
            if key in {"adfr", "agfr"}:
                paths.extend(str(p) for p in _adfr_candidate_paths(name, platform_name))
            else:
                paths.append(str(_bundled_candidate_path(name, platform_name)))
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
    """Bundled MGLTools ships only as a Linux build (bin/linux/MGLTools-1.5.6),
    used for the AD4 grid path. It is looked up under the current platform only,
    so on native Windows/macOS it is correctly reported as unavailable — those
    builds use Meeko for preparation and cannot run the Linux AD4 pipeline."""
    preferred = _preferred_platform(use_wsl_backend)
    candidates: list[Path] = [_platform_bin(preferred) / "MGLTools-1.5.6"]
    roots = ([Path.home(), Path("C:/"), Path("C:/Program Files"), Path("C:/Program Files (x86)")]
             if preferred == "windows"
             else [Path.home(), Path("/opt"), Path("/usr/local"), Path("/usr/lib")])
    for root in roots:
        candidates.extend(_versioned_dirs(root, "MGLTools"))
        candidates.extend(_versioned_dirs(root, "mgltools"))
    return _dedupe([str(path) for path in candidates])


def resolve_mgltools_dir(override: str = "", use_wsl_backend: bool = False) -> str:
    if override and override.strip():
        return override.strip()

    for candidate in mgltools_candidates(use_wsl_backend=use_wsl_backend):
        if os.path.isdir(candidate):
            return candidate
    return ""


def _versioned_dirs(root: Path, stem: str) -> list[Path]:
    """``<root>/<stem>`` plus any ``<root>/<stem>-<version>``, newest first.

    The official installers create a versioned directory — ADFRsuite-1.0,
    MGLTools-1.5.6 — so looking only for the bare name misses a perfectly good
    installation by nothing more than a version suffix.
    """
    found = [root / stem]
    try:
        found.extend(sorted(root.glob(f"{stem}-*"), reverse=True))
    except OSError:
        pass
    return found


def adfrsuite_candidates(use_wsl_backend: bool = False) -> list[str]:
    preferred = _preferred_platform(use_wsl_backend)
    candidates: list[Path] = [_platform_bin(preferred) / "ADFRsuite-1.0"]
    roots = ([Path.home(), Path("C:/"), Path("C:/Program Files")]
             if preferred == "windows"
             else [Path.home(), Path("/opt"), Path("/usr/local")])
    for root in roots:
        candidates.extend(_versioned_dirs(root, "ADFRsuite"))
    return _dedupe([str(path) for path in candidates])


def resolve_adfrsuite_dir(override: str = "", use_wsl_backend: bool = False) -> str:
    if override and override.strip():
        return override.strip()

    for candidate in adfrsuite_candidates(use_wsl_backend=use_wsl_backend):
        if os.path.isdir(candidate):
            return candidate
    return ""
