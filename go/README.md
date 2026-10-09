# storeypath (Go)

Reads StoreyPath packages (`*.storeypath`) for systems written in Go: the
buildings, floors, spaces, zones and openings of a project with their stable IDs,
the furniture and equipment on its floors, and (format 0.8) the way from one place
in a building to another. Standard library only; Go 1.25.

```go
import storeypath "github.com/storeypath/storeypath/go"

pkg, err := storeypath.Open("campus.storeypath")
if err != nil { … }                       // not a package at all
for _, p := range pkg.Validate() { … }    // p.Code (stable), p.ID, p.Message

for _, b := range pkg.Buildings {
	frame, _ := pkg.Frame(b.ID)           // lon/lat ↔ Studio's drawing metres
	for _, f := range pkg.FloorsOf(b.ID) { // lowest first
		for _, u := range pkg.UnitsOn(f.ID) { // what people are placed in
			// u.ID: a zone, or a space with no zones; u.Kind says which
			rings, _ := frame.PolygonsToLocal(u.Geometry)
			_ = rings
		}
		for _, it := range pkg.ItemsOn(f.ID) { // desks, photocopiers, access points, …
			t := pkg.ItemType(it.Type)     // its type in the catalogue (nil if none)
			_, _ = it.Space, t             // where it stands; its ID is not its place
		}
	}
}
```

## What it gives

- `Open` / `Read`: a package from a file or a reader, within `Limits` against
  broken or hostile ZIPs: files in the ZIP, bytes per file and in all, how many
  times its size in the ZIP a file may be (a package's files are 10 to 20), and
  entries in any one list (features, a ring's points, IDs). Fails only when a
  package cannot be read at all (`ErrTooLarge` when over its limits); everything
  else is reported by `Validate`. `DefaultLimits` (64 MiB a file, 256 MiB in all,
  100 times, 200,000 entries) are generous for the largest building, whose
  package is a few MB. **A server reading uploaded packages should pass its own
  limits, tied to the size of upload it accepts** (`OpenWithLimits`, `Read`): what
  reading costs is in proportion to them, not to the upload.
- `Validate`: the checks Studio's validator makes, each `Problem` with a stable
  `Code` (`MISSING_FILE`, `BAD_FILE`, `DUPLICATE_ID`, `PARENT`, `ZONE`, `CHANGES`,
  `PLACEMENT`, `ITEM`, `ITEM_TYPE`, `VALUE`, …), the file and the ID it is about.
  A file is read by its keys as written: one that differs from the format's only
  in case (`"Hidden"`) makes it a `BAD_FILE`, as Go's JSON decoder would
  otherwise read it where other readers do not. Types of spaces, zones and
  openings are those the manifest's `types` list.
- Lookups: `Get`, `Building`, `Floor`, `Space`, `Zone`, `Opening`, `FloorsOf`,
  `SpacesOn`, `ZonesOf`, `OpeningsOn`, and `UnitsOn`: the zones of a divided space
  and every space with none, which is what a system placing people should use.
- Items (format 0.6): `Items`, `Item`, `ItemsOn`, the `Catalogue` of their types
  and `ItemType`. An item's ID is an asset's tag (format 0.8: `7K2Q-XM9F-4DP`,
  `IsItemID`; the project's code and its number, `K7Q2XM-I000142`, before), not its
  place nor its project's: where it stands is its `Floor`,
  `Space` and `Zone`, and (0.7) its `Local` position in its building, which moving
  the building on the map never changes: keep that, not the map position, for the
  history of where an item has been (`ItemLocal` gives it for older packages too,
  worked out from the map). An item carried to another building is in the
  package's `Changes.MovedAway`, not retired. Its `Values` are the details entered
  in StoreyPath; the fields the managing system owns (`OwnerSystem`) are never in a
  package. Older packages have no items and no catalogue.
- Seating (format 0.7): each space's and zone's (and `Unit`'s) `Capacity` (set in
  StoreyPath's review, `CapacityFrom` "review", or its desks' workplaces, "items")
  and `Grade` (who it is laid out for: its highest desk), defaults a system placing
  people may keep its own instead of; the catalogue's `Workplaces` and `Grade`.
- Stacks (format 0.8): each lift, stairs and escalator space's `Stack`, the key
  its spaces share on every floor it serves (nil on other spaces): which of them
  are one.
- Navigation (format 0.8): the building's walking network (`Navigation()`, nil for
  a package without one) and the way on it (`Route`, below).
- One building per package (format 0.7): `Manifest.Scope` names it; `Holds` says
  whether a package holds a building. Older packages may hold several, or a whole
  project; a StoreyPath project file (`.storeypath-project`) is refused by `Read`.
- `LocalFrame`: a building's longitude and latitude back to the local metres
  Studio drew it in, and back (azimuthal equidistant on WGS84, Vincenty), within a
  millimetre of Studio; positions read from a package are within about a
  centimetre (it keeps 7 decimals of a degree).
- `ParseID`: a place's ID's project, parent, code and the prefix at any level (an
  item's ID is refused: it is not a place's).
- Items' IDs (format 0.8): `IsItemID` (as a package writes it, its check symbol
  right), `NormalizeItemID` (as a person typed it: either case, O for 0, I and L for 1,
  hyphens and spaces left out; for a kiosk's search box, never for an ID read from a
  package) and `ItemCheckSymbol`. `ValidateAcross` checks packages held together: the
  same item ID in packages of two projects is a clash (`ITEM_ELSEWHERE`), reported
  against the later one, which a system refuses.

Each file is found through the manifest's `files`, as the format asks (at its usual
name when a manifest does not list a file every package has; items and their
catalogue only when it lists them).

Properties a later format version adds are ignored, as the format asks, and so are
`objects.csv` rows of kinds this module does not know (with their IDs in
`changes.json`). So are files it does not read: a package's floors pre-built in
3D (format 0.5, `world/`) are for viewers. A package this module cannot process
is reported by `Validate` (`VERSION`), and `CheckVersion` says so from the
manifest's `format_version` alone: what is not a format version (`major.minor`,
a `.patch` if any, then a `-` or `+` suffix if any: ASCII digits and letters),
another major version, or before 1.0 a newer minor one (0.9 for this 0.8
reader), with a message to update the reader. `ReadManifest` reads the manifest
alone, so a server can refuse a package before reading the rest of it:

```go
m, err := storeypath.ReadManifest(r, size, limits)
if err == nil {
	err = storeypath.CheckVersion(m.FormatVersion)
}
```

## Navigation

A package of format 0.8 carries its building's walking network (`navigation.json`):
doors and the points in front of them, a point in each space and zone, lifts and
stairs on each floor, entrances and wayfinding kiosks, and the walks and rides
between them. `Route` finds the way on it, the same way Studio and the viewers do
(spec/FORMAT.md, "Navigation"; `../spec/conformance/routes.json` holds ways all of
them must agree on): from a kiosk to an office, say, as a kiosk in a lobby does when
someone types their employee number.

```go
// the kiosk is an item of the package (IsKiosk, KiosksOn); the office a space
// (or a zone) the system keeps people in, by its ID
way, err := pkg.Route(kioskItemID, officeSpaceID, storeypath.RouteOptions{})
switch {
case errors.Is(err, storeypath.ErrNoRoute): // no way (or none without stairs)
case err != nil:                            // an ID the network does not know
}
for _, s := range way.Steps {
	fmt.Println(s.Text) // "Walk 24 m along CORRIDOR to the lift", "Take the lift up to Floor 1", …
}
for _, leg := range way.Legs {
	_ = leg.Points // the line to draw on leg.Floor, in the building's own metres
}
```

- From and to: a node's ID (`kiosk:<item>`, `door:<opening>`, `room:<space>`, …),
  a space's or zone's ID (where it is arrived at: a divided space's nearest zone),
  or an item's (a kiosk's node; any other, the zone or space it stands in).
- `RouteOptions{Accessible: true}`: lifts and ramps alone, no stairs or escalators.
- A `Route` has its `Nodes`, `Legs` (the walking on each floor between rides: the
  line to draw, as `LocalFrame` turns it onto the map), `Changes` (each ride: by
  lift or stairs, from which floor to which, up or down), `Metres` and `Seconds`,
  and `Steps`: each a `Kind` (start, walk, take, arrive), its values (places and
  floors by ID, whole metres, the side the destination's door is on) and `Text` in
  English. A system words steps in its own language from the kind and values.
- `Navigation()` gives the network itself (`Nodes`, `Edges`, `Places` with what a
  step calls them, `Floors`; `Node`, `Place`); `Navigation().Route` routes on it by
  node, space and zone IDs alone. Both may be used from several goroutines.

`ErrNoNavigation` is returned for a package without a network (older than 0.8).

## Tests

`go test ./...` reads the packages in `../spec/conformance/` (made by Studio, see
`make.py` there) and checks the same things Studio's own tests check.
