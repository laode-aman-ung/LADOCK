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

**2. Host** the three archives. They are attached to the GitHub release, e.g.
`https://github.com/laode-aman-ung/LADOCK/releases/download/v0.3.0/bin-windows.tar.gz`
(a private bucket works too — see `LADOCK_BIN_BASE_URL` below).

**3. Fetch** (CI runs this automatically before each build):
```bash
python -m ladock.binaries <platform>            # downloads + extracts your archive
```
Configure the location with repo secrets (both optional; the default base is
the release assets for the current version, see `_DEFAULT_BASE` in
`ladock/binaries.py`):
- `LADOCK_BIN_BASE_URL` — base URL of your archives
- `LADOCK_BIN_TOKEN` — bearer token for private storage

`build_release.py` prunes ADFRsuite at stage time, so your hosted `bin-linux`
archive may include it or not — either works.

## Hosting the downloads

Two channels exist. **GitHub releases is the one in use.**

### GitHub releases (primary)

`ladock/binaries.py` fetches engine archives from release assets by default:

```
https://github.com/laode-aman-ung/LADOCK/releases/download/v0.3.0/...
```

No separate web host, no certificate to keep alive, and the files are already
public. The website's download cards (`ladock-website`, `docs.html`) link to
the releases page rather than to fixed filenames, so they survive a version
bump without editing.

Publishing a release:

```
gh release create v0.3.1 --title "LADOCK 0.3.1" --notes "..."
gh release upload  v0.3.1 <installers and engine archives>
```

The installer jobs in `build-installers.yml` publish their output as workflow
artifacts, so download those and attach them to the release.

### Self-hosted (optional, currently unused)

`build-installers.yml` also has an rclone step that copies the installers to
storage of your choice, driven by two repository secrets:

- `LADOCK_UPLOAD_REMOTE` — e.g. `r2:my-bucket/downloads`
- `RCLONE_CONFIG_B64` — base64 of your `rclone.conf`

That was meant to serve `https://ladock.ladeep.id/downloads/`. As of
2026-09-02 that path returns **404** and the live site no longer links to it,
so the step is dormant. Point new links at the releases page instead; if you
revive self-hosting, keep the installer filenames in sync with whatever links
to them.

`LADOCK_BIN_BASE_URL` overrides the engine download base, and
`LADOCK_BIN_TOKEN` adds an Authorization header for a private host.

## Status / caveats

- The **staging step is tested** and works.
- The **freeze + installer steps run on CI** and typically need a round or two of
  iteration on the real runners: PyInstaller hidden‑imports/data for
  PySide6‑WebEngine, RDKit and Meeko, and code‑signing/notarization for macOS.
- Resource lookup is freeze‑aware via `core/resources.py:resource_root()`
  (`bin/` etc. resolve from `sys._MEIPASS` when frozen). If a freeze can't find a
  resource, route that path through `resource_root()` too.
