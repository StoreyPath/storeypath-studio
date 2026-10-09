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
//
// A page may edit on top of it (Studio's Review does): pointAt says what is under the
// pointer or the crosshair, ghost shows where an item would go, setFloorItems draws a
// floor's items again (nothing else), items are dragged (setDraggable, itemdrag…), a
// click says what it is on first (pick, cancelable), updateSpace and reload show
// corrections and a floor read again. The world itself changes nothing.
//
// A way through the building (format 0.8: route() in ../navigation.js) is drawn with
// showRoute: a ribbon just over each floor it walks on, through the lift or stairs
// between them, its start and end marked; flyRoute takes the camera along it.
//
// It is drawn in a look (style.js: "real", real but clean, by default; "model", an
// architectural model) and a quality ("high", with ambient occlusion and multisampling;
// "low", for weak graphics; "auto", the default, chooses Low on a software, virtual or
// integrated renderer, or when High draws slowly at first). Switching either swaps
// materials, lights and passes: nothing is built again.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

import { loadPackage } from "../package.js";
import { TYPE_COLORS } from "../theme.js";
import {
  BUILDER, GEOMETRY, buildItems, buildPieces, ceilingPanels, cutPieces, flat, inside, itemBox, itemExtent, itemPoint,
  occluders, originOf, planFloor, planItem, setItems, toLocal as localOf,
} from "./build.js";
import { Painter } from "./finishes.js";
import { toLonLat } from "./frame.js";
import { qualityFor, rendererName } from "./gpu.js";
import { Materials } from "./materials.js";
import { QUALITY, SLOW_FRAME, STYLES } from "./style.js";
import { Obstacles, Walker } from "./walk.js";

const DEFAULTS = {
  ...GEOMETRY, // slab, door and window heights, wall thickness, cutaway height
  explode: 0, // m between floors in the dollhouse view
  labels: true,
  showHidden: false,
  items: null, // furniture and equipment: true, false, or null: shown when one floor is
  style: "real", // the look: "real" or "model" (style.js)
  quality: "auto", // "auto", "high" or "low"
  fog: null, // its colour (default: the look's)
};
const LABEL_STYLE = {
  display: "flex", flexDirection: "column", alignItems: "center", padding: "2px 7px", borderRadius: "7px",
  background: "rgba(255, 255, 255, 0.86)", color: "#16181d", whiteSpace: "nowrap",
  font: '600 11px/1.25 system-ui, -apple-system, "Segoe UI", sans-serif', boxShadow: "0 1px 4px rgba(0, 0, 0, 0.18)",
};
const VERTICAL = new Set(["stairs", "elevator", "escalator", "ramp"]);
const LABEL_ROOM = 56; // px: a room this wide on the screen (its longer side) has its label shown
// what casts and takes shadows, by material (walking, the ceiling keeps the sun out but
// at the windows); the order drawn in, after the rest
const CASTS = new Set(["slab", "wall", "wallTop", "wallCut", "wallPlain", "frame", "doorFrame", "door", "item", "skirting",
  "trim", "handle", "sillBoard", "ceiling"]);
const TAKES = new Set([...CASTS, "floor", "glass"].filter((m) => m !== "ceiling"));
const ORDER = { volume: 2, glass: 4 };
const QUALITIES = new Set(["auto", "high", "low"]);
// the sun's shadow follows what is looked at on a floor bigger than this (metres round
// it), walking and orbiting; and its frame moves in steps, so that it is drawn again
// only now and then
const SHADOW = { walk: 24, orbit: 1.1, least: 16, steps: 4 };
// auto: High is watched for this many frames, once the building has been shown this long (ms)
const WATCH = { frames: 90, after: 1500 };
// a way through the building: its ribbon this high over the floor and this wide, seen
// through what is in front of it, and arrows on it this far apart
const ROUTE = { lift: 0.14, width: 0.55, casing: 0.16, arrows: 3, order: 20, faded: 0.18,
  // its colours by default: the plan viewer's (viewer/svg/src/plan.css, --sp-route…)
  colours: { color: "#1a73e8", casing: "#ffffff", arrow: "#ffffff", start: "#0f9d58", end: "#d62d50" } };

export class StoreyPathWorld extends EventTarget {
  #o;
  #element;
  #renderer;
  #labelRenderer;
  #scene;
  #camera;
  #orbit;
  #walker;
  #materials; // the look's and quality's materials, shown now (one of #looks)
  #looks = new Map(); // "style:finish size" → Materials, each made the first time it is shown
  #painter = new Painter(); // paints the finishes, off the page's thread
  #style; // the look: "real" or "model"
  #quality; // asked for: "auto", "high" or "low"
  #drawn; // drawn: "high" or "low"
  #why = null; // why auto chose Low
  #watch = null; // auto: High's frames timed, at first
  #post = null; // High's passes (post.js), once loaded
  #posting = null;
  #hemi;
  #sun;
  #sunDir = new THREE.Vector3(-0.6, 1.4, 0.45).normalize(); // towards the sun
  #shadowAt = ""; // where the sun's shadow is fitted now
  #shadowsDirty = true; // the shadows drawn again next frame
  #panelLights = []; // walking: the nearest ceiling panels' lights
  #ground = null;
  #destroyed = false;
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
  #paused = false;
  #draggable = false; // items carried across their floor by a drag (setDraggable)
  #ghost = null; // an item's ghost (ghost)
  #ghostLook = null; // its materials
  #orbitView = null; // the dollhouse view before walking: { position, target }
  #caster = new THREE.Raycaster();
  #labelView = null; // the camera the labels were last placed for
  #labelsMoved = true; // what is shown changed: the labels placed again
  #route = null; // the way shown: { route, legs, links, marks, materials }
  #tour = null; // the camera going along it

  constructor(container, options = {}) {
    super();
    this.#o = { ...DEFAULTS, ...options };
    this.#style = STYLES[this.#o.style] ? this.#o.style : "real";
    this.#quality = QUALITIES.has(this.#o.quality) ? this.#o.quality : "auto";
    const element = typeof container === "string" ? document.querySelector(container) : container;
    if (!element) throw new Error(`StoreyPathWorld: container ${container} not found`);
    this.#element = element;
    element.classList.add("storeypath-world");
    if (getComputedStyle(element).position === "static") element.style.position = "relative";

    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.shadowMap.autoUpdate = false; // drawn again only when what casts them, or their frame, changed
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    element.appendChild(renderer.domElement);
    this.#renderer = renderer;
    // auto: Low on a software, virtual or integrated renderer; else High, watched at first
    const gpu = qualityFor(rendererName(renderer.getContext()));
    this.#drawn = this.#quality === "auto" ? gpu.quality : this.#quality;
    this.#why = this.#quality === "auto" ? gpu.why : null;

    this.#labelRenderer = new CSS2DRenderer();
    Object.assign(this.#labelRenderer.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
    element.appendChild(this.#labelRenderer.domElement);

    const scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();
    this.#hemi = new THREE.HemisphereLight();
    scene.add(this.#hemi);
    const sun = new THREE.DirectionalLight();
    sun.castShadow = true;
    scene.add(sun, sun.target);
    this.#sun = sun;
    this.#scene = scene;
    const look = (color) => ({
      solid: new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.5, depthWrite: false }),
      line: new THREE.LineBasicMaterial({ color, transparent: true, depthTest: false }),
    });
    this.#ghostLook = { ok: look(0x0ca678), refused: look(0xe03131),
      guide: new THREE.LineBasicMaterial({ color: 0xff8a00, transparent: true, depthTest: false }) };

    // near and far as close as they can be: the occlusion is worked out from depth
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 3000);
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
    this.#applyLook({ quiet: true });
    new ResizeObserver(() => this.#resize()).observe(element);
    this.#resize();
    this.#timer.connect(document);
    renderer.setAnimationLoop(() => this.#frame());
  }

  /** Stop drawing (the world kept as it is: shown again at once by resume), for a page
   * that hides it for a while. */
  pause() {
    if (this.#paused) return;
    this.#paused = true;
    this.#walker.unlock();
    this.#renderer.setAnimationLoop(null);
  }

  /** Draw again after pause. */
  resume() {
    if (!this.#paused) return;
    this.#paused = false;
    this.#timer.reset();
    this.#resize();
    this.#renderer.setAnimationLoop(() => this.#frame());
  }

  get paused() { return this.#paused; }

  // ---- the look --------------------------------------------------------------------

  /** The look: "real" (real but clean: floors finished by room type, plaster walls, soft
   * shadows) or "model" (an architectural model: white clay, floors tinted by room type,
   * lines along edges). Its materials, lights and passes change; nothing is built again. */
  setStyle(style) {
    if (!STYLES[style] || style === this.#style) return;
    this.#style = style;
    this.#applyLook();
  }

  /** The quality: "high" (ambient occlusion, multisampling, finer shadows and finishes),
   * "low" (none of them: for weak graphics), or "auto": Low on a software, virtual or
   * integrated renderer, or when High draws slowly at first; else High. */
  setQuality(quality) {
    if (!QUALITIES.has(quality) || quality === this.#quality) return;
    this.#quality = quality;
    const gpu = qualityFor(rendererName(this.#renderer.getContext()));
    this.#drawn = quality === "auto" ? gpu.quality : quality;
    this.#why = quality === "auto" ? gpu.why : null;
    this.#applyLook();
  }

  /** How it is drawn: ``style``, ``quality`` (as asked), ``drawn`` ("high" or "low") and
   * ``why`` auto chose Low ("software", "virtual" or "integrated": its renderer; "slow":
   * High drew slowly), else null. */
  get look() {
    return { style: this.#style, quality: this.#quality, drawn: this.#drawn, why: this.#why };
  }

  /** Resolves once the look is drawn as it will stay: its finishes painted, its passes loaded. */
  ready() {
    return Promise.all([this.#materials.ready(), this.#posting]).then(() => undefined);
  }

  /** The look and quality applied: tone mapping, materials (swapped on what is built),
   * the sky, lights, shadow map, passes and the pixels drawn. */
  #applyLook({ quiet = false } = {}) {
    const s = STYLES[this.#style], q = QUALITY[this.#drawn], r = this.#renderer;
    r.toneMapping = s.toneMapping;
    r.toneMappingExposure = s.exposure;
    const key = `${this.#style}:${q.finish}`;
    if (!this.#looks.has(key)) this.#looks.set(key, new Materials(r, { style: this.#style, quality: q, painter: this.#painter }));
    const m = this.#looks.get(key), swapped = m !== this.#materials;
    this.#materials = m;
    this.#scene.background = m.sky(s.sky);
    this.#scene.fog?.color.set(this.#o.fog ?? s.fog);
    this.#hemi.color.set(s.hemisphere[0]);
    this.#hemi.groundColor.set(s.hemisphere[1]);
    this.#sun.color.set(s.sun[0]);
    const shadow = this.#sun.shadow;
    Object.assign(shadow, { bias: s.shadow.bias, normalBias: s.shadow.normalBias, radius: s.shadow.radius });
    if (shadow.mapSize.x !== q.shadowMap) {
      shadow.mapSize.set(q.shadowMap, q.shadowMap);
      shadow.map?.dispose();
      shadow.map = null;
    }
    if (swapped) this.#rematerial();
    this.#lights(q.panels);
    this.#postProcessing(q.post);
    this.#resize();
    this.#applyVisibility(); // (lit as indoors or out, the x-ray, lines along edges, shadows)
    this.#watch = this.#quality === "auto" && this.#drawn === "high" && !this.#why ? { since: null, times: [] } : null;
    if (!quiet) this.#emit("lookchange", this.look);
  }

  /** Every piece built drawn with the look's materials. */
  #rematerial() {
    for (const f of this.#floors.values()) {
      for (const mesh of [...f.pieces, ...Object.values(f.itemMeshes).flat()]) mesh.material = this.#material(mesh.userData);
    }
    if (this.#ground) this.#ground.material = this.#materials.ground();
    if (this.#lit) this.#lit.material = this.#materials.highlight;
  }

  /** ``n`` lights for the ceiling panels nearest the walker (shown only when walking). */
  #lights(n) {
    while (this.#panelLights.length < n) {
      // shining down from just under the ceiling, wide and soft
      const light = new THREE.SpotLight(0xfff6ea, 0, 9, 1.25, 1, 2);
      light.visible = false;
      this.#scene.add(light, light.target);
      this.#panelLights.push(light);
    }
    while (this.#panelLights.length > n) {
      const light = this.#panelLights.pop();
      this.#scene.remove(light, light.target);
      light.dispose();
    }
  }

  /** High's passes (post.js, loaded the first time), or none. */
  #postProcessing(on) {
    if (!on) {
      this.#post?.dispose();
      this.#post = null;
      this.#posting = null;
      return;
    }
    if (this.#post || this.#posting) return;
    this.#posting = import("./post.js").then(({ makeComposer }) => {
      if (this.#post || this.#destroyed || !QUALITY[this.#drawn].post) return;
      this.#post = makeComposer(this.#renderer, this.#scene, this.#camera, { samples: QUALITY.high.samples });
      this.#resize();
    }).catch((e) => console.warn(`StoreyPathWorld: no post-processing (${e.message}): drawn as Low is`));
  }

  /** Lines along the edges of a floor's pieces (the "model" look), made the first time:
   * of its walls, slab, openings and items in a form. */
  #edgesOf(f, form) {
    const add = (meshes, pieces) => {
      const lines = new Map(pieces.map((p) => [p.name, p.edges]));
      for (const mesh of meshes) {
        const edges = lines.get(mesh.name);
        if (!edges?.length) continue;
        const g = new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(edges, 3));
        const line = new THREE.LineSegments(g, this.#materials.edges);
        line.name = `${mesh.name}:edges`;
        line.renderOrder = 1;
        line.raycast = () => {}; // never what a click is on
        mesh.add(line);
      }
    };
    if (!f.edged) {
      f.edged = true;
      const { pieces } = buildPieces(f.plan, { ...this.#o, only: "edges" });
      add(f.pieces, [...pieces, ...cutPieces(pieces, f.elevation, this.#o)]);
    }
    f.itemsEdged ??= new Set();
    if (form && f.itemMeshes[form] && !f.itemsEdged.has(form)) {
      f.itemsEdged.add(form);
      add(f.itemMeshes[form], buildItems(f.plan, form, { ...this.#o, only: "edges" }));
    }
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

  /** Build and show a building; with ``keep`` (the same building, read again), where it
   * was: the view, the floor shown, what is selected and the walker kept, and its frame. */
  #show(id, { keep = false } = {}) {
    const was = keep ? { floor: this.#floor, selected: this.#selected, route: this.#route } : null;
    if (keep) this.clearRoute(); // drawn again on the floors built again
    if (this.#buildingGroup) {
      this.#scene.remove(this.#buildingGroup);
      this.#dispose(this.#buildingGroup);
    }
    this.#building = id;
    this.#endTour();
    if (this.#watch) this.#watch.since = null; // (auto) its frames timed once it has been shown a while
    this.#route = null; // drawn on the floors taken away
    this.#floors.clear();
    this.#floor = null;
    this.#selected = null;
    this.#lit = null;
    this.#ghost = null;
    this.#buildingGroup = this.#buildBuilding(id, { keepOrigin: keep });
    this.#scene.add(this.#buildingGroup);
    if (keep) {
      if (was.floor && this.#floors.has(was.floor)) this.#floor = was.floor;
      if (this.#mode === "walk" && !this.#floors.has(this.#walkFloor)) {
        const p = this.#camera.position;
        this.#walkTo(this.#floor || this.#floorList()[0]?.id, p.x, p.z);
      }
      this.#applyVisibility();
      const found = was.selected ? this.#findSpace(was.selected) ?? this.#findItem(was.selected) : null;
      if (found) {
        this.#highlight(found);
        this.#selected = was.selected;
      }
      if (was.route) this.showRoute(was.route.route, was.route.colours);
      return;
    }
    this.#applyVisibility();
    this.#frameBuilding(false);
    this.#emit("buildingchange", { id });
  }

  /** Read the package again (a URL, Blob, File or ArrayBuffer: the same project, changed)
   * and build again only ``floors`` (default: every floor of the building shown), where
   * they stand: the view, the mode, the floor shown, what is selected and where the
   * walker stands are kept, and the other floors as they are. A building whose floors
   * are not the same any more is built again whole, still where it was; one the package
   * no longer has is replaced by its first, as `open` does. Floors read again are built
   * here (not from a pre-built world/). Resolves with the package. */
  async reload(source, { floors } = {}) {
    const pkg = await loadPackage(source);
    const id = this.#building;
    if (!id || !pkg.floorsOf(id).length) {
      this.#pkg = pkg;
      this.#baked = new Map();
      await this.setBuilding(pkg.buildings[0]?.id);
      if (this.#pkg === pkg) this.#emit("load", { package: pkg });
      return pkg;
    }
    const ids = (list) => list.map((f) => f.id ?? f).sort().join();
    const same = ids([...this.#floors.keys()]) === ids(pkg.floorsOf(id));
    this.#pkg = pkg;
    this.#baked = new Map();
    if (!same) this.#show(id, { keep: true });
    else for (const f of floors ?? [...this.#floors.keys()]) if (this.#floors.has(f)) this.#rebuildFloor(f, { given: false });
    this.#emit("reload", { floors: same ? (floors ?? [...this.#floors.keys()]).filter((f) => this.#floors.has(f)) : [...this.#floors.keys()] });
    return pkg;
  }

  /** One floor built again from the package, in place of the one built (its items as
   * given by setFloorItems kept, unless ``given`` is false). */
  #rebuildFloor(id, { given = true } = {}) {
    const old = this.#floors.get(id), floor = this.#pkg?.get(id);
    if (!old || !floor) return;
    if (this.#ghost?.parent === old.group) this.#ghost = null; // goes with it
    if (this.#lit?.parent === old.group) this.#lit = null;
    const route = this.#route;
    if (route) this.clearRoute(); // drawn again over the floor built again
    const built = this.#buildFloor(floor);
    if (given && old.given) {
      built.given = old.given;
      this.#furnish(built);
    }
    this.#buildingGroup.remove(old.group);
    this.#dispose(old.group);
    this.#floors.set(id, built);
    this.#buildingGroup.add(built.group);
    this.#applyVisibility();
    this.#relight();
    if (route) this.showRoute(route.route, route.colours);
  }

  /** The highlight made again where what is selected is now (it may have moved), or
   * taken away when it is gone. */
  #relight() {
    if (!this.#selected) return;
    const found = this.#findSpace(this.#selected) ?? this.#findItem(this.#selected);
    this.#highlight(found);
    if (!found) this.#selected = null;
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

  /** "dollhouse": orbit around the building; "walk": walk through it in the first person.
   * Walking starts outside the front door, looking in; or ``at`` ({ x, z }, local metres:
   * where the dollhouse view looks is `target`), on ``floor``, facing ``heading`` (radians,
   * as `player` turns; default: the way the view faces); at the middle of the nearest
   * room when ``at`` is in none. Back to the dollhouse, the view goes round the whole
   * building, or with ``back``, back to where it was before walking. */
  setMode(mode, { at, floor, heading, back = false } = {}) {
    if (mode === this.#mode) return;
    this.#mode = mode;
    if (mode === "walk") {
      // the orbit's last turn settled now, so that back from walking the view is where it was
      this.#orbit.enableDamping = false;
      this.#orbit.update();
      this.#orbit.enableDamping = true;
      this.#orbitView = { position: this.#camera.position.clone(), target: this.#orbit.target.clone() };
      this.#flight = null;
      const floorId = floor && this.#floors.has(floor) ? floor : this.#floor || this.#floorList()[0]?.id;
      const start = at ? this.#standAt(floorId, at) : this.#startPoint(floorId);
      this.#orbit.enabled = false;
      this.#walker.enabled = true;
      const dir = this.#camera.getWorldDirection(new THREE.Vector3());
      this.#walkTo(floorId, start.x, start.z, heading ?? start.heading ?? Math.atan2(-dir.x, -dir.z));
    } else {
      this.#walker.enabled = false;
      this.#walker.unlock();
      this.#orbit.enabled = true;
      this.#room = null;
      if (back && this.#orbitView) {
        const { position, target } = this.#orbitView;
        this.#flight = { from: this.#camera.position.clone(), to: position, fromT: this.#orbit.target.clone(), toT: target, t: 0 };
      } else this.#frameBuilding(true);
    }
    this.#applyVisibility();
    this.#emit("modechange", { mode });
  }

  /** In the walk view: take the mouse to look around (call from a click). */
  startWalking() {
    if (this.#mode !== "walk") this.setMode("walk");
    this.#walker.lock();
  }

  /** Give the mouse back (as Esc does), still in the walk view. */
  stopWalking() {
    this.#walker.unlock();
  }

  /** Where the dollhouse view looks: the point it turns around, { x, z } in local metres. */
  get target() {
    return { x: this.#orbit.target.x, z: this.#orbit.target.z };
  }

  /** Where to stand on a floor, near ``at``: there, in a room; else the middle of the
   * room nearest it. */
  #standAt(floorId, { x, z }) {
    const f = this.#floors.get(floorId);
    if (!f || this.#spaceAt(f, x, z)) return { x, z };
    let best = null;
    for (const s of f.spaces) {
      if (s.tucked && !this.#o.showHidden) continue;
      const d = Math.hypot(s.centre.x - x, s.centre.z - z);
      if (!best || d < best.d) best = { s, d };
    }
    return best ? { x: best.s.centre.x, z: best.s.centre.z } : { x, z };
  }

  // ---- the building's own frame ---------------------------------------------------------

  /** The building's placement on the map (format 0.7), or null. */
  #placement() {
    return this.#pkg?.manifest?.placements?.[this.#building] ?? null;
  }

  /** A point of the building's own frame — [x, y], metres of its drawings, as Studio and
   * an item's `local` have them — in the world: { x, z } (local metres). Null when the
   * package does not place the building (no `placements`: before format 0.7). */
  worldPoint([x, y]) {
    const placement = this.#placement();
    if (!placement || !this.#origin) return null;
    const [wx, n] = localOf(this.#origin, toLonLat(placement, [x, y]));
    return { x: wx, z: -n };
  }

  /** A point of the world ({ x, z }, local metres) in the building's own frame: [x, y],
   * the point its placement puts there (to a micrometre); null as for worldPoint. */
  buildingPoint({ x, z }) {
    const placement = this.#placement();
    if (!placement || !this.#origin) return null;
    const world = (p) => localOf(this.#origin, toLonLat(placement, p));
    // Newton's method: over a building the placement is a turn and a shift, all but exactly
    let p = [placement.x, placement.y];
    for (let i = 0; i < 8; i++) {
      const f = world(p), ax = world([p[0] + 1, p[1]]), ay = world([p[0], p[1] + 1]);
      const a = ax[0] - f[0], c = ax[1] - f[1], b = ay[0] - f[0], d = ay[1] - f[1], det = a * d - b * c;
      const ex = x - f[0], en = -z - f[1];
      if (Math.hypot(ex, en) < 1e-7 || !det) break;
      p = [p[0] + (d * ex - b * en) / det, p[1] + (a * en - c * ex) / det];
    }
    return p;
  }

  // ---- aiming: what is under the pointer, or the crosshair ------------------------------------

  /** What is under a point of the screen (client pixels) — walking with the mouse taken,
   * or given no point, under the middle of the view (the crosshair): the floor, the point
   * on it ({ x, z }, local metres, and ``local``, [x, y] in the building's own frame), the
   * space or zone it is in, and the item there, if one is in the way. The first thing in
   * the way counts: aimed at a wall, the point is on the floor just before it; at an item,
   * the point under where it was met. Null when nothing is in the way and no floor is
   * there. Quick whatever the floor (no triangles: the plan's walls and the items' boxes),
   * so it can follow the pointer, or the crosshair every frame. */
  pointAt(clientX, clientY) {
    if (!this.#pkg) return null;
    this.#camera.updateMatrixWorld(); // (moved since the last frame, maybe)
    this.#caster.setFromCamera(this.#ndc(clientX, clientY), this.#camera);
    const ray = this.#caster.ray;
    const walking = this.#mode === "walk";
    const floors = walking ? [this.#floors.get(this.#walkFloor)].filter(Boolean)
      : [...this.#floors.values()].filter((f) => f.group.visible).sort((a, b) => b.ordinal - a.ordinal);
    let best = null, plane = null;
    for (const f of floors) {
      const got = this.#aimOn(f, ray);
      if (!got) continue;
      // a floor met where it has no slab is passed by (outside it), unless it is the walker's
      const on = got.item || got.wall || walking || this.#onSlab(f, got.x, got.z);
      if (!on) {
        plane ??= { f, ...got };
        continue;
      }
      if (!best || got.t < best.t) best = { f, ...got };
    }
    best ??= plane;
    if (!best) return null;
    const { f, x, z, item = null } = best;
    return { floor: f.id, x, z, local: this.buildingPoint({ x, z }), space: this.#spaceAt(f, x, z)?.id ?? null, item };
  }

  /** The point of the view under a point of the screen, as three.js has it (-1 to 1); the
   * middle when walking with the mouse taken, or given none. */
  #ndc(clientX, clientY) {
    if (clientX === undefined || clientY === undefined || (this.#mode === "walk" && this.#walker.locked)) return new THREE.Vector2(0, 0);
    const r = this.#renderer.domElement.getBoundingClientRect();
    return new THREE.Vector2(((clientX - r.left) / (r.width || 1)) * 2 - 1, -((clientY - r.top) / (r.height || 1)) * 2 + 1);
  }

  /** What a ray meets first on a floor: { t, x, z } and ``item`` (its ID), ``wall`` (the
   * point pulled back off it, onto the floor before it) or neither (the floor); or null. */
  #aimOn(f, ray) {
    const o = ray.origin, d = ray.direction;
    const y0 = f.elevation + this.#offset(f);
    let best = null;
    if (d.y < -1e-9) {
      const t = (y0 - o.y) / d.y;
      if (t > 0) best = { t, x: o.x + d.x * t, z: o.z + d.z * t };
    }
    const far = best ? best.t : 500;
    // walls, parapets, windows and heads: where the ray's way across the plan crosses each,
    // at a height it stands
    const cut = this.#mode === "dollhouse" && this.#cutaway;
    f.occluders ??= occluders(f.plan, this.#o);
    for (const [x1, z1, x2, z2, from, to, toCut] of f.occluders) {
      const top = cut ? Math.min(to, toCut) : to;
      if (top <= from) continue;
      const ex = x2 - x1, ez = z2 - z1, den = d.x * ez - d.z * ex;
      if (Math.abs(den) < 1e-12) continue;
      const qx = x1 - o.x, qz = z1 - o.z;
      const t = (qx * ez - qz * ex) / den, s = (qx * d.z - qz * d.x) / den;
      if (t <= 0 || t >= (best?.t ?? far) || s < 0 || s > 1) continue;
      const y = o.y + d.y * t - y0;
      if (y < from || y > top) continue;
      best = { t, x: o.x + d.x * t, z: o.z + d.z * t, wall: true };
    }
    if (best?.wall) { // onto the floor just before it
      const h = Math.hypot(d.x, d.z);
      if (h > 1e-6) {
        best.x -= (d.x / h) * 0.05;
        best.z -= (d.z / h) * 0.05;
      }
    }
    // the items shown: each the box it takes
    if (f.furnished) {
      for (const it of f.items) {
        const t = this.#hitItem(it, y0, o, d);
        if (t !== null && t < (best?.t ?? Infinity)) best = { t, x: o.x + d.x * t, z: o.z + d.z * t, item: it.id };
      }
    }
    return best;
  }

  /** Where a ray (origin o, direction d) meets an item's box, standing on a floor at y0: the
   * ray's t, or null. */
  #hitItem(it, y0, o, d) {
    const [u0, v0, u1, v1, top] = itemExtent(it);
    // its own frame: across (-fn, -fx) and ahead (fx, -fn) in x and z, up from its bottom
    const rx = o.x - it.x, rz = o.z + it.n;
    const ou = -rx * it.fn - rz * it.fx, ov = rx * it.fx - rz * it.fn, ow = o.y - (y0 + it.y);
    const du = -d.x * it.fn - d.z * it.fx, dv = d.x * it.fx - d.z * it.fn, dw = d.y;
    let near = 0, far = Infinity;
    for (const [p, q, lo, hi] of [[ou, du, u0, u1], [ov, dv, v0, v1], [ow, dw, 0, top]]) {
      if (Math.abs(q) < 1e-12) {
        if (p < lo || p > hi) return null;
        continue;
      }
      let t0 = (lo - p) / q, t1 = (hi - p) / q;
      if (t0 > t1) [t0, t1] = [t1, t0];
      near = Math.max(near, t0);
      far = Math.min(far, t1);
      if (near > far) return null;
    }
    return near > 0 ? near : null;
  }

  /** Whether a point of a floor is on its slab (in its outline, or in a room). */
  #onSlab(f, x, z) {
    const n = -z;
    return f.plan.outline.some((rings) => inside(rings[0], x, n)) || Boolean(this.#spaceAt(f, x, z, true));
  }

  /** The point of a floor's level under a point of the screen (where an item carried
   * across it is), or null. */
  #onLevel(f, clientX, clientY) {
    this.#caster.setFromCamera(this.#ndc(clientX, clientY), this.#camera);
    const { origin: o, direction: d } = this.#caster.ray;
    const t = (f.elevation + this.#offset(f) - o.y) / d.y;
    return d.y < -1e-9 && t > 0 ? { x: o.x + d.x * t, z: o.z + d.z * t } : null;
  }

  // ---- editing: items given, a ghost, items carried ---------------------------------------

  /** Replace one floor's furniture and equipment, building nothing else again: ``items``,
   * each { id, type, x, y, rotation } in the building's own frame (metres of its drawings;
   * rotation: degrees counter-clockwise, its front its own -y, as Studio keeps them), with
   * ``width``, ``depth``, ``height`` (m), ``mount``, ``elevation``, ``color`` and ``grade``
   * where they are not its type's in the package's catalogue. They are drawn again the next
   * frame (in milliseconds, for thousands); the walker bumps into them at once. Needs the
   * building's placement (format 0.7). Whether the floor is built. */
  setFloorItems(floorId, items) {
    const f = this.#floors.get(floorId);
    if (!f || !this.#placement()) return false;
    f.given = items.map((g) => ({ ...g }));
    this.#furnish(f);
    this.#applyVisibility();
    this.#relight();
    return true;
  }

  /** A floor's items made again from those given (setFloorItems). */
  #furnish(f) {
    for (const meshes of Object.values(f.itemMeshes)) {
      for (const m of meshes) {
        m.removeFromParent();
        m.traverse((o) => o.geometry?.dispose()); // (its lines along edges with it)
      }
    }
    f.itemMeshes = {};
    f.itemsEdged = null;
    f.bakedItems = null;
    f.itemObstacles = null;
    setItems(f.plan, f.given.map((g) => this.#itemOf(f, g)));
    f.items = f.plan.items;
    f.itemIds = f.items.map((i) => i.id);
  }

  /** An item given in the building's own frame, as the floor plans its items. */
  #itemOf(f, g) {
    const t = this.#pkg?.itemType?.(g.type) ?? null;
    const p = { type: g.type, mount: g.mount ?? t?.mount ?? "floor",
      local: { x_m: g.x, y_m: g.y, rotation_deg: g.rotation ?? 0 },
      width_m: g.width ?? t?.width, depth_m: g.depth ?? t?.depth, height_m: g.height ?? t?.height,
      elevation_m: g.elevation !== undefined ? g.elevation : t?.elevation ?? null };
    return planItem(g.id, p, { origin: this.#origin, placement: this.#placement(), wallHeight: f.plan.wallHeight,
      type: { color: g.color ?? t?.color, grade: g.grade ?? t?.grade } });
  }

  /** Show where an item would go (placed, or carried): see-through over its floor, green,
   * or red when it may not go there (``ok: false``), its footprint outlined, with the
   * lines it is drawn to (``guides``: [[x, y], [x, y]] each). ``spec``: an item as
   * setFloorItems takes one (no ID needed), on ``floor`` (default: the floor shown, or
   * walked on). Null takes it away. */
  ghost(spec) {
    if (this.#ghost) {
      this.#ghost.removeFromParent();
      this.#ghost.traverse((o) => o.geometry?.dispose());
      this.#ghost = null;
    }
    if (!spec) return;
    const f = this.#floors.get(spec.floor ?? (this.#mode === "walk" ? this.#walkFloor : this.#floor ?? this.#topShown()?.id));
    const it = f && this.#placement() ? this.#itemOf(f, { id: "ghost", ...spec }) : null;
    if (!it) return;
    const look = spec.ok === false ? this.#ghostLook.refused : this.#ghostLook.ok;
    const group = new THREE.Group();
    group.name = "ghost";
    for (const p of buildItems({ elevation: f.elevation, items: [it] }, "detailed", this.#o)) {
      const mesh = new THREE.Mesh(p.geometry, look.solid);
      mesh.renderOrder = 6;
      group.add(mesh);
    }
    const y = f.elevation + 0.02;
    const v = ([x, z]) => new THREE.Vector3(x, y, z);
    const ring = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => v(itemPoint(it, (a * it.width) / 2, (b * it.depth) / 2)));
    const outline = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(ring), look.line);
    outline.renderOrder = 7;
    group.add(outline);
    const guides = (spec.guides ?? []).map(([a, b]) => [this.worldPoint(a), this.worldPoint(b)]).filter(([p, q]) => p && q);
    if (guides.length) {
      const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(
        guides.flatMap(([p, q]) => [v([p.x, p.z]), v([q.x, q.z])])), this.#ghostLook.guide);
      lines.renderOrder = 7;
      group.add(lines);
    }
    f.group.add(group);
    this.#ghost = group;
  }

  /** Let items be carried across their floor by a drag in the dollhouse view (not by
   * default): the world says where they are dragged (itemdragstart, itemdrag, itemdragend,
   * each { id, floor, x, z, local, altKey, shiftKey }) and the page moves them
   * (setFloorItems, ghost) as it decides. A press that does not move stays a click. */
  setDraggable(on) {
    this.#draggable = Boolean(on);
    if (!on) this.#renderer.domElement.style.cursor = "";
  }

  get draggable() { return this.#draggable; }

  /** A space or zone corrected (any of ``name``, ``number``, ``type``, ``hidden``,
   * ``ignored``, as the package's properties): its label changed at once; its floor built
   * again when its type (its floor's finish) or whether it is shown changed. Whether it is
   * in the world. */
  updateSpace(id, props) {
    const feature = this.#pkg?.get(id);
    const f = feature ? this.#floors.get(feature.properties.floor_id) : null;
    if (!f) return false;
    const p = feature.properties;
    const tucked = () => Boolean(p.hidden || p.ignored);
    const was = { type: p.type, tucked: tucked() };
    for (const k of ["name", "number", "type", "hidden", "ignored"]) if (k in props) p[k] = props[k];
    if (p.type !== was.type || tucked() !== was.tucked) {
      this.#rebuildFloor(f.id);
      return true;
    }
    const s = f.spaces.find((u) => u.id === id);
    if (s) {
      Object.assign(s, { name: p.name, number: p.number });
      const label = this.#label(s);
      label.position.copy(s.label.position);
      s.label.removeFromParent(); // (its element with it)
      s.label = label;
      if (label.element.textContent) f.group.add(label);
      this.#applyVisibility();
    }
    return true;
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

  // ---- a way through the building ---------------------------------------------

  /** The way shown (showRoute), or null. */
  get route() { return this.#route?.route ?? null; }

  /** Draw a way (as route() in navigation.js finds it, format 0.8): a ribbon just over
   * each floor it walks on, edged, arrows the way it goes, joined through the lift or
   * stairs between floors, its start and end marked; seen through what is in front of
   * it, and through the floors above it, which the dollhouse view of the whole building
   * leaves out. Null: none. With `fly`, the camera goes along it (flyRoute). Its
   * colours (CSS colours: a page gives its own, light or dark): `color`, `casing` (its
   * edge), `arrow`, `start`, `end`; by default the plan viewer's. */
  showRoute(route, { fly = false, ...colours } = {}) {
    this.clearRoute();
    const placement = this.#pkg?.manifest.placements?.[this.#building];
    if (!route || !placement || !route.legs?.some((leg) => this.#floors.has(leg.floor_id))) return Promise.resolve();
    // a point of the building's drawings, in the world: x east, z south
    const at = ([x, y]) => {
      const [wx, n] = localOf(this.#origin, toLonLat(placement, [x, y]));
      return [wx, -n];
    };
    const colour = (key) => {
      const c = new THREE.Color(ROUTE.colours[key]);
      try {
        if (colours[key] !== undefined && colours[key] !== null) c.set(colours[key]);
      } catch {
        // not a colour: the default
      }
      return c;
    };
    const shown = { route, colours, legs: [], links: [], marks: [], materials: [] }; // (colours: drawn again so)
    const material = (key, more = {}) => {
      const m = new THREE.MeshBasicMaterial({ color: colour(key), side: THREE.DoubleSide, depthTest: false, transparent: true,
        ...more });
      shown.materials.push(m);
      return m;
    };
    const add = (mesh, name, parent, order = ROUTE.order) => {
      mesh.name = name;
      mesh.renderOrder = order;
      parent.add(mesh);
      return mesh;
    };
    route.legs.forEach((leg, i) => {
      const f = this.#floors.get(leg.floor_id);
      if (!f) return;
      const points = leg.points.map(at);
      const y = f.elevation + ROUTE.lift;
      // each leg its own materials: the legs not on the floor the camera is going along fade
      const entry = { floor: f, points, y, mesh: null, materials: [] };
      if (points.length > 1) {
        const casing = material("casing", { opacity: 0.95 }), ribbon = material("color", { opacity: 0.95 });
        entry.materials.push(casing, ribbon);
        shown.marks.push(add(new THREE.Mesh(ribbonOf(points, y - 0.004, ROUTE.width + 2 * ROUTE.casing), casing),
          `route:casing:${i}`, f.group, ROUTE.order - 1));
        entry.mesh = add(new THREE.Mesh(ribbonOf(points, y, ROUTE.width), ribbon), `route:leg:${i}`, f.group);
        const marks = arrowsOf(points, y + 0.01, ROUTE.width, ROUTE.arrows);
        if (marks) {
          const arrow = material("arrow");
          entry.materials.push(arrow);
          shown.marks.push(add(new THREE.Mesh(marks, arrow), `route:arrows:${i}`, f.group, ROUTE.order + 1));
        }
      }
      shown.legs.push(entry);
      if (i === 0) {
        const dot = add(new THREE.Mesh(new THREE.CylinderGeometry(0.45, 0.45, 0.12, 24), material("start")), "route:start", f.group,
          ROUTE.order + 2);
        dot.position.set(points[0][0], y + 0.06, points[0][1]);
        shown.marks.push(dot);
      }
      if (i === route.legs.length - 1) {
        const pin = add(new THREE.Mesh(new THREE.ConeGeometry(0.4, 1.3, 20), material("end")), "route:end", f.group, ROUTE.order + 2);
        const last = points[points.length - 1];
        pin.rotation.x = Math.PI; // its point down, on the destination
        pin.position.set(last[0], y + 0.65, last[1]);
        shown.marks.push(pin);
      }
    });
    // between floors: through the lift or stairs, from where one leg ends to where the next starts
    for (let i = 0; i + 1 < shown.legs.length; i++) {
      const a = shown.legs[i], b = shown.legs[i + 1];
      const ribbon = material("color", { opacity: 0.95 });
      const mesh = add(new THREE.Mesh(new THREE.CylinderGeometry(ROUTE.width / 2.5, ROUTE.width / 2.5, 1, 12, 1, true), ribbon),
        `route:link:${i}`, this.#buildingGroup);
      shown.links.push({ a, b, from: a.points[a.points.length - 1], to: b.points[0], mesh, materials: [ribbon] });
    }
    this.#route = shown;
    this.#applyVisibility();
    return fly ? this.flyRoute() : Promise.resolve();
  }

  /** The way's legs as the camera goes along it: the one it is on (and the rides to and
   * from it) clear, the others faded; all clear with null. */
  #fadeRoute(leg) {
    const shown = this.#route;
    if (!shown) return;
    const clear = (i) => leg === null || i === leg;
    shown.legs.forEach((l, i) => {
      for (const m of l.materials) m.opacity = clear(i) ? 0.95 : ROUTE.faded;
    });
    shown.links.forEach((l, i) => {
      for (const m of l.materials) m.opacity = leg === null || i === leg || i + 1 === leg ? 0.95 : ROUTE.faded;
    });
  }

  /** Take the way away (and stop going along it). */
  clearRoute() {
    this.#endTour();
    const shown = this.#route;
    this.#route = null;
    if (!shown) return;
    for (const m of [...shown.legs.map((l) => l.mesh), ...shown.links.map((l) => l.mesh), ...shown.marks]) {
      if (!m) continue;
      m.removeFromParent();
      m.geometry.dispose();
    }
    for (const m of shown.materials) m.dispose();
    this.#applyVisibility();
  }

  /** Take the camera along the way shown, over `seconds`, in the dollhouse view;
   * resolves when it is there (or when the way is taken away). */
  flyRoute({ seconds = 14 } = {}) {
    if (!this.#route) return Promise.resolve();
    if (this.#mode === "walk") this.setMode("dollhouse");
    this.#endTour();
    const points = [], legOf = [];
    this.#route.legs.forEach((leg, i) => {
      const dy = leg.floor.group.position.y;
      for (const [x, z] of leg.points) {
        points.push(new THREE.Vector3(x, leg.y + dy, z));
        legOf.push(i);
      }
    });
    if (points.length < 2) return Promise.resolve();
    const along = [0];
    for (let i = 1; i < points.length; i++) along.push(along[i - 1] + points[i].distanceTo(points[i - 1]));
    this.#flight = null;
    return new Promise((resolve) => {
      this.#tour = { points, along, legOf, leg: -1, t: 0, seconds: Math.max(0.1, seconds), resolve };
    });
  }

  #endTour() {
    const tour = this.#tour;
    this.#tour = null;
    if (tour) this.#fadeRoute(null);
    tour?.resolve();
  }

  /** The point `s` metres along the tour's line, and the leg it is on. */
  #tourAt(tour, s) {
    const { points, along } = tour;
    const total = along[along.length - 1];
    s = Math.max(0, Math.min(total, s));
    let i = 1;
    while (i < along.length - 1 && along[i] < s) i++;
    const span = along[i] - along[i - 1] || 1;
    const point = points[i - 1].clone().lerp(points[i], (s - along[i - 1]) / span);
    point.leg = tour.legOf[i - 1] === tour.legOf[i] ? tour.legOf[i] : tour.legOf[s - along[i - 1] < span / 2 ? i - 1 : i];
    return point;
  }

  /** The way's links between floors where the floors are now (one apart from the
   * other in the exploded view), and shown when both floors are. */
  #placeRoute() {
    for (const link of this.#route?.links ?? []) {
      const a = new THREE.Vector3(link.from[0], link.a.y + link.a.floor.group.position.y, link.from[1]);
      const b = new THREE.Vector3(link.to[0], link.b.y + link.b.floor.group.position.y, link.to[1]);
      const length = a.distanceTo(b);
      link.mesh.visible = link.a.floor.group.visible && link.b.floor.group.visible && length > 1e-3;
      link.mesh.position.copy(a).add(b).multiplyScalar(0.5);
      link.mesh.scale.set(1, Math.max(length, 1e-3), 1);
      link.mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize());
    }
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
    this.#destroyed = true;
    this.#endTour();
    this.#renderer.setAnimationLoop(null);
    this.#walker.dispose();
    this.#orbit.dispose();
    if (this.#buildingGroup) this.#dispose(this.#buildingGroup);
    this.#post?.dispose();
    this.#painter.dispose();
    for (const m of this.#looks.values()) m.dispose();
    this.#sun.shadow.map?.dispose();
    this.#scene.environment?.dispose();
    this.#renderer.dispose();
    this.#element.replaceChildren();
  }

  // ---- building the world ------------------------------------------------------

  #walkFloor = null;

  #floorList() {
    return this.#pkg ? this.#pkg.floorsOf(this.#building).filter((f) => this.#floors.has(f.id)) : [];
  }

  #buildBuilding(buildingId, { keepOrigin = false } = {}) {
    const pkg = this.#pkg;
    const floors = pkg.floorsOf(buildingId);
    if (!keepOrigin || !this.#origin) this.#origin = originOf(pkg, buildingId); // kept: the view stays where it was
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
    ground.renderOrder = -1; // drawn first, out of the depth the occlusion reads (Materials.ground)
    group.add(ground);
    this.#ground = ground;
    this.#placeSun(centre, span);
    this.#scene.fog = new THREE.Fog(this.#o.fog ?? STYLES[this.#style].fog, span * 4, fogFar);
    this.#camera.far = Math.max(3000, fogFar * 1.6);
    this.#camera.updateProjectionMatrix();
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
    mesh.userData = { material: p.material, type: p.type, view: p.view, hidden: Boolean(p.hidden), form: p.form };
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
    // a way shown, over the whole building: the floors above the highest it goes to are left out
    const top = !walking && !this.#floor && this.#route
      ? Math.max(...this.#route.legs.map((l) => l.floor.ordinal)) : Infinity;
    const s = STYLES[this.#style], q = QUALITY[this.#drawn];
    for (const f of this.#floors.values()) {
      // Walking: the floors up to yours, open to the sky. Dollhouse: one or all.
      const shown = walking ? Boolean(wf) && f.ordinal <= wf.ordinal : (!this.#floor || f.id === this.#floor) && f.ordinal <= top;
      f.group.visible = shown;
      f.group.position.y = this.#offset(f);
      const cut = !walking && this.#cutaway;
      const seen = ({ view, hidden }) => (this.#o.showHidden || !hidden) && (view === "full" ? !cut : view === "cut" ? cut
        : view === "walk" ? walking && f === wf : view === "xray" ? this.#xray : true);
      for (const m of f.pieces) m.visible = seen(m.userData);
      for (const s of f.spaces) {
        const visible = this.#o.showHidden || !s.tucked;
        s.labelOn = visible && this.#o.labels && !walking && (!this.#floor ? f === this.#topShown() : true);
        s.label.visible = s.labelOn; // (and, each frame, only where there is room for it: #placeLabels)
      }
      const furnished = items && shown && (!walking || f === wf) && f.items.length > 0;
      f.furnished = furnished;
      if (furnished) this.#itemsIn(f, form);
      for (const [name, meshes] of Object.entries(f.itemMeshes)) {
        for (const m of meshes) m.visible = furnished && name === form && seen(m.userData);
      }
      if (f === wf) this.#walker.obstacles = this.#obstaclesOf(f, furnished);
      // the "model" look: lines along edges, made the first time a floor (its items) shows
      if (s.edges && shown) this.#edgesOf(f, furnished ? form : null);
      for (const mesh of [...f.pieces, ...Object.values(f.itemMeshes).flat()]) {
        for (const line of mesh.children) {
          line.visible = s.edges;
          line.material = this.#materials.edges;
        }
      }
    }
    if (this.#lit) this.#lit.visible = this.#o.showHidden || !this.#lit.userData.space?.tucked;
    this.#labelsMoved = true;
    this.#placeRoute();
    const see = this.#xray ? 0.22 : 1;
    const m8 = this.#materials;
    for (const m of [m8.wall, m8.wallPlain, m8.wallTop, m8.wallCut, m8.frame, m8.doorFrame, m8.door, m8.skirting, m8.handle,
      m8.sillBoard]) {
      m.transparent = this.#xray;
      m.opacity = see;
      m.depthWrite = !this.#xray;
    }
    // walking, lit as indoors: less sky (the ceiling keeps the sun out but at the
    // windows), and the nearest ceiling panels (#lightPanels)
    this.#hemi.intensity = walking ? s.walk.hemisphere * q.ambient : s.hemisphere[2];
    this.#scene.environmentIntensity = walking ? s.walk.environment * q.ambient : s.environment;
    this.#sun.intensity = walking ? s.walk.sun : s.sun[1];
    for (const light of this.#panelLights) light.visible = walking;
    this.#shadowsDirty = true;
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
    sun.position.copy(centre).addScaledVector(this.#sunDir, size * 1.6);
    sun.target.position.copy(centre);
    this.#shadowAt = ""; // fitted again, next frame
  }

  /** The sun's shadow fitted closely round the floors shown (the walker's, walking), so
   * that its texels are as small as they can be; on a floor bigger than what is looked at,
   * round that (walking, round the walker), in steps. Drawn again only when it moved. */
  #fitSun() {
    const walking = this.#mode === "walk", wf = walking ? this.#floors.get(this.#walkFloor) : null;
    const box = this.#sunBox.makeEmpty(), floor = this.#floorBox;
    for (const f of this.#floors.values()) {
      if (!f.group.visible || (wf && f !== wf) || f.bounds3.isEmpty()) continue;
      floor.copy(f.bounds3);
      floor.min.y += f.group.position.y;
      floor.max.y += f.group.position.y;
      box.union(floor);
    }
    if (box.isEmpty()) return;
    // round what is looked at: a reach in steps of √2, its middle in quarters of it
    const focus = walking ? this.#camera.position : this.#orbit.target;
    const wide = walking ? SHADOW.walk : Math.max(SHADOW.least, this.#camera.position.distanceTo(this.#orbit.target) * SHADOW.orbit);
    const reach = SHADOW.least * Math.SQRT2 ** Math.ceil(Math.log2(wide / SHADOW.least) * 2);
    const step = reach / SHADOW.steps;
    const near = (lo, hi, at) => {
      const a = Math.max(lo, Math.floor((at - reach) / step) * step), b = Math.min(hi, Math.ceil((at + reach) / step) * step);
      return a < b ? [a, b] : [lo, hi];
    };
    const [x0, x1] = near(box.min.x, box.max.x, focus.x), [z0, z1] = near(box.min.z, box.max.z, focus.z);
    const at = [x0, x1, z0, z1, box.min.y, box.max.y].map((v) => v.toFixed(2)).join();
    if (at === this.#shadowAt) return;
    this.#shadowAt = at;
    const sun = this.#sun, cam = sun.shadow.camera;
    const centre = new THREE.Vector3((x0 + x1) / 2, (box.min.y + box.max.y) / 2, (z0 + z1) / 2);
    const away = Math.hypot(x1 - x0, box.max.y - box.min.y, z1 - z0) + 50;
    sun.target.position.copy(centre);
    sun.position.copy(centre).addScaledVector(this.#sunDir, away);
    sun.updateMatrixWorld();
    sun.target.updateMatrixWorld();
    cam.position.copy(sun.position);
    cam.lookAt(centre);
    cam.updateMatrixWorld();
    // the box's corners as the sun sees them (a little under the floor: what stands on it)
    const lo = new THREE.Vector3(Infinity, Infinity, Infinity), hi = lo.clone().negate();
    for (let i = 0; i < 8; i++) {
      const v = new THREE.Vector3(i & 1 ? x1 : x0, i & 2 ? box.max.y : box.min.y - 0.5, i & 4 ? z1 : z0).applyMatrix4(cam.matrixWorldInverse);
      lo.min(v);
      hi.max(v);
    }
    Object.assign(cam, { left: lo.x - 1, right: hi.x + 1, bottom: lo.y - 1, top: hi.y + 1, near: Math.max(0.5, -hi.z - 10),
      far: -lo.z + 10 });
    cam.updateProjectionMatrix();
    this.#shadowsDirty = true;
  }

  #sunBox = new THREE.Box3(); // (each frame: kept, not made)
  #floorBox = new THREE.Box3();

  // ---- loop and input ----------------------------------------------------------------

  #frame() {
    this.#timer.update();
    const dt = this.#timer.getDelta();
    if (this.#tour) { // along the way: from behind and above where it has got to, looking ahead
      const tour = this.#tour;
      tour.t = Math.min(tour.seconds, tour.t + dt);
      const total = tour.along[tour.along.length - 1];
      const s = (tour.t / tour.seconds) * total;
      const here = this.#tourAt(tour, s), ahead = this.#tourAt(tour, s + 4);
      if (here.leg !== tour.leg) {
        tour.leg = here.leg;
        this.#fadeRoute(here.leg);
      }
      const back = here.clone().sub(ahead).setY(0);
      if (back.lengthSq() < 1e-6) back.copy(this.#camera.position).sub(here).setY(0);
      back.normalize().multiplyScalar(9);
      const want = here.clone().add(back).add(new THREE.Vector3(0, 8, 0));
      this.#camera.position.lerp(want, tour.t >= tour.seconds ? 1 : Math.min(1, dt * 3));
      this.#orbit.target.lerp(here, Math.min(1, dt * 4));
      if (tour.t >= tour.seconds) {
        this.#orbit.target.copy(here);
        this.#endTour();
      }
    } else if (this.#flight) {
      const f = this.#flight;
      f.t = Math.min(1, f.t + dt / 0.9);
      const k = f.t < 0.5 ? 2 * f.t * f.t : 1 - (-2 * f.t + 2) ** 2 / 2;
      this.#camera.position.lerpVectors(f.from, f.to, k);
      this.#orbit.target.lerpVectors(f.fromT, f.toT, k);
      if (f.t >= 1) this.#flight = null;
    }
    if (this.#mode === "walk") {
      this.#walker.update(dt);
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
    this.#lightPanels();
    this.#fitSun();
    if (this.#shadowsDirty) {
      this.#renderer.shadowMap.needsUpdate = true;
      this.#shadowsDirty = false;
    }
    if (this.#post) {
      // occlusion over metres close up; seen from afar, over a share of the distance
      const ao = STYLES[this.#style].ao;
      const far = this.#mode === "walk" ? 0 : this.#camera.position.distanceTo(this.#orbit.target);
      this.#post.setAo(ao, Math.min(4, Math.max(ao.radius, far * ao.overview)));
      this.#post.composer.render(dt);
    } else this.#renderer.render(this.#scene, this.#camera);
    this.#placeLabels();
    this.#timeHigh(dt);
  }

  /** Walking: the ceiling panels of the walker's room nearest them lit (spot lights
   * without shadows: none from another room, so none through a wall); outside a room,
   * those within a few metres. The lights stay in the scene (unlit), so that no material
   * is made again as the walker goes from room to room. */
  #lightPanels() {
    const lights = this.#panelLights;
    const f = this.#mode === "walk" ? this.#floors.get(this.#walkFloor) : null;
    if (!f || !lights.length) return;
    if (!f.panels) {
      f.panels = new Map();
      for (const q of ceilingPanels(f.plan)) f.panels.set(q.unit, [...(f.panels.get(q.unit) ?? []), q]);
    }
    const p = this.#camera.position, room = this.#room?.id;
    const near = (room ? f.panels.get(room) ?? [] : [...f.panels.values()].flat().filter((q) => Math.hypot(q.x - p.x, q.z - p.z) < 4))
      .map((q) => ({ q, d: (q.x - p.x) ** 2 + (q.z - p.z) ** 2 }))
      .sort((a, b) => a.d - b.d);
    const y = f.elevation + f.plan.wallHeight - 0.03, power = STYLES[this.#style].walk.panels;
    lights.forEach((light, i) => {
      const hit = near[i]?.q;
      light.intensity = hit ? power : 0;
      if (!hit) return;
      light.position.set(hit.x, y, hit.z);
      light.target.position.set(hit.x, f.elevation, hit.z);
    });
  }

  /** Auto: High's frames timed once the building has been shown a while; drawn slower
   * than SLOW_FRAME (the median), Low instead, from then on. */
  #timeHigh(dt) {
    const watch = this.#watch;
    if (!watch || !this.#floors.size || document.hidden) return;
    const now = performance.now();
    watch.since ??= now;
    if (now - watch.since < WATCH.after || dt <= 0) return;
    watch.times.push(dt * 1000);
    if (watch.times.length < WATCH.frames) return;
    this.#watch = null;
    const median = watch.times.sort((a, b) => a - b)[watch.times.length >> 1];
    if (median <= SLOW_FRAME || this.#quality !== "auto") return;
    this.#drawn = "low";
    this.#why = "slow";
    this.#applyLook();
  }

  /** The rooms' labels, placed again when the view moved (or what is shown changed): only
   * those of rooms on the screen and large enough on it to hold one (as the plan does),
   * so a floor of a thousand rooms moves as smoothly as one of ten. */
  #placeLabels() {
    const cam = this.#camera, view = cam.matrixWorld.elements, was = this.#labelView;
    const moved = !was || this.#labelsMoved || view.some((v, i) => v !== was[i]);
    if (!moved) return;
    this.#labelView = view.slice();
    this.#labelsMoved = false;
    const focal = (this.#renderer.domElement.clientHeight || 1) / (2 * Math.tan((cam.fov * Math.PI) / 360));
    const v = new THREE.Vector3();
    for (const f of this.#floors.values()) {
      if (!f.group.visible) continue;
      for (const s of f.spaces) {
        if (!s.labelOn) continue;
        v.copy(s.label.position);
        v.y += f.group.position.y;
        const far = Math.max(v.distanceTo(cam.position), 1e-3);
        v.project(cam);
        s.label.visible = v.z > -1 && v.z < 1 && Math.abs(v.x) < 1.15 && Math.abs(v.y) < 1.15 && (s.size * focal) / far >= LABEL_ROOM;
      }
    }
    this.#labelRenderer.render(this.#scene, cam);
  }

  #resize() {
    const w = this.#element.clientWidth || 1, h = this.#element.clientHeight || 1;
    // High: up to 1.5 of the screen's pixels a CSS pixel, and not too many in all; Low: one
    const q = QUALITY[this.#drawn];
    const ratio = Math.min(window.devicePixelRatio || 1, q.pixelRatio, Math.sqrt(q.pixels / (w * h)));
    if (this.#renderer.getPixelRatio() !== ratio) this.#renderer.setPixelRatio(ratio);
    this.#renderer.setSize(w, h, false);
    this.#post?.composer.setPixelRatio(ratio);
    this.#post?.composer.setSize(w, h);
    this.#renderer.domElement.style.width = "100%";
    this.#renderer.domElement.style.height = "100%";
    this.#labelRenderer.setSize(w, h);
    this.#camera.aspect = w / h;
    this.#camera.updateProjectionMatrix();
    this.#labelsMoved = true;
  }

  /** A click (a press that does not move) on the dollhouse view, or walking with the mouse
   * taken (at the crosshair): ``pick``, which a page may cancel (preventDefault: it does
   * something else with the click, as placing an item there), else what was clicked is
   * selected. A press on an item that moves, where items may be carried (setDraggable):
   * the item dragged. */
  #pointerPicking() {
    const canvas = this.#renderer.domElement;
    let down = null, drag = null, hover = 0;
    // An item taken up: before the orbit sees the press (this listens on the way down to it).
    this.#element.addEventListener("pointerdown", (e) => {
      if (!this.#draggable || this.#mode !== "dollhouse" || e.button !== 0 || e.target !== canvas) return;
      const p = this.pointAt(e.clientX, e.clientY);
      if (!p?.item) return;
      this.#orbit.enabled = false;
      drag = { id: p.item, floor: this.#floors.get(p.floor), at: [e.clientX, e.clientY], moved: false };
      canvas.setPointerCapture?.(e.pointerId);
    }, true);
    const where = (e, [cx, cy] = [e.clientX, e.clientY]) => {
      const at = this.#onLevel(drag.floor, cx, cy);
      return { id: drag.id, floor: drag.floor.id, x: at?.x ?? null, z: at?.z ?? null, local: at ? this.buildingPoint(at) : null,
        altKey: e.altKey, shiftKey: e.shiftKey };
    };
    canvas.addEventListener("pointermove", (e) => {
      if (drag) {
        if (!drag.moved && Math.hypot(e.clientX - drag.at[0], e.clientY - drag.at[1]) <= 4) return;
        if (!drag.moved) {
          drag.moved = true;
          this.#emit("itemdragstart", where(e, drag.at));
        }
        this.#emit("itemdrag", where(e));
        return;
      }
      // over an item that may be carried: the hand
      if (!this.#draggable || this.#mode !== "dollhouse" || e.buttons || hover) return;
      hover = requestAnimationFrame(() => {
        hover = 0;
        if (!this.#draggable) return;
        canvas.style.cursor = this.pointAt(e.clientX, e.clientY)?.item ? "grab" : "";
      });
    });
    const putDown = (e, cancelled = false) => {
      if (!drag) return;
      const { moved } = drag;
      if (moved) this.#emit("itemdragend", { ...where(e), cancelled });
      drag = null;
      this.#orbit.enabled = this.#mode === "dollhouse";
      if (moved) down = null; // not a click
    };
    canvas.addEventListener("pointerup", (e) => putDown(e));
    canvas.addEventListener("pointercancel", (e) => putDown(e, true));
    canvas.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
    canvas.addEventListener("pointerup", (e) => {
      const walking = this.#mode === "walk" && this.#walker.locked;
      if (e.button !== 0) return;
      if (!walking && (this.#mode !== "dollhouse" || !down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4)) return;
      const p = this.pointAt(e.clientX, e.clientY); // walking: at the crosshair
      const pick = new CustomEvent("pick", { cancelable: true, detail: {
        ...(p ?? { floor: null, x: null, z: null, local: null, space: null, item: null }),
        button: e.button, altKey: e.altKey, shiftKey: e.shiftKey } });
      this.dispatchEvent(pick);
      if (pick.defaultPrevented) return;
      this.select(walking ? p?.item ?? p?.space ?? null : this.#clicked(e.clientX, e.clientY), { go: false });
    });
  }

  /** The room or item clicked in the dollhouse view: of the floor finishes and the items
   * shown (merged), the one the triangle hit is of. */
  #clicked(clientX, clientY) {
    this.#caster.setFromCamera(this.#ndc(clientX, clientY), this.#camera);
    const targets = [];
    for (const f of this.#floors.values()) {
      if (!f.group.visible) continue;
      for (const m of f.pieces) if (m.visible && m.userData.material === "floor") targets.push(m);
      for (const meshes of Object.values(f.itemMeshes)) for (const m of meshes) if (m.visible) targets.push(m);
    }
    const hit = this.#caster.intersectObjects(targets, false)[0];
    const floor = hit && [...this.#floors.values()].find((f) => f.group === hit.object.parent);
    const g = hit?.object.geometry;
    const id = !floor ? null : hit.object.userData.material === "item" ? floor.itemIds[g.getAttribute("_item").getX(hit.face.a)]
      : floor.rooms[g.getAttribute("_room")?.getX(hit.face.a)];
    return id ?? null;
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

/** A ribbon `width` wide along points [x, z] at height y: a strip of triangles, its
 * corners mitred (no further out than twice its half-width). */
function ribbonOf(points, y, width) {
  const half = width / 2, pos = [], index = [];
  const n = points.length;
  const unit = (dx, dz) => {
    const l = Math.hypot(dx, dz) || 1;
    return [dx / l, dz / l];
  };
  for (let i = 0; i < n; i++) {
    const [x, z] = points[i];
    const into = i > 0 ? unit(x - points[i - 1][0], z - points[i - 1][1]) : null;
    const out = i < n - 1 ? unit(points[i + 1][0] - x, points[i + 1][1] - z) : null;
    const [tx, tz] = unit((into?.[0] ?? 0) + (out?.[0] ?? 0), (into?.[1] ?? 0) + (out?.[1] ?? 0));
    const side = [-tz, tx]; // across the way
    const across = into ?? out;
    const k = half / Math.max(0.5, Math.abs(side[0] * -across[1] + side[1] * across[0]));
    pos.push(x + side[0] * k, y, z + side[1] * k, x - side[0] * k, y, z - side[1] * k);
    if (i > 0) index.push(2 * i - 2, 2 * i - 1, 2 * i, 2 * i - 1, 2 * i + 1, 2 * i);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(index);
  return g;
}

/** Arrows on a ribbon, `every` metres along it, each pointing the way it goes; null
 * for a way too short for one. */
function arrowsOf(points, y, width, every) {
  const pos = [];
  let next = every / 2, walked = 0;
  for (let i = 1; i < points.length; i++) {
    const [x0, z0] = points[i - 1], [x1, z1] = points[i];
    const seg = Math.hypot(x1 - x0, z1 - z0);
    while (seg > 0 && next <= walked + seg) {
      const t = (next - walked) / seg;
      const dx = (x1 - x0) / seg, dz = (z1 - z0) / seg;
      const cx = x0 + (x1 - x0) * t, cz = z0 + (z1 - z0) * t;
      const l = width * 0.45, w = width * 0.32;
      pos.push(cx + dx * l, y, cz + dz * l, cx - dz * w - dx * l * 0.4, y, cz + dx * w - dz * l * 0.4,
        cx + dz * w - dx * l * 0.4, y, cz - dx * w - dz * l * 0.4);
      next += every;
    }
    walked += seg;
  }
  if (!pos.length) return null;
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  return g;
}
