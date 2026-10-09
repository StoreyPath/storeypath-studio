// Share an area: a person drags a rectangle over the plan (at most 50 × 50 m), looks
// at what a sample of it would hold (the area as drawn, Studio's reading of it, and what
// is taken out for privacy, each text switchable) and downloads it, <id>.spsample, to
// send to the StoreyPath team (Studio's areasample/, docs/AREA-SAMPLES.md). Anyone who
// may see the floor may do it: nothing on the floor changes.
//
// Its own file, joined to the page by:
//
//   setup(api)       once, with what of the page it uses: the Share an area tool (A),
//                    whose pointer on the plan drags the rectangle (the plan's metres);
//   sharing()        whether the tool is in use;
//   stop()           another floor: the rectangle goes (the tool with it).

import { sentAway } from "../account.js";
import { PAGE } from "../together.js";

const MAX_SIDE = 50; // metres: as the server (areasample.frame.MAX_SIDE_M)
const MIN_SIDE = 0.5;
const SVG_NS = "http://www.w3.org/2000/svg";
const KINDS = { name: "a person's name", "maybe a name": "may be a name", phone: "phone number", email: "email",
  extension: "extension", web: "web address", "id number": "ID or permit number", contact: "contact",
  project: "the project", site: "the site", building: "the building", floor: "the floor", address: "the address" };

let api = null;
const share = { on: false, start: null, end: null, dialog: null };
const $ = (id) => document.getElementById(id);

/** An element: attributes left out when null, undefined or false; children flattened. */
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

/** Once, before the page starts: the tool, and the rectangle's layer. */
export function setup(given) {
  api = given;
  for (const world of ["world", "world-print"]) {
    const layer = document.createElementNS(SVG_NS, "g");
    layer.setAttribute("class", "share-preview");
    $(world).append(layer);
  }
  api.tool({
    id: "share", label: "Share an area", icon: "square-dashed-mouse-pointer", key: "a", group: "share", keepsSelection: true,
    words: "sample spsample send report wrong",
    wrongView: () => "An area is chosen on the plan",
    available: () => (api.state.floor?.source ? "" : "This floor has no drawing yet: a sample is made from a floor's drawing"),
    hint: () => `Drag a rectangle over the part of the plan Studio read wrong (at most ${MAX_SIDE} × ${MAX_SIDE} m): a file to send to the StoreyPath team`,
    options: () => {
      const a = area();
      return [el("span", { class: "to-note" }, "Nothing is sent: you download the sample and send it yourself"),
        el("span", { class: `to-value${a && tooBig(a) ? " too-big" : ""}` }, a ? size(a) : `≤ ${MAX_SIDE} × ${MAX_SIDE} m`)];
    },
    start: () => {
      share.on = true;
      share.start = share.end = null;
      $("map").classList.add("sharing");
      draw();
    },
    stop: () => {
      share.on = false;
      share.start = share.end = null;
      $("map").classList.remove("sharing");
      draw();
    },
    escape: () => {
      if (!share.start) return false;
      share.start = share.end = null;
      draw();
      return true;
    },
    plan: { down, move, up },
  });
}

export const sharing = () => share.on;

/** The rectangle goes, and the tool with it. */
export function stop() {
  if (share.on) api.setTool(null);
}

function area() {
  if (!share.start || !share.end) return null;
  const [a, b] = [share.start, share.end];
  const x0 = Math.min(a[0], b[0]), y0 = Math.min(a[1], b[1]), x1 = Math.max(a[0], b[0]), y1 = Math.max(a[1], b[1]);
  return { box: [x0, y0, x1, y1], w: x1 - x0, h: y1 - y0 };
}

const size = (a) => `${a.w.toFixed(1)} × ${a.h.toFixed(1)} m`;
const tooBig = (a) => a.w > MAX_SIDE || a.h > MAX_SIDE;

function draw() {
  const a = area();
  for (const layer of document.querySelectorAll(".share-preview")) {
    layer.replaceChildren();
    if (!a) continue;
    const r = document.createElementNS(SVG_NS, "rect");
    r.setAttribute("x", a.box[0]);
    r.setAttribute("y", a.box[1]);
    r.setAttribute("width", a.w);
    r.setAttribute("height", a.h);
    r.setAttribute("class", tooBig(a) ? "share-rect too-big" : "share-rect");
    layer.append(r);
  }
  api.emit("tool-options");
}

function down(p) {
  share.start = share.end = p;
  draw();
  return true; // the drag is the tool's
}

function move(p) {
  if (!share.start) return;
  share.end = p;
  draw();
}

function up(p) {
  if (!share.start) return;
  share.end = p;
  const a = area();
  draw();
  if (a.w < MIN_SIDE || a.h < MIN_SIDE) {
    share.start = share.end = null;
    draw();
    return api.toast("Drag a rectangle over the area to share: press, drag and let go", true);
  }
  if (tooBig(a)) {
    return api.toast(`The area is ${size(a)}: a sample is at most ${MAX_SIDE} × ${MAX_SIDE} m, so drag a smaller one`, true);
  }
  open(a.box.map((v) => Math.round(v * 100) / 100), api.state.floor.id);
}

// ---- the dialog: what the sample would hold, then the download ---------------------------

async function post(path, body, raw = false) {
  const res = await fetch(`/api/${path}`, {
    method: "POST",
    headers: { "X-StoreyPath": "1", "Content-Type": "application/json", "X-StoreyPath-Page": PAGE },
    body: JSON.stringify(body),
  });
  if (raw && res.ok) return res;
  const data = await res.json().catch(() => ({}));
  if (sentAway(res, data)) throw new Error("log in again");
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function open(box, floorId) {
  share.dialog?.remove();
  const choice = { keep: new Set(), remove: new Set() };
  const note = el("textarea", { id: "sample-note", rows: 3, maxlength: 2000,
    placeholder: "What went wrong here? (optional) e.g. the corridor was read as three rooms" });
  const status = el("p", { class: "sample-status", role: "status" }, "Making the preview…");
  const pictures = el("div", { class: "sample-pictures" });
  const found = el("div", { class: "sample-found" });
  const download = el("button", { type: "button", class: "primary", disabled: true }, "Download");
  const cancel = el("button", { type: "button" }, "Cancel");
  const w = (box[2] - box[0]).toFixed(1), h = (box[3] - box[1]).toFixed(1);
  const dialog = el("dialog", { class: "sample", "aria-labelledby": "sample-title" },
    el("div", { class: "sample-head" },
      el("h2", { id: "sample-title" }, "Share an area of this floor"),
      el("span", { class: "meta" }, `${w} × ${h} m · at most ${MAX_SIDE} × ${MAX_SIDE} m`)),
    el("p", { class: "meta" }, "A small file for the StoreyPath team, to see how Studio read this part of the floor and " +
      "improve it: the drawing in the area, the two pictures below, what Studio decided there and what was corrected. " +
      "Nothing is sent: you download it and send it yourself."),
    el("label", { class: "field" }, "Note", note),
    status, pictures, found,
    el("div", { class: "row sample-buttons" }, cancel, download));
  share.dialog = dialog;
  document.body.append(dialog);
  dialog.addEventListener("close", () => {
    dialog.remove();
    if (share.dialog === dialog) share.dialog = null;
    share.start = share.end = null; // another area may be dragged
    draw();
  });
  cancel.addEventListener("click", () => dialog.close());
  dialog.showModal();

  let asked = 0;
  let timer = null;
  const body = () => ({ area: box, keep: [...choice.keep], remove: [...choice.remove] });
  const preview = async () => {
    const n = ++asked;
    status.textContent = "Making the preview…";
    status.classList.remove("error");
    dialog.classList.add("busy");
    try {
      const got = await post(`${api.BASE}/floors/${encodeURIComponent(floorId)}/sample/preview`, body());
      if (n !== asked) return;
      show(got);
      const c = got.counts;
      status.textContent = `${c.spaces + c.zones} spaces and zones, ${c.openings} doors, windows and openings, ` +
        `${c.texts} texts; ${c.corrections} corrected, ${c.drawn} drawn, ${c.items} items placed.`;
      download.disabled = false;
    } catch (e) {
      if (n !== asked) return;
      status.textContent = `No sample of this area: ${e.message}`;
      status.classList.add("error");
      download.disabled = true;
    } finally {
      if (n === asked) dialog.classList.remove("busy");
    }
  };
  const again = () => {
    download.disabled = true;
    clearTimeout(timer);
    timer = setTimeout(preview, 600);
  };

  const show = (got) => {
    const figure = (src, caption) => {
      const img = el("img", { src, alt: caption, title: "Click to see it larger" });
      const f = el("figure", {}, img, el("figcaption", {}, caption));
      img.addEventListener("click", () => f.classList.toggle("zoomed"));
      return f;
    };
    pictures.replaceChildren(figure(got.images.drawing, "The area as drawn (drawing.png)"),
      figure(got.images.reading, "Studio's reading of it (reading.png)"));
    const p = got.privacy;
    const row = (item, takenOut, onChange, always = false) => {
      const box_ = el("input", { type: "checkbox", checked: takenOut || undefined, disabled: always || undefined });
      box_.addEventListener("change", () => onChange(box_.checked));
      const where = item.where?.length && item.where[0] !== item.text ? `in “${item.where[0]}”` : "";
      return el("li", { class: takenOut ? "out" : "" },
        el("label", {}, box_, el("span", { class: "sample-text" }, item.text)),
        el("span", { class: "sample-kind" }, takenOut ? `→ ${item.placeholder || "[TEXT]"}` : "kept"),
        el("span", { class: "meta" }, [KINDS[item.kind] || "", where].filter(Boolean).join(" · ")));
    };
    const listed = p.found.filter((f) => !f.always);
    const always = p.found.filter((f) => f.always);
    const others = [...p.chosen.map((c) => ({ ...c, removed: true })), ...p.others.map((o) => ({ ...o, removed: false }))]
      .sort((a, b) => a.text.localeCompare(b.text));
    found.replaceChildren(
      el("h3", {}, "Taken out for privacy"),
      el("p", { class: "meta" }, "Always: where the area is (moved to 0, 0), Studio's IDs (S1, D1… instead), the " +
        "drawing file's own data (who saved it, paths, properties), layouts, images and references, and the " +
        `project's, site's, building's and floor's names and codes${always.length ? ` (${always.length} here)` : ""}. ` +
        "Untick a text below to keep it; tick another to take it out."),
      listed.length ? el("ul", { class: "sample-list" }, listed.map((f) => row(f, f.removed, (out) => {
        if (out) choice.keep.delete(f.id);
        else choice.keep.add(f.id);
        again();
      }))) : el("p", { class: "meta" }, "No names, phone numbers or emails were found in this area."),
      el("details", {}, el("summary", {}, `Other texts in the area (${others.length}): kept`),
        el("ul", { class: "sample-list" }, others.map((o) => row(o, o.removed, (out) => {
          if (out) choice.remove.add(o.id);
          else choice.remove.delete(o.id);
          again();
        })))),
    );
    const open_ = found.querySelector("details");
    if (choice.remove.size) open_.open = true;
  };

  download.addEventListener("click", async () => {
    download.disabled = true;
    status.textContent = "Making the sample…";
    status.classList.remove("error");
    try {
      const res = await post(`${api.BASE}/floors/${encodeURIComponent(floorId)}/sample`,
        { ...body(), note: note.value }, true);
      const blob = await res.blob();
      const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1] || "area.spsample";
      const a = el("a", { href: URL.createObjectURL(blob), download: name });
      document.body.append(a);
      a.click();
      setTimeout(() => {
        URL.revokeObjectURL(a.href);
        a.remove();
      }, 1000);
      dialog.close();
      stop();
      api.toast(`Saved ${name}: send it to the StoreyPath team`);
    } catch (e) {
      status.textContent = `Not made: ${e.message}`;
      status.classList.add("error");
      download.disabled = false;
    }
  });
  preview();
}
