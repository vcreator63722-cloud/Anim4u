"""FFmpeg discovery and invocation helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from typing import Sequence


class FFmpegError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """Locate ffmpeg: $CELCRAFT_FFMPEG, then PATH, then the imageio-ffmpeg bundled binary."""
    env = os.environ.get("CELCRAFT_FFMPEG")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        raise FFmpegError("ffmpeg not found. Install ffmpeg, `pip install imageio-ffmpeg`, or set CELCRAFT_FFMPEG.") from e


def run_ffmpeg(args: Sequence[str], *, input_bytes: bytes | None = None, capture: bool = False) -> bytes:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, input=input_bytes, stdout=subprocess.PIPE if capture else subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {' '.join(cmd[:12])} ...\n{proc.stderr.decode(errors='replace')[-2000:]}")
    return proc.stdout if capture else b""


@lru_cache(maxsize=1)
def encoders() -> frozenset[str]:
    out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)
