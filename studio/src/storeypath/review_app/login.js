// Logging in to Studio, and changing one's password (a temporary one, given by an
// admin, before anything else). ?next= is where to go after: a page of Studio's.

import { safeNext } from "./account.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const next = safeNext(params.get("next") || "/");

async function post(path, body) {
  const res = await fetch(`/api/${path}`, {
    method: "POST",
    // the header a page of another site cannot send (server.py refuses changes without it)
    headers: { "X-StoreyPath": "1", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { res, data: await res.json().catch(() => ({})) };
}

function say(id, message) {
  $(id).textContent = message || "";
  $(id).hidden = !message;
}

function showLogin() {
  $("change-card").hidden = true;
  $("login-card").hidden = false;
  $("username").focus();
}

function showChange(me, forced) {
  $("login-card").hidden = true;
  $("change-card").hidden = false;
  $("change-username").value = me?.username || "";
  $("change-cancel").hidden = forced;
  $("change-cancel").href = next;
  $("change-why").textContent = forced
    ? "The password you have was given to you to change. Choose your own: only you will know it."
    : "A new password for your account. Your other sessions end; this one goes on.";
  $("current").focus();
}

$("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  say("login-error", "");
  const button = e.target.querySelector("button");
  button.disabled = true;
  try {
    const { res, data } = await post("login", { username: $("username").value, password: $("password").value });
    if (!res.ok) {
      say("login-error", data.error || `${res.status} ${res.statusText}`);
      $("password").select();
      return;
    }
    if (data.must_change_password) {
      $("current").value = $("password").value;
      return showChange(data.user, true);
    }
    location.replace(next);
  } catch {
    say("login-error", "Studio cannot be reached");
  } finally {
    button.disabled = false;
  }
});

$("change").addEventListener("submit", async (e) => {
  e.preventDefault();
  say("change-error", "");
  if ($("new").value !== $("again").value) return say("change-error", "The two new passwords are not the same");
  const { res, data } = await post("me/password", { current: $("current").value, new: $("new").value });
  if (res.status === 401) return showLogin(false);
  if (!res.ok) return say("change-error", data.error || `${res.status} ${res.statusText}`);
  location.replace(next);
});

async function start() {
  let res, data;
  try {
    res = await fetch("/api/me");
    data = await res.json().catch(() => ({}));
  } catch {
    showLogin(false);
    return say("login-error", "Studio cannot be reached");
  }
  if (res.status === 401) return showLogin();
  if (!res.ok) return showLogin(false);
  if (data.local) return location.replace(next); // this computer alone: no accounts
  if (data.must_change_password || params.has("change")) return showChange(data, Boolean(data.must_change_password));
  location.replace(next); // logged in already
}

start();
