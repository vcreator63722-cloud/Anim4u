import pytest
from pydantic import ValidationError

from celcraft.schema import Scene, Layer, StrokeElement, build_frame_payload, camera_at, json_schema, resolve_palette_refs, CameraKey


def _scene(**kw):
    base = {
        "name": "t",
        "width": 200,
        "height": 100,
        "frames": [
            {"hold": 2, "key": "key", "layers": [
                {"id": "color", "elements": [{"type": "ellipse", "cx": 50, "cy": 50, "rx": 20, "fill": "@skin"}]},
                {"id": "shade", "clip_to": "color", "blend": "multiply", "elements": [{"type": "rect", "x": 50, "y": 0, "w": 100, "h": 100, "fill": "@skin_shade"}]},
                {"id": "line", "kind": "line", "elements": [{"type": "stroke", "points": [[10, 10], [90, 90, 0.8]], "color": "@line"}]},
            ]},
            {"hold": 1, "layers": []},
            {"hold": 3, "layers": []},
        ],
    }
    base.update(kw)
    return Scene.model_validate(base)


def test_timeline_and_holds():
    s = _scene()
    assert s.timeline() == [0, 0, 1, 2, 2, 2]
    assert s.duration_frames == 6
    assert s.timeline_start(2) == 3
    assert s.duration_seconds == pytest.approx(6 / 24)


def test_clip_to_must_reference_layer_below():
    with pytest.raises(ValidationError, match="below"):
        Scene.model_validate({"name": "x", "frames": [{"layers": [{"id": "shade", "clip_to": "color"}, {"id": "color"}]}]})


def test_duplicate_layer_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        Scene.model_validate({"name": "x", "frames": [{"layers": [{"id": "a"}, {"id": "a"}]}]})


def test_unknown_fields_and_bad_values_rejected():
    with pytest.raises(ValidationError):
        StrokeElement.model_validate({"points": [[0, 0]], "colour": "#000"})  # typo
    with pytest.raises(ValidationError):
        StrokeElement.model_validate({"points": [[0, 0]], "brush": "crayon"})
    with pytest.raises(ValidationError):
        StrokeElement.model_validate({"points": [[0, 0]], "color": "not a colour!"})
    with pytest.raises(ValidationError):
        Layer.model_validate({"id": "a", "blend": "erase"})  # erase is an element op, not a layer blend
    with pytest.raises(ValidationError):
        StrokeElement.model_validate({"points": [[0, 0, 0.5, 9]]})


def test_discriminated_union_parses_element_types():
    s = _scene()
    types = [el.type for l in s.frames[0].layers for el in l.elements]
    assert types == ["ellipse", "rect", "stroke"]


def test_payload_resolves_palette_and_post():
    s = _scene(post={"grain": {"amount": 0.1}})
    p = build_frame_payload(s, 0, palette={"skin": "#ffd9c2", "skin_shade": "#e8a99a", "line": "#111111"}, style_post={"vignette": {"amount": 0.3}})
    assert p["layers"][0]["elements"][0]["fill"] == "#ffd9c2"
    assert p["layers"][2]["elements"][0]["color"] == "#111111"
    assert p["post"]["grain"]["amount"] == 0.1 and p["post"]["vignette"]["amount"] == 0.3
    assert p["width"] == 200 and p["frame"] == 0


def test_palette_overrides_and_missing_role():
    s = _scene(palette_overrides={"skin": "#000000"})
    p = build_frame_payload(s, 0, palette={"skin": "#ffffff", "skin_shade": "#eeeeee", "line": "#111111"})
    assert p["layers"][0]["elements"][0]["fill"] == "#000000"
    with pytest.raises(KeyError, match="@nope"):
        resolve_palette_refs({"a": "@nope"}, {"x": "#fff"})


def test_camera_keys_interpolate_per_timeline_frame():
    keys = [CameraKey(frame=0, x=0, zoom=1.0, ease="linear"), CameraKey(frame=10, x=100, zoom=2.0)]
    c = camera_at(keys, 5)
    assert c["x"] == pytest.approx(50) and c["zoom"] == pytest.approx(1.5)
    assert camera_at(keys, 50)["x"] == 100
    s = _scene(camera_keys=[{"frame": 0, "x": 0, "ease": "linear"}, {"frame": 4, "x": 40}])
    assert build_frame_payload(s, 1, timeline_frame=2)["camera"]["x"] == pytest.approx(20)


def test_roundtrip_json_and_schema():
    s = _scene()
    s2 = Scene.model_validate_json(s.model_dump_json(exclude_none=True))
    assert s2.timeline() == s.timeline()
    sch = json_schema()
    assert "frames" in sch["properties"]
