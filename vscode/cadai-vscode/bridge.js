// Client for the CadAI bridge running inside FreeCAD (see freecad/CadAI/cadai/bridge.py).
'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');
const os = require('os');
const { AsyncLocalStorage } = require('async_hooks');

function candidateDirs() {
  // explicit directory: use only it (tests, multiple FreeCAD installs)
  if (process.env.CADAI_BRIDGE_DIR) return [process.env.CADAI_BRIDGE_DIR];
  if (process.env.CADAI_SESSIONS_DIR) return []; // isolated registry; never inspect another user's legacy bridge
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

function sessionsDir() {
  return process.env.CADAI_SESSIONS_DIR || path.join(process.env.APPDATA ||
    (process.platform === 'darwin' ? path.join(os.homedir(), 'Library', 'Application Support')
      : path.join(os.homedir(), '.local', 'share')), 'CadAI', 'sessions');
}

function listBridgeInfos() {
  const files = candidateDirs().map((dir) => path.join(dir, 'bridge.json'));
  if (!process.env.CADAI_BRIDGE_DIR) {
    try { for (const dir of fs.readdirSync(sessionsDir())) files.push(path.join(sessionsDir(), dir, 'bridge.json')); } catch (_) { /* no sessions */ }
  }
  const infos = new Map();
  for (const file of files) {
    try {
      const info = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (info.pid) { try { process.kill(info.pid, 0); } catch (_) { continue; } }
      const url = new URL(info.url);
      if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || !info.token) continue;
      info.backend_id = info.backend_id || 'freecad';
      infos.set(info.session_id || info.url, { ...info, file });
    } catch (_) { /* invalid or stale file */ }
  }
  return [...infos.values()];
}

// The session the user chose (or the view connected to) last. The MCP server (auto mode) reads the same file, so
// agents act on the program shown in VS Code.
function activeSessionFile() { return path.join(sessionsDir(), 'active-session.json'); } // a file, not a session dir

function readActiveSession() {
  try { return JSON.parse(fs.readFileSync(activeSessionFile(), 'utf8')).session_id || null; } catch (_) { return null; }
}

function writeActiveSession(info) {
  if (!info || !info.session_id || process.env.CADAI_BRIDGE_DIR) return;
  try {
    fs.mkdirSync(path.dirname(activeSessionFile()), { recursive: true });
    fs.writeFileSync(activeSessionFile(), JSON.stringify({ session_id: info.session_id, backend_id: info.backend_id }));
  } catch (_) { /* read-only profile: agents then fall back to the only running session */ }
}

function isAlive(info) {
  if (!info || !info.pid) return true;
  try { process.kill(info.pid, 0); return true; } catch (e) { return e.code === 'EPERM'; }
}

// Automatic connection: the session chosen last if it still runs, else the only running CAD program (FreeCAD or
// Fusion). With several running and none chosen it never guesses.
function findBridgeInfo() {
  const candidates = listBridgeInfos();
  const active = readActiveSession();
  const chosen = active && candidates.find((info) => info.session_id === active);
  if (chosen) return chosen;
  if (candidates.length > 1) {
    const names = [...new Set(candidates.map((i) => ({ freecad: 'FreeCAD', fusion: 'Fusion 360' })[i.backend_id] || i.backend_id))].join(', ');
    throw new BridgeError(`Birden çok CAD oturumu açık (${names}). CadAI → CAD oturumunu seç komutuyla hangisinde çalışılacağını seçin.`, true);
  }
  return candidates[0] || null;
}

class BridgeError extends Error {
  constructor(message, offline) { super(message); this.offline = !!offline; }
}

class Bridge {
  constructor() {
    this.info = null; this.context = null; this.capabilities = null; this.generation = 0;
    this.operations = new AsyncLocalStorage();
  }

  operation(fn) {
    if (this.operations.getStore()) return fn();
    return this.operations.run({ info: this.info, context: this.context, generation: this.generation }, fn);
  }

  outsideOperation(fn) { return this.operations.exit(fn); }

  acceptContext(context) {
    if (!context) return;
    this.context = context;
    const scope = this.operations.getStore();
    if (scope) scope.context = context;
  }

  async select(info) {
    this.generation++;
    this.info = info;
    this.context = this.capabilities = null;
    await this.bindDocument();
    this.capabilities = await this.request('GET', '/capabilities');
    writeActiveSession(info);
  }

  /** The CAD process behind the current session exited (closed, crashed, restarted): forget it so that the next
   * request discovers the new session. Returns true when something was dropped. */
  dropDeadSession() {
    if (!this.info || isAlive(this.info)) return false;
    this.generation++;
    this.info = this.context = this.capabilities = null;
    return true;
  }

  async reload() {
    const original = this.info;
    const generation = this.generation;
    const documentName = this.context?.document_name;
    const reloaded = await this.ui('reload_addon');
    // Fusion reloads its adapter behind the same bridge and session; only the tool list can change.
    if (reloaded?.same_session) return this.outsideOperation(() => this.select(original));
    return this.outsideOperation(async () => {
      for (let attempt = 0; attempt < 50; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        if (generation !== this.generation) throw new BridgeError('CAD oturumu değişti; yeniden bağlantı iptal edildi.', false);
        const candidates = listBridgeInfos().filter((info) => original?.pid && info.pid === original.pid &&
          info.backend_id === (original.backend_id || 'freecad') && info.token !== original.token);
        if (candidates.length !== 1) continue;
        const probe = new Bridge();
        try { await probe.select(candidates[0]); } catch (_) { continue; }
        if (generation !== this.generation) throw new BridgeError('CAD oturumu değişti; yeniden bağlantı iptal edildi.', false);
        if (documentName && probe.context?.document_name !== documentName) {
          throw new BridgeError('Yeniden yükleme sırasında belge değişti. CAD oturumunu yeniden seçin.', false);
        }
        await this.select(candidates[0]);
        return;
      }
      throw new BridgeError('Yeniden yüklenen CAD oturumu bulunamadı. CAD oturumunu yeniden seçin.', true);
    });
  }

  get backend() { return this.info?.backend_id || 'freecad'; }
  get label() { return { freecad: 'FreeCAD', fusion: 'Fusion 360' }[this.backend] || this.backend; }

  async bindDocument(timeoutMs = this.backend === 'fusion' ? 20000 : 5000) {
    const health = await this.health();
    if (health.protocol_version) this.acceptContext(await this.request('GET', '/session', undefined, timeoutMs));
    if (!this.capabilities && health.protocol_version) this.capabilities = await this.request('GET', '/capabilities', undefined, timeoutMs);
    return this.context;
  }

  request(method, route, body, timeoutMs = 30000) {
    const scope = this.operations.getStore();
    if (scope && scope.generation !== this.generation) {
      return Promise.reject(new BridgeError('CAD oturumu değişti; bekleyen işlem uygulanmadı.', false));
    }
    let info;
    try {
      info = scope?.info || this.info;
      if (!info) { info = findBridgeInfo(); writeActiveSession(info); }
    } catch (e) { return Promise.reject(e); }
    if (!info) return Promise.reject(new BridgeError('CAD köprüsü bulunamadı. Programı açın veya CAD oturumunu seçin.', true));
    this.info = info;
    if (scope) scope.info = info;
    const generation = this.generation;
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
          if (generation !== this.generation) return reject(new BridgeError('CAD oturumu değişti; eski yanıt kullanılmadı.', false));
          const text = Buffer.concat(chunks).toString('utf8');
          if (res.statusCode !== 200) return reject(new BridgeError(`Köprü HTTP ${res.statusCode}: ${text}`));
          try { resolve(JSON.parse(text)); } catch (e) { reject(new BridgeError('Köprüden geçersiz yanıt.')); }
        });
      });
      req.on('error', () => reject(new BridgeError(`${this.label} oturumuna ulaşılamadı. CAD oturumunu yeniden seçin.`, true)));
      // FreeCAD is running but busy (long recompute/meshing holds the GIL, or a dialog is open): not offline
      req.setTimeout(timeoutMs, () => {
        reject(new BridgeError(`${this.label} yanıt vermiyor (meşgul olabilir).${this.backend === 'fusion' ? ' Fusion’daki Scripts and Add-Ins ve diğer açık diyalogları kapatıp tekrar deneyin.' : ''}`, false));
        req.destroy();
      });
      if (data) req.write(data);
      req.end();
    });
  }

  version() { return this.request('GET', '/version', undefined, 3000); }
  health() { return this.request('GET', '/health', undefined, 3000); }

  async ui(action, args = {}, timeoutMs = 60000, target = undefined) {
    if (this.capabilities?.ui_actions && !this.capabilities.ui_actions.includes(action)) {
      throw new BridgeError(`${this.label} adaptörü bu işlemi desteklemiyor: ${action}`, false);
    }
    if (!this.context) await this.bindDocument();
    const res = await this.request('POST', '/ui', { action, args,
      target: target || this.operations.getStore()?.context || this.context }, timeoutMs);
    if (!res.ok) throw new BridgeError(res.error || 'İşlem başarısız.');
    this.acceptContext(res.context);
    return res.result;
  }

  // AI tools (same ones the MCP server exposes). Returns {content, is_error, image}.
  async tool(name, args = {}, timeoutMs = 900000) {
    if (this.capabilities?.tools && !this.capabilities.tools.includes(name)) {
      throw new BridgeError(`${this.label} adaptörü bu aracı desteklemiyor: ${name}`, false);
    }
    if (!this.context) await this.bindDocument();
    const res = await this.request('POST', '/call', { name, arguments: args,
      target: this.operations.getStore()?.context || this.context }, timeoutMs);
    if (!res.is_error) this.acceptContext(res.context);
    return res;
  }

  async toolJson(name, args = {}, timeoutMs) {
    const res = await this.tool(name, args, timeoutMs);
    if (res.is_error) throw new BridgeError(res.content);
    try { return JSON.parse(res.content); } catch (_) { return res.content; }
  }
}

module.exports = { Bridge, BridgeError, findBridgeInfo, listBridgeInfos, sessionsDir, readActiveSession, activeSessionFile };
