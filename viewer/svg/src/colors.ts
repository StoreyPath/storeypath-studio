// A colour per StoreyPath space type, for spaces no style colours (the same as the
// other StoreyPath viewers, but for `unspecified`: there it is red, to be reviewed;
// here, shown to the people who use the building, it is plain). Override any of them
// with the `colors` option, or colour by anything else with `styleOf`.

export const TYPE_COLORS: Record<string, string> = {
  office: "#7aa6d8", room: "#9db8d2", meeting_room: "#b39ddb", corridor: "#e9e5da", lobby: "#f2d388",
  elevator: "#e07a5f", stairs: "#e8a15c", escalator: "#d4876a", ramp: "#c9a26b", restroom: "#81c7c0",
  kitchen: "#a5d6a7", storage: "#bcaaa4", utility: "#a3a3ab", shaft: "#86868e", open_area: "#a8d4f7",
  bedroom: "#8fb3e8", living_room: "#f0b67f", dining_room: "#e6c27a", bathroom: "#7fc6d6",
  dressing_room: "#c9b2d9", laundry: "#9cc9b4", prayer_room: "#d4b8e0", parking: "#c4c4bc",
  balcony: "#b7d9a8", terrace: "#cfe3b0", open_to_below: "#f4f4f2", unspecified: "#dedbd3",
};
