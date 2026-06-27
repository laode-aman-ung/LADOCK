"""
LADOCK — Ligand Test Docking Panel
=====================================
Panel untuk docking semua ligan dari ligand_ready/ atau ligand_input/
ke reseptor yang dipilih dari receptor_ready/.

Mirip dengan NativeRedockingPanel tetapi:
  - Tidak ada Native Ligand selection (hanya receptor)
  - Menampilkan daftar ligan dari ligand_ready/ atau ligand_input/
  - Menjalankan docking untuk setiap ligan
  - Menampilkan tabel hasil: best pose per ligan
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import datetime
import uuid
from pathlib import Path
from typing import List, Tuple

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QListWidget,
    QListWidgetItem, QGroupBox, QDoubleSpinBox, QSpinBox,
    QComboBox, QTextEdit, QSplitter, QCheckBox, QProgressBar,
    QMessageBox, QAbstractItemView, QRadioButton, QButtonGroup,
    QFrame, QLineEdit, QStackedWidget, QAbstractSpinBox
)
from PySide6.QtCore import Qt, Signal, Slot, QFileSystemWatcher, QObject, QThread, QSettings
from PySide6.QtGui import QColor, QBrush, QDesktopServices, QFont
from PySide6.QtCore import QUrl

from gui.widgets.common import SectionLabel, HDivider
from gui import theme
from gui.panels.native_redocking_panel import (
    NativeRedockingPanel, parse_pdb_components,
    _GRP, _SPIN, _COMBO, _TYPE_COLOR,
    compute_ligand_center, extract_pdb_component, sanitize_pdb_text_for_mgltools,
    _centered_widget, _inner_widget, find_flex_residues
)
from core.tool_paths import (
    resolve_adfrsuite_dir,
    resolve_mgltools_dir,
    resolve_tool_path,
)
from core.ligand_smiles import smiles_from_structure
from core.wsl_backend import prepare_subprocess, wsl_available


class _LigandDockingWorker(QObject):
    """
    Worker that docks a list of ligand files against a receptor.

    `finished` emits list[dict]:
        [{"lig_name","sf","out_path","smiles","source_path"}, ...]
    """
    log          = Signal(str)
    progress     = Signal(str)
    progress_pct = Signal(int)   # 0-100 per-ligand percent
    finished     = Signal(object)
    error        = Signal(str)

    def __init__(self, params: dict):
        super().__init__()
        self._p = params
        self._use_wsl_backend = bool(params.get('use_wsl_backend'))
        self._wsl_distro = str(params.get('wsl_distro', '')).strip()

    # ------------------------------------------------------------------ #
    def _run_cmd(self, cmd: list, tag: str, cwd: str = None):
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
                        "— continuing (non-standard atoms may be skipped).")
                else:
                    raise RuntimeError(f"{tag} failed (exit code {proc.returncode})")
        except FileNotFoundError:
            raise RuntimeError(f"Executable not found: {exec_cmd[0]}")

    def _persist_copy(self, src_path: str, dest_path: str) -> str:
        if not src_path or not os.path.isfile(src_path):
            return ""
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(src_path, dest_path)
            return dest_path
        except OSError:
            return ""

    def _persist_converted_ligand(self, src_path: str, idx: int, mol_name: str) -> str:
        dest_root = str(self._p.get('persistent_ligand_pdbqt_dir', '') or '').strip()
        if not dest_root:
            return ""
        safe_name = re.sub(r'[^\w\-.]', '_', mol_name) or f'lig_{idx}'
        return self._persist_copy(src_path, os.path.join(dest_root, f"{idx:03d}_{safe_name}.pdbqt"))

    def _persist_docking_output(self, src_path: str, lig_name: str, mode: str, sf: str) -> str:
        dest_root = str(self._p.get('persistent_output_dir', '') or '').strip()
        if not dest_root:
            return ""
        safe_name = re.sub(r'[^\w\-.]', '_', lig_name) or "ligand"
        ext = os.path.splitext(src_path)[1] or ".pdbqt"
        return self._persist_copy(src_path, os.path.join(dest_root, safe_name, mode, f"{sf}{ext}"))

    def _lig_fallback_obabel(self, src_pdb: str, out_pdbqt: str, log_fn) -> bool:
        """Convert ligand PDB → PDBQT via obabel. Returns True on success."""
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

    # ------------------------------------------------------------------ #
    def _prep_receptor(self, tmp: str) -> tuple[str, str | None, str | None]:
        """
        Convert receptor PDB → PDBQT.
        Returns (rec_pdbqt, flex_pdbqt_or_None, rigid_pdbqt_or_None).
        """
        p = self._p
        pythonsh  = p['pythonsh']
        prep_rec  = p['prepare_receptor']
        prep_flex = p['prepare_flexreceptor']
        rec_pdbqt = os.path.join(tmp, 'receptor.pdbqt')

        self.progress.emit("Preparing receptor PDBQT…")
        self._run_cmd_warn(
            [pythonsh, prep_rec, '-r', p['receptor_pdb'], '-o', rec_pdbqt,
             '-A', 'hydrogens', '-U', 'nphs_lps'],
            "prepare_receptor4.py", rec_pdbqt)
        if not os.path.isfile(rec_pdbqt):
            raise RuntimeError(
                f"prepare_receptor4.py did not produce: {rec_pdbqt}\n"
                "Ensure receptor PDB contains only ATOM records and is clean.")

        flex_residues = p.get('flex_residues_list', [])
        if flex_residues and 'flexible' in p.get('listmode', []):
            flex_spec   = '_'.join(flex_residues)
            rigid_pdbqt = os.path.join(tmp, 'rigid.pdbqt')
            flex_pdbqt  = os.path.join(tmp, 'flex.pdbqt')
            self.progress.emit(
                f"Splitting flexible residues ({len(flex_residues)})…")
            self._run_cmd([
                pythonsh, prep_flex,
                '-r', rec_pdbqt, '-s', flex_spec,
                '-g', rigid_pdbqt, '-x', flex_pdbqt,
            ], "prepare_flexreceptor4.py")
            if not os.path.isfile(rigid_pdbqt) or not os.path.isfile(flex_pdbqt):
                raise RuntimeError(
                    "prepare_flexreceptor4.py did not produce rigid/flex PDBQT.\n"
                    f"Residue spec: {flex_spec}")
            receptor_dir = str(p.get('persistent_receptor_dir', '') or '').strip()
            if receptor_dir:
                self._persist_copy(rec_pdbqt, os.path.join(receptor_dir, 'receptor.pdbqt'))
                self._persist_copy(rigid_pdbqt, os.path.join(receptor_dir, 'rigid.pdbqt'))
                self._persist_copy(flex_pdbqt, os.path.join(receptor_dir, 'flex.pdbqt'))
            return rec_pdbqt, flex_pdbqt, rigid_pdbqt

        receptor_dir = str(p.get('persistent_receptor_dir', '') or '').strip()
        if receptor_dir:
            self._persist_copy(rec_pdbqt, os.path.join(receptor_dir, 'receptor.pdbqt'))
        return rec_pdbqt, None, None

    def _convert_ligand(self, lig_path: str, tmp: str, idx: int) -> List[dict]:
        """
        Convert ligand file to one or more PDBQT files.
        Returns list of per-molecule dicts.
        Multi-molecule files (SDF, MOL2, SMI, CSV…) expand to multiple entries.
        Raises RuntimeError if conversion fails completely.
        """
        from core.ligand_importer import expand_to_pdbqt
        p        = self._p
        lig_dir  = os.path.join(tmp, f'lig_{idx}_converted')
        os.makedirs(lig_dir, exist_ok=True)

        results = expand_to_pdbqt(
            lig_path,
            out_dir    = lig_dir,
            pythonsh   = p.get('pythonsh', ''),
            prep_lig   = p.get('prepare_ligand', ''),
            use_wsl_backend = p.get('use_wsl_backend', False),
            wsl_distro = p.get('wsl_distro', ''),
            idx_offset = idx * 1000,
            log_fn     = lambda msg: self.log.emit(msg),
        )
        if not results:
            raise RuntimeError(
                f"Cannot convert {os.path.basename(lig_path)} to PDBQT.\n"
                "Ensure obabel is installed or MGLTools path is configured.")
        smiles_map = self._build_input_smiles_map(lig_path, results)
        mol_infos = []
        for mol_name, pdbqt_path in results:
            persisted = self._persist_converted_ligand(pdbqt_path, idx, mol_name)
            mol_infos.append({
                "name": mol_name,
                "pdbqt_path": persisted or pdbqt_path,
                "source_path": lig_path,
                "smiles": smiles_map.get(mol_name, ""),
            })
        return mol_infos

    def _iter_convert_ligand(self, lig_path: str, tmp: str, idx: int):
        ext = os.path.splitext(lig_path)[1].lower()
        if ext in ('.smi', '.smiles', '.txt', '.csv', '.tsv', '.xlsx', '.xls', '.sdf', '.mol2'):
            from core.ligand_importer import (
                count_molecules,
                iter_delimited_to_pdbqt,
                iter_excel_to_pdbqt,
                iter_mol2_to_pdbqt,
                iter_sdf_to_pdbqt,
                iter_smiles_file_to_pdbqt,
            )
            p = self._p
            lig_dir = os.path.join(tmp, f'lig_{idx}_converted')
            os.makedirs(lig_dir, exist_ok=True)
            n_mols = count_molecules(lig_path)
            if n_mols > 1:
                self.log.emit(f"  ↳ Streaming {n_mols} molecule(s)")
            base = os.path.splitext(os.path.basename(lig_path))[0]
            common = dict(
                lig_path=lig_path,
                out_dir=lig_dir,
                base=base,
                idx_offset=idx * 1000,
                log_fn=lambda msg: self.log.emit(msg),
                use_wsl_backend=p.get('use_wsl_backend', False),
                wsl_distro=p.get('wsl_distro', ''),
            )
            if ext in ('.smi', '.smiles', '.txt'):
                mol_iter = iter_smiles_file_to_pdbqt(**common)
            elif ext in ('.csv', '.tsv'):
                mol_iter = iter_delimited_to_pdbqt(sep=',' if ext == '.csv' else '\t', **common)
            elif ext in ('.xlsx', '.xls'):
                mol_iter = iter_excel_to_pdbqt(**common)
            elif ext == '.sdf':
                mol_iter = iter_sdf_to_pdbqt(
                    pythonsh=p.get('pythonsh', ''),
                    prep_lig=p.get('prepare_ligand', ''),
                    **common,
                )
            else:
                mol_iter = iter_mol2_to_pdbqt(**common)

            for item in mol_iter:
                if len(item) == 3:
                    mol_name, pdbqt_path, smiles = item
                else:
                    mol_name, pdbqt_path = item
                    smiles = ""
                persisted = self._persist_converted_ligand(pdbqt_path, idx, mol_name)
                yield {
                    "name": mol_name,
                    "pdbqt_path": persisted or pdbqt_path,
                    "source_path": lig_path,
                    "smiles": smiles,
                }
            return

        for mol_info in self._convert_ligand(lig_path, tmp, idx):
            yield mol_info

    def _build_input_smiles_map(self, lig_path: str,
                                results: List[Tuple[str, str]]) -> dict[str, str]:
        ext = os.path.splitext(lig_path)[1].lower()
        try:
            from core.ligand_importer import _find_smiles_and_name_cols, _looks_like_smiles
        except Exception:
            _find_smiles_and_name_cols = None
            _looks_like_smiles = None

        if ext in ('.smi', '.smiles', '.txt') and _looks_like_smiles:
            out: dict[str, str] = {}
            try:
                lines = Path(lig_path).read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                return out
            idx = 0
            for raw_line in lines:
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(None, 1)
                smiles = parts[0]
                name = parts[1].strip() if len(parts) > 1 else results[idx][0] if idx < len(results) else ""
                if not _looks_like_smiles(smiles):
                    if len(parts) > 1 and _looks_like_smiles(parts[1].strip()):
                        name, smiles = smiles, parts[1].strip()
                    else:
                        continue
                safe_name = re.sub(r'[^\w\-.]', '_', name) if name else ""
                if safe_name:
                    out[safe_name] = smiles
                idx += 1
            return out

        if ext in ('.csv', '.tsv') and _find_smiles_and_name_cols and _looks_like_smiles:
            out: dict[str, str] = {}
            sep = ',' if ext == '.csv' else '\t'
            import csv as _csv
            try:
                with open(lig_path, newline='', encoding='utf-8', errors='replace') as fh:
                    reader = _csv.reader(fh, delimiter=sep)
                    headers = next(reader, None)
                    if headers is None:
                        return out
                    smiles_idx, name_idx = _find_smiles_and_name_cols(headers)
                    for row in reader:
                        if not row:
                            continue
                        smiles = ""
                        if 0 <= smiles_idx < len(row):
                            smiles = row[smiles_idx].strip()
                        if not _looks_like_smiles(smiles):
                            continue
                        name = row[name_idx].strip() if 0 <= name_idx < len(row) else ""
                        safe_name = re.sub(r'[^\w\-.]', '_', name) if name else ""
                        if safe_name:
                            out[safe_name] = smiles
            except Exception:
                return out
            return out

        if ext in ('.xlsx', '.xls'):
            out: dict[str, str] = {}
            try:
                import pandas as pd
                from core.ligand_importer import _find_smiles_and_name_cols, _looks_like_smiles
                df = pd.read_excel(lig_path)
                headers = list(df.columns)
                smiles_idx, name_idx = _find_smiles_and_name_cols(headers)
                if smiles_idx < 0:
                    return out
                smiles_col = headers[smiles_idx]
                name_col = headers[name_idx] if name_idx >= 0 else None
                for _, row in df.iterrows():
                    smiles = str(row[smiles_col]).strip()
                    if not _looks_like_smiles(smiles):
                        continue
                    name = str(row[name_col]).strip() if name_col else ""
                    safe_name = re.sub(r'[^\w\-.]', '_', name) if name else ""
                    if safe_name:
                        out[safe_name] = smiles
            except Exception:
                return out
            return out

        if len(results) == 1 and ext in ('.pdb', '.mol', '.sdf', '.mol2'):
            smiles = smiles_from_structure(lig_path, wsl_distro=self._wsl_distro)
            if smiles:
                return {results[0][0]: smiles}
        return {}

    def _build_ad4_grids(self, tmp: str, rec_pdbqt: str, lig_pdbqt: str,
                         flex_pdbqt: str | None, lig_idx: int) -> tuple[str, str]:
        """Build AutoGrid4 maps. Returns (fld_path, gpf_path)."""
        p        = self._p
        pythonsh = p['pythonsh']
        prep_gpf = p['prepare_gpf']
        ag4      = p['ag4_path']
        sub_tmp  = os.path.join(tmp, f'grid_{lig_idx}')
        os.makedirs(sub_tmp, exist_ok=True)
        gpf_path = os.path.join(sub_tmp, 'grid.gpf')
        glg_path = os.path.join(sub_tmp, 'grid.glg')
        local_rec = os.path.join(sub_tmp, os.path.basename(rec_pdbqt))
        local_lig = os.path.join(sub_tmp, os.path.basename(lig_pdbqt))
        if os.path.abspath(rec_pdbqt) != os.path.abspath(local_rec):
            shutil.copy2(rec_pdbqt, local_rec)
        if os.path.abspath(lig_pdbqt) != os.path.abspath(local_lig):
            shutil.copy2(lig_pdbqt, local_lig)
        local_flex = None
        if flex_pdbqt:
            local_flex = os.path.join(sub_tmp, os.path.basename(flex_pdbqt))
            if os.path.abspath(flex_pdbqt) != os.path.abspath(local_flex):
                shutil.copy2(flex_pdbqt, local_flex)

        spacing = p.get('spacing', 0.375)
        nx = max(2, round(p['sx'] / spacing))
        ny = max(2, round(p['sy'] / spacing))
        nz = max(2, round(p['sz'] / spacing))
        nx += nx % 2; ny += ny % 2; nz += nz % 2

        self.progress.emit(f"Generating AutoGrid4 GPF (lig {lig_idx})…")
        cmd = [
            pythonsh, prep_gpf,
            '-r', local_rec, '-l', local_lig, '-o', gpf_path,
            '-p', f'npts={nx},{ny},{nz}',
            '-p', f'spacing={spacing}',
            '-p', f'gridcenter={p["cx"]},{p["cy"]},{p["cz"]}',
        ]
        if local_flex:
            cmd += ['-x', local_flex]
        self._run_cmd(cmd, "prepare_gpf4.py")
        if not os.path.isfile(gpf_path):
            raise RuntimeError(f"prepare_gpf4.py did not produce: {gpf_path}")

        self.progress.emit(f"Running AutoGrid4 (lig {lig_idx})…")
        self._run_cmd(
            [ag4, '-p', gpf_path, '-l', glg_path],
            "autogrid4", cwd=sub_tmp)

        fld_files = [f for f in os.listdir(sub_tmp) if f.endswith('.maps.fld')]
        if not fld_files:
            raise RuntimeError(
                f"AutoGrid4 did not produce .maps.fld.\nCheck: {glg_path}")
        fld_path = os.path.join(sub_tmp, fld_files[0])
        return fld_path, gpf_path

    # ------------------------------------------------------------------ #
    @Slot()
    def run(self):
        p   = self._p
        tmp = p['tmp_dir']

        # Derive MGLTools helper paths
        _mgldir = os.path.dirname(os.path.dirname(p.get('prepare_receptor', '')))
        _util24 = os.path.join(_mgldir, 'MGLToolsPckgs',
                               'AutoDockTools', 'Utilities24')
        p.setdefault('prepare_dpf',
                     os.path.join(_util24, 'prepare_dpf42.py'))
        p.setdefault('prepare_gpf',
                     os.path.join(_util24, 'prepare_gpf4.py'))
        p.setdefault('prepare_flexreceptor',
                     os.path.join(_util24, 'prepare_flexreceptor4.py'))

        try:
            sf_types  = p['sf_types']
            lig_files = p['ligand_files']

            # Step 1: Prepare receptor
            rec_pdbqt, flex_pdbqt, rigid_pdbqt = self._prep_receptor(tmp)

            results: list[dict] = []
            n_failed = 0
            total_mols = 0
            for file_idx, lig_path in enumerate(lig_files, start=1):
                input_name = os.path.basename(lig_path)
                self.log.emit(
                    f"\n{'─'*50}\n"
                    f"  Converting [{file_idx}/{len(lig_files)}]: {input_name}")
                had_molecule = False
                try:
                    mol_iter = self._iter_convert_ligand(lig_path, tmp, file_idx)
                    for mol_info in mol_iter:
                        had_molecule = True
                        total_mols += 1
                        mol_name = mol_info["name"]
                        lig_pdbqt = mol_info["pdbqt_path"]
                        mol_smiles = mol_info.get("smiles", "")
                        source_path = mol_info.get("source_path", "")
                        self.log.emit(
                            f"\n{'─'*50}\n"
                            f"  Molecule [{total_mols}]: {mol_name}")
                        lig_tmp = os.path.join(tmp, f'mol_{total_mols}_dock')
                        os.makedirs(lig_tmp, exist_ok=True)

                        lig_name = mol_name
                        idx = total_mols

                        try:
                            listmode = p.get('listmode', ['rigid'])
                            for mode in listmode:
                                use_flex = (mode == 'flexible' and flex_pdbqt and rigid_pdbqt)
                                _flex    = flex_pdbqt  if use_flex else None
                                _rigid   = rigid_pdbqt if use_flex else None
                                mode_dir = os.path.join(lig_tmp, mode)
                                os.makedirs(mode_dir, exist_ok=True)

                                for sf in [s for s in sf_types if s in ('vina', 'vinardo')]:
                                    out_pdbqt = os.path.join(mode_dir, f'out_{sf}.pdbqt')
                                    self.progress.emit(
                                        f"Docking {lig_name} — {sf} "
                                        f"({'flex' if use_flex else 'rigid'})…")
                                    cmd = [
                                        p['vina_path'],
                                        '--receptor',   _rigid if use_flex else rec_pdbqt,
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
                                    if use_flex:
                                        cmd += ['--flex', _flex]
                                    self._run_cmd(cmd, f"Vina ({sf}) — {lig_name}")
                                    if os.path.isfile(out_pdbqt):
                                        final_out = self._persist_docking_output(out_pdbqt, lig_name, mode, sf) or out_pdbqt
                                        results.append({
                                            "lig_name": lig_name,
                                            "sf": f"{mode}/{sf}",
                                            "out_path": final_out,
                                            "smiles": mol_smiles,
                                            "source_path": source_path,
                                        })
                                    else:
                                        self.log.emit(f"⚠ No output for {lig_name} [{mode}/{sf}]")

                                ad4_needed   = 'ad4'    in sf_types
                                adgpu_needed = 'ad4gpu' in sf_types
                                if ad4_needed or adgpu_needed:
                                    fld_path, gpf_path = self._build_ad4_grids(
                                        mode_dir, rec_pdbqt, lig_pdbqt, _flex, idx)

                                    if ad4_needed:
                                        pythonsh = p['pythonsh']
                                        prep_dpf = p['prepare_dpf']
                                        dpf_path = os.path.join(mode_dir, 'dock.dpf')
                                        dlg_path = os.path.join(mode_dir, 'dock_ad4.dlg')
                                        local_rec = os.path.join(mode_dir, os.path.basename(rec_pdbqt))
                                        local_lig = os.path.join(mode_dir, os.path.basename(lig_pdbqt))
                                        if os.path.abspath(rec_pdbqt) != os.path.abspath(local_rec):
                                            shutil.copy2(rec_pdbqt, local_rec)
                                        if os.path.abspath(lig_pdbqt) != os.path.abspath(local_lig):
                                            shutil.copy2(lig_pdbqt, local_lig)
                                        local_flex = None
                                        if _flex:
                                            local_flex = os.path.join(mode_dir, os.path.basename(_flex))
                                            if os.path.abspath(_flex) != os.path.abspath(local_flex):
                                                shutil.copy2(_flex, local_flex)
                                        self.progress.emit(f"Generating DPF for {lig_name}…")
                                        cmd = [
                                            pythonsh, prep_dpf,
                                            '-r', local_rec, '-l', local_lig, '-o', dpf_path,
                                            '-p', f'ga_num_evals='
                                                  f'{p.get("ad4_exhaustiveness", 8) * 250000}',
                                            '-p', f'ga_run={p.get("n_poses", 9)}',
                                            '-p', f'ga_pop_size={p.get("ga_pop_size", 150)}',
                                            '-p', f'rmstol={p.get("cluster_rmsd", 2.0)}',
                                        ]
                                        if local_flex:
                                            cmd += ['-x', local_flex]
                                        self._run_cmd(cmd, f"prepare_dpf42.py — {lig_name}")
                                        if not os.path.isfile(dpf_path):
                                            raise RuntimeError(
                                                f"prepare_dpf42.py did not produce: {dpf_path}")
                                        self.progress.emit(f"Running AutoDock4 for {lig_name}…")
                                        self._run_cmd(
                                            [p['ad4_path'], '-p', dpf_path, '-l', dlg_path],
                                            f"autodock4 — {lig_name}", cwd=mode_dir)
                                        if os.path.isfile(dlg_path):
                                            final_out = self._persist_docking_output(dlg_path, lig_name, mode, 'ad4') or dlg_path
                                            results.append({
                                                "lig_name": lig_name,
                                                "sf": f"{mode}/ad4",
                                                "out_path": final_out,
                                                "smiles": mol_smiles,
                                                "source_path": source_path,
                                            })
                                        else:
                                            self.log.emit(f"⚠ AutoDock4 no output for {lig_name}")

                                    if adgpu_needed:
                                        dlg_base = os.path.join(mode_dir, 'dock_adgpu')
                                        self.progress.emit(f"Running AutoDock-GPU for {lig_name}…")
                                        cmd = [
                                            p['autodockgpu'],
                                            '--lfile', lig_pdbqt,
                                            '--ffile', fld_path,
                                            '--resnam', dlg_base,
                                            '--nrun', str(p.get('n_poses', 9)),
                                            '--nev',  str(p.get('ad4_exhaustiveness', 8) * 250000),
                                        ]
                                        seed = p.get('seed', 0)
                                        if seed:
                                            cmd += ['--seed', str(seed)]
                                        if _flex:
                                            cmd += ['--flexres', _flex]
                                        self._run_cmd(cmd, f"AutoDock-GPU — {lig_name}",
                                                      cwd=mode_dir)
                                        dlg_path = dlg_base + '.dlg'
                                        if os.path.isfile(dlg_path):
                                            final_out = self._persist_docking_output(dlg_path, lig_name, mode, 'ad4gpu') or dlg_path
                                            results.append({
                                                "lig_name": lig_name,
                                                "sf": f"{mode}/ad4gpu",
                                                "out_path": final_out,
                                                "smiles": mol_smiles,
                                                "source_path": source_path,
                                            })
                                        else:
                                            self.log.emit(
                                                f"⚠ AD4GPU no output for {lig_name}")

                        except Exception as mol_exc:
                            n_failed += 1
                            self.log.emit(
                                f"\n❌ [{total_mols}] {lig_name} FAILED — "
                                f"skipping: {mol_exc}")
                except RuntimeError as e:
                    self.log.emit(f"⚠ Skipping {input_name}: {e}")
                    continue

                if not had_molecule:
                    self.log.emit(f"⚠ Skipping {input_name}: no molecules were converted")
                    continue
                pct = int(file_idx * 100 / len(lig_files))
                self.progress_pct.emit(pct)

            if not results:
                raise RuntimeError(
                    "Docking produced no output files for any ligand "
                    f"({n_failed}/{total_mols} failed).")
            if n_failed:
                self.log.emit(
                    f"\n⚠ {n_failed}/{total_mols} molecule(s) failed and were skipped.")
            self.finished.emit(results)

        except Exception as exc:
            self.error.emit(str(exc))


class LigandTestPanel(QWidget):
    """
    Ligand Test Docking Panel.

    1. Select receptor from receptor_ready/
    2. Parse receptor components → checkbox table (receptor-only, no native lig)
    3. Select ligands from ligand_ready/ or ligand_input/
    4. Set docking parameters (customized)
    5. Run batch docking → result table (best pose per ligand)
    """

    docking_finished   = Signal(str)
    result_csv_ready   = Signal(str)   # path to results CSV
    # Job tracking signals → connect to JobManagerPanel
    job_registered     = Signal(object)   # DockingJob (RUNNING)
    job_log_line       = Signal(str, str) # (job_id, message)
    job_status_changed = Signal(object)   # DockingJob (FINISHED/FAILED)

    def __init__(self, job_dir: str = "", parent=None):
        super().__init__(parent)
        self._job_dir      = job_dir
        self._current_pdb  = ""
        self._components: list[dict] = []
        self._current_job  = None   # DockingJob — live tracking
        self._rec_name     = ""     # receptor display name (set at run start)
        self._pending_queue: list[dict] = []   # queued params waiting to run
        # Buffered log: avoid flooding GUI event queue with per-line signals
        self._log_buffer: list[str] = []
        from PySide6.QtCore import QTimer
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(150)
        self._log_flush_timer.timeout.connect(self._flush_log_buffer)
        self._build_ui()
        # Watcher: auto-refresh lists when receptor_ready/ or ligand_ready/ changes
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        if job_dir:
            self.set_job_dir(job_dir)

    def set_job_dir(self, path: str):
        self._job_dir = path
        # Update watcher paths
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        for subdir in ("receptor_ready", "ligand_ready", "ligand_input"):
            d = os.path.join(path, subdir)
            os.makedirs(d, exist_ok=True)
            self._watcher.addPath(d)
        self._refresh_receptor_list()
        self._refresh_ligand_list(self._src_combo.currentText().rstrip('/'))

    def set_job_dir(self, path: str):
        self._job_dir = path
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        for subdir in ("receptor_ready", "ligand_ready", "ligand_input"):
            d = os.path.join(path, subdir)
            os.makedirs(d, exist_ok=True)
            self._watcher.addPath(d)
        self._refresh_receptor_list()
        self._refresh_ligand_list(self._src_combo.currentText().rstrip('/'))
        self._stack.setCurrentIndex(1)

    # ------------------------------------------------------------------ #
    # Buffered log helpers (prevent event-queue flooding)
    # ------------------------------------------------------------------ #

    @Slot(str)
    def _queue_log(self, msg: str):
        """Receive log line from worker thread — buffer it, don't touch QTextEdit yet."""
        self._log_buffer.append(msg)

    def _flush_log_buffer(self):
        """Flush buffered log lines to QTextEdit (called every 150 ms by timer)."""
        if not self._log_buffer:
            return
        text = '\n'.join(self._log_buffer)
        self._log_buffer.clear()
        self._log.append(text)

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
        overlay.setStyleSheet("background:#161b22;")
        ov_lay = QVBoxLayout(overlay)
        ov_lay.setAlignment(Qt.AlignCenter)
        ov_icon = QLabel("📂")
        ov_icon.setFont(QFont("Sans", 40))
        ov_icon.setAlignment(Qt.AlignCenter)
        ov_msg = QLabel("Job Directory not set.\nGo to Preparation → Open or Generate Job Dir first.")
        ov_msg.setAlignment(Qt.AlignCenter)
        ov_msg.setStyleSheet("color:#545d68; font-size:13px;")
        ov_lay.addWidget(ov_icon)
        ov_lay.addSpacing(8)
        ov_lay.addWidget(ov_msg)
        self._stack.addWidget(overlay)  # index 0

        # Page 1 — main content
        main_w = QWidget()
        main_lay = QVBoxLayout(main_w)
        main_lay.setContentsMargins(8, 8, 8, 8)
        main_lay.setSpacing(6)
        self._stack.addWidget(main_w)   # index 1
        self._stack.setCurrentIndex(0)  # show overlay until job dir is set

        self._build_main_content(main_lay)

    def _build_main_content(self, root):
        root.addWidget(SectionLabel("💊 Ligand Test Docking"))

        # ── Top: receptor file + component table | ligand list ─────────
        top_split = QSplitter(Qt.Horizontal)
        top_split.setFixedHeight(260)
        top_split.setStyleSheet(
            "QSplitter::handle{background:#22272e;width:2px;}")

        # ── Left: receptor file list ────────────────────────────────────
        rec_panel = QWidget()
        rp = QVBoxLayout(rec_panel)
        rp.setContentsMargins(0, 0, 0, 0)
        rp.setSpacing(3)

        rp_hdr = QHBoxLayout()
        rp_hdr.addWidget(QLabel("📂 receptor_ready/"))
        ref_btn = QPushButton("⟳")
        ref_btn.setFixedSize(24, 24)
        ref_btn.setStyleSheet(
            "QPushButton{background:#22272e;color:#e6edf3;"
            "border:1px solid #2d333b;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#2d333b;}")
        ref_btn.clicked.connect(self._refresh_receptor_list)
        rp_hdr.addWidget(ref_btn)
        rp.addLayout(rp_hdr)

        self._rec_list = QListWidget()
        self._rec_list.setStyleSheet(
            "QListWidget{background:#161b22;color:#e6edf3;"
            "border:1px solid #22272e;font-size:11px;}"
            "QListWidget::item:selected{background:#22272e;}")
        self._rec_list.itemClicked.connect(self._on_receptor_selected)
        rp.addWidget(self._rec_list)
        rec_panel.setFixedWidth(190)
        top_split.addWidget(rec_panel)

        # ── Center: component table ─────────────────────────────────────
        comp_panel = QWidget()
        cp = QVBoxLayout(comp_panel)
        cp.setContentsMargins(0, 0, 0, 0)
        cp.setSpacing(3)
        cp.addWidget(QLabel("🔬 Receptor Components"))

        self._comp_table = QTableWidget(0, 8)
        self._comp_table.setHorizontalHeaderLabels(
            ["Chain", "ResName", "ResSeq", "Type", "#Res", "#Atoms", "As Receptor", "Box Ligand"])
        self._comp_table.horizontalHeader().setStyleSheet(
            "QHeaderView::section{background:#0d1117;color:#58a6ff;"
            "border:1px solid #22272e;padding:3px;font-size:11px;}")
        self._comp_table.setStyleSheet(
            "QTableWidget{background:#161b22;color:#e6edf3;"
            "border:1px solid #22272e;gridline-color:#22272e;font-size:11px;}"
            "QTableWidget::item:selected{background:#2d333b;}")
        self._comp_table.verticalHeader().setVisible(False)
        self._comp_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self._comp_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        cp.addWidget(self._comp_table)

        # Radio group — exclusive selection of which ligand to use for Box Center
        self._ligand_radio_group = QButtonGroup(self)
        self._ligand_radio_group.setExclusive(True)
        self._ligand_radio_group.buttonClicked.connect(
            lambda _: self._on_box_ligand_changed())
        top_split.addWidget(comp_panel)

        # ── Right: ligand list ──────────────────────────────────────────
        lig_panel = QWidget()
        lp = QVBoxLayout(lig_panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(3)

        lp_hdr = QHBoxLayout()
        lp_hdr.addWidget(QLabel("💊 Ligands"))
        self._src_combo = QComboBox()
        self._src_combo.addItems(["ligand_ready/", "ligand_input/"])
        self._src_combo.setStyleSheet(
            "QComboBox{background:#22272e;color:#e6edf3;"
            "border:1px solid #2d333b;border-radius:3px;"
            "padding:1px 4px;font-size:10px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#22272e;color:#e6edf3;}")
        self._src_combo.setFixedWidth(110)
        self._src_combo.currentTextChanged.connect(
            lambda t: self._refresh_ligand_list(t.rstrip('/')))
        lp_hdr.addWidget(self._src_combo)
        lp_ref = QPushButton("⟳")
        lp_ref.setFixedSize(24, 24)
        lp_ref.setStyleSheet(
            "QPushButton{background:#22272e;color:#e6edf3;"
            "border:1px solid #2d333b;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#2d333b;}")
        lp_ref.clicked.connect(lambda: self._refresh_ligand_list(
            self._src_combo.currentText().rstrip('/')))
        lp_hdr.addWidget(lp_ref)
        lp.addLayout(lp_hdr)

        self._lig_list = QListWidget()
        self._lig_list.setSelectionMode(QAbstractItemView.MultiSelection)
        self._lig_list.setStyleSheet(
            "QListWidget{background:#161b22;color:#a6e3a1;"
            "border:1px solid #22272e;font-size:11px;}"
            "QListWidget::item:selected{background:#22272e;}")
        lp.addWidget(self._lig_list)

        sel_row = QHBoxLayout()
        sel_all = QPushButton("Select All")
        sel_none = QPushButton("None")
        for btn in (sel_all, sel_none):
            btn.setFixedHeight(22)
            btn.setStyleSheet(
                "QPushButton{background:#22272e;color:#e6edf3;"
                "border:1px solid #2d333b;border-radius:3px;font-size:10px;}"
                "QPushButton:hover{background:#2d333b;}")
        sel_all.clicked.connect(self._lig_list.selectAll)
        sel_none.clicked.connect(self._lig_list.clearSelection)
        sel_row.addWidget(sel_all)
        sel_row.addWidget(sel_none)
        sel_row.addStretch()
        lp.addLayout(sel_row)

        # Browse / Import buttons
        browse_row = QHBoxLayout()
        browse_btn = QPushButton("📂 Browse…")
        browse_btn.setFixedHeight(22)
        browse_btn.setStyleSheet(
            "QPushButton{background:#22272e;color:#e6edf3;"
            "border:1px solid #2d333b;border-radius:3px;font-size:10px;}"
            "QPushButton:hover{background:#2d333b;}")
        browse_btn.setToolTip(
            "Add ligand files from any location\n"
            "Supported: PDBQT, PDB, SDF (multi), MOL, MOL2 (multi),\n"
            "SMILES (.smi/.smiles/.txt), CSV/TSV/Excel with SMILES column")
        browse_btn.clicked.connect(self._browse_ligands)

        smi_btn = QPushButton("✏ SMILES")
        smi_btn.setFixedHeight(22)
        smi_btn.setStyleSheet(
            "QPushButton{background:#22272e;color:#e6edf3;"
            "border:1px solid #2d333b;border-radius:3px;font-size:10px;}"
            "QPushButton:hover{background:#2d333b;}")
        smi_btn.setToolTip("Paste one or more SMILES strings")
        smi_btn.clicked.connect(self._import_smiles_dialog)

        rem_btn = QPushButton("✕")
        rem_btn.setFixedSize(22, 22)
        rem_btn.setStyleSheet(
            "QPushButton{background:#22272e;color:#f38ba8;"
            "border:1px solid #2d333b;border-radius:3px;font-size:10px;}"
            "QPushButton:hover{background:#2d333b;}")
        rem_btn.setToolTip("Remove selected entries from list")
        rem_btn.clicked.connect(self._remove_selected_ligands)

        browse_row.addWidget(browse_btn, stretch=1)
        browse_row.addWidget(smi_btn, stretch=1)
        browse_row.addWidget(rem_btn)
        lp.addLayout(browse_row)

        lig_panel.setFixedWidth(190)
        top_split.addWidget(lig_panel)

        root.addWidget(top_split)

        # ── Docking parameters ──────────────────────────────────────────
        params_grp = QGroupBox("Docking Parameters")
        params_grp.setStyleSheet(_GRP.format(t=theme.ACCENT))
        pg = QVBoxLayout(params_grp)
        pg.setSpacing(5)

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

        # ── Row 1: Scoring functions + mode ────────────────────────────
        sf_row = QHBoxLayout()
        sf_row.addWidget(QLabel("Scoring:"))
        self._sf_vina    = QCheckBox("Vina");    self._sf_vina.setChecked(True)
        self._sf_vinardo = QCheckBox("Vinardo"); self._sf_vinardo.setChecked(False)
        self._sf_ad4     = QCheckBox("AD4");     self._sf_ad4.setChecked(False)
        self._sf_ad4gpu  = QCheckBox("AD4-GPU"); self._sf_ad4gpu.setChecked(False)
        for cb in (self._sf_vina, self._sf_vinardo, self._sf_ad4, self._sf_ad4gpu):
            cb.setStyleSheet("color:#e6edf3;font-size:11px;")
            sf_row.addWidget(cb)
            cb.stateChanged.connect(self._update_sf_params)
        sf_row.addSpacing(20)
        sf_row.addWidget(QLabel("Mode:"))
        self._mode_rigid    = QCheckBox("Rigid");    self._mode_rigid.setChecked(True)
        self._mode_flexible = QCheckBox("Flexible"); self._mode_flexible.setChecked(False)
        for cb in (self._mode_rigid, self._mode_flexible):
            cb.setStyleSheet("color:#e6edf3;font-size:11px;")
            sf_row.addWidget(cb)
        sf_row.addStretch()
        pg.addLayout(sf_row)

        # ── Row 2: Box center ───────────────────────────────────────────
        center_row = QHBoxLayout()
        center_row.addWidget(QLabel("Box Center:"))
        self._center_grp = QButtonGroup(self)
        for i, (lbl, val) in enumerate([
                ("To Ligand", "ligand"), ("To Protein", "protein"),
                ("Custom", "custom")]):
            rb = QRadioButton(lbl)
            rb.setStyleSheet("color:#e6edf3;font-size:11px;")
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
        center_row.addStretch()
        pg.addLayout(center_row)

        # ── Row 3: Box size + AD4 spacing ──────────────────────────────
        size_row = QHBoxLayout()
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

        # ── Row 4: Flexible residues ────────────────────────────────────
        flex_row = QHBoxLayout()
        flex_row.addWidget(QLabel("Flex Residue Distance (Å):"))
        self._flex_dist = QDoubleSpinBox()
        self._flex_dist.setRange(0.0, 20.0)
        self._flex_dist.setDecimals(1)
        self._flex_dist.setValue(3.0)
        _tune_spinbox(self._flex_dist, 100)
        flex_row.addWidget(self._flex_dist)
        flex_row.addWidget(QLabel("Flexible Residues:"))
        from PySide6.QtWidgets import QLineEdit
        self._flex_residues = QLineEdit()
        self._flex_residues.setPlaceholderText(
            "e.g. A:LYS:123_A:ASP:89  (auto-filled when Run)")
        self._flex_residues.setStyleSheet(
            f"background:#22272e;color:#e6edf3;border:1px solid #2d333b;"
            "border-radius:3px;padding:2px 4px;font-size:11px;")
        flex_row.addWidget(self._flex_residues, 1)
        pg.addLayout(flex_row)

        # ── Row 5: Multi-ligand arrangement ────────────────────────────
        arr_row = QHBoxLayout()
        arr_row.addWidget(QLabel("Arrangement:"))
        self._arr_type = QComboBox()
        self._arr_type.addItems(["combination", "permutation"])
        self._arr_type.setFixedWidth(110)
        self._arr_type.setStyleSheet(_COMBO)
        arr_row.addWidget(self._arr_type)
        arr_row.addSpacing(10)
        arr_row.addWidget(QLabel("Simultaneous Ligands:"))
        self._elements = QSpinBox()
        self._elements.setRange(1, 5)
        self._elements.setValue(1)
        self._elements.setFixedWidth(50)
        self._elements.setStyleSheet(_SPIN)
        arr_row.addWidget(self._elements)
        arr_row.addStretch()
        pg.addLayout(arr_row)

        # ── Row 6: Common params (always visible) ──────────────────────
        common_row = QHBoxLayout()
        common_row.addWidget(QLabel("N Poses:"))
        self._n_poses = QSpinBox(); self._n_poses.setRange(1, 20); self._n_poses.setValue(1)
        _tune_spinbox(self._n_poses, 88)
        common_row.addWidget(self._n_poses)
        common_row.addSpacing(8)
        common_row.addWidget(QLabel("CPU:"))
        self._cpu = QSpinBox(); self._cpu.setRange(1, 64); self._cpu.setValue(4)
        _tune_spinbox(self._cpu, 88)
        self._cpu.setToolTip("CPU cores per job (Vina/Vinardo/AD4). Not used by AD4GPU.")
        common_row.addWidget(self._cpu)
        common_row.addSpacing(8)
        common_row.addWidget(QLabel("Max Workers:"))
        self._max_workers = QSpinBox(); self._max_workers.setRange(1, 16); self._max_workers.setValue(3)
        _tune_spinbox(self._max_workers, 92)
        self._max_workers.setToolTip("Max parallel docking jobs.")
        common_row.addWidget(self._max_workers)
        common_row.addSpacing(8)
        common_row.addWidget(QLabel("Seed (0=rnd):"))
        self._seed = QSpinBox(); self._seed.setRange(0, 2147483647); self._seed.setValue(0)
        _tune_spinbox(self._seed, 132)
        self._seed.setToolTip("Random seed (0 = random). Used by Vina/Vinardo and AD4GPU.")
        common_row.addWidget(self._seed)
        common_row.addStretch()
        pg.addLayout(common_row)

        # ── Row 6b: Vina/Vinardo specific ──────────────────────────────
        self._vv_params_widget = QWidget()
        vv_row = QHBoxLayout(self._vv_params_widget)
        vv_row.setContentsMargins(0, 0, 0, 0)
        vv_lbl = QLabel("Vina/Vinardo ▸")
        vv_lbl.setStyleSheet("color:#58a6ff;font-size:11px;font-weight:bold;")
        vv_row.addWidget(vv_lbl)
        vv_row.addWidget(QLabel("Exhaustiveness:"))
        self._exhaustiveness = QSpinBox(); self._exhaustiveness.setRange(1, 64); self._exhaustiveness.setValue(8)
        _tune_spinbox(self._exhaustiveness, 92)
        self._exhaustiveness.setToolTip("Search exhaustiveness (Vina/Vinardo). Higher = more thorough, slower.")
        vv_row.addWidget(self._exhaustiveness)
        vv_row.addSpacing(8)
        vv_row.addWidget(QLabel("Energy Range:"))
        self._energy_range = QSpinBox(); self._energy_range.setRange(1, 10); self._energy_range.setValue(3)
        _tune_spinbox(self._energy_range, 88)
        self._energy_range.setToolTip("Max energy difference from best pose (kcal/mol).")
        vv_row.addWidget(self._energy_range)
        vv_row.addStretch()
        pg.addWidget(self._vv_params_widget)

        # ── Row 6c: AD4 / AD4GPU specific (grid-based) ─────────────────
        self._grid_params_widget = QWidget()
        grid_row = QHBoxLayout(self._grid_params_widget)
        grid_row.setContentsMargins(0, 0, 0, 0)
        grid_lbl = QLabel("AD4/GPU ▸")
        grid_lbl.setStyleSheet("color:#a6e3a1;font-size:11px;font-weight:bold;")
        grid_row.addWidget(grid_lbl)
        grid_row.addWidget(QLabel("Exhaustiveness:"))
        self._ad4_exhaustiveness = QSpinBox(); self._ad4_exhaustiveness.setRange(1, 100); self._ad4_exhaustiveness.setValue(8)
        _tune_spinbox(self._ad4_exhaustiveness, 92)
        self._ad4_exhaustiveness.setToolTip("Multiplied × 250 000 → ga_num_evals (AD4) / --nev (AD4GPU).")
        grid_row.addWidget(self._ad4_exhaustiveness)
        grid_row.addSpacing(8)
        grid_row.addWidget(QLabel("Grid Spacing (Å):"))
        self._spacing = QDoubleSpinBox(); self._spacing.setRange(0.1, 2.0); self._spacing.setDecimals(3)
        self._spacing.setValue(0.375); _tune_spinbox(self._spacing, 104)
        self._spacing.setToolTip("AutoGrid4 grid point spacing (AD4 and AD4GPU).")
        grid_row.addWidget(self._spacing)
        # AD4-only GA params
        self._ad4_only_widget = QWidget()
        ad4_sub = QHBoxLayout(self._ad4_only_widget)
        ad4_sub.setContentsMargins(12, 0, 0, 0)
        ad4_sub.addWidget(QLabel("GA Pop:"))
        self._ga_pop_size = QSpinBox(); self._ga_pop_size.setRange(50, 1000); self._ga_pop_size.setValue(150)
        self._ga_pop_size.setSingleStep(50); _tune_spinbox(self._ga_pop_size, 100)
        self._ga_pop_size.setToolTip("GA population size (AD4 only). Default 150.")
        ad4_sub.addWidget(self._ga_pop_size)
        ad4_sub.addSpacing(8)
        ad4_sub.addWidget(QLabel("Cluster RMSD (Å):"))
        self._cluster_rmsd = QDoubleSpinBox(); self._cluster_rmsd.setRange(0.1, 10.0); self._cluster_rmsd.setDecimals(1)
        self._cluster_rmsd.setValue(2.0); _tune_spinbox(self._cluster_rmsd, 100)
        self._cluster_rmsd.setToolTip("Pose clustering RMSD (AD4 only). Default 2.0 Å.")
        ad4_sub.addWidget(self._cluster_rmsd)
        grid_row.addWidget(self._ad4_only_widget)
        grid_row.addStretch()
        pg.addWidget(self._grid_params_widget)

        # ── Row 7: I/O options ──────────────────────────────────────────
        io_row = QHBoxLayout()
        self._save_input  = QCheckBox("Save Input Files")
        self._save_output = QCheckBox("Save Output Files")
        self._parallel    = QCheckBox("Parallel Simulation")
        self._save_input.setChecked(True)
        self._save_output.setChecked(True)
        self._parallel.setChecked(False)
        for cb in (self._save_input, self._save_output, self._parallel):
            cb.setStyleSheet("color:#e6edf3;font-size:11px;")
            io_row.addWidget(cb)
        io_row.addStretch()
        pg.addLayout(io_row)

        # ── Row 8: Tools compact status bar ────────────────────────────
        from engine.tool_detector import INSTALL_URLS, PIP_INSTALL, BUNDLED_KEYS
        from PySide6.QtWidgets import QLineEdit, QDialog, QDialogButtonBox

        tools_bar = QHBoxLayout()
        tools_bar.setSpacing(4)
        tools_bar.addWidget(QLabel("🔧"))

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

        for lbl, attr, det_key, _ in _tool_fields:
            le = QLineEdit()
            le.setVisible(False)
            setattr(self, attr, le)
            badge = QLabel(f"⏳ {lbl}")
            badge.setStyleSheet(
                f"background:#22272e;color:#545d68;border-radius:3px;"
                "padding:1px 6px;font-size:10px;")
            badge.setToolTip(f"{lbl}: detecting…")
            self._tp_status_labels[det_key] = badge
            tools_bar.addWidget(badge)

        tools_bar.addStretch()
        override_btn = QPushButton("⚙ Override Paths")
        override_btn.setFixedHeight(22)
        override_btn.setStyleSheet(
            "QPushButton{background:#22272e;color:#8b949e;border:1px solid #2d333b;"
            "border-radius:3px;padding:0 8px;font-size:10px;}"
            "QPushButton:hover{background:#2d333b;color:#e6edf3;}")
        redetect_btn = QPushButton("⟳")
        redetect_btn.setFixedSize(26, 22)
        redetect_btn.setToolTip("Re-detect all tools")
        redetect_btn.setStyleSheet(
            "QPushButton{background:#22272e;color:#8b949e;border:1px solid #2d333b;"
            "border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#2d333b;}")
        redetect_btn.clicked.connect(self._detect_tools)
        tools_bar.addWidget(override_btn)
        tools_bar.addWidget(redetect_btn)
        pg.addLayout(tools_bar)

        def _open_override_dialog():
            dlg = QDialog(self)
            dlg.setWindowTitle("Override Tool Paths")
            dlg.setMinimumWidth(620)
            dlg.setStyleSheet(
                "QDialog{background:#161b22;color:#e6edf3;}"
                "QLabel{color:#8b949e;font-size:10px;}"
                "QLineEdit{background:#22272e;color:#e6edf3;border:1px solid #2d333b;"
                "border-radius:3px;padding:2px 4px;font-size:10px;}")
            dv = QVBoxLayout(dlg)
            dv.setSpacing(6)
            dv.addWidget(QLabel(
                "Bundled tools are auto-detected. Override only if you need a custom binary."))
            _btn_style = (
                "QPushButton{background:#22272e;color:#e6edf3;border:1px solid #2d333b;"
                "border-radius:3px;padding:0 6px;font-size:10px;}"
                "QPushButton:hover{background:#2d333b;}")
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
                        "QPushButton{background:#1e3a2e;color:#a6e3a1;border:1px solid #2d5a3d;"
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
                "QPushButton{background:#a6e3a1;color:#161b22;border-radius:3px;"
                "padding:4px 18px;font-weight:bold;}")
            bbox.accepted.connect(dlg.accept)
            dv.addWidget(bbox)
            dlg.exec()
            self._update_sf_availability()

        override_btn.clicked.connect(_open_override_dialog)

        # Connect path changes to SF availability
        for attr in ("_tp_vina", "_tp_ad4", "_tp_adgpu"):
            getattr(self, attr).textChanged.connect(self._update_sf_availability)

        self._update_sf_params()   # initial visibility based on default SF selection

        root.addWidget(params_grp)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, self._detect_tools)

        # ── Run button ─────────────────────────────────────────────────
        self._run_btn = QPushButton("▶  Run Ligand Test Docking")
        self._run_btn.setFixedHeight(36)
        self._run_btn.setStyleSheet(
            "QPushButton{background:#a6e3a1;color:#161b22;border-radius:4px;"
            "font-weight:bold;font-size:13px;}"
            "QPushButton:hover{background:#c0f5b0;}"
            "QPushButton:disabled{background:#2d333b;color:#545d68;}")
        self._run_btn.clicked.connect(self._on_run)
        root.addWidget(self._run_btn)

        self._prog = QProgressBar()
        self._prog.setRange(0, 100)
        self._prog.setValue(0)
        self._prog.setVisible(False)
        self._prog.setFixedHeight(5)
        self._prog.setStyleSheet(
            "QProgressBar{border:none;background:#22272e;border-radius:2px;}"
            "QProgressBar::chunk{background:#a6e3a1;border-radius:2px;}")
        root.addWidget(self._prog)

        # ── Log + Results ──────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(70)
        self._log.setStyleSheet(
            f"background:#161b22;color:#8b949e;font-family:monospace;"
            "font-size:10px;border:1px solid #22272e;border-radius:3px;")
        self._log.document().setMaximumBlockCount(800)  # prevent unbounded growth
        root.addWidget(self._log)

        res_label = SectionLabel("📊 Results (Best Pose per Ligand)")
        root.addWidget(res_label)

        self._result_table = QTableWidget(0, 6)
        self._result_table.setHorizontalHeaderLabels(
            ["#", "Receptor", "Ligand [SF]", "Best ΔG (kcal/mol)", "RMSD lb", "RMSD ub"])
        self._result_table.horizontalHeader().setStyleSheet(
            "QHeaderView::section{background:#0d1117;color:#58a6ff;"
            "border:1px solid #22272e;padding:3px;font-size:11px;}")
        self._result_table.setStyleSheet(
            "QTableWidget{background:#161b22;color:#e6edf3;"
            "border:1px solid #22272e;gridline-color:#22272e;font-size:11px;}"
            "QTableWidget::item:selected{background:#2d333b;}")
        self._result_table.verticalHeader().setVisible(False)
        hh2 = self._result_table.horizontalHeader()
        hh2.setSectionResizeMode(2, QHeaderView.Stretch)
        root.addWidget(self._result_table, 1)

    # ------------------------------------------------------------------ #
    # File lists
    # ------------------------------------------------------------------ #

    def _on_dir_changed(self, path: str):
        """Called by QFileSystemWatcher when a watched directory changes."""
        rec_dir = os.path.join(self._job_dir, "receptor_ready")
        if path == rec_dir:
            self._refresh_receptor_list()
        else:
            self._refresh_ligand_list(self._src_combo.currentText().rstrip('/'))

    def _refresh_receptor_list(self):
        self._rec_list.clear()
        ready_dir = os.path.join(self._job_dir, "receptor_ready")
        if not os.path.isdir(ready_dir):
            return
        for fname in sorted(os.listdir(ready_dir)):
            if fname.lower().endswith(('.pdb', '.pdbqt')):
                item = QListWidgetItem(fname)
                item.setData(Qt.UserRole, os.path.join(ready_dir, fname))
                self._rec_list.addItem(item)

    def _refresh_ligand_list(self, subdir: str = "ligand_ready"):
        self._lig_list.clear()
        lig_dir = os.path.join(self._job_dir, subdir)
        if not os.path.isdir(lig_dir):
            return
        from core.ligand_importer import SUPPORTED_EXTENSIONS, count_molecules
        for fname in sorted(os.listdir(lig_dir)):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            full_path = os.path.join(lig_dir, fname)
            n = count_molecules(full_path)
            display = fname if n <= 1 else f"{fname}  ({n} mols)"
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, full_path)
            item.setToolTip("{SUPPORTED_EXTENSIONS[ext]}\n{full_path}")
            self._lig_list.addItem(item)
        self._lig_list.selectAll()

    def _browse_ligands(self):
        """Open file dialog to add ligand files from any location."""
        from PySide6.QtWidgets import QFileDialog
        from core.ligand_importer import FILE_FILTER, SUPPORTED_EXTENSIONS, count_molecules
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Ligand Files", "", FILE_FILTER)
        if not paths:
            return
        for path in paths:
            # Skip duplicates
            existing = [self._lig_list.item(i).data(Qt.UserRole)
                        for i in range(self._lig_list.count())]
            if path in existing:
                continue
            ext = os.path.splitext(path)[1].lower()
            n   = count_molecules(path)
            fname = os.path.basename(path)
            display = fname if n <= 1 else f"{fname}  ({n} mols)"
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, path)
            item.setToolTip(
                "{SUPPORTED_EXTENSIONS.get(ext, ext)}\n{path}")
            self._lig_list.addItem(item)
            item.setSelected(True)

    def _remove_selected_ligands(self):
        """Remove selected rows from the ligand list."""
        for item in self._lig_list.selectedItems():
            self._lig_list.takeItem(self._lig_list.row(item))

    def _import_smiles_dialog(self):
        """Dialog to paste SMILES strings and add them as virtual ligands."""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QTextEdit, QPushButton, QDialogButtonBox, QLineEdit
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Import SMILES")
        dlg.setMinimumWidth(500)
        v = QVBoxLayout(dlg)

        v.addWidget(QLabel(
            "Enter one SMILES per line.  Optionally add a name after a space:\n"
            "  <SMILES>  <name>\n"
            "Example:  CC(=O)Oc1ccccc1C(=O)O  aspirin"))

        txt = QTextEdit()
        txt.setPlaceholderText("CC(=O)Oc1ccccc1C(=O)O  aspirin\nCN1C=NC2=C1C(=O)N(C)C(=O)N2C  caffeine")
        txt.setMinimumHeight(140)
        v.addWidget(txt)

        row = QHBoxLayout()
        row.addWidget(QLabel("Save to:"))
        dest_edit = QLineEdit()
        lig_ready = os.path.join(self._job_dir, "ligand_ready") if self._job_dir else ""
        dest_edit.setText(lig_ready)
        row.addWidget(dest_edit, stretch=1)
        v.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        v.addWidget(btns)

        if dlg.exec() != QDialog.Accepted:
            return

        raw_text = txt.toPlainText().strip()
        if not raw_text:
            return

        dest_dir = dest_edit.text().strip() or lig_ready
        os.makedirs(dest_dir, exist_ok=True)

        # Write as .smi file
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        smi_path = os.path.join(dest_dir, f"smiles_import_{ts}.smi")
        with open(smi_path, 'w', encoding='utf-8') as fh:
            fh.write(raw_text + '\n')

        from core.ligand_importer import count_molecules, SUPPORTED_EXTENSIONS
        n = count_molecules(smi_path)
        display = f"smiles_import_{ts}.smi  ({n} mols)"
        item = QListWidgetItem(display)
        item.setData(Qt.UserRole, smi_path)
        item.setToolTip(f"SMILES import\n{smi_path}f")
        self._lig_list.addItem(item)
        item.setSelected(True)


    def _on_receptor_selected(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if not path or not os.path.isfile(path):
            return
        self._current_pdb = path
        self._load_components(path)

    def _load_components(self, pdb_path: str):
        self._components = parse_pdb_components(pdb_path)
        self._comp_table.setRowCount(0)

        # Clear old radio buttons
        for btn in self._ligand_radio_group.buttons():
            self._ligand_radio_group.removeButton(btn)

        first_ligand_row = -1

        for row, comp in enumerate(self._components):
            self._comp_table.insertRow(row)
            for col, key in enumerate(('chain', 'resname', 'resseq', 'type',
                                        'n_residues', 'n_atoms')):
                item = QTableWidgetItem(str(comp.get(key, '')))
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(
                    QBrush(QColor(_TYPE_COLOR.get(comp['type'], '#e6edf3'))))
                self._comp_table.setItem(row, col, item)

            # As Receptor checkbox
            rec_cb = QCheckBox()
            rec_cb.setChecked(comp['type'] == 'Protein')
            self._comp_table.setCellWidget(row, 6, _centered_widget(rec_cb))

            # Box Ligand radio (which ligand to center box on) — only for Ligand type
            lig_rb = QRadioButton()
            lig_rb.setProperty("row", row)
            lig_rb.setEnabled(comp['type'] == 'Ligand')
            self._ligand_radio_group.addButton(lig_rb)
            self._comp_table.setCellWidget(row, 7, _centered_widget(lig_rb))

            if comp['type'] == 'Ligand' and first_ligand_row < 0:
                first_ligand_row = row

        # Select first ligand by default
        if first_ligand_row >= 0:
            rb = _inner_widget(self._comp_table.cellWidget(first_ligand_row, 7))
            if rb:
                rb.setChecked(True)

        self._log.append(
            f"Receptor: {os.path.basename(pdb_path)} "
            f"— {len(self._components)} components")

        # Auto-update center if mode is "To Ligand"
        self._on_center_mode_changed(self._center_grp.checkedButton())

    # ------------------------------------------------------------------ #
    # Center
    # ------------------------------------------------------------------ #

    def _update_sf_params(self):
        """Show/hide parameter sections based on selected scoring functions."""
        vv   = self._sf_vina.isChecked() or self._sf_vinardo.isChecked()
        grid = self._sf_ad4.isChecked()  or self._sf_ad4gpu.isChecked()
        ad4_only = self._sf_ad4.isChecked()
        self._vv_params_widget.setVisible(vv)
        self._grid_params_widget.setVisible(grid)
        self._ad4_only_widget.setVisible(ad4_only)

    def _on_center_mode_changed(self, btn):
        mode = btn.property("mode") if btn else "ligand"
        is_custom = (mode == "custom")
        for sp in (self._cxx, self._cxy, self._cxz):
            sp.setEnabled(is_custom)
        if mode == "ligand":
            self._update_center_from_ligand()
        elif mode == "protein":
            self._update_center_from_protein()

    def _on_box_ligand_changed(self):
        """Called when user picks a different ligand row as Box Ligand."""
        btn = self._center_grp.checkedButton()
        if btn and btn.property("mode") == "ligand":
            self._update_center_from_ligand()

    def _extract_native_ligand_for_queue(self) -> str:
        """
        Extract the radio-button-selected native ligand from the PDB to a
        temp file and return its path, so it can be prepended to the
        docking queue.  Returns '' if no native ligand is selected or on error.
        """
        if not self._current_pdb or not self._components:
            return ''
        native_comp = None
        for row in range(self._comp_table.rowCount()):
            rb = _inner_widget(self._comp_table.cellWidget(row, 7))
            if rb and rb.isChecked() and row < len(self._components):
                native_comp = self._components[row]
                break
        if not native_comp:
            return ''
        try:
            from gui.panels.native_redocking_panel import extract_pdb_component
            import tempfile, os
            text = extract_pdb_component(
                self._current_pdb,
                [native_comp['chain']],
                [native_comp['resname']])
            if not text.strip():
                return ''
            tmp = tempfile.mkdtemp(prefix='ladock_native_lig_')
            out = os.path.join(tmp, f"native_{native_comp['resname']}.pdb")
            with open(out, 'w') as f:
                f.write(text)
            return out
        except Exception:
            return ''

    def _update_center_from_ligand(self):
        if not self._current_pdb or not self._components:
            return
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
                return

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

    # ------------------------------------------------------------------ #
    # Run
    # ------------------------------------------------------------------ #

    def _detect_tools(self):
        from engine.tool_detector import detect_all
        from PySide6.QtCore import QThread, QObject, Signal as Sig

        class _W(QObject):
            done = Sig(dict)
            def run(self):
                self.done.emit(detect_all())

        self._detect_thread = QThread(self)
        self._detect_worker = _W()
        self._detect_worker.moveToThread(self._detect_thread)
        self._detect_thread.started.connect(self._detect_worker.run)
        self._detect_worker.done.connect(self._on_tools_detected)
        self._detect_worker.done.connect(self._detect_thread.quit)
        self._detect_thread.start()

    def _on_tools_detected(self, results: dict):
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
                        "background:#1e3a2e;color:#a6e3a1;border-radius:3px;"
                        "padding:1px 6px;font-size:10px;")
                else:
                    badge.setText(f"❌ {short}")
                    badge.setToolTip(f"{short}: not found")
                    badge.setStyleSheet(
                        "background:#3a1e1e;color:#f38ba8;border-radius:3px;"
                        "padding:1px 6px;font-size:10px;")
        self._update_sf_availability()

    def _update_sf_availability(self):
        """Enable/disable scoring function checkboxes based on tool path availability."""
        import os
        def _available(path: str) -> bool:
            p = path.strip()
            return bool(p) and os.path.isfile(p) and os.access(p, os.X_OK)

        vina_ok   = _available(self._tp_vina.text())
        adgpu_ok  = _available(self._tp_adgpu.text())
        ad4_ok    = _available(self._tp_ad4.text())

        _tip_na = "Tool not found — configure path in Tool Paths"

        for cb, ok in (
            (self._sf_vina,   vina_ok),
            (self._sf_vinardo,vina_ok),
            (self._sf_ad4,    ad4_ok),
            (self._sf_ad4gpu, adgpu_ok),
        ):
            cb.setEnabled(ok)
            if not ok:
                cb.setChecked(False)
                cb.setToolTip(_tip_na)
                cb.setStyleSheet("color:#545d68;font-size:11px;")
            else:
                cb.setToolTip("")
                cb.setStyleSheet("color:#e6edf3;font-size:11px;")

        # Ensure at least Vina stays checked when it becomes available
        if vina_ok and not self._sf_vina.isChecked() and not any(
                cb.isChecked() for cb in (self._sf_vinardo, self._sf_ad4, self._sf_ad4gpu)):
            self._sf_vina.setChecked(True)

    def _pip_install(self, name: str, cmd: str):
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
                QMessageBox.information(self, "Installed",
                    f"{name} installed successfully.\nRe-detecting tools…")
                self._detect_tools()
            else:
                QMessageBox.warning(self, "Install failed", r.stderr[:800])
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_run(self):
        if not self._current_pdb:
            QMessageBox.warning(self, "No Receptor",
                "Select a receptor PDB from the list first.")
            return

        selected_ligs = [item.data(Qt.UserRole)
                         for item in self._lig_list.selectedItems()]
        if not selected_ligs:
            QMessageBox.warning(self, "No Ligands",
                "Select at least one ligand file from the list.")
            return

        # ── Prepend native ligand from receptor if one is selected ──────
        # The component table has a radio column (col 7) for "Box Ligand"
        # which identifies the native ligand.  Extract it to a temp PDB
        # and put it first in the docking queue.
        native_lig_file = self._extract_native_ligand_for_queue()
        if native_lig_file and native_lig_file not in selected_ligs:
            selected_ligs = [native_lig_file] + selected_ligs
        # Collect scoring functions
        sf_types = []
        if self._sf_vina.isChecked():    sf_types.append("vina")
        if self._sf_vinardo.isChecked(): sf_types.append("vinardo")
        if self._sf_ad4.isChecked():     sf_types.append("ad4")
        if self._sf_ad4gpu.isChecked():  sf_types.append("ad4gpu")
        if not sf_types:
            QMessageBox.warning(self, "No Scoring",
                "Select at least one scoring function.")
            return

        listmode = []
        if self._mode_rigid.isChecked():    listmode.append("rigid")
        if self._mode_flexible.isChecked(): listmode.append("flexible")
        if not listmode:
            QMessageBox.warning(self, "No Mode",
                "Select at least one docking mode.")
            return

        mode_btn = self._center_grp.checkedButton()
        mode = mode_btn.property("mode") if mode_btn else "ligand"
        if mode == "ligand":
            self._update_center_from_ligand()
        elif mode == "protein":
            self._update_center_from_protein()

        use_wsl_backend = str(QSettings("LADOCK", "Desktop").value(
            "use_wsl_backend", os.name == "nt"
        )).lower() in ("1", "true", "yes")
        wsl_distro = str(QSettings("LADOCK", "Desktop").value(
            "wsl_distro", ""
        )).strip()
        mgltools_dir = resolve_mgltools_dir(
            self._tp_mgltools.text().strip(),
            use_wsl_backend=use_wsl_backend,
        )
        adfrsuite_dir = resolve_adfrsuite_dir(
            self._tp_adfr.text().strip() or self._tp_agfr.text().strip(),
            use_wsl_backend=use_wsl_backend,
        )

        params = {
            'pdb_path':            self._current_pdb,
            'ligand_files':        selected_ligs,
            # Box
            'cx': self._cxx.value(), 'cy': self._cxy.value(),
            'cz': self._cxz.value(),
            'sx': self._sxx.value(), 'sy': self._sxy.value(),
            'sz': self._sxz.value(),
            'box_size':            f"{int(self._sxx.value())},{int(self._sxy.value())},{int(self._sxz.value())}",
            'spacing':             self._spacing.value(),
            # Scoring & mode
            'sf_types':            sf_types,
            'listmode':            listmode,
            # Multi-ligand
            'arrangement_type':    self._arr_type.currentText(),
            'elements':            [str(self._elements.value())],
            # Search — separate exhaustiveness per engine type
            'exhaustiveness':      self._exhaustiveness.value(),
            'ad4_exhaustiveness':  self._ad4_exhaustiveness.value(),
            'n_poses':             self._n_poses.value(),
            'energy_range':        self._energy_range.value(),
            'cpu':                 self._cpu.value(),
            'max_workers':         self._max_workers.value(),
            'seed':                self._seed.value(),
            'ga_pop_size':         self._ga_pop_size.value(),
            'cluster_rmsd':        self._cluster_rmsd.value(),
            # Flexible receptor
            'distance':            self._flex_dist.value(),
            'flexible_residues':   self._flex_residues.text().strip(),
            # I/O options
            'input_file_saved':    str(self._save_input.isChecked()).lower(),
            'output_file_saved':   str(self._save_output.isChecked()).lower(),
            'parallel_simulation': str(self._parallel.isChecked()).lower(),
            # Tool paths
            'vina_path':           resolve_tool_path(
                "vina", self._tp_vina.text().strip(), use_wsl_backend=use_wsl_backend
            ),
            'ag4_path':            resolve_tool_path(
                "autogrid4", self._tp_ag4.text().strip(), use_wsl_backend=use_wsl_backend
            ),
            'ad4_path':            resolve_tool_path(
                "autodock4", self._tp_ad4.text().strip(), use_wsl_backend=use_wsl_backend
            ),
            'autodockgpu':         resolve_tool_path(
                "autodock_gpu", self._tp_adgpu.text().strip(), use_wsl_backend=use_wsl_backend
            ),
            'agfr':                resolve_tool_path(
                "agfr",
                os.path.join(adfrsuite_dir, 'bin', 'agfr') if adfrsuite_dir else self._tp_agfr.text().strip(),
                use_wsl_backend=use_wsl_backend,
            ),
            'adfr':                resolve_tool_path(
                "adfr",
                os.path.join(adfrsuite_dir, 'bin', 'adfr') if adfrsuite_dir else self._tp_adfr.text().strip(),
                use_wsl_backend=use_wsl_backend,
            ),
            'mgltools_dir':        mgltools_dir,
        }

        # Derive MGLTools paths
        _pythonsh = os.path.join(mgltools_dir, 'bin', 'pythonsh')
        _util24   = os.path.join(mgltools_dir, 'MGLToolsPckgs',
                                 'AutoDockTools', 'Utilities24')
        _prep_rec  = os.path.join(_util24, 'prepare_receptor4.py')
        _prep_lig  = os.path.join(_util24, 'prepare_ligand4.py')
        _prep_gpf  = os.path.join(_util24, 'prepare_gpf4.py')
        _prep_dpf  = os.path.join(_util24, 'prepare_dpf42.py')
        _prep_flex = os.path.join(_util24, 'prepare_flexreceptor4.py')
        params.update({
            'pythonsh':              _pythonsh,
            'prepare_receptor':      _prep_rec,
            'prepare_ligand':        _prep_lig,
            'prepare_gpf':           _prep_gpf,
            'prepare_dpf':           _prep_dpf,
            'prepare_flexreceptor':  _prep_flex,
            'use_wsl_backend':       use_wsl_backend,
            'wsl_distro':            wsl_distro,
        })

        # Validate MGLTools
        if params.get('use_wsl_backend') and os.name == "nt" and not wsl_available():
            QMessageBox.warning(
                self, "WSL Not Found",
                "WSL backend is enabled, but `wsl.exe` is not available on this Windows system."
            )
            return

        missing = []
        if not os.path.isfile(_pythonsh):
            missing.append(f"pythonsh: {_pythonsh}")
        if not os.path.isfile(_prep_rec):
            missing.append(f"prepare_receptor4.py: {_prep_rec}")
        if not os.path.isfile(_prep_lig):
            missing.append(f"prepare_ligand4.py: {_prep_lig}")
        if missing:
            QMessageBox.warning(self, "MGLTools Not Found",
                "Required MGLTools scripts not found:\n\n" +
                "\n".join(f"  • {m}" for m in missing) +
                "\n\nConfigure MGLTools Path in Tool Paths section.")
            return

        # Auto-compute flex residues if flexible mode
        flex_residues_list = []
        if 'flexible' in listmode:
            flex_dist     = params.get('distance', 3.0)
            current_field = params['flexible_residues']
            if current_field:
                flex_residues_list = [r.strip()
                                      for r in current_field.split('_')
                                      if r.strip()]
            else:
                flex_residues_list = find_flex_residues(
                    params['pdb_path'],
                    cx=params['cx'], cy=params['cy'], cz=params['cz'],
                    cutoff=flex_dist)
                self._flex_residues.setText('_'.join(flex_residues_list))
            if not flex_residues_list:
                QMessageBox.warning(self, "No Flex Residues",
                    f"No residues found within {flex_dist} Å of box center.\n"
                    "Increase Flex Distance or use Rigid mode.")
                return
        params['flex_residues_list'] = flex_residues_list

        # Extract receptor components to temp PDB (component-aware, not just by chain)
        rec_chains = []
        rec_components = []
        for row in range(self._comp_table.rowCount()):
            cb = _inner_widget(self._comp_table.cellWidget(row, 6))
            if cb and cb.isChecked() and row < len(self._components):
                comp = self._components[row]
                rec_components.append(comp)
                chain = comp.get('chain', '')
                if chain not in rec_chains:
                    rec_chains.append(chain)
        if not rec_components:
            QMessageBox.warning(self, "No Receptor",
                "Mark at least one component as receptor in the component table.")
            return

        tmp_dir = tempfile.mkdtemp(prefix="ladock_ligtest_")
        rec_pdb = os.path.join(tmp_dir, 'receptor.pdb')
        rec_text = extract_pdb_component(params['pdb_path'], rec_chains,
                                         components=rec_components)
        rec_text = sanitize_pdb_text_for_mgltools(rec_text)
        Path(rec_pdb).write_text(rec_text, encoding='utf-8')
        params['receptor_pdb'] = rec_pdb
        params['tmp_dir']      = tmp_dir
        params['_selected_ligs'] = selected_ligs  # store for job name
        if use_wsl_backend and self._job_dir:
            run_tag = datetime.datetime.now().strftime("ligtest_%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
            run_root = os.path.join(self._job_dir, "docking_runs", run_tag)
            ligand_pdbqt_dir = os.path.join(self._job_dir, "ligand_ready_pdbqt")
            receptor_dir = os.path.join(run_root, "receptor")
            output_dir = os.path.join(run_root, "output")
            os.makedirs(ligand_pdbqt_dir, exist_ok=True)
            os.makedirs(receptor_dir, exist_ok=True)
            os.makedirs(output_dir, exist_ok=True)
            params['persistent_run_dir'] = run_root
            params['persistent_ligand_pdbqt_dir'] = ligand_pdbqt_dir
            params['persistent_receptor_dir'] = receptor_dir
            params['persistent_output_dir'] = output_dir

        # ── If a job is already running → enqueue as Pending ─────────────
        if self._current_job is not None:
            self._enqueue_pending(params)
            return

        # ── No running job → start immediately ──────────────────────────
        self._start_job(params)


    def _enqueue_pending(self, params: dict):
        """Add params to the pending queue and register a QUEUED job entry."""
        from core.job_scheduler import DockingJob, JobStatus
        sel = params.get('_selected_ligs', [])
        sf_types  = params.get('sf_types', [])
        listmode  = params.get('listmode', [])
        rec_tag   = os.path.splitext(os.path.basename(params['pdb_path']))[0][:20]
        sf_tag    = '+'.join(sf_types)
        job_name  = f"LigTest {rec_tag} [{sf_tag}] × {len(sel)} lig(s)"
        queued_job = DockingJob(
            job_id     = uuid.uuid4().hex[:8],
            name       = job_name,
            parameters = {'receptor':  params['pdb_path'],
                          'n_ligands': len(sel),
                          'sf_types':  sf_types,
                          'mode':      listmode},
            status     = JobStatus.QUEUED,
            progress   = 0,
            created_at = datetime.datetime.now().isoformat(),
        )
        params['_job_id'] = queued_job.job_id
        self._pending_queue.append(params)
        self.job_registered.emit(queued_job)
        queue_len = len(self._pending_queue)
        self._log.append(
            f"\n📋 Job added to queue (position {queue_len}): {job_name}\n"
            "   Will start after current job finishes.")

    def _start_job(self, params: dict):
        """Start a docking job immediately (called when no job is running)."""
        from core.job_scheduler import DockingJob, JobStatus

        sel       = params.get('_selected_ligs', [])
        sf_types  = params.get('sf_types', [])
        listmode  = params.get('listmode', [])
        tmp_dir   = params['tmp_dir']

        flex_residues_list = params.get('flex_residues_list', [])
        flex_info = (f"\nFlex residues : {', '.join(flex_residues_list)}"
                     if flex_residues_list else "")

        self._log.clear()
        self._log.append(
            f"Receptor      : {os.path.basename(params['pdb_path'])}\n"
            f"Ligands       : {len(sel)} file(s)\n"
            f"Scoring       : {', '.join(sf_types)}\n"
            f"Mode          : {', '.join(listmode)}{flex_info}\n"
            "Center        : ({params['cx']:.2f}, {params['cy']:.2f}, "
            "{params['cz']:.2f})\n"
            f"Box           : {params['sx']}×{params['sy']}×{params['sz']} Å  "
            f"| Spacing: {params['spacing']} Å\n"
            f"Working dir   : {tmp_dir}\n"
            "─────────────────────────────────────────────")
        if params.get('persistent_run_dir'):
            self._log.append(
                f"Saved ligands : {params.get('persistent_ligand_pdbqt_dir', '')}\n"
                f"Saved outputs : {params.get('persistent_output_dir', '')}\n"
                "─────────────────────────────────────────────")

        self._result_table.setRowCount(0)
        self._prog.setValue(0)
        self._prog.setVisible(True)
        self._rec_name = os.path.splitext(os.path.basename(params['pdb_path']))[0]
        self._last_rec_pdbqt = os.path.join(tmp_dir, 'receptor.pdbqt')

        rec_tag  = self._rec_name[:20]
        sf_tag   = '+'.join(sf_types)
        job_name = f"LigTest {rec_tag} [{sf_tag}] × {len(sel)} lig(s)"

        # Reuse job_id if this came from the pending queue
        pending_job_id = params.pop('_job_id', None)
        self._current_job = DockingJob(
            job_id     = pending_job_id or uuid.uuid4().hex[:8],
            name       = job_name,
            parameters = {'receptor': params['pdb_path'],
                          'n_ligands': len(sel),
                          'sf_types':  sf_types,
                          'mode':      listmode},
            status     = JobStatus.RUNNING,
            progress   = 0,
            created_at  = datetime.datetime.now().isoformat(),
            started_at  = datetime.datetime.now().isoformat(),
        )
        self.job_registered.emit(self._current_job)

        self._thread = QThread()
        self._worker = _LigandDockingWorker(params)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._queue_log)
        self._worker.log.connect(
            lambda m, jid=self._current_job.job_id: self.job_log_line.emit(jid, m))
        self._worker.progress.connect(lambda m: self._queue_log(f"⏳ {m}"))
        self._worker.progress.connect(
            lambda m, jid=self._current_job.job_id: self.job_log_line.emit(jid, f"⏳ {m}"))
        self._worker.progress_pct.connect(self._on_progress_pct)
        self._worker.finished.connect(self._on_docking_done)
        self._worker.error.connect(self._on_docking_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._log_flush_timer.start()
        self._thread.start()


    @Slot(int)
    def _on_progress_pct(self, pct: int):
        """Update progress bar and job tracker with per-ligand percentage."""
        self._prog.setValue(pct)
        if self._current_job:
            self._current_job.progress = pct
            self.job_status_changed.emit(self._current_job)

    @Slot(object)
    def _on_docking_done(self, results: list):
        self._log_flush_timer.stop()
        self._flush_log_buffer()
        self._prog.setVisible(False)
        # Parse each result → append best pose row
        for result in results:
            lig_name = result.get("lig_name", "")
            sf = result.get("sf", "")
            out_path = result.get("out_path", "")
            if sf.endswith('/ad4') or sf.endswith('/ad4gpu') or sf in ('ad4', 'ad4gpu'):
                self._parse_dlg_best(lig_name, sf, out_path)
            else:
                self._parse_vina_best(lig_name, sf, out_path)
        total = self._result_table.rowCount()
        self._log.append(
            f"\n✔  Docking complete — {total} result(s) from "
            f"{len(results)} run(s)")
        # Save results to CSV and notify
        csv_path = self._save_result_csv(results)
        if csv_path:
            self._log.append(f"📄 Results CSV: {csv_path}")
            self.result_csv_ready.emit(csv_path)
        # Update job tracker
        if self._current_job:
            from core.job_scheduler import JobStatus
            self._current_job.status      = JobStatus.FINISHED
            self._current_job.progress    = 100
            self._current_job.finished_at = datetime.datetime.now().isoformat()
            self._current_job.result_csv  = csv_path
            self.job_status_changed.emit(self._current_job)
        self._current_job = None
        self._run_btn.setEnabled(True)
        self._start_next_queued()

    def _save_result_csv(self, results: list) -> str:
        """Save result table to a timestamped CSV. Returns path or empty string."""
        import csv as _csv
        import json as _json
        if self._result_table.rowCount() == 0:
            return ""
        try:
            out_dir = os.path.join(self._job_dir, "results") if self._job_dir else ""
            if not out_dir:
                out_dir = os.path.dirname(results[0].get("out_path", "")) if results else ""
            os.makedirs(out_dir, exist_ok=True)

            ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            rec_tag = os.path.splitext(
                os.path.basename(self._current_pdb))[0][:20] if self._current_pdb else "run"
            csv_path = os.path.join(out_dir, f"results_{rec_tag}_{ts}.csv")

            headers = [self._result_table.horizontalHeaderItem(c).text()
                       for c in range(self._result_table.columnCount())]
            headers = headers[:3] + ["smiles"] + headers[3:]
            rows_meta = []
            with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
                writer = _csv.writer(fh)
                writer.writerow(headers)
                for row in range(self._result_table.rowCount()):
                    row_vals = [
                        (self._result_table.item(row, col).text()
                         if self._result_table.item(row, col) else "")
                        for col in range(self._result_table.columnCount())
                    ]
                    smiles = results[row].get("smiles", "") if row < len(results) else ""
                    row_vals = row_vals[:3] + [smiles] + row_vals[3:]
                    writer.writerow(row_vals)
                    first_item = self._result_table.item(row, 0)
                    rows_meta.append({
                        "output_path": first_item.data(Qt.UserRole) if first_item else "",
                        "label": self._result_table.item(row, 2).text()
                                 if self._result_table.item(row, 2) else "",
                        "smiles": smiles,
                        "source_path": results[row].get("source_path", "") if row < len(results) else "",
                    })

            meta = {
                "receptor_pdbqt": getattr(self, '_last_rec_pdbqt', ''),
                "receptor_pdb":   self._current_pdb,
                "rows": rows_meta,
            }
            meta_path = csv_path.replace('.csv', '.meta.json')
            with open(meta_path, 'w', encoding='utf-8') as fh:
                _json.dump(meta, fh, indent=2)

            return csv_path
        except Exception as e:
            self._log.append(f"⚠ Could not save CSV: {e}")
            return ""

    @Slot(str)
    def _on_docking_error(self, msg: str):
        self._log_flush_timer.stop()
        self._flush_log_buffer()
        self._prog.setVisible(False)
        self._log.append(f"\n❌ Error: {msg}")
        if self._current_job:
            from core.job_scheduler import JobStatus
            self._current_job.status      = JobStatus.FAILED
            self._current_job.error       = msg
            self._current_job.finished_at = datetime.datetime.now().isoformat()
            self.job_status_changed.emit(self._current_job)
        self._current_job = None
        self._run_btn.setEnabled(True)
        self._start_next_queued()

    def _start_next_queued(self):
        """If there are pending jobs in the queue, start the next one."""
        if not self._pending_queue:
            return
        next_params = self._pending_queue.pop(0)
        queue_left = len(self._pending_queue)
        self._log.append(
            "\n▶ Starting next queued job "
            f"({'%d remaining in queue' % queue_left if queue_left else 'last in queue'})…")
        self._start_job(next_params)

    def _parse_vina_best(self, lig_name: str, sf: str, pdbqt_path: str):
        """Parse first (best) pose from Vina PDBQT output and append one row."""
        try:
            text = Path(pdbqt_path).read_text(encoding='utf-8', errors='replace')
        except OSError:
            self._log.append(f"⚠ Cannot read: {pdbqt_path}")
            return
        for line in text.splitlines():
            if line.startswith('REMARK VINA RESULT:'):
                parts = line.split()
                if len(parts) >= 5:
                    energy  = parts[3]
                    rmsd_lb = parts[4]
                    rmsd_ub = parts[5] if len(parts) > 5 else "—"
                    self._append_result_row(lig_name, sf, energy, rmsd_lb, rmsd_ub,
                                            out_path=pdbqt_path)
                    self._log.append(
                        f"  [{sf}] {lig_name}: ΔG={energy} kcal/mol → {pdbqt_path}")
                    return
        self._log.append(f"⚠ No VINA RESULT in {pdbqt_path}")

    def _parse_dlg_best(self, lig_name: str, sf: str, dlg_path: str):
        """Parse best pose from AD4/AD4GPU DLG output and append one row."""
        try:
            text = Path(dlg_path).read_text(encoding='utf-8', errors='replace')
        except OSError:
            self._log.append(f"⚠ Cannot read: {dlg_path}")
            return
        # Try RANKING table first (AD4GPU format)
        for line in text.splitlines():
            m = re.match(
                r'\s*RANKING\s+1\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)', line)
            if m:
                self._append_result_row(lig_name, sf,
                                        m.group(1), m.group(2), m.group(3),
                                        out_path=dlg_path)
                self._log.append(
                    f"  [{sf}] {lig_name}: ΔG={m.group(1)} kcal/mol → {dlg_path}")
                return
        # Fallback: first DOCKED block energy
        energy = rmsd = None
        for line in text.splitlines():
            if 'Estimated Free Energy of Binding' in line:
                m = re.search(r'=\s*([-\d.]+)', line)
                if m and energy is None:
                    energy = m.group(1)
            elif 'RMSD from reference' in line and energy is not None:
                m = re.search(r'=\s*([\d.]+)', line)
                if m and rmsd is None:
                    rmsd = m.group(1)
            if energy and rmsd:
                self._append_result_row(lig_name, sf, energy, rmsd, "—",
                                        out_path=dlg_path)
                self._log.append(
                    f"  [{sf}] {lig_name}: ΔG={energy} kcal/mol → {dlg_path}")
                return
        # Last fallback: summary table row 1
        for line in text.splitlines():
            m = re.match(
                r'\s*1\s+\|\s*([-\d.]+)\s+\|\s*([\d.]+)\s+\|\s*([\d.]+)', line)
            if m:
                self._append_result_row(lig_name, sf,
                                        m.group(1), m.group(2), m.group(3),
                                        out_path=dlg_path)
                self._log.append(
                    f"  [{sf}] {lig_name}: ΔG={m.group(1)} kcal/mol → {dlg_path}")
                return
        self._log.append(f"⚠ No docking result found in {dlg_path}")

    def _append_result_row(self, lig_name: str, sf: str,
                           energy: str, rmsd_lb: str, rmsd_ub: str,
                           out_path: str = ""):
        """Insert one row into the result table."""
        row = self._result_table.rowCount()
        self._result_table.insertRow(row)
        display_name = f"{lig_name} [{sf}]"
        vals = [str(row + 1), self._rec_name, display_name, energy, rmsd_lb, rmsd_ub]
        for col, val in enumerate(vals):
            item = QTableWidgetItem(val)
            item.setTextAlignment(Qt.AlignCenter)
            if col == 0 and out_path:
                item.setData(Qt.UserRole, out_path)
            if col == 3:  # ΔG column — color by affinity
                try:
                    e = float(energy)
                    color = ('#a6e3a1' if e < -8 else
                             '#fab387' if e < -6 else '#f38ba8')
                    item.setForeground(QBrush(QColor(color)))
                except ValueError:
                    pass
            self._result_table.setItem(row, col, item)
