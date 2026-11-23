# Recorder

A Swift menu bar app, built with SwiftPM (Xcode is not required). It records two tracks of a call to `~/MeetingAssistant/recordings/<id>/`:

- `mic.caf`: the microphone ("Me")
- `remote.caf`: audio from Slack's processes only ("Others")
- `meta.json`: timing, segments, headphones or room mode

It needs macOS 15 or later on Apple Silicon and Swift 6.2 or later. It is tested on macOS 26. On macOS 15, only the build and unit tests have been run.

Recording starts from the menu. Automatic recording of Slack calls is **off by default**. To use it, turn on **Record Slack Calls Automatically** and accept the one-time consent notice. Then recording starts when Slack has held the mic for 3 s and stops 15 s after Slack releases it. After a recording, the recorder runs `ma process`, never during a call. Design details are in [docs/architecture.md](../docs/architecture.md#1-recorder).

Commands below run from `recorder/`. [scripts/install.sh](../scripts/install.sh) builds everything for a normal install.

## Signing certificate (once)

Without it the app is signed ad-hoc. macOS then ties the mic and audio permissions to the binary hash and silently stops granting them after each rebuild.

1. Open Keychain Access
2. Choose Keychain Access > Certificate Assistant > Create a Certificate...
3. Name: `Meeting Assistant Dev`, Identity Type: Self-Signed Root, Certificate Type: Code Signing, then Create
4. On the first build, macOS asks to let `codesign` use the key. Choose Always Allow

Check it with `security find-identity -p codesigning`, which should list `"Meeting Assistant Dev"`. The `CSSMERR_TP_NOT_TRUSTED` note is normal: signing does not need trust.

## Build and run

```bash
scripts/build-app.sh
```

This builds `build/MeetingRecorder.app` (bundle id `local.meetingassistant.recorder`) and signs it. At the end it prints the signing requirement, which should be `certificate leaf = H"…"` and not `cdhash`. A new build replaces the whole `.app`, so a running recorder does not crash. `SIGN_IDENTITY` overrides the certificate.

```bash
open build/MeetingRecorder.app
```

A waveform icon appears in the menu bar. On first launch macOS asks for microphone and audio capture access; allow both. If you refuse, the menu shows an item that opens System Settings.

Always launch the `.app` through `open`. A binary started from Terminal gets Terminal's permissions.

Menu:

- **Record Manually**: starts a recording (red icon with a timer)
- **Stop and Save**: saves to `~/MeetingAssistant/recordings/<id>/`
- **Stop Without Saving…**: deletes the recording after confirmation. No new auto-recording until the call ends
- **Record Slack Calls Automatically**: off by default, needs the consent notice
- **Launch at Login**: macOS may ask you to approve it in System Settings > General > Login Items & Extensions
- **Open Recordings and Voices…**: the `ma ui` web UI (the recorder starts it)
- **Open Recordings Folder**, **Show Last Recording**
- **Quit**: a running recording is saved

The menu also shows the processing state (running, or paused during a call).

Notifications: "Recording Slack call" with a **Don't Record** action that deletes the recording, then "Minutes ready", which opens the note in Obsidian. Allow them in System Settings > Notifications > Meeting Recorder.

Logs: `~/MeetingAssistant/logs/recorder.log`, and pipeline output in `process.log` in the same folder.

## Tests

```bash
scripts/test.sh
```

Runs the RecorderCore tests (Swift Testing): TOML parsing, consent, headphones or room, mic choice, watchdogs, CAF writing on synthetic audio, `meta.json`, translations. Plain `swift test` without Xcode finds no Swift Testing and silently runs nothing; the script passes the Command Line Tools paths.

## Manual checks

These tools use the real microphone. Only record people who agreed to it.

`record.sh` and `probe.sh` write logs under the real `~/MeetingAssistant`, even with `--root`. They do not pass `MA_HOME` to the app.

```bash
scripts/record.sh 60
```

The same recording as the menu, from the terminal, for 60 s or until Ctrl+C. It rebuilds the app first and prints track levels once a second. It does not show the consent notice. Options of `MeetingRecorder record`: `--root DIR` (where to write), `--bundle-prefix ID` (whose audio to record instead of Slack), `--mute` (audio is recorded but not heard).

```bash
uv run scripts/check_recording.py <recording-folder>
```

Checks a recording: both tracks exist with equal length, the mic offset against remote (if the mic heard Slack from the speakers), no mic voice or long music in remote, no digital silence during a call.

### Testing without Slack

Play a sound from another process with the recorder's bundle id. Run the second command in another terminal while the first one records:

```bash
scripts/record.sh 30 --root "$(mktemp -d)" --bundle-prefix local.meetingassistant.recorder
open -n build/MeetingRecorder.app --args probe play <file.wav>
```

To test auto-recording, run a second instance with its own data folder. Turn on auto-recording in its menu, then start the "call":

```bash
export MA_HOME="$(mktemp -d)"
printf '[recorder]\nbundle_id_prefixes = ["local.meetingassistant.recorder"]\n' > "$MA_HOME/config.toml"
open -n --env MA_HOME="$MA_HOME" build/MeetingRecorder.app
open -n --env MA_HOME="$MA_HOME" build/MeetingRecorder.app --args probe tap --seconds 20 --out "$MA_HOME/probe"
```

The `probe tap` process holds the mic and acts as the call.

- Stop a test instance **only by its pid** (`kill <pid>`). `pkill` by name also closes any recorder you use for real
- An instance started with `MA_HOME` keeps its auto-record switch and accepted notice in the separate defaults domain `local.meetingassistant.recorder.test`, so it does not change the real app
- Restart a real recorder only when it is not recording (see `recorder.log`)

## Diagnostics

```bash
scripts/probe.sh watch
```

Shows which Slack processes hold the mic and play audio, on which devices, and the default devices. It needs no permissions. Ctrl+C prints a summary. Log: `~/MeetingAssistant/probe/watch-*.log`.

```bash
scripts/probe.sh tap 900
```

Records for 900 s (or until Ctrl+C) to `~/MeetingAssistant/probe/tap-*/`: two tap tracks, by bundle id (`remote-bundle.caf`) and by process list (`remote-pid.caf`), plus the mic (`mic.caf`). `tap.log` has levels once a second. `--bundle-prefix ID` targets another app. It requires the certificate, or `ALLOW_ADHOC=1`.

`MeetingRecorder probe writer-test --seconds N --out DIR` writes a synthetic CAF, for checking the file after `kill -9`.
