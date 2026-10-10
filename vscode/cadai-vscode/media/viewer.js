// CadAI 3D viewer: renders the live FreeCAD document.
// Modes: select (click = select face in FreeCAD) and "show & describe" tools that create numbered annotations with
// a note: mark (pin), measure (two points), line (polyline), circle (center + radius on a face), pen (freehand).
// Also: FEM result color maps (von Mises / displacement, deformed shape), DFM finding highlights, section plane.
//
// Geometry: one merged mesh per FreeCAD object (a face is a triangle range), BVH-accelerated picking
// (three-mesh-bvh) and pixel-width edges (LineSegments2). three.js + add-ons come from vendor/three-bundle.js.
import * as THREE from './vendor/three-bundle.js';

const { OrbitControls, Lut, LineSegments2, LineSegmentsGeometry, LineMaterial } = THREE;
THREE.BufferGeometry.prototype.computeBoundsTree = THREE.computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = THREE.disposeBoundsTree;
THREE.Mesh.prototype.raycast = THREE.acceleratedRaycast;

const vscode = acquireVsCodeApi();
let viewTarget = null;
function send(message) { vscode.postMessage(viewTarget ? { ...message, target: viewTarget } : message); }
const $ = (id) => document.getElementById(id);

// ---------------- GPU: any vendor, any class ----------------
// Ask for the discrete GPU on dual-GPU machines, then step down (no antialiasing, default GPU) when an old, weak or
// blocklisted driver refuses. Without WebGL 2 at all, say why instead of showing an empty panel.
const GL_OPTIONS = [
  { antialias: true, powerPreference: 'high-performance' },
  { antialias: false, powerPreference: 'high-performance' },
  { antialias: false, powerPreference: 'default' },
  { antialias: false }, // some drivers reject any explicit power preference
];
let renderer = null, glAttempt = -1, glError = null, contextLost = false;
for (const [i, opts] of GL_OPTIONS.entries()) {
  try {
    let parameters = opts;
    if (i === GL_OPTIONS.length - 1) {
      // three.js itself supplies a default powerPreference. Create the last context ourselves to omit it entirely.
      const canvas = document.createElement('canvas');
      const context = canvas.getContext('webgl2', { antialias: false });
      if (!context) throw new Error('WebGL 2 context unavailable');
      parameters = { ...opts, canvas, context };
    }
    renderer = new THREE.WebGLRenderer(parameters); glAttempt = i; break;
  } catch (e) { glError = String((e && e.message) || e); }
}

function showGlNotice(text, reload = true) {
  $('glText').textContent = text;
  $('glReload').classList.toggle('hidden', !reload);
  $('glNotice').classList.remove('hidden');
}
$('glReload').onclick = () => send({ type: 'reloadViewer' });
$('glDiag').onclick = () => send({ type: 'command', command: 'cadai.gpuDiagnostics' });

if (!renderer) {
  showGlNotice('3B görünüm açılamadı: bu bilgisayarda WebGL 2 kullanılamıyor.\n'
    + 'Genelde nedeni eksik ya da eski ekran kartı sürücüsü, VS Code\'da kapalı donanım hızlandırması, uzak masaüstü '
    + 'veya sanal makinedir. "GPU tanılaması" nedenini ve çözümünü gösterir.', false);
  send({ type: 'ready', gl: null, glError });
  await new Promise(() => {}); // stop here: everything below needs a renderer (modules may await at top level)
}
renderer.localClippingEnabled = true;
document.body.appendChild(renderer.domElement);

// Driver resets (TDR after a hang, driver update, sleep, GPU switch on hybrid laptops) take the WebGL context away.
// Keep it restorable, tell the user, and rebuild every GPU buffer from FreeCAD's data when it comes back.
renderer.domElement.addEventListener('webglcontextlost', (e) => {
  e.preventDefault();
  contextLost = true;
  lastFrameAt = 0;
  frameGaps.length = 0;
  showGlNotice('Ekran kartı sürücüsü 3B görünümü sıfırladı (sürücü güncellemesi, uyku, GPU değişimi ya da aşırı yük).\n'
    + 'Görünüm kendiliğinden geri gelir; gelmezse yeniden yükleyin.');
  send({ type: 'glLost' });
});
renderer.domElement.addEventListener('webglcontextrestored', () => {
  contextLost = false;
  glStart = glInfo() || glStart;
  softwareGl = SOFTWARE_GL.test(glStart.renderer || '');
  resize();
  send({ type: 'glRestored', gl: { ...glStart, pixelRatio: quality.ratio } });
  $('glNotice').classList.add('hidden');
  send({ type: 'needFullScene' });
  requestRender();
});

// Resolution follows the GPU: full (up to 2× on HiDPI screens) while frames are quick, fewer pixels while a weak, old
// or software GPU struggles. Measured as the median frame interval while the camera moves (idle frames don't count).
const SOFTWARE_GL = /swiftshader|llvmpipe|softpipe|lavapipe|microsoft basic render|software/i;
let glStart = glInfo() || {};
let softwareGl = SOFTWARE_GL.test(glStart.renderer || '');

// A large/HiDPI window can exceed old GPUs' renderbuffer/viewport limits even at 1x. Limit the drawing buffer
// by capabilities, not vendor names; a pixel budget also bounds framebuffer memory on every GPU.
function pixelBounds(width = window.innerWidth, height = window.innerHeight) {
  const w = Math.max(width, 1), h = Math.max(height, 1);
  const limits = glStart.limits || {};
  const limit = (n) => Number.isFinite(n) && n > 0 ? n : Infinity;
  const budget = softwareGl ? 2 * 1024 * 1024 : 8 * 1024 * 1024;
  const max = Math.min(window.devicePixelRatio || 1, 2, limit(limits.renderbuffer) / w,
    limit(limits.renderbuffer) / h, limit(limits.texture) / w, limit(limits.texture) / h,
    limit(limits.viewport && limits.viewport[0]) / w, limit(limits.viewport && limits.viewport[1]) / h,
    Math.sqrt(budget / (w * h)));
  return { max, min: Math.min(softwareGl ? 0.5 : 0.75, max) };
}
let { max: MAX_RATIO, min: MIN_RATIO } = pixelBounds();
// ceiling: a ratio that was too slow; it is retried only after `need` smooth windows in a row (doubling per retry)
const quality = { ratio: softwareGl ? Math.min(1, MAX_RATIO) : MAX_RATIO, ceiling: Infinity, streak: 0, need: 3, changes: 0 };
renderer.setPixelRatio(quality.ratio);

/** Next quality state for the median frame interval of one window: < 20 fps gives up pixels, a smooth view takes
 *  them back. */
function nextPixelRatio(q, medianMs) {
  if (medianMs > 50) {
    if (q.ratio <= MIN_RATIO) return { ...q, streak: 0 };
    return { ...q, ratio: Math.max(MIN_RATIO, +(q.ratio * 0.75).toFixed(3)), ceiling: q.ratio, streak: 0 };
  }
  if (medianMs >= 22) return { ...q, streak: 0 };
  const up = Math.min(MAX_RATIO, +(q.ratio / 0.75).toFixed(3));
  if (up <= q.ratio) return q;
  if (up < q.ceiling) return { ...q, ratio: up, streak: 0 };
  const streak = q.streak + 1;
  if (streak >= q.need) return { ...q, ratio: up, ceiling: Infinity, streak: 0, need: q.need * 2 }; // try once more
  return { ...q, streak };
}

function adaptQuality(medianMs) {
  const before = quality.ratio;
  Object.assign(quality, nextPixelRatio(quality, medianMs));
  if (quality.ratio === before) return quality.ratio;
  quality.changes++;
  renderer.setPixelRatio(quality.ratio);
  resize();
  requestRender();
  return quality.ratio;
}

const frameGaps = [];
let lastFrameAt = 0;
function trackFrame(now, moving) {
  if (moving && lastFrameAt && now - lastFrameAt < 500) frameGaps.push(now - lastFrameAt);
  lastFrameAt = now;
  if (frameGaps.length < 30) return;
  frameGaps.sort((a, b) => a - b);
  const median = frameGaps[15];
  frameGaps.length = 0;
  adaptQuality(median);
}

const css = getComputedStyle(document.body);
const scene = new THREE.Scene();
scene.background = new THREE.Color(css.getPropertyValue('--vscode-editor-background').trim() || '#1e1e1e');

const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 1e7);
camera.up.set(0, 0, 1);
camera.position.set(200, -200, 160);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.15;
controls.screenSpacePanning = true;

scene.add(new THREE.HemisphereLight(0xffffff, 0x444455, 1.2));
const headLight = new THREE.DirectionalLight(0xffffff, 1.6);
camera.add(headLight);
headLight.position.set(1, 1, 2);
scene.add(camera);
const axes = new THREE.AxesHelper(20);
scene.add(axes);

const model = new THREE.Group();
const highlightGroup = new THREE.Group();
const resultGroup = new THREE.Group();
const markerGroup = new THREE.Group();
const tempGroup = new THREE.Group();
scene.add(model, highlightGroup, resultGroup, markerGroup, tempGroup);

let objectMeshes = [];      // one Mesh per FreeCAD object; userData.faces = [{name, tri0, triN, v0, vN}]
let edgeObjects = [];       // one LineSegments2 per object; userData.edges = [{name, seg0, segN}]
let vertices = [];          // {object, label, name, pos}
let showEdges = true;
let showMarkers = true;
let selectionKeys = new Set();
let highlights = new Map(); // "object|Face3" -> THREE.Color (DFM findings)
let hovered = null;         // {mesh, range}
let bbox = null;
let mode = 'select';
let pending = null;         // annotation being created
let stroke = null;          // pen stroke in progress
let markers = [];

const SELECT_COLOR = new THREE.Color('#2f8cff');
const SNAP_COLORS = { vertex: '#ff4d4f', edge: '#ffb020', face: '#3fb950' };
const SNAP_NAMES = { vertex: 'Köşe', edge: 'Kenar', face: 'Yüz' };
const KIND_COLORS = { line: 0x00c2ff, circle: 0xff3df5, pen: 0xffe14d };
const KIND_BG = { point: '#2f8cff', dimension: '#b26b00', line: '#0089b5', circle: '#b0129f', pen: '#9a8200' };
const SEVERITY_COLORS = { error: '#ff3b30', warning: '#ff9f0a', info: '#64d2ff' };

let needsRender = true;
const lineMaterials = new Set(); // LineMaterial needs the canvas size

function resize() {
  const w = Math.max(window.innerWidth, 1), h = Math.max(window.innerHeight, 1);
  const bounds = pixelBounds(w, h);
  MAX_RATIO = bounds.max; MIN_RATIO = bounds.min;
  quality.ratio = Math.max(MIN_RATIO, Math.min(quality.ratio, MAX_RATIO));
  if (renderer.getPixelRatio() !== quality.ratio) renderer.setPixelRatio(quality.ratio);
  renderer.setSize(w, h);
  requestRender();
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
  for (const m of lineMaterials) m.resolution.set(w, h);
}
window.addEventListener('resize', resize);

function lineMaterial(color, width) {
  const m = new LineMaterial({ color, linewidth: width, worldUnits: false });
  m.resolution.set(window.innerWidth, window.innerHeight);
  lineMaterials.add(m);
  return m;
}

function disposeGroup(group) {
  for (const c of [...group.children]) {
    group.remove(c);
    if (c.children && c.children.length) disposeGroup(c);
    if (c.geometry) { if (c.geometry.boundsTree) c.geometry.disposeBoundsTree(); c.geometry.dispose(); }
    if (c.material) {
      lineMaterials.delete(c.material);
      if (c.material.map) c.material.map.dispose();
      c.material.dispose();
    }
  }
}
resize();

// ---------------- section plane ----------------
const SECTION_AXES = [null, 'x', 'y', 'z'];
let section = { axis: null, plane: new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0), t: 0.5 };
const clipped = new Set(); // materials that follow the section plane

function clipMaterial(m) {
  clipped.add(m);
  m.clippingPlanes = section.axis ? [section.plane] : null;
  return m;
}

function updateSection() {
  const on = !!section.axis && !!bbox;
  if (on) {
    const i = { x: 0, y: 1, z: 2 }[section.axis];
    const n = new THREE.Vector3(); n.setComponent(i, -1);
    const pos = bbox.min[i] + (bbox.max[i] - bbox.min[i]) * section.t;
    section.plane.set(n, pos); // keeps the part of the model with coordinate < pos
  }
  for (const m of clipped) { m.clippingPlanes = on ? [section.plane] : null; m.needsUpdate = true; }
  $('vSection').textContent = on ? `Kesit ${section.axis.toUpperCase()}` : 'Kesit';
  $('vSection').classList.toggle('active', on);
  $('sectionBox').classList.toggle('hidden', !on);
}

// ---------------- model ----------------
// FreeCAD sends geometry as base64 float32/uint16/uint32 ("enc": "b64"); older add-ons send JSON number lists.
function b64bytes(s) {
  if (Uint8Array.fromBase64) return Uint8Array.fromBase64(s);
  const bin = atob(s), out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function floats(v) { if (typeof v !== 'string') return v || []; const b = b64bytes(v); return new Float32Array(b.buffer, b.byteOffset, b.byteLength / 4); }
function ints(v, wide) {
  if (typeof v !== 'string') return v || [];
  const b = b64bytes(v);
  return wide ? new Uint32Array(b.buffer, b.byteOffset, b.byteLength / 4) : new Uint16Array(b.buffer, b.byteOffset, b.byteLength / 2);
}

const objectCache = new Map(); // key -> {mesh, line, verts}: objects FreeCAD reported unchanged are reused as they are
const legacyBuilt = [];

function buildObject(obj) {
  const base = new THREE.Color(...obj.color);
  const faces = obj.faces.map((f) => ({ name: f.name, positions: floats(f.positions), indices: ints(f.indices, f.wide) }))
    .filter((f) => f.indices.length);
  const built = { mesh: null, line: null, verts: [] };
  let nV = 0, nI = 0;
  for (const f of faces) { nV += f.positions.length / 3; nI += f.indices.length; }
  if (nI) {
    const pos = new Float32Array(nV * 3), col = new Float32Array(nV * 3);
    const idx = nV > 65535 ? new Uint32Array(nI) : new Uint16Array(nI);
    const ranges = [];
    let v = 0, i = 0;
    for (const f of faces) {
      pos.set(f.positions, v * 3);
      for (let k = 0; k < f.indices.length; k++) idx[i + k] = f.indices[k] + v;
      ranges.push({ name: f.name, tri0: i / 3, triN: f.indices.length / 3, v0: v, vN: f.positions.length / 3 });
      v += f.positions.length / 3; i += f.indices.length;
    }
    for (let k = 0; k < nV; k++) base.toArray(col, k * 3);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    g.computeVertexNormals();
    g.computeBoundsTree();
    const m = clipMaterial(new THREE.MeshStandardMaterial({ vertexColors: true, metalness: 0.1, roughness: 0.65,
      side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
    built.mesh = new THREE.Mesh(g, m);
    built.mesh.userData = { object: obj.name, label: obj.label, faces: ranges, base };
  }
  const edges = obj.edges.map((e) => ({ name: e.name, points: floats(e.points) }));
  let nSeg = 0;
  for (const e of edges) nSeg += Math.max(e.points.length / 3 - 1, 0);
  if (nSeg) {
    const segs = new Float32Array(nSeg * 6), eranges = [];
    let s0 = 0;
    for (const e of edges) {
      const p = e.points, seg0 = s0;
      for (let k = 0; k + 5 < p.length; k += 3) { segs.set(p.subarray ? p.subarray(k, k + 6) : p.slice(k, k + 6), s0 * 6); s0++; }
      eranges.push({ name: e.name, seg0, segN: s0 - seg0 });
    }
    const lg = new LineSegmentsGeometry();
    lg.setPositions(segs);
    built.line = new LineSegments2(lg, clipMaterial(lineMaterial(0x111111, 1.6)));
    built.line.userData = { object: obj.name, label: obj.label, edges: eranges };
  }
  const vs = floats(obj.vertices);
  for (let i = 0; i < vs.length; i += 3) {
    built.verts.push({ object: obj.name, label: obj.label, name: `Vertex${i / 3 + 1}`, pos: new THREE.Vector3(vs[i], vs[i + 1], vs[i + 2]) });
  }
  return built;
}

function disposeBuilt(b) {
  for (const o of [b.mesh, b.line]) {
    if (!o) continue;
    model.remove(o);
    if (o.geometry.boundsTree) o.geometry.disposeBoundsTree();
    o.geometry.dispose();
    clipped.delete(o.material);
    lineMaterials.delete(o.material);
    o.material.dispose();
  }
}

/** Keys of the objects the viewer currently holds (the extension sends them back as `known`). */
function knownKeys() { return [...objectCache.keys()]; }

function buildScene(data) {
  const missing = data.objects.filter((o) => o.same && !objectCache.has(o.key));
  if (missing.length) { send({ type: 'needFullScene' }); return; } // the viewer lost its cache: resync
  for (const b of legacyBuilt.splice(0)) disposeBuilt(b);
  const keep = new Set(data.objects.map((o) => o.key).filter(Boolean));
  for (const [key, b] of objectCache) if (!keep.has(key)) { disposeBuilt(b); objectCache.delete(key); }
  objectMeshes = []; edgeObjects = []; vertices = []; hovered = null;
  bbox = data.bbox;
  let reused = 0;
  for (const obj of data.objects) {
    let b = obj.key ? objectCache.get(obj.key) : null;
    if (b && obj.same) reused++;
    else {
      if (b) disposeBuilt(b);
      b = buildObject(obj);
      if (obj.key) objectCache.set(obj.key, b);
      else legacyBuilt.push(b); // add-on without keys: rebuilt every time
      for (const o of [b.mesh, b.line]) if (o) model.add(o);
    }
    if (b.mesh) objectMeshes.push(b.mesh);
    if (b.line) { b.line.visible = showEdges && !result; edgeObjects.push(b.line); }
    vertices.push(...b.verts);
  }
  paintAll();
  drawHighlightEdges();
  drawMarkers();
  updateSection();
  lastBuild = { objects: data.objects.length, reused };
  $('info').textContent = data.doc ? `${data.label} · ${data.objects.length} parça` : 'Açık belge yok';
}
let lastBuild = null;

function findRange(list, key, n) { // binary search: last range whose list[k][key] <= n
  let lo = 0, hi = list.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (list[mid][key] <= n) lo = mid; else hi = mid - 1; }
  return list[lo];
}

/** The FreeCAD face under a raycast hit on a merged object mesh. */
function faceOf(hit) {
  const u = hit.object.userData;
  const range = findRange(u.faces, 'tri0', hit.faceIndex);
  return { object: u.object, label: u.label, face: range.name, range, mesh: hit.object };
}

function faceColor(u, range) {
  if (selectionKeys.has(`${u.object}|${range.name}`) || selectionKeys.has(`${u.object}|`)) return SELECT_COLOR;
  return highlights.get(`${u.object}|${range.name}`) || u.base;
}

function paintRange(mesh, range, color) {
  const attr = mesh.geometry.getAttribute('color');
  for (let k = range.v0; k < range.v0 + range.vN; k++) attr.setXYZ(k, color.r, color.g, color.b);
  attr.needsUpdate = true;
}

function paintAll() {
  for (const mesh of objectMeshes) {
    for (const range of mesh.userData.faces) paintRange(mesh, range, faceColor(mesh.userData, range));
  }
  if (hovered) paintRange(hovered.mesh, hovered.range, faceColor(hovered.mesh.userData, hovered.range).clone().lerp(new THREE.Color('#ffffff'), 0.22));
}

function setHover(f) {
  const same = hovered && f && hovered.mesh === f.mesh && hovered.range === f.range;
  if (same) return;
  if (hovered) paintRange(hovered.mesh, hovered.range, faceColor(hovered.mesh.userData, hovered.range));
  hovered = f ? { mesh: f.mesh, range: f.range } : null;
  if (hovered) paintRange(hovered.mesh, hovered.range, faceColor(f.mesh.userData, f.range).clone().lerp(new THREE.Color('#ffffff'), 0.22));
}

// ---------------- DFM highlights ----------------
let highlightEdges = []; // [{object, edge, color}]

function setHighlights(items) {
  highlights = new Map();
  highlightEdges = [];
  for (const it of items || []) {
    const color = new THREE.Color(it.color || SEVERITY_COLORS[it.severity] || '#ff3b30');
    for (const el of it.elements || []) {
      if (/^Face\d+$/.test(el)) { if (!highlights.has(`${it.object}|${el}`)) highlights.set(`${it.object}|${el}`, color); }
      else if (/^Edge\d+$/.test(el)) highlightEdges.push({ object: it.object, edge: el, color });
    }
  }
  paintAll();
  drawHighlightEdges();
}

function drawHighlightEdges() {
  disposeGroup(highlightGroup);
  for (const h of highlightEdges) {
    const line = edgeObjects.find((l) => l.userData.object === h.object);
    const r = line && line.userData.edges.find((e) => e.name === h.edge);
    if (!r) continue;
    const start = line.geometry.attributes.instanceStart, end = line.geometry.attributes.instanceEnd;
    const segs = [];
    for (let s = r.seg0; s < r.seg0 + r.segN; s++) segs.push(start.getX(s), start.getY(s), start.getZ(s), end.getX(s), end.getY(s), end.getZ(s));
    const lg = new LineSegmentsGeometry();
    lg.setPositions(segs);
    const m = lineMaterial(h.color, 5);
    m.depthTest = false;
    const l = new LineSegments2(lg, m);
    l.renderOrder = 7;
    highlightGroup.add(l);
  }
}

// ---------------- FEM result color map ----------------
let result = null; // {field, meshes, lut, factor}
const QUANTITY = { von_mises: 'von Mises gerilmesi', displacement: 'Yer değiştirme' };

function resultRange(field) {
  const hi = $('rClamp').checked ? field.p99 : field.max;
  return [field.min, hi > field.min ? hi : field.min + 1e-9];
}

function autoFactor(field) {
  const diag = bbox ? new THREE.Vector3(...bbox.min).distanceTo(new THREE.Vector3(...bbox.max)) : 100;
  return field.max_displacement_mm > 0 ? (0.1 * diag) / field.max_displacement_mm : 0;
}

function showResult(field) {
  clearResult(false);
  const lut = new Lut('rainbow', 512);
  result = { field, meshes: [], lut, factor: 0 };
  for (const f of field.faces) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(f.positions), 3));
    g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(f.values.length * 3), 3));
    g.setIndex(f.indices);
    const m = clipMaterial(new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide }));
    m.userData.result = true;
    const mesh = new THREE.Mesh(g, m);
    mesh.userData = { face: f.name, base: Float32Array.from(f.positions), disp: Float32Array.from(f.displacements), values: f.values };
    resultGroup.add(mesh);
    result.meshes.push(mesh);
  }
  model.visible = false;
  $('resultBox').classList.remove('hidden');
  $('rTitle').textContent = `${QUANTITY[field.quantity] || field.quantity} (${field.unit})`
    + (field.frequency_hz ? ` · mod ${field.mode}: ${fmt(field.frequency_hz)} Hz` : '');
  $('rVM').classList.toggle('active', field.quantity === 'von_mises');
  $('rDisp').classList.toggle('active', field.quantity === 'displacement');
  recolorResult();
  deformResult();
  updateSection();
}

// three.js Lut.getColor(max) indexes one past its table (round(1 * n)); fall back to the last entry.
function lutColor(v) { return result.lut.getColor(v) || result.lut.lut[result.lut.lut.length - 1]; }

function recolorResult() {
  if (!result) return;
  const [lo, hi] = resultRange(result.field);
  result.lut.setMin(lo); result.lut.setMax(hi);
  for (const mesh of result.meshes) {
    const col = mesh.geometry.getAttribute('color');
    mesh.userData.values.forEach((v, k) => { const c = lutColor(Math.min(Math.max(v, lo), hi)); col.setXYZ(k, c.r, c.g, c.b); });
    col.needsUpdate = true;
  }
  drawLegend(lo, hi);
}

function deformResult() {
  if (!result) return;
  const pct = Number($('rScale').value);
  result.factor = autoFactor(result.field) * pct / 100;
  for (const mesh of result.meshes) {
    const pos = mesh.geometry.getAttribute('position'), { base, disp } = mesh.userData;
    for (let k = 0; k < base.length; k++) pos.array[k] = base[k] + disp[k] * result.factor;
    pos.needsUpdate = true;
    mesh.geometry.computeVertexNormals();
    if (mesh.geometry.boundsTree) mesh.geometry.disposeBoundsTree();
    mesh.geometry.computeBoundsTree();
    mesh.geometry.computeBoundingSphere();
  }
  $('rScaleText').textContent = result.factor ? `×${fmt(result.factor)}` : 'kapalı';
}

function drawLegend(lo, hi) {
  const c = $('rBar'), ctx = c.getContext('2d');
  for (let y = 0; y < c.height; y++) {
    ctx.fillStyle = lutColor(hi - (hi - lo) * (y / (c.height - 1))).getStyle();
    ctx.fillRect(0, y, c.width, 1);
  }
  const ticks = [];
  for (let i = 0; i <= 5; i++) ticks.push(`<div>${fmt(hi - (hi - lo) * (i / 5))}</div>`);
  $('rTicks').innerHTML = ticks.join('');
  const f = result.field;
  $('rStats').textContent = `en büyük ${fmt(f.max)} · %99 ${fmt(f.p99)} ${f.unit}` + ($('rClamp').checked && f.max > f.p99 ? ' (renk %99\'da kırpıldı)' : '');
}

function clearResult(showModel = true) {
  if (result) for (const mesh of result.meshes) clipped.delete(mesh.material);
  disposeGroup(resultGroup);
  result = null;
  if (showModel) {
    model.visible = true;
    for (const l of edgeObjects) l.visible = showEdges;
    $('resultBox').classList.add('hidden');
  }
}

function resultValueAt(hit) {
  const mesh = hit.object, { a, b, c } = hit.face;
  const pos = mesh.geometry.getAttribute('position');
  const A = new THREE.Vector3().fromBufferAttribute(pos, a), B = new THREE.Vector3().fromBufferAttribute(pos, b);
  const C = new THREE.Vector3().fromBufferAttribute(pos, c);
  const w = THREE.Triangle.getBarycoord(hit.point, A, B, C, new THREE.Vector3());
  const v = mesh.userData.values;
  return w ? v[a] * w.x + v[b] * w.y + v[c] * w.z : v[a];
}

// ---------------- camera ----------------
function center() {
  if (!bbox) return { c: new THREE.Vector3(), r: 100 };
  const min = new THREE.Vector3(...bbox.min), max = new THREE.Vector3(...bbox.max);
  return { c: min.clone().add(max).multiplyScalar(0.5), r: Math.max(min.distanceTo(max) / 2, 1) };
}

function setView(dir) {
  const { c, r } = center();
  const d = r / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.15;
  const v = new THREE.Vector3(...dir).normalize();
  camera.position.copy(c.clone().add(v.multiplyScalar(d)));
  camera.up.set(0, 0, 1);
  if (Math.abs(dir[2]) > 0.99) camera.up.set(0, 1, 0);
  controls.target.copy(c);
  camera.near = Math.max(d / 1000, 0.01);
  camera.far = d * 100;
  camera.updateProjectionMatrix();
  controls.update();
  axes.scale.setScalar(r / 40);
}
const VIEWS = { iso: [1, -1, 0.8], top: [0, 0, 1], front: [0, -1, 0], right: [1, 0, 0] };

function worldPerPixel(point) {
  const dist = camera.position.distanceTo(point);
  return 2 * dist * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) / renderer.domElement.clientHeight;
}

function annoRadius() { return Math.max(center().r * 0.005, 0.02); }

// ---------------- picking & snapping ----------------
const raycaster = new THREE.Raycaster();
raycaster.firstHitOnly = true; // three-mesh-bvh: stop at the nearest triangle
raycaster.params.Line2 = { threshold: 6 };
const ndc = new THREE.Vector2();

function setRay(ev) {
  const rect = renderer.domElement.getBoundingClientRect();
  ndc.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  return rect;
}

function clippedAway(p) { return section.axis && bbox && section.plane.distanceToPoint(p) < 0; }

function firstVisible(hits) { return hits.find((h) => !clippedAway(h.point)) || null; }

function faceHit(ev) {
  setRay(ev);
  if (!model.visible) return null;
  raycaster.firstHitOnly = !section.axis;
  const hit = firstVisible(raycaster.intersectObjects(objectMeshes, false));
  raycaster.firstHitOnly = true;
  return hit;
}

function hitNormal(hit) {
  return hit.face.normal.clone().transformDirection(hit.object.matrixWorld).normalize();
}

// Priority: vertex (within 10 px) > edge (within 6 px) > face. Hidden (occluded) vertices/edges are ignored.
function snap(ev) {
  const hit = faceHit(ev);
  const rect = renderer.domElement.getBoundingClientRect();
  const limit = hit ? hit.distance * 1.002 + worldPerPixel(hit.point) * 2 : Infinity;

  let best = null;
  const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
  for (const v of vertices) {
    if (clippedAway(v.pos)) continue;
    const s = v.pos.clone().project(camera);
    const sx = (s.x + 1) / 2 * rect.width, sy = (1 - s.y) / 2 * rect.height;
    const d = Math.hypot(sx - px, sy - py);
    if (d < 10 && (!best || d < best.d) && camera.position.distanceTo(v.pos) <= limit + worldPerPixel(v.pos) * 4) {
      best = { d, v };
    }
  }
  const faceNormal = hit ? hitNormal(hit).toArray().map((x) => +x.toFixed(4)) : undefined;
  if (best) {
    const v = best.v;
    return { snap: 'vertex', object: v.object, label: v.label, element: v.name, point: v.pos.toArray(), faceNormal };
  }
  if (showEdges && model.visible) {
    const eh = raycaster.intersectObjects(edgeObjects, false)
      .filter((h) => h.distance <= limit && !clippedAway(h.pointOnLine || h.point)).sort((a, b) => a.distance - b.distance)[0];
    if (eh) {
      const u = eh.object.userData;
      const r = findRange(u.edges, 'seg0', eh.faceIndex);
      const p = (eh.pointOnLine || eh.point).toArray();
      return { snap: 'edge', object: u.object, label: u.label, element: r.name, point: p, faceNormal };
    }
  }
  if (hit) {
    const f = faceOf(hit);
    return { snap: 'face', object: f.object, label: f.label, element: f.face, point: hit.point.toArray(), normal: faceNormal };
  }
  return null;
}

function cleanTarget(s) {
  const t = { snap: s.snap, object: s.object, label: s.label, element: s.element, point: s.point.map((x) => +x.toFixed(4)) };
  if (s.normal) t.normal = s.normal;
  return t;
}

// ---------------- drawing helpers ----------------
function labelSprite(text, bg = '#2f8cff', fg = '#ffffff') {
  const pad = 10, font = 'bold 36px sans-serif';
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width) + pad * 2, h = 52;
  c.width = w; c.height = h;
  ctx.font = font;
  ctx.fillStyle = bg;
  const r = 14;
  ctx.beginPath();
  ctx.moveTo(r, 0); ctx.arcTo(w, 0, w, h, r); ctx.arcTo(w, h, 0, h, r); ctx.arcTo(0, h, 0, 0, r); ctx.arcTo(0, 0, w, 0, r);
  ctx.fill();
  ctx.fillStyle = fg;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, pad, h / 2 + 2);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, sizeAttenuation: false }));
  const scale = 0.032;
  s.scale.set(scale * w / h, scale, 1);
  s.center.set(0, 0);
  s.renderOrder = 10;
  return s;
}

function dotSprite(color) {
  const c = document.createElement('canvas');
  c.width = c.height = 32;
  const ctx = c.getContext('2d');
  ctx.fillStyle = color;
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 4;
  ctx.beginPath(); ctx.arc(16, 16, 11, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  const tex = new THREE.CanvasTexture(c);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, sizeAttenuation: false }));
  s.scale.set(0.022, 0.022, 1);
  s.renderOrder = 11;
  return s;
}

function lineBetween(a, b, color) {
  const g = new THREE.BufferGeometry().setFromPoints([a, b]);
  const l = new THREE.Line(g, new THREE.LineDashedMaterial({ color, dashSize: 1, gapSize: 0.6, depthTest: false }));
  l.computeLineDistances();
  const len = a.distanceTo(b);
  l.material.dashSize = len / 20; l.material.gapSize = len / 40;
  l.renderOrder = 9;
  return l;
}

// Thick polyline drawn as a tube (on the model, so it shades with it).
function tube(points, color, radius = annoRadius()) {
  const pts = [];
  for (const p of points) if (!pts.length || pts[pts.length - 1].distanceTo(p) > radius * 0.05) pts.push(p);
  if (pts.length < 2) return null;
  const path = new THREE.CurvePath();
  for (let i = 0; i < pts.length - 1; i++) path.add(new THREE.LineCurve3(pts[i], pts[i + 1]));
  const g = new THREE.TubeGeometry(path, Math.min(pts.length * 3, 3000), radius, 6, false);
  const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color }));
  m.renderOrder = 8;
  return m;
}

function circlePoints(c, n, radius, segments = 72) {
  const normal = n.clone().normalize();
  const u = Math.abs(normal.z) < 0.9 ? new THREE.Vector3(0, 0, 1).cross(normal).normalize()
    : new THREE.Vector3(1, 0, 0).cross(normal).normalize();
  const v = normal.clone().cross(u);
  const pts = [];
  for (let i = 0; i <= segments; i++) {
    const t = (i / segments) * Math.PI * 2;
    pts.push(c.clone().add(u.clone().multiplyScalar(Math.cos(t) * radius)).add(v.clone().multiplyScalar(Math.sin(t) * radius)));
  }
  return { pts, u };
}

function fmt(n) {
  const a = Math.abs(Number(n));
  const d = a !== 0 && a < 0.01 ? 5 : a < 1 ? 4 : 2;
  return Number(n).toLocaleString('tr-TR', { maximumFractionDigits: d });
}
function polyLength(pts) { let s = 0; for (let i = 1; i < pts.length; i++) s += pts[i].distanceTo(pts[i - 1]); return s; }
const V = (a) => new THREE.Vector3(...a);

// ---------------- annotations (saved) ----------------
function addLabel(group, text, pos, kind) {
  const lab = labelSprite(text, KIND_BG[kind] || '#2f8cff');
  lab.position.copy(pos);
  group.add(lab);
}

function drawAnnotation(group, m) {
  const kind = m.kind || 'point';
  if (kind === 'point') {
    const dot = dotSprite(SNAP_COLORS[m.a.snap] || '#2f8cff');
    dot.position.copy(V(m.a.point));
    group.add(dot);
    addLabel(group, `#${m.id}`, V(m.a.point), kind);
  } else if (kind === 'dimension') {
    const a = V(m.a.point), b = V(m.b.point);
    for (const t of [m.a, m.b]) { const d = dotSprite(SNAP_COLORS[t.snap] || '#2f8cff'); d.position.copy(V(t.point)); group.add(d); }
    group.add(lineBetween(a, b, 0xffb020));
    addLabel(group, `#${m.id} ${fmt(m.distance)} mm`, a.clone().add(b).multiplyScalar(0.5), kind);
  } else if (kind === 'line') {
    const pts = m.points.map((t) => V(t.point));
    const t = tube(pts, KIND_COLORS.line);
    if (t) group.add(t);
    for (const p of pts) { const d = dotSprite('#00c2ff'); d.position.copy(p); d.scale.multiplyScalar(0.7); group.add(d); }
    addLabel(group, `#${m.id}`, pts[0], kind);
  } else if (kind === 'circle') {
    const c = V(m.a.point), n = V(m.normal).normalize();
    const { pts, u } = circlePoints(c.clone().add(n.clone().multiplyScalar(annoRadius())), n, m.radius);
    const t = tube(pts, KIND_COLORS.circle);
    if (t) group.add(t);
    const dot = dotSprite('#ff3df5'); dot.position.copy(c); dot.scale.multiplyScalar(0.7); group.add(dot);
    addLabel(group, `#${m.id} Ø${fmt(m.radius * 2)}`, c.clone().add(u.multiplyScalar(m.radius)), kind);
  } else if (kind === 'pen') {
    const pts = m.path.map(V);
    const t = tube(pts, KIND_COLORS.pen);
    if (t) group.add(t);
    addLabel(group, `#${m.id}`, pts[0], kind);
  }
}

function drawMarkers() {
  disposeGroup(markerGroup);
  markerGroup.visible = showMarkers;
  for (const m of markers) {
    try { drawAnnotation(markerGroup, m); } catch (e) { /* skip malformed annotation */ }
  }
}

// ---------------- in-progress visuals ----------------
const snapDot = dotSprite('#ffffff');
snapDot.visible = false;
scene.add(snapDot);
let rubber = null;  // dashed line / preview circle following the cursor

function showSnap(s) {
  if (!s) { snapDot.visible = false; return; }
  snapDot.material.map.dispose();
  snapDot.material.dispose();
  snapDot.material = dotSprite(SNAP_COLORS[s.snap]).material;
  snapDot.position.set(...s.point);
  snapDot.visible = true;
  $('info').textContent = `${SNAP_NAMES[s.snap]}: ${s.label} · ${s.element}  (${s.point.map(fmt).join(', ')})`;
}

function setRubber(obj) {
  if (rubber) { tempGroup.remove(rubber); rubber.geometry.dispose(); rubber.material.dispose(); }
  rubber = obj;
  if (obj) tempGroup.add(obj);
}

function drawPending() {
  setRubber(null);
  disposeGroup(tempGroup);
  rubber = null;
  if (!pending) return;
  const tmp = Object.assign({ id: '…' }, pending);
  if (pending.kind === 'line' && pending.points.length === 1) {
    const d = dotSprite('#00c2ff'); d.position.copy(V(pending.points[0].point)); tempGroup.add(d);
    return;
  }
  if (pending.kind === 'circle' && !pending.radius) {
    const d = dotSprite('#ff3df5'); d.position.copy(V(pending.a.point)); tempGroup.add(d);
    return;
  }
  if (pending.kind === 'dimension' && !pending.b) {
    const d = dotSprite(SNAP_COLORS[pending.a.snap]); d.position.copy(V(pending.a.point)); tempGroup.add(d);
    return;
  }
  drawAnnotation(tempGroup, tmp);
}

// Labels come from the model file: always escape before putting them into HTML.
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function describe(t) { return `${esc(t.label)} · ${esc(t.element)} (${SNAP_NAMES[t.snap].toLowerCase()})`; }

const PLACEHOLDERS = {
  point: 'Burada ne değişsin? Örn: bu kenarı 5 mm kısalt, buraya Ø8 delik aç…',
  dimension: 'Ne olmalı? Örn: bu mesafe 40 mm olsun',
  line: 'Bu çizgi ne? Örn: buradan kes, bu hat boyunca 2 mm kanal aç, buraya pah…',
  circle: 'Bu daire ne? Örn: buraya bu çapta delik aç, M6 diş, 10 mm boss…',
  pen: 'Çizdiğin yer ne? Örn: bu bölgeyi 2 mm incelt, buraya nervür ekle…',
};

function openNoteForm() {
  const p = pending;
  let title;
  if (p.kind === 'dimension') {
    const d = p.delta.map(fmt);
    title = `Ölçü: <b>${fmt(p.distance)} mm</b> &nbsp; ΔX ${d[0]} · ΔY ${d[1]} · ΔZ ${d[2]}<br>${describe(p.a)} → ${describe(p.b)}`;
  } else if (p.kind === 'line') {
    title = `Çizgi: <b>${p.points.length} nokta, ${fmt(p.length)} mm</b><br>${describe(p.points[0])} → ${describe(p.points[p.points.length - 1])}`;
  } else if (p.kind === 'circle') {
    title = `Daire: <b>Ø${fmt(p.radius * 2)} mm</b> (yarıçap ${fmt(p.radius)})<br>merkez ${describe(p.a)}`;
  } else if (p.kind === 'pen') {
    title = `Kalem izi: <b>${fmt(p.length)} mm</b>, ${p.faces.map((f) => `${esc(f.label)} · ${esc(f.element)}`).join(', ')}`;
  } else {
    title = `İşaret: <b>${describe(p.a)}</b>`;
  }
  $('noteTitle').innerHTML = title;
  $('noteInput').placeholder = PLACEHOLDERS[p.kind] || PLACEHOLDERS.point;
  $('noteInput').value = '';
  $('noteForm').classList.remove('hidden');
  setTimeout(() => $('noteInput').focus(), 0);
}

function formOpen() { return !$('noteForm').classList.contains('hidden'); }

function cancelPending() {
  pending = null;
  stroke = null;
  controls.enabled = true;
  drawPending();
  $('noteForm').classList.add('hidden');
  updateHint();
}

$('noteForm').addEventListener('submit', (e) => {
  e.preventDefault();
  if (!pending) return;
  send({ type: 'addMarker', marker: Object.assign({}, pending, { note: $('noteInput').value.trim() }) });
  cancelPending();
});
$('noteCancel').onclick = cancelPending;

function finishLine() {
  if (!pending || pending.kind !== 'line' || pending.points.length < 2) return;
  pending.length = +polyLength(pending.points.map((t) => V(t.point))).toFixed(4);
  drawPending();
  openNoteForm();
}

// ---------------- modes ----------------
const HINTS = {
  select: 'Seç: yüze tıkla (Ctrl ile çoklu). Sürükle = döndür, sağ tık sürükle = kaydır, tekerlek = yakınlaştır.',
  mark: 'İşaret: modelde bir noktaya tıkla (köşe ve kenarlara yapışır), sonra orada ne istediğini yaz.',
  measure: 'Ölçü: ilk noktaya tıkla.',
  line: 'Çizgi: noktalara sırayla tıkla; Enter ya da çift tık ile bitir, Backspace son noktayı siler.',
  circle: 'Daire: merkeze tıkla.',
  pen: 'Kalem: yüzeyin üzerinde fareyi basılı tutup çiz. Boşlukta sürüklersen görünüm döner.',
};
function updateHint() {
  let h = HINTS[mode];
  if (mode === 'measure' && pending && !pending.b) h = 'Ölçü: ikinci noktaya tıkla.';
  if (mode === 'circle' && pending && !pending.radius) h = 'Daire: yarıçap kadar uzaktaki bir noktaya tıkla.';
  if (mode === 'line' && pending) h = `Çizgi: ${pending.points.length} nokta. Devam et; Enter / çift tık = bitir, Backspace = geri.`;
  if (result) h = 'Sonuç görünümü: imleçle değeri oku. Modele dönmek için "Kapat" ya da Esc.';
  $('hint').textContent = h;
}
const MODE_BUTTONS = [['mSelect', 'select'], ['mMark', 'mark'], ['mMeasure', 'measure'], ['mLine', 'line'],
  ['mCircle', 'circle'], ['mPen', 'pen']];
function setMode(m) {
  if (m !== 'select' && result) clearResult();
  mode = m;
  cancelPending();
  showSnap(null);
  for (const [id, name] of MODE_BUTTONS) $(id).classList.toggle('active', mode === name);
  renderer.domElement.style.cursor = mode === 'select' ? 'default' : 'crosshair';
  updateHint();
}

// ---------------- input ----------------
let down = null;

// Capture phase on the canvas runs before OrbitControls, so a pen stroke on the model does not rotate the view.
renderer.domElement.addEventListener('pointerdown', (e) => {
  down = { x: e.clientX, y: e.clientY };
  if (mode !== 'pen' || e.button !== 0 || formOpen()) return;
  const hit = faceHit(e);
  if (!hit) return;
  const f = faceOf(hit);
  controls.enabled = false;
  stroke = { pts: [hit.point.clone().add(hitNormal(hit).multiplyScalar(annoRadius()))], last: { x: e.clientX, y: e.clientY },
    faces: new Map([[`${f.object}|${f.face}`, f]]) };
}, { capture: true });

function finishStroke() {
  const s = stroke;
  stroke = null;
  controls.enabled = true;
  if (!s || s.pts.length < 2) { drawPending(); return; }
  pending = { kind: 'pen', path: s.pts.map((p) => p.toArray().map((x) => +x.toFixed(3))),
    faces: [...s.faces.values()].map((f) => ({ object: f.object, label: f.label, element: f.face })),
    length: +polyLength(s.pts).toFixed(3) };
  drawPending();
  openNoteForm();
}

renderer.domElement.addEventListener('pointerup', (e) => {
  if (stroke) { finishStroke(); down = null; return; }
  if (!down || e.button !== 0) return;
  const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
  down = null;
  if (moved > 4) return; // drag = orbit
  if (mode === 'select') {
    if (result) return;
    const hit = faceHit(e);
    const additive = e.ctrlKey || e.shiftKey || e.metaKey;
    if (hit) { const f = faceOf(hit); send({ type: 'pick', object: f.object, sub: f.face, additive }); }
    else if (!additive) send({ type: 'pick', object: null });
    return;
  }
  if (formOpen() || mode === 'pen') return;
  const s = snap(e);

  if (mode === 'circle' && pending && pending.kind === 'circle' && !pending.radius) {
    const c = V(pending.a.point), n = V(pending.normal).normalize();
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(n, c);
    const onPlane = new THREE.Vector3();
    if (!raycaster.ray.intersectPlane(plane, onPlane)) return;
    const radius = s ? (() => { const p = V(s.point); return p.sub(n.clone().multiplyScalar(plane.distanceToPoint(p))).distanceTo(c); })()
      : onPlane.distanceTo(c);
    if (radius < 1e-6) return;
    pending.radius = +radius.toFixed(4);
    drawPending();
    openNoteForm();
    return;
  }
  if (!s) return;
  if (mode === 'mark') {
    pending = { kind: 'point', a: cleanTarget(s) };
    drawPending();
    openNoteForm();
  } else if (mode === 'measure') {
    if (!pending) {
      pending = { kind: 'dimension', a: cleanTarget(s) };
      drawPending();
    } else {
      const a = V(pending.a.point), b = V(s.point);
      pending.b = cleanTarget(s);
      pending.distance = +a.distanceTo(b).toFixed(4);
      pending.delta = b.clone().sub(a).toArray().map((x) => +x.toFixed(4));
      drawPending();
      openNoteForm();
    }
  } else if (mode === 'line') {
    if (!pending) pending = { kind: 'line', points: [] };
    const last = pending.points[pending.points.length - 1];
    if (last && V(last.point).distanceTo(V(s.point)) < worldPerPixel(V(s.point)) * 3) return; // double click
    pending.points.push(cleanTarget(s));
    drawPending();
  } else if (mode === 'circle') {
    const normal = s.normal || s.faceNormal;
    if (!normal) return; // need a face to know the circle's plane
    pending = { kind: 'circle', a: cleanTarget(s), normal };
    drawPending();
  }
  updateHint();
});

renderer.domElement.addEventListener('dblclick', () => { if (mode === 'line') finishLine(); });

renderer.domElement.addEventListener('pointermove', (e) => {
  if (stroke) {
    if (Math.hypot(e.clientX - stroke.last.x, e.clientY - stroke.last.y) < 3) return;
    const hit = faceHit(e);
    if (!hit) return;
    const f = faceOf(hit);
    stroke.last = { x: e.clientX, y: e.clientY };
    stroke.pts.push(hit.point.clone().add(hitNormal(hit).multiplyScalar(annoRadius())));
    stroke.faces.set(`${f.object}|${f.face}`, f);
    setRubber(tube(stroke.pts, KIND_COLORS.pen));
    return;
  }
  if (result) {
    setRay(e);
    const hit = firstVisible(raycaster.intersectObjects(result.meshes, false));
    if (hit) {
      $('info').textContent = `${hit.object.userData.face} · ${QUANTITY[result.field.quantity] || ''}: `
        + `${fmt(resultValueAt(hit))} ${result.field.unit}`;
    }
    return;
  }
  if (mode === 'select') {
    const hit = faceHit(e);
    const f = hit ? faceOf(hit) : null;
    setHover(f);
    if (f) $('info').textContent = `${f.label} · ${f.face}`;
    return;
  }
  if (formOpen()) return;
  if (mode === 'pen') {
    const hit = faceHit(e);
    renderer.domElement.style.cursor = hit ? 'crosshair' : 'grab';
    return;
  }
  const s = snap(e);
  showSnap(s);
  // rubber band previews
  if (mode === 'circle' && pending && !pending.radius) {
    const c = V(pending.a.point), n = V(pending.normal).normalize();
    const onPlane = new THREE.Vector3();
    if (raycaster.ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n, c), onPlane)) {
      const radius = onPlane.distanceTo(c);
      if (radius > 1e-6) {
        setRubber(tube(circlePoints(c.clone().add(n.clone().multiplyScalar(annoRadius())), n, radius).pts, KIND_COLORS.circle));
        $('info').textContent = `Ø${fmt(radius * 2)} mm`;
      }
    }
  } else if ((mode === 'line' && pending && pending.points.length) || (mode === 'measure' && pending && !pending.b)) {
    const from = V(mode === 'line' ? pending.points[pending.points.length - 1].point : pending.a.point);
    const to = s ? V(s.point) : null;
    if (to) {
      setRubber(lineBetween(from, to, mode === 'line' ? 0x00c2ff : 0xffb020));
      $('info').textContent += `   ← ${fmt(from.distanceTo(to))} mm`;
    }
  }
});

renderer.domElement.addEventListener('pointerleave', () => setHover(null));

window.addEventListener('keydown', (e) => {
  if (e.target === $('noteInput')) {
    if (e.key === 'Escape') cancelPending();
    return;
  }
  if (e.target && e.target.tagName === 'INPUT') return;
  const k = e.key.toLowerCase();
  if ((e.ctrlKey || e.metaKey) && k === 'z') send({ type: 'command', command: 'cadai.undo' });
  else if ((e.ctrlKey || e.metaKey) && k === 'y') send({ type: 'command', command: 'cadai.redo' });
  else if (e.key === 'Enter' && mode === 'line') finishLine();
  else if (e.key === 'Backspace' && mode === 'line' && pending) {
    pending.points.pop();
    if (!pending.points.length) pending = null;
    drawPending();
    updateHint();
  } else if (k === 'f') setView(VIEWS.iso);
  else if (k === 'm') setMode('mark');
  else if (k === 'd') setMode('measure');
  else if (k === 'l') setMode('line');
  else if (k === 'c') setMode('circle');
  else if (k === 'p') setMode('pen');
  else if (k === 's') setMode('select');
  else if (k === 'x') cycleSection();
  else if (e.key === 'Escape') {
    if (pending || stroke) cancelPending();
    else if (result) clearResult();
    else if (mode !== 'select') setMode('select');
    else send({ type: 'pick', object: null });
  }
});

function cycleSection() {
  section.axis = SECTION_AXES[(SECTION_AXES.indexOf(section.axis) + 1) % SECTION_AXES.length];
  updateSection();
}

for (const [id, name] of MODE_BUTTONS) $(id).onclick = () => setMode(name);
$('vFit').onclick = () => setView(VIEWS.iso);
$('vIso').onclick = () => setView(VIEWS.iso);
$('vTop').onclick = () => setView(VIEWS.top);
$('vFront').onclick = () => setView(VIEWS.front);
$('vRight').onclick = () => setView(VIEWS.right);
$('vEdges').onclick = () => { showEdges = !showEdges; edgeObjects.forEach((l) => { l.visible = showEdges && !result; }); };
$('vMarkers').onclick = () => { showMarkers = !showMarkers; markerGroup.visible = showMarkers; };
$('vSection').onclick = cycleSection;
$('sectionPos').oninput = () => { section.t = Number($('sectionPos').value) / 1000; updateSection(); };
$('vUndo').onclick = () => send({ type: 'command', command: 'cadai.undo' });
$('vRedo').onclick = () => send({ type: 'command', command: 'cadai.redo' });
$('vStart').onclick = () => send({ type: 'command', command: 'cadai.startFreeCAD' });
$('rVM').onclick = () => send({ type: 'femField', quantity: 'von_mises' });
$('rDisp').onclick = () => send({ type: 'femField', quantity: 'displacement' });
$('rClamp').onchange = recolorResult;
$('rScale').oninput = deformResult;
$('rClose').onclick = () => { clearResult(); updateHint(); };
$('hClear').onclick = () => { setHighlights([]); $('hBox').classList.add('hidden'); };

window.addEventListener('message', (e) => {
  const m = e.data;
  if (m.type === 'scene') {
    viewTarget = m.scene.context || null;
    buildScene(m.scene);
    selectionKeys = new Set((m.scene.selection || []).map((s) => `${s.object}|${s.sub || ''}`));
    paintAll();
    if (m.fit) setView(VIEWS.iso);
  } else if (m.type === 'selection') {
    selectionKeys = new Set((m.items || []).map((s) => `${s.object}|${s.sub || ''}`));
    paintAll();
  } else if (m.type === 'markers') {
    markers = m.items || [];
    drawMarkers();
  } else if (m.type === 'mode') {
    setMode(m.mode);
  } else if (m.type === 'femField') {
    setMode('select');
    showResult(m.field);
    updateHint();
  } else if (m.type === 'highlight') {
    setHighlights(m.items);
    $('hBox').classList.toggle('hidden', !(m.items || []).length);
    $('hText').textContent = m.title || '';
  } else if (m.type === 'connection') {
    $('overlay').classList.toggle('hidden', m.connected);
  }
});

// Render on demand: an idle view costs no GPU time (the old loop drew 60+ frames per second forever).
// Anything that can change the picture arrives as an input/message event, a controls change or damping.
function requestRender() { needsRender = true; }
controls.addEventListener('change', requestRender);
for (const ev of ['pointerdown', 'pointermove', 'pointerup', 'wheel', 'keydown', 'input', 'change', 'click', 'message', 'resize']) {
  window.addEventListener(ev, requestRender, { capture: true, passive: true });
}

function loop(now) {
  requestAnimationFrame(loop);
  if (contextLost) return;
  const moving = controls.update(); // still moving (damping)
  if (moving) needsRender = true;
  if (!needsRender) return;
  needsRender = false;
  renderer.render(scene, camera);
  trackFrame(now, moving);
}

function glInfo() {
  try {
    const gl = renderer.getContext();
    let r = gl.getParameter(gl.RENDERER), v = gl.getParameter(gl.VENDOR);
    // Unmasked names can be unavailable for privacy reasons. Generic names must still render normally.
    try {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      if (ext) { r = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL); v = gl.getParameter(ext.UNMASKED_VENDOR_WEBGL); }
    } catch (_) { /* driver or browser hides hardware names */ }
    return { renderer: r, vendor: v, limits: {
      renderbuffer: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), texture: gl.getParameter(gl.MAX_TEXTURE_SIZE),
      viewport: Array.from(gl.getParameter(gl.MAX_VIEWPORT_DIMS) || []),
    } };
  } catch (_) { return null; }
}

// test hook (used by the headless browser test; harmless in VS Code)
window.__cadai = { THREE, camera, renderer, markerGroup, controls, setView, VIEWS, getMode: () => mode,
  objectMeshes: () => objectMeshes, edgeObjects: () => edgeObjects, highlights: () => highlights,
  result: () => result, section: () => section, knownKeys, lastBuild: () => lastBuild, quality: () => ({ ...quality,
    min: MIN_RATIO, max: MAX_RATIO, software: softwareGl }), adaptQuality, faceColorOf: (object, face) => {
    const mesh = objectMeshes.find((x) => x.userData.object === object);
    const r = mesh && mesh.userData.faces.find((f) => f.name === face);
    if (!r) return null;
    const c = mesh.geometry.getAttribute('color');
    return [c.getX(r.v0), c.getY(r.v0), c.getZ(r.v0)];
  } };

setMode('select');
requestAnimationFrame(loop);
send({ type: 'ready', gl: Object.assign(glStart, { attempt: glAttempt, antialias: GL_OPTIONS[glAttempt].antialias,
  software: softwareGl, pixelRatio: quality.ratio }) });
