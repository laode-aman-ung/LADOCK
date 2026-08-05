# LADOCK Agent

`agent/` berisi **LADOCK docking CLI agent** berbasis aturan (non-LLM).
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

Editable install membuat `__file__` tetap menunjuk ke `agent/ladock_agent.py`,
sehingga agent tetap menemukan `REPO_ROOT` dan binari bundel di
`desktop/bin/<platform>` dari direktori kerja mana pun. Contoh dari sembarang
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

Agent ini **self-contained** — tidak mengimpor kode `desktop/`, melainkan
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

Dari root repository atau dari folder `agent/`:

```bash
python agent/ladock_agent.py
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
python agent/ladock_agent.py components receptor_ready/target_prepared.pdb
python agent/ladock_agent.py dock \
  --receptor receptor_ready/target_prepared.pdb \
  --ligand ligand_input/example.sdf \
  --center 10.5 22.0 -4.5 \
  --size 20 20 20 \
  --scoring vina vinardo \
  --out results/agent_run
```

Untuk native redocking, pusat box bisa dihitung dari ligand kristal dalam PDB:

```bash
python agent/ladock_agent.py dock \
  --receptor receptor_ready/complex_prepared.pdb \
  --ligand receptor_ready/complex_prepared.pdb \
  --native-ligand LIG \
  --native-chain A \
  --scoring vina \
  --out results/redock_agent
```

Untuk mode flexible:

```bash
python agent/ladock_agent.py dock \
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
python agent/ladock_agent.py dock \
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

## Output

Agent menulis:

- file docking mentah di `out/<mode>/<ligand>/<scoring>/`;
- `results.csv` berisi pose/energi terbaik yang bisa dibaca ulang oleh workflow LADOCK;
- `run.meta.json` berisi parameter, file intermediate, dan daftar output.

## Binari engine

Agent memakai binari docking dari (urutan pencarian):
1. `$LADOCK_AGENT_BIN/<platform>` (jika env di-set),
2. `agent/bin/<platform>`,
3. **`desktop/bin/<platform>`** — binari bundel LADOCK Desktop (default, tanpa setup).

Sehingga di repository ini agent langsung memakai `desktop/bin/` tanpa menyalin apa pun.

## Validasi tool dini

Setelah memilih scoring + backend, wizard menampilkan **ketersediaan tiap engine
terpilih** (mis. di Windows native hanya Vina/Vinardo; AD4/AD4-GPU butuh WSL +
MGLTools) dan menawarkan perbaikan (aktifkan WSL / hapus engine / lanjut / batal)
sebelum protokol lainnya dikonfigurasi. Validasi ulang tetap dijalankan saat "Jalankan".

## Catatan

Agent ini tidak memakai LLM, tidak membuat keputusan bebas, dan hanya mengikuti
aturan deterministik dari parameter CLI plus aturan docking yang sama seperti
`desktop/` (tetapi di-reimplementasi mandiri, tanpa mengimpor `desktop/`).
