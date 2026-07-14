"""
LADOCK — Ligand Importer / Format Converter
============================================

Central utility for converting any ligand file format to PDBQT, including
multi-molecule files (split into individual PDBQT per molecule).

Supported input formats
-----------------------
Format       Extension(s)       Multi-mol?  Notes
------------ ------------------ ----------- ----------------------------
PDBQT        .pdbqt             no          Used as-is
PDB          .pdb               no          MGLTools → obabel fallback
SDF/MDL      .sdf               YES         obabel -m split
MOL (V2000)  .mol               no          obabel
MOL2         .mol2              YES         obabel -m split
SMILES file  .smi .smiles       YES         one SMILES per line
SMILES TXT   .txt               YES         one SMILES per line
CSV/TSV      .csv .tsv          YES         auto-detect SMILES column
Excel        .xlsx .xls         YES         auto-detect SMILES column
SDF+name col .sdf (with title)  YES         molecule title used as name

Pipeline
--------
1. `expand_to_pdbqt(path, out_dir, pythonsh, prep_lig)` → list[(name, pdbqt_path)]
   - Single-mol formats → [(name, pdbqt)]
   - Multi-mol formats  → [(name_1, pdbqt_1), (name_2, pdbqt_2), …]

2. Conversion priority (per molecule):
   a. PDBQT — copy directly
   b. PDB   — MGLTools prepare_ligand4.py (if available)
   c. Any   — obabel + charge calculation + torsion detect
   d. SMILES — obabel --gen3d (3-D embedding via Open Babel)
   e. SMILES — RDKit AllChem.EmbedMolecule + obabel for PDBQT

3. Molecule naming:
   - From file title / _Name field in SDF
   - From first column of CSV/SMI
   - Fallback: basename_N
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, List, Tuple

from core.wsl_backend import command_exists, maybe_to_wsl_path, prepare_subprocess

# ─────────────────────────────────────────────────────────────────────────────
# Public constants
# ─────────────────────────────────────────────────────────────────────────────

#: Map extension → human-readable label shown in UI filter
SUPPORTED_EXTENSIONS: dict[str, str] = {
    '.pdbqt': 'AutoDock PDBQT',
    '.pdb':   'PDB',
    '.sdf':   'SDF / MDL Molfile (multi-mol)',
    '.mol':   'MDL MOL V2000',
    '.mol2':  'Tripos MOL2 (multi-mol)',
    '.smi':   'SMILES (multi-mol)',
    '.smiles':'SMILES (multi-mol)',
    '.txt':   'SMILES text (multi-mol)',
    '.csv':   'CSV with SMILES column (multi-mol)',
    '.tsv':   'TSV with SMILES column (multi-mol)',
    '.xlsx':  'Excel with SMILES column (multi-mol)',
    '.xls':   'Excel with SMILES column (multi-mol)',
}

# Qt-compatible file filter string for QFileDialog
FILE_FILTER = (
    "All Ligand Files (*.pdbqt *.pdb *.sdf *.mol *.mol2 "
    "*.smi *.smiles *.txt *.csv *.tsv *.xlsx *.xls);;"
    "PDBQT (*.pdbqt);;"
    "PDB (*.pdb);;"
    "SDF/Mol (*.sdf *.mol);;"
    "MOL2 (*.mol2);;"
    "SMILES (*.smi *.smiles *.txt);;"
    "CSV/TSV (*.csv *.tsv);;"
    "Excel (*.xlsx *.xls);;"
    "All Files (*)"
)

# Column name hints for SMILES auto-detection in CSV/Excel
_SMILES_COLUMN_HINTS = [
    'smiles', 'smi', 'canonical_smiles', 'isomeric_smiles',
    'molecule', 'structure', 'mol',
]
_NAME_COLUMN_HINTS = [
    'name', 'id', 'compound_id', 'cmpd_id', 'title',
    'molecule_name', 'compound_name', 'ligand',
]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(
    cmd: list[str],
    label: str = "",
    log_fn=None,
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
) -> bool:
    """Run a subprocess. Returns True if it succeeded (exit 0 + output file)."""
    try:
        exec_cmd, exec_cwd = prepare_subprocess(
            cmd,
            use_wsl_backend=use_wsl_backend,
            wsl_distro=wsl_distro,
        )
        result = subprocess.run(
            exec_cmd,
            cwd=exec_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if log_fn:
            combined = (result.stdout + result.stderr).decode('utf-8', errors='replace')
            for line in combined.splitlines():
                if line.strip():
                    log_fn(f"    {label}: {line}")
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        if log_fn:
            log_fn(f"    {label}: {exc}")
        return False


def _obabel_available(use_wsl_backend: bool = False, wsl_distro: str = "") -> bool:
    cmd, _ = _resolve_obabel_spec(use_wsl_backend, wsl_distro)
    return bool(cmd)


def _resolve_obabel_spec(
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
) -> tuple[str, dict[str, str]]:
    if command_exists('obabel', use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro):
        return 'obabel', {}
    # obabel ships inside the (Linux-only) bundled ADFRsuite.
    suite_root = Path(__file__).resolve().parents[1] / 'bin' / 'linux' / 'ADFRsuite-1.0'
    bundled = suite_root / 'bin' / 'obabelbin' / 'obabel'
    if bundled.is_file():
        env = {
            'BABEL_LIBDIR': str(suite_root / 'lib' / 'openbabel' / '2.4.1'),
            'BABEL_DATADIR': str(suite_root / 'share' / 'openbabel' / '2.4.1'),
            'LD_LIBRARY_PATH': str(suite_root / 'lib'),
        }
        return str(bundled), env
    return "", {}


def _smiles_to_pdbqt_obabel(smiles: str, out_path: str, mol_name: str = "",
                              log_fn=None, use_wsl_backend: bool = False,
                              wsl_distro: str = "") -> bool:
    """Convert a SMILES string to PDBQT via obabel (3-D generation)."""
    obabel_cmd, obabel_env = _resolve_obabel_spec(use_wsl_backend, wsl_distro)
    if not obabel_cmd:
        return False
    cmd = _with_env_prefix(
        [obabel_cmd, f'-:{smiles}', '--gen3d', '-O', out_path, '--partialcharge', 'gasteiger'],
        obabel_env,
        use_wsl_backend=use_wsl_backend,
    )
    if mol_name:
        cmd += ['--title', mol_name]
    return _run(cmd, 'obabel SMILES→PDBQT', log_fn, use_wsl_backend, wsl_distro)


def _smiles_to_pdbqt_rdkit(smiles: str, out_path: str, mol_name: str = "",
                             log_fn=None, use_wsl_backend: bool = False,
                             wsl_distro: str = "") -> bool:
    """Convert SMILES to PDBQT via RDKit 3D embedding + obabel."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError:
        return False
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return False
    try:
        mol = Chem.AddHs(mol)
        if AllChem.EmbedMolecule(mol, AllChem.ETKDGv3()) != 0:
            AllChem.EmbedMolecule(mol)
        try:
            AllChem.MMFFOptimizeMolecule(mol)
        except Exception:
            pass
    except Exception:
        return False
    name = mol_name or 'LIG'
    mol.SetProp('_Name', name)

    # Write a proper temp SDF, then convert to PDBQT — native Meeko first,
    # OpenBabel as fallback.
    with tempfile.NamedTemporaryFile(suffix='.sdf', delete=False) as tmp_sdf:
        tmp_sdf_path = tmp_sdf.name
    try:
        writer = Chem.SDWriter(tmp_sdf_path)
        writer.write(mol)
        writer.close()

        if _file_to_pdbqt_native(tmp_sdf_path, out_path, log_fn) and os.path.isfile(out_path):
            return True

        obabel_cmd, obabel_env = _resolve_obabel_spec(use_wsl_backend, wsl_distro)
        if obabel_cmd:
            ok = _run(_with_env_prefix(
                       [obabel_cmd, tmp_sdf_path, '-O', out_path, '--partialcharge', 'gasteiger'],
                       obabel_env,
                       use_wsl_backend=use_wsl_backend,
                      ), 'obabel SDF→PDBQT', log_fn,
                      use_wsl_backend, wsl_distro)
            return ok and os.path.isfile(out_path)
    finally:
        if os.path.isfile(tmp_sdf_path):
            os.unlink(tmp_sdf_path)
    return False


def _file_to_pdbqt_native(in_path: str, out_path: str, log_fn=None) -> bool:
    """Convert a ligand file to PDBQT with Meeko (native, cross-platform)."""
    try:
        from engine.native_prep import meeko_available, native_prepare_ligand
    except Exception:  # noqa: BLE001
        return False
    if not meeko_available():
        return False
    return native_prepare_ligand(in_path, out_path, log_fn or (lambda _m: None))


def _file_to_pdbqt_obabel(in_path: str, out_path: str, gen3d: bool = False,
                            log_fn=None, use_wsl_backend: bool = False,
                            wsl_distro: str = "") -> bool:
    """Convert a ligand file to PDBQT.

    Prefers native Meeko (no external binary, cross-platform); falls back to
    OpenBabel. Kept under the historical name because every file→PDBQT record
    path already routes through here.
    """
    if _file_to_pdbqt_native(in_path, out_path, log_fn) and os.path.isfile(out_path):
        return True
    obabel_cmd, obabel_env = _resolve_obabel_spec(use_wsl_backend, wsl_distro)
    if not obabel_cmd:
        return False
    cmd = _with_env_prefix(
        [obabel_cmd, in_path, '-O', out_path, '--partialcharge', 'gasteiger'],
        obabel_env,
        use_wsl_backend=use_wsl_backend,
    )
    if gen3d:
        cmd.append('--gen3d')
    return _run(
        cmd, f'obabel {os.path.basename(in_path)}→PDBQT', log_fn,
        use_wsl_backend, wsl_distro
    ) and os.path.isfile(out_path)


def _with_env_prefix(
    cmd: list[str],
    env_map: dict[str, str],
    use_wsl_backend: bool = False,
) -> list[str]:
    if not env_map:
        return cmd
    prefixed = ["env"]
    for key, value in env_map.items():
        if value:
            env_value = maybe_to_wsl_path(value) if use_wsl_backend else value
            prefixed.append(f"{key}={env_value}")
    prefixed.extend(cmd)
    return prefixed


def _file_to_pdbqt_mgltools(in_path: str, out_path: str,
                              pythonsh: str, prep_lig: str,
                              log_fn=None, use_wsl_backend: bool = False,
                              wsl_distro: str = "") -> bool:
    """Convert a PDB/SDF ligand to PDBQT via MGLTools prepare_ligand4.py.
    Treats non-zero exit as a warning if the output file was written
    (handles ligands with unusual atoms such as metals or macrocycles)."""
    if not (os.path.isfile(pythonsh) and os.path.isfile(prep_lig)):
        return False
    try:
        exec_cmd, exec_cwd = prepare_subprocess(
            [pythonsh, prep_lig, '-l', in_path, '-o', out_path],
            use_wsl_backend=use_wsl_backend,
            wsl_distro=wsl_distro,
        )
        result = subprocess.run(
            exec_cmd,
            cwd=exec_cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
        combined = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        if log_fn:
            for line in combined.splitlines():
                if line.strip():
                    log_fn(f"    prepare_ligand4.py: {line}")
        if os.path.isfile(out_path):
            if result.returncode != 0 and log_fn:
                log_fn("    ⚠ prepare_ligand4.py exited non-zero but output was written "
                       "— continuing (some atoms may have been skipped).")
            return True
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        if log_fn:
            log_fn(f"    prepare_ligand4.py: {exc}")
        return False


def _find_smiles_and_name_cols(headers: list[str]) -> tuple[int, int]:
    """Return (smiles_col_idx, name_col_idx) from a list of column headers. -1 = not found."""
    lower = [h.lower().strip() for h in headers]
    smiles_idx = -1
    for hint in _SMILES_COLUMN_HINTS:
        if hint in lower:
            smiles_idx = lower.index(hint)
            break
    if smiles_idx == -1:
        # Heuristic: find column whose first value looks like a SMILES string
        smiles_idx = 0  # will be verified on first row

    name_idx = -1
    for hint in _NAME_COLUMN_HINTS:
        if hint in lower:
            name_idx = lower.index(hint)
            break
    return smiles_idx, name_idx


def _looks_like_smiles(s: str) -> bool:
    """Very light check that a string looks like a SMILES."""
    s = s.strip()
    return bool(s) and any(c in s for c in ('C', 'c', 'N', 'n', 'O', 'o',
                                             'S', 'F', 'P', 'B', '[', '(', '='))


def _sdf_titles(path: str) -> list[str]:
    """Extract molecule titles (line 1 of each record) from an SDF file."""
    titles = []
    with open(path, 'r', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if line.startswith('$$$$'):
                pass
            elif not titles or (titles and titles[-1] is None):
                titles.append(line.strip() or None)
            else:
                continue
    # Actually parse properly
    titles = []
    current_title = None
    in_record = False
    with open(path, 'r', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if not in_record:
                current_title = line.strip()
                in_record = True
            elif line.startswith('$$$$'):
                titles.append(current_title or f'mol_{len(titles)+1}')
                in_record = False
    return titles


def _mol2_titles(path: str) -> list[str]:
    """Extract MOLECULE names from a multi-mol MOL2 file."""
    titles = []
    lines = Path(path).read_text(errors='replace').splitlines()
    for i, line in enumerate(lines):
        if line.strip() == '@<TRIPOS>MOLECULE':
            if i + 1 < len(lines):
                titles.append(lines[i + 1].strip() or f'mol_{len(titles)+1}')
    return titles


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def expand_to_pdbqt(
    lig_path: str,
    out_dir: str,
    pythonsh: str = "",
    prep_lig: str = "",
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
    idx_offset: int = 0,
    log_fn=None,
) -> List[Tuple[str, str]]:
    """
    Convert a ligand file (any supported format) to one or more PDBQT files.

    Parameters
    ----------
    lig_path   : source file path
    out_dir    : directory where PDBQT outputs will be written
    pythonsh   : path to MGLTools pythonsh (optional)
    prep_lig   : path to prepare_ligand4.py (optional)
    idx_offset : starting index suffix for output filenames
    log_fn     : callable(str) for progress messages (optional)

    Returns
    -------
    List of (molecule_name, pdbqt_path) tuples.
    Empty list if conversion fails.
    """
    ext = os.path.splitext(lig_path)[1].lower()
    base = os.path.splitext(os.path.basename(lig_path))[0]
    os.makedirs(out_dir, exist_ok=True)

    def _log(msg):
        if log_fn:
            log_fn(msg)

    # ── Already PDBQT ────────────────────────────────────────────────────
    if ext == '.pdbqt':
        out = os.path.join(out_dir, f'{base}.pdbqt')
        shutil.copy2(lig_path, out)
        return [(base, out)]

    # ── PDB ──────────────────────────────────────────────────────────────
    if ext == '.pdb':
        out = os.path.join(out_dir, f'{base}_{idx_offset}.pdbqt')
        if _file_to_pdbqt_mgltools(lig_path, out, pythonsh, prep_lig, _log,
                                   use_wsl_backend, wsl_distro):
            return [(base, out)]
        if _file_to_pdbqt_obabel(lig_path, out, gen3d=False, log_fn=_log,
                                 use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro):
            return [(base, out)]
        _log(f"⚠ Cannot convert {base}.pdb")
        return []

    # ── MOL (single) ─────────────────────────────────────────────────────
    if ext == '.mol':
        out = os.path.join(out_dir, f'{base}_{idx_offset}.pdbqt')
        if _file_to_pdbqt_obabel(lig_path, out, log_fn=_log):
            return [(base, out)]
        _log(f"⚠ Cannot convert {base}.mol")
        return []

    # ── SDF (single or multi) ────────────────────────────────────────────
    if ext == '.sdf':
        return _expand_sdf(lig_path, out_dir, base, idx_offset,
                           pythonsh, prep_lig, _log, use_wsl_backend, wsl_distro)

    # ── MOL2 (single or multi) ───────────────────────────────────────────
    if ext == '.mol2':
        return _expand_mol2(lig_path, out_dir, base, idx_offset, _log,
                            use_wsl_backend, wsl_distro)

    # ── SMILES files (.smi .smiles .txt) ─────────────────────────────────
    if ext in ('.smi', '.smiles', '.txt'):
        return _expand_smiles_file(lig_path, out_dir, base, idx_offset, _log,
                                   use_wsl_backend, wsl_distro)

    # ── CSV / TSV ─────────────────────────────────────────────────────────
    if ext in ('.csv', '.tsv'):
        sep = ',' if ext == '.csv' else '\t'
        return _expand_delimited(lig_path, sep, out_dir, base, idx_offset, _log,
                                 use_wsl_backend, wsl_distro)

    # ── Excel ─────────────────────────────────────────────────────────────
    if ext in ('.xlsx', '.xls'):
        return _expand_excel(lig_path, out_dir, base, idx_offset, _log,
                             use_wsl_backend, wsl_distro)

    _log(f"⚠ Unsupported format: {ext}")
    return []


def iter_smiles_file_to_pdbqt(
    lig_path: str,
    out_dir: str,
    base: str = "",
    idx_offset: int = 0,
    log_fn=None,
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
) -> Iterator[Tuple[str, str, str]]:
    """
    Stream-convert a SMILES text file into individual PDBQT files.

    Yields
    ------
    (molecule_name, pdbqt_path, smiles)
        One tuple per successfully converted molecule.
    """
    base = base or os.path.splitext(os.path.basename(lig_path))[0]
    os.makedirs(out_dir, exist_ok=True)

    def _log(msg):
        if log_fn:
            log_fn(msg)

    try:
        lines = Path(lig_path).read_text(errors='replace').splitlines()
    except OSError as exc:
        _log(f"⚠ Cannot read {lig_path}: {exc}")
        return

    mol_idx = idx_offset
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        lower_line = line.lower()
        if mol_idx == idx_offset and any(lower_line.startswith(f"{hint} ") for hint in _SMILES_COLUMN_HINTS):
            continue
        parts = line.split(None, 1)
        smiles = parts[0]
        name = parts[1].strip() if len(parts) > 1 else f'{base}_{mol_idx}'
        name = re.sub(r'[^\w\-.]', '_', name) or f'{base}_{mol_idx}'
        if not _looks_like_smiles(smiles):
            if len(parts) > 1 and _looks_like_smiles(parts[1]):
                name, smiles = smiles, parts[1].strip()
                name = re.sub(r'[^\w\-.]', '_', name) or f'{base}_{mol_idx}'
            else:
                continue

        out = os.path.join(out_dir, f'{name}_{mol_idx}.pdbqt')
        _log(f"  SMILES→PDBQT: {name} ({smiles[:40]}…)")
        if _smiles_to_pdbqt_rdkit(smiles, out, name, _log, use_wsl_backend, wsl_distro):
            yield (name, out, smiles)
        elif _smiles_to_pdbqt_obabel(smiles, out, name, _log, use_wsl_backend, wsl_distro):
            yield (name, out, smiles)
        else:
            _log(f"  ⚠ Failed to convert SMILES: {smiles[:40]}")
        mol_idx += 1


def iter_delimited_to_pdbqt(
    lig_path: str,
    sep: str,
    out_dir: str,
    base: str = "",
    idx_offset: int = 0,
    log_fn=None,
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
) -> Iterator[Tuple[str, str, str]]:
    """Stream-convert CSV/TSV SMILES rows into PDBQT files."""
    base = base or os.path.splitext(os.path.basename(lig_path))[0]
    os.makedirs(out_dir, exist_ok=True)

    def _log(msg):
        if log_fn:
            log_fn(msg)

    try:
        with open(lig_path, newline='', encoding='utf-8', errors='replace') as fh:
            reader = csv.reader(fh, delimiter=sep)
            headers = next(reader, None)
            if headers is None:
                return
            smiles_idx, name_idx = _find_smiles_and_name_cols(headers)
            mol_idx = idx_offset
            for row in reader:
                if not row or len(row) <= smiles_idx:
                    continue
                smiles = row[smiles_idx].strip()
                if not _looks_like_smiles(smiles):
                    found = False
                    for ci, val in enumerate(row):
                        if _looks_like_smiles(val.strip()):
                            smiles = val.strip()
                            smiles_idx = ci
                            found = True
                            break
                    if not found:
                        continue

                name = (
                    row[name_idx].strip()
                    if name_idx >= 0 and len(row) > name_idx
                    else f'{base}_{mol_idx}'
                )
                name = re.sub(r'[^\w\-.]', '_', name) or f'{base}_{mol_idx}'
                out = os.path.join(out_dir, f'{name}_{mol_idx}.pdbqt')
                _log(f"  CSV row→PDBQT: {name}")
                if _smiles_to_pdbqt_rdkit(smiles, out, name, _log, use_wsl_backend, wsl_distro):
                    yield (name, out, smiles)
                elif _smiles_to_pdbqt_obabel(smiles, out, name, _log, use_wsl_backend, wsl_distro):
                    yield (name, out, smiles)
                else:
                    _log(f"  ⚠ Failed to convert row {mol_idx}: {smiles[:40]}")
                mol_idx += 1
    except Exception as exc:
        _log(f"⚠ CSV parse error: {exc}")


def iter_excel_to_pdbqt(
    lig_path: str,
    out_dir: str,
    base: str = "",
    idx_offset: int = 0,
    log_fn=None,
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
) -> Iterator[Tuple[str, str, str]]:
    """Stream-convert Excel SMILES rows into PDBQT files."""
    base = base or os.path.splitext(os.path.basename(lig_path))[0]
    os.makedirs(out_dir, exist_ok=True)

    def _log(msg):
        if log_fn:
            log_fn(msg)

    try:
        import pandas as pd
    except ImportError:
        _log("⚠ pandas not installed — cannot read Excel files")
        return
    try:
        df = pd.read_excel(lig_path)
    except Exception as exc:
        _log(f"⚠ Cannot read Excel {lig_path}: {exc}")
        return

    headers = list(df.columns)
    smiles_idx, name_idx = _find_smiles_and_name_cols(headers)
    if smiles_idx == -1:
        for ci, col in enumerate(headers):
            sample = df[col].dropna().astype(str).head(3)
            if any(_looks_like_smiles(v) for v in sample):
                smiles_idx = ci
                break
    if smiles_idx == -1:
        _log("⚠ No SMILES column detected in Excel file")
        return

    smiles_col = headers[smiles_idx]
    name_col = headers[name_idx] if name_idx >= 0 else None
    for i, row in df.iterrows():
        smiles = str(row[smiles_col]).strip()
        if not _looks_like_smiles(smiles):
            continue
        name = str(row[name_col]).strip() if name_col else f'{base}_{idx_offset + i}'
        name = re.sub(r'[^\w\-.]', '_', name) or f'{base}_{idx_offset + i}'
        out = os.path.join(out_dir, f'{name}_{idx_offset + i}.pdbqt')
        _log(f"  Excel row→PDBQT: {name}")
        if _smiles_to_pdbqt_rdkit(smiles, out, name, _log, use_wsl_backend, wsl_distro):
            yield (name, out, smiles)
        elif _smiles_to_pdbqt_obabel(smiles, out, name, _log, use_wsl_backend, wsl_distro):
            yield (name, out, smiles)
        else:
            _log(f"  ⚠ Failed: {smiles[:40]}")


def iter_sdf_to_pdbqt(
    lig_path: str,
    out_dir: str,
    base: str = "",
    idx_offset: int = 0,
    pythonsh: str = "",
    prep_lig: str = "",
    log_fn=None,
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
) -> Iterator[Tuple[str, str]]:
    """Stream-convert SDF records into PDBQT files one record at a time."""
    base = base or os.path.splitext(os.path.basename(lig_path))[0]
    os.makedirs(out_dir, exist_ok=True)

    def _log(msg):
        if log_fn:
            log_fn(msg)

    try:
        text = Path(lig_path).read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        _log(f"⚠ Cannot read {lig_path}: {exc}")
        return

    records = [rec for rec in text.split('$$$$') if rec.strip()]
    if len(records) <= 1:
        out = os.path.join(out_dir, f'{base}_{idx_offset}.pdbqt')
        if _file_to_pdbqt_obabel(lig_path, out, log_fn=_log,
                                 use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro):
            yield (base, out)
            return
        if _file_to_pdbqt_mgltools(lig_path, out, pythonsh, prep_lig, _log,
                                   use_wsl_backend, wsl_distro):
            yield (base, out)
        return

    for i, rec in enumerate(records):
        lines = rec.strip('\n').splitlines()
        title = lines[0].strip() if lines else ""
        mol_name = title or f'{base}_{idx_offset + i}'
        mol_name = re.sub(r'[^\w\-.]', '_', mol_name) or f'{base}_{idx_offset + i}'
        tmp_sdf = os.path.join(out_dir, f'__tmp_{mol_name}_{idx_offset + i}.sdf')
        out = os.path.join(out_dir, f'{mol_name}_{idx_offset + i}.pdbqt')
        try:
            Path(tmp_sdf).write_text(rec.strip() + '\n$$$$\n', encoding='utf-8')
            _log(f"  SDF→PDBQT: {mol_name}")
            if _file_to_pdbqt_obabel(tmp_sdf, out, log_fn=_log,
                                     use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro):
                yield (mol_name, out)
            elif _file_to_pdbqt_mgltools(tmp_sdf, out, pythonsh, prep_lig, _log,
                                         use_wsl_backend, wsl_distro):
                yield (mol_name, out)
            else:
                _log(f"  ⚠ Failed to convert SDF record: {mol_name}")
        finally:
            if os.path.isfile(tmp_sdf):
                os.unlink(tmp_sdf)


def iter_mol2_to_pdbqt(
    lig_path: str,
    out_dir: str,
    base: str = "",
    idx_offset: int = 0,
    log_fn=None,
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
) -> Iterator[Tuple[str, str]]:
    """Stream-convert MOL2 records into PDBQT files one record at a time."""
    base = base or os.path.splitext(os.path.basename(lig_path))[0]
    os.makedirs(out_dir, exist_ok=True)

    def _log(msg):
        if log_fn:
            log_fn(msg)

    try:
        text = Path(lig_path).read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        _log(f"⚠ Cannot read {lig_path}: {exc}")
        return

    chunks = text.split('@<TRIPOS>MOLECULE')
    records = []
    for chunk in chunks[1:]:
        rec = '@<TRIPOS>MOLECULE' + chunk
        if rec.strip():
            records.append(rec)

    if len(records) <= 1:
        out = os.path.join(out_dir, f'{base}_{idx_offset}.pdbqt')
        if _file_to_pdbqt_obabel(lig_path, out, log_fn=_log,
                                 use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro):
            yield (base, out)
        return

    for i, rec in enumerate(records):
        lines = rec.splitlines()
        title = lines[1].strip() if len(lines) > 1 else ""
        mol_name = title or f'{base}_{idx_offset + i}'
        mol_name = re.sub(r'[^\w\-.]', '_', mol_name) or f'{base}_{idx_offset + i}'
        tmp_mol2 = os.path.join(out_dir, f'__tmp_{mol_name}_{idx_offset + i}.mol2')
        out = os.path.join(out_dir, f'{mol_name}_{idx_offset + i}.pdbqt')
        try:
            Path(tmp_mol2).write_text(rec, encoding='utf-8')
            _log(f"  MOL2→PDBQT: {mol_name}")
            if _file_to_pdbqt_obabel(tmp_mol2, out, log_fn=_log,
                                     use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro):
                yield (mol_name, out)
            else:
                _log(f"  ⚠ Failed to convert MOL2 record: {mol_name}")
        finally:
            if os.path.isfile(tmp_mol2):
                os.unlink(tmp_mol2)


# ─────────────────────────────────────────────────────────────────────────────
# Format-specific expanders
# ─────────────────────────────────────────────────────────────────────────────

def _expand_sdf(lig_path, out_dir, base, idx_offset, pythonsh, prep_lig, log,
                use_wsl_backend=False, wsl_distro=""):
    """Split a (possibly multi-molecule) SDF into individual PDBQTs."""
    if not _obabel_available(use_wsl_backend, wsl_distro):
        log("⚠ obabel not found — cannot convert SDF")
        return []

    # Check number of molecules
    count = 0
    with open(lig_path, errors='replace') as fh:
        for line in fh:
            if line.startswith('$$$$'):
                count += 1

    if count <= 1:
        # Single molecule
        out = os.path.join(out_dir, f'{base}_{idx_offset}.pdbqt')
        if _file_to_pdbqt_obabel(lig_path, out, log_fn=log,
                                 use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro):
            return [(base, out)]
        # MGLTools fallback
        if _file_to_pdbqt_mgltools(lig_path, out, pythonsh, prep_lig, log,
                                   use_wsl_backend, wsl_distro):
            return [(base, out)]
        return []

    # Multi-molecule: use obabel -m to split
    titles = _sdf_titles(lig_path)
    split_prefix = os.path.join(out_dir, f'{base}_split_')
    ok = _run(['obabel', lig_path, '-O', f'{split_prefix}%.pdbqt', '-m'],
              'obabel SDF split', log, use_wsl_backend, wsl_distro)
    # obabel writes base_split_1.pdbqt, base_split_2.pdbqt, …
    results = []
    for i in range(1, count + 1):
        split_out = f'{split_prefix}{i}.pdbqt'
        if os.path.isfile(split_out):
            mol_name = titles[i - 1] if i - 1 < len(titles) else f'{base}_{idx_offset + i - 1}'
            if not mol_name:
                mol_name = f'{base}_{idx_offset + i - 1}'
            results.append((mol_name, split_out))
        else:
            log(f"⚠ Split output missing: {split_out}")
    if not results:
        # Fallback: try converting whole file as single
        out = os.path.join(out_dir, f'{base}_{idx_offset}.pdbqt')
        if _file_to_pdbqt_obabel(lig_path, out, log_fn=log,
                                 use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro):
            return [(base, out)]
    return results


def _expand_mol2(lig_path, out_dir, base, idx_offset, log,
                 use_wsl_backend=False, wsl_distro=""):
    """Split a (possibly multi-molecule) MOL2 into individual PDBQTs."""
    if not _obabel_available(use_wsl_backend, wsl_distro):
        log("⚠ obabel not found — cannot convert MOL2")
        return []

    titles = _mol2_titles(lig_path)
    n = len(titles)

    if n <= 1:
        out = os.path.join(out_dir, f'{base}_{idx_offset}.pdbqt')
        if _file_to_pdbqt_obabel(lig_path, out, log_fn=log,
                                 use_wsl_backend=use_wsl_backend, wsl_distro=wsl_distro):
            return [(base, out)]
        return []

    split_prefix = os.path.join(out_dir, f'{base}_split_')
    _run(['obabel', lig_path, '-O', f'{split_prefix}%.pdbqt', '-m'],
         'obabel MOL2 split', log, use_wsl_backend, wsl_distro)
    results = []
    for i in range(1, n + 1):
        split_out = f'{split_prefix}{i}.pdbqt'
        if os.path.isfile(split_out):
            mol_name = titles[i - 1] if i - 1 < len(titles) else f'{base}_{idx_offset + i - 1}'
            results.append((mol_name, split_out))
    return results


def _expand_smiles_file(lig_path, out_dir, base, idx_offset, log,
                        use_wsl_backend=False, wsl_distro=""):
    """Parse a SMILES file (one SMILES per line, optional name in 2nd field)."""
    results = []
    try:
        lines = Path(lig_path).read_text(errors='replace').splitlines()
    except OSError as exc:
        log(f"⚠ Cannot read {lig_path}: {exc}")
        return []

    mol_idx = idx_offset
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        lower_line = line.lower()
        if mol_idx == idx_offset and any(lower_line.startswith(f"{hint} ") for hint in _SMILES_COLUMN_HINTS):
            continue
        parts = line.split(None, 1)
        smiles = parts[0]
        name   = parts[1].strip() if len(parts) > 1 else f'{base}_{mol_idx}'
        name   = re.sub(r'[^\w\-.]', '_', name)  # sanitize for filesystem
        if not _looks_like_smiles(smiles):
            # Maybe name is first; swap
            if len(parts) > 1 and _looks_like_smiles(parts[1]):
                name, smiles = smiles, parts[1].strip()
            else:
                continue

        out = os.path.join(out_dir, f'{name}_{mol_idx}.pdbqt')
        log(f"  SMILES→PDBQT: {name} ({smiles[:40]}…)")
        if _smiles_to_pdbqt_rdkit(smiles, out, name, log, use_wsl_backend, wsl_distro):
            results.append((name, out))
        elif _smiles_to_pdbqt_obabel(smiles, out, name, log, use_wsl_backend, wsl_distro):
            results.append((name, out))
        else:
            log(f"  ⚠ Failed to convert SMILES: {smiles[:40]}")
        mol_idx += 1
    return results


def _expand_delimited(lig_path, sep, out_dir, base, idx_offset, log,
                      use_wsl_backend=False, wsl_distro=""):
    """Parse a CSV/TSV file and convert each SMILES row to PDBQT."""
    results = []
    try:
        with open(lig_path, newline='', encoding='utf-8', errors='replace') as fh:
            reader = csv.reader(fh, delimiter=sep)
            headers = next(reader, None)
            if headers is None:
                return []
            smiles_idx, name_idx = _find_smiles_and_name_cols(headers)
            mol_idx = idx_offset
            for row in reader:
                if not row or len(row) <= smiles_idx:
                    continue
                smiles = row[smiles_idx].strip()
                if not _looks_like_smiles(smiles):
                    # Auto-detect: scan all columns
                    found = False
                    for ci, val in enumerate(row):
                        if _looks_like_smiles(val.strip()):
                            smiles = val.strip()
                            smiles_idx = ci
                            found = True
                            break
                    if not found:
                        continue

                name = (row[name_idx].strip()
                        if name_idx >= 0 and len(row) > name_idx
                        else f'{base}_{mol_idx}')
                name = re.sub(r'[^\w\-.]', '_', name) or f'{base}_{mol_idx}'

                out = os.path.join(out_dir, f'{name}_{mol_idx}.pdbqt')
                log(f"  CSV row→PDBQT: {name}")
                if _smiles_to_pdbqt_rdkit(smiles, out, name, log, use_wsl_backend, wsl_distro):
                    results.append((name, out))
                elif _smiles_to_pdbqt_obabel(smiles, out, name, log, use_wsl_backend, wsl_distro):
                    results.append((name, out))
                else:
                    log(f"  ⚠ Failed to convert row {mol_idx}: {smiles[:40]}")
                mol_idx += 1
    except Exception as exc:
        log(f"⚠ CSV parse error: {exc}")
    return results


def _expand_excel(lig_path, out_dir, base, idx_offset, log,
                  use_wsl_backend=False, wsl_distro=""):
    """Parse an Excel file and convert each SMILES row to PDBQT."""
    try:
        import pandas as pd
    except ImportError:
        log("⚠ pandas not installed — cannot read Excel files")
        return []
    try:
        df = pd.read_excel(lig_path)
    except Exception as exc:
        log(f"⚠ Cannot read Excel {lig_path}: {exc}")
        return []

    headers = list(df.columns)
    smiles_idx, name_idx = _find_smiles_and_name_cols(headers)
    if smiles_idx == -1:
        # Auto-detect: find column whose first non-null value looks like SMILES
        for ci, col in enumerate(headers):
            sample = df[col].dropna().astype(str).head(3)
            if any(_looks_like_smiles(v) for v in sample):
                smiles_idx = ci
                break

    if smiles_idx == -1:
        log("⚠ No SMILES column detected in Excel file")
        return []

    smiles_col = headers[smiles_idx]
    name_col   = headers[name_idx] if name_idx >= 0 else None

    results = []
    for i, row in df.iterrows():
        smiles = str(row[smiles_col]).strip()
        if not _looks_like_smiles(smiles):
            continue
        name = str(row[name_col]).strip() if name_col else f'{base}_{idx_offset + i}'
        name = re.sub(r'[^\w\-.]', '_', name) or f'{base}_{idx_offset + i}'
        out  = os.path.join(out_dir, f'{name}_{idx_offset + i}.pdbqt')
        log(f"  Excel row→PDBQT: {name}")
        if _smiles_to_pdbqt_rdkit(smiles, out, name, log, use_wsl_backend, wsl_distro):
            results.append((name, out))
        elif _smiles_to_pdbqt_obabel(smiles, out, name, log, use_wsl_backend, wsl_distro):
            results.append((name, out))
        else:
            log(f"  ⚠ Failed: {smiles[:40]}")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Molecule count helper (for UI display)
# ─────────────────────────────────────────────────────────────────────────────

def count_molecules(path: str) -> int:
    """
    Fast estimate of molecule count in a file without full conversion.
    Returns 1 for single-molecule formats or when count cannot be determined.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == '.sdf':
            return max(1, Path(path).read_text(errors='replace').count('$$$$'))
        if ext == '.mol2':
            return max(1, Path(path).read_text(errors='replace').count('@<TRIPOS>MOLECULE'))
        if ext in ('.smi', '.smiles', '.txt'):
            lines = [l.strip() for l in Path(path).read_text(errors='replace').splitlines()
                     if l.strip() and not l.startswith('#')]
            return len(lines)
        if ext in ('.csv', '.tsv'):
            sep = ',' if ext == '.csv' else '\t'
            with open(path, newline='', encoding='utf-8', errors='replace') as fh:
                return max(0, sum(1 for _ in csv.reader(fh, delimiter=sep)) - 1)
        if ext in ('.xlsx', '.xls'):
            try:
                import pandas as pd
                return len(pd.read_excel(path))
            except Exception:
                return 1
    except Exception:
        pass
    return 1
