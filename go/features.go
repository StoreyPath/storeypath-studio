package storeypath

// The features of a package, by file. Each has its StoreyPath ID, its geometry
// and its properties. Properties a later format version adds are ignored when
// read, as the format asks of every reader.

// Location is the site a project covers (location.geojson).
type Location struct {
	ID       string
	Geometry *Geometry
	Code     string  `json:"code"`
	Name     string  `json:"name"`
	Address  *string `json:"address"`
	Project  string  `json:"project_id"`
	Label    *LonLat `json:"display_point"`
}

// Building is a building of the location (buildings.geojson), its footprint.
type Building struct {
	ID       string
	Geometry *Geometry
	Code     string  `json:"code"`
	Name     string  `json:"name"`
	Location string  `json:"location_id"`
	Label    *LonLat `json:"display_point"`
}

// Floor is a storey of a building (floors.geojson), its outline.
type Floor struct {
	ID       string
	Geometry *Geometry
	Code     string `json:"code"`
	Name     string `json:"name"`
	Building string `json:"building_id"`
	// Ordinal: 0 the ground floor, negative below ground.
	Ordinal int `json:"ordinal"`
	// Elevation above the building's ground floor, and floor-to-floor height (m).
	Elevation float64 `json:"elevation"`
	Height    float64 `json:"height"`
	// Walls as drawn, with their door and window gaps; full height except the
	// parapets, which are ParapetHeight high.
	Walls         *Geometry `json:"walls"`
	WallThickness *float64  `json:"wall_thickness_m"`
	Parapets      *Geometry `json:"parapets"`
	ParapetHeight *float64  `json:"parapet_height_m"`
}

// Space is what walls, doors and windows enclose (spaces.geojson). A space with
// Zones is used through them; one without is used as a whole.
type Space struct {
	ID       string
	Geometry *Geometry
	Type     string   `json:"type"`
	Name     *string  `json:"name"`
	Number   *string  `json:"number"`
	Floor    string   `json:"floor_id"`
	Area     float64  `json:"area_m2"`
	Label    LonLat   `json:"display_point"`
	Zones    []string `json:"zones"`
	// Outdoor: open to the sky (a terrace or balcony with no windows of its own).
	Outdoor bool `json:"outdoor"`
	// Hidden: real, but not shown unless asked for. Ignored: judged not worth
	// anything by a person; best left out.
	Hidden  bool `json:"hidden"`
	Ignored bool `json:"ignored"`
}

// Zone is a named part of a space used for one thing, with no wall between it
// and the rest of the space (zones.geojson).
type Zone struct {
	ID       string
	Geometry *Geometry
	Type     string  `json:"type"`
	Name     *string `json:"name"`
	Number   *string `json:"number"`
	Space    string  `json:"space_id"`
	Floor    string  `json:"floor_id"`
	Area     float64 `json:"area_m2"`
	Label    LonLat  `json:"display_point"`
	Hidden   bool    `json:"hidden"`
	Ignored  bool    `json:"ignored"`
}

// Opening is a door, a window, or a way through with no door (openings.geojson);
// its geometry is a point in the wall.
type Opening struct {
	ID       string
	Geometry *Geometry
	// Type: door, window or opening.
	Type  string `json:"type"`
	Floor string `json:"floor_id"`
	// Connects: the one or two spaces it joins; Exterior when it leads outside.
	Connects []string `json:"connects"`
	Exterior bool     `json:"exterior"`
	Width    *float64 `json:"width_m"`
	// Span: across the wall, jamb to jamb. Swings: a door's leaves as drawn, each
	// [hinge, free edge when open].
	Span   []LonLat   `json:"span"`
	Swings [][]LonLat `json:"swings"`
	// Sill and Height from the drawing's schedule of openings, when known (m).
	Sill    *float64 `json:"sill_m"`
	Height  *float64 `json:"height_m"`
	Hidden  bool     `json:"hidden"`
	Ignored bool     `json:"ignored"`
}

// Unit is what people are placed in: a zone, or a space with no zones.
type Unit struct {
	ID     string
	Kind   string // "space" or "zone"
	Space  *Space // the space, or the space the zone is part of
	Zone   *Zone  // nil for a space
	Type   string
	Name   *string
	Number *string
	Area   float64
	Label  LonLat
	// Geometry: the zone's, or the space's.
	Geometry *Geometry
	Hidden   bool
	Ignored  bool
}
