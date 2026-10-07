# storeypath (Go)

Reads StoreyPath packages (`*.storeypath`) for systems written in Go: the
buildings, floors, spaces, zones and openings of a project with their stable IDs,
and the furniture and equipment on its floors. Standard library only; Go 1.25.

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

- `Open` / `Read`: a package from a file or a reader, within `Limits` (files,
  bytes) against broken or hostile ZIPs. Fails only when a package cannot be read
  at all; everything else is reported by `Validate`.
- `Validate`: the checks Studio's validator makes, each `Problem` with a stable
  `Code` (`MISSING_FILE`, `DUPLICATE_ID`, `PARENT`, `ZONE`, `CHANGES`, `ITEM`,
  `ITEM_TYPE`, …), the file and the ID it is about.
- Lookups: `Get`, `Building`, `Floor`, `Space`, `Zone`, `Opening`, `FloorsOf`,
  `SpacesOn`, `ZonesOf`, `OpeningsOn`, and `UnitsOn`: the zones of a divided space
  and every space with none, which is what a system placing people should use.
- Items (format 0.6): `Items`, `Item`, `ItemsOn`, the `Catalogue` of their types
  and `ItemType`. An item's ID is the project's and its own number
  (`K7Q2XM-I000142`, `IsItemID`), not its place: where it stands is its `Floor`,
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
- One building per package (format 0.7): `Manifest.Scope` names it; `Holds` says
  whether a package holds a building. Older packages may hold several, or a whole
  project; a StoreyPath project file (`.storeypath-project`) is refused by `Read`.
- `LocalFrame`: a building's longitude and latitude back to the local metres
  Studio drew it in, and back (azimuthal equidistant on WGS84, Vincenty), within a
  millimetre of Studio; positions read from a package are within about a
  centimetre (it keeps 7 decimals of a degree).
- `ParseID`: an ID's project, parent, code and the prefix at any level.

Properties a later format version adds are ignored, as the format asks, and so are
`objects.csv` rows of kinds this module does not know (with their IDs in
`changes.json`). A package this module cannot process is reported by `Validate`
(`VERSION`), and `CheckVersion` says so from the manifest's `format_version` alone:
another major version, or before 1.0 a newer minor one (0.8 for this 0.7 reader),
with a message to update the reader. So are files it
does not read: a package's floors pre-built in 3D (format 0.5, `world/`) are for
viewers.

## Tests

`go test ./...` reads the packages in `../spec/conformance/` (made by Studio, see
`make.py` there) and checks the same things Studio's own tests check.
