# LADOCK Agent

`ladock/cli/` berisi **LADOCK docking CLI agent** berbasis aturan (non-LLM).
Mode utama agent adalah dialog terminal dengan pertanyaan tertutup:

```bash
python -m ladock.cli.agent
```

## Instalasi sebagai command global

Satu paket memasang kedua front-end:

```bash
pip install ladock
```

Command yang tersedia di PATH sesudahnya:

```bash
ladock-cli        # agen docking berbasis aturan (bukan LLM)
ladock-desktop    # workstation GUI
```

Untuk pengembangan, pasang dari root repository dalam mode editable:

```bash
pip install -e .
```

Mode editable membuat `__file__` tetap menunjuk ke `ladock/cli/agent.py`,
sehingga agen tetap menemukan binari bundel di `ladock/bin/<platform>` dari
direktori kerja mana pun. Contoh dari sembarang folder:

```bash
ladock-cli                                   # dialog wizard (job dir = direktori saat ini)
ladock-cli components target.pdb             # daftar komponen PDB
ladock-cli dock --receptor r.pdb --ligand l.sdf --center 10 22 -4 --scoring vina --out out/
```

Saat wizard dijalankan, **default job directory adalah direktori kerja saat
ini** (bukan repo), dengan opsi memilih subdirektori di dalamnya atau memakai
contoh bawaan LADOCK. Untuk melepas: `pip uninstall ladock`.

Contoh alur:

```text
Selamat datang di LADOCK Agent: agent docking profesional.

ladock-cli > Apa tujuan docking?
1. Redocking
2. Virtual Screening

ladock-cli > Dimana file target(s) berada?
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
python -m ladock.cli.agent
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

## Berkas konfigurasi

`ladock-cli dock` membaca `ladock_config.txt` dari direktori kerja bila ada.
Formatnya `key: value` dengan komentar `#` — sengaja sama persis dengan
`gmx_config.txt` milik LAGMX, karena dua alat dalam satu alur kerja tidak
seharusnya memaksa pengguna belajar dua dialek konfigurasi.

```text
# Kantong substrat berada di antarmuka subunit, jadi reseptornya trimer utuh.
receptor: target/siap/thim_trimer_noMg.pdb
ligand: ligan/pustaka_3d.sdf
size: 16 16 16
scoring: vina vinardo ad4 ad4gpu adfr
mode: rigid flexible
flex_distance: 6.0
exhaustiveness: 32
cpu: 4
jobs: 8
seed: 42
```

**Flag di command line selalu menang.** Yang ditaruh di berkas adalah hal yang
sama untuk semua run; yang berbeda per run tetap diberikan lewat argumen:

```bash
ladock-cli dock --center 21.63 63.32 35.83 --out hasil/kantong1
ladock-cli dock --center 19.80 62.89 55.83 --out hasil/kantong2
```

Karena `receptor`, `ligand`, dan `out` bisa berasal dari berkas konfigurasi,
ketiganya tidak lagi wajib di command line — tetapi tetap diperiksa setelah
konfigurasi digabungkan, dan pesan galatnya menyebut kedua cara mengisinya.

Berkas lain dipakai dengan `--config`. Kunci yang tidak dikenali dilaporkan
lalu diabaikan, bukan didiamkan:

```bash
ladock-cli dock --config protokol/skrining_ketat.txt --out hasil/ketat
```

Kunci yang dikenali: `receptor`, `ligand`, `out`, `center`, `size`, `scoring`,
`mode`, `native_ligand`, `native_chain`, `native_resseq`, `flex_residue`,
`flex_distance`, `simultaneous`, `arrangement`, `max_groups`, `spacing`,
`exhaustiveness`, `ad4_exhaustiveness`, `n_poses`, `energy_range`, `cpu`,
`jobs`, `grid_cache`, `seed`, `ga_pop_size`, `cluster_rmsd`, `timeout`,
`vina`, `autogrid4`, `autodock4`, `autodock_gpu`, `mgltools`, `adfrsuite`,
`pythonsh`, `wsl_distro`.

## Mode fleksibel

Residu fleksibel dipilih dengan `--flex-residue chain:RESNAME:resseq` yang
bisa diulang, atau otomatis dalam radius `--flex-distance` dari **pusat kotak**.

Dua hal yang perlu diketahui tentang radius otomatis:

- Radiusnya diukur dari satu titik pusat kotak, dan pusat kotak menurut
  definisinya berada di ruang kosong rongga. Nilai kecil karena itu tidak
  menghasilkan apa-apa: pada situs ikatan yang normal, 3,0 Å memberi nol
  residu sedangkan 6,0 Å memberi belasan.
- Alanin dan glisin disaring otomatis. Keduanya tidak punya rantai samping yang
  bisa diputar, `prepare_flexreceptor4.py` memang sudah membuangnya diam-diam,
  tetapi `agfr` menolak seluruh job bila menemukannya — sehingga tanpa
  penyaringan ini Vina berjalan dan ADFR mati pada daftar residu yang sama.
  Yang dibuang dicetak ke layar.

Untuk situs ikatan di **antarmuka dua rantai**, daftar residu boleh memuat
rantai mana pun; keduanya diteruskan utuh ke `prepare_flexreceptor4.py`. Residu
yang diminta tetapi tidak muncul di berkas flex dilaporkan sebagai peringatan,
karena split yang tidak lengkap tetap menghasilkan docking dan tetap menulis
energi yang tampak masuk akal.

## Ligan makrosiklik dan AutoGrid4

Meeko membuka cincin makrosiklik dan menyambungnya kembali dengan pseudo-atom
bertipe `CG0`/`G0`. Vina dan Vinardo menilainya tanpa masalah; **AutoGrid4
menolak tipe itu mentah-mentah**:

```
autogrid4: ERROR:  unknown ligand atom type CG0
add parameters for it to the parameter library first!
```

Akibatnya, pada screening dengan `ad4` atau `ad4gpu`, setiap ligan makrosiklik
kehilangan skornya. Cincin berukuran ≥ 8 atom sudah dihitung makrosiklik, jadi
ini menyentuh banyak seskuiterpen dan diterpen — pada satu pustaka bahan alam
359 senyawa, 24 di antaranya terdampak.

```bash
ladock-cli dock ... --scoring ad4gpu --rigid-macrocycles
```

atau `rigid_macrocycles: yes` di `ladock_config.txt`.

**Ini bukan perbaikan tanpa biaya.** Dengan cincin dibuat rigid, konformasi
cincin tidak lagi disampling dan hasilnya bergantung pada konformer masukan.
Pada uji α-humulen, skor Vina berpindah dari −4,10 (cincin fleksibel) ke −6,11
(cincin rigid). Bila Anda memakai opsi ini, pakailah untuk **seluruh** pustaka,
bukan sebagian, supaya ligan tetap sebanding satu sama lain.

## Kegagalan sebagian pada screening

Satu ligan yang gagal pada satu engine **tidak** menghentikan batch. Sebelumnya
begitu: satu ligan makrosiklik membuat run 359 ligan mati di 8%, padahal Vina
dan Vinardo sudah menilai ligan itu dengan baik.

Sekarang kegagalan dicatat per ligan per engine, hasil engine lain untuk ligan
yang sama tetap disimpan, dan ringkasannya dicetak sebelum tabel hasil:

```
  1 kegagalan pada 1 ligan (ad4/ad4gpu: 1)
       1x  autogrid4: ERROR:  unknown ligand atom type CG0
    Rincian lengkap di run.meta.json (kunci 'failures').
```

Rincian per ligan masuk ke `run.meta.json` pada kunci `failures`. Kegagalan
tidak pernah didiamkan — kalau tabel hasil punya lubang, ringkasan itu yang
memberi tahu di mana dan mengapa.

## MLSD (Multiple Ligand Simultaneous Docking)

MLSD mendokking **beberapa ligan berbeda sekaligus di dalam satu pocket**
(bukan satu-satu). Fitur ini mengikuti aturan LADOCK: **hanya untuk
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
3. **`ladock/bin/<platform>`** — binari bundel LADOCK (default, tanpa setup).

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
