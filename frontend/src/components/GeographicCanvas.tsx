/**
 * GeographicCanvas — the geographic (map) view of a topology.
 *
 * A MapLibre GL map with a dark Protomaps basemap rendered on a globe
 * projection (curvature of the earth). Nodes are draggable markers; links
 * are a GeoJSON line layer. This is a placement + visualization surface:
 * dragging a node sets its *geographic* lat/lng (independent of the logical
 * React Flow layout), and clicking a node selects it. Rich editing stays in
 * the logical view.
 *
 * Map source/theme come from the backend /api/features `map` block so the
 * basemap can be switched (free demo ↔ self-hosted PMTiles ↔ keyed cloud)
 * via .env without a rebuild. With no env set we fall back to the free
 * Protomaps demo basemap.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { Protocol } from 'pmtiles';
import { layers, namedFlavor } from '@protomaps/basemaps';
import JSZip from 'jszip';
import { kml as kmlToGeoJSON } from '@tmcw/togeojson';
import { useStore } from '../stores/useStore';
import { api } from '../utils/api';
import { NodeInstance } from '../types';
import { ORBIT_COLORS, SAT_POP, isSatRole, isSatelliteLink, satLinkVisible } from '../utils/satellite';
import CarrierPanel, { CarrierLookup } from './CarrierPanel';
import NodeSearchBox, { matchNodes, moveHighlight } from './NodeSearchBox';
import toast from 'react-hot-toast';
import { isTypingTarget, isActivationTarget, panDirection, makeRightDoubleClickDetector, CLOSE_MENUS_EVENT } from '../utils/hotkeys';
import { editSessionErrorMessage } from '../utils/editSession';

// Protomaps hosted style endpoint. With an API key the basemap is selected
// by theme (light / dark / white / black / grayscale).
const PROTOMAPS_STYLE = (theme: string, key: string) =>
  `https://api.protomaps.com/styles/v5/${theme}/en.json?key=${encodeURIComponent(key)}`;
const GLYPHS = 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf';

type MapCfg = { style_url?: string; tiles_url?: string; theme?: string; api_key?: string };

// pmtiles:// protocol must be registered with MapLibre exactly once per page.
let pmtilesRegistered = false;
function ensurePmtiles() {
  if (pmtilesRegistered) return;
  maplibregl.addProtocol('pmtiles', new Protocol().tile);
  pmtilesRegistered = true;
}

// Returns the MapLibre style, or null if Protomaps isn't configured yet
// (no silent fallback to a different provider — the caller shows a
// "configure Protomaps" message instead).
function buildStyle(cfg: MapCfg): string | maplibregl.StyleSpecification | null {
  const theme = cfg.theme || 'dark';
  // 1. Full style URL wins (self-hosted style JSON, or a pre-built cloud URL).
  if (cfg.style_url) return cfg.style_url;
  // 2. Protomaps cloud API key → hosted style for the chosen theme.
  if (cfg.api_key) return PROTOMAPS_STYLE(theme, cfg.api_key);
  // 3. Self-hosted Protomaps .pmtiles → build the style from @protomaps/basemaps.
  if (cfg.tiles_url) {
    const tiles = cfg.tiles_url;
    const isPmtiles = tiles.endsWith('.pmtiles') || tiles.startsWith('pmtiles://');
    const url = tiles.startsWith('pmtiles://') ? tiles : (isPmtiles ? `pmtiles://${tiles}` : undefined);
    return {
      version: 8,
      glyphs: GLYPHS,
      sources: {
        protomaps: url
          ? { type: 'vector', url, attribution: '<a href="https://protomaps.com">Protomaps</a>' }
          : { type: 'vector', tiles: [tiles], attribution: '<a href="https://protomaps.com">Protomaps</a>' },
      },
      layers: layers('protomaps', namedFlavor(theme as any), { lang: 'en' }) as any,
    };
  }
  // 4. Not configured.
  return null;
}

// English-only basemap labels. Protomaps tiles carry up to three names per
// feature (name / name2 / name3); `script`/`script2`/`script3` is present only
// when that name isn't Latin script. The stock styles show a second line in
// the local script and fall back to it when there's no English name; this
// shows name:en, else the first Latin-script name, else no label.
const ENGLISH_LABEL: any = ['case',
  ['has', 'name:en'], ['get', 'name:en'],
  ['all', ['has', 'name'], ['!', ['has', 'script']]], ['get', 'name'],
  ['all', ['has', 'name2'], ['!', ['has', 'script2']]], ['get', 'name2'],
  ['all', ['has', 'name3'], ['!', ['has', 'script3']]], ['get', 'name3'],
  ''];
function applyEnglishLabels(map: maplibregl.Map) {
  for (const layer of map.getStyle().layers || []) {
    if (layer.type !== 'symbol') continue;
    const field = map.getLayoutProperty(layer.id, 'text-field');
    if (!field || !JSON.stringify(field).includes('"name')) continue;
    map.setLayoutProperty(layer.id, 'text-field', ENGLISH_LABEL);
    // The font is chosen per script (e.g. Devanagari); labels are Latin now,
    // so use the layer's default (last) font from that case expression.
    const font = map.getLayoutProperty(layer.id, 'text-font') as any;
    if (Array.isArray(font) && font[0] === 'case') map.setLayoutProperty(layer.id, 'text-font', font[font.length - 1]);
  }
}

// Last globe camera per topology, restored when the map remounts or the
// topology changes. Session-only.
type GeoCamera = { center: [number, number]; zoom: number; bearing: number; pitch: number };
const savedGeoCamera = new Map<number, GeoCamera>();
function cameraOf(map: maplibregl.Map): GeoCamera {
  const c = map.getCenter();
  return { center: [c.lng, c.lat], zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() };
}

// A node's position on the map: real geo coords if placed, else seeded from
// the logical lat/lng (an equirectangular projection of the canvas layout —
// already plausible US-ish coordinates). null if the node has neither.
function nodePos(n: NodeInstance): [number, number] | null {
  let lat: number | null = null, lng: number | null = null;
  if (n.geo_latitude != null && n.geo_longitude != null) { lat = n.geo_latitude; lng = n.geo_longitude; }
  else if (n.latitude != null && n.longitude != null) { lat = n.latitude; lng = n.longitude; }
  if (lat == null || lng == null || !Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  // Clamp to a valid on-globe coordinate. Logical coords are an equirectangular
  // projection of the canvas layout, so a large/spread topology can seed nodes
  // outside [-90,90]/[-180,180]. MapLibre's LngLat THROWS on out-of-range
  // latitude, which aborts marker/link rendering and blanks the whole globe —
  // hence "works on small topologies, blue screen on large ones". Clamping
  // keeps every node valid (±85 stays clear of the mercator pole limit).
  lat = Math.max(-85, Math.min(85, lat));
  lng = Math.max(-180, Math.min(180, lng));
  return [lng, lat];
}

// `hl` per link feature drives the line paint:
//   0 = neutral (no flow highlighted — normal styling)
//   1 = on the highlighted flow's path (amber, emphasized)
//   2 = off-path while a flow is highlighted (dimmed)
// A node's "kind" for the geographic-view filter. Blank/missing → 'router'.
function roleOf(n: NodeInstance): string { return (n.role || 'router'); }

// Parse a user-supplied KMZ (zipped KML) or KML file into GeoJSON, entirely in
// the browser — proprietary fiber routes never leave the client.
async function parseKmzOrKml(file: File): Promise<{ fc: GeoJSON.FeatureCollection; tree: KmlNode }> {
  let kmlText: string;
  if (file.name.toLowerCase().endsWith('.kmz')) {
    const zip = await JSZip.loadAsync(await file.arrayBuffer());
    const entry = Object.values(zip.files).find(f => !f.dir && f.name.toLowerCase().endsWith('.kml'));
    if (!entry) throw new Error('No .kml found inside the KMZ');
    kmlText = await entry.async('text');
  } else {
    kmlText = await file.text();
  }
  const dom = new DOMParser().parseFromString(kmlText, 'text/xml');
  if (dom.querySelector('parsererror')) throw new Error('Could not parse the KML (invalid XML)');
  // togeojson for rendering; a folder tree (preserving hierarchy) for per-link
  // route assignment + stitching, which togeojson flattens away.
  return { fc: kmlToGeoJSON(dom) as GeoJSON.FeatureCollection, tree: buildKmlTree(dom) };
}

// Bounding box [w,s,e,n] of a GeoJSON FeatureCollection, or null if empty.
function bboxOfGeoJSON(fc: GeoJSON.FeatureCollection): [number, number, number, number] | null {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  const walk = (c: any) => {
    if (typeof c[0] === 'number') {
      w = Math.min(w, c[0]); e = Math.max(e, c[0]); s = Math.min(s, c[1]); n = Math.max(n, c[1]);
    } else if (Array.isArray(c)) { for (const x of c) walk(x); }
  };
  for (const f of fc.features || []) { if (f.geometry && 'coordinates' in f.geometry) walk((f.geometry as any).coordinates); }
  return Number.isFinite(w) ? [w, s, e, n] : null;
}

// Ray-casting point-in-ring (planar lng/lat — fine for local alert areas).
function pointInRing(lng: number, lat: number, ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if (((yi > lat) !== (yj > lat)) && (lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}
// Point inside a GeoJSON Polygon/MultiPolygon (respecting holes).
function pointInGeom(pt: [number, number], geom: any): boolean {
  if (!geom) return false;
  const [lng, lat] = pt;
  const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.type === 'MultiPolygon' ? geom.coordinates : [];
  for (const poly of polys) {
    if (poly.length && pointInRing(lng, lat, poly[0])) {
      let inHole = false;
      for (let h = 1; h < poly.length; h++) { if (pointInRing(lng, lat, poly[h])) { inHole = true; break; } }
      if (!inHole) return true;
    }
  }
  return false;
}

// Great-circle path between two [lng,lat] points, as MultiLineString segment
// coordinates. Following the great circle (the shortest surface path) means a
// link always takes the correct direction around the globe — e.g. California↔
// Guam crosses the Pacific instead of wrapping the long way. Densified for a
// smooth arc and split at the antimeridian so it never streaks across the map.
function greatCircleSegments(a: [number, number], b: [number, number]): number[][][] {
  const R = Math.PI / 180, D = 180 / Math.PI;
  const lng1 = a[0] * R, lat1 = a[1] * R, lng2 = b[0] * R, lat2 = b[1] * R;
  const dLat = lat2 - lat1, dLng = lng2 - lng1;
  const d = 2 * Math.asin(Math.sqrt(
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2));
  if (!(d > 1e-9)) return [[a, b]];                       // coincident/degenerate
  const n = Math.min(64, Math.max(6, Math.round(d * D / 4)));  // ~1 point / 4°
  const pts: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const f = i / n;
    const A = Math.sin((1 - f) * d) / Math.sin(d);
    const B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(lat1) * Math.cos(lng1) + B * Math.cos(lat2) * Math.cos(lng2);
    const y = A * Math.cos(lat1) * Math.sin(lng1) + B * Math.cos(lat2) * Math.sin(lng2);
    const z = A * Math.sin(lat1) + B * Math.sin(lat2);
    pts.push([Math.atan2(y, x) * D, Math.atan2(z, Math.hypot(x, y)) * D]);
  }
  // Split where longitude wraps past the antimeridian.
  const segs: number[][][] = [];
  let seg: number[][] = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    if (Math.abs(pts[i][0] - pts[i - 1][0]) > 180) { if (seg.length > 1) segs.push(seg); seg = [pts[i]]; }
    else seg.push(pts[i]);
  }
  if (seg.length > 1) segs.push(seg);
  return segs.length ? segs : [[a, b]];
}

// Longest LineString in a (KMZ-derived) GeoJSON FeatureCollection, as [lng,lat]
// pairs — used as a link's fiber route. null if no usable line.
function longestLineString(fc: GeoJSON.FeatureCollection): [number, number][] | null {
  let best: any = null, bestLen = -1;
  for (const f of fc.features || []) {
    const g: any = f.geometry; if (!g) continue;
    const lines = g.type === 'LineString' ? [g.coordinates] : g.type === 'MultiLineString' ? g.coordinates : [];
    for (const ln of lines) if (ln.length > bestLen) { bestLen = ln.length; best = ln; }
  }
  return best && best.length >= 2 ? best.map((c: number[]) => [c[0], c[1]] as [number, number]) : null;
}

// Great-circle distance (km) between two [lng,lat] points.
function haversineKm(p1: [number, number], p2: [number, number]): number {
  const R = 6371, toR = Math.PI / 180;
  const dLat = (p2[1] - p1[1]) * toR, dLng = (p2[0] - p1[0]) * toR;
  const la1 = p1[1] * toR, la2 = p2[1] * toR;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

// All LineString coordinate arrays in a GeoJSON geometry, recursing into
// MultiLineString parts and GeometryCollections. togeojson emits a
// GeometryCollection for a KML <MultiGeometry> (e.g. our multi-part simplified
// paths), so handling it here is what lets such files re-import.
function geomLineParts(g: any): [number, number][][] {
  if (!g) return [];
  if (g.type === 'LineString') return Array.isArray(g.coordinates) && g.coordinates.length >= 2 ? [g.coordinates] : [];
  if (g.type === 'MultiLineString') return (g.coordinates || []).filter((ln: any) => ln && ln.length >= 2);
  if (g.type === 'GeometryCollection') { const out: [number, number][][] = []; for (const sub of (g.geometries || [])) for (const p of geomLineParts(sub)) out.push(p); return out; }
  return [];
}

// Every LineString in a KMZ/KML overlay, as a pickable segment (name + coords +
// length). Multi-part geometries are flattened to one entry per part. Used so a
// file holding several fiber routes can map a different segment onto each link.
function lineStringsOf(fc: GeoJSON.FeatureCollection): { name: string; coords: [number, number][]; lenKm: number }[] {
  const out: { name: string; coords: [number, number][]; lenKm: number }[] = [];
  for (const f of fc.features || []) {
    const nm = (f.properties && (f.properties.name || f.properties.Name)) || '';
    for (const ln of geomLineParts(f.geometry as any)) {
      const coords = ln.map((c: number[]) => [c[0], c[1]] as [number, number]);
      let len = 0; for (let i = 1; i < coords.length; i++) len += haversineKm(coords[i - 1], coords[i]);
      out.push({ name: nm || `Segment ${out.length + 1}`, coords, lenKm: len });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// KMZ folder tree + segment stitching.
//
// Real carrier KMZs (e.g. DWDM circuit exports) shatter a single route between
// two sites into dozens-to-hundreds of tiny, out-of-order LineString fragments
// nested inside Folders ("orders" → "Path N: A-B" → "path"). To map one logical
// link to such a route we (1) preserve the folder hierarchy so the user can
// pick the right folder, then (2) stitch every fragment under it into one
// ordered polyline by chaining shared endpoints.
// ---------------------------------------------------------------------------
type KmlNode = {
  id: string;                                      // stable hierarchical id ("r", "r/0", "r/0/3")
  name: string;
  folders: KmlNode[];
  lines: { name: string; coords: [number, number][] }[];
  lineCount: number;                               // recursive line total
  bbox: [number, number, number, number] | null;  // [w,s,e,n] recursive
};

function kmlNameOf(el: Element): string {
  for (const c of Array.from(el.children)) if (c.localName === 'name') return (c.textContent || '').trim();
  return '';
}
function kmlParseCoords(text: string): [number, number][] {
  const out: [number, number][] = [];
  for (const tok of text.trim().split(/\s+/)) {
    const p = tok.split(',');
    if (p.length >= 2) { const x = +p[0], y = +p[1]; if (Number.isFinite(x) && Number.isFinite(y)) out.push([x, y]); }
  }
  return out;
}
// Every LineString in a Placemark (incl. inside MultiGeometry), as coord arrays.
function kmlLinesOfPlacemark(pm: Element): { name: string; coords: [number, number][] }[] {
  const nm = kmlNameOf(pm) || 'segment';
  const out: { name: string; coords: [number, number][] }[] = [];
  const rec = (el: Element) => {
    for (const c of Array.from(el.children)) {
      if (c.localName === 'LineString') {
        const co = Array.from(c.children).find(x => x.localName === 'coordinates');
        if (co?.textContent) { const pts = kmlParseCoords(co.textContent); if (pts.length >= 2) out.push({ name: nm, coords: pts }); }
      } else if (c.localName === 'MultiGeometry') rec(c);
    }
  };
  rec(pm);
  return out;
}
// Build the folder hierarchy from a parsed KML DOM (one pass).
function buildKmlTree(dom: Document): KmlNode {
  const mk = (name: string): KmlNode => ({ id: '', name, folders: [], lines: [], lineCount: 0, bbox: null });
  const root = mk('(root)');
  const walk = (el: Element, node: KmlNode) => {
    for (const child of Array.from(el.children)) {
      const ln = child.localName;
      if (ln === 'Folder' || ln === 'Document') {
        const sub = mk(kmlNameOf(child) || ln); node.folders.push(sub); walk(child, sub);
      } else if (ln === 'Placemark') {
        for (const l of kmlLinesOfPlacemark(child)) node.lines.push(l);
      } else if (ln === 'kml') walk(child, node);
    }
  };
  walk(dom.documentElement, root);
  // Recursive line count + bbox.
  const finalize = (node: KmlNode): void => {
    let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity, count = node.lines.length;
    for (const l of node.lines) for (const [x, y] of l.coords) { if (x < w) w = x; if (x > e) e = x; if (y < s) s = y; if (y > n) n = y; }
    for (const f of node.folders) { finalize(f); count += f.lineCount; if (f.bbox) { w = Math.min(w, f.bbox[0]); s = Math.min(s, f.bbox[1]); e = Math.max(e, f.bbox[2]); n = Math.max(n, f.bbox[3]); } }
    node.lineCount = count;
    node.bbox = Number.isFinite(w) ? [w, s, e, n] : null;
  };
  finalize(root);
  // Collapse structural single-child wrappers (kml→Document→rootFolder…) so the
  // user starts at the first folder that actually branches or holds geometry.
  let r = root;
  while (r.folders.length === 1 && !r.lines.length) r = r.folders[0];
  const assignIds = (node: KmlNode, id: string): void => { node.id = id; node.folders.forEach((f, i) => assignIds(f, `${id}/${i}`)); };
  assignIds(r, 'r');
  return r;
}
// Flatten a node's subtree to an id→node lookup (for selection state).
function indexKmlNodes(root: KmlNode): Map<string, KmlNode> {
  const m = new Map<string, KmlNode>();
  const walk = (n: KmlNode) => { m.set(n.id, n); n.folders.forEach(walk); };
  walk(root);
  return m;
}
// Write a set of named routes to a KMZ Blob (doc.kml zipped) — client-side export.
async function buildKmzBlob(paths: { name: string; parts: [number, number][][] }[]): Promise<Blob> {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const lineStr = (c: [number, number][]) => `<LineString><tessellate>1</tessellate><coordinates>${c.map(p => `${p[0]},${p[1]},0`).join(' ')}</coordinates></LineString>`;
  const placemarks = paths.map(p => {
    const parts = p.parts.filter(pt => pt.length >= 2);
    const geom = parts.length === 1 ? lineStr(parts[0]) : `<MultiGeometry>${parts.map(lineStr).join('')}</MultiGeometry>`;
    return `<Placemark><name>${esc(p.name)}</name><Style><LineStyle><color>ff00d3ff</color><width>4</width></LineStyle></Style>${geom}</Placemark>`;
  }).join('');
  const doc = `<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Simplified paths</name>${placemarks}</Document></kml>`;
  const zip = new JSZip();
  zip.file('doc.kml', doc);
  return zip.generateAsync({ type: 'blob' });
}
// Distance (screen px) from point p to segment a–b. Used to find which edge of
// a stitched path lies under the erase brush.
function distPtSeg(p: { x: number; y: number }, a: { x: number; y: number }, b: { x: number; y: number }): number {
  const dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
  if (l2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2; t = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}
// The two far ends of a cable geometry (first point of the first part, last of
// the last part) — used to mark a highlighted submarine cable's endpoints.
function cableEndsOf(geom: any): [number, number][] {
  if (!geom) return [];
  if (geom.type === 'LineString') { const c = geom.coordinates; return c.length >= 2 ? [c[0], c[c.length - 1]] : []; }
  if (geom.type === 'MultiLineString') {
    const parts = geom.coordinates.filter((p: any) => p.length); if (!parts.length) return [];
    const first = parts[0], last = parts[parts.length - 1];
    return [first[0], last[last.length - 1]];
  }
  return [];
}
// Stable-ish key for a fragment so duplicates (from tree vs. map-trace) dedupe.
function fragKey(coords: [number, number][]): string {
  const a = coords[0], b = coords[coords.length - 1];
  const r = (n: number) => n.toFixed(5);
  return `${r(a[0])},${r(a[1])}|${r(b[0])},${r(b[1])}|${coords.length}`;
}
// All line fragments under a node (recursive).
function collectKmlLines(node: KmlNode): [number, number][][] {
  const out: [number, number][][] = node.lines.map(l => l.coords);
  for (const f of node.folders) for (const c of collectKmlLines(f)) out.push(c);
  return out;
}
// Distance (km) from a point to the nearest edge of a bbox (0 if inside).
function distPtBbox(pt: [number, number], bbox: [number, number, number, number]): number {
  const cx = Math.max(bbox[0], Math.min(pt[0], bbox[2]));
  const cy = Math.max(bbox[1], Math.min(pt[1], bbox[3]));
  return haversineKm([cx, cy], pt);
}
// Stitch out-of-order LineString fragments into one ordered polyline by greedy
// nearest-endpoint chaining, anchored so the chain starts nearest `anchor`
// (the link's A node). Each hop is bounded by `tolKm` so it never leaps across
// unrelated fragments — it simply stops, leaving disconnected pieces unused.
// When `bridge` is false (default) a hop farther than `tolKm` ends the chain,
// leaving unrelated fragments unused — right for "stitch a whole folder" that
// may hold several distinct routes. When `bridge` is true (a curated brush
// selection), the chain never stops while fragments remain: it jumps to the
// nearest leftover endpoint, joining separate segment groups into ONE path with
// a straight connector across the gap. `bridgedKm`/`bridges` report those gaps.
function stitchFragments(
  frags: [number, number][][], anchor: [number, number] | null,
  opts?: { tolKm?: number; bridge?: boolean; maxBridgeKm?: number },
): { coords: [number, number][]; usedCount: number; totalFrags: number; lengthKm: number; bridges: number; bridgedKm: number } | null {
  const tolKm = opts?.tolKm ?? 2;
  const bridge = opts?.bridge ?? false;
  const maxBridgeKm = opts?.maxBridgeKm ?? Infinity;  // refuse connectors longer than this
  const segs = frags.filter(f => f.length >= 2);
  if (!segs.length) return null;
  const used = new Array(segs.length).fill(false);
  // Start: the fragment endpoint nearest the anchor (or the first fragment).
  let si = 0, se = 0, bestd = Infinity;
  segs.forEach((f, i) => {
    const d0 = anchor ? haversineKm(anchor, f[0]) : 0;
    const d1 = anchor ? haversineKm(anchor, f[f.length - 1]) : 0;
    if (d0 < bestd) { bestd = d0; si = i; se = 0; }
    if (d1 < bestd) { bestd = d1; si = i; se = 1; }
  });
  let chain = se === 0 ? segs[si].slice() : segs[si].slice().reverse();
  used[si] = true;
  // Grow from BOTH ends: each step attaches the nearest unused fragment to
  // whichever end of the chain it is closest to (prepend at head / append at
  // tail). Single-ended growth strands the half of the route "behind" the start
  // fragment, which then bridges back as a long straight-line artifact.
  let head = chain[0], tail = chain[chain.length - 1];
  let bridges = 0, bridgedKm = 0;
  for (;;) {
    let best = Infinity, bi = -1, atTail = true, fragRev = false;
    for (let i = 0; i < segs.length; i++) {
      if (used[i]) continue;
      const f = segs[i], s = f[0], e = f[f.length - 1];
      const dts = haversineKm(tail, s); if (dts < best) { best = dts; bi = i; atTail = true; fragRev = false; }   // append, start→tail
      const dte = haversineKm(tail, e); if (dte < best) { best = dte; bi = i; atTail = true; fragRev = true; }    // append reversed, end→tail
      const dhe = haversineKm(head, e); if (dhe < best) { best = dhe; bi = i; atTail = false; fragRev = false; }  // prepend, end→head
      const dhs = haversineKm(head, s); if (dhs < best) { best = dhs; bi = i; atTail = false; fragRev = true; }   // prepend reversed, start→head
    }
    if (bi < 0) break;                       // nothing left
    if (best > tolKm) {
      // Stop (leaving the rest unused) at a real gap when not bridging, or when
      // the nearest leftover is farther than the user's max-connector limit —
      // avoids drawing a long straight line to a stray/disconnected fragment.
      if (!bridge || best > maxBridgeKm) break;
      bridges++; bridgedKm = Math.max(bridgedKm, best);  // curated: bridge it
    }
    const f = segs[bi];
    const seg = fragRev ? f.slice().reverse() : f.slice();
    const snap = best < 0.05;
    if (atTail) { chain = chain.concat(snap ? seg.slice(1) : seg); tail = chain[chain.length - 1]; }   // seg[0] adjacent to tail
    else { chain = (snap ? seg.slice(0, seg.length - 1) : seg).concat(chain); head = chain[0]; }       // seg[last] adjacent to head
    used[bi] = true;
  }
  let length = 0; for (let k = 1; k < chain.length; k++) length += haversineKm(chain[k - 1], chain[k]);
  return { coords: chain, usedCount: used.filter(Boolean).length, totalFrags: segs.length, lengthKm: length, bridges, bridgedKm };
}

// Link geometry on the globe: if the link has a geo_path (fiber route), follow
// it — connecting each node to the path's nearest end with a straight (great-
// circle) gap-filler, so a partial route shows real fiber in the middle and
// straight connectors to the nodes. Otherwise a plain great-circle line.
// Normalize a geo_path to a list of parts. Single line [[lng,lat],...] → one
// part; multi-part [[[lng,lat],...],...] → as-is. Multi-part preserves gaps the
// user left (e.g. cleaned-up routes) — parts render separately, no connector.
function geoPathParts(geoPath: any): [number, number][][] {
  if (!Array.isArray(geoPath) || geoPath.length === 0) return [];
  if (Array.isArray(geoPath[0]) && Array.isArray(geoPath[0][0])) return geoPath as [number, number][][];  // nested
  return [geoPath as [number, number][]];  // flat single part
}
function linkRouteSegments(a: [number, number], b: [number, number], geoPath?: any): number[][][] {
  const parts = geoPathParts(geoPath).filter(p => p.length >= 2);
  if (parts.length) {
    // Orient the whole route so node A connects to the nearer outer end.
    const rs = parts[0][0], re = parts[parts.length - 1][parts[parts.length - 1].length - 1];
    const reversed = ((a[0] - re[0]) ** 2 + (a[1] - re[1]) ** 2) < ((a[0] - rs[0]) ** 2 + (a[1] - rs[1]) ** 2);
    const ordered = reversed ? parts.slice().reverse().map(p => p.slice().reverse()) : parts;
    const first = ordered[0], last = ordered[ordered.length - 1];
    return [
      ...greatCircleSegments(a, first[0]),                       // node A → route start (straight)
      ...ordered.map(p => p.map(c => [c[0], c[1]])),             // each part — NO connector between parts
      ...greatCircleSegments(last[last.length - 1], b),          // route end → node B (straight)
    ];
  }
  return greatCircleSegments(a, b);
}

function linksGeoJSON(
  nodes: NodeInstance[], links: any[], highlightedLinkIds: Set<number> | null,
  hiddenRoles?: Set<string>, hiddenNodeIds?: Set<number>,
  satShown: (l: any) => boolean = () => false,
): GeoJSON.FeatureCollection {
  const posById = new Map<number, [number, number]>();
  for (const n of nodes) {
    if (hiddenRoles && hiddenRoles.has(roleOf(n))) continue;     // drop links to hidden kinds
    if (hiddenNodeIds && hiddenNodeIds.has(n.id)) continue;       // ...and to individually-hidden nodes
    const p = nodePos(n); if (p) posById.set(n.id, p);
  }
  const active = !!highlightedLinkIds && highlightedLinkIds.size > 0;
  const features: GeoJSON.Feature[] = [];
  for (const l of links) {
    const sat = isSatelliteLink(l);
    // Satellite links are hidden unless their dish / PoP is selected, they're
    // on a highlighted path, or "Show satellite links" is on.
    if (sat && !satShown(l)) continue;
    const a = posById.get(l.source_node_id);
    const b = posById.get(l.target_node_id);
    if (a && b) {
      const hl = !active ? 0 : (highlightedLinkIds!.has(l.id) ? 1 : 2);
      features.push({
        type: 'Feature',
        geometry: { type: 'MultiLineString', coordinates: linkRouteSegments(a, b, sat ? undefined : l.geo_path) },
        properties: {
          id: l.id, hl, planned: l.is_planned ? 1 : 0, routed: geoPathParts(l.geo_path).some(p => p.length >= 2) ? 1 : 0,
          sat: sat ? 1 : 0, satColor: ORBIT_COLORS[l.orbit || ''] || '#38bdf8',
        },
      });
    }
  }
  return { type: 'FeatureCollection', features };
}

/** Marker dot shape for a node role: a satellite dish is a circle with 📡,
 *  a satellite PoP a rounded square with 🛰; anything else the plain dot.
 *  Applied on every marker update so role changes show immediately. */
function applySatGlyph(dot: HTMLDivElement, role?: string) {
  const sat = isSatRole(role);
  const glyph = !sat ? '' : role === SAT_POP ? '🛰' : '📡';
  if (dot.textContent === glyph) return;
  dot.textContent = glyph;
  Object.assign(dot.style, sat
    ? { width: '18px', height: '18px', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: '10px', lineHeight: '1', borderRadius: role === SAT_POP ? '4px' : '50%' }
    : { width: '14px', height: '14px', display: '', fontSize: '', lineHeight: '', borderRadius: '50%' });
}

/** Satellite-link visibility test from the current store state (selection,
 *  "Show satellite links") plus the highlighted path links. */
function satShownNow(highlighted: Set<number> | null): (l: any) => boolean {
  const st = useStore.getState() as any;
  const sel = new Set<number>(st.selectedNodeId != null ? [st.selectedNodeId] : []);
  const focused = new Set<number>([
    ...(highlighted || []), ...(st.selectedLinkIds || []), ...(st.agentHighlightLinkIds || []),
    ...(st.maintenanceLinkIds || []), ...(st.selectedLinkId != null ? [st.selectedLinkId] : []),
  ]);
  return l => satLinkVisible(l, { showAll: !!st.showSatelliteLinks, selectedNodeIds: sel, focusedLinkIds: focused });
}

// Distinct, dark-background-friendly palette for coloring data centers by
// vendor. The N busiest operators each get a color; the rest fall to grey.
const DC_PALETTE = ['#34d399', '#60a5fa', '#f472b6', '#fbbf24', '#a78bfa', '#22d3ee', '#fb923c', '#4ade80'];
// Distinct colors for up to 10 interconnection paths (path 1 = emerald).
const PATH_PALETTE = ['#34d399', '#fbbf24', '#60a5fa', '#f472b6', '#a78bfa', '#22d3ee', '#fb923c', '#4ade80', '#f43f5e', '#e879f9'];
const DC_OTHER = '#94a3b8';

// Build a MapLibre `match` expression coloring the `operator` property by
// the top operators, plus the legend rows to render alongside.
function dcColorExpr(operators: { name: string; count: number }[]): { expr: any; legend: { name: string; color: string }[] } {
  const top = operators.slice(0, DC_PALETTE.length);
  const legend = top.map((o, i) => ({ name: o.name, color: DC_PALETTE[i] }));
  // ['match', ['get','operator'], name1, color1, name2, color2, ..., default]
  const expr: any[] = ['match', ['get', 'operator']];
  for (const { name, color } of legend) expr.push(name, color);
  expr.push(DC_OTHER);
  legend.push({ name: 'Other', color: DC_OTHER });
  return { expr, legend };
}

// Flatten a flow-path result into the set of link ids it traverses (handles
// ECMP — link_ids_per_path is an array of per-path link-id arrays).
function pathLinkIds(fp: any): Set<number> | null {
  if (!fp || !Array.isArray(fp.link_ids_per_path)) return null;
  const s = new Set<number>();
  for (const arr of fp.link_ids_per_path) for (const id of (arr || [])) s.add(id);
  return s.size ? s : null;
}

// Linear-interpolate a constellation's expected sats-in-view at an arbitrary
// latitude from its (sorted) by-latitude grid. Used by the click-to-compare
// readout so any clicked point anywhere on the globe gets a value.
function interpStrengthAt(by: { lat: number; in_view: number }[], lat: number): number {
  if (!by.length) return 0;
  if (lat <= by[0].lat) return by[0].in_view;
  if (lat >= by[by.length - 1].lat) return by[by.length - 1].in_view;
  for (let i = 0; i < by.length - 1; i++) {
    const a = by[i], b = by[i + 1];
    if (lat >= a.lat && lat <= b.lat) {
      const t = (lat - a.lat) / ((b.lat - a.lat) || 1);
      return a.in_view + t * (b.in_view - a.in_view);
    }
  }
  return 0;
}

const _RAD = Math.PI / 180;

// Subsolar point (where the sun is directly overhead) — approximate, good
// enough for a day/night visual. Declination from day-of-year, longitude
// from UTC time (0° at 12:00 UTC, moving west).
function subsolarPoint(d: Date): { lat: number; lon: number } {
  const start = Date.UTC(d.getUTCFullYear(), 0, 0);
  const dayOfYear = (Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - start) / 86400000;
  const decl = -23.44 * Math.cos(_RAD * (360 / 365) * (dayOfYear + 10));
  const hours = d.getUTCHours() + d.getUTCMinutes() / 60 + d.getUTCSeconds() / 3600;
  const lon = ((-(hours - 12) * 15 + 540) % 360) - 180;
  return { lat: decl, lon };
}

// Great-circle distance (km) between two [lon,lat] points.
function greatCircleKm(a: [number, number], b: [number, number]): number {
  const R = 6371;
  const dLat = (b[1] - a[1]) * _RAD, dLon = (b[0] - a[0]) * _RAD;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * _RAD) * Math.cos(b[1] * _RAD) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

// `active` is false while the logical view is shown: the map stays mounted
// (hidden) so returning is instant, but its keyboard handlers stand down.
// Marker color when a node has no node / view / customer color.
const DEFAULT_MARKER_COLOR = '#38bdf8';
// Soft glow in the marker's color; color-mix works for any CSS color format.
const markerGlow = (color: string) => `0 0 6px color-mix(in srgb, ${color} 50%, transparent)`;

export default function GeographicCanvas({ active = true, nodeColors }: {
  active?: boolean;
  /** Per-node marker color matching the logical canvas (node, view or customer color). */
  nodeColors?: Map<number, string>;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<Map<number, maplibregl.Marker>>(new Map());
  const [mapReady, setMapReady] = useState(false);
  const [mapCfg, setMapCfg] = useState<MapCfg | null>(null);
  const [styleError, setStyleError] = useState<string>('');

  const nodes = useStore(s => s.nodes);
  const links = useStore(s => s.links);
  const updateNode = useStore(s => s.updateNode);
  const updateLink = useStore(s => (s as any).updateLink) as (linkId: number, data: any) => Promise<void>;
  const setSelectedNode = useStore(s => s.setSelectedNode);
  const setSidePanel = useStore(s => s.setSidePanel);
  const selectedNodeId = useStore(s => s.selectedNodeId);
  const showSatelliteLinks = useStore(s => s.showSatelliteLinks);
  // Links in focus (selected / AI-highlighted / maintenance) also reveal hidden
  // satellite links; this key re-runs the link refresh when they change.
  const satFocusKey = useStore(s => [s.selectedLinkId, s.selectedLinkIds.join(','), s.agentHighlightLinkIds.join(','),
    [...s.maintenanceLinkIds].join(',')].join('|'));
  const toggleShowSatelliteLinks = useStore(s => s.toggleShowSatelliteLinks);
  const currentTopologyId = useStore(s => s.currentTopologyId);
  const currentTopologyIdRef = useRef(currentTopologyId); currentTopologyIdRef.current = currentTopologyId;
  // Protected topology: markers aren't draggable unless the user holds the edit session.
  const canEdit = useStore(s => (s as any).canEditCurrentTopology()) as boolean;
  const canEditRef = useRef(canEdit); canEditRef.current = canEdit;
  // The flow path the user clicked in Flows/Links — same store field the
  // logical view highlights. We emphasize its links on the globe and dim
  // the rest.
  const highlightedFlowPaths = useStore(s => (s as any).highlightedFlowPaths);
  const highlightedLinkIds = useMemo(() => pathLinkIds(highlightedFlowPaths), [highlightedFlowPaths]);
  const showSubmarineCables = useStore(s => (s as any).showSubmarineCables) as boolean;
  const toggleSubmarineCables = useStore(s => (s as any).toggleSubmarineCables) as () => void;
  const showDataCenters = useStore(s => (s as any).showDataCenters) as boolean;
  const toggleDataCenters = useStore(s => (s as any).toggleDataCenters) as () => void;
  const showIXPs = useStore(s => (s as any).showIXPs) as boolean;
  const toggleIXPs = useStore(s => (s as any).toggleIXPs) as () => void;
  const showCloudRegions = useStore(s => (s as any).showCloudRegions) as boolean;
  const toggleCloudRegions = useStore(s => (s as any).toggleCloudRegions) as () => void;
  const showGeoCoverage = useStore(s => (s as any).showGeoCoverage) as boolean;
  const toggleGeoCoverage = useStore(s => (s as any).toggleGeoCoverage) as () => void;
  const showWeatherAlerts = useStore(s => (s as any).showWeatherAlerts) as boolean;
  const toggleWeatherAlerts = useStore(s => (s as any).toggleWeatherAlerts) as () => void;
  const showActiveFires = useStore(s => (s as any).showActiveFires) as boolean;
  const toggleActiveFires = useStore(s => (s as any).toggleActiveFires) as () => void;
  const showEarthquakes = useStore(s => (s as any).showEarthquakes) as boolean;
  const toggleEarthquakes = useStore(s => (s as any).toggleEarthquakes) as () => void;
  const showCyclones = useStore(s => (s as any).showCyclones) as boolean;
  const toggleCyclones = useStore(s => (s as any).toggleCyclones) as () => void;
  const showInternetOutages = useStore(s => (s as any).showInternetOutages) as boolean;
  const toggleInternetOutages = useStore(s => (s as any).toggleInternetOutages) as () => void;
  const showCoverageStrength = useStore(s => (s as any).showCoverageStrength) as boolean;
  const toggleCoverageStrength = useStore(s => (s as any).toggleCoverageStrength) as () => void;
  // Tracks whether overlay layers have been added yet (lazy — we only fetch
  // the datasets the first time each overlay is enabled).
  const subseaLoadedRef = useRef(false);
  const [subseaError, setSubseaError] = useState('');
  // Cable ownership enrichment (color/filter cables by owner).
  const cableOwnersLoadedRef = useRef(false);
  const cablesFCRef = useRef<any>(null);
  const cableHlRef = useRef<Set<string>>(new Set());  // highlighted cable ids (left-click; ctrl-click adds)
  const cableOwnersRef = useRef<Record<string, { owners: string; suppliers: string; rfs: string; rfs_year: number | null; length: string; length_km: number | null; provider: string }>>({});
  const [cableProviders, setCableProviders] = useState<{ key: string; label: string; color: string; count: number }[]>([]);
  const [cableProviderFilter, setCableProviderFilter] = useState<string | null>(null);
  // Carrier-lookup panel: which facility (and its country) is being inspected.
  const [carrierLookup, setCarrierLookup] = useState<CarrierLookup | null>(null);
  // "Connectivity finder" mode: when on, clicking anywhere finds nearest hubs.
  const [finderMode, setFinderMode] = useState(false);
  const finderModeRef = useRef(finderMode); finderModeRef.current = finderMode;
  // Geographic edit mode: node markers are only draggable while this is on, so
  // panning the globe can't accidentally move a node. Default OFF every session.
  const [geoEditMode, setGeoEditMode] = useState(false);
  const geoEditModeRef = useRef(geoEditMode); geoEditModeRef.current = geoEditMode;
  // Undo stack for accidental moves: each entry restores a node to its position
  // before a drag. undoCount drives the button; the ref holds the data.
  const undoStackRef = useRef<Array<{ nodeId: number; lat: number; lng: number }>>([]);
  const [undoCount, setUndoCount] = useState(0);
  // Undo the most recent geographic move by restoring the node's prior position.
  const undoLastMove = useCallback(() => {
    const prev = undoStackRef.current.pop();
    setUndoCount(undoStackRef.current.length);
    if (!prev) return;
    // Skip if the node is gone or has since become inherited (parent-owned).
    const cur = nodesRef.current.find(n => n.id === prev.nodeId);
    if (!cur || (cur as any).is_inherited) return;
    updateNode(prev.nodeId, { geo_latitude: prev.lat, geo_longitude: prev.lng });
  }, [updateNode]);
  // Move the selected node onto the location-search pin. Re-assigned every
  // render so the hotkey handler sees current state. Returns true if it moved.
  const snapToPinRef = useRef<() => boolean>(() => false);
  snapToPinRef.current = () => {
    const pin = searchMarkerRef.current;
    if (!pin || !geoEditMode || !canEdit || pbTool !== 'off' || selectedNodeId == null) return false;
    const n = nodesRef.current.find(nn => nn.id === selectedNodeId);
    if (!n) return false;
    if ((n as any).is_inherited) {
      toast.error('Inherited node — set its geographic position in the parent topology');
      return true;
    }
    const prev = nodePos(n);
    const ll = pin.getLngLat();
    // Record the undo entry only once the move has actually been saved.
    updateNode(n.id, { geo_latitude: ll.lat, geo_longitude: ll.lng })
      .then(() => {
        if (prev) {
          undoStackRef.current.push({ nodeId: n.id, lat: prev[1], lng: prev[0] });
          setUndoCount(undoStackRef.current.length);
        }
        toast.success(`Moved ${n.name} to the search location`);
      })
      .catch(() => toast.error('Could not move node'));
    return true;
  };
  // Leaving edit mode on a protected topology (commit / discard / expiry) also
  // ends position editing — the button is hidden then, so it can't be toggled off.
  useEffect(() => { if (!canEdit) setGeoEditMode(false); }, [canEdit]);
  // Drop the undo stack when switching topologies (positions are per-topology).
  useEffect(() => {
    undoStackRef.current = [];
    setUndoCount(0);
  }, [currentTopologyId]);
  // Ctrl/Cmd+Z undoes a move while in edit mode.
  useEffect(() => {
    if (!geoEditMode || !active) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
        // Don't hijack undo while the user is typing in a field.
        const t = e.target as HTMLElement | null;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
        e.preventDefault();
        undoLastMove();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [geoEditMode, undoLastMove, active]);
  // Node-kind filter: which roles are hidden from the globe. Customer nodes are
  // hidden by default (they clutter the geographic view); the user can toggle
  // any role on/off. Persisted across sessions.
  const [hiddenRoles, setHiddenRoles] = useState<Set<string>>(() => {
    try { const s = localStorage.getItem('geo_hidden_roles'); if (s) return new Set<string>(JSON.parse(s)); } catch { /* ignore */ }
    return new Set<string>(['customer']);
  });
  const hiddenRolesRef = useRef(hiddenRoles); hiddenRolesRef.current = hiddenRoles;
  useEffect(() => {
    try { localStorage.setItem('geo_hidden_roles', JSON.stringify([...hiddenRoles])); } catch { /* ignore */ }
  }, [hiddenRoles]);
  // Roles present in the topology (+ counts) for the filter control.
  const availableRoles = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of nodes) { const r = roleOf(n); m.set(r, (m.get(r) || 0) + 1); }
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([role, count]) => ({ role, count }));
  }, [nodes]);
  // Individually-hidden nodes (right-click a marker to hide it). Persisted;
  // node ids are globally unique so a single set is safe across topologies.
  const [hiddenNodeIds, setHiddenNodeIds] = useState<Set<number>>(() => {
    try { const s = localStorage.getItem('geo_hidden_nodes'); if (s) return new Set<number>(JSON.parse(s)); } catch { /* ignore */ }
    return new Set<number>();
  });
  const hiddenNodeIdsRef = useRef(hiddenNodeIds); hiddenNodeIdsRef.current = hiddenNodeIds;
  useEffect(() => {
    try { localStorage.setItem('geo_hidden_nodes', JSON.stringify([...hiddenNodeIds])); } catch { /* ignore */ }
  }, [hiddenNodeIds]);
  const hiddenNodesPresent = useMemo(() => nodes.filter(n => hiddenNodeIds.has(n.id)), [nodes, hiddenNodeIds]);
  // Address/place search (geocode → re-orient the map).
  const [geoQuery, setGeoQuery] = useState('');
  const [geoResults, setGeoResults] = useState<{ display_name: string; lat: number; lon: number; bbox: [number, number, number, number] | null }[]>([]);
  const [geoSearching, setGeoSearching] = useState(false);
  const [geoSearchError, setGeoSearchError] = useState('');
  const [geoOpen, setGeoOpen] = useState(false);
  const [hasPin, setHasPin] = useState(false);
  const searchMarkerRef = useRef<maplibregl.Marker | null>(null);
  const geoInputRef = useRef<HTMLInputElement>(null);
  // "/" node search: fly to (and select) a node shown on the map.
  const [nodeSearchOpen, setNodeSearchOpen] = useState(false);
  const closeNodeSearch = useCallback(() => setNodeSearchOpen(false), []);
  // Address box: node-name suggestions shown while typing, plus a keyboard
  // highlight across [node suggestions..., geocode results...].
  const [addrFocused, setAddrFocused] = useState(false);
  const [addrHl, setAddrHl] = useState(-1);
  // Interconnection path finder (A-loc → Z-loc heuristic carrier path).
  const [showPathFinder, setShowPathFinder] = useState(false);
  const [pathA, setPathA] = useState('');
  const [pathZ, setPathZ] = useState('');
  const [pathResult, setPathResult] = useState<any>(null);
  const [pathLoading, setPathLoading] = useState(false);
  const [pathError, setPathError] = useState('');
  const [pathCount, setPathCount] = useState(2);
  // User-imported KMZ/KML overlays (e.g. fiber routes) — client-side only.
  const [kmzLayers, setKmzLayers] = useState<{ id: string; name: string; count: number }[]>([]);
  const [kmzError, setKmzError] = useState('');
  const kmzCounterRef = useRef(0);
  const kmzDataRef = useRef<Map<string, GeoJSON.FeatureCollection>>(new Map());  // imported overlay GeoJSON, for link assignment
  const kmzTreeRef = useRef<Map<string, KmlNode>>(new Map());  // imported overlay folder tree, for stitching
  const kmzLayersRef = useRef<{ id: string; name: string; count: number }[]>([]); kmzLayersRef.current = kmzLayers;
  // Path Builder: turn a messy multi-folder KMZ into clean named routes.
  const [pbOpen, setPbOpen] = useState(false);
  const [pbOverlayId, setPbOverlayId] = useState<string>('');
  const [pbExpanded, setPbExpanded] = useState<Set<string>>(new Set());
  const [pbSelected, setPbSelected] = useState<Set<string>>(new Set());
  const [pbName, setPbName] = useState('');
  const [pbPaths, setPbPaths] = useState<{ name: string; parts: [number, number][][]; lengthKm: number; used: number; total: number }[]>([]);
  const [pbEditIdx, setPbEditIdx] = useState<number | null>(null);  // built path open for artifact-erase editing
  const pbEditIdxRef = useRef<number | null>(null); pbEditIdxRef.current = pbEditIdx;
  const pbPathsRef = useRef<typeof pbPaths>([]); pbPathsRef.current = pbPaths;
  const [pbNote, setPbNote] = useState('');
  const [pbTool, setPbTool] = useState<'off' | 'brush' | 'erase' | 'move'>('off');  // art-canvas tool
  const [pbBrush, setPbBrush] = useState(18);            // brush radius in screen px
  const [pbMaxConnector, setPbMaxConnector] = useState(50);  // km; longest straight connector the stitch may draw
  const [pbShowFolders, setPbShowFolders] = useState(false);
  const [pbSelVersion, setPbSelVersion] = useState(0);   // bumped when the traced selection changes
  const pbTraceRef = useRef<Map<string, [number, number][]>>(new Map());  // "featureId:part" → coords (traced)
  const pbToolRef = useRef<'off' | 'brush' | 'erase' | 'move'>('off'); pbToolRef.current = pbTool;
  const pbBrushRef = useRef(18); pbBrushRef.current = pbBrush;
  const pbOverlayIdRef = useRef(''); pbOverlayIdRef.current = pbOverlayId;
  const pbPaintingRef = useRef(false);
  const pbLastPtRef = useRef<{ x: number; y: number } | null>(null);  // previous brush position (stroke interpolation)
  const pbBrushCircleRef = useRef<HTMLDivElement>(null);
  const pbSelectedRef = useRef<Set<string>>(new Set()); pbSelectedRef.current = pbSelected;
  // Attaching a fiber route to a link via right-click → "import new file".
  const linkRouteInputRef = useRef<HTMLInputElement>(null);
  const pendingLinkRef = useRef<number | null>(null);
  const routePopupRef = useRef<any>(null);
  const dcLoadedRef = useRef(false);
  const [dcError, setDcError] = useState('');
  const ixLoadedRef = useRef(false);
  const [ixError, setIxError] = useState('');
  const cloudLoadedRef = useRef(false);
  const [cloudError, setCloudError] = useState('');
  const [cloudProviders, setCloudProviders] = useState<{ key: string; label: string; color: string; count: number }[]>([]);
  const [cloudHidden, setCloudHidden] = useState<Set<string>>(new Set());
  const geoLoadedRef = useRef(false);
  const [geoError, setGeoError] = useState('');
  const [geoRingLevels, setGeoRingLevels] = useState(0);
  const [geoOperators, setGeoOperators] = useState<{ key: string; label: string; color: string; count: number }[]>([]);
  const [geoHidden, setGeoHidden] = useState<Set<string>>(new Set());
  // Legend entries (vendor → color) populated once data-centers load.
  const [dcLegend, setDcLegend] = useState<{ name: string; color: string }[]>([]);
  // Active vendor filter for the data-center layer. null = show all.
  // 'Other' = everything outside the named legend operators.
  const [dcFilter, setDcFilter] = useState<string | null>(null);
  // Weather-alerts overlay state.
  const weatherLoadedRef = useRef(false);
  const [weatherError, setWeatherError] = useState('');
  const [weatherSeverities, setWeatherSeverities] = useState<{ severity: string; color: string; count: number }[]>([]);
  const [weatherFeatures, setWeatherFeatures] = useState<any[]>([]);
  // Active-fires overlay state.
  const firesLoadedRef = useRef(false);
  const [firesError, setFiresError] = useState('');
  const [fireCounts, setFireCounts] = useState<{ perimeters: number; incidents: number }>({ perimeters: 0, incidents: 0 });
  // Earthquake / cyclone / internet-outage overlay state.
  const quakeLoadedRef = useRef(false);
  const [quakeError, setQuakeError] = useState('');
  const [quakeCount, setQuakeCount] = useState(0);
  const cycloneLoadedRef = useRef(false);
  const [cycloneError, setCycloneError] = useState('');
  const [cycloneCount, setCycloneCount] = useState(0);
  const outageLoadedRef = useRef(false);
  const [outageError, setOutageError] = useState('');
  const [outageCount, setOutageCount] = useState(0);
  const [weatherActivity, setWeatherActivity] = useState<{ active: number; possible: number }>({ active: 0, possible: 0 });
  // Nodes that fall inside an active weather-alert area (visible nodes only).
  const atRiskNodeIds = useMemo(() => {
    const s = new Set<number>();
    if (!showWeatherAlerts || weatherFeatures.length === 0) return s;
    for (const n of nodes) {
      if (hiddenRoles.has(roleOf(n)) || hiddenNodeIds.has(n.id)) continue;
      const p = nodePos(n);
      if (!p) continue;
      for (const f of weatherFeatures) { if (pointInGeom(p, f.geometry)) { s.add(n.id); break; } }
    }
    return s;
  }, [showWeatherAlerts, weatherFeatures, nodes, hiddenRoles, hiddenNodeIds]);
  // Coverage-strength overlay state (satellites-in-view vs latitude).
  type StrengthC = { key: string; vendor: string; color: string; regime: string; satellite_count: number; peak_in_view: number; by_latitude: { lat: number; in_view: number }[] };
  const strengthLoadedRef = useRef(false);
  const [strengthError, setStrengthError] = useState('');
  const [strengthData, setStrengthData] = useState<StrengthC[]>([]);
  // Which constellation's density heat is shown (one at a time, by design).
  const [heatKey, setHeatKey] = useState<string>('');
  // Satellite-operator PoP plot (Starlink/OneWeb interconnection points);
  // any combination of operators at once.
  const [popKeys, setPopKeys] = useState<string[]>([]);
  const togglePopKey = (key: string) => setPopKeys(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]);
  const [operatorPopsInfo, setOperatorPopsInfo] = useState<{ label: string; facilities: number; ixps: number }[]>([]);
  const [operatorPopsError, setOperatorPopsError] = useState('');
  const popCacheRef = useRef(new Map<string, Awaited<ReturnType<typeof api.getOperatorPops>>>());
  // Layer-panel groups: how many layers each has on, and which are expanded
  // (a group starts open when any of its layers is already on).
  const infraOn = [showSubmarineCables, showDataCenters, showIXPs, showCloudRegions, showSatelliteLinks].filter(Boolean).length + popKeys.length;
  const satOn = [showCoverageStrength, showGeoCoverage].filter(Boolean).length;
  const liveOn = [showWeatherAlerts, showActiveFires, showEarthquakes, showCyclones, showInternetOutages].filter(Boolean).length;
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set(
    ([['infra', infraOn], ['sat', satOn], ['live', liveOn]] as const).filter(([, n]) => n > 0).map(([k]) => k),
  ));
  // F ("full screen") hotkey: collapse the layer groups and close the map's
  // own menus / popovers. Layers stay on; only their controls fold away.
  useEffect(() => {
    const onClose = () => {
      setOpenGroups(new Set());
      setShowPathFinder(false);
      setFinderMode(false);
      setCarrierLookup(null);
      setGeoOpen(false);
      setNodeSearchOpen(false);
      if (routePopupRef.current) { routePopupRef.current.remove(); routePopupRef.current = null; }
    };
    window.addEventListener(CLOSE_MENUS_EVENT, onClose);
    return () => window.removeEventListener(CLOSE_MENUS_EVENT, onClose);
  }, []);
  const toggleGroup = (key: string) => setOpenGroups(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });
  // Latest strength data for the imperative map-click readout handler.
  const strengthRef = useRef<StrengthC[]>([]); strengthRef.current = strengthData;
  const strengthPopupRef = useRef<any>(null);

  // Latest nodes/links/highlight for use inside imperative MapLibre handlers
  // without re-binding them on every render.
  const nodesRef = useRef(nodes); nodesRef.current = nodes;
  const linksRef = useRef(links); linksRef.current = links;
  const hlRef = useRef(highlightedLinkIds); hlRef.current = highlightedLinkIds;

  // Fetch the map config (basemap source/theme) once.
  useEffect(() => {
    api.getFeatures().then(f => setMapCfg(f.map || {})).catch(() => setMapCfg({}));
  }, []);

  const notConfigured = !!mapCfg && !mapCfg.style_url && !mapCfg.api_key && !mapCfg.tiles_url;

  // Initialize the map once we have config.
  useEffect(() => {
    if (!mapCfg || !containerRef.current || mapRef.current) return;
    const style = buildStyle(mapCfg);
    if (!style) return;  // not configured — the banner below explains how to set it up
    ensurePmtiles();
    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        style,
        center: [-60, 25],   // pulled back over the Americas so the sphere reads
        zoom: 1.6,           // zoomed out so the globe is visible as a ball
        // ...unless we're coming back from the logical view: resume that camera.
        ...(savedGeoCamera.get(currentTopologyIdRef.current ?? -1) || {}),
        minZoom: 0.4,        // allow pulling back to the full sphere
        attributionControl: { compact: true },
      });
    } catch (e: any) {
      setStyleError(e?.message || String(e));
      return;
    }
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    map.on('error', (e: any) => {
      const msg = e?.error?.message || '';
      // Per-tile / source fetch failures (a vector tile blip, a client-side
      // blocker, a momentary network drop) are transient — the map keeps
      // working — so don't raise the "Map failed to load" banner for them.
      // Only genuine style/source-level failures should surface.
      const isTileError = !!e?.sourceId || /\.(mvt|pbf)(\?|$|#)/.test(msg);
      if (isTileError) { if (msg) console.warn('Map tile fetch issue (transient):', msg); return; }
      if (msg) setStyleError(msg);
    });
    // Clicking the basemap (empty space) clears the highlighted flow path —
    // mirrors clicking the empty canvas in the logical view. Node-marker
    // clicks are separate DOM overlays and don't reach this handler.
    map.on('click', () => {
      if (useStore.getState().highlightedFlowPaths) {
        (useStore.getState() as any).setHighlightedFlowPaths(null);
      }
    });
    map.on('load', () => {
      applyEnglishLabels(map);
      // Globe projection gives the curvature-of-the-earth feel. `setProjection`
      // isn't in maplibre-gl v4's published types yet, so call via any.
      try { (map as any).setProjection({ type: 'globe' }); } catch { /* older maplibre */ }
      // Sky + atmosphere so the globe floats in dark space with a soft halo,
      // selling the "large globe" feel rather than a flat slightly-curved map.
      try {
        (map as any).setSky({
          'sky-color': '#06090f',
          'horizon-color': '#1b2735',
          'fog-color': '#0b1018',
          'sky-horizon-blend': 0.5,
          'horizon-fog-blend': 0.6,
          'fog-ground-blend': 0.4,
          'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.8, 5, 0.4, 7, 0],
        });
      } catch { /* setSky unavailable on older maplibre */ }
      map.addSource('topo-links', { type: 'geojson', data: linksGeoJSON(nodesRef.current, linksRef.current, hlRef.current, hiddenRolesRef.current, hiddenNodeIdsRef.current, satShownNow(hlRef.current)) });
      // Shared hl-driven paint (1 = on highlighted flow path → amber,
      // 2 = off-path → dim, 0 = neutral).
      const hlColor = (base: string) => ['match', ['get', 'hl'], 1, '#f59e0b', base] as any;
      const hlWidth = ['match', ['get', 'hl'], 1, 3.5, 1.5] as any;
      const hlOpacity = (neutral: number) => ['match', ['get', 'hl'], 1, 0.95, 2, 0.12, neutral] as any;
      // Non-planned links: solid sky-blue.
      map.addLayer({
        id: 'topo-links',
        type: 'line',
        source: 'topo-links',
        filter: ['all', ['!=', ['get', 'planned'], 1], ['!=', ['get', 'sat'], 1]],
        paint: { 'line-color': hlColor('#38bdf8'), 'line-width': hlWidth, 'line-opacity': hlOpacity(0.7) },
      });
      // Satellite links (only present when shown): dotted, colored by orbit.
      map.addLayer({
        id: 'topo-links-sat',
        type: 'line',
        source: 'topo-links',
        filter: ['==', ['get', 'sat'], 1],
        paint: {
          'line-color': ['match', ['get', 'hl'], 1, '#f59e0b', ['get', 'satColor']] as any,
          'line-width': ['match', ['get', 'hl'], 1, 3, 1.6] as any,
          'line-opacity': hlOpacity(0.85),
          'line-dasharray': [1, 2.5],
        },
      });
      // Planned links: dashed indigo, dimmer — mirrors the logical view.
      // line-dasharray isn't data-driven in MapLibre, so planned links need
      // their own layer.
      map.addLayer({
        id: 'topo-links-planned',
        type: 'line',
        source: 'topo-links',
        filter: ['all', ['==', ['get', 'planned'], 1], ['!=', ['get', 'sat'], 1]],
        paint: {
          'line-color': hlColor('#818cf8'),
          'line-width': hlWidth,
          'line-opacity': hlOpacity(0.5),
          'line-dasharray': [2, 2],
        },
      });
      // Invisible wide hit-line so right-click (attach fiber route) is easy.
      map.addLayer({ id: 'topo-links-hit', type: 'line', source: 'topo-links',
        paint: { 'line-color': '#000', 'line-width': 14, 'line-opacity': 0 } });
      setMapReady(true);
    });
    return () => {
      if (currentTopologyIdRef.current != null) savedGeoCamera.set(currentTopologyIdRef.current, cameraOf(map));
      map.remove(); mapRef.current = null; markersRef.current.clear(); setMapReady(false); };
  }, [mapCfg]);

  // The map stays mounted across topology switches, so save/restore the
  // per-topology camera here too (not just on mount/unmount).
  const prevTopoIdRef = useRef(currentTopologyId);
  useEffect(() => {
    const prev = prevTopoIdRef.current;
    prevTopoIdRef.current = currentTopologyId;
    const map = mapRef.current;
    if (!map || prev === currentTopologyId) return;
    if (prev != null) savedGeoCamera.set(prev, cameraOf(map));
    const saved = currentTopologyId != null ? savedGeoCamera.get(currentTopologyId) : undefined;
    if (saved) map.jumpTo(saved);
  }, [currentTopologyId]);

  // Auto-dismiss the map-error banner — a recovered transient shouldn't linger.
  useEffect(() => {
    if (!styleError) return;
    const t = setTimeout(() => setStyleError(''), 12000);
    return () => clearTimeout(t);
  }, [styleError]);

  // Sync markers + link geometry whenever nodes/links change.
  // Far-side node markers: MapLibre hides them (opacityWhenCovered: 0 on each
  // marker); here they're also made click-through, using the same occlusion
  // test so the clickable set matches what's drawn at any tilt or zoom.
  const applyOcclusion = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    const transform = (map as any).transform;
    for (const marker of markersRef.current.values()) {
      const occluded = !!transform?.isLocationOccluded?.(marker.getLngLat());
      // Also click-through while a Path Builder tool is active, so the
      // brush/pan acts on the KMZ canvas and never grabs a node.
      marker.getElement().style.pointerEvents = (pbToolRef.current !== 'off' || occluded) ? 'none' : '';
    }
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    // Skipped while hidden behind the logical view; reconciles on return.
    if (!map || !mapReady || !active) return;

    const seen = new Set<number>();
    for (const n of nodes) {
      if (hiddenRoles.has(roleOf(n)) || hiddenNodeIds.has(n.id)) continue;  // hidden kind, or individually hidden
      const pos = nodePos(n);
      if (!pos) continue;
      seen.add(n.id);
      // A node is draggable only in edit mode AND when not inherited (an
      // inherited node's geo position is owned by the parent topology). Node ids
      // are stable across parent/child, so cached markers are reconciled via
      // setDraggable in the reuse branch below rather than rebuilt.
      const geoLocked = !!(n as any).is_inherited;
      // Also non-draggable while a path-builder tool is active — otherwise a
      // brush stroke near a node could grab and move it (marker drag bypasses
      // the pointer-events:none that path-builder sets on the element).
      const wantDraggable = geoEditMode && !geoLocked && pbTool === 'off' && canEdit;
      let marker = markersRef.current.get(n.id);
      if (!marker) {
        const el = document.createElement('div');
        el.className = 'geo-node-marker';
        el.style.cssText = 'cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:2px;';
        const dot = document.createElement('div');
        dot.style.cssText = `width:14px;height:14px;border-radius:50%;background:#1e293b;border:2px solid ${DEFAULT_MARKER_COLOR};box-shadow:${markerGlow(DEFAULT_MARKER_COLOR)};`;
        const label = document.createElement('div');
        label.style.cssText = 'font-size:10px;font-weight:600;color:#e2e8f0;text-shadow:0 1px 2px #000;white-space:nowrap;';
        label.textContent = n.name;
        el.appendChild(dot);
        el.appendChild(label);
        el.title = 'Click to select · right-click to hide from map';
        el.addEventListener('click', (ev) => {
          if (pbToolRef.current !== 'off') return;  // path-builder mode: nodes inert
          ev.stopPropagation();
          setSelectedNode(n.id);
          setSidePanel('nodes');
        });
        // Right-click → hide this specific node from the geographic view.
        el.addEventListener('contextmenu', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          setHiddenNodeIds(prev => { const s = new Set(prev); s.add(n.id); return s; });
        });
        if (geoLocked) {
          el.style.cursor = 'default';
          el.title = 'Inherited node — set its geographic position in the parent topology';
        }
        // opacityWhenCovered 0: MapLibre hides the marker while it's on the far
        // side of the globe (its default 0.2 lets nodes show through the earth).
        marker = new maplibregl.Marker({ element: el, draggable: wantDraggable, anchor: 'top', opacityWhenCovered: 0 })
          .setLngLat(pos)
          .addTo(map);
        // Handlers are attached unconditionally; MapLibre only fires drag events
        // while the marker is draggable, and persistence is gated below.
        // preDrag holds the node's position before a drag, for undo.
        let preDrag: { lat: number; lng: number } | null = null;
        marker.on('dragstart', () => {
          const cur = nodesRef.current.find(nn => nn.id === n.id);
          preDrag = (cur && cur.geo_latitude != null && cur.geo_longitude != null)
            ? { lat: cur.geo_latitude as number, lng: cur.geo_longitude as number } : null;
        });
        // Live-update link geometry while dragging; persist geo coords on drop.
        marker.on('drag', () => {
          const src = map.getSource('topo-links') as maplibregl.GeoJSONSource | undefined;
          if (src) {
            const ll = marker!.getLngLat();
            const draft = nodesRef.current.map(nn => nn.id === n.id
              ? { ...nn, geo_latitude: ll.lat, geo_longitude: ll.lng } : nn);
            src.setData(linksGeoJSON(draft, linksRef.current, hlRef.current, hiddenRolesRef.current, hiddenNodeIdsRef.current, satShownNow(hlRef.current)));
          }
        });
        marker.on('dragend', () => {
          // Only persist a deliberate move: edit mode on, no path-builder tool
          // active, and never for a node that's inherited right now (parent-owned).
          if (!geoEditModeRef.current || pbToolRef.current !== 'off') return;
          if ((nodesRef.current.find(nn => nn.id === n.id) as any)?.is_inherited) return;
          const ll = marker!.getLngLat();
          // Push the prior position so the move can be undone (skip first-ever
          // placements, which have no prior position to restore to).
          if (preDrag) {
            undoStackRef.current.push({ nodeId: n.id, lat: preDrag.lat, lng: preDrag.lng });
            setUndoCount(undoStackRef.current.length);
          }
          // Fire-and-forget; swallow rejection (e.g. session expired mid-drag)
          // so it doesn't surface as an unhandled promise rejection.
          updateNode(n.id, { geo_latitude: ll.lat, geo_longitude: ll.lng }).catch(() => {});
        });
        markersRef.current.set(n.id, marker);
      } else {
        // Reconcile draggability + the inherited cue with the current edit mode /
        // topology (markers are cached across both, keyed by stable node id).
        // Guard the writes — this branch runs on every effect pass (selection,
        // hover, filters), so only touch the DOM when something actually changed.
        if (marker.isDraggable() !== wantDraggable) marker.setDraggable(wantDraggable);
        const mEl = marker.getElement();
        const cursor = geoLocked ? 'default' : 'pointer';
        const title = geoLocked
          ? 'Inherited node — set its geographic position in the parent topology'
          : 'Click to select · right-click to hide from map';
        if (mEl.style.cursor !== cursor) mEl.style.cursor = cursor;
        if (mEl.title !== title) mEl.title = title;
        // Only reposition from the store when not mid-drag (avoid fighting the
        // user's pointer). Store updates during drag come from our own dragend,
        // by which point this is the same value.
        marker.setLngLat(pos);
        const labelEl = mEl.querySelector('div + div') as HTMLDivElement | null;
        if (labelEl && labelEl.textContent !== n.name) labelEl.textContent = n.name;
      }
      // State styling: selection wins (amber); otherwise planned nodes get
      // the dashed-indigo treatment from the logical view, normal nodes their
      // node / view / customer color (sky-blue by default). Planned markers
      // are also dimmed slightly. The glow follows the border color; the
      // at-risk effect below applies it.
      const planned = !!(n as any).is_planned;
      const dotEl = marker.getElement().firstChild as HTMLDivElement | null;
      if (dotEl) {
        const selected = selectedNodeId === n.id;
        const border = selected ? '#f59e0b' : (planned ? '#818cf8' : (nodeColors?.get(n.id) || DEFAULT_MARKER_COLOR));
        dotEl.dataset.color = border;  // glow color, applied by the at-risk effect
        dotEl.style.borderColor = border;
        dotEl.style.borderStyle = planned ? 'dashed' : 'solid';
        applySatGlyph(dotEl, n.role);
      }
      // Via setOpacity (not style.opacity) so MapLibre keeps far-side markers hidden.
      marker.setOpacity(planned ? '0.7' : '1', 0);
    }
    // Remove markers for nodes that disappeared or lost their position.
    for (const [id, marker] of markersRef.current) {
      if (!seen.has(id)) { marker.remove(); markersRef.current.delete(id); }
    }
    // Refresh link geometry + highlight state.
    const src = map.getSource('topo-links') as maplibregl.GeoJSONSource | undefined;
    if (src) src.setData(linksGeoJSON(nodes, links, highlightedLinkIds, hiddenRoles, hiddenNodeIds, satShownNow(highlightedLinkIds)));
    // Apply far-side occlusion to the current marker set.
    applyOcclusion();
  }, [nodes, links, mapReady, active, selectedNodeId, highlightedLinkIds, hiddenRoles, hiddenNodeIds, geoEditMode, pbTool, canEdit, setSelectedNode, setSidePanel, updateNode, applyOcclusion, showSatelliteLinks, satFocusKey, nodeColors]);

  // Marker glow: a red ring for nodes inside an active weather-alert area,
  // otherwise a soft glow in the marker's border color (set by the marker
  // sync above, which runs first).
  useEffect(() => {
    if (!mapReady) return;
    for (const [id, marker] of markersRef.current) {
      const dot = marker.getElement().firstChild as HTMLDivElement | null;
      if (!dot) continue;
      dot.style.boxShadow = atRiskNodeIds.has(id)
        ? '0 0 0 3px rgba(239,68,68,0.95), 0 0 10px rgba(239,68,68,0.9)'
        : markerGlow(dot.dataset.color || DEFAULT_MARKER_COLOR);
    }
  }, [atRiskNodeIds, nodes, mapReady, nodeColors, selectedNodeId]);

  // Keep occlusion in sync as the globe rotates/zooms.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    map.on('move', applyOcclusion);
    applyOcclusion();
    return () => { map.off('move', applyOcclusion); };
  }, [mapReady, applyOcclusion]);

  // Navigation hotkeys ("?" and "o" are handled by TopologyEditor). MapLibre's
  // built-in keyboard handler only works with the canvas focused, so it's
  // replaced by a window-level one that works whenever no field has focus.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !active) return;
    map.keyboard.disable();
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target)) return;
      const k = e.key;
      if (k === 'e' || k === 'E') {
        // Step through: enter edit mode (protected) → edit positions on → off.
        e.preventDefault();
        const st = useStore.getState();
        if (!st.canEditCurrentTopology()) {
          st.beginEditSession()
            .then(() => toast.success('Edit mode on'))
            .catch((err: any) => toast.error(editSessionErrorMessage(err)));
        } else {
          const on = !geoEditModeRef.current;
          setGeoEditMode(on);
          toast(on ? 'Edit positions on' : 'Edit positions off', { id: 'geo-edit-positions' });
        }
        return;
      }
      // S snaps the selected node to the location-search pin while editing
      // positions; otherwise it falls through to pan down (WASD).
      if ((k === 's' || k === 'S') && !e.shiftKey && snapToPinRef.current()) {
        e.preventDefault();
        return;
      }
      const dir = panDirection(k);
      if (dir && e.shiftKey && k.startsWith('Arrow')) {
        // Shift+left/right rotates the bearing; Shift+up/down tilts the camera.
        e.preventDefault();
        if (dir[0]) map.easeTo({ bearing: map.getBearing() - dir[0] * 15, duration: 300 });
        else map.easeTo({ pitch: Math.max(0, Math.min(map.getMaxPitch(), map.getPitch() - dir[1] * 10)), duration: 300 });
      } else if (dir) {
        e.preventDefault();
        map.panBy([dir[0] * 150, dir[1] * 150], { duration: 200 });
      } else if (k === 'PageUp' || k === 'PageDown') {
        e.preventDefault();
        const sign = k === 'PageUp' ? 1 : -1;
        // Shift = fine-grained camera altitude steps; plain = a full zoom level.
        map.easeTo({ zoom: map.getZoom() + sign * (e.shiftKey ? 0.25 : 1), duration: 250 });
      } else if (k === ' ') {
        // Leave Space alone on a focused button/checkbox so it still activates.
        if (isActivationTarget(e.target)) return;
        e.preventDefault();
        map.stop();
      } else if (k === 'n' || k === 'N') {
        e.preventDefault(); map.easeTo({ bearing: 0, duration: 400 });
      } else if (k === 'u' || k === 'U') {
        e.preventDefault(); map.easeTo({ pitch: 0, duration: 400 });
      } else if (k === 'r' || k === 'R') {
        e.preventDefault(); map.flyTo({ center: [-60, 25], zoom: 1.6, bearing: 0, pitch: 0, duration: 1200 });
      } else if (k === '/') {
        e.preventDefault(); setNodeSearchOpen(true);
      } else if (k === 'l' || k === 'L') {
        e.preventDefault(); geoInputRef.current?.focus(); geoInputRef.current?.select();
      }
    };
    window.addEventListener('keydown', onKey);
    // Right double-click zooms out around the cursor (left double-click zoom-in
    // is MapLibre's built-in). Capture phase so the second right-click doesn't
    // also open the link route menu. Markers keep their own right-click (hide).
    const el = map.getContainer();
    const isRightDouble = makeRightDoubleClickDetector();
    const onCtx = (e: MouseEvent) => {
      if ((e.target as HTMLElement)?.closest?.('.maplibregl-marker, .maplibregl-popup')) return;
      if (!isRightDouble(e)) return;
      e.preventDefault();
      e.stopPropagation();
      if (routePopupRef.current) { routePopupRef.current.remove(); routePopupRef.current = null; }
      const b = el.getBoundingClientRect();
      map.easeTo({ zoom: map.getZoom() - 1, around: map.unproject([e.clientX - b.left, e.clientY - b.top]), duration: 250 });
    };
    el.addEventListener('contextmenu', onCtx, true);
    return () => {
      window.removeEventListener('keydown', onKey);
      el.removeEventListener('contextmenu', onCtx, true);
      map.keyboard.enable();
    };
  }, [mapReady, active]);

  // Re-measure when un-hidden; close the node search when hidden.
  useEffect(() => {
    if (active) mapRef.current?.resize();
    else setNodeSearchOpen(false);
  }, [active]);

  // Nodes actually on the map: placed, and not hidden by kind or individually.
  const shownNodes = useMemo(
    () => nodes.filter(n => nodePos(n) && !hiddenRoles.has(roleOf(n)) && !hiddenNodeIds.has(n.id)),
    [nodes, hiddenRoles, hiddenNodeIds],
  );
  const flyToNode = (n: NodeInstance) => {
    setSelectedNode(n.id);
    const pos = nodePos(n);
    const map = mapRef.current;
    if (pos && map) map.flyTo({ center: pos, zoom: Math.max(map.getZoom(), 6), duration: 2000, essential: true });
  };

  // Submarine-cable overlay: lazily fetch + add the layers the first time the
  // overlay is enabled (avoids pulling ~1 MB if it's never used), then just
  // toggle their visibility. Layers go beneath 'topo-links' so the user's own
  // topology stays prominent on top.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const setVis = (v: 'visible' | 'none') => {
      for (const id of ['subsea-cables', 'subsea-cables-hit', 'subsea-landings', 'subsea-hl-line', 'subsea-hl-ends']) {
        if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v);
      }
    };
    if (!showSubmarineCables) {
      setVis('none');
      cableHlRef.current.clear();
      (map.getSource('subsea-hl') as any)?.setData({ type: 'FeatureCollection', features: [] });
      return;
    }
    // Trust the "already loaded" guard only if the layers actually still exist
    // on the current map — a basemap-style reload (or a dev hot-reload) drops
    // all sources, so re-add rather than silently no-op.
    if (subseaLoadedRef.current && map.getSource('subsea-cables')) { setVis('visible'); return; }
    subseaLoadedRef.current = true;  // guard against double-fetch
    cableOwnersLoadedRef.current = false;  // re-enrich ownership on a (re)add
    api.getSubmarineCables().then(d => {
      if (!mapRef.current) return;
      cablesFCRef.current = d.cables;
      if (!map.getSource('subsea-cables')) {
        map.addSource('subsea-cables', { type: 'geojson', data: d.cables });
        map.addSource('subsea-landings', { type: 'geojson', data: d.landing_points });
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        map.addLayer({
          id: 'subsea-cables', type: 'line', source: 'subsea-cables',
          paint: { 'line-color': '#fb923c', 'line-width': 0.8, 'line-opacity': 0.5 },
        }, beneath);
        // Invisible wide hit-area over the thin cable line so it's easy to
        // click. Transparent (opacity 0) but still returned by hit-testing.
        map.addLayer({
          id: 'subsea-cables-hit', type: 'line', source: 'subsea-cables',
          paint: { 'line-color': '#000', 'line-width': 12, 'line-opacity': 0 },
        }, beneath);
        map.addLayer({
          id: 'subsea-landings', type: 'circle', source: 'subsea-landings',
          paint: {
            'circle-radius': 3, 'circle-color': '#fdba74', 'circle-opacity': 0.85,
            'circle-stroke-width': 0.6, 'circle-stroke-color': '#0b1018',
          },
        }, beneath);
        // Highlight overlay for clicked cable(s): the full route bright + both ends.
        map.addSource('subsea-hl', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addLayer({
          id: 'subsea-hl-line', type: 'line', source: 'subsea-hl', filter: ['!=', ['geometry-type'], 'Point'],
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': '#22d3ee', 'line-width': 3, 'line-opacity': 0.95 },
        }, beneath);
        map.addLayer({
          id: 'subsea-hl-ends', type: 'circle', source: 'subsea-hl', filter: ['==', ['geometry-type'], 'Point'],
          paint: { 'circle-radius': 5.5, 'circle-color': '#22d3ee', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#0b1018' },
        }, beneath);
        // Landing stations are cable chokepoints — make them clickable.
        map.on('click', 'subsea-landings', (e: any) => {
          const p = e.features?.[0]?.properties; if (!p) return;
          const name = p.name || p.label || 'Cable landing station';
          new maplibregl.Popup({ closeButton: true, offset: 8 })
            .setLngLat(e.lngLat)
            .setHTML(
              `<div style="font:12px/1.4 sans-serif;color:#0f172a">
                 <div style="font-weight:600">${name}</div>
                 <div style="color:#64748b;font-size:11px">Submarine cable landing station</div>
               </div>`,
            ).addTo(map);
        });
        map.on('mouseenter', 'subsea-landings', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'subsea-landings', () => { map.getCanvas().style.cursor = ''; });
        // Rebuild the highlight source from the selected-id set.
        const updateCableHighlight = () => {
          const fc = cablesFCRef.current; const sel = cableHlRef.current;
          const feats: any[] = [];
          if (fc?.features) for (const f of fc.features) {
            if (!sel.has(String(f.properties?.id))) continue;
            feats.push(f);
            for (const end of cableEndsOf(f.geometry)) feats.push({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: end } });
          }
          (map.getSource('subsea-hl') as any)?.setData({ type: 'FeatureCollection', features: feats });
        };
        // Left-click elsewhere clears the highlight (ctrl-click keeps building it).
        map.on('click', (e: any) => {
          if (!cableHlRef.current.size) return;
          if (e.originalEvent && (e.originalEvent.ctrlKey || e.originalEvent.metaKey)) return;
          const onCable = map.getLayer('subsea-cables-hit') && map.queryRenderedFeatures(e.point, { layers: ['subsea-cables-hit'] }).length > 0;
          if (!onCable) { cableHlRef.current.clear(); updateCableHighlight(); }
        });
        // Cable line click → highlight the cable (ctrl/⌘-click adds more) + show
        // owners/suppliers/year popup (single-select only, to avoid popup spam).
        map.on('click', 'subsea-cables-hit', (e: any) => {
          const p = e.features?.[0]?.properties; if (!p) return;
          const multi = !!(e.originalEvent && (e.originalEvent.ctrlKey || e.originalEvent.metaKey));
          const id = String(p.id);
          const sel = cableHlRef.current;
          if (multi) { sel.has(id) ? sel.delete(id) : sel.add(id); }
          else { sel.clear(); sel.add(id); }
          updateCableHighlight();
          if (multi) return;  // skip the info popup while multi-selecting
          const info = cableOwnersRef.current[p.id];
          const owners = info?.owners || p.owners || '';
          const suppliers = info?.suppliers || '';
          const rfs = info?.rfs || '';
          const km = info?.length_km || null;
          const lengthStr = info?.length || '';
          // Age from RFS year (negative ⇒ not yet in service / planned).
          const ry = info?.rfs_year || null;
          const nowYr = new Date().getFullYear();
          let ageStr = '';
          if (ry) {
            const a = nowYr - ry;
            ageStr = a > 0 ? `${a} yr old` : a === 0 ? 'in service this year' : `planned (${ry})`;
          }
          // Approx latency from length: fiber ≈ 200,000 km/s (≈⅔ c).
          let latStr = '';
          if (km && km > 0) latStr = `~${Math.round(km / 200)} ms one-way · ~${Math.round(km / 100)} ms RTT`;
          new maplibregl.Popup({ closeButton: true, offset: 6 })
            .setLngLat(e.lngLat)
            .setHTML(
              `<div style="font:12px/1.45 sans-serif;color:#0f172a;max-width:250px">
                 <div style="font-weight:600">${p.name || 'Submarine cable'}</div>
                 ${owners ? `<div style="margin-top:2px"><span style="color:#64748b">Owners:</span> ${owners}</div>` : ''}
                 ${suppliers ? `<div><span style="color:#64748b">Built by:</span> ${suppliers}</div>` : ''}
                 ${rfs ? `<div><span style="color:#64748b">Ready for service:</span> ${rfs}${ageStr ? ` <span style="color:#94a3b8">(${ageStr})</span>` : ''}</div>` : ''}
                 ${lengthStr ? `<div><span style="color:#64748b">Length:</span> ${lengthStr}</div>` : ''}
                 ${latStr ? `<div><span style="color:#64748b">Latency:</span> ${latStr}</div>
                 <div style="color:#94a3b8;font-size:10px;margin-top:1px">approx — fiber propagation only</div>` : ''}
               </div>`,
            ).addTo(map);
        });
        map.on('mouseenter', 'subsea-cables-hit', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'subsea-cables-hit', () => { map.getCanvas().style.cursor = ''; });
      }
      setVis('visible');
      // Lazily enrich with ownership: recolor cables by provider (Google etc.).
      if (!cableOwnersLoadedRef.current) {
        cableOwnersLoadedRef.current = true;
        api.getSubmarineCableOwners().then(o => {
          if (!mapRef.current) return;
          cableOwnersRef.current = o.owners || {};
          setCableProviders(o.providers || []);
          const fc = cablesFCRef.current;
          if (fc?.features) {
            for (const f of fc.features) {
              const info = o.owners?.[f.properties?.id];
              f.properties.provider = info?.provider || 'other';
              if (info?.owners) f.properties.owners = info.owners;
            }
            (map.getSource('subsea-cables') as any)?.setData(fc);
          }
          // Color/emphasis by provider — hyperscalers bright + wider, the
          // carrier/consortium majority dim + thin so the big owners pop.
          const colorExpr: any[] = ['match', ['get', 'provider']];
          for (const p of (o.providers || [])) { if (p.key !== 'other') colorExpr.push(p.key, p.color); }
          colorExpr.push('#64748b');
          if (map.getLayer('subsea-cables')) {
            map.setPaintProperty('subsea-cables', 'line-color', colorExpr as any);
            map.setPaintProperty('subsea-cables', 'line-width', ['match', ['get', 'provider'], 'other', 0.6, 1.6] as any);
            map.setPaintProperty('subsea-cables', 'line-opacity', ['match', ['get', 'provider'], 'other', 0.3, 0.95] as any);
          }
        }).catch(() => { cableOwnersLoadedRef.current = false; /* owners are optional; cables still show */ });
      }
    }).catch(e => {
      subseaLoadedRef.current = false;  // allow retry on next toggle
      setSubseaError(e?.message || String(e));
    });
  }, [showSubmarineCables, mapReady]);

  // Filter cables to a single owner-provider (legend click). null = show all.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer('subsea-cables')) return;
    const f = cableProviderFilter ? ['==', ['get', 'provider'], cableProviderFilter] : null;
    map.setFilter('subsea-cables', f as any);
    if (map.getLayer('subsea-cables-hit')) map.setFilter('subsea-cables-hit', f as any);
  }, [cableProviderFilter, cableProviders]);

  // Data-center overlay: lazy fetch PeeringDB facilities, render as circles
  // colored by operator (vendor), with a click popup + legend. Same
  // lazy/visibility pattern as the submarine cables, layered beneath topology.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const setVis = (v: 'visible' | 'none') => {
      if (map.getLayer('datacenters')) map.setLayoutProperty('datacenters', 'visibility', v);
    };
    if (!showDataCenters) { setVis('none'); return; }
    if (dcLoadedRef.current && map.getSource('datacenters')) { setVis('visible'); return; }
    dcLoadedRef.current = true;
    api.getDataCenters().then(d => {
      if (!mapRef.current) return;
      const { expr, legend } = dcColorExpr(d.operators || []);
      setDcLegend(legend);
      if (!map.getSource('datacenters')) {
        map.addSource('datacenters', { type: 'geojson', data: d.datacenters });
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        map.addLayer({
          id: 'datacenters', type: 'circle', source: 'datacenters',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 2.5, 6, 5],
            'circle-color': expr,
            'circle-opacity': 0.85,
            'circle-stroke-width': 0.6,
            'circle-stroke-color': '#0b1018',
          },
        }, beneath);
        // Click a facility → popup with name / operator / connectivity counts.
        map.on('click', 'datacenters', (e: any) => {
          const f = e.features?.[0];
          if (!f) return;
          const p = f.properties || {};
          const el = document.createElement('div');
          el.style.cssText = 'font:12px/1.4 sans-serif;color:#0f172a';
          el.innerHTML =
            `<div style="font-weight:600">${p.name || ''}</div>
             <div style="color:#475569">${p.operator || ''}</div>
             <div style="color:#64748b;font-size:11px">${p.city || ''}${p.country ? ', ' + p.country : ''}</div>
             <div style="color:#64748b;font-size:11px;margin:2px 0 6px">${p.net_count || 0} networks · ${p.ix_count || 0} IX</div>
             <button data-carriers style="cursor:pointer;font:600 11px sans-serif;color:#fff;background:#0ea5e9;border:none;border-radius:4px;padding:4px 8px">Who can I connect to here? →</button>`;
          const pop = new maplibregl.Popup({ closeButton: true, offset: 8 }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
          el.querySelector('[data-carriers]')?.addEventListener('click', () => {
            setCarrierLookup({ kind: 'facility', facId: p.id, facName: p.name, operator: p.operator, city: p.city, country: p.country });
            pop.remove();
          });
          // Clicking a facility also filters the map to that operator.
          if (p.operator) setDcFilter(p.operator);
        });
        map.on('mouseenter', 'datacenters', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'datacenters', () => { map.getCanvas().style.cursor = ''; });
      }
      setVis('visible');
    }).catch(e => {
      dcLoadedRef.current = false;
      setDcError(e?.message || String(e));
    });
  }, [showDataCenters, mapReady]);

  // Apply the active vendor filter to the data-center layer.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer('datacenters')) return;
    let filter: any = null;
    if (dcFilter === 'Other') {
      // Everything not covered by a named legend operator.
      const named = dcLegend.filter(l => l.name !== 'Other').map(l => l.name);
      filter = ['!', ['in', ['get', 'operator'], ['literal', named]]];
    } else if (dcFilter) {
      filter = ['==', ['get', 'operator'], dcFilter];
    }
    map.setFilter('datacenters', filter);
  }, [dcFilter, dcLegend, showDataCenters]);

  // IXP overlay: lazy-load Internet Exchange Points as circles sized by peer
  // count, beneath topology, with a click popup. Same pattern as data centers.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const setVis = (v: 'visible' | 'none') => {
      if (map.getLayer('ixps')) map.setLayoutProperty('ixps', 'visibility', v);
    };
    if (!showIXPs) { setVis('none'); return; }
    if (ixLoadedRef.current && map.getSource('ixps')) { setVis('visible'); return; }
    ixLoadedRef.current = true;
    api.getIXPs().then(d => {
      if (!mapRef.current) return;
      if (!map.getSource('ixps')) {
        map.addSource('ixps', { type: 'geojson', data: d.ixps });
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        map.addLayer({
          id: 'ixps', type: 'circle', source: 'ixps',
          paint: {
            // Radius scales with peer count (net_count) so the big exchanges stand out.
            'circle-radius': ['interpolate', ['linear'], ['get', 'net_count'], 0, 2.5, 100, 4.5, 800, 8],
            'circle-color': '#a3e635',
            'circle-opacity': 0.8,
            'circle-stroke-width': 0.6,
            'circle-stroke-color': '#0b1018',
          },
        }, beneath);
        map.on('click', 'ixps', (e: any) => {
          const f = e.features?.[0]; if (!f) return;
          const p = f.properties || {};
          const el = document.createElement('div');
          el.style.cssText = 'font:12px/1.4 sans-serif;color:#0f172a';
          el.innerHTML =
            `<div style="font-weight:600">${p.name || ''}</div>
             <div style="color:#64748b;font-size:11px">${p.city || ''}${p.country ? ', ' + p.country : ''}</div>
             <div style="color:#64748b;font-size:11px;margin:2px 0 6px">${p.net_count || 0} networks · ${p.fac_count || 0} facilities</div>
             <button data-members style="cursor:pointer;font:600 11px sans-serif;color:#fff;background:#0ea5e9;border:none;border-radius:4px;padding:4px 8px">Who can I peer with here? →</button>`;
          const pop = new maplibregl.Popup({ closeButton: true, offset: 8 }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
          el.querySelector('[data-members]')?.addEventListener('click', () => {
            setCarrierLookup({ kind: 'ix', ixId: Number(p.id), ixName: p.name, city: p.city, country: p.country });
            pop.remove();
          });
        });
        map.on('mouseenter', 'ixps', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'ixps', () => { map.getCanvas().style.cursor = ''; });
      }
      setVis('visible');
    }).catch(e => {
      ixLoadedRef.current = false;
      setIxError(e?.message || String(e));
    });
  }, [showIXPs, mapReady]);

  // Cloud-regions overlay: AWS/Azure/GCP regions as points colored by provider,
  // with per-provider legend toggle (via layer filter) + click popup.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const setVis = (v: 'visible' | 'none') => {
      if (map.getLayer('cloud-regions')) map.setLayoutProperty('cloud-regions', 'visibility', v);
    };
    if (!showCloudRegions) { setVis('none'); return; }
    if (cloudLoadedRef.current && map.getSource('cloud-regions')) { setVis('visible'); return; }
    cloudLoadedRef.current = true;
    api.getCloudRegions().then(d => {
      if (!mapRef.current) return;
      setCloudProviders(d.providers || []);
      if (!map.getSource('cloud-regions')) {
        map.addSource('cloud-regions', { type: 'geojson', data: d.cloud_regions });
        const colorExpr: any[] = ['match', ['get', 'provider']];
        for (const p of d.providers) colorExpr.push(p.key, p.color);
        colorExpr.push('#94a3b8');
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        map.addLayer({
          id: 'cloud-regions', type: 'circle', source: 'cloud-regions',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 3.5, 6, 6],
            'circle-color': colorExpr as any,
            'circle-opacity': 0.9,
            'circle-stroke-width': 1, 'circle-stroke-color': '#0b1018',
          },
        }, beneath);
        const labelOf: Record<string, string> = Object.fromEntries(d.providers.map(p => [p.key, p.label]));
        map.on('click', 'cloud-regions', (e: any) => {
          const p = e.features?.[0]?.properties; if (!p) return;
          new maplibregl.Popup({ closeButton: true, offset: 8 })
            .setLngLat(e.lngLat)
            .setHTML(
              `<div style="font:12px/1.4 sans-serif;color:#0f172a">
                 <div style="font-weight:600">${labelOf[p.provider] || p.provider} · ${p.name || ''}</div>
                 <div style="color:#475569;font-size:11px">${p.code || ''}</div>
                 <div style="color:#64748b;font-size:11px">${p.city || ''}${p.country ? ', ' + p.country : ''}</div>
               </div>`,
            ).addTo(map);
        });
        map.on('mouseenter', 'cloud-regions', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'cloud-regions', () => { map.getCanvas().style.cursor = ''; });
      }
      setVis('visible');
    }).catch(e => {
      cloudLoadedRef.current = false;
      setCloudError(e?.message || String(e));
    });
  }, [showCloudRegions, mapReady]);

  // Per-provider visibility for the cloud-regions layer.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer('cloud-regions')) return;
    const visible = cloudProviders.map(p => p.key).filter(k => !cloudHidden.has(k));
    map.setFilter('cloud-regions', ['in', ['get', 'provider'], ['literal', visible]]);
  }, [cloudHidden, cloudProviders, showCloudRegions]);

  // GEO coverage overlay: nested footprint-cap rings per operator. One fill
  // layer per ring level (outer→inner) so their opacities accumulate into a
  // nadir-strong, edge-faint gradient. Colored by operator. Beneath topology.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const layerIds = [...Array.from({ length: geoRingLevels }, (_, i) => `geo-cov-${i}`), 'geo-cov-outline'];
    const setVis = (v: 'visible' | 'none') => {
      for (const id of layerIds) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v);
    };
    if (!showGeoCoverage) { setVis('none'); return; }
    if (geoLoadedRef.current && map.getSource('geo-coverage')) { setVis('visible'); return; }
    geoLoadedRef.current = true;
    api.getGeoCoverage().then(d => {
      if (!mapRef.current) return;
      setGeoOperators(d.operators || []);
      setGeoRingLevels(d.ring_levels || 0);
      if (!map.getSource('geo-coverage')) {
        map.addSource('geo-coverage', { type: 'geojson', data: d.geo_coverage });
        const colorExpr: any[] = ['match', ['get', 'operator']];
        for (const o of d.operators) colorExpr.push(o.key, o.color);
        colorExpr.push('#94a3b8');
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        // One fill layer per ring level (outer 0 → inner), added so inner sits
        // on top. Opacities are kept LOW on purpose: a single operator often
        // has several satellites whose ~76° footprints overlap, and fills
        // compound where they stack — too high and the overlap saturates into
        // a solid blob that hides the map. Low fills keep the basemap readable
        // even in the overlap zone, while the nadir rings still read brighter.
        // (Set here, not from the baked 'strength' property, so it's tunable
        // without re-fetching CelesTrak data.)
        const RING_OPACITY = [0.05, 0.06, 0.08, 0.10, 0.12];
        for (let lvl = 0; lvl < d.ring_levels; lvl++) {
          const op = RING_OPACITY[lvl] ?? (0.05 + (0.07 * lvl) / Math.max(1, d.ring_levels - 1));
          map.addLayer({
            id: `geo-cov-${lvl}`, type: 'fill', source: 'geo-coverage',
            filter: ['==', ['get', 'level'], lvl],
            paint: { 'fill-color': colorExpr as any, 'fill-opacity': op },
          }, beneath);
        }
        // Coverage-boundary outline on the outer ring (level 0), so the edge of
        // each footprint is legible even though the fill is light — this is
        // what makes "it covers the Americas" actually readable.
        map.addLayer({
          id: 'geo-cov-outline', type: 'line', source: 'geo-coverage',
          filter: ['==', ['get', 'level'], 0],
          paint: { 'line-color': colorExpr as any, 'line-width': 1.2, 'line-opacity': 0.7 },
        }, beneath);
      }
      for (let lvl = 0; lvl < d.ring_levels; lvl++) {
        if (map.getLayer(`geo-cov-${lvl}`)) map.setLayoutProperty(`geo-cov-${lvl}`, 'visibility', 'visible');
      }
      if (map.getLayer('geo-cov-outline')) map.setLayoutProperty('geo-cov-outline', 'visibility', 'visible');
    }).catch(e => {
      geoLoadedRef.current = false;
      setGeoError(e?.message || String(e));
    });
  }, [showGeoCoverage, mapReady, geoRingLevels]);

  // Per-operator visibility for the GEO coverage rings (applies across levels).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const visible = geoOperators.map(o => o.key).filter(k => !geoHidden.has(k));
    for (let lvl = 0; lvl < geoRingLevels; lvl++) {
      if (map.getLayer(`geo-cov-${lvl}`)) {
        map.setFilter(`geo-cov-${lvl}`, ['all', ['==', ['get', 'level'], lvl], ['in', ['get', 'operator'], ['literal', visible]]]);
      }
    }
    if (map.getLayer('geo-cov-outline')) {
      map.setFilter('geo-cov-outline', ['all', ['==', ['get', 'level'], 0], ['in', ['get', 'operator'], ['literal', visible]]]);
    }
  }, [geoHidden, geoOperators, geoRingLevels, showGeoCoverage]);

  // Geographic latency estimate for the highlighted flow: sum great-circle
  // hops along its path and convert to a fiber RTT lower bound (light in
  // fiber ≈ 200 km/ms one-way). Shown as a badge; no external data.
  const latencyEstimate = useMemo(() => {
    const fp: any = highlightedFlowPaths;
    const path: number[] | undefined = fp?.paths?.[0];
    if (!path || path.length < 2) return null;
    const posById = new Map<number, [number, number]>();
    for (const n of nodes) { const p = nodePos(n); if (p) posById.set(n.id, p); }
    let km = 0;
    for (let i = 0; i < path.length - 1; i++) {
      const a = posById.get(path[i]); const b = posById.get(path[i + 1]);
      if (!a || !b) return null;
      km += greatCircleKm(a, b);
    }
    const oneWayMs = km / 200;
    return { km: Math.round(km), rttMs: Math.round(oneWayMs * 2), flowName: fp.flow_name as string | undefined };
  }, [highlightedFlowPaths, nodes]);

  // Weather-alerts overlay: active NWS severe/extreme warnings as polygons
  // colored by severity, beneath topology. Lazy-loaded, click → details.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const setVis = (v: 'visible' | 'none') => {
      for (const id of ['weather-alerts-fill', 'weather-alerts-line']) {
        if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v);
      }
    };
    if (!showWeatherAlerts) { setVis('none'); return; }
    if (weatherLoadedRef.current && map.getSource('weather-alerts')) { setVis('visible'); return; }
    weatherLoadedRef.current = true;
    api.getWeatherAlerts().then(d => {
      if (!mapRef.current) return;
      setWeatherSeverities(d.severities || []);
      setWeatherFeatures(d.alerts?.features || []);
      setWeatherActivity(d.activity || { active: 0, possible: 0 });
      const colorExpr: any[] = ['match', ['get', 'severity']];
      for (const s of (d.severities || [])) colorExpr.push(s.severity, s.color);
      colorExpr.push('#eab308');
      if (!map.getSource('weather-alerts')) {
        map.addSource('weather-alerts', { type: 'geojson', data: d.alerts });
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        map.addLayer({
          id: 'weather-alerts-fill', type: 'fill', source: 'weather-alerts',
          // Active (occurring) alerts read bolder; possible (anticipated) fainter.
          paint: { 'fill-color': colorExpr as any, 'fill-opacity': ['match', ['get', 'activity'], 'active', 0.34, 0.1] as any },
        }, beneath);
        map.addLayer({
          id: 'weather-alerts-line', type: 'line', source: 'weather-alerts',
          paint: {
            'line-color': colorExpr as any,
            'line-width': ['match', ['get', 'activity'], 'active', 1.8, 0.7] as any,
            'line-opacity': ['match', ['get', 'activity'], 'active', 0.95, 0.5] as any,
          },
        }, beneath);
        map.on('click', 'weather-alerts-fill', (e: any) => {
          const p = e.features?.[0]?.properties; if (!p) return;
          new maplibregl.Popup({ closeButton: true, offset: 6 })
            .setLngLat(e.lngLat)
            .setHTML(
              `<div style="font:12px/1.45 sans-serif;color:#0f172a;max-width:250px">
                 <div style="font-weight:600">${p.event || 'Weather alert'}</div>
                 <div><span style="color:#64748b">Severity:</span> ${p.severity || ''}</div>
                 ${p.areaDesc ? `<div><span style="color:#64748b">Area:</span> ${String(p.areaDesc).slice(0, 120)}</div>` : ''}
                 ${p.expires ? `<div><span style="color:#64748b">Expires:</span> ${new Date(p.expires).toLocaleString()}</div>` : ''}
                 ${p.sender ? `<div style="color:#64748b;font-size:10px;margin-top:2px">${p.sender}</div>` : ''}
               </div>`,
            ).addTo(map);
        });
        map.on('mouseenter', 'weather-alerts-fill', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'weather-alerts-fill', () => { map.getCanvas().style.cursor = ''; });
      }
      setVis('visible');
    }).catch(e => {
      weatherLoadedRef.current = false;
      setWeatherError(e?.message || String(e));
    });
  }, [showWeatherAlerts, mapReady]);

  // Active-fires overlay: NIFC wildfire perimeters (fill+outline) + incident
  // points, beneath topology. Lazy-loaded, click → incident details.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const ids = ['fire-perimeters-fill', 'fire-perimeters-line', 'fire-incidents'];
    const setVis = (v: 'visible' | 'none') => {
      for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v);
    };
    if (!showActiveFires) { setVis('none'); return; }
    if (firesLoadedRef.current && map.getSource('fire-perimeters')) { setVis('visible'); return; }
    firesLoadedRef.current = true;
    api.getActiveFires().then(d => {
      if (!mapRef.current) return;
      setFireCounts(d.counts || { perimeters: 0, incidents: 0 });
      const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
      if (!map.getSource('fire-perimeters')) {
        map.addSource('fire-perimeters', { type: 'geojson', data: d.perimeters });
        map.addSource('fire-incidents', { type: 'geojson', data: d.incidents });
        map.addLayer({
          id: 'fire-perimeters-fill', type: 'fill', source: 'fire-perimeters',
          paint: { 'fill-color': '#ef4444', 'fill-opacity': 0.35 },
        }, beneath);
        map.addLayer({
          id: 'fire-perimeters-line', type: 'line', source: 'fire-perimeters',
          paint: { 'line-color': '#dc2626', 'line-width': 1.2, 'line-opacity': 0.9 },
        }, beneath);
        map.addLayer({
          id: 'fire-incidents', type: 'circle', source: 'fire-incidents',
          paint: {
            'circle-radius': 3.5, 'circle-color': '#f97316', 'circle-opacity': 0.9,
            'circle-stroke-width': 0.6, 'circle-stroke-color': '#7c2d12',
          },
        }, beneath);
        const firePopup = (e: any) => {
          const p = e.features?.[0]?.properties; if (!p) return;
          const acres = p.acres != null && p.acres !== '' ? `${Math.round(Number(p.acres)).toLocaleString()} acres` : '';
          const disc = p.discovered ? new Date(Number(p.discovered) || p.discovered).toLocaleDateString() : '';
          new maplibregl.Popup({ closeButton: true, offset: 6 })
            .setLngLat(e.lngLat)
            .setHTML(
              `<div style="font:12px/1.45 sans-serif;color:#0f172a;max-width:240px">
                 <div style="font-weight:600">🔥 ${p.name || 'Wildfire'}</div>
                 ${acres ? `<div><span style="color:#64748b">Size:</span> ${acres}</div>` : ''}
                 ${p.cause ? `<div><span style="color:#64748b">Cause:</span> ${p.cause}</div>` : ''}
                 ${disc ? `<div><span style="color:#64748b">Discovered:</span> ${disc}</div>` : ''}
               </div>`,
            ).addTo(map);
        };
        for (const layer of ['fire-incidents', 'fire-perimeters-fill']) {
          map.on('click', layer, firePopup);
          map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
          map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
        }
      }
      setVis('visible');
    }).catch(e => {
      firesLoadedRef.current = false;
      setFiresError(e?.message || String(e));
    });
  }, [showActiveFires, mapReady]);

  // Earthquakes overlay: USGS recent quakes as circles sized/colored by magnitude.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const setVis = (v: 'visible' | 'none') => { if (map.getLayer('earthquakes')) map.setLayoutProperty('earthquakes', 'visibility', v); };
    if (!showEarthquakes) { setVis('none'); return; }
    if (quakeLoadedRef.current && map.getSource('earthquakes')) { setVis('visible'); return; }
    quakeLoadedRef.current = true;
    api.getEarthquakes().then(d => {
      if (!mapRef.current) return;
      setQuakeCount(d.count || 0);
      if (!map.getSource('earthquakes')) {
        map.addSource('earthquakes', { type: 'geojson', data: d.earthquakes });
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        map.addLayer({
          id: 'earthquakes', type: 'circle', source: 'earthquakes',
          paint: {
            // radius grows with magnitude; color ramps yellow→red.
            'circle-radius': ['interpolate', ['linear'], ['get', 'mag'], 2.5, 3, 6, 9, 8, 16],
            'circle-color': ['interpolate', ['linear'], ['get', 'mag'], 2.5, '#fde047', 5, '#f97316', 7, '#dc2626'],
            'circle-opacity': 0.55, 'circle-stroke-width': 0.8, 'circle-stroke-color': '#7f1d1d',
          },
        }, beneath);
        map.on('click', 'earthquakes', (e: any) => {
          const p = e.features?.[0]?.properties; if (!p) return;
          new maplibregl.Popup({ closeButton: true, offset: 6 }).setLngLat(e.lngLat).setHTML(
            `<div style="font:12px/1.45 sans-serif;color:#0f172a;max-width:230px">
               <div style="font-weight:600">M${p.mag} earthquake</div>
               <div>${p.place || ''}</div>
               ${p.depth_km != null ? `<div style="color:#64748b">Depth: ${Math.round(p.depth_km)} km</div>` : ''}
               ${p.time ? `<div style="color:#64748b">${new Date(Number(p.time)).toLocaleString()}</div>` : ''}
             </div>`).addTo(map);
        });
        map.on('mouseenter', 'earthquakes', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'earthquakes', () => { map.getCanvas().style.cursor = ''; });
      }
      setVis('visible');
    }).catch(e => { quakeLoadedRef.current = false; setQuakeError(e?.message || String(e)); });
  }, [showEarthquakes, mapReady]);

  // Tropical cyclones overlay: NHC active storm positions.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const setVis = (v: 'visible' | 'none') => { if (map.getLayer('cyclones')) map.setLayoutProperty('cyclones', 'visibility', v); };
    if (!showCyclones) { setVis('none'); return; }
    if (cycloneLoadedRef.current && map.getSource('cyclones')) { setVis('visible'); return; }
    cycloneLoadedRef.current = true;
    api.getCyclones().then(d => {
      if (!mapRef.current) return;
      setCycloneCount(d.count || 0);
      if (!map.getSource('cyclones')) {
        map.addSource('cyclones', { type: 'geojson', data: d.cyclones });
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        map.addLayer({
          id: 'cyclones', type: 'circle', source: 'cyclones',
          paint: { 'circle-radius': 7, 'circle-color': '#a855f7', 'circle-opacity': 0.7, 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' },
        }, beneath);
        map.on('click', 'cyclones', (e: any) => {
          const p = e.features?.[0]?.properties; if (!p) return;
          new maplibregl.Popup({ closeButton: true, offset: 6 }).setLngLat(e.lngLat).setHTML(
            `<div style="font:12px/1.45 sans-serif;color:#0f172a;max-width:230px">
               <div style="font-weight:600">🌀 ${p.name || 'Cyclone'}</div>
               ${p.classification ? `<div>${p.classification}${p.intensity_kt ? ` · ${p.intensity_kt} kt` : ''}</div>` : ''}
               ${p.pressure_mb ? `<div style="color:#64748b">Pressure: ${p.pressure_mb} mb</div>` : ''}
               ${p.movement ? `<div style="color:#64748b">Moving: ${p.movement}</div>` : ''}
             </div>`).addTo(map);
        });
        map.on('mouseenter', 'cyclones', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'cyclones', () => { map.getCanvas().style.cursor = ''; });
      }
      setVis('visible');
    }).catch(e => { cycloneLoadedRef.current = false; setCycloneError(e?.message || String(e)); });
  }, [showCyclones, mapReady]);

  // Internet-outages overlay: country-centroid markers (IODA), sized by severity.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const setVis = (v: 'visible' | 'none') => { for (const id of ['internet-outages', 'internet-outages-label']) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v); };
    if (!showInternetOutages) { setVis('none'); return; }
    if (outageLoadedRef.current && map.getSource('internet-outages')) { setVis('visible'); return; }
    outageLoadedRef.current = true;
    api.getInternetOutages().then(d => {
      if (!mapRef.current) return;
      setOutageCount(d.count || 0);
      if (!map.getSource('internet-outages')) {
        map.addSource('internet-outages', { type: 'geojson', data: d.outages });
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        map.addLayer({
          id: 'internet-outages', type: 'circle', source: 'internet-outages',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['get', 'score'], 0, 6, 5000, 12, 20000, 22],
            'circle-color': '#22d3ee', 'circle-opacity': 0.35, 'circle-stroke-width': 1.2, 'circle-stroke-color': '#0e7490',
          },
        }, beneath);
        map.on('click', 'internet-outages', (e: any) => {
          const p = e.features?.[0]?.properties; if (!p) return;
          new maplibregl.Popup({ closeButton: true, offset: 6 }).setLngLat(e.lngLat).setHTML(
            `<div style="font:12px/1.45 sans-serif;color:#0f172a;max-width:230px">
               <div style="font-weight:600">📡 ${p.country || ''}</div>
               <div style="color:#64748b">Internet-outage activity (last 24h)</div>
               <div>Severity score: ${p.score} · ${p.events} event${p.events === 1 ? '' : 's'}</div>
             </div>`).addTo(map);
        });
        map.on('mouseenter', 'internet-outages', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'internet-outages', () => { map.getCanvas().style.cursor = ''; });
      }
      setVis('visible');
    }).catch(e => { outageLoadedRef.current = false; setOutageError(e?.message || String(e)); });
  }, [showInternetOutages, mapReady]);

  // Coverage-strength overlay: per-constellation density "heat" by latitude.
  // Builds one fill source of latitude bands for all constellations (opacity ∝
  // that fleet's normalized sats-in-view); the selected constellation is shown
  // by filtering on `key`, so switching fleets is just a filter change.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const setVis = (v: 'visible' | 'none') => {
      if (map.getLayer('cov-strength-heat')) map.setLayoutProperty('cov-strength-heat', 'visibility', v);
    };
    if (!showCoverageStrength) {
      setVis('none');
      if (strengthPopupRef.current) { strengthPopupRef.current.remove(); strengthPopupRef.current = null; }
      return;
    }
    // Only trust the "loaded" guard if the layer still exists — a basemap-style
    // reload (or dev hot-reload) drops all sources, so re-add rather than
    // silently no-op (which would leave the heat map missing).
    if (strengthLoadedRef.current && map.getSource('cov-strength')) {
      if (map.getLayer('cov-strength-heat') && heatKey) map.setFilter('cov-strength-heat', ['==', ['get', 'key'], heatKey]);
      setVis('visible');
      return;
    }
    strengthLoadedRef.current = true;
    api.getCoverageStrength().then(d => {
      if (!mapRef.current) return;
      setStrengthData(d.constellations);
      const top = [...d.constellations].sort((a, b) => b.peak_in_view - a.peak_in_view)[0];
      const initialKey = top ? top.key : '';
      setHeatKey(k => k || initialKey);
      // Latitude-band polygons, opacity ∝ each fleet's own normalized density.
      const lons: number[] = [];
      for (let lo = -180; lo <= 180; lo += 10) lons.push(lo);
      const feats: any[] = [];
      for (const c of d.constellations) {
        const peak = c.peak_in_view || 1;
        const lats = c.by_latitude;
        for (let i = 0; i < lats.length - 1; i++) {
          const a = lats[i], b = lats[i + 1];
          const strength = Math.max(a.in_view, b.in_view) / peak;
          if (strength <= 0.01) continue;
          const ring = lons.map(lo => [lo, a.lat]).concat(lons.slice().reverse().map(lo => [lo, b.lat]));
          ring.push(ring[0]);
          feats.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: { key: c.key, color: c.color, strength: Math.min(1, strength) } });
        }
      }
      const fc = { type: 'FeatureCollection', features: feats } as any;
      if (!map.getSource('cov-strength')) {
        map.addSource('cov-strength', { type: 'geojson', data: fc });
        const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
        map.addLayer({
          id: 'cov-strength-heat', type: 'fill', source: 'cov-strength',
          filter: ['==', ['get', 'key'], heatKey || initialKey],
          paint: { 'fill-color': ['get', 'color'] as any, 'fill-opacity': ['*', ['get', 'strength'], 0.6] as any },
        }, beneath);
      } else {
        (map.getSource('cov-strength') as any).setData(fc);
      }
    }).catch(e => {
      strengthLoadedRef.current = false;
      setStrengthError(e?.message || String(e));
    });
  }, [showCoverageStrength, mapReady, strengthData, heatKey]);

  // Click-to-compare: click anywhere → popup ranking constellations by
  // estimated sats-in-view at that latitude. Registered once; no-ops unless
  // the coverage-strength overlay is active.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const onClick = (e: any) => {
      if (!(useStore.getState() as any).showCoverageStrength) return;
      // Don't show the sats-in-view readout when the click landed on an
      // interactive feature (a PoP, data center, alert, …) — that feature's own
      // popup handles it; only respond to bare basemap clicks.
      const interactive = ['operator-pops', 'datacenters', 'subsea-cables-hit', 'subsea-landings',
        'ixps', 'cloud-regions', 'weather-alerts-fill', 'fire-incidents', 'fire-perimeters-fill',
        'earthquakes', 'cyclones', 'internet-outages'].filter(id => map.getLayer(id));
      if (interactive.length && map.queryRenderedFeatures(e.point, { layers: interactive }).length) return;
      const data = strengthRef.current;
      if (!data.length) return;
      const lat = e.lngLat.lat;
      const ranked = data
        .map(c => ({ vendor: c.vendor, color: c.color, here: interpStrengthAt(c.by_latitude, lat) }))
        .sort((a, b) => b.here - a.here);
      const maxHere = Math.max(...ranked.map(r => r.here), 0.0001);
      const latLabel = `${Math.abs(lat).toFixed(1)}°${lat >= 0 ? 'N' : 'S'}`;
      const rows = ranked.map(r => {
        const w = Math.round(100 * r.here / maxHere);
        const val = r.here < 1 ? r.here.toFixed(1) : String(Math.round(r.here));
        return `<div style="display:flex;align-items:center;gap:6px;margin:3px 0">
          <span style="width:8px;height:8px;border-radius:9999px;background:${r.color};flex:none"></span>
          <span style="flex:1;color:#cbd5e1;font-size:11px">${r.vendor}</span>
          <span style="width:70px;height:6px;background:#1e293b;border-radius:3px;overflow:hidden;flex:none"><span style="display:block;height:100%;width:${w}%;background:${r.color}"></span></span>
          <span style="width:30px;text-align:right;color:#e2e8f0;font-size:11px;flex:none">${val}</span>
        </div>`;
      }).join('');
      const html = `<div style="min-width:215px">
        <div style="color:#f1f5f9;font-size:12px;font-weight:600">Satellites in view · ${latLabel}</div>
        <div style="color:#64748b;font-size:9px;margin:1px 0 5px">estimate ≥25° elevation · depends on latitude</div>
        ${rows}</div>`;
      if (strengthPopupRef.current) strengthPopupRef.current.remove();
      strengthPopupRef.current = new maplibregl.Popup({ closeButton: true, offset: 8 })
        .setLngLat(e.lngLat).setHTML(html).addTo(map);
    };
    map.on('click', onClick);
    return () => { map.off('click', onClick); };
  }, [mapReady]);

  // Connectivity finder: when active, a click anywhere opens the carrier panel
  // for that point (nearest hubs). Registered once; gated on finderModeRef.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const onClick = (e: any) => {
      if (!finderModeRef.current) return;
      setCarrierLookup({ kind: 'point', lat: e.lngLat.lat, lon: e.lngLat.lng });
    };
    map.on('click', onClick);
    return () => { map.off('click', onClick); };
  }, [mapReady]);

  // Crosshair cursor while the finder is armed.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    map.getCanvas().style.cursor = finderMode ? 'crosshair' : '';
  }, [finderMode, mapReady]);

  // Right-click a link → menu to attach a fiber route (KMZ) it should follow:
  // pick an already-imported overlay, import a new file, or clear the route.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const onCtx = (e: any) => {
      e.preventDefault?.();
      // The route menu only edits (stitch / import / clear) — none of it
      // applies to a read-only (protected, not editing) topology.
      if (!canEditRef.current) return;
      const p = e.features?.[0]?.properties; if (!p) return;
      const linkId = p.id;
      const link = linksRef.current.find((l: any) => l.id === linkId);
      if (link && isSatelliteLink(link)) return;  // satellite links have no fiber route
      const aN = nodesRef.current.find(n => n.id === link?.source_node_id);
      const zN = nodesRef.current.find(n => n.id === link?.target_node_id);
      const el = document.createElement('div');
      el.style.cssText = 'font:12px/1.4 sans-serif;color:#0f172a;min-width:200px;max-height:320px;overflow:auto';
      const btn = 'display:block;width:100%;text-align:left;cursor:pointer;border-radius:4px;padding:4px 6px;margin-bottom:3px;font:11px sans-serif';
      const nodePt = (n: any): [number, number] | null =>
        n ? [n.geo_longitude ?? n.longitude, n.geo_latitude ?? n.latitude] : null;
      const aPt = nodePt(aN), zPt = nodePt(zN);
      // Best-orientation total gap (km) from a segment's two ends to this link's nodes.
      const fitKm = (coords: [number, number][]): number | null => {
        if (!aPt || !zPt) return null;
        const s = coords[0], e2 = coords[coords.length - 1];
        return Math.min(haversineKm(aPt, s) + haversineKm(zPt, e2), haversineKm(aPt, e2) + haversineKm(zPt, s));
      };
      // Replace any prior route menu so only one is ever open.
      if (routePopupRef.current) { routePopupRef.current.remove(); routePopupRef.current = null; }
      const pop = new maplibregl.Popup({ closeButton: true, offset: 6 }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
      routePopupRef.current = pop;
      pop.on('close', () => { if (routePopupRef.current === pop) routePopupRef.current = null; });
      const fmtGap = (km: number) => km < 1 ? `${Math.round(km * 1000)} m` : `${Math.round(km)} km`;
      const ovName = (id: string) => kmzLayersRef.current.find(o => o.id === id)?.name || 'Overlay';
      // Folder tree for an overlay (synthesize a flat node for pre-tree imports).
      const overlayNode = (id: string): KmlNode => {
        const t = kmzTreeRef.current.get(id);
        if (t) { if (t.name === '(root)') t.name = ovName(id); return t; }
        const fc = kmzDataRef.current.get(id);
        const segs = fc ? lineStringsOf(fc) : [];
        return { id: 'r', name: ovName(id), folders: [], lines: segs.map(s => ({ name: s.name, coords: s.coords })), lineCount: segs.length, bbox: null };
      };
      // Result screen after a stitch.
      const renderResult = (title: string, lines: string[]) => {
        el.innerHTML = `<div style="font-weight:600;margin-bottom:3px">${title}</div>`
          + lines.map(l => `<div style="color:#475569;font-size:10px">${l}</div>`).join('')
          + `<button data-done style="${btn};margin-top:5px;color:#fff;background:#16a34a;border:none;font-weight:600;text-align:center">Done</button>`;
        el.querySelector('[data-done]')?.addEventListener('click', () => pop.remove());
      };
      // Use the folder's lines AS-IS: keep every part in order as a multi-part
      // route (gaps preserved, no connector). Right for an already-clean path
      // (e.g. a simplified KMZ) — never re-bridges what you cleaned up.
      const doUseAsIs = (node: KmlNode) => {
        const parts = collectKmlLines(node).filter(p => p.length >= 2).map(p => p.map(c => [c[0], c[1]] as [number, number]));
        if (!parts.length) { setKmzError('No line geometry in that folder'); pop.remove(); return; }
        updateLink(linkId, { geo_path: parts.length === 1 ? parts[0] : parts });
        renderResult(`Assigned as-is · ${node.name}`, [`${parts.length} part(s) kept in order · gaps preserved`]);
      };
      // Stitch every fragment under a folder into one ordered route, assign it.
      // bridge: true → connect across gaps (so the route spans the full A→Z path,
      // e.g. through ATL) instead of stopping at the first >2 km gap; only drop
      // fragments farther than maxBridgeKm (likely strays).
      const doStitch = (node: KmlNode) => {
        const res = stitchFragments(collectKmlLines(node), aPt ?? zPt, { bridge: true, maxBridgeKm: 200 });
        if (!res || res.coords.length < 2) { setKmzError('Could not stitch a route from that folder'); pop.remove(); return; }
        updateLink(linkId, { geo_path: res.coords });
        const gapA = aPt ? haversineKm(aPt, res.coords[0]) : null;
        const gapZ = zPt ? haversineKm(zPt, res.coords[res.coords.length - 1]) : null;
        const dropped = res.totalFrags - res.usedCount;
        renderResult(`Stitched · ${node.name}`, [
          `${res.usedCount}/${res.totalFrags} segments · ${Math.round(res.lengthKm)} km`,
          gapA != null && gapZ != null ? `ends ${fmtGap(gapA)} / ${fmtGap(gapZ)} from nodes` : 'assigned to link',
          res.bridges > 0 ? `${res.bridges} connector(s) up to ${Math.round(res.bridgedKm)} km` : '',
          dropped > 0 ? `⚠ ${dropped} fragment(s) >200 km away skipped` : '',
        ].filter(Boolean));
      };
      // Level 2: browse the overlay's folder tree → stitch a folder, drill in,
      // or pick a single segment. Subfolders are ranked by proximity to the link.
      const renderBrowser = (node: KmlNode, parents: KmlNode[]) => {
        const folders = node.folders.slice().sort((a, b) => {
          const sa = a.bbox && aPt && zPt ? distPtBbox(aPt, a.bbox) + distPtBbox(zPt, a.bbox) : Infinity;
          const sb = b.bbox && aPt && zPt ? distPtBbox(aPt, b.bbox) + distPtBbox(zPt, b.bbox) : Infinity;
          return sa - sb;
        });
        let html = `<button data-back style="${btn};color:#0ea5e9;background:none;border:none;padding-left:0;font-weight:600">‹ back</button>`;
        html += `<div style="font-weight:600;margin-bottom:1px">${node.name}</div>`;
        html += `<div style="color:#64748b;font-size:10px;margin-bottom:4px">→ ${aN?.name || 'A'} ↔ ${zN?.name || 'Z'} · ${node.lineCount} segment(s)</div>`;
        if (node.lineCount >= 1)
          html += `<button data-asis style="${btn};color:#fff;background:#0ea5e9;border:none;font-weight:600">✓ Use as-is → assign (${node.lineCount} part(s), keep gaps)</button>`;
        if (node.lineCount >= 2)
          html += `<button data-stitch style="${btn};color:#fff;background:#7c3aed;border:none;font-weight:600">⤳ Stitch (reorder + bridge gaps)</button>`;
        if (folders.length)
          html += folders.map((f, i) => `<button data-fi="${i}" style="${btn};color:#0f172a;background:#f1f5f9;border:1px solid #cbd5e1">📁 ${f.name} <span style="color:#64748b">(${f.lineCount}) ›</span></button>`).join('');
        if (node.lines.length && node.lines.length <= 40) {
          const ranked = node.lines.map((l, idx) => {
            let len = 0; for (let k = 1; k < l.coords.length; k++) len += haversineKm(l.coords[k - 1], l.coords[k]);
            return { l, idx, len, fit: fitKm(l.coords) };
          }).sort((a, b) => (a.fit ?? Infinity) - (b.fit ?? Infinity));
          html += `<div style="color:#64748b;font-size:10px;margin:3px 0 2px">Or a single segment:</div>`;
          html += ranked.map(({ l, idx, len, fit }, rank) => {
            const fitTxt = fit == null ? '' : ` · fit ${fit < 1 ? '<1' : Math.round(fit)} km`;
            const best = rank === 0 && fit != null && fit < 50 ? ' <span style="color:#16a34a">✓</span>' : '';
            return `<button data-li="${idx}" style="${btn};color:#0f172a;background:#f8fafc;border:1px solid #e2e8f0">— ${l.name}${best}<br><span style="color:#64748b;font-size:10px">${Math.round(len)} km${fitTxt}</span></button>`;
          }).join('');
        } else if (node.lines.length > 40) {
          html += `<div style="color:#64748b;font-size:10px;margin-top:3px">${node.lines.length} fragments here — use Stitch above</div>`;
        }
        el.innerHTML = html;
        el.querySelector('[data-back]')?.addEventListener('click', () => {
          if (parents.length) renderBrowser(parents[parents.length - 1], parents.slice(0, -1));
          else renderTop();
        });
        el.querySelector('[data-asis]')?.addEventListener('click', () => doUseAsIs(node));
        el.querySelector('[data-stitch]')?.addEventListener('click', () => doStitch(node));
        el.querySelectorAll('[data-fi]').forEach(b => b.addEventListener('click', () => {
          renderBrowser(folders[parseInt((b as HTMLElement).dataset.fi!, 10)], [...parents, node]);
        }));
        el.querySelectorAll('[data-li]').forEach(b => b.addEventListener('click', () => {
          updateLink(linkId, { geo_path: node.lines[parseInt((b as HTMLElement).dataset.li!, 10)].coords }); pop.remove();
        }));
      };
      // Level 1: choose an imported overlay, import a new file, or clear the route.
      const renderTop = () => {
        let html = `<div style="font-weight:600;margin-bottom:4px">Fiber route · ${aN?.name || 'A'} ↔ ${zN?.name || 'Z'}</div>`;
        const overlays = kmzLayersRef.current;
        if (overlays.length) {
          html += `<div style="color:#64748b;font-size:10px;margin-bottom:2px">Use imported overlay:</div>`;
          html += overlays.map(o => `<button data-ov="${o.id}" style="${btn};color:#0f172a;background:#f1f5f9;border:1px solid #cbd5e1">📄 ${o.name} ›</button>`).join('');
        }
        html += `<button data-new style="${btn};color:#fff;background:#0ea5e9;border:none;font-weight:600">＋ Import new KMZ/KML…</button>`;
        if (link?.geo_path?.length) html += `<div style="border-top:1px solid #e2e8f0;margin:5px 0"></div><button data-clear style="${btn};color:#b91c1c;background:#fee2e2;border:none">✕ Clear route (straight line)</button>`;
        el.innerHTML = html;
        el.querySelectorAll('[data-ov]').forEach(b => b.addEventListener('click', () => renderBrowser(overlayNode((b as HTMLElement).dataset.ov!), [])));
        el.querySelector('[data-new]')?.addEventListener('click', () => { pendingLinkRef.current = linkId; linkRouteInputRef.current?.click(); pop.remove(); });
        el.querySelector('[data-clear]')?.addEventListener('click', () => { updateLink(linkId, { geo_path: [] }); pop.remove(); });
      };
      renderTop();
    };
    // Dismiss the menu when the user clicks/right-clicks anywhere that isn't a link.
    const dismiss = (e: any) => {
      if (!routePopupRef.current) return;
      const onLink = map.getLayer('topo-links-hit') &&
        map.queryRenderedFeatures(e.point, { layers: ['topo-links-hit'] }).length > 0;
      if (!onLink) { routePopupRef.current.remove(); routePopupRef.current = null; }
    };
    map.on('contextmenu', 'topo-links-hit', onCtx);
    map.on('contextmenu', dismiss);
    map.on('click', dismiss);
    map.on('mouseenter', 'topo-links-hit', () => { map.getCanvas().style.cursor = 'context-menu'; });
    map.on('mouseleave', 'topo-links-hit', () => { map.getCanvas().style.cursor = ''; });
    return () => {
      map.off('contextmenu', 'topo-links-hit', onCtx);
      map.off('contextmenu', dismiss);
      map.off('click', dismiss);
      if (routePopupRef.current) { routePopupRef.current.remove(); routePopupRef.current = null; }
    };
  }, [mapReady]);

  // Register the operator-PoP click/hover handlers once (they fire only when
  // the layer exists). Popup shows the facility/IX behind each PoP.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const onClick = (e: any) => {
      const p = e.features?.[0]?.properties; if (!p) return;
      // A facility PoP behaves like a data-center click: show the facility with
      // a "Who can I connect to here?" button that opens the carrier panel.
      if (p.kind === 'facility' && p.fac_id != null) {
        const el = document.createElement('div');
        el.style.cssText = 'font:12px/1.4 sans-serif;color:#0f172a';
        el.innerHTML =
          `<div style="font-weight:600">◆ ${p.name || ''}</div>
           ${p.operator ? `<div style="color:#475569">${p.operator}</div>` : ''}
           <div style="color:#64748b;font-size:11px">${p.city || ''}${p.country ? ', ' + p.country : ''}</div>
           <div style="color:#64748b;font-size:11px;margin:2px 0 6px">Interconnection PoP</div>
           <button data-carriers style="cursor:pointer;font:600 11px sans-serif;color:#fff;background:#0ea5e9;border:none;border-radius:4px;padding:4px 8px">Who can I connect to here? →</button>`;
        const pop = new maplibregl.Popup({ closeButton: true, offset: 6 }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
        el.querySelector('[data-carriers]')?.addEventListener('click', () => {
          setCarrierLookup({ kind: 'facility', facId: Number(p.fac_id), facName: p.name, operator: p.operator, city: p.city, country: p.country });
          pop.remove();
        });
        return;
      }
      // IX PoP → popup with a button that opens the exchange's member list.
      if (p.kind === 'ix' && p.ix_id != null) {
        const el = document.createElement('div');
        el.style.cssText = 'font:12px/1.4 sans-serif;color:#0f172a';
        el.innerHTML =
          `<div style="font-weight:600">⬡ ${p.name || ''}</div>
           <div style="color:#64748b;font-size:11px">Internet exchange${p.city ? ` · ${p.city}` : ''}${p.country ? ', ' + p.country : ''}</div>
           <div style="color:#64748b;font-size:11px;margin:2px 0 6px">Interconnection PoP</div>
           <button data-members style="cursor:pointer;font:600 11px sans-serif;color:#fff;background:#0ea5e9;border:none;border-radius:4px;padding:4px 8px">Who can I peer with here? →</button>`;
        const pop = new maplibregl.Popup({ closeButton: true, offset: 6 }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
        el.querySelector('[data-members]')?.addEventListener('click', () => {
          setCarrierLookup({ kind: 'ix', ixId: Number(p.ix_id), ixName: p.name, city: p.city, country: p.country });
          pop.remove();
        });
        return;
      }
      // Fallback simple popup.
      new maplibregl.Popup({ closeButton: true, offset: 6 }).setLngLat(e.lngLat).setHTML(
        `<div style="font:12px/1.45 sans-serif;color:#0f172a;max-width:230px">
           <div style="font-weight:600">${p.name || ''}</div>
           <div style="color:#64748b;font-size:10px;margin-top:1px">Interconnection PoP</div>
         </div>`).addTo(map);
    };
    map.on('click', 'operator-pops', onClick);
    map.on('mouseenter', 'operator-pops', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'operator-pops', () => { map.getCanvas().style.cursor = ''; });
    return () => { map.off('click', 'operator-pops', onClick); };
  }, [mapReady]);

  // Plot the selected satellite operators' PoPs (◆ facilities in the
  // operator's color, ⬡ IXPs amber) as one layer, so they stay visible over the
  // data-center / IXP overlays too.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const clear = () => {
      if (map.getLayer('operator-pops')) map.removeLayer('operator-pops');
      if (map.getSource('operator-pops')) map.removeSource('operator-pops');
    };
    if (!popKeys.length) { clear(); setOperatorPopsInfo([]); setOperatorPopsError(''); return; }
    let cancelled = false;
    // Each operator loads (and fails) on its own; loaded ones are cached so
    // toggling another operator doesn't refetch them.
    const missing = popKeys.filter(k => !popCacheRef.current.has(k));
    Promise.allSettled(missing.map(k => api.getOperatorPops(k).then(d => popCacheRef.current.set(k, d)))).then(results => {
      if (cancelled || !mapRef.current) return;
      const failed = results.flatMap((r, i) => r.status === 'rejected' ? [`${missing[i]}: ${(r.reason as any)?.message || r.reason}`] : []);
      setOperatorPopsError(failed.join(' · '));
      const loaded = popKeys.map(k => popCacheRef.current.get(k)).filter(Boolean) as any[];
      clear();
      if (!loaded.length) { setOperatorPopsInfo([]); return; }
      const features = loaded.flatMap(d => (d.pops?.features || []).map((f: any) => ({
        ...f, properties: { ...f.properties, op_color: d.color },
      })));
      map.addSource('operator-pops', { type: 'geojson', data: { type: 'FeatureCollection', features } });
      map.addLayer({
        id: 'operator-pops', type: 'circle', source: 'operator-pops',
        paint: {
          'circle-radius': ['match', ['get', 'kind'], 'ix', 5, 4] as any,
          'circle-color': ['match', ['get', 'kind'], 'ix', '#fbbf24', ['get', 'op_color']] as any,
          'circle-opacity': 0.9, 'circle-stroke-width': 1.4, 'circle-stroke-color': '#0b1018',
        },
      });
      setOperatorPopsInfo(loaded.map(d => ({ label: d.operator, facilities: d.counts.facilities, ixps: d.counts.ixps })));
    });
    return () => { cancelled = true; };
  }, [popKeys, mapReady]);

  // Render the interconnection path: great-circle hop lines + waypoint markers.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const clear = () => {
      for (const id of ['path-hops', 'path-waypoints']) if (map.getLayer(id)) map.removeLayer(id);
      for (const id of ['path-hops', 'path-waypoints']) if (map.getSource(id)) map.removeSource(id);
    };
    clear();
    if (!pathResult || !pathResult.paths?.length) return;
    const hopFeats: any[] = [];
    const wpFeats: any[] = [];
    const wpSeen = new Set<string>();
    const addWp = (loc: any, role: string) => {
      if (!loc?.lonlat) return;
      const k = loc.lonlat.join(',');
      if (wpSeen.has(k)) return; wpSeen.add(k);
      wpFeats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: loc.lonlat }, properties: { role, name: loc.name, city: loc.city, kind: loc.kind } });
    };
    pathResult.paths.forEach((p: any, pi: number) => {
      p.segments.forEach((s: any) => {
        if (s.from.lonlat && s.to.lonlat) {
          hopFeats.push({ type: 'Feature', geometry: { type: 'MultiLineString', coordinates: greatCircleSegments(s.from.lonlat, s.to.lonlat) },
            properties: { pi, carrier: s.carrier.name, asn: s.carrier.asn, type: s.carrier.type } });
        }
        addWp(s.from, pi === 0 ? 'mid' : 'alt'); addWp(s.to, pi === 0 ? 'mid' : 'alt');
      });
    });
    addWp(pathResult.a.snap, 'a'); addWp(pathResult.z.snap, 'z');
    const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
    const pathColor: any[] = ['match', ['get', 'pi']];
    PATH_PALETTE.forEach((c, i) => pathColor.push(i, c));
    pathColor.push('#94a3b8');
    map.addSource('path-hops', { type: 'geojson', data: { type: 'FeatureCollection', features: hopFeats } });
    map.addLayer({
      id: 'path-hops', type: 'line', source: 'path-hops',
      layout: { 'line-cap': 'round' },
      paint: {
        'line-color': pathColor as any,
        'line-width': ['match', ['get', 'pi'], 0, 3, 1.8] as any,
        'line-opacity': ['match', ['get', 'pi'], 0, 0.95, 0.65] as any,
      },
    }, beneath);
    map.addSource('path-waypoints', { type: 'geojson', data: { type: 'FeatureCollection', features: wpFeats } });
    map.addLayer({
      id: 'path-waypoints', type: 'circle', source: 'path-waypoints',
      paint: {
        'circle-radius': ['match', ['get', 'role'], 'a', 7, 'z', 7, 4] as any,
        'circle-color': ['match', ['get', 'role'], 'a', '#22d3ee', 'z', '#f43f5e', '#34d399'] as any,
        'circle-stroke-width': 1.5, 'circle-stroke-color': '#0b1018',
      },
    }, beneath);
    map.on('click', 'path-waypoints', (e: any) => {
      const p = e.features?.[0]?.properties; if (!p) return;
      new maplibregl.Popup({ closeButton: true, offset: 6 }).setLngLat(e.lngLat).setHTML(
        `<div style="font:12px/1.45 sans-serif;color:#0f172a"><div style="font-weight:600">${p.role === 'a' ? '◉ A · ' : p.role === 'z' ? '◉ Z · ' : ''}${p.name || ''}</div><div style="color:#64748b">${p.city || ''} · ${p.kind === 'ix' ? 'exchange' : 'facility'}</div></div>`).addTo(map);
    });
  }, [pathResult, mapReady]);

  // Re-orient the map to a geocoded place + drop a temporary pin.
  const goToPlace = (r: { display_name: string; lat: number; lon: number; bbox: [number, number, number, number] | null }) => {
    const map = mapRef.current;
    if (!map) return;
    setGeoOpen(false);
    // Hand focus back to the map so the navigation hotkeys work straight away.
    geoInputRef.current?.blur();
    // Slow, arcing flyTo to a regional overview (not street level) — enough to
    // see where it is and drop a node; the user zooms in for detail. We frame
    // the result's bbox but cap the zoom well out, so a precise address and a
    // city both settle at a comfortable "general area" scale.
    const FLY = { duration: 3600, curve: 1.5, essential: true };
    let center: [number, number] = [r.lon, r.lat];
    let zoom = 6.5;
    if (r.bbox) {
      const cam = map.cameraForBounds([[r.bbox[0], r.bbox[1]], [r.bbox[2], r.bbox[3]]], { padding: 120, maxZoom: 6.5 });
      if (cam && cam.center) {
        const c = cam.center as any;
        center = Array.isArray(c) ? [c[0], c[1]] : [c.lng, c.lat];
        zoom = Math.min(cam.zoom ?? 6.5, 6.5);
      }
    }
    map.flyTo({ center, zoom, ...FLY });
    if (searchMarkerRef.current) searchMarkerRef.current.remove();
    const el = document.createElement('div');
    el.style.cssText = 'width:16px;height:16px;border-radius:50%;background:#f43f5e;border:2px solid #fff;box-shadow:0 0 8px rgba(244,63,94,0.8);';
    el.title = r.display_name;
    searchMarkerRef.current = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat([r.lon, r.lat]).addTo(map);
    setHasPin(true);
  };

  const runGeocode = () => {
    const q = geoQuery.trim();
    if (!q) return;
    setGeoSearching(true); setGeoSearchError(''); setGeoOpen(false);
    api.geocode(q, 5).then(d => {
      setGeoResults(d.results);
      if (d.results.length === 1) goToPlace(d.results[0]);
      else if (d.results.length > 1) setGeoOpen(true);
      else setGeoSearchError('No matches found.');
    }).catch(e => setGeoSearchError(e?.message || String(e)))
      .finally(() => setGeoSearching(false));
  };

  const clearSearch = () => {
    if (searchMarkerRef.current) { searchMarkerRef.current.remove(); searchMarkerRef.current = null; }
    setHasPin(false); setGeoResults([]); setGeoOpen(false); setGeoSearchError('');
  };

  // Geocode A & Z, then find the heuristic carrier path between them.
  const findPath = () => {
    const a = pathA.trim(), z = pathZ.trim();
    if (!a || !z) return;
    setPathLoading(true); setPathError(''); setPathResult(null);
    Promise.all([api.geocode(a, 1), api.geocode(z, 1)]).then(([ra, rz]) => {
      const A = ra.results[0], Z = rz.results[0];
      if (!A) throw new Error(`Couldn't find "${a}"`);
      if (!Z) throw new Error(`Couldn't find "${z}"`);
      return api.getPath(A.lat, A.lon, Z.lat, Z.lon, pathCount);
    }).then(d => {
      setPathResult(d);
      // Frame the whole path.
      const map = mapRef.current;
      const pts: [number, number][] = [];
      if (d.a.snap.lonlat) pts.push(d.a.snap.lonlat);
      if (d.z.snap.lonlat) pts.push(d.z.snap.lonlat);
      for (const p of d.paths) for (const s of p.segments) { if (s.from.lonlat) pts.push(s.from.lonlat); if (s.to.lonlat) pts.push(s.to.lonlat); }
      if (map && pts.length) {
        const lons = pts.map(p => p[0]), lats = pts.map(p => p[1]);
        map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 80, maxZoom: 5, duration: 1500 });
      }
    }).catch(e => setPathError(e?.message || String(e)))
      .finally(() => setPathLoading(false));
  };

  const clearPath = () => { setPathResult(null); setPathError(''); };

  // Register a GeoJSON overlay on the map (source + line/point/fill layers +
  // popups), tracked in kmzLayers. Used by both file import and the Path Builder.
  const addOverlay = (name: string, fc: GeoJSON.FeatureCollection, tree: KmlNode | null, opts?: { fit?: boolean; color?: string }) => {
    const map = mapRef.current;
    if (!map) return null;
    if (!fc.features?.length) { setKmzError(`No geometry found in ${name}`); return null; }
    const color = opts?.color || '#f59e0b';
    const id = `kmz-${kmzCounterRef.current++}`;
    kmzDataRef.current.set(id, fc);                       // keep for link-route assignment
    if (tree) kmzTreeRef.current.set(id, tree);           // folder tree for the picker / stitching
    map.addSource(id, { type: 'geojson', data: fc, generateId: true });  // ids → Path Builder map-tracing
    const beneath = map.getLayer('topo-links') ? 'topo-links' : undefined;
    // Style from the KML's own simplestyle props where present, else default.
    map.addLayer({ id: `${id}-fill`, type: 'fill', source: id, filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': ['coalesce', ['get', 'fill'], color] as any, 'fill-opacity': 0.15 } }, beneath);
    map.addLayer({ id: `${id}-line`, type: 'line', source: id, filter: ['!=', ['geometry-type'], 'Point'],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ['coalesce', ['get', 'stroke'], color] as any, 'line-width': ['coalesce', ['get', 'stroke-width'], 2.2] as any, 'line-opacity': 0.9 } }, beneath);
    map.addLayer({ id: `${id}-point`, type: 'circle', source: id, filter: ['==', ['geometry-type'], 'Point'],
      paint: { 'circle-radius': 4, 'circle-color': ['coalesce', ['get', 'marker-color'], ['get', 'stroke'], color] as any, 'circle-stroke-width': 0.8, 'circle-stroke-color': '#0b1018' } }, beneath);
    const popup = (e: any) => {
      const p = e.features?.[0]?.properties; if (!p) return;
      new maplibregl.Popup({ closeButton: true, offset: 6 }).setLngLat(e.lngLat).setHTML(
        `<div style="font:12px/1.45 sans-serif;color:#0f172a;max-width:240px"><div style="font-weight:600">${p.name || name}</div>${p.description ? `<div style="color:#475569">${String(p.description).slice(0, 200)}</div>` : ''}</div>`).addTo(map);
    };
    for (const lid of [`${id}-line`, `${id}-point`, `${id}-fill`]) {
      map.on('click', lid, popup);
      map.on('mouseenter', lid, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', lid, () => { map.getCanvas().style.cursor = ''; });
    }
    if (opts?.fit !== false) { const bb = bboxOfGeoJSON(fc); if (bb) map.fitBounds([[bb[0], bb[1]], [bb[2], bb[3]]], { padding: 60, maxZoom: 12, duration: 1200 }); }
    setKmzLayers(prev => [...prev, { id, name, count: fc.features.length }]);
    return id;
  };

  // Import a KMZ/KML file and overlay its geometry (lines/points/polygons).
  const importKmz = (file: File) => {
    setKmzError('');
    parseKmzOrKml(file).then(({ fc, tree }) => addOverlay(file.name, fc, tree))
      .catch(e => setKmzError(`${file.name}: ${e?.message || e}`));
  };

  const removeKmz = (id: string) => {
    const map = mapRef.current;
    if (map) {
      for (const lid of [`${id}-line`, `${id}-point`, `${id}-fill`]) if (map.getLayer(lid)) map.removeLayer(lid);
      if (map.getSource(id)) map.removeSource(id);
    }
    kmzDataRef.current.delete(id);
    kmzTreeRef.current.delete(id);
    setKmzLayers(prev => prev.filter(l => l.id !== id));
    if (pbOverlayId === id) { setPbOverlayId(''); setPbSelected(new Set()); setPbExpanded(new Set()); }
  };

  // ---- Path Builder: curate a messy KMZ's folders into clean named routes ----
  const pbRoot = useMemo<KmlNode | null>(() => {
    if (!pbOverlayId) return null;
    const t = kmzTreeRef.current.get(pbOverlayId);
    if (t) return t;
    const fc = kmzDataRef.current.get(pbOverlayId);
    if (!fc) return null;
    const segs = lineStringsOf(fc);
    return { id: 'r', name: kmzLayersRef.current.find(o => o.id === pbOverlayId)?.name || 'Overlay', folders: [], lines: segs.map(s => ({ name: s.name, coords: s.coords })), lineCount: segs.length, bbox: null };
  }, [pbOverlayId]);
  const pbIndex = useMemo(() => pbRoot ? indexKmlNodes(pbRoot) : new Map<string, KmlNode>(), [pbRoot]);
  const pbIndexRef = useRef(pbIndex); pbIndexRef.current = pbIndex;
  // Working fragment set = map-traced fragments ∪ folder-tree selection, deduped
  // by geometry (so the same span picked both ways counts once). Reads refs so
  // map event handlers always see fresh selection.
  const combinedFrags = (): [number, number][][] => {
    const byKey = new Map<string, [number, number][]>();
    for (const c of pbTraceRef.current.values()) if (c.length >= 2) byKey.set(fragKey(c), c);
    const seen = new Set<unknown>();
    const add = (n: KmlNode) => { for (const l of n.lines) if (!seen.has(l.coords)) { seen.add(l.coords); if (l.coords.length >= 2) byKey.set(fragKey(l.coords), l.coords); } n.folders.forEach(add); };
    for (const id of pbSelectedRef.current) { const n = pbIndexRef.current.get(id); if (n) add(n); }
    return [...byKey.values()];
  };
  const pbSel = useMemo(() => {
    const frags = combinedFrags();
    let km = 0; for (const f of frags) for (let k = 1; k < f.length; k++) km += haversineKm(f[k - 1], f[k]);
    return { count: frags.length, km };
  }, [pbSelected, pbIndex, pbSelVersion]);
  // Draw the current selection bright over the (dim) overlay.
  const updatePbHighlight = () => {
    const src = mapRef.current?.getSource('pb-sel') as any; if (!src) return;
    const frags = combinedFrags();
    src.setData({ type: 'FeatureCollection', features: frags.map(c => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: c } })) });
  };
  // Add/erase overlay line fragments under the brush. Paints over a whole stroke
  // (a list of interpolated screen points) so a FAST drag can't skip fragments
  // between sparse mousemove samples — which would leave holes in the route and
  // make the stitched path stop short instead of going end to end.
  const pbPaintPoints = (points: { x: number; y: number }[]) => {
    const map = mapRef.current; const oid = pbOverlayIdRef.current; if (!map || !oid) return;
    const lid = `${oid}-line`; if (!map.getLayer(lid)) return;
    const r = pbBrushRef.current;
    const fc = kmzDataRef.current.get(oid);
    const erasing = pbToolRef.current === 'erase';
    let changed = false;
    for (const pt of points) {
      const box: [[number, number], [number, number]] = [[pt.x - r, pt.y - r], [pt.x + r, pt.y + r]];
      for (const f of map.queryRenderedFeatures(box, { layers: [lid] })) {
        const fid = f.id;
        if (typeof fid !== 'number') continue;
        if (erasing) {
          // remove every part of this feature
          for (const k of [...pbTraceRef.current.keys()]) if (k.startsWith(`${fid}:`)) { pbTraceRef.current.delete(k); changed = true; }
        } else {
          // Store each LineString PART of the feature separately (keyed fid:part).
          // A multi-part feature (re-imported GeometryCollection) must NOT be
          // joined into one line — that would draw a straight connector between
          // the parts. The stitch handles ordering/bridging across them.
          const parts = geomLineParts((fc?.features[fid] as any)?.geometry);
          const useParts = parts.length ? parts : geomLineParts((f as any).geometry);
          useParts.forEach((coords, k) => {
            const key = `${fid}:${k}`;
            if (!pbTraceRef.current.has(key) && coords.length >= 2) { pbTraceRef.current.set(key, coords.map(c => [c[0], c[1]] as [number, number])); changed = true; }
          });
        }
      }
    }
    if (changed) { updatePbHighlight(); setPbSelVersion(v => v + 1); }
  };
  // Interpolated screen points from the previous pointer position to the current
  // one (steps ≈ brush size) so a fast drag paints/erases continuously.
  const strokePoints = (cur: { x: number; y: number }): { x: number; y: number }[] => {
    const last = pbLastPtRef.current;
    if (!last) return [cur];
    const dx = cur.x - last.x, dy = cur.y - last.y, dist = Math.hypot(dx, dy);
    const step = Math.max(3, pbBrushRef.current * 1.2);
    const n = Math.min(150, Math.max(1, Math.ceil(dist / step)));
    const pts: { x: number; y: number }[] = [];
    for (let k = 1; k <= n; k++) pts.push({ x: last.x + (dx * k) / n, y: last.y + (dy * k) / n });
    return pts;
  };
  // Redraw the built path that's open for editing (amber, over everything).
  const updatePbEditHighlight = () => {
    const src = mapRef.current?.getSource('pb-edit') as any; if (!src) return;
    const idx = pbEditIdxRef.current; const path = idx != null ? pbPathsRef.current[idx] : null;
    const feats = path ? path.parts.filter(p => p.length >= 2).map(c => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: c } })) : [];
    src.setData({ type: 'FeatureCollection', features: feats });
  };
  // Erase mode while editing: remove the path edges under the brush, splitting
  // the polyline there. Cleanly deletes a bad straight connector (one long edge).
  const pbEraseEdges = (strokePts: { x: number; y: number }[]) => {
    const map = mapRef.current; const idx = pbEditIdxRef.current; if (!map || idx == null) return;
    const path = pbPathsRef.current[idx]; if (!path) return;
    const r = pbBrushRef.current;
    let changed = false;
    const newParts: [number, number][][] = [];
    for (const part of path.parts) {
      if (part.length < 2) continue;
      const scr = part.map(c => map.project(c as any));
      let cur: [number, number][] = [part[0]];
      for (let i = 1; i < part.length; i++) {
        let hit = false;
        for (const p of strokePts) { if (distPtSeg(p, scr[i - 1], scr[i]) <= r) { hit = true; break; } }
        if (hit) { changed = true; if (cur.length >= 2) newParts.push(cur); cur = [part[i]]; }
        else cur.push(part[i]);
      }
      if (cur.length >= 2) newParts.push(cur);
    }
    if (!changed) return;
    let km = 0; for (const pt of newParts) for (let k = 1; k < pt.length; k++) km += haversineKm(pt[k - 1], pt[k]);
    setPbPaths(prev => prev.map((p, j) => j === idx ? { ...p, parts: newParts, lengthKm: km } : p));
  };
  const pbToggleEdit = (i: number) => {
    setPbEditIdx(prev => prev === i ? null : i);
    setPbTool('erase'); setPbNote(prev => prev);
  };

  const openPathBuilder = () => {
    setPbOpen(true); setPbNote('');
    if (!pbOverlayId && kmzLayers.length) setPbOverlayId(kmzLayers[0].id);
  };
  const pbToggleExp = (id: string) => setPbExpanded(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; });
  const pbToggleSel = (id: string) => { setPbSelected(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; }); setPbSelVersion(v => v + 1); };
  const pbClearSel = () => { pbTraceRef.current.clear(); setPbSelected(new Set()); setPbSelVersion(v => v + 1); };
  const pbAddPath = () => {
    const frags = combinedFrags().filter(f => f.length >= 2);
    if (!frags.length) { setPbNote('Trace a route on the map (or check a folder) first.'); return; }
    let anchor = frags[0][0];
    for (const f of frags) for (const p of [f[0], f[f.length - 1]]) if (p[0] < anchor[0]) anchor = p;  // westmost → stable orientation
    // bridge: true → connect everything the user painted into ONE path, even
    // across gaps between separate groups (straight connector) — but never draw
    // a connector longer than the user's "max connector" limit (drops strays).
    const res = stitchFragments(frags, anchor, { bridge: true, maxBridgeKm: pbMaxConnector });
    if (!res || res.coords.length < 2) { setPbNote('Could not stitch a route from the selection.'); return; }
    const nm = pbName.trim() || `Path ${pbPaths.length + 1}`;
    setPbPaths(prev => [...prev, { name: nm, parts: [res.coords], lengthKm: res.lengthKm, used: res.usedCount, total: res.totalFrags }]);
    setPbName('');
    const dropped = res.totalFrags - res.usedCount;
    setPbNote(`Added “${nm}” · ${res.usedCount}/${res.totalFrags} segments · ${Math.round(res.lengthKm)} km`
      + (res.bridges > 0 ? ` · ${res.bridges} connector(s) up to ${Math.round(res.bridgedKm)} km` : '')
      + (dropped > 0 ? ` · dropped ${dropped} stray(s) >${pbMaxConnector} km away (raise “max connector” to include)` : '')
      + `. Selection kept.`);
  };
  const pbAddAsOverlay = () => {
    if (!pbPaths.length) return;
    const fc: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: pbPaths.map(p => {
        const parts = p.parts.filter(pt => pt.length >= 2);
        return {
          type: 'Feature', properties: { name: p.name, stroke: '#22d3ee', 'stroke-width': 3 },
          geometry: parts.length === 1 ? { type: 'LineString', coordinates: parts[0] } : { type: 'MultiLineString', coordinates: parts },
        } as GeoJSON.Feature;
      }),
    };
    const id = addOverlay(`Simplified · ${pbPaths.length} path(s)`, fc, null, { fit: false, color: '#22d3ee' });
    setPbNote(id ? `Added ${pbPaths.length} clean path(s) as an overlay — right-click a link to map them.` : 'Could not add overlay.');
  };
  const pbDownload = () => {
    if (!pbPaths.length) return;
    buildKmzBlob(pbPaths).then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = 'simplified-paths.kmz';
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    });
  };
  // Recursive folder-tree row for the Path Builder.
  const renderPbNode = (node: KmlNode, depth: number) => {
    const hasKids = node.folders.length > 0;
    const exp = pbExpanded.has(node.id);
    return (
      <div key={node.id}>
        <div className="flex items-center gap-1 text-[11px] py-[1px] hover:bg-surface-800/60 rounded" style={{ paddingLeft: depth * 12 }}>
          {hasKids
            ? <button onClick={() => pbToggleExp(node.id)} className="w-3.5 shrink-0 text-surface-400 hover:text-surface-200">{exp ? '▾' : '▸'}</button>
            : <span className="w-3.5 shrink-0" />}
          <input type="checkbox" checked={pbSelected.has(node.id)} onChange={() => pbToggleSel(node.id)} className="accent-cyan-500 shrink-0" />
          <span className="flex-1 truncate text-surface-300" title={node.name}>{hasKids ? '📁 ' : ''}{node.name}</span>
          <span className="text-surface-600 shrink-0 tabular-nums">{node.lineCount}</span>
        </div>
        {exp && hasKids && node.folders.map(f => renderPbNode(f, depth + 1))}
      </div>
    );
  };

  // Path Builder: highlight layer for the current selection (bright over the dim overlay).
  useEffect(() => {
    const map = mapRef.current; if (!map || !mapReady) return;
    if (!map.getSource('pb-sel')) map.addSource('pb-sel', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    if (!map.getLayer('pb-sel-line')) map.addLayer({ id: 'pb-sel-line', type: 'line', source: 'pb-sel', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#22d3ee', 'line-width': 5, 'line-opacity': 0.95 } });
    if (!map.getSource('pb-edit')) map.addSource('pb-edit', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    if (!map.getLayer('pb-edit-line')) map.addLayer({ id: 'pb-edit-line', type: 'line', source: 'pb-edit', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#f59e0b', 'line-width': 5, 'line-opacity': 1 } });
  }, [mapReady]);
  // Sync the highlight with the selection (and clear it when the panel is closed).
  useEffect(() => {
    const src = mapRef.current?.getSource('pb-sel') as any; if (!src) return;
    if (!pbOpen) { src.setData({ type: 'FeatureCollection', features: [] }); return; }
    updatePbHighlight();
  }, [pbOpen, pbOverlayId, pbSelected, pbSelVersion, mapReady]);
  // Sync the edit-path highlight (amber) with the path being edited.
  useEffect(() => {
    const src = mapRef.current?.getSource('pb-edit') as any; if (!src) return;
    if (!pbOpen) { src.setData({ type: 'FeatureCollection', features: [] }); return; }
    updatePbEditHighlight();
  }, [pbOpen, pbEditIdx, pbPaths, mapReady]);
  // Brush painting: with the brush/erase tool, mousedown/drag paints over the
  // source overlay; the brush-circle cursor follows the pointer.
  useEffect(() => {
    const map = mapRef.current; if (!map || !mapReady) return;
    const painting = () => pbToolRef.current === 'brush' || pbToolRef.current === 'erase';
    const moveCircle = (e: any) => {
      const c = pbBrushCircleRef.current; if (!c) return;
      if (painting()) { const r = pbBrushRef.current; c.style.display = 'block'; c.style.width = c.style.height = `${r * 2}px`; c.style.left = `${e.point.x}px`; c.style.top = `${e.point.y}px`; }
      else c.style.display = 'none';
    };
    const down = (e: any) => {
      if (pbEditIdxRef.current != null) {           // editing a built path: only erase acts (on the path)
        if (pbToolRef.current !== 'erase') return;
        pbPaintingRef.current = true; pbLastPtRef.current = e.point; pbEraseEdges([e.point]); return;
      }
      if (!painting()) return;
      pbPaintingRef.current = true; pbLastPtRef.current = e.point; pbPaintPoints([e.point]);
    };
    const move = (e: any) => {
      moveCircle(e);
      if (!pbPaintingRef.current) return;
      const pts = strokePoints(e.point);
      if (pbEditIdxRef.current != null) { if (pbToolRef.current === 'erase') pbEraseEdges(pts); }
      else if (painting()) pbPaintPoints(pts);
      pbLastPtRef.current = e.point;
    };
    const up = () => { pbPaintingRef.current = false; pbLastPtRef.current = null; };
    const leave = () => { const c = pbBrushCircleRef.current; if (c) c.style.display = 'none'; };
    map.on('mousedown', down); map.on('mousemove', move); map.on('mouseup', up);
    map.getCanvas().addEventListener('mouseout', leave);
    return () => { map.off('mousedown', down); map.off('mousemove', move); map.off('mouseup', up); map.getCanvas().removeEventListener('mouseout', leave); };
  }, [mapReady]);
  // Tool ↔ map interaction: brush/erase pause panning (drag paints); move re-enables
  // panning so you can reposition the globe; nodes go inert whenever a tool is on.
  useEffect(() => {
    const map = mapRef.current; if (!map || !mapReady) return;
    const tracing = pbTool !== 'off' && pbOpen;
    if (tracing && (pbTool === 'brush' || pbTool === 'erase')) { map.dragPan.disable(); map.getCanvas().style.cursor = 'none'; }
    else { map.dragPan.enable(); map.getCanvas().style.cursor = tracing ? 'grab' : (finderModeRef.current ? 'crosshair' : ''); }
    if (!tracing && pbBrushCircleRef.current) pbBrushCircleRef.current.style.display = 'none';
    applyOcclusion();  // refresh node click-through for the new tool state
  }, [pbTool, pbOpen, mapReady, applyOcclusion]);

  // Address-box dropdown: matching nodes while typing, then geocode results.
  type AddrItem = { kind: 'node'; node: NodeInstance } | { kind: 'place'; place: typeof geoResults[number] };
  const addrItems: AddrItem[] = [
    ...(addrFocused && geoQuery.trim() ? matchNodes(shownNodes, geoQuery, 5).map(node => ({ kind: 'node' as const, node })) : []),
    ...(geoOpen && geoResults.length > 1 ? geoResults.map(place => ({ kind: 'place' as const, place })) : []),
  ];

  return (
    <div className="relative w-full h-full bg-surface-950">
      <div ref={containerRef} className="absolute inset-0" />
      {/* Path Builder brush cursor — follows the pointer, sized to the brush. */}
      <div
        ref={pbBrushCircleRef}
        className="absolute z-30 rounded-full pointer-events-none -translate-x-1/2 -translate-y-1/2"
        style={{ display: 'none', border: `2px solid ${pbTool === 'erase' ? '#fb7185' : '#22d3ee'}`, background: pbTool === 'erase' ? 'rgba(251,113,133,0.12)' : 'rgba(34,211,238,0.12)' }}
      />
      {/* Hidden input for right-click "import new file" fiber-route assignment. */}
      <input
        ref={linkRouteInputRef} type="file" accept=".kmz,.kml" className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]; const lid = pendingLinkRef.current;
          if (f && lid != null) {
            parseKmzOrKml(f).then(({ fc }) => {
              // Assign the file's geometry AS-IS: keep all LineString parts in
              // file order as a multi-part route (gaps preserved, no connector
              // drawn). This includes every piece (e.g. MIA→ATL then ATL→DC) and
              // never re-bridges a cleaned-up path. Use "Stitch" for raw folders.
              const parts = (fc.features || []).flatMap(ft => geomLineParts(ft.geometry as any))
                .filter(p => p.length >= 2).map(p => p.map(c => [c[0], c[1]] as [number, number]));
              if (!parts.length) { setKmzError(`No line geometry in ${f.name}`); return; }
              updateLink(lid, { geo_path: parts.length === 1 ? parts[0] : parts });
            }).catch(err => setKmzError(`${f.name}: ${err?.message || err}`));
          }
          e.currentTarget.value = ''; pendingLinkRef.current = null;
        }}
      />
      {/* "/" node search — overlays the address box while open. */}
      {nodeSearchOpen && (
        <NodeSearchBox
          nodes={shownNodes}
          onClose={closeNodeSearch}
          onPick={n => { setNodeSearchOpen(false); flyToNode(n); }}
          emptyText="No matching nodes on the map"
          focusClass="focus:border-sky-500"
        />
      )}
      {/* Address / place search → re-orient the map. */}
      <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 w-[300px]">
        <div className="flex gap-1">
          <input
            ref={geoInputRef}
            value={geoQuery}
            onChange={e => { setGeoQuery(e.target.value); setGeoOpen(false); setAddrHl(-1); }}
            onFocus={() => setAddrFocused(true)}
            onBlur={() => setTimeout(() => setAddrFocused(false), 150)}
            onKeyDown={e => {
              // -1 = nothing highlighted, so plain Enter still runs the address lookup.
              if (moveHighlight(e, addrItems.length, setAddrHl)) return;
              if (e.key === 'Enter') {
                const it = addrItems[addrHl];
                if (it?.kind === 'node') { flyToNode(it.node); e.currentTarget.blur(); }
                else if (it?.kind === 'place') goToPlace(it.place);
                else runGeocode();
                setAddrHl(-1);
              }
              if (e.key === 'Escape') e.currentTarget.blur();
            }}
            placeholder="Search address, place or node…  ( L )"
            className="flex-1 text-[12px] bg-surface-900/95 border border-surface-700 rounded px-2.5 py-1.5 text-surface-100 placeholder-surface-500 shadow-lg backdrop-blur-sm focus:outline-none focus:border-sky-500"
          />
          <button
            onClick={runGeocode}
            disabled={geoSearching || !geoQuery.trim()}
            className="text-[12px] font-medium px-3 py-1.5 rounded bg-sky-600/90 border border-sky-500 text-white shadow-lg disabled:opacity-40 hover:bg-sky-500"
          >{geoSearching ? '…' : 'Go'}</button>
          {hasPin && (
            <button onClick={clearSearch} title="Clear search pin" className="text-[12px] px-2 py-1.5 rounded bg-surface-900/95 border border-surface-700 text-surface-300 shadow-lg hover:text-surface-100">×</button>
          )}
        </div>
        {geoSearchError && (
          <div className="mt-1 bg-surface-900/95 border border-surface-700 rounded px-2 py-1 text-[10px] text-amber-300 shadow-lg">{geoSearchError}</div>
        )}
        {addrItems.length > 0 && (
          <div className="mt-1 bg-surface-900/95 border border-surface-700 rounded shadow-lg overflow-hidden max-h-[280px] overflow-y-auto">
            {addrItems.map((it, i) => (
              <button
                key={it.kind === 'node' ? `n${it.node.id}` : `p${i}`}
                onMouseDown={e => e.preventDefault()}
                onMouseEnter={() => setAddrHl(i)}
                onClick={() => { if (it.kind === 'node') { flyToNode(it.node); geoInputRef.current?.blur(); } else goToPlace(it.place); setAddrHl(-1); }}
                className={`w-full text-left text-[11px] text-surface-200 px-2.5 py-1.5 border-b border-surface-800 last:border-0 ${i === addrHl ? 'bg-surface-700' : 'hover:bg-surface-800'}`}
              >{it.kind === 'node'
                ? <><span className="text-sky-400">● </span>{it.node.name}{it.node.role ? <span className="text-surface-500"> · {it.node.role}</span> : null}</>
                : it.place.display_name}</button>
            ))}
          </div>
        )}
      </div>
      {carrierLookup && <CarrierPanel lookup={carrierLookup} onClose={() => setCarrierLookup(null)} />}
      {/* Path Builder — docked panel (map stays interactive for tracing). */}
      {pbOpen && (
        <div className="absolute top-3 right-3 bottom-3 z-30 w-[300px] flex flex-col bg-surface-900/95 border border-surface-600 rounded-lg shadow-2xl backdrop-blur-sm">
          <div className="flex items-center justify-between px-3 py-2 border-b border-surface-700">
            <div className="text-sm font-semibold text-surface-100">🧬 Path Builder</div>
            <button onClick={() => { setPbOpen(false); setPbTool('off'); setPbEditIdx(null); }} className="text-surface-500 hover:text-surface-200 text-lg leading-none">×</button>
          </div>
          <div className="px-3 py-2 border-b border-surface-700">
            <label className="text-[10px] uppercase tracking-wide text-surface-500">Source overlay</label>
            <select
              value={pbOverlayId}
              onChange={e => { setPbOverlayId(e.target.value); pbTraceRef.current.clear(); setPbSelected(new Set()); setPbExpanded(new Set()); setPbSelVersion(v => v + 1); setPbNote(''); }}
              className="w-full mt-0.5 text-[11px] bg-surface-950 border border-surface-700 rounded px-2 py-1 text-surface-200 focus:outline-none focus:border-cyan-500"
            >
              {kmzLayers.map(l => <option key={l.id} value={l.id}>{l.name} ({l.count})</option>)}
            </select>
          </div>
          {/* Tools — paint the KMZ lines like an art canvas. */}
          <div className="px-3 py-2 border-b border-surface-700 space-y-1.5">
            <div className="grid grid-cols-3 gap-1">
              {([['brush', '🖌 Brush'], ['erase', '🧽 Erase'], ['move', '✋ Move']] as const).map(([t, label]) => (
                <button key={t}
                  onClick={() => setPbTool(prev => prev === t ? 'off' : t)}
                  className={`text-[11px] font-medium py-1.5 rounded border ${pbTool === t
                    ? (t === 'erase' ? 'bg-rose-600 border-rose-500 text-white' : t === 'move' ? 'bg-amber-600 border-amber-500 text-white' : 'bg-cyan-600 border-cyan-500 text-white')
                    : 'bg-surface-800 border-surface-600 text-surface-300 hover:bg-surface-700'}`}
                >{label}</button>
              ))}
            </div>
            {(pbTool === 'brush' || pbTool === 'erase') && (
              <label className="flex items-center gap-2 text-[10px] text-surface-400">
                <span className="shrink-0">Brush</span>
                <input type="range" min={4} max={60} value={pbBrush} onChange={e => setPbBrush(Number(e.target.value))} className="flex-1 accent-cyan-500" />
                <span className="w-7 text-right tabular-nums text-surface-300">{pbBrush}px</span>
              </label>
            )}
            <div className="text-[10px] text-surface-500 leading-snug">
              {pbTool === 'off' && 'Pick 🖌 Brush, then drag over the KMZ lines to paint a path. Nodes are inert while a tool is active.'}
              {pbTool === 'brush' && 'Drag over the route — lines under the brush turn cyan + are selected. Use ✋ Move to reposition the globe.'}
              {pbTool === 'erase' && 'Drag over selected lines to remove them.'}
              {pbTool === 'move' && 'Drag to move the globe. Switch back to 🖌 Brush to keep painting.'}
            </div>
            <div className="flex items-center justify-between text-[10px] text-surface-400">
              <span>Selected: <span className="text-cyan-300">{pbSel.count}</span> seg · <span className="text-surface-200">{Math.round(pbSel.km)}</span> km</span>
              {(pbSel.count > 0) && <button onClick={pbClearSel} className="text-cyan-400 hover:underline">clear</button>}
            </div>
          </div>
          {/* Build a path from the current selection */}
          <div className="px-3 py-2 border-b border-surface-700 space-y-1.5">
            <input
              value={pbName} onChange={e => setPbName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') pbAddPath(); }}
              placeholder="Path name (optional)…"
              className="w-full text-[11px] bg-surface-950 border border-surface-700 rounded px-2 py-1 text-surface-200 placeholder-surface-600 focus:outline-none focus:border-cyan-500"
            />
            <label className="flex items-center gap-2 text-[10px] text-surface-400" title="The longest straight line the stitch may draw to join gaps. Fragments farther than this are dropped (reported), not connected — kills stray straight-line artifacts.">
              <span className="shrink-0">Max connector</span>
              <input type="range" min={0} max={300} step={5} value={pbMaxConnector} onChange={e => setPbMaxConnector(Number(e.target.value))} className="flex-1 accent-violet-500" />
              <span className="w-12 text-right tabular-nums text-surface-300">{pbMaxConnector} km</span>
            </label>
            <button onClick={pbAddPath} disabled={pbSel.count === 0}
              className="w-full text-[11px] font-semibold py-1.5 rounded bg-violet-600 border border-violet-500 text-white disabled:opacity-40 hover:bg-violet-500">⤳ Stitch selection into a path</button>
            {pbNote && <div className="text-[10px] text-surface-400 leading-snug">{pbNote}</div>}
          </div>
          {/* Collected paths */}
          <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-surface-500">Built paths ({pbPaths.length})</div>
          {pbEditIdx != null && pbPaths[pbEditIdx] && (
            <div className="mx-2 mb-1 px-2 py-1 rounded bg-amber-900/40 border border-amber-700 text-[10px] text-amber-200 leading-snug">
              Editing <b>{pbPaths[pbEditIdx].name}</b> — pick 🧽 Erase and drag across an artifact (e.g. a straight connector) to delete that edge; the line splits there.
              <button onClick={() => setPbEditIdx(null)} className="ml-1 underline text-amber-100">Done</button>
            </div>
          )}
          <div className="flex-1 min-h-0 overflow-auto px-2 pb-2 space-y-1">
            {pbPaths.length === 0 && <div className="text-[11px] text-surface-500 px-1">None yet — trace a route, then stitch it.</div>}
            {pbPaths.map((p, i) => {
              const editing = pbEditIdx === i;
              const segs = p.parts.filter(pt => pt.length >= 2).length;
              return (
                <div key={i} className={`flex items-center gap-1.5 text-[11px] rounded px-2 py-1 border ${editing ? 'bg-amber-950/50 border-amber-700' : 'bg-surface-950/60 border-surface-700'}`}>
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: editing ? '#f59e0b' : '#22d3ee' }} />
                  <div className="flex-1 min-w-0">
                    <div className="truncate text-surface-200" title={p.name}>{p.name}</div>
                    <div className="text-[9px] text-surface-500">{Math.round(p.lengthKm)} km{segs > 1 ? ` · ${segs} pieces` : ''}</div>
                  </div>
                  <button onClick={() => pbToggleEdit(i)} className={`shrink-0 ${editing ? 'text-amber-300' : 'text-surface-500 hover:text-amber-300'}`} title={editing ? 'Finish editing' : 'Edit — erase artifacts'}>✎</button>
                  <button onClick={() => { setPbPaths(prev => prev.filter((_, j) => j !== i)); if (pbEditIdx === i) setPbEditIdx(null); }} className="text-surface-500 hover:text-red-300 shrink-0" title="Remove">×</button>
                </div>
              );
            })}
          </div>
          {/* Advanced: pick whole folders instead of tracing */}
          <div className="border-t border-surface-700">
            <button onClick={() => setPbShowFolders(v => !v)} className="w-full text-left px-3 py-1.5 text-[10px] uppercase tracking-wide text-surface-500 hover:text-surface-300">
              {pbShowFolders ? '▾' : '▸'} Select by folder (advanced)
            </button>
            {pbShowFolders && (
              <div className="max-h-[160px] overflow-auto px-2 pb-2">
                {pbRoot ? renderPbNode(pbRoot, 0) : <div className="text-[11px] text-surface-500 px-1">No overlay.</div>}
              </div>
            )}
          </div>
          {/* Outputs */}
          <div className="px-3 py-2 border-t border-surface-700 grid grid-cols-2 gap-1.5">
            <button onClick={pbAddAsOverlay} disabled={!pbPaths.length}
              className="text-[11px] font-medium py-1.5 rounded bg-cyan-600 border border-cyan-500 text-white disabled:opacity-40 hover:bg-cyan-500">Add as overlay</button>
            <button onClick={pbDownload} disabled={!pbPaths.length}
              className="text-[11px] font-medium py-1.5 rounded bg-surface-800 border border-surface-600 text-surface-200 disabled:opacity-40 hover:bg-surface-700">⬇ Download .kmz</button>
          </div>
        </div>
      )}
      {notConfigured && (
        <div className="absolute inset-0 z-20 flex items-center justify-center p-6">
          <div className="max-w-md bg-surface-800 border border-surface-600 rounded-lg p-5 text-center space-y-2">
            <div className="text-sm font-medium text-surface-100">Protomaps basemap not configured</div>
            <div className="text-[11px] text-surface-400 leading-relaxed">
              Get a free API key at{' '}
              <a href="https://protomaps.com/account" target="_blank" rel="noreferrer" className="text-primary-400 hover:underline">
                protomaps.com/account
              </a>{' '}
              and set it in your <code>.env</code>:
            </div>
            <pre className="text-[10px] text-left bg-surface-950 border border-surface-700 rounded p-2 text-surface-300 whitespace-pre-wrap">
PROTOMAPS_API_KEY=your_key_here{'\n'}PROTOMAPS_THEME=dark</pre>
            <div className="text-[10px] text-surface-500">
              then <code>docker compose up -d backend</code> (no rebuild). Self-hosted
              instances can set <code>PROTOMAPS_TILES_URL</code> or <code>PROTOMAPS_STYLE_URL</code> instead.
            </div>
          </div>
        </div>
      )}
      {styleError && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 bg-red-900/80 border border-red-600 text-red-100 text-[11px] px-3 py-1.5 rounded shadow-lg max-w-[80%]">
          Map failed to load: {styleError}
        </div>
      )}
      {/* Overlay controls. Capped to the map height and scrollable, so layers
          whose legends run past the bottom of the screen stay reachable;
          children don't shrink, so the column overflows into the scroll. */}
      {!notConfigured && (
        <div className="absolute top-3 left-3 z-10 flex flex-col gap-2 max-h-[calc(100%-1.5rem)] overflow-y-auto overflow-x-hidden pr-1 overscroll-contain [&>*]:shrink-0">
          {/* Edit positions: guards against accidentally dragging nodes while
              panning. Nodes are only draggable while this is on (and not
              inherited). Undo reverts an accidental move. Hidden on a protected
              topology until the user enters edit mode. */}
          {canEdit && <div className="flex items-center gap-1.5">
            <button
              onClick={() => setGeoEditMode(v => !v)}
              title={geoEditMode
                ? 'Editing positions — drag nodes to move them, or select a node and press S to snap it to the search pin. Click (or E) to turn off.'
                : 'Turn on to drag nodes to new positions (E)'}
              className={`flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1.5 rounded shadow-lg border transition-colors ${
                geoEditMode
                  ? 'bg-amber-600/90 border-amber-500 text-white'
                  : 'bg-surface-900/85 border-surface-700 text-surface-300 hover:text-surface-100'
              }`}
            >
              ✎ {geoEditMode ? 'Editing positions' : 'Edit positions'}
            </button>
            {geoEditMode && undoCount > 0 && (
              <button
                onClick={undoLastMove}
                title="Undo the last node move (Ctrl/Cmd+Z)"
                className="flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1.5 rounded shadow-lg border bg-surface-900/85 border-surface-700 text-surface-300 hover:text-surface-100"
              >
                ↶ Undo move{undoCount > 1 ? ` (${undoCount})` : ''}
              </button>
            )}
          </div>}
          {/* Node-kind filter — hide/show node roles (customers hidden by default). */}
          {availableRoles.length > 1 && (
            <div className="bg-surface-900/85 border border-surface-700 rounded px-2 py-1.5 max-w-[200px]">
              <div className="text-[9px] uppercase tracking-wide text-surface-500 mb-1">Show nodes</div>
              <div className="space-y-0.5">
                {availableRoles.map(({ role, count }) => {
                  const hidden = hiddenRoles.has(role);
                  return (
                    <button
                      key={role}
                      onClick={() => setHiddenRoles(prev => { const n = new Set(prev); n.has(role) ? n.delete(role) : n.add(role); return n; })}
                      className={`w-full flex items-center gap-1.5 text-[10px] px-1 py-0.5 rounded transition-colors hover:bg-surface-800 ${hidden ? 'opacity-40' : 'text-surface-300'}`}
                      title={hidden ? `Show ${role} nodes` : `Hide ${role} nodes`}
                    >
                      <span className="w-2.5 h-2.5 rounded-full shrink-0 border-2" style={{ borderColor: hidden ? '#475569' : '#38bdf8', background: hidden ? 'transparent' : '#1e293b' }} />
                      <span className="flex-1 text-left capitalize">{role}</span>
                      <span className="text-surface-500 shrink-0">{count}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          {/* Individually-hidden nodes (right-click a marker to hide) — restore here. */}
          {hiddenNodesPresent.length > 0 && (
            <div className="bg-surface-900/85 border border-surface-700 rounded px-2 py-1.5 max-w-[200px]">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[9px] uppercase tracking-wide text-surface-500">Hidden nodes ({hiddenNodesPresent.length})</span>
                <button
                  onClick={() => setHiddenNodeIds(prev => { const s = new Set(prev); for (const n of nodes) s.delete(n.id); return s; })}
                  className="text-[9px] text-sky-300 hover:underline"
                >Show all</button>
              </div>
              <div className="space-y-0.5 max-h-[120px] overflow-y-auto">
                {hiddenNodesPresent.map(n => (
                  <button
                    key={n.id}
                    onClick={() => setHiddenNodeIds(prev => { const s = new Set(prev); s.delete(n.id); return s; })}
                    className="w-full flex items-center gap-1.5 text-[10px] px-1 py-0.5 rounded text-surface-400 hover:bg-surface-800 hover:text-surface-200"
                    title={`Show ${n.name}`}
                  >
                    <span className="shrink-0">↺</span>
                    <span className="flex-1 truncate text-left">{n.name}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {/* Tools: things you run (route / hub finders, KMZ fiber routes). */}
          <div className="text-[9px] font-semibold uppercase tracking-widest text-surface-500 pt-1 pb-0.5 px-0.5">Tools</div>
          {/* Interconnection path finder (A-loc → Z-loc). */}
          <button
            onClick={() => setShowPathFinder(v => !v)}
            className={`flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1.5 rounded shadow-lg border transition-colors ${
              showPathFinder ? 'bg-emerald-600/90 border-emerald-500 text-white' : 'bg-surface-900/85 border-surface-700 text-surface-300 hover:text-surface-100'
            }`}
            title="Find a heuristic carrier path from an A-location to a Z-location"
          >
            <span>🧭</span>
            Path finder
          </button>
          {showPathFinder && (
            <div className="bg-surface-900/85 border border-surface-700 rounded px-2 py-1.5 max-w-[240px] space-y-1">
              <input value={pathA} onChange={e => setPathA(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') findPath(); }}
                placeholder="From (A-loc)…" className="w-full text-[11px] bg-surface-950 border border-surface-700 rounded px-2 py-1 text-surface-200 placeholder-surface-600 focus:outline-none focus:border-emerald-500" />
              <input value={pathZ} onChange={e => setPathZ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') findPath(); }}
                placeholder="To (Z-loc)…" className="w-full text-[11px] bg-surface-950 border border-surface-700 rounded px-2 py-1 text-surface-200 placeholder-surface-600 focus:outline-none focus:border-emerald-500" />
              <label className="flex items-center gap-1.5 text-[10px] text-surface-400">
                Paths:
                <select value={pathCount} onChange={e => setPathCount(Number(e.target.value))}
                  className="bg-surface-950 border border-surface-700 rounded px-1 py-0.5 text-surface-200 focus:outline-none focus:border-emerald-500">
                  {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(n => <option key={n} value={n}>{n}</option>)}
                </select>
                <span className="text-surface-600">(may return fewer)</span>
              </label>
              <div className="flex gap-1">
                <button onClick={findPath} disabled={pathLoading || !pathA.trim() || !pathZ.trim()}
                  className="flex-1 text-[11px] font-medium py-1 rounded bg-emerald-600/90 border border-emerald-500 text-white disabled:opacity-40 hover:bg-emerald-500">
                  {pathLoading ? 'Finding…' : 'Find paths'}</button>
                {pathResult && <button onClick={clearPath} className="text-[11px] px-2 py-1 rounded border border-surface-700 text-surface-300 hover:text-surface-100">clear</button>}
              </div>
              {pathError && <div className="text-[10px] text-red-300">{pathError}</div>}
              {pathResult && (
                <div className="space-y-1.5 max-h-[260px] overflow-y-auto pt-0.5">
                  <div className="text-[9px] text-surface-400 leading-snug">
                    A → <span className="text-cyan-300">{pathResult.a.snap.city || pathResult.a.snap.name}</span>{pathResult.a.snap_km > 50 ? ` (${pathResult.a.snap_km} km — first mile)` : ''} · Z → <span className="text-rose-300">{pathResult.z.snap.city || pathResult.z.snap.name}</span>{pathResult.z.snap_km > 50 ? ` (${pathResult.z.snap_km} km — last mile)` : ''}
                  </div>
                  {pathResult.paths.map((p: any, pi: number) => (
                    <div key={pi} className="border-t border-surface-700 pt-1">
                      <div className="flex items-center gap-1 text-[9px] uppercase tracking-wide mb-0.5">
                        <span className="w-3 h-0.5 rounded" style={{ background: PATH_PALETTE[pi % PATH_PALETTE.length] }} />
                        <span className="text-surface-400">Path {pi + 1} · {p.hops} hop{p.hops === 1 ? '' : 's'}</span>
                      </div>
                      {p.segments.map((s: any, si: number) => (
                        <div key={si} className="text-[10px] text-surface-300 pl-1 leading-tight">
                          <span className="text-surface-500">{si === 0 ? (pathResult.a.snap.city || 'A') : (p.segments[si - 1].to.city || '·')}</span>
                          {' → '}<span className="text-surface-200">{s.carrier.name}</span> <span className="text-surface-500">AS{s.carrier.asn}</span>
                          {' → '}<span className="text-surface-400">{s.to.city || s.to.name}{s.to.kind === 'ix' ? ' (IX)' : ''}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                  <div className="text-[9px] text-surface-500 leading-snug border-t border-surface-700 pt-1">
                    Heuristic carrier path · presence ≠ guaranteed transit/peering, no pricing. Big first/last-mile distance → use nearest-hub + satellite.
                  </div>
                </div>
              )}
            </div>
          )}
          <button
            onClick={() => setFinderMode(v => !v)}
            className={`flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1.5 rounded shadow-lg border transition-colors ${
              finderMode
                ? 'bg-sky-600/90 border-sky-500 text-white'
                : 'bg-surface-900/85 border-surface-700 text-surface-300 hover:text-surface-100'
            }`}
            title="Click anywhere on the map to find the nearest interconnection hubs and their carriers"
          >
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: finderMode ? '#fff' : '#38bdf8' }} />
            {finderMode ? 'Click the map…' : 'Connectivity finder'}
          </button>
          {/* Import KMZ/KML (e.g. fiber routes) — parsed + overlaid client-side. */}
          <label
            className="flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1.5 rounded shadow-lg border bg-surface-900/85 border-surface-700 text-surface-300 hover:text-surface-100 cursor-pointer"
            title="Import a KMZ/KML file (e.g. fiber routes) and overlay it — stays in your browser"
          >
            <span>＋</span>
            Import KMZ/KML
            <input
              type="file" accept=".kmz,.kml" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) importKmz(f); e.currentTarget.value = ''; }}
            />
          </label>
          {kmzError && <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">{kmzError}</div>}
          {kmzLayers.length > 0 && (
            <div className="bg-surface-900/85 border border-surface-700 rounded px-2 py-1.5 max-w-[220px]">
              <div className="text-[9px] uppercase tracking-wide text-surface-500 mb-1">Imported overlays</div>
              <div className="space-y-0.5">
                {kmzLayers.map(l => (
                  <div key={l.id} className="flex items-center gap-1.5 text-[10px] text-surface-300">
                    <span className="w-2.5 h-0.5 rounded-full shrink-0" style={{ background: '#f59e0b' }} />
                    <span className="flex-1 truncate" title={l.name}>{l.name}</span>
                    <span className="text-surface-500 shrink-0">{l.count}</span>
                    <button onClick={() => removeKmz(l.id)} className="text-surface-500 hover:text-red-300 shrink-0" title="Remove">×</button>
                  </div>
                ))}
              </div>
              <div className="mt-1 pt-1 border-t border-surface-700 text-[9px] text-surface-500 leading-snug">Client-side only · not saved across reloads.</div>
            </div>
          )}
          {/* Path Builder — simplify a messy multi-folder KMZ into clean named routes. */}
          {kmzLayers.length > 0 && (
            <button
              onClick={openPathBuilder}
              className="flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1.5 rounded shadow-lg border bg-surface-900/85 border-surface-700 text-surface-300 hover:text-surface-100"
              title="Select the segments that make up a path and stitch them into a clean, simple route"
            >
              <span>🧬</span>
              Path Builder
            </button>
          )}
          {/* Layers: overlays you switch on, grouped; each group's header shows
              how many of its layers are on. */}
          <div className="text-[9px] font-semibold uppercase tracking-widest text-surface-500 pt-1.5 pb-0.5 px-0.5">Layers</div>
          <LayerGroup label="Infrastructure" color="#34d399" activeCount={infraOn} open={openGroups.has('infra')}
            onToggle={() => toggleGroup('infra')} title="Submarine cables, data centers, internet exchanges, cloud regions and satellite-operator PoPs">
              <LayerToggle on={showSubmarineCables} onClick={toggleSubmarineCables} color="#fb923c" label="Submarine cables"
                title="Overlay submarine cable routes + landing points (© TeleGeography)" />
              {subseaError && (
                <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">
                  Cable data failed: {subseaError}
                </div>
              )}
              {/* Cable owners — click a provider to isolate its cables (Google etc.). */}
              {showSubmarineCables && cableProviders.length > 0 && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5">
                  <div className="text-[9px] uppercase tracking-wide text-surface-500 mb-1">Cable owners</div>
                  <div className="space-y-0.5">
                    {cableProviders.map(p => {
                      const active = cableProviderFilter === p.key;
                      return (
                        <button
                          key={p.key}
                          onClick={() => setCableProviderFilter(active ? null : p.key)}
                          className={`w-full flex items-center gap-1.5 text-[10px] px-1 py-0.5 rounded transition-colors ${
                            active ? 'bg-surface-700 text-surface-100' : 'text-surface-300 hover:bg-surface-800'
                          }`}
                          title={active ? 'Show all cables' : `Show only ${p.label} cables`}
                        >
                          <span className="w-3 h-[2px] rounded-full shrink-0" style={{ background: p.color }} />
                          <span className="truncate flex-1 text-left">{p.label}</span>
                          <span className="text-surface-500 shrink-0">{p.count}</span>
                        </button>
                      );
                    })}
                  </div>
                  <div className="mt-1 pt-1 border-t border-surface-700 text-[9px] text-surface-500 leading-snug">
                    {cableProviderFilter ? 'Showing one owner · click again to show all.' : 'Click an owner to isolate · click a cable for details.'}
                  </div>
                </div>
              )}
              <LayerToggle on={showDataCenters} onClick={toggleDataCenters} color="#34d399" label="Data centers"
                title="Overlay colocation / data-center facilities, colored by operator (© PeeringDB)" />
              {dcError && (
                <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">
                  Data-center data failed: {dcError}
                </div>
              )}
              {/* Vendor legend — click a row to filter the map to that operator;
                  click again (or "Show all") to clear. */}
              {showDataCenters && dcLegend.length > 0 && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[9px] uppercase tracking-wide text-surface-500">Operators</span>
                    {dcFilter && (
                      <button
                        className="text-[9px] text-primary-400 hover:text-primary-300"
                        onClick={() => setDcFilter(null)}
                      >
                        Show all
                      </button>
                    )}
                  </div>
                  <div className="space-y-0.5">
                    {dcLegend.map(l => {
                      const activeRow = dcFilter === l.name;
                      return (
                        <button
                          key={l.name}
                          onClick={() => setDcFilter(activeRow ? null : l.name)}
                          className={`w-full flex items-center gap-1.5 text-[10px] px-1 py-0.5 rounded transition-colors ${
                            activeRow ? 'bg-surface-700 text-surface-100' : 'text-surface-300 hover:bg-surface-800'
                          } ${dcFilter && !activeRow ? 'opacity-50' : ''}`}
                          title={`Show only ${l.name}`}
                        >
                          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: l.color }} />
                          <span className="truncate">{l.name}</span>
                        </button>
                      );
                    })}
                  </div>
                  {/* A facility click can filter to an operator outside the
                      top-N legend — surface that so it's clear what's shown. */}
                  {dcFilter && !dcLegend.some(l => l.name === dcFilter) && (
                    <div className="mt-1 pt-1 border-t border-surface-700 text-[10px] text-surface-200 truncate" title={dcFilter}>
                      Showing: {dcFilter}
                    </div>
                  )}
                </div>
              )}
              <LayerToggle on={showIXPs} onClick={toggleIXPs} color="#a3e635" label="Internet exchanges"
                title="Overlay Internet Exchange Points, sized by peer count (© PeeringDB)" />
              {ixError && (
                <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">
                  IXP data failed: {ixError}
                </div>
              )}
              <LayerToggle on={showCloudRegions} onClick={toggleCloudRegions} color="#818cf8" label="Cloud regions"
                title="Overlay AWS / Azure / GCP cloud regions (curated dataset)" />
              {cloudError && (
                <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">
                  Cloud data failed: {cloudError}
                </div>
              )}
              {showCloudRegions && cloudProviders.length > 0 && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5">
                  <div className="text-[9px] uppercase tracking-wide text-surface-500 mb-1">Cloud providers</div>
                  <div className="space-y-0.5">
                    {cloudProviders.map(p => {
                      const hidden = cloudHidden.has(p.key);
                      return (
                        <button
                          key={p.key}
                          onClick={() => setCloudHidden(prev => {
                            const n = new Set(prev); n.has(p.key) ? n.delete(p.key) : n.add(p.key); return n;
                          })}
                          className={`w-full flex items-center gap-1.5 text-[10px] px-1 py-0.5 rounded transition-colors hover:bg-surface-800 ${hidden ? 'opacity-40' : 'text-surface-300'}`}
                          title={hidden ? `Show ${p.label}` : `Hide ${p.label}`}
                        >
                          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: p.color }} />
                          <span className="truncate flex-1 text-left">{p.label}</span>
                          <span className="text-surface-500 shrink-0">{p.count}</span>
                        </button>
                      );
                    })}
                  </div>
                  <div className="mt-1 pt-1 border-t border-surface-700 text-[9px] text-surface-500 leading-snug">
                    Approximate region metros · for colo on-ramps, use the Data centers vendor filter.
                  </div>
                </div>
              )}
              <LayerToggle on={popKeys.includes('starlink')} onClick={() => togglePopKey('starlink')} color="#22d3ee" label="Starlink PoPs"
                title="Starlink's terrestrial interconnection PoPs — facilities and IXPs (© PeeringDB)" />
              <LayerToggle on={showSatelliteLinks} onClick={toggleShowSatelliteLinks} color="#22d3ee" label="Satellite links"
                title="Draw your dish ↔ PoP satellite links (otherwise shown only for the selected dish / PoP or a highlighted path · Ctrl+Shift+S)" />
              <LayerToggle on={popKeys.includes('oneweb')} onClick={() => togglePopKey('oneweb')} color="#f472b6" label="OneWeb PoPs"
                title="OneWeb's terrestrial interconnection PoPs — facilities and IXPs (© PeeringDB)" />
              {operatorPopsError && <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded">PoPs failed: {operatorPopsError}</div>}
              {operatorPopsInfo.length > 0 && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5 space-y-0.5">
                  {operatorPopsInfo.map(i => (
                    <div key={i.label} className="text-[9px] text-surface-400">
                      <span className="text-surface-200">{i.label}</span> · ◆ {i.facilities} facilities · ⬡ {i.ixps} IXPs
                    </div>
                  ))}
                  <div className="text-[9px] text-surface-500 leading-snug">Where you can interconnect with the operator.</div>
                </div>
              )}
          </LayerGroup>
          <LayerGroup label="Satellite" color="#2dd4bf" activeCount={satOn} open={openGroups.has('sat')}
            onToggle={() => toggleGroup('sat')} title="Satellite constellation and GEO coverage">
              <LayerToggle on={showCoverageStrength} onClick={toggleCoverageStrength} color="#2dd4bf" label="Constellation coverage"
                title="Expected satellites-in-view by latitude — click anywhere to compare constellations there" />
              {strengthError && (
                <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">
                  Strength data failed: {strengthError}
                </div>
              )}
              {showCoverageStrength && strengthData.length > 0 && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5">
                  <div className="text-[9px] uppercase tracking-wide text-surface-500 mb-1">Density heat — pick a fleet</div>
                  <div className="space-y-0.5">
                    {strengthData.map(c => {
                      const active = heatKey === c.key;
                      const hasPops = c.key === 'starlink' || c.key === 'oneweb';
                      const popsOn = popKeys.includes(c.key);
                      return (
                        <div key={c.key} className={`w-full flex items-center gap-1 rounded ${active ? 'bg-surface-700' : ''}`}>
                          <button
                            onClick={() => setHeatKey(c.key)}
                            className={`flex-1 min-w-0 flex items-center gap-1.5 text-[10px] px-1 py-0.5 rounded transition-colors ${active ? 'text-surface-100' : 'text-surface-300 hover:bg-surface-800'}`}
                            title={`Show ${c.vendor} density heat`}
                          >
                            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: c.color, outline: active ? '2px solid rgba(255,255,255,0.5)' : 'none', outlineOffset: '1px' }} />
                            {c.regime && (
                              <span className="text-[7px] font-semibold uppercase tracking-wide bg-surface-700 text-surface-300 px-1 rounded shrink-0">{c.regime}</span>
                            )}
                            <span className="truncate flex-1 text-left">{c.vendor}</span>
                            <span className="text-surface-500 shrink-0">peak ~{Math.round(c.peak_in_view)}</span>
                          </button>
                          {hasPops && (
                            <button
                              onClick={() => togglePopKey(c.key)}
                              title={popsOn ? `Hide ${c.vendor} interconnection PoPs` : `Show ${c.vendor} interconnection PoPs`}
                              className={`shrink-0 text-[10px] leading-none px-1 py-0.5 rounded ${popsOn ? 'bg-cyan-600 text-white' : 'text-surface-400 hover:text-surface-100 hover:bg-surface-800'}`}
                            >📍</button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  <div className="mt-1 pt-1 border-t border-surface-700 text-[9px] text-surface-400 leading-snug">
                    <span className="text-teal-300">Click the globe anywhere</span> to compare sats-in-view there · 📍 plots an operator's interconnection PoPs (Starlink/OneWeb).
                  </div>
                </div>
              )}
              <LayerToggle on={showGeoCoverage} onClick={toggleGeoCoverage} color="#f59e0b" label="GEO coverage"
                title="Geostationary coverage footprints per operator (fades to the low-elevation edge; geometric reach)" />
              {geoError && (
                <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">
                  GEO data failed: {geoError}
                </div>
              )}
              {showGeoCoverage && geoOperators.length > 0 && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5">
                  <div className="text-[9px] uppercase tracking-wide text-surface-500 mb-1">GEO operators</div>
                  <div className="space-y-0.5">
                    {geoOperators.map(o => {
                      const hidden = geoHidden.has(o.key);
                      return (
                        <button
                          key={o.key}
                          onClick={() => setGeoHidden(prev => { const n = new Set(prev); n.has(o.key) ? n.delete(o.key) : n.add(o.key); return n; })}
                          className={`w-full flex items-center gap-1.5 text-[10px] px-1 py-0.5 rounded transition-colors hover:bg-surface-800 ${hidden ? 'opacity-40' : 'text-surface-300'}`}
                          title={hidden ? `Show ${o.label}` : `Hide ${o.label}`}
                        >
                          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: o.color }} />
                          <span className="truncate flex-1 text-left">{o.label}</span>
                          <span className="text-surface-500 shrink-0">{o.count}</span>
                        </button>
                      );
                    })}
                  </div>
                  <div className="mt-1 pt-1 border-t border-surface-700 text-[9px] text-surface-500 leading-snug">
                    Brighter = overhead (strong); fades to the low-elevation edge. Geometric reach, not service.
                  </div>
                </div>
              )}
          </LayerGroup>
          <LayerGroup label="Live events" color="#ef4444" activeCount={liveOn} open={openGroups.has('live')}
            onToggle={() => toggleGroup('live')} title="Live feeds: weather, fires, earthquakes, cyclones, internet outages">
              <LayerToggle on={showWeatherAlerts} onClick={toggleWeatherAlerts} color="#f97316" label="Weather alerts"
                title="Active US severe-weather warnings (NWS) — an outage risk proxy" />
              {weatherError && (
                <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">
                  Alert data failed: {weatherError}
                </div>
              )}
              {showWeatherAlerts && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5">
                  <div className="text-[9px] uppercase tracking-wide text-surface-500 mb-1">Weather alerts (US)</div>
                  <div className="flex gap-2 text-[10px] mb-1 px-1">
                    <span className="text-surface-200" title="Occurring / imminent (observed or immediate)">● Active {weatherActivity.active}</span>
                    <span className="text-surface-400" title="Anticipated conditions (watches / forecast)">◌ Possible {weatherActivity.possible}</span>
                  </div>
                  {weatherSeverities.length > 0 ? (
                    <div className="space-y-0.5">
                      {weatherSeverities.map(s => (
                        <div key={s.severity} className="flex items-center gap-1.5 text-[10px] text-surface-300 px-1">
                          <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: s.color }} />
                          <span className="flex-1">{s.severity}</span>
                          <span className="text-surface-500">{s.count}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-[10px] text-surface-500 px-1">No active severe alerts.</div>
                  )}
                  {atRiskNodeIds.size > 0 && (
                    <div className="mt-1 flex items-center gap-1.5 text-[10px] text-red-300 px-1">
                      <span>⚠</span>
                      <span className="flex-1">{atRiskNodeIds.size} of your node{atRiskNodeIds.size === 1 ? '' : 's'} in an alert area</span>
                    </div>
                  )}
                  <div className="mt-1 pt-1 border-t border-surface-700 text-[9px] text-surface-500 leading-snug">
                    Severe/extreme warnings incl. zone-based · NWS · outage risk proxy, not actual outages.
                  </div>
                </div>
              )}
              <LayerToggle on={showActiveFires} onClick={toggleActiveFires} color="#f97316" label="Active fires"
                title="Current US wildfires — perimeters + incidents (NIFC). Actual active events." />
              {firesError && (
                <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">
                  Fire data failed: {firesError}
                </div>
              )}
              {showActiveFires && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5">
                  <div className="text-[9px] uppercase tracking-wide text-surface-500 mb-1">Active fires (US)</div>
                  <div className="space-y-0.5 text-[10px] text-surface-300 px-1">
                    <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: '#ef4444' }} /><span className="flex-1">Perimeters</span><span className="text-surface-500">{fireCounts.perimeters}</span></div>
                    <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: '#f97316' }} /><span className="flex-1">Incidents</span><span className="text-surface-500">{fireCounts.incidents}</span></div>
                  </div>
                  <div className="mt-1 pt-1 border-t border-surface-700 text-[9px] text-surface-500 leading-snug">
                    Current US wildfires · NIFC WFIGS · actual active incidents.
                  </div>
                </div>
              )}
              <LayerToggle on={showEarthquakes} onClick={toggleEarthquakes} color="#f59e0b" label="Earthquakes"
                title="Recent earthquakes, USGS M2.5+ past 24h" />
              {quakeError && <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">Quake data failed: {quakeError}</div>}
              {showEarthquakes && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5 text-[9px] text-surface-500 leading-snug">
                  {quakeCount} quakes · USGS M2.5+ past 24h · size/color = magnitude.
                </div>
              )}
              <LayerToggle on={showCyclones} onClick={toggleCyclones} color="#a855f7" label="Cyclones"
                title="Active tropical cyclones (NOAA NHC — Atlantic + E/C Pacific)" />
              {cycloneError && <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">Cyclone data failed: {cycloneError}</div>}
              {showCyclones && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5 text-[9px] text-surface-500 leading-snug">
                  {cycloneCount > 0 ? `${cycloneCount} active storm${cycloneCount === 1 ? '' : 's'}` : 'No active storms'} · NHC (Atlantic + E/C Pacific) · positions only.
                </div>
              )}
              <LayerToggle on={showInternetOutages} onClick={toggleInternetOutages} color="#22d3ee" label="Internet outages"
                title="Countries with recent internet-outage activity (IODA, last 24h)" />
              {outageError && <div className="bg-red-900/80 border border-red-600 text-red-100 text-[10px] px-2 py-1 rounded max-w-[220px]">Outage data failed: {outageError}</div>}
              {showInternetOutages && (
                <div className="ml-4 pl-2 border-l border-surface-700 py-0.5 text-[9px] text-surface-500 leading-snug">
                  {outageCount > 0 ? `${outageCount} countr${outageCount === 1 ? 'y' : 'ies'} with outage activity` : 'No outage activity (last 24h)'} · IODA · marker at country centroid (approx).
                </div>
              )}
          </LayerGroup>
        </div>
      )}
      {/* Geographic latency estimate for the highlighted flow. */}
      {!notConfigured && latencyEstimate && (
        <div className="absolute top-3 right-14 z-10 bg-surface-900/90 border border-amber-600/50 text-surface-200 text-[11px] px-3 py-1.5 rounded shadow-lg">
          <span className="text-amber-300 font-medium">{latencyEstimate.flowName || 'Flow'}</span>
          {' '}· {latencyEstimate.km.toLocaleString()} km · ~{latencyEstimate.rttMs} ms RTT
          <span className="block text-[9px] text-surface-500">great-circle fiber minimum</span>
        </div>
      )}
      {!notConfigured && (
        <div className="absolute bottom-3 left-3 z-10 bg-surface-900/80 text-surface-300 text-[10px] px-2 py-1 rounded pointer-events-none">
          Geographic view — drag a node to set its real-world location
          {showSubmarineCables && <span className="text-surface-500"> · cables © TeleGeography</span>}
          {(showDataCenters || showIXPs) && <span className="text-surface-500"> · DC/IX © PeeringDB</span>}
          {(showCoverageStrength || showGeoCoverage) && <span className="text-surface-500"> · orbits © CelesTrak</span>}
          {showWeatherAlerts && <span className="text-surface-500"> · alerts © NWS</span>}
          {showActiveFires && <span className="text-surface-500"> · fires © NIFC</span>}
          {showEarthquakes && <span className="text-surface-500"> · quakes © USGS</span>}
          {showCyclones && <span className="text-surface-500"> · storms © NHC</span>}
          {showInternetOutages && <span className="text-surface-500"> · outages © IODA</span>}
          {operatorPopsInfo.length > 0 && <span className="text-surface-500"> · PoPs © PeeringDB</span>}
          {pathResult && <span className="text-surface-500"> · path heuristic © PeeringDB</span>}
          {showCloudRegions && <span className="text-surface-500"> · cloud regions approx.</span>}
        </div>
      )}
    </div>
  );
}

/** Checkbox-style row for one overlay layer inside a grouped layer panel. */
function LayerToggle({ on, onClick, color, label, title }: { on: boolean; onClick: () => void; color: string; label: string; title: string }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`w-full flex items-center gap-1.5 text-[11px] px-1 py-1 rounded transition-colors hover:bg-surface-800 ${on ? 'text-surface-100' : 'text-surface-400'}`}
    >
      <span
        className="w-3 h-3 rounded-sm border flex items-center justify-center shrink-0 text-[9px] leading-none text-surface-950 font-bold"
        style={{ borderColor: color, background: on ? color : 'transparent' }}
      >
        {on ? '✓' : ''}
      </span>
      <span className="flex-1 text-left">{label}</span>
    </button>
  );
}

/** Collapsible group of overlay layers in the map's layer panel. The header
 *  is tinted while any of its layers is on and shows how many are. */
function LayerGroup({ label, color, activeCount, open, onToggle, title, children }: {
  label: string; color: string; activeCount: number; open: boolean; onToggle: () => void; title: string; children: React.ReactNode;
}) {
  return (
    <>
      <button
        onClick={onToggle}
        title={title}
        className={`flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1.5 rounded shadow-lg border transition-colors ${
          activeCount > 0
            ? 'bg-surface-800/95 text-white'
            : 'bg-surface-900/85 border-surface-700 text-surface-300 hover:text-surface-100'
        }`}
        style={activeCount > 0 ? { borderColor: color } : undefined}
      >
        <span className="w-2.5 h-2.5 rounded-full" style={{ background: color }} />
        {label}{activeCount > 0 ? ` · ${activeCount}` : ''}
        <span className="ml-auto pl-2 text-[9px] opacity-70">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="bg-surface-900/85 border border-surface-700 rounded px-2 py-1.5 max-w-[240px] space-y-1">
          {children}
        </div>
      )}
    </>
  );
}
