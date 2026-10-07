package storeypath

import (
	"archive/zip"
	"bytes"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
)

// FormatName and FormatVersion: the format this module reads. Packages of the
// same major version are read; properties and files it does not know are ignored.
const (
	FormatName    = "storeypath-package"
	FormatVersion = "0.6.0"
)

// The files of a package, by role. Items and the catalogue (format 0.6) are found
// through the manifest's files ("items", "catalogue"), and are not in older packages.
const (
	FileManifest  = "manifest.json"
	FileLocation  = "location.geojson"
	FileBuildings = "buildings.geojson"
	FileFloors    = "floors.geojson"
	FileSpaces    = "spaces.geojson"
	FileZones     = "zones.geojson"
	FileOpenings  = "openings.geojson"
	FileObjects   = "objects.csv"
	FileChanges   = "changes.json"
	FileItems     = "items.geojson"
	FileCatalogue = "catalogue.json"
)

// Manifest is manifest.json: what the package is and holds.
type Manifest struct {
	Format        string `json:"format"`
	FormatVersion string `json:"format_version"`
	Generator     struct {
		Name    string `json:"name"`
		Version string `json:"version"`
	} `json:"generator"`
	Project struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	} `json:"project"`
	Export struct {
		// Sequence: 1 for the project's first export, then 2, 3, …
		Sequence         int    `json:"sequence"`
		ExportedAt       string `json:"exported_at"`
		PreviousSequence *int   `json:"previous_sequence"`
	} `json:"export"`
	CRS        string               `json:"crs"`
	LengthUnit string               `json:"length_unit"`
	Files      map[string]string    `json:"files"`
	Counts     map[string]int       `json:"counts"`
	Types      map[string][]string  `json:"types"`
	Sources    []Source             `json:"sources"`
	Placements map[string]Placement `json:"placements"`
	// Scope: the buildings the package holds when it is not the whole project
	// (format 0.4); nil, all of them. What is outside it is not in the package, and
	// its absence says nothing about it: see Holds.
	Scope *Scope `json:"scope,omitempty"`
}

// Scope is the part of a project a package holds.
type Scope struct {
	Buildings []string `json:"buildings"`
}

// Source is the drawing a floor was read from (its file name only).
type Source struct {
	Floor  string  `json:"floor_id"`
	File   string  `json:"file"`
	SHA256 *string `json:"sha256"`
}

// Placement is where a building is on earth: its anchor (local X, Y in drawing
// metres) at (Lon, Lat), with the drawing's +Y turned to Bearing degrees.
type Placement struct {
	Lon        float64 `json:"lon"`
	Lat        float64 `json:"lat"`
	X          float64 `json:"x"`
	Y          float64 `json:"y"`
	Bearing    float64 `json:"bearing"`
	Projection string  `json:"projection"`
	// Placed is false for a building not placed on the map yet: exported around
	// 0°N 0°E with its true shape and size, its position on earth unknown.
	Placed *bool `json:"placed"`
}

// IsPlaced says whether the building's position on earth is known.
func (p Placement) IsPlaced() bool { return p.Placed == nil || *p.Placed }

// Changes is changes.json: what changed since the project's previous export.
type Changes struct {
	Sequence         int      `json:"sequence"`
	PreviousSequence *int     `json:"previous_sequence"`
	Added            []string `json:"added"`
	Changed          []string `json:"changed"`
	Retired          []string `json:"retired"`
	// AllRetired: every ID the project has ever retired, so a system that skipped
	// an export can clean up its links.
	AllRetired []string `json:"all_retired"`
}

// Object is a row of objects.csv: every ID in the package, flat.
type Object struct {
	ID, Kind, Type, Name, Number                           string
	Project, Location, Building, Floor, FloorOrdinal, Area string
	Lon, Lat, Hidden, Ignored, Space                       string
	DrawingLabel                                           string
}

// Limits on what Open reads, against broken or hostile files.
type Limits struct {
	MaxFiles      int   // entries in the ZIP
	MaxFileBytes  int64 // one file, uncompressed
	MaxTotalBytes int64 // the files read, uncompressed
}

// DefaultLimits are generous for a large campus.
var DefaultLimits = Limits{MaxFiles: 1000, MaxFileBytes: 512 << 20, MaxTotalBytes: 2 << 30}

// Package is an opened package. Its features are in file order; the lookups find
// them by ID, floor, building and space.
type Package struct {
	Manifest  Manifest
	Changes   *Changes // nil when the package has none (Validate says so)
	Objects   []Object
	Locations []*Location
	Buildings []*Building
	Floors    []*Floor
	Spaces    []*Space
	Zones     []*Zone
	Openings  []*Opening
	// Items: the furniture and equipment on the floors (format 0.6), none in older
	// packages. Catalogue: their types, nil when the package has none.
	Items     []*Item
	Catalogue *Catalogue

	problems []Problem           // found while reading, reported by Validate
	files    map[string]bool     // the files in the ZIP
	read     map[string]bool     // the feature files read without a problem
	kinds    map[string][]string // id → kinds it was read as (duplicates show as more than one)
	byID     map[string]any
}

// Open reads a package file.
func Open(path string) (*Package, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	return Read(f, st.Size(), DefaultLimits)
}

// Read reads a package from a reader of size bytes, within limits. It fails only
// when the package cannot be read at all (not a ZIP, no readable manifest, too
// large); anything else is left for Validate to report.
func Read(r io.ReaderAt, size int64, limits Limits) (*Package, error) {
	z, err := zip.NewReader(r, size)
	if err != nil {
		return nil, fmt.Errorf("not a readable package: %w", err)
	}
	if len(z.File) > limits.MaxFiles {
		return nil, fmt.Errorf("the package has %d files, more than %d", len(z.File), limits.MaxFiles)
	}
	p := &Package{files: map[string]bool{}, read: map[string]bool{}, kinds: map[string][]string{}, byID: map[string]any{}}
	entries := map[string]*zip.File{}
	for _, f := range z.File {
		entries[f.Name] = f
		p.files[f.Name] = true
	}
	var total int64
	read := func(name string) ([]byte, bool, error) {
		f, ok := entries[name]
		if !ok {
			return nil, false, nil
		}
		rc, err := f.Open()
		if err != nil {
			return nil, true, err
		}
		defer rc.Close()
		data, err := io.ReadAll(io.LimitReader(rc, limits.MaxFileBytes+1))
		if err != nil {
			return nil, true, err
		}
		if int64(len(data)) > limits.MaxFileBytes {
			return nil, true, fmt.Errorf("%s is larger than %d bytes", name, limits.MaxFileBytes)
		}
		if total += int64(len(data)); total > limits.MaxTotalBytes {
			return nil, true, fmt.Errorf("the package is larger than %d bytes", limits.MaxTotalBytes)
		}
		return data, true, nil
	}

	data, ok, err := read(FileManifest)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, errors.New("not a package: no manifest.json")
	}
	if err := json.Unmarshal(data, &p.Manifest); err != nil {
		return nil, fmt.Errorf("manifest.json: %w", err)
	}

	collections := []struct {
		file string
		read func([]byte) error
	}{
		{FileLocation, func(b []byte) error {
			return decode(b, "location", &p.Locations, func(f *Location, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{FileBuildings, func(b []byte) error {
			return decode(b, "building", &p.Buildings, func(f *Building, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{FileFloors, func(b []byte) error {
			return decode(b, "floor", &p.Floors, func(f *Floor, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{FileSpaces, func(b []byte) error {
			return decode(b, "space", &p.Spaces, func(f *Space, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{FileZones, func(b []byte) error {
			return decode(b, "zone", &p.Zones, func(f *Zone, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{FileOpenings, func(b []byte) error {
			return decode(b, "opening", &p.Openings, func(f *Opening, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
	}
	for _, c := range collections {
		data, ok, err := read(c.file)
		if err != nil {
			return nil, err
		}
		if !ok {
			p.problems = append(p.problems, Problem{Code: ProblemMissingFile, File: c.file, Message: "missing file " + c.file})
			continue
		}
		if err := c.read(data); err != nil {
			p.problems = append(p.problems, Problem{Code: ProblemBadFile, File: c.file, Message: c.file + ": " + err.Error()})
		} else {
			p.read[c.file] = true
		}
	}

	// furniture and equipment, and their types (format 0.6), when the manifest lists them
	if name := p.Manifest.Files["items"]; name != "" {
		data, ok, err := read(name)
		if err != nil {
			return nil, err
		}
		if !ok {
			p.problems = append(p.problems, Problem{Code: ProblemMissingFile, File: name, Message: "missing file " + name})
		} else if err := decode(data, "item", &p.Items, func(f *Item, id string, g *Geometry) { f.ID, f.Geometry = id, g }); err != nil {
			p.problems = append(p.problems, Problem{Code: ProblemBadFile, File: name, Message: name + ": " + err.Error()})
		} else {
			p.read[name] = true
		}
	}
	if name := p.Manifest.Files["catalogue"]; name != "" {
		data, ok, err := read(name)
		if err != nil {
			return nil, err
		}
		var c Catalogue
		if !ok {
			p.problems = append(p.problems, Problem{Code: ProblemMissingFile, File: name, Message: "missing file " + name})
		} else if err := json.Unmarshal(data, &c); err != nil {
			p.problems = append(p.problems, Problem{Code: ProblemBadFile, File: name, Message: name + ": " + err.Error()})
		} else {
			p.Catalogue = &c
		}
	}

	if data, ok, err := read(FileObjects); err != nil {
		return nil, err
	} else if !ok {
		p.problems = append(p.problems, Problem{Code: ProblemMissingFile, File: FileObjects, Message: "missing file " + FileObjects})
	} else if p.Objects, err = readObjects(data); err != nil {
		p.problems = append(p.problems, Problem{Code: ProblemBadFile, File: FileObjects, Message: err.Error()})
	}
	if data, ok, err := read(FileChanges); err != nil {
		return nil, err
	} else if !ok {
		p.problems = append(p.problems, Problem{Code: ProblemMissingFile, File: FileChanges, Message: "missing file " + FileChanges})
	} else {
		var c Changes
		if err := json.Unmarshal(data, &c); err != nil {
			p.problems = append(p.problems, Problem{Code: ProblemBadFile, File: FileChanges, Message: err.Error()})
		} else {
			p.Changes = &c
		}
	}
	p.index()
	return p, nil
}

// decode reads a feature collection into features of one kind.
func decode[T any](data []byte, kind string, out *[]*T, set func(*T, string, *Geometry)) error {
	var fc struct {
		Type     string `json:"type"`
		Features []struct {
			ID         string          `json:"id"`
			Geometry   *Geometry       `json:"geometry"`
			Properties json.RawMessage `json:"properties"`
		} `json:"features"`
	}
	if err := json.Unmarshal(data, &fc); err != nil {
		return err
	}
	if fc.Type != "FeatureCollection" {
		return fmt.Errorf("not a FeatureCollection")
	}
	for i, f := range fc.Features {
		var k struct {
			Kind string `json:"kind"`
		}
		if err := json.Unmarshal(f.Properties, &k); err != nil {
			return fmt.Errorf("feature %d (%s): %w", i, f.ID, err)
		}
		if k.Kind != kind {
			return fmt.Errorf("feature %d (%s): kind %q, expected %q", i, f.ID, k.Kind, kind)
		}
		v := new(T)
		if err := json.Unmarshal(f.Properties, v); err != nil {
			return fmt.Errorf("feature %d (%s): %w", i, f.ID, err)
		}
		set(v, f.ID, f.Geometry)
		*out = append(*out, v)
	}
	return nil
}

func readObjects(data []byte) ([]Object, error) {
	r := csv.NewReader(bytes.NewReader(data))
	rows, err := r.ReadAll()
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, errors.New("no header row")
	}
	col := map[string]int{}
	for i, name := range rows[0] {
		col[strings.TrimSpace(name)] = i
	}
	get := func(row []string, name string) string {
		if i, ok := col[name]; ok && i < len(row) {
			return row[i]
		}
		return ""
	}
	out := make([]Object, 0, len(rows)-1)
	for _, row := range rows[1:] {
		out = append(out, Object{
			ID: get(row, "id"), Kind: get(row, "kind"), Type: get(row, "type"), Name: get(row, "name"),
			Number: get(row, "number"), Project: get(row, "project_id"), Location: get(row, "location_id"),
			Building: get(row, "building_id"), Floor: get(row, "floor_id"), FloorOrdinal: get(row, "floor_ordinal"),
			Area: get(row, "area_m2"), Lon: get(row, "lon"), Lat: get(row, "lat"), Hidden: get(row, "hidden"),
			Ignored: get(row, "ignored"), Space: get(row, "space_id"), DrawingLabel: get(row, "drawing_label"),
		})
	}
	return out, nil
}

func (p *Package) index() {
	add := func(id, kind string, f any) {
		p.kinds[id] = append(p.kinds[id], kind)
		if _, seen := p.byID[id]; !seen {
			p.byID[id] = f
		}
	}
	for _, f := range p.Locations {
		add(f.ID, "location", f)
	}
	for _, f := range p.Buildings {
		add(f.ID, "building", f)
	}
	for _, f := range p.Floors {
		add(f.ID, "floor", f)
	}
	for _, f := range p.Spaces {
		add(f.ID, "space", f)
	}
	for _, f := range p.Zones {
		add(f.ID, "zone", f)
	}
	for _, f := range p.Openings {
		add(f.ID, "opening", f)
	}
	for _, f := range p.Items {
		add(f.ID, "item", f)
	}
}

// Get is the feature with an ID: a *Location, *Building, *Floor, *Space, *Zone,
// *Opening or *Item.
func (p *Package) Get(id string) (any, bool) {
	f, ok := p.byID[id]
	return f, ok
}

// Holds reports whether the package holds a building, so that what it says about
// the building (its floors, spaces, what changed) is the whole truth: always, for a
// package of the whole project; for a part of one, when the building is in its
// scope. A system keeping a project's buildings leaves the others as they are.
func (p *Package) Holds(buildingID string) bool {
	if p.Manifest.Scope == nil {
		return true
	}
	for _, b := range p.Manifest.Scope.Buildings {
		if b == buildingID {
			return true
		}
	}
	return false
}

// Building, Floor, Space, Zone, Opening and Item find a feature of that kind by ID
// (nil when there is none).
func (p *Package) Building(id string) *Building { f, _ := p.byID[id].(*Building); return f }
func (p *Package) Floor(id string) *Floor       { f, _ := p.byID[id].(*Floor); return f }
func (p *Package) Space(id string) *Space       { f, _ := p.byID[id].(*Space); return f }
func (p *Package) Zone(id string) *Zone         { f, _ := p.byID[id].(*Zone); return f }
func (p *Package) Opening(id string) *Opening   { f, _ := p.byID[id].(*Opening); return f }
func (p *Package) Item(id string) *Item         { f, _ := p.byID[id].(*Item); return f }

// ItemsOn is the furniture and equipment on a floor, in file order. An item's ID
// does not say where it is: its Floor does.
func (p *Package) ItemsOn(floorID string) []*Item {
	var out []*Item
	for _, it := range p.Items {
		if it.Floor == floorID {
			out = append(out, it)
		}
	}
	return out
}

// ItemType is the catalogue's type with a code (an item's Type), or nil when the
// package has no catalogue or no such type.
func (p *Package) ItemType(code string) *ItemType { return p.Catalogue.Type(code) }

// FloorsOf is a building's floors, lowest first.
func (p *Package) FloorsOf(buildingID string) []*Floor {
	var out []*Floor
	for _, f := range p.Floors {
		if f.Building == buildingID {
			out = append(out, f)
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Ordinal < out[j].Ordinal })
	return out
}

// SpacesOn is a floor's spaces, ZonesOf a space's zones, OpeningsOn a floor's
// openings, in file order.
func (p *Package) SpacesOn(floorID string) []*Space {
	var out []*Space
	for _, s := range p.Spaces {
		if s.Floor == floorID {
			out = append(out, s)
		}
	}
	return out
}

func (p *Package) ZonesOf(spaceID string) []*Zone {
	var out []*Zone
	if s := p.Space(spaceID); s != nil {
		for _, id := range s.Zones {
			if z := p.Zone(id); z != nil {
				out = append(out, z)
			}
		}
	}
	return out
}

func (p *Package) OpeningsOn(floorID string) []*Opening {
	var out []*Opening
	for _, o := range p.Openings {
		if o.Floor == floorID {
			out = append(out, o)
		}
	}
	return out
}

// UnitsOn is what people are placed in on a floor: the zones of a space divided
// into zones, and every space with none, in file order.
func (p *Package) UnitsOn(floorID string) []Unit {
	var out []Unit
	for _, s := range p.SpacesOn(floorID) {
		zones := p.ZonesOf(s.ID)
		if len(zones) == 0 {
			out = append(out, Unit{ID: s.ID, Kind: "space", Space: s, Type: s.Type, Name: s.Name, Number: s.Number,
				Area: s.Area, Label: s.Label, Geometry: s.Geometry, Hidden: s.Hidden, Ignored: s.Ignored,
				DrawingLabel: s.DrawingLabel})
			continue
		}
		for _, z := range zones {
			out = append(out, Unit{ID: z.ID, Kind: "zone", Space: s, Zone: z, Type: z.Type, Name: z.Name,
				Number: z.Number, Area: z.Area, Label: z.Label, Geometry: z.Geometry, Hidden: z.Hidden,
				Ignored: z.Ignored, DrawingLabel: z.DrawingLabel})
		}
	}
	return out
}

// Frame is the local frame of a building: its placement, to turn lon/lat back
// into the drawing metres Studio works in.
func (p *Package) Frame(buildingID string) (LocalFrame, error) {
	pl, ok := p.Manifest.Placements[buildingID]
	if !ok {
		return LocalFrame{}, fmt.Errorf("no placement for %s", buildingID)
	}
	return NewLocalFrame(pl), nil
}
