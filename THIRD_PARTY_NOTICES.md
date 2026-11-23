# Third-party notices

This project is licensed under Apache-2.0 (see `LICENSE`). It bundles no models and no third-party binaries: dependencies and models are downloaded to your Mac during installation and first use, each under its own license.

## Models

| Model | Used for | License | Source |
|---|---|---|---|
| Whisper large-v3-turbo and large-v3 (OpenAI), MLX conversions by mlx-community | Speech recognition | MIT | [turbo](https://huggingface.co/mlx-community/whisper-large-v3-turbo), [large-v3](https://huggingface.co/mlx-community/whisper-large-v3-mlx) |
| Silero VAD | Speech detection | MIT | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) |
| pyannote Community-1, Core ML conversion by Fluid Inference | Speaker diarization and voice embeddings | CC-BY-4.0 | [FluidInference/speaker-diarization-coreml](https://huggingface.co/FluidInference/speaker-diarization-coreml) |
| Qwen 3.5 9B (Alibaba Cloud), via Ollama | Meeting minutes | Apache-2.0 | [ollama.com/library/qwen3.5](https://ollama.com/library/qwen3.5) |

### Speaker diarization attribution

The diarization models are modified Core ML conversions made by Fluid Inference. They derive from:

- [pyannote speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) by pyannote
- speaker embeddings from [WeSpeaker](https://github.com/wenet-e2e/wespeaker)
- PLDA parameters from Brno University of Technology, BUT Speech@FIT

They are licensed under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). The exact scope and citations are in the model repository's `NOTICE.md` and `README.md`.

## Libraries

| Library | License |
|---|---|
| [FluidAudio](https://github.com/FluidInference/FluidAudio) | Apache-2.0, with its own third-party notices |
| [mlx](https://github.com/ml-explore/mlx), [mlx-whisper](https://github.com/ml-explore/mlx-examples) | MIT |
| [PyTorch](https://github.com/pytorch/pytorch) (required by Silero VAD) | Apache-2.0, BSD and other permissive licenses, listed in its package metadata |
| [FastAPI](https://github.com/fastapi/fastapi), [Starlette](https://github.com/encode/starlette), [Uvicorn](https://github.com/encode/uvicorn), [Jinja](https://github.com/pallets/jinja) | MIT or BSD-3-Clause |
| [NumPy](https://numpy.org), [SciPy](https://scipy.org) | BSD-style |
| [python-soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause; its wheel includes libsndfile (LGPL-2.1) |
| [certifi](https://github.com/certifi/python-certifi), [tqdm](https://github.com/tqdm/tqdm) | MPL-2.0 (tqdm also MIT) |

The full list with exact versions is in `pipeline/uv.lock`. [FFmpeg](https://ffmpeg.org), [Ollama](https://ollama.com) and [uv](https://github.com/astral-sh/uv) are separate programs that you install yourself. If you redistribute a packaged build, review the licenses of everything you include.

Slack and Obsidian are trademarks of their respective owners. This project is not affiliated with or endorsed by them.
