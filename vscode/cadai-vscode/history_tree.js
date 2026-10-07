// Design-history tree data (no VS Code API, unit-tested): commits from history_log grouped by day, newest first.
'use strict';

const path = require('path');

const MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];

function ymd(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** "2026-10-05" -> "Bugün" / "Dün" / "3 Ekim 2026". Commit dates are local ISO times, so the prefix is the local day. */
function dayLabel(key, now = new Date()) {
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key === ymd(now)) return 'Bugün';
  if (key === ymd(yesterday)) return 'Dün';
  const [y, m, d] = key.split('-').map(Number);
  return MONTHS[m - 1] ? `${d} ${MONTHS[m - 1]} ${y}` : key;
}

/** Entries (newest first) -> [{key, label, entries}], newest day first, order kept inside a day. */
function groupByDay(entries, now = new Date()) {
  const days = [];
  for (const e of entries || []) {
    const key = String(e.date || '').slice(0, 10);
    let day = days[days.length - 1];
    if (!day || day.key !== key) {
      day = { key, label: dayLabel(key, now), entries: [] };
      days.push(day);
    }
    day.entries.push(e);
  }
  return days;
}

/** "14:32" from an ISO date. */
function clock(iso) { return String(iso || '').slice(11, 16); }

/** added | removed | modified, from the change text history.py writes ("eklendi (Box)", "silindi", "Length …"). */
function changeKind(text) {
  if (/^(eklendi|ilk kayıt)/.test(text)) return 'added';
  if (/^silindi/.test(text)) return 'removed';
  return 'modified';
}

/** Journal note of a commit as an absolute .md path inside the history folder, or null (the note path comes from a
 *  commit message, so it is never trusted to point elsewhere). */
function notePath(repo, note) {
  if (!repo || !note) return null;
  const root = path.resolve(repo);
  const file = path.resolve(root, note + '.md');
  const rel = path.relative(root, file);
  return rel && !rel.startsWith('..') && !path.isAbsolute(rel) ? file : null;
}

/** Plain-text tooltip of a commit. */
function tooltip(e) {
  const lines = [e.title, `${String(e.date || '').slice(0, 16).replace('T', ' ')} · commit ${e.commit}`];
  if (e.source) lines.push('Kaynak: ' + e.source);
  if (e.changes && e.changes.length) lines.push('', ...e.changes.map((c) => `${c.object}: ${c.text}`));
  return lines.join('\n');
}

module.exports = { dayLabel, groupByDay, clock, changeKind, notePath, tooltip };
