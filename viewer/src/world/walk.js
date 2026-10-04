// Walking through a floor in the first person: mouse to look, WASD to move,
// walls and windows to bump into.

import * as THREE from "three";
import { PointerLockControls } from "three/addons/controls/PointerLockControls.js";

const CELL = 2; // m, collision grid
const KEYS = { KeyW: "f", ArrowUp: "f", KeyS: "b", ArrowDown: "b", KeyA: "l", ArrowLeft: "l", KeyD: "r", ArrowRight: "r" };

/** Wall edges of one floor, bucketed for quick collision checks. */
export class Obstacles {
  constructor(segments) {
    this.segments = segments; // [[x1, z1, x2, z2], …]
    this.grid = new Map();
    segments.forEach((s, i) => {
      const [x1, z1, x2, z2] = s;
      for (let gx = Math.floor(Math.min(x1, x2) / CELL); gx <= Math.floor(Math.max(x1, x2) / CELL); gx++) {
        for (let gz = Math.floor(Math.min(z1, z2) / CELL); gz <= Math.floor(Math.max(z1, z2) / CELL); gz++) {
          const key = `${gx},${gz}`;
          if (!this.grid.has(key)) this.grid.set(key, []);
          this.grid.get(key).push(i);
        }
      }
    });
  }

  near(x, z) {
    const out = new Set();
    const gx = Math.floor(x / CELL), gz = Math.floor(z / CELL);
    for (let i = -1; i <= 1; i++) {
      for (let j = -1; j <= 1; j++) for (const k of this.grid.get(`${gx + i},${gz + j}`) || []) out.add(k);
    }
    return out;
  }

  /** Move a circle of ``radius`` from (x, z) by (dx, dz), sliding along walls. */
  move(x, z, dx, dz, radius) {
    let nx = x + dx, nz = z + dz;
    for (let pass = 0; pass < 3; pass++) {
      let pushed = false;
      for (const k of this.near(nx, nz)) {
        const [x1, z1, x2, z2] = this.segments[k];
        const ex = x2 - x1, ez = z2 - z1;
        const len2 = ex * ex + ez * ez || 1e-9;
        const t = Math.max(0, Math.min(1, ((nx - x1) * ex + (nz - z1) * ez) / len2));
        const px = x1 + t * ex, pz = z1 + t * ez;
        const ox = nx - px, oz = nz - pz;
        const d = Math.hypot(ox, oz);
        if (d < radius && d > 1e-9) {
          nx = px + (ox / d) * radius;
          nz = pz + (oz / d) * radius;
          pushed = true;
        }
      }
      if (!pushed) break;
    }
    return [nx, nz];
  }
}

export class Walker extends EventTarget {
  constructor(camera, element, { eye = 1.6, radius = 0.22, speed = 1.7, run = 3.6 } = {}) {
    super();
    this.camera = camera;
    this.eye = eye;
    this.radius = radius;
    this.speed = speed;
    this.run = run;
    this.controls = new PointerLockControls(camera, element);
    this.controls.pointerSpeed = 0.8;
    this.obstacles = new Obstacles([]);
    this.floorY = 0;
    this.keys = new Set();
    this.fast = false;
    this.enabled = false;
    this.controls.addEventListener("lock", () => this.dispatchEvent(new Event("lock")));
    this.controls.addEventListener("unlock", () => this.dispatchEvent(new Event("unlock")));
    this._down = (e) => {
      if (!this.enabled) return;
      if (KEYS[e.code]) { this.keys.add(KEYS[e.code]); e.preventDefault(); }
      if (e.code === "ShiftLeft" || e.code === "ShiftRight") this.fast = true;
    };
    this._up = (e) => {
      if (KEYS[e.code]) this.keys.delete(KEYS[e.code]);
      if (e.code === "ShiftLeft" || e.code === "ShiftRight") this.fast = false;
    };
    window.addEventListener("keydown", this._down);
    window.addEventListener("keyup", this._up);
  }

  get locked() {
    return this.controls.isLocked;
  }

  lock() {
    this.controls.lock();
  }

  unlock() {
    this.controls.unlock();
  }

  /** Stand at (x, z) on a floor whose walking surface is at ``y``. */
  place(x, z, y, obstacles, heading) {
    this.floorY = y;
    this.obstacles = obstacles;
    this.camera.position.set(x, y + this.eye, z);
    if (heading !== undefined) this.camera.rotation.set(0, heading, 0, "YXZ");
  }

  update(dt) {
    if (!this.enabled) return;
    const forward = new THREE.Vector3();
    this.camera.getWorldDirection(forward);
    forward.y = 0;
    forward.normalize();
    const right = new THREE.Vector3(-forward.z, 0, forward.x);
    const move = new THREE.Vector3();
    if (this.keys.has("f")) move.add(forward);
    if (this.keys.has("b")) move.sub(forward);
    if (this.keys.has("r")) move.add(right);
    if (this.keys.has("l")) move.sub(right);
    if (move.lengthSq() > 0) {
      move.normalize().multiplyScalar((this.fast ? this.run : this.speed) * Math.min(dt, 0.1));
      const p = this.camera.position;
      const [x, z] = this.obstacles.move(p.x, p.z, move.x, move.z, this.radius);
      p.set(x, this.floorY + this.eye, z);
    }
  }

  dispose() {
    window.removeEventListener("keydown", this._down);
    window.removeEventListener("keyup", this._up);
    this.controls.dispose();
  }
}
