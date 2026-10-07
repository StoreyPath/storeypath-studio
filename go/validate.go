package storeypath

import (
	"fmt"
	"sort"
	"strings"
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
	ProblemZone         = "ZONE"          // a zone and its space do not list each other
	ProblemOpening      = "OPENING"       // an opening joins a space that is not there, or on another floor
	ProblemObjects      = "OBJECTS"       // objects.csv does not list exactly the features
	ProblemChanges      = "CHANGES"       // changes.json does not agree with the package
	ProblemGeometry     = "GEOMETRY"      // a feature's geometry is not the kind its file holds
	ProblemScope        = "SCOPE"         // a package of part of a project holds a building its scope does not list, or lacks one it does
)

var kindLevel = map[string]string{
	"location": LevelLocation, "building": LevelBuilding, "floor": LevelFloor,
	"space": LevelObject, "zone": LevelObject, "opening": LevelObject,
}

var collectionFiles = map[string]string{
	"location": FileLocation, "building": FileBuildings, "floor": FileFloors,
	"space": FileSpaces, "zone": FileZones, "opening": FileOpenings,
}

// Validate checks the package as Studio's validator does: its files, the
// manifest's counts, every ID and every reference between features, objects.csv
// and changes.json. No problems means the package can be linked to as it is.
func (p *Package) Validate() []Problem {
	out := append([]Problem(nil), p.problems...)
	add := func(code, file, id, format string, args ...any) {
		out = append(out, Problem{Code: code, File: file, ID: id, Message: fmt.Sprintf(format, args...)})
	}
	m := p.Manifest
	if m.Format != FormatName {
		add(ProblemFormat, FileManifest, "", "unknown format %q", m.Format)
	}
	if major(m.FormatVersion) != major(FormatVersion) {
		add(ProblemVersion, FileManifest, "", "unsupported format version %s (this reader reads %s.x)",
			m.FormatVersion, major(FormatVersion))
	}
	project := m.Project.ID

	counts := map[string]int{"location": len(p.Locations), "buildings": len(p.Buildings), "floors": len(p.Floors),
		"spaces": len(p.Spaces), "zones": len(p.Zones), "openings": len(p.Openings)}
	roles := []string{"location", "buildings", "floors", "spaces", "zones", "openings"}
	for _, role := range roles {
		file := role + ".geojson"
		if !p.read[file] { // missing or unreadable: already a problem of its own
			continue
		}
		if got, ok := m.Counts[role]; !ok || got != counts[role] {
			add(ProblemCount, file, "", "%s: manifest counts %d, file has %d", file, m.Counts[role], counts[role])
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
			add(ProblemDuplicateID, collectionFiles[kinds[0]], id, "duplicate ID %s", id)
		}
		pid, err := ParseID(id)
		if err != nil {
			add(ProblemBadID, collectionFiles[kinds[0]], id, "%s: %v", collectionFiles[kinds[0]], err)
			continue
		}
		if pid.Project() != project {
			add(ProblemWrongProject, collectionFiles[kinds[0]], id, "%s: project segment is not the package's project %s", id, project)
		}
		if want := kindLevel[kinds[0]]; pid.Level() != want {
			add(ProblemIDLevel, collectionFiles[kinds[0]], id, "%s: a %s ID needs %d segments", id, kinds[0], levelDepth(want))
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
		parent(b.ID, b.Location, "location", FileBuildings)
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
				add(ProblemScope, FileBuildings, b.ID, "building %s is in the package but not in its scope", b.ID)
			}
		}
	}
	for _, f := range p.Floors {
		parent(f.ID, f.Building, "building", FileFloors)
	}
	zonesOf := map[string]map[string]bool{}
	for _, s := range p.Spaces {
		parent(s.ID, s.Floor, "floor", FileSpaces)
		zonesOf[s.ID] = map[string]bool{}
		for _, z := range s.Zones {
			zonesOf[s.ID][z] = true
			if kindOf(z) != "zone" {
				add(ProblemZone, FileSpaces, s.ID, "%s: lists unknown zone %s", s.ID, z)
			}
		}
		geometryOf(s.ID, s.Geometry, FileSpaces, false, add)
	}
	for _, z := range p.Zones {
		parent(z.ID, z.Floor, "floor", FileZones)
		if kindOf(z.Space) != "space" {
			add(ProblemZone, FileZones, z.ID, "%s: zone of unknown space %s", z.ID, z.Space)
		} else if !zonesOf[z.Space][z.ID] {
			add(ProblemZone, FileZones, z.ID, "%s: not listed in the zones of its space %s", z.ID, z.Space)
		}
		geometryOf(z.ID, z.Geometry, FileZones, false, add)
	}
	for _, o := range p.Openings {
		parent(o.ID, o.Floor, "floor", FileOpenings)
		for _, s := range o.Connects {
			if kindOf(s) != "space" {
				add(ProblemOpening, FileOpenings, o.ID, "%s: connects to unknown space %s", o.ID, s)
			} else if !strings.HasPrefix(s, o.Floor+"-") {
				add(ProblemOpening, FileOpenings, o.ID, "%s: connects to %s on another floor", o.ID, s)
			}
		}
		geometryOf(o.ID, o.Geometry, FileOpenings, true, add)
	}

	if p.files[FileObjects] {
		listed := map[string]bool{}
		for _, row := range p.Objects {
			if row.Kind != "project" {
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
			add(ProblemObjects, FileObjects, "", "%s: rows do not match the features (%d extra, %d missing)", FileObjects, extra, missing)
		}
	}

	if c := p.Changes; c != nil {
		for _, id := range append(append([]string(nil), c.Added...), c.Changed...) {
			if _, ok := p.kinds[id]; !ok {
				add(ProblemChanges, FileChanges, id, "%s: %s is listed as added/changed but not in the package", FileChanges, id)
			}
		}
		for _, id := range c.AllRetired {
			if _, ok := p.kinds[id]; ok {
				add(ProblemChanges, FileChanges, id, "%s: retired ID %s is still in the package", FileChanges, id)
			}
		}
		if c.Sequence != m.Export.Sequence {
			add(ProblemChanges, FileChanges, "", "%s: sequence does not match the manifest", FileChanges)
		}
	}
	return out
}

// geometryOf checks a feature's geometry is a polygon (or, for openings, a point).
func geometryOf(id string, g *Geometry, file string, point bool, add func(code, file, id, format string, args ...any)) {
	if point {
		if g != nil && g.Type != "Point" {
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

func major(version string) string { return strings.SplitN(version, ".", 2)[0] }

func levelDepth(level string) int {
	for i, l := range levels {
		if l == level {
			return i + 1
		}
	}
	return 0
}
