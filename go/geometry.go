package storeypath

import (
	"encoding/json"
	"fmt"
)

// LonLat is a position as [longitude, latitude] in degrees (WGS84).
type LonLat [2]float64

// Geometry is a GeoJSON geometry: a Point, Polygon or MultiPolygon in a package.
// Its coordinates are kept as read; Point, Polygons and Rings decode them.
type Geometry struct {
	Type        string          `json:"type"`
	Coordinates json.RawMessage `json:"coordinates"`
}

// Point is the position of a Point geometry.
func (g *Geometry) Point() (LonLat, error) {
	var p LonLat
	if g == nil || g.Type != "Point" {
		return p, fmt.Errorf("not a Point")
	}
	return p, json.Unmarshal(g.Coordinates, &p)
}

// Polygons is a Polygon or MultiPolygon as polygons, each its outer ring and then
// its holes, each ring closed (its first position repeated last).
func (g *Geometry) Polygons() ([][][]LonLat, error) {
	if g == nil {
		return nil, nil
	}
	switch g.Type {
	case "Polygon":
		var p [][]LonLat
		if err := json.Unmarshal(g.Coordinates, &p); err != nil {
			return nil, err
		}
		return [][][]LonLat{p}, nil
	case "MultiPolygon":
		var mp [][][]LonLat
		err := json.Unmarshal(g.Coordinates, &mp)
		return mp, err
	default:
		return nil, fmt.Errorf("a %s is not a polygon", g.Type)
	}
}
