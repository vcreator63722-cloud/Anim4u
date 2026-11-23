import math

import pytest

from celcraft import motion_engine as me
from celcraft.rig_engine import Affine, Skeleton, Bone, line_boil, two_bone_ik


@pytest.mark.parametrize("name", [n for n in me.EASINGS if n != "constant"])
def test_easings_hit_endpoints(name):
    f = me.EASINGS[name]
    assert f(0.0) == pytest.approx(0.0, abs=1e-6)
    assert f(1.0) == pytest.approx(1.0, abs=1e-6)


def test_anticipation_and_overshoot_leave_range():
    assert min(me.ease("anticipate", t / 20) for t in range(21)) < 0
    assert max(me.ease("overshoot", t / 20) for t in range(21)) > 1


def test_cubic_bezier_linear_and_css_string():
    f = me.cubic_bezier(0.25, 0.25, 0.75, 0.75)
    for t in (0.1, 0.5, 0.9):
        assert f(t) == pytest.approx(t, abs=1e-3)
    g = me.get_easing("cubic_bezier(0.42, 0, 0.58, 1)")
    assert g(0.5) == pytest.approx(0.5, abs=1e-3)
    assert g(0.1) < 0.1  # slow in


def test_spacing_chart_ease_in_out_is_symmetric_and_clustered():
    fr = me.spacing_chart(3, "ease_in_out")
    assert fr == [0.0, 0.125, 0.5, 0.875, 1.0]
    gaps = [b - a for a, b in zip(fr, fr[1:])]
    assert gaps[0] < gaps[1]  # slow out of the first key


def test_interpolate_keys_and_holds():
    keys = [me.Key(0, (0.0, 0.0), "linear"), me.Key(10, (100.0, 50.0), "constant"), me.Key(20, (0.0, 0.0))]
    assert me.interpolate(keys, 5) == pytest.approx((50.0, 25.0))
    assert me.interpolate(keys, 15) == pytest.approx((100.0, 50.0))  # constant = stepped hold
    assert me.interpolate(keys, -3) == (0.0, 0.0)


def test_ballistic_jump_endpoints_and_apex():
    pts = me.ballistic_jump((0, 500), (300, 500), 200, 24)
    assert len(pts) == 25
    assert pts[0] == pytest.approx((0, 500))
    assert pts[-1] == pytest.approx((300, 500))
    assert min(p[1] for p in pts) == pytest.approx(300, abs=2)
    # gravity => vertical spacing shrinks toward the apex
    dy = [abs(b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    assert dy[0] > dy[10]


def test_ballistic_jump_uneven_heights():
    pts = me.ballistic_jump((0, 600), (400, 400), 100, 20)
    assert min(p[1] for p in pts) == pytest.approx(300, abs=3)
    assert pts[-1][1] == pytest.approx(400)


def test_arc_through_passes_through_breakdown():
    f = me.arc_through((0, 0), (50, -30), (100, 0))
    assert f(0) == pytest.approx((0, 0), abs=1e-6)
    assert f(1) == pytest.approx((100, 0), abs=1e-6)
    samples = [f(t / 100) for t in range(101)]
    assert min(math.dist(s, (50, -30)) for s in samples) < 1.5
    assert all(s[1] <= 1e-6 for s in samples)  # arc bulges up, never below the chord


def test_squash_stretch_preserves_area_and_matrix_pivot():
    ss = me.squash_stretch((30, 0), factor=0.02)
    assert ss.scale_along * ss.scale_across == pytest.approx(1.0)
    assert ss.scale_along > 1
    m = Affine.from_list(ss.matrix((10, 20)))
    assert m.apply((10, 20)) == pytest.approx((10, 20))
    assert m.apply((20, 20))[0] == pytest.approx(10 + 10 * ss.scale_along)


def test_impact_squash_recovers():
    sx, sy = me.impact_squash(0)
    assert sy < 1 < sx
    assert me.impact_squash(60) == pytest.approx((1.0, 1.0), abs=1e-3)


def test_spring_follow_lags_then_converges():
    out = me.spring_follow([0] * 3 + [10] * 60)
    assert out[3] < 10
    assert out[-1] == pytest.approx(10, abs=0.05)


def test_follow_through_chain_keeps_segment_lengths():
    anchors = [(100 + i * 8, 100) for i in range(30)]
    frames = me.follow_through_chain(anchors, segments=5, segment_length=10)
    assert len(frames) == 30 and len(frames[0]) == 6
    last = frames[-1]
    assert last[0] == pytest.approx(anchors[-1])
    for a, b in zip(last, last[1:]):
        assert math.dist(a, b) == pytest.approx(10, rel=0.15)
    assert last[-1][0] < last[0][0]  # trails behind the motion


def test_spacing_report_detects_pop_and_linear():
    pts = [(i * 10, 0) for i in range(10)]
    assert me.spacing_report(pts)["linear_warning"]
    pts[6] = (150, 0)
    assert 6 in me.spacing_report(pts)["pops"] or 7 in me.spacing_report(pts)["pops"]


def test_smear_trail_tapers():
    pos = me.ballistic_jump((0, 0), (200, 0), 80, 10)
    sm = me.smear_trail(pos, 5, length=3, radius=20)
    assert sm["type"] == "smear"
    assert sm["radii"][0] < sm["radii"][-1] == pytest.approx(20)


def test_two_bone_ik_reaches_target_and_clamps():
    joint, end = two_bone_ik((0, 0), (30, 40), 30, 30)
    assert end == pytest.approx((30, 40), abs=1e-3)
    assert math.dist((0, 0), joint) == pytest.approx(30)
    _, far = two_bone_ik((0, 0), (500, 0), 30, 30)
    assert math.dist((0, 0), far) == pytest.approx(60, abs=1e-2)


def test_fk_chain():
    sk = Skeleton([Bone("a", 10, 0), Bone("b", 10, 90, parent="a")])
    out = sk.solve()
    assert out["b"]["end"] == pytest.approx((10, 10))


def test_line_boil_cycles():
    pts = [[i * 5.0, 0.0, 0.5] for i in range(8)]
    a = line_boil(pts, drawing=0)
    assert line_boil(pts, drawing=3) == a  # 3-drawing cycle
    assert line_boil(pts, drawing=1) != a
    assert all(len(p) == 3 for p in a)
