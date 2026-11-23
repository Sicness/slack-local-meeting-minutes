"""Processing of recorder output: transcription, voices and meeting minutes."""

import os

# nothing about the user's work leaves the Mac, not even library usage statistics
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")
