# Decisions

The main design decisions and why they were made. Numbers are stable: code and docs refer to them as D1, D2, and so on.

| # | Topic | Decision |
|---|---|---|
| D1 | Purpose | Record your own calls and produce clear meeting minutes. Noise suppression is out of scope |
| D2 | Audio capture | No virtual microphone or driver. The mic is recorded directly, and Slack audio comes through a Core Audio Process Tap on Slack's processes only |
| D3 | Supported apps | Slack huddles only. Other call apps are not supported |
| D4 | Start and stop | Auto-recording is opt-in: off by default, and it needs a one-time consent notice. When on, recording runs while Slack holds the mic. A notification offers "Don't Record" |
| D5 | Headphones or room | On headphones the mic track is "Me". On any speakers (built-in, DisplayPort/HDMI monitor, AirPlay) it is "Room". Bluetooth, USB and the headphone jack count as headphones. The Room track is labeled and cleaned of speaker echo, but not diarized |
| D6 | Languages | English and Russian. Root `language` sets the UI, notes and minutes prompt. `[asr] language` sets the spoken language and defaults to `language`. Each language has a starter glossary |
| D7 | Transcription | Local Whisper large-v3-turbo through mlx-whisper (Apple GPU). Each track whole, glossary prompt in every window, one temperature, VAD as a filter after Whisper. Full large-v3 rechecks weak spots |
| D8 | Diarization | Local FluidAudio: the pyannote community-1 pipeline on CoreML (`diarizer/`, `ma-diarize`), on CPU and Neural Engine. Each utterance chunk is labeled by its own embedding. The owner's voice comes from the mic track, with no model |
| D9 | Names | No calendar or participant list. Unknown voices get permanent numbers ("Voice 1", "Voice 2") and are recognized in later meetings. You assign names in the UI, and a name applies to all past and future meetings |
| D10 | Minutes | Local model through Ollama: `qwen3.5:9b` with reasoning. No cloud API. Nothing leaves the Mac, not even text |
| D11 | Minutes format | Schema-bound JSON from the model, rendered by our code. See [minutes-format.md](minutes-format.md) |
| D12 | Output | Markdown notes in an Obsidian vault. Nothing is sent anywhere else |
| D13 | Retention | Audio of fully processed recordings is deleted after 7 days (`audio_retention_days`). Transcripts and minutes stay until you delete them |
| D14 | UI | A separate UI with a list of recordings, where you name voices, rather than a popup after each meeting |
| D15 | Stack | Recorder in Swift (menu bar app). Pipeline and UI in Python (uv). The UI is a local FastAPI site opened from the recorder menu |
| D16 | Repository | A standalone repository that holds code only. Meeting data lives outside it, in `~/MeetingAssistant` and the Obsidian vault |
| D17 | Latency | Minutes are ready a few minutes after the call. Live transcription is out of scope |
| D18 | Recorder signing | A self-signed "Meeting Assistant Dev" certificate from Keychain Access, created once. Ad-hoc signing is a fallback with a warning |
| D19 | "Me" track | An IOProc directly on the input device Slack uses, instead of `AVAudioEngine`. Never open a Bluetooth mic before Slack, and release it 3 s after Slack |
| D20 | "Others" track | A tap on the list of Slack processes, updated live with `setDescription`. Not a tap by bundle id |
| D21 | Recorder settings | The recorder reads `[recorder]`, root `language` and `[ui] port` from `config.toml` with a small TOML-subset parser. The pipeline owns the file |
| D22 | Glossary | The working copy is `~/MeetingAssistant/glossary.txt`, created from a starter in `pipeline/src/ma/data/` for the spoken language. Only `!` terms go into the Whisper prompt (order is priority). Recognition errors are fixed with `wrong => right` replacements |
| D23 | Processing load | `ma` runs in macOS background mode, so transcription does not slow Slack or the system |
| D24 | Voice registry | Meeting voices match registry centroids by cosine similarity (threshold 0.65), one to one. A cluster with under 5 s of speech creates no voice. The voice number is its id and does not change on reprocessing |
| D25 | Processing after a call | The recorder runs `ma process` after each recording, one at a time. During a call or recording, processing is paused (`SIGSTOP` to the process group) and resumes afterwards |

## Why

**D2.** A virtual device is only needed to change audio on the fly. For recording, a Process Tap (macOS 14.2+) is enough. Slack audio is not disturbed, no latency is added, and nothing needs configuring in Slack. It also gives two separate tracks for free.

**D3.** One app keeps call detection and process handling simple. `[recorder] bundle_id_prefixes` is the extension point, and the recorder's own bundle id is used for testing without Slack.

**D4.** A recording captures other people. The app must not do that until its user has seen what it does and accepted responsibility, so automatic recording never starts on its own after install. See [Recording consent](architecture.md#recording-consent).

**D5.** On speakers the mic also hears the remote side, so the track cannot be labeled as one person. Words that also appear on the remote track at the same moment are removed as echo.

**D6.** Speech and notes can differ: `[asr] language` lets you read English notes for Russian speech. Work calls mix languages and jargon, which is why the glossary matters (D22).

**D7.**

- Cutting audio by VAD produced more invented text than a whole track, because Whisper pads a short piece with zeros and invents text on the tail
- Without the prompt in every window, terms drift after the first 30 s
- The default temperature fallback adds no words but costs much more time on noisy tracks. The rescue pass handles the loops it used to fix
- large-v3 on a whole track is not better than turbo and is slower, but it helps on weak spots. So it runs only there, and the more confident variant wins

**D7, Parakeet v3** (`parakeet-tdt-0.6b-v3` through `parakeet-mlx`): rejected as both the main and the second model. It is faster and clean on silence. But on Russian speech with English terms it jumps between languages and loses utterances. On synthetic `say` speech it got 14 of 26 glossary terms wrong, against 0 for Whisper with the glossary. Beam search with boosted glossary words helped only a little, ran much slower and invented text on noise. Worth revisiting if a version with language selection appears.

**D8.** FluidAudio's models are open (about 23 MB, no token) and do not use the GPU, so diarization does not disturb Slack. pyannote needs PyTorch (about 2 GB) and a Hugging Face token, and on a Mac it needs the GPU to be fast.

The diarizer often finds short replies as separate segments but gives them a neighbor's label. On synthetic `say` speech about a quarter to a third of utterances were wrong by segment label alone. A chunk's own embedding identifies the voice much better (69 of 70 correct on the same synthetic speech). So utterances are cut at diarizer segments and pauses, and each chunk's voice comes from its embedding.

**D8, pyannote community-1** (pyannote.audio 4.0.7): rejected. On CPU at background priority it ran slower than real time, and on the GPU it competes with Slack (D23). On synthetic speech its accuracy was slightly better (DER 4.9–5.7% against 5.6–6.6%; wrong by segment label 17–23% against 26–30%). That is still above the 15% target, so it would need chunk-level labeling too.

**D9.** People you meet with tend to stay the same, so the registry fills up quickly. Not using a calendar means less setup and less personal data.

**D10.** Meeting text is sensitive, so the app sends none of it to a cloud service. Among the local models compared (`mistral-small3.2:24b`, `vikhr_nemo_12b`, `qwen3.5:9b`), qwen3.5 captured the most content. The others lost much of the content or invented due dates.

The prompt rules (a due date only if spoken, status reports and complaints are not decisions, one pass per participant, a source time on each item) removed most invented details. Reasoning improves accuracy but sometimes loops, so it is capped at 16K tokens and followed by a retry without it. The model can still miss points on long meetings, so minutes are a draft with links back to the transcript.

**D13.** Audio is the most sensitive data and is only needed for a short time, to check voices. Only recordings that finished the `archive` step are cleaned, so audio that has not been fully processed is not lost.

**D15.** Whisper and the voice registry belong in Python. Swift stays where it is required: audio capture, the menu bar and notifications. Diarization is a small Swift program (`ma-diarize`, D8) that `ma` calls like ffmpeg. All voice logic stays in Python.

**D18.** macOS ties microphone and audio capture permissions to the code signing requirement. With an ad-hoc signature that is the binary hash, so after each rebuild the permissions silently stop working: the switch in System Settings stays on and the tap returns silence. With a certificate the requirement is `certificate leaf`, which survives rebuilds.

**D19.** Slack may use a mic other than the system default. An IOProc gives an exact host time for each buffer and shares code with the tap. A Bluetooth mic opened before Slack would switch headphones to headset mode (16 kHz, poor sound). One not released after the call would keep them there.

**D20.** A tap by bundle id and a tap by process list produce identical audio. But a bundle id tap does not pick up a process launched after it was created, so audio would be lost after a Slack restart. A process list can be updated on a live tap without a new segment.

**D21.** The recorder needs only a few keys. An external library would be overkill, and having Python write JSON for the recorder would couple the two.

**D22.** The glossary will hold colleagues' names, so it lives with the data and not in the repository. The Whisper prompt is limited to about 220 tokens, so `!` goes only on terms Whisper gets wrong without help.

**D23.** Whisper runs on the GPU and can make the Slack interface lag during a call. Background mode lowers CPU, I/O and, in practice, GPU priority. A slow test checks that one hour of synthetic audio still processes in under 5 minutes.

**D24.** The same person scores far higher across meetings than different people do within one, which is what makes a fixed threshold workable. Very similar voices can still collide: two synthetic `say` voices (Milena and Lesya) score 0.70, above the threshold, and need a manual "This is a different voice". Matching is one to one because two clusters of one meeting cannot be the same registry voice, unless they are at least 0.75 similar to each other, which means the diarizer split one person. An embedding from under about 5 s of speech is unreliable, so such a cluster may take a known voice but never creates one.

**D25.** Transcription and minutes use the GPU, and calls can be back to back. Pausing with a signal is simpler and more reliable than teaching each step to stop: `SIGSTOP` halts uv, python and ma-diarize together, and after `SIGCONT` they continue where they were. An Ollama request already sent still finishes on the server.
