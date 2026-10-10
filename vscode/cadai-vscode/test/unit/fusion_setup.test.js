'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { syncAddin, manifestVersion, compareVersions, addinTarget } = require('../../fusion_setup');

function addin(dir, version, code = `VERSION = "${version}"`) {
  fs.mkdirSync(path.join(dir, 'cadai_core'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'CadAI.manifest'), JSON.stringify({ version }));
  fs.writeFileSync(path.join(dir, 'CadAI.py'), code);
  return dir;
}

function tmp(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cadai-fusion-setup-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test('an older extension never overwrites a newer installed Fusion add-in', (t) => {
  const root = tmp(t);
  const src = addin(path.join(root, 'ext'), '0.17.3');
  const target = addin(path.join(root, 'AddIns', 'CadAI'), '0.18.1');
  for (const force of [false, true]) {
    assert.equal(syncAddin(src, target, { force }).action, 'newer-installed');
    assert.equal(manifestVersion(target), '0.18.1');
  }
});

test('a newer extension updates the add-in and removes stale bytecode', (t) => {
  const root = tmp(t);
  const src = addin(path.join(root, 'ext'), '0.18.2');
  const target = addin(path.join(root, 'AddIns', 'CadAI'), '0.18.1');
  fs.mkdirSync(path.join(target, 'cadai_core', '__pycache__'));
  const out = syncAddin(src, target);
  assert.deepEqual([out.action, out.installed, manifestVersion(target)], ['updated', '0.18.1', '0.18.2']);
  assert.ok(!fs.existsSync(path.join(target, 'cadai_core', '__pycache__')));
  assert.equal(syncAddin(src, target).action, 'current');
  assert.equal(syncAddin(src, target, { force: true }).action, 'updated');
});

test('automatic sync installs only where Fusion is installed', (t) => {
  const root = tmp(t);
  const src = addin(path.join(root, 'ext'), '0.18.2');
  assert.equal(syncAddin(src, path.join(root, 'NoFusion', 'AddIns', 'CadAI')).action, 'no-fusion');
  fs.mkdirSync(path.join(root, 'Fusion', 'AddIns'), { recursive: true });
  assert.equal(syncAddin(src, path.join(root, 'Fusion', 'AddIns', 'CadAI')).action, 'installed');
});

test('versions compare numerically and the add-in path follows Autodesk Fusion', () => {
  assert.equal(compareVersions('0.18.10', '0.18.9'), 1);
  assert.equal(compareVersions('0.18.0', '0.18'), 0);
  assert.equal(addinTarget('win32', { APPDATA: 'C:\\A' }), path.join('C:\\A', 'Autodesk', 'Autodesk Fusion', 'API', 'AddIns', 'CadAI'));
  assert.equal(addinTarget('linux', {}), null);
});
