"""
LADOCK — Reusable GUI Widgets (PySide6)
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor, QPalette
from gui import theme


# ---------------------------------------------------------------------------
# Path picker (entry + Browse button)
# ---------------------------------------------------------------------------

class PathPicker(QWidget):
    """Single-line path entry with a Browse button."""

    path_changed = Signal(str)

    def __init__(self, placeholder="", mode="file", parent=None):
        """
        mode: "file" | "dir"
        """
        super().__init__(parent)
        self._mode = mode
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.entry = QLineEdit(placeholderText=placeholder)
        self.entry.textChanged.connect(self.path_changed)
        layout.addWidget(self.entry)

        btn = QPushButton("Browse")
        btn.setFixedWidth(72)
        btn.clicked.connect(self._browse)
        layout.addWidget(btn)

    def _browse(self):
        if self._mode == "dir":
            path = QFileDialog.getExistingDirectory(self, "Select Directory")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select File")
        if path:
            self.entry.setText(path)

    def text(self) -> str:
        return self.entry.text()

    def setText(self, text: str):
        self.entry.setText(text)


# ---------------------------------------------------------------------------
# Section header label
# ---------------------------------------------------------------------------

class SectionLabel(QLabel):
    """Bold section header used inside panels."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        font = QFont()
        font.setBold(True)
        font.setPointSize(10)
        self.setFont(font)
        self.setContentsMargins(0, 10, 0, 2)


# ---------------------------------------------------------------------------
# Horizontal divider
# ---------------------------------------------------------------------------

class HDivider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Sunken)


# ---------------------------------------------------------------------------
# Collapsible card widget
# ---------------------------------------------------------------------------

class CardWidget(QWidget):
    """A bordered card with optional title."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("CardWidget")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if title:
            hdr = QLabel(f"  {title}")
            hdr.setObjectName("CardHeader")
            hdr.setFixedHeight(28)
            outer.addWidget(hdr)

        self.body = QWidget()
        self.body.setObjectName("CardBody")
        self._layout = QVBoxLayout(self.body)
        self._layout.setContentsMargins(10, 8, 10, 8)
        outer.addWidget(self.body)

    def body_layout(self) -> QVBoxLayout:
        return self._layout

    def add_widget(self, widget: QWidget):
        self._layout.addWidget(widget)

    def add_layout(self, layout):
        self._layout.addLayout(layout)


# ---------------------------------------------------------------------------
# Status badge
# ---------------------------------------------------------------------------

STATUS_COLORS = theme.STATUS_BADGE_COLORS

class StatusBadge(QLabel):
    def __init__(self, status: str = "queued", parent=None):
        super().__init__(parent)
        self.setStatus(status)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(80)

    def setStatus(self, status: str):
        color = STATUS_COLORS.get(status.lower(), "#888888")
        self.setText(status.upper())
        self.setStyleSheet(
            f"background:{color}; color:white; border-radius:3px;"
            f" padding:2px 6px; font-weight:bold; font-size:10px;"
        )
