// Finding FreeCAD on Windows / macOS / Linux and installing the bundled CadAI add-on into it.
'use strict';

const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const IS_WIN = process.platform === 'win32';
const IS_MAC = process.platform === 'darwin';

function versionKey(p) {
  const m = p.match(/FreeCAD[ _-]?(\d+)(?:\.(\d+))?(?:\.(\d+))?/i);
  return m ? [+m[1], +(m[2] || 0), +(m[3] || 0)] : [0, 0, 0];
}
function newestFirst(a, b) {
  const ka = versionKey(a), kb = versionKey(b);
  for (let i = 0; i < 3; i++) if (ka[i] !== kb[i]) return kb[i] - ka[i];
  return 0;
}
function listDirs(base, prefix) {
  try { return fs.readdirSync(base).filter((d) => d.toLowerCase().startsWith(prefix)).map((d) => path.join(base, d)); }
  catch (_) { return []; }
}
function which(name) {
  try {
    const out = cp.execFileSync(IS_WIN ? 'where.exe' : 'which', [name], { encoding: 'utf8', timeout: 3000, windowsHide: true });
    return out.split(/\r?\n/).map((s) => s.trim()).find(Boolean) || null;
  } catch (_) { return null; }
}

/** Path of the FreeCAD GUI executable, or null. `configured` (user setting) wins when it exists. */
function findFreeCAD(configured) {
  if (configured && fs.existsSync(configured)) return configured;
  const candidates = [];
  if (IS_WIN) {
    const roots = [process.env.ProgramFiles, process.env['ProgramFiles(x86)'],
      process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Programs')].filter(Boolean);
    for (const root of roots) for (const d of listDirs(root, 'freecad')) candidates.push(path.join(d, 'bin', 'freecad.exe'));
  } else if (IS_MAC) {
    candidates.push('/Applications/FreeCAD.app/Contents/MacOS/FreeCAD',
      path.join(os.homedir(), 'Applications', 'FreeCAD.app', 'Contents', 'MacOS', 'FreeCAD'));
  } else {
    for (const n of ['freecad', 'FreeCAD']) { const w = which(n); if (w) candidates.push(w); }
    candidates.push('/usr/bin/freecad', '/usr/bin/FreeCAD', '/usr/local/bin/freecad');
    for (const d of [path.join(os.homedir(), 'Applications'), path.join(os.homedir(), 'Downloads'), '/opt']) {
      try { for (const f of fs.readdirSync(d)) if (/^freecad.*\.appimage$/i.test(f)) candidates.push(path.join(d, f)); } catch (_) { /* none */ }
    }
  }
  return candidates.filter((p) => fs.existsSync(p)).sort(newestFirst)[0] || null;
}

/** Headless FreeCAD (freecadcmd) next to the GUI executable. AppImages accept the same binary with a flag. */
function freecadCmd(exe) {
  if (!exe) return null;
  if (/\.appimage$/i.test(exe)) return exe;
  const dir = path.dirname(exe);
  for (const n of IS_WIN ? ['freecadcmd.exe', 'FreeCADCmd.exe'] : ['freecadcmd', 'FreeCADCmd']) {
    const p = path.join(dir, n);
    if (fs.existsSync(p)) return p;
  }
  return null;
}

/** Python that can run the stdlib-only MCP server: FreeCAD's bundled one if present, else the system's. */
function pythonFor(exe) {
  if (exe) {
    const dir = path.dirname(exe);
    const bundled = IS_WIN ? [path.join(dir, 'python.exe')]
      : IS_MAC ? [path.join(dir, '..', 'Resources', 'bin', 'python'), path.join(dir, '..', 'Resources', 'bin', 'python3')]
        : [path.join(dir, 'python3'), path.join(dir, 'python')];
    const hit = bundled.find((p) => fs.existsSync(p));
    if (hit) return path.resolve(hit);
  }
  return which(IS_WIN ? 'python.exe' : 'python3') || which('python');
}

/** Ask FreeCAD itself where its user data folder is (it differs per OS and per FreeCAD version). Async: takes a
 * few seconds the first time, must not block the extension host. */
function userDataDir(exe) {
  const cmd = freecadCmd(exe);
  if (!cmd) return Promise.resolve(null);
  const script = path.join(os.tmpdir(), `cadai_userdir_${process.pid}_${Date.now()}.py`);
  fs.writeFileSync(script, 'import FreeCAD\nprint("CADAI_DIR=" + FreeCAD.getUserAppDataDir())\n');
  const args = /\.appimage$/i.test(cmd) ? ['freecadcmd', script] : [script];
  return new Promise((resolve) => {
    cp.execFile(cmd, args, { encoding: 'utf8', timeout: 60000, windowsHide: true }, (err, stdout) => {
      try { fs.unlinkSync(script); } catch (_) { /* ignore */ }
      const m = !err && stdout.match(/CADAI_DIR=(.+)/);
      resolve(m ? m[1].trim() : null);
    });
  });
}

function addonVersion(dir) {
  try {
    const m = fs.readFileSync(path.join(dir, 'cadai', '__init__.py'), 'utf8').match(/__version__\s*=\s*"([^"]+)"/);
    return m ? m[1] : null;
  } catch (_) { return null; }
}

function versionGreater(a, b) {
  const pa = String(a).split('.').map(Number), pb = String(b).split('.').map(Number);
  for (let i = 0; i < 3; i++) { if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0); }
  return false;
}

/**
 * Install or update the bundled add-on into <userData>/Mod/CadAI.
 * A symlink/junction there means a developer setup pointing at a source checkout: never touched.
 * Returns {status: 'installed'|'updated'|'current'|'developer', target, version}.
 */
function installAddon(bundledDir, userData) {
  const target = path.join(userData, 'Mod', 'CadAI');
  const bundled = addonVersion(bundledDir);
  let st = null;
  try { st = fs.lstatSync(target); } catch (_) { /* not there */ }
  if (st && st.isSymbolicLink()) return { status: 'developer', target, version: addonVersion(target) };
  const current = st ? addonVersion(target) : null;
  if (current && !versionGreater(bundled, current)) return { status: 'current', target, version: current };
  if (st) fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(bundledDir, target, { recursive: true, filter: (src) => !/__pycache__/.test(src) });
  return { status: current ? 'updated' : 'installed', target, version: bundled };
}

module.exports = { findFreeCAD, freecadCmd, pythonFor, userDataDir, installAddon, addonVersion, versionGreater };
