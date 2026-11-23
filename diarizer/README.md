# ma-diarize

Splits the "others" track into voices for `ma` (the `voices` step). It is a thin wrapper over [FluidAudio](https://github.com/FluidInference/FluidAudio) 0.18.3 (Apache-2.0): the pyannote community-1 pipeline (segmentation, WeSpeaker embeddings, VBx clustering) on CoreML. It runs on CPU and Neural Engine and leaves the GPU alone, so it does not slow Slack. The reasoning is in D8 of [docs/decisions.md](../docs/decisions.md).

## Build

From the repository root:

```bash
diarizer/scripts/build.sh
```

The result is `diarizer/build/ma-diarize`. Command Line Tools are enough; Xcode is not required. The first build takes 5–10 minutes. Rebuild after changing the FluidAudio version.

No signing is needed, because the program asks for no macOS permissions. The open models (about 23 MB) download on first run to `~/Library/Application Support/FluidAudio`. After that it needs no network.

## Commands

- `ma-diarize diarize <wav> <out.json> [--threshold x]`: voice segments (`start`, `end`, `speaker`, `quality`), the mean embedding of each voice (`speakers`), and `model`, the model snapshot
- `ma-diarize embed <wav> <spans.json> <out.json>`: an embedding (256 numbers) for each span in `[[start, end], …]`, in seconds

On error it exits with a nonzero code and writes the message to stderr. Only embeddings of the same `model` can be compared; the voice registry checks this itself.

## Tests

There are no unit tests for this component. The slow pipeline tests exercise it on synthetic speech:

```bash
uv run --directory pipeline pytest -m slow -s
```
