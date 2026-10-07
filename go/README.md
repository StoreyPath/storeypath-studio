# storeypath (Go)

Reads StoreyPath packages (`*.storeypath`) for systems written in Go: the
buildings, floors, spaces, zones and openings of a project with their stable IDs.
Standard library only; Go 1.25.

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
	}
}
```

## What it gives

- `Open` / `Read`: a package from a file or a reader, within `Limits` (files,
  bytes) against broken or hostile ZIPs. Fails only when a package cannot be read
  at all; everything else is reported by `Validate`.
- `Validate`: the checks Studio's validator makes, each `Problem` with a stable
  `Code` (`MISSING_FILE`, `DUPLICATE_ID`, `PARENT`, `ZONE`, `CHANGES`, …), the
  file and the ID it is about.
- Lookups: `Get`, `Building`, `Floor`, `Space`, `Zone`, `Opening`, `FloorsOf`,
  `SpacesOn`, `ZonesOf`, `OpeningsOn`, and `UnitsOn`: the zones of a divided space
  and every space with none, which is what a system placing people should use.
- `LocalFrame`: a building's longitude and latitude back to the local metres
  Studio drew it in, and back (azimuthal equidistant on WGS84, Vincenty), within a
  millimetre of Studio; positions read from a package are within about a
  centimetre (it keeps 7 decimals of a degree).
- `ParseID`: an ID's project, parent, code and the prefix at any level.

Properties a later format version adds are ignored, as the format asks; a package
of another major version is reported by `Validate` (`VERSION`).

## Tests

`go test ./...` reads the packages in `../spec/conformance/` (made by Studio, see
`make.py` there) and checks the same things Studio's own tests check.
