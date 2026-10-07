// Reading a package file (*.storeypath, a ZIP archive) in the browser or in Node,
// with what the platform has: no library to load.

import type { PackageLike } from "./package.js";

export const FORMAT = "storeypath-package";
export const SUPPORTED_MAJOR_VERSION = 0;

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
  if (Number(String(manifest.format_version).split(".")[0]) !== SUPPORTED_MAJOR_VERSION) {
    throw new Error(`Package format ${manifest.format_version} is not supported.`);
  }
  const features = async (role: string): Promise<never[]> => {
    const name = manifest.files[role];
    return name ? ((await json(name)) as { features: never[] }).features : [];
  };
  const [floors, spaces, zones, openings] = await Promise.all(["floors", "spaces", "zones", "openings"].map(features));
  return { manifest, floors: floors!, spaces: spaces!, zones: zones!, openings: openings! };
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
