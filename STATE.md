# Status

Diperbarui: 2026-09-02 — PC Ubuntu 24 (`arga`)

## Sedang dikerjakan

Tidak ada yang tergantung. Direktori kerja bersih, semua terdorong ke
`origin/main`. Delapan berkas WIP multireceptor yang menggantung sejak
sebelum sesi ini sudah dipecah jadi tiga commit dan selesai.

## Yang berubah hari ini

- **Rilis 0.3.0 terbit** di GitHub dengan installer keempat platform
  (`.exe` native, `.exe` hybrid WSL, `.deb` + AppImage, `.dmg`) plus arsip
  engine. Pipeline installer sebelumnya belum pernah berhasil sekali pun;
  empat cacat diperbaiki: `ARCH` untuk appimagetool, `fail-fast: false` pada
  matriks Windows, path absolut untuk Inno Setup, dan versi yang di-hardcode
  2.0.0 di keempat skrip packaging.
- **macOS**: `packaging/ladock.spec` kini punya `BUNDLE`, jadi PyInstaller
  benar-benar menghasilkan `LADOCK.app`. Sebelumnya hanya direktori biasa,
  dan blok `run:` di workflow rusak karena skalar YAML polos mengubah `\`
  jadi spasi ter-escape.
- **PyPI**: `ladock 0.3.1` terbit lewat Trusted Publishing. `0.3.0` di-yank
  karena membawa teks lisensi lama.
- **Lisensi diseragamkan**: satu teks di repo dan situs, mencantumkan
  Surat Pencatatan Ciptaan No. 001413018. Sebelumnya tiga dokumen menyebut
  tiga hal berbeda, dan situs menjanjikan gratis sampai 2030 padahal kode
  berhenti 2029-12-31.
- **Penguncian lisensi jadi nyata**: `ladock/licensing.py` sumber tunggal,
  desktop kini ikut berhenti seperti CLI, dan jam yang dimundurkan tidak lagi
  menambah waktu.
- **Kunci lisensi tidak lagi bisa dipalsukan**: HMAC dengan rahasia yang ikut
  terkirim ke setiap pengguna diganti Ed25519. Klien hanya membawa kunci
  publik; kunci privat di `~/.ladock-signing/`, di luar semua repo.

## Langkah berikutnya

1. Perbaikan kode dari daftar cacat di CLAUDE.md — `tool_status_widget.py`
   yatim, duplikasi 59% antar dua panel docking, 35 `except` yang menelan
   galat, dan mode desktop yang belum punya tes sama sekali.
2. Bila kelak ada rilis yang membawa installer baru, buat sebagai rilis
   biasa — **bukan pre-release** — supaya `releases/latest` ikut berpindah.
   Saat ini v0.3.1 pre-release, sehingga `latest` masih menunjuk v0.3.0 yang
   berisi installer.

## Tertunda / macet

- **Sentra HKI UNG.** Kode publik sejak 27 Juni 2026 — tanggal itu kini
  tercatat resmi di sertifikat hak cipta. Bila ada rencana paten, kebaruannya
  tersentuh. Ditambah dua lisensi berdampingan di PyPI: 0.1.6 Apache-2.0
  (permanen, tidak bisa ditarik) dan 0.3.1 proprietary.
- **GitHub Support** belum dihubungi untuk purge commit lama yang memuat
  `HAKI/`. Draf di repo riset, `haki/PERMINTAAN-PURGE-GITHUB.md`.
- **Token rclone perlu dicabut** — sempat tercetak ke layar saat sesi ini.
  `myaccount.google.com/permissions` → rclone → hapus akses, lalu
  `rclone config reconnect grive:`.

## Keputusan terakhir

- 2026-09-02 — Wheel PyPI sengaja tidak memuat engine (0,5 MB). Engine
  diunduh saat pertama dipakai dari aset rilis; installer mendapatkannya dari
  `build_release.py`, bukan dari setuptools.
- 2026-09-02 — `_DEFAULT_BASE` di `binaries.py` dipatok ke `v0.3.0` tempat
  arsip engine berada, dan **tidak** ikut naik saat versi paket naik.
- 2026-09-02 — Penguncian lisensi bukan anti-oprek. LADOCK dikirim sebagai
  Python yang bisa dibaca; batas waktunya satu baris yang bisa disunting di
  disk pengguna. Berlaku untuk pengguna biasa, bukan untuk yang gigih.
