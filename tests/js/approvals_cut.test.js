// S23-02 (headless Chrome, tests/test_relay_js.py): a permission card whose command summary was cut ("…" from the hook
// or the connector) says so, so nobody approves a command whose end they never saw. Fake hub, real DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ApprovalCenter, summaryWasCut } from '../../web/js/approvals.js';

function fakeHub() {
  return { on() {}, device: () => null, isReady: () => true, request: async () => ({}) };
}

function card(summary) {
  const banner = document.createElement('div');
  const c = new ApprovalCenter({ hub: fakeHub(), banner, now: () => 0 });
  c.add('dev1', { id: 'a' + 'x'.repeat(16), ask: 'tool', tool: 'Bash', agent: 'Claude Code', summary, expiresAt: 60000 });
  return banner;
}

test('a cut command summary shows a warning on the card', () => {
  const b = card('npm test -- --reporter=dot --reporter=dot …');
  const warn = b.querySelector('.apv-cut');
  assert.ok(warn, 'warning element');
  assert.match(warn.textContent, /kısaltıldı/);
  assert.equal(warn.getAttribute('role'), 'note');
});

test('a complete summary has no warning', () => {
  assert.equal(card('git status').querySelector('.apv-cut'), null);
  assert.equal(card('').querySelector('.apv-cut'), null);
});

test('summaryWasCut', () => {
  assert.equal(summaryWasCut('abc…'), true);
  assert.equal(summaryWasCut('abc ...'), false);
  assert.equal(summaryWasCut(''), false);
  assert.equal(summaryWasCut(null), false);
});
