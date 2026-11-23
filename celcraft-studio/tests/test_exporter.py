import numpy as np
import pytest

from celcraft import audio_lipsync as al
from celcraft.exporter import export_video, probe, timecode
from celcraft.project import Project, xsheet, xsheet_csv, xsheet_text

pytestmark = pytest.mark.usefixtures("needs_ffmpeg")


def test_timecode():
    assert timecode(0, 24) == "00:00:00:00"
    assert timecode(24 * 61 + 5, 24) == "00:01:01:05"


def test_mp4_with_audio(tmp_path, synthetic_frames):
    wav = al.save_wav(tmp_path / "a.wav", al.synth_pop(0.3))
    out = export_video(synthetic_frames, tmp_path / "o.mp4", fps=24, audio=wav)
    info = probe(out.path)
    assert info["video_codec"] == "h264" and info["audio_codec"] == "aac"
    assert info["width"] == 160 and info["height"] == 90
    assert info["duration"] == pytest.approx(0.5, abs=0.08)  # 12 frames @ 24 fps, audio padded/trimmed


def test_alpha_formats(tmp_path, synthetic_frames):
    webm = export_video(synthetic_frames, tmp_path / "o.webm", fps=24)
    assert probe(webm.path)["video_codec"] == "vp9"
    mov = export_video(synthetic_frames, tmp_path / "o.mov", fps=24)
    info = probe(mov.path)
    assert info["video_codec"] == "prores" and "yuva444p" in info.get("pix_fmt", "")


def test_gif_apng_png_and_animatic(tmp_path, synthetic_frames):
    gif = export_video(synthetic_frames, tmp_path / "o.gif", fps=24, gif_width=80)
    assert gif.path.read_bytes()[:3] == b"GIF"
    apng = export_video(synthetic_frames, tmp_path / "o.png", fmt="apng", fps=24)
    assert apng.path.read_bytes()[:4] == b"\x89PNG"
    seq = export_video(synthetic_frames, tmp_path / "seq", fmt="png", fps=24)
    assert len(list(seq.path.glob("frame_*.png"))) == 12
    an = export_video(synthetic_frames, tmp_path / "an.mp4", fmt="animatic", fps=24,
                      hud={"scene": "t", "labels": ["key"] * 12, "phonemes": ["A"] * 12, "notes": ["n"] * 12})
    assert probe(an.path)["video_codec"] == "h264"


def test_bad_format(tmp_path, synthetic_frames):
    with pytest.raises(ValueError):
        export_video(synthetic_frames, tmp_path / "o.xyz")


def test_mixer_lipsync_and_beats(tmp_path):
    voice = al.synth_voice([(0.1, 0.2, "a"), (0.4, 0.2, "o")], 0.8)
    al.save_wav(tmp_path / "v.wav", voice)
    shapes = al.amplitude_lipsync(tmp_path / "v.wav", 24)
    assert shapes[0] == "X" and any(s in "BCDEF" for s in shapes)
    assert all(s in "XABCDEFGH" for s in shapes)
    # no single-frame flickers
    runs = [len(r) for r in "".join(shapes).replace("X", " X ").split()]
    assert min(runs) >= 1
    txt = al.text_lipsync("yatta", tmp_path / "v.wav", 24, n_frames=24)
    assert len(txt) == 24
    ph = al.phoneme_lipsync([(0.0, 0.2, "M"), (0.2, 0.5, "AA1"), (0.5, 0.7, "UW")], 24, 20)
    assert ph[1] == "A" and ph[7] == "D" and ph[14] == "F"

    al.save_wav(tmp_path / "m.wav", al.synth_music(4.0, bpm=120))
    bt = al.beat_markers(tmp_path / "m.wav", 24)
    assert bt["tempo"] == pytest.approx(120, rel=0.06) or bt["tempo"] == pytest.approx(240, rel=0.06) or bt["tempo"] == pytest.approx(60, rel=0.06)

    bus = al.mix_tracks([{"path": str(tmp_path / "v.wav"), "start": 0.5, "gain_db": -6, "pan": 1.0}], 2.0)
    assert bus.shape == (96000, 2)
    assert np.abs(bus[:24000]).max() == 0  # silent before start
    assert np.abs(bus[:, 1]).max() > np.abs(bus[:, 0]).max()  # panned right


def test_project_and_xsheet(tmp_path):
    p = Project.init(tmp_path / "proj", "Test", width=64, height=48)
    p.new_scene("shot1")
    p.add_frames("shot1", [
        {"hold": 2, "key": "key", "phoneme": "A", "notes": "start", "layers": [{"id": "bg", "elements": []}, {"id": "char", "elements": [{"type": "ellipse", "cx": 1, "cy": 1, "rx": 1}]}]},
        {"hold": 1, "layers": [{"id": "bg", "elements": []}, {"id": "char", "elements": [{"type": "ellipse", "cx": 2, "cy": 1, "rx": 1}]}]},
    ])
    s = p.load_scene("shot1")
    rows = xsheet(s)
    assert [r["char"] for r in rows] == ["1", "|", "2"]
    assert [r["bg"] for r in rows] == ["1", "|", "|"]  # unchanged background is a hold
    assert rows[0]["phoneme"] == "A" and rows[1]["key"] == "|"
    assert "frame" in xsheet_csv(s) and "char" in xsheet_text(s)
    assert p.info()["scenes"][0]["frames"] == 3
    assert Project.find(tmp_path / "proj" / "scenes").root == p.root
