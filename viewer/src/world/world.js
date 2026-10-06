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
// textures or models are downloaded.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

import { loadPackage } from "../package.js";
import { TYPE_COLORS } from "../theme.js";
import { Materials } from "./materials.js";
import { Obstacles, Walker } from "./walk.js";

const DEFAULTS = {
  slab: 0.22, // m, floor slab thickness
  doorHead: 2.1, // m above the floor
  windowSill: 0.9,
  windowHead: 2.2,
  wallThickness: 0.2, // when the package does not say
  cutHeight: 1.25, // walls are cut to this in the cutaway view
  explode: 0, // m between floors in the dollhouse view
  labels: true,
  showHidden: false,
  fog: 0xeef1f2,
};
const LABEL_STYLE = {
  display: "flex", flexDirection: "column", alignItems: "center", padding: "2px 7px", borderRadius: "7px",
  background: "rgba(255, 255, 255, 0.86)", color: "#16181d", whiteSpace: "nowrap",
  font: '600 11px/1.25 system-ui, -apple-system, "Segoe UI", sans-serif', boxShadow: "0 1px 4px rgba(0, 0, 0, 0.18)",
};
const VERTICAL = new Set(["stairs", "elevator", "escalator", "ramp"]);
const OPEN_SPAN = 2.6; // m: wider ways through are open-plan joins, with no wall above
const LEAF = 0.045; // m, a door leaf's thickness
const CASING = 0.06; // m, the frame round a door
const WINDOW_FRAME = 0.05; // m, the frame round the glass
const PANE = 1.0; // m: a mullion about this often across a window
const DOUBLE_DOOR = 1.3; // m: a door wider than this, drawn without its swings, has two leaves
const OUTDOOR = new Set(["terrace", "balcony"]); // open to the sky, behind parapets
const PARAPET = 1.1; // m, when the package gives no parapet height

/** Distance from point ``p`` to the segment ``a``–``b`` (plan meters). */
function distanceToSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

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
  #floor = null; // the floor shown on its own, or null for all
  #mode = "dollhouse";
  #xray = false;
  #cutaway = false;
  #selected = null;
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
  /** The three.js camera, renderer and scene, for anything else. */
  get camera() { return this.#camera; }
  get renderer() { return this.#renderer; }
  get scene() { return this.#scene; }

  /** Open a package from a URL, Blob, File or ArrayBuffer. */
  async open(source) {
    this.#pkg = await loadPackage(source);
    this.setBuilding(this.#pkg.buildings[0]?.id);
    this.#emit("load", { package: this.#pkg });
    return this.#pkg;
  }

  /** Build and show a building. */
  setBuilding(id) {
    if (!this.#pkg || !id) return;
    if (this.#buildingGroup) {
      this.#scene.remove(this.#buildingGroup);
      this.#dispose(this.#buildingGroup);
    }
    this.#building = id;
    this.#floors.clear();
    this.#floor = null;
    this.#selected = null;
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

  /** Select a space (highlight it); in the walk view, go there. */
  select(id, { go = true } = {}) {
    const found = id ? this.#findSpace(id) : null;
    for (const f of this.#floors.values()) for (const s of f.spaces) s.highlight.visible = s.id === id;
    this.#selected = found ? id : null;
    if (found && go) {
      if (this.#mode === "walk") this.#walkTo(found.floor.id, found.space.centre.x, found.space.centre.z);
      else {
        if (this.#floor && this.#floor !== found.floor.id) this.setFloor(found.floor.id);
        this.#flyTo(found.space.centre, found.floor.elevation + this.#offset(found.floor), found.space.size);
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
    const [x, n] = this.#local([lon, lat]);
    return { x, z: -n };
  }

  /** A floor's plan in local meters (x east, z south), for a minimap. */
  plan(floorId) {
    const f = this.#floors.get(floorId);
    if (!f) return null;
    const xz = (ring) => ring.map(([x, n]) => [x, -n]);
    return {
      walls: f.wallRings.map(xz),
      spaces: f.spaces.filter((s) => this.#o.showHidden || !s.tucked)
        .map((s) => ({ id: s.id, type: s.type, name: s.name, rings: s.rings.flat(1).map(xz) })),
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
    this.#origin = this.#originOf(floors.length ? floors : [pkg.get(buildingId)]);
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

  #originOf(features) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const visit = (c) => {
      if (typeof c[0] === "number") {
        x0 = Math.min(x0, c[0]); y0 = Math.min(y0, c[1]); x1 = Math.max(x1, c[0]); y1 = Math.max(y1, c[1]);
      } else c.forEach(visit);
    };
    for (const f of features) if (f?.geometry) visit(f.geometry.coordinates);
    const lon = Number.isFinite(x0) ? (x0 + x1) / 2 : 0;
    const lat = Number.isFinite(y0) ? (y0 + y1) / 2 : 0;
    return { lon, lat, kx: 111320 * Math.cos((lat * Math.PI) / 180), ky: 110540 };
  }

  /** Longitude/latitude → local meters: x east, n north. */
  #local([lon, lat]) {
    const o = this.#origin;
    return [(lon - o.lon) * o.kx, (lat - o.lat) * o.ky];
  }

  /** A GeoJSON (Multi)Polygon → polygons as rings of [x, n] in local meters. */
  #polygons(geometry) {
    if (!geometry) return [];
    const polys = geometry.type === "Polygon" ? [geometry.coordinates]
      : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
    return polys.map((rings) => rings.map((ring) => ring.map((c) => this.#local(c))));
  }

  /** A polygon's rings as a shape, or null. A ring too small to have an inside (a
   * hole a few millimetres wide, collapsed by the package's 1 cm rounding) is
   * left out: the triangulator fails on it, and takes the whole floor with it. */
  #shape(rings) {
    const toPts = (ring) => ring.map(([x, n]) => new THREE.Vector2(x, n));
    const usable = (pts) => pts.length >= 3 && Math.abs(THREE.ShapeUtils.area(pts)) > 1e-4;
    const outer = toPts(rings[0]);
    if (!usable(outer)) return null;
    const shape = new THREE.Shape(outer);
    for (const hole of rings.slice(1)) {
      const pts = toPts(hole);
      if (usable(pts)) shape.holes.push(new THREE.Path(pts));
    }
    return shape;
  }

  /** Shapes for ``polygons``, built with ``make``; when that fails on the whole set,
   * with only the shapes it works on, one bad outline does not hide the rest. */
  #geometry(polygons, make) {
    const shapes = polygons.map((rings) => this.#shape(rings)).filter(Boolean);
    if (!shapes.length) return new THREE.BufferGeometry();
    try {
      return make(shapes);
    } catch {
      const good = shapes.filter((shape) => {
        try {
          make([shape]).dispose();
          return true;
        } catch {
          return false;
        }
      });
      return good.length ? make(good) : new THREE.BufferGeometry();
    }
  }

  /** Polygons extruded upward from ``y`` by ``depth``: material 0 on the top and
   * bottom faces, material 1 on the sides. */
  #extrude(polygons, depth, y) {
    if (!polygons.length) return null;
    const g = this.#geometry(polygons,
      (shapes) => new THREE.ExtrudeGeometry(shapes, { depth, bevelEnabled: false, curveSegments: 1 }));
    g.rotateX(-Math.PI / 2);
    g.translate(0, y, 0);
    return g;
  }

  #flat(polygons, y) {
    const g = this.#geometry(polygons, (shapes) => new THREE.ShapeGeometry(shapes, 1));
    g.rotateX(-Math.PI / 2);
    g.translate(0, y, 0);
    return g;
  }

  #buildFloor(floor) {
    const o = this.#o;
    const props = floor.properties;
    const e = props.elevation;
    const wallHeight = Math.max(2.4, props.height - o.slab);
    let thickness = props.wall_thickness_m || o.wallThickness;
    const group = new THREE.Group();
    group.name = floor.id;
    const built = { id: floor.id, group, elevation: e, height: props.height, ordinal: props.ordinal,
      spaces: [], wallRings: [], bounds: [Infinity, Infinity, -Infinity, -Infinity], bounds3: new THREE.Box3() };

    // the slab
    const outline = this.#polygons(floor.geometry);
    const slab = this.#extrude(outline, o.slab, e - o.slab);
    if (slab) {
      const mesh = new THREE.Mesh(slab, this.#materials.slab);
      mesh.receiveShadow = mesh.castShadow = true;
      group.add(mesh);
    }

    // the rooms: floor finish, x-ray volume, highlight, label
    const parapetHeight = Math.min(props.parapet_height_m || PARAPET, wallHeight);
    const roofed = []; // under the ceiling: every room but terraces and balconies
    // One volume per space (walls stand on its edges); a space divided into zones is
    // used through them: each has its own floor, highlight and label, and no wall.
    built.rooms = [];
    for (const space of this.#pkg.spacesOn(floor.id)) {
      const sp = space.properties;
      const spacePolys = this.#polygons(space.geometry);
      if (!spacePolys.length) continue;
      built.rooms.push({ id: space.id, rings: spacePolys });
      const outdoor = OUTDOOR.has(sp.type);
      if (!outdoor) roofed.push(...spacePolys);
      const volume = new THREE.Mesh(this.#extrude(spacePolys, (outdoor ? parapetHeight : wallHeight) - 0.05, e + 0.01),
        this.#materials.volume(TYPE_COLORS[sp.type] || TYPE_COLORS.unspecified));
      volume.userData.spaceId = space.id;
      volume.renderOrder = 2;
      group.add(volume);
      const zones = this.#pkg.zonesOf(space.id);
      for (const unit of zones.length ? zones : [space]) {
        const p = unit.properties;
        const polys = unit === space ? spacePolys : this.#polygons(unit.geometry);
        if (!polys.length) continue;
        const finish = p.type === "open_to_below" ? null : new THREE.Mesh(this.#flat(polys, e + 0.004),
          this.#materials.floor(p.type));
        if (finish) {
          finish.receiveShadow = true;
          finish.userData.spaceId = unit.id;
          group.add(finish);
        }
        const highlight = new THREE.Mesh(this.#flat(polys, e + 0.02), this.#materials.highlight);
        highlight.visible = false;
        highlight.renderOrder = 3;
        group.add(highlight);
        const [lx, ln] = p.display_point ? this.#local(p.display_point) : polys[0][0][0];
        const label = this.#label(p);
        label.position.set(lx, e + 0.25, -ln);
        if (label.element.textContent) group.add(label);
        let x0 = Infinity, n0 = Infinity, x1 = -Infinity, n1 = -Infinity;
        for (const [x, n] of polys.flat(2)) {
          x0 = Math.min(x0, x); n0 = Math.min(n0, n); x1 = Math.max(x1, x); n1 = Math.max(n1, n);
        }
        built.spaces.push({
          id: unit.id, type: p.type, name: p.name, number: p.number, tucked: p.hidden || p.ignored,
          rings: polys, finish, volume, highlight, label,
          centre: { x: lx, z: -ln }, size: Math.max(x1 - x0, n1 - n0, 2),
        });
      }
    }

    // the ceiling, seen only when walking (it casts no shadow: rooms stay sunlit); none
    // over terraces and balconies
    const ceilingPolys = built.spaces.length ? roofed : outline;
    const ceiling = ceilingPolys.length
      ? new THREE.Mesh(this.#flat(ceilingPolys, e + wallHeight), this.#materials.ceiling) : null;
    if (ceiling) {
      ceiling.visible = false;
      group.add(ceiling);
    }
    built.ceiling = ceiling;

    // the walls, full height and cut low; drawn along the rooms' edges when the
    // package has none (older packages, drawings without wall layers)
    let walls = this.#polygons(props.walls);
    let ways;
    if (walls.length) {
      ways = this.#pkg.openings
        .filter((x) => x.properties.floor_id === floor.id && x.properties.span)
        .map((x) => {
          const [a, b] = x.properties.span.map((c) => this.#local(c));
          const leaves = (x.properties.swings || []).map((leaf) => leaf.map((c) => this.#local(c)));
          return { a, b, type: x.properties.type, connects: x.properties.connects || [], leaves };
        });
    } else {
      thickness = props.wall_thickness_m || 0.12;
      const fallback = this.#roomWalls(floor.id, built.rooms, thickness); // spaces, not zones: no wall between zones
      walls = fallback.walls;
      ways = fallback.gaps;
    }
    const full = this.#extrude(walls, wallHeight, e);
    const cut = this.#extrude(walls, o.cutHeight, e);
    built.wallsFull = full && new THREE.Mesh(full, [this.#materials.wallTop, this.#materials.wall]);
    built.wallsCut = cut && new THREE.Mesh(cut, [this.#materials.wallCut, this.#materials.wall]);
    // the parapets: the low walls around terraces, balconies and the roof
    const parapets = this.#polygons(props.parapets);
    const parapetsFull = this.#extrude(parapets, parapetHeight, e);
    const parapetsCut = this.#extrude(parapets, Math.min(parapetHeight, o.cutHeight), e);
    built.parapetsFull = parapetsFull && new THREE.Mesh(parapetsFull, [this.#materials.wallTop, this.#materials.wall]);
    built.parapetsCut = parapetsCut && new THREE.Mesh(parapetsCut, [this.#materials.wallCut, this.#materials.wall]);
    for (const m of [built.wallsFull, built.wallsCut, built.parapetsFull, built.parapetsCut]) {
      if (!m) continue;
      m.castShadow = m.receiveShadow = true;
      group.add(m);
    }
    built.wallRings = walls.flat(1).concat(parapets.flat(1));
    // Openings in a parapet (no full wall at either side) get no head, sill or glass:
    // a full wall ends at one of its jambs (its middle is far from any wall when wide).
    const fullSegments = [];
    for (const ring of walls.flat(1)) {
      for (let i = 0; i + 1 < ring.length; i++) fullSegments.push([ring[i], ring[i + 1]]);
    }
    const inFullWall = ({ a, b }) => !parapets.length || [a, b].some((jamb) => fullSegments.some(([p, q]) =>
      distanceToSegment(jamb, p, q) <= thickness + 0.3));

    // door heads, frames and leaves; window sills, heads, frames and glass
    const heads = [], sills = [], glass = [], frames = [], casings = [], leaves = [];
    // a box from plan point p to q, y0 to y1 above the floor, depth across
    const piece = (p, q, y0, y1, depth) => {
      const g = new THREE.BoxGeometry(Math.hypot(q[0] - p[0], q[1] - p[1]), y1 - y0, depth);
      g.rotateY(Math.atan2(q[1] - p[1], q[0] - p[0]));
      g.translate((p[0] + q[0]) / 2, e + (y0 + y1) / 2, -(p[1] + q[1]) / 2);
      return g;
    };
    const segments = [];
    for (const ring of built.wallRings) {
      for (let i = 0; i + 1 < ring.length; i++) {
        segments.push([ring[i][0], -ring[i][1], ring[i + 1][0], -ring[i + 1][1]]);
      }
    }
    for (const way of ways) {
      const { a, b, type } = way;
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (len < 0.3 || !inFullWall(way)) continue;
      const box = (y0, y1, depth) => {
        const g = new THREE.BoxGeometry(len, y1 - y0, depth);
        g.rotateY(Math.atan2(b[1] - a[1], b[0] - a[0]));
        g.translate((a[0] + b[0]) / 2, e + (y0 + y1) / 2, -(a[1] + b[1]) / 2);
        return g;
      };
      const ux = (b[0] - a[0]) / len, un = (b[1] - a[1]) / len;
      const at = (t) => [a[0] + ux * t, a[1] + un * t];
      if (type === "window") {
        sills.push(box(0, o.windowSill, thickness));
        heads.push(box(o.windowHead, wallHeight, thickness));
        glass.push(box(o.windowSill, o.windowHead, 0.02));
        // the frame: along the sill and the head, at each side, and a mullion about
        // every metre between
        const f = WINDOW_FRAME, depth = Math.min(thickness, 0.09);
        frames.push(piece(a, b, o.windowSill, o.windowSill + f, depth), piece(a, b, o.windowHead - f, o.windowHead, depth));
        const panes = Math.max(1, Math.round(len / PANE));
        for (let k = 0; k <= panes; k++) {
          const t = Math.min(Math.max((k * len) / panes, f / 2), len - f / 2);
          frames.push(piece(at(t - f / 2), at(t + f / 2), o.windowSill, o.windowHead, depth));
        }
        segments.push([a[0], -a[1], b[0], -b[1]]); // you cannot walk through a window
      } else if (type === "door") {
        heads.push(box(o.doorHead, wallHeight, thickness));
        // the frame: a jamb each side and a head, standing a little proud of the wall
        const c = Math.min(CASING, len / 4), depth = thickness + 0.03;
        casings.push(piece(a, at(c), 0, o.doorHead, depth), piece(at(len - c), b, 0, o.doorHead, depth),
          piece(a, b, o.doorHead - c, o.doorHead, depth));
        // the leaves, open as the plan draws them; without the swings, open into the
        // room it serves, one leaf or two
        let open = way.leaves || [];
        if (!open.length) {
          const mid = at(len / 2), side = [-un, ux];
          const into = built.spaces.filter((s) => way.connects?.includes(s.id))
            .some((s) => s.rings.some((r) => inside(r[0], mid[0] + side[0] * 0.5, mid[1] + side[1] * 0.5)));
          const k = into ? 1 : -1;
          const hinges = len > DOUBLE_DOOR ? [[0, len / 2], [len, len / 2]] : [[0, len]];
          open = hinges.map(([t, w]) => {
            const h = at(t);
            return [h, [h[0] + side[0] * k * w, h[1] + side[1] * k * w]];
          });
        }
        for (const [h, q] of open) {
          const reach = Math.hypot(q[0] - h[0], q[1] - h[1]);
          if (reach < 0.3) continue;
          const w = Math.min(reach, len) - c, dx = (q[0] - h[0]) / reach, dn = (q[1] - h[1]) / reach;
          const from = [h[0] + dx * c, h[1] + dn * c], to = [h[0] + dx * (c + w), h[1] + dn * (c + w)];
          leaves.push(piece(from, to, 0.01, o.doorHead - c, LEAF));
          segments.push([from[0], -from[1], to[0], -to[1]]); // an open leaf stands in the way
        }
      } else if (len <= OPEN_SPAN) { // a doorway: a way through with no door
        heads.push(box(o.doorHead, wallHeight, thickness));
      }
    }
    const add = (geoms, material, name) => {
      if (!geoms.length) return null;
      const mesh = new THREE.Mesh(mergeGeometries(geoms, false), material);
      mesh.castShadow = mesh.receiveShadow = true;
      mesh.name = name;
      group.add(mesh);
      geoms.forEach((g) => g.dispose());
      return mesh;
    };
    built.heads = add(heads, this.#materials.wallPlain, "heads");
    built.sills = add(sills, this.#materials.wallPlain, "sills");
    built.glass = add(glass, this.#materials.glass, "glass");
    built.frames = add(frames, this.#materials.frame, "window frames");
    built.casings = add(casings, this.#materials.doorFrame, "door frames");
    built.leaves = add(leaves, this.#materials.door, "doors");
    if (built.glass) {
      built.glass.castShadow = false;
      built.glass.renderOrder = 4;
    }
    built.obstacles = new Obstacles(segments);

    for (const rings of [...outline, ...walls, ...built.spaces.flatMap((s) => s.rings)]) {
      for (const [x, n] of rings[0]) {
        built.bounds[0] = Math.min(built.bounds[0], x); built.bounds[1] = Math.min(built.bounds[1], -n);
        built.bounds[2] = Math.max(built.bounds[2], x); built.bounds[3] = Math.max(built.bounds[3], -n);
      }
    }
    if (Number.isFinite(built.bounds[0])) {
      built.bounds3.set(new THREE.Vector3(built.bounds[0], e - o.slab, built.bounds[1]),
        new THREE.Vector3(built.bounds[2], e + wallHeight, built.bounds[3]));
    }
    return built;
  }

  /** Thin walls along the edges of the rooms, open where the doors and windows are. */
  #roomWalls(floorId, spaces, thickness) {
    const openings = this.#pkg.openings
      .filter((o) => o.properties.floor_id === floorId && o.geometry?.type === "Point")
      .map((o) => ({ p: this.#local(o.geometry.coordinates), type: o.properties.type,
        w: Math.min(Math.max(o.properties.width_m || 0.9, 0.7), 2.4) }));
    const seen = new Set();
    const pieces = [], gaps = [];
    for (const s of spaces) {
      for (const ring of s.rings.flat(1)) {
        for (let i = 0; i + 1 < ring.length; i++) {
          const a = ring[i], b = ring[i + 1];
          const key = [a, b].map((q) => `${q[0].toFixed(2)},${q[1].toFixed(2)}`).sort().join("|");
          const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
          if (seen.has(key) || len < 0.05) continue;
          seen.add(key);
          const ux = (b[0] - a[0]) / len, un = (b[1] - a[1]) / len;
          const at = (t) => [a[0] + ux * t, a[1] + un * t];
          const cuts = [];
          for (const o of openings) {
            const t = (o.p[0] - a[0]) * ux + (o.p[1] - a[1]) * un;
            const off = Math.abs((o.p[1] - a[1]) * ux - (o.p[0] - a[0]) * un);
            if (off < 0.45 && t > 0 && t < len) cuts.push([Math.max(0, t - o.w / 2), Math.min(len, t + o.w / 2), o.type]);
          }
          cuts.sort((x, y) => x[0] - y[0]);
          let done = 0;
          for (const [c0, c1, type] of cuts) {
            if (c0 > done + 0.05) pieces.push([at(done), at(c0)]);
            if (c1 > Math.max(c0, done)) gaps.push({ a: at(Math.max(c0, done)), b: at(c1), type });
            done = Math.max(done, c1);
          }
          if (len > done + 0.05) pieces.push([at(done), at(len)]);
        }
      }
    }
    const h = thickness / 2;
    const walls = pieces.map(([p, q]) => {
      const len = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1;
      const nx = (-(q[1] - p[1]) / len) * h, nn = ((q[0] - p[0]) / len) * h;
      const ring = [[p[0] + nx, p[1] + nn], [q[0] + nx, q[1] + nn], [q[0] - nx, q[1] - nn], [p[0] - nx, p[1] - nn]];
      return [[...ring, ring[0]]];
    });
    return { walls, gaps };
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

  #applyVisibility() {
    const walking = this.#mode === "walk";
    const wf = walking ? this.#floors.get(this.#walkFloor) : null;
    for (const f of this.#floors.values()) {
      // Walking: the floors up to yours, open to the sky. Dollhouse: one or all.
      const shown = walking ? Boolean(wf) && f.ordinal <= wf.ordinal : !this.#floor || f.id === this.#floor;
      f.group.visible = shown;
      f.group.position.y = this.#offset(f);
      const cut = !walking && this.#cutaway;
      if (f.wallsFull) f.wallsFull.visible = !cut;
      if (f.wallsCut) f.wallsCut.visible = cut;
      if (f.parapetsFull) f.parapetsFull.visible = !cut;
      if (f.parapetsCut) f.parapetsCut.visible = cut;
      for (const m of [f.heads, f.glass, f.frames, f.casings, f.leaves]) if (m) m.visible = !cut;
      if (f.ceiling) f.ceiling.visible = walking && f === wf;
      for (const s of f.spaces) {
        const visible = this.#o.showHidden || !s.tucked;
        if (s.finish) s.finish.visible = visible;
        s.volume.visible = visible && this.#xray;
        s.label.visible = visible && this.#o.labels && !walking && (!this.#floor ? f === this.#topShown() : true);
        if (!visible && s.id === this.#selected) s.highlight.visible = false;
      }
    }
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
        const [a, b] = op.span.map((c) => this.#local(c));
        mx = (a[0] + b[0]) / 2; mn = (a[1] + b[1]) / 2;
        len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
        nx = -(b[1] - a[1]) / len; nn = (b[0] - a[0]) / len;
      } else if (opening.geometry?.type === "Point" && space) {
        // no span (older packages): out is away from the middle of the room
        [mx, mn] = this.#local(opening.geometry.coordinates);
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
      const targets = [];
      for (const f of this.#floors.values()) {
        if (!f.group.visible) continue;
        for (const s of f.spaces) if (s.finish?.visible) targets.push(s.finish);
      }
      const hit = ray.intersectObjects(targets, false)[0];
      this.select(hit ? hit.object.userData.spaceId : null, { go: false });
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

/** Point in ring (ray casting), ring as [[x, n], …]. */
function inside(ring, x, n) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, ni] = ring[i], [xj, nj] = ring[j];
    if ((ni > n) !== (nj > n) && x < ((xj - xi) * (n - ni)) / (nj - ni) + xi) hit = !hit;
  }
  return hit;
}
