// Declarations of ids.js: items' IDs (format 0.8), as spec/FORMAT.md ("Asset IDs")
// describes them.

/** Crockford's base32: the symbols of an item's ID (no I, L, O or U). */
export declare const ITEM_ID_ALPHABET: string;

/** The check symbol (Luhn mod 32) of an item ID's ten symbols, or null when they are
 * not ten symbols of the alphabet, upper case. */
export declare function itemCheckSymbol(symbols: string): string | null;

/** Whether a value is an item's ID as packages write it: 7K2Q-XM9F-4DP, its check right. */
export declare function isItemId(value: unknown): value is string;

/** An item's ID as a person typed it ("7k2q xm9f 4dp", O for 0, I and L for 1), as it is
 * written ("7K2Q-XM9F-4DP"); null when it is not one. */
export declare function normalizeItemId(text: unknown): string | null;
