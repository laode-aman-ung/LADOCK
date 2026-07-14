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


# ---------------------------------------------------------------------------
# Docking-parameter tooltips (shared by Redocking and Lig Test panels)
# ---------------------------------------------------------------------------

DOCKING_TOOLTIPS: dict[str, str] = {
    # Scoring functions
    "_sf_vina":    "AutoDock Vina — fast, general-purpose scoring. Native on all platforms.",
    "_sf_vinardo": "Vinardo scoring (Vina engine). Often better for hydrophobic pockets.",
    "_sf_ad4":     "AutoDock4 (AutoGrid4 grid). Linux-only; also needs MGLTools.",
    "_sf_ad4gpu":  "AutoDock-GPU. Linux + NVIDIA CUDA. Fastest for large screens.",
    # Box center / size
    "_cxx": "Grid box center X (Å). Auto-filled and locked unless Box Center = Custom.",
    "_cxy": "Grid box center Y (Å). Auto-filled and locked unless Box Center = Custom.",
    "_cxz": "Grid box center Z (Å). Auto-filled and locked unless Box Center = Custom.",
    "_sxx": "Grid box size X (Å). Must enclose the binding site (typically 18–25 Å).",
    "_sxy": "Grid box size Y (Å). Must enclose the binding site (typically 18–25 Å).",
    "_sxz": "Grid box size Z (Å). Must enclose the binding site (typically 18–25 Å).",
    "_spacing": "Grid point spacing in Å (AutoDock4/AutoGrid4). Default 0.375.",
    # Mode / flexibility
    "_mode_rigid":    "Keep the receptor rigid (fastest).",
    "_mode_flexible": "Allow selected side chains to move. Supported by all engines.",
    "_flex_dist":     "Residues with any atom within this distance (Å, max 10) of the box "
                      "center become flexible.",
    "_flex_residues": "Auto-filled from Flex Distance. Edit to override "
                      "(format chain:RES:num, joined by '_').",
    # MLSD
    "_elements": "Dock this many ligands together in one pocket (MLSD). Vina / Vinardo only.",
    "_arr_type": "combination = unordered groups; permutation = ordered (more combos, slower).",
    # Search settings
    "_n_poses":        "Number of output poses per ligand.",
    "_exhaustiveness": "Vina search thoroughness. Higher = more accurate, slower (default 8).",
    "_ad4_exhaustiveness": "AutoDock4 search effort (scales ga_num_evals). Higher = slower.",
    "_energy_range":   "Keep poses within this energy (kcal/mol) of the best pose.",
    "_cpu":            "CPU cores per docking job (Vina/Vinardo/AD4). Not used by AD4-GPU.",
    "_max_workers":    "Number of ligands docked in parallel.",
    "_seed":           "Random seed (0 = random). Set a fixed value for reproducible runs.",
    "_ga_pop_size":    "AutoDock4 genetic-algorithm population size (default 150).",
    "_cluster_rmsd":   "AutoDock4 clustering RMSD tolerance (Å).",
}

_CENTER_MODE_TOOLTIPS = {
    "ligand":  "Center the grid on the selected native ligand (best for redocking / known site).",
    "protein": "Center on the whole-protein centroid (blind docking).",
    "custom":  "Enter grid center coordinates manually.",
}


def apply_docking_tooltips(panel) -> None:
    """Attach explanatory tooltips to a panel's docking-parameter controls.

    Controls are looked up by attribute name, so a panel that lacks one simply
    skips it. Box-center radio buttons are resolved through ``_center_grp``."""
    for attr, tip in DOCKING_TOOLTIPS.items():
        widget = getattr(panel, attr, None)
        if widget is not None:
            widget.setToolTip(tip)
    grp = getattr(panel, "_center_grp", None)
    if grp is not None:
        for btn in grp.buttons():
            tip = _CENTER_MODE_TOOLTIPS.get(btn.property("mode"))
            if tip:
                btn.setToolTip(tip)
