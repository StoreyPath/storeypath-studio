// The floor plan in plain SVG: no WebGL, no dependencies, for any machine (VDI
// desktops and kiosks without a GPU). One floor at a time: its spaces and zones,
// walls, doors with their swings, windows and openings, its furniture and
// equipment, with labels that stay upright and readable at any zoom, in any
// language and direction.
//
// The engine draws what it is given (setFloor: the floor model in types.ts) and
// leaves meaning to its host: the host says how each space looks (styleOf), what
// it is called (label), which spaces can be chosen (interactive) and what to do
// when one is (the "select" event). Hooks for tests: the root carries data-cam
// (k,tx,ty), each space data-sp-id (and data-selected when chosen), each item
// data-sp-item, the pin data-sp-pin with data-plan-x/y.

import { TYPE_COLORS } from "./colors.js";
import { boundsOf, finite, inside, pathOf, poleOf, round, type Box } from "./geometry.js";
import type { Camera, FloorPlan, PlanItem, PlanOpening, PlanSpace, SpaceStyle, XY } from "./types.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const DRAG_PX = 4; // a press that moves less than this is a click
const ANIMATION_MS = 260;

export interface EngineOptions {
  /** A space's label, as lines (default: its name, then its number; else the text its drawing writes in it). */
  label?: (space: PlanSpace) => string | string[] | null;
  /** What a screen reader says for a space that can be chosen (default: its label and type). */
  ariaLabel?: (space: PlanSpace) => string | null;
  /** How a space looks (default: its type's colour). */
  styleOf?: (space: PlanSpace) => SpaceStyle | null;
  /** Which spaces can be clicked or chosen with the keyboard (default: all). */
  interactive?: (space: PlanSpace) => boolean;
  /** A colour per space type, over the defaults. */
  colors?: Record<string, string>;
  /** Animate pans and zooms (default: unless the system asks for reduced motion). */
  motion?: boolean;
  /** Pixels left round the floor when it is fitted. */
  padding?: number;
  /** The closest zoom, in pixels per metre. */
  maxScale?: number;
  /** Show labels. */
  labels?: boolean;
  /** Show the furniture and equipment (default: true). */
  items?: boolean;
  /** Whether items can be clicked or chosen with the keyboard (default: true); when
   * not, a click on one chooses the space under it. */
  interactiveItems?: boolean;
  /** Label size in pixels (set the font with CSS: .sp-labels). */
  labelSize?: number;
  /** What the plan is called, for screen readers. */
  title?: string;
}

export interface SelectDetail {
  id: string | null;
  /** The space or zone chosen, or the item: one of them, or neither. */
  space: PlanSpace | null;
  item: PlanItem | null;
}

/** How an item is drawn, by its type code's first part (DESK-MANAGER is a desk);
 * others by how they are mounted. */
const ITEM_KINDS: Record<string, string> = { DESK: "desk", SOFA: "sofa", TV: "tv", SCREEN: "tv", COPIER: "copier",
  PRINTER: "copier", ACCESS: "ap" };
const itemKind = (it: PlanItem): string =>
  ITEM_KINDS[(it.type ?? "").split("-")[0]!] ?? (it.mount === "ceiling" ? "round" : "plain");
const fine = (v: number): number => Math.round(v * 1e4) / 1e4;

const svg = <K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}): SVGElementTagNameMap[K] => {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
};

export class FloorPlanEngine extends EventTarget {
  readonly element: HTMLElement;
  readonly svg: SVGSVGElement;
  private opts: EngineOptions;
  private colors: Record<string, string>;
  private plan: FloorPlan | null = null;
  private readonly world: SVGGElement;
  private readonly layers: Record<"outline" | "units" | "containers" | "items" | "selection" | "walls" | "openings", SVGGElement>;
  private readonly labelLayer: SVGGElement;
  private readonly pinLayer: SVGGElement;
  private paths = new Map<string, SVGPathElement>();
  private labels = new Map<string, { text: SVGTextElement; width: number; height: number }>();
  private measured = false;
  private spaces = new Map<string, PlanSpace>();
  private items = new Map<string, { item: PlanItem; shape: SVGGElement; outline: string }>();
  private boxes = new Map<string, Box>();
  private markers = new Map<string, XY>();
  private floorBox: Box = [0, 0, 1, 1];
  private cam: Camera = { k: 1, tx: 0, ty: 0 };
  private ySign = -1;
  private minScale = 0.01;
  private chosen: string | null = null;
  private lit: Set<string> | null = null;
  private dim = true;
  private pinned: string | null = null;
  private frame = 0;
  private fitted = false;
  private size = { w: 0, h: 0 };
  private readonly observer: ResizeObserver;
  private pointers = new Map<number, XY>();
  private press: { start: XY; cam: Camera; moved: boolean; target: EventTarget | null } | null = null;
  private pinch: { distance: number; mid: XY; cam: Camera } | null = null;

  constructor(container: HTMLElement | string, options: EngineOptions = {}) {
    super();
    const element = typeof container === "string" ? document.querySelector<HTMLElement>(container) : container;
    if (!element) throw new Error(`FloorPlanEngine: container ${String(container)} not found`);
    this.element = element;
    this.opts = { padding: 24, maxScale: 400, labels: true, labelSize: 12, items: true, interactiveItems: true, ...options };
    this.colors = { ...TYPE_COLORS, ...options.colors };
    this.svg = svg("svg", { class: "sp-plan", role: "group", tabindex: 0, "aria-label": options.title ?? "Floor plan" });
    this.world = svg("g", { class: "sp-world" });
    this.layers = {
      outline: svg("g", { class: "sp-outline" }),
      units: svg("g", { class: "sp-units" }),
      containers: svg("g", { class: "sp-containers" }),
      items: svg("g", { class: "sp-items" }), // over the spaces, under the walls and the labels
      selection: svg("g", { class: "sp-selection" }),
      walls: svg("g", { class: "sp-walls" }),
      openings: svg("g", { class: "sp-openings" }),
    };
    this.world.append(this.layers.outline, this.layers.units, this.layers.containers, this.layers.items, this.layers.selection,
      this.layers.walls, this.layers.openings);
    this.labelLayer = svg("g", { class: "sp-labels", "aria-hidden": "true" });
    this.pinLayer = svg("g", { class: "sp-pin-layer" });
    this.svg.append(this.world, this.labelLayer, this.pinLayer);
    element.append(this.svg);
    this.bind();
    this.observer = new ResizeObserver(() => this.resized());
    this.observer.observe(this.svg);
  }

  // ---- what is shown ----------------------------------------------------------

  /** Draw a floor; fitted unless `fit` is false (then the view stays). */
  setFloor(plan: FloorPlan, { fit = true }: { fit?: boolean } = {}): void {
    this.plan = plan;
    this.ySign = plan.yDown ? 1 : -1;
    if (!this.size.w) {
      const r = this.svg.getBoundingClientRect();
      if (r.width && r.height) this.size = { w: r.width, h: r.height };
    }
    for (const layer of Object.values(this.layers)) layer.replaceChildren();
    this.labelLayer.replaceChildren();
    this.paths.clear();
    this.labels.clear();
    this.spaces.clear();
    this.items.clear();
    this.boxes.clear();
    this.markers.clear();
    const d = plan.drawing ?? {};
    const box = boundsOf(d.outline ?? []);
    if (d.outline?.length) this.layers.outline.append(svg("path", { d: pathOf(d.outline) }));
    for (const s of plan.spaces) {
      if (!s.polygons.length) continue;
      this.spaces.set(s.id, s);
      const b = boundsOf(s.polygons);
      boundsOf(s.polygons, box);
      this.boxes.set(s.id, b);
      this.markers.set(s.id, s.marker && inside(s.polygons, s.marker) ? s.marker : poleOf(s.polygons, Math.max(0.02, (b[2] - b[0]) / 200)));
      const path = svg("path", { d: pathOf(s.polygons), "data-sp-id": s.id, "data-sp-kind": s.kind ?? "space" });
      if (this.interactive(s)) {
        path.setAttribute("tabindex", "0");
        path.setAttribute("role", "button");
        const said = this.opts.ariaLabel ? this.opts.ariaLabel(s) : this.lines(s).join(" ") || s.type || s.id;
        if (said) path.setAttribute("aria-label", said);
      }
      this.layers.units.append(path);
      this.paths.set(s.id, path);
      const lines = this.lines(s);
      if (lines.length) this.labels.set(s.id, this.makeLabel(lines));
    }
    this.measureLabels();
    for (const c of d.containers ?? []) this.layers.containers.append(svg("path", { d: pathOf(c.polygons), "data-sp-container": c.id }));
    if (d.walls?.length) this.layers.walls.append(svg("path", { class: "sp-wall", d: pathOf(d.walls) }));
    if (d.parapets?.length) this.layers.walls.append(svg("path", { class: "sp-parapet", d: pathOf(d.parapets) }));
    for (const o of d.openings ?? []) this.layers.openings.append(...this.drawOpening(o));
    for (const it of plan.items ?? []) this.drawItem(it);
    this.showItems();
    this.floorBox = finite(box) ? box : [0, 0, 1, 1];
    if (this.chosen && !this.spaces.has(this.chosen) && !this.items.has(this.chosen)) this.chosen = null;
    if (this.pinned && !this.spaces.has(this.pinned)) this.pinned = null;
    this.restyle();
    if (fit || !this.fitted) this.fit({ animate: false });
    else this.apply();
  }

  /** Change options (a new styleOf, label, colours…) and draw again what they change. */
  setOptions(options: EngineOptions): void {
    this.opts = { ...this.opts, ...options };
    if (options.colors) this.colors = { ...TYPE_COLORS, ...options.colors };
    if (this.plan && (options.label || options.ariaLabel || options.interactive || options.labelSize
      || options.interactiveItems !== undefined)) {
      this.setFloor(this.plan, { fit: false });
    } else {
      this.showItems();
      this.restyle();
      this.apply();
    }
  }

  /** Show the furniture and equipment, or not (a chosen item is let go). */
  setItems(on: boolean): void {
    this.opts.items = on;
    this.showItems();
  }

  get itemsShown(): boolean {
    return this.opts.items !== false;
  }

  /** Apply styleOf again (the host's data changed: blocks, occupants…). */
  restyle(): void {
    for (const [id, path] of this.paths) {
      const s = this.spaces.get(id)!;
      const style: SpaceStyle = this.opts.styleOf?.(s) ?? {};
      const classes = ["sp-unit", `sp-type-${s.type ?? "unspecified"}`];
      if (s.kind === "zone") classes.push("sp-zone");
      if (style.className) classes.push(style.className);
      if (this.lit) classes.push(this.lit.has(id) ? "sp-highlight" : this.dim ? "sp-dim" : "");
      if (id === this.chosen) classes.push("sp-selected");
      path.setAttribute("class", classes.filter(Boolean).join(" "));
      path.style.cssText = "";
      path.style.fill = style.fill ?? (style.className ? "" : this.colors[s.type ?? "unspecified"] ?? this.colors.unspecified ?? "");
      if (style.stroke) path.style.stroke = style.stroke;
      if (style.opacity !== undefined) path.style.opacity = String(style.opacity);
      if (style.style) path.style.cssText += `;${style.style}`;
      if (id === this.chosen) path.setAttribute("data-selected", "true");
      else path.removeAttribute("data-selected");
    }
    for (const [id, { shape }] of this.items) {
      shape.classList.toggle("sp-selected", id === this.chosen);
      if (id === this.chosen) shape.setAttribute("data-selected", "true");
      else shape.removeAttribute("data-selected");
    }
    // the chosen space's (or item's) outline over its neighbours' (a copy: moving the
    // space itself would take the keyboard focus off it)
    const d = this.chosen ? this.paths.get(this.chosen)?.getAttribute("d") ?? this.items.get(this.chosen)?.outline : undefined;
    this.layers.selection.replaceChildren(...(d ? [svg("path", { d })] : []));
  }

  // ---- choosing --------------------------------------------------------------

  get selected(): string | null {
    return this.chosen;
  }

  /** Choose a space or an item (or none). `focus`: bring it into view, panning only, or zooming to it. */
  select(id: string | null, { focus = false }: { focus?: false | "pan" | "zoom" } = {}): boolean {
    if (id !== null && !this.spaces.has(id) && !(this.items.has(id) && this.itemsShown)) return false;
    this.chosen = id;
    this.restyle();
    if (id && focus) this.focus(id, { zoom: focus === "zoom" });
    return true;
  }

  /** Bring out some spaces (and dim the rest), or none. */
  highlight(ids: Iterable<string> | null, { dim = true }: { dim?: boolean } = {}): void {
    this.lit = ids ? new Set(ids) : null;
    this.dim = dim;
    this.restyle();
  }

  /** A pin in a space (inside it, wherever its shape bends), or none. */
  setPin(id: string | null): void {
    this.pinned = id && this.spaces.has(id) ? id : null;
    this.drawPin();
  }

  /** Where a space's label and pin go: a point inside it (drawing metres). */
  markerOf(id: string): XY | null {
    return this.markers.get(id) ?? null;
  }

  // ---- the view --------------------------------------------------------------

  /** The view: screen = (x·k + tx, ±y·k + ty) in CSS pixels. */
  camera(): Camera {
    return { ...this.cam };
  }

  setCamera(cam: Camera, { animate = false }: { animate?: boolean } = {}): void {
    this.moveTo(cam, animate);
  }

  /** Where a point of the plan is on the screen (CSS pixels from the plan's corner). */
  toScreen(p: XY): XY {
    return [p[0] * this.cam.k + this.cam.tx, this.ySign * p[1] * this.cam.k + this.cam.ty];
  }

  toPlan(s: XY): XY {
    return [(s[0] - this.cam.tx) / this.cam.k, (s[1] - this.cam.ty) / (this.ySign * this.cam.k)];
  }

  /** The whole floor in view. */
  fit({ animate = true }: { animate?: boolean } = {}): void {
    this.fitBox(this.floorBox, animate, true);
  }

  /** Some spaces in view. */
  fitTo(ids: Iterable<string>, { animate = true }: { animate?: boolean } = {}): void {
    const box: Box = [Infinity, Infinity, -Infinity, -Infinity];
    for (const id of ids) {
      const b = this.boxes.get(id);
      if (!b) continue;
      box[0] = Math.min(box[0], b[0]); box[1] = Math.min(box[1], b[1]);
      box[2] = Math.max(box[2], b[2]); box[3] = Math.max(box[3], b[3]);
    }
    if (finite(box)) this.fitBox(box, animate, false);
  }

  /** A space in view: its marker in the middle, at the same zoom; or zoomed to it. */
  focus(id: string, { zoom = false, animate = true }: { zoom?: boolean; animate?: boolean } = {}): void {
    if (zoom) return this.fitTo([id], { animate });
    const m = this.markers.get(id);
    if (!m) return;
    const { k } = this.cam;
    this.moveTo({ k, tx: this.size.w / 2 - k * m[0], ty: this.size.h / 2 - this.ySign * k * m[1] }, animate);
  }

  /** Zoom by a factor, keeping a screen point still (the middle unless given). */
  zoomBy(factor: number, at?: XY, { animate = false }: { animate?: boolean } = {}): void {
    const point: XY = at ?? [this.size.w / 2, this.size.h / 2];
    const k = this.clampScale(this.cam.k * factor);
    const p = this.toPlan(point);
    this.moveTo({ k, tx: point[0] - k * p[0], ty: point[1] - this.ySign * k * p[1] }, animate);
  }

  destroy(): void {
    cancelAnimationFrame(this.frame);
    this.observer.disconnect();
    this.svg.remove();
  }

  // ---- inside ----------------------------------------------------------------

  private interactive(s: PlanSpace): boolean {
    return this.opts.interactive ? this.opts.interactive(s) : true;
  }

  private lines(s: PlanSpace): string[] {
    if (this.opts.label) {
      const l = this.opts.label(s);
      return l === null ? [] : (Array.isArray(l) ? l : [l]).filter((x) => x && x.trim());
    }
    const name = s.name?.trim() ?? "";
    const number = s.number?.trim() ?? "";
    if (!name && !number) return s.label?.trim() ? [s.label.trim()] : [];
    return [name, number && number !== name ? number : ""].filter(Boolean);
  }

  private makeLabel(lines: string[]): { text: SVGTextElement; width: number; height: number } {
    const size = this.opts.labelSize ?? 12;
    const text = svg("text", { "text-anchor": "middle", "font-size": size });
    lines.forEach((line, i) => {
      const t = svg("tspan", { x: 0, dy: i === 0 ? `${(-(lines.length - 1) / 2) * 1.15 + 0.35}em` : "1.15em" });
      if (i > 0) t.setAttribute("class", "sp-label-sub");
      t.textContent = line;
      text.append(t);
    });
    this.labelLayer.append(text);
    // a first guess, until measured: the label shows only where its space is big enough
    const width = Math.max(...lines.map((l) => l.length)) * size * 0.62;
    return { text, width, height: lines.length * size * 1.2 };
  }

  /** The labels' real sizes, as the browser lays them out (once the plan is shown). */
  private measureLabels(): void {
    this.measured = false;
    if (!this.svg.isConnected) return;
    for (const label of this.labels.values()) {
      try {
        const box = label.text.getBBox();
        if (box.width > 0) {
          label.width = box.width + 4; // the halo
          label.height = box.height + 2;
          this.measured = true;
        }
      } catch {
        // not laid out (the plan is not shown): keep the guess
      }
    }
  }

  private drawOpening(o: PlanOpening): SVGElement[] {
    const out: SVGElement[] = [];
    const cls = o.type === "window" ? "sp-window" : o.type === "opening" ? "sp-way" : "sp-door";
    if (o.type === "door" && o.span && o.swings?.length) {
      for (const [h, q] of o.swings) {
        const from = (p: XY): number => Math.hypot(p[0] - h[0], p[1] - h[1]);
        const jamb = from(o.span[0]) > from(o.span[1]) ? o.span[0] : o.span[1]; // the other side
        const r = from(q);
        const k = r / (from(jamb) || 1);
        const shut: XY = [h[0] + (jamb[0] - h[0]) * k, h[1] + (jamb[1] - h[1]) * k];
        const sweep = (q[0] - h[0]) * (shut[1] - h[1]) - (q[1] - h[1]) * (shut[0] - h[0]) > 0 ? 1 : 0;
        out.push(svg("path", {
          class: "sp-door sp-swing", "data-sp-opening": o.id,
          d: `M${round(h[0])},${round(h[1])}L${round(q[0])},${round(q[1])}A${round(r)},${round(r)} 0 0 ${sweep} ${round(shut[0])},${round(shut[1])}`,
        }));
      }
    } else if (o.span) {
      out.push(svg("line", { class: cls, "data-sp-opening": o.id, x1: round(o.span[0][0]), y1: round(o.span[0][1]),
        x2: round(o.span[1][0]), y2: round(o.span[1][1]) }));
    }
    return out;
  }

  /** An item: its footprint in its colour, its front edge darker, and a mark of its
   * kind, drawn in its own frame (x along its width, y towards its front); round
   * ones (on the ceiling) a circle, upright on the screen whatever the plan's y. */
  private drawItem(it: PlanItem): void {
    const len = Math.hypot(it.front[0], it.front[1]) || 1;
    const f: XY = [it.front[0] / len, it.front[1] / len], u: XY = [-f[1], f[0]];
    const w = it.width, d = it.depth, kind = itemKind(it);
    const circle = kind === "ap" || kind === "round";
    const classes = ["sp-item", `sp-item-${kind}`, it.mount === "ceiling" && "sp-item-overhead",
      this.opts.interactiveItems === false && "sp-item-passive"];
    const g = svg("g", { class: classes.filter(Boolean).join(" "), "data-sp-item": it.id });
    const mark = (tag: "path" | "rect" | "line" | "circle", attrs: Record<string, string | number>, cls = "sp-item-mark") =>
      g.append(svg(tag, { class: cls, ...attrs }));
    let ring: XY[];
    if (circle) {
      const r = Math.max(Math.min(w, d) / 2, 0.3); // big enough to see
      g.setAttribute("transform", `translate(${round(it.at[0])},${round(it.at[1])}) scale(1,${this.ySign})`);
      mark("circle", { r: round(r), style: `fill:${it.color ?? ""}` }, "sp-item-body");
      if (kind === "ap") { // a dot and two arcs over it: wifi
        const at = 0.35 * r;
        mark("circle", { cy: round(at), r: round(0.09 * r) }, "sp-item-dot");
        for (const q of [0.3 * r, 0.58 * r]) {
          const s = q * Math.SQRT1_2;
          mark("path", { d: `M${round(-s)},${round(at - s)}A${round(q)},${round(q)} 0 0 1 ${round(s)},${round(at - s)}` });
        }
      }
      ring = Array.from({ length: 24 }, (_, i): XY => [it.at[0] + r * Math.cos((i * Math.PI) / 12), it.at[1] + r * Math.sin((i * Math.PI) / 12)]);
    } else {
      g.setAttribute("transform", `matrix(${fine(u[0])} ${fine(u[1])} ${fine(f[0])} ${fine(f[1])} ${round(it.at[0])} ${round(it.at[1])})`);
      mark("rect", { x: round(-w / 2), y: round(-d / 2), width: round(w), height: round(d), style: `fill:${it.color ?? ""}` },
        "sp-item-body");
      if (kind === "desk") { // its chair, before it
        mark("rect", { x: -0.22, y: round(d / 2 + 0.1), width: 0.44, height: 0.42, rx: 0.1 }, "sp-item-chair");
      } else if (kind === "sofa") { // its seat, between the arms and before the back
        const arm = Math.min(0.2, w / 6), back = Math.min(0.22, d / 3);
        mark("rect", { x: round(-w / 2 + arm), y: round(-d / 2 + back), width: round(w - 2 * arm), height: round(d - back) });
      } else if (kind === "tv") { // what it faces
        mark("path", { d: `M${round(-w * 0.3)},${round(d / 2)}L0,${round(d / 2 + Math.min(0.5, w * 0.3))}L${round(w * 0.3)},${round(d / 2)}` },
          "sp-item-mark sp-item-view");
      } else if (kind === "copier") { // its lid
        const m = Math.min(0.08, w / 8, d / 8);
        mark("rect", { x: round(-w / 2 + m), y: round(-d / 2 + m), width: round(w - 2 * m), height: round((d - 2 * m) * 0.6) });
      }
      mark("line", { x1: round(-w / 2), y1: round(d / 2), x2: round(w / 2), y2: round(d / 2) }, "sp-item-front");
      ring = ([[-1, -1], [1, -1], [1, 1], [-1, 1]] as const).map(([a, b]): XY => [
        it.at[0] + (a * w * u[0] + b * d * f[0]) / 2, it.at[1] + (a * w * u[1] + b * d * f[1]) / 2]);
    }
    const said = [it.name, it.type].find((x) => x) ?? it.id;
    if (this.opts.interactiveItems !== false) {
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.setAttribute("aria-label", said);
    }
    const title = svg("title");
    title.textContent = said;
    g.prepend(title);
    this.layers.items.append(g);
    this.items.set(it.id, { item: it, shape: g, outline: pathOf([[[...ring, ring[0]!]]]) });
    this.markers.set(it.id, it.at);
    this.boxes.set(it.id, boundsOf([[ring]]));
  }

  /** The items' layer shown or not; a chosen item hidden is let go. */
  private showItems(): void {
    this.layers.items.style.display = this.itemsShown ? "" : "none";
    if (!this.itemsShown && this.chosen && this.items.has(this.chosen)) {
      this.chosen = null;
      this.restyle();
    }
  }

  private drawPin(): void {
    this.pinLayer.replaceChildren();
    const id = this.pinned;
    const m = id ? this.markers.get(id) : null;
    if (!id || !m) return;
    const [sx, sy] = this.toScreen(m);
    const pin = svg("g", { class: "sp-pin", "data-sp-pin": id, "data-plan-x": round(m[0]), "data-plan-y": round(m[1]),
      transform: `translate(${round(sx)},${round(sy)})` });
    pin.append(svg("path", { d: "M0,0C-3,-7 -11,-12 -11,-20A11,11 0 1 1 11,-20C11,-12 3,-7 0,0Z" }), svg("circle", { cx: 0, cy: -20, r: 4.2 }));
    this.pinLayer.append(pin);
  }

  private fitBox(box: Box, animate: boolean, whole: boolean): void {
    const { w, h } = this.size;
    if (!w || !h) return;
    const pad = this.opts.padding ?? 24;
    const bw = Math.max(box[2] - box[0], 1e-6), bh = Math.max(box[3] - box[1], 1e-6);
    let k = Math.min((w - 2 * pad) / bw, (h - 2 * pad) / bh);
    if (!Number.isFinite(k) || k <= 0) k = 1;
    if (whole) this.minScale = k / 4;
    k = this.clampScale(k);
    const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
    this.fitted = true;
    this.moveTo({ k, tx: w / 2 - k * cx, ty: h / 2 - this.ySign * k * cy }, animate);
  }

  private clampScale(k: number): number {
    return Math.min(Math.max(k, this.minScale), this.opts.maxScale ?? 400);
  }

  private moveTo(target: Camera, animate: boolean): void {
    cancelAnimationFrame(this.frame);
    const motion = this.opts.motion ?? !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!animate || !motion) {
      this.cam = { ...target };
      this.apply();
      return;
    }
    // zoom in a straight line in log scale, keeping the screen point that moves to
    // the middle on a straight path
    const from = { ...this.cam };
    const start = performance.now();
    const step = (now: number): void => {
      const t = Math.min(1, (now - start) / ANIMATION_MS);
      const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      const k = Math.exp(Math.log(from.k) + (Math.log(target.k) - Math.log(from.k)) * e);
      const cFrom = [(this.size.w / 2 - from.tx) / from.k, (this.size.h / 2 - from.ty) / from.k];
      const cTo = [(this.size.w / 2 - target.tx) / target.k, (this.size.h / 2 - target.ty) / target.k];
      const cx = cFrom[0]! + (cTo[0]! - cFrom[0]!) * e, cy = cFrom[1]! + (cTo[1]! - cFrom[1]!) * e;
      this.cam = t < 1 ? { k, tx: this.size.w / 2 - k * cx, ty: this.size.h / 2 - k * cy } : { ...target };
      this.apply();
      if (t < 1) this.frame = requestAnimationFrame(step);
    };
    this.frame = requestAnimationFrame(step);
  }

  private apply(): void {
    const { k, tx, ty } = this.cam;
    this.world.setAttribute("transform", `matrix(${k} 0 0 ${this.ySign * k} ${tx} ${ty})`);
    this.svg.setAttribute("data-cam", `${round(k)},${round(tx)},${round(ty)}`);
    this.placeLabels();
    this.drawPin();
    this.dispatchEvent(new CustomEvent<Camera>("camerachange", { detail: this.camera() }));
  }

  private placeLabels(): void {
    const show = this.opts.labels !== false;
    for (const [id, label] of this.labels) {
      const b = this.boxes.get(id)!;
      const m = this.markers.get(id)!;
      const fits = show && (b[2] - b[0]) * this.cam.k >= label.width * 1.05 && (b[3] - b[1]) * this.cam.k >= label.height * 1.1;
      if (!fits) {
        label.text.setAttribute("visibility", "hidden");
        continue;
      }
      const [sx, sy] = this.toScreen(m);
      label.text.setAttribute("visibility", "visible");
      label.text.setAttribute("transform", `translate(${round(sx)},${round(sy)})`);
    }
  }

  private resized(): void {
    const r = this.svg.getBoundingClientRect();
    const w = r.width, h = r.height;
    if (!w || !h) return;
    const before = this.size;
    this.size = { w, h };
    if (!this.plan) return;
    if (!this.measured) this.measureLabels(); // drawn while hidden
    if (!this.fitted || !before.w) return this.fit({ animate: false });
    // the point in the middle stays in the middle: nothing drifts
    const c = this.toPlan([before.w / 2, before.h / 2]);
    const { k } = this.cam;
    this.cam = { k, tx: w / 2 - k * c[0], ty: h / 2 - this.ySign * k * c[1] };
    this.apply();
  }

  private local(e: { clientX: number; clientY: number }): XY {
    const r = this.svg.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  private chooseFrom(target: EventTarget | null): void {
    const el = target instanceof Element ? target.closest("[data-sp-id], [data-sp-item]") : null;
    const item = this.items.get(el?.getAttribute("data-sp-item") ?? "")?.item ?? null;
    if (item) {
      this.select(item.id);
      this.dispatchEvent(new CustomEvent<SelectDetail>("select", { detail: { id: item.id, space: null, item } }));
      return;
    }
    const id = el?.getAttribute("data-sp-id") ?? null;
    const space = id ? this.spaces.get(id) ?? null : null;
    if (space && !this.interactive(space)) return;
    this.select(space ? space.id : null);
    this.dispatchEvent(new CustomEvent<SelectDetail>("select", { detail: { id: space?.id ?? null, space, item: null } }));
  }

  private bind(): void {
    const s = this.svg;
    s.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 && e.pointerType === "mouse") return;
      this.pointers.set(e.pointerId, this.local(e));
      try {
        s.setPointerCapture(e.pointerId);
      } catch {
        // a pointer the browser does not track (made by a script): nothing to capture
      }
      if (this.pointers.size === 1) {
        this.press = { start: this.local(e), cam: this.camera(), moved: false, target: e.target };
      } else if (this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()] as [XY, XY];
        this.pinch = { distance: Math.hypot(a[0] - b[0], a[1] - b[1]), mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], cam: this.camera() };
        if (this.press) this.press.moved = true;
      }
    });
    s.addEventListener("pointermove", (e) => {
      if (!this.pointers.has(e.pointerId)) return;
      const p = this.local(e);
      this.pointers.set(e.pointerId, p);
      if (this.pinch && this.pointers.size >= 2) {
        const [a, b] = [...this.pointers.values()] as [XY, XY];
        const distance = Math.hypot(a[0] - b[0], a[1] - b[1]);
        const mid: XY = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        const c = this.pinch.cam;
        const k = this.clampScale(c.k * (distance / (this.pinch.distance || 1)));
        const at: XY = [(this.pinch.mid[0] - c.tx) / c.k, (this.pinch.mid[1] - c.ty) / (this.ySign * c.k)];
        this.moveTo({ k, tx: mid[0] - k * at[0], ty: mid[1] - this.ySign * k * at[1] }, false);
        return;
      }
      const press = this.press;
      if (!press) return;
      const dx = p[0] - press.start[0], dy = p[1] - press.start[1];
      if (!press.moved && Math.hypot(dx, dy) < DRAG_PX) return;
      press.moved = true;
      s.classList.add("sp-dragging");
      this.moveTo({ k: press.cam.k, tx: press.cam.tx + dx, ty: press.cam.ty + dy }, false);
    });
    const end = (e: PointerEvent): void => {
      if (!this.pointers.delete(e.pointerId)) return;
      if (this.pointers.size < 2) this.pinch = null;
      if (this.pointers.size > 0) return;
      const press = this.press;
      this.press = null;
      s.classList.remove("sp-dragging");
      if (press && !press.moved && e.type === "pointerup") this.chooseFrom(press.target);
    };
    s.addEventListener("pointerup", end);
    s.addEventListener("pointercancel", end);
    s.addEventListener("wheel", (e) => {
      e.preventDefault();
      const speed = e.deltaMode === 1 ? 0.05 : 0.0018;
      this.zoomBy(Math.exp(-e.deltaY * speed), this.local(e));
    }, { passive: false });
    s.addEventListener("keydown", (e) => {
      const onSpace = e.target instanceof Element && (e.target.hasAttribute("data-sp-id") || e.target.hasAttribute("data-sp-item"));
      if (onSpace && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        this.chooseFrom(e.target);
        return;
      }
      const pan = 40;
      const moves: Record<string, () => void> = {
        "+": () => this.zoomBy(1.25), "=": () => this.zoomBy(1.25), "-": () => this.zoomBy(0.8),
        "0": () => this.fit(), Home: () => this.fit(),
        ArrowLeft: () => this.moveTo({ ...this.cam, tx: this.cam.tx + pan }, false),
        ArrowRight: () => this.moveTo({ ...this.cam, tx: this.cam.tx - pan }, false),
        ArrowUp: () => this.moveTo({ ...this.cam, ty: this.cam.ty + pan }, false),
        ArrowDown: () => this.moveTo({ ...this.cam, ty: this.cam.ty - pan }, false),
      };
      const move = moves[e.key];
      if (move) {
        e.preventDefault();
        move();
      }
    });
  }
}
