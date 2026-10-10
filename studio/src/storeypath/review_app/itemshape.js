// An item's symbol on a plan, as the viewers draw it (viewer/svg, the engine's drawItem): its
// footprint in its type's colour, the edge it faces darker, and a mark of how it is drawn (its
// shape, fit.js shapeOf): a desk's chair and what goes with its grade, a meeting table's chairs
// round it, a sofa's seat, the way a screen faces, a copier's lid, a bed's headboard and
// pillows, a kiosk's screen; on the ceiling a circle. In the plan's metres, an item's front its
// own -y. Review's plan (review/items.js) and the Item types page (itemtypes.js) draw it.

import { DESK_SETS, shapeOf, tableChairs, visitorChairs } from "./fit.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const PLAIN = "#8a8a8a"; // a type with no colour of its own

function svg(tag, attrs = {}) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  return e;
}

/** A desk's chair and what goes with its grade, in its own frame: its user towards -y. */
function deskSet(g, t, w, d) {
  const set = DESK_SETS[t?.grade] || DESK_SETS.junior;
  // [x, y] from its middle towards its user, as the viewers measure it
  const rect = (x, y, rw, rh, attrs) => g.append(svg("rect", { x, y: -(y + rh), width: rw, height: rh, ...attrs }));
  const color = t?.color || PLAIN;
  if (set.return) rect(w / 2 - Math.min(0.45, w / 3), d / 2, Math.min(0.45, w / 3), 0.8, { fill: color });
  if (set.cabinet) rect(-w * 0.45, d / 2 + 0.95, w * 0.9, 0.45, { fill: color });
  const chair = (x, y, cw, cd, back) => { // its back at y + cd
    rect(x - cw / 2, y, cw, cd, { class: "chair", rx: 0.08 });
    rect(x - cw / 2, y + cd - back, cw, back, { class: "chair back", rx: 0.04 });
  };
  if (set.executive) chair(0, d / 2 + 0.08, 0.6, 0.62, 0.14);
  else rect(-0.22, d / 2 + 0.1, 0.44, 0.42, { class: "chair", rx: 0.1 });
  const { xs, vw, vd } = visitorChairs(set, w);
  for (const x of xs) { // facing its user: their backs away from it
    rect(x - vw / 2, -d / 2 - 0.15 - vd, vw, vd, { class: "chair", rx: 0.08 });
    rect(x - vw / 2, -d / 2 - 0.15 - vd, vw, 0.12, { class: "chair back", rx: 0.04 });
  }
}

/** A meeting table's chairs round it (tableChairs), their backs away from it. */
function tableSet(g, w, d) {
  const { at, board, cw, cd, gap } = tableChairs(w, d), back = board ? 0.14 : 0.1;
  for (const [x, y, [ox, oy]] of at) {
    // from ``near`` to ``far`` metres out from the edge, ``cw`` along it: the chair, its back
    const part = (near, far, attrs) => {
      const [x0, x1] = ox ? [x + ox * (gap + near), x + ox * (gap + far)].sort((p, q) => p - q) : [x - cw / 2, x + cw / 2];
      const [y0, y1] = oy ? [y + oy * (gap + near), y + oy * (gap + far)].sort((p, q) => p - q) : [y - cw / 2, y + cw / 2];
      g.append(svg("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, ...attrs }));
    };
    part(0, cd, { class: "chair", rx: board ? 0.08 : 0.1 });
    part(cd - back, cd, { class: "chair back", rx: 0.04 });
  }
}

/** The mark of the other shapes, in the item's frame (its front -y, its back +y). */
function markOf(g, shape, w, d) {
  const mark = (tag, attrs, cls = "mark") => g.append(svg(tag, { class: cls, ...attrs }));
  const wedge = (reach) => `M${-w * 0.3},${-d / 2}L0,${-d / 2 - reach}L${w * 0.3},${-d / 2}`; // the way it faces
  if (shape === "sofa") { // its seat, between the arms and before the back
    const arm = Math.min(0.2, w / 6), back = Math.min(0.22, d / 3);
    mark("rect", { x: -w / 2 + arm, y: -d / 2, width: w - 2 * arm, height: d - back });
  } else if (shape === "screen") {
    mark("path", { d: wedge(Math.min(0.5, w * 0.3)) }, "mark view");
  } else if (shape === "copier") { // its lid, over its back
    const m = Math.min(0.08, w / 8, d / 8), h = (d - 2 * m) * 0.6;
    mark("rect", { x: -w / 2 + m, y: d / 2 - m - h, width: w - 2 * m, height: h });
  } else if (shape === "bed") { // its headboard, its pillows against it, where the covers turn down
    const back = d / 2 - Math.min(0.08, d / 20);
    mark("line", { x1: -w / 2, y1: back, x2: w / 2, y2: back });
    const n = w >= 1.3 ? 2 : 1, gap = 0.08, pw = (w - 0.12 - gap * (n - 1)) / n;
    for (let i = 0; i < n; i++) mark("rect", { x: -w / 2 + 0.06 + i * (pw + gap), y: back - 0.46, width: pw, height: 0.4, rx: 0.08 });
    mark("line", { x1: -w / 2, y1: back - 0.62, x2: w / 2, y2: back - 0.62 });
  } else if (shape === "kiosk") { // its screen along its front, and the way it faces
    const t = Math.min(0.08, d / 4), m = Math.min(0.06, w / 8);
    mark("rect", { x: -w / 2 + m, y: -d / 2, width: w - 2 * m, height: t }, "mark screen");
    mark("path", { d: wedge(Math.min(0.5, w * 0.6)) }, "mark view");
  }
}

/** An item's shape on the plan (``a``: {x, y, rotation}, ``t``: its type): its footprint
 * turned with it, the edge it faces darker and the mark of how it is drawn; on the
 * ceiling, a circle (an access point's with a ring and a dot). */
export function assetShape(a, t, cls) {
  const g = svg("g", { transform: `translate(${a.x} ${a.y}) rotate(${a.rotation || 0})`, class: cls });
  const w = t?.width ?? 1, d = t?.depth ?? 0.6, shape = shapeOf(t);
  g.dataset.shape = shape;
  if (t?.mount === "ceiling") {
    const r = Math.max(w, d) / 2;
    g.classList.add("ceiling");
    g.append(svg("circle", { r, fill: t?.color || PLAIN }));
    if (shape === "access_point") {
      g.append(svg("circle", { class: "mark", r: r * 0.6 }), svg("circle", { class: "mark solid", r: r * 0.15 }));
    }
    return g;
  }
  g.append(svg("rect", { x: -w / 2, y: -d / 2, width: w, height: d, fill: t?.color || PLAIN }));
  g.append(svg("line", { x1: -w / 2, y1: -d / 2, x2: w / 2, y2: -d / 2, class: "front" })); // its front: the plan's -y, turned
  if (shape === "desk") deskSet(g, t, w, d);
  else if (shape === "meeting_table") tableSet(g, w, d);
  else markOf(g, shape, w, d);
  return g;
}

/** What an item of type ``t`` takes on the plan, about its middle in its own frame
 * [x0, y0, x1, y1]: its symbol's box (for a preview to frame it whole). */
export function symbolBox(t) {
  const w = t?.width ?? 1, d = t?.depth ?? 0.6;
  if (t?.mount === "ceiling") {
    const r = Math.max(w, d) / 2;
    return [-r, -r, r, r];
  }
  const shape = shapeOf(t);
  if (shape === "desk") {
    const set = DESK_SETS[t?.grade] || DESK_SETS.junior, { xs, vw, vd } = visitorChairs(set, w);
    const user = Math.max(set.executive ? 0.7 : 0.52, set.return ? 0.8 : 0, set.cabinet ? 1.4 : 0);
    const side = Math.max(w / 2, ...xs.map((x) => Math.abs(x) + vw / 2));
    return [-side, -(d / 2 + user), side, d / 2 + (xs.length ? 0.15 + vd : 0)]; // (its user's side -y)
  }
  if (shape === "meeting_table") {
    const { ends, gap, cd } = tableChairs(w, d), out = gap + cd;
    return [-w / 2 - (ends ? out : 0), -d / 2 - out, w / 2 + (ends ? out : 0), d / 2 + out];
  }
  const ahead = shape === "screen" ? Math.min(0.5, w * 0.3) : shape === "kiosk" ? Math.min(0.5, w * 0.6) : 0;
  return [-w / 2, -d / 2 - ahead, w / 2, d / 2];
}
