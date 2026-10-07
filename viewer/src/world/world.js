// StoreyPathWorld: a building from a StoreyPath package as a 3D world you can
// look at from outside (the dollhouse view) or walk through (the walk view).
//
//   const world = new StoreyPathWorld("#world");
//   await world.open("/campus.storeypath");
//   world.setMode("walk");
//
// Walls, door and window openings come from the package (the floor's `walls`, the
// openings' `span`, and a door's `swings`: its leaves, open as the plan draws them),
// floor finishes follow each space's type, and everything is drawn here: no
// textures or models are downloaded. A floor's geometry is built by build.js, here,
// or comes pre-built in the package (format 0.5: world/<floor-id>.glb, baked by
// the same build.js in Node), which is quicker to show on a slow machine.
//
// Furniture and equipment (format 0.6: desks, photocopiers, access points, …) are
// drawn only when asked for, or when one floor is shown: a building of thousands of
// desks costs nothing until then. Their geometry is made (or taken from the
// pre-built file) the first time a floor shows them: detailed when one floor is
// shown, a box each when more are.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

import { loadPackage } from "../package.js";
import { TYPE_COLORS } from "../theme.js";
import {
  BUILDER, GEOMETRY, buildItems, buildPieces, cutPieces, flat, inside, itemBox, itemPoint, originOf, planFloor,
  toLocal as localOf,
} from "./build.js";
import { Materials } from "./materials.js";
import { Obstacles, Walker } from "./walk.js";

const DEFAULTS = {
  ...GEOMETRY, // slab, door and window heights, wall thickness, cutaway height
  explode: 0, // m between floors in the dollhouse view
  labels: true,
  showHidden: false,
  items: null, // furniture and equipment: true, false, or null: shown when one floor is
  fog: 0xeef1f2,
};
const LABEL_STYLE = {
  display: "flex", flexDirection: "column", alignItems: "center", padding: "2px 7px", borderRadius: "7px",
  background: "rgba(255, 255, 255, 0.86)", color: "#16181d", whiteSpace: "nowrap",
  font: '600 11px/1.25 system-ui, -apple-system, "Segoe UI", sans-serif', boxShadow: "0 1px 4px rgba(0, 0, 0, 0.18)",
};
const VERTICAL = new Set(["stairs", "elevator", "escalator", "ramp"]);
// what casts and takes shadows, by material; the order drawn in, after the rest
const CASTS = new Set(["slab", "wall", "wallTop", "wallCut", "wallPlain", "frame", "doorFrame", "door", "item"]);
const TAKES = new Set([...CASTS, "floor", "glass"]);
const ORDER = { volume: 2, glass: 4 };

export class StoreyPathWorld extends EventTarget {
  #o;
  #element;
  #renderer;
  #labelRenderer;
  #scene;
  #camera;
  #orbit;
  #walker;
  #materials;
  #sun;
  #lamp;
  #pkg = null;
  #origin = null;
  #building = null;
  #buildingGroup = null;
  #floors = new Map(); // floor id → built floor
  #baked = new Map(); // floor id → its pre-built 3D as read, or null to build it here
  #asked = 0; // the last building asked for
  #floor = null; // the floor shown on its own, or null for all
  #mode = "dollhouse";
  #xray = false;
  #cutaway = false;
  #selected = null;
  #lit = null; // the selected space's highlight
  #room = null; // the space the walker is in
  #timer = new THREE.Timer();
  #flight = null;

  constructor(container, options = {}) {
    super();
    this.#o = { ...DEFAULTS, ...options };
    const element = typeof container === "string" ? document.querySelector(container) : container;
    if (!element) throw new Error(`StoreyPathWorld: container ${container} not found`);
    this.#element = element;
    element.classList.add("storeypath-world");
    if (getComputedStyle(element).position === "static") element.style.position = "relative";

    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.95;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    element.appendChild(renderer.domElement);
    this.#renderer = renderer;

    this.#labelRenderer = new CSS2DRenderer();
    Object.assign(this.#labelRenderer.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
    element.appendChild(this.#labelRenderer.domElement);

    const scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environmentIntensity = 0.4;
    scene.add(new THREE.HemisphereLight(0xf4f7fb, 0x8b8374, 0.6));
    const sun = new THREE.DirectionalLight(0xfff3e3, 3.0);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.03;
    sun.shadow.radius = 3;
    scene.add(sun, sun.target);
    this.#sun = sun;
    // A warm light that goes with the walker, as if each room were lit.
    this.#lamp = new THREE.PointLight(0xfff0dc, 0, 14, 1.4);
    scene.add(this.#lamp);
    this.#scene = scene;
    this.#materials = new Materials(renderer);
    scene.background = this.#materials.sky();

    const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 3000);
    camera.position.set(30, 30, 30);
    this.#camera = camera;
    const orbit = new OrbitControls(camera, renderer.domElement);
    orbit.enableDamping = true;
    orbit.maxPolarAngle = Math.PI * 0.49;
    orbit.screenSpacePanning = true;
    this.#orbit = orbit;

    this.#walker = new Walker(camera, renderer.domElement);
    this.#walker.addEventListener("lock", () => this.#emit("walklock", { locked: true }));
    this.#walker.addEventListener("unlock", () => this.#emit("walklock", { locked: false }));

    this.#pointerPicking();
    new ResizeObserver(() => this.#resize()).observe(element);
    this.#resize();
    this.#timer.connect(document);
    renderer.setAnimationLoop(() => this.#frame());
  }

  // ---- public API ---------------------------------------------------------------

  get package() { return this.#pkg; }
  get building() { return this.#building; }
  get floor() { return this.#floor; }
  get mode() { return this.#mode; }
  get selected() { return this.#selected; }
  get room() { return this.#room; }
  get walking() { return this.#walker.locked; }
  /** The floors shown from the package's pre-built 3D rather than built here. */
  get prebuilt() { return [...this.#floors.values()].filter((f) => f.prebuilt).map((f) => f.id); }
  /** The three.js camera, renderer and scene, for anything else. */
  get camera() { return this.#camera; }
  get renderer() { return this.#renderer; }
  get scene() { return this.#scene; }

  /** Open a package from a URL, Blob, File or ArrayBuffer. */
  async open(source) {
    const pkg = await loadPackage(source);
    this.#pkg = pkg;
    this.#baked = new Map();
    await this.setBuilding(pkg.buildings[0]?.id);
    if (this.#pkg === pkg) this.#emit("load", { package: pkg }); // not when another was opened meanwhile
    return pkg;
  }

  /** Build and show a building. Resolves once it is shown: at once, unless its
   * floors come pre-built and are still to be read. */
  setBuilding(id) {
    const pkg = this.#pkg;
    if (!pkg || !id) return Promise.resolve();
    const asked = ++this.#asked;
    if (!pkg.floorsOf(id).some((f) => !this.#baked.has(f.id) && pkg.hasWorld(f.id))) {
      this.#show(id);
      return Promise.resolve();
    }
    return this.#readBaked(pkg, id).then(() => {
      if (this.#pkg === pkg && this.#asked === asked) this.#show(id);
    });
  }

  #show(id) {
    if (this.#buildingGroup) {
      this.#scene.remove(this.#buildingGroup);
      this.#dispose(this.#buildingGroup);
    }
    this.#building = id;
    this.#floors.clear();
    this.#floor = null;
    this.#selected = null;
    this.#lit = null;
    this.#buildingGroup = this.#buildBuilding(id);
    this.#scene.add(this.#buildingGroup);
    this.#applyVisibility();
    this.#frameBuilding(false);
    this.#emit("buildingchange", { id });
  }

  /** Show one floor (or all, with null). In the walk view, go to that floor. */
  setFloor(id) {
    if (id && !this.#floors.has(id)) return;
    this.#floor = id;
    if (this.#mode === "walk" && id) {
      const p = this.#camera.position;
      this.#walkTo(id, p.x, p.z);
    }
    this.#applyVisibility();
    this.#emit("floorchange", { id });
  }

  /** "dollhouse": orbit around the building; "walk": walk through it in the first person. */
  setMode(mode) {
    if (mode === this.#mode) return;
    this.#mode = mode;
    if (mode === "walk") {
      const floorId = this.#floor || this.#floorList()[0]?.id;
      const start = this.#startPoint(floorId);
      this.#orbit.enabled = false;
      this.#walker.enabled = true;
      const dir = this.#camera.getWorldDirection(new THREE.Vector3());
      this.#walkTo(floorId, start.x, start.z, start.heading ?? Math.atan2(-dir.x, -dir.z));
    } else {
      this.#walker.enabled = false;
      this.#walker.unlock();
      this.#orbit.enabled = true;
      this.#room = null;
      this.#frameBuilding(true);
    }
    this.#applyVisibility();
    this.#emit("modechange", { mode });
  }

  /** In the walk view: take the mouse to look around (call from a click). */
  startWalking() {
    if (this.#mode !== "walk") this.setMode("walk");
    this.#walker.lock();
  }

  /** See-through walls and rooms coloured by type. */
  setXray(on) {
    this.#xray = Boolean(on);
    this.#applyVisibility();
  }

  /** Walls cut low, as on a plan, in the dollhouse view. */
  setCutaway(on) {
    this.#cutaway = Boolean(on);
    this.#applyVisibility();
  }

  setLabels(on) {
    this.#o.labels = Boolean(on);
    this.#applyVisibility();
  }

  setShowHidden(on) {
    this.#o.showHidden = Boolean(on);
    this.#applyVisibility();
  }

  /** Space between floors in the dollhouse view, in meters. */
  setExplode(meters) {
    this.#o.explode = Math.max(0, Number(meters) || 0);
    this.#applyVisibility();
  }

  /** Furniture and equipment: shown (true), not drawn at all (false), or (null, the
   * default) shown when one floor is: on the floor shown on its own, on the walker's
   * floor, in a building of one floor. */
  setItems(on) {
    this.#o.items = on === null || on === undefined ? null : Boolean(on);
    this.#applyVisibility();
  }

  /** Whether furniture and equipment are drawn now. */
  get items() {
    return this.#o.items ?? this.#shownFloors() <= 1;
  }

  /** Select a space, a zone or an item (highlight it); in the walk view, go there. */
  select(id, { go = true } = {}) {
    const found = id ? this.#findSpace(id) ?? this.#findItem(id) : null;
    this.#highlight(found);
    this.#selected = found ? id : null;
    if (found && go) {
      const { x, z } = found.space?.centre ?? found.at;
      if (this.#mode === "walk") this.#walkTo(found.floor.id, x, z);
      else {
        if (this.#floor && this.#floor !== found.floor.id) this.setFloor(found.floor.id);
        this.#flyTo({ x, z }, found.floor.elevation + this.#offset(found.floor), found.space?.size ?? 4);
      }
    }
    this.#emit("select", { id: this.#selected, feature: found ? this.#pkg.get(id) : null });
  }

  /** Go up (+1) or down (−1) a floor, from where the walker stands. */
  changeFloor(step) {
    const list = this.#floorList();
    const current = this.#walkFloor ? list.findIndex((f) => f.id === this.#walkFloor) : -1;
    const next = list[current + step];
    if (!next) return false;
    const p = this.#camera.position;
    this.#walkTo(next.id, p.x, p.z);
    this.#floor = next.id;
    this.#applyVisibility();
    this.#emit("floorchange", { id: next.id });
    return true;
  }

  /** Whether the walker stands where floors can be changed: stairs or a lift, or
   * anywhere in a building whose drawings show neither. */
  get atStairs() {
    const f = this.#floors.get(this.#walkFloor);
    const here = f && this.#spaceAt(f, this.#camera.position.x, this.#camera.position.z);
    if (here && VERTICAL.has(here.type)) return true;
    return ![...this.#floors.values()].some((f) => f.spaces.some((s) => VERTICAL.has(s.type)));
  }

  /** The floor the walker is on. */
  get walkFloor() { return this.#walkFloor; }

  /** Longitude/latitude → the world's local meters: x east, z south. */
  toLocal([lon, lat]) {
    const [x, n] = localOf(this.#origin, [lon, lat]);
    return { x, z: -n };
  }

  /** A floor's plan in local meters (x east, z south), for a minimap: its walls,
   * spaces, items (their footprints) and what the walker bumps into besides them
   * ([x1, z1, x2, z2] each). */
  plan(floorId) {
    const f = this.#floors.get(floorId);
    if (!f) return null;
    const xz = (ring) => ring.map(([x, n]) => [x, -n]);
    return {
      walls: f.wallRings.map(xz),
      spaces: f.spaces.filter((s) => this.#o.showHidden || !s.tucked)
        .map((s) => ({ id: s.id, type: s.type, name: s.name, rings: s.rings.flat(1).map(xz) })),
      items: f.items.map((i) => ({ id: i.id, type: i.type, mount: i.mount, color: i.color,
        ring: [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => itemPoint(i, (a * i.width) / 2, (b * i.depth) / 2)) })),
      obstacles: f.obstacles.segments,
      bounds: f.bounds,
    };
  }

  /** Where the walker is: x, z (local meters), heading (radians) and floor. */
  get player() {
    const dir = new THREE.Vector3();
    this.#camera.getWorldDirection(dir);
    const h = Math.hypot(dir.x, dir.z) || 1;
    return { x: this.#camera.position.x, z: this.#camera.position.z, dx: dir.x / h, dz: dir.z / h,
      floor: this.#walkFloor };
  }

  destroy() {
    this.#renderer.setAnimationLoop(null);
    this.#walker.dispose();
    this.#orbit.dispose();
    if (this.#buildingGroup) this.#dispose(this.#buildingGroup);
    this.#renderer.dispose();
    this.#element.replaceChildren();
  }

  // ---- building the world ------------------------------------------------------

  #walkFloor = null;

  #floorList() {
    return this.#pkg ? this.#pkg.floorsOf(this.#building).filter((f) => this.#floors.has(f.id)) : [];
  }

  #buildBuilding(buildingId) {
    const pkg = this.#pkg;
    const floors = pkg.floorsOf(buildingId);
    this.#origin = originOf(pkg, buildingId);
    const group = new THREE.Group();
    group.name = buildingId;
    for (const floor of floors) {
      const built = this.#buildFloor(floor);
      this.#floors.set(floor.id, built);
      group.add(built.group);
    }
    const all = [...this.#floors.values()];
    const bounds = all.length ? all.reduce((b, f) => b.union(f.bounds3), new THREE.Box3()) : new THREE.Box3();
    this.#bounds = bounds;
    // The ground, a little wider than the building.
    const size = bounds.isEmpty() ? new THREE.Vector3(40, 0, 40) : bounds.getSize(new THREE.Vector3());
    const centre = bounds.isEmpty() ? new THREE.Vector3() : bounds.getCenter(new THREE.Vector3());
    const span = Math.max(size.x, size.z, 20);
    const fogFar = span * 10 + 150;
    const ground = new THREE.Mesh(new THREE.CircleGeometry(fogFar * 1.5, 64), this.#materials.ground());
    ground.rotation.x = -Math.PI / 2;
    ground.position.set(centre.x, (all[0]?.elevation ?? 0) - this.#o.slab - 0.02, centre.z);
    ground.receiveShadow = true;
    ground.material.map.repeat.set(1 / 6, 1 / 6);
    group.add(ground);
    this.#placeSun(centre, span);
    this.#scene.fog = new THREE.Fog(this.#o.fog, span * 4, fogFar);
    return group;
  }

  #bounds = new THREE.Box3();

  /** Read a building's pre-built floors (world/<floor-id>.glb), each kept when it
   * was built from this package, by this builder, in this frame, with these options;
   * otherwise (or unreadable) the floor is built here. */
  async #readBaked(pkg, buildingId) {
    const baked = this.#baked;
    const floors = pkg.floorsOf(buildingId).filter((f) => !baked.has(f.id) && pkg.hasWorld(f.id));
    const origin = originOf(pkg, buildingId);
    const o = this.#o;
    const matches = (x, floor) => x && x.floor_id === floor.id && x.project_id === pkg.project.id
      && x.export_sequence === pkg.manifest.export?.sequence && x.builder === BUILDER
      && Object.keys(GEOMETRY).every((k) => x.options?.[k] === o[k])
      && ["lon", "lat", "kx", "ky"].every((k) => Math.abs(x.origin?.[k] - origin[k]) <= 1e-9 * Math.max(1, Math.abs(origin[k])));
    try {
      const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
      const loader = new GLTFLoader();
      await Promise.all(floors.map(async (floor) => {
        let gltf = null;
        try {
          gltf = await loader.parseAsync(await pkg.world(floor.id), "");
        } catch (e) {
          console.warn(`StoreyPathWorld: ${floor.id}'s pre-built 3D could not be read, so it is built here: ${e.message}`);
        }
        baked.set(floor.id, gltf && matches(gltf.scene.userData.storeypath, floor) ? gltf : null);
      }));
    } catch (e) {
      console.warn(`StoreyPathWorld: no glTF loader, so floors are built here: ${e.message}`);
      for (const f of floors) baked.set(f.id, null);
    }
  }

  /** A pre-built floor's pieces, room IDs and obstacles, as buildPieces gives them;
   * its items' pieces by form, as buildItems gives them, and the IDs they index. */
  #bakedPieces(gltf) {
    const pieces = [], obstacles = [], items = { detailed: [], light: [] };
    gltf.scene.traverse((m) => {
      const d = m.userData;
      if (m.isLineSegments && d.name === "obstacles") {
        const p = m.geometry.getAttribute("position");
        for (let i = 0; i + 1 < p.count; i += 2) obstacles.push([p.getX(i), p.getZ(i), p.getX(i + 1), p.getZ(i + 1)]);
        m.geometry.dispose();
      } else if (m.isMesh && d.material) {
        const piece = { name: d.name ?? m.name, material: d.material, view: d.view, type: d.type, hidden: d.hidden,
          geometry: m.geometry };
        if (d.material === "item") items[d.form === "light" ? "light" : "detailed"].push({ ...piece, form: d.form });
        else pieces.push(piece);
      }
      if (m.material) m.material.dispose(); // the file's own: the world's are used
    });
    const x = gltf.scene.userData.storeypath;
    return { pieces, rooms: x.rooms, obstacles, items: { ids: x.items ?? [], ...items } };
  }

  /** The material a piece is drawn with. */
  #material({ material, type }) {
    const m = this.#materials;
    if (material === "floor") return m.floor(type);
    if (material === "volume") return m.volume(TYPE_COLORS[type] || TYPE_COLORS.unspecified);
    return m[material] instanceof THREE.Material ? m[material] : m.wallPlain;
  }

  #buildFloor(floor) {
    const o = this.#o;
    const plan = planFloor(this.#pkg, floor, this.#origin, o);
    const baked = this.#baked.get(floor.id);
    const { pieces, rooms, obstacles, items } = baked ? this.#bakedPieces(baked) : buildPieces(plan, o);
    if (baked) this.#baked.delete(floor.id); // its geometry is the floor's now: read again when built again
    const e = plan.elevation;
    pieces.push(...cutPieces(pieces, e, o));
    const group = new THREE.Group();
    group.name = floor.id;
    const built = { id: floor.id, group, elevation: e, height: plan.height, ordinal: plan.ordinal, prebuilt: Boolean(baked),
      spaces: [], pieces: [], rooms, wallRings: plan.wallRings, bounds: plan.bounds, bounds3: new THREE.Box3(),
      // the furniture and equipment: as planned, the IDs their geometry indexes, the
      // pre-built pieces of each form, and each form's meshes once shown
      plan, items: plan.items, itemIds: items?.ids ?? plan.items.map((i) => i.id), bakedItems: items ?? null,
      itemMeshes: {}, itemObstacles: null };

    // slab, walls, floors, doors and windows: one mesh a piece
    for (const p of pieces) {
      const mesh = this.#mesh(p);
      mesh.visible = p.view !== "walk" && p.view !== "xray";
      group.add(mesh);
      built.pieces.push(mesh);
    }

    // the label of each space and zone in use
    for (const u of plan.units) {
      const label = this.#label(u);
      label.position.set(u.label[0], e + 0.25, -u.label[1]);
      if (label.element.textContent) group.add(label);
      built.spaces.push({ id: u.id, type: u.type, name: u.name, number: u.number, tucked: u.tucked, rings: u.rings,
        label, centre: u.centre, size: u.size });
    }
    built.obstacles = new Obstacles(obstacles);

    if (Number.isFinite(plan.bounds[0])) {
      built.bounds3.set(new THREE.Vector3(plan.bounds[0], e - o.slab, plan.bounds[1]),
        new THREE.Vector3(plan.bounds[2], e + plan.wallHeight, plan.bounds[3]));
    }
    return built;
  }

  /** A piece as a mesh, drawn with the world's material for it. */
  #mesh(p) {
    const mesh = new THREE.Mesh(p.geometry, this.#material(p));
    mesh.name = p.name;
    mesh.userData = { material: p.material, view: p.view, hidden: Boolean(p.hidden), form: p.form };
    mesh.castShadow = CASTS.has(p.material);
    mesh.receiveShadow = TAKES.has(p.material);
    mesh.renderOrder = ORDER[p.material] ?? 0;
    return mesh;
  }

  /** A floor's items in a form ("detailed" or "light"), made the first time they are
   * shown: from the pre-built file, or here. */
  #itemsIn(f, form) {
    if (!f.itemMeshes[form]) {
      const pieces = f.bakedItems?.[form]?.length ? f.bakedItems[form] : buildItems(f.plan, form, this.#o);
      f.itemMeshes[form] = pieces.map((p) => {
        const mesh = this.#mesh(p);
        f.group.add(mesh);
        return mesh;
      });
    }
    return f.itemMeshes[form];
  }

  /** What the walker bumps into on a floor: its walls, and its items when they are shown. */
  #obstaclesOf(f, items) {
    if (!items || !f.items.length) return f.obstacles;
    f.itemObstacles ??= new Obstacles([...f.obstacles.segments, ...f.plan.itemObstacles]);
    return f.itemObstacles;
  }

  /** Highlight a space (``found``: its floor and space) or an item (its floor and
   * item), or none. */
  #highlight(found) {
    if (this.#lit) {
      this.#lit.removeFromParent();
      this.#lit.geometry.dispose();
      this.#lit = null;
    }
    if (!found) return;
    const geometry = found.item ? itemBox(found.floor.plan, found.item) : flat(found.space.rings, found.floor.elevation + 0.02);
    const lit = new THREE.Mesh(geometry, this.#materials.highlight);
    lit.name = "highlight";
    lit.renderOrder = 3;
    lit.userData.space = found.space ?? null;
    lit.visible = this.#o.showHidden || !found.space?.tucked;
    found.floor.group.add(lit);
    this.#lit = lit;
  }

  #label(p) {
    // The renderer sets the outer element's display to show and hide it, so the
    // layout lives on an inner one.
    const outer = document.createElement("div");
    const el = document.createElement("div");
    el.className = "sp3d-label"; // styled here; pages may restyle the class
    Object.assign(el.style, LABEL_STYLE);
    const name = p.name || "";
    const number = p.number && p.number !== p.name ? p.number : "";
    if (name) el.append(Object.assign(document.createElement("strong"), { textContent: name }));
    if (number) {
      const n = Object.assign(document.createElement("span"), { textContent: number });
      Object.assign(n.style, { fontWeight: "500", fontSize: "10px", color: "#5f6570" });
      el.append(n);
    }
    outer.append(el);
    return new CSS2DObject(outer);
  }

  // ---- what is shown -------------------------------------------------------------

  #offset(floor) {
    return this.#mode === "dollhouse" && !this.#floor ? floor.ordinal * this.#o.explode : 0;
  }

  /** How many floors are shown: walking, the walker's (the floors under it are seen
   * only through openings); otherwise one, or all. */
  #shownFloors() {
    return this.#mode === "walk" || this.#floor ? 1 : this.#floors.size;
  }

  #applyVisibility() {
    const walking = this.#mode === "walk";
    const wf = walking ? this.#floors.get(this.#walkFloor) : null;
    // furniture and equipment: detailed on one floor, a box each on more
    const one = this.#shownFloors() <= 1;
    const items = this.#o.items ?? one;
    const form = one ? "detailed" : "light";
    for (const f of this.#floors.values()) {
      // Walking: the floors up to yours, open to the sky. Dollhouse: one or all.
      const shown = walking ? Boolean(wf) && f.ordinal <= wf.ordinal : !this.#floor || f.id === this.#floor;
      f.group.visible = shown;
      f.group.position.y = this.#offset(f);
      const cut = !walking && this.#cutaway;
      const seen = ({ view, hidden }) => (this.#o.showHidden || !hidden) && (view === "full" ? !cut : view === "cut" ? cut
        : view === "walk" ? walking && f === wf : view === "xray" ? this.#xray : true);
      for (const m of f.pieces) m.visible = seen(m.userData);
      for (const s of f.spaces) {
        const visible = this.#o.showHidden || !s.tucked;
        s.label.visible = visible && this.#o.labels && !walking && (!this.#floor ? f === this.#topShown() : true);
      }
      const furnished = items && shown && (!walking || f === wf) && f.items.length > 0;
      if (furnished) this.#itemsIn(f, form);
      for (const [name, meshes] of Object.entries(f.itemMeshes)) {
        for (const m of meshes) m.visible = furnished && name === form && seen(m.userData);
      }
      if (f === wf) this.#walker.obstacles = this.#obstaclesOf(f, furnished);
    }
    if (this.#lit) this.#lit.visible = this.#o.showHidden || !this.#lit.userData.space?.tucked;
    const see = this.#xray ? 0.22 : 1;
    const m8 = this.#materials;
    for (const m of [m8.wall, m8.wallPlain, m8.wallTop, m8.wallCut, m8.frame, m8.doorFrame, m8.door]) {
      m.transparent = this.#xray;
      m.opacity = see;
      m.depthWrite = !this.#xray;
    }
  }

  /** In the dollhouse view of all floors, labels show for the top floor only. */
  #topShown() {
    const list = [...this.#floors.values()];
    return list.length ? list.reduce((a, b) => (b.ordinal > a.ordinal ? b : a)) : null;
  }

  // ---- walking -----------------------------------------------------------------------

  /** Where walking starts: outside the front door, looking in; else in the main room. */
  #startPoint(floorId) {
    const f = this.#floors.get(floorId);
    if (!f) return { x: 0, z: 0 };
    const door = this.#frontDoor(f);
    if (door) return door;
    const prefer = ["lobby", "corridor", "living_room", "open_area", "room"];
    const pick = [...f.spaces].sort((a, b) => {
      const ia = prefer.indexOf(a.type), ib = prefer.indexOf(b.type);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || b.size - a.size;
    })[0];
    return pick ? { x: pick.centre.x, z: pick.centre.z } : { x: (f.bounds[0] + f.bounds[2]) / 2, z: (f.bounds[1] + f.bounds[3]) / 2 };
  }

  #frontDoor(f) {
    const prefer = ["lobby", "corridor", "living_room", "open_area"];
    const doors = [];
    for (const opening of this.#pkg.openings) {
      const op = opening.properties;
      if (op.floor_id !== f.id || op.type !== "door" || op.connects.length !== 1) continue;
      const space = f.spaces.find((s) => s.id === op.connects[0]);
      let mx, mn, nx, nn, len;
      if (op.span) {
        const [a, b] = op.span.map((c) => localOf(this.#origin, c));
        mx = (a[0] + b[0]) / 2; mn = (a[1] + b[1]) / 2;
        len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
        nx = -(b[1] - a[1]) / len; nn = (b[0] - a[0]) / len;
      } else if (opening.geometry?.type === "Point" && space) {
        // no span (older packages): out is away from the middle of the room
        [mx, mn] = localOf(this.#origin, opening.geometry.coordinates);
        const dx = mx - space.centre.x, dn = mn + space.centre.z;
        const d = Math.hypot(dx, dn) || 1;
        nx = dx / d; nn = dn / d;
        len = op.width_m || 0.9;
      } else continue;
      // outside is the side of the door that no room covers
      const out = [1, -1].find((k) => !this.#spaceAt(f, mx + k * nx * 1.2, -(mn + k * nn * 1.2), true));
      if (out === undefined) continue;
      const rank = prefer.indexOf(space?.type);
      doors.push({ x: mx + out * nx * 2.2, z: -(mn + out * nn * 2.2), heading: Math.atan2(out * nx, -out * nn),
        rank: rank < 0 ? 99 : rank, len });
    }
    doors.sort((p, q) => p.rank - q.rank || q.len - p.len);
    return doors[0] ?? null;
  }

  #walkTo(floorId, x, z, heading) {
    const f = this.#floors.get(floorId);
    if (!f) return;
    this.#walkFloor = floorId;
    this.#walker.place(x, z, f.elevation, f.obstacles, heading);
    this.#room = null;
    this.#applyVisibility();
  }

  #spaceAt(f, x, z, all = false) {
    const n = -z;
    for (const s of f.spaces) {
      if (!all && !this.#o.showHidden && s.tucked) continue;
      for (const rings of s.rings) {
        if (inside(rings[0], x, n) && !rings.slice(1).some((h) => inside(h, x, n))) return s;
      }
    }
    return null;
  }

  #findSpace(id) {
    for (const floor of this.#floors.values()) {
      const space = floor.spaces.find((s) => s.id === id);
      if (space) return { floor, space };
    }
    return null;
  }

  /** An item: its floor, its plan, and where to go to it (before its front, for one
   * on the floor). */
  #findItem(id) {
    for (const floor of this.#floors.values()) {
      const item = floor.items.find((i) => i.id === id);
      if (!item) continue;
      const ahead = item.mount === "floor" ? item.depth / 2 + 0.9 : 0;
      return { floor, item, at: { x: item.x + item.fx * ahead, z: -(item.n + item.fn * ahead) } };
    }
    return null;
  }

  // ---- camera ------------------------------------------------------------------------

  #frameBuilding(animate) {
    const b = this.#bounds;
    if (b.isEmpty()) return;
    const centre = b.getCenter(new THREE.Vector3());
    const size = b.getSize(new THREE.Vector3());
    const radius = Math.max(size.x, size.z, size.y * 2) * 0.6 + 4;
    const to = new THREE.Vector3(centre.x + radius * 0.8, centre.y + radius * 0.75, centre.z + radius * 0.9);
    if (animate) this.#flight = { from: this.#camera.position.clone(), to, fromT: this.#orbit.target.clone(), toT: centre, t: 0 };
    else {
      this.#flight = null; // one under way would take the camera on elsewhere
      this.#camera.position.copy(to);
      this.#orbit.target.copy(centre);
      this.#orbit.update();
    }
  }

  #flyTo(centre, y, size) {
    const target = new THREE.Vector3(centre.x, y, centre.z);
    // from high up, so the room's walls do not hide it
    const dist = Math.max(12, size * 2.6);
    const dir = this.#camera.position.clone().sub(this.#orbit.target);
    dir.y = 0;
    if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1);
    dir.normalize().multiplyScalar(0.45);
    dir.y = 1;
    dir.normalize();
    this.#flight = { from: this.#camera.position.clone(), to: target.clone().addScaledVector(dir, dist),
      fromT: this.#orbit.target.clone(), toT: target, t: 0 };
  }

  #placeSun(centre, size) {
    const sun = this.#sun;
    sun.position.set(centre.x - size * 0.6, centre.y + size * 1.4, centre.z + size * 0.45);
    sun.target.position.copy(centre);
    const c = sun.shadow.camera;
    const r = size * 0.85 + 5;
    Object.assign(c, { left: -r, right: r, top: r, bottom: -r, near: 0.5, far: size * 4 + 50 });
    c.updateProjectionMatrix();
  }

  // ---- loop and input ----------------------------------------------------------------

  #frame() {
    this.#timer.update();
    const dt = this.#timer.getDelta();
    if (this.#flight) {
      const f = this.#flight;
      f.t = Math.min(1, f.t + dt / 0.9);
      const k = f.t < 0.5 ? 2 * f.t * f.t : 1 - (-2 * f.t + 2) ** 2 / 2;
      this.#camera.position.lerpVectors(f.from, f.to, k);
      this.#orbit.target.lerpVectors(f.fromT, f.toT, k);
      if (f.t >= 1) this.#flight = null;
    }
    this.#lamp.intensity = this.#mode === "walk" ? 9 : 0;
    if (this.#mode === "walk") {
      this.#walker.update(dt);
      this.#lamp.position.copy(this.#camera.position).y += 0.9;
      const f = this.#floors.get(this.#walkFloor);
      if (f) {
        const here = this.#spaceAt(f, this.#camera.position.x, this.#camera.position.z);
        if (here !== this.#room) {
          this.#room = here;
          this.#emit("roomchange", { id: here?.id ?? null, type: here?.type ?? null, name: here?.name ?? null,
            number: here?.number ?? null, stairs: this.atStairs });
        }
      }
    } else {
      this.#orbit.update();
    }
    this.#renderer.render(this.#scene, this.#camera);
    this.#labelRenderer.render(this.#scene, this.#camera);
  }

  #resize() {
    const w = this.#element.clientWidth || 1, h = this.#element.clientHeight || 1;
    this.#renderer.setSize(w, h, false);
    this.#renderer.domElement.style.width = "100%";
    this.#renderer.domElement.style.height = "100%";
    this.#labelRenderer.setSize(w, h);
    this.#camera.aspect = w / h;
    this.#camera.updateProjectionMatrix();
  }

  #pointerPicking() {
    const canvas = this.#renderer.domElement;
    const ray = new THREE.Raycaster();
    let down = null;
    canvas.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
    canvas.addEventListener("pointerup", (e) => {
      if (this.#mode !== "dollhouse" || !down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
      const r = canvas.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.#camera);
      // the floor finishes and the items shown, merged: the room or item is the one
      // the triangle hit is of
      const targets = [];
      for (const f of this.#floors.values()) {
        if (!f.group.visible) continue;
        for (const m of f.pieces) if (m.visible && m.userData.material === "floor") targets.push(m);
        for (const meshes of Object.values(f.itemMeshes)) for (const m of meshes) if (m.visible) targets.push(m);
      }
      const hit = ray.intersectObjects(targets, false)[0];
      const floor = hit && [...this.#floors.values()].find((f) => f.group === hit.object.parent);
      const g = hit?.object.geometry;
      const id = !floor ? null : hit.object.userData.material === "item" ? floor.itemIds[g.getAttribute("_item").getX(hit.face.a)]
        : floor.rooms[g.getAttribute("_room")?.getX(hit.face.a)];
      this.select(id ?? null, { go: false });
    });
  }

  #emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  #dispose(group) {
    group.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.isCSS2DObject) o.element.remove();
    });
  }
}
