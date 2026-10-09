// The finishes of the "real" look, painted here — nothing is downloaded: carpet tiles,
// terrazzo, polished concrete with its joints, porcelain tiles, plain concrete, oak
// planks and wall plaster, each a square image that tiles, with (for High quality) a
// normal map whose alpha is its roughness. Painting a 1024 px finish takes tens of
// milliseconds, so it is done off the page's thread when it can be (a worker made from
// this very code: `painter`), and the world shows each floor in its finish's colour
// until its image comes.
//
// `finishes` is self-contained (it refers to nothing outside itself), so that its
// source, as the browser has it, is the worker's.

/** The finishes: ``paint(name, px, fine)`` → { size (metres the image covers), color
 * (RGBA bytes, px²), normal (RGBA: the normal, and roughness in alpha; only when
 * ``fine``), roughness (its scale), normalScale, macro (how much a far larger tint
 * varies it, so that it does not visibly repeat) }; ``tone(name)``, its colour on the
 * whole ([r, g, b], 0–255), to show before its image; ``macro(px)``, that tint. */
export function finishes() {
  /** A seeded random number generator: a finish looks the same every time. */
  function random(seed) {
    let s = seed >>> 0;
    return () => {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
    };
  }

  /** Tileable value noise of n² samples, ``cells`` lattice cells across, as fractal
   * octaves: values about 0–1. */
  function fbm(n, cells, octaves, rnd, gain = 0.5) {
    const out = new Float32Array(n * n);
    let amp = 1, total = 0;
    for (let o = 0; o < octaves; o++) {
      const c = Math.min(cells << o, n);
      const lattice = new Float32Array(c * c);
      for (let i = 0; i < lattice.length; i++) lattice[i] = rnd();
      const k = c / n;
      for (let y = 0; y < n; y++) {
        const fy = y * k, y0 = Math.floor(fy), ty = fy - y0, sy = ty * ty * (3 - 2 * ty);
        const r0 = (y0 % c) * c, r1 = ((y0 + 1) % c) * c;
        for (let x = 0; x < n; x++) {
          const fx = x * k, x0 = Math.floor(fx), tx = fx - x0, sx = tx * tx * (3 - 2 * tx);
          const c0 = x0 % c, c1 = (x0 + 1) % c;
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

  const FINE = {
    /** Carpet tiles, 50 cm, laid quarter-turned: fibres one way, then across. */
    carpet(seed, n, fine, base = [92, 97, 106]) {
      const rnd = random(seed);
      const tiles = 4, t = n / tiles;
      const streaks = fbm(n, 8, 3, rnd);
      const grain = fbm(n, Math.min(256, n / 4), 2, rnd);
      const color = image(n), height = fine ? new Float32Array(n * n) : null;
      const tone = Array.from({ length: tiles * tiles }, () => 1 + (rnd() - 0.5) * 0.07);
      const seam = Math.max(1, Math.round(n / 1024));
      for (let y = 0; y < n; y++) {
        for (let x = 0; x < n; x++) {
          const tx = Math.floor(x / t), ty = Math.floor(y / t);
          const across = (tx + ty) % 2 === 0;
          const sx = across ? (x * 7) % n : x, sy = across ? y : (y * 7) % n; // streaks along the pile
          const g = grain[y * n + x] - 0.5, fleck = rnd();
          let k = tone[ty * tiles + tx] * (1 + (streaks[sy * n + sx] - 0.5) * 0.12 + g * 0.22);
          if (fleck < 0.012) k *= 1.25;
          else if (fleck < 0.024) k *= 0.8;
          const edge = x % t < seam || y % t < seam;
          if (edge) k *= 0.88;
          set(color, y * n + x, base[0] * k, base[1] * k, base[2] * k);
          if (height) height[y * n + x] = g * 0.9 + (edge ? -0.4 : 0);
        }
      }
      return { size: 2.0, color, normal: fine && normalMap(height, n, 1.6, 1), normalScale: 0.5, roughness: 1, macro: 0.1 };
    },

    /** Terrazzo: chips of marble in a pale binder, polished, in bays with zinc strips. */
    terrazzo(seed, n, fine) {
      const rnd = random(seed), k = n / 1024;
      const binder = fbm(n, 6, 4, rnd);
      const palette = [[[246, 244, 239], 34], [[205, 202, 196], 26], [[168, 165, 160], 16], [[128, 126, 124], 8],
        [[212, 196, 170], 9], [[86, 86, 88], 3], [[178, 122, 92], 2], [[150, 160, 166], 2]];
      const total = palette.reduce((s, [, w]) => s + w, 0);
      const pick = () => {
        let r = rnd() * total;
        for (const [c, w] of palette) if ((r -= w) <= 0) return c;
        return palette[0][0];
      };
      const shapes = [];
      for (let i = 0; i < 9000; i++) {
        const x = rnd() * n, y = rnd() * n, r = (1.2 + rnd() ** 2.2 * 9) * k, sides = 4 + Math.floor(rnd() * 4);
        const [cr, cg, cb] = pick(), v = 0.94 + rnd() * 0.12;
        const pts = Array.from({ length: sides }, (_, s) => {
          const a = (s / sides) * Math.PI * 2 + rnd() * 0.8, d = r * (0.6 + rnd() * 0.5);
          return [x + Math.cos(a) * d, y + Math.sin(a) * d];
        });
        shapes.push({ pts, rgb: [cr * v, cg * v, cb * v] });
      }
      const chips = fill(n, shapes);
      const color = image(n), rough = new Float32Array(n * n), strip = Math.max(1, Math.round(2 * k));
      for (let i = 0; i < n * n; i++) {
        const b = 0.97 + (binder[i] - 0.5) * 0.08;
        let r = 226 * b, g = 222 * b, bl = 214 * b;
        const a = chips[i * 4 + 3] / 255;
        r += (chips[i * 4] - r) * a; g += (chips[i * 4 + 1] - g) * a; bl += (chips[i * 4 + 2] - bl) * a;
        if (i % n < strip || i < n * strip) { r = 182; g = 180; bl = 176; } // the divider strips
        set(color, i, r, g, bl);
        rough[i] = 0.16 + (binder[i] - 0.5) * 0.12;
      }
      return { size: 1.5, color, normal: fine && normalMap(null, n, 0, rough), normalScale: 0, roughness: 1, macro: 0.06 };
    },

    /** Polished concrete: mottled, fine aggregate, and saw-cut joints every 3 m. */
    polished(seed, n, fine) {
      const rnd = random(seed);
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
        set(color, i, 182 * k, 180 * k, 176 * k);
        rough[i] = 0.28 + (mottle[i] - 0.5) * 0.3 + (cut ? 0.5 : 0);
        height[i] = cut ? -1 : 0;
      }
      return { size: 3.0, color, normal: fine && normalMap(height, n, 2, rough), normalScale: 0.6, roughness: 1, macro: 0.08 };
    },

    /** Porcelain tiles, 60 cm, with narrow grout. */
    porcelain(seed, n, fine, base = [216, 214, 209]) {
      const rnd = random(seed), k = n / 1024;
      const tiles = 2, t = n / tiles, grout = Math.max(1, Math.round(3 * k)), ease = 3 * k;
      const vein = fbm(n, 6, 5, rnd, 0.6), grain = fbm(n, Math.min(200, n / 4), 1, rnd);
      const tone = Array.from({ length: tiles * tiles }, () => 1 + (rnd() - 0.5) * 0.05);
      const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
      for (let i = 0; i < n * n; i++) {
        const x = i % n, y = (i / n) | 0, gx = x % t, gy = y % t;
        if (gx < grout || gy < grout) {
          set(color, i, 164, 161, 155);
          rough[i] = 0.9;
          height[i] = -1;
          continue;
        }
        const s = tone[Math.floor(y / t) * tiles + Math.floor(x / t)] * (1 + (vein[i] - 0.5) * 0.07 + (grain[i] - 0.5) * 0.04);
        set(color, i, base[0] * s, base[1] * s, base[2] * s);
        rough[i] = 0.22 + (grain[i] - 0.5) * 0.06;
        const e = Math.min(gx - grout, gy - grout, t - gx, t - gy); // the tile's edge, eased
        height[i] = e < ease ? -0.5 + e / (2 * ease) : 0;
      }
      return { size: 1.2, color, normal: fine && normalMap(height, n, 3 / Math.max(k, 0.5), rough), normalScale: 0.7, roughness: 1,
        macro: 0.05 };
    },

    /** Plain concrete: matte, mottled and stained. */
    concrete(seed, n, fine) {
      const rnd = random(seed);
      const mottle = fbm(n, 4, 6, rnd, 0.55), stains = fbm(n, 3, 3, rnd), grain = fbm(n, Math.min(256, n / 4), 1, rnd);
      const color = image(n), height = new Float32Array(n * n);
      for (let i = 0; i < n * n; i++) {
        let k = 0.97 + (mottle[i] - 0.5) * 0.24 + (grain[i] - 0.5) * 0.08;
        if (stains[i] > 0.62) k *= 1 - (stains[i] - 0.62) * 0.6;
        set(color, i, 166 * k, 164 * k, 160 * k);
        height[i] = grain[i] * 0.6 + mottle[i] * 0.4;
      }
      return { size: 4.0, color, normal: fine && normalMap(height, n, 1.2, 0.88), normalScale: 0.4, roughness: 1, macro: 0.08 };
    },

    /** Oak planks, 20 cm wide, staggered, with their grain and seams. */
    oak(seed, n, fine) {
      const rnd = random(seed);
      const planks = 12, w = n / planks, k = n / 1024;
      const grain = fbm(n, 16, 4, rnd);
      const color = image(n), rough = new Float32Array(n * n), height = new Float32Array(n * n);
      const columns = Array.from({ length: planks }, () => {
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
          let s = run.tone * (0.92 + (g - 0.5) * 0.25 + lines * 0.06);
          const edge = gx < seam || along < seam;
          if (edge) s *= 0.55;
          const warm = 1 + (run.hue - 0.5) * 0.08;
          set(color, y * n + x, 186 * s * warm, 146 * s, (104 * s) / warm);
          rough[y * n + x] = 0.42 + lines * 0.12 + (edge ? 0.4 : 0);
          height[y * n + x] = edge ? -1 : lines * 0.15;
        }
      }
      return { size: 2.4, color, normal: fine && normalMap(height, n, 1.5, rough), normalScale: 0.6, roughness: 1, macro: 0.07 };
    },

    /** Plaster, for walls: off-white with a faint orange peel. */
    plaster(seed, n, fine) {
      const rnd = random(seed);
      const peel = fbm(n, Math.min(96, n / 4), 3, rnd), cloud = fbm(n, 4, 3, rnd);
      const color = image(n);
      for (let i = 0; i < n * n; i++) {
        const k = 1 + (cloud[i] - 0.5) * 0.025 + (peel[i] - 0.5) * 0.02;
        set(color, i, 234 * k, 231 * k, 225 * k);
      }
      return { size: 1.0, color, normal: fine && normalMap(peel, n, 2.2, 0.9), normalScale: 0.1, roughness: 1, macro: 0.03 };
    },
  };
  FINE.carpetWarm = (seed, n, fine) => FINE.carpet(seed, n, fine, [118, 108, 100]);
  FINE.porcelainWarm = (seed, n, fine) => FINE.porcelain(seed, n, fine, [206, 198, 186]);

  const TONES = { carpet: [92, 97, 106], carpetWarm: [118, 108, 100], terrazzo: [218, 214, 207], polished: [178, 176, 172],
    porcelain: [214, 212, 207], porcelainWarm: [204, 196, 184], concrete: [162, 160, 156], oak: [178, 138, 98],
    plaster: [234, 231, 225] };

  return {
    names: Object.keys(FINE),
    paint: (name, px, fine) => FINE[name](name.length * 7919 + 3, px, fine),
    tone: (name) => TONES[name] ?? [180, 180, 180],
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

/** Which finish each kind of space has: carpet tiles in offices and meeting rooms,
 * terrazzo in lobbies, on stairs and at lifts, polished concrete in corridors,
 * porcelain tiles in restrooms and kitchens, concrete in plant rooms and stores, oak
 * in homes. */
export const FINISH_OF = {
  office: "carpet", meeting_room: "carpetWarm", open_area: "carpet", prayer_room: "carpetWarm", room: "carpet",
  bedroom: "oak", living_room: "oak", dining_room: "oak", dressing_room: "oak",
  lobby: "terrazzo", stairs: "terrazzo", elevator: "terrazzo", escalator: "terrazzo", ramp: "terrazzo",
  corridor: "polished",
  bathroom: "porcelain", restroom: "porcelain", kitchen: "porcelainWarm", laundry: "porcelain",
  storage: "concrete", utility: "concrete", shaft: "concrete", unspecified: "concrete", parking: "concrete",
  balcony: "porcelainWarm", terrace: "porcelainWarm",
};

/** Paints finishes (finishes().paint) off the page's thread: in a worker made from
 * finishes' own source, else (no workers, or a page that forbids them) on the page,
 * one finish a task. ``paint`` resolves with what finishes().paint gives. */
export class Painter {
  #worker = null;
  #local = null; // finishes(), on the page
  #waiting = new Map(); // job → { resolve, reject }
  #next = 0;

  constructor() {
    if (typeof Worker === "undefined" || typeof Blob === "undefined" || typeof URL?.createObjectURL !== "function") return;
    try {
      const source = `const f = (${finishes.toString()})();
onmessage = (e) => {
  const { job, name, px, fine } = e.data;
  try {
    const r = f.paint(name, px, fine);
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
  paint(name, px, fine) {
    const args = { name, px, fine };
    if (!this.#worker) return this.#here(args);
    const job = ++this.#next;
    return new Promise((resolve, reject) => {
      this.#waiting.set(job, { resolve, reject, args });
      this.#worker.postMessage({ job, ...args });
    });
  }

  /** Painted on the page, in a task of its own. */
  #here({ name, px, fine }) {
    return new Promise((resolve, reject) => setTimeout(() => {
      try {
        this.#local ??= finishes();
        resolve(this.#local.paint(name, px, fine));
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
