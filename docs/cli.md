# LADOCK Agent

`ladock/cli/` berisi **LADOCK docking CLI agent** berbasis aturan (non-LLM).
Mode utama agent adalah dialog terminal dengan pertanyaan tertutup:

```bash
python ladock_agent.py
```

## Instalasi sebagai command global

Agar `ladock` bisa dijalankan dari **workspace/direktori mana pun** di PC:

```bash
pip install -e agent      # dari root repository (editable install)
```

Ini memasang dua command ke folder Scripts Python (yang ada di PATH):

```bash
ladock            # = ladock_agent:main
ladock-agent      # alias
```

Editable install membuat `__file__` tetap menunjuk ke `ladock/cli/agent.py`,
sehingga agent tetap menemukan `REPO_ROOT` dan binari bundel di
`ladock/bin/<platform>` dari direktori kerja mana pun. Contoh dari sembarang
folder:

```bash
ladock                                   # dialog wizard (job dir = direktori saat ini)
ladock components target.pdb             # daftar komponen PDB
ladock dock --receptor r.pdb --ligand l.sdf --center 10 22 -4 --scoring vina --out out/
```

Saat wizard dijalankan, **default job directory adalah direktori kerja saat
ini** (bukan repo), dengan opsi memilih subdirektori di dalamnya atau memakai
contoh bawaan LADOCK. Untuk melepas: `pip uninstall ladock-agent`.

Contoh alur:

```text
Selamat datang di LADOCK Agent: agent docking profesional.

ladock > Apa tujuan docking?
1. Redocking
2. Virtual Screening

ladock > Dimana file target(s) berada?
1. receptor_ready/
2. target_input/
...
```

Agent ini **self-contained** — tidak mengimpor kode `ladock/desktop/`, melainkan
me-reimplementasi logika docking-nya sendiri, tetapi mengikuti aturan yang sama:

- preparasi receptor PDB ke PDBQT via Meeko, fallback MGLTools bila tersedia;
- konversi ligand multi-format (SDF/MOL2/SMILES/CSV/…) ke PDBQT via RDKit + Meeko,
  fallback OpenBabel (logika di-reimplementasi di dalam agent, bukan mengimpor desktop);
- docking `vina`, `vinardo`, `ad4`, dan `ad4gpu` dengan aturan fitur yang sama:
  - rigid dan flexible mode;
  - flexible receptor membutuhkan MGLTools `prepare_flexreceptor4.py`;
  - AD4/AD4-GPU membutuhkan MGLTools + AutoGrid4;
  - Vina/Vinardo berjalan native bila Vina tersedia.

## Tampilan terminal

LADOCK CLI punya tampilan khas: banner ASCII "LADOCK" dengan gradien
cyan→green, prompt `ladock ❯`, header `◆`, konfirmasi `✔`, dan panel ringkasan
protokol. Warna & glyph otomatis menyesuaikan:

- **warna** aktif hanya pada terminal interaktif (TTY); mati saat output di-pipe
  atau bila env `NO_COLOR` / `LADOCK_NO_COLOR` di-set;
- **glyph unicode** dipakai bila stdout UTF-8; jika tidak, otomatis fallback ke
  banner + simbol ASCII (`[OK]`, `->`, `+`/`|`).

Pada Windows, agent mengaktifkan output UTF-8 dan ANSI (virtual terminal)
secara otomatis saat start.

### Output docking yang bersih

Secara default CLI hanya menampilkan **output miliknya sendiri**, bukan perintah
mentah/engine:

- **Redocking** → tabel hasil akhir (Ligand · Engine · Mode · ΔG) + pose terbaik.
- **Virtual Screening** → **persen progres per ligan** (`[ 20%] … [100%]`) dengan
  tabel hasil yang tampil streaming, lalu peringkat terbaik.

Echo perintah subprocess dan stdout engine (vina/meeko/autogrid) disembunyikan.
Untuk debugging, tampilkan semuanya dengan `--verbose` (subcommand `dock`) atau
set env `LADOCK_VERBOSE=1`.

## Menjalankan Dialog Agent

Dari mana saja setelah `pip install ladock`:

```bash
ladock-cli
```

atau:

```bash
cd agent
python ladock_agent.py
```

Dialog akan memandu pilihan:

- tujuan docking: Redocking atau Virtual Screening;
- job directory dan subdirektori target;
- file target dan komponen receptor yang dipakai;
- native ligand untuk redocking, atau ligand library untuk virtual screening;
- center box;
- score function: Vina, Vinardo, AD4, AD4-GPU;
- mode rigid/flexible;
- MLSD (Multiple Ligand Simultaneous Docking) — hanya Virtual Screening + Vina/Vinardo;
- ukuran box;
- preset parameter pencarian;
- review protocol dengan opsi menjalankan, mengubah scoring, box, mode, file,
  atau membatalkan sebelum docking dijalankan.

## Menjalankan CLI Langsung

Dari root repository:

```bash
ladock-cli components receptor_ready/target_prepared.pdb
ladock-cli dock \
  --receptor receptor_ready/target_prepared.pdb \
  --ligand ligand_input/example.sdf \
  --center 10.5 22.0 -4.5 \
  --size 20 20 20 \
  --scoring vina vinardo \
  --out results/agent_run
```

Untuk native redocking, pusat box bisa dihitung dari ligand kristal dalam PDB:

```bash
ladock-cli dock \
  --receptor receptor_ready/complex_prepared.pdb \
  --ligand receptor_ready/complex_prepared.pdb \
  --native-ligand LIG \
  --native-chain A \
  --scoring vina \
  --out results/redock_agent
```

Untuk mode flexible:

```bash
ladock-cli dock \
  --receptor receptor_ready/target_prepared.pdb \
  --ligand ligand_input/example.sdf \
  --center 10.5 22.0 -4.5 \
  --mode flexible \
  --flex-distance 3.0 \
  --scoring vina \
  --out results/flex_agent
```

## MLSD (Multiple Ligand Simultaneous Docking)

MLSD mendokking **beberapa ligan berbeda sekaligus di dalam satu pocket**
(bukan satu-satu). Fitur ini mengikuti aturan LADOCK Desktop: **hanya untuk
Vina/Vinardo** (AD4/AD4-GPU tidak mendukung multi-ligan simultan) dan pada
konteks **Virtual Screening** (butuh ≥ 2 ligan pada library).

Cara kerja: agent menyiapkan seluruh library ke PDBQT, membentuk grup berukuran
`N` dari library (`combination` = set tak berurut, disarankan; `permutation` =
berurut, jauh lebih banyak grup), lalu setiap grup didokking bersama lewat satu
panggilan `vina --ligand g1.pdbqt g2.pdbqt … gN.pdbqt` (Vina 1.2 mendukung
multi-ligan). Bila scoring campuran (mis. `vina ad4`), Vina/Vinardo berjalan
sebagai grup MLSD sedangkan AD4/AD4-GPU tetap per-ligan.

Di wizard, langkah MLSD muncul otomatis saat tujuan = Virtual Screening,
scoring memuat Vina/Vinardo, dan tersedia ≥ 2 ligan. Lewat CLI:

```bash
ladock-cli dock \
  --receptor receptor_ready/target_prepared.pdb \
  --ligand ligand_input/frag_a.sdf \
  --ligand ligand_input/frag_b.sdf \
  --ligand ligand_input/frag_c.sdf \
  --center 10.5 22.0 -4.5 \
  --size 20 20 20 \
  --scoring vinardo \
  --simultaneous 2 \
  --arrangement combination \
  --out results/mlsd_run
```

Baris `results.csv` untuk grup MLSD memakai kolom `ligand` gabungan
(mis. `frag_a+frag_b`), dan output pose tiap grup berada di
`out/<mode>/MLSD_<gabungan>/<scoring>/`.

### Batas jumlah grup

Jumlah grup tumbuh sangat cepat: 500 ligan diambil 3 sekaligus = 20,7 juta grup,
dan **tiap grup adalah satu run Vina penuh**. Karena itu `--max-groups`
(default `5000`) menolak job yang terlalu besar sebelum docking dimulai:

```bash
--max-groups 20000   # naikkan batasnya
--max-groups 0       # matikan batas sepenuhnya
```

Wizard menampilkan jumlah grup lebih dulu dan menawarkan mengurangi ligan per
grup, mengganti susunan, atau melanjutkan dengan sadar.

## ADFR (AutoDockFR)

ADFR adalah engine kelima, dipakai lewat `--scoring adfr`. Berbeda dari yang
lain, binernya **tidak dibundel** — ADFRsuite berukuran ~500 MB dan dipasang
terpisah dari <https://ccsb.scripps.edu/adfr/downloads/>.

Agent mencarinya otomatis di `$ADFRSUITE_HOME`, `~/ADFRsuite*`, `/opt`,
`/usr/local`, dan di dalam `ladock/bin/<platform>/`. Nama direktori berversi
seperti `ADFRsuite-1.0` ikut dikenali. Kalau letaknya lain:

```bash
ladock-cli dock ... --scoring adfr --adfrsuite /path/ke/ADFRsuite-1.0
```

Alurnya dua tahap: **AGFR** membangun file target `.trg` dari receptor + box
(+ residu fleksibel), lalu **ADFR** mendocking tiap ligan ke target itu. Karena
target hanya bergantung pada receptor dan box — bukan pada ligan — satu target
dipakai ulang untuk seluruh library, jadi biaya AGFR dibayar sekali saja.

Energi diambil dari medan `FEB` pada pose keluaran (3 desimal), bukan dari tabel
ringkasan yang hanya 1 desimal.

Catatan: ADFR tidak mendukung MLSD, dan fleksibilitas rantai samping ditangani
AGFR sendiri lewat daftar residu — bukan lewat pemisahan rigid/flex ala
`prepare_flexreceptor4.py`.

## Performa

Tiga hal yang menentukan lama sebuah virtual screening:

**`--jobs` — docking paralel.** Default `1` (satu ligan pada satu waktu).
Setiap job menjalankan satu ligan penuh, jadi jaga `--jobs × --cpu` tidak
melebihi jumlah core:

```bash
ladock-cli dock ... --jobs 4 --cpu 1     # 4 ligan paralel, 1 thread Vina masing-masing
```

Urutan baris `results.csv` tidak bergantung pada `--jobs`; dengan `--seed` yang
sama, hasilnya identik berapa pun jumlah job.

**Grid AD4 di-cache.** Peta AutoGrid4 hanya bergantung pada receptor, box, dan
**himpunan tipe atom ligan** — bukan pada ligannya. Agent membangun peta sekali
per himpunan tipe atom lalu men-*hardlink*-nya ke direktori tiap ligan.
Statistiknya tercatat di `run.meta.json` (`ad4_grid_cache`).

**Konversi ligan dipakai ulang.** PDBQT yang sudah ada tidak dikonversi ulang,
sehingga menjalankan ulang job yang sama — atau mendocking satu library ke
banyak receptor — hanya membayar konversi satu kali.

**`--timeout`** (default `7200` detik, `0` = tanpa batas) membatasi tiap
panggilan engine. Tanpa ini, satu engine yang menggantung membekukan seluruh
screening semalaman.

## Output

Agent menulis:

- file docking mentah di `out/<mode>/<ligand>/<scoring>/`;
- `results.csv` berisi pose/energi terbaik yang bisa dibaca ulang oleh workflow LADOCK;
- `receptor_ready/` berisi receptor PDBQT hasil preparasi;
- `run.meta.json` berisi parameter, statistik cache grid, dan daftar output.

### Multi-receptor

Tiap receptor tetap punya sub-direktori dan `results.csv` sendiri, dan di level
atas agent menambahkan:

- `results_all.csv` — seluruh baris dari semua receptor, dengan kolom `receptor`
  di depan;
- `ranking.csv` — sama, tapi diurutkan energi terbaik lebih dulu (baris tanpa
  energi terbaca ditaruh paling akhir);
- `multi_receptor.summary.json` — receptor mana yang berhasil, mana yang
  dilewati, dan alasannya.

Ringkasan di terminal menampilkan ligan terbaik **per receptor** plus pemenang
keseluruhan, sehingga reverse screening tidak perlu membandingkan N file CSV
secara manual. Receptor yang gagal tidak menghentikan run — namanya dicatat di
ringkasan.

## Binari engine

Agent memakai binari docking dari (urutan pencarian):
1. `$LADOCK_AGENT_BIN/<platform>` (jika env di-set),
2. `$LADOCK_AGENT_BIN/<platform>`,
3. **`ladock/bin/<platform>`** — binari bundel LADOCK Desktop (default, tanpa setup).

Sehingga di repository ini agent langsung memakai `ladock/bin/` tanpa menyalin apa pun.

## Validasi tool dini

Setelah memilih scoring + backend, wizard menampilkan **ketersediaan tiap engine
terpilih** (mis. di Windows native hanya Vina/Vinardo; AD4/AD4-GPU butuh WSL +
MGLTools) dan menawarkan perbaikan (aktifkan WSL / hapus engine / lanjut / batal)
sebelum protokol lainnya dikonfigurasi. Validasi ulang tetap dijalankan saat "Jalankan".

## Catatan

Agent ini tidak memakai LLM, tidak membuat keputusan bebas, dan hanya mengikuti
aturan deterministik dari parameter CLI plus aturan docking yang sama seperti
`ladock/desktop/` (tetapi di-reimplementasi mandiri, tanpa mengimpor `ladock/desktop/`).
