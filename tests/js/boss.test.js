// Patron Odası P6 (headless Chrome, tests/test_relay_js.py): the Boss's panel built from the real index.html markup
// (brief, folder, cost cap, plan card, progress with the Test-order approval, report, İptal, "Hafızayı sıfırla"), the
// store fed by untrusted `boss.state` / `boss.plan` / `boss.report` and `boss.get`, the "Plan onayı · Siz" card on the
// Task Board, the Boss hit-test and the viewer / unpaired info text. Fake hub, real DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BossStore, BossPanel, supportsBoss, costCeiling, parseCost, readState, readPlan, readReport, codeText,
  BRIEF_MAX, COST_MIN, COST_DEFAULT, INFO_TEXT } from '../../web/js/boss.js';
import { categorizeTasks, TaskBoard } from '../../web/js/board.js';
import { Office, BOSS_SEAT, BOSS_DESK, TILE } from '../../web/js/office.js';
import { Renderer } from '../../web/js/renderer.js';

const XSS = '<img src=x onerror=window.__pwnedP6=1>';
const JOB = 'b_AAAAAAAAAA';
const RUN = 'w_RRRRRRRRRR';

function state(st = 'approval', extra = {}) {
  return { type: 'boss.state', job: JOB, state: st, code: null, round: 0, orders: 2, costUsd: 0.04, maxCostUsd: 0.8,
    estimateUsd: 0.9, run: null, orderStates: [], workspace: { id: 'pixel', sub: null }, startedAt: Date.now(),
    endedAt: null, expiresAt: Date.now() + 14 * 60000, ...extra };
}

function planMsg(extra = {}) {
  return { type: 'boss.plan', job: JOB, round: 0, estimateUsd: 0.9, maxCostUsd: 0.8, expiresAt: Date.now() + 14 * 60000,
    roles: { docs: { name: 'Belge', badge: 'pencil', profile: 'edit' }, test: { name: XSS, badge: 'checklist', profile: 'dev' } },
    plan: { v: 1, summary: `Özet ${XSS}`, orders: [
      { id: 'o1', role: 'docs', title: `Başlık ${XSS}`, goal: `Hedef ${XSS}`, accept: [`Ölçüt ${XSS}`], files: ['docs/a.md'], after: [] },
      { id: 'o2', role: 'test', title: 'Test yaz', goal: 'Bir test', accept: ['Geçer'], files: [], after: ['o1'] },
    ] }, ...extra };
}

function reportMsg(extra = {}) {
  return { type: 'boss.report', job: JOB, report: `Patronun raporu:\n${XSS}\nSatır 2`, partial: false, state: 'done',
    code: null, round: 0, costUsd: 0.3, maxCostUsd: 0.8, notes: [], orders: [], ...extra };
}

function fakeHub({ features = ['handoff', 'workflows', 'boss'], boss = { maxCostUsd: 0.8 }, ready = true, get = null,
  paired = true } = {}) {
  const dev = { id: 'd1', label: 'Masaüstü', ready, status: ready ? 'ready' : 'offline',
    info: { features, boss, engines: { claude: { available: true } } }, agents: new Map() };
  const listeners = new Map();
  const calls = [];
  const hub = {
    calls, dev, devices: paired ? new Map([[dev.id, dev]]) : new Map(),
    get pairedCount() { return this.devices.size; },
    hold: null,                         // a request type answered only when release() is called
    replies: {},                        // type → reply object or Error to reject with
    on(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
    emit(t, ...a) { for (const fn of listeners.get(t) || []) fn(...a); },
    device: (id) => hub.devices.get(id) || null,
    isReady: (id) => !!(hub.devices.get(id) && hub.devices.get(id).ready),
    async request(id, msg) {
      calls.push(msg);
      if (hub.hold === msg.type) await new Promise((r) => { hub.release = r; });
      const rep = hub.replies[msg.type];
      if (rep instanceof Error) throw rep;
      if (rep) return rep;
      if (msg.type === 'boss.get') return get || { type: 'boss.get.result', job: null, plan: null, report: null };
      if (msg.type === 'workspace.list') return { workspaces: [{ id: 'pixel', name: XSS, children: ['sub'] }, { id: 'isler', name: 'İşler', children: null }] };
      if (msg.type === 'boss.ask') return { type: 'boss.ack', job: state('planning', { orders: 0, estimateUsd: null, expiresAt: null }) };
      if (msg.type === 'boss.reset') return { type: 'boss.ok', job: null, reset: true };
      return { type: 'boss.ok', job: msg.job || null };
    },
  };
  return hub;
}

class CodeError extends Error {
  constructor(code) { super(code); this.code = code; }
}

async function dialogFromIndex() {
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const dlg = document.importNode(doc.getElementById('dlg-boss'), true);
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
const visible = (e) => !!e && !e.hidden && !e.closest('[hidden]');

async function setup(opts = {}) {
  const hub = fakeHub(opts);
  const store = new BossStore({ hub });
  const dlg = await dialogFromIndex();
  const toasts = [];
  const panel = new BossPanel({ dialog: dlg, hub, store, toast: (t, o) => toasts.push([t, o]) });
  return { hub, store, dlg, panel, toasts, done: () => { panel.close(); dlg.remove(); } };
}

// ---- gates and validation ---------------------------------------------------------------------------------------
test('feature gate: a ready device whose connector lists "boss"; the cap ceiling only from a valid device.info.boss', () => {
  assert.equal(supportsBoss(fakeHub().dev), true);
  assert.equal(supportsBoss(fakeHub({ features: ['workflows'] }).dev), false);
  assert.equal(supportsBoss(fakeHub({ ready: false }).dev), false);
  assert.equal(supportsBoss({ ready: true, info: {} }), false);
  assert.equal(supportsBoss(null), false);
  assert.equal(costCeiling(fakeHub().dev), 0.8);
  assert.equal(costCeiling(fakeHub({ boss: { maxCostUsd: 3 } }).dev), 3);
  assert.equal(costCeiling({ ready: true, info: { features: ['boss'] } }), COST_DEFAULT);   // older device.info: no `boss`
  for (const bad of [null, {}, { maxCostUsd: 5 }, { maxCostUsd: 0.01 }, { maxCostUsd: '2' }, { maxCostUsd: NaN }]) {
    assert.equal(costCeiling(fakeHub({ boss: bad }).dev), COST_DEFAULT, JSON.stringify(bad));
  }
  assert.equal(COST_DEFAULT, 1);
  assert.equal(COST_MIN, 0.1);
  assert.equal(BRIEF_MAX, 4096);
});

test('the cost cap can only lower the ceiling: above it, below 0.10 or not a number is refused, never clamped', () => {
  assert.deepEqual(parseCost('0.5', 0.8), { value: 0.5 });
  assert.deepEqual(parseCost('0,25', 0.8), { value: 0.25 });
  assert.deepEqual(parseCost('0.8', 0.8), { value: 0.8 });
  assert.deepEqual(parseCost('0.10', 1), { value: 0.1 });
  for (const bad of ['0.81', '3', '0.09', '0', '-1', '', 'abc', '1e9', 'NaN', 'Infinity']) {
    const r = parseCost(bad, 0.8);
    assert.equal(r.value, undefined, bad);
    assert.match(r.error, /Maliyet tavanı/, bad);
  }
});

test('boss.state is read through an allow-list: malformed messages are dropped, unknown fields never kept', () => {
  const s = readState(state('running', { run: RUN, orderStates: [{ id: 'o1', role: 'docs', state: 'running', code: null, agent: 'k1', task: 't1' }], extra: XSS }));
  assert.equal(s.job, JOB);
  assert.equal(s.state, 'running');
  assert.equal(s.run, RUN);
  assert.equal(s.orderStates.length, 1);
  assert.equal(s.extra, undefined);
  for (const bad of [null, 'x', {}, { ...state(), job: '../x' }, { ...state(), state: 'hacked' }, { ...state(), costUsd: 'free' },
    { ...state(), run: 'not-a-run' }, { ...state(), orderStates: 'x' }, { ...state(), round: 7 }]) {
    assert.equal(readState(bad), null, JSON.stringify(bad));
  }
  // bad order rows are dropped, not drawn
  const rows = readState(state('running', { orderStates: [{ id: 'o1', role: 'docs', state: 'done' }, { id: '<b>', state: 'done' },
    { id: 'o2', role: 'test', state: 'evil' }, null] })).orderStates;
  assert.deepEqual(rows.map((o) => o.id), ['o1']);
});

test('boss.plan and boss.report: strings kept as data, wrong shapes refused', () => {
  const p = readPlan(planMsg());
  assert.equal(p.job, JOB);
  assert.equal(p.orders.length, 2);
  assert.equal(p.orders[0].title, `Başlık ${XSS}`);
  assert.deepEqual(p.orders[1].after, ['o1']);
  assert.equal(p.roles.test.name, XSS);
  for (const bad of [null, { ...planMsg(), job: 'x' }, { ...planMsg(), plan: null }, { ...planMsg(), plan: { orders: [] } },
    { ...planMsg(), plan: { summary: 's', orders: [{ id: 'o1', role: 'docs', title: 5, goal: 'g', accept: [] }] } },
    { ...planMsg(), plan: { summary: 's', orders: new Array(9).fill(planMsg().plan.orders[0]) } }]) {
    assert.equal(readPlan(bad), null, JSON.stringify(bad && bad.plan));
  }
  const r = readReport(reportMsg());
  assert.equal(r.report, `Patronun raporu:\n${XSS}\nSatır 2`);
  assert.equal(r.partial, false);
  assert.equal(readReport({ ...reportMsg(), report: 5 }), null);
  assert.equal(readReport({ ...reportMsg(), job: 'nope' }), null);
  assert.equal(readReport({ ...reportMsg(), report: 'x'.repeat(40000) }).report.length <= 16384, true);
  assert.equal(codeText('cost_cap'), 'Maliyet tavanı doldu');
  assert.equal(codeText('weird_code'), 'İşlem başarısız oldu');           // never the raw code
});

// ---- the store ----------------------------------------------------------------------------------------------------
test('store: boss.get once per ready connection, then events; replies (re), unknown and malformed messages ignored', async () => {
  const hub = fakeHub({ get: { type: 'boss.get.result', job: state(), plan: planMsg(), report: null } });
  const store = new BossStore({ hub });
  let changes = 0;
  store.onChange(() => { changes += 1; });
  await until(() => store.entry('d1'));
  assert.equal(hub.calls.filter((m) => m.type === 'boss.get').length, 1);
  hub.emit('change');
  await tick();
  assert.equal(hub.calls.filter((m) => m.type === 'boss.get').length, 1);     // not on every change
  assert.equal(store.entry('d1').job.state, 'approval');
  assert.equal(store.entry('d1').plan.orders.length, 2);
  assert.equal(store.pendingPlans().length, 1);
  const before = changes;
  hub.emit('event', 'd1', { ...state('running'), re: 'r1' });               // a reply, not an event
  hub.emit('event', 'd1', { type: 'boss.state', job: 'bad', state: 'running' });
  hub.emit('event', 'd1', { type: 'boss.unknown', job: JOB });
  hub.emit('event', 'd1', null);
  assert.equal(store.entry('d1').job.state, 'approval');
  assert.equal(changes, before);
  hub.emit('event', 'd1', state('running', { run: RUN }));
  assert.equal(store.entry('d1').job.state, 'running');
  assert.equal(store.pendingPlans().length, 0);
  hub.emit('event', 'd1', state('done'));
  hub.emit('event', 'd1', state('running'));                                   // late, out of order: a finished job stays finished
  assert.equal(store.entry('d1').job.state, 'done');
  hub.emit('event', 'd1', reportMsg());
  assert.equal(store.entry('d1').report.report.includes('Satır 2'), true);
  // a new job replaces the old one (plan and report of the old job are gone)
  hub.emit('event', 'd1', state('planning', { job: 'b_BBBBBBBBBB' }));
  assert.equal(store.entry('d1').job.job, 'b_BBBBBBBBBB');
  assert.equal(store.entry('d1').plan, null);
  assert.equal(store.entry('d1').report, null);
  // reconnect (resync): asked again; device gone: forgotten
  hub.emit('resync', 'd1');
  await tick();
  assert.equal(hub.calls.filter((m) => m.type === 'boss.get').length, 2);
  hub.dev.ready = false;
  hub.emit('change');
  assert.equal(store.entry('d1'), null);
});

test('store: no boss.get for a connector without the feature', async () => {
  const hub = fakeHub({ features: ['workflows'] });
  const store = new BossStore({ hub });
  await tick();
  assert.equal(hub.calls.length, 0);
  hub.emit('event', 'd1', state());
  assert.equal(store.entry('d1'), null);                                       // events of such a device are not kept either
});

// ---- the panel: info texts -------------------------------------------------------------------------------------------
test('viewer (no hub): only the info text, no form, no plan, no actions', async () => {
  const dlg = await dialogFromIndex();
  try {
    const panel = new BossPanel({ dialog: dlg, hub: null, viewer: true });
    panel.open();
    assert.equal(dlg.open, true);
    assert.equal(visible(dlg.querySelector('#boss-info')), true);
    assert.equal(dlg.querySelector('#boss-info').textContent, INFO_TEXT.viewer);
    assert.equal(visible(dlg.querySelector('#boss-form')), false);
    assert.equal(visible(dlg.querySelector('#boss-job')), false);
    assert.equal([...dlg.querySelectorAll('button')].filter((b) => visible(b) && !b.classList.contains('close')).length, 0);
    panel.close();
    const p2 = new BossPanel({ dialog: dlg, hub: null });
    p2.open();
    assert.equal(dlg.querySelector('#boss-info').textContent, INFO_TEXT.unpaired);
    p2.close();
  } finally { dlg.remove(); }
});

test('unpaired browser and a paired device without the feature: info text, no form', async () => {
  let s = await setup({ paired: false });
  try {
    s.panel.open();
    assert.equal(s.dlg.querySelector('#boss-info').textContent, INFO_TEXT.unpaired);
    assert.equal(visible(s.dlg.querySelector('#boss-form')), false);
  } finally { s.done(); }
  s = await setup({ features: ['workflows'] });
  try {
    s.panel.open();
    assert.equal(s.dlg.querySelector('#boss-info').textContent, INFO_TEXT.offline);
    assert.equal(visible(s.dlg.querySelector('#boss-form')), false);
    assert.equal(s.hub.calls.filter((m) => m.type.startsWith('boss.')).length, 0);
  } finally { s.done(); }
});

// ---- asking the Boss ------------------------------------------------------------------------------------------------
test('ask: folder from workspace.list (names as text), cap field limited to the device ceiling, boss.ask fields', async () => {
  const s = await setup();
  try {
    s.panel.open();
    const f = s.dlg.querySelector('#boss-form').elements;
    await until(() => f.workspace.options.length === 2);
    assert.equal(visible(s.dlg.querySelector('#boss-form')), true);
    assert.equal(f.workspace.options[0].textContent, XSS);
    assert.equal(s.dlg.querySelector('img'), null);
    assert.equal(f.cost.value, '0.80');
    assert.equal(f.cost.max, '0.8');
    assert.match(s.dlg.querySelector('#boss-cap-hint').textContent, /0\.80 \$/);
    assert.equal(f.workspace.value, 'isler');                                  // ~/PixelOfficeIsler first, as in Şablonlar
    assert.equal(visible(s.dlg.querySelector('#boss-sub-row')), false);         // no children
    f.workspace.value = 'pixel';
    f.workspace.dispatchEvent(new Event('change'));
    assert.equal(visible(s.dlg.querySelector('#boss-sub-row')), true);          // 'pixel' has children

    const err = s.dlg.querySelector('#boss-error');
    const asks = () => s.hub.calls.filter((m) => m.type === 'boss.ask');
    f.brief.value = '   ';
    s.panel.ask();
    await tick();
    assert.equal(asks().length, 0);
    assert.match(err.textContent, /Patrona ne yaptırmak/);
    f.brief.value = 'ş'.repeat(BRIEF_MAX / 2 + 1);                             // 4098 UTF-8 bytes
    await s.panel.ask();
    assert.equal(asks().length, 0);
    assert.match(err.textContent, /4 KB/);
    f.brief.value = `Koyu tema ekle ${XSS}`;
    f.cost.value = '0.81';                                                     // above the device ceiling
    await s.panel.ask();
    assert.equal(asks().length, 0);
    assert.match(err.textContent, /Maliyet tavanı/);
    f.cost.value = '0.5';                                                      // ("0,5": parseCost unit test; a number input refuses it)
    f.sub.value = 'sub';
    await s.panel.ask();
    assert.equal(asks().length, 1);
    assert.deepEqual(asks()[0], { type: 'boss.ask', workspace: { id: 'pixel', sub: 'sub' }, brief: `Koyu tema ekle ${XSS}`, maxCostUsd: 0.5 });
    assert.equal(err.hidden, true);
    assert.equal(s.store.entry('d1').job.state, 'planning');                  // the ack is the first state
    assert.equal(visible(s.dlg.querySelector('#boss-form')), false);           // one job at a time
    assert.match(s.dlg.querySelector('#boss-job').textContent, /Planlıyor/);
  } finally { s.done(); }
});

test('ask: the button is disabled while the request is in flight; error codes become Turkish messages', async () => {
  const s = await setup();
  try {
    s.panel.open();
    const f = s.dlg.querySelector('#boss-form').elements;
    await until(() => f.workspace.options.length === 2);
    f.brief.value = 'Bir iş';
    s.hub.hold = 'boss.ask';
    s.hub.replies['boss.ask'] = new CodeError('busy');
    const p = s.panel.ask();
    await until(() => !!s.hub.release);
    assert.equal(f.ask.disabled, true);
    s.panel.ask();                                                             // a second click while in flight: nothing
    assert.equal(s.hub.calls.filter((m) => m.type === 'boss.ask').length, 1);
    s.hub.release();
    await p;
    assert.equal(f.ask.disabled, false);
    assert.match(s.dlg.querySelector('#boss-error').textContent, /iş akışının işi sürüyor/);
    s.hub.hold = null;
    for (const [code, re] of [['not_allowed', /uzaktan ajan oluşturmaya/], ['workspace_denied', /izin listesinde/],
      ['engine_unavailable', /Claude Code/], ['too_large', /4 KB/], ['bad_request', /geçersiz/],
      ['rate_limited', /Çok hızlı/], ['something_new', /başarısız/]]) {
      s.hub.replies['boss.ask'] = new CodeError(code);
      await s.panel.ask();
      assert.match(s.dlg.querySelector('#boss-error').textContent, re, code);
    }
  } finally { s.done(); }
});

// ---- plan card, progress, report --------------------------------------------------------------------------------------
test('plan card: orders, roles, estimate vs cap as plain text (XSS canary), Onayla/Reddet with in-flight lock', async () => {
  const s = await setup();
  try {
    s.hub.emit('event', 'd1', planMsg());
    s.hub.emit('event', 'd1', state());
    s.panel.open();
    const job = s.dlg.querySelector('#boss-job');
    assert.equal(visible(job), true);
    const card = job.querySelector('.boss-plan');
    assert.ok(card);
    assert.ok(card.textContent.includes(`Özet ${XSS}`));
    assert.ok(card.textContent.includes(`Başlık ${XSS}`));
    assert.ok(card.textContent.includes(`Ölçüt ${XSS}`));
    assert.ok(card.textContent.includes('docs/a.md'));
    assert.ok(card.textContent.includes(XSS));                                 // the role name, too
    assert.match(card.textContent, /Tahmini maliyet: \$0\.90/);
    assert.match(card.textContent, /Kesin tavan: \$0\.80/);
    assert.match(card.textContent, /o1 bitince/);
    assert.equal(s.dlg.querySelector('img'), null);
    assert.equal(window.__pwnedP6, undefined);
    assert.equal(visible(s.dlg.querySelector('#boss-form')), false);

    s.hub.hold = 'boss.approve';
    job.querySelector('.boss-approve').click();
    await until(() => !!s.hub.release);
    assert.equal(job.querySelector('.boss-approve').disabled, true);
    assert.equal(job.querySelector('.boss-deny').disabled, true);
    job.querySelector('.boss-approve').click();
    assert.equal(s.hub.calls.filter((m) => m.type === 'boss.approve').length, 1);
    s.hub.release();
    await until(() => !job.querySelector('.boss-approve').disabled);
    assert.deepEqual(s.hub.calls.find((m) => m.type === 'boss.approve'), { type: 'boss.approve', job: JOB, approve: true });
    s.hub.hold = null;
    s.hub.replies['boss.approve'] = new CodeError('unknown_approval');
    job.querySelector('.boss-deny').click();
    await until(() => s.toasts.length >= 2);
    assert.deepEqual(s.hub.calls.filter((m) => m.type === 'boss.approve')[1], { type: 'boss.approve', job: JOB, approve: false });
    assert.match(s.toasts.at(-1)[0], /başka yerden cevaplandı/);
    await until(() => !!job.querySelector('.boss-act-error'));               // also inline: toasts sit under the modal
    assert.match(job.querySelector('.boss-act-error').textContent, /başka yerden cevaplandı/);
  } finally { s.done(); }
});

test('progress: order states with plan titles, the Test order approval (workflow.answer with step), İptal', async () => {
  const s = await setup();
  try {
    s.hub.emit('event', 'd1', planMsg());
    s.hub.emit('event', 'd1', state('running', { run: RUN, costUsd: 0.12, orderStates: [
      { id: 'o1', role: 'docs', state: 'done', code: null, agent: 'k1', task: 't1' },
      { id: 'o2', role: 'test', state: 'approval', code: null, agent: 'k2', task: null }] }));
    s.panel.open();
    const job = s.dlg.querySelector('#boss-job');
    const rows = [...job.querySelectorAll('.boss-order')];
    assert.equal(rows.length, 2);
    assert.ok(rows[0].textContent.includes(`Başlık ${XSS}`));
    assert.match(rows[0].textContent, /Bitti/);
    assert.match(rows[1].textContent, /Onay bekliyor/);
    assert.match(job.textContent, /\$0\.12 \/ \$0\.80/);
    assert.equal(s.dlg.querySelector('img'), null);
    const yes = job.querySelector('.boss-gate-approve');
    assert.ok(yes);
    yes.click();
    await until(() => s.hub.calls.some((m) => m.type === 'workflow.answer'));
    assert.deepEqual(s.hub.calls.find((m) => m.type === 'workflow.answer'), { type: 'workflow.answer', run: RUN, decision: 'approve', step: 'o2' });
    await until(() => !job.querySelector('.boss-gate-deny').disabled);
    job.querySelector('.boss-gate-deny').click();
    await until(() => s.hub.calls.filter((m) => m.type === 'workflow.answer').length === 2);
    assert.equal(s.hub.calls.filter((m) => m.type === 'workflow.answer')[1].decision, 'deny');
    await until(() => !job.querySelector('.boss-cancel').disabled);
    job.querySelector('.boss-cancel').click();
    await until(() => s.hub.calls.some((m) => m.type === 'boss.cancel'));
    assert.deepEqual(s.hub.calls.find((m) => m.type === 'boss.cancel'), { type: 'boss.cancel', job: JOB });
  } finally { s.done(); }
});

test('retry: "Yeniden dene" only on a failed order of a job that ended in error, only with the bossRetry feature', async () => {
  const failed = state('error', { code: 'engine_error', run: RUN, endedAt: Date.now(), orderStates: [
    { id: 'o1', role: 'docs', state: 'done', code: null, agent: 'k1', task: 't1' },
    { id: 'o2', role: 'test', state: 'error', code: 'timeout', agent: 'k2', task: 't2' },
    { id: 'patron', role: 'patron', state: 'error', code: 'engine_error', agent: null, task: null }] });
  let s = await setup({ features: ['handoff', 'workflows', 'boss'] });
  try {
    s.hub.emit('event', 'd1', planMsg());
    s.hub.emit('event', 'd1', failed);
    s.panel.open();
    assert.equal(s.dlg.querySelector('.boss-retry'), null);           // an older connector: no button
  } finally { s.done(); }
  s = await setup({ features: ['handoff', 'workflows', 'boss', 'bossRetry'] });
  try {
    s.hub.emit('event', 'd1', planMsg());
    s.hub.emit('event', 'd1', state('running', { run: RUN, orderStates: [
      { id: 'o2', role: 'test', state: 'error', code: 'timeout', agent: 'k2', task: 't2' }] }));
    s.panel.open();
    assert.equal(s.dlg.querySelector('.boss-retry'), null);           // the job is still active
    s.hub.emit('event', 'd1', failed);
    await until(() => s.dlg.querySelectorAll('.boss-retry').length === 1);
    const btn = s.dlg.querySelector('.boss-retry');
    assert.ok(btn.closest('.boss-order').classList.contains('st-error'));
    assert.ok(btn.closest('.boss-order').textContent.includes('Test yaz'));   // o2, never the Boss's own review
    s.hub.replies['boss.retry_step'] = new CodeError('retry_used');
    btn.click();
    await until(() => s.hub.calls.some((m) => m.type === 'boss.retry_step'));
    assert.deepEqual(s.hub.calls.find((m) => m.type === 'boss.retry_step'), { type: 'boss.retry_step', job: JOB, order: 'o2' });
    await until(() => s.toasts.some(([, o]) => o && o.error));
    assert.match(s.toasts.find(([, o]) => o && o.error)[0], /bir kez/);
  } finally { s.done(); }
});

test('report: plain text with its line breaks, partial note, the form comes back for a new job', async () => {
  const s = await setup();
  try {
    s.hub.emit('event', 'd1', state('error', { code: 'cost_cap', endedAt: Date.now() }));
    s.hub.emit('event', 'd1', reportMsg({ partial: true, state: 'error', code: 'cost_cap' }));
    s.panel.open();
    const job = s.dlg.querySelector('#boss-job');
    const rep = job.querySelector('.boss-report');
    assert.ok(rep);
    assert.equal(rep.textContent, `Patronun raporu:\n${XSS}\nSatır 2`);
    assert.equal(rep.children.length, 0);
    assert.match(job.textContent, /Maliyet tavanı doldu/);
    assert.match(job.textContent, /yarım/i);
    assert.equal(job.querySelector('.boss-cancel'), null);
    assert.equal(visible(s.dlg.querySelector('#boss-form')), true);
    assert.equal(s.dlg.querySelector('img'), null);
  } finally { s.done(); }
});

test('after a reload the panel is rebuilt from boss.get alone', async () => {
  const s = await setup({ get: { type: 'boss.get.result', job: state(), plan: planMsg(), report: null } });
  try {
    await until(() => s.store.entry('d1'));
    s.panel.open();
    assert.ok(s.dlg.querySelector('#boss-job .boss-plan .boss-approve'));
  } finally { s.done(); }
});

test('Hafızayı sıfırla asks first; Vazgeç sends nothing, Evet sends boss.reset for the folder; busy is explained', async () => {
  const s = await setup();
  try {
    s.panel.open();
    const f = s.dlg.querySelector('#boss-form').elements;
    await until(() => f.workspace.options.length === 2);
    const box = s.dlg.querySelector('#boss-reset-confirm');
    assert.equal(visible(box), false);
    s.dlg.querySelector('[data-boss-reset]').click();
    assert.equal(visible(box), true);
    box.querySelector('[data-boss-reset-no]').click();
    assert.equal(visible(box), false);
    assert.equal(s.hub.calls.filter((m) => m.type === 'boss.reset').length, 0);
    f.workspace.value = 'isler';
    f.workspace.dispatchEvent(new Event('change'));
    s.dlg.querySelector('[data-boss-reset]').click();
    box.querySelector('[data-boss-reset-yes]').click();
    await until(() => s.toasts.length === 1);
    assert.deepEqual(s.hub.calls.find((m) => m.type === 'boss.reset'), { type: 'boss.reset', workspace: { id: 'isler', sub: null } });
    assert.match(s.toasts[0][0], /sıfırlandı/);
    assert.match(s.dlg.querySelector('#boss-note').textContent, /sıfırlandı/);
    assert.equal(visible(s.dlg.querySelector('#boss-note')), true);
    s.hub.replies['boss.reset'] = new CodeError('busy');
    s.dlg.querySelector('[data-boss-reset]').click();
    box.querySelector('[data-boss-reset-yes]').click();
    await until(() => !s.dlg.querySelector('#boss-error').hidden);
    assert.match(s.dlg.querySelector('#boss-error').textContent, /çalışırken/);
  } finally { s.done(); }
});

// ---- Task Board ---------------------------------------------------------------------------------------------------
test('Task Board: "Plan onayı · Siz" card only for a paired browser while a plan waits; it opens the panel', async () => {
  const hub = fakeHub();
  const store = new BossStore({ hub });
  hub.emit('event', 'd1', planMsg());
  hub.emit('event', 'd1', state());
  const plans = store.pendingPlans();
  let data = categorizeTasks({ agents: [], isPaired: true, bossPlans: plans });
  const card = data.waiting.find((c) => c.target === 'boss');
  assert.ok(card);
  assert.equal(card.agentName, 'Siz');
  assert.equal(card.title, 'Plan onayı');
  assert.equal(card.isApproval, true);
  assert.match(card.detail, /2 iş emri/);
  assert.ok(!card.detail.includes(XSS));                                       // numbers only on the board
  data = categorizeTasks({ agents: [], isPaired: false, bossPlans: plans });
  assert.equal(data.waiting.length, 0);
  hub.emit('event', 'd1', state('running', { run: RUN }));
  assert.equal(categorizeTasks({ agents: [], isPaired: true, bossPlans: store.pendingPlans() }).waiting.length, 0);

  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const dlg = document.importNode(doc.getElementById('dlg-board'), true);
  document.body.appendChild(dlg);
  try {
    const opened = [];
    const board = new TaskBoard({ dialog: dlg, onSelectAgent: () => {}, onOpenTarget: (t) => opened.push(t) });
    board.open();
    board.render(categorizeTasks({ agents: [], isPaired: true, bossPlans: [{ device: 'd1', job: readState(state()), plan: readPlan(planMsg()) }] }));
    const el = [...dlg.querySelectorAll('.board-card')].find((c) => /Plan onayı/.test(c.textContent));
    assert.ok(el);
    assert.match(el.textContent, /Siz/);
    el.click();
    assert.deepEqual(opened, ['boss']);
    assert.equal(board.isOpen, false);
  } finally { dlg.remove(); }
});

// ---- canvas hit-test and markup ------------------------------------------------------------------------------------
test('pickBoss: the Boss and his desk, nothing else', () => {
  const office = new Office();
  const canvas = document.createElement('canvas');
  document.body.appendChild(canvas);
  try {
    const r = new Renderer(canvas, office);
    canvas.style.width = `${r.worldW}px`;
    canvas.style.height = `${r.worldH}px`;
    assert.equal(r.pickBoss(BOSS_SEAT.x * TILE + 8, BOSS_SEAT.y * TILE + 8), true);           // his face
    assert.equal(r.pickBoss(BOSS_DESK.x * TILE + 2, BOSS_DESK.y * TILE + 10), true);           // his desk
    assert.equal(r.pickBoss(BOSS_DESK.x * TILE + 47, BOSS_DESK.y * TILE + 15), true);
    assert.equal(r.pickBoss(BOSS_DESK.x * TILE + 8, (BOSS_DESK.y + 2) * TILE), false);         // the drop tiles' row below
    assert.equal(r.pickBoss(2, 2), false);
    assert.equal(r.pickBoss(BOSS_DESK.x * TILE - 4, BOSS_DESK.y * TILE + 8), false);
  } finally { canvas.remove(); }
});

test('index.html: the Boss dialog has no inline handlers or scripts, the form starts hidden, the info text shown', async () => {
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const dlg = doc.getElementById('dlg-boss');
  assert.ok(dlg);
  for (const e of dlg.querySelectorAll('*')) {
    for (const a of e.attributes) assert.ok(!/^on/i.test(a.name), `inline handler ${a.name}`);
  }
  assert.equal(dlg.querySelector('script'), null);
  assert.equal(dlg.querySelector('#boss-form').hidden, true);
  assert.equal(dlg.querySelector('#boss-job').hidden, true);
  assert.equal(dlg.querySelector('#boss-form [name=ask]').textContent, 'Patrona ver');
  assert.equal(dlg.querySelector('[data-boss-reset]').textContent, 'Hafızayı sıfırla');
});
