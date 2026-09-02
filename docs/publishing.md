# Merilis LADOCK ke PyPI

Rilis dilakukan lewat **Trusted Publishing**: PyPI memverifikasi workflow GitHub
melalui OIDC, jadi tidak ada API token yang perlu dibuat, disalin, disimpan,
atau bisa bocor. Repositori ini tidak menyimpan rahasia apa pun.

## Sekali saja: daftarkan penerbit tepercaya di PyPI

Ini **bukan** tombol "Connect GitHub" di halaman akun — itu untuk menautkan akun.
Trusted publishing diatur per-proyek:

> pypi.org → **Your projects** → **ladock** → **Manage** → **Publishing** →
> *Add a new publisher* → **GitHub**

Isi persis seperti ini:

| Kolom | Nilai |
|---|---|
| Owner | `laode-aman-ung` |
| Repository name | `LADOCK` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

Keempatnya harus sama persis; PyPI menolak permintaan dari workflow yang tidak
cocok. Itulah yang membuat mekanisme ini aman tanpa token.

## Sekali saja: buat environment di GitHub

> GitHub → repo → **Settings** → **Environments** → *New environment* → `pypi`

Environment memberi satu lapis kendali lagi: di situ Anda bisa mewajibkan
persetujuan manual sebelum langkah unggah berjalan, dan membatasi branch mana
yang boleh merilis. Namanya harus `pypi`, sesuai isian di PyPI dan di
`.github/workflows/publish.yml`.

## Setiap rilis

1. Naikkan `version` di `pyproject.toml` (mis. `2.0.1`) dan commit.
2. Buat GitHub Release dengan tag versinya:

   ```bash
   git tag v2.0.1 && git push origin v2.0.1
   gh release create v0.3.1 --title "LADOCK 0.3.1" --notes "..."
   ```

3. Workflow membangun sdist + wheel, menjalankan `twine check`, memasang wheel
   di venv bersih untuk memastikan ketiga perintah muncul, lalu mengunggah.

Ingin menguji tanpa menerbitkan? Jalankan workflow secara manual
(**Actions → Publish to PyPI → Run workflow**): tahap build tetap berjalan
lengkap, tahap publish dilewati.

## Yang tidak boleh dilupakan

- **Nomor versi permanen.** PyPI melarang versi yang sama dipakai ulang, bahkan
  setelah dihapus. Salah sedikit → rilis 2.0.1.
- **Biner engine tidak ikut di wheel.** Pastikan arsip di
  `https://ladock.ladeep.id/bin/` sudah ada sebelum merilis, kalau tidak
  `ladock-fetch-binaries` gagal untuk semua pengguna baru. Buat arsipnya dengan
  `python packaging/package_binaries.py linux`.
- **Pengguna 0.1.x akan terkejut.** Perintah `ladock` dan `ladockgui` hilang,
  diganti `ladock-cli` / `ladock-desktop`, dan lisensinya berubah dari
  Apache-2.0 menjadi lisensi akademik. Sebutkan di catatan rilis.

## Kalau tetap ingin memakai token

Trusted publishing tidak wajib. Alternatifnya tetap:

```bash
python -m build
python -m twine upload dist/*      # username: __token__
```

Catatan: token pypi.org **tidak berlaku** di test.pypi.org. TestPyPI adalah
layanan terpisah dengan akun dan token sendiri — memakai token pypi.org di sana
selalu menghasilkan `403 Forbidden`.
