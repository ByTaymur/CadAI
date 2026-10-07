// GPU diagnostics, vendor-neutral (AMD, NVIDIA, Intel, Apple, Qualcomm, software renderers, VMs) and cross-platform.
// CAD modeling (B-rep booleans, fillets, recompute) runs on the CPU in every CAD program; the GPU only draws. So the
// question is whether FreeCAD's view (OpenGL) and the CadAI 3D view (WebGL → ANGLE/Direct3D, Metal or OpenGL) run on
// the best GPU in the machine. The classic mistake on desktops: the monitor cable in the motherboard port, so the
// small integrated GPU draws everything while the graphics card idles.
'use strict';

const cp = require('child_process');

const SOFTWARE = /microsoft basic render|swiftshader|llvmpipe|softpipe|lavapipe|software rasterizer|gdi generic|mesa offscreen/i;
// "Microsoft Basic Display Adapter": Windows' fallback when the GPU has no driver installed — a real card running blind
const NO_DRIVER = /microsoft basic display/i;
const VIRTUAL = /vmware|virtualbox|vbox|svga3d|parallels|qxl|virgl|red hat|hyper-v|citrix|remotefx|remote display|indirect display|virtual display|parsec|displaylink|spacedesk|iddsample|duet display|usb mobile monitor/i;
// AMD integrated GPUs as Linux (lspci) names them: APU code names instead of "Radeon Graphics"
const AMD_APU = /raven|picasso|renoir|lucienne|cezanne|barcelo|rembrandt|mendocino|raphael|phoenix|hawk point|strix|krackan|van gogh|granite ridge/i;

/** {vendor, kind} for an adapter / OpenGL / WebGL renderer name.
 *  kind: discrete | integrated | software | virtual | nodriver | unknown. */
function classify(name) {
  const n = String(name || '');
  if (!n.trim()) return { vendor: 'unknown', kind: 'unknown' };
  if (NO_DRIVER.test(n)) return { vendor: 'unknown', kind: 'nodriver' };
  if (SOFTWARE.test(n)) return { vendor: 'software', kind: 'software' };
  if (VIRTUAL.test(n)) return { vendor: 'virtual', kind: 'virtual' };
  if (/nvidia|geforce|quadro|tesla|\brtx\b|\bgtx\b/i.test(n)) return { vendor: 'nvidia', kind: /tegra/i.test(n) ? 'integrated' : 'discrete' };
  if (/\bamd\b|radeon|\bati\b|firepro|instinct/i.test(n)) {
    if (/radeon\s*\(tm\)\s*graphics|radeon graphics|vega \d+ graphics|radeon \d{3,4}[ms]\b|radeon\(tm\) \d{3,4}[ms]|\b\d{3}m graphics/i.test(n)
      || (AMD_APU.test(n) && !/\brx\s?\d/i.test(n))) {
      return { vendor: 'amd', kind: 'integrated' };
    }
    if (/\brx\s?\d|radeon pro|radeon vii|firepro|instinct|\br9\b|\br7\b \d|vega (56|64)|hd \d{4}/i.test(n)) return { vendor: 'amd', kind: 'discrete' };
    return { vendor: 'amd', kind: 'unknown' };
  }
  if (/intel/i.test(n)) {
    // "Intel(R) Arc(TM) A770 Graphics" / "Arc B580" are cards; "Intel(R) Arc(TM) Graphics" (Core Ultra), UHD, Iris are not
    return { vendor: 'intel', kind: /arc[^a-z]*\(?tm\)?\s*[ab]\d{3}|\barc\s+[ab]\d{3}|iris.*xe.*max|\bdg1\b/i.test(n) ? 'discrete' : 'integrated' };
  }
  if (/apple m\d|apple gpu|apple/i.test(n)) return { vendor: 'apple', kind: 'integrated' };
  if (/adreno|qualcomm|snapdragon/i.test(n)) return { vendor: 'qualcomm', kind: 'integrated' };
  if (/mali|powervr|moore threads|mtt s/i.test(n)) return { vendor: 'other', kind: /mtt s\d/i.test(n) ? 'discrete' : 'integrated' };
  // server board display chips (BMC): enough for a console, far too weak for 3D
  if (/aspeed|matrox|g200e/i.test(n)) return { vendor: 'other', kind: 'integrated' };
  return { vendor: 'other', kind: 'unknown' };
}

/** Major OpenGL version from a GL_VERSION string ("4.6.0 Compatibility Profile Context 24.9.1", "3.1 Mesa 23.2"). */
function glMajor(version) {
  const m = String(version || '').match(/^\s*(?:OpenGL ES\s+)?(\d+)\.\d+/i);
  return m ? Number(m[1]) : null;
}

/** "ANGLE (AMD, AMD Radeon(TM) Graphics (0x0000164E) Direct3D11 vs_5_0 ps_5_0, D3D11)" → "AMD Radeon(TM) Graphics". */
function cleanRenderer(s) {
  const str = String(s || '').trim();
  const open = str.indexOf('ANGLE (');
  if (open !== 0 || !str.endsWith(')')) return str.replace(/\s+/g, ' ');
  // "ANGLE (vendor, device, backend)": split on top-level commas only (devices may contain parentheses)
  const inner = str.slice(7, -1), parts = [];
  let depth = 0, cur = '';
  for (const ch of inner) {
    if (ch === '(') depth++;
    if (ch === ')') depth--;
    if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; } else cur += ch;
  }
  parts.push(cur);
  const device = (parts[1] || parts[0] || '').replace(/\s*\(0x[0-9a-f]+\)/ig, '').replace(/\s+(Direct3D|D3D)\d.*$/i, '');
  return device.replace(/\s+/g, ' ').trim();
}

const VENDOR_NAMES = { amd: 'AMD', nvidia: 'NVIDIA', intel: 'Intel', apple: 'Apple', qualcomm: 'Qualcomm' };
const DRIVER_PAGES = {
  amd: 'https://www.amd.com/en/support/download/drivers.html',
  nvidia: 'https://www.nvidia.com/en-us/drivers/',
  intel: 'https://www.intel.com/content/www/us/en/support/detect.html',
};

/** Advice for making an app use the strong GPU on a laptop with switchable graphics, per vendor. */
function hybridAdvice(vendor, apps) {
  const list = apps.join(' ve ');
  const lines = [`Windows: Ayarlar → Sistem → Ekran → Grafik → ${list} için "Yüksek performans" seçin.`];
  if (vendor === 'nvidia') lines.push(`NVIDIA Denetim Masası → 3D Ayarlarını Yönet → Program Ayarları → ${list}: "Yüksek performanslı NVIDIA işlemcisi".`);
  if (vendor === 'amd') lines.push(`AMD Software → Ayarlar → Grafik (Değiştirilebilir Grafik) → ${list}: "Yüksek Performans".`);
  if (vendor === 'intel') lines.push('Intel Arc kartlarda Intel Graphics Software\'tan uygulama profili de ayarlanabilir.');
  lines.push('Linux: uygulamayı "prime-run" (NVIDIA) ya da DRI_PRIME=1 (AMD/Intel) ile başlatın.');
  return lines;
}

/**
 * Findings from what we know: adapters (OS), the OpenGL renderer and version FreeCAD uses, the WebGL renderer of the
 * 3D view (or why it has none, and how often its driver reset) and FreeCAD's view settings.
 * Returns [{severity: 'error'|'warning'|'info', id, title, detail, fix?}].
 */
function diagnose({ adapters = [], freecadGl = null, freecadGlVersion = null, viewerGl = null, viewerError = null, viewerLost = 0,
  settings = null, laptop = false, platform = process.platform, now = Date.now() } = {}) {
  const out = [];
  const ad = adapters.map((a) => Object.assign({}, a, classify(a.name)));
  const best = ad.find((a) => a.kind === 'discrete') || null;
  // remote-desktop, streaming and USB display adapters also "drive" a display; they say nothing about the cable
  const drivingDisplay = ad.filter((a) => a.active && a.kind !== 'virtual');
  const fc = freecadGl ? Object.assign({ name: freecadGl }, classify(freecadGl)) : null;
  const vw = viewerGl ? Object.assign({ name: cleanRenderer(viewerGl) }, classify(viewerGl)) : null;

  for (const a of ad.filter((x) => x.kind === 'nodriver')) {
    out.push({ severity: 'error', id: 'nodriver', title: `Ekran kartı sürücüsü kurulu değil (${a.name})`,
      detail: 'Windows yalnızca temel görüntü sürücüsüyle çalışıyor: 3B hızlandırma yok, FreeCAD ve 3B görünüm çok yavaş ya da hiç açılmaz.',
      fix: 'Kartın üreticisinin sürücüsünü kurun: AMD ' + DRIVER_PAGES.amd + ' · NVIDIA ' + DRIVER_PAGES.nvidia + ' · Intel ' + DRIVER_PAGES.intel
        + ' (ya da Windows Update → İsteğe bağlı güncelleştirmeler).' });
  }
  if (viewerError) {
    out.push({ severity: 'error', id: 'webgl', title: 'CadAI 3B görünüm WebGL 2 başlatamadı',
      detail: String(viewerError).slice(0, 300),
      fix: 'Ekran kartı sürücüsünü kurun/güncelleyin. VS Code donanım hızlandırması kapalıysa açın: Komut Paleti → "Preferences: '
        + 'Configure Runtime Arguments" → "disable-hardware-acceleration": false, sonra VS Code\'u yeniden başlatın. Uzak masaüstü ve '
        + 'sanal makinelerde 3B hızlandırma kapalı olabilir.' });
  }
  if (viewerLost > 0) {
    out.push({ severity: 'warning', id: 'gl-lost', title: `Ekran kartı sürücüsü 3B görünümü bu oturumda ${viewerLost} kez sıfırladı`,
      detail: 'WebGL bağlamı kayboldu; uyku, GPU değişimi, bellek baskısı ya da sürücü sıfırlaması buna yol açabilir. Görünüm bağlam geri geldiğinde yeniden yüklenir.',
      fix: 'Sürücüyü güncelleyin; hız aşırtma/voltaj ayarı varsa varsayılana döndürün; kartın sıcaklığını ve güç kablolarını kontrol edin.' });
  }

  for (const [who, r] of [['FreeCAD (OpenGL)', fc], ['CadAI 3B görünüm (WebGL)', vw]]) {
    if (r && r.kind === 'software') {
      out.push({ severity: 'error', id: `software:${who}`, title: `${who} yazılımla çiziliyor (${r.name})`,
        detail: 'GPU kullanılmıyor; döndürme ve yakınlaştırma çok yavaş olur.',
        fix: who.startsWith('FreeCAD') ? 'FreeCAD: Tercihler → Görünüm → "Yazılımsal OpenGL kullan" kapalı olmalı; ekran kartı sürücüsünü kurun/güncelleyin.'
          : 'VS Code donanım hızlandırması kapalı olabilir: Komut Paleti → "Preferences: Configure Runtime Arguments" → "disable-hardware-acceleration": false. Ekran kartı sürücüsünü güncelleyin.' });
    }
  }

  if (best) {
    const idle = !best.active && drivingDisplay.length && drivingDisplay.every((a) => a.kind === 'integrated');
    const onWeak = [fc, vw].filter((r) => r && r.kind === 'integrated');
    const renderers = [fc, vw].filter(Boolean);
    if (idle && !laptop && renderers.length && renderers.every((r) => r.kind === 'integrated')) {
      out.push({ severity: 'warning', id: 'cable', title: `Uygulamalar tümleşik GPU'da; monitör bağlantısını kontrol edin (${best.name})`,
        detail: 'Ekran bilgisi tümleşik GPU\'yu gösteriyor. Bu, güçlü kartın boşta olduğunu kesin olarak göstermez; uygulama GPU tercihi de etkili olabilir.',
        fix: 'Monitör kablosunun ekran kartının DisplayPort/HDMI çıkışına bağlı olup olmadığını kontrol edin. Windows Grafik ayarlarında uygulamalar için yüksek performans tercihi de kullanılabilir.' });
    } else if (onWeak.length) {
      const apps = [fc && fc.kind === 'integrated' ? 'FreeCAD' : null, vw && vw.kind === 'integrated' ? 'VS Code' : null].filter(Boolean);
      out.push({ severity: 'warning', id: 'hybrid', title: `${apps.join(' ve ')} tümleşik GPU'da çalışıyor; ${best.name} kullanılmıyor`,
        detail: laptop ? 'Değiştirilebilir grafikli dizüstünde uygulamalar varsayılan olarak tasarruflu GPU\'yu kullanır.' : 'Güçlü kart var ama bu uygulamalar onu kullanmıyor.',
        fix: hybridAdvice(best.vendor, apps).join('\n'), apps });
    }
  }

  for (const a of ad) {
    const age = a.driverDate ? (now - Date.parse(a.driverDate)) / (365.25 * 24 * 3600e3) : null;
    if (age !== null && age > 1.5 && DRIVER_PAGES[a.vendor] && (a === best || !best)) {
      out.push({ severity: 'warning', id: `driver:${a.name}`, title: `${a.name}: sürücü ${Math.floor(age)} yıllık`,
        detail: 'Eski OpenGL sürücüleri (özellikle 2022 öncesi AMD) FreeCAD\'de belirgin yavaşlık yapar.', fix: `Güncel sürücü: ${DRIVER_PAGES[a.vendor]}` });
    }
  }

  if (settings) {
    if (settings.software_opengl) {
      out.push({ severity: 'error', id: 'fc-software', title: 'FreeCAD "Yazılımsal OpenGL" ayarı açık', detail: 'GPU hiç kullanılmıyor.', fix: 'apply' });
    }
    // VBOs are only recommended on a real GPU with OpenGL 3+: old, software and virtual drivers can show glitches
    const major = glMajor(freecadGlVersion);
    const vboSafe = fc && !['software', 'virtual', 'nodriver'].includes(fc.kind) && major !== null && major >= 3;
    if (!settings.use_vbo && vboSafe) {
      out.push({ severity: 'info', id: 'fc-vbo', title: 'FreeCAD: OpenGL VBO kapalı', detail: 'Açıkken geometri GPU belleğinde kalır; büyük modellerde döndürme belirgin hızlanır.', fix: 'apply' });
    }
    const version = String(freecadGlVersion || '').match(/^\s*(?:OpenGL ES\s+)?(\d+)\.(\d+)/i);
    if (fc && version && (Number(version[1]) < 2 || (Number(version[1]) === 2 && Number(version[2]) < 1))) {
      out.push({ severity: 'error', id: 'fc-old-gl', title: `FreeCAD OpenGL ${freecadGlVersion} ile çalışıyor`,
        detail: 'FreeCAD en az OpenGL 2.1 ister; bu sürüm genelde sürücünün kurulu olmadığını gösterir.', fix: 'Ekran kartı sürücüsünü kurun/güncelleyin.' });
    }
  }
  if (platform === 'win32' && !adapters.length) {
    out.push({ severity: 'info', id: 'no-adapters', title: 'Ekran kartları okunamadı', detail: 'PowerShell/WMI erişilemedi.' });
  }
  if (!out.some((f) => f.severity !== 'info') && (fc || vw)) {
    out.push({ severity: 'info', id: 'ok', title: 'GPU kullanımı iyi görünüyor',
      detail: [fc && `FreeCAD: ${fc.name}`, vw && `3B görünüm: ${vw.name}`].filter(Boolean).join(' · ') });
  }
  return out;
}

function run(cmd, args, timeout = 15000) {
  return new Promise((resolve) => {
    cp.execFile(cmd, args, { encoding: 'utf8', timeout, windowsHide: true, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => resolve(err ? '' : stdout));
  });
}

/** PowerShell 5.1 serializes dates as "/Date(1786924800000)/", PowerShell 7 as ISO text. */
function psDate(v) {
  if (!v) return undefined;
  const m = String(v).match(/\/Date\((-?\d+)/);
  const t = m ? Number(m[1]) : Date.parse(String(v));
  return Number.isFinite(t) ? new Date(t).toISOString() : undefined;
}

/** Display adapters of this machine: [{name, active, driverVersion?, driverDate?}], plus whether it is a laptop. */
async function listAdapters(platform = process.platform) {
  if (platform === 'win32') {
    const ps = 'Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate,CurrentHorizontalResolution | ConvertTo-Json -Compress;'
      + ' "|BATTERY|"; @(Get-CimInstance Win32_Battery).Count';
    const out = await run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps]);
    const [json, battery] = out.split('|BATTERY|');
    let rows = [];
    try { rows = [].concat(JSON.parse(json.trim() || '[]')); } catch (_) { rows = []; }
    const adapters = rows.filter(Boolean).map((r) => ({ name: (r.Name || '').trim(), active: !!r.CurrentHorizontalResolution,
      driverVersion: r.DriverVersion, driverDate: psDate(r.DriverDate) }));
    return { adapters, laptop: Number((battery || '0').trim()) > 0 };
  }
  if (platform === 'darwin') {
    const out = await run('system_profiler', ['SPDisplaysDataType', '-json']);
    try {
      const items = JSON.parse(out).SPDisplaysDataType || [];
      return { adapters: items.map((i) => ({ name: i.sppci_model || i._name, active: Array.isArray(i.spdisplays_ndrvs) && i.spdisplays_ndrvs.length > 0 })),
        laptop: /book/i.test(await run('sysctl', ['-n', 'hw.model'])) };
    } catch (_) { return { adapters: [], laptop: false }; }
  }
  const out = await run('lspci', ['-mm']);
  const adapters = out.split('\n').filter((l) => /"(VGA compatible controller|3D controller|Display controller)"/.test(l)).map((l) => {
    const f = l.match(/"([^"]*)"/g) || [];
    return { name: f.slice(1, 3).map((s) => s.replace(/"/g, '')).join(' '), active: /VGA/.test(f[0] || '') };
  });
  let laptop = false;
  try { laptop = require('fs').readdirSync('/sys/class/power_supply').some((d) => /^BAT/.test(d)); } catch (_) { /* no sysfs */ }
  return { adapters, laptop };
}

module.exports = { classify, cleanRenderer, diagnose, listAdapters, hybridAdvice, psDate, glMajor };
