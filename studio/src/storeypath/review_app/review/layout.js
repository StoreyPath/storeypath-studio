// The panels round the canvas: the navigator (left) and the inspector (right), each hidden
// and shown again ([ and ], \ both), and made wider or narrower by dragging its edge (or
// the arrows on it); remembered in this browser. On a narrow window the navigator starts
// hidden, so the canvas keeps its room.

import { emit } from "./bus.js";
import { $, save, saved } from "./dom.js";

const LIMITS = { navigator: [220, 460], inspector: [272, 520] };
const VAR = { navigator: "--nav-w", inspector: "--insp-w" };
const HIDDEN = { navigator: "nav-hidden", inspector: "insp-hidden" };
const REVEAL = { navigator: "show-navigator", inspector: "show-inspector" };

export const panelShown = (which) => !document.body.classList.contains(HIDDEN[which]);

/** A panel shown (true) or hidden (false); toggled when ``on`` is left out. */
export function showPanel(which, on = !panelShown(which), { remember = true } = {}) {
  document.body.classList.toggle(HIDDEN[which], !on);
  $(REVEAL[which]).hidden = on;
  if (remember) save(`storeypath.review.${which}`, on ? "1" : "0");
  emit("layout");
  // the keys follow: into the panel shown, back to the plan from one hidden
  if (!on && document.activeElement?.closest?.(`#${which}`)) $("svg").focus({ preventScroll: true });
}

function width(which, px) {
  const [lo, hi] = LIMITS[which];
  const w = Math.round(Math.min(hi, Math.max(lo, px)));
  document.documentElement.style.setProperty(VAR[which], `${w}px`);
  save(`storeypath.review.${which}.width`, String(w));
  return w;
}

function current(which) {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue(VAR[which])) || LIMITS[which][0];
}

export function setupLayout() {
  for (const which of ["navigator", "inspector"]) {
    const w = Number(saved(`storeypath.review.${which}.width`));
    if (w) width(which, w);
    const remembered = saved(`storeypath.review.${which}`);
    const narrow = innerWidth < 1180 && which === "navigator";
    showPanel(which, remembered ? remembered === "1" : !narrow, { remember: false });
    $(REVEAL[which]).addEventListener("click", () => showPanel(which, true));
  }
  for (const b of document.querySelectorAll("[data-collapse]")) b.addEventListener("click", () => showPanel(b.dataset.collapse, false));

  for (const handle of document.querySelectorAll(".resizer")) {
    const which = handle.dataset.resize;
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      const start = e.clientX, from = current(which);
      handle.classList.add("active");
      document.body.classList.add("resizing");
      const move = (ev) => {
        const dx = ev.clientX - start;
        width(which, which === "navigator" ? from + dx : from - dx);
      };
      const up = () => {
        handle.removeEventListener("pointermove", move);
        handle.classList.remove("active");
        document.body.classList.remove("resizing");
        emit("layout");
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up, { once: true });
      handle.addEventListener("pointercancel", up, { once: true });
    });
    handle.addEventListener("keydown", (e) => {
      const step = (e.shiftKey ? 40 : 10) * (which === "navigator" ? 1 : -1);
      if (e.key === "ArrowLeft") width(which, current(which) - step);
      else if (e.key === "ArrowRight") width(which, current(which) + step);
      else return;
      e.preventDefault();
      emit("layout");
    });
    handle.addEventListener("dblclick", () => {
      document.documentElement.style.removeProperty(VAR[which]);
      save(`storeypath.review.${which}.width`, "");
      emit("layout");
    });
  }
}
