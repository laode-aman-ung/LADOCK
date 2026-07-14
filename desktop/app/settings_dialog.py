"""
LADOCK — Settings Dialog (app/settings_dialog.py)

Persistent settings via QSettings (org=LADOCK, app=Desktop).
Covers:
  • Tool Paths (Vina, AD4, AutoGrid4, MGL, ADFR, AutoDockGPU, VinaGPU)
  • Default Docking Parameters
  • UI Preferences (theme, worker count)
"""

from __future__ import annotations

import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QFormLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QDoubleSpinBox, QComboBox, QCheckBox, QFileDialog,
    QDialogButtonBox, QGroupBox, QMessageBox, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QFont


# ---------------------------------------------------------------------------
# Small helper: path field + browse button
# ---------------------------------------------------------------------------

class _PathField(QWidget):
    def __init__(self, placeholder="", mode="file", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(placeholderText=placeholder)
        lay.addWidget(self._edit)
        btn = QPushButton("…")
        btn.setFixedWidth(28)
        btn.clicked.connect(self._browse)
        lay.addWidget(btn)
        self._mode = mode

    def _browse(self):
        if self._mode == "dir":
            p = QFileDialog.getExistingDirectory(self, "Select Directory")
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Select Executable")
        if p:
            self._edit.setText(p)

    def text(self) -> str:
        return self._edit.text().strip()

    def setText(self, v: str):
        self._edit.setText(v)


# ---------------------------------------------------------------------------
# Settings Dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):

    # Default values (also used as fallback if QSettings missing)
    DEFAULTS = {
        # Tool paths
        "vina_path":       "vina",
        "ad4_path":        "autodock4",
        "ag4_path":        "autogrid4",
        "mgl_path":        "",
        "adfr_path":       "",
        "autodockgpu":     "",
        "vinagpu":         "",
        # Docking defaults
        "exhaustiveness":  8,
        "cpu":             4,
        "n_poses":         9,
        "spacing":         0.75,
        "box_size_x":      20.0,
        "box_size_y":      20.0,
        "box_size_z":      20.0,
        "distance_flex":   4.0,
        # Job scheduler
        "max_workers":     2,
        # UI
        "theme":           "dark",
        "autosave_project": True,
        # Backend — Hybrid mode (Windows only). Off ⇒ pure-native Windows
        # (Vina/Vinardo). On ⇒ dispatch the Linux-only engines (AD4/AD-GPU +
        # AutoGrid4/MGLTools grid path) to WSL, keeping the GUI + prep native.
        "use_wsl_backend": False,
        "wsl_distro":      "",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LADOCK Settings")
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self._settings = QSettings("LADOCK", "Desktop")
        self._fields: dict[str, QWidget] = {}
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------ #
    # Build UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        lay = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._tab_tools(),    "🔧 Tool Paths")
        self._tabs.addTab(self._tab_docking(),  "⚙️ Docking Defaults")
        self._tabs.addTab(self._tab_ui(),       "🎨 UI / Scheduler")
        lay.addWidget(self._tabs)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults
        )
        btns.accepted.connect(self._save_and_accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._restore_defaults)
        lay.addWidget(btns)

    # ── Tools tab ──────────────────────────────────────────────────────
    def _tab_tools(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        grp = QGroupBox("Docking Engine Paths")
        form = QFormLayout(grp)
        tools = [
            ("vina_path",   "AutoDock Vina",    "file"),
            ("ad4_path",    "AutoDock 4",        "file"),
            ("ag4_path",    "AutoGrid 4",        "file"),
            ("mgl_path",    "MGLTools dir",      "dir"),
            ("adfr_path",   "ADFRsuite dir",     "dir"),
            ("autodockgpu", "AutoDock-GPU",      "file"),
            ("vinagpu",     "Vina-GPU",          "file"),
        ]
        for key, label, mode in tools:
            field = _PathField(mode=mode)
            self._fields[key] = field
            form.addRow(label + ":", field)
        lay.addWidget(grp)

        # Verify button
        verify_btn = QPushButton("✔ Verify All Paths")
        verify_btn.clicked.connect(self._verify_paths)
        lay.addWidget(verify_btn)
        lay.addStretch()
        return w

    # ── Docking defaults tab ───────────────────────────────────────────
    def _tab_docking(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        spin_fields = [
            ("exhaustiveness", "Exhaustiveness",    1, 64,  int,   1),
            ("cpu",            "CPU Cores",         1, 128, int,   1),
            ("n_poses",        "Number of Poses",   1, 20,  int,   1),
            ("spacing",        "Grid Spacing (Å)",  0.1, 2.0, float, 0.05),
            ("box_size_x",     "Box Size X (Å)",    5.0, 200.0, float, 1.0),
            ("box_size_y",     "Box Size Y (Å)",    5.0, 200.0, float, 1.0),
            ("box_size_z",     "Box Size Z (Å)",    5.0, 200.0, float, 1.0),
            ("distance_flex",  "Flex Residue Dist (Å)", 1.0, 10.0, float, 0.5),
        ]
        for key, label, lo, hi, typ, step in spin_fields:
            if typ == int:
                sb = QSpinBox()
                sb.setRange(lo, hi)
            else:
                sb = QDoubleSpinBox()
                sb.setRange(lo, hi)
                sb.setSingleStep(step)
                sb.setDecimals(2)
            self._fields[key] = sb
            form.addRow(label + ":", sb)

        return w

    # ── UI tab ─────────────────────────────────────────────────────────
    def _tab_ui(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        theme = QComboBox()
        theme.addItems(["dark", "light"])
        self._fields["theme"] = theme
        form.addRow("Theme:", theme)

        workers = QSpinBox()
        workers.setRange(1, 32)
        self._fields["max_workers"] = workers
        form.addRow("Max Parallel Workers:", workers)

        autosave = QCheckBox("Auto-save project on docking start")
        self._fields["autosave_project"] = autosave
        form.addRow("", autosave)

        # Hybrid mode — Windows only. Vina/Vinardo run natively; AD4/AD-GPU and
        # the AutoGrid4/MGLTools grid path are dispatched to WSL. The GUI stays
        # native Windows, so the embedded 3D preview keeps working.
        if os.name == "nt":
            use_wsl = QCheckBox(
                "Hybrid mode — run AD4 / AD-GPU via WSL (requires WSL + Ubuntu)")
            self._fields["use_wsl_backend"] = use_wsl
            form.addRow("Backend:", use_wsl)

            wsl_distro = QLineEdit()
            wsl_distro.setPlaceholderText("Optional WSL distro name (blank = default)")
            self._fields["wsl_distro"] = wsl_distro
            form.addRow("WSL distro:", wsl_distro)

        return w

    # ------------------------------------------------------------------ #
    # Load / Save
    # ------------------------------------------------------------------ #

    def _load(self):
        for key, default in self.DEFAULTS.items():
            val = self._settings.value(key, default)
            w = self._fields.get(key)
            if w is None:
                continue
            if isinstance(w, _PathField):
                w.setText(str(val))
            elif isinstance(w, QLineEdit):
                w.setText(str(val))
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                try:
                    w.setValue(float(val))
                except (TypeError, ValueError):
                    w.setValue(default)
            elif isinstance(w, QComboBox):
                idx = w.findText(str(val))
                if idx >= 0:
                    w.setCurrentIndex(idx)
            elif isinstance(w, QCheckBox):
                w.setChecked(str(val).lower() in ("true", "1", "yes"))

    def _save_and_accept(self):
        self._save()
        self.accept()

    def _save(self):
        for key, w in self._fields.items():
            if isinstance(w, _PathField):
                self._settings.setValue(key, w.text())
            elif isinstance(w, QLineEdit):
                self._settings.setValue(key, w.text().strip())
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                self._settings.setValue(key, w.value())
            elif isinstance(w, QComboBox):
                self._settings.setValue(key, w.currentText())
            elif isinstance(w, QCheckBox):
                self._settings.setValue(key, w.isChecked())
        self._settings.sync()

    def _restore_defaults(self):
        for key, default in self.DEFAULTS.items():
            w = self._fields.get(key)
            if w is None:
                continue
            if isinstance(w, _PathField):
                w.setText(str(default) if not isinstance(default, bool) else "")
            elif isinstance(w, QLineEdit):
                w.setText(str(default))
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(float(default))
            elif isinstance(w, QComboBox):
                idx = w.findText(str(default))
                if idx >= 0:
                    w.setCurrentIndex(idx)
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(default))

    # ------------------------------------------------------------------ #
    # Verify
    # ------------------------------------------------------------------ #

    def _verify_paths(self):
        results = []
        for key in ("vina_path", "ad4_path", "ag4_path",
                    "autodockgpu", "vinagpu"):
            w = self._fields.get(key)
            if not w:
                continue
            path = w.text()
            if not path:
                continue
            import shutil
            found = bool(shutil.which(path) or os.path.isfile(path))
            results.append(f"{'✅' if found else '❌'}  {key}: {path or '(empty)'}")
        for key in ("mgl_path", "adfr_path"):
            w = self._fields.get(key)
            if not w:
                continue
            path = w.text()
            if not path:
                continue
            found = os.path.isdir(path)
            results.append(f"{'✅' if found else '❌'}  {key}: {path or '(empty)'}")
        if not results:
            QMessageBox.information(self, "Verify", "No paths configured.")
        else:
            QMessageBox.information(self, "Path Verification",
                                    "\n".join(results))

    # ------------------------------------------------------------------ #
    # Class-level helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def get(cls, key: str):
        """Read a single setting value (with default fallback)."""
        s = QSettings("LADOCK", "Desktop")
        return s.value(key, cls.DEFAULTS.get(key))
