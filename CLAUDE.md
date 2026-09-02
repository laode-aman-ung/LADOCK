# CLAUDE.md — LADOCK (CLI + Desktop)

Panduan kerja untuk sesi Claude Code di repositori ini.

---

## ⚠️ Repositori ini PUBLIK

Tautannya sudah tercantum di naskah yang beredar, jadi statusnya memang
disengaja. Konsekuensinya mutlak:

- **Jangan pernah** menaruh dokumen HKI, naskah belum terbit, dokumen hibah,
  data keuangan, atau data pribadi di sini. Semua itu ada di repo privat
  `laode-aman-ung/ladock-riset`.
- `.gitignore` sudah memblokir `HAKI/`, `luaran/`, `Surat_Pernyataan*`,
  `Surat_Pengalihan*`, `*_Biodata_*` dan pola kredensial. Jangan dilonggarkan.
- Folder `HAKI/` pernah tidak sengaja publik di sini selama tujuh minggu
  (15 Jul – 2 Sep 2026). Riwayat sudah ditulis ulang. Kronologi lengkap ada
  di repo riset, `haki/CATATAN-INSIDEN.md`.
- **Ada rencana paten.** Kode ini publik sejak 2026-06-27. Konsultasikan ke
  Sentra HKI UNG sebelum menambah pengungkapan.

---

## Ringkasan

LADOCK adalah workstation molecular docking yang dikemas sebagai **satu**
paket pip (`ladock`) dengan dua front-end:

| Perintah | Isi |
|---|---|
| `ladock-desktop` | GUI PySide6, tema gelap Catppuccin |
| `ladock-cli` | Agen docking berbasis aturan (**bukan LLM**) — wizard terpandu atau sepenuhnya skrip |
| `ladock-fetch-binaries` | Mengunduh mesin docking dari GitHub release assets |

Keduanya berbagi mesin, pipeline preparasi, dan pohon `bin/` yang sama.

Lisensi **bukan open source**: bebas untuk penggunaan akademik sampai
2029-12-31, hak lain dipertahankan, penggunaan komersial perlu lisensi.
Karena itu tidak ada classifier `License :: OSI Approved` di pyproject.

## Repositori terkait

Hanya ada **satu** repo kode. `ladock-cli` dan `ladock-desktop` adalah nama
perintah, bukan nama repositori — jangan pernah membuat repo dengan nama itu.

| Repo | Status | Isi |
|---|---|---|
| `laode-aman-ung/LADOCK` | publik | repo ini — produk lengkap, kedua mode |
| `laode-aman-ung/ladock-riset` | privat | HKI, naskah, luaran hibah, pengujian per tahun |

Repo `ladock-desktop` pernah ada berisi snapshot GUI 2.0.0 yang sudah
didahului oleh `ladock/desktop/` di sini. Repo itu dibubarkan 2026-09-02.

## Struktur

```
ladock/
  cli/          Agen CLI berbasis aturan
  desktop/      Aplikasi GUI PySide6
  bin/          Mesin docking — TIDAK di Git, 658 MB
  binaries.py   Pengunduh mesin dari release assets
  paths.py      Resolusi lokasi paket dan mesin
docs/           cli.md, publishing.md
packaging/      Skrip rilis, spec PyInstaller, pembungkus per-OS
scripts/        Peluncur dan pemasang untuk Linux, Windows, WSL
tools/          generate_license.py
tests/          test_cli_agent.py
website/        Halaman web proyek
.github/        CI: build-installers.yml, publish.yml
```

## Cara menjalankan dan menguji

```bash
python -m pip install -e .
ladock-fetch-binaries        # isi ladock/bin/ dari GitHub release assets
ladock-cli                   # agen CLI
ladock-desktop               # GUI
```

```bash
python -m pytest tests/ -q
```

Membangun rilis: lihat `packaging/README.md` dan `docs/publishing.md`.

## Dependensi

Python ≥ 3.10 (CI menguji 3.10–3.13). `PySide6` metapackage (QtWebEngine ada
di Addons), `numpy`, `pandas`. RDKit dan Meeko dimuat **lazily** — CLI tidak
mengimpornya kecuali jalur kode yang membutuhkannya dijalankan.

## Mesin docking di Google Drive

`ladock/bin/` (658 MB) tidak masuk Git. Dua cara mengisinya:

```bash
ladock-fetch-binaries                                               # dari GitHub release
rclone sync gdrive:riset/LADOCK/bin ~/riset/LADOCK/ladock/bin        # dari Drive
```

`build/` dan `dist/` adalah artefak dan tidak disinkronkan ke mana pun.

## Konvensi yang teramati

- Satu paket pip, banyak entry point; `gui-scripts` dipakai untuk GUI agar di
  Windows tidak memunculkan jendela konsol.
- Impor pustaka berat dilakukan lazily di dalam fungsi, bukan di tingkat modul.
- Komentar di `pyproject.toml` menjelaskan *alasan* pilihan, bukan sekadar apa
  — pertahankan gaya itu.
- `.gitattributes` memaksa `eol=lf` kecuali `.bat/.ps1/.iss` yang `crlf`.
- Komentar kode dan pesan commit dalam bahasa Inggris.

## Hal yang perlu diketahui sebelum mengubah apa pun

Audit 2026-09-02 atas 49 berkas / 21.101 baris di `ladock/`, **tidak termasuk**
`ladock/bin/` yang berisi pustaka pihak ketiga (ADFRsuite, MGLTools). Setiap
pengukuran atas basis kode ini harus mengecualikan `bin/`, kalau tidak
angkanya membengkak sepuluh kali lipat dan menyesatkan.

Kondisi yang sehat dan sebaiknya dipertahankan:

- Nol `shell=True`, nol perintah yang dirakit dari f-string. Semua pemanggilan
  proses eksternal memakai daftar argv.
- Nol `except:` telanjang.
- Tidak ada definisi ganda dalam satu scope.
- `core/tool_paths.py` memilih binary per-platform (windows/linux/mac) dan
  punya mode hybrid: GUI Windows melempar mesin Linux-only ke WSL.

Yang masih terbuka:

1. **`gui/widgets/tool_status_widget.py` (159 baris) tidak pernah diimpor.**
   Kandidat hapus.
2. **Duplikasi panel docking.** 59% baris `gui/panels/ligand_test_panel.py`
   identik dengan `native_redocking_panel.py` — worker, parser keluaran,
   deteksi tool, dan penulis CSV diduplikasi. Kandidat ekstraksi kelas basis.
3. **35 blok `except` berakhir `pass`/`continue`,** menelan galat tanpa jejak.
4. **Tidak ada `logging` sama sekali** di 49 berkas; diagnostik mengandalkan
   `print` dan sinyal Qt.
5. **Tes hanya menutupi CLI.** 68 fungsi tes di `tests/test_cli_agent.py`,
   seluruhnya mengimpor `ladock.cli.agent`. Mode desktop tidak diuji sama
   sekali. Kandidat pertama yang mudah: fungsi murni di
   `engine/interaction_analyzer.py` dan `data/result_parser.py`.

Sudah diperbaiki 2026-09-02: `set_job_dir` yang terdefinisi dua kali di
`ligand_test_panel.py`, dan `show_welcome` yang dibaca tanpa `type=bool`
sehingga string `"false"` selalu bernilai benar.

## Aturan sesi

### Awal sesi
1. Jalankan `git pull` sebelum melakukan apa pun.
2. Baca `STATE.md`.
3. Ringkas dalam 2–3 kalimat di mana pekerjaan terhenti.
4. Jangan mulai mengerjakan sebelum saya konfirmasi arahnya.

### Akhir sesi
Dipicu saat saya bilang "tutup sesi", "selesai", atau sejenisnya.
1. Perbarui `STATE.md`: perubahan, langkah berikutnya, yang macet. Ringkas,
   dengan tanggal dan nama mesin.
2. Perbarui `CLAUDE.md` bila ada keputusan arsitektur atau konvensi baru.
3. Tampilkan daftar berkas yang akan di-commit dan tunggu persetujuan saya.
4. Commit dengan pesan deskriptif, lalu push.
5. Ingatkan saya bila ada berkas besar yang belum disinkronkan ke Drive.

### Sepanjang sesi
- Bahasa Indonesia untuk penjelasan, Inggris untuk komentar kode dan pesan
  commit.
- Jangan pernah menambahkan berkas data besar atau kredensial ke Git.
- Jangan membuat berkas baru bila mengedit yang ada sudah memadai.
