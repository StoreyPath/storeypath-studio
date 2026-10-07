package storeypath

import (
	"archive/zip"
	"bytes"
	"compress/flate"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
	"time"
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

func TestTheCatalogueFindsATypeByItsCode(t *testing.T) {
	// Validate looks up every item's type: by going through the types, a catalogue
	// of n types and n items took n² steps.
	p := open(t, "campus-hq.storeypath")
	if p.Catalogue.byCode == nil {
		t.Fatal("not indexed when read")
	}
	for i := range p.Catalogue.Types {
		if ty := &p.Catalogue.Types[i]; p.ItemType(ty.Code) != ty {
			t.Errorf("%s: not found", ty.Code)
		}
	}
	// the first of a code, as before; a type added since, or a catalogue made by hand
	c := &Catalogue{Types: []ItemType{{Code: "A", NameEN: "first"}, {Code: "B"}, {Code: "A", NameEN: "second"}}}
	if c.Type("A").NameEN != "first" || c.Type("C") != nil {
		t.Error("made by hand")
	}
	c.index()
	if c.Type("A").NameEN != "first" || c.Type("B") != &c.Types[1] || c.Type("C") != nil {
		t.Error("indexed")
	}
	if c.Types = append(c.Types, ItemType{Code: "C"}); c.Type("C") == nil {
		t.Error("a type added after it was indexed")
	}
	var none *Catalogue
	if none.Type("A") != nil {
		t.Error("no catalogue")
	}

	n := 100_000
	big := &Catalogue{Types: make([]ItemType, n)}
	for i := range big.Types {
		big.Types[i].Code = fmt.Sprintf("T%07d", i)
	}
	big.index()
	last := big.Types[n-1].Code
	start := time.Now()
	for range n {
		if big.Type(last) == nil {
			t.Fatal("the last type not found")
		}
	}
	if took := time.Since(start); took > time.Second {
		t.Errorf("%d lookups in a catalogue of %d types took %v", n, n, took)
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

func TestABuildingAcrossTheAntimeridian(t *testing.T) {
	// The Headquarters anchored at 179.9998°E: its items stand both sides of the
	// antimeridian, and a package has their longitudes in [-180, 180]. ToLonLat
	// went past 180, and Validate put the items 38,000 km from where they stand.
	plain := open(t, "campus-hq.storeypath")
	building := plain.Buildings[0].ID
	pl := plain.Manifest.Placements[building]
	pl.Lon, pl.Lat = 179.9998, -16.5
	f := NewLocalFrame(pl)
	east, west := 0, 0
	p := rewriteFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		switch n {
		case FileManifest:
			return editJSON(n, func(doc map[string]any) {
				at := doc["placements"].(map[string]any)[building].(map[string]any)
				at["lon"], at["lat"] = pl.Lon, pl.Lat
			})(n, d)
		case FileItems:
			return editJSON(n, func(doc map[string]any) {
				for _, it := range features(doc) {
					local := props(it)["local"].(map[string]any)
					ll := f.ToLonLat(local["x_m"].(float64), local["y_m"].(float64))
					lon := math.Round(math.Remainder(ll[0], 360)*1e7) / 1e7 // as a package has it
					if lon < 0 {
						west++
					} else {
						east++
					}
					props(it)["display_point"] = []any{lon, math.Round(ll[1]*1e7) / 1e7}
				}
			})(n, d)
		}
		return d
	})
	if east == 0 || west == 0 {
		t.Fatalf("%d items east of the antimeridian, %d west: not across it", east, west)
	}
	if problems := p.Validate(); len(problems) != 0 {
		t.Errorf("problems: %v", problems)
	}
	for _, it := range p.Items {
		ll := f.ToLonLat(it.Local.X, it.Local.Y)
		if ll[0] <= -180 || ll[0] > 180 {
			t.Errorf("%s: ToLonLat %v", it.ID, ll)
		}
		own := *it.Local
		it.Local = nil
		local, err := p.ItemLocal(it)
		if it.Local = &own; err != nil || math.Hypot(local.X-own.X, local.Y-own.Y) > 0.02 {
			t.Errorf("%s: worked out from the map %v, in its building %v (%v)", it.ID, local, own, err)
		}
	}
	if ll := NewLocalFrame(Placement{Lon: 179.9999, Lat: -16.5}).ToLonLat(50, 0); math.Abs(ll[0]-(-179.9996316)) > 1e-6 {
		t.Errorf("50 m east of 179.9999°E: %v", ll)
	}
	for lon, want := range map[float64]float64{180: 180, -180: 180, 180.5: -179.5, -180.5: 179.5, 540: 180, 0: 0, -179.5: -179.5} {
		if got := wrapLon(lon); math.Abs(got-want) > 1e-9 {
			t.Errorf("wrapLon(%v) = %v, want %v", lon, got, want)
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
	return rewriteFrom(t, "campus.storeypath", edit)
}

// rewriteFrom is a corpus package with its files edited (nil: left out), read.
func rewriteFrom(t *testing.T, name string, edit func(name string, data []byte) []byte) *Package {
	t.Helper()
	data := zipFrom(t, name, edit, zip.Deflate)
	p, err := Read(bytes.NewReader(data), int64(len(data)), DefaultLimits)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// zipFrom is a corpus package with its files edited (nil: left out), as a ZIP
// whose files are compressed by method (zip.Store: not at all; zip.Deflate: at
// best compression).
func zipFrom(t *testing.T, name string, edit func(name string, data []byte) []byte, method uint16) []byte {
	t.Helper()
	src, err := zip.OpenReader(filepath.Join(corpus, "packages", name))
	if err != nil {
		t.Fatal(err)
	}
	defer src.Close()
	var buf bytes.Buffer
	w := zip.NewWriter(&buf)
	w.RegisterCompressor(zip.Deflate, func(out io.Writer) (io.WriteCloser, error) {
		return flate.NewWriter(out, flate.BestCompression)
	})
	for _, f := range src.File {
		rc, _ := f.Open()
		data, _ := io.ReadAll(rc)
		rc.Close()
		if data = edit(f.Name, data); data == nil {
			continue
		}
		out, _ := w.CreateHeader(&zip.FileHeader{Name: f.Name, Method: method})
		out.Write(data)
	}
	w.Close()
	return buf.Bytes()
}

// readZip reads a package from its bytes, within limits.
func readZip(data []byte, limits Limits) (*Package, error) {
	return Read(bytes.NewReader(data), int64(len(data)), limits)
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
	if _, err := OpenWithLimits(filepath.Join(corpus, "packages", "campus.storeypath"), Limits{MaxFileBytes: 200}); !errors.Is(err, ErrTooLarge) {
		t.Errorf("OpenWithLimits: %v", err)
	}
}

func TestAPackageThatExpandsIsRefused(t *testing.T) {
	// A few hundred KB uploaded, a spaces.geojson of tiny features repeated: under
	// MaxFileBytes, but hundreds of times its size in the ZIP. Read refuses it
	// before decoding it.
	feature := `{"type":"Feature","id":"SD8YHE-DEMO-HQ-F00-0001","geometry":null,"properties":{"kind":"space"}},`
	bomb := zipFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		if n != FileSpaces {
			return d
		}
		return []byte(`{"type":"FeatureCollection","features":[` + strings.Repeat(feature, (32<<20)/len(feature)) + `{}]}`)
	}, zip.Deflate)
	if _, err := readZip(bomb, DefaultLimits); !errors.Is(err, ErrTooLarge) || !strings.Contains(err.Error(), "times its size") {
		t.Errorf("%d KB expanding to 32 MB: %v", len(bomb)>>10, err)
	}
	// stored as it is, the same file is within the ratio: then it has too many features
	stored := zipFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		if n != FileSpaces {
			return d
		}
		return []byte(`{"type":"FeatureCollection","features":[` + strings.Repeat("{},", DefaultLimits.MaxFeatures) + `{}]}`)
	}, zip.Store)
	if _, err := readZip(stored, DefaultLimits); !errors.Is(err, ErrTooLarge) || !strings.Contains(err.Error(), "entries") {
		t.Errorf("%d features: %v", DefaultLimits.MaxFeatures+1, err)
	}
	if _, err := readZip(stored, Limits{MaxFeatures: DefaultLimits.MaxFeatures + 1}); err != nil {
		t.Errorf("%d features, within limits: %v", DefaultLimits.MaxFeatures+1, err)
	}
	// objects.csv lists the features, and may have MaxFeatures rows more
	rows := zipFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		if n != FileObjects {
			return d
		}
		return append(d, strings.Repeat("X-P000001,plant,PALM,,,,,,,,,,,,,,\n", 1000)...)
	}, zip.Store)
	if _, err := readZip(rows, Limits{MaxFeatures: 999}); !errors.Is(err, ErrTooLarge) {
		t.Errorf("1000 rows more than the features: %v", err)
	}
	if _, err := readZip(rows, Limits{MaxFeatures: 1000}); err != nil {
		t.Errorf("1000 rows more than the features, within limits: %v", err)
	}
	// a small file may compress far better than a package's files: not refused
	padded := zipFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		if n != FileChanges {
			return d
		}
		return append(d, bytes.Repeat([]byte(" "), 900<<10)...)
	}, zip.Deflate)
	if p, err := readZip(padded, DefaultLimits); err != nil || len(p.Validate()) != 0 {
		t.Errorf("changes.json padded to 900 KB: %v", err)
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
	for _, bad := range []string{"", "k7q2xm", "A-B-C-D-E-F", "A--B", "A-B C", strings.Repeat("A", 17)} {
		if _, err := ParseID(bad); err == nil {
			t.Errorf("%q read as an ID", bad)
		}
	}
	longest := strings.TrimSuffix(strings.Repeat(strings.Repeat("A", 16)+"-", 5), "-")
	if _, err := ParseID(longest); err != nil || len(longest) != maxIDLength {
		t.Errorf("the longest ID (%d characters): %v", len(longest), err)
	}
	if id := strings.Repeat("A", 16) + "-I000001"; !IsItemID(id) || len(id) != maxItemIDLength {
		t.Errorf("the longest item ID %s", id)
	}
}

func TestAnIDTooLongIsRefusedBeforeItIsTakenApart(t *testing.T) {
	// 16 MiB of hyphens: refused by its length, without splitting it (which took
	// gigabytes), and shown cut short in the problem.
	hyphens := strings.Repeat("-", 16<<20)
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)
	_, err := ParseID(hyphens)
	isItem := IsItemID(hyphens)
	runtime.ReadMemStats(&after)
	if err == nil || isItem || len(err.Error()) > 200 || after.TotalAlloc-before.TotalAlloc > 1<<20 {
		t.Errorf("ParseID: %d bytes of error, %d bytes allocated", len(err.Error()), after.TotalAlloc-before.TotalAlloc)
	}
	// stored, not compressed: compressed, it is more than MaxRatio times its size
	p, err := readZip(zipFrom(t, "campus-hq.storeypath", editJSON(FileSpaces, func(doc map[string]any) {
		features(doc)[0].(map[string]any)["id"] = hyphens
	}), zip.Store), DefaultLimits)
	if err != nil {
		t.Fatal(err)
	}
	for _, pr := range p.Validate() {
		if len(pr.Message) > 300 || len(pr.ID) > 300 {
			t.Errorf("a problem of %d bytes (ID %d bytes)", len(pr.Message), len(pr.ID))
		}
		if pr.Code == ProblemBadID && !strings.Contains(pr.Message, "at most 84 characters") {
			t.Errorf("%s", pr.Message)
		}
	}
	if !slices.Contains(codes(p.Validate()), ProblemBadID) {
		t.Error("an ID of 16 MiB: not a problem")
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
	if minor(p.Manifest.FormatVersion) < 5 || p.Manifest.Files["world"] != "world/" {
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

func TestAPackageHoldsOneBuilding(t *testing.T) {
	// From 0.7 a package holds exactly one building, and names it (scope); a
	// package of 0.6 held the whole campus.
	for _, name := range []string{"campus-hq.storeypath", "campus-annex.storeypath", "campus-hq-2.storeypath", "campus-annex-2.storeypath"} {
		p := open(t, name)
		if p.Manifest.FormatVersion != FormatVersion || p.Manifest.Scope == nil || len(p.Manifest.Scope.Buildings) != 1 ||
			len(p.Buildings) != 1 || p.Buildings[0].ID != p.Manifest.Scope.Buildings[0] {
			t.Errorf("%s: format %s, scope %v, %d buildings", name, p.Manifest.FormatVersion, p.Manifest.Scope, len(p.Buildings))
		}
	}
	two := rewriteFrom(t, "campus-hq.storeypath", editJSON(FileManifest, func(doc map[string]any) {
		scope := doc["scope"].(map[string]any)
		scope["buildings"] = append(scope["buildings"].([]any), "X")
	}))
	none := rewriteFrom(t, "campus-hq.storeypath", editJSON(FileManifest, func(doc map[string]any) { delete(doc, "scope") }))
	for _, p := range []*Package{two, none} {
		if !slices.Contains(codes(p.Validate()), ProblemScope) {
			t.Errorf("scope %v: not a problem", p.Manifest.Scope)
		}
	}
	if problems := open(t, "campus.storeypath").Validate(); len(problems) != 0 {
		t.Errorf("0.6, the whole campus: %v", problems)
	}
}

func TestItemsArePlacedInTheirBuilding(t *testing.T) {
	// campus-hq-2 is the Headquarters after it was moved on the map: every item is
	// where it was in the building (Local), wherever it is on the map now.
	before, after := open(t, "campus-hq.storeypath"), open(t, "campus-hq-2.storeypath")
	moved := 0
	for _, it := range after.Items {
		was := before.Item(it.ID)
		if was == nil || it.Local == nil || was.Local == nil {
			t.Fatalf("%s: in both, with Local", it.ID)
		}
		if *it.Local != *was.Local {
			t.Errorf("%s: Local %v, was %v", it.ID, *it.Local, *was.Local)
		}
		if it.Label != was.Label {
			moved++
		}
		local, err := after.ItemLocal(it)
		if err != nil || local != *it.Local {
			t.Errorf("%s: ItemLocal %v %v", it.ID, local, err)
		}
		// without Local (an older package), worked out from the map to a centimetre
		it2 := *it
		it2.Local = nil
		if w, err := after.ItemLocal(&it2); err != nil || math.Hypot(w.X-it.Local.X, w.Y-it.Local.Y) > 0.02 ||
			math.Abs(math.Mod(w.Rotation-it.Local.Rotation+540, 360)-180) > 0.05 {
			t.Errorf("%s: worked out %v, Local %v (%v)", it.ID, w, *it.Local, err)
		}
	}
	if moved != len(after.Items) || moved == 0 {
		t.Errorf("%d of %d items elsewhere on the map", moved, len(after.Items))
	}
	off := rewriteFrom(t, "campus-hq.storeypath", editJSON(FileItems, func(doc map[string]any) {
		props(features(doc)[0])["local"].(map[string]any)["x_m"] = props(features(doc)[0])["local"].(map[string]any)["x_m"].(float64) + 1
	}))
	without := rewriteFrom(t, "campus-hq.storeypath", editJSON(FileItems, func(doc map[string]any) {
		delete(props(features(doc)[0]), "local")
	}))
	for _, p := range []*Package{off, without} {
		if got := codes(p.Validate()); !slices.Equal(got, []string{ProblemItem}) {
			t.Errorf("problems %v, want ITEM", got)
		}
	}
}

func TestAnItemsHeadingIsTheWayItFacesInItsBuilding(t *testing.T) {
	// heading = bearing + 180 - rotation_deg: an item turned round on the map, but
	// not in its building, passed. In campus-hq-2 the building is turned on the map.
	for _, name := range []string{"campus-hq.storeypath", "campus-hq-2.storeypath"} {
		turned := func(by float64) *Package {
			return rewriteFrom(t, name, editJSON(FileItems, func(doc map[string]any) {
				props(features(doc)[0])["heading"] = props(features(doc)[0])["heading"].(float64) + by
			}))
		}
		for by, ok := range map[float64]bool{180: false, 1: false, -0.6: false, 0.3: true, -0.3: true, 360: true, -720.2: true} {
			problems := turned(by).Validate()
			if ok != (len(problems) == 0) || (!ok && (problems[0].Code != ProblemItem || !strings.Contains(problems[0].Message, "heading"))) {
				t.Errorf("%s: heading turned by %v: %v", name, by, problems)
			}
		}
	}
}

func TestEveryBuildingHasAPlacement(t *testing.T) {
	// Without its building's placement an item's map position was not checked
	// against its position in the building, and nothing said so.
	none := rewriteFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		switch n {
		case FileManifest:
			return editJSON(n, func(doc map[string]any) { doc["placements"] = map[string]any{} })(n, d)
		case FileItems:
			return editJSON(n, func(doc map[string]any) { props(features(doc)[0])["display_point"] = []any{47.0, 25.0} })(n, d)
		}
		return d
	})
	problems := none.Validate()
	if !slices.Equal(codes(problems), []string{ProblemPlacement}) || len(problems) != 1 || problems[0].ID != none.Buildings[0].ID {
		t.Errorf("no placement: %v", problems)
	}
	// campus (0.6) holds two buildings: the Annex unplaced, and an item said to be
	// in a building the package does not hold
	annex := rewriteFrom(t, "campus.storeypath", func(n string, d []byte) []byte {
		switch n {
		case FileManifest:
			return editJSON(n, func(doc map[string]any) {
				for id := range doc["placements"].(map[string]any) {
					if strings.HasSuffix(id, "-ANNEX") {
						delete(doc["placements"].(map[string]any), id)
					}
				}
			})(n, d)
		case FileItems:
			return editJSON(n, func(doc map[string]any) { props(features(doc)[0])["building_id"] = "EWBSSN-DEMO-GONE" })(n, d)
		}
		return d
	})
	var unplaced []string
	for _, pr := range annex.Validate() {
		if pr.Code == ProblemPlacement {
			unplaced = append(unplaced, pr.ID)
		}
	}
	if !slices.Equal(unplaced, []string{"EWBSSN-DEMO-ANNEX", "EWBSSN-DEMO-GONE"}) {
		t.Errorf("unplaced: %v", unplaced)
	}
}

func TestAZoneIsPartOfOneSpace(t *testing.T) {
	// A zone also listed by another space of its floor passed: UnitsOn listed it
	// twice, and the other space was used through it, not as a whole.
	plain := open(t, "campus-hq.storeypath")
	zone := plain.Zones[0]
	var other *Space
	for _, s := range plain.SpacesOn(zone.Floor) {
		if s.ID != zone.Space && len(s.Zones) == 0 {
			other = s
			break
		}
	}
	listing := func(space string, zones ...any) func(string, []byte) []byte {
		return editJSON(FileSpaces, func(doc map[string]any) {
			for _, f := range features(doc) {
				if f.(map[string]any)["id"] == space {
					props(f)["zones"] = zones
				}
			}
		})
	}
	problems := rewriteFrom(t, "campus-hq.storeypath", listing(other.ID, zone.ID)).Validate()
	if len(problems) != 1 || problems[0].Code != ProblemZone || problems[0].ID != other.ID || !strings.Contains(problems[0].Message, "part of space "+zone.Space) {
		t.Errorf("%s listed by %s too: %v", zone.ID, other.ID, problems)
	}
	own := plain.Space(zone.Space)
	twice := append(append([]any{}, toAny(own.Zones)...), zone.ID)
	problems = rewriteFrom(t, "campus-hq.storeypath", listing(own.ID, twice...)).Validate()
	if len(problems) != 1 || problems[0].Code != ProblemZone || !strings.Contains(problems[0].Message, "twice") {
		t.Errorf("%s listed twice by its space: %v", zone.ID, problems)
	}
}

func toAny(s []string) []any {
	out := make([]any, len(s))
	for i, v := range s {
		out[i] = v
	}
	return out
}

func TestAnItemCarriedAwayIsNotRetired(t *testing.T) {
	// campus-hq-2: a desk carried to the Annex (moved away), the TV taken away
	// (retired); campus-annex-2 holds the desk, changed.
	hq, annex := open(t, "campus-hq-2.storeypath"), open(t, "campus-annex-2.storeypath")
	c := hq.Changes
	if len(c.MovedAway) != 1 || c.MovedAway[0].Building != annex.Buildings[0].ID || hq.Item(c.MovedAway[0].ID) != nil {
		t.Fatalf("moved away %v", c.MovedAway)
	}
	if len(c.Retired) != 1 || !IsItemID(c.Retired[0]) || slices.Contains(c.Retired, c.MovedAway[0].ID) {
		t.Errorf("retired %v", c.Retired)
	}
	// the building moved, and the office the desk left seats one fewer
	before := open(t, "campus-hq.storeypath")
	left := *before.Item(c.MovedAway[0].ID).Space
	if !slices.Equal(c.Changed, []string{hq.Buildings[0].ID, left}) ||
		*before.Space(left).Capacity != *hq.Space(left).Capacity+1 {
		t.Errorf("changed %v: the building, and %s (it seats one fewer)", c.Changed, left)
	}
	desk := c.MovedAway[0].ID
	if annex.Item(desk) == nil || !slices.Contains(annex.Changes.Changed, desk) || slices.Contains(annex.Changes.Added, desk) {
		t.Errorf("the Annex: %v changed, %v added", annex.Changes.Changed, annex.Changes.Added)
	}
	gone := rewriteFrom(t, "campus-hq-2.storeypath", editJSON(FileChanges, func(doc map[string]any) {
		doc["moved_away"].([]any)[0].(map[string]any)["building_id"] = hq.Buildings[0].ID
	}))
	if !slices.Contains(codes(gone.Validate()), ProblemChanges) {
		t.Error("moved away to its own building: not a problem")
	}
}

func TestRowsOfKindsThisReaderDoesNotKnowAreLeftAlone(t *testing.T) {
	// A later format may add kinds, as 0.6 added items: their rows in objects.csv,
	// and their IDs in changes.json, are no problem.
	p := rewriteFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		switch n {
		case FileObjects:
			return append(d, []byte("X-P000001,plant,PALM,,,,,,,,,,,,,,\n")...)
		case FileChanges:
			var doc map[string]any
			json.Unmarshal(d, &doc)
			doc["added"] = append(doc["added"].([]any), "X-P000001")
			d, _ = json.Marshal(doc)
		}
		return d
	})
	if problems := p.Validate(); len(problems) != 0 {
		t.Errorf("problems: %v", problems)
	}
}

func TestFilesAreFoundThroughTheManifest(t *testing.T) {
	// Every file is where manifest.files says, whatever its name; a file every
	// package has, at its usual name when they do not say.
	plain := open(t, "campus-hq.storeypath")
	moved := map[string]string{} // usual name → where it is moved to
	for role, name := range plain.Manifest.Files {
		if usualNames[role] != "" {
			moved[name] = "data/" + role + "-" + name
		}
	}
	listMoved := editJSON(FileManifest, func(doc map[string]any) {
		files := doc["files"].(map[string]any)
		for role, name := range files {
			if to, ok := moved[name.(string)]; ok {
				files[role] = to
			}
		}
	})
	missing := 0
	for _, pr := range rewriteFrom(t, "campus-hq.storeypath", listMoved).Validate() {
		if pr.Code == ProblemMissingFile && strings.HasPrefix(pr.File, "data/") {
			missing++
		}
	}
	if missing != len(moved) {
		t.Fatalf("the manifest lists %d files where they are not: %d missing", len(moved), missing)
	}
	p, err := readZip(renamed(t, zipFrom(t, "campus-hq.storeypath", listMoved, zip.Deflate), moved), DefaultLimits)
	if err != nil {
		t.Fatal(err)
	}
	if problems := p.Validate(); len(problems) != 0 {
		t.Fatalf("every file moved, and listed where it is: %v", problems)
	}
	if len(p.Spaces) != len(plain.Spaces) || len(p.Items) != len(plain.Items) || p.Catalogue == nil ||
		len(p.Objects) != len(plain.Objects) || p.Changes == nil || p.file("spaces") != "data/spaces-spaces.geojson" {
		t.Errorf("%d spaces, %d items, catalogue %v, %d objects, changes %v", len(p.Spaces), len(p.Items),
			p.Catalogue != nil, len(p.Objects), p.Changes != nil)
	}
	// spaces listed at rooms.geojson, not there: the problem names the file listed
	rooms := rewriteFrom(t, "campus-hq.storeypath", editJSON(FileManifest, func(doc map[string]any) {
		doc["files"].(map[string]any)["spaces"] = "rooms.geojson"
	}))
	if problems := rooms.Validate(); len(problems) == 0 || problems[0].Code != ProblemMissingFile || problems[0].File != "rooms.geojson" {
		t.Errorf("spaces listed at rooms.geojson, not there: %v", problems)
	}
	// a name of a MiB (stored: compressed, the manifest is too many times its size)
	long, err := readZip(zipFrom(t, "campus-hq.storeypath", editJSON(FileManifest, func(doc map[string]any) {
		doc["files"].(map[string]any)["spaces"] = strings.Repeat("x", 1<<20)
	}), zip.Store), DefaultLimits)
	if err != nil {
		t.Fatal(err)
	}
	if problems := long.Validate(); len(problems) == 0 || len(problems[0].File) > 200 || len(problems[0].Message) > 300 {
		t.Errorf("a name of a MiB: %d problems", len(problems))
	}
	// a manifest that does not list a file every package has: at its usual name
	unlisted := rewriteFrom(t, "campus-hq.storeypath", editJSON(FileManifest, func(doc map[string]any) {
		delete(doc["files"].(map[string]any), "spaces")
		delete(doc["files"].(map[string]any), "objects")
	}))
	if problems := unlisted.Validate(); len(problems) != 0 || len(unlisted.Spaces) != len(plain.Spaces) {
		t.Errorf("spaces and objects not listed: %v", problems)
	}
}

// renamed is a package's ZIP with files renamed (from → to).
func renamed(t *testing.T, data []byte, names map[string]string) []byte {
	t.Helper()
	src, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	w := zip.NewWriter(&buf)
	for _, f := range src.File {
		rc, _ := f.Open()
		d, _ := io.ReadAll(rc)
		rc.Close()
		name := f.Name
		if to, ok := names[name]; ok {
			name = to
		}
		out, _ := w.Create(name)
		out.Write(d)
	}
	w.Close()
	return buf.Bytes()
}

func TestAKeyWrittenInAnotherCaseIsABrokenFile(t *testing.T) {
	// encoding/json reads "Hidden" into Hidden, the last of "hidden" and "Hidden"
	// winning: every other reader keeps to "hidden". Such a file is broken.
	first := func(file string, change func(f map[string]any)) func(string, []byte) []byte {
		return editJSON(file, func(doc map[string]any) { change(features(doc)[0].(map[string]any)) })
	}
	for _, c := range []struct {
		name, file, key string
		edit            func(string, []byte) []byte
	}{
		{"a property", FileSpaces, `"Hidden"`, first(FileSpaces, func(f map[string]any) {
			props(f)["hidden"], props(f)["Hidden"] = false, true
		})},
		{"a property of an embedded struct", FileSpaces, `"Capacity"`, first(FileSpaces, func(f map[string]any) { props(f)["Capacity"] = 40 })},
		{"its kind", FileZones, `"KIND"`, first(FileZones, func(f map[string]any) { props(f)["KIND"] = "zone" })},
		{"its kind, with a Kelvin sign", FileZones, `"Kind"`, first(FileZones, func(f map[string]any) { props(f)["Kind"] = "zone" })},
		{"a feature's ID", FileOpenings, `"Id"`, first(FileOpenings, func(f map[string]any) { f["Id"] = "X" })},
		{"a geometry's type", FileFloors, `"TYPE"`, first(FileFloors, func(f map[string]any) {
			f["geometry"].(map[string]any)["TYPE"] = "Point"
		})},
		{"walls' type", FileFloors, `"Type"`, first(FileFloors, func(f map[string]any) {
			props(f)["walls"].(map[string]any)["Type"] = "Point"
		})},
		{"an item's position in its building", FileItems, `"X_M"`, first(FileItems, func(f map[string]any) {
			props(f)["local"].(map[string]any)["X_M"] = 1000.0
		})},
		{"a collection's features", FileSpaces, `"Features"`, editJSON(FileSpaces, func(doc map[string]any) { doc["Features"] = []any{} })},
		{"a catalogue type's colour", FileCatalogue, `"Color"`, editJSON(FileCatalogue, func(doc map[string]any) {
			doc["types"].([]any)[0].(map[string]any)["Color"] = "#ff0000"
		})},
		{"changes", FileChanges, `"All_Retired"`, editJSON(FileChanges, func(doc map[string]any) { doc["All_Retired"] = []any{} })},
	} {
		t.Run(c.name, func(t *testing.T) {
			problems := rewriteFrom(t, "campus-hq.storeypath", c.edit).Validate()
			if len(problems) == 0 || problems[0].Code != ProblemBadFile || problems[0].File != c.file || !strings.Contains(problems[0].Message, c.key) {
				t.Errorf("%v", problems)
			}
		})
	}
	// an escape spelling the key exactly is the key; keys of no field, and the
	// item's values (any key), are left alone
	p := rewriteFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		switch n {
		case FileSpaces:
			return bytes.Replace(d, []byte(`"hidden"`), []byte(`"hidden"`), 1)
		case FileItems:
			var doc map[string]any
			json.Unmarshal(d, &doc)
			props(features(doc)[0])["Colour"] = "red"
			props(features(doc)[0])["values"] = map[string]any{"Model": "X", "model": "Y"}
			d, _ = json.Marshal(doc)
		}
		return d
	})
	if problems := p.Validate(); len(problems) != 0 || p.Items[0].Values["Model"] != "X" {
		t.Errorf("problems: %v", problems)
	}
	// in the manifest, the package cannot be read
	data := zipFrom(t, "campus-hq.storeypath", editJSON(FileManifest, func(doc map[string]any) { doc["Format_Version"] = "0.1.0" }), zip.Deflate)
	if _, err := readZip(data, DefaultLimits); err == nil || !strings.Contains(err.Error(), `"Format_Version"`) {
		t.Errorf("manifest: %v", err)
	}
}

func TestValuesAreThoseTheFormatAllows(t *testing.T) {
	// The values Studio's validator checks: types the manifest lists, a capacity
	// of none or more and what says so, grades, mounts, categories and colours.
	first := func(file string, change func(p map[string]any)) func(string, []byte) []byte {
		return editJSON(file, func(doc map[string]any) { change(props(features(doc)[0])) })
	}
	firstType := func(change func(ty map[string]any)) func(string, []byte) []byte {
		return editJSON(FileCatalogue, func(doc map[string]any) { change(doc["types"].([]any)[0].(map[string]any)) })
	}
	for _, c := range []struct {
		name string
		edit func(string, []byte) []byte
		ok   bool
	}{
		{"a space type not listed", first(FileSpaces, func(p map[string]any) { p["type"] = "spaceship" }), false},
		{"a space with no type", first(FileSpaces, func(p map[string]any) { delete(p, "type") }), false},
		{"a zone type not listed", first(FileZones, func(p map[string]any) { p["type"] = "door" }), false},
		{"an opening type not listed", first(FileOpenings, func(p map[string]any) { p["type"] = "portal" }), false},
		{"a type the manifest lists (a later format's)", func(n string, d []byte) []byte {
			switch n {
			case FileManifest:
				return editJSON(n, func(doc map[string]any) {
					types := doc["types"].(map[string]any)
					types["space"] = append(types["space"].([]any), "gym")
				})(n, d)
			case FileSpaces:
				return first(n, func(p map[string]any) { p["type"] = "gym" })(n, d)
			}
			return d
		}, true},
		{"a capacity less than none", first(FileSpaces, func(p map[string]any) { p["capacity"], p["capacity_from"] = -3, "review" }), false},
		{"a capacity of none", first(FileSpaces, func(p map[string]any) { p["capacity"], p["capacity_from"] = 0, "review" }), true},
		{"capacity from a guess", first(FileSpaces, func(p map[string]any) { p["capacity"], p["capacity_from"] = 4, "guess" }), false},
		{"capacity from review, of no capacity", first(FileZones, func(p map[string]any) { p["capacity"], p["capacity_from"] = nil, "review" }), false},
		{"a grade not of the format", first(FileSpaces, func(p map[string]any) { p["grade"] = "ceo" }), false},
		{"a grade", first(FileZones, func(p map[string]any) { p["grade"] = "section_head" }), true},
		{"an item mounted nowhere", first(FileItems, func(p map[string]any) { p["mount"] = "floating" }), false},
		{"an item of no category", first(FileItems, func(p map[string]any) { p["category"] = "food" }), false},
		{"a colour that is not one", firstType(func(ty map[string]any) { ty["color"] = "red;fill:url(https://example.com/beacon.svg#a)" }), false},
		{"a colour in capitals", firstType(func(ty map[string]any) { ty["color"] = "#A1B2C3" }), true},
		{"a type's colour, category and mount left out (Studio's defaults)", firstType(func(ty map[string]any) {
			delete(ty, "color")
			delete(ty, "category")
			delete(ty, "mount")
		}), true},
		{"a type's category", firstType(func(ty map[string]any) { ty["category"] = "food" }), false},
		{"a type's mount", firstType(func(ty map[string]any) { ty["mount"] = "roof" }), false},
		{"a type's grade", firstType(func(ty map[string]any) { ty["grade"] = "ceo" }), false},
		{"a type's workplaces", firstType(func(ty map[string]any) { ty["workplaces"] = -1 }), false},
		{"a type's workplaces, too many", firstType(func(ty map[string]any) { ty["workplaces"] = 101 }), false},
	} {
		t.Run(c.name, func(t *testing.T) {
			problems := rewriteFrom(t, "campus-hq.storeypath", c.edit).Validate()
			if c.ok && len(problems) != 0 {
				t.Errorf("problems: %v", problems)
			}
			if !c.ok && !slices.Equal(codes(problems), []string{ProblemValue}) {
				t.Errorf("problems %v, want VALUE", problems)
			}
		})
	}
	// a manifest that lists no types: this format's
	untyped := rewriteFrom(t, "simple-office.storeypath", editJSON(FileManifest, func(doc map[string]any) { delete(doc, "types") }))
	if problems := untyped.Validate(); len(problems) != 0 {
		t.Errorf("no types listed: %v", problems)
	}
}

func TestAProjectFileIsNotAPackage(t *testing.T) {
	var buf bytes.Buffer
	w := zip.NewWriter(&buf)
	f, _ := w.Create("project.json")
	f.Write([]byte(`{"format": "storeypath-project"}`))
	w.Close()
	_, err := Read(bytes.NewReader(buf.Bytes()), int64(buf.Len()), DefaultLimits)
	if err == nil || !strings.Contains(err.Error(), "project file") {
		t.Errorf("error %v", err)
	}
}

func TestSeating(t *testing.T) {
	// How many a room seats and who it is for: the open office (117) set to 8 in
	// review; offices as their desks say, the President's office in the Annex.
	byNumber := map[string]Unit{}
	for _, name := range []string{"campus-hq.storeypath", "campus-annex.storeypath"} {
		p := open(t, name)
		for _, f := range p.Floors {
			for _, u := range p.UnitsOn(f.ID) {
				if u.Number != nil {
					byNumber[p.Buildings[0].Code+" "+*u.Number] = u
				}
			}
		}
		if ty := p.ItemType("DESK-PRESIDENT"); ty == nil || ty.Workplaces != 1 || ty.Grade == nil || *ty.Grade != "president" {
			t.Errorf("%s: DESK-PRESIDENT %+v", name, ty)
		}
	}
	str := func(s *string) string {
		if s == nil {
			return ""
		}
		return *s
	}
	seats := func(n string) (int, string, string) {
		u, ok := byNumber[n]
		if !ok || u.Capacity == nil {
			return -1, str(u.CapacityFrom), str(u.Grade)
		}
		return *u.Capacity, str(u.CapacityFrom), str(u.Grade)
	}
	for _, want := range []struct {
		number, from, grade string
		capacity            int
	}{{"HQ 117", "review", "junior", 8}, {"HQ 002", "items", "senior", 2}, {"ANNEX 001", "items", "president", 1}} {
		if c, f, g := seats(want.number); c != want.capacity || f != want.from || g != want.grade {
			t.Errorf("%s: %d %s %s, want %d %s %s", want.number, c, f, g, want.capacity, want.from, want.grade)
		}
	}
}

func TestAPackageNewerThanTheReaderIsRefused(t *testing.T) {
	// Before 1.0 a minor version may change what a package means: a newer one is
	// refused with a message to update the reader; older ones and newer patches are read.
	for v, ok := range map[string]bool{FormatVersion: true, "0.6.0": true, "0.4.1": true, "0.7.9": true,
		"0.8.0": false, "0.10.0": false, "1.0.0": false} {
		if err := CheckVersion(v); (err == nil) != ok {
			t.Errorf("%s: %v", v, err)
		}
	}
	newer := rewriteFrom(t, "campus-hq.storeypath", editJSON(FileManifest, func(doc map[string]any) { doc["format_version"] = "0.8.0" }))
	problems := newer.Validate()
	if !slices.Contains(codes(problems), ProblemVersion) || !strings.Contains(problems[0].Message, "update the reader") {
		t.Errorf("0.8.0: %v", problems)
	}
}

func TestAFormatVersionIsReadStrictly(t *testing.T) {
	// Read loosely, "0.8a.0" and "0.8-rc1" were 0.0 and 0.8 read as 0.0: a
	// newer package read, without the rules of 0.7. What is not a format
	// version is refused, and every rule applies to it.
	for v, want := range map[string]string{
		"0.7.0": "", "0.6.0": "", "0.4.1": "", "0.7.9": "", "0.7": "", "0.7.0-rc1": "", "0.7.0+build.5": "", "0.07.0": "",
		"0.8.0": "newer", "0.8": "newer", "0.8-rc1": "newer", "0.10.0": "newer", "0.99999999999999999999.0": "newer",
		"1.0.0": "unsupported", "99999999999999999999.0.0": "unsupported",
		"0.8a.0": "not a format version", "0.x": "not a format version", "0.-1.0": "not a format version",
		"0. 8.0": "not a format version", "0.1_0.0": "not a format version", "": "not a format version",
		"abc": "not a format version", "0": "not a format version", " 0.7.0": "not a format version",
		"0.7.0 ": "not a format version", "0.+8.0": "not a format version", "v0.7.0": "not a format version",
		"0.7.0.1": "not a format version", "٠.٧.٠": "not a format version", "0.7.0-": "not a format version",
		"0.7.0-rc 1": "not a format version", "0.7.0-rc1\n": "not a format version",
	} {
		err := CheckVersion(v)
		if (want == "") != (err == nil) || (err != nil && !strings.Contains(err.Error(), want)) {
			t.Errorf("%q: %v, want %q", v, err, want)
		}
	}
	// campus (0.6: two buildings, items with no position in their building) labelled
	// so: not read as 0.0, but refused, and held to 0.7's rules
	for _, v := range []string{"0.8a.0", "0.8-rc1"} {
		p := rewriteFrom(t, "campus.storeypath", editJSON(FileManifest, func(doc map[string]any) { doc["format_version"] = v }))
		if got := codes(p.Validate()); !slices.Contains(got, ProblemVersion) || !slices.Contains(got, ProblemScope) {
			t.Errorf("campus as %s: %v", v, got)
		}
	}
}

func TestTheManifestAloneTellsWhetherToReadAPackage(t *testing.T) {
	// A server can refuse a package by its manifest before reading the rest: here
	// a package whose spaces.geojson Read refuses, as too large.
	feature := `{"type":"Feature","id":"X","geometry":null,"properties":{"kind":"space"}},`
	data := zipFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		switch n {
		case FileSpaces:
			return []byte(`{"type":"FeatureCollection","features":[` + strings.Repeat(feature, (8<<20)/len(feature)) + `{}]}`)
		case FileManifest:
			var doc map[string]any
			json.Unmarshal(d, &doc)
			doc["format_version"] = "0.8.0"
			d, _ = json.Marshal(doc)
		}
		return d
	}, zip.Deflate)
	if _, err := readZip(data, DefaultLimits); !errors.Is(err, ErrTooLarge) {
		t.Fatalf("Read: %v", err)
	}
	m, err := ReadManifest(bytes.NewReader(data), int64(len(data)), DefaultLimits)
	if err != nil || m.Project.ID != "SD8YHE" || m.FormatVersion != "0.8.0" || CheckVersion(m.FormatVersion) == nil {
		t.Errorf("ReadManifest: %+v %v", m, err)
	}
	if _, err := ReadManifest(bytes.NewReader(data), int64(len(data)), Limits{MaxFileBytes: 100}); !errors.Is(err, ErrTooLarge) {
		t.Errorf("a manifest larger than the limit: %v", err)
	}
	if _, err := ReadManifest(bytes.NewReader([]byte("not a zip")), 9, DefaultLimits); err == nil {
		t.Error("not a ZIP: a manifest")
	}
	noManifest := zipFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		if n == FileManifest {
			return nil
		}
		return d
	}, zip.Deflate)
	if _, err := ReadManifest(bytes.NewReader(noManifest), int64(len(noManifest)), DefaultLimits); err == nil {
		t.Error("no manifest.json: a manifest")
	}
}
