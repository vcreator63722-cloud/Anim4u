import io
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@lru_cache(maxsize=1)
def _renderer_ok() -> str:
    try:
        from celcraft.renderer_bridge import render_payloads

        render_payloads([{"width": 8, "height": 8, "layers": []}], workers=1)
        return ""
    except Exception as e:  # noqa: BLE001
        return str(e)[:300]


@lru_cache(maxsize=1)
def _ffmpeg_ok() -> str:
    try:
        from celcraft._ffmpeg import ffmpeg_exe

        ffmpeg_exe()
        return ""
    except Exception as e:  # noqa: BLE001
        return str(e)


@pytest.fixture
def needs_renderer():
    err = _renderer_ok()
    if err:
        pytest.skip(f"headless Chromium unavailable: {err}")


@pytest.fixture
def needs_ffmpeg():
    err = _ffmpeg_ok()
    if err:
        pytest.skip(f"ffmpeg unavailable: {err}")


def png_to_array(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


@pytest.fixture
def synthetic_frames(tmp_path):
    """12 frames of a dark ball moving right on a white background."""
    paths = []
    for i in range(12):
        im = Image.new("RGBA", (160, 90), (255, 255, 255, 255))
        from PIL import ImageDraw

        d = ImageDraw.Draw(im)
        x = 20 + i * 10
        d.ellipse([x - 8, 37, x + 8, 53], fill=(20, 20, 30, 255))
        p = tmp_path / f"frame_{i:05d}.png"
        im.save(p)
        paths.append(p)
    return paths
