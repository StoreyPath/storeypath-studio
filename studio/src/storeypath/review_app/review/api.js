// Studio's calls, as Review makes them: GETs, and changes (POST) with the header a page of
// another site cannot send; a floor someone else is editing heard of (423); jobs followed on
// the floor's stream (together.js). Who is told what is Review's (notify.js).

import { sentAway } from "../account.js";
import { PAGE, followJob as followOnStream, heardLocked } from "../together.js";
import { say } from "./notify.js";

const watchers = new Set(); // told of every change sent: ("saving" | "saved" | "failed", error?)
let sending = 0;

/** ``fn(state, error)`` at each change sent, and when it is saved or failed. */
export function onSaving(fn) {
  watchers.add(fn);
}

function tell(what, error) {
  for (const fn of watchers) fn(what, error, sending);
}

export async function request(path, body) {
  const init = body === undefined ? { headers: { "X-StoreyPath-Page": PAGE } } : {
    method: "POST",
    // the header a page of another site cannot send (server.py refuses changes without it)
    headers: { "X-StoreyPath": "1", "Content-Type": "application/json", "X-StoreyPath-Page": PAGE },
    body: JSON.stringify(body),
  };
  const change = body !== undefined;
  if (change) {
    sending++;
    tell("saving");
  }
  try {
    const res = await fetch(`/api/${path}`, init);
    const data = await res.json().catch(() => ({}));
    if (sentAway(res, data)) throw new Error("log in again");
    if (res.status === 423) heardLocked(data.locked); // someone else is editing the floor: shown so
    if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
    if (change) {
      sending--;
      tell("saved");
    }
    return data;
  } catch (e) {
    if (change) {
      sending--;
      tell("failed", e);
    }
    throw e;
  }
}

/** Wait for a server job, saying what it is doing; resolves with the job when done (its
 * progress comes on the floor's stream: together.js). */
export function followJob(job, saying) {
  return followOnStream(job, (line) => say(line === null ? "" : line || saying));
}
