// A floor of a StoreyPath package (spec/FORMAT.md) as the engine's floor model:
// its zones and undivided spaces, in the building's local drawing metres (the
// drawing the way the architect drew it, whatever its bearing on the map).

import { LocalFrame, type Placement } from "./frame.js";
import type { FloorPlan, PlanOpening, PlanSpace, Polygon, XY } from "./types.js";

type LonLat = [number, number];

interface Geometry {
  type: string;
  coordinates: unknown;
}

interface Feature<P> {
  id: string;
  geometry: Geometry | null;
  properties: P;
}

interface FloorProps { building_id: string; walls?: Geometry | null; parapets?: Geometry | null }
interface SpaceProps {
  floor_id: string; type: string; name?: string | null; number?: string | null;
  display_point: LonLat; zones?: string[]; hidden?: boolean; ignored?: boolean;
}
interface ZoneProps extends SpaceProps { space_id: string }
interface OpeningProps {
  floor_id: string; type: "door" | "window" | "opening"; span?: LonLat[] | null; swings?: LonLat[][] | null;
  hidden?: boolean; ignored?: boolean;
}

/** What a package gives: its manifest and its features (as viewer/src/package.js loads them, or as read). */
export interface PackageLike {
  manifest: { placements?: Record<string, Placement> };
  floors: Feature<FloorProps>[];
  spaces: Feature<SpaceProps>[];
  zones?: Feature<ZoneProps>[];
  openings: Feature<OpeningProps>[];
}

export interface FromPackageOptions {
  /** Spaces marked hidden in review: left out unless asked for (ignored ones always are). */
  showHidden?: boolean;
}

/** A floor of a package, ready to draw. */
export function floorFromPackage(pkg: PackageLike, floorId: string, options: FromPackageOptions = {}): FloorPlan {
  const floor = pkg.floors.find((f) => f.id === floorId);
  if (!floor) throw new Error(`no floor ${floorId} in the package`);
  const placement = pkg.manifest.placements?.[floor.properties.building_id];
  if (!placement) throw new Error(`no placement for ${floor.properties.building_id}`);
  const frame = new LocalFrame(placement);
  const local = (p: LonLat): XY => frame.toLocal(p);
  const polygons = (g: Geometry | null | undefined): Polygon[] => {
    if (!g) return [];
    if (g.type === "Polygon") return [(g.coordinates as LonLat[][]).map((r) => r.map(local))];
    if (g.type === "MultiPolygon") return (g.coordinates as LonLat[][][]).map((p) => p.map((r) => r.map(local)));
    return [];
  };
  const shown = (p: { hidden?: boolean; ignored?: boolean }): boolean => !p.ignored && (options.showHidden || !p.hidden);

  const zonesOf = new Map<string, Feature<ZoneProps>[]>();
  for (const z of pkg.zones ?? []) {
    if (z.properties.floor_id !== floorId) continue;
    const list = zonesOf.get(z.properties.space_id) ?? [];
    list.push(z);
    zonesOf.set(z.properties.space_id, list);
  }
  const spaces: PlanSpace[] = [];
  const containers: { id: string; polygons: Polygon[] }[] = [];
  const unit = (f: Feature<SpaceProps>, kind: "space" | "zone", container: string | null): PlanSpace => ({
    id: f.id, kind, polygons: polygons(f.geometry), marker: local(f.properties.display_point), type: f.properties.type,
    name: f.properties.name ?? null, number: f.properties.number ?? null, container,
  });
  for (const s of pkg.spaces) {
    if (s.properties.floor_id !== floorId || !shown(s.properties)) continue;
    const zones = zonesOf.get(s.id) ?? [];
    if (!zones.length) {
      spaces.push(unit(s, "space", null));
      continue;
    }
    containers.push({ id: s.id, polygons: polygons(s.geometry) });
    for (const z of zones) if (shown(z.properties)) spaces.push(unit(z, "zone", s.id));
  }
  const openings: PlanOpening[] = [];
  for (const o of pkg.openings) {
    const p = o.properties;
    if (p.floor_id !== floorId || !shown(p)) continue;
    const span = p.span && p.span.length === 2 ? ([local(p.span[0]!), local(p.span[1]!)] as [XY, XY]) : undefined;
    const swings = (p.swings ?? []).filter((s) => s.length === 2).map((s) => [local(s[0]!), local(s[1]!)] as [XY, XY]);
    openings.push({ id: o.id, type: p.type, ...(span ? { span } : {}), ...(swings.length ? { swings } : {}) });
  }
  return {
    id: floorId,
    spaces,
    drawing: {
      outline: polygons(floor.geometry),
      walls: polygons(floor.properties.walls),
      parapets: polygons(floor.properties.parapets),
      openings,
      containers,
    },
  };
}
