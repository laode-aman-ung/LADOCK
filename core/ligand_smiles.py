from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

from core.wsl_backend import command_exists, prepare_subprocess, wsl_available


def smiles_from_structure(path: str, wsl_distro: str = "") -> str:
    path = (path or "").strip()
    if not path or not os.path.isfile(path):
        return ""

    smiles = _smiles_via_obabel(path)
    if smiles:
        return smiles

    if os.name == "nt" and wsl_available():
        smiles = _smiles_via_obabel_wsl(path, wsl_distro=wsl_distro)
        if smiles:
            return smiles

    return ""


def smiles_from_ccd(chem_comp_id: str) -> str:
    chem_comp_id = (chem_comp_id or "").strip().upper()
    if not chem_comp_id:
        return ""
    url = f"https://data.rcsb.org/rest/v1/core/chemcomp/{chem_comp_id}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.load(resp)
    except Exception:
        return ""

    descriptors = payload.get("pdbx_chem_comp_descriptor") or []
    preferred = []
    fallback = []
    for item in descriptors:
        dtype = str(item.get("type", "")).upper()
        program = str(item.get("program", "")).upper()
        descriptor = str(item.get("descriptor", "")).strip()
        if not descriptor or "SMILES" not in dtype:
            continue
        entry = (dtype, program, descriptor)
        if "CANONICAL" in dtype:
            preferred.append(entry)
        else:
            fallback.append(entry)

    for _, _, descriptor in preferred + fallback:
        return descriptor
    return ""


def _smiles_via_obabel(path: str) -> str:
    if not command_exists("obabel"):
        return ""
    return _run_smiles_command(["obabel", path, "-ocan"])


def _smiles_via_obabel_wsl(path: str, wsl_distro: str = "") -> str:
    if not command_exists("obabel", use_wsl_backend=True, wsl_distro=wsl_distro):
        return ""
    exec_cmd, exec_cwd = prepare_subprocess(
        ["obabel", path, "-ocan"],
        cwd=str(Path(path).resolve().parent),
        use_wsl_backend=True,
        wsl_distro=wsl_distro,
    )
    return _run_smiles_command(exec_cmd, cwd=exec_cwd)


def _run_smiles_command(cmd: list[str], cwd: str | None = None) -> str:
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        return text.split()[0]
    return ""
