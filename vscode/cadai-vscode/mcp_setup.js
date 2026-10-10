// "Connect AI agents": register the cadai MCP server with every installed agent. The server finds the running CAD
// program itself (FreeCAD or Fusion 360), so one entry serves both; older per-program entries are removed.
// Each writer merges into the agent's own config and keeps everything else; a config that cannot be parsed is
// never overwritten.
'use strict';

const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const NAME = 'cadai';
// Entries written by CadAI <= 0.18.2. cadai-fusion was pinned to one Fusion session and broke on every restart.
const LEGACY_NAMES = ['cadai-freecad', 'cadai-fusion'];
// read-only tools: safe to auto-approve; tools that change the model or write files always ask
// cad_select_session is deliberately absent: it changes which CAD program later (approved) edits reach.
const READ_ONLY = ['cad_bridge_status', 'cad_refresh_context', 'list_parameters', 'freecad_bridge_status', 'get_document_summary', 'get_selection', 'get_markers', 'find_faces',
  'list_edges', 'measure', 'check_design_requirements', 'capture_view', 'beam_hand_calc', 'fem_status', 'dfm_check', 'freecad_recipes', 'search_parts', 'design_history',
  'external_tools', 'code_cad_source', 'render_status', 'inspect_holes'];

function readJson(file) {
  if (!fs.existsSync(file)) return {};
  const text = fs.readFileSync(file, 'utf8');
  if (!text.trim()) return {};
  return JSON.parse(text); // throws on invalid JSON: caller reports it, file stays untouched
}

function writeJson(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(obj, null, 2) + '\n');
}

function upsertJson(file, key, entry, name = NAME) {
  const obj = readJson(file);
  obj[key] = Object.assign({}, obj[key], { [name]: entry });
  writeJson(file, obj);
}

/** Remove our entry (if any); returns true when the file changed. */
function removeJson(file, key, name = NAME) {
  const obj = readJson(file);
  if (!obj[key] || !(name in obj[key])) return false;
  delete obj[key][name];
  writeJson(file, obj);
  return true;
}

function tomlString(s) {
  return s.includes("'") ? `"${s.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"` : `'${s}'`;
}

/**
 * Replace (or append) the [mcp_servers.cadai] table in Codex's config.toml, leaving the rest as is. Tables named in
 * `remove` (older CadAI entries) and our comment line above them are dropped.
 */
function upsertCodexToml(file, command, args, env = {}, name = NAME, remove = []) {
  const text = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
  const lines = text.split(/\r?\n/);
  const names = [name, ...remove];
  const out = [];
  let skipping = false;
  for (const line of lines) {
    const header = line.match(/^\s*\[([^\]]+)\]\s*$/);
    if (header) {
      skipping = names.some((n) => header[1] === `mcp_servers.${n}` || header[1].startsWith(`mcp_servers.${n}.`));
      if (skipping && (out[out.length - 1] || '').startsWith('# CadAI:')) out.pop(); // our comment above it
    }
    if (!skipping) out.push(line);
  }
  while (out.length && out[out.length - 1].trim() === '') out.pop();
  out.push('', '# CadAI: FreeCAD / Fusion 360 tools (3D markers, modeling, FEM)', `[mcp_servers.${name}]`,
    `command = ${tomlString(command)}`, `args = [${args.map(tomlString).join(', ')}]`,
    ...(Object.keys(env).length ? [`env = { ${Object.entries(env).map(([k, v]) => `${k} = ${tomlString(v)}`).join(', ')} }`] : []),
    'startup_timeout_sec = 30', 'tool_timeout_sec = 900', '');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, out.join('\n'));
}

/**
 * Targets for one machine. `userDir` is VS Code's User folder (…/Code/User), `claudeCli` the claude executable
 * (or null), `env` extra environment for the server (e.g. CADAI_TOOLSET=small for small local models).
 * Returns [{id, label, describe, apply()}].
 */
function targets({ userDir, claudeCli, installed, command, args, nativeVsCode = false, env = {}, name = NAME,
  legacy = name === NAME ? LEGACY_NAMES : [] }) {
  const gs = (ext, file) => path.join(userDir, 'globalStorage', ext, 'settings', file);
  const hasEnv = Object.keys(env).length > 0;
  const roEntry = (approveKey) => Object.assign({ command, args, timeout: 900, disabled: false, [approveKey]: READ_ONLY },
    hasEnv ? { env } : {});
  const envFlags = Object.entries(env).flatMap(([k, v]) => ['-e', `${k}=${v}`]);
  const list = [];
  if (claudeCli) {
    list.push({ id: 'claude', label: 'Claude Code', describe: `${path.basename(claudeCli)} mcp add -s user ${name}`,
      apply: () => {
        for (const old of [name, ...legacy]) {
          try { cp.execFileSync(claudeCli, ['mcp', 'remove', '-s', 'user', old], { stdio: 'ignore', timeout: 30000, windowsHide: true }); } catch (_) { /* not there */ }
        }
        cp.execFileSync(claudeCli, ['mcp', 'add', '-s', 'user', ...envFlags, name, '--', command, ...args], { stdio: 'pipe', timeout: 30000, windowsHide: true });
      } });
  }
  const codexToml = path.join(os.homedir(), '.codex', 'config.toml');
  if (installed.has('openai.chatgpt') || fs.existsSync(path.dirname(codexToml))) {
    list.push({ id: 'codex', label: 'Codex', describe: codexToml, apply: () => upsertCodexToml(codexToml, command, args, env, name, legacy) });
  }
  const jsonTargets = [
    ['saoudrizwan.claude-dev', 'cline', 'Cline', gs('saoudrizwan.claude-dev', 'cline_mcp_settings.json'), 'mcpServers', roEntry('autoApprove')],
    ['kilocode.kilo-code', 'kilo', 'Kilo Code', gs('kilocode.kilo-code', 'mcp_settings.json'), 'mcpServers', roEntry('alwaysAllow')],
    ['rooveterinaryinc.roo-cline', 'roo', 'Roo Code', gs('rooveterinaryinc.roo-cline', 'mcp_settings.json'), 'mcpServers', roEntry('alwaysAllow')],
  ];
  for (const [ext, id, label, file, key, entry] of jsonTargets) {
    if (installed.has(ext)) {
      list.push({ id, label, describe: file, apply: () => { upsertJson(file, key, entry, name); for (const old of legacy) removeJson(file, key, old); } });
    }
  }
  // VS Code's own MCP support (GitHub Copilot agent mode and other chat extensions). When the extension registers
  // the server through VS Code's API, an mcp.json entry would show the same server twice: offer to remove it.
  const vscodeMcp = path.join(userDir, 'mcp.json');
  if (!nativeVsCode) {
    list.push({ id: 'vscode', label: 'VS Code / GitHub Copilot', describe: vscodeMcp,
      apply: () => {
        upsertJson(vscodeMcp, 'servers', Object.assign({ type: 'stdio', command, args }, hasEnv ? { env } : {}), name);
        for (const old of legacy) removeJson(vscodeMcp, 'servers', old);
      } });
  } else {
    let present = [];
    try { present = [name, ...legacy].filter((n) => n in (readJson(vscodeMcp).servers || {})); } catch (_) { /* unreadable: leave it alone */ }
    if (present.length) {
      list.push({ id: 'vscode', label: 'VS Code / GitHub Copilot', describe: 'CadAI artık yerel API ile kayıtlı; mcp.json içindeki eski kopya kaldırılacak',
        apply: () => { for (const n of present) removeJson(vscodeMcp, 'servers', n); } });
    }
  }
  return list;
}

/** Server environment from the cadai.mcp.toolset setting ('full', 'small', 'small+fem+drawing', 'small+all'). */
function toolsetEnv(toolset) {
  const value = String(toolset || 'full').trim().toLowerCase();
  return value.startsWith('small') ? { CADAI_TOOLSET: value } : {};
}

module.exports = { NAME, LEGACY_NAMES, READ_ONLY, targets, toolsetEnv, upsertJson, removeJson, upsertCodexToml, readJson };
