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
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


AGENT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[1]


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
    if is_windows_host():
        return "linux" if use_wsl_backend else "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def _agent_bin_roots(platform_name: str) -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("LADOCK_AGENT_BIN", "").strip()
    if env_root:
        roots.append(Path(env_root) / platform_name)
        roots.append(Path(env_root))
    # Frozen one-file build (PyInstaller): binaries are bundled under _MEIPASS/bin.
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(Path(meipass) / "bin" / platform_name)
        roots.append(Path(meipass) / "bin")
    roots.append(AGENT_ROOT / "bin" / platform_name)
    roots.append(AGENT_ROOT / "bin")
    # Fall back to the LADOCK Desktop bundled binaries (the shared bin/ set),
    # so the agent works out of the box without copying binaries or setting
    # LADOCK_AGENT_BIN.
    roots.append(REPO_ROOT / "desktop" / "bin" / platform_name)
    roots.append(REPO_ROOT / "desktop" / "bin")
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
}


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


def print_banner(version: str = "2.0.0") -> None:
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


def _looks_like_smiles(value: str) -> bool:
    value = value.strip()
    return bool(value) and any(c in value for c in ("C", "c", "N", "n", "O", "o", "S", "F", "P", "B", "[", "(", "="))


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


def _iter_smiles_rows(path: str) -> Iterator[tuple[str, str]]:
    ext = Path(path).suffix.lower()
    if ext in {".csv", ".tsv"}:
        sep = "," if ext == ".csv" else "\t"
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh, delimiter=sep)
            headers = next(reader, [])
            lower = [h.strip().lower() for h in headers]
            smiles_idx = lower.index("smiles") if "smiles" in lower else 0
            name_idx = lower.index("name") if "name" in lower else -1
            for i, row in enumerate(reader, start=1):
                if len(row) <= smiles_idx:
                    continue
                smiles = row[smiles_idx].strip()
                if not _looks_like_smiles(smiles):
                    for val in row:
                        if _looks_like_smiles(val):
                            smiles = val.strip()
                            break
                if not _looks_like_smiles(smiles):
                    continue
                name = row[name_idx].strip() if 0 <= name_idx < len(row) else f"row_{i}"
                yield name, smiles
        return
    if ext in {".xlsx", ".xls"}:
        try:
            import pandas as pd
        except ImportError:
            raise RuntimeError("pandas diperlukan untuk membaca Excel.")
        df = pd.read_excel(path)
        lower = {str(c).strip().lower(): c for c in df.columns}
        smiles_col = lower.get("smiles") or df.columns[0]
        name_col = lower.get("name")
        for i, row in df.iterrows():
            smiles = str(row[smiles_col]).strip()
            if not _looks_like_smiles(smiles):
                continue
            name = str(row[name_col]).strip() if name_col else f"row_{i + 1}"
            yield name, smiles
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
                tmp_sdf = os.path.join(out_dir, f"__tmp_{mol_name}_{i}.sdf")
                out = os.path.join(out_dir, f"{mol_name}_{i}.pdbqt")
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
                tmp_mol2 = os.path.join(out_dir, f"__tmp_{mol_name}_{i}.mol2")
                out = os.path.join(out_dir, f"{mol_name}_{i}.pdbqt")
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
    if _convert_file_to_pdbqt(lig_path, out, pythonsh, prep_lig, log_fn,
                              use_wsl_backend, wsl_distro):
        return [(base, out)]
    if log_fn:
        log_fn(f"  Cannot convert ligand: {lig_path}")
    return []


def safe_name(value: str, fallback: str = "item") -> str:
    return re.sub(r"[^\w\-.]", "_", value or fallback) or fallback


def parse_pdb_components(pdb_path: str) -> list[dict]:
    chains: dict[str, dict] = {}
    ligands: dict[tuple[str, str, str], dict] = {}
    metals = {"chain": "-", "resname": "Metals", "resseq": "", "type": "Metal Ion",
              "resnames": set(), "n_residues": 0, "n_atoms": 0}
    waters = {"chain": "-", "resname": "HOH", "resseq": "", "type": "Water",
              "n_residues": 0, "n_atoms": 0}
    others = {"chain": "-", "resname": "Others", "resseq": "", "type": "Other",
              "resnames": set(), "n_residues": 0, "n_atoms": 0}

    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            rec = line[:6].strip()
            if rec not in ("ATOM", "HETATM"):
                continue
            resname = line[17:20].strip()
            chain = line[21].strip() or "?"
            resseq = line[22:26].strip()
            elem = line[76:78].strip().upper() if len(line) > 76 else ""

            if rec == "ATOM" and resname in STANDARD_AA:
                chains.setdefault(chain, {
                    "chain": chain, "resname": f"Chain {chain}", "resseq": "",
                    "type": "Protein", "resseqs": set(), "n_atoms": 0,
                })
                chains[chain]["resseqs"].add(resseq)
                chains[chain]["n_atoms"] += 1
            elif resname in WATER_RESNAMES:
                waters["n_residues"] += 1
                waters["n_atoms"] += 1
            elif rec == "HETATM" and (elem in METAL_ELEMENTS or resname in METAL_ELEMENTS):
                metals["resnames"].add(resname)
                metals["n_residues"] += 1
                metals["n_atoms"] += 1
            elif rec == "HETATM":
                key = (chain, resname, resseq)
                ligands.setdefault(key, {
                    "chain": chain, "resname": resname, "resseq": resseq,
                    "type": "Ligand", "n_residues": 1, "n_atoms": 0,
                })
                ligands[key]["n_atoms"] += 1
            else:
                others["resnames"].add(resname)
                others["n_residues"] += 1
                others["n_atoms"] += 1

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
    if metals["n_atoms"] > 0:
        metals["resname"] = ", ".join(sorted(metals.pop("resnames"))) or "Metal"
        result.append(metals)
    if waters["n_atoms"] > 0:
        result.append(waters)
    if others["n_atoms"] > 0:
        others["resname"] = ", ".join(sorted(others.pop("resnames"))) or "Others"
        result.append(others)
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
    rec = line[:6].strip()
    if rec not in ("ATOM", "HETATM"):
        return True
    chain = line[21].strip() or "?"
    resname = line[17:20].strip()
    resseq = line[22:26].strip()
    elem = line[76:78].strip().upper() if len(line) > 76 else ""
    for comp in components:
        ctype = comp.get("type", "")
        if ctype == "Protein" and rec == "ATOM" and chain == comp.get("chain"):
            return True
        if ctype == "Ligand" and rec == "HETATM":
            if (
                chain == comp.get("chain")
                and resname == comp.get("resname")
                and resseq == str(comp.get("resseq", ""))
            ):
                return True
        if ctype == "Water" and resname in WATER_RESNAMES:
            return True
        if ctype == "Metal Ion" and rec == "HETATM":
            if elem in METAL_ELEMENTS or resname in METAL_ELEMENTS:
                return True
        if ctype == "Other" and rec == "HETATM":
            is_water = resname in WATER_RESNAMES
            is_metal = elem in METAL_ELEMENTS or resname in METAL_ELEMENTS
            if not is_water and not is_metal:
                return True
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
            use_wsl_backend: bool = False, wsl_distro: str = "") -> None:
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
    tail: list[str] = []
    for line in proc.stdout:
        if _VERBOSE:
            log(line.rstrip())
        else:                       # keep a short tail to explain failures
            tail.append(line.rstrip())
            if len(tail) > 20:
                tail.pop(0)
    proc.wait()
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
    use_wsl_backend: bool = False
    wsl_distro: str = ""


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
    seed: int = 0
    ga_pop_size: int = 150
    cluster_rmsd: float = 2.0
    flex_residues: list[str] = field(default_factory=list)
    flex_distance: float = 3.0
    simultaneous: int = 1
    arrangement: str = "combination"
    native_ligand: str = ""
    native_chain: str = ""
    native_resseq: str = ""


def resolve_tools(args) -> ToolConfig:
    use_wsl_backend = bool(args.use_wsl)
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
        use_wsl_backend=use_wsl_backend,
        wsl_distro=wsl_distro,
    )


def validate_rules(cfg: DockConfig, tools: ToolConfig) -> None:
    unknown = set(cfg.scoring) - (VINA_SCORING | AD4_SCORING)
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
    if "flexible" in cfg.modes and not cfg.flex_residues:
        if cfg.center is None:
            raise RuntimeError("Flexible mode needs a box center to auto-detect residues.")
        cfg.flex_residues = find_flex_residues(
            cfg.receptor, cfg.center[0], cfg.center[1], cfg.center[2], cfg.flex_distance
        )
        if not cfg.flex_residues:
            raise RuntimeError("No flexible residues were found. Use --flex-residue or increase --flex-distance.")
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
        raise RuntimeError(f"Vina executable was not found: {tools.vina_path}")
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
    )
    # An empty flex file means no residue matched -s: fail loudly instead of
    # silently docking rigid (Vina ignores an empty --flex; AD4 errors on it).
    if not os.path.isfile(rigid) or not os.path.isfile(flex) or os.path.getsize(flex) == 0:
        raise RuntimeError(
            "Flexible receptor split produced no flexible residues "
            f"(spec: {flex_spec}). Check --flex-residue / --flex-distance.")
    return rigid, flex


def build_ad4_grids(cfg: DockConfig, tools: ToolConfig, mode_dir: str,
                    rec_pdbqt: str, lig_pdbqt: str, flex_pdbqt: str = "") -> tuple[str, str]:
    gpf = os.path.join(mode_dir, "grid.gpf")
    glg = os.path.join(mode_dir, "grid.glg")
    local_rec = os.path.join(mode_dir, os.path.basename(rec_pdbqt))
    local_lig = os.path.join(mode_dir, os.path.basename(lig_pdbqt))
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
        local_flex = os.path.join(mode_dir, os.path.basename(flex_pdbqt))
        shutil.copy2(flex_pdbqt, local_flex)
        cmd += ["-x", local_flex]
    run_cmd(cmd, "prepare_gpf4.py", use_wsl_backend=tools.use_wsl_backend, wsl_distro=tools.wsl_distro)
    run_cmd([tools.ag4_path, "-p", gpf, "-l", glg], "autogrid4", cwd=mode_dir,
            use_wsl_backend=tools.use_wsl_backend, wsl_distro=tools.wsl_distro)
    fld_files = [f for f in os.listdir(mode_dir) if f.endswith(".maps.fld")]
    if not fld_files:
        raise RuntimeError(f"AutoGrid4 did not produce .maps.fld. Check {glg}")
    return os.path.join(mode_dir, fld_files[0]), gpf


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
    if cfg.seed:
        cmd += ["--seed", str(cfg.seed)]
    if flex_pdbqt:
        cmd += ["--flex", flex_pdbqt]
    run_cmd(cmd, f"AutoDock Vina ({sf})", use_wsl_backend=tools.use_wsl_backend,
            wsl_distro=tools.wsl_distro)
    if not os.path.isfile(out):
        raise RuntimeError(f"Vina did not produce {out}")
    return out


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
    run_cmd(cmd, "prepare_dpf42.py", use_wsl_backend=tools.use_wsl_backend, wsl_distro=tools.wsl_distro)
    run_cmd([tools.ad4_path, "-p", dpf, "-l", dlg], "autodock4", cwd=mode_dir,
            use_wsl_backend=tools.use_wsl_backend, wsl_distro=tools.wsl_distro)
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
    if cfg.seed:
        cmd += ["--seed", str(cfg.seed)]
    if flex_pdbqt:
        cmd += ["--flexres", flex_pdbqt]
    run_cmd(cmd, "AutoDock-GPU", cwd=mode_dir, use_wsl_backend=tools.use_wsl_backend,
            wsl_distro=tools.wsl_distro)
    dlg = dlg_base + ".dlg"
    if not os.path.isfile(dlg):
        raise RuntimeError(f"AutoDock-GPU did not produce {dlg}")
    return dlg


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


class _DockReporter:
    """Renders the CLI's own docking output: a streaming result table with a
    per-ligand percent for screening runs, and a final best-hit footer."""

    def __init__(self, title: str, subtitle: str, show_pct: bool):
        self.show_pct = show_pct
        self.best: tuple[str, str, float] | None = None
        self._hdr = False
        self._rule_w = 0
        ui_header(title, subtitle)

    def _print_header(self) -> None:
        dg = glyph("ΔG (kcal/mol)", "dG (kcal/mol)")
        cols = f"{'Ligand'.ljust(26)} {'Engine'.ljust(8)} {'Mode'.ljust(8)} {dg}"
        pw = 7 if self.show_pct else 0           # width of the "[ 20%] " prefix
        self._rule_w = len(cols) + pw
        print("   " + (" " * pw) + sty(cols, "gray"))
        print("   " + sty(glyph("─", "-") * self._rule_w, "teal"))
        self._hdr = True

    def emit(self, ligand: str, engine: str, mode: str, energy, pct=None) -> None:
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
        converted_dir = out_dir / "ligand_ready_pdbqt"
        converted_dir.mkdir(exist_ok=True)
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
            gen = itertools.permutations if cfg.arrangement == "permutation" else itertools.combinations
            groups = list(gen(all_ligs, cfg.simultaneous))
            _vlog(f"\nMLSD: {len(groups)} group(s) of {cfg.simultaneous} ligands "
                  f"({cfg.arrangement}) via {', '.join(vina_sfs)}")
            reporter = _DockReporter(
                "MLSD — docking simultan",
                f"{len(groups)} grup ({cfg.simultaneous} ligan/grup) · "
                f"{', '.join(vina_sfs)} · {modes_txt}",
                show_pct=len(groups) > 1,
            )
            for gi, group in enumerate(groups, start=1):
                names = [name for name, _p in group]
                paths = [p for _n, p in group]
                combo_name = "+".join(names)
                group_label = "MLSD_" + safe_name("+".join(names))
                gpct = int(gi * 100 / len(groups))
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
        if per_ligand_vina or ad4_sfs:
            n_lig = len(all_ligs)
            show_pct = n_lig > 1
            if reporter is None:                 # not created by the MLSD phase
                title = "Virtual Screening" if show_pct else "Redocking"
                reporter = _DockReporter(
                    title, f"{n_lig} ligan · {', '.join(cfg.scoring)} · {modes_txt}",
                    show_pct=show_pct,
                )
            for li, (mol_name, lig_pdbqt) in enumerate(all_ligs, start=1):
                lpct = int(li * 100 / n_lig) if show_pct else None
                for mode in cfg.modes:
                    use_flex = mode == "flexible"
                    mode_dir = out_dir / mode / safe_name(mol_name)
                    mode_dir.mkdir(parents=True, exist_ok=True)
                    receptor_for_vina = rigid_pdbqt if use_flex else rec_pdbqt
                    flex_for_run = flex_pdbqt if use_flex else ""

                    for sf in per_ligand_vina:
                        sf_dir = mode_dir / sf
                        sf_dir.mkdir(exist_ok=True)
                        out = run_vina(cfg, tools, str(sf_dir), receptor_for_vina, lig_pdbqt, sf, flex_for_run)
                        row = {"ligand": mol_name, "mode": mode, "scoring": sf, "out_path": out,
                               **parse_result(out, sf)}
                        results.append(row)
                        meta["outputs"].append(row)
                        reporter.emit(mol_name, sf, mode, row.get("energy"), pct=lpct)

                    if ad4_sfs:
                        # Grid maps, autodock4, and autodock-gpu must share ONE
                        # directory: the .dpf/.fld reference the maps by relative
                        # name, so running the docker elsewhere can't find them.
                        grid_dir = mode_dir / "ad4"
                        grid_dir.mkdir(exist_ok=True)
                        fld, gpf = build_ad4_grids(cfg, tools, str(grid_dir), receptor_for_vina, lig_pdbqt, flex_for_run)
                        if "ad4" in cfg.scoring:
                            out = run_ad4(cfg, tools, str(grid_dir), receptor_for_vina, lig_pdbqt, gpf, flex_for_run)
                            row = {"ligand": mol_name, "mode": mode, "scoring": "ad4", "out_path": out,
                                   **parse_result(out, "ad4")}
                            results.append(row)
                            meta["outputs"].append(row)
                            reporter.emit(mol_name, "ad4", mode, row.get("energy"), pct=lpct)
                        if "ad4gpu" in cfg.scoring:
                            out = run_adgpu(cfg, tools, str(grid_dir), lig_pdbqt, fld, flex_for_run)
                            row = {"ligand": mol_name, "mode": mode, "scoring": "ad4gpu", "out_path": out,
                                   **parse_result(out, "ad4gpu")}
                            results.append(row)
                            meta["outputs"].append(row)
                            reporter.emit(mol_name, "ad4gpu", mode, row.get("energy"), pct=lpct)

        if reporter is not None:
            reporter.footer(out_dir)
        write_results(out_dir / "results.csv", results)
        meta["finished_at"] = _dt.datetime.now().isoformat()
        (out_dir / "run.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return results
    finally:
        if not results:
            _vlog(f"Temporary files kept for inspection: {tmp_dir}")


def write_results(path: Path, rows: list[dict]) -> None:
    headers = ["ligand", "mode", "scoring", "energy", "rmsd_lb", "rmsd_ub", "out_path"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in headers})
    _vlog(f"\nResults CSV: {path}")


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
                total += max(0, len(rows) - 1)          # minus header row
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
        pythonsh="",
    )


def run_wizard() -> int:
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
            [("Vina", "vina"), ("Vinardo", "vinardo"), ("AD4", "ad4"), ("AD4-GPU", "ad4gpu")],
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
        state["arrangement"] = _prompt_choice(
            "Susunan kombinasi ligan?",
            [
                ("Combination: set tak berurut (disarankan)", "combination"),
                ("Permutation: berurut, jauh lebih banyak grup", "permutation"),
            ],
        )
        gen = itertools.permutations if state["arrangement"] == "permutation" else itertools.combinations
        n_groups = sum(1 for _ in gen(range(n_lig), state["simultaneous"]))
        print(f"ladock > MLSD: {n_groups} grup x {state['simultaneous']} ligan "
              f"({state['arrangement']}).")

    def select_preset():
        state["preset"] = _prompt_choice(
            "Preset parameter pencarian?",
            [
                ("Seimbang/profesional: exhaustiveness 8, poses 9", {"label": "Seimbang", "ex": 8, "ad4ex": 8, "poses": 9, "cpu": 4}),
                ("Cepat: exhaustiveness 4, poses 5", {"label": "Cepat", "ex": 4, "ad4ex": 4, "poses": 5, "cpu": 4}),
                ("Teliti: exhaustiveness 16, poses 20", {"label": "Teliti", "ex": 16, "ad4ex": 16, "poses": 20, "cpu": 4}),
            ],
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
            **common,
        )

    def run_multi_receptor(tools) -> list[dict]:
        all_rows: list[dict] = []
        targets = state["target_paths"]
        n = len(targets)
        for i, rec in enumerate(targets, start=1):
            name = Path(rec).name
            print("\n" + sty(glyph("◆", "*"), "cyan", "bold")
                  + sty(f" Receptor [{i}/{n}]: {name}", "bold"))
            try:
                rows = dock(build_config_for(rec), tools)
                all_rows.extend(rows)
            except Exception as exc:
                print(sty(f"  ! Receptor {name} gagal, dilewati: {exc}", "yellow"))
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
                    return run_wizard()
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


def add_tool_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vina", default="", help="Override Vina executable")
    parser.add_argument("--autogrid4", default="", help="Override AutoGrid4 executable")
    parser.add_argument("--autodock4", default="", help="Override AutoDock4 executable")
    parser.add_argument("--autodock-gpu", default="", help="Override AutoDock-GPU executable")
    parser.add_argument("--mgltools", default="", help="MGLTools root directory")
    parser.add_argument("--pythonsh", default="", help="MGLTools pythonsh override")
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
                        choices=["vina", "vinardo", "ad4", "ad4gpu"])
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
    dock_p.add_argument("--spacing", type=float, default=0.375)
    dock_p.add_argument("--exhaustiveness", type=int, default=8)
    dock_p.add_argument("--ad4-exhaustiveness", type=int, default=8)
    dock_p.add_argument("--n-poses", type=int, default=9)
    dock_p.add_argument("--energy-range", type=int, default=3)
    dock_p.add_argument("--cpu", type=int, default=4)
    dock_p.add_argument("--seed", type=int, default=0)
    dock_p.add_argument("--ga-pop-size", type=int, default=150)
    dock_p.add_argument("--cluster-rmsd", type=float, default=2.0)
    dock_p.add_argument("-v", "--verbose", action="store_true",
                        help="Show raw engine commands & output (default: hidden)")
    add_tool_args(dock_p)

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
