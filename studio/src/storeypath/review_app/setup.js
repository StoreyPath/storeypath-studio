// The first admin, made with the link Studio printed when it started (its one-time
// token after the #): it works once, until Studio has a user.

const $ = (id) => document.getElementById(id);
const token = location.hash.slice(1);
// the token is not kept in the address (nor in the history) once read
if (token) history.replaceState(null, "", location.pathname + location.search);

function say(message) {
  $("setup-error").textContent = message || "";
  $("setup-error").hidden = !message;
}

function done(why) {
  $("setup-card").hidden = true;
  $("done-card").hidden = false;
  if (why) $("done-why").textContent = why;
}

$("setup").addEventListener("submit", async (e) => {
  e.preventDefault();
  say("");
  if ($("password").value !== $("again").value) return say("The two passwords are not the same");
  const res = await fetch("/api/setup", {
    method: "POST",
    headers: { "X-StoreyPath": "1", "Content-Type": "application/json" },
    body: JSON.stringify({ token, username: $("username").value, name: $("name").value, password: $("password").value }),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 404) return done(data.error);
  if (!res.ok) return say(data.error || `${res.status} ${res.statusText}`);
  location.replace("/");
});

async function start() {
  const res = await fetch("/api/me").catch(() => null);
  const data = res ? await res.json().catch(() => ({})) : {};
  if (res && res.ok) return location.replace("/"); // logged in: set up long ago
  if (res && res.status === 401 && !data.setup) return done();
  if (!token) {
    $("setup-card").hidden = false;
    $("setup").querySelector("button").disabled = true;
    return say("This page needs the whole setup link Studio printed when it started (with what follows the #).");
  }
  $("setup-card").hidden = false;
  $("username").focus();
}

start();
