// The floor model the engine draws: plain data in a floor's local metres, so a
// system can serve it from its own records (wayfinder serves it from its
// database) or make it from a StoreyPath package (floorFromPackage).

/** A point [x, y] in metres. */
export type XY = [number, number];
/** A closed ring of points. */
export type Ring = XY[];
/** A polygon: its outer ring, then its holes. */
export type Polygon = Ring[];

/** What people use and click: a zone, or a space with no zones. */
export interface PlanSpace {
  /** The space's ID (a StoreyPath ID, or the system's own). */
  id: string;
  kind?: "space" | "zone";
  polygons: Polygon[];
  /** Where its label and a pin go: a point inside it. Found when not given. */
  marker?: XY;
  /** Its type (StoreyPath space type), for its colour when no style says otherwise. */
  type?: string;
  name?: string | null;
  number?: string | null;
  /** The text the drawing writes in it (a room code: RM-GF-33), shown when it has no name or number. */
  label?: string | null;
  /** For a zone: the space it is part of. */
  container?: string | null;
}

export interface PlanOpening {
  id: string;
  type: "door" | "window" | "opening";
  /** Across the wall, jamb to jamb. */
  span?: [XY, XY];
  /** A door's leaves as drawn: each [hinge, free edge when open]. */
  swings?: [XY, XY][];
}

/** What is drawn around the spaces. */
export interface PlanDrawing {
  /** The floor's outline, under everything. */
  outline?: Polygon[];
  /** The walls, full height; and the low walls round terraces and roofs. */
  walls?: Polygon[];
  parapets?: Polygon[];
  openings?: PlanOpening[];
  /** Spaces divided into zones: their outline, drawn over their zones. */
  containers?: { id: string; polygons: Polygon[] }[];
}

/** A piece of furniture or equipment: a desk, a photocopier, an access point… Drawn
 * over the spaces, below the labels, as its footprint with a mark of its kind. */
export interface PlanItem {
  /** The item's ID (a StoreyPath item ID, or the system's own). */
  id: string;
  /** Its type's code (DESK-MANAGER, COPIER, ACCESS-POINT, …): its first part says how
   * it is drawn (DESK, SOFA, TV, COPIER, ACCESS); others by how they are mounted. */
  type?: string;
  /** furniture, equipment or appliance. */
  category?: string;
  name?: string | null;
  /** floor, wall or ceiling (drawn dashed, as overhead). */
  mount?: "floor" | "wall" | "ceiling";
  /** Its middle. */
  at: XY;
  /** The way its front faces (where a desk's user sits), as a direction in the plan's
   * coordinates: [0, -1] is towards -y. */
  front: XY;
  /** Metres along its front, and front to back. */
  width: number;
  depth: number;
  /** Its colour (its type's), a CSS colour. */
  color?: string;
}

export interface FloorPlan {
  id?: string;
  spaces: PlanSpace[];
  /** Furniture and equipment on the floor. */
  items?: PlanItem[];
  drawing?: PlanDrawing;
  /** True when y grows downwards (as on a page); false (the default) for y up. */
  yDown?: boolean;
}

/** How a space looks: a class to style it with CSS, or colours, or inline CSS. */
export interface SpaceStyle {
  className?: string;
  fill?: string;
  stroke?: string;
  opacity?: number;
  /** Inline CSS declarations ("fill: var(--taken); stroke-dasharray: 5 4"), over the rest. */
  style?: string;
}

/** The view: screen = (x·k + tx, ±y·k + ty), in CSS pixels. */
export interface Camera {
  k: number;
  tx: number;
  ty: number;
}
