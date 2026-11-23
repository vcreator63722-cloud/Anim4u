"""2D puppet rigging: bones/FK, analytic two-bone IK, cel-shaded part builder,
Preston Blair mouths, anime eyes, blink tracks, line boil and a ready-made
biped character ("Pip").

Rigging is *optional*: it produces ordinary CelCraft element dicts, so any
frame can still be hand-drawn stroke by stroke. Rigs are best for keeping
volumes on-model across inbetweens; drawn strokes/smears are layered on top
for the expressive frames.

Cel part recipe (used by every body part) — an *isolated group*:

    1. base fill            (palette base colour)
    2. shadow crescent      (part minus part shifted toward the key light, alpha-locked)
    3. rim-light crescent   (part minus part shifted away from the rim light, alpha-locked)
    4. ink contour          (pressure-tapered G-pen, optional line boil)

Because each part is isolated, shadows never leak onto overlapping parts and
hidden lines never show through — the classic problem with naive
"one line layer over one colour layer" rigs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from . import motion_engine as me
from .presets import mouths

Vec = tuple[float, float]

# ---------------------------------------------------------------------------
# Affine helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Affine:
    """2D affine matrix [a, b, c, d, e, f] (canvas convention)."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def __matmul__(self, o: "Affine") -> "Affine":
        return Affine(
            self.a * o.a + self.c * o.b,
            self.b * o.a + self.d * o.b,
            self.a * o.c + self.c * o.d,
            self.b * o.c + self.d * o.d,
            self.a * o.e + self.c * o.f + self.e,
            self.b * o.e + self.d * o.f + self.f,
        )

    def apply(self, p: Sequence[float]) -> Vec:
        return (self.a * p[0] + self.c * p[1] + self.e, self.b * p[0] + self.d * p[1] + self.f)

    def as_list(self) -> list[float]:
        return [self.a, self.b, self.c, self.d, self.e, self.f]

    @staticmethod
    def translate(x: float, y: float) -> "Affine":
        return Affine(e=x, f=y)

    @staticmethod
    def rotate(deg: float, pivot: Vec = (0.0, 0.0)) -> "Affine":
        r = math.radians(deg)
        c, s = math.cos(r), math.sin(r)
        rot = Affine(c, s, -s, c)
        return Affine.translate(*pivot) @ rot @ Affine.translate(-pivot[0], -pivot[1])

    @staticmethod
    def scale(sx: float, sy: Optional[float] = None, pivot: Vec = (0.0, 0.0)) -> "Affine":
        sy = sx if sy is None else sy
        return Affine.translate(*pivot) @ Affine(sx, 0, 0, sy) @ Affine.translate(-pivot[0], -pivot[1])

    @staticmethod
    def from_list(m: Sequence[float]) -> "Affine":
        return Affine(*m)


# ---------------------------------------------------------------------------
# Bones / FK / IK
# ---------------------------------------------------------------------------

@dataclass
class Bone:
    name: str
    length: float
    angle: float = 0.0  # degrees, relative to parent
    parent: Optional[str] = None
    offset: Vec = (0.0, 0.0)  # start position in parent space (from parent's start)
    at_parent_end: bool = True  # attach at parent's tip (True) or at offset from parent start


class Skeleton:
    """Hierarchical forward kinematics."""

    def __init__(self, bones: Sequence[Bone], root: Vec = (0.0, 0.0)):
        self.bones = {b.name: b for b in bones}
        self.order = [b.name for b in bones]
        self.root = root

    def set(self, **angles: float) -> "Skeleton":
        for k, v in angles.items():
            self.bones[k].angle = v
        return self

    def solve(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for name in self.order:
            b = self.bones[name]
            if b.parent is None:
                start = (self.root[0] + b.offset[0], self.root[1] + b.offset[1])
                world_angle = b.angle
            else:
                p = out[b.parent]
                pa = math.radians(p["angle"])
                base = p["end"] if b.at_parent_end else p["start"]
                ox, oy = b.offset
                start = (base[0] + ox * math.cos(pa) - oy * math.sin(pa), base[1] + ox * math.sin(pa) + oy * math.cos(pa))
                world_angle = p["angle"] + b.angle
            r = math.radians(world_angle)
            end = (start[0] + math.cos(r) * b.length, start[1] + math.sin(r) * b.length)
            out[name] = {"start": start, "end": end, "angle": world_angle}
        return out


def two_bone_ik(root: Vec, target: Vec, l1: float, l2: float, bend: float = 1.0) -> tuple[Vec, Vec]:
    """Analytic two-bone IK (law of cosines). Returns (joint, end).

    ``bend`` = +1 / -1 picks which side the elbow/knee bends to. Targets out
    of reach are clamped so the limb straightens toward the target.
    """
    dx, dy = target[0] - root[0], target[1] - root[1]
    d = math.hypot(dx, dy)
    d_clamped = max(abs(l1 - l2) + 1e-4, min(l1 + l2 - 1e-4, d))
    base = math.atan2(dy, dx)
    cos_a = (l1 * l1 + d_clamped * d_clamped - l2 * l2) / (2 * l1 * d_clamped)
    a = math.acos(max(-1.0, min(1.0, cos_a)))
    ang = base - a * (1 if bend >= 0 else -1)
    joint = (root[0] + math.cos(ang) * l1, root[1] + math.sin(ang) * l1)
    end_dir = math.atan2(root[1] + dy * d_clamped / (d or 1) - joint[1], root[0] + dx * d_clamped / (d or 1) - joint[0])
    end = (joint[0] + math.cos(end_dir) * l2, joint[1] + math.sin(end_dir) * l2)
    return joint, end


# ---------------------------------------------------------------------------
# Line boil
# ---------------------------------------------------------------------------

def line_boil(points: Sequence[Sequence[float]], *, amplitude: float = 0.8, drawing: int = 0, seed: int = 0, cycle: int = 3) -> list[list[float]]:
    """Hand-drawn "boiling" line: subtle per-drawing wobble that cycles every
    ``cycle`` drawings (classic 3-drawing boil). Pressure values are kept."""
    if amplitude <= 0:
        return [list(p) for p in points]
    phase = drawing % max(1, cycle)
    out = []
    for i, p in enumerate(points):
        jx = me.value_noise(i * 0.7 + phase * 13.1, seed * 31 + 1) * amplitude
        jy = me.value_noise(i * 0.7 + phase * 13.1, seed * 31 + 7) * amplitude
        q = [p[0] + jx, p[1] + jy]
        if len(p) > 2:
            q.append(p[2])
        out.append(q)
    return out


# ---------------------------------------------------------------------------
# Shape builders
# ---------------------------------------------------------------------------

def ellipse_points(cx: float, cy: float, rx: float, ry: float, n: int = 20, rotation: float = 0.0) -> list[list[float]]:
    r = math.radians(rotation)
    cr, sr = math.cos(r), math.sin(r)
    pts = []
    for i in range(n):
        t = i / n * 2 * math.pi
        x, y = math.cos(t) * rx, math.sin(t) * ry
        pts.append([cx + x * cr - y * sr, cy + x * sr + y * cr])
    return pts


def bendy_limb(a: Vec, joint: Vec, b: Vec, samples: int = 10) -> list[Vec]:
    """Rubber-hose limb centreline: quadratic bezier from a to b passing through joint."""
    ctrl = (2 * joint[0] - (a[0] + b[0]) / 2, 2 * joint[1] - (a[1] + b[1]) / 2)
    return [me.bezier_point([a, ctrl, b], i / samples) for i in range(samples + 1)]


def capsule_outline(path: Sequence[Vec], r_start: float, r_end: float, cap_points: int = 5) -> list[list[float]]:
    """Closed contour of a tapered tube around a centreline (limbs, scarf, tails)."""
    n = len(path)
    if n < 2:
        return ellipse_points(path[0][0], path[0][1], r_start, r_start, 12)
    left, right = [], []
    for i in range(n):
        a = path[max(0, i - 1)]
        b = path[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        L = math.hypot(tx, ty) or 1
        nx, ny = -ty / L, tx / L
        r = me.lerp(r_start, r_end, i / (n - 1))
        left.append([path[i][0] + nx * r, path[i][1] + ny * r])
        right.append([path[i][0] - nx * r, path[i][1] - ny * r])

    def cap(center, r, from_pt, steps):
        ang0 = math.atan2(from_pt[1] - center[1], from_pt[0] - center[0])
        return [[center[0] + math.cos(ang0 - math.pi * k / (steps + 1)) * r, center[1] + math.sin(ang0 - math.pi * k / (steps + 1)) * r]
                for k in range(1, steps + 1)]

    end_cap = cap(path[-1], r_end, left[-1], cap_points)
    start_cap = cap(path[0], r_start, right[0], cap_points)
    return left + end_cap + right[::-1] + start_cap


def transform_points(points: Sequence[Sequence[float]], m: Affine) -> list[list[float]]:
    return [list(m.apply(p)) + ([p[2]] if len(p) > 2 else []) for p in points]


def shift(points: Sequence[Sequence[float]], dx: float, dy: float) -> list[list[float]]:
    return [[p[0] + dx, p[1] + dy] for p in points]


def crescent(contour: Sequence[Sequence[float]], offset: Vec, color: str, *, blend: str = "source-atop", opacity: float = 1.0, smooth: bool = True) -> dict:
    """Part shape minus the part shifted by ``offset`` -> crescent on the opposite side."""
    return {
        "type": "group",
        "isolate": True,
        "blend": blend,
        "opacity": opacity,
        "elements": [
            {"type": "polygon", "points": [list(p) for p in contour], "smooth": smooth, "fill": color},
            {"type": "polygon", "points": shift(contour, *offset), "smooth": smooth, "fill": "#000000", "blend": "erase"},
        ],
    }


@dataclass
class Lighting:
    """Key light direction (where light comes *from*, unit-ish vector) + rim light."""

    key: Vec = (-0.6, -0.8)  # top-left key light
    rim: Vec = (0.9, -0.3)  # back/right rim light
    shade_depth: float = 0.22  # fraction of part size covered by the shadow crescent
    rim_width: float = 0.05
    rim_opacity: float = 0.6
    enable_rim: bool = True


def cel_part(
    contour: Sequence[Sequence[float]],
    *,
    base: str,
    shade: Optional[str] = None,
    rim: Optional[str] = None,
    line: Optional[str] = "@line",
    line_size: float = 4.0,
    lighting: Lighting = Lighting(),
    size_hint: Optional[float] = None,
    smooth: bool = True,
    boil: float = 0.0,
    drawing: int = 0,
    seed: int = 0,
    extra: Sequence[dict] = (),
    brush: str = "gpen",
    part_id: Optional[str] = None,
    line_points: Optional[Sequence[Sequence[float]]] = None,
) -> dict:
    """Build an isolated cel-shaded part (fill + shadow + rim + ink)."""
    xs = [p[0] for p in contour]
    ys = [p[1] for p in contour]
    size = size_hint or max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    kx, ky = lighting.key
    kl = math.hypot(kx, ky) or 1
    els: list[dict] = [{"type": "polygon", "points": [list(p) for p in contour], "smooth": smooth, "fill": base}]
    if shade:
        d = size * lighting.shade_depth
        els.append(crescent(contour, (kx / kl * d, ky / kl * d), shade))
    if rim and lighting.enable_rim:
        rx, ry = lighting.rim
        rl = math.hypot(rx, ry) or 1
        d = size * lighting.rim_width
        els.append(crescent(contour, (-rx / rl * d, -ry / rl * d), rim, opacity=lighting.rim_opacity))
    els.extend(extra)
    if line:
        pts = line_points if line_points is not None else contour
        pts = line_boil(pts, amplitude=boil, drawing=drawing, seed=seed) if boil else [list(p) for p in pts]
        els.append({
            "type": "stroke", "brush": brush, "points": pts, "closed": line_points is None,
            "size": line_size, "color": line, "seed": seed, "smooth_input": smooth,
            "taper_start": 0.0 if line_points is None else 0.2, "taper_end": 0.0 if line_points is None else 0.3,
        })
    g = {"type": "group", "isolate": True, "elements": els}
    if part_id:
        g["id"] = part_id
    return g


# ---------------------------------------------------------------------------
# Face: mouths, eyes, blinks
# ---------------------------------------------------------------------------

MOUTH_SHAPES = ("X", "A", "B", "C", "D", "E", "F", "G", "H")


def mouth_elements(shape: str, cx: float, cy: float, width: float, *, line: str = "@line", line_size: float = 2.6,
                   dark: str = "@mouth_dark", teeth: str = "@teeth", tongue: str = "@tongue", rotation: float = 0.0) -> dict:
    """Preston Blair mouth as an isolated group. ``width`` = half mouth width in px."""
    if shape not in MOUTH_SHAPES:
        raise ValueError(f"unknown mouth shape {shape!r}; use one of {MOUTH_SHAPES}")
    spec = mouths()[shape]
    m = Affine.translate(cx, cy) @ Affine.rotate(rotation) @ Affine.scale(width)

    def tp(pts):
        return transform_points(pts, m)

    els: list[dict] = []
    if spec.get("inner"):
        inner = tp(spec["inner"])
        els.append({"type": "polygon", "points": inner, "smooth": True, "fill": dark})
        if spec.get("tongue"):
            els.append({"type": "polygon", "points": tp(spec["tongue"]), "smooth": True, "fill": tongue, "blend": "source-atop"})
        if spec.get("teeth"):
            els.append({"type": "polygon", "points": tp(spec["teeth"]), "smooth": False, "fill": teeth, "blend": "source-atop"})
        els.append({"type": "stroke", "points": inner, "closed": True, "size": line_size, "color": line, "taper_start": 0, "taper_end": 0})
    if spec.get("line"):
        els.append({"type": "stroke", "points": tp(spec["line"]), "size": line_size * 1.1, "color": line, "taper_start": 0.3, "taper_end": 0.3})
    if spec.get("lower_lip"):
        els.append({"type": "stroke", "points": tp(spec["lower_lip"]), "size": line_size * 0.8, "color": line, "opacity": 0.7, "taper_start": 0.4, "taper_end": 0.4})
    return {"type": "group", "isolate": True, "id": f"mouth_{shape}", "elements": els}


def eye_elements(cx: float, cy: float, size: float, *, openness: float = 1.0, look: Vec = (0.0, 0.0), line: str = "@line",
                 white: str = "@eye_white", iris: str = "@eye", iris_dark: str = "@eye_dark", shine: str = "@eye_shine",
                 lash_size: float = 4.0, happy: bool = False) -> dict:
    """Anime eye: white, gradient iris with pupil + shines (alpha-locked), heavy upper lash."""
    rx, ry_full = size * 0.72, size
    openness = me.clamp(openness)
    if openness < 0.18 or happy:
        # closed eye: gentle arc (down-curve for blink, up-curve for happy ^^)
        sign = -1 if happy else 1
        pts = [[cx - rx, cy], [cx, cy + sign * ry_full * 0.25], [cx + rx, cy]]
        return {"type": "group", "elements": [{"type": "stroke", "points": pts, "size": lash_size, "color": line, "taper_start": 0.3, "taper_end": 0.3}]}
    ry = ry_full * openness
    ecy = cy + ry_full - ry  # bottom lid stays put; top lid comes down
    ix = cx + look[0] * rx * 0.35
    iy = ecy + look[1] * ry * 0.25 + ry * 0.08
    els = [
        {"type": "ellipse", "cx": cx, "cy": ecy, "rx": rx, "ry": ry, "fill": white},
        {"type": "ellipse", "cx": ix, "cy": iy, "rx": rx * 0.68, "ry": ry_full * 0.78, "blend": "source-atop",
         "fill": {"type": "linear", "x0": ix, "y0": iy - ry_full * 0.8, "x1": ix, "y1": iy + ry_full * 0.8, "stops": [[0, iris_dark], [0.55, iris], [1, iris]]}},
        {"type": "ellipse", "cx": ix, "cy": iy, "rx": rx * 0.3, "ry": ry_full * 0.36, "fill": iris_dark, "blend": "source-atop"},
        {"type": "ellipse", "cx": ix - rx * 0.25, "cy": iy - ry_full * 0.32, "rx": rx * 0.2, "ry": ry_full * 0.22, "fill": shine, "blend": "source-atop"},
        {"type": "ellipse", "cx": ix + rx * 0.22, "cy": iy + ry_full * 0.3, "rx": rx * 0.09, "ry": ry_full * 0.09, "fill": shine, "blend": "source-atop"},
        # upper lash: thick tapered stroke following the top of the eye
        {"type": "stroke", "points": [[cx - rx * 1.15, ecy + ry * 0.15], [cx - rx * 0.6, ecy - ry * 0.85], [cx + rx * 0.2, ecy - ry * 1.02], [cx + rx * 1.05, ecy - ry * 0.55]],
         "size": lash_size, "color": line, "taper_start": 0.25, "taper_end": 0.15},
        {"type": "stroke", "points": [[cx - rx * 0.45, ecy + ry * 0.98], [cx + rx * 0.4, ecy + ry * 0.95]], "size": lash_size * 0.45, "color": line, "opacity": 0.7},
    ]
    return {"type": "group", "isolate": True, "elements": els}


def blink_track(n_frames: int, fps: float = 24, *, seed: int = 1, min_gap: float = 2.2, max_gap: float = 4.5, first: float = 0.8) -> list[float]:
    """Eye openness per frame with natural, irregular blinks (4-frame blink)."""
    import random

    rnd = random.Random(seed)
    out = [1.0] * n_frames
    shape = [0.55, 0.0, 0.25, 0.75]
    t = int(first * fps)
    while t < n_frames:
        for k, v in enumerate(shape):
            if t + k < n_frames:
                out[t + k] = v
        t += int(rnd.uniform(min_gap, max_gap) * fps)
    return out


# ---------------------------------------------------------------------------
# Biped character "Pip"
# ---------------------------------------------------------------------------

@dataclass
class BipedPose:
    """Pose in *character-local* units (hip = origin, +x = facing direction, y down).

    Feet/hands are IK targets relative to the hip. Defaults = relaxed stand.
    """

    x: float = 0.0  # hip world position
    y: float = 0.0
    scale: float = 1.0
    facing: int = 1  # 1 = right, -1 = left
    lean: float = 0.0  # upper body rotation (deg, + leans forward)
    head_tilt: float = 0.0
    head_offset: Vec = (0.0, 0.0)
    foot_front: Vec = (16.0, 86.0)
    foot_back: Vec = (-14.0, 86.0)
    hand_front: Vec = (30.0, -10.0)
    hand_back: Vec = (-30.0, -10.0)
    knee_bend: float = 1.0  # +1 knees forward
    elbow_bend: float = -1.0
    squash: Optional[list[float]] = None  # world-space affine (e.g. SquashStretch.matrix(pivot))
    mouth: str = "X"
    eye_open: float = 1.0
    look: Vec = (0.4, 0.0)
    brow: float = 0.0  # -1 determined/angry ... +1 surprised
    happy_eyes: bool = False
    hair_swing: float = 0.0  # degrees of follow-through on hair spikes
    scarf: Optional[list[Vec]] = None  # world-space chain points (anchor first)
    drawing: int = 0  # for line boil
    boil: float = 0.0
    blush: float = 0.6


@dataclass
class Biped:
    """A stylised 2.6-heads-tall hero built entirely from cel parts."""

    line_size: float = 4.2
    detail_size: float = 2.6
    lighting: Lighting = field(default_factory=Lighting)
    skin: str = "@skin"
    skin_shade: str = "@skin_shade"
    hair: str = "@hair"
    hair_shade: str = "@hair_shade"
    top: str = "@outfit"
    top_shade: str = "@outfit_shade"
    pants: str = "@outfit_2"
    pants_shade: str = "@outfit_2_shade"
    shoe: str = "@accent"
    shoe_shade: str = "@accent_shade"
    scarf_color: str = "@accent"
    scarf_shade: str = "@accent_shade"
    rim: Optional[str] = "@rim"
    line: str = "@line"
    seed: int = 11

    # proportions (local px at scale 1)
    torso_len: float = 64.0
    thigh: float = 44.0
    shin: float = 44.0
    upper_arm: float = 32.0
    forearm: float = 30.0
    head_r: float = 50.0

    # -- transforms ----------------------------------------------------------
    def local_matrix(self, pose: BipedPose) -> Affine:
        m = Affine.translate(pose.x, pose.y) @ Affine.scale(pose.scale * pose.facing, pose.scale)
        if pose.squash:
            m = Affine.from_list(pose.squash) @ m
        return m

    def upper_matrix(self, pose: BipedPose) -> Affine:
        return Affine.rotate(pose.lean, (0.0, 0.0))

    def anchors(self, pose: BipedPose) -> dict[str, Vec]:
        """World positions of useful rig points (neck, head, hands, feet, hip)."""
        lm = self.local_matrix(pose)
        um = lm @ self.upper_matrix(pose)
        neck = (0.0, -self.torso_len)
        head = (pose.head_offset[0] + 6, -self.torso_len - self.head_r * 0.92 + pose.head_offset[1])
        return {
            "hip": lm.apply((0, 0)),
            "neck": um.apply(neck),
            "head": um.apply(head),
            "hand_front": um.apply(pose.hand_front),
            "hand_back": um.apply(pose.hand_back),
            "foot_front": lm.apply(pose.foot_front),
            "foot_back": lm.apply(pose.foot_back),
        }

    # -- parts -----------------------------------------------------------------
    def _part(self, contour, base, shade, pose, seed_off, **kw):
        return cel_part(contour, base=base, shade=shade, rim=self.rim, line=self.line, line_size=kw.pop("line_size", self.line_size),
                        lighting=self.lighting, boil=pose.boil, drawing=pose.drawing, seed=self.seed + seed_off, **kw)

    def _leg(self, pose: BipedPose, hip: Vec, foot: Vec, seed_off: int) -> list[dict]:
        knee, end = two_bone_ik(hip, foot, self.thigh, self.shin, bend=-pose.knee_bend)
        path = bendy_limb(hip, knee, end, 8)
        leg = self._part(capsule_outline(path, 13, 10), self.pants, self.pants_shade, pose, seed_off, size_hint=40)
        sx, sy = end
        shoe = [[sx - 12, sy - 6], [sx + 4, sy - 12], [sx + 22, sy - 6], [sx + 26, sy + 4], [sx + 10, sy + 10], [sx - 12, sy + 9], [sx - 16, sy + 1]]
        shoe_el = self._part(shoe, self.shoe, self.shoe_shade, pose, seed_off + 1, size_hint=30)
        return [leg, shoe_el]

    def _arm(self, pose: BipedPose, shoulder: Vec, hand: Vec, seed_off: int) -> list[dict]:
        elbow, end = two_bone_ik(shoulder, hand, self.upper_arm, self.forearm, bend=pose.elbow_bend)
        path = bendy_limb(shoulder, elbow, end, 8)
        sleeve = self._part(capsule_outline(path, 10, 7.5), self.top, self.top_shade, pose, seed_off, size_hint=30)
        hx, hy = end
        hand_el = self._part(ellipse_points(hx, hy, 10.5, 10, 14), self.skin, self.skin_shade, pose, seed_off + 1, size_hint=22)
        return [sleeve, hand_el]

    def _torso(self, pose: BipedPose) -> dict:
        t = self.torso_len
        contour = [[-24, 4], [-27, -t * 0.45], [-20, -t * 0.9], [0, -t - 4], [22, -t * 0.9], [29, -t * 0.45], [24, 4], [0, 10]]
        belt = {"type": "stroke", "points": [[-25, -4], [0, 1], [25, -4]], "size": self.detail_size, "color": self.line, "opacity": 0.8}
        zip_line = {"type": "stroke", "points": [[6, -t + 2], [8, -t * 0.5], [6, -6]], "size": self.detail_size * 0.8, "color": self.line, "opacity": 0.6}
        return self._part(contour, self.top, self.top_shade, pose, 20, size_hint=60, extra=[belt, zip_line])

    def _head(self, pose: BipedPose) -> list[dict]:
        r = self.head_r
        hx, hy = pose.head_offset[0] + 6, -self.torso_len - r * 0.92 + pose.head_offset[1]
        face = [[hx - r * 0.95, hy - r * 0.1], [hx - r * 0.8, hy - r * 0.75], [hx, hy - r * 1.02], [hx + r * 0.85, hy - r * 0.7],
                [hx + r * 1.02, hy + r * 0.05], [hx + r * 0.78, hy + r * 0.62], [hx + r * 0.3, hy + r * 0.9], [hx - r * 0.35, hy + r * 0.82],
                [hx - r * 0.85, hy + r * 0.45]]
        ear = self._part(ellipse_points(hx - r * 0.62, hy + r * 0.12, r * 0.18, r * 0.24, 12), self.skin, self.skin_shade, pose, 31, size_hint=20,
                         line_size=self.detail_size * 1.2)
        # face shadow under the hair fringe is painted as an extra alpha-locked crescent
        fringe_shadow = {"type": "polygon", "smooth": True, "fill": self.skin_shade, "blend": "source-atop", "opacity": 0.9,
                         "points": [[hx - r, hy - r * 0.35], [hx - r * 0.2, hy - r * 0.18], [hx + r * 0.5, hy - r * 0.3], [hx + r * 1.1, hy - r * 0.2],
                                    [hx + r * 1.1, hy - r * 1.2], [hx - r, hy - r * 1.2]]}
        blush = {"type": "ellipse", "cx": hx + r * 0.55, "cy": hy + r * 0.38, "rx": r * 0.2, "ry": r * 0.1, "fill": "@blush",
                 "opacity": 0.55 * pose.blush, "blur": 3, "blend": "source-atop"}
        head = self._part(face, self.skin, self.skin_shade, pose, 30, size_hint=r * 2, extra=[fringe_shadow, blush])

        # eyes (3/4 view: near eye bigger), brows, mouth
        e1 = (hx + r * 0.18, hy + r * 0.05)
        e2 = (hx + r * 0.72, hy + r * 0.03)
        eyes = [
            eye_elements(*e1, r * 0.25, openness=pose.eye_open, look=pose.look, line=self.line, lash_size=self.line_size * 1.05, happy=pose.happy_eyes),
            eye_elements(*e2, r * 0.21, openness=pose.eye_open, look=pose.look, line=self.line, lash_size=self.line_size * 0.95, happy=pose.happy_eyes),
        ]
        b = pose.brow
        brows = [
            {"type": "stroke", "points": [[e1[0] - r * 0.2, e1[1] - r * 0.36 + b * -4 + 4], [e1[0] + r * 0.18, e1[1] - r * 0.42 - b * 6]], "size": self.line_size * 0.9, "color": self.line, "taper_start": 0.1, "taper_end": 0.5},
            {"type": "stroke", "points": [[e2[0] - r * 0.14, e2[1] - r * 0.4 - b * 6], [e2[0] + r * 0.17, e2[1] - r * 0.34 + b * -4 + 4]], "size": self.line_size * 0.8, "color": self.line, "taper_start": 0.5, "taper_end": 0.1},
        ]
        mouth = mouth_elements(pose.mouth, hx + r * 0.5, hy + r * 0.5, r * 0.2, line=self.line, line_size=self.detail_size)

        # hair: spiky mass with follow-through swing on the tips
        cx, cy = hx, hy - r * 0.15
        spikes = [(-170, 1.05), (-150, 1.32), (-128, 1.12), (-110, 1.42), (-88, 1.18), (-66, 1.45), (-44, 1.15), (-22, 1.3), (-2, 1.02), (15, 0.92)]
        hair_pts = []
        for i, (ang, rr) in enumerate(spikes):
            swing = pose.hair_swing * (rr - 0.9) * 1.4
            a_tip = math.radians(ang - swing)
            hair_pts.append([cx + math.cos(a_tip) * r * rr, cy + math.sin(a_tip) * r * rr])
            if i < len(spikes) - 1:
                a_mid = math.radians((ang + spikes[i + 1][0]) / 2 - swing * 0.3)
                hair_pts.append([cx + math.cos(a_mid) * r * 0.98, cy + math.sin(a_mid) * r * 0.98])
        # fringe along forehead
        hair_pts += [[hx + r * 0.9, hy - r * 0.25], [hx + r * 0.55, hy - r * 0.42], [hx + r * 0.3, hy - r * 0.15], [hx + r * 0.05, hy - r * 0.38],
                     [hx - r * 0.25, hy - r * 0.12], [hx - r * 0.45, hy - r * 0.4], [hx - r * 0.85, hy + r * 0.05]]
        hair_shine = {"type": "polygon", "smooth": True, "fill": "@hair_light", "blend": "source-atop", "opacity": 0.85,
                      "points": [[hx - r * 0.5, hy - r * 0.75], [hx - r * 0.1, hy - r * 0.88], [hx + r * 0.3, hy - r * 0.8], [hx - r * 0.05, hy - r * 0.7]]}
        hair = self._part(hair_pts, self.hair, self.hair_shade, pose, 40, size_hint=r * 2.2, smooth=False, extra=[hair_shine])
        return [ear, head, *eyes, *brows, mouth, hair]

    def _scarf(self, pose: BipedPose) -> list[dict]:
        if not pose.scarf or len(pose.scarf) < 2:
            return []
        path = me.catmull_rom_path(pose.scarf, samples_per_segment=3)
        contour = capsule_outline(path, 9 * pose.scale, 6 * pose.scale, cap_points=3)
        return [cel_part(contour, base=self.scarf_color, shade=self.scarf_shade, rim=self.rim, line=self.line, line_size=self.line_size * pose.scale,
                         lighting=self.lighting, boil=pose.boil, drawing=pose.drawing, seed=self.seed + 60, size_hint=24)]

    def build(self, pose: BipedPose) -> list[dict]:
        """Return element dicts (world space) for one drawing of the character."""
        lean = self.upper_matrix(pose)
        shoulder_f = lean.apply((16, -self.torso_len + 12))
        shoulder_b = lean.apply((-14, -self.torso_len + 12))
        hand_f = lean.apply(pose.hand_front)
        hand_b = lean.apply(pose.hand_back)

        back_leg = self._leg(pose, (-10, -2), pose.foot_back, 1)
        front_leg = self._leg(pose, (12, -2), pose.foot_front, 3)
        back_arm = self._arm(pose, shoulder_b, hand_b, 5)
        front_arm = self._arm(pose, shoulder_f, hand_f, 7)
        torso = self._torso(pose)
        head_parts = self._head(pose)

        upper_t = {"type": "group", "transform": lean.as_list(), "elements": [torso]}
        head_m = lean @ Affine.rotate(pose.head_tilt, (6, -self.torso_len - 4))
        head_g = {"type": "group", "transform": head_m.as_list(), "elements": head_parts}
        collar = {"type": "group", "transform": lean.as_list(), "elements": [
            self._part(capsule_outline([(-20, -self.torso_len + 2), (0, -self.torso_len - 2), (24, -self.torso_len + 2)], 7, 7, 3),
                       self.scarf_color, self.scarf_shade, pose, 61, size_hint=20)]}

        local = {
            "type": "group",
            "id": "pip",
            "transform": self.local_matrix(pose).as_list(),
            "elements": [*back_arm, *back_leg, upper_t, *front_leg, head_g, collar, *front_arm],
        }
        return [*self._scarf(pose), local]

    def ground_shadow(self, x: float, ground_y: float, height_above: float = 0.0, *, color: str = "@ground_shade", width: float = 70.0) -> dict:
        """Contact shadow that shrinks/fades with height (sells weight on jumps)."""
        k = max(0.25, 1 - height_above / 260)
        return {"type": "ellipse", "cx": x, "cy": ground_y + 2, "rx": width * k, "ry": 11 * k, "fill": color, "opacity": 0.55 * k, "blur": 2}
