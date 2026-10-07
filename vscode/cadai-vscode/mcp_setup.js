// "Connect AI agents": register the cadai-freecad MCP server with every installed agent.
// Each writer merges into the agent's own config and keeps everything else; a config that cannot be parsed is
// never overwritten.
'use strict';

const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const NAME = 'cadai-freecad';
// read-only tools: safe to auto-approve; tools that change the model or write files always ask
const READ_ONLY = ['freecad_bridge_status', 'get_document_summary', 'get_selection', 'get_markers', 'find_faces',
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

function upsertJson(file, key, entry) {
  const obj = readJson(file);
  obj[key] = Object.assign({}, obj[key], { [NAME]: entry });
  writeJson(file, obj);
}

/** Remove our entry (if any); returns true when the file changed. */
function removeJson(file, key) {
  const obj = readJson(file);
  if (!obj[key] || !(NAME in obj[key])) return false;
  delete obj[key][NAME];
  writeJson(file, obj);
  return true;
}

function tomlString(s) {
  return s.includes("'") ? `"${s.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"` : `'${s}'`;
}

/** Replace (or append) the [mcp_servers.cadai-freecad] table in Codex's config.toml, leaving the rest as is. */
function upsertCodexToml(file, command, args, env = {}) {
  const text = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
  const lines = text.split(/\r?\n/);
  const out = [];
  let skipping = false;
  for (const line of lines) {
    const header = line.match(/^\s*\[([^\]]+)\]\s*$/);
    if (header) skipping = header[1] === `mcp_servers.${NAME}` || header[1].startsWith(`mcp_servers.${NAME}.`);
    if (!skipping) out.push(line);
  }
  while (out.length && out[out.length - 1].trim() === '') out.pop();
  out.push('', '# CadAI: FreeCAD tools (3D markers, modeling, FEM)', `[mcp_servers.${NAME}]`,
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
function targets({ userDir, claudeCli, installed, command, args, nativeVsCode = false, env = {} }) {
  const gs = (ext, file) => path.join(userDir, 'globalStorage', ext, 'settings', file);
  const hasEnv = Object.keys(env).length > 0;
  const roEntry = (approveKey) => Object.assign({ command, args, timeout: 900, disabled: false, [approveKey]: READ_ONLY },
    hasEnv ? { env } : {});
  const envFlags = Object.entries(env).flatMap(([k, v]) => ['-e', `${k}=${v}`]);
  const list = [];
  if (claudeCli) {
    list.push({ id: 'claude', label: 'Claude Code', describe: `${path.basename(claudeCli)} mcp add -s user ${NAME}`,
      apply: () => {
        try { cp.execFileSync(claudeCli, ['mcp', 'remove', '-s', 'user', NAME], { stdio: 'ignore', timeout: 30000, windowsHide: true }); } catch (_) { /* not there */ }
        cp.execFileSync(claudeCli, ['mcp', 'add', '-s', 'user', ...envFlags, NAME, '--', command, ...args], { stdio: 'pipe', timeout: 30000, windowsHide: true });
      } });
  }
  const codexToml = path.join(os.homedir(), '.codex', 'config.toml');
  if (installed.has('openai.chatgpt') || fs.existsSync(path.dirname(codexToml))) {
    list.push({ id: 'codex', label: 'Codex', describe: codexToml, apply: () => upsertCodexToml(codexToml, command, args, env) });
  }
  const jsonTargets = [
    ['saoudrizwan.claude-dev', 'cline', 'Cline', gs('saoudrizwan.claude-dev', 'cline_mcp_settings.json'), 'mcpServers', roEntry('autoApprove')],
    ['kilocode.kilo-code', 'kilo', 'Kilo Code', gs('kilocode.kilo-code', 'mcp_settings.json'), 'mcpServers', roEntry('alwaysAllow')],
    ['rooveterinaryinc.roo-cline', 'roo', 'Roo Code', gs('rooveterinaryinc.roo-cline', 'mcp_settings.json'), 'mcpServers', roEntry('alwaysAllow')],
  ];
  for (const [ext, id, label, file, key, entry] of jsonTargets) {
    if (installed.has(ext)) list.push({ id, label, describe: file, apply: () => upsertJson(file, key, entry) });
  }
  // VS Code's own MCP support (GitHub Copilot agent mode and other chat extensions). When the extension registers
  // the server through VS Code's API, an mcp.json entry would show the same server twice: offer to remove it.
  const vscodeMcp = path.join(userDir, 'mcp.json');
  if (!nativeVsCode) {
    list.push({ id: 'vscode', label: 'VS Code / GitHub Copilot', describe: vscodeMcp,
      apply: () => upsertJson(vscodeMcp, 'servers', Object.assign({ type: 'stdio', command, args }, hasEnv ? { env } : {})) });
  } else {
    let stale = false;
    try { stale = NAME in (readJson(vscodeMcp).servers || {}); } catch (_) { /* unreadable: leave it alone */ }
    if (stale) {
      list.push({ id: 'vscode', label: 'VS Code / GitHub Copilot', describe: 'CadAI artık yerel API ile kayıtlı; mcp.json içindeki eski kopya kaldırılacak',
        apply: () => removeJson(vscodeMcp, 'servers') });
    }
  }
  return list;
}

/** Server environment from the cadai.mcp.toolset setting ('full', 'small', 'small+fem+drawing', 'small+all'). */
function toolsetEnv(toolset) {
  const value = String(toolset || 'full').trim().toLowerCase();
  return value.startsWith('small') ? { CADAI_TOOLSET: value } : {};
}

module.exports = { NAME, READ_ONLY, targets, toolsetEnv, upsertJson, removeJson, upsertCodexToml, readJson };
