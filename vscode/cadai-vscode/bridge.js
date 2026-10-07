// Client for the CadAI bridge running inside FreeCAD (see freecad/CadAI/cadai/bridge.py).
'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');
const os = require('os');

function candidateDirs() {
  // explicit directory: use only it (tests, multiple FreeCAD installs)
  if (process.env.CADAI_BRIDGE_DIR) return [process.env.CADAI_BRIDGE_DIR];
  const dirs = [];
  const roots = [process.env.APPDATA, path.join(os.homedir(), '.local', 'share'),
    path.join(os.homedir(), 'Library', 'Application Support')].filter(Boolean);
  for (const root of roots) {
    const base = path.join(root, 'FreeCAD');
    if (!fs.existsSync(base)) continue;
    dirs.push(path.join(base, 'CadAI'));
    for (const v of fs.readdirSync(base)) dirs.push(path.join(base, v, 'CadAI'));
  }
  return dirs;
}

function findBridgeInfo() {
  let best = null;
  for (const dir of candidateDirs()) {
    const file = path.join(dir, 'bridge.json');
    try {
      const st = fs.statSync(file);
      if (!best || st.mtimeMs > best.mtime) best = { file, mtime: st.mtimeMs };
    } catch (_) { /* not there */ }
  }
  if (!best) return null;
  try { return JSON.parse(fs.readFileSync(best.file, 'utf8')); } catch (_) { return null; }
}

class BridgeError extends Error {
  constructor(message, offline) { super(message); this.offline = !!offline; }
}

class Bridge {
  request(method, route, body, timeoutMs = 30000) {
    const info = findBridgeInfo();
    if (!info) return Promise.reject(new BridgeError('FreeCAD açık değil (köprü bulunamadı).', true));
    const url = new URL(info.url + route);
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body), 'utf8');
    return new Promise((resolve, reject) => {
      const req = http.request({
        hostname: url.hostname, port: url.port, path: url.pathname, method,
        headers: Object.assign({ Authorization: 'Bearer ' + info.token },
          data ? { 'Content-Type': 'application/json', 'Content-Length': data.length } : {}),
      }, (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          if (res.statusCode !== 200) return reject(new BridgeError(`Köprü HTTP ${res.statusCode}: ${text}`));
          try { resolve(JSON.parse(text)); } catch (e) { reject(new BridgeError('Köprüden geçersiz yanıt.')); }
        });
      });
      req.on('error', () => reject(new BridgeError('FreeCAD\'e ulaşılamadı (kapalı olabilir).', true)));
      // FreeCAD is running but busy (long recompute/meshing holds the GIL, or a dialog is open): not offline
      req.setTimeout(timeoutMs, () => {
        reject(new BridgeError('FreeCAD yanıt vermiyor (meşgul olabilir).', false));
        req.destroy();
      });
      if (data) req.write(data);
      req.end();
    });
  }

  version() { return this.request('GET', '/version', undefined, 3000); }
  health() { return this.request('GET', '/health', undefined, 3000); }

  async ui(action, args = {}, timeoutMs = 60000) {
    const res = await this.request('POST', '/ui', { action, args }, timeoutMs);
    if (!res.ok) throw new BridgeError(res.error || 'İşlem başarısız.');
    return res.result;
  }

  // AI tools (same ones the MCP server exposes). Returns {content, is_error, image}.
  tool(name, args = {}, timeoutMs = 900000) {
    return this.request('POST', '/call', { name, arguments: args }, timeoutMs);
  }

  async toolJson(name, args = {}, timeoutMs) {
    const res = await this.tool(name, args, timeoutMs);
    if (res.is_error) throw new BridgeError(res.content);
    try { return JSON.parse(res.content); } catch (_) { return res.content; }
  }
}

module.exports = { Bridge, BridgeError, findBridgeInfo };
