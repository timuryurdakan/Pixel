// v4-B / A9 desk-to-desk handoff (headless Chrome, tests/test_relay_js.py): the "Bitince devret" option in the prompt
// panel, `task.handoff` events (A's and B's output blocks, B's view for the Task Board), the walk + paper animation and
// the new `paper` bubble. Fake hub, real DOM, real Office/CharacterManager.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PromptPanel, HANDOFF_NOTE_MAX } from '../../web/js/prompt.js';
import { categorizeTasks } from '../../web/js/board.js';
import { Office } from '../../web/js/office.js';
import { CharacterManager, HANDOFF_GIVE_S } from '../../web/js/agents.js';
import { validateSprites, getBubbleSprite, BUBBLE_KINDS } from '../../web/js/sprites.js';

const XSS = '<img src=x onerror=window.__pwnedA9=1>';

function fakeHub({ features = ['handoff'], extra = [] } = {}) {
  const agents = [
    { key: 'ka', name: 'Mimar', engine: 'ollama', profile: 'edit', task: null },
    { key: 'kb', name: 'Yazar', engine: 'claude', profile: 'readonly', task: null },
    ...extra,
  ];
  const dev = { id: 'd1', ready: true, status: 'ready', info: { features, remote: {}, limits: { maxPrompt: 32768 }, engines: {} },
    agents: new Map(agents.map((a) => [a.key, a])) };
  const listeners = new Map();
  const calls = [];
  const watches = [];
  return {
    calls, dev, watches,
    on(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
    emit(t, ...a) { for (const fn of listeners.get(t) || []) fn(...a); },
    isPaired: () => true, isReady: () => true, device: () => dev,
    agentInfo: (c) => dev.agents.get(c.agent) || null,
    watch(d, key, on) { watches.push([key, on]); },
    async request(id, msg) {
      calls.push(msg);
      if (msg.type === 'usage.get') return { totals: { tokens: 0, costUsd: 0 }, budgets: [] };
      if (msg.type === 'task.start') return { type: 'task.ack', task: 't_AAAAAAAAAA', agent: msg.agent, confirm: null, queued: false, ...(msg.handoff ? { handoff: msg.handoff.agent } : {}) };
      return {};
    },
  };
}

function panel(hub, onHandoff = null) {
  const slot = document.createElement('div');
  document.body.appendChild(slot);
  const toasts = [];
  const p = new PromptPanel({ hub, slot, toast: (t) => toasts.push(t), onPair() {}, onDevices() {}, onActivity() {}, onHandoff });
  p.show({ id: 'office-a', control: { device: 'd1', agent: 'ka' } });
  return { p, slot, toasts, done() { clearInterval(p._tick); slot.remove(); } };
}

test('compose offers "Bitince devret" with the other agents of the device only', () => {
  const hub = fakeHub({ extra: [{ key: 'kx', name: XSS, engine: 'ollama' }] });
  const { slot, done } = panel(hub);
  const box = slot.querySelector('.ctl-handoff');
  assert.equal(box.hidden, false);
  const opts = [...box.querySelectorAll('select.ctl-handoff-to option')].map((o) => [o.value, o.textContent]);
  assert.deepEqual(opts, [['', 'Devretme'], ['kb', 'Yazar'], ['kx', XSS]]);       // never the agent itself
  assert.equal(box.querySelector('img'), null);
  assert.equal(window.__pwnedA9, undefined);
  assert.equal(box.querySelector('.ctl-handoff-note').hidden, true);
  done();
});

test('hidden when the connector has no handoff feature or there is no other agent', () => {
  const old = panel(fakeHub({ features: null }));            // connector < 3.3.0: no features list
  assert.equal(old.slot.querySelector('.ctl-handoff').hidden, true);
  old.done();
  const hub = fakeHub();
  hub.dev.agents.delete('kb');
  const alone = panel(hub);
  assert.equal(alone.slot.querySelector('.ctl-handoff').hidden, true);
  alone.done();
});

test('send carries {agent, note}; without a choice no handoff field; selection resets after send', async () => {
  const hub = fakeHub();
  const { p, slot, done } = panel(hub);
  slot.querySelector('.ctl-ta').value = 'Plan yap';
  const sel = slot.querySelector('.ctl-handoff-to');
  sel.value = 'kb';
  sel.dispatchEvent(new Event('change'));
  const note = slot.querySelector('.ctl-handoff-note');
  assert.equal(note.hidden, false);
  note.value = 'Bu planı uygula';
  note.dispatchEvent(new Event('input'));
  await p.send();
  const st = hub.calls.filter((c) => c.type === 'task.start');
  assert.deepEqual(st[0], { type: 'task.start', agent: 'ka', prompt: 'Plan yap', fresh: false, handoff: { agent: 'kb', note: 'Bu planı uygula' } });
  assert.match(slot.querySelector('.out-task').textContent, /Bitince «Yazar» ajanına devredilecek/);
  assert.equal(slot.querySelector('.ctl-handoff-to').value, '');
  slot.querySelector('.ctl-ta').value = 'İkinci';
  await p.send();
  assert.equal('handoff' in hub.calls.filter((c) => c.type === 'task.start')[1], false);
  done();
});

test('a too long handoff note is refused in the browser', async () => {
  const hub = fakeHub();
  const { p, slot, done } = panel(hub);
  slot.querySelector('.ctl-ta').value = 'x';
  const sel = slot.querySelector('.ctl-handoff-to');
  sel.value = 'kb';
  sel.dispatchEvent(new Event('change'));
  const note = slot.querySelector('.ctl-handoff-note');
  note.value = 'ş'.repeat(HANDOFF_NOTE_MAX / 2 + 1);
  note.dispatchEvent(new Event('input'));
  await p.send();
  assert.equal(hub.calls.filter((c) => c.type === 'task.start').length, 0);
  assert.match(slot.querySelector('.ctl-msg').textContent, /Devir talimatı çok uzun/);
  done();
});

test('task.handoff started: A notes it, B gets a view + block (Task Board column), animation callback', async () => {
  const hub = fakeHub();
  const seen = [];
  const { p, slot, done } = panel(hub, (dev, m) => seen.push([dev, m.from, m.to]));
  slot.querySelector('.ctl-ta').value = 'Plan yap';
  await p.send();
  hub.emit('event', 'd1', { type: 'task.end', task: 't_AAAAAAAAAA', agent: 'ka', status: 'success', result: 'plan', usage: {} });
  hub.emit('event', 'd1', { type: 'task.handoff', task: 't_AAAAAAAAAA', from: 'ka', to: 'kb', status: 'started',
    next: 't_BBBBBBBBBB', confirm: null, queued: true, code: null, truncated: false });
  assert.match(slot.querySelector('.out-task').textContent, /→ «Yazar» ajanına devredildi/);
  const vb = p.views.get('d1.kb');
  assert.ok(vb);
  assert.equal(vb.task, 't_BBBBBBBBBB');
  assert.equal(vb.state, 'queued');
  assert.match(vb.blocks.get('t_BBBBBBBBBB').el.textContent, /«Mimar» ajanından devredilen görev/);
  assert.deepEqual(seen, [['d1', 'ka', 'kb']]);
  assert.deepEqual(hub.watches.filter(([k]) => k === 'kb'), [['kb', true]]);   // B's events reach this tab
  const office = [{ id: 'oa', name: 'Mimar', state: 'idle', control: { device: 'd1', agent: 'ka' } },
    { id: 'ob', name: 'Yazar', state: 'idle', control: { device: 'd1', agent: 'kb' } }];
  let board = categorizeTasks({ agents: office, views: p.views, isPaired: true });
  assert.deepEqual(board.queued.map((c) => c.agentName), ['Yazar']);
  vb.state = 'confirm';
  board = categorizeTasks({ agents: office, views: p.views, isPaired: true });
  assert.deepEqual(board.waiting.map((c) => c.agentName), ['Yazar']);
  hub.emit('event', 'd1', { type: 'task.chunk', task: 't_BBBBBBBBBB', agent: 'kb', n: 0, parts: [{ k: 'text', v: 'yazıyorum' }] });
  board = categorizeTasks({ agents: office, views: p.views, isPaired: true });
  assert.deepEqual(board.working.map((c) => c.agentName), ['Yazar']);
  hub.emit('event', 'd1', { type: 'task.end', task: 't_BBBBBBBBBB', agent: 'kb', status: 'success', result: 'ok', usage: {} });
  assert.deepEqual(hub.watches.filter(([k]) => k === 'kb'), [['kb', true], ['kb', false]]);
  done();
});

test('task.handoff failed: a Turkish reason, no B view, no animation; malformed events ignored', async () => {
  const hub = fakeHub();
  const seen = [];
  const { p, slot, toasts, done } = panel(hub, () => seen.push(1));
  slot.querySelector('.ctl-ta').value = 'Plan yap';
  await p.send();
  hub.emit('event', 'd1', { type: 'task.handoff', task: 't_AAAAAAAAAA', from: 'ka', to: 'kb', status: 'failed',
    next: null, confirm: null, queued: false, code: 'busy', truncated: false });
  assert.match(slot.querySelector('.out-task').textContent, /Devir yapılamadı: Ajan meşgul\./);
  assert.deepEqual(toasts, ['Devir yapılamadı: Ajan meşgul.']);
  for (const bad of [{ from: 5, to: 'kb' }, { from: 'ka', to: ['kb'] }, { from: 'ka', to: 'kb', status: 'weird' },
    { from: 'ka', to: 'kb', status: 'started', next: 42 }]) {
    hub.emit('event', 'd1', { type: 'task.handoff', task: 't_AAAAAAAAAA', ...bad });
  }
  assert.equal(p.views.has('d1.kb'), false);
  assert.deepEqual(seen, []);
  done();
});

// ---- office animation ------------------------------------------------------------------------------------------
function officeWithTwo() {
  const office = new Office();
  const m = new CharacterManager(office);
  const a = m.upsert({ id: 'oa', name: 'Mimar', state: 'working', colorSeed: 1 }, { entering: false });
  const b = m.upsert({ id: 'ob', name: 'Yazar', state: 'working', colorSeed: 2 }, { entering: false });
  return { office, m, a, b };
}
const run = (m, seconds, t0 = 10) => { let t = t0; for (let i = 0; i < seconds * 60; i++) { t += 1 / 60; m.update(1 / 60, t); } return t; };

test('A walks to B\'s desk, both show the paper bubble, A walks back to its own spot', () => {
  const { m, a, b } = officeWithTwo();
  run(m, 3);
  const home = { x: a.px, y: a.py, spot: a.spot };
  assert.equal(m.handoff('oa', 'ob'), 'walk');
  assert.equal(a.phase, 'walking');
  let t = 13;
  let met = false;
  for (let i = 0; i < 60 * 20 && !met; i++) {
    t += 1 / 60;
    m.update(1 / 60, t);
    met = !!(a.bubble() && a.bubble().kind === 'paper' && b.bubble() && b.bubble().kind === 'paper');
  }
  assert.ok(met, 'both characters showed the paper bubble');
  const d = Math.abs(a.tile.x - b.spot.x) + Math.abs(a.tile.y - b.spot.y);
  assert.ok(d <= 1, `A stood next to B's desk (distance ${d})`);
  t = run(m, HANDOFF_GIVE_S + 20, t);
  assert.equal(a.errand, null);
  assert.equal(a.phase, 'settled');
  assert.equal(a.spot, home.spot);
  assert.deepEqual([a.px, a.py], [home.x, home.y]);
  assert.notEqual((b.bubble() || {}).kind, 'paper');
});

test('a state change during the errand is applied after it; reduced motion only shows the bubbles', () => {
  const { m, a } = officeWithTwo();
  run(m, 3);
  assert.equal(m.handoff('oa', 'ob'), 'walk');
  a.setAgent({ ...a.agent, state: 'idle' });
  assert.ok(a.errand, 'the errand is not cut short');
  run(m, 40, 13);
  assert.equal(a.errand, null);
  assert.equal(a.spot.kind, 'coffee');

  const two = officeWithTwo();
  run(two.m, 3);
  const pos = [two.a.px, two.a.py];
  assert.equal(two.m.handoff('oa', 'ob', { reducedMotion: true }), 'bubbles');
  two.m.update(1 / 60, 13);
  assert.equal(two.a.bubble().kind, 'paper');
  assert.equal(two.b.bubble().kind, 'paper');
  assert.deepEqual([two.a.px, two.a.py], pos);
  run(two.m, HANDOFF_GIVE_S + 0.5, 13);
  assert.notEqual((two.a.bubble() || {}).kind, 'paper');
  assert.equal(two.m.handoff('oa', 'missing'), null);
  two.b.setAgent({ ...two.b.agent, state: 'waiting_for_user' });     // B waits for "Devredilsin mi?" on the computer
  run(two.m, 3, 20);
  two.m.handoff('oa', 'ob', { reducedMotion: true });
  assert.equal(two.b.bubble().kind, 'alert');                        // the "!" is never hidden (v2.md H35)
  assert.equal(two.a.bubble().kind, 'paper');
  assert.equal(two.m.handoff('oa', 'oa'), null);
});

test('paper bubble sprite is an addition and validateSprites() stays empty', () => {
  assert.ok(BUBBLE_KINDS.includes('paper'));
  const img = getBubbleSprite('paper', 0);
  assert.equal(img.width, 12);
  assert.equal(img.height, 12);
  assert.notEqual(getBubbleSprite('paper', 1), img);
  assert.deepEqual(validateSprites(), []);
});
