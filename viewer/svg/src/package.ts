// A floor of a StoreyPath package (spec/FORMAT.md) as the engine's floor model:
// its zones and undivided spaces, and its furniture and equipment, in the
// building's local drawing metres (the drawing the way the architect drew it,
// whatever its bearing on the map).

import { LocalFrame, type Placement } from "./frame.js";
import type { FloorPlan, PlanItem, PlanOpening, PlanSpace, Polygon, XY } from "./types.js";

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
  floor_id: string; type: string; name?: string | null; number?: string | null; drawing_label?: string | null;
  display_point: LonLat; zones?: string[]; hidden?: boolean; ignored?: boolean;
}
interface ZoneProps extends SpaceProps { space_id: string }
interface OpeningProps {
  floor_id: string; type: "door" | "window" | "opening"; span?: LonLat[] | null; swings?: LonLat[][] | null;
  hidden?: boolean; ignored?: boolean;
}
interface ItemProps {
  floor_id: string; building_id: string; type: string; category?: string; name?: string | null;
  mount?: "floor" | "wall" | "ceiling"; display_point: LonLat; heading: number; width_m: number; depth_m: number;
}

/** What a package gives: its manifest and its features (as viewer/src/package.js loads them, or as read). */
export interface PackageLike {
  manifest: { placements?: Record<string, Placement> };
  floors: Feature<FloorProps>[];
  spaces: Feature<SpaceProps>[];
  zones?: Feature<ZoneProps>[];
  openings: Feature<OpeningProps>[];
  /** Furniture and equipment (format 0.6), and the catalogue of their types. */
  items?: Feature<ItemProps>[];
  catalogue?: { types: { code: string; color?: string }[] } | null;
}

export interface FromPackageOptions {
  /** Spaces marked hidden in review: left out unless asked for (ignored ones always are). */
  showHidden?: boolean;
}

const ITEM_COLOR = "#8a8a8a"; // an item whose type the package does not describe

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
    name: f.properties.name ?? null, number: f.properties.number ?? null, label: f.properties.drawing_label ?? null, container,
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
  // furniture and equipment: the way each faces, from north to the drawing's own
  // (its +y is turned to the placement's bearing)
  const colors = new Map((pkg.catalogue?.types ?? []).map((t) => [t.code, t.color]));
  const items: PlanItem[] = [];
  for (const f of pkg.items ?? []) {
    const p = f.properties;
    if (p.floor_id !== floorId || !p.display_point) continue;
    const a = (((p.heading ?? 0) - (placement.bearing || 0)) * Math.PI) / 180;
    items.push({ id: f.id, type: p.type, category: p.category ?? "furniture", name: p.name ?? null, mount: p.mount ?? "floor",
      at: local(p.display_point), front: [Math.sin(a), Math.cos(a)], width: p.width_m || 1, depth: p.depth_m || 0.6,
      color: colors.get(p.type) ?? ITEM_COLOR });
  }
  return {
    id: floorId,
    spaces,
    items,
    drawing: {
      outline: polygons(floor.geometry),
      walls: polygons(floor.properties.walls),
      parapets: polygons(floor.properties.parapets),
      openings,
      containers,
    },
  };
}
