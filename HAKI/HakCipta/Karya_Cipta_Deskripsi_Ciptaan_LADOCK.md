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
