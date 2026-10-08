// Who a project is shared with, and how far (server.py: /api/projects/<code>/access):
// a person is given view, edit or share on the whole project, one of its buildings or
// one of its floors. Someone with share on a part of it shares that part, and nothing
// wider; nobody changes their own access, nor the owner's. An admin may give the
// project another owner.

import { sentAway } from "./account.js";

const LEVELS = [
  ["view", "View", "sees it: plans, review, 3D, its packages"],
  ["edit", "Edit", "changes it as well: corrections, items, walls and doors, drawings read again"],
  ["share", "Share", "shares it as well, with others, up to share"],
];

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
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

const who = (u) => (u ? (u.name && u.name !== u.username ? `${u.name} (${u.username})` : u.username) : "—");
const where = (scope) => scope.kind === "project" ? "The whole project"
  : `${scope.kind === "building" ? "Building" : "Floor"}: ${scope.name || scope.id}`;
const levelSelect = (value, disabled) => el("select", { "aria-label": "Level", disabled },
  LEVELS.map(([v, label, about]) => el("option", { value: v, title: about, selected: v === value || undefined }, label)));

/** The share dialog of a project, for ``me`` (GET /api/me). ``changed`` is called when
 * anything was changed (the page may show less, or more). */
export async function openShare(code, me, changed = () => {}) {
  const dialog = el("dialog", { class: "share", "aria-labelledby": "share-title" });
  const status = el("p", { class: "form-error", role: "alert", hidden: true });
  const body = el("div", { class: "share-body" });
  let users = [];
  const fail = (e) => {
    status.textContent = e.message;
    status.hidden = false;
  };
  const close = () => {
    dialog.close();
    dialog.remove();
  };
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);

  const render = (a) => {
    status.hidden = true;
    const scopes = [];
    if (a.scopes.project) scopes.push({ kind: "project", id: null, name: a.project.name, label: "The whole project" });
    for (const b of a.scopes.buildings) {
      if (b.share) scopes.push({ kind: "building", id: b.id, label: `Building: ${b.name}` });
      for (const f of b.floors) scopes.push({ kind: "floor", id: f.id, label: `Floor: ${f.name} · ${b.name}` });
    }
    const set = async (user, scope, level) => {
      try {
        render(await api(`projects/${encodeURIComponent(code)}/access`, { user, scope: { kind: scope.kind, id: scope.id }, level }));
        changed();
      } catch (e) {
        fail(e);
      }
    };
    const rows = a.grants.map((g) => {
      const mine = g.user.id === me.id;
      const level = levelSelect(g.level, mine);
      level.addEventListener("change", () => set(g.user.id, g.scope, level.value));
      return el("tr", {},
        el("td", {}, who(g.user), mine ? el("span", { class: "muted small" }, " · you") : null),
        el("td", {}, where(g.scope)),
        el("td", {}, level),
        el("td", { class: "muted small" }, `${who(g.by)}${g.at ? ` · ${new Date(g.at).toLocaleDateString()}` : ""}`),
        el("td", { class: "actions" }, mine ? null : el("button", { type: "button", class: "danger",
          title: `${who(g.user)} no longer has this`, onclick: () => set(g.user.id, g.scope, null) }, "Remove")));
    });
    const others = users.filter((u) => u.id !== me.id && u.id !== a.owner?.id);
    const person = el("select", { "aria-label": "Person" }, el("option", { value: "" }, "Choose someone…"),
      others.map((u) => el("option", { value: u.id }, who(u))));
    const scope = el("select", { "aria-label": "Where" }, scopes.map((s, i) => el("option", { value: String(i) }, s.label)));
    const level = levelSelect("view", false);
    const add = el("form", { class: "share-add", onsubmit: (e) => {
      e.preventDefault();
      if (!person.value) return fail(new Error("choose who to share it with"));
      set(person.value, scopes[Number(scope.value)], level.value);
    } }, el("label", {}, "Share with", person), el("label", {}, "Where", scope), el("label", {}, "Level", level),
    el("button", { class: "primary", type: "submit", disabled: !scopes.length || !others.length }, "Share"));

    let owner = null;
    if (a.you.admin && me.role === "admin") {
      const pick = el("select", { "aria-label": "Owner" }, el("option", { value: "" }, a.owner ? "Choose another owner…" : "Choose an owner…"),
        users.filter((u) => u.id !== a.owner?.id).map((u) => el("option", { value: u.id }, who(u))));
      owner = el("form", { class: "share-owner", onsubmit: async (e) => {
        e.preventDefault();
        if (!pick.value) return;
        try {
          render(await api(`projects/${encodeURIComponent(code)}/owner`, { user: pick.value }));
          changed();
        } catch (err) {
          fail(err);
        }
      } }, el("label", {}, "Owner", pick), el("button", { type: "submit",
        title: "The owner before keeps share on the whole project: a grant of theirs, listed below, that may be removed" },
      "Make owner"));
    }
    body.replaceChildren(...[
      el("p", { class: "owner-line" }, a.owner
        ? `Owner: ${who(a.owner)}. The owner may do everything in the project, and delete it.`
        : "No owner yet (it was made before Studio had accounts): admins manage it."),
      owner,
      el("h3", {}, "Who has access", el("span", { class: "muted small" }, a.scopes.project ? "" : " · in the parts you may share")),
      a.grants.length
        ? el("div", { class: "grants-wrap" }, el("table", { class: "grants" },
          el("thead", {}, el("tr", {}, el("th", {}, "Who"), el("th", {}, "Where"), el("th", {}, "Level"), el("th", {}, "Given by"), el("th", {}))),
          el("tbody", {}, rows)))
        : el("p", { class: "muted small" }, "Nobody yet, but the owner and admins."),
      el("h3", {}, "Share"),
      scopes.length ? add : el("p", { class: "muted small" }, "There is nothing here you may share."),
      el("p", { class: "muted small" }, LEVELS.map(([, label, about]) => `${label}: ${about}.`).join(" "),
        " A grant on the project covers its buildings and floors, ones added later too; on a building, all its floors."),
    ].filter(Boolean)); // (no owner form but for admins)
  };

  dialog.append(
    el("div", { class: "row" }, el("h2", { id: "share-title", class: "grow" }, "Share"),
      el("button", { type: "button", class: "icon", "aria-label": "Close", title: "Close", onclick: close }, "×")),
    status, body,
    el("div", { class: "row end" }, el("button", { type: "button", onclick: close }, "Done")));
  dialog.showModal();
  try {
    const [access, people] = await Promise.all([api(`projects/${encodeURIComponent(code)}/access`), api("users")]);
    users = people;
    dialog.querySelector("#share-title").textContent = `Share ${access.project.name}`;
    render(access);
  } catch (e) {
    fail(e);
  }
}
