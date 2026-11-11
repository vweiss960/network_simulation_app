// Projection constants — must match backend (app/routes/nodes.py)
const SCALE = 18;
const LNG_OFFSET = 130;
const LAT_OFFSET = 55;

export function latLngToXY(lat: number, lng: number): { x: number; y: number } {
  return {
    x: Math.round(((lng + LNG_OFFSET) * SCALE) * 10) / 10,
    y: Math.round(((LAT_OFFSET - lat) * SCALE) * 10) / 10,
  };
}

export function xyToLatLng(x: number, y: number): { latitude: number; longitude: number } {
  return {
    latitude: Math.round((LAT_OFFSET - (y / SCALE)) * 10000) / 10000,
    longitude: Math.round(((x / SCALE) - LNG_OFFSET) * 10000) / 10000,
  };
}
