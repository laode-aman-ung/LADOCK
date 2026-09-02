#!/usr/bin/env bash
# Build a Linux AppImage from the PyInstaller one-dir output.
# Usage: build_appimage.sh <dist_dir> <version> [appversion]
#   dist_dir = build/dist-linux/LADOCK   (the PyInstaller COLLECT output)
set -euo pipefail

DIST="${1:?dist dir required}"
VERSION="${2:-linux}"
# Version comes from the package, so the installer filenames cannot drift away
# from what pip reports. Pass it explicitly as an argument to override.
_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_pkgver="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$_root/ladock/__init__.py" 2>/dev/null | head -1)"
APPVER="${3:-${_pkgver:-0.0.0}}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$(cd "$DIST/../.." && pwd)/installers"
mkdir -p "$OUT"

APPDIR="$(mktemp -d)/LADOCK.AppDir"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

cp -a "$DIST/." "$APPDIR/usr/bin/"

cat > "$APPDIR/usr/share/applications/ladock.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=LADOCK Desktop
Exec=LADOCK
Icon=ladock
Categories=Science;Education;
DESK
cp "$APPDIR/usr/share/applications/ladock.desktop" "$APPDIR/ladock.desktop"

ICON="$DIST/gui/assets/ladock.png"
[ -f "$ICON" ] && cp "$ICON" "$APPDIR/usr/share/icons/hicolor/256x256/apps/ladock.png" \
              && cp "$ICON" "$APPDIR/ladock.png" || : > "$APPDIR/ladock.png"

cat > "$APPDIR/AImRun" <<'RUN'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/LADOCK" "$@"
RUN
mv "$APPDIR/AImRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

# appimagetool must be on PATH (installed by the CI workflow).
#
# It refuses to run without ARCH and only says so at the very end, after the
# whole AppDir has been assembled — set it here so the script works the same
# way on a developer machine as it does in CI.
export ARCH="${ARCH:-x86_64}"
appimagetool "$APPDIR" "$OUT/LADOCK-${APPVER}-${VERSION}-x86_64.AppImage"
echo "AppImage: $OUT/LADOCK-${APPVER}-${VERSION}-x86_64.AppImage"
