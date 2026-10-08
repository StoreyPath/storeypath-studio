package storeypath

import (
	"container/heap"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
	"sync"
)

// Navigation (format 0.8) is navigation.json: the walking network of the package's
// building. Nodes are the points a person is at or passes (doors and the points in
// front of them, a point in each space and zone, lifts and stairs on each floor,
// entrances, kiosks); edges the ways between them, each with its length, time and
// cost, its line in the building's own frame, and whether it is accessible (stairs
// and escalators are not). Studio works it out when it exports; a reader finds a way
// on it with Route, the same way every reader does (spec/FORMAT.md, "Navigation").
type Navigation struct {
	// SpeedMS: how fast a person walks, metres a second (what the walks' Seconds are).
	SpeedMS   float64    `json:"speed_m_s"`
	Buildings []string   `json:"buildings"`
	Floors    []NavFloor `json:"floors"` // lowest first
	Places    []NavPlace `json:"places"`
	Nodes     []NavNode  `json:"nodes"`
	Edges     []NavEdge  `json:"edges"`

	once  sync.Once
	graph *navGraph
}

// NavFloor is a floor of the network, as a step names it.
type NavFloor struct {
	ID        string  `json:"id"`
	Building  string  `json:"building_id"`
	Name      string  `json:"name"`
	Ordinal   int     `json:"ordinal"`
	Elevation float64 `json:"elevation"`
}

// NavPlace is a space or zone the network goes through or to, and what a step calls
// it: its Label ("OFFICE 112", "Room 114", "the corridor").
type NavPlace struct {
	ID    string  `json:"id"`
	Kind  string  `json:"kind"` // space or zone
	Space *string `json:"space_id"`
	Floor string  `json:"floor_id"`
	Type  string  `json:"type"`
	Label string  `json:"label"`
}

// NavLocal is a point in the building's own frame, in metres (as items' Local).
type NavLocal struct {
	X float64 `json:"x_m"`
	Y float64 `json:"y_m"`
}

// NavNode is a node of the network. Its ID is made from what it is, so it stays
// from one export to the next: door:<opening ID>, approach:<opening ID>@<space ID>,
// room:<space or zone ID>, kiosk:<item ID>, lift:<object code>@<floor ID>, ….
type NavNode struct {
	ID string `json:"id"`
	// Kind: door, entrance, approach, room, kiosk, lift, stairs, escalator or ramp.
	Kind  string `json:"kind"`
	Floor string `json:"floor_id"`
	// Space and Zone: where it is (nil for a door: Spaces says what it joins).
	Space  *string  `json:"space_id"`
	Zone   *string  `json:"zone_id"`
	Local  NavLocal `json:"local"`
	LonLat LonLat   `json:"lonlat"`
	// Opening: a door's, entrance's or approach's (nil for the way into a lift or
	// stairs no opening is drawn for). Spaces: the spaces a door joins. Item: a
	// kiosk's. Stack: a lift's, stairs', escalator's or ramp's.
	Opening *string  `json:"opening_id"`
	Spaces  []string `json:"spaces"`
	Item    *string  `json:"item_id"`
	Stack   *string  `json:"stack"`
}

// NavEdge is a way between two nodes, both ways; Path is its line from From to To.
type NavEdge struct {
	From string `json:"from"`
	To   string `json:"to"`
	// Kind: walk, door, lift, stairs, escalator or ramp.
	Kind    string  `json:"kind"`
	Length  float64 `json:"length_m"`
	Seconds float64 `json:"seconds"`
	// Cost: what a way takes the fewest of (Seconds, and more for a way into a room
	// not for passing through).
	Cost       float64      `json:"cost"`
	Accessible bool         `json:"accessible"`
	Space      *string      `json:"space_id"`
	Zone       *string      `json:"zone_id"`
	Path       [][2]float64 `json:"path"`
}

// RouteOptions: Accessible takes no edge that is not (no stairs, no escalators).
type RouteOptions struct {
	Accessible bool
}

// Route is a way through the building, as every reader finds it.
type Route struct {
	From       string   `json:"from"`
	To         string   `json:"to"`
	Accessible bool     `json:"accessible"`
	Nodes      []string `json:"nodes"` // its nodes' IDs, in order
	// Metres and Seconds: the sums of its edges', rounded to two decimals.
	Metres  float64 `json:"metres"`
	Seconds float64 `json:"seconds"`
	// Legs: the walking on one floor between rides from floor to floor (one more
	// than the rides). Changes: the rides.
	Legs    []Leg    `json:"legs"`
	Changes []Change `json:"changes"`
	// Steps: what to tell a person, each a kind, its values and its text in English.
	Steps []Step `json:"steps"`
}

// Leg is a way's walking on one floor: the line to draw, in the building's frame.
type Leg struct {
	Floor  string       `json:"floor_id"`
	Points [][2]float64 `json:"points"`
	Metres float64      `json:"metres"`
}

// Change is a ride between floors: by lift, stairs, escalator or ramp.
type Change struct {
	By        string `json:"by"`
	FromFloor string `json:"from_floor_id"`
	ToFloor   string `json:"to_floor_id"`
	FromNode  string `json:"from_node"`
	ToNode    string `json:"to_node"`
	Floors    int    `json:"floors"`    // how many of the building's floors apart
	Direction string `json:"direction"` // up or down
}

// Step is what to tell a person: its Kind (start, walk, take, arrive), the values of
// that kind, and Text in English. A system words steps in its own language from the
// kind and the values; in JSON a step has its kind's keys alone (null where a value
// is not known), as every reader gives it.
type Step struct {
	Kind string `json:"kind"`
	Text string `json:"text"`
	// start: the node and its kind; start, walk, arrive: the place (a space or
	// zone) and the floor
	Node     string  `json:"node"`
	NodeKind string  `json:"node_kind"`
	Place    *string `json:"place"`
	Floor    string  `json:"floor_id"`
	// walk: whole metres, the place most of it is in, and what it goes to (a ride's
	// kind, or "destination")
	Metres int     `json:"metres"`
	Along  *string `json:"along"`
	To     string  `json:"to"`
	// take: the ride
	By        string `json:"by"`
	FromFloor string `json:"from_floor_id"`
	ToFloor   string `json:"to_floor_id"`
	Floors    int    `json:"floors"`
	Direction string `json:"direction"`
	// arrive: left, right, ahead or here
	Side string `json:"side"`
}

// MarshalJSON writes a step with its kind's keys, as Studio does.
func (s Step) MarshalJSON() ([]byte, error) {
	var keys []string
	switch s.Kind {
	case "start":
		keys = []string{"kind", "node", "node_kind", "place", "floor_id", "text"}
	case "walk":
		keys = []string{"kind", "floor_id", "metres", "along", "to", "place", "text"}
	case "take":
		keys = []string{"kind", "by", "from_floor_id", "to_floor_id", "floors", "direction", "text"}
	case "arrive":
		keys = []string{"kind", "place", "floor_id", "side", "text"}
	default:
		keys = []string{"kind", "text"}
	}
	values := map[string]any{"kind": s.Kind, "text": s.Text, "node": s.Node, "node_kind": s.NodeKind, "place": s.Place,
		"floor_id": s.Floor, "metres": s.Metres, "along": s.Along, "to": s.To, "by": s.By, "from_floor_id": s.FromFloor,
		"to_floor_id": s.ToFloor, "floors": s.Floors, "direction": s.Direction, "side": s.Side}
	var b strings.Builder
	b.WriteByte('{')
	for i, k := range keys {
		if i > 0 {
			b.WriteByte(',')
		}
		key, _ := json.Marshal(k)
		value, err := json.Marshal(values[k])
		if err != nil {
			return nil, err
		}
		b.Write(key)
		b.WriteByte(':')
		b.Write(value)
	}
	b.WriteByte('}')
	return []byte(b.String()), nil
}

// ErrNoRoute is wrapped by the error Route returns when there is no way (none, or
// none without stairs).
var ErrNoRoute = errors.New("no way")

// ErrNotInNetwork is wrapped by the error Route returns for an ID the network does
// not know (no node, place or item of it).
var ErrNotInNetwork = errors.New("no node, place or item of the network")

// ErrNoNavigation: the package has no walking network (older than format 0.8, or
// exported without one).
var ErrNoNavigation = errors.New("the package has no walking network (navigation.json, format 0.8)")

// Navigation is the package's walking network (format 0.8), or nil when it has none.
func (p *Package) Navigation() *Navigation { return p.navigation }

// Route is the way from one place to another on the package's network: each a
// node's ID, a space's or zone's (where it is arrived at), or an item's (a kiosk's
// own node; any other, the zone or space it stands in). ErrNoNavigation for a
// package without a network.
func (p *Package) Route(from, to string, opts RouteOptions) (*Route, error) {
	if p.navigation == nil {
		return nil, ErrNoNavigation
	}
	items := map[string][2]*string{}
	for _, it := range p.Items {
		if _, seen := items[it.ID]; !seen {
			items[it.ID] = [2]*string{it.Zone, it.Space}
		}
	}
	return p.navigation.route(from, to, opts.Accessible, items)
}

// Route is the way from one place to another (a node's, space's or zone's ID; a
// kiosk's item ID), on lifts and ramps alone with Accessible: the fewest whole
// tenths of a second of cost, nodes taken in order of their distance and then of
// their IDs, as every reader takes them. Errors wrap ErrNoRoute when there is no
// way, ErrNotInNetwork for an ID the network does not have. It may be called from
// several goroutines at once.
func (n *Navigation) Route(from, to string, opts RouteOptions) (*Route, error) {
	return n.route(from, to, opts.Accessible, nil)
}

// Node is a node by its ID, or nil.
func (n *Navigation) Node(id string) *NavNode { return n.prepared().nodes[id] }

// Place is a space or zone of the network by its ID, or nil.
func (n *Navigation) Place(id string) *NavPlace { return n.prepared().places[id] }

// ---- the network, ready to route on -----------------------------------------------------

type navArc struct {
	to   string
	edge *NavEdge
}

type navGraph struct {
	nodes    map[string]*NavNode
	places   map[string]*NavPlace
	floors   map[string]*NavFloor
	order    map[string]int
	adjacent map[string][]navArc
}

func (n *Navigation) prepared() *navGraph {
	n.once.Do(func() {
		g := &navGraph{nodes: map[string]*NavNode{}, places: map[string]*NavPlace{}, floors: map[string]*NavFloor{},
			order: map[string]int{}, adjacent: map[string][]navArc{}}
		for i := range n.Nodes {
			g.nodes[n.Nodes[i].ID] = &n.Nodes[i]
		}
		for i := range n.Places {
			g.places[n.Places[i].ID] = &n.Places[i]
		}
		for i := range n.Floors {
			g.floors[n.Floors[i].ID] = &n.Floors[i]
			g.order[n.Floors[i].ID] = i
		}
		for i := range n.Edges {
			e := &n.Edges[i]
			if g.nodes[e.From] != nil && g.nodes[e.To] != nil {
				g.adjacent[e.From] = append(g.adjacent[e.From], navArc{e.To, e})
				g.adjacent[e.To] = append(g.adjacent[e.To], navArc{e.From, e})
			}
		}
		n.graph = g
	})
	return n.graph
}

var arrivals = map[string]bool{"room": true, "lift": true, "stairs": true, "escalator": true, "ramp": true}
var verticals = map[string]bool{"lift": true, "stairs": true, "escalator": true, "ramp": true}
var doorKinds = map[string]bool{"door": true, "entrance": true}

func is(s *string, v string) bool { return s != nil && *s == v }
func set(s *string) bool          { return s != nil && *s != "" }

// ends: the nodes a place is.
func (g *navGraph) ends(ref string, items map[string][2]*string) ([]string, error) {
	if g.nodes[ref] != nil {
		return []string{ref}, nil
	}
	var found []string
	for id, n := range g.nodes {
		if arrivals[n.Kind] && (is(n.Space, ref) || is(n.Zone, ref)) {
			found = append(found, id)
		}
	}
	if len(found) > 0 {
		sort.Strings(found)
		return found, nil
	}
	if g.nodes["kiosk:"+ref] != nil {
		return []string{"kiosk:" + ref}, nil
	}
	if where, ok := items[ref]; ok {
		for _, place := range where { // its zone, else its space
			if set(place) {
				return g.ends(*place, nil)
			}
		}
	}
	return nil, fmt.Errorf("%w: %s", ErrNotInNetwork, clip(ref))
}

func (g *navGraph) placeOf(id string) *string {
	n := g.nodes[id]
	if doorKinds[n.Kind] {
		if len(n.Spaces) > 0 {
			return &n.Spaces[0]
		}
		return nil
	}
	if set(n.Zone) {
		return n.Zone
	}
	return n.Space
}

func (g *navGraph) label(place *string) string {
	if set(place) {
		if p := g.places[*place]; p != nil {
			return p.Label
		}
	}
	return "the room"
}

// the queue of nodes to take: by distance, then ID
type navEntry struct {
	d  int64
	id string
}
type navQueue []navEntry

func (q navQueue) Len() int { return len(q) }
func (q navQueue) Less(i, j int) bool {
	return q[i].d < q[j].d || (q[i].d == q[j].d && q[i].id < q[j].id)
}
func (q navQueue) Swap(i, j int) { q[i], q[j] = q[j], q[i] }
func (q *navQueue) Push(x any)   { *q = append(*q, x.(navEntry)) }
func (q *navQueue) Pop() any {
	old := *q
	x := old[len(old)-1]
	*q = old[:len(old)-1]
	return x
}

// shortest is the cheapest way from any source to any target (in whole tenths of a
// second), nil when there is none.
func (g *navGraph) shortest(sources, targets []string, accessible bool) []string {
	want := map[string]bool{}
	for _, t := range targets {
		want[t] = true
	}
	dist, prev := map[string]int64{}, map[string]string{}
	q := &navQueue{}
	seen := map[string]bool{}
	for _, s := range sources {
		dist[s] = 0
		if !seen[s] {
			seen[s] = true
			heap.Push(q, navEntry{0, s})
		}
	}
	done := map[string]bool{}
	for q.Len() > 0 {
		e := heap.Pop(q).(navEntry)
		u := e.id
		if done[u] {
			continue
		}
		done[u] = true
		if want[u] {
			path := []string{u}
			for {
				p, ok := prev[path[len(path)-1]]
				if !ok {
					break
				}
				path = append(path, p)
			}
			for i, j := 0, len(path)-1; i < j; i, j = i+1, j-1 {
				path[i], path[j] = path[j], path[i]
			}
			return path
		}
		for _, arc := range g.adjacent[u] {
			if accessible && !arc.edge.Accessible {
				continue
			}
			nd := e.d + int64(math.Round(arc.edge.Cost*10))
			if old, ok := dist[arc.to]; !ok || nd < old {
				dist[arc.to], prev[arc.to] = nd, u
				heap.Push(q, navEntry{nd, arc.to})
			}
		}
	}
	return nil
}

func (g *navGraph) edge(a, b string) *NavEdge {
	for _, arc := range g.adjacent[a] {
		if arc.to == b {
			return arc.edge
		}
	}
	return nil
}

func forward(e *NavEdge, a string) [][2]float64 {
	if e.From == a {
		return e.Path
	}
	out := make([][2]float64, len(e.Path))
	for i, p := range e.Path {
		out[len(e.Path)-1-i] = p
	}
	return out
}

func round2(v float64) float64  { return math.Floor(float64(v*100)+0.5) / 100 }
func wholeMetres(v float64) int { return int(math.Floor(v + 0.5)) }

func capitalised(s string) string {
	if s != "" && s[0] >= 'a' && s[0] <= 'z' {
		return string(s[0]-'a'+'A') + s[1:]
	}
	return s
}

func (n *Navigation) route(from, to string, accessible bool, items map[string][2]*string) (*Route, error) {
	g := n.prepared()
	sources, err := g.ends(from, items)
	if err != nil {
		return nil, err
	}
	targets, err := g.ends(to, items)
	if err != nil {
		return nil, err
	}
	path := g.shortest(sources, targets, accessible)
	if path == nil {
		without := ""
		if accessible {
			without = " without stairs"
		}
		return nil, fmt.Errorf("%w from %s to %s%s", ErrNoRoute, clip(from), clip(to), without)
	}
	edges := make([]*NavEdge, len(path)-1)
	for i := range edges {
		edges[i] = g.edge(path[i], path[i+1])
	}
	// runs on one floor, between the rides from floor to floor
	runs, rides := [][]int{{}}, [][]int{}
	for i, e := range edges {
		switch {
		case e.Kind == "walk" || e.Kind == "door":
			runs[len(runs)-1] = append(runs[len(runs)-1], i)
		case len(rides) > 0 && rides[len(rides)-1][len(rides[len(rides)-1])-1] == i-1 &&
			edges[rides[len(rides)-1][0]].Kind == e.Kind && len(runs[len(runs)-1]) == 0:
			rides[len(rides)-1] = append(rides[len(rides)-1], i)
		default:
			rides = append(rides, []int{i})
			runs = append(runs, []int{})
		}
	}
	starts := []int{0}
	for _, r := range rides {
		starts = append(starts, r[len(r)-1]+1)
	}
	r := &Route{From: from, To: to, Accessible: accessible, Nodes: path, Legs: []Leg{}, Changes: []Change{}}
	for k, run := range runs {
		at := g.nodes[path[starts[k]]]
		points := [][2]float64{{at.Local.X, at.Local.Y}}
		metres := 0.0
		for _, i := range run {
			for _, p := range forward(edges[i], path[i])[1:] {
				if p != points[len(points)-1] {
					points = append(points, p)
				}
			}
			metres += edges[i].Length
		}
		r.Legs = append(r.Legs, Leg{Floor: at.Floor, Points: points, Metres: round2(metres)})
	}
	for _, ride := range rides {
		a, b := path[ride[0]], path[ride[len(ride)-1]+1]
		fa, fb := g.nodes[a].Floor, g.nodes[b].Floor
		apart := g.order[fb] - g.order[fa]
		c := Change{By: edges[ride[0]].Kind, FromFloor: fa, ToFloor: fb, FromNode: a, ToNode: b, Floors: apart,
			Direction: "up"}
		if apart <= 0 {
			c.Direction = "down"
		}
		if apart < 0 {
			c.Floors = -apart
		}
		r.Changes = append(r.Changes, c)
	}
	metres, seconds := 0.0, 0.0
	for _, e := range edges {
		metres += e.Length
		seconds += e.Seconds
	}
	r.Metres, r.Seconds = round2(metres), round2(seconds)
	r.Steps = g.steps(path, edges, runs, starts, r.Changes)
	return r, nil
}

var throughAlong = map[string]bool{"corridor": true, "ramp": true}

func (g *navGraph) steps(path []string, edges []*NavEdge, runs [][]int, starts []int, changes []Change) []Step {
	first := g.nodes[path[0]]
	here := g.placeOf(path[0])
	var text string
	switch first.Kind {
	case "lift", "stairs", "escalator", "ramp":
		text = "Start at the " + first.Kind
	case "kiosk":
		text = "Start at the kiosk in " + g.label(here)
	case "entrance":
		text = "Start at the entrance into " + g.label(here)
	case "door":
		text = "Start at the door of " + g.label(here)
	default:
		text = "Start in " + g.label(here)
	}
	steps := []Step{{Kind: "start", Node: path[0], NodeKind: first.Kind, Place: here, Floor: first.Floor, Text: text}}
	goal := g.placeOf(path[len(path)-1])
	for k, run := range runs {
		metres, by := 0.0, map[string]float64{}
		for _, i := range run {
			e := edges[i]
			metres += e.Length
			var place *string
			if set(e.Zone) {
				place = e.Zone
			} else {
				place = e.Space
			}
			if set(place) {
				by[*place] += e.Length
			}
		}
		last := k == len(runs)-1
		m := wholeMetres(metres)
		if len(run) > 0 && m > 0 {
			var along *string
			for place, length := range by {
				if along == nil || length > by[*along] || (length == by[*along] && place < *along) {
					p := place
					along = &p
				}
			}
			to, toText := "destination", g.label(goal)
			if !last {
				to = changes[k].By
				toText = "the " + to
			}
			kind := ""
			if along != nil {
				if p := g.places[*along]; p != nil {
					kind = p.Type
				}
			}
			var text string
			if along == nil || (last && goal != nil && *along == *goal) {
				text = fmt.Sprintf("Walk %d m to %s", m, toText)
			} else {
				how := "through"
				if throughAlong[kind] {
					how = "along"
				}
				text = fmt.Sprintf("Walk %d m %s %s to %s", m, how, g.label(along), toText)
			}
			var place *string
			if last {
				place = goal
			}
			steps = append(steps, Step{Kind: "walk", Floor: g.nodes[path[starts[k]]].Floor, Metres: m, Along: along,
				To: to, Place: place, Text: text})
		}
		if !last {
			c := changes[k]
			name := c.ToFloor
			if f := g.floors[c.ToFloor]; f != nil && f.Name != "" {
				name = f.Name
			}
			steps = append(steps, Step{Kind: "take", By: c.By, FromFloor: c.FromFloor, ToFloor: c.ToFloor, Floors: c.Floors,
				Direction: c.Direction, Text: fmt.Sprintf("Take the %s %s to %s", c.By, c.Direction, name)})
		}
	}
	side := "here"
	if len(path) > 1 {
		side = g.side(path, edges, runs[len(runs)-1], starts[len(starts)-1], goal)
	}
	label := g.label(goal)
	switch side {
	case "here":
		text = "You are at " + label
	case "ahead":
		text = capitalised(label) + " is ahead"
	default:
		text = capitalised(label) + " is on your " + side
	}
	return append(steps, Step{Kind: "arrive", Place: goal, Floor: g.nodes[path[len(path)-1]].Floor, Side: side, Text: text})
}

func samePlace(a, b *string) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}

// walkBackM: the way a person walks before a door, over this much of the way.
const walkBackM = 2.0

// side: which side the destination's door is on, as a person walks to it. Products
// are rounded on their own (float64(…)), never fused with what they are added to,
// so that it is the same on every machine as in every reader.
func (g *navGraph) side(path []string, edges []*NavEdge, run []int, at int, goal *string) string {
	end := at + len(run)
	for j := end - 1; j > at; j-- {
		if !doorKinds[g.nodes[path[j]].Kind] {
			continue
		}
		if !samePlace(g.placeOf(path[j+1]), goal) || samePlace(g.placeOf(path[j-1]), goal) {
			return "ahead"
		}
		start := g.nodes[path[at]].Local
		points := [][2]float64{{start.X, start.Y}}
		for i := at; i < j-1; i++ {
			for _, p := range forward(edges[i], path[i])[1:] {
				if p != points[len(points)-1] {
					points = append(points, p)
				}
			}
		}
		back, q := walkBackM, points[0]
		for i := len(points) - 1; i > 0; i-- {
			x1, y1, x0, y0 := points[i][0], points[i][1], points[i-1][0], points[i-1][1]
			dx, dy := x0-x1, y0-y1
			seg := math.Sqrt(float64(dx*dx) + float64(dy*dy))
			if seg >= back {
				q = [2]float64{x1 + float64(dx*(back/seg)), y1 + float64(dy*(back/seg))}
				break
			}
			back -= seg
			q = points[i-1]
		}
		last := points[len(points)-1]
		wx, wy := last[0]-q[0], last[1]-q[1]
		door, inside := g.nodes[path[j]].Local, g.nodes[path[j+1]].Local
		rx, ry := inside.X-door.X, inside.Y-door.Y
		if wx == 0 && wy == 0 {
			return "ahead"
		}
		dot := float64(wx*rx) + float64(wy*ry)
		cross := float64(wx*ry) - float64(wy*rx)
		if dot > 0 && math.Abs(cross) <= float64(0.5*dot) {
			return "ahead"
		}
		if cross > 0 {
			return "left"
		}
		if cross < 0 {
			return "right"
		}
		return "ahead"
	}
	return "ahead"
}
