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
	Type     string  `json:"type"`
	Name     *string `json:"name"`
	Number   *string `json:"number"`
	// DrawingLabel is the text written in it on the drawing, as written (never
	// corrected): a key to match on, beside the ID. Nil when there is none.
	DrawingLabel *string  `json:"drawing_label"`
	Floor        string   `json:"floor_id"`
	Area         float64  `json:"area_m2"`
	Label        LonLat   `json:"display_point"`
	Zones        []string `json:"zones"`
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
	// DrawingLabel: as for a space.
	DrawingLabel *string `json:"drawing_label"`
	Space        string  `json:"space_id"`
	Floor        string  `json:"floor_id"`
	Area         float64 `json:"area_m2"`
	Label        LonLat  `json:"display_point"`
	Hidden       bool    `json:"hidden"`
	Ignored      bool    `json:"ignored"`
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
	// DrawingLabel is the text the drawing writes in it (never corrected).
	DrawingLabel *string
	Area         float64
	Label        LonLat
	// Geometry: the zone's, or the space's.
	Geometry *Geometry
	Hidden   bool
	Ignored  bool
}

// Item is a piece of furniture or equipment placed on a floor (items.geojson,
// format 0.6): a desk, a photocopier, an access point, a sofa, a TV. Its ID is the
// project's code and its own number (K7Q2XM-I000142), not its place: carried to
// another room or floor it keeps it, and where it stands is in Floor, Space and
// Zone. Its geometry is its footprint.
type Item struct {
	ID       string
	Geometry *Geometry
	// Type is a code of the catalogue (DESK-MANAGER, COPIER, …); Name is that
	// type's English name, for a reader that does not read the catalogue.
	Type     string `json:"type"`
	Category string `json:"category"` // furniture, equipment or appliance
	Name     string `json:"name"`
	Floor    string `json:"floor_id"`
	Building string `json:"building_id"`
	// Space, and Zone when the space is divided, are where its middle stood when
	// the package was exported: nil when it is in none.
	Space *string `json:"space_id"`
	Zone  *string `json:"zone_id"`
	// Local (format 0.7) is where it stands in its building: what it is placed by.
	// Moving the building on the map never changes it, so a system keeping the
	// history of where items have been keeps this (and the floor), not the map
	// position. Nil in older packages: Package.ItemLocal works it out from Label
	// and Heading.
	Local *ItemLocal `json:"local"`
	// Label is its middle on the map, and Heading the way its front faces (where a
	// desk's user sits) in degrees clockwise from north: both follow from Local and
	// the building's placement.
	Label   LonLat  `json:"display_point"`
	Heading float64 `json:"heading"`
	// Width along its front, Depth front to back, Height (m).
	Width  float64 `json:"width_m"`
	Depth  float64 `json:"depth_m"`
	Height float64 `json:"height_m"`
	// Mount: floor, wall or ceiling. Elevation is how high above the floor its
	// bottom is; nil for one on the ceiling (just under it).
	Mount     string   `json:"mount"`
	Elevation *float64 `json:"elevation_m"`
	// Values are its details entered in StoreyPath, by the catalogue's field keys:
	// strings, or numbers as float64. The fields the system that manages the asset
	// owns are never in a package: that system keeps them, by the item's ID.
	Values map[string]any `json:"values"`
}

// ItemLocal is where an item stands in its building's own frame: its middle (X,
// Y: metres from the origin of the building's drawings, shared by all its floors)
// and the way its front faces (Rotation: degrees counter-clockwise, 0 the
// drawings' -Y, 90 their +X).
type ItemLocal struct {
	X        float64 `json:"x_m"`
	Y        float64 `json:"y_m"`
	Rotation float64 `json:"rotation_deg"`
}

// Catalogue is catalogue.json (format 0.6): the types of items, the organization's,
// the same for every project.
type Catalogue struct {
	Format        string     `json:"format"`
	FormatVersion int        `json:"format_version"`
	Types         []ItemType `json:"types"`
}

// Type is the item type with a code, or nil.
func (c *Catalogue) Type(code string) *ItemType {
	if c == nil {
		return nil
	}
	for i := range c.Types {
		if c.Types[i].Code == code {
			return &c.Types[i]
		}
	}
	return nil
}

// ItemType is a type of item. Its Code is kept for good and never given to another
// type: a type no longer used is Retired, not removed.
type ItemType struct {
	Code     string `json:"code"`
	NameEN   string `json:"name_en"`
	NameAR   string `json:"name_ar"`
	Category string `json:"category"`
	// Width along its front, Depth front to back, Height (m).
	Width  float64 `json:"width"`
	Depth  float64 `json:"depth"`
	Height float64 `json:"height"`
	Mount  string  `json:"mount"`
	// Elevation is its bottom above the floor; nil: on the floor, 1.2 m up a wall,
	// just under the ceiling.
	Elevation *float64    `json:"elevation"`
	Color     string      `json:"color"` // #rrggbb
	Fields    []ItemField `json:"fields"`
	Retired   bool        `json:"retired"`
}

// ItemField is a detail the items of a type carry, and who enters it.
type ItemField struct {
	Key     string   `json:"key"`
	NameEN  string   `json:"name_en"`
	NameAR  string   `json:"name_ar"`
	Kind    string   `json:"kind"` // text, number, choice or color
	Choices []string `json:"choices"`
	// Owner is OwnerStoreyPath (what is physical: a colour, a model) or OwnerSystem
	// (the system that manages the asset: its network, its asset tag).
	Owner string `json:"owner"`
}

// Who enters a field of an item type.
const (
	OwnerStoreyPath = "storeypath"
	OwnerSystem     = "system"
)
