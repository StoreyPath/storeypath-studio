// Items' IDs (format 0.8, spec/FORMAT.md "Asset IDs"): an asset's tag, ten random
// symbols of Crockford's base32 and a check symbol (Luhn mod 32), written 4-4-3 with
// hyphens: 7K2Q-XM9F-4DP. Of no project and no place: a desk carried to another
// building keeps it. Shared by the viewers, and for a system's own pages (a search box
// where people type the tag on a desk), in a browser or in Node.

/** Crockford's base32: the symbols of an item's ID (no I, L, O or U). */
export const ITEM_ID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

const SYMBOLS = 10; // random; then the check symbol
const TYPED_MAX = 64; // what a person typed, before it is read: longer is not an item's ID
const IGNORED = new Set(["-", " ", "\t", "\n", "\r", "\v", "\f"]); // hyphens and spaces, wherever they are

const WRITTEN = new Map([...ITEM_ID_ALPHABET].map((c, v) => [c, v]));
// as a person may type them: either case, O for 0, I and L for 1
const TYPED = new Map([...WRITTEN, ...[...WRITTEN].map(([c, v]) => [c.toLowerCase(), v]),
  ["O", 0], ["o", 0], ["I", 1], ["i", 1], ["L", 1], ["l", 1]]);

/** Luhn mod 32: the 2nd, 4th, … 10th values doubled (a doubled value's two base-32
 * digits added: 2v − 31 from 16 on), all added, mod 32. Of ten symbols and their check, 0. */
function luhnSum(values) {
  let sum = 0;
  values.forEach((v, k) => {
    if (k % 2 === 1) {
      v *= 2;
      v = Math.floor(v / 32) + (v % 32);
    }
    sum += v;
  });
  return sum % 32;
}

function written(values) {
  const s = values.map((v) => ITEM_ID_ALPHABET[v]).join("");
  return `${s.slice(0, 4)}-${s.slice(4, 8)}-${s.slice(8)}`;
}

/**
 * The check symbol of an item ID's ten symbols (as written: upper case, Crockford's
 * base32), or null when they are not ten such symbols.
 * @param {string} symbols
 * @returns {string | null}
 */
export function itemCheckSymbol(symbols) {
  if (typeof symbols !== "string" || symbols.length !== SYMBOLS) return null;
  const values = [...symbols].map((c) => WRITTEN.get(c));
  if (values.some((v) => v === undefined)) return null;
  return ITEM_ID_ALPHABET[(32 - luhnSum(values)) % 32];
}

/**
 * Whether a value is an item's ID as packages write it: 4-4-3 symbols of Crockford's
 * base32, upper case, with hyphens, its check symbol right (7K2Q-XM9F-4DP).
 * @param {unknown} value
 * @returns {boolean}
 */
export function isItemId(value) {
  if (typeof value !== "string" || value.length !== 13 || value[4] !== "-" || value[9] !== "-") return false;
  const values = [...value.slice(0, 4) + value.slice(5, 9) + value.slice(10)].map((c) => WRITTEN.get(c));
  return !values.some((v) => v === undefined) && luhnSum(values) === 0;
}

/**
 * An item's ID as a person typed it (a search box): letters in either case, O read as
 * 0, I and L as 1, hyphens and spaces left out wherever they are; eleven symbols with
 * their check right are the ID, as written ("7k2q xm9f 4dp": "7K2Q-XM9F-4DP"). Anything
 * else is not one: null. Never for an ID read from a package (written so, or not an ID).
 * @param {unknown} text
 * @returns {string | null}
 */
export function normalizeItemId(text) {
  if (typeof text !== "string" || text.length > TYPED_MAX) return null;
  const values = [];
  for (const c of text) {
    if (IGNORED.has(c)) continue;
    const v = TYPED.get(c);
    if (v === undefined || values.length === SYMBOLS + 1) return null;
    values.push(v);
  }
  return values.length === SYMBOLS + 1 && luhnSum(values) === 0 ? written(values) : null;
}
