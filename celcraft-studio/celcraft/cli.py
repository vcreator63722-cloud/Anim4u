"""`celcraft` command line interface.

Every command prints machine-readable JSON (unless noted) so agents can parse
results. Run ``celcraft --help`` or ``celcraft COMMAND --help``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from . import __version__

app = typer.Typer(add_completion=False, no_args_is_help=True, help="CelCraft Studio — frame-by-frame 2D animation engine for AI agents.")
scene_app = typer.Typer(no_args_is_help=True, help="Create and edit scenes.")
app.add_typer(scene_app, name="scene")

ProjectOpt = typer.Option(".", "--project", "-p", help="Project directory (searched upward for project_bible.json).")


def _out(obj) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


def _project(path: str):
    from .project import Project

    return Project.find(path)


def _parse_range(spec: Optional[str], total: int) -> Optional[list[int]]:
    """'0-10,15,20-' -> frame list (0-based, inclusive ranges)."""
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            lo = int(a) if a else 0
            hi = int(b) if b else total - 1
            out.extend(range(lo, min(hi, total - 1) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(f for f in out if 0 <= f < total))


def _fail(msg: str, code: int = 1):
    typer.echo(json.dumps({"ok": False, "error": msg}), err=True)
    raise typer.Exit(code)


@app.command()
def version():
    """Print the CelCraft version."""
    typer.echo(__version__)


@app.command()
def doctor():
    """Check the environment: Chromium renderer, FFmpeg, encoders, presets."""
    report: dict = {"python": sys.version.split()[0], "celcraft": __version__}
    try:
        from ._ffmpeg import encoders, ffmpeg_exe

        report["ffmpeg"] = ffmpeg_exe()
        enc = encoders()
        report["encoders"] = {e: e in enc for e in ("libx264", "libvpx-vp9", "prores_ks", "gif", "apng", "aac", "libopus")}
    except Exception as e:  # noqa: BLE001
        report["ffmpeg"] = f"MISSING: {e}"
    try:
        from .renderer_bridge import render_payloads

        t0 = time.perf_counter()
        png = render_payloads([{"width": 64, "height": 64, "background": "#fff", "layers": [
            {"id": "l", "elements": [{"type": "stroke", "points": [[8, 8], [56, 56]], "size": 6}]}]}])[0]
        report["renderer"] = {"ok": len(png) > 100, "seconds": round(time.perf_counter() - t0, 2)}
    except Exception as e:  # noqa: BLE001
        report["renderer"] = {"ok": False, "error": str(e)[:500]}
    from .presets import palettes, styles

    report["presets"] = {"palettes": list(palettes()), "styles": list(styles())}
    report["ok"] = bool(report.get("renderer", {}).get("ok")) and not str(report.get("ffmpeg", "")).startswith("MISSING")
    _out(report)
    if not report["ok"]:
        raise typer.Exit(1)


@app.command()
def init(
    path: Path = typer.Argument(..., help="Project directory to create."),
    title: Optional[str] = typer.Option(None, help="Project title."),
    width: int = 1920,
    height: int = 1080,
    fps: float = 24,
    style: str = typer.Option("anime_cel", help="Style preset (see `celcraft presets styles`)."),
    palette: Optional[str] = typer.Option(None, help="Palette preset (defaults to the style's palette)."),
):
    """Create a project workspace with a project bible."""
    from .project import Project

    p = Project.init(path, title, width=width, height=height, fps=fps, style=style, palette=palette)
    _out({"ok": True, "root": str(p.root), "bible": p.bible.model_dump()})


@app.command()
def info(project: str = ProjectOpt):
    """Show project bible and scene summary."""
    _out(_project(project).info())


# ---------------------------------------------------------------------------
# scenes
# ---------------------------------------------------------------------------

@scene_app.command("new")
def scene_new(
    name: str,
    project: str = ProjectOpt,
    from_json: Optional[Path] = typer.Option(None, "--from-json", help="Full scene JSON to import."),
    overwrite: bool = False,
):
    """Create a scene (optionally from a full scene JSON file)."""
    p = _project(project)
    overrides = json.loads(from_json.read_text()) if from_json else {}
    overrides.pop("name", None)
    s = p.new_scene(name, overwrite=overwrite, **overrides)
    _out({"ok": True, "scene": name, "path": str(p.scene_path(name)), "drawings": len(s.frames), "frames": s.duration_frames})


@scene_app.command("add-frames")
def scene_add_frames(name: str, frames_json: Path, project: str = ProjectOpt, index: Optional[int] = None):
    """Append (or insert at --index) frames from a JSON file (a frame object or a list of them)."""
    p = _project(project)
    data = json.loads(frames_json.read_text())
    frames = data if isinstance(data, list) else [data]
    s = p.add_frames(name, frames, index)
    _out({"ok": True, "drawings": len(s.frames), "frames": s.duration_frames})


@scene_app.command("lint")
def scene_lint(name: str, project: str = ProjectOpt):
    """Validate a scene JSON (schema + studio lint rules)."""
    from .validator import validate_scene_json

    p = _project(project)
    try:
        s = p.load_scene(name)
    except Exception as e:  # noqa: BLE001
        _fail(f"schema error: {e}")
    _out({"ok": True, "drawings": len(s.frames), "frames": s.duration_frames, "issues": validate_scene_json(s)})


@scene_app.command("list")
def scene_list(project: str = ProjectOpt):
    _out(_project(project).list_scenes())


# ---------------------------------------------------------------------------
# rendering & review
# ---------------------------------------------------------------------------

@app.command()
def render(
    name: str,
    project: str = ProjectOpt,
    scale: float = typer.Option(1.0, help="Output scale (0.5 = fast half-res preview)."),
    frames: Optional[str] = typer.Option(None, help="Timeline frames, e.g. '0-23,40'."),
    workers: Optional[int] = typer.Option(None, help="Parallel Chromium pages."),
    no_cache: bool = typer.Option(False, help="Re-render everything."),
):
    """Render a scene to PNG frames (renders/<scene>/frame_XXXXX.png)."""
    from .renderer_bridge import RendererPool, render_scene

    p = _project(project)
    s = p.load_scene(name)
    out = p.render_dir(name, preview=scale != 1.0)
    pool = RendererPool(workers=workers) if workers else None
    res = render_scene(s, out, scale=scale, frames=_parse_range(frames, s.duration_frames), use_cache=not no_cache, base_dir=p.root, pool=pool)
    if pool is not None:
        from .renderer_bridge import run_sync

        run_sync(pool.close())
    _out({"ok": True, **res.as_dict()})


@app.command()
def preview(name: str, frame: int = typer.Argument(0), project: str = ProjectOpt, scale: float = 0.5, out: Optional[Path] = None):
    """Render one timeline frame to a PNG (fast look-dev)."""
    from .renderer_bridge import render_frame_png

    p = _project(project)
    s = p.load_scene(name)
    png = render_frame_png(s, frame, scale=scale, base_dir=p.root)
    dest = out or p.review_dir(name) / f"preview_{frame:05d}.png"
    dest.write_bytes(png)
    _out({"ok": True, "path": str(dest)})


@app.command()
def onion(
    name: str,
    frame: int,
    project: str = ProjectOpt,
    before: int = 2,
    after: int = 1,
    ink: bool = typer.Option(False, help="Draw the current frame as ink only (light-table look)."),
    layers: Optional[str] = typer.Option(None, help="Comma-separated layer ids for a transparent subject pass (use when the camera moves)."),
    out: Optional[Path] = None,
):
    """FlipaClip-style onion skin of a frame (past=red, next=green)."""
    import io

    from PIL import Image

    from .onion_skin import onion_skin

    p = _project(project)
    s = p.load_scene(name)
    idx = [f for f in range(frame - before, frame + after + 1) if 0 <= f < s.duration_frames]
    if layers:
        from .renderer_bridge import subject_pass

        pngs = subject_pass(s, idx, [x.strip() for x in layers.split(",")], base_dir=p.root)
        imgs = {f: Image.open(io.BytesIO(b)) for f, b in zip(idx, pngs)}
    else:
        frames = sorted(p.render_dir(name).glob("frame_*.png"))
        if not frames:
            _fail("scene not rendered yet: run `celcraft render` first (or pass --layers)")
        imgs = {f: frames[f] for f in idx}
    prev = [imgs[f] for f in range(frame - 1, frame - before - 1, -1) if f in imgs]
    nxt = [imgs[f] for f in range(frame + 1, frame + after + 1) if f in imgs]
    img = onion_skin(imgs[frame], prev, nxt, current_style="ink" if ink else "full", label=f"frame {frame}")
    dest = out or p.review_dir(name) / f"onion_{frame:05d}.png"
    img.save(dest)
    _out({"ok": True, "path": str(dest)})


@app.command()
def sheet(
    name: str,
    project: str = ProjectOpt,
    every: int = typer.Option(1, help="Take every Nth timeline frame."),
    keys_only: bool = typer.Option(False, help="Only drawings tagged key/extreme/breakdown/smear/impact."),
    drawings: bool = typer.Option(True, help="One thumbnail per drawing (skip holds)."),
    columns: int = 8,
    thumb: int = 240,
    out: Optional[Path] = None,
):
    """Annotated contact sheet of a rendered scene."""
    from .onion_skin import contact_sheet

    p = _project(project)
    s = p.load_scene(name)
    frames = sorted(p.render_dir(name).glob("frame_*.png"))
    if not frames:
        _fail("scene not rendered yet: run `celcraft render` first")
    tl = s.timeline()
    picks = []
    for tf in range(0, min(len(frames), len(tl)), max(1, every)):
        d = tl[tf]
        if drawings and tf > 0 and tl[tf - 1] == d:
            continue
        if keys_only and s.frames[d].key not in ("key", "extreme", "breakdown", "smear", "impact"):
            continue
        picks.append(tf)
    img = contact_sheet(
        [frames[t] for t in picks], indices=picks, columns=columns, thumb_width=thumb,
        labels=[f"{s.frames[tl[t]].key} x{s.frames[tl[t]].hold}" + (f" [{s.frames[tl[t]].phoneme}]" if s.frames[tl[t]].phoneme else "") for t in picks],
        notes=[s.frames[tl[t]].notes or "" for t in picks],
        title=f"{s.name} — {len(tl)} frames @ {s.fps:g} fps",
        highlight=[t for t in picks if s.frames[tl[t]].key in ("key", "extreme")],
    )
    dest = out or p.review_dir(name) / "contact_sheet.png"
    img.save(dest)
    _out({"ok": True, "path": str(dest), "thumbnails": len(picks)})


@app.command()
def xsheet(name: str, project: str = ProjectOpt, csv: bool = typer.Option(False, help="CSV instead of text table."), rows: Optional[int] = None):
    """Print the X-sheet (exposure sheet) of a scene."""
    from .project import xsheet_csv, xsheet_text

    s = _project(project).load_scene(name)
    typer.echo(xsheet_csv(s) if csv else xsheet_text(s, rows))


@app.command()
def qa(name: str, project: str = ProjectOpt):
    """Automated QA of rendered frames (blank, flicker, frozen, flashes, frame count)."""
    from .project import save_json
    from .validator import validate_scene_json, validate_scene_render

    p = _project(project)
    s = p.load_scene(name)
    rep = validate_scene_render(s, p.render_dir(name)).as_dict()
    rep["lint"] = validate_scene_json(s)
    path = save_json(rep, p.review_dir(name) / "qa_report.json")
    summary = {k: rep[k] for k in ("ok", "frames", "errors", "warnings")}
    summary["issues"] = rep["issues"][:25]
    summary["report"] = str(path)
    _out(summary)
    if not rep["ok"]:
        raise typer.Exit(2)


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------

@app.command()
def lipsync(
    audio: Path,
    fps: float = 24,
    start: float = typer.Option(0.0, help="Timeline second where this audio starts."),
    frames: Optional[int] = typer.Option(None, help="Total timeline frames (default: audio length)."),
    text: Optional[str] = typer.Option(None, help="Dialogue script for text-guided sync."),
    rhubarb: Optional[Path] = typer.Option(None, help="Rhubarb JSON to import instead of analysing audio."),
):
    """Mouth shapes (Preston Blair X,A-H) per frame from dialogue audio."""
    from . import audio_lipsync as al

    if rhubarb:
        if frames is None:
            _fail("--frames is required with --rhubarb")
        shapes = al.rhubarb_lipsync(rhubarb, fps, frames, offset=start)
    elif text:
        shapes = al.text_lipsync(text, audio, fps, n_frames=frames, start=start)
    else:
        shapes = al.amplitude_lipsync(audio, fps, n_frames=frames, start=start)
    _out({"fps": fps, "frames": len(shapes), "shapes": "".join(shapes)})


@app.command()
def beats(audio: Path, fps: float = 24):
    """Tempo, beat grid and onsets as timeline frames."""
    from .audio_lipsync import beat_markers

    _out(beat_markers(audio, fps))


@app.command()
def mix(name: str, project: str = ProjectOpt):
    """Mix the scene's audio tracks into audio/<scene>_mix.wav."""
    from .audio_lipsync import mix_scene_audio

    p = _project(project)
    s = p.load_scene(name)
    path = mix_scene_audio(s, p.root / "audio" / f"{name}_mix.wav", base_dir=p.root)
    _out({"ok": True, "path": str(path) if path else None})


@app.command("export")
def export_cmd(
    name: str,
    project: str = ProjectOpt,
    fmt: str = typer.Option("mp4", "--format", "-f", help="mp4 | webm | prores | gif | apng | png | animatic"),
    out: Optional[Path] = None,
    audio: bool = typer.Option(True, help="Mix and mux the scene audio."),
    crf: int = 16,
    background: Optional[str] = typer.Option(None, help="Flatten transparency over this colour (mp4/gif)."),
):
    """Encode rendered frames (+ mixed audio) into a deliverable."""
    from .audio_lipsync import mix_scene_audio
    from .exporter import EXT, export_scene

    p = _project(project)
    s = p.load_scene(name)
    frames_dir = p.render_dir(name)
    if not any(frames_dir.glob("frame_*.png")):
        _fail("scene not rendered yet: run `celcraft render` first")
    mix_path = mix_scene_audio(s, p.root / "audio" / f"{name}_mix.wav", base_dir=p.root) if audio else None
    suffix = "_animatic" if fmt == "animatic" else ""
    dest = out or p.export_path(name + suffix, EXT[fmt])
    res = export_scene(s, frames_dir, dest, fmt=fmt, audio=mix_path, crf=crf, background=background)
    _out({"ok": True, **res.as_dict()})


# ---------------------------------------------------------------------------
# reference
# ---------------------------------------------------------------------------

@app.command()
def presets(kind: str = typer.Argument("all", help="palettes | styles | mouths | easings | brushes | blends | all")):
    """List bundled presets."""
    from . import motion_engine as me
    from .presets import mouths, palettes, styles
    from .schema import LAYER_BLEND_MODES

    data = {
        "palettes": {k: {"description": v["description"], "roles": list(v["colors"])} for k, v in palettes().items()},
        "styles": {k: v["description"] for k, v in styles().items()},
        "mouths": {k: v["description"] for k, v in mouths().items()},
        "easings": list(me.EASINGS),
        "brushes": ["gpen", "ink", "pencil", "charcoal", "marker", "drybrush", "spray", "airbrush"],
        "blends": LAYER_BLEND_MODES,
    }
    _out(data if kind == "all" else data.get(kind, {"error": f"unknown kind {kind}"}))


@app.command()
def schema():
    """Print the Scene JSON Schema."""
    from .schema import json_schema

    _out(json_schema())


@app.command()
def mcp():
    """Run the MCP stdio server (for Claude Desktop, Kiro, Cursor, ...)."""
    from .mcp_server import main

    main()


@app.command()
def demo(
    out: Path = typer.Option(Path("celcraft_demo"), help="Project directory for the demo."),
    scale: float = typer.Option(1.0, help="Render scale (0.5 for a quick run)."),
):
    """Build, render, review and export the 5-second sakuga jump showcase."""
    import runpy

    from .presets import data_file

    try:
        script = data_file("examples/demo_sakuga_jump.py")
    except FileNotFoundError:
        _fail("demo script examples/demo_sakuga_jump.py not found")
    sys.argv = [str(script), "--out", str(out), "--scale", str(scale)]
    runpy.run_path(str(script), run_name="__main__")


def main():
    app()


if __name__ == "__main__":
    main()
