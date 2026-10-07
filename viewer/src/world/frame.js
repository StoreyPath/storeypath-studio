// A building's own frame on the map: a point of its drawings (metres) at its
// longitude and latitude. Studio places a building by turning the drawing by the
// placement's bearing around its anchor, then projecting from an
// azimuthal-equidistant plane centred on the anchor (WGS84). This is that
// projection with Vincenty's direct formula on the ellipsoid, as
// viewer/svg/src/frame.ts does: within a millimetre of Studio's
// (spec/conformance/localframe.json).

const A = 6378137.0;
const F = 1 / 298.257223563;
const B = A * (1 - F);
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;

/** A point [x, y] of a building's drawings on earth, [lon, lat], by its placement
 * (manifest.json → placements: { lon, lat, x, y, bearing }). */
export function toLonLat(placement, [x, y]) {
  const p = placement;
  const b = rad(p.bearing || 0);
  const dx = x - p.x, dy = y - p.y;
  const east = dx * Math.cos(b) + dy * Math.sin(b);
  const north = -dx * Math.sin(b) + dy * Math.cos(b);
  const dist = Math.hypot(east, north);
  if (dist === 0) return [p.lon, p.lat];
  const [lat, lon] = direct(p.lat, p.lon, Math.atan2(east, north), dist);
  return [lon, lat];
}

function direct(lat1, lon1, azimuth, dist) {
  const sinA1 = Math.sin(azimuth), cosA1 = Math.cos(azimuth);
  const tanU1 = (1 - F) * Math.tan(rad(lat1));
  const cosU1 = 1 / Math.sqrt(1 + tanU1 * tanU1);
  const sinU1 = tanU1 * cosU1;
  const sigma1 = Math.atan2(tanU1, cosA1);
  const sinAlpha = cosU1 * sinA1;
  const cos2Alpha = 1 - sinAlpha * sinAlpha;
  const u2 = (cos2Alpha * (A * A - B * B)) / (B * B);
  const Ac = 1 + (u2 / 16384) * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)));
  const Bc = (u2 / 1024) * (256 + u2 * (-128 + u2 * (74 - 47 * u2)));
  let sigma = dist / (B * Ac), sinSigma = 0, cosSigma = 0, cos2SigmaM = 0;
  for (let i = 0; i < 200; i++) {
    cos2SigmaM = Math.cos(2 * sigma1 + sigma);
    sinSigma = Math.sin(sigma);
    cosSigma = Math.cos(sigma);
    const deltaSigma = Bc * sinSigma * (cos2SigmaM + (Bc / 4) * (cosSigma * (-1 + 2 * cos2SigmaM ** 2) -
      (Bc / 6) * cos2SigmaM * (-3 + 4 * sinSigma ** 2) * (-3 + 4 * cos2SigmaM ** 2)));
    const prev = sigma;
    sigma = dist / (B * Ac) + deltaSigma;
    if (Math.abs(sigma - prev) < 1e-13) break;
  }
  sinSigma = Math.sin(sigma);
  cosSigma = Math.cos(sigma);
  cos2SigmaM = Math.cos(2 * sigma1 + sigma);
  const tmp = sinU1 * sinSigma - cosU1 * cosSigma * cosA1;
  const lat2 = Math.atan2(sinU1 * cosSigma + cosU1 * sinSigma * cosA1, (1 - F) * Math.sqrt(sinAlpha * sinAlpha + tmp * tmp));
  const lambda = Math.atan2(sinSigma * sinA1, cosU1 * cosSigma - sinU1 * sinSigma * cosA1);
  const C = (F / 16) * cos2Alpha * (4 + F * (4 - 3 * cos2Alpha));
  const L = lambda - (1 - C) * F * sinAlpha * (sigma + C * sinSigma * (cos2SigmaM + C * cosSigma * (-1 + 2 * cos2SigmaM ** 2)));
  return [deg(lat2), lon1 + deg(L)];
}
