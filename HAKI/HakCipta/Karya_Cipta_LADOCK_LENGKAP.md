**DESKRIPSI CIPTAAN (KARYA CIPTA)**

| Field | Isian |
|-------|-------|
| Judul Ciptaan | LADOCK — Molecular Docking Workstation |
| Jenis Ciptaan | Program Komputer |
| Pencipta | Dr. La Ode Aman, M.Si |
| Pemegang Hak Cipta | Universitas Negeri Gorontalo |
| Tanggal & tempat pertama diumumkan | 2024, Gorontalo, Indonesia |

## Deskripsi Ciptaan

**LADOCK** adalah program komputer berupa aplikasi desktop **stasiun kerja penambatan molekuler (molecular docking workstation)** dengan antarmuka grafis modern (PySide6/Qt, tema gelap Catppuccin). LADOCK mengintegrasikan beberapa mesin penambatan molekuler ke dalam satu alur kerja terpadu, dilengkapi penjadwal pekerjaan (job scheduler) untuk penambatan sejumlah besar ligan secara paralel, manajemen pustaka ligan, visualisasi molekul tiga dimensi, analisis interaksi non-kovalen, serta manajemen proyek. LADOCK dapat berjalan lintas platform (Windows, Linux/macOS) dan mendukung eksekusi biner Linux dari Windows melalui backend WSL.

### Modul fungsional utama
1. **Multi-engine Docking** — mengorkestrasi beberapa mesin penambatan: AutoDock Vina, AutoDock 4, VinaGPU, dan AutoDock-GPU.
2. **Batch Docking** — penjadwalan dan eksekusi banyak ligan secara paralel melalui job scheduler bawaan.
3. **Manajemen Pustaka Ligan** — impor dari CSV, SDF, atau PDBQT; perenderan struktur dari SMILES melalui RDKit.
4. **Persiapan Molekul** — penyiapan reseptor & ligan serta deteksi otomatis perkakas eksternal (tool detector).
5. **Viewer 3D Interaktif** — visualisasi molekul berbasis 3Dmol.js.
6. **Analisis Interaksi Non-kovalen** — deteksi ikatan hidrogen, π-stacking, kontak hidrofobik, dan interaksi lain.
7. **Result Explorer** — tabel hasil energi ikatan yang dapat diurutkan.
8. **Manajemen Proyek** — simpan/muat proyek penambatan dengan struktur direktori pekerjaan yang terorganisir.
9. **Backend WSL** — menjalankan biner penambatan Linux dari lingkungan Windows.
10. **Manajemen Lisensi** — pengelolaan lisensi akademik/komersial di dalam aplikasi.

### Fitur pendukung
- Antarmuka grafis multi-panel (persiapan docking, pengelola job, penjelajah hasil, pengaturan).
- Deteksi otomatis lokasi perkakas eksternal dan pengaturan Tool Paths.
- Pengelola berkas dan direktori proyek terstruktur.
- Perkakas penambatan yang dibundel (tidak perlu instalasi terpisah).

## Spesifikasi Teknis

| Aspek | Keterangan |
|-------|-----------|
| Bahasa pemrograman | Python (≥ 3.10) |
| Antarmuka grafis | PySide6 (Qt), tema Catppuccin |
| Pustaka utama | NumPy, SciPy, pandas, RDKit (opsional), 3Dmol.js |
| Perkakas penambatan (dibundel) | AutoDock Vina 1.2.7, AutoDock 4, ADFRsuite 1.0, MGLTools, OpenBabel |
| Platform | Windows, Linux, macOS; dukungan WSL |
| Ukuran kode sumber orisinal | ± 19.500 baris (di luar biner & pustaka pihak ketiga) |

**Rincian volume kode sumber (di luar pustaka/biner pihak ketiga):**

| Komponen | Jumlah berkas | Perkiraan baris |
|----------|---------------|-----------------|
| `app/` (lapisan aplikasi) | ± 6 berkas `.py` | ± 1.680 |
| `core/` (utilitas inti) | ± 10 berkas `.py` | ± 2.270 |
| `data/` (model data) | ± 4 berkas `.py` | ± 540 |
| `engine/` (mesin docking & analisis) | ± 5 berkas `.py` | ± 1.930 |
| `gui/` (antarmuka & panel) | ± 18 berkas `.py` | ± 12.250 |
| root & tools (`main.py`, `docking.py`, dll.) | ± 5 berkas `.py` | ± 830 |
| **Total** | **± 48 berkas** | **± 19.500** |

## Daftar Berkas Kode Sumber (Inventaris Ciptaan)

**Root:** `main.py`, `docking.py`, `ladock_entry.py`

**app/:** `main_window.py`, `project_manager.py`, `settings_dialog.py`, `license_dialog.py`, `about_dialog.py`

**core/:** `job_scheduler.py`, `task_manager.py`, `wsl_backend.py`, `tool_paths.py`, `license_manager.py`, `ligand_importer.py`, `ligand_smiles.py`, `render_smiles_svg.py`, `file_manager.py`

**data/:** `project.py`, `ligand_library.py`, `result_parser.py`

**engine/:** `docking_engine.py`, `interaction_analyzer.py`, `mol_prep.py`, `tool_detector.py`

**gui/:** tema dan panel-panel antarmuka (`panels/` — persiapan docking, pengelola job, penjelajah hasil, dll.)

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

**LAMPIRAN CONTOH CIPTAAN — KODE SUMBER**

Judul Ciptaan: **LADOCK — Molecular Docking Workstation**

Jenis Ciptaan: **Program Komputer** — Pencipta: **Dr. La Ode Aman, M.Si** — Pemegang Hak Cipta: **Universitas Negeri Gorontalo**

Dokumen ini memuat cuplikan representatif kode sumber orisinal LADOCK (di luar biner & pustaka pihak ketiga) sebagai contoh ciptaan.

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## main.py

*(total 88 baris)*

~~~~python
#!/usr/bin/env python3
"""
LADOCK Desktop — Entry Point
Run with: ~/miniconda3/bin/python main.py
"""

import sys
import os
import platform


def _configure_qt_runtime():
    """Configure Qt runtime flags for Linux/WSL compatibility."""
    if not sys.platform.startswith("linux"):
        return

    env = os.environ
    release = platform.release().lower()
    is_wsl = "microsoft" in release or "wsl" in release or "WSL_DISTRO_NAME" in env

    # Always disable sandbox for QtWebEngine (required in many containerised/WSL envs)
    env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    if is_wsl:
        # WSL: no real GPU — force software rendering and XCB platform.
        # WSLg advertises Wayland even when EGL is unavailable for Qt WebEngine.
        env.setdefault("QT_OPENGL", "software")
        env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        env.setdefault("QT_QUICK_BACKEND", "software")
        env.setdefault("QT_QPA_PLATFORM", "xcb")

    chromium_flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    extra_flags = ["--enable-webgl", "--ignore-gpu-blocklist"]
    if is_wsl:
        extra_flags.extend(
            [
                "--use-angle=swiftshader",
                "--disable-features=Vulkan",
                "--disable-gpu-compositing",
            ]
        )
    for flag in extra_flags:
        if flag not in chromium_flags:
            chromium_flags = f"{chromium_flags} {flag}".strip()
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = chromium_flags


_configure_qt_runtime()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSettings
from app.main_window import MainWindow
from app.project_manager import WelcomeDialog


def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # Software OpenGL only when explicitly requested (e.g. set by _configure_qt_runtime for WSL)
    if os.environ.get("QT_OPENGL") == "software":
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("LADOCK Desktop")
    app.setOrganizationName("LADOCK")

    window = MainWindow()
    window.show()

    # Show welcome dialog on first launch or if no recent projects
    settings = QSettings("LADOCK", "Desktop")
    show_welcome = settings.value("show_welcome", True)
    if show_welcome:
        dlg = WelcomeDialog(window)
        dlg.project_chosen.connect(window._project_mgr.set_project)
        dlg.exec()
        settings.setValue("show_welcome", False)
        settings.sync()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## docking.py

*(total 648 baris; ditampilkan 120 baris pertama)*

~~~~python
import rdkit
from rdkit import Chem
from rdkit.Chem import AllChem
import pandas as pd
import subprocess
import os
import glob
import shutil
from tqdm import tqdm
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
import itertools
import numpy as np
import argparse
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
from tabulate import tabulate
from os.path import basename
from ladeep.utility import extract_gz, download_file_with_retry, is_smiles_valid, vina_energy, mol_opt, adfr_energy, get_residues_within_distance, run_command, extract_molecule, calculate_molecule_center, move_molecule_to_target_center, processing_ligand, process_smi, process_sdf, developer_note, developer_contact, citation_list, print_dev, delete_files_except_pdb, pdb_to_smiles

def read_config_from_file(filename):
    config = {}
    with open(filename, 'r') as file:
        for line in file:
            key, value = map(str.strip, line.split(':'))
            config[key] = value
    return config


def write_molecule_to_pdb(startsWith, atom_coordinates, endsWith, output_path):
    with open(output_path, 'w') as file:
        for start, (x, y, z), end in zip(startsWith, atom_coordinates, endsWith):
            # Format koordinat atom ke dalam format PDB
            line = f"{start}    {x:8.3f}{y:8.3f}{z:8.3f}  {end}\n"
            file.write(line)

def create_vina_config(ligand_pdb, box_size):
    startsWith, atom_coordinates, endsWith = extract_molecule(ligand_pdb)    
    center = calculate_molecule_center(atom_coordinates)
    center_x, center_y, center_z = center
    config_file='config.txt'
    with open(config_file, 'w') as config_file:
        config_file.write(f"size_x = {box_size[0]}\n")
        config_file.write(f"size_y = {box_size[1]}\n")
        config_file.write(f"size_z = {box_size[2]}\n")
        config_file.write(f"center_x = {center_x:.3f}\n")
        config_file.write(f"center_y = {center_y:.3f}\n")
        config_file.write(f"center_z = {center_z:.3f}\n")
        config_file.write("# Script written by:\n")
        config_file.write("# La Ode Aman\n")
        config_file.write("# laodeaman.ai@gmail.com\n")
        config_file.write("# laode_aman@ung.ac.id\n")
        config_file.write("# Universitas Negeri Gorontalo, Indonesia\n")

    print("  Docking parameter:")
    print("\tsize_x =", box_size[0])
    print("\tsize_y =", box_size[1])
    print("\tsize_z =", box_size[2])
    print(f"\tcenter_x = {center_x:.3f}")
    print(f"\tcenter_y = {center_y:.3f}")
    print(f"\tcenter_z = {center_z:.3f}")
    
def generate_ligand_pdbqt(prepare_ligand, smiles, reference_center):
    try:
        ligand_name = smiles[0]
        smi = smiles[1]
        activity = smiles[2] if len(smiles) >= 3 and smiles[2] else 'NaN'
        others = smiles[3:] if len(smiles) > 3 else None

        if os.path.exists(os.path.join('.', f'{ligand_name}.pdbqt')):
            return smi, ligand_name, activity, others
        else:
            ligand_pdb = f'{ligand_name}.pdb'

            if not os.path.exists(os.path.join('.', ligand_pdb)):
                mol = mol_opt(smi)
                if mol is not None:
                    mol_block = Chem.MolToPDBBlock(mol)
                    with open(ligand_pdb, 'w') as pdb_file:
                        pdb_file.write(mol_block)
                else:
                    # Handle the case where mol is None
                    return None

            startsWith, atom_coordinates, endsWith = extract_molecule(ligand_pdb)
            center = calculate_molecule_center(atom_coordinates)

            # Move the ligand to the target box center
            moved_atom_coordinates = move_molecule_to_target_center(atom_coordinates, reference_center)

            # Write the moved ligand to a new PDB file
            moved_ligand_pdb_path = f'{ligand_name}_tmp.pdb'
            write_molecule_to_pdb(startsWith, moved_atom_coordinates, endsWith, moved_ligand_pdb_path)
            ligand_pdbqt = f'{ligand_name}.pdbqt'
            run_command(f'{prepare_ligand} -l {moved_ligand_pdb_path} -o {ligand_pdbqt}')
            os.remove(ligand_pdb)
            os.rename(moved_ligand_pdb_path, f'{ligand_name}.pdb')

            return smi, ligand_name, activity, others

    except Exception as e:
        #print(f"An error occurred: {e}")
        return None

def docking_reference(output_model_dir, result_text, agfr, adfr, prepare_gpf, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, csv_result, receptor_pdb, reference_pdb, flexible_residues, vina_path, ad4_path, ag4_path):
    # Center box target is the center box of reference
       
    startsWithR, atom_coordinatesR, endsWithR = extract_molecule(reference_pdb)
    reference_center = calculate_molecule_center(atom_coordinatesR)

    # Preparation
    npts = ",".join(map(str, box_size))
    receptor_name = os.path.basename(receptor_pdb).split('.')[0]
    reference_name = os.path.basename(reference_pdb).split('.')[0]
    reference_pdbqt = f'{reference_name}.pdbqt'
    combine_ref = receptor_name
    combine_ref_pdbqt = f'{combine_ref}.pdbqt'
    run_command(f'{prepare_ligand} -l {reference_pdb} -o {reference_pdbqt}')
    run_command(f'{prepare_receptor} -r {receptor_pdb} -A hydrogens -o {combine_ref_pdbqt}')
~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## app/main_window.py

*(total 509 baris; ditampilkan 120 baris pertama)*

~~~~python
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

~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## app/project_manager.py

*(total 281 baris; ditampilkan 100 baris pertama)*

~~~~python
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
~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## core/job_scheduler.py

*(total 158 baris; ditampilkan 140 baris pertama)*

~~~~python
"""
LADOCK Job Scheduler
Manages a queue of docking jobs with configurable parallel execution.
Emits status signals compatible with both Qt (via callback) and CLI.
"""

import os
import uuid
import threading
import datetime
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class JobStatus(Enum):
    QUEUED   = "queued"
    RUNNING  = "running"
    FINISHED = "finished"
    FAILED   = "failed"
    CANCELLED = "cancelled"


@dataclass
class DockingJob:
    job_id: str
    name: str
    parameters: dict
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0          # 0-100
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    result_csv: str = ""

    def elapsed(self) -> str:
        if not self.started_at:
            return "-"
        start = datetime.datetime.fromisoformat(self.started_at)
        if self.finished_at:
            end = datetime.datetime.fromisoformat(self.finished_at)
        else:
            end = datetime.datetime.now()
        delta = end - start
        m, s = divmod(int(delta.total_seconds()), 60)
        return f"{m}m {s}s"


class JobScheduler:
    """
    Thread-safe job queue.

    Usage:
        scheduler = JobScheduler(max_workers=2)
        scheduler.on_status_change = my_callback   # optional
        job_id = scheduler.submit(params)
        scheduler.start()
    """

    def __init__(self, max_workers: int = 1):
        self.max_workers = max_workers
        self._jobs: Dict[str, DockingJob] = {}
        self._lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: Dict[str, Future] = {}

        # Callbacks — set these from the GUI
        self.on_status_change: Optional[Callable[[DockingJob], None]] = None
        self.on_log: Optional[Callable[[str, str], None]] = None  # (job_id, message)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def submit(self, name: str, parameters: dict) -> str:
        """Add a job to the queue and return its job_id."""
        job_id = uuid.uuid4().hex[:8]
        job = DockingJob(
            job_id=job_id,
            name=name,
            parameters=parameters,
            created_at=datetime.datetime.now().isoformat()
        )
        with self._lock:
            self._jobs[job_id] = job
        self._notify(job)
        return job_id

    def start(self):
        """Start processing queued jobs."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        with self._lock:
            pending = [j for j in self._jobs.values() if j.status == JobStatus.QUEUED]
        for job in pending:
            future = self._executor.submit(self._run_job, job.job_id)
            self._futures[job.job_id] = future

    def cancel(self, job_id: str):
        """Cancel a queued job (running jobs cannot be interrupted yet)."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job and job.status == JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            self._notify(job)

    def get_job(self, job_id: str) -> Optional[DockingJob]:
        return self._jobs.get(job_id)

    def all_jobs(self) -> List[DockingJob]:
        return list(self._jobs.values())

    def shutdown(self, wait: bool = True):
        if self._executor:
            self._executor.shutdown(wait=wait)
            self._executor = None

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _run_job(self, job_id: str):
        with self._lock:
            job = self._jobs[job_id]
            if job.status != JobStatus.QUEUED:
                return
            job.status = JobStatus.RUNNING
            job.started_at = datetime.datetime.now().isoformat()
        self._notify(job)

        try:
            from engine.docking_engine import run_docking

            def log_cb(msg):
                if self.on_log:
                    self.on_log(job_id, msg)

            run_docking(log_callback=log_cb, **job.parameters)
~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## core/wsl_backend.py

*(total 120 baris)*

~~~~python
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess


_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def is_windows_host() -> bool:
    return os.name == "nt"


def wsl_executable() -> str:
    return shutil.which("wsl.exe") or shutil.which("wsl") or "wsl.exe"


def wsl_available() -> bool:
    return bool(shutil.which("wsl.exe") or shutil.which("wsl"))


def windows_to_wsl_path(path: str) -> str:
    if not path:
        return path
    norm = os.path.normpath(path)
    if not _WIN_DRIVE_RE.match(norm):
        return norm.replace("\\", "/")
    drive = norm[0].lower()
    tail = norm[2:].replace("\\", "/")
    return f"/mnt/{drive}{tail}"


def maybe_to_wsl_path(arg: str) -> str:
    if not isinstance(arg, str):
        return str(arg)
    if _WIN_DRIVE_RE.match(arg):
        return windows_to_wsl_path(arg)
    return arg


def prepare_subprocess(
    cmd: list[str],
    cwd: str | None = None,
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
) -> tuple[list[str], str | None]:
    normalized = [str(part) for part in cmd]
    if not (use_wsl_backend and is_windows_host()):
        return normalized, cwd

    linux_cmd = [maybe_to_wsl_path(part) for part in normalized]
    script = " ".join(shlex.quote(part) for part in linux_cmd)

    if cwd:
        linux_cwd = windows_to_wsl_path(cwd)
        script = f"cd {shlex.quote(linux_cwd)} && {script}"

    wrapped = [wsl_executable()]
    distro = (wsl_distro or "").strip()
    if distro:
        wrapped += ["-d", distro]
    wrapped += ["bash", "-lc", script]
    return wrapped, None


def command_exists(cmd: str, use_wsl_backend: bool = False, wsl_distro: str = "") -> bool:
    if not cmd:
        return False
    if use_wsl_backend and is_windows_host():
        exec_cmd = [wsl_executable()]
        distro = (wsl_distro or "").strip()
        if distro:
            exec_cmd += ["-d", distro]
        exec_cmd += ["bash", "-lc", f"command -v {shlex.quote(cmd)}"]
        result = subprocess.run(
            exec_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    return bool(shutil.which(cmd))


def resolve_wsl_python(wsl_distro: str = "") -> str:
    if not is_windows_host():
        return ""
    exec_cmd = [wsl_executable()]
    distro = (wsl_distro or "").strip()
    if distro:
        exec_cmd += ["-d", distro]
    script = (
        'if [ -x "$HOME/miniconda3/bin/python" ]; then '
        'printf "%s" "$HOME/miniconda3/bin/python"; '
        'elif [ -x "$HOME/anaconda3/bin/python" ]; then '
        'printf "%s" "$HOME/anaconda3/bin/python"; '
        'elif command -v python3 >/dev/null 2>&1; then '
        'command -v python3; '
        'elif command -v python >/dev/null 2>&1; then '
        'command -v python; '
        'fi'
    )
    try:
        result = subprocess.run(
            exec_cmd + ["bash", "-lc", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()

~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## core/license_manager.py

*(total 212 baris; ditampilkan 120 baris pertama)*

~~~~python
"""
LADOCK License Manager (core/license_manager.py)

Offline license system — license keys are generated by the owner
and sent to users via email after verification.

License types:
  ACADEMIC_FREE     — free until 2027-12-31, verified institutional email
  ACADEMIC_DISCOUNT — discounted, post-2027, verified institutional email
  COMMERCIAL        — paid, for-profit use

Key format:
  LADOCK-<BASE64_PAYLOAD>.<HMAC_SIGNATURE>

Payload (JSON):
  {
    "type":    "ACADEMIC_FREE" | "ACADEMIC_DISCOUNT" | "COMMERCIAL",
    "name":    "Recipient name or institution",
    "email":   "user@university.ac.id",
    "issued":  "YYYY-MM-DD",
    "expires": "YYYY-MM-DD" | null   (null = perpetual commercial)
  }
"""

import json
import hmac
import hashlib
import base64
import os
from datetime import date, datetime
from pathlib import Path
from enum import Enum

# ── Secret key (embedded, offline validation) ────────────────────────────────
# Keep this consistent across all LADOCK releases.
_SECRET = b"LADOCK-La-Ode-Aman-2024-UNG-acId-$3cur3"

# ── License storage path ─────────────────────────────────────────────────────
_LICENSE_DIR  = Path.home() / ".ladock"
_LICENSE_FILE = _LICENSE_DIR / "license.key"

# ── Academic free period ─────────────────────────────────────────────────────
ACADEMIC_FREE_UNTIL = date(2030, 12, 31)


class LicenseType(str, Enum):
    ACADEMIC_FREE     = "ACADEMIC_FREE"
    ACADEMIC_DISCOUNT = "ACADEMIC_DISCOUNT"
    COMMERCIAL        = "COMMERCIAL"
    UNLICENSED        = "UNLICENSED"


class LicenseStatus(str, Enum):
    VALID   = "VALID"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
    MISSING = "MISSING"


class LicenseInfo:
    def __init__(self, *, type: LicenseType, status: LicenseStatus,
                 name: str = "", email: str = "",
                 issued: str = "", expires: str | None = None,
                 message: str = ""):
        self.type    = type
        self.status  = status
        self.name    = name
        self.email   = email
        self.issued  = issued
        self.expires = expires
        self.message = message

    @property
    def is_valid(self) -> bool:
        return self.status == LicenseStatus.VALID

    @property
    def expires_date(self) -> date | None:
        if self.expires:
            return date.fromisoformat(self.expires)
        return None

    @property
    def days_remaining(self) -> int | None:
        d = self.expires_date
        if d is None:
            return None
        return (d - date.today()).days

    def type_label(self) -> str:
        labels = {
            LicenseType.ACADEMIC_FREE:     "Academic Free",
            LicenseType.ACADEMIC_DISCOUNT: "Academic Discount",
            LicenseType.COMMERCIAL:        "Commercial",
            LicenseType.UNLICENSED:        "Unlicensed",
        }
        return labels.get(self.type, self.type)


# ── Core functions ────────────────────────────────────────────────────────────

def _sign(payload_b64: str) -> str:
    sig = hmac.new(_SECRET, payload_b64.encode(), hashlib.sha256).hexdigest()
    return sig[:32]  # 32 hex chars — compact but sufficient


def generate_key(license_type: str, name: str, email: str,
                 expires: str | None = None) -> str:
    """
    Generate a license key. Called by the owner tool (tools/generate_license.py).
    expires: "YYYY-MM-DD" or None (perpetual, for commercial)
    """
    payload = {
        "type":    license_type,
        "name":    name,
        "email":   email,
        "issued":  date.today().isoformat(),
        "expires": expires,
    }
    payload_json  = json.dumps(payload, separators=(",", ":"))
~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## engine/docking_engine.py

*(total 718 baris; ditampilkan 150 baris pertama)*

~~~~python
"""
LADOCK Docking Engine
Refactored from docking.py — GUI-agnostic, uses callback for output.

Instead of writing directly to a tkinter Text widget, all output goes
through an optional `log_callback(message: str)` function.
"""

import os
import glob
import shutil
import itertools

import numpy as np
import pandas as pd
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from os.path import basename

try:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

try:
    from ladeep.utility import (
        extract_gz, download_file_with_retry, is_smiles_valid,
        vina_energy, mol_opt, adfr_energy, get_residues_within_distance,
        run_command, extract_molecule, calculate_molecule_center,
        move_molecule_to_target_center, processing_ligand, process_smi,
        process_sdf, developer_note, developer_contact, citation_list,
        print_dev, delete_files_except_pdb, pdb_to_smiles
    )
    LADEEP_AVAILABLE = True
except ImportError:
    LADEEP_AVAILABLE = False


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _log(callback, message: str):
    """Send message to callback if provided, else print."""
    if callback is not None:
        callback(message)
    else:
        print(message)


def adgpu_energy(dlg_path: str):
    """Parse best Free Energy of Binding from AutoDock-GPU .dlg output.

    Returns the FEB (kcal/mol) of cluster rank 1, or None if not found.
    DLG RANKING line format:
      <ClusterRank> <RunRank> <Run> <FEB> <RMSD1> <RMSD2>  RANKING
    """
    if not os.path.isfile(dlg_path):
        return None
    try:
        with open(dlg_path) as f:
            for line in f:
                if 'RANKING' in line:
                    parts = line.split()
                    if len(parts) >= 4 and parts[0] == '1':
                        return float(parts[3])
    except (OSError, ValueError):
        pass
    return None


def write_molecule_to_pdb(startsWith, atom_coordinates, endsWith, output_path):
    with open(output_path, 'w') as file:
        for start, (x, y, z), end in zip(startsWith, atom_coordinates, endsWith):
            line = f"{start}    {x:8.3f}{y:8.3f}{z:8.3f}  {end}\n"
            file.write(line)


def create_vina_config(ligand_pdb, box_size):
    startsWith, atom_coordinates, endsWith = extract_molecule(ligand_pdb)
    center = calculate_molecule_center(atom_coordinates)
    center_x, center_y, center_z = center
    config_file = 'config.txt'
    with open(config_file, 'w') as f:
        f.write(f"size_x = {box_size[0]}\n")
        f.write(f"size_y = {box_size[1]}\n")
        f.write(f"size_z = {box_size[2]}\n")
        f.write(f"center_x = {center_x:.3f}\n")
        f.write(f"center_y = {center_y:.3f}\n")
        f.write(f"center_z = {center_z:.3f}\n")
        f.write("# Script written by LADOCK\n")

    return center_x, center_y, center_z


# ---------------------------------------------------------------------------
# Ligand preparation
# ---------------------------------------------------------------------------

def generate_ligand_pdbqt(prepare_ligand, smiles, reference_center):
    try:
        ligand_name = smiles[0]
        smi = smiles[1]
        activity = smiles[2] if len(smiles) >= 3 and smiles[2] else 'NaN'
        others = smiles[3:] if len(smiles) > 3 else None

        if os.path.exists(os.path.join('.', f'{ligand_name}.pdbqt')):
            return smi, ligand_name, activity, others

        ligand_pdb = f'{ligand_name}.pdb'
        if not os.path.exists(os.path.join('.', ligand_pdb)):
            mol = mol_opt(smi)
            if mol is None:
                return None
            mol_block = Chem.MolToPDBBlock(mol)
            with open(ligand_pdb, 'w') as pdb_file:
                pdb_file.write(mol_block)

        startsWith, atom_coordinates, endsWith = extract_molecule(ligand_pdb)
        moved_atom_coordinates = move_molecule_to_target_center(
            atom_coordinates, reference_center
        )

        moved_ligand_pdb_path = f'{ligand_name}_tmp.pdb'
        write_molecule_to_pdb(startsWith, moved_atom_coordinates, endsWith, moved_ligand_pdb_path)
        ligand_pdbqt = f'{ligand_name}.pdbqt'
        run_command(f'{prepare_ligand} -l {moved_ligand_pdb_path} -o {ligand_pdbqt}')
        os.remove(ligand_pdb)
        os.rename(moved_ligand_pdb_path, f'{ligand_name}.pdb')

        return smi, ligand_name, activity, others

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Receptor preparation
# ---------------------------------------------------------------------------

def generate_receptor_pdbqt(prepare_receptor, receptor_pdb):
    try:
        receptor_name = os.path.basename(receptor_pdb).split('.')[0]
        receptor_pdbqt = f'{receptor_name}.pdbqt'
        counter = 1
        while os.path.exists(receptor_pdbqt):
            receptor_pdbqt = f'{receptor_name}_{counter}.pdbqt'
            counter += 1
~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## engine/interaction_analyzer.py

*(total 550 baris; ditampilkan 140 baris pertama)*

~~~~python
"""
LADOCK — Interaction Analyzer (engine/interaction_analyzer.py)

Detects non-covalent interactions between receptor and ligand
by parsing PDBQT atom coordinates + types. No RDKit required.

Interactions detected
---------------------
1. H-Bond         — donor-acceptor distance ≤ 3.5 Å, D-H…A angle ≥ 120°
2. Hydrophobic     — C/A···C/A distance ≤ 4.5 Å (nonpolar carbons)
3. Pi-Stacking     — aromatic ring centroid ≤ 5.5 Å (face-face or T-shape)
4. Salt Bridge     — charged group centroid ≤ 5.0 Å (opposite charges)
5. Halogen Bond    — halogen···acceptor ≤ 3.5 Å

PDBQT atom types used
---------------------
A  — aromatic carbon
C  — aliphatic carbon
HD — hydrogen on donor
NA — nitrogen H-bond acceptor
OA — oxygen H-bond acceptor
SA — sulfur H-bond acceptor
N  — nitrogen
O  — oxygen

Usage
-----
    from engine.interaction_analyzer import analyze_interactions, InteractionResult
    atoms_rec = parse_pdbqt(receptor_text)
    atoms_lig = parse_pdbqt(ligand_text)
    results   = analyze_interactions(atoms_rec, atoms_lig)
    for r in results:
        print(r)
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Atom:
    serial:    int
    name:      str
    resname:   str
    chain:     str
    resseq:    int
    x: float
    y: float
    z: float
    charge:    float
    atom_type: str          # PDBQT type (A, C, N, OA, HD, …)
    source:    str = ""     # "receptor" | "ligand"

    @property
    def pos(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z], dtype=float)

    def __repr__(self):
        return f"Atom({self.name}/{self.resname}{self.resseq}/{self.chain} {self.atom_type})"


@dataclass
class Interaction:
    itype:       str               # "H-Bond" | "Hydrophobic" | "Pi-Stacking" | "Salt Bridge" | "Halogen Bond"
    rec_atom:    Atom
    lig_atom:    Atom
    distance:    float             # Å
    angle:       Optional[float]   # degrees (H-bond D-H-A angle, pi tilt angle, etc.)
    subtype:     str = ""          # e.g. "Face-to-Face", "T-Shaped", "donor=N", etc.

    def rec_label(self) -> str:
        return f"{self.rec_atom.resname}{self.rec_atom.resseq}:{self.rec_atom.chain} {self.rec_atom.name}"

    def lig_label(self) -> str:
        return f"{self.lig_atom.resname} {self.lig_atom.name}"

    def __str__(self):
        angle_str = f"  angle={self.angle:.1f}°" if self.angle is not None else ""
        return (f"[{self.itype}] {self.rec_label()} ↔ {self.lig_label()}"
                f"  d={self.distance:.2f}Å{angle_str}  {self.subtype}")


@dataclass
class AnalysisResult:
    interactions:   List[Interaction] = field(default_factory=list)
    summary:        dict              = field(default_factory=dict)

    def by_type(self, itype: str) -> List[Interaction]:
        return [i for i in self.interactions if i.itype == itype]

    def count(self) -> dict:
        counts: dict[str, int] = {}
        for i in self.interactions:
            counts[i.itype] = counts.get(i.itype, 0) + 1
        return counts

    def to_hbond_vectors(self) -> list[dict]:
        """Return H-bond start/end dicts for 3Dmol.js showHBonds()."""
        return self.to_vectors(["H-Bond"])

    def to_vectors(self, types: list[str] | None = None) -> list[dict]:
        """
        Return bond vector dicts for 3Dmol.js showHBonds() / showInteractionHighlights().
        Includes residue info for pocket highlighting.
        types: list of itype strings to include; None = all types.
        """
        _COLORS = {
            "H-Bond":       "#4fc3f7",
            "Hydrophobic":  "#ffb74d",
            "Pi-Stacking":  "#ce93d8",
            "Salt Bridge":  "#ef5350",
            "Halogen Bond": "#80cbc4",
        }
        out = []
        for i in self.interactions:
            if types and i.itype not in types:
                continue
            out.append({
                "start":      {"x": i.rec_atom.x, "y": i.rec_atom.y, "z": i.rec_atom.z},
                "end":        {"x": i.lig_atom.x, "y": i.lig_atom.y, "z": i.lig_atom.z},
                "itype":      i.itype,
                "color":      _COLORS.get(i.itype, "#ffffff"),
                "distance":   round(i.distance, 2),
                "rec_resn":   i.rec_atom.resname,
                "rec_resi":   i.rec_atom.resseq,
                "rec_chain":  i.rec_atom.chain,
                "rec_atom":   i.rec_atom.name,
                "lig_atom":   i.lig_atom.name,
            })
        return out
~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## engine/mol_prep.py

*(total 502 baris; ditampilkan 110 baris pertama)*

~~~~python
"""
LADOCK — Molecular Preparation Engine
======================================
RDKit-based pipeline untuk preparasi reseptor (protein) dan ligan
sebelum docking.

Fungsi utama
------------
prep_receptor(pdb_path, steps)  → (pdb_str, report_str)
prep_ligand(mol_path, steps)    → (sdf_str, report_str)

Steps yang didukung
-------------------
Receptor:
  'remove_water'    — hapus molekul air (HOH/WAT/H2O)
  'remove_hetatm'   — hapus semua HETATM kecuali yang dipilih
  'remove_metal'    — hapus ion logam
  'keep_chain'      — pertahankan chain tertentu saja (opsional, via kwarg)
  'add_h'           — tambah atom hidrogen (RDKit AddHs)
  'add_charge'      — hitung Gasteiger partial charge dan simpan di B-factor
  'fix_pdb'         — sanitasi / fix format PDB via RDKit

Ligand:
  'add_h'           — tambah H
  'embed_3d'        — generate/embed konformer 3D (ETKDG)
  'optimize'        — optimasi geometri MMFF94
  'add_charge'      — Gasteiger charge
"""

from __future__ import annotations
import os
import re
import copy
from pathlib import Path
from typing import Optional

# ── RDKit imports (all optional — degrade gracefully) ──────────────────────
try:
    from rdkit import Chem
    from rdkit.Chem import AllChem, rdPartialCharges, rdMolDescriptors
    _RDKIT = True
except ImportError:
    _RDKIT = False


# ── Metal element set ───────────────────────────────────────────────────────
METAL_ELEMENTS = {
    'ZN','MG','CA','FE','MN','CU','NI','CO','MO','NA','K','CD','HG',
    'PT','AU','AG','AL','BA','SR','PB','BI','CS','LI','RB','IN','CR','V','W'
}

WATER_RESNAMES = {'HOH','WAT','H2O','DOD','TIP','SOL'}


# ═══════════════════════════════════════════════════════════════════════════ #
# Pure-text PDB manipulation (no RDKit dependency)
# ═══════════════════════════════════════════════════════════════════════════ #

def _parse_atom_line(line: str) -> dict | None:
    """Parse a PDB ATOM/HETATM line into a dict."""
    rec = line[:6].strip()
    if rec not in ('ATOM', 'HETATM'):
        return None
    try:
        return {
            'record': rec,
            'serial': int(line[6:11]),
            'name':   line[12:16].strip(),
            'altLoc': line[16],
            'resName':line[17:20].strip(),
            'chainID':line[21],
            'resSeq': int(line[22:26]),
            'iCode':  line[26],
            'x':      float(line[30:38]),
            'y':      float(line[38:46]),
            'z':      float(line[46:54]),
            'occ':    float(line[54:60]) if line[54:60].strip() else 1.0,
            'bfac':   float(line[60:66]) if line[60:66].strip() else 0.0,
            'elem':   line[76:78].strip().upper() if len(line) > 76 else '',
            'raw':    line,
        }
    except (ValueError, IndexError):
        return None


def pdb_remove_water(pdb_str: str) -> tuple[str, str]:
    """Remove water molecules (HOH, WAT, …) from PDB text."""
    kept, removed = [], 0
    for line in pdb_str.splitlines(keepends=True):
        a = _parse_atom_line(line)
        if a and a['resName'] in WATER_RESNAMES:
            removed += 1
        else:
            kept.append(line)
    return ''.join(kept), f"Removed {removed} water atoms."


def pdb_remove_hetatm(pdb_str: str, keep_resnames: set[str] | None = None) -> tuple[str, str]:
    """Remove HETATM records (optionally keeping specified residue names)."""
    keep_resnames = keep_resnames or set()
    kept, removed = [], 0
    for line in pdb_str.splitlines(keepends=True):
        a = _parse_atom_line(line)
        if a and a['record'] == 'HETATM' and a['resName'] not in keep_resnames:
            removed += 1
        else:
            kept.append(line)
    return ''.join(kept), f"Removed {removed} HETATM atoms."


~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## data/project.py

*(total 149 baris; ditampilkan 100 baris pertama)*

~~~~python
"""
LADOCK Project Model
Manages the project directory structure and metadata.

Project layout:
  <project_root>/
  ├── receptors/          PDB receptor files
  ├── ligands/            Ligand input files (SMI, SDF, URI)
  ├── grids/              Grid/config files
  ├── docking_runs/       Per-run working directories
  │   └── <run_id>/
  │       ├── model*/     Model subdirectories (receptor+ref ligand)
  │       ├── ligand_input/
  │       └── output/
  ├── poses/              PDBQT pose files
  ├── results/            CSV result files
  └── logs/               Log files
"""

import os
import json
import uuid
import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional


SUBDIRS = ["receptors", "ligands", "grids", "docking_runs", "poses", "results", "logs"]


@dataclass
class DockingRun:
    run_id: str
    name: str
    created_at: str
    receptor: str = ""
    ligand_file: str = ""
    sf_types: List[str] = field(default_factory=list)
    listmode: List[str] = field(default_factory=list)
    status: str = "pending"   # pending | running | finished | failed
    result_csv: str = ""


@dataclass
class LADOCKProject:
    name: str
    root: str
    created_at: str = ""
    runs: List[DockingRun] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Class methods
    # ------------------------------------------------------------------ #

    @classmethod
    def create(cls, root: str, name: str) -> "LADOCKProject":
        """Create a new project at `root`."""
        root = str(Path(root).resolve())
        os.makedirs(root, exist_ok=True)
        for sub in SUBDIRS:
            os.makedirs(os.path.join(root, sub), exist_ok=True)

        project = cls(
            name=name,
            root=root,
            created_at=datetime.datetime.now().isoformat()
        )
        project.save()
        return project

    @classmethod
    def load(cls, root: str) -> "LADOCKProject":
        """Load an existing project from its root directory (or project.json path)."""
        root = str(Path(root).resolve())
        # Accept either the directory or the project.json file itself
        if root.endswith("project.json") and os.path.isfile(root):
            root = str(Path(root).parent)
        meta_file = os.path.join(root, "project.json")
        if not os.path.exists(meta_file):
            raise FileNotFoundError(f"No LADOCK project found at: {root}")
        with open(meta_file, 'r') as f:
            data = json.load(f)
        runs = [DockingRun(**r) for r in data.pop("runs", [])]
        project = cls(**data, runs=runs)
        return project

    # ------------------------------------------------------------------ #
    # Instance methods
    # ------------------------------------------------------------------ #

    def save(self):
        """Persist project metadata to project.json."""
        meta_file = os.path.join(self.root, "project.json")
        data = asdict(self)
        with open(meta_file, 'w') as f:
            json.dump(data, f, indent=2)

    def path(self, *parts) -> str:
        """Return absolute path under project root."""
~~~~

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## data/result_parser.py

*(total 130 baris; ditampilkan 110 baris pertama)*

~~~~python
"""
LADOCK Result Parser
Parse docking output files (PDBQT, CSV) into structured DataFrames.
"""

import os
import re
import pandas as pd
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# PDBQT energy parser
# ---------------------------------------------------------------------------

def parse_pdbqt_energies(pdbqt_file: str) -> List[dict]:
    """
    Parse all pose energies from a Vina/Vinardo/AD4 output PDBQT file.

    Returns list of dicts:
      [{'pose': 1, 'affinity': -8.2, 'rmsd_lb': 0.0, 'rmsd_ub': 0.0}, ...]
    """
    poses = []
    pattern = re.compile(
        r'REMARK VINA RESULT:\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)'
    )
    if not os.path.exists(pdbqt_file):
        return poses
    with open(pdbqt_file, 'r') as f:
        for line in f:
            m = pattern.match(line)
            if m:
                poses.append({
                    'pose': len(poses) + 1,
                    'affinity': float(m.group(1)),
                    'rmsd_lb': float(m.group(2)),
                    'rmsd_ub': float(m.group(3)),
                })
    return poses


def get_best_energy(pdbqt_file: str) -> Optional[float]:
    """Return best (lowest) binding energy from a PDBQT output file."""
    poses = parse_pdbqt_energies(pdbqt_file)
    if not poses:
        return None
    return min(p['affinity'] for p in poses)


# ---------------------------------------------------------------------------
# CSV result loader
# ---------------------------------------------------------------------------

def load_results_csv(csv_file: str) -> pd.DataFrame:
    """Load a LADOCK results CSV and return a sorted DataFrame."""
    if not os.path.exists(csv_file):
        return pd.DataFrame()
    df = pd.read_csv(csv_file)
    # Sort by first energy column if present
    energy_cols = [c for c in df.columns if c.endswith('_Energy')]
    if energy_cols:
        df = df.sort_values(by=energy_cols[0], ascending=True).reset_index(drop=True)
        df.insert(0, 'rank', range(1, len(df) + 1))
    return df


def find_result_csvs(output_dir: str) -> List[str]:
    """Recursively find all results CSV files under output_dir."""
    result_files = []
    for root, dirs, files in os.walk(output_dir):
        for f in files:
            if f.startswith('results_') and f.endswith('.csv'):
                result_files.append(os.path.join(root, f))
    return sorted(result_files)


def merge_results(csv_files: List[str]) -> pd.DataFrame:
    """Merge multiple results CSVs into one DataFrame."""
    frames = []
    for f in csv_files:
        df = load_results_csv(f)
        if not df.empty:
            df['source_file'] = os.path.basename(f)
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# PDBQT pose extractor
# ---------------------------------------------------------------------------

def extract_poses(pdbqt_file: str) -> List[str]:
    """
    Split a multi-pose PDBQT output into individual pose strings.

    Returns list of pose blocks (each is a full PDBQT string for one pose).
    """
    if not os.path.exists(pdbqt_file):
        return []

    poses = []
    current = []
    with open(pdbqt_file, 'r') as f:
        for line in f:
            current.append(line)
            if line.startswith('ENDMDL') or line.startswith('END'):
                if current:
                    poses.append(''.join(current))
~~~~
