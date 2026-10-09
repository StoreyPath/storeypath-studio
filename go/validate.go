package storeypath

import (
	"fmt"
	"math"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// A Problem found in a package: a stable Code a program can act on, the file and
// the ID it is about when there is one, and a Message for people.
type Problem struct {
	Code    string
	File    string
	ID      string
	Message string
}

func (p Problem) String() string { return p.Message }

// shownLength: the longest a string from a package is shown in a problem (longer
// than any ID), so a file of hostile strings cannot make problems as large.
const shownLength = 100

// clip is a string from a package as a problem shows it: cut short at shownLength.
func clip(s string) string {
	if len(s) <= shownLength {
		return s
	}
	i := shownLength
	for i > 0 && !utf8.RuneStart(s[i]) {
		i--
	}
	return s[:i] + "…"
}

// Problem codes. They never change meaning; new ones may be added.
const (
	ProblemMissingFile  = "MISSING_FILE"  // a file of the package is not in it
	ProblemBadFile      = "BAD_FILE"      // a file cannot be read as its kind
	ProblemFormat       = "FORMAT"        // not a StoreyPath package
	ProblemVersion      = "VERSION"       // a format major version this reader does not read
	ProblemCount        = "COUNT"         // the manifest's count of a file's features is wrong
	ProblemBadID        = "BAD_ID"        // an ID is not a StoreyPath ID
	ProblemDuplicateID  = "DUPLICATE_ID"  // two features share an ID
	ProblemWrongProject = "WRONG_PROJECT" // an ID is not of the package's project
	ProblemIDLevel      = "ID_LEVEL"      // an ID has the wrong number of segments for its kind
	ProblemParent       = "PARENT"        // a feature's parent is missing, or its ID does not start with it
	ProblemZone         = "ZONE"          // a zone and its space do not list each other, or a space lists another's zone (or one twice)
	ProblemOpening      = "OPENING"       // an opening joins a space that is not there, or on another floor
	ProblemObjects      = "OBJECTS"       // objects.csv does not list exactly the features
	ProblemChanges      = "CHANGES"       // changes.json does not agree with the package
	ProblemGeometry     = "GEOMETRY"      // a feature's geometry is not the kind its file holds
	ProblemScope        = "SCOPE"         // a package holds a building its scope does not list, or lacks one it does; from 0.7, not exactly one
	ProblemItem         = "ITEM"          // an item is on a floor not in the package (or not of its building), or in a space or zone not on its floor; from 0.7, without its position in its building, or with a map position or heading away from it
	ProblemItemType     = "ITEM_TYPE"     // an item's type is not in the package's catalogue
	ProblemPlacement    = "PLACEMENT"     // a building of the package, or one an item stands in, has no placement in the manifest
	ProblemValue        = "VALUE"         // a value the format does not allow: a type the manifest's types do not list, a negative capacity, a grade, mount, category or colour not of the format
	ProblemNavigation   = "NAVIGATION"    // (0.8) the walking network names a building, floor, space or zone not in the package, holds a node twice, or an edge joins a node it does not have (or two nodes another edge joins), or has no line
)

var kindLevel = map[string]string{
	"location": LevelLocation, "building": LevelBuilding, "floor": LevelFloor,
	"space": LevelObject, "zone": LevelObject, "opening": LevelObject,
}

// knownTypes: the types of spaces, zones and openings of this format, for a
// manifest that does not list them (manifest.json → types lists those a package
// may use, a later format's too).
var knownTypes = map[string][]string{
	"space": spaceTypes, "zone": spaceTypes, "opening": {"door", "opening", "window"},
}

var spaceTypes = []string{"office", "room", "meeting_room", "corridor", "lobby", "elevator", "stairs", "escalator",
	"ramp", "restroom", "kitchen", "storage", "utility", "shaft", "open_area", "unspecified", "bedroom", "living_room",
	"dining_room", "bathroom", "dressing_room", "laundry", "prayer_room", "parking", "balcony", "terrace", "open_to_below"}

// The values a property may have, as Studio's models allow them.
var (
	grades        = []string{"president", "c_level", "director", "manager", "section_head", "senior", "junior"}
	capacityFroms = []string{"review", "items"}
	mounts        = []string{"floor", "wall", "ceiling"}
	categories    = []string{"furniture", "equipment", "appliance"}
	colourRE      = regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)
)

// maxWorkplaces: the most people who work at one item (a bench of desks).
const maxWorkplaces = 100

// knownKinds: the kinds of objects.csv rows this reader knows. Rows of others (a
// later format's), and their IDs in changes.json, are left alone.
var knownKinds = map[string]bool{"project": true, "location": true, "building": true, "floor": true,
	"space": true, "zone": true, "opening": true, "item": true}

// localAgreesM: how far an item's map position may be from its position in its
// building (the package keeps 7 decimals of a degree, about a centimetre), and
// headingAgreesDeg how far its heading may be from the way it faces in its building
// (each kept to 2 decimals).
const (
	localAgreesM     = 0.05
	headingAgreesDeg = 0.5
)

// kindRoles: the role of the file each kind of feature is in.
var kindRoles = map[string]string{
	"location": "location", "building": "buildings", "floor": "floors",
	"space": "spaces", "zone": "zones", "opening": "openings", "item": "items",
}

// Validate checks the package as Studio's validator does: its files, the
// manifest's counts, every ID and every reference between features, objects.csv
// and changes.json, every building's placement, and the items (format 0.6): their
// IDs, the floor, space and zone each stands in, its type in the catalogue, and
// (0.7) their position in their building, which their map position must agree
// with through the building's placement. From 0.7 a package holds one building.
// The walking network (0.8), when there is one: what its nodes are on and in, and
// what its edges join.
// No problems means the package can be linked to as it is.
func (p *Package) Validate() []Problem {
	out := append([]Problem(nil), p.problems...)
	add := func(code, file, id, format string, args ...any) {
		for i, arg := range args {
			if s, ok := arg.(string); ok {
				args[i] = clip(s)
			}
		}
		out = append(out, Problem{Code: code, File: clip(file), ID: clip(id), Message: fmt.Sprintf(format, args...)})
	}
	m := p.Manifest
	// the files, where the manifest's files say
	buildingsFile, floorsFile, spacesFile, zonesFile := p.file("buildings"), p.file("floors"), p.file("spaces"), p.file("zones")
	openingsFile, itemsFile, objectsFile, changesFile := p.file("openings"), p.file("items"), p.file("objects"), p.file("changes")
	fileOf := func(kind string) string { return p.file(kindRoles[kind]) }
	if m.Format != FormatName {
		add(ProblemFormat, FileManifest, "", "unknown format %q", m.Format)
	}
	if err := CheckVersion(m.FormatVersion); err != nil {
		add(ProblemVersion, FileManifest, "", "%v", err)
	}
	project := m.Project.ID
	// values: the types the manifest lists for each kind (else this format's), and
	// what a space's or zone's seating may be
	types := map[string]map[string]bool{}
	for kind, known := range knownTypes {
		names := m.Types[kind]
		if len(names) == 0 {
			names = known
		}
		types[kind] = map[string]bool{}
		for _, name := range names {
			types[kind][name] = true
		}
	}
	typed := func(kind, file, id, typ string) {
		if !types[kind][typ] {
			add(ProblemValue, file, id, "%s: type %q is not one of the %s types of the package", id, typ, kind)
		}
	}
	seated := func(file, id string, s Seating) {
		if s.Capacity != nil && *s.Capacity < 0 {
			add(ProblemValue, file, id, "%s: capacity %d is less than none", id, *s.Capacity)
		}
		if s.CapacityFrom != nil && !slices.Contains(capacityFroms, *s.CapacityFrom) {
			add(ProblemValue, file, id, "%s: capacity_from %q is not review or items", id, *s.CapacityFrom)
		} else if s.CapacityFrom != nil && s.Capacity == nil {
			add(ProblemValue, file, id, "%s: capacity_from %q with no capacity", id, *s.CapacityFrom)
		} else if s.CapacityFrom == nil && s.Capacity != nil {
			add(ProblemValue, file, id, "%s: capacity %d with no capacity_from", id, *s.Capacity)
		}
		if s.Grade != nil && !slices.Contains(grades, *s.Grade) {
			add(ProblemValue, file, id, "%s: grade %q is not one of %s", id, *s.Grade, strings.Join(grades, ", "))
		}
	}
	oneBuilding := minor(m.FormatVersion) >= oneBuildingFrom
	// an item's ID: an asset's tag (0.8), else the project's code, -I and six digits
	assetIDs := minor(m.FormatVersion) >= assetIDsFrom
	itemForm := IsItemID
	if !assetIDs {
		itemForm = isLegacyItemID
	}

	counts := map[string]int{"location": len(p.Locations), "buildings": len(p.Buildings), "floors": len(p.Floors),
		"spaces": len(p.Spaces), "zones": len(p.Zones), "openings": len(p.Openings)}
	roles := []string{"location", "buildings", "floors", "spaces", "zones", "openings"}
	for _, role := range roles {
		file := p.file(role)
		if !p.read[role] { // missing or unreadable: already a problem of its own
			continue
		}
		if got, ok := m.Counts[role]; !ok || got != counts[role] {
			add(ProblemCount, file, "", "%s: manifest counts %d, file has %d", file, m.Counts[role], counts[role])
		}
	}
	if p.read["items"] {
		if got, ok := m.Counts["items"]; !ok || got != len(p.Items) {
			add(ProblemCount, itemsFile, "", "%s: manifest counts %d, file has %d", itemsFile, m.Counts["items"], len(p.Items))
		}
	}

	ids := make([]string, 0, len(p.kinds))
	for id := range p.kinds {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		kinds := p.kinds[id]
		if len(kinds) > 1 {
			add(ProblemDuplicateID, fileOf(kinds[0]), id, "duplicate ID %s", id)
		}
		if kinds[0] == "item" { // an asset's tag (before 0.8, the project's and its number): where it is, is data
			switch {
			case !itemForm(id) && assetIDs:
				add(ProblemBadID, itemsFile, id, "%s: not an item ID (four, four and three symbols of Crockford's "+
					"base32, the last its check: 7K2Q-XM9F-4DP)", id)
			case !itemForm(id):
				add(ProblemBadID, itemsFile, id, "%s: not an item ID (the project's code, -I and six digits)", id)
			case !assetIDs && !strings.HasPrefix(id, project+"-"):
				add(ProblemWrongProject, itemsFile, id, "%s: project segment is not the package's project %s", id, project)
			}
			continue
		}
		pid, err := ParseID(id)
		if err != nil {
			add(ProblemBadID, fileOf(kinds[0]), id, "%s: %v", fileOf(kinds[0]), err)
			continue
		}
		if pid.Project() != project {
			add(ProblemWrongProject, fileOf(kinds[0]), id, "%s: project segment is not the package's project %s", id, project)
		}
		if want := kindLevel[kinds[0]]; pid.Level() != want {
			add(ProblemIDLevel, fileOf(kinds[0]), id, "%s: a %s ID needs %d segments", id, kinds[0], levelDepth(want))
		}
	}
	kindOf := func(id string) string {
		if k := p.kinds[id]; len(k) > 0 {
			return k[0]
		}
		return ""
	}
	parent := func(child, parentID, kind, file string) {
		if parentID == "" || kindOf(parentID) != kind {
			add(ProblemParent, file, child, "%s: parent %s %s is not in the package", child, kind, parentID)
		} else if !strings.HasPrefix(child, parentID+"-") {
			add(ProblemParent, file, child, "%s: ID does not start with its parent %s", child, parentID)
		}
	}
	for _, b := range p.Buildings {
		parent(b.ID, b.Location, "location", buildingsFile)
	}
	if oneBuilding { // from 0.7, a package holds one building and names it
		if m.Scope == nil || len(m.Scope.Buildings) != 1 {
			add(ProblemScope, FileManifest, "", "a package of format %s holds one building: its manifest's scope names it", m.FormatVersion)
		}
		if len(p.Buildings) != 1 {
			add(ProblemScope, buildingsFile, "", "%s: a package of format %s holds one building, this one %d",
				buildingsFile, m.FormatVersion, len(p.Buildings))
		}
	}
	if m.Scope != nil {
		listed := map[string]bool{}
		for _, id := range m.Scope.Buildings {
			listed[id] = true
			if p.Building(id) == nil {
				add(ProblemScope, FileManifest, id, "scope lists building %s, which is not in the package", id)
			}
		}
		for _, b := range p.Buildings {
			if !listed[b.ID] {
				add(ProblemScope, buildingsFile, b.ID, "building %s is in the package but not in its scope", b.ID)
			}
		}
	}
	// the placements hold one for each building: where its frame is on the map,
	// which the items standing in it are checked against
	unplaced := map[string]bool{}
	placed := func(building string) {
		if _, ok := m.Placements[building]; !ok && !unplaced[building] {
			unplaced[building] = true
			add(ProblemPlacement, FileManifest, building, "building %s has no placement", building)
		}
	}
	for _, b := range p.Buildings {
		placed(b.ID)
	}
	for _, it := range p.Items {
		placed(it.Building)
	}
	for _, b := range sortedKeys(m.Placements) { // and no other building
		if p.Building(b) == nil {
			add(ProblemPlacement, FileManifest, b, "the manifest places building %s, which is not in the package", b)
		}
	}
	for _, f := range p.Floors {
		parent(f.ID, f.Building, "building", floorsFile)
	}
	zonesOf := map[string]map[string]bool{}
	for _, s := range p.Spaces {
		parent(s.ID, s.Floor, "floor", spacesFile)
		zonesOf[s.ID] = map[string]bool{}
		for _, z := range s.Zones {
			if zonesOf[s.ID][z] {
				add(ProblemZone, spacesFile, s.ID, "%s: lists zone %s twice", s.ID, z)
			}
			zonesOf[s.ID][z] = true
			if kindOf(z) != "zone" {
				add(ProblemZone, spacesFile, s.ID, "%s: lists unknown zone %s", s.ID, z)
			} else if of := p.Zone(z).Space; of != s.ID { // a zone is part of one space
				add(ProblemZone, spacesFile, s.ID, "%s: lists zone %s, which is part of space %s", s.ID, z, of)
			}
		}
		typed("space", spacesFile, s.ID, s.Type)
		seated(spacesFile, s.ID, s.Seating)
		geometryOf(s.ID, s.Geometry, spacesFile, false, add)
	}
	for _, z := range p.Zones {
		parent(z.ID, z.Floor, "floor", zonesFile)
		if kindOf(z.Space) != "space" {
			add(ProblemZone, zonesFile, z.ID, "%s: zone of unknown space %s", z.ID, z.Space)
		} else if !zonesOf[z.Space][z.ID] {
			add(ProblemZone, zonesFile, z.ID, "%s: not listed in the zones of its space %s", z.ID, z.Space)
		}
		typed("zone", zonesFile, z.ID, z.Type)
		seated(zonesFile, z.ID, z.Seating)
		geometryOf(z.ID, z.Geometry, zonesFile, false, add)
	}
	for _, o := range p.Openings {
		parent(o.ID, o.Floor, "floor", openingsFile)
		for _, s := range o.Connects {
			if kindOf(s) != "space" {
				add(ProblemOpening, openingsFile, o.ID, "%s: connects to unknown space %s", o.ID, s)
			} else if !strings.HasPrefix(s, o.Floor+"-") {
				add(ProblemOpening, openingsFile, o.ID, "%s: connects to %s on another floor", o.ID, s)
			}
		}
		typed("opening", openingsFile, o.ID, o.Type)
		geometryOf(o.ID, o.Geometry, openingsFile, true, add)
	}
	for _, it := range p.Items {
		if kindOf(it.Building) != "building" {
			add(ProblemItem, itemsFile, it.ID, "%s: in building %s, which is not a building of the package", it.ID, it.Building)
		} else if kindOf(it.Floor) != "floor" || !strings.HasPrefix(it.Floor, it.Building+"-") {
			add(ProblemItem, itemsFile, it.ID, "%s: on unknown floor %s (or not of building %s)", it.ID, it.Floor, it.Building)
		}
		for _, in := range []struct {
			id   *string
			kind string
		}{{it.Space, "space"}, {it.Zone, "zone"}} {
			if in.id != nil && (kindOf(*in.id) != in.kind || !strings.HasPrefix(*in.id, it.Floor+"-")) {
				add(ProblemItem, itemsFile, it.ID, "%s: in unknown %s %s (or not on its floor)", it.ID, in.kind, *in.id)
			}
		}
		if p.Catalogue != nil && p.Catalogue.Type(it.Type) == nil {
			add(ProblemItemType, itemsFile, it.ID, "%s: type %s is not in the catalogue", it.ID, it.Type)
		}
		if it.Local == nil && oneBuilding {
			add(ProblemItem, itemsFile, it.ID, "%s: no position in its building (local)", it.ID)
		} else if f, err := p.Frame(it.Building); it.Local != nil && err == nil {
			at := f.ToLonLat(it.Local.X, it.Local.Y)
			dlon := math.Remainder(at[0]-it.Label[0], 360) // the short way, across the antimeridian too
			off := math.Hypot(dlon*111320*math.Cos(at[1]*math.Pi/180), (at[1]-it.Label[1])*110574)
			if off > localAgreesM {
				add(ProblemItem, itemsFile, it.ID, "%s: its map position is %.2f m from its position in its building", it.ID, off)
			}
			// its front faces rotation_deg counter-clockwise from the drawings' -y,
			// which the building's bearing turns to: on the map, clockwise from north
			faces := f.Placement.Bearing + 180 - it.Local.Rotation
			if math.Abs(math.Remainder(it.Heading-faces, 360)) > headingAgreesDeg {
				add(ProblemItem, itemsFile, it.ID, "%s: its heading %.2f° is not the way it faces in its building (%.2f°)",
					it.ID, it.Heading, math.Mod(math.Mod(faces, 360)+360, 360))
			}
		}
		if !slices.Contains(mounts, it.Mount) {
			add(ProblemValue, itemsFile, it.ID, "%s: mount %q is not floor, wall or ceiling", it.ID, it.Mount)
		}
		if !slices.Contains(categories, it.Category) {
			add(ProblemValue, itemsFile, it.ID, "%s: category %q is not furniture, equipment or appliance", it.ID, it.Category)
		}
		geometryOf(it.ID, it.Geometry, itemsFile, false, add)
		if it.Geometry != nil && it.Geometry.Type != "Polygon" {
			add(ProblemGeometry, itemsFile, it.ID, "%s: an item's footprint is a Polygon, not a %s", it.ID, it.Geometry.Type)
		}
	}

	if c := p.Catalogue; c != nil { // a colour, category or mount left out is Studio's default
		file := p.file("catalogue")
		for _, ty := range c.Types {
			if ty.Color != "" && !colourRE.MatchString(ty.Color) {
				add(ProblemValue, file, ty.Code, "%s: type %s: colour %q is not #rrggbb", file, ty.Code, ty.Color)
			}
			if ty.Category != "" && !slices.Contains(categories, ty.Category) {
				add(ProblemValue, file, ty.Code, "%s: type %s: category %q is not furniture, equipment or appliance", file, ty.Code, ty.Category)
			}
			if ty.Mount != "" && !slices.Contains(mounts, ty.Mount) {
				add(ProblemValue, file, ty.Code, "%s: type %s: mount %q is not floor, wall or ceiling", file, ty.Code, ty.Mount)
			}
			if ty.Workplaces < 0 || ty.Workplaces > maxWorkplaces {
				add(ProblemValue, file, ty.Code, "%s: type %s: workplaces %d is not 0 to %d", file, ty.Code, ty.Workplaces, maxWorkplaces)
			}
			if ty.Grade != nil && !slices.Contains(grades, *ty.Grade) {
				add(ProblemValue, file, ty.Code, "%s: type %s: grade %q is not one of %s", file, ty.Code, *ty.Grade, strings.Join(grades, ", "))
			}
		}
	}

	if n := p.navigation; n != nil {
		file := p.file("navigation")
		for _, b := range n.Buildings {
			if kindOf(b) != "building" {
				add(ProblemNavigation, file, b, "%s: building %s is not in the package", file, b)
			}
		}
		nodes := map[string]bool{}
		for _, node := range n.Nodes {
			if nodes[node.ID] {
				add(ProblemNavigation, file, node.ID, "%s: node %s is there twice", file, node.ID)
			}
			nodes[node.ID] = true
			if kindOf(node.Floor) != "floor" {
				add(ProblemNavigation, file, node.ID, "%s: node %s is on unknown floor %s", file, node.ID, node.Floor)
			}
			for _, in := range []struct {
				id   *string
				kind string
			}{{node.Space, "space"}, {node.Zone, "zone"}} {
				if in.id != nil && kindOf(*in.id) != in.kind {
					add(ProblemNavigation, file, node.ID, "%s: node %s is in unknown %s %s", file, node.ID, in.kind, *in.id)
				}
			}
		}
		pairs := map[[2]string]bool{}
		for _, e := range n.Edges {
			for _, end := range []string{e.From, e.To} {
				if !nodes[end] {
					add(ProblemNavigation, file, end, "%s: an edge joins unknown node %s", file, end)
				}
			}
			pair := [2]string{e.From, e.To}
			if pair[1] < pair[0] {
				pair = [2]string{e.To, e.From}
			}
			if pairs[pair] {
				add(ProblemNavigation, file, pair[0], "%s: two edges join %s and %s", file, pair[0], pair[1])
			}
			pairs[pair] = true
			if len(e.Path) < 2 {
				add(ProblemNavigation, file, e.From, "%s: the edge from %s to %s has no line", file, e.From, e.To)
			}
		}
	}

	unknown := map[string]bool{} // rows of a later format's kinds
	if p.files[objectsFile] {
		listed := map[string]bool{}
		for _, row := range p.Objects {
			if !knownKinds[row.Kind] {
				unknown[row.ID] = true
			} else if row.Kind != "project" {
				listed[row.ID] = true
			}
		}
		extra, missing := 0, 0
		for id := range listed {
			if _, ok := p.kinds[id]; !ok {
				extra++
			}
		}
		for id := range p.kinds {
			if !listed[id] {
				missing++
			}
		}
		if extra+missing > 0 {
			add(ProblemObjects, objectsFile, "", "%s: rows do not match the features (%d extra, %d missing)", objectsFile, extra, missing)
		}
	}

	if c := p.Changes; c != nil {
		for _, id := range append(append([]string(nil), c.Added...), c.Changed...) {
			if _, ok := p.kinds[id]; !ok && !unknown[id] {
				add(ProblemChanges, changesFile, id, "%s: %s is listed as added/changed but not in the package", changesFile, id)
			}
		}
		retired := map[string]bool{}
		for _, id := range append(append([]string(nil), c.Retired...), c.AllRetired...) {
			if _, ok := p.kinds[id]; ok && !retired[id] {
				add(ProblemChanges, changesFile, id, "%s: retired ID %s is still in the package", changesFile, id)
			}
			retired[id] = true
		}
		for _, mv := range c.MovedAway {
			if _, here := p.kinds[mv.ID]; here || !itemForm(mv.ID) {
				add(ProblemChanges, changesFile, mv.ID, "%s: %s is listed as moved away but is not an item gone from here", changesFile, mv.ID)
			}
			if _, here := p.kinds[mv.Building]; here || !strings.HasPrefix(mv.Building, project+"-") {
				add(ProblemChanges, changesFile, mv.ID, "%s: %s moved to %s, not another building of the project", changesFile, mv.ID, mv.Building)
			}
			if retired[mv.ID] {
				add(ProblemChanges, changesFile, mv.ID, "%s: %s is listed both as moved away and as retired", changesFile, mv.ID)
			}
		}
		if prev := c.PreviousSequence; prev != nil && *prev >= c.Sequence {
			add(ProblemChanges, changesFile, "", "%s: previous_sequence %d is not before sequence %d", changesFile, *prev, c.Sequence)
		}
		if a, b := c.PreviousSequence, m.Export.PreviousSequence; (a == nil) != (b == nil) || (a != nil && *a != *b) {
			add(ProblemChanges, changesFile, "", "%s: previous_sequence does not match the manifest's", changesFile)
		}
		if c.Sequence != m.Export.Sequence {
			add(ProblemChanges, changesFile, "", "%s: sequence does not match the manifest", changesFile)
		}
	}
	return out
}

// geometryOf checks a feature's geometry is a polygon (or, for openings, a point).
func geometryOf(id string, g *Geometry, file string, point bool, add func(code, file, id, format string, args ...any)) {
	if point {
		if g == nil {
			add(ProblemGeometry, file, id, "%s: no geometry (an opening's is a Point)", id)
		} else if g.Type != "Point" {
			add(ProblemGeometry, file, id, "%s: an opening's geometry is a Point, not a %s", id, g.Type)
		}
		return
	}
	if g == nil {
		add(ProblemGeometry, file, id, "%s: no geometry", id)
	} else if _, err := g.Polygons(); err != nil {
		add(ProblemGeometry, file, id, "%s: %v", id, err)
	}
}

// versionRE: a format version as the format writes it: major.minor, a patch
// number if any, then a pre-release or build after - or + (ASCII only).
var versionRE = regexp.MustCompile(`^([0-9]+)\.([0-9]+)(\.[0-9]+)?([-+][0-9A-Za-z.-]+)?$`)

// parseVersion is a format version's major and minor numbers, ok false for what
// is not a format version. A number too large to hold is taken as the largest.
func parseVersion(version string) (major, minor int, ok bool) {
	m := versionRE.FindStringSubmatch(version)
	if m == nil {
		return 0, 0, false
	}
	number := func(s string) int {
		n, err := strconv.Atoi(s)
		if err != nil { // only too large, after the pattern
			return math.MaxInt
		}
		return n
	}
	return number(m[1]), number(m[2]), true
}

// CheckVersion says whether this module reads packages of a format version: what
// is not a format version (major.minor.patch) is refused, as is one of another
// major version, or (before 1.0, where a minor version may change what a package
// means, as 0.4's scope and 0.7's one building per package did) of a newer minor
// version, with a message to update the reader. Older versions, and newer patch
// versions (properties added), are read.
func CheckVersion(version string) error {
	major, minor, ok := parseVersion(version)
	if !ok {
		return fmt.Errorf("%q is not a format version (major.minor.patch)", clip(version))
	}
	ownMajor, ownMinor, _ := parseVersion(FormatVersion)
	if major != ownMajor {
		return fmt.Errorf("unsupported format version %s (this reader reads %d.x)", clip(version), ownMajor)
	}
	if ownMajor == 0 && minor > ownMinor {
		return fmt.Errorf("format version %s is newer than this reader's %s: update the reader to read it", clip(version), FormatVersion)
	}
	return nil
}

// minor is a format version's minor number ("0.7.0" → 7). What is not a format
// version is taken as the newest, so that every rule applies to it (and Validate
// reports it).
func minor(version string) int {
	_, n, ok := parseVersion(version)
	if !ok {
		return math.MaxInt
	}
	return n
}

func levelDepth(level string) int {
	for i, l := range levels {
		if l == level {
			return i + 1
		}
	}
	return 0
}

// sortedKeys is a map's keys in order, so problems come out the same every time.
func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
