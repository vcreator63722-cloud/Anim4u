"""Project workspace, project bible and X-Sheet (exposure sheet).

Layout created by ``celcraft init``::

    my_short/
    ├── project_bible.json   # title, format, style, palette, characters, rules
    ├── scenes/<scene>.json  # Scene JSON (schema.Scene)
    ├── audio/               # dialogue, sfx, music, mixes
    ├── assets/              # reference images, textures
    ├── renders/<scene>/     # frame_00000.png ... + manifest.json (render cache)
    ├── review/<scene>/      # onion skins, contact sheets, QA reports
    └── exports/             # mp4 / webm / mov / gif deliveries
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import Field

from .schema import Frame, Scene, StrictModel

BIBLE = "project_bible.json"
SUBDIRS = ("scenes", "audio", "assets", "renders", "review", "exports")


class CharacterSheet(StrictModel):
    name: str
    description: str = ""
    palette_roles: dict[str, str] = Field(default_factory=dict, description="role -> colour overrides for this character")
    rig: Optional[str] = Field(default=None, description="rig preset, e.g. 'biped'")
    proportions: str = ""
    turnaround_notes: str = ""
    expressions: list[str] = Field(default_factory=list)


class Location(StrictModel):
    name: str
    description: str = ""
    palette_roles: dict[str, str] = Field(default_factory=dict)


class ProjectBible(StrictModel):
    title: str
    logline: str = ""
    width: int = Field(1920, ge=16, le=8192)
    height: int = Field(1080, ge=16, le=8192)
    fps: float = Field(24, gt=0, le=120)
    style: Optional[str] = "anime_cel"
    palette: Optional[str] = None
    characters: list[CharacterSheet] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=lambda: [
        "Line art on its own layer (kind='line'), above colour.",
        "Cel shadows/rims are clipped to their colour layer (clip_to).",
        "Keys first, then breakdowns on arcs, then inbetweens with eased spacing.",
        "Fast action on ones, dialogue/holds on twos.",
        "Review every shot with onion skin + contact sheet before export.",
    ])
    created: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    updated: Optional[str] = None


class Project:
    def __init__(self, root: Union[str, Path]):
        self.root = Path(root).resolve()
        if not (self.root / BIBLE).is_file():
            raise FileNotFoundError(f"{self.root} is not a CelCraft project (missing {BIBLE}); run `celcraft init`")
        self.bible = ProjectBible.model_validate_json((self.root / BIBLE).read_text())

    # -- creation / discovery -------------------------------------------------
    @classmethod
    def init(cls, root: Union[str, Path], title: Optional[str] = None, *, exist_ok: bool = True, **bible: Any) -> "Project":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS:
            (root / d).mkdir(exist_ok=True)
        bp = root / BIBLE
        if bp.exists() and not exist_ok:
            raise FileExistsError(bp)
        if not bp.exists():
            b = ProjectBible(title=title or root.name, **bible)
            bp.write_text(b.model_dump_json(indent=2))
        return cls(root)

    @classmethod
    def find(cls, start: Union[str, Path] = ".") -> "Project":
        p = Path(start).resolve()
        for cand in (p, *p.parents):
            if (cand / BIBLE).is_file():
                return cls(cand)
        raise FileNotFoundError(f"no {BIBLE} found in {p} or its parents")

    def save_bible(self) -> None:
        self.bible.updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (self.root / BIBLE).write_text(self.bible.model_dump_json(indent=2))

    # -- paths ----------------------------------------------------------------
    def scene_path(self, name: str) -> Path:
        return self.root / "scenes" / f"{name}.json"

    def render_dir(self, name: str, preview: bool = False) -> Path:
        return self.root / "renders" / (f"{name}__preview" if preview else name)

    def review_dir(self, name: str) -> Path:
        d = self.root / "review" / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def export_path(self, name: str, ext: str) -> Path:
        return self.root / "exports" / f"{name}{ext}"

    # -- scenes -----------------------------------------------------------------
    def list_scenes(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "scenes").glob("*.json"))

    def new_scene(self, name: str, *, overwrite: bool = False, **overrides: Any) -> Scene:
        path = self.scene_path(name)
        if path.exists() and not overwrite:
            raise FileExistsError(f"scene {name!r} already exists")
        b = self.bible
        data = {"name": name, "width": b.width, "height": b.height, "fps": b.fps, "style": b.style, "palette": b.palette}
        data.update(overrides)
        scene = Scene.model_validate(data)
        self.save_scene(scene)
        return scene

    def load_scene(self, name: str) -> Scene:
        path = self.scene_path(name)
        if not path.is_file():
            raise FileNotFoundError(f"scene {name!r} not found (have: {', '.join(self.list_scenes()) or 'none'})")
        return Scene.model_validate_json(path.read_text())

    def save_scene(self, scene: Scene) -> Path:
        path = self.scene_path(scene.name)
        path.write_text(scene.model_dump_json(indent=1, exclude_none=True))
        return path

    # -- frame editing (used by CLI/MCP) ----------------------------------------
    def add_frames(self, name: str, frames: list[dict], index: Optional[int] = None) -> Scene:
        scene = self.load_scene(name)
        new = [Frame.model_validate(f) for f in frames]
        if index is None:
            scene.frames.extend(new)
        else:
            scene.frames[index:index] = new
        self.save_scene(scene)
        return scene

    def replace_frame(self, name: str, index: int, frame: dict) -> Scene:
        scene = self.load_scene(name)
        scene.frames[index] = Frame.model_validate(frame)
        self.save_scene(scene)
        return scene

    def delete_frames(self, name: str, start: int, end: Optional[int] = None) -> Scene:
        scene = self.load_scene(name)
        del scene.frames[start: (end if end is not None else start + 1)]
        self.save_scene(scene)
        return scene

    def info(self) -> dict:
        scenes = []
        for n in self.list_scenes():
            try:
                s = self.load_scene(n)
                scenes.append({"name": n, "drawings": len(s.frames), "frames": s.duration_frames, "seconds": round(s.duration_seconds, 2),
                               "fps": s.fps, "size": f"{s.width}x{s.height}", "style": s.style})
            except Exception as e:  # noqa: BLE001
                scenes.append({"name": n, "error": str(e)})
        return {"root": str(self.root), "bible": self.bible.model_dump(), "scenes": scenes}


# ---------------------------------------------------------------------------
# X-Sheet
# ---------------------------------------------------------------------------

def xsheet(scene: Scene) -> list[dict]:
    """Exposure sheet rows, one per timeline frame.

    Layer columns follow the traditional convention: the drawing number is
    written where a new drawing starts on that layer and ``|`` marks a hold.
    A layer whose content is identical to the previous drawing is treated as
    held too (e.g. a background held for the whole shot).
    """
    from .schema import camera_at

    rows: list[dict] = []
    tl = scene.timeline()
    layer_ids: list[str] = []
    for f in scene.frames:
        for l in f.layers:
            if l.id not in layer_ids:
                layer_ids.append(l.id)
    last_sig: dict[str, Optional[str]] = {lid: None for lid in layer_ids}
    layer_dwg: dict[str, int] = {lid: 0 for lid in layer_ids}
    sfx_at: dict[int, list[str]] = {}
    for t in scene.audio:
        sfx_at.setdefault(int(round(t.start * scene.fps)), []).append(f"{t.kind}:{Path(t.path).name}")
    markers = {m.frame: m.label for m in scene.markers}
    prev_d = -1
    for tf, d in enumerate(tl):
        fr = scene.frames[d]
        row: dict[str, Any] = {"frame": tf + 1, "drawing": d, "key": fr.key if d != prev_d else "|"}
        layers = {l.id: l for l in fr.layers}
        for lid in layer_ids:
            if lid not in layers:
                row[lid] = ""
                last_sig[lid] = None
                continue
            sig = layers[lid].model_dump_json()
            if d != prev_d and sig != last_sig[lid]:
                layer_dwg[lid] += 1
                row[lid] = str(layer_dwg[lid])
                last_sig[lid] = sig
            else:
                row[lid] = "|"
        row["phoneme"] = fr.phoneme or ""
        row["dialogue"] = (fr.dialogue or "") if d != prev_d else ""
        row["sfx"] = ", ".join(sfx_at.get(tf, []))
        cam = camera_at(scene.camera_keys, tf) if scene.camera_keys else (fr.camera.model_dump(exclude_none=True) if fr.camera else {})
        row["camera"] = " ".join(f"{k}={float(v):.1f}" for k, v in cam.items() if k in ("x", "y", "zoom", "rotation"))
        row["notes"] = " / ".join(x for x in [(fr.notes or "") if d != prev_d else "", markers.get(tf, "")] if x)
        rows.append(row)
        prev_d = d
    return rows


def xsheet_csv(scene: Scene) -> str:
    rows = xsheet(scene)
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def xsheet_text(scene: Scene, max_rows: Optional[int] = None) -> str:
    """Fixed-width X-sheet for chat / terminal review."""
    rows = xsheet(scene)[: max_rows or None]
    if not rows:
        return "(empty)"
    cols = list(rows[0].keys())
    width = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    width = {c: min(w, 28) for c, w in width.items()}
    line = " | ".join(c.ljust(width[c]) for c in cols)
    out = [line, "-" * len(line)]
    for r in rows:
        out.append(" | ".join(str(r[c])[: width[c]].ljust(width[c]) for c in cols))
    return "\n".join(out)


def save_json(obj: Any, path: Union[str, Path]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))
    return path
