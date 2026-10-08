// Users (admins): who may log in to this Studio, their role and capabilities, a
// temporary password for a new user or a reset (shown once), and what was done
// lately (the audit log). Users are never deleted: they are disabled (their grants
// and what the log says of them stay).

import { sentAway } from "./account.js";

const ROLES = [
  ["user", "User", "sees only what is shared with them"],
  ["engineer", "Engineer", "creates projects and opens files as new ones; owns what they make"],
  ["admin", "Admin", "manages users; sees and does everything"],
];
const CAPABILITIES = [
  ["backup", "Backup", "downloads everything in Studio's data folder"],
  ["catalogue", "Item types", "changes the catalogue of furniture and equipment"],
];

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

const when = (iso) => (iso ? new Date(iso).toLocaleString() : "never");

/** A temporary password, shown once, to give to the person (they change it when they log in). */
function shown(title, user, password) {
  const copy = el("button", { type: "button", onclick: async () => {
    try {
      await navigator.clipboard.writeText(password);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Select it and copy it";
    }
  } }, "Copy");
  return el("section", { class: "card", role: "status" },
    el("h2", {}, title),
    el("p", { class: "muted small" }, `Give ${user.username} this password. It is shown once, here only; they choose their own when they first log in.`),
    el("div", { class: "row" }, el("code", { class: "secret" }, password), copy));
}

/** The users page, into ``page``; ``toast`` says how things went. */
export async function usersPage(page, me, toast) {
  const [users, audit] = await Promise.all([api("admin/users"), api("admin/audit?n=100")]);
  const notice = el("div");

  const refresh = async (note = null) => {
    await usersPage(page, me, toast);
    if (note) page.querySelector("[data-notice]")?.replaceChildren(note);
  };

  const change = async (u, body, what) => {
    try {
      await api(`admin/users/${encodeURIComponent(u.id)}`, body);
      toast(`${u.username}: ${what}`);
      await refresh();
    } catch (e) {
      toast(e.message, true);
      await refresh();
    }
  };

  const rows = users.map((u) => {
    const self = u.id === me.id;
    const name = el("input", { type: "text", value: u.name, "aria-label": `Name of ${u.username}` });
    name.addEventListener("change", () => change(u, { name: name.value }, "name changed"));
    const role = el("select", { "aria-label": `Role of ${u.username}`, disabled: self,
      title: self ? "Another admin changes your role" : undefined },
    ROLES.map(([v, label, about]) => el("option", { value: v, title: about, selected: v === u.role || undefined }, label)));
    role.addEventListener("change", () => change(u, { role: role.value }, `now ${role.value}: their sessions have ended`));
    const caps = el("div", { class: "caps" }, CAPABILITIES.map(([c, label, about]) => {
      const box = el("input", { type: "checkbox", checked: u.role === "admin" || u.capabilities.includes(c),
        disabled: u.role === "admin" });
      box.addEventListener("change", () => {
        const next = CAPABILITIES.map(([k]) => k).filter((k) => (k === c ? box.checked : u.capabilities.includes(k)));
        change(u, { capabilities: next }, "capabilities changed: their sessions have ended");
      });
      return el("label", { title: u.role === "admin" ? "Admins have it" : about }, box, label);
    }));
    const reset = el("button", { type: "button", title: "A new temporary password, to change at the next login; their sessions end",
      onclick: async () => {
        if (!confirm(`Give ${u.username} a new temporary password? Their sessions end, and the password they have stops working.`)) return;
        try {
          const r = await api(`admin/users/${encodeURIComponent(u.id)}/password`, {});
          await refresh(shown(`New password for ${u.username}`, u, r.password));
        } catch (e) {
          toast(e.message, true);
        }
      } }, "Reset password");
    const active = el("button", { type: "button", class: u.active ? "danger" : "", disabled: self,
      title: self ? "Another admin disables you" : u.active ? "They cannot log in, and their sessions end; nothing of theirs is deleted" : "They may log in again",
      onclick: () => change(u, { active: !u.active }, u.active ? "disabled" : "enabled") }, u.active ? "Disable" : "Enable");
    return el("tr", { class: u.active ? "" : "disabled" },
      el("td", {}, el("strong", {}, u.username), self ? el("span", { class: "muted small" }, " · you") : null,
        u.must_change_password ? el("div", { class: "muted small" }, "to change their password") : null),
      el("td", {}, name),
      el("td", {}, role),
      el("td", {}, caps),
      el("td", {}, u.active ? "Active" : "Disabled"),
      el("td", { class: "muted small" }, when(u.last_login_at)),
      el("td", { class: "actions" }, reset, " ", active));
  });

  const username = el("input", { type: "text", required: true, autocapitalize: "none", spellcheck: "false",
    placeholder: "e.g. s.ahmed", pattern: "[A-Za-z0-9][A-Za-z0-9._\\-]{1,31}" });
  const fullName = el("input", { type: "text", placeholder: "Sara Ahmed" });
  const newRole = el("select", {}, ROLES.map(([v, label, about]) => el("option", { value: v, title: about }, label)));
  const newCaps = CAPABILITIES.map(([c, label, about]) => {
    const box = el("input", { type: "checkbox" });
    box.dataset.cap = c;
    return el("label", { class: "check", title: about }, box, label);
  });
  const add = el("form", { class: "user-add", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const r = await api("admin/users", { username: username.value, name: fullName.value, role: newRole.value,
        capabilities: newCaps.map((l) => l.querySelector("input")).filter((b) => b.checked).map((b) => b.dataset.cap) });
      await refresh(shown(`${r.user.username} added`, r.user, r.password));
    } catch (err) {
      toast(err.message, true);
    }
  } }, el("label", {}, "Username", username), el("label", {}, "Name", fullName), el("label", {}, "Role", newRole),
  el("div", { class: "caps" }, newCaps), el("button", { class: "primary", type: "submit" }, "Add user"));

  page.replaceChildren(
    el("section", {},
      el("a", { class: "back", href: "#/" }, "← Projects"),
      el("h1", {}, "Users"),
      el("p", { class: "lead" }, "Who may log in to this Studio. ", ROLES.map(([, label, about]) => `${label}: ${about}.`).join(" "),
        " Projects are shared from their own page.")),
    el("div", { "data-notice": "" }, notice),
    el("section", { class: "card" }, el("h2", {}, "Add a user"), add,
      el("p", { class: "muted small" }, "They get a temporary password, shown once, to change when they first log in.")),
    el("section", { class: "card" },
      el("div", { class: "table-wrap" }, el("table", { class: "users" },
        el("thead", {}, el("tr", {}, ["Username", "Name", "Role", "Capabilities", "", "Last login", ""].map((h) => el("th", {}, h)))),
        el("tbody", {}, rows)))),
    el("section", { class: "card" },
      el("h2", {}, "Lately"),
      el("p", { class: "muted small" }, "Logins, users, sharing, projects, exports and backups: the latest first."),
      audit.length ? el("ul", { class: "audit" }, audit.map((a) => {
        const more = Object.entries(a).filter(([k]) => !["at", "user", "address", "action", "target", "outcome"].includes(k))
          .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join(" · ");
        return el("li", {},
          el("span", { class: "muted" }, when(a.at)),
          el("span", {}, a.user ? a.user.username : "—"),
          el("span", {}, a.action, a.target ? el("span", { class: "muted" }, ` · ${a.target}`) : null,
            more ? el("span", { class: "muted small" }, ` · ${more}`) : null),
          el("span", { class: a.outcome }, a.outcome === "ok" ? "" : a.outcome, a.address ? el("span", { class: "muted small" }, ` ${a.address}`) : null));
      })) : el("p", { class: "empty" }, "Nothing yet.")),
  );
}
