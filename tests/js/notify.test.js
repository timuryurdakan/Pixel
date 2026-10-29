// G3 (docs/EVRENSEL_PLATFORM_ARASTIRMA_RAPORU.md §1.5): browser notifications + tab title approval badge
// (permission only requested on user click; generic text only, never prompt/output text), and "Kopyala" +
// ".md indir" buttons for agent task output and Boss reports (textContent/Blob only, never innerHTML).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  Notifier, NOTIFY_STORAGE_KEY, GENERIC_APPROVAL_TEXT, GENERIC_DONE_TEXT,
  copyText, downloadMarkdown,
} from '../../web/js/notify.js';
import { ApprovalCenter } from '../../web/js/approvals.js';
import { TaskBlock } from '../../web/js/prompt.js';
import { BossStore, BossPanel } from '../../web/js/boss.js';

function fakeNotificationEnv(initialPerm = 'default') {
  const created = [];
  let perm = initialPerm;
  let requestCalls = 0;
  class FakeNotification {
    static get permission() { return perm; }
    static async requestPermission() {
      requestCalls += 1;
      if (perm === 'default') perm = 'granted';
      return perm;
    }
    constructor(title, opts = {}) {
      this.title = title;
      this.body = opts.body || '';
      this.tag = opts.tag || '';
      created.push(this);
    }
    close() {}
  }
  return {
    FakeNotification,
    created,
    get requestCalls() { return requestCalls; },
    setPermission(p) { perm = p; },
  };
}

function fakeHub() {
  const listeners = new Map();
  const devices = new Map([['d1', { id: 'd1', label: 'Mac', ready: true, info: { features: ['boss'] } }]]);
  return {
    devices,
    pairedCount: 1,
    device(id) { return devices.get(id) || null; },
    isReady(id) { const d = devices.get(id); return !!(d && d.ready); },
    on(ev, fn) { if (!listeners.has(ev)) listeners.set(ev, []); listeners.get(ev).push(fn); },
    emit(ev, ...args) { for (const fn of listeners.get(ev) || []) fn(...args); },
    async request() { return { ok: true }; },
  };
}

test('tab title badge updates with pending approvals from ApprovalCenter and BossStore and restores when zero', () => {
  const origTitle = document.title;
  try {
    document.title = 'Pixel Office';
    const env = fakeNotificationEnv('default');
    const notifier = new Notifier({ NotificationImpl: env.FakeNotification, baseTitle: 'Pixel Office' });

    assert.equal(document.title, 'Pixel Office');
    notifier.setSourceCount('approvals', 1);
    assert.equal(document.title, '(1 Onay) Pixel Office');

    notifier.setSourceCount('boss', 2);
    assert.equal(document.title, '(3 Onay) Pixel Office');

    notifier.setSourceCount('approvals', 0);
    assert.equal(document.title, '(2 Onay) Pixel Office');

    notifier.setSourceCount('boss', 0);
    assert.equal(document.title, 'Pixel Office');
    assert.equal(env.requestCalls, 0, 'never requests permission automatically');
  } finally {
    document.title = origTitle;
    try { localStorage.removeItem(NOTIFY_STORAGE_KEY); } catch {}
  }
});

test('notification permission is ONLY requested on user click and notifications NEVER contain prompt or output text', async () => {
  try { localStorage.removeItem(NOTIFY_STORAGE_KEY); } catch {}
  const origTitle = document.title;
  const banner = document.createElement('div');
  document.body.appendChild(banner);
  try {
    document.title = 'Pixel Office';
    const env = fakeNotificationEnv('default');
    const notifier = new Notifier({
      NotificationImpl: env.FakeNotification,
      baseTitle: 'Pixel Office',
      isHidden: () => true,
    });
    const hub = fakeHub();
    const center = new ApprovalCenter({ hub, banner, notifier, now: () => 1000 });

    const SECRET = 'GIZLI_KOMUT_rm_-rf_CANARY_999';
    center.add('d1', {
      id: 'a1234567890abcdef',
      ask: 'tool',
      agent: 'GizliAjan',
      tool: 'Bash',
      summary: SECRET,
      project: 'GizliProje',
      expiresAt: 60000,
    });

    // Title updated to (1 Onay), but permission was NOT requested and no Notification was fired yet.
    assert.equal(document.title, '(1 Onay) Pixel Office');
    assert.equal(env.requestCalls, 0);
    assert.equal(env.created.length, 0);

    // User clicks the notification toggle button inside ApprovalCenter header.
    const toggleBtn = banner.querySelector('[data-notify-toggle]');
    assert.ok(toggleBtn, 'notification toggle button exists');
    toggleBtn.click();
    await new Promise((r) => setTimeout(r, 0));

    assert.equal(env.requestCalls, 1, 'permission requested only after user clicked button');
    assert.equal(notifier.enabled, true);

    // Now a second approval arrives with sensitive text: notification fires with generic text ONLY.
    center.add('d1', {
      id: 'a2234567890abcdef',
      ask: 'tool',
      agent: 'GizliAjan2',
      tool: 'Bash',
      summary: SECRET,
      project: 'GizliProje2',
      expiresAt: 60000,
    });

    assert.equal(document.title, '(2 Onay) Pixel Office');
    assert.equal(env.created.length, 1);
    assert.equal(env.created[0].title, 'Pixel Office');
    assert.equal(env.created[0].body, GENERIC_APPROVAL_TEXT);
    assert.equal(env.created[0].body, 'Onay bekliyor');
    assert.ok(!env.created[0].body.includes(SECRET));
    assert.ok(!env.created[0].title.includes(SECRET));

    // Task completion notification also never includes prompt or output text.
    notifier.notifyDone(SECRET);
    assert.equal(env.created.length, 2);
    assert.equal(env.created[1].body, GENERIC_DONE_TEXT);
    assert.ok(!env.created[1].body.includes(SECRET));
  } finally {
    banner.remove();
    document.title = origTitle;
    try { localStorage.removeItem(NOTIFY_STORAGE_KEY); } catch {}
  }
});

test('TaskBlock renders Kopyala and .md indir buttons using textContent/Blob without innerHTML', async () => {
  const XSS = '<img src=x onerror="window.__pwned_notify=1"> **kalın** `kod`';
  const blk = new TaskBlock('tsk_1', 'Bana rapor yaz', null);
  document.body.appendChild(blk.el);
  try {
    blk.part({ k: 'text', v: `Satır 1\n${XSS}` });
    blk.end({ status: 'success', turns: 2, durationMs: 4000 }, 'Tamamlandı · 2 tur · 4 sn', { text: 'maliyet yok', title: '' });

    assert.equal(Boolean(window.__pwned_notify), false);
    assert.equal(blk.el.querySelector('img, script'), null);
    assert.match(blk.el.querySelector('.out-end').textContent, /Tamamlandı/);

    const copyBtn = blk.el.querySelector('[data-action="copy"]');
    const dlBtn = blk.el.querySelector('[data-action="download-md"]');
    assert.ok(copyBtn && dlBtn, 'Kopyala and .md indir buttons rendered');
    assert.equal(copyBtn.textContent, 'Kopyala');
    assert.equal(dlBtn.textContent, '.md indir');

    let copied = null;
    await copyText(blk.getOutputText(), {
      clipboard: { async writeText(t) { copied = t; } },
    });
    assert.equal(copied, `Satır 1\n${XSS}`);

    const dl = downloadMarkdown(blk.getOutputText(), 'gorev-tsk_1.md', { triggerDownload: false });
    assert.equal(dl.filename, 'gorev-tsk_1.md');
    const blobText = await dl.blob.text();
    assert.equal(blobText, `Satır 1\n${XSS}`);
  } finally {
    blk.el.remove();
  }
});

test('BossPanel renders Kopyala and .md indir for Boss report while keeping .boss-report children.length === 0', async () => {
  const html = await (await fetch('/web/index.html')).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const dlg = document.importNode(doc.getElementById('dlg-boss'), true);
  document.body.appendChild(dlg);
  try {
    const hub = fakeHub();
    const store = new BossStore({ hub });
    await new Promise((r) => setTimeout(r, 0));
    const panel = new BossPanel({ dialog: dlg, hub, store });
    const XSS_REPORT = '# Patron Raporu\n<script>window.__boss_xss=1</script>\nTamamlandı.';

    store.putState('d1', {
      type: 'boss.state',
      job: 'b_1234567890',
      state: 'done',
      round: 0,
      workspace: { id: 'isler', sub: null },
      costUsd: 0.12,
      maxCostUsd: 1.0,
      orders: 1,
      orderStates: [{ id: 'o1', role: 'backend', state: 'done' }],
    });
    store.putReport('d1', {
      type: 'boss.report',
      job: 'b_1234567890',
      report: XSS_REPORT,
      partial: false,
      state: 'done',
    });

    panel.open();
    const repEl = dlg.querySelector('#boss-job .boss-report');
    assert.ok(repEl);
    assert.equal(repEl.textContent, XSS_REPORT);
    assert.equal(repEl.children.length, 0, '.boss-report must have 0 child elements');
    assert.equal(Boolean(window.__boss_xss), false);

    const copyBtn = dlg.querySelector('#boss-job [data-action="copy"]');
    const dlBtn = dlg.querySelector('#boss-job [data-action="download-md"]');
    assert.ok(copyBtn && dlBtn, 'Boss report has Kopyala and .md indir buttons');
    assert.equal(copyBtn.textContent, 'Kopyala');
    assert.equal(dlBtn.textContent, '.md indir');
  } finally {
    dlg.remove();
  }
});
