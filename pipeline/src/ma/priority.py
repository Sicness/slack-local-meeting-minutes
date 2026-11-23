"""Processing priority: transcription must not slow down Slack or the rest of the system.

- background: macOS background mode (PRIO_DARWIN_BG), lower CPU and I/O priority,
  macOS gives resources to interactive apps. The default
- low: nice 10 only
- normal: unchanged
"""

import ctypes
import os
import sys

PRIO_DARWIN_PROCESS = 4
PRIO_DARWIN_BG = 0x1000
MODES = ("background", "low", "normal")


def apply(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"priority: {mode!r}, expected one of {MODES}")
    if mode == "normal":
        return mode
    try:
        os.setpriority(os.PRIO_PROCESS, 0, max(os.getpriority(os.PRIO_PROCESS, 0), 10))
    except OSError:
        pass
    if mode == "background" and sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.setpriority(PRIO_DARWIN_PROCESS, 0, PRIO_DARWIN_BG) != 0:
            return "low"
    return mode
