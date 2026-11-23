/*
 * CelCraft Studio - brushes.js
 *
 * Pressure-sensitive brush engine for the CelCraft renderer.
 *
 * Contains a self-contained implementation of the "perfect-freehand" stroke
 * outline algorithm (original algorithm by Steve Ruiz, MIT licensed,
 * https://github.com/steveruizok/perfect-freehand). It is re-implemented here
 * so the renderer runs fully offline with no CDN dependency.
 *
 * Brushes: gpen, ink, pencil, charcoal, marker, drybrush, spray, airbrush.
 * Every random decision is driven by a seeded PRNG so renders are
 * deterministic (important for controlled line boil and test stability).
 */
(function (global) {
  "use strict";
  const CC = (global.CelCraft = global.CelCraft || {});

  // ---------------------------------------------------------------------------
  // Deterministic randomness
  // ---------------------------------------------------------------------------
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hashString(str) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }
  function gaussian(rand) {
    let u = 0, v = 0;
    while (u === 0) u = rand();
    while (v === 0) v = rand();
    return Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
  }
  CC.rng = mulberry32;
  CC.hashString = hashString;
  CC.gaussian = gaussian;

  // ---------------------------------------------------------------------------
  // Vector helpers
  // ---------------------------------------------------------------------------
  const neg = (A) => [-A[0], -A[1]];
  const add = (A, B) => [A[0] + B[0], A[1] + B[1]];
  const sub = (A, B) => [A[0] - B[0], A[1] - B[1]];
  const mul = (A, n) => [A[0] * n, A[1] * n];
  const per = (A) => [A[1], -A[0]];
  const dpr = (A, B) => A[0] * B[0] + A[1] * B[1];
  const isEqual = (A, B) => A[0] === B[0] && A[1] === B[1];
  const len = (A) => Math.hypot(A[0], A[1]);
  const uni = (A) => { const l = len(A); return l === 0 ? [0, 0] : [A[0] / l, A[1] / l]; };
  const dist = (A, B) => Math.hypot(A[1] - B[1], A[0] - B[0]);
  const dist2 = (A, B) => { const dx = A[0] - B[0], dy = A[1] - B[1]; return dx * dx + dy * dy; };
  const lrp = (A, B, t) => add(A, mul(sub(B, A), t));
  const prj = (A, B, c) => add(A, mul(B, c));
  function rotAround(A, C, r) {
    const s = Math.sin(r), c = Math.cos(r);
    const px = A[0] - C[0], py = A[1] - C[1];
    return [px * c - py * s + C[0], px * s + py * c + C[1]];
  }
  CC.vec = { neg, add, sub, mul, per, dpr, len, uni, dist, lrp, rotAround };

  // ---------------------------------------------------------------------------
  // perfect-freehand (re-implementation)
  // ---------------------------------------------------------------------------
  const RATE_OF_PRESSURE_CHANGE = 0.275;
  const FIXED_PI = Math.PI + 0.0001;
  const EASINGS = {
    linear: (t) => t,
    easeInQuad: (t) => t * t,
    easeOutQuad: (t) => t * (2 - t),
    easeInCubic: (t) => t * t * t,
    easeOutCubic: (t) => --t * t * t + 1,
    easeInOutCubic: (t) => (t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1),
    easeOutSine: (t) => Math.sin((t * Math.PI) / 2),
    easeInSine: (t) => 1 - Math.cos((t * Math.PI) / 2),
  };
  CC.strokeEasings = EASINGS;

  function getStrokeRadius(size, thinning, pressure, easing) {
    return size * easing(0.5 - thinning * (0.5 - pressure));
  }

  function getStrokePoints(points, options) {
    const streamline = options.streamline ?? 0.5;
    const size = options.size ?? 16;
    const isComplete = options.last ?? false;
    if (points.length === 0) return [];
    const t = 0.15 + (1 - streamline) * 0.85;
    let pts = points.map((p) => [p[0], p[1], p.length > 2 && p[2] != null ? p[2] : 0.5]);
    if (pts.length === 2) {
      const last = pts[1];
      pts = pts.slice(0, -1);
      for (let i = 1; i < 5; i++) {
        const q = lrp(pts[0], last, i / 4);
        pts.push([q[0], q[1], pts[0][2] + (last[2] - pts[0][2]) * (i / 4)]);
      }
    }
    if (pts.length === 1) pts = [...pts, [pts[0][0] + 1, pts[0][1] + 1, pts[0][2]]];

    const strokePoints = [
      { point: [pts[0][0], pts[0][1]], pressure: pts[0][2] >= 0 ? pts[0][2] : 0.25, vector: [1, 1], distance: 0, runningLength: 0 },
    ];
    let hasReachedMinimumLength = false;
    let runningLength = 0;
    let prev = strokePoints[0];
    const max = pts.length - 1;
    for (let i = 1; i < pts.length; i++) {
      const raw = [pts[i][0], pts[i][1]];
      const point = isComplete && i === max ? raw : lrp(prev.point, raw, t);
      if (isEqual(prev.point, point)) continue;
      const distance = dist(point, prev.point);
      runningLength += distance;
      if (i < max && !hasReachedMinimumLength) {
        if (runningLength < size) continue;
        hasReachedMinimumLength = true;
      }
      prev = { point, pressure: pts[i][2] >= 0 ? pts[i][2] : 0.5, vector: uni(sub(prev.point, point)), distance, runningLength };
      strokePoints.push(prev);
    }
    strokePoints[0].vector = strokePoints[1]?.vector || [0, 0];
    return strokePoints;
  }

  function getStrokeOutlinePoints(points, options) {
    const size = options.size ?? 16;
    const smoothing = options.smoothing ?? 0.5;
    const thinning = options.thinning ?? 0.5;
    const simulatePressure = options.simulatePressure ?? true;
    const easing = options.easing ?? ((t) => t);
    const start = options.start ?? {};
    const end = options.end ?? {};
    const isComplete = options.last ?? false;
    const capStart = start.cap ?? true;
    const taperStartEase = start.easing ?? ((t) => t * (2 - t));
    const capEnd = end.cap ?? true;
    const taperEndEase = end.easing ?? ((t) => --t * t * t + 1);

    if (points.length === 0 || size <= 0) return [];
    const totalLength = points[points.length - 1].runningLength;
    const taperStart = start.taper === false ? 0 : start.taper === true ? Math.max(size, totalLength) : start.taper || 0;
    const taperEnd = end.taper === false ? 0 : end.taper === true ? Math.max(size, totalLength) : end.taper || 0;
    const minDistance = Math.pow(size * smoothing, 2);
    const leftPts = [];
    const rightPts = [];

    let prevPressure = points.slice(0, 10).reduce((acc, curr) => {
      let pressure = curr.pressure;
      if (simulatePressure) {
        const sp = Math.min(1, curr.distance / size);
        const rp = Math.min(1, 1 - sp);
        pressure = Math.min(1, acc + (rp - acc) * (sp * RATE_OF_PRESSURE_CHANGE));
      }
      return (acc + pressure) / 2;
    }, points[0].pressure);

    let radius = getStrokeRadius(size, thinning, points[points.length - 1].pressure, easing);
    let firstRadius;
    let prevVector = points[0].vector;
    let pl = points[0].point, pr = pl, tl = pl, tr = pr;
    let isPrevPointSharpCorner = false;

    for (let i = 0; i < points.length; i++) {
      let { pressure } = points[i];
      const { point, vector, distance, runningLength } = points[i];
      if (i < points.length - 1 && totalLength - runningLength < 3) continue;

      if (thinning) {
        if (simulatePressure) {
          const sp = Math.min(1, distance / size);
          const rp = Math.min(1, 1 - sp);
          pressure = Math.min(1, prevPressure + (rp - prevPressure) * (sp * RATE_OF_PRESSURE_CHANGE));
        }
        radius = getStrokeRadius(size, thinning, pressure, easing);
      } else {
        radius = size / 2;
      }
      if (firstRadius === undefined) firstRadius = radius;

      const ts = runningLength < taperStart ? taperStartEase(runningLength / taperStart) : 1;
      const te = totalLength - runningLength < taperEnd ? taperEndEase((totalLength - runningLength) / taperEnd) : 1;
      radius = Math.max(0.01, radius * Math.min(ts, te));

      const nextVector = (i < points.length - 1 ? points[i + 1] : points[i]).vector;
      const nextDpr = i < points.length - 1 ? dpr(vector, nextVector) : 1.0;
      const prevDpr = dpr(vector, prevVector);
      const isPointSharpCorner = prevDpr < 0 && !isPrevPointSharpCorner;
      const isNextPointSharpCorner = nextDpr !== null && nextDpr < 0;

      if (isPointSharpCorner || isNextPointSharpCorner) {
        const offset = mul(per(prevVector), radius);
        const step = 1 / 13;
        for (let t = 0; t <= 1; t += step) {
          tl = rotAround(sub(point, offset), point, FIXED_PI * t);
          leftPts.push(tl);
          tr = rotAround(add(point, offset), point, FIXED_PI * -t);
          rightPts.push(tr);
        }
        pl = tl; pr = tr;
        if (isNextPointSharpCorner) isPrevPointSharpCorner = true;
        continue;
      }
      isPrevPointSharpCorner = false;

      if (i === points.length - 1) {
        const offset = mul(per(vector), radius);
        leftPts.push(sub(point, offset));
        rightPts.push(add(point, offset));
        continue;
      }
      const offset = mul(per(lrp(nextVector, vector, nextDpr)), radius);
      tl = sub(point, offset);
      if (i <= 1 || dist2(pl, tl) > minDistance) { leftPts.push(tl); pl = tl; }
      tr = add(point, offset);
      if (i <= 1 || dist2(pr, tr) > minDistance) { rightPts.push(tr); pr = tr; }
      prevPressure = pressure;
      prevVector = vector;
    }

    const firstPoint = points[0].point;
    const lastPoint = points.length > 1 ? points[points.length - 1].point : add(points[0].point, [1, 1]);
    const startCap = [];
    const endCap = [];

    if (points.length === 1) {
      if (!(taperStart || taperEnd) || isComplete) {
        const s = prj(firstPoint, uni(per(sub(firstPoint, lastPoint))), -(firstRadius || radius));
        const dotPts = [];
        for (let t = 1 / 13; t <= 1; t += 1 / 13) dotPts.push(rotAround(s, firstPoint, FIXED_PI * 2 * t));
        return dotPts;
      }
    } else {
      if (taperStart || (taperEnd && points.length === 1)) {
        // no start cap
      } else if (capStart) {
        for (let t = 1 / 13; t <= 1; t += 1 / 13) startCap.push(rotAround(rightPts[0], firstPoint, FIXED_PI * t));
      } else {
        const cornersVector = sub(leftPts[0], rightPts[0]);
        const offsetA = mul(cornersVector, 0.5);
        const offsetB = mul(cornersVector, 0.51);
        startCap.push(sub(firstPoint, offsetA), sub(firstPoint, offsetB), add(firstPoint, offsetB), add(firstPoint, offsetA));
      }
      const direction = per(neg(points[points.length - 1].vector));
      if (taperEnd || (taperStart && points.length === 1)) {
        endCap.push(lastPoint);
      } else if (capEnd) {
        const s = prj(lastPoint, direction, radius);
        for (let t = 1 / 29; t < 1; t += 1 / 29) endCap.push(rotAround(s, lastPoint, FIXED_PI * 3 * t));
      } else {
        endCap.push(add(lastPoint, mul(direction, radius)), add(lastPoint, mul(direction, radius * 0.99)), sub(lastPoint, mul(direction, radius * 0.99)), sub(lastPoint, mul(direction, radius)));
      }
    }
    return leftPts.concat(endCap, rightPts.reverse(), startCap);
  }

  function getStroke(points, options) {
    return getStrokeOutlinePoints(getStrokePoints(points, options), options);
  }
  CC.getStroke = getStroke;
  CC.getStrokePoints = getStrokePoints;
  CC.getStrokeOutlinePoints = getStrokeOutlinePoints;

  /** Smooth closed Path2D through outline points using quadratic midpoints. */
  function outlineToPath(pts) {
    const path = new Path2D();
    if (!pts.length) return path;
    if (pts.length < 3) {
      path.arc(pts[0][0], pts[0][1], 0.5, 0, Math.PI * 2);
      return path;
    }
    path.moveTo(pts[0][0], pts[0][1]);
    for (let i = 0; i < pts.length; i++) {
      const p0 = pts[i];
      const p1 = pts[(i + 1) % pts.length];
      path.quadraticCurveTo(p0[0], p0[1], (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2);
    }
    path.closePath();
    return path;
  }
  CC.outlineToPath = outlineToPath;

  /** Catmull-Rom spline -> Path2D (used for smooth fills/polygons). */
  function smoothPath(points, closed, tension) {
    const path = new Path2D();
    const n = points.length;
    if (n === 0) return path;
    const k = (tension ?? 0.5) / 3; // 0.5 => classic Catmull-Rom
    path.moveTo(points[0][0], points[0][1]);
    if (n < 3) {
      for (let i = 1; i < n; i++) path.lineTo(points[i][0], points[i][1]);
      if (closed) path.closePath();
      return path;
    }
    const get = (i) => (closed ? points[(i + n) % n] : points[Math.max(0, Math.min(n - 1, i))]);
    const segs = closed ? n : n - 1;
    for (let i = 0; i < segs; i++) {
      const p0 = get(i - 1), p1 = get(i), p2 = get(i + 1), p3 = get(i + 2);
      const c1 = [p1[0] + (p2[0] - p0[0]) * k, p1[1] + (p2[1] - p0[1]) * k];
      const c2 = [p2[0] - (p3[0] - p1[0]) * k, p2[1] - (p3[1] - p1[1]) * k];
      path.bezierCurveTo(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1]);
    }
    if (closed) path.closePath();
    return path;
  }
  CC.smoothPath = smoothPath;

  // ---------------------------------------------------------------------------
  // Texture cache (pencil tooth, charcoal grain, marker streaks)
  // ---------------------------------------------------------------------------
  const textureCache = new Map();
  function makeCanvas(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.ceil(w));
    c.height = Math.max(1, Math.ceil(h));
    return c;
  }
  CC.makeCanvas = makeCanvas;

  /** Tileable noise texture. `density` = fraction of pixels punched out. */
  function noiseTexture(kind, seed) {
    const key = kind + ":" + seed;
    if (textureCache.has(key)) return textureCache.get(key);
    const size = 256;
    const c = makeCanvas(size, size);
    const ctx = c.getContext("2d");
    const img = ctx.createImageData(size, size);
    const rand = mulberry32(seed);
    const d = img.data;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const i = (y * size + x) * 4;
        let a = 0;
        if (kind === "pencil") {
          // paper tooth: mostly solid with fine holes
          a = rand() < 0.38 ? 255 * (0.4 + 0.6 * rand()) : 0;
        } else if (kind === "charcoal") {
          // coarse, clumpy grain (diagonal bias)
          const v = rand();
          const streak = Math.sin((x + y * 0.6) * 0.35 + rand() * 0.8) * 0.5 + 0.5;
          a = v * 0.6 + streak * 0.4 > 0.55 ? 255 * rand() : 0;
        } else if (kind === "marker") {
          const streak = Math.sin(y * 0.9 + Math.sin(x * 0.05) * 2.0) * 0.5 + 0.5;
          a = streak * 40 + rand() * 25;
        } else {
          a = rand() * 255;
        }
        d[i] = d[i + 1] = d[i + 2] = 0;
        d[i + 3] = a;
      }
    }
    ctx.putImageData(img, 0, 0);
    textureCache.set(key, c);
    return c;
  }
  CC.noiseTexture = noiseTexture;

  // ---------------------------------------------------------------------------
  // Brush definitions
  // ---------------------------------------------------------------------------
  /*
   * Brush presets. Values are defaults; stroke elements can override any of
   * size / thinning / smoothing / streamline / taper_start / taper_end.
   */
  const BRUSHES = {
    gpen:     { thinning: 0.62, smoothing: 0.55, streamline: 0.45, taperStart: 0.18, taperEnd: 0.28, texture: null },
    ink:      { thinning: 0.82, smoothing: 0.6,  streamline: 0.5,  taperStart: 0.25, taperEnd: 0.4,  texture: null },
    pencil:   { thinning: 0.35, smoothing: 0.4,  streamline: 0.3,  taperStart: 0.1,  taperEnd: 0.15, texture: "pencil", passes: 2, jitter: 0.35 },
    charcoal: { thinning: 0.45, smoothing: 0.35, streamline: 0.3,  taperStart: 0.12, taperEnd: 0.2,  texture: "charcoal", passes: 3, jitter: 0.9, rough: 0.18 },
    marker:   { thinning: 0.0,  smoothing: 0.6,  streamline: 0.55, taperStart: 0,    taperEnd: 0,    texture: "marker", defaultOpacity: 0.75, defaultBlend: "multiply" },
    drybrush: { thinning: 0.5,  smoothing: 0.5,  streamline: 0.5,  taperStart: 0.05, taperEnd: 0.3,  bristles: true },
    spray:    { spray: true },
    airbrush: { thinning: 0.2,  smoothing: 0.6,  streamline: 0.5,  taperStart: 0.2,  taperEnd: 0.2,  soft: true, defaultOpacity: 0.6 },
  };
  CC.BRUSHES = BRUSHES;

  function strokeOptions(el, preset, sizeOverride) {
    const size = sizeOverride ?? el.size ?? 6;
    const fraction = (v) => (v == null ? 0 : v <= 1 ? v : 1); // taper given as fraction of length
    return {
      size,
      thinning: el.thinning ?? preset.thinning ?? 0.5,
      smoothing: el.smoothing ?? preset.smoothing ?? 0.5,
      streamline: el.streamline ?? preset.streamline ?? 0.5,
      simulatePressure: el.simulate_pressure ?? !el.points.some((p) => p.length > 2),
      last: true,
      _taperStartFrac: fraction(el.taper_start ?? preset.taperStart ?? 0),
      _taperEndFrac: fraction(el.taper_end ?? preset.taperEnd ?? 0),
      start: { cap: el.cap !== "butt" },
      end: { cap: el.cap !== "butt" },
    };
  }

  function polylineLength(points) {
    let L = 0;
    for (let i = 1; i < points.length; i++) L += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
    return L;
  }

  /** Resample a polyline so tapering and spray spacing are even. */
  function resample(points, spacing) {
    if (points.length < 2) return points.map((p) => p.slice());
    const out = [points[0].slice()];
    let carry = 0;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i];
      const segLen = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (segLen === 0) continue;
      let d = spacing - carry;
      while (d <= segLen) {
        const t = d / segLen;
        const pa = a[2] ?? 0.5, pb = b[2] ?? 0.5;
        out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, pa + (pb - pa) * t]);
        d += spacing;
      }
      carry = segLen - (d - spacing);
    }
    const last = points[points.length - 1];
    const tail = out[out.length - 1];
    if (tail[0] !== last[0] || tail[1] !== last[1]) out.push(last.slice());
    return out;
  }
  CC.resample = resample;

  function outlineFor(points, opts) {
    const L = polylineLength(points);
    const o = Object.assign({}, opts);
    o.start = Object.assign({}, opts.start, { taper: opts._taperStartFrac * L });
    o.end = Object.assign({}, opts.end, { taper: opts._taperEndFrac * L });
    return getStroke(points, o);
  }

  function bboxOf(points, pad) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of points) {
      if (p[0] < x0) x0 = p[0];
      if (p[1] < y0) y0 = p[1];
      if (p[0] > x1) x1 = p[0];
      if (p[1] > y1) y1 = p[1];
    }
    return { x: x0 - pad, y: y0 - pad, w: x1 - x0 + pad * 2, h: y1 - y0 + pad * 2 };
  }

  function jitterPoints(points, amount, rand) {
    return points.map((p) => [p[0] + gaussian(rand) * amount, p[1] + gaussian(rand) * amount, p[2]]);
  }

  /**
   * Draw a stroke element into ctx. Strokes are drawn into a bbox-sized scratch
   * surface so semi-transparent strokes do not darken where they overlap
   * themselves (matches how FlipaClip/Harmony composite a single stroke).
   */
  function drawStroke(ctx, el, env) {
    const pts0 = el.points || [];
    if (!pts0.length) return;
    const brushName = el.brush || "gpen";
    const preset = BRUSHES[brushName] || BRUSHES.gpen;
    const seed = (el.seed ?? hashString(JSON.stringify(pts0.slice(0, 3)) + brushName)) >>> 0;
    const rand = mulberry32(seed);
    const size = el.size ?? 6;
    const color = el.color || "#111111";
    const opacity = (el.opacity ?? preset.defaultOpacity ?? 1);
    const blend = CC.blendOp(el.blend || preset.defaultBlend || "normal");

    // Optional fill under a closed contour (classic cel "line + paint" in one element).
    if (el.fill) {
      ctx.save();
      ctx.globalAlpha = el.fill_opacity ?? 1;
      ctx.fillStyle = CC.resolvePaint(ctx, el.fill);
      ctx.fill(smoothPath(pts0, true, el.tension ?? 0.5));
      ctx.restore();
    }
    if (el.size === 0) return;

    let points = pts0;
    if (el.smooth_input !== false && pts0.length > 2 && pts0.length < 64) {
      // densify sparse control points with a Catmull-Rom pass for organic curves
      points = densify(pts0, el.closed);
    } else if (el.closed && pts0.length > 2) {
      points = pts0.concat([pts0[0]]);
    }
    // Sparse polylines (e.g. mountain ridges with smooth_input:false) must be
    // densified linearly, otherwise streamline smoothing cuts every corner.
    if (el.smooth_input === false) points = resample(points, Math.max(2, size * 0.75));

    const pad = size * 2 + 4;
    const bb = bboxOf(points, pad);
    const m = ctx.getTransform();
    const scale = Math.max(0.05, Math.sqrt(Math.abs(m.a * m.d - m.b * m.c)));
    const sw = Math.ceil(bb.w * scale), sh = Math.ceil(bb.h * scale);
    if (sw <= 0 || sh <= 0 || sw * sh > 64e6) return;

    const scratch = CC.scratchCanvas(sw, sh);
    const s = scratch.getContext("2d");
    s.setTransform(1, 0, 0, 1, 0, 0);
    s.globalCompositeOperation = "source-over";
    s.globalAlpha = 1;
    s.filter = "none";
    // clear a margin past the blit rect: bilinear sampling reads 1-2px beyond it
    s.clearRect(0, 0, Math.min(scratch.width, sw + 4), Math.min(scratch.height, sh + 4));
    s.setTransform(scale, 0, 0, scale, -bb.x * scale, -bb.y * scale);
    s.fillStyle = CC.resolvePaint(s, color);

    if (preset.spray) {
      drawSpray(s, points, el, rand);
    } else if (preset.bristles) {
      drawDryBrush(s, points, el, preset, rand);
    } else {
      const opts = strokeOptions(el, preset);
      const passes = preset.passes || 1;
      for (let pass = 0; pass < passes; pass++) {
        let p = points;
        if (preset.jitter && pass > 0) p = jitterPoints(points, preset.jitter * Math.max(0.6, size * 0.08), rand);
        let outline = outlineFor(p, opts);
        if (preset.rough) outline = outline.map((q) => [q[0] + gaussian(rand) * size * preset.rough, q[1] + gaussian(rand) * size * preset.rough]);
        s.globalAlpha = pass === 0 ? 1 : 0.55;
        if (preset.soft) s.filter = `blur(${Math.max(0.5, size * 0.35 * scale)}px)`;
        s.fill(outlineToPath(outline));
        s.filter = "none";
      }
      s.globalAlpha = 1;
      if (preset.texture) {
        const tex = noiseTexture(preset.texture, (seed % 7) + 1);
        const pat = s.createPattern(tex, "repeat");
        s.setTransform(1, 0, 0, 1, 0, 0);
        s.globalCompositeOperation = "destination-out";
        s.globalAlpha = preset.texture === "marker" ? 0.6 : preset.texture === "charcoal" ? 0.85 : 0.6;
        s.fillStyle = pat;
        s.fillRect(0, 0, sw, sh);
        s.globalCompositeOperation = "source-over";
        s.globalAlpha = 1;
      }
    }

    ctx.save();
    ctx.globalAlpha *= opacity;
    ctx.globalCompositeOperation = blend;
    ctx.drawImage(scratch, 0, 0, sw, sh, bb.x, bb.y, sw / scale, sh / scale);
    ctx.restore();
  }
  CC.drawStroke = drawStroke;

  /** Catmull-Rom densification of sparse control points (keeps pressure). */
  function densify(points, closed) {
    const n = points.length;
    const out = [];
    const get = (i) => (closed ? points[(i + n) % n] : points[Math.max(0, Math.min(n - 1, i))]);
    const segs = closed ? n : n - 1;
    for (let i = 0; i < segs; i++) {
      const p0 = get(i - 1), p1 = get(i), p2 = get(i + 1), p3 = get(i + 2);
      const segLen = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
      const steps = Math.max(2, Math.min(24, Math.ceil(segLen / 4)));
      for (let k = 0; k < steps; k++) {
        const t = k / steps, t2 = t * t, t3 = t2 * t;
        const cr = (a, b, c, d) => 0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
        const pa = p1[2] ?? 0.5, pb = p2[2] ?? 0.5;
        const pt = [cr(p0[0], p1[0], p2[0], p3[0]), cr(p0[1], p1[1], p2[1], p3[1])];
        if (points.some((p) => p.length > 2)) pt.push(pa + (pb - pa) * t);
        out.push(pt);
      }
    }
    const last = closed ? points[0] : points[n - 1];
    out.push(last.slice());
    return out;
  }
  CC.densify = densify;

  function drawSpray(s, points, el, rand) {
    const size = el.size ?? 20;
    const density = el.density ?? 0.6;
    const dot = el.dot_size ?? Math.max(0.6, size * 0.05);
    const path = resample(points, Math.max(1, size * 0.15));
    for (const p of path) {
      const pressure = p[2] ?? 0.5;
      const count = Math.round(size * density * (0.5 + pressure));
      for (let i = 0; i < count; i++) {
        const r = Math.abs(gaussian(rand)) * size * 0.5;
        const a = rand() * Math.PI * 2;
        const ds = dot * (0.4 + rand() * 1.2);
        s.globalAlpha = 0.35 + rand() * 0.65;
        s.beginPath();
        s.arc(p[0] + Math.cos(a) * r, p[1] + Math.sin(a) * r, ds, 0, Math.PI * 2);
        s.fill();
      }
    }
    s.globalAlpha = 1;
  }

  function drawDryBrush(s, points, el, preset, rand) {
    const size = el.size ?? 18;
    const count = Math.max(4, Math.round(el.bristles ?? size / 2.2));
    const path = resample(points, Math.max(1.5, size * 0.12));
    if (path.length < 2) return;
    // perpendicular frame along the path
    const normals = path.map((p, i) => {
      const a = path[Math.max(0, i - 1)], b = path[Math.min(path.length - 1, i + 1)];
      const t = uni([b[0] - a[0], b[1] - a[1]]);
      return [-t[1], t[0]];
    });
    for (let k = 0; k < count; k++) {
      const off = (k / (count - 1) - 0.5) * size + gaussian(rand) * size * 0.04;
      const bristleSize = Math.max(0.7, size / count * (0.9 + rand() * 1.1));
      const dryAt = 0.45 + rand() * 0.55; // bristle runs out of paint here
      const startAt = rand() * 0.08;
      const bp = [];
      for (let i = 0; i < path.length; i++) {
        const t = i / (path.length - 1);
        if (t < startAt || t > dryAt) continue;
        // random skips create the scratchy dry gaps
        if (t > dryAt * 0.7 && rand() < 0.25) { flush(); continue; }
        const p = path[i], n = normals[i];
        bp.push([p[0] + n[0] * off, p[1] + n[1] * off, (p[2] ?? 0.5) * (1 - t * 0.5)]);
      }
      flush();
      function flush() {
        if (bp.length > 1) {
          s.globalAlpha = 0.65 + rand() * 0.35;
          const o = { size: bristleSize, thinning: 0.4, smoothing: 0.4, streamline: 0.3, simulatePressure: false, last: true, start: { taper: bristleSize * 2 }, end: { taper: bristleSize * 6 } };
          s.fill(outlineToPath(getStroke(bp, o)));
        }
        bp.length = 0;
      }
    }
    s.globalAlpha = 1;
  }
})(window);
