#!/usr/bin/env python
"""Standalone rule-based LADOCK docking CLI agent.

The agent is intentionally self-contained: it does not import LADOCK Desktop
modules.  It can be copied with an optional ``agent/bin/<platform>/`` tool
bundle, or it can use executables available on PATH / explicit CLI overrides.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import itertools
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from ladock import __version__
from ladock.paths import bin_root, cache_root, platform_name as _pkg_platform_name


AGENT_ROOT = Path(__file__).resolve().parent



STANDARD_AA = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "HID", "HIE", "HIP", "CYX", "CYM", "MSE", "SEC", "PYL", "UNK", "ACE", "NME",
}
WATER_RESNAMES = {"HOH", "WAT", "H2O", "DOD", "TIP", "SOL"}
METAL_ELEMENTS = {
    "ZN", "MG", "CA", "FE", "MN", "CU", "NI", "CO", "MO", "NA", "K", "CD", "HG",
    "PT", "AU", "AG", "AL", "BA", "SR", "PB", "BI", "CS", "LI", "RB", "IN", "CR",
    "V", "W",
}
VINA_SCORING = {"vina", "vinardo"}
AD4_SCORING = {"ad4", "ad4gpu"}
# AutoDockFR: its own engine pair (agfr builds a .trg target, adfr docks into it),
# shipped in ADFRsuite rather than with the bundled binaries.
ADFR_SCORING = {"adfr"}
ALL_SCORING = VINA_SCORING | AD4_SCORING | ADFR_SCORING
LIGAND_SUFFIXES = {
    ".pdbqt", ".pdb", ".sdf", ".mol", ".mol2", ".smi", ".smiles", ".txt",
    ".csv", ".tsv", ".xlsx", ".xls",
}

# LADOCK academic free license: this version is activated (free, no key) up to
# this date, mirroring LADOCK Desktop (core/license_manager.py ACADEMIC_FREE_UNTIL).
LICENSE_FREE_UNTIL = _dt.date(2029, 12, 31)


def is_windows_host() -> bool:
    return os.name == "nt"


def wsl_available() -> bool:
    return is_windows_host() and shutil.which("wsl.exe") is not None


def wsl_executable() -> str:
    return shutil.which("wsl.exe") or "wsl.exe"


def windows_to_wsl_path(path: str) -> str:
    p = Path(path)
    try:
        resolved = str(p.resolve())
    except OSError:
        resolved = str(p)
    if re.match(r"^[A-Za-z]:", resolved):
        drive = resolved[0].lower()
        rest = resolved[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return resolved.replace("\\", "/")


def maybe_to_wsl_path(path: str, use_wsl_backend: bool = False) -> str:
    if use_wsl_backend and is_windows_host():
        return windows_to_wsl_path(path)
    return path


def prepare_subprocess(cmd: list[str], cwd: str | None = None,
                       use_wsl_backend: bool = False,
                       wsl_distro: str = "") -> tuple[list[str], str | None]:
    if not (use_wsl_backend and is_windows_host()):
        return cmd, cwd
    translated: list[str] = []
    for part in cmd:
        if isinstance(part, str) and (os.path.exists(part) or re.match(r"^[A-Za-z]:", part)):
            translated.append(windows_to_wsl_path(part))
        else:
            translated.append(str(part))
    prefix = [wsl_executable()]
    if wsl_distro:
        prefix += ["-d", wsl_distro]
    if cwd:
        shell_cmd = f"cd {shlex.quote(windows_to_wsl_path(cwd))} && " + " ".join(
            shlex.quote(p) for p in translated
        )
        return prefix + ["bash", "-lc", shell_cmd], None
    return prefix + translated, None


def _platform_name(use_wsl_backend: bool = False) -> str:
    return _pkg_platform_name(use_wsl_backend)


def _agent_bin_roots(platform_name: str) -> list[Path]:
    """Where to look for engine binaries, most specific first.

    ``LADOCK_AGENT_BIN`` stays supported for agent-only overrides; everything
    else comes from :func:`ladock.paths.bin_root`, the shared ``ladock/bin/``
    tree that the desktop app uses too (and which ``LADOCK_BIN`` can redirect).
    """
    roots: list[Path] = []
    env_root = os.environ.get("LADOCK_AGENT_BIN", "").strip()
    if env_root:
        roots.append(Path(env_root) / platform_name)
        roots.append(Path(env_root))
    shared = bin_root()
    roots.append(shared / platform_name)
    roots.append(shared)
    return roots


_TOOL_NAMES: dict[str, dict[str, list[str]]] = {
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
    "obabel": {
        "windows": ["obabel.exe", "obabel"],
        "linux": ["obabel"],
        "mac": ["obabel"],
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


def resolve_adfrsuite_dir(override: str = "", use_wsl_backend: bool = False) -> str:
    """Locate an ADFRsuite installation (ADFR/AGFR live in its ``bin/``).

    ADFRsuite is ~500 MB and is not part of the bundled binaries, so this looks
    where the official Scripps installer puts it. Versioned directory names are
    globbed: the installer creates ``ADFRsuite-1.0``, and searching only for a
    bare ``ADFRsuite`` misses a perfectly good install by a version suffix.
    """
    if override and override.strip():
        return override.strip()
    env = os.environ.get("ADFRSUITE_HOME", "").strip()
    platform = _platform_name(use_wsl_backend)
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    for root in _agent_bin_roots(platform):
        candidates.extend(sorted(root.glob("ADFRsuite*"), reverse=True))
    roots = ([Path.home(), Path("C:/"), Path("C:/Program Files")] if platform == "windows"
             else [Path.home(), Path("/opt"), Path("/usr/local")])
    for root in roots:
        candidates.append(root / "ADFRsuite")
        try:
            candidates.extend(sorted(root.glob("ADFRsuite-*"), reverse=True))
        except OSError:
            pass
    for candidate in candidates:
        if (candidate / "bin" / "adfr").is_file() or (candidate / "bin" / "adfr.bat").is_file():
            return str(candidate)
    return ""


def _tool_candidates(key: str, use_wsl_backend: bool = False) -> list[str]:
    platform_name = _platform_name(use_wsl_backend)
    names = _TOOL_NAMES.get(key, {}).get(platform_name, [key])
    candidates: list[str] = []
    for root in _agent_bin_roots(platform_name):
        for name in names:
            candidates.append(str(root / name))
    for name in names:
        found = shutil.which(name)
        if found:
            candidates.append(found)
    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        norm = os.path.normcase(item)
        if norm not in seen:
            seen.add(norm)
            out.append(item)
    return out


def resolve_tool_path(key: str, override: str = "", use_wsl_backend: bool = False) -> str:
    if override and override.strip():
        return override.strip()
    for candidate in _tool_candidates(key, use_wsl_backend):
        if os.path.isfile(candidate):
            return candidate
    platform_name = _platform_name(use_wsl_backend)
    return (_TOOL_NAMES.get(key, {}).get(platform_name) or [key])[-1]


def resolve_mgltools_dir(override: str = "", use_wsl_backend: bool = False) -> str:
    if override and override.strip():
        return override.strip()
    platform_name = _platform_name(use_wsl_backend)
    candidates = []
    env = os.environ.get("MGLTOOLS_HOME", "").strip()
    if env:
        candidates.append(Path(env))
    for root in _agent_bin_roots(platform_name):
        candidates.extend([root / "MGLTools-1.5.6", root / "mgltools", root / "MGLTools"])
    if platform_name == "windows":
        candidates.extend([Path("C:/MGLTools"), Path("C:/Program Files/MGLTools")])
    else:
        candidates.extend([Path("/opt/MGLTools"), Path("/usr/local/MGLTools"), Path.home() / "MGLTools"])
    for candidate in candidates:
        if (candidate / "MGLToolsPckgs").exists() or (candidate / "bin" / "prepare_receptor4.py").exists():
            return str(candidate)
    return ""


def meeko_available() -> bool:
    try:
        import meeko  # noqa: F401
        return True
    except Exception:
        return False


def rdkit_available() -> bool:
    try:
        import rdkit  # noqa: F401
        return True
    except Exception:
        return False


def log(message: str) -> None:
    print(message, flush=True)


# Verbosity: by default the CLI shows only its OWN output (progress + result
# tables); raw sub-process command lines and engine stdout are hidden. Pass
# --verbose (or set LADOCK_VERBOSE=1) to surface everything for debugging.
_VERBOSE = bool(os.environ.get("LADOCK_VERBOSE"))


def set_verbose(value: bool) -> None:
    global _VERBOSE
    _VERBOSE = bool(value)


def _vlog(message: str) -> None:
    """Log only in verbose mode (used for command/engine noise)."""
    if _VERBOSE:
        print(message, flush=True)


def _noop_log(_message: str) -> None:
    pass


# --------------------------------------------------------------------------- #
# LADOCK CLI signature look & feel (self-contained, zero-dependency)
# --------------------------------------------------------------------------- #
_COLOR = False        # ANSI colour enabled (interactive TTY, not NO_COLOR)
_UNICODE = False      # fancy glyphs enabled (UTF-8 capable stdout)

# 256-colour SGR codes used across the UI.
_SGR = {
    "reset": "0", "bold": "1", "dim": "2", "italic": "3", "underline": "4",
    "cyan": "38;5;51", "teal": "38;5;44", "green": "38;5;46",
    "grn2": "38;5;42", "blue": "38;5;39", "gray": "38;5;245",
    "yellow": "38;5;220", "red": "38;5;203", "white": "38;5;255",
}
# Vertical cyan -> green gradient for the banner rows.
_BANNER_GRAD = [51, 45, 44, 43, 42, 46]

_BANNER_UNICODE = [
    "██╗      █████╗ ██████╗  ██████╗  ██████╗██╗  ██╗",
    "██║     ██╔══██╗██╔══██╗██╔═══██╗██╔════╝██║ ██╔╝",
    "██║     ███████║██║  ██║██║   ██║██║     █████╔╝ ",
    "██║     ██╔══██║██║  ██║██║   ██║██║     ██╔═██╗ ",
    "███████╗██║  ██║██████╔╝╚██████╔╝╚██████╗██║  ██╗",
    "╚══════╝╚═╝  ╚═╝╚═════╝  ╚═════╝  ╚═════╝╚═╝  ╚═╝",
]
_BANNER_ASCII = [
    " _        _    ____   ___   ____ _  __",
    "| |      / \\  |  _ \\ / _ \\ / ___| |/ /",
    "| |     / _ \\ | | | | | | | |   | ' / ",
    "| |___ / ___ \\| |_| | |_| | |___| . \\ ",
    "|_____/_/   \\_\\____/ \\___/ \\____|_|\\_\\",
]


def init_terminal() -> None:
    """Prepare the LADOCK CLI terminal: UTF-8 output, ANSI on Windows consoles,
    and detect whether colour / unicode glyphs are usable. Safe to call twice."""
    global _COLOR, _UNICODE
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
                handle = kernel32.GetStdHandle(handle_id)
                mode = ctypes.c_uint32()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    _UNICODE = "utf" in enc
    no_color = os.environ.get("NO_COLOR") or os.environ.get("LADOCK_NO_COLOR")
    try:
        is_tty = sys.stdout.isatty()
    except Exception:
        is_tty = False
    _COLOR = bool(is_tty) and not no_color


def sty(text: str, *styles: str) -> str:
    """Wrap text in ANSI styles (no-op when colour is disabled)."""
    if not _COLOR or not styles:
        return text
    codes = ";".join(_SGR.get(s, "0") for s in styles)
    return f"\033[{codes}m{text}\033[0m"


def glyph(uni: str, ascii_: str) -> str:
    return uni if _UNICODE else ascii_


def prompt_glyph() -> str:
    return (sty("ladock", "cyan", "bold") + " "
            + sty(glyph("❯", ">"), "green", "bold") + " ")


def clear_screen() -> None:
    """Start the LADOCK UI on a fresh screen (like a dedicated app), instead of
    scrolling below the existing shell history. No-op when output is piped."""
    try:
        if not sys.stdout.isatty():
            return
    except Exception:
        return
    print("\033[2J\033[3J\033[H", end="", flush=True)  # clear screen + scrollback


def print_banner(version: str = __version__) -> None:
    art = _BANNER_UNICODE if _UNICODE else _BANNER_ASCII
    dot = glyph("·", "-")
    print()
    for line, code in zip(art, _BANNER_GRAD):
        colored = f"\033[1;38;5;{code}m{line}\033[0m" if _COLOR else line
        print("  " + colored)
    print()
    print("  " + sty("Rule-based Molecular Docking Agent", "white", "bold")
          + sty(f"  {dot}  non-LLM", "gray"))
    print("  " + sty(f"v{version}", "teal", "bold")
          + sty(f"   {dot} Vina {dot} Vinardo {dot} AutoDock4 {dot} AD-GPU", "gray"))
    print(sty("  " + glyph("─", "-") * 50, "teal"))
    print("  " + license_note())


def license_expired() -> bool:
    return _dt.date.today() > LICENSE_FREE_UNTIL


def license_note() -> str:
    """One-line license status for the CLI (academic free until a fixed date)."""
    today = _dt.date.today()
    until = LICENSE_FREE_UNTIL.isoformat()
    if today <= LICENSE_FREE_UNTIL:
        return (sty(glyph("©", "(c)"), "gray") + " "
                + sty("Lisensi Akademik gratis", "green")
                + sty(f" — aktif s/d {until}, tanpa key", "gray"))
    return (sty(glyph("©", "(c)"), "gray") + " "
            + sty(f"Lisensi kadaluarsa sejak {until}", "red", "bold")
            + sty(" — hubungi laode_aman@ung.ac.id", "gray"))


def ui_header(text: str, sub: str = "") -> None:
    """A section header line: '◆ text' with an optional dim continuation."""
    print("\n" + sty(glyph("◆", "*"), "cyan", "bold") + " " + sty(text, "bold"))
    if sub:
        for line in sub.splitlines():
            print("   " + sty(line, "gray"))


def ui_ok(text: str) -> None:
    print(sty("  " + glyph("✔", "[OK]"), "green", "bold") + " " + text)


def ui_note(text: str) -> None:
    print(sty("  " + glyph("→", "->"), "teal") + " " + sty(text, "gray"))


def ui_panel(title: str, rows: list[tuple[str, str]], width: int = 50) -> None:
    """Left-barred summary panel (avoids per-line right-padding math with ANSI)."""
    bar = glyph("│", "|")
    tl, bl, dash = glyph("╭", "+"), glyph("╰", "+"), glyph("─", "-")
    head_dash = max(0, width - len(title) - 3)
    print()
    print(sty(f"{tl}{dash} ", "teal") + sty(title, "cyan", "bold")
          + " " + sty(dash * head_dash, "teal"))
    label_w = max((len(k) for k, _ in rows), default=0)
    for k, v in rows:
        print(sty(bar, "teal") + " " + sty(k.ljust(label_w), "gray")
              + sty(" : ", "gray") + sty(str(v), "white"))
    print(sty(bl + dash * width, "teal"))


def _run_capture(cmd: list[str], tag: str, log_fn=log, cwd: str | None = None,
                 use_wsl_backend: bool = False, wsl_distro: str = "",
                 timeout: int = 300) -> bool:
    exec_cmd, exec_cwd = prepare_subprocess(
        cmd, cwd=cwd, use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro
    )
    if log_fn and _VERBOSE:
        log_fn(f"  $ {' '.join(str(c) for c in exec_cmd)}")
    try:
        result = subprocess.run(
            exec_cmd, cwd=exec_cwd, capture_output=True, text=True, timeout=timeout
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        if log_fn and _VERBOSE:
            log_fn(f"  {tag}: {exc}")
        return False
    combined = (result.stdout or "") + (result.stderr or "")
    if log_fn and _VERBOSE:
        for line in combined.splitlines():
            if line.strip():
                log_fn(f"  {tag}: {line}")
    return result.returncode == 0


def _meeko_cmd(module: str, args: list[str]) -> list[str]:
    """Command to run a Meeko CLI module. A frozen (PyInstaller) exe cannot do
    `sys.executable -m module`, so re-invoke the exe with an internal --_meeko
    dispatch (handled at the top of main())."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--_meeko", module, *args]
    return [sys.executable, "-m", module, *args]


def native_prepare_receptor(in_pdb: str, out_pdbqt: str, log_fn=log) -> bool:
    if not meeko_available():
        return False
    cmd = _meeko_cmd("meeko.cli.mk_prepare_receptor",
                     ["--read_pdb", in_pdb, "-p", out_pdbqt, "--allow_bad_res"])
    _run_capture(cmd, "mk_prepare_receptor", log_fn)
    return os.path.isfile(out_pdbqt)


def _rdkit_load_mol(path: str, log_fn=log):
    from rdkit import Chem

    ext = Path(path).suffix.lower()
    try:
        if ext == ".sdf":
            for mol in Chem.SDMolSupplier(path, removeHs=False, sanitize=True):
                if mol is not None:
                    return mol
            return None
        if ext in (".mol", ".mdl"):
            return Chem.MolFromMolFile(path, removeHs=False, sanitize=True)
        if ext == ".mol2":
            return Chem.MolFromMol2File(path, removeHs=False, sanitize=True)
        if ext in (".pdb", ".ent", ".pdbqt"):
            return Chem.MolFromPDBFile(path, removeHs=False, sanitize=True)
        if ext in (".smi", ".smiles", ".txt"):
            token = Path(path).read_text(encoding="utf-8", errors="replace").split()
            return Chem.MolFromSmiles(token[0]) if token else None
    except Exception as exc:
        if log_fn:
            log_fn(f"  RDKit read failed for {path}: {exc}")
    return None


def _rdkit_write_sdf(path: str, out_sdf: str, log_fn=log) -> bool:
    if not rdkit_available():
        return False
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem

        mol = _rdkit_load_mol(path, log_fn)
        if mol is None:
            return False
        mol = Chem.AddHs(mol, addCoords=True)
        if mol.GetNumConformers() == 0 or not mol.GetConformer().Is3D():
            if log_fn:
                log_fn("  Embedding 3D conformer with RDKit...")
            if AllChem.EmbedMolecule(mol, AllChem.ETKDGv3()) != 0:
                AllChem.EmbedMolecule(mol, useRandomCoords=True)
            try:
                AllChem.MMFFOptimizeMolecule(mol)
            except Exception:
                pass
        writer = Chem.SDWriter(out_sdf)
        writer.write(mol)
        writer.close()
        return os.path.isfile(out_sdf)
    except Exception as exc:
        if log_fn:
            log_fn(f"  RDKit SDF normalisation failed: {exc}")
        return False


def _file_to_pdbqt_meeko(in_path: str, out_path: str, log_fn=log) -> bool:
    if not meeko_available():
        return False
    tmp_dir = tempfile.mkdtemp(prefix="ladock_agent_meeko_")
    try:
        sdf = os.path.join(tmp_dir, "ligand.sdf")
        if not _rdkit_write_sdf(in_path, sdf, log_fn):
            return False
        cmd = _meeko_cmd("meeko.cli.mk_prepare_ligand", ["-i", sdf, "-o", out_path])
        _run_capture(cmd, "mk_prepare_ligand", log_fn)
        return os.path.isfile(out_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _obabel_command(use_wsl_backend: bool = False) -> str:
    return resolve_tool_path("obabel", "", use_wsl_backend)


def _file_to_pdbqt_obabel(in_path: str, out_path: str, gen3d: bool = False,
                          log_fn=log, use_wsl_backend: bool = False,
                          wsl_distro: str = "") -> bool:
    obabel = _obabel_command(use_wsl_backend)
    cmd = [obabel, in_path, "-O", out_path, "--partialcharge", "gasteiger"]
    if gen3d:
        cmd.append("--gen3d")
    return _run_capture(cmd, "obabel", log_fn, use_wsl_backend=use_wsl_backend,
                        wsl_distro=wsl_distro) and os.path.isfile(out_path)


def _file_to_pdbqt_mgltools(in_path: str, out_path: str, pythonsh: str,
                            prep_lig: str, log_fn=log,
                            use_wsl_backend: bool = False,
                            wsl_distro: str = "") -> bool:
    if not (os.path.isfile(pythonsh) and os.path.isfile(prep_lig)):
        return False
    ok = _run_capture(
        [pythonsh, prep_lig, "-l", in_path, "-o", out_path],
        "prepare_ligand4.py",
        log_fn,
        use_wsl_backend=use_wsl_backend,
        wsl_distro=wsl_distro,
    )
    return os.path.isfile(out_path) or ok


# Organic-subset atoms writable without brackets. Two-character symbols must be
# tried before the one-character ones, or "Cl" reads as "C" followed by junk.
_SMILES_ATOMS = ("Cl", "Br", "B", "C", "N", "O", "P", "S", "F", "I",
                 "b", "c", "n", "o", "p", "s")
# Bonds, branches, ring closures, stereo, charges, wildcards.
_SMILES_PUNCT = set("()[]=#$:/\\@+-.*%0123456789")


def _scan_smiles(value: str) -> bool:
    """Whether ``value`` parses as SMILES by structure alone (no RDKit needed).

    Walks the string as SMILES tokens instead of sniffing for a stray letter, so
    ordinary words are rejected: "Name" stops at 'a', "ethanol" at 'e',
    "Compound" at 'm'.
    """
    i, atoms = 0, 0
    while i < len(value):
        ch = value[i]
        if ch == "[":                       # bracket atom: [nH], [Fe+2], [13C]
            end = value.find("]", i)
            if end < 0:
                return False
            atoms += 1
            i = end + 1
            continue
        for symbol in _SMILES_ATOMS:
            if value.startswith(symbol, i):
                atoms += 1
                i += len(symbol)
                break
        else:
            if ch not in _SMILES_PUNCT:
                return False
            i += 1
    return atoms > 0


def _looks_like_smiles(value: str) -> bool:
    """Whether a cell holds a molecule rather than a name, id or header.

    RDKit is authoritative when installed (it is a declared dependency); the
    token scan is the fallback. The previous test accepted any string containing
    one of "CcNnOoSFPB([=", so a "compound" column, a "Name" header and most
    English words were all happily queued for docking as molecules.
    """
    value = value.strip()
    if not value or any(ch.isspace() for ch in value):
        return False                        # SMILES never contains whitespace
    if rdkit_available():
        try:
            from rdkit import Chem, RDLogger

            RDLogger.DisableLog("rdApp.*")  # parse failures are expected here
            return Chem.MolFromSmiles(value, sanitize=False) is not None
        except Exception:
            pass                            # fall through to the scanner
    return _scan_smiles(value)


def _smiles_to_pdbqt(smiles: str, out_path: str, name: str = "",
                     log_fn=log, use_wsl_backend: bool = False,
                     wsl_distro: str = "") -> bool:
    if rdkit_available():
        tmp_dir = tempfile.mkdtemp(prefix="ladock_agent_smi_")
        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem

            mol = Chem.MolFromSmiles(smiles)
            if mol is not None:
                mol = Chem.AddHs(mol)
                if name:
                    mol.SetProp("_Name", name)
                if AllChem.EmbedMolecule(mol, AllChem.ETKDGv3()) != 0:
                    AllChem.EmbedMolecule(mol, useRandomCoords=True)
                try:
                    AllChem.MMFFOptimizeMolecule(mol)
                except Exception:
                    pass
                sdf = os.path.join(tmp_dir, "smiles.sdf")
                writer = Chem.SDWriter(sdf)
                writer.write(mol)
                writer.close()
                if _file_to_pdbqt_meeko(sdf, out_path, log_fn):
                    return True
                if _file_to_pdbqt_obabel(sdf, out_path, False, log_fn, use_wsl_backend, wsl_distro):
                    return True
        except Exception as exc:
            if log_fn:
                log_fn(f"  RDKit SMILES conversion failed: {exc}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    obabel = _obabel_command(use_wsl_backend)
    cmd = [obabel, f"-:{smiles}", "--gen3d", "-O", out_path, "--partialcharge", "gasteiger"]
    if name:
        cmd += ["--title", name]
    return _run_capture(cmd, "obabel SMILES", log_fn, use_wsl_backend=use_wsl_backend,
                        wsl_distro=wsl_distro) and os.path.isfile(out_path)


def _split_records(path: str, marker: str) -> list[str]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if marker == "$$$$":
        return [rec.strip() + "\n$$$$\n" for rec in text.split("$$$$") if rec.strip()]
    chunks = text.split(marker)
    return [marker + chunk for chunk in chunks[1:] if chunk.strip()]


def _convert_file_to_pdbqt(in_path: str, out_path: str, pythonsh: str = "",
                           prep_lig: str = "", log_fn=log,
                           use_wsl_backend: bool = False,
                           wsl_distro: str = "") -> bool:
    ext = Path(in_path).suffix.lower()
    if ext == ".pdbqt":
        shutil.copy2(in_path, out_path)
        return True
    if ext == ".pdb" and _file_to_pdbqt_mgltools(in_path, out_path, pythonsh, prep_lig, log_fn,
                                                 use_wsl_backend, wsl_distro):
        return True
    if _file_to_pdbqt_meeko(in_path, out_path, log_fn):
        return True
    return _file_to_pdbqt_obabel(in_path, out_path, gen3d=ext in {".smi", ".smiles", ".txt"},
                                 log_fn=log_fn, use_wsl_backend=use_wsl_backend,
                                 wsl_distro=wsl_distro)


_SMILES_HEADERS = {"smiles", "smile", "canonical_smiles", "isomeric_smiles",
                   "smiles_string", "structure"}
_NAME_HEADERS = {"name", "id", "title", "molecule", "mol_name", "compound",
                 "compound_name", "ligand", "zinc_id", "chembl_id"}


def _name_column(lower: list[str], smiles_idx: int) -> int:
    """Index of the column holding molecule names, or -1.

    Real exports rarely use a column literally called "name": a ChEMBL dump has
    ``molecule_chembl_id`` and ``pref_name``. Falling through to "row_1, row_2,
    …" makes the results table impossible to map back to the input, so identifier
    -like columns are matched by shape too. Identifiers are preferred over names
    because names are often shared by many rows (in that ChEMBL dump every
    ``pref_name`` is the same target).
    """
    for i, col in enumerate(lower):                       # 1. exact match
        if i != smiles_idx and col in _NAME_HEADERS:
            return i
    for i, col in enumerate(lower):                       # 2. an identifier
        if i != smiles_idx and (col == "id" or col.endswith("_id")):
            return i
    for i, col in enumerate(lower):                       # 3. anything name-like
        if i != smiles_idx and ("name" in col or "title" in col):
            return i
    return -1


def _header_indices(cells: list[str]) -> tuple[int, int] | None:
    """``(smiles_idx, name_idx)`` when ``cells`` is a header row, else ``None``.

    A header is recognised by its column NAMES, never by content: the content
    test :func:`_looks_like_smiles` is deliberately permissive and happily
    accepts the word "compound", so using it here is exactly what made a
    headerless file silently lose its first molecule.
    """
    lower = [str(c).strip().lower() for c in cells]
    smiles_idx = next((i for i, c in enumerate(lower) if c in _SMILES_HEADERS), -1)
    if smiles_idx < 0:
        # No SMILES column named outright: still a header if a name-ish column
        # is present, in which case the molecules are in the first column.
        if not any(c in _NAME_HEADERS for c in lower):
            return None
        smiles_idx = 0
    return smiles_idx, _name_column(lower, smiles_idx)


def _row_to_smiles(row: list[str], smiles_idx: int, name_idx: int,
                   fallback_name: str) -> tuple[str, str] | None:
    if len(row) <= smiles_idx:
        return None
    smiles = row[smiles_idx].strip()
    if not _looks_like_smiles(smiles):
        for val in row:
            if _looks_like_smiles(val):
                smiles = val.strip()
                break
    if not _looks_like_smiles(smiles):
        return None
    name = row[name_idx].strip() if 0 <= name_idx < len(row) else fallback_name
    return name, smiles


def _iter_smiles_rows(path: str) -> Iterator[tuple[str, str]]:
    ext = Path(path).suffix.lower()
    if ext in {".csv", ".tsv"}:
        sep = "," if ext == ".csv" else "\t"
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh, delimiter=sep)
            first = next(reader, None)
            if first is None:
                return
            header = _header_indices(first)
            if header is None:               # headerless: row 1 is a molecule
                smiles_idx, name_idx = 0, -1
                rows: Iterable[list[str]] = itertools.chain([first], reader)
            else:
                smiles_idx, name_idx = header
                rows = reader
            for i, row in enumerate(rows, start=1):
                parsed = _row_to_smiles(row, smiles_idx, name_idx, f"row_{i}")
                if parsed:
                    yield parsed
        return
    if ext in {".xlsx", ".xls"}:
        try:
            import pandas as pd
        except ImportError:
            raise RuntimeError("pandas diperlukan untuk membaca Excel.")
        df = pd.read_excel(path)
        header = _header_indices([str(c) for c in df.columns])
        if header is None:
            # pandas consumed row 1 as column labels; it was data. Re-read it.
            df = pd.read_excel(path, header=None)
            smiles_idx, name_idx = 0, -1
        else:
            smiles_idx, name_idx = header
        for i, (_index, row) in enumerate(df.iterrows(), start=1):
            parsed = _row_to_smiles([str(v).strip() for v in row.tolist()],
                                    smiles_idx, name_idx, f"row_{i}")
            if parsed:
                yield parsed
        return
    for i, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        smiles = parts[0]
        name = parts[1].strip() if len(parts) > 1 else f"mol_{i}"
        if not _looks_like_smiles(smiles) and len(parts) > 1 and _looks_like_smiles(parts[1]):
            name, smiles = smiles, parts[1].strip()
        if _looks_like_smiles(smiles):
            yield name, smiles


def _pdbqt_ready(path: str) -> bool:
    """Whether ``path`` already holds a usable PDBQT from an earlier conversion.

    This is what makes the ligand cache safe to reuse across receptors and
    across re-runs. A truncated leftover from a crashed run is rejected: the
    file has to actually contain coordinates, not merely exist.
    """
    try:
        if os.path.getsize(path) == 0:
            return False
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return any(line.startswith(("ATOM", "HETATM")) for line in fh)
    except OSError:
        return False


def expand_to_pdbqt(lig_path: str, out_dir: str, pythonsh: str = "",
                    prep_lig: str = "", use_wsl_backend: bool = False,
                    wsl_distro: str = "", idx_offset: int = 0,
                    log_fn=log) -> list[tuple[str, str]]:
    ext = Path(lig_path).suffix.lower()
    base = safe_name(Path(lig_path).stem, "ligand")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, str]] = []

    if ext in {".smi", ".smiles", ".txt", ".csv", ".tsv", ".xlsx", ".xls"}:
        for i, (name, smiles) in enumerate(_iter_smiles_rows(lig_path), start=idx_offset):
            mol_name = safe_name(name, f"{base}_{i}")
            out = os.path.join(out_dir, f"{mol_name}_{i}.pdbqt")
            if _pdbqt_ready(out):
                results.append((mol_name, out))
                continue
            if log_fn:
                log_fn(f"  SMILES -> PDBQT: {mol_name}")
            if _smiles_to_pdbqt(smiles, out, mol_name, log_fn, use_wsl_backend, wsl_distro):
                results.append((mol_name, out))
        return results

    if ext == ".sdf":
        records = _split_records(lig_path, "$$$$")
        if len(records) > 1:
            for i, record in enumerate(records, start=idx_offset):
                title = record.splitlines()[0].strip() if record.splitlines() else f"{base}_{i}"
                mol_name = safe_name(title, f"{base}_{i}")
                out = os.path.join(out_dir, f"{mol_name}_{i}.pdbqt")
                if _pdbqt_ready(out):
                    results.append((mol_name, out))
                    continue
                tmp_sdf = os.path.join(out_dir, f"__tmp_{mol_name}_{i}.sdf")
                Path(tmp_sdf).write_text(record, encoding="utf-8")
                try:
                    if _convert_file_to_pdbqt(tmp_sdf, out, pythonsh, prep_lig, log_fn,
                                              use_wsl_backend, wsl_distro):
                        results.append((mol_name, out))
                finally:
                    if os.path.isfile(tmp_sdf):
                        os.unlink(tmp_sdf)
            return results

    if ext == ".mol2":
        records = _split_records(lig_path, "@<TRIPOS>MOLECULE")
        if len(records) > 1:
            for i, record in enumerate(records, start=idx_offset):
                lines = record.splitlines()
                title = lines[1].strip() if len(lines) > 1 else f"{base}_{i}"
                mol_name = safe_name(title, f"{base}_{i}")
                out = os.path.join(out_dir, f"{mol_name}_{i}.pdbqt")
                if _pdbqt_ready(out):
                    results.append((mol_name, out))
                    continue
                tmp_mol2 = os.path.join(out_dir, f"__tmp_{mol_name}_{i}.mol2")
                Path(tmp_mol2).write_text(record, encoding="utf-8")
                try:
                    if _convert_file_to_pdbqt(tmp_mol2, out, pythonsh, prep_lig, log_fn,
                                              use_wsl_backend, wsl_distro):
                        results.append((mol_name, out))
                finally:
                    if os.path.isfile(tmp_mol2):
                        os.unlink(tmp_mol2)
            return results

    out = os.path.join(out_dir, f"{base}_{idx_offset}.pdbqt")
    if _pdbqt_ready(out):
        return [(base, out)]
    if _convert_file_to_pdbqt(lig_path, out, pythonsh, prep_lig, log_fn,
                              use_wsl_backend, wsl_distro):
        return [(base, out)]
    if log_fn:
        log_fn(f"  Cannot convert ligand: {lig_path}")
    return []


def safe_name(value: str, fallback: str = "item") -> str:
    return re.sub(r"[^\w\-.]", "_", value or fallback) or fallback


def unique_labels(names: Iterable[str]) -> list[str]:
    """Directory-safe labels, disambiguated when a library repeats a name.

    Libraries routinely carry duplicate titles (an SDF where every record is
    called "Structure", a CSV whose name column is the target rather than the
    compound). Without this, two ligands share one output directory and the
    second silently overwrites the first's poses.
    """
    used: set[str] = set()
    labels: list[str] = []
    for name in names:
        base = safe_name(name)
        label, n = base, 1
        while label in used:
            n += 1
            label = f"{base}_{n}"
        used.add(label)
        labels.append(label)
    return labels


def _classify_atom_line(line: str) -> tuple[str, tuple, str] | None:
    """Classify one coordinate line as ``(component type, residue key, resname)``.

    ``None`` for anything that is not an ATOM/HETATM record.

    :func:`parse_pdb_components` and :func:`_component_atom_match` must agree
    *exactly* on this classification, so they share it. They used to have
    separate copies that had drifted: "Other" was built from ATOM records but
    matched against HETATM, so selecting it dropped nucleic acids from the
    receptor while quietly keeping the native ligand inside the pocket.
    """
    rec = line[:6].strip()
    if rec not in ("ATOM", "HETATM"):
        return None
    resname = line[17:20].strip()
    chain = line[21].strip() or "?"
    resseq = line[22:26].strip()
    elem = line[76:78].strip().upper() if len(line) > 76 else ""

    if rec == "ATOM" and resname in STANDARD_AA:
        return "Protein", (chain, resseq), resname
    if resname in WATER_RESNAMES:
        return "Water", (chain, resseq), resname
    if rec == "HETATM" and (elem in METAL_ELEMENTS or resname in METAL_ELEMENTS):
        return "Metal Ion", (chain, resname, resseq), resname
    if rec == "HETATM":
        return "Ligand", (chain, resname, resseq), resname
    return "Other", (chain, resname, resseq), resname


def parse_pdb_components(pdb_path: str) -> list[dict]:
    chains: dict[str, dict] = {}
    ligands: dict[tuple, dict] = {}
    # Residue keys are collected in sets: counting one residue per ATOM line
    # reported a 3-atom water as 3 residues.
    aggregates = {
        "Metal Ion": {"chain": "-", "resname": "Metals", "resseq": "", "type": "Metal Ion",
                      "resnames": set(), "residues": set(), "n_atoms": 0},
        "Water": {"chain": "-", "resname": "HOH", "resseq": "", "type": "Water",
                  "resnames": set(), "residues": set(), "n_atoms": 0},
        "Other": {"chain": "-", "resname": "Others", "resseq": "", "type": "Other",
                  "resnames": set(), "residues": set(), "n_atoms": 0},
    }

    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            classified = _classify_atom_line(line)
            if classified is None:
                continue
            ctype, key, resname = classified
            if ctype == "Protein":
                chain = key[0]
                chains.setdefault(chain, {
                    "chain": chain, "resname": f"Chain {chain}", "resseq": "",
                    "type": "Protein", "resseqs": set(), "n_atoms": 0,
                })
                chains[chain]["resseqs"].add(key[1])
                chains[chain]["n_atoms"] += 1
            elif ctype == "Ligand":
                ligands.setdefault(key, {
                    "chain": key[0], "resname": key[1], "resseq": key[2],
                    "type": "Ligand", "n_residues": 1, "n_atoms": 0,
                })
                ligands[key]["n_atoms"] += 1
            else:
                agg = aggregates[ctype]
                agg["resnames"].add(resname)
                agg["residues"].add(key)
                agg["n_atoms"] += 1

    result: list[dict] = []
    for info in sorted(chains.values(), key=lambda x: x["chain"]):
        info["n_residues"] = len(info.pop("resseqs"))
        result.append(info)
    result.extend(sorted(
        ligands.values(),
        key=lambda x: (
            x["chain"],
            x["resname"],
            int(x["resseq"]) if str(x["resseq"]).lstrip("-").isdigit() else 0,
        ),
    ))
    for ctype, default_label in (("Metal Ion", "Metal"), ("Water", "HOH"), ("Other", "Others")):
        agg = aggregates[ctype]
        if agg["n_atoms"] == 0:
            continue
        agg["n_residues"] = len(agg.pop("residues"))
        names = sorted(agg.pop("resnames"))
        if ctype != "Water":                 # waters stay labelled HOH
            agg["resname"] = ", ".join(names) or default_label
        result.append(agg)
    return result


def sanitize_pdb_text_for_mgltools(pdb_text: str) -> str:
    lines = pdb_text.splitlines(keepends=True)
    grouped: dict[tuple, list[str]] = {}
    passthrough: list[str] = []
    for line in lines:
        rec = line[:6].strip()
        if rec not in ("ATOM", "HETATM") or len(line) < 54:
            passthrough.append(line)
            continue
        key = (rec, line[12:16], line[17:20], line[21], line[22:26])
        grouped.setdefault(key, []).append(line)

    def alt_rank(line: str) -> tuple[int, str]:
        alt = line[16:17]
        return ({" ": 0, "A": 1, "1": 2}.get(alt, 3), alt)

    sanitized: list[str] = []
    seen: set[tuple] = set()
    for line in lines:
        rec = line[:6].strip()
        if rec not in ("ATOM", "HETATM") or len(line) < 54:
            continue
        key = (rec, line[12:16], line[17:20], line[21], line[22:26])
        if key in seen:
            continue
        seen.add(key)
        buf = list(min(grouped[key], key=alt_rank).rstrip("\n").ljust(80))
        buf[16] = " "
        buf[26] = " "
        sanitized.append("".join(buf).rstrip() + "\n")
    return "".join(sanitized + [line for line in passthrough
                                if line[:6].strip() not in ("ATOM", "HETATM")])


def compute_ligand_center(pdb_path: str, resname: str,
                          chain: str = "", resseq: str = "") -> tuple[float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line[:6].strip() not in ("ATOM", "HETATM"):
                continue
            if line[17:20].strip() != resname:
                continue
            if chain and line[21].strip() != chain:
                continue
            if resseq and line[22:26].strip() != resseq:
                continue
            try:
                xs.append(float(line[30:38]))
                ys.append(float(line[38:46]))
                zs.append(float(line[46:54]))
            except ValueError:
                pass
    if not xs:
        raise RuntimeError(f"Native ligand {resname!r} was not found in {pdb_path}")
    return round(sum(xs) / len(xs), 3), round(sum(ys) / len(ys), 3), round(sum(zs) / len(zs), 3)


def extract_native_ligand(pdb_path: str, out_pdb: str, resname: str,
                          chain: str = "", resseq: str = "") -> str:
    out: list[str] = []
    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line[:6].strip() != "HETATM":
                continue
            if line[17:20].strip() != resname:
                continue
            if chain and line[21].strip() != chain:
                continue
            if resseq and line[22:26].strip() != resseq:
                continue
            out.append(line)
    if not out:
        raise RuntimeError(f"Native ligand {resname!r} tidak ditemukan untuk ekstraksi.")
    out.append("END\n")
    Path(out_pdb).write_text("".join(out), encoding="utf-8")
    return out_pdb


def _component_atom_match(line: str, components: list[dict]) -> bool:
    """Whether this coordinate line belongs to one of the selected components."""
    classified = _classify_atom_line(line)
    if classified is None:
        return True                      # headers, TER, CONECT, … are kept
    ctype, key, _resname = classified
    for comp in components:
        if comp.get("type") != ctype:
            continue
        if ctype == "Protein":
            if key[0] == comp.get("chain"):
                return True
            continue                     # another chain may still match
        if ctype == "Ligand":
            if (key[0] == comp.get("chain")
                    and key[1] == comp.get("resname")
                    and key[2] == str(comp.get("resseq", ""))):
                return True
            continue
        return True                      # Water / Metal Ion / Other are aggregates
    return False


def extract_selected_components(pdb_path: str, out_pdb: str, components: list[dict]) -> str:
    if not components:
        shutil.copy2(pdb_path, out_pdb)
        return out_pdb
    out: list[str] = []
    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if _component_atom_match(line, components):
                out.append(line)
    if not any(line[:6].strip() in ("ATOM", "HETATM") for line in out):
        raise RuntimeError("Komponen receptor yang dipilih tidak menghasilkan atom.")
    if not any(line[:6].strip() == "END" for line in out):
        out.append("END\n")
    Path(out_pdb).write_text("".join(out), encoding="utf-8")
    return out_pdb


def find_flex_residues(pdb_path: str, cx: float, cy: float, cz: float,
                       cutoff: float) -> list[str]:
    seen: set[tuple[str, str, str]] = set()
    cutoff2 = cutoff * cutoff
    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line[:6].strip() != "ATOM":
                continue
            resname = line[17:20].strip()
            if resname not in STANDARD_AA:
                continue
            chain = line[21].strip()
            resseq = line[22:26].strip()
            try:
                ax, ay, az = float(line[30:38]), float(line[38:46]), float(line[46:54])
            except ValueError:
                continue
            if (ax - cx) ** 2 + (ay - cy) ** 2 + (az - cz) ** 2 <= cutoff2:
                seen.add((chain, resname, resseq))
    return [f"{ch}:{rn}:{rs}" for ch, rn, rs in sorted(seen)]


def run_cmd(cmd: list[str], tag: str, cwd: str | None = None,
            use_wsl_backend: bool = False, wsl_distro: str = "",
            timeout: float | None = None) -> None:
    """Run an engine, streaming its output, and fail if it exceeds ``timeout``.

    The output is pumped on a helper thread rather than read inline: a process
    that hangs without printing anything would block a plain ``for line in
    proc.stdout`` forever, which is how a single stuck engine used to freeze an
    entire overnight screening run.
    """
    exec_cmd, exec_cwd = prepare_subprocess(
        cmd, cwd=cwd, use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro
    )
    _vlog(f"\n> {tag}")
    _vlog("  $ " + " ".join(str(c) for c in exec_cmd))
    try:
        proc = subprocess.Popen(
            exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=exec_cwd, bufsize=1,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Executable not found: {exec_cmd[0]}") from exc
    assert proc.stdout is not None
    tail: deque[str] = deque(maxlen=20)   # short tail to explain a failure

    def _pump() -> None:
        for line in proc.stdout:          # type: ignore[union-attr]
            if _VERBOSE:
                log(line.rstrip())
            else:
                tail.append(line.rstrip())

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pump.join(timeout=5)
            detail = ("\n" + "\n".join(tail)) if tail else ""
            raise RuntimeError(
                f"{tag} exceeded the {timeout:g}s time limit and was killed "
                f"(raise or disable it with --timeout){detail}") from None
        pump.join(timeout=5)
    finally:
        # Screening runs spawn thousands of these; a leaked pipe per call would
        # eventually exhaust the process's file descriptors.
        try:
            proc.stdout.close()
        except OSError:
            pass
    if proc.returncode != 0:
        detail = ("\n" + "\n".join(tail)) if (tail and not _VERBOSE) else ""
        raise RuntimeError(f"{tag} failed with exit code {proc.returncode}{detail}")


@dataclass
class ToolConfig:
    vina_path: str
    ag4_path: str
    ad4_path: str
    autodockgpu: str
    pythonsh: str
    prepare_receptor: str
    prepare_ligand: str
    prepare_gpf: str
    prepare_dpf: str
    prepare_flexreceptor: str
    adfr_path: str = "adfr"
    agfr_path: str = "agfr"
    use_wsl_backend: bool = False
    wsl_distro: str = ""
    # Per-invocation wall-clock limit for every engine/preparation call.
    # None = no limit (what the CLI had before it could time out at all).
    timeout: float | None = None


@dataclass
class DockConfig:
    receptor: str
    ligands: list[str]
    out_dir: str
    receptor_components: list[dict] = field(default_factory=list)
    scoring: list[str] = field(default_factory=lambda: ["vina"])
    modes: list[str] = field(default_factory=lambda: ["rigid"])
    center: tuple[float, float, float] | None = None
    size: tuple[float, float, float] = (20.0, 20.0, 20.0)
    spacing: float = 0.375
    exhaustiveness: int = 8
    ad4_exhaustiveness: int = 8
    n_poses: int = 9
    energy_range: int = 3
    cpu: int = 4
    # None = don't pass a seed at all; any explicit value is forwarded verbatim.
    # (Vina's own default is --seed 0, which it reads as "pick one randomly", so
    # only a non-zero seed makes a Vina run reproducible. AutoDock4 takes its
    # seed from the DPF instead — see _set_dpf_seed.)
    seed: int | None = None
    ga_pop_size: int = 150
    cluster_rmsd: float = 2.0
    flex_residues: list[str] = field(default_factory=list)
    flex_distance: float = 3.0
    simultaneous: int = 1
    arrangement: str = "combination"
    max_groups: int = 5000          # MLSD safety net; 0 disables it
    jobs: int = 1                   # ligands docked concurrently
    ligand_cache_dir: str = ""      # shared PDBQT cache; "" = per-run directory
    grid_cache_dir: str = ""        # AutoGrid4 map store; "" = platform cache dir
    grid_cache_enabled: bool = True
    native_ligand: str = ""
    native_chain: str = ""
    native_resseq: str = ""


def resolve_tools(args) -> ToolConfig:
    use_wsl_backend = bool(args.use_wsl)
    raw_timeout = getattr(args, "timeout", None)
    wsl_distro = args.wsl_distro or ""
    if use_wsl_backend and os.name == "nt" and not wsl_available():
        raise RuntimeError("WSL backend was requested, but wsl.exe is not available.")

    mgldir = resolve_mgltools_dir(args.mgltools or "", use_wsl_backend=use_wsl_backend)
    if mgldir and os.path.isdir(mgldir):
        pythonsh = os.path.join(mgldir, "bin", "pythonsh")
        util24 = os.path.join(mgldir, "MGLToolsPckgs", "AutoDockTools", "Utilities24")
        prep_rec = os.path.join(util24, "prepare_receptor4.py")
        prep_lig = os.path.join(util24, "prepare_ligand4.py")
        prep_gpf = os.path.join(util24, "prepare_gpf4.py")
        prep_dpf = os.path.join(util24, "prepare_dpf42.py")
        prep_flex = os.path.join(util24, "prepare_flexreceptor4.py")
    else:
        pythonsh = args.pythonsh or "pythonsh"
        prep_rec = "prepare_receptor4.py"
        prep_lig = "prepare_ligand4.py"
        prep_gpf = "prepare_gpf4.py"
        prep_dpf = "prepare_dpf42.py"
        prep_flex = "prepare_flexreceptor4.py"

    suite = resolve_adfrsuite_dir(getattr(args, "adfrsuite", "") or "",
                                 use_wsl_backend=use_wsl_backend)
    suite_bin = (lambda n: os.path.join(suite, "bin", n)) if suite else (lambda n: "")

    return ToolConfig(
        vina_path=resolve_tool_path("vina", args.vina or "", use_wsl_backend=use_wsl_backend),
        ag4_path=resolve_tool_path("autogrid4", args.autogrid4 or "", use_wsl_backend=use_wsl_backend),
        ad4_path=resolve_tool_path("autodock4", args.autodock4 or "", use_wsl_backend=use_wsl_backend),
        autodockgpu=resolve_tool_path("autodock_gpu", args.autodock_gpu or "", use_wsl_backend=use_wsl_backend),
        pythonsh=pythonsh,
        prepare_receptor=prep_rec,
        prepare_ligand=prep_lig,
        prepare_gpf=prep_gpf,
        prepare_dpf=prep_dpf,
        prepare_flexreceptor=prep_flex,
        adfr_path=resolve_tool_path("adfr", suite_bin("adfr"), use_wsl_backend=use_wsl_backend),
        agfr_path=resolve_tool_path("agfr", suite_bin("agfr"), use_wsl_backend=use_wsl_backend),
        use_wsl_backend=use_wsl_backend,
        wsl_distro=wsl_distro,
        timeout=(float(raw_timeout) if raw_timeout else None),
    )


def validate_rules(cfg: DockConfig, tools: ToolConfig) -> None:
    unknown = set(cfg.scoring) - ALL_SCORING
    if unknown:
        raise RuntimeError(f"Unknown scoring function(s): {', '.join(sorted(unknown))}")
    if cfg.simultaneous < 1:
        raise RuntimeError("--simultaneous must be >= 1.")
    if cfg.arrangement not in ("combination", "permutation"):
        raise RuntimeError("--arrangement must be 'combination' or 'permutation'.")
    if cfg.simultaneous > 1:
        if not (VINA_SCORING & set(cfg.scoring)):
            raise RuntimeError(
                "MLSD (--simultaneous > 1) only supports Vina/Vinardo scoring; "
                "AD4/AD4-GPU cannot dock multiple ligands simultaneously."
            )
        # NOTE: don't count --ligand file arguments here — one file (SDF/SMILES/
        # CSV) can hold many molecules. The real "enough ligands" check runs in
        # dock() after the library is expanded to individual PDBQTs.
        #
        # An oversized MLSD job is still worth catching now, from a cheap count
        # of the library, so the user is not billed a full ligand-preparation
        # pass before being told the run is too large.
        if cfg.max_groups:
            estimate = _count_library_molecules(cfg.ligands)
            groups = mlsd_group_count(estimate, cfg.simultaneous, cfg.arrangement)
            if groups > cfg.max_groups:
                raise RuntimeError(
                    f"MLSD would run about {groups:,} groups "
                    f"(~{estimate} ligands taken {cfg.simultaneous} at a time, "
                    f"{cfg.arrangement}) — over the --max-groups limit of "
                    f"{cfg.max_groups:,}. Use a smaller library, a smaller "
                    f"--simultaneous, 'combination' instead of 'permutation', or "
                    f"raise --max-groups (0 disables the limit).")
    if "flexible" in cfg.modes and not cfg.flex_residues:
        if cfg.center is None:
            raise RuntimeError("Flexible mode needs a box center to auto-detect residues.")
        cfg.flex_residues = find_flex_residues(
            cfg.receptor, cfg.center[0], cfg.center[1], cfg.center[2], cfg.flex_distance
        )
        if not cfg.flex_residues:
            raise RuntimeError("No flexible residues were found. Use --flex-residue or increase --flex-distance.")
    if ADFR_SCORING & set(cfg.scoring):
        missing = [f"{label}: {path}" for label, path in
                   (("agfr", tools.agfr_path), ("adfr", tools.adfr_path))
                   if not (os.path.isfile(path) or shutil.which(path))]
        if missing:
            raise RuntimeError(
                "ADFR scoring needs ADFRsuite (agfr + adfr):\n" + "\n".join(missing)
                + "\nInstall it from https://ccsb.scripps.edu/adfr/downloads/ and "
                  "point --adfrsuite (or $ADFRSUITE_HOME) at the directory.")
    needs_mgl = bool(AD4_SCORING & set(cfg.scoring)) or "flexible" in cfg.modes
    if needs_mgl:
        missing = []
        for label, path in [
            ("pythonsh", tools.pythonsh),
            ("prepare_gpf4.py", tools.prepare_gpf),
            ("prepare_flexreceptor4.py", tools.prepare_flexreceptor),
        ]:
            if not os.path.isfile(path):
                missing.append(f"{label}: {path}")
        if missing:
            raise RuntimeError("MGLTools is required for AD4/AD4-GPU/flexible mode:\n" + "\n".join(missing))
    if VINA_SCORING & set(cfg.scoring) and not (os.path.isfile(tools.vina_path) or shutil.which(tools.vina_path)):
        raise RuntimeError(
            f"Vina executable was not found: {tools.vina_path}\n"
            f"Docking engines are not part of the pip install. Fetch them once:\n"
            f"    ladock-fetch-binaries")
    if not meeko_available() and not os.path.isfile(tools.prepare_receptor):
        raise RuntimeError("Meeko is not available and MGLTools receptor preparation was not found.")


def prepare_receptor_pdbqt(receptor: str, out_pdbqt: str, tools: ToolConfig) -> str:
    if receptor.lower().endswith(".pdbqt"):
        shutil.copy2(receptor, out_pdbqt)
        return out_pdbqt
    done = False
    if meeko_available():
        _vlog("Preparing receptor with Meeko native prep...")
        done = native_prepare_receptor(receptor, out_pdbqt, log)
    if not done and os.path.isfile(tools.pythonsh) and os.path.isfile(tools.prepare_receptor):
        _vlog("Meeko receptor prep did not finish; trying MGLTools...")
        run_cmd(
            [tools.pythonsh, tools.prepare_receptor, "-r", receptor, "-o", out_pdbqt,
             "-A", "hydrogens", "-U", "nphs_lps"],
            "prepare_receptor4.py",
            use_wsl_backend=tools.use_wsl_backend,
            wsl_distro=tools.wsl_distro,
            timeout=tools.timeout,
        )
    if not os.path.isfile(out_pdbqt):
        raise RuntimeError(f"Receptor preparation did not produce {out_pdbqt}")
    return out_pdbqt


def _flexres_spec(rec_pdbqt: str, flex_residues: list[str]) -> str:
    """Build the -s argument prepare_flexreceptor4.py expects:
    ``<molname>:<chain>:<RESnameRESnum>_...`` (comma-separated per chain).
    The molecule name is the receptor PDBQT stem; residues arrive as
    ``chain:resname:resseq`` and become e.g. ``receptor:A:ALA55_ASN51_MET98``."""
    stem = Path(rec_pdbqt).stem
    by_chain: dict[str, list[str]] = {}
    for res in flex_residues:
        parts = res.split(":")
        if len(parts) != 3:
            continue
        chain, resname, resseq = parts
        by_chain.setdefault(chain, []).append(f"{resname}{resseq}")
    chain_specs = ",".join(f"{chain}:{'_'.join(res)}" for chain, res in by_chain.items())
    return f"{stem}:{chain_specs}"


def split_flexible_receptor(rec_pdbqt: str, flex_residues: list[str],
                            tmp_dir: str, tools: ToolConfig) -> tuple[str, str]:
    rigid = os.path.join(tmp_dir, "rigid.pdbqt")
    flex = os.path.join(tmp_dir, "flex.pdbqt")
    flex_spec = _flexres_spec(rec_pdbqt, flex_residues)
    run_cmd(
        [tools.pythonsh, tools.prepare_flexreceptor, "-r", rec_pdbqt, "-s", flex_spec,
         "-g", rigid, "-x", flex],
        "prepare_flexreceptor4.py",
        use_wsl_backend=tools.use_wsl_backend,
        wsl_distro=tools.wsl_distro,
        timeout=tools.timeout,
    )
    # An empty flex file means no residue matched -s: fail loudly instead of
    # silently docking rigid (Vina ignores an empty --flex; AD4 errors on it).
    if not os.path.isfile(rigid) or not os.path.isfile(flex) or os.path.getsize(flex) == 0:
        raise RuntimeError(
            "Flexible receptor split produced no flexible residues "
            f"(spec: {flex_spec}). Check --flex-residue / --flex-distance.")
    return rigid, flex


def ligand_atom_types(pdbqt_path: str) -> frozenset[str]:
    """The AutoDock atom types present in a PDBQT (its last column).

    AutoGrid4 computes one map per ligand atom type, so two ligands with the
    same type set need exactly the same maps — this is the cache key that lets a
    screening run build the grids once instead of once per ligand.
    """
    types: set[str] = set()
    with open(pdbqt_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line[:6].strip() in ("ATOM", "HETATM") and len(line) > 77:
                atype = line[77:79].strip()
                if atype:
                    types.add(atype)
    return frozenset(types)


def _link_or_copy(src: str, dst: str) -> None:
    """Hard-link a file, falling back to a copy across filesystems.

    Grid maps run to tens of megabytes per ligand; linking keeps the tidy
    per-ligand output layout without paying for the bytes again.
    """
    if os.path.exists(dst):
        return
    try:
        os.link(src, dst)
    except (OSError, AttributeError):
        shutil.copy2(src, dst)


_GRID_FILE_SUFFIXES = (".map", ".maps.fld", ".maps.xyz")


def _types_slug(types: frozenset[str]) -> str:
    """Stable, readable directory name for an atom-type set.

    Deliberately not ``hash()``: that is salted per process, so the cache
    directory would change name on every run.
    """
    joined = "_".join(sorted(types)) or "none"
    if len(joined) <= 40:
        return f"types_{joined}"
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:10]
    return f"types_{joined[:30]}_{digest}"


def build_ad4_grids(cfg: DockConfig, tools: ToolConfig, grid_dir: str,
                    rec_pdbqt: str, lig_pdbqt: str, flex_pdbqt: str = "") -> tuple[str, str]:
    """Run prepare_gpf4 + autogrid4 in ``grid_dir``; return ``(fld, gpf)``."""
    os.makedirs(grid_dir, exist_ok=True)
    gpf = os.path.join(grid_dir, "grid.gpf")
    glg = os.path.join(grid_dir, "grid.glg")
    local_rec = os.path.join(grid_dir, os.path.basename(rec_pdbqt))
    local_lig = os.path.join(grid_dir, os.path.basename(lig_pdbqt))
    if not os.path.isfile(local_rec):
        shutil.copy2(rec_pdbqt, local_rec)
    shutil.copy2(lig_pdbqt, local_lig)
    cmd = [
        tools.pythonsh, tools.prepare_gpf,
        "-r", local_rec, "-l", local_lig, "-o", gpf,
        "-p", f"npts={_grid_points(cfg.size[0], cfg.spacing)},{_grid_points(cfg.size[1], cfg.spacing)},{_grid_points(cfg.size[2], cfg.spacing)}",
        "-p", f"spacing={cfg.spacing}",
        "-p", f"gridcenter={cfg.center[0]},{cfg.center[1]},{cfg.center[2]}",
    ]
    if flex_pdbqt:
        local_flex = os.path.join(grid_dir, os.path.basename(flex_pdbqt))
        if not os.path.isfile(local_flex):
            shutil.copy2(flex_pdbqt, local_flex)
        cmd += ["-x", local_flex]
    run_cmd(cmd, "prepare_gpf4.py", use_wsl_backend=tools.use_wsl_backend,
            wsl_distro=tools.wsl_distro, timeout=tools.timeout)
    run_cmd([tools.ag4_path, "-p", gpf, "-l", glg], "autogrid4", cwd=grid_dir,
            use_wsl_backend=tools.use_wsl_backend, wsl_distro=tools.wsl_distro,
            timeout=tools.timeout)
    fld_files = [f for f in os.listdir(grid_dir) if f.endswith(".maps.fld")]
    if not fld_files:
        raise RuntimeError(f"AutoGrid4 did not produce .maps.fld. Check {glg}")
    return os.path.join(grid_dir, fld_files[0]), gpf


_COMPLETE_MARKER = ".ladock-grid-complete"


def _file_digest(path: str, _memo: dict = {}) -> str:
    """SHA-1 of a file, memoised on (path, mtime, size).

    The receptor is hashed once per screen rather than once per ligand.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return ""
    key = (path, stat.st_mtime_ns, stat.st_size)
    if key in _memo:
        return _memo[key]
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    _memo[key] = digest.hexdigest()
    return _memo[key]


def grid_cache_key(cfg: DockConfig, mode: str, rec_pdbqt: str, lig_pdbqt: str,
                   flex_pdbqt: str = "") -> str:
    """Identity of a set of AutoGrid4 maps: everything the maps depend on.

    That is the receptor (by content, not by name), the box, the flexible-residue
    split, and the ligand's atom types — but nothing else about the ligand. Two
    runs that agree on all of it can share maps, which is what makes the cache
    safe to keep on disk between invocations.
    """
    digest = hashlib.sha1()
    digest.update(mode.encode("utf-8"))
    digest.update(repr((cfg.center, cfg.size, cfg.spacing)).encode("utf-8"))
    digest.update(_file_digest(rec_pdbqt).encode("utf-8"))
    digest.update(_file_digest(flex_pdbqt).encode("utf-8") if flex_pdbqt else b"-")
    digest.update(",".join(sorted(ligand_atom_types(lig_pdbqt))).encode("utf-8"))
    return digest.hexdigest()[:16]


class _GridCache:
    """Build AutoGrid4 maps once per grid identity and reuse them.

    The maps depend on the receptor, the box and which atom types the ligand
    contains — not on the ligand itself. Screening a library used to rerun
    AutoGrid4 for every single ligand, which dominates the wall-clock time of an
    AD4 screen.

    The store lives outside the run directory (see :func:`ladock.paths.cache_root`)
    so a second invocation reuses the maps too, and is published by an atomic
    rename with a completion marker: a killed run can never leave a half-written
    map set that a later run would treat as valid. Built maps are hard-linked
    into each ligand's own directory, so the per-ligand output layout is
    unchanged and the bytes are not paid for twice.
    """

    def __init__(self, root: Path, cfg: DockConfig, tools: ToolConfig):
        self.root = root
        self._cfg = cfg
        self._tools = tools
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._built: dict[str, tuple[str, str]] = {}
        self.hits = 0          # already built in this process
        self.disk_hits = 0     # found in the on-disk store from an earlier run
        self.misses = 0        # actually ran AutoGrid4

    @staticmethod
    def _fld_in(directory: Path) -> str:
        for name in sorted(os.listdir(directory)):
            if name.endswith(".maps.fld"):
                return str(directory / name)
        return ""

    def _publish(self, key: str, rec_pdbqt: str, lig_pdbqt: str,
                 flex_pdbqt: str) -> tuple[str, str]:
        """Build maps in a staging directory and move them into the store."""
        cache_dir = self.root / key
        staging = self.root / f".building-{key}-{os.getpid()}-{threading.get_ident()}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            build_ad4_grids(self._cfg, self._tools, str(staging),
                            rec_pdbqt, lig_pdbqt, flex_pdbqt)
            (staging / _COMPLETE_MARKER).write_text("ok\n", encoding="utf-8")
            try:
                os.replace(staging, cache_dir)
            except OSError:
                # Another process published the same maps first: prefer theirs.
                if not (cache_dir / _COMPLETE_MARKER).exists():
                    raise
                shutil.rmtree(staging, ignore_errors=True)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self._fld_in(cache_dir), str(cache_dir / "grid.gpf")

    def materialise(self, mode: str, dest_dir: Path, rec_pdbqt: str,
                    lig_pdbqt: str, flex_pdbqt: str = "") -> tuple[str, str]:
        """Ensure ``dest_dir`` holds usable maps; return ``(fld, gpf)`` inside it."""
        key = grid_cache_key(self._cfg, mode, rec_pdbqt, lig_pdbqt, flex_pdbqt)
        with self._lock:
            per_key = self._locks.setdefault(key, threading.Lock())
        with per_key:                      # one builder per key, even in parallel
            if key in self._built:
                self.hits += 1
            else:
                self.root.mkdir(parents=True, exist_ok=True)
                cache_dir = self.root / key
                if (cache_dir / _COMPLETE_MARKER).is_file() and self._fld_in(cache_dir):
                    self._built[key] = (self._fld_in(cache_dir), str(cache_dir / "grid.gpf"))
                    self.disk_hits += 1
                else:
                    self._built[key] = self._publish(key, rec_pdbqt, lig_pdbqt, flex_pdbqt)
                    self.misses += 1
            src_fld, src_gpf = self._built[key]

        cache_dir = Path(src_fld).parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in os.listdir(cache_dir):
            if name.endswith(_GRID_FILE_SUFFIXES):
                _link_or_copy(str(cache_dir / name), str(dest_dir / name))
        # The receptor PDBQT is referenced by the .fld/.dpf by relative name.
        rec_name = os.path.basename(rec_pdbqt)
        if os.path.isfile(cache_dir / rec_name):
            _link_or_copy(str(cache_dir / rec_name), str(dest_dir / rec_name))
        return str(dest_dir / os.path.basename(src_fld)), src_gpf


def _grid_points(size: float, spacing: float) -> int:
    n = max(2, round(size / spacing))
    return n + (n % 2)


def run_vina(cfg: DockConfig, tools: ToolConfig, mode_dir: str, rec_pdbqt: str,
             lig_pdbqt, sf: str, flex_pdbqt: str = "") -> str:
    # lig_pdbqt may be a single path (str) or a list of paths (MLSD: multiple
    # ligands docked simultaneously in one pocket). Vina 1.2 accepts several
    # values after a single --ligand flag.
    lig_paths = [lig_pdbqt] if isinstance(lig_pdbqt, str) else list(lig_pdbqt)
    out = os.path.join(mode_dir, f"out_{sf}.pdbqt")
    cmd = [
        tools.vina_path,
        "--receptor", rec_pdbqt,
        "--ligand", *lig_paths,
        "--scoring", sf,
        "--center_x", str(cfg.center[0]),
        "--center_y", str(cfg.center[1]),
        "--center_z", str(cfg.center[2]),
        "--size_x", str(cfg.size[0]),
        "--size_y", str(cfg.size[1]),
        "--size_z", str(cfg.size[2]),
        "--exhaustiveness", str(cfg.exhaustiveness),
        "--num_modes", str(cfg.n_poses),
        "--energy_range", str(cfg.energy_range),
        "--cpu", str(cfg.cpu),
        "--out", out,
    ]
    if cfg.seed is not None:
        cmd += ["--seed", str(cfg.seed)]
    if flex_pdbqt:
        cmd += ["--flex", flex_pdbqt]
    run_cmd(cmd, f"AutoDock Vina ({sf})", use_wsl_backend=tools.use_wsl_backend,
            wsl_distro=tools.wsl_distro, timeout=tools.timeout)
    if not os.path.isfile(out):
        raise RuntimeError(f"Vina did not produce {out}")
    return out


def _set_dpf_seed(dpf: str, seed: int) -> None:
    """Force AutoDock4's RNG seed by editing the generated DPF.

    AD4's ``seed`` directive takes TWO values, which cannot be expressed through
    prepare_dpf42.py's ``-p key=value``; the seed was therefore never applied and
    ``--seed`` had no effect on AD4 runs at all. The second value is derived from
    the first so one ``--seed`` still gives a fully reproducible run.
    """
    lines = [ln for ln in Path(dpf).read_text(encoding="utf-8", errors="replace").splitlines()
             if not ln.strip().startswith("seed ")]
    directive = f"seed {seed} {seed + 1}"
    # AD4 reads the DPF top-down, so the seed has to precede the search setup.
    for i, line in enumerate(lines):
        if line.strip().startswith("autodock_parameter_version"):
            lines.insert(i + 1, directive)
            break
    else:
        lines.insert(0, directive)
    Path(dpf).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_ad4(cfg: DockConfig, tools: ToolConfig, mode_dir: str, rec_pdbqt: str,
            lig_pdbqt: str, gpf: str, flex_pdbqt: str = "") -> str:
    dpf = os.path.join(mode_dir, "dock.dpf")
    dlg = os.path.join(mode_dir, "dock_ad4.dlg")
    local_rec = os.path.join(mode_dir, os.path.basename(rec_pdbqt))
    # Use a SHORT ligand filename: prepare_dpf42.py pads the 'move' line to a
    # fixed column and, for long basenames, appends "# small molecule" with no
    # space, so autodock4 tries to open "<name>.pdbqt#" and fails.
    local_lig = os.path.join(mode_dir, "ligand.pdbqt")
    if not os.path.isfile(local_rec):
        shutil.copy2(rec_pdbqt, local_rec)
    shutil.copy2(lig_pdbqt, local_lig)
    cmd = [
        tools.pythonsh, tools.prepare_dpf,
        "-r", local_rec, "-l", local_lig, "-o", dpf,
        "-p", f"ga_num_evals={cfg.ad4_exhaustiveness * 250000}",
        "-p", f"ga_run={cfg.n_poses}",
        "-p", f"ga_pop_size={cfg.ga_pop_size}",
        "-p", f"rmstol={cfg.cluster_rmsd}",
    ]
    if flex_pdbqt:
        local_flex = os.path.join(mode_dir, os.path.basename(flex_pdbqt))
        if not os.path.isfile(local_flex):
            shutil.copy2(flex_pdbqt, local_flex)
        cmd += ["-x", local_flex]
    run_cmd(cmd, "prepare_dpf42.py", use_wsl_backend=tools.use_wsl_backend,
            wsl_distro=tools.wsl_distro, timeout=tools.timeout)
    if cfg.seed is not None and os.path.isfile(dpf):
        _set_dpf_seed(dpf, cfg.seed)
    run_cmd([tools.ad4_path, "-p", dpf, "-l", dlg], "autodock4", cwd=mode_dir,
            use_wsl_backend=tools.use_wsl_backend, wsl_distro=tools.wsl_distro,
            timeout=tools.timeout)
    if not os.path.isfile(dlg):
        raise RuntimeError(f"AutoDock4 did not produce {dlg}")
    return dlg


def run_adgpu(cfg: DockConfig, tools: ToolConfig, mode_dir: str,
              lig_pdbqt: str, fld: str, flex_pdbqt: str = "") -> str:
    dlg_base = os.path.join(mode_dir, "dock_adgpu")
    cmd = [
        tools.autodockgpu,
        "--lfile", lig_pdbqt,
        "--ffile", fld,
        "--resnam", dlg_base,
        "--nrun", str(cfg.n_poses),
        "--nev", str(cfg.ad4_exhaustiveness * 250000),
    ]
    if cfg.seed is not None:
        # AutoDock-GPU takes up to THREE comma-separated seeds and fills any it
        # is not given from time and process id, so a single value still left
        # two sources of entropy. Derive all three from the one seed given.
        #
        # NOTE: this pins AD-GPU's RNG but does NOT make the run bit-identical —
        # the GPU reduction order varies between runs, so repeated seeded runs
        # still differ by ~0.1 kcal/mol. Seed Vina or AutoDock4 instead when an
        # exactly reproducible number is required.
        s = cfg.seed
        cmd += ["--seed", f"{s},{s + 1},{s + 2}"]
    if flex_pdbqt:
        cmd += ["--flexres", flex_pdbqt]
    run_cmd(cmd, "AutoDock-GPU", cwd=mode_dir, use_wsl_backend=tools.use_wsl_backend,
            wsl_distro=tools.wsl_distro, timeout=tools.timeout)
    dlg = dlg_base + ".dlg"
    if not os.path.isfile(dlg):
        raise RuntimeError(f"AutoDock-GPU did not produce {dlg}")
    return dlg


def _agfr_flex_spec(flex_residues: list[str]) -> str:
    """AGFR's flexible-residue syntax: ``A:ILE10,VAL32;B:SER48``.

    Different from prepare_flexreceptor4.py's format, which also embeds the
    molecule name — hence a separate formatter rather than reusing _flexres_spec.
    """
    by_chain: dict[str, list[str]] = {}
    for res in flex_residues:
        parts = res.split(":")
        if len(parts) != 3:
            continue
        chain, resname, resseq = parts
        by_chain.setdefault(chain, []).append(f"{resname}{resseq}")
    return ";".join(f"{chain}:{','.join(res)}" for chain, res in by_chain.items())


def build_adfr_target(cfg: DockConfig, tools: ToolConfig, target_dir: str,
                      rec_pdbqt: str, flex_residues: list[str] | None = None) -> str:
    """Run AGFR to produce the ``.trg`` target ADFR docks into.

    The target depends on the receptor, the box and the flexible residues but
    NOT on the ligand, so one target serves a whole library — unlike AutoGrid4,
    whose maps also depend on the ligand's atom types.
    """
    os.makedirs(target_dir, exist_ok=True)
    local_rec = os.path.join(target_dir, os.path.basename(rec_pdbqt))
    if not os.path.isfile(local_rec):
        shutil.copy2(rec_pdbqt, local_rec)
    stem = os.path.join(target_dir, "target")
    cx, cy, cz = cfg.center
    sx, sy, sz = cfg.size
    cmd = [
        tools.agfr_path,
        "-r", local_rec,
        "-b", "user", str(cx), str(cy), str(cz), str(sx), str(sy), str(sz),
        "-o", stem,
    ]
    spec = _agfr_flex_spec(flex_residues or [])
    if spec:
        cmd += ["-f", spec]
    run_cmd(cmd, "agfr", cwd=target_dir, use_wsl_backend=tools.use_wsl_backend,
            wsl_distro=tools.wsl_distro, timeout=tools.timeout)
    trg = stem + ".trg"
    if not os.path.isfile(trg):
        raise RuntimeError(f"AGFR did not produce {trg}")
    return trg


def run_adfr(cfg: DockConfig, tools: ToolConfig, mode_dir: str,
             lig_pdbqt: str, trg: str) -> str:
    """Dock one ligand with ADFR; return the poses PDBQT."""
    os.makedirs(mode_dir, exist_ok=True)
    # ADFR names its output "<ligand stem>_<jobName>_out.pdbqt" in the working
    # directory, so the ligand is copied in under a short, predictable stem.
    local_lig = os.path.join(mode_dir, "ligand.pdbqt")
    shutil.copy2(lig_pdbqt, local_lig)
    job = "adfr"
    cmd = [
        tools.adfr_path,
        "-l", local_lig,
        "-t", trg,
        "-J", job,
        "-c", str(cfg.cpu),
        "-n", str(cfg.n_poses),
    ]
    if cfg.seed is not None:
        cmd += ["-S", str(cfg.seed)]
    run_cmd(cmd, "ADFR", cwd=mode_dir, use_wsl_backend=tools.use_wsl_backend,
            wsl_distro=tools.wsl_distro, timeout=tools.timeout)
    out = os.path.join(mode_dir, f"ligand_{job}_out.pdbqt")
    if not os.path.isfile(out):
        raise RuntimeError(f"ADFR did not produce {out}")
    return out


def _strip_waters(in_pdb: str, out_pdb: str) -> str:
    """Write a copy of the receptor PDB with crystallographic waters removed.
    Waters have no docking parameters and break AutoGrid4/MGLTools receptor prep
    (and add noise for Vina). The native ligand and protein are kept."""
    kept: list[str] = []
    for line in Path(in_pdb).read_text(encoding="utf-8", errors="replace").splitlines():
        if line[:6].strip() in ("ATOM", "HETATM") and line[17:20].strip() in WATER_RESNAMES:
            continue
        kept.append(line)
    Path(out_pdb).write_text("\n".join(kept) + "\n", encoding="utf-8")
    return out_pdb


def parse_result(out_path: str, sf: str) -> dict:
    text = Path(out_path).read_text(encoding="utf-8", errors="replace")
    if sf == "adfr":
        # ADFR writes the free energy of binding on each pose's USER SCORE line:
        #   USER: SCORE 12.219520 LL: -0.881 LR: -11.339 RR: 0.000 FEB: -9.847
        # FEB is the comparable number and carries more precision than the one
        # decimal place of the summary table.
        for line in text.splitlines():
            match = re.search(r"FEB:\s*([-\d.]+)", line)
            if match:
                return {"energy": match.group(1), "rmsd_lb": "", "rmsd_ub": ""}
        return {"energy": "", "rmsd_lb": "", "rmsd_ub": ""}
    if sf.endswith("ad4") or sf.endswith("ad4gpu"):
        for line in text.splitlines():
            match = re.match(r"\s*RANKING\s+1\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)", line)
            if match:
                return {"energy": match.group(1), "rmsd_lb": match.group(2), "rmsd_ub": match.group(3)}
        for line in text.splitlines():
            if "Estimated Free Energy of Binding" in line:
                match = re.search(r"=\s*([-\d.]+)", line)
                if match:
                    return {"energy": match.group(1), "rmsd_lb": "", "rmsd_ub": ""}
    else:
        for line in text.splitlines():
            if line.startswith("REMARK VINA RESULT:"):
                parts = line.split()
                return {
                    "energy": parts[3] if len(parts) > 3 else "",
                    "rmsd_lb": parts[4] if len(parts) > 4 else "",
                    "rmsd_ub": parts[5] if len(parts) > 5 else "",
                }
    return {"energy": "", "rmsd_lb": "", "rmsd_ub": ""}


def mlsd_group_count(n: int, k: int, arrangement: str) -> int:
    """How many MLSD groups a library of ``n`` ligands yields, without enumerating.

    Counting by enumeration is not an option: 2000 ligands taken 3 at a time is
    1.3 billion tuples, which froze the wizard for minutes before a single
    docking run had started.
    """
    if k < 1 or k > n:
        return 0
    return math.perm(n, k) if arrangement == "permutation" else math.comb(n, k)


def mlsd_groups(items: list, k: int, arrangement: str) -> Iterator[tuple]:
    """Lazily yield the MLSD groups. Never materialise them — see the count above."""
    gen = itertools.permutations if arrangement == "permutation" else itertools.combinations
    return gen(items, k)


class _DockReporter:
    """Renders the CLI's own docking output: a streaming result table with a
    per-ligand percent for screening runs, and a final best-hit footer."""

    def __init__(self, title: str, subtitle: str, show_pct: bool):
        self.show_pct = show_pct
        self.best: tuple[str, str, float] | None = None
        self._hdr = False
        self._rule_w = 0
        self._lock = threading.Lock()
        self._done = 0
        ui_header(title, subtitle)

    def tick(self, total: int) -> int | None:
        """Count one finished unit of work and return the percentage so far.

        With --jobs > 1 the ligands finish out of order, so progress is the
        number completed rather than the index of the one being started.
        """
        if not self.show_pct or total <= 0:
            return None
        with self._lock:
            self._done += 1
            return int(self._done * 100 / total)

    def _print_header(self) -> None:
        dg = glyph("ΔG (kcal/mol)", "dG (kcal/mol)")
        cols = f"{'Ligand'.ljust(26)} {'Engine'.ljust(8)} {'Mode'.ljust(8)} {dg}"
        pw = 7 if self.show_pct else 0           # width of the "[ 20%] " prefix
        self._rule_w = len(cols) + pw
        print("   " + (" " * pw) + sty(cols, "gray"))
        print("   " + sty(glyph("─", "-") * self._rule_w, "teal"))
        self._hdr = True

    def emit(self, ligand: str, engine: str, mode: str, energy, pct=None) -> None:
        with self._lock:                 # keeps rows intact when --jobs > 1
            self._emit(ligand, engine, mode, energy, pct)

    def _emit(self, ligand: str, engine: str, mode: str, energy, pct=None) -> None:
        if not self._hdr:
            self._print_header()
        name = ligand if len(ligand) <= 26 else ligand[:25] + glyph("…", "~")
        try:
            ev = float(energy)
            etxt = sty(f"{ev:+.2f}", "green" if ev < 0 else "yellow")
            if self.best is None or ev < self.best[2]:
                self.best = (ligand, engine, ev)
        except (TypeError, ValueError):
            etxt = sty("n/a", "yellow")
        prefix = ""
        if self.show_pct:
            prefix = sty(f"[{pct:3d}%] ", "cyan", "bold") if pct is not None else "       "
        print("   " + prefix + f"{name.ljust(26)} {engine.ljust(8)} {mode.ljust(8)} {etxt}")

    def footer(self, out_dir) -> None:
        if not self._hdr:
            return
        print("   " + sty(glyph("─", "-") * self._rule_w, "teal"))
        if self.best:
            lig, eng, ev = self.best
            print("   " + sty("Terbaik: ", "gray")
                  + sty(f"{lig} ({eng})  {ev:+.2f} kcal/mol", "green", "bold"))
        print("   " + sty(f"Output : {out_dir}", "gray"))


def dock(cfg: DockConfig, tools: ToolConfig) -> list[dict]:
    if cfg.center is None and cfg.native_ligand:
        cfg.center = compute_ligand_center(cfg.receptor, cfg.native_ligand, cfg.native_chain, cfg.native_resseq)
        _vlog(f"Box center from native ligand {cfg.native_ligand}: {cfg.center}")
    if cfg.center is None:
        raise RuntimeError("Box center is required. Use --center or --native-ligand.")

    validate_rules(cfg, tools)
    # Resolve to an absolute path: AutoGrid4/AutoDock4 run with cwd set to the
    # grid directory, so a relative --out would make their .gpf/.dpf paths
    # unresolvable. Absolute paths work regardless of cwd.
    out_dir = Path(cfg.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="ladock_agent_", dir=str(out_dir))
    results: list[dict] = []
    meta = {"started_at": _dt.datetime.now().isoformat(), "tmp_dir": tmp_dir, "outputs": []}

    try:
        receptor_source = cfg.receptor
        if cfg.receptor_components and not cfg.receptor.lower().endswith(".pdbqt"):
            receptor_source = extract_selected_components(
                cfg.receptor,
                os.path.join(tmp_dir, "selected_receptor.pdb"),
                cfg.receptor_components,
            )
            receptor_source = sanitize_pdb_text_for_mgltools(
                Path(receptor_source).read_text(encoding="utf-8", errors="replace")
            )
            selected_path = os.path.join(tmp_dir, "selected_receptor_sanitized.pdb")
            Path(selected_path).write_text(receptor_source, encoding="utf-8")
            receptor_source = selected_path
        elif not cfg.receptor.lower().endswith(".pdbqt"):
            # CLI path (no explicit component selection): drop crystallographic
            # waters so AutoGrid4/AD4 receptor prep doesn't choke on them.
            receptor_source = _strip_waters(
                cfg.receptor, os.path.join(tmp_dir, "receptor_nowater.pdb"))
        rec_pdbqt = prepare_receptor_pdbqt(receptor_source, os.path.join(tmp_dir, "receptor.pdbqt"), tools)
        rigid_pdbqt = rec_pdbqt
        flex_pdbqt = ""
        if "flexible" in cfg.modes:
            rigid_pdbqt, flex_pdbqt = split_flexible_receptor(rec_pdbqt, cfg.flex_residues, tmp_dir, tools)
            _vlog("Flexible residues: " + ", ".join(cfg.flex_residues))

        print("   " + sty(glyph("⋯", "..."), "gray")
              + sty(" Menyiapkan receptor & ligan (PDBQT)…", "gray"))
        # A shared cache lets a multi-receptor run convert the library once
        # instead of once per receptor (the conversion is receptor-independent).
        converted_dir = (Path(cfg.ligand_cache_dir).resolve() if cfg.ligand_cache_dir
                         else out_dir / "ligand_ready_pdbqt")
        converted_dir.mkdir(parents=True, exist_ok=True)
        ligands_to_process = list(cfg.ligands)
        if cfg.native_ligand:
            same_as_receptor = [
                os.path.abspath(path) == os.path.abspath(cfg.receptor)
                for path in ligands_to_process
            ]
            if same_as_receptor and any(same_as_receptor):
                native_pdb = extract_native_ligand(
                    cfg.receptor,
                    os.path.join(tmp_dir, "native_ligand.pdb"),
                    cfg.native_ligand,
                    cfg.native_chain,
                    cfg.native_resseq,
                )
                ligands_to_process = [
                    native_pdb if os.path.abspath(path) == os.path.abspath(cfg.receptor) else path
                    for path in ligands_to_process
                ]

        # --- Phase 1: convert every ligand to PDBQT (a file may expand to many) ---
        all_ligs: list[tuple[str, str]] = []  # (mol_name, lig_pdbqt)
        for idx, ligand in enumerate(ligands_to_process, start=1):
            _vlog(f"\nConverting ligand [{idx}/{len(ligands_to_process)}]: {ligand}")
            converted = expand_to_pdbqt(
                ligand,
                str(converted_dir / safe_name(Path(ligand).stem)),
                pythonsh=tools.pythonsh,
                prep_lig=tools.prepare_ligand,
                use_wsl_backend=tools.use_wsl_backend,
                wsl_distro=tools.wsl_distro,
                idx_offset=idx * 1000,
                log_fn=(log if _VERBOSE else _noop_log),
            )
            if not converted:
                _vlog(f"Skipping {ligand}: no PDBQT was produced.")
                continue
            all_ligs.extend(converted)

        if not all_ligs:
            raise RuntimeError("No ligands could be prepared to PDBQT.")

        vina_sfs = [s for s in cfg.scoring if s in VINA_SCORING]
        ad4_sfs = [s for s in cfg.scoring if s in AD4_SCORING]
        # MLSD = dock N different ligands together in the pocket. Only Vina/Vinardo
        # support multi-ligand simultaneous docking (mirrors desktop SF_SUPPORTS_MLSD).
        mlsd_active = cfg.simultaneous > 1 and bool(vina_sfs)
        modes_txt = ", ".join(cfg.modes)
        reporter: _DockReporter | None = None

        # --- Phase 2a: MLSD groups (simultaneous multi-ligand docking via Vina) ---
        if mlsd_active:
            if len(all_ligs) < cfg.simultaneous:
                raise RuntimeError(
                    f"MLSD needs at least {cfg.simultaneous} ligands, "
                    f"but only {len(all_ligs)} were prepared."
                )
            n_groups = mlsd_group_count(len(all_ligs), cfg.simultaneous, cfg.arrangement)
            # Every group is a full Vina run, so the group count is the real cost
            # of the job. Refuse an accidental combinatorial explosion up front
            # instead of discovering it hours in.
            if cfg.max_groups and n_groups > cfg.max_groups:
                raise RuntimeError(
                    f"MLSD would run {n_groups:,} groups "
                    f"({len(all_ligs)} ligands taken {cfg.simultaneous} at a time, "
                    f"{cfg.arrangement}) — over the --max-groups limit of "
                    f"{cfg.max_groups:,}. Use a smaller library, a smaller "
                    f"--simultaneous, 'combination' instead of 'permutation', or "
                    f"raise --max-groups (0 disables the limit).")
            _vlog(f"\nMLSD: {n_groups} group(s) of {cfg.simultaneous} ligands "
                  f"({cfg.arrangement}) via {', '.join(vina_sfs)}")
            reporter = _DockReporter(
                "MLSD — docking simultan",
                f"{n_groups} grup ({cfg.simultaneous} ligan/grup) · "
                f"{', '.join(vina_sfs)} · {modes_txt}",
                show_pct=n_groups > 1,
            )
            for gi, group in enumerate(
                    mlsd_groups(all_ligs, cfg.simultaneous, cfg.arrangement), start=1):
                names = [name for name, _p in group]
                paths = [p for _n, p in group]
                combo_name = "+".join(names)
                group_label = "MLSD_" + safe_name("+".join(names))
                gpct = int(gi * 100 / n_groups)
                for mode in cfg.modes:
                    use_flex = mode == "flexible"
                    receptor_for_vina = rigid_pdbqt if use_flex else rec_pdbqt
                    flex_for_run = flex_pdbqt if use_flex else ""
                    for sf in vina_sfs:
                        sf_dir = out_dir / mode / group_label / sf
                        sf_dir.mkdir(parents=True, exist_ok=True)
                        out = run_vina(cfg, tools, str(sf_dir), receptor_for_vina, paths, sf, flex_for_run)
                        row = {"ligand": combo_name, "mode": mode, "scoring": sf, "out_path": out,
                               **parse_result(out, sf)}
                        results.append(row)
                        meta["outputs"].append(row)
                        reporter.emit(combo_name, sf, mode, row.get("energy"), pct=gpct)
            if ad4_sfs:
                _vlog("Note: AD4/AD4-GPU do not support MLSD; running them per-ligand.")

        # --- Phase 2b: per-ligand docking ---
        # Vina/Vinardo run per-ligand only when MLSD is off; AD4/AD4-GPU always
        # run per-ligand (no MLSD support).
        per_ligand_vina = [] if mlsd_active else vina_sfs
        # ADFR is per-ligand too (no MLSD support), and must be able to carry the
        # phase on its own — with only --scoring adfr the other two lists are
        # empty, which used to skip the entire per-ligand phase silently.
        if per_ligand_vina or ad4_sfs or ("adfr" in cfg.scoring):
            n_lig = len(all_ligs)
            show_pct = n_lig > 1
            if reporter is None:                 # not created by the MLSD phase
                title = "Virtual Screening" if show_pct else "Redocking"
                reporter = _DockReporter(
                    title, f"{n_lig} ligan · {', '.join(cfg.scoring)} · {modes_txt}",
                    show_pct=show_pct,
                )
            # Progress is counted per finished engine run, not per ligand index:
            # with --jobs > 1 the ligands complete out of order.
            per_ligand_units = len(cfg.modes) * (
                len(per_ligand_vina) + ("ad4" in ad4_sfs) + ("ad4gpu" in ad4_sfs)
                + ("adfr" in cfg.scoring))
            total_units = n_lig * per_ligand_units

            # One AGFR target serves the whole library: it depends on the
            # receptor, the box and the flexible residues, never on the ligand.
            adfr_targets: dict[str, str] = {}
            adfr_lock = threading.Lock()

            def _adfr_target(mode: str) -> str:
                with adfr_lock:
                    if mode not in adfr_targets:
                        adfr_targets[mode] = build_adfr_target(
                            cfg, tools, str(out_dir / mode / "_adfr_target"),
                            # ADFR takes side-chain flexibility from the residue
                            # list itself, not from a pre-split rigid/flex pair.
                            rec_pdbqt,
                            cfg.flex_residues if mode == "flexible" else [])
                    return adfr_targets[mode]
            # Persistent by default: a second run against the same receptor and
            # box reuses the maps instead of rebuilding them.
            grid_root = (Path(cfg.grid_cache_dir).resolve() if cfg.grid_cache_dir
                         else cache_root() / "grids")
            if not cfg.grid_cache_enabled:
                grid_root = Path(tmp_dir) / "grids"     # discarded with tmp_dir
            grid_cache = _GridCache(grid_root, cfg, tools)

            # Output directories are keyed by a de-duplicated label so two
            # ligands sharing a name cannot overwrite each other's poses.
            labels = unique_labels(name for name, _p in all_ligs)

            def _dock_one(mol_name: str, lig_pdbqt: str, label: str) -> list[dict]:
                rows: list[dict] = []
                for mode in cfg.modes:
                    use_flex = mode == "flexible"
                    mode_dir = out_dir / mode / label
                    mode_dir.mkdir(parents=True, exist_ok=True)
                    receptor_for_run = rigid_pdbqt if use_flex else rec_pdbqt
                    flex_for_run = flex_pdbqt if use_flex else ""

                    for sf in per_ligand_vina:
                        sf_dir = mode_dir / sf
                        sf_dir.mkdir(exist_ok=True)
                        out = run_vina(cfg, tools, str(sf_dir), receptor_for_run, lig_pdbqt, sf, flex_for_run)
                        row = {"ligand": mol_name, "mode": mode, "scoring": sf, "out_path": out,
                               **parse_result(out, sf)}
                        rows.append(row)
                        reporter.emit(mol_name, sf, mode, row.get("energy"),
                                      pct=reporter.tick(total_units))

                    if ad4_sfs:
                        # Grid maps, autodock4, and autodock-gpu must share ONE
                        # directory: the .dpf/.fld reference the maps by relative
                        # name, so running the docker elsewhere can't find them.
                        # The maps themselves come from the cache — they depend on
                        # the ligand's atom types, not on the ligand.
                        grid_dir = mode_dir / "ad4"
                        fld, gpf = grid_cache.materialise(
                            mode, grid_dir, receptor_for_run, lig_pdbqt, flex_for_run)
                        if "ad4" in cfg.scoring:
                            out = run_ad4(cfg, tools, str(grid_dir), receptor_for_run, lig_pdbqt, gpf, flex_for_run)
                            row = {"ligand": mol_name, "mode": mode, "scoring": "ad4", "out_path": out,
                                   **parse_result(out, "ad4")}
                            rows.append(row)
                            reporter.emit(mol_name, "ad4", mode, row.get("energy"),
                                          pct=reporter.tick(total_units))
                        if "ad4gpu" in cfg.scoring:
                            out = run_adgpu(cfg, tools, str(grid_dir), lig_pdbqt, fld, flex_for_run)
                            row = {"ligand": mol_name, "mode": mode, "scoring": "ad4gpu", "out_path": out,
                                   **parse_result(out, "ad4gpu")}
                            rows.append(row)
                            reporter.emit(mol_name, "ad4gpu", mode, row.get("energy"),
                                          pct=reporter.tick(total_units))

                    if "adfr" in cfg.scoring:
                        out = run_adfr(cfg, tools, str(mode_dir / "adfr"),
                                       lig_pdbqt, _adfr_target(mode))
                        row = {"ligand": mol_name, "mode": mode, "scoring": "adfr", "out_path": out,
                               **parse_result(out, "adfr")}
                        rows.append(row)
                        reporter.emit(mol_name, "adfr", mode, row.get("energy"),
                                      pct=reporter.tick(total_units))
                return rows

            jobs = max(1, cfg.jobs)
            if jobs > 1:
                _vlog(f"Docking {n_lig} ligand(s) with {jobs} concurrent job(s)")
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                futures = [pool.submit(_dock_one, name, path, label)
                           for (name, path), label in zip(all_ligs, labels)]
                try:
                    # Collected in submission order, so results.csv does not
                    # depend on how many jobs happened to run in parallel.
                    per_ligand_rows = [f.result() for f in futures]
                except BaseException:
                    for pending in futures:
                        pending.cancel()
                    raise
            for rows in per_ligand_rows:
                results.extend(rows)
                meta["outputs"].extend(rows)
            meta["ad4_grid_cache"] = {
                "built": grid_cache.misses,
                "reused": grid_cache.hits,
                "reused_from_disk": grid_cache.disk_hits,
                "store": str(grid_cache.root) if cfg.grid_cache_enabled else "",
            }
            if grid_cache.hits or grid_cache.disk_hits:
                _vlog(f"AD4 grid cache: built {grid_cache.misses}, "
                      f"reused {grid_cache.hits} in-run, "
                      f"{grid_cache.disk_hits} from {grid_cache.root}")

        if reporter is not None:
            reporter.footer(out_dir)
        # The prepared receptor is worth keeping next to the results; the rest of
        # the scratch tree is not (it used to be left behind on every run).
        kept_dir = out_dir / "receptor_ready"
        kept_dir.mkdir(exist_ok=True)
        for src in (rec_pdbqt, rigid_pdbqt, flex_pdbqt):
            if src and os.path.isfile(src):
                shutil.copy2(src, kept_dir / os.path.basename(src))
        meta["receptor_ready"] = str(kept_dir)
        meta["tmp_dir"] = ""
        write_results(out_dir / "results.csv", results)
        meta["finished_at"] = _dt.datetime.now().isoformat()
        (out_dir / "run.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return results
    finally:
        if results:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            # A failed run keeps its scratch files: they are the only evidence of
            # what the receptor/ligand preparation actually produced.
            _vlog(f"Temporary files kept for inspection: {tmp_dir}")


_RESULT_HEADERS = ["ligand", "mode", "scoring", "energy", "rmsd_lb", "rmsd_ub", "out_path"]


def write_results(path: Path, rows: list[dict]) -> None:
    # "receptor" only exists on multi-receptor rows; keep it first when present
    # so the combined CSV reads receptor -> ligand -> score.
    headers = list(_RESULT_HEADERS)
    if any("receptor" in row for row in rows):
        headers.insert(0, "receptor")
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in headers})
    _vlog(f"\nResults CSV: {path}")


def _energy_of(row: dict) -> float:
    """Binding energy as a float; unparsable rows sort last."""
    try:
        return float(row.get("energy", ""))
    except (TypeError, ValueError):
        return float("inf")


def rank_rows(rows: list[dict], per_receptor: bool = False) -> list[dict]:
    """Best-first ranking. With ``per_receptor``, keep only each receptor's best."""
    ranked = sorted(rows, key=_energy_of)
    if not per_receptor:
        return ranked
    seen: set[str] = set()
    best: list[dict] = []
    for row in ranked:
        rec = str(row.get("receptor", ""))
        if rec in seen:
            continue
        seen.add(rec)
        best.append(row)
    return best


def _report_multi_receptor(out_dir: Path, rows: list[dict],
                           skipped: list[tuple[str, str]]) -> None:
    """Aggregate a multi-receptor run into one CSV, one ranking and one summary.

    Every receptor already writes its own ``results.csv`` in its own
    sub-directory; without this the user is left diffing N files by hand to find
    which target the library actually hit.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    docked = sorted({str(row.get("receptor", "")) for row in rows})
    if rows:
        write_results(out_dir / "results_all.csv", rows)
        write_results(out_dir / "ranking.csv", rank_rows(rows))
    (out_dir / "multi_receptor.summary.json").write_text(
        json.dumps({
            "n_results": len(rows),
            "receptors_docked": docked,
            "receptors_skipped": [{"receptor": n, "error": e} for n, e in skipped],
        }, indent=2),
        encoding="utf-8")

    if not rows:
        print(sty("\n  Tidak ada hasil docking dari receptor mana pun.", "yellow"))
        if skipped:
            for name, exc in skipped:
                print(sty(f"    {name}: {exc}", "gray"))
        return

    ui_header("Ringkasan multi-receptor",
              f"{len(rows)} hasil dari {len(docked)} receptor")
    best_each = rank_rows(rows, per_receptor=True)
    head_rec, head_lig, head_dg = "Receptor", "Ligan terbaik", glyph("ΔG", "dG")
    # Columns must fit the headings too, not just the values.
    rec_w = min(30, max([len(head_rec)] + [len(str(r.get("receptor", ""))) for r in best_each]))
    lig_w = min(24, max([len(head_lig)] + [len(str(r.get("ligand", ""))) for r in best_each]))
    rule = glyph("─", "-") * (rec_w + lig_w + len(head_dg) + 8)
    print("   " + sty(f"{head_rec.ljust(rec_w)} {head_lig.ljust(lig_w)} {head_dg}", "gray"))
    print("   " + sty(rule, "teal"))
    for row in best_each:
        energy = _energy_of(row)
        etxt = sty(f"{energy:+.2f}", "green") if energy != float("inf") else sty("n/a", "yellow")
        print("   " + str(row.get("receptor", ""))[:rec_w].ljust(rec_w) + " "
              + str(row.get("ligand", ""))[:lig_w].ljust(lig_w) + " " + etxt)
    print("   " + sty(rule, "teal"))

    top = rank_rows(rows)[0]
    if _energy_of(top) != float("inf"):
        print("   " + sty("Terbaik keseluruhan: ", "gray")
              + sty(f"{top.get('ligand')} @ {top.get('receptor')} "
                    f"({top.get('scoring')})  {_energy_of(top):+.2f} kcal/mol",
                    "green", "bold"))
    if skipped:
        print("   " + sty(f"Dilewati: {len(skipped)} receptor", "yellow"))
        for name, exc in skipped:
            print("     " + sty(f"{name}: {exc}", "gray"))
    ui_note(f"results_all.csv & ranking.csv: {out_dir}")


def _print_question(question: str) -> None:
    head, _, rest = question.partition("\n")
    ui_header(head, rest)


def _print_options(options: list[tuple[str, object]]) -> None:
    for i, (label, _value) in enumerate(options, start=1):
        print(f"  {sty(str(i).rjust(2), 'cyan', 'bold')}  {label}")


def _prompt_choice(question: str, options: list[tuple[str, object]]) -> object:
    while True:
        _print_question(question)
        _print_options(options)
        raw = input(prompt_glyph()).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][1]
        print(sty("  ! Pilihan tidak valid. Masukkan nomor pada daftar.", "yellow"))


def _prompt_multi_choice(question: str, options: list[tuple[str, object]],
                         default_all: bool = False) -> list[object]:
    while True:
        _print_question(question)
        _print_options(options)
        if default_all:
            print("  " + sty("Cara memilih:", "gray"))
            print("   " + sty("• satu", "white") + sty("      ketik 1 nomor      contoh: ", "gray")
                  + sty("2", "cyan", "bold"))
            print("   " + sty("• sebagian", "white") + sty("  pisah dengan koma    contoh: ", "gray")
                  + sty("1,3", "cyan", "bold"))
            print("   " + sty("• semua", "white") + sty("     tekan ", "gray")
                  + sty("ENTER", "cyan", "bold") + sty(" (kosongkan)", "gray"))
        else:
            print("   " + sty("(pilih beberapa, pisah koma — contoh: ", "gray")
                  + sty("1,2", "cyan", "bold") + sty(")", "gray"))
        raw = input(prompt_glyph()).strip()
        if not raw and default_all:
            return [value for _label, value in options]
        try:
            idxs = [int(part.strip()) for part in raw.split(",") if part.strip()]
        except ValueError:
            print(sty("  ! Pilihan tidak valid. Gunakan nomor dipisahkan koma.", "yellow"))
            continue
        if idxs and all(1 <= idx <= len(options) for idx in idxs):
            seen = []
            for idx in idxs:
                value = options[idx - 1][1]
                if value not in seen:
                    seen.append(value)
            return seen
        print(sty("  ! Pilihan tidak valid. Gunakan nomor pada daftar.", "yellow"))


def _subdir_options(root: Path) -> list[tuple[str, Path]]:
    dirs = [p for p in sorted(root.iterdir(), key=lambda x: x.name.lower()) if p.is_dir()]
    return [(f"{p.name}/", p) for p in dirs]


def _file_options(root: Path, suffixes: set[str]) -> list[tuple[str, Path]]:
    files = [
        p for p in sorted(root.iterdir(), key=lambda x: x.name.lower())
        if p.is_file() and p.suffix.lower() in suffixes
    ]
    return [(p.name, p) for p in files]


def _dirs_with_files(root: Path, suffixes: set[str]) -> list[tuple[str, Path]]:
    """Candidate directories that directly contain a file with one of the given
    suffixes: the root itself (listed first) plus any immediate subdirectory.
    Lets the wizard work in a flat workspace where files sit directly in the
    job directory, not only in a repo-style subdirectory layout."""
    options: list[tuple[str, Path]] = []
    if _file_options(root, suffixes):
        options.append((". (direktori ini)", root))
    for label, sub in _subdir_options(root):
        if _file_options(sub, suffixes):
            options.append((label, sub))
    return options


def _count_library_molecules(paths: list[str]) -> int:
    """Best-effort count of how many molecules the selected ligand file(s) hold.
    One file can contain many molecules (SDF/SMILES/CSV/Excel), so counting file
    paths is wrong — MLSD gating and group sizing need the molecule count."""
    total = 0
    for p in paths:
        ext = Path(p).suffix.lower()
        try:
            if ext in (".smi", ".smiles", ".txt"):
                for line in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
                    s = line.strip()
                    if s and not s.startswith("#"):
                        total += 1
            elif ext == ".sdf":
                text = Path(p).read_text(encoding="utf-8", errors="replace")
                total += text.count("$$$$") or (1 if text.strip() else 0)
            elif ext == ".mol2":
                text = Path(p).read_text(encoding="utf-8", errors="replace")
                total += text.count("@<TRIPOS>MOLECULE") or 1
            elif ext in (".csv", ".tsv"):
                rows = [ln for ln in Path(p).read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                sep = "," if ext == ".csv" else "\t"
                # Subtract a header row only when there is one — same rule as
                # _iter_smiles_rows, so this count matches what actually docks.
                has_header = bool(rows) and _header_indices(rows[0].split(sep)) is not None
                total += max(0, len(rows) - (1 if has_header else 0))
            elif ext in (".xlsx", ".xls"):
                try:
                    import pandas as pd
                    total += int(pd.read_excel(p).dropna(how="all").shape[0])
                except Exception:
                    total += 2                            # assume multi -> offer MLSD
            else:
                total += 1                                # single-molecule structure
        except OSError:
            total += 1
    return total


def _protein_center(pdb_path: str) -> tuple[float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line[:6].strip() != "ATOM":
                continue
            try:
                xs.append(float(line[30:38]))
                ys.append(float(line[38:46]))
                zs.append(float(line[46:54]))
            except ValueError:
                pass
    if not xs:
        raise RuntimeError(f"Tidak dapat menghitung pusat protein dari {pdb_path}")
    return round(sum(xs) / len(xs), 3), round(sum(ys) / len(ys), 3), round(sum(zs) / len(zs), 3)


def _manual_center() -> tuple[float, float, float]:
    while True:
        raw = input("masukkan center_x center_y center_z > ").strip()
        try:
            x, y, z = [float(part) for part in raw.replace(",", " ").split()]
            return x, y, z
        except ValueError:
            print("Format belum benar. Contoh: 10.5 22.0 -4.5")


def _manual_size() -> tuple[float, float, float]:
    while True:
        raw = input("masukkan size_x size_y size_z > ").strip()
        try:
            x, y, z = [float(part) for part in raw.replace(",", " ").split()]
            return x, y, z
        except ValueError:
            print("Format belum benar. Contoh: 20 20 20")


def _tool_args_namespace(use_wsl: bool = False):
    return argparse.Namespace(
        use_wsl=use_wsl,
        wsl_distro="",
        vina="",
        autogrid4="",
        autodock4="",
        autodock_gpu="",
        mgltools="",
        adfrsuite="",
        pythonsh="",
        timeout=7200,
    )


_WIZARD_RESTART = object()      # sentinel: "run the dialog again from the top"


def run_wizard() -> int:
    """Run the guided dialog, looping when the user asks for another docking run.

    A loop, not recursion: "Jalankan docking baru" used to call ``run_wizard()``
    from inside itself, so every additional run grew the call stack and pinned
    the previous run's entire state in memory.
    """
    while True:
        outcome = _wizard_session()
        if outcome is not _WIZARD_RESTART:
            return int(outcome)          # type: ignore[arg-type]


def _wizard_session():
    init_terminal()
    clear_screen()
    print_banner()
    print("  " + sty("Dialog docking terpandu", "white")
          + sty("  (jawab dengan nomor pilihan)", "gray"))

    state = {
        "purpose": None,
        "job_dir": Path.cwd(),
        "target_dir": None,
        "target_path": None,
        "target_paths": [],          # multi-receptor: list of selected receptors
        "target_specs": [],          # per-receptor {path, resname, chain, resseq}
        "multi_receptor": False,
        "multi_center_mode": "native",  # per-receptor centering: native | protein
        "components": [],
        "receptor_components": [],
        "ligand_components": [],
        "native_ligand": "",
        "native_chain": "",
        "native_resseq": "",
        "ligand_paths": [],
        "center": None,
        "scoring": ["vina"],
        "backend": False,
        "mode": ["rigid"],
        "flex_residues": [],
        "flex_distance": 3.0,
        "simultaneous": 1,
        "arrangement": "combination",
        "max_groups": 5000,
        "jobs": 1,
        "size": (20.0, 20.0, 20.0),
        "preset": {"label": "Seimbang", "ex": 8, "ad4ex": 8, "poses": 9, "cpu": 4},
        "out_dir": None,
    }

    def select_purpose():
        state["purpose"] = _prompt_choice(
            "Apa tujuan docking?",
            [("Redocking", "redocking"), ("Virtual Screening", "virtual_screening")],
        )

    def select_job_dir():
        cwd = Path.cwd()
        mode = _prompt_choice(
            f"Gunakan job directory ini?\n{cwd}",
            [
                ("Ya, gunakan direktori kerja saat ini", "default"),
                ("Pilih dari subdirektori di sini", "subdir"),
                ("Input path baru", "manual"),
            ],
        )
        if mode == "default":
            state["job_dir"] = cwd
            return
        if mode == "manual":
            while True:
                print("\n" + sty(glyph("◆", "*"), "cyan", "bold") + " "
                      + sty("Masukkan path job directory:", "bold"))
                raw = input(prompt_glyph()).strip().strip('"').strip("'")
                if not raw:
                    continue
                p = Path(os.path.expanduser(raw)).resolve()
                if p.is_dir():
                    state["job_dir"] = p
                    return
                if p.exists():
                    print(sty(f"  ! Bukan direktori: {p}", "yellow"))
                    continue
                make = _prompt_choice(
                    f"Direktori belum ada: {p}\nBuat direktori ini?",
                    [("Ya, buat direktori", True), ("Tidak, masukkan path lain", False)],
                )
                if make:
                    p.mkdir(parents=True, exist_ok=True)
                    state["job_dir"] = p
                    return
            return
        subdirs = _subdir_options(cwd)
        if not subdirs:
            raise RuntimeError(f"Tidak ada subdirektori yang bisa dipilih di {cwd}.")
        state["job_dir"] = _prompt_choice("Pilih job directory:", subdirs)

    def report_layout():
        """Detect and show what each sub-directory of the job dir contains, so
        the user can tell which one is the receptor library and which is the
        ligand library."""
        job = state["job_dir"]
        rows = [("." + " (direktori ini)", job)] + _subdir_options(job)
        ui_header("Deteksi isi job directory", str(job))
        for label, d in rows:
            n_struct = len(_file_options(d, {".pdb", ".pdbqt"}))
            n_lig = len(_file_options(d, LIGAND_SUFFIXES))
            parts = []
            if n_struct:
                parts.append(sty(f"{n_struct} struktur (.pdb/.pdbqt)", "teal"))
            if n_lig:
                parts.append(sty(f"{n_lig} kandidat ligand", "teal"))
            if not parts:                       # skip empty / output dirs (e.g. results/)
                continue
            print(f"  {sty(label.ljust(20), 'white')} : " + sty(", ", "gray").join(parts))
        if state["purpose"] == "redocking":
            ui_note("Redocking: ligand diambil dari native ligand pada file target "
                    "(tidak menanyakan library ligand).")
        else:
            ui_note("Virtual Screening: perlu library receptor DAN library ligand.")

    def select_target():
        job = state["job_dir"]
        # Receptor library = a directory that holds at least one target file.
        dir_options = _dirs_with_files(job, {".pdb", ".pdbqt"})
        if not dir_options:
            raise RuntimeError(
                f"Library receptor tidak ditemukan: tak ada file .pdb/.pdbqt di "
                f"{job} maupun subdirektorinya."
            )
        state["target_dir"] = (
            dir_options[0][1] if len(dir_options) == 1
            else _prompt_choice(
                "Pilih sub-direktori library receptor (berisi file target .pdb/.pdbqt):",
                dir_options,
            )
        )
        target_files = _file_options(state["target_dir"], {".pdb", ".pdbqt"})
        if not target_files:
            raise RuntimeError(f"Tidak ada file target .pdb/.pdbqt di {state['target_dir']}")
        ui_ok(f"Library receptor: {state['target_dir']} "
              f"({len(target_files)} file target)")
        # Multi-receptor: VS = dock library against each receptor (reverse
        # screening); Redocking = redock each receptor's own native ligand
        # (batch validation across complexes).
        if len(target_files) > 1:
            chosen = _prompt_multi_choice(
                "Pilih target (satu / sebagian / semua)", target_files, default_all=True)
            state["target_paths"] = list(chosen)
        else:
            state["target_paths"] = [_prompt_choice("Pilih file target:", target_files)]
        state["multi_receptor"] = len(state["target_paths"]) > 1
        state["target_path"] = state["target_paths"][0]
        if state["multi_receptor"]:
            ui_ok(f"{len(state['target_paths'])} receptor dipilih (multi-receptor)")
        state["components"] = parse_pdb_components(str(state["target_path"]))
        state["ligand_components"] = [c for c in state["components"] if c.get("type") == "Ligand"]

    def select_receptor_components():
        receptor_candidates = [
            c for c in state["components"]
            if c.get("type") in {"Protein", "Metal Ion", "Other"}
        ]
        if not receptor_candidates:
            raise RuntimeError("Tidak ada komponen receptor/protein yang ditemukan pada target.")
        options = [
            (
                f"{c['type']} | chain={c['chain']} | {c['resname']} | "
                f"res={c['n_residues']} atoms={c['n_atoms']}",
                c,
            )
            for c in receptor_candidates
        ]
        state["receptor_components"] = _prompt_multi_choice(
            "Komponen mana yang digunakan sebagai receptor?", options
        )

    def handle_missing_native_ligand():
        action = _prompt_choice(
            "Redocking membutuhkan native ligand di file target, tetapi tidak ditemukan. Apa yang ingin dilakukan?",
            [
                ("Pilih target lain", "target"),
                ("Ubah tujuan menjadi Virtual Screening", "vs"),
                ("Keluar", "exit"),
            ],
        )
        if action == "target":
            select_target()
            select_receptor_components()
            select_native_ligand()
        elif action == "vs":
            state["purpose"] = "virtual_screening"
            select_ligand_library()
            select_center()
        else:
            raise KeyboardInterrupt

    def select_native_ligand():
        ligands = state["ligand_components"]
        if not ligands:
            handle_missing_native_ligand()
            return
        native = _prompt_choice(
            "Pilih native ligand:",
            [
                (f"{c['resname']} chain={c['chain']} resseq={c.get('resseq', '')} atoms={c['n_atoms']}", c)
                for c in ligands
            ],
        )
        state["native_ligand"] = native["resname"]
        state["native_chain"] = native["chain"]
        state["native_resseq"] = native.get("resseq", "")
        state["center"] = compute_ligand_center(
            str(state["target_path"]),
            state["native_ligand"],
            state["native_chain"],
            state["native_resseq"],
        )
        state["ligand_paths"] = [str(state["target_path"])]

    def select_ligand_library():
        # Only Virtual Screening asks for a ligand library; redocking uses the
        # native ligand embedded in the target file instead.
        job = state["job_dir"]
        # A ligand library must not be the same directory that only holds the
        # receptor target; still, a flat workspace may legitimately mix both.
        dir_options = _dirs_with_files(job, LIGAND_SUFFIXES)
        if not dir_options:
            raise RuntimeError(
                f"Library ligand tidak ditemukan: tak ada file ligand yang didukung di "
                f"{job} maupun subdirektorinya."
            )
        lig_subdir = (
            dir_options[0][1] if len(dir_options) == 1
            else _prompt_choice(
                "Pilih sub-direktori library ligand (SDF/MOL2/SMILES/CSV/PDBQT/...):",
                dir_options,
            )
        )
        ligand_file_options = _file_options(lig_subdir, LIGAND_SUFFIXES)
        if not ligand_file_options:
            raise RuntimeError(f"Tidak ada file ligand yang didukung di {lig_subdir}")
        ui_ok(f"Library ligand: {lig_subdir} "
              f"({len(ligand_file_options)} file ligand)")
        ligand_choice = _prompt_choice(
            "Pilih ligand:",
            [("Semua file ligand di folder ini", "__all__")] + ligand_file_options,
        )
        state["ligand_paths"] = (
            [str(path) for _label, path in ligand_file_options]
            if ligand_choice == "__all__"
            else [str(ligand_choice)]
        )
        if not state["ligand_paths"]:
            raise RuntimeError("Library ligand kosong: minimal 1 file ligand diperlukan untuk Virtual Screening.")

    def select_center():
        options = [("Dari pusat protein", "protein"), ("Manual coordinate", "manual")]
        if state["ligand_components"]:
            options.insert(0, ("Dari native/reference ligand di target", "native"))
        center_mode = _prompt_choice("Bagaimana menentukan pusat box?", options)
        state["native_ligand"] = ""
        state["native_chain"] = ""
        state["native_resseq"] = ""
        if center_mode == "native":
            native = _prompt_choice(
                "Pilih ligand referensi:",
                [
                    (f"{c['resname']} chain={c['chain']} resseq={c.get('resseq', '')}", c)
                    for c in state["ligand_components"]
                ],
            )
            state["native_ligand"] = native["resname"]
            state["native_chain"] = native["chain"]
            state["native_resseq"] = native.get("resseq", "")
            state["center"] = compute_ligand_center(
                str(state["target_path"]),
                state["native_ligand"],
                state["native_chain"],
                state["native_resseq"],
            )
        elif center_mode == "protein":
            state["center"] = _protein_center(str(state["target_path"]))
        else:
            state["center"] = _manual_center()

    def select_multi_center():
        # Each receptor has its own pocket, so the box centre is computed
        # per-receptor. "native" lets you pick the chain + native ligand of each
        # receptor; "protein" uses each receptor's protein centroid (blind).
        state["multi_center_mode"] = _prompt_choice(
            "Pusat box tiap receptor ditentukan dari?",
            [
                ("Native/reference ligand tiap receptor (pilih chain + ligand)", "native"),
                ("Pusat protein tiap receptor (blind docking)", "protein"),
            ],
        )

    def select_target_specs():
        """For each selected receptor, choose its chain + native ligand (used as
        the redocking ligand and/or the box centre)."""
        state["target_specs"] = []
        for rec in state["target_paths"]:
            name = Path(rec).name
            ligs = [c for c in parse_pdb_components(str(rec)) if c.get("type") == "Ligand"]
            if not ligs:
                if state["purpose"] == "redocking":
                    print(sty(f"  ! {name}: tak ada native ligand — receptor ini akan dilewati.",
                              "yellow"))
                state["target_specs"].append(
                    {"path": rec, "resname": "", "chain": "", "resseq": ""})
                continue
            # Step 1 — pilih CHAIN (independen dari ligand).
            chains = sorted({c["chain"] for c in ligs})
            chain = (chains[0] if len(chains) == 1
                     else _prompt_choice(f"[{name}] Pilih chain:", [(ch, ch) for ch in chains]))
            # Step 2 — pilih NATIVE LIGAND di chain itu (SELALU eksplisit; satu
            # chain bisa memuat >1 ligand, jadi user harus memilih yang mana).
            in_chain = [c for c in ligs if c["chain"] == chain]
            lig = _prompt_choice(
                f"[{name}] Pilih native ligand (chain {chain}):",
                [(f"{c['resname']} resseq={c.get('resseq', '')} atoms={c['n_atoms']}", c)
                 for c in in_chain])
            state["target_specs"].append(
                {"path": rec, "resname": lig["resname"], "chain": lig["chain"],
                 "resseq": lig.get("resseq", "")})
            ui_ok(f"{name}: {lig['resname']} resseq={lig.get('resseq', '')} (chain {lig['chain']})")

    def select_scoring():
        state["scoring"] = _prompt_multi_choice(
            "Apa score function yang ingin digunakan?",
            [("Vina", "vina"), ("Vinardo", "vinardo"), ("AD4", "ad4"),
             ("AD4-GPU", "ad4gpu"), ("ADFR (butuh ADFRsuite)", "adfr")],
        )

    def select_backend():
        if any(sf in AD4_SCORING for sf in state["scoring"]):
            state["backend"] = _prompt_choice(
                "AD4/AD4-GPU membutuhkan toolchain Linux/MGLTools. Backend eksekusi?",
                [("WSL backend untuk engine Linux-only", True), ("Native OS saat ini", False)],
            )
        else:
            state["backend"] = _prompt_choice(
                "Backend eksekusi yang digunakan?",
                [("Native OS saat ini", False), ("WSL backend untuk engine Linux-only", True)],
            )

    def validate_tools_early():
        """Report engine availability for the chosen backend + scoring, and offer
        repairs, before the rest of the protocol is configured."""
        def _exists(p: str) -> bool:
            return bool(p) and (os.path.isfile(p) or bool(shutil.which(p)))

        while True:
            try:
                tools = resolve_tools(_tool_args_namespace(use_wsl=bool(state["backend"])))
            except RuntimeError as exc:
                print(f"ladock > {exc}")
                act = _prompt_choice(
                    "Backend tidak bisa dipakai. Tindakan?",
                    [("Gunakan Native OS", "native"), ("Batalkan", "cancel")],
                )
                if act == "native":
                    state["backend"] = False
                    continue
                raise KeyboardInterrupt

            mgl_ok = os.path.isfile(tools.pythonsh) and os.path.isfile(tools.prepare_gpf)
            vina_ok = _exists(tools.vina_path)
            rows: list[tuple[str, bool, str]] = []
            for sf in state["scoring"]:
                if sf in VINA_SCORING:
                    rows.append((sf, vina_ok, "Vina executable tak ditemukan"))
                elif sf == "ad4":
                    ok = _exists(tools.ad4_path) and _exists(tools.ag4_path) and mgl_ok
                    rows.append((sf, ok, "butuh AutoDock4 + AutoGrid4 + MGLTools"))
                elif sf == "ad4gpu":
                    ok = _exists(tools.autodockgpu) and mgl_ok
                    rows.append((sf, ok, "butuh AutoDock-GPU + MGLTools (+ CUDA runtime)"))

            prep = ("Meeko (native)" if meeko_available()
                    else ("MGLTools" if os.path.isfile(tools.prepare_receptor) else "TIDAK ADA"))

            print(f"\nladock > Ketersediaan engine — backend: "
                  f"{'WSL' if state['backend'] else 'Native OS'}")
            for sf, ok, reason in rows:
                print(f"   [{'v' if ok else 'x'}] {sf}" + ("" if ok else f"  ({reason})"))
            print(f"   prep receptor/ligand : {prep}")

            unavailable = [sf for sf, ok, _ in rows if not ok]
            prep_bad = prep == "TIDAK ADA"
            if not unavailable and not prep_bad:
                print("   Semua engine terpilih tersedia.")
                return

            opts = []
            has_ad = any(sf in AD4_SCORING for sf in state["scoring"])
            if not state["backend"] and (has_ad or prep_bad):
                opts.append(("Aktifkan WSL backend (engine Linux / MGLTools)", "wsl"))
            if unavailable:
                opts.append((f"Hapus engine tak tersedia: {', '.join(unavailable)}", "drop"))
            opts.append(("Lanjutkan (validasi ulang saat Jalankan)", "continue"))
            opts.append(("Batalkan", "cancel"))
            act = _prompt_choice("Ada engine/tool tak tersedia. Tindakan?", opts)
            if act == "wsl":
                state["backend"] = True
                continue
            if act == "drop":
                state["scoring"] = [s for s in state["scoring"] if s not in unavailable] or ["vina"]
                continue
            if act == "continue":
                return
            raise KeyboardInterrupt

    def select_mode():
        mode_choice = _prompt_choice(
            "Mode docking?",
            [("Rigid", ["rigid"]), ("Flexible receptor", ["flexible"]), ("Rigid dan Flexible", ["rigid", "flexible"])],
        )
        state["mode"] = mode_choice

    def select_box():
        size = _prompt_choice(
            "Ukuran box docking?",
            [
                ("20 x 20 x 20 Angstrom", (20.0, 20.0, 20.0)),
                ("22.5 x 22.5 x 22.5 Angstrom", (22.5, 22.5, 22.5)),
                ("25 x 25 x 25 Angstrom", (25.0, 25.0, 25.0)),
                ("30 x 30 x 30 Angstrom", (30.0, 30.0, 30.0)),
                ("Masukkan manual", "manual"),
            ],
        )
        state["size"] = _manual_size() if size == "manual" else size

    def select_flex_residues():
        state["flex_residues"] = []
        if "flexible" not in state["mode"]:
            return
        if state["multi_receptor"]:
            # Residues differ per receptor -> only pick the distance; each
            # receptor auto-detects its own flexible residues at run time.
            state["flex_distance"] = float(_prompt_choice(
                "Radius auto-detect flexible residues (tiap receptor)?",
                [("3 Angstrom dari center", 3.0), ("5 Angstrom dari center", 5.0),
                 ("7 Angstrom dari center", 7.0)],
            ))
            return
        while True:
            state["flex_distance"] = float(_prompt_choice(
                "Bagaimana menentukan flexible residues?",
                [
                    ("Auto-detect residu protein dalam 3 Angstrom dari center", 3.0),
                    ("Auto-detect residu protein dalam 5 Angstrom dari center", 5.0),
                    ("Auto-detect residu protein dalam 7 Angstrom dari center", 7.0),
                ],
            ))
            residues = find_flex_residues(
                str(state["target_path"]),
                state["center"][0],
                state["center"][1],
                state["center"][2],
                state["flex_distance"],
            )
            if residues:
                state["flex_residues"] = residues
                print(f"ladock > Flexible residues terdeteksi: {', '.join(residues)}")
                return
            action = _prompt_choice(
                f"Tidak ditemukan residu fleksibel dalam radius {state['flex_distance']} A. Pilih tindakan:",
                [
                    ("Coba radius lain", "retry"),
                    ("Gunakan mode rigid saja", "rigid"),
                    ("Batalkan", "cancel"),
                ],
            )
            if action == "retry":
                continue
            if action == "rigid":
                state["mode"] = ["rigid"]
                return
            raise KeyboardInterrupt

    def select_mlsd():
        state["simultaneous"] = 1
        state["arrangement"] = "combination"
        # MLSD only makes sense with a multi-ligand library docked by Vina/Vinardo.
        if state["purpose"] != "virtual_screening":
            return
        if not any(sf in VINA_SCORING for sf in state["scoring"]):
            return
        # Count molecules, not files: one .smi/.sdf/.csv can hold many ligands.
        n_lig = _count_library_molecules(state["ligand_paths"])
        if n_lig < 2:
            return
        use_mlsd = _prompt_choice(
            "Multiple Ligand Simultaneous Docking (MLSD)? "
            "Dock beberapa ligan berbeda sekaligus dalam satu pocket (khusus Vina/Vinardo).",
            [("Tidak, dock satu per satu", False), ("Ya, aktifkan MLSD", True)],
        )
        if not use_mlsd:
            return
        max_n = min(n_lig, 5)
        state["simultaneous"] = _prompt_choice(
            f"Berapa ligan didokking bersama? (tersedia {n_lig} ligan)",
            [(f"{k} ligan sekaligus", k) for k in range(2, max_n + 1)],
        )
        while True:
            state["arrangement"] = _prompt_choice(
                "Susunan kombinasi ligan?",
                [
                    ("Combination: set tak berurut (disarankan)", "combination"),
                    ("Permutation: berurut, jauh lebih banyak grup", "permutation"),
                ],
            )
            n_groups = mlsd_group_count(n_lig, state["simultaneous"], state["arrangement"])
            print(f"ladock > MLSD: {n_groups:,} grup x {state['simultaneous']} ligan "
                  f"({state['arrangement']}).")
            if not state["max_groups"] or n_groups <= state["max_groups"]:
                return
            # Each group is one full Vina run; make the cost visible before the
            # run starts rather than after hours of docking.
            action = _prompt_choice(
                f"{n_groups:,} grup itu sangat banyak (batas aman "
                f"{state['max_groups']:,}) — tiap grup = satu run Vina penuh. "
                f"Tindakan?",
                [
                    ("Kurangi jumlah ligan per grup", "fewer"),
                    ("Ganti susunan (combination/permutation)", "arrangement"),
                    ("Lanjutkan saja, saya paham konsekuensinya", "continue"),
                    ("Batalkan MLSD, dock satu per satu", "off"),
                ],
            )
            if action == "continue":
                state["max_groups"] = 0          # user opted out of the limit
                return
            if action == "off":
                state["simultaneous"] = 1
                state["arrangement"] = "combination"
                return
            if action == "fewer":
                state["simultaneous"] = _prompt_choice(
                    f"Berapa ligan didokking bersama? (tersedia {n_lig} ligan)",
                    [(f"{k} ligan sekaligus", k) for k in range(2, max_n + 1)],
                )

    def select_preset():
        state["preset"] = _prompt_choice(
            "Preset parameter pencarian?",
            [
                ("Seimbang/profesional: exhaustiveness 8, poses 9", {"label": "Seimbang", "ex": 8, "ad4ex": 8, "poses": 9, "cpu": 4}),
                ("Cepat: exhaustiveness 4, poses 5", {"label": "Cepat", "ex": 4, "ad4ex": 4, "poses": 5, "cpu": 4}),
                ("Teliti: exhaustiveness 16, poses 20", {"label": "Teliti", "ex": 16, "ad4ex": 16, "poses": 20, "cpu": 4}),
            ],
        )

    def select_jobs():
        # Only worth asking when there is a library to spread across cores.
        state["jobs"] = 1
        if state["purpose"] != "virtual_screening":
            return
        if _count_library_molecules(state["ligand_paths"]) < 2:
            return
        cores = os.cpu_count() or 4
        options = [("1 (satu per satu)", 1)]
        for k in (2, 4, 8):
            if k <= cores:
                options.append((f"{k} ligan paralel", k))
        if len(options) == 1:
            return
        state["jobs"] = _prompt_choice(
            f"Berapa ligan didokking paralel? ({cores} core terdeteksi; "
            f"tiap job memakai {state['preset']['cpu']} thread Vina)",
            options,
        )

    def select_output():
        run_tag = _dt.datetime.now().strftime("agent_%Y%m%d_%H%M%S")
        state["out_dir"] = state["job_dir"] / "results" / run_tag

    def print_protocol():
        receptor_labels = [
            f"{c.get('type')}:{c.get('chain')}:{c.get('resname')}"
            for c in state["receptor_components"]
        ]
        rows: list[tuple[str, str]] = [("Tujuan", str(state["purpose"]))]
        if state["multi_receptor"]:
            names = ", ".join(Path(p).name for p in state["target_paths"])
            rows.append(("Target", f"{len(state['target_paths'])} receptor: {names}"))
            rows.append(("Receptor", "protein (+metal); ligand & air dibuang"))
        else:
            rows.append(("Target", str(state["target_path"])))
            rows.append(("Receptor", ", ".join(receptor_labels)))
        if state["multi_receptor"] and state["purpose"] == "redocking":
            rows.append(("Ligand", "native ligand tiap receptor"))
        else:
            rows.append(("Ligand files", f"{len(state['ligand_paths'])} file"))
        if state["native_ligand"]:
            rows.append(("Reference lig",
                         f"{state['native_ligand']} chain {state['native_chain']} "
                         f"resseq {state['native_resseq']}"))
        rows.append(("Scoring", ", ".join(state["scoring"])))
        rows.append(("Backend WSL", "Ya" if state["backend"] else "Tidak"))
        rows.append(("Mode", ", ".join(state["mode"])))
        if state["flex_residues"]:
            rows.append(("Flex residues", ", ".join(state["flex_residues"])))
        if state["simultaneous"] > 1:
            rows.append(("MLSD", f"{state['simultaneous']} ligan/grup, {state['arrangement']}"))
        if state["multi_receptor"]:
            mode_lbl = {"native": "native ligand tiap receptor",
                        "protein": "pusat protein tiap receptor"}[state["multi_center_mode"]]
            rows.append(("Center", mode_lbl))
        else:
            rows.append(("Center", str(state["center"])))
        rows.append(("Box", str(state["size"])))
        rows.append(("Preset", str(state["preset"]["label"])))
        if state["jobs"] > 1:
            rows.append(("Paralel", f"{state['jobs']} ligan sekaligus"))
        rows.append(("Output", str(state["out_dir"])))
        ui_panel("Ringkasan Protocol Docking", rows)

    def build_config() -> DockConfig:
        return DockConfig(
            receptor=str(state["target_path"]),
            ligands=state["ligand_paths"],
            out_dir=str(state["out_dir"]),
            receptor_components=state["receptor_components"],
            scoring=state["scoring"],
            modes=state["mode"],
            center=state["center"],
            size=state["size"],
            exhaustiveness=state["preset"]["ex"],
            ad4_exhaustiveness=state["preset"]["ad4ex"],
            n_poses=state["preset"]["poses"],
            cpu=state["preset"]["cpu"],
            flex_residues=state["flex_residues"],
            flex_distance=state["flex_distance"],
            simultaneous=state["simultaneous"],
            arrangement=state["arrangement"],
            max_groups=state["max_groups"],
            jobs=state["jobs"],
            native_ligand=state["native_ligand"] if state["purpose"] == "redocking" else "",
            native_chain=state["native_chain"] if state["purpose"] == "redocking" else "",
            native_resseq=state["native_resseq"] if state["purpose"] == "redocking" else "",
        )

    def _spec_for(rec_path) -> dict:
        for s in state["target_specs"]:
            if str(s["path"]) == str(rec_path):
                return s
        return {}

    def build_config_for(rec_path) -> DockConfig:
        """Build a DockConfig for one receptor in a multi-receptor run using the
        per-target chain + native-ligand chosen in select_target_specs()."""
        rec = str(rec_path)
        spec = _spec_for(rec_path)
        out_dir = str(Path(state["out_dir"]) / safe_name(Path(rec).stem))
        # Receptor = protein (+ structural metals) only. Excluding all ligands
        # keeps the pocket clean — critical when a chain holds >1 ligand, or the
        # co-crystal ligand would otherwise occupy the VS/redocking site.
        receptor_comps = [c for c in parse_pdb_components(rec)
                          if c.get("type") in {"Protein", "Metal Ion"}]
        common = dict(
            receptor=rec, out_dir=out_dir,
            receptor_components=receptor_comps,
            scoring=state["scoring"], modes=state["mode"], size=state["size"],
            exhaustiveness=state["preset"]["ex"], ad4_exhaustiveness=state["preset"]["ad4ex"],
            n_poses=state["preset"]["poses"], cpu=state["preset"]["cpu"],
            flex_residues=[],                # auto-detected per receptor in dock()
            flex_distance=state["flex_distance"],
            jobs=state["jobs"],
            # One shared PDBQT cache for every receptor: the ligand library is
            # identical across them, so converting it N times is pure waste.
            ligand_cache_dir=str(Path(state["out_dir"]) / "ligand_ready_pdbqt"),
        )
        if state["purpose"] == "redocking":
            if not spec.get("resname"):
                raise RuntimeError(f"{Path(rec).name}: tak ada native ligand untuk redocking")
            center = compute_ligand_center(rec, spec["resname"], spec["chain"], spec["resseq"])
            return DockConfig(
                ligands=[rec], center=center,
                native_ligand=spec["resname"], native_chain=spec["chain"],
                native_resseq=spec["resseq"], **common,
            )
        # Virtual Screening: dock the shared ligand library.
        if state["multi_center_mode"] == "native" and spec.get("resname"):
            center = compute_ligand_center(rec, spec["resname"], spec["chain"], spec["resseq"])
        else:
            center = _protein_center(rec)
        return DockConfig(
            ligands=state["ligand_paths"], center=center,
            simultaneous=state["simultaneous"], arrangement=state["arrangement"],
            max_groups=state["max_groups"],
            **common,
        )

    def run_multi_receptor(tools) -> list[dict]:
        all_rows: list[dict] = []
        skipped: list[tuple[str, str]] = []
        targets = state["target_paths"]
        n = len(targets)
        for i, rec in enumerate(targets, start=1):
            name = Path(rec).name
            print("\n" + sty(glyph("◆", "*"), "cyan", "bold")
                  + sty(f" Receptor [{i}/{n}]: {name}", "bold"))
            try:
                rows = dock(build_config_for(rec), tools)
                for row in rows:            # tag so the combined CSV is readable
                    row["receptor"] = name
                all_rows.extend(rows)
            except Exception as exc:
                print(sty(f"  ! Receptor {name} gagal, dilewati: {exc}", "yellow"))
                skipped.append((name, str(exc)))
        _report_multi_receptor(Path(state["out_dir"]), all_rows, skipped)
        return all_rows

    def resolve_tools_with_repair():
        while True:
            tools = resolve_tools(_tool_args_namespace(use_wsl=bool(state["backend"])))
            try:
                cfg = build_config_for(state["target_paths"][0]) if state["multi_receptor"] else build_config()
                validate_rules(cfg, tools)
                return tools
            except RuntimeError as exc:
                if any(sf in AD4_SCORING for sf in state["scoring"]):
                    action = _prompt_choice(
                        f"{exc}\nPilih tindakan:",
                        [
                            ("Gunakan WSL backend", "wsl"),
                            ("Hapus AD4/AD4-GPU dari scoring", "remove_ad"),
                            ("Batalkan", "cancel"),
                        ],
                    )
                    if action == "wsl":
                        state["backend"] = True
                        continue
                    if action == "remove_ad":
                        state["scoring"] = [sf for sf in state["scoring"] if sf not in AD4_SCORING]
                        if not state["scoring"]:
                            state["scoring"] = ["vina"]
                        continue
                else:
                    action = _prompt_choice(
                        f"{exc}\nPilih tindakan:",
                        [("Ubah score function", "scoring"), ("Batalkan", "cancel")],
                    )
                    if action == "scoring":
                        select_scoring()
                        continue
                raise KeyboardInterrupt

    try:
        select_purpose()
        select_job_dir()
        report_layout()
        select_target()
        if state["multi_receptor"]:
            if state["purpose"] == "virtual_screening":
                select_ligand_library()
                select_multi_center()
                if state["multi_center_mode"] == "native":
                    select_target_specs()
            else:  # multi redocking: pick chain + native ligand per receptor
                select_target_specs()
        elif state["purpose"] == "redocking":
            select_receptor_components()
            select_native_ligand()
        else:
            select_receptor_components()
            select_ligand_library()
            select_center()
        select_scoring()
        select_backend()
        validate_tools_early()
        select_mode()
        select_box()
        select_flex_residues()
        select_mlsd()
        select_preset()
        select_jobs()
        select_output()

        while True:
            print_protocol()
            action = _prompt_choice(
                "Lanjutkan?",
                [
                    ("Jalankan docking", "run"),
                    ("Ubah score function", "scoring"),
                    ("Ubah box/center", "box"),
                    ("Ubah mode docking", "mode"),
                    ("Ubah target/ligand", "files"),
                    ("Batalkan", "cancel"),
                ],
            )
            if action == "run":
                tools = resolve_tools_with_repair()
                if state["multi_receptor"]:
                    rows = run_multi_receptor(tools)
                else:
                    rows = dock(build_config(), tools)
                print(f"\nladock > Selesai. {len(rows)} hasil docking ditulis ke {state['out_dir']}")
                nxt = _prompt_choice(
                    "Apa selanjutnya?",
                    [("Jalankan docking baru", "again"), ("Keluar", "exit")],
                )
                if nxt == "again":
                    return _WIZARD_RESTART
                print(sty("\n  Terima kasih telah menggunakan LADOCK.", "gray"))
                return 0
            if action == "scoring":
                select_scoring()
                select_backend()
                validate_tools_early()
                select_mlsd()
            elif action == "box":
                if state["multi_receptor"]:
                    if state["purpose"] == "virtual_screening":
                        select_multi_center()
                        if state["multi_center_mode"] == "native":
                            select_target_specs()
                    # multi redocking: center from target_specs already chosen
                elif state["purpose"] == "redocking":
                    state["center"] = compute_ligand_center(
                        str(state["target_path"]),
                        state["native_ligand"],
                        state["native_chain"],
                        state["native_resseq"],
                    )
                else:
                    select_center()
                select_box()
                select_flex_residues()
            elif action == "mode":
                select_mode()
                select_flex_residues()
            elif action == "files":
                report_layout()
                select_target()
                if state["multi_receptor"]:
                    if state["purpose"] == "virtual_screening":
                        select_ligand_library()
                        select_multi_center()
                        if state["multi_center_mode"] == "native":
                            select_target_specs()
                        select_mlsd()
                    else:  # multi redocking
                        select_target_specs()
                elif state["purpose"] == "redocking":
                    select_receptor_components()
                    select_native_ligand()
                else:
                    select_receptor_components()
                    select_ligand_library()
                    select_center()
                    select_mlsd()
            else:
                print("ladock > Dibatalkan oleh user.")
                return 0
    except KeyboardInterrupt:
        print("\nladock > Dibatalkan.")
        return 0
    except EOFError:
        # stdin closed (piped/redirected input that ran out) — exit cleanly
        # instead of dumping a traceback from input().
        print("\nladock > Input berakhir (EOF). Dibatalkan.")
        return 0


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _cache_command(args) -> int:
    """Show or clear the on-disk AutoGrid4 map store.

    Cached maps are regenerable but bulky, so they need a supported way to be
    inspected and thrown away rather than a path the user has to guess.
    """
    root = Path(args.grid_cache).resolve() if args.grid_cache else cache_root() / "grids"
    if not root.is_dir():
        print(f"Grid cache: {root} (kosong)")
        return 0
    entries = [d for d in root.iterdir()
               if d.is_dir() and (d / _COMPLETE_MARKER).is_file()]
    partial = [d for d in root.iterdir() if d.is_dir() and d.name.startswith(".building-")]
    size_mb = _dir_size(root) / (1024 * 1024)
    print(f"Grid cache: {root}")
    print(f"  {len(entries)} map set, {size_mb:.1f} MB"
          + (f", {len(partial)} sisa build yang gagal" if partial else ""))
    if not args.clear:
        print("  Hapus dengan: ladock-cli cache --clear")
        return 0
    shutil.rmtree(root, ignore_errors=True)
    print(f"  Dihapus ({size_mb:.1f} MB dibebaskan).")
    return 0


def add_tool_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vina", default="", help="Override Vina executable")
    parser.add_argument("--autogrid4", default="", help="Override AutoGrid4 executable")
    parser.add_argument("--autodock4", default="", help="Override AutoDock4 executable")
    parser.add_argument("--autodock-gpu", default="", help="Override AutoDock-GPU executable")
    parser.add_argument("--mgltools", default="", help="MGLTools root directory")
    parser.add_argument("--adfrsuite", default="",
                        help="ADFRsuite root directory (provides adfr/agfr)")
    parser.add_argument("--pythonsh", default="", help="MGLTools pythonsh override")
    parser.add_argument("--timeout", type=float, default=7200,
                        help="Wall-clock limit in seconds for a single engine or "
                             "preparation call (default: 7200; 0 disables it)")
    parser.add_argument("--use-wsl", action="store_true", help="Dispatch Linux-only tools through WSL")
    parser.add_argument("--wsl-distro", default="", help="Optional WSL distro name")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LADOCK rule-based docking CLI agent")
    sub = parser.add_subparsers(dest="command", required=True)

    wizard = sub.add_parser("wizard", help="Run guided closed-question docking dialog")
    wizard.set_defaults(wizard=True)

    comp = sub.add_parser("components", help="List molecular components in a PDB")
    comp.add_argument("pdb")

    dock_p = sub.add_parser("dock", help="Run rule-based docking")
    dock_p.add_argument("--receptor", required=True, help="Receptor PDB/PDBQT")
    dock_p.add_argument("--ligand", required=True, action="append", help="Ligand file; repeat for batch docking")
    dock_p.add_argument("--out", required=True, help="Output directory")
    dock_p.add_argument("--center", nargs=3, type=float, metavar=("X", "Y", "Z"))
    dock_p.add_argument("--size", nargs=3, type=float, default=(20.0, 20.0, 20.0),
                        metavar=("SX", "SY", "SZ"))
    dock_p.add_argument("--scoring", nargs="+", default=["vina"],
                        choices=["vina", "vinardo", "ad4", "ad4gpu", "adfr"])
    dock_p.add_argument("--mode", nargs="+", default=["rigid"], choices=["rigid", "flexible"])
    dock_p.add_argument("--native-ligand", default="", help="Residue name used to auto-center the box")
    dock_p.add_argument("--native-chain", default="", help="Native ligand chain filter")
    dock_p.add_argument("--native-resseq", default="", help="Native ligand residue number filter")
    dock_p.add_argument("--flex-residue", action="append", default=[],
                        help="Flexible residue spec chain:resname:resseq; repeatable")
    dock_p.add_argument("--flex-distance", type=float, default=3.0)
    dock_p.add_argument("--simultaneous", type=int, default=1,
                        help="MLSD: dock N different ligands together in one pocket (Vina/Vinardo only)")
    dock_p.add_argument("--arrangement", default="combination",
                        choices=["combination", "permutation"],
                        help="MLSD grouping of the ligand library (default: combination)")
    dock_p.add_argument("--max-groups", type=int, default=5000,
                        help="Refuse an MLSD run with more groups than this "
                             "(default: 5000; 0 disables the limit)")
    dock_p.add_argument("--spacing", type=float, default=0.375)
    dock_p.add_argument("--exhaustiveness", type=int, default=8)
    dock_p.add_argument("--ad4-exhaustiveness", type=int, default=8)
    dock_p.add_argument("--n-poses", type=int, default=9)
    dock_p.add_argument("--energy-range", type=int, default=3)
    dock_p.add_argument("--cpu", type=int, default=4,
                        help="CPU threads handed to a single Vina run")
    dock_p.add_argument("--jobs", type=int, default=1,
                        help="Ligands docked concurrently (default: 1). Keep "
                             "--jobs x --cpu at or below your core count.")
    dock_p.add_argument("--grid-cache", default="", metavar="DIR",
                        help="Where to keep reusable AutoGrid4 maps "
                             "(default: the platform cache directory)")
    dock_p.add_argument("--no-grid-cache", action="store_true",
                        help="Do not keep AutoGrid4 maps between runs")
    dock_p.add_argument("--seed", type=int, default=None,
                        help="Explicit RNG seed for reproducible runs (Vina, "
                             "AutoDock4 and AutoDock-GPU). Use a non-zero value: "
                             "Vina reads seed 0 as 'choose randomly'.")
    dock_p.add_argument("--ga-pop-size", type=int, default=150)
    dock_p.add_argument("--cluster-rmsd", type=float, default=2.0)
    dock_p.add_argument("-v", "--verbose", action="store_true",
                        help="Show raw engine commands & output (default: hidden)")
    add_tool_args(dock_p)

    cache_p = sub.add_parser("cache", help="Inspect or clear the AutoGrid4 map cache")
    cache_p.add_argument("--clear", action="store_true", help="Delete every cached map set")
    cache_p.add_argument("--grid-cache", default="", metavar="DIR",
                         help="Operate on this store instead of the default one")

    prep = sub.add_parser("prepare-receptor", help="Prepare receptor PDB/PDBQT")
    prep.add_argument("--receptor", required=True)
    prep.add_argument("--out", required=True)
    add_tool_args(prep)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    # Internal dispatch (frozen build): run a bundled Meeko CLI module in-process,
    # since a PyInstaller exe cannot do `-m module`.
    if len(sys.argv) >= 3 and sys.argv[1] == "--_meeko":
        import runpy
        module = sys.argv[2]
        sys.argv = [module, *sys.argv[3:]]
        try:
            runpy.run_module(module, run_name="__main__")
        except SystemExit as exc:
            return int(exc.code or 0)
        return 0
    init_terminal()
    # Hard-lock: once the academic free period ends, the CLI stops working
    # entirely (mirrors LADOCK Desktop).
    if license_expired():
        print("\n" + license_note())
        print(sty("  LADOCK CLI dinonaktifkan — masa lisensi telah berakhir.",
                  "red", "bold"))
        print(sty("  Hubungi laode_aman@ung.ac.id untuk perpanjangan/lisensi.", "gray"))
        return 3
    parser = build_parser()
    argv = list(argv) if argv is not None else sys.argv[1:]
    if not argv:
        return run_wizard()
    args = parser.parse_args(argv)
    if getattr(args, "verbose", False):
        set_verbose(True)
    if args.command != "wizard":          # wizard shows the notice in its banner
        print(license_note())
    try:
        if args.command == "wizard":
            return run_wizard()
        if args.command == "cache":
            return _cache_command(args)
        if args.command == "components":
            for i, comp in enumerate(parse_pdb_components(args.pdb), start=1):
                print(
                    f"{i:02d} {comp['type']:<10} chain={comp['chain']:<3} "
                    f"resname={comp['resname']:<12} resseq={comp.get('resseq', ''):<5} "
                    f"res={comp['n_residues']:<4} atoms={comp['n_atoms']}"
                )
            return 0
        tools = resolve_tools(args)
        if args.command == "prepare-receptor":
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            prepare_receptor_pdbqt(args.receptor, args.out, tools)
            print(args.out)
            return 0
        cfg = DockConfig(
            receptor=args.receptor,
            ligands=args.ligand,
            out_dir=args.out,
            scoring=args.scoring,
            modes=args.mode,
            center=tuple(args.center) if args.center else None,
            size=tuple(args.size),
            spacing=args.spacing,
            exhaustiveness=args.exhaustiveness,
            ad4_exhaustiveness=args.ad4_exhaustiveness,
            n_poses=args.n_poses,
            energy_range=args.energy_range,
            cpu=args.cpu,
            seed=args.seed,
            ga_pop_size=args.ga_pop_size,
            cluster_rmsd=args.cluster_rmsd,
            flex_residues=args.flex_residue,
            flex_distance=args.flex_distance,
            simultaneous=args.simultaneous,
            arrangement=args.arrangement,
            max_groups=args.max_groups,
            jobs=args.jobs,
            grid_cache_dir=args.grid_cache,
            grid_cache_enabled=not args.no_grid_cache,
            native_ligand=args.native_ligand,
            native_chain=args.native_chain,
            native_resseq=args.native_resseq,
        )
        rows = dock(cfg, tools)
        log(f"Completed {len(rows)} docking result(s).")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
