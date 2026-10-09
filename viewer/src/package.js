// Reading StoreyPath packages (*.storeypath): a ZIP of GeoJSON, JSON and CSV files.
// The format is described in spec/FORMAT.md.

import JSZip from "jszip";

export const FORMAT = "storeypath-package";
/** The format version this viewer reads, any patch of it (spec/FORMAT.md, "Versioning"). */
export const FORMAT_VERSION = "0.9";
export const SUPPORTED_MAJOR_VERSION = Number(FORMAT_VERSION.split(".")[0]);

const VERSION = /^(\d+)\.(\d+)(\.\d+)?([-+][0-9A-Za-z.-]+)?$/;

/** Refuses a package's format version this viewer does not read: one that is not a
 * version, one of another major version, or (before 1.0, where a minor version may
 * change what a package means) one of a newer minor version, saying to update the
 * viewer. Older versions, and newer patches (properties added), are read. */
export function checkVersion(version) {
  const m = typeof version === "string" ? VERSION.exec(version) : null;
  if (!m) throw new Error(`The package's format version ${JSON.stringify(version) ?? "(none)"} is not a version this viewer can read.`);
  const major = Number(m[1]), minor = Number(m[2]);
  const [MAJOR, MINOR] = FORMAT_VERSION.split(".").map(Number);
  if (major > MAJOR || (major === MAJOR && MAJOR === 0 && minor > MINOR)) {
    throw new Error(`This package is format ${version}, newer than this viewer's ${FORMAT_VERSION}: update the viewer.`);
  }
  if (major !== MAJOR) throw new Error(`This package is format ${version}, older than this viewer reads (${MAJOR}.x).`);
}

const COLLECTIONS = ["location", "buildings", "floors", "spaces", "openings"];
const SINCE_0_3 = ["zones"]; // parts of open spaces; none in older packages
const SINCE_0_6 = ["items"]; // furniture and equipment; none in older packages

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
  checkVersion(manifest.format_version);
  const collections = {};
  for (const role of COLLECTIONS) collections[role] = (await read(manifest.files[role])).features;
  for (const role of [...SINCE_0_3, ...SINCE_0_6]) {
    collections[role] = manifest.files[role] ? (await read(manifest.files[role])).features : [];
  }
  collections.catalogue = manifest.files.catalogue ? await read(manifest.files.catalogue) : null;
  // the walking network (format 0.8), for finding the way: navigation.js
  collections.navigation = manifest.files.navigation ? await read(manifest.files.navigation) : null;
  return new StoreyPathPackage(manifest, collections, zip);
}

export class StoreyPathPackage {
  /** ``zip``: the archive, for the files read only when needed (the pre-built 3D). */
  constructor(manifest, { location, buildings, floors, spaces, openings, zones = [], items = [], catalogue = null,
    navigation = null }, zip = null) {
    this.manifest = manifest;
    this._zip = zip;
    this.locations = location;
    this.buildings = buildings;
    this.floors = floors;
    this.spaces = spaces; // what walls and doors enclose
    this.zones = zones; // the parts of open spaces, with no wall between them
    this.openings = openings;
    this.items = items; // furniture and equipment: desks, photocopiers, access points, …
    this.catalogue = catalogue; // the types of items (catalogue.json), or null
    // the building's walking network (navigation.json, format 0.8), or null: route()
    // in navigation.js finds the way on it
    this.navigation = navigation;

    this.byId = new Map();
    for (const list of [location, buildings, floors, spaces, zones, openings, items]) {
      for (const f of list) this.byId.set(f.id, f);
    }
    this._itemsByFloor = groupBy(items, (i) => i.properties.floor_id);
    this._itemTypes = new Map((catalogue?.types ?? []).map((t) => [t.code, t]));
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

  /** The buildings the package holds when it is part of a project (format 0.4; from
   * 0.7, always one), or null for the whole project. */
  get scope() {
    return this.manifest.scope?.buildings ?? null;
  }

  /** Whether the package holds a building: always, for the whole project; for a
   * part, when the building is in its scope. What is outside it is not in the
   * package, and its absence says nothing about it. */
  holds(buildingId) {
    return this.scope === null || this.scope.includes(buildingId);
  }

  /** Whether a floor comes pre-built in 3D (format 0.5: world/<floor-id>.glb). */
  hasWorld(floorId) {
    return Boolean(this._worldFile(floorId));
  }

  /** A floor's pre-built 3D as binary glTF, or null when the package has none. */
  async world(floorId) {
    const file = this._worldFile(floorId);
    return file ? file.async("arraybuffer") : null;
  }

  _worldFile(floorId) {
    const dir = this.manifest.files?.world;
    return dir && this._zip ? this._zip.file(`${dir.endsWith("/") ? dir : dir + "/"}${floorId}.glb`) : null;
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

  /** The furniture and equipment on a floor (format 0.6; none in older packages). An
   * item's ID says nothing of where it is: its properties do (from 0.7, ``local``:
   * where it stands in its building). */
  itemsOn(floorId) {
    return this._itemsByFloor.get(floorId) ?? [];
  }

  /** An item type of the catalogue by its code (an item's ``type``): its names,
   * category, size, mount, colour and fields; or null. */
  itemType(code) {
    return this._itemTypes.get(code) ?? null;
  }

  /** The ground floor of a building (ordinal 0), or its lowest floor. */
  groundFloor(buildingId) {
    const floors = this.floorsOf(buildingId);
    return floors.find((f) => f.properties.ordinal === 0) ?? floors[0] ?? null;
  }

  /**
   * Everything an ID says about where an object is:
   * PROJECT-LOCATION-BUILDING-FLOOR-OBJECT. An item's ID (an asset's tag,
   * 7K2Q-XM9F-4DP) says nothing of it: its floor does, and the item is the object.
   */
  hierarchy(id) {
    const item = this.get(id);
    if (item?.properties.kind === "item") return { ...this.hierarchy(item.properties.floor_id), object: item };
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
