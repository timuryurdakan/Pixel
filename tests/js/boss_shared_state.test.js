// Patron Odası P7 (headless Chrome, tests/test_relay_js.py): the Boss on stage reads the panel's BossStore, so a
// connection asks `boss.get` once (P6 sent two: one from connectBossScene, one from BossStore). Real BossStore /
// BossScene / CharacterManager; only the device hub is faked.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Office } from '../../web/js/office.js';
import { CharacterManager } from '../../web/js/agents.js';
import { BossScene, connectBossScene } from '../../web/js/boss_scene.js';
import { BossStore } from '../../web/js/boss.js';

const JOB = 'b_AAAAAAAAAA';
const RUN = 'w_RRRRRRRRRR';

function state(st, extra = {}) {
  return { type: 'boss.state', job: JOB, state: st, code: null, round: 0, orders: 2, costUsd: 0.04, maxCostUsd: 0.8,
    estimateUsd: 0.9, run: RUN, orderStates: [{ id: 'o1', role: 'docs', state: 'running' }, { id: 'o2', role: 'test', state: 'waiting' }],
    workspace: { id: 'pixel', sub: null }, ...extra };
}

function fakeHub(getJob) {
  const listeners = new Map();
  const asked = [];
  const devices = new Map([['d1', { id: 'd1', ready: true, info: { features: ['boss'] } }],
    ['d2', { id: 'd2', ready: true, info: { features: [] } }]]);
  const hub = {
    asked, devices, getJob,
    on(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
    emit(t, ...a) { for (const fn of listeners.get(t) || []) fn(...a); },
    device: (id) => devices.get(id) || null,
    async request(dev, msg) {
      asked.push([dev, msg.type]);
      return { type: 'boss.get.result', job: hub.getJob, plan: null, report: null };
    },
  };
  return hub;
}

function stage() {
  const m = new CharacterManager(new Office());
  const ids = new Map([['d1:docs', 'od']]);
  return { m, s: new BossScene({ manager: m, resolve: (dev, key) => ids.get(`${dev}:${key}`) || null }) };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test('with the store, one boss.get per connection and the Boss follows the store', async () => {
  const hub = fakeHub(state('approval', { run: null, orderStates: [] }));
  const store = new BossStore({ hub });
  const { s } = stage();
  connectBossScene(s, hub, store);
  await tick();
  assert.deepEqual(hub.asked, [['d1', 'boss.get']]);              // the store's only; d2 has no "boss" feature
  assert.deepEqual(s.job(), { state: 'approval', orders: 2, waiting: 0 });

  hub.emit('event', 'd1', state('running'));
  assert.deepEqual(s.job(), { state: 'running', orders: 2, waiting: 1 });
  hub.emit('event', 'd1', state('done', { orderStates: [] }));
  assert.equal(s.job(), null);
  hub.emit('event', 'd1', state('running'));                       // late "active" after the end: still over
  assert.equal(s.job(), null);
  hub.emit('event', 'd1', { type: 'boss.state', state: 'running', orders: 2 });   // malformed for the store
  assert.equal(s.job(), null);

  hub.getJob = state('planning', { job: 'b_BBBBBBBBBB', run: null, orders: 0, orderStates: [] });   // a new job
  hub.emit('resync', 'd1');                                         // a reconnect asks once more, still once
  await tick();
  assert.deepEqual(hub.asked, [['d1', 'boss.get'], ['d1', 'boss.get']]);
  assert.equal(s.job().state, 'planning');

  hub.devices.get('d1').ready = false;
  hub.emit('change');
  assert.equal(s.job(), null);
  assert.equal(s.jobs.size, 0);
});

test('with the store, boss.event still sends the specialist; a device that goes away drops its pending walks', async () => {
  const hub = fakeHub(state('running'));
  const store = new BossStore({ hub });
  const { s } = stage();
  connectBossScene(s, hub, store);
  await tick();
  assert.equal(hub.asked.length, 1);
  assert.equal(s.onEvent('d1', { event: 'take', agent: 'nobody' }), 'pending');
  hub.emit('event', 'd1', { type: 'boss.event', event: 'take', agent: 'docs' });
  assert.equal(s.pending.length, 2);                                // 'od' is not in the office yet: it waits
  hub.emit('event', 'd1', { type: 'boss.event', event: 'take', agent: 'docs', re: 'r1' });   // a reply: ignored
  assert.equal(s.pending.length, 2);
  hub.devices.get('d1').ready = false;
  hub.emit('change');
  assert.equal(s.pending.length, 0);
  assert.equal(s.jobs.size, 0);
});
