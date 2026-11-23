/*
 * CelCraft Studio - studio_renderer.js
 *
 * Entry point used by the Python bridge (celcraft/renderer_bridge.py).
 *
 *   await CelCraft.renderFrame(payload) -> "data:image/png;base64,..."
 *
 * payload = {
 *   width, height, scale,          // world size + output scale (0.5 = preview)
 *   frame,                         // timeline frame (drives grain, shake, boil)
 *   background,                    // paint or null for transparent RGBA
 *   camera: {x, y, zoom, rotation, shake},
 *   layers: [...],                 // bottom -> top
 *   post: {...}                    // filmic post chain (see vfx.js)
 * }
 */
(function (global) {
  "use strict";
  const CC = (global.CelCraft = global.CelCraft || {});
  CC.version = "1.0.0";

  const imageCache = new Map();
  function loadImage(src) {
    if (imageCache.has(src)) return imageCache.get(src);
    const p = new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("Failed to load image: " + src.slice(0, 80)));
      img.src = src;
    });
    imageCache.set(src, p);
    return p;
  }

  function collectImages(elements, out) {
    for (const el of elements || []) {
      if (el.type === "image" && el.src) out.add(el.src);
      if (el.type === "group") collectImages(el.elements, out);
    }
  }

  let mainCanvas = null;

  CC.renderFrame = async function (payload) {
    const width = payload.width, height = payload.height;
    const scale = payload.scale ?? 1;
    const pxWidth = Math.max(1, Math.round(width * scale));
    const pxHeight = Math.max(1, Math.round(height * scale));
    const srcs = new Set();
    for (const layer of payload.layers || []) collectImages(layer.elements, srcs);
    const images = {};
    await Promise.all([...srcs].map(async (s) => { images[s] = await loadImage(s); }));

    const env = { width, height, scale, pxWidth, pxHeight, frame: payload.frame ?? 0, camera: payload.camera || {}, images };

    if (!mainCanvas) mainCanvas = CC.makeCanvas(pxWidth, pxHeight);
    if (mainCanvas.width !== pxWidth || mainCanvas.height !== pxHeight) { mainCanvas.width = pxWidth; mainCanvas.height = pxHeight; }
    const ctx = mainCanvas.getContext("2d", { willReadFrequently: true });
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.filter = "none";
    ctx.clearRect(0, 0, pxWidth, pxHeight);

    if (payload.background != null) {
      ctx.save();
      ctx.scale(scale, scale);
      ctx.fillStyle = CC.resolvePaint(ctx, payload.background);
      ctx.fillRect(0, 0, width, height);
      ctx.restore();
    }

    CC.compositeLayers(mainCanvas, payload.layers || [], env);
    CC.vfx.postProcess(mainCanvas, payload.post, env);
    return mainCanvas.toDataURL("image/png");
  };

  /** Pixel probe used by tests: returns [r,g,b,a] at (x, y) of the last frame. */
  CC.probe = function (x, y) {
    if (!mainCanvas) return null;
    const d = mainCanvas.getContext("2d").getImageData(x, y, 1, 1).data;
    return [d[0], d[1], d[2], d[3]];
  };

  global.__celcraftReady = true;
})(window);
