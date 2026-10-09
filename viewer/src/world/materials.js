// Materials for the 3D world, one set for each look and quality (style.js), all made
// here: floor finishes are painted (finishes.js), the sky and ground on a canvas, so
// nothing is downloaded and the world works offline. A world makes a set the first
// time it is shown and swaps sets without building anything again.
//
//   real:  each room's floor and walls in their finishes (finish(code): painted, with,
//          at High, normal and roughness maps, and a far larger tint so that nothing
//          visibly repeats), painted joinery, metal handles; furniture rough or metallic
//          part by part (its `_finish`)
//   model: white clay, floors and walls lightly tinted by their finishes' tones, the
//          furniture white with a hint of its colour, dark lines along edges (`edges`)

import * as THREE from "three";
import { EXTERIOR, defaultFinish, finishOf } from "../finishes.js";
import { finishes } from "./finishes.js";

const MACRO_EVERY = 7.3; // m: the tint against repetition (not a multiple of any finish's size)

/** A seeded random number generator, for the canvases' noise. */
function random(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

/** A canvas texture painted by ``paint(ctx, size)``. */
function painted(size, paint, { repeat = true, colorSpace = THREE.SRGBColorSpace } = {}) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  paint(c.getContext("2d"), size);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = colorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

export class Materials {
  /** ``style``: "real" or "model"; ``quality``: a QUALITY (its finishes' size, and
   * whether fine); ``painter``: the Painter to paint finishes with. */
  constructor(renderer, { style, quality, painter }) {
    this.style = style;
    this.maxAnisotropy = renderer.capabilities.getMaxAnisotropy();
    this.#quality = quality;
    this.#painter = painter;
    this.#finishes = new Map();
    this.#volumes = new Map();
    this.highlight = new THREE.MeshStandardMaterial({
      color: 0xff8a00, emissive: 0xff8a00, emissiveIntensity: 0.35, transparent: true, opacity: 0.35, depthWrite: false,
    });
    // lines along edges ("model"): seen over the faces they edge, drawn a little nearer
    // (a share of the distance, so that at a glancing angle what is behind a wall is not
    // seen through it), and not in the depth the occlusion is worked out from
    this.edges = new THREE.LineBasicMaterial({ color: 0x33363b, transparent: true, opacity: 0.72, depthWrite: false });
    this.edges.onBeforeCompile = (shader) => {
      shader.vertexShader = shader.vertexShader.replace("#include <project_vertex>",
        "#include <project_vertex>\nmvPosition.xyz *= 0.997;\ngl_Position = projectionMatrix * mvPosition;");
    };
    this.edges.customProgramCacheKey = () => "storeypath-edges";
    this.#clay = style === "model";
    if (style === "model") this.#model();
    else this.#real();
  }

  #quality;
  #painter;
  #finishes; // code → its material, made the first time a surface shows it
  #volumes;
  #pending = new Set(); // finishes being painted
  #clay = false; // the "model" look
  #textures = [];
  #disposed = false;

  // ---- the "real" look --------------------------------------------------------------

  #real() {
    // (boxes — over and under openings, frames, doors, skirting, boards, handles, panels —
    // have no normals: their materials are flat-shaded)
    const flat = { flatShading: true };
    this.wall = this.finish(EXTERIOR); // (a wall's face outside every room)
    this.wallPlain = new THREE.MeshStandardMaterial({ color: 0xeae7e1, roughness: 0.9, ...flat }); // no texture coordinates
    this.wallTop = new THREE.MeshStandardMaterial({ color: 0xd9d5cd, roughness: 0.9, ...flat }); // (and over openings: boxes)
    this.wallCut = new THREE.MeshStandardMaterial({ color: 0x3a3a3f, roughness: 0.8 });
    this.slab = new THREE.MeshStandardMaterial({ color: 0xcfcbc3, roughness: 0.9 });
    this.ceiling = new THREE.MeshStandardMaterial({
      color: 0xf7f6f3, roughness: 0.95, side: THREE.DoubleSide, emissive: 0xfff8ee, emissiveIntensity: 0.42,
    });
    this.glass = new THREE.MeshPhysicalMaterial({
      color: 0xc9dde6, roughness: 0.03, metalness: 0, transparent: true, opacity: 0.2, envMapIntensity: 1.6,
      depthWrite: false, specularIntensity: 1, ...flat,
    });
    // window frames: dark aluminium; doors: oak leaves in painted frames and architraves
    this.frame = new THREE.MeshStandardMaterial({ color: 0x3f4349, roughness: 0.4, metalness: 0.55, ...flat });
    this.door = new THREE.MeshStandardMaterial({ color: 0xb48c64, roughness: 0.5, ...flat });
    this.doorFrame = new THREE.MeshStandardMaterial({ color: 0xd8d4cc, roughness: 0.5, ...flat });
    this.trim = this.doorFrame;
    this.skirting = new THREE.MeshStandardMaterial({ color: 0xe6e3dd, roughness: 0.5, ...flat });
    this.sillBoard = new THREE.MeshStandardMaterial({ color: 0xe9e6e0, roughness: 0.35, ...flat });
    this.handle = new THREE.MeshStandardMaterial({ color: 0xc4c7cb, roughness: 0.28, metalness: 1, ...flat });
    this.lightPanel = new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0xfffaf0, emissiveIntensity: 2.2, ...flat });
    // furniture: each vertex says how rough and how metallic it is (`_finish`); the
    // catalogue's colours a little quieter (they are chosen to tell types apart)
    this.item = new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 1, metalness: 1 });
    this.item.onBeforeCompile = (shader) => {
      shader.vertexShader = shader.vertexShader
        .replace("#include <common>", "#include <common>\nattribute vec2 _finish;\nvarying vec2 vFinish;")
        .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFinish = _finish;");
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <common>", "#include <common>\nvarying vec2 vFinish;")
        .replace("#include <color_fragment>",
          "#include <color_fragment>\ndiffuseColor.rgb = mix(vec3(dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722))), diffuseColor.rgb, 0.8);")
        .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\nroughnessFactor = vFinish.x;")
        .replace("#include <metalnessmap_fragment>", "#include <metalnessmap_fragment>\nmetalnessFactor = vFinish.y;");
    };
    this.item.customProgramCacheKey = () => "storeypath-item-finish";
  }

  /** A material in a finish (finishes.js): its tone on the whole at once, its image (and,
   * High, its normal and roughness map and the far larger tint) once painted. A wall's
   * finish of a metre or less is painted at 512 px at most. */
  #painted(f) {
    const fine = this.#quality.fine;
    const px = f.applies === "wall" && f.size_m <= 1 ? Math.min(512, this.#quality.finish) : this.#quality.finish;
    const m = new THREE.MeshStandardMaterial({ color: toneOf(f), roughness: f.roughness ?? 0.9 });
    const name = f.code;
    const job = this.#painter.paint(name, px, fine).then((f) => {
      if (this.#disposed) return;
      m.color.set(0xffffff);
      m.map = this.#texture(f.color, px, f.size, true);
      if (f.normal) {
        m.normalMap = this.#texture(f.normal, px, f.size, false);
        m.normalScale.set(f.normalScale, f.normalScale);
        m.roughness = f.roughness;
      }
      if (fine) this.#tint(m, f);
      m.needsUpdate = true;
    }).catch((e) => console.warn(`StoreyPathWorld: the ${name} finish could not be painted: ${e.message}`))
      .finally(() => this.#pending.delete(job));
    this.#pending.add(job);
    return m;
  }

  /** High: roughness from the normal map's alpha, and a far larger tint varying the
   * colour over metres, so that a finish does not visibly repeat. */
  #tint(m, f) {
    this.macro ??= (() => {
      const px = 256, t = this.#texture(finishes().macro(px), px, 1, false);
      t.anisotropy = 1;
      return t;
    })();
    const macro = this.macro, scale = f.size / MACRO_EVERY, amount = f.macro || 0;
    m.onBeforeCompile = (shader) => {
      shader.uniforms.macroMap = { value: macro };
      shader.uniforms.macroScale = { value: scale };
      shader.uniforms.macroAmount = { value: amount };
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <common>", "#include <common>\nuniform sampler2D macroMap;\nuniform float macroScale;\nuniform float macroAmount;")
        .replace("#include <map_fragment>", `#include <map_fragment>
          float macroV = texture2D(macroMap, vMapUv * macroScale).r + texture2D(macroMap, vMapUv * macroScale * 0.37 + 0.5).r - 1.0;
          diffuseColor.rgb *= 1.0 + macroAmount * macroV * 1.6;`)
        .replace("#include <roughnessmap_fragment>", `float roughnessFactor = roughness;
          #ifdef USE_NORMALMAP
            roughnessFactor *= texture2D(normalMap, vNormalMapUv).a;
          #endif`);
    };
    m.customProgramCacheKey = () => "storeypath-finish";
  }

  /** A texture from RGBA bytes, tiling every ``size`` metres; its bytes let go once on
   * the graphics card. */
  #texture(bytes, px, size, srgb) {
    const t = new THREE.DataTexture(new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.length), px, px, THREE.RGBAFormat);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.generateMipmaps = true;
    t.minFilter = THREE.LinearMipmapLinearFilter;
    t.magFilter = THREE.LinearFilter;
    t.anisotropy = this.maxAnisotropy;
    t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    t.repeat.set(1 / size, 1 / size);
    t.onUpdate = () => {
      t.image = { width: px, height: px, data: null };
    };
    t.needsUpdate = true;
    this.#textures.push(t);
    return t;
  }

  // ---- the "model" look -------------------------------------------------------------

  #model() {
    // flat-shaded (boxes have no normals)
    const clay = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.92, flatShading: true, ...extra });
    this.wall = this.finish(EXTERIOR);
    this.wallPlain = clay(0xedece8);
    this.wallTop = clay(0xdddbd6);
    this.wallCut = clay(0x34363b);
    this.slab = clay(0xdedcd7);
    this.ceiling = new THREE.MeshStandardMaterial({ color: 0xfbfbfa, roughness: 0.95, side: THREE.DoubleSide,
      emissive: 0xffffff, emissiveIntensity: 0.25 });
    this.glass = new THREE.MeshStandardMaterial({ color: 0xcfe0ea, roughness: 0.1, transparent: true, opacity: 0.32,
      depthWrite: false, flatShading: true });
    this.frame = clay(0xc9ccd1);
    this.door = clay(0xe8e6e1);
    this.doorFrame = clay(0xd8d6d1);
    this.trim = this.doorFrame;
    this.skirting = clay(0xeceae6);
    this.sillBoard = clay(0xeceae6);
    this.handle = clay(0xb8bbc0, { roughness: 0.5 });
    this.lightPanel = new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0xffffff, emissiveIntensity: 1.2, flatShading: true });
    // furniture: white, keeping a hint of its colour
    this.item = clay(0xffffff, { vertexColors: true, flatShading: true, roughness: 0.85 });
    this.item.onBeforeCompile = (shader) => {
      shader.fragmentShader = shader.fragmentShader.replace("#include <color_fragment>",
        "#if defined( USE_COLOR )\n diffuseColor.rgb *= mix(vec3(1.0), vColor.rgb, 0.16);\n#endif");
    };
    this.item.customProgramCacheKey = () => "storeypath-item-clay";
  }

  // ---- by finish, and the rest ------------------------------------------------------

  /** The material of a finish (a code of ../finishes.js; one it does not have: the
   * exterior's): real, painted; model, clay lightly tinted by its tone (a floor's more
   * than a wall's). Made the first time it is asked for. */
  finish(code) {
    const f = finishOf(code) ?? finishOf(EXTERIOR);
    if (!this.#finishes.has(f.code)) {
      let m;
      if (this.#clay) {
        const c = toneOf(f), hsl = c.getHSL({});
        if (f.applies === "floor") c.setHSL(hsl.h, hsl.s * 0.55, 0.74 + hsl.l * 0.14);
        else c.setHSL(hsl.h, hsl.s * 0.4, 0.86 + hsl.l * 0.08);
        m = new THREE.MeshStandardMaterial({ color: c, roughness: 0.92, flatShading: f.applies === "wall" });
      } else m = this.#painted(f);
      m.userData.finish = f.code;
      this.#finishes.set(f.code, m);
    }
    return this.#finishes.get(f.code);
  }

  /** The floor material of a type of space, as it is when given no finish. */
  floor(type) {
    return this.finish(defaultFinish("floor", type));
  }

  /** The materials of walls' finishes made so far (to see through them: x-ray). */
  walls() {
    return [...this.#finishes.values()].filter((m) => finishOf(m.userData.finish)?.applies === "wall");
  }

  /** A see-through volume tinted by a space's type (x-ray view). */
  volume(color) {
    if (!this.#volumes.has(color)) {
      this.#volumes.set(color, new THREE.MeshStandardMaterial({
        color, transparent: true, opacity: 0.22, depthWrite: false, roughness: 0.6, side: THREE.DoubleSide,
      }));
    }
    return this.#volumes.get(color);
  }

  /** The ground round the building. */
  ground() {
    if (!this._ground) {
      const model = this.style === "model";
      const texture = painted(256, (ctx, n) => {
        ctx.fillStyle = model ? "rgb(236,236,233)" : "rgb(190,192,190)";
        ctx.fillRect(0, 0, n, n);
        const rnd = random(17), alpha = model ? 0.012 : 0.035;
        for (let i = 0; i < 8000; i++) {
          const v = Math.floor(rnd() * 255);
          ctx.fillStyle = `rgba(${v},${v},${v},${alpha})`;
          ctx.fillRect(rnd() * n, rnd() * n, 1 + rnd() * 2, 1 + rnd() * 2);
        }
      });
      texture.anisotropy = this.maxAnisotropy;
      texture.repeat.set(1 / 6, 1 / 6);
      this.#textures.push(texture);
      // kept out of the depth the occlusion reads: far off, at a glancing angle, it would blotch
      this._ground = new THREE.MeshStandardMaterial({ map: texture, roughness: 1, depthWrite: false });
    }
    return this._ground;
  }

  /** The sky: a soft gradient behind everything. */
  sky([top, bottom]) {
    if (!this._sky) {
      this._sky = painted(256, (ctx) => {
        const g = ctx.createLinearGradient(0, 0, 0, 256);
        g.addColorStop(0, top);
        g.addColorStop(0.62, bottom);
        g.addColorStop(1, bottom);
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, 256, 256);
      }, { repeat: false });
      this.#textures.push(this._sky);
    }
    return this._sky;
  }

  /** Resolves once the finishes asked for so far are painted. */
  ready() {
    return Promise.all([...this.#pending]).then(() => (this.#pending.size ? this.ready() : undefined));
  }

  dispose() {
    this.#disposed = true;
    const seen = new Set();
    for (const v of [...Object.values(this), ...this.#finishes.values(), ...this.#volumes.values()]) {
      if (v?.isMaterial && !seen.has(v)) {
        seen.add(v);
        v.dispose();
      }
    }
    for (const t of this.#textures) t.dispose();
  }
}

/** A finish's colour on the whole (its tone), shown until its image is painted. */
function toneOf(f) {
  return new THREE.Color().setStyle(f.tone, THREE.SRGBColorSpace);
}
