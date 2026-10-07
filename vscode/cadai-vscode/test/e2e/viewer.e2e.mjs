// End-to-end test of the 3D viewer webview in a real browser (headless Edge/Chrome via playwright-core):
// WebGL rendering, BVH picking, snapping, annotation tools, FEM color map, DFM highlights and the section plane.
// Fixtures come from a real FreeCAD + CalculiX run (test/make_fixtures.py).
//   npm run test:viewer            (uses the installed Edge; set CADAI_BROWSER=chrome for Chrome)
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const media = join(root, 'media');
const fixtures = join(root, 'test', 'fixtures');
const shots = process.env.CADAI_SHOTS || join(root, 'test', 'out');
mkdirSync(shots, { recursive: true });

const harness = `<!DOCTYPE html><html><head><meta charset="UTF-8"><link rel="stylesheet" href="/media/style.css">
<style>:root{--vscode-editor-background:#1e1e1e;--vscode-foreground:#ccc;--vscode-editorWidget-background:#252526;
--vscode-button-background:#0e639c;--vscode-button-foreground:#fff;--vscode-button-secondaryBackground:#3a3d41;
--vscode-button-secondaryForeground:#fff;--vscode-input-background:#3c3c3c;--vscode-input-foreground:#ccc}</style>
<script>window.__msgs=[];window.acquireVsCodeApi=()=>({postMessage:(m)=>{if(m.type==='ready')window.__readyGl=m.gl;window.__msgs.push(m);}});</script></head>
<body class="viewer">${readFileSync(join(media, 'viewer.html'), 'utf8')}
<script type="module" src="/media/viewer.js"></script></body></html>`;

const controlsHarness = harness.slice(0, harness.indexOf('<body'))
  + `<style>body{background:#1e1e1e;--vscode-font-family:Arial,sans-serif;--vscode-font-size:13px}</style><body class="controls">`
  + readFileSync(join(media, 'controls.html'), 'utf8') + `<script src="/media/controls.js"></script></body></html>`;

const TYPES = { '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json' };
const server = createServer((req, res) => {
  const url = decodeURIComponent(req.url.split('?')[0]);
  try {
    if (url === '/') { res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end(harness); }
    if (url === '/controls') { res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end(controlsHarness); }
    if (url === '/favicon.ico') { res.writeHead(204); return res.end(); }
    const file = url.startsWith('/media/') ? join(media, url.slice(7)) : url.startsWith('/fixtures/') ? join(fixtures, url.slice(10)) : null;
    if (!file) throw new Error('404');
    res.writeHead(200, { 'Content-Type': TYPES[extname(file)] || 'application/octet-stream' });
    res.end(readFileSync(file));
  } catch (_) { res.writeHead(404); res.end(); }
}).listen(0, '127.0.0.1');
await new Promise((r) => server.once('listening', r));
const base = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch({ channel: process.env.CADAI_BROWSER === 'chrome' ? 'chrome' : 'msedge', headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
await page.goto(base + '/');
await page.waitForFunction(() => window.__msgs.some((m) => m.type === 'ready'));

const results = [];
async function check(name, fn) {
  try { await fn(); results.push([name, null]); console.log('PASS', name); } catch (e) { results.push([name, e]); console.log('FAIL', name, '\n ', e.message); }
}
// dispatch synchronously: window.postMessage is async and could race the next mouse event
const post = (msg) => page.evaluate((m) => { window.dispatchEvent(new MessageEvent('message', { data: m })); }, msg);
const frame = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
const takeMsgs = () => page.evaluate(() => window.__msgs.splice(0));
/** Screen position of a model point (CSS px). */
const screen = (p) => page.evaluate((pt) => {
  const { THREE, camera, renderer } = window.__cadai;
  const v = new THREE.Vector3(...pt).project(camera), r = renderer.domElement.getBoundingClientRect();
  return { x: r.left + (v.x + 1) / 2 * r.width, y: r.top + (1 - v.y) / 2 * r.height };
}, p);
async function clickAt(p) { const s = await screen(p); await page.mouse.click(s.x, s.y); await frame(); }

const scene = JSON.parse(readFileSync(join(fixtures, 'scene.json'), 'utf8'));
const field = JSON.parse(readFileSync(join(fixtures, 'fem_von_mises.json'), 'utf8'));

await post({ type: 'connection', connected: true });
await post({ type: 'scene', scene, fit: true });
await frame();

await check('merged geometry: one mesh and one edge object per FreeCAD object', async () => {
  const s = await page.evaluate(() => ({
    meshes: window.__cadai.objectMeshes().map((m) => [m.userData.object, m.userData.faces.length, !!m.geometry.boundsTree]),
    edges: window.__cadai.edgeObjects().map((l) => [l.userData.object, l.userData.edges.length]),
    calls: window.__cadai.renderer.info.render.calls,
  }));
  assert.deepEqual(s.meshes, [['Beam', 6, true], ['LBlock', 9, true]]);
  assert.deepEqual(s.edges, [['Beam', 12], ['LBlock', 21]]);
  assert.ok(s.calls < 20, `draw calls ${s.calls}`);
});

await check('click selects the face under the cursor (BVH raycast)', async () => {
  await takeMsgs();
  await clickAt([50, 10, 10]);
  const m = (await takeMsgs()).find((x) => x.type === 'pick');
  assert.deepEqual([m.object, m.sub], ['Beam', 'Face6']);
});

await check('selection from FreeCAD paints the face', async () => {
  await page.mouse.move(2, 790); // not hovering: hover tints the face under the cursor
  await post({ type: 'selection', items: [{ object: 'Beam', sub: 'Face6' }] });
  await frame();
  const [sel, other] = await page.evaluate(() => [window.__cadai.faceColorOf('Beam', 'Face6'), window.__cadai.faceColorOf('Beam', 'Face1')]);
  const blue = await page.evaluate(() => new window.__cadai.THREE.Color('#2f8cff').toArray());
  await post({ type: 'selection', items: [] });
  sel.forEach((c, i) => assert.ok(Math.abs(c - blue[i]) < 1e-3, `${sel} vs ${blue}`));
  assert.ok(Math.abs(other[0] - 0.8) < 1e-3);
});

await check('mark mode snaps to a vertex and asks for a note', async () => {
  await post({ type: 'mode', mode: 'mark' });
  await takeMsgs();
  await clickAt([100, 0, 10]);
  await page.waitForSelector('#noteForm:not(.hidden)', { timeout: 5000 });
  await page.fill('#noteInput', 'bu köşeyi pahla');
  await page.click('#noteForm button[type=submit]');
  const m = (await takeMsgs()).find((x) => x.type === 'addMarker').marker;
  assert.equal(m.a.snap, 'vertex');
  assert.deepEqual(m.a.point, [100, 0, 10]);
  assert.equal(m.note, 'bu köşeyi pahla');
});

await check('edge snapping picks the edge between two vertices', async () => {
  await clickAt([50, 0, 10]);
  await page.click('#noteCancel');
  const s = await page.evaluate(() => document.getElementById('info').textContent);
  assert.match(s, /^Kenar: Beam · Edge\d+/);
});

await check('measure: 100 mm between two vertices', async () => {
  await post({ type: 'mode', mode: 'measure' });
  await takeMsgs();
  await clickAt([0, 0, 10]);
  await clickAt([100, 0, 10]);
  await page.fill('#noteInput', '120 mm olsun');
  await page.click('#noteForm button[type=submit]');
  const m = (await takeMsgs()).find((x) => x.type === 'addMarker').marker;
  assert.equal(m.kind, 'dimension');
  assert.equal(m.distance, 100);
  await post({ type: 'mode', mode: 'select' });
});

await check('pen stroke on the surface does not rotate the view', async () => {
  await post({ type: 'mode', mode: 'pen' });
  const cam0 = await page.evaluate(() => window.__cadai.camera.position.toArray());
  const a = await screen([20, 10, 10]), b = await screen([80, 10, 10]);
  await page.mouse.move(a.x, a.y); await page.mouse.down();
  for (let i = 1; i <= 12; i++) await page.mouse.move(a.x + (b.x - a.x) * i / 12, a.y + (b.y - a.y) * i / 12);
  await page.mouse.up(); await frame();
  const cam1 = await page.evaluate(() => window.__cadai.camera.position.toArray());
  cam1.forEach((c, i) => assert.ok(Math.abs(c - cam0[i]) < 1e-6, `camera moved: ${cam0} -> ${cam1}`));
  assert.ok(await page.isVisible('#noteForm'));
  assert.match(await page.textContent('#noteTitle'), /Beam · Face6/);
  await page.click('#noteCancel');
  await post({ type: 'mode', mode: 'select' });
});

await check('FEM color map: result surface, legend, p99 clamp, deformation, value under cursor', async () => {
  await post({ type: 'femField', field });
  await frame();
  const r = await page.evaluate(() => ({ n: window.__cadai.result().meshes.length, model: window.__cadai.objectMeshes()[0].parent.visible }));
  assert.equal(r.n, 6);
  assert.equal(r.model, false, 'model is hidden while the result is shown');
  assert.ok(await page.isVisible('#resultBox'));
  assert.match(await page.textContent('#rTitle'), /von Mises.*MPa/);
  const top = await page.evaluate(() => document.querySelector('#rTicks div').textContent);
  assert.equal(top, Number(field.p99).toLocaleString('tr-TR', { maximumFractionDigits: 2 }));
  await page.click('#rClamp');
  const top2 = await page.evaluate(() => document.querySelector('#rTicks div').textContent);
  assert.equal(top2, Number(field.max).toLocaleString('tr-TR', { maximumFractionDigits: 2 }));
  const factor = await page.evaluate(() => window.__cadai.result().factor);
  assert.ok(factor > 1, `auto deformation factor ${factor}`);
  const s = await screen([30, 10, 10 - field.max_displacement_mm * factor * 0.09]);
  await page.mouse.move(s.x, s.y); await frame();
  assert.match(await page.textContent('#info'), /Face6 · von Mises gerilmesi: [\d.,]+ MPa/);
  await page.screenshot({ path: join(shots, 'viewer-fem.png') });
  await page.click('#rClose');
  assert.equal(await page.evaluate(() => window.__cadai.objectMeshes()[0].parent.visible), true);
});

await check('DFM highlights paint faces and draw edges', async () => {
  await post({ type: 'highlight', title: 'cnc: 1 hata', items: [{ object: 'LBlock', severity: 'error', elements: ['Face3', 'Edge5'] }] });
  await frame();
  const c = await page.evaluate(() => window.__cadai.faceColorOf('LBlock', 'Face3'));
  const red = await page.evaluate(() => new window.__cadai.THREE.Color('#ff3b30').toArray());
  c.forEach((x, i) => assert.ok(Math.abs(x - red[i]) < 1e-3));
  assert.ok(await page.isVisible('#hBox'));
  await page.screenshot({ path: join(shots, 'viewer-dfm.png') });
  await page.click('#hClear');
  const c2 = await page.evaluate(() => window.__cadai.faceColorOf('LBlock', 'Face3'));
  assert.ok(Math.abs(c2[0] - 0.8) < 1e-3);
});

await check('section plane clips the model and picking ignores the cut-away part', async () => {
  await page.keyboard.press('x');
  await frame();
  const s = await page.evaluate(() => ({ axis: window.__cadai.section().axis,
    clip: window.__cadai.objectMeshes()[0].material.clippingPlanes && window.__cadai.objectMeshes()[0].material.clippingPlanes.length }));
  assert.deepEqual(s, { axis: 'x', clip: 1 });
  await takeMsgs();
  await clickAt([75, 10, 10]);   // x = 75 > 50: removed by the section
  const m = (await takeMsgs()).find((x) => x.type === 'pick');
  assert.equal(m.object, null);
  await clickAt([25, 10, 10]);   // kept side
  const m2 = (await takeMsgs()).find((x) => x.type === 'pick');
  assert.deepEqual([m2.object, m2.sub], ['Beam', 'Face6']);
  await page.screenshot({ path: join(shots, 'viewer-section.png') });
  for (let i = 0; i < 3; i++) await page.keyboard.press('x');
  assert.equal(await page.evaluate(() => window.__cadai.section().axis), null);
});

await check('binary geometry from FreeCAD and the WebGL GPU are reported', async () => {
  assert.equal(scene.objects[0].enc, 'b64');
  const ready = await page.evaluate(() => window.__readyGl);
  assert.ok(ready && typeof ready.renderer === 'string' && ready.renderer.length > 3, JSON.stringify(ready));
});

await check('delta scene: unchanged objects are reused, not rebuilt', async () => {
  const before = await page.evaluate(() => window.__cadai.objectMeshes().map((m) => m.uuid));
  const same = Object.assign({}, scene, { objects: scene.objects.map((o) => ({ name: o.name, label: o.label, key: o.key, same: true })) });
  await post({ type: 'scene', scene: same, fit: false });
  await frame();
  const after = await page.evaluate(() => ({ ids: window.__cadai.objectMeshes().map((m) => m.uuid), build: window.__cadai.lastBuild() }));
  assert.deepEqual(after.ids, before);
  assert.deepEqual(after.build, { objects: 2, reused: 2 });
  // an object the viewer never had: it asks the extension for a full scene instead of drawing nothing
  await takeMsgs();
  await post({ type: 'scene', scene: Object.assign({}, same, { objects: [{ name: 'X', key: 'X|0', same: true }] }), fit: false });
  assert.ok((await takeMsgs()).some((m) => m.type === 'needFullScene'));
  await post({ type: 'scene', scene, fit: false });
});

await check('idle view does not render; interaction does', async () => {
  await page.mouse.move(5, 795);
  for (let i = 0; i < 30; i++) await frame(); // let damping settle
  const f0 = await page.evaluate(() => window.__cadai.renderer.info.render.frame);
  for (let i = 0; i < 10; i++) await frame();
  const f1 = await page.evaluate(() => window.__cadai.renderer.info.render.frame);
  assert.equal(f1, f0, 'no frames while idle');
  const s = await screen([50, 10, 10]);
  await page.mouse.move(s.x, s.y);
  await frame();
  const f2 = await page.evaluate(() => window.__cadai.renderer.info.render.frame);
  assert.ok(f2 > f1, 'hover renders');
});

await check('resolution adapts to a slow GPU, has a floor, and is retried after a smooth stretch', async () => {
  const q = () => page.evaluate(() => ({ ...window.__cadai.quality(), px: window.__cadai.renderer.getPixelRatio(),
    w: window.__cadai.renderer.domElement.width }));
  const adapt = (ms, n = 1) => page.evaluate(([m, k]) => { for (let i = 0; i < k; i++) window.__cadai.adaptQuality(m); }, [ms, n]);
  const q0 = await q();
  assert.equal(q0.software, true, 'the test browser draws with SwiftShader');
  assert.equal(q0.ratio, 1);
  await adapt(80, 5); // ~12 fps
  const low = await q();
  assert.equal(low.ratio, low.min);
  assert.equal(low.px, low.min);
  assert.equal(low.w, Math.round(1200 * low.min), 'the drawing buffer really shrank');
  await adapt(10, 2); // smooth, but the next step up was already too slow: wait
  assert.equal((await q()).ratio, low.min);
  await adapt(10, 1); // third smooth window in a row: retry
  assert.ok((await q()).ratio > low.min);
  await adapt(10, 5);
  const back = await q();
  assert.equal(back.ratio, back.max);
  assert.equal(back.need, 6, 'next retry waits twice as long');
  await frame();
});

await check('GPU driver reset: notice, restore, scene rebuilt', async () => {
  await takeMsgs();
  await page.evaluate(() => {
    window.__lose = window.__cadai.renderer.getContext().getExtension('WEBGL_lose_context');
    window.__lose.loseContext();
  });
  await page.waitForSelector('#glNotice:not(.hidden)', { timeout: 5000 });
  assert.match(await page.textContent('#glText'), /sürücüsü 3B görünümü sıfırladı/);
  assert.ok((await takeMsgs()).some((m) => m.type === 'glLost'));
  await page.evaluate(() => window.__lose.restoreContext());
  await page.waitForSelector('#glNotice.hidden', { state: 'attached', timeout: 5000 });
  const restored = await takeMsgs();
  assert.ok(restored.some((m) => m.type === 'needFullScene'), 'asks the extension for every buffer again');
  assert.ok(restored.some((m) => m.type === 'glRestored' && m.gl.limits.renderbuffer > 0), 'refreshes GPU capabilities after a switch');
  await post({ type: 'scene', scene, fit: false });
  await frame();
  const f0 = await page.evaluate(() => window.__cadai.renderer.info.render.frame);
  await clickAt([50, 10, 10]);
  const s = await page.evaluate(() => ({ meshes: window.__cadai.objectMeshes().length, frame: window.__cadai.renderer.info.render.frame,
    lost: window.__cadai.renderer.getContext().isContextLost() }));
  assert.deepEqual([s.meshes, s.lost], [2, false]);
  assert.ok(s.frame > f0, 'renders again');
  const m = (await takeMsgs()).find((x) => x.type === 'pick');
  assert.deepEqual([m.object, m.sub], ['Beam', 'Face6'], 'picking works after the reset');
});

await check('no browser errors', async () => { assert.deepEqual(errors, []); });

await page.screenshot({ path: join(shots, 'viewer-model.png') });

// Drivers that refuse some context settings, and machines without WebGL 2: fresh pages with a patched getContext.
async function viewerWith(initScript) {
  const p = await browser.newPage({ viewport: { width: 800, height: 600 } });
  await p.addInitScript(initScript);
  await p.goto(base + '/');
  await p.waitForFunction(() => window.__msgs.some((m) => m.type === 'ready'));
  await p.evaluate(() => window.dispatchEvent(new MessageEvent('message', { data: { type: 'connection', connected: true } })));
  return p;
}

await check('a driver that refuses antialiasing / the discrete GPU gets the next settings', async () => {
  const p = await viewerWith(() => {
    const orig = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, attrs) {
      if (/webgl/.test(type) && attrs && (attrs.antialias || attrs.powerPreference === 'high-performance')) return null;
      return orig.call(this, type, attrs);
    };
  });
  const ready = await p.evaluate(() => window.__msgs.find((m) => m.type === 'ready'));
  assert.equal(ready.gl.attempt, 2);
  assert.equal(ready.gl.antialias, false);
  assert.ok(ready.gl.renderer.length > 3);
  assert.equal(await p.isVisible('#glNotice'), false);
  await p.close();
});

await check('a driver that rejects every explicit GPU preference still opens the viewer', async () => {
  const p = await viewerWith(() => {
    const orig = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, attrs) {
      if (/webgl/.test(type) && attrs && attrs.powerPreference !== undefined) return null;
      return orig.call(this, type, attrs);
    };
  });
  try {
    const ready = await p.evaluate(() => window.__readyGl);
    assert.equal(ready.attempt, 3);
    assert.equal(ready.antialias, false);
    assert.equal(await p.isVisible('#glNotice'), false);
  } finally { await p.close(); }
});

await check('GPU drawing-buffer limits apply at startup and after window resize', async () => {
  const p = await viewerWith(() => {
    const orig = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function (key) {
      if (key === this.MAX_RENDERBUFFER_SIZE || key === this.MAX_TEXTURE_SIZE) return 256;
      if (key === this.MAX_VIEWPORT_DIMS) return new Int32Array([256, 256]);
      return orig.call(this, key);
    };
  });
  try {
    const state = () => p.evaluate(() => ({ ...window.__cadai.quality(),
      width: window.__cadai.renderer.domElement.width, height: window.__cadai.renderer.domElement.height }));
    const initial = await state();
    assert.ok(initial.width <= 256 && initial.height <= 256, JSON.stringify(initial));
    assert.ok(initial.ratio < 0.5, 'hardware limits can require less than the normal quality floor');
    await p.setViewportSize({ width: 1600, height: 1200 });
    await p.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    const resized = await state();
    assert.ok(resized.width <= 256 && resized.height <= 256, JSON.stringify(resized));
    assert.ok(resized.ratio < initial.ratio, 'resize recalculates capability limits');
    await p.evaluate((s) => window.dispatchEvent(new MessageEvent('message', { data: { type: 'scene', scene: s, fit: true } })), scene);
    await p.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    assert.ok(await p.evaluate(() => window.__cadai.renderer.info.render.calls > 0), 'the constrained GPU still draws');
    await p.evaluate(() => { for (let i = 0; i < 20; i++) window.__cadai.adaptQuality(10); });
    const smooth = await state();
    assert.ok(smooth.width <= 256 && smooth.height <= 256, 'quality recovery cannot exceed hardware limits');
  } finally { await p.close(); }
});

await check('privacy-hidden GPU names do not prevent drawing or picking', async () => {
  const p = await viewerWith(() => {
    const orig = WebGL2RenderingContext.prototype.getExtension;
    WebGL2RenderingContext.prototype.getExtension = function (name) {
      if (name === 'WEBGL_debug_renderer_info') throw new Error('Hardware identity hidden');
      return orig.call(this, name);
    };
  });
  try {
    const ready = await p.evaluate(() => window.__readyGl);
    assert.ok(ready && ready.limits.renderbuffer > 0);
    await p.evaluate((s) => window.dispatchEvent(new MessageEvent('message', { data: { type: 'scene', scene: s, fit: true } })), scene);
    await p.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    assert.ok(await p.evaluate(() => window.__cadai.renderer.info.render.calls > 0));
    const hit = await p.evaluate(() => {
      const { THREE, camera, renderer } = window.__cadai;
      const v = new THREE.Vector3(50, 10, 10).project(camera), r = renderer.domElement.getBoundingClientRect();
      return { x: r.left + (v.x + 1) / 2 * r.width, y: r.top + (1 - v.y) / 2 * r.height };
    });
    await p.mouse.click(hit.x, hit.y);
    await p.waitForFunction(() => window.__msgs.some((m) => m.type === 'pick'), null, { timeout: 5000 });
    const pick = await p.evaluate(() => window.__msgs.find((m) => m.type === 'pick'));
    assert.deepEqual([pick.object, pick.sub], ['Beam', 'Face6']);
  } finally { await p.close(); }
});

await check('without WebGL 2 the view explains why and offers GPU diagnostics', async () => {
  const p = await viewerWith(() => {
    const orig = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, attrs) { return /webgl/.test(type) ? null : orig.call(this, type, attrs); };
  });
  const ready = await p.evaluate(() => window.__msgs.find((m) => m.type === 'ready'));
  assert.equal(ready.gl, null);
  assert.match(ready.glError, /WebGL/i);
  assert.ok(await p.isVisible('#glNotice'));
  assert.match(await p.textContent('#glText'), /WebGL 2 kullanılamıyor/);
  assert.equal(await p.isVisible('#glReload'), false);
  await p.click('#glDiag');
  assert.ok(await p.evaluate(() => window.__msgs.some((m) => m.type === 'command' && m.command === 'cadai.gpuDiagnostics')));
  await p.close();
});
const controlsPage = await browser.newPage({ viewport: { width: 420, height: 1000 } });
const controlsErrors = [];
controlsPage.on('pageerror', (e) => controlsErrors.push(e.message));
await controlsPage.goto(base + '/controls');
await controlsPage.waitForFunction(() => window.__msgs.some((m) => m.cmd === 'ready'));
const controlsPost = (m) => controlsPage.evaluate((data) => window.dispatchEvent(new MessageEvent('message', { data })), m);
const reqFixture = JSON.parse(readFileSync(join(fixtures, 'requirements.json'), 'utf8'));
await controlsPost({ type: 'connection', connected: true });
await controlsPost({ type: 'document', tree: reqFixture.tree });
await controlsPost({ type: 'requirements', result: reqFixture.report });

await check('requirements panel shows real geometric failures and units', async () => {
  assert.match(await controlsPage.textContent('#reqStatus'), /2\/3/);
  assert.equal(await controlsPage.locator('#reqList li').count(), 3);
  assert.equal(await controlsPage.locator('#reqList .fail').count(), 1);
  assert.match(await controlsPage.locator('#reqList .fail').textContent(), /6.*hedef.*8/);
  await controlsPage.evaluate(() => document.querySelectorAll('details').forEach((d) => {
    d.open = d.querySelector('summary')?.textContent === 'Tasarım şartları';
  }));
  await controlsPage.locator('details').filter({ has: controlsPage.locator('#reqStatus') })
    .screenshot({ path: join(shots, 'requirements-panel.png') });
});

await check('requirement editing sends exact rules and document, deletion is explicit', async () => {
  await controlsPage.click('[data-req-edit="Delik_capi"]');
  assert.equal(await controlsPage.inputValue('#reqMetric'), 'hole_diameter');
  assert.equal(await controlsPage.inputValue('#reqValue'), '8');
  assert.ok(await controlsPage.locator('#reqId').evaluate((e) => e.readOnly));
  await controlsPage.fill('#reqValue', '6');
  await controlsPage.click('#reqSave');
  const message = await controlsPage.evaluate(() => window.__msgs.find((m) => m.cmd === 'requirementsSave'));
  assert.equal(message.document, reqFixture.tree.active);
  assert.deepEqual(message.requirement, { id: 'Delik_capi', measure: { object: 'Plate', metric: 'hole_diameter', axis: 'z' },
    operator: 'eq', value: 6, tolerance: 0.01 });
  await controlsPost({ type: 'requirementsError', text: 'Hesaplama hatası' });
  assert.equal(await controlsPage.locator('#reqSave').isDisabled(), false);
  await controlsPage.click('#reqCancel');
  await controlsPost({ type: 'requirements', result: reqFixture.report });
  await controlsPage.click('[data-req-delete="Delik_capi"]');
  const removal = await controlsPage.evaluate(() => window.__msgs.find((m) => m.cmd === 'requirementsRemove'));
  assert.deepEqual(removal, { cmd: 'requirementsRemove', document: reqFixture.tree.active, id: 'Delik_capi' });
});

await check('document switch clears requirements and ignores old results', async () => {
  await controlsPost({ type: 'document', tree: { active: 'NextDoc', label: 'Next', objects: [] } });
  assert.equal(await controlsPage.locator('#reqList li').count(), 0);
  await controlsPost({ type: 'requirements', result: reqFixture.report });
  assert.equal(await controlsPage.locator('#reqList li').count(), 0);
});

await check('requirement labels and errors are escaped, controls have no script errors', async () => {
  const label = '<img src=x onerror="window.injected=true">';
  await controlsPost({ type: 'requirements', result: { document: 'NextDoc', status: 'fail', count: 1, failed: 1,
    checks: [{ id: label, status: 'error', error: label }], requirements: [] } });
  assert.equal(await controlsPage.locator('#reqList img').count(), 0);
  assert.match(await controlsPage.textContent('#reqList'), /<img/);
  assert.equal(await controlsPage.evaluate(() => !!window.injected), false);
  assert.deepEqual(controlsErrors, []);
});
await controlsPage.close();
await browser.close();
server.close();
const failed = results.filter(([, e]) => e);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
