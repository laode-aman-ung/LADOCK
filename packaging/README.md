# LADOCK — release packaging

Produces a **separate installer per version**, each bundling only the binaries
that version needs (avoiding an 800 MB download for platforms that don't use it).

## Versions

| Version | Engines | Bundled `bin/` | Approx. size | Installer |
|---|---|---|---|---|
| `windows` | Vina, Vinardo | `windows` | small | `.exe` (Inno Setup) |
| `windows-hybrid` | Vina, Vinardo, AD4, AD‑GPU* | `windows` + `linux`† | medium | `.exe` (Inno Setup) |
| `linux` | Vina, Vinardo, AD4, AD‑GPU* | `linux`† | medium | `.deb` + `.AppImage` |
| `mac` | Vina, Vinardo | `mac` | small | `.dmg` |

\* AD‑GPU also needs CUDA (CUDA‑on‑WSL for the hybrid variant).
† **ADFRsuite (~500 MB) is excluded by default** — it only provides ADFR/AGFR and
the OpenBabel fallback, both non‑essential now that preparation uses Meeko. Pass
`--with-adfrsuite` to include it.

`windows` and `windows-hybrid` are the **same application** — they differ only in
the bundled binaries. Hybrid keeps the GUI + prep native (so the embedded 3D
preview works) and dispatches AD4/AD‑GPU to WSL.

## Pipeline

```
build_release.py <version>        # 1. stage source + selected bin/  → build/stage-<v>/
   └─ (PyInstaller via ladock.spec)  # 2. freeze                        → build/dist-<v>/LADOCK
windows/ladock.iss (ISCC)          # 3a. Windows installer            → build/installers/*.exe
linux/build_deb.sh / build_appimage.sh   # 3b. Linux                  → *.deb / *.AppImage
macos/build_dmg.sh                 # 3c. macOS                        → *.dmg
```

### Local (staging only — no build tools needed)
```bash
python packaging/build_release.py windows --stage-only
```

### Full build
Requires, per OS: PyInstaller, plus **Inno Setup** (Windows), **appimagetool** +
`dpkg-deb` (Linux), **create-dmg** (macOS). Because these are per‑OS, the real
installers are produced on CI:

```
.github/workflows/build-installers.yml   # matrix: windows / ubuntu / macos
```
Run it via *Actions → Build installers → Run workflow*, or push a `v*` tag.

## Providing the binaries (use YOUR validated `bin/`, not upstream)

`bin/` (≈800 MB) is **not** committed to git. The build uses the exact binaries
you already validated — it does **not** re-download engines from Scripps/apt/etc.
(too fragile). The flow is: package your local `bin/` once, host the archives,
and let CI pull them.

**1. Package (run once, on your machine that has the full `bin/`):**
```bash
python packaging/package_binaries.py           # → build/bin-archives/bin-<platform>.tar.gz
```
Each archive has a top-level `<platform>/` folder; exec bits are forced for
linux/mac so they work even when packaged from Windows.

**2. Host** the three archives on your storage / download host, e.g.
`https://ladock.ladeep.id/bin/bin-windows.tar.gz` (or a private bucket).

**3. Fetch** (CI runs this automatically before each build):
```bash
python -m ladock.binaries <platform>            # downloads + extracts your archive
```
Configure the location with repo secrets (both optional; default base
`https://ladock.ladeep.id/bin`):
- `LADOCK_BIN_BASE_URL` — base URL of your archives
- `LADOCK_BIN_TOKEN` — bearer token for private storage

`build_release.py` prunes ADFRsuite at stage time, so your hosted `bin-linux`
archive may include it or not — either works.

## Hosting the downloads (self-hosted)

Downloads are served from **your own host / cloud** (domain `ladock.ladeep.id`),
not GitHub. The website links (`website/docs.html` → download cards) point to:

```
https://ladock.ladeep.id/downloads/LADOCK-0.3.0-windows-setup.exe
https://ladock.ladeep.id/downloads/LADOCK-0.3.0-windows-hybrid-setup.exe
https://ladock.ladeep.id/downloads/ladock-desktop_0.3.0_linux_amd64.deb
https://ladock.ladeep.id/downloads/LADOCK-0.3.0-linux-x86_64.AppImage
https://ladock.ladeep.id/downloads/LADOCK-0.3.0-mac.dmg
```

So serve the built installers at `https://ladock.ladeep.id/downloads/` — either as
real files under that path, or by pointing it at your object storage/CDN. Two ways
to populate it:

1. **Manual** — download the CI artifacts and upload them to `…/downloads/`.
2. **Automatic** — the CI `publish` job uploads every installer via **rclone** to
   your storage. Set two repo secrets:
   - `LADOCK_UPLOAD_REMOTE` — e.g. `r2:my-bucket/downloads`
   - `RCLONE_CONFIG_B64` — base64 of your `rclone.conf` (S3 / Cloudflare R2 /
     Backblaze B2 / SFTP / WebDAV / …)
   Then serve that bucket/dir at the site's `/downloads/` (or change the links to
   absolute URLs if the files live on a different domain/CDN).

Keep the installer filenames in sync with the download links (versioned as
`0.3.0`); bump both when you release a new version.

## Status / caveats

- The **staging step is tested** and works.
- The **freeze + installer steps run on CI** and typically need a round or two of
  iteration on the real runners: PyInstaller hidden‑imports/data for
  PySide6‑WebEngine, RDKit and Meeko, and code‑signing/notarization for macOS.
- Resource lookup is freeze‑aware via `core/resources.py:resource_root()`
  (`bin/` etc. resolve from `sys._MEIPASS` when frozen). If a freeze can't find a
  resource, route that path through `resource_root()` too.
