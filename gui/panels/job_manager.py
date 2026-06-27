"""
LADOCK — Job Manager Panel (PySide6)
Real-time table of all docking jobs with status, progress, and elapsed time.
"""

import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QAbstractItemView, QSplitter,
    QTextEdit, QFrame
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QColor

from core.job_scheduler import DockingJob, JobStatus
from gui.widgets.common import StatusBadge, SectionLabel
from gui import theme


# Column indices
COL_ID       = 0
COL_NAME     = 1
COL_STATUS   = 2
COL_PROGRESS = 3
COL_ELAPSED  = 4
COL_RESULT   = 5

COLUMNS = ["Job ID", "Name", "Status", "Progress", "Elapsed", "Result CSV"]


class JobManagerPanel(QWidget):
    """
    Displays all docking jobs in a table.
    Connect a JobScheduler's on_status_change to self.update_job().
    """

    job_selected = Signal(str)   # emits job_id when row is clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: dict[str, DockingJob] = {}
        self._log_buffer: dict[str, list] = {}
        self._build_ui()

        # Timer to refresh elapsed time every second
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_elapsed)
        self._timer.start(1000)

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Top bar
        top_bar = QHBoxLayout()
        top_bar.addWidget(SectionLabel("Job Manager"))
        top_bar.addStretch()
        self._clear_btn = QPushButton("Clear Finished")
        self._clear_btn.clicked.connect(self._clear_finished)
        top_bar.addWidget(self._clear_btn)
        layout.addLayout(top_bar)

        # Splitter: table top, log bottom
        splitter = QSplitter(Qt.Vertical)

        # Job table
        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(COL_NAME,   QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(COL_RESULT, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().hide()
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        splitter.addWidget(self._table)

        # Log pane
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.addWidget(QLabel("Job Log:"))
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Monospace", 9))
        self._log_view.setStyleSheet(theme.LOG_STYLE)
        log_layout.addWidget(self._log_view)
        splitter.addWidget(log_frame)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        # Summary bar
        self._summary_label = QLabel("No jobs.")
        self._summary_label.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:11px;")
        layout.addWidget(self._summary_label)

    # ------------------------------------------------------------------ #
    # Public API — called by JobScheduler callbacks
    # ------------------------------------------------------------------ #

    def update_job(self, job: DockingJob):
        """Add or update a job row. Thread-safe via Qt's signal/slot."""
        self._jobs[job.job_id] = job
        self._refresh_table()

    def append_log(self, job_id: str, message: str):
        """Append a log line for a job."""
        if job_id not in self._log_buffer:
            self._log_buffer[job_id] = []
        self._log_buffer[job_id].append(message)

        # If this job is currently selected, update log view
        selected_id = self._selected_job_id()
        if selected_id == job_id:
            self._log_view.append(message)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _refresh_table(self):
        self._table.setRowCount(len(self._jobs))
        for row, job in enumerate(self._jobs.values()):
            self._set_row(row, job)
        self._update_summary()

    def _set_row(self, row: int, job: DockingJob):
        def item(text):
            it = QTableWidgetItem(str(text))
            it.setData(Qt.UserRole, job.job_id)
            return it

        self._table.setItem(row, COL_ID,       item(job.job_id[:8]))
        self._table.setItem(row, COL_NAME,     item(job.name))
        self._table.setItem(row, COL_PROGRESS, item(f"{job.progress}%"))
        self._table.setItem(row, COL_ELAPSED,  item(job.elapsed()))
        self._table.setItem(row, COL_RESULT,   item(job.result_csv))

        # Status badge — embed as custom widget
        badge = StatusBadge(job.status.value)
        self._table.setCellWidget(row, COL_STATUS, badge)

        # Row color per status
        row_colors = {
            JobStatus.FAILED:   theme.ERROR_BG,
            JobStatus.QUEUED:   theme.WARNING_BG,
            JobStatus.RUNNING:  theme.INFO_BG,
            JobStatus.FINISHED: None,
        }
        bg = row_colors.get(job.status)
        for col in range(len(COLUMNS)):
            item_widget = self._table.item(row, col)
            if item_widget:
                if bg:
                    item_widget.setBackground(QColor(bg))
                else:
                    item_widget.setData(Qt.BackgroundRole, None)

    def _refresh_elapsed(self):
        """Update only the Elapsed column without full redraw."""
        for row, job in enumerate(self._jobs.values()):
            item = self._table.item(row, COL_ELAPSED)
            if item:
                item.setText(job.elapsed())

    def _on_row_selected(self):
        job_id = self._selected_job_id()
        if job_id:
            self.job_selected.emit(job_id)
            logs = self._log_buffer.get(job_id, [])
            self._log_view.setPlainText("\n".join(logs))
            self._log_view.verticalScrollBar().setValue(
                self._log_view.verticalScrollBar().maximum()
            )

    def _selected_job_id(self) -> str | None:
        rows = self._table.selectedItems()
        if rows:
            return rows[0].data(Qt.UserRole)
        return None

    def _clear_finished(self):
        remove = [
            jid for jid, j in self._jobs.items()
            if j.status in (JobStatus.FINISHED, JobStatus.CANCELLED)
        ]
        for jid in remove:
            del self._jobs[jid]
            self._log_buffer.pop(jid, None)
        self._refresh_table()

    def _update_summary(self):
        total    = len(self._jobs)
        queued   = sum(1 for j in self._jobs.values() if j.status == JobStatus.QUEUED)
        running  = sum(1 for j in self._jobs.values() if j.status == JobStatus.RUNNING)
        finished = sum(1 for j in self._jobs.values() if j.status == JobStatus.FINISHED)
        failed   = sum(1 for j in self._jobs.values() if j.status == JobStatus.FAILED)
        parts = [f"Total: {total}"]
        if queued:   parts.append(f"🕐 Pending: {queued}")
        if running:  parts.append(f"▶ Running: {running}")
        if finished: parts.append(f"✔ Finished: {finished}")
        if failed:   parts.append(f"✖ Failed: {failed}")
        self._summary_label.setText("  |  ".join(parts) if total else "No jobs.")
