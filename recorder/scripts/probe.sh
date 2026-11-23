#!/bin/bash
# Recorder prototype.
#   probe.sh watch          - which Slack processes hold the mic and play audio (no permissions needed)
#   probe.sh tap [seconds] [--bundle-prefix ID]  - records Slack audio with two tap methods plus the mic (needs permissions)
# Everything is written to ~/MeetingAssistant/probe/.
set -euo pipefail
cd "$(dirname "$0")/.."

PROBE_DIR="$HOME/MeetingAssistant/probe"
mkdir -p "$PROBE_DIR"
cmd="${1:-}"; shift || true

case "$cmd" in
  watch)
    swift build -c release --product MeetingRecorder >&2
    exec "$(swift build -c release --show-bin-path)/MeetingRecorder" probe watch "$@"
    ;;

  tap)
    seconds="${1:-900}"; shift || true
    if [[ -z "${ALLOW_ADHOC:-}" ]] && ! security find-identity -p codesigning 2>/dev/null | grep -q '"Meeting Assistant Dev"'; then
      echo "No \"Meeting Assistant Dev\" certificate in the keychain." >&2
      echo "Without it, microphone and audio permissions are lost on the next rebuild. Create it (see recorder/README.md)" >&2
      echo "or run with ALLOW_ADHOC=1 if this is intentional." >&2
      exit 1
    fi
    app="$(scripts/build-app.sh)"
    out="$PROBE_DIR/tap-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$out"
    log="$out/tap.log"
    : > "$log"

    # Via open, so macOS asks for permissions for Meeting Recorder rather than Terminal.
    open -W -n --stdout "$log" --stderr "$log" "$app" --args probe tap --seconds "$seconds" --out "$out" "$@" &
    open_pid=$!
    tail -n +1 -f "$log" &
    tail_pid=$!

    stop_app() {
      local app_pid
      app_pid="$(cat "$out/pid" 2>/dev/null || true)"
      if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null; then
        echo; echo "Stopping recording..."
        kill -TERM "$app_pid" 2>/dev/null || true
        for _ in $(seq 1 100); do kill -0 "$app_pid" 2>/dev/null || break; sleep 0.1; done
      fi
    }
    trap 'stop_app; kill "$tail_pid" 2>/dev/null || true; echo; tail -n 25 "$log"; echo; echo "Folder: $out"; exit 0' INT TERM

    wait "$open_pid" || true
    sleep 0.3
    kill "$tail_pid" 2>/dev/null || true
    echo; echo "Folder: $out"
    ;;

  *)
    sed -n '2,5p' "$0"
    exit 2
    ;;
esac
