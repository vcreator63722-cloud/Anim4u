"""CelCraft MCP server (stdio).

Exposes the whole studio to any MCP client: project & scene management,
rendering, visual review (preview / onion skin / contact sheet returned as
images the model can *see*), motion math, rig builders, lip-sync, QA and
export.

Run:  ``celcraft mcp``  or  ``python -m celcraft.mcp_server``

Works with the MCP Python SDK 1.x (FastMCP) and 2.x (MCPServer).
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any, Optional

try:  # MCP SDK 2.x
    from mcp.server.mcpserver import Image, MCPServer as _Server
except ImportError:  # MCP SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore[no-redef]

from PIL import Image as PILImage

from . import __version__
from . import motion_engine as me
from .project import Project, xsheet_text
from .renderer_bridge import RendererPool, render_scene_async, scene_payloads

INSTRUCTIONS = """CelCraft Studio: frame-by-frame 2D cel animation.
Workflow: project_init -> scene_create -> (design keys with motion_* + rig_* helpers) -> scene_add_frames
-> preview_frame (LOOK at it) -> render_scene -> onion_skin / contact_sheet (LOOK, fix spacing/arcs)
-> qa_scene -> export_scene. Call studio_guide for the full SOP and JSON cheat-sheet."""

mcp = _Server("celcraft-studio", instructions=INSTRUCTIONS)

_pool: Optional[RendererPool] = None
_pool_lock = asyncio.Lock()


async def _get_pool() -> RendererPool:
    """Persistent Chromium pool shared by every tool call (fast iteration)."""
    global _pool
    async with _pool_lock:
        if _pool is None:
            _pool = RendererPool()
            await _pool.start()
        return _pool


def _image(img: "PILImage.Image | bytes", max_side: int = 1280) -> Image:
    if isinstance(img, (bytes, bytearray)):
        img = PILImage.open(io.BytesIO(img))
    img = img.convert("RGBA")
    if max(img.size) > max_side:
        s = max_side / max(img.size)
        img = img.resize((int(img.width * s), int(img.height * s)), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return Image(data=buf.getvalue(), format="png")


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=1, default=str)


def _project(path: str) -> Project:
    return Project.find(path)


# ---------------------------------------------------------------------------
# Guide & presets
# ---------------------------------------------------------------------------

@mcp.tool()
def studio_guide() -> str:
    """Return the CelCraft studio SOP (SKILL.md): pipeline, JSON schema cheat-sheet, quality gates."""
    from .presets import data_file

    try:
        return data_file("SKILL.md").read_text()
    except FileNotFoundError:
        return INSTRUCTIONS


@mcp.tool()
def list_presets(kind: str = "all") -> str:
    """List palettes (with colour roles usable as '@role'), styles, mouth shapes, easings, brushes, blend modes."""
    from .presets import mouths, palettes, styles
    from .schema import LAYER_BLEND_MODES

    data = {
        "palettes": {k: {"description": v["description"], "colors": v["colors"]} for k, v in palettes().items()},
        "styles": {k: {"description": v["description"], "palette": v.get("palette"), "line": v.get("line"), "post": v.get("post")} for k, v in styles().items()},
        "mouths": {k: v["description"] for k, v in mouths().items()},
        "easings": list(me.EASINGS),
        "brushes": ["gpen", "ink", "pencil", "charcoal", "marker", "drybrush", "spray", "airbrush"],
        "blends": LAYER_BLEND_MODES,
    }
    return _json(data if kind == "all" else data.get(kind, {"error": f"unknown kind {kind}"}))


@mcp.tool()
def scene_json_schema() -> str:
    """Full JSON Schema of a Scene (frames, layers, elements, camera, post, audio)."""
    from .schema import json_schema

    return _json(json_schema())


# ---------------------------------------------------------------------------
# Project & scenes
# ---------------------------------------------------------------------------

@mcp.tool()
def project_init(path: str, title: str = "", width: int = 1920, height: int = 1080, fps: float = 24,
                 style: str = "anime_cel", palette: Optional[str] = None) -> str:
    """Create (or open) a project workspace with a project bible."""
    p = Project.init(path, title or None, width=width, height=height, fps=fps, style=style, palette=palette)
    return _json({"root": str(p.root), "bible": p.bible.model_dump()})


@mcp.tool()
def project_info(project: str) -> str:
    """Project bible + scene list with durations."""
    return _json(_project(project).info())


@mcp.tool()
def project_update_bible(project: str, patch: dict) -> str:
    """Update project bible fields (title, logline, characters, locations, rules, style, palette...)."""
    p = _project(project)
    data = p.bible.model_dump()
    data.update(patch)
    p.bible = type(p.bible).model_validate(data)
    p.save_bible()
    return _json(p.bible.model_dump())


@mcp.tool()
def scene_create(project: str, name: str, scene: Optional[dict] = None, overwrite: bool = False) -> str:
    """Create a scene. Optional ``scene`` dict may contain any Scene fields (frames, audio, camera_keys, post...)."""
    p = _project(project)
    data = dict(scene or {})
    data.pop("name", None)
    s = p.new_scene(name, overwrite=overwrite, **data)
    return _json({"scene": name, "drawings": len(s.frames), "frames": s.duration_frames})


@mcp.tool()
def scene_get(project: str, name: str, include_frames: bool = False, drawing: Optional[int] = None) -> str:
    """Scene summary; ``include_frames`` returns the full JSON; ``drawing`` returns a single drawing's JSON."""
    s = _project(project).load_scene(name)
    if drawing is not None:
        return s.frames[drawing].model_dump_json(indent=1, exclude_none=True)
    if include_frames:
        return s.model_dump_json(indent=1, exclude_none=True)
    return _json({
        "name": s.name, "size": [s.width, s.height], "fps": s.fps, "style": s.style, "palette": s.palette,
        "drawings": len(s.frames), "frames": s.duration_frames, "seconds": round(s.duration_seconds, 3),
        "holds": [f.hold for f in s.frames], "keys": [i for i, f in enumerate(s.frames) if f.key in ("key", "extreme")],
        "audio": [a.model_dump() for a in s.audio], "camera_keys": len(s.camera_keys),
    })


@mcp.tool()
def scene_update(project: str, name: str, patch: dict) -> str:
    """Set top-level scene fields (style, palette, palette_overrides, post, audio, camera_keys, markers, background, fps...)."""
    p = _project(project)
    s = p.load_scene(name)
    data = s.model_dump()
    patch = {k: v for k, v in patch.items() if k not in ("name", "frames")}
    data.update(patch)
    s = type(s).model_validate(data)
    p.save_scene(s)
    return _json({"ok": True, "updated": list(patch)})


@mcp.tool()
def scene_add_frames(project: str, name: str, frames: list[dict], index: Optional[int] = None) -> str:
    """Append drawings (or insert at ``index``). Each frame: {hold, key, layers:[{id, kind, blend, clip_to, parallax, elements:[...]}], camera, post, phoneme, notes}."""
    s = _project(project).add_frames(name, frames, index)
    return _json({"drawings": len(s.frames), "frames": s.duration_frames})


@mcp.tool()
def scene_replace_frame(project: str, name: str, index: int, frame: dict) -> str:
    """Replace one drawing (fix a bad inbetween after reviewing the onion skin)."""
    s = _project(project).replace_frame(name, index, frame)
    return _json({"drawings": len(s.frames), "frames": s.duration_frames})


@mcp.tool()
def scene_delete_frames(project: str, name: str, start: int, end: Optional[int] = None) -> str:
    """Delete drawings [start, end)."""
    s = _project(project).delete_frames(name, start, end)
    return _json({"drawings": len(s.frames), "frames": s.duration_frames})


@mcp.tool()
def scene_xsheet(project: str, name: str, max_rows: int = 200) -> str:
    """X-sheet (exposure sheet): per-frame drawing numbers per layer, holds, phonemes, sfx, camera, notes."""
    return xsheet_text(_project(project).load_scene(name), max_rows)


@mcp.tool()
def scene_lint(project: str, name: str) -> str:
    """Static studio lint of a scene (no rendering)."""
    from .validator import validate_scene_json

    return _json(validate_scene_json(_project(project).load_scene(name)))


# ---------------------------------------------------------------------------
# Rendering & visual review
# ---------------------------------------------------------------------------

@mcp.tool()
async def sketch(layers: list[dict], width: int = 1280, height: int = 720, background: Optional[Any] = "#ffffff",
                 palette: Optional[str] = "anime_cel", style: Optional[str] = None, camera: Optional[dict] = None,
                 post: Optional[dict] = None, frame: int = 0) -> list:
    """Render raw layers immediately (no project needed) and return the image — use for look-dev and pose checks."""
    from .schema import Scene

    scene = Scene.model_validate({"name": "sketch", "width": width, "height": height, "background": background, "palette": palette,
                                  "style": style, "frames": [{"layers": layers, "camera": camera, "post": post}]})
    _, _, payload = scene_payloads(scene, frames=[0])[0]
    payload["frame"] = frame
    png = await (await _get_pool()).render(payload)
    return [f"{width}x{height} rendered", _image(png)]


@mcp.tool()
async def preview_frame(project: str, name: str, frame: int = 0, scale: float = 0.5) -> list:
    """Render a single timeline frame and return it as an image (fast; does not touch the render cache)."""
    p = _project(project)
    s = p.load_scene(name)
    items = scene_payloads(s, scale=scale, frames=[frame])
    if not items:
        return [f"frame {frame} out of range (0..{s.duration_frames - 1})"]
    png = await (await _get_pool()).render(items[0][2], base_dir=p.root)
    dest = p.review_dir(name) / f"preview_{frame:05d}.png"
    dest.write_bytes(png)
    return [_json({"frame": frame, "drawing": items[0][1], "path": str(dest)}), _image(png)]


@mcp.tool()
async def render_scene(project: str, name: str, scale: float = 1.0, frames: Optional[str] = None, use_cache: bool = True) -> str:
    """Render a scene to renders/<scene>/frame_XXXXX.png (parallel, cached). ``frames`` e.g. '0-23,40'."""
    from .cli import _parse_range

    p = _project(project)
    s = p.load_scene(name)
    out = p.render_dir(name, preview=scale != 1.0)
    res = await render_scene_async(s, out, pool=await _get_pool(), scale=scale, frames=_parse_range(frames, s.duration_frames),
                                   use_cache=use_cache, base_dir=p.root)
    return _json(res.as_dict())


def _rendered(p: Project, name: str) -> list[Path]:
    frames = sorted(p.render_dir(name).glob("frame_*.png"))
    if not frames:
        raise FileNotFoundError("scene not rendered yet — call render_scene first")
    return frames


@mcp.tool()
async def onion_skin(project: str, name: str, frame: int, before: int = 2, after: int = 1, ink_only: bool = False,
                     layers: Optional[list[str]] = None) -> list:
    """FlipaClip onion skin: previous frames red, next frames green. Check arcs, spacing, volume.

    ``layers`` (e.g. ["character"]) renders a transparent subject pass of just those layers — use it
    whenever the camera moves, so the background doesn't ghost.
    """
    from .onion_skin import onion_skin as _onion

    p = _project(project)
    s = p.load_scene(name)
    idx = [f for f in range(frame - before, frame + after + 1) if 0 <= f < s.duration_frames]
    if layers:
        from .renderer_bridge import subject_pass_async

        pngs = await subject_pass_async(s, idx, layers, pool=await _get_pool(), base_dir=p.root)
        imgs = {f: PILImage.open(io.BytesIO(b)) for f, b in zip(idx, pngs)}
    else:
        frames = _rendered(p, name)
        imgs = {f: frames[f] for f in idx}
    prev = [imgs[f] for f in range(frame - 1, frame - before - 1, -1) if f in imgs]
    nxt = [imgs[f] for f in range(frame + 1, frame + after + 1) if f in imgs]
    img = _onion(imgs[frame], prev, nxt, current_style="ink" if ink_only else "full", label=f"frame {frame}")
    dest = p.review_dir(name) / f"onion_{frame:05d}.png"
    img.save(dest)
    return [str(dest), _image(img)]


@mcp.tool()
def contact_sheet(project: str, name: str, every: int = 1, keys_only: bool = False, columns: int = 8, thumb_width: int = 220) -> list:
    """Annotated contact sheet (one thumbnail per drawing) of the rendered scene."""
    from .onion_skin import contact_sheet as cs

    p = _project(project)
    s = p.load_scene(name)
    frames = _rendered(p, name)
    tl = s.timeline()
    picks = [t for t in range(0, min(len(frames), len(tl)), max(1, every)) if t == 0 or tl[t - 1] != tl[t]]
    if keys_only:
        picks = [t for t in picks if s.frames[tl[t]].key in ("key", "extreme", "breakdown", "smear", "impact")]
    img = cs([frames[t] for t in picks], indices=picks, columns=columns, thumb_width=thumb_width,
             labels=[f"{s.frames[tl[t]].key} x{s.frames[tl[t]].hold}" for t in picks],
             notes=[s.frames[tl[t]].notes or "" for t in picks], title=f"{s.name} — {len(tl)} frames",
             highlight=[t for t in picks if s.frames[tl[t]].key in ("key", "extreme")])
    dest = p.review_dir(name) / "contact_sheet.png"
    img.save(dest)
    return [str(dest), _image(img, 1600)]


@mcp.tool()
def motion_path(project: str, name: str, tracks: dict[str, list[list[float]]], frame: int = 0) -> list:
    """Overlay motion-path dots (one per frame) on a rendered frame and report spacing (pops / linear warnings)."""
    from .onion_skin import motion_path_overlay

    p = _project(project)
    frames = _rendered(p, name)
    img = motion_path_overlay(frames[min(frame, len(frames) - 1)], tracks, highlight=frame)
    report = {k: me.spacing_report([tuple(x[:2]) for x in v]) for k, v in tracks.items()}
    return [_json(report), _image(img)]


@mcp.tool()
def qa_scene(project: str, name: str) -> str:
    """Automated QA on rendered frames: blank, flicker, frozen, photosensitive flashes, frame count."""
    from .project import save_json
    from .validator import validate_scene_json, validate_scene_render

    p = _project(project)
    s = p.load_scene(name)
    rep = validate_scene_render(s, p.render_dir(name)).as_dict()
    rep["lint"] = validate_scene_json(s)
    save_json(rep, p.review_dir(name) / "qa_report.json")
    rep.pop("metrics", None)
    return _json(rep)


@mcp.tool()
def export_scene(project: str, name: str, format: str = "mp4", with_audio: bool = True, background: Optional[str] = None) -> str:
    """Export rendered frames: mp4 | webm (alpha) | prores (alpha .mov) | gif | apng | png | animatic (HUD burn-in)."""
    from .audio_lipsync import mix_scene_audio
    from .exporter import EXT, export_scene as _export

    p = _project(project)
    s = p.load_scene(name)
    _rendered(p, name)
    mix = mix_scene_audio(s, p.root / "audio" / f"{name}_mix.wav", base_dir=p.root) if with_audio else None
    dest = p.export_path(name + ("_animatic" if format == "animatic" else ""), EXT[format])
    res = _export(s, p.render_dir(name), dest, fmt=format, audio=mix, background=background)
    return _json(res.as_dict())


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

@mcp.tool()
def lipsync(audio_path: str, fps: float = 24, start: float = 0.0, n_frames: Optional[int] = None, text: Optional[str] = None,
            rhubarb_json: Optional[str] = None) -> str:
    """Per-frame Preston Blair mouth shapes (X A B C D E F G H) from dialogue audio (optionally text-guided or Rhubarb JSON)."""
    from . import audio_lipsync as al

    if rhubarb_json:
        if n_frames is None:
            return _json({"error": "n_frames required with rhubarb_json"})
        shapes = al.rhubarb_lipsync(rhubarb_json, fps, n_frames, offset=start)
    elif text:
        shapes = al.text_lipsync(text, audio_path, fps, n_frames=n_frames, start=start)
    else:
        shapes = al.amplitude_lipsync(audio_path, fps, n_frames=n_frames, start=start)
    return _json({"fps": fps, "frames": len(shapes), "shapes": shapes})


@mcp.tool()
def beat_markers(audio_path: str, fps: float = 24) -> str:
    """Tempo, beat frames and onset frames of a music/sfx file (time hits and accents to the beat)."""
    from .audio_lipsync import beat_markers as bm

    return _json(bm(audio_path, fps))


@mcp.tool()
def synth_sfx(kind: str, out_path: str, duration: float = 0.5, bpm: float = 120) -> str:
    """Generate placeholder audio: whoosh | impact | pop | music (wav)."""
    from . import audio_lipsync as al

    gen = {"whoosh": lambda: al.synth_whoosh(duration), "impact": lambda: al.synth_impact(duration), "pop": lambda: al.synth_pop(duration),
           "music": lambda: al.synth_music(duration, bpm=bpm)}
    if kind not in gen:
        return _json({"error": f"kind must be one of {list(gen)}"})
    return _json({"path": str(al.save_wav(out_path, gen[kind]()))})


# ---------------------------------------------------------------------------
# Motion math & rigs
# ---------------------------------------------------------------------------

@mcp.tool()
def motion_spacing(easing: str = "ease_in_out", inbetweens: int = 3, start: Optional[list[float]] = None, end: Optional[list[float]] = None) -> str:
    """Timing chart: fractions for key->inbetweens->key with an easing; with start/end also returns positions."""
    fr = me.spacing_chart(inbetweens, easing)
    out: dict = {"fractions": fr}
    if start and end:
        out["positions"] = [list(me.lerp_value(tuple(start), tuple(end), f)) for f in fr]
    return _json(out)


@mcp.tool()
def motion_jump(start: list[float], end: list[float], apex_height: float, frames: int) -> str:
    """Physically-timed jump arc: positions per frame + velocities + squash/stretch matrices about each position."""
    pts = me.ballistic_jump(tuple(start), tuple(end), apex_height, frames)
    vel = me.velocities(pts)
    ss = [me.squash_stretch(v) for v in vel]
    return _json({"positions": pts, "velocities": vel, "squash_stretch": [{"along": s.scale_along, "across": s.scale_across, "angle": s.angle} for s in ss],
                  "spacing": me.spacing_report(pts)})


@mcp.tool()
def motion_arc(a: list[float], b: list[float], c: list[float], samples: int = 8, easing: str = "ease_in_out") -> str:
    """Positions along the circular arc through a (start), b (breakdown), c (end) with eased spacing."""
    f = me.arc_through(tuple(a), tuple(b), tuple(c))
    return _json([f(t) for t in me.spacing_chart(samples - 1, easing)])


@mcp.tool()
def motion_follow_chain(anchors: list[list[float]], segments: int = 6, segment_length: float = 18.0, gravity_y: float = 0.9,
                        rest_direction: Optional[list[float]] = None) -> str:
    """Follow-through chain (hair, scarf, tail): per-frame chain points dragged behind an anchor track."""
    frames = me.follow_through_chain([tuple(a) for a in anchors], segments=segments, segment_length=segment_length,
                                     gravity=(0.0, gravity_y), rest_direction=tuple(rest_direction or (-1.0, 0.3)))
    return _json(frames)


@mcp.tool()
def rig_biped(pose: dict, line_size: float = 4.2) -> str:
    """Elements for the built-in cel-shaded biped "Pip" in a pose.

    pose keys (all optional): x, y (hip), scale, facing (1/-1), lean, head_tilt, foot_front, foot_back,
    hand_front, hand_back ([x,y] relative to hip), knee_bend, elbow_bend, squash (6-float matrix),
    mouth (X/A-H), eye_open (0..1), look [x,y], brow (-1..1), happy_eyes, hair_swing, scarf ([[x,y],...] world),
    drawing, boil. Put the returned list in one layer's ``elements``.
    """
    from .rig_engine import Biped, BipedPose

    data = dict(pose)
    for k in ("foot_front", "foot_back", "hand_front", "hand_back", "look", "head_offset"):
        if k in data:
            data[k] = tuple(data[k])
    if "scarf" in data and data["scarf"]:
        data["scarf"] = [tuple(p) for p in data["scarf"]]
    b = Biped(line_size=line_size)
    p = BipedPose(**data)
    return _json({"elements": b.build(p), "anchors": b.anchors(p)})


@mcp.tool()
def rig_mouth(shape: str, cx: float, cy: float, width: float = 12.0, rotation: float = 0.0) -> str:
    """Preston Blair mouth element group (X, A..H) at (cx, cy); width = half mouth width in px."""
    from .rig_engine import mouth_elements

    return _json(mouth_elements(shape, cx, cy, width, rotation=rotation))


@mcp.tool()
def rig_eye(cx: float, cy: float, size: float = 14.0, openness: float = 1.0, look_x: float = 0.0, look_y: float = 0.0, happy: bool = False) -> str:
    """Anime eye element group (white, gradient iris, shines, lash) — openness 0..1 for blinks."""
    from .rig_engine import eye_elements

    return _json(eye_elements(cx, cy, size, openness=openness, look=(look_x, look_y), happy=happy))


@mcp.tool()
def health() -> str:
    """Server version + renderer status."""
    return _json({"celcraft": __version__, "renderer_started": bool(_pool and _pool.started)})


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
