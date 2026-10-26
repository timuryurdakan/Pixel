// v4-C / A10 workflows (headless Chrome, tests/test_relay_js.py): the "Şablonlar" dialog built from the real
// index.html markup, the run store fed by `workflow.state`, Task Board columns for workflow steps, following step tasks
// in the agent panel, and hiding everything for connectors without the "workflows" feature. Fake hub, real DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WorkflowStore, WorkflowDialog, supportsWorkflows, runChanges, validRun, codeText, progressText }
  from '../../web/js/workflows.js';
import { categorizeTasks } from '../../web/js/board.js';
import { PromptPanel } from '../../web/js/prompt.js';

const XSS = '<img src=x onerror=window.__pwnedA10=1>';

function tplStatic() {
  return {
    id: 'statik-site', name: 'Statik site taslağı', description: XSS, folder: 'statik-site', builtin: true,
    limits: { maxCostUsd: 1, maxTurns: 60, timeoutS: 1800 }, needsBrief: true,
    steps: [
      { id: 'mimar', type: 'agent', role: 'Mimar', engine: 'claude', engines: ['claude', 'ollama'], profile: 'readonly', model: null },
      { id: 'gelistirici', type: 'agent', role: 'Geliştirici', engine: 'claude', engines: ['claude'], profile: 'edit', model: null },
      { id: 'inceleme', type: 'agent', role: 'İnceleme', engine: 'claude', engines: ['claude'], profile: 'readonly', model: null },
      { id: 'onay', type: 'approval', role: 'Taslak kabul edilsin mi?', timeoutS: 900 },
    ],
  };
}

function run(state = 'running', steps = null, extra = {}) {
  return {
    id: 'w_AAAAAAAAAA', template: 'statik-site', name: 'Statik site taslağı', state, code: null,
    workspace: { id: 'isler', name: 'Pixel Office İşleri', sub: 'statik-site' }, startedAt: Date.now(), endedAt: null,
    costUsd: 0.05, turns: 3, limits: { maxCostUsd: 1, maxTurns: 60, timeoutS: 1800 }, current: null,
    steps: steps || [
      { id: 'mimar', type: 'agent', role: 'Mimar', engine: 'claude', model: 'haiku', agent: 'k1', task: 't_MMMMMMMMMM', state: 'done' },
      { id: 'gelistirici', type: 'agent', role: 'Geliştirici', engine: 'claude', model: 'haiku', agent: 'k2', task: 't_GGGGGGGGGG', state: 'running' },
      { id: 'inceleme', type: 'agent', role: 'İnceleme', engine: 'claude', model: 'haiku', agent: 'k3', task: null, state: 'waiting' },
      { id: 'onay', type: 'approval', role: 'Taslak kabul edilsin mi?', state: 'waiting' },
    ],
    ...extra,
  };
}

function fakeHub({ features = ['handoff', 'workflows'], ollama = false, runs = [] } = {}) {
  const dev = { id: 'd1', label: 'Masaüstü', ready: true, status: 'ready',
    info: { features, remote: {}, limits: { maxPrompt: 32768 },
      engines: { claude: { available: true }, ollama: { available: ollama, models: ollama ? ['qwen2.5-coder:7b'] : [] } } },
    agents: new Map() };
  const listeners = new Map();
  const calls = [];
  const watches = [];
  return {
    calls, dev, watches, devices: new Map([[dev.id, dev]]),
    on(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
    emit(t, ...a) { for (const fn of listeners.get(t) || []) fn(...a); },
    isPaired: () => true, isReady: () => true, device: (id) => (id === dev.id ? dev : null),
    agentInfo: (c) => dev.agents.get(c.agent) || null,
    watch(d, key, on) { watches.push([key, on]); },
    async request(id, msg) {
      calls.push(msg);
      if (msg.type === 'workflow.list') return { type: 'workflow.list.result', templates: [tplStatic()], invalid: 0, runs };
      if (msg.type === 'workspace.list') return { workspaces: [{ id: 'pixel', name: 'Pixel', children: null }, { id: 'isler', name: XSS, children: [] }] };
      if (msg.type === 'workflow.start') return { type: 'workflow.started', run: run('running') };
      if (msg.type === 'usage.get') return { totals: { tokens: 0, costUsd: 0 }, budgets: [] };
      return { type: 'workflow.ok', run: msg.run };
    },
  };
}

async function dialogFromIndex() {
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const dlg = document.importNode(doc.getElementById('dlg-workflows'), true);
  document.body.appendChild(dlg);
  return dlg;
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function until(cond, ms = 2000) {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error('condition not reached');
    await new Promise((r) => setTimeout(r, 10));
  }
}

test('feature gate: only connectors that list "workflows" (and are connected)', () => {
  assert.equal(supportsWorkflows(fakeHub().dev), true);
  assert.equal(supportsWorkflows(fakeHub({ features: ['handoff'] }).dev), false);       // 3.3.0
  assert.equal(supportsWorkflows({ ready: true, info: {} }), false);                     // older: no features
  assert.equal(supportsWorkflows({ ...fakeHub().dev, ready: false }), false);
});

test('index.html has the toolbar button hidden by default and the dialog uses no inline handlers', async () => {
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const btn = doc.getElementById('btn-workflows');
  assert.equal(btn.hidden, true);
  assert.equal(btn.textContent, 'Şablonlar');
  const dlg = doc.getElementById('dlg-workflows');
  for (const e of dlg.querySelectorAll('*')) {
    for (const a of e.attributes) assert.ok(!/^on/i.test(a.name), `inline handler ${a.name}`);
  }
  assert.equal(dlg.querySelector('script'), null);
});

test('run shape check, change detection (tasks to follow, the A9 walk) and texts', () => {
  assert.equal(validRun(run()), true);
  assert.equal(validRun({ ...run(), id: '../x' }), false);
  assert.equal(validRun({ ...run(), state: 'hacked' }), false);
  assert.equal(validRun({ ...run(), steps: [] }), false);
  assert.equal(validRun({ ...run(), steps: new Array(9).fill(run().steps[0]) }), false);
  const first = run('running', run().steps.map((s, i) => (i === 0 ? { ...s, state: 'running', task: 't_MMMMMMMMMM' } : i === 1 ? { ...s, state: 'waiting', task: null } : s)));
  let ch = runChanges(null, first);
  assert.deepEqual(ch.started, [{ agent: 'k1', task: 't_MMMMMMMMMM', role: 'Mimar' }]);
  assert.equal(ch.walk, null);                                    // first sight: no walk
  ch = runChanges(first, run());
  assert.deepEqual(ch.started, [{ agent: 'k2', task: 't_GGGGGGGGGG', role: 'Geliştirici' }]);
  assert.deepEqual(ch.ended, [{ agent: 'k1', task: 't_MMMMMMMMMM', state: 'done' }]);
  assert.deepEqual(ch.walk, { from: 'k1', to: 'k2' });
  assert.deepEqual(runChanges(run(), run()), { started: [], ended: [], walk: null });
  assert.equal(codeText('cost_cap'), 'Maliyet tavanı aşıldı');
  assert.equal(codeText('approval_timeout'), 'Onay süresi doldu (reddedildi sayıldı)');
  assert.equal(codeText('busy'), 'Ajan meşgul');
  assert.equal(codeText('revoked'), 'Başlatan tarayıcının eşleşmesi kaldırıldı');   // S26-02
  assert.equal(progressText(run()), '1/4 adım · $0.05 / $1.00 · 3 tur');
});

test('store: workflow.state events, invalid runs ignored, onRun with the changes', async () => {
  const hub = fakeHub();
  const seen = [];
  const store = new WorkflowStore({ hub, onRun: (dev, ch, r) => seen.push([dev, ch, r.state]) });
  hub.emit('event', 'd1', { type: 'workflow.state', run: { ...run(), id: 'bad' } });
  assert.equal(store.runs.size, 0);
  hub.emit('event', 'd1', { type: 'workflow.state', run: run() });
  assert.equal(store.active().length, 1);
  assert.equal(seen.length, 1);
  hub.emit('event', 'd1', { type: 'workflow.state', run: run('done', run().steps.map((s) => ({ ...s, state: 'done' }))) });
  assert.equal(store.active().length, 0);
  assert.equal(store.list().length, 1);
  assert.deepEqual(seen[1][1].ended, [{ agent: 'k2', task: 't_GGGGGGGGGG', state: 'done' }]);
  hub.emit('change');                                             // a ready device with the feature → list fetched once
  await until(() => hub.calls.some((c) => c.type === 'workflow.list'));
  assert.equal(store.templates.get('d1').list[0].id, 'statik-site');
  hub.emit('change');
  await tick();
  assert.equal(hub.calls.filter((c) => c.type === 'workflow.list').length, 1);
});

test('dialog: templates and steps as text, engine choice, request, approve/deny/cancel', async () => {
  const hub = fakeHub();
  const store = new WorkflowStore({ hub });
  const dlg = await dialogFromIndex();
  const toasts = [];
  const d = new WorkflowDialog({ dialog: dlg, hub, store, toast: (t) => toasts.push(t) });
  d.open();
  await until(() => dlg.querySelectorAll('#wf-steps li').length === 4);
  assert.equal(dlg.open, true);
  assert.equal(dlg.querySelector('[name=device]').closest('label').hidden, true);          // one device
  assert.deepEqual([...dlg.querySelectorAll('[name=template] option')].map((o) => o.textContent), ['Statik site taslağı']);
  assert.equal(dlg.querySelector('#wf-desc').textContent, XSS);                             // text, never markup
  assert.equal(dlg.querySelector('[name=workspace]').value, 'isler');                       // ~/PixelOfficeIsler default
  assert.equal(dlg.querySelector('#wf-sub-row').hidden, false);
  assert.equal(dlg.querySelector('[name=sub]').value, 'statik-site');
  assert.equal(dlg.querySelector('[name=cost]').value, '1');
  assert.equal(dlg.querySelector('img'), null);
  assert.equal(window.__pwnedA10, undefined);
  // the architect offers Ollama, disabled while it does not run on the device; Claude is the default
  const eng = dlg.querySelector('[name=engine-mimar]');
  assert.equal(eng.hidden, false);
  assert.deepEqual([...eng.options].map((o) => [o.value, o.disabled]), [['claude', false], ['ollama', true]]);
  assert.equal(eng.value, 'claude');
  assert.equal(dlg.querySelector('[name=engine-gelistirici]').hidden, true);
  dlg.querySelector('[name=model-mimar]').value = 'haiku';
  dlg.querySelector('[name=model-gelistirici]').value = 'haiku';
  // validation in the page
  assert.equal(d.request().error, 'Ne istediğinizi kısaca yazın.');
  dlg.querySelector('[name=brief]').value = 'ş'.repeat(2049);
  assert.match(d.request().error, /çok uzun/);
  dlg.querySelector('[name=brief]').value = 'Kafe sitesi';
  dlg.querySelector('[name=cost]').value = '50';
  assert.match(d.request().error, /Maliyet tavanı/);
  // S26-04: the template's cap (1 $ here) is the ceiling, not the 20 $ protocol bound
  assert.equal(dlg.querySelector('[name=cost]').max, '1');
  dlg.querySelector('[name=cost]').value = '1.5';
  assert.equal(d.request().error, 'Maliyet tavanı 0.01–1 $ arasında olmalı (şablonun sınırı 1 $).');
  dlg.querySelector('[name=cost]').value = '1';
  assert.equal(d.request().msg.maxCostUsd, 1);
  dlg.querySelector('[name=cost]').value = '0.3';
  const { msg } = d.request();
  assert.deepEqual(msg, { type: 'workflow.start', template: 'statik-site', workspace: { id: 'isler', sub: 'statik-site' },
    brief: 'Kafe sitesi', maxCostUsd: 0.3,
    steps: { mimar: { engine: 'claude', model: 'haiku' }, gelistirici: { engine: 'claude', model: 'haiku' },
      inceleme: { engine: 'claude', model: null } } });
  dlg.querySelector('#wf-form').requestSubmit();
  await until(() => dlg.querySelectorAll('.wf-run').length === 1);
  assert.ok(hub.calls.some((c) => c.type === 'workflow.start'));
  assert.equal(dlg.querySelector('[name=start]').disabled, true);                          // one run at a time
  assert.match(dlg.querySelector('.wf-run').textContent, /Geliştirici/);
  // the checkpoint arrives: approve / deny / cancel buttons send the right messages
  const appr = run('approval', run().steps.map((s) => (s.type === 'agent' ? { ...s, state: 'done' } : { ...s, state: 'approval', expiresAt: Date.now() + 600000 })), { current: 'onay' });
  hub.emit('event', 'd1', { type: 'workflow.state', run: appr });
  await until(() => !!dlg.querySelector('.wf-approve'));
  assert.match(dlg.querySelector('.wf-run').textContent, /Onay bekliyor/);
  assert.match(dlg.querySelector('.wf-run').textContent, /10 dk sonra reddedilmiş sayılır/);
  dlg.querySelector('.wf-approve').click();
  await until(() => hub.calls.some((c) => c.type === 'workflow.answer'));
  assert.deepEqual(hub.calls.find((c) => c.type === 'workflow.answer'), { type: 'workflow.answer', decision: 'approve', run: 'w_AAAAAAAAAA' });
  await until(() => !!dlg.querySelector('.wf-deny') && !dlg.querySelector('.wf-deny').disabled);
  dlg.querySelector('.wf-deny').click();
  await until(() => hub.calls.filter((c) => c.type === 'workflow.answer').length === 2);
  assert.equal(hub.calls.filter((c) => c.type === 'workflow.answer')[1].decision, 'deny');
  await until(() => !dlg.querySelector('.wf-cancel').disabled);
  dlg.querySelector('.wf-cancel').click();
  await until(() => hub.calls.some((c) => c.type === 'workflow.cancel'));
  // the end: the reason is shown, no more buttons
  hub.emit('event', 'd1', { type: 'workflow.state', run: { ...appr, state: 'cancelled', code: 'denied', current: null,
    steps: appr.steps.map((s) => (s.type === 'approval' ? { ...s, state: 'cancelled', code: 'denied' } : s)) } });
  await until(() => !dlg.querySelector('.wf-cancel'));
  assert.match(dlg.querySelector('.wf-run').textContent, /Reddedildi/);
  assert.equal(dlg.querySelector('[name=start]').disabled, false);
  dlg.close();
  dlg.remove();
});

test('dialog: Ollama architect needs a model; no feature → the dialog does not open', async () => {
  const hub = fakeHub({ ollama: true });
  const store = new WorkflowStore({ hub });
  const dlg = await dialogFromIndex();
  const d = new WorkflowDialog({ dialog: dlg, hub, store, toast() {} });
  d.open();
  await until(() => dlg.querySelectorAll('#wf-steps li').length === 4);
  const eng = dlg.querySelector('[name=engine-mimar]');
  eng.value = 'ollama';
  eng.dispatchEvent(new Event('change'));
  assert.deepEqual([...dlg.querySelectorAll('[name=model-mimar] option')].map((o) => o.value), ['qwen2.5-coder:7b']);
  dlg.querySelector('[name=brief]').value = 'x';
  assert.deepEqual(d.request().msg.steps.mimar, { engine: 'ollama', model: 'qwen2.5-coder:7b' });
  dlg.close();
  dlg.remove();
  const old = fakeHub({ features: ['handoff'] });
  const dlg2 = await dialogFromIndex();
  const toasts = [];
  new WorkflowDialog({ dialog: dlg2, hub: old, store: new WorkflowStore({ hub: old }), toast: (t) => toasts.push(t) }).open();
  assert.equal(dlg2.open, false);
  assert.match(toasts[0], /3\.4\.0/);
  dlg2.remove();
});

test('Task Board: workflow steps in the right columns, paired only, no duplicate of a followed task', () => {
  const agents = [
    { id: 'o1', name: 'Mimar', state: 'idle', control: { device: 'd1', agent: 'k1' } },
    { id: 'o2', name: 'Geliştirici', state: 'working', control: { device: 'd1', agent: 'k2' } },
    { id: 'o3', name: 'İnceleme', state: 'idle', control: { device: 'd1', agent: 'k3' } },
  ];
  const wf = [{ device: 'd1', run: run() }];
  const b = categorizeTasks({ agents, views: new Map(), isPaired: true, workflows: wf });
  assert.deepEqual(b.queued.map((c) => c.agentName), ['İnceleme', 'Siz']);
  assert.deepEqual(b.working.map((c) => [c.agentName, c.title]), [['Geliştirici', 'İş akışı: çalışıyor']]);
  assert.deepEqual(b.done.map((c) => [c.agentName, c.title]), [['Mimar', 'İş akışı: bitti']]);
  assert.equal(b.queued[0].detail, 'Statik site taslağı · 3/4: İnceleme');
  // the running step's task is followed by the agent panel → one card (the view's), not two
  const views = new Map([['d1.k2', { key: 'k2', running: true, task: 't_GGGGGGGGGG', state: 'running', recent: '' }]]);
  const b2 = categorizeTasks({ agents, views, isPaired: true, workflows: wf });
  assert.equal(b2.working.length, 1);
  assert.equal(b2.working[0].id, 'task-t_GGGGGGGGGG');
  // the checkpoint is an approval card
  const appr = run('approval', run().steps.map((s) => (s.type === 'agent' ? { ...s, state: 'done' } : { ...s, state: 'approval' })));
  const b3 = categorizeTasks({ agents, views: new Map(), isPaired: true, workflows: [{ device: 'd1', run: appr }] });
  assert.deepEqual(b3.waiting.map((c) => [c.agentName, c.isApproval]), [['Siz', true]]);
  // unpaired / viewer: no workflow card, no run name anywhere
  const pub = categorizeTasks({ agents, views: null, isPaired: false, workflows: wf });
  assert.ok(!JSON.stringify(pub).includes('Statik site'));
  assert.equal(pub.counts.working + pub.counts.done + pub.counts.queued + pub.counts.waiting, 3);
});

test('agent panel follows a step task (view + watch) and settles it from the run state', () => {
  const hub = fakeHub();
  hub.dev.agents.set('ka', { key: 'ka', name: 'Ana', engine: 'ollama', task: null });
  const slot = document.createElement('div');
  document.body.appendChild(slot);
  const activity = [];
  const p = new PromptPanel({ hub, slot, toast() {}, onPair() {}, onDevices() {}, onActivity: (r) => activity.push(r) });
  p.show({ id: 'office-a', control: { device: 'd1', agent: 'ka' } });
  const v = p.trackTask('d1', 'k2', 't_GGGGGGGGGG', 'İş akışı: Statik site taslağı · Geliştirici');
  assert.equal(v.state, 'running');
  assert.ok(v.blocks.get('t_GGGGGGGGGG').el.textContent.includes('İş akışı: Statik site taslağı · Geliştirici'));
  assert.deepEqual(hub.watches.at(-1), ['k2', true]);
  assert.equal(activity.at(-1), true);
  p.settleTask('d1', 'k2', 't_OTHER00000');                     // another task: nothing changes
  assert.equal(v.state, 'running');
  p.settleTask('d1', 'k2', 't_GGGGGGGGGG');
  assert.equal(v.state, 'idle');
  assert.deepEqual(hub.watches.at(-1), ['k2', false]);
  clearInterval(p._tick);
  slot.remove();
});
