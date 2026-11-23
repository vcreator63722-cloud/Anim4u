"""Persistent headless-Chromium worker pool driving the CelCraft JS renderer.

One Chromium process hosts ``workers`` pages; every page loads
``renderer/engine.html`` once and then renders frames on demand, so the
per-frame cost is only the draw calls (no browser start-up per frame).

Async use (MCP server, notebooks)::

    async with RendererPool(workers=4) as pool:
        png = await pool.render(payload)

Sync use (CLI, scripts)::

    pngs = render_payloads([payload1, payload2], workers=4)
    render_scene(scene, "renders/shot01")

Environment variables:
    CELCRAFT_WORKERS        default worker count
    CELCRAFT_CHROMIUM       explicit chromium executable path
    PLAYWRIGHT_BROWSERS_PATH (standard Playwright variable)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from .presets import renderer_dir, resolve_scene_look
from .schema import Scene, build_frame_payload

DEFAULT_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--force-color-profile=srgb",
    "--disable-lcd-text",
    "--allow-file-access-from-files",
    "--ignore-gpu-blocklist",
    "--enable-gpu-rasterization",
]


class RendererError(RuntimeError):
    pass


def _default_workers() -> int:
    env = os.environ.get("CELCRAFT_WORKERS")
    if env:
        return max(1, int(env))
    return max(1, min(4, (os.cpu_count() or 2)))


# ---------------------------------------------------------------------------
# Payload preprocessing
# ---------------------------------------------------------------------------

_DATA_URL_CACHE: dict[str, str] = {}


def _to_data_url(src: str, base_dir: Optional[Path]) -> str:
    if src.startswith(("data:", "http://", "https://", "file://")):
        return src
    path = Path(src)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    key = str(path.resolve())
    if key not in _DATA_URL_CACHE:
        if not path.is_file():
            raise RendererError(f"image not found: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        _DATA_URL_CACHE[key] = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
    return _DATA_URL_CACHE[key]


def inline_images(payload: dict, base_dir: Optional[Path] = None) -> dict:
    """Replace local image paths with data URLs (the page runs from file:// and is sandboxed)."""

    def walk(elements):
        for el in elements:
            if el.get("type") == "image" and "src" in el:
                el["src"] = _to_data_url(el["src"], base_dir)
            elif el.get("type") == "group":
                walk(el.get("elements", []))

    for layer in payload.get("layers", []):
        walk(layer.get("elements", []))
    return payload


def payload_hash(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def frame_dependent(payload: dict) -> bool:
    """True if the payload renders differently per timeline frame (grain, shake, animated lines)."""
    post = payload.get("post") or {}
    grain = post.get("grain") or {}
    if grain.get("amount", 0) > 0 and grain.get("animate", True):
        return True
    shake = (payload.get("camera") or {}).get("shake") or {}
    if shake.get("amplitude", 0) > 0:
        return True

    def anim(elements):
        for el in elements:
            if el.get("type") == "speed_lines" and el.get("animate", True):
                return True
            if el.get("type") == "group" and anim(el.get("elements", [])):
                return True
        return False

    return any(anim(layer.get("elements", [])) for layer in payload.get("layers", []))


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

class RendererPool:
    """Async pool of persistent Chromium pages running the CelCraft engine."""

    def __init__(self, workers: Optional[int] = None, *, timeout_ms: int = 120_000, extra_args: Sequence[str] = ()):
        self.workers = workers or _default_workers()
        self.timeout_ms = timeout_ms
        self.extra_args = list(extra_args)
        self._pw = None
        self._browser = None
        self._pages: list[Any] = []
        self._free: Optional[asyncio.Queue] = None
        self._engine_url = (renderer_dir() / "engine.html").resolve().as_uri()
        self._lock = asyncio.Lock()
        self.stats = {"frames": 0, "seconds": 0.0}

    async def __aenter__(self) -> "RendererPool":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @property
    def started(self) -> bool:
        return self._browser is not None

    async def start(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:  # pragma: no cover
                raise RendererError("playwright is not installed: pip install playwright && playwright install chromium") from e
            self._pw = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {"args": DEFAULT_ARGS + self.extra_args, "headless": True}
            exe = os.environ.get("CELCRAFT_CHROMIUM")
            if exe:
                launch_kwargs["executable_path"] = exe
            try:
                self._browser = await self._pw.chromium.launch(**launch_kwargs)
            except Exception as e:
                await self._pw.stop()
                self._pw = None
                raise RendererError(
                    "Could not launch Chromium. Run `playwright install chromium` (or set CELCRAFT_CHROMIUM). "
                    f"Original error: {e}"
                ) from e
            self._free = asyncio.Queue()
            for _ in range(self.workers):
                page = await self._new_page()
                self._pages.append(page)
                self._free.put_nowait(page)

    async def _new_page(self):
        page = await self._browser.new_page(viewport={"width": 64, "height": 64})
        page.set_default_timeout(self.timeout_ms)
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        await page.goto(self._engine_url)
        await page.wait_for_function("window.__celcraftReady === true")
        page._celcraft_errors = errors  # type: ignore[attr-defined]
        return page

    async def close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            finally:
                self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        self._pages.clear()

    async def render(self, payload: dict, *, base_dir: Optional[Path] = None) -> bytes:
        """Render one payload to PNG bytes (RGBA, lossless)."""
        if not self.started:
            await self.start()
        payload = inline_images(json.loads(json.dumps(payload)), base_dir)
        page = await self._free.get()
        t0 = time.perf_counter()
        try:
            data_url = await page.evaluate("p => window.CelCraft.renderFrame(p)", payload)
        except Exception as e:
            # replace a possibly-broken page so the pool keeps working
            try:
                await page.close()
            except Exception:
                pass
            new_page = await self._new_page()
            self._pages = [new_page if p is page else p for p in self._pages]
            page = new_page
            raise RendererError(f"render failed: {e}") from e
        finally:
            self._free.put_nowait(page)
        self.stats["frames"] += 1
        self.stats["seconds"] += time.perf_counter() - t0
        if not isinstance(data_url, str) or not data_url.startswith("data:image/png;base64,"):
            raise RendererError("renderer returned no PNG data")
        return base64.b64decode(data_url.split(",", 1)[1])

    async def render_many(
        self,
        payloads: Sequence[dict],
        *,
        base_dir: Optional[Path] = None,
        on_done: Optional[Callable[[int, bytes], None]] = None,
    ) -> list[bytes]:
        results: list[Optional[bytes]] = [None] * len(payloads)

        async def one(i: int, p: dict):
            png = await self.render(p, base_dir=base_dir)
            results[i] = png
            if on_done:
                on_done(i, png)

        await asyncio.gather(*(one(i, p) for i, p in enumerate(payloads)))
        return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------

def run_sync(coro):
    """Run a coroutine from sync code, even if an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def target():
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    th = threading.Thread(target=target)
    th.start()
    th.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


def render_payloads(payloads: Sequence[dict], *, workers: Optional[int] = None, base_dir: Optional[Path] = None) -> list[bytes]:
    async def go():
        async with RendererPool(workers=workers or min(len(payloads), _default_workers()) or 1) as pool:
            return await pool.render_many(payloads, base_dir=base_dir)

    return run_sync(go())


# ---------------------------------------------------------------------------
# Scene rendering
# ---------------------------------------------------------------------------

@dataclass
class RenderResult:
    out_dir: Path
    frames: list[Path]  # one PNG per *timeline* frame (holds expanded)
    rendered: int  # frames actually rasterised this run
    reused: int  # frames served from cache / identical holds
    seconds: float
    manifest: Path
    drawings: list[int] = field(default_factory=list)  # drawing index per timeline frame

    def as_dict(self) -> dict:
        return {
            "out_dir": str(self.out_dir),
            "frame_count": len(self.frames),
            "first_frame": str(self.frames[0]) if self.frames else None,
            "rendered": self.rendered,
            "reused": self.reused,
            "seconds": round(self.seconds, 2),
            "manifest": str(self.manifest),
        }


def scene_payloads(
    scene: Scene,
    *,
    scale: float = 1.0,
    frames: Optional[Iterable[int]] = None,
    only_layers: Optional[Sequence[str]] = None,
) -> list[tuple[int, int, dict]]:
    """(timeline_frame, drawing_index, payload) for the requested timeline frames.

    ``only_layers`` renders a transparent *subject pass* (e.g. just the
    character layers, no background, no post) — what FlipaClip onion-skins.
    """
    palette, style_post = resolve_scene_look(scene)
    timeline = scene.timeline()
    wanted = range(len(timeline)) if frames is None else [f for f in frames if 0 <= f < len(timeline)]
    out = []
    for tf in wanted:
        di = timeline[tf]
        p = build_frame_payload(scene, di, timeline_frame=tf, scale=scale, palette=palette, style_post=style_post)
        if only_layers is not None:
            keep = set(only_layers)
            p["layers"] = [layer for layer in p["layers"] if layer.get("id") in keep]
            p["background"] = None
            p["post"] = {}
        out.append((tf, di, p))
    return out


async def subject_pass_async(scene: Scene, frames: Sequence[int], layers: Sequence[str], *, pool: RendererPool,
                             scale: float = 1.0, base_dir: Optional[Path] = None) -> list[bytes]:
    """Render transparent PNGs of only ``layers`` for the given timeline frames."""
    items = scene_payloads(scene, scale=scale, frames=frames, only_layers=layers)
    return await asyncio.gather(*(pool.render(p, base_dir=base_dir) for _, _, p in items))


def subject_pass(scene: Scene, frames: Sequence[int], layers: Sequence[str], *, scale: float = 1.0, base_dir: Optional[Path] = None) -> list[bytes]:
    async def go():
        async with RendererPool(workers=min(len(frames), _default_workers()) or 1) as pool:
            return await subject_pass_async(scene, frames, layers, pool=pool, scale=scale, base_dir=base_dir)

    return run_sync(go())


async def render_scene_async(
    scene: Scene,
    out_dir: Path | str,
    *,
    pool: Optional[RendererPool] = None,
    scale: float = 1.0,
    frames: Optional[Iterable[int]] = None,
    use_cache: bool = True,
    base_dir: Optional[Path] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> RenderResult:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    old: dict = {}
    if use_cache and manifest_path.is_file():
        try:
            old = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            old = {}
    old_hashes: dict[str, str] = old.get("hashes", {}) if old.get("scale") == scale else {}

    items = scene_payloads(scene, scale=scale, frames=frames)
    t0 = time.perf_counter()
    jobs: list[tuple[int, dict, str]] = []
    reuse: dict[int, int] = {}  # timeline frame -> identical earlier timeline frame
    hashes: dict[str, str] = dict(old_hashes)
    prev_key: Optional[str] = None
    prev_tf: Optional[int] = None
    reused = 0
    for tf, di, payload in items:
        h = payload_hash(payload)
        static_key = None if frame_dependent(payload) else payload_hash({**payload, "frame": 0})
        path = out / f"frame_{tf:05d}.png"
        if static_key is not None and static_key == prev_key and prev_tf is not None:
            reuse[tf] = prev_tf
        elif use_cache and old_hashes.get(str(tf)) == h and path.is_file():
            reused += 1
        else:
            jobs.append((tf, payload, h))
        hashes[str(tf)] = h
        prev_key, prev_tf = static_key, tf

    own_pool = pool is None
    if own_pool:
        pool = RendererPool(workers=min(_default_workers(), max(1, len(jobs))))
    done = 0
    try:
        if jobs:
            await pool.start()

            async def one(tf: int, payload: dict):
                nonlocal done
                png = await pool.render(payload, base_dir=base_dir)
                (out / f"frame_{tf:05d}.png").write_bytes(png)
                done += 1
                if progress:
                    progress(done, len(jobs))

            await asyncio.gather(*(one(tf, p) for tf, p, _ in jobs))
    finally:
        if own_pool:
            await pool.close()

    for tf, src in sorted(reuse.items()):
        dst = out / f"frame_{tf:05d}.png"
        srcp = out / f"frame_{src:05d}.png"
        if dst.exists():
            dst.unlink()
        try:
            os.link(srcp, dst)
        except OSError:
            dst.write_bytes(srcp.read_bytes())
        reused += 1

    timeline = scene.timeline()
    frame_paths = [out / f"frame_{tf:05d}.png" for tf, _, _ in items]
    manifest = {
        "scene": scene.name,
        "width": scene.width,
        "height": scene.height,
        "fps": scene.fps,
        "scale": scale,
        "frame_count": len(timeline),
        "timeline": timeline,
        "hashes": hashes,
        "pattern": "frame_%05d.png",
    }
    manifest_path.write_text(json.dumps(manifest, indent=1))
    return RenderResult(
        out_dir=out,
        frames=frame_paths,
        rendered=len(jobs),
        reused=reused,
        seconds=time.perf_counter() - t0,
        manifest=manifest_path,
        drawings=[di for _, di, _ in items],
    )


def render_scene(scene: Scene, out_dir: Path | str, **kwargs) -> RenderResult:
    return run_sync(render_scene_async(scene, out_dir, **kwargs))


def render_frame_png(scene: Scene, timeline_frame: int = 0, *, scale: float = 1.0, base_dir: Optional[Path] = None) -> bytes:
    """Render a single timeline frame of a scene to PNG bytes."""
    items = scene_payloads(scene, scale=scale, frames=[timeline_frame])
    if not items:
        raise RendererError(f"timeline frame {timeline_frame} out of range (0..{scene.duration_frames - 1})")
    return render_payloads([items[0][2]], workers=1, base_dir=base_dir)[0]
