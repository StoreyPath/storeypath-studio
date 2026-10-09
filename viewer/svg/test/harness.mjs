// What the tests need, with nothing to install: a package read from its ZIP, a
// static server, and Chrome driven over the DevTools protocol (real mouse, wheel,
// touch and keys, as a person would use the plan).

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, join, normalize } from "node:path";
import { crc32, deflateRawSync, inflateRawSync } from "node:zlib";

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

/** A ZIP archive of files (name → bytes), each deflated. */
export function zip(files) {
  const local = [], central = [];
  let offset = 0;
  for (const [name, data] of files) {
    const raw = Buffer.from(data), packed = deflateRawSync(raw), path = Buffer.from(name, "utf8");
    const head = Buffer.alloc(30), entry = Buffer.alloc(46);
    head.writeUInt32LE(0x04034b50, 0);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4); // made by, and
    for (const [b, at] of [[head, 4], [entry, 6]]) { // needed: 2.0; names in UTF-8, deflated
      b.writeUInt16LE(20, at);
      b.writeUInt16LE(0x800, at + 2);
      b.writeUInt16LE(8, at + 4);
      b.writeUInt32LE(crc32(raw), at + 10);
      b.writeUInt32LE(packed.length, at + 14);
      b.writeUInt32LE(raw.length, at + 18);
      b.writeUInt16LE(path.length, at + 22);
    }
    entry.writeUInt32LE(offset, 42);
    local.push(head, path, packed);
    central.push(entry, path);
    offset += head.length + path.length + packed.length;
  }
  const directory = Buffer.concat(central), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.size, 8);
  end.writeUInt16LE(files.size, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, directory, end]);
}

/** A package file changed: ``change`` gets its JSON files (name → parsed) to change in place. */
export function repack(path, change) {
  const files = unzip(readFileSync(path));
  const json = new Map([...files].filter(([name]) => /\.(json|geojson)$/.test(name))
    .map(([name, data]) => [name, JSON.parse(data.toString("utf8"))]));
  change(json);
  for (const [name, value] of json) files.set(name, JSON.stringify(value));
  return zip(files);
}

/** A longitude in (-180, 180]. */
export const wrapped = (lon) => lon - 360 * Math.ceil((lon - 180) / 360);

/** A package file moved round the earth, every longitude ``east`` degrees east (in
 * (-180, 180]): its buildings the same, as the earth is the same all round. */
export function movedEast(path, east) {
  const move = (c) => (typeof c[0] === "number" ? [wrapped(c[0] + east), ...c.slice(1)] : c.map(move));
  const walk = (o) => {
    if (Array.isArray(o)) o.forEach(walk);
    else if (o && typeof o === "object") {
      for (const [k, v] of Object.entries(o)) {
        if (["coordinates", "display_point", "span", "swings"].includes(k) && Array.isArray(v)) o[k] = move(v);
        else walk(v);
      }
    }
  };
  return repack(path, (files) => {
    for (const p of Object.values(files.get("manifest.json").placements ?? {})) p.lon = wrapped(p.lon + east);
    for (const [name, doc] of files) if (name.endsWith(".geojson")) walk(doc);
  });
}

/** A package (its file, or its bytes) as floorFromPackage takes it, found through its manifest. */
export function readPackage(path) {
  const files = unzip(Buffer.isBuffer(path) ? path : readFileSync(path));
  const json = (name) => JSON.parse(files.get(name).toString("utf8"));
  const manifest = json("manifest.json");
  const features = (role) => (manifest.files[role] ? json(manifest.files[role]).features : []);
  return { manifest, floors: features("floors"), spaces: features("spaces"), zones: features("zones"),
    openings: features("openings"), items: features("items"),
    catalogue: manifest.files.catalogue ? json(manifest.files.catalogue) : null,
    navigation: manifest.files.navigation ? json(manifest.files.navigation) : null };
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

/** Headless Chrome and one page in it; with ``webgl``, WebGL drawn in software
 * (SwiftShader), for the 3D world's tests ("gpu": on the graphics card). Driven over a pipe (--remote-debugging-pipe),
 * so that when this process ends, however it ends (killed too), the pipe closes and
 * Chrome quits with it; and killed on exit, on Ctrl-C or SIGTERM, and after ``timeout``
 * seconds (the run failing then): no Chrome left drawing on its own. */
export async function launch({ webgl = false, timeout = 600 } = {}) {
  const chrome = CHROMES.find((c) => c && existsSync(c));
  if (!chrome) throw new Error("no Chrome or Chromium found: set CHROME to one");
  const profile = mkdtempSync(join(tmpdir(), "sp-svg-test-"));
  const proc = spawn(chrome, ["--headless=new", "--remote-debugging-pipe", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
    ...(webgl === "gpu" ? ["--use-angle=metal", "--ignore-gpu-blocklist"] : webgl ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
      : ["--disable-gpu"]), "about:blank"],
  { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"] });
  let said = "";
  proc.stderr.on("data", (d) => {
    said = (said + d).slice(-4000);
  });
  const kill = () => {
    if (proc.exitCode === null && proc.signalCode === null) proc.kill("SIGKILL");
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* Chrome may still hold it */ }
  };
  const signalled = (code) => () => {
    kill();
    process.exit(code);
  };
  const onInt = signalled(130), onTerm = signalled(143);
  process.on("exit", kill); // (an error nothing caught ends the process: this too)
  process.once("SIGINT", onInt);
  process.once("SIGTERM", onTerm);
  const watchdog = setTimeout(() => {
    console.error(`Chrome killed: the run took more than ${timeout} s`);
    kill();
    process.exit(1);
  }, timeout * 1000);
  watchdog.unref();

  // the protocol: JSON messages, each ended by a NUL, written to fd 3 and read from fd 4
  const toChrome = proc.stdio[3], fromChrome = proc.stdio[4];
  let next = 0, pending = Buffer.alloc(0);
  const waiting = new Map();
  const listeners = [];
  fromChrome.on("data", (chunk) => {
    pending = Buffer.concat([pending, chunk]);
    for (let end = pending.indexOf(0); end >= 0; end = pending.indexOf(0)) {
      const m = JSON.parse(pending.subarray(0, end).toString("utf8"));
      pending = pending.subarray(end + 1);
      if (m.id && waiting.has(m.id)) {
        const { resolve, reject } = waiting.get(m.id);
        waiting.delete(m.id);
        m.error ? reject(new Error(`${m.error.message} ${m.error.data ?? ""}`)) : resolve(m.result);
      } else if (m.method) {
        for (const l of listeners) l(m);
      }
    }
  });
  proc.on("exit", () => {
    for (const { reject } of waiting.values()) reject(new Error(`Chrome exited: ${said}`));
    waiting.clear();
  });
  toChrome.on("error", () => {}); // (Chrome gone: the calls waiting are refused above)
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    if (proc.exitCode !== null || proc.signalCode !== null) return reject(new Error(`Chrome exited: ${said}`));
    const id = ++next;
    waiting.set(id, { resolve, reject });
    toChrome.write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + "\0");
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
      clearTimeout(watchdog);
      process.off("exit", kill);
      process.off("SIGINT", onInt);
      process.off("SIGTERM", onTerm);
      kill();
    },
  };
  return page;
}
