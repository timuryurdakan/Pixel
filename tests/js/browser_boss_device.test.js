// F4-3a end to end (headless Chrome): the real DeviceHub, BrowserDevice, BossStore and the Boss panel from index.html,
// with only the provider faked. The page talks boss.* to the browser device exactly as to a connector; the API key
// never shows up in any message; the panel shows the model row (not the cost cap) for the browser device.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MemoryStore } from '../../web/js/idb.js';
import { DeviceHub } from '../../web/js/control.js';
import { BrowserDevice, createMemoryKeystore } from '../../web/js/browser_device.js';
import { planMarkers, reviewMarkers } from '../../web/js/browser_boss.js';
import { BossStore, BossPanel } from '../../web/js/boss.js';

const CANARY = 'sk-CANARY-f43a-0123456789-SECRET';
const PLAN = { v: 1, summary: 'Bir not', orders: [{ id: 'o1', role: 'docs', title: 'Not yaz', goal: 'notlar/a.md yaz',
  accept: ['Dosya var'], files: ['notlar/a.md'] }] };

function reply(content, out = 20) {
  const body = JSON.stringify({ choices: [{ message: { role: 'assistant', content } }],
    usage: { prompt_tokens: 10, completion_tokens: out } });
  return { status: 200, headers: { get: () => 'application/json' }, text: async () => body, json: async () => JSON.parse(body) };
}

/** The fake provider answers by what the last user message asks for. */
function provider(seen) {
  return async (url, init) => {
    const body = JSON.parse(init.body);
    seen.push({ url, auth: init.headers.Authorization, body });
    const last = [...body.messages].reverse().find((m) => m.role === 'user').content;
    const pm = /<<<PATRON-PLAN-(\w+) BAŞLANGIÇ>>>/.exec(last);
    if (pm) { const [s, e] = planMarkers(pm[1]); return reply(`Plan:\n${s}\n${JSON.stringify(PLAN)}\n${e}`); }
    const rm = /<<<PATRON-KARAR-(\w+) BAŞLANGIÇ>>>/.exec(last);
    if (rm) { const [s, e] = reviewMarkers(rm[1]); return reply(`Not yazılmış.\n${s}\n{"v":1,"decision":"accept"}\n${e}`); }
    return reply('Not yazıldı.');
  };
}

async function until(cond, ms = 4000) {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error('condition not reached');
    await new Promise((r) => setTimeout(r, 10));
  }
}

async function env() {
  const store = new MemoryStore();
  const hub = new DeviceHub({ roomId: 'a'.repeat(26), writeKey: 'k'.repeat(24), store,
    relayFactory: () => ({ status: 'idle', presence: new Set(), need() {}, send() { return true; }, destroy() {} }) });
  await hub.init();
  const keystore = createMemoryKeystore();
  await keystore.set('groq', CANARY, { remember: false });
  const seen = [];
  const events = [];
  const office = [];
  hub.on('event', (dev, m) => events.push(m));
  const dev = new BrowserDevice({ keystore, store: new MemoryStore(), fetchFn: provider(seen), onUpsert: (a) => office.push(a) });
  await dev.attach(hub);
  return { hub, dev, seen, events, office };
}

test('boss.* through the hub: plan, approval, the specialist, review and report; the key stays in the header only', async () => {
  const e = await env();
  const info = e.hub.devices.get(e.dev.id).info;
  assert.ok(info.features.includes('boss') && info.features.includes('bossDepartments'));
  const store = new BossStore({ hub: e.hub });
  const ack = await e.hub.request(e.dev.id, { type: 'boss.ask', workspace: { id: e.dev.workspace.workspaceId },
    brief: 'Bir not yaz', department: 'yazilim', model: { provider: 'groq', model: 'llama-3.3-70b-versatile' } });
  assert.equal(ack.type, 'boss.ack');
  await until(() => store.entry(e.dev.id) && store.entry(e.dev.id).job.state === 'approval');
  assert.equal(store.pendingPlans().length, 1);
  await e.hub.request(e.dev.id, { type: 'boss.approve', job: ack.job.job, approve: true });
  await until(() => store.entry(e.dev.id).job.state === 'done');
  assert.ok(store.entry(e.dev.id).report.report.includes('Not yazılmış.'));
  assert.ok(e.office.some((a) => a.control && a.control.agent === 'boss-docs'));        // the specialist walks in
  assert.ok(!e.office.some((a) => a.control && a.control.agent === 'patron'));          // the Boss is the scene's
  assert.deepEqual(e.events.filter((m) => m.type === 'boss.event').map((m) => m.event), ['take', 'deliver']);
  assert.ok(e.seen.length >= 3);
  for (const s of e.seen) {
    assert.equal(s.auth, `Bearer ${CANARY}`);
    assert.ok(s.url.startsWith('https://api.groq.com/'));
    assert.equal(s.body.model, 'llama-3.3-70b-versatile');
    assert.ok(!JSON.stringify(s.body).includes(CANARY));
  }
  assert.ok(!JSON.stringify(e.events).includes(CANARY));
  const saved = await e.dev.store.get('e2e', `browserAgents:${e.dev.id}`);
  assert.ok(!(saved || []).some((a) => a.key === 'boss-docs' || a.key === 'patron'));   // transient, never saved
});

test('requests are checked at the boundary: unknown fields, other folders and a busy Boss are refused', async () => {
  const e = await env();
  const ws = { id: e.dev.workspace.workspaceId };
  const model = { provider: 'groq', model: 'm1' };
  for (const [msg, code] of [[{ type: 'boss.ask', workspace: ws, brief: 'x', model, maxTurns: 9 }, 'bad_request'],
    [{ type: 'boss.ask', workspace: { id: 'other' }, brief: 'x', model }, 'workspace_denied'],
    [{ type: 'boss.ask', workspace: ws, brief: 'x', model: { provider: 'evil', model: 'm' } }, 'bad_request'],
    [{ type: 'boss.approve', job: 'b_XXXXXXXXXX', approve: true }, 'unknown_job']]) {
    await assert.rejects(e.hub.request(e.dev.id, msg), (x) => x.code === code, JSON.stringify(msg));
  }
});

test('the panel shows the model row, not the cost cap, for the browser device and sends the chosen model', async () => {
  const e = await env();
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const dlg = document.importNode(doc.getElementById('dlg-boss'), true);
  document.body.appendChild(dlg);
  try {
    const store = new BossStore({ hub: e.hub });
    const panel = new BossPanel({ dialog: dlg, hub: e.hub, store, toast: () => {} });
    panel.open();
    await until(() => !dlg.querySelector('#boss-model-row').hidden);
    assert.equal(dlg.querySelector('#boss-cost-row').hidden, true);
    const prov = dlg.querySelector('[name=provider]');
    assert.ok([...prov.options].some((o) => o.value === 'groq'));
    prov.value = 'groq';
    prov.dispatchEvent(new Event('change'));
    assert.ok(dlg.querySelector('[name=model]').options.length > 0);
    dlg.querySelector('[name=brief]').value = 'Bir not';
    const r = panel.request();
    assert.deepEqual(r.msg.model, { provider: 'groq', model: dlg.querySelector('[name=model]').value });
    assert.equal(r.msg.maxCostUsd, undefined);
    panel.close();
  } finally { dlg.remove(); }
});
