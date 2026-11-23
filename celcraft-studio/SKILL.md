---
name: celcraft-studio
description: >-
  Frame-by-frame 2D cel animation studio. Use when asked to animate, storyboard, draw an animated
  character/scene, make an anime/cartoon clip, lip-sync dialogue, or export an animation as MP4/GIF/WebM.
  Draws pressure-tapered ink strokes, cel shading, smears, speed lines and impact frames in headless
  Chromium; lets you SEE your frames via onion skins and contact sheets; mixes audio and exports with FFmpeg.
version: 1.0.0
tools: celcraft CLI (`celcraft ...`) or the `celcraft-studio` MCP server (`celcraft mcp`)
---

# CelCraft Studio — Agent SOP

You are the director, key animator, inbetweener, colourist and compositor. Work like a studio:
**plan → keys → breakdowns → inbetweens → review with your eyes → fix → QA → export.**
Never ship frames you have not looked at.

## 0. Setup check (once)

```bash
pip install -e ".[dev]" && playwright install chromium   # or: uv pip install -e .
celcraft doctor          # must print "ok": true (renderer + ffmpeg)
```

MCP clients: call `health`, then `studio_guide` (returns this file).

## 1. The non-negotiable rules

1. **Look at every shot.** After drafting keys: `preview_frame` each key. After rendering:
   `contact_sheet` + `onion_skin` on every fast move. Fix what you see before moving on.
2. **Pose-to-pose.** Keys (story poses) first, tagged `"key": "key"`. Then breakdowns on **arcs**
   (`motion_arc`, `"key": "breakdown"`). Then inbetweens with eased spacing (`motion_spacing`).
3. **Spacing, not just timing.** Moves accelerate and decelerate (`ease_in_out`, `snap`, `overshoot`).
   Even spacing on a long move is a bug (`spacing_report(...)["linear_warning"]`).
4. **Ones for action, twos for acting.** Fast moves `hold: 1`. Dialogue, idles, holds `hold: 2`.
   Held key poses `hold: 3-8` with line boil so they stay alive.
5. **Layer discipline** (bottom → top): backgrounds (`parallax` < 1) → contact shadow → colour →
   shade (`clip_to` colour, `blend: multiply`) → rim (`clip_to`, `screen`/`add`) → line (`kind: "line"`)
   → FX (`blend: "add"` + glow) → foreground (`parallax` > 1, blurred) → titles (`screen_space`).
6. **Colours come from the palette.** Use `"@role"` (e.g. `"@skin_shade"`), never ad-hoc hex for
   characters. `list_presets palettes` shows roles. Override per scene with `palette_overrides`.
7. **Stay on model.** Use the rig (`rig_biped`) or reuse the same contour points per part so volumes
   don't drift. Squash/stretch must preserve area (`squash_stretch` does this).
8. **QA before export.** `qa_scene` must return `"ok": true`. Tag intentional spikes
   (`"key": "impact"` / `"smear"`) so they are not reported as flicker.

## 2. Pipeline

| Step | CLI | MCP tool |
|---|---|---|
| Create project + bible | `celcraft init shot_dir --style anime_cel` | `project_init` |
| Characters/rules in bible | edit `project_bible.json` | `project_update_bible` |
| New scene | `celcraft scene new s01 -p shot_dir` | `scene_create` |
| Look-dev a pose instantly | — | `sketch` (returns image) |
| Add drawings | `celcraft scene add-frames s01 frames.json` | `scene_add_frames` |
| Check one frame | `celcraft preview s01 12` | `preview_frame` (returns image) |
| Render all frames | `celcraft render s01 [--scale 0.5]` | `render_scene` |
| Light-table review | `celcraft onion s01 40 --layers character` | `onion_skin` (image) |
| Shot overview | `celcraft sheet s01` | `contact_sheet` (image) |
| Spacing / arc check | — | `motion_path` (image + numbers) |
| Exposure sheet | `celcraft xsheet s01` | `scene_xsheet` |
| Lip-sync | `celcraft lipsync voice.wav --text "..."` | `lipsync` |
| Beat grid | `celcraft beats music.wav` | `beat_markers` |
| QA | `celcraft qa s01` | `qa_scene` |
| Export | `celcraft export s01 -f mp4` (`webm`, `prores`, `gif`, `apng`, `png`, `animatic`) | `export_scene` |

Renders are cached by content hash: editing one drawing re-renders only that drawing's frames.
Held drawings without animated grain/shake are hard-linked, not re-rendered.

## 3. Scene JSON cheat-sheet

```jsonc
{
  "name": "s01", "width": 1920, "height": 1080, "fps": 24,
  "style": "anime_cel",              // post chain + default palette (anime_cel, ghibli_warm, noir_ink, neon_sakuga, cartoon_pop)
  "background": "#1d1a2b",           // null = transparent RGBA render
  "camera_keys": [                   // camera on ONES even when drawings are on twos
    {"frame": 0, "x": 960, "y": 540, "zoom": 1.0, "ease": "ease_in_out"},
    {"frame": 48, "x": 1300, "zoom": 1.2,
     "shake": {"amplitude": 24, "decay": 0.16, "start_frame": 48, "rotation": 1.5}}
  ],
  "audio": [{"path": "audio/voice.wav", "kind": "dialogue", "start": 0.5, "gain_db": -2, "pan": 0}],
  "frames": [{                        // ONE entry = ONE drawing
    "hold": 2, "key": "key",          // key | extreme | breakdown | inbetween | smear | impact | hold
    "phoneme": "D", "dialogue": "Yatta!", "notes": "hero pose",
    "camera": {"x": 960, "y": 540},   // per-drawing camera (ignored for props set by camera_keys)
    "post": {"impact": {"mode": "mono"}},   // per-drawing post override
    "layers": [
      {"id": "bg", "kind": "background", "parallax": 0.3, "elements": [ ... ]},
      {"id": "color", "kind": "color", "elements": [ ... ]},
      {"id": "shade", "kind": "shade", "clip_to": "color", "blend": "multiply", "opacity": 0.6, "elements": [ ... ]},
      {"id": "line", "kind": "line", "elements": [ ... ]},
      {"id": "fx", "kind": "fx", "blend": "add", "filters": {"glow": {"radius": 12, "strength": 1.2}}, "elements": [ ... ]},
      {"id": "title", "screen_space": true, "elements": [ ... ]}
    ]
  }]
}
```

### Elements (all accept `opacity`, `blend`, `transform`, `blur`, `visible`, `id`)

| type | key fields | use |
|---|---|---|
| `stroke` | `points` [[x,y,(pressure)]], `brush`, `size`, `color`, `taper_start/end` (0-1), `thinning`, `closed`, `fill`, `seed`, `smooth_input` | all ink & paint |
| `polygon` | `points`, `smooth` (spline), `fill`, `stroke`, `stroke_width` | fill-tool regions, flats |
| `path` | `d` (SVG path), `fill`, `stroke` | precise vector shapes |
| `ellipse` / `rect` | `cx,cy,rx,ry,rotation` / `x,y,w,h,radius` | eyes, props, panels |
| `text` | `text,x,y,size,align,fill,stroke,stroke_width` | titles, SFX lettering |
| `image` | `src` (path relative to project or data URL), `x,y,w,h` | references, paintovers |
| `group` | `elements`, `transform`, `isolate` | rig parts; `isolate:true` = private sub-layer |
| `speed_lines` | `mode` radial/parallel, `cx,cy,inner_radius` / `angle,x,y,w,h`, `count`, `width`, `color` | focus & action lines |
| `smear` | `points` (trail), `radii` | smear frames on the fastest drawings |
| `halftone` | `x,y,w,h`, `spacing`, `angle`, `x0,y0 → x1,y1` falloff, `color` | screen tone, shading |
| `sparkle` | `points` [[x,y,scale]], `size`, `color` | glints |

**Brushes:** `gpen` (default crisp anime line), `ink` (heavy brush-pen taper), `pencil` (textured
rough), `charcoal` (grainy, rough edge), `marker` (flat, multiply), `drybrush` (bristles),
`spray` (dots), `airbrush` (soft). Pressure comes from the 3rd point value; without it pressure is
simulated from speed. Give 6–30 control points per stroke; sparse points are splined automatically
(set `smooth_input: false` for sharp polylines like mountain ridges).

**Element blend ops inside a layer:** `source-atop`/`alpha-lock` (paint only on existing pixels),
`erase`, `behind`, plus all colour blends. Use them inside an `isolate: true` group so they only
affect that part.

**Layer blends:** normal, multiply, screen, overlay, add, color-dodge, color-burn, soft-light,
hard-light, darken, lighten, difference, exclusion, hue, saturation, color, luminosity.

**Layer filters:** `blur`, `motion_blur {angle, distance}`, `glow {radius, strength, color}`, `css`.

**Post (scene/frame/style):** `grain`, `paper`, `vignette`, `bloom`, `chromatic_aberration`,
`grade {brightness, contrast, saturation, hue_rotate, tint, tint_amount}`,
`impact {mode: mono|invert|flash}`, `letterbox {ratio}`.

## 4. Motion recipes (use the math, don't guess)

* **Jump / throw / fall:** `motion_jump(start, end, apex_height, frames)` → physically spaced
  positions + velocities + squash/stretch per frame. Apply `SquashStretch.matrix(pivot)` as the
  character group's `transform` (or `BipedPose.squash`).
* **Anticipation → action → follow-through:** 4–10 frames into the anticipation key (`ease_in_out_cubic`),
  hold it 2–4 frames, 1–3 frames to the extreme (`snap`), then `overshoot` into the settle.
* **Arcs:** every breakdown sits on `motion_arc(a, b, c)`. Heads, hands and feet travel on arcs.
* **Overlapping action:** hair/scarf/tail/cape = `motion_follow_chain(anchor_track)` simulated on ones
  over the whole shot; then sample the chain on each drawing's start frame.
* **Smears:** on the 1–3 fastest drawings, add a `smear` built with `smear_trail(positions, i)` in a
  layer under the character, and tag the drawing `"key": "smear"`.
* **Impact:** 1–2 drawings with `post.impact` mono, radial `speed_lines`, camera `shake` with `decay`,
  plus an impact SFX on that frame. Never more than 3 flash frames per second (QA enforces this).
* **Holds:** keep them alive with line boil (`BipedPose.boil≈0.8` or `rig_engine.line_boil`) and a
  slow camera push.
* **Blinks:** `blink_track` (4-frame blink every 2–4.5 s). Don't blink during big action.

## 5. Characters

`rig_biped(pose)` returns cel-shaded elements for "Pip" (2.6-heads hero): isolated parts with base
fill, alpha-locked shadow crescent from the key light, rim light, and G-pen contour. Pose fields:
`x, y` (hip, world), `scale`, `facing`, `lean`, `head_tilt`, `foot_front/back`, `hand_front/back`
(IK targets relative to hip), `mouth` (X/A–H), `eye_open`, `look`, `brow`, `happy_eyes`,
`hair_swing`, `scarf` (chain points), `drawing`, `boil`, `squash`.
Keep hands off the face in hero poses (arms are short: reach ≈ 62 local px).

For custom characters build parts with `rig_engine.cel_part(contour, base=..., shade=..., rim=...)`
and mouths/eyes with `rig_mouth` / `rig_eye`.

## 6. Lip-sync

1. Put dialogue on its own track (`kind: "dialogue"`, `start` seconds).
2. `lipsync(audio, fps, start, n_frames, text="...")` → one shape per timeline frame.
   Prefer Rhubarb JSON (`rhubarb_json=`) when available; phoneme lists via `phoneme_lipsync`.
3. Dialogue drawings go on twos; take the shape at each drawing's **start** frame, set
   `frame.phoneme`, and draw the mouth with `mouth_elements(shape, ...)`.
4. Shapes: X rest · A M/B/P · B most consonants/EE · C EH/AE · D AA (wide) · E AO/ER · F OO/W · G F/V · H L.
   Mouths lead the sound by 0–1 frame; never hold one shape for a single frame (auto-enforced).

## 7. Review loop (what to look for)

* **Contact sheet:** silhouettes readable? keys distinct? staging clear? character in title-safe area?
* **Onion skin** (`onion_skin(..., layers=["character"])` when the camera moves): red = past,
  green = next. Ghost spacing should widen through fast parts and cluster into keys. A ghost
  jumping far out of line = pop. Volumes should match between ghosts.
* **Motion path:** dots on a smooth arc; `pops` list should be empty.
* **QA:** `blank`, `flicker`, `frozen`, `flash`, `count`. Fix errors; read warnings.

## 8. Export

* Delivery: `mp4` (H.264 CRF 16, AAC). Transparent: `webm` (VP9 alpha) or `prores` (4444 + alpha).
* Chat preview: `gif` (`gif_width` 480–640, 12 fps). Hand-off: `png` sequence.
* Director review: `animatic` (burned-in scene, frame/total, timecode, key tags, phoneme, dialogue,
  notes, safe areas, progress bar).
* Audio is mixed from `scene.audio` automatically (gain, fades, pan, limiter).

## 9. Worked example

`examples/demo_sakuga_jump.py` (`celcraft demo`) builds a 5 s, 120-frame shot: idle on twos with
lip-sync, anticipation hold, smear take-off, physical jump arc with scarf follow-through and
parallax camera follow, two-tone impact frames with shake and dust, overshoot recovery, hero pose
with "Yatta!" lip-sync and a title card. Read it before building complex shots.

## 10. Failure modes to avoid

* Linear motion (no easing) and straight-line inbetweens (no arcs).
* Everything on twos (choppy action) or everything on ones (floaty acting).
* Lines and colour on one layer → shading leaks; use `clip_to` and isolated parts.
* Moving the camera per drawing on twos → judder; use `camera_keys`.
* Shipping without looking: always `contact_sheet` + `onion_skin` + `qa_scene` first.
