"""
LADOCK — Job Manager Panel (PySide6)
Real-time table of all docking jobs with status, progress, and elapsed time.
"""

import os
import re
import json
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
    Docking panels call update_job()/append_log() as their jobs progress.
    """

    job_selected = Signal(str)   # emits job_id when row is clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: dict[str, DockingJob] = {}
        self._log_buffer: dict[str, list] = {}
        self._log_dir: str = ""            # <job_dir>/logs — set via set_log_dir()
        self._logged_headers: set[str] = set()
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
    # Public API — called by the docking panels as jobs progress
    # ------------------------------------------------------------------ #

    def update_job(self, job: DockingJob):
        """Add or update a job row. Thread-safe via Qt's signal/slot."""
        self._jobs[job.job_id] = job
        self._refresh_table()
        self._persist_jobs()

    def append_log(self, job_id: str, message: str):
        """Append a log line for a job (kept in memory and persisted to disk)."""
        if job_id not in self._log_buffer:
            self._log_buffer[job_id] = []
        self._log_buffer[job_id].append(message)

        # Persist to <job_dir>/logs/<job_id>.log
        self._persist_log(job_id, message)

        # If this job is currently selected, update log view
        selected_id = self._selected_job_id()
        if selected_id == job_id:
            self._log_view.append(message)

    def set_log_dir(self, path: str):
        """Set the directory (usually <job_dir>/logs) where job logs are saved,
        and reload any previously persisted jobs for this project.

        Reloads only when the directory actually changes, so re-setting the
        same project mid-session never wipes a running job's live state.
        """
        new_dir = path or ""
        if new_dir:
            try:
                os.makedirs(new_dir, exist_ok=True)
            except OSError:
                new_dir = ""
        if new_dir == self._log_dir:
            return
        self._log_dir = new_dir
        self._logged_headers.clear()
        self._load_jobs()

    # ------------------------------------------------------------------ #
    # Job persistence (so jobs survive app/project close & reopen)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_id(job_id: str) -> str:
        return re.sub(r'[<>:"/\\|?*]', "", job_id) or "job"

    def _jobs_json_path(self) -> str:
        return os.path.join(self._log_dir, "jobs.json") if self._log_dir else ""

    def _persist_jobs(self):
        """Write the current jobs to <logs>/jobs.json. Never raises."""
        path = self._jobs_json_path()
        if not path:
            return
        try:
            data = [{
                'job_id': j.job_id, 'name': j.name, 'parameters': j.parameters,
                'status': j.status.value, 'progress': j.progress,
                'created_at': j.created_at, 'started_at': j.started_at,
                'finished_at': j.finished_at, 'error': j.error,
                'result_csv': j.result_csv,
            } for j in self._jobs.values()]
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except (OSError, TypeError):
            pass

    def _load_jobs(self):
        """Replace the table with jobs persisted for the current project."""
        self._jobs.clear()
        self._log_buffer.clear()
        path = self._jobs_json_path()
        if path and os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = []
            for d in data:
                job = self._dict_to_job(d)
                if job is None:
                    continue
                self._jobs[job.job_id] = job
                # Don't re-write a header if this job ever logs again.
                self._logged_headers.add(job.job_id)
                # Preload the persisted log so clicking the job shows history.
                logp = os.path.join(self._log_dir, f"{self._safe_id(job.job_id)}.log")
                if os.path.isfile(logp):
                    try:
                        self._log_buffer[job.job_id] = \
                            open(logp, encoding='utf-8', errors='replace').read().splitlines()
                    except OSError:
                        pass
        self._refresh_table()

    def _dict_to_job(self, d: dict):
        try:
            status = JobStatus(d.get('status', 'finished'))
        except ValueError:
            status = JobStatus.FAILED
        error = d.get('error', '')
        # A job left RUNNING/QUEUED means the app closed mid-run — it can no
        # longer progress, so surface it as interrupted rather than "running".
        if status in (JobStatus.RUNNING, JobStatus.QUEUED):
            status = JobStatus.FAILED
            error = error or "Interrupted (application closed)"
        try:
            return DockingJob(
                job_id=d['job_id'], name=d.get('name', ''),
                parameters=d.get('parameters', {}) or {},
                status=status, progress=d.get('progress', 0),
                created_at=d.get('created_at', ''), started_at=d.get('started_at', ''),
                finished_at=d.get('finished_at', ''), error=error,
                result_csv=d.get('result_csv', ''))
        except KeyError:
            return None

    def _persist_log(self, job_id: str, message: str):
        """Append one log line to the job's log file. Never raises."""
        if not self._log_dir:
            return
        safe_id = re.sub(r'[<>:"/\\|?*]', "", job_id) or "job"
        path = os.path.join(self._log_dir, f"{safe_id}.log")
        try:
            with open(path, "a", encoding="utf-8") as f:
                if job_id not in self._logged_headers:
                    self._logged_headers.add(job_id)
                    job = self._jobs.get(job_id)
                    name = job.name if job else job_id
                    f.write(
                        f"# LADOCK job log\n"
                        f"# Job:     {name} ({job_id})\n"
                        f"# Started: {datetime.datetime.now().isoformat(timespec='seconds')}\n\n"
                    )
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                f.write(f"[{ts}] {message}\n")
        except OSError:
            # Logging must never break the docking run or the UI.
            pass

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
        self._persist_jobs()

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
