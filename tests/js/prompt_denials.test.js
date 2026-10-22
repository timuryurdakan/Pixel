// v2.md H39 + E11 (headless Chrome, tests/test_relay_js.py): permission denials collapse into one friendly line,
// the folder/permission hint, and "Ajanı sil" with an inline confirmation in the prompt panel. Fake hub, real DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TaskBlock, PromptPanel, denialText, shortPath, agentScope } from '../../web/js/prompt.js';
import { DeviceHub, ControlError } from '../../web/js/control.js';
import { MemoryStore } from '../../web/js/idb.js';

const SCOPE = { folder: 'Pixel Office İşleri', profile: 'edit' };
const XSS = '<img src=x onerror=window.__pwnedH39=1>';

function flood(b, n) {
  const calls = [['Bash', 'touch /home/ali/Desktop/not.txt'], ['Write', '/home/ali/Desktop/not.txt'],
    ['Read', '~/Desktop/not.txt'], ['Write', 'C:\\Users\\ali\\Desktop\\b.txt']];
  for (let i = 0; i < n; i++) {
    const [name, v] = calls[i % calls.length];
    b.part({ k: 'tool', name, v });
    b.part({ k: 'denied', name, v: "Permission to use " + name + " has been denied because Claude Code is running in don't ask mode." });
    b.part({ k: 'tool_done', name, ok: false });
  }
}

test('consecutive denials become ONE line with tool → ~/path and the folder', () => {
  const b = new TaskBlock('t1', 'masaüstünde bir txt dosyası oluştur');
  b.scope = SCOPE;
  b.part({ k: 'note', v: 'Model: x' });
  flood(b, 4);
  b.part({ k: 'text', v: 'Masaüstüne yazma iznim yok.' });
  const groups = b.body.querySelectorAll('details.part-denied');
  assert.equal(groups.length, 1);
  const sum = groups[0].querySelector('summary').textContent;
  assert.equal(sum, '⚠ İzin dışı işlem engellendi (4 deneme): Bash · Write → ~/Desktop/not.txt · Read → ~/Desktop/not.txt · … · Bu ajan yalnızca «Pixel Office İşleri» klasöründe çalışabilir.');
  assert.ok(!sum.includes('/home/ali'));
  assert.equal(groups[0].open, false);                               // detail stays collapsed
  assert.equal(groups[0].querySelectorAll('.part-tool.bad').length, 4);
  assert.equal(b.body.querySelectorAll(':scope > .part-tool').length, 0);   // no red rows outside the group
  assert.match(b.body.textContent, /Masaüstüne yazma iznim yok/);
});

test('text or a successful tool closes the group; genuine tool errors stay visible', () => {
  const b = new TaskBlock('t2', 'x');
  b.scope = SCOPE;
  flood(b, 2);
  b.part({ k: 'tool', name: 'Read', v: 'a.md' });
  b.part({ k: 'tool_done', name: 'Read', ok: true });
  flood(b, 1);
  b.part({ k: 'tool', name: 'Edit', v: 'b.md' });
  b.part({ k: 'tool_done', name: 'Edit', ok: false });                // a real error (not a denial)
  const groups = b.body.querySelectorAll('details.part-denied');
  assert.equal(groups.length, 2);
  assert.match(groups[0].querySelector('summary').textContent, /\(2 deneme\)/);
  assert.match(groups[1].querySelector('summary').textContent, /^⚠ İzin dışı işlem engellendi: Bash · /);
  const rows = b.body.querySelectorAll(':scope > .part-tool');
  assert.equal(rows.length, 2);
  assert.ok(rows[0].classList.contains('ok'));
  assert.ok(rows[1].classList.contains('bad'));
  assert.match(rows[1].textContent, /Edit — b\.md ✗/);
  b.end({ status: 'error' }, 'Hata: Motor beklenmedik biçimde kapandı.', null);
  assert.match(b.el.querySelector('.out-end.st-error').textContent, /Motor beklenmedik/);
});

test('readonly text, unknown scope and text-only rendering', () => {
  assert.equal(denialText(1, ['Write → ~/a'], { folder: 'Notlar', profile: 'readonly' }),
    '⚠ İzin dışı işlem engellendi: Write → ~/a · Bu ajan yalnızca «Notlar» klasöründe ve yalnızca okuma yapabilir.');
  assert.equal(denialText(2, ['Bash', 'Bash'], null), '⚠ İzin dışı işlem engellendi (2 deneme): Bash · Bu ajanın izinleri bu işlemi kapsamıyor.');
  assert.equal(shortPath('/Users/ali/Desktop/x /home/bob/y C:\\Users\\Ali\\Desktop'), '~/Desktop/x ~/y ~\\Desktop');
  const b = new TaskBlock('t3', 'x');
  b.scope = { folder: XSS, profile: 'edit' };
  b.part({ k: 'tool', name: 'Write', v: XSS });
  b.part({ k: 'denied', name: 'Write', v: XSS });
  assert.equal(b.body.querySelector('img'), null);
  assert.equal(window.__pwnedH39, undefined);
  assert.deepEqual(agentScope({ profile: 'dev', workspace: { name: 'Pixel', sub: 'web' } }), { folder: 'Pixel/web', profile: 'dev' });
});

// ---- prompt panel: folder hint + delete ---------------------------------------------------------------------
function fakeHub({ remote = { agentDelete: true }, task = null } = {}) {
  const agent = { key: 'k1', name: 'Kâtip', engine: 'claude', profile: 'readonly', task,
    workspace: { id: 'isler', name: 'Pixel Office İşleri', sub: null } };
  const dev = { id: 'd1', ready: true, status: 'ready', info: { remote, engines: { claude: { auth: 'subscription' } } },
    agents: new Map([[agent.key, agent]]) };
  const listeners = new Map();
  const calls = [];
  return {
    calls, dev, agent,
    on(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
    emit(t, ...a) { for (const fn of listeners.get(t) || []) fn(...a); },
    isPaired: () => true, isReady: () => true, device: () => dev,
    agentInfo: (c) => dev.agents.get(c.agent) || null,
    watch() {},
    async request(id, msg) { calls.push(msg); if (msg.type === 'usage.get') return { totals: { tokens: 0, costUsd: 0 }, budgets: [] }; return {}; },
    async deleteAgent(id, key) { calls.push({ type: 'agent.delete', agent: key }); dev.agents.delete(key); },
  };
}

function panel(hub) {
  const slot = document.createElement('div');
  document.body.appendChild(slot);
  const toasts = [];
  const p = new PromptPanel({ hub, slot, toast: (t) => toasts.push(t), onPair() {}, onDevices() {}, onActivity() {} });
  p.show({ id: 'office-1', control: { device: 'd1', agent: 'k1' } });
  return { p, slot, toasts };
}
const tick = () => new Promise((r) => setTimeout(r, 10));

test('prompt box shows the folder and the permission', async () => {
  const hub = fakeHub();
  const { p, slot } = panel(hub);
  assert.equal(slot.querySelector('.ctl-hint').textContent, 'Klasör: Pixel Office İşleri · İzin: Sadece okuma · Yalnızca bu klasörde çalışır');
  clearInterval(p._tick);
  slot.remove();
});

test('delete: inline confirm, Vazgeç keeps the agent, Evet deletes', async () => {
  const hub = fakeHub();
  const { p, slot, toasts } = panel(hub);
  const del = slot.querySelector('.ctl-del');
  assert.equal(slot.querySelector('.ctl-delete').hidden, false);
  assert.equal(del.disabled, false);
  del.click();
  const ask = slot.querySelector('.ctl-del-ask');
  assert.equal(ask.hidden, false);
  assert.equal(slot.querySelector('.ctl-del-q').textContent, '«Kâtip» silinsin mi? Karakter ofisten çıkar, sohbet geçmişi silinir.');
  slot.querySelector('.ctl-del-no').click();
  assert.equal(ask.hidden, true);
  assert.equal(hub.calls.filter((c) => c.type === 'agent.delete').length, 0);
  del.click();
  slot.querySelector('.ctl-del-yes').click();
  await tick();
  assert.deepEqual(hub.calls.filter((c) => c.type === 'agent.delete'), [{ type: 'agent.delete', agent: 'k1' }]);
  assert.deepEqual(toasts, ['Ajan silindi']);
  assert.equal(slot.querySelector('.ctl-delete').hidden, true);
  assert.equal(slot.querySelector('.ctl-ta').disabled, true);
  assert.match(slot.querySelector('.ctl-msg').textContent, /Ajan silindi/);
  clearInterval(p._tick);
  slot.remove();
});

test('delete: disabled while a task runs, hidden when the device switched it off', async () => {
  const busy = panel(fakeHub({ task: 't_running01' }));
  assert.equal(busy.slot.querySelector('.ctl-del').disabled, true);
  assert.match(busy.slot.querySelector('.ctl-del-why').textContent, /önce durdurun/);
  clearInterval(busy.p._tick);
  busy.slot.remove();
  const off = panel(fakeHub({ remote: { agentDelete: false } }));
  assert.equal(off.slot.querySelector('.ctl-delete').hidden, true);
  clearInterval(off.p._tick);
  off.slot.remove();
  const old = panel(fakeHub({ remote: { agentCreate: true } }));     // connector < 3.2.4: no agentDelete key
  assert.equal(old.slot.querySelector('.ctl-delete').hidden, false);
  clearInterval(old.p._tick);
  old.slot.remove();
});

test('hub: agent.deleted from the device removes the agent in every tab; deleteAgent forgets it', async () => {
  const hub = new DeviceHub({ roomId: 'abcdefghijklmnopqrstuvwxyz', writeKey: 'K'.repeat(32), store: new MemoryStore() });
  const d = { id: 'd1', agents: new Map([['a', { key: 'a' }], ['b', { key: 'b' }]]), watching: new Set(['b']) };
  hub.devices.set('d1', d);
  let changes = 0;
  hub.on('change', () => { changes += 1; });
  hub._dispatch(d, { type: 'agent.deleted', agent: 'a' });
  assert.deepEqual([...d.agents.keys()], ['b']);
  assert.equal(changes, 1);
  hub.request = async (id, msg) => { assert.deepEqual(msg, { type: 'agent.delete', agent: 'b' }); return { type: 'agent.deleted', agent: 'b' }; };
  await hub.deleteAgent('d1', 'b');
  assert.equal(d.agents.size, 0);
  assert.equal(d.watching.size, 0);
  hub.request = async () => { throw new ControlError('not_allowed'); };
  d.agents.set('c', { key: 'c' });
  await assert.rejects(hub.deleteAgent('d1', 'c'));
  assert.equal(d.agents.size, 1);
});
