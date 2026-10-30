// E4b (headless Chrome, tests/test_relay_js.py): the browser device's wiring into the page (docs/FAZ3_TASARIM.md §3).
// Its characters are untrusted records turned into v1 Agent JSON for this office only; the provider list matches the
// connector's presets; the dialog is static markup without inline handlers; the virtual folder has its own database.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toLocalAgent } from '../../web/js/browser_office.js';
import { PROVIDER_PRESETS } from '../../web/js/browser_device.js';
import { PROVIDERS } from '../../web/js/keystore.js';

const XSS = '<img src=x onerror=window.__pwnedE4=1>';

test('office records become v1 agents; unknown states fall back to idle; bad ids are dropped', () => {
  const a = toLocalAgent({ id: 'browser-ayse', name: `  Ayşe ${XSS}  `, state: 'working', tool: 'Read',
    control: { device: 'browser', agent: 'ayse' } }, null, 1000);
  assert.equal(a.id, 'browser-ayse');
  assert.equal(a.name, `Ayşe ${XSS}`.slice(0, 40));
  assert.equal(a.state, 'working');
  assert.equal(a.tool, 'Read');
  assert.equal(a.source, 'browser');
  assert.deepEqual(a.control, { device: 'browser', agent: 'ayse' });
  assert.equal(toLocalAgent({ id: 'browser-x', state: 'attention' }).state, 'idle');
  assert.equal(toLocalAgent({ id: 'browser-x', state: '__proto__' }).state, 'idle');
  for (const id of ['x', 'browser-', 'browser-a b', `browser-${'a'.repeat(25)}`, 'room-agent', null, 7]) {
    assert.equal(toLocalAgent({ id, state: 'idle' }), null, String(id));
  }
  assert.equal(toLocalAgent(null), null);
});

test('startedAt is kept and stateSince moves only when the state changes', () => {
  const first = toLocalAgent({ id: 'browser-k', state: 'thinking' }, null, 1000);
  const same = toLocalAgent({ id: 'browser-k', state: 'thinking' }, first, 5000);
  const next = toLocalAgent({ id: 'browser-k', state: 'working' }, same, 9000);
  assert.equal(same.startedAt, 1000);
  assert.equal(same.stateSince, 1000);
  assert.equal(next.stateSince, 9000);
  assert.equal(next.startedAt, 1000);
});

test('the browser device, the key store and the connector presets name the same six providers', async () => {
  assert.deepEqual(Object.keys(PROVIDER_PRESETS).sort(), [...PROVIDERS].sort());
  const py = await (await fetch('/pixel_connector/config.py')).text();
  for (const p of Object.values(PROVIDER_PRESETS)) {
    assert.match(p.url, /^https:\/\/[a-z0-9.-]+\//);
    assert.ok(py.includes(`"url": "${p.url}"`), p.url);
  }
});

test('index.html: the dialog and button are static, hidden, without inline handlers', async () => {
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const btn = doc.getElementById('btn-browser');
  const dlg = doc.getElementById('dlg-browser');
  assert.ok(btn && btn.hidden);
  assert.ok(dlg && dlg.querySelector('#browser-panel') && dlg.querySelector('[data-close]'));
  for (const e of [btn, ...dlg.querySelectorAll('*')]) {
    for (const at of e.getAttributeNames()) assert.ok(!at.startsWith('on'), at);
  }
});

test('main.js gives the virtual folder its own database, never the E2E one', async () => {
  const src = await (await fetch('/web/js/main.js')).text();
  assert.match(src, /new IdbStore\('pixel-office-browser'\)/);
  assert.ok(!/store: new IdbStore\(\)/.test(src));
});
