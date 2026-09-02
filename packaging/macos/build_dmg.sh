#!/usr/bin/env bash
# Build a macOS .dmg from a PyInstaller .app bundle.
# Usage: build_dmg.sh <app_bundle> [appversion]
#   app_bundle = build/dist-mac/LADOCK.app  (PyInstaller with a BUNDLE step,
#   or a .app assembled from the one-dir output)
set -euo pipefail

APP="${1:?path to LADOCK.app required}"
# Version comes from the package, so the installer filenames cannot drift away
# from what pip reports. Pass it explicitly as an argument to override.
_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_pkgver="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$_root/ladock/__init__.py" 2>/dev/null | head -1)"
APPVER="${2:-${_pkgver:-0.0.0}}"
OUT="$(cd "$APP/../.." && pwd)/installers"
mkdir -p "$OUT"
DMG="$OUT/LADOCK-${APPVER}-mac.dmg"

# Prefer create-dmg if present (nicer layout); fall back to hdiutil.
if command -v create-dmg >/dev/null 2>&1; then
  create-dmg \
    --volname "LADOCK Desktop" \
    --app-drop-link 480 170 \
    --icon "$(basename "$APP")" 160 170 \
    --window-size 640 360 \
    "$DMG" "$APP"
else
  STAGE="$(mktemp -d)/dmgroot"
  mkdir -p "$STAGE"
  cp -a "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "LADOCK Desktop" -srcfolder "$STAGE" \
      -ov -format UDZO "$DMG"
fi
echo "dmg: $DMG"
