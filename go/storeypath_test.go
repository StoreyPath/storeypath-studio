package storeypath

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"io"
	"math"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

const corpus = "../spec/conformance"

func open(t *testing.T, name string) *Package {
	t.Helper()
	p, err := Open(filepath.Join(corpus, "packages", name))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// corpusPackages is every package in the corpus.
func corpusPackages(t *testing.T) []string {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join(corpus, "packages", "*.storeypath"))
	if err != nil || len(paths) < 3 {
		t.Fatalf("corpus packages: %v %v", paths, err)
	}
	names := make([]string, len(paths))
	for i, path := range paths {
		names[i] = filepath.Base(path)
	}
	return names
}

func TestSimpleOfficeIsWayfindersFloor(t *testing.T) {
	p := open(t, "simple-office.storeypath")
	if len(p.Floors) != 1 {
		t.Fatalf("%d floors", len(p.Floors))
	}
	numbers := map[string]Unit{}
	var hidden []string
	for _, u := range p.UnitsOn(p.Floors[0].ID) {
		if u.Hidden {
			hidden = append(hidden, u.Type)
			continue
		}
		if u.Number == nil {
			t.Errorf("%s (%s) has no number", u.ID, u.Type)
			continue
		}
		numbers[*u.Number] = u
	}
	for n, want := range map[string]string{"F0-301": "office", "F0-302": "office", "F0-303": "office", "F0-304": "meeting_room",
		"F0-305": "kitchen", "F0-C01": "corridor", "F0-315": "office", "F0-316": "restroom", "F0-317": "stairs",
		"F0-318": "elevator", "F0-319": "utility", "F0-320": "utility", "F0-321": "storage", "F0-322": "prayer_room"} {
		if u, ok := numbers[n]; !ok || u.Type != want || u.Kind != "space" {
			t.Errorf("%s: %+v, want a %s space", n, u, want)
		}
	}
	for _, n := range []string{"F0-330", "F0-331"} {
		if u := numbers[n]; u.Kind != "zone" || u.Type != "office" {
			t.Errorf("%s: %+v, want an office zone", n, u)
		}
	}
	if len(numbers) != 16 || len(hidden) != 1 || hidden[0] != "shaft" {
		t.Errorf("%d numbered units, hidden %v", len(numbers), hidden)
	}
}

func TestTheConformancePackagesAreValid(t *testing.T) {
	for _, name := range corpusPackages(t) {
		if problems := open(t, name).Validate(); len(problems) > 0 {
			t.Errorf("%s: %v", name, problems)
		}
	}
}

func TestFloorsSpacesZonesAndUnits(t *testing.T) {
	p := open(t, "campus.storeypath")
	if len(p.Buildings) != 2 || len(p.Floors) != 5 {
		t.Fatalf("%d buildings, %d floors", len(p.Buildings), len(p.Floors))
	}
	hq := p.Manifest.Project.ID + "-DEMO-HQ"
	floors := p.FloorsOf(hq)
	if len(floors) != 3 || floors[0].Ordinal != 0 || floors[2].Ordinal != 2 {
		t.Fatalf("floors of HQ: %v", floors)
	}
	// on the first floor a space is divided into two zones: used through them
	var divided *Space
	for _, s := range p.SpacesOn(floors[1].ID) {
		if len(s.Zones) > 0 {
			divided = s
		}
	}
	if divided == nil || len(p.ZonesOf(divided.ID)) != 2 {
		t.Fatal("no space divided into two zones")
	}
	units := p.UnitsOn(floors[1].ID)
	var zones int
	for _, u := range units {
		if u.ID == divided.ID {
			t.Error("a divided space is not a unit")
		}
		if u.Kind == "zone" {
			zones++
			if u.Space != divided || u.Zone == nil || u.Zone.Space != divided.ID {
				t.Error("a zone's unit does not point at its space")
			}
		}
	}
	if zones != 2 || len(units) != len(p.SpacesOn(floors[1].ID))-1+2 {
		t.Errorf("%d units, %d of them zones", len(units), zones)
	}
	if f, ok := p.Get(divided.ID); !ok || f.(*Space) != divided {
		t.Error("Get does not find the space")
	}
	for _, o := range p.OpeningsOn(floors[1].ID) {
		for _, id := range o.Connects {
			if p.Space(id) == nil {
				t.Errorf("%s connects to %s, not a space", o.ID, id)
			}
		}
	}
}

func TestItemsAndTheirCatalogue(t *testing.T) {
	// campus has furniture and equipment (format 0.6); simple-office, an older
	// package, has none.
	p := open(t, "campus.storeypath")
	if len(p.Items) != p.Manifest.Counts["items"] || len(p.Items) < 10 || p.Catalogue == nil {
		t.Fatalf("%d items (manifest %d), catalogue %v", len(p.Items), p.Manifest.Counts["items"], p.Catalogue)
	}
	hq := p.FloorsOf(p.Manifest.Project.ID + "-DEMO-HQ")
	var types []string
	var zoned *Item
	for _, it := range p.ItemsOn(hq[0].ID) {
		types = append(types, it.Type)
		if it.Floor != hq[0].ID || !IsItemID(it.ID) || p.Item(it.ID) != it {
			t.Errorf("%s: floor %s, an item ID %v, found by ID %v", it.ID, it.Floor, IsItemID(it.ID), p.Item(it.ID) == it)
		}
		if f, ok := p.Get(it.ID); !ok || f.(*Item) != it {
			t.Errorf("Get does not find item %s", it.ID)
		}
	}
	for _, want := range []string{"DESK-DIRECTOR", "COPIER", "ACCESS-POINT", "SOFA", "TV"} {
		if !slices.Contains(types, want) {
			t.Errorf("no %s on the ground floor: %v", want, types)
		}
	}
	for _, it := range p.Items {
		if it.Zone != nil {
			zoned = it
		}
		switch it.Type {
		case "COPIER":
			if it.Space == nil || p.Space(*it.Space).Type != "corridor" || it.Values["model"] != "MFP-C450" || it.Mount != "floor" {
				t.Errorf("the copier: %+v", it)
			}
		case "ACCESS-POINT":
			if it.Mount != "ceiling" || it.Elevation != nil {
				t.Errorf("an access point is under the ceiling: %+v", it)
			}
		case "TV":
			if it.Mount != "wall" || it.Elevation == nil || *it.Elevation != 1.2 || it.Values["size_in"] != 65.0 {
				t.Errorf("the TV: %+v", it)
			}
		}
		if poly, err := it.Geometry.Polygons(); err != nil || len(poly) != 1 || len(poly[0][0]) != 5 {
			t.Errorf("%s: its footprint is four corners: %v %v", it.ID, poly, err)
		}
	}
	if zoned == nil || p.Zone(*zoned.Zone).Space != *zoned.Space {
		t.Fatal("no item in a zone of its space")
	}
	ap := p.ItemType("ACCESS-POINT")
	owners := map[string]string{}
	for _, f := range ap.Fields {
		owners[f.Key] = f.Owner
	}
	if ap.Mount != "ceiling" || ap.Color != "#1f9d8b" || owners["ssid"] != OwnerSystem || owners["color"] != OwnerStoreyPath {
		t.Errorf("the access point's type: %+v", ap)
	}
	if p.ItemType("SPACESHIP") != nil {
		t.Error("a type that is not in the catalogue")
	}
	// an item's row in objects.csv says where it is
	for _, row := range p.Objects {
		if row.Kind == "item" && (row.Floor != p.Item(row.ID).Floor || row.Building != p.Item(row.ID).Building) {
			t.Errorf("%s: row %+v", row.ID, row)
		}
	}

	old := open(t, "simple-office.storeypath")
	if len(old.Items) != 0 || old.Catalogue != nil || old.ItemType("COPIER") != nil || old.ItemsOn(old.Floors[0].ID) != nil {
		t.Error("an older package has no items")
	}
}

func TestItemIDs(t *testing.T) {
	for id, want := range map[string]bool{"K7Q2XM-I000142": true, "K7Q2XM-I00014": false, "K7Q2XM-RUH": false,
		"K7Q2XM-I000142-X": false, "k7q2xm-I000142": false, "I000142": false} {
		if IsItemID(id) != want {
			t.Errorf("IsItemID(%q) = %v", id, !want)
		}
	}
}

func TestTheLocalFrameAgreesWithStudio(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(corpus, "localframe.json"))
	if err != nil {
		t.Fatal(err)
	}
	var vectors struct {
		Tolerance float64 `json:"tolerance_m"`
		Vectors   []struct {
			Placement Placement  `json:"placement"`
			Local     [2]float64 `json:"local"`
			LonLat    LonLat     `json:"lonlat"`
		} `json:"vectors"`
	}
	if err := json.Unmarshal(data, &vectors); err != nil {
		t.Fatal(err)
	}
	for _, v := range vectors.Vectors {
		f := NewLocalFrame(v.Placement)
		x, y := f.ToLocal(v.LonLat)
		if d := math.Hypot(x-v.Local[0], y-v.Local[1]); d > vectors.Tolerance {
			t.Errorf("ToLocal %v at %v: %.4f m off", v.LonLat, v.Placement, d)
		}
		ll := f.ToLonLat(v.Local[0], v.Local[1])
		bx, by := f.ToLocal(ll) // compared in metres, through the other way
		if d := math.Hypot(bx-v.Local[0], by-v.Local[1]); d > vectors.Tolerance/10 {
			t.Errorf("ToLonLat then ToLocal at %v: %.5f m off", v.Local, d)
		}
		if dlon, dlat := (ll[0]-v.LonLat[0])*111320*math.Cos(v.LonLat[1]*math.Pi/180), (ll[1]-v.LonLat[1])*110574; math.Hypot(dlon, dlat) > vectors.Tolerance {
			t.Errorf("ToLonLat %v: %v, Studio %v", v.Local, ll, v.LonLat)
		}
	}
}

func TestAreasInLocalMetresAreStudios(t *testing.T) {
	for _, name := range corpusPackages(t) {
		p := open(t, name)
		for _, s := range p.Spaces {
			b, _ := ParseID(s.ID)
			building, _ := b.Prefix(LevelBuilding)
			f, err := p.Frame(building)
			if err != nil {
				t.Fatal(err)
			}
			polys, err := f.PolygonsToLocal(s.Geometry)
			if err != nil {
				t.Fatal(err)
			}
			// a package keeps 7 decimals of a degree, about a centimetre: an area read
			// back is off by up to its perimeter times that
			a, perimeter := area(polys)
			if math.Abs(a-s.Area) > 0.012*perimeter+0.005 {
				t.Errorf("%s %s: %.3f m² in local metres, Studio says %.3f", name, s.ID, a, s.Area)
			}
		}
	}
}

func area(polys [][][][2]float64) (total, perimeter float64) {
	for _, poly := range polys {
		for i, ring := range poly {
			var a float64
			for k := 0; k+1 < len(ring); k++ {
				a += ring[k][0]*ring[k+1][1] - ring[k+1][0]*ring[k][1]
				perimeter += math.Hypot(ring[k+1][0]-ring[k][0], ring[k+1][1]-ring[k][1])
			}
			if i == 0 {
				total += math.Abs(a) / 2
			} else {
				total -= math.Abs(a) / 2
			}
		}
	}
	return total, perimeter
}

// rewrite is the campus package with files changed: edit gets each file's JSON
// (or raw bytes for others) and returns what to write, or nil to leave it out.
func rewrite(t *testing.T, edit func(name string, data []byte) []byte) *Package {
	t.Helper()
	src, err := zip.OpenReader(filepath.Join(corpus, "packages", "campus.storeypath"))
	if err != nil {
		t.Fatal(err)
	}
	defer src.Close()
	var buf bytes.Buffer
	w := zip.NewWriter(&buf)
	for _, f := range src.File {
		rc, _ := f.Open()
		data, _ := io.ReadAll(rc)
		rc.Close()
		if data = edit(f.Name, data); data == nil {
			continue
		}
		out, _ := w.Create(f.Name)
		out.Write(data)
	}
	w.Close()
	p, err := Read(bytes.NewReader(buf.Bytes()), int64(buf.Len()), DefaultLimits)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// editJSON changes one JSON file of the package.
func editJSON(file string, change func(doc map[string]any)) func(string, []byte) []byte {
	return func(name string, data []byte) []byte {
		if name != file {
			return data
		}
		var doc map[string]any
		json.Unmarshal(data, &doc)
		change(doc)
		out, _ := json.Marshal(doc)
		return out
	}
}

func features(doc map[string]any) []any { return doc["features"].([]any) }
func props(f any) map[string]any        { return f.(map[string]any)["properties"].(map[string]any) }

func codes(problems []Problem) []string {
	var out []string
	for _, p := range problems {
		if !slices.Contains(out, p.Code) {
			out = append(out, p.Code)
		}
	}
	return out
}

func TestBrokenPackagesAreFound(t *testing.T) {
	cases := []struct {
		name string
		edit func(string, []byte) []byte
		want []string
	}{
		{"no changes.json", func(n string, d []byte) []byte {
			if n == FileChanges {
				return nil
			}
			return d
		}, []string{ProblemMissingFile}},
		{"spaces.geojson unreadable", func(n string, d []byte) []byte {
			if n == FileSpaces {
				return []byte("{not json")
			}
			return d
		}, []string{ProblemBadFile}},
		{"a space twice", editJSON(FileSpaces, func(doc map[string]any) {
			doc["features"] = append(features(doc), features(doc)[0])
		}), []string{ProblemCount, ProblemDuplicateID}},
		{"a space on a floor that is not there", editJSON(FileSpaces, func(doc map[string]any) {
			props(features(doc)[0])["floor_id"] = "NOPE"
		}), []string{ProblemParent}},
		{"a zone its space does not list", editJSON(FileSpaces, func(doc map[string]any) {
			for _, f := range features(doc) {
				if zs, ok := props(f)["zones"].([]any); ok && len(zs) > 0 {
					props(f)["zones"] = zs[:1]
				}
			}
		}), []string{ProblemZone}},
		{"an opening to another floor", func() func(string, []byte) []byte {
			return editJSON(FileOpenings, func(doc map[string]any) {
				o := props(features(doc)[0])
				floor := o["floor_id"].(string)
				// a space ID on another floor of the same project
				other := strings.Replace(floor, "-F00", "-F01", 1)
				if other == floor {
					other = strings.Replace(floor, "-F01", "-F00", 1)
				}
				o["connects"] = []any{other + "-0001"}
			})
		}(), []string{ProblemOpening}},
		{"a retired ID still there", func() func(string, []byte) []byte {
			var some string
			return func(n string, d []byte) []byte {
				if n == FileSpaces {
					var doc map[string]any
					json.Unmarshal(d, &doc)
					some = features(doc)[0].(map[string]any)["id"].(string)
					return d
				}
				if n == FileChanges && some != "" {
					var doc map[string]any
					json.Unmarshal(d, &doc)
					doc["all_retired"] = append(doc["all_retired"].([]any), some)
					d, _ = json.Marshal(doc)
				}
				return d
			}
		}(), []string{ProblemChanges}},
		{"another project's ID", editJSON(FileManifest, func(doc map[string]any) {
			doc["project"].(map[string]any)["id"] = "ZZZZZZ"
		}), []string{ProblemWrongProject}},
		{"a format this reader does not read", editJSON(FileManifest, func(doc map[string]any) {
			doc["format_version"] = "1.0.0"
		}), []string{ProblemVersion}},
		{"a count wrong", editJSON(FileManifest, func(doc map[string]any) {
			doc["counts"].(map[string]any)["spaces"] = 1
		}), []string{ProblemCount}},
		{"items counted wrong", editJSON(FileManifest, func(doc map[string]any) {
			doc["counts"].(map[string]any)["items"] = 1
		}), []string{ProblemCount}},
		{"an item on a floor that is not there", editJSON(FileItems, func(doc map[string]any) {
			props(features(doc)[0])["floor_id"] = "NOPE"
		}), []string{ProblemItem}},
		{"an item of another building's floor", editJSON(FileItems, func(doc map[string]any) {
			b := props(features(doc)[0])["building_id"].(string)
			props(features(doc)[0])["building_id"] = strings.Replace(b, "-HQ", "-ANNEX", 1)
		}), []string{ProblemItem}},
		{"an item in a space on another floor", editJSON(FileItems, func(doc map[string]any) {
			p := props(features(doc)[0])
			p["space_id"] = strings.Replace(p["space_id"].(string), "-F00-", "-F01-", 1)
		}), []string{ProblemItem}},
		{"an item in a zone that is not there", editJSON(FileItems, func(doc map[string]any) {
			props(features(doc)[0])["zone_id"] = props(features(doc)[0])["floor_id"].(string) + "-9999"
		}), []string{ProblemItem}},
		{"an item of a type the catalogue does not have", editJSON(FileItems, func(doc map[string]any) {
			props(features(doc)[0])["type"] = "SPACESHIP"
		}), []string{ProblemItemType}},
		{"an item whose ID says where it is", editJSON(FileItems, func(doc map[string]any) {
			f := features(doc)[0].(map[string]any)
			f["id"] = props(f)["floor_id"].(string) + "-0999"
		}), []string{ProblemBadID}},
		{"an item of another project", editJSON(FileItems, func(doc map[string]any) {
			features(doc)[0].(map[string]any)["id"] = "ZZZZZZ-I000001"
		}), []string{ProblemWrongProject, ProblemObjects}},
		{"items.geojson unreadable", func(n string, d []byte) []byte {
			if n == FileItems {
				return []byte("[]")
			}
			return d
		}, []string{ProblemBadFile}},
		{"no catalogue.json", func(n string, d []byte) []byte {
			if n == FileCatalogue {
				return nil
			}
			return d
		}, []string{ProblemMissingFile}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := codes(rewrite(t, c.edit).Validate())
			for _, want := range c.want {
				if !slices.Contains(got, want) {
					t.Errorf("problems %v, want %s among them", got, want)
				}
			}
		})
	}
}

func TestLimitsKeepOutWhatIsTooBig(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(corpus, "packages", "campus.storeypath"))
	if err != nil {
		t.Fatal(err)
	}
	r := bytes.NewReader(data)
	if _, err := Read(r, int64(len(data)), Limits{MaxFiles: 3, MaxFileBytes: 1 << 30, MaxTotalBytes: 1 << 30}); err == nil {
		t.Error("too many files read")
	}
	if _, err := Read(r, int64(len(data)), Limits{MaxFiles: 1000, MaxFileBytes: 200, MaxTotalBytes: 1 << 30}); err == nil {
		t.Error("too large a file read")
	}
	if _, err := Read(bytes.NewReader([]byte("not a zip")), 9, DefaultLimits); err == nil {
		t.Error("not a ZIP read")
	}
}

func TestIDs(t *testing.T) {
	id, err := ParseID("K7Q2XM-RUH-HQ-F02-0142")
	if err != nil {
		t.Fatal(err)
	}
	floor, _ := id.Prefix(LevelFloor)
	if id.Level() != LevelObject || id.Project() != "K7Q2XM" || id.Code() != "0142" ||
		id.Parent() != "K7Q2XM-RUH-HQ-F02" || floor != "K7Q2XM-RUH-HQ-F02" {
		t.Errorf("%v: level %s, project %s, code %s, parent %s", id, id.Level(), id.Project(), id.Code(), id.Parent())
	}
	for _, bad := range []string{"", "k7q2xm", "A-B-C-D-E-F", "A--B", "A-B C"} {
		if _, err := ParseID(bad); err == nil {
			t.Errorf("%q read as an ID", bad)
		}
	}
}

func TestAPackageOfOneBuildingOfAProject(t *testing.T) {
	// campus-part holds the Headquarters of a two-building project: the Annex is
	// not in it, and that says nothing about the Annex.
	p := open(t, "campus-part.storeypath")
	if problems := p.Validate(); len(problems) != 0 {
		t.Fatalf("problems: %v", problems)
	}
	hq := p.Manifest.Project.ID + "-DEMO-HQ"
	annex := p.Manifest.Project.ID + "-DEMO-ANNEX"
	if p.Manifest.Scope == nil || !p.Holds(hq) || p.Holds(annex) {
		t.Fatalf("scope %v: holds HQ %v, Annex %v", p.Manifest.Scope, p.Holds(hq), p.Holds(annex))
	}
	if len(p.Buildings) != 1 || p.Building(annex) != nil || len(p.FloorsOf(hq)) != 3 {
		t.Fatalf("%d buildings, %d floors of HQ", len(p.Buildings), len(p.FloorsOf(hq)))
	}
	c := p.Changes
	if c == nil || len(c.Changed) != 1 || !strings.HasPrefix(c.Changed[0], hq+"-") || len(c.Retired) != 0 || len(c.Added) != 0 {
		t.Fatalf("changes: %+v", c)
	}
	whole := open(t, "campus.storeypath")
	if whole.Manifest.Scope != nil || !whole.Holds(whole.Manifest.Project.ID+"-DEMO-ANNEX") {
		t.Fatal("a package of the whole project holds every building")
	}
}

func TestAScopeMustListTheBuildingsHeld(t *testing.T) {
	p := open(t, "campus-part.storeypath")
	p.Manifest.Scope.Buildings = []string{p.Manifest.Project.ID + "-DEMO-ANNEX"}
	codes := map[string]bool{}
	for _, pr := range p.Validate() {
		codes[pr.Code] = true
	}
	if !codes[ProblemScope] {
		t.Fatal("a scope that lists another building is a problem")
	}
}

func TestAPackageWithItsFloorsPreBuiltReadsAsWithout(t *testing.T) {
	// campus-world is campus with world/<floor-id>.glb (format 0.5): read the same,
	// the folder left to those that draw in 3D.
	p, plain := open(t, "campus-world.storeypath"), open(t, "campus.storeypath")
	if problems := p.Validate(); len(problems) != 0 {
		t.Fatalf("problems: %v", problems)
	}
	if p.Manifest.FormatVersion != FormatVersion || p.Manifest.Files["world"] != "world/" {
		t.Fatalf("format %s, files %v", p.Manifest.FormatVersion, p.Manifest.Files)
	}
	if len(p.Floors) != len(plain.Floors) || len(p.Spaces) != len(plain.Spaces) || len(p.Openings) != len(plain.Openings) ||
		len(p.Items) != len(plain.Items) || len(p.Items) == 0 {
		t.Fatalf("%d floors, %d spaces, %d openings, %d items; without world/: %d, %d, %d, %d", len(p.Floors), len(p.Spaces),
			len(p.Openings), len(p.Items), len(plain.Floors), len(plain.Spaces), len(plain.Openings), len(plain.Items))
	}
	for _, f := range p.Floors {
		if !p.files["world/"+f.ID+".glb"] {
			t.Errorf("%s: not pre-built", f.ID)
		}
	}
}
