// The likely types of a room, for review mode's shortlist (1–9): what was detected, what
// vision saw, what its words say (as the rooms of the floor with the same words are, and a
// small dictionary), what rooms of its size on the floor are, its shape, and what is common
// on the floor. Each with why, in words. Pure: the floor's rooms are given.

const WORDS = {
  office: ["office", "off", "ofc", "mgr", "manager", "director", "مكتب"],
  meeting_room: ["meeting", "conf", "conference", "board", "boardroom", "mtg", "اجتماعات", "اجتماع"],
  corridor: ["corridor", "corr", "passage", "hall", "hallway", "ممر"],
  lobby: ["lobby", "reception", "foyer", "waiting", "entrance", "استقبال", "ردهة"],
  elevator: ["lift", "elevator", "elev", "مصعد"],
  stairs: ["stair", "stairs", "staircase", "درج"],
  restroom: ["wc", "toilet", "toilets", "men", "women", "ladies", "gents", "restroom", "washroom", "دورة", "حمامات"],
  kitchen: ["kitchen", "pantry", "kitchenette", "مطبخ"],
  storage: ["store", "storage", "stor", "str", "archive", "مخزن", "مستودع"],
  utility: ["elec", "electrical", "mech", "mechanical", "server", "it", "tel", "ups", "plant", "janitor", "jan", "غرفة"],
  shaft: ["shaft", "duct", "riser"],
  open_area: ["open", "workstation", "workstations", "openplan"],
  bedroom: ["bedroom", "bed", "master", "غرفة نوم", "نوم"],
  living_room: ["living", "majlis", "مجلس", "معيشة", "صالة"],
  dining_room: ["dining", "طعام"],
  bathroom: ["bath", "bathroom", "حمام"],
  dressing_room: ["dressing", "closet", "wardrobe", "ملابس"],
  laundry: ["laundry", "غسيل"],
  prayer_room: ["prayer", "mosque", "musalla", "مصلى"],
  parking: ["parking", "car", "garage", "مواقف"],
  balcony: ["balcony", "شرفة", "بلكونة"],
  terrace: ["terrace", "roof", "تراس"],
};
const COMMON = ["office", "meeting_room", "storage", "corridor", "restroom", "utility", "open_area", "lobby", "kitchen", "room"];

const tokens = (text) => new Set((text || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim().split(" ").filter((w) => w.length > 1 && !/^\d+$/.test(w)));

/** The likely types of ``s`` among ``rooms`` (the floor's, in use), most likely first:
 * [{type, why}], at most ``n``, never "unspecified". */
export function likelyTypes(s, rooms, types, n = 9) {
  const score = new Map(), why = new Map();
  const add = (t, v, reason) => {
    if (!t || t === "unspecified" || !types.includes(t)) return;
    score.set(t, (score.get(t) || 0) + v);
    if (reason && !(why.get(t) || []).includes(reason)) why.set(t, [...(why.get(t) || []), reason]);
  };
  add(s.detected?.type, 6, "detected so");
  if (s.type !== s.detected?.type) add(s.type, 3, "as it is now");
  for (const r of s.reasons || []) {
    const m = /typed ([a-z ]+?) from/.exec(r);
    if (m) add(m[1].trim().replaceAll(" ", "_"), 5, "vision saw it");
  }
  const mine = new Set([...tokens(s.drawing_label), ...tokens(s.name)]);
  const known = rooms.filter((o) => o.id !== s.id && o.type !== "unspecified" && !(o.reasons || []).length);
  if (mine.size) {
    for (const [type, words] of Object.entries(WORDS)) {
      const hit = words.find((w) => mine.has(w));
      if (hit) add(type, 4, `“${hit}” in its name`);
    }
    const byWords = new Map();
    for (const o of known) {
      const theirs = new Set([...tokens(o.drawing_label), ...tokens(o.name)]);
      const shared = [...mine].filter((w) => theirs.has(w));
      if (!shared.length) continue;
      const v = byWords.get(o.type) || { n: 0, word: shared[0] };
      v.n++;
      byWords.set(o.type, v);
    }
    for (const [type, v] of byWords) add(type, Math.min(4, 1.5 + v.n * 0.5), `${v.n} room${v.n === 1 ? "" : "s"} named “${v.word}” here ${v.n === 1 ? "is" : "are"} one`);
  }
  // rooms of about its size on the floor
  if (s.area > 0 && known.length) {
    const near = known.map((o) => ({ o, d: Math.abs(Math.log((o.area || 1) / s.area)) })).filter((x) => x.d < 0.35)
      .sort((a, b) => a.d - b.d).slice(0, 12);
    const by = new Map();
    for (const { o } of near) by.set(o.type, (by.get(o.type) || 0) + 1);
    for (const [type, k] of by) add(type, (2 * k) / near.length, `${k} of the rooms its size (${Math.round(s.area)} m²) here`);
  }
  // long and narrow: a corridor; very small: a shaft or a store
  const [w, h] = s.label_room || [0, 0];
  if (w && h && Math.max(w, h) / Math.min(w, h) > 4 && Math.min(w, h) < 3) add("corridor", 1.5, "long and narrow");
  if (s.area > 0 && s.area < 2.5) {
    add("shaft", 1.2, "very small");
    add("storage", 1, "very small");
  }
  // what is common on the floor
  const count = new Map();
  for (const o of rooms) if (o.type !== "unspecified") count.set(o.type, (count.get(o.type) || 0) + 1);
  const total = rooms.length || 1;
  for (const [type, k] of count) add(type, k / total, null);
  for (const t of COMMON) add(t, 0.01, null);
  return [...score].sort((a, b) => b[1] - a[1]).slice(0, n)
    .map(([type]) => ({ type, why: (why.get(type) || [`common on this floor`]).join(" · ") }));
}

