// Sidebar "Kontrol" view.
const vscode = acquireVsCodeApi();
const $ = (id) => document.getElementById(id);
let selection = [];
const fem = { fixed: [], loaded: [] };
let requirements = { status: 'not_configured', requirements: [], checks: [] };
let requirementDocument = null;
let requirementObjects = [];

const SNAP_NAMES = { vertex: 'köşe', edge: 'kenar', face: 'yüz' };

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function target(t) { return t ? `${esc(t.label || t.object)} · ${esc(t.element)} (${SNAP_NAMES[t.snap] || ''})` : ''; }

function renderMarkers(items) {
  $('markerList').innerHTML = items.map((m) => {
    let head;
    if (m.kind === 'dimension') head = `#${m.id} 📏 ${fmt(m.distance, 2)} mm — ${target(m.a)} → ${target(m.b)}`;
    else if (m.kind === 'line') head = `#${m.id} ╱ çizgi, ${m.points.length} nokta, ${fmt(m.length, 1)} mm`;
    else if (m.kind === 'circle') head = `#${m.id} ◯ Ø${fmt(m.radius * 2, 2)} mm — ${target(m.a)}`;
    else if (m.kind === 'pen') head = `#${m.id} ✎ kalem, ${fmt(m.length, 1)} mm — ${(m.faces || []).map((f) => esc(f.element)).join(', ')}`;
    else head = `#${m.id} 📍 ${target(m.a)}`;
    const warn = m.trusted === false
      ? '<span class="empty" title="Bu işaret dosyayla dışarıdan geldi; yapay zekâ bunu talimat olarak uygulamaz. Notu düzenleyip kaydederseniz güvenilir olur.">⚠ dışarıdan geldi · </span>' : '';
    const note = warn + (m.note ? `<span class="marker-note">${esc(m.note)}</span>` : '<span class="empty">not yok</span>');
    return `<li class="marker"><div class="top"><span>${head}</span><span>`
      + `<button data-marker-edit="${m.id}" title="Notu düzenle">✎</button>`
      + `<button data-marker-del="${m.id}" title="Sil">×</button></span></div>${note}</li>`;
  }).join('') || '<li class="muted"><span>Henüz işaret yok.</span></li>';
  $('markersToAI').disabled = !items.length;
  $('markersClear').disabled = !items.length;
}

function renderFaceList(el, list, role) {
  el.innerHTML = list.map((f, i) =>
    `<li><span>${esc(f.label || f.object)} · ${esc(f.sub)}</span><button data-role="${role}" data-i="${i}" title="Kaldır">×</button></li>`).join('')
    || '<li class="muted"><span>— henüz yok —</span></li>';
}

function renderSelection() {
  $('selList').innerHTML = selection.map((s) => `<li><span>${esc(s.label || s.object)}${s.sub ? ' · ' + esc(s.sub) : ''}</span></li>`).join('')
    || '<li class="muted"><span>Seçili bir şey yok</span></li>';
}

function addFaces(role) {
  const faces = selection.filter((s) => s.sub && s.sub.startsWith('Face'));
  if (!faces.length) { $('femStatus').textContent = 'Önce 3B görünümde bir yüz seçin.'; return; }
  for (const f of faces) {
    if (!fem[role].some((x) => x.object === f.object && x.sub === f.sub)) fem[role].push(f);
  }
  renderFaceList($(role === 'fixed' ? 'fixedList' : 'loadedList'), fem[role], role);
  $('femStatus').textContent = '';
}

function fmt(n, d = 3) { return typeof n === 'number' ? n.toLocaleString('tr-TR', { maximumFractionDigits: d }) : n; }

const REQ_STATUS = { pass: '✓ Uygun', fail: '⚠ Şart sağlanmıyor', error: '⚠ Ölçülemedi' };
function renderRequirements(r) {
  requirements = r;
  $('reqSave').disabled = false;
  $('reqCheck').disabled = false;
  $('reqStatus').textContent = r.status === 'not_configured' ? 'Kayıtlı şart yok; uygunluk henüz denetlenmedi.'
    : r.status === 'error' ? 'Denetim yapılamadı: ' + r.error
      : `${r.count - r.failed}/${r.count} şart uygun${r.failed ? ` · ${r.failed} şart incelenmeli` : ''}`;
  $('reqList').innerHTML = (r.checks || []).map((c) => {
    const numbers = (v) => Array.isArray(v) ? v.map((n) => fmt(n, 4)).join(', ') : fmt(v, 4);
    const metric = c.evidence?.metric || '';
    const metricLabel = [...$('reqMetric').options].find((o) => o.value === metric)?.textContent || metric;
    const unit = { count: 'adet', mm3: 'mm³' }[c.unit] || c.unit;
    const result = c.status === 'error' ? esc(c.error) : `${esc(numbers(c.measured))} ${esc(unit)} · hedef `
      + `${{ eq: '=', min: '≥', max: '≤' }[c.operator]} ${esc(numbers(c.expected))} ±${esc(numbers(c.tolerance))}`;
    return `<li class="marker requirement ${c.status === 'pass' ? 'pass' : 'fail'}"><div class="top">`
      + `<b>${esc(REQ_STATUS[c.status] || c.status)} · ${esc(c.id)}</b><span>`
      + `<button type="button" data-req-edit="${esc(c.id)}" title="Şartı düzenle">✎</button>`
      + `<button type="button" data-req-delete="${esc(c.id)}" title="Şartı sil">×</button></span></div>`
      + `<span>${result}</span><span class="muted">${esc(metricLabel)}${c.reference ? ' · ölçüye bağlı hedef' : ''}</span>`
      + (c.evidence?.object ? `<button type="button" data-req-select="${esc(c.evidence.object)}">${esc(c.evidence.object)} · seç</button>` : '')
      + '</li>';
  }).join('');
}

function requirementFields() {
  const metric = $('reqMetric').value, relative = !!$('reqReference').value;
  for (const [selector, show] of [['[data-req-other]', ['min_distance', 'interference_volume'].includes(metric)],
    ['[data-req-hole]', metric.startsWith('hole_')], ['[data-req-edge]', metric === 'hole_edge_offset'],
    ['[data-req-reference]', relative]]) document.querySelectorAll(selector).forEach((el) => el.classList.toggle('hidden', !show));
  $('reqValueLabel').textContent = relative ? 'Fark (hedef = çarpan × ölçüm + fark)' : 'Hedef';
  $('reqHelp').textContent = metric.startsWith('hole_')
    ? 'Tam silindirik delikler ölçülür; açık kanallar kapsam dışıdır. Çap/uzaklık şartıyla birlikte delik sayısını da kaydedin. Uzaklık, merkezin sınırlayıcı kutunun en yakın kenarına uzaklığıdır.'
    : 'Boyutlar dünya eksenlerinde ölçülür. Mesafe çakışma denetimi değildir; bunun için çakışma hacmini kullanın.';
}

function updateRequirementObjects(tree) {
  if (requirementDocument !== tree.active) {
    requirementDocument = tree.active;
    $('reqForm').classList.add('hidden');
    renderRequirements({ status: 'not_configured', requirements: [], checks: [] });
  }
  const flatten = (nodes) => nodes.flatMap((n) => [n, ...flatten(n.children || [])]);
  requirementObjects = flatten(tree.objects || []).filter((o) => /^(Part::|PartDesign::)/.test(o.type || ''));
  for (const id of ['reqObject', 'reqOther', 'reqReference']) {
    const old = $(id).value;
    $(id).innerHTML = (id === 'reqReference' ? '<option value="">Sabit hedef</option>' : '')
      + requirementObjects.map((o) => `<option value="${esc(o.name)}">${esc(o.label || o.name)}</option>`).join('');
    if ([...$(id).options].some((o) => o.value === old)) $(id).value = old;
  }
  for (const id of ['reqAdd', 'reqExport', 'reqCheck', 'reqLink']) $(id).disabled = !tree.active;
}

function editRequirement(id) {
  const r = id ? (requirements.requirements || []).find((r) => r.id === id) : null;
  if (id && !r) { $('reqStatus').textContent = 'Şart tanımı bulunamadı; listeyi yenileyin.'; return; }
  if (r?.reference && !['bbox_x', 'bbox_y', 'bbox_z', 'volume', 'solid_count'].includes(r.reference.metric)) {
    $('reqStatus').textContent = 'Bu gelişmiş ilişkiyi ajan üzerinden düzenleyin; kayıtlı tanım korundu.'; return;
  }
  $('reqForm').reset();
  $('reqId').value = r?.id || '';
  $('reqId').readOnly = !!r;
  if (r) {
    // A deleted object still needs to be visible when fixing a requirement.
    for (const [id, value] of [['reqObject', r.measure.object], ['reqOther', r.measure.other], ['reqReference', r.reference?.object]]) {
      if (!value) continue;
      if (![...$(id).options].some((o) => o.value === value)) $(id).add(new Option(value + ' (bulunamadı)', value));
      $(id).value = value;
    }
    $('reqMetric').value = r.measure.metric; $('reqOperator').value = r.operator || 'eq';
    $('reqValue').value = r.value; $('reqTolerance').value = r.tolerance ?? 0.01;
    $('reqAxis').value = r.measure.axis || 'z'; $('reqEdge').value = r.measure.edge_axis || 'x';
    $('reqRefMetric').value = r.reference?.metric || 'bbox_x'; $('reqFactor').value = r.factor ?? 1;
  }
  $('reqForm').classList.remove('hidden');
  requirementFields(); $('reqId').focus();
}

$('reqMetric').addEventListener('change', () => {
  $('reqTolerance').value = ['solid_count', 'hole_count'].includes($('reqMetric').value) ? '0' : '0.01';
  requirementFields();
});
$('reqReference').addEventListener('change', requirementFields);
$('reqAdd').addEventListener('click', () => editRequirement());
$('reqCancel').addEventListener('click', () => $('reqForm').classList.add('hidden'));
$('reqCheck').addEventListener('click', () => {
  $('reqCheck').disabled = true; $('reqStatus').textContent = 'Geometri denetleniyor…';
  vscode.postMessage({ cmd: 'requirementsCheck' });
});
$('reqExport').addEventListener('click', () => vscode.postMessage({ cmd: 'requirementsExport' }));
$('reqLink').addEventListener('click', () => vscode.postMessage({ cmd: 'parameterRelation' }));
$('reqForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const measure = { object: $('reqObject').value, metric: $('reqMetric').value };
  if (['min_distance', 'interference_volume'].includes(measure.metric)) measure.other = $('reqOther').value;
  if (measure.metric.startsWith('hole_')) measure.axis = $('reqAxis').value;
  if (measure.metric === 'hole_edge_offset') measure.edge_axis = $('reqEdge').value;
  const requirement = { id: $('reqId').value.trim(), measure, operator: $('reqOperator').value,
    value: Number($('reqValue').value), tolerance: Number($('reqTolerance').value) };
  if ($('reqReference').value) {
    requirement.reference = { object: $('reqReference').value, metric: $('reqRefMetric').value };
    requirement.factor = Number($('reqFactor').value);
  }
  $('reqSave').disabled = true;
  vscode.postMessage({ cmd: 'requirementsSave', document: requirementDocument, requirement });
});

function showFemResult(r) {
  const el = $('femResult');
  el.classList.remove('hidden');
  if (r.frequencies_hz) {
    el.innerHTML = '<b>Doğal frekanslar</b>\n' + r.frequencies_hz.map((f, i) => `  ${i + 1}. mod: ${fmt(f, 1)} Hz`).join('\n')
      + `\n<span class="muted">${r.nodes} düğüm · ${fmt(r.elapsed_s, 1)} sn</span>`;
  } else {
    el.innerHTML = `<b>En büyük yer değiştirme:</b> ${fmt(r.max_displacement_mm, 4)} mm\n`
      + `<b>En büyük von Mises:</b> ${fmt(r.max_von_mises_mpa, 1)} MPa\n`
      + `<b>von Mises (%99):</b> ${fmt(r.von_mises_p99_mpa, 1)} MPa\n`
      + (r.force_balance ? `<b>Kuvvet dengesi:</b> ${r.force_balance.ok ? '✓' : '⚠'} tepki ${r.force_balance.reaction_n.map((v) => fmt(v, 1)).join(', ')} N (dengesizlik %${fmt(r.force_balance.imbalance_pct, 2)})\n` : '')
      + `<span class="muted">Tepe gerilme konumu: ${r.max_von_mises_at.map((v) => fmt(v, 1)).join(', ')}\n`
      + `Mesnet köşelerindeki tepe değer sayısal tekillik olabilir; %99 değerine bakın.\n${r.nodes} düğüm · ${fmt(r.elapsed_s, 1)} sn</span>`;
  }
  $('femStatus').textContent = 'Tamamlandı.';
  $('femRun').disabled = false;
}

const SEVERITY = { error: 'hata', warning: 'uyarı', info: 'bilgi' };

function showDfm(r) {
  $('dfmStatus').innerHTML = `<b>${esc(r.object)}</b> · ${esc(r.process_label)}: <b>${esc(r.verdict)}</b> `
    + `(${r.errors} hata, ${r.warnings} uyarı)`;
  const wall = r.measurements.wall_thickness;
  const meas = wall ? `<li class="muted"><span>Duvar kalınlığı ${fmt(wall.min_mm, 2)}–${fmt(wall.max_mm, 2)} mm</span></li>` : '';
  const items = r.findings.map((f) => `<li class="marker finding ${esc(f.severity)}"><div class="top">`
    + `<span><b>${esc(SEVERITY[f.severity] || f.severity)}</b> · ${esc(f.message)}</span></div>`
    + `<span class="basis">${esc(f.basis)}${f.elements ? ' — ' + f.elements.map(esc).join(', ') : ''}</span></li>`).join('');
  $('dfmList').innerHTML = (items || '<li class="muted"><span>Bulgu yok.</span></li>') + meas;
  $('dfmList').classList.remove('hidden');
  $('dfmRun').disabled = false;
}

function showParts(r) {
  $('partsStatus').textContent = `${r.count} sonuç${r.hint ? ' — ' + r.hint : ''}`;
  $('partsList').innerHTML = r.parts.map((p) => `<li><span title="${esc(p.id)}">${esc(p.name)}`
    + `${p.standard ? ' <span class="muted">' + esc(p.standard) + '</span>' : ''}</span>`
    + `<button data-part="${esc(p.id)}" title="Modele ekle">Ekle</button></li>`).join('');
}

document.addEventListener('click', (e) => {
  const t = e.target.closest('button');
  if (!t) return;
  if (t.dataset.reqEdit) { editRequirement(t.dataset.reqEdit); return; }
  if (t.dataset.reqDelete) { vscode.postMessage({ cmd: 'requirementsRemove', document: requirementDocument, id: t.dataset.reqDelete }); return; }
  if (t.dataset.reqSelect) { vscode.postMessage({ cmd: 'requirementsSelect', document: requirementDocument, object: t.dataset.reqSelect }); return; }
  if (t.dataset.part) { vscode.postMessage({ cmd: 'partsInsert', id: t.dataset.part }); return; }
  if (t.dataset.command) vscode.postMessage({ cmd: 'command', command: t.dataset.command });
  else if (t.dataset.markerEdit) vscode.postMessage({ cmd: 'editMarker', id: Number(t.dataset.markerEdit) });
  else if (t.dataset.markerDel) vscode.postMessage({ cmd: 'deleteMarker', id: Number(t.dataset.markerDel) });
  else if (t.dataset.role) {
    fem[t.dataset.role].splice(Number(t.dataset.i), 1);
    renderFaceList($(t.dataset.role === 'fixed' ? 'fixedList' : 'loadedList'), fem[t.dataset.role], t.dataset.role);
  }
});

document.querySelectorAll('[data-mode]').forEach((b) =>
  b.addEventListener('click', () => vscode.postMessage({ cmd: 'viewerMode', mode: b.dataset.mode })));
$('markersToAI').addEventListener('click', () => vscode.postMessage({ cmd: 'markersToAI' }));
$('agentSel').addEventListener('change', () => vscode.postMessage({ cmd: 'setAgent', id: $('agentSel').value }));
$('markersClear').addEventListener('click', () => vscode.postMessage({ cmd: 'clearMarkers' }));
$('selClear').addEventListener('click', () => vscode.postMessage({ cmd: 'clearSelection' }));
$('addFixed').addEventListener('click', () => addFaces('fixed'));
$('addLoaded').addEventListener('click', () => addFaces('loaded'));
$('femType').addEventListener('change', () => $('loadBox').classList.toggle('hidden', $('femType').value !== 'static'));
$('femShow').addEventListener('click', () => vscode.postMessage({ cmd: 'showFemResult' }));
$('histBtn').addEventListener('click', () => {
  $('histBtn').disabled = true; // until FreeCAD confirms the new state
  vscode.postMessage({ cmd: 'historyToggle', on: $('histBtn').dataset.on !== '1' });
});
$('dfmRun').addEventListener('click', () => {
  $('dfmRun').disabled = true;
  $('dfmList').classList.add('hidden');
  vscode.postMessage({ cmd: 'dfm', process: $('dfmProcess').value });
});
$('partsForm').addEventListener('submit', (e) => {
  e.preventDefault();
  vscode.postMessage({ cmd: 'partsSearch', query: $('partsQuery').value });
});
$('femRun').addEventListener('click', () => {
  $('femRun').disabled = true;
  $('femResult').classList.add('hidden');
  $('femStatus').textContent = 'Başlatılıyor…';
  vscode.postMessage({ cmd: 'femRun', params: {
    analysisType: $('femType').value, material: $('femMaterial').value, meshSize: $('femMesh').value || null,
    fixed: fem.fixed, loaded: fem.loaded, force: Number($('femForce').value),
    direction: $('femDir').value.split(',').map(Number),
  } });
});

window.addEventListener('message', (e) => {
  const m = e.data;
  if (m.type === 'connection') {
    $('dot').classList.toggle('on', m.connected);
    $('connText').textContent = m.connected ? 'FreeCAD bağlı' : 'FreeCAD kapalı';
    $('connRow').classList.toggle('hidden', m.connected);
    $('main').classList.toggle('hidden', !m.connected);
  } else if (m.type === 'document') {
    const t = m.tree;
    updateRequirementObjects(t);
    $('docInfo').textContent = t.active
      ? `${t.label}${t.file ? ' — ' + t.file : ' (kaydedilmedi)'} · ${t.objects.length} nesne`
      : 'Açık belge yok. "Yeni" ile başlayın.';
  } else if (m.type === 'requirements') {
    if (m.result.document && m.result.document !== requirementDocument) return;
    renderRequirements(m.result);
    if (m.saved) $('reqForm').classList.add('hidden');
  } else if (m.type === 'requirementsError') {
    $('reqStatus').textContent = 'Denetim yapılamadı: ' + m.text;
    $('reqCheck').disabled = $('reqSave').disabled = false;
  } else if (m.type === 'selection') {
    selection = m.items || [];
    renderSelection();
  } else if (m.type === 'markers') {
    renderMarkers(m.items || []);
  } else if (m.type === 'agents') {
    const sel = $('agentSel');
    sel.innerHTML = m.items.filter((a) => a.available)
      .map((a) => `<option value="${esc(a.id)}">${esc(a.label)}${a.detail ? ' — ' + esc(a.detail) : ''}</option>`).join('');
    sel.value = m.selected;
    $('markersToAI').textContent = 'Uygulat: ' + ((m.items.find((a) => a.id === m.selected) || {}).label || 'ajan');
  } else if (m.type === 'measure') {
    const el = $('measure');
    el.classList.remove('hidden');
    el.innerHTML = Object.entries(m.result).map(([name, v]) => v.bbox
      ? `<b>${esc(name)}</b>: ${v.bbox.size.map((x) => fmt(x, 2)).join(' × ')} mm` + (v.volume_mm3 ? `, hacim ${fmt(v.volume_mm3, 1)} mm³` : '')
        + (v.mass_g ? `, çelik kütlesi ${fmt(v.mass_g, 1)} g` : '')
      : `<b>${esc(name)}</b>: ${esc(JSON.stringify(v))}`).join('\n');
  } else if (m.type === 'femStatus') {
    $('femStatus').textContent = m.text;
    if (m.error) $('femRun').disabled = false;
  } else if (m.type === 'femResult') {
    showFemResult(m.result);
  } else if (m.type === 'dfmStatus') {
    $('dfmStatus').textContent = m.text;
    if (/^Hata/.test(m.text)) $('dfmRun').disabled = false;
  } else if (m.type === 'dfmResult') {
    showDfm(m.result);
  } else if (m.type === 'partsStatus') {
    $('partsStatus').textContent = m.text;
  } else if (m.type === 'partsResult') {
    showParts(m.result);
  } else if (m.type === 'external') {
    $('extList').innerHTML = Object.values(m.status || {}).map((p) => `<li title="${esc(p.used_for)}"><span>`
      + `${p.found ? '✓' : '✗'} <b>${esc(p.title)}</b> <span class="muted">${esc(p.found ? (p.version || '') : 'kurulu değil')}</span>`
      + '</span></li>').join('');
  } else if (m.type === 'history') {
    const st = m.status || {};
    const btn = $('histBtn');
    btn.dataset.on = st.enabled ? '1' : '0';
    btn.textContent = st.enabled ? '■ Geçmişi durdur' : '● Geçmişi aç';
    btn.classList.toggle('primary', !st.enabled);
    btn.disabled = false;
    $('histStatus').textContent = (st.enabled ? 'Açık' : 'Kapalı')
      + (st.enabled && st.repo ? ` · ${st.repo}${st.inside_project_repo ? ' (proje deposu)' : ''}` : '')
      + (st.commits ? ` · bu oturumda ${st.commits} commit` : '')
      + (st.git ? '' : ' · ⚠ git bulunamadı') + (st.last_error ? ` · ⚠ ${st.last_error}` : '');
  }
});

renderMarkers([]);
renderSelection();
renderFaceList($('fixedList'), fem.fixed, 'fixed');
renderFaceList($('loadedList'), fem.loaded, 'loaded');
vscode.postMessage({ cmd: 'ready' });
