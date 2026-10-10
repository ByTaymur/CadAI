'use strict';
// Keeps Fusion's installed CadAI add-in in step with this extension. Never replaces a newer install with an older one:
// a VS Code window still running an old CadAI did exactly that twice, and Fusion then started old code.
const fs = require('fs');
const os = require('os');
const path = require('path');

function addinTarget(platform = process.platform, env = process.env) {
  if (platform === 'win32') return env.APPDATA ? path.join(env.APPDATA, 'Autodesk', 'Autodesk Fusion', 'API', 'AddIns', 'CadAI') : null;
  if (platform === 'darwin') return path.join(os.homedir(), 'Library', 'Application Support', 'Autodesk', 'Autodesk Fusion', 'API', 'AddIns', 'CadAI');
  return null;
}

function manifestVersion(dir) {
  try { return JSON.parse(fs.readFileSync(path.join(dir, 'CadAI.manifest'), 'utf8')).version || null; } catch (_) { return null; }
}

function compareVersions(a, b) {
  const pa = String(a).split('.').map(Number), pb = String(b).split('.').map(Number);
  for (let i = 0; i < 3; i++) { if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0) ? 1 : -1; }
  return 0;
}

// force: user asked explicitly; reinstall the same version too (repairs a damaged copy), still never downgrade.
// Without force a missing install is only created when Fusion's add-in folder exists (Fusion is installed).
function syncAddin(src, target, { force = false } = {}) {
  const ours = manifestVersion(src);
  if (!ours) throw new Error('Fusion paketi bulunamadı; build_vsix.py ile eklentiyi yeniden paketleyin.');
  if (!target) return { action: 'unsupported', ours };
  const installed = manifestVersion(target);
  if (installed && compareVersions(installed, ours) > 0) return { action: 'newer-installed', ours, installed };
  if (installed && compareVersions(installed, ours) === 0 && !force) return { action: 'current', ours, installed };
  if (!installed && !force && !fs.existsSync(path.dirname(target))) return { action: 'no-fusion', ours };
  fs.mkdirSync(target, { recursive: true });
  // Stale bytecode next to replaced sources is harmless (mtime check) but confusing when debugging; drop it.
  for (const dir of [target, path.join(target, 'cadai_core')]) fs.rmSync(path.join(dir, '__pycache__'), { recursive: true, force: true });
  fs.cpSync(src, target, { recursive: true });
  if (manifestVersion(target) !== ours) throw new Error(`Fusion eklentisi kopyalanamadı: ${target}`);
  return { action: installed ? 'updated' : 'installed', ours, installed };
}

module.exports = { addinTarget, manifestVersion, compareVersions, syncAddin };
