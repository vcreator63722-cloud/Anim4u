#!/usr/bin/env python3
"""CelCraft Studio showcase: a 5-second sakuga jump (120 frames @ 24 fps).

Beat sheet
----------
  0-23   Idle on twos: breathing, blink, line boil, says "Yosh!" (lip-sync)
 24-35   Anticipation on ones: crouch, arms wind back, held key 32-35
 36-39   Take-off: stretch along velocity, smear frames, action lines, whoosh
 40-63   Physically-timed jump arc, tuck at apex, scarf follow-through,
         multi-plane parallax camera follow
 64-65   Impact frames (two-tone flash), radial focus lines, camera shake, thump
 66-80   Squash recovery with overshoot, dust clouds
 81-119  Hero pose on twos, "Yatta!" lip-sync, happy eyes, sparkles, title card

Run:  python examples/demo_sakuga_jump.py --out celcraft_demo [--scale 0.5]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from celcraft import audio_lipsync as al  # noqa: E402
from celcraft import motion_engine as me  # noqa: E402
from celcraft.exporter import export_scene  # noqa: E402
from celcraft.onion_skin import contact_sheet, motion_path_overlay, onion_from_sequence  # noqa: E402
from celcraft.project import Project, save_json, xsheet_text  # noqa: E402
from celcraft.renderer_bridge import RendererPool, render_scene  # noqa: E402
from celcraft.rig_engine import Biped, BipedPose, Lighting, capsule_outline, cel_part, ellipse_points  # noqa: E402
from celcraft.schema import Scene  # noqa: E402
from celcraft.validator import validate_scene_json, validate_scene_render  # noqa: E402

FPS = 24
N = 120
W, H = 1920, 1080
GROUND = 900.0
S = 1.9  # character scale
HIP_STAND = GROUND - 96 * S
START_X, LAND_X = 700.0, 1650.0
TAKEOFF, LAND = 36, 64
CROUCH_D, LAND_D = 48.0, 58.0

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

YOSH_START, YATTA_START = 0.30, 3.70


def build_audio(audio_dir: Path) -> list[dict]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    al.save_wav(audio_dir / "music.wav", al.synth_music(N / FPS, bpm=128, key=196.0))
    al.save_wav(audio_dir / "whoosh.wav", al.synth_whoosh(0.55, seed=4))
    al.save_wav(audio_dir / "impact.wav", al.synth_impact(0.8))
    al.save_wav(audio_dir / "pop.wav", al.synth_pop(0.2, pitch=1200))
    al.save_wav(audio_dir / "voice_yosh.wav", al.synth_voice([(0.0, 0.16, "o"), (0.2, 0.22, "o")], 0.5, pitch=250))
    al.save_wav(audio_dir / "voice_yatta.wav", al.synth_voice([(0.0, 0.14, "a"), (0.2, 0.36, "a")], 0.65, pitch=270))
    return [
        {"path": "audio/music.wav", "kind": "music", "gain_db": -11, "fade_out": 0.8},
        {"path": "audio/voice_yosh.wav", "kind": "dialogue", "start": YOSH_START, "gain_db": -2},
        {"path": "audio/whoosh.wav", "kind": "sfx", "start": TAKEOFF / FPS - 0.05, "gain_db": -3, "pan": 0.2},
        {"path": "audio/impact.wav", "kind": "sfx", "start": LAND / FPS, "gain_db": 0},
        {"path": "audio/voice_yatta.wav", "kind": "dialogue", "start": YATTA_START, "gain_db": -1},
        {"path": "audio/pop.wav", "kind": "sfx", "start": 92 / FPS, "gain_db": -8, "pan": -0.3},
    ]


def build_mouths(audio_dir: Path) -> list[str]:
    shapes = ["X"] * N
    for text, wav, start in (("yosh", "voice_yosh.wav", YOSH_START), ("yatta", "voice_yatta.wav", YATTA_START)):
        seg = al.text_lipsync(text, audio_dir / wav, FPS, n_frames=N, start=start)
        for i, s in enumerate(seg):
            if s != "X":
                shapes[i] = s
    return shapes


# ---------------------------------------------------------------------------
# Motion: one pose per *timeline* frame (drawings sample this on ones/twos)
# ---------------------------------------------------------------------------

JUMP = me.ballistic_jump((START_X, HIP_STAND + CROUCH_D * S), (LAND_X, HIP_STAND + LAND_D * S), apex_height=400, frames=LAND - TAKEOFF)
JUMP_V = me.velocities(JUMP)


def lerp2(a, b, t):
    return (me.lerp(a[0], b[0], t), me.lerp(a[1], b[1], t))


def pose_at(t: int, mouths: list[str], blinks: list[float]) -> tuple[BipedPose, dict]:
    """Return (pose, info) where info carries world-space extras for FX."""
    info: dict = {"height": 0.0}
    p = BipedPose(scale=S, facing=1, mouth=mouths[t], eye_open=blinks[t])
    if t < 24:  # ---------------- idle
        br = math.sin(t / 24 * 2 * math.pi)
        d = 2 + br * 1.5
        p.x, p.y = START_X, HIP_STAND + d * S
        p.lean, p.head_tilt = 2 + br * 0.6, br * 1.2
        p.foot_front, p.foot_back = (16, 86 - d), (-14, 86 - d)
        p.hand_front, p.hand_back = (27, -8 + br), (-29, -6 + br)
        p.look, p.boil = (0.55, -0.1), 0.9
    elif t < TAKEOFF:  # ----------- anticipation (slow in to the crouch, then hold)
        a = me.ease("ease_in_out_cubic", (t - 24) / 8)
        a += 0.04 * max(0, t - 32)  # creeping settle during the held key
        d = CROUCH_D * a
        p.x, p.y = START_X, HIP_STAND + d * S
        p.lean, p.head_tilt = 2 + 30 * a, -12 * a
        p.foot_front, p.foot_back = (16 + 10 * a, 86 - d), (-14 - 12 * a, 86 - d)
        p.hand_front = lerp2((27, -8), (-48, 16), a)
        p.hand_back = lerp2((-29, -6), (-58, 6), a)
        p.look, p.brow = (0.7, -0.6 * a), -a
        p.mouth = "A" if t >= 28 else p.mouth
        p.squash = me.SquashStretch(1 + 0.06 * a, 1 - 0.06 * a, 0).matrix((p.x, GROUND))  # widen, squash down
    elif t <= LAND:  # -------------- airborne
        i = t - TAKEOFF
        u = i / (LAND - TAKEOFF)
        p.x, p.y = JUMP[i]
        vx, vy = JUMP_V[i]
        info["velocity"] = (vx, vy)
        launch = {"ff": (8, 80), "fb": (-36, 66), "hf": (46, -104), "hb": (26, -114), "lean": -10}
        tuck = {"ff": (30, 38), "fb": (4, 52), "hf": (58, -58), "hb": (-54, -42), "lean": 14}
        reach = {"ff": (24, 82), "fb": (-18, 86), "hf": (54, -86), "hb": (-44, -78), "lean": 4}
        if u < 0.45:
            k, A, B = me.ease("ease_out_cubic", u / 0.45), launch, tuck
        else:
            k, A, B = me.ease("ease_in_out", (u - 0.45) / 0.55), tuck, reach
        p.foot_front, p.foot_back = lerp2(A["ff"], B["ff"], k), lerp2(A["fb"], B["fb"], k)
        p.hand_front, p.hand_back = lerp2(A["hf"], B["hf"], k), lerp2(A["hb"], B["hb"], k)
        p.lean = me.lerp(A["lean"], B["lean"], k)
        p.head_tilt = me.lerp(-8, 6, u)
        p.look, p.brow = (0.8, me.lerp(-0.4, 0.5, u)), me.lerp(-1, -0.6, u)
        p.mouth = "D" if 0.3 < u < 0.7 else "C"
        p.eye_open = 1.0
        ss = me.squash_stretch((vx, vy), factor=0.012, max_stretch=1.35)
        p.squash = ss.matrix((p.x, p.y - 40 * S))
        info["height"] = max(0.0, (HIP_STAND - p.y) / S + LAND_D * (u > 0.9))
        if t == LAND:
            info.pop("velocity")
            sx, sy = me.impact_squash(0, amount=0.3)
            p.squash = me.SquashStretch(sx, sy, 0).matrix((p.x, GROUND))
            p.foot_front, p.foot_back = (34, 86 - LAND_D), (-30, 86 - LAND_D)
            p.lean = 26
    else:  # --------------------- landing recovery + hero pose
        k = t - LAND
        decay = math.exp(-k * 0.2)
        d = LAND_D * decay * math.cos(min(k, 14) * 0.2)  # deep landing crouch, small overshoot, settle
        if t >= 81:
            d = 1.5 * math.sin((t - 81) / 22 * 2 * math.pi)  # hero idle breathing
            p.boil = 0.9
        p.x, p.y = LAND_X, HIP_STAND + d * S
        sx, sy = me.impact_squash(k, amount=0.3, recover_frames=9)
        p.squash = me.SquashStretch(sx, sy, 0).matrix((p.x, GROUND))
        p.lean = 26 * decay - 6 * (1 - decay)
        r = me.ease("overshoot", min(1.0, k / 14))
        p.hand_front = lerp2((50, 10), (92, -96), r)  # victory fist out to the side, clear of the face
        p.hand_back = lerp2((-50, 2), (-25, -4), me.ease("ease_out", min(1.0, k / 10)))
        p.elbow_bend = -1
        p.foot_front, p.foot_back = (34, 86 - d), (-30, 86 - d)
        p.head_tilt = me.lerp(10, -4, me.ease("ease_out", min(1.0, k / 12)))
        p.brow = me.lerp(-1, 0.3, me.ease("ease_out", min(1.0, k / 16)))
        p.look = (0.3, -0.2)
        talking = mouths[t] != "X"
        p.happy_eyes = t >= 86 and not talking and t < 112 or (talking and t > 86)
        if t >= 86 and not talking:
            p.mouth = "C" if t < 112 else "B"  # open grin after the line, then a calmer smile
        p.blush = 1.0
    return p, info


# ---------------------------------------------------------------------------
# Background (multi-plane)
# ---------------------------------------------------------------------------

def tree(x: float, base_y: float, s: float, seed: int, light: Lighting) -> list[dict]:
    trunk = [[x - 7 * s, base_y], [x - 5 * s, base_y - 70 * s], [x + 5 * s, base_y - 70 * s], [x + 8 * s, base_y]]
    blobs = [(x, base_y - 110 * s, 48 * s), (x - 34 * s, base_y - 82 * s, 34 * s), (x + 32 * s, base_y - 86 * s, 36 * s)]
    els = [cel_part(trunk, base="#6a4a3a", shade="#4a3028", line="@line_soft", line_size=2.2, lighting=light, smooth=False, seed=seed)]
    for i, (bx, by, r) in enumerate(blobs):
        els.append(cel_part(ellipse_points(bx, by, r, r * 0.9, 16), base="@mid", shade="@near", rim="@rim", line="@line_soft",
                            line_size=2.4, lighting=light, seed=seed + i + 1))
    return els


def background_layers(light: Lighting) -> list[dict]:
    sky = {
        "id": "sky", "kind": "background", "parallax": 0.0,
        "elements": [
            {"type": "rect", "x": -50, "y": -50, "w": W + 100, "h": H + 100,
             "fill": {"type": "linear", "x0": 0, "y0": 0, "x1": 0, "y1": 900, "stops": [[0, "@sky_top"], [0.55, "@sky_mid"], [1, "@sky_bottom"]]}},
            {"type": "ellipse", "cx": 1480, "cy": 420, "rx": 520, "blend": "screen", "opacity": 0.8,
             "fill": {"type": "radial", "cx": 1480, "cy": 420, "r": 520, "stops": [[0, "rgba(255,240,190,0.95)"], [0.25, "rgba(255,200,140,0.45)"], [1, "rgba(255,160,120,0)"]]}},
            {"type": "ellipse", "cx": 1480, "cy": 420, "rx": 78, "fill": "#fff4d6"},
        ],
    }
    clouds = []
    for i, (cx, cy, s) in enumerate([(260, 220, 1.0), (760, 150, 0.7), (1150, 260, 0.9), (1900, 180, 1.1), (2450, 240, 0.8)]):
        for j, (dx, dy, r) in enumerate([(-70, 10, 55), (0, -18, 75), (80, 8, 58), (150, 20, 40)]):
            clouds.append({"type": "ellipse", "cx": cx + dx * s, "cy": cy + dy * s, "rx": r * s * 1.3, "ry": r * s * 0.8, "fill": "rgba(255,226,214,0.55)", "blur": 3})
        clouds.append({"type": "ellipse", "cx": cx + 20 * s, "cy": cy + 30 * s, "rx": 150 * s, "ry": 16 * s, "fill": "rgba(120,80,160,0.25)", "blur": 4})
    mountains_pts = [[-700, 760], [-420, 520], [-200, 610], [80, 430], [380, 600], [640, 470], [900, 560], [1180, 400], [1500, 590], [1760, 470],
                     [2050, 600], [2350, 450], [2700, 620], [3100, 500], [3400, 760]]
    far = {
        "id": "far", "kind": "background", "parallax": 0.3,
        "elements": [
            {"type": "polygon", "points": mountains_pts + [[3400, 1200], [-700, 1200]], "smooth": False,
             "fill": {"type": "linear", "x0": 0, "y0": 420, "x1": 0, "y1": 820, "stops": [[0, "@far"], [1, "@sky_mid"]]}},
            {"type": "stroke", "points": mountains_pts, "brush": "pencil", "size": 3, "color": "@rim", "opacity": 0.55, "smooth_input": False, "seed": 5},
        ],
    }
    hills_pts = [[-600, 820], [-200, 700], [300, 760], [700, 690], [1100, 770], [1500, 700], [1900, 760], [2300, 690], [2800, 760], [3300, 720]]
    mid_els = [{"type": "polygon", "points": hills_pts + [[3300, 1200], [-600, 1200]], "smooth": True,
                "fill": {"type": "linear", "x0": 0, "y0": 690, "x1": 0, "y1": 950, "stops": [[0, "@mid"], [1, "@near"]]}},
               {"type": "stroke", "points": hills_pts, "size": 3, "color": "@line_soft", "opacity": 0.7, "seed": 8}]
    for i, (tx, ty, ts) in enumerate([(180, 760, 0.8), (980, 760, 0.65), (1340, 735, 0.75), (2100, 735, 0.85), (2600, 735, 0.7)]):
        mid_els += tree(tx, ty, ts, 100 + i * 7, light)
    mid = {"id": "mid", "kind": "background", "parallax": 0.6, "elements": mid_els}

    ground_top = [[-800, GROUND + 14], [-300, GROUND + 4], [300, GROUND - 4], [900, GROUND + 2], [1500, GROUND - 2], [2100, GROUND + 4], [2700, GROUND - 2], [3400, GROUND + 6]]
    g_els = [
        {"type": "polygon", "points": ground_top + [[3400, 1500], [-800, 1500]], "smooth": True,
         "fill": {"type": "linear", "x0": 0, "y0": GROUND, "x1": 0, "y1": 1150, "stops": [[0, "@ground_light"], [0.15, "@ground"], [1, "@ground_shade"]]}},
        {"type": "stroke", "points": ground_top, "size": 4, "color": "@line", "seed": 3, "taper_start": 0, "taper_end": 0},
        {"type": "halftone", "x": -800, "y": GROUND + 40, "w": 4200, "h": 300, "spacing": 12, "angle": 30, "color": "@ground_shade",
         "x0": 0, "y0": 1150, "x1": 0, "y1": GROUND + 40, "max_radius": 5, "opacity": 0.6},
    ]
    import random

    rnd = random.Random(7)
    for i in range(70):
        gx = -600 + i * 55 + rnd.uniform(-20, 20)
        gy = GROUND + rnd.uniform(14, 160)
        h = rnd.uniform(10, 26)
        for k in (-1, 0, 1):
            g_els.append({"type": "stroke", "points": [[gx + k * 5, gy], [gx + k * 9 + 2, gy - h * (1 - abs(k) * 0.3)]], "size": 2.6, "color": "@ground_shade",
                          "taper_start": 0, "taper_end": 0.9, "seed": i * 3 + k})
    for rx, ry, rr in [(420, GROUND + 26, 30), (1180, GROUND + 60, 22), (2240, GROUND + 34, 36)]:
        g_els.append(cel_part(ellipse_points(rx, ry, rr * 1.4, rr * 0.8, 14), base="#9a8fa8", shade="#6a5f80", rim="@rim", line="@line",
                              line_size=3, lighting=light, seed=int(rx)))
    ground = {"id": "ground", "kind": "background", "parallax": 1.0, "elements": g_els}
    return [sky, {"id": "clouds", "kind": "background", "parallax": 0.12, "elements": clouds}, far, mid, ground]


def foreground_layer() -> dict:
    import random

    rnd = random.Random(21)
    els = []
    for i in range(34):
        x = -400 + i * 95 + rnd.uniform(-30, 30)
        h = rnd.uniform(80, 170)
        for k in range(4):
            ang = rnd.uniform(-0.5, 0.5)
            els.append({"type": "stroke", "points": [[x + k * 12, H + 40], [x + k * 12 + math.sin(ang) * h * 0.5, H + 40 - h * 0.6], [x + k * 12 + math.sin(ang) * h, H + 40 - h]],
                        "size": 13, "color": "@near", "taper_start": 0, "taper_end": 0.95, "seed": i * 4 + k})
    return {"id": "foreground", "kind": "overlay", "parallax": 1.35, "filters": {"blur": 3}, "elements": els}


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------

def fx_elements(t: int, pose: BipedPose, info: dict, history: list[tuple[float, float]]) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (smear layer, fx layer (additive glow), dust layer) elements."""
    smear, fx, dust = [], [], []
    torso = (pose.x, pose.y - 40 * S)
    if TAKEOFF + 1 <= t <= TAKEOFF + 3:
        tr = me.smear_trail(history, len(history) - 1, length=len(history) - 1, radius=30 * S, taper=0.15)
        tr["color"] = "@outfit"
        smear.append(tr)
        legs = [(x, y + 70 * S) for x, y in history]
        tl = me.smear_trail(legs, len(legs) - 1, length=len(legs) - 1, radius=20 * S, taper=0.15)
        tl["color"] = "@outfit_2"
        smear.append(tl)
    if "velocity" in info and t <= TAKEOFF + 10:
        vx, vy = info["velocity"]
        fx.append({"type": "speed_lines", "mode": "parallel", "angle": math.degrees(math.atan2(vy, vx)), "x": torso[0] - 380, "y": torso[1] - 300,
                   "w": 760, "h": 600, "count": 30, "width": 4, "length": 420, "min_length": 120, "color": "rgba(255,250,235,0.85)", "seed": 9 + t // 2,
                   "animate": False,  # re-seed on twos: lines change every other frame instead of strobing
                   "opacity": me.clamp(1 - (t - TAKEOFF - 1) / 10)})
    if 49 <= t <= 57:
        k = (t - 49) / 8
        fx.append({"type": "sparkle", "color": "@fx_glow", "size": 26 * math.sin(k * math.pi) + 4,
                   "points": [[torso[0] + 140, torso[1] - 120, 1], [torso[0] - 120, torso[1] - 60, 0.6], [torso[0] + 60, torso[1] + 160, 0.5]]})
    if LAND <= t <= LAND + 3:
        fx.append({"type": "speed_lines", "mode": "radial", "cx": LAND_X, "cy": GROUND - 140, "inner_radius": 300 + (t - LAND) * 40,
                   "count": 110, "width": 9, "color": "@fx", "seed": 3})
    if t >= 88:
        k = t - 88
        tw = [(LAND_X + 150 * S, HIP_STAND - 150 * S), (LAND_X - 150 * S, HIP_STAND - 140 * S), (LAND_X + 190 * S, HIP_STAND - 40 * S)]
        fx.append({"type": "sparkle", "color": "@fx_glow",
                   "points": [[x, y, 0.5 + 0.5 * abs(math.sin(k * 0.35 + i * 1.7))] for i, (x, y) in enumerate(tw)], "size": 22})
    if LAND <= t <= LAND + 22:
        k = t - LAND
        fade = me.clamp(1 - k / 22)
        for side in (-1, 1):
            for j in range(3):
                spread = (40 + j * 55 + k * (7 + j * 3)) * S * 0.75
                r = (16 + j * 6 + k * (1.6 + j * 0.4)) * S * 0.7
                dust.append({"type": "ellipse", "cx": LAND_X + side * spread, "cy": GROUND - r * 0.5 - k * 0.6 * j, "rx": r * 1.3, "ry": r,
                             "fill": "@dust", "stroke": "@line_soft", "stroke_width": 2, "opacity": 0.9 * fade})
        dust.append({"type": "stroke", "brush": "spray", "points": [[LAND_X - (60 + k * 14) * S, GROUND - 8], [LAND_X + (60 + k * 14) * S, GROUND - 8]],
                     "size": 30 + k, "color": "@dust", "density": 0.5, "opacity": fade, "seed": 70 + k})
    return smear, fx, dust


def title_layer(t: int) -> dict | None:
    if t < 92:
        return None
    k = me.ease("overshoot", min(1.0, (t - 92) / 10))
    x = me.lerp(-700, 960, k)
    sub_k = me.ease("ease_out_cubic", min(1.0, max(0.0, (t - 98) / 8)))
    return {
        "id": "title", "kind": "overlay", "screen_space": True,
        "elements": [
            {"type": "polygon", "points": [[x - 520, 95], [x + 560, 80], [x + 520, 235], [x - 560, 250]], "fill": "@outfit", "stroke": "@line", "stroke_width": 6, "opacity": 0.95},
            {"type": "halftone", "x": x - 560, "y": 80, "w": 1120, "h": 170, "spacing": 10, "color": "@outfit_shade", "x0": x - 560, "y0": 250, "x1": x - 560, "y1": 80, "max_radius": 5.5, "blend": "source-atop"},
            {"type": "text", "text": "CelCraft Studio", "x": x, "y": 195, "size": 104, "align": "center", "fill": "@accent", "stroke": "@line", "stroke_width": 12},
            {"type": "text", "text": "frame-by-frame sakuga engine", "x": 960, "y": 300, "size": 36, "align": "center", "fill": "#ffffff",
             "stroke": "@line", "stroke_width": 7, "opacity": sub_k},
        ],
    }


# ---------------------------------------------------------------------------
# Scene assembly
# ---------------------------------------------------------------------------

def drawing_schedule() -> list[tuple[int, int, str, str]]:
    """(start_frame, hold, key, notes) — idle & hero on twos, action on ones, held anticipation."""
    out: list[tuple[int, int, str, str]] = []
    for f in range(0, 24, 2):
        out.append((f, 2, "key" if f == 0 else "inbetween", "idle: breathe + boil" if f == 0 else ""))
    for f in range(24, 32):
        out.append((f, 1, "breakdown" if f == 28 else "inbetween", "anticipation slow-in" if f == 24 else ""))
    out.append((32, 4, "key", "anticipation hold (crouch key)"))
    for f in range(TAKEOFF, 81):
        key, note = "inbetween", ""
        if f == TAKEOFF:
            key, note = "extreme", "take-off: max stretch"
        elif TAKEOFF < f <= TAKEOFF + 3:
            key, note = "smear", "smear frame"
        elif f == 50:
            key, note = "key", "apex tuck"
        elif f in (LAND, LAND + 1):
            key, note = "impact", "impact frame + shake"
        elif f in (LAND + 2, LAND + 3):
            key, note = "impact", "impact focus lines"
        elif f == 72:
            key, note = "breakdown", "recovery overshoot"
        out.append((f, 1, key, note))
    for f in range(81, N, 2):
        out.append((f, min(2, N - f), "key" if f == 81 else "inbetween", "hero pose (twos, boil)" if f == 81 else ""))
    return out


def build_scene(audio_tracks: list[dict], mouths: list[str]) -> Scene:
    pip = Biped(lighting=Lighting(key=(0.75, -0.65), rim=(-0.9, -0.35)))
    blinks = al_blinks()
    poses = [pose_at(t, mouths, blinks) for t in range(N)]

    # scarf follow-through simulated on ones over the whole shot
    anchors = [pip.anchors(p)["neck"] for p, _ in poses]
    anchors = [(x - 14 * S, y + 6 * S) for x, y in anchors]
    chains = me.follow_through_chain(anchors, segments=6, segment_length=21 * S, gravity=(0.0, 0.9), stiffness=0.5, damping=0.9,
                                     rest_direction=(-0.55, 1.0), wind=lambda f, i: (-0.18 * i, math.sin(f * 0.35 + i) * 0.3))
    # hair follow-through: spring on vertical velocity
    ys = [p.y for p, _ in poses]
    vy = [0.0] + [ys[i] - ys[i - 1] for i in range(1, N)]
    hair = me.spring_follow([me.clamp(-v * 1.1, -28, 28) for v in vy], stiffness=0.25, damping=0.7, delay=1)

    light = pip.lighting
    bg = background_layers(light)
    fg = foreground_layer()
    hip_hist: list[tuple[float, float]] = []
    frames = []
    for start, hold, key, notes in drawing_schedule():
        pose, info = poses[start]
        pose.scarf = chains[start]
        pose.hair_swing = hair[start]
        pose.drawing = start
        hip_hist = [(poses[f][0].x, poses[f][0].y - 40 * S) for f in range(max(0, start - 5), start + 1)]
        smear, fx, dust = fx_elements(start, pose, info, hip_hist)
        height = (HIP_STAND - pose.y) / S if TAKEOFF < start < LAND else 0.0
        layers = [
            *bg,
            {"id": "contact_shadow", "kind": "shade", "blend": "multiply", "elements": [pip.ground_shadow(pose.x + 10, GROUND, max(0.0, height) * S, width=80 * S)]},
            {"id": "smear", "kind": "fx", "elements": smear},
            {"id": "character", "kind": "line", "elements": pip.build(pose)},
            {"id": "dust", "kind": "fx", "elements": dust},
            {"id": "fx", "kind": "fx", "blend": "add", "filters": {"glow": {"radius": 10, "strength": 1.2}}, "elements": fx},
            fg,
        ]
        tl = title_layer(start)
        if tl:
            layers.append(tl)
        frame = {"hold": hold, "key": key, "layers": layers, "phoneme": mouths[start], "notes": notes or None}
        if start == 4:
            frame["dialogue"] = "Yosh!"
        if start == 89:
            frame["dialogue"] = "Yatta!"
        if start in (LAND, LAND + 1):
            frame["post"] = {"impact": {"mode": "mono", "color_a": "#fff3dc", "color_b": "#1d1a2b", "threshold": 0.52}}
        frames.append(frame)

    camera_keys = [
        {"frame": 0, "x": 860, "y": 590, "zoom": 1.05, "ease": "ease_in_out"},
        {"frame": 32, "x": 820, "y": 640, "zoom": 1.16, "ease": "ease_in_cubic"},
        {"frame": TAKEOFF, "x": 900, "y": 590, "zoom": 1.02, "ease": "ease_in_out"},
        {"frame": 52, "x": 1220, "y": 470, "zoom": 0.92, "ease": "ease_in_out"},
        {"frame": LAND, "x": 1520, "y": 600, "zoom": 1.02, "ease": "ease_out_cubic",
         "shake": {"amplitude": 26, "frequency": 0.55, "rotation": 1.6, "decay": 0.16, "start_frame": LAND, "seed": 4}},
        {"frame": 84, "x": 1600, "y": 560, "zoom": 1.06, "ease": "ease_in_out"},
        {"frame": N - 1, "x": 1610, "y": 550, "zoom": 1.1},
    ]
    return Scene.model_validate({
        "name": "sakuga_jump", "width": W, "height": H, "fps": FPS, "style": "anime_cel", "background": "#1d1a2b",
        "frames": frames, "camera_keys": camera_keys, "audio": audio_tracks,
        "markers": [{"frame": TAKEOFF, "label": "take-off", "kind": "sfx"}, {"frame": LAND, "label": "impact", "kind": "sfx"},
                    {"frame": 89, "label": "Yatta!", "kind": "dialogue"}],
        "meta": {"generator": "examples/demo_sakuga_jump.py"},
    })


def al_blinks() -> list[float]:
    from celcraft.rig_engine import blink_track

    b = blink_track(N, FPS, seed=5, first=0.7, min_gap=2.6, max_gap=3.4)
    for t in range(24, 90):  # eyes wide open during the action
        b[t] = 1.0
    return b


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="celcraft_demo", help="project directory")
    ap.add_argument("--scale", type=float, default=1.0, help="render scale (0.5 = 960x540 quick run)")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    proj = Project.init(args.out, "CelCraft Sakuga Demo", width=W, height=H, fps=FPS, style="anime_cel")
    tracks = build_audio(proj.root / "audio")
    mouths = build_mouths(proj.root / "audio")
    scene = build_scene(tracks, mouths)
    proj.save_scene(scene)
    print(f"[scene] {len(scene.frames)} drawings / {scene.duration_frames} frames; lint: {validate_scene_json(scene)}")

    out_dir = proj.render_dir(scene.name)
    pool = RendererPool(workers=args.workers) if args.workers else None
    res = render_scene(scene, out_dir, scale=args.scale, base_dir=proj.root, pool=pool,
                       progress=lambda d, n: print(f"\r[render] {d}/{n}", end="", flush=True))
    print(f"\n[render] {res.rendered} rendered, {res.reused} reused in {res.seconds:.1f}s")

    review = proj.review_dir(scene.name)
    frames = sorted(out_dir.glob("frame_*.png"))
    tl = scene.timeline()
    picks = [t for t in range(N) if t == 0 or tl[t] != tl[t - 1]]
    contact_sheet([frames[t] for t in picks], indices=picks, columns=10, thumb_width=200,
                  labels=[scene.frames[tl[t]].key for t in picks], notes=[scene.frames[tl[t]].notes or "" for t in picks],
                  title="sakuga_jump — contact sheet", highlight=[t for t in picks if scene.frames[tl[t]].key in ("key", "extreme")]).save(review / "contact_sheet.png")
    # onion skins on a transparent subject pass (character + smear only) so the moving camera doesn't ghost the BG
    import io

    from PIL import Image

    from celcraft.renderer_bridge import subject_pass

    for f in (30, 38, 50, 66):
        idx = list(range(f - 3, f + 3))
        pngs = subject_pass(scene, idx, ["smear", "character"], scale=args.scale, base_dir=proj.root)
        imgs = [Image.open(io.BytesIO(b)) for b in pngs]
        onion_from_sequence(imgs, 3, before=3, after=2, label=f"frame {f}").save(review / f"onion_{f:05d}.png")
    k = args.scale
    hip_track = [(pose_at(t, mouths, [1.0] * N)[0].x, pose_at(t, mouths, [1.0] * N)[0].y) for t in range(TAKEOFF, LAND + 1)]
    cam_track = []
    from celcraft.schema import camera_at

    for i, t in enumerate(range(TAKEOFF, LAND + 1)):
        cam = camera_at(scene.camera_keys, t)
        z = cam.get("zoom", 1.0)
        x, y = hip_track[i]
        cam_track.append(((x - cam["x"]) * z + W / 2, (y - cam["y"]) * z + H / 2))
    motion_path_overlay(frames[50], {"hip (screen)": cam_track}, highlight=50 - TAKEOFF, scale=k).save(review / "motion_path_jump.png")
    (review / "xsheet.txt").write_text(xsheet_text(scene))

    qa = validate_scene_render(scene, out_dir)
    save_json(qa.as_dict(), review / "qa_report.json")
    print(f"[qa] ok={qa.ok} errors={sum(i.severity == 'error' for i in qa.issues)} warnings={sum(i.severity == 'warning' for i in qa.issues)}")
    for issue in qa.issues[:10]:
        print("     ", issue.as_dict())

    summary = {"project": str(proj.root), "render": res.as_dict(), "qa_ok": qa.ok, "review": str(review)}
    if not args.no_export:
        mix = al.mix_scene_audio(scene, proj.root / "audio" / "sakuga_jump_mix.wav", base_dir=proj.root)
        exports = {}
        for fmt, name, kw in (("mp4", "sakuga_jump.mp4", {}), ("animatic", "sakuga_jump_animatic.mp4", {}), ("gif", "sakuga_jump.gif", {"gif_width": 480, "gif_fps": 12})):
            r = export_scene(scene, out_dir, proj.root / "exports" / name, fmt=fmt, audio=mix if fmt != "gif" else None, **kw)
            exports[fmt] = r.as_dict()
            print(f"[export] {fmt}: {r.path} ({r.bytes / 1e6:.2f} MB)")
        summary["exports"] = exports
        bt = al.beat_markers(proj.root / "audio" / "music.wav", FPS)
        summary["music_tempo"] = bt["tempo"]
    summary["seconds"] = round(time.perf_counter() - t0, 1)
    save_json(summary, review / "summary.json")
    print(json.dumps(summary, indent=2, default=str))
    return 0 if qa.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
