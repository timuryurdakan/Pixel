// E1b (Faz 2): Boss department selector UI (web/js/boss.js + web/index.html) and
// workflow finished-step preview via `task.get` -> `task.get.result` (web/js/workflows.js).

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  BossStore, BossPanel, readState,
  BOSS_DEPARTMENTS_FEATURE, DEFAULT_DEPARTMENT, DEPT_RE, DEPARTMENT_HINTS,
  supportsBossDepartments, readDepartments,
} from '../../web/js/boss.js';
import { WorkflowStore, WorkflowDialog } from '../../web/js/workflows.js';
import { ControlError } from '../../web/js/control.js';

const XSS = '<img src=x onerror="window.__pwnedE1b=1"><script>window.__pwnedE1b=2</script>';
const JOB = 'b_EEEEEEEEEE';

function sampleDepartments(extra = []) {
  return [
    { id: 'yazilim', name: 'Yazılım', roles: ['mimar', 'test'] },
    { id: 'icerik', name: 'İçerik ve Medya', roles: ['yazar', 'editor'] },
    { id: 'akademik', name: 'Akademik ve Eğitim', roles: ['akademisyen', 'soru', 'danisman'] },
    { id: 'girisim', name: 'Girişim ve Pazarlama', roles: ['pazarlama', 'analist', 'denetci'] },
    { id: 'kisisel', name: 'Kişisel Yaşam', roles: ['planlayici', 'kontrol'] },
    ...extra,
  ];
}

function makeBossHub({
  features = ['handoff', 'workflows', 'boss', 'bossDepartments'],
  bossDepartments = sampleDepartments(),
} = {}) {
  const dev = {
    id: 'd1',
    label: 'Masaüstü',
    ready: true,
    status: 'ready',
    info: {
      features,
      boss: { maxCostUsd: 1.0 },
      bossDepartments,
      engines: { claude: { available: true } },
    },
    agents: new Map(),
  };
  const listeners = new Map();
  const calls = [];
  const hub = {
    calls,
    dev,
    devices: new Map([[dev.id, dev]]),
    get pairedCount() { return this.devices.size; },
    on(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
    emit(t, ...a) { for (const fn of listeners.get(t) || []) fn(...a); },
    device: (id) => hub.devices.get(id) || null,
    isReady: (id) => !!(hub.devices.get(id) && hub.devices.get(id).ready),
    async request(id, msg) {
      calls.push(msg);
      if (msg.type === 'boss.get') return { type: 'boss.get.result', job: null, plan: null, report: null };
      if (msg.type === 'workspace.list') return { workspaces: [{ id: 'isler', name: 'İşler', children: null }] };
      if (msg.type === 'boss.ask') {
        return {
          type: 'boss.ack',
          job: {
            type: 'boss.state',
            job: JOB,
            state: 'planning',
            department: msg.department || 'yazilim',
            code: null,
            round: 0,
            orders: 0,
            costUsd: 0,
            maxCostUsd: msg.maxCostUsd,
            estimateUsd: null,
            run: null,
            orderStates: [],
            workspace: msg.workspace,
            expiresAt: null,
          },
        };
      }
      return { type: 'boss.ok' };
    },
  };
  return hub;
}

async function bossDialogFromIndex() {
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const dlg = document.importNode(doc.getElementById('dlg-boss'), true);
  document.body.appendChild(dlg);
  return dlg;
}

const until = async (cond, ms = 2000) => {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error('condition not reached');
    await new Promise((r) => setTimeout(r, 10));
  }
};

test('E1b — readDepartments validates untrusted bossDepartments (/^[a-z]{1,16}$/, max 10 items, drops malformed)', () => {
  assert.equal(BOSS_DEPARTMENTS_FEATURE, 'bossDepartments');
  assert.equal(DEFAULT_DEPARTMENT, 'yazilim');
  assert.equal(DEPT_RE.test('yazilim'), true);
  assert.equal(DEPT_RE.test('icerik'), true);
  assert.equal(DEPT_RE.test('YAZILIM'), false);
  assert.equal(DEPT_RE.test('yazilim-1'), false);
  assert.equal(DEPT_RE.test('yazilim_1'), false);
  assert.equal(DEPT_RE.test('a'.repeat(17)), false);

  const withoutFeature = makeBossHub({ features: ['boss'], bossDepartments: sampleDepartments() });
  assert.equal(supportsBossDepartments(withoutFeature.dev), false);
  assert.deepEqual(readDepartments(withoutFeature.dev), []);

  // Malformed items mixed with valid items + more than 10 valid items
  const fourteenValid = [
    'yazilim', 'icerik', 'akademik', 'girisim', 'kisisel',
    'hukuk', 'finans', 'tasarim', 'iletisim', 'operasyon',
    'ik', 'satis', 'destek', 'lojistik',
  ].map((id, i) => ({ id, name: `Bölüm ${i + 1}`, roles: ['yazar'] }));

  const dirtyList = [
    null,
    42,
    'yazilim',
    {},
    { id: 'BUYUK', name: 'Büyük Harf', roles: [] },
    { id: 'rakam1', name: 'Rakamlı', roles: [] },
    { id: 'tire-var', name: 'Tireli', roles: [] },
    { id: 'a'.repeat(17), name: 'Çok Uzun ID', roles: [] },
    { id: 'bosad', name: '   ', roles: [] },
    { id: ' yanlis', name: 'Boşluklu', roles: [] },
    { id: 'yazilim', name: 'İlk Yazılım', roles: ['mimar'] },
    { id: 'yazilim', name: 'Kopya Yazılım', roles: ['mimar'] }, // duplicate id dropped
    ...fourteenValid.slice(1),
  ];

  const hub = makeBossHub({ bossDepartments: dirtyList });
  const cleaned = readDepartments(hub.dev);
  assert.equal(cleaned.length, 10, 'capped at 10 valid departments');
  assert.equal(cleaned[0].id, 'yazilim');
  assert.equal(cleaned[0].name, 'İlk Yazılım');
  assert.equal(cleaned.every((d) => DEPT_RE.test(d.id)), true);
});

test('E1b — readState allow-lists department only when matching /^[a-z]{1,16}$/', () => {
  const base = {
    type: 'boss.state',
    job: JOB,
    state: 'running',
    code: null,
    round: 0,
    orders: 1,
    costUsd: 0.05,
    maxCostUsd: 1.0,
    estimateUsd: 0.2,
    run: null,
    orderStates: [],
    workspace: { id: 'isler', sub: null },
  };
  assert.equal(readState({ ...base, department: 'icerik' }).department, 'icerik');
  assert.equal(readState(base).department, null);
  for (const bad of ['BUYUK', 'icerik_1', 'icerik-1', 'a'.repeat(17), '', 123, XSS]) {
    assert.equal(readState({ ...base, department: bad }).department, null, `expected ${bad} -> null`);
  }
});

test('E1b — BossPanel with bossDepartments shows select (default yazilim), Turkish hints, XSS-safe textContent, sends department in boss.ask, and shows department on job card', async () => {
  const hub = makeBossHub({
    bossDepartments: [
      { id: 'icerik', name: 'İçerik ve Medya', roles: ['yazar', 'editor'] },
      { id: 'yazilim', name: 'Yazılım', roles: ['mimar', 'test'] },
      { id: 'ozel', name: `Özel ${XSS}`, roles: ['yazar'] },
      { id: 'BOZUK_ID', name: 'Atlanmalı', roles: [] },
    ],
  });
  const store = new BossStore({ hub });
  const dlg = await bossDialogFromIndex();
  try {
    const panel = new BossPanel({ dialog: dlg, hub, store });
    panel.open();
    const f = dlg.querySelector('#boss-form').elements;
    await until(() => f.workspace.options.length === 1);

    const deptRow = dlg.querySelector('#boss-dept-row');
    const deptHint = dlg.querySelector('#boss-dept-hint');
    assert.ok(deptRow && !deptRow.hidden, 'department row should be visible');
    assert.deepEqual([...f.department.options].map((o) => o.value), ['icerik', 'yazilim', 'ozel']);
    // Default is "yazilim" even when it wasn't the first item in the array
    assert.equal(f.department.value, 'yazilim');
    assert.equal(deptHint.textContent, DEPARTMENT_HINTS.yazilim);
    assert.ok(deptHint.textContent.length > 0);

    // Switch to "icerik" -> Turkish hint updates
    f.department.value = 'icerik';
    f.department.dispatchEvent(new Event('change'));
    assert.equal(deptHint.textContent, DEPARTMENT_HINTS.icerik);
    assert.match(deptHint.textContent, /Blog|video|sosyal medya/i);

    // XSS attempt in department name is rendered via textContent only
    assert.equal(f.department.options[2].textContent, `Özel ${XSS}`);
    assert.equal(dlg.querySelector('img, script'), null);
    assert.equal(window.__pwnedE1b, undefined);

    // Submit with "ozel" department -> boss.ask includes department: 'ozel' and job card displays department name safely
    f.department.value = 'ozel';
    f.department.dispatchEvent(new Event('change'));
    f.brief.value = 'Bülten planla';
    await panel.ask();

    const askCall = hub.calls.find((m) => m.type === 'boss.ask');
    assert.ok(askCall);
    assert.equal(askCall.department, 'ozel');

    const jobBox = dlg.querySelector('#boss-job');
    assert.equal(jobBox.hidden, false);
    assert.ok(jobBox.textContent.includes(`Özel ${XSS}`), 'job card shows department name');
    assert.equal(dlg.querySelector('img, script'), null);
    assert.equal(window.__pwnedE1b, undefined);
    panel.close();
  } finally {
    if (dlg.open) dlg.close();
    dlg.remove();
  }
});

test('E1b — BossPanel WITHOUT bossDepartments feature hides department select and NEVER sends department field in boss.ask', async () => {
  const hub = makeBossHub({
    features: ['handoff', 'workflows', 'boss'], // no 'bossDepartments'
    bossDepartments: undefined,
  });
  const store = new BossStore({ hub });
  const dlg = await bossDialogFromIndex();
  try {
    const panel = new BossPanel({ dialog: dlg, hub, store });
    panel.open();
    const f = dlg.querySelector('#boss-form').elements;
    await until(() => f.workspace.options.length === 1);

    const deptRow = dlg.querySelector('#boss-dept-row');
    assert.equal(deptRow.hidden, true, 'department row must stay hidden for older connectors');

    f.brief.value = 'Koyu tema ekle';
    await panel.ask();

    const askCall = hub.calls.find((m) => m.type === 'boss.ask');
    assert.ok(askCall);
    assert.equal('department' in askCall, false, 'department field must NOT be sent when feature is absent');
    panel.close();
  } finally {
    if (dlg.open) dlg.close();
    dlg.remove();
  }
});

test('E1b — WorkflowDialog shows "Önizle" on finished agent step with task, sends task.get, opens PreviewDrawer with task.get.result, and toasts on error', async () => {
  const html = await (await fetch('/web/index.html')).text();
  const parsedDoc = new DOMParser().parseFromString(html, 'text/html');
  const dlg = document.importNode(parsedDoc.getElementById('dlg-workflows'), true);
  const previewDlg = document.importNode(parsedDoc.getElementById('dlg-preview'), true);
  document.body.append(dlg, previewDlg);

  const sampleRun = {
    id: 'w_1234567890',
    template: 'statik-site',
    name: 'Statik Site Ekibi',
    workspace: { id: 'isler', name: 'Pixel Office İşleri', sub: 'site' },
    state: 'running',
    code: null,
    current: 'gelistirici',
    costUsd: 0.12,
    turns: 3,
    limits: { maxCostUsd: 1.5, maxTurns: 30, timeoutS: 900 },
    startedAt: 1,
    endedAt: null,
    steps: [
      { id: 'mimar', type: 'agent', role: 'Mimar', engine: 'claude', model: 'haiku', agent: 'k1', task: 't_MMMMMMMMMM', state: 'done' },
      { id: 'bos-gorev', type: 'agent', role: 'Yardımcı', engine: 'claude', model: 'haiku', agent: 'k2', task: null, state: 'done' },
      { id: 'gelistirici', type: 'agent', role: 'Geliştirici', engine: 'claude', model: 'haiku', agent: 'k3', task: 't_GGGGGGGGGG', state: 'running' },
      { id: 'onay', type: 'approval', role: 'Taslak uygun mu?', state: 'waiting' },
    ],
  };

  let failTaskGet = false;
  const calls = [];
  const dev = {
    id: 'd1',
    label: 'Masaüstü',
    ready: true,
    status: 'ready',
    info: {
      features: ['workflows'],
      remote: {},
      limits: { maxPrompt: 32768 },
      engines: { claude: { available: true }, ollama: { available: false, models: [] } },
    },
    agents: new Map(),
  };
  const listeners = new Map();
  const hub = {
    calls,
    dev,
    devices: new Map([[dev.id, dev]]),
    on(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
    emit(t, ...a) { for (const fn of listeners.get(t) || []) fn(...a); },
    isPaired: () => true,
    isReady: () => true,
    device: (id) => (id === dev.id ? dev : null),
    async request(id, msg) {
      calls.push(msg);
      if (msg.type === 'workflow.list') {
        return {
          type: 'workflow.list.result',
          templates: [{
            id: 'statik-site',
            name: 'Statik Site Ekibi',
            description: 'Demo',
            builtin: true,
            folder: 'site',
            limits: { maxCostUsd: 1.5, maxTurns: 30, timeoutS: 900 },
            needsBrief: true,
            steps: [{ id: 'mimar', type: 'agent', role: 'Mimar', engine: 'claude', engines: ['claude'], profile: 'readonly', model: null }],
          }],
          invalid: 0,
          runs: [sampleRun],
        };
      }
      if (msg.type === 'workspace.list') {
        return { workspaces: [{ id: 'isler', name: 'İşler', children: [] }] };
      }
      if (msg.type === 'task.get') {
        if (failTaskGet) throw new ControlError('unknown_task', 'Görev bulunamadı.');
        // Exact shape of pixel_connector/app.py:h_task_get
        return {
          type: 'task.get.result',
          task: msg.task,
          agent: 'k1',
          prompt: 'Mimari plan hazırla',
          result: `# Mimari Tasarım\n\nModüller **onaylandı**.\n${XSS}`,
          status: 'success',
          usage: { inTok: 120, outTok: 340, cacheRead: 0, cacheWrite: 0, estimated: false },
          costUsd: 0.04,
          costBasis: 'reported',
          startedAt: 1000,
          endedAt: 2500,
        };
      }
      return {};
    },
  };

  const toasts = [];
  const store = new WorkflowStore({ hub });
  const wfDlg = new WorkflowDialog({
    dialog: dlg,
    hub,
    store,
    toast: (t, o) => toasts.push([t, o]),
    previewDialog: previewDlg,
  });

  try {
    wfDlg.open();
    await until(() => dlg.querySelectorAll('.wf-run .wf-step').length === 4);

    // Only the finished agent step WITH a task ('mimar') has the "Önizle" button
    const stepItems = [...dlg.querySelectorAll('.wf-run .wf-step')];
    assert.ok(stepItems[0].querySelector('[data-action="preview-step"]'), 'done agent step with task has Önizle');
    assert.equal(stepItems[0].querySelector('[data-action="preview-step"]').textContent, 'Önizle');
    assert.equal(stepItems[1].querySelector('[data-action="preview-step"]'), null, 'done step without task has no Önizle');
    assert.equal(stepItems[2].querySelector('[data-action="preview-step"]'), null, 'running step has no Önizle');
    assert.equal(stepItems[3].querySelector('[data-action="preview-step"]'), null, 'approval step has no Önizle');

    // Click "Önizle" -> sends task.get and opens PreviewDrawer with result
    const btn = stepItems[0].querySelector('[data-action="preview-step"]');
    btn.click();
    await until(() => previewDlg.hasAttribute('open'));

    assert.deepEqual(calls.find((m) => m.type === 'task.get'), { type: 'task.get', task: 't_MMMMMMMMMM' });
    const iframe = previewDlg.querySelector('iframe.preview-frame');
    assert.ok(iframe);
    assert.equal(iframe.getAttribute('sandbox'), '');
    assert.ok(iframe.srcdoc.includes('<h1>Mimari Tasarım</h1>'));
    assert.ok(iframe.srcdoc.includes('&lt;script&gt;window.__pwnedE1b=2&lt;/script&gt;'));
    assert.equal(window.__pwnedE1b, undefined);
    assert.equal(dlg.querySelectorAll('img, script').length, 0);
    assert.equal(previewDlg.querySelectorAll('img, script').length, 0);

    previewDlg.close();

    // Error path: task.get fails -> Turkish toast shown
    failTaskGet = true;
    btn.click();
    await until(() => toasts.length >= 1);
    assert.equal(Boolean(toasts[0][1] && toasts[0][1].error), true);
    assert.match(toasts[0][0], /Görev bulunamadı|alınamadı/i);
  } finally {
    if (previewDlg.open) previewDlg.close();
    if (dlg.open) dlg.close();
    previewDlg.remove();
    dlg.remove();
  }
});
