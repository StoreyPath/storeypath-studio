// The finishes of the "real" look, painted here — nothing is downloaded. Which finishes
// there are, and what each is painted with, is StoreyPath's fixed set (spec/finishes.json,
// as ../finishes.js has it): carpet tiles, patterned and prayer carpet, sheet vinyl and
// vinyl planks, porcelain and wall tiles, marble, terrazzo, wood planks and herringbone,
// polished and plain concrete, epoxy, studded rubber, raised access floor; paint,
// wallpapers (linen, stripes, geometric, damask), mosaic, oak slats, walnut panels and
// stone. Each is painted by its kind, with its values, as a square image that tiles,
// with (for High quality) a normal map whose alpha is its roughness. Painting a 1024 px
// finish takes tens of milliseconds, so it is done off the page's thread when it can be
// (a worker made from this very code: `Painter`), and the world shows each surface in its
// finish's tone until its image comes. Only the finishes shown are painted.
//
// `finishes` is self-contained (it refers to nothing outside itself), so that its
// source, as the browser has it, is the worker's.

import { FINISHES, finishOf } from "../finishes.js";

/** The painters: ``paint(kind, values, size, seed, px, fine)`` → { color (RGBA bytes,
 * px²), normal (RGBA: the normal, and roughness in alpha; only when ``fine``), roughness
 * (its scale), normalScale, macro (how much a far larger tint varies it, so that it does
 * not visibly repeat) }, for an image covering ``size`` metres a side; ``kinds``, the
 * kinds it paints; ``macro(px)``, that tint. */
export function finishes() {
  /** A seeded random number generator: a finish looks the same every time. */
  function random(seed) {
    let s = seed >>> 0;
    return () => {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
    };
  }

  /** Tileable value noise of n² samples, ``cells`` lattice cells across (``down`` down:
   * as many by default), as fractal octaves: values about 0–1. */
  function fbm(n, cells, octaves, rnd, gain = 0.5, down = cells) {
    const out = new Float32Array(n * n);
    let amp = 1, total = 0;
    for (let o = 0; o < octaves; o++) {
      const cx = Math.min(cells << o, n), cy = Math.min(down << o, n);
      const lattice = new Float32Array(cx * cy);
      for (let i = 0; i < lattice.length; i++) lattice[i] = rnd();
      const kx = cx / n, ky = cy / n;
      for (let y = 0; y < n; y++) {
        const fy = y * ky, y0 = Math.floor(fy), ty = fy - y0, sy = ty * ty * (3 - 2 * ty);
        const r0 = (y0 % cy) * cx, r1 = ((y0 + 1) % cy) * cx;
        for (let x = 0; x < n; x++) {
          const fx = x * kx, x0 = Math.floor(fx), tx = fx - x0, sx = tx * tx * (3 - 2 * tx);
          const c0 = x0 % cx, c1 = (x0 + 1) % cx;
          const a = lattice[r0 + c0] + (lattice[r0 + c1] - lattice[r0 + c0]) * sx;
          const b = lattice[r1 + c0] + (lattice[r1 + c1] - lattice[r1 + c0]) * sx;
          out[y * n + x] += (a + (b - a) * sy) * amp;
        }
      }
      total += amp;
      amp *= gain;
    }
    for (let i = 0; i < out.length; i++) out[i] /= total;
    return out;
  }

  /** A normal map (tangent space) from a height field, wrapping at its edges, with
   * roughness (0–1 each pixel, or one value) in its alpha. */
  function normalMap(height, n, strength, rough) {
    const out = new Uint8ClampedArray(n * n * 4);
    for (let y = 0; y < n; y++) {
      const up = ((y - 1 + n) % n) * n, down = ((y + 1) % n) * n;
      for (let x = 0; x < n; x++) {
        const i = y * n + x, o = i * 4;
        const dx = height ? (height[y * n + (x + 1) % n] - height[y * n + (x - 1 + n) % n]) * strength : 0;
        const dy = height ? (height[down + x] - height[up + x]) * strength : 0;
        const len = Math.hypot(dx, dy, 1);
        out[o] = ((-dx / len) * 0.5 + 0.5) * 255;
        out[o + 1] = ((dy / len) * 0.5 + 0.5) * 255; // rows go down, the texture's v up
        out[o + 2] = ((1 / len) * 0.5 + 0.5) * 255;
        out[o + 3] = Math.max(0, Math.min(1, typeof rough === "number" ? rough : rough[i])) * 255;
      }
    }
    return out;
  }

  const image = (n) => new Uint8ClampedArray(n * n * 4);
  const set = (img, i, r, g, b) => {
    img[i * 4] = r;
    img[i * 4 + 1] = g;
    img[i * 4 + 2] = b;
    img[i * 4 + 3] = 255;
  };
  const hex = (c) => [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
  const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
  /** How much of a line ``half`` wide a point ``d`` from its middle is in (1 inside, 0
   * outside, a pixel's blend at its edge): ``px``, pixels a unit. */
  const band = (d, half, px) => clamp01((half - d) * px + 0.5);
  /** One of ``list``, by weight ([value, weight] each). */
  const pick = (list, rnd) => {
    const total = list.reduce((s, [, w]) => s + w, 0);
    let r = rnd() * total;
    for (const [v, w] of list) if ((r -= w) <= 0) return v;
    return list[0][0];
  };
  /** A finish painted: its colour, its normal map (fine) from heights, its roughness. */
  const done = (color, n, fine, { height = null, strength = 1, rough = 0.9, normalScale = 0.5, macro = 0.06 } = {}) =>
    ({ color, normal: fine ? normalMap(height, n, strength, rough) : null, normalScale: height ? normalScale : 0, roughness: 1, macro });

  /** Star-shaped polygons ([x, y] each, round their middles) filled into an image of
   * n², wrapping round its edges so that it tiles: their colour, and coverage in alpha. */
  function fill(n, shapes) {
    const out = image(n).fill(0);
    for (const { pts, rgb } of shapes) {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of pts) {
        x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
      }
      for (let py = Math.floor(y0); py <= Math.ceil(y1); py++) {
        for (let px = Math.floor(x0); px <= Math.ceil(x1); px++) {
          const cx = px + 0.5, cy = py + 0.5;
          let hit = false;
          for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
            const [xi, yi] = pts[i], [xj, yj] = pts[j];
            if ((yi > cy) !== (yj > cy) && cx < ((xj - xi) * (cy - yi)) / (yj - yi) + xi) hit = !hit;
          }
          if (!hit) continue;
          const i = (((py % n) + n) % n) * n + (((px % n) + n) % n);
          set(out, i, rgb[0], rgb[1], rgb[2]);
        }
      }
    }
    return out;
  }

  /** Carpet, its pile streaked and flecked; in tiles (50 cm, laid quarter-turned: fibres
   * one way, then across) or broadloom; ``pattern(x, y)`` (0–1) blends in ``accent``, and
   * ``shade(x, y)`` multiplies it. */
  function carpet(p, n, fine, rnd, size, { tiles = true, pattern = null, shade = null } = {}) {
    const base = hex(p.base), accent = p.accent ? hex(p.accent) : base;
    const count = tiles ? Math.max(1, Math.round(size / 0.5)) : 1, t = n / count;
    const streaks = fbm(n, 8, 3, rnd), grain = fbm(n, Math.min(256, n / 4), 2, rnd);
    const color = image(n), height = fine ? new Float32Array(n * n) : null;
    const tone = Array.from({ length: count * count }, () => 1 + (rnd() - 0.5) * (tiles ? 0.07 : 0));
    const seam = Math.max(1, Math.round(n / 1024));
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x;
        const tx = Math.floor(x / t), ty = Math.floor(y / t);
        const across = tiles && (tx + ty) % 2 === 0;
        const sx = across ? (x * 7) % n : x, sy = across ? y : (y * 7) % n; // streaks along the pile
        const g = grain[i] - 0.5, fleck = rnd();
        let k = tone[ty * count + tx] * (1 + (streaks[sy * n + sx] - 0.5) * 0.12 + g * 0.22);
        if (fleck < 0.012) k *= 1.25;
        else if (fleck < 0.024) k *= 0.8;
        const edge = tiles && (x % t < seam || y % t < seam);
        if (edge) k *= 0.88;
        if (shade) k *= shade(x, y);
        const c = pattern ? mix(base, accent, pattern(x, y)) : base;
        set(color, i, c[0] * k, c[1] * k, c[2] * k);
        if (height) height[i] = g * 0.9 + (edge ? -0.4 : 0);
      }
    }
    return done(color, n, fine, { height, strength: 1.6, rough: 1, macro: 0.1 });
  }

  /** Planks (``width_m`` wide), staggered, with their grain and seams: wood, or vinyl
   * made to look it. */
  function planks(p, n, fine, rnd, size) {
    const base = hex(p.base), contrast = p.grain ?? 0.25, gloss = p.gloss ?? 0.42;
    const count = Math.max(1, Math.round(size / (p.width_m || 0.2))), w = n / count, k = n / 1024;
    const grain = fbm(n, 16, 4, rnd);
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    const columns = Array.from({ length: count }, () => {
      let y = Math.floor(rnd() * n);
      const runs = [];
      while (runs.length < 8) {
        const len = Math.floor(n * (0.45 + rnd() * 0.4));
        runs.push({ from: y, len, tone: 0.86 + rnd() * 0.24, shift: Math.floor(rnd() * n), hue: rnd() });
        y += len;
        if (y - runs[0].from >= n) break;
      }
      return runs;
    });
    const seam = 1.5 * Math.max(k, 0.67);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const col = Math.floor(x / w), gx = x - col * w;
        const runs = columns[col];
        let run = runs[runs.length - 1];
        for (const r of runs) {
          if ((y - r.from + n * 4) % n < r.len) {
            run = r;
            break;
          }
        }
        const along = (y - run.from + n * 4) % n;
        const g = grain[(Math.floor(gx * 6 / Math.max(k, 0.5) + run.shift) % n) * n + ((Math.floor(along / (8 * k)) + run.shift) % n)];
        const lines = 0.5 + 0.5 * Math.sin((gx / w) * 40 + g * 18);
        let s = run.tone * (0.92 + (g - 0.5) * contrast + lines * contrast * 0.24);
        const edge = gx < seam || along < seam;
        if (edge) s *= 0.55;
        const warm = 1 + (run.hue - 0.5) * 0.08;
        set(color, y * n + x, base[0] * s * warm, base[1] * s, (base[2] * s) / warm);
        rough[y * n + x] = gloss + lines * 0.12 + (edge ? 0.4 : 0);
        height[y * n + x] = edge ? -1 : lines * 0.15;
      }
    }
    return done(color, n, fine, { height, strength: 1.5, rough, normalScale: 0.6, macro: 0.07 });
  }

  /** Rectangular tiles (``tile_m``: [across, down]), rows offset by ``offset`` of a tile,
   * with narrow grout; faintly clouded (``vein``). */
  function tiles(p, n, fine, rnd, size) {
    const base = hex(p.base), groutColor = hex(p.grout), [tw, th] = p.tile_m;
    const nx = Math.max(1, Math.round(size / tw)), ny = Math.max(1, Math.round(size / th));
    const tx = n / nx, ty = n / ny, offset = p.offset ?? 0, k = n / 1024;
    const grout = Math.max(1, Math.round(((p.grout_m ?? 0.003) * n) / size)), ease = 3 * k;
    const cloud = fbm(n, 6, 5, rnd, 0.6), grain = fbm(n, Math.min(200, n / 4), 1, rnd);
    const tone = Array.from({ length: nx * ny }, () => 1 + (rnd() - 0.5) * 0.05);
    const vein = p.vein ?? 0.07, gloss = p.gloss ?? 0.22;
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    for (let y = 0; y < n; y++) {
      const row = Math.floor(y / ty), gy = y - row * ty, shift = (row % 2) * offset * tx;
      for (let x = 0; x < n; x++) {
        const i = y * n + x, xs = (x - shift + n) % n, col = Math.floor(xs / tx) % nx, gx = xs - col * tx;
        if (gx < grout || gy < grout) {
          set(color, i, ...groutColor);
          rough[i] = 0.9;
          height[i] = -1;
          continue;
        }
        const s = tone[row * nx + col] * (1 + (cloud[i] - 0.5) * vein + (grain[i] - 0.5) * 0.04);
        set(color, i, base[0] * s, base[1] * s, base[2] * s);
        rough[i] = gloss + (grain[i] - 0.5) * 0.06;
        const e = Math.min(gx - grout, gy - grout, tx - gx, ty - gy); // the tile's edge, eased
        height[i] = e < ease ? -0.5 + e / (2 * ease) : 0;
      }
    }
    return done(color, n, fine, { height, strength: 3 / Math.max(k, 0.5), rough, normalScale: 0.7, macro: 0.05 });
  }

  /** Terrazzo: chips of marble in a binder, polished, in bays with zinc strips. */
  function terrazzo(p, n, fine, rnd) {
    const k = n / 1024, binderColor = hex(p.binder ?? "#e2ded6");
    const binder = fbm(n, 6, 4, rnd);
    const palette = p.chips === "dark"
      ? [[[236, 234, 229], 20], [[150, 150, 150], 18], [[96, 96, 98], 18], [[30, 30, 32], 26], [[178, 160, 132], 6], [[120, 70, 52], 3],
        [[110, 126, 136], 4]]
      : [[[246, 244, 239], 34], [[205, 202, 196], 26], [[168, 165, 160], 16], [[128, 126, 124], 8], [[212, 196, 170], 9],
        [[86, 86, 88], 3], [[178, 122, 92], 2], [[150, 160, 166], 2]];
    const shapes = [];
    for (let i = 0; i < 9000; i++) {
      const x = rnd() * n, y = rnd() * n, r = (1.2 + rnd() ** 2.2 * 9) * k, sides = 4 + Math.floor(rnd() * 4);
      const [cr, cg, cb] = pick(palette, rnd), v = 0.94 + rnd() * 0.12;
      const pts = Array.from({ length: sides }, (_, s) => {
        const a = (s / sides) * Math.PI * 2 + rnd() * 0.8, d = r * (0.6 + rnd() * 0.5);
        return [x + Math.cos(a) * d, y + Math.sin(a) * d];
      });
      shapes.push({ pts, rgb: [cr * v, cg * v, cb * v] });
    }
    const chips = fill(n, shapes);
    const strip = p.chips === "dark" ? [140, 138, 132] : [182, 180, 176];
    const color = image(n), rough = new Float32Array(n * n), width = Math.max(1, Math.round(2 * k));
    for (let i = 0; i < n * n; i++) {
      const b = 0.97 + (binder[i] - 0.5) * 0.08;
      let r = binderColor[0] * b, g = binderColor[1] * b, bl = binderColor[2] * b;
      const a = chips[i * 4 + 3] / 255;
      r += (chips[i * 4] - r) * a; g += (chips[i * 4 + 1] - g) * a; bl += (chips[i * 4 + 2] - bl) * a;
      if (i % n < width || i < n * width) [r, g, bl] = strip; // the divider strips
      set(color, i, r, g, bl);
      rough[i] = 0.16 + (binder[i] - 0.5) * 0.12;
    }
    return done(color, n, fine, { rough, macro: 0.06 });
  }

  /** A field of n² sampled between its pixels (wrapping), at (u, v). */
  const at = (field, n, u, v) => {
    const x0 = Math.floor(u), y0 = Math.floor(v), tx = u - x0, ty = v - y0;
    const i0 = ((x0 % n) + n) % n, i1 = (i0 + 1) % n, r0 = (((y0 % n) + n) % n) * n, r1 = ((((y0 + 1) % n) + n) % n) * n;
    const a = field[r0 + i0] + (field[r0 + i1] - field[r0 + i0]) * tx, b = field[r1 + i0] + (field[r1 + i1] - field[r1 + i0]) * tx;
    return a + (b - a) * ty;
  };

  /** Marble tiles (``tile_m`` square): veins where a smooth noise crosses its middle (long,
   * branching curves), finer ones from another, clouded; each tile its own part of them,
   * with fine joints. */
  function marble(p, n, fine, rnd, size) {
    const base = hex(p.base), veinColor = hex(p.vein), strength = p.veins ?? 0.6;
    const count = Math.max(1, Math.round(size / (p.tile_m || 0.8))), t = n / count;
    const joint = Math.max(1, Math.round((0.0015 * n) / size));
    // (noise longer one way than the other: veins run long, each tile turned its own way)
    const main = fbm(n, 3, 4, rnd, 0.5, 2), thin = fbm(n, 7, 3, rnd, 0.5, 4), cloud = fbm(n, 3, 4, rnd);
    const tilesOf = Array.from({ length: count * count }, () => ({ dx: Math.floor(rnd() * n), dy: Math.floor(rnd() * n),
      tone: 1 + (rnd() - 0.5) * 0.05, turn: rnd() * Math.PI }));
    const px = n / 1024;
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, cx = Math.floor(x / t), cy = Math.floor(y / t);
        if (x % t < joint || y % t < joint) {
          set(color, i, ...mix(base, [200, 198, 192], 0.5));
          rough[i] = 0.6;
          height[i] = -1;
          continue;
        }
        const tile = tilesOf[cy * count + cx];
        // (a tile's joints part it from the next: its veins need not run on into it)
        const lx = x - cx * t, ly = y - cy * t, c = Math.cos(tile.turn), sn = Math.sin(tile.turn);
        const u = lx * c - ly * sn + tile.dx, v = lx * sn + ly * c + tile.dy;
        const j = (((Math.floor(v) % n) + n) % n) * n + (((Math.floor(u) % n) + n) % n);
        // a main vein, a soft haze round it, and finer veins branching near it
        const ridge = Math.abs(at(main, n, u, v) - 0.5) * 2, fineRidge = Math.abs(at(thin, n, u, v) - 0.5) * 2;
        const w = band(ridge, 0.007, 300 * px) * 0.8 + clamp01(1 - ridge / 0.07) ** 3 * 0.18
          + band(fineRidge, 0.004, 420 * px) * 0.4 * clamp01(1 - ridge / 0.3);
        const s = tile.tone * (1 + (cloud[j] - 0.5) * 0.1);
        set(color, i, ...mix([base[0] * s, base[1] * s, base[2] * s], veinColor, Math.min(1, w * strength)));
        rough[i] = 0.1 + (cloud[j] - 0.5) * 0.04;
      }
    }
    return done(color, n, fine, { height, strength: 2, rough, normalScale: 0.4, macro: 0.05 });
  }

  /** Herringbone parquet: blocks ``length`` times as long as wide, at right angles, their
   * grain along them. The image is 2 × length blocks' widths a side. */
  function herringbone(p, n, fine, rnd) {
    const base = hex(p.base), L = p.length ?? 5, W = n / (2 * L); // pixels a block's width
    const grain = fbm(n, 16, 4, rnd);
    const tones = Array.from({ length: 4 * L }, () => ({ tone: 0.86 + rnd() * 0.24, shift: Math.floor(rnd() * n), hue: rnd() }));
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    const seam = Math.max(0.6, 1.2 * (n / 1024)) / W; // in blocks' widths
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const X = (x + 0.5) / W, Y = (y + 0.5) / W; // (each pixel's middle)
        // H blocks [0, L] × [0, 1] and V blocks [L, L + 1] × [1 − L, 1], repeated by (1, 1) and (L, −L)
        const s = X - Math.floor(Y), m2 = Math.floor(s / (2 * L)), r = s - 2 * L * m2;
        let along, across, id;
        if (r < L) {
          along = r;
          across = Y - Math.floor(Y);
          const m = Math.floor(Y) + m2 * L;
          id = (((m - L * m2) % (2 * L)) + 2 * L) % (2 * L);
        } else {
          const t = Y - Math.floor(X), w0 = t - (1 - 2 * L), q = -Math.floor(w0 / (2 * L));
          along = w0 + 2 * L * q;
          across = X - Math.floor(X);
          const m = Math.floor(X) - q * L - L;
          id = 2 * L + ((((m - L * q) % (2 * L)) + 2 * L) % (2 * L));
        }
        const b = tones[id];
        const g = grain[(Math.floor(across * W * 6 * (1024 / n) / 4 + b.shift) % n) * n + ((Math.floor((along * W) / 8) + b.shift) % n)];
        const lines = 0.5 + 0.5 * Math.sin(across * 30 + g * 18);
        let k = b.tone * (0.92 + (g - 0.5) * 0.25 + lines * 0.05);
        const edge = across < seam || along < seam;
        if (edge) k *= 0.6;
        const warm = 1 + (b.hue - 0.5) * 0.08;
        const i = y * n + x;
        set(color, i, base[0] * k * warm, base[1] * k, (base[2] * k) / warm);
        rough[i] = 0.42 + lines * 0.1 + (edge ? 0.4 : 0);
        height[i] = edge ? -1 : lines * 0.12;
      }
    }
    return done(color, n, fine, { height, strength: 1.5, rough, normalScale: 0.6, macro: 0.07 });
  }

  /** Polished concrete: mottled, fine aggregate, and saw-cut joints every image. */
  function polished(p, n, fine, rnd) {
    const base = hex(p.base);
    const mottle = fbm(n, 4, 5, rnd, 0.55), grain = fbm(n, Math.min(128, n / 4), 2, rnd);
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    const joint = Math.max(1, Math.round((2 * n) / 1024));
    for (let i = 0; i < n * n; i++) {
      const x = i % n, y = (i / n) | 0;
      let k = 0.98 + (mottle[i] - 0.5) * 0.22 + (grain[i] - 0.5) * 0.05;
      const s = rnd();
      if (s < 0.01) k *= 0.82;
      else if (s < 0.02) k *= 1.12;
      const cut = x < joint || y < joint;
      if (cut) k *= 0.62;
      set(color, i, base[0] * k, base[1] * k, base[2] * k);
      rough[i] = 0.28 + (mottle[i] - 0.5) * 0.3 + (cut ? 0.5 : 0);
      height[i] = cut ? -1 : 0;
    }
    return done(color, n, fine, { height, strength: 2, rough, normalScale: 0.6, macro: 0.08 });
  }

  /** Plain concrete: matte, mottled and stained. */
  function concrete(p, n, fine, rnd) {
    const base = hex(p.base);
    const mottle = fbm(n, 4, 6, rnd, 0.55), stains = fbm(n, 3, 3, rnd), grain = fbm(n, Math.min(256, n / 4), 1, rnd);
    const color = image(n), height = new Float32Array(n * n);
    for (let i = 0; i < n * n; i++) {
      let k = 0.97 + (mottle[i] - 0.5) * 0.24 + (grain[i] - 0.5) * 0.08;
      if (stains[i] > 0.62) k *= 1 - (stains[i] - 0.62) * 0.6;
      set(color, i, base[0] * k, base[1] * k, base[2] * k);
      height[i] = grain[i] * 0.6 + mottle[i] * 0.4;
    }
    return done(color, n, fine, { height, strength: 1.2, rough: 0.88, normalScale: 0.4, macro: 0.08 });
  }

  /** Epoxy resin: smooth, glossy, faintly clouded. */
  function epoxy(p, n, fine, rnd) {
    const base = hex(p.base), mottle = fbm(n, 3, 5, rnd, 0.55), grain = fbm(n, Math.min(128, n / 4), 1, rnd);
    const color = image(n), rough = new Float32Array(n * n);
    for (let i = 0; i < n * n; i++) {
      const k = 1 + (mottle[i] - 0.5) * 0.06 + (grain[i] - 0.5) * 0.015;
      set(color, i, base[0] * k, base[1] * k, base[2] * k);
      rough[i] = 0.2 + (mottle[i] - 0.5) * 0.1;
    }
    return done(color, n, fine, { rough, macro: 0.05 });
  }

  /** Sheet vinyl: a fine speckle of chips in a mottled ground, smooth, no seams. */
  function vinyl(p, n, fine, rnd) {
    const base = hex(p.base), speckle = (p.speckle ?? []).map(hex);
    const mottle = fbm(n, 4, 4, rnd, 0.55), grain = fbm(n, Math.min(128, n / 4), 1, rnd);
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    for (let i = 0; i < n * n; i++) {
      const k = 1 + (mottle[i] - 0.5) * 0.07 + (grain[i] - 0.5) * 0.03;
      let c = [base[0] * k, base[1] * k, base[2] * k];
      if (speckle.length && rnd() < 0.07) c = mix(c, speckle[Math.floor(rnd() * speckle.length)], 0.75);
      set(color, i, ...c);
      rough[i] = 0.36 + (grain[i] - 0.5) * 0.1;
      height[i] = grain[i] * 0.3;
    }
    return done(color, n, fine, { height, strength: 0.6, rough, normalScale: 0.2, macro: 0.05 });
  }

  /** Rubber flooring: round studs (5 cm apart) standing up from a matte ground. */
  function rubber(p, n, fine, rnd, size) {
    const base = hex(p.base), count = Math.max(1, Math.round(size / 0.05)), s = n / count, r = s * 0.32;
    const grain = fbm(n, Math.min(128, n / 4), 2, rnd);
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, dx = (x % s) - s / 2 + 0.5, dy = (y % s) - s / 2 + 0.5, d = Math.hypot(dx, dy);
        const on = band(d, r, 1);
        const dome = d < r ? Math.sqrt(1 - (d / r) ** 2) : 0;
        const k = (0.96 + (grain[i] - 0.5) * 0.08) * (1 + on * 0.1 + dome * 0.05);
        set(color, i, base[0] * k, base[1] * k, base[2] * k);
        rough[i] = 0.85 - on * 0.15;
        height[i] = dome * 1.2 + on * 0.3;
      }
    }
    return done(color, n, fine, { height, strength: 2.5, rough, normalScale: 0.8, macro: 0.04 });
  }

  /** A raised access floor: 60 cm panels, speckled, with dark joints and eased edges. */
  function raised(p, n, fine, rnd, size) {
    const base = hex(p.base), speckle = (p.speckle ?? []).map(hex);
    const count = Math.max(1, Math.round(size / 0.6)), t = n / count, joint = Math.max(1, Math.round((0.003 * n) / size));
    const grain = fbm(n, Math.min(128, n / 4), 1, rnd), tone = Array.from({ length: count * count }, () => 1 + (rnd() - 0.5) * 0.06);
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n), ease = 4 * (n / 1024);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, gx = x % t, gy = y % t;
        if (gx < joint || gy < joint) {
          set(color, i, 52, 54, 56);
          rough[i] = 0.9;
          height[i] = -1;
          continue;
        }
        const k = tone[Math.floor(y / t) * count + Math.floor(x / t)] * (1 + (grain[i] - 0.5) * 0.05);
        let c = [base[0] * k, base[1] * k, base[2] * k];
        if (speckle.length && rnd() < 0.06) c = mix(c, speckle[Math.floor(rnd() * speckle.length)], 0.7);
        set(color, i, ...c);
        rough[i] = 0.45;
        const e = Math.min(gx - joint, gy - joint, t - gx, t - gy);
        height[i] = e < ease ? -0.6 + (0.6 * e) / ease : 0;
      }
    }
    return done(color, n, fine, { height, strength: 2, rough, normalScale: 0.6, macro: 0.04 });
  }

  /** Paint on plaster: its colour, with a faint orange peel. */
  function paint(p, n, fine, rnd) {
    const base = hex(p.base);
    const peel = fbm(n, Math.min(96, n / 4), 3, rnd), cloud = fbm(n, 4, 3, rnd);
    const color = image(n);
    for (let i = 0; i < n * n; i++) {
      const k = 1 + (cloud[i] - 0.5) * 0.025 + (peel[i] - 0.5) * 0.02;
      set(color, i, base[0] * k, base[1] * k, base[2] * k);
    }
    return done(color, n, fine, { height: peel, strength: 2.2, rough: 0.9, normalScale: 0.1, macro: 0.03 });
  }

  /** A woven wallpaper: threads across and down, slubbed. */
  function linen(p, n, fine, rnd) {
    const base = hex(p.base), cloud = fbm(n, 4, 3, rnd);
    const thread = Math.max(1, Math.round(n / 512));
    const rows = Array.from({ length: Math.ceil(n / thread) }, () => rnd() * 2 - 1);
    const cols = Array.from({ length: Math.ceil(n / thread) }, () => rnd() * 2 - 1);
    const slubs = fbm(n, 32, 2, rnd);
    const color = image(n), height = new Float32Array(n * n);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, a = rows[Math.floor(y / thread)], b = cols[Math.floor(x / thread)];
        const over = (Math.floor(x / thread) + Math.floor(y / thread)) % 2 ? a : b; // over and under
        const k = 1 + over * 0.05 + (a + b) * 0.015 + (cloud[i] - 0.5) * 0.04 + (slubs[i] - 0.5) * 0.05;
        set(color, i, base[0] * k, base[1] * k, base[2] * k);
        height[i] = over * 0.5 + slubs[i] * 0.3;
      }
    }
    return done(color, n, fine, { height, strength: 1.2, rough: 0.85, normalScale: 0.35, macro: 0.03 });
  }

  /** Striped wallpaper: a broad stripe, edged by fine lines, every 15 cm, down the wall. */
  function stripes(p, n, fine, rnd, size) {
    const base = hex(p.base), stripe = hex(p.stripe), count = Math.max(1, Math.round(size / 0.15)), period = n / count;
    const paper = fbm(n, Math.min(128, n / 4), 2, rnd);
    const color = image(n);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, f = (x % period) / period;
        let c = base;
        if (f >= 0.58 && f < 0.88) c = stripe;
        else if ((f >= 0.54 && f < 0.555) || (f >= 0.905 && f < 0.92)) c = mix(base, stripe, 0.7);
        const k = 1 + (paper[i] - 0.5) * 0.04;
        set(color, i, c[0] * k, c[1] * k, c[2] * k);
      }
    }
    return done(color, n, fine, { height: paper, strength: 0.6, rough: 0.8, normalScale: 0.15, macro: 0.03 });
  }

  /** Geometric wallpaper: interlocking circles (a lattice of rings through each other's
   * middles), in fine lines. */
  function geometric(p, n, fine, rnd, size) {
    const base = hex(p.base), line = hex(p.line), count = Math.max(1, Math.round(size / 0.15)), s = n / count;
    const R = s * Math.SQRT1_2, half = Math.max(0.6, s * 0.018);
    const paper = fbm(n, Math.min(128, n / 4), 2, rnd);
    const color = image(n), rough = new Float32Array(n * n);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, fx = (x + 0.5) % s, fy = (y + 0.5) % s;
        let w = 0;
        for (const [a, b] of [[0, 0], [s, 0], [0, s], [s, s], [s / 2, s / 2]]) {
          w = Math.max(w, band(Math.abs(Math.hypot(fx - a, fy - b) - (a === s / 2 ? R * 0.42 : R)), half, 1));
        }
        const k = 1 + (paper[i] - 0.5) * 0.04;
        const c = mix(base, line, w * 0.9);
        set(color, i, c[0] * k, c[1] * k, c[2] * k);
        rough[i] = 0.8 - w * 0.3;
      }
    }
    return done(color, n, fine, { rough, macro: 0.03 });
  }

  /** Damask wallpaper: a medallion tone on tone, in a half-drop repeat, satin on a matte ground. */
  function damask(p, n, fine, rnd, size) {
    const base = hex(p.base), motif = hex(p.motif);
    const cols = Math.max(2, 2 * Math.round(size / 0.4)), rows = Math.max(1, Math.round(size / 0.3));
    const cw = n / cols, ch = n / rows;
    const paper = fbm(n, Math.min(128, n / 4), 2, rnd);
    const color = image(n), rough = new Float32Array(n * n);
    const shape = (th) => 0.56 + 0.2 * Math.cos(4 * th) + 0.1 * Math.cos(8 * th) + 0.06 * Math.cos(2 * th);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, col = Math.floor(x / cw), drop = (col % 2) * ch / 2;
        const u = (x + 0.5 - col * cw) / cw - 0.5, yy = (y + 0.5 - drop + n) % n, v = (yy % ch) / ch - 0.5;
        const qx = u / 0.42, qy = (v / 0.42) * (ch / cw) * 0.62, r = Math.hypot(qx, qy), th = Math.atan2(qy, qx);
        const R = shape(th), px = cw * 0.42;
        let w = r < R ? 1 : 0;
        w = Math.max(w * (r < 0.16 ? 0 : 1), band(Math.abs(r - 0.16), 0.02, px), band(Math.abs(r - (R + 0.12)), 0.025, px));
        const corner = Math.abs(Math.abs(u) - 0.5) + Math.abs(Math.abs(v) - 0.5) * (ch / cw);
        w = Math.max(w, corner < 0.09 ? 1 : 0);
        const k = 1 + (paper[i] - 0.5) * 0.035;
        const c = mix(base, motif, w * 0.85);
        set(color, i, c[0] * k, c[1] * k, c[2] * k);
        rough[i] = 0.85 - w * 0.35;
      }
    }
    return done(color, n, fine, { rough, macro: 0.03 });
  }

  /** Mosaic: small square tiles (2.5 cm) in a few colours, glossy, with light grout. */
  function mosaic(p, n, fine, rnd, size) {
    const colors = p.colors.map(hex), grout = hex(p.grout), count = Math.max(1, Math.round(size / 0.025)), t = n / count;
    const g = t * 0.1; // the grout: a tenth of a tile, blended where it covers part of a pixel
    const tiles = Array.from({ length: count * count }, () => ({ c: colors[Math.floor(rnd() ** 1.3 * colors.length)], k: 0.92 + rnd() * 0.16 }));
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, gx = x % t, gy = y % t;
        const w = Math.max(clamp01(g - gx), clamp01(g - gy));
        const tile = tiles[Math.floor(y / t) * count + Math.floor(x / t)];
        set(color, i, ...mix([tile.c[0] * tile.k, tile.c[1] * tile.k, tile.c[2] * tile.k], grout, w));
        rough[i] = 0.12 + w * 0.78;
        height[i] = -w;
      }
    }
    return done(color, n, fine, { height, strength: 2, rough, normalScale: 0.6, macro: 0.03 });
  }

  /** Grain down a board: streaks long down the image (a field of noise of many cells
   * across and few down, so that it wraps both ways), at ``across`` pixels into the board. */
  const woodGrain = (field, n, x, y, shift) => field[y * n + ((x + shift) % n)];

  /** Oak slats (4.5 cm, 1.5 cm apart) down a dark backing. */
  function slats(p, n, fine, rnd, size) {
    const base = hex(p.base), gap = hex(p.gap), count = Math.max(1, Math.round(size / 0.06)), period = n / count, k = n / 1024;
    const grain = fbm(n, 96, 3, rnd, 0.5, 3);
    const boards = Array.from({ length: count }, () => ({ tone: 0.88 + rnd() * 0.2, shift: Math.floor(rnd() * n) }));
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, b = Math.floor(x / period), gx = x - b * period;
        if (gx >= period * 0.75) {
          set(color, i, ...gap);
          rough[i] = 0.95;
          height[i] = -1;
          continue;
        }
        const board = boards[b], g = woodGrain(grain, n, x, y, board.shift);
        const lines = 0.5 + 0.5 * Math.sin((gx / period) * 30 + g * 16);
        const s = board.tone * (0.92 + (g - 0.5) * 0.25 + lines * 0.05) * (gx < 1.2 * Math.max(k, 0.5) ? 0.8 : 1);
        set(color, i, base[0] * s, base[1] * s, base[2] * s);
        rough[i] = 0.5 + lines * 0.1;
        height[i] = lines * 0.1;
      }
    }
    return done(color, n, fine, { height, strength: 2, rough, normalScale: 0.7, macro: 0.05 });
  }

  /** Walnut panels, 60 cm wide, the grain down them, with V-grooves between. */
  function panels(p, n, fine, rnd, size) {
    const base = hex(p.base), count = Math.max(1, Math.round(size / 0.6)), w = n / count, k = n / 1024;
    const grain = fbm(n, 96, 3, rnd, 0.5, 3), groove = Math.max(1, Math.round((0.004 * n) / size));
    const boards = Array.from({ length: count }, () => ({ tone: 0.9 + rnd() * 0.16, shift: Math.floor(rnd() * n) }));
    const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const i = y * n + x, b = Math.floor(x / w), gx = x - b * w;
        if (gx < groove) {
          set(color, i, base[0] * 0.35, base[1] * 0.35, base[2] * 0.35);
          rough[i] = 0.9;
          height[i] = -1;
          continue;
        }
        const board = boards[b], g = woodGrain(grain, n, x, y, board.shift);
        const figure = 0.5 + 0.5 * Math.sin((gx / w) * 22 + g * 20 + Math.sin((y / n) * Math.PI * 6) * 2);
        const s = board.tone * (0.9 + (g - 0.5) * 0.3 + figure * 0.08);
        set(color, i, base[0] * s, base[1] * s, base[2] * s);
        rough[i] = 0.45 + figure * 0.1;
        height[i] = figure * 0.08;
      }
    }
    return done(color, n, fine, { height, strength: 1.5, rough, normalScale: 0.5, macro: 0.05 });
  }

  /** Stone cladding: courses 30 cm high of stones 25–60 cm long, each its own tone,
   * roughly dressed, in mortar joints. */
  function stone(p, n, fine, rnd, size) {
    const colors = p.colors.map(hex), jointColor = hex(p.joint), courses = Math.max(1, Math.round(size / 0.3)), ch = n / courses;
    const joint = Math.max(1, Math.round((0.01 * n) / size)), rough = fbm(n, 24, 4, rnd, 0.6), cloud = fbm(n, 6, 3, rnd);
    const rows = Array.from({ length: courses }, () => {
      const stones = [];
      let at = 0;
      while (at < n) {
        let len = Math.round(((0.25 + rnd() * 0.35) * n) / size);
        if (n - (at + len) < (0.2 * n) / size) len = n - at; // the last one to the edge: the course wraps
        stones.push({ from: at, to: at + len, c: colors[Math.floor(rnd() * colors.length)], k: 0.9 + rnd() * 0.18 });
        at += len;
      }
      return { stones, offset: Math.floor(rnd() * n) };
    });
    const color = image(n), roughness = new Float32Array(n * n), height = new Float32Array(n * n);
    for (let y = 0; y < n; y++) {
      const c = Math.floor(y / ch), gy = y - c * ch, row = rows[c];
      for (let x = 0; x < n; x++) {
        const i = y * n + x, xs = (x + row.offset) % n;
        const s = row.stones.find((t) => xs < t.to) ?? row.stones[row.stones.length - 1];
        const gx = Math.min(xs - s.from, s.to - 1 - xs);
        if (gy < joint || gx < joint / 2) {
          const k = 0.95 + (rough[i] - 0.5) * 0.2;
          set(color, i, jointColor[0] * k, jointColor[1] * k, jointColor[2] * k);
          roughness[i] = 0.95;
          height[i] = -1.2;
          continue;
        }
        const edge = Math.min(gy - joint, ch - gy, gx - joint / 2) / Math.max(1, joint * 1.5);
        const k = s.k * (1 + (rough[i] - 0.5) * 0.22 + (cloud[i] - 0.5) * 0.1) * (edge < 1 ? 0.88 + edge * 0.12 : 1);
        set(color, i, s.c[0] * k, s.c[1] * k, s.c[2] * k);
        roughness[i] = 0.9;
        height[i] = rough[i] * 1.4 - (edge < 1 ? (1 - edge) * 0.6 : 0);
      }
    }
    return done(color, n, fine, { height, strength: 2.5, rough: roughness, normalScale: 0.9, macro: 0.05 });
  }

  const KINDS = {
    carpet: (p, n, fine, rnd, size) => carpet(p, n, fine, rnd, size),
    /** Carpet tiles with a pattern woven in: a loop and a diagonal lattice in ``accent``. */
    carpetPattern: (p, n, fine, rnd, size) => {
      const t = n / Math.max(1, Math.round(size / 0.5));
      return carpet(p, n, fine, rnd, size, { pattern: (x, y) => {
        const u = (x % t) / t, v = (y % t) / t, px = t;
        const loop = band(Math.abs(Math.max(Math.abs(u - 0.5), Math.abs(v - 0.5)) - 0.3), 0.022, px);
        const lattice = Math.max(band(Math.abs(u - v), 0.012, px), band(Math.abs(u + v - 1), 0.012, px));
        return Math.max(loop, lattice * 0.8, band(Math.hypot(u - 0.5, v - 0.5), 0.05, px));
      } });
    },
    /** Prayer carpet: rows of mats (60 cm by 1.2 m), each bordered, with an arch at its head. */
    prayer: (p, n, fine, rnd, size) => {
      const mw = n / Math.max(1, Math.round(size / 0.6)), mh = n / Math.max(1, Math.round(size / 1.2)), perM = n / size;
      // in a mat's own metres: the arch's middle panel, and how far a point is outside it
      const outside = (u, v) => {
        const vs = 0.42;
        if (v < vs) return Math.max(Math.hypot(u - 0.5, v - vs) - 0.4, Math.hypot(u - 0.1, v - vs) - 0.4);
        return Math.max(0.1 - u, u - 0.5, v - 1.06);
      };
      const at = (x, y) => [((x % mw) / mw) * 0.6, ((y % mh) / mh) * 1.2];
      return carpet(p, n, fine, rnd, size, { tiles: false,
        pattern: (x, y) => {
          const [u, v] = at(x, y), edge = Math.min(u, 0.6 - u, v, 1.2 - v);
          return Math.max(band(edge, 0.012, perM), band(Math.abs(edge - 0.035), 0.004, perM), band(Math.abs(outside(u, v)), 0.006, perM));
        },
        shade: (x, y) => {
          const [u, v] = at(x, y);
          return outside(u, v) < 0 ? 1.07 : 1;
        } });
    },
    planks, tiles, terrazzo, marble, herringbone, polished, concrete, epoxy, vinyl, rubber, raised,
    paint, linen, stripes, geometric, damask, mosaic, slats, panels, stone,
  };

  return {
    kinds: Object.keys(KINDS),
    paint(kind, values, size, seed, px, fine) {
      const painter = KINDS[kind];
      if (!painter) throw new Error(`no painter for finishes of kind ${kind}`);
      return painter(values, px, Boolean(fine), random(seed), size);
    },
    /** A large, soft, tileable noise (in every channel) to vary a finish's colour over metres. */
    macro(px = 256) {
      const v = fbm(px, 4, 4, random(97), 0.55);
      const out = new Uint8ClampedArray(px * px * 4);
      for (let i = 0; i < px * px; i++) {
        out[i * 4] = out[i * 4 + 1] = out[i * 4 + 2] = v[i] * 255;
        out[i * 4 + 3] = 255;
      }
      return out;
    },
  };
}

/** A finish's seed: the same image every time. */
export function seedOf(code) {
  let h = 2166136261;
  for (let i = 0; i < code.length; i++) h = Math.imul(h ^ code.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** Paints finishes off the page's thread: in a worker made from finishes' own source,
 * else (no workers, or a page that forbids them) on the page, one finish a task.
 * ``paint(code, px, fine)`` resolves with what finishes().paint gives, and ``size`` (the
 * metres the image covers) and ``code``; a code StoreyPath does not have is refused. */
export class Painter {
  #worker = null;
  #local = null; // finishes(), on the page
  #waiting = new Map(); // job → { resolve, reject }
  #next = 0;
  #swatches = new Map(); // "code px" → its painting, made once

  constructor() {
    if (typeof Worker === "undefined" || typeof Blob === "undefined" || typeof URL?.createObjectURL !== "function") return;
    try {
      const source = `const f = (${finishes.toString()})();
onmessage = (e) => {
  const { job, kind, values, size, seed, px, fine } = e.data;
  try {
    const r = f.paint(kind, values, size, seed, px, fine);
    postMessage({ job, r }, [r.color.buffer, ...(r.normal ? [r.normal.buffer] : [])]);
  } catch (err) {
    postMessage({ job, error: String(err && err.message || err) });
  }
};`;
      const url = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
      this.#worker = new Worker(url);
      URL.revokeObjectURL(url);
      this.#worker.onmessage = ({ data: { job, r, error } }) => {
        const w = this.#waiting.get(job);
        this.#waiting.delete(job);
        if (error) w?.reject(new Error(error));
        else w?.resolve(r);
      };
      this.#worker.onerror = (e) => { // (a page that forbids it): the rest painted here
        e.preventDefault?.();
        this.#worker?.terminate();
        this.#worker = null;
        const waiting = [...this.#waiting.values()];
        this.#waiting.clear();
        for (const w of waiting) this.#here(w.args).then(w.resolve, w.reject);
      };
    } catch {
      this.#worker = null;
    }
  }

  /** A finish, painted: ``px`` square, its normal map too when ``fine``. */
  paint(code, px, fine) {
    const f = finishOf(code);
    if (!f) return Promise.reject(new Error(`no finish ${code}`));
    const args = { kind: f.paint.kind, values: f.paint, size: f.size_m, seed: seedOf(code), px, fine: Boolean(fine) };
    const painted = (r) => ({ ...r, size: f.size_m, code });
    if (!this.#worker) return this.#here(args).then(painted);
    const job = ++this.#next;
    return new Promise((resolve, reject) => {
      this.#waiting.set(job, { resolve, reject, args });
      this.#worker.postMessage({ job, ...args });
    }).then(painted);
  }

  /** A small picture of a finish (``px`` square, plain), for a palette: its painting, kept. */
  swatch(code, px = 96) {
    const key = `${code} ${px}`;
    if (!this.#swatches.has(key)) this.#swatches.set(key, this.paint(code, px, false));
    return this.#swatches.get(key);
  }

  /** Painted on the page, in a task of its own. */
  #here({ kind, values, size, seed, px, fine }) {
    return new Promise((resolve, reject) => setTimeout(() => {
      try {
        this.#local ??= finishes();
        resolve(this.#local.paint(kind, values, size, seed, px, fine));
      } catch (e) {
        reject(e);
      }
    }, 0));
  }

  dispose() {
    this.#worker?.terminate();
    this.#worker = null;
    this.#waiting.clear();
  }
}

export { FINISHES };
