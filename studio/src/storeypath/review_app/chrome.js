// The frame Studio's pages share (Review has its own, in review/): the top bar's theme switch
// (light or dark, the same setting as Review's), its crumbs (where you are), and tooltips.

import { setupTooltips } from "./review/tooltip.js";

const $ = (id) => document.getElementById(id);

function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // not remembered; fine
  }
}

/** Where you are, in the top bar: ``parts`` [{label, href?}], the last the page itself. */
export function setCrumbs(parts) {
  const nav = $("crumbs");
  if (!nav) return;
  const out = [];
  parts.forEach((p, i) => {
    out.push(Object.assign(document.createElement("span"), { className: "crumb-sep", textContent: "/", ariaHidden: "true" }));
    const last = i === parts.length - 1;
    const node = document.createElement(p.href && !last ? "a" : "span");
    node.className = `crumb${last ? " crumb-here" : ""}`;
    node.textContent = p.label;
    if (p.href && !last) node.href = p.href;
    if (last) node.setAttribute("aria-current", "page");
    out.push(node);
  });
  nav.replaceChildren(...out);
}

/** The frame set up: tooltips, the theme switch. */
export function setupChrome() {
  setupTooltips();
  $("theme-toggle")?.addEventListener("click", () => {
    const light = document.documentElement.dataset.theme !== "light";
    document.documentElement.dataset.theme = light ? "light" : "dark";
    save("storeypath.theme", light ? "light" : "dark");
  });
}
