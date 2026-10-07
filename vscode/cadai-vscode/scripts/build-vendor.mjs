// Builds media/vendor/three-bundle.js: three.js + the add-ons the viewer uses + three-mesh-bvh as ONE minified ES
// module with no bare imports (webviews have no import maps and the CSP forbids inline scripts).
// The bundle is committed, so users and the VSIX build need no Node. Run after changing versions:
//   npm install && npm run vendor
import { build } from 'esbuild';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const pkg = (name) => JSON.parse(readFileSync(join(root, 'node_modules', name, 'package.json'), 'utf8'));
const out = join(root, 'media', 'vendor', 'three-bundle.js');

const entry = `
export * from 'three';
export { OrbitControls } from 'three/addons/controls/OrbitControls.js';
export { Lut } from 'three/addons/math/Lut.js';
export { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
export { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
export { LineMaterial } from 'three/addons/lines/LineMaterial.js';
export { computeBoundsTree, disposeBoundsTree, acceleratedRaycast } from 'three-mesh-bvh';
`;

await build({
  stdin: { contents: entry, resolveDir: root, sourcefile: 'three-entry.js' },
  bundle: true, format: 'esm', minify: true, target: 'es2022', legalComments: 'none', outfile: out,
});
const banner = `/* CadAI vendor bundle — three.js ${pkg('three').version} (MIT, Copyright 2010-2026 Three.js Authors) and `
  + `three-mesh-bvh ${pkg('three-mesh-bvh').version} (MIT, Copyright Garrett Johnson). `
  + 'Licenses: THREE_LICENSE.txt, THREE_MESH_BVH_LICENSE.txt. Built by scripts/build-vendor.mjs. */\n';
writeFileSync(out, banner + readFileSync(out, 'utf8'));
for (const [name, file] of [['three', 'THREE_LICENSE.txt'], ['three-mesh-bvh', 'THREE_MESH_BVH_LICENSE.txt']]) {
  writeFileSync(join(root, 'media', 'vendor', file), readFileSync(join(root, 'node_modules', name, 'LICENSE'), 'utf8'));
}
console.log('wrote', out);
