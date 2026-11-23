# CelCraft Studio

Frame-by-frame 2D cel animation for AI agents. An agent writes scenes as JSON (or calls MCP tools),
CelCraft draws them with a pressure-sensitive brush engine in headless Chromium, shows the agent
its own frames through FlipaClip-style onion skins and contact sheets, syncs mouths to dialogue,
and masters the result with FFmpeg.

```
agent ──JSON/MCP──▶ celcraft (Python) ──Playwright──▶ Chromium page pool ──▶ RGBA PNG frames
                       │                                 (renderer/*.js)            │
                       ├── motion_engine  (12 principles math)                      ▼
                       ├── rig_engine     (IK puppet, mouths, eyes, boil)    onion_skin / contact sheet / QA
                       ├── audio_lipsync  (mixer, lip-sync, beats, SFX)             │
                       └── exporter       (FFmpeg) ◀────────────────────────────────┘
```

## Features

* **Renderer** (`renderer/`, offline Canvas2D): perfect-freehand stroke outlines (re-implemented, no
  CDN), 8 brushes (G-pen, ink, pencil, charcoal, marker, dry brush, spray, airbrush), linear/radial
  gradients, 19 layer blend modes, clipping layers (`clip_to`) for cel shadows and rim light,
  isolated groups with alpha-lock/erase, Gaussian/motion blur, glow, speed lines, smears, halftone,
  sparkles, impact frames, bloom, chromatic aberration, grade, vignette, paper texture, animated grain,
  letterbox, multi-plane parallax camera with seeded handheld/impact shake.
* **Persistent worker pool**: one Chromium, N pages; frames render in parallel. Renders are cached by
  content hash and held drawings are hard-linked.
* **Motion engine**: 30 easings + CSS cubic-bezier, timing charts, keyframe tracks, circular arcs,
  Catmull-Rom paths, area-preserving squash/stretch, impact squash, physically-timed jumps, springs,
  Verlet follow-through chains, smear trails, camera follow, spacing diagnostics (pops, linear moves).
* **Rig engine**: FK skeleton, analytic two-bone IK, cel part builder (fill + shadow + rim + ink),
  Preston Blair mouths (X, A–H), anime eyes, blink tracks, line boil, and a ready-made biped "Pip".
* **Review**: onion skins (past red / next green, falloff, subject-layer passes for moving cameras),
  annotated contact sheets, motion-path overlays, filmstrips.
* **Audio**: multi-track mixer (gain, fades, pan, limiter), amplitude / text-guided / phoneme /
  Rhubarb lip-sync, onset + tempo + beat grid, procedural whoosh/impact/pop/music/voice.
* **Export**: MP4 (H.264), WebM VP9 alpha, ProRes 4444 alpha, GIF (palettegen), APNG, PNG sequence,
  and an animatic with a burned-in studio HUD.
* **QA**: blank frames, flicker spikes, undeclared frozen runs, photosensitive flashes, frame count,
  plus a static scene lint.
* **Interfaces**: `celcraft` CLI (JSON output), MCP stdio server (33 tools, returns images), and
  `SKILL.md` for agents.

## Install

Requires Python 3.11+.

```bash
cd celcraft-studio
pip install -e ".[dev]"          # or: uv pip install -e ".[dev]"
playwright install chromium      # headless browser used by the renderer
celcraft doctor                  # checks renderer + ffmpeg + encoders
```

FFmpeg is found in this order: `$CELCRAFT_FFMPEG`, `ffmpeg` on `PATH`, the binary bundled with
`imageio-ffmpeg` (installed as a dependency). Other environment variables: `CELCRAFT_WORKERS`
(render pages, default min(4, CPUs)), `CELCRAFT_CHROMIUM` (explicit browser binary),
`PLAYWRIGHT_BROWSERS_PATH`.

On minimal Linux images run `playwright install-deps chromium` if Chromium fails to start.

## Quick start

```bash
celcraft demo --out celcraft_demo            # 5 s showcase: render, review, QA, export
ls celcraft_demo/exports                     # sakuga_jump.mp4, sakuga_jump_animatic.mp4, sakuga_jump.gif
ls celcraft_demo/review/sakuga_jump          # contact_sheet.png, onion_*.png, motion_path_jump.png, qa_report.json, xsheet.txt
```

Your own shot:

```bash
celcraft init myshot --style anime_cel
cd myshot
celcraft scene new s01
celcraft scene add-frames s01 frames.json    # a frame object or a list of frames
celcraft preview s01 0                       # review/s01/preview_00000.png
celcraft render s01                          # renders/s01/frame_00000.png ...
celcraft sheet s01                           # review/s01/contact_sheet.png
celcraft onion s01 12 --layers character     # review/s01/onion_00012.png
celcraft qa s01
celcraft export s01 -f mp4                   # exports/s01.mp4 (+ mixed audio)
```

Minimal `frames.json`:

```json
[{"hold": 2, "key": "key", "layers": [
  {"id": "color", "kind": "color", "elements": [
    {"type": "ellipse", "cx": 960, "cy": 540, "rx": 180, "fill": "@skin"}]},
  {"id": "shade", "kind": "shade", "clip_to": "color", "blend": "multiply", "opacity": 0.6, "elements": [
    {"type": "ellipse", "cx": 1040, "cy": 600, "rx": 180, "fill": "@shadow"}]},
  {"id": "line", "kind": "line", "elements": [
    {"type": "stroke", "brush": "gpen", "size": 6, "color": "@line", "closed": true,
     "points": [[960, 360], [1130, 470], [1120, 640], [960, 720], [800, 640], [790, 470]]}]}
]}]
```

## CLI reference

| command | purpose |
|---|---|
| `doctor` | environment check |
| `init PATH` | new project with `project_bible.json` |
| `info` | bible + scene summary |
| `scene new / add-frames / lint / list` | scene editing |
| `render NAME [--scale --frames --workers --no-cache]` | render PNG frames |
| `preview NAME FRAME` | one frame, fast |
| `onion NAME FRAME [--before --after --ink --layers]` | onion skin |
| `sheet NAME [--every --keys-only]` | contact sheet |
| `xsheet NAME [--csv]` | exposure sheet |
| `qa NAME` | automated QA (exit 2 on errors) |
| `lipsync AUDIO [--text --rhubarb --start --frames]` | mouth shapes per frame |
| `beats AUDIO` | tempo, beats, onsets |
| `mix NAME` | mix scene audio to WAV |
| `export NAME -f mp4/webm/prores/gif/apng/png/animatic` | encode |
| `presets [palettes/styles/mouths/easings/brushes/blends]` | list presets |
| `schema` | Scene JSON Schema |
| `mcp` | run the MCP server |
| `demo` | build the showcase |

## MCP server

```json
{
  "mcpServers": {
    "celcraft-studio": {
      "command": "celcraft-mcp",
      "env": { "CELCRAFT_WORKERS": "4" }
    }
  }
}
```

Use `"command": "python", "args": ["-m", "celcraft.mcp_server"]` if the script is not on `PATH`.
Works with MCP Python SDK 1.x (FastMCP) and 2.x (MCPServer).

Tools: `studio_guide`, `list_presets`, `scene_json_schema`, `project_init`, `project_info`,
`project_update_bible`, `scene_create`, `scene_get`, `scene_update`, `scene_add_frames`,
`scene_replace_frame`, `scene_delete_frames`, `scene_xsheet`, `scene_lint`, `sketch`*,
`preview_frame`*, `render_scene`, `onion_skin`*, `contact_sheet`*, `motion_path`*, `qa_scene`,
`export_scene`, `lipsync`, `beat_markers`, `synth_sfx`, `motion_spacing`, `motion_jump`,
`motion_arc`, `motion_follow_chain`, `rig_biped`, `rig_mouth`, `rig_eye`, `health`
(* returns an image). The Chromium pool stays warm between calls.

## Python API

```python
from celcraft import motion_engine as me
from celcraft.rig_engine import Biped, BipedPose
from celcraft.schema import Scene
from celcraft.renderer_bridge import render_scene

pip = Biped()
arc = me.ballistic_jump((600, 760), (1300, 760), 300, 20)
frames = []
for i, (x, y) in enumerate(arc):
    pose = BipedPose(x=x, y=y, scale=1.6, mouth="D",
                     squash=me.squash_stretch(me.velocities(arc)[i]).matrix((x, y - 60)))
    frames.append({"hold": 1, "layers": [{"id": "character", "elements": pip.build(pose)}]})
scene = Scene(name="hop", style="anime_cel", frames=frames)
render_scene(scene, "renders/hop")
```

## Tests

```bash
pytest -q          # renderer tests skip without Chromium, exporter tests skip without ffmpeg
```

## Layout

```
celcraft/        Python package (cli, mcp_server, project, schema, motion_engine, rig_engine,
                 renderer_bridge, onion_skin, audio_lipsync, exporter, validator, presets)
renderer/        engine.html + brushes.js, compositor.js, camera.js, vfx.js, studio_renderer.js
presets/         palettes.json, styles.json, mouths_preston_blair.json
examples/        demo_sakuga_jump.py
tests/           pytest suite
SKILL.md         agent operating procedure
```

## Credits

The stroke outline algorithm follows [perfect-freehand](https://github.com/steveruizok/perfect-freehand)
by Steve Ruiz (MIT), re-implemented in `renderer/brushes.js`. Mouth chart after Preston Blair, shape
letters compatible with [Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync).
