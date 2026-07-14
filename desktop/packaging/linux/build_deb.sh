#!/usr/bin/env bash
# Build a Debian .deb from the PyInstaller one-dir output.
# Usage: build_deb.sh <dist_dir> <version> [appversion]
set -euo pipefail

DIST="${1:?dist dir required}"
VERSION="${2:-linux}"
APPVER="${3:-2.0.0}"
OUT="$(cd "$DIST/../.." && pwd)/installers"
mkdir -p "$OUT"

PKG="$(mktemp -d)/ladock"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/ladock" "$PKG/usr/bin" "$PKG/usr/share/applications"
cp -a "$DIST/." "$PKG/opt/ladock/"

cat > "$PKG/DEBIAN/control" <<CTRL
Package: ladock-desktop
Version: ${APPVER}
Section: science
Priority: optional
Architecture: amd64
Maintainer: La Ode Aman <laode_aman@ung.ac.id>
Description: LADOCK Desktop — molecular docking workstation (${VERSION})
CTRL

cat > "$PKG/usr/bin/ladock" <<'LAUNCH'
#!/bin/sh
exec /opt/ladock/LADOCK "$@"
LAUNCH
chmod +x "$PKG/usr/bin/ladock"

cat > "$PKG/usr/share/applications/ladock.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=LADOCK Desktop
Exec=ladock
Icon=/opt/ladock/gui/assets/ladock.png
Categories=Science;Education;
DESK

dpkg-deb --build --root-owner-group "$PKG" \
    "$OUT/ladock-desktop_${APPVER}_${VERSION}_amd64.deb"
echo "deb: $OUT/ladock-desktop_${APPVER}_${VERSION}_amd64.deb"
