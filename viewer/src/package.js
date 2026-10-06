// Reading StoreyPath packages (*.storeypath): a ZIP of GeoJSON, JSON and CSV files.
// The format is described in spec/FORMAT.md.

import JSZip from "jszip";

export const FORMAT = "storeypath-package";
export const SUPPORTED_MAJOR_VERSION = 0;

const COLLECTIONS = ["location", "buildings", "floors", "spaces", "openings"];
const SINCE_0_3 = ["zones"]; // parts of open spaces; none in older packages

/**
 * Load a package from a URL, Blob, File or ArrayBuffer.
 * @returns {Promise<StoreyPathPackage>}
 */
export async function loadPackage(source) {
  let data = source;
  if (typeof source === "string" || source instanceof URL) {
    const response = await fetch(source);
    if (!response.ok) throw new Error(`Could not load ${source} (HTTP ${response.status})`);
    data = await response.blob();
  }
  const zip = await JSZip.loadAsync(data);
  const read = async (name) => {
    const file = zip.file(name);
    if (!file) throw new Error(`The package has no ${name}.`);
    return JSON.parse(await file.async("string"));
  };

  const manifest = await read("manifest.json");
  if (manifest.format !== FORMAT) throw new Error("This is not a StoreyPath package.");
  const major = Number(String(manifest.format_version).split(".")[0]);
  if (major !== SUPPORTED_MAJOR_VERSION) {
    throw new Error(`Package format ${manifest.format_version} is not supported by this viewer.`);
  }
  const collections = {};
  for (const role of COLLECTIONS) collections[role] = (await read(manifest.files[role])).features;
  for (const role of SINCE_0_3) collections[role] = manifest.files[role] ? (await read(manifest.files[role])).features : [];
  return new StoreyPathPackage(manifest, collections);
}

export class StoreyPathPackage {
  constructor(manifest, { location, buildings, floors, spaces, openings, zones = [] }) {
    this.manifest = manifest;
    this.locations = location;
    this.buildings = buildings;
    this.floors = floors;
    this.spaces = spaces; // what walls and doors enclose
    this.zones = zones; // the parts of open spaces, with no wall between them
    this.openings = openings;

    this.byId = new Map();
    for (const list of [location, buildings, floors, spaces, zones, openings]) {
      for (const f of list) this.byId.set(f.id, f);
    }
    this._zonesBySpace = groupBy(zones, (z) => z.properties.space_id);
    // what is used: the zones of a space that has them, otherwise the space
    this.units = [...zones, ...spaces.filter((s) => !this._zonesBySpace.has(s.id))];
    this._unitsByFloor = groupBy(this.units, (u) => u.properties.floor_id);
    this._floorsByBuilding = groupBy(floors, (f) => f.properties.building_id);
    for (const list of this._floorsByBuilding.values()) {
      list.sort((a, b) => a.properties.ordinal - b.properties.ordinal);
    }
    this._spacesByFloor = groupBy(spaces, (s) => s.properties.floor_id);
    this._doorsBySpace = new Map();
    for (const o of openings) {
      for (const id of o.properties.connects) {
        if (!this._doorsBySpace.has(id)) this._doorsBySpace.set(id, []);
        this._doorsBySpace.get(id).push(o);
      }
    }
  }

  get project() {
    return this.manifest.project;
  }

  /** The feature with this ID, or null. */
  get(id) {
    return this.byId.get(id) ?? null;
  }

  /** Floors of a building, lowest first. */
  floorsOf(buildingId) {
    return this._floorsByBuilding.get(buildingId) ?? [];
  }

  spacesOn(floorId) {
    return this._spacesByFloor.get(floorId) ?? [];
  }

  /** The zones a space is divided into (none when it is used as a whole). */
  zonesOf(spaceId) {
    return this._zonesBySpace.get(spaceId) ?? [];
  }

  /** What is used on a floor: the zones of spaces divided into zones, and the other spaces. */
  unitsOn(floorId) {
    return this._unitsByFloor.get(floorId) ?? [];
  }

  /** The ground floor of a building (ordinal 0), or its lowest floor. */
  groundFloor(buildingId) {
    const floors = this.floorsOf(buildingId);
    return floors.find((f) => f.properties.ordinal === 0) ?? floors[0] ?? null;
  }

  /**
   * Everything an ID says about where an object is:
   * PROJECT-LOCATION-BUILDING-FLOOR-OBJECT.
   */
  hierarchy(id) {
    const segments = id.split("-");
    const at = (n) => (segments.length >= n ? this.get(segments.slice(0, n).join("-")) : null);
    return {
      project: this.manifest.project,
      location: at(2),
      building: at(3),
      floor: at(4),
      object: segments.length === 5 ? this.get(id) : null,
    };
  }

  /** Doors of a space, each with the space on its other side (null for exits). */
  doorsOf(spaceId) {
    return (this._doorsBySpace.get(spaceId) ?? []).map((door) => {
      const other = door.properties.connects.find((c) => c !== spaceId);
      return { door, space: other ? this.get(other) : null };
    });
  }

  /**
   * Find spaces and zones (what is used) by name, number, ID or type. Options: type
   * (exact type), buildingId, floorId, limit.
   */
  search(query = "", { type, buildingId, floorId, limit = 50 } = {}) {
    const q = query.trim().toLowerCase();
    const hits = [];
    for (const s of this.units) {
      const p = s.properties;
      if (type && p.type !== type) continue;
      if (floorId && p.floor_id !== floorId) continue;
      if (buildingId && !p.floor_id.startsWith(buildingId + "-")) continue;
      if (q && ![p.name, p.number, s.id, p.type].some((v) => v && v.toLowerCase().includes(q))) continue;
      hits.push(s);
      if (hits.length >= limit) break;
    }
    return hits;
  }
}

function groupBy(items, key) {
  const map = new Map();
  for (const item of items) {
    const k = key(item);
    if (!map.has(k)) map.set(k, []);
    map.get(k).push(item);
  }
  return map;
}
