"""FlipaClip-style onion skinning, motion-path overlays and annotated contact sheets.

These are the agent's *eyes*: render frames, then call :func:`onion_skin` on a
frame to see previous drawings in red and upcoming drawings in green, exactly
like FlipaClip / Harmony light-table. Use :func:`contact_sheet` to review a
whole shot at a glance (thumbnails + frame numbers + key/phoneme/notes).

All compositing here is done with numpy/Pillow on already-rendered PNGs, so it
is fast and needs no browser.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ImageLike = Union[str, Path, Image.Image]

PAST_COLOR = (230, 40, 50)  # FlipaClip red = previous frames
FUTURE_COLOR = (40, 190, 70)  # FlipaClip green = next frames


def _load(img: ImageLike) -> Image.Image:
    if isinstance(img, Image.Image):
        return img.convert("RGBA")
    return Image.open(img).convert("RGBA")


@lru_cache(maxsize=16)
def font(size: int = 14, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "DejaVuSans.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def ink_mask(img: Image.Image, mode: str = "auto", background: Optional[Sequence[int]] = None) -> np.ndarray:
    """Float mask (0..1) of the "drawing" in a frame.

    mode="alpha": use transparency (transparent character renders).
    mode="luma":  darkness (line art on white).
    mode="diff":  difference from the background colour (full-colour frames).
    mode="auto":  alpha if the frame has real transparency, else diff vs. the
                  most common border colour.
    """
    a = np.asarray(img, dtype=np.float32) / 255.0
    rgb, alpha = a[..., :3], a[..., 3]
    if mode == "auto":
        mode = "alpha" if alpha.min() < 0.98 else "diff"
    if mode == "alpha":
        return alpha
    if mode == "luma":
        lum = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
        return np.clip((1 - lum) * 1.4, 0, 1) * alpha
    if background is None:
        border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
        background = np.median(border, axis=0)
    bg = np.asarray(background, dtype=np.float32)
    if bg.max() > 1:
        bg = bg / 255.0
    d = np.abs(rgb - bg).max(axis=-1)
    return np.clip((d - 0.04) * 3.0, 0, 1) * alpha


def _tint(mask: np.ndarray, color: Sequence[int], opacity: float) -> np.ndarray:
    h, w = mask.shape
    out = np.zeros((h, w, 4), dtype=np.float32)
    out[..., 0] = color[0] / 255
    out[..., 1] = color[1] / 255
    out[..., 2] = color[2] / 255
    out[..., 3] = mask * opacity
    return out


def _over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    sa = src[..., 3:4]
    da = dst[..., 3:4]
    oa = sa + da * (1 - sa)
    rgb = (src[..., :3] * sa + dst[..., :3] * da * (1 - sa)) / np.maximum(oa, 1e-6)
    return np.concatenate([rgb, oa], axis=-1)


def onion_skin(
    current: ImageLike,
    previous: Sequence[ImageLike] = (),
    upcoming: Sequence[ImageLike] = (),
    *,
    opacity: float = 0.5,
    falloff: float = 0.55,
    mode: str = "auto",
    current_style: str = "full",  # "full" | "ink" (draw current frame as black ink only)
    paper: Sequence[int] = (250, 248, 244),
    label: Optional[str] = None,
) -> Image.Image:
    """Composite a light-table view.

    ``previous`` is ordered nearest-first (frame-1, frame-2, ...), same for
    ``upcoming`` (frame+1, frame+2, ...). Each step further away is multiplied
    by ``falloff``. Past = red, future = green, current = on top.
    """
    cur = _load(current)
    w, h = cur.size
    ghosts = []
    for i, img in enumerate(previous):
        ghosts.append((i, img, PAST_COLOR))
    for i, img in enumerate(upcoming):
        ghosts.append((i, img, FUTURE_COLOR))
    ghost_imgs = {}
    for i, img, color in ghosts:
        im = _load(img)
        ghost_imgs[(i, color)] = im if im.size == (w, h) else im.resize((w, h))

    cur_arr = np.asarray(cur, dtype=np.float32) / 255
    opaque = cur_arr[..., 3].min() > 0.98
    if mode == "auto":
        # Opaque full-colour renders: estimate the static background as the
        # per-pixel median of the window, so only *moving* drawing shows up.
        mode = "median" if opaque and len(ghosts) >= 2 else ("diff" if opaque else "alpha")
    if mode == "median":
        stack = np.stack([cur_arr[..., :3]] + [np.asarray(g, dtype=np.float32)[..., :3] / 255 for g in ghost_imgs.values()])
        bg = np.median(stack, axis=0)

        def mask_of(im):
            d = np.abs(np.asarray(im, dtype=np.float32)[..., :3] / 255 - bg).max(axis=-1)
            return np.clip((d - 0.06) * 4.0, 0, 1)
    else:
        def mask_of(im):
            return ink_mask(im, mode)

    canvas = np.zeros((h, w, 4), dtype=np.float32)
    canvas[..., :3] = np.asarray(paper, dtype=np.float32) / 255
    canvas[..., 3] = 1.0
    if opaque and current_style == "full":
        # current frame washed out as context (light-table look)
        washed = cur_arr.copy()
        washed[..., :3] = washed[..., :3] * 0.45 + canvas[..., :3] * 0.55
        canvas = washed
    # farthest first so the nearest ghosts sit on top
    for i, _, color in sorted(ghosts, key=lambda t: -t[0]):
        canvas = _over(canvas, _tint(mask_of(ghost_imgs[(i, color)]), color, opacity * (falloff ** i)))

    if current_style == "ink":
        canvas = _over(canvas, _tint(mask_of(cur), (20, 18, 28), 1.0))
    elif opaque:
        m = mask_of(cur) if mode in ("median", "diff") else np.ones((h, w), dtype=np.float32)
        top = cur_arr.copy()
        top[..., 3] = m
        canvas = _over(canvas, top)
    else:
        canvas = _over(canvas, cur_arr)
    out = Image.fromarray(np.clip(canvas * 255, 0, 255).astype(np.uint8), "RGBA")
    if label:
        _badge(out, label)
    _legend(out, len(previous), len(upcoming))
    return out


def _badge(img: Image.Image, text: str) -> None:
    d = ImageDraw.Draw(img)
    f = font(max(12, img.width // 60), bold=True)
    tw = d.textlength(text, font=f)
    pad = 6
    d.rounded_rectangle([8, 8, 8 + tw + pad * 2, 8 + f.size + pad * 2], radius=6, fill=(20, 18, 28, 210))
    d.text((8 + pad, 8 + pad - 1), text, fill=(255, 255, 255, 255), font=f)


def _legend(img: Image.Image, n_prev: int, n_next: int) -> None:
    if not (n_prev or n_next):
        return
    d = ImageDraw.Draw(img)
    f = font(max(11, img.width // 80))
    y = img.height - f.size - 14
    x = 10
    for text, col in ((f"-{n_prev} past", PAST_COLOR), (f"+{n_next} next", FUTURE_COLOR)):
        d.rectangle([x, y + 3, x + 12, y + 15], fill=col + (255,))
        d.text((x + 17, y), text, fill=(30, 30, 30, 255), font=f)
        x += int(d.textlength(text, font=f)) + 40


def onion_from_sequence(frames: Sequence[ImageLike], index: int, *, before: int = 2, after: int = 1, **kw) -> Image.Image:
    """Onion skin for ``frames[index]`` using its neighbours in a rendered sequence."""
    prev = [frames[index - k] for k in range(1, before + 1) if index - k >= 0]
    nxt = [frames[index + k] for k in range(1, after + 1) if index + k < len(frames)]
    kw.setdefault("label", f"frame {index}")
    return onion_skin(frames[index], prev, nxt, **kw)


def motion_path_overlay(
    base: ImageLike,
    tracks: dict[str, Sequence[Sequence[float]]],
    *,
    highlight: Optional[int] = None,
    colors: Optional[dict[str, tuple]] = None,
    scale: float = 1.0,
) -> Image.Image:
    """Draw spacing charts: each track's points (one per frame) + connecting arc.

    Uneven dot spacing = ease; a sudden big gap = a pop. Use with
    ``motion_engine.spacing_report`` for numbers.
    """
    img = _load(base).copy()
    d = ImageDraw.Draw(img, "RGBA")
    palette = [(255, 80, 60), (40, 140, 255), (250, 190, 20), (170, 80, 255), (20, 200, 160)]
    f = font(max(10, img.width // 110))
    for ti, (name, pts) in enumerate(tracks.items()):
        col = (colors or {}).get(name, palette[ti % len(palette)])
        sp = [(p[0] * scale, p[1] * scale) for p in pts]
        if len(sp) > 1:
            d.line(sp, fill=col + (170,), width=2)
        for i, (x, y) in enumerate(sp):
            r = 5 if i == highlight else 3
            d.ellipse([x - r, y - r, x + r, y + r], fill=col + (255,), outline=(255, 255, 255, 255))
            if i % 4 == 0 or i == highlight:
                d.text((x + 5, y - 14), str(i), fill=col + (255,), font=f)
        if sp:
            d.text((sp[0][0] + 6, sp[0][1] + 4), name, fill=col + (255,), font=f)
    return img


def contact_sheet(
    frames: Sequence[ImageLike],
    *,
    labels: Optional[Sequence[str]] = None,
    notes: Optional[Sequence[str]] = None,
    columns: int = 6,
    thumb_width: int = 280,
    title: Optional[str] = None,
    indices: Optional[Sequence[int]] = None,
    highlight: Iterable[int] = (),
    gap: int = 10,
) -> Image.Image:
    """Grid of thumbnails with frame numbers / labels, like a storyboard sheet."""
    if not frames:
        raise ValueError("no frames given")
    first = _load(frames[0])
    aspect = first.height / first.width
    tw = thumb_width
    th = int(round(tw * aspect))
    cap_h = 38 if notes else 22
    n = len(frames)
    cols = max(1, min(columns, n))
    rows = math.ceil(n / cols)
    header = 46 if title else 0
    W = cols * tw + (cols + 1) * gap
    H = header + rows * (th + cap_h) + (rows + 1) * gap
    sheet = Image.new("RGBA", (W, H), (32, 30, 40, 255))
    d = ImageDraw.Draw(sheet)
    if title:
        d.text((gap, 12), title, fill=(255, 255, 255, 255), font=font(22, bold=True))
    f = font(13, bold=True)
    fn = font(11)
    hl = set(highlight)
    for i, fr in enumerate(frames):
        r, c = divmod(i, cols)
        x = gap + c * (tw + gap)
        y = header + gap + r * (th + cap_h + gap)
        im = _load(fr).resize((tw, th), Image.LANCZOS)
        checker = _checker(tw, th)
        checker.alpha_composite(im)
        sheet.paste(checker, (x, y))
        idx = indices[i] if indices is not None else i
        border = (255, 200, 40, 255) if idx in hl else (70, 68, 82, 255)
        d.rectangle([x - 1, y - 1, x + tw, y + th], outline=border, width=2 if idx in hl else 1)
        cap = f"#{idx}"
        if labels and i < len(labels) and labels[i]:
            cap += f"  {labels[i]}"
        d.text((x + 2, y + th + 3), cap, fill=(235, 235, 240, 255), font=f)
        if notes and i < len(notes) and notes[i]:
            txt = notes[i]
            while d.textlength(txt, font=fn) > tw - 4 and len(txt) > 4:
                txt = txt[:-2]
            if txt != notes[i]:
                txt = txt[:-1] + "…"
            d.text((x + 2, y + th + 20), txt, fill=(170, 170, 185, 255), font=fn)
    return sheet


@lru_cache(maxsize=8)
def _checker_cached(w: int, h: int, s: int = 10) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w]
    v = (((xx // s) + (yy // s)) % 2) * 30 + 200
    arr = np.stack([v, v, v, np.full_like(v, 255)], axis=-1).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def _checker(w: int, h: int) -> Image.Image:
    return _checker_cached(w, h).copy()


def flip_strip(frames: Sequence[ImageLike], *, height: int = 180) -> Image.Image:
    """Horizontal filmstrip (quick 'flip' preview for chat responses)."""
    ims = [_load(f) for f in frames]
    thumbs = [im.resize((int(im.width * height / im.height), height), Image.LANCZOS) for im in ims]
    W = sum(t.width for t in thumbs) + 4 * (len(thumbs) + 1)
    strip = Image.new("RGBA", (W, height + 8), (20, 20, 24, 255))
    x = 4
    for t in thumbs:
        strip.alpha_composite(t, (x, 4))
        x += t.width + 4
    return strip
