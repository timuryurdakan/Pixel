// Pixel Office v4-C — workflow templates and runs (A10, V3.md §20 "v4-C / A10").
// The connector keeps the templates (built-in + its own workflows.json); this page lists them, starts one with a folder,
// the user's request, the step models and a cost cap, and follows `workflow.state` events (ids, states and numbers only).
// Device strings reach the DOM only through textContent. Shown only for devices whose device.info.features has
// "workflows" (older connectors: no button, no events).

import { ControlError, errorText } from './control.js';
import { CLAUDE_MODELS } from './devices.js';
import { PROFILE_TEXT } from './prompt.js';
import { TemplateBuilder } from './template_builder.js';
import { PreviewDrawer } from './preview.js';
import { t as tt } from './i18n.js';

export const WORKFLOWS_FEATURE = 'workflows';
export const BRIEF_MAX = 4096;               // = pixel_connector/workflows.py BRIEF_MAX (UTF-8 bytes)
export const COST_MIN = 0.01;                // = COST_BOUNDS
export const COST_MAX = 20;
export const MAX_STEPS = 8;
const RUN_ID_RE = /^w_[A-Za-z0-9_-]{10}$/;

/** S26-04: the template's guardrails.maxCostUsd is the highest cap a start may ask for (the connector refuses more). */
function costCeiling(t) {
  const v = t && t.limits ? t.limits.maxCostUsd : NaN;
  return Number.isFinite(v) && v >= COST_MIN && v <= COST_MAX ? v : COST_MAX;
}
const SHOW_FINISHED = 3;                     // finished runs listed per device

export const STATE_TEXT = {
  waiting: 'Bekliyor', running: tt('Çalışıyor'), approval: tt('Onay bekliyor'), done: 'Bitti', error: 'Hata', cancelled: tt('İptal'),
};
const STATE_MARK = { waiting: '…', running: '●', approval: '?', done: '✓', error: '✗', cancelled: '■' };
const ENGINE_TEXT = { claude: 'Claude Code', ollama: 'Ollama' };

/** Why a run or step stopped (codes of pixel_connector/workflows.py), else the generic device error text. */
export const CODE_TEXT = {
  cost_cap: tt('Maliyet tavanı aşıldı'),
  max_turns: tt('Tur sınırı aşıldı'),
  timeout: tt('Süre sınırı doldu'),
  denied: 'Reddedildi',
  approval_timeout: tt('Onay süresi doldu (reddedildi sayıldı)'),
  cancelled: tt('İptal edildi'),
  stopped: tt('Bağlayıcı kapandı'),
  budget: tt('Bütçe sınırına ulaşıldı'),
  error: tt('Adım hata verdi'),
  revoked: tt('Başlatan tarayıcının eşleşmesi kaldırıldı'),
};

export function codeText(code) {
  if (!code) return '';
  return Object.hasOwn(CODE_TEXT, code) ? CODE_TEXT[code] : errorText(code).replace(/\.$/, '');
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

/** A device whose connector understands workflow.* (v4-C) and is connected. */
export function supportsWorkflows(dev) {
  return !!(dev && dev.ready && dev.info && Array.isArray(dev.info.features) && dev.info.features.includes(WORKFLOWS_FEATURE));
}

export const isActive = (run) => !!run && ['waiting', 'running', 'approval'].includes(run.state);

/** Shape check of a `run` from the device before it is stored or drawn. */
export function validRun(run) {
  return !!(run && typeof run === 'object' && typeof run.id === 'string' && RUN_ID_RE.test(run.id)
    && Object.hasOwn(STATE_TEXT, run.state) && Array.isArray(run.steps) && run.steps.length >= 1
    && run.steps.length <= MAX_STEPS
    && run.steps.every((s) => s && typeof s.id === 'string' && Object.hasOwn(STATE_TEXT, s.state)));
}

const money = (n) => `$${(Number.isFinite(n) ? n : 0).toFixed(2)}`;

/** "2/4 adım · $0.05 / $1.00 · 5 tur" */
export function progressText(run) {
  const finished = run.steps.filter((s) => s.state === 'done').length;
  const bits = [tt('{done}/{all} adım', { done: finished, all: run.steps.length })];
  const cap = run.limits && Number.isFinite(run.limits.maxCostUsd) ? ` / ${money(run.limits.maxCostUsd)}` : '';
  bits.push(`${money(run.costUsd)}${cap}`);
  if (Number.isFinite(run.turns) && run.turns > 0) bits.push(`${run.turns} tur`);
  return bits.join(' · ');
}

/**
 * Compare two snapshots of one run → what the page should do: tasks that started (to follow them in the agent panel
 * and the Task Board), tasks that ended, and an office walk from the previous agent to the next one (A9 animation).
 */
export function runChanges(prev, next) {
  const out = { started: [], ended: [], walk: null };
  const before = new Map((prev && prev.steps || []).map((s) => [s.id, s]));
  let lastDoneAgent = null;
  for (const s of next.steps) {
    const p = before.get(s.id);
    if (s.type === 'agent' && s.task && s.state === 'running' && !(p && p.task === s.task && p.state === 'running')) {
      out.started.push({ agent: s.agent, task: s.task, role: s.role });
      if (prev && lastDoneAgent && lastDoneAgent !== s.agent) out.walk = { from: lastDoneAgent, to: s.agent };
    }
    if (s.type === 'agent' && s.task && ['done', 'error', 'cancelled'].includes(s.state) && p && p.state === 'running') {
      out.ended.push({ agent: s.agent, task: s.task, state: s.state });
    }
    if (s.type === 'agent' && s.state === 'done' && s.agent) lastDoneAgent = s.agent;
  }
  return out;
}

/** Runs per paired device, kept current from `workflow.list` and `workflow.state`. */
export class WorkflowStore {
  /**
   * @param {object} o
   * @param {import('./control.js').DeviceHub} o.hub
   * @param {(deviceId:string, changes:object, run:object) => void} [o.onRun]   after every state of a run
   */
  constructor({ hub, onRun = null }) {
    this.hub = hub;
    this.onRun = onRun;
    this.runs = new Map();              // `${device}.${run}` → {device, run}
    this.templates = new Map();         // device → {list, invalid}
    this.loaded = new Set();            // devices whose list was fetched on this connection
    this._listeners = new Set();
    hub.on('event', (dev, m) => { if (m && m.type === 'workflow.state' && !m.re) this.put(dev, m.run); });
    hub.on('change', () => this._autoload());
    hub.on('resync', (dev) => { this.loaded.delete(dev); this._autoload(); });
  }

  onChange(fn) { this._listeners.add(fn); }
  _changed() { for (const fn of this._listeners) fn(); }

  _autoload() {
    for (const d of this.hub.devices.values()) {
      if (!d.ready) this.loaded.delete(d.id);
      else if (supportsWorkflows(d) && !this.loaded.has(d.id)) this.load(d.id).catch(() => {});
    }
  }

  async load(dev) {
    this.loaded.add(dev);
    let r;
    try {
      r = await this.hub.request(dev, { type: 'workflow.list' });
    } catch (e) {
      this.loaded.delete(dev);
      throw e;
    }
    this.templates.set(dev, { list: Array.isArray(r.templates) ? r.templates : [], invalid: Number(r.invalid) || 0 });
    for (const run of Array.isArray(r.runs) ? r.runs : []) this.put(dev, run, { quiet: true });
    this._changed();
    return r;
  }

  put(dev, run, { quiet = false } = {}) {
    if (!validRun(run)) return;
    const k = `${dev}.${run.id}`;
    const prev = this.runs.get(k);
    this.runs.set(k, { device: dev, run });
    if (this.onRun && !quiet) this.onRun(dev, runChanges(prev ? prev.run : null, run), run);
    if (!quiet) this._changed();
  }

  /** [{device, run}] newest first; finished runs only the last few per device. */
  list(dev = null) {
    const all = [...this.runs.values()].filter((x) => !dev || x.device === dev)
      .sort((a, b) => (b.run.startedAt || 0) - (a.run.startedAt || 0));
    const finished = new Map();
    return all.filter((x) => {
      if (isActive(x.run)) return true;
      const n = (finished.get(x.device) || 0) + 1;
      finished.set(x.device, n);
      return n <= SHOW_FINISHED;
    });
  }

  active() { return this.list().filter((x) => isActive(x.run)); }
}

/** The "Şablonlar" dialog: start a template, follow, approve/deny and cancel runs. */
export class WorkflowDialog {
  /**
   * @param {object} o
   * @param {HTMLDialogElement} o.dialog
   * @param {import('./control.js').DeviceHub} o.hub
   * @param {WorkflowStore} o.store
   * @param {(text:string, opts?:object)=>void} o.toast
   */
  constructor({ dialog, hub, store, toast = () => {}, previewDialog = null, storage = null }) {
    Object.assign(this, { dialog, hub, store, toast });
    const f = this.form = dialog.querySelector('#wf-form');
    this.f = f.elements;
    this.runsBox = dialog.querySelector('#wf-runs');
    this.desc = dialog.querySelector('#wf-desc');
    this.stepsBox = dialog.querySelector('#wf-steps');
    this.limits = dialog.querySelector('#wf-limits');
    this.err = dialog.querySelector('#wf-error');
    this.briefRow = dialog.querySelector('#wf-brief-row');
    this.subRow = dialog.querySelector('#wf-sub-row');
    this.wsCache = new Map();
    this.busy = new Set();              // run ids with a request in flight
    this._initBuilder(storage);
    this._initPreview(previewDialog);
    this.f.device.addEventListener('change', () => this._loadDevice());
    this.f.template.addEventListener('change', () => this._template());
    this.f.workspace.addEventListener('change', () => this._ws());
    f.addEventListener('submit', (e) => { e.preventDefault(); this.start(); });
    dialog.querySelector('[data-wf-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
    store.onChange(() => { if (this.dialog.open) { this.renderRuns(); this._updateStart(); } });
  }

  _initBuilder(storage) {
    const doc = this.dialog.ownerDocument || document;
    let bar = this.dialog.querySelector('.wf-builder-bar');
    if (!bar) {
      bar = el('div', 'wf-builder-bar');
      const toggleBtn = el('button', 'btn small', tt('Kodsuz Şablon Oluşturucu'));
      toggleBtn.type = 'button';
      toggleBtn.dataset.action = 'toggle-builder';
      bar.appendChild(toggleBtn);
      this.dialog.insertBefore(bar, this.form);
    }
    let box = this.dialog.querySelector('#wf-builder');
    if (!box) {
      box = el('section', 'wf-builder');
      box.id = 'wf-builder';
      box.hidden = true;
      this.dialog.insertBefore(box, this.form);
    }
    this.builderBox = box;
    this.builder = new TemplateBuilder({
      container: box,
      storage,
      onReload: () => this._loadDevice(),
    });
    const toggleBtn = bar.querySelector('[data-action="toggle-builder"]');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => {
        this.builderBox.hidden = !this.builderBox.hidden;
        toggleBtn.classList.toggle('primary', !this.builderBox.hidden);
        if (!this.builderBox.hidden) this.builder.render();
      });
    }
  }

  _initPreview(previewDialog) {
    this.previewDialog = previewDialog || null;
    this.preview = previewDialog
      ? new PreviewDrawer({ dialog: previewDialog, doc: this.dialog.ownerDocument || document })
      : null;
  }

  _getPreviewDrawer() {
    if (this.preview) return this.preview;
    const doc = this.dialog.ownerDocument || document;
    let dlg = this.previewDialog || doc.getElementById('dlg-preview');
    if (!dlg && doc.body) {
      dlg = doc.createElement('dialog');
      dlg.id = 'dlg-preview';
      dlg.className = 'dlg wide preview-dlg';
      doc.body.appendChild(dlg);
    }
    if (!dlg) return null;
    if (!dlg._previewDrawer) dlg._previewDrawer = new PreviewDrawer({ dialog: dlg, doc });
    this.preview = dlg._previewDrawer;
    return this.preview;
  }

  get isOpen() { return this.dialog.open; }

  _devices() { return [...this.hub.devices.values()].filter((d) => supportsWorkflows(d)); }
  _device() { return this.hub.device(this.f.device.value); }

  open() {
    const devs = this._devices();
    if (!devs.length) { this.toast(tt('İş akışlarını destekleyen çevrimiçi cihaz yok (bağlayıcı 3.4.0 ve üstü).'), { error: true, ms: 5000 }); return; }
    const sel = this.f.device;
    const keep = sel.value;
    sel.replaceChildren(...devs.map((d) => { const o = el('option', null, d.label); o.value = d.id; return o; }));
    sel.value = devs.some((d) => d.id === keep) ? keep : devs[0].id;
    sel.closest('label').hidden = devs.length < 2;
    this._error('');
    if (!this.dialog.open) this.dialog.showModal();
    this.renderRuns();
    this._loadDevice();
  }

  close() { if (this.dialog.open) this.dialog.close(); }

  async _loadDevice() {
    const d = this._device();
    if (!d) return;
    this.f.template.replaceChildren(el('option', null, tt('Yükleniyor…')));
    try {
      await this.store.load(d.id);
      if (!this.wsCache.has(d.id)) {
        const r = await this.hub.request(d.id, { type: 'workspace.list' });
        this.wsCache.set(d.id, Array.isArray(r.workspaces) ? r.workspaces : []);
      }
    } catch (e) {
      this._error(e instanceof ControlError ? e.text : tt('Şablonlar alınamadı.'));
      return;
    }
    if (this._device() !== d) return;
    const t = this.store.templates.get(d.id) || { list: [], invalid: 0 };
    const keep = this.f.template.value;
    this.f.template.replaceChildren(...t.list.map((x) => { const o = el('option', null, x.name); o.value = x.id; return o; }));
    if (t.list.some((x) => x.id === keep)) this.f.template.value = keep;
    const ws = this.f.workspace;
    const list = this.wsCache.get(d.id) || [];
    ws.replaceChildren(...list.map((w) => { const o = el('option', null, w.name); o.value = w.id; return o; }));
    if (list.some((w) => w.id === 'isler')) ws.value = 'isler';         // ~/PixelOfficeIsler (Phase 17 default)
    this._template();
    if (t.invalid) this._error(tt('Cihazdaki workflows.json dosyasında {n} geçersiz şablon atlandı.', { n: t.invalid }));
  }

  _tpl() {
    const t = this.store.templates.get(this.f.device.value);
    return t ? t.list.find((x) => x.id === this.f.template.value) || null : null;
  }

  _template() {
    const t = this._tpl();
    const d = this._device();
    this.desc.textContent = t && t.description ? t.description : '';
    this.briefRow.hidden = !(t && t.needsBrief);
    this.f.cost.value = t && t.limits ? String(t.limits.maxCostUsd) : '1';
    this.f.cost.max = String(costCeiling(t));
    this.f.sub.value = t && t.folder ? t.folder : '';
    this.limits.textContent = t && t.limits
      ? tt('Sınırlar: en fazla {turns} tur, {min} dk. Aşılırsa akış durur, sonraki adım başlamaz.', { turns: t.limits.maxTurns, min: Math.round(t.limits.timeoutS / 60) })
      : '';
    this.stepsBox.replaceChildren();
    for (const s of t ? t.steps : []) this.stepsBox.appendChild(this._stepRow(s, d));
    this._ws();
    this._updateStart();
  }

  _stepRow(s, d) {
    const li = el('li', `wf-step-opt${s.type === 'approval' ? ' is-approval' : ''}`);
    li.dataset.step = s.id;
    li.appendChild(el('b', 'wf-step-role', s.role));
    if (s.type === 'approval') {
      li.appendChild(el('span', 'muted small', tt(' — sizin onayınız (Onayla / Reddet)')));
      return li;
    }
    const eng = (d && d.info && d.info.engines) || {};
    const row = el('div', 'wf-step-row');
    const engSel = el('select', 'wf-engine');
    engSel.name = `engine-${s.id}`;
    engSel.setAttribute('aria-label', `${s.role}: motor`);
    for (const e of s.engines || [s.engine]) {
      const ok = !!(eng[e] && eng[e].available);
      const o = el('option', null, ok ? ENGINE_TEXT[e] || e : `${ENGINE_TEXT[e] || e} (cihazda yok)`);
      o.value = e;
      o.disabled = !ok;
      engSel.appendChild(o);
    }
    const firstOk = [...engSel.options].find((o) => !o.disabled);
    engSel.value = [...engSel.options].some((o) => o.value === s.engine && !o.disabled) ? s.engine : (firstOk ? firstOk.value : s.engine);
    engSel.hidden = engSel.options.length < 2;
    const modelSel = el('select', 'wf-model');
    modelSel.name = `model-${s.id}`;
    modelSel.setAttribute('aria-label', `${s.role}: model`);
    const fillModels = () => {
      const e = engSel.value;
      const models = e === 'claude' ? CLAUDE_MODELS
        : ((eng.ollama && eng.ollama.models) || []).map((m) => [m, m]);
      modelSel.replaceChildren(...models.map(([v, t]) => { const o = el('option', null, t); o.value = v; return o; }));
      if (!models.length) { const o = el('option', null, tt('Model yok')); o.value = ''; modelSel.appendChild(o); }
      if (s.model && models.some(([v]) => v === s.model) && e === s.engine) modelSel.value = s.model;
    };
    engSel.addEventListener('change', fillModels);
    fillModels();
    const prof = (PROFILE_TEXT[s.profile] || { label: s.profile }).label;
    const note = el('span', 'wf-step-note muted small', engSel.hidden ? `${ENGINE_TEXT[s.engine] || s.engine} · ${tt('İzin: {label}', { label: prof })}` : `${tt('İzin: {label}', { label: prof })} (Claude)`);
    row.append(engSel, modelSel, note);
    li.appendChild(row);
    return li;
  }

  _ws() {
    const list = this.wsCache.get(this.f.device.value) || [];
    const w = list.find((x) => x.id === this.f.workspace.value);
    this.subRow.hidden = !(w && Array.isArray(w.children));
  }

  _error(text) {
    this.err.textContent = text;
    this.err.hidden = !text;
  }

  _updateStart() {
    const busy = this.store.list(this.f.device.value).some((x) => isActive(x.run));
    this.f.start.disabled = busy || !this._tpl() || !!this.starting;
    this.f.start.title = busy ? tt('Bu cihazda bir iş akışı çalışıyor; bitince yenisini başlatabilirsiniz.') : '';
  }

  /** The workflow.start request from the form, or {error}. */
  request() {
    const t = this._tpl();
    if (!t) return { error: tt('Bir şablon seçin.') };
    const brief = this.f.brief.value.trim();
    if (t.needsBrief && !brief) return { error: tt('Ne istediğinizi kısaca yazın.') };
    if (new TextEncoder().encode(brief).length > BRIEF_MAX) return { error: tt('İstek çok uzun (en fazla 4 KB).') };
    const cost = Number(String(this.f.cost.value).replace(',', '.'));
    const ceiling = costCeiling(t);
    if (!(cost >= COST_MIN && cost <= ceiling)) {
      return { error: tt('Maliyet tavanı {min}–{max} $ arasında olmalı (şablonun sınırı {max} $).', { min: COST_MIN, max: ceiling }) };
    }
    if (!this.f.workspace.value) return { error: tt('Bir çalışma klasörü seçin.') };
    const steps = {};
    for (const s of t.steps) {
      if (s.type !== 'agent') continue;
      const engine = this.form.querySelector(`[name="engine-${s.id}"]`).value;
      const model = this.form.querySelector(`[name="model-${s.id}"]`).value || null;
      if (engine === 'ollama' && !model) return { error: `${s.role}: ${tt('Ollama için bir model seçin.')}` };
      steps[s.id] = { engine, model };
    }
    const sub = this.subRow.hidden ? null : this.f.sub.value.trim() || null;
    return { msg: { type: 'workflow.start', template: t.id, workspace: { id: this.f.workspace.value, sub }, brief,
      maxCostUsd: cost, steps } };
  }

  async start() {
    const d = this._device();
    const r = this.request();
    if (r.error) { this._error(r.error); return; }
    this._error('');
    this.starting = true;
    this._updateStart();
    try {
      const res = await this.hub.request(d.id, r.msg, { timeout: 130000 });   // may wait for a confirmation on the computer
      this.store.put(d.id, res.run);
      this.toast(tt('İş akışı başladı'), { ms: 3000 });
      this.f.brief.value = '';
      this.runsBox.scrollIntoView({ block: 'nearest' });
    } catch (e) {
      const t = { workspace_denied: tt('Bu klasör cihazın izin listesinde değil ya da kullanılamıyor.'),
        busy: tt('Bu cihazda bir iş akışı ya da adım ajanı zaten çalışıyor.'),
        not_allowed: tt('Bu cihaz uzaktan ajan oluşturmaya izin vermiyor (iş akışları ajan oluşturur).'),
        unknown_template: tt('Şablon cihazda bulunamadı.') }[e && e.code];
      this._error(t || (e instanceof ControlError ? e.text : tt('İş akışı başlatılamadı.')));
    } finally {
      this.starting = false;
      this._updateStart();
    }
  }

  async _act(dev, run, msg, okText) {
    if (this.busy.has(run.id)) return;
    this.busy.add(run.id);
    this.renderRuns();
    try {
      await this.hub.request(dev, { ...msg, run: run.id });
      if (okText) this.toast(okText, { ms: 2500 });
    } catch (e) {
      this.toast(e instanceof ControlError ? e.text : tt('İşlem yapılamadı'), { error: true });
    } finally {
      this.busy.delete(run.id);
      this.renderRuns();
    }
  }

  renderRuns() {
    const box = this.runsBox;
    const items = this.store.list();
    box.replaceChildren();
    box.hidden = !items.length;
    if (!items.length) return;
    box.appendChild(el('h3', null, tt('İş akışları')));
    for (const { device, run } of items) box.appendChild(this._runCard(device, run));
  }

  _runCard(dev, run) {
    const card = el('article', `wf-run st-${run.state}`);
    card.dataset.run = run.id;
    const head = el('div', 'wf-run-head');
    head.append(el('b', 'wf-run-name', run.name || run.template), el('span', `wf-badge st-${run.state}`, STATE_TEXT[run.state]));
    card.appendChild(head);
    const ws = run.workspace || {};
    const folder = ws.name ? `${ws.name}${ws.sub ? '/' + ws.sub : ''}` : '';
    card.appendChild(el('div', 'wf-run-meta muted small', `${folder ? tt('Klasör: ') + folder + ' · ' : ''}${progressText(run)}`));
    const ol = el('ol', 'wf-step-list');
    for (const s of run.steps) {
      const li = el('li', `wf-step st-${s.state}`);
      li.append(el('span', 'wf-step-st', STATE_MARK[s.state] || '·'), el('span', 'wf-step-name', s.role || s.id));
      let extra = STATE_TEXT[s.state];
      if (s.type === 'agent' && s.model) extra += ` · ${s.model}`;
      if (s.code && s.state !== 'done') extra += ` · ${codeText(s.code)}`;
      li.appendChild(el('span', 'wf-step-meta muted small', extra));
      if (s.type === 'agent' && s.state === 'done' && typeof s.task === 'string' && s.task) {
        const prevBtn = el('button', 'btn small ghost wf-preview-btn', tt('Önizle'));
        prevBtn.type = 'button';
        prevBtn.dataset.action = 'preview-step';
        prevBtn.addEventListener('click', () => this._previewStep(dev, run, s));
        li.appendChild(prevBtn);
      }
      ol.appendChild(li);
    }
    card.appendChild(ol);
    if (run.code && !isActive(run)) card.appendChild(el('p', 'wf-run-note small', codeText(run.code)));
    const actions = el('div', 'wf-run-actions');
    const busy = this.busy.has(run.id);
    const cur = run.steps.find((s) => s.id === run.current);
    if (run.state === 'approval' && cur && cur.type === 'approval') {
      const left = Number.isFinite(cur.expiresAt) ? Math.max(0, Math.round((cur.expiresAt - Date.now()) / 60000)) : null;
      card.appendChild(el('p', 'wf-run-note small', tt('Önceki adımın çıktısını o ajanın panelinde okuyun.') + (left !== null ? ' ' + tt('Yanıt verilmezse {n} dk sonra reddedilmiş sayılır.', { n: left }) : '')));
      const yes = el('button', 'btn small accent wf-approve', 'Onayla');
      const no = el('button', 'btn small wf-deny', 'Reddet');
      for (const b of [yes, no]) { b.type = 'button'; b.disabled = busy; }
      yes.addEventListener('click', () => this._act(dev, run, { type: 'workflow.answer', decision: 'approve' }, tt('Onaylandı')));
      no.addEventListener('click', () => this._act(dev, run, { type: 'workflow.answer', decision: 'deny' }, 'Reddedildi'));
      actions.append(yes, no);
    }
    if (isActive(run)) {
      const stop = el('button', 'btn small ghost wf-cancel', tt('İptal'));
      stop.type = 'button';
      stop.disabled = busy;
      stop.addEventListener('click', () => this._act(dev, run, { type: 'workflow.cancel' }, tt('İş akışı durduruluyor')));
      actions.appendChild(stop);
    }
    if (actions.childElementCount) card.appendChild(actions);
    return card;
  }

  async _previewStep(dev, run, step) {
    if (!step || typeof step.task !== 'string' || !step.task) return;
    try {
      const r = await this.hub.request(dev, { type: 'task.get', task: step.task });
      const text = r && typeof r.result === 'string' ? r.result : '';
      const drawer = this._getPreviewDrawer();
      if (drawer) {
        drawer.open({
          title: `${run.name || run.template || tt('İş akışı')} · ${step.role || step.id}`,
          content: text,
        });
      }
    } catch (e) {
      this.toast(e instanceof ControlError ? e.text : tt('Adım çıktısı alınamadı.'), { error: true });
    }
  }
}
