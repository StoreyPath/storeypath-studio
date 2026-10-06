// Materials for the 3D world, all drawn here: floor finishes are painted on a
// canvas, so nothing is downloaded and the world works offline.

import * as THREE from "three";

const TEXTURE_PX = 512;

/** A seeded random number generator, so finishes look the same every time. */
function random(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function canvas(paint) {
  const c = document.createElement("canvas");
  c.width = c.height = TEXTURE_PX;
  paint(c.getContext("2d"), TEXTURE_PX);
  return c;
}

function noise(ctx, size, amount, rnd, alpha = 0.06) {
  for (let i = 0; i < amount; i++) {
    const v = Math.floor(rnd() * 255);
    ctx.fillStyle = `rgba(${v},${v},${v},${alpha})`;
    ctx.fillRect(rnd() * size, rnd() * size, 1 + rnd() * 2, 1 + rnd() * 2);
  }
}

const FINISHES = {
  // metres one texture covers, and how to paint it
  wood: { size: 2.4, paint(ctx, n, rnd) {
    const planks = 14;
    const w = n / planks;
    for (let i = 0; i < planks; i++) {
      let y = -rnd() * n;
      while (y < n) {
        const len = n * (0.35 + rnd() * 0.5);
        const tone = 0.85 + rnd() * 0.25;
        ctx.fillStyle = `rgb(${Math.round(176 * tone)},${Math.round(132 * tone)},${Math.round(92 * tone)})`;
        ctx.fillRect(i * w, y, w - 1, len - 1);
        ctx.strokeStyle = "rgba(90,60,35,0.12)";
        for (let g = 0; g < 6; g++) {
          ctx.beginPath();
          const gx = i * w + rnd() * w;
          ctx.moveTo(gx, y);
          ctx.bezierCurveTo(gx + rnd() * 4 - 2, y + len / 3, gx + rnd() * 4 - 2, y + 2 * len / 3, gx, y + len);
          ctx.stroke();
        }
        y += len;
      }
    }
    noise(ctx, n, 6000, rnd, 0.05);
  }, roughness: 0.55 },
  tile: { size: 1.2, paint(ctx, n, rnd) {
    const tiles = 2;
    const t = n / tiles;
    for (let i = 0; i < tiles; i++) {
      for (let j = 0; j < tiles; j++) {
        const tone = 0.95 + rnd() * 0.06;
        ctx.fillStyle = `rgb(${Math.round(226 * tone)},${Math.round(224 * tone)},${Math.round(218 * tone)})`;
        ctx.fillRect(i * t, j * t, t, t);
      }
    }
    noise(ctx, n, 9000, rnd, 0.04);
    ctx.strokeStyle = "rgb(170,166,158)";
    ctx.lineWidth = 3;
    for (let k = 0; k <= tiles; k++) {
      ctx.beginPath(); ctx.moveTo(k * t, 0); ctx.lineTo(k * t, n); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, k * t); ctx.lineTo(n, k * t); ctx.stroke();
    }
  }, roughness: 0.25 },
  stone: { size: 1.6, paint(ctx, n, rnd) {
    ctx.fillStyle = "rgb(214,208,198)";
    ctx.fillRect(0, 0, n, n);
    ctx.strokeStyle = "rgba(140,130,118,0.18)";
    for (let v = 0; v < 18; v++) {
      ctx.lineWidth = 0.5 + rnd() * 1.5;
      ctx.beginPath();
      let x = rnd() * n, y = rnd() * n;
      ctx.moveTo(x, y);
      for (let k = 0; k < 6; k++) {
        x += (rnd() - 0.4) * 90; y += (rnd() - 0.5) * 90;
        ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    noise(ctx, n, 8000, rnd, 0.04);
    ctx.strokeStyle = "rgba(120,112,100,0.5)";
    ctx.lineWidth = 2;
    ctx.strokeRect(0, 0, n, n);
  }, roughness: 0.3 },
  carpet: { size: 1.0, paint(ctx, n, rnd) {
    ctx.fillStyle = "rgb(122,128,138)";
    ctx.fillRect(0, 0, n, n);
    noise(ctx, n, 40000, rnd, 0.12);
  }, roughness: 0.95 },
  deck: { size: 2.4, paint(ctx, n, rnd) {
    const boards = 12;
    const w = n / boards;
    ctx.fillStyle = "rgb(60,52,44)";
    ctx.fillRect(0, 0, n, n);
    for (let i = 0; i < boards; i++) {
      const tone = 0.85 + rnd() * 0.2;
      ctx.fillStyle = `rgb(${Math.round(150 * tone)},${Math.round(128 * tone)},${Math.round(104 * tone)})`;
      ctx.fillRect(i * w + 1.5, 0, w - 3, n);
    }
    noise(ctx, n, 6000, rnd, 0.06);
  }, roughness: 0.7 },
  concrete: { size: 3.0, paint(ctx, n, rnd) {
    ctx.fillStyle = "rgb(186,184,180)";
    ctx.fillRect(0, 0, n, n);
    noise(ctx, n, 30000, rnd, 0.07);
  }, roughness: 0.85 },
  asphalt: { size: 3.0, paint(ctx, n, rnd) {
    ctx.fillStyle = "rgb(92,94,98)";
    ctx.fillRect(0, 0, n, n);
    noise(ctx, n, 40000, rnd, 0.15);
  }, roughness: 0.9 },
  ground: { size: 6.0, paint(ctx, n, rnd) {
    ctx.fillStyle = "rgb(190,186,170)";
    ctx.fillRect(0, 0, n, n);
    noise(ctx, n, 30000, rnd, 0.05);
  }, roughness: 1.0 },
};

/** Which finish each kind of space has. */
export const FINISH_OF = {
  bedroom: "wood", living_room: "wood", dining_room: "wood", dressing_room: "wood", office: "wood",
  meeting_room: "carpet", open_area: "carpet", prayer_room: "carpet", room: "wood",
  bathroom: "tile", restroom: "tile", kitchen: "tile", laundry: "tile",
  lobby: "stone", corridor: "stone", stairs: "stone", elevator: "stone", escalator: "stone", ramp: "stone",
  balcony: "deck", terrace: "deck", parking: "asphalt",
  storage: "concrete", utility: "concrete", shaft: "concrete", unspecified: "concrete",
};

/** Plaster over a skirting board. The sides of extruded walls have v = 1 − height
 * (in metres), so the texture is mapped to cover heights 0–4 m, floor at the top. */
function wallTexture() {
  const c = document.createElement("canvas");
  c.width = 8;
  c.height = 512;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "rgb(241,237,230)";
  ctx.fillRect(0, 0, 8, 512);
  const board = Math.round(512 * (0.09 / 4));
  ctx.fillStyle = "rgb(122,112,102)";
  ctx.fillRect(0, 0, 8, board);
  ctx.fillStyle = "rgba(0,0,0,0.12)"; // the board's top edge
  ctx.fillRect(0, board, 8, 2);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.repeat.set(1, 0.25);
  texture.offset.set(0, 0.75);
  return texture;
}

export class Materials {
  constructor(renderer) {
    this.maxAnisotropy = renderer.capabilities.getMaxAnisotropy();
    this._floors = new Map();
    this._volumes = new Map();
    // Wall faces: plaster, with a skirting board along the floor.
    this.wall = new THREE.MeshStandardMaterial({ map: wallTexture(), roughness: 0.92 });
    // Heads over doors and windows, and sills: plaster only.
    this.wallPlain = new THREE.MeshStandardMaterial({ color: 0xf1ede6, roughness: 0.92 });
    // The cut top of a wall, as on an architect's section: dark and crisp.
    this.wallCut = new THREE.MeshStandardMaterial({ color: 0x3a3a3f, roughness: 0.8 });
    this.wallTop = new THREE.MeshStandardMaterial({ color: 0xcfc9bf, roughness: 0.9 });
    this.slab = new THREE.MeshStandardMaterial({ color: 0xd8d4cc, roughness: 0.9 });
    this.ceiling = new THREE.MeshStandardMaterial({
      color: 0xfbfaf7, roughness: 0.95, side: THREE.DoubleSide, emissive: 0xfff6ea, emissiveIntensity: 0.18,
    });
    this.glass = new THREE.MeshPhysicalMaterial({
      color: 0xbcd6e4, roughness: 0.04, metalness: 0, transparent: true, opacity: 0.28,
      envMapIntensity: 1.2, depthWrite: false,
    });
    // Window frames: aluminium. Doors: a wooden leaf in a darker wooden frame.
    this.frame = new THREE.MeshStandardMaterial({ color: 0x5b5f66, roughness: 0.45, metalness: 0.35 });
    this.door = new THREE.MeshStandardMaterial({ color: 0x9a7350, roughness: 0.55 });
    this.doorFrame = new THREE.MeshStandardMaterial({ color: 0x6b4a32, roughness: 0.6 });
    this.highlight = new THREE.MeshStandardMaterial({
      color: 0xff8a00, emissive: 0xff8a00, emissiveIntensity: 0.35, transparent: true, opacity: 0.35, depthWrite: false,
    });
  }

  /** The floor material for a type of space. */
  floor(type) {
    const finish = FINISH_OF[type] || "concrete";
    if (!this._floors.has(finish)) {
      const spec = FINISHES[finish];
      const texture = new THREE.CanvasTexture(canvas((ctx, n) => spec.paint(ctx, n, random(finish.length * 7919))));
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.repeat.set(1 / spec.size, 1 / spec.size);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = this.maxAnisotropy;
      this._floors.set(finish, new THREE.MeshStandardMaterial({ map: texture, roughness: spec.roughness }));
    }
    return this._floors.get(finish);
  }

  ground() {
    if (!this._groundMaterial) {
      const spec = FINISHES.ground;
      const texture = new THREE.CanvasTexture(canvas((ctx, n) => spec.paint(ctx, n, random(17))));
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.repeat.set(1 / spec.size, 1 / spec.size);
      texture.colorSpace = THREE.SRGBColorSpace;
      this._groundMaterial = new THREE.MeshStandardMaterial({ map: texture, roughness: 1 });
    }
    return this._groundMaterial;
  }

  /** The sky: a soft gradient behind everything. */
  sky(top = "#9fc3e6", bottom = "#eef1f2") {
    const c = document.createElement("canvas");
    c.width = 2;
    c.height = 256;
    const ctx = c.getContext("2d");
    const g = ctx.createLinearGradient(0, 0, 0, 256);
    g.addColorStop(0, top);
    g.addColorStop(0.62, bottom);
    g.addColorStop(1, bottom);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 2, 256);
    const texture = new THREE.CanvasTexture(c);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }

  /** A see-through volume tinted by a space's type (x-ray view). */
  volume(color) {
    if (!this._volumes.has(color)) {
      this._volumes.set(color, new THREE.MeshStandardMaterial({
        color, transparent: true, opacity: 0.22, depthWrite: false, roughness: 0.6, side: THREE.DoubleSide,
      }));
    }
    return this._volumes.get(color);
  }
}
