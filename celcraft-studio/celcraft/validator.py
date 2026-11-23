"""Automated frame QA — run this before every export.

Checks (per timeline frame, on rendered PNGs):

* blank           frame has (almost) no content: fully transparent or a flat colour
* bounds          subject touches the frame edge / leaves the title-safe area
                  (measured on the ``subject_layer`` mask when available, else
                  on difference from the frame's border colour)
* flicker         abnormal frame-to-frame change (mean abs diff spike vs. the
                  shot's median) that is *not* tagged as impact/smear/key
* frozen          unintended identical frames in a run longer than the declared
                  holds (rendering bug or forgotten drawing)
* flash           photosensitivity: > 3 large luminance flashes within any 1 s
                  window (WCAG 2.3.1 / Harding-style rule of thumb)

Returns a JSON-friendly report with per-frame metrics and a pass/fail list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np
from PIL import Image


@dataclass
class Issue:
    frame: int
    check: str
    severity: str  # "error" | "warning"
    message: str

    def as_dict(self) -> dict:
        return {"frame": self.frame, "check": self.check, "severity": self.severity, "message": self.message}


@dataclass
class Report:
    frames: int
    issues: list[Issue] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "frames": self.frames,
            "errors": sum(1 for i in self.issues if i.severity == "error"),
            "warnings": sum(1 for i in self.issues if i.severity == "warning"),
            "issues": [i.as_dict() for i in self.issues],
            "metrics": self.metrics,
        }


def _load_small(path: Union[str, Path], max_side: int = 320) -> np.ndarray:
    im = Image.open(path).convert("RGBA")
    s = max_side / max(im.size)
    if s < 1:
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.BILINEAR)
    return np.asarray(im, dtype=np.float32) / 255.0


def _luma(a: np.ndarray) -> np.ndarray:
    rgb = a[..., :3] * a[..., 3:4]
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def _content_mask(a: np.ndarray) -> np.ndarray:
    alpha = a[..., 3]
    if alpha.min() < 0.98:
        return alpha > 0.1
    rgb = a[..., :3]
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    return np.abs(rgb - bg).max(axis=-1) > 0.08


def validate_frames(
    frames: Sequence[Union[str, Path]],
    *,
    fps: float = 24,
    tags: Optional[Sequence[str]] = None,
    holds_timeline: Optional[Sequence[int]] = None,
    subject_frames: Optional[Sequence[Union[str, Path]]] = None,
    safe_area: float = 0.9,
    flicker_factor: float = 4.0,
    check_bounds: bool = True,
) -> Report:
    """Validate a rendered sequence.

    ``tags``: per-frame key type (e.g. from ``Frame.key``); "impact"/"smear"/"key"
    frames are allowed to spike. ``holds_timeline``: drawing index per frame, so
    declared holds are not reported as frozen. ``subject_frames``: optional
    transparent renders of only the character for precise bounds checks.
    """
    rep = Report(frames=len(frames))
    if not frames:
        rep.issues.append(Issue(0, "blank", "error", "no frames"))
        return rep
    arrs = [_load_small(f) for f in frames]
    lum = [_luma(a) for a in arrs]
    diffs = [0.0] + [float(np.abs(lum[i] - lum[i - 1]).mean()) for i in range(1, len(lum))]
    mean_l = [float(l.mean()) for l in lum]
    nonzero = [d for d in diffs[1:] if d > 1e-5]
    med = float(np.median(nonzero)) if nonzero else 0.0
    allowed = {"impact", "smear", "key", "extreme"}

    for i, a in enumerate(arrs):
        # blank
        alpha = a[..., 3]
        if alpha.max() < 0.02:
            rep.issues.append(Issue(i, "blank", "error", "frame is fully transparent"))
        elif float(a[..., :3].std()) < 0.004:
            rep.issues.append(Issue(i, "blank", "error", "frame is a single flat colour"))

        # bounds
        if check_bounds and subject_frames and i < len(subject_frames):
            mask = _content_mask(_load_small(subject_frames[i]))
            if mask.any():
                ys, xs = np.nonzero(mask)
                h, w = mask.shape
                mx, my = w * (1 - safe_area) / 2, h * (1 - safe_area) / 2
                if xs.min() <= 0 or ys.min() <= 0 or xs.max() >= w - 1 or ys.max() >= h - 1:
                    rep.issues.append(Issue(i, "bounds", "warning", "subject touches the frame edge"))
                elif xs.min() < mx or ys.min() < my or xs.max() > w - mx or ys.max() > h - my:
                    rep.issues.append(Issue(i, "bounds", "warning", f"subject leaves the {int(safe_area * 100)}% title-safe area"))

        # flicker
        tag = tags[i] if tags and i < len(tags) else None
        prev_tag = tags[i - 1] if tags and 0 < i <= len(tags) else None
        if i > 0 and med > 0 and diffs[i] > med * flicker_factor and diffs[i] > 0.02:
            if tag not in allowed and prev_tag not in allowed:
                rep.issues.append(Issue(i, "flicker", "warning", f"frame diff {diffs[i]:.3f} is {diffs[i] / med:.1f}x the shot median ({med:.3f}); tag it impact/smear if intended"))

    # frozen runs (identical frames beyond declared holds)
    run_start = 0
    for i in range(1, len(arrs) + 1):
        same = i < len(arrs) and diffs[i] < 1e-6
        if not same:
            length = i - run_start
            if length >= max(4, int(fps / 3)):
                declared = holds_timeline is not None and len(set(holds_timeline[run_start:i])) == 1
                if not declared:
                    rep.issues.append(Issue(run_start, "frozen", "warning", f"{length} identical frames ({run_start}-{i - 1}) not declared as a hold"))
            run_start = i

    # photosensitive flashes: luminance swings > 0.2 (relative) with direction reversals
    flashes = []
    for i in range(1, len(mean_l)):
        delta = mean_l[i] - mean_l[i - 1]
        if abs(delta) > 0.2:
            flashes.append(i)
    win = int(round(fps))
    for i in range(len(flashes)):
        in_win = [f for f in flashes if flashes[i] <= f < flashes[i] + win]
        if len(in_win) > 6:  # > 3 flash *pairs* (on/off) per second
            rep.issues.append(Issue(flashes[i], "flash", "error", f"{len(in_win) // 2} large luminance flashes within 1s — photosensitivity risk"))
            break

    rep.metrics = {
        "mean_luma": [round(x, 4) for x in mean_l],
        "frame_diff": [round(x, 4) for x in diffs],
        "median_diff": round(med, 5),
        "flash_frames": flashes,
    }
    return rep


def validate_scene_render(scene, frames_dir: Union[str, Path], **kw) -> Report:
    frames = sorted(Path(frames_dir).glob("frame_*.png"))
    tl = scene.timeline()
    tags = [scene.frames[d].key for d in tl]
    rep = validate_frames(frames, fps=scene.fps, tags=tags, holds_timeline=tl, **kw)
    if len(frames) != len(tl):
        rep.issues.append(Issue(0, "count", "error", f"{len(frames)} rendered frames but scene timeline has {len(tl)}"))
    return rep


def validate_scene_json(scene) -> list[dict]:
    """Static lint of a scene before rendering (cheap, catches common agent mistakes)."""
    issues: list[dict] = []
    if not scene.frames:
        issues.append({"severity": "error", "message": "scene has no frames"})
    for i, f in enumerate(scene.frames):
        if not f.layers:
            issues.append({"severity": "warning", "drawing": i, "message": "drawing has no layers"})
        kinds = {l.kind for l in f.layers}
        if f.layers and "line" not in kinds and not any(el.type == "group" for l in f.layers for el in l.elements):
            issues.append({"severity": "info", "drawing": i, "message": "no layer with kind='line' (ink) — intended?"})
    keys = [f.key for f in scene.frames]
    if scene.frames and "key" not in keys and "extreme" not in keys:
        issues.append({"severity": "info", "message": "no drawings tagged key/extreme — tag keys so QA and contact sheets can find them"})
    if scene.duration_frames > 1:
        holds = [f.hold for f in scene.frames]
        if len(set(holds)) == 1 and holds[0] >= 3:
            issues.append({"severity": "warning", "message": f"every drawing is held {holds[0]} frames — motion will look choppy; use ones for fast action"})
    return issues
