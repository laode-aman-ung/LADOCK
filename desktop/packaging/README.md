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

## Providing the binaries

`bin/` (≈800 MB) is **not** committed to git. Before building a real release,
populate `desktop/bin/<platform>/` (from your archive, a private release asset,
Git LFS, or by downloading each engine from upstream). The CI workflow has a
`Fetch bundled binaries` step to wire this up.

## Status / caveats

- The **staging step is tested** and works.
- The **freeze + installer steps run on CI** and typically need a round or two of
  iteration on the real runners: PyInstaller hidden‑imports/data for
  PySide6‑WebEngine, RDKit and Meeko, and code‑signing/notarization for macOS.
- Resource lookup is freeze‑aware via `core/resources.py:resource_root()`
  (`bin/` etc. resolve from `sys._MEIPASS` when frozen). If a freeze can't find a
  resource, route that path through `resource_root()` too.
