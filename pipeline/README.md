# Pipeline

The `ma` CLI, in Python 3.12 through uv. It takes a recording from `~/MeetingAssistant/recordings/<id>/` (`mic.caf`, `remote.caf`, `meta.json`) and produces a transcript, voices, minutes and Obsidian notes. Everything runs locally. Design details are in [docs/architecture.md](../docs/architecture.md#2-pipeline).

## Requirements

- macOS 15 or later on Apple Silicon. Tested on macOS 26; on macOS 15, only the build and unit tests have been run
- [uv](https://github.com/astral-sh/uv) and ffmpeg (`brew install uv ffmpeg`)
- `ma-diarize` for the voices step: `diarizer/scripts/build.sh`, see [diarizer/README.md](../diarizer/README.md)
- [Ollama](https://ollama.com) running, with `ollama pull qwen3.5:9b` (about 6.6 GB), for the minutes step
- An Obsidian vault folder for the notes step

[scripts/install.sh](../scripts/install.sh) checks the tools, builds the components and creates `config.toml`. Then check the setup:

```bash
uv run --project pipeline ma doctor
```

`ma init [--name NAME] [--language en|ru] [--vault PATH] [--uv PATH] [--pipeline PATH]` creates `config.toml` and never overwrites an existing one.

All commands below run from the repository root.

`MA_HOME=<folder>` replaces the `~/MeetingAssistant` data root. Use a fresh temporary folder for development:

```bash
export MA_HOME="$(mktemp -d)"
```

## Processing

```bash
uv run --project pipeline ma list
uv run --project pipeline ma process <id>
```

The steps and what they leave in the recording folder:

1. **prepare**: `mic.16k.wav`, `remote.16k.wav` for the models
2. **asr**: `asr.mic.json`, `asr.remote.json`. Whisper turbo on each track, large-v3 on weak spots, speech regions, and what the filter dropped and why
3. **voices**: `voices.json`. Voices of the "others" track and their numbers in the registry `~/MeetingAssistant/voices.sqlite`
4. **transcript**: `transcript.json`, `transcript.md` (Me, Voice N or a name, Room; low confidence is marked `(?)`)
5. **minutes**: `minutes.json`, from the local model through Ollama. Meetings under 2 minutes get none
6. **notes**: `Meetings/<date> — <title>.md` and `Transcripts/…` in the Obsidian vault, with links from decisions and action items to the transcript
7. **archive**: `audio.m4a` (mic left, remote right) for playback. CAF files are removed after a length check

Step status is in `meta.json` under `pipeline`. Finished steps are skipped.

- `--from STEP` reruns from a step, for example `--from transcript` after editing glossary replacements
- `--force` reruns everything
- `--priority background|low|normal` sets the priority. The default is macOS background mode (`[pipeline] priority`), which keeps Slack responsive. `low` is faster but more noticeable

The first run downloads the Whisper models (turbo about 1.6 GB, large-v3 about 3 GB) and the FluidAudio models. After that the pipeline works offline.

## Languages

Root `language` (`en` by default, or `ru`) sets the UI, the notes and the minutes prompt. `[asr] language` is the spoken language and defaults to `language`.

## Voices

```bash
uv run --project pipeline ma voices list
uv run --project pipeline ma voices name 3 Sam
```

- `list [--all]`: registry voices with number, name, meetings and speech time
- `show <id>`: a voice's meetings and clip times for playback in `audio.m4a` (right channel)
- `name <id> <name>`: set a name; an empty name clears it
- `merge <into> <other>`: same person, the second voice moves into the first
- `delete <id> [--yes]`: erase the voiceprint and name. The speech stays in transcripts as "Unknown"

After `name`, `merge` and `delete`, transcripts and notes of affected recordings are rebuilt. To do it by hand, without a model: `ma rerender <id|all>`.

Write your own text in a note outside the `<!-- ma:begin -->` … `<!-- ma:end -->` block: re-rendering changes only the block. The matching threshold and minimum speech are `[voices] match_threshold` (0.65) and `min_speech_seconds` (5).

## Deleting data

```bash
uv run --project pipeline ma delete <id>
uv run --project pipeline ma cleanup --dry-run
```

- `ma delete <id> [--keep-notes] [--yes]` permanently removes a recording, its voice data, voices known only from it, and its Obsidian notes. Text you added to a note stays. Without a terminal it requires `--yes`
- `ma cleanup [--dry-run] [--days N]` deletes audio (`*.16k.wav`, `audio.m4a`, CAF) of fully processed recordings older than `audio_retention_days` (7). Transcripts, minutes and notes stay. The recorder runs it once a day

Both delete commands also remove lines that mention the deleted name, recording or note from `~/MeetingAssistant/logs`.

## Web UI

```bash
uv run --project pipeline ma ui
```

In a terminal it prints an address on `http://127.0.0.1:8765` with a session token. Open that address. The recorder menu item "Open Recordings and Voices…" does this for you. The UI shows recordings, each meeting's voices with playable clips, names, "This is a different voice", merging, deletion, re-rendering and restarting processing. It listens only on 127.0.0.1.

## Glossary

`~/MeetingAssistant/glossary.txt` is created on first run from a starter for the spoken language ([English](src/ma/data/glossary.en.md), [Russian](src/ma/data/glossary.ru.md)):

- `! term`: goes into the Whisper prompt (order is priority, limit about 220 tokens; terms that do not fit are logged)
- `term`: a known term, passed to the minutes model
- `wrong => right`: a replacement after recognition, whole words, case-insensitive
- `# …`: a comment

Add your project names and colleagues' names here.

## Tests

```bash
uv run --directory pipeline pytest
uv run --directory pipeline pytest -m slow -s
```

All tests use a temporary `MA_HOME` automatically (`tests/conftest.py`).

Slow tests run real models on synthetic `say` speech and take a few minutes. They need a built `ma-diarize`. They check glossary terms, no invented text on silence and noise, one hour of audio in under 5 minutes, stable voice numbers across two meetings, under 15% wrong speaker labels, minutes from a real Ollama model (skipped if Ollama is not running) with notes in a temporary vault, and an English call through every step.
