package storeypath

import "math"

// LocalFrame turns a building's longitude and latitude back into the local
// drawing metres Studio works in, and the other way. Studio places a building by
// rotating the drawing by the placement's bearing around its anchor, then
// projecting from an azimuthal-equidistant plane centred on the anchor (WGS84).
// This is that projection, computed with Vincenty's formulas on the ellipsoid:
// within a millimetre of Studio's for anything the size of a campus. A package
// keeps 7 decimals of a degree, so a position read back from one is within about
// a centimetre of where Studio had it.
type LocalFrame struct {
	Placement Placement
	cos, sin  float64
}

// NewLocalFrame is the frame of a placement.
func NewLocalFrame(p Placement) LocalFrame {
	b := p.Bearing * math.Pi / 180
	return LocalFrame{Placement: p, cos: math.Cos(b), sin: math.Sin(b)}
}

// ToLonLat is where a local point (drawing metres) is on earth, as Studio puts it:
// its longitude in (-180, 180], across the antimeridian too.
func (f LocalFrame) ToLonLat(x, y float64) LonLat {
	dx, dy := x-f.Placement.X, y-f.Placement.Y
	east := dx*f.cos + dy*f.sin
	north := -dx*f.sin + dy*f.cos
	dist := math.Hypot(east, north)
	if dist == 0 {
		return LonLat{wrapLon(f.Placement.Lon), f.Placement.Lat}
	}
	lat, lon := vincentyDirect(f.Placement.Lat, f.Placement.Lon, math.Atan2(east, north), dist)
	return LonLat{wrapLon(lon), lat}
}

// wrapLon is a longitude in (-180, 180].
func wrapLon(lon float64) float64 {
	return lon - 360*math.Ceil((lon-180)/360)
}

// ToLocal is a position on earth in the building's drawing metres.
func (f LocalFrame) ToLocal(p LonLat) (x, y float64) {
	dist, azimuth := vincentyInverse(f.Placement.Lat, f.Placement.Lon, p[1], p[0])
	east, north := dist*math.Sin(azimuth), dist*math.Cos(azimuth)
	dx := east*f.cos - north*f.sin
	dy := east*f.sin + north*f.cos
	return f.Placement.X + dx, f.Placement.Y + dy
}

// PolygonsToLocal is a Polygon or MultiPolygon in drawing metres.
func (f LocalFrame) PolygonsToLocal(g *Geometry) ([][][][2]float64, error) {
	polys, err := g.Polygons()
	if err != nil {
		return nil, err
	}
	out := make([][][][2]float64, len(polys))
	for i, poly := range polys {
		out[i] = make([][][2]float64, len(poly))
		for j, ring := range poly {
			out[i][j] = make([][2]float64, len(ring))
			for k, p := range ring {
				x, y := f.ToLocal(p)
				out[i][j][k] = [2]float64{x, y}
			}
		}
	}
	return out, nil
}

// WGS84
const (
	wgsA = 6378137.0
	wgsF = 1 / 298.257223563
	wgsB = wgsA * (1 - wgsF)
)

func rad(d float64) float64 { return d * math.Pi / 180 }
func deg(r float64) float64 { return r * 180 / math.Pi }

// vincentyInverse is the distance (m) and the azimuth (radians, clockwise from
// north) from point 1 to point 2, on the WGS84 ellipsoid.
func vincentyInverse(lat1, lon1, lat2, lon2 float64) (float64, float64) {
	L := rad(math.Remainder(lon2-lon1, 360)) // the short way, across the antimeridian too
	U1 := math.Atan((1 - wgsF) * math.Tan(rad(lat1)))
	U2 := math.Atan((1 - wgsF) * math.Tan(rad(lat2)))
	sinU1, cosU1 := math.Sincos(U1)
	sinU2, cosU2 := math.Sincos(U2)
	lambda := L
	var sinSigma, cosSigma, sigma, cos2Alpha, cos2SigmaM, sinLambda, cosLambda float64
	for i := 0; i < 200; i++ {
		sinLambda, cosLambda = math.Sincos(lambda)
		a := cosU2 * sinLambda
		b := cosU1*sinU2 - sinU1*cosU2*cosLambda
		sinSigma = math.Sqrt(a*a + b*b)
		if sinSigma == 0 {
			return 0, 0 // the same point
		}
		cosSigma = sinU1*sinU2 + cosU1*cosU2*cosLambda
		sigma = math.Atan2(sinSigma, cosSigma)
		sinAlpha := cosU1 * cosU2 * sinLambda / sinSigma
		cos2Alpha = 1 - sinAlpha*sinAlpha
		cos2SigmaM = 0
		if cos2Alpha != 0 {
			cos2SigmaM = cosSigma - 2*sinU1*sinU2/cos2Alpha
		}
		C := wgsF / 16 * cos2Alpha * (4 + wgsF*(4-3*cos2Alpha))
		prev := lambda
		lambda = L + (1-C)*wgsF*sinAlpha*(sigma+C*sinSigma*(cos2SigmaM+C*cosSigma*(-1+2*cos2SigmaM*cos2SigmaM)))
		if math.Abs(lambda-prev) < 1e-13 {
			break
		}
	}
	u2 := cos2Alpha * (wgsA*wgsA - wgsB*wgsB) / (wgsB * wgsB)
	A := 1 + u2/16384*(4096+u2*(-768+u2*(320-175*u2)))
	B := u2 / 1024 * (256 + u2*(-128+u2*(74-47*u2)))
	deltaSigma := B * sinSigma * (cos2SigmaM + B/4*(cosSigma*(-1+2*cos2SigmaM*cos2SigmaM)-
		B/6*cos2SigmaM*(-3+4*sinSigma*sinSigma)*(-3+4*cos2SigmaM*cos2SigmaM)))
	dist := wgsB * A * (sigma - deltaSigma)
	azimuth := math.Atan2(cosU2*sinLambda, cosU1*sinU2-sinU1*cosU2*cosLambda)
	return dist, azimuth
}

// vincentyDirect is where one arrives (lat, lon in degrees) going dist metres
// from a point at an azimuth (radians, clockwise from north), on WGS84.
func vincentyDirect(lat1, lon1, azimuth, dist float64) (float64, float64) {
	sinA1, cosA1 := math.Sincos(azimuth)
	tanU1 := (1 - wgsF) * math.Tan(rad(lat1))
	cosU1 := 1 / math.Sqrt(1+tanU1*tanU1)
	sinU1 := tanU1 * cosU1
	sigma1 := math.Atan2(tanU1, cosA1)
	sinAlpha := cosU1 * sinA1
	cos2Alpha := 1 - sinAlpha*sinAlpha
	u2 := cos2Alpha * (wgsA*wgsA - wgsB*wgsB) / (wgsB * wgsB)
	A := 1 + u2/16384*(4096+u2*(-768+u2*(320-175*u2)))
	B := u2 / 1024 * (256 + u2*(-128+u2*(74-47*u2)))
	sigma := dist / (wgsB * A)
	var sinSigma, cosSigma, cos2SigmaM float64
	for i := 0; i < 200; i++ {
		cos2SigmaM = math.Cos(2*sigma1 + sigma)
		sinSigma, cosSigma = math.Sincos(sigma)
		deltaSigma := B * sinSigma * (cos2SigmaM + B/4*(cosSigma*(-1+2*cos2SigmaM*cos2SigmaM)-
			B/6*cos2SigmaM*(-3+4*sinSigma*sinSigma)*(-3+4*cos2SigmaM*cos2SigmaM)))
		prev := sigma
		sigma = dist/(wgsB*A) + deltaSigma
		if math.Abs(sigma-prev) < 1e-13 {
			break
		}
	}
	sinSigma, cosSigma = math.Sincos(sigma)
	cos2SigmaM = math.Cos(2*sigma1 + sigma)
	tmp := sinU1*sinSigma - cosU1*cosSigma*cosA1
	lat2 := math.Atan2(sinU1*cosSigma+cosU1*sinSigma*cosA1, (1-wgsF)*math.Sqrt(sinAlpha*sinAlpha+tmp*tmp))
	lambda := math.Atan2(sinSigma*sinA1, cosU1*cosSigma-sinU1*sinSigma*cosA1)
	C := wgsF / 16 * cos2Alpha * (4 + wgsF*(4-3*cos2Alpha))
	L := lambda - (1-C)*wgsF*sinAlpha*(sigma+C*sinSigma*(cos2SigmaM+C*cosSigma*(-1+2*cos2SigmaM*cos2SigmaM)))
	return deg(lat2), lon1 + deg(L)
}
