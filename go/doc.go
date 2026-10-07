// Package storeypath reads StoreyPath packages (*.storeypath): the buildings,
// floors, spaces, zones and openings of a project, with their stable IDs, and the
// furniture and equipment on its floors (items, and the catalogue of their types),
// as StoreyPath Studio exports them. See spec/FORMAT.md in the StoreyPath repository.
//
// A system that consumes packages keeps its own records and IDs and links each to
// a StoreyPath ID: Open reads a package, Validate checks it, and the lookups give
// the floors of a building, the spaces of a floor, the zones of a space and the
// units people are placed in (a space's zones where it has them, otherwise the
// space). LocalFrame turns the package's longitude and latitude back into the
// local drawing metres Studio works in, and ParseID takes an ID apart.
//
// The module uses only Go's standard library, so it can be pinned and vendored
// into an offline build.
package storeypath
