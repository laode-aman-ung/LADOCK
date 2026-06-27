"""
LADOCK — Molecular Preparation Panel
======================================
Panel untuk preparasi reseptor (protein PDB) dan ligan sebelum docking.

Alur:
  1. File di-load dari DockingSetupPanel (via file_preview_requested signal)
  2. User memilih step preparasi
  3. Klik "Run Preparation" → engine/mol_prep.py dijalankan di thread terpisah
  4. Hasil preview di QTextEdit (PDB/SDF preview)
  5. "Save to receptor_ready/" atau "Save to ligand_ready/"
"""

from __future__ import annotations
import os
import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QGroupBox, QTextEdit, QScrollArea, QSplitter,
    QFileDialog, QMessageBox, QFrame, QProgressBar, QComboBox,
    QLineEdit, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, Slot
from PySide6.QtGui import QFont, QColor

from gui.widgets.common import SectionLabel, HDivider, StatusBadge


# ── Worker ──────────────────────────────────────────────────────────────────

class _PrepWorker(QObject):
    finished = Signal(str, str)   # (output_str, report)
    error    = Signal(str)

    def __init__(self, mol_path: str, role: str, steps: list[str],
                 keep_chains: list[str]):
        super().__init__()
        self.mol_path    = mol_path
        self.role        = role          # 'receptor' or 'ligand'
        self.steps       = steps
        self.keep_chains = keep_chains

    @Slot()
    def run(self):
        try:
            from engine.mol_prep import prep_receptor, prep_ligand
            if self.role == 'receptor':
                result = prep_receptor(self.mol_path, self.steps,
                                       keep_chains=self.keep_chains or None)
            else:
                result = prep_ligand(self.mol_path, self.steps)
            self.finished.emit(result.output_str, result.full_report())
        except Exception as e:
            self.error.emit(str(e))


# ── Styled checkbox group ────────────────────────────────────────────────────

def _make_check(label: str, tooltip: str = "") -> QCheckBox:
    cb = QCheckBox(label)
    cb.setToolTip(tooltip)
    cb.setStyleSheet("color:#cdd6f4; font-size:12px;")
    return cb


# ═══════════════════════════════════════════════════════════════════════════ #
# Main Panel
# ═══════════════════════════════════════════════════════════════════════════ #

class PrepPanel(QWidget):
    """
    Molecular Preparation Panel.

    Signals
    -------
    prepared_file_ready(path: str)   — emitted after file saved to ready dir
    preview_molecule(path: str)      — ask viewer to display the given file
    """

    prepared_file_ready = Signal(str)
    preview_molecule    = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_file: str = ""
        self._current_role: str = ""   # 'receptor' or 'ligand'
        self._prepared_str: str = ""
        self._thread  = None
        self._worker  = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── LEFT: controls ─────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(320)
        lv = QVBoxLayout(left)
        lv.setSpacing(8)
        lv.setContentsMargins(0, 0, 0, 0)

        lv.addWidget(SectionLabel("🔬 Molecular Preparation"))

        # File info
        info_box = QGroupBox("Source File")
        info_box.setStyleSheet("QGroupBox{color:#89b4fa;border:1px solid #313244;"
                               "border-radius:4px;margin-top:8px;padding-top:6px;}"
                               "QGroupBox::title{subcontrol-origin:margin;left:8px;}")
        ib = QVBoxLayout(info_box)
        self._lbl_file = QLabel("No file loaded")
        self._lbl_file.setWordWrap(True)
        self._lbl_file.setStyleSheet("color:#cdd6f4; font-size:11px;")
        self._lbl_role = StatusBadge("—", "#45475a")
        ib.addWidget(self._lbl_file)
        ib.addWidget(self._lbl_role)
        lv.addWidget(info_box)

        # ── Receptor steps ─────────────────────────────────────────────
        self._grp_receptor = QGroupBox("Receptor Preparation Steps")
        self._grp_receptor.setStyleSheet(
            "QGroupBox{color:#89b4fa;border:1px solid #313244;"
            "border-radius:4px;margin-top:8px;padding-top:6px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:8px;}")
        rv = QVBoxLayout(self._grp_receptor)

        self._cb_rm_water  = _make_check("Remove Water (HOH/WAT)",
            "Delete all water molecules from the structure")
        self._cb_rm_het    = _make_check("Remove HETATM",
            "Delete all heteroatom records (small molecules, cofactors)")
        self._cb_rm_metal  = _make_check("Remove Metal Ions",
            "Delete metal ion records (Zn, Mg, Ca, Fe, …)")
        self._cb_keep_chain = _make_check("Keep Chain Only:",
            "Discard atoms not belonging to specified chain(s)")
        self._chain_edit    = QLineEdit("A")
        self._chain_edit.setFixedWidth(60)
        self._chain_edit.setStyleSheet(
            "background:#313244; color:#cdd6f4; border:1px solid #45475a;"
            "border-radius:3px; padding:2px 4px;")
        chain_row = QHBoxLayout()
        chain_row.addWidget(self._cb_keep_chain)
        chain_row.addWidget(self._chain_edit)
        chain_row.addStretch()
        self._cb_fix_pdb   = _make_check("Fix / Sanitize PDB",
            "Round-trip through RDKit to fix atom names and format")
        self._cb_rec_addh  = _make_check("Add Hydrogens",
            "Add missing hydrogen atoms (RDKit)")
        self._cb_rec_chrg  = _make_check("Add Gasteiger Charges",
            "Compute Gasteiger partial charges (stored in B-factor)")

        # Default selections for receptor
        self._cb_rm_water.setChecked(True)
        self._cb_fix_pdb.setChecked(True)

        rv.addWidget(self._cb_rm_water)
        rv.addWidget(self._cb_rm_het)
        rv.addWidget(self._cb_rm_metal)
        rv.addLayout(chain_row)
        rv.addWidget(self._cb_fix_pdb)
        rv.addWidget(self._cb_rec_addh)
        rv.addWidget(self._cb_rec_chrg)
        lv.addWidget(self._grp_receptor)

        # ── Ligand steps ────────────────────────────────────────────────
        self._grp_ligand = QGroupBox("Ligand Preparation Steps")
        self._grp_ligand.setStyleSheet(
            "QGroupBox{color:#a6e3a1;border:1px solid #313244;"
            "border-radius:4px;margin-top:8px;padding-top:6px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:8px;}")
        lgv = QVBoxLayout(self._grp_ligand)

        self._cb_lig_addh   = _make_check("Add Hydrogens",
            "Add explicit hydrogen atoms")
        self._cb_lig_embed  = _make_check("Generate 3D Conformer (ETKDGv3)",
            "Embed a 3D conformer if none exists")
        self._cb_lig_opt    = _make_check("Optimize Geometry (MMFF94)",
            "Energy minimization using MMFF94 force field")
        self._cb_lig_chrg   = _make_check("Add Gasteiger Charges",
            "Compute Gasteiger partial charges")

        self._cb_lig_addh.setChecked(True)
        self._cb_lig_embed.setChecked(True)
        self._cb_lig_opt.setChecked(True)
        self._cb_lig_chrg.setChecked(True)

        lgv.addWidget(self._cb_lig_addh)
        lgv.addWidget(self._cb_lig_embed)
        lgv.addWidget(self._cb_lig_opt)
        lgv.addWidget(self._cb_lig_chrg)
        lv.addWidget(self._grp_ligand)

        lv.addWidget(HDivider())

        # Run button
        self._run_btn = QPushButton("⚗ Run Preparation")
        self._run_btn.setEnabled(False)
        self._run_btn.setFixedHeight(34)
        self._run_btn.setStyleSheet(
            "QPushButton{background:#89b4fa;color:#1e1e2e;border-radius:4px;"
            "font-weight:bold;font-size:13px;}"
            "QPushButton:hover{background:#b4d0fa;}"
            "QPushButton:disabled{background:#45475a;color:#6c7086;}")
        self._run_btn.clicked.connect(self._on_run)
        lv.addWidget(self._run_btn)

        self._prog = QProgressBar()
        self._prog.setRange(0, 0)   # indeterminate
        self._prog.setVisible(False)
        self._prog.setFixedHeight(6)
        self._prog.setStyleSheet(
            "QProgressBar{border:none;background:#313244;border-radius:3px;}"
            "QProgressBar::chunk{background:#89b4fa;border-radius:3px;}")
        lv.addWidget(self._prog)

        lv.addWidget(HDivider())

        # Save buttons
        self._save_receptor_btn = QPushButton("💾 Save to receptor_ready/")
        self._save_ligand_btn   = QPushButton("💾 Save to ligand_ready/")
        self._preview_btn       = QPushButton("👁 Preview in Viewer")
        for btn in (self._save_receptor_btn, self._save_ligand_btn, self._preview_btn):
            btn.setEnabled(False)
            btn.setFixedHeight(30)
            btn.setStyleSheet(
                "QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
                "border-radius:4px;font-size:12px;}"
                "QPushButton:hover{background:#45475a;}"
                "QPushButton:disabled{color:#6c7086;}")
        self._save_receptor_btn.clicked.connect(self._on_save_receptor)
        self._save_ligand_btn.clicked.connect(self._on_save_ligand)
        self._preview_btn.clicked.connect(self._on_preview)
        lv.addWidget(self._save_receptor_btn)
        lv.addWidget(self._save_ligand_btn)
        lv.addWidget(self._preview_btn)

        lv.addStretch()
        root.addWidget(left)

        # ── RIGHT: report + preview ──────────────────────────────────────
        right = QWidget()
        rv2 = QVBoxLayout(right)
        rv2.setContentsMargins(0, 0, 0, 0)
        rv2.setSpacing(4)

        rv2.addWidget(SectionLabel("📋 Preparation Report"))
        self._report_txt = QTextEdit()
        self._report_txt.setReadOnly(True)
        self._report_txt.setFixedHeight(130)
        self._report_txt.setStyleSheet(
            "background:#1e1e2e; color:#a6e3a1; font-family:monospace; font-size:11px;"
            "border:1px solid #313244; border-radius:4px;")
        self._report_txt.setPlaceholderText("Preparation report will appear here…")
        rv2.addWidget(self._report_txt)

        rv2.addWidget(SectionLabel("📄 Prepared Structure Preview"))
        self._preview_txt = QTextEdit()
        self._preview_txt.setReadOnly(True)
        self._preview_txt.setStyleSheet(
            "background:#181825; color:#cdd6f4; font-family:monospace; font-size:10px;"
            "border:1px solid #313244; border-radius:4px;")
        self._preview_txt.setPlaceholderText("Prepared file content will appear here…")
        rv2.addWidget(self._preview_txt)

        root.addWidget(right, 1)

        # Initial state
        self._update_groups()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def load_file(self, path: str):
        """Called when a file is selected in the Job Dir tree."""
        if not os.path.isfile(path):
            return
        self._current_file = path
        parent_dir = os.path.basename(os.path.dirname(path))
        ext = os.path.splitext(path)[1].lower()

        # Detect role
        if parent_dir == 'target_input' or ext in ('.pdb', '.pdbqt'):
            self._current_role = 'receptor'
        else:
            self._current_role = 'ligand'

        self._lbl_file.setText(f"📄 {os.path.basename(path)}\n📁 {os.path.dirname(path)}")
        role_label = "🧬 Receptor" if self._current_role == 'receptor' else "💊 Ligand"
        self._lbl_role.setText(role_label)

        self._prepared_str = ""
        self._report_txt.clear()
        self._preview_txt.clear()
        self._run_btn.setEnabled(True)
        self._save_receptor_btn.setEnabled(False)
        self._save_ligand_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)
        self._update_groups()

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _update_groups(self):
        is_receptor = (self._current_role == 'receptor')
        self._grp_receptor.setEnabled(is_receptor)
        self._grp_ligand.setEnabled(not is_receptor)

    def _collect_receptor_steps(self) -> list[str]:
        steps = []
        if self._cb_rm_water.isChecked():   steps.append('remove_water')
        if self._cb_rm_het.isChecked():     steps.append('remove_hetatm')
        if self._cb_rm_metal.isChecked():   steps.append('remove_metal')
        if self._cb_keep_chain.isChecked(): steps.append('keep_chain')
        if self._cb_fix_pdb.isChecked():    steps.append('fix_pdb')
        if self._cb_rec_addh.isChecked():   steps.append('add_h')
        if self._cb_rec_chrg.isChecked():   steps.append('add_charge')
        return steps

    def _collect_ligand_steps(self) -> list[str]:
        steps = []
        if self._cb_lig_addh.isChecked():  steps.append('add_h')
        if self._cb_lig_embed.isChecked(): steps.append('embed_3d')
        if self._cb_lig_opt.isChecked():   steps.append('optimize')
        if self._cb_lig_chrg.isChecked():  steps.append('add_charge')
        return steps

    def _append_report(self, text: str):
        self._report_txt.append(text)

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def _on_run(self):
        if not self._current_file:
            return

        if self._current_role == 'receptor':
            steps = self._collect_receptor_steps()
            chains = [c.strip() for c in self._chain_edit.text().split(',') if c.strip()]
        else:
            steps = self._collect_ligand_steps()
            chains = []

        if not steps:
            QMessageBox.information(self, "No Steps", "Select at least one preparation step.")
            return

        self._run_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._report_txt.clear()
        self._preview_txt.clear()
        self._prepared_str = ""

        self._thread = QThread()
        self._worker = _PrepWorker(self._current_file, self._current_role, steps, chains)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_prep_done)
        self._worker.error.connect(self._on_prep_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _on_prep_done(self, output_str: str, report: str):
        self._prepared_str = output_str
        self._prog.setVisible(False)
        self._run_btn.setEnabled(True)

        self._report_txt.setPlainText(report)
        self._preview_txt.setPlainText(output_str)

        # Enable save buttons
        self._save_receptor_btn.setEnabled(self._current_role == 'receptor')
        self._save_ligand_btn.setEnabled(self._current_role == 'ligand')
        self._preview_btn.setEnabled(True)

    def _on_prep_error(self, msg: str):
        self._prog.setVisible(False)
        self._run_btn.setEnabled(True)
        self._report_txt.setPlainText(f"❌ ERROR:\n{msg}")

    def _save_prepared(self, subdir: str):
        if not self._prepared_str:
            QMessageBox.warning(self, "Nothing to Save", "Run preparation first.")
            return

        job_dir = self._guess_job_dir()
        if not job_dir:
            job_dir = QFileDialog.getExistingDirectory(self, "Select Job Directory")
        if not job_dir:
            return

        dest_dir = os.path.join(job_dir, subdir)
        os.makedirs(dest_dir, exist_ok=True)

        orig_name = Path(self._current_file).stem
        ext = '.pdb' if self._current_role == 'receptor' else '.sdf'
        dest_path = os.path.join(dest_dir, f"{orig_name}_prepared{ext}")

        Path(dest_path).write_text(self._prepared_str, encoding='utf-8')
        self._append_report(f"\n✔ Saved → {dest_path}")
        QMessageBox.information(self, "Saved",
            f"File saved to:\n{dest_path}")
        self.prepared_file_ready.emit(dest_path)

    def _on_save_receptor(self):
        self._save_prepared("receptor_ready")

    def _on_save_ligand(self):
        self._save_prepared("ligand_ready")

    def _on_preview(self):
        """Write prepared content to a temp file and ask viewer to load it."""
        if not self._prepared_str:
            return
        import tempfile
        ext = '.pdb' if self._current_role == 'receptor' else '.sdf'
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False,
                                          mode='w', encoding='utf-8')
        tmp.write(self._prepared_str)
        tmp.close()
        self.preview_molecule.emit(tmp.name)

    def _guess_job_dir(self) -> str:
        """Try to find job dir from current file's parent hierarchy."""
        p = Path(self._current_file)
        for parent in p.parents:
            if (parent / 'target_input').is_dir() or (parent / 'ligand_input').is_dir():
                return str(parent)
        return ""
