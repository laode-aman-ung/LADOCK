"""
LADOCK — Result Explorer Panel (PySide6)

Layout:
  ┌──────────────────┬──────────────────────────────────────────────┐
  │  📁 Result Files  │  📊 Table View (selected CSV)                │
  │  results_A.csv ● │  Filter: [___]  Sort: [___] ▲                │
  │  [Open…] [↺]     │  │ Rank │ Ligand   │  ΔG  │ RMSD │          │
  │                  │           (double-click row → tab below)      │
  └──────────────────┴──────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────────────┐
  │  🔬 [lig_01 × vina ×]  [lig_02 × ad4 ×]                        │
  │  ┌─────────────────────────┬─────────────────────────────────┐  │
  │  │   3D Molecular Viewer   │   Interaction Analysis          │  │
  │  └─────────────────────────┴─────────────────────────────────┘  │
  └─────────────────────────────────────────────────────────────────┘
"""

import csv
import json
import os

import pandas as pd
from PySide6.QtCore import Qt, QTimer, Signal, QFileSystemWatcher, QPoint, QPointF
from PySide6.QtGui import QBrush, QColor, QFont, QPixmap, QImage, QPainter, QRegion
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QRadioButton, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from gui.widgets.common import SectionLabel
from core.ligand_smiles import smiles_from_ccd, smiles_from_structure


_STYLE_LIST = """
QListWidget {
    background: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 4px;
    font-size: 11px;
    outline: none;
}
QListWidget::item {
    padding: 5px 8px;
    border-bottom: 1px solid #1e1e2e;
}
QListWidget::item:selected {
    background: #45475a;
    color: #89b4fa;
}
QListWidget::item:hover { background: #313244; }
"""

_STYLE_TABLE = """
QTableWidget {
    background: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
    gridline-color: #313244;
    font-size: 11px;
}
QTableWidget::item:selected { background: #45475a; }
QHeaderView::section {
    background: #181825;
    color: #89b4fa;
    border: 1px solid #313244;
    padding: 4px;
    font-size: 11px;
}
"""

_STYLE_TAB = """
QTabWidget::pane {
    border: 1px solid #313244;
    background: #1e1e2e;
}
QTabBar::tab {
    background: #181825;
    color: #cdd6f4;
    padding: 4px 10px;
    border: 1px solid #313244;
    border-bottom: none;
    font-size: 11px;
}
QTabBar::tab:selected { background: #313244; color: #89b4fa; }
QTabBar::tab:hover    { background: #2a2a3c; }
"""


# ─────────────────────────────────────────────────────────────────────────────
class _LigandDetailWidget(QWidget):
    """
    Per-ligand detail: QTabWidget with three tabs:
      🔬 3D View   — MolecularViewerPanel with interaction highlights
      🗺 2D Diagram — Radial interaction diagram (residues around ligand)
      📋 Table     — Sortable interaction table + summary badges
    All tabs are lazy-loaded on first show to avoid startup overhead.
    """

    def __init__(self, label: str, out_path: str, rec_pdbqt: str,
                 energy: str = "", sf: str = "", ligand_smiles: str = "",
                 ligand_source_path: str = "",
                 parent=None):
        super().__init__(parent)
        self._label       = label
        self._out_path    = out_path
        self._rec_pdbqt   = rec_pdbqt
        self._energy      = energy
        self._sf          = sf
        self._ligand_smiles = ligand_smiles or ""
        self._ligand_source_path = ligand_source_path or ""
        self._loaded      = False
        self._result      = None      # AnalysisResult (shared between tabs)
        self._viewer      = None
        self._diagram     = None
        self._inter_tbl   = None
        self._summary_bar = None
        self._filter_cbs: dict = {}
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        # ── Header row ─────────────────────────────────────────────────
        hdr = QHBoxLayout()
        name_lbl = QLabel(f"<b>{self._label}</b>")
        name_lbl.setStyleSheet("color:#89b4fa; font-size:12px;")
        hdr.addWidget(name_lbl)
        if self._energy:
            try:
                e = float(self._energy)
                color = ('#a6e3a1' if e <= -8 else '#fab387' if e <= -6 else '#f38ba8')
                badge = QLabel(f"ΔG = {e:.2f} kcal/mol")
                badge.setStyleSheet(
                    f"color:{color}; font-size:11px; font-weight:bold; "
                    "background:#181825; padding:2px 6px; border-radius:4px;")
                hdr.addWidget(badge)
            except ValueError:
                pass
        if self._sf:
            sf_lbl = QLabel(f"[{self._sf}]")
            sf_lbl.setStyleSheet("color:#cba6f7; font-size:11px;")
            hdr.addWidget(sf_lbl)
        hdr.addStretch()
        reload_btn = QPushButton("⟳ Reload")
        reload_btn.setFixedWidth(72)
        reload_btn.setFixedHeight(22)
        reload_btn.clicked.connect(self._force_reload)
        hdr.addWidget(reload_btn)
        layout.addLayout(hdr)

        # ── Shared interaction-type filter bar ─────────────────────────
        from gui.panels.interaction_panel import ITYPE_COLORS, ITYPE_ICONS
        filter_bar = QWidget()
        filter_bar.setStyleSheet("background:#1e1e2e;")
        fb_lay = QHBoxLayout(filter_bar)
        fb_lay.setContentsMargins(6, 2, 6, 2)
        fb_lay.setSpacing(8)
        show_lbl = QLabel("Show:")
        show_lbl.setStyleSheet("color:#a6adc8; font-size:10px;")
        fb_lay.addWidget(show_lbl)
        self._filter_cbs: dict[str, QCheckBox] = {}
        for itype, color in ITYPE_COLORS.items():
            icon = ITYPE_ICONS.get(itype, "")
            cb = QCheckBox(f"{icon} {itype}")
            cb.setChecked(itype == "H-Bond")
            cb.setStyleSheet(
                f"QCheckBox{{color:{color}; font-size:10px;}}"
                f"QCheckBox::indicator{{width:12px;height:12px;}}"
            )
            cb.stateChanged.connect(self._on_filter_changed)
            self._filter_cbs[itype] = cb
            fb_lay.addWidget(cb)
        fb_lay.addStretch()
        layout.addWidget(filter_bar)

        # ── Tab widget ─────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(_STYLE_TAB)
        self._tabs.setDocumentMode(False)

        # Three fixed containers — content added lazily
        self._tab_3d  = QWidget(); self._tab_3d.setLayout(QVBoxLayout())
        self._tab_2d  = QWidget(); self._tab_2d.setLayout(QVBoxLayout())
        self._tab_tbl = QWidget(); self._tab_tbl.setLayout(QVBoxLayout())
        for c in (self._tab_3d, self._tab_2d, self._tab_tbl):
            c.layout().setContentsMargins(0, 0, 0, 0)
            c.layout().setSpacing(0)

        # Placeholders
        self._ph_3d  = self._make_placeholder("⏳  Loading 3D viewer…")
        self._ph_2d  = self._make_placeholder("⏳  Running interaction analysis…")
        self._ph_tbl = self._make_placeholder("⏳  Loading interaction table…")
        self._tab_3d.layout().addWidget(self._ph_3d)
        self._tab_2d.layout().addWidget(self._ph_2d)
        self._tab_tbl.layout().addWidget(self._ph_tbl)

        self._tabs.addTab(self._tab_3d,  "🔬 3D View")
        self._tabs.addTab(self._tab_2d,  "🗺 2D Diagram")
        self._tabs.addTab(self._tab_tbl, "📋 Interactions")

        layout.addWidget(self._tabs, stretch=1)

        # Path info strip
        path_lbl = QLabel(f"Output: {self._out_path or '—'}")
        path_lbl.setStyleSheet("color:#585b70; font-size:10px;")
        path_lbl.setWordWrap(True)
        layout.addWidget(path_lbl)

    # ── Filter helper ───────────────────────────────────────────────────
    def _checked_types(self) -> set[str]:
        return {t for t, cb in self._filter_cbs.items() if cb.isChecked()}

    def _on_filter_changed(self):
        types = self._checked_types()
        # Update 2D diagram
        if self._diagram is not None:
            self._diagram.set_type_filter(types)
        # Update 3D view
        if self._viewer is not None and self._result is not None:
            bonds = self._result.to_vectors(list(types))
            self._viewer.set_interaction_view(bonds)
        # Update interaction table
        if self._inter_tbl is not None and self._result is not None:
            self._inter_tbl.load_filtered(self._result, types)
            if self._summary_bar is not None:
                from engine.interaction_analyzer import AnalysisResult
                filtered_interactions = [
                    i for i in self._result.interactions if i.itype in types]
                counts: dict = {}
                for i in filtered_interactions:
                    counts[i.itype] = counts.get(i.itype, 0) + 1
                self._summary_bar.update_counts(counts)

    @staticmethod
    def _make_placeholder(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color:#585b70; font-size:12px; background:#181825;")
        return lbl

    # ------------------------------------------------------------------ #
    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            QTimer.singleShot(120, self._do_load)

    def _force_reload(self):
        self._loaded = False
        self._viewer = None
        self._diagram = None
        self._inter_tbl = None
        self._summary_bar = None
        self._result = None
        for container, ph_text in (
            (self._tab_3d,  "⏳  Loading 3D viewer…"),
            (self._tab_2d,  "⏳  Running interaction analysis…"),
            (self._tab_tbl, "⏳  Loading interaction table…"),
        ):
            lay = container.layout()
            while lay.count():
                item = lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            ph = self._make_placeholder(ph_text)
            lay.addWidget(ph)
        self._do_load()

    # ------------------------------------------------------------------ #
    def _swap_in(self, container: QWidget, real_widget: QWidget):
        """Remove placeholder from container and add real_widget."""
        lay = container.layout()
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        lay.addWidget(real_widget)

    def _set_error(self, container: QWidget, msg: str):
        self._swap_in(container, self._make_placeholder(f"⚠  {msg}"))

    # ------------------------------------------------------------------ #
    def _do_load(self):
        self._loaded = True
        if not self._out_path or not os.path.isfile(self._out_path):
            for c in (self._tab_3d, self._tab_2d, self._tab_tbl):
                self._set_error(c, f"Output file not found:\n{self._out_path}")
            return

        # ── Run analysis (shared between 2D and Table tabs) ─────────────
        try:
            from engine.interaction_analyzer import (
                analyze_from_files, AnalysisResult)
            rec = self._rec_pdbqt if (self._rec_pdbqt
                                      and os.path.isfile(self._rec_pdbqt)) else None
            if rec:
                self._result = analyze_from_files(rec, self._out_path)
            else:
                self._result = AnalysisResult()
        except Exception as exc:
            self._result = None
            self._set_error(self._tab_2d,  f"Analysis error: {exc}")
            self._set_error(self._tab_tbl, f"Analysis error: {exc}")

        types = self._checked_types()

        # ── 3D Viewer ───────────────────────────────────────────────────
        try:
            from gui.viewer.molecular_viewer import MolecularViewerPanel
            self._viewer = MolecularViewerPanel()

            # Toolbar for 3D tab
            w3d = QWidget(); vl3d = QVBoxLayout(w3d)
            vl3d.setContentsMargins(0, 0, 0, 0); vl3d.setSpacing(0)
            tb3d = self._make_tab_toolbar([
                ("📷 Capture", self._capture_3d),
            ])
            vl3d.addWidget(tb3d)
            vl3d.addWidget(self._viewer, stretch=1)
            self._swap_in(self._tab_3d, w3d)

            if self._rec_pdbqt and os.path.isfile(self._rec_pdbqt):
                self._viewer.load_receptor(self._rec_pdbqt)
            self._viewer.load_ligand(self._out_path)
            if self._result and self._result.interactions:
                bonds = self._result.to_vectors(list(types))
                QTimer.singleShot(1800,
                    lambda b=bonds: self._viewer.set_interaction_view(b))
        except Exception as exc:
            self._set_error(self._tab_3d, f"3D viewer error: {exc}")

        # ── 2D Diagram ──────────────────────────────────────────────────
        try:
            from gui.panels.interaction_panel import InteractionDiagram
            self._diagram = InteractionDiagram()
            self._diagram.set_type_filter(types)
            self._diagram.set_ligand_path(self._ligand_source_path or self._out_path)
            self._diagram.set_ligand_smiles(self._ligand_smiles)
            if self._result:
                self._diagram.set_result(self._result)

            w2d = QWidget(); vl2d = QVBoxLayout(w2d)
            vl2d.setContentsMargins(0, 0, 0, 0); vl2d.setSpacing(0)
            tb2d = self._make_tab_toolbar([
                ("🔍+ Zoom In",    lambda: self._zoom_2d(+1)),
                ("🔍- Zoom Out",   lambda: self._zoom_2d(-1)),
                ("⟳ Reset View",  self._reset_2d_view),
                ("📷 Capture",     self._capture_2d),
            ])
            vl2d.addWidget(tb2d)
            vl2d.addWidget(self._diagram, stretch=1)
            self._swap_in(self._tab_2d, w2d)
        except Exception as exc:
            self._set_error(self._tab_2d, f"2D diagram error: {exc}")

        # ── Interaction Table ────────────────────────────────────────────
        try:
            from gui.panels.interaction_panel import InteractionTable, SummaryBar
            wtbl = QWidget(); vltbl = QVBoxLayout(wtbl)
            vltbl.setContentsMargins(4, 4, 4, 4); vltbl.setSpacing(4)

            tbtbl = self._make_tab_toolbar([
                ("💾 Export CSV", self._export_table_csv),
            ])
            vltbl.addWidget(tbtbl)

            self._summary_bar = SummaryBar()
            self._inter_tbl = InteractionTable()
            vltbl.addWidget(self._summary_bar)
            vltbl.addWidget(self._inter_tbl, stretch=1)
            if self._result:
                self._inter_tbl.load_filtered(self._result, types)
                filtered_counts: dict = {}
                for i in self._result.interactions:
                    if i.itype in types:
                        filtered_counts[i.itype] = filtered_counts.get(i.itype, 0) + 1
                self._summary_bar.update_counts(filtered_counts)
            self._swap_in(self._tab_tbl, wtbl)
        except Exception as exc:
            self._set_error(self._tab_tbl, f"Table error: {exc}")

    # ------------------------------------------------------------------ #
    @staticmethod
    def _make_tab_toolbar(actions: list[tuple[str, object]]) -> QWidget:
        """Create a slim action bar above a tab's content."""
        bar = QWidget()
        bar.setFixedHeight(26)
        bar.setStyleSheet("background:#1e1e2e;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)
        lay.addStretch()
        for label, slot in actions:
            btn = QPushButton(label)
            btn.setFixedHeight(20)
            btn.setStyleSheet(
                "QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
                "border-radius:3px;padding:1px 8px;font-size:10px;}"
                "QPushButton:hover{background:#45475a;}"
            )
            btn.clicked.connect(slot)
            lay.addWidget(btn)
        return bar

    # ── Capture 3D ─────────────────────────────────────────────────────
    def _capture_3d(self):
        if self._viewer is None:
            return
        transparent = self._ask_capture_mode("3D Viewer")
        self._viewer._request_screenshot(transparent=transparent)

    # ── Zoom helpers for 2D diagram ─────────────────────────────────────
    def _zoom_2d(self, direction: int):
        if self._diagram is None:
            return
        canvas = getattr(self._diagram, '_canvas', self._diagram)
        factor = 1.18 if direction > 0 else (1.0 / 1.18)
        new_zoom = max(0.25, min(6.0, canvas._zoom * factor))
        # Zoom toward centre of canvas
        cx, cy = canvas.width() / 2.0, canvas.height() / 2.0
        canvas._pan = QPointF(
            cx - new_zoom / canvas._zoom * (cx - canvas._pan.x()),
            cy - new_zoom / canvas._zoom * (cy - canvas._pan.y()),
        )
        canvas._zoom = new_zoom
        canvas.update()

    def _reset_2d_view(self):
        if self._diagram is None:
            return
        canvas = getattr(self._diagram, '_canvas', self._diagram)
        canvas.reset_view()

    # ── Capture 2D diagram ──────────────────────────────────────────────
    def _capture_2d(self):
        if self._diagram is None:
            return
        canvas = getattr(self._diagram, '_canvas', self._diagram)
        mode = self._ask_capture_mode("2D Diagram")
        if mode is None:
            return

        if mode == "svg":
            path, _ = QFileDialog.getSaveFileName(
                self, "Save 2D Diagram (SVG)",
                "interaction_2d.svg",
                "SVG Vector (*.svg)"
            )
            if not path:
                return
            ok = canvas.render_svg(path)
        else:  # normal PNG
            path, _ = QFileDialog.getSaveFileName(
                self, "Save 2D Diagram",
                "interaction_2d.png",
                "PNG Images (*.png)"
            )
            if not path:
                return
            img = canvas._render_full()
            ok = img.save(path, "PNG")

        if not ok:
            QMessageBox.warning(self, "Save Failed",
                                f"Could not save image to:\n{path}")

    # ── Helper: capture mode dialog ─────────────────────────────────────
    @staticmethod
    def _ask_capture_mode(context: str):
        """Show dialog with 2 export options. Returns 'normal'|'svg' or None."""
        dlg = QDialog()
        dlg.setWindowTitle(f"Export {context}")
        dlg.setFixedWidth(360)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)
        lay.addWidget(QLabel(f"<b>Choose export format for {context}:</b>"))

        rb_normal = QRadioButton("🖼  Normal PNG  — exports current view as-is")
        rb_normal.setChecked(True)
        rb_svg    = QRadioButton("📐  SVG Vector  — infinitely scalable (best for journals)")
        lay.addWidget(rb_normal)
        lay.addWidget(rb_svg)

        note = QLabel(
            "<small><i>SVG is recommended for journal figures: vector format, "
            "no pixelation at any zoom. Import into Word (Insert → Pictures) "
            "or Inkscape for final layout.</i></small>"
        )
        note.setWordWrap(True)
        lay.addWidget(note)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.Accepted:
            return None
        if rb_svg.isChecked():
            return "svg"
        return "normal"

    # ── Export filtered interaction table as CSV ───────────────────────
    def _export_table_csv(self):
        if self._inter_tbl is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Interactions CSV", "interactions.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return
        tbl = self._inter_tbl
        headers = [tbl.horizontalHeaderItem(c).text()
                   for c in range(tbl.columnCount())]
        rows = []
        for r in range(tbl.rowCount()):
            row_data = []
            for c in range(tbl.columnCount()):
                item = tbl.item(r, c)
                row_data.append(item.text() if item else "")
            rows.append(row_data)
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)
            QMessageBox.information(self, "Exported",
                                    f"Interactions saved to:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export Failed", str(exc))


# ─────────────────────────────────────────────────────────────────────────────
class ResultExplorerPanel(QWidget):
    """
    Three-section Results Explorer:
      • Top-left : list of result CSV files (auto-detected + manual open)
      • Top-right: sortable/filterable table of the selected CSV
      • Bottom   : dynamic QTabWidget — one tab per double-clicked ligand row,
                   each showing 3D viewer + interaction analysis
    """

    ligand_selected = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._df:           pd.DataFrame | None = None
        self._current_csv:  str  = ""
        self._known_csvs:   list = []
        self._watch_dir:    str  = ""
        self._meta:         dict = {}        # loaded from .meta.json
        self._tab_keys:     dict = {}        # tab_key → tab index
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_watch_dir_changed)
        self._build_ui()

    # ────────────────────────────────────────────────────── UI construction ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.addWidget(SectionLabel("Result Explorer"))
        title_row.addStretch()
        root.addLayout(title_row)

        # Outer vertical splitter: top panes / bottom tabs
        v_splitter = QSplitter(Qt.Vertical)
        v_splitter.setChildrenCollapsible(True)

        # ── TOP: horizontal splitter (file list | table) ───────────────
        h_splitter = QSplitter(Qt.Horizontal)
        h_splitter.setChildrenCollapsible(False)

        # Left pane: file list
        left = QWidget()
        left.setMinimumWidth(180)
        left.setMaximumWidth(320)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.setSpacing(4)
        lv.addWidget(QLabel("📁  Result Files"))

        self._file_list = QListWidget()
        self._file_list.setStyleSheet(_STYLE_LIST)
        self._file_list.setToolTip("Click to view  |  Double-click row in table to open 3D tab")
        self._file_list.currentItemChanged.connect(self._on_file_selected)
        lv.addWidget(self._file_list, stretch=1)

        left_btns = QHBoxLayout()
        self._open_btn = QPushButton("Open…")
        self._open_btn.clicked.connect(self._open_csv)
        self._refresh_btn = QPushButton("↺")
        self._refresh_btn.setFixedWidth(32)
        self._refresh_btn.clicked.connect(self._refresh_file_list)
        left_btns.addWidget(self._open_btn, stretch=1)
        left_btns.addWidget(self._refresh_btn)
        lv.addLayout(left_btns)
        h_splitter.addWidget(left)

        # Right pane: CSV table
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.setSpacing(4)

        self._csv_label = QLabel("— no file selected —")
        self._csv_label.setStyleSheet(
            "color:#585b70; font-size:11px; font-style:italic;")
        rv.addWidget(self._csv_label)

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Search any column…")
        self._filter_edit.textChanged.connect(self._apply_filter)
        ctrl_row.addWidget(self._filter_edit, stretch=1)

        ctrl_row.addWidget(QLabel("Sort:"))
        self._sort_combo = QComboBox()
        self._sort_combo.setMinimumWidth(140)
        self._sort_combo.currentTextChanged.connect(self._apply_sort)
        ctrl_row.addWidget(self._sort_combo)

        self._sort_dir_btn = QPushButton("▲")
        self._sort_dir_btn.setFixedWidth(30)
        self._sort_dir_btn.setCheckable(True)
        self._sort_dir_btn.toggled.connect(self._apply_sort)
        self._sort_dir_btn.toggled.connect(
            lambda c: self._sort_dir_btn.setText("▼" if c else "▲"))
        ctrl_row.addWidget(self._sort_dir_btn)
        rv.addLayout(ctrl_row)

        self._table = QTableWidget()
        self._table.setStyleSheet(_STYLE_TABLE)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.horizontalHeader().sectionClicked.connect(
            self._on_header_clicked)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.cellDoubleClicked.connect(self._open_ligand_tab)
        rv.addWidget(self._table, stretch=1)

        # hint label
        hint_lbl = QLabel("💡 Double-click a row to open 3D viewer + interaction analysis")
        hint_lbl.setStyleSheet("color:#585b70; font-size:10px; font-style:italic;")
        rv.addWidget(hint_lbl)

        bot_row = QHBoxLayout()
        self._export_btn = QPushButton("Export…")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        bot_row.addWidget(self._export_btn)
        bot_row.addStretch()
        self._summary_label = QLabel("No results loaded.")
        self._summary_label.setStyleSheet("color:#585b70; font-size:11px;")
        bot_row.addWidget(self._summary_label)
        rv.addLayout(bot_row)

        h_splitter.addWidget(right)
        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        v_splitter.addWidget(h_splitter)

        # ── BOTTOM: ligand detail tabs ─────────────────────────────────
        bottom_container = QWidget()
        bv = QVBoxLayout(bottom_container)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(2)

        tab_hdr = QHBoxLayout()
        tab_hdr.addWidget(QLabel("🔬  Ligand Detail"))
        tab_hdr.addStretch()
        close_all_btn = QPushButton("Close All Tabs")
        close_all_btn.setFixedWidth(110)
        close_all_btn.clicked.connect(self._close_all_tabs)
        tab_hdr.addWidget(close_all_btn)
        bv.addLayout(tab_hdr)

        self._tab_widget = QTabWidget()
        self._tab_widget.setStyleSheet(_STYLE_TAB)
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close)
        bv.addWidget(self._tab_widget, stretch=1)
        v_splitter.addWidget(bottom_container)

        # Default sizes: top 55%, bottom 45%
        v_splitter.setSizes([500, 420])
        root.addWidget(v_splitter, stretch=1)

    # ────────────────────────────────────────────────────────── Public API ──

    def load_csv(self, csv_path: str):
        """Load a CSV, display it, and select it in the left list."""
        if not os.path.isfile(csv_path):
            return
        self._current_csv = csv_path
        self._load_meta(csv_path)
        self._add_to_list(csv_path)
        self._select_in_list(csv_path)
        self._load_and_render(csv_path)

    def set_watch_dir(self, directory: str):
        """Watch a directory for new results_*.csv files."""
        if self._watch_dir and self._watch_dir in self._watcher.directories():
            self._watcher.removePath(self._watch_dir)
        self._watch_dir = directory
        if directory:
            os.makedirs(directory, exist_ok=True)
            self._watcher.addPath(directory)
        self._refresh_file_list()

    # ─────────────────────────────────────────────────────── Meta loading ──

    def _load_meta(self, csv_path: str):
        """Load companion .meta.json file if it exists."""
        self._meta = {}
        meta_path = csv_path.replace('.csv', '.meta.json')
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as fh:
                    self._meta = json.load(fh)
            except Exception:
                self._meta = {}

    def _get_row_meta(self, orig_row_idx: int) -> dict:
        """Return meta dict for a table row (by original df row index)."""
        rows = self._meta.get('rows', [])
        if 0 <= orig_row_idx < len(rows):
            return rows[orig_row_idx]
        return {}

    # ────────────────────────────────────────────────── Left pane helpers ──

    def _add_to_list(self, csv_path: str):
        if csv_path in self._known_csvs:
            self._known_csvs.remove(csv_path)
        self._known_csvs.insert(0, csv_path)
        self._known_csvs = self._known_csvs[:50]
        self._rebuild_file_list()

    def _rebuild_file_list(self):
        self._file_list.blockSignals(True)
        self._file_list.clear()
        for path in self._known_csvs:
            if not os.path.isfile(path):
                continue
            item = QListWidgetItem()
            item.setText(os.path.basename(path))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self._file_list.addItem(item)
        self._file_list.blockSignals(False)
        if self._current_csv:
            self._select_in_list(self._current_csv)

    def _select_in_list(self, csv_path: str):
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            if item and item.data(Qt.UserRole) == csv_path:
                self._file_list.blockSignals(True)
                self._file_list.setCurrentItem(item)
                self._file_list.blockSignals(False)
                return

    def _refresh_file_list(self):
        if self._watch_dir and os.path.isdir(self._watch_dir):
            found = sorted([
                os.path.join(self._watch_dir, f)
                for f in os.listdir(self._watch_dir)
                if f.startswith('results_') and f.endswith('.csv')
            ], key=lambda x: os.path.getmtime(x), reverse=True)
            for p in found:
                if p not in self._known_csvs:
                    self._known_csvs.append(p)
            self._known_csvs = sorted(
                [p for p in set(self._known_csvs) if os.path.isfile(p)],
                key=lambda x: os.path.getmtime(x), reverse=True)[:50]
        self._rebuild_file_list()

    def _on_file_selected(self, current, previous):
        if current is None:
            return
        csv_path = current.data(Qt.UserRole)
        if csv_path and csv_path != self._current_csv:
            self._current_csv = csv_path
            self._load_meta(csv_path)
            self._load_and_render(csv_path)

    def _on_watch_dir_changed(self, path: str):
        self._refresh_file_list()
        try:
            candidates = sorted([
                os.path.join(path, f)
                for f in os.listdir(path)
                if f.startswith('results_') and f.endswith('.csv')
                and os.path.isfile(os.path.join(path, f))
            ], key=os.path.getmtime, reverse=True)
            if candidates and candidates[0] != self._current_csv:
                self.load_csv(candidates[0])
        except Exception:
            pass

    # ───────────────────────────────────────────────── Right pane helpers ──

    def _load_and_render(self, csv_path: str):
        from data.result_parser import load_results_csv
        try:
            self._df = load_results_csv(csv_path)
        except Exception as e:
            QMessageBox.warning(self, "Load Error", f"Cannot read CSV:\n{e}")
            return
        self._csv_label.setText(f"📄  {os.path.basename(csv_path)}")
        self._csv_label.setStyleSheet("color:#89b4fa; font-size:11px;")
        self._filter_edit.clear()
        self._populate_sort_combo()
        self._render_table(self._df)
        self._export_btn.setEnabled(True)

    def _populate_sort_combo(self):
        self._sort_combo.blockSignals(True)
        self._sort_combo.clear()
        if self._df is not None:
            self._sort_combo.addItems(self._df.columns.tolist())
            energy_cols = [c for c in self._df.columns
                           if any(k in c.lower()
                                  for k in ('energy', 'affinity', 'δg', 'dg'))]
            if energy_cols:
                self._sort_combo.setCurrentText(energy_cols[0])
        self._sort_combo.blockSignals(False)

    def _render_table(self, df: pd.DataFrame):
        if df is None or df.empty:
            self._table.clearContents()
            self._table.setRowCount(0)
            self._summary_label.setText("No data.")
            return

        self._table.setColumnCount(len(df.columns))
        self._table.setHorizontalHeaderLabels(df.columns.tolist())
        self._table.setRowCount(len(df))

        energy_cols = {c for c in df.columns
                       if any(k in c.lower()
                              for k in ('energy', 'affinity', 'δg', 'dg', 'kcal'))}

        for row_idx, (orig_idx, row) in enumerate(df.iterrows()):
            for col_idx, col in enumerate(df.columns):
                val = row[col]
                item = QTableWidgetItem(str(val) if pd.notna(val) else "")
                item.setTextAlignment(Qt.AlignCenter)
                # Store original df row index for meta lookup
                item.setData(Qt.UserRole + 1, int(orig_idx))
                if col in energy_cols:
                    try:
                        fval = float(val)
                        if fval <= -8.0:
                            item.setForeground(QBrush(QColor("#a6e3a1")))
                        elif fval <= -6.0:
                            item.setForeground(QBrush(QColor("#fab387")))
                        else:
                            item.setForeground(QBrush(QColor("#f38ba8")))
                    except (ValueError, TypeError):
                        pass
                self._table.setItem(row_idx, col_idx, item)

        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        widest = max(range(len(df.columns)),
                     key=lambda i: max(
                         (len(str(df.iloc[r, i])) for r in range(min(len(df), 20))),
                         default=0),
                     default=1)
        self._table.horizontalHeader().setSectionResizeMode(
            widest, QHeaderView.Stretch)

        total = len(df)
        energy_col = next((c for c in df.columns if c in energy_cols), None)
        if energy_col:
            try:
                best = pd.to_numeric(df[energy_col], errors='coerce').min()
                self._summary_label.setText(
                    f"{total} row(s)  |  Best {energy_col}: {best:.2f} kcal/mol")
            except Exception:
                self._summary_label.setText(f"{total} row(s)")
        else:
            self._summary_label.setText(f"{total} row(s)")

    def _apply_filter(self):
        if self._df is None:
            return
        q = self._filter_edit.text().strip().lower()
        if not q:
            self._render_table(self._df)
            return
        mask = self._df.apply(
            lambda col: col.astype(str).str.lower().str.contains(q, na=False)
        ).any(axis=1)
        self._render_table(self._df[mask].reset_index(drop=True))

    def _apply_sort(self):
        if self._df is None:
            return
        col = self._sort_combo.currentText()
        if col not in self._df.columns:
            return
        ascending = not self._sort_dir_btn.isChecked()
        try:
            sorted_df = self._df.sort_values(
                by=col, ascending=ascending,
                key=lambda s: pd.to_numeric(s, errors='coerce')
            ).reset_index(drop=True)
        except Exception:
            sorted_df = self._df.sort_values(
                by=col, ascending=ascending).reset_index(drop=True)
        self._render_table(sorted_df)

    def _on_header_clicked(self, logical_index: int):
        if self._df is None:
            return
        col_name = self._df.columns[logical_index]
        self._sort_combo.setCurrentText(col_name)
        self._sort_dir_btn.setChecked(not self._sort_dir_btn.isChecked())

    def _on_row_selected(self):
        items = self._table.selectedItems()
        if not items:
            return
        row = items[0].row()
        first_item = self._table.item(row, 0)
        if first_item is None:
            return
        self.ligand_selected.emit(first_item.text(), self._current_csv)

    # ──────────────────────────────────────────────── Bottom tab helpers ──

    def _open_ligand_tab(self, row: int, col: int = 0):
        """Open (or focus) a detail tab for the double-clicked table row."""
        first_item = self._table.item(row, 0)
        if first_item is None:
            return

        orig_idx = first_item.data(Qt.UserRole + 1)
        row_meta = self._get_row_meta(orig_idx if orig_idx is not None else row)

        out_path  = row_meta.get('output_path', '')
        rec_pdbqt = row_meta.get('receptor_pdbqt',
                                 self._meta.get('receptor_pdbqt', ''))
        ligand_source_path = row_meta.get('ligand_pdb', '') or self._meta.get('ligand_pdb', '')
        ligand_resname = row_meta.get('ligand_resname', '') or self._meta.get('ligand_resname', '')
        if not ligand_source_path and out_path:
            tmp_root = os.path.dirname(os.path.dirname(out_path))
            candidate = os.path.join(tmp_root, 'ligand.pdb')
            if os.path.isfile(candidate):
                ligand_source_path = candidate

        # Determine label + energy from table
        label = ""
        energy = ""
        ligand_smiles = row_meta.get('smiles', '') or self._meta.get('ligand_smiles', '')
        sf = row_meta.get('sf', '')
        for col_idx in range(self._table.columnCount()):
            hdr = self._table.horizontalHeaderItem(col_idx)
            cell = self._table.item(row, col_idx)
            if hdr and cell:
                hdr_text = hdr.text().lower()
                if any(k in hdr_text for k in ('ligand', 'name', 'label')):
                    label = cell.text()
                elif "smiles" in hdr_text:
                    ligand_smiles = cell.text()
                elif any(k in hdr_text for k in ('energy', 'affinity', 'δg', 'dg')):
                    energy = cell.text()
        if not label:
            label = first_item.text()
        if not ligand_smiles and ligand_source_path:
            from PySide6.QtCore import QSettings
            settings = QSettings("LADEEP", "LADOCK")
            wsl_distro = str(settings.value("wsl_distro", "") or "").strip()
            ligand_smiles = smiles_from_structure(ligand_source_path, wsl_distro=wsl_distro)
        if not ligand_smiles:
            ccd_code = (ligand_resname or label or "").strip()
            if ccd_code and len(ccd_code) <= 4:
                ligand_smiles = smiles_from_ccd(ccd_code)

        tab_key = f"{label}::{out_path or orig_idx}"

        # If tab already open, bring to front
        if tab_key in self._tab_keys:
            idx = self._tab_keys[tab_key]
            if idx < self._tab_widget.count():
                self._tab_widget.setCurrentIndex(idx)
                return
            else:
                del self._tab_keys[tab_key]

        # Create new tab
        widget = _LigandDetailWidget(
            label=label,
            out_path=out_path,
            rec_pdbqt=rec_pdbqt,
            energy=energy,
            sf=sf,
            ligand_smiles=ligand_smiles,
            ligand_source_path=ligand_source_path,
        )
        short_label = label[:18] + "…" if len(label) > 20 else label
        if sf:
            short_label += f" [{sf}]"
        tab_idx = self._tab_widget.addTab(widget, short_label)
        self._tab_widget.setCurrentIndex(tab_idx)
        self._tab_keys[tab_key] = tab_idx

        if not out_path:
            self._tab_widget.setTabToolTip(
                tab_idx,
                "⚠ No output file path found in meta.json — "
                "run a new job to populate 3D view.")

    def _on_tab_close(self, index: int):
        """Remove tab and clean up tab_keys reference."""
        widget = self._tab_widget.widget(index)
        self._tab_widget.removeTab(index)
        if widget:
            widget.deleteLater()
        # Rebuild tab_keys map (indices may have shifted)
        self._tab_keys = {}
        for i in range(self._tab_widget.count()):
            w = self._tab_widget.widget(i)
            if w is not None:
                key = f"{w._label}::{w._out_path or ''}"
                self._tab_keys[key] = i

    def _close_all_tabs(self):
        while self._tab_widget.count():
            self._on_tab_close(0)

    # ──────────────────────────────────────────── File dialog / export ──

    def _open_csv(self):
        start_dir = self._watch_dir or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Results CSV", start_dir, "CSV Files (*.csv)")
        if path:
            self.load_csv(path)

    def _export(self):
        if self._df is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Results", "", "CSV (*.csv);;Excel (*.xlsx)")
        if not path:
            return
        try:
            if path.endswith(".xlsx"):
                self._df.to_excel(path, index=False)
            else:
                self._df.to_csv(path, index=False)
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))
