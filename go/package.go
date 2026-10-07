package storeypath

import (
	"archive/zip"
	"bytes"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strings"
)

// FormatName and FormatVersion: the format this module reads. Packages of the
// same major version are read; properties, files and objects.csv rows of kinds it
// does not know are ignored.
const (
	FormatName    = "storeypath-package"
	FormatVersion = "0.7.0"
)

// oneBuildingFrom: from format 0.7 a package holds exactly one building.
const oneBuildingFrom = 7

// The files of a package, by the names they usually have. A reader finds each
// through the manifest's files, by its role; the names are for a manifest that
// does not list a file every package has. Items and the catalogue (format 0.6) are
// read only when it lists them ("items", "catalogue"): older packages have none.
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

// usualNames: the files of a package by role, where they are when the manifest's
// files do not say.
var usualNames = map[string]string{
	"location": FileLocation, "buildings": FileBuildings, "floors": FileFloors, "spaces": FileSpaces,
	"zones": FileZones, "openings": FileOpenings, "objects": FileObjects, "changes": FileChanges,
	"items": FileItems, "catalogue": FileCatalogue,
}

// optionalRoles: files a package has only when its manifest lists them.
var optionalRoles = map[string]bool{"items": true, "catalogue": true}

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
	// Scope: the building the package holds (from format 0.7, always exactly one;
	// from 0.4, the buildings it held when not the whole project; nil before, or for
	// a whole project). What is outside it is not in the package, and its absence
	// says nothing about it: see Holds.
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
	// MovedAway (format 0.7): items in this building when it was last exported,
	// carried since to another building of the project. They are not retired: that
	// building's package holds them when it is next exported.
	MovedAway []MovedAway `json:"moved_away"`
}

// MovedAway is an item carried to another building of the project.
type MovedAway struct {
	ID       string `json:"id"`
	Building string `json:"building_id"`
}

// Object is a row of objects.csv: every ID in the package, flat.
type Object struct {
	ID, Kind, Type, Name, Number                           string
	Project, Location, Building, Floor, FloorOrdinal, Area string
	Lon, Lat, Hidden, Ignored, Space                       string
	DrawingLabel                                           string
}

// Limits on what Read takes in, against broken or hostile files. A server reading
// uploaded packages should set them from the size of upload it accepts. A field
// left zero takes DefaultLimits' value.
type Limits struct {
	MaxFiles      int   // entries in the ZIP
	MaxFileBytes  int64 // one file, uncompressed
	MaxTotalBytes int64 // the files read, uncompressed
	// MaxRatio: how many times its size in the ZIP one file may be, uncompressed
	// (a package's files are 10 to 20 times); a file of up to 1 MiB may be more.
	MaxRatio int64
	// MaxFeatures: the entries of any one list in a JSON file (the features of a
	// collection, the points of a ring, the IDs of a list in changes.json, the
	// types of the catalogue). objects.csv, which lists every feature, may have
	// this many rows more than the features read.
	MaxFeatures int
}

// DefaultLimits are generous for the largest building: its package is a few MB.
var DefaultLimits = Limits{MaxFiles: 1000, MaxFileBytes: 64 << 20, MaxTotalBytes: 256 << 20, MaxRatio: 100, MaxFeatures: 200_000}

// ratioFloor: a file this small is not held to MaxRatio (a short file of repeated
// text may compress far better than a package's files do).
const ratioFloor = 1 << 20

// ErrTooLarge is wrapped by the error Read returns for a package over its Limits,
// so that a server can tell a package too large to read from a broken one.
var ErrTooLarge = errors.New("too large")

// orDefault is the limits with each field left zero (or negative) taken from
// DefaultLimits.
func (l Limits) orDefault() Limits {
	if l.MaxFiles <= 0 {
		l.MaxFiles = DefaultLimits.MaxFiles
	}
	if l.MaxFileBytes <= 0 {
		l.MaxFileBytes = DefaultLimits.MaxFileBytes
	}
	if l.MaxTotalBytes <= 0 {
		l.MaxTotalBytes = DefaultLimits.MaxTotalBytes
	}
	if l.MaxRatio <= 0 {
		l.MaxRatio = DefaultLimits.MaxRatio
	}
	if l.MaxFeatures <= 0 {
		l.MaxFeatures = DefaultLimits.MaxFeatures
	}
	return l
}

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
	names    map[string]string   // role → the file read for it
	read     map[string]bool     // the roles whose file was read without a problem
	kinds    map[string][]string // id → kinds it was read as (duplicates show as more than one)
	byID     map[string]any
}

// Open reads a package file, within DefaultLimits.
func Open(path string) (*Package, error) {
	return OpenWithLimits(path, DefaultLimits)
}

// OpenWithLimits reads a package file within limits.
func OpenWithLimits(path string, limits Limits) (*Package, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	return Read(f, st.Size(), limits)
}

// archive is a package's ZIP, its files read within limits.
type archive struct {
	entries map[string]*zip.File
	limits  Limits
	total   int64 // bytes read so far, uncompressed
}

func openArchive(r io.ReaderAt, size int64, limits Limits) (*archive, error) {
	limits = limits.orDefault()
	z, err := zip.NewReader(r, size)
	if err != nil {
		return nil, fmt.Errorf("not a readable package: %w", err)
	}
	if len(z.File) > limits.MaxFiles {
		return nil, fmt.Errorf("%w: the package has %d files, more than %d", ErrTooLarge, len(z.File), limits.MaxFiles)
	}
	a := &archive{entries: map[string]*zip.File{}, limits: limits}
	for _, f := range z.File {
		a.entries[f.Name] = f
	}
	return a, nil
}

// read is a file of the package (ok false when it has none), within the limits:
// its size, how many times its size in the ZIP it is, and the total read. They
// are checked against the size its ZIP header gives, before anything is
// uncompressed: archive/zip holds a file to that size.
func (a *archive) read(name string) (data []byte, ok bool, err error) {
	f, ok := a.entries[name]
	if !ok {
		return nil, false, nil
	}
	size, l := f.UncompressedSize64, a.limits
	if size > uint64(l.MaxFileBytes) {
		return nil, true, fmt.Errorf("%w: %s is larger than %d bytes", ErrTooLarge, name, l.MaxFileBytes)
	}
	if size > ratioFloor && (size-1)/uint64(l.MaxRatio) >= f.CompressedSize64 { // size > MaxRatio × compressed
		return nil, true, fmt.Errorf("%w: %s is more than %d times its size in the package", ErrTooLarge, name, l.MaxRatio)
	}
	if a.total+int64(size) > l.MaxTotalBytes {
		return nil, true, fmt.Errorf("%w: the package is larger than %d bytes", ErrTooLarge, l.MaxTotalBytes)
	}
	rc, err := f.Open()
	if err != nil {
		return nil, true, err
	}
	defer rc.Close()
	if data, err = readAll(rc, size); err != nil {
		return nil, true, fmt.Errorf("%s: %w", name, err)
	}
	a.total += int64(len(data))
	return data, true, nil
}

// readAll reads a file of the ZIP to its end, which must be at the size its
// header gives (archive/zip checks that, and the file's CRC, at its end).
func readAll(r io.Reader, size uint64) ([]byte, error) {
	data := make([]byte, size+1)
	n := 0
	for {
		m, err := r.Read(data[n:])
		if n += m; err == io.EOF {
			return data[:n], nil
		} else if err != nil {
			return nil, err
		}
		if n == len(data) { // longer than its header says
			return nil, zip.ErrFormat
		}
	}
}

// Read reads a package from a reader of size bytes, within limits. It fails only
// when the package cannot be read at all (not a ZIP, no readable manifest, over
// its limits: ErrTooLarge); anything else is left for Validate to report.
func Read(r io.ReaderAt, size int64, limits Limits) (*Package, error) {
	a, err := openArchive(r, size, limits)
	if err != nil {
		return nil, err
	}
	max := a.limits.MaxFeatures
	p := &Package{files: map[string]bool{}, read: map[string]bool{}, kinds: map[string][]string{}, byID: map[string]any{}}
	for name := range a.entries {
		p.files[name] = true
	}

	data, ok, err := a.read(FileManifest)
	if err != nil {
		return nil, err
	}
	if !ok {
		if a.entries["project.json"] != nil || a.entries["studio/project.spproj"] != nil {
			return nil, errors.New("not a package: a StoreyPath project file, for StoreyPath Studio to continue " +
				"the project; export a building's package (.storeypath) from Studio instead")
		}
		return nil, errors.New("not a package: no manifest.json")
	}
	if err := decodeJSON(data, &p.Manifest, max); err != nil {
		return nil, fmt.Errorf("manifest.json: %w", err)
	}

	// Each file is where the manifest's files say, or where it usually is when they
	// do not say; items and their catalogue (format 0.6) only when they list them.
	p.names = map[string]string{}
	for role, name := range usualNames {
		if listed := p.Manifest.Files[role]; listed != "" {
			name = listed
		}
		p.names[role] = name
	}
	// bad: a file the package has, unreadable as its kind; a limit passed fails Read
	bad := func(name string, err error) error {
		if errors.Is(err, ErrTooLarge) {
			return fmt.Errorf("%s: %w", clip(name), err)
		}
		p.problems = append(p.problems, Problem{Code: ProblemBadFile, File: clip(name), Message: clip(name) + ": " + err.Error()})
		return nil
	}
	files := []struct {
		role   string
		decode func([]byte) error
	}{
		{"location", func(b []byte) error {
			return decode(b, "location", max, &p.Locations, func(f *Location, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{"buildings", func(b []byte) error {
			return decode(b, "building", max, &p.Buildings, func(f *Building, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{"floors", func(b []byte) error {
			return decode(b, "floor", max, &p.Floors, func(f *Floor, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{"spaces", func(b []byte) error {
			return decode(b, "space", max, &p.Spaces, func(f *Space, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{"zones", func(b []byte) error {
			return decode(b, "zone", max, &p.Zones, func(f *Zone, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{"openings", func(b []byte) error {
			return decode(b, "opening", max, &p.Openings, func(f *Opening, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{"items", func(b []byte) error {
			return decode(b, "item", max, &p.Items, func(f *Item, id string, g *Geometry) { f.ID, f.Geometry = id, g })
		}},
		{"catalogue", func(b []byte) error {
			var c Catalogue
			if err := decodeJSON(b, &c, max); err != nil {
				return err
			}
			c.index()
			p.Catalogue = &c
			return nil
		}},
		{"objects", func(b []byte) (err error) {
			// every feature read, and maybe rows of kinds a later format adds
			features := len(p.Locations) + len(p.Buildings) + len(p.Floors) + len(p.Spaces) + len(p.Zones) + len(p.Openings) + len(p.Items)
			p.Objects, err = readObjects(b, features+1+max)
			return err
		}},
		{"changes", func(b []byte) error {
			var c Changes
			if err := decodeJSON(b, &c, max); err != nil {
				return err
			}
			p.Changes = &c
			return nil
		}},
	}
	for _, f := range files {
		if optionalRoles[f.role] && p.Manifest.Files[f.role] == "" {
			continue
		}
		name := p.names[f.role]
		data, ok, err := a.read(name)
		if err != nil {
			return nil, err
		}
		if !ok {
			p.problems = append(p.problems, Problem{Code: ProblemMissingFile, File: clip(name), Message: "missing file " + clip(name)})
		} else if err := f.decode(data); err != nil {
			if err := bad(name, err); err != nil {
				return nil, err
			}
		} else {
			p.read[f.role] = true
		}
	}
	p.index()
	return p, nil
}

// readObjects reads objects.csv, of at most max rows after its header.
func readObjects(data []byte, max int) ([]Object, error) {
	r := csv.NewReader(bytes.NewReader(data))
	r.ReuseRecord = true
	header, err := r.Read()
	if err == io.EOF {
		return nil, errors.New("no header row")
	} else if err != nil {
		return nil, err
	}
	col := map[string]int{}
	for i, name := range header {
		col[strings.TrimSpace(name)] = i
	}
	get := func(row []string, name string) string {
		if i, ok := col[name]; ok && i < len(row) {
			return row[i]
		}
		return ""
	}
	var out []Object
	for {
		row, err := r.Read()
		if err == io.EOF {
			break
		} else if err != nil {
			return nil, err
		}
		if len(out) == max {
			return nil, fmt.Errorf("%w: more than %d rows", ErrTooLarge, max)
		}
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

// file is the name of the package's file with a role, as Read found it.
func (p *Package) file(role string) string {
	if name, ok := p.names[role]; ok {
		return name
	}
	return usualNames[role]
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
				DrawingLabel: s.DrawingLabel, Seating: s.Seating})
			continue
		}
		for _, z := range zones {
			out = append(out, Unit{ID: z.ID, Kind: "zone", Space: s, Zone: z, Type: z.Type, Name: z.Name,
				Number: z.Number, Area: z.Area, Label: z.Label, Geometry: z.Geometry, Hidden: z.Hidden,
				Ignored: z.Ignored, DrawingLabel: z.DrawingLabel, Seating: z.Seating})
		}
	}
	return out
}

// ItemLocal is where an item stands in its building's own frame: its Local as the
// package has it (format 0.7), or for an older package worked out from its map
// position and heading through the building's placement (to about a centimetre).
func (p *Package) ItemLocal(it *Item) (ItemLocal, error) {
	if it.Local != nil {
		return *it.Local, nil
	}
	f, err := p.Frame(it.Building)
	if err != nil {
		return ItemLocal{}, err
	}
	x, y := f.ToLocal(it.Label)
	rotation := math.Mod(180-(it.Heading-f.Placement.Bearing)+720, 360)
	return ItemLocal{X: x, Y: y, Rotation: rotation}, nil
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
