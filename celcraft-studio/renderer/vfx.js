/*
 * CelCraft Studio - vfx.js
 *
 * Anime / cartoon effects elements and the filmic post-processing chain.
 *
 * Elements: speed_lines (radial focus lines or parallel action lines),
 *           smear (multi-circle swept smear polygon), halftone (screen-tone
 *           dots with gradient falloff), sparkle (4-point star glints).
 * Post:     impact frame, colour grade, bloom, chromatic aberration,
 *           vignette, paper texture, animated film/cel grain, letterbox.
 */
(function (global) {
  "use strict";
  const CC = (global.CelCraft = global.CelCraft || {});
  const vfx = (CC.vfx = {});

  // ---------------------------------------------------------------------------
  // Speed lines
  // ---------------------------------------------------------------------------
  vfx.speedLines = function (ctx, el, env) {
    const rand = CC.rng((el.seed ?? 1) + (el.animate === false ? 0 : env.frame * 7919));
    const color = CC.resolvePaint(ctx, el.color || "#ffffff");
    const count = el.count ?? 80;
    const width = el.width ?? 6;
    ctx.fillStyle = color;
    if ((el.mode || "radial") === "radial") {
      // Focus lines: tapered wedges pointing at (cx, cy) from outside the frame.
      const cx = el.cx ?? env.width / 2, cy = el.cy ?? env.height / 2;
      const outer = el.outer_radius ?? Math.hypot(env.width, env.height);
      const inner = el.inner_radius ?? Math.min(env.width, env.height) * 0.3;
      for (let i = 0; i < count; i++) {
        const a = (i / count) * Math.PI * 2 + (rand() - 0.5) * ((Math.PI * 2) / count) * 1.6;
        const r0 = inner * (0.75 + rand() * (el.jitter ?? 0.6));
        const w = width * (0.3 + rand() * 1.4);
        const ca = Math.cos(a), sa = Math.sin(a);
        const nx = -sa, ny = ca;
        ctx.beginPath();
        ctx.moveTo(cx + ca * r0, cy + sa * r0);
        ctx.lineTo(cx + ca * outer + nx * w, cy + sa * outer + ny * w);
        ctx.lineTo(cx + ca * outer - nx * w, cy + sa * outer - ny * w);
        ctx.closePath();
        ctx.fill();
      }
    } else {
      // Parallel action lines across a region, tapered at both ends.
      const a = ((el.angle ?? 0) * Math.PI) / 180;
      const ca = Math.cos(a), sa = Math.sin(a);
      const nx = -sa, ny = ca;
      const x = el.x ?? 0, y = el.y ?? 0, w = el.w ?? env.width, h = el.h ?? env.height;
      const cxr = x + w / 2, cyr = y + h / 2;
      const span = Math.hypot(w, h);
      const minLen = el.min_length ?? span * 0.15, maxLen = el.length ?? span * 0.5;
      for (let i = 0; i < count; i++) {
        const off = (rand() - 0.5) * span;
        const along = (rand() - 0.5) * span;
        const L = minLen + rand() * (maxLen - minLen);
        const lw = width * (0.25 + rand());
        const mx = cxr + nx * off + ca * along, my = cyr + ny * off + sa * along;
        ctx.beginPath();
        ctx.moveTo(mx - ca * L / 2, my - sa * L / 2);
        ctx.quadraticCurveTo(mx + nx * lw, my + ny * lw, mx + ca * L / 2, my + sa * L / 2);
        ctx.quadraticCurveTo(mx - nx * lw, my - ny * lw, mx - ca * L / 2, my - sa * L / 2);
        ctx.fill();
      }
    }
  };

  // ---------------------------------------------------------------------------
  // Smear: swept union of circles along a trail (classic animation smear frame)
  // ---------------------------------------------------------------------------
  vfx.smear = function (ctx, el) {
    const pts = el.points || [];
    if (!pts.length) return;
    const radii = el.radii || pts.map(() => el.radius ?? 20);
    const R = (i) => radii[Math.min(i, radii.length - 1)];
    ctx.fillStyle = CC.resolvePaint(ctx, el.color || el.fill || "#000");
    // de-duplicate coincident points (they have no direction)
    const P = [], RR = [];
    for (let i = 0; i < pts.length; i++) {
      if (P.length && Math.hypot(pts[i][0] - P[P.length - 1][0], pts[i][1] - P[P.length - 1][1]) < 0.5) { RR[RR.length - 1] = Math.max(RR[RR.length - 1], R(i)); continue; }
      P.push(pts[i]); RR.push(R(i));
    }
    const path = new Path2D();
    if (P.length === 1) {
      path.arc(P[0][0], P[0][1], RR[0], 0, Math.PI * 2);
      ctx.fill(path);
      return;
    }
    // Swept hull: left side forward, round head, right side back, round tail.
    const left = [], right = [];
    for (let i = 0; i < P.length; i++) {
      const a = P[Math.max(0, i - 1)], b = P[Math.min(P.length - 1, i + 1)];
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const L = Math.hypot(dx, dy) || 1;
      const nx = -dy / L, ny = dx / L;
      left.push([P[i][0] + nx * RR[i], P[i][1] + ny * RR[i]]);
      right.push([P[i][0] - nx * RR[i], P[i][1] - ny * RR[i]]);
    }
    const n = P.length - 1;
    const headAng = Math.atan2(left[n][1] - P[n][1], left[n][0] - P[n][0]);
    const tailAng = Math.atan2(right[0][1] - P[0][1], right[0][0] - P[0][0]);
    path.moveTo(left[0][0], left[0][1]);
    for (let i = 1; i <= n; i++) path.lineTo(left[i][0], left[i][1]);
    path.arc(P[n][0], P[n][1], RR[n], headAng, headAng - Math.PI, true);
    for (let i = n; i >= 0; i--) path.lineTo(right[i][0], right[i][1]);
    path.arc(P[0][0], P[0][1], RR[0], tailAng, tailAng - Math.PI, true);
    path.closePath();
    ctx.fill(path, "nonzero");
    // head circle guarantees a full round leading edge
    const head = new Path2D();
    head.arc(P[n][0], P[n][1], RR[n], 0, Math.PI * 2);
    ctx.fill(head);
  };

  // ---------------------------------------------------------------------------
  // Halftone screen tone
  // ---------------------------------------------------------------------------
  vfx.halftone = function (ctx, el, env) {
    const x = el.x ?? 0, y = el.y ?? 0, w = el.w ?? env.width, h = el.h ?? env.height;
    const spacing = Math.max(2, el.spacing ?? 10);
    const maxR = el.max_radius ?? spacing * 0.55;
    const minR = el.min_radius ?? 0;
    const ang = ((el.angle ?? 45) * Math.PI) / 180;
    // falloff direction: from (x0,y0) full size to (x1,y1) min size
    const x0 = el.x0 ?? x, y0 = el.y0 ?? y, x1 = el.x1 ?? x + w, y1 = el.y1 ?? y + h;
    const gdx = x1 - x0, gdy = y1 - y0, gl2 = gdx * gdx + gdy * gdy || 1;
    ctx.fillStyle = CC.resolvePaint(ctx, el.color || "#000");
    const cxr = x + w / 2, cyr = y + h / 2;
    const R = Math.hypot(w, h) / 2 + spacing;
    const ca = Math.cos(ang), sa = Math.sin(ang);
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    const path = new Path2D();
    for (let v = -R; v <= R; v += spacing) {
      for (let u = -R; u <= R; u += spacing) {
        const px = cxr + u * ca - v * sa, py = cyr + u * sa + v * ca;
        if (px < x - spacing || px > x + w + spacing || py < y - spacing || py > y + h + spacing) continue;
        let t = ((px - x0) * gdx + (py - y0) * gdy) / gl2;
        t = Math.max(0, Math.min(1, t));
        const r = maxR + (minR - maxR) * t;
        if (r < 0.3) continue;
        path.moveTo(px + r, py);
        path.arc(px, py, r, 0, Math.PI * 2);
      }
    }
    ctx.fill(path);
    ctx.restore();
  };

  // ---------------------------------------------------------------------------
  // Sparkles / glints
  // ---------------------------------------------------------------------------
  vfx.sparkle = function (ctx, el) {
    const pts = el.points || [[el.x ?? 0, el.y ?? 0]];
    const size = el.size ?? 16;
    ctx.fillStyle = CC.resolvePaint(ctx, el.color || "#fff");
    for (const p of pts) {
      const s = size * (p[2] ?? 1);
      ctx.beginPath();
      ctx.moveTo(p[0], p[1] - s);
      ctx.quadraticCurveTo(p[0], p[1], p[0] + s * 0.6, p[1]);
      ctx.quadraticCurveTo(p[0], p[1], p[0], p[1] + s);
      ctx.quadraticCurveTo(p[0], p[1], p[0] - s * 0.6, p[1]);
      ctx.quadraticCurveTo(p[0], p[1], p[0], p[1] - s);
      ctx.fill();
    }
  };

  // ---------------------------------------------------------------------------
  // Post-processing
  // ---------------------------------------------------------------------------
  const grainTiles = new Map();
  function grainTile(seed, monochrome) {
    const key = seed + (monochrome ? "m" : "c");
    if (grainTiles.has(key)) return grainTiles.get(key);
    const size = 384;
    const c = CC.makeCanvas(size, size);
    const g = c.getContext("2d");
    const img = g.createImageData(size, size);
    const rand = CC.rng(seed * 92821 + 7);
    for (let i = 0; i < img.data.length; i += 4) {
      const n = CC.gaussian(rand) * 0.5;
      const v = Math.max(0, Math.min(255, 128 + n * 128));
      img.data[i] = v;
      img.data[i + 1] = monochrome ? v : Math.max(0, Math.min(255, 128 + CC.gaussian(rand) * 64));
      img.data[i + 2] = monochrome ? v : Math.max(0, Math.min(255, 128 + CC.gaussian(rand) * 64));
      img.data[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    grainTiles.set(key, c);
    return c;
  }

  let paperTile = null;
  function paperTexture() {
    if (paperTile) return paperTile;
    const size = 512;
    const c = CC.makeCanvas(size, size);
    const g = c.getContext("2d");
    const rand = CC.rng(424242);
    g.fillStyle = "#808080";
    g.fillRect(0, 0, size, size);
    // fibres
    for (let i = 0; i < 2600; i++) {
      const x = rand() * size, y = rand() * size, a = rand() * Math.PI, l = 4 + rand() * 18;
      g.strokeStyle = rand() < 0.5 ? "rgba(255,255,255,0.10)" : "rgba(0,0,0,0.10)";
      g.lineWidth = 0.6 + rand();
      g.beginPath();
      g.moveTo(x, y);
      g.lineTo(x + Math.cos(a) * l, y + Math.sin(a) * l);
      g.stroke();
    }
    // tooth blotches (drawn tiled so the texture wraps seamlessly)
    for (let i = 0; i < 900; i++) {
      const x = rand() * size, y = rand() * size, r = 1 + rand() * 6;
      g.fillStyle = rand() < 0.5 ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)";
      for (const ox of [-size, 0, size]) for (const oy of [-size, 0, size]) {
        g.beginPath();
        g.arc(x + ox, y + oy, r, 0, Math.PI * 2);
        g.fill();
      }
    }
    paperTile = c;
    return c;
  }

  function redraw(canvas, filter, op, alpha) {
    const tmp = CC.acquireCanvas(canvas.width, canvas.height);
    const t = tmp.getContext("2d");
    t.filter = filter || "none";
    t.drawImage(canvas, 0, 0);
    t.filter = "none";
    const ctx = canvas.getContext("2d");
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (!op || op === "copy") ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.globalCompositeOperation = !op || op === "copy" ? "source-over" : op;
    ctx.globalAlpha = alpha ?? 1;
    ctx.drawImage(tmp, 0, 0);
    ctx.restore();
    CC.releaseCanvas(tmp);
  }

  vfx.postProcess = function (canvas, post, env) {
    if (!post) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height, k = env.scale;

    // 1. Impact frame (high-contrast flash frame used on hits)
    if (post.impact) {
      const im = typeof post.impact === "string" ? { mode: post.impact } : post.impact;
      const mode = im.mode || "mono";
      if (mode === "invert") {
        redraw(canvas, "invert(1)");
      } else if (mode === "flash") {
        ctx.save();
        ctx.globalCompositeOperation = "screen";
        ctx.globalAlpha = im.strength ?? 0.85;
        ctx.fillStyle = im.color_a || "#ffffff";
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
      } else {
        // two-tone: threshold luminance into color_a (light) / color_b (dark)
        const img = ctx.getImageData(0, 0, W, H);
        const d = img.data;
        const A = hexToRgb(im.color_a || "#ffffff"), B = hexToRgb(im.color_b || "#0a0a12");
        const thr = (im.threshold ?? 0.5) * 255;
        const invert = im.invert ?? true;
        for (let i = 0; i < d.length; i += 4) {
          const l = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
          const light = invert ? l < thr : l >= thr;
          const c = light ? A : B;
          d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 255;
        }
        ctx.putImageData(img, 0, 0);
      }
    }

    // 2. Grade
    if (post.grade) {
      const g = post.grade;
      const parts = [];
      if (g.brightness != null) parts.push(`brightness(${g.brightness})`);
      if (g.contrast != null) parts.push(`contrast(${g.contrast})`);
      if (g.saturation != null) parts.push(`saturate(${g.saturation})`);
      if (g.hue_rotate) parts.push(`hue-rotate(${g.hue_rotate}deg)`);
      if (parts.length) redraw(canvas, parts.join(" "));
      if (g.tint && g.tint_amount) {
        ctx.save();
        ctx.globalCompositeOperation = g.tint_blend ? CC.blendOp(g.tint_blend) : "soft-light";
        ctx.globalAlpha = g.tint_amount;
        ctx.fillStyle = g.tint;
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
      }
    }

    // 3. Bloom: bright-pass approximation (contrast-crushed + blurred, screen)
    if (post.bloom && (post.bloom.strength ?? 0) > 0) {
      const b = post.bloom;
      const thr = b.threshold ?? 0.7;
      const crush = 1 + thr * 4;
      const tmp = CC.acquireCanvas(W, H);
      const t = tmp.getContext("2d");
      t.filter = `brightness(${1 - thr * 0.5}) contrast(${crush}) blur(${(b.radius ?? 18) * k}px)`;
      t.drawImage(canvas, 0, 0);
      t.filter = "none";
      ctx.save();
      ctx.globalCompositeOperation = "screen";
      ctx.globalAlpha = Math.min(1, b.strength);
      ctx.drawImage(tmp, 0, 0);
      ctx.restore();
      CC.releaseCanvas(tmp);
    }

    // 4. Chromatic aberration (radial-ish channel split)
    if (post.chromatic_aberration) {
      const px = Math.round((post.chromatic_aberration.px ?? post.chromatic_aberration) * k);
      if (px > 0) {
        const src = ctx.getImageData(0, 0, W, H);
        const out = ctx.createImageData(W, H);
        const s = src.data, o = out.data;
        for (let y = 0; y < H; y++) {
          for (let x = 0; x < W; x++) {
            const i = (y * W + x) * 4;
            const xr = Math.min(W - 1, x + px), xb = Math.max(0, x - px);
            o[i] = s[(y * W + xr) * 4];
            o[i + 1] = s[i + 1];
            o[i + 2] = s[(y * W + xb) * 4 + 2];
            o[i + 3] = Math.max(s[i + 3], s[(y * W + xr) * 4 + 3], s[(y * W + xb) * 4 + 3]);
          }
        }
        ctx.putImageData(out, 0, 0);
      }
    }

    // 5. Vignette
    if (post.vignette && (post.vignette.amount ?? 0) > 0) {
      const v = post.vignette;
      const g = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * (v.inner ?? 0.45), W / 2, H / 2, Math.hypot(W, H) / 2);
      g.addColorStop(0, "rgba(0,0,0,0)");
      g.addColorStop(1, v.color || "rgba(10,6,20,1)");
      ctx.save();
      ctx.globalCompositeOperation = "multiply";
      ctx.globalAlpha = v.amount;
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
      ctx.restore();
    }

    // 6. Paper texture (static; it is the "paper" the cels are photographed on)
    if (post.paper && (post.paper.amount ?? 0) > 0) {
      const pat = ctx.createPattern(paperTexture(), "repeat");
      ctx.save();
      ctx.globalCompositeOperation = "overlay";
      ctx.globalAlpha = post.paper.amount;
      ctx.setTransform(post.paper.scale ?? 1, 0, 0, post.paper.scale ?? 1, 0, 0);
      ctx.fillStyle = pat;
      ctx.fillRect(0, 0, W / (post.paper.scale ?? 1), H / (post.paper.scale ?? 1));
      ctx.restore();
    }

    // 7. Animated grain (changes every frame, like film / camera noise)
    if (post.grain && (post.grain.amount ?? 0) > 0) {
      const gr = post.grain;
      const seed = (gr.seed ?? 3) + (gr.animate === false ? 0 : env.frame);
      const tile = grainTile(seed % 8, gr.monochrome ?? true);
      const rand = CC.rng(seed * 31 + 1);
      const pat = ctx.createPattern(tile, "repeat");
      const sz = gr.size ?? 1;
      ctx.save();
      ctx.globalCompositeOperation = "overlay";
      ctx.globalAlpha = gr.amount;
      ctx.setTransform(sz * k, 0, 0, sz * k, -rand() * 384, -rand() * 384);
      ctx.fillStyle = pat;
      ctx.fillRect(0, 0, (W + 800) / (sz * k), (H + 800) / (sz * k));
      ctx.restore();
    }

    // 8. Letterbox
    if (post.letterbox) {
      const ratio = post.letterbox.ratio ?? post.letterbox;
      const visibleH = W / ratio;
      const bar = Math.max(0, (H - visibleH) / 2);
      if (bar > 0) {
        ctx.save();
        ctx.fillStyle = post.letterbox.color || "#000";
        ctx.fillRect(0, 0, W, bar);
        ctx.fillRect(0, H - bar, W, bar);
        ctx.restore();
      }
    }
  };

  function hexToRgb(hex) {
    let h = hex.replace("#", "");
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    const n = parseInt(h.slice(0, 6), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  CC.hexToRgb = hexToRgb;
})(window);
