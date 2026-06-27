"""
LADOCK — Docking Preparation Panel
====================================
Panel utama untuk persiapan docking.

Struktur:
  ├── Left (210px)  : Job Directory tree + 3 buttons
  ├── Center (flex) : Dynamic QTabWidget — satu tab per file
  │     🧬 Target Prep tab : preparasi reseptor (steps + report)
  │     💊 Ligand Prep tab : validasi + preparasi ligan
  └── Right (380px) : Shared 3D viewer
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QPushButton,
    QCheckBox, QRadioButton, QGroupBox, QTextEdit, QProgressBar, QTabWidget,
    QFileDialog, QMessageBox, QSplitter, QLineEdit, QScrollArea,
    QApplication, QFrame, QTreeView, QAbstractItemView, QMenu, QInputDialog,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, Slot, QFileSystemWatcher
from PySide6.QtGui import QFont, QDesktopServices, QColor, QBrush
from PySide6.QtCore import QDir, QUrl

from gui.widgets.common import SectionLabel, HDivider, StatusBadge
from data.project import create_legacy_job_directory
from gui.panels.native_redocking_panel import (
    parse_pdb_components, _centered_widget, _inner_widget, _TYPE_COLOR
)

# ═══════════════════════════════════════════════════════════════════════════ #
# Background Worker
# ═══════════════════════════════════════════════════════════════════════════ #

class _PrepWorker(QObject):
    finished = Signal(str, str)   # (output_str, full_report)
    error    = Signal(str)

    def __init__(self, path: str, role: str, steps: list[str], chains: list[str],
                 kept_components: list = None):
        super().__init__()
        self._path            = path
        self._role            = role
        self._steps           = steps
        self._chains          = chains
        self._kept_components = kept_components or []

    @Slot()
    def run(self):
        try:
            from engine.mol_prep import prep_receptor, prep_ligand
            if self._role == 'receptor':
                r = prep_receptor(self._path, self._steps,
                                  keep_chains=self._chains or None,
                                  kept_components=self._kept_components or None)
            else:
                r = prep_ligand(self._path, self._steps)
            self.finished.emit(r.output_str, r.full_report())
        except Exception as e:
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════ #
# Target Preparation Tab
# ═══════════════════════════════════════════════════════════════════════════ #

_BTN_STYLE = (
    "QPushButton{{background:{bg};color:{fg};border-radius:4px;"
    "font-weight:bold;font-size:12px;padding:5px;}}"
    "QPushButton:hover{{background:{hov};}}"
    "QPushButton:disabled{{background:#45475a;color:#6c7086;}}"
)

_CB_STYLE = "QCheckBox{color:#cdd6f4;font-size:12px;}"

_GRP_STYLE = (
    "QGroupBox{{color:{title};border:1px solid #313244;"
    "border-radius:4px;margin-top:8px;padding-top:6px;}}"
    "QGroupBox::title{{subcontrol-origin:margin;left:8px;}}"
)


class TargetPrepTab(QWidget):
    """
    Preparation tab for a single target PDB file.
    Contains: molecular-component table (Keep/Remove) | prep steps | run | save.
    """
    viewer_load = Signal(str)   # request shared viewer to load this path
    saved       = Signal(str)   # path saved to receptor_ready/

    def __init__(self, file_path: str, job_dir: str = "", parent=None):
        super().__init__(parent)
        self.current_file  = file_path
        self._job_dir      = job_dir
        self._prepared_str = ""
        self._thread       = None
        self._worker       = None
        self._components: list[dict] = []
        self._build_ui()
        # Parse components if file is already a PDB
        if file_path and os.path.isfile(file_path):
            self._load_components(file_path)

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── Molecular Components table ───────────────────────────────────
        grp = QGroupBox("Receptor Preparation Steps")
        grp.setStyleSheet(_GRP_STYLE.format(title="#89b4fa"))
        gv = QVBoxLayout(grp)
        gv.setSpacing(4)

        self._comp_table = QTableWidget(0, 7)
        self._comp_table.setHorizontalHeaderLabels(
            ["Chain", "ResName", "ResSeq", "Type", "#Res", "#Atoms", "Keep"])
        self._comp_table.horizontalHeader().setStyleSheet(
            "QHeaderView::section{background:#181825;color:#89b4fa;"
            "border:1px solid #313244;padding:3px;font-size:11px;}")
        self._comp_table.setStyleSheet(
            "QTableWidget{background:#1e1e2e;color:#cdd6f4;"
            "border:1px solid #313244;gridline-color:#313244;font-size:11px;}"
            "QTableWidget::item:selected{background:#313244;}")
        self._comp_table.verticalHeader().setVisible(False)
        self._comp_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._comp_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._comp_table.setFixedHeight(160)
        hh = self._comp_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        gv.addWidget(self._comp_table)

        # ── Preparation steps ────────────────────────────────────────────
        def _cb(text, checked=False, tip=""):
            c = QCheckBox(text)
            c.setChecked(checked)
            if tip: c.setToolTip(tip)
            c.setStyleSheet(_CB_STYLE)
            return c

        def _rb(text):
            r = QRadioButton(text)
            r.setStyleSheet(_CB_STYLE)
            return r

        # Fix / Sanitize — whole PDB
        self._cb_fix = _cb("Fix / Sanitize PDB", True,
            "Round-trip through RDKit to repair atom names and format (all records)")
        gv.addWidget(self._cb_fix)

        # Add Hydrogens — Protein row
        self._cb_addh_protein   = _cb("Add H  Protein", False, "Add H to protein ATOM records")
        self._rb_addh_pro_all   = _rb("All H")
        self._rb_addh_pro_polar = _rb("Polar Only")
        self._rb_addh_pro_all.setChecked(True)
        self._rb_addh_pro_all.setEnabled(False)
        self._rb_addh_pro_polar.setEnabled(False)
        self._cb_addh_protein.toggled.connect(self._rb_addh_pro_all.setEnabled)
        self._cb_addh_protein.toggled.connect(self._rb_addh_pro_polar.setEnabled)
        row_pro_h = QWidget(); rph = QHBoxLayout(row_pro_h)
        rph.setContentsMargins(0,0,0,0); rph.setSpacing(6)
        rph.addWidget(self._cb_addh_protein)
        rph.addWidget(self._rb_addh_pro_all)
        rph.addWidget(self._rb_addh_pro_polar)
        rph.addStretch()
        gv.addWidget(row_pro_h)

        # Add Hydrogens — Ligand row
        self._cb_addh_ligand    = _cb("Add H  Ligand", False, "Add H to ligand HETATM records")
        self._rb_addh_lig_all   = _rb("All H")
        self._rb_addh_lig_polar = _rb("Polar Only")
        self._rb_addh_lig_all.setChecked(True)
        self._rb_addh_lig_all.setEnabled(False)
        self._rb_addh_lig_polar.setEnabled(False)
        self._cb_addh_ligand.toggled.connect(self._rb_addh_lig_all.setEnabled)
        self._cb_addh_ligand.toggled.connect(self._rb_addh_lig_polar.setEnabled)
        row_lig_h = QWidget(); rlh = QHBoxLayout(row_lig_h)
        rlh.setContentsMargins(0,0,0,0); rlh.setSpacing(6)
        rlh.addWidget(self._cb_addh_ligand)
        rlh.addWidget(self._rb_addh_lig_all)
        rlh.addWidget(self._rb_addh_lig_polar)
        rlh.addStretch()
        gv.addWidget(row_lig_h)

        # Gasteiger Charges
        self._cb_charge_protein = _cb("Gasteiger Charges  Protein", False,
            "Compute Gasteiger charges for protein ATOM records")
        self._cb_charge_ligand  = _cb("Gasteiger Charges  Ligand", False,
            "Compute Gasteiger charges for ligand HETATM records")
        gv.addWidget(self._cb_charge_protein)
        gv.addWidget(self._cb_charge_ligand)
        gv.addStretch()

        root.addWidget(grp)

        # ── Run + Save ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("⚗  Run Preparation")
        self._run_btn.setFixedHeight(34)
        self._run_btn.setStyleSheet(
            _BTN_STYLE.format(bg="#89b4fa", fg="#1e1e2e", hov="#b4d0fa"))
        self._run_btn.clicked.connect(self._on_run)

        self._save_btn = QPushButton("💾  Save")
        self._save_btn.setEnabled(False)
        self._save_btn.setFixedHeight(34)
        self._save_btn.setStyleSheet(
            "QPushButton{background:#313244;color:#cdd6f4;"
            "border:1px solid #45475a;border-radius:4px;font-size:12px;}"
            "QPushButton:hover{background:#45475a;}"
            "QPushButton:disabled{color:#6c7086;}")
        self._save_btn.clicked.connect(self._on_save)

        btn_row.addWidget(self._run_btn, 1)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

        self._prog = QProgressBar()
        self._prog.setRange(0, 0)
        self._prog.setVisible(False)
        self._prog.setFixedHeight(4)
        self._prog.setStyleSheet(
            "QProgressBar{border:none;background:#313244;border-radius:2px;}"
            "QProgressBar::chunk{background:#89b4fa;border-radius:2px;}")
        root.addWidget(self._prog)
        root.addStretch()

    # ------------------------------------------------------------------ #
    # Component table
    # ------------------------------------------------------------------ #

    def _load_components(self, path: str):
        """Parse PDB and populate molecular component table."""
        try:
            self._components = parse_pdb_components(path)
        except Exception:
            self._components = []
        self._comp_table.setRowCount(0)
        for row, comp in enumerate(self._components):
            self._comp_table.insertRow(row)
            for col, key in enumerate(('chain', 'resname', 'resseq', 'type',
                                        'n_residues', 'n_atoms')):
                item = QTableWidgetItem(str(comp.get(key, '')))
                item.setTextAlignment(Qt.AlignCenter)
                color = _TYPE_COLOR.get(comp['type'], '#cdd6f4')
                item.setForeground(QBrush(QColor(color)))
                self._comp_table.setItem(row, col, item)
            # Keep checkbox — default: keep everything
            keep_cb = QCheckBox()
            keep_cb.setChecked(True)
            self._comp_table.setCellWidget(row, 6, _centered_widget(keep_cb))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _collect_steps(self) -> tuple[list[str], list[str], list[dict]]:
        """Return (steps, keep_chains, kept_components).
        kept_components = all component dicts whose Keep checkbox is checked.
        filter_components step is always first when any component is present.
        """
        kept_components = []
        keep_chains     = []

        for row, comp in enumerate(self._components):
            cb   = _inner_widget(self._comp_table.cellWidget(row, 6))
            kept = cb.isChecked() if cb else True
            if kept:
                kept_components.append(comp)
                if comp['type'] == 'Protein' and comp['chain'] not in keep_chains:
                    keep_chains.append(comp['chain'])

        steps = []
        if self._components:           steps.append('filter_components')
        if self._cb_fix.isChecked():   steps.append('fix_pdb')
        if self._cb_addh_protein.isChecked():
            steps.append('add_h_polar_protein' if self._rb_addh_pro_polar.isChecked()
                         else 'add_h_protein')
        if self._cb_addh_ligand.isChecked():
            steps.append('add_h_polar_ligand' if self._rb_addh_lig_polar.isChecked()
                         else 'add_h_ligand')
        if self._cb_charge_protein.isChecked(): steps.append('add_charge_protein')
        if self._cb_charge_ligand.isChecked():  steps.append('add_charge_ligand')
        return steps, keep_chains, kept_components

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def _on_run(self):
        steps, keep_chains, kept_components = self._collect_steps()
        if not steps:
            QMessageBox.information(self, "No Steps",
                "Select at least one preparation step.")
            return
        self._run_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._prepared_str = ""

        self._thread = QThread()
        self._worker = _PrepWorker(self.current_file, 'receptor', steps,
                                   keep_chains, kept_components)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    @Slot(str, str)
    def _on_done(self, output_str: str, report: str):
        self._prepared_str = output_str
        self._prog.setVisible(False)
        self._run_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        # Preview prepared structure in viewer
        tmp = tempfile.NamedTemporaryFile(
            suffix='.pdb', delete=False, mode='w', encoding='utf-8')
        tmp.write(output_str)
        tmp.close()
        self.viewer_load.emit(tmp.name)

    @Slot(str)
    def _on_error(self, msg: str):
        self._prog.setVisible(False)
        self._run_btn.setEnabled(True)
        QMessageBox.warning(self, "Preparation Error", f"❌ ERROR:\n{msg}")

    def _on_save(self):
        if not self._prepared_str:
            return
        job_dir = self._job_dir
        if not job_dir or not os.path.isdir(job_dir):
            job_dir = QFileDialog.getExistingDirectory(
                self, "Select Job Directory")
        if not job_dir:
            return
        dest_dir = os.path.join(job_dir, "receptor_ready")
        os.makedirs(dest_dir, exist_ok=True)
        stem = Path(self.current_file).stem
        dest = os.path.join(dest_dir, f"{stem}_prepared.pdb")
        Path(dest).write_text(self._prepared_str, encoding='utf-8')
        self.saved.emit(dest)
        QMessageBox.information(self, "Saved", f"✔  Saved → {dest}")


# ═══════════════════════════════════════════════════════════════════════════ #
# Ligand Preparation Tab
# ═══════════════════════════════════════════════════════════════════════════ #

class LigandPrepTab(QWidget):
    """
    Preparation & validation tab for a single ligand file.
    Steps: validity check | Add H | neutralize | 3D conformer |
           MMFF optimize | Gasteiger charges | drug-likeness.
    """
    viewer_load = Signal(str)
    saved       = Signal(str)

    def __init__(self, file_path: str, job_dir: str = "", parent=None):
        super().__init__(parent)
        self.current_file  = file_path
        self._job_dir      = job_dir
        self._prepared_str = ""
        self._thread       = None
        self._worker       = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── Prep steps ──────────────────────────────────────────────────
        grp = QGroupBox("Ligand Preparation Steps")
        grp.setStyleSheet(_GRP_STYLE.format(title="#a6e3a1"))
        gv = QVBoxLayout(grp)
        gv.setSpacing(4)

        def _cb(text, checked=True, tip=""):
            c = QCheckBox(text)
            c.setChecked(checked)
            c.setToolTip(tip)
            c.setStyleSheet(_CB_STYLE)
            return c

        self._cb_addh      = _cb("Add Hydrogens",                   True,
            "Add explicit hydrogen atoms")
        self._cb_neutralize= _cb("Neutralize Charges",              False,
            "Remove formal charges (protonation state normalization)")
        self._cb_embed     = _cb("Generate 3D Conformer (ETKDGv3)", True,
            "Embed a 3D conformer if molecule has no coordinates")
        self._cb_opt       = _cb("Optimize Geometry (MMFF94)",      True,
            "Energy minimization with MMFF94 force field")
        self._cb_charges   = _cb("Add Gasteiger Charges",           True,
            "Compute Gasteiger partial charges")

        for w in (self._cb_addh, self._cb_neutralize, self._cb_embed,
                  self._cb_opt, self._cb_charges):
            gv.addWidget(w)
        root.addWidget(grp)

        # ── Run + Save ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("⚗  Run Preparation")
        self._run_btn.setFixedHeight(34)
        self._run_btn.setStyleSheet(
            _BTN_STYLE.format(bg="#a6e3a1", fg="#1e1e2e", hov="#c0f5b0"))
        self._run_btn.clicked.connect(self._on_run)

        self._save_btn = QPushButton("💾  Save")
        self._save_btn.setEnabled(False)
        self._save_btn.setFixedHeight(34)
        self._save_btn.setStyleSheet(
            "QPushButton{background:#313244;color:#cdd6f4;"
            "border:1px solid #45475a;border-radius:4px;font-size:12px;}"
            "QPushButton:hover{background:#45475a;}"
            "QPushButton:disabled{color:#6c7086;}")
        self._save_btn.clicked.connect(self._on_save)

        btn_row.addWidget(self._run_btn, 1)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

        self._prog = QProgressBar()
        self._prog.setRange(0, 0)
        self._prog.setVisible(False)
        self._prog.setFixedHeight(4)
        self._prog.setStyleSheet(
            "QProgressBar{border:none;background:#313244;border-radius:2px;}"
            "QProgressBar::chunk{background:#a6e3a1;border-radius:2px;}")
        root.addWidget(self._prog)
        root.addStretch()

    # ------------------------------------------------------------------ #
    # Run preparation
    # ------------------------------------------------------------------ #

    def _on_run(self):
        steps = []
        if self._cb_addh.isEnabled()     and self._cb_addh.isChecked():
            steps.append('add_h')
        if self._cb_embed.isChecked():    steps.append('embed_3d')
        if self._cb_opt.isChecked():      steps.append('optimize')
        if self._cb_charges.isChecked():  steps.append('add_charge')

        if not steps:
            QMessageBox.information(self, "No Steps",
                "Select at least one preparation step.")
            return

        self._run_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._prepared_str = ""

        self._thread = QThread()
        self._worker = _PrepWorker(self.current_file, 'ligand', steps, [])
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    @Slot(str, str)
    def _on_done(self, output_str: str, report: str):
        self._prepared_str = output_str
        self._prog.setVisible(False)
        self._run_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        tmp = tempfile.NamedTemporaryFile(
            suffix='.sdf', delete=False, mode='w', encoding='utf-8')
        tmp.write(output_str)
        tmp.close()
        self.viewer_load.emit(tmp.name)

    @Slot(str)
    def _on_error(self, msg: str):
        self._prog.setVisible(False)
        self._run_btn.setEnabled(True)
        QMessageBox.warning(self, "Preparation Error", f"❌ ERROR:\n{msg}")

    def _on_save(self):
        if not self._prepared_str:
            return
        job_dir = self._job_dir
        if not job_dir or not os.path.isdir(job_dir):
            job_dir = QFileDialog.getExistingDirectory(
                self, "Select Job Directory")
        if not job_dir:
            return
        dest_dir = os.path.join(job_dir, "ligand_ready")
        os.makedirs(dest_dir, exist_ok=True)
        stem = Path(self.current_file).stem
        dest = os.path.join(dest_dir, f"{stem}_prepared.sdf")
        Path(dest).write_text(self._prepared_str, encoding='utf-8')
        self.saved.emit(dest)
        QMessageBox.information(self, "Saved", f"✔  Saved → {dest}")




# ═══════════════════════════════════════════════════════════════════════════ #
# Main Panel
# ═══════════════════════════════════════════════════════════════════════════ #

_TAB_STYLE = """
    QTabWidget::pane {
        border: 1px solid #313244;
        background: #1e1e2e;
    }
    QTabBar::tab {
        background: #181825;
        color: #a6adc8;
        padding: 5px 12px;
        border: 1px solid #313244;
        border-bottom: none;
        border-radius: 3px 3px 0 0;
        min-width: 90px;
        font-size: 11px;
    }
    QTabBar::tab:selected {
        background: #1e1e2e;
        color: #cdd6f4;
        border-bottom: 2px solid #89b4fa;
    }
    QTabBar::tab:hover {
        background: #313244;
    }
"""

_SIDEBAR_BTN = (
    "QPushButton{background:#313244;color:#cdd6f4;"
    "border:1px solid #45475a;border-radius:3px;"
    "font-size:11px;padding:5px 4px;text-align:left;}"
    "QPushButton:hover{background:#45475a;}"
)


class _TreeShim(QObject):
    """
    Compatibility shim so DockingPrepPanel can call self._job_tree.set_path()
    and receive file_selected / dir_changed signals, while the actual widget
    is now a plain QTreeView (VS Code style, no PathPicker).
    """
    file_selected = Signal(str)
    dir_changed   = Signal(str)

    def __init__(self, tree_view: QTreeView, fs_model,
                 root_lbl: QLabel, panel: QWidget):
        super().__init__(panel)
        self._tree  = tree_view
        self._model = fs_model
        self._lbl   = root_lbl
        self._path  = ""

    def set_path(self, p: str):
        if not p or not os.path.isdir(p):
            return
        self._path = p
        idx = self._model.setRootPath(p)
        self._tree.setRootIndex(idx)
        self._tree.expandToDepth(0)
        name = os.path.basename(p) or p
        self._lbl.setText(f"  ▾ {name.upper()}")
        self.dir_changed.emit(p)

    def path(self) -> str:
        return self._path

    def _refresh(self):
        self.set_path(self._path)


class DockingPrepPanel(QWidget):
    """
    Main Docking Preparation Panel.

    Signals
    -------
    file_saved(str)  — absolute path of any file saved to receptor_ready/ or ligand_ready/
    """

    file_saved      = Signal(str)   # path saved to receptor_ready/ or ligand_ready/
    job_dir_changed = Signal(str)   # new job directory path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tabs_by_path: dict[str, QScrollArea] = {}
        self._job_dir = ""
        self._build_ui()
        # Watch receptor_ready/ and ligand_ready/ for changes → refresh tree
        self._ready_watcher = QFileSystemWatcher(self)
        self._ready_watcher.directoryChanged.connect(self._on_ready_dir_changed)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    _MOL_EXT = {'.pdb', '.pdbqt', '.mol', '.mol2', '.sdf'}

    def open_file(self, path: str):
        """
        Open a file from the Explorer.
        - Molecular files (.pdb/.pdbqt/.mol/.mol2/.sdf): open/switch prep tab
          AND update 3D Viewer + File Content in the right panel.
        - Any other file: update File Content only (no prep tab created).
        Double-click and right-click 'Open' both call this method.
        """
        if not os.path.isfile(path):
            return
        ext = os.path.splitext(path)[1].lower()

        # ── Non-molecular file: show content only, no tab ───────────────
        if ext not in self._MOL_EXT:
            self._show_file_content(path)
            return

        # ── Already open → switch to that tab ───────────────────────────
        if path in self._tabs_by_path:
            idx = self._tab_widget.indexOf(self._tabs_by_path[path])
            self._tab_widget.setCurrentIndex(idx)
            self._on_viewer_load(path)
            return

        # ── Create new prep tab ──────────────────────────────────────────
        fname      = os.path.basename(path)
        parent_dir = os.path.basename(os.path.dirname(path))

        if parent_dir in ('target_input', 'receptor_ready'):
            tab  = TargetPrepTab(path, self._job_dir)
            icon = "🧬"
        else:
            tab  = LigandPrepTab(path, self._job_dir)
            icon = "💊"
            QApplication.processEvents()
            tab._on_check()

        tab.viewer_load.connect(self._on_viewer_load)
        tab.saved.connect(self.file_saved)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(tab)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        short = f"{icon} {Path(fname).stem[:12]}"
        idx = self._tab_widget.addTab(scroll, short)
        self._tab_widget.setCurrentIndex(idx)
        self._tabs_by_path[path] = scroll

        # Update right panel
        self._on_viewer_load(path)

    def _show_file_content(self, path: str):
        """Show file content in the right panel without opening a prep tab."""
        try:
            content = Path(path).read_text(encoding='utf-8', errors='replace')
            self._file_text.setPlainText(content)
        except OSError:
            self._file_text.setPlainText("Cannot read file.")

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left sidebar: VS Code-style Explorer ──────────────────────
        left = QWidget()
        left.setFixedWidth(220)
        left.setStyleSheet("background:#181825;border-right:1px solid #313244;")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        # VS Code-style "EXPLORER" header
        hdr = QWidget()
        hdr.setFixedHeight(35)
        hdr.setStyleSheet("background:#181825;")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(12, 0, 6, 0)
        hdr_lay.setSpacing(4)
        exp_lbl = QLabel("EXPLORER")
        exp_lbl.setStyleSheet(
            "color:#bbbfca;font-size:10px;font-weight:bold;letter-spacing:1px;")
        hdr_lay.addWidget(exp_lbl)
        hdr_lay.addStretch()
        # refresh icon button (top-right, VS Code style)
        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedSize(22, 22)
        refresh_btn.setToolTip("Refresh")
        refresh_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#585b70;font-size:13px;}"
            "QPushButton:hover{color:#cdd6f4;}")
        refresh_btn.clicked.connect(lambda: self._job_tree._refresh())
        # open folder icon
        open_btn = QPushButton("📂")
        open_btn.setFixedSize(22, 22)
        open_btn.setToolTip("Open Job Directory")
        open_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;font-size:12px;}"
            "QPushButton:hover{background:#313244;border-radius:3px;}")
        open_btn.clicked.connect(self._browse_job_dir)
        hdr_lay.addWidget(open_btn)
        hdr_lay.addWidget(refresh_btn)
        lv.addWidget(hdr)

        # Job Dir name as collapsible section label (VS Code folder label)
        self._root_lbl = QLabel("  NO FOLDER OPENED")
        self._root_lbl.setStyleSheet(
            "background:#252537;color:#cdd6f4;font-size:10px;font-weight:bold;"
            "letter-spacing:0.5px;padding:4px 8px;border-top:1px solid #313244;"
            "border-bottom:1px solid #313244;")
        lv.addWidget(self._root_lbl)

        # Pure QTreeView — no PathPicker, no labels
        from PySide6.QtWidgets import QFileSystemModel as _FSM
        self._fs_model = _FSM()
        self._fs_model.setFilter(
            QDir.AllEntries | QDir.NoDotAndDotDot)
        self._fs_model.setReadOnly(True)

        self._job_tree_view = QTreeView()
        self._job_tree_view.setModel(self._fs_model)
        self._job_tree_view.setAnimated(True)
        self._job_tree_view.setIndentation(12)
        self._job_tree_view.setSortingEnabled(False)
        self._job_tree_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._job_tree_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._job_tree_view.setHeaderHidden(True)
        # Hide size/type/date columns — name only
        for col in range(1, 4):
            self._job_tree_view.setColumnHidden(col, True)
        self._job_tree_view.setStyleSheet("""
            QTreeView {
                background:#181825; color:#cdd6f4;
                border:none; font-size:12px;
            }
            QTreeView::item { padding:2px 0; }
            QTreeView::item:hover    { background:#2a2a3e; }
            QTreeView::item:selected { background:#094771; color:#ffffff; }
            QTreeView::branch {
                background:#181825;
            }
            QTreeView::branch:has-siblings:adjoins-item,
            QTreeView::branch:!has-children:!has-siblings:adjoins-item {
                border-image:none; image:none;
            }
        """)
        self._job_tree_view.doubleClicked.connect(self._on_tree_double_click)
        self._job_tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._job_tree_view.customContextMenuRequested.connect(self._on_tree_context_menu)
        lv.addWidget(self._job_tree_view, 1)

        # Keep a compatibility shim so rest of code using self._job_tree still works
        self._job_tree = _TreeShim(self._job_tree_view, self._fs_model,
                                   self._root_lbl, self)
        self._job_tree.file_selected.connect(self.open_file)
        self._job_tree.dir_changed.connect(self._on_dir_changed)

        # ── Buttons below tree ────────────────────────────────────────
        btn_bar = QWidget()
        btn_bar.setStyleSheet("background:#181825;border-top:1px solid #313244;")
        bb = QVBoxLayout(btn_bar)
        bb.setContentsMargins(6, 6, 6, 6)
        bb.setSpacing(4)

        gen_btn = QPushButton("📁  Generate Job Dir")
        gen_btn.setStyleSheet(_SIDEBAR_BTN)
        gen_btn.clicked.connect(self._on_gen_jobdir)
        bb.addWidget(gen_btn)

        imp_t = QPushButton("🧬  Import Targets")
        imp_t.setStyleSheet(_SIDEBAR_BTN)
        imp_t.clicked.connect(self._import_target)
        bb.addWidget(imp_t)

        imp_l = QPushButton("💊  Import Ligands")
        imp_l.setStyleSheet(_SIDEBAR_BTN)
        imp_l.clicked.connect(self._import_ligand)
        bb.addWidget(imp_l)

        lv.addWidget(btn_bar)
        root.addWidget(left)

        # ── Right: tab widget + shared viewer ────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet("QSplitter::handle{background:#313244;width:2px;}")

        # Tab widget
        self._tab_widget = QTabWidget()
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.setMovable(True)
        self._tab_widget.setStyleSheet(_TAB_STYLE)
        self._tab_widget.tabCloseRequested.connect(self._close_tab)

        # Welcome tab — must be set BEFORE connecting currentChanged
        welcome = QWidget()
        welcome_lay = QVBoxLayout(welcome)
        welcome_lay.setContentsMargins(24, 20, 24, 16)
        welcome_lay.setSpacing(12)

        # -- instruction block
        instr = QLabel(
            "<b style='color:#89b4fa;font-size:15px;'>LADOCK Desktop</b><br/><br/>"
            "<span style='color:#cdd6f4;'>📂 Double-click a file in the tree to open its preparation tab.</span><br/><br/>"
            "<span style='color:#a6e3a1;'>🧬 Files in <code>target_input/</code> → Target Preparation</span><br/>"
            "<span style='color:#89dceb;'>💊 Files in <code>ligand_input/</code> → Ligand Preparation</span><br/><br/>"
            "<span style='color:#585b70;'>After preparation, receptors are saved to <code>receptor_ready/</code> "
            "and ligands to <code>ligand_ready/</code>.</span>"
        )
        instr.setWordWrap(True)
        instr.setTextFormat(Qt.RichText)
        instr.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        welcome_lay.addWidget(instr)

        # -- divider
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("color:#313244;")
        welcome_lay.addWidget(div)

        # -- citation block (HTML cards, same style as About dialog)
        from app.about_dialog import _CITATION_HTML, _CITATION_PLAIN
        cite_box = QTextEdit()
        cite_box.setReadOnly(True)
        cite_box.setHtml(_CITATION_HTML)
        cite_box.setStyleSheet(
            "QTextEdit{background:#1e1e2e;border:1px solid #313244;border-radius:4px;}")
        welcome_lay.addWidget(cite_box, 1)

        from PySide6.QtCore import QTimer
        copy_btn = QPushButton("📋  Copy Citations")
        copy_btn.setFixedHeight(26)
        copy_btn.setFixedWidth(160)
        copy_btn.setStyleSheet(
            "QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
            "border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#45475a;}")
        def _copy():
            QApplication.clipboard().setText(_CITATION_PLAIN)
            copy_btn.setText("✅  Copied!")
            QTimer.singleShot(2000, lambda: copy_btn.setText("📋  Copy Citations"))
        copy_btn.clicked.connect(_copy)
        welcome_lay.addWidget(copy_btn, alignment=Qt.AlignLeft)

        self._tab_widget.addTab(welcome, "  Welcome  ")
        self._welcome_tab = welcome
        # Disable close button on welcome tab
        from PySide6.QtWidgets import QTabBar
        self._tab_widget.tabBar().setTabButton(
            0, QTabBar.ButtonPosition.RightSide, None)

        # Connect AFTER _welcome_tab is set
        self._tab_widget.currentChanged.connect(self._on_tab_changed)

        splitter.addWidget(self._tab_widget)

        # Shared right panel: 3D viewer (top) + file text (bottom)
        viewer_wrap = QWidget()
        viewer_wrap.setMinimumWidth(280)
        vw = QVBoxLayout(viewer_wrap)
        vw.setContentsMargins(0, 0, 0, 0)
        vw.setSpacing(0)

        # Vertical splitter: 3D on top, text on bottom
        vright = QSplitter(Qt.Vertical)
        vright.setStyleSheet("QSplitter::handle{background:#313244;height:4px;}")
        vright.setChildrenCollapsible(False)

        # ── Top: 3D Viewer ────────────────────────────────────────────
        viewer_box = QWidget()
        vblay = QVBoxLayout(viewer_box)
        vblay.setContentsMargins(0, 0, 0, 0)
        vblay.setSpacing(0)

        vhdr = QLabel("  🔬 3D Preview")
        vhdr.setFixedHeight(24)
        vhdr.setStyleSheet(
            "background:#181825;color:#89b4fa;font-size:11px;"
            "font-weight:bold;border-bottom:1px solid #313244;")
        vblay.addWidget(vhdr)

        self._viewer = None
        self._viewer_box_layout = vblay
        self._viewer_placeholder = QLabel(
            "3D preview loads on demand.\nIf WebEngine is unavailable, file content still works."
        )
        self._viewer_placeholder.setAlignment(Qt.AlignCenter)
        self._viewer_placeholder.setWordWrap(True)
        self._viewer_placeholder.setStyleSheet(
            "color:#6c7086;background:#11111b;border-top:1px solid #313244;"
            "padding:18px;font-size:11px;"
        )
        vblay.addWidget(self._viewer_placeholder)
        vright.addWidget(viewer_box)

        # ── Bottom: File text preview ─────────────────────────────────
        text_box = QWidget()
        tblay = QVBoxLayout(text_box)
        tblay.setContentsMargins(0, 0, 0, 0)
        tblay.setSpacing(0)

        thdr = QLabel("  📄 File Content")
        thdr.setFixedHeight(24)
        thdr.setStyleSheet(
            "background:#181825;color:#a6adc8;font-size:11px;"
            "font-weight:bold;border-bottom:1px solid #313244;")
        tblay.addWidget(thdr)

        self._file_text = QTextEdit()
        self._file_text.setReadOnly(True)
        self._file_text.setStyleSheet(
            "QTextEdit{background:#11111b;color:#a6adc8;"
            "font-family:monospace;font-size:10px;"
            "border:none;border-top:1px solid #313244;}")
        self._file_text.setPlaceholderText(
            "File content will appear here when a molecule is selected…")
        tblay.addWidget(self._file_text)
        vright.addWidget(text_box)

        vright.setSizes([320, 180])
        vw.addWidget(vright)
        splitter.addWidget(viewer_wrap)
        splitter.setSizes([520, 380])

        root.addWidget(splitter, 1)

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def set_job_dir(self, path: str):
        """Public API: set the job directory from outside (e.g. Open Project)."""
        if path and os.path.isdir(path):
            self._job_dir = path
            self._job_tree.set_path(path)
            self._update_ready_watcher(path)
            self.job_dir_changed.emit(path)

    def _browse_job_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Open Job Directory")
        if path:
            self.set_job_dir(path)

    def _on_tree_double_click(self, index):
        path = self._fs_model.filePath(index)
        if os.path.isfile(path):
            self.open_file(path)

    def _on_tree_context_menu(self, pos):
        index = self._job_tree_view.indexAt(pos)
        path  = self._fs_model.filePath(index) if index.isValid() else ""
        is_file = os.path.isfile(path)
        is_dir  = os.path.isdir(path)
        is_item = is_file or is_dir

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#1e1e2e;color:#cdd6f4;border:1px solid #45475a;"
            "font-size:12px;padding:4px 0;}"
            "QMenu::item{padding:5px 20px 5px 12px;}"
            "QMenu::item:selected{background:#313244;}"
            "QMenu::separator{height:1px;background:#313244;margin:3px 0;}")

        # ── Open ────────────────────────────────────────────────────────
        if is_file:
            ext = os.path.splitext(path)[1].lower()
            is_mol = ext in self._MOL_EXT

            if is_mol:
                act_open = menu.addAction("📂  Open in Prep Tab")
                act_open.triggered.connect(lambda: self.open_file(path))
                act_viewer = menu.addAction("🔬  Preview in 3D Viewer")
                act_viewer.triggered.connect(lambda: self._on_viewer_load(path))
            else:
                act_view = menu.addAction("📄  View File Content")
                act_view.triggered.connect(lambda: self._show_file_content(path))

            act_os = menu.addAction("🖥  Open with System App")
            act_os.triggered.connect(lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(path)))

        if is_dir:
            act_exp = menu.addAction("📁  Open Folder in File Manager")
            act_exp.triggered.connect(lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(path)))

            act_set = menu.addAction("🏠  Set as Job Directory")
            act_set.triggered.connect(lambda: self._set_job_dir(path))

        # ── Copy ────────────────────────────────────────────────────────
        if is_item:
            menu.addSeparator()
            act_copy_path = menu.addAction("📋  Copy Path")
            act_copy_path.triggered.connect(
                lambda: QApplication.clipboard().setText(path))

            act_copy_name = menu.addAction("📋  Copy Filename")
            act_copy_name.triggered.connect(
                lambda: QApplication.clipboard().setText(os.path.basename(path)))

        # ── Rename ──────────────────────────────────────────────────────
        if is_item:
            menu.addSeparator()
            act_rename = menu.addAction("✏  Rename…")
            act_rename.triggered.connect(lambda: self._tree_rename(path))

        # ── New folder ──────────────────────────────────────────────────
        base = path if is_dir else os.path.dirname(path)
        if not base:
            base = self._job_dir
        menu.addSeparator()
        act_mkdir = menu.addAction("🗂  New Folder…")
        act_mkdir.triggered.connect(lambda: self._tree_new_folder(base))

        # ── Delete ──────────────────────────────────────────────────────
        if is_item:
            menu.addSeparator()
            act_del = menu.addAction("🗑  Delete")
            act_del.triggered.connect(lambda: self._tree_delete(path))

        menu.exec(self._job_tree_view.viewport().mapToGlobal(pos))

    # ── Explorer helper actions ──────────────────────────────────────────────

    def _set_job_dir(self, path: str):
        self.set_job_dir(path)

    def _tree_rename(self, path: str):
        old_name = os.path.basename(path)
        new_name, ok = QInputDialog.getText(
            self, "Rename", "New name:", text=old_name)
        if not ok or not new_name.strip() or new_name == old_name:
            return
        new_path = os.path.join(os.path.dirname(path), new_name.strip())
        try:
            os.rename(path, new_path)
            self._job_tree._refresh()
        except OSError as e:
            QMessageBox.warning(self, "Rename failed", str(e))

    def _tree_new_folder(self, parent: str):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        new_dir = os.path.join(parent, name.strip())
        try:
            os.makedirs(new_dir, exist_ok=True)
            self._job_tree._refresh()
        except OSError as e:
            QMessageBox.warning(self, "Create folder failed", str(e))

    def _tree_delete(self, path: str):
        kind = "folder" if os.path.isdir(path) else "file"
        ret = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete {kind}:\n{path}\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            self._job_tree._refresh()
        except OSError as e:
            QMessageBox.warning(self, "Delete failed", str(e))

    def _on_dir_changed(self, path: str):
        self._job_dir = path
        for scroll in self._tabs_by_path.values():
            tab = scroll.widget()
            if hasattr(tab, '_job_dir'):
                tab._job_dir = path
        self._update_ready_watcher(path)

    def _update_ready_watcher(self, job_dir: str):
        """Register receptor_ready/ and ligand_ready/ with the file watcher."""
        if self._ready_watcher.directories():
            self._ready_watcher.removePaths(self._ready_watcher.directories())
        for sub in ("receptor_ready", "ligand_ready"):
            d = os.path.join(job_dir, sub)
            os.makedirs(d, exist_ok=True)
            self._ready_watcher.addPath(d)

    def _on_ready_dir_changed(self, _path: str):
        """Called when receptor_ready/ or ligand_ready/ contents change."""
        self._job_tree._refresh()

    def _on_tab_changed(self, idx: int):
        scroll = self._tab_widget.widget(idx)
        if scroll is None or scroll is self._welcome_tab:
            self._clear_right_panel()
            return
        tab = scroll.widget() if isinstance(scroll, QScrollArea) else scroll
        if hasattr(tab, 'current_file') and tab.current_file:
            self._on_viewer_load(tab.current_file)
        else:
            self._clear_right_panel()

    def _clear_right_panel(self):
        """Clear 3D viewer and file content when no file tab is active."""
        if self._viewer is not None:
            self._viewer.clear()
        self._file_text.setPlainText("")

    def _ensure_viewer(self) -> bool:
        if self._viewer is not None:
            return True

        try:
            from gui.viewer.molecular_viewer import MolecularViewerPanel

            self._viewer = MolecularViewerPanel()
            if self._viewer_placeholder is not None:
                self._viewer_box_layout.removeWidget(self._viewer_placeholder)
                self._viewer_placeholder.deleteLater()
                self._viewer_placeholder = None
            self._viewer_box_layout.addWidget(self._viewer)
            return True
        except Exception as exc:
            if self._viewer_placeholder is not None:
                self._viewer_placeholder.setText(
                    "3D preview is unavailable in this graphics session.\n"
                    f"{exc}"
                )
            return False

    def _on_viewer_load(self, path: str):
        if not path or not os.path.isfile(path):
            return
        parent = os.path.basename(os.path.dirname(path))
        if self._ensure_viewer():
            if parent in ('target_input', 'receptor_ready'):
                self._viewer.load_receptor(path)
            else:
                self._viewer.load_ligand(path)
        # Load full file content
        try:
            content = Path(path).read_text(encoding='utf-8', errors='replace')
            self._file_text.setPlainText(content)
        except OSError:
            self._file_text.setPlainText("Cannot read file.")

    def _close_tab(self, idx: int):
        scroll = self._tab_widget.widget(idx)
        if scroll is self._welcome_tab:
            return
        for path, t in list(self._tabs_by_path.items()):
            if t is scroll:
                del self._tabs_by_path[path]
                break
        self._tab_widget.removeTab(idx)
        # If no file tabs remain (only Welcome or nothing), clear right panel
        has_file_tab = any(
            self._tab_widget.widget(i) is not self._welcome_tab
            for i in range(self._tab_widget.count())
        )
        if not has_file_tab:
            self._clear_right_panel()

    def _on_gen_jobdir(self):
        job_dir = create_legacy_job_directory()
        self.set_job_dir(job_dir)

    def _import_target(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Target PDB Files", "",
            "PDB Files (*.pdb *.pdbqt);;All Files (*)")
        if files:
            self._import_files(files, "target_input")

    def _import_ligand(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Ligand Files", "",
            "Ligand Files (*.pdb *.pdbqt *.mol2 *.sdf *.mol);;All Files (*)")
        if files:
            self._import_files(files, "ligand_input")

    def _import_files(self, files: list[str], subdir: str):
        if not self._job_dir or not os.path.isdir(self._job_dir):
            QMessageBox.warning(self, "No Job Directory",
                "Please generate or select a job directory first.")
            return
        dest = os.path.join(self._job_dir, subdir)
        os.makedirs(dest, exist_ok=True)
        copied = 0
        for src in files:
            if not os.path.isfile(src):
                continue
            dst = os.path.join(dest, os.path.basename(src))
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                copied += 1
        self._job_tree._refresh()
        QMessageBox.information(self, "Import Done",
            f"{copied} file(s) copied to {subdir}/")
