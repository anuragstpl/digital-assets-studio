#!/usr/bin/env bash
# Build the macOS app bundle and a drag-to-install DMG.
#
#   bash packaging/build_macos.sh
#
# Produces:
#   dist/Artalo Digi Suit.app
#   dist/ArtaloDigiSuit-<version>-macos-<arch>.dmg
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Installing build dependencies"
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r requirements.txt pyinstaller >/dev/null

VERSION=$(python3 -c "from digital_assets_studio.config import APP_VERSION; print(APP_VERSION)")
ARCH=$(uname -m)
echo "==> Building Artalo Digi Suit $VERSION for $ARCH"

# PyInstaller wants .icns on macOS; iconutil ships with the OS
if [ ! -f packaging/icon.icns ]; then
  echo "==> Making the app icon"
  rm -rf packaging/icon.iconset
  mkdir -p packaging/icon.iconset
  for size in 16 32 64 128 256 512; do
    sips -z $size $size packaging/icon.png \
      --out "packaging/icon.iconset/icon_${size}x${size}.png" >/dev/null
    sips -z $((size*2)) $((size*2)) packaging/icon.png \
      --out "packaging/icon.iconset/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns packaging/icon.iconset -o packaging/icon.icns
  rm -rf packaging/icon.iconset
fi

rm -rf dist build
pyinstaller packaging/das.spec --noconfirm

APP="dist/Artalo Digi Suit.app"
[ -d "$APP" ] || { echo "The .app bundle was not produced"; exit 1; }

echo "==> Verifying the build"
"$APP/Contents/MacOS/Artalo Digi Suit" --selftest

# Ad-hoc signature. Without it Gatekeeper reports the app as damaged on Apple
# silicon rather than merely unidentified. It is not notarisation: users still
# need right-click - Open the first time.
echo "==> Ad-hoc signing"
codesign --force --deep --sign - "$APP"

echo "==> Building the DMG"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist/ArtaloDigiSuit-${VERSION}-macos-${ARCH}.dmg"
hdiutil create -volname "Artalo Digi Suit" -srcfolder "$STAGE" \
  -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo
echo "App: $APP"
echo "DMG: $DMG"
