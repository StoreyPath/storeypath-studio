// Who is using Studio (server.py, accounts.py): the person's menu on every page, and
// where a page goes when Studio answers that nobody is logged in (401) or that a
// password given to change must be changed first.

const $menu = { open: null };

/** A path to come back to after logging in: one of Studio's own, nothing else. Its
 * path is begun with one slash alone: "/.//evil.example" is "//evil.example" once read
 * (the dot taken out), which a browser takes for another site. */
export function safeNext(raw) {
  if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return "/";
  try {
    const u = new URL(raw, location.origin);
    return u.origin === location.origin ? u.pathname.replace(/^\/+/, "/") + u.search + u.hash : "/";
  } catch {
    return "/";
  }
}

const here = () => location.pathname + location.search + location.hash;

export function toLogin() {
  location.assign(`/login.html?next=${encodeURIComponent(here())}`);
}

export function toChangePassword() {
  location.assign(`/login.html?change=1&next=${encodeURIComponent(here())}`);
}

/** Whether an answer sends the page elsewhere (not logged in, a password to change). */
export function sentAway(res, data) {
  if (res.status === 401) {
    toLogin();
    return true;
  }
  if (res.status === 403 && data && data.must_change_password) {
    toChangePassword();
    return true;
  }
  return false;
}

/** The person logged in (GET /api/me), or off to the login page. */
export async function whoami() {
  const res = await fetch("/api/me");
  const data = await res.json().catch(() => ({}));
  if (sentAway(res, data)) return new Promise(() => {}); // the page is leaving
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  if (data.must_change_password) {
    toChangePassword();
    return new Promise(() => {});
  }
  return data;
}

function node(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

export async function logout() {
  await fetch("/api/logout", { method: "POST", headers: { "X-StoreyPath": "1", "Content-Type": "application/json" }, body: "{}" })
    .catch(() => {});
  location.assign("/login.html");
}

/** The person's name, and their menu: change password, users (admins), backup
 * (admins, and whoever an admin let), log out. Studio without accounts (storeypath
 * review, on this computer alone) has none of it. */
export function accountMenu(me) {
  if (!me || me.local) return node("span");
  const can = (c) => me.role === "admin" || (me.capabilities || []).includes(c);
  const items = [
    node("a", { role: "menuitem", href: `/login.html?change=1&next=${encodeURIComponent(here())}` }, "Change password"),
    me.role === "admin" ? node("a", { role: "menuitem", href: "/#/users" }, "Users") : null,
    can("backup") ? node("a", { role: "menuitem", href: "/api/backup", download: "",
      title: "Everything in Studio's data folder as one .tar.gz: projects, item types, users (with their passwords' hashes), sharing and the audit log; keep it safe" }, "Download a backup") : null,
    node("button", { type: "button", role: "menuitem", onclick: logout }, "Log out"),
  ];
  const list = node("div", { class: "account-menu", role: "menu", hidden: true }, items);
  const role = { admin: "Admin", engineer: "Engineer", user: "User" }[me.role] || me.role;
  const button = node("button", { type: "button", class: "account-button", "aria-haspopup": "menu", "aria-expanded": "false",
    title: `${me.username} · ${role}` }, node("span", { class: "avatar", "aria-hidden": "true" }, (me.name || me.username).slice(0, 1).toUpperCase()),
  node("span", { class: "account-name" }, me.name || me.username));
  const wrap = node("div", { class: "account" }, button, list);
  const close = () => {
    list.hidden = true;
    button.setAttribute("aria-expanded", "false");
  };
  button.addEventListener("click", (e) => {
    e.stopPropagation();
    list.hidden = !list.hidden;
    button.setAttribute("aria-expanded", String(!list.hidden));
    if (!list.hidden) list.querySelector("a, button")?.focus();
  });
  document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) close(); });
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      close();
      button.focus();
    }
  });
  list.addEventListener("click", close);
  $menu.open = close;
  return wrap;
}
