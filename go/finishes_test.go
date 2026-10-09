package storeypath

import (
	"bytes"
	"os"
	"slices"
	"strings"
	"testing"
)

func TestTheFinishesAreTheSpecsList(t *testing.T) {
	// finishes.json is a copy of spec/finishes.json (spec/finishes.mjs makes it)
	spec, err := os.ReadFile("../spec/finishes.json")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(spec, finishesJSON) {
		t.Fatal("go/finishes.json is not spec/finishes.json as it is: run node spec/finishes.mjs")
	}
}

func TestTheFinishesAreWhole(t *testing.T) {
	l := Finishes()
	groups := map[string]string{}
	for _, g := range l.Groups {
		if g.Name == "" || g.NameAr == "" || (g.Applies != "floor" && g.Applies != "wall") || groups[g.Code] != "" {
			t.Errorf("group %+v", g)
		}
		groups[g.Code] = g.Applies
	}
	seen := map[string]bool{}
	counts := map[string]int{}
	for _, f := range l.Finishes {
		if seen[f.Code] {
			t.Errorf("%s twice", f.Code)
		}
		seen[f.Code] = true
		counts[f.Applies]++
		if !wellFormedFinish(f.Code, f.Applies) || !strings.HasPrefix(f.Code, strings.ToUpper(f.Applies)+"-") {
			t.Errorf("%s: not a %s finish's code", f.Code, f.Applies)
		}
		if groups[f.Group] != f.Applies {
			t.Errorf("%s: group %q is not a %s group", f.Code, f.Group, f.Applies)
		}
		if f.Name == "" || f.NameAr == "" || !colourRE.MatchString(f.Tone) || f.SizeM <= 0 || f.Roughness <= 0 ||
			f.Roughness > 1 || f.Paint["kind"] == "" {
			t.Errorf("%s: %+v", f.Code, f)
		}
		if FinishOf(f.Code) == nil || FinishOf(f.Code).Code != f.Code {
			t.Errorf("%s: not found by its code", f.Code)
		}
	}
	if counts["floor"] < 25 || counts["wall"] < 15 || len(l.Finishes) < 40 || len(l.Finishes) > 60 {
		t.Errorf("finishes: %v", counts)
	}
	// a default for every type, of what it applies to; the exterior's a wall's
	for _, applies := range []string{"floor", "wall"} {
		for _, typ := range spaceTypes {
			code, ok := l.Defaults[applies][typ]
			if f := FinishOf(code); !ok || f == nil || f.Applies != applies {
				t.Errorf("%s: no %s default (%q)", typ, applies, code)
			}
		}
	}
	if f := FinishOf(l.Exterior); f == nil || f.Applies != "wall" {
		t.Errorf("exterior %q", l.Exterior)
	}
}

func TestARoomsFinishIsItsOwnElseItsSpacesElseItsTypes(t *testing.T) {
	str := func(s string) *string { return &s }
	office := &Space{Type: "office"}
	if office.FloorFinishOf() != DefaultFinish("floor", "office") || office.WallFinishOf() != DefaultFinish("wall", "office") {
		t.Errorf("an office given none: %s, %s", office.FloorFinishOf(), office.WallFinishOf())
	}
	office.FloorFinish, office.WallFinish = str("FLOOR-CARPET-NAVY"), str("WALL-PAINT-NAVY")
	if office.FloorFinishOf() != "FLOOR-CARPET-NAVY" || office.WallFinishOf() != "WALL-PAINT-NAVY" {
		t.Errorf("an office given its own: %s, %s", office.FloorFinishOf(), office.WallFinishOf())
	}
	// a code not known (a later version's), or for the other side: the default
	odd := &Space{Type: "restroom", FloorFinish: str("FLOOR-MOON-DUST"), WallFinish: str("FLOOR-CARPET-NAVY")}
	if odd.FloorFinishOf() != DefaultFinish("floor", "restroom") || odd.WallFinishOf() != "WALL-TILE-WHITE" {
		t.Errorf("unknown codes: %s, %s", odd.FloorFinishOf(), odd.WallFinishOf())
	}
	zone := &Zone{Type: "corridor"}
	if zone.FloorFinishOf(office) != "FLOOR-CARPET-NAVY" || zone.FloorFinishOf(nil) != DefaultFinish("floor", "corridor") {
		t.Errorf("a zone: its space's, else its type's")
	}
	zone.FloorFinish = str("FLOOR-VINYL-GREY")
	if zone.FloorFinishOf(office) != "FLOOR-VINYL-GREY" {
		t.Errorf("a zone's own")
	}
	if DefaultFinish("floor", "spaceship") != DefaultFinish("floor", "unspecified") {
		t.Error("an unknown type: as unspecified")
	}
}

func TestTheConformancePackagesHaveFinishes(t *testing.T) {
	p := open(t, "campus-hq.storeypath")
	got := map[string]string{}
	for _, s := range p.Spaces {
		if s.FloorFinish != nil || s.WallFinish != nil {
			got[s.Type] = s.FloorFinishOf() + " " + s.WallFinishOf()
		}
	}
	want := map[string]string{"lobby": "FLOOR-MARBLE-WHITE WALL-STONE", "meeting_room": "FLOOR-WOOD-HERRINGBONE WALL-WOOD-SLATS",
		"office": "FLOOR-CARPET-NAVY WALL-PAPER-LINEN", "restroom": "FLOOR-PORCELAIN-DARK WALL-TILE-MOSAIC",
		"unspecified": DefaultFinish("floor", "unspecified") + " WALL-PAINT-GREEN"}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s: %q, want %q", k, got[k], v)
		}
	}
	zones := []string{}
	for _, z := range p.Zones {
		if z.FloorFinish != nil {
			zones = append(zones, z.Type+" "+*z.FloorFinish)
		}
	}
	slices.Sort(zones)
	if strings.Join(zones, ",") != "corridor FLOOR-VINYL-GREY,open_area FLOOR-TERRAZZO-LIGHT" {
		t.Errorf("zones: %v", zones)
	}
	// older packages: none, every room as its type
	for _, s := range open(t, "campus.storeypath").Spaces {
		if s.FloorFinish != nil || s.WallFinish != nil {
			t.Errorf("campus (0.6): %s has finishes", s.ID)
		}
	}
}

func TestAFinishNotOfTheFormIsRefused(t *testing.T) {
	for _, tc := range []struct {
		file, key, value string
		bad              bool
	}{
		{FileSpaces, "floor_finish", "FLOOR-CARPET-NAVY", false},
		{FileSpaces, "floor_finish", "FLOOR-LATER-ONE", false}, // a later version's: read as the default
		{FileSpaces, "wall_finish", "WALL-PAINT-NAVY", false},
		{FileSpaces, "floor_finish", "WALL-PAINT-NAVY", true}, // a wall's on a floor
		{FileSpaces, "wall_finish", "FLOOR-CARPET-NAVY", true},
		{FileSpaces, "floor_finish", "floor-carpet-navy", true},
		{FileSpaces, "floor_finish", "FLOOR-", true},
		{FileZones, "floor_finish", "carpet", true},
		{FileZones, "floor_finish", "FLOOR-" + strings.Repeat("X", 40), true},
	} {
		p := rewriteFrom(t, "campus-hq.storeypath", editJSON(tc.file, func(doc map[string]any) {
			f := doc["features"].([]any)[0].(map[string]any)
			f["properties"].(map[string]any)[tc.key] = tc.value
		}))
		got := codes(p.Validate())
		if slices.Contains(got, ProblemValue) != tc.bad {
			t.Errorf("%s %s %q: %v", tc.file, tc.key, tc.value, got)
		}
	}
}
