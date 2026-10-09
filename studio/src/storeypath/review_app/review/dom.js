// The page's small helpers: elements by ID, elements and SVG elements made, icons from the
// sprite, and what this browser remembers (a convenience: never needed to work).

export const SVG_NS = "http://www.w3.org/2000/svg";
export const $ = (id) => document.getElementById(id);

/** An element: ``attrs`` set as attributes (left out when undefined, null or false; true:
 * present), ``on…`` functions as listeners, ``class`` as its class; children flattened,
 * null and false left out. */
export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  setAttrs(e, attrs);
  e.append(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

function setAttrs(e, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.setAttribute("class", v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
}

/** An SVG element. */
export function svg(tag, attrs = {}) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  return e;
}

/** An icon of the sprite (icons/lucide.svg): decorative unless ``label`` names it. */
export function icon(name, { size = 16, label = null, cls = "" } = {}) {
  const s = svg("svg", { class: `icon ${cls}`.trim(), width: size, height: size, viewBox: "0 0 24 24",
    ...(label ? { role: "img", "aria-label": label } : { "aria-hidden": "true", focusable: "false" }) });
  s.append(svg("use", { href: `/icons/lucide.svg#${name}` }));
  return s;
}

/** What this browser remembers under ``key`` (null when nothing, or it cannot). */
export function saved(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // not remembered; fine
  }
}
