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

| Repo | Status | Isi |
|---|---|---|
| `laode-aman-ung/LADOCK` | publik | repo ini — CLI + Desktop 0.3.0 |
| `laode-aman-ung/ladock-desktop` | privat | LADOCK Desktop 2.0, GUI lama berbasis PySide6 |
| `laode-aman-ung/ladock-riset` | privat | HKI, naskah, luaran hibah, laporan pengujian |

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

1. Cabang `main` **tertinggal** dari cabang kerja
   `ladock-cli-agent-multireceptor`. Halaman repo yang dilihat pembaca naskah
   menampilkan struktur lama (`desktop/`), bukan struktur `ladock/` sekarang.
2. `README.md` merujuk gambar `ladock_viewer.png` yang tidak ada di repo —
   tautan gambarnya rusak di halaman GitHub.
3. Riwayat kedua cabang ditulis ulang pada 2026-09-02. Siapa pun yang punya
   klon lama harus `git fetch && git reset --hard origin/<cabang>`.

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
