# Status

Diperbarui: 2026-09-02 — PC Ubuntu 24 (`arga`)

## Sedang dikerjakan

Cabang `ladock-cli-agent-multireceptor` dengan 8 berkas termodifikasi yang
**belum di-commit**: `ladock/{__init__,binaries,paths}.py`,
`ladock/cli/agent.py`, `tests/test_cli_agent.py`, `pyproject.toml`,
`docs/publishing.md`, `packaging/README.md`. Perubahan ini ada sebelum sesi
penyiapan sinkronisasi dan tidak saya sentuh.

## Langkah berikutnya

1. Selesaikan dan commit pekerjaan multireceptor yang tergantung di 8 berkas
   itu, lalu jalankan `pytest tests/`.
2. Putuskan apakah `ladock-cli-agent-multireceptor` di-merge ke `main`.
   Cabang `main` masih menampilkan struktur lama (`desktop/`) di halaman
   repo publik yang dirujuk naskah.
3. Perbaiki tautan gambar `ladock_viewer.png` yang rusak di `README.md`.
4. Pasang rclone, dorong `ladock/bin/` (658 MB) ke `gdrive:riset/LADOCK/bin`.

## Tertunda / macet

- rclone belum terpasang di mesin mana pun.
- Permintaan purge ke GitHub Support belum dikirim — commit lama masih dapat
  diakses lewat SHA. Draf ada di repo riset, `haki/PERMINTAAN-PURGE-GITHUB.md`.
- Konsultasi Sentra HKI soal paten belum dilakukan.

## Keputusan terakhir

- 2026-09-02 — Arsitektur final: SATU repo kode (`LADOCK`, publik, mencakup
  kedua mode) + SATU repo riset privat (`ladock-riset`, dipecah per tahun).
  Repo `ladock-desktop` dibubarkan; isinya snapshot GUI 2.0.0 yang sudah
  didahului `ladock/desktop/`.
- 2026-09-02 — Deskripsi dan topik repo GitHub diperbarui agar mencakup kedua
  mode, bukan hanya desktop.

- 2026-09-02 — Repo tetap **publik** karena tautannya sudah tercantum di
  naskah. Materi sensitif dipindahkan ke repo privat `ladock-riset`, bukan
  disimpan di sini seperti rencana awal.
- 2026-09-02 — Riwayat kedua cabang ditulis ulang untuk membuang `HAKI/`,
  lalu di-force-push. Klon lama harus di-reset.
- 2026-09-02 — Proyek dipindah ke `~/riset/LADOCK` agar path identik di PC
  dan laptop WSL.
