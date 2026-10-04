// Default colors per space type. Override any of them with the `colors` option.

export const TYPE_COLORS = {
  office: "#7aa6d8",
  room: "#9db8d2",
  meeting_room: "#b39ddb",
  corridor: "#e9e5da",
  lobby: "#f2d388",
  elevator: "#e07a5f",
  stairs: "#e8a15c",
  escalator: "#d4876a",
  ramp: "#c9a26b",
  restroom: "#81c7c0",
  kitchen: "#a5d6a7",
  storage: "#bcaaa4",
  utility: "#a3a3ab",
  shaft: "#86868e",
  open_area: "#a8d4f7",
  unspecified: "#ff4d6d",
};

// Circulation areas are drawn as floor, not as raised blocks.
export const FLAT_TYPES = ["corridor", "lobby", "open_area"];

export function typeLabel(type) {
  return type === "unspecified" ? "needs review" : type.replaceAll("_", " ");
}
