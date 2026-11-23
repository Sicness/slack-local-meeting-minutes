#!/bin/bash
# Builds MeetingRecorder.app: swift build, .app with Info.plist, code signing.
# Signs with the "Meeting Assistant Dev" keychain certificate (macOS permissions
# survive rebuilds), otherwise ad-hoc. SIGN_IDENTITY overrides the choice.
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${CONFIG:-release}"
BUNDLE_ID="local.meetingassistant.recorder"
IDENTITY_NAME="Meeting Assistant Dev"
APP="build/MeetingRecorder.app"
TMP="build/.staging/MeetingRecorder.app"

swift build -c "$CONFIG" --product MeetingRecorder >&2
BIN="$(swift build -c "$CONFIG" --show-bin-path)/MeetingRecorder"

# Build alongside and swap the whole bundle: macOS treats copying over a running
# recorder's binary as a broken signature and kills the process.
rm -rf "$TMP"
mkdir -p "$TMP/Contents/MacOS"
cp "$BIN" "$TMP/Contents/MacOS/MeetingRecorder"
cp Resources/Info.plist "$TMP/Contents/Info.plist"

if [[ -n "${SIGN_IDENTITY:-}" ]]; then
  identity="$SIGN_IDENTITY"
else
  # No -v: an untrusted self-signed certificate is fine for signing.
  # Use the hash, not the name: several certificates may share the name.
  identity="$(security find-identity -p codesigning 2>/dev/null \
    | awk -v name="\"$IDENTITY_NAME\"" 'index($0, name) { print $2; exit }')"
  if [[ -z "$identity" ]]; then
    identity="-"
    echo "WARNING: certificate \"$IDENTITY_NAME\" not found, signing ad-hoc." >&2
    echo "         After the next rebuild, macOS will stop granting the app microphone and audio access." >&2
  fi
fi

codesign --force --sign "$identity" --identifier "$BUNDLE_ID" "$TMP" >&2
codesign -d -r- "$TMP" 2>&1 | grep 'designated' >&2 || true
rm -rf "$APP"
mv "$TMP" "$APP"
echo "$PWD/$APP"
