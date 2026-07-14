# LADOCK Desktop

**Molecular docking workstation** built on the LADOCK pipeline, with a modern PySide6 GUI and dark Catppuccin theme. Proprietary software — free for academic use, commercial license required for for-profit use (see [License](#license)).

![LADOCK Desktop](ladock_viewer.png)

---

## Features

- **Multi-engine docking** — AutoDock Vina, AutoDock 4, VinaGPU, AutoDock-GPU
- **Batch docking** — run multiple ligands in parallel with built-in job scheduler
- **Ligand library management** — import from CSV, SDF, or PDBQT; SMILES rendering via RDKit
- **Interactive 3D viewer** — 3Dmol.js-powered molecular visualization
- **Non-covalent interaction analysis** — H-bond, π-stacking, hydrophobic contacts, and more
- **Result explorer** — sortable tables with binding energy results
- **Project management** — save/load docking projects with structured job directories
- **Native cross-platform prep** — receptor/ligand PDBQT preparation runs natively on Windows, Linux and macOS via Meeko + RDKit (no MGLTools, no WSL required for Vina/Vinardo)
- **Platform-aware engines** — the docking UI enables only the scoring functions supported where LADOCK runs. The Windows build is pure-native (Vina/Vinardo); AutoDock4 / AutoDock-GPU are Linux-only and become available by running LADOCK inside WSL or on Linux

---

## Requirements

- Python ≥ 3.10
- PySide6 ≥ 6.5
- NumPy ≥ 1.24
- SciPy ≥ 1.10
- pandas ≥ 2.0
- RDKit ≥ 2023.3 — molecular preparation and SMILES rendering
- Meeko ≥ 0.5 (+ gemmi) — native receptor/ligand PDBQT preparation

---

## Installation

The desktop application lives in the `desktop/` subdirectory.

### Windows

```bat
# Clone the repository
git clone https://github.com/your-org/ladock-desktop.git
cd ladock-desktop/desktop

# Install dependencies (Miniconda/Anaconda recommended)
# This includes RDKit, used by the molecular preparation engine.
python -m pip install -e .
```

### Linux / WSL

```bash
git clone https://github.com/your-org/ladock-desktop.git
cd ladock-desktop/desktop
pip install -e .
```

---

## Usage

All launchers live in `desktop/`.

### Windows

Double-click `desktop/ladock.bat`, or from a terminal:

```bat
cd desktop
ladock.bat
```

### Linux / macOS

```bash
cd desktop
bash ladock.sh
```

### WSL (Windows Subsystem for Linux)

```bat
cd desktop
ladock-wsl.bat
```

### Python (cross-platform)

```bash
cd desktop
python main.py
```

---

## Workspace Structure

```
LADOCK/
├── desktop/              # LADOCK Desktop application
│   ├── app/              # Application layer (main window, dialogs, project manager)
│   ├── core/             # Core utilities (job models, WSL backend, tool paths)
│   ├── data/             # Data models (project, result parser)
│   ├── engine/           # Molecule prep, interaction analyzer, tool detector
│   ├── gui/              # Theme and UI panels
│   │   └── panels/       # Docking prep, redocking, lig test, jobs, results
│   ├── bin/              # Bundled binaries, split per platform:
│   │   ├── windows/      #   Vina (native .exe)
│   │   ├── linux/        #   Vina, AutoDock4/AutoGrid4, AD-GPU, ADFRsuite, MGLTools
│   │   └── mac/          #   Vina (native)
│   ├── main.py           # Entry point
│   └── ladock.bat/.sh    # Launchers (+ install.*, ladock-wsl.*)
├── website/             # Project website (static HTML/CSS/JS)
└── HAKI/                # Intellectual property (HakCipta, Merek)
```

---

## Bundled Tools

Binaries are bundled per platform under `bin/<platform>/` and require no separate installation:

| Tool | Version | windows | linux | mac |
|------|---------|:-------:|:-----:|:---:|
| AutoDock Vina | 1.2.7 | ✅ | ✅ | ✅ |
| AutoDock 4 / AutoGrid 4 | — | | ✅ | |
| AutoDock-GPU | 1.6 | | ✅ | |
| ADFR / AGFR | ADFRsuite 1.0 | | ✅ | |
| MGLTools | 1.5.6 | | ✅ | |
| OpenBabel | 2.4.1 (in ADFRsuite) | | ✅ | |

Receptor and ligand PDBQT preparation is done **natively** by Meeko + RDKit on all
platforms, so the Linux-only MGLTools bundle is only needed for the AutoDock4 /
AutoDock-GPU grid path and flexible-receptor mode. The **Windows build is
pure-native** (Vina/Vinardo only) and is deliberately *not* combined with WSL — to
use the Linux engines, run LADOCK **inside** WSL or on Linux, where the app detects
itself as a Linux host and enables the full engine set.

External tools (AutoDock-GPU, VinaGPU) can also be configured via **Settings → Tool Paths**.

---

## Citation

If you use LADOCK in your research, please cite:

> Aman LO, Ischak NI, Tuloli TS, Arfan A, Asnawi A. (2024). Multiple ligands simultaneous molecular docking and dynamics approach to study the synergetic inhibitory of curcumin analogs on ErbB4 tyrosine phosphorylation. *Research in Pharmaceutical Sciences*, 19(6), 754–765. https://doi.org/10.4103/RPS.RPS_191_23

> Aman LO, Sihaloho M, Arfan A. (2023). Pencarian inhibitor DYRK2 dari database bahan alam ZINC15: Analisis farmakofor, simulasi docking dan dinamika molekuler. *Jurnal Sains Farmasi & Klinis*, 10(1), 100–113. https://doi.org/10.25077/jsfk.10.1.100-113.2023

> Aman LO, Arfan A, Asnawi A. (2023). In silico study of the synergistic interaction of 5-fluorouracil and curcumin analogues as inhibitors of B-cell lymphoma 2 protein. *International Journal of Applied Pharmaceutics*, 15(Special Issue 2), 61–66. https://doi.org/10.22159/ijap.2023.v15s2.05

### Third-party tools

- **AutoDock Vina**: Eberhardt J. et al. (2021) *J. Chem. Inf. Model.* 61(8):3891–3898. https://doi.org/10.1021/acs.jcim.1c00203
- **RDKit**: Landrum G. https://www.rdkit.org
- **3Dmol.js**: Rego N. & Koes D. (2015) *Bioinformatics* 31(8):1322–1324. https://doi.org/10.1093/bioinformatics/btu829

---

## License

LADOCK Desktop is **proprietary software** — Copyright (c) 2024 La Ode Aman. All rights reserved. See [LICENSE](LICENSE) for the full terms.

- **Free non-commercial use (2024–2030):** free of charge for **everyone** — students, academics, independent researchers, and the general public — for non-commercial research, study, teaching, and evaluation. **No registration, institutional email, or license key is required**; the app activates the free academic license automatically until December 31, 2030. Subject to the citation requirement above.
- **Commercial use:** requires a paid **commercial license** (for-profit companies, CROs, pharmaceutical/biotech firms, or any commercial R&D). Contact the licensor at laode_aman@ung.ac.id.

This is **not** an open-source license. Redistribution, sublicensing, and resale are not permitted.
