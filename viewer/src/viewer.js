// StoreyPathViewer: a read-only 3D viewer for StoreyPath packages, built on MapLibre GL.
//
//   const viewer = new StoreyPathViewer("#map");
//   await viewer.open("/campus.storeypath");
//   viewer.select("K7Q2XM-RUH-HQ-F02-0142");
//   viewer.addEventListener("select", (e) => console.log(e.detail.id));

import maplibregl from "maplibre-gl";

import { loadPackage } from "./package.js";
import { FLAT_TYPES, TYPE_COLORS } from "./theme.js";

const SOURCES = ["sp-buildings", "sp-floors", "sp-spaces", "sp-openings"];
const EMPTY = { type: "FeatureCollection", features: [] };

const DEFAULTS = {
  // Map under the buildings: true for OpenStreetMap tiles (needs the internet),
  // a tile URL template ("https://tiles.example/{z}/{x}/{y}.png") for your own
  // tile server, or false for none (offline).
  basemap: true,
  showHidden: false, // show spaces and doors marked hidden or ignored in Studio
  labels: true, // room names and numbers on the current floor
  labelMinZoom: 18.6,
  roomHeight: 2.4, // meters, how tall enclosed rooms are drawn on a single floor
  colors: {}, // space type → color, overrides TYPE_COLORS
  selectColor: "#ff8a00",
  background: "#ecebe6",
  mapOptions: {}, // passed to maplibregl.Map
};

/**
 * Events (listen with addEventListener; data in event.detail):
 *   load            { package }
 *   buildingchange  { id }
 *   floorchange     { id }
 *   modechange      { mode }               "floor" or "stack"
 *   select          { id, feature }        id is null when the selection is cleared
 */
export class StoreyPathViewer extends EventTarget {
  #options;
  #map;
  #ready;
  #pkg = null;
  #building = null;
  #floor = null;
  #mode = "floor";
  #selected = null;
  #highlighted = [];
  #highlightColor = "#13a37a";
  #markers = [];

  constructor(container, options = {}) {
    super();
    this.#options = { ...DEFAULTS, ...options, colors: { ...TYPE_COLORS, ...options.colors } };
    const element = typeof container === "string" ? document.querySelector(container) : container;
    if (!element) throw new Error(`StoreyPathViewer: container ${container} not found`);
    element.classList.add("storeypath-viewer");

    this.#map = new maplibregl.Map({
      container: element,
      style: baseStyle(this.#options),
      center: [0, 20],
      zoom: 1.5,
      maxPitch: 75,
      ...this.#options.mapOptions,
    });
    this.#map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    this.#map.addControl(new maplibregl.ScaleControl(), "bottom-right");
    this.#ready = new Promise((resolve) => {
      this.#map.on("load", () => {
        this.#addLayers();
        resolve();
      });
    });
  }

  /** The underlying MapLibre map, for anything this API does not cover. */
  get map() {
    return this.#map;
  }
  get package() {
    return this.#pkg;
  }
  get building() {
    return this.#building;
  }
  get floor() {
    return this.#floor;
  }
  get mode() {
    return this.#mode;
  }
  get selected() {
    return this.#selected;
  }

  /** Open a package from a URL, Blob, File or ArrayBuffer. */
  async open(source) {
    const pkg = await loadPackage(source);
    await this.#ready;
    this.#pkg = pkg;
    this.#selected = null;
    // Buildings not placed on the map yet sit around 0°N 0°E: a street map there
    // would only show sea.
    const placements = Object.values(pkg.manifest.placements || {});
    if (placements.length && placements.every((p) => p.placed === false)) this.setBasemap(false);

    this.#setData();

    this.setBuilding(pkg.buildings[0]?.id, { fit: false });
    this.fitTo(pkg.buildings, { duration: 0 });
    this.#emit("load", { package: pkg });
    return pkg;
  }

  /** Show or leave out spaces and doors marked hidden or ignored. */
  setShowHidden(on) {
    this.#options.showHidden = Boolean(on);
    if (!this.#pkg) return;
    this.#setData();
    this.#renderLabels();
  }

  #shown(feature) {
    return this.#options.showHidden || !(feature.properties.hidden || feature.properties.ignored);
  }

  #setData() {
    const pkg = this.#pkg;
    const floorInfo = new Map(pkg.floors.map((f) => [f.id, f.properties]));
    const styled = (features, extra, keep = () => true) => ({
      type: "FeatureCollection",
      features: features
        .filter((f) => f.geometry && keep(f))
        .map((f) => ({ ...f, properties: { ...f.properties, _id: f.id, ...extra(f) } })),
    });
    const shown = (f) => this.#shown(f);
    this.#map.getSource("sp-buildings").setData(styled(pkg.buildings, () => ({})));
    this.#map.getSource("sp-floors").setData(styled(pkg.floors, (f) => ({ _elev: f.properties.elevation })));
    this.#map.getSource("sp-openings").setData(styled(pkg.openings, () => ({}), shown));
    this.#map.getSource("sp-spaces").setData(
      styled(pkg.spaces, (s) => {
        const floor = floorInfo.get(s.properties.floor_id);
        return { _elev: floor.elevation, _fh: floor.height, _building: floor.building_id };
      }, shown),
    );
  }

  /** Show a building; its ground floor unless `floor` is given. */
  setBuilding(id, { floor, fit = true } = {}) {
    if (!this.#pkg || !id) return;
    if (id !== this.#building) {
      this.#building = id;
      this.#emit("buildingchange", { id });
    }
    this.setFloor(floor ?? this.#pkg.groundFloor(id)?.id);
    if (fit) this.fitTo([this.#pkg.get(id)]);
  }

  setFloor(id) {
    if (!this.#pkg || !id) return;
    const changed = id !== this.#floor;
    this.#floor = id;
    this.#applyView();
    if (changed) this.#emit("floorchange", { id });
  }

  /** "floor": the current floor at ground level, with labels and doors.
   *  "stack": every floor of the building at its real height. */
  setMode(mode) {
    if (mode !== "floor" && mode !== "stack") throw new Error(`unknown mode ${mode}`);
    if (mode === this.#mode) return;
    this.#mode = mode;
    this.#applyView();
    this.fitTo([this.#pkg?.get(this.#building)], { pitch: mode === "stack" ? 60 : 50 });
    this.#emit("modechange", { mode });
  }

  /** Select a space by ID: switches to its building and floor, and flies to it. */
  select(id, { fly = true } = {}) {
    const space = this.#pkg?.get(id);
    if (!space || space.properties.kind !== "space") return false;
    const floorId = space.properties.floor_id;
    const buildingId = this.#pkg.get(floorId).properties.building_id;
    if (buildingId !== this.#building) this.setBuilding(buildingId, { floor: floorId, fit: false });
    else if (floorId !== this.#floor) this.setFloor(floorId);
    this.#selected = id;
    this.#applyOverlays();
    if (fly) {
      this.#map.flyTo({ center: space.properties.display_point, zoom: Math.max(this.#map.getZoom(), 19.5) });
    }
    this.#emit("select", { id, feature: space });
    return true;
  }

  clearSelection() {
    if (this.#selected === null) return;
    this.#selected = null;
    this.#applyOverlays();
    this.#emit("select", { id: null, feature: null });
  }

  /** Color a set of spaces, e.g. where certain people sit. Replaces the previous highlight. */
  highlight(ids, { color = this.#highlightColor } = {}) {
    this.#highlighted = [...ids];
    this.#highlightColor = color;
    if (!this.#map.getLayer("sp-highlighted")) return; // before the first open(): applied on load
    this.#applyOverlays();
  }

  clearHighlight() {
    this.highlight([]);
  }

  /** Frame features (default: the current building). */
  fitTo(features, { pitch = 50, duration = 600 } = {}) {
    const list = (features ?? [this.#pkg?.get(this.#building)]).filter((f) => f?.geometry);
    if (!list.length) return;
    const bounds = new maplibregl.LngLatBounds();
    const walk = (c) => (typeof c[0] === "number" ? bounds.extend(c) : c.forEach(walk));
    for (const f of list) walk(f.geometry.coordinates);
    this.#map.fitBounds(bounds, { padding: 80, pitch, bearing: this.#map.getBearing() || -15, duration });
  }

  set3D(on) {
    this.#map.easeTo({ pitch: on ? 50 : 0 });
  }

  setBasemap(visible) {
    this.#ready.then(() => {
      if (this.#map.getLayer("sp-basemap")) {
        this.#map.setLayoutProperty("sp-basemap", "visibility", visible ? "visible" : "none");
      }
    });
  }

  destroy() {
    for (const m of this.#markers) m.remove();
    this.#map.remove();
  }

  // ---- internals -----------------------------------------------------------

  #emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  #addLayers() {
    const map = this.#map;
    for (const id of SOURCES) map.addSource(id, { type: "geojson", data: EMPTY });
    const color = ["match", ["get", "type"], ...Object.entries(this.#options.colors).flat(), "#cccccc"];

    map.addLayer({ id: "sp-building-outline", type: "line", source: "sp-buildings",
      paint: { "line-color": "#55555c", "line-width": 1.5, "line-dasharray": [2, 1] } });
    map.addLayer({ id: "sp-floor-slab", type: "fill-extrusion", source: "sp-floors",
      paint: { "fill-extrusion-color": "#d8d6cf", "fill-extrusion-opacity": 0.9 } });
    map.addLayer({ id: "sp-ghost", type: "fill-extrusion", source: "sp-spaces",
      paint: { "fill-extrusion-color": color, "fill-extrusion-opacity": 0.2 } });
    map.addLayer({ id: "sp-spaces", type: "fill-extrusion", source: "sp-spaces",
      paint: { "fill-extrusion-color": color, "fill-extrusion-opacity": 0.92 } });
    map.addLayer({ id: "sp-highlighted", type: "fill-extrusion", source: "sp-spaces",
      filter: ["==", ["get", "_id"], ""],
      paint: { "fill-extrusion-color": "#13a37a", "fill-extrusion-opacity": 0.95 } });
    map.addLayer({ id: "sp-selected", type: "fill-extrusion", source: "sp-spaces",
      filter: ["==", ["get", "_id"], ""],
      paint: { "fill-extrusion-color": this.#options.selectColor, "fill-extrusion-opacity": 0.95 } });
    map.addLayer({ id: "sp-doors", type: "circle", source: "sp-openings",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 17, 1.5, 21, 5],
        "circle-color": ["case", ["get", "exterior"], "#2e7d32", "#3b3b40"],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1,
      } });

    map.on("click", (e) => {
      const hit = map.queryRenderedFeatures(e.point, { layers: ["sp-spaces"] })[0];
      if (hit) this.select(hit.properties._id, { fly: false });
      else this.clearSelection();
    });
    map.on("mouseenter", "sp-spaces", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "sp-spaces", () => (map.getCanvas().style.cursor = ""));
    map.on("zoom", () => this.#updateLabelVisibility());
  }

  #applyView() {
    const map = this.#map;
    const flat = ["in", ["get", "type"], ["literal", FLAT_TYPES]];
    const onFloor = ["==", ["get", "floor_id"], this.#floor];
    const inBuilding = ["==", ["get", "_building"], this.#building];
    const raised = ["sp-spaces", "sp-highlighted", "sp-selected"];

    if (this.#mode === "stack") {
      const top = ["+", ["get", "_elev"], ["case", flat, 0.12, ["*", ["get", "_fh"], 0.7]]];
      map.setFilter("sp-floor-slab", ["==", ["get", "building_id"], this.#building]);
      map.setPaintProperty("sp-floor-slab", "fill-extrusion-base", ["max", 0, ["-", ["get", "_elev"], 0.15]]);
      map.setPaintProperty("sp-floor-slab", "fill-extrusion-height", ["get", "_elev"]);
      map.setPaintProperty("sp-floor-slab", "fill-extrusion-opacity", 0.35);
      map.setFilter("sp-ghost", ["all", inBuilding, ["!", onFloor]]);
      for (const id of ["sp-ghost", ...raised]) {
        map.setPaintProperty(id, "fill-extrusion-base", ["get", "_elev"]);
        map.setPaintProperty(id, "fill-extrusion-height", id === "sp-spaces" || id === "sp-ghost" ? top : ["+", top, 0.05]);
      }
      map.setLayoutProperty("sp-ghost", "visibility", "visible");
      map.setLayoutProperty("sp-doors", "visibility", "none");
    } else {
      // One floor, brought down to ground level so labels and doors line up with it.
      const top = ["case", flat, 0.08, this.#options.roomHeight];
      map.setFilter("sp-floor-slab", ["==", ["get", "_id"], this.#floor]);
      map.setPaintProperty("sp-floor-slab", "fill-extrusion-base", 0);
      map.setPaintProperty("sp-floor-slab", "fill-extrusion-height", 0.02);
      map.setPaintProperty("sp-floor-slab", "fill-extrusion-opacity", 0.9);
      for (const id of raised) {
        map.setPaintProperty(id, "fill-extrusion-base", 0);
        map.setPaintProperty(id, "fill-extrusion-height", id === "sp-spaces" ? top : ["+", top, 0.05]);
      }
      map.setLayoutProperty("sp-ghost", "visibility", "none");
      map.setLayoutProperty("sp-doors", "visibility", "visible");
      map.setFilter("sp-doors", onFloor);
    }
    map.setFilter("sp-spaces", onFloor);
    this.#applyOverlays();
    this.#renderLabels();
  }

  /** Selection and highlight, limited to what is on screen: the current floor,
   *  or the current building when every floor is shown. */
  #applyOverlays() {
    const scope = this.#mode === "stack"
      ? ["==", ["get", "_building"], this.#building]
      : ["==", ["get", "floor_id"], this.#floor];
    this.#map.setFilter("sp-selected", ["all", scope, ["==", ["get", "_id"], this.#selected ?? ""]]);
    this.#map.setFilter("sp-highlighted", ["all", scope, ["in", ["get", "_id"], ["literal", this.#highlighted]]]);
    this.#map.setPaintProperty("sp-highlighted", "fill-extrusion-color", this.#highlightColor);
  }

  #renderLabels() {
    for (const m of this.#markers) m.remove();
    this.#markers = [];
    if (!this.#options.labels || this.#mode === "stack" || !this.#pkg) return;
    for (const s of this.#pkg.spacesOn(this.#floor)) {
      if (!this.#shown(s)) continue;
      const { name, number, display_point } = s.properties;
      if (!name && !number) continue;
      const el = document.createElement("div");
      el.className = "sp-label";
      el.append(Object.assign(document.createElement("b"), { textContent: number || name }));
      if (number && name) el.append(document.createTextNode(name));
      this.#markers.push(new maplibregl.Marker({ element: el }).setLngLat(display_point).addTo(this.#map));
    }
    this.#updateLabelVisibility();
  }

  #updateLabelVisibility() {
    this.#map.getContainer().classList.toggle("sp-labels-hidden", this.#map.getZoom() < this.#options.labelMinZoom);
  }
}

function baseStyle({ basemap, background }) {
  const layers = [{ id: "sp-background", type: "background", paint: { "background-color": background } }];
  const style = { version: 8, sources: {}, layers };
  if (basemap) {
    // No source at all when there is no basemap: an offline page requests nothing.
    const own = typeof basemap === "string";
    style.sources.basemap = {
      type: "raster",
      tileSize: 256,
      maxzoom: 19,
      tiles: [own ? basemap : "https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      attribution: own ? "" : "© OpenStreetMap contributors",
    };
    layers.push({
      id: "sp-basemap",
      type: "raster",
      source: "basemap",
      paint: { "raster-opacity": 0.55, "raster-saturation": -0.6 },
    });
  }
  return style;
}
