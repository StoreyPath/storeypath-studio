// Reading a package file (*.storeypath, a ZIP archive) in the browser or in Node,
// with what the platform has: no library to load.

import type { PackageLike } from "./package.js";

export const FORMAT = "storeypath-package";
/** The format version this viewer reads, any patch of it (spec/FORMAT.md, "Versioning"). */
export const FORMAT_VERSION = "0.7";
export const SUPPORTED_MAJOR_VERSION = Number(FORMAT_VERSION.split(".")[0]);

const VERSION = /^(\d+)\.(\d+)(\.\d+)?([-+][0-9A-Za-z.-]+)?$/;

/** Refuses a package's format version this viewer does not read: one that is not a
 * version, one of another major version, or (before 1.0, where a minor version may
 * change what a package means) one of a newer minor version, saying to update the
 * viewer. Older versions, and newer patches (properties added), are read. */
export function checkVersion(version: unknown): void {
  const m = typeof version === "string" ? VERSION.exec(version) : null;
  if (!m) throw new Error(`The package's format version ${JSON.stringify(version) ?? "(none)"} is not a version this viewer can read.`);
  const major = Number(m[1]), minor = Number(m[2]);
  const [MAJOR, MINOR] = FORMAT_VERSION.split(".").map(Number) as [number, number];
  if (major > MAJOR || (major === MAJOR && MAJOR === 0 && minor > MINOR)) {
    throw new Error(`This package is format ${version}, newer than this viewer's ${FORMAT_VERSION}: update the viewer.`);
  }
  if (major !== MAJOR) throw new Error(`This package is format ${version}, older than this viewer reads (${MAJOR}.x).`);
}

interface Manifest {
  format: string;
  format_version: string;
  files: Record<string, string>;
  [key: string]: unknown;
}

/** A package's manifest and features, from its bytes (a file, a download). */
export async function readPackage(source: ArrayBuffer | Uint8Array | Blob): Promise<PackageLike & { manifest: Manifest }> {
  const bytes = source instanceof Blob ? new Uint8Array(await source.arrayBuffer())
    : source instanceof Uint8Array ? source : new Uint8Array(source);
  const files = entries(bytes);
  const json = async (name: string): Promise<unknown> => {
    const entry = files.get(name);
    if (!entry) throw new Error(`The package has no ${name}.`);
    return JSON.parse(new TextDecoder().decode(await inflate(bytes, entry)));
  };
  const manifest = (await json("manifest.json")) as Manifest;
  if (manifest.format !== FORMAT) throw new Error("This is not a StoreyPath package.");
  checkVersion(manifest.format_version);
  const features = async (role: string): Promise<never[]> => {
    const name = manifest.files[role];
    return name ? ((await json(name)) as { features: never[] }).features : [];
  };
  const [floors, spaces, zones, openings, items] = await Promise.all(["floors", "spaces", "zones", "openings", "items"].map(features));
  // the types of the items (format 0.6): their colours
  const catalogue = manifest.files["catalogue"] ? ((await json(manifest.files["catalogue"])) as PackageLike["catalogue"]) : null;
  return { manifest, floors: floors!, spaces: spaces!, zones: zones!, openings: openings!, items: items!, catalogue };
}

interface Entry {
  method: number;
  start: number;
  size: number;
}

function entries(b: Uint8Array): Map<string, Entry> {
  const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
  let end = b.length - 22;
  while (end >= 0 && v.getUint32(end, true) !== 0x06054b50) end--;
  if (end < 0) throw new Error("This is not a StoreyPath package (not a ZIP archive).");
  const out = new Map<string, Entry>();
  let p = v.getUint32(end + 16, true);
  const names = new TextDecoder();
  for (let i = v.getUint16(end + 10, true); i > 0; i--) {
    if (v.getUint32(p, true) !== 0x02014b50) throw new Error("The package is damaged (its ZIP directory).");
    const nameLength = v.getUint16(p + 28, true);
    const local = v.getUint32(p + 42, true);
    out.set(names.decode(b.subarray(p + 46, p + 46 + nameLength)), {
      method: v.getUint16(p + 10, true),
      start: local + 30 + v.getUint16(local + 26, true) + v.getUint16(local + 28, true),
      size: v.getUint32(p + 20, true),
    });
    p += 46 + nameLength + v.getUint16(p + 30, true) + v.getUint16(p + 32, true);
  }
  return out;
}

async function inflate(b: Uint8Array, e: Entry): Promise<Uint8Array> {
  const raw = b.slice(e.start, e.start + e.size);
  if (e.method === 0) return raw;
  if (e.method !== 8) throw new Error(`The package uses a ZIP compression this reader does not know (${e.method}).`);
  const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
