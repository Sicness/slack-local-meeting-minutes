#!/bin/bash
# Records from the terminal with the same code as "Record Manually" in the menu. For testing.
#   record.sh [seconds] [more MeetingRecorder record args: --root DIR, --bundle-prefix ID, --mute]
# Without seconds, records until Ctrl+C. Output: ~/MeetingAssistant/recordings/<id>/ (or --root).
set -euo pipefail
cd "$(dirname "$0")/.."

args=(record)
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then args+=(--seconds "$1"); shift; fi
args+=("$@")

app="$(scripts/build-app.sh)"
log="$HOME/MeetingAssistant/logs/record-cli.log"
mkdir -p "$(dirname "$log")"
: > "$log"
pidfile="$(mktemp -t meeting-recorder-pid)"
rm -f "$pidfile"

# Via open, so Meeting Recorder gets the permissions rather than Terminal.
open -W -n --stdout "$log" --stderr "$log" "$app" --args "${args[@]}" --pid-file "$pidfile" &
open_pid=$!
tail -n +1 -f "$log" &
tail_pid=$!

stop_app() {
  local pid
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 100); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
  fi
}
trap 'stop_app; sleep 0.3; kill "$tail_pid" 2>/dev/null || true; exit 0' INT TERM

wait "$open_pid" || true
sleep 0.3
kill "$tail_pid" 2>/dev/null || true
