// F4-3b end to end (headless Chrome): the real DeviceHub, BrowserDevice, WorkflowStore and the workflow dialog from
// index.html, with only the provider faked. The page talks workflow.* to the browser device exactly as to a connector;
// the API key never shows up in any message; the dialog offers one provider/model and no cost cap for the browser.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MemoryStore } from '../../web/js/idb.js';
import { DeviceHub } from '../../web/js/control.js';
import { BrowserDevice, createMemoryKeystore } from '../../web/js/browser_device.js';
import { WorkflowStore, WorkflowDialog, supportsWorkflows, progressText } from '../../web/js/workflows.js';

const CANARY = 'sk-CANARY-f43b-0123456789-SECRET';
const MODEL = { provider: 'groq', model: 'llama-3.3-70b-versatile' };

function reply(content, out = 20) {
  const body = JSON.stringify({ choices: [{ message: { role: 'assistant', content } }],
    usage: { prompt_tokens: 10, completion_tokens: out } });
  return { status: 200, headers: { get: () => 'application/json' }, text: async () => body, json: async () => JSON.parse(body) };
}

async function until(cond, ms = 4000) {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error('condition not reached');
    await new Promise((r) => setTimeout(r, 10));
  }
}

async function env(library = []) {
  const hub = new DeviceHub({ roomId: 'a'.repeat(26), writeKey: 'k'.repeat(24), store: new MemoryStore(),
    relayFactory: () => ({ status: 'idle', presence: new Set(), need() {}, send() { return true; }, destroy() {} }) });
  await hub.init();
  const keystore = createMemoryKeystore();
  await keystore.set('groq', CANARY, { remember: false });
  const seen = [];
  const events = [];
  const office = [];
  hub.on('event', (dev, m) => events.push(m));
  let n = 0;
  const fetchFn = async (url, init) => {
    seen.push({ url, auth: init.headers.Authorization, body: JSON.parse(init.body) });
    n += 1;
    return reply(`ADIM-${n}-ÇIKTISI`);
  };
  const store = new MemoryStore();
  const dev = new BrowserDevice({ keystore, store, fetchFn, onUpsert: (a) => office.push(a), templateLibrary: () => library });
  await dev.attach(hub);
  return { hub, dev, store, seen, events, office };
}

test('workflow.* through the hub: the built-in template runs step by step, waits for approval, the key stays in the header', async () => {
  const e = await env();
  const d = e.hub.devices.get(e.dev.id);
  assert.ok(supportsWorkflows(d));
  assert.ok(!d.info.features.includes('workflowSave'));                 // C1: the library is already in this browser
  const runs = new WorkflowStore({ hub: e.hub });
  await runs.load(e.dev.id);
  assert.ok(runs.templates.get(e.dev.id).list.some((t) => t.id === 'statik-site'));
  const ack = await e.hub.request(e.dev.id, { type: 'workflow.start', template: 'statik-site',
    workspace: { id: e.dev.workspace.workspaceId, sub: null }, brief: 'Kafe sitesi', model: MODEL });
  assert.equal(ack.type, 'workflow.started');
  const id = ack.run.id;
  const run = () => runs.list(e.dev.id).find((x) => x.run.id === id).run;
  await until(() => runs.list(e.dev.id).some((x) => x.run.id === id && x.run.state === 'approval'));
  assert.deepEqual(run().steps.map((s) => s.state), ['done', 'done', 'done', 'approval']);
  assert.ok(run().steps.slice(0, 3).every((s) => /^bt_/.test(s.task) && s.agent === `wf-${s.id}`));
  assert.match(progressText(run()), /jeton/);
  await assert.rejects(e.hub.request(e.dev.id, { type: 'boss.ask', workspace: { id: e.dev.workspace.workspaceId },
    brief: 'x', model: MODEL }), (x) => x.code === 'busy');                                        // C6
  await e.hub.request(e.dev.id, { type: 'workflow.answer', run: id, decision: 'approve' });
  await until(() => run().state === 'done');
  assert.equal(e.seen.length, 3);
  assert.ok(e.seen[1].body.messages.at(-1).content.includes('ADIM-1-ÇIKTISI'));                     // handed over, fenced
  for (const s of e.seen) {
    assert.equal(s.auth, `Bearer ${CANARY}`);
    assert.ok(s.url.startsWith('https://api.groq.com/'));
    assert.ok(!JSON.stringify(s.body).includes(CANARY));
  }
  const wfEvents = JSON.stringify(e.events.filter((m) => m.type === 'workflow.state'));
  assert.ok(!wfEvents.includes(CANARY) && !wfEvents.includes('ADIM-1-ÇIKTISI') && !wfEvents.includes('Kafe sitesi'));
  assert.ok(e.office.some((a) => a.control && a.control.agent === 'wf-mimar'));                     // the step walks in
  const saved = await e.store.get('e2e', `browserAgents:${e.dev.id}`);
  assert.ok(!(saved || []).some((a) => String(a.key).startsWith('wf-')));                           // transient
  const kept = await e.store.get('e2e', `browserWorkflows:${e.dev.id}`);
  assert.equal(kept.at(-1).state, 'done');
  assert.ok(!JSON.stringify(kept).includes(CANARY));
});

test('the dialog offers the provider and model for the browser device, hides the cost cap and refuses dev templates', async () => {
  const dev = { id: 'kur', name: 'Kurulum', steps: [{ id: 'a', type: 'agent', role: 'Kurucu', engine: 'claude', profile: 'dev', prompt: 'p' }] };
  const e = await env([dev]);
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const dlg = document.importNode(doc.getElementById('dlg-workflows'), true);
  document.body.appendChild(dlg);
  try {
    const store = new WorkflowStore({ hub: e.hub });
    const w = new WorkflowDialog({ dialog: dlg, hub: e.hub, store, toast: () => {}, storage: { getItem: () => null, setItem() {} } });
    w.open();
    await until(() => [...dlg.querySelector('[name=template]').options].some((o) => o.value === 'statik-site'));
    assert.equal(dlg.querySelector('#wf-cost-row').hidden, true);
    assert.equal(dlg.querySelector('#wf-model-row').hidden, false);
    assert.equal(dlg.querySelector('#wf-sub-row').hidden, true);                                    // C7
    assert.equal(dlg.querySelector('.wf-engine'), null);                                             // C2: no per-step engine
    const prov = dlg.querySelector('#wf-model-row [name=provider]');
    prov.value = 'groq';
    prov.dispatchEvent(new Event('change'));
    dlg.querySelector('[name=brief]').value = 'Kafe sitesi';
    const r = w.request();
    assert.deepEqual(r.msg.model, { provider: 'groq', model: dlg.querySelector('#wf-model-row [name=model]').value });
    assert.equal(r.msg.maxCostUsd, undefined);
    assert.equal(r.msg.steps, undefined);
    const sel = dlg.querySelector('[name=template]');
    sel.value = 'kur';
    sel.dispatchEvent(new Event('change'));
    assert.match(sel.selectedOptions[0].textContent, /tarayıcıda çalışmaz/);
    assert.equal(dlg.querySelector('[name=start]').disabled, true);
    assert.match(dlg.querySelector('#wf-limits').textContent, /tarayıcıda çalışamaz/);
    assert.ok(w.request().error);
    w.close();
  } finally { dlg.remove(); }
});
