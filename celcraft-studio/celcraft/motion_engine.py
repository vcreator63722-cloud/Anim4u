"""Motion math built around the 12 Principles of Animation.

Everything here is pure Python (no rendering), deterministic, and works on
plain floats / ``(x, y)`` tuples so agents can compute poses, then draw them.

Principle            -> helpers
-------------------------------------------------------------------------
Squash & stretch     -> :func:`squash_stretch`, :func:`impact_squash`
Anticipation         -> :func:`anticipate`, ease ``"anticipate"``
Staging              -> (camera helpers in :func:`camera_follow`)
Straight-ahead/P2P   -> :class:`Key` + :func:`interpolate`
Follow-through /     -> :class:`Spring`, :func:`spring_follow`,
overlapping action      :func:`follow_through_chain`
Slow in / slow out   -> :data:`EASINGS`, :func:`cubic_bezier`, :func:`spacing_chart`
Arcs                 -> :func:`arc_through`, :func:`bezier_point`, :func:`catmull_rom_path`
Secondary action     -> :func:`secondary_wave`, :func:`value_noise`
Timing               -> :func:`exposure_holds`, :func:`frames_for`
Exaggeration         -> :func:`exaggerate`
Solid drawing/appeal -> (see rig_engine and SKILL.md drawing rules)
Physics              -> :func:`ballistic_jump`, :func:`bounce_heights`
Smears               -> :func:`smear_trail`
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, Union

Vec = tuple[float, float]
Value = Union[float, Sequence[float]]

# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_value(a: Value, b: Value, t: float) -> Value:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return lerp(float(a), float(b), t)
    return tuple(lerp(float(x), float(y), t) for x, y in zip(a, b))


def dist(a: Vec, b: Vec) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def angle_of(v: Vec) -> float:
    """Angle of a vector in degrees (screen space, y down => clockwise positive)."""
    return math.degrees(math.atan2(v[1], v[0]))


# ---------------------------------------------------------------------------
# Easing (slow in / slow out)
# ---------------------------------------------------------------------------

def _back_in(t: float, s: float = 1.70158) -> float:
    return t * t * ((s + 1) * t - s)


def _back_out(t: float, s: float = 1.70158) -> float:
    t -= 1
    return t * t * ((s + 1) * t + s) + 1


def _bounce_out(t: float) -> float:
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def _elastic_out(t: float) -> float:
    if t in (0.0, 1.0):
        return t
    c4 = (2 * math.pi) / 3
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * c4) + 1


def _in_out(f_in: Callable[[float], float]) -> Callable[[float], float]:
    return lambda t: f_in(2 * t) / 2 if t < 0.5 else 1 - f_in(2 * (1 - t)) / 2


EASINGS: dict[str, Callable[[float], float]] = {
    "linear": lambda t: t,
    "constant": lambda t: 0.0,  # hold the previous key (stepped / pose-to-pose blocking)
    "ease_in": lambda t: t * t,  # slow in at start (accelerate)
    "ease_out": lambda t: 1 - (1 - t) ** 2,  # decelerate into the key
    "ease_in_out": _in_out(lambda t: t * t),
    "ease_in_cubic": lambda t: t ** 3,
    "ease_out_cubic": lambda t: 1 - (1 - t) ** 3,
    "ease_in_out_cubic": _in_out(lambda t: t ** 3),
    "ease_in_quart": lambda t: t ** 4,
    "ease_out_quart": lambda t: 1 - (1 - t) ** 4,
    "ease_in_out_quart": _in_out(lambda t: t ** 4),
    "ease_in_quint": lambda t: t ** 5,
    "ease_out_quint": lambda t: 1 - (1 - t) ** 5,
    "ease_in_out_quint": _in_out(lambda t: t ** 5),
    "ease_in_sine": lambda t: 1 - math.cos(t * math.pi / 2),
    "ease_out_sine": lambda t: math.sin(t * math.pi / 2),
    "ease_in_out_sine": lambda t: -(math.cos(math.pi * t) - 1) / 2,
    "ease_in_expo": lambda t: 0.0 if t == 0 else 2 ** (10 * t - 10),
    "ease_out_expo": lambda t: 1.0 if t == 1 else 1 - 2 ** (-10 * t),
    "ease_in_out_expo": _in_out(lambda t: 0.0 if t == 0 else 2 ** (10 * t - 10)),
    "ease_in_circ": lambda t: 1 - math.sqrt(max(0.0, 1 - t * t)),
    "ease_out_circ": lambda t: math.sqrt(max(0.0, 1 - (t - 1) ** 2)),
    "anticipate": _back_in,  # pulls back before going (anticipation)
    "overshoot": _back_out,  # passes the key and settles (follow-through)
    "anticipate_overshoot": _in_out(lambda t: _back_in(t, 1.70158 * 1.525)),
    "bounce": _bounce_out,
    "elastic": _elastic_out,
    "snap": lambda t: 1 - (1 - t) ** 6,  # sakuga "snap": almost all motion in first frames
}


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """CSS-style cubic-bezier timing function (Newton-Raphson + bisection)."""

    def bx(t):
        return 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3

    def by(t):
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3

    def dbx(t):
        return 3 * (1 - t) ** 2 * x1 + 6 * (1 - t) * t * (x2 - x1) + 3 * t * t * (1 - x2)

    def solve(x: float) -> float:
        x = clamp(x)
        t = x
        for _ in range(8):
            err = bx(t) - x
            if abs(err) < 1e-6:
                return by(t)
            d = dbx(t)
            if abs(d) < 1e-6:
                break
            t = clamp(t - err / d)
        lo, hi = 0.0, 1.0
        t = x
        for _ in range(40):
            if bx(t) < x:
                lo = t
            else:
                hi = t
            t = (lo + hi) / 2
        return by(t)

    return solve


def get_easing(name_or_fn: Union[str, Callable[[float], float], Sequence[float]]) -> Callable[[float], float]:
    if callable(name_or_fn):
        return name_or_fn
    if isinstance(name_or_fn, str):
        if name_or_fn.startswith("cubic_bezier(") or name_or_fn.startswith("cubic-bezier("):
            nums = [float(x) for x in name_or_fn[name_or_fn.index("(") + 1: name_or_fn.rindex(")")].split(",")]
            return cubic_bezier(*nums)
        if name_or_fn not in EASINGS:
            raise KeyError(f"unknown easing {name_or_fn!r}; available: {', '.join(EASINGS)}")
        return EASINGS[name_or_fn]
    return cubic_bezier(*name_or_fn)


def ease(name: Union[str, Callable], t: float) -> float:
    return get_easing(name)(clamp(t))


def spacing_chart(inbetweens: int, easing: str = "ease_in_out") -> list[float]:
    """Fractions (0..1) for key, inbetweens, key — the animator's timing chart.

    ``spacing_chart(3, "ease_in_out")`` -> [0, 0.125, 0.5, 0.875, 1]
    """
    n = inbetweens + 1
    f = get_easing(easing)
    return [round(f(i / n), 6) for i in range(n + 1)]


def favor(t: float, toward: str = "end", amount: float = 2.0) -> float:
    """Bias a breakdown toward one key ("favouring" in a timing chart)."""
    t = clamp(t)
    return t ** (1 / amount) if toward == "end" else t ** amount


# ---------------------------------------------------------------------------
# Keyframes (pose-to-pose)
# ---------------------------------------------------------------------------

@dataclass
class Key:
    frame: float
    value: Value
    ease: Union[str, Callable[[float], float]] = "ease_in_out"  # easing *leaving* this key


def interpolate(keys: Sequence[Key], frame: float) -> Value:
    """Evaluate a key track at ``frame`` (holds before first / after last key)."""
    if not keys:
        raise ValueError("no keys")
    ks = sorted(keys, key=lambda k: k.frame)
    if frame <= ks[0].frame:
        return ks[0].value
    if frame >= ks[-1].frame:
        return ks[-1].value
    for a, b in zip(ks, ks[1:]):
        if a.frame <= frame <= b.frame:
            span = b.frame - a.frame
            t = 0.0 if span == 0 else (frame - a.frame) / span
            return lerp_value(a.value, b.value, get_easing(a.ease)(t))
    return ks[-1].value


def track(keys: Sequence[Key], frames: Iterable[float]) -> list[Value]:
    return [interpolate(keys, f) for f in frames]


# ---------------------------------------------------------------------------
# Arcs
# ---------------------------------------------------------------------------

def bezier_point(points: Sequence[Vec], t: float) -> Vec:
    """De Casteljau evaluation of a bezier of any degree."""
    pts = [tuple(p) for p in points]
    while len(pts) > 1:
        pts = [(lerp(a[0], b[0], t), lerp(a[1], b[1], t)) for a, b in zip(pts, pts[1:])]
    return pts[0]


def arc_through(a: Vec, b: Vec, c: Vec) -> Callable[[float], Vec]:
    """Circular arc through three points; returns f(t) with f(0)=a, f(1)=c.

    Natural motion travels on arcs — use this for head turns, arm swings and
    any breakdown so inbetweens never cut straight across.
    """
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:  # collinear -> straight line
        return lambda t: (lerp(ax, cx, t), lerp(ay, cy, t))
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0 = math.atan2(ay - uy, ax - ux)
    a1 = math.atan2(by - uy, bx - ux)
    a2 = math.atan2(cy - uy, cx - ux)

    def norm(x):
        return x % (2 * math.pi)

    # choose direction so that the arc passes through b
    ccw_span = norm(a2 - a0)
    passes_b_ccw = norm(a1 - a0) <= ccw_span
    span = ccw_span if passes_b_ccw else -(2 * math.pi - ccw_span)
    return lambda t: (ux + r * math.cos(a0 + span * t), uy + r * math.sin(a0 + span * t))


def catmull_rom_path(points: Sequence[Vec], samples_per_segment: int = 12, closed: bool = False) -> list[Vec]:
    """Smooth path through control points (motion path / spline)."""
    n = len(points)
    if n < 2:
        return [tuple(p) for p in points]

    def get(i):
        return points[i % n] if closed else points[max(0, min(n - 1, i))]

    out: list[Vec] = []
    segs = n if closed else n - 1
    for i in range(segs):
        p0, p1, p2, p3 = get(i - 1), get(i), get(i + 1), get(i + 2)
        for s in range(samples_per_segment):
            t = s / samples_per_segment
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                       + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)
                for k in range(2)
            ))
    out.append(tuple(get(0) if closed else points[-1]))
    return out


def path_point_at(path: Sequence[Vec], t: float) -> Vec:
    """Point at arc-length fraction ``t`` along a polyline (constant-speed sampling)."""
    if len(path) == 1:
        return tuple(path[0])
    lengths = [0.0]
    for a, b in zip(path, path[1:]):
        lengths.append(lengths[-1] + dist(a, b))
    target = clamp(t) * lengths[-1]
    for i in range(1, len(path)):
        if lengths[i] >= target:
            seg = lengths[i] - lengths[i - 1]
            u = 0 if seg == 0 else (target - lengths[i - 1]) / seg
            return (lerp(path[i - 1][0], path[i][0], u), lerp(path[i - 1][1], path[i][1], u))
    return tuple(path[-1])


# ---------------------------------------------------------------------------
# Squash & stretch
# ---------------------------------------------------------------------------

@dataclass
class SquashStretch:
    scale_along: float  # along motion direction
    scale_across: float  # perpendicular
    angle: float  # degrees, direction of motion

    def matrix(self, pivot: Vec) -> list[float]:
        """Affine [a, b, c, d, e, f] applying the squash/stretch about ``pivot``."""
        th = math.radians(self.angle)
        c, s = math.cos(th), math.sin(th)
        sx, sy = self.scale_along, self.scale_across
        # R * S * R^-1
        a = c * c * sx + s * s * sy
        b = c * s * (sx - sy)
        d = s * s * sx + c * c * sy
        px, py = pivot
        e = px - (a * px + b * py)
        f = py - (b * px + d * py)
        return [a, b, b, d, e, f]


def squash_stretch(velocity: Vec, *, factor: float = 0.015, max_stretch: float = 1.6, min_squash: float = 0.55) -> SquashStretch:
    """Volume-preserving stretch along the velocity vector.

    stretch = clamp(1 + |v| * factor) and across = 1 / stretch, so the drawn
    area (2D "volume") stays constant while the shape elongates.
    """
    speed = math.hypot(*velocity)
    s = clamp(1 + speed * factor, min_squash, max_stretch)
    return SquashStretch(scale_along=s, scale_across=1 / s, angle=angle_of(velocity) if speed > 1e-6 else 0.0)


def impact_squash(t_since_impact: float, *, amount: float = 0.35, recover_frames: float = 6.0, wobble: float = 0.5) -> tuple[float, float]:
    """(scale_x, scale_y) for a vertical impact: squash then a damped stretch rebound."""
    if t_since_impact < 0:
        return 1.0, 1.0
    k = t_since_impact / max(recover_frames, 1e-6)
    sy = 1 - amount * math.exp(-3 * k) * math.cos(k * math.pi * (1 + wobble))
    return 1 / sy, sy


def exaggerate(value: float, rest: float, amount: float = 1.3) -> float:
    """Push a value further from rest (poses, angles, squash)."""
    return rest + (value - rest) * amount


# ---------------------------------------------------------------------------
# Physics (jumps, bounces)
# ---------------------------------------------------------------------------

def ballistic_jump(start: Vec, end: Vec, apex_height: float, frames: int) -> list[Vec]:
    """Physically-timed parabolic jump: constant horizontal speed, gravity in y.

    ``apex_height`` is measured upward (screen y decreases) from the higher of
    start/end. Returns ``frames + 1`` positions including both ends. Spacing
    automatically clusters at the apex (slow) and spreads near take-off and
    landing (fast) — exactly the timing an animator would chart.
    """
    if frames < 1:
        raise ValueError("frames must be >= 1")
    x0, y0 = start
    x1, y1 = end
    top = min(y0, y1) - apex_height
    # solve y(t) = y0 + v*t + g/2*t^2 over t in [0,1], with min = top
    # sqrt form: choose ratio so the apex time tp satisfies both legs
    h0 = y0 - top
    h1 = y1 - top
    tp = math.sqrt(h0) / (math.sqrt(h0) + math.sqrt(h1)) if (h0 + h1) > 0 else 0.5
    g = 2 * h0 / (tp * tp) if tp > 0 else 0
    v = -g * tp
    out = []
    for i in range(frames + 1):
        t = i / frames
        out.append((lerp(x0, x1, t), y0 + v * t + 0.5 * g * t * t))
    return out


def velocities(positions: Sequence[Vec]) -> list[Vec]:
    """Per-frame velocity (central differences)."""
    n = len(positions)
    out = []
    for i in range(n):
        a = positions[max(0, i - 1)]
        b = positions[min(n - 1, i + 1)]
        span = min(n - 1, i + 1) - max(0, i - 1) or 1
        out.append(((b[0] - a[0]) / span, (b[1] - a[1]) / span))
    return out


def bounce_heights(height: float, restitution: float = 0.55, bounces: int = 4) -> list[float]:
    """Successive apex heights of a bouncing object (energy loss per hit)."""
    return [height * restitution ** (2 * i) for i in range(bounces + 1)]


# ---------------------------------------------------------------------------
# Follow-through & overlapping action
# ---------------------------------------------------------------------------

@dataclass
class Spring:
    """Damped spring for follow-through (hair, antennae, cheeks, props)."""

    stiffness: float = 0.18
    damping: float = 0.72
    value: float = 0.0
    velocity: float = 0.0

    def step(self, target: float) -> float:
        self.velocity = self.velocity * self.damping + (target - self.value) * self.stiffness
        self.value += self.velocity
        return self.value


def spring_follow(targets: Sequence[float], *, stiffness: float = 0.18, damping: float = 0.72, delay: int = 0) -> list[float]:
    """Drag a value behind a target track (overlapping action with optional frame delay)."""
    if not targets:
        return []
    sp = Spring(stiffness, damping, value=targets[0])
    out = []
    for i in range(len(targets)):
        tgt = targets[max(0, i - delay)]
        out.append(sp.step(tgt))
    return out


def follow_through_chain(
    anchors: Sequence[Vec],
    *,
    segments: int = 6,
    segment_length: float = 18.0,
    gravity: Vec = (0.0, 0.9),
    stiffness: float = 0.55,
    damping: float = 0.92,
    wind: Callable[[int, int], Vec] | None = None,
    iterations: int = 6,
    rest_direction: Vec = (-1.0, 0.3),
) -> list[list[Vec]]:
    """Verlet rope attached to a moving anchor (scarf, ponytail, tail, cape).

    Returns, for every frame, the chain points (anchor first). Drives natural
    drag, overlap and settle without any hand keying.
    """
    rd = math.hypot(*rest_direction) or 1
    rdx, rdy = rest_direction[0] / rd, rest_direction[1] / rd
    ax, ay = anchors[0]
    pts = [[ax + rdx * segment_length * i, ay + rdy * segment_length * i] for i in range(segments + 1)]
    prev = [p[:] for p in pts]
    frames: list[list[Vec]] = []
    for f, anchor in enumerate(anchors):
        for i in range(1, len(pts)):
            vx = (pts[i][0] - prev[i][0]) * damping
            vy = (pts[i][1] - prev[i][1]) * damping
            prev[i] = pts[i][:]
            wx, wy = wind(f, i) if wind else (0.0, 0.0)
            # soft pull toward rest shape (keeps the chain from collapsing straight down)
            rest_x = pts[i - 1][0] + rdx * segment_length
            rest_y = pts[i - 1][1] + rdy * segment_length
            pts[i][0] += vx + gravity[0] + wx + (rest_x - pts[i][0]) * (1 - stiffness) * 0.05
            pts[i][1] += vy + gravity[1] + wy + (rest_y - pts[i][1]) * (1 - stiffness) * 0.05
        pts[0] = [anchor[0], anchor[1]]
        prev[0] = pts[0][:]
        for _ in range(iterations):
            for i in range(1, len(pts)):
                dx = pts[i][0] - pts[i - 1][0]
                dy = pts[i][1] - pts[i - 1][1]
                d = math.hypot(dx, dy) or 1e-6
                diff = (d - segment_length) / d
                if i == 1:
                    pts[i][0] -= dx * diff
                    pts[i][1] -= dy * diff
                else:
                    pts[i][0] -= dx * diff * 0.5
                    pts[i][1] -= dy * diff * 0.5
                    pts[i - 1][0] += dx * diff * 0.5
                    pts[i - 1][1] += dy * diff * 0.5
            pts[0] = [anchor[0], anchor[1]]
        frames.append([(p[0], p[1]) for p in pts])
    return frames


# ---------------------------------------------------------------------------
# Secondary action / noise
# ---------------------------------------------------------------------------

def _hash(n: int, seed: int) -> float:
    x = (n * 374761393 + seed * 668265263) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((x ^ (x >> 16)) & 0xFFFFFFFF) / 0xFFFFFFFF * 2 - 1


def value_noise(t: float, seed: int = 0) -> float:
    """Smooth 1D value noise in [-1, 1]."""
    i = math.floor(t)
    f = t - i
    u = f * f * (3 - 2 * f)
    return _hash(i, seed) * (1 - u) + _hash(i + 1, seed) * u


def secondary_wave(frame: float, *, period: float = 24.0, amplitude: float = 1.0, phase: float = 0.0) -> float:
    """Sine-based idle motion (breathing, bobbing, tail sway)."""
    return math.sin((frame / period) * 2 * math.pi + phase) * amplitude


# ---------------------------------------------------------------------------
# Smears
# ---------------------------------------------------------------------------

def smear_trail(positions: Sequence[Vec], index: int, *, length: int = 3, radius: float = 20.0, taper: float = 0.35, samples: int = 10) -> dict:
    """Build a SmearElement dict trailing behind ``positions[index]``.

    The trail follows the actual motion arc of the previous ``length`` frames
    (resampled with Catmull-Rom) and tapers from ``radius`` to ``radius*taper``.
    """
    start = max(0, index - length)
    ctrl = list(positions[start: index + 1])
    if len(ctrl) < 2:
        return {"type": "smear", "points": [list(positions[index])], "radius": radius}
    path = catmull_rom_path(ctrl, samples_per_segment=max(2, samples // max(1, len(ctrl) - 1)))
    n = len(path)
    radii = [radius * lerp(taper, 1.0, i / (n - 1)) for i in range(n)]
    return {"type": "smear", "points": [list(p) for p in path], "radii": radii}


# ---------------------------------------------------------------------------
# Timing / exposure
# ---------------------------------------------------------------------------

def frames_for(seconds: float, fps: float = 24) -> int:
    return max(1, round(seconds * fps))


def exposure_holds(n_drawings: int, on: int = 2) -> list[int]:
    """Hold list for a run of drawings shot on ones/twos/threes."""
    return [on] * n_drawings


def spacing_report(positions: Sequence[Vec]) -> dict:
    """Spacing diagnostics for review: per-frame distances and pops.

    A *pop* is a frame whose spacing jumps > 2.5x the median spacing of its
    neighbours. Even spacing (stddev/mean < 0.08) on a long move signals
    missing slow-in/slow-out.
    """
    d = [dist(a, b) for a, b in zip(positions, positions[1:])]
    if not d:
        return {"spacing": [], "pops": [], "evenness": 0.0}
    mean = sum(d) / len(d)
    var = sum((x - mean) ** 2 for x in d) / len(d)
    pops = []
    for i, x in enumerate(d):
        neigh = sorted(d[max(0, i - 2): i] + d[i + 1: i + 3])
        if neigh:
            med = neigh[len(neigh) // 2]
            if med > 0.5 and x > med * 2.5:
                pops.append(i + 1)
    return {
        "spacing": [round(x, 2) for x in d],
        "mean": round(mean, 3),
        "evenness": round(math.sqrt(var) / mean, 3) if mean else 0.0,
        "pops": pops,
        "linear_warning": bool(mean > 2 and len(d) > 6 and math.sqrt(var) / mean < 0.08),
    }


def camera_follow(subject: Sequence[Vec], *, lag: float = 0.12, lead: float = 6.0, dead_zone: float = 20.0) -> list[Vec]:
    """Camera centre trailing a subject with critically-damped lag and look-ahead."""
    if not subject:
        return []
    cx, cy = subject[0]
    vs = velocities(subject)
    out = []
    for (sx, sy), (vx, vy) in zip(subject, vs):
        tx, ty = sx + vx * lead, sy + vy * lead
        dx, dy = tx - cx, ty - cy
        if math.hypot(dx, dy) > dead_zone:
            cx += dx * lag
            cy += dy * lag
        out.append((cx, cy))
    return out
