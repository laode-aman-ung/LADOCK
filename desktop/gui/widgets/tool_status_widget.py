"""
gui/widgets/tool_status_widget.py — Tool detection status table widget.
Embeddable in any panel. Runs detection in background thread.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QColor


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _DetectWorker(QObject):
    finished = Signal(dict)   # key → ToolInfo

    def run(self):
        from engine.tool_detector import detect_all
        self.finished.emit(detect_all())


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class ToolStatusWidget(QWidget):
    """
    Compact table showing detection status for all external tools.
    Usage: embed anywhere, call refresh() to re-detect.
    """

    # emitted when detection completes: key → found_path or ""
    detected = Signal(dict)   # key → str path

    _COLS = ["Tool", "Binary", "Status", "Version", "Path"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: dict = {}
        self._thread: QThread | None = None
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # header row
        hdr = QHBoxLayout()
        title = QLabel("🔧 External Tool Status")
        title.setStyleSheet("color:#e6edf3;font-weight:bold;font-size:12px;")
        self._status_lbl = QLabel("Detecting…")
        self._status_lbl.setStyleSheet("color:#545d68;font-size:10px;")
        self._refresh_btn = QPushButton("⟳ Re-detect")
        self._refresh_btn.setFixedHeight(22)
        self._refresh_btn.setFixedWidth(90)
        self._refresh_btn.clicked.connect(self.refresh)
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(self._status_lbl)
        hdr.addWidget(self._refresh_btn)
        lay.addLayout(hdr)

        # table
        self._table = QTableWidget(0, len(self._COLS))
        self._table.setHorizontalHeaderLabels(self._COLS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("""
            QTableWidget {
                background:#161b22; color:#e6edf3;
                gridline-color:#2d333b; font-size:11px;
                border:1px solid #2d333b;
            }
            QTableWidget::item:alternate { background:#1c2128; }
            QHeaderView::section {
                background:#2d333b; color:#8b949e;
                font-size:10px; padding:2px 4px;
                border:none; border-bottom:1px solid #373e47;
            }
        """)
        lay.addWidget(self._table)

    # ------------------------------------------------------------------
    def refresh(self):
        if self._thread and self._thread.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._status_lbl.setText("Detecting…")
        self._table.setRowCount(0)

        self._thread = QThread(self)
        self._worker = _DetectWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    # ------------------------------------------------------------------
    def _on_done(self, results: dict):
        self._results = results
        self._table.setRowCount(len(results))

        n_ok = 0
        paths_out: dict[str, str] = {}

        for row, (key, t) in enumerate(results.items()):
            # Tool label
            self._table.setItem(row, 0, QTableWidgetItem(t.label))

            # Binary
            self._table.setItem(row, 1, QTableWidgetItem(t.binary))

            # Status badge
            if t.available:
                badge = QTableWidgetItem("✅  Found")
                badge.setForeground(QColor("#3fb950"))
                n_ok += 1
                paths_out[key] = t.found_path or t.binary
            else:
                badge = QTableWidgetItem("❌  Not found")
                badge.setForeground(QColor("#f85149"))
                paths_out[key] = ""
            self._table.setItem(row, 2, badge)

            # Version
            ver = QTableWidgetItem(t.version or "—")
            ver.setForeground(QColor("#58a6ff" if t.version else "#545d68"))
            self._table.setItem(row, 3, ver)

            # Path
            path_item = QTableWidgetItem(t.found_path or "—")
            path_item.setForeground(QColor("#e6edf3" if t.found_path else "#545d68"))
            self._table.setItem(row, 4, path_item)

        total = len(results)
        self._status_lbl.setText(f"{n_ok}/{total} tools found")
        self._status_lbl.setStyleSheet(
            f"color:{'#3fb950' if n_ok == total else '#d29922'};font-size:10px;")
        self._refresh_btn.setEnabled(True)
        self.detected.emit(paths_out)

    # ------------------------------------------------------------------
    def get_path(self, key: str) -> str:
        """Return detected path for tool key, or empty string."""
        t = self._results.get(key)
        return t.found_path or "" if t else ""
