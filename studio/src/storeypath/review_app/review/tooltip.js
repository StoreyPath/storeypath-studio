// Tooltips: a control's name and its key, shown when the pointer rests on it or the
// keyboard reaches it. A control says them with data-tip (its name; else its aria-label),
// data-key (its keys, as keys.js writes them, space between alternatives) and data-why
// (why it cannot be used now). Disabled controls use aria-disabled, so they still say why.

import { el } from "./dom.js";
import { kbd } from "./keys.js";

let tip = null;
let timer = 0;
let shownFor = null;
const DELAY = 450;
const QUICK = 60; // when another tooltip was shown a moment ago

let lastHidden = 0;

function textOf(target) {
  return target.dataset.tip || target.getAttribute("aria-label") || "";
}

function show(target) {
  const text = textOf(target);
  if (!text || !target.isConnected) return;
  tip ??= document.body.appendChild(el("div", { class: "tooltip", role: "tooltip", id: "tooltip" }));
  const keys = (target.dataset.key || "").split(" ").filter(Boolean);
  const why = target.dataset.why || "";
  tip.replaceChildren(...[
    el("span", { class: "tip-text" }, el("span", {}, text), why ? el("span", { class: "tip-why" }, why) : null),
    keys.length ? el("span", { class: "tip-keys" }, keys.map((k, i) => [i ? el("span", { class: "tip-or" }, "/") : null, kbd(k)])) : null,
  ].filter(Boolean));
  tip.classList.remove("shown");
  const r = target.getBoundingClientRect();
  const t = tip.getBoundingClientRect();
  const side = target.closest(".rail") ? "right" : target.closest(".statusbar, .canvas-corner") ? "top" : "bottom";
  let x, y;
  if (side === "right") {
    x = r.right + 10;
    y = r.top + r.height / 2 - t.height / 2;
  } else if (side === "top") {
    x = r.left + r.width / 2 - t.width / 2;
    y = r.top - t.height - 8;
  } else {
    x = r.left + r.width / 2 - t.width / 2;
    y = r.bottom + 8;
  }
  x = Math.max(6, Math.min(x, innerWidth - t.width - 6));
  y = Math.max(6, Math.min(y, innerHeight - t.height - 6));
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
  tip.classList.add("shown");
  shownFor = target;
  target.setAttribute("aria-describedby", "tooltip");
}

export function hideTooltip() {
  clearTimeout(timer);
  if (shownFor) {
    shownFor.removeAttribute("aria-describedby");
    lastHidden = performance.now();
  }
  shownFor = null;
  tip?.classList.remove("shown");
}

function soon(target) {
  clearTimeout(timer);
  const quick = performance.now() - lastHidden < 500;
  timer = setTimeout(() => show(target), quick ? QUICK : DELAY);
}

const tipped = (node) => node?.closest?.("[data-tip], .rail button, .btn-icon[aria-label], #view-mode button");

export function setupTooltips() {
  document.addEventListener("pointerover", (e) => {
    const t = tipped(e.target);
    if (!t || t === shownFor) return;
    hideTooltip();
    soon(t);
  });
  document.addEventListener("pointerout", (e) => {
    const t = tipped(e.target);
    if (t && !t.contains(e.relatedTarget)) hideTooltip();
  });
  document.addEventListener("focusin", (e) => {
    const t = tipped(e.target);
    if (t && e.target.matches(":focus-visible")) {
      hideTooltip();
      soon(t);
    }
  });
  document.addEventListener("focusout", hideTooltip);
  document.addEventListener("pointerdown", hideTooltip, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTooltip(); }, true);
  window.addEventListener("blur", hideTooltip);
}
