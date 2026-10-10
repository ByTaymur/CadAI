// AI agents the user can talk to. All of them use the same "cadai" MCP tools; this module only decides
// where the conversation happens and how a prepared message (e.g. the marker list) gets there.
'use strict';

const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const AGENTS = [
  { id: 'claude-code', label: 'Claude Code', detail: 'mesaj kutusuna yazılır', ext: 'anthropic.claude-code',
    focus: ['claude-vscode.focus', 'claude-vscode.sidebar.open', 'claude-vscode.editor.openLast'],
    // Same call Claude Code's own deep link (vscode://anthropic.claude-code/open?prompt=…) makes: new conversation,
    // prompt pre-filled in the input box. Claude Code never auto-sends; the user presses Enter.
    prefill: { command: 'claude-vscode.primaryEditor.open', args: (p) => [undefined, p],
      uri: (s, p) => `${s}://anthropic.claude-code/open?prompt=${encodeURIComponent(p)}` } },
  { id: 'claude-cli', label: 'Claude Code (terminal)', detail: 'mesaj doğrudan gönderilir', cli: 'claude' },
  { id: 'codex', label: 'Codex', detail: 'mesaj kutusuna yazılır', ext: 'openai.chatgpt', focus: ['chatgpt.openSidebar'],
    // Codex routes vscode://openai.chatgpt/<route>?<query> into its webview; its home route pre-fills `prompt`.
    // Not a documented contract, so the text also goes to the clipboard as a fallback.
    prefill: { uri: (s, p) => `${s}://openai.chatgpt/?prompt=${encodeURIComponent(p)}`, alsoClipboard: true } },
  { id: 'codex-cli', label: 'Codex (terminal)', detail: 'mesaj doğrudan gönderilir', cli: 'codex' },
  { id: 'cline', label: 'Cline', detail: 'VS Code paneli', ext: 'saoudrizwan.claude-dev',
    focus: ['cline.focusChatInput', 'claude-dev.SidebarProvider.focus'] },
  { id: 'kilo', label: 'Kilo Code', detail: 'VS Code paneli', ext: 'kilocode.kilo-code',
    focus: ['kilo-code.new.plusButtonClicked', 'kilo-code.SidebarProvider.focus'] },
  { id: 'roo', label: 'Roo Code', detail: 'VS Code paneli', ext: 'rooveterinaryinc.roo-cline', focus: ['roo-cline.SidebarProvider.focus'] },
  { id: 'copilot', label: 'GitHub Copilot Chat', detail: 'VS Code sohbeti', ext: 'github.copilot-chat', chat: true },
  { id: 'clipboard', label: 'Sadece panoya kopyala', detail: 'istediğin yere yapıştır' },
];

// Known install locations for CLIs that are often not on PATH.
function cliFallbacks(name) {
  const home = os.homedir();
  const local = process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  if (name === 'claude') return [path.join(home, '.local', 'bin', 'claude.exe')];
  if (name === 'codex') {
    const base = path.join(local, 'OpenAI', 'Codex', 'bin');
    try {
      return fs.readdirSync(base).map((d) => path.join(base, d, 'codex.exe')).filter((p) => fs.existsSync(p))
        .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
    } catch (_) { return []; }
  }
  return [];
}

function findCli(name) {
  try {
    const out = cp.execFileSync(process.platform === 'win32' ? 'where.exe' : 'which', [name],
      { encoding: 'utf8', timeout: 3000, windowsHide: true });
    // prefer real executables; npm .ps1/.cmd shims need Node, which may be missing
    const hits = out.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    const exe = hits.find((h) => /\.exe$/i.test(h)) || (process.platform !== 'win32' ? hits[0] : null);
    if (exe) return exe;
  } catch (_) { /* not on PATH */ }
  return cliFallbacks(name).find((p) => fs.existsSync(p)) || null;
}

function detect() {
  return AGENTS.map((a) => {
    let available = a.id === 'clipboard';
    let cliPath = null;
    if (a.ext) available = !!vscode.extensions.getExtension(a.ext);
    if (a.cli) { cliPath = findCli(a.cli); available = !!cliPath; }
    return Object.assign({}, a, { available, cliPath });
  });
}

const PREFERENCE = ['claude-code', 'codex', 'cline', 'kilo', 'copilot', 'roo', 'claude-cli', 'codex-cli', 'clipboard'];
function defaultAgent(list) {
  for (const id of PREFERENCE) { const a = list.find((x) => x.id === id && x.available); if (a) return a.id; }
  return 'clipboard';
}

async function tryCommands(ids) {
  for (const id of ids || []) {
    try { await vscode.commands.executeCommand(id); return true; } catch (_) { /* next */ }
  }
  return false;
}

// Windows command lines do not survive raw newlines/double quotes well; keep the prompt on one line.
// A leading '-' would be parsed by the CLI as an option, not as the prompt.
function cliSafe(text) {
  const one = text.replace(/"/g, "'").replace(/\r?\n+/g, ' | ');
  return one.startsWith('-') ? ' ' + one : one;
}

/**
 * Open the agent, optionally with a prepared message.
 * Returns how the message was delivered: 'sent' (agent already has it), 'typed' (in the input, user presses send),
 * 'clipboard' (user pastes with Ctrl+V) or 'opened' (no message).
 */
async function open(agent, prompt, cwd) {
  if (agent.cli) {
    const term = vscode.window.createTerminal({ name: `CadAI · ${agent.label}`, shellPath: agent.cliPath,
      shellArgs: prompt ? [cliSafe(prompt)] : [], cwd });
    term.show();
    return prompt ? 'sent' : 'opened';
  }
  if (agent.chat) {
    await vscode.commands.executeCommand('workbench.action.chat.open', prompt ? { query: prompt, isPartialQuery: true } : undefined);
    return prompt ? 'typed' : 'opened';
  }
  if (agent.prefill && prompt) {
    const pf = agent.prefill;
    if (pf.alsoClipboard) await vscode.env.clipboard.writeText(prompt);
    if (pf.command) {
      try {
        await vscode.commands.executeCommand(pf.command, ...pf.args(prompt));
        return 'typed';
      } catch (_) { /* fall back to the deep link */ }
    }
    if (pf.uri) {
      // openExternal on our own scheme is handled in-process by the target extension's URI handler
      const scheme = (vscode.env && vscode.env.uriScheme) || 'vscode';
      try {
        if (await vscode.env.openExternal(vscode.Uri.parse(pf.uri(scheme, prompt)))) return pf.alsoClipboard ? 'typed+clipboard' : 'typed';
      } catch (_) { /* fall through to clipboard */ }
    }
  }
  if (prompt) await vscode.env.clipboard.writeText(prompt);
  if (agent.focus) await tryCommands(agent.focus);
  return prompt ? 'clipboard' : 'opened';
}

module.exports = { AGENTS, detect, defaultAgent, open, cliSafe };
