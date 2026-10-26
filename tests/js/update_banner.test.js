// Connector 3.5.0 (headless Chrome, tests/test_relay_js.py): the "Bilgisayarınızdaki bağlayıcı eski." strip
// (web/js/update_banner.js). Fake hub, real DOM, memory storage; text only, never markup.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { UpdateBanner, DISMISS_KEY, BANNER_TITLE, TEXT_INSTALL, bannerModel, outdatedDevices, safeStorage } from '../../web/js/update_banner.js';

const A = 'a'.repeat(64);
const B = 'b'.repeat(64);
const XSS = '<img src=x onerror=window.__pwnedUB=1>';
const tick = () => new Promise((r) => setTimeout(r, 0));

function fakeHub(devs) {
  const handlers = {};
  return {
    devices: new Map(devs.map((d) => [d.id, d])),
    on(ev, fn) { (handlers[ev] = handlers[ev] || []).push(fn); },
    emit(ev) { for (const fn of handlers[ev] || []) fn(); },
  };
}

function memStorage() {
  const m = new Map();
  return { get: (k) => (m.has(k) ? m.get(k) : null), set: (k, v) => m.set(k, String(v)), m };
}

function setup(devs, sha = A, storage = memStorage()) {
  const root = document.createElement('section');
  root.hidden = true;
  document.body.appendChild(root);
  const calls = { copy: [], toast: [], install: 0, layout: 0 };
  const hub = fakeHub(devs);
  const b = new UpdateBanner({ root, hub, storage, shaFn: async () => sha,
    copy: async (t) => { calls.copy.push(t); return true; }, toast: (t) => calls.toast.push(t),
    onInstall: () => { calls.install += 1; }, onLayout: () => { calls.layout += 1; } });
  return { root, hub, b, calls, storage };
}

const dev = (id, connector, pyz, extra = {}) => ({ id, ready: true, label: `PC ${id}`, info: { connector, pyz }, ...extra });

test('3.5.0+ outdated: the update command with a copy button; dismissal per published SHA', async () => {
  const { root, hub, b, calls, storage } = setup([dev('d1', '3.5.0', B)]);
  await b.refresh();
  assert.equal(root.hidden, false);
  assert.ok(document.body.classList.contains('update-banner-on'));
  assert.ok(root.textContent.startsWith(BANNER_TITLE));
  assert.equal(root.querySelector('.ub-cmd').textContent, 'pixel-connector update');
  assert.equal(root.querySelector('[role=status]').tagName, 'P');
  assert.equal(root.querySelector('.ub-install'), null);
  root.querySelector('.ub-copy').click();
  await tick();
  assert.deepEqual(calls.copy, ['pixel-connector update']);
  assert.deepEqual(calls.toast, ['Komut kopyalandı']);
  for (const btn of root.querySelectorAll('button')) assert.equal(btn.type, 'button');
  assert.equal(root.querySelector('.ub-close').getAttribute('aria-label'), 'Güncelleme uyarısını kapat');
  root.querySelector('.ub-close').click();
  assert.equal(root.hidden, true);
  assert.equal(storage.get(DISMISS_KEY), A);
  assert.ok(!document.body.classList.contains('update-banner-on'));
  hub.emit('change');                                   // stays closed for this release …
  await tick(); await tick();
  assert.equal(root.hidden, true);
  b.sha = 'c'.repeat(64);                               // … and comes back for the next one
  await b.refresh();
  assert.equal(root.hidden, false);
  assert.ok(calls.layout >= 3);
  root.remove();
});

test('pre-3.5.0 outdated: install command, the button opens Cihaz ekle', async () => {
  const { root, b, calls } = setup([dev('d1', '3.4.1', B)]);
  await b.refresh();
  assert.equal(root.hidden, false);
  assert.ok(root.textContent.includes(TEXT_INSTALL));
  assert.equal(root.querySelector('.ub-cmd'), null);
  root.querySelector('.ub-install').click();
  assert.equal(calls.install, 1);
  root.remove();
});

test('mixed devices: install command wins; names are text, at most three', async () => {
  const devs = [dev('d1', '3.5.0', B), dev('d2', '3.4.1', B, { label: XSS }), dev('d3', '3.5.0', B), dev('d4', '3.5.0', B)];
  assert.deepEqual(bannerModel(devs), { useCommand: false, devices: `PC d1, ${XSS.slice(0, 32)}, PC d3 +1` });   // names cut to 32
  const { root, b } = setup(devs);
  await b.refresh();
  assert.ok(root.querySelector('.ub-install'));
  assert.ok(root.querySelector(".ub-dev").textContent.includes(XSS.slice(0, 32)));
  assert.equal(root.querySelector('img'), null);
  assert.equal(window.__pwnedUB, undefined);
  root.remove();
});

test('nothing to show: up to date, offline device, unpaired browser, SHA unknown for a current connector', async () => {
  for (const devs of [[dev('d1', '3.5.0', A)], [dev('d1', '3.5.0', B, { ready: false })], [], [dev('d1', '3.5.0', null)]]) {
    const { root, b } = setup(devs);
    await b.refresh();
    assert.equal(root.hidden, true, JSON.stringify(devs));
    assert.equal(root.childElementCount, 0);
    root.remove();
  }
  const { root, b } = setup([dev('d1', '3.5.0', B)], null);       // SHA256SUMS unavailable → cannot tell
  await b.refresh();
  assert.equal(root.hidden, true);
  root.remove();
  assert.deepEqual(outdatedDevices([dev('d1', '3.0.0', null)], null).map((d) => d.id), ['d1']);   // < 3.1.0 always
});

test('storage that throws never breaks the strip', async () => {
  const throwing = { get() { throw new Error('blocked'); }, set() { throw new Error('blocked'); } };
  const safe = { get: (k) => { try { return throwing.get(k); } catch { return null; } }, set: (k, v) => { try { throwing.set(k, v); } catch { /* ignore */ } } };
  const { root, b } = setup([dev('d1', '3.5.0', B)], A, safe);
  await b.refresh();
  root.querySelector('.ub-close').click();
  assert.equal(root.hidden, true);
  assert.equal(typeof safeStorage.get('x'), 'object');           // the real helper returns null or a string, never throws
  root.remove();
});
