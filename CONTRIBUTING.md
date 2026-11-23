# Contributing

Bug reports and pull requests are welcome.

## Never share meeting data

Do not attach recordings, transcripts, minutes, voiceprints, `voices.sqlite`, `meta.json` or logs from real meetings to issues, pull requests or commits. They contain other people's voices and words. Reproduce problems with synthetic speech instead (`say` on macOS works well).

## Development setup

You need a Mac with Apple Silicon, macOS 15 or later, Swift 6.2+, [uv](https://github.com/astral-sh/uv) and ffmpeg.

```bash
scripts/install.sh --allow-adhoc
```

Use a separate data folder, so that development never touches your own recordings:

```bash
export MA_HOME="$(mktemp -d)"
```

## Tests

```bash
uv run --directory pipeline pytest
```

```bash
recorder/scripts/test.sh
```

Slow tests run the real models on synthetic speech. They need `diarizer/scripts/build.sh`, and take a few minutes:

```bash
uv run --directory pipeline pytest -m slow
```

## Guidelines

- Keep everything local: no telemetry, and no network calls with audio, text or voice data
- The web UI listens only on `127.0.0.1`
- User-facing text goes through the translation catalogs (`pipeline/src/ma/i18n.py`, `recorder/Sources/RecorderCore/L10n.swift`) in both English and Russian
- Comments, logs and CLI output are in English
- For a change to a macOS audio API, test on a real Slack huddle with people who agreed to be recorded
