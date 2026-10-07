// Unit tests for the extension's Node modules (no VS Code needed). Run: npm test
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');

// agents.js requires 'vscode'; give it a minimal stand-in
const realLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === 'vscode') return { extensions: { getExtension: () => undefined } };
  return realLoad.call(this, request, ...rest);
};

const root = path.join(__dirname, '..', '..');
const mcpSetup = require(path.join(root, 'mcp_setup.js'));
const freecad = require(path.join(root, 'freecad.js'));
const agents = require(path.join(root, 'agents.js'));

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'cadai-test-'));

test('upsertJson merges and keeps other servers', () => {
  const file = path.join(tmp(), 'mcp_settings.json');
  fs.writeFileSync(file, JSON.stringify({ mcpServers: { other: { command: 'x' } }, theme: 'dark' }));
  mcpSetup.upsertJson(file, 'mcpServers', { command: 'py', args: ['s.py'] });
  const obj = JSON.parse(fs.readFileSync(file, 'utf8'));
  assert.deepEqual(Object.keys(obj.mcpServers).sort(), ['cadai-freecad', 'other']);
  assert.equal(obj.theme, 'dark');
});

test('a config that cannot be parsed is never overwritten', () => {
  const file = path.join(tmp(), 'broken.json');
  fs.writeFileSync(file, '{ not json');
  assert.throws(() => mcpSetup.upsertJson(file, 'mcpServers', {}));
  assert.equal(fs.readFileSync(file, 'utf8'), '{ not json');
});

test('removeJson drops only our entry', () => {
  const file = path.join(tmp(), 'mcp.json');
  fs.writeFileSync(file, JSON.stringify({ servers: { 'cadai-freecad': {}, keep: {} } }));
  assert.equal(mcpSetup.removeJson(file, 'servers'), true);
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')).servers, { keep: {} });
  assert.equal(mcpSetup.removeJson(file, 'servers'), false);
});

test('Codex TOML: our table is replaced, everything else is kept', () => {
  const file = path.join(tmp(), 'config.toml');
  fs.writeFileSync(file, 'model = "gpt"\n\n[mcp_servers.cadai-freecad]\ncommand = \'old\'\n\n[mcp_servers.other]\ncommand = \'o\'\n');
  mcpSetup.upsertCodexToml(file, "C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe", ['C:\\s.py']);
  const text = fs.readFileSync(file, 'utf8');
  assert.match(text, /model = "gpt"/);
  assert.match(text, /\[mcp_servers\.other\]/);
  assert.doesNotMatch(text, /'old'/);
  assert.equal((text.match(/\[mcp_servers\.cadai-freecad\]/g) || []).length, 1);
  assert.match(text, /tool_timeout_sec = 900/);
});

test('read-only auto-approve list contains the new read-only tools but no tool that changes the model', () => {
  assert.ok(mcpSetup.READ_ONLY.includes('check_design_requirements'));
  assert.ok(!mcpSetup.READ_ONLY.includes('set_design_requirements'));
  for (const t of ['dfm_check', 'freecad_recipes', 'search_parts', 'get_markers', 'design_history', 'external_tools', 'render_status']) assert.ok(mcpSetup.READ_ONLY.includes(t), t);
  for (const t of ['run_python', 'set_property', 'insert_part', 'fem_run', 'fem_convergence', 'export_model', 'open_design_version', 'technical_drawing',
    'render', 'code_cad', 'kicad_board']) {
    assert.ok(!mcpSetup.READ_ONLY.includes(t), t);
  }
});

test('every button in the webviews runs an allowed, registered and declared command', () => {
  const ext = fs.readFileSync(path.join(root, 'extension.js'), 'utf8');
  const allowed = new Set(ext.match(/const WEBVIEW_COMMANDS = new Set\(\[([\s\S]*?)\]\)/)[1].match(/'[^']+'/g).map((q) => q.slice(1, -1)));
  const registered = new Set([...ext.matchAll(/register\('([^']+)'/g)].map((m) => m[1]));
  const declared = new Set(JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8')).contributes.commands.map((c) => c.command));
  const media = path.join(root, 'media');
  const buttons = fs.readdirSync(media).filter((f) => f.endsWith('.html'))
    .flatMap((f) => [...fs.readFileSync(path.join(media, f), 'utf8').matchAll(/data-command="([^"]+)"/g)].map((m) => m[1]));
  assert.ok(buttons.includes('cadai.technicalDrawing'));
  for (const c of buttons) {
    assert.ok(allowed.has(c), `${c} is not in WEBVIEW_COMMANDS`);
    assert.ok(registered.has(c), `${c} is not registered`);
    assert.ok(declared.has(c), `${c} is not in package.json`);
  }
});

test('with native VS Code registration the mcp.json target only cleans up an old entry', () => {
  const userDir = tmp();
  const base = { userDir, claudeCli: null, installed: new Set(), command: 'py', args: ['s.py'] };
  assert.ok(mcpSetup.targets(base).some((t) => t.id === 'vscode'));
  assert.ok(!mcpSetup.targets(Object.assign({ nativeVsCode: true }, base)).some((t) => t.id === 'vscode'));
  fs.writeFileSync(path.join(userDir, 'mcp.json'), JSON.stringify({ servers: { 'cadai-freecad': { command: 'old' } } }));
  const cleanup = mcpSetup.targets(Object.assign({ nativeVsCode: true }, base)).find((t) => t.id === 'vscode');
  cleanup.apply();
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(userDir, 'mcp.json'), 'utf8')).servers, {});
});

test('FreeCAD add-on install, update and developer link', () => {
  const src = tmp(), user = tmp();
  fs.mkdirSync(path.join(src, 'cadai'));
  fs.writeFileSync(path.join(src, 'cadai', '__init__.py'), '__version__ = "0.4.0"\n');
  assert.equal(freecad.installAddon(src, user).status, 'installed');
  assert.equal(freecad.installAddon(src, user).status, 'current');
  fs.writeFileSync(path.join(src, 'cadai', '__init__.py'), '__version__ = "0.4.1"\n');
  const r = freecad.installAddon(src, user);
  assert.deepEqual([r.status, r.version], ['updated', '0.4.1']);
  assert.equal(freecad.addonVersion(path.join(user, 'Mod', 'CadAI')), '0.4.1');
});

test('version comparison', () => {
  assert.ok(freecad.versionGreater('0.10.0', '0.9.9'));
  assert.ok(!freecad.versionGreater('0.4.0', '0.4.0'));
  assert.ok(freecad.versionGreater('1.0', '0.99.99'));
});

test('CLI prompt is one line and never looks like an option', () => {
  assert.equal(agents.cliSafe('-rf "x"\nline2'), ` -rf 'x' | line2`);
  assert.ok(!agents.cliSafe('a\r\nb').includes('\n'));
});

test('small-model toolset reaches every agent config as CADAI_TOOLSET', (t) => {
  const fakeHome = tmp();
  fs.mkdirSync(path.join(fakeHome, '.codex'));
  t.mock.method(os, 'homedir', () => fakeHome); // targets must never write the user's actual agent settings
  assert.deepEqual(mcpSetup.toolsetEnv('full'), {});
  assert.deepEqual(mcpSetup.toolsetEnv(undefined), {});
  assert.deepEqual(mcpSetup.toolsetEnv('small+fem+drawing'), { CADAI_TOOLSET: 'small+fem+drawing' });
  const userDir = tmp();
  const env = mcpSetup.toolsetEnv('small');
  const list = mcpSetup.targets({ userDir, claudeCli: null, installed: new Set(['saoudrizwan.claude-dev']), command: 'py',
    args: ['s.py'], env });
  for (const target of list) target.apply();
  assert.match(fs.readFileSync(path.join(fakeHome, '.codex', 'config.toml'), 'utf8'), /CADAI_TOOLSET = 'small'/);
  const cline = JSON.parse(fs.readFileSync(path.join(userDir, 'globalStorage', 'saoudrizwan.claude-dev', 'settings',
    'cline_mcp_settings.json'), 'utf8')).mcpServers['cadai-freecad'];
  assert.deepEqual(cline.env, { CADAI_TOOLSET: 'small' });
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(userDir, 'mcp.json'), 'utf8')).servers['cadai-freecad'].env, env);
  const toml = path.join(tmp(), 'config.toml');
  mcpSetup.upsertCodexToml(toml, 'py', ['s.py'], env);
  assert.match(fs.readFileSync(toml, 'utf8'), /env = \{ CADAI_TOOLSET = 'small' \}/);
  const plain = mcpSetup.targets({ userDir: tmp(), claudeCli: null, installed: new Set(['saoudrizwan.claude-dev']), command: 'py', args: [] });
  const plainCline = plain.find((target) => target.id === 'cline');
  plainCline.apply();
  assert.ok(!('env' in JSON.parse(fs.readFileSync(plainCline.describe, 'utf8')).mcpServers['cadai-freecad']));
});
