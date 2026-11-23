/*
 * CelCraft Studio - compositor.js
 *
 * Layer stack compositor: element rasterisation, paints (solid / linear /
 * radial gradients), Photoshop-style blend modes, alpha-locked clipping masks
 * (cel shade + rim light layers clipped to the colour layer), per-layer
 * Gaussian / directional motion blur and glow.
 */
(function (global) {
  "use strict";
  const CC = (global.CelCraft = global.CelCraft || {});

  // ---------------------------------------------------------------------------
  // Blend modes
  // ---------------------------------------------------------------------------
  const BLEND_MAP = {
    normal: "source-over",
    "source-over": "source-over",
    multiply: "multiply",
    screen: "screen",
    overlay: "overlay",
    add: "lighter",
    lighter: "lighter",
    "linear-dodge": "lighter",
    "color-dodge": "color-dodge",
    "color-burn": "color-burn",
    darken: "darken",
    lighten: "lighten",
    "hard-light": "hard-light",
    "soft-light": "soft-light",
    difference: "difference",
    exclusion: "exclusion",
    hue: "hue",
    saturation: "saturation",
    color: "color",
    luminosity: "luminosity",
    // in-layer operators
    "source-atop": "source-atop", // alpha lock: paint only where pixels exist
    "alpha-lock": "source-atop",
    erase: "destination-out",
    "destination-out": "destination-out",
    "destination-in": "destination-in",
    "destination-over": "destination-over", // paint behind
    behind: "destination-over",
  };
  CC.blendOp = (name) => BLEND_MAP[name || "normal"] || "source-over";
  CC.BLEND_MAP = BLEND_MAP;

  // ---------------------------------------------------------------------------
  // Canvas pools
  // ---------------------------------------------------------------------------
  const scratchPool = {};
  CC.scratchCanvas = function (w, h, name) {
    const key = name || "stroke";
    let c = scratchPool[key];
    if (!c) c = scratchPool[key] = CC.makeCanvas(w, h);
    if (c.width < w || c.height < h) {
      c.width = Math.max(c.width, Math.ceil(w));
      c.height = Math.max(c.height, Math.ceil(h));
    }
    return c;
  };

  const layerPool = [];
  CC.acquireCanvas = function (w, h) {
    let c = layerPool.pop();
    if (!c) c = CC.makeCanvas(w, h);
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const ctx = c.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.filter = "none";
    ctx.clearRect(0, 0, w, h);
    return c;
  };
  CC.releaseCanvas = function (c) { if (c && layerPool.length < 48) layerPool.push(c); };

  // ---------------------------------------------------------------------------
  // Paint resolution
  // ---------------------------------------------------------------------------
  CC.resolvePaint = function (ctx, paint) {
    if (paint == null) return "rgba(0,0,0,0)";
    if (typeof paint === "string") return paint;
    let g;
    if (paint.type === "linear") {
      g = ctx.createLinearGradient(paint.x0, paint.y0, paint.x1, paint.y1);
    } else if (paint.type === "radial") {
      g = ctx.createRadialGradient(paint.fx ?? paint.cx, paint.fy ?? paint.cy, paint.fr ?? 0, paint.cx, paint.cy, paint.r);
    } else if (paint.type === "conic" && ctx.createConicGradient) {
      g = ctx.createConicGradient(((paint.angle ?? 0) * Math.PI) / 180, paint.cx, paint.cy);
    } else {
      return paint.color || "#000";
    }
    for (const [offset, color] of paint.stops || []) g.addColorStop(Math.max(0, Math.min(1, offset)), color);
    return g;
  };

  // ---------------------------------------------------------------------------
  // Element drawing
  // ---------------------------------------------------------------------------
  function applyTransform(ctx, t) {
    if (!t) return;
    if (Array.isArray(t)) { ctx.transform(t[0], t[1], t[2], t[3], t[4], t[5]); return; }
    // object form: {x, y, rotation(deg), scale_x, scale_y, pivot_x, pivot_y, skew_x}
    const px = t.pivot_x ?? 0, py = t.pivot_y ?? 0;
    ctx.translate((t.x ?? 0) + px, (t.y ?? 0) + py);
    if (t.rotation) ctx.rotate((t.rotation * Math.PI) / 180);
    if (t.skew_x) ctx.transform(1, 0, Math.tan((t.skew_x * Math.PI) / 180), 1, 0, 0);
    ctx.scale(t.scale_x ?? t.scale ?? 1, t.scale_y ?? t.scale ?? 1);
    ctx.translate(-px, -py);
  }
  CC.applyTransform = applyTransform;

  function strokeAndFill(ctx, el, path) {
    if (el.fill != null) {
      ctx.fillStyle = CC.resolvePaint(ctx, el.fill);
      ctx.fill(path, el.fill_rule || "nonzero");
    }
    if (el.stroke && (el.stroke_width ?? 1) > 0) {
      ctx.strokeStyle = CC.resolvePaint(ctx, el.stroke);
      ctx.lineWidth = el.stroke_width ?? 1;
      ctx.lineJoin = el.line_join || "round";
      ctx.lineCap = el.line_cap || "round";
      if (el.dash) ctx.setLineDash(el.dash);
      ctx.stroke(path);
      ctx.setLineDash([]);
    }
  }

  const ELEMENT_DRAWERS = {
    stroke: (ctx, el, env) => CC.drawStroke(ctx, el, env),
    path: (ctx, el) => strokeAndFill(ctx, el, new Path2D(el.d)),
    ellipse: (ctx, el) => {
      const p = new Path2D();
      p.ellipse(el.cx, el.cy, Math.max(0, el.rx), Math.max(0, el.ry ?? el.rx), ((el.rotation ?? 0) * Math.PI) / 180, 0, Math.PI * 2);
      strokeAndFill(ctx, el, p);
    },
    rect: (ctx, el) => {
      const p = new Path2D();
      if (el.radius && p.roundRect) p.roundRect(el.x, el.y, el.w, el.h, el.radius);
      else p.rect(el.x, el.y, el.w, el.h);
      strokeAndFill(ctx, el, p);
    },
    polygon: (ctx, el) => {
      const closed = el.closed ?? true;
      const p = el.smooth ? CC.smoothPath(el.points, closed, el.tension) : (() => {
        const q = new Path2D();
        el.points.forEach((pt, i) => (i ? q.lineTo(pt[0], pt[1]) : q.moveTo(pt[0], pt[1])));
        if (closed) q.closePath();
        return q;
      })();
      strokeAndFill(ctx, el, p);
    },
    text: (ctx, el) => {
      ctx.font = `${el.weight || "bold"} ${el.size || 32}px ${el.font || "'Noto Sans', 'DejaVu Sans', sans-serif"}`;
      ctx.textAlign = el.align || "left";
      ctx.textBaseline = el.baseline || "alphabetic";
      if (el.stroke) {
        ctx.lineJoin = "round";
        ctx.lineWidth = el.stroke_width ?? 4;
        ctx.strokeStyle = CC.resolvePaint(ctx, el.stroke);
        ctx.strokeText(el.text, el.x, el.y);
      }
      ctx.fillStyle = CC.resolvePaint(ctx, el.fill ?? "#000");
      ctx.fillText(el.text, el.x, el.y);
    },
    image: (ctx, el, env) => {
      const img = env.images[el.src];
      if (!img) return;
      ctx.drawImage(img, el.x ?? 0, el.y ?? 0, el.w ?? img.width, el.h ?? img.height);
    },
    group: (ctx, el, env) => {
      if (!el.isolate) {
        for (const child of el.elements || []) drawElement(ctx, child, env);
        return;
      }
      // Isolated group = sub-layer: children composite against each other only
      // (in-group erase / alpha-lock / multiply never touch the parent layer),
      // then the result is blended onto the parent with the group's opacity+blend.
      const sub = CC.acquireCanvas(env.pxWidth, env.pxHeight);
      const sctx = sub.getContext("2d");
      sctx.setTransform(ctx.getTransform());
      for (const child of el.elements || []) drawElement(sctx, child, env);
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.filter = "none";
      ctx.drawImage(sub, 0, 0);
      ctx.restore();
      CC.releaseCanvas(sub);
    },
    speed_lines: (ctx, el, env) => CC.vfx.speedLines(ctx, el, env),
    smear: (ctx, el, env) => CC.vfx.smear(ctx, el, env),
    halftone: (ctx, el, env) => CC.vfx.halftone(ctx, el, env),
    sparkle: (ctx, el, env) => CC.vfx.sparkle(ctx, el, env),
  };
  CC.ELEMENT_DRAWERS = ELEMENT_DRAWERS;

  function drawElement(ctx, el, env) {
    if (!el || el.visible === false) return;
    const fn = ELEMENT_DRAWERS[el.type];
    if (!fn) throw new Error(`Unknown element type: ${el.type}`);
    ctx.save();
    applyTransform(ctx, el.transform);
    if (el.type !== "stroke") {
      ctx.globalAlpha *= el.opacity ?? 1;
      ctx.globalCompositeOperation = CC.blendOp(el.blend);
    }
    if (el.blur) ctx.filter = `blur(${el.blur * env.scale}px)`;
    fn(ctx, el, env);
    ctx.restore();
  }
  CC.drawElement = drawElement;

  // ---------------------------------------------------------------------------
  // Layer filters
  // ---------------------------------------------------------------------------
  function copyCanvas(src) {
    const c = CC.acquireCanvas(src.width, src.height);
    c.getContext("2d").drawImage(src, 0, 0);
    return c;
  }

  function gaussianBlur(canvas, radius) {
    if (!radius || radius <= 0) return;
    const tmp = copyCanvas(canvas);
    const ctx = canvas.getContext("2d");
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.filter = `blur(${radius}px)`;
    ctx.drawImage(tmp, 0, 0);
    ctx.restore();
    CC.releaseCanvas(tmp);
  }
  CC.gaussianBlur = gaussianBlur;

  /** Directional motion blur: average of N copies offset along `angle`. */
  function motionBlur(canvas, angleDeg, distance, samples) {
    if (!distance || distance <= 0) return;
    const n = Math.max(3, Math.min(48, samples || Math.ceil(distance / 2)));
    const tmp = copyCanvas(canvas);
    const ctx = canvas.getContext("2d");
    const a = (angleDeg * Math.PI) / 180;
    const dx = Math.cos(a) * distance, dy = Math.sin(a) * distance;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.globalCompositeOperation = "lighter"; // premultiplied sum => average
    ctx.globalAlpha = 1 / n;
    for (let i = 0; i < n; i++) {
      const t = i / (n - 1) - 0.5;
      ctx.drawImage(tmp, dx * t, dy * t);
    }
    ctx.restore();
    CC.releaseCanvas(tmp);
  }
  CC.motionBlur = motionBlur;

  function glow(canvas, radius, strength, color) {
    const tmp = copyCanvas(canvas);
    const tctx = tmp.getContext("2d");
    if (color) {
      tctx.globalCompositeOperation = "source-in";
      tctx.fillStyle = color;
      tctx.fillRect(0, 0, tmp.width, tmp.height);
    }
    const blurred = CC.acquireCanvas(canvas.width, canvas.height);
    const bctx = blurred.getContext("2d");
    bctx.filter = `blur(${radius}px)`;
    bctx.drawImage(tmp, 0, 0);
    bctx.filter = "none";
    const ctx = canvas.getContext("2d");
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "lighter";
    let s = strength ?? 1;
    while (s > 0) {
      ctx.globalAlpha = Math.min(1, s);
      ctx.drawImage(blurred, 0, 0);
      s -= 1;
    }
    ctx.restore();
    CC.releaseCanvas(tmp);
    CC.releaseCanvas(blurred);
  }
  CC.glow = glow;

  function applyLayerFilters(canvas, filters, env) {
    if (!filters) return;
    const k = env.scale;
    if (filters.blur) gaussianBlur(canvas, filters.blur * k);
    if (filters.motion_blur) {
      const mb = filters.motion_blur;
      motionBlur(canvas, mb.angle ?? 0, (mb.distance ?? 0) * k, mb.samples);
    }
    if (filters.glow) glow(canvas, (filters.glow.radius ?? 12) * k, filters.glow.strength ?? 1, filters.glow.color);
    if (filters.css) {
      // escape hatch: any CSS filter string e.g. "saturate(1.3) contrast(1.1)"
      const tmp = copyCanvas(canvas);
      const ctx = canvas.getContext("2d");
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.filter = filters.css;
      ctx.drawImage(tmp, 0, 0);
      ctx.restore();
      CC.releaseCanvas(tmp);
    }
  }

  // ---------------------------------------------------------------------------
  // Layer rendering + composite
  // ---------------------------------------------------------------------------
  CC.renderLayer = function (layer, env) {
    const canvas = CC.acquireCanvas(env.pxWidth, env.pxHeight);
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    // screen_space layers (titles, HUD, overlays) ignore the camera entirely
    const cam = layer.screen_space
      ? new DOMMatrix().scaleSelf(env.scale, env.scale)
      : CC.cameraMatrix(env.camera, layer.parallax ?? 1, env);
    ctx.setTransform(cam);
    if (layer.transform) CC.applyTransform(ctx, layer.transform);
    for (const el of layer.elements || []) drawElement(ctx, el, env);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    applyLayerFilters(canvas, layer.filters, env);
    return canvas;
  };

  /**
   * Composite a layer stack (bottom -> top) onto `target`.
   * A layer with `clip_to: "<layer id>"` is alpha-masked by that layer's
   * pixels before blending (FlipaClip "clipping layer" / Harmony "inherit
   * alpha"). This is how cel shadows and rim lights stay inside the paint.
   */
  CC.compositeLayers = function (target, layers, env) {
    const tctx = target.getContext("2d");
    const rendered = {};
    for (const layer of layers) {
      if (layer.visible === false) continue;
      const canvas = CC.renderLayer(layer, env);
      if (layer.clip_to) {
        const base = rendered[layer.clip_to];
        if (base) {
          const c = canvas.getContext("2d");
          c.save();
          c.globalCompositeOperation = "destination-in";
          c.drawImage(base, 0, 0);
          c.restore();
        } else {
          console.warn(`clip_to target '${layer.clip_to}' not found below layer '${layer.id}'`);
        }
      }
      tctx.save();
      tctx.setTransform(1, 0, 0, 1, 0, 0);
      tctx.globalAlpha = layer.opacity ?? 1;
      tctx.globalCompositeOperation = CC.blendOp(layer.blend);
      tctx.drawImage(canvas, 0, 0);
      tctx.restore();
      if (layer.id) {
        if (rendered[layer.id]) CC.releaseCanvas(rendered[layer.id]);
        rendered[layer.id] = canvas;
      } else {
        CC.releaseCanvas(canvas);
      }
    }
    for (const k of Object.keys(rendered)) CC.releaseCanvas(rendered[k]);
  };
})(window);
