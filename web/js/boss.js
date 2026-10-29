// Pixel Office — the Boss's panel (Patron Odası, docs/PATRON_ODASI.md §2, §4.6, §9 P6).
// Clicking the Boss opens it: the user's request (brief), the working folder, a cost cap that can only lower the
// device's ceiling and "Patrona ver"; then the plan card (Onayla / Reddet), the orders' progress with the approval
// before each Test order, the final report, İptal and "Hafızayı sıfırla". Everything the device sends is untrusted:
// read through allow-lists here and put into the DOM only with textContent. Only a paired, connected device whose
// device.info.features has "boss" gets the form; a viewer or an unpaired browser sees an info text only.

import { ControlError, errorText } from './control.js';
import { BOSS_FEATURE } from './boss_scene.js';
import { makeExportActions, defaultNotifier } from './notify.js';

export const BRIEF_MAX = 4096;               // = pixel_connector/workflows.py BRIEF_MAX (UTF-8 bytes)
export const COST_MIN = 0.10;                // = pixel_connector/boss.py RUN_COST_MIN
export const COST_DEFAULT = 1.00;            // = RUN_COST_DEFAULT: the ceiling when the device does not say another
export const COST_CEILING_MAX = 3.00;        // = RUN_COST_CEILING_MAX
export const REPORT_MAX = 16384;             // = REPORT_MAX (the connector counts bytes; characters are a safe bound)
const ASK_TIMEOUT_MS = 130000;               // boss.ask may wait for a confirmation on the computer (confirm.agentCreate)
const COUNTDOWN_MS = 15000;                  // the plan's "N dk" is redrawn this often while the panel is open
const MAX_ORDERS = 8;                        // the connector allows 4; a little room, more is refused
const TEXT_MAX = { summary: 600, title: 160, goal: 4000, accept: 400, file: 320, role: 64 };
const LIST_MAX = { accept: 10, files: 24 };

const JOB_RE = /^b_[A-Za-z0-9_-]{10}$/;
const RUN_RE = /^w_[A-Za-z0-9_-]{10}$/;
const ORDER_RE = /^o[1-9]$/;
const STEP_RE = /^(o[1-9]|patron)$/;
const WORD_RE = /^[a-z][a-z0-9_]{0,31}$/;    // role ids and codes

export const STATE_TEXT = {
  idle: 'Boşta', planning: 'Planlıyor', approval: 'Onay bekliyor', approved: 'Onaylandı', running: 'Çalışıyor',
  reviewing: 'Değerlendiriyor', reporting: 'Rapor yazıyor', done: 'Bitti', error: 'Hata', cancelled: 'İptal',
};
const TERMINAL = new Set(['done', 'error', 'cancelled']);
export const ORDER_TEXT = {
  waiting: 'Sırada', running: 'Çalışıyor', approval: 'Onay bekliyor', done: 'Bitti', error: 'Hata', cancelled: 'Yapılmadı',
};
const ORDER_MARK = { waiting: '…', running: '●', approval: '?', done: '✓', error: '✗', cancelled: '■' };
const STATE_NOTE = {
  planning: 'Patron klasörü yalnızca okuyarak inceliyor ve bir plan yazıyor.',
  approved: 'Plan onaylandı; uzmanlar geliyor.',
  reviewing: 'Uzmanlar bitirdi. Patron sonuçları kontrol ediyor.',
  reporting: 'Patron raporunu yazıyor.',
};

/** Why a job or an order stopped (pixel_connector/boss.py codes); unknown codes get the generic text. */
export const CODE_TEXT = {
  cancelled: 'İptal edildi', revoked: 'Başlatan tarayıcının eşleşmesi kaldırıldı', stopped: 'Bağlayıcı kapandı',
  cost_cap: 'Maliyet tavanı doldu', max_turns: 'Tur tavanı doldu', timeout: 'Süre tavanı doldu',
  denied: 'Reddedildi', approval_timeout: 'Onay süresi doldu (reddedildi sayıldı)',
  bad_plan: 'Patron geçerli bir plan yazamadı', role_unavailable: 'Planın istediği uzman bu bilgisayarda kullanılamıyor',
  skipped: 'Bağlı olduğu iş emri yapılmadı', busy: 'Ajan meşguldü', not_allowed: 'İzin verilmedi',
  workspace_denied: 'Çalışma klasörüne erişilemedi', engine_error: 'Uzman hata verdi', engine_exit: 'Motor beklenmedik biçimde kapandı',
  engine_unavailable: 'Claude Code bulunamadı', rate_limited: 'Görev hız sınırına takıldı', budget_exceeded: 'Bütçe sınırına ulaşıldı',
};

/** Turkish text of a stop code; never the raw code. */
export function codeText(code) {
  if (!code) return '';
  return Object.hasOwn(CODE_TEXT, code) ? CODE_TEXT[code] : errorText(code).replace(/\.$/, '');
}

/** Errors of the panel's requests (boss.ask / approve / cancel / reset, workflow.answer). */
const REQUEST_TEXT = {
  busy: 'Bu bilgisayarda Patronun ya da bir iş akışının işi sürüyor; bitince yenisini verebilirsiniz.',
  not_allowed: 'Bu bilgisayar uzaktan ajan oluşturmaya izin vermiyor (Patronun uzmanları ajan oluşturur).',
  workspace_denied: 'Bu klasör bilgisayarın izin listesinde değil ya da alt klasör yok.',
  engine_unavailable: 'Claude Code bu bilgisayarda bulunamadı. Patron yalnızca Claude Code ile çalışır.',
  too_large: 'İstek çok uzun (en fazla 4 KB).',
  bad_request: 'Bilgisayar isteği geçersiz buldu (metin ya da maliyet tavanı).',
  unknown_job: 'Bu iş bulunamadı ya da zaten bitti.',
};
/** S29-03: the approval before each Test order (§10/3); the same warning is the local approval item's preview. */
export const GATE_TEXT = 'Test uzmanı test komutlarını çalıştıracak. Bu komutlar uzmanların yazdığı kodu (test dosyaları, '
  + 'package.json betikleri) bilgisayarınızda sizin yetkilerinizle çalıştırır; bu kod klasörle ve Patron işinin '
  + 'tavanlarıyla sınırlı değildir. Başlasın mı? Reddederseniz bu iş emri atlanır.';
const RESET_BUSY_TEXT = 'Patron çalışırken hafızası sıfırlanamaz. İş bitince tekrar deneyin.';

function requestText(e) {
  const code = e && e.code;
  if (code && Object.hasOwn(REQUEST_TEXT, code)) return REQUEST_TEXT[code];
  return e instanceof ControlError || code ? errorText(code) : 'İşlem yapılamadı.';
}

export const INFO_TEXT = {
  viewer: 'Bu oda yalnızca izleme bağlantısıyla açık. Patrona iş vermek için davet bağlantısıyla girip bir bilgisayar eşleştirin.',
  unpaired: 'Patrona iş vermek için önce bir bilgisayar eşleştirin.',
  offline: 'Eşleşmiş bilgisayarınız şu an bağlı değil ya da bağlayıcısı Patron Odası\'nı desteklemiyor (bağlayıcı 3.8.0 ve üstü).',
};

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

function button(cls, text, onClick, disabled = false) {
  const b = el('button', cls, text);
  b.type = 'button';
  b.disabled = disabled;
  b.addEventListener('click', onClick);
  return b;
}

const money = (n) => `$${(Number.isFinite(n) ? n : 0).toFixed(2)}`;
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const numOrNull = (v) => (isNum(v) ? v : null);
const bytes = (s) => new TextEncoder().encode(s).length;

/** A device whose connector understands boss.* and is connected. */
export function supportsBoss(dev) {
  return !!(dev && dev.ready && dev.info && Array.isArray(dev.info.features) && dev.info.features.includes(BOSS_FEATURE));
}

/** The device's run cost ceiling (device.info.boss.maxCostUsd); anything invalid → the connector's default. */
export function costCeiling(dev) {
  const v = dev && dev.info && dev.info.boss ? dev.info.boss.maxCostUsd : undefined;
  return isNum(v) && v >= COST_MIN && v <= COST_CEILING_MAX ? v : COST_DEFAULT;
}

/** The cap field → {value} or {error}. It may only lower the ceiling: refused, never clamped (S26-04). */
export function parseCost(raw, ceiling) {
  const text = String(raw ?? '').trim().replace(',', '.');
  const v = /^\d+(\.\d+)?$/.test(text) ? Number(text) : NaN;
  if (!(v >= COST_MIN && v <= ceiling)) {
    return { error: `Maliyet tavanı ${COST_MIN.toFixed(2)}–${ceiling.toFixed(2)} $ arasında olmalı (bu bilgisayarın sınırı; yalnızca düşürebilirsiniz).` };
  }
  return { value: Math.round(v * 100) / 100 };
}

const str = (v, max) => (typeof v === 'string' ? v.slice(0, max) : null);
const strList = (v, maxItems, maxLen) => (Array.isArray(v) && v.length <= maxItems && v.every((x) => typeof x === 'string')
  ? v.map((x) => x.slice(0, maxLen)) : null);

function readOrderState(o) {
  if (!o || typeof o !== 'object' || typeof o.id !== 'string' || !STEP_RE.test(o.id)
    || !Object.hasOwn(ORDER_TEXT, o.state)) return null;
  return { id: o.id, role: typeof o.role === 'string' && WORD_RE.test(o.role) ? o.role : null, state: o.state,
    code: typeof o.code === 'string' && WORD_RE.test(o.code) ? o.code : null };
}

/** Untrusted `boss.state` (or a boss.ack / boss.get job) → a clean job object, or null. */
export function readState(m) {
  if (!m || typeof m !== 'object' || typeof m.job !== 'string' || !JOB_RE.test(m.job)
    || !Object.hasOwn(STATE_TEXT, m.state)) return null;
  if (!isNum(m.costUsd) || !isNum(m.maxCostUsd) || (m.round !== 0 && m.round !== 1)) return null;
  if (m.run != null && !(typeof m.run === 'string' && RUN_RE.test(m.run))) return null;
  if (m.orderStates != null && !Array.isArray(m.orderStates)) return null;
  const ws = m.workspace && typeof m.workspace === 'object' ? m.workspace : {};
  return {
    job: m.job, state: m.state, code: typeof m.code === 'string' && WORD_RE.test(m.code) ? m.code : null,
    round: m.round, orders: Number.isInteger(m.orders) && m.orders >= 0 && m.orders <= MAX_ORDERS ? m.orders : 0,
    costUsd: m.costUsd, maxCostUsd: m.maxCostUsd, estimateUsd: numOrNull(m.estimateUsd), run: m.run || null,
    orderStates: (m.orderStates || []).slice(0, MAX_ORDERS + 1).map(readOrderState).filter(Boolean),
    workspace: { id: str(ws.id, 64), sub: str(ws.sub, 200) }, expiresAt: numOrNull(m.expiresAt),
  };
}

function readOrder(o) {
  if (!o || typeof o !== 'object' || typeof o.id !== 'string' || !ORDER_RE.test(o.id)
    || typeof o.role !== 'string' || !WORD_RE.test(o.role)) return null;
  const title = str(o.title, TEXT_MAX.title), goal = str(o.goal, TEXT_MAX.goal);
  const accept = strList(o.accept, LIST_MAX.accept, TEXT_MAX.accept);
  const files = o.files == null ? [] : strList(o.files, LIST_MAX.files, TEXT_MAX.file);
  const after = o.after == null ? [] : strList(o.after, MAX_ORDERS, 2);
  if (title === null || goal === null || !accept || !files || !after || !after.every((a) => ORDER_RE.test(a))) return null;
  return { id: o.id, role: o.role, title, goal, accept, files, after };
}

/** Untrusted `boss.plan` → {job, summary, orders, roles, estimateUsd, maxCostUsd, round, expiresAt} or null. */
export function readPlan(m) {
  if (!m || typeof m !== 'object' || typeof m.job !== 'string' || !JOB_RE.test(m.job)) return null;
  const p = m.plan;
  if (!p || typeof p !== 'object' || !Array.isArray(p.orders) || !p.orders.length || p.orders.length > MAX_ORDERS) return null;
  const orders = p.orders.map(readOrder);
  const summary = str(p.summary, TEXT_MAX.summary);
  if (summary === null || orders.some((o) => !o)) return null;
  const roles = {};
  for (const [id, r] of Object.entries(m.roles && typeof m.roles === 'object' ? m.roles : {})) {
    if (WORD_RE.test(id) && r && typeof r.name === 'string') roles[id] = { name: r.name.slice(0, TEXT_MAX.role) };
  }
  return { job: m.job, summary, orders, roles, estimateUsd: numOrNull(m.estimateUsd), maxCostUsd: numOrNull(m.maxCostUsd),
    round: m.round === 1 ? 1 : 0, expiresAt: numOrNull(m.expiresAt) };
}

/** Untrusted `boss.report` → {job, report, partial, state, code, costUsd, maxCostUsd} or null. */
export function readReport(m) {
  if (!m || typeof m !== 'object' || typeof m.job !== 'string' || !JOB_RE.test(m.job) || typeof m.report !== 'string') return null;
  return { job: m.job, report: m.report.slice(0, REPORT_MAX), partial: m.partial === true,
    state: Object.hasOwn(STATE_TEXT, m.state) ? m.state : null, code: typeof m.code === 'string' && WORD_RE.test(m.code) ? m.code : null,
    costUsd: numOrNull(m.costUsd), maxCostUsd: numOrNull(m.maxCostUsd) };
}

/**
 * The latest Boss job per paired device: `boss.state` / `boss.plan` / `boss.report` events and one `boss.get` per
 * connection of a ready device with the "boss" feature (state after a reload or reconnect). Devices that are gone or
 * not ready are forgotten.
 */
export class BossStore {
  /** @param {{hub: import('./control.js').DeviceHub, notifier?: import('./notify.js').Notifier}} o */
  constructor({ hub, notifier = defaultNotifier }) {
    this.hub = hub;
    this.notifier = notifier;
    this.entries = new Map();           // device → {jobId, job, plan, report}
    this.loaded = new Set();
    this._listeners = new Set();
    this._lastApprovalCount = 0;
    hub.on('event', (dev, m) => this._onEvent(dev, m));
    hub.on('change', () => this._sweep());
    hub.on('resync', (dev) => { this.loaded.delete(dev); this._sweep(); });
    this._sweep();
  }

  onChange(fn) { this._listeners.add(fn); }
  _changed() {
    if (this.notifier) {
      const prev = this._lastApprovalCount || 0;
      const next = this.approvalCount();
      this._lastApprovalCount = next;
      this.notifier.setSourceCount('boss', next);
      if (next > prev) this.notifier.notifyApproval();
    }
    for (const fn of this._listeners) fn();
  }

  approvalCount() {
    let n = 0;
    for (const e of this.entries.values()) {
      if (!e || !e.job) continue;
      if (e.job.state === 'approval') n += 1;
      else if (Array.isArray(e.job.orderStates)) {
        for (const o of e.job.orderStates) if (o && o.state === 'approval') n += 1;
      }
    }
    return n;
  }

  _ok(dev) { return supportsBoss(this.hub.devices.get(dev)); }

  _onEvent(dev, m) {
    if (!m || typeof m !== 'object' || m.re || !this._ok(dev)) return;
    let changed = false;
    if (m.type === 'boss.state') changed = this.putState(dev, m);
    else if (m.type === 'boss.plan') changed = this.putPlan(dev, m);
    else if (m.type === 'boss.report') changed = this.putReport(dev, m);
    if (changed) this._changed();
  }

  _sweep() {
    let changed = false;
    for (const id of [...this.entries.keys(), ...this.loaded]) {
      if (!this._ok(id)) { this.loaded.delete(id); changed = this.entries.delete(id) || changed; }
    }
    for (const d of this.hub.devices.values()) if (supportsBoss(d) && !this.loaded.has(d.id)) this.load(d.id).catch(() => {});
    if (changed) this._changed();
  }

  async load(dev) {
    this.loaded.add(dev);
    let r;
    try {
      r = await this.hub.request(dev, { type: 'boss.get' });
    } catch (e) {
      this.loaded.delete(dev);
      throw e;
    }
    if (!this.loaded.has(dev)) return;
    if (!r || !r.job) this.entries.delete(dev);
    else if (this.putState(dev, r.job)) {
      if (r.plan) this.putPlan(dev, r.plan);
      if (r.report) this.putReport(dev, r.report);
    }
    this._changed();
  }

  _entry(dev, jobId) {
    const cur = this.entries.get(dev);
    if (cur && cur.jobId === jobId) return cur;
    const fresh = { jobId, job: null, plan: null, report: null };
    this.entries.set(dev, fresh);
    return fresh;
  }

  putState(dev, raw) {
    const job = readState(raw);
    if (!job) return false;
    const cur = this.entries.get(dev);
    // The delivery order may mix (V3.md §20): a finished job never becomes active again.
    if (cur && cur.jobId === job.job && cur.job && TERMINAL.has(cur.job.state) && !TERMINAL.has(job.state)) return false;
    this._entry(dev, job.job).job = job;
    return true;
  }

  putPlan(dev, raw) {
    const plan = readPlan(raw);
    if (!plan) return false;
    this._entry(dev, plan.job).plan = plan;
    return true;
  }

  putReport(dev, raw) {
    const report = readReport(raw);
    if (!report) return false;
    const entry = this._entry(dev, report.job);
    const isNew = !entry.report;      // a resync re-delivers the same report: notify only once
    entry.report = report;
    if (isNew && this.notifier) { try { this.notifier.notifyDone(); } catch { /* notifications are best effort */ } }
    return true;
  }

  /** A request's reply carrying a job (boss.ack): stored like a boss.state. */
  acceptAck(dev, raw) {
    if (this.putState(dev, raw)) this._changed();
  }

  /** {jobId, job, plan, report} of a device, or null. */
  entry(dev) {
    const e = this.entries.get(dev);
    return e && e.job ? e : null;
  }

  /** [{device, job, plan}] of the plans waiting for the user's approval (Task Board card). */
  pendingPlans() {
    const out = [];
    for (const [device, e] of this.entries) {
      if (e.job && e.job.state === 'approval' && e.plan) out.push({ device, job: e.job, plan: e.plan });
    }
    return out;
  }
}

/** The "Patron Odası" dialog. Without a hub (viewer, local mode, no relay) it only shows the info text. */
export class BossPanel {
  /**
   * @param {object} o
   * @param {HTMLDialogElement} o.dialog
   * @param {import('./control.js').DeviceHub|null} [o.hub]
   * @param {BossStore|null} [o.store]
   * @param {(text:string, opts?:object)=>void} [o.toast]
   * @param {boolean} [o.viewer]   opened with a view-only link (no key): a different info text
   */
  constructor({ dialog, hub = null, store = null, toast = () => {}, viewer = false }) {
    Object.assign(this, { dialog, hub, store, toast, viewer });
    this.info = dialog.querySelector('#boss-info');
    this.deviceRow = dialog.querySelector('#boss-device-row');
    this.deviceSel = dialog.querySelector('#boss-device');
    this.jobBox = dialog.querySelector('#boss-job');
    this.form = dialog.querySelector('#boss-form');
    this.f = this.form.elements;
    this.subRow = dialog.querySelector('#boss-sub-row');
    this.capHint = dialog.querySelector('#boss-cap-hint');
    this.err = dialog.querySelector('#boss-error');
    this.resetBox = dialog.querySelector('#boss-reset-confirm');
    this.note = dialog.querySelector('#boss-note');
    this.actError = '';                 // the last failed job action, shown in the job card (toasts sit under the modal)
    this.wsCache = new Map();
    this.inflight = new Set();          // request keys: 'ask', 'reset', 'plan', 'cancel', 'gate:<order>'
    this.timer = null;
    dialog.addEventListener('close', () => this._stopTimer());
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
    if (!hub) return;
    this.deviceSel.addEventListener('change', () => { this._error(''); this.render(); this._loadDevice(); });
    this.f.workspace.addEventListener('change', () => this._ws());
    this.form.addEventListener('submit', (e) => { e.preventDefault(); this.ask(); });
    dialog.querySelector('[data-boss-reset]').addEventListener('click', () => this._askReset(true));
    dialog.querySelector('[data-boss-reset-no]').addEventListener('click', () => this._askReset(false));
    dialog.querySelector('[data-boss-reset-yes]').addEventListener('click', () => this.reset());
    if (store) store.onChange(() => { if (this.dialog.open) this.render(); });
  }

  get isOpen() { return this.dialog.open; }

  _devices() { return this.hub ? [...this.hub.devices.values()].filter((d) => supportsBoss(d)) : []; }
  _device() { return this.hub ? this.hub.device(this.deviceSel.value) : null; }

  open() {
    if (this.hub) {
      const devs = this._devices();
      const keep = this.deviceSel.value;
      this.deviceSel.replaceChildren(...devs.map((d) => { const o = el('option', null, d.label); o.value = d.id; return o; }));
      if (devs.length) this.deviceSel.value = devs.some((d) => d.id === keep) ? keep : devs[0].id;
    }
    this._error('');
    this._note('');
    this._askReset(false);
    this.actError = '';
    this.render();
    if (!this.dialog.open) {
      if (typeof this.dialog.showModal === 'function') this.dialog.showModal();
      else this.dialog.setAttribute('open', '');
    }
    this._startTimer();
    if (this._device()) this._loadDevice();
  }

  close() {
    if (this.dialog.open) this.dialog.close();
    this._stopTimer();
  }

  _startTimer() {
    this._stopTimer();
    this.timer = setInterval(() => {
      const e = this._entry();
      if (this.dialog.open && e && e.job.state === 'approval') this.render();
    }, COUNTDOWN_MS);
  }

  _stopTimer() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  _entry() {
    const d = this._device();
    return d && this.store ? this.store.entry(d.id) : null;
  }

  _infoText() {
    if (!this.hub) return this.viewer ? INFO_TEXT.viewer : INFO_TEXT.unpaired;
    if (!this.hub.pairedCount) return INFO_TEXT.unpaired;
    return this._devices().length ? '' : INFO_TEXT.offline;
  }

  /** Info text, or the device choice + the job card + the form (only while no job of that device is active). */
  render() {
    const info = this._infoText();
    this.info.textContent = info;
    this.info.hidden = !info;
    const usable = !info && !!this._device();
    this.deviceRow.hidden = !usable || this._devices().length < 2;
    const entry = usable ? this._entry() : null;
    this.jobBox.hidden = !entry;
    this.jobBox.replaceChildren();
    if (entry) this.jobBox.appendChild(this._jobCard(entry));
    this.form.hidden = !usable || !!(entry && !TERMINAL.has(entry.job.state));
    if (!this.form.hidden) this._updateAsk();
  }

  async _loadDevice() {
    const d = this._device();
    if (!d || this.wsCache.has(d.id)) { this._fillForm(); return; }
    try {
      const r = await this.hub.request(d.id, { type: 'workspace.list' });
      this.wsCache.set(d.id, Array.isArray(r && r.workspaces) ? r.workspaces : []);
    } catch (e) {
      this._error(e instanceof ControlError ? e.text : 'Çalışma klasörleri alınamadı.');
      return;
    }
    if (this._device() === d) this._fillForm();
  }

  _fillForm() {
    const d = this._device();
    if (!d) return;
    const list = (this.wsCache.get(d.id) || []).filter((w) => w && typeof w.id === 'string');
    const ws = this.f.workspace;
    const keep = ws.value;
    ws.replaceChildren(...list.map((w) => { const o = el('option', null, typeof w.name === 'string' ? w.name : w.id); o.value = w.id; return o; }));
    if (list.some((w) => w.id === keep)) ws.value = keep;
    else if (list.some((w) => w.id === 'isler')) ws.value = 'isler';            // ~/PixelOfficeIsler (Phase 17 default)
    const ceiling = costCeiling(d);
    this.f.cost.max = String(ceiling);
    this.f.cost.min = String(COST_MIN);
    if (!this.f.cost.value || !(parseCost(this.f.cost.value, ceiling).value >= 0)) this.f.cost.value = ceiling.toFixed(2);
    this.capHint.textContent = `Bu bilgisayarda en fazla ${ceiling.toFixed(2)} $. Yalnızca düşürebilirsiniz; tavana gelince iş durur. `
      + 'Abonelikle kullanımda bu tutar kullanım kotanızdan düşen eşdeğer maliyettir.';
    this._ws();
    this._updateAsk();
  }

  _ws() {
    const d = this._device();
    const w = (d ? this.wsCache.get(d.id) || [] : []).find((x) => x && x.id === this.f.workspace.value);
    this.subRow.hidden = !(w && Array.isArray(w.children));
  }

  _workspace() {
    const sub = this.subRow.hidden ? '' : this.f.sub.value.trim();
    return { id: this.f.workspace.value, sub: sub || null };
  }

  _error(text) {
    this.err.textContent = text;
    this.err.hidden = !text;
  }

  _note(text) {
    this.note.textContent = text;
    this.note.hidden = !text;
  }

  _updateAsk() {
    this.f.ask.disabled = this.inflight.has('ask');
    this.dialog.querySelector('[data-boss-reset]').disabled = this.inflight.has('reset');
    this.dialog.querySelector('[data-boss-reset-yes]').disabled = this.inflight.has('reset');
  }

  /** The boss.ask message from the form, or {error}. */
  request() {
    const d = this._device();
    if (!d) return { error: INFO_TEXT.offline };
    const brief = this.f.brief.value.trim();
    if (!brief) return { error: 'Patrona ne yaptırmak istediğinizi kısaca yazın.' };
    if (bytes(brief) > BRIEF_MAX) return { error: 'İstek çok uzun (en fazla 4 KB).' };
    if (!this.f.workspace.value) return { error: 'Bir çalışma klasörü seçin.' };
    const cost = parseCost(this.f.cost.value, costCeiling(d));
    if (cost.error) return { error: cost.error };
    return { msg: { type: 'boss.ask', workspace: this._workspace(), brief, maxCostUsd: cost.value } };
  }

  async ask() {
    if (!this.hub || this.inflight.has('ask')) return;
    const r = this.request();
    if (r.error) { this._error(r.error); return; }
    const d = this._device();
    this._error('');
    this.inflight.add('ask');
    this._updateAsk();
    try {
      const res = await this.hub.request(d.id, r.msg, { timeout: ASK_TIMEOUT_MS });
      if (this.store && res) this.store.acceptAck(d.id, res.job);
      this.f.brief.value = '';
      this.toast('Patron işi aldı, plan yazıyor', { ms: 3000 });
    } catch (e) {
      this._error(requestText(e));
    } finally {
      this.inflight.delete('ask');
      if (this.dialog.open) this.render();
      this._updateAsk();
    }
  }

  _askReset(show) {
    this.resetBox.hidden = !show;
    if (show) { this._error(''); this._note(''); }
  }

  async reset() {
    if (!this.hub || this.inflight.has('reset')) return;
    const d = this._device();
    if (!d || !this.f.workspace.value) { this._error('Bir çalışma klasörü seçin.'); return; }
    this.inflight.add('reset');
    this._updateAsk();
    try {
      const r = await this.hub.request(d.id, { type: 'boss.reset', workspace: this._workspace() });
      this._askReset(false);
      const text = r && r.reset ? 'Patronun bu klasördeki hafızası sıfırlandı.' : 'Bu klasör için Patronun hafızası zaten boş.';
      this._note(text);
      this.toast(text, { ms: 3000 });
    } catch (e) {
      this._error(e && e.code === 'busy' ? RESET_BUSY_TEXT : requestText(e));
    } finally {
      this.inflight.delete('reset');
      this._updateAsk();
    }
  }

  async _act(key, msg, okText) {
    const d = this._device();
    if (!d || this.inflight.has(key)) return;
    this.inflight.add(key);
    this.actError = '';
    this.render();
    try {
      await this.hub.request(d.id, msg);
      if (okText) this.toast(okText, { ms: 2500 });
    } catch (e) {
      this.actError = requestText(e);
      this.toast(this.actError, { error: true });
    } finally {
      this.inflight.delete(key);
      if (this.dialog.open) this.render();
    }
  }

  // -- the job card ---------------------------------------------------------------------------------------------
  _jobCard({ job, plan, report }) {
    const card = el('article', `boss-card wf-run st-${job.state}`);
    card.dataset.job = job.job;
    const head = el('div', 'wf-run-head');
    head.append(el('b', 'wf-run-name', job.round ? 'Patron işi · düzeltme turu' : 'Patron işi'),
      el('span', `wf-badge st-${job.state}`, STATE_TEXT[job.state]));
    card.appendChild(head);
    const name = this._folderName(job.workspace.id);
    const ws = name ? `Klasör: ${name}${job.workspace.sub ? '/' + job.workspace.sub : ''} · ` : '';
    card.appendChild(el('div', 'wf-run-meta muted small', `${ws}${money(job.costUsd)} / ${money(job.maxCostUsd)}`));
    if (STATE_NOTE[job.state]) card.appendChild(el('p', 'wf-run-note small', STATE_NOTE[job.state]));
    if (this.actError) {
      const err = el('p', 'form-error boss-act-error', this.actError);
      err.setAttribute('role', 'alert');
      card.appendChild(err);
    }
    const thePlan = plan && plan.job === job.job ? plan : null;
    if (job.state === 'approval' && thePlan) card.appendChild(this._planCard(job, thePlan));
    else if (job.state === 'approval') card.appendChild(el('p', 'wf-run-note small', 'Plan yükleniyor…'));
    if (job.orderStates.length && job.state !== 'approval') card.appendChild(this._orders(job, thePlan));
    if (TERMINAL.has(job.state)) {
      const end = job.state === 'done' ? 'İş bitti.' : `İş durdu${job.code ? ': ' + codeText(job.code) : ''}.`;
      card.appendChild(el('p', 'wf-run-note small', end));
      const rep = report && report.job === job.job ? report : null;
      if (rep) {
        card.appendChild(el('h4', null, rep.partial ? 'Rapor (iş yarım kaldı)' : 'Rapor'));
        card.appendChild(el('pre', 'boss-report', rep.report));
        card.appendChild(makeExportActions(() => rep.report, `patron-rapor-${job.job}`, {
          btnClass: 'btn small ghost',
          containerClass: 'boss-report-actions export-actions',
        }));
      }
    } else {
      const actions = el('div', 'wf-run-actions');
      actions.appendChild(button('btn small ghost boss-cancel', 'İptal',
        () => this._act('cancel', { type: 'boss.cancel', job: job.job }, 'Patron işi durduruluyor'), this.inflight.has('cancel')));
      card.appendChild(actions);
    }
    return card;
  }

  /** The folder's name from workspace.list (the id until the list is in). */
  _folderName(id) {
    const d = this._device();
    const w = d && id ? (this.wsCache.get(d.id) || []).find((x) => x && x.id === id) : null;
    return w && typeof w.name === 'string' ? w.name : id;
  }

  _roleName(plan, id) {
    return plan && plan.roles[id] ? plan.roles[id].name : (id === 'patron' ? 'Patron' : id || 'Uzman');
  }

  _planCard(job, plan) {
    const box = el('section', 'boss-plan');
    box.appendChild(el('h4', null, plan.round ? 'Düzeltme planı' : 'Patronun planı'));
    box.appendChild(el('p', 'boss-summary', plan.summary));
    const ol = el('ol', 'boss-plan-orders');
    for (const o of plan.orders) {
      const li = el('li', 'boss-plan-order');
      const top = el('div', 'boss-order-top');
      top.append(el('span', 'boss-role', this._roleName(plan, o.role)), el('b', 'boss-order-title', o.title));
      li.appendChild(top);
      li.appendChild(el('p', 'boss-goal small', o.goal));
      if (o.accept.length) {
        const ul = el('ul', 'boss-accept small');
        for (const a of o.accept) ul.appendChild(el('li', null, a));
        li.appendChild(el('div', 'muted small', 'Bitti sayılması için:'));
        li.appendChild(ul);
      }
      const bits = [];
      if (o.files.length) bits.push(`Dosyalar: ${o.files.join(', ')}`);
      if (o.after.length) bits.push(`${o.after.join(', ')} bitince başlar`);
      if (bits.length) li.appendChild(el('div', 'boss-order-meta muted small', bits.join(' · ')));
      ol.appendChild(li);
    }
    box.appendChild(ol);
    const est = plan.estimateUsd ?? job.estimateUsd;
    box.appendChild(el('p', 'boss-cost', `Tahmini maliyet: ${money(est)} · Kesin tavan: ${money(plan.maxCostUsd ?? job.maxCostUsd)}`));
    const exp = plan.expiresAt ?? job.expiresAt;
    const left = isNum(exp) ? Math.max(0, Math.round((exp - Date.now()) / 60000)) : null;
    box.appendChild(el('p', 'muted small', `Onaylamazsanız hiçbir uzman çalışmaz.${left !== null ? ` Yanıt verilmezse ${left} dk sonra reddedilmiş sayılır.` : ''}`));
    const actions = el('div', 'wf-run-actions');
    const busy = this.inflight.has('plan');
    actions.append(
      button('btn small accent boss-approve', 'Onayla', () => this._act('plan', { type: 'boss.approve', job: job.job, approve: true }, 'Plan onaylandı'), busy),
      button('btn small boss-deny', 'Reddet', () => this._act('plan', { type: 'boss.approve', job: job.job, approve: false }, 'Plan reddedildi'), busy));
    box.appendChild(actions);
    return box;
  }

  _orders(job, plan) {
    const ol = el('ol', 'wf-step-list boss-orders');
    for (const o of job.orderStates) {
      const li = el('li', `wf-step boss-order st-${o.state}`);
      const known = plan ? plan.orders.find((x) => x.id === o.id) : null;
      const name = o.id === 'patron' ? 'Patronun değerlendirmesi' : known ? known.title : o.id;
      li.append(el('span', 'wf-step-st', ORDER_MARK[o.state] || '·'), el('span', 'wf-step-name', name));
      let extra = `${this._roleName(plan, o.role)} · ${ORDER_TEXT[o.state]}`;
      if (o.code && o.state !== 'done') extra += ` · ${codeText(o.code)}`;
      li.appendChild(el('span', 'wf-step-meta muted small', extra));
      if (o.state === 'approval' && job.run) li.appendChild(this._gate(job, o));
      ol.appendChild(li);
    }
    return ol;
  }

  /** §10/3: every Test order waits for the user's approval before it starts (workflow.answer with the step). */
  _gate(job, o) {
    const box = el('div', 'boss-gate');
    box.appendChild(el('p', 'wf-run-note small', GATE_TEXT));
    const key = `gate:${o.id}`;
    const busy = this.inflight.has(key);
    const actions = el('div', 'wf-run-actions');
    const answer = (decision, text) => this._act(key, { type: 'workflow.answer', run: job.run, decision, step: o.id }, text);
    actions.append(button('btn small accent boss-gate-approve', 'Onayla', () => answer('approve', 'Test başlıyor'), busy),
      button('btn small boss-gate-deny', 'Reddet', () => answer('deny', 'Test iş emri atlanacak'), busy));
    box.appendChild(actions);
    return box;
  }
}
