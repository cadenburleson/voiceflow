#!/usr/bin/env bash
# Build a signed, notarized, stapled VoiceFlow.app and package it as a DMG.
#
# Produces a download anyone can double-click — no Gatekeeper warning, no xattr.
# Run from the repo's packaging/ dir:  ./build_release.sh
#
# Prerequisites (one-time):
#   - PyInstaller in the venv:   uv pip install pyinstaller
#   - A "Developer ID Application" cert in your keychain.
#   - An App Store Connect API key (.p8) with App Manager or Admin role.
#
# Set these for your account before running (or edit the defaults):
#   SIGN_ID   : the Developer ID Application identity
#   ASC_KEY   : path to the App Store Connect API .p8
#   ASC_KEY_ID: the key's ID (the AuthKey_<ID>.p8 suffix)
#   ASC_ISSUER: your issuer UUID (App Store Connect > Users and Access > Integrations)
set -euo pipefail
cd "$(dirname "$0")"

VERSION="0.2.0"
SIGN_ID="${SIGN_ID:-Developer ID Application: Caden Burleson (9GW225XZWY)}"
ASC_KEY="${ASC_KEY:-$HOME/Downloads/AuthKey_VLV3LKZZY2.p8}"
ASC_KEY_ID="${ASC_KEY_ID:-VLV3LKZZY2}"
ASC_ISSUER="${ASC_ISSUER:-581ca664-267d-4498-8d37-9aaf51609de4}"

APP="dist/VoiceFlow.app"
ENT="entitlements.plist"
DMG="dist/VoiceFlow-${VERSION}.dmg"

echo "==> 1/5  PyInstaller build"
rm -rf build dist
../.venv/bin/pyinstaller voiceflow.spec --noconfirm

echo "==> 2/5  Code-sign (hardened runtime + entitlements, inner-out)"
# Sign every nested dylib/.so first, then the executable and the bundle.
find "$APP/Contents" -type f \( -name "*.dylib" -o -name "*.so" \) -print0 \
  | xargs -0 codesign --force --timestamp --options runtime --sign "$SIGN_ID"
codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$SIGN_ID" "$APP/Contents/MacOS/VoiceFlow"
codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$SIGN_ID" "$APP"
codesign --verify --deep --strict --verbose=1 "$APP"

echo "==> 3/5  Notarize (waits for Apple's verdict)"
ZIP="dist/VoiceFlow-notarize.zip"
ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --key "$ASC_KEY" --key-id "$ASC_KEY_ID" --issuer "$ASC_ISSUER" --wait

echo "==> 4/5  Staple the ticket"
xcrun stapler staple "$APP"
spctl -a -vv -t execute "$APP"

echo "==> 5/5  Package DMG (app + Applications symlink)"
STAGE="$(mktemp -d)"
ditto "$APP" "$STAGE/VoiceFlow.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "VoiceFlow" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE" "$ZIP"

echo "Done: $DMG"
echo "Upload with:  gh release upload v${VERSION} $DMG --clobber"
