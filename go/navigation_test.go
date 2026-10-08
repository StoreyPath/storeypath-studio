package storeypath

import (
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"
)

// routesFile is spec/conformance/routes.json: ways every reader must find the same.
type routesFile struct {
	ToleranceM float64 `json:"tolerance_m"`
	ToleranceS float64 `json:"tolerance_s"`
	Routes     []struct {
		Name       string         `json:"name"`
		Package    string         `json:"package"`
		From       string         `json:"from"`
		To         string         `json:"to"`
		Accessible bool           `json:"accessible"`
		Expect     map[string]any `json:"expect"`
	} `json:"routes"`
}

func conformanceRoutes(t *testing.T) routesFile {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(corpus, "routes.json"))
	if err != nil {
		t.Fatal(err)
	}
	var rf routesFile
	if err := json.Unmarshal(data, &rf); err != nil {
		t.Fatal(err)
	}
	if len(rf.Routes) < 5 {
		t.Fatalf("%d ways in routes.json", len(rf.Routes))
	}
	return rf
}

// asJSON is a value as JSON reads it back: maps, lists, float64s, strings, nil.
func asJSON(t *testing.T, v any) any {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestEachConformanceWayIsFoundAsEveryReaderFindsIt(t *testing.T) {
	rf := conformanceRoutes(t)
	for _, c := range rf.Routes {
		t.Run(c.Name, func(t *testing.T) {
			r, err := open(t, c.Package).Route(c.From, c.To, RouteOptions{Accessible: c.Accessible})
			if err != nil {
				t.Fatal(err)
			}
			got, want := asJSON(t, r).(map[string]any), c.Expect
			for _, key := range []string{"from", "to", "accessible", "nodes", "changes", "steps"} {
				if !reflect.DeepEqual(got[key], want[key]) {
					t.Errorf("%s:\n got %v\nwant %v", key, got[key], want[key])
				}
			}
			if math.Abs(r.Metres-want["metres"].(float64)) > rf.ToleranceM ||
				math.Abs(r.Seconds-want["seconds"].(float64)) > rf.ToleranceS {
				t.Errorf("%v m, %v s; want %v m, %v s", r.Metres, r.Seconds, want["metres"], want["seconds"])
			}
			legs := want["legs"].([]any)
			if len(r.Legs) != len(legs) {
				t.Fatalf("%d legs, want %d", len(r.Legs), len(legs))
			}
			for i, leg := range legs {
				w, g := leg.(map[string]any), asJSON(t, r.Legs[i]).(map[string]any)
				if g["floor_id"] != w["floor_id"] || !reflect.DeepEqual(g["points"], w["points"]) ||
					math.Abs(r.Legs[i].Metres-w["metres"].(float64)) > rf.ToleranceM {
					t.Errorf("leg %d: %v, want %v", i, g, w)
				}
			}
		})
	}
}

func TestAWayIsFoundTheSameFromManyGoroutines(t *testing.T) {
	rf := conformanceRoutes(t)
	c := rf.Routes[0]
	p := open(t, c.Package)
	var wg sync.WaitGroup
	got := make([][]string, 8)
	for i := range got {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if r, err := p.Route(c.From, c.To, RouteOptions{Accessible: c.Accessible}); err == nil {
				got[i] = r.Nodes
			}
		}()
	}
	wg.Wait()
	want := asJSON(t, c.Expect["nodes"])
	for i, nodes := range got {
		if !reflect.DeepEqual(asJSON(t, nodes), want) {
			t.Errorf("goroutine %d: %v", i, nodes)
		}
	}
}

func TestTheNetworkOfAPackage(t *testing.T) {
	p := open(t, "campus-hq.storeypath")
	n := p.Navigation()
	if n == nil {
		t.Fatal("no network")
	}
	if n.SpeedMS != 1.3 || len(n.Buildings) != 1 || n.Buildings[0] != p.Buildings[0].ID || len(n.Floors) != 3 {
		t.Fatalf("speed %v, buildings %v, %d floors", n.SpeedMS, n.Buildings, len(n.Floors))
	}
	kinds := map[string]int{}
	for _, node := range n.Nodes {
		kinds[node.Kind]++
	}
	for _, kind := range []string{"door", "entrance", "approach", "room", "kiosk", "lift", "stairs"} {
		if kinds[kind] == 0 {
			t.Errorf("no %s node", kind)
		}
	}
	kiosk := p.KiosksOn(p.Buildings[0].ID + "-F00")[0]
	node := n.Node("kiosk:" + kiosk.ID)
	if node == nil || node.Item == nil || *node.Item != kiosk.ID || !reflect.DeepEqual(node.Space, kiosk.Space) {
		t.Errorf("the kiosk's node: %+v", node)
	}
	for _, e := range n.Edges {
		if e.Kind == "stairs" && e.Accessible || e.Kind == "lift" && !e.Accessible {
			t.Errorf("%s edge accessible %v", e.Kind, e.Accessible)
		}
	}
	if place := n.Place(*kiosk.Space); place == nil || place.Label != "RECEPTION 017" {
		t.Errorf("the reception: %+v", place)
	}
}

func TestStacksLinkTheLiftsAndStairsOfEachFloor(t *testing.T) {
	p := open(t, "campus-hq.storeypath")
	byStack := map[string][]*Space{}
	for _, s := range p.Spaces {
		switch s.Type {
		case "elevator", "stairs", "escalator":
			if s.Stack == nil {
				t.Errorf("%s (%s): no stack", s.ID, s.Type)
				continue
			}
			byStack[*s.Stack] = append(byStack[*s.Stack], s)
		default:
			if s.Stack != nil {
				t.Errorf("%s (%s): stack %s", s.ID, s.Type, *s.Stack)
			}
		}
	}
	if len(byStack) != 3 { // two lifts and a staircase
		t.Fatalf("%d stacks", len(byStack))
	}
	floors := p.FloorsOf(p.Buildings[0].ID)
	for key, members := range byStack {
		var on []string
		for _, s := range members {
			on = append(on, s.Floor)
			if s.Type != members[0].Type {
				t.Errorf("stack %s: a %s and a %s", key, s.Type, members[0].Type)
			}
		}
		slices.Sort(on)
		if len(on) != 3 || p.Space(key) == nil || p.Space(key).Floor != floors[0].ID {
			t.Errorf("stack %s on %v: named after its space on the ground floor", key, on)
		}
	}
	if s := open(t, "campus.storeypath").Spaces[0]; s.Stack != nil { // 0.6: none
		t.Errorf("an older package's stack: %v", *s.Stack)
	}
}

func TestAPackageWithoutANetworkHasNoWay(t *testing.T) {
	p := open(t, "campus.storeypath")
	if p.Navigation() != nil {
		t.Fatal("a 0.6 package with a network")
	}
	if _, err := p.Route(p.Spaces[0].ID, p.Spaces[1].ID, RouteOptions{}); !errors.Is(err, ErrNoNavigation) {
		t.Errorf("Route: %v", err)
	}
	// a 0.8 package may lack it too
	none := rewriteFrom(t, "campus-hq.storeypath", func(n string, d []byte) []byte {
		if n == FileNavigation {
			return nil
		}
		if n == FileManifest {
			var doc map[string]any
			json.Unmarshal(d, &doc)
			delete(doc["files"].(map[string]any), "navigation")
			d, _ = json.Marshal(doc)
		}
		return d
	})
	if none.Navigation() != nil || len(none.Validate()) != 0 {
		t.Errorf("without navigation.json: %v", none.Validate())
	}
}

func TestWithoutStairsThereMayBeNoWay(t *testing.T) {
	rf := conformanceRoutes(t)
	c := rf.Routes[0] // the kiosk to an office upstairs
	// the lifts out of order: no way up without stairs; by stairs, as before
	p := rewriteFrom(t, c.Package, editJSON(FileNavigation, func(doc map[string]any) {
		for _, e := range doc["edges"].([]any) {
			if e := e.(map[string]any); e["kind"] == "lift" {
				e["accessible"] = false
			}
		}
	}))
	if _, err := p.Route(c.From, c.To, RouteOptions{Accessible: true}); !errors.Is(err, ErrNoRoute) ||
		!strings.Contains(err.Error(), "without stairs") {
		t.Errorf("without stairs: %v", err)
	}
	if r, err := p.Route(c.From, c.To, RouteOptions{}); err != nil || r.Changes[0].By != "stairs" {
		t.Errorf("by stairs: %v", err)
	}
	for _, unknown := range []string{"nowhere", "door:nowhere", ""} {
		if _, err := p.Route(unknown, c.To, RouteOptions{}); !errors.Is(err, ErrNotInNetwork) {
			t.Errorf("from %q: %v", unknown, err)
		}
	}
}

func TestAWayFromAPlaceToItself(t *testing.T) {
	p := open(t, "campus-hq.storeypath")
	kiosk := p.KiosksOn(p.Buildings[0].ID + "-F00")[0]
	r, err := p.Route(kiosk.ID, "kiosk:"+kiosk.ID, RouteOptions{})
	if err != nil || len(r.Nodes) != 1 || r.Metres != 0 || len(r.Legs) != 1 || len(r.Changes) != 0 {
		t.Fatalf("%+v %v", r, err)
	}
	last := r.Steps[len(r.Steps)-1]
	if last.Kind != "arrive" || last.Side != "here" || last.Text != "You are at RECEPTION 017" {
		t.Errorf("%+v", last)
	}
	data, _ := json.Marshal(last)
	if string(data) != `{"kind":"arrive","place":"`+*kiosk.Space+`","floor_id":"`+kiosk.Floor+`","side":"here","text":"You are at RECEPTION 017"}` {
		t.Errorf("as JSON: %s", data)
	}
}

func TestABrokenNetworkIsFound(t *testing.T) {
	edit := func(change func(doc map[string]any)) []Problem {
		return rewriteFrom(t, "campus-hq.storeypath", editJSON(FileNavigation, change)).Validate()
	}
	nodes := func(doc map[string]any) []any { return doc["nodes"].([]any) }
	edges := func(doc map[string]any) []any { return doc["edges"].([]any) }
	cases := map[string]struct {
		change func(doc map[string]any)
		words  string
	}{
		"an edge to a node it does not have": {func(doc map[string]any) {
			edges(doc)[0].(map[string]any)["to"] = "door:NOWHERE"
		}, "unknown node door:NOWHERE"},
		"a node twice": {func(doc map[string]any) {
			doc["nodes"] = append(nodes(doc), nodes(doc)[0])
		}, "is there twice"},
		"a node on a floor not in the package": {func(doc map[string]any) {
			nodes(doc)[0].(map[string]any)["floor_id"] = "X-Y-Z-F09"
		}, "unknown floor X-Y-Z-F09"},
		"a node in a space not in the package": {func(doc map[string]any) {
			for _, n := range nodes(doc) {
				if n := n.(map[string]any); n["kind"] == "room" {
					n["space_id"] = "X-Y-Z-F09-0001"
					return
				}
			}
		}, "unknown space X-Y-Z-F09-0001"},
		"two edges joining one pair": {func(doc map[string]any) {
			e := edges(doc)[0].(map[string]any)
			twin := map[string]any{}
			for k, v := range e {
				twin[k] = v
			}
			twin["from"], twin["to"] = e["to"], e["from"]
			doc["edges"] = append(edges(doc), twin)
		}, "two edges join"},
		"an edge with no line": {func(doc map[string]any) {
			e := edges(doc)[0].(map[string]any)
			e["path"] = e["path"].([]any)[:1]
		}, "has no line"},
		"a building not in the package": {func(doc map[string]any) {
			doc["buildings"] = []any{"X-Y-Z"}
		}, "building X-Y-Z is not in the package"},
	}
	for name, c := range cases {
		problems := edit(c.change)
		found := false
		for _, pr := range problems {
			if pr.Code == ProblemNavigation && strings.Contains(pr.Message, c.words) {
				found = true
			}
		}
		if !found {
			t.Errorf("%s: %v", name, problems)
		}
	}
	// a key in another case, or a string for a number: the file cannot be read as it is
	for name, change := range map[string]func(doc map[string]any){
		"Nodes":       func(doc map[string]any) { doc["Nodes"] = doc["nodes"]; delete(doc, "nodes") },
		"cost a word": func(doc map[string]any) { edges(doc)[0].(map[string]any)["cost"] = "1.2" },
	} {
		if got := codes(edit(change)); !slices.Contains(got, ProblemBadFile) {
			t.Errorf("%s: %v", name, got)
		}
	}
}
