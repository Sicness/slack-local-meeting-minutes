#!/bin/bash
# Installs from a clone: checks the tools, builds everything, creates config.toml.
# Installs nothing system-wide unless --applications is passed.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
CERTIFICATE="Meeting Assistant Dev"
MODEL="qwen3.5:9b"
ALLOW_ADHOC=0
APPLICATIONS=0

usage() {
  cat <<EOF
Usage: scripts/install.sh [--applications] [--allow-adhoc]

  --applications  copy the app to /Applications
  --allow-adhoc   build without the signing certificate
                  (macOS forgets the app's permissions after every rebuild)
EOF
}

for arg in "$@"; do
  case "$arg" in
    --applications) APPLICATIONS=1 ;;
    --allow-adhoc) ALLOW_ADHOC=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

die() { echo "Error: $*" >&2; exit 1; }
ask() { [[ -t 0 ]] && read -r -p "$1 " REPLY && [[ "$REPLY" =~ ^[Yy] ]]; }

echo "==> Checking the system"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || die "needs a Mac with Apple Silicon"
MACOS="$(sw_vers -productVersion)"
(( ${MACOS%%.*} >= 15 )) || die "needs macOS 15 or later, this is $MACOS"

missing=()
command -v swift >/dev/null || missing+=("Swift 6.2+: xcode-select --install")
command -v uv >/dev/null || missing+=("uv: brew install uv")
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || missing+=("ffmpeg: brew install ffmpeg")
if (( ${#missing[@]} )); then
  printf 'Missing:\n' >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 1
fi
SWIFT="$(swift --version 2>&1 | sed -nE 's/.*Swift version ([0-9]+\.[0-9]+).*/\1/p' | head -1)"
[[ "$(printf '%s\n6.2\n' "$SWIFT" | sort -V | head -1)" == 6.2 ]] || die "needs Swift 6.2 or later, found ${SWIFT:-none}"

if [[ -z "${SIGN_IDENTITY:-}" ]] && ! security find-identity -p codesigning 2>/dev/null | grep -q "\"$CERTIFICATE\""; then
  if (( ! ALLOW_ADHOC )); then
    cat >&2 <<EOF

No code signing certificate "$CERTIFICATE". Without it macOS forgets the app's
microphone and audio permissions after every rebuild. Create it once:

  1. Open Keychain Access
  2. Keychain Access > Certificate Assistant > Create a Certificate...
  3. Name: $CERTIFICATE, Identity Type: Self-Signed Root, Certificate Type: Code Signing

Then run this script again, or pass --allow-adhoc to continue without it.
EOF
    exit 1
  fi
  echo "    Continuing without a certificate (ad-hoc signature)"
fi

echo "==> Installing Python dependencies"
uv sync --quiet --project pipeline

echo "==> Building the diarizer (the first build takes about 5 minutes)"
diarizer/scripts/build.sh >/dev/null

echo "==> Building the recorder"
recorder/scripts/build-app.sh >/dev/null
APP="$ROOT/recorder/build/MeetingRecorder.app"

if (( APPLICATIONS )); then
  echo "==> Copying the app to /Applications"
  rm -rf /Applications/MeetingRecorder.app
  ditto "$APP" /Applications/MeetingRecorder.app
  APP=/Applications/MeetingRecorder.app
fi

MA=(uv run --quiet --project pipeline ma)
CONFIG="${MA_HOME:-$HOME/MeetingAssistant}/config.toml"
if [[ -e "$CONFIG" ]]; then
  echo "==> Keeping the existing $CONFIG"
  if (( APPLICATIONS )) && ! grep -qE '^pipeline *=' "$CONFIG"; then
    echo "    Add this to its [recorder] section so the app can find the pipeline:"
    echo "    pipeline = \"$ROOT/pipeline\""
  fi
else
  echo "==> Creating $CONFIG"
  NAME="" LANGUAGE="en" VAULT="$HOME/Documents/Obsidian"
  if [[ -t 0 ]]; then
    read -r -p "    Your name in meeting notes [Me]: " NAME
    read -r -p "    Language of the interface and notes, en or ru [en]: " REPLY
    LANGUAGE="${REPLY:-en}"
    read -r -p "    Obsidian vault [$VAULT]: " REPLY
    VAULT="${REPLY:-$VAULT}"
    VAULT="${VAULT/#\~/$HOME}"
  fi
  args=(init --language "$LANGUAGE" --vault "$VAULT" --uv "$(command -v uv)" --pipeline "$ROOT/pipeline")
  [[ -n "$NAME" ]] && args+=(--name "$NAME")
  "${MA[@]}" "${args[@]}"
fi

if command -v ollama >/dev/null && curl -fs --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null; then
  if ! ollama list | awk '{print $1}' | grep -qx "$MODEL"; then
    if ask "==> Download the minutes model $MODEL (about 6.6 GB)? [y/N]"; then
      ollama pull "$MODEL"
    fi
  fi
fi

echo
"${MA[@]}" doctor || true
cat <<EOF

Start the recorder:
  open "$APP"

Before recording anyone, make sure the people on the call know and agree.
EOF
