"""
LADOCK — Batch Docking Panel (gui/panels/batch_panel.py)

Two-column layout:
  Left  — Receptor + docking settings (reuses TaskManager.build_parameters)
  Right — Ligand library summary + per-ligand progress table

Signals
-------
batch_submitted(list[str])  — list of job_ids submitted to scheduler
"""

from __future__ import annotations

import os
from typing import Optional, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter, QProgressBar,
    QFileDialog, QMessageBox, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from gui.widgets.common import SectionLabel, PathPicker
from data.ligand_library import LigandLibrary, LigandEntry
from core.job_scheduler import JobScheduler, DockingJob, JobStatus
from core.task_manager  import TaskManager


# ---------------------------------------------------------------------------
# Batch Panel
# ---------------------------------------------------------------------------

class BatchPanel(QWidget):
    """
    Batch docking: one receptor × N ligands.
    Each ligand becomes a separate DockingJob in the scheduler.
    """

    batch_submitted = Signal(list)   # list[str] job_ids

    def __init__(self, scheduler: JobScheduler, parent=None):
        super().__init__(parent)
        self._scheduler = scheduler
        self._library: Optional[LigandLibrary] = None
        self._job_rows: dict[str, int] = {}   # job_id → table row
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Header
        hdr = QHBoxLayout()
        hdr.addWidget(SectionLabel("🚀  Batch Docking"))
        hdr.addStretch()
        self._run_btn = QPushButton("▶▶ Run Batch")
        self._run_btn.setFixedHeight(32)
        self._run_btn.setEnabled(False)
        self._run_btn.setStyleSheet(
            "QPushButton{background:#1a2e3e;border:1px solid #89b4fa;"
            "border-radius:4px;color:#89b4fa;padding:4px 16px;font-weight:bold;}"
            "QPushButton:hover{background:#223040;}"
            "QPushButton:disabled{color:#585b70;border-color:#45475a;}"
        )
        self._run_btn.clicked.connect(self._run_batch)
        hdr.addWidget(self._run_btn)
        root.addLayout(hdr)

        # Main splitter: left settings | right job table
        splitter = QSplitter(Qt.Horizontal)

        # ── Left: settings ──────────────────────────────────────────────
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFrameShape(QFrame.NoFrame)
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 4, 0)

        # Receptor
        rec_grp = QGroupBox("Receptor")
        rec_lay = QFormLayout(rec_grp)
        self._receptor_pick = PathPicker("Select receptor PDBQT/PDB…")
        rec_lay.addRow("Receptor:", self._receptor_pick)
        left_lay.addWidget(rec_grp)

        # Grid box
        grid_grp = QGroupBox("Grid Box")
        grid_lay = QFormLayout(grid_grp)

        self._cx = QDoubleSpinBox(); self._cx.setRange(-999,999); self._cx.setDecimals(3)
        self._cy = QDoubleSpinBox(); self._cy.setRange(-999,999); self._cy.setDecimals(3)
        self._cz = QDoubleSpinBox(); self._cz.setRange(-999,999); self._cz.setDecimals(3)
        self._sx = QDoubleSpinBox(); self._sx.setRange(1,100); self._sx.setValue(20); self._sx.setDecimals(1)
        self._sy = QDoubleSpinBox(); self._sy.setRange(1,100); self._sy.setValue(20); self._sy.setDecimals(1)
        self._sz = QDoubleSpinBox(); self._sz.setRange(1,100); self._sz.setValue(20); self._sz.setDecimals(1)

        for label, w in [
            ("Center X:", self._cx), ("Center Y:", self._cy), ("Center Z:", self._cz),
            ("Size X:",   self._sx), ("Size Y:",   self._sy), ("Size Z:",   self._sz),
        ]:
            grid_lay.addRow(label, w)
        left_lay.addWidget(grid_grp)

        # Docking params
        param_grp = QGroupBox("Docking Parameters")
        param_lay = QFormLayout(param_grp)

        self._sf_combo = QComboBox()
        self._sf_combo.addItems(["vina", "vinardo", "ad4"])
        param_lay.addRow("Scoring Function:", self._sf_combo)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["rigid", "flexible"])
        param_lay.addRow("Docking Mode:", self._mode_combo)

        self._exhaust = QSpinBox(); self._exhaust.setRange(1,32); self._exhaust.setValue(8)
        param_lay.addRow("Exhaustiveness:", self._exhaust)

        self._cpu = QSpinBox(); self._cpu.setRange(1,64); self._cpu.setValue(4)
        param_lay.addRow("CPU Cores:", self._cpu)

        self._n_poses = QSpinBox(); self._n_poses.setRange(1,20); self._n_poses.setValue(9)
        param_lay.addRow("N Poses:", self._n_poses)

        left_lay.addWidget(param_grp)

        # Library summary
        lib_grp = QGroupBox("Ligand Library")
        lib_lay = QVBoxLayout(lib_grp)
        self._lib_label = QLabel("No library loaded.\nUse 📚 Library tab to import ligands.")
        self._lib_label.setStyleSheet("color:#585b70;")
        self._lib_label.setWordWrap(True)
        lib_lay.addWidget(self._lib_label)
        left_lay.addWidget(lib_grp)

        # Workers (parallel jobs)
        worker_grp = QGroupBox("Parallel Workers")
        worker_lay = QFormLayout(worker_grp)
        self._workers = QSpinBox(); self._workers.setRange(1,16); self._workers.setValue(2)
        worker_lay.addRow("Max Parallel Jobs:", self._workers)
        left_lay.addWidget(worker_grp)

        left_lay.addStretch()
        left.setWidget(left_w)
        splitter.addWidget(left)

        # ── Right: per-ligand job table ──────────────────────────────────
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(4, 0, 0, 0)

        right_lay.addWidget(QLabel("Batch Job Progress:"))

        self._job_table = QTableWidget()
        self._job_table.setColumnCount(5)
        self._job_table.setHorizontalHeaderLabels(
            ["Ligand", "Status", "Progress", "Energy", "Time"]
        )
        self._job_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._job_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._job_table.setAlternatingRowColors(True)
        self._job_table.verticalHeader().hide()
        self._job_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._job_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        right_lay.addWidget(self._job_table)

        # Overall progress
        prog_row = QHBoxLayout()
        prog_row.addWidget(QLabel("Overall:"))
        self._overall_prog = QProgressBar()
        self._overall_prog.setValue(0)
        prog_row.addWidget(self._overall_prog, stretch=1)
        self._prog_label = QLabel("0 / 0")
        self._prog_label.setStyleSheet("color:#585b70; font-size:11px; min-width:60px;")
        prog_row.addWidget(self._prog_label)
        right_lay.addLayout(prog_row)

        # Summary row
        self._batch_summary = QLabel("")
        self._batch_summary.setStyleSheet("color:#585b70; font-size:11px;")
        right_lay.addWidget(self._batch_summary)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_library(self, lib: LigandLibrary):
        """Called from MainWindow when library_ready signal arrives."""
        self._library = lib
        sel = len(lib.selected())
        self._lib_label.setText(
            f"Library: <b>{lib.name}</b><br>"
            f"Total: {len(lib)} ligands  |  Selected: {sel}"
        )
        self._lib_label.setTextFormat(Qt.RichText)
        self._lib_label.setStyleSheet("color:#cdd6f4;")
        self._run_btn.setEnabled(sel > 0 and bool(self._receptor_pick.text()))

    # ------------------------------------------------------------------ #
    # Batch run
    # ------------------------------------------------------------------ #

    def _run_batch(self):
        if not self._library:
            QMessageBox.warning(self, "No Library", "Load a ligand library first.")
            return

        receptor = self._receptor_pick.text()
        if not receptor or not os.path.isfile(receptor):
            QMessageBox.warning(self, "No Receptor", "Select a valid receptor file.")
            return

        ligands = self._library.selected()
        if not ligands:
            QMessageBox.warning(self, "Empty Selection", "No ligands selected.")
            return

        # Confirm
        reply = QMessageBox.question(
            self, "Confirm Batch",
            f"Submit {len(ligands)} docking jobs?\n"
            f"Receptor: {os.path.basename(receptor)}\n"
            f"Scoring: {self._sf_combo.currentText()} | Mode: {self._mode_combo.currentText()}",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # Update scheduler worker count
        self._scheduler._max_workers = self._workers.value()

        # Build base params
        base_params = self._collect_base_params(receptor)

        # Clear old job table
        self._job_table.setRowCount(len(ligands))
        self._job_rows.clear()
        self._completed_count = 0

        submitted_ids: List[str] = []

        for row, ligand in enumerate(ligands):
            params = dict(base_params)
            # Inject single-ligand as SMILES list
            params["ligand_smiles_list"] = [ligand.to_smiles_row()]
            params["ligand_name"]        = ligand.name

            job_id = self._scheduler.submit(
                name=f"Batch: {ligand.name}",
                parameters=params
            )
            self._job_rows[job_id] = row
            submitted_ids.append(job_id)

            # Populate table row
            self._job_table.setItem(row, 0, QTableWidgetItem(ligand.name))
            status_item = QTableWidgetItem("Queued")
            status_item.setForeground(QColor("#585b70"))
            self._job_table.setItem(row, 1, status_item)
            prog_item = QTableWidgetItem("—")
            prog_item.setTextAlignment(Qt.AlignCenter)
            self._job_table.setItem(row, 2, prog_item)
            self._job_table.setItem(row, 3, QTableWidgetItem("—"))
            self._job_table.setItem(row, 4, QTableWidgetItem("—"))

        # Update overall
        self._overall_prog.setMaximum(len(ligands))
        self._overall_prog.setValue(0)
        self._prog_label.setText(f"0 / {len(ligands)}")

        # Start scheduler
        self._scheduler.start()
        self._run_btn.setEnabled(False)
        self.batch_submitted.emit(submitted_ids)

    # ------------------------------------------------------------------ #
    # Job updates (called from MainWindow via scheduler callback)
    # ------------------------------------------------------------------ #

    def update_job(self, job: DockingJob):
        row = self._job_rows.get(job.job_id)
        if row is None:
            return

        status_colors = {
            JobStatus.QUEUED:   "#585b70",
            JobStatus.RUNNING:  "#89b4fa",
            JobStatus.FINISHED: "#a6e3a1",
            JobStatus.FAILED:   "#f38ba8",
            JobStatus.CANCELLED:"#fab387",
        }
        color = status_colors.get(job.status, "#cdd6f4")

        status_item = self._job_table.item(row, 1)
        if status_item:
            status_item.setText(job.status.value.capitalize())
            status_item.setForeground(QColor(color))

        prog_item = self._job_table.item(row, 2)
        if prog_item:
            prog_item.setText(f"{job.progress}%")

        time_item = self._job_table.item(row, 4)
        if time_item:
            time_item.setText(job.elapsed())

        if job.status == JobStatus.FINISHED:
            self._completed_count = getattr(self, "_completed_count", 0) + 1
            self._overall_prog.setValue(self._completed_count)
            total = self._overall_prog.maximum()
            self._prog_label.setText(f"{self._completed_count} / {total}")
            if self._completed_count >= total:
                self._run_btn.setEnabled(True)
                self._batch_summary.setText(
                    f"✅ Batch complete: {self._completed_count}/{total} jobs finished."
                )

        elif job.status == JobStatus.FAILED:
            self._completed_count = getattr(self, "_completed_count", 0) + 1
            self._overall_prog.setValue(self._completed_count)
            energy_item = self._job_table.item(row, 3)
            if energy_item:
                energy_item.setText("ERROR")
                energy_item.setForeground(QColor("#f38ba8"))

    # ------------------------------------------------------------------ #
    # Param builder
    # ------------------------------------------------------------------ #

    def _collect_base_params(self, receptor: str) -> dict:
        """Build base parameter dict for batch docking."""
        return {
            'receptor_path':    receptor,
            'sf_types':         [self._sf_combo.currentText()],
            'listmode':         [self._mode_combo.currentText()],
            'exhaustiveness':   self._exhaust.value(),
            'cpu':              self._cpu.value(),
            'n_poses':          self._n_poses.value(),
            'box_size':         (
                f"{self._cx.value()} {self._cy.value()} {self._cz.value()} "
                f"{self._sx.value()} {self._sy.value()} {self._sz.value()}"
            ),
            'center_x': self._cx.value(),
            'center_y': self._cy.value(),
            'center_z': self._cz.value(),
            'size_x':   self._sx.value(),
            'size_y':   self._sy.value(),
            'size_z':   self._sz.value(),
        }
