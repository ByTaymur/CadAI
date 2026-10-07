// GPU classification and diagnosis across vendors and machine types. Run: npm test
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const gpu = require(path.join(__dirname, '..', '..', 'gpu.js'));

const KIND = [
  // AMD
  ['AMD Radeon RX 7900 XTX', 'amd', 'discrete'],
  ['AMD Radeon RX 6600', 'amd', 'discrete'],
  ['AMD Radeon PRO W7800', 'amd', 'discrete'],
  ['AMD Radeon(TM) Graphics', 'amd', 'integrated'],           // Ryzen 7000 desktop iGPU
  ['AMD Radeon 780M Graphics', 'amd', 'integrated'],          // Ryzen laptop iGPU
  ['AMD Radeon(TM) Vega 8 Graphics', 'amd', 'integrated'],
  // NVIDIA
  ['NVIDIA GeForce RTX 4070 Laptop GPU', 'nvidia', 'discrete'],
  ['NVIDIA RTX A2000', 'nvidia', 'discrete'],
  ['Quadro P620', 'nvidia', 'discrete'],
  // Intel
  ['Intel(R) UHD Graphics 770', 'intel', 'integrated'],
  ['Intel(R) Iris(R) Xe Graphics', 'intel', 'integrated'],
  ['Intel(R) Arc(TM) Graphics', 'intel', 'integrated'],       // Core Ultra iGPU
  ['Intel(R) Arc(TM) A770 Graphics', 'intel', 'discrete'],
  ['Intel(R) Arc(TM) B580 Graphics', 'intel', 'discrete'],
  ['Intel(R) Iris(R) Xe MAX Graphics', 'intel', 'discrete'],
  ['AMD Radeon 8060S Graphics', 'amd', 'integrated'],
  // others
  ['Apple M2 Pro', 'apple', 'integrated'],
  ['Qualcomm(R) Adreno(TM) X1-85 GPU', 'qualcomm', 'integrated'],
  ['Microsoft Basic Render Driver', 'software', 'software'],
  ['llvmpipe (LLVM 17.0.6, 256 bits)', 'software', 'software'],
  ['Google SwiftShader', 'software', 'software'],
  ['VMware SVGA 3D', 'virtual', 'virtual'],
  ['Parsec Virtual Display Adapter', 'virtual', 'virtual'],
  ['Microsoft Remote Display Adapter', 'virtual', 'virtual'],
  ['DisplayLink USB Device', 'virtual', 'virtual'],
  ['Microsoft Basic Display Adapter', 'unknown', 'nodriver'],  // GPU without a driver
  ['ASPEED Graphics Family', 'other', 'integrated'],           // server BMC
  ['Matrox G200eR2', 'other', 'integrated'],
  // Linux lspci names
  ['Advanced Micro Devices, Inc. [AMD/ATI] Navi 31 [Radeon RX 7900 XT/7900 XTX]', 'amd', 'discrete'],
  ['Advanced Micro Devices, Inc. [AMD/ATI] Raphael', 'amd', 'integrated'],
  ['Advanced Micro Devices, Inc. [AMD/ATI] Phoenix1', 'amd', 'integrated'],
  ['NVIDIA Corporation AD102 [GeForce RTX 4090]', 'nvidia', 'discrete'],
  ['Intel Corporation DG2 [Arc A770]', 'intel', 'discrete'],
  ['Intel Corporation Raptor Lake-S GT1 [UHD Graphics 770]', 'intel', 'integrated'],
];

test('classify: vendor and integrated/discrete for common GPUs', () => {
  for (const [name, vendor, kind] of KIND) assert.deepEqual(gpu.classify(name), { vendor, kind }, name);
});

test('WebGL renderer strings from ANGLE are cleaned', () => {
  assert.equal(gpu.cleanRenderer('ANGLE (AMD, AMD Radeon(TM) Graphics (0x0000164E) Direct3D11 vs_5_0 ps_5_0, D3D11)'), 'AMD Radeon(TM) Graphics');
  assert.equal(gpu.cleanRenderer('ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)'), 'NVIDIA GeForce RTX 3060');
  assert.equal(gpu.cleanRenderer('ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)'), 'ANGLE Metal Renderer: Apple M2');
  assert.equal(gpu.classify(gpu.cleanRenderer('ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)')).vendor, 'apple');
});

test('desktop with the monitor on the motherboard: tells to move the cable (any brand)', () => {
  for (const [card, igpu] of [['AMD Radeon RX 7900 XTX', 'AMD Radeon(TM) Graphics'], ['NVIDIA GeForce RTX 4080', 'Intel(R) UHD Graphics 770'],
    ['Intel(R) Arc(TM) A750 Graphics', 'Intel(R) UHD Graphics 730']]) {
    const f = gpu.diagnose({ adapters: [{ name: card, active: false }, { name: igpu, active: true }], freecadGl: igpu, laptop: false });
    assert.equal(f[0].id, 'cable', card);
    assert.equal(f[0].severity, 'warning');
    assert.match(f[0].fix, /ekran kartının DisplayPort/);
  }
});

test('laptop with switchable graphics: per-app GPU advice for the right vendor', () => {
  const f = gpu.diagnose({ adapters: [{ name: 'NVIDIA GeForce RTX 4070 Laptop GPU', active: false }, { name: 'Intel(R) UHD Graphics', active: true }],
    freecadGl: 'Intel(R) UHD Graphics', viewerGl: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Laptop GPU (0x2860) Direct3D11 vs_5_0 ps_5_0, D3D11)', laptop: true });
  const h = f.find((x) => x.id === 'hybrid');
  assert.equal(h.severity, 'warning');
  assert.deepEqual(h.apps, ['FreeCAD']);
  assert.match(h.fix, /NVIDIA Denetim Masası/);
  assert.match(h.fix, /Yüksek performans/);
  const amd = gpu.diagnose({ adapters: [{ name: 'AMD Radeon RX 7600S', active: false }, { name: 'AMD Radeon 780M Graphics', active: true }],
    freecadGl: 'AMD Radeon 780M Graphics', laptop: true }).find((x) => x.id === 'hybrid');
  assert.match(amd.fix, /AMD Software/);
});

test('good setups are reported as fine; Apple and single-GPU machines get no false alarm', () => {
  const ok = gpu.diagnose({ adapters: [{ name: 'AMD Radeon RX 7900 XTX', active: true }, { name: 'AMD Radeon(TM) Graphics', active: false }],
    freecadGl: 'AMD Radeon RX 7900 XTX', settings: { use_vbo: true, software_opengl: false } });
  assert.deepEqual(ok.map((x) => x.id), ['ok']);
  const mac = gpu.diagnose({ adapters: [{ name: 'Apple M3', active: true }], viewerGl: 'ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)', platform: 'darwin' });
  assert.deepEqual(mac.map((x) => x.id), ['ok']);
  const intelOnly = gpu.diagnose({ adapters: [{ name: 'Intel(R) Iris(R) Xe Graphics', active: true }], freecadGl: 'Intel(R) Iris(R) Xe Graphics' });
  assert.deepEqual(intelOnly.map((x) => x.id), ['ok']);
});

test('software rendering, FreeCAD settings and old drivers are flagged', () => {
  const f = gpu.diagnose({ adapters: [{ name: 'NVIDIA GeForce GTX 1060', active: true, driverDate: '2021-01-01T00:00:00Z' }],
    freecadGl: 'GDI Generic', viewerGl: 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)',
    settings: { use_vbo: false, software_opengl: true }, now: Date.parse('2026-10-01') });
  const ids = f.map((x) => x.id);
  assert.ok(ids.includes('software:FreeCAD (OpenGL)'));
  assert.ok(ids.includes('fc-software'));
  assert.ok(!ids.includes('fc-vbo'), 'no VBO advice for a software renderer');
  assert.ok(ids.some((i) => i.startsWith('driver:')), ids);
  assert.ok(!ids.includes('ok'));
});

test('VBO is recommended only on a real GPU with OpenGL 3+', () => {
  const vbo = (gl, version) => gpu.diagnose({ adapters: [], freecadGl: gl, freecadGlVersion: version,
    settings: { use_vbo: false, software_opengl: false }, platform: 'linux' }).some((x) => x.id === 'fc-vbo');
  assert.equal(vbo('AMD Radeon RX 7900 XTX', '4.6.0 Compatibility Profile Context 24.9.1.240815'), true);
  assert.equal(vbo('Intel(R) UHD Graphics 770', null), false, 'unknown capabilities: do not change view settings');
  assert.equal(vbo('Unrecognized GPU', '4.6'), true, 'capabilities, not a vendor allow-list');
  assert.equal(vbo('Mesa Intel(R) HD Graphics 3000 (SNB GT2)', '2.1 Mesa 23.2.1'), false, 'old OpenGL 2.1 driver');
  assert.equal(vbo('llvmpipe (LLVM 17.0.6, 256 bits)', '4.5 (Compatibility Profile) Mesa 24.0'), false);
  assert.equal(vbo('VMware SVGA 3D', '4.3'), false);
  assert.deepEqual(['4.6.0 NVIDIA 560.94', '3.1 Mesa 23.2', 'OpenGL ES 3.2 v1.r38p1', '1.1.0', 'x'].map(gpu.glMajor), [4, 3, 3, 1, null]);
  const old = gpu.diagnose({ adapters: [], freecadGl: 'GDI Generic', freecadGlVersion: '1.1.0', settings: { use_vbo: false, software_opengl: false } });
  assert.ok(old.some((x) => x.id === 'fc-old-gl'));
  assert.ok(gpu.diagnose({ freecadGl: 'Old GPU', freecadGlVersion: '2.0', settings: {} }).some((x) => x.id === 'fc-old-gl'));
  assert.ok(!gpu.diagnose({ freecadGl: 'Old GPU', freecadGlVersion: '2.1', settings: {} }).some((x) => x.id === 'fc-old-gl'));
});

test('a GPU without a driver, a 3D view without WebGL and driver resets are reported', () => {
  const f = gpu.diagnose({ adapters: [{ name: 'Microsoft Basic Display Adapter', active: true }], viewerError: 'Error creating WebGL context.', viewerLost: 2 });
  const byId = Object.fromEntries(f.map((x) => [x.id, x]));
  assert.equal(byId.nodriver.severity, 'error');
  assert.match(byId.nodriver.fix, /amd\.com.*nvidia\.com.*intel\.com/);
  assert.equal(byId.webgl.severity, 'error');
  assert.match(byId.webgl.fix, /disable-hardware-acceleration/);
  assert.match(byId['gl-lost'].title, /2 kez/);
  assert.ok(!f.some((x) => x.id === 'ok'));
});

test('remote-desktop / streaming display adapters do not hide the cable problem', () => {
  const f = gpu.diagnose({ adapters: [{ name: 'NVIDIA GeForce RTX 3080', active: false }, { name: 'Intel(R) UHD Graphics 630', active: true },
    { name: 'Parsec Virtual Display Adapter', active: true }], freecadGl: 'Intel(R) UHD Graphics 630', laptop: false });
  assert.equal(f[0].id, 'cable');
});

test('PowerShell dates from 5.1 and 7 both parse', () => {
  assert.equal(gpu.psDate('/Date(1786924800000)/'), '2026-08-17T00:00:00.000Z');
  assert.equal(gpu.psDate('2026-08-17T00:00:00Z'), '2026-08-17T00:00:00.000Z');
  assert.equal(gpu.psDate(''), undefined);
});

test('display attachment does not imply which GPU an application uses', () => {
  const adapters = [{ name: 'NVIDIA GeForce RTX 3080', active: false }, { name: 'Intel(R) UHD Graphics 630', active: true }];
  const strong = gpu.diagnose({ adapters, freecadGl: 'NVIDIA GeForce RTX 3080', viewerGl: 'NVIDIA GeForce RTX 3080' });
  assert.deepEqual(strong.map((x) => x.id), ['ok']);
  assert.ok(!gpu.diagnose({ adapters }).some((x) => x.id === 'cable'), 'unknown renderer: do not diagnose a cable');
  const mixed = gpu.diagnose({ adapters, freecadGl: 'Intel(R) UHD Graphics 630', viewerGl: 'NVIDIA GeForce RTX 3080' });
  assert.ok(!mixed.some((x) => x.id === 'cable'));
  assert.deepEqual(mixed.find((x) => x.id === 'hybrid').apps, ['FreeCAD']);
});
