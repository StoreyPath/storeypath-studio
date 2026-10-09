// GPU helpers (admins): the servers that run the vision model (the gpu-helper image, or
// any OpenAI-compatible server that takes images), kept in Studio's database and used at
// once: each one's address, key, whether it is used and how many rooms it is asked about
// at once; how each is, the model it serves (all must serve the same one: one serving
// another is left out), and a Test button that sends it a sample room.

import { sentAway } from "./account.js";

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: "POST", headers: { "X-StoreyPath": "1", "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(`/api/${path}`, init);
  const data = await res.json().catch(() => ({}));
  if (sentAway(res, data)) throw new Error("log in again");
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

const STATES = {
  answers: ["ok", "Answers"],
  "other model": ["off", "Left out: serves another model"],
  "not answering": ["off", "Not answering"],
  "not asked yet": ["", "Not asked yet"],
  off: ["", "Not used"],
};

/** The GPU helpers page, into ``page``; ``toast`` says how things went. */
export async function helpersPage(page, toast) {
  const said = await api("admin/helpers");
  const rows = said.helpers.map((h) => ({ ...h, key: undefined, hasKey: h.key }));

  const save = async () => {
    try {
      await api("admin/helpers", { helpers: rows.filter((r) => r.url.trim()).map((r) => ({
        url: r.url.trim(), enabled: r.enabled, parallel: Number(r.parallel) || 1, ...(r.key !== undefined ? { key: r.key } : {}),
      })) });
      toast("Saved: Studio uses them now");
      await helpersPage(page, toast);
    } catch (e) {
      toast(e.message, true);
    }
  };

  const line = (r, i) => {
    const [cls, words] = STATES[r.state] || ["", r.state || "New"];
    const result = el("div", { class: "muted small", "aria-live": "polite" });
    const test = el("button", { type: "button", title: "Send it a sample room, as a conversion would ask it", onclick: async () => {
      test.disabled = true;
      result.textContent = "Asking…";
      try {
        const t = await api("admin/helpers/test", { url: r.url.trim(), ...(r.key ? { key: r.key } : {}) });
        result.className = `small ${t.ok ? "ok" : "unsure"}`;
        result.textContent = (t.ok
          ? `Answered in ${t.seconds} s (${t.model}): ${Object.values(t.answer).join(", ") || "nothing it could say"}`
          : `No answer: ${t.error}`) + (t.tls ? ` · ${t.tls}` : "");
      } catch (e) {
        result.className = "small unsure";
        result.textContent = e.message;
      } finally {
        test.disabled = false;
      }
    } }, "Test");
    const url = el("input", { type: "text", value: r.url, placeholder: "https://gpu1:8105/v1", spellcheck: "false",
      "aria-label": "Address", oninput: (e) => { r.url = e.target.value; } });
    const key = el("input", { type: "password", autocomplete: "off", "aria-label": "Key",
      placeholder: r.hasKey ? "kept (type to change)" : "no key", oninput: (e) => { r.key = e.target.value; } });
    const used = el("input", { type: "checkbox", checked: r.enabled, "aria-label": "Used",
      onchange: (e) => { r.enabled = e.target.checked; } });
    const parallel = el("input", { type: "number", min: 1, max: 32, step: 1, value: r.parallel, "aria-label": "Rooms at once",
      oninput: (e) => { r.parallel = e.target.value; } });
    const models = r.models?.length ? r.models.join(", ") : "—";
    return el("tr", { class: r.enabled ? "" : "disabled" },
      el("td", {}, url, result),
      el("td", {}, key),
      el("td", { class: "center" }, used),
      el("td", {}, parallel),
      el("td", {}, el("span", { class: `chip ${cls}` }, words),
        r.tls ? el("div", { class: "muted small" }, r.tls) : null,
        r.error ? el("div", { class: "muted small" }, r.error) : null,
        r.busy ? el("div", { class: "muted small" }, `${r.busy} being asked now`) : null),
      el("td", { class: "small" }, models),
      el("td", { class: "actions" }, test, " ",
        el("button", { type: "button", class: "danger", title: "Not a helper any more (once saved)",
          onclick: () => { rows.splice(i, 1); draw(); } }, "Remove")));
  };

  const table = el("tbody");
  const draw = () => table.replaceChildren(...rows.map(line));
  draw();

  page.replaceChildren(
    el("section", {},
      el("a", { class: "back", href: "#/" }, "← Projects"),
      el("h1", {}, "GPU helpers"),
      el("p", { class: "lead" }, "The servers that run the vision model, which looks at every room on the plans as printed. "
        + "Rooms are spread over the helpers used, each asked about so many at once; one that does not answer is left out "
        + "for a while and tried again. They must all serve the same model: one serving another is left out. "
        + "Saved here, they are used at once.")),
    said.from === "environment" ? el("section", { class: "card", role: "note" },
      el("p", { class: "muted small" }, "These come from STOREYPATH_VISION_URL and STOREYPATH_VISION_KEY, where Studio "
        + "starts from. Once saved here, Studio keeps them in its database and uses them from then on.")) : null,
    el("section", { class: "card" },
      el("div", { class: "row" }, el("h2", { class: "grow" }, "Helpers"),
        el("span", { class: "muted small" }, said.model ? `Model: ${said.model}` : "No model known yet")),
      el("div", { class: "table-wrap" }, el("table", { class: "users helpers" },
        el("thead", {}, el("tr", {}, ["Address", "Key", "Used", "Rooms at once", "How it is", "Models it serves", ""]
          .map((h) => el("th", {}, h)))),
        table)),
      el("div", { class: "row", style: "margin-top:12px" },
        el("button", { type: "button", onclick: () => {
          rows.push({ url: "", key: "", hasKey: false, enabled: true, parallel: 2, state: null });
          draw();
          table.querySelector("tr:last-child input")?.focus();
        } }, "Add a helper"),
        el("span", { class: "grow" }),
        el("button", { type: "button", onclick: () => helpersPage(page, toast), title: "Ask each how it is again" }, "Check again"),
        el("button", { type: "button", class: "primary", onclick: save }, "Save"))),
  );
}
