"""
LADOCK — Docking Setup Panel (PySide6)
Port of ladockgui.py → PySide6, GUI-agnostic output via signal.

Input structure:
  1. Job Directory  (root dir, explorer tree)
  2. Target Dir     (dir berisi file PDB protein+ligand alami, explorer tree)
  3. Ligand Dir     (dir berisi file ligand berbagai format, explorer tree)
  4. Score Function
  5. Receptor Mode
  6. Docking Settings
  7. Installation Paths
"""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QCheckBox, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox,
    QPushButton, QGroupBox, QScrollArea, QSizePolicy, QFrame,
    QTextEdit, QProgressBar, QSplitter, QTreeView, QHeaderView,
    QTabWidget, QAbstractItemView, QFileSystemModel
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, QDir, QSortFilterProxyModel
from PySide6.QtGui import QFont, QColor

from gui.widgets.common import PathPicker, SectionLabel, HDivider


# ---------------------------------------------------------------------------
# Directory Tree Explorer Widget
# ---------------------------------------------------------------------------

class DirTreeExplorer(QWidget):
    """
    PathPicker + live QTreeView explorer for a directory.
    Shows root dir and all subdirectories/files.
    Double-click a file → emits file_selected(path).
    """

    file_selected = Signal(str)     # absolute path of double-clicked file
    dir_changed   = Signal(str)     # emitted when path changes

    # Extensions shown with distinct icons (cosmetic only)
    _PDB_EXTS   = {".pdb", ".pdbqt"}
    _LIGAND_EXTS = {".sdf", ".mol2", ".mol", ".smi", ".csv", ".pdbqt", ".gz"}

    def __init__(self, label: str = "Directory", mode: str = "dir",
                 placeholder: str = "Select folder…", parent=None):
        super().__init__(parent)
        self._model = QFileSystemModel()
        self._model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden)
        self._build_ui(label, mode, placeholder)

    def _build_ui(self, label: str, mode: str, placeholder: str):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        # Header row: label + path picker + refresh
        hdr = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFont(QFont("Sans", 9, QFont.Bold))
        lbl.setStyleSheet("color:#89b4fa;")
        hdr.addWidget(lbl)

        self._picker = PathPicker(placeholder=placeholder, mode=mode)
        self._picker.path_changed.connect(self._on_path_changed)
        hdr.addWidget(self._picker, stretch=1)

        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedSize(26, 26)
        refresh_btn.setToolTip("Refresh tree")
        refresh_btn.setStyleSheet(
            "QPushButton{background:#313244;border:1px solid #45475a;"
            "border-radius:4px;color:#cdd6f4;font-size:14px;}"
            "QPushButton:hover{background:#45475a;}"
        )
        refresh_btn.clicked.connect(self._refresh)
        hdr.addWidget(refresh_btn)
        lay.addLayout(hdr)

        # Tree view
        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._tree.setMinimumHeight(130)
        self._tree.setMaximumHeight(200)
        self._tree.setAnimated(True)
        self._tree.setIndentation(14)
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(0, Qt.AscendingOrder)
        self._tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setStyleSheet("""
            QTreeView {
                background:#181825; color:#cdd6f4;
                border:1px solid #313244; border-radius:4px;
                font-size:11px;
            }
            QTreeView::item:hover    { background:#313244; }
            QTreeView::item:selected { background:#45475a; color:#89b4fa; }
            QTreeView::branch { background:#181825; }
        """)
        # Only show name column + size
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 4):
            self._tree.setColumnHidden(col, col != 1)   # show Size column only
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tree.header().setStyleSheet(
            "QHeaderView::section{background:#1e1e2e;color:#585b70;"
            "font-size:10px;border:none;padding:2px;}"
        )
        self._tree.doubleClicked.connect(self._on_double_click)
        lay.addWidget(self._tree)

        # File count label
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color:#585b70; font-size:10px;")
        lay.addWidget(self._count_label)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def path(self) -> str:
        return self._picker.text().strip()

    def set_path(self, p: str):
        self._picker.setText(p)
        self._on_path_changed(p)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _on_path_changed(self, path: str):
        path = path.strip()
        if path and os.path.isdir(path):
            root_index = self._model.setRootPath(path)
            self._tree.setRootIndex(root_index)
            self._tree.expandToDepth(0)
            self._update_count(path)
            self.dir_changed.emit(path)
        elif path and os.path.isfile(path):
            # If user picks a file, show its parent dir
            parent = os.path.dirname(path)
            root_index = self._model.setRootPath(parent)
            self._tree.setRootIndex(root_index)
            self._update_count(parent)
            self.dir_changed.emit(parent)

    def _refresh(self):
        self._on_path_changed(self._picker.text())

    def _on_double_click(self, index):
        path = self._model.filePath(index)
        if os.path.isfile(path):
            self.file_selected.emit(path)

    def _update_count(self, folder: str):
        try:
            entries = os.listdir(folder)
            n_files = sum(1 for e in entries if os.path.isfile(os.path.join(folder, e)))
            n_dirs  = sum(1 for e in entries if os.path.isdir(os.path.join(folder, e)))
            self._count_label.setText(
                f"{n_files} file(s)  ·  {n_dirs} subfolder(s)"
            )
        except OSError:
            self._count_label.setText("")


# ---------------------------------------------------------------------------
# Worker thread — runs docking without blocking GUI
# ---------------------------------------------------------------------------

class DockingWorker(QObject):
    log_signal = Signal(str)
    finished   = Signal()
    error      = Signal(str)

    def __init__(self, parameters: dict):
        super().__init__()
        self.parameters = parameters

    def run(self):
        try:
            from engine.docking_engine import run_docking
            run_docking(log_callback=self.log_signal.emit, **self.parameters)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


# ---------------------------------------------------------------------------
# Docking Setup Panel
# ---------------------------------------------------------------------------

class DockingSetupPanel(QWidget):
    """
    Full docking configuration panel.
    Emits `run_requested(parameters: dict)` when the user clicks RUN.

    Layout (top → bottom in left column):
      1. Job Directory   ← paling atas
      2. Target Dir      ← berisi PDB protein+ligand alami
      3. Ligand Dir      ← berisi file ligand (SDF/SMILES/PDBQT/CSV)
      4. Score Function
      5. Receptor Mode
      6. Simultaneous Docking
      7. Docking Settings
      8. Additional Settings
      9. Installation Paths
    """

    run_requested        = Signal(dict)
    log_message          = Signal(str)
    file_preview_requested = Signal(str)   # absolute path of clicked file in tree

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread  = None
        self._worker  = None
        self._build_ui()
        self._job_tree.file_selected.connect(self.file_preview_requested)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        # ---- LEFT COLUMN (settings) ----
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(420)
        left_inner = QWidget()
        left_layout = QVBoxLayout(left_inner)
        left_layout.setSpacing(6)
        left_scroll.setWidget(left_inner)

        # ════════════════════════════════════════════════════════════════
        # 1. JOB DIRECTORY  — tree + 3 action buttons
        # ════════════════════════════════════════════════════════════════
        left_layout.addWidget(SectionLabel("Job Directory"))

        # Job root dir explorer (the only tree view)
        self._job_tree = DirTreeExplorer(
            label="Job Dir:",
            mode="dir",
            placeholder="Root directory for this docking job…"
        )
        default_jobdir = os.path.join(os.getcwd(), "dock")
        if os.path.exists(default_jobdir):
            self._job_tree.set_path(default_jobdir)
        left_layout.addWidget(self._job_tree)

        # 3 action buttons
        btn_row = QHBoxLayout()
        gen_btn = QPushButton("📁 Generate Job Dir")
        imp_target_btn = QPushButton("🧬 Import Targets")
        imp_ligand_btn = QPushButton("💊 Import Ligands")
        for btn in (gen_btn, imp_target_btn, imp_ligand_btn):
            btn.setFixedHeight(28)
            btn_row.addWidget(btn)
        gen_btn.clicked.connect(self._on_gen_jobdir)
        imp_target_btn.clicked.connect(self._import_target)
        imp_ligand_btn.clicked.connect(self._import_ligand)
        left_layout.addLayout(btn_row)

        left_layout.addWidget(HDivider())

        # ════════════════════════════════════════════════════════════════
        # 4. SCORE FUNCTION
        # ════════════════════════════════════════════════════════════════
        left_layout.addWidget(SectionLabel("Score Function"))
        self._sf_checks = {}
        sf_group = QGroupBox()
        sf_grid = QGridLayout(sf_group)
        sf_grid.setSpacing(4)
        sf_options = ["Vina", "Vinardo", "AD4", "Vina GPU", "Vinardo GPU", "AD4 GPU"]
        default_sf = {"Vina", "Vinardo", "AD4"}
        for i, name in enumerate(sf_options):
            cb = QCheckBox(name)
            cb.setChecked(name in default_sf)
            self._sf_checks[name] = cb
            sf_grid.addWidget(cb, i // 3, i % 3)
        left_layout.addWidget(sf_group)

        # ════════════════════════════════════════════════════════════════
        # 5. RECEPTOR MODE
        # ════════════════════════════════════════════════════════════════
        left_layout.addWidget(SectionLabel("Receptor Mode"))
        mode_box = QGroupBox()
        mode_layout = QHBoxLayout(mode_box)
        self._mode_checks = {}
        for name, default in [("Rigid", True), ("Flexible", False)]:
            cb = QCheckBox(name)
            cb.setChecked(default)
            self._mode_checks[name] = cb
            mode_layout.addWidget(cb)
        self._mode_checks["Flexible"].stateChanged.connect(self._on_flexible_changed)
        left_layout.addWidget(mode_box)

        flex_row = QHBoxLayout()
        flex_row.addWidget(QLabel("Flexible residue distance (Å):"))
        self._distance_spin = QDoubleSpinBox()
        self._distance_spin.setValue(4.0)
        self._distance_spin.setRange(1.0, 20.0)
        self._distance_spin.setSingleStep(0.5)
        self._distance_spin.setEnabled(False)
        flex_row.addWidget(self._distance_spin)
        left_layout.addLayout(flex_row)

        # ════════════════════════════════════════════════════════════════
        # 6. SIMULTANEOUS DOCKING
        # ════════════════════════════════════════════════════════════════
        left_layout.addWidget(SectionLabel("Simultaneous Ligand Docking"))
        simul_box = QGroupBox()
        simul_layout = QGridLayout(simul_box)
        simul_layout.addWidget(QLabel("Number of Ligand(s):"), 0, 0)
        self._n_ligands_edit = QLineEdit("1")
        simul_layout.addWidget(self._n_ligands_edit, 0, 1)
        simul_layout.addWidget(QLabel("Arrangement Type:"), 1, 0)
        self._arrangement_combo = QComboBox()
        self._arrangement_combo.addItems(["combination", "permutation"])
        simul_layout.addWidget(self._arrangement_combo, 1, 1)
        left_layout.addWidget(simul_box)

        # ════════════════════════════════════════════════════════════════
        # 7. DOCKING SETTINGS
        # ════════════════════════════════════════════════════════════════
        left_layout.addWidget(SectionLabel("Docking Settings"))
        dock_box = QGroupBox()
        dock_layout = QGridLayout(dock_box)
        self._box_size_edit    = QLineEdit("40,40,40")
        self._spacing_spin     = QDoubleSpinBox()
        self._spacing_spin.setValue(0.375); self._spacing_spin.setRange(0.1, 1.0); self._spacing_spin.setSingleStep(0.025)
        self._n_poses_spin     = QSpinBox(); self._n_poses_spin.setValue(10); self._n_poses_spin.setRange(1, 100)
        self._exhaustiveness_spin = QSpinBox(); self._exhaustiveness_spin.setValue(8); self._exhaustiveness_spin.setRange(1, 128)
        self._cpu_spin         = QSpinBox(); self._cpu_spin.setValue(4); self._cpu_spin.setRange(1, os.cpu_count() or 8)

        for row, (label, widget) in enumerate([
            ("Box Size (x,y,z):", self._box_size_edit),
            ("Spacing:",          self._spacing_spin),
            ("N Poses:",          self._n_poses_spin),
            ("Exhaustiveness:",   self._exhaustiveness_spin),
            ("CPU Cores:",        self._cpu_spin),
        ]):
            dock_layout.addWidget(QLabel(label), row, 0)
            dock_layout.addWidget(widget, row, 1)
        left_layout.addWidget(dock_box)

        # ════════════════════════════════════════════════════════════════
        # 8. ADDITIONAL SETTINGS
        # ════════════════════════════════════════════════════════════════
        left_layout.addWidget(SectionLabel("Additional Settings"))
        add_box = QGroupBox()
        add_layout = QGridLayout(add_box)
        self._parallel_combo    = QComboBox(); self._parallel_combo.addItems(["false", "true"])
        self._save_input_combo  = QComboBox(); self._save_input_combo.addItems(["false", "true"])
        self._save_output_combo = QComboBox(); self._save_output_combo.addItems(["false", "true"])
        for row, (label, widget) in enumerate([
            ("Parallel Simulation:", self._parallel_combo),
            ("Save Input Files:",    self._save_input_combo),
            ("Save Output Files:",   self._save_output_combo),
        ]):
            add_layout.addWidget(QLabel(label), row, 0)
            add_layout.addWidget(widget, row, 1)
        left_layout.addWidget(add_box)

        # ════════════════════════════════════════════════════════════════
        # 9. INSTALLATION PATHS
        # ════════════════════════════════════════════════════════════════
        left_layout.addWidget(SectionLabel("Installation Paths"))
        path_box = QGroupBox()
        path_layout = QGridLayout(path_box)
        self._paths = {}
        path_defaults = {
            "AutoDock4":    ("file", "autodock4"),
            "AutoGrid4":    ("file", "autogrid4"),
            "Vina":         ("file", "vina"),
            "ADFR (bin/)":  ("dir",  os.path.expanduser("~/ADFRsuite-1.0/bin")),
            "MGLTools":     ("dir",  os.path.expanduser("~/MGLTools-1.5.6")),
            "AutoDock GPU": ("file", ""),
            "Vina GPU":     ("file", ""),
        }
        for row, (label, (mode, default)) in enumerate(path_defaults.items()):
            path_layout.addWidget(QLabel(label + ":"), row, 0)
            picker = PathPicker(mode=mode)
            picker.setText(default)
            self._paths[label] = picker
            path_layout.addWidget(picker, row, 1)
        left_layout.addWidget(path_box)

        left_layout.addStretch()

        # ---- RIGHT COLUMN (log + controls) ----
        right_layout = QVBoxLayout()
        right_layout.setSpacing(8)

        # Quick summary of selected dirs
        self._dir_summary = QLabel("No directories selected.")
        self._dir_summary.setStyleSheet(
            "background:#181825; color:#585b70; font-size:11px;"
            "border:1px solid #313244; border-radius:4px; padding:4px 8px;"
        )
        self._dir_summary.setWordWrap(True)
        right_layout.addWidget(self._dir_summary)
        self._job_tree.dir_changed.connect(self._update_dir_summary)

        # Log console
        self._log_console = QTextEdit()
        self._log_console.setReadOnly(True)
        self._log_console.setFont(QFont("Monospace", 10))
        self._log_console.setStyleSheet(
            "background:#1e1e2e; color:#cdd6f4; border-radius:6px;"
        )
        right_layout.addWidget(self._log_console, stretch=1)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)   # indeterminate
        self._progress_bar.setVisible(False)
        right_layout.addWidget(self._progress_bar)

        # Button row
        btn_row = QHBoxLayout()
        self._run_btn    = QPushButton("▶  RUN")
        self._run_btn.setFixedHeight(36)
        self._run_btn.setStyleSheet(
            "QPushButton{background:#4CAF50;color:white;font-weight:bold;border-radius:4px;}"
            "QPushButton:hover{background:#43A047;}"
            "QPushButton:disabled{background:#555;}"
        )
        self._cancel_btn = QPushButton("✕  Cancel")
        self._cancel_btn.setFixedHeight(36)
        self._cancel_btn.setEnabled(False)
        self._gen_btn    = QPushButton("📁 Generate Job Dir")
        self._gen_btn.setFixedHeight(36)

        self._run_btn.clicked.connect(self._on_run)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._gen_btn.clicked.connect(self._on_gen_jobdir)

        btn_row.addWidget(self._run_btn)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._gen_btn)
        right_layout.addLayout(btn_row)

        # Assemble
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_scroll)
        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def _on_flexible_changed(self, state):
        self._distance_spin.setEnabled(state == Qt.Checked)

    def _on_gen_jobdir(self):
        from data.project import create_legacy_job_directory
        job_dir = create_legacy_job_directory()
        self._job_tree.set_path(job_dir)
        self._append_log(
            f"Job directory created: {job_dir}\n"
            f"  ├── target_input/\n"
            f"  ├── ligand_input/\n"
            f"  └── output/"
        )

    def _import_target(self):
        """Open file dialog → copy selected PDB files to target_input/."""
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Target Files (PDB)", "",
            "PDB Files (*.pdb *.pdbqt);;All Files (*)"
        )
        if files:
            self._import_files(files, "target_input", "target")

    def _import_ligand(self):
        """Open file dialog → copy selected ligand files to ligand_input/."""
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Ligand Files", "",
            "Ligand Files (*.pdb *.pdbqt *.mol2 *.sdf *.mol);;All Files (*)"
        )
        if files:
            self._import_files(files, "ligand_input", "ligand")

    def _import_files(self, files: list, subdir: str, label: str):
        import shutil
        job_dir = self._job_tree.path()

        if not job_dir or not os.path.isdir(job_dir):
            self._append_log("[WARNING] Job directory not set. Generate or select it first.")
            return

        dest = os.path.join(job_dir, subdir)
        os.makedirs(dest, exist_ok=True)

        copied = 0
        skipped = 0
        for src_file in files:
            if not os.path.isfile(src_file):
                continue
            fname = os.path.basename(src_file)
            dst_file = os.path.join(dest, fname)
            if os.path.exists(dst_file):
                skipped += 1
                continue
            shutil.copy2(src_file, dst_file)
            copied += 1

        self._append_log(
            f"Import {label}: {copied} file(s) copied → {dest}"
            + (f"  ({skipped} skipped, already exist)" if skipped else "")
        )
        # Refresh job dir tree to show new files
        self._job_tree._refresh()

    def _update_dir_summary(self, _=""):
        job = self._job_tree.path()
        if not job:
            self._dir_summary.setText("No job directory selected.")
            return

        def _count(d):
            try:
                return len(os.listdir(d)) if os.path.isdir(d) else 0
            except OSError:
                return 0

        t_n = _count(os.path.join(job, "target_input"))
        l_n = _count(os.path.join(job, "ligand_input"))
        o_n = _count(os.path.join(job, "output"))
        self._dir_summary.setText(
            f"📁 {os.path.basename(job)}\n"
            f"   🧬 target_input: {t_n} file(s)   "
            f"💊 ligand_input: {l_n} file(s)   "
            f"📄 output: {o_n} file(s)"
        )

    def _on_run(self):
        params = self._collect_parameters()
        if params is None:
            return

        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress_bar.setVisible(True)
        self._log_console.clear()
        self._append_log("Starting docking pipeline…\n")

        self._worker = DockingWorker(params)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log_signal.connect(self._append_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()
        self.run_requested.emit(params)

    def _on_cancel(self):
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
        self._on_finished()

    def _on_finished(self):
        self._run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._append_log("\n— Done —")

    def _on_error(self, msg: str):
        self._append_log(f"\n[ERROR] {msg}")
        self._on_finished()

    def _append_log(self, msg: str):
        self._log_console.append(msg)
        self._log_console.verticalScrollBar().setValue(
            self._log_console.verticalScrollBar().maximum()
        )
        self.log_message.emit(msg)

    # ------------------------------------------------------------------ #
    # Parameter collection
    # ------------------------------------------------------------------ #

    def _collect_parameters(self) -> dict | None:
        sf_types = [
            k.lower().replace(" ", "")
            for k, cb in self._sf_checks.items() if cb.isChecked()
        ]
        listmode = [
            k.lower()
            for k, cb in self._mode_checks.items() if cb.isChecked()
        ]
        if not sf_types:
            self._append_log("[WARNING] No scoring function selected.")
            return None
        if not listmode:
            self._append_log("[WARNING] No receptor mode selected.")
            return None

        job_directory = self._job_tree.path()
        if not job_directory:
            self._append_log("[WARNING] Job directory is empty.")
            return None

        # Use the imported subdirs inside job_dir as the actual docking inputs
        target_dir = os.path.join(job_directory, "target_input")
        ligand_dir = os.path.join(job_directory, "ligand_input")

        if not os.path.isdir(target_dir) or not os.listdir(target_dir):
            self._append_log(
                "[WARNING] target_input/ is empty. "
                "Browse Target Dir and click 📥 Import first."
            )
            return None
        if not os.path.isdir(ligand_dir) or not os.listdir(ligand_dir):
            self._append_log(
                "[WARNING] ligand_input/ is empty. "
                "Browse Ligand Dir and click 📥 Import first."
            )
            return None

        mgl_path = self._paths["MGLTools"].text().strip()
        mgl_utils = os.path.join(mgl_path, "MGLToolsPckgs/AutoDockTools/Utilities24")
        adfr_bin  = self._paths["ADFR (bin/)"].text().strip()

        from core.task_manager import TaskManager
        params = TaskManager.build_parameters(
            sf_types=sf_types,
            listmode=listmode,
            distance=self._distance_spin.value(),
            arrangement_type=self._arrangement_combo.currentText(),
            elements=self._n_ligands_edit.text().strip().split(","),
            box_size=self._box_size_edit.text().strip(),
            spacing=self._spacing_spin.value(),
            n_poses=self._n_poses_spin.value(),
            exhaustiveness=self._exhaustiveness_spin.value(),
            cpu=self._cpu_spin.value(),
            parallel_simulation=self._parallel_combo.currentText(),
            input_file_saved=self._save_input_combo.currentText(),
            output_file_saved=self._save_output_combo.currentText(),
            vina_path=self._paths["Vina"].text().strip(),
            ad4_path=self._paths["AutoDock4"].text().strip(),
            ag4_path=self._paths["AutoGrid4"].text().strip(),
            autodockgpu=self._paths["AutoDock GPU"].text().strip(),
            vinagpu=self._paths["Vina GPU"].text().strip(),
            job_directory=job_directory,
            agfr=os.path.join(adfr_bin, "agfr"),
            adfr=os.path.join(adfr_bin, "adfr"),
            prepare_ligand=os.path.join(mgl_utils, "prepare_ligand4.py"),
            prepare_receptor=os.path.join(mgl_utils, "prepare_receptor4.py"),
            prepare_gpf=os.path.join(mgl_utils, "prepare_gpf4.py"),
            prepare_flexreceptor=os.path.join(mgl_utils, "prepare_flexreceptor4.py"),
        )
        # Inject directory-based inputs
        params["target_dir"] = target_dir
        params["ligand_dir"] = ligand_dir
        return params

