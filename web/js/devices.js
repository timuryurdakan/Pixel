// Pixel Office v3 — "Cihazlar" page, 3-step "Cihaz ekle" pairing wizard and the "Yeni ajan" dialog
// (V3.md §11.4, §11.5, §11.6). Device data is rendered with textContent only.

import { ControlError, errorText, PAIR_ERROR_TEXT, relayErrorCode } from './control.js';
import { formatCode } from './e2e.js';
import { fmtCompact, gaugeEl, totalCostLabel, claudeLimitText, ENGINE_LABELS, WEIGHT_NOTE } from './usage.js';
import { PROFILE_TEXT } from './prompt.js';

export const RUNTIME_POLL_MS = 10000;
const OS_LABEL = { linux: 'Linux', darwin: 'macOS', windows: 'Windows' };
const RT_STATE = { running: 'Çalışıyor', stopped: 'Durdu', starting: 'Başlatılıyor', unknown: 'Durum bilinmiyor' };

const RELAY_STATUS_TEXT = {
  live: 'Röle: bağlı', connecting: 'Röle: bağlanıyor…', reconnecting: 'Röle: yeniden bağlanıyor…',
  quota: 'Röle şu an meşgul ya da günlük kota doldu', idle: 'Röle: bağlı değil',
};
const RELAY_STOP_TEXT = {
  relay_csp: 'Röle: güvenlik politikası (CSP) röle adresini engelliyor',
  unauthorized: 'Röle: bu tarayıcıdaki oda anahtarı geçersiz. Katılım bağlantısını (#key=… içeren) yeniden açın.',
  relay_not_configured: 'Röle: sunucuda röle ayarlı değil',
  relay_ticket: 'Röle: sunucu bilet vermedi',
};

/**
 * "Röle: …" line of the Cihazlar page (v2.md H2). When the relay client stopped for good (idle + error) the reason is
 * shown instead of a plain "bağlı değil": a wrong key in this browser and a server without relay settings differ.
 */
export function relayStatusText(status, relayError) {
  if (status === 'idle' && relayError) {
    const code = relayErrorCode(relayError);
    if (Object.hasOwn(RELAY_STOP_TEXT, code)) return RELAY_STOP_TEXT[code];
  }
  return Object.hasOwn(RELAY_STATUS_TEXT, status) ? RELAY_STATUS_TEXT[status] : RELAY_STATUS_TEXT.idle;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function btn(text, cls = 'btn small', onClick = null) {
  const b = el('button', cls, text);
  b.type = 'button';
  if (onClick) b.addEventListener('click', onClick);
  return b;
}

function ago(ms) {
  if (!ms) return '';
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 90) return 'az önce';
  if (s < 3600) return `${Math.round(s / 60)} dk önce`;
  if (s < 86400) return `${Math.round(s / 3600)} sa önce`;
  return `${Math.round(s / 86400)} gün önce`;
}

/** "4 dk sonra boşalır" from Ollama's expires_at (ISO string or ms). */
export function expiresText(v) {
  const t = typeof v === 'number' ? v : Date.parse(v || '');
  if (!Number.isFinite(t)) return '';
  const min = Math.round((t - Date.now()) / 60000);
  if (min <= 0) return 'birazdan boşalır';
  if (min > 60 * 24 * 365) return 'sürekli yüklü';
  if (min < 60) return `${min} dk sonra boşalır`;
  return `${Math.round(min / 60)} sa sonra boşalır`;
}

function gb(bytes) {
  return Number.isFinite(bytes) && bytes > 0
    ? `${new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 1 }).format(bytes / 1e9)} GB VRAM` : '';
}

const confirmBox = (text) => window.confirm(text);

// Phase 17: a paired device with an empty workspace allow-list. Static text plus the local command; nothing here
// comes from the browser or the device, and the allow-list can only be widened on the computer itself.
export const NO_WORKSPACE_CMD = 'pixel-connector workspace add ~/projem';
export const REMOTE_APPROVALS_CMD = 'pixel-connector remote-approvals on';
export const NO_WORKSPACE_HINT = `Bu cihazda izinli klasör yok. Bilgisayarda şunu çalıştırın: ${NO_WORKSPACE_CMD} `
  + '(ya da varsayılan ~/PixelOfficeIsler klasörünü kullanmak için bağlayıcıyı güncelleyin).';

// v2.md E1: the connector version on the device card and an update hint. The device reports the SHA-256 of the .pyz it
// runs (`device.info.pyz`, connector ≥ 3.1.0); the server publishes the current one in /connector/SHA256SUMS. Older
// connectors don't report it, so a version below 3.1.0 counts as outdated too. Text only, never HTML.
export const MIN_CONNECTOR = '3.1.0';
export const UPDATE_HINT = 'Güncelleme var: bu bilgisayarda kurulum komutunu yeniden çalıştırın '
  + '(Cihazlar → Cihaz ekle → kurulum komutu). Ayarlar ve eşleşmeler korunur.';
// Connector 3.5.0 has `pixel-connector update`; older ones only update by running the install command again.
export const UPDATE_COMMAND_SINCE = '3.5.0';
export const UPDATE_CMD = 'pixel-connector update';
export const UPDATE_HINT_CMD = `Güncelleme var: bu bilgisayarda şunu çalıştırın: ${UPDATE_CMD} `
  + '(ayarlar ve eşleşmeler korunur).';
const SHA_RE = /^[0-9a-f]{64}$/;
const VERSION_RE = /^(\d{1,4})\.(\d{1,4})\.(\d{1,4})/;

/** The pixel-connector.pyz SHA-256 from a SHA256SUMS text, or null. */
export function parsePublishedSha(text) {
  for (const line of String(text || '').split(/\r?\n/)) {
    const m = /^([0-9a-f]{64}) {2}pixel-connector\.pyz$/.exec(line);
    if (m) return m[1];
  }
  return null;
}

let publishedShaPromise = null;
/** Fetched once per page; null when unavailable (offline, local file, error). */
export function publishedConnectorSha(fetchFn = (u, o) => fetch(u, o)) {
  if (!publishedShaPromise) {
    publishedShaPromise = Promise.resolve()
      .then(() => fetchFn('/connector/SHA256SUMS', { cache: 'no-store' }))
      .then((r) => (r && r.ok ? r.text() : ''))
      .then(parsePublishedSha)
      .catch(() => null);
  }
  return publishedShaPromise;
}

function versionLess(a, b) {
  const x = VERSION_RE.exec(String(a || ''));
  const y = VERSION_RE.exec(String(b || ''));
  if (!x || !y) return false;
  for (let i = 1; i <= 3; i += 1) {
    if (Number(x[i]) !== Number(y[i])) return Number(x[i]) < Number(y[i]);
  }
  return false;
}

/** "Bağlayıcı 3.1.0" or '' when the device didn't say. */
export function connectorLabel(info) {
  const v = info && typeof info.connector === 'string' ? info.connector.slice(0, 16) : '';
  return v ? `Bağlayıcı ${v}` : '';
}

/** True when the device runs an older connector than the one the server publishes. */
export function connectorOutdated(info, publishedSha) {
  if (!info || typeof info.connector !== 'string') return false;
  if (versionLess(info.connector, MIN_CONNECTOR)) return true;
  return typeof info.pyz === 'string' && SHA_RE.test(info.pyz) && typeof publishedSha === 'string'
    && SHA_RE.test(publishedSha) && info.pyz !== publishedSha;
}

/** True when the device's connector has the `pixel-connector update` command (3.5.0 or newer). */
export function hasUpdateCommand(info) {
  if (!info || typeof info.connector !== 'string' || !VERSION_RE.test(info.connector)) return false;
  return !versionLess(info.connector, UPDATE_COMMAND_SINCE);
}

/** The device card's update hint for this connector: the update command (3.5.0+) or the install command again. */
export function updateHint(info) {
  return hasUpdateCommand(info) ? UPDATE_HINT_CMD : UPDATE_HINT;
}

/** True when a device's `workspace.list` result is known and empty. */
export function noWorkspaces(list) { return Array.isArray(list) && list.length === 0; }

/** The hint as a <p>, built with textContent only (the command in a <code>). */
export function workspaceHintEl(cls = 'muted small') {
  const p = el('p', cls);
  p.dataset.wsHint = '';
  p.append(document.createTextNode('Bu cihazda izinli klasör yok. Bilgisayarda şunu çalıştırın: '),
    el('code', null, NO_WORKSPACE_CMD),
    document.createTextNode(' (ya da varsayılan ~/PixelOfficeIsler klasörünü kullanmak için bağlayıcıyı güncelleyin).'));
  return p;
}

// =====================================================================================================
// Devices page
// =====================================================================================================
export class DevicesPage {
  /**
   * @param {object} o
   * @param {import('./control.js').DeviceHub} o.hub
   * @param {HTMLElement} o.root          section#devices-page
   * @param {PairWizard} o.wizard
   * @param {NewAgentDialog} o.newAgent
   * @param {Function} o.toast
   * @param {(control:{device:string, agent:string}) => void} o.onSelectAgent
   * @param {(open:boolean) => void} [o.onToggle]
   */
  constructor({ hub, root, wizard, newAgent, toast, onSelectAgent, onToggle = null }) {
    Object.assign(this, { hub, root, wizard, newAgent, toast, onSelectAgent, onToggle });
    this.body = root.querySelector('.dev-body');
    this.relayEl = root.querySelector('.dev-relay');
    this.cards = new Map();          // deviceId → {el, parts…}
    this.data = new Map();           // deviceId → {usage, runtimes, peers, rtBusy:{}, rtMsg:{}, peersOpen}
    this.isOpen = false;
    this._poll = 0;
    root.querySelector('[data-dev-close]').addEventListener('click', () => this.close());
    root.querySelector('[data-dev-add]').addEventListener('click', () => this.wizard.open());
    root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && this.isOpen && !document.querySelector('dialog[open]')) { e.stopPropagation(); this.close(); } });
    root.addEventListener('click', (e) => { if (e.target === root) this.close(); });
    hub.on('change', () => { if (this.isOpen) this.render(); });
    hub.on('status', () => { if (this.isOpen) this._relayLine(); });
    hub.on('event', (dev, m) => {
      if (m && m.type === 'usage' && !m.re && m.scope === 'device' && this.isOpen) { this._d(dev).usage = m; this._renderUsage(dev); }
    });
  }

  _d(id) {
    if (!this.data.has(id)) this.data.set(id, { usage: null, runtimes: null, peers: null, rtBusy: {}, rtMsg: {}, peersOpen: false, loaded: false });
    return this.data.get(id);
  }

  open() {
    if (this.isOpen) { this.refreshAll(); return; }
    this.isOpen = true;
    this.root.hidden = false;
    document.body.classList.add('devices-open');
    this.hub.need('devices', true);
    this.render();
    this.refreshAll();
    clearInterval(this._poll);
    this._poll = setInterval(() => this._pollRuntimes(), RUNTIME_POLL_MS);
    const f = this.root.querySelector('.dev-panel');
    if (f) f.focus({ preventScroll: true });
    if (this.onToggle) this.onToggle(true);
  }

  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.root.hidden = true;
    document.body.classList.remove('devices-open');
    clearInterval(this._poll);
    this._poll = 0;
    this.hub.need('devices', false);
    if (this.onToggle) this.onToggle(false);
  }

  /** Load usage, runtimes and peers of every ready device. */
  refreshAll() {
    for (const d of this.hub.devices.values()) if (d.ready) this._refreshDevice(d.id);
  }

  async _refreshDevice(id) {
    const st = this._d(id);
    st.loaded = true;
    const h = this.hub;
    await Promise.all([
      h.request(id, { type: 'usage.get', period: 'day', agent: null }).then((u) => { st.usage = u; this._renderUsage(id); }).catch(() => {}),
      h.request(id, { type: 'runtime.list' }).then((r) => { st.runtimes = r.runtimes || []; this._renderRuntimes(id); }).catch(() => {}),
      h.request(id, { type: 'peer.list' }).then((r) => { st.peers = r.peers || []; this._renderPeers(id); }).catch(() => {}),
      h.request(id, { type: 'workspace.list' }).then((r) => {
        st.workspaces = Array.isArray(r.workspaces) ? r.workspaces : [];
        if (this.newAgent && this.newAgent.wsCache) this.newAgent.wsCache.set(id, st.workspaces);
        this._renderAgents(id);
      }).catch(() => {}),
      h.refreshAgents(id).catch(() => {}),
    ]);
  }

  _pollRuntimes() {
    if (!this.isOpen || document.visibilityState === 'hidden') return;
    for (const d of this.hub.devices.values()) {
      if (!d.ready) continue;
      const st = this._d(d.id);
      if (Object.keys(st.rtBusy).length) continue;
      this.hub.request(d.id, { type: 'runtime.list' }).then((r) => { st.runtimes = r.runtimes || []; this._renderRuntimes(d.id); }).catch(() => {});
    }
  }

  _relayLine() {
    const s = this.hub.relayStatus;
    this.relayEl.textContent = relayStatusText(s, this.hub.relayError);
    this.relayEl.dataset.status = s;
  }

  // -- render ------------------------------------------------------------------------------------
  render() {
    this._relayLine();
    const devs = [...this.hub.devices.values()].sort((a, b) => (b.online - a.online) || (a.pairedAt - b.pairedAt));
    if (!devs.length) {
      this.cards.clear();
      const empty = el('div', 'dev-empty');
      empty.appendChild(el('p', null, 'Bu tarayıcı henüz hiçbir cihazla eşleşmedi.'));
      empty.appendChild(el('p', 'muted', 'Bilgisayarınıza bağlayıcıyı kurun ve "Cihaz ekle" ile eşleştirin. Sonra o bilgisayardaki Claude Code ve Ollama ajanlarına buradan görev verebilirsiniz.'));
      empty.appendChild(btn('+ Cihaz ekle', 'btn accent', () => this.wizard.open()));
      this.body.replaceChildren(empty);
      return;
    }
    const seen = new Set();
    const frag = [];
    for (const d of devs) {
      seen.add(d.id);
      let c = this.cards.get(d.id);
      if (!c) { c = this._card(d.id); this.cards.set(d.id, c); }
      this._renderHead(d.id);
      this._renderAgents(d.id);
      this._renderUsage(d.id);
      this._renderRuntimes(d.id);
      this._renderPeers(d.id);
      c.el.classList.toggle('offline', !d.ready);
      frag.push(c.el);
      if (d.ready && !this._d(d.id).loaded) this._refreshDevice(d.id);
    }
    for (const id of [...this.cards.keys()]) if (!seen.has(id)) this.cards.delete(id);
    this.body.replaceChildren(...frag);
  }

  _card(id) {
    const c = { el: el('article', 'dev-card') };
    c.el.dataset.device = id;
    c.head = el('header', 'dev-head');
    c.usage = el('div', 'dev-usage');
    c.agents = el('div', 'dev-agents');
    c.runtimes = el('div', 'dev-runtimes');
    c.peers = el('div', 'dev-peers');
    c.budget = this._deviceBudget(id);
    const foot = el('div', 'dev-foot');
    foot.appendChild(btn('Bu tarayıcının eşleşmesini kaldır', 'btn small ghost danger-btn', () => this._unpair(id)));
    c.el.append(c.head, c.usage, c.budget, c.agents, c.runtimes, c.peers, foot);
    return c;
  }

  _renderHead(id) {
    const d = this.hub.device(id);
    const c = this.cards.get(id);
    if (!d || !c) return;
    c.head.replaceChildren();
    const dot = el('span', `dev-dot ${d.ready ? 'on' : d.status === 'connecting' ? 'wait' : 'off'}`, d.ready ? '●' : '○');
    const name = el('b', 'dev-name', d.label);
    const meta = [];
    if (d.info) {
      if (OS_LABEL[d.info.os]) meta.push(OS_LABEL[d.info.os]);
      const label = connectorLabel(d.info);
      if (label) meta.push(label);
    }
    meta.push(d.ready ? 'çevrimiçi' : d.status === 'connecting' ? 'bağlanıyor…' : d.status === 'error' ? 'güvenli bağlantı kurulamadı' : 'çevrimdışı');
    const m = el('span', 'dev-meta muted', meta.join(' · '));
    c.head.append(dot, name, m);
    const headToken = (c.headToken = (c.headToken || 0) + 1);
    if (d.ready && d.info) {
      const info = d.info;
      publishedConnectorSha().then((sha) => {
        if (c.headToken !== headToken || !connectorOutdated(info, sha)) return;
        c.head.appendChild(el('p', 'dev-hint dev-update small', updateHint(info)));
      });
    }
    if (d.ready && d.info && d.info.approvals && typeof d.info.approvals.remote === 'boolean') {
      c.head.appendChild(el('p', 'dev-hint muted small dev-apv', d.info.approvals.remote
        ? 'Uzaktan onay: açık — Claude Code izin istekleri ve soruları burada da görünür.'
        : `Uzaktan onay kapalı. Açmak için bu bilgisayarda: ${REMOTE_APPROVALS_CMD}`));
    }
    if (d.status === 'error' || (!d.ready && d.online)) {
      c.head.appendChild(btn('Yeniden dene', 'btn small', () => this.hub.connect(id).catch((e) => this.toast(e instanceof ControlError ? e.text : 'Bağlanılamadı', { error: true }))));
    }
    if (!d.ready) {
      const hint = el('p', 'dev-hint muted small');
      hint.textContent = d.status === 'error'
        ? 'Cihaz bu tarayıcıyı tanımıyor olabilir (eşleşme kaldırılmış). Yeniden eşleştirmeyi deneyin.'
        : 'Bağlayıcı çalışıyor mu? Cihazda: systemctl --user status pixel-connector';
      c.head.appendChild(hint);
    }
  }

  _renderUsage(id) {
    const c = this.cards.get(id);
    const d = this.hub.device(id);
    if (!c || !d) return;
    const u = this._d(id).usage;
    c.usage.replaceChildren();
    if (!d.ready || !u) return;
    const t = u.totals || {};
    const line = el('div', 'usage-line');
    const tok = el('span', null, `Bugün ${fmtCompact(t.tokens)} token`);
    tok.title = WEIGHT_NOTE;
    const hasClaude = !!(d.info && d.info.engines && d.info.engines.claude && d.info.engines.claude.available);
    const cl = totalCostLabel(t.costUsd, hasClaude ? 'claude' : 'ollama', d.info && d.info.engines && d.info.engines.claude ? d.info.engines.claude.auth : null);
    const cost = el('span', 'muted', ` · ${cl.text}`);
    cost.title = cl.title;
    line.append(tok, cost, el('span', 'muted', ` · ${t.tasks || 0} görev`));
    c.usage.appendChild(line);
    for (const b of u.budgets || []) c.usage.appendChild(gaugeEl(b, { showScope: true }));
    if (hasClaude) c.usage.appendChild(el('div', 'usage-note muted small', claudeLimitText(u.claudeLimit)));
    const editable = !!(d.info && d.info.remote && d.info.remote.budgetEdit);
    c.budget.hidden = !editable;
  }

  _deviceBudget(id) {
    const det = el('details', 'budget-edit');
    det.appendChild(el('summary', null, 'Cihaz bütçesi'));
    const f = el('form', 'budget-form');
    f.setAttribute('autocomplete', 'off');
    const sel = (name, opts, label) => {
      const s = el('select');
      s.name = name;
      s.setAttribute('aria-label', label);
      for (const [v, t] of opts) { const o = el('option', null, t); o.value = v; s.appendChild(o); }
      return s;
    };
    const period = sel('period', [['day', 'Günlük'], ['week', 'Haftalık'], ['month', 'Aylık']], 'Dönem');
    const lim = document.createElement('input');
    lim.type = 'number';
    lim.name = 'limit';
    lim.min = '0';
    lim.step = 'any';
    lim.inputMode = 'decimal';
    lim.placeholder = 'Sınır';
    lim.setAttribute('aria-label', 'Bütçe sınırı');
    const unit = sel('unit', [['usd', '$ (tahmini)'], ['tokens', 'token']], 'Birim');
    const action = sel('action', [['warn', 'uyar'], ['block', 'durdur']], 'Aşılınca');
    const save = el('button', 'btn small accent', 'Kaydet');
    save.type = 'submit';
    const del = btn('Kaldır', 'btn small ghost', async () => {
      try {
        const u = await this.hub.request(id, { type: 'budget.set', scope: 'device', period: period.value, delete: true });
        this._d(id).usage = u;
        this._renderUsage(id);
        this.toast('Cihaz bütçesi kaldırıldı');
      } catch (e) { this.toast(e instanceof ControlError ? e.text : 'Kaldırılamadı', { error: true }); }
    });
    f.append(period, lim, unit, action, save, del);
    f.appendChild(el('p', 'muted small', 'Cihazdaki tüm ajanlar için ortak sınır. "durdur": sınır dolunca yeni görev kabul edilmez.'));
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const n = Number(String(lim.value).replace(',', '.'));
      if (!(n > 0)) { this.toast('Sınır sıfırdan büyük olmalı', { error: true }); return; }
      try {
        const u = await this.hub.request(id, { type: 'budget.set', scope: 'device', period: period.value, unit: unit.value, limit: n, action: action.value });
        this._d(id).usage = u;
        this._renderUsage(id);
        det.open = false;
        this.toast('Cihaz bütçesi kaydedildi');
      } catch (err) { this.toast(err instanceof ControlError ? err.text : 'Kaydedilemedi', { error: true }); }
    });
    det.appendChild(f);
    det.hidden = true;
    return det;
  }

  _renderAgents(id) {
    const c = this.cards.get(id);
    const d = this.hub.device(id);
    if (!c || !d) return;
    c.agents.replaceChildren();
    const head = el('div', 'dev-sec-head');
    head.appendChild(el('h3', null, `Ajanlar (${d.agents.size})`));
    const add = btn('+ Yeni ajan', 'btn small accent', () => this.newAgent.open(id));
    add.disabled = !d.ready;
    head.appendChild(add);
    c.agents.appendChild(head);
    const claudeOk = !!(d.info && d.info.engines && d.info.engines.claude && d.info.engines.claude.available);
    if (d.ready && claudeOk && noWorkspaces(this._d(id).workspaces)) c.agents.appendChild(workspaceHintEl('dev-hint muted small'));
    if (!d.agents.size) {
      c.agents.appendChild(el('p', 'muted small', d.ready ? 'Bu cihazda henüz ajan yok.' : 'Cihaz çevrimiçi olunca ajanlar görünür.'));
      return;
    }
    const ul = el('ul', 'dev-agent-list');
    for (const a of d.agents.values()) {
      const li = el('li');
      const b = el('button', 'link-btn', a.name);
      b.type = 'button';
      b.title = 'Ofiste göster';
      b.addEventListener('click', () => this.onSelectAgent({ device: id, agent: a.key }));
      const where = [ENGINE_LABELS[a.engine] || a.engine];
      if (a.model) where.push(a.model);
      if (a.workspace && a.workspace.name) where.push(a.workspace.name + (a.workspace.sub ? '/' + a.workspace.sub : ''));
      const meta = el('span', 'muted small', ` ${where.join(' · ')}`);
      const today = a.today && Number.isFinite(a.today.tokens) ? el('span', 'muted small', ` · bugün ${fmtCompact(a.today.tokens)} token`) : null;
      const del = btn('Sil', 'btn small ghost', () => this._deleteAgent(id, a));
      del.disabled = !d.ready || !!a.task;
      if (a.task) del.title = 'Görev çalışırken silinemez; önce durdurun.';
      if (d.info && d.info.remote && d.info.remote.agentDelete === false) del.hidden = true;
      li.append(b, meta);
      if (today) li.appendChild(today);
      li.appendChild(del);
      ul.appendChild(li);
    }
    c.agents.appendChild(ul);
  }

  async _deleteAgent(id, a) {
    if (!confirmBox(`"${a.name}" ajanı silinsin mi? Karakter ofisten çıkar, sohbet geçmişi silinir.`)) return;
    try {
      await this.hub.deleteAgent(id, a.key);
      this.toast('Ajan silindi');
      this.render();
    } catch (e) { this.toast(e instanceof ControlError ? e.text : 'Silinemedi', { error: true }); }
  }

  _renderRuntimes(id) {
    const c = this.cards.get(id);
    const d = this.hub.device(id);
    if (!c || !d) return;
    const st = this._d(id);
    c.runtimes.replaceChildren();
    c.runtimes.appendChild(el('h3', null, 'Çalışma ortamları'));
    if (!d.ready) return;
    if (!st.runtimes) { c.runtimes.appendChild(el('p', 'muted small', 'Yükleniyor…')); return; }
    if (!st.runtimes.length) {
      c.runtimes.appendChild(el('p', 'muted small', 'Cihazda tanımlı çalışma ortamı yok (connector.json → runtimes).'));
      return;
    }
    const ul = el('ul', 'rt-list');
    for (const r of st.runtimes) {
      const li = el('li', 'rt');
      li.dataset.runtime = r.id;
      const row = el('div', 'rt-row');
      const state = ['running', 'stopped', 'starting'].includes(r.state) ? r.state : 'unknown';
      row.appendChild(el('span', 'rt-name', r.name || r.id));
      const s = el('span', `rt-state st-${state}`, `${state === 'running' ? '●' : '○'} ${RT_STATE[state]}`);
      row.appendChild(s);
      if (r.detail) row.appendChild(el('span', 'muted small', r.detail));
      const busy = st.rtBusy[r.id];
      const act = el('span', 'rt-actions');
      if (busy) {
        act.appendChild(el('span', 'muted small', busy));
      } else {
        if (r.canStart && state !== 'running') act.appendChild(btn('Başlat', 'btn rt-btn accent', () => this._rt(id, r, 'runtime.start')));
        if (r.canStop && state !== 'stopped') act.appendChild(btn('Durdur', 'btn rt-btn', () => this._rt(id, r, 'runtime.stop')));
      }
      row.appendChild(act);
      li.appendChild(row);
      if (st.rtMsg[r.id]) {
        const m = el('div', `rt-msg small ${st.rtMsg[r.id].error ? 'error' : 'muted'}`, st.rtMsg[r.id].text);
        li.appendChild(m);
      }
      if (Array.isArray(r.models) && r.models.length) {
        const ml = el('ul', 'rt-models');
        for (const m of r.models) {
          const mi = el('li');
          mi.appendChild(el('span', 'rt-model', m.name));
          const info = m.loaded
            ? ['Yüklü', gb(m.vramBytes), expiresText(m.expiresAt)].filter(Boolean).join(' · ')
            : (m.installed === false ? 'Kurulu değil' : 'Yüklü değil');
          mi.appendChild(el('span', 'muted small', info));
          const k = `${r.id}:${m.name}`;
          if (st.rtBusy[k]) mi.appendChild(el('span', 'muted small', st.rtBusy[k]));
          else if (state === 'running' && m.installed !== false) {
            mi.appendChild(m.loaded
              ? btn('Boşalt', 'btn small', () => this._model(id, r, m.name, 'model.unload'))
              : btn('Yükle', 'btn small', () => this._model(id, r, m.name, 'model.load')));
          }
          ml.appendChild(mi);
        }
        li.appendChild(ml);
      }
      ul.appendChild(li);
    }
    c.runtimes.appendChild(ul);
  }

  async _rt(id, r, type) {
    const st = this._d(id);
    const start = type === 'runtime.start';
    st.rtBusy[r.id] = start ? 'Başlatılıyor…' : 'Durduruluyor…';
    delete st.rtMsg[r.id];
    this._renderRuntimes(id);
    try {
      const res = await this.hub.request(id, { type, runtime: r.id }, { timeout: 45000 });
      if (!res.ok) {
        const code = res.exitCode === null || res.exitCode === undefined ? '' : ` (çıkış kodu ${res.exitCode})`;
        st.rtMsg[r.id] = { text: `${start ? 'Başlatılamadı' : 'Durdurulamadı'}${code}${res.message ? ': ' + String(res.message).slice(0, 300) : ''}`, error: true };
      } else {
        st.rtMsg[r.id] = { text: start ? 'Başlatıldı' : 'Durduruldu' };
      }
    } catch (e) {
      st.rtMsg[r.id] = { text: e instanceof ControlError ? e.text : 'İşlem başarısız oldu.', error: true };
    }
    delete st.rtBusy[r.id];
    try { const l = await this.hub.request(id, { type: 'runtime.list' }); st.runtimes = l.runtimes || []; } catch { /* keep */ }
    this._renderRuntimes(id);
    this.hub.connect(id).catch(() => {});
  }

  async _model(id, r, model, type) {
    const st = this._d(id);
    const k = `${r.id}:${model}`;
    st.rtBusy[k] = type === 'model.load' ? 'Yükleniyor…' : 'Boşaltılıyor…';
    this._renderRuntimes(id);
    try {
      const res = await this.hub.request(id, { type, runtime: r.id, model }, { timeout: 310000 });
      st.rtMsg[r.id] = res.ok ? { text: `${model}: ${type === 'model.load' ? 'yüklendi' : 'boşaltıldı'}` }
        : { text: `${model}: işlem başarısız${res.message ? ': ' + String(res.message).slice(0, 200) : ''}`, error: true };
    } catch (e) {
      st.rtMsg[r.id] = { text: e instanceof ControlError ? e.text : 'İşlem başarısız oldu.', error: true };
    }
    delete st.rtBusy[k];
    try { const l = await this.hub.request(id, { type: 'runtime.list' }); st.runtimes = l.runtimes || []; } catch { /* keep */ }
    this._renderRuntimes(id);
  }

  _renderPeers(id) {
    const c = this.cards.get(id);
    const d = this.hub.device(id);
    if (!c || !d) return;
    const st = this._d(id);
    c.peers.replaceChildren();
    if (!d.ready || !st.peers) return;
    const head = el('div', 'dev-sec-head');
    const names = st.peers.map((p) => (p.self ? 'Bu tarayıcı' : (p.label || 'Tarayıcı') + (p.lastSeen ? ` (${ago(p.lastSeen)})` : '')));
    head.appendChild(el('span', 'small', `Eşleşen tarayıcılar: ${names.join(' · ')}`));
    head.appendChild(btn(st.peersOpen ? 'Kapat' : 'Yönet', 'btn small ghost', () => { st.peersOpen = !st.peersOpen; this._renderPeers(id); }));
    c.peers.appendChild(head);
    if (!st.peersOpen) return;
    const ul = el('ul', 'peer-list');
    for (const p of st.peers) {
      const li = el('li');
      li.appendChild(el('span', null, p.self ? `${p.label || 'Tarayıcı'} (bu tarayıcı)` : (p.label || 'Tarayıcı')));
      li.appendChild(el('span', 'muted small', ` eşleşme ${ago(p.pairedAt)}${p.lastSeen ? ' · son görülme ' + ago(p.lastSeen) : ''}`));
      if (!p.self) li.appendChild(btn('Kaldır', 'btn small ghost', () => this._revoke(id, p)));
      ul.appendChild(li);
    }
    c.peers.appendChild(ul);
  }

  async _revoke(id, p) {
    if (!confirmBox('Bu tarayıcı artık bu cihaza görev veremez. Emin misiniz?')) return;
    try {
      await this.hub.request(id, { type: 'peer.revoke', bid: p.bid });
      const st = this._d(id);
      st.peers = (st.peers || []).filter((x) => x.bid !== p.bid);
      this._renderPeers(id);
      this.toast('Eşleşme kaldırıldı');
    } catch (e) { this.toast(e instanceof ControlError ? e.text : 'Kaldırılamadı', { error: true }); }
  }

  async _unpair(id) {
    const d = this.hub.device(id);
    if (!d) return;
    if (!confirmBox(`Bu tarayıcının "${d.label}" ile eşleşmesi kaldırılsın mı? Görev vermek için yeniden eşleştirmeniz gerekir.`)) return;
    const { remote } = await this.hub.unpair(id);
    this.data.delete(id);
    this.toast(remote ? 'Eşleşme kaldırıldı' : 'Eşleşme bu tarayıcıdan silindi. Cihazda da kaldırmak için: pixel-connector peers', { ms: 6000 });
    this.render();
  }
}

// =====================================================================================================
// Pairing wizard (§11.5)
// =====================================================================================================
// G4: `pixel-connector pair` also shows a QR of /?room=<id>#pair=<code>. main.js reads `pair` before the fragment is
// stripped from the address bar. Only the exact form the connector writes (12 Crockford characters) is accepted, and
// it is only filled in: the user still presses "Eşleştir" (a link from someone else must not pair this browser in
// one tap) and still confirms the SAS on the computer.
export const PAIR_LINK_RE = /^[0-9A-HJKMNP-TV-Z]{12}$/;
export const PAIR_LINK_TEXT = 'Kod QR\'dan yazıldı. Bilgisayarınızın terminalinde aynı kod görünüyorsa Eşleştir\'e basın.';

/** `#pair=` value → the formatted code, or null when it is not exactly a pairing code. */
export function pairCodeFromLink(raw) {
  return typeof raw === 'string' && PAIR_LINK_RE.test(raw) ? formatCode(raw) : null;
}

export class PairWizard {
  /**
   * @param {object} o
   * @param {HTMLDialogElement} o.dialog
   * @param {import('./control.js').DeviceHub} o.hub
   * @param {(name:string) => object} o.commands   install commands for this room (cloud.buildConnectorCommands)
   * @param {Function} o.toast
   * @param {(device) => void} [o.onPaired]
   */
  constructor({ dialog, hub, commands, toast, onPaired = null, copy }) {
    Object.assign(this, { dialog, hub, commands, toast, onPaired, copy });
    const $ = (s) => dialog.querySelector(s);
    this.steps = [...dialog.querySelectorAll('[data-step]')];
    this.form = $('#pair-form');
    this.input = $('#pair-code');
    this.status = $('#pair-status');
    this.sas = $('#pair-sas');
    this.submit = $('#pair-submit');
    this.busy = false;
    for (const b of dialog.querySelectorAll('[data-goto]')) b.addEventListener('click', () => this.go(Number(b.dataset.goto)));
    const tabs = [...dialog.querySelectorAll('.tab[data-ostab]')];
    for (const t of tabs) {
      t.addEventListener('click', () => {
        for (const x of tabs) x.setAttribute('aria-selected', x === t ? 'true' : 'false');
        for (const p of dialog.querySelectorAll('[data-ospane]')) p.hidden = p.dataset.ospane !== t.dataset.ostab;
      });
    }
    if (/Windows/i.test(navigator.userAgent)) tabs.find((t) => t.dataset.ostab === 'win')?.click();
    else if (/Mac OS X|Macintosh/.test(navigator.userAgent)) tabs.find((t) => t.dataset.ostab === 'mac')?.click();
    this.input.addEventListener('input', () => {
      const pos = this.input.selectionStart === this.input.value.length;
      const f = formatCode(this.input.value);
      if (f !== this.input.value) { this.input.value = f; if (pos) this.input.setSelectionRange(f.length, f.length); }
      this.status.textContent = '';
      this.status.className = 'pair-status';
    });
    this.form.addEventListener('submit', (e) => { e.preventDefault(); this.run(); });
    dialog.addEventListener('click', (e) => { if (e.target === dialog && !this.busy) dialog.close(); });
    dialog.addEventListener('cancel', (e) => { if (this.busy) e.preventDefault(); });
    dialog.addEventListener('close', () => this.hub.need('pairdlg', false));
    // v2.md H2: opening the dialog starts the relay; when it is refused (wrong key, no relay on the server) say so
    // before the user types a code, instead of failing only after "Eşleştir".
    hub.on('status', (st, detail) => {
      if (!this.dialog.open || this.busy || st !== 'idle' || !detail || !detail.error) return;
      const code = relayErrorCode(detail.error);
      this._status(PAIR_ERROR_TEXT[code] || errorText(code), 'error');
    });
    const nameInput = $('#pair-name');
    if (nameInput) nameInput.addEventListener('input', () => this._fill());
  }

  _fill() {
    const name = (this.dialog.querySelector('#pair-name') || {}).value || '';
    const cmds = this.commands(name);
    for (const box of this.dialog.querySelectorAll('.cmd[data-pcmd]')) {
      box.textContent = '';
      const pre = el('pre');
      const code = el('code', null, cmds[box.dataset.pcmd] || '');
      pre.appendChild(code);
      const b = btn('Kopyala', 'btn small copy-btn', async () => {
        const ok = await this.copy(cmds[box.dataset.pcmd] || '');
        this.toast(ok ? 'Komut kopyalandı' : 'Kopyalanamadı, metni elle seçin', { error: !ok });
      });
      box.append(pre, b);
    }
  }

  open(step = 1) {
    this._fill();
    this.go(step);
    this.input.value = '';
    this.sas.hidden = true;
    this.status.textContent = '';
    this.status.className = 'pair-status';
    this.submit.disabled = false;
    this.input.disabled = false;
    this.hub.need('pairdlg', true);
    if (!this.dialog.open) this.dialog.showModal();
    if (step === 3) this.input.focus();
  }

  /** Open step 3 with a code from the QR link (pairCodeFromLink). → false (and nothing opens) for a bad value. */
  openWithCode(raw) {
    const code = pairCodeFromLink(raw);
    if (!code) return false;
    this.open(3);
    this.input.value = code;
    this._status(PAIR_LINK_TEXT);
    return true;
  }

  go(n) {
    for (const s of this.steps) s.hidden = Number(s.dataset.step) !== n;
    const lab = this.dialog.querySelector('#pair-steplabel');
    if (lab) lab.textContent = `Adım ${n} / 3`;
    if (n === 3) setTimeout(() => this.input.focus(), 0);
  }

  _status(text, cls = '') {
    this.status.textContent = text;
    this.status.className = `pair-status ${cls}`;
  }

  async run() {
    if (this.busy) return;
    const code = this.input.value;
    this.busy = true;
    this.submit.disabled = true;
    this.input.disabled = true;
    this.sas.hidden = true;
    this._status('Doğrulanıyor…');
    try {
      const dev = await this.hub.pair(code, {
        onStage: (s) => this._status(s === 'relay' ? 'Röleye bağlanılıyor…' : 'Doğrulanıyor…'),
        onSas: (sas) => {
          this.sas.hidden = false;
          this.sas.replaceChildren();
          this.sas.appendChild(document.createTextNode('Terminalde '));
          this.sas.appendChild(el('b', 'sas-code', sas));
          this.sas.appendChild(document.createTextNode(' görünüyorsa orada '));
          this.sas.appendChild(el('b', null, 'e'));
          this.sas.appendChild(document.createTextNode(' yazıp onaylayın.'));
          this._status('Cihazda onay bekleniyor…');
        },
      });
      this.sas.hidden = true;
      this._status(`Eşleşti: ${dev.label} ✓`, 'ok');
      this.toast(`Eşleşti: ${dev.label}`);
      if (this.onPaired) this.onPaired(dev);
      this.input.value = '';
      this.submit.disabled = false;
      this.input.disabled = false;
    } catch (e) {
      this.sas.hidden = true;
      const code2 = e && e.code;
      this._status(PAIR_ERROR_TEXT[code2] || errorText(code2), 'error');
      this.submit.disabled = false;
      this.input.disabled = false;
      this.input.select();
    } finally {
      this.busy = false;
    }
  }
}

// =====================================================================================================
// New agent dialog (§11.4)
// =====================================================================================================
export const CLAUDE_MODELS = [['', 'Varsayılan'], ['sonnet', 'sonnet'], ['opus', 'opus'], ['haiku', 'haiku']];

export class NewAgentDialog {
  constructor({ dialog, hub, toast, onCreated }) {
    Object.assign(this, { dialog, hub, toast, onCreated });
    const f = this.form = dialog.querySelector('#agent-form');
    this.f = f.elements;
    this.err = dialog.querySelector('#agent-error');
    this.autoNote = dialog.querySelector('#agent-auto-note');
    this.engineBox = dialog.querySelector('#agent-engines');
    this.subRow = dialog.querySelector('#agent-sub-row');
    this.subList = dialog.querySelector('#agent-sub-list');
    this.wsHint = workspaceHintEl();
    this.wsHint.hidden = true;
    const wsLabel = this.f.workspace.closest('label');
    if (wsLabel) wsLabel.after(this.wsHint);
    this.wsCache = new Map();
    this.f.device.addEventListener('change', () => this._loadDevice());
    this.f.workspace.addEventListener('change', () => this._ws());
    this.f.profile.addEventListener('change', () => { this.autoNote.hidden = this.f.profile.value !== 'auto'; });
    this.f.useBudget.addEventListener('change', () => this._budgetRow());
    f.addEventListener('submit', (e) => { e.preventDefault(); this.create(); });
    dialog.querySelector('[data-agent-cancel]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  }

  _budgetRow() {
    for (const n of ['budgetLimit', 'budgetUnit', 'budgetAction']) this.f[n].disabled = !this.f.useBudget.checked;
  }

  open(deviceId = null) {
    const ready = [...this.hub.devices.values()].filter((d) => d.ready);
    if (!ready.length) { this.toast('Çevrimiçi eşleşmiş cihaz yok. Önce Cihazlar → Cihaz ekle.', { error: true, ms: 5000 }); return; }
    const sel = this.f.device;
    sel.replaceChildren();
    for (const d of this.hub.devices.values()) {
      const o = el('option', null, `${d.label} (${d.ready ? 'çevrimiçi' : 'çevrimdışı'})`);
      o.value = d.id;
      o.disabled = !d.ready;
      sel.appendChild(o);
    }
    sel.value = deviceId && this.hub.isReady(deviceId) ? deviceId : ready[0].id;
    this.f.name.value = '';
    this.f.sub.value = '';
    this.f.useBudget.checked = false;
    this.f.budgetLimit.value = '3';
    this._budgetRow();
    this.err.hidden = true;
    this.autoNote.hidden = true;
    this.f.create.disabled = false;
    this._loadDevice();
    if (!this.dialog.open) this.dialog.showModal();
    this.f.name.focus();
  }

  _device() { return this.hub.device(this.f.device.value); }

  async _loadDevice() {
    const d = this._device();
    if (!d) return;
    const info = d.info || {};
    const eng = info.engines || {};
    // engines
    this.engineBox.replaceChildren();
    const opts = [];
    const c = eng.claude || {};
    opts.push({ value: 'claude', label: 'Claude Code', ok: !!c.available, why: 'cihazda bulunamadı' });
    const o = eng.ollama || {};
    opts.push({ value: 'ollama', label: 'Ollama', ok: !!o.available, why: 'çalışmıyor ya da kapalı' });
    for (const s of Array.isArray(eng.openai) ? eng.openai : []) opts.push({ value: `openai:${s.id}`, label: s.name || s.id, ok: !!s.available, why: 'sunucu çalışmıyor' });
    let first = null;
    for (const op of opts) {
      const lab = el('label', `radio${op.ok ? '' : ' disabled'}`);
      const r = document.createElement('input');
      r.type = 'radio';
      r.name = 'engine';
      r.value = op.value;
      r.disabled = !op.ok;
      if (op.ok && !first) first = r;
      r.addEventListener('change', () => this._engine());
      lab.append(r, document.createTextNode(` ${op.label}`));
      if (!op.ok) lab.appendChild(el('span', 'muted small', ` (${op.why})`));
      this.engineBox.appendChild(lab);
    }
    if (first) first.checked = true;
    // profiles
    const ps = this.f.profile;
    ps.replaceChildren();
    const profiles = Array.isArray(info.profiles) && info.profiles.length ? info.profiles
      : ['readonly', 'edit', 'dev', 'auto'].map((id) => ({ id, enabled: id !== 'auto' }));
    for (const p of profiles) {
      const t = PROFILE_TEXT[p.id];
      if (!t) continue;
      const op = el('option', null, p.enabled ? t.label : `${t.label} (yalnız cihazda açıksa)`);
      op.value = p.id;
      op.disabled = !p.enabled;
      op.title = t.tip;
      ps.appendChild(op);
    }
    ps.value = 'edit';
    this.autoNote.hidden = true;
    // workspaces
    const wsSel = this.f.workspace;
    wsSel.replaceChildren(el('option', null, 'Yükleniyor…'));
    let list = this.wsCache.get(d.id);
    if (!list || !list.length) {         // an empty list is re-asked: the user may have just run `workspace add`
      try {
        list = (await this.hub.request(d.id, { type: 'workspace.list' })).workspaces || [];
        this.wsCache.set(d.id, list);
      } catch (e) {
        list = null;                     // unknown, not empty: no "no workspaces" hint
        this._error(e instanceof ControlError ? e.text : 'Klasörler alınamadı');
      }
    }
    if (this._device() !== d) return;
    this.workspaces = list;
    this._engine();
  }

  _engineValue() {
    const r = this.form.querySelector('input[name="engine"]:checked');
    return r ? r.value : null;
  }

  _engine() {
    const d = this._device();
    const ev = this._engineValue();
    const eng = (d && d.info && d.info.engines) || {};
    const isClaude = ev === 'claude';
    // models
    const ms = this.f.model;
    ms.replaceChildren();
    let models = [];
    if (isClaude) models = CLAUDE_MODELS;
    else if (ev === 'ollama') models = (eng.ollama && eng.ollama.models || []).map((m) => [m, m]);
    else if (ev && ev.startsWith('openai:')) {
      const s = (eng.openai || []).find((x) => `openai:${x.id}` === ev);
      models = (s && s.models || []).map((m) => [m, m]);
    }
    for (const [v, t] of models) { const o = el('option', null, t); o.value = v; ms.appendChild(o); }
    if (!models.length) { const o = el('option', null, 'Model yok'); o.value = ''; ms.appendChild(o); }
    // workspace: required for Claude, optional for chat models
    const wsSel = this.f.workspace;
    const keep = wsSel.value;
    wsSel.replaceChildren();
    if (!isClaude) { const o = el('option', null, '(yok, yalnızca sohbet)'); o.value = ''; wsSel.appendChild(o); }
    for (const w of this.workspaces || []) { const o = el('option', null, w.name); o.value = w.id; wsSel.appendChild(o); }
    const none = isClaude && noWorkspaces(this.workspaces);
    if (none) { const o = el('option', null, 'İzinli klasör yok'); o.value = ''; wsSel.appendChild(o); }
    this.wsHint.hidden = !none;
    if ([...wsSel.options].some((o) => o.value === keep)) wsSel.value = keep;
    this.f.profile.disabled = !isClaude;
    this.dialog.querySelector('#agent-profile-row').hidden = !isClaude;
    this.dialog.querySelector('#agent-local-note').hidden = isClaude || !ev;
    if (!this.f.name.value || this.f.name.dataset.auto === '1') {
      this.f.name.value = isClaude ? 'Kâtip' : 'Yerel';
      this.f.name.dataset.auto = '1';
    }
    this.f.name.oninput = () => { this.f.name.dataset.auto = '0'; };
    this._ws();
  }

  _ws() {
    const w = (this.workspaces || []).find((x) => x.id === this.f.workspace.value);
    const kids = w && Array.isArray(w.children) ? w.children : null;
    this.subRow.hidden = !kids;
    this.subList.replaceChildren();
    for (const k of kids || []) { const o = document.createElement('option'); o.value = k; this.subList.appendChild(o); }
  }

  _error(text) {
    this.err.textContent = text;
    this.err.hidden = !text;
  }

  async create() {
    const d = this._device();
    const ev = this._engineValue();
    if (!d || !ev) { this._error('Bir motor seçin.'); return; }
    const name = this.f.name.value.trim();
    if (!name || [...name].length > 24) { this._error('Ad 1–24 karakter olmalı.'); return; }
    const engine = ev.startsWith('openai:') ? 'openai' : ev;
    const model = this.f.model.value || null;
    if (engine !== 'claude' && !model) { this._error('Bir model seçin.'); return; }
    const wsId = this.f.workspace.value;
    if (engine === 'claude' && !wsId) {
      this._error(noWorkspaces(this.workspaces) ? NO_WORKSPACE_HINT : 'Claude Code ajanı için bir çalışma klasörü seçin.');
      return;
    }
    const sub = this.f.sub.value.trim() || null;
    let budget = null;
    if (this.f.useBudget.checked) {
      const n = Number(String(this.f.budgetLimit.value).replace(',', '.'));
      if (!(n > 0)) { this._error('Bütçe sınırı sıfırdan büyük olmalı.'); return; }
      budget = { period: 'day', unit: this.f.budgetUnit.value, limit: n, action: this.f.budgetAction.value };
    }
    const msg = { type: 'agent.create', name, engine, model, workspace: wsId ? { id: wsId, sub: this.subRow.hidden ? null : sub } : null,
      profile: engine === 'claude' ? this.f.profile.value : 'edit', budget };
    if (engine === 'openai') msg.engineId = ev.slice(7);
    this._error('');
    this.f.create.disabled = true;
    try {
      const r = await this.hub.request(d.id, msg, { timeout: 130000 });
      this.hub.putAgent(d.id, r.agent);
      this.dialog.close();
      this.toast(`Ajan oluşturuldu: ${r.agent.name}. Birazdan ofise girer.`, { ms: 5000 });
      if (this.onCreated) this.onCreated({ device: d.id, agent: r.agent.key });
    } catch (e) {
      const t = { workspace_denied: 'Bu klasör cihazın izin listesinde değil.', not_allowed: 'Bu cihaz uzaktan ajan oluşturmaya izin vermiyor.',
        engine_unavailable: 'Bu motor cihazda bulunamadı.' }[e && e.code];
      this._error(t || (e instanceof ControlError ? e.text : 'Ajan oluşturulamadı.'));
    } finally {
      this.f.create.disabled = false;
    }
  }
}

