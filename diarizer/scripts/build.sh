#!/bin/bash
# Builds ma-diarize into diarizer/build/ma-diarize. The first build takes ~5 min (all of FluidAudio).
set -euo pipefail
cd "$(dirname "$0")/.."
swift build -c release --product ma-diarize >&2
BIN="$(swift build -c release --show-bin-path)/ma-diarize"
mkdir -p build
cp "$BIN" build/ma-diarize.tmp
mv -f build/ma-diarize.tmp build/ma-diarize
echo "$PWD/build/ma-diarize"
