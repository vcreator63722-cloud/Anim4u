"""Audio: multi-track mixer, automatic lip-sync, beat markers, procedural SFX.

Lip-sync produces one Preston Blair mouth shape (X A B C D E F G H) per
timeline frame. Three sources, best first:

1. :func:`rhubarb_lipsync` — import a Rhubarb Lip Sync JSON (most accurate).
2. :func:`phoneme_lipsync` — timed ARPAbet phonemes (from any aligner).
3. :func:`amplitude_lipsync` — analysis of the dialogue audio itself
   (loudness + spectral centroid + zero-crossings), no extra tools needed.
   :func:`text_lipsync` refines it with the script when you have one.

All return plain lists of shape letters, ready to put into ``Frame.phoneme``
and to drive ``rig_engine.mouth_elements``.
"""

from __future__ import annotations

import json
import math
import wave
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np

from ._ffmpeg import run_ffmpeg
from .presets import mouths

SR = 48000
AudioLike = Union[str, Path, np.ndarray]

# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_audio(path: Union[str, Path], sr: int = SR) -> np.ndarray:
    """Decode any audio file to float32 stereo [n, 2] at ``sr`` (WAV natively, others via ffmpeg)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
                raw = w.readframes(n)
            if width in (2, 4) and rate == sr:
                dt = np.int16 if width == 2 else np.int32
                data = np.frombuffer(raw, dtype=dt).astype(np.float32) / float(np.iinfo(dt).max)
                data = data.reshape(-1, ch)
                return _to_stereo(data)
        except wave.Error:
            pass
    raw = run_ffmpeg(["-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"], capture=True)
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()


def _to_stereo(data: np.ndarray) -> np.ndarray:
    if data.ndim == 1:
        data = data[:, None]
    if data.shape[1] == 1:
        return np.repeat(data, 2, axis=1)
    return data[:, :2]


def save_wav(path: Union[str, Path], data: np.ndarray, sr: int = SR) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _to_stereo(np.asarray(data, dtype=np.float32))
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def _mono(audio: AudioLike, sr: int) -> np.ndarray:
    data = load_audio(audio, sr) if isinstance(audio, (str, Path)) else np.asarray(audio, dtype=np.float32)
    return data.mean(axis=1) if data.ndim == 2 else data


# ---------------------------------------------------------------------------
# Mixer
# ---------------------------------------------------------------------------

def mix_tracks(tracks: Sequence, duration: float, *, sr: int = SR, base_dir: Optional[Path] = None, limiter: bool = True) -> np.ndarray:
    """Mix ``schema.AudioTrack`` objects (or dicts) onto a stereo bus of ``duration`` seconds.

    Supports start offset, trim, duration, gain (dB), fade in/out and
    constant-power pan. A soft tanh limiter prevents clipping.
    """
    n = int(round(duration * sr))
    bus = np.zeros((n, 2), dtype=np.float32)
    for t in tracks:
        t = t if isinstance(t, dict) else t.model_dump()
        p = Path(t["path"])
        if not p.is_absolute() and base_dir is not None:
            p = base_dir / p
        clip = load_audio(p, sr)
        trim = int(t.get("trim_start", 0) * sr)
        clip = clip[trim:]
        if t.get("duration"):
            clip = clip[: int(t["duration"] * sr)]
        if not len(clip):
            continue
        clip = clip * (10 ** (t.get("gain_db", 0.0) / 20))
        fi, fo = int(t.get("fade_in", 0) * sr), int(t.get("fade_out", 0) * sr)
        if fi > 0:
            clip[: min(fi, len(clip))] *= np.linspace(0, 1, min(fi, len(clip)), dtype=np.float32)[:, None]
        if fo > 0:
            k = min(fo, len(clip))
            clip[-k:] *= np.linspace(1, 0, k, dtype=np.float32)[:, None]
        pan = t.get("pan", 0.0)
        ang = (pan + 1) * math.pi / 4
        clip = clip * np.array([math.cos(ang), math.sin(ang)], dtype=np.float32) * math.sqrt(2)
        start = int(t.get("start", 0) * sr)
        if start >= n:
            continue
        end = min(n, start + len(clip))
        bus[start:end] += clip[: end - start]
    if limiter:
        peak = float(np.abs(bus).max()) if len(bus) else 0.0
        if peak > 0.89:
            bus = np.tanh(bus * 1.1) / np.tanh(1.1) * 0.95
    return bus


def mix_scene_audio(scene, out_path: Union[str, Path], *, base_dir: Optional[Path] = None, sr: int = SR) -> Optional[Path]:
    if not scene.audio:
        return None
    bus = mix_tracks(scene.audio, scene.duration_seconds, sr=sr, base_dir=base_dir)
    return save_wav(out_path, bus, sr)


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def frame_features(audio: AudioLike, fps: float, *, sr: int = SR, n_frames: Optional[int] = None, start: float = 0.0) -> dict[str, np.ndarray]:
    """Per-video-frame RMS, spectral centroid (Hz) and zero-crossing rate."""
    x = _mono(audio, sr)
    hop = sr / fps
    if n_frames is None:
        n_frames = int(math.ceil(len(x) / hop + start * fps))
    win = int(hop * 1.5)
    window = np.hanning(win).astype(np.float32)
    rms = np.zeros(n_frames, dtype=np.float32)
    cent = np.zeros(n_frames, dtype=np.float32)
    zcr = np.zeros(n_frames, dtype=np.float32)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    for f in range(n_frames):
        c = int((f / fps - start) * sr + hop / 2)
        a, b = c - win // 2, c - win // 2 + win
        if b <= 0 or a >= len(x):
            continue
        seg = np.zeros(win, dtype=np.float32)
        src = x[max(0, a): min(len(x), b)]
        seg[max(0, -a): max(0, -a) + len(src)] = src
        rms[f] = math.sqrt(float(np.mean(seg ** 2)))
        spec = np.abs(np.fft.rfft(seg * window))
        total = float(spec.sum())
        cent[f] = float((freqs * spec).sum() / total) if total > 1e-9 else 0.0
        zcr[f] = float(np.mean(np.abs(np.diff(np.sign(seg))) > 0))
    return {"rms": rms, "centroid": cent, "zcr": zcr}


def _enforce_min_hold(shapes: list[str], min_hold: int = 2) -> list[str]:
    """Remove 1-frame flickers (mouths that pop for a single frame read as noise)."""
    out = shapes[:]
    i = 0
    while i < len(out):
        j = i
        while j < len(out) and out[j] == out[i]:
            j += 1
        if j - i < min_hold and len(out) > min_hold:
            fill = out[i - 1] if i > 0 else (out[j] if j < len(out) else out[i])
            for k in range(i, j):
                out[k] = fill
        i = j
    return out


# ---------------------------------------------------------------------------
# Lip-sync
# ---------------------------------------------------------------------------

def amplitude_lipsync(
    audio: AudioLike,
    fps: float,
    *,
    sr: int = SR,
    n_frames: Optional[int] = None,
    start: float = 0.0,
    silence: float = 0.08,
    min_hold: int = 2,
) -> list[str]:
    """Mouth shape per frame from dialogue audio alone.

    Heuristic (tuned for speech):
      * level < silence           -> X (rest)
      * first frame after silence -> A (lips part from closed — reads as speech onset)
      * high zero-crossing        -> B (sibilants / fricatives: s, t, k, ee)
      * low centroid              -> E (mid) / F (quiet)   — rounded vowels (o, oo)
      * else by loudness          -> B < C < D
    ``start`` = timeline time (s) at which the audio begins.
    """
    feats = frame_features(audio, fps, sr=sr, n_frames=n_frames, start=start)
    rms, cent, zcr = feats["rms"], feats["centroid"], feats["zcr"]
    voiced = rms[rms > 1e-4]
    ref = float(np.percentile(voiced, 95)) if len(voiced) else 1.0
    lvl = np.clip(rms / max(ref, 1e-6), 0, 1.2)
    out: list[str] = []
    prev = "X"
    for i in range(len(lvl)):
        L, C, Z = float(lvl[i]), float(cent[i]), float(zcr[i])
        if L < silence:
            s = "X"
        elif prev == "X":
            s = "A" if L < 0.5 else "B"
        elif Z > 0.28 and L < 0.75:
            s = "B"
        elif C < 650 and L > 0.25:
            s = "E" if L > 0.5 else "F"
        elif L > 0.72:
            s = "D"
        elif L > 0.4:
            s = "C"
        else:
            s = "B"
        out.append(s)
        prev = s
    return _enforce_min_hold(out, min_hold)


def _phoneme_map() -> dict[str, str]:
    m: dict[str, str] = {}
    for shape, spec in mouths().items():
        for ph in spec.get("phonemes", []):
            m[ph.upper()] = shape
    return m


def phoneme_lipsync(phonemes: Sequence[tuple[float, float, str]], fps: float, n_frames: int, *, min_hold: int = 2) -> list[str]:
    """Timed ARPAbet phonemes ``(start_s, end_s, "AA1")`` -> mouth shapes per frame."""
    pm = _phoneme_map()
    out = ["X"] * n_frames
    for start, end, ph in phonemes:
        key = "".join(ch for ch in ph.upper() if ch.isalpha())
        shape = pm.get(key, "B")
        for f in range(int(math.floor(start * fps)), int(math.ceil(end * fps))):
            if 0 <= f < n_frames:
                out[f] = shape
    return _enforce_min_hold(out, min_hold)


def rhubarb_lipsync(path: Union[str, Path], fps: float, n_frames: int, *, offset: float = 0.0) -> list[str]:
    """Import Rhubarb Lip Sync JSON (``rhubarb -f json``). Shapes map 1:1 (A-H, X)."""
    data = json.loads(Path(path).read_text())
    out = ["X"] * n_frames
    for cue in data.get("mouthCues", []):
        v = cue["value"]
        for f in range(int(round((cue["start"] + offset) * fps)), int(round((cue["end"] + offset) * fps))):
            if 0 <= f < n_frames:
                out[f] = v if v in "ABCDEFGHX" else "B"
    return out


_DIGRAPHS = {"th": "TH", "sh": "SH", "ch": "CH", "oo": "UW", "ee": "IY", "ou": "AW", "ow": "OW", "ai": "EY", "ay": "EY", "ph": "F", "wh": "W", "ng": "NG", "qu": "K"}
_LETTERS = {"a": "AA", "e": "EH", "i": "IH", "o": "AO", "u": "UH", "y": "Y", "m": "M", "b": "B", "p": "P", "f": "F", "v": "V", "l": "L", "r": "R",
            "w": "W", "s": "S", "z": "Z", "t": "T", "d": "D", "n": "N", "k": "K", "c": "K", "g": "G", "j": "JH", "h": "HH", "x": "K", "q": "K"}


def text_to_phonemes(text: str) -> list[str]:
    """Very rough grapheme -> ARPAbet (fallback when no aligner is available)."""
    t = text.lower()
    out, i = [], 0
    while i < len(t):
        if t[i: i + 2] in _DIGRAPHS:
            out.append(_DIGRAPHS[t[i: i + 2]])
            i += 2
        elif t[i] in _LETTERS:
            out.append(_LETTERS[t[i]])
            i += 1
        elif t[i] in " ,.!?;:-":
            out.append("sil" if t[i] != " " else "_")
            i += 1
        else:
            i += 1
    return out


def text_lipsync(text: str, audio: AudioLike, fps: float, *, n_frames: Optional[int] = None, start: float = 0.0, sr: int = SR, min_hold: int = 2) -> list[str]:
    """Script-guided lip-sync: phonemes from the text are spread across the
    *voiced* frames found in the audio (so mouths only move while sound plays)."""
    amp = amplitude_lipsync(audio, fps, sr=sr, n_frames=n_frames, start=start, min_hold=1)
    voiced = [i for i, s in enumerate(amp) if s != "X"]
    phs = [p for p in text_to_phonemes(text) if p not in ("_",)]
    if not voiced or not phs:
        return amp
    pm = _phoneme_map()
    out = ["X"] * len(amp)
    for k, f in enumerate(voiced):
        ph = phs[min(len(phs) - 1, int(k * len(phs) / len(voiced)))]
        shape = "X" if ph == "sil" else pm.get(ph, "B")
        # loud frames open wider than the text guess (keeps energy readable)
        if shape in "BC" and amp[f] == "D":
            shape = "D"
        out[f] = shape
    return _enforce_min_hold(out, min_hold)


# ---------------------------------------------------------------------------
# Beats
# ---------------------------------------------------------------------------

def beat_markers(audio: AudioLike, fps: float, *, sr: int = 22050, min_bpm: float = 60, max_bpm: float = 200) -> dict:
    """Onsets + tempo + beat grid (as timeline frames) for timing animation to music."""
    x = _mono(audio, sr)
    hop, win = 512, 2048
    if len(x) < win * 2:
        return {"tempo": None, "beats": [], "onsets": []}
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(win)[None, :]
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10)
    flux = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0], flux])
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    env_rate = sr / hop
    # onsets: local maxima above adaptive threshold
    onsets = []
    w = int(env_rate * 0.05) or 1
    for i in range(1, len(flux) - 1):
        lo, hi = max(0, i - w), min(len(flux), i + w + 1)
        if flux[i] == flux[lo:hi].max() and flux[i] > np.median(flux[lo:hi]) + 1.0:
            if not onsets or i - onsets[-1] > w:
                onsets.append(i)
    # tempo via autocorrelation of onset envelope
    env = np.maximum(flux, 0)
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lag_min = int(env_rate * 60 / max_bpm)
    lag_max = int(env_rate * 60 / min_bpm)
    if lag_max >= len(ac) or lag_min < 1:
        return {"tempo": None, "beats": [], "onsets": [int(round(o / env_rate * fps)) for o in onsets]}
    lag = lag_min + int(np.argmax(ac[lag_min:lag_max]))
    tempo = 60 * env_rate / lag
    # phase: offset maximising summed envelope on the grid
    best_phase, best = 0, -1.0
    for ph in range(lag):
        s = float(env[ph::lag].sum())
        if s > best:
            best, best_phase = s, ph
    beats = [int(round(b / env_rate * fps)) for b in range(best_phase, len(env), lag)]
    return {
        "tempo": round(tempo, 2),
        "beats": beats,
        "onsets": [int(round(o / env_rate * fps)) for o in onsets],
    }


# ---------------------------------------------------------------------------
# Procedural SFX / music (so demos and agents have audio without assets)
# ---------------------------------------------------------------------------

def _env(n: int, attack: float, release: float, sr: int) -> np.ndarray:
    t = np.arange(n) / sr
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    r = np.exp(-np.maximum(0, t - attack) / max(release, 1e-4))
    return (a * r).astype(np.float32)


def synth_whoosh(duration: float = 0.45, *, sr: int = SR, seed: int = 1, rising: bool = True) -> np.ndarray:
    """Filtered-noise swoosh with a pitch sweep (jumps, swings, smears)."""
    rng = np.random.default_rng(seed)
    n = int(duration * sr)
    noise = rng.standard_normal(n).astype(np.float32)
    t = np.linspace(0, 1, n, dtype=np.float32)
    # time-varying one-pole lowpass (cutoff sweep)
    cutoff = 300 + (3500 if rising else 1500) * (np.sin(np.pi * t) ** 1.5)
    alpha = 1 - np.exp(-2 * np.pi * cutoff / sr)
    y = np.zeros(n, dtype=np.float32)
    acc = 0.0
    for i in range(n):
        acc += alpha[i] * (noise[i] - acc)
        y[i] = acc
    y *= np.sin(np.pi * t) ** 2
    y /= np.abs(y).max() + 1e-9
    pan = np.sin(np.pi * t * 0.9)
    return np.stack([y * (1 - 0.4 * pan), y * (0.6 + 0.4 * pan)], axis=1) * 0.7


def synth_impact(duration: float = 0.7, *, sr: int = SR, seed: int = 2) -> np.ndarray:
    """Punchy thump + crunch (landings, hits)."""
    rng = np.random.default_rng(seed)
    n = int(duration * sr)
    t = np.arange(n) / sr
    freq = 45 + 120 * np.exp(-t * 30)
    body = np.sin(2 * np.pi * np.cumsum(freq) / sr) * _env(n, 0.002, 0.18, sr)
    crunch = rng.standard_normal(n) * _env(n, 0.001, 0.04, sr) * 0.6
    y = np.tanh((body + crunch) * 2.2).astype(np.float32) * 0.9
    return np.stack([y, y], axis=1)


def synth_pop(duration: float = 0.18, *, sr: int = SR, pitch: float = 900) -> np.ndarray:
    """Cartoon 'pop' / sparkle blip."""
    n = int(duration * sr)
    t = np.arange(n) / sr
    f = pitch * (1 + 1.5 * np.exp(-t * 40))
    y = (np.sin(2 * np.pi * np.cumsum(f) / sr) * _env(n, 0.002, 0.05, sr)).astype(np.float32) * 0.5
    return np.stack([y, y], axis=1)


def synth_music(duration: float, *, bpm: float = 120, sr: int = SR, key: float = 220.0, seed: int = 3) -> np.ndarray:
    """Upbeat loop: kick on beats, hat on offbeats, plucked pentatonic arp, pad."""
    rng = np.random.default_rng(seed)
    n = int(duration * sr)
    out = np.zeros(n, dtype=np.float32)
    beat = 60 / bpm
    kick = synth_impact(0.35, sr=sr, seed=9)[:, 0] * 0.55
    hat_n = int(0.05 * sr)
    hat = (rng.standard_normal(hat_n) * _env(hat_n, 0.001, 0.012, sr)).astype(np.float32) * 0.18
    scale = [0, 3, 5, 7, 10, 12, 15]
    pattern = [0, 2, 4, 2, 5, 4, 2, 1]
    step = beat / 2
    k = 0
    t0 = 0.0
    while t0 < duration:
        i = int(t0 * sr)
        if k % 2 == 0:
            e = min(n, i + len(kick))
            out[i:e] += kick[: e - i]
        else:
            e = min(n, i + hat_n)
            out[i:e] += hat[: e - i]
        semis = scale[pattern[k % len(pattern)]] + (12 if (k // 16) % 2 else 0)
        f = key * 2 ** (semis / 12)
        pn = int(step * sr * 1.6)
        tt = np.arange(pn) / sr
        pluck = (np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(4 * np.pi * f * tt)) * _env(pn, 0.003, 0.12, sr) * 0.16
        e = min(n, i + pn)
        out[i:e] += pluck[: e - i].astype(np.float32)
        k += 1
        t0 += step
    tt = np.arange(n) / sr
    pad = sum(np.sin(2 * np.pi * key / 2 * r * tt) for r in (1, 1.25, 1.5)) * 0.03
    out += pad.astype(np.float32) * np.clip(tt / 1.0, 0, 1)
    stereo = np.stack([out, np.roll(out, int(0.012 * sr))], axis=1)
    return stereo / (np.abs(stereo).max() + 1e-9) * 0.6


def synth_voice(syllables: Sequence[tuple[float, float, str]], duration: float, *, sr: int = SR, pitch: float = 230.0) -> np.ndarray:
    """Formant-synth 'voice' for placeholder dialogue (drives the lip-sync demo).

    ``syllables`` = [(start_s, length_s, vowel)] with vowel in a/e/i/o/u.
    """
    formants = {"a": (800, 1200), "e": (500, 1900), "i": (300, 2300), "o": (500, 850), "u": (330, 800)}
    n = int(duration * sr)
    out = np.zeros(n, dtype=np.float32)
    for start, length, v in syllables:
        m = int(length * sr)
        t = np.arange(m) / sr
        f0 = pitch * (1 + 0.12 * np.sin(np.pi * t / max(length, 1e-3))) * (1 + 0.01 * np.sin(2 * np.pi * 5.5 * t))
        phase = 2 * np.pi * np.cumsum(f0) / sr
        f1, f2 = formants.get(v, formants["a"])
        sig = np.zeros(m)
        for h in range(1, 30):
            fh = f0 * h
            amp = np.exp(-((fh - f1) / 130) ** 2) + 0.6 * np.exp(-((fh - f2) / 180) ** 2) + 0.02
            sig += amp * np.sin(phase * h) / h ** 0.3
        env = np.clip(t / 0.03, 0, 1) * np.clip((length - t) / 0.06, 0, 1)
        sig = sig * env
        i = int(start * sr)
        e = min(n, i + m)
        out[i:e] += sig[: e - i].astype(np.float32)
    out /= np.abs(out).max() + 1e-9
    return np.stack([out, out], axis=1) * 0.75
