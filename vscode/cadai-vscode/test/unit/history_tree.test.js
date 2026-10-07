// Unit tests for the design-history tree data (history_tree.js). Run: npm test
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

const h = require(path.join(__dirname, '..', '..', 'history_tree.js'));

const NOW = new Date(2026, 9, 5, 15, 0); // 5 Ekim 2026, local time

test('days are labelled today / yesterday / date', () => {
  assert.equal(h.dayLabel('2026-10-05', NOW), 'Bugün');
  assert.equal(h.dayLabel('2026-10-04', NOW), 'Dün');
  assert.equal(h.dayLabel('2026-09-30', NOW), '30 Eylül 2026');
  assert.equal(h.dayLabel('2026-01-01', new Date(2026, 0, 2)), 'Dün');
  assert.equal(h.dayLabel('2025-12-31', new Date(2026, 0, 1)), 'Dün', 'across a year boundary');
});

test('commits are grouped by day, newest day first, order kept', () => {
  const entries = [
    { commit: 'c4', date: '2026-10-05T14:32:10+03:00', title: 'Beam.Length 100 mm → 120 mm' },
    { commit: 'c3', date: '2026-10-05T09:01:00+03:00', title: 'Pin eklendi' },
    { commit: 'c2', date: '2026-10-04T18:00:00+03:00', title: 'Pin silindi' },
    { commit: 'c1', date: '2026-09-30T08:00:00+03:00', title: 'Bracket: geçmiş başladı' },
  ];
  const days = h.groupByDay(entries, NOW);
  assert.deepEqual(days.map((d) => [d.label, d.entries.map((e) => e.commit)]),
    [['Bugün', ['c4', 'c3']], ['Dün', ['c2']], ['30 Eylül 2026', ['c1']]]);
  assert.deepEqual(h.groupByDay(undefined, NOW), []);
  assert.equal(h.clock(entries[0].date), '14:32');
});

test('change kinds follow the texts history.py writes', () => {
  assert.equal(h.changeKind('eklendi (Cylinder)'), 'added');
  assert.equal(h.changeKind('ilk kayıt'), 'added');
  assert.equal(h.changeKind('silindi'), 'removed');
  assert.equal(h.changeKind('Length 100 mm → 120 mm'), 'modified');
  assert.equal(h.changeKind('hacim 1000 → 1200 mm³'), 'modified');
});

test('a note path from a commit message cannot leave the history folder', () => {
  const repo = path.resolve('/tmp/Bracket.cadai-history');
  assert.equal(h.notePath(repo, 'journal/2026-10-05/143210 Beam.Length'),
    path.join(repo, 'journal', '2026-10-05', '143210 Beam.Length.md'));
  assert.equal(h.notePath(repo, '../../etc/passwd'), null);
  assert.equal(h.notePath(repo, path.resolve('/elsewhere/x')), null);
  assert.equal(h.notePath(repo, ''), null);
  assert.equal(h.notePath(null, 'journal/x'), null);
});

test('tooltip lists source and changes as plain text', () => {
  const t = h.tooltip({ commit: 'abc1234', date: '2026-10-05T14:32:10+03:00', title: 'Beam.Length 100 mm → 120 mm',
    source: 'AI · set_property', changes: [{ object: 'Beam', text: 'Length 100 mm → 120 mm' }] });
  assert.match(t, /2026-10-05 14:32 · commit abc1234/);
  assert.match(t, /Kaynak: AI · set_property/);
  assert.match(t, /Beam: Length 100 mm → 120 mm/);
});
