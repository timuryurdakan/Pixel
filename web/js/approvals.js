// Pixel Office v3 — remote approval cards (Phase 19, V3.md §6.9). Plain ES module.
//
// A Claude Code permission request (or AskUserQuestion) on a paired computer arrives as `approval.request` over the
// E2E channel; the card shows tool + short summary + agent + countdown with "Onayla" / "Reddet" (options for a
// question). The first answer anywhere wins: `approval.resolved` (another device, the computer's own dialog, the /onay
// page, the CLI, expiry) removes the card on every device. All device text is rendered with textContent only.
// There is deliberately no "approve all" control.

import { defaultNotifier } from './notify.js';
import { t as tt } from './i18n.js';

export const APPROVAL_TICK_MS = 1000;
export const VIBRATE_MS = 180;
const ID_RE = /^a[A-Za-z0-9_-]{16}$/;
const MAX_QUESTIONS = 4;
const MAX_OPTIONS = 6;

/** Turkish text for an answer error code. */
export const APPROVAL_ERROR_TEXT = {
  unknown_approval: tt('Bu istek başka yerden cevaplandı ya da süresi doldu.'),
  device_offline: tt('Cihaz çevrimdışı.'),
  timeout: tt('Cihaz yanıt vermedi.'),
  bad_request: tt('Cihaz cevabı geçersiz buldu.'),
};

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

function str(v, max) { return typeof v === 'string' ? v.slice(0, max) : ''; }

/**
 * Validate an `approval` object from a device (the device is trusted to be ours, but the UI still only takes the
 * fields and sizes it knows). → normalized object or null.
 */
export function normalizeApproval(a) {
  if (!a || typeof a !== 'object' || typeof a.id !== 'string' || !ID_RE.test(a.id)) return null;
  if (a.ask !== 'tool' && a.ask !== 'question') return null;
  if (!Number.isFinite(a.expiresAt)) return null;
  const out = { id: a.id, ask: a.ask, agent: str(a.agent, 40) || 'Claude Code', tool: str(a.tool, 64),
    summary: str(a.summary, 200), project: str(a.project, 32), expiresAt: a.expiresAt, questions: [] };
  if (a.ask === 'question') {
    if (!Array.isArray(a.questions) || !a.questions.length || a.questions.length > MAX_QUESTIONS) return null;
    for (const q of a.questions) {
      if (!q || typeof q.question !== 'string' || !Array.isArray(q.options) || !q.options.length
        || q.options.length > MAX_OPTIONS) return null;
      out.questions.push({ question: str(q.question, 200), header: str(q.header, 30), multiSelect: q.multiSelect === true,
        options: q.options.map((o) => ({ label: str(o && o.label, 60), description: str(o && o.description, 120) })) });
    }
  }
  return out;
}

/** S23-02: the hook and the connector end a cut summary with "…"; such a command was never shown in full. */
export function summaryWasCut(summary) { return typeof summary === 'string' && summary.endsWith('…'); }

/** "4:05" / "0:09" */
export function countdownText(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export class ApprovalCenter {
  /**
   * @param {object} o
   * @param {import('./control.js').DeviceHub} o.hub
   * @param {HTMLElement} o.banner        overlay over the office (#approvals)
   * @param {HTMLElement} [o.pageSlot]    slot on the Cihazlar page; the list moves there while the page is open
   * @param {HTMLElement[]} [o.badges]    elements that get data-count (Cihazlar button / tab)
   * @param {object} [o.sound]            audio.js engine (alert only when the user unmuted it)
   * @param {() => number} [o.now]
   */
  constructor({ hub, banner, pageSlot = null, badges = [], sound = null, notifier = defaultNotifier, now = () => Date.now() }) {
    Object.assign(this, { hub, banner, pageSlot, badges, sound, notifier, now });
    this.items = new Map();            // `${deviceId}:${id}` → {deviceId, a, el, parts, busy, sel}
    this.collapsed = false;
    this.list = el('div', 'apv-list');
    this.head = el('div', 'apv-bar');
    this.title = el('b', 'apv-title');
    this.notifyBtn = this.notifier ? this.notifier.createToggleButton('btn small ghost apv-notify') : null;
    this.toggle = el('button', 'btn small apv-toggle', tt('Gizle'));
    this.toggle.type = 'button';
    this.toggle.addEventListener('click', () => { this.collapsed = !this.collapsed; this._chrome(); });
    if (this.notifyBtn) this.head.append(this.title, this.notifyBtn, this.toggle);
    else this.head.append(this.title, this.toggle);
    this.banner.replaceChildren(this.head, this.list);
    this._timer = 0;
    hub.on('event', (dev, m) => this._onEvent(dev, m));
    hub.on('change', () => this._pruneOffline());
    this._chrome();
  }

  get count() { return this.items.size; }

  _onEvent(deviceId, m) {
    if (!m || typeof m.type !== 'string') return;
    if (m.type === 'approval.request') this.add(deviceId, m.approval);
    else if (m.type === 'approval.resolved' && typeof m.approval === 'string') this.remove(deviceId, m.approval);
  }

  /** Cards of a device whose channel is gone can't be answered; the device re-sends them after the next handshake. */
  _pruneOffline() {
    let changed = false;
    for (const [k, it] of this.items) {
      if (!this.hub.isReady(it.deviceId)) { it.el.remove(); this.items.delete(k); changed = true; }
    }
    if (changed) this._chrome();
  }

  add(deviceId, raw) {
    const a = normalizeApproval(raw);
    if (!a || a.expiresAt <= this.now()) return false;
    const key = `${deviceId}:${a.id}`;
    if (this.items.has(key)) return false;
    const it = { deviceId, a, busy: false, sel: a.questions.map(() => new Set()) };
    this._build(it);
    this.items.set(key, it);
    this.list.prepend(it.el);
    this._chrome();
    this._alert();
    return true;
  }

  remove(deviceId, id) {
    const key = `${deviceId}:${id}`;
    const it = this.items.get(key);
    if (!it) return;
    it.el.remove();
    this.items.delete(key);
    this._chrome();
  }

  _alert() {
    try { if (navigator.vibrate) navigator.vibrate(VIBRATE_MS); } catch { /* not allowed without a gesture */ }
    try { if (this.sound && !this.sound.muted && this.sound.playAlert) this.sound.playAlert(); } catch { /* ignore */ }
    try { if (this.notifier) this.notifier.notifyApproval(); } catch { /* ignore */ }
  }

  _build(it) {
    const { a } = it;
    const card = el('article', 'apv-card');
    card.dataset.approval = a.id;
    card.setAttribute('aria-label', a.ask === 'question' ? tt('Soru') : tt('İzin isteği'));
    const head = el('header', 'apv-head');
    const dev = this.hub.device(it.deviceId);
    head.append(el('span', 'apv-tool', a.ask === 'question' ? tt('Soru') : (a.tool || tt('Araç'))),
      el('span', 'apv-agent', [a.agent, a.project, dev ? dev.label : ''].filter(Boolean).join(' · ')));
    const time = el('span', 'apv-time');
    time.setAttribute('aria-label', tt('Kalan süre'));
    head.appendChild(time);
    card.appendChild(head);
    if (a.summary) card.appendChild(el('code', 'apv-summary', a.summary));
    if (summaryWasCut(a.summary)) {
      const cut = el('p', 'apv-cut small', tt('Komut kısaltıldı: sonunu görmeden onaylamayın. Tamamı bilgisayardaki Claude Code penceresinde.'));
      cut.setAttribute('role', 'note');
      card.appendChild(cut);
    }
    a.questions.forEach((q, qi) => card.appendChild(this._question(it, q, qi)));
    const actions = el('div', 'apv-actions');
    const ok = el('button', 'btn accent', a.ask === 'question' ? tt('Gönder') : tt('Onayla'));
    const no = el('button', 'btn danger', tt('Reddet'));
    ok.type = 'button'; no.type = 'button';
    ok.dataset.apv = 'allow'; no.dataset.apv = 'deny';
    ok.addEventListener('click', () => this.answer(it, 'allow'));
    no.addEventListener('click', () => this.answer(it, 'deny'));
    actions.append(ok, no);
    const msg = el('p', 'apv-msg small');
    msg.setAttribute('role', 'status');
    card.append(actions, msg);
    it.el = card;
    it.parts = { time, ok, no, msg };
    this._sync(it);
  }

  _question(it, q, qi) {
    const fs = el('fieldset', 'apv-q');
    fs.appendChild(el('legend', 'apv-qtext', q.header ? `${q.header}: ${q.question}` : q.question));
    const name = `apv-${it.a.id}-${qi}`;
    q.options.forEach((o, oi) => {
      const lab = el('label', 'apv-opt');
      const inp = document.createElement('input');
      inp.type = q.multiSelect ? 'checkbox' : 'radio';
      inp.name = name;
      inp.value = String(oi);
      inp.addEventListener('change', () => {
        const s = it.sel[qi];
        if (!q.multiSelect) s.clear();
        if (inp.checked) s.add(oi); else s.delete(oi);
        this._sync(it);
      });
      lab.append(inp, el('span', 'apv-olabel', o.label));
      if (o.description) lab.appendChild(el('span', 'apv-odesc muted small', o.description));
      fs.appendChild(lab);
    });
    return fs;
  }

  _ready(it) { return it.a.ask !== 'question' || it.sel.every((s) => s.size > 0); }

  _sync(it) {
    it.parts.ok.disabled = it.busy || !this._ready(it);
    it.parts.no.disabled = it.busy;
  }

  async answer(it, decision) {
    if (it.busy) return;
    const msg = { type: 'approval.answer', approval: it.a.id, decision };
    if (decision === 'allow' && it.a.ask === 'question') {
      if (!this._ready(it)) return;
      msg.answers = it.sel.map((s) => [...s].sort((x, y) => x - y));
    }
    it.busy = true;
    this._sync(it);
    it.parts.msg.textContent = tt('Gönderiliyor…');
    try {
      await this.hub.request(it.deviceId, msg);
      this.remove(it.deviceId, it.a.id);
    } catch (e) {
      const code = e && e.code;
      it.parts.msg.textContent = APPROVAL_ERROR_TEXT[code] || tt('Cevap gönderilemedi.');
      if (code === 'unknown_approval') {
        setTimeout(() => this.remove(it.deviceId, it.a.id), 2500);
      } else {
        it.busy = false;
        this._sync(it);
      }
    }
  }

  _tick() {
    const now = this.now();
    for (const [, it] of this.items) {
      const left = it.a.expiresAt - now;
      if (left <= 0) { this.remove(it.deviceId, it.a.id); continue; }
      it.parts.time.textContent = countdownText(left);
    }
  }

  /** Move the list onto the Cihazlar page while it is open (call with the page state). */
  placeOnPage(open) {
    const host = open && this.pageSlot ? this.pageSlot : this.banner;
    if (this.list.parentNode !== host) host.appendChild(this.list);
    this._chrome();
  }

  _chrome() {
    const n = this.items.size;
    if (this.notifier) this.notifier.setSourceCount('approvals', n);
    const onPage = !!(this.pageSlot && this.list.parentNode === this.pageSlot);
    this.banner.hidden = n === 0 || onPage;
    if (this.pageSlot) this.pageSlot.hidden = n === 0;
    this.title.textContent = n === 1 ? tt('1 onay bekliyor') : tt('{n} onay bekliyor', { n });
    this.toggle.textContent = this.collapsed ? tt('Göster') : tt('Gizle');
    this.toggle.setAttribute('aria-expanded', this.collapsed ? 'false' : 'true');
    this.list.hidden = this.collapsed && !onPage;
    for (const b of this.badges) {
      if (!b) continue;
      if (n) b.dataset.count = String(n); else delete b.dataset.count;
    }
    if (n && !this._timer) {
      this._tick();
      this._timer = setInterval(() => this._tick(), APPROVAL_TICK_MS);
    } else if (!n && this._timer) {
      clearInterval(this._timer);
      this._timer = 0;
    }
  }
}
