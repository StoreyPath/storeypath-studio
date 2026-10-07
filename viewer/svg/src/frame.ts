// A building's local frame: its longitude and latitude back into the drawing
// metres Studio works in, and the other way. Studio places a building by turning
// the drawing by the placement's bearing around its anchor, then projecting from
// an azimuthal-equidistant plane centred on the anchor (WGS84). This is that
// projection with Vincenty's formulas on the ellipsoid, as the Go reader does:
// within a millimetre of Studio's (spec/conformance/localframe.json).

import type { XY } from "./types.js";

export interface Placement {
  lon: number;
  lat: number;
  x: number;
  y: number;
  bearing: number;
}

const A = 6378137.0;
const F = 1 / 298.257223563;
const B = A * (1 - F);
const rad = (d: number): number => (d * Math.PI) / 180;
const deg = (r: number): number => (r * 180) / Math.PI;

export class LocalFrame {
  readonly placement: Placement;
  private readonly cos: number;
  private readonly sin: number;

  constructor(placement: Placement) {
    this.placement = placement;
    const b = rad(placement.bearing || 0);
    this.cos = Math.cos(b);
    this.sin = Math.sin(b);
  }

  /** A position [lon, lat] in drawing metres [x, y]. */
  toLocal(lonlat: readonly [number, number]): XY {
    const p = this.placement;
    const [dist, azimuth] = inverse(p.lat, p.lon, lonlat[1], lonlat[0]);
    const east = dist * Math.sin(azimuth);
    const north = dist * Math.cos(azimuth);
    return [p.x + east * this.cos - north * this.sin, p.y + east * this.sin + north * this.cos];
  }

  /** A point in drawing metres [x, y] on earth [lon, lat]. */
  toLonLat(xy: readonly [number, number]): [number, number] {
    const p = this.placement;
    const dx = xy[0] - p.x;
    const dy = xy[1] - p.y;
    const east = dx * this.cos + dy * this.sin;
    const north = -dx * this.sin + dy * this.cos;
    const dist = Math.hypot(east, north);
    if (dist === 0) return [p.lon, p.lat];
    const [lat, lon] = direct(p.lat, p.lon, Math.atan2(east, north), dist);
    return [lon, lat];
  }
}

function inverse(lat1: number, lon1: number, lat2: number, lon2: number): [number, number] {
  const L = rad(lon2 - lon1);
  const U1 = Math.atan((1 - F) * Math.tan(rad(lat1)));
  const U2 = Math.atan((1 - F) * Math.tan(rad(lat2)));
  const sinU1 = Math.sin(U1), cosU1 = Math.cos(U1), sinU2 = Math.sin(U2), cosU2 = Math.cos(U2);
  let lambda = L, sinSigma = 0, cosSigma = 0, sigma = 0, cos2Alpha = 0, cos2SigmaM = 0, sinLambda = 0, cosLambda = 0;
  for (let i = 0; i < 200; i++) {
    sinLambda = Math.sin(lambda);
    cosLambda = Math.cos(lambda);
    const a = cosU2 * sinLambda;
    const b = cosU1 * sinU2 - sinU1 * cosU2 * cosLambda;
    sinSigma = Math.sqrt(a * a + b * b);
    if (sinSigma === 0) return [0, 0];
    cosSigma = sinU1 * sinU2 + cosU1 * cosU2 * cosLambda;
    sigma = Math.atan2(sinSigma, cosSigma);
    const sinAlpha = (cosU1 * cosU2 * sinLambda) / sinSigma;
    cos2Alpha = 1 - sinAlpha * sinAlpha;
    cos2SigmaM = cos2Alpha !== 0 ? cosSigma - (2 * sinU1 * sinU2) / cos2Alpha : 0;
    const C = (F / 16) * cos2Alpha * (4 + F * (4 - 3 * cos2Alpha));
    const prev = lambda;
    lambda = L + (1 - C) * F * sinAlpha * (sigma + C * sinSigma * (cos2SigmaM + C * cosSigma * (-1 + 2 * cos2SigmaM ** 2)));
    if (Math.abs(lambda - prev) < 1e-13) break;
  }
  const u2 = (cos2Alpha * (A * A - B * B)) / (B * B);
  const Ac = 1 + (u2 / 16384) * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)));
  const Bc = (u2 / 1024) * (256 + u2 * (-128 + u2 * (74 - 47 * u2)));
  const deltaSigma = Bc * sinSigma * (cos2SigmaM + (Bc / 4) * (cosSigma * (-1 + 2 * cos2SigmaM ** 2) -
    (Bc / 6) * cos2SigmaM * (-3 + 4 * sinSigma ** 2) * (-3 + 4 * cos2SigmaM ** 2)));
  return [B * Ac * (sigma - deltaSigma), Math.atan2(cosU2 * sinLambda, cosU1 * sinU2 - sinU1 * cosU2 * cosLambda)];
}

function direct(lat1: number, lon1: number, azimuth: number, dist: number): [number, number] {
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
  const Lon = lambda - (1 - C) * F * sinAlpha * (sigma + C * sinSigma * (cos2SigmaM + C * cosSigma * (-1 + 2 * cos2SigmaM ** 2)));
  return [deg(lat2), lon1 + deg(Lon)];
}
