"""
LADOCK Desktop — Main Window (PySide6)

Layout:
  ┌──────────────────────────────────────────┐
  │  LADOCK Desktop         [minimize][close]│
  ├─────────┬────────────────────────────────┤
  │         │                                │
  │ Sidebar │       Content Area             │
  │         │   (stacked panels)             │
  │  • Dock │                                │
  │  • Jobs │                                │
  │  • Results                               │
  │         │                                │
  └─────────┴────────────────────────────────┘
"""

import sys
import os
from pathlib import Path
from gui import theme
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QStackedWidget, QFrame,
    QSizePolicy, QApplication, QStatusBar, QToolBar,
    QMenuBar, QMenu
)
from PySide6.QtCore import Qt, QSize, QSettings
from PySide6.QtGui import QFont, QIcon, QAction

from gui.panels.docking_prep_panel    import DockingPrepPanel
from gui.panels.native_redocking_panel import NativeRedockingPanel
from gui.panels.ligand_test_panel      import LigandTestPanel
from gui.panels.job_manager            import JobManagerPanel
from gui.panels.result_explorer        import ResultExplorerPanel
from core.job_scheduler import JobScheduler
from app.settings_dialog  import SettingsDialog
from app.about_dialog     import AboutDialog
from app.license_dialog   import LicenseDialog
from app.project_manager  import ProjectManager, WelcomeDialog
from core.license_manager import load_license, LicenseStatus, LicenseType, ACADEMIC_FREE_UNTIL
from data.project import create_legacy_job_directory
from datetime import date


# ---------------------------------------------------------------------------
# Sidebar button
# ---------------------------------------------------------------------------

class NavButton(QPushButton):
    def __init__(self, icon_text: str, label: str, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedHeight(56)
        self.setFixedWidth(110)
        self.setText(f"{icon_text}\n{label}")
        self.setFont(QFont("Sans", 9))
        self.setStyleSheet(theme.NAV_BUTTON_QSS)


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    APP_TITLE   = "LADOCK Desktop"
    WIN_WIDTH   = 1280
    WIN_HEIGHT  = 820

    def __init__(self):
        super().__init__()
        self._scheduler = JobScheduler(max_workers=2)
        self._project_mgr = ProjectManager(self)
        self._setup_scheduler_callbacks()
        self._build_ui()
        self._apply_default_job_dir()
        self._restore_geometry()
        self._check_license_on_startup()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        self.setWindowTitle(self.APP_TITLE)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(self.WIN_WIDTH, self.WIN_HEIGHT)
        self.setMinimumSize(900, 600)

        self._apply_theme()
        self._build_menubar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Sidebar ----
        sidebar = self._build_sidebar()
        root.addWidget(sidebar)

        # Vertical divider
        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setStyleSheet("color:#313244;")
        root.addWidget(divider)

        # ---- Content stack ----
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        # Create panels
        self._panel_prep      = DockingPrepPanel()          # index 0
        self._panel_redock    = NativeRedockingPanel()       # index 1
        self._panel_ligtest   = LigandTestPanel()            # index 2
        self._panel_jobs      = JobManagerPanel()            # index 3
        self._panel_results   = ResultExplorerPanel()        # index 4

        self._stack.addWidget(self._panel_prep)       # 0
        self._stack.addWidget(self._panel_redock)     # 1
        self._stack.addWidget(self._panel_ligtest)    # 2
        self._stack.addWidget(self._panel_jobs)       # 3
        self._stack.addWidget(self._panel_results)    # 4

        # Wire up signals
        # Prep panel: saved receptor/ligand → refresh redocking/ligtest lists
        self._panel_prep.file_saved.connect(self._on_prep_file_saved)
        # Prep panel: job dir set/generated → propagate to all panels
        self._panel_prep.job_dir_changed.connect(self._on_job_dir_changed)
        # Results: ligand selected → status bar
        self._panel_results.ligand_selected.connect(self._on_ligand_selected)
        # Docking panels: CSV ready → auto-load in Result Explorer + switch tab
        self._panel_redock.result_csv_ready.connect(self._on_result_csv_ready)
        self._panel_ligtest.result_csv_ready.connect(self._on_result_csv_ready)
        # Docking panels: job lifecycle → Job Manager
        for panel in (self._panel_redock, self._panel_ligtest):
            panel.job_registered.connect(self._on_job_registered)
            panel.job_log_line.connect(self._panel_jobs.append_log)
            panel.job_status_changed.connect(self._panel_jobs.update_job)
            panel.job_status_changed.connect(self._update_status_bar)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready")

        # Select first tab
        self._nav_btns[0].setChecked(True)


    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(120)
        sidebar.setStyleSheet(theme.SIDEBAR_QSS)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(5, 10, 5, 10)
        layout.setSpacing(4)

        # Logo / app name
        logo = QLabel("🧬")
        logo.setFont(QFont("Sans", 24))
        logo.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo)

        title = QLabel("LADOCK")
        title.setFont(QFont("Sans", 10, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color:{theme.ACCENT};")
        layout.addWidget(title)

        layout.addSpacing(10)

        # Navigation buttons
        nav_items = [
            ("🔧",  "Preparation"),
            ("🎯",  "Redocking"),
            ("💊",  "Lig Test"),
            ("📋",  "Jobs"),
            ("📊",  "Results"),
        ]
        self._nav_btns = []
        for i, (icon, label) in enumerate(nav_items):
            btn = NavButton(icon, label)
            btn.clicked.connect(lambda checked, idx=i: self._switch_panel(idx))
            layout.addWidget(btn)
            self._nav_btns.append(btn)

        layout.addStretch()

        # Version label
        ver = QLabel("v2.0")
        ver.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10px;")
        ver.setAlignment(Qt.AlignCenter)
        layout.addWidget(ver)

        return sidebar

    # ------------------------------------------------------------------ #
    # Panel switching
    # ------------------------------------------------------------------ #

    def _switch_panel(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == index)

    # ------------------------------------------------------------------ #
    # Scheduler wiring
    # ------------------------------------------------------------------ #

    def _setup_scheduler_callbacks(self):
        def on_status(job):
            # Must route to GUI thread — use Qt's invokeMethod pattern via lambda
            self._panel_jobs.update_job(job)
            self._update_status_bar(job)

        def on_log(job_id, msg):
            self._panel_jobs.append_log(job_id, msg)

        self._scheduler.on_status_change = on_status
        self._scheduler.on_log = on_log

    def _on_prep_file_saved(self, path: str):
        """A prepared file was saved to receptor_ready/ or ligand_ready/."""
        self._status.showMessage(f"Saved: {os.path.basename(path)}")
        # Refresh explorer lists on redocking/ligtest (job dir already set via job_dir_changed)
        job_dir = os.path.dirname(os.path.dirname(path))
        if job_dir == self._panel_redock._job_dir:
            self._panel_redock._refresh_file_list()
        if job_dir == self._panel_ligtest._job_dir:
            self._panel_ligtest._refresh_receptor_list()
            self._panel_ligtest._refresh_ligand_list()

    def _on_run_requested(self, params: dict):
        job_id = self._scheduler.submit("Docking Run", params)
        self._scheduler.start()
        self._switch_panel(3)   # Jobs panel
        self._status.showMessage(f"Job {job_id[:8]} submitted.")

    def _on_log(self, msg: str):
        self._status.showMessage(msg[:120])

    def _on_ligand_selected(self, ligand_id: str, csv_path: str):
        self._status.showMessage(f"Selected: {ligand_id}  |  {csv_path}")

    def _on_job_registered(self, job):
        """Called when a docking panel starts or queues a new run."""
        self._panel_jobs.update_job(job)
        from core.job_scheduler import JobStatus
        if job.status == JobStatus.RUNNING:
            self._status.showMessage(f"▶ Running: {job.name}  [{job.job_id}]")
            self._switch_panel(3)   # Auto-switch to Jobs tab only when running
        else:
            self._status.showMessage(
                f"🕐 Queued: {job.name}  [{job.job_id}]")

    def _on_result_csv_ready(self, csv_path: str):
        """Called when a docking panel finishes and saves a results CSV."""
        try:
            self._panel_results.load_csv(csv_path)
        except Exception as e:
            self._status.showMessage(f"Result CSV load error: {e}")
            return
        self._switch_panel(4)   # Switch to Results tab
        self._status.showMessage(f"Results loaded: {os.path.basename(csv_path)}")

    def _update_status_bar(self, job):
        from core.job_scheduler import JobStatus
        if job.status == JobStatus.RUNNING:
            self._status.showMessage(f"Running: {job.name} ({job.job_id[:8]})")
        elif job.status == JobStatus.FINISHED:
            self._status.showMessage(f"Finished: {job.name} — {job.elapsed()}")
            if job.result_csv and os.path.exists(job.result_csv):
                self._panel_results.load_csv(job.result_csv)
        elif job.status == JobStatus.FAILED:
            self._status.showMessage(f"Failed: {job.name} — {job.error[:80]}")

    # ------------------------------------------------------------------ #
    # Theme
    # ------------------------------------------------------------------ #

    def _apply_theme(self):
        self.setStyleSheet(theme.GLOBAL_QSS)

    # ------------------------------------------------------------------ #
    # Menu bar
    # ------------------------------------------------------------------ #

    def _build_menubar(self):
        mb = self.menuBar()
        mb.setStyleSheet(theme.MENUBAR_QSS)

        # ── File ────────────────────────────────────────────────────────
        file_menu = mb.addMenu("&File")
        self._add_action(file_menu, "➕  New Project",  "Ctrl+N",
                         self._project_mgr.action_new)
        self._add_action(file_menu, "📂  Open Project…", "Ctrl+O",
                         self._project_mgr.action_open)
        file_menu.addSeparator()
        self._add_action(file_menu, "💾  Save Project",  "Ctrl+S",
                         self._project_mgr.action_save)
        self._add_action(file_menu, "💾  Save Project As…", "Ctrl+Shift+S",
                         self._project_mgr.action_save_as)
        file_menu.addSeparator()
        self._add_action(file_menu, "❌  Quit",          "Ctrl+Q",
                         self.close)

        # ── View ────────────────────────────────────────────────────────
        view_menu = mb.addMenu("&View")
        panel_labels = [
            ("🔧  Preparation",    0),
            ("🎯  Redocking",      1),
            ("💊  Lig Test",       2),
            ("📋  Jobs",           3),
            ("📊  Results",        4),
        ]
        for label, idx in panel_labels:
            self._add_action(view_menu, label, "",
                             lambda checked=False, i=idx: self._switch_panel(i))
        view_menu.addSeparator()
        self._act_toggle_max = self._add_action(
            view_menu, "🗖  Maximize Window", "F10", self._toggle_maximized
        )
        self._act_toggle_fullscreen = self._add_action(
            view_menu, "⛶  Enter Full Screen", "F11", self._toggle_fullscreen
        )
        self._update_view_menu_labels()

        # ── Tools ───────────────────────────────────────────────────────
        tools_menu = mb.addMenu("&Tools")
        self._add_action(tools_menu, "⚙️  Settings…", "Ctrl+,",
                         self._open_settings)
        tools_menu.addSeparator()
        self._add_action(tools_menu, "🗑  Clear All Jobs", "",
                         self._clear_jobs)

        # ── Help ────────────────────────────────────────────────────────
        help_menu = mb.addMenu("&Help")
        self._add_action(help_menu, "🧬  About LADOCK…", "",
                         self._open_about)
        self._add_action(help_menu, "🔐  License…", "",
                         self._open_license)

        # Wire project manager
        self._project_mgr.project_loaded.connect(self._on_project_loaded)

    def _add_action(self, menu: QMenu, label: str,
                    shortcut: str, slot) -> QAction:
        act = QAction(label, self)
        if shortcut:
            act.setShortcut(shortcut)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    def _open_about(self):
        AboutDialog(self).exec()

    def _open_license(self):
        LicenseDialog(self).exec()
        self._update_license_status_bar()

    def _check_license_on_startup(self):
        from PySide6.QtCore import QTimer
        info = load_license()
        if info.status == LicenseStatus.MISSING:
            # Show license dialog but don't block — free period may have expired
            QTimer.singleShot(800, self._show_license_required)
        elif info.status == LicenseStatus.EXPIRED:
            QTimer.singleShot(800, self._show_license_expired)
        else:
            self._update_license_status_bar()

    def _show_license_required(self):
        dlg = LicenseDialog(self, require_valid=True)
        dlg.exec()
        self._update_license_status_bar()

    def _show_license_expired(self):
        dlg = LicenseDialog(self, require_valid=True)
        dlg.exec()
        self._update_license_status_bar()

    def _update_license_status_bar(self):
        info = load_license()
        if info.is_valid:
            days = info.days_remaining
            if days is not None:
                label = f"🔐 {info.type_label()}  ·  {days} days remaining"
            else:
                label = f"🔐 {info.type_label()}  ·  Perpetual"
            self._status.showMessage(label)
        else:
            self._status.showMessage("⚠️  No valid license — see Help → License")

    def _clear_jobs(self):
        self._panel_jobs._table.setRowCount(0)
        self._status.showMessage("Job list cleared.")

    def _toggle_maximized(self):
        if self.isFullScreen():
            self.showNormal()
        elif self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._update_view_menu_labels()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._update_view_menu_labels()

    def _update_view_menu_labels(self):
        if hasattr(self, "_act_toggle_max"):
            self._act_toggle_max.setText(
                "🗗  Restore Window" if self.isMaximized() and not self.isFullScreen()
                else "🗖  Maximize Window"
            )
        if hasattr(self, "_act_toggle_fullscreen"):
            self._act_toggle_fullscreen.setText(
                "⛶  Exit Full Screen" if self.isFullScreen()
                else "⛶  Enter Full Screen"
            )

    def _on_job_dir_changed(self, job_dir: str):
        """Job dir set/generated from Prep panel — propagate to Redocking & Lig Test."""
        self._panel_redock.set_job_dir(job_dir)
        self._panel_ligtest.set_job_dir(job_dir)
        # Watch job_dir/results/ for auto-detect of new CSVs
        results_dir = os.path.join(job_dir, "results")
        os.makedirs(results_dir, exist_ok=True)
        self._panel_results.set_watch_dir(results_dir)
        self._status.showMessage(f"Job dir: {job_dir}")

    def _apply_default_job_dir(self):
        env = os.environ
        release = ""
        try:
            import platform
            release = platform.release().lower()
        except Exception:
            release = ""
        is_wsl = "microsoft" in release or "wsl" in release or "WSL_DISTRO_NAME" in env
        if not is_wsl:
            return

        settings = QSettings("LADOCK", "Desktop")
        configured = str(settings.value("wsl_default_job_dir", "") or "").strip()
        if configured:
            base_dir = configured
        else:
            base_dir = str((Path.home() / "LADOCK_jobs" / "default").resolve())

        try:
            job_dir = create_legacy_job_directory(base_dir)
        except Exception:
            return
        if os.path.isdir(job_dir):
            settings.setValue("wsl_default_job_dir", base_dir)
            self._panel_prep.set_job_dir(job_dir)

    def _on_project_loaded(self, project):
        job_dir = project.root
        self.setWindowTitle(self.APP_TITLE)
        self._status.showMessage(f"Project: {project.name}  ({job_dir})")
        # Set job dir on all three panels
        self._panel_prep.set_job_dir(job_dir)
        self._panel_redock.set_job_dir(job_dir)
        self._panel_ligtest.set_job_dir(job_dir)

    # ------------------------------------------------------------------ #
    # Geometry persistence
    # ------------------------------------------------------------------ #

    def _restore_geometry(self):
        settings = QSettings("LADOCK", "Desktop")
        geom = settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)
        was_maximized = settings.value("window_maximized", False, type=bool)
        was_fullscreen = settings.value("window_fullscreen", False, type=bool)
        if was_fullscreen:
            self.showFullScreen()
        elif was_maximized:
            self.showMaximized()
        self._update_view_menu_labels()

    def closeEvent(self, event):
        settings = QSettings("LADOCK", "Desktop")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("window_maximized", self.isMaximized())
        settings.setValue("window_fullscreen", self.isFullScreen())
        self._scheduler.shutdown(wait=False)
        super().closeEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            self._update_view_menu_labels()
