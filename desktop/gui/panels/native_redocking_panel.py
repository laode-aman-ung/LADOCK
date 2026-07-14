"""
LADOCK — Native Ligand Redocking Panel
========================================
Panel untuk native ligand redocking dari file receptor_ready/.

Alur:
  1. Pilih file PDB dari receptor_ready/
  2. Parse komponen (chain/resname) → tampilkan di tabel
  3. User centang: Include as Receptor | Mark as Native Ligand
  4. Set parameter docking (center, box size, scoring, dll.)
  5. Run docking → Result tabel semua pose
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import datetime
import uuid
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QListWidget,
    QListWidgetItem, QGroupBox, QRadioButton, QButtonGroup,
    QDoubleSpinBox, QSpinBox, QComboBox, QTextEdit,
    QSplitter, QFrame, QCheckBox, QProgressBar,
    QMessageBox, QFileDialog, QAbstractItemView, QSizePolicy, QAbstractSpinBox,
    QStackedWidget, QLineEdit
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, Slot, QFileSystemWatcher, QSettings
from PySide6.QtGui import QColor, QFont, QBrush, QDesktopServices
from PySide6.QtCore import QUrl

from gui.widgets.common import SectionLabel, HDivider, StatusBadge, apply_docking_tooltips
from gui import theme
from core.tool_paths import (
    resolve_adfrsuite_dir,
    resolve_mgltools_dir,
    resolve_tool_path,
)
from core.ligand_smiles import smiles_from_ccd, smiles_from_structure
from core.wsl_backend import prepare_subprocess, wsl_available
from engine.native_prep import (
    meeko_available,
    native_prepare_ligand,
    native_prepare_receptor,
)


# Residue/element classification sets — imported from the engine so the Keep
# table (parse_pdb_components) and the component filter (pdb_filter_components)
# always agree on what each atom is.
from engine.mol_prep import (
    STANDARD_AA    as _STANDARD_AA,
    WATER_RESNAMES as _WATER_RES,
    METAL_ELEMENTS as _METAL_ELEM,
)
_TYPE_COLOR  = {
    'Protein':  theme.ROLE_PROTEIN,
    'Ligand':   theme.ROLE_LIGAND,
    'Metal Ion':theme.ROLE_METAL,
    'Water':    theme.ROLE_WATER,
    'Other':    theme.ROLE_OTHER,
}


# ═══════════════════════════════════════════════════════════════════════════ #
# UI helpers
# ═══════════════════════════════════════════════════════════════════════════ #

def _centered_widget(widget) -> QWidget:
    """Wrap a widget in a centered container for QTableWidget cells."""
    container = QWidget()
    lay = QHBoxLayout(container)
    lay.addWidget(widget)
    lay.setAlignment(Qt.AlignCenter)
    lay.setContentsMargins(0, 0, 0, 0)
    return container

def _inner_widget(container):
    """Return the first child widget of a _centered_widget container."""
    if container is None:
        return None
    lay = container.layout()
    if lay and lay.count():
        item = lay.itemAt(0)
        return item.widget() if item else None
    return None

# ═══════════════════════════════════════════════════════════════════════════ #
# PDB Component Parser
# ═══════════════════════════════════════════════════════════════════════════ #

def _widget_value(w):
    """Read a value from a param widget (for per-receptor config snapshots)."""
    if isinstance(w, (QSpinBox, QDoubleSpinBox)):
        return w.value()
    if isinstance(w, QCheckBox):
        return w.isChecked()
    if isinstance(w, QComboBox):
        return w.currentText()
    if isinstance(w, QLineEdit):
        return w.text()
    return None


def _set_widget_value(w, val):
    """Write a value back into a param widget."""
    if val is None:
        return
    if isinstance(w, (QSpinBox, QDoubleSpinBox)):
        w.setValue(val)
    elif isinstance(w, QCheckBox):
        w.setChecked(bool(val))
    elif isinstance(w, QComboBox):
        i = w.findText(str(val))
        if i >= 0:
            w.setCurrentIndex(i)
    elif isinstance(w, QLineEdit):
        w.setText(str(val))


def parse_pdb_components(pdb_path: str) -> list[dict]:
    """
    Parse a PDB file into high-level molecular components:
      - One row per protein chain  (ATOM, standard AA)
      - One row per ligand MOLECULE  (HETATM, non-water, non-metal) keyed by chain+resname+resseq
      - One combined row for all Metal ions
      - One combined row for all Water (HOH)
      - One combined row for Other HETATM (if any)
    Each dict: chain, resname, resseq, type, n_residues, n_atoms
    """
    chains:  dict[str, dict]         = {}  # chain_id -> protein row
    ligands: dict[tuple, dict]       = {}  # (chain, resname, resseq) -> ligand row
    metals  = {'chain': '-', 'resname': 'Metals',  'type': 'Metal Ion',
               'resnames': set(), 'n_residues': 0, 'n_atoms': 0}
    waters  = {'chain': '-', 'resname': 'HOH',     'type': 'Water',
               'n_residues': 0, 'n_atoms': 0}
    others  = {'chain': '-', 'resname': 'Others',  'type': 'Other',
               'resnames': set(), 'n_residues': 0, 'n_atoms': 0}

    with open(pdb_path, 'r', errors='replace') as fh:
        for line in fh:
            rec = line[:6].strip()
            if rec not in ('ATOM', 'HETATM'):
                continue
            resname = line[17:20].strip()
            chain   = line[21].strip() or '?'
            resseq  = line[22:26].strip()
            elem    = line[76:78].strip().upper() if len(line) > 76 else ''

            # ── Protein chain (ATOM + standard AA) ──────────────────────
            if rec == 'ATOM' and resname in _STANDARD_AA:
                if chain not in chains:
                    chains[chain] = {
                        'chain': chain, 'resname': f'Chain {chain}', 'resseq': '',
                        'type': 'Protein', 'resseqs': set(), 'n_atoms': 0,
                    }
                chains[chain]['resseqs'].add(resseq)
                chains[chain]['n_atoms'] += 1
                continue

            # ── Water ────────────────────────────────────────────────────
            if resname in _WATER_RES:
                waters['n_residues'] += 1
                waters['n_atoms']    += 1
                continue

            # ── Metal ion ────────────────────────────────────────────────
            if rec == 'HETATM' and (elem in _METAL_ELEM or resname in _METAL_ELEM):
                metals['resnames'].add(resname)
                metals['n_residues'] += 1
                metals['n_atoms']    += 1
                continue

            # ── Ligand — one row per molecule (chain + resname + resseq) ─
            if rec == 'HETATM':
                key = (chain, resname, resseq)
                if key not in ligands:
                    ligands[key] = {
                        'chain': chain, 'resname': resname, 'resseq': resseq,
                        'type': 'Ligand', 'n_residues': 1, 'n_atoms': 0,
                    }
                ligands[key]['n_atoms'] += 1
                continue

            # ── Anything else ────────────────────────────────────────────
            others['resnames'].add(resname)
            others['n_residues'] += 1
            others['n_atoms']    += 1

    result = []

    # Protein chains
    for info in sorted(chains.values(), key=lambda x: x['chain']):
        info['n_residues'] = len(info.pop('resseqs'))
        result.append(info)

    # Ligands — sorted by chain, resname, then resseq numerically
    for info in sorted(ligands.values(),
                       key=lambda x: (x['chain'], x['resname'],
                                      int(x['resseq']) if x['resseq'].lstrip('-').isdigit() else 0)):
        result.append(info)

    # Metal (combined)
    if metals['n_atoms'] > 0:
        metals['resname'] = ', '.join(sorted(metals.pop('resnames'))) or 'Metal'
        metals['resseq'] = ''
        result.append(metals)

    # Water (combined)
    if waters['n_atoms'] > 0:
        waters['resseq'] = ''
        result.append(waters)

    # Others (combined)
    if others['n_atoms'] > 0:
        others['resname'] = ', '.join(sorted(others.pop('resnames'))) or 'Others'
        others['resseq'] = ''
        result.append(others)

    return result


def extract_pdb_component(pdb_path: str, chains: list[str],
                           resnames: list[str] | None = None,
                           components: list[dict] | None = None) -> str:
    """
    Extract atoms from a PDB file matching the requested components.

    Two modes:
      1. Simple (backward-compat): chains + optional resnames filter.
      2. Component-aware: when `components` is provided, each dict is:
         {chain, resname, resseq, type}
         - Protein components → include all ATOM records for that chain
           but EXCLUDE hetero residues not in the component list
         - Ligand components  → include HETATM matching chain+resname+resseq
         - Metal/Water/Other  → included only if explicitly in components list
    """
    if components is None:
        # Legacy mode
        chains_set   = set(chains)
        resnames_set = set(resnames) if resnames else None
        out = []
        with open(pdb_path, 'r', errors='replace') as fh:
            for line in fh:
                rec = line[:6].strip()
                if rec in ('ATOM', 'HETATM'):
                    chain   = line[21].strip() or '?'
                    resname = line[17:20].strip()
                    if chain not in chains_set:
                        continue
                    if resnames_set and resname not in resnames_set:
                        continue
                out.append(line)
        return ''.join(out)

    # Component-aware mode
    # Build lookup sets
    protein_chains: set[str] = set()          # chains for protein (ATOM) records
    hetatm_keys: set[tuple] = set()           # (chain, resname, resseq) for HETATM
    include_metals = False
    include_waters = False
    include_others = False

    for comp in components:
        ctype = comp.get('type', '')
        if ctype == 'Protein':
            protein_chains.add(comp['chain'])
        elif ctype == 'Ligand':
            hetatm_keys.add((comp['chain'], comp['resname'], comp.get('resseq', '')))
        elif ctype == 'Metal Ion':
            include_metals = True
        elif ctype == 'Water':
            include_waters = True
        elif ctype == 'Other':
            include_others = True

    out = []
    with open(pdb_path, 'r', errors='replace') as fh:
        for line in fh:
            rec = line[:6].strip()
            if rec == 'ATOM':
                chain = line[21].strip() or '?'
                if chain in protein_chains:
                    out.append(line)
            elif rec == 'HETATM':
                chain   = line[21].strip() or '?'
                resname = line[17:20].strip()
                resseq  = line[22:26].strip()
                elem    = line[76:78].strip().upper() if len(line) > 76 else ''
                is_water = resname in _WATER_RES
                is_metal = elem in _METAL_ELEM or resname in _METAL_ELEM
                if is_water:
                    if include_waters:
                        out.append(line)
                elif is_metal:
                    if include_metals:
                        out.append(line)
                elif (chain, resname, resseq) in hetatm_keys:
                    out.append(line)
                elif include_others:
                    out.append(line)
            else:
                out.append(line)
    return ''.join(out)


def sanitize_pdb_text_for_mgltools(pdb_text: str) -> str:
    """
    Normalize ATOM/HETATM records before feeding them to MGLTools.

    Why:
    - alternate-location markers can be carried into atom names by old
      MGLTools scripts and produce malformed PDBQT output
    - insertion codes are not needed for docking prep temp files here

    Strategy:
    - keep only one altLoc per atom site, preferring blank > A > 1 > first seen
    - blank out altLoc and iCode columns on kept records
    - leave all non-coordinate records untouched
    """
    lines = pdb_text.splitlines(keepends=True)
    grouped: dict[tuple, list[str]] = {}
    passthrough: list[str] = []

    for line in lines:
        rec = line[:6].strip()
        if rec not in ('ATOM', 'HETATM') or len(line) < 54:
            passthrough.append(line)
            continue
        key = (
            rec,
            line[12:16],
            line[17:20],
            line[21],
            line[22:26],
        )
        grouped.setdefault(key, []).append(line)

    def _alt_rank(line: str) -> tuple[int, str]:
        alt = line[16:17]
        if alt == ' ':
            return (0, alt)
        if alt == 'A':
            return (1, alt)
        if alt == '1':
            return (2, alt)
        return (3, alt)

    sanitized_atoms: list[str] = []
    seen_keys: set[tuple] = set()
    for line in lines:
        rec = line[:6].strip()
        if rec not in ('ATOM', 'HETATM') or len(line) < 54:
            continue
        key = (
            rec,
            line[12:16],
            line[17:20],
            line[21],
            line[22:26],
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        chosen = min(grouped[key], key=_alt_rank)
        buf = list(chosen.rstrip('\n').ljust(80))
        buf[16] = ' '
        buf[26] = ' '
        sanitized_atoms.append(''.join(buf).rstrip() + '\n')

    return ''.join(sanitized_atoms + [line for line in passthrough if line[:6].strip() not in ('ATOM', 'HETATM')])


def compute_ligand_center(pdb_path: str, resname: str,
                          chain: str | None = None,
                          resseq: str | None = None) -> tuple[float, float, float]:
    """Compute the geometric center (centroid) of a specific ligand residue."""
    xs, ys, zs = [], [], []
    with open(pdb_path, 'r', errors='replace') as fh:
        for line in fh:
            if line[:6].strip() not in ('ATOM', 'HETATM'):
                continue
            if line[17:20].strip() != resname:
                continue
            if chain and line[21].strip() != chain.strip():
                continue
            if resseq and line[22:26].strip() != resseq.strip():
                continue
            try:
                xs.append(float(line[30:38]))
                ys.append(float(line[38:46]))
                zs.append(float(line[46:54]))
            except ValueError:
                pass
    if not xs:
        return 0.0, 0.0, 0.0
    return (round(sum(xs)/len(xs), 3),
            round(sum(ys)/len(ys), 3),
            round(sum(zs)/len(zs), 3))


def find_flex_residues(pdb_path: str,
                       cx: float, cy: float, cz: float,
                       cutoff: float = 3.0) -> list[str]:
    """
    Find protein residues (ATOM records, standard AA) whose any heavy atom
    is within `cutoff` Å of the box center (cx, cy, cz).

    Returns list of residue identifiers in prepare_flexreceptor4.py format:
        ["chain:resname:resseq", ...]   →  joined as "A:LYS:123_A:ASP:89"
    """
    seen: dict[tuple, bool] = {}   # (chain, resname, resseq) → within cutoff?
    cutoff2 = cutoff * cutoff
    with open(pdb_path, 'r', errors='replace') as fh:
        for line in fh:
            if line[:6].strip() != 'ATOM':
                continue
            resname = line[17:20].strip()
            if resname not in _STANDARD_AA:
                continue
            chain  = line[21].strip()
            resseq = line[22:26].strip()
            key    = (chain, resname, resseq)
            if key in seen:
                continue
            try:
                ax = float(line[30:38])
                ay = float(line[38:46])
                az = float(line[46:54])
            except ValueError:
                continue
            d2 = (ax-cx)**2 + (ay-cy)**2 + (az-cz)**2
            if d2 <= cutoff2:
                seen[key] = True

    return [f"{ch}:{rn}:{rs}" for (ch, rn, rs) in seen]


# ═══════════════════════════════════════════════════════════════════════════ #
# Docking Worker
# ═══════════════════════════════════════════════════════════════════════════ #

class _DockingWorker(QObject):
    log      = Signal(str)
    progress = Signal(str)           # short stage description
    finished = Signal(object)        # list[tuple[str, str]]  → [(sf, out_path), …]
    error    = Signal(str)

    def __init__(self, params: dict):
        super().__init__()
        self._params = params
        self._use_wsl_backend = bool(params.get('use_wsl_backend'))
        self._wsl_distro = str(params.get('wsl_distro', '')).strip()

    # ------------------------------------------------------------------ #
    def _run_cmd(self, cmd: list, tag: str, cwd: str = None):
        """Stream subprocess to log; raise RuntimeError on non-zero exit."""
        exec_cmd, exec_cwd = prepare_subprocess(
            cmd, cwd=cwd,
            use_wsl_backend=self._use_wsl_backend,
            wsl_distro=self._wsl_distro,
        )
        self.log.emit(f"\n▶ {tag}\n  $ {' '.join(str(c) for c in exec_cmd)}")
        try:
            proc = subprocess.Popen(
                exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=exec_cwd)
            for line in proc.stdout:
                self.log.emit(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"{tag} failed (exit code {proc.returncode})")
        except FileNotFoundError:
            raise RuntimeError(f"Executable not found: {exec_cmd[0]}")

    def _run_cmd_warn(self, cmd: list, tag: str, out_file: str, cwd: str = None):
        """Like _run_cmd but treats non-zero exit as a warning if out_file was produced."""
        exec_cmd, exec_cwd = prepare_subprocess(
            cmd, cwd=cwd,
            use_wsl_backend=self._use_wsl_backend,
            wsl_distro=self._wsl_distro,
        )
        self.log.emit(f"\n▶ {tag}\n  $ {' '.join(str(c) for c in exec_cmd)}")
        try:
            proc = subprocess.Popen(
                exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=exec_cwd)
            for line in proc.stdout:
                self.log.emit(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                if os.path.isfile(out_file):
                    self.log.emit(
                        f"⚠ {tag} exited {proc.returncode} but output was written "
                        f"— continuing (non-standard atoms may be skipped).")
                else:
                    raise RuntimeError(f"{tag} failed (exit code {proc.returncode})")
        except FileNotFoundError:
            raise RuntimeError(f"Executable not found: {exec_cmd[0]}")

    def _lig_fallback_obabel(self, src_pdb: str, out_pdbqt: str, log_fn) -> bool:
        """Convert ligand PDB → PDBQT via obabel.  Returns True on success."""
        try:
            import subprocess as sp
            log_fn("  ↩ Trying obabel fallback for ligand conversion…")
            cmd = ["obabel", src_pdb, "-O", out_pdbqt, "--gen3d", "-p", "7.4"]
            exec_cmd, exec_cwd = prepare_subprocess(
                cmd,
                use_wsl_backend=self._use_wsl_backend,
                wsl_distro=self._wsl_distro,
            )
            log_fn(f"  $ {' '.join(exec_cmd)}")
            r = sp.run(exec_cmd, capture_output=True, text=True, cwd=exec_cwd)
            if r.stdout: log_fn(r.stdout.strip())
            if r.stderr: log_fn(r.stderr.strip())
            if r.returncode == 0 and os.path.isfile(out_pdbqt):
                log_fn("  ✅ obabel conversion succeeded.")
                return True
            # try without --gen3d
            cmd2 = ["obabel", src_pdb, "-O", out_pdbqt]
            exec_cmd2, exec_cwd2 = prepare_subprocess(
                cmd2,
                use_wsl_backend=self._use_wsl_backend,
                wsl_distro=self._wsl_distro,
            )
            log_fn(f"  $ {' '.join(exec_cmd2)}")
            r2 = sp.run(exec_cmd2, capture_output=True, text=True, cwd=exec_cwd2)
            if r2.stdout: log_fn(r2.stdout.strip())
            if r2.stderr: log_fn(r2.stderr.strip())
            return r2.returncode == 0 and os.path.isfile(out_pdbqt)
        except Exception as e:
            log_fn(f"  obabel fallback failed: {e}")
            return False

    # ── Sub-pipelines -------------------------------------------------- #
    def _prep_pdbqt(self, p: dict, tmp: str) -> tuple[str, str, str | None, str | None]:
        """
        Convert receptor+ligand PDB → PDBQT.
        If flexible mode: also split receptor into rigid.pdbqt + flex.pdbqt.
        Returns (rec_pdbqt_or_rigid, lig_pdbqt, flex_pdbqt_or_None, rigid_pdbqt_or_None)
        """
        pythonsh  = p['pythonsh']
        prep_rec  = p['prepare_receptor']
        prep_lig  = p['prepare_ligand']
        prep_flex = p['prepare_flexreceptor']
        rec_pdbqt = os.path.join(tmp, 'receptor.pdbqt')
        lig_pdbqt = os.path.join(tmp, 'ligand.pdbqt')

        have_mgltools = bool(pythonsh) and os.path.isfile(pythonsh)

        # ── Receptor: native Meeko first, MGLTools as fallback ──────────
        self.progress.emit("Preparing receptor PDBQT…")
        rec_done = False
        if meeko_available():
            self.log.emit("  Preparing receptor with Meeko (native)…")
            rec_done = native_prepare_receptor(
                p['receptor_pdb'], rec_pdbqt, self.log.emit)
            if not rec_done:
                self.log.emit("  ⚠ Native receptor prep failed — trying MGLTools…")
        if not rec_done and have_mgltools:
            self._run_cmd_warn(
                [pythonsh, prep_rec, '-r', p['receptor_pdb'], '-o', rec_pdbqt,
                 '-A', 'hydrogens', '-U', 'nphs_lps'],
                "prepare_receptor4.py", rec_pdbqt)
        if not os.path.isfile(rec_pdbqt):
            raise RuntimeError(
                f"Receptor PDBQT preparation failed: {rec_pdbqt}\n"
                "Ensure Meeko is installed (native prep) or MGLTools is "
                "configured, and that the receptor PDB is clean (ATOM records).")

        # ── Ligand: native Meeko first, MGLTools then obabel as fallback ─
        self.progress.emit("Preparing ligand PDBQT…")
        lig_done = False
        if meeko_available():
            self.log.emit("  Preparing ligand with Meeko (native)…")
            lig_done = native_prepare_ligand(
                p['ligand_pdb'], lig_pdbqt, self.log.emit)
            if not lig_done:
                self.log.emit("  ⚠ Native ligand prep failed — trying MGLTools/obabel…")
        if not lig_done and have_mgltools:
            try:
                self._run_cmd_warn(
                    [pythonsh, prep_lig, '-l', p['ligand_pdb'], '-o', lig_pdbqt],
                    "prepare_ligand4.py", lig_pdbqt)
                lig_done = os.path.isfile(lig_pdbqt)
            except RuntimeError as e:
                self.log.emit(f"⚠ prepare_ligand4.py error: {e}")
        if not lig_done or not os.path.isfile(lig_pdbqt):
            ok = self._lig_fallback_obabel(
                p['ligand_pdb'], lig_pdbqt, self.log.emit)
            if not ok:
                raise RuntimeError(
                    "Ligand PDBQT preparation failed.\n"
                    "Meeko, MGLTools and obabel all failed.\n"
                    "Check that the ligand has valid atoms and known "
                    "element types.")

        # ── Flexible receptor split ────────────────────────────────────
        flex_residues = p.get('flex_residues_list', [])   # list of "chain:res:seq"
        if flex_residues and 'flexible' in p.get('listmode', []):
            flex_spec  = '_'.join(flex_residues)
            rigid_pdbqt = os.path.join(tmp, 'rigid.pdbqt')
            flex_pdbqt  = os.path.join(tmp, 'flex.pdbqt')
            self.progress.emit(
                f"Splitting flexible residues ({len(flex_residues)})…")
            self.log.emit(
                f"  Flex residues: {flex_spec}")
            self._run_cmd([
                pythonsh, prep_flex,
                '-r', rec_pdbqt,
                '-s', flex_spec,
                '-g', rigid_pdbqt,
                '-x', flex_pdbqt,
            ], "prepare_flexreceptor4.py")
            if not os.path.isfile(rigid_pdbqt) or not os.path.isfile(flex_pdbqt):
                raise RuntimeError(
                    "prepare_flexreceptor4.py did not produce rigid/flex PDBQT.\n"
                    f"Residue spec used: {flex_spec}")
            return rec_pdbqt, lig_pdbqt, flex_pdbqt, rigid_pdbqt

        return rec_pdbqt, lig_pdbqt, None, None

    def _run_vina_sf(self, p: dict, tmp: str,
                     rec_pdbqt: str, lig_pdbqt: str, sf: str,
                     rigid_pdbqt: str | None = None,
                     flex_pdbqt: str | None = None) -> tuple[str, str] | None:
        """Run AutoDock Vina with given scoring function. Returns (sf, out_pdbqt)."""
        out_pdbqt = os.path.join(tmp, f'out_{sf}.pdbqt')
        is_flex   = flex_pdbqt and rigid_pdbqt
        self.progress.emit(
            f"Docking — Vina scoring={sf} "
            f"({'flexible' if is_flex else 'rigid'})…")
        cmd = [
            p['vina_path'],
            '--receptor',   rigid_pdbqt if is_flex else rec_pdbqt,
            '--ligand',     lig_pdbqt,
            '--scoring',    sf,
            '--center_x',   str(p['cx']),
            '--center_y',   str(p['cy']),
            '--center_z',   str(p['cz']),
            '--size_x',     str(p['sx']),
            '--size_y',     str(p['sy']),
            '--size_z',     str(p['sz']),
            '--exhaustiveness', str(p.get('exhaustiveness', 8)),
            '--num_modes',      str(p.get('n_poses', 9)),
            '--energy_range',   str(p.get('energy_range', 3)),
            '--cpu',            str(p.get('cpu', 4)),
            '--out',            out_pdbqt,
        ]
        seed = p.get('seed', 0)
        if seed:
            cmd += ['--seed', str(seed)]
        if is_flex:
            cmd += ['--flex', flex_pdbqt]
        self._run_cmd(cmd, f"AutoDock Vina ({sf})")
        return (sf, out_pdbqt) if os.path.isfile(out_pdbqt) else None

    def _build_ad4_grids(self, p: dict, tmp: str,
                         rec_pdbqt: str, lig_pdbqt: str,
                         flex_pdbqt: str | None = None) -> str:
        """
        Prepare GPF and run AutoGrid4 to generate grid maps.
        Returns path to the .fld file (used by AD-GPU) and .gpf (used by AD4).
        If flex_pdbqt is given, adds '-x flex.pdbqt' to prepare_gpf4 call.
        """
        pythonsh = p['pythonsh']
        prep_gpf = p['prepare_gpf']
        ag4      = p['ag4_path']
        gpf_path = os.path.join(tmp, 'grid.gpf')
        glg_path = os.path.join(tmp, 'grid.glg')
        local_rec = os.path.join(tmp, os.path.basename(rec_pdbqt))
        local_lig = os.path.join(tmp, os.path.basename(lig_pdbqt))
        if os.path.abspath(rec_pdbqt) != os.path.abspath(local_rec):
            shutil.copy2(rec_pdbqt, local_rec)
        if os.path.abspath(lig_pdbqt) != os.path.abspath(local_lig):
            shutil.copy2(lig_pdbqt, local_lig)
        local_flex = None
        if flex_pdbqt:
            local_flex = os.path.join(tmp, os.path.basename(flex_pdbqt))
            if os.path.abspath(flex_pdbqt) != os.path.abspath(local_flex):
                shutil.copy2(flex_pdbqt, local_flex)

        # npts derived from box size and spacing
        spacing = p.get('spacing', 0.375)
        nx = max(2, round(p['sx'] / spacing))
        ny = max(2, round(p['sy'] / spacing))
        nz = max(2, round(p['sz'] / spacing))
        # AutoGrid4 requires even npts
        nx += nx % 2; ny += ny % 2; nz += nz % 2

        self.progress.emit("Generating AutoGrid4 GPF…")
        cmd = [
            pythonsh, prep_gpf,
            '-r', local_rec,
            '-l', local_lig,
            '-o', gpf_path,
            '-p', f'npts={nx},{ny},{nz}',
            '-p', f'spacing={spacing}',
            '-p', f'gridcenter={p["cx"]},{p["cy"]},{p["cz"]}',
        ]
        if local_flex:
            cmd += ['-x', local_flex]
        self._run_cmd(cmd, "prepare_gpf4.py")
        if not os.path.isfile(gpf_path):
            raise RuntimeError(f"prepare_gpf4.py did not produce: {gpf_path}")

        self.progress.emit("Running AutoGrid4…")
        self._run_cmd(
            [ag4, '-p', gpf_path, '-l', glg_path],
            "autogrid4", cwd=tmp)

        # Locate the .fld file produced by autogrid4
        fld_files = [f for f in os.listdir(tmp) if f.endswith('.maps.fld')]
        if not fld_files:
            raise RuntimeError(
                "AutoGrid4 did not produce a .maps.fld file.\n"
                f"Check grid log: {glg_path}")
        fld_path = os.path.join(tmp, fld_files[0])
        self.log.emit(f"  Grid maps ready: {fld_path}")
        return fld_path, gpf_path

    def _run_ad4(self, p: dict, tmp: str,
                 rec_pdbqt: str, lig_pdbqt: str, gpf_path: str,
                 flex_pdbqt: str | None = None) -> tuple[str, str]:
        """Run AutoDock4. Returns (sf, dlg_path)."""
        pythonsh  = p['pythonsh']
        prep_dpf  = p['prepare_dpf']
        ad4       = p['ad4_path']
        dpf_path  = os.path.join(tmp, 'dock.dpf')
        dlg_path  = os.path.join(tmp, 'dock_ad4.dlg')
        local_rec = os.path.join(tmp, os.path.basename(rec_pdbqt))
        local_lig = os.path.join(tmp, os.path.basename(lig_pdbqt))
        if os.path.abspath(rec_pdbqt) != os.path.abspath(local_rec):
            shutil.copy2(rec_pdbqt, local_rec)
        if os.path.abspath(lig_pdbqt) != os.path.abspath(local_lig):
            shutil.copy2(lig_pdbqt, local_lig)
        local_flex = None
        if flex_pdbqt:
            local_flex = os.path.join(tmp, os.path.basename(flex_pdbqt))
            if os.path.abspath(flex_pdbqt) != os.path.abspath(local_flex):
                shutil.copy2(flex_pdbqt, local_flex)

        self.progress.emit("Generating AutoDock4 DPF…")
        cmd = [
            pythonsh, prep_dpf,
            '-r', local_rec,
            '-l', local_lig,
            '-o', dpf_path,
            '-p', f'ga_num_evals={p.get("ad4_exhaustiveness", p.get("exhaustiveness", 8)) * 250000}',
            '-p', f'ga_run={p.get("n_poses", 9)}',
            '-p', f'ga_pop_size={p.get("ga_pop_size", 150)}',
            '-p', f'rmstol={p.get("cluster_rmsd", 2.0)}',
        ]
        if local_flex:
            cmd += ['-x', local_flex]
        self._run_cmd(cmd, "prepare_dpf42.py")
        if not os.path.isfile(dpf_path):
            raise RuntimeError(f"prepare_dpf42.py did not produce: {dpf_path}")

        self.progress.emit("Running AutoDock4…")
        self._run_cmd(
            [ad4, '-p', dpf_path, '-l', dlg_path],
            "autodock4", cwd=tmp)
        if not os.path.isfile(dlg_path):
            raise RuntimeError(f"AutoDock4 did not produce: {dlg_path}")
        return ('ad4', dlg_path)

    def _run_adgpu(self, p: dict, tmp: str,
                   lig_pdbqt: str, fld_path: str,
                   flex_pdbqt: str | None = None) -> tuple[str, str]:
        """Run AutoDock-GPU. Returns (sf, dlg_path)."""
        adgpu    = p['autodockgpu']
        dlg_base = os.path.join(tmp, 'dock_adgpu')

        self.progress.emit("Running AutoDock-GPU…")
        cmd = [
            adgpu,
            '--lfile', lig_pdbqt,
            '--ffile', fld_path,
            '--resnam', dlg_base,
            '--nrun',   str(p.get('n_poses', 9)),
            '--nev',    str(p.get('ad4_exhaustiveness', p.get('exhaustiveness', 8)) * 250000),
        ]
        seed = p.get('seed', 0)
        if seed:
            cmd += ['--seed', str(seed)]
        if flex_pdbqt:
            cmd += ['--flexres', flex_pdbqt]
        self._run_cmd(cmd, "AutoDock-GPU", cwd=tmp)

        dlg_path = dlg_base + '.dlg'
        if not os.path.isfile(dlg_path):
            raise RuntimeError(f"AutoDock-GPU did not produce: {dlg_path}")
        return ('ad4gpu', dlg_path)

    # ── Main run ------------------------------------------------------- #
    @Slot()
    def run(self):
        p   = self._params
        tmp = p['tmp_dir']

        # Derive prepare_dpf path from mgltools dir
        _mgldir  = os.path.dirname(os.path.dirname(p.get('prepare_receptor', '')))
        _util24  = os.path.join(_mgldir, 'MGLToolsPckgs', 'AutoDockTools', 'Utilities24')
        p.setdefault('prepare_dpf',
                     os.path.join(_util24, 'prepare_dpf42.py'))

        try:
            sf_types = p['sf_types']

            # ── Step 1 & 2: PDB → PDBQT (+ optional flex split) ─────
            rec_pdbqt, lig_pdbqt, flex_pdbqt, rigid_pdbqt = \
                self._prep_pdbqt(p, tmp)

            results   = []
            fld_path  = None
            gpf_path  = None

            # ── Steps 3 & 4: Iterate modes × scoring functions ────────
            listmode = p.get('listmode', ['rigid'])
            for mode in listmode:
                use_flex = (mode == 'flexible' and flex_pdbqt and rigid_pdbqt)
                _flex    = flex_pdbqt  if use_flex else None
                _rigid   = rigid_pdbqt if use_flex else None
                mode_tmp = os.path.join(tmp, mode)
                os.makedirs(mode_tmp, exist_ok=True)

                for sf in [s for s in sf_types if s in ('vina', 'vinardo')]:
                    r = self._run_vina_sf(
                        p, mode_tmp, rec_pdbqt, lig_pdbqt, sf,
                        rigid_pdbqt=_rigid, flex_pdbqt=_flex)
                    if r:
                        results.append((f"{mode}/{r[0]}", r[1]))
                    else:
                        self.log.emit(f"⚠ No output for scoring={sf} mode={mode}")

                ad4_needed   = 'ad4'    in sf_types
                adgpu_needed = 'ad4gpu' in sf_types

                if ad4_needed or adgpu_needed:
                    fld_path, gpf_path = self._build_ad4_grids(
                        p, mode_tmp, rec_pdbqt, lig_pdbqt, flex_pdbqt=_flex)

                    if ad4_needed:
                        r = self._run_ad4(
                            p, mode_tmp, rec_pdbqt, lig_pdbqt, gpf_path,
                            flex_pdbqt=_flex)
                        results.append((f"{mode}/{r[0]}", r[1]))

                    if adgpu_needed:
                        r = self._run_adgpu(
                            p, mode_tmp, lig_pdbqt, fld_path,
                            flex_pdbqt=_flex)
                        results.append((f"{mode}/{r[0]}", r[1]))

            if not results:
                raise RuntimeError("Docking produced no output files.")

            self.finished.emit(results)

        except Exception as exc:
            self.error.emit(str(exc))


# ═══════════════════════════════════════════════════════════════════════════ #
# Native Redocking Panel
# ═══════════════════════════════════════════════════════════════════════════ #

_GRP = (
    "QGroupBox{{color:{t};border:1px solid " + theme.BORDER_DIM + ";border-radius:5px;"
    "margin-top:8px;padding-top:8px;font-weight:600;}}"
    "QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 5px;color:{t};}}"
)
_SPIN = (
    f"QDoubleSpinBox,QSpinBox{{background:{theme.BG_MUTED};color:{theme.TEXT};"
    f"border:1px solid {theme.BORDER};border-radius:4px;padding:2px 6px;}}"
)
_COMBO = (
    f"QComboBox{{background:{theme.BG_MUTED};color:{theme.TEXT};"
    f"border:1px solid {theme.BORDER};border-radius:4px;padding:2px 6px;}}"
    "QComboBox::drop-down{border:none;}"
    f"QComboBox QAbstractItemView{{background:{theme.BG_MUTED};color:{theme.TEXT};}}"
)


class NativeRedockingPanel(QWidget):
    """
    Native Ligand Redocking Panel.

    1. Load receptor PDB from receptor_ready/
    2. Parse components → component table with checkboxes
    3. Set docking parameters (center, box, scoring, …)
    4. Run docking → result table (all poses)
    """

    docking_finished  = Signal(str)   # path to output PDBQT
    result_csv_ready  = Signal(str)   # path to results CSV
    # Job tracking signals → connect to JobManagerPanel
    job_registered    = Signal(object)   # DockingJob (RUNNING)
    job_log_line      = Signal(str, str) # (job_id, message)
    job_status_changed = Signal(object)  # DockingJob (FINISHED/FAILED)

    def __init__(self, job_dir: str = "", parent=None):
        super().__init__(parent)
        self._job_dir     = job_dir
        self._current_pdb = ""
        self._components: list[dict] = []
        self._comp_state: dict[str, list[dict]] = {}  # path → [{receptor, native_ligand}]
        self._rec_config: dict[str, dict] = {}         # path → {params, center_mode}
        self._checked_receptors: set[str] = set()      # proteins ticked for docking
        self._batch_queue: list[str] = []              # remaining receptors to redock
        self._thread = None
        self._worker = None
        self._current_job = None   # DockingJob — live tracking
        self._rec_name = ""  # receptor display name (set at run start)
        self._lig_name = ""  # native ligand resname (set at run start)
        self._result_rows: list[dict] = []   # accumulated poses (→ Results tab)
        self._build_ui()
        # Watcher: auto-refresh list when receptor_ready/ changes
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._refresh_file_list)
        if job_dir:
            self.set_job_dir(job_dir)

    # ------------------------------------------------------------------ #
    # Logging — streamed live to the Jobs tab (this panel has no log view)
    # ------------------------------------------------------------------ #

    def _emit_log(self, msg: str, job_id: str = ""):
        """Stream a log line to the Jobs tab (persisted to logs/ per line)."""
        jid = job_id or (self._current_job.job_id if self._current_job else "")
        if jid:
            self.job_log_line.emit(jid, msg)

    def _refresh_button(self, callback) -> QPushButton:
        """A small refresh button using Qt's themed reload icon (never a tofu
        box, unlike a font glyph)."""
        from PySide6.QtWidgets import QStyle
        btn = QPushButton()
        btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        btn.setFixedSize(24, 24)
        btn.setToolTip("Refresh list from directory")
        btn.setStyleSheet(
            f"QPushButton{{background:{theme.BG_HOVER};border:1px solid {theme.BORDER};"
            f"border-radius:4px;}}"
            f"QPushButton:hover{{background:{theme.BORDER};}}")
        btn.clicked.connect(callback)
        return btn

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_job_dir(self, path: str):
        self._job_dir = path
        # Update watcher to track receptor_ready/
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        ready_dir = os.path.join(path, "receptor_ready")
        os.makedirs(ready_dir, exist_ok=True)
        self._watcher.addPath(ready_dir)
        self._refresh_file_list()
        self._stack.setCurrentIndex(1)  # show main content

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Stacked: overlay (no job dir) / main content ──────────────
        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        # Page 0 — "No job dir" overlay
        overlay = QWidget()
        overlay.setStyleSheet(f"background:{theme.BG_BASE};")
        ov_lay = QVBoxLayout(overlay)
        ov_lay.setAlignment(Qt.AlignCenter)
        ov_icon = QLabel("📂")
        ov_icon.setFont(QFont("Sans", 40))
        ov_icon.setAlignment(Qt.AlignCenter)
        ov_msg = QLabel("Job Directory not set.\nGo to Preparation → Open or Generate Job Dir first.")
        ov_msg.setAlignment(Qt.AlignCenter)
        ov_msg.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:13px;")
        ov_lay.addWidget(ov_icon)
        ov_lay.addSpacing(8)
        ov_lay.addWidget(ov_msg)
        self._stack.addWidget(overlay)  # index 0

        # Page 1 — main content (wrapped in a scroll area so it stays usable on
        # short screens: parameters and the Run button scroll instead of being
        # clipped; on tall screens the component table still expands to fill).
        from PySide6.QtWidgets import QScrollArea
        main_w = QWidget()
        main_lay = QVBoxLayout(main_w)
        main_lay.setContentsMargins(8, 8, 8, 8)
        main_lay.setSpacing(6)
        main_scroll = QScrollArea()
        main_scroll.setWidgetResizable(True)
        main_scroll.setFrameShape(QFrame.NoFrame)
        main_scroll.setWidget(main_w)
        self._stack.addWidget(main_scroll)   # index 1
        self._stack.setCurrentIndex(0)  # show overlay until job dir is set

        self._build_main_content(main_lay)

    def _build_main_content(self, root):
        root.addWidget(SectionLabel("🎯 Native Ligand Redocking"))

        # ── Top splitter: file list | component table ──────────────────
        top_split = QSplitter(Qt.Horizontal)
        top_split.setMinimumHeight(200)   # expands to fill; pushes params down
        top_split.setStyleSheet(
            f"QSplitter::handle{{background:{theme.BORDER_DIM};width:2px;}}")

        # File list
        file_panel = QWidget()
        fp = QVBoxLayout(file_panel)
        fp.setContentsMargins(0, 0, 0, 0)
        fp.setSpacing(3)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("RECEPTOR READY"))
        hdr.addStretch()
        hdr.addWidget(self._refresh_button(self._refresh_file_list))
        fp.addLayout(hdr)

        self._file_list = QListWidget()
        self._file_list.itemChanged.connect(self._on_receptor_checked)
        self._file_list.setStyleSheet(theme.LIST_STYLE)
        self._file_list.itemClicked.connect(self._on_file_selected)
        fp.addWidget(self._file_list)
        file_panel.setFixedWidth(200)
        top_split.addWidget(file_panel)

        # Component table
        comp_panel = QWidget()
        cp = QVBoxLayout(comp_panel)
        cp.setContentsMargins(0, 0, 0, 0)
        cp.setSpacing(3)
        cp.addWidget(QLabel("🔬  Molecular Components"))

        self._comp_table = QTableWidget(0, 8)
        self._comp_table.setHorizontalHeaderLabels(
            ["Chain", "ResName", "ResSeq", "Type", "#Res", "#Atoms",
             "As Receptor", "Native Ligand"])
        self._comp_table.horizontalHeader().setStyleSheet(theme.TABLE_HEADER_STYLE)
        self._comp_table.setStyleSheet(theme.TABLE_STYLE)
        self._comp_table.verticalHeader().setVisible(False)
        self._comp_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._comp_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self._comp_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        cp.addWidget(self._comp_table)

        # Ligand radio group (only one native ligand at a time)
        self._ligand_radio_group = QButtonGroup(self)
        self._ligand_radio_group.setExclusive(True)

        top_split.addWidget(comp_panel)
        root.addWidget(top_split, 1)   # component table area takes the extra space

        # ── Docking parameters ─────────────────────────────────────────
        params_grp = QGroupBox("Docking Parameters")
        params_grp.setStyleSheet(_GRP.format(t=theme.ACCENT))
        # Split: parameter controls on the left, live 3D preview on the right.
        _params_outer = QHBoxLayout(params_grp)
        _params_outer.setContentsMargins(6, 6, 6, 6)
        _params_outer.setSpacing(10)
        _params_left = QWidget()
        pg = QVBoxLayout(_params_left)
        pg.setContentsMargins(0, 0, 0, 0)
        pg.setSpacing(8)

        def _tune_spinbox(sp, width: int):
            sp.setFixedWidth(width)
            sp.setFixedHeight(32)
            sp.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
            sp.setKeyboardTracking(False)
            sp.setAlignment(Qt.AlignRight)
            # Keep native spinbox painting on Windows; styling subcontrols via
            # stylesheet broke the up/down buttons and their hitboxes.
            sp.setStyleSheet("")
            return sp

        def _sec(txt: str) -> QLabel:
            lbl = QLabel(txt)
            lbl.setStyleSheet(
                f"color:{theme.ACCENT};font-size:11px;font-weight:bold;margin-top:5px;")
            return lbl

        # ── Section 1: Scoring functions ───────────────────────────────
        pg.addWidget(_sec("1 · Scoring functions"))
        sf_row = QHBoxLayout()
        sf_row.setSpacing(10)
        sf_row.addWidget(QLabel("Scoring:"))
        self._sf_vina    = QCheckBox("Vina");    self._sf_vina.setChecked(True)
        self._sf_vinardo = QCheckBox("Vinardo"); self._sf_vinardo.setChecked(False)
        self._sf_ad4     = QCheckBox("AD4");     self._sf_ad4.setChecked(False)
        self._sf_ad4gpu  = QCheckBox("AD4-GPU"); self._sf_ad4gpu.setChecked(False)
        for cb in (self._sf_vina, self._sf_vinardo, self._sf_ad4, self._sf_ad4gpu):
            cb.setStyleSheet(f"color:{theme.TEXT};font-size:11px;")
            sf_row.addWidget(cb)
            cb.stateChanged.connect(self._update_sf_params)
        sf_row.addStretch()
        pg.addLayout(sf_row)
        # Scoring functions are enabled/disabled by real tool availability
        # (see _update_sf_availability), which runs once the tool-path fields
        # below are created and again after tool detection.
        self._sf_hint = QLabel("")
        self._sf_hint.setStyleSheet(f"color:{theme.TEXT_DIM};font-size:10px;")
        self._sf_hint.setVisible(False)
        pg.addWidget(self._sf_hint)

        # Engine-specific parameter rows sit directly under their scoring
        # functions and are shown/hidden by _update_sf_params.
        # ── Vina/Vinardo specific ──────────────────────────────────────
        self._vv_params_widget = QWidget()
        vv_row = QHBoxLayout(self._vv_params_widget)
        vv_row.setContentsMargins(0, 0, 0, 0)
        vv_row.setSpacing(8)
        vv_lbl = QLabel("Vina/Vinardo ▸")
        vv_lbl.setStyleSheet("color:#58a6ff;font-size:11px;font-weight:bold;")
        vv_row.addWidget(vv_lbl)
        vv_row.addWidget(QLabel("Exhaustiveness:"))
        self._exhaustiveness = QSpinBox(); self._exhaustiveness.setRange(1, 64); self._exhaustiveness.setValue(8)
        _tune_spinbox(self._exhaustiveness, 92)
        self._exhaustiveness.setToolTip("Search exhaustiveness (Vina/Vinardo). Higher = more thorough, slower.")
        vv_row.addWidget(self._exhaustiveness)
        vv_row.addWidget(QLabel("Energy Range:"))
        self._energy_range = QSpinBox(); self._energy_range.setRange(1, 10); self._energy_range.setValue(3)
        _tune_spinbox(self._energy_range, 88)
        self._energy_range.setToolTip("Max energy difference from best pose to report (kcal/mol).")
        vv_row.addWidget(self._energy_range)
        vv_row.addStretch()
        pg.addWidget(self._vv_params_widget)

        # ── AD4 / AD4GPU specific (grid-based) ─────────────────────────
        self._grid_params_widget = QWidget()
        grid_row = QHBoxLayout(self._grid_params_widget)
        grid_row.setContentsMargins(0, 0, 0, 0)
        grid_row.setSpacing(8)
        grid_lbl = QLabel("AD4/GPU ▸")
        grid_lbl.setStyleSheet("color:#3fb950;font-size:11px;font-weight:bold;")
        grid_row.addWidget(grid_lbl)
        grid_row.addWidget(QLabel("Exhaustiveness:"))
        self._ad4_exhaustiveness = QSpinBox(); self._ad4_exhaustiveness.setRange(1, 100); self._ad4_exhaustiveness.setValue(8)
        _tune_spinbox(self._ad4_exhaustiveness, 92)
        self._ad4_exhaustiveness.setToolTip("Multiplied × 250 000 → ga_num_evals (AD4) / --nev (AD4GPU).")
        grid_row.addWidget(self._ad4_exhaustiveness)
        grid_row.addWidget(QLabel("Grid Spacing (Å):"))
        self._spacing = QDoubleSpinBox(); self._spacing.setRange(0.1, 2.0); self._spacing.setDecimals(3)
        self._spacing.setValue(0.375); _tune_spinbox(self._spacing, 104)
        self._spacing.setToolTip("AutoGrid4 grid point spacing (AD4 and AD4GPU).")
        grid_row.addWidget(self._spacing)
        # AD4-only sub-group (GA params)
        self._ad4_only_widget = QWidget()
        ad4_sub = QHBoxLayout(self._ad4_only_widget)
        ad4_sub.setContentsMargins(12, 0, 0, 0)
        ad4_sub.setSpacing(8)
        ad4_sub.addWidget(QLabel("GA Pop:"))
        self._ga_pop_size = QSpinBox(); self._ga_pop_size.setRange(50, 1000); self._ga_pop_size.setValue(150)
        self._ga_pop_size.setSingleStep(50); _tune_spinbox(self._ga_pop_size, 100)
        self._ga_pop_size.setToolTip("GA population size (AD4 only). Default 150.")
        ad4_sub.addWidget(self._ga_pop_size)
        ad4_sub.addWidget(QLabel("Cluster RMSD (Å):"))
        self._cluster_rmsd = QDoubleSpinBox(); self._cluster_rmsd.setRange(0.1, 10.0); self._cluster_rmsd.setDecimals(1)
        self._cluster_rmsd.setValue(2.0); _tune_spinbox(self._cluster_rmsd, 100)
        self._cluster_rmsd.setToolTip("Pose clustering RMSD (AD4 only). Default 2.0 Å.")
        ad4_sub.addWidget(self._cluster_rmsd)
        grid_row.addWidget(self._ad4_only_widget)
        grid_row.addStretch()
        pg.addWidget(self._grid_params_widget)

        # ── Section 2: Search box ──────────────────────────────────────
        pg.addWidget(_sec("2 · Search box"))
        center_row = QHBoxLayout()
        center_row.setSpacing(8)
        center_row.addWidget(QLabel("Box Center:"))
        self._center_grp = QButtonGroup(self)
        for i, (lbl, val) in enumerate([
                ("To Ligand", "ligand"), ("To Protein", "protein"),
                ("Custom", "custom")]):
            rb = QRadioButton(lbl)
            rb.setStyleSheet(f"color:{theme.TEXT};font-size:11px;")
            rb.setProperty("mode", val)
            self._center_grp.addButton(rb, i)
            center_row.addWidget(rb)
            if i == 0:
                rb.setChecked(True)
        center_row.addSpacing(10)
        for ax in ('X', 'Y', 'Z'):
            center_row.addWidget(QLabel(ax + ":"))
            sp = QDoubleSpinBox()
            sp.setRange(-999, 999)
            sp.setDecimals(3)
            _tune_spinbox(sp, 132)
            setattr(self, f'_cx{ax.lower()}', sp)
            center_row.addWidget(sp)
        self._center_grp.buttonClicked.connect(self._on_center_mode_changed)
        # NOTE: initial _on_center_mode_changed() is called after the Mode/Flex
        # row is built (it depends on self._mode_flexible existing).
        center_row.addStretch()
        pg.addLayout(center_row)

        # ── Row 3: Box size + AD4 spacing ─────────────────────────────
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_row.addWidget(QLabel("Box Size (Å):"))
        for ax in ('X', 'Y', 'Z'):
            size_row.addWidget(QLabel(ax + ":"))
            sp = QDoubleSpinBox()
            sp.setRange(1, 200)
            sp.setDecimals(1)
            sp.setValue(20.0)
            _tune_spinbox(sp, 100)
            setattr(self, f'_sx{ax.lower()}', sp)
            size_row.addWidget(sp)
        size_row.addStretch()
        pg.addLayout(size_row)

        # ── Section 3: Receptor flexibility ────────────────────────────
        pg.addWidget(_sec("3 · Receptor flexibility"))
        # ── Row 4: Mode (rigid/flex) + Flexible residues ───────────────
        # Flex-residue controls only apply in Flexible mode, so they share the
        # row with the mode toggle and are enabled/disabled by it.
        from PySide6.QtWidgets import QLineEdit
        flex_row = QHBoxLayout()
        flex_row.setSpacing(8)
        flex_row.addWidget(QLabel("Mode:"))
        self._mode_rigid    = QCheckBox("Rigid");    self._mode_rigid.setChecked(True)
        self._mode_flexible = QCheckBox("Flexible"); self._mode_flexible.setChecked(False)
        for cb in (self._mode_rigid, self._mode_flexible):
            cb.setStyleSheet(f"color:{theme.TEXT};font-size:11px;")
            flex_row.addWidget(cb)
        flex_row.addSpacing(12)
        self._flex_dist_lbl = QLabel("Flex Distance (Å):")
        flex_row.addWidget(self._flex_dist_lbl)
        self._flex_dist = QDoubleSpinBox()
        self._flex_dist.setRange(0.0, 10.0)
        self._flex_dist.setDecimals(1)
        self._flex_dist.setValue(3.0)
        _tune_spinbox(self._flex_dist, 100)
        flex_row.addWidget(self._flex_dist)
        self._flex_res_lbl = QLabel("Flexible Residues:")
        flex_row.addWidget(self._flex_res_lbl)
        self._flex_residues = QLineEdit()
        self._flex_residues.setPlaceholderText(
            "auto-filled when Flexible mode is selected")
        self._flex_residues.setStyleSheet(
            f"background:{theme.BG_MUTED};color:{theme.TEXT};"
            f"border:1px solid {theme.BORDER};border-radius:4px;"
            "padding:4px 8px;font-size:11px;")
        self._flex_residues.setMinimumHeight(32)
        flex_row.addWidget(self._flex_residues, 1)
        pg.addLayout(flex_row)
        self._flex_hint = QLabel("")
        self._flex_hint.setStyleSheet(f"color:{theme.TEXT_DIM};font-size:10px;")
        self._flex_hint.setVisible(False)
        pg.addWidget(self._flex_hint)

        # Connect flex controls → live update + enable/disable by mode
        self._mode_flexible.stateChanged.connect(
            lambda _: self._refresh_flex_residues())
        self._flex_dist.valueChanged.connect(
            lambda _: self._refresh_flex_residues())

        def _sync_flex_enabled():
            on = self._mode_flexible.isChecked()
            for w in (self._flex_dist_lbl, self._flex_dist,
                      self._flex_res_lbl, self._flex_residues):
                w.setEnabled(on)
        self._mode_flexible.toggled.connect(lambda _=None: _sync_flex_enabled())
        _sync_flex_enabled()
        # Now that the mode widgets exist, run the deferred center-mode init.
        self._on_center_mode_changed(self._center_grp.button(0))

        # ── Section 4: Search settings ─────────────────────────────────
        pg.addWidget(_sec("4 · Search settings"))
        # ── Row 5: Common params (always visible) ──────────────────────
        common_row = QHBoxLayout()
        common_row.setSpacing(8)
        common_row.addWidget(QLabel("N Poses:"))
        self._n_poses = QSpinBox(); self._n_poses.setRange(1, 20); self._n_poses.setValue(9)
        _tune_spinbox(self._n_poses, 88)
        common_row.addWidget(self._n_poses)
        common_row.addWidget(QLabel("CPU:"))
        self._cpu = QSpinBox(); self._cpu.setRange(1, 64); self._cpu.setValue(4)
        _tune_spinbox(self._cpu, 88)
        self._cpu.setToolTip("CPU cores per job (Vina/Vinardo/AD4). Not used by AD4GPU.")
        common_row.addWidget(self._cpu)
        common_row.addWidget(QLabel("Max Workers:"))
        self._max_workers = QSpinBox(); self._max_workers.setRange(1, 16); self._max_workers.setValue(3)
        _tune_spinbox(self._max_workers, 92)
        self._max_workers.setToolTip("Max parallel docking jobs.")
        common_row.addWidget(self._max_workers)
        common_row.addWidget(QLabel("Seed (0=rnd):"))
        self._seed = QSpinBox(); self._seed.setRange(0, 2147483647); self._seed.setValue(0)
        _tune_spinbox(self._seed, 132)
        self._seed.setToolTip("Random seed (0 = random). Used by Vina/Vinardo and AD4GPU.")
        common_row.addWidget(self._seed)
        common_row.addStretch()
        pg.addLayout(common_row)

        # ── Row 6: I/O options ──────────────────────────────────────────
        io_row = QHBoxLayout()
        io_row.setSpacing(10)
        self._save_input  = QCheckBox("Save Input Files")
        self._save_output = QCheckBox("Save Output Files")
        self._parallel    = QCheckBox("Parallel Simulation")
        self._save_input.setChecked(True)
        self._save_output.setChecked(True)
        self._parallel.setChecked(False)
        for cb in (self._save_input, self._save_output, self._parallel):
            cb.setStyleSheet(f"color:{theme.TEXT};font-size:11px;")
            io_row.addWidget(cb)
        io_row.addStretch()
        pg.addLayout(io_row)

        # ── Row 7: Tools compact status bar ────────────────────────────
        from PySide6.QtWidgets import QLineEdit, QDialog, QDialogButtonBox, QScrollArea
        from engine.tool_detector import INSTALL_URLS, PIP_INSTALL, BUNDLED_KEYS

        tools_bar = QHBoxLayout()
        tools_bar.setSpacing(4)

        _tool_fields = [
            ("Vina",         "_tp_vina",    "vina",         "file"),
            ("AutoDock4",    "_tp_ad4",     "autodock4",    "file"),
            ("AutoGrid4",    "_tp_ag4",     "autogrid4",    "file"),
            ("AutoDock-GPU", "_tp_adgpu",   "autodock_gpu", "file"),
            ("ADFR",         "_tp_adfr",    "adfr",         "file"),
            ("AGFR",         "_tp_agfr",    "agfr",         "file"),
            ("MGLTools Path","_tp_mgltools","mgltools",      "dir"),
        ]
        self._tp_status_labels: dict[str, QLabel] = {}

        # Create hidden QLineEdit fields (still used for path storage)
        for lbl, attr, det_key, _ in _tool_fields:
            le = QLineEdit()
            le.setVisible(False)
            setattr(self, attr, le)
            # Status badge label
            badge = QLabel(f"⏳ {lbl}")
            badge.setStyleSheet(
                f"background:{theme.BG_MUTED};color:{theme.TEXT_DIM};border-radius:3px;"
                "padding:1px 6px;font-size:10px;")
            badge.setToolTip(f"{lbl}: detecting…")
            self._tp_status_labels[det_key] = badge
            tools_bar.addWidget(badge)

        tools_bar.addStretch()
        override_btn = QPushButton("⚙ Override Paths")
        override_btn.setFixedHeight(22)
        override_btn.setStyleSheet(
            f"QPushButton{{background:{theme.BG_HOVER};color:{theme.TEXT_DIM};"
            f"border:1px solid {theme.BORDER};border-radius:3px;padding:0 8px;font-size:10px;}}"
            f"QPushButton:hover{{background:{theme.BORDER};color:{theme.TEXT};}}")
        from PySide6.QtWidgets import QStyle
        redetect_btn = QPushButton()
        redetect_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        redetect_btn.setFixedSize(26, 22)
        redetect_btn.setToolTip("Re-detect all tools")
        redetect_btn.setStyleSheet(
            "QPushButton{background:#2d333b;border:1px solid #373e47;"
            "border-radius:3px;}"
            "QPushButton:hover{background:#373e47;}")
        redetect_btn.clicked.connect(self._detect_tools)
        tools_bar.addWidget(override_btn)
        tools_bar.addWidget(redetect_btn)
        # Tool paths are global (not per-receptor docking parameters), so they
        # live in their own group outside "Docking Parameters".
        self._tools_grp = QGroupBox("🔧 Tool Paths")
        self._tools_grp.setStyleSheet(_GRP.format(t="#8b949e"))
        _tgl = QVBoxLayout(self._tools_grp)
        _tgl.setContentsMargins(8, 4, 8, 4)
        _tgl.addLayout(tools_bar)

        def _open_override_dialog():
            dlg = QDialog(self)
            dlg.setWindowTitle("Override Tool Paths")
            dlg.setMinimumWidth(620)
            dlg.setStyleSheet(
                "QDialog{background:#161b22;color:#e6edf3;}"
                "QLabel{color:#8b949e;font-size:10px;}"
                "QLineEdit{background:#2d333b;color:#e6edf3;border:1px solid #373e47;"
                "border-radius:3px;padding:2px 4px;font-size:10px;}")
            dv = QVBoxLayout(dlg)
            dv.setSpacing(6)
            dv.addWidget(QLabel(
                "Bundled tools are auto-detected. Override only if you need a custom binary."))
            _btn_style = (
                "QPushButton{background:#2d333b;color:#e6edf3;border:1px solid #373e47;"
                "border-radius:3px;padding:0 6px;font-size:10px;}"
                "QPushButton:hover{background:#373e47;}")
            for lbl, attr, det_key, browse_mode in _tool_fields:
                row = QHBoxLayout()
                row.setSpacing(4)
                lbl_w = QLabel(f"{lbl}:")
                lbl_w.setFixedWidth(110)
                le = getattr(self, attr)
                le.setVisible(True)
                le.setFixedHeight(22)
                sl = self._tp_status_labels.get(det_key)
                sl_copy = QLabel(sl.toolTip() if sl else "")
                sl_copy.setFixedWidth(120)
                sl_copy.setStyleSheet("color:#8b949e;font-size:9px;")
                br_btn = QPushButton("📁")
                br_btn.setFixedSize(26, 22)
                br_btn.setStyleSheet(_btn_style)
                if browse_mode == "dir":
                    br_btn.clicked.connect(
                        lambda _=None, f=le: f.setText(
                            QFileDialog.getExistingDirectory(dlg, "Select directory") or f.text()))
                else:
                    br_btn.clicked.connect(
                        lambda _=None, f=le, n=lbl: f.setText(
                            QFileDialog.getOpenFileName(dlg, f"Select {n} binary")[0] or f.text()))
                url = INSTALL_URLS.get(det_key, "")
                if det_key in BUNDLED_KEYS:
                    act_btn = QPushButton("📦 Bundled")
                    act_btn.setFixedWidth(80)
                    act_btn.setEnabled(False)
                    act_btn.setStyleSheet(
                        "QPushButton{background:#12261e;color:#3fb950;border:1px solid #238636;"
                        "border-radius:3px;padding:0 6px;font-size:10px;}")
                elif url:
                    act_btn = QPushButton("🌐 Get")
                    act_btn.setFixedWidth(68)
                    act_btn.setStyleSheet(_btn_style)
                    act_btn.clicked.connect(
                        lambda _=None, u=url: QDesktopServices.openUrl(QUrl(u)))
                else:
                    act_btn = QPushButton("—")
                    act_btn.setFixedWidth(68)
                    act_btn.setEnabled(False)
                    act_btn.setStyleSheet(_btn_style)
                row.addWidget(lbl_w)
                row.addWidget(le, 1)
                row.addWidget(sl_copy)
                row.addWidget(br_btn)
                row.addWidget(act_btn)
                dv.addLayout(row)
            bbox = QDialogButtonBox(QDialogButtonBox.Ok)
            bbox.setStyleSheet(
                "QPushButton{background:#58a6ff;color:#161b22;border-radius:3px;"
                "padding:4px 18px;font-weight:bold;}")
            bbox.accepted.connect(dlg.accept)
            dv.addWidget(bbox)
            dlg.exec()
            self._update_sf_availability()

        override_btn.clicked.connect(_open_override_dialog)

        # Connect path changes to SF availability
        for attr in ("_tp_vina", "_tp_ad4", "_tp_adgpu"):
            getattr(self, attr).textChanged.connect(self._update_sf_availability)

        self._update_sf_availability()   # initial state from platform-honest detection
        self._update_sf_params()   # initial visibility based on default SF selection
        apply_docking_tooltips(self)

        # ── Right side: live 3D preview (protein + native ligand + grid box) ──
        _params_outer.addWidget(_params_left, 3)
        _preview_col = QVBoxLayout()
        _preview_col.setContentsMargins(0, 0, 0, 0)
        _preview_col.setSpacing(3)
        _prev_lbl = QLabel("3D Preview · grid box")
        _prev_lbl.setStyleSheet(f"color:{theme.ACCENT};font-size:11px;font-weight:bold;")
        _preview_col.addWidget(_prev_lbl)
        from gui.viewer.molecular_viewer import MolecularViewerPanel
        self._preview3d = MolecularViewerPanel()
        self._preview3d.set_auto_open(False)   # live preview: open browser on demand only
        self._preview3d.setMinimumWidth(300)
        self._preview3d.setMinimumHeight(320)
        _preview_col.addWidget(self._preview3d, 1)
        _params_outer.addLayout(_preview_col, 2)

        # Keep the grid box in sync with the Search box controls.
        for _sp in (self._cxx, self._cxy, self._cxz,
                    self._sxx, self._sxy, self._sxz):
            _sp.valueChanged.connect(lambda _=None: self._update_preview_box())

        root.addWidget(params_grp)
        root.addWidget(self._tools_grp)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, self._detect_tools)

        # ── Run button ─────────────────────────────────────────────────
        run_row = QHBoxLayout()
        self._run_btn = QPushButton("▶  Run Native Redocking")
        self._run_btn.setFixedHeight(36)
        self._run_btn.setStyleSheet(
            "QPushButton{background:#58a6ff;color:#161b22;border-radius:4px;"
            "font-weight:bold;font-size:13px;}"
            "QPushButton:hover{background:#79c0ff;}"
            "QPushButton:disabled{background:#373e47;color:#545d68;}")
        self._run_btn.clicked.connect(self._on_run)
        run_row.addWidget(self._run_btn)
        root.addLayout(run_row)

        self._prog = QProgressBar()
        self._prog.setRange(0, 0)
        self._prog.setVisible(False)
        self._prog.setFixedHeight(5)
        self._prog.setStyleSheet(
            "QProgressBar{border:none;background:#2d333b;border-radius:2px;}"
            "QProgressBar::chunk{background:#58a6ff;border-radius:2px;}")
        root.addWidget(self._prog)

        # No log or results view here: docking progress streams to the Jobs
        # tab and results are shown in the dedicated Results tab. The component
        # table above expands to fill; parameters sit below it.

    # ------------------------------------------------------------------ #
    # File list
    # ------------------------------------------------------------------ #

    def _refresh_file_list(self):
        self._file_list.blockSignals(True)
        self._file_list.clear()
        ready_dir = os.path.join(self._job_dir, "receptor_ready")
        if os.path.isdir(ready_dir):
            for fname in sorted(os.listdir(ready_dir)):
                if fname.lower().endswith(('.pdb', '.pdbqt')):
                    path = os.path.join(ready_dir, fname)
                    item = QListWidgetItem(fname)
                    item.setData(Qt.UserRole, path)
                    # click = show its components + parameters;
                    # check = include this protein in redocking.
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(
                        Qt.Checked if path in self._checked_receptors else Qt.Unchecked)
                    item.setToolTip("Click to configure · check to include in redocking")
                    self._file_list.addItem(item)
        self._file_list.blockSignals(False)

    def _on_receptor_checked(self, item: QListWidgetItem):
        """Track which proteins are ticked to undergo redocking."""
        path = item.data(Qt.UserRole)
        if not path:
            return
        if item.checkState() == Qt.Checked:
            self._checked_receptors.add(path)
        else:
            self._checked_receptors.discard(path)

    def _hybrid_enabled(self) -> bool:
        """Windows-only hybrid mode: dispatch AD4 / AD-GPU to WSL (embedded GUI
        stays native Windows). Off on Linux/macOS."""
        if os.name != "nt":
            return False
        return str(QSettings("LADOCK", "Desktop").value(
            "use_wsl_backend", False)).lower() in ("1", "true", "yes")

    def _detect_tools(self):
        """Run tool detection in background and update inline status labels."""
        from engine.tool_detector import detect_all
        from PySide6.QtCore import QThread, QObject, Signal as Sig
        _hyb = self._hybrid_enabled()

        class _W(QObject):
            done = Sig(dict)
            def run(self):
                self.done.emit(detect_all(_hyb))

        self._detect_thread = QThread(self)
        self._detect_worker = _W()
        self._detect_worker.moveToThread(self._detect_thread)
        self._detect_thread.started.connect(self._detect_worker.run)
        self._detect_worker.done.connect(self._on_tools_detected)
        self._detect_worker.done.connect(self._detect_thread.quit)
        self._detect_thread.start()

    def _on_tools_detected(self, results: dict):
        """Auto-fill path fields and update compact status badges."""
        _mapping = {
            "vina":         "_tp_vina",
            "autodock4":    "_tp_ad4",
            "autogrid4":    "_tp_ag4",
            "autodock_gpu": "_tp_adgpu",
            "adfr":         "_tp_adfr",
            "agfr":         "_tp_agfr",
            "mgltools":     "_tp_mgltools",
        }
        _labels = {
            "vina": "Vina", "autodock4": "AD4", "autogrid4": "AG4",
            "autodock_gpu": "AD-GPU", "adfr": "ADFR", "agfr": "AGFR",
            "mgltools": "MGLTools",
        }
        for key, t in results.items():
            attr = _mapping.get(key)
            field = getattr(self, attr, None) if attr else None
            if field is not None and t.found_path:
                field.setText(t.found_path)
            badge = self._tp_status_labels.get(key)
            if badge:
                short = _labels.get(key, key)
                if t.available:
                    ver = t.version.split()[-1] if t.version else ""
                    badge.setText(f"✅ {short}")
                    badge.setToolTip(f"{short}: {t.found_path}\n{ver}")
                    badge.setStyleSheet(
                        f"background:{theme.SUCCESS_BG};color:{theme.SUCCESS};"
                        "border-radius:3px;padding:1px 6px;font-size:10px;")
                else:
                    reason = t.version or "not found"
                    badge.setText(f"❌ {short}")
                    badge.setToolTip(f"{short}: {reason}")
                    badge.setStyleSheet(
                        f"background:{theme.ERROR_BG};color:{theme.ERROR};"
                        "border-radius:3px;padding:1px 6px;font-size:10px;")
        self._update_sf_availability()

    def _update_sf_availability(self):
        """Enable/disable scoring functions by real tool availability.

        A function is enabled only when its engine binary actually exists for
        this platform — either the path shown in the Tool Paths field (an
        override or a detected path) or, before detection has populated that
        field, the platform-honest auto-detected candidate. Linux-only engines
        (AD4 / AD4-GPU) therefore stay disabled on native Windows/macOS."""
        import os
        from core.tool_paths import tool_available, autodock_gpu_runnable
        hyb = self._hybrid_enabled()

        def _ok(key: str, field) -> bool:
            path = field.text().strip()
            if path:
                return os.path.isfile(path)
            return tool_available(key, hyb)

        vina_ok   = _ok("vina",         self._tp_vina)
        ad4_ok    = _ok("autodock4",    self._tp_ad4)
        # AutoDock-GPU additionally needs its CUDA runtime to resolve.
        adgpu_present = _ok("autodock_gpu", self._tp_adgpu)
        adgpu_ok  = adgpu_present and autodock_gpu_runnable(self._tp_adgpu.text(), hyb)

        _tip_na = "Tool not found — configure path in Tool Paths"
        _tip_cuda = "AutoDock-GPU found, but its CUDA runtime is missing"
        adgpu_tip = _tip_cuda if (adgpu_present and not adgpu_ok) else _tip_na

        for cb, ok, tip in (
            (self._sf_vina,   vina_ok,  _tip_na),
            (self._sf_vinardo,vina_ok,  _tip_na),
            (self._sf_ad4,    ad4_ok,   _tip_na),
            (self._sf_ad4gpu, adgpu_ok, adgpu_tip),
        ):
            cb.setEnabled(ok)
            if not ok:
                cb.setChecked(False)
                cb.setToolTip(tip)
                cb.setStyleSheet("color:#545d68;font-size:11px;")
            else:
                cb.setToolTip("")
                cb.setStyleSheet(f"color:{theme.TEXT};font-size:11px;")

        # Ensure at least Vina stays checked when it becomes available
        if vina_ok and not self._sf_vina.isChecked() and not any(
                cb.isChecked() for cb in (self._sf_vinardo, self._sf_ad4, self._sf_ad4gpu)):
            self._sf_vina.setChecked(True)

        # Inline hint explaining any greyed-out engine.
        if hasattr(self, "_sf_hint"):
            unavailable = [name for name, ok in
                           (("AD4", ad4_ok), ("AD4-GPU", adgpu_ok)) if not ok]
            if unavailable:
                tip = ("enable Hybrid mode in Settings → Backend to run them via WSL"
                       if (os.name == "nt" and not hyb)
                       else "needs the Linux engine (WSL/Linux); AD-GPU also needs CUDA")
                self._sf_hint.setText(
                    f"{', '.join(unavailable)} unavailable — {tip}.")
                self._sf_hint.setVisible(True)
            else:
                self._sf_hint.setVisible(False)

    def _pip_install(self, name: str, cmd: str):
        """Run pip install in a subprocess and show result dialog."""
        import subprocess
        ret = QMessageBox.question(
            self, f"Install {name}",
            f"Run the following command?\n\n  {cmd}\n",
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            r = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                QMessageBox.information(self, "Installed", f"{name} installed successfully.\nRe-detecting tools…")
                self._detect_tools()
            else:
                QMessageBox.warning(self, "Install failed", r.stderr[:800])
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_file_selected(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if not path or not os.path.isfile(path):
            return
        self._activate_receptor(path)

    # ------------------------------------------------------------------ #
    # Per-receptor configuration — each protein keeps its own components
    # and full docking parameters.
    # ------------------------------------------------------------------ #

    def _param_widgets(self) -> dict:
        return {
            'cx': self._cxx, 'cy': self._cxy, 'cz': self._cxz,
            'sx': self._sxx, 'sy': self._sxy, 'sz': self._sxz,
            'spacing': self._spacing,
            'sf_vina': self._sf_vina, 'sf_vinardo': self._sf_vinardo,
            'sf_ad4': self._sf_ad4, 'sf_ad4gpu': self._sf_ad4gpu,
            'mode_rigid': self._mode_rigid, 'mode_flexible': self._mode_flexible,
            'flex_dist': self._flex_dist, 'flex_residues': self._flex_residues,
            'n_poses': self._n_poses, 'cpu': self._cpu,
            'max_workers': self._max_workers, 'seed': self._seed,
            'exhaustiveness': self._exhaustiveness, 'energy_range': self._energy_range,
            'ad4_exhaustiveness': self._ad4_exhaustiveness,
            'ga_pop_size': self._ga_pop_size, 'cluster_rmsd': self._cluster_rmsd,
            'save_input': self._save_input, 'save_output': self._save_output,
            'parallel': self._parallel,
        }

    def _capture_config(self) -> dict:
        btn = self._center_grp.checkedButton()
        return {
            'params': {k: _widget_value(w) for k, w in self._param_widgets().items()},
            'center_mode': btn.property("mode") if btn else "ligand",
        }

    def _apply_config(self, cfg: dict):
        wmap = self._param_widgets()
        for k, v in cfg.get('params', {}).items():
            w = wmap.get(k)
            if w is not None:
                _set_widget_value(w, v)
        mode = cfg.get('center_mode')
        if mode:
            for b in self._center_grp.buttons():
                if b.property("mode") == mode:
                    b.setChecked(True)
                    break

    def _activate_receptor(self, path: str):
        """Make `path` the active receptor: save the previous one's components
        and parameters, load this receptor, then restore its saved config."""
        if (self._current_pdb and self._current_pdb != path
                and os.path.isfile(self._current_pdb)):
            self._save_comp_state(self._current_pdb)
            self._rec_config[self._current_pdb] = self._capture_config()
        self._current_pdb = path
        self._load_components(path)                 # restores comp state if any
        cfg = self._rec_config.get(path)
        if cfg:
            self._apply_config(cfg)
        self._update_preview_structure()

    def _save_comp_state(self, path: str):
        """Save per-row receptor/native-ligand state for given file path."""
        state = []
        for row in range(self._comp_table.rowCount()):
            rec_cb = _inner_widget(self._comp_table.cellWidget(row, 6))
            lig_rb = _inner_widget(self._comp_table.cellWidget(row, 7))
            state.append({
                'receptor':      rec_cb.isChecked() if rec_cb else False,
                'native_ligand': lig_rb.isChecked() if lig_rb else False,
            })
        self._comp_state[path] = state

    def _load_components(self, pdb_path: str):
        self._components = parse_pdb_components(pdb_path)
        self._comp_table.setRowCount(0)

        # Clear old radio buttons
        for btn in self._ligand_radio_group.buttons():
            self._ligand_radio_group.removeButton(btn)

        first_ligand_row = -1

        for row, comp in enumerate(self._components):
            self._comp_table.insertRow(row)

            # Chain, ResName, ResSeq, Type, #Res, #Atoms
            for col, key in enumerate(('chain', 'resname', 'resseq', 'type',
                                        'n_residues', 'n_atoms')):
                item = QTableWidgetItem(str(comp.get(key, '')))
                item.setTextAlignment(Qt.AlignCenter)
                color = _TYPE_COLOR.get(comp['type'], '#e6edf3')
                item.setForeground(QBrush(QColor(color)))
                self._comp_table.setItem(row, col, item)

            # As Receptor checkbox (default: Protein=yes, others=no)
            rec_cb = QCheckBox()
            rec_cb.setChecked(comp['type'] == 'Protein')
            self._comp_table.setCellWidget(row, 6, _centered_widget(rec_cb))

            # Native Ligand radio — only enabled for Ligand type
            lig_rb = QRadioButton()
            lig_rb.setProperty("row", row)
            lig_rb.setEnabled(comp['type'] == 'Ligand')
            self._ligand_radio_group.addButton(lig_rb)
            self._comp_table.setCellWidget(row, 7, _centered_widget(lig_rb))

            if comp['type'] == 'Ligand' and first_ligand_row < 0:
                first_ligand_row = row

        # Restore saved state if exists; otherwise select first ligand as default
        if pdb_path in self._comp_state:
            self._restore_comp_state(pdb_path)
        elif first_ligand_row >= 0:
            rb = _inner_widget(self._comp_table.cellWidget(first_ligand_row, 7))
            if rb:
                rb.setChecked(True)

        self._emit_log(
            f"Loaded: {os.path.basename(pdb_path)} "
            f"— {len(self._components)} components")

        # Auto-update center spinboxes and flex residues after loading
        mode_btn = self._center_grp.checkedButton()
        mode = mode_btn.property("mode") if mode_btn else "ligand"
        if mode == "ligand":
            self._update_center_from_ligand()
        elif mode == "protein":
            self._update_center_from_protein()
        else:
            # Custom mode: center stays, but still refresh flex if flexible on
            self._refresh_flex_residues()

    def _restore_comp_state(self, path: str):
        """Restore per-row receptor/native-ligand state for given file path."""
        state = self._comp_state.get(path)
        if not state:
            return
        for row, s in enumerate(state):
            if row >= self._comp_table.rowCount():
                break
            rec_cb = _inner_widget(self._comp_table.cellWidget(row, 6))
            lig_rb = _inner_widget(self._comp_table.cellWidget(row, 7))
            if rec_cb:
                rec_cb.setChecked(s['receptor'])
            if lig_rb:
                lig_rb.setChecked(s['native_ligand'])

    # ------------------------------------------------------------------ #
    # Center mode
    # ------------------------------------------------------------------ #

    def _update_sf_params(self):
        """Show/hide parameter sections and gate Flexible mode based on the
        selected scoring functions."""
        vv   = self._sf_vina.isChecked() or self._sf_vinardo.isChecked()
        grid = self._sf_ad4.isChecked()  or self._sf_ad4gpu.isChecked()
        ad4_only = self._sf_ad4.isChecked()
        self._vv_params_widget.setVisible(vv)
        self._grid_params_widget.setVisible(grid)
        self._ad4_only_widget.setVisible(ad4_only)

        # Flexible-residue mode — enabled if a selected SF supports flex.
        # (All engines currently support flex; MLSD does not apply to redocking,
        # which docks a single native ligand.)
        from core.docking_features import any_supports_flex
        checked = []
        if self._sf_vina.isChecked():    checked.append("vina")
        if self._sf_vinardo.isChecked(): checked.append("vinardo")
        if self._sf_ad4.isChecked():     checked.append("ad4")
        if self._sf_ad4gpu.isChecked():  checked.append("ad4gpu")
        flex_ok = any_supports_flex(checked)
        self._mode_flexible.setEnabled(flex_ok)
        self._mode_flexible.setToolTip(
            "" if flex_ok else "No selected scoring function supports flexible residues")
        if not flex_ok and self._mode_flexible.isChecked():
            self._mode_flexible.setChecked(False)
            self._mode_rigid.setChecked(True)

    def _on_center_mode_changed(self, btn):
        mode = btn.property("mode") if btn else "ligand"
        is_custom = (mode == "custom")
        for sp in (self._cxx, self._cxy, self._cxz):
            sp.setEnabled(is_custom)
        if mode == "ligand":
            self._update_center_from_ligand()
        elif mode == "protein":
            self._update_center_from_protein()
        # Auto-refresh flex residues when center mode changes
        self._refresh_flex_residues()

    def _refresh_flex_residues(self):
        """
        Recompute flex residues from box center and update the field.
        Only runs when: flexible mode is checked AND a PDB is loaded
        AND center coordinates are non-zero.
        """
        flexible = self._mode_flexible.isChecked()
        self._flex_hint.setVisible(flexible)
        if not flexible:
            return
        if not self._current_pdb or not os.path.isfile(self._current_pdb):
            self._flex_hint.setText("Select a receptor to auto-fill flexible residues.")
            return
        cx = self._cxx.value()
        cy = self._cxy.value()
        cz = self._cxz.value()
        # Skip if center is still at (0,0,0) — not yet computed
        if cx == 0.0 and cy == 0.0 and cz == 0.0:
            self._flex_hint.setText("Set the box center to auto-fill flexible residues.")
            return
        cutoff = self._flex_dist.value()
        residues = find_flex_residues(self._current_pdb, cx, cy, cz, cutoff)
        self._flex_residues.setText('_'.join(residues))
        # Show count as tooltip
        self._flex_residues.setToolTip(
            f"{len(residues)} residue(s) within {cutoff} Å of box center "
            f"({cx:.2f}, {cy:.2f}, {cz:.2f})\n"
            + (', '.join(residues) if residues else "none found"))
        if residues:
            self._flex_hint.setText(
                f"{len(residues)} residue(s) within {cutoff:g} Å of the box center.")
        else:
            self._flex_hint.setText(
                f"No residues within {cutoff:g} Å — increase Flex Distance.")

    def _update_center_from_ligand(self):
        if not self._current_pdb or not self._components:
            return
        # Find which row has native ligand radio checked
        for row in range(self._comp_table.rowCount()):
            rb = _inner_widget(self._comp_table.cellWidget(row, 7))
            if rb and rb.isChecked() and row < len(self._components):
                comp = self._components[row]
                cx, cy, cz = compute_ligand_center(
                    self._current_pdb, comp['resname'], comp['chain'],
                    comp.get('resseq') or None)
                self._cxx.setValue(cx)
                self._cxy.setValue(cy)
                self._cxz.setValue(cz)
                self._refresh_flex_residues()
                return

    # ── 3D preview (protein + native ligand + dynamic grid box) ─────────
    def _update_preview_structure(self):
        """Load the active receptor and its native ligand into the 3D preview,
        then draw the current grid box. Called when the receptor changes."""
        if not hasattr(self, "_preview3d"):
            return
        if not (self._current_pdb and os.path.isfile(self._current_pdb)):
            return
        self._preview3d.load_receptor(self._current_pdb)
        lig = self._extract_native_ligand_preview()
        if lig and os.path.isfile(lig):
            self._preview3d.load_ligand(lig)
        self._update_preview_box()

    def _extract_native_ligand_preview(self) -> str:
        """Write the radio-selected native ligand to a temp PDB for the preview."""
        if not self._current_pdb or not self._components:
            return ""
        comp = None
        for row in range(self._comp_table.rowCount()):
            rb = _inner_widget(self._comp_table.cellWidget(row, 7))
            if rb and rb.isChecked() and row < len(self._components):
                comp = self._components[row]
                break
        if not comp:
            return ""
        try:
            text = extract_pdb_component(
                self._current_pdb, [comp['chain']], [comp['resname']])
            if not text.strip():
                return ""
            import tempfile
            tmp = tempfile.mkdtemp(prefix="ladock_preview_lig_")
            out = os.path.join(tmp, f"native_{comp['resname']}.pdb")
            with open(out, "w") as fh:
                fh.write(text)
            return out
        except Exception:
            return ""

    def _update_preview_box(self):
        """Redraw the grid box from the current Search box center + size."""
        if not hasattr(self, "_preview3d"):
            return
        self._preview3d.highlight_pocket(
            self._cxx.value(), self._cxy.value(), self._cxz.value(),
            self._sxx.value(), self._sxy.value(), self._sxz.value())

    def _update_center_from_protein(self):
        if not self._current_pdb:
            return
        xs, ys, zs = [], [], []
        with open(self._current_pdb, 'r', errors='replace') as fh:
            for line in fh:
                if line[:4] != 'ATOM':
                    continue
                try:
                    xs.append(float(line[30:38]))
                    ys.append(float(line[38:46]))
                    zs.append(float(line[46:54]))
                except ValueError:
                    pass
        if xs:
            self._cxx.setValue(round(sum(xs)/len(xs), 3))
            self._cxy.setValue(round(sum(ys)/len(ys), 3))
            self._cxz.setValue(round(sum(zs)/len(zs), 3))
            self._refresh_flex_residues()

    # ------------------------------------------------------------------ #
    # Collect params & run
    # ------------------------------------------------------------------ #

    def _log_flex_info(self, residues: list[str], manual: bool):
        """Internal helper: log flex residue info before run."""
        if not hasattr(self, '_log'):
            return
        source = "user-specified" if manual else "auto-detected"
        if residues:
            self._emit_log(
                f"ℹ Flex residues ({source}, {len(residues)}): "
                + ", ".join(residues))
        else:
            self._emit_log(f"ℹ No flex residues {source}.")

    def _collect_docking_params(self) -> dict | None:
        if not self._current_pdb:
            QMessageBox.warning(self, "No File",
                "Select a receptor PDB from the list first.")
            return None

        # Find selected native ligand
        native_chain = native_resname = native_resseq = ""
        for row in range(self._comp_table.rowCount()):
            rb = _inner_widget(self._comp_table.cellWidget(row, 7))
            if rb and rb.isChecked() and row < len(self._components):
                comp = self._components[row]
                native_chain   = comp['chain']
                native_resname = comp['resname']
                native_resseq  = comp.get('resseq', '')
                break

        if not native_resname:
            QMessageBox.warning(self, "No Native Ligand",
                "Select a native ligand (radio button in 'Native Ligand' column).")
            return None

        # Collect receptor components (full component spec)
        rec_chains = []
        rec_components = []
        for row in range(self._comp_table.rowCount()):
            cb = _inner_widget(self._comp_table.cellWidget(row, 6))
            if cb and cb.isChecked() and row < len(self._components):
                comp = self._components[row]
                rec_components.append(comp)
                if comp['chain'] not in rec_chains:
                    rec_chains.append(comp['chain'])

        if not rec_components:
            QMessageBox.warning(self, "No Receptor",
                "Check at least one component as 'As Receptor'.")
            return None

        # Auto center from ligand if mode = ligand
        mode_btn = self._center_grp.checkedButton()
        mode = mode_btn.property("mode") if mode_btn else "ligand"
        if mode == "ligand":
            self._update_center_from_ligand()
        elif mode == "protein":
            self._update_center_from_protein()

        # Collect scoring functions
        sf_types = []
        if self._sf_vina.isChecked():    sf_types.append("vina")
        if self._sf_vinardo.isChecked(): sf_types.append("vinardo")
        if self._sf_ad4.isChecked():     sf_types.append("ad4")
        if self._sf_ad4gpu.isChecked():  sf_types.append("ad4gpu")
        if not sf_types:
            QMessageBox.warning(self, "No Scoring", "Select at least one scoring function.")
            return None

        # Collect docking modes
        listmode = []
        if self._mode_rigid.isChecked():    listmode.append("rigid")
        if self._mode_flexible.isChecked(): listmode.append("flexible")
        if not listmode:
            QMessageBox.warning(self, "No Mode", "Select at least one docking mode.")
            return None

        # Hybrid mode (Windows only): dispatch the Linux-only engines (AD4/AD-GPU
        # and the AutoGrid4/MGLTools grid path) to WSL, while the GUI + Meeko prep
        # stay native. Off ⇒ pure-native Windows (Vina/Vinardo only).
        use_wsl_backend = self._hybrid_enabled()
        wsl_distro = str(QSettings("LADOCK", "Desktop").value("wsl_distro", "")).strip()

        # Resolve tool directories according to the active backend.
        _mgldir = resolve_mgltools_dir(
            self._tp_mgltools.text().strip(),
            use_wsl_backend=use_wsl_backend,
        )
        _adfrsuite = resolve_adfrsuite_dir(
            self._tp_adfr.text().strip() or self._tp_agfr.text().strip(),
            use_wsl_backend=use_wsl_backend,
        )

        # Derive MGLTools script paths from the mgltools directory field
        if _mgldir and os.path.isdir(_mgldir):
            _pythonsh  = os.path.join(_mgldir, "bin", "pythonsh")
            _util24    = os.path.join(_mgldir, "MGLToolsPckgs",
                                      "AutoDockTools", "Utilities24")
            _prep_rec  = os.path.join(_util24, "prepare_receptor4.py")
            _prep_lig  = os.path.join(_util24, "prepare_ligand4.py")
            _prep_gpf  = os.path.join(_util24, "prepare_gpf4.py")
            _prep_dpf  = os.path.join(_util24, "prepare_dpf42.py")
            _prep_flex = os.path.join(_util24, "prepare_flexreceptor4.py")
        else:
            _pythonsh  = "pythonsh"
            _prep_rec  = "prepare_receptor4.py"
            _prep_lig  = "prepare_ligand4.py"
            _prep_gpf  = "prepare_gpf4.py"
            _prep_dpf  = "prepare_dpf42.py"
            _prep_flex = "prepare_flexreceptor4.py"

        return {
            'pdb_path':            self._current_pdb,
            'receptor_chains':     rec_chains,
            'receptor_components': rec_components,
            'native_resname':      native_resname,
            'native_chain':        native_chain,
            # Box
            'cx': self._cxx.value(),
            'cy': self._cxy.value(),
            'cz': self._cxz.value(),
            'sx': self._sxx.value(),
            'sy': self._sxy.value(),
            'sz': self._sxz.value(),
            'box_size':          f"{int(self._sxx.value())},{int(self._sxy.value())},{int(self._sxz.value())}",
            'spacing':           self._spacing.value(),
            # Scoring & mode
            'sf_types':          sf_types,
            'listmode':          listmode,
            # Search — separate exhaustiveness per engine type
            'exhaustiveness':        self._exhaustiveness.value(),
            'ad4_exhaustiveness':    self._ad4_exhaustiveness.value(),
            'n_poses':           self._n_poses.value(),
            'energy_range':      self._energy_range.value(),
            'cpu':               self._cpu.value(),
            'max_workers':       self._max_workers.value(),
            # Advanced
            'seed':              self._seed.value(),
            'ga_pop_size':       self._ga_pop_size.value(),
            'cluster_rmsd':      self._cluster_rmsd.value(),
            # Flexible receptor
            'distance':          self._flex_dist.value(),
            'flexible_residues': self._flex_residues.text().strip(),
            # I/O options
            'input_file_saved':  str(self._save_input.isChecked()).lower(),
            'output_file_saved': str(self._save_output.isChecked()).lower(),
            'parallel_simulation': str(self._parallel.isChecked()).lower(),
            # Tool paths
            'vina_path':         resolve_tool_path(
                "vina", self._tp_vina.text().strip(), use_wsl_backend=use_wsl_backend
            ),
            'ag4_path':          resolve_tool_path(
                "autogrid4", self._tp_ag4.text().strip(), use_wsl_backend=use_wsl_backend
            ),
            'ad4_path':          resolve_tool_path(
                "autodock4", self._tp_ad4.text().strip(), use_wsl_backend=use_wsl_backend
            ),
            'autodockgpu':       resolve_tool_path(
                "autodock_gpu", self._tp_adgpu.text().strip(), use_wsl_backend=use_wsl_backend
            ),
            'agfr':              resolve_tool_path(
                "agfr",
                os.path.join(_adfrsuite, "bin", "agfr") if _adfrsuite else self._tp_agfr.text().strip(),
                use_wsl_backend=use_wsl_backend,
            ),
            'adfr':              resolve_tool_path(
                "adfr",
                os.path.join(_adfrsuite, "bin", "adfr") if _adfrsuite else self._tp_adfr.text().strip(),
                use_wsl_backend=use_wsl_backend,
            ),
            # MGLTools-derived paths
            'pythonsh':          _pythonsh,
            'prepare_receptor':  _prep_rec,
            'prepare_ligand':    _prep_lig,
            'prepare_gpf':       _prep_gpf,
            'prepare_dpf':       _prep_dpf,
            'prepare_flexreceptor': _prep_flex,
            'use_wsl_backend':   use_wsl_backend,
            'wsl_distro':        wsl_distro,
        }

    def _on_run(self):
        """Redock every checked protein iteratively, each with its own
        components + docking parameters (sequential via a small batch queue)."""
        if self._current_pdb and os.path.isfile(self._current_pdb):
            self._save_comp_state(self._current_pdb)
            self._rec_config[self._current_pdb] = self._capture_config()

        targets = [p for p in sorted(self._checked_receptors) if os.path.isfile(p)]
        if not targets and self._current_pdb:
            targets = [self._current_pdb]     # fall back to the selected receptor
        if not targets:
            QMessageBox.warning(self, "No Receptor",
                "Select or check at least one receptor to redock.")
            return

        self._batch_queue = list(targets)
        self._run_next_in_batch()

    def _run_next_in_batch(self):
        """Start the next checked receptor's redocking job, if any."""
        while self._batch_queue:
            path = self._batch_queue.pop(0)
            self._activate_receptor(path)
            before = self._current_job
            self._run_for_current_receptor()
            if self._current_job is not before:   # a job actually started
                return
            # validation failed for this receptor → try the next one

    def _run_for_current_receptor(self):
        params = self._collect_docking_params()
        if params is None:
            return

        if params.get('use_wsl_backend') and os.name == "nt" and not wsl_available():
            QMessageBox.warning(
                self, "WSL Not Found",
                "WSL backend is enabled, but `wsl.exe` is not available on this Windows system."
            )
            return

        # Validate: flexible mode requires "To Ligand" center
        if 'flexible' in params.get('listmode', []):
            mode_btn = self._center_grp.checkedButton()
            center_mode = mode_btn.property("mode") if mode_btn else "ligand"
            if center_mode != "ligand":
                QMessageBox.warning(self, "Flexible Mode Constraint",
                    "Flexible receptor mode requires 'Box Center → To Ligand'.\n"
                    "Please switch the center mode or use Rigid mode.")
                return

        # Receptor/ligand PDBQT prep is done natively by Meeko. MGLTools is only
        # still required for the AutoDock4 / AutoDock-GPU grid path (autogrid4 +
        # prepare_gpf4) and for flexible-receptor splitting (prepare_flexreceptor4).
        sf_types = params.get('sf_types', [])
        needs_mgltools = (
            'flexible' in params.get('listmode', [])
            or any(sf in ('ad4', 'ad4gpu') for sf in sf_types)
        )
        if needs_mgltools:
            pythonsh = params['pythonsh']
            missing = []
            if not (pythonsh and os.path.isfile(pythonsh)):
                missing.append(f"pythonsh: {pythonsh}")
            if not os.path.isfile(params['prepare_gpf']):
                missing.append(f"prepare_gpf4.py: {params['prepare_gpf']}")
            if 'flexible' in params.get('listmode', []) and \
                    not os.path.isfile(params['prepare_flexreceptor']):
                missing.append(
                    f"prepare_flexreceptor4.py: {params['prepare_flexreceptor']}")
            if missing:
                QMessageBox.warning(self, "MGLTools Not Found",
                    "AutoDock4 / AutoDock-GPU and flexible-receptor mode still "
                    "require MGLTools:\n\n" +
                    "\n".join(f"  • {m}" for m in missing) +
                    "\n\nUse Vina/Vinardo (native, no MGLTools) or configure "
                    "MGLTools Path in Tool Paths section.")
                return
        elif not meeko_available():
            QMessageBox.warning(self, "Meeko Not Installed",
                "Native ligand/receptor preparation needs Meeko.\n\n"
                "Install it with:  pip install meeko\n\n"
                "Or configure MGLTools Path to use the legacy prep pipeline.")
            return

        # Auto-compute flex residues if flexible mode is on
        flex_residues_list = []
        if 'flexible' in params.get('listmode', []):
            flex_dist = params.get('distance', 3.0)
            current_field = self._flex_residues.text().strip()
            if current_field:
                # Field already auto-filled or user-typed — use as-is
                flex_residues_list = [r.strip() for r in current_field.split('_') if r.strip()]
                self._log_flex_info(flex_residues_list, manual=True)
            else:
                # Fallback: compute now from box center
                flex_residues_list = find_flex_residues(
                    params['pdb_path'],
                    cx=params['cx'], cy=params['cy'], cz=params['cz'],
                    cutoff=flex_dist)
                self._flex_residues.setText('_'.join(flex_residues_list))
                self._log_flex_info(flex_residues_list, manual=False)
            if not flex_residues_list:
                QMessageBox.warning(self, "No Flex Residues",
                    f"No protein residues found within {flex_dist} Å of box center.\n"
                    "Try increasing the Flex Residue Distance or use Rigid mode.")
                return
        params['flex_residues_list'] = flex_residues_list

        # Extract receptor and ligand to temp PDB files
        tmp_dir  = tempfile.mkdtemp(prefix="ladock_redock_")
        rec_pdb  = os.path.join(tmp_dir, "receptor.pdb")
        lig_pdb  = os.path.join(tmp_dir, "ligand.pdb")

        # Use component-aware extraction so only user-checked components
        # (e.g. protein chains A/B/C but NOT metals, waters, etc.) are included
        rec_text = extract_pdb_component(
            params['pdb_path'],
            params['receptor_chains'],
            components=params.get('receptor_components'))
        Path(rec_pdb).write_text(rec_text, encoding='utf-8')

        lig_text = extract_pdb_component(
            params['pdb_path'],
            [params['native_chain']],
            [params['native_resname']])
        Path(lig_pdb).write_text(lig_text, encoding='utf-8')

        self._result_rows = []
        self._run_btn.setEnabled(False)
        self._prog.setVisible(True)
        # Cache display names for result table columns
        self._rec_name = os.path.splitext(os.path.basename(params['pdb_path']))[0]
        self._lig_name = params.get('native_resname', '')
        # Cache rec_pdbqt path for meta JSON (will be written after docking)
        self._last_rec_pdbqt  = os.path.join(tmp_dir, 'receptor.pdbqt')
        self._last_tmp_dir    = tmp_dir
        self._last_ligand_pdb = lig_pdb
        self._last_ligand_resname = params.get('native_resname', '')
        self._last_ligand_smiles = (
            smiles_from_structure(lig_pdb, wsl_distro=params.get('wsl_distro', ''))
            or smiles_from_ccd(params.get('native_resname', ''))
        )

        # Register job for tracking
        from core.job_scheduler import DockingJob, JobStatus
        rec_tag = os.path.splitext(os.path.basename(params['pdb_path']))[0][:20]
        sf_tag  = '+'.join(params['sf_types'])
        job_name = f"Redock {rec_tag} [{sf_tag}]"
        self._current_job = DockingJob(
            job_id     = uuid.uuid4().hex[:8],
            name       = job_name,
            parameters = {'receptor': params['pdb_path'],
                          'sf_types': params['sf_types'],
                          'mode':     params['listmode']},
            status     = JobStatus.RUNNING,
            progress   = 0,
            created_at  = datetime.datetime.now().isoformat(),
            started_at  = datetime.datetime.now().isoformat(),
        )
        self.job_registered.emit(self._current_job)

        # Job header — streamed live to the Jobs tab log
        flex_info = (f"\nFlex residues   : {', '.join(flex_residues_list)}"
                     if flex_residues_list else "")
        rec_comp_labels = [
            f"{c.get('resname','?')} ({c.get('type','?')})"
            for c in params.get('receptor_components', [])]
        self._emit_log(
            f"Receptor chains : {params['receptor_chains']}\n"
            f"Receptor comps  : {', '.join(rec_comp_labels)}\n"
            f"Native ligand   : {params['native_resname']} "
            f"(chain {params['native_chain']})\n"
            f"Center          : ({params['cx']:.2f}, {params['cy']:.2f}, "
            f"{params['cz']:.2f})\n"
            f"Box             : {params['sx']}×{params['sy']}×{params['sz']} Å\n"
            f"Scoring         : {', '.join(params['sf_types'])}\n"
            f"Mode            : {', '.join(params['listmode'])}"
            f"{flex_info}\n"
            f"Exhaustiveness  : {params['exhaustiveness']}\n"
            f"Working dir     : {tmp_dir}\n"
            "─────────────────────────────────────────────")

        worker_params = {
            **params,
            'tmp_dir':      tmp_dir,
            'receptor_pdb': rec_pdb,
            'ligand_pdb':   lig_pdb,
        }

        self._thread = QThread()
        self._worker = _DockingWorker(worker_params)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(
            lambda m, jid=self._current_job.job_id: self.job_log_line.emit(jid, m))
        self._worker.progress.connect(
            lambda m, jid=self._current_job.job_id: self.job_log_line.emit(jid, f"⏳ {m}"))
        self._worker.finished.connect(self._on_docking_done)
        self._worker.error.connect(self._on_docking_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    @Slot(object)
    def _on_docking_done(self, results: list):
        self._prog.setVisible(False)
        self._run_btn.setEnabled(True)
        self._run_next_in_batch()   # continue with the next checked protein
        self._result_rows = []
        for sf, path in results:
            if sf in ('ad4', 'ad4gpu'):
                self._parse_dlg_output(path, sf_label=sf)
            else:
                self._parse_vina_output(path, sf_label=sf)
        total = len(self._result_rows)
        self._emit_log(
            f"\n✔  Docking complete — {total} pose(s) across "
            f"{len(results)} scoring function(s)")
        if results:
            self.docking_finished.emit(results[0][1])
        # Save results to CSV and notify
        csv_path = self._save_result_csv(results)
        if csv_path:
            self._emit_log(f"📄 Results CSV: {csv_path}")
            self.result_csv_ready.emit(csv_path)
        # Update job tracker
        if self._current_job:
            from core.job_scheduler import JobStatus
            self._current_job.status      = JobStatus.FINISHED
            self._current_job.progress    = 100
            self._current_job.finished_at = datetime.datetime.now().isoformat()
            self._current_job.result_csv  = csv_path
            self.job_status_changed.emit(self._current_job)

    def _save_result_csv(self, results: list) -> str:
        """
        Save all poses from the result table to a timestamped CSV.
        Returns the CSV path, or empty string on failure.
        """
        import csv as _csv
        if not self._result_rows:
            return ""
        try:
            out_dir = os.path.join(self._job_dir, "results") if self._job_dir else ""
            if not out_dir:
                out_dir = os.path.dirname(results[0][1]) if results else ""
            os.makedirs(out_dir, exist_ok=True)

            ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            rec_tag = os.path.splitext(
                os.path.basename(self._current_pdb))[0][:20] if self._current_pdb else "run"
            csv_path = os.path.join(out_dir, f"results_{rec_tag}_{ts}.csv")

            smiles  = getattr(self, '_last_ligand_smiles', '')
            headers = ["Receptor", "Ligand", "smiles", "Scoring", "Pose",
                       "ΔG (kcal/mol)", "RMSD lb", "RMSD ub"]
            rows_meta = []
            with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
                writer = _csv.writer(fh)
                writer.writerow(headers)
                for r in self._result_rows:
                    writer.writerow([
                        r["receptor"], r["ligand"], smiles, r["sf"], r["pose"],
                        r["energy"], r["rmsd_lb"], r["rmsd_ub"],
                    ])
                    rows_meta.append({
                        "output_path": r.get("out_path", ""),
                        "sf": r["sf"],
                        "smiles": smiles,
                        "ligand_resname": getattr(self, '_last_ligand_resname', ''),
                    })

            # Write companion meta JSON for 3D/interaction lookup
            import json as _json
            meta = {
                "receptor_pdbqt": getattr(self, '_last_rec_pdbqt', ''),
                "receptor_pdb":   self._current_pdb,
                "ligand_pdb":     getattr(self, '_last_ligand_pdb', ''),
                "ligand_smiles":  getattr(self, '_last_ligand_smiles', ''),
                "ligand_resname": getattr(self, '_last_ligand_resname', ''),
                "rows": rows_meta,
            }
            meta_path = csv_path.replace('.csv', '.meta.json')
            with open(meta_path, 'w', encoding='utf-8') as fh:
                _json.dump(meta, fh, indent=2)

            return csv_path
        except Exception as e:
            self._emit_log(f"⚠ Could not save CSV: {e}")
            return ""

    @Slot(str)
    def _on_docking_error(self, msg: str):
        self._prog.setVisible(False)
        self._run_btn.setEnabled(True)
        self._run_next_in_batch()   # continue with the next checked protein
        self._emit_log(f"\n❌ Error: {msg}")
        if self._current_job:
            from core.job_scheduler import JobStatus
            self._current_job.status      = JobStatus.FAILED
            self._current_job.error       = msg
            self._current_job.finished_at = datetime.datetime.now().isoformat()
            self.job_status_changed.emit(self._current_job)

    def _parse_vina_output(self, pdbqt_path: str, sf_label: str = "vina"):
        """Parse Vina PDBQT output and append rows to results table."""
        try:
            text = Path(pdbqt_path).read_text(encoding='utf-8', errors='replace')
        except OSError:
            self._emit_log(f"⚠ Cannot read output: {pdbqt_path}")
            return
        pose = 0
        for line in text.splitlines():
            if line.startswith('REMARK VINA RESULT:'):
                parts = line.split()
                if len(parts) >= 5:
                    pose += 1
                    energy  = parts[3]
                    rmsd_lb = parts[4]
                    rmsd_ub = parts[5] if len(parts) > 5 else "—"
                    self._append_result_row(sf_label, pose, energy, rmsd_lb, rmsd_ub,
                                            out_path=pdbqt_path)
        self._emit_log(f"  [{sf_label}] {pose} pose(s) → {pdbqt_path}")

    def _parse_dlg_output(self, dlg_path: str, sf_label: str = "ad4"):
        """Parse AutoDock4 / AutoDock-GPU DLG output and append rows to results table."""
        try:
            text = Path(dlg_path).read_text(encoding='utf-8', errors='replace')
        except OSError:
            self._emit_log(f"⚠ Cannot read output: {dlg_path}")
            return
        import re
        pose = 0
        parsed_rows: set[int] = set()

        # Prefer explicit ranking rows when available.
        for line in text.splitlines():
            m = re.match(
                r'\s*RANKING\s+(\d+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)', line)
            if m:
                pose_idx = int(m.group(1))
                self._append_result_row(
                    sf_label, pose_idx, m.group(2), m.group(3), m.group(4),
                    out_path=dlg_path)
                parsed_rows.add(pose_idx)
        if parsed_rows:
            self._emit_log(f"  [{sf_label}] {len(parsed_rows)} pose(s) → {dlg_path}")
            return

        energy = rmsd = None
        for line in text.splitlines():
            if 'DOCKED: MODEL' in line or line.startswith('DOCKED: MODEL'):
                pose += 1
                energy = rmsd = None
            elif 'Estimated Free Energy of Binding' in line:
                m = re.search(r'=\s*([-\d.]+)', line)
                if m:
                    energy = m.group(1)
            elif 'RMSD from reference' in line and energy is not None:
                m = re.search(r'=\s*([\d.]+)', line)
                if m:
                    rmsd = m.group(1)
            if energy is not None and rmsd is not None:
                self._append_result_row(sf_label, pose, energy, rmsd, "—",
                                        out_path=dlg_path)
                parsed_rows.add(pose)
                energy = rmsd = None
        if not parsed_rows:
            for line in text.splitlines():
                m = re.match(
                    r'\s*(\d+)\s+\|\s*([-\d.]+)\s+\|\s*([\d.]+)\s+\|\s*([\d.]+)', line)
                if m:
                    pose = int(m.group(1))
                    self._append_result_row(
                        sf_label, pose,
                        m.group(2), m.group(3), m.group(4),
                        out_path=dlg_path)
                    parsed_rows.add(pose)
        if parsed_rows:
            self._emit_log(f"  [{sf_label}] {len(parsed_rows)} pose(s) → {dlg_path}")
        else:
            self._emit_log(f"⚠ No docking result found in {dlg_path}")

    def _append_result_row(self, sf_label: str, pose: int,
                           energy: str, rmsd_lb: str, rmsd_ub: str,
                           out_path: str = ""):
        """Accumulate one docked pose (persisted to CSV → Results tab)."""
        self._result_rows.append({
            "receptor": self._rec_name,
            "ligand":   self._lig_name,
            "sf":       sf_label,
            "pose":     str(pose),
            "energy":   energy,
            "rmsd_lb":  rmsd_lb,
            "rmsd_ub":  rmsd_ub,
            "out_path": out_path,
        })
