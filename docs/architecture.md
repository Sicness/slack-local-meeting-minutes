# Architecture

slack-local-meeting-minutes records Slack calls on a Mac, transcribes them, tells voices apart, and writes meeting minutes to Obsidian. All processing runs on the Mac.

**Platform.** macOS 15 or later on Apple Silicon. The app is tested on macOS 26. On macOS 15, only the build and unit tests have been run.

## Overview

```
Slack huddle
   |
   |-- microphone ---------.
   `-- Slack process audio -+  Recorder (Swift, menu bar app)
                            |   - sees that Slack holds the mic: start and stop
                            |   - writes mic.caf, remote.caf, meta.json
                            v
              ~/MeetingAssistant/recordings/<id>/
                            |   after a recording, the recorder runs `ma process <id>`
                            v
                   Pipeline (Python, uv)
   1. prepare     ffmpeg: 16 kHz mono wav for the models
   2. asr         mlx-whisper, each track separately, with word timestamps
   3. voices      FluidAudio on the "others" track, then the voice registry
   4. transcript  one timeline of utterances: [time] @voice: text
   5. minutes     local model through Ollama, schema-bound JSON
   6. notes       Obsidian: a minutes note and a transcript note
   7. archive     audio.m4a for playback, CAF files removed
                            |
                            v
              Web UI (FastAPI, 127.0.0.1:8765)
   - recordings and processing status
   - play voice clips, assign names, merge, correct and delete voices
   - names update the transcripts and notes
```

## 1. Recorder

A menu bar app (`LSUIElement`, no Dock icon) in `recorder/`. It builds with SwiftPM and does not need Xcode. `scripts/build-app.sh` packages it as `build/MeetingRecorder.app` (bundle id `local.meetingassistant.recorder`) and signs it with the self-signed "Meeting Assistant Dev" certificate (D18). The `.app` bundle is required for notifications and stable permissions.

```
recorder/
  Package.swift               swift-tools 6.2, macOS 15
  Resources/Info.plist        LSUIElement, permission prompts
  Sources/RecorderCore/       no AppKit: Core Audio, recording, policies (unit tested)
    CoreAudioExt, Devices, SlackProcesses   Core Audio properties, devices, processes
    Capture                   TapCapture (tap, aggregate, IOProc), InputCapture (IOProc on the mic)
    SampleRing, TrackWriter   ring buffer from the audio thread, CAF writing, segments, anchors
    Recording                 a recording session: folder, t0, watchdogs, meta.json
    AutoRecord                when to record, processing queue policy, cleanup schedule
    Policies, OutputRoute     consent, mic choice, silence watch, headphones or room
    Processing                `ma process` and `ma ui` runners, locating uv and pipeline/
    L10n                      user-facing strings in English and Russian
    Meta, Config, Log, Permissions
  Sources/MeetingRecorder/    MenuApp, HeadlessRecord (`record`), Probe (diagnostics)
  Tests/RecorderCoreTests/    Swift Testing
  scripts/                    build-app.sh, test.sh, record.sh, probe.sh, check_recording.py
```

### Slack processes

Slack appears in Core Audio as several processes: the main app (`com.tinyspeck.slackmacgap`) and Chromium helpers (`com.tinyspeck.slackmacgap.helper`). During a huddle the Chromium audio service holds the mic and plays the sound. Three behaviors shape the design:

- Slack takes and releases the mic twice in the first 3 seconds of a huddle
- Muting in Slack does not release the mic
- Slack keeps its audio output open for a while after the call

### Call detection and auto-recording

A call is active when a Core Audio process whose bundle id starts with a `bundle_id_prefixes` entry has `kAudioProcessPropertyIsRunningInput` set. The recorder excludes itself. The menu app polls every 0.5 s. A running session polls every 0.25 s and logs the periods in `call_periods`.

`AutoRecordPolicy`:

- Slack holds the mic for `start_after_seconds` (3 s): recording starts with `trigger: "auto"`
- Slack has not held the mic for `stop_after_seconds` (15 s): recording stops
- Auto-recording never stops a manual recording
- "Don't Record", "Stop Without Saving…" and a manual stop during a call block auto-start until that call ends

### Recording consent

Automatic recording is **off by default** (`RecordingConsent` in `Policies.swift`, `MenuApp.swift`). It runs only when both are true:

- the user turned on "Record Slack Calls Automatically"
- the user accepted a one-time notice about consent and voiceprints

The same notice appears before the first manual recording. Both flags live in the app's user defaults. An instance started with `MA_HOME` uses the separate domain `local.meetingassistant.recorder.test`. If an older version left auto-recording on without an accepted notice, the app asks at launch. The terminal `record` command is a developer tool and does not show the notice.

### "Others" track (`remote.caf`)

The chain is `CATapDescription(monoMixdownOfProcesses:)` over the **list of Slack processes** (private, `muteBehavior = unmuted`), then a private aggregate device holding only this tap (`private: 1`, `stacked: 0`, no real devices, no drift compensation), then an IOProc.

- The aggregate has no real devices, so switching output between headphones and speakers does not affect it
- A new Slack process joins the live tap through `setDescription`, without a new segment
- A tap by bundle id (`bundleIDs` with `processRestoreEnabled`, macOS 26) does **not** pick up processes launched after it was created, so it is not used (D20)
- The tap and aggregate are rebuilt, with a new segment, if the format changes or `setDescription` fails. Teardown order is strict: IOProc, aggregate, tap

Watchdog: if Slack is in a call and `remote` delivers pure zeros for 10 s, the tap is rebuilt once and the menu shows a warning. During a call Slack emits low-level noise rather than pure zeros. Without the audio capture permission, a tap returns zeros and no error.

### "Me" track (`mic.caf`)

An IOProc runs directly on the input device instead of `AVAudioEngine` (D19). Slack may use a mic other than the system default, each buffer has an exact host time, and the code is shared with the tap. The recorder does not change the sample rate, buffer size or hog mode, so Slack is not affected. On a multichannel mic it records channel 0.

Device choice (`MicPolicy`):

- Slack holds the mic: use Slack's device (`kAudioProcessPropertyDevices`, input scope)
- Otherwise use the default input, but **never open a Bluetooth mic first**: that would switch headphones to 16 kHz headset mode before Slack does
- Release a Bluetooth mic 3 s after Slack releases it, or the headset stays in call mode. The same delay rides out the mic bounce when joining a huddle
- A device that disappears or changes rate, or Slack switching mics, starts a new segment

### Files and synchronization

Tracks are CAF, Linear PCM Float32, mono. The file rate is the rate of the first segment. Later segments at another rate go through their own `AVAudioConverter`, because ExtAudioFile cannot change rate mid-file. The file is therefore written through AudioFile, with one write call every 50 ms. Header sizes update on every write, so a CAF stays readable after `kill -9`. The IOProc pushes audio into a lock-free ring without allocating; a separate queue writes it to disk.

- One t0 (host time) per recording: frame 0 of both tracks is t0
- A segment is one continuous run from one device start. Silence fills the gap before it, by host time. Audio before t0 or before the end of the previous segment is dropped
- Inside a segment, silence is inserted only when `mSampleTime` jumps
- Every 10 s an anchor records the file frame, `mSampleTime`, `mHostTime` and `mRateScalar`. The pipeline uses anchors to remove clock drift
- On stop, both tracks are padded with silence to a common end. A track with no audio becomes a 16 kHz silent file
- Device latency and safety offset are stored in the segment `info` but not applied to the audio. `check_recording.py` allows a mic-to-remote offset of up to ±100 ms

### Headphones or room

The mode depends on the device Slack plays to, or on the default output if Slack is not playing (`OutputRoute`, D5):

| Output | Mode |
|---|---|
| Bluetooth, USB | `headphones` |
| DisplayPort, HDMI, AirPlay | `room` |
| Built-in, headphone terminal (`hdph`, `0x0302`, headset codes) or `hdpn` source | `headphones` |
| Other built-in output, such as speakers | `room` |
| Aggregate | the mode of its first active output subdevice |
| Unknown | `headphones` |

Device names are not compared because they are localized. A change counts once it has lasted 1 s. The initial mode goes to `mode`, later changes to `route_changes`.

### Permissions

- `NSMicrophoneUsageDescription`: microphone, requested with `AVCaptureDevice.requestAccess`
- `NSAudioCaptureUsageDescription`: audio from other apps (System Settings > Privacy & Security > Screen & System Audio Recording, under System Audio Recording Only). There is no public API to check it, so the app calls `TCCAccessPreflight` and `TCCAccessRequest` for `kTCCServiceAudioCapture` from the private TCC.framework through `dlopen`
- Without permission macOS returns zeros and no error, so a denied permission appears in the menu and in `warnings`
- Permissions are tied to the code signing requirement. With an ad-hoc signature that is the binary hash, so each rebuild silently loses them. With a certificate it is `certificate leaf`, which survives rebuilds
- A binary started from Terminal gets Terminal's permissions, so `record.sh` and `probe.sh tap` launch the `.app` through `open`

### Menu

- Status line, and during a recording the mic, the Slack state and warnings
- **Record Manually** / **Stop and Save**
- **Stop Without Saving…**: asks for confirmation and deletes the folder
- **Record Slack Calls Automatically** (off by default)
- **Launch at Login** (`SMAppService.mainApp`; macOS may ask for approval in System Settings)
- A processing line: current job, paused state, queue length
- **Open Recordings and Voices…**, **Open Recordings Folder**, **Show Last Recording**
- **Quit**: a running recording is saved. The same happens on logout and SIGTERM

During a recording, `beginActivity(.userInitiated, .idleSystemSleepDisabled)` keeps the Mac awake and App Nap away. `recorder.lock` prevents a second menu bar instance. The log is `logs/recorder.log` (rotated at 5 MB), also available with `log stream --predicate 'subsystem == "local.meetingassistant.recorder"'`.

### Notifications

- On auto-start: "Recording Slack call", with a **Don't Record** action that deletes the recording and suppresses auto-start until the call ends
- After processing: "Minutes ready" or "Transcript ready". A click opens the note through `obsidian://open?path=…`
- On failure: "Processing failed" with the failed step
- Ollama not running: one notification, and retries every 15 minutes

### After a recording

`ProcessingRunner` writes `meta.json` with status `recorded`, then runs `<uv> run --project <repo>/pipeline ma process <id>` in its own process group (`posix_spawn` with `SETPGROUP`). Output goes to `logs/process.log`.

- Recordings are processed one at a time, in queue order
- Processing loads the GPU, which slows Slack (D23). During a call or recording no job starts, and a running job is paused with `SIGSTOP` to its process group (uv, python, ma-diarize). `SIGCONT` resumes it afterwards (D25). An Ollama request already sent still finishes on the server
- At launch the recorder queues recordings with status `recorded` and no `pipeline` entry, for example when the Mac shut down right after a call. It does not queue crashed recordings (status `recording`) or partly processed ones: run `ma process <id>` for those
- Quitting the recorder does not kill processing
- If the minutes step fails because Ollama is not running, the recorder retries every 15 minutes while it stays running. `ma process` resumes from the minutes step
- uv is looked up in Homebrew, `~/.local/bin` and `~/.cargo/bin`, and `pipeline/` next to the app (`recorder/build/MeetingRecorder.app`). `[recorder] uv` and `pipeline` override both, as `scripts/install.sh` does for you

## 2. Pipeline

The `ma` CLI (uv, Python 3.12) lives in `pipeline/`. Run it from the repository with `uv run --project pipeline ma …`.

| Command | What it does |
|---|---|
| `ma process <id\|folder> [--force] [--from STEP] [--priority MODE]` | run the pipeline steps |
| `ma list` | recordings and step status |
| `ma voices list\|show\|name\|merge\|delete` | voice registry |
| `ma rerender <id\|all>` | rebuild transcripts and notes without models |
| `ma cleanup [--dry-run] [--days N]` | delete old audio |
| `ma delete <id> [--keep-notes] [--yes]` | delete a recording and its data |
| `ma ui` | web UI |
| `ma doctor` | check the setup |
| `ma init` | create `config.toml` |

Steps run in order: `prepare`, `asr`, `voices`, `transcript`, `minutes`, `notes`, `archive`. Each writes its result to the recording folder and its state to `meta.json` under `pipeline.<step>`: `status` (`running`, `done`, `failed`), `updated_at`, `seconds`, and `result` or `error`.

- Finished steps are skipped. A failed or interrupted step runs again on the next run
- `--from STEP` reruns from that step. `--force` reruns everything
- `.ma.lock` keeps two `ma` processes off one recording
- A recording with status `recording` is refused if its recorder `pid` is alive. Otherwise it is marked `interrupted: true` and processed as is
- The log is `logs/pipeline.log`

```
pipeline/src/ma/
  cli.py, process.py      commands and steps
  prepare.py              ffmpeg: wav and the m4a archive
  priority.py             macOS background mode
  vad.py, asr.py          speech regions, mlx-whisper and its filter
  glossary.py             glossary.txt: Whisper prompt and replacements
  timeline.py             headphones/room over time, time mapping through anchors
  diarize.py              voices step: ma-diarize, chunks, relabeling by embedding
  registry.py             voice registry, voices.sqlite
  voices_cli.py           ma voices …
  transcript.py           transcript.json and transcript.md
  minutes.py              minutes through Ollama, minutes.json
  notes.py                Obsidian notes
  forget.py               deleting voices and recordings
  doctor.py               ma doctor and ma init
  i18n.py                 user-facing strings in English and Russian
  web.py, templates/, static/   web UI
  data/                   starter glossaries (glossary.en.md, glossary.ru.md)
  meta.py, config.py, paths.py, log.py
pipeline/tests/           pytest; -m slow runs real models on synthetic `say` speech
diarizer/                 SwiftPM: ma-diarize (FluidAudio 0.18.3)
```

### prepare

ffmpeg converts each track to `mic.16k.wav` and `remote.16k.wav` (16 kHz, mono, s16). The length must match the CAF within ±20 ms. The CAF files stay until the archive step.

### asr

mlx-whisper with `large-v3-turbo`, `[asr] language`, word timestamps, on each **whole** track (D7).

- **Glossary prompt in every window.** With `condition_on_previous_text=False`, mlx-whisper passes `initial_prompt` to the first 30-second window only. `asr.py` injects it into all windows by patching `DecodingOptions`. The decoder limit is 223 tokens; the prompt uses at most 220
- **VAD (silero) is a filter after Whisper, not a cutter.** Whisper pads cut pieces with zeros and invents more text on those tails than on a whole track. Thresholds are soft (0.35, speech from 150 ms, 300 ms pad) so that short replies stay speech. A track with no speech skips Whisper
- **Segment filter.** Removes known hallucination phrases, segments under 30% speech by VAD, `avg_logprob < -1.5` (or `< -1` with under 60% speech), prompt echo, and words outside speech regions
- **One temperature (0)** in the main pass. The default six-temperature fallback adds no words but is much slower on noisy tracks
- **Rescue.** If Whisper got stuck in a loop or produced garbage over at least 3 s of VAD speech, the span is retranscribed without a prompt, at temperatures 0 and 0.4, using the second model when one is set
- **Second opinion** (`second_model`, default `whisper-large-v3`). Segments with `avg_logprob < -0.45` or mean word probability `< 0.65`, grouped when adjacent, are retranscribed by full large-v3. Its variant wins if its `avg_logprob` is higher by more than 0.05
- Output: `asr.<track>.json` with the prompt and terms that did not fit, speech regions, segments with words (`w`, `start`, `end`, `p`), dropped segments with reasons, and what the second model replaced

Results vary slightly between runs. Default model revisions are pinned (`asr.REVISIONS`); a different model set in `config.toml` uses its latest revision.

### voices

Runs on the "others" track only (`diarize.py`, D8). In room mode the mic track is not diarized.

- `ma-diarize diarize` (FluidAudio offline: pyannote segmentation, WeSpeaker embeddings, VBx clustering; CoreML on CPU and Neural Engine, leaving the GPU to Slack) produces meeting voice segments S1, S2, and so on
- **Chunks.** Whisper words of one diarizer segment with pauses up to 1.5 s. The diarizer often finds a short reply as its own segment but gives it a neighbor's label, so chunks always break at segment boundaries
- `ma-diarize embed` computes an embedding per chunk. Chunks under 0.6 s are extended around their middle
- **Relabeling.** A meeting voice centroid is the length-weighted mean of its chunks of at least 1.5 s. Each chunk moves to the nearest centroid, in two passes. If the top two candidates differ by less than 0.05, the diarizer's label stays
- Meeting voices then go to the registry (section 3). `voices.json` stores voices, chunks, raw segments and a label per remote word

Limit: the diarizer can merge very similar voices. Fix this with "This is a different voice" in the web UI.

### transcript

Word times are mapped from file time to seconds from t0 through anchors (`timeline.TimeMap`). An utterance is words of one track and speaker with pauses up to 1.5 s.

| Label | Meaning |
|---|---|
| `@me` | mic track in headphones mode |
| `@room` | mic track in room mode |
| `@v7` | registry voice 7 on the remote track (merges resolved) |
| `@unknown` | a remote voice that could not be identified |
| `@others` | remote track when no `voices.json` exists |

- Glossary replacements apply here, so after editing them `--from transcript` is enough
- In room mode the mic hears Slack from the speakers. An `@room` word that matches a remote word within 0.8 s (`SequenceMatcher` at least 0.75) is removed as echo and counted in `room_echo_dropped`
- `transcript.json` has `speakers` (label to name or "Voice N") and utterances with `confidence` (mean word probability)
- `transcript.md` is a plain working copy. Consecutive utterances of one speaker are joined, and low-confidence ones are marked `(?)`

### minutes

Ollama at `127.0.0.1:11434`, model `qwen3.5:9b` with reasoning (D10). See [minutes-format.md](minutes-format.md) for the schema and prompt rules.

- The model receives the labeled timeline, the participants with their names, the glossary and the instructions. It replies with schema-bound JSON
- People appear only as labels. Labels that are not in the meeting are removed from `owner` and `who`
- `num_ctx` is sized from the input, starting at 16K and doubling. `keep_alive: 0` unloads the model right after the request
- If reasoning hits `max_tokens` (16K) or the reply is not JSON, the request is retried without reasoning
- Meetings under `min_meeting_seconds` (2 min) and empty transcripts get no minutes
- If Ollama is not running the step fails with a hint. The next `ma process` resumes from it
- Ollama is a separate server, so the background priority of `ma` does not apply to it

### notes

Renders `minutes.json` and `transcript.json` to Markdown and replaces labels with names (section 4).

### archive

Builds `audio.m4a` (mic left, remote right, AAC 96 kbit/s) for playback. The CAF files are deleted only after the m4a length (±100 ms) and the wav files are checked. It is the last step, so a failure in `minutes` or `notes` leaves the CAF files in place.

### Priority

`ma` runs in macOS background mode (`PRIO_DARWIN_BG`, `[pipeline] priority = "background"`). This lowers CPU and I/O priority, and in practice GPU priority, so Slack and the UI stay responsive. The cost is longer processing. `low` is nice 10, and `normal` changes nothing.

## 3. Voice registry

SQLite at `~/MeetingAssistant/voices.sqlite` (`registry.py`), with `secure_delete` on.

- `meta`: schema version and embedding model (`fluidaudio-offline@<snapshot>`). Embeddings of different models are not comparable, so `ma` refuses to mix them
- `voices`: `id`, `label`, `name` (empty until assigned), `centroid`, `created_at`, `merged_into`. The displayed label is "Voice N", where N is the id, in the UI language
- `voice_samples`: the embedding of each appearance (`voice_id`, `recording_id`, `cluster`, `embedding`, `speech_seconds`, `clips` with the longest chunks for playback)
- `recording_voices`: which meeting cluster became which voice, with similarity and decision (`matched`, `new`, `split`, `short`, `short-near`, `unknown`, `manual`)

Matching (D24):

- Cosine similarity between meeting centroids and registry centroids, **one to one**, greedy by descending similarity, with `match_threshold` 0.65
- No match: a new "Voice N"
- Two meeting voices near the same registry voice and at least 0.75 similar to each other: the diarizer split one person, so both get that voice (`split`)
- A meeting voice with under `min_speech_seconds` (5 s) of speech is not stored. It takes a known voice at or above the threshold, otherwise the nearest meeting voice at 0.5 or more, otherwise `@unknown`
- A voice centroid is the mean of its samples, weighted by speech, capped at 120 s per meeting

Reprocessing (`--from voices`) replaces the recording's samples and matches again. A voice with no samples keeps its last centroid, so it is found again with the same number. `ma voices list` hides voices with no meetings and no name; `--all` shows them.

Operations: `name`, `merge` (samples move, `merged_into` is set, the centroid is recomputed), `show`, `delete`. The web UI adds "This is a different voice" for one meeting. Afterwards, the transcripts and notes of affected recordings are rebuilt.

## 4. Obsidian notes

The vault is `obsidian_vault`. The format is in [minutes-format.md](minutes-format.md).

- `<vault>/Meetings/2026-01-15 1530 — <title>.md`: minutes (`meetings_folder`)
- `<vault>/Transcripts/2026-01-15 1530 — <title>.md`: transcript (`transcripts_folder`). Each paragraph has a block id such as `^t001234`, its start time
- The file name is chosen once and stored in `meta.json` under `notes`, so links survive a new title
- Named voices become links such as `[[Sam]]`, the owner uses `me_name`, and an unnamed voice stays plain text. The app does not create notes for people: Obsidian shows them as unresolved links, and backlinks give a person's meeting history
- Decisions and action items link to the transcript paragraph of their source utterance
- Generated text sits between `<!-- ma:begin -->` and `<!-- ma:end -->`. Re-rendering changes only this block, so text you add outside it stays. If the block is removed, the note is left alone
- Frontmatter: `date`, `duration`, `participants`, `recording_id`, `source`, `tags`, and `transcript` or `meeting` linking the paired note. Only these keys are updated; your own keys stay
- `ma rerender <id|all>` and `ma voices name|merge` rebuild transcripts and notes without a model

## 5. Data on disk

```
~/MeetingAssistant/            (MA_HOME overrides this path)
  config.toml
  glossary.txt                 working glossary, created from pipeline/src/ma/data/
  voices.sqlite
  ui.token                     web UI session secret
  recorder.lock                single menu bar instance
  cleanup.last                 last successful daily cleanup
  logs/                        recorder.log, pipeline.log, process.log, ui.log, record-cli.log
  probe/                       output of scripts/probe.sh
  recordings/2026-01-15_1530_ab12/
    meta.json                  step status, track timing, mode, duration
    mic.caf, remote.caf        until the archive step
    mic.16k.wav, remote.16k.wav, audio.m4a    removed by `ma cleanup`
    asr.mic.json, asr.remote.json
    voices.json
    transcript.json, transcript.md
    minutes.json
```

### Audio retention

`ma cleanup` deletes `*.16k.wav`, `audio.m4a` and any CAF files of recordings older than `audio_retention_days` (7) and sets `audio_deleted_at`. JSON files stay, so notes can always be rebuilt.

Only recordings whose `archive` step is done are touched. Audio of a recording that stopped at an earlier step, for example because Ollama or the vault is missing, is kept until processing finishes or you run `ma delete`.

The recorder runs cleanup once a day, after queued recordings and never during a call. A failure retries after an hour. Output goes to `logs/process.log`. `[recorder] cleanup_daily = false` turns it off. It also runs with `process_after_stop = false`. `ma doctor` lists recordings that keep audio past the retention period because processing did not finish.

### `meta.json`

The recorder writes it atomically at start, every 30 s and at stop. Pipeline steps add their fields.

```json
{
  "version": 1,
  "id": "2026-01-15_1015_ab12",
  "status": "recorded",
  "source": "slack",
  "trigger": "manual",
  "started_at": "2026-01-15T10:15:02.120+00:00",
  "ended_at": "2026-01-15T10:20:14.530+00:00",
  "duration_seconds": 312.4,
  "t0_host_time": 37034337731629,
  "host_ticks_per_second": 24000000,
  "mode": "headphones",
  "mode_reason": "Bluetooth: Headset",
  "route_changes": [{"at_seconds": 120.5, "mode": "room", "reason": "built-in speakers: Speakers", "device_uid": "BuiltInSpeakerDevice"}],
  "call_periods": [{"start_seconds": 3.4, "end_seconds": 288.0}],
  "tracks": {
    "mic": {
      "file": "mic.caf", "sample_rate": 16000, "frames": 4998400, "seconds": 312.4,
      "segments": [{
        "index": 0, "start_frame": 55111, "host_time": 37034418919371, "seconds_from_t0": 3.44,
        "sample_rate": 16000, "padded_frames": 55111, "dropped_leading_frames": 0,
        "info": {"kind": "input", "device": "Headset", "uid": "00-00-00:input", "transport": "blue",
                 "reason": "Slack device", "format": "16000 Hz 1 ch f32",
                 "latency_frames": "160", "safety_offset_frames": "0"},
        "anchors": [{"frame": 55111, "sample_time": 3064, "host_time": 37034418919371, "rate_scalar": 1}]
      }],
      "overflow_frames": 0, "discontinuities": 0, "overlaps": 0, "errors": []
    },
    "remote": {"file": "remote.caf", "sample_rate": 48000, "…": "same fields, info.kind = tap"}
  },
  "warnings": [{"at_seconds": 12.3, "text": "…"}],
  "recorder": {"version": "0.1", "pid": 12345, "bundle_id_prefixes": ["com.tinyspeck.slackmacgap"]}
}
```

- `status`: `recording` means in progress or the recorder crashed. If no recorder with this `pid` exists, the recording was interrupted; its files stay readable
- `t0_host_time` is frame 0 of both tracks. Segment and anchor times are host time; convert with `host_ticks_per_second`
- `segments[].info` holds strings: device, reason, format, device latency (not applied to the audio)
- `call_periods`: when Slack held the mic, in seconds from t0

## 6. Configuration

`~/MeetingAssistant/config.toml`. `ma init` or `scripts/install.sh` creates it. A missing key uses its default.

```toml
language = "en"                  # UI, notes and minutes prompt: en or ru
me_name = "Alex"                 # default: Me (en), the Russian equivalent (ru)
obsidian_vault = "~/Documents/Obsidian"
meetings_folder = "Meetings"
transcripts_folder = "Transcripts"
audio_retention_days = 7
min_meeting_seconds = 120
# ffmpeg = "/opt/homebrew/bin/ffmpeg"   # default: from PATH or Homebrew

[recorder]
bundle_id_prefixes = ["com.tinyspeck.slackmacgap"]
start_after_seconds = 3      # Slack holds the mic this long: recording starts
stop_after_seconds = 15      # Slack has not held it this long: recording stops
process_after_stop = true    # run ma process after a recording, never during a call
cleanup_daily = true         # run ma cleanup once a day
# uv = "/opt/homebrew/bin/uv"
# pipeline = "/path/to/repo/pipeline"

[pipeline]
priority = "background"      # background | low | normal

[asr]
model = "mlx-community/whisper-large-v3-turbo"
second_model = "mlx-community/whisper-large-v3-mlx"   # "" turns it off
# language = "ru"            # spoken language; defaults to the root language

[voices]
# diarizer = "/path/to/ma-diarize"   # default: diarizer/build/ma-diarize in the repository
match_threshold = 0.65
min_speech_seconds = 5

[minutes]
model = "qwen3.5:9b"
ollama_url = "http://127.0.0.1:11434"
think = true                 # reasoning: more accurate, slower
max_tokens = 16384           # reply cap with reasoning; beyond it, retry without

[ui]
port = 8765
```

**Languages.** Root `language` controls the web UI, the menu, the notes and the minutes prompt. `[asr] language` is the spoken language for Whisper and defaults to `language`, so you can, for example, read English notes for Russian speech. It also selects the starter glossary on first run. Translations live in `pipeline/src/ma/i18n.py` and `recorder/Sources/RecorderCore/L10n.swift`.

**Recorder config.** The recorder reads `[recorder]`, the root `language` and `[ui] port` with a small TOML-subset parser (`Config.swift`): sections, strings, numbers, booleans, arrays including multiline, and comments. Other sections are skipped even if the parser could not read them. A missing file means defaults. An error in `[recorder]` means defaults and a menu warning. The config is read at launch and before each recording; the processing runner (`process_after_stop`, `uv`, `pipeline`) is set up at launch only. The Python pipeline owns the file; the recorder never writes it.

The app needs no secrets. Minutes come from a local model (D10) and the voice models are open (D8).

## 7. Web UI (`ma ui`)

FastAPI and Jinja2, with no external scripts or CDNs. It listens **only on `127.0.0.1`**, on `[ui] port` (8765). The menu item "Open Recordings and Voices…" starts it if nothing listens on the port (`uv run --project <pipeline> ma ui`, output in `logs/ui.log`). It stops when the recorder quits.

- `/`: recordings with date, title, length, participants, step status and links to the notes
- `/r/<id>`: a meeting's voices, up to three playable clips each (`/r/<id>/clip?start&end`, WAV from `remote.16k.wav` or the right channel of `audio.m4a`), sample utterances, name, "This is a different voice", re-render, restart processing from a step, delete the recording
- `/voices`: all voices with name, meetings, last clip, merge and delete
- Naming, merging and moving a voice rebuild the transcripts and notes of affected meetings at once

## 8. Privacy and security

**Local only.** Audio, text and voice data stay on the Mac. The app uses no cloud API. Network access is limited to downloading dependencies and models (Python and Swift packages, Whisper models from Hugging Face, FluidAudio models, the Ollama model). Hugging Face telemetry is disabled and `DO_NOT_TRACK` is set in `ma/__init__.py`. Whisper model revisions are pinned (`asr.REVISIONS`).

**Data folder.** `ma` and the menu app set `umask 077` and make the data folder 700 and its files 600 (`paths.secure_home`, `Paths.securePermissions`). The terminal `record` and `probe` modes apply it too. Notes in the Obsidian vault are outside this folder.

**Web UI.** Other sites open in the browser can send requests to `127.0.0.1`, so:

- `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding protection)
- Every request needs a session cookie (`ma_session_<port>`, `HttpOnly`, `SameSite=Strict`). The menu app opens the page with a `?token=` link, and `ma ui` prints one in a terminal. The server sets the cookie and redirects to a URL without the token. The secret lives in `ui.token` (600) and stays the same between runs. Before opening the link, the menu app checks `/ma-identity`: the server must return an HMAC of a random nonce, so another program on the port never receives the token
- POST requests also need the header `X-MA: 1` and no foreign `Origin`

**Notes.** Model and speech text is escaped by `notes.safe`: `<` and `![` cannot act as HTML, block markers or embeds that load by themselves.

**Deletion** (`forget.py`):

- `ma voices delete <id>` or the UI button erases the voiceprint, samples and name. The person's speech stays in transcripts as "Unknown". Stored `voices.json` and `minutes.json` are scrubbed, note file names containing the name are renamed, and notes are rebuilt
- `ma delete <id>` or the UI button removes the recording folder, its registry data, voices known only from it, and its notes. Text you added to a note stays. `--keep-notes` keeps the notes
- The registry is vacuumed after deletion
- Both refuse while the recording is being written or processed
- Logs do not record names or meeting titles. Deletion also removes lines that mention the deleted name, recording id or note from older logs

See also [SECURITY.md](../SECURITY.md).
