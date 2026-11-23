"""Strict Pydantic v2 data model for CelCraft scenes.

Hierarchy::

    Scene
      ├── frames: [Frame]            (one Frame = one *drawing*; ``hold`` = exposure)
      │     ├── camera: Camera
      │     ├── post: PostFX          (overrides scene-level post)
      │     └── layers: [Layer]       (bottom -> top)
      │           └── elements: [Element]  (stroke, path, ellipse, rect, polygon,
      │                                     text, image, group, speed_lines,
      │                                     smear, halftone, sparkle)
      ├── audio: [AudioTrack]
      └── markers: [Marker]

Colours may be any CSS colour string, or a palette reference such as
``"@skin_shadow"`` which is resolved against the scene palette at render time.

All models use ``extra="forbid"`` so typos in agent-written JSON fail loudly
instead of being silently ignored.
"""

from __future__ import annotations

import copy
import re
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

_COLOR_RE = re.compile(
    r"^(#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
    r"|(rgb|rgba|hsl|hsla)\([^)]*\)"
    r"|@[A-Za-z0-9_.\-]+"
    r"|[a-zA-Z]+)$"
)


def _check_color(v: str) -> str:
    if not isinstance(v, str) or not _COLOR_RE.match(v.strip()):
        raise ValueError(f"invalid colour {v!r}; use '#rrggbb', 'rgba(...)', a CSS name or '@palette_role'")
    return v.strip()


Color = Annotated[str, Field(description="CSS colour or '@palette_role'")]
Point = Annotated[list[float], Field(min_length=2, max_length=3, description="[x, y] or [x, y, pressure 0..1]")]

BlendMode = Literal[
    "normal", "multiply", "screen", "overlay", "add", "lighter", "linear-dodge",
    "color-dodge", "color-burn", "darken", "lighten", "hard-light", "soft-light",
    "difference", "exclusion", "hue", "saturation", "color", "luminosity",
    # in-layer operators
    "source-atop", "alpha-lock", "erase", "destination-out", "destination-in",
    "destination-over", "behind",
]
LAYER_BLEND_MODES = [
    "normal", "multiply", "screen", "overlay", "add", "lighter", "linear-dodge", "color-dodge",
    "color-burn", "darken", "lighten", "hard-light", "soft-light", "difference", "exclusion",
    "hue", "saturation", "color", "luminosity",
]
BrushName = Literal["gpen", "ink", "pencil", "charcoal", "marker", "drybrush", "spray", "airbrush"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)


class GradientStopsMixin(StrictModel):
    stops: list[tuple[float, Color]] = Field(min_length=2)

    @field_validator("stops")
    @classmethod
    def _stops(cls, v):
        for off, col in v:
            if not 0.0 <= off <= 1.0:
                raise ValueError("gradient stop offsets must be within 0..1")
            _check_color(col)
        return v


class LinearGradient(GradientStopsMixin):
    type: Literal["linear"] = "linear"
    x0: float
    y0: float
    x1: float
    y1: float


class RadialGradient(GradientStopsMixin):
    type: Literal["radial"] = "radial"
    cx: float
    cy: float
    r: float = Field(gt=0)
    fx: Optional[float] = None
    fy: Optional[float] = None
    fr: Optional[float] = Field(default=None, ge=0)


Gradient = Annotated[Union[LinearGradient, RadialGradient], Field(discriminator="type")]
Paint = Union[Color, Gradient]


def _check_paint(v):
    if isinstance(v, str):
        return _check_color(v)
    return v


class TransformSpec(StrictModel):
    """Affine transform in object form (applied about the pivot)."""

    x: float = 0.0
    y: float = 0.0
    rotation: float = Field(0.0, description="degrees, clockwise")
    scale: Optional[float] = None
    scale_x: Optional[float] = None
    scale_y: Optional[float] = None
    skew_x: float = 0.0
    pivot_x: float = 0.0
    pivot_y: float = 0.0


Transform = Union[Annotated[list[float], Field(min_length=6, max_length=6)], TransformSpec]


# ---------------------------------------------------------------------------
# Elements
# ---------------------------------------------------------------------------

class ElementBase(StrictModel):
    id: Optional[str] = None
    opacity: float = Field(1.0, ge=0.0, le=1.0)
    blend: BlendMode = "normal"
    transform: Optional[Transform] = None
    visible: bool = True
    blur: Optional[float] = Field(default=None, ge=0, description="element gaussian blur radius (px)")


class StrokeElement(ElementBase):
    """Pressure-sensitive brush stroke (perfect-freehand outline)."""

    type: Literal["stroke"] = "stroke"
    points: list[Point] = Field(min_length=1)
    brush: BrushName = "gpen"
    size: float = Field(6.0, ge=0, le=2000)
    color: Paint = "#1a1420"
    thinning: Optional[float] = Field(default=None, ge=-1, le=1)
    smoothing: Optional[float] = Field(default=None, ge=0, le=1)
    streamline: Optional[float] = Field(default=None, ge=0, le=1)
    taper_start: Optional[float] = Field(default=None, ge=0, le=1, description="fraction of stroke length")
    taper_end: Optional[float] = Field(default=None, ge=0, le=1, description="fraction of stroke length")
    cap: Literal["round", "butt"] = "round"
    closed: bool = False
    fill: Optional[Paint] = Field(default=None, description="fill the closed contour under the line")
    fill_opacity: float = Field(1.0, ge=0, le=1)
    tension: float = Field(0.5, ge=0, le=1)
    simulate_pressure: Optional[bool] = None
    smooth_input: bool = True
    seed: Optional[int] = None
    density: Optional[float] = Field(default=None, ge=0, description="spray density")
    dot_size: Optional[float] = Field(default=None, gt=0, description="spray dot size")
    bristles: Optional[int] = Field(default=None, ge=2, le=200, description="dry brush bristle count")

    @field_validator("color", "fill")
    @classmethod
    def _paint(cls, v):
        return _check_paint(v) if v is not None else v


class _ShapeBase(ElementBase):
    fill: Optional[Paint] = None
    stroke: Optional[Paint] = None
    stroke_width: float = Field(1.0, ge=0)
    line_join: Literal["round", "miter", "bevel"] = "round"
    line_cap: Literal["round", "butt", "square"] = "round"
    dash: Optional[list[float]] = None

    @field_validator("fill", "stroke")
    @classmethod
    def _paint(cls, v):
        return _check_paint(v) if v is not None else v


class PathElement(_ShapeBase):
    """Raw SVG path data (``d`` attribute)."""

    type: Literal["path"] = "path"
    d: str = Field(min_length=1)
    fill_rule: Literal["nonzero", "evenodd"] = "nonzero"


class EllipseElement(_ShapeBase):
    type: Literal["ellipse"] = "ellipse"
    cx: float
    cy: float
    rx: float = Field(ge=0)
    ry: Optional[float] = Field(default=None, ge=0)
    rotation: float = 0.0


class RectElement(_ShapeBase):
    type: Literal["rect"] = "rect"
    x: float
    y: float
    w: float = Field(ge=0)
    h: float = Field(ge=0)
    radius: float = Field(0.0, ge=0)


class PolygonElement(_ShapeBase):
    """Polygon / fill-tool region. ``smooth=True`` passes a Catmull-Rom spline through the points."""

    type: Literal["polygon"] = "polygon"
    points: list[Point] = Field(min_length=2)
    smooth: bool = False
    closed: bool = True
    tension: float = Field(0.5, ge=0, le=1)


class TextElement(ElementBase):
    type: Literal["text"] = "text"
    text: str
    x: float
    y: float
    size: float = Field(32, gt=0)
    font: Optional[str] = None
    weight: str = "bold"
    align: Literal["left", "center", "right", "start", "end"] = "left"
    baseline: Literal["alphabetic", "top", "middle", "bottom", "hanging", "ideographic"] = "alphabetic"
    fill: Paint = "#000000"
    stroke: Optional[Paint] = None
    stroke_width: float = 4.0


class ImageElement(ElementBase):
    """Bitmap reference: local file path (inlined by the bridge) or data: URL."""

    type: Literal["image"] = "image"
    src: str
    x: float = 0
    y: float = 0
    w: Optional[float] = Field(default=None, gt=0)
    h: Optional[float] = Field(default=None, gt=0)


class SpeedLinesElement(ElementBase):
    type: Literal["speed_lines"] = "speed_lines"
    mode: Literal["radial", "parallel"] = "radial"
    cx: Optional[float] = None
    cy: Optional[float] = None
    inner_radius: Optional[float] = Field(default=None, ge=0)
    outer_radius: Optional[float] = Field(default=None, ge=0)
    angle: float = 0.0
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    length: Optional[float] = None
    min_length: Optional[float] = None
    count: int = Field(80, ge=1, le=2000)
    width: float = Field(6.0, gt=0)
    jitter: float = Field(0.6, ge=0)
    color: Paint = "#ffffff"
    seed: int = 1
    animate: bool = True


class SmearElement(ElementBase):
    """Swept multi-circle smear (use on the fastest frames of an action)."""

    type: Literal["smear"] = "smear"
    points: list[Point] = Field(min_length=1)
    radii: Optional[list[float]] = None
    radius: float = 20.0
    color: Paint = "#000000"


class HalftoneElement(ElementBase):
    type: Literal["halftone"] = "halftone"
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    spacing: float = Field(10, gt=1)
    max_radius: Optional[float] = None
    min_radius: float = 0.0
    angle: float = 45.0
    x0: Optional[float] = None
    y0: Optional[float] = None
    x1: Optional[float] = None
    y1: Optional[float] = None
    color: Paint = "#000000"


class SparkleElement(ElementBase):
    type: Literal["sparkle"] = "sparkle"
    points: list[Point] = Field(min_length=1, description="[x, y, scale]")
    size: float = 16
    color: Paint = "#ffffff"


class GroupElement(ElementBase):
    """Nested elements sharing a transform (used by the rig engine)."""

    type: Literal["group"] = "group"
    isolate: bool = Field(False, description="render children into a private sub-layer before blending (needed for in-group erase/alpha-lock)")
    elements: list["Element"] = Field(default_factory=list)


Element = Annotated[
    Union[
        StrokeElement, PathElement, EllipseElement, RectElement, PolygonElement, TextElement,
        ImageElement, SpeedLinesElement, SmearElement, HalftoneElement, SparkleElement, GroupElement,
    ],
    Field(discriminator="type"),
]
GroupElement.model_rebuild()


# ---------------------------------------------------------------------------
# Layers, camera, post
# ---------------------------------------------------------------------------

class MotionBlur(StrictModel):
    angle: float = 0.0
    distance: float = Field(0.0, ge=0)
    samples: Optional[int] = Field(default=None, ge=3, le=48)


class Glow(StrictModel):
    radius: float = Field(12, gt=0)
    strength: float = Field(1.0, ge=0, le=4)
    color: Optional[Color] = None


class LayerFilters(StrictModel):
    blur: Optional[float] = Field(default=None, ge=0)
    motion_blur: Optional[MotionBlur] = None
    glow: Optional[Glow] = None
    css: Optional[str] = Field(default=None, description="raw CSS filter, e.g. 'saturate(1.2)'")


LayerKind = Literal["background", "bg", "color", "paint", "shade", "highlight", "rim", "line", "fx", "overlay", "guide", "other"]


class Layer(StrictModel):
    id: str = Field(min_length=1)
    name: Optional[str] = None
    kind: LayerKind = "other"
    blend: Literal[tuple(LAYER_BLEND_MODES)] = "normal"  # type: ignore[valid-type]
    opacity: float = Field(1.0, ge=0, le=1)
    visible: bool = True
    clip_to: Optional[str] = Field(default=None, description="alpha-clip this layer to a layer below (cel shading)")
    parallax: float = Field(1.0, ge=-4, le=4, description="multi-plane depth factor, 0=screen-locked, 1=character plane")
    screen_space: bool = False
    transform: Optional[Transform] = None
    filters: Optional[LayerFilters] = None
    elements: list[Element] = Field(default_factory=list)


class Shake(StrictModel):
    amplitude: float = Field(0.0, ge=0)
    frequency: float = Field(0.35, gt=0)
    rotation: float = 0.0
    seed: int = 7
    decay: Optional[float] = Field(default=None, ge=0)
    start_frame: Optional[int] = None


class Camera(StrictModel):
    x: Optional[float] = None
    y: Optional[float] = None
    zoom: float = Field(1.0, gt=0, le=50)
    rotation: float = 0.0
    shake: Optional[Shake] = None


class CameraKey(StrictModel):
    """Scene-level camera key on the *timeline* (camera moves on ones even when drawings are on twos)."""

    frame: int = Field(ge=0)
    x: Optional[float] = None
    y: Optional[float] = None
    zoom: Optional[float] = Field(default=None, gt=0, le=50)
    rotation: Optional[float] = None
    ease: str = Field("ease_in_out", description="easing leaving this key (motion_engine.EASINGS or cubic_bezier(...))")
    shake: Optional[Shake] = None


class Grain(StrictModel):
    amount: float = Field(0.0, ge=0, le=1)
    size: float = Field(1.0, gt=0)
    monochrome: bool = True
    animate: bool = True
    seed: int = 3


class Paper(StrictModel):
    amount: float = Field(0.0, ge=0, le=1)
    scale: float = Field(1.0, gt=0)


class Vignette(StrictModel):
    amount: float = Field(0.0, ge=0, le=1)
    inner: float = Field(0.45, ge=0, le=1)
    color: Optional[Color] = None


class Bloom(StrictModel):
    strength: float = Field(0.0, ge=0, le=1)
    radius: float = Field(18, gt=0)
    threshold: float = Field(0.7, ge=0, le=1)


class ChromaticAberration(StrictModel):
    px: float = Field(0.0, ge=0, le=40)


class Grade(StrictModel):
    brightness: Optional[float] = Field(default=None, ge=0)
    contrast: Optional[float] = Field(default=None, ge=0)
    saturation: Optional[float] = Field(default=None, ge=0)
    hue_rotate: Optional[float] = None
    tint: Optional[Color] = None
    tint_amount: float = Field(0.0, ge=0, le=1)
    tint_blend: Optional[str] = None


class Impact(StrictModel):
    mode: Literal["mono", "invert", "flash"] = "mono"
    color_a: Color = "#ffffff"
    color_b: Color = "#0a0a12"
    threshold: float = Field(0.5, ge=0, le=1)
    invert: bool = True
    strength: float = Field(0.85, ge=0, le=1)


class Letterbox(StrictModel):
    ratio: float = Field(2.39, gt=0)
    color: Color = "#000000"


class PostFX(StrictModel):
    grain: Optional[Grain] = None
    paper: Optional[Paper] = None
    vignette: Optional[Vignette] = None
    bloom: Optional[Bloom] = None
    chromatic_aberration: Optional[ChromaticAberration] = None
    grade: Optional[Grade] = None
    impact: Optional[Impact] = None
    letterbox: Optional[Letterbox] = None


# ---------------------------------------------------------------------------
# Frames, audio, scene
# ---------------------------------------------------------------------------

class Frame(StrictModel):
    """One drawing. ``hold`` is the exposure (1 = on ones, 2 = on twos, ...)."""

    hold: int = Field(1, ge=1, le=240)
    key: Literal["key", "breakdown", "inbetween", "hold", "extreme", "smear", "impact"] = "inbetween"
    layers: list[Layer] = Field(default_factory=list)
    camera: Optional[Camera] = None
    post: Optional[PostFX] = None
    background: Optional[Paint] = None
    phoneme: Optional[Literal["X", "A", "B", "C", "D", "E", "F", "G", "H"]] = None
    dialogue: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _layers_valid(self):
        seen: list[str] = []
        for layer in self.layers:
            if layer.id in seen:
                raise ValueError(f"duplicate layer id {layer.id!r}")
            if layer.clip_to is not None and layer.clip_to not in seen:
                raise ValueError(f"layer {layer.id!r} clip_to={layer.clip_to!r} must reference a layer *below* it")
            seen.append(layer.id)
        return self


class AudioTrack(StrictModel):
    path: str
    kind: Literal["dialogue", "sfx", "music", "ambience"] = "sfx"
    start: float = Field(0.0, ge=0, description="seconds on the scene timeline")
    gain_db: float = Field(0.0, ge=-60, le=24)
    fade_in: float = Field(0.0, ge=0)
    fade_out: float = Field(0.0, ge=0)
    pan: float = Field(0.0, ge=-1, le=1)
    trim_start: float = Field(0.0, ge=0)
    duration: Optional[float] = Field(default=None, gt=0)


class Marker(StrictModel):
    frame: int = Field(ge=0)
    label: str
    kind: Literal["beat", "cue", "dialogue", "sfx", "note"] = "note"


class Scene(StrictModel):
    version: Literal[1] = 1
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_\-]+$")
    width: int = Field(1920, ge=16, le=8192)
    height: int = Field(1080, ge=16, le=8192)
    fps: float = Field(24, gt=0, le=120)
    background: Optional[Paint] = "#ffffff"
    palette: Optional[str] = Field(default=None, description="palette preset name")
    palette_overrides: dict[str, Color] = Field(default_factory=dict)
    style: Optional[str] = Field(default=None, description="style preset name (post profile)")
    post: Optional[PostFX] = None
    frames: list[Frame] = Field(default_factory=list)
    camera_keys: list[CameraKey] = Field(default_factory=list)
    audio: list[AudioTrack] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    # -- timeline -----------------------------------------------------------
    def timeline(self) -> list[int]:
        """Map every timeline frame to the drawing (``frames`` index) exposed on it."""
        out: list[int] = []
        for i, f in enumerate(self.frames):
            out.extend([i] * f.hold)
        return out

    @property
    def duration_frames(self) -> int:
        return sum(f.hold for f in self.frames)

    @property
    def duration_seconds(self) -> float:
        return self.duration_frames / self.fps

    def timeline_start(self, drawing_index: int) -> int:
        return sum(f.hold for f in self.frames[:drawing_index])


# ---------------------------------------------------------------------------
# Payload building (Scene -> renderer JSON)
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve_palette_refs(obj: Any, palette: dict[str, str]) -> Any:
    """Replace every ``"@role"`` string with the palette colour (recursively)."""
    if isinstance(obj, str):
        if obj.startswith("@"):
            key = obj[1:]
            if key not in palette:
                raise KeyError(f"palette role {obj!r} not defined (available: {', '.join(sorted(palette))})")
            return palette[key]
        return obj
    if isinstance(obj, list):
        return [resolve_palette_refs(v, palette) for v in obj]
    if isinstance(obj, tuple):
        return [resolve_palette_refs(v, palette) for v in obj]
    if isinstance(obj, dict):
        return {k: resolve_palette_refs(v, palette) for k, v in obj.items()}
    return obj


def build_frame_payload(
    scene: Scene,
    drawing_index: int,
    *,
    timeline_frame: Optional[int] = None,
    scale: float = 1.0,
    palette: Optional[dict[str, str]] = None,
    style_post: Optional[dict] = None,
) -> dict:
    """Resolve a drawing into the flat JSON consumed by ``CelCraft.renderFrame``."""
    frame = scene.frames[drawing_index]
    post: dict = {}
    if style_post:
        post = _deep_merge(post, style_post)
    if scene.post:
        post = _deep_merge(post, scene.post.model_dump(exclude_none=True))
    if frame.post:
        post = _deep_merge(post, frame.post.model_dump(exclude_none=True))
    tl_frame = scene.timeline_start(drawing_index) if timeline_frame is None else timeline_frame
    camera = frame.camera.model_dump(exclude_none=True) if frame.camera else {}
    if scene.camera_keys:
        camera = {**camera, **camera_at(scene.camera_keys, tl_frame)}
    payload = {
        "width": scene.width,
        "height": scene.height,
        "scale": scale,
        "frame": tl_frame,
        "background": frame.background if frame.background is not None else scene.background,
        "camera": camera,
        "layers": [layer.model_dump(exclude_none=True) for layer in frame.layers],
        "post": post,
    }
    if isinstance(payload["background"], BaseModel):
        payload["background"] = payload["background"].model_dump(exclude_none=True)
    pal = dict(palette or {})
    pal.update(scene.palette_overrides)
    return resolve_palette_refs(payload, pal)


def camera_at(keys: list[CameraKey], frame: float) -> dict:
    """Interpolate a camera key track at a timeline frame (per-property tracks)."""
    from .motion_engine import Key, interpolate

    out: dict = {}
    ordered = sorted(keys, key=lambda k: k.frame)
    for prop in ("x", "y", "zoom", "rotation"):
        ks = [Key(k.frame, getattr(k, prop), k.ease) for k in ordered if getattr(k, prop) is not None]
        if ks:
            out[prop] = float(interpolate(ks, frame))
    shakes = [k for k in ordered if k.shake is not None and k.frame <= frame]
    if shakes:
        out["shake"] = shakes[-1].shake.model_dump(exclude_none=True)
    return out


def json_schema() -> dict:
    """JSON Schema for a Scene (handy for agents and MCP clients)."""
    return Scene.model_json_schema()
