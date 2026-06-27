"""
LADOCK — Ligand Library Panel (gui/panels/library_panel.py)

Provides a full-featured ligand library browser:
  • Load from CSV / SDF / PDBQT folder / manual SMILES
  • Filter, sort, tag, select/deselect
  • Summary stats (count, Lipinski pass rate)
  • Emits library_ready(LigandLibrary) when user confirms selection

Signals
-------
library_ready(LigandLibrary)  — user clicked "Use in Batch Docking"
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLineEdit, QFileDialog, QComboBox, QCheckBox, QMessageBox,
    QSplitter, QTextEdit, QFrame, QInputDialog, QSizePolicy,
    QProgressBar, QMenu
)
from PySide6.QtCore import Qt, Signal, QThread, QObject
from PySide6.QtGui import QFont, QColor, QAction

from gui.widgets.common import SectionLabel
from data.ligand_library import (
    LigandLibrary, LigandEntry,
    load_smiles_csv, load_sdf, load_pdbqt_folder, load_smiles_string
)


# ---------------------------------------------------------------------------
# Worker: load library in background thread
# ---------------------------------------------------------------------------

class _LoadWorker(QObject):
    finished = Signal(object)   # LigandLibrary
    error    = Signal(str)

    def __init__(self, loader_fn, *args):
        super().__init__()
        self._fn   = loader_fn
        self._args = args

    def run(self):
        try:
            lib = self._fn(*self._args)
            self.finished.emit(lib)
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Library Panel
# ---------------------------------------------------------------------------

class LibraryPanel(QWidget):
    """
    Ligand library browser and manager.
    """

    library_ready = Signal(object)   # LigandLibrary

    COLUMNS = ["✓", "Name", "SMILES", "Activity", "Source", "Tags"]
    COL_SEL, COL_NAME, COL_SMILES, COL_ACT, COL_SRC, COL_TAGS = range(6)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lib: Optional[LigandLibrary] = None
        self._filtered: list[LigandEntry] = []
        self._thread: Optional[QThread] = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header
        hdr = QHBoxLayout()
        hdr.addWidget(SectionLabel("📚  Ligand Library"))
        hdr.addStretch()

        self._save_btn  = QPushButton("💾 Save Library")
        self._send_btn  = QPushButton("▶ Use in Batch Docking")
        self._send_btn.setStyleSheet(
            "QPushButton{background:#1a3e1a;border:1px solid #44ff88;"
            "border-radius:4px;color:#44ff88;padding:4px 12px;}"
            "QPushButton:hover{background:#22552a;}"
        )
        self._save_btn.clicked.connect(self._save_library)
        self._send_btn.clicked.connect(self._emit_library)
        self._save_btn.setEnabled(False)
        self._send_btn.setEnabled(False)
        hdr.addWidget(self._save_btn)
        hdr.addWidget(self._send_btn)
        layout.addLayout(hdr)

        # Load buttons
        load_bar = QHBoxLayout()
        load_bar.addWidget(QLabel("Import:"))
        for label, tip, fn in [
            ("📄 CSV",    "SMILES CSV file",          self._load_csv),
            ("🧪 SDF",    "SD File (.sdf / .sdf.gz)", self._load_sdf),
            ("📁 PDBQT",  "Folder of PDBQT files",    self._load_pdbqt_folder),
            ("✏️ SMILES", "Enter single SMILES",      self._add_smiles),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedHeight(28)
            btn.clicked.connect(fn)
            load_bar.addWidget(btn)
        load_bar.addStretch()

        # Select all / none
        self._sel_all_btn  = QPushButton("☑ All")
        self._sel_none_btn = QPushButton("☐ None")
        self._sel_lip_btn  = QPushButton("🔬 Lipinski")
        for btn in (self._sel_all_btn, self._sel_none_btn, self._sel_lip_btn):
            btn.setFixedHeight(26)
            load_bar.addWidget(btn)
        self._sel_all_btn.clicked.connect(self._select_all)
        self._sel_none_btn.clicked.connect(self._select_none)
        self._sel_lip_btn.clicked.connect(self._select_lipinski)

        layout.addLayout(load_bar)

        # Filter row
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Search name, SMILES, activity…")
        self._filter_edit.textChanged.connect(self._apply_filter)
        frow.addWidget(self._filter_edit, stretch=1)
        frow.addWidget(QLabel("Sort:"))
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["name", "activity", "mw", "hba", "hbd"])
        self._sort_combo.currentTextChanged.connect(self._apply_filter)
        frow.addWidget(self._sort_combo)
        layout.addLayout(frow)

        # Progress bar (shown while loading)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)     # indeterminate
        self._progress.setFixedHeight(6)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # Main splitter: table | detail
        splitter = QSplitter(Qt.Vertical)

        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(self.COL_SMILES, QHeaderView.Stretch)
        self._table.setSortingEnabled(False)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.cellDoubleClicked.connect(self._toggle_selected)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        splitter.addWidget(self._table)

        # Detail pane
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setFont(QFont("Monospace", 9))
        self._detail.setMaximumHeight(100)
        splitter.addWidget(self._detail)

        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        # Summary bar
        self._summary = QLabel("No library loaded.")
        self._summary.setStyleSheet("color:gray; font-size:11px;")
        layout.addWidget(self._summary)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_library(self, lib: LigandLibrary):
        self._lib = lib
        self._apply_filter()
        self._save_btn.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._update_summary()

    # ------------------------------------------------------------------ #
    # Loaders
    # ------------------------------------------------------------------ #

    def _load_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open SMILES CSV", "", "CSV Files (*.csv);;All (*)"
        )
        if path:
            self._load_async(load_smiles_csv, path)

    def _load_sdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open SDF File", "",
            "SDF Files (*.sdf *.sdf.gz);;All (*)"
        )
        if path:
            self._load_async(load_sdf, path)

    def _load_pdbqt_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select PDBQT Folder")
        if folder:
            self._load_async(load_pdbqt_folder, folder)

    def _add_smiles(self):
        smiles, ok = QInputDialog.getText(self, "Add SMILES", "Enter SMILES:")
        if not ok or not smiles.strip():
            return
        name, ok2 = QInputDialog.getText(self, "Ligand Name", "Name (optional):")
        if not ok2:
            name = ""
        entry = load_smiles_string(smiles.strip(), name.strip())
        if self._lib is None:
            self._lib = LigandLibrary(name="Manual")
        self._lib.add(entry)
        self._apply_filter()
        self._save_btn.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._update_summary()

    def _load_async(self, fn, *args):
        self._progress.setVisible(True)
        worker = _LoadWorker(fn, *args)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_library_loaded)
        worker.finished.connect(thread.quit)
        worker.error.connect(self._on_load_error)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker   # keep reference
        thread.start()

    def _on_library_loaded(self, lib: LigandLibrary):
        self._progress.setVisible(False)
        if self._lib is not None:
            # Append to existing library
            self._lib.add_many(lib.entries)
        else:
            self._lib = lib
        self._apply_filter()
        self._save_btn.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._update_summary()

    def _on_load_error(self, msg: str):
        self._progress.setVisible(False)
        QMessageBox.warning(self, "Load Error", msg)

    # ------------------------------------------------------------------ #
    # Table rendering
    # ------------------------------------------------------------------ #

    def _apply_filter(self):
        if self._lib is None:
            return
        q = self._filter_edit.text().strip()
        sort_key = self._sort_combo.currentText()
        entries = self._lib.filter(q) if q else list(self._lib.entries)
        entries = sorted(entries, key=lambda e: (getattr(e, sort_key, "") or ""))
        self._filtered = entries
        self._render_table(entries)
        self._update_summary()

    def _render_table(self, entries: list[LigandEntry]):
        self._table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            # Checkbox column
            chk = QTableWidgetItem()
            chk.setCheckState(Qt.Checked if e.selected else Qt.Unchecked)
            chk.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, self.COL_SEL, chk)

            def _c(txt, color="#cdd6f4"):
                it = QTableWidgetItem(str(txt))
                it.setForeground(QColor(color))
                return it

            self._table.setItem(row, self.COL_NAME,   _c(e.name, "#89b4fa"))
            self._table.setItem(row, self.COL_SMILES,  _c(e.smiles))
            self._table.setItem(row, self.COL_ACT,     _c(e.activity or "—", "#a6e3a1"))
            src = os.path.basename(e.source) if e.source else "manual"
            self._table.setItem(row, self.COL_SRC,     _c(src, "#585b70"))
            self._table.setItem(row, self.COL_TAGS,    _c(", ".join(e.tags) or "—", "#585b70"))

    # ------------------------------------------------------------------ #
    # Selection helpers
    # ------------------------------------------------------------------ #

    def _toggle_selected(self, row: int, col: int):
        """Double-click → toggle selected flag."""
        if row >= len(self._filtered):
            return
        entry = self._filtered[row]
        entry.selected = not entry.selected
        chk = self._table.item(row, self.COL_SEL)
        if chk:
            chk.setCheckState(Qt.Checked if entry.selected else Qt.Unchecked)
        self._update_summary()

    def _select_all(self):
        for e in self._lib.entries if self._lib else []:
            e.selected = True
        self._apply_filter()

    def _select_none(self):
        for e in self._lib.entries if self._lib else []:
            e.selected = False
        self._apply_filter()

    def _select_lipinski(self):
        if not self._lib:
            return
        for e in self._lib.entries:
            e.selected = e.passes_lipinski()
        self._apply_filter()

    def _on_row_selected(self):
        rows = self._table.selectedIndexes()
        if not rows:
            return
        row = rows[0].row()
        if row >= len(self._filtered):
            return
        e = self._filtered[row]
        lines = [
            f"Name:     {e.name}",
            f"SMILES:   {e.smiles or '—'}",
            f"Activity: {e.activity or '—'}",
            f"Source:   {e.source or '—'}",
            f"PDBQT:    {e.pdbqt or '—'}",
            f"Tags:     {', '.join(e.tags) or '—'}",
            f"MW:       {e.mw or '—'}  HBA:{e.hba}  HBD:{e.hbd}",
            f"Lipinski: {'✅ Pass' if e.passes_lipinski() else '❌ Fail'}",
        ]
        self._detail.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------ #
    # Context menu
    # ------------------------------------------------------------------ #

    def _context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._filtered):
            return
        entry = self._filtered[row]
        menu = QMenu(self)
        if entry.selected:
            menu.addAction("☐ Deselect").triggered.connect(
                lambda: self._set_selected(row, False))
        else:
            menu.addAction("☑ Select").triggered.connect(
                lambda: self._set_selected(row, True))
        menu.addSeparator()
        menu.addAction("🏷 Add Tag…").triggered.connect(
            lambda: self._add_tag(entry))
        menu.addAction("🗑 Remove").triggered.connect(
            lambda: self._remove_entry(entry))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _set_selected(self, row: int, val: bool):
        self._filtered[row].selected = val
        chk = self._table.item(row, self.COL_SEL)
        if chk:
            chk.setCheckState(Qt.Checked if val else Qt.Unchecked)
        self._update_summary()

    def _add_tag(self, entry: LigandEntry):
        tag, ok = QInputDialog.getText(self, "Add Tag", "Tag:")
        if ok and tag.strip():
            entry.tags.append(tag.strip())
            self._apply_filter()

    def _remove_entry(self, entry: LigandEntry):
        if self._lib:
            self._lib.remove(entry.name)
            self._apply_filter()
            self._update_summary()

    # ------------------------------------------------------------------ #
    # Save / emit
    # ------------------------------------------------------------------ #

    def _save_library(self):
        if not self._lib:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Library", f"{self._lib.name}.lib.json",
            "Library JSON (*.lib.json *.json)"
        )
        if path:
            try:
                self._lib.save(path)
                self._summary.setText(
                    self._summary.text() + f"  ·  Saved: {os.path.basename(path)}"
                )
            except Exception as e:
                QMessageBox.warning(self, "Save Error", str(e))

    def _emit_library(self):
        if self._lib and len(self._lib.selected()) > 0:
            self.library_ready.emit(self._lib)
        else:
            QMessageBox.information(self, "Empty Selection",
                                    "No ligands selected. Select at least one.")

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #

    def _update_summary(self):
        if not self._lib:
            return
        total = len(self._lib)
        sel   = len(self._lib.selected())
        lip   = len(self._lib.lipinski_pass())
        self._summary.setText(
            f"Total: {total}  |  Selected: {sel}  |  Lipinski pass: {lip}"
            + (f"  |  Showing: {len(self._filtered)}" if self._filtered else "")
        )
