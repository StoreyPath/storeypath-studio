// What the tests need, with nothing to install: a package read from its ZIP, a
// static server, and Chrome driven over the DevTools protocol (real mouse, wheel,
// touch and keys, as a person would use the plan).

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, join, normalize } from "node:path";
import { inflateRawSync } from "node:zlib";

/** The files in a ZIP archive: name → bytes. */
export function unzip(buf) {
  let end = buf.length - 22;
  while (end >= 0 && buf.readUInt32LE(end) !== 0x06054b50) end--;
  if (end < 0) throw new Error("not a ZIP archive");
  const files = new Map();
  let p = buf.readUInt32LE(end + 16);
  for (let i = buf.readUInt16LE(end + 10); i > 0; i--) {
    if (buf.readUInt32LE(p) !== 0x02014b50) throw new Error("bad ZIP directory");
    const method = buf.readUInt16LE(p + 10);
    const size = buf.readUInt32LE(p + 20);
    const nameLength = buf.readUInt16LE(p + 28);
    const local = buf.readUInt32LE(p + 42);
    const name = buf.toString("utf8", p + 46, p + 46 + nameLength);
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const raw = buf.subarray(start, start + size);
    files.set(name, method === 0 ? raw : inflateRawSync(raw));
    p += 46 + nameLength + buf.readUInt16LE(p + 30) + buf.readUInt16LE(p + 32);
  }
  return files;
}

/** A package as floorFromPackage takes it, found through its manifest. */
export function readPackage(path) {
  const files = unzip(readFileSync(path));
  const json = (name) => JSON.parse(files.get(name).toString("utf8"));
  const manifest = json("manifest.json");
  const features = (role) => (manifest.files[role] ? json(manifest.files[role]).features : []);
  return { manifest, floors: features("floors"), spaces: features("spaces"), zones: features("zones"),
    openings: features("openings") };
}

const TYPES = { ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
  ".json": "application/json", ".map": "application/json" };

/** Serve a folder, and some made-up files (path → text). */
export function serve(root, extra = {}) {
  const server = createServer((req, res) => {
    const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (path in extra) {
      res.writeHead(200, { "content-type": TYPES[extname(path)] ?? "text/plain" });
      return res.end(extra[path]);
    }
    const file = normalize(join(root, path));
    if (!file.startsWith(root) || !existsSync(file)) {
      res.writeHead(404);
      return res.end();
    }
    res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream" });
    res.end(readFileSync(file));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({
    url: `http://127.0.0.1:${server.address().port}`, close: () => server.close() })));
}

const CHROMES = [
  process.env.CHROME,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
];

/** Headless Chrome and one page in it. */
export async function launch() {
  const chrome = CHROMES.find((c) => c && existsSync(c));
  if (!chrome) throw new Error("no Chrome or Chromium found: set CHROME to one");
  const profile = mkdtempSync(join(tmpdir(), "sp-svg-test-"));
  const proc = spawn(chrome, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars", "about:blank"],
  { stdio: ["ignore", "ignore", "pipe"] });
  const wsUrl = await new Promise((resolve, reject) => {
    let said = "";
    proc.stderr.on("data", (d) => {
      said += d;
      const m = said.match(/DevTools listening on (ws:\/\/\S+)/);
      if (m) resolve(m[1]);
    });
    proc.on("exit", () => reject(new Error(`Chrome exited: ${said}`)));
  });
  const ws = new WebSocket(wsUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  let next = 0;
  const waiting = new Map();
  const listeners = [];
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && waiting.has(m.id)) {
      const { resolve, reject } = waiting.get(m.id);
      waiting.delete(m.id);
      m.error ? reject(new Error(`${m.error.message} ${m.error.data ?? ""}`)) : resolve(m.result);
    } else if (m.method) {
      for (const l of listeners) l(m);
    }
  });
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++next;
    waiting.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await call("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
  const send = (method, params) => call(method, params, sessionId);
  const errors = [];
  listeners.push((m) => {
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text);
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") errors.push(m.params.args.map((a) => a.value ?? a.description).join(" "));
  });
  await send("Runtime.enable");
  await send("Page.enable");

  const page = {
    send,
    errors,
    /** Run a function in the page with JSON arguments; its (awaited) result. */
    async run(fn, ...args) {
      const r = await send("Runtime.evaluate", { expression: `(${fn})(...${JSON.stringify(args)})`, returnByValue: true,
        awaitPromise: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
      return r.result.value;
    },
    async open(url, width = 1000, height = 700) {
      await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
      const loaded = new Promise((r) => listeners.push((m) => m.method === "Page.loadEventFired" && r()));
      await send("Page.navigate", { url });
      await loaded;
    },
    mouse: (type, x, y, extra = {}) => send("Input.dispatchMouseEvent", { type, x, y, button: "left",
      buttons: type === "mouseReleased" ? 0 : extra.down ? 1 : 0, clickCount: type === "mouseMoved" ? 0 : 1, ...extra.params }),
    async click(x, y) {
      await page.mouse("mouseMoved", x, y);
      await page.mouse("mousePressed", x, y, { down: true });
      await page.mouse("mouseReleased", x, y);
    },
    async drag(from, to, steps = 8) {
      await page.mouse("mouseMoved", from[0], from[1]);
      await page.mouse("mousePressed", from[0], from[1], { down: true });
      for (let i = 1; i <= steps; i++) {
        await page.mouse("mouseMoved", from[0] + ((to[0] - from[0]) * i) / steps, from[1] + ((to[1] - from[1]) * i) / steps,
          { down: true, params: { button: "left" } });
      }
      await page.mouse("mouseReleased", to[0], to[1]);
    },
    wheel: (x, y, deltaY) => send("Input.dispatchMouseEvent", { type: "mouseWheel", x, y, deltaX: 0, deltaY }),
    touch: (type, points) => send("Input.dispatchTouchEvent", { type, touchPoints: points.map(([x, y], id) => ({ x, y, id })) }),
    key: async (key, code = key, keyCode = 0) => {
      const text = key.length === 1 ? key : key === "Enter" ? "\r" : undefined;
      await send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode: keyCode, ...(text ? { text } : {}) });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: keyCode });
    },
    close() {
      ws.close();
      proc.kill();
      try { rmSync(profile, { recursive: true, force: true }); } catch { /* Chrome may still hold it */ }
    },
  };
  return page;
}
