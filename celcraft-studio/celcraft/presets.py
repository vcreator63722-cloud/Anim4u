"""Loaders for bundled presets (palettes, styles, mouth chart)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def _presets_dir() -> Path:
    # Installed wheel ships presets inside the package; editable/dev installs use the repo folder.
    for candidate in (PACKAGE_ROOT / "_data" / "presets", PROJECT_ROOT / "presets"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("celcraft presets directory not found")


def renderer_dir() -> Path:
    for candidate in (PACKAGE_ROOT / "_data" / "renderer", PROJECT_ROOT / "renderer"):
        if (candidate / "engine.html").is_file():
            return candidate
    raise FileNotFoundError("celcraft renderer directory (engine.html) not found")


def data_file(relative: str) -> Path:
    """Locate a bundled file (SKILL.md, examples/...) in a wheel install or a source checkout."""
    for base in (PACKAGE_ROOT / "_data", PROJECT_ROOT):
        cand = base / relative
        if cand.exists():
            return cand
    raise FileNotFoundError(relative)


@lru_cache(maxsize=None)
def _load(name: str) -> dict[str, Any]:
    with open(_presets_dir() / name, encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def palettes() -> dict[str, dict]:
    return _load("palettes.json")


def palette(name: str | None) -> dict[str, str]:
    if not name:
        return {}
    all_p = palettes()
    if name not in all_p:
        raise KeyError(f"unknown palette {name!r}; available: {', '.join(all_p)}")
    return dict(all_p[name]["colors"])


def styles() -> dict[str, dict]:
    return _load("styles.json")


def style(name: str | None) -> dict:
    if not name:
        return {}
    all_s = styles()
    if name not in all_s:
        raise KeyError(f"unknown style {name!r}; available: {', '.join(all_s)}")
    return all_s[name]


def mouths() -> dict[str, dict]:
    return _load("mouths_preston_blair.json")["shapes"]


def resolve_scene_look(scene) -> tuple[dict[str, str], dict]:
    """Return (palette colours, style post dict) for a scene."""
    st = style(scene.style) if scene.style else {}
    pal_name = scene.palette or st.get("palette")
    return palette(pal_name), dict(st.get("post", {}))
