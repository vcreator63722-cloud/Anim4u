import numpy as np
import pytest

from celcraft.renderer_bridge import render_payloads, render_scene
from celcraft.rig_engine import Biped, BipedPose, mouth_elements
from celcraft.schema import Scene

from conftest import png_to_array

pytestmark = pytest.mark.usefixtures("needs_renderer")


def payload(layers, **kw):
    p = {"width": 120, "height": 80, "scale": 1, "frame": 0, "background": "#ffffff", "camera": {}, "layers": layers, "post": {}}
    p.update(kw)
    return p


def test_stroke_draws_dark_line_on_white():
    png = render_payloads([payload([{"id": "l", "elements": [{"type": "stroke", "points": [[10, 40], [110, 40]], "size": 8, "color": "#000000", "taper_start": 0, "taper_end": 0}]}])])[0]
    a = png_to_array(png)
    assert a.shape == (80, 120, 4)
    assert a[40, 60, :3].max() < 40  # centre of the stroke is ink
    assert (a[5, 5, :3] == 255).all()  # background untouched


def test_transparent_background_keeps_alpha():
    a = png_to_array(render_payloads([payload([], background=None)])[0])
    assert a[..., 3].max() == 0


def test_clip_to_masks_shadow_layer():
    layers = [
        {"id": "color", "elements": [{"type": "rect", "x": 20, "y": 20, "w": 40, "h": 40, "fill": "#ff0000"}]},
        {"id": "shade", "clip_to": "color", "blend": "multiply", "elements": [{"type": "rect", "x": 0, "y": 0, "w": 120, "h": 80, "fill": "#0000ff"}]},
    ]
    a = png_to_array(render_payloads([payload(layers)])[0])
    assert tuple(a[40, 40, :3]) == (0, 0, 0)  # red * blue = black inside
    assert tuple(a[5, 100, :3]) == (255, 255, 255)  # shade clipped away outside


def test_blend_screen_and_layer_opacity():
    layers = [
        {"id": "a", "elements": [{"type": "rect", "x": 0, "y": 0, "w": 120, "h": 80, "fill": "#404040"}]},
        {"id": "b", "blend": "screen", "opacity": 1, "elements": [{"type": "rect", "x": 0, "y": 0, "w": 60, "h": 80, "fill": "#404040"}]},
    ]
    a = png_to_array(render_payloads([payload(layers)])[0])
    assert a[40, 30, 0] > a[40, 90, 0]  # screen brightens


def test_erase_inside_isolated_group_only():
    layers = [{"id": "l", "elements": [
        {"type": "rect", "x": 0, "y": 0, "w": 120, "h": 80, "fill": "#00ff00"},
        {"type": "group", "isolate": True, "elements": [
            {"type": "rect", "x": 10, "y": 10, "w": 50, "h": 50, "fill": "#ff0000"},
            {"type": "rect", "x": 10, "y": 10, "w": 25, "h": 50, "fill": "#000", "blend": "erase"},
        ]},
    ]}]
    a = png_to_array(render_payloads([payload(layers)])[0])
    assert tuple(a[30, 20, :3]) == (0, 255, 0)  # erased part of group shows layer below, not a hole
    assert tuple(a[30, 50, :3]) == (255, 0, 0)


def test_scale_and_determinism():
    p = payload([{"id": "l", "elements": [{"type": "stroke", "brush": "charcoal", "points": [[10, 10], [60, 70], [110, 10]], "size": 10, "seed": 3}]}],
                post={"grain": {"amount": 0.2}})
    a, b = render_payloads([p, p])
    assert a == b
    half = png_to_array(render_payloads([{**p, "scale": 0.5}])[0])
    assert half.shape[:2] == (40, 60)


@pytest.mark.parametrize("brush", ["gpen", "ink", "pencil", "charcoal", "marker", "drybrush", "spray", "airbrush"])
def test_every_brush_paints(brush):
    p = payload([{"id": "l", "elements": [{"type": "stroke", "brush": brush, "points": [[10, 40], [60, 30], [110, 40]], "size": 14, "color": "#000"}]}])
    a = png_to_array(render_payloads([p])[0])
    assert (a[..., :3].mean(axis=-1) < 200).sum() > 30


def test_vfx_and_post_chain_render():
    layers = [{"id": "fx", "elements": [
        {"type": "speed_lines", "mode": "radial", "color": "#000", "count": 40},
        {"type": "speed_lines", "mode": "parallel", "angle": 20, "color": "#000", "count": 20},
        {"type": "smear", "points": [[10, 40], [50, 40], [90, 40]], "radii": [3, 8, 12], "color": "#f00"},
        {"type": "halftone", "x": 0, "y": 60, "w": 120, "h": 20, "color": "#00f"},
        {"type": "sparkle", "points": [[100, 15, 1]], "color": "#0f0"},
    ]}]
    post = {"grain": {"amount": 0.1}, "vignette": {"amount": 0.4}, "bloom": {"strength": 0.3}, "chromatic_aberration": {"px": 2},
            "grade": {"contrast": 1.1, "saturation": 1.2, "tint": "#ffaa00", "tint_amount": 0.1}, "paper": {"amount": 0.2}, "letterbox": {"ratio": 2.39}}
    a = png_to_array(render_payloads([payload(layers, post=post)])[0])
    assert a[0, 60, :3].max() < 10  # letterbox bar
    mono = png_to_array(render_payloads([payload(layers, post={"impact": {"mode": "mono", "color_a": "#ffffff", "color_b": "#000000"}})])[0])
    assert set(np.unique(mono[..., 0])) <= {0, 255}


def test_rig_character_and_mouths_render():
    b = Biped()
    els = b.build(BipedPose(x=60, y=55, scale=0.25, mouth="D"))
    for shape in "XABCDEFGH":
        els.append(mouth_elements(shape, 10 + "XABCDEFGH".index(shape) * 12, 75, 5))
    from celcraft.presets import palette
    from celcraft.schema import resolve_palette_refs

    p = resolve_palette_refs(payload([{"id": "c", "elements": els}]), palette("anime_cel"))
    a = png_to_array(render_payloads([p])[0])
    assert (a[..., :3].mean(axis=-1) < 128).sum() > 100


def test_render_scene_cache_and_hold_reuse(tmp_path):
    scene = Scene.model_validate({"name": "s", "width": 64, "height": 48, "frames": [
        {"hold": 3, "layers": [{"id": "l", "elements": [{"type": "ellipse", "cx": 32, "cy": 24, "rx": 10, "fill": "#123456"}]}]},
        {"hold": 1, "layers": [{"id": "l", "elements": [{"type": "ellipse", "cx": 40, "cy": 24, "rx": 10, "fill": "#123456"}]}]},
    ]})
    r1 = render_scene(scene, tmp_path)
    assert len(r1.frames) == 4 and all(f.exists() for f in r1.frames)
    assert r1.rendered == 2 and r1.reused == 2  # held frames are linked, not re-rendered
    r2 = render_scene(scene, tmp_path)
    assert r2.rendered == 0
    assert (tmp_path / "manifest.json").exists()
