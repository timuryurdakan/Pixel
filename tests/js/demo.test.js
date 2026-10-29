// G2 (headless Chrome, tests/test_relay_js.py): the live demo office (/?demo=1). The simulation is pure and runs in
// the browser only: fixed agents whose states change, and a Boss job that plans, sends specialists to his desk and
// reports, again and again. The banner and the guide's demo link come from the real markup.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DemoOffice, DEMO_DEVICE, BOSS_CYCLE_S, DEMO_AGENT_COUNT } from '../../web/js/demo.js';
import { readJob } from '../../web/js/boss_scene.js';

const STATES = new Set(['working', 'thinking', 'waiting_for_user', 'idle', 'sleeping']);

/** Deterministic pseudo-random numbers in [0, 1) (a small LCG), so a run is repeatable. */
function lcg(seed = 7) {
  let x = seed;
  return () => { x = (x * 1103515245 + 12345) % 2147483648; return x / 2147483648; };
}

function seq(values) {
  let i = 0;
  return () => values[i++ % values.length];
}

test('agents: a fixed cast in v1 Agent JSON, each with a demo control key the Boss scene can resolve', () => {
  const demo = new DemoOffice({ random: seq([0.5]) });
  const list = demo.start(0);
  assert.equal(list.length, DEMO_AGENT_COUNT);
  const ids = new Set();
  for (const a of list) {
    assert.ok(STATES.has(a.state), a.state);
    assert.equal(typeof a.name, 'string');
    assert.equal(a.control.device, DEMO_DEVICE);
    assert.match(a.control.agent, /^[A-Za-z0-9_-]{1,24}$/);
    ids.add(a.id);
  }
  assert.equal(ids.size, list.length);
});

test('agents change state over time; only changed agents are returned, never more than the cast', () => {
  const demo = new DemoOffice({ random: lcg() });
  demo.start(0);
  const seen = new Set();
  for (let t = 0; t <= 120000; t += 500) {
    const { upserts } = demo.step(t);
    assert.ok(upserts.length <= DEMO_AGENT_COUNT);
    for (const a of upserts) { seen.add(a.state); assert.ok(a.stateSince <= t); }
  }
  assert.ok(seen.has('working') && seen.has('thinking') && seen.has('waiting_for_user'), [...seen].join());
});

test('Boss: one cycle walks through every job state, sends take and deliver for demo agents, then goes idle', () => {
  const demo = new DemoOffice({ random: seq([0.3]) });
  const keys = new Set(demo.start(0).map((a) => a.control.agent));
  const states = [];
  const events = [];
  for (let t = 0; t < BOSS_CYCLE_S * 1000; t += 250) {
    const { boss } = demo.step(t);
    if (boss.job !== undefined) {
      if (boss.job === null) states.push(null);
      else { assert.ok(readJob(boss.job), JSON.stringify(boss.job)); states.push(boss.job.state); }
    }
    for (const e of boss.events) {
      assert.equal(e.type, 'boss.event');
      assert.ok(keys.has(e.agent));
      events.push(e.event);
    }
  }
  for (const st of ['planning', 'approval', 'running', 'reviewing', 'reporting', 'done']) assert.ok(states.includes(st), st);
  assert.ok(events.includes('take') && events.includes('deliver'));
  assert.ok(events.indexOf('take') < events.lastIndexOf('deliver'));
  assert.equal(states.at(-1), null);   // idle again before the next cycle
});

test('step before start or with time going backwards changes nothing', () => {
  const demo = new DemoOffice({ random: seq([0.5]) });
  assert.deepEqual(demo.step(1000), { upserts: [], boss: { events: [] } });
  demo.start(5000);
  demo.step(9000);
  assert.deepEqual(demo.step(8000), { upserts: [], boss: { events: [] } });
});

test('index.html: the demo banner starts hidden, links to the guide and the features page, no inline handlers', async () => {
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const banner = doc.getElementById('demo-banner');
  assert.ok(banner);
  assert.equal(banner.hidden, true);
  const hrefs = [...banner.querySelectorAll('a')].map((a) => a.getAttribute('href'));
  assert.ok(hrefs.includes('/rehber.html'));
  assert.ok(hrefs.includes('/tanitim.html'));
  for (const e of banner.querySelectorAll('*')) {
    for (const at of e.getAttributeNames()) assert.ok(!at.startsWith('on'), at);
  }
});

test('rehber.html: a "Canlı demoyu gör" link to /?demo=1 that guide.js does not rewrite (no data-back)', async () => {
  const html = await (await fetch('/web/rehber.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const link = doc.querySelector('a[href="/?demo=1"]');
  assert.ok(link);
  assert.match(link.textContent, /Canlı demo/);
  assert.equal(link.hasAttribute('data-back'), false);
});
