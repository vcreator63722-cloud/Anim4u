/*
 * CelCraft Studio - camera.js
 *
 * 2D virtual multi-plane camera.
 *
 * World space: the scene's pixel grid. A camera at (width/2, height/2) with
 * zoom 1 and rotation 0 shows the world 1:1.
 *
 * Multi-plane parallax: every layer has `parallax` (depth factor).
 *   0   -> plane is locked to the screen (distant sky, HUD)
 *   0.3 -> far mountains move 30% of the camera move
 *   1   -> character plane (moves exactly with the camera)
 *   1.4 -> foreground plane moves faster than the camera
 *
 * Handheld / impact shake is generated from smooth seeded value-noise keyed on
 * the timeline frame, so it is deterministic and looks organic (not random
 * per-frame jitter).
 */
(function (global) {
  "use strict";
  const CC = (global.CelCraft = global.CelCraft || {});

  function hash1(n, seed) {
    let x = Math.imul((n | 0) ^ (seed | 0), 0x27d4eb2d);
    x ^= x >>> 15;
    x = Math.imul(x, 0x85ebca6b);
    x ^= x >>> 13;
    return ((x >>> 0) / 4294967295) * 2 - 1;
  }
  /** Smooth 1D value noise in [-1, 1]. */
  function noise1(t, seed) {
    const i = Math.floor(t);
    const f = t - i;
    const u = f * f * (3 - 2 * f);
    return hash1(i, seed) * (1 - u) + hash1(i + 1, seed) * u;
  }
  /** Fractal noise: 3 octaves for a natural handheld feel. */
  function fbm(t, seed) {
    return noise1(t, seed) * 0.6 + noise1(t * 2.1, seed + 17) * 0.28 + noise1(t * 4.3, seed + 53) * 0.12;
  }
  CC.noise1 = noise1;
  CC.fbm = fbm;

  CC.shakeOffset = function (shake, frame) {
    if (!shake || !shake.amplitude) return { x: 0, y: 0, r: 0 };
    const f = shake.frequency ?? 0.35; // noise cycles per frame
    const seed = shake.seed ?? 7;
    const t = frame * f;
    let amp = shake.amplitude;
    if (shake.decay && shake.start_frame != null) {
      // impact shake: exponential decay after start_frame
      const dt = Math.max(0, frame - shake.start_frame);
      amp *= Math.exp(-dt * shake.decay);
    }
    return {
      x: fbm(t, seed) * amp,
      y: fbm(t + 101.7, seed + 3) * amp,
      r: fbm(t + 33.3, seed + 9) * (shake.rotation ?? 0) * (amp / (shake.amplitude || 1)),
    };
  };

  /** DOMMatrix mapping world -> output pixels for a plane at `parallax`. */
  CC.cameraMatrix = function (camera, parallax, env) {
    const W = env.width, H = env.height;
    const cam = camera || {};
    const p = parallax ?? 1;
    const cx = W / 2 + ((cam.x ?? W / 2) - W / 2) * p;
    const cy = H / 2 + ((cam.y ?? H / 2) - H / 2) * p;
    const zoom = 1 + ((cam.zoom ?? 1) - 1) * p;
    const shake = CC.shakeOffset(cam.shake, env.frame);
    const rot = (((cam.rotation ?? 0) * p + shake.r) * Math.PI) / 180;
    const m = new DOMMatrix();
    m.scaleSelf(env.scale, env.scale);
    m.translateSelf(W / 2 + shake.x, H / 2 + shake.y);
    m.rotateSelf(0, 0, (rot * 180) / Math.PI);
    m.scaleSelf(zoom, zoom);
    m.translateSelf(-cx, -cy);
    return m;
  };
})(window);
