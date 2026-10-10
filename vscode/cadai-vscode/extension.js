// CadAI VS Code extension: drive FreeCAD from VS Code (3D view, model tree, shapes, FEM, AI, dev tools).
'use strict';

const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const path = require('path');
const { Bridge, BridgeError, listBridgeInfos, sessionsDir } = require('./bridge');
const agents = require('./agents');
const freecad = require('./freecad');
const fusionSetup = require('./fusion_setup');
const mcpSetup = require('./mcp_setup');
const gpu = require('./gpu');
const historyTree = require('./history_tree');

const bridge = new Bridge();
function supports(action) { return !bridge.capabilities || bridge.capabilities.ui_actions.includes(action); }

function assertCurrentView(target) {
  if (!bridge.context) return;
  if (['backend_id', 'session_id', 'document_id', 'revision'].some((k) => target[k] !== bridge.context[k])) {
    throw new BridgeError('CAD modeli veya oturumu değişti. Görünüm yenilendikten sonra işlemi tekrar seçin.', false);
  }
}

async function selectCADSession(backend = null) {
  const sessions = listBridgeInfos().filter((info) => !backend || info.backend_id === backend);
  const picks = await Promise.all(sessions.map(async (info) => {
    const probe = new Bridge();
    try {
      await probe.select(info);
      return { label: probe.label, description: probe.context?.document_name || 'Açık belge yok',
        detail: `Oturum ${info.session_id || info.pid || info.url}`, info };
    } catch (e) {
      log(`${probe.label} bağlantı denetimi: ${e.message}`);
      return { label: `$(warning) ${probe.label}`, description: 'Bağlantı hatası', detail: e.message, error: e.message, info };
    }
  }));
  if (!picks.length) {
    const errorFile = path.join(path.dirname(sessionsDir()), 'fusion', 'startup-error.log');
    if (fs.existsSync(errorFile)) log('Fusion başlangıç hatası:\n' + fs.readFileSync(errorFile, 'utf8'));
    const action = await vscode.window.showWarningMessage(
      `CadAI: Çalışan ${backend === 'fusion' ? 'Fusion' : 'CAD'} oturumu bulunamadı. Fusion’da Scripts and Add-Ins → Add-Ins → CadAI → Run komutunu çalıştırın.`
      + (fs.existsSync(errorFile) ? ' Fusion başlangıç hatası CadAI çıktı kanalına yazıldı.' : ''), 'CadAI çıktısını aç');
    if (action) output.show();
    return;
  }
  const pick = backend && picks.length === 1 ? picks[0]
    : await vscode.window.showQuickPick(picks, { title: 'İşlem yapılacak CAD oturumunu seçin' });
  if (!pick) return;
  if (pick.error) throw new BridgeError(`${pick.info.backend_id} bağlantısı kurulamadı: ${pick.error}`, false);
  await bridge.select(pick.info);
  try { await checkFusionVersion(); } catch (e) { log('Fusion sürüm denetimi: ' + e.message); }
  resetCADView();
  await poll();
}

function resetCADView() {
  state.connected = false;
  state.doc = state.sel = state.mk = state.hist = -1;
  state.docName = null;
  state.markers = [];
  viewerKeys = [];
  lastSceneDoc = null;
  externalStatus = null;
  // No mcpChanged here: the MCP definition does not depend on the session (auto mode follows it by itself), and
  // a definition change would restart the agents' server on every reconnect.
}

function syncFusionAddin(force) {
  const target = fusionSetup.addinTarget();
  if (!target) throw new Error('Fusion adaptörü Windows ve macOS içindir.');
  const out = fusionSetup.syncAddin(path.join(ctx.extensionPath, 'fusion-addon', 'CadAI'), target, { force });
  log(`Fusion eklentisi: ${out.action} (bu paket ${out.ours}, kurulu ${out.installed || '-'}) ${target}`);
  return { ...out, target };
}

// Runs on activation: an update of this extension also updates the Fusion add-in; Fusion loads it on its next start
// (the add-in runs on startup) or immediately through connectFusionChecked's in-place reload.
function autoSyncFusionAddin() {
  try {
    const out = syncFusionAddin(false);
    if (out.action === 'newer-installed') {
      vscode.window.showWarningMessage(`CadAI: Bu VS Code penceresi eski CadAI (${out.ours}) çalıştırıyor; kurulu Fusion eklentisi ${out.installed}. `
        + 'Komut paletinden "Developer: Reload Window" çalıştırın.', 'Pencereyi yeniden yükle')
        .then((a) => a && vscode.commands.executeCommand('workbench.action.reloadWindow'));
    }
  } catch (e) { log('Fusion eklentisi eşitlenemedi: ' + e.message); }
}

async function setupFusion() {
  const out = syncFusionAddin(true);
  if (out.action === 'newer-installed') {
    vscode.window.showWarningMessage(`CadAI: Kurulu Fusion eklentisi (${out.installed}) bu VS Code eklentisinden (${out.ours}) yeni; üzerine yazılmadı. `
      + 'VS Code penceresini yeniden yükleyin (Developer: Reload Window).', 'Pencereyi yeniden yükle')
      .then((a) => a && vscode.commands.executeCommand('workbench.action.reloadWindow'));
    return;
  }
  const running = listBridgeInfos().some((info) => info.backend_id === 'fusion');
  const action = await vscode.window.showInformationMessage(running
    ? `CadAI: Fusion eklentisi ${out.ours} kuruldu. Çalışan Fusion'a bağlanınca kod kendiliğinden yenilenir.`
    : `CadAI: Fusion eklentisi ${out.ours} kuruldu (${out.target}). Fusion'ı açın; CadAI kendiliğinden başlar. İlk kurulumda Fusion → Scripts and Add-Ins → Add-Ins → CadAI → Run.`,
  'Klasörü aç', "Fusion'a bağlan");
  if (action === 'Klasörü aç') await vscode.env.openExternal(vscode.Uri.file(out.target));
  if (action === "Fusion'a bağlan") await selectCADSession('fusion');
}

// A Fusion that started before the add-in was updated keeps running the old code. Reload it in place when the
// running add-in supports that (0.18+); otherwise say exactly what to do instead of failing in odd ways later.
async function checkFusionVersion() {
  if (bridge.backend !== 'fusion') return;
  const ours = fusionSetup.manifestVersion(path.join(ctx.extensionPath, 'fusion-addon', 'CadAI'));
  const running = (await bridge.health()).version;
  if (!ours || !running || fusionSetup.compareVersions(running, ours) >= 0) return;
  syncFusionAddin(false);
  if (bridge.capabilities?.ui_actions?.includes('reload_addon')) {
    await bridge.reload();
    log(`Fusion eklentisi ${running} → ${(await bridge.health()).version} yerinde yenilendi.`);
    return;
  }
  vscode.window.showWarningMessage(`CadAI: Fusion eski CadAI eklentisini (${running}) çalıştırıyor; kurulu sürüm ${ours}. `
    + 'Fusion → Scripts and Add-Ins → Add-Ins → CadAI: Çalıştır anahtarını kapatıp açın ya da Fusion\'ı yeniden başlatın.');
}
let ctx;
let state = { connected: false, doc: -1, sel: -1, mk: -1, hist: -1, docName: null, markers: [] };
let statusItem;
let viewerPanel = null;
let controls = null;
let treeProvider = null;
let historyProvider = null;
let historyView = null;
let polling = false;
let output;

function cfg(key) { return vscode.workspace.getConfiguration('cadai').get(key); }
function log(msg) { output.appendLine(`[${new Date().toLocaleTimeString()}] ${msg}`); }

async function guard(fn) {
  try { return await fn(); } catch (e) {
    const msg = e instanceof BridgeError || e instanceof Error ? e.message : String(e);
    vscode.window.showErrorMessage('CadAI: ' + msg);
    log('HATA ' + msg);
    return undefined;
  }
}

// ---------------- webview → command allow-list ----------------
// Webviews render data that comes from model files (labels, marker notes). Even though the CSP blocks injected
// scripts, never let a webview message run an arbitrary VS Code command: only this extension's own commands.
const WEBVIEW_COMMANDS = new Set([
  'cadai.selectCADSession', 'cadai.connectFusion', 'cadai.setupFusion',
  'cadai.startFreeCAD', 'cadai.showFreeCAD', 'cadai.openViewer', 'cadai.newDocument', 'cadai.openDocument',
  'cadai.saveDocument', 'cadai.saveDocumentAs', 'cadai.undo', 'cadai.redo', 'cadai.recompute',
  'cadai.measureSelection', 'cadai.exportStep', 'cadai.exportStl', 'cadai.technicalDrawing', 'cadai.openAI', 'cadai.chooseAgent',
  'cadai.render', 'cadai.importCode', 'cadai.externalTools',
  'cadai.markersToAI', 'cadai.runTests', 'cadai.reloadAddon', 'cadai.attachDebugger', 'cadai.showFemResult',
  'cadai.connectAgents', 'cadai.dfmCheck', 'cadai.searchParts', 'cadai.gpuDiagnostics',
  'cadai.history.toggle', 'cadai.history.show', 'cadai.history.openVault',
]);

function runWebviewCommand(command) {
  if (!WEBVIEW_COMMANDS.has(command)) {
    log('engellenen webview komutu: ' + String(command).slice(0, 80));
    return undefined;
  }
  return vscode.commands.executeCommand(command);
}

// ---------------- self-update ----------------
// VS Code only loads a newly installed extension version after the window reloads; offer that reload.
// The FreeCAD add-on is hot-reloaded automatically when its source version differs from the running one.

function versionGreater(a, b) {
  const pa = String(a).split('.').map(Number), pb = String(b).split('.').map(Number);
  for (let i = 0; i < 3; i++) { if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0); }
  return false;
}

let promptedExtVersion = null;
function checkExtensionUpdate() {
  try {
    const file = path.join(path.dirname(ctx.extensionPath), 'extensions.json');
    const entry = JSON.parse(fs.readFileSync(file, 'utf8')).find((e) => e.identifier && e.identifier.id === 'taymur.cadai');
    const running = ctx.extension.packageJSON.version;
    if (entry && versionGreater(entry.version, running) && promptedExtVersion !== entry.version) {
      promptedExtVersion = entry.version;
      vscode.window.showInformationMessage(
        `CadAI ${entry.version} yüklendi (şu an çalışan: ${running}). Yeni sürümü kullanmak için pencere yeniden yüklenmeli.`,
        'Şimdi yeniden yükle').then((choice) => {
        if (choice) vscode.commands.executeCommand('workbench.action.reloadWindow');
      });
    }
  } catch (_) { /* extensions.json missing or unreadable: nothing to do */ }
}

// ---------------- FreeCAD discovery, add-on install, agent connection ----------------

function freecadExe() { return freecad.findFreeCAD(cfg('freecadPath')); }

function bundledAddonDir() { return path.join(ctx.extensionPath, 'freecad-addon', 'CadAI'); }

/** FreeCAD's user data folder, cached per executable (sync, may be null until freecadUserDirAsync ran). */
function cachedUserDir(exe) {
  const dir = exe && ctx.globalState.get('cadai.userDir:' + exe);
  return dir && fs.existsSync(dir) ? dir : null;
}

/** Ask FreeCAD for its user data folder once per executable, then cache it. */
async function freecadUserDir(exe) {
  if (!exe) return null;
  const cached = cachedUserDir(exe);
  if (cached) return cached;
  const dir = await freecad.userDataDir(exe);
  if (dir) await ctx.globalState.update('cadai.userDir:' + exe, dir);
  return dir;
}

/** The add-on FreeCAD actually loads (developer override → installed copy → bundled copy). */
function addonDir() {
  const dev = cfg('addonPath');
  if (dev && fs.existsSync(dev)) return dev;
  const user = cachedUserDir(freecadExe());
  const installed = user && path.join(user, 'Mod', 'CadAI');
  if (installed && fs.existsSync(installed)) return installed;
  return bundledAddonDir();
}

/** Startup: silently install/update the bundled add-on when FreeCAD is present (no popups if it is not). */
async function autoSetup() {
  const exe = freecadExe();
  if (!exe) return;
  const userDir = await freecadUserDir(exe);
  if (!userDir) return;
  const installedVersion = freecad.addonVersion(path.join(userDir, 'Mod', 'CadAI'));
  const bundledVersion = freecad.addonVersion(bundledAddonDir());
  if (!installedVersion || (bundledVersion && freecad.versionGreater(bundledVersion, installedVersion))) await setupFreeCAD(false);
}

/** Make sure FreeCAD exists and has the current CadAI add-on. Returns {exe, userDir, result} or null. */
async function setupFreeCAD(interactive) {
  const exe = freecadExe();
  if (!exe) {
    const pick = await vscode.window.showWarningMessage(
      'CadAI: FreeCAD bulunamadı. FreeCAD 1.0 veya üstünü kurun ya da ayarlardan "cadai.freecadPath" yolunu girin.',
      'FreeCAD indir', 'Ayarı aç');
    if (pick === 'FreeCAD indir') vscode.env.openExternal(vscode.Uri.parse('https://www.freecad.org/downloads.php'));
    if (pick === 'Ayarı aç') vscode.commands.executeCommand('workbench.action.openSettings', 'cadai.freecadPath');
    return null;
  }
  const userDir = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Window, title: 'CadAI: FreeCAD yapılandırması okunuyor…' }, async () => freecadUserDir(exe));
  if (!userDir) {
    if (interactive) vscode.window.showErrorMessage(`CadAI: FreeCAD'in kullanıcı klasörü okunamadı (${exe}).`);
    return null;
  }
  let result;
  try {
    result = freecad.installAddon(bundledAddonDir(), userDir);
  } catch (e) {
    vscode.window.showErrorMessage(`CadAI: FreeCAD eklentisi kurulamadı: ${e.message}`);
    return null;
  }
  log(`FreeCAD: ${exe} | eklenti ${result.status} ${result.version} → ${result.target}`);
  if (result.status === 'installed' || result.status === 'updated') {
    const choice = await vscode.window.showInformationMessage(
      `CadAI: FreeCAD eklentisi ${result.status === 'installed' ? 'kuruldu' : 'güncellendi'} (${result.version}). `
      + 'Yapay zekâ ajanlarınızın (Claude Code, Codex, Cline…) FreeCAD araçlarını kullanabilmesi için bağlansın mı?',
      'Ajanlara bağla', 'Daha sonra');
    if (choice === 'Ajanlara bağla') await connectAgents();
  } else if (interactive) {
    vscode.window.showInformationMessage(`CadAI: FreeCAD eklentisi güncel (${result.version}${result.status === 'developer' ? ', geliştirici kurulumu' : ''}).`);
  }
  return { exe, userDir, result };
}

// ---------------- MCP server definition (VS Code native API) ----------------
// VS Code >= 1.101 lets an extension provide MCP servers directly: Copilot agent mode and every other chat
// extension that uses VS Code's MCP support then sees the cadai server without anyone editing mcp.json.

let nativeMcp = false;
const mcpChanged = new vscode.EventEmitter();

/** {command, args} that start the MCP server, or null. Prefers the copy installed into FreeCAD. */
/** CADAI_TOOLSET for small local models (Cline/Continue + Ollama): short tool list and descriptions. */
// One `cadai` server for every agent: it finds the running CAD program itself (FreeCAD or Fusion), follows the
// session chosen in VS Code (active-session.json) and switches when the program changes. No session is pinned here,
// so a restarted FreeCAD/Fusion never leaves agents bound to a dead session.
function mcpEnv() {
  return Object.assign({ CADAI_BACKEND: 'auto' },
    mcpSetup.toolsetEnv(vscode.workspace.getConfiguration('cadai').get('mcp.toolset')));
}

async function mcpServerCommand() {
  const exe = freecadExe();
  const python = freecad.pythonFor(exe);
  const userDir = exe ? await freecadUserDir(exe) : null;
  const candidates = [userDir && path.join(userDir, 'Mod', 'CadAI', 'mcp_server', 'cadai_mcp.py'),
    path.join(addonDir(), 'mcp_server', 'cadai_mcp.py'), path.join(bundledAddonDir(), 'mcp_server', 'cadai_mcp.py')].filter(Boolean);
  const server = candidates.find((f) => fs.existsSync(f));
  return python && server ? { command: python, args: [server] } : null;
}

function registerNativeMcp(context) {
  if (!vscode.lm || typeof vscode.lm.registerMcpServerDefinitionProvider !== 'function' || !vscode.McpStdioServerDefinition) return false;
  context.subscriptions.push(mcpChanged, vscode.lm.registerMcpServerDefinitionProvider('cadai.mcp', {
    onDidChangeMcpServerDefinitions: mcpChanged.event,
    provideMcpServerDefinitions: async () => {
      const cmd = await mcpServerCommand();
      if (!cmd) return [];
      return [new vscode.McpStdioServerDefinition(mcpSetup.NAME, cmd.command, cmd.args, mcpEnv(),
        freecad.addonVersion(addonDir()) || ctx.extension.packageJSON.version)];
    },
  }));
  return true;
}

async function connectAgents() {
  const cmd = await mcpServerCommand();
  if (!cmd) {
    vscode.window.showErrorMessage('CadAI: MCP sunucusu için Python bulunamadı. FreeCAD\'i kurun (kendi Python\'u kullanılır) ya da Python 3.9+ kurun.');
    return;
  }
  const python = cmd.command, server = cmd.args[0];
  const installed = new Set(vscode.extensions.all.map((e) => e.id.toLowerCase()));
  const claudeAgent = agentList.find((a) => a.id === 'claude-cli');
  const list = mcpSetup.targets({ userDir: path.resolve(ctx.globalStorageUri.fsPath, '..', '..'),
    claudeCli: claudeAgent && claudeAgent.cliPath, installed, command: python, args: [server], nativeVsCode: nativeMcp,
    env: mcpEnv() });
  const picks = await vscode.window.showQuickPick(list.map((t) => ({ label: t.label, description: t.describe, picked: true, t })),
    { canPickMany: true, title: 'CadAI araçları (cadai: açık FreeCAD ya da Fusion kendiliğinden bulunur) hangi ajanlara eklensin? Eski cadai-freecad / cadai-fusion kayıtları kaldırılır; diğer ayarlarınız korunur.' });
  if (!picks || !picks.length) return;
  const done = [], failed = [];
  for (const p of picks) {
    try { p.t.apply(); done.push(p.label); } catch (e) { failed.push(`${p.label}: ${e.message.split('\n')[0]}`); }
  }
  log(`ajanlara bağlandı: ${done.join(', ')}${failed.length ? ' | hatalar: ' + failed.join('; ') : ''}`);
  if (done.length) vscode.window.showInformationMessage(`CadAI araçları eklendi → ${done.join(', ')}. Ajan hangi CAD programı açıksa (FreeCAD/Fusion) ona kendiliğinden bağlanır. Açık ajan pencerelerini yeniden başlatmanız gerekebilir.`);
  if (failed.length) vscode.window.showErrorMessage('CadAI: Bazı ajanlara eklenemedi — ' + failed.join(' | '));
}

function addonSourceVersion() { return freecad.addonVersion(addonDir()); }

let reloadedAddonFor = null;
async function syncFreeCADAddon() {
  const source = addonSourceVersion();
  if (!source) return;
  const health = await bridge.health();
  if (versionGreater(source, health.version) && reloadedAddonFor !== source) {
    reloadedAddonFor = source;
    log(`FreeCAD eklentisi ${health.version} → ${source} güncelleniyor`);
    await bridge.reload();
    resetCADView();
    vscode.window.showInformationMessage(`CadAI: FreeCAD eklentisi ${source} sürümüne güncellendi (FreeCAD kapanmadan).`);
  }
}

// ---------------- connection & polling ----------------

async function poll() {
  if (polling) return;
  polling = true;
  try {
    // FreeCAD/Fusion closed or restarted: forget the dead session; the next request connects to whatever runs now.
    if (bridge.dropDeadSession()) { log('CAD oturumu kapandı; çalışan programa yeniden bağlanılıyor.'); resetCADView(); onConnectionChanged(); }
    const v = await bridge.version();
    if (!state.connected) {
      state.connected = true;
      onConnectionChanged();
      await bridge.bindDocument();
      log(`${bridge.label} oturumuna bağlanıldı (${bridge.info?.session_id || bridge.info?.url}).`);
      if (bridge.backend === 'fusion') checkFusionVersion().catch((e) => log('Fusion sürüm denetimi: ' + e.message));
      if (bridge.backend === 'freecad') {
        syncFreeCADAddon().catch((e) => log('eklenti sürüm kontrolü: ' + e.message));
        setTimeout(() => quietGpuCheck().catch((e) => log('GPU denetimi: ' + e.message)), 8000);
        syncHistory().catch((e) => log('tasarım geçmişi: ' + e.message));
        setTimeout(() => syncExternal(), 3000);
      }
    }
    if (v.doc !== state.doc) { state.doc = v.doc; await refreshDocument(); }
    if (v.sel !== state.sel) { state.sel = v.sel; await refreshSelection(); }
    if (v.mk !== state.mk) { state.mk = v.mk; await refreshMarkers(); }
    // a history commit landed (or failed): FreeCAD writes it on a worker thread after a short pause
    if (v.hist !== undefined && v.hist !== state.hist) { state.hist = v.hist; await refreshHistory(); }
  } catch (e) {
    // a busy FreeCAD (timeout) stays connected; reconnecting would re-run the whole sync and pile up requests
    if (state.connected && e.offline !== false) {
      state.connected = false;
      state.doc = state.sel = state.mk = state.hist = -1;
      onConnectionChanged();
    }
  } finally {
    polling = false;
  }
}

function onConnectionChanged() {
  if (state.connected) {
    statusItem.text = `$(circle-filled) CadAI ${ctx.extension.packageJSON.version}: ${bridge.label} bağlı`;
    statusItem.command = 'cadai.openViewer';
    statusItem.tooltip = '3B görünümü aç';
  } else {
    statusItem.text = `$(debug-disconnect) CadAI: ${bridge.label} bağlantısı yok`;
    statusItem.command = 'cadai.selectCADSession';
    statusItem.tooltip = 'CAD oturumunu seç';
  }
  post(controls, { type: 'connection', connected: state.connected, backend: bridge.backend, label: bridge.label,
    capabilities: bridge.capabilities });
  post(viewerPanel && viewerPanel.webview, { type: 'connection', connected: state.connected });
  vscode.commands.executeCommand('setContext', 'cadai.connected', state.connected);
  if (!state.connected) {
    vscode.commands.executeCommand('setContext', 'cadai.hasDocument', false);
    historyProvider.setData(null);
  }
  treeProvider.refresh();
}

let refreshTimer = null;
function refreshDocument() {
  // debounce: during a recompute FreeCAD fires many change events
  clearTimeout(refreshTimer);
  return new Promise((resolve) => {
    refreshTimer = setTimeout(async () => {
      try {
        await bridge.bindDocument();
        const tree = await bridge.ui('tree');
        for (const w of tree.warnings || []) log(`${w.label} · ${w.operation}: ${w.error}`);
        const docSwitched = tree.active !== state.docName;
        state.docName = tree.active;
        treeProvider.setData(tree);
        post(controls, { type: 'document', tree });
        if (controls && supports('design_requirements')) await refreshRequirements();
        vscode.commands.executeCommand('setContext', 'cadai.hasDocument', !!tree.active);
        if (viewerPanel) await sendScene(false);
        if (docSwitched) {
          await refreshMarkers(); // markers and design history belong to the document
          if (supports('history_status')) await refreshHistory();
        } else {
          if (supports('history_status')) refreshHistoryStatus();
        }
      } catch (e) {
        log('belge yenilenemedi: ' + e.message);
        post(controls, { type: 'documentError', text: e.message });
      }
      resolve();
    }, 150);
  });
}

async function refreshSelection() {
  try {
    const items = await bridge.ui('selection');
    post(controls, { type: 'selection', items });
    post(viewerPanel && viewerPanel.webview, { type: 'selection', items });
  } catch (e) { log('seçim alınamadı: ' + e.message); }
}

async function refreshMarkers() {
  try {
    state.markers = await bridge.ui('markers');
    post(controls, { type: 'markers', items: state.markers });
    post(viewerPanel && viewerPanel.webview, { type: 'markers', items: state.markers });
  } catch (e) { log('işaretler alınamadı: ' + e.message); }
}

function markerTarget(t) {
  const kind = { vertex: 'köşe', edge: 'kenar', face: 'yüz' }[t.snap] || '';
  return `${t.label || t.object} · ${t.element} (${kind}, nokta ${t.point.map((x) => +x.toFixed(2)).join(', ')})`;
}

async function markersToAI() {
  const items = await bridge.ui('markers');
  if (!items.length) throw new Error('Henüz işaret yok. 3B görünümde "📍 İşaretle" ile ekleyin.');
  const lines = items.map((m) => {
    const note = m.note || '(not yok)';
    if (m.kind === 'dimension') return `#${m.id} ölçü ${(+m.distance).toFixed(2)} mm (${markerTarget(m.a)} → ${markerTarget(m.b)}): ${note}`;
    if (m.kind === 'line') return `#${m.id} çizgi, ${m.points.length} nokta, ${(+m.length).toFixed(1)} mm (${markerTarget(m.points[0])} → ${markerTarget(m.points[m.points.length - 1])}): ${note}`;
    if (m.kind === 'circle') return `#${m.id} daire Ø${(2 * m.radius).toFixed(2)} mm, merkez ${markerTarget(m.a)}: ${note}`;
    if (m.kind === 'pen') return `#${m.id} kalem izi ${(+m.length).toFixed(1)} mm, yüzler ${(m.faces || []).map((f) => `${f.label || f.object}·${f.element}`).join(', ')}: ${note}`;
    return `#${m.id} ${markerTarget(m.a)}: ${note}`;
  });
  const text = `${bridge.label} modelinde 3B görünümde koyduğum işaretlere göre değişiklik yap. cadai MCP `
    + 'araçlarını kullan (açık program kendiliğinden bulunur; işaretli gövde mesh ise önce convert_mesh ile katıya çevir): önce get_markers ile işaretlerin ayrıntılarını oku, her işareti sırayla uygula, sonra '
    + 'measure ile doğrula ve hangi değişikliğin hangi işarete ait olduğunu söyle.\n\n' + lines.join('\n');
  const agent = currentAgent();
  const how = await agents.open(agent, text, projectDir());
  const msg = {
    sent: `${agent.label} işaretlerinle başlatıldı (terminal).`,
    typed: `İşaretler ${agent.label} mesaj kutusuna yazıldı — Enter'a basmanız yeterli.`,
    'typed+clipboard': `İşaretler ${agent.label} mesaj kutusuna yazıldı — Enter'a basın. (Kutuda görünmüyorsa Ctrl+V; metin panoda da var.)`,
    clipboard: agent.id === 'clipboard' ? 'İşaret listesi panoya kopyalandı; istediğiniz ajana Ctrl+V ile yapıştırın.'
      : `İşaretler panoya kopyalandı ve ${agent.label} açıldı; sohbet kutusuna Ctrl+V ile yapıştırıp gönderin.`,
  }[how];
  if (msg) vscode.window.showInformationMessage('CadAI: ' + msg);
}

// ---------------- AI agent choice ----------------

let agentList = [];

function currentAgent() {
  const saved = ctx.globalState.get('cadai.agent');
  return agentList.find((a) => a.id === saved && a.available)
    || agentList.find((a) => a.id === agents.defaultAgent(agentList));
}

function projectDir() {
  const dev = cfg('addonPath');
  if (dev) {
    const repo = path.resolve(dev, '..', '..');
    if (fs.existsSync(path.join(repo, 'AGENTS.md'))) return repo;
  }
  const ws = vscode.workspace.workspaceFolders;
  return ws && ws.length ? ws[0].uri.fsPath : undefined;
}

function sendAgents() {
  post(controls, { type: 'agents', items: agentList.map(({ id, label, detail, available }) => ({ id, label, detail, available })),
    selected: currentAgent().id });
}

async function setAgent(id) {
  await ctx.globalState.update('cadai.agent', id);
  sendAgents();
}

async function chooseAgent() {
  agentList = agents.detect();
  const pick = await vscode.window.showQuickPick(
    agentList.filter((a) => a.available).map((a) => ({ label: a.label, description: a.detail, id: a.id,
      picked: a.id === currentAgent().id })),
    { title: 'Yapay zekâ ajanı (hepsi aynı cadai araçlarını kullanır)' });
  if (pick) await setAgent(pick.id);
}

let lastSceneDoc = null;
let viewerKeys = [];   // object keys the viewer already holds: FreeCAD sends only what changed (delta scenes)
let viewerGl = null;   // WebGL renderer of the 3D view, reported by the viewer
let viewerGlError = null; // why the 3D view could not create a WebGL context, if it could not
let viewerGlLost = 0;  // driver resets seen this session
async function sendScene(forceFit) {
  if (!viewerPanel) return;
  const t0 = Date.now();
  const scene = await bridge.ui('scene', { quality: cfg('meshQuality') || 1, known: viewerKeys }, 120000);
  const fit = forceFit || scene.doc !== lastSceneDoc;
  lastSceneDoc = scene.doc;
  viewerKeys = scene.objects.map((o) => o.key).filter(Boolean);
  post(viewerPanel.webview, { type: 'scene', scene: { ...scene, context: bridge.context }, fit });
  post(controls, { type: 'sceneWarnings', count: (scene.warnings || []).length });
  for (const w of scene.warnings || []) log(`${w.label} · ${w.element}: ${w.error}`);
  if (scene.stats) log(`sahne ${Date.now() - t0} ms: ${scene.stats.tessellated} yeni, ${scene.stats.unchanged} değişmedi, ${scene.stats.cached} önbellekten`);
}

function post(target, msg) {
  const webview = target && target.webview ? target.webview : target;
  if (webview) webview.postMessage({ ...msg, context: bridge.context });
}

// ---------------- FreeCAD process ----------------

async function startFreeCAD() {
  if (state.connected) {
    vscode.window.showInformationMessage(`CadAI: ${bridge.label} bağlı. FreeCAD'e geçmek için CAD oturumunu seçin.`);
    return;
  }
  bridge.generation++;
  bridge.info = bridge.context = bridge.capabilities = null;
  const setup = await setupFreeCAD(false);
  if (!setup) return;
  const exe = setup.exe;
  const child = cp.spawn(exe, [], { detached: true, stdio: 'ignore' });
  child.unref();
  log('FreeCAD başlatıldı: ' + exe);
  await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'CadAI: FreeCAD açılıyor…' },
    async () => {
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try { await bridge.health(); break; } catch (_) { /* not yet */ }
      }
    });
  await poll();
  if (!state.connected) {
    vscode.window.showErrorMessage('CadAI: FreeCAD açıldı ama köprüye bağlanılamadı. CadAI eklentisi FreeCAD\'e kurulu mu?');
    return;
  }
  if (cfg('minimizeFreeCAD')) await guard(() => bridge.ui('minimize_freecad'));
  openViewer();
}

// ---------------- 3D viewer ----------------

let pendingViewerMode = null;
let viewerReady = false;
const pendingViewerMessages = [];

function openViewer(mode) {
  if (mode) {
    if (viewerPanel) post(viewerPanel.webview, { type: 'mode', mode });
    else pendingViewerMode = mode;
  }
  if (viewerPanel) { viewerPanel.reveal(); return; }
  viewerPanel = vscode.window.createWebviewPanel('cadai.viewer', 'CadAI 3B Görünüm', vscode.ViewColumn.One, {
    enableScripts: true, retainContextWhenHidden: true,
    localResourceRoots: [vscode.Uri.joinPath(ctx.extensionUri, 'media')],
  });
  viewerPanel.iconPath = vscode.Uri.joinPath(ctx.extensionUri, 'media', 'cadai-activity.svg');
  viewerPanel.webview.html = webviewHtml(viewerPanel.webview, 'viewer');
  viewerReady = false;
  viewerKeys = [];
  viewerPanel.onDidDispose(() => { viewerPanel = null; lastSceneDoc = null; viewerReady = false; viewerKeys = []; });
  viewerPanel.webview.onDidReceiveMessage((m) => guard(() => bridge.operation(async () => {
    if (m.target && ['addMarker', 'pick', 'command'].includes(m.type)
      && !['cadai.selectCADSession', 'cadai.connectFusion', 'cadai.setupFusion', 'cadai.openViewer'].includes(m.command)) assertCurrentView(m.target);
    if (m.type === 'ready') {
      viewerKeys = [];
      if (m.gl) {
        viewerGl = m.gl;
        viewerGlError = null;
        log(`3B görünüm GPU'su: ${m.gl.renderer}${m.gl.attempt > 0 ? ` (yedek ayar ${m.gl.attempt}, kenar yumuşatma ${m.gl.antialias ? 'açık' : 'kapalı'})` : ''}`
          + `${m.gl.software ? ', yazılımla çizim' : ''}, piksel oranı ${m.gl.pixelRatio}`);
      } else if (m.glError) {
        viewerGl = null;
        viewerGlError = m.glError;
        log('3B görünüm WebGL başlatamadı: ' + m.glError);
        vscode.window.showErrorMessage('CadAI: 3B görünüm açılamadı (WebGL 2 yok).', 'GPU tanılaması')
          .then((pick) => { if (pick) vscode.commands.executeCommand('cadai.gpuDiagnostics'); });
        return;
      }
      post(viewerPanel.webview, { type: 'connection', connected: state.connected });
      if (state.connected) { await sendScene(true); await refreshSelection(); await refreshMarkers(); }
      if (pendingViewerMode) { post(viewerPanel.webview, { type: 'mode', mode: pendingViewerMode }); pendingViewerMode = null; }
      viewerReady = true;
      for (const msg of pendingViewerMessages.splice(0)) post(viewerPanel.webview, msg);
    } else if (m.type === 'addMarker') {
      const added = await bridge.ui('add_marker', { marker: m.marker }, 60000, m.target);
      log(`işaret #${added.id} eklendi: ${added.note}`);
    } else if (m.type === 'pick') {
      await bridge.ui('set_selection', { object: m.object || null, sub: m.sub || '', additive: !!m.additive }, 60000, m.target);
    } else if (m.type === 'command') {
      await runWebviewCommand(m.command);
    } else if (m.type === 'femField') {
      await showFemResult(m.quantity === 'displacement' ? 'displacement' : 'von_mises');
    } else if (m.type === 'needFullScene') {
      viewerKeys = [];
      await sendScene(false);
    } else if (m.type === 'glRestored' && m.gl) {
      viewerGl = m.gl;
      viewerGlError = null;
      log('3B görünüm WebGL bağlamı geri geldi: ' + m.gl.renderer);
    } else if (m.type === 'glLost') {
      viewerGlLost++;
      log(`3B görünüm: ekran kartı sürücüsü WebGL bağlamını sıfırladı (${viewerGlLost}. kez)`);
    } else if (m.type === 'reloadViewer') {
      const panel = viewerPanel;
      panel.webview.html = '';
      panel.webview.html = webviewHtml(panel.webview, 'viewer'); // a fresh page gets a fresh WebGL context
      viewerReady = false;
    }
  })));
}

// ---------------- sidebar controls ----------------

class ControlsProvider {
  resolveWebviewView(view) {
    controls = view;
    view.webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(ctx.extensionUri, 'media')] };
    view.webview.html = webviewHtml(view.webview, 'controls');
    view.onDidDispose(() => { controls = null; });
    view.webview.onDidReceiveMessage((m) => guard(() => bridge.operation(() => handleControl(m))));
  }
}

async function handleControl(m) {
  if (m.target && !['ready', 'setAgent', 'viewerMode'].includes(m.cmd)
    && !['cadai.selectCADSession', 'cadai.connectFusion', 'cadai.setupFusion', 'cadai.openViewer'].includes(m.command)) assertCurrentView(m.target);
  switch (m.cmd) {
    case 'setAgent':
      return setAgent(m.id);
    case 'ready':
      sendAgents();
      post(controls, { type: 'connection', connected: state.connected, backend: bridge.backend, label: bridge.label,
        capabilities: bridge.capabilities });
      if (state.connected) {
        post(controls, { type: 'document', tree: await bridge.ui('tree') });
        await refreshSelection();
        await refreshMarkers();
        if (supports('history_status')) await refreshHistoryStatus();
        if (supports('design_requirements')) await refreshRequirements();
        if (externalStatus) post(controls, { type: 'external', status: externalStatus });
      }
      return;
    case 'viewerMode':
      openViewer(m.mode);
      return;
    case 'editMarker': {
      const mk = state.markers.find((x) => x.id === m.id);
      const note = await vscode.window.showInputBox({ prompt: `İşaret #${m.id}: ne değişsin?`, value: mk ? mk.note : '' });
      if (note !== undefined) await bridge.ui('update_marker', { id: m.id, note });
      return;
    }
    case 'deleteMarker':
      await bridge.ui('delete_marker', { id: m.id });
      return;
    case 'clearMarkers': {
      const ok = await vscode.window.showWarningMessage('Tüm işaretler silinsin mi?', { modal: true }, 'Sil');
      if (ok === 'Sil') await bridge.ui('clear_markers');
      return;
    }
    case 'markersToAI':
      return markersToAI();
    case 'command':
      return runWebviewCommand(m.command);
    case 'addPrimitive':
      await bridge.ui('add_primitive', { kind: m.kind, params: m.params, position: m.position });
      return;
    case 'clearSelection':
      await bridge.ui('clear_selection');
      return;
    case 'requirementsCheck':
      return refreshRequirements();
    case 'requirementsSave':
    case 'requirementsRemove':
      try {
        const result = await bridge.ui('update_design_requirements', { document: m.document,
          requirements: m.cmd === 'requirementsSave' ? [m.requirement] : [],
          remove_ids: m.cmd === 'requirementsRemove' ? [m.id] : [] });
        post(controls, { type: 'requirements', result, saved: true });
      } catch (e) { post(controls, { type: 'requirementsError', text: e.message }); throw e; }
      return;
    case 'requirementsSelect':
      if (m.document !== state.docName) throw new Error('Belge değişti; listeyi yenileyin.');
      await bridge.ui('set_selection', { object: m.object });
      return;
    case 'requirementsExport': {
      const document = state.docName;
      const file = await pickFile(true, { 'Tasarım denetimi': ['json'] }, 'Tasarım raporunu kaydet');
      if (file) {
        const result = await bridge.ui('export_requirements', { document, path: file });
        vscode.window.showInformationMessage(`CadAI: Rapor kaydedildi (${result.design_validation.status}): ${result.path}`);
      }
      return;
    }
    case 'parameterRelation':
      return parameterRelation();
    case 'historyToggle':
      try { return await toggleHistory(m.on); } finally { refreshHistoryStatus(); } // re-enables the button on failure too
    case 'showFemResult':
      return showFemResult(m.quantity === 'displacement' ? 'displacement' : 'von_mises');
    case 'dfm':
      try { return await runDfm(m.process); } catch (e) { post(controls, { type: 'dfmStatus', text: 'Hata: ' + e.message }); throw e; }
    case 'partsSearch':
      try { return await searchParts(m.query); } catch (e) { post(controls, { type: 'partsStatus', text: 'Hata: ' + e.message }); throw e; }
    case 'partsInsert':
      try { return await insertPart(m.id); } catch (e) { post(controls, { type: 'partsStatus', text: 'Hata: ' + e.message }); throw e; }
    case 'femRun':
      try {
        return await runFem(m.params);
      } catch (e) {
        post(controls, { type: 'femStatus', text: 'Hata: ' + e.message, error: true });
        throw e;
      }
    default:
      log('bilinmeyen kontrol mesajı ' + JSON.stringify(m));
  }
}

let requirementsRequest = 0;
async function refreshRequirements() {
  const request = ++requirementsRequest;
  try {
    const result = await bridge.ui('design_requirements');
    if (request === requirementsRequest) post(controls, { type: 'requirements', result });
  } catch (e) {
    if (request === requirementsRequest) post(controls, { type: 'requirementsError', text: e.message });
  }
}

async function parameterRelation() {
  const summary = await bridge.toolJson('get_document_summary', {});
  const entries = summary.objects.flatMap((o) => Object.keys(o.dimensions || {}).map((p) =>
    ({ label: `${o.label} · ${p}`, description: o.name, object: o.name, property: p })));
  const target = await vscode.window.showQuickPick(entries, { title: 'Bağlanacak ölçü' });
  if (!target) return;
  const source = await vscode.window.showQuickPick(entries.filter((p) => p.object !== target.object || p.property !== target.property),
    { title: 'Değişimini takip edeceği ölçü' });
  if (!source) return;
  const numeric = (s) => s.trim() && Number.isFinite(Number(s.replace(',', '.'))) ? undefined : 'Sonlu bir sayı yazın.';
  const factor = await vscode.window.showInputBox({ prompt: 'Çarpan: hedef = çarpan × kaynak + fark', value: '1', validateInput: numeric });
  if (factor === undefined) return;
  const offset = await vscode.window.showInputBox({ prompt: 'Fark (ölçünün biriminde)', value: '0', validateInput: numeric });
  if (offset === undefined) return;
  const current = await bridge.ui('tree');
  if (current.active !== summary.document) throw new Error('Belge değişti; bağıntıyı yeniden seçin.');
  await bridge.toolJson('set_parameter_relation', { object: target.object, property: target.property,
    reference_object: source.object, reference_property: source.property,
    factor: Number(factor.replace(',', '.')), offset: Number(offset.replace(',', '.')) });
  await refreshDocument();
}

async function showFemResult(quantity = 'von_mises') {
  const field = await bridge.ui('fem_field', { quantity }, 120000);
  sendToViewer({ type: 'femField', field });
  log(`FEM alanı ${field.analysis}: ${field.quantity} ${field.min}…${field.max} ${field.unit}`);
}

/** Open the 3D view if needed and deliver a message once its script is ready. */
function sendToViewer(msg) {
  openViewer();
  if (viewerReady) post(viewerPanel.webview, msg);
  else pendingViewerMessages.push(msg);
}

// ---------------- GPU diagnostics ----------------
// CAD modeling runs on the CPU; the GPU draws. Check that FreeCAD (OpenGL) and the 3D view (WebGL) use the best GPU,
// for any vendor. Typical finding on desktops: monitor cable in the motherboard port, graphics card idle.

async function collectGpu() {
  const sys = await gpu.listAdapters();
  let fc = null;
  if (state.connected) {
    try { fc = await bridge.ui('gpu_info', {}, 20000); } catch (e) { log('FreeCAD GPU bilgisi alınamadı: ' + e.message); }
  }
  const findings = gpu.diagnose({ adapters: sys.adapters, laptop: sys.laptop, settings: fc && fc.settings,
    freecadGl: fc && fc.opengl && fc.opengl.renderer, freecadGlVersion: fc && fc.opengl && fc.opengl.version,
    viewerGl: viewerGl && viewerGl.renderer, viewerError: viewerGlError, viewerLost: viewerGlLost });
  return { sys, fc, findings };
}

const SEV_ICON = { error: '✖', warning: '⚠', info: 'ℹ' };

/** Windows "Graphics settings" per-app GPU preference (what Settings → Display → Graphics writes). */
function setHighPerformanceGpu(exes) {
  for (const exe of exes.filter(Boolean)) {
    cp.execFileSync('reg.exe', ['add', 'HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences', '/v', exe, '/t', 'REG_SZ',
      '/d', 'GpuPreference=2;', '/f'], { windowsHide: true, timeout: 10000 });
  }
}

async function gpuDiagnostics() {
  const { sys, fc, findings } = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification,
    title: 'CadAI: GPU ve performans denetleniyor…' }, collectGpu);
  const lines = [
    `Ekran kartları: ${sys.adapters.map((a) => `${a.name}${a.active ? ' (monitör bağlı)' : ''}`).join(', ') || 'okunamadı'}`,
    `FreeCAD OpenGL: ${fc && fc.opengl ? fc.opengl.renderer : (state.connected ? 'okunamadı' : 'FreeCAD kapalı')}`,
    `3B görünüm WebGL: ${viewerGl ? gpu.cleanRenderer(viewerGl.renderer) + (viewerGl.pixelRatio ? ` (piksel oranı ${viewerGl.pixelRatio})` : '')
      : viewerGlError ? 'başlatılamadı' : '3B görünümü bir kez açın'}`, '',
    ...findings.map((f) => `${SEV_ICON[f.severity]} ${f.title}\n${f.detail || ''}${f.fix && f.fix !== 'apply' ? '\n→ ' + f.fix : ''}`),
  ];
  log('GPU tanılaması:\n' + lines.join('\n'));
  const buttons = [];
  if (findings.some((f) => f.fix === 'apply') && state.connected) buttons.push('FreeCAD ayarlarını uygula');
  const hybrid = findings.find((f) => f.id === 'hybrid');
  if (hybrid && process.platform === 'win32') buttons.push('Yüksek performans GPU\'su ata');
  const worst = findings.some((f) => f.severity === 'error') ? 'showErrorMessage' : findings.some((f) => f.severity === 'warning') ? 'showWarningMessage' : 'showInformationMessage';
  const pick = await vscode.window[worst]('CadAI: GPU ve performans', { modal: true, detail: lines.join('\n') }, ...buttons);
  if (pick === 'FreeCAD ayarlarını uygula') {
    const r = await bridge.ui('gpu_apply_recommended', { vbo: findings.some((f) => f.id === 'fc-vbo') });
    vscode.window.showInformationMessage(`CadAI: FreeCAD ayarları güncellendi (${r.changed.join(', ') || 'zaten uygun'}). ${r.note}`);
  } else if (pick && pick.startsWith('Yüksek performans')) {
    setHighPerformanceGpu([hybrid.apps.includes('FreeCAD') ? freecadExe() : null, hybrid.apps.includes('VS Code') ? process.execPath : null]);
    vscode.window.showInformationMessage('CadAI: Windows grafik tercihi "Yüksek performans" yapıldı. FreeCAD ve VS Code yeniden başlatılınca etkinleşir.');
  }
}

let gpuChecked = false;
/** Once per session after FreeCAD connects: warn (once per finding) when the strong GPU is not used. */
async function quietGpuCheck() {
  if (gpuChecked || !cfg('gpuCheck')) return;
  gpuChecked = true;
  const { findings } = await collectGpu();
  const serious = findings.filter((f) => f.severity !== 'info' && /^(cable|hybrid|software|nodriver|webgl)/.test(f.id));
  const seen = ctx.globalState.get('cadai.gpuWarned', []);
  const fresh = serious.filter((f) => !seen.includes(f.id + '|' + f.title));
  if (!fresh.length) return;
  await ctx.globalState.update('cadai.gpuWarned', seen.concat(fresh.map((f) => f.id + '|' + f.title)));
  const pick = await vscode.window.showWarningMessage(`CadAI: ${fresh[0].title}`, 'Ayrıntılar ve çözüm', 'Bir daha gösterme');
  if (pick === 'Ayrıntılar ve çözüm') await gpuDiagnostics();
  if (pick === 'Bir daha gösterme') await vscode.workspace.getConfiguration('cadai').update('gpuCheck', false, vscode.ConfigurationTarget.Global);
}

// ---------------- design history (optional) ----------------
// Every model change becomes a git commit plus Markdown notes (an Obsidian vault) in a separate history folder.
// The arbitrary folder path is a machine setting; a workspace can only opt into "<workspace>/cadai-history".

function historyFolder() {
  if (cfg('history.useWorkspace')) {
    const ws = vscode.workspace.workspaceFolders;
    return ws && ws.length ? path.join(ws[0].uri.fsPath, 'cadai-history') : '';
  }
  return cfg('history.folder') || '';
}

/** Show a history status everywhere: sidebar button, tree title buttons (context key), tree header. */
function applyHistoryStatus(st) {
  post(controls, { type: 'history', status: st });
  vscode.commands.executeCommand('setContext', 'cadai.historyEnabled', !!st.enabled);
  historyProvider.status = st;
  if (historyView) historyView.description = st.enabled ? '● kayıt açık' : 'kapalı';
}

async function syncHistory() {
  if (!state.connected || !supports('history_configure')) return null;
  const st = await bridge.ui('history_configure', { enabled: !!cfg('history.enabled'), folder: historyFolder(),
    notes: cfg('history.notes') !== false });
  applyHistoryStatus(st);
  return st;
}

async function refreshHistoryStatus() {
  if (!state.connected || !supports('history_status')) return;
  try { applyHistoryStatus(await bridge.ui('history_status')); } catch (_) { /* older add-on */ }
}

const HISTORY_LIMIT = 200;
let historyBusy = null, historyAgain = false;
/** Status + commit list of the active document into the tree. Calls that arrive while one runs are merged. */
async function refreshHistory() {
  if (historyBusy) { historyAgain = true; return historyBusy; }
  historyBusy = (async () => {
    do {
      historyAgain = false;
      if (!state.connected) { historyProvider.setData(null); break; }
      try {
        applyHistoryStatus(await bridge.ui('history_status'));
        historyProvider.setData(await bridge.ui('history_log', { limit: HISTORY_LIMIT }));
      } catch (e) { log('tasarım geçmişi okunamadı: ' + e.message); }
    } while (historyAgain);
  })();
  try { await historyBusy; } finally { historyBusy = null; }
}

async function toggleHistory(on) {
  const value = on === undefined ? !cfg('history.enabled') : !!on;
  await vscode.workspace.getConfiguration('cadai').update('history.enabled', value, vscode.ConfigurationTarget.Global);
  const st = await syncHistory();
  if (!st) return;
  await refreshHistory();
  if (value && !st.git) {
    const pick = await vscode.window.showWarningMessage('CadAI: Tasarım geçmişi için git gerekli ama bulunamadı.', 'Git indir');
    if (pick) vscode.env.openExternal(vscode.Uri.parse('https://git-scm.com/downloads'));
  } else {
    vscode.window.showInformationMessage(value
      ? `CadAI: Otomatik tasarım geçmişi açık. Her değişiklik bir commit ve bir not olur → ${st.repo || 'belge kaydedilince yanına'}`
      : 'CadAI: Otomatik tasarım geçmişi kapatıldı (var olan geçmiş silinmedi).');
  }
}

async function openVault(repo) {
  if (!repo) repo = (await bridge.ui('history_status')).repo;
  if (!repo || !fs.existsSync(repo)) {
    const enabled = historyProvider.status && historyProvider.status.enabled;
    const pick = await vscode.window.showInformationMessage(enabled ? 'CadAI: Bu belgenin henüz geçmişi yok; ilk değişiklikte oluşur.'
      : 'CadAI: Bu belgenin henüz geçmişi yok. Otomatik tasarım geçmişi kapalı.', ...(enabled ? [] : ['Geçmişi aç']));
    if (pick) await toggleHistory(true);
    return;
  }
  const pick = await vscode.window.showInformationMessage(`Tasarım geçmişi: ${repo}`, "Obsidian'da aç", 'Klasörü göster', "VS Code'da aç");
  if (pick === "Obsidian'da aç") {
    await vscode.env.openExternal(vscode.Uri.parse('obsidian://open?path=' + encodeURIComponent(repo)));
  } else if (pick === 'Klasörü göster') {
    await vscode.commands.executeCommand('revealFileInOS', vscode.Uri.file(path.join(repo, 'README.md')));
  } else if (pick === "VS Code'da aç") {
    await vscode.commands.executeCommand('vscode.openFolder', vscode.Uri.file(repo), { forceNewWindow: true });
  }
}

async function showHistory() {
  await vscode.commands.executeCommand('cadai.historyTree.focus');
  await refreshHistory();
}

async function openHistoryVersion(node) {
  const e = node && node.entry;
  if (!e) return;
  const r = await vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: `CadAI: ${e.commit} açılıyor…` },
    () => bridge.ui('history_open_version', { commit: e.commit }, 120000));
  vscode.window.showInformationMessage(`CadAI: "${e.title}" sürümü yeni belge olarak açıldı (${r.document}). Asıl belge değişmedi.`);
}

async function openHistoryNote(node) {
  const e = node && node.entry;
  const file = e && historyTree.notePath(historyProvider.data && historyProvider.data.repo, e.note);
  if (!file || !fs.existsSync(file)) throw new Error('Bu kaydın notu yok (notlar kapalıyken kaydedilmiş olabilir).');
  await vscode.window.showTextDocument(vscode.Uri.file(file), { preview: true });
}

async function selectHistoryObject(name) {
  try {
    await bridge.ui('set_selection', { object: name });
  } catch (_) {
    vscode.window.showInformationMessage(`CadAI: ${name} artık modelde yok.`);
  }
}

// ---------------- design history tree ----------------
// Day → commit (newest first, the latest marked) → what changed. Empty states are viewsWelcome buttons in package.json.

class HistoryProvider {
  constructor() { this.data = null; this.status = null; this._em = new vscode.EventEmitter(); this.onDidChangeTreeData = this._em.event; }
  setData(d) { this.data = d; this._em.fire(); }
  getTreeItem(node) {
    if (node.kind === 'info') {
      const it = new vscode.TreeItem(node.label);
      it.iconPath = new vscode.ThemeIcon(node.icon);
      if (node.command) it.command = node.command;
      return it;
    }
    if (node.kind === 'day') {
      const it = new vscode.TreeItem(node.day.label, node.first ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.Collapsed);
      it.id = 'day:' + node.day.key;
      it.description = `${node.day.entries.length} kayıt`;
      it.iconPath = new vscode.ThemeIcon('calendar');
      return it;
    }
    if (node.kind === 'commit') {
      const e = node.entry;
      const it = new vscode.TreeItem(e.title, (e.changes || []).length ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
      it.id = 'commit:' + e.commit;
      it.description = [node.latest ? 'son' : '', historyTree.clock(e.date), e.source].filter(Boolean).join(' · ');
      it.tooltip = historyTree.tooltip(e); // plain text: titles carry object labels from the model file
      it.iconPath = node.latest ? new vscode.ThemeIcon('circle-filled', new vscode.ThemeColor('charts.green')) : new vscode.ThemeIcon('git-commit');
      it.contextValue = e.note ? 'cadaiCommitNote' : 'cadaiCommit';
      return it;
    }
    const c = node.change;
    const kind = historyTree.changeKind(c.text);
    const it = new vscode.TreeItem(c.object);
    it.description = c.text;
    it.tooltip = `${c.object}: ${c.text}`;
    it.iconPath = new vscode.ThemeIcon({ added: 'diff-added', removed: 'diff-removed', modified: 'diff-modified' }[kind],
      new vscode.ThemeColor({ added: 'charts.green', removed: 'charts.red', modified: 'charts.yellow' }[kind]));
    if (kind !== 'removed') it.command = { command: 'cadai.history.selectObject', title: 'Modelde seç', arguments: [c.object] };
    return it;
  }
  getChildren(node) {
    if (!node) {
      const h = this.data;
      if (!state.connected || !h || !h.entries || !h.entries.length) return []; // → viewsWelcome
      const out = [];
      if (!h.enabled) {
        out.push({ kind: 'info', label: 'Kayıt durduruldu — sürdürmek için tıklayın', icon: 'debug-pause',
          command: { command: 'cadai.history.enable', title: 'Geçmişi aç' } });
      }
      const days = historyTree.groupByDay(h.entries);
      days.forEach((day, i) => out.push({ kind: 'day', day, first: i === 0 }));
      if (h.entries.length >= HISTORY_LIMIT) {
        out.push({ kind: 'info', label: `Son ${HISTORY_LIMIT} kayıt gösteriliyor — tamamı Obsidian'da`, icon: 'book',
          command: { command: 'cadai.history.openVault', title: 'Obsidian' } });
      }
      return out;
    }
    if (node.kind === 'day') {
      const newest = this.data.entries[0];
      return node.day.entries.map((entry) => ({ kind: 'commit', entry, latest: entry === newest }));
    }
    if (node.kind === 'commit') return (node.entry.changes || []).map((change) => ({ kind: 'change', change }));
    return [];
  }
}

// ---------------- DFM & standard parts (sidebar) ----------------

async function selectedOrVisibleObject(purpose = 'DFM') {
  const sel = await bridge.ui('selection');
  if (sel.length) return sel[0].object;
  const tree = await bridge.ui('tree');
  const part = tree.objects.find((o) => o.visible && /^(Part::|PartDesign::Body)/.test(o.type));
  if (!part) throw new Error(`${purpose} için önce bir parça seçin.`);
  return part.name;
}

async function runDfm(process) {
  const object = await selectedOrVisibleObject();
  post(controls, { type: 'dfmStatus', text: `${object}: denetleniyor…` });
  const res = await bridge.toolJson('dfm_check', { object, process }, 300000);
  post(controls, { type: 'dfmResult', result: res });
  const items = res.findings.filter((f) => f.elements && f.elements.length)
    .map((f) => ({ object: res.object, severity: f.severity, elements: f.elements }));
  sendToViewer({ type: 'highlight', items, title: `${res.process_label}: ${res.errors} hata, ${res.warnings} uyarı` });
  log(`DFM ${res.object} ${res.process}: ${res.verdict} (${res.errors} hata, ${res.warnings} uyarı)`);
}

async function searchParts(query) {
  if (!query || !query.trim()) return;
  post(controls, { type: 'partsStatus', text: 'step.parts aranıyor…' });
  const res = await bridge.toolJson('search_parts', { query: query.trim(), limit: 12 }, 60000);
  post(controls, { type: 'partsResult', result: res });
}

async function insertPart(id) {
  let position;
  try {
    const sel = await bridge.toolJson('get_selection');
    const el = sel.selection && sel.selection[0] && (sel.selection[0].elements || [])[0];
    if (el && el.center) position = el.center; // drop it on the selected face
  } catch (_) { /* no selection */ }
  post(controls, { type: 'partsStatus', text: 'İndiriliyor ve doğrulanıyor (SHA-256)…' });
  const res = await bridge.toolJson('insert_part', position ? { part_id: id, position } : { part_id: id }, 120000);
  post(controls, { type: 'partsStatus', text: `Eklendi: ${res.label} → ${res.object}${position ? ' (seçili yüze)' : ''}` });
}

async function runFem(p) {
  const all = [...(p.fixed || []), ...(p.loaded || [])];
  if (!(p.fixed || []).length) throw new Error('En az bir sabit yüz seçin ("Seçimi ekle").');
  const objects = [...new Set(all.map((f) => f.object))];
  if (objects.length !== 1) throw new Error('Tüm yüzler aynı parçaya ait olmalı.');
  const object = objects[0];
  const args = {
    object, material: p.material, fixed_faces: p.fixed.map((f) => f.sub),
    analysis_type: p.analysisType, mesh_size_mm: p.meshSize ? Number(p.meshSize) : undefined,
  };
  if (p.analysisType === 'static') {
    if (!(p.loaded || []).length) throw new Error('Kuvvet uygulanacak yüzü seçin.');
    args.forces = [{ faces: p.loaded.map((f) => f.sub), force_n: Number(p.force), direction: p.direction }];
  } else {
    args.modes = 6;
  }
  post(controls, { type: 'femStatus', text: 'Analiz kuruluyor…' });
  const setup = await bridge.toolJson('fem_setup', args);
  post(controls, { type: 'femStatus', text: 'Mesh ve çözüm (CalculiX)…' });
  const job = await bridge.toolJson('fem_run', { analysis: setup.analysis, background: true });
  for (;;) {
    await new Promise((r) => setTimeout(r, 1000));
    const st = await bridge.toolJson('fem_status', { job: job.job });
    if (st.status === 'finished') {
      if (st.ok === false) throw new Error(st.error);
      post(controls, { type: 'femResult', result: st });
      log('FEM sonucu ' + JSON.stringify(st));
      if (!st.frequencies_hz) await showFemResult('von_mises').catch((e) => log('FEM alanı gösterilemedi: ' + e.message));
      return;
    }
    post(controls, { type: 'femStatus', text: `Çözülüyor… ${st.elapsed_s} sn` });
  }
}

// ---------------- model tree ----------------

class TreeProvider {
  constructor() { this.data = null; this._em = new vscode.EventEmitter(); this.onDidChangeTreeData = this._em.event; }
  setData(d) { this.data = d; this._em.fire(); }
  refresh() { if (!state.connected) this.data = null; this._em.fire(); }
  getTreeItem(node) {
    if (node.kind === 'info') {
      const it = new vscode.TreeItem(node.label);
      it.iconPath = new vscode.ThemeIcon(node.icon || 'info');
      if (node.command) it.command = node.command;
      return it;
    }
    const o = node.obj;
    const hasKids = o.children && o.children.length;
    const it = new vscode.TreeItem(o.label, hasKids ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None);
    const dims = Object.entries(o.dims || {}).map(([k, v]) => `${k}=${v}`).join(', ');
    it.description = o.type.split('::').pop() + (dims ? ' · ' + dims : '');
    it.tooltip = `${o.name} (${o.type})\n${dims}`;
    it.iconPath = new vscode.ThemeIcon(o.error ? 'error' : (o.visible ? 'symbol-class' : 'eye-closed'));
    // Fusion's parameter list is not a body: its dimensions are editable but it cannot be hidden or deleted.
    it.contextValue = o.type === 'Fusion::Parameters' ? (Object.keys(o.dims || {}).length ? 'cadaiParamsDims' : 'cadaiParams')
      : Object.keys(o.dims || {}).length ? 'cadaiObjectDims' : 'cadaiObject';
    it.command = { command: 'cadai.tree.select', title: 'Seç', arguments: [node] };
    return it;
  }
  getChildren(node) {
    if (!node) {
      if (!state.connected) {
        return [{ kind: 'info', label: 'FreeCAD kapalı — başlatmak için tıklayın', icon: 'play',
          command: { command: 'cadai.startFreeCAD', title: 'Başlat' } }];
      }
      if (!this.data || !this.data.active) {
        return [{ kind: 'info', label: 'Açık belge yok — yeni belge oluştur', icon: 'new-file',
          command: { command: 'cadai.newDocument', title: 'Yeni' } }];
      }
      return this.data.objects.map((obj) => ({ kind: 'obj', obj }));
    }
    return (node.obj && node.obj.children || []).map((obj) => ({ kind: 'obj', obj }));
  }
}

// ---------------- commands ----------------

async function pickFile(save, filters, title) {
  const opts = { filters, title };
  const uri = save ? await vscode.window.showSaveDialog(opts) : await vscode.window.showOpenDialog(Object.assign(opts, { canSelectMany: false }));
  if (!uri) return null;
  return save ? uri.fsPath : uri[0].fsPath;
}

async function shapeObjectsForExport() {
  const sel = await bridge.ui('selection');
  const selected = [...new Set(sel.map((s) => s.object))];
  if (selected.length) return selected;
  const tree = await bridge.ui('tree');
  return tree.objects.filter((o) => o.visible && /^(Part::|PartDesign::Body|Fusion::BRepBody|Fusion::MeshBody)/.test(o.type)).map((o) => o.name);
}

async function exportAs(ext, label) {
  if (bridge.backend === 'fusion' && ext === 'stl') throw new Error('İlk Fusion adaptörü STEP ve F3D dışa aktarır.');
  const objects = await shapeObjectsForExport();
  if (!objects.length) throw new Error('Dışa aktarılacak parça yok.');
  const file = await pickFile(true, { [label]: [ext] }, `${label} olarak dışa aktar`);
  if (!file) return;
  const out = await bridge.toolJson('export_model', bridge.backend === 'fusion' ? { path: file } : { objects, path: file });
  vscode.window.showInformationMessage(`CadAI: ${bridge.backend === 'fusion' ? 'Kök bileşenin tamamı' : objects.join(', ')} → ${out.path || out.file}`);
}

// A3 technical drawing (PDF + PNG) of the selected part, or of the first visible one
async function technicalDrawing() {
  const object = await selectedOrVisibleObject('Teknik resim');
  const title = await vscode.window.showInputBox({ title: `Teknik resim: ${object}`, prompt: 'Başlık (antette BAŞLIK)', value: object });
  if (title === undefined) return;
  const material = await vscode.window.showInputBox({
    title: `Teknik resim: ${object}`, value: '',
    prompt: "Malzeme, ör. AL 6061, S235, PLA (ağırlık bundan hesaplanır). Boş: FreeCAD'deki malzemesi",
  });
  if (material === undefined) return;
  const args = { object, title: title.trim() || object };
  if (material.trim()) args.material = material.trim();
  const out = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `CadAI: ${object} teknik resmi çiziliyor…` },
    () => bridge.toolJson('technical_drawing', args, 300000));
  for (const w of out.warnings || []) log('teknik resim uyarısı: ' + w);
  await vscode.commands.executeCommand('vscode.open', vscode.Uri.file(out.png), { preview: false });
  const mass = out.mass_g == null ? 'ağırlık yok (malzeme bilinmiyor)' : `~${Math.round(out.mass_g)} g`;
  const warn = out.warnings && out.warnings.length ? ` — ${out.warnings.length} uyarı (Çıktı panelinde)` : '';
  const pick = await vscode.window.showInformationMessage(
    `CadAI: teknik resim hazır — ölçek ${out.scale}, ${mass}${warn}`, "PDF'i aç", 'Klasörde göster');
  if (pick === "PDF'i aç") await vscode.env.openExternal(vscode.Uri.file(out.pdf));
  else if (pick === 'Klasörde göster') await vscode.commands.executeCommand('revealFileInOS', vscode.Uri.file(out.pdf));
}

// ---------------- other open-source programs: Blender (render), OpenSCAD, KiCad, build123d / CadQuery ----------------
// They run in the background as separate processes started by the FreeCAD add-on (cadai/external.py); results
// land in the FreeCAD document (3D view) or as images opened in VS Code. Paths come only from machine settings.

const EXTERNAL_SETTINGS = { blender: 'external.blenderPath', openscad: 'external.openscadPath',
  kicad_cli: 'external.kicadCliPath', codecad_python: 'external.codeCadPython' };
// Install commands are fixed here (never taken from the bridge) and run only after the user confirms them.
// build123d goes into CadAI's own virtual environment (found automatically), never into the system Python.
const WINGET = 'winget install -e --accept-package-agreements --accept-source-agreements --id ';
const POSIX_CODECAD = 'python3 -m venv ~/.cadai/codecad && ~/.cadai/codecad/bin/python -m pip install build123d';
const EXTERNAL_INSTALL = {
  win32: { blender: WINGET + 'BlenderFoundation.Blender', openscad: WINGET + 'OpenSCAD.OpenSCAD',
    kicad_cli: WINGET + 'KiCad.KiCad',
    codecad_python: 'python -m venv "%LOCALAPPDATA%\\CadAI\\codecad" && "%LOCALAPPDATA%\\CadAI\\codecad\\Scripts\\python.exe" -m pip install build123d' },
  darwin: { blender: 'brew install --cask blender', openscad: 'brew install --cask openscad', kicad_cli: 'brew install --cask kicad',
    codecad_python: POSIX_CODECAD },
  linux: { blender: 'sudo snap install blender --classic', openscad: 'sudo apt install -y openscad', kicad_cli: 'sudo apt install -y kicad',
    codecad_python: POSIX_CODECAD },
};
// the commands above use cmd / sh syntax (&&, %VAR%, ~), whatever the user's default terminal is
const INSTALL_SHELL = process.platform === 'win32'
  ? { executable: 'cmd.exe', shellArgs: ['/d', '/c'] } : { executable: '/bin/sh', shellArgs: ['-c'] };
let externalStatus = null;

async function syncExternal() {
  if (!state.connected) return null;
  const paths = {};
  for (const [key, setting] of Object.entries(EXTERNAL_SETTINGS)) paths[key] = cfg(setting) || '';
  try {
    externalStatus = await bridge.ui('external_configure', { paths }, 120000);
  } catch (e) {
    log('harici araçlar: ' + e.message); // older FreeCAD add-on
    return null;
  }
  post(controls, { type: 'external', status: externalStatus });
  return externalStatus;
}

function openFile(file) {
  return vscode.commands.executeCommand('vscode.open', vscode.Uri.file(file), { preview: false });
}

async function renderCommand() {
  const st = externalStatus || await syncExternal() || {};
  const hasBlender = !!(st.blender && st.blender.found);
  const modes = [
    { label: '$(sparkle) Taslak render', description: hasBlender ? 'Blender Cycles, birkaç saniye' : 'Blender kurulu değil', engine: 'blender', quality: 'draft', ok: hasBlender },
    { label: '$(star-full) Son kalite', description: 'Blender Cycles, daha çok örnek, ince mesh', engine: 'blender', quality: 'final', ok: hasBlender },
    { label: '$(sync) Dönen animasyon (GIF)', description: 'Blender, 48 kare', engine: 'blender', quality: 'draft', frames: 48, ok: hasBlender },
    { label: '$(zap) Hızlı render', description: 'Yerleşik, ek program gerekmez', engine: 'quick', quality: 'draft', ok: true },
  ].filter((m) => m.ok);
  const mode = await vscode.window.showQuickPick(modes, { title: 'Render' + (hasBlender ? '' : ' — Blender için: CadAI › Harici araçlar') });
  if (!mode) return;
  const view = await vscode.window.showQuickPick([
    { label: 'İzometrik', view: 'iso' }, { label: 'Ürün fotoğrafı açısı', view: 'hero' }, { label: 'Ön', view: 'front' },
    { label: 'Üst', view: 'top' }, { label: 'Sağ', view: 'right' }, { label: 'Arka izometrik', view: 'iso_back' }],
  { title: 'Kamera açısı' });
  if (!view) return;
  const sel = await bridge.ui('selection');
  const objects = [...new Set(sel.map((s) => s.object))];
  const args = { engine: mode.engine, quality: mode.quality, view: view.view };
  if (objects.length) args.objects = objects;
  if (mode.frames) Object.assign(args, { turntable_frames: mode.frames, width: 960, height: 720 });
  const out = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: 'CadAI: render', cancellable: false },
    async (progress) => {
      if (mode.engine === 'quick') return bridge.toolJson('render', args, 300000);
      // Blender runs in the background so FreeCAD stays responsive; poll its job
      const job = await bridge.toolJson('render', Object.assign(args, { background_job: true }), 120000);
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        const st2 = await bridge.toolJson('render_status', { job: job.job }, 120000);
        if (st2.status !== 'running') return st2;
        progress.report({ message: `Blender çalışıyor… ${Math.round(st2.elapsed_s)} sn` });
      }
    });
  await openFile(out.gif || out.png);
  const where = out.engine === 'blender' ? `${out.device}, ${out.seconds} sn` : `hızlı render, ${out.seconds} sn`;
  const pick = await vscode.window.showInformationMessage(`CadAI: render hazır (${where})`, 'Klasörde göster');
  if (pick) await vscode.commands.executeCommand('revealFileInOS', vscode.Uri.file(out.png));
}

async function importCodeCommand() {
  const file = await pickFile(false, { 'OpenSCAD, build123d/CadQuery, KiCad': ['scad', 'py', 'kicad_pcb'] }, 'Modele aktar');
  if (!file) return;
  const ext = path.extname(file).toLowerCase();
  let tool, args;
  if (ext === '.kicad_pcb') {
    tool = 'kicad_board'; args = { file };
  } else if (ext === '.scad') {
    tool = 'code_cad'; args = { file, language: 'openscad' };
  } else {
    const lang = await vscode.window.showQuickPick([{ label: 'build123d', id: 'build123d' }, { label: 'CadQuery', id: 'cadquery' }],
      { title: `${path.basename(file)}: hangi kütüphane?` });
    if (!lang) return;
    tool = 'code_cad'; args = { file, language: lang.id };
  }
  const out = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `CadAI: ${path.basename(file)} aktarılıyor…` },
    () => bridge.toolJson(tool, args, 600000));
  openViewer();
  if (tool === 'kicad_board') {
    vscode.window.showInformationMessage(`CadAI: ${out.board.object} — kart ${out.board.size_mm.join(' × ')} mm, ` +
      `${out.component_count} bileşen` + (out.missing_3d_models ? ' (bazı 3B modeller eksik)' : ''));
  } else {
    const size = out.bbox.size.map((x) => Math.round(x * 100) / 100).join(' × ');
    vscode.window.showInformationMessage(`CadAI: ${out.object} — ${size} mm, ${out.faces} yüz` + (out.warning ? ' — ⚠ ' + out.warning : ''));
  }
}

async function externalToolsCommand() {
  const st = await syncExternal();
  if (!st) throw new Error('FreeCAD eklentisi harici araçları desteklemiyor; eklentiyi güncelleyin.');
  const items = Object.entries(st).map(([key, p]) => ({
    key, p,
    label: `${p.found ? '$(pass-filled)' : '$(circle-large-outline)'} ${p.title}`,
    description: p.found ? (p.version || '') : 'kurulu değil',
    detail: p.used_for + (p.found ? ` — ${p.path}` : ''),
  }));
  const pick = await vscode.window.showQuickPick(items, { title: 'Harici araçlar (arka planda, penceresiz çalışır)' });
  if (!pick) return;
  const install = (EXTERNAL_INSTALL[process.platform] || EXTERNAL_INSTALL.linux)[pick.key];
  const actions = [];
  if (!pick.p.found && install) actions.push('Kur');
  actions.push('Yolu ayarla', 'İndirme sayfası');
  const act = await vscode.window.showInformationMessage(
    `${pick.p.title}: ${pick.p.found ? 'kurulu' : 'bulunamadı'}.` + (!pick.p.found && install ? `\nKurulum komutu: ${install}` : ''),
    { modal: true }, ...actions);
  if (act === 'Kur') await installExternal(pick.key, pick.p.title, install);
  else if (act === 'Yolu ayarla') await vscode.commands.executeCommand('workbench.action.openSettings', 'cadai.' + EXTERNAL_SETTINGS[pick.key]);
  else if (act === 'İndirme sayfası' && pick.p.url) await vscode.env.openExternal(vscode.Uri.parse(pick.p.url));
}

async function installExternal(key, title, command) {
  const task = new vscode.Task({ type: 'cadai', program: key }, vscode.TaskScope.Global, `${title} kur`, 'CadAI',
    new vscode.ShellExecution(command, INSTALL_SHELL));
  const execution = await vscode.tasks.executeTask(task);
  const done = vscode.tasks.onDidEndTaskProcess(async (e) => {
    if (e.execution !== execution) return;
    done.dispose();
    const st = await syncExternal();
    const ok = st && st[key] && st[key].found;
    if (ok) vscode.window.showInformationMessage(`CadAI: ${title} hazır (${st[key].version || st[key].path}).`);
    else vscode.window.showWarningMessage(`CadAI: ${title} kurulumu bitti (çıkış kodu ${e.exitCode}) ama program bulunamadı. ` +
      'Yeni kurulan programlar için VS Code\'u yeniden başlatmak ya da yolu ayarlardan vermek gerekebilir.');
  });
}

async function addPrimitiveCommand() {
  const kinds = [
    { label: 'Kutu', kind: 'box', fields: [['length', 'Uzunluk (X, mm)', 50], ['width', 'Genişlik (Y, mm)', 30], ['height', 'Yükseklik (Z, mm)', 10]] },
    { label: 'Silindir', kind: 'cylinder', fields: [['radius', 'Yarıçap (mm)', 10], ['height', 'Yükseklik (mm)', 30]] },
    { label: 'Küre', kind: 'sphere', fields: [['radius', 'Yarıçap (mm)', 20]] },
    { label: 'Koni', kind: 'cone', fields: [['radius1', 'Alt yarıçap (mm)', 15], ['radius2', 'Üst yarıçap (mm)', 5], ['height', 'Yükseklik (mm)', 30]] },
  ];
  const pick = await vscode.window.showQuickPick(bridge.backend === 'fusion' ? kinds.slice(0, 2) : kinds, { title: 'Eklenecek şekil' });
  if (!pick) return;
  const params = {};
  for (const [key, prompt, def] of pick.fields) {
    const v = await vscode.window.showInputBox({ prompt, value: String(def), validateInput: (s) => isNaN(Number(s)) ? 'Sayı girin' : null });
    if (v === undefined) return;
    params[key] = Number(v);
  }
  if (bridge.backend === 'fusion') await bridge.toolJson('add_' + pick.kind, params);
  else await bridge.ui('add_primitive', { kind: pick.kind, params });
}

async function editDimension(node) {
  const o = node && node.obj;
  if (!o) return;
  const entries = Object.entries(o.dims || {});
  if (!entries.length) { vscode.window.showInformationMessage('Bu nesnenin düzenlenebilir ölçüsü yok.'); return; }
  const pick = entries.length === 1 ? { prop: entries[0][0], value: entries[0][1] }
    : await vscode.window.showQuickPick(entries.map(([prop, value]) => ({ label: `${prop} = ${value}`, prop, value })), { title: `${o.label} ölçüleri` });
  if (!pick) return;
  const value = await vscode.window.showInputBox({ prompt: `${o.label}.${pick.prop} (birim yazabilirsiniz, ör. 25 mm)`, value: pick.value });
  if (value === undefined) return;
  const num = Number(value);
  await bridge.toolJson('set_property', { object: o.name, property: pick.prop, value: isNaN(num) ? value : num });
}

// Debugging the FreeCAD add-on: VS Code listens (attach + "listen", port 0 = any free port) and FreeCAD connects
// (debugpy.connect) once the adapter reports it is waiting. The other way round (FreeCAD listening) works only once
// per FreeCAD process: after VS Code's "Stop" debugpy refuses a second listen() and nothing could attach again.
const isFreecadSession = (s) => s.type === 'debugpy' && s.configuration.request === 'attach' && s.configuration.listen
  && (s.configuration.cadaiFreecad === true || /freecad/i.test(s.name));
const freecadSessions = new Set();

const freecadDebugTracker = {
  createDebugAdapterTracker(session) {
    if (!isFreecadSession(session)) return undefined;
    return {
      onDidSendMessage: (msg) => {
        if (msg.type !== 'event' || msg.event !== 'debugpyWaitingForServer') return;
        const { host, port } = msg.body;
        bridge.ui('start_debugger', { host, port }, 60000).then(
          () => log(`hata ayıklayıcı: FreeCAD ${host}:${port} adresine bağlandı`),
          (e) => {
            vscode.window.showErrorMessage(`CadAI: FreeCAD hata ayıklayıcıya bağlanamadı: ${e.message}`);
            vscode.debug.stopDebugging(session);
          });
      },
    };
  },
};

async function attachDebugger() {
  if (!vscode.extensions.getExtension('ms-python.debugpy')) {
    const pick = await vscode.window.showErrorMessage('CadAI: hata ayıklamak için "Python Debugger" (ms-python.debugpy) eklentisi gerekli.', 'Kur');
    if (pick === 'Kur') await vscode.commands.executeCommand('workbench.extensions.installExtension', 'ms-python.debugpy');
    return;
  }
  if (freecadSessions.size) {
    vscode.window.showInformationMessage('CadAI: hata ayıklayıcı FreeCAD\'e zaten bağlı.');
    return;
  }
  await bridge.health();  // FreeCAD must be running before the session starts waiting for it
  const userDir = await freecadUserDir(freecadExe());
  const remote = userDir ? path.join(userDir, 'Mod', 'CadAI') : addonDir();
  const ok = await vscode.debug.startDebugging(vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0], {
    type: 'debugpy', request: 'attach', name: 'FreeCAD (CadAI)', cadaiFreecad: true,
    listen: { host: '127.0.0.1', port: 0 },
    pathMappings: [{ localRoot: addonDir(), remoteRoot: remote }], justMyCode: true,
  });
  if (!ok) vscode.window.showErrorMessage('CadAI: hata ayıklama oturumu açılamadı; ayrıntı için Hata Ayıklama Konsolu\'na bakın.');
}

function runTests() {
  const exe = freecad.freecadCmd(freecadExe());
  if (!exe) throw new Error('freecadcmd bulunamadı (FreeCAD kurulu mu?).');
  const tests = path.join(addonDir(), 'tests', 'run_tests.py');
  const task = new vscode.Task({ type: 'cadai' }, vscode.TaskScope.Workspace, 'CadAI testleri', 'cadai',
    new vscode.ProcessExecution(exe, [tests]));
  return vscode.tasks.executeTask(task);
}

async function openAI() {
  await agents.open(currentAgent(), null, projectDir());
}

function register(name, fn) {
  const choose = ['cadai.selectCADSession', 'cadai.connectFusion', 'cadai.setupFusion', 'cadai.startFreeCAD'].includes(name);
  ctx.subscriptions.push(vscode.commands.registerCommand(name, (...a) => guard(() => choose
    ? bridge.outsideOperation(() => fn(...a)) : bridge.operation(() => fn(...a)))));
}

// ---------------- webview html ----------------

function webviewHtml(webview, page) {
  const media = (f) => webview.asWebviewUri(vscode.Uri.joinPath(ctx.extensionUri, 'media', f));
  const csp = `default-src 'none'; img-src ${webview.cspSource} data:; style-src ${webview.cspSource} 'unsafe-inline'; script-src ${webview.cspSource};`;
  const body = fs.readFileSync(path.join(ctx.extensionPath, 'media', `${page}.html`), 'utf8');
  return `<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${media('style.css')}"></head>
<body class="${page}">${body}
<script type="module" src="${media(page + '.js')}"></script></body></html>`;
}

// ---------------- activation ----------------

function activate(context) {
  ctx = context;
  output = vscode.window.createOutputChannel('CadAI');
  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusItem.show();
  treeProvider = new TreeProvider();
  historyProvider = new HistoryProvider();
  historyView = vscode.window.createTreeView('cadai.historyTree', { treeDataProvider: historyProvider });
  agentList = agents.detect();
  context.subscriptions.push(vscode.extensions.onDidChange(() => { agentList = agents.detect(); sendAgents(); }));
  context.subscriptions.push(
    vscode.debug.registerDebugAdapterTrackerFactory('debugpy', freecadDebugTracker),
    vscode.debug.onDidStartDebugSession((s) => { if (isFreecadSession(s)) freecadSessions.add(s.id); }),
    vscode.debug.onDidTerminateDebugSession((s) => freecadSessions.delete(s.id)));
  context.subscriptions.push(output, statusItem, historyView,
    vscode.window.registerTreeDataProvider('cadai.tree', treeProvider),
    vscode.window.registerWebviewViewProvider('cadai.controls', new ControlsProvider(), { webviewOptions: { retainContextWhenHidden: true } }));

  register('cadai.startFreeCAD', startFreeCAD);
  register('cadai.selectCADSession', () => selectCADSession());
  register('cadai.connectFusion', () => selectCADSession('fusion'));
  register('cadai.setupFusion', setupFusion);
  autoSyncFusionAddin();
  register('cadai.showFreeCAD', () => bridge.ui('show_freecad'));
  register('cadai.openViewer', async () => openViewer());
  register('cadai.newDocument', async () => {
    const name = await vscode.window.showInputBox({ prompt: 'Belge adı', value: 'Model' });
    if (name !== undefined) await bridge.ui('new_document', { name: name || 'Model' });
  });
  register('cadai.openDocument', async () => {
    const file = await pickFile(false, { 'CAD dosyaları': ['FCStd', 'step', 'stp', 'iges', 'igs', 'stl', 'brep'] }, 'Dosya aç');
    if (file) { await bridge.ui('open_document', { path: file }); openViewer(); }
  });
  register('cadai.saveDocument', async () => {
    try { await bridge.ui('save_document'); vscode.window.showInformationMessage('CadAI: Kaydedildi.'); } catch (e) {
      if (/dosya yolu/.test(e.message)) return vscode.commands.executeCommand('cadai.saveDocumentAs');
      throw e;
    }
  });
  register('cadai.saveDocumentAs', async () => {
    const file = await pickFile(true, { 'FreeCAD': ['FCStd'] }, 'Farklı kaydet');
    if (file) { const r = await bridge.ui('save_document', { path: file }); vscode.window.showInformationMessage('CadAI: ' + r.file); }
  });
  register('cadai.undo', () => bridge.ui('undo'));
  register('cadai.redo', () => bridge.ui('redo'));
  register('cadai.recompute', () => bridge.ui('recompute'));
  register('cadai.addPrimitive', addPrimitiveCommand);
  register('cadai.measureSelection', async () => {
    const sel = await bridge.ui('selection');
    const objects = [...new Set(sel.map((s) => s.object))];
    if (!objects.length) throw new Error('Önce 3B görünümde bir parça seçin.');
    const m = await bridge.toolJson('measure', { objects, density_kg_m3: 7850 });
    post(controls, { type: 'measure', result: m });
  });
  register('cadai.exportStep', () => exportAs('step', 'STEP'));
  register('cadai.exportStl', () => exportAs('stl', 'STL'));
  register('cadai.technicalDrawing', technicalDrawing);
  register('cadai.render', renderCommand);
  register('cadai.importCode', importCodeCommand);
  register('cadai.externalTools', externalToolsCommand);
  register('cadai.openAI', openAI);
  register('cadai.chooseAgent', chooseAgent);
  register('cadai.markersToAI', markersToAI);
  register('cadai.runTests', runTests);
  register('cadai.reloadAddon', async () => {
    await bridge.reload();
    resetCADView();
    vscode.window.showInformationMessage(`CadAI: ${bridge.label} eklentisi yeniden yüklendi.`);
  });
  register('cadai.attachDebugger', attachDebugger);
  register('cadai.showFemResult', () => showFemResult('von_mises'));
  register('cadai.gpuDiagnostics', gpuDiagnostics);
  register('cadai.history.toggle', () => toggleHistory());
  register('cadai.history.enable', () => toggleHistory(true));
  register('cadai.history.disable', () => toggleHistory(false));
  register('cadai.history.show', showHistory);
  register('cadai.history.refresh', refreshHistory);
  register('cadai.history.openVault', () => openVault());
  register('cadai.history.openVersion', openHistoryVersion);
  register('cadai.history.openNote', openHistoryNote);
  register('cadai.history.copyCommit', async (node) => {
    if (node && node.entry) await vscode.env.clipboard.writeText(node.entry.commit);
  });
  register('cadai.history.selectObject', selectHistoryObject);
  context.subscriptions.push(vscode.workspace.onDidChangeConfiguration((e) => {
    if (e.affectsConfiguration('cadai.history')) syncHistory().then(refreshHistory).catch((err) => log('tasarım geçmişi: ' + err.message));
    if (e.affectsConfiguration('cadai.external')) syncExternal();
    if (e.affectsConfiguration('cadai.mcp.toolset')) {
      mcpChanged.fire();
      vscode.window.showInformationMessage('CadAI: araç seti değişti. Cline / Kilo / Roo / Codex / Claude Code için "Yapay zekâ ajanlarına bağla" komutunu yeniden çalıştırın (Copilot kendiliğinden güncellenir).');
    }
  }));
  register('cadai.dfmCheck', async () => {
    const pick = await vscode.window.showQuickPick([
      { label: 'FDM 3B baskı', process: 'fdm' }, { label: '3 eksen CNC', process: 'cnc' },
      { label: 'Plastik enjeksiyon', process: 'injection_molding' }, { label: 'Sac metal', process: 'sheet_metal' }],
    { title: 'Üretilebilirlik denetimi (DFM): süreç' });
    if (pick) await runDfm(pick.process);
  });
  register('cadai.searchParts', async () => {
    const q = await vscode.window.showInputBox({ prompt: 'step.parts: standart parça ara (ör. M8x20 cıvata, 608 rulman, 2020 profil)' });
    if (!q) return;
    const res = await bridge.toolJson('search_parts', { query: q, limit: 20 }, 60000);
    const pick = await vscode.window.showQuickPick(res.parts.map((x) => ({ label: x.name, description: x.standard || x.family || '', detail: x.id, id: x.id })),
      { title: `${res.count} sonuç — eklemek için seçin` });
    if (pick) await insertPart(pick.id);
  });
  register('cadai.setupFreeCAD', () => setupFreeCAD(true));
  register('cadai.connectAgents', connectAgents);
  register('cadai.tree.refresh', () => refreshDocument());
  register('cadai.tree.select', (node) => node && node.obj && bridge.ui('set_selection', { object: node.obj.name }));
  register('cadai.tree.editDimension', editDimension);
  register('cadai.tree.toggleVisibility', (node) => node && bridge.ui('set_visibility', { name: node.obj.name, visible: !node.obj.visible }));
  register('cadai.tree.delete', async (node) => {
    if (!node) return;
    const ok = await vscode.window.showWarningMessage(`${node.obj.label} silinsin mi?`, { modal: true }, 'Sil');
    if (ok === 'Sil') await bridge.ui('delete_object', { name: node.obj.name });
  });

  nativeMcp = registerNativeMcp(context);
  if (nativeMcp) log("MCP sunucusu VS Code'a yerel API ile tanıtıldı (cadai)");
  onConnectionChanged();
  const timer = setInterval(poll, 700);
  const updateTimer = setInterval(checkExtensionUpdate, 15000);
  context.subscriptions.push({ dispose: () => { clearInterval(timer); clearInterval(updateTimer); } },
    vscode.window.onDidChangeWindowState((s) => { if (s.focused) checkExtensionUpdate(); }));
  checkExtensionUpdate();
  log(`CadAI ${context.extension.packageJSON.version} etkin`);
  setTimeout(() => autoSetup().catch((e) => log('otomatik kurulum: ' + e.message)), 3000);
  poll().then(() => { if (!state.connected && cfg('autoStartFreeCAD')) startFreeCAD(); });
}

function deactivate() {}

module.exports = { activate, deactivate };
