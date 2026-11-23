"""FFmpeg mastering pipeline.

Formats
-------
mp4      H.264 High, yuv420p, CRF 16 default, +faststart, AAC 192k  (delivery / web)
webm     VP9 with alpha (yuva420p), Opus audio                    (transparent overlays)
prores   ProRes 4444 + alpha (.mov, yuva444p10le), PCM audio      (editorial / compositing)
gif      two-pass palettegen/paletteuse, Floyd-Steinberg dither    (previews, chat)
apng     animated PNG, lossless with alpha
png      numbered PNG sequence copy                                (hand-off to other tools)
animatic MP4 with a burned-in studio HUD: scene, frame/total, SMPTE timecode,
         key/breakdown tag, phoneme, dialogue, notes, safe-area guides, progress bar
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

from PIL import Image, ImageDraw

from ._ffmpeg import FFmpegError, encoders, ffmpeg_exe, run_ffmpeg

FORMATS = ("mp4", "webm", "prores", "gif", "apng", "png", "animatic")
EXT = {"mp4": ".mp4", "webm": ".webm", "prores": ".mov", "gif": ".gif", "apng": ".png", "png": "", "animatic": ".mp4"}


@dataclass
class ExportResult:
    path: Path
    format: str
    frames: int
    fps: float
    bytes: int
    has_audio: bool

    def as_dict(self) -> dict:
        return {"path": str(self.path), "format": self.format, "frames": self.frames, "fps": self.fps,
                "duration_s": round(self.frames / self.fps, 3), "bytes": self.bytes, "has_audio": self.has_audio}


def timecode(frame: int, fps: float) -> str:
    """SMPTE-style HH:MM:SS:FF (non-drop)."""
    ifps = max(1, int(round(fps)))
    ff = frame % ifps
    s = frame // ifps
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}:{ff:02d}"


def _collect_frames(source: Union[str, Path, Sequence[Union[str, Path]]]) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_dir():
            frames = sorted(p.glob("frame_*.png")) or sorted(p.glob("*.png"))
            if not frames:
                raise FileNotFoundError(f"no PNG frames in {p}")
            return frames
        return [p]
    return [Path(s) for s in source]


def _stage_sequence(frames: Sequence[Path], tmp: Path) -> str:
    """Link frames into a contiguous numbered sequence and return the ffmpeg pattern."""
    for i, f in enumerate(frames):
        dst = tmp / f"seq_{i:05d}.png"
        try:
            dst.symlink_to(f.resolve())
        except OSError:
            shutil.copyfile(f, dst)
    return str(tmp / "seq_%05d.png")


def _even_filter() -> str:
    return "pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:color=black@0"


def export_video(
    source: Union[str, Path, Sequence[Union[str, Path]]],
    out_path: Union[str, Path],
    *,
    fmt: Optional[str] = None,
    fps: float = 24,
    audio: Optional[Union[str, Path]] = None,
    crf: int = 16,
    preset: str = "slow",
    background: Optional[str] = None,
    gif_width: Optional[int] = 640,
    gif_fps: Optional[float] = None,
    loop: bool = True,
    hud: Optional[dict] = None,
) -> ExportResult:
    """Encode a rendered frame sequence. ``fmt`` is inferred from the extension if omitted.

    ``background``: flatten transparent frames over this colour (mp4/gif).
    ``hud``: for fmt="animatic" — dict(scene=..., labels=[...], phonemes=[...], dialogue=[...], notes=[...]).
    """
    out = Path(out_path)
    if fmt is None:
        ext = out.suffix.lower()
        fmt = {".mp4": "mp4", ".m4v": "mp4", ".webm": "webm", ".mov": "prores", ".gif": "gif", ".png": "apng", "": "png"}.get(ext)
        if fmt is None:
            raise ValueError(f"cannot infer format from {out.name!r}; pass fmt=one of {FORMATS}")
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; use one of {FORMATS}")
    frames = _collect_frames(source)
    out.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "png":
        out.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(frames):
            shutil.copyfile(f, out / f"frame_{i:05d}.png")
        total = sum(p.stat().st_size for p in out.glob("frame_*.png"))
        return ExportResult(out, fmt, len(frames), fps, total, False)

    with tempfile.TemporaryDirectory(prefix="celcraft_export_") as td:
        tmp = Path(td)
        if fmt == "animatic":
            frames = _burn_hud(frames, tmp / "hud", fps, hud or {})
        pattern = _stage_sequence(frames, tmp)
        args: list[str] = ["-framerate", f"{fps}", "-i", pattern]
        audio_idx = None
        if audio and fmt not in ("gif", "apng"):
            args += ["-i", str(audio)]
            audio_idx = 1
        duration = len(frames) / fps
        vf_flat = None
        if background and fmt in ("mp4", "gif", "animatic"):
            vf_flat = f"split[a][b];[a]drawbox=c={background}:t=fill[bg];[bg][b]overlay=format=auto"

        if fmt in ("mp4", "animatic"):
            if "libx264" not in encoders():
                raise FFmpegError("this ffmpeg build lacks libx264")
            vf = ",".join(x for x in [vf_flat, _even_filter(), "format=yuv420p"] if x)
            args += ["-vf", vf, "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
                     "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-r", f"{fps}"]
            if audio_idx is not None:
                args += ["-c:a", "aac", "-b:a", "192k", "-af", "apad", "-t", f"{duration:.4f}"]
        elif fmt == "webm":
            args += ["-vf", _even_filter() + ",format=yuva420p", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
                     "-crf", str(max(crf + 10, 20)), "-row-mt", "1", "-auto-alt-ref", "0", "-r", f"{fps}"]
            if audio_idx is not None:
                args += ["-c:a", "libopus", "-b:a", "160k", "-af", "apad", "-t", f"{duration:.4f}"]
        elif fmt == "prores":
            args += ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", "-vendor", "apl0", "-r", f"{fps}"]
            if audio_idx is not None:
                args += ["-c:a", "pcm_s16le", "-af", "apad", "-t", f"{duration:.4f}"]
        elif fmt == "gif":
            gfps = gif_fps or min(fps, 24)
            scale = f"scale={gif_width}:-1:flags=lanczos" if gif_width else "null"
            pre = f"{vf_flat}," if vf_flat else ""
            graph = (f"{pre}fps={gfps},{scale},split[s0][s1];[s0]palettegen=max_colors=255:stats_mode=diff:reserve_transparent=1[p];"
                     f"[s1][p]paletteuse=dither=floyd_steinberg:diff_mode=rectangle:alpha_threshold=128")
            args += ["-filter_complex", graph, "-loop", "0" if loop else "-1"]
        elif fmt == "apng":
            args += ["-c:v", "apng", "-plays", "0" if loop else "1", "-f", "apng"]
        args.append(str(out))
        run_ffmpeg(args)
    return ExportResult(out, fmt, len(frames), fps, out.stat().st_size, audio_idx is not None)


def export_scene(scene, frames_dir: Union[str, Path], out_path: Union[str, Path], *, fmt: Optional[str] = None,
                 audio: Optional[Union[str, Path]] = None, **kw) -> ExportResult:
    """Export a rendered scene; builds animatic HUD data from the scene automatically."""
    hud = None
    if (fmt or "") == "animatic":
        tl = scene.timeline()
        fr = scene.frames
        hud = {
            "scene": scene.name,
            "labels": [fr[d].key for d in tl],
            "phonemes": [fr[d].phoneme or "" for d in tl],
            "dialogue": [fr[d].dialogue or "" for d in tl],
            "notes": [fr[d].notes or "" for d in tl],
            "drawings": tl,
        }
    return export_video(frames_dir, out_path, fmt=fmt, fps=scene.fps, audio=audio, hud=hud, **kw)


# ---------------------------------------------------------------------------
# Animatic HUD
# ---------------------------------------------------------------------------

def _burn_hud(frames: Sequence[Path], out_dir: Path, fps: float, hud: dict) -> list[Path]:
    from .onion_skin import font

    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(frames)
    res: list[Path] = []
    for i, f in enumerate(frames):
        im = Image.open(f).convert("RGBA")
        W, H = im.size
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
        d = ImageDraw.Draw(im, "RGBA")
        s = max(1.0, W / 1280)
        bar = int(34 * s)
        fb = font(int(17 * s), bold=True)
        fs = font(int(14 * s))
        # safe areas (action 93%, title 90%)
        for pct, col in ((0.93, (0, 200, 255, 90)), (0.90, (255, 200, 0, 90))):
            mx, my = W * (1 - pct) / 2, H * (1 - pct) / 2
            d.rectangle([mx, my, W - mx, H - my], outline=col, width=max(1, int(s)))
        d.rectangle([0, 0, W, bar], fill=(12, 12, 18, 200))
        d.rectangle([0, H - bar, W, H], fill=(12, 12, 18, 200))
        draw_no = hud.get("drawings", [None] * n)[i] if hud.get("drawings") else None
        left = f"{hud.get('scene', 'scene')}   frame {i + 1}/{n}" + (f"   dwg {draw_no}" if draw_no is not None else "")
        d.text((10 * s, 8 * s), left, fill=(255, 255, 255, 255), font=fb)
        tc = timecode(i, fps)
        d.text((W - d.textlength(tc, font=fb) - 10 * s, 8 * s), tc, fill=(255, 220, 90, 255), font=fb)
        label = (hud.get("labels") or [""] * n)[i] if hud.get("labels") else ""
        if label:
            colr = {"key": (255, 80, 80), "extreme": (255, 80, 80), "breakdown": (80, 160, 255), "smear": (255, 160, 40), "impact": (255, 255, 255)}.get(label, (150, 150, 160))
            tw = d.textlength(label.upper(), font=fs)
            x0 = W / 2 - tw / 2 - 8 * s
            d.rounded_rectangle([x0, 6 * s, x0 + tw + 16 * s, bar - 6 * s], radius=int(5 * s), fill=colr + (230,))
            d.text((x0 + 8 * s, 9 * s), label.upper(), fill=(10, 10, 10, 255), font=fs)
        ph = (hud.get("phonemes") or [""] * n)[i] if hud.get("phonemes") else ""
        dia = (hud.get("dialogue") or [""] * n)[i] if hud.get("dialogue") else ""
        note = (hud.get("notes") or [""] * n)[i] if hud.get("notes") else ""
        bottom = "   ".join(x for x in [f"mouth {ph}" if ph else "", f'"{dia}"' if dia else "", note] if x)
        d.text((10 * s, H - bar + 8 * s), bottom, fill=(230, 230, 240, 255), font=fs)
        # progress bar
        d.rectangle([0, H - 4 * s, W * (i + 1) / n, H], fill=(255, 200, 40, 255))
        p = out_dir / f"hud_{i:05d}.png"
        im.convert("RGB").save(p, compress_level=1)
        res.append(p)
    return res


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

def probe(path: Union[str, Path]) -> dict:
    """Minimal media probe (duration, video codec/size/fps, audio codec) via `ffmpeg -i`."""
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    err = proc.stderr
    info: dict = {"path": str(path)}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    v = re.search(r"Stream #\S+.*Video: (\w+)[^\n]*?(\d{2,5})x(\d{2,5})", err)
    if v:
        info["video_codec"], info["width"], info["height"] = v.group(1), int(v.group(2)), int(v.group(3))
    f = re.search(r"([\d.]+) fps", err)
    if f:
        info["fps"] = float(f.group(1))
    a = re.search(r"Stream #\S+.*Audio: (\w+)", err)
    info["audio_codec"] = a.group(1) if a else None
    pix = re.search(r"Video: \w+[^,]*, (\w+)", err)
    if pix:
        info["pix_fmt"] = pix.group(1)
    return info


def write_report(result: ExportResult, path: Union[str, Path]) -> None:
    Path(path).write_text(json.dumps(result.as_dict(), indent=2))
