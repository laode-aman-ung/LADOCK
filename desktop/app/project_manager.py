"""
LADOCK — Project Manager (app/project_manager.py)

Wraps data.project.LADOCKProject with GUI actions:
  • New project
  • Open project
  • Save / Save As
  • Recent projects list (via QSettings)

Emits project_loaded(LADOCKProject) when project changes.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem,
    QFileDialog, QInputDialog, QMessageBox, QDialogButtonBox
)
from PySide6.QtCore import Qt, Signal, QSettings
from PySide6.QtGui import QFont

from data.project import LADOCKProject


MAX_RECENT = 8


# ---------------------------------------------------------------------------
# Welcome / New Project dialog
# ---------------------------------------------------------------------------

class WelcomeDialog(QDialog):
    """
    Shown on first launch or File → New Project.
    User can create new project or open recent.
    """

    project_chosen = Signal(object)   # LADOCKProject

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LADOCK — Welcome")
        self.setMinimumSize(540, 400)
        self.setStyleSheet("""
            QDialog { background:#1e1e2e; color:#cdd6f4; }
            QListWidget { background:#181825; border:1px solid #45475a;
                          color:#cdd6f4; font-size:12px; }
            QListWidget::item:selected { background:#313244; color:#89b4fa; }
            QPushButton { background:#313244; border:1px solid #45475a;
                          border-radius:4px; color:#cdd6f4; padding:6px 18px; }
            QPushButton:hover { background:#45475a; }
            QLabel { color:#cdd6f4; }
        """)
        self._chosen: Optional[LADOCKProject] = None
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)

        # Logo row
        logo_row = QHBoxLayout()
        logo = QLabel("🧬")
        logo.setFont(QFont("Sans", 32))
        logo_row.addWidget(logo)
        title_col = QVBoxLayout()
        t1 = QLabel("LADOCK Desktop")
        t1.setFont(QFont("Sans", 18, QFont.Bold))
        t1.setStyleSheet("color:#89b4fa;")
        t2 = QLabel("Open-source molecular docking workstation")
        t2.setStyleSheet("color:#585b70; font-size:11px;")
        title_col.addWidget(t1)
        title_col.addWidget(t2)
        logo_row.addLayout(title_col)
        logo_row.addStretch()
        lay.addLayout(logo_row)
        lay.addSpacing(16)

        # Actions
        action_row = QHBoxLayout()
        new_btn  = QPushButton("➕  New Project")
        open_btn = QPushButton("📂  Open Project")
        new_btn.setFixedHeight(38)
        open_btn.setFixedHeight(38)
        new_btn.clicked.connect(self._new_project)
        open_btn.clicked.connect(self._open_project)
        action_row.addWidget(new_btn)
        action_row.addWidget(open_btn)
        lay.addLayout(action_row)

        lay.addSpacing(12)
        lay.addWidget(QLabel("Recent Projects:"))

        self._recent_list = QListWidget()
        self._recent_list.itemDoubleClicked.connect(self._open_recent)
        lay.addWidget(self._recent_list)
        self._populate_recent()

        lay.addSpacing(6)

        # Skip button
        skip_row = QHBoxLayout()
        skip_row.addStretch()
        skip_btn = QPushButton("Skip (no project)")
        skip_btn.setStyleSheet("color:#585b70; border:none; background:transparent;")
        skip_btn.clicked.connect(self.reject)
        skip_row.addWidget(skip_btn)
        lay.addLayout(skip_row)

    def _populate_recent(self):
        self._recent_list.clear()
        for path in _recent_projects():
            if os.path.exists(path):
                item = QListWidgetItem(f"📁  {os.path.basename(path)}  —  {path}")
                item.setData(Qt.UserRole, path)
                self._recent_list.addItem(item)
        if self._recent_list.count() == 0:
            placeholder = QListWidgetItem("(no recent projects)")
            placeholder.setForeground(Qt.gray)
            placeholder.setFlags(Qt.NoItemFlags)
            self._recent_list.addItem(placeholder)

    def _new_project(self):
        project = _create_project_interactive(self)
        if project:
            self.project_chosen.emit(project)
            self.accept()

    def _open_project(self):
        path = QFileDialog.getExistingDirectory(
            self, "Open Job / Project Directory", ""
        )
        if path:
            self._load_project_file(path)

    def _open_recent(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if path:
            self._load_project_file(path)

    def _load_project_file(self, path: str):
        try:
            # Accept both directory and project.json file
            if os.path.isfile(path):
                path = os.path.dirname(path)
            meta = os.path.join(path, "project.json")
            if os.path.isfile(meta):
                project = LADOCKProject.load(path)
            else:
                import datetime
                project = LADOCKProject(
                    name=os.path.basename(path),
                    root=path,
                    created_at=datetime.datetime.now().isoformat()
                )
            _add_recent(path)
            self.project_chosen.emit(project)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))


# ---------------------------------------------------------------------------
# Project Manager (non-modal helper class for MainWindow)
# ---------------------------------------------------------------------------

class ProjectManager(QWidget):
    """
    Manages current project state and provides menu actions.
    Attach to MainWindow and use action_* methods from menus.
    """

    project_loaded = Signal(object)   # LADOCKProject

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[LADOCKProject] = None

    @property
    def project(self) -> Optional[LADOCKProject]:
        return self._project

    def set_project(self, project: LADOCKProject):
        self._project = project
        self.project_loaded.emit(project)

    # ── Menu actions ────────────────────────────────────────────────────

    def action_new(self):
        project = _create_project_interactive(self.parent())
        if project:
            self.set_project(project)

    def action_open(self):
        path = QFileDialog.getExistingDirectory(
            self.parent(), "Open Job / Project Directory", ""
        )
        if not path:
            return
        try:
            meta = os.path.join(path, "project.json")
            if os.path.isfile(meta):
                project = LADOCKProject.load(path)
            else:
                # No project.json — treat directory as a bare job dir
                import datetime
                project = LADOCKProject(
                    name=os.path.basename(path),
                    root=path,
                    created_at=datetime.datetime.now().isoformat()
                )
            _add_recent(path)
            self.set_project(project)
        except Exception as e:
            QMessageBox.critical(self.parent(), "Load Error", str(e))

    def action_save(self):
        if self._project:
            self._project.save()

    def action_save_as(self):
        if not self._project:
            return
        path, _ = QFileDialog.getSaveFileName(
            self.parent(), "Save Project As", "project.json",
            "JSON (*.json)"
        )
        if path:
            self._project.save(path)

    def recent_paths(self) -> list[str]:
        return _recent_projects()


# ---------------------------------------------------------------------------
# Shared "New Project" flow
# ---------------------------------------------------------------------------

def _safe_folder_name(name: str) -> str:
    """Turn a project name into a filesystem-safe folder name."""
    # Drop characters that are invalid in Windows/Unix paths.
    safe = re.sub(r'[<>:"/\\|?*]', "", name).strip().rstrip(".")
    # Collapse whitespace runs to single spaces.
    safe = re.sub(r"\s+", " ", safe)
    return safe


def _create_project_interactive(parent) -> Optional[LADOCKProject]:
    """
    Ask for a project name and a parent location, then create a project in a
    subfolder named after the project. Returns the project, or None if the
    user cancelled. Shows the resolved path so it is clear where it was saved.
    """
    name, ok = QInputDialog.getText(parent, "New Project", "Project Name:")
    if not ok or not name.strip():
        return None
    name = name.strip()

    folder_name = _safe_folder_name(name)
    if not folder_name:
        QMessageBox.warning(parent, "Invalid Name",
            "The project name contains no usable characters for a folder.")
        return None

    parent_dir = QFileDialog.getExistingDirectory(
        parent,
        f"Select where to create the '{folder_name}' project folder"
    )
    if not parent_dir:
        return None

    root = os.path.join(parent_dir, folder_name)
    if os.path.isdir(root) and os.listdir(root):
        resp = QMessageBox.question(
            parent, "Folder Already Exists",
            f"This folder already exists and is not empty:\n\n{root}\n\n"
            "Create the project here anyway? Existing files are kept.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return None

    try:
        project = LADOCKProject.create(root, name)
    except Exception as e:
        QMessageBox.critical(parent, "Error", str(e))
        return None

    _add_recent(project.root)
    QMessageBox.information(
        parent, "Project Created",
        f"Project '{name}' was created at:\n\n{project.root}")
    return project


# ---------------------------------------------------------------------------
# Recent projects helpers (stored in QSettings)
# ---------------------------------------------------------------------------

def _recent_projects() -> list[str]:
    s = QSettings("LADOCK", "Desktop")
    val = s.value("recent_projects", [])
    if isinstance(val, str):
        return [val] if val else []
    return list(val) if val else []


def _add_recent(path: str):
    s = QSettings("LADOCK", "Desktop")
    val = s.value("recent_projects", [])
    if isinstance(val, str):
        recent = [val] if val else []
    else:
        recent = list(val) if val else []
    if path in recent:
        recent.remove(path)
    recent.insert(0, path)
    s.setValue("recent_projects", recent[:MAX_RECENT])
    s.sync()
