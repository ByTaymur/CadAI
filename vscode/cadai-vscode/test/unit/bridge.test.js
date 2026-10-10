'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { Bridge, listBridgeInfos, findBridgeInfo, readActiveSession, activeSessionFile } = require('../../bridge');
const mcpSetup = require('../../mcp_setup');

function workspace(t) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cadai-adapters-'));
  const old = { dir: process.env.CADAI_BRIDGE_DIR, sessions: process.env.CADAI_SESSIONS_DIR };
  delete process.env.CADAI_BRIDGE_DIR;
  process.env.CADAI_SESSIONS_DIR = tmp;
  t.after(() => {
    if (old.dir === undefined) delete process.env.CADAI_BRIDGE_DIR; else process.env.CADAI_BRIDGE_DIR = old.dir;
    if (old.sessions === undefined) delete process.env.CADAI_SESSIONS_DIR; else process.env.CADAI_SESSIONS_DIR = old.sessions;
    fs.rmSync(tmp, { recursive: true, force: true });
  });
  return tmp;
}

async function session(t, tmp, backend, id) {
  const context = { backend_id: backend, session_id: id, document_id: 'part-A', revision: 1 };
  const calls = [];
  let delayed = null;
  const server = http.createServer((req, res) => {
    const send = (value) => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
    if (req.url === '/health') return send({ backend_id: backend, session_id: id, protocol_version: 1 });
    if (req.url === '/session') return send(context);
    if (req.url === '/capabilities') return send({ tools: ['measure', 'set_property'], ui_actions: ['tree', 'reload_addon'] });
    if (req.url === '/version') return send({ doc: context.revision, sel: 0, mk: 0 });
    let body = '';
    req.on('data', (data) => { body += data; });
    req.on('end', () => {
      const parsed = JSON.parse(body); calls.push(parsed);
      if (parsed.action === 'reload_addon') {
        // FreeCAD restarts its bridge; Fusion reloads the adapter behind the same bridge and session.
        return send({ ok: true, result: backend === 'fusion' ? { same_session: true } : {}, context: { ...context } });
      }
      const wrong = Object.keys(context).some((key) => parsed.target?.[key] !== context[key]);
      const result = { content: JSON.stringify({ backend }), is_error: wrong, context: { ...context } };
      if (delayed) delayed(() => send(result)); else send(result);
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const dir = path.join(tmp, id);
  fs.mkdirSync(dir);
  const info = { url: `http://127.0.0.1:${server.address().port}`, token: 'test', backend_id: backend,
    session_id: id, pid: process.pid, protocol_version: 1, file: path.join(dir, 'bridge.json') };
  fs.writeFileSync(info.file, JSON.stringify(info));
  return { info, context, calls, delay: (fn) => { delayed = fn; } };
}

test('the only running CAD program connects by itself; with two, only an explicit choice decides', async (t) => {
  const tmp = workspace(t);
  const fusion = await session(t, tmp, 'fusion', 'fusion');
  assert.equal(findBridgeInfo().session_id, 'fusion'); // Fusion alone: no "Fusion'a bağlan" needed
  const first = await session(t, tmp, 'freecad', 'one');
  assert.throws(findBridgeInfo, /Birden çok CAD oturumu açık \(Fusion 360, FreeCAD\)/);
  const bridge = new Bridge();
  await bridge.select(first.info); // the user's choice is remembered for the view and the agents
  assert.equal(readActiveSession(), 'one');
  assert.equal(JSON.parse(fs.readFileSync(activeSessionFile(), 'utf8')).backend_id, 'freecad');
  assert.equal(findBridgeInfo().session_id, 'one');
  await bridge.tool('measure');
  assert.equal(first.calls[0].target.session_id, 'one');
  assert.equal(fusion.calls.length, 0);
});

test('a closed CAD program is dropped and the view reconnects to the one still running', async (t) => {
  const tmp = workspace(t);
  const fusion = await session(t, tmp, 'fusion', 'fusion');
  const bridge = new Bridge();
  await bridge.select(fusion.info);
  assert.equal(bridge.dropDeadSession(), false);
  bridge.info = { ...bridge.info, pid: 2 ** 22 + 12345 }; // no such process: Fusion was closed
  const generation = bridge.generation;
  assert.equal(bridge.dropDeadSession(), true);
  assert.equal(bridge.info, null);
  assert.ok(bridge.generation > generation);
  const freecad = await session(t, tmp, 'freecad', 'restarted');
  fs.rmSync(fusion.info.file); fs.writeFileSync(path.join(tmp, 'active-session.json'), '{}');
  await bridge.tool('measure');
  assert.equal(freecad.calls.length, 1);
  assert.equal(readActiveSession(), 'restarted');
});

test('a changed document or revision cannot silently redirect a mutation', async (t) => {
  const tmp = workspace(t);
  const selected = await session(t, tmp, 'fusion', 'one');
  const bridge = new Bridge(); await bridge.select(selected.info);
  selected.context.document_id = 'part-B';
  assert.equal((await bridge.tool('set_property')).is_error, true);
  assert.equal(bridge.context.document_id, 'part-A');
  await bridge.bindDocument();
  assert.equal((await bridge.tool('set_property')).is_error, false);
  selected.context.revision = 2;
  assert.equal((await bridge.tool('set_property')).is_error, true);
});

test('a bridge file replaced by another session cannot redirect an existing client', async (t) => {
  const tmp = workspace(t);
  const a = await session(t, tmp, 'freecad', 'one');
  const b = await session(t, tmp, 'fusion', 'two');
  const bridge = new Bridge(); await bridge.select(a.info);
  fs.writeFileSync(a.info.file, JSON.stringify(b.info));
  await bridge.tool('measure');
  assert.equal(a.calls.length, 1); assert.equal(b.calls.length, 0);
});

test('responses from a previous selection cannot overwrite the current session context', async (t) => {
  const tmp = workspace(t);
  const a = await session(t, tmp, 'freecad', 'one');
  const b = await session(t, tmp, 'fusion', 'two');
  const bridge = new Bridge(); await bridge.select(a.info);
  let release;
  const received = new Promise((resolve) => a.delay((send) => { release = send; resolve(); }));
  const pending = bridge.tool('measure');
  const rejected = assert.rejects(pending, /eski yanıt/);
  await received;
  await bridge.select(b.info); release(); await rejected;
  assert.equal(bridge.context.session_id, 'two');
});

test('capabilities reject unsupported tools and UI actions before network execution', async (t) => {
  const tmp = workspace(t);
  const a = await session(t, tmp, 'fusion', 'one');
  const bridge = new Bridge(); await bridge.select(a.info);
  await assert.rejects(bridge.tool('fem_run'), /desteklemiyor/);
  await assert.rejects(bridge.ui('run_python'), /desteklemiyor/);
  assert.equal(a.calls.length, 0);
});

test('a dialog awaiting user input stays bound to its original CAD session', async (t) => {
  const tmp = workspace(t);
  const a = await session(t, tmp, 'freecad', 'one');
  const b = await session(t, tmp, 'fusion', 'two');
  const bridge = new Bridge(); await bridge.select(a.info);
  let release;
  const dialog = new Promise((resolve) => { release = resolve; });
  const operation = bridge.operation(async () => { await dialog; await bridge.tool('set_property'); });
  const rejected = assert.rejects(operation, /bekleyen işlem/);
  await bridge.select(b.info); release(); await rejected;
  assert.equal(a.calls.length, 0); assert.equal(b.calls.length, 0);
});

test('session discovery never accepts a remote URL with a bearer token', async (t) => {
  const tmp = workspace(t);
  const dir = path.join(tmp, 'remote'); fs.mkdirSync(dir);
  fs.writeFileSync(path.join(dir, 'bridge.json'), JSON.stringify({ url: 'https://example.com', token: 'secret' }));
  assert.ok(!listBridgeInfos().some((i) => i.token === 'secret'));
});

test('explicit addon reload reconnects only to a renewed session in the same CAD process', async (t) => {
  const tmp = workspace(t);
  const a = await session(t, tmp, 'freecad', 'old');
  const bridge = new Bridge(); await bridge.select(a.info);
  const b = await session(t, tmp, 'freecad', 'renewed');
  b.info.token = 'renewed-token'; fs.writeFileSync(b.info.file, JSON.stringify(b.info));
  const fusion = await session(t, tmp, 'fusion', 'fusion');
  fusion.info.token = 'also-renewed'; fs.writeFileSync(fusion.info.file, JSON.stringify(fusion.info));
  await bridge.operation(() => bridge.reload());
  assert.equal(bridge.context.session_id, 'renewed');
  await bridge.tool('set_property');
  assert.equal(b.calls.length, 1); assert.equal(fusion.calls.length, 0);
});

test('Fusion reload keeps the same session and refreshes its capabilities', async (t) => {
  const tmp = workspace(t);
  const a = await session(t, tmp, 'fusion', 'fusion');
  const bridge = new Bridge(); await bridge.select(a.info);
  const other = await session(t, tmp, 'fusion', 'other');
  other.info.token = 'renewed-token'; fs.writeFileSync(other.info.file, JSON.stringify(other.info));
  await bridge.operation(() => bridge.reload());
  assert.equal(bridge.context.session_id, 'fusion');
  assert.ok(bridge.capabilities.ui_actions.includes('reload_addon'));
  await bridge.tool('set_property');
  assert.equal(a.calls.length, 2); assert.equal(other.calls.length, 0);
});

test('connecting agents replaces old cadai-freecad / session-pinned cadai-fusion entries with one cadai', (t) => {
  const tmp = workspace(t);
  const userDir = path.join(tmp, 'User');
  const cline = path.join(userDir, 'globalStorage', 'saoudrizwan.claude-dev', 'settings', 'cline_mcp_settings.json');
  fs.mkdirSync(path.dirname(cline), { recursive: true });
  fs.writeFileSync(cline, JSON.stringify({ mcpServers: { 'cadai-freecad': { command: 'old' },
    'cadai-fusion': { env: { CADAI_SESSION_ID: 'dead-session' } }, other: { command: 'keep' } } }));
  fs.writeFileSync(path.join(userDir, 'mcp.json'), JSON.stringify({ servers: { 'cadai-fusion': {}, mine: {} } }));
  const list = mcpSetup.targets({ userDir, claudeCli: null, installed: new Set(['saoudrizwan.claude-dev']),
    command: 'py', args: ['cadai_mcp.py'], env: { CADAI_BACKEND: 'auto' } }).filter((x) => x.id !== 'codex');
  for (const target of list) target.apply();
  const servers = JSON.parse(fs.readFileSync(cline, 'utf8')).mcpServers;
  assert.deepEqual(Object.keys(servers).sort(), ['cadai', 'other']);
  assert.equal(servers.cadai.env.CADAI_BACKEND, 'auto');
  assert.ok(!servers.cadai.autoApprove.includes('cad_select_session')); // switching programs always asks the user
  assert.deepEqual(Object.keys(JSON.parse(fs.readFileSync(path.join(userDir, 'mcp.json'), 'utf8')).servers).sort(), ['cadai', 'mine']);
  const toml = path.join(tmp, 'config.toml');
  fs.writeFileSync(toml, "model = 'x'\n\n# CadAI: FreeCAD tools (3D markers, modeling, FEM)\n[mcp_servers.cadai-freecad]\ncommand = 'old'\n\n"
    + "[mcp_servers.cadai-fusion]\ncommand = 'old'\n[mcp_servers.cadai-fusion.env]\nCADAI_SESSION_ID = 'dead'\n\n[mcp_servers.other]\ncommand = 'o'\n");
  mcpSetup.upsertCodexToml(toml, 'py', ['cadai_mcp.py'], { CADAI_BACKEND: 'auto' }, mcpSetup.NAME, mcpSetup.LEGACY_NAMES);
  const text = fs.readFileSync(toml, 'utf8');
  assert.doesNotMatch(text, /cadai-freecad|cadai-fusion|'old'|dead|FreeCAD tools \(/);
  assert.match(text, /\[mcp_servers\.cadai\]/);
  assert.match(text, /\[mcp_servers\.other\]/);
});
