// The Measure tool (M), on the plan: a distance along points clicked, or an area and its
// perimeter, in metres. Points snap to the walls' corners and edges (and square up) as a
// wall's ends do. Nothing is saved: a measure stays shown until another is started, the
// tool is put down or Esc.

import { emit } from "./bus.js";
import { $, el, svg } from "./dom.js";
import { snapWall } from "./drawing.js";
import { onViewChange } from "./plan.js";
import { state } from "./state.js";
import { tool } from "./tools.js";

const m = { mode: "distance", points: [], live: null, done: false };

const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
const metres = (v) => `${v < 10 ? v.toFixed(2) : v.toFixed(1)} m`;

function length(points, closed = false) {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += dist(points[i - 1], points[i]);
  if (closed && points.length > 2) total += dist(points.at(-1), points[0]);
  return total;
}

function area(points) {
  let a = 0;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) a += (points[j][0] + points[i][0]) * (points[j][1] - points[i][1]);
  return Math.abs(a / 2);
}

/** What is measured now: {distance} or {area, perimeter} (with the pointer's point while it is under way). */
export function measured() {
  const pts = m.done || !m.live ? m.points : [...m.points, m.live];
  if (m.mode === "area") return { area: pts.length >= 3 ? area(pts) : 0, perimeter: length(pts, true), points: pts.length };
  return { distance: length(pts), points: pts.length };
}

function summary() {
  const r = measured();
  if (m.mode === "area") return r.points >= 3 ? `${r.area.toFixed(r.area < 100 ? 2 : 1)} m² · perimeter ${metres(r.perimeter)}` : "—";
  return r.points >= 2 ? metres(r.distance) : "—";
}

function closesArea(p) {
  return m.mode === "area" && m.points.length >= 3 && dist(p, m.points[0]) <= 10 / state.view.k;
}

function draw(structural = true) {
  const pts = m.done || !m.live ? m.points : [...m.points, m.live];
  const layer = $("measure");
  const r = 4 / state.view.k;
  const shapes = [];
  if (pts.length >= 2) {
    const d = `M${pts.map(([x, y]) => `${x},${y}`).join("L")}${m.mode === "area" && (m.done || pts.length >= 3) ? "Z" : ""}`;
    shapes.push(svg("path", { d, class: `${m.mode === "area" ? "m-area" : "m-line"}${m.done ? "" : " m-live"}` }));
  }
  for (const [x, y] of m.points) shapes.push(svg("circle", { cx: x, cy: y, r, class: "m-point" }));
  layer.replaceChildren(...shapes);
  placeLabels();
  const result = $("measure-result");
  if (result) result.textContent = summary();
  if (structural) {
    emit("tool-options");
    emit("tool-progress");
  }
}

/** The lengths of the sides, and the total (or the area), written where they are: in the
 * screen's pixels, so they read the same at any zoom. */
function placeLabels() {
  const layer = $("measure-labels");
  if (state.tool !== "measure" && !m.points.length) return layer.replaceChildren();
  const pts = m.done || !m.live ? m.points : [...m.points, m.live];
  const { k, tx, ty } = state.view;
  const screen = ([x, y]) => [x * k + tx, -y * k + ty];
  const tag = (at, text, cls = "") => {
    const [sx, sy] = screen(at);
    const w = text.length * 6.4 + 12;
    const g = svg("g", { class: cls, transform: `translate(${sx} ${sy})` });
    g.append(svg("rect", { x: -w / 2, y: -9, width: w, height: 18, rx: 4 }));
    const t = svg("text", { x: 0, y: 0 });
    t.textContent = text;
    g.append(t);
    return g;
  };
  const tags = [];
  const sides = m.mode === "area" && (m.done || pts.length >= 3) ? [...pts, pts[0]] : pts;
  for (let i = 1; i < sides.length; i++) {
    const a = sides[i - 1], b = sides[i];
    if (dist(a, b) * k < 46) continue; // too short to write on
    tags.push(tag([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], metres(dist(a, b))));
  }
  if (m.mode === "area" && pts.length >= 3) {
    const c = pts.reduce((acc, p) => [acc[0] + p[0] / pts.length, acc[1] + p[1] / pts.length], [0, 0]);
    tags.push(tag(c, `${area(pts).toFixed(1)} m²`, "m-total"));
  } else if (m.mode === "distance" && pts.length >= 3) {
    tags.push(tag(pts.at(-1), `${metres(length(pts))} in all`, "m-total"));
  }
  layer.replaceChildren(...tags);
}

function clear() {
  m.points = [];
  m.live = null;
  m.done = false;
  draw();
}

function finish() {
  if (m.mode === "area" && m.points.length < 3) return;
  if (m.points.length < 2) return;
  m.done = true;
  m.live = null;
  draw();
}

export function setupMeasure() {
  onViewChange(placeLabels);
  tool({
    id: "measure", label: "Measure", icon: "ruler", key: "m", group: "measure", keepsSelection: true,
    words: "distance length area perimeter size",
    wrongView: () => "Measuring is on the plan",
    hint: () => (m.done ? "Click to start another measure · Esc clears it"
      : m.points.length ? `Click the next point · double-click or Enter to finish${m.mode === "area" ? " (or click the first point)" : ""} · Backspace takes a point back`
        : m.mode === "area" ? "Click round the area, point by point (points snap to walls)" : "Click where the distance starts (points snap to walls)"),
    options: () => {
      const modes = el("div", { class: "segmented", role: "radiogroup", "aria-label": "What to measure" },
        ...[["distance", "Distance"], ["area", "Area"]].map(([mode, label]) => {
          const b = el("button", { type: "button", role: "radio", "aria-checked": String(m.mode === mode) }, label);
          b.addEventListener("click", () => {
            m.mode = mode;
            clear();
          });
          return b;
        }));
      const reset = el("button", { type: "button", class: "btn-sm btn-ghost", disabled: !m.points.length }, "Clear");
      reset.addEventListener("click", clear);
      return [modes, el("span", { class: "to-value", "aria-live": "polite", id: "measure-result" }, summary()), reset];
    },
    plan: {
      hover: (p) => {
        if (m.done) return;
        m.live = snapWall(p, m.points.at(-1) || null);
        draw(false);
      },
      click: (p) => {
        if (m.done) {
          m.points = [];
          m.done = false;
        }
        const at = snapWall(p, m.points.at(-1) || null);
        if (closesArea(at)) return finish();
        const last = m.points.at(-1);
        if (last && dist(at, last) < 0.01) return; // a double-click's second click
        m.points.push(at);
        draw();
      },
      dblclick: finish,
    },
    keys: {
      enter: [finish, "finish the measure"],
      backspace: [() => {
        if (m.done) m.done = false;
        m.points.pop();
        draw();
      }, "take back the last point"],
    },
    escape: () => {
      if (!m.points.length) return false;
      clear();
      return true;
    },
    stop: () => {
      m.points = [];
      m.live = null;
      m.done = false;
      $("measure").replaceChildren();
      $("measure-labels").replaceChildren();
    },
  });
}
