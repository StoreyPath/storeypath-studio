// What happened, for the parts of the page that show it: each listens for what it shows
// and draws itself again (the panels, the inspector, the status bar). The plan and the 3D
// view are drawn by the code that changes them; this is for the rest.
//
//   floor      a floor opened, or read again (state.floor replaced)
//   spaces     spaces changed in place (detail: their IDs, or null: any)
//   items      the floor's items changed
//   selection  what is chosen changed
//   project    the project's floors (their counts to review) changed
//   view       2D, 3D or Walk
//   tool       the tool, or its options
//   access     who may change the floor (view only, someone else editing)
//   settings   how the floor is shown (labels, colours, deleted shown, the drawing under it)

const listeners = new Map();

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

export function emit(event, detail) {
  for (const fn of listeners.get(event) || []) {
    try {
      fn(detail);
    } catch (e) {
      console.error(`${event}:`, e); // one listener failing never stops the others
    }
  }
}
