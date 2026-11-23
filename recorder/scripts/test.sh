#!/bin/bash
# RecorderCore tests (Swift Testing). Without Xcode, SwiftPM can't find Swift Testing from the
# Command Line Tools, so the paths are passed to the whole build, including the generated test runner.
set -euo pipefail
cd "$(dirname "$0")/.."
CLT=/Library/Developer/CommandLineTools/Library/Developer
if [[ "$(xcode-select -p 2>/dev/null)" != /Library/Developer/CommandLineTools ]]; then
  exec swift test "$@"   # full Xcode finds Swift Testing itself
fi
exec swift test \
  -Xswiftc -F -Xswiftc "$CLT/Frameworks" \
  -Xlinker -F -Xlinker "$CLT/Frameworks" \
  -Xlinker -rpath -Xlinker "$CLT/Frameworks" \
  -Xlinker -rpath -Xlinker "$CLT/usr/lib" \
  "$@"
