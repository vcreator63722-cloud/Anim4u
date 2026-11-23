import numpy as np
from PIL import Image

from celcraft.onion_skin import contact_sheet, flip_strip, ink_mask, motion_path_overlay, onion_from_sequence, onion_skin
from celcraft.validator import validate_frames


def test_onion_skin_tints_past_red_and_future_green(synthetic_frames):
    img = onion_from_sequence(synthetic_frames, 5, before=2, after=2)
    a = np.asarray(img.convert("RGB")).astype(int)
    past = a[45, 20 + 4 * 10]  # ball position at frame 4
    future = a[45, 20 + 6 * 10]  # frame 6
    current = a[45, 20 + 5 * 10]
    assert past[0] > past[1] + 60  # red
    assert future[1] > future[0] + 60  # green
    assert current.max() < 80  # current drawing on top, dark


def test_falloff_and_ink_mode(synthetic_frames):
    img = onion_skin(synthetic_frames[5], [synthetic_frames[4], synthetic_frames[3]], [], falloff=0.3, current_style="ink")
    a = np.asarray(img.convert("RGB")).astype(int)
    near, far = a[45, 60], a[45, 50]
    assert (255 - near[1]) > (255 - far[1])  # nearer ghost is stronger


def test_ink_mask_modes():
    im = Image.new("RGBA", (10, 10), (255, 255, 255, 255))
    im.putpixel((5, 5), (0, 0, 0, 255))
    assert ink_mask(im, "luma")[5, 5] > 0.9 and ink_mask(im, "luma")[0, 0] == 0
    t = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    t.putpixel((2, 2), (200, 0, 0, 255))
    m = ink_mask(t, "auto")
    assert m[2, 2] == 1 and m[0, 0] == 0


def test_contact_sheet_layout(synthetic_frames):
    sheet = contact_sheet(synthetic_frames, columns=4, thumb_width=80, labels=["key"] * 12, notes=["note"] * 12, title="t", highlight=[0])
    assert sheet.width == 4 * 80 + 5 * 10
    assert sheet.height > 3 * 45


def test_motion_path_and_strip(synthetic_frames):
    over = motion_path_overlay(synthetic_frames[0], {"ball": [(20 + i * 10, 45) for i in range(12)]}, highlight=3)
    assert over.size == (160, 90)
    assert flip_strip(synthetic_frames[:4], height=45).height == 53


def test_validator_flags_blank_and_frozen(tmp_path, synthetic_frames):
    rep = validate_frames(synthetic_frames, fps=24)
    assert rep.ok and not rep.issues
    blank = tmp_path / "blank.png"
    Image.new("RGBA", (160, 90), (0, 0, 0, 0)).save(blank)
    frozen = [synthetic_frames[0]] * 10
    rep2 = validate_frames([blank] + frozen, fps=24)
    checks = {i.check for i in rep2.issues}
    assert "blank" in checks and "frozen" in checks and not rep2.ok
    # declared hold is fine
    rep3 = validate_frames(frozen, fps=24, holds_timeline=[0] * 10)
    assert not [i for i in rep3.issues if i.check == "frozen"]


def test_validator_flags_strobing(tmp_path):
    paths = []
    for i in range(24):
        p = tmp_path / f"f{i:03d}.png"
        Image.new("RGBA", (32, 32), (255, 255, 255, 255) if i % 2 else (0, 0, 0, 255)).save(p)
        paths.append(p)
    rep = validate_frames(paths, fps=24)
    assert any(i.check == "flash" for i in rep.issues)
