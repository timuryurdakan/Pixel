// Pixel Office v3 — prompt box + streamed output in the details panel (V3.md §11.2, §11.3, §11.6).
// Device text (model output, tool summaries, names, errors) reaches the DOM only through textContent / text nodes;
// MdStream renders a "markdown-lite" subset (paragraphs, fenced code, inline code, bold) with DOM nodes, never
// innerHTML, and re-renders only the unfinished last line on every chunk, so long outputs stay cheap.

import { errorText, ControlError } from './control.js';
import {
  fmtClock, fmtDuration, fmtCompact, liveTokensText, costLabel, totalCostLabel, gaugeEl, claudeLimitText,
  weightedTokens, WEIGHT_NOTE, PERIOD_NOUN,
} from './usage.js';
import { makeExportActions, defaultNotifier } from './notify.js';

const MAX_TASK_BLOCKS = 12;      // older task blocks are dropped from the output log
const MAX_OUT_NODES = 3000;      // per task block (very long outputs keep their tail)
/** v4-B: same limit as the connector's `task.start.handoff.note` (pixel_connector/tasks.py HANDOFF_NOTE_MAX). */
export const HANDOFF_NOTE_MAX = 4096;

export const PROFILE_TEXT = {
  readonly: { label: 'Sadece okuma', tip: 'Klasördeki dosyaları okuyabilir; düzenleyemez, komut çalıştıramaz.' },
  edit: { label: 'Düzenleyebilir', tip: 'Dosya okuyup düzenleyebilir, komut çalıştıramaz.' },
  dev: { label: 'Geliştirici', tip: 'Dosya düzenleyebilir ve yalnızca cihazda izin verilen komutları çalıştırabilir.' },
  auto: { label: 'Otomatik', tip: 'Claude her adımı sizin yerinize, bir güvenlik sınıflandırıcısıyla onaylar.' },
};

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

/** Inline markdown-lite: `code` and **bold** → DOM nodes. Everything else stays literal text. */
export function appendInline(parent, text) {
  const re = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
    const tok = m[0];
    if (tok[0] === '`') parent.appendChild(el('code', 'md-ic', tok.slice(1, -1)));
    else parent.appendChild(el('strong', null, tok.slice(2, -2)));
    last = m.index + tok.length;
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
}

/** Streaming markdown-lite renderer into `root`. */
export class MdStream {
  constructor(root) {
    this.root = root;
    this.line = '';            // unfinished last line
    this.block = null;         // current <p>
    this.blockEmpty = true;
    this.code = null;          // current <code> inside a fence
    this.codeText = null;      // its text node (appendData is O(added))
    this.tail = null;
    this.chars = 0;
  }

  append(text) {
    if (!text) return;
    this.chars += text.length;
    const parts = (this.line + text).split('\n');
    this.line = parts.pop();
    for (const l of parts) this._commit(l);
    this._renderTail();
    this._trim();
  }

  end() {
    if (this.line) { const l = this.line; this.line = ''; this._commit(l); }
    this._clearTail();
  }

  _clearTail() {
    if (this.tail) { this.tail.remove(); this.tail = null; }
  }

  _ensurePara() {
    if (!this.block) {
      this.block = el('p', 'md-p');
      this.root.appendChild(this.block);
      this.blockEmpty = true;
    }
  }

  _commit(line) {
    this._clearTail();
    if (/^\s*```/.test(line)) {
      if (this.code) {
        this.code = null;
        this.codeText = null;
      } else {
        if (this.block && this.blockEmpty) this.block.remove();
        this.block = null;
        const pre = el('pre', 'md-code');
        const lang = line.trim().slice(3).trim();
        if (lang) pre.dataset.lang = lang.slice(0, 20);
        this.code = document.createElement('code');
        this.codeText = document.createTextNode('');
        this.code.appendChild(this.codeText);
        pre.appendChild(this.code);
        this.root.appendChild(pre);
      }
      return;
    }
    if (this.code) { this.codeText.appendData(line + '\n'); return; }
    if (!line.trim()) { this.block = null; return; }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      if (this.block && this.blockEmpty) this.block.remove();
      const hd = el('p', 'md-h');
      appendInline(hd, h[2]);
      this.root.appendChild(hd);
      this.block = null;
      return;
    }
    this._ensurePara();
    if (!this.blockEmpty) this.block.appendChild(document.createTextNode('\n'));
    appendInline(this.block, line);
    this.blockEmpty = false;
  }

  _renderTail() {
    this._clearTail();
    if (!this.line) return;
    if (this.code) {
      this.tail = document.createTextNode(this.line);
      this.code.appendChild(this.tail);
      return;
    }
    this._ensurePara();
    this.tail = el('span', 'md-tail');
    if (!this.blockEmpty) this.tail.appendChild(document.createTextNode('\n'));
    appendInline(this.tail, this.line);
    this.block.appendChild(this.tail);
  }

  _trim() {
    const n = this.root.childNodes.length;
    if (n <= MAX_OUT_NODES) return;
    for (let i = 0; i < n - MAX_OUT_NODES + 200; i++) {
      const c = this.root.firstChild;
      if (!c || c === this.block || c === (this.code && this.code.parentNode)) break;
      c.remove();
    }
    if (!this.root.querySelector(':scope > .out-cut')) this.root.prepend(el('div', 'out-cut muted', '… önceki çıktı kısaltıldı'));
  }
}

const END_TEXT = {
  cancelled: 'Durduruldu',
  denied: 'Cihazda reddedildi',
  budget: 'Bütçe sınırına ulaşıldı',
};

/** task.end → status line text (§11.2). */
export function endText(m) {
  const st = m.status;
  if (st === 'success') {
    const bits = ['Tamamlandı'];
    if (Number.isFinite(m.turns) && m.turns > 0) bits.push(`${m.turns} tur`);
    if (Number.isFinite(m.durationMs)) bits.push(fmtDuration(m.durationMs));
    return bits.join(' · ');
  }
  if (st === 'timeout') return `Süre aşımı (${Math.max(1, Math.round((m.durationMs || 0) / 60000))} dk)`;
  if (st === 'error') return `Hata: ${errorText(m.error && m.error.code)}`;
  if (st === 'denied' && m.error && m.error.code === 'confirm_timeout') return 'Cihazda onay süresi doldu';
  if (st === 'cancelled' && m.error && m.error.code === 'revoked') return 'Durduruldu: başlatan tarayıcının eşleşmesi kaldırıldı';
  return END_TEXT[st] || 'Bitti';
}

const FILE_TOOLS = new Set(['Read', 'Edit', 'Write', 'NotebookEdit', 'MultiEdit']);
const MAX_DENY_TARGETS = 3;

/**
 * v2.md H39: a home path → "~/…" so the user name never shows (connectors from 3.2.4 already send "~/…"; older ones
 * send absolute paths).
 */
export function shortPath(text) {
  return String(text || '').replace(/(?:\/home\/|\/Users\/|\b[A-Za-z]:[\\/]Users[\\/])[^/\\\s]+/g, '~');
}

/** "Write → ~/Desktop/a.txt" for file tools, else just the tool name (commands can be long). */
function denyTarget(name, summary) {
  const n = String(name || '?').slice(0, 40);
  if (!FILE_TOOLS.has(n) || !summary) return n;
  const p = shortPath(summary);
  return `${n} → ${p.length > 60 ? '…' + p.slice(-59) : p}`;
}

/**
 * The line for a group of permission denials (v2.md H39). scope: {folder, profile} of the agent (may be null).
 * → "⚠ İzin dışı işlem engellendi (3 deneme): Write → ~/Desktop/a.txt · Bash · Bu ajan yalnızca «X» klasöründe …"
 */
export function denialText(count, targets, scope) {
  const uniq = [...new Set(targets)];
  const shown = uniq.slice(0, MAX_DENY_TARGETS).join(' · ') + (uniq.length > MAX_DENY_TARGETS ? ' · …' : '');
  const head = count > 1 ? `⚠ İzin dışı işlem engellendi (${count} deneme)` : '⚠ İzin dışı işlem engellendi';
  let why = 'Bu ajanın izinleri bu işlemi kapsamıyor.';
  if (scope && scope.profile === 'readonly') {
    why = scope.folder ? `Bu ajan yalnızca «${scope.folder}» klasöründe ve yalnızca okuma yapabilir.` : 'Bu ajan yalnızca okuma yapabilir.';
  } else if (scope && scope.folder) {
    why = `Bu ajan yalnızca «${scope.folder}» klasöründe çalışabilir.`;
  }
  return `${head}${shown ? ': ' + shown : ''} · ${why}`;
}

/** One task's output block (prompt echo, parts, end line). */
export class TaskBlock {
  constructor(task, prompt, note) {
    this.task = task;
    this.el = el('div', 'out-task');
    this.el.dataset.task = task || '';
    if (prompt) {
      const q = el('div', 'out-prompt');
      q.appendChild(el('span', 'out-you', 'Siz › '));
      q.appendChild(document.createTextNode(prompt.length > 2000 ? prompt.slice(0, 2000) + '…' : prompt));
      this.el.appendChild(q);
    }
    if (note) this.el.appendChild(el('div', 'part-note muted', note));
    this.body = el('div', 'out-body');
    this.el.appendChild(this.body);
    this.md = null;
    this.thinking = null;
    this.tools = [];
    this.rawText = '';
    this.sawText = false;
    this.ended = false;
    this.scope = null;             // {folder, profile} for the denial line (set by the panel)
    this.denyGroup = null;         // open group of consecutive denials (H39)
  }

  getOutputText() {
    return this.rawText || (this.body ? this.body.textContent : '') || '';
  }

  _md() {
    if (!this.md) { this.md = new MdStream(this.body); this.thinking = null; }
    return this.md;
  }

  _break() {
    if (this.md) { this.md.end(); this.md = null; }
    this.thinking = null;
  }

  part(p) {
    if (!p || typeof p !== 'object') return;
    const v = typeof p.v === 'string' ? p.v : '';
    switch (p.k) {
      case 'text':
        this.sawText = true;
        this.rawText += v;
        this.denyGroup = null;
        this._md().append(v);
        break;
      case 'thinking': {
        if (this.md) { this.md.end(); this.md = null; }
        if (!this.thinking) {
          const d = el('div', 'part-thinking');
          this.thinking = document.createTextNode('');
          d.appendChild(this.thinking);
          this.body.appendChild(d);
        }
        this.thinking.appendData(v);
        break;
      }
      case 'tool': {
        this._break();
        const row = el('div', 'part-tool');
        row.appendChild(el('span', 'tool-k', 'Araç: '));
        row.appendChild(el('b', null, String(p.name || '?').slice(0, 60)));
        if (v) row.appendChild(document.createTextNode(` — ${v}`));
        const st = el('span', 'tool-st', ' …');
        row.appendChild(st);
        this.body.appendChild(row);
        this.tools.push({ name: p.name, row, st, done: false, v });
        break;
      }
      case 'tool_done': {
        const rev = [...this.tools].reverse();
        let t = rev.find((x) => !x.done && x.name === p.name);
        // The result of a denied call follows its denial: never mark another (pending) row with it.
        if (!t && !rev.some((x) => x.denied && x.name === p.name)) t = rev.find((x) => !x.done);
        if (t) {
          t.done = true;
          t.st.textContent = p.ok ? ' ✓' : ' ✗';
          t.row.classList.add(p.ok ? 'ok' : 'bad');
        }
        if (p.ok) this.denyGroup = null;
        break;
      }
      case 'denied':
        this._denied(p, v);
        break;
      case 'retry': {
        this._break();
        const wait = Number.isFinite(p.waitMs) ? `, ${Math.round(p.waitMs / 1000)} sn sonra` : '';
        this.body.appendChild(el('div', 'part-note muted', `Yeniden deneniyor (${Number(p.attempt) || '?'}. deneme${wait})`));
        break;
      }
      case 'note':
        this._break();
        this.body.appendChild(el('div', 'part-note muted', v));
        break;
      default:
        break;
    }
  }

  /**
   * v2.md H39: consecutive permission denials collapse into ONE line (a <details>: the summary says what was blocked
   * and why, the denied tool rows stay inside for the curious). Text or a successful tool closes the group.
   */
  _denied(p, v) {
    this._break();
    let t = [...this.tools].reverse().find((x) => x.name === p.name && !x.denied);
    if (t) {
      t.row.remove();
    } else {
      const row = el('div', 'part-tool');
      row.appendChild(el('span', 'tool-k', 'Araç: '));
      row.appendChild(el('b', null, String(p.name || '?').slice(0, 60)));
      const st = el('span', 'tool-st');
      row.appendChild(st);
      t = { name: p.name, row, st, v: '' };
      this.tools.push(t);
    }
    t.done = true;
    t.denied = true;
    t.st.textContent = ' ✗ İzin reddedildi';
    t.row.classList.add('bad');
    if (v) t.row.title = v;
    let g = this.denyGroup;
    if (!g) {
      const det = el('details', 'part-denied');
      const sum = el('summary', 'part-denied-sum');
      const list = el('div', 'part-denied-list');
      det.append(sum, list);
      this.body.appendChild(det);
      g = { det, sum, list, count: 0, targets: [] };
      this.denyGroup = g;
    }
    g.count += 1;
    g.targets.push(denyTarget(p.name, t.v));
    g.list.appendChild(t.row);
    g.sum.textContent = denialText(g.count, g.targets, this.scope);
  }

  /** A connector/UI note under the block (e.g. the handoff outcome), after the end line if there is one. */
  note(text) {
    this.el.appendChild(el('div', 'part-note muted handoff-note', text));
  }

  result(text, truncated) {
    if (typeof text === 'string' && text && !this.sawText) {
      this.rawText += text;
      this._md().append(text);
      this.sawText = true;
    }
    this._break();
    if (truncated) this.body.appendChild(el('div', 'part-note muted', 'Çıktı kısaltıldı; tam metin cihazda duruyor.'));
  }

  end(m, line, costTxt) {
    this.ended = true;
    this._break();
    for (const t of this.tools) if (!t.done) { t.done = true; t.st.textContent = ''; }
    const e = el('div', `out-end st-${['success', 'cancelled', 'error', 'denied', 'budget', 'timeout'].includes(m.status) ? m.status : 'error'}`);
    e.appendChild(el('span', 'out-end-st', line));
    if (costTxt) {
      const c = el('span', 'out-end-cost', ` · ${costTxt.text}`);
      c.title = costTxt.title;
      e.appendChild(c);
    }
    this.el.appendChild(e);
    if (this.sawText || this.rawText) {
      this.el.appendChild(makeExportActions(() => this.getOutputText(), `gorev-${this.task || 'cikti'}`, {
        btnClass: 'btn small ghost',
        containerClass: 'out-actions export-actions',
      }));
    }
    if (m && m.status === 'success') {
      try { defaultNotifier.notifyDone(); } catch {}
    }
  }
}

/** {folder, profile} of a device agent (agent.list JSON) for the denial line, or null. */
export function agentScope(da) {
  if (!da) return null;
  const ws = da.workspace;
  const folder = ws && ws.name ? ws.name + (ws.sub ? '/' + ws.sub : '') : null;
  return { folder, profile: da.profile || null };
}

/** Per office agent (device + key) view state; survives switching between agents. */
class View {
  constructor(deviceId, key) {
    this.deviceId = deviceId;
    this.key = key;
    this.out = el('div', 'ctl-out');
    this.out.setAttribute('role', 'log');
    this.out.setAttribute('aria-live', 'polite');
    this.out.tabIndex = 0;
    this.blocks = new Map();         // task → TaskBlock
    this.task = null;                // current (latest) task id
    this.state = 'idle';             // idle | sending | confirm | queued | running | stopping
    this.startedAt = 0;
    this.live = null;
    this.endLine = null;             // {text, cls} after the last task.end
    this.message = null;             // {text, error}
    this.usage = null;               // last `usage` (agent scope, day)
    this.draft = '';
    this.fresh = false;
    this.recent = null;
    this.handoffTo = '';             // v4-B: agent key to hand the result to ('' = none)
    this.handoffNote = '';
    this.autoWatch = false;          // watched only because a handoff task of this agent runs (not selected)
  }

  get running() { return ['sending', 'confirm', 'queued', 'running', 'stopping'].includes(this.state); }

  block(task, prompt = null, note = null) {
    let b = this.blocks.get(task);
    if (!b) {
      b = new TaskBlock(task, prompt, note);
      this.blocks.set(task, b);
      this.out.appendChild(b.el);
      while (this.blocks.size > MAX_TASK_BLOCKS) {
        const [k, old] = this.blocks.entries().next().value;
        old.el.remove();
        this.blocks.delete(k);
      }
    }
    return b;
  }
}

/**
 * The control section under the agent details. `slot` is a persistent element the v2 details renderer never
 * rewrites, so typing, focus and the output survive every poll.
 */
export class PromptPanel {
  /**
   * @param {object} o
   * @param {import('./control.js').DeviceHub} o.hub
   * @param {HTMLElement} o.slot
   * @param {(text:string, opts?:object)=>void} o.toast
   * @param {() => void} o.onPair              open the pairing wizard
   * @param {() => void} o.onDevices           open the devices page
   * @param {(running:boolean) => void} o.onActivity   a task of this tab runs / all finished
   * @param {HTMLElement} [o.headStop]         phone sheet header "Durdur" button
   * @param {(deviceId:string, msg:object) => void} [o.onHandoff]   a handoff started (office animation)
   */
  constructor({ hub, slot, toast, onPair, onDevices, onActivity, headStop = null, onHandoff = null }) {
    Object.assign(this, { hub, slot, toast, onPair, onDevices, onActivity, headStop, onHandoff });
    this.views = new Map();
    this.agent = null;              // office agent JSON
    this.view = null;
    this.mode = null;               // rendered mode (unpaired | offline | ready …)
    this.els = null;
    this._tick = setInterval(() => this._tickClock(), 1000);
    hub.on('event', (dev, msg) => this._onEvent(dev, msg));
    hub.on('change', () => this.refresh());
    hub.on('resync', (dev) => this._resync(dev));
    if (headStop) headStop.addEventListener('click', () => this.cancel());
  }

  static key(c) { return `${c.device}.${c.agent}`; }

  _viewFor(dev, key, create = true) {
    const k = `${dev}.${key}`;
    let v = this.views.get(k);
    if (!v && create) { v = new View(dev, key); this.views.set(k, v); }
    return v || null;
  }

  // -- selection ---------------------------------------------------------------------------------
  /** Office agent (or null). Called on selection changes and on every room snapshot. */
  show(agent) {
    const c = agent && agent.control;
    if (!c) {
      this._leave();
      this.agent = null;
      this.slot.hidden = true;
      this.slot.replaceChildren();
      this.mode = null;
      this._syncHeadStop();
      return;
    }
    const same = this.view && this.agent && this.agent.control && PromptPanel.key(this.agent.control) === PromptPanel.key(c);
    this.agent = agent;
    if (!same) {
      this._leave();
      this.view = this._viewFor(c.device, c.agent);
      this.mode = null;
      if (this.hub.isPaired(c.device)) {
        this.hub.watch(c.device, c.agent, true);
        this._loadUsage();
      }
    }
    this.slot.hidden = false;
    this.refresh();
  }

  _leave() {
    if (this.view && this.els && this.els.ta) this.view.draft = this.els.ta.value;
    if (this.view && this.hub.isPaired(this.view.deviceId)) this.hub.watch(this.view.deviceId, this.view.key, false);
    this.view = null;
  }

  _modeOf() {
    const c = this.agent.control;
    if (!this.hub.isPaired(c.device)) return 'unpaired';
    const d = this.hub.device(c.device);
    if (d.ready) return 'ready';
    if (d.status === 'connecting') return 'connecting';
    if (d.status === 'error') return 'error';
    return 'offline';
  }

  /** Re-render the dynamic parts (or the whole section when the mode changed). */
  refresh() {
    if (!this.agent || !this.view) return;
    const mode = this._modeOf();
    if (mode !== this.mode) {
      if (this.els && this.els.ta) this.view.draft = this.els.ta.value;
      this.mode = mode;
      this._build();
      if (mode === 'ready' && !this.view.usage) this._loadUsage();
    }
    this._update();
  }

  // -- DOM ---------------------------------------------------------------------------------------
  _build() {
    const s = this.slot;
    s.replaceChildren();
    this.els = {};
    const c = this.agent.control;
    if (this.mode === 'unpaired') {
      const box = el('div', 'ctl ctl-unpaired');
      box.appendChild(el('p', 'muted', 'Bu ajan bir bağlayıcı cihazında çalışıyor. Cihaz sizinse, görev vermek için bu tarayıcıyı eşleştirin.'));
      const b = el('button', 'btn small', 'Cihazı eşleştir');
      b.type = 'button';
      b.addEventListener('click', () => this.onPair());
      box.appendChild(b);
      s.appendChild(box);
      return;
    }
    const v = this.view;
    const root = el('section', 'ctl');
    root.setAttribute('aria-label', 'Görev ver');

    // usage line + gauges + budget editor
    const usage = el('div', 'ctl-usage');
    this.els.usageLine = el('div', 'usage-line');
    this.els.gauges = el('div', 'gauges');
    this.els.limit = el('div', 'usage-note muted small');
    usage.append(this.els.usageLine, this.els.gauges, this.els.limit);
    this.els.budget = this._budgetEditor();
    usage.appendChild(this.els.budget);

    // compose
    const form = el('form', 'ctl-compose');
    form.setAttribute('autocomplete', 'off');
    const h = el('h3', 'ctl-title', 'Görev ver');
    const ta = el('textarea', 'ctl-ta');
    ta.rows = 3;
    ta.placeholder = 'Bu ajana bir görev yazın…';
    ta.setAttribute('aria-label', 'Görev metni');
    ta.setAttribute('enterkeyhint', 'send');
    ta.spellcheck = false;
    ta.value = v.draft || '';
    ta.addEventListener('input', () => { v.draft = ta.value; this._grow(); });
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.send(); }
      else if (e.key === 'Escape' && ta.value) e.stopPropagation();
    });
    const row = el('div', 'ctl-row');
    const fresh = el('label', 'check small');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = v.fresh;
    cb.addEventListener('change', () => { v.fresh = cb.checked; });
    fresh.append(cb, document.createTextNode(' Yeni sohbet başlat'));
    fresh.title = 'Önceki konuşmayı unutup sıfırdan başlar.';
    this.els.profile = el('span', 'ctl-profile small');
    const send = el('button', 'btn accent ctl-send');
    send.type = 'submit';
    send.appendChild(document.createTextNode('Gönder'));
    send.appendChild(el('kbd', 'kbd-hint', 'Ctrl+Enter'));
    row.append(fresh, this.els.profile, send);
    this.els.hint = el('div', 'ctl-hint muted small');
    this.els.msg = el('div', 'ctl-msg small');
    this.els.msg.setAttribute('role', 'status');
    form.append(h, ta, row, this._handoffBox(), this.els.hint, this.els.msg);
    form.addEventListener('submit', (e) => { e.preventDefault(); this.send(); });
    Object.assign(this.els, { ta, send, fresh: cb, form });

    // output
    const outWrap = el('div', 'ctl-outwrap');
    const head = el('div', 'ctl-out-head');
    head.appendChild(el('span', 'ctl-out-title', 'Çıktı'));
    this.els.run = el('span', 'ctl-run');
    head.appendChild(this.els.run);
    const jump = el('button', 'ctl-jump btn small', '↓ Sona git');
    jump.type = 'button';
    jump.hidden = true;
    jump.addEventListener('click', () => { v.out.scrollTop = v.out.scrollHeight; jump.hidden = true; });
    v.out.onscroll = () => { if (this.view === v) jump.hidden = this._atBottom(v.out); };
    const foot = el('div', 'ctl-foot');
    this.els.live = el('span', 'ctl-live');
    this.els.cost = el('span', 'ctl-cost muted');
    const stop = el('button', 'btn small ctl-stop', 'Durdur');
    stop.type = 'button';
    stop.addEventListener('click', () => this.cancel());
    foot.append(this.els.live, this.els.cost, stop);
    outWrap.append(head, v.out, jump, foot);
    Object.assign(this.els, { outWrap, jump, stop, foot });

    // recent tasks
    const recent = el('details', 'ctl-recent');
    recent.appendChild(el('summary', null, 'Son görevler'));
    this.els.recentList = el('ul', 'recent-tasks');
    recent.appendChild(this.els.recentList);
    recent.addEventListener('toggle', () => { if (recent.open) this._loadRecent(); });
    this.els.recent = recent;

    root.append(form, outWrap, recent, usage, this._deleteBox());
    s.appendChild(root);
    this._grow();
    v.out.scrollTop = v.out.scrollHeight;
  }

  /** v4-B: "Bitince devret" — an agent of the same device that gets this task's last reply (+ an extra instruction). */
  _handoffBox() {
    const v = this.view;
    const box = el('details', 'ctl-handoff');
    const sum = el('summary', 'ctl-handoff-sum small', 'Bitince devret');
    sum.title = 'Görev başarıyla biterse son yanıtı, seçtiğiniz ajana veri olarak verilir ve o ajan çalışmaya başlar.';
    const to = el('select', 'ctl-handoff-to');
    to.setAttribute('aria-label', 'Bitince şu ajana devret');
    const note = el('textarea', 'ctl-handoff-note');
    note.rows = 2;
    note.placeholder = 'Ek talimat (isteğe bağlı), ör. "Bu planı uygula"';
    note.setAttribute('aria-label', 'Devir için ek talimat');
    note.spellcheck = false;
    note.value = v.handoffNote || '';
    to.addEventListener('change', () => { v.handoffTo = to.value; this._update(); });
    note.addEventListener('input', () => { v.handoffNote = note.value; });
    box.open = !!v.handoffTo;
    box.append(sum, to, note);
    Object.assign(this.els, { handoff: box, handoffSum: sum, handoffTo: to, handoffNote: note });
    return box;
  }

  _updateHandoff(v, d) {
    const { handoff, handoffSum, handoffTo, handoffNote } = this.els;
    if (!handoff) return;
    const supported = !!(d && d.info && Array.isArray(d.info.features) && d.info.features.includes('handoff'));
    const others = supported && this.mode === 'ready' && !v.deleted && d.agents
      ? [...d.agents.values()].filter((a) => a && typeof a.key === 'string' && a.key !== v.key) : [];
    handoff.hidden = !others.length;
    const sig = JSON.stringify(others.map((a) => [a.key, a.name]));
    if (handoffTo.dataset.sig !== sig) {
      const opts = [['', 'Devretme'], ...others.map((a) => [a.key, String(a.name || a.key)])].map(([value, text]) => {
        const o = el('option', null, text);
        o.value = value;
        return o;
      });
      handoffTo.replaceChildren(...opts);
      handoffTo.dataset.sig = sig;
    }
    const target = others.find((a) => a.key === v.handoffTo);
    if (!target) v.handoffTo = '';
    handoffTo.value = v.handoffTo;
    handoffNote.hidden = !v.handoffTo;
    handoffSum.textContent = target ? `Bitince devret: «${target.name}»` : 'Bitince devret';
  }

  /** E11: "Ajanı sil" with an inline confirmation (no window.confirm: it can be blocked in installed apps). */
  _deleteBox() {
    const box = el('div', 'ctl-delete');
    const del = el('button', 'btn small ghost ctl-del', 'Ajanı sil');
    del.type = 'button';
    const why = el('span', 'ctl-del-why muted small');
    const ask = el('div', 'ctl-del-ask');
    ask.hidden = true;
    ask.setAttribute('role', 'group');
    const q = el('span', 'ctl-del-q small');
    const yes = el('button', 'btn small danger-btn ctl-del-yes', 'Evet, sil');
    yes.type = 'button';
    const no = el('button', 'btn small ctl-del-no', 'Vazgeç');
    no.type = 'button';
    ask.append(q, yes, no);
    box.append(del, why, ask);
    del.addEventListener('click', () => {
      const da = this._devAgent();
      q.textContent = `«${da ? da.name : 'Bu ajan'}» silinsin mi? Karakter ofisten çıkar, sohbet geçmişi silinir.`;
      del.hidden = true;
      ask.hidden = false;
      no.focus();
    });
    no.addEventListener('click', () => { ask.hidden = true; del.hidden = false; del.focus(); });
    yes.addEventListener('click', () => this.deleteAgent());
    Object.assign(this.els, { delBox: box, del, delWhy: why, delAsk: ask, delYes: yes });
    return box;
  }

  /** agent.delete (E11). Not offered while a task of this agent runs (stop it first). */
  async deleteAgent() {
    const v = this.view;
    if (!v || this.mode !== 'ready' || v.running || v.deleting) return;
    v.deleting = true;
    this._update();
    try {
      await this.hub.deleteAgent(v.deviceId, v.key);
      v.deleted = true;
      v.message = { text: 'Ajan silindi. Karakter birazdan ofisten çıkar.' };
      this.toast('Ajan silindi');
    } catch (e) {
      this.toast(e instanceof ControlError ? e.text : 'Silinemedi', { error: true });
    } finally {
      v.deleting = false;
      if (this.view === v) {
        if (this.els && this.els.delAsk) { this.els.delAsk.hidden = true; this.els.del.hidden = false; }
        this._update();
      }
    }
  }

  _updateDelete(v, d, da) {
    const { delBox, del, delWhy, delYes } = this.els;
    if (!delBox) return;
    const allowed = !(d && d.info && d.info.remote && d.info.remote.agentDelete === false);
    delBox.hidden = this.mode !== 'ready' || !da || v.deleted || !allowed;
    const busy = v.running || !!(da && da.task);
    del.disabled = busy || !!v.deleting;
    delYes.disabled = busy || !!v.deleting;
    delWhy.textContent = busy ? 'Görev çalışırken silinemez; önce durdurun.' : '';
  }

  _grow() {
    const ta = this.els && this.els.ta;
    if (!ta) return;
    ta.style.height = 'auto';
    const lh = 18;
    const max = lh * 12 + 12;
    ta.style.height = `${Math.min(max, Math.max(lh * 3 + 12, ta.scrollHeight + 2))}px`;
  }

  _atBottom(box) { return box.scrollHeight - box.scrollTop - box.clientHeight < 40; }

  _info() { return this.hub.device(this.agent.control.device); }
  _devAgent() { return this.hub.agentInfo(this.agent.control); }

  _update() {
    if (!this.els || this.mode === 'unpaired') { this._syncHeadStop(); return; }
    const v = this.view;
    const d = this._info();
    const da = this._devAgent();
    const ready = this.mode === 'ready';
    const engine = da ? da.engine : null;
    const { ta, send, stop } = this.els;
    ta.disabled = !ready || !!v.deleted;
    send.disabled = !ready || v.state === 'sending' || !!v.deleted;
    stop.hidden = !v.running || !v.task;
    stop.disabled = v.state === 'stopping';
    stop.textContent = v.state === 'stopping' ? 'Durduruluyor…' : 'Durdur';
    // profile badge / engine hint
    this.els.profile.replaceChildren();
    if (engine === 'claude') {
      const p = PROFILE_TEXT[da.profile] || PROFILE_TEXT.edit;
      this.els.profile.appendChild(document.createTextNode(`İzin: ${p.label} `));
      const i = el('span', 'info', 'ⓘ');
      i.title = p.tip;
      i.tabIndex = 0;
      i.setAttribute('aria-label', p.tip);
      this.els.profile.appendChild(i);
    }
    let hint = '';
    if (this.mode === 'offline') hint = 'Cihaz çevrimdışı. Bağlayıcı çalışıyor mu? (Cihazlar → Durum)';
    else if (this.mode === 'connecting') hint = 'Cihaza güvenli bağlantı kuruluyor…';
    else if (this.mode === 'error') hint = 'Cihazla güvenli bağlantı kurulamadı. Yeniden eşleştirmeyi deneyin (Cihazlar).';
    else if (d && d.isBrowserLocal) hint = 'Tarayıcı ajanı: bu tarayıcıdaki sanal klasörde dosya okur ve yazar, komut çalıştırmaz.';
    else if (engine === 'ollama' || engine === 'openai') hint = 'Model: sohbet eder. Bağlayıcıda araçlar açıksa klasörde dosya okuyup yazar, komut çalıştırmaz.';
    else if (engine === 'claude' && da) {
      // H39: where files go and what the agent may do, so out-of-folder requests don't surprise anyone.
      const sc = agentScope(da);
      const p = PROFILE_TEXT[da.profile] || PROFILE_TEXT.edit;
      hint = sc.folder ? `Klasör: ${sc.folder} · İzin: ${p.label} · Yalnızca bu klasörde çalışır` : `İzin: ${p.label}`;
    }
    this.els.hint.textContent = hint;
    this.els.hint.classList.toggle('warn', this.mode !== 'ready');
    // status message
    let msg = v.message;
    if (!msg) {
      if (v.state === 'sending') msg = { text: 'Gönderiliyor…' };
      else if (v.state === 'confirm') msg = { text: 'Cihazda onay bekleniyor… Bilgisayardaki bildirimden onaylayın.' };
      else if (v.state === 'queued') msg = { text: 'Ajan meşgul, görev sıraya alındı.' };
    }
    this.els.msg.textContent = msg ? msg.text : '';
    this.els.msg.classList.toggle('error', !!(msg && msg.error));
    // run label + live counters
    this._tickClock();
    const lt = liveTokensText(v.live);
    const pending = v.running && (!v.live || weightedTokens(v.live) === 0);
    this.els.live.textContent = pending ? (engine === 'claude' ? '' : 'Token sayısı görev bitince gelir')
      : v.live ? lt.text : '';
    this.els.live.title = lt.title;
    this.els.outWrap.classList.toggle('empty', !v.blocks.size);
    this._updateDelete(v, d, da);
    this._updateHandoff(v, d);
    this._renderUsage();
    this._syncHeadStop();
  }

  _tickClock() {
    const v = this.view;
    if (!v || !this.els || !this.els.run) return;
    let t = '';
    let cls = '';
    if (v.state === 'running' || v.state === 'stopping') {
      t = `● Çalışıyor ${fmtClock(Date.now() - (v.startedAt || Date.now()))}`;
      cls = 'run';
    } else if (v.state === 'confirm') { t = '● Onay bekliyor'; cls = 'wait'; }
    else if (v.state === 'queued') { t = '● Sırada'; cls = 'wait'; }
    else if (v.state === 'sending') { t = '● Gönderiliyor'; cls = 'wait'; }
    else if (v.endLine) { t = v.endLine.text; cls = v.endLine.cls; }
    this.els.run.textContent = t;
    this.els.run.className = `ctl-run ${cls}`;
  }

  _syncHeadStop() {
    if (!this.headStop) return;
    const v = this.view;
    this.headStop.hidden = !(v && v.running && v.task && this.mode === 'ready');
  }

  _costAuth() {
    const d = this._info();
    return d && d.info && d.info.engines && d.info.engines.claude ? d.info.engines.claude.auth : null;
  }

  _renderUsage() {
    const v = this.view;
    const da = this._devAgent();
    const engine = da ? da.engine : null;
    const u = v.usage;
    const tokens = u ? u.totals.tokens : (da && da.today ? da.today.tokens : null);
    const cost = u ? u.totals.costUsd : (da && da.today ? da.today.costUsd : null);
    this.els.usageLine.replaceChildren();
    if (tokens === null || tokens === undefined) {
      this.els.usageLine.textContent = '';
    } else {
      const t = el('span', null, `${PERIOD_NOUN.day}: ${fmtCompact(tokens)} token`);
      t.title = WEIGHT_NOTE;
      const cl = totalCostLabel(cost, engine, this._costAuth());
      const c = el('span', 'muted', ` · ${cl.text}`);
      c.title = cl.title;
      const i = el('span', 'info', ' ⓘ');
      i.title = cl.title;
      i.tabIndex = 0;
      this.els.usageLine.append(t, c, i);
    }
    this.els.gauges.replaceChildren();
    const budgets = u ? (u.budgets || []) : [];
    for (const b of budgets) this.els.gauges.appendChild(gaugeEl(b, { showScope: b.scope !== `agent:${v.key}` }));
    this.els.limit.textContent = engine === 'claude' ? claudeLimitText(u && u.claudeLimit) : '';
    // budget editor state
    const d = this._info();
    const editable = !!(d && d.info && d.info.remote && d.info.remote.budgetEdit);
    this.els.budget.hidden = !editable || this.mode !== 'ready';
    const cur = da && da.budget;
    if (this.els.budget.dataset.filled !== JSON.stringify(cur || null) && !this.els.budget.open) {
      const f = this.els.budgetForm;
      f.elements.limit.value = cur ? String(cur.limit) : '';
      f.elements.unit.value = cur ? cur.unit : 'usd';
      f.elements.period.value = cur ? cur.period : 'day';
      f.elements.action.value = cur ? cur.action : 'warn';
      f.elements.del.hidden = !cur;
      this.els.budget.dataset.filled = JSON.stringify(cur || null);
    }
  }

  _budgetEditor() {
    const det = el('details', 'budget-edit');
    det.appendChild(el('summary', null, 'Ajan bütçesi'));
    const f = el('form', 'budget-form');
    f.setAttribute('autocomplete', 'off');
    const sel = (name, opts) => {
      const s = el('select');
      s.name = name;
      for (const [v, t] of opts) { const o = el('option', null, t); o.value = v; s.appendChild(o); }
      return s;
    };
    const lim = document.createElement('input');
    lim.name = 'limit';
    lim.type = 'number';
    lim.min = '0';
    lim.step = 'any';
    lim.inputMode = 'decimal';
    lim.placeholder = 'Sınır';
    lim.setAttribute('aria-label', 'Bütçe sınırı');
    const unit = sel('unit', [['usd', '$ (tahmini)'], ['tokens', 'token']]);
    const period = sel('period', [['day', 'Günlük'], ['week', 'Haftalık'], ['month', 'Aylık']]);
    const action = sel('action', [['warn', 'uyar'], ['block', 'durdur']]);
    for (const [s, l] of [[unit, 'Birim'], [period, 'Dönem'], [action, 'Aşılınca']]) s.setAttribute('aria-label', l);
    const save = el('button', 'btn small accent', 'Kaydet');
    save.type = 'submit';
    const del = el('button', 'btn small ghost', 'Kaldır');
    del.type = 'button';
    del.name = 'del';
    f.append(period, lim, unit, action, save, del);
    f.appendChild(el('p', 'muted small', '"durdur": sınır dolunca yeni görev kabul edilmez. "uyar": yalnızca gösterge kırmızıya döner.'));
    f.addEventListener('submit', (e) => {
      e.preventDefault();
      const n = Number(String(lim.value).replace(',', '.'));
      if (!(n > 0)) { this.toast('Sınır sıfırdan büyük olmalı', { error: true }); return; }
      this._setBudget({ period: period.value, unit: unit.value, limit: n, action: action.value });
    });
    del.addEventListener('click', () => this._setBudget(null));
    det.appendChild(f);
    this.els.budgetForm = f;
    return det;
  }

  async _setBudget(budget) {
    const v = this.view;
    try {
      const r = await this.hub.request(v.deviceId, { type: 'agent.update', agent: v.key, budget });
      this.hub.putAgent(v.deviceId, r.agent);
      this.toast(budget ? 'Bütçe kaydedildi' : 'Bütçe kaldırıldı');
      if (this.els && this.els.budget) this.els.budget.open = false;
      await this._loadUsage();
    } catch (e) {
      this.toast(e instanceof ControlError ? e.text : 'Bütçe kaydedilemedi', { error: true });
    }
  }

  async _loadUsage() {
    const v = this.view;
    if (!v || !this.hub.isReady(v.deviceId)) return;
    try {
      const u = await this.hub.request(v.deviceId, { type: 'usage.get', period: 'day', agent: v.key });
      v.usage = u;
      if (this.view === v) this._update();
    } catch { /* shown as raw numbers from agent.list */ }
  }

  async _loadRecent() {
    const v = this.view;
    const list = this.els && this.els.recentList;
    if (!v || !list || !this.hub.isReady(v.deviceId)) return;
    list.replaceChildren(el('li', 'muted', 'Yükleniyor…'));
    try {
      const r = await this.hub.request(v.deviceId, { type: 'task.list', agent: v.key, limit: 10 });
      if (this.view !== v || !this.els) return;
      list.replaceChildren();
      const tasks = Array.isArray(r.tasks) ? r.tasks : [];
      if (!tasks.length) list.appendChild(el('li', 'muted', 'Henüz görev yok.'));
      for (const t of tasks) list.appendChild(this._recentRow(v, t));
    } catch (e) {
      list.replaceChildren(el('li', 'error', e instanceof ControlError ? e.text : 'Yüklenemedi'));
    }
  }

  _recentRow(v, t) {
    const li = el('li', 'recent-task');
    const b = el('button', 'recent-btn');
    b.type = 'button';
    const st = { success: '✓', error: '✗', cancelled: '■', denied: '⊘', budget: '$', timeout: '⏱', running: '●', queued: '…', confirm: '?' }[t.status] || '·';
    b.appendChild(el('span', `rt-st st-${t.status}`, st));
    b.appendChild(el('span', 'rt-prev', t.preview || '(boş)'));
    const meta = [];
    if (t.startedAt && t.endedAt) meta.push(fmtDuration(t.endedAt - t.startedAt));
    if (Number.isFinite(t.tokens)) meta.push(`${fmtCompact(t.tokens)} token`);
    b.appendChild(el('span', 'rt-meta muted', meta.join(' · ')));
    b.addEventListener('click', () => this._openTask(v, t.task));
    li.appendChild(b);
    return li;
  }

  async _openTask(v, task) {
    try {
      const r = await this.hub.request(v.deviceId, { type: 'task.get', task });
      const b = new TaskBlock(`old-${task}`, r.prompt, 'Önceki görev');
      b.result(r.result || '', r.truncated);
      const cost = costLabel(r.costUsd, r.costBasis, this._costAuth());
      b.end({ status: r.status }, `${endText({ status: r.status, durationMs: r.endedAt && r.startedAt ? r.endedAt - r.startedAt : null })} · ${fmtCompact(weightedTokens(r.usage))} token`, cost);
      v.out.appendChild(b.el);
      v.out.scrollTop = v.out.scrollHeight;
      if (this.els) this.els.outWrap.classList.remove('empty');
    } catch (e) {
      this.toast(e instanceof ControlError ? e.text : 'Görev açılamadı', { error: true });
    }
  }

  // -- actions -----------------------------------------------------------------------------------
  async send() {
    const v = this.view;
    if (!v || !this.els || this.mode !== 'ready' || v.state === 'sending') return;
    const text = this.els.ta.value;
    if (!text.trim()) { this.els.ta.focus(); return; }
    const d = this._info();
    const max = (d && d.info && d.info.limits && d.info.limits.maxPrompt) || 32768;
    if (new TextEncoder().encode(text).length > max) {
      v.message = { text: errorText('too_large'), error: true };
      this._update();
      return;
    }
    const req = { type: 'task.start', agent: v.key, prompt: text, fresh: !!v.fresh };
    const target = v.handoffTo && d && d.agents ? d.agents.get(v.handoffTo) : null;
    if (target) {
      const note = (v.handoffNote || '').trim();
      if (new TextEncoder().encode(note).length > HANDOFF_NOTE_MAX) {
        v.message = { text: 'Devir talimatı çok uzun (en fazla 4 KB).', error: true };
        this._update();
        return;
      }
      req.handoff = { agent: target.key, note };
    }
    const prev = v.state;
    v.state = 'sending';
    v.message = null;
    this._update();
    let ack;
    try {
      ack = await this.hub.request(v.deviceId, req);
    } catch (e) {
      v.state = prev === 'sending' ? 'idle' : prev;
      v.message = { text: e instanceof ControlError ? e.text : 'Gönderilemedi.', error: true };
      if (this.view === v) this._update();
      return;
    }
    v.draft = '';
    v.fresh = false;
    v.handoffTo = '';
    v.handoffNote = '';
    if (this.view === v && this.els) {
      this.els.ta.value = '';
      this.els.fresh.checked = false;
      this.els.handoffNote.value = '';
      this.els.handoff.open = false;
      this._grow();
    }
    const handNote = target && ack.handoff === target.key ? `Bitince «${target.name}» ajanına devredilecek` : null;
    const b = v.block(ack.task, text, handNote);
    if (!b.ended) {
      v.task = ack.task;
      v.live = null;
      v.endLine = null;
      v.state = ack.confirm === 'pending' ? 'confirm' : ack.queued ? 'queued' : 'running';
      v.startedAt = Date.now();
    } else if (v.state === 'sending') {
      v.state = 'idle';
    }
    this.onActivity(this._anyRunning());
    if (this.view === v) {
      v.out.scrollTop = v.out.scrollHeight;
      this._update();
    }
  }

  async cancel() {
    const v = this.view;
    if (!v || !v.task || !v.running) return;
    v.state = 'stopping';
    this._update();
    try {
      await this.hub.request(v.deviceId, { type: 'task.cancel', task: v.task }, { quiet: true, timeout: 5000 });
    } catch (e) {
      if (v.state === 'stopping') v.state = 'running';
      this.toast(e instanceof ControlError ? e.text : 'Durdurulamadı', { error: true });
      if (this.view === v) this._update();
    }
  }

  _anyRunning() {
    for (const v of this.views.values()) if (v.running) return true;
    return false;
  }

  // -- device events -----------------------------------------------------------------------------
  _onEvent(dev, m) {
    if (!m || typeof m.type !== 'string') return;
    if (m.type === 'usage' && !m.re) {
      for (const v of this.views.values()) if (v.deviceId === dev && m.scope === `agent:${v.key}`) v.usage = m;
      if (this.view && this.view.deviceId === dev) this._update();
      return;
    }
    if (m.type === 'task.handoff') { this._onHandoff(dev, m); return; }
    if (!['task.chunk', 'task.end', 'confirm.result', 'agent.state'].includes(m.type) || typeof m.agent !== 'string' && m.type !== 'confirm.result') return;
    let v = typeof m.agent === 'string' ? this._viewFor(dev, m.agent, false) : null;
    if (!v && m.type === 'confirm.result') {
      for (const x of this.views.values()) if (x.deviceId === dev && x.task === m.task) v = x;
    }
    if (!v) return;
    const shown = this.view === v;
    const stick = shown && this._atBottom(v.out);
    if (m.type === 'task.chunk') {
      const b = v.block(m.task, null, v.blocks.has(m.task) ? null : 'Başka bir tarayıcıdan ya da cihazdan başlatılan görev');
      b.scope = agentScope(this.hub.agentInfo({ device: dev, agent: m.agent }));
      if (v.task !== m.task || !v.running) {
        v.task = m.task;
        v.endLine = null;
        v.state = 'running';
        this.onActivity(true);
      }
      if (v.state === 'confirm' || v.state === 'queued') v.state = 'running';
      for (const p of Array.isArray(m.parts) ? m.parts : []) b.part(p);
      if (m.live && typeof m.live === 'object') {
        v.live = m.live;
        if (Number.isFinite(m.live.elapsedMs)) v.startedAt = Date.now() - m.live.elapsedMs;
      }
    } else if (m.type === 'task.end') {
      const b = v.block(m.task, null, v.blocks.has(m.task) ? null : 'Başka bir tarayıcıdan ya da cihazdan başlatılan görev');
      b.result(m.result, m.truncated);
      const line = endText(m);
      const cost = costLabel(m.costUsd, m.costBasis, this._costAuthFor(dev));
      const tok = weightedTokens(m.usage);
      b.end(m, `${line} · ${fmtCompact(tok)} token${m.usage && m.usage.estimated ? ' (tahmini)' : ''}`, cost);
      if (v.task === m.task || !v.running) {
        v.task = m.task;
        v.state = 'idle';
        v.live = m.usage ? { ...m.usage } : v.live;
        v.endLine = { text: line, cls: m.status === 'success' ? 'ok' : m.status === 'cancelled' ? 'muted' : 'bad' };
        v.message = m.status === 'error' || m.status === 'budget' || m.status === 'denied'
          ? { text: line, error: true } : null;
      }
      if (v.autoWatch && !v.running && !shown) {
        v.autoWatch = false;
        this.hub.watch(dev, v.key, false);
      }
      this.onActivity(this._anyRunning());
      if (shown) {
        this._loadUsage();
        if (this.els && this.els.recent && this.els.recent.open) this._loadRecent();
        if (this.els) {
          const c = costLabel(m.costUsd, m.costBasis, this._costAuthFor(dev));
          this.els.cost.textContent = c.text;
          this.els.cost.title = c.title;
        }
      }
    } else if (m.type === 'confirm.result') {
      if (v.state === 'confirm') v.state = m.ok ? 'queued' : v.state;
    }
    if (shown) {
      if (stick) v.out.scrollTop = v.out.scrollHeight;
      else if (this.els && this.els.jump) this.els.jump.hidden = false;
      if (m.type !== 'task.end' && this.els) this.els.cost.textContent = '';
      this._update();
    }
  }

  /**
   * v4-B `task.handoff` {task, from, to, status, next, confirm, queued, code, truncated}: ids and flags only. A's block
   * says where the result went (or why not); B gets a view with the new task, so its output and the Task Board follow.
   */
  _onHandoff(dev, m) {
    const ok = typeof m.task === 'string' && typeof m.from === 'string' && typeof m.to === 'string'
      && (m.status === 'failed' || (m.status === 'started' && typeof m.next === 'string'));
    if (!ok) return;
    const nameOf = (key) => {
      const a = this.hub.agentInfo({ device: dev, agent: key });
      return a && a.name ? String(a.name) : 'diğer ajan';
    };
    const started = m.status === 'started';
    const failText = `Devir yapılamadı: ${errorText(m.code)}`;
    const va = this._viewFor(dev, m.from, false);
    if (va && va.blocks.has(m.task)) {
      va.blocks.get(m.task).note(started
        ? `→ «${nameOf(m.to)}» ajanına devredildi${m.truncated ? ' (çıktı kısaltılarak)' : ''}` : failText);
    }
    if (!started) {
      this.toast(failText, { error: true });
    } else {
      this.trackTask(dev, m.to, m.next, `«${nameOf(m.from)}» ajanından devredilen görev`,
        m.confirm === 'pending' ? 'confirm' : m.queued ? 'queued' : 'running');
      if (this.onHandoff) this.onHandoff(dev, m);
    }
    if (this.view && this.view.deviceId === dev) this._update();
  }

  /**
   * Follow a task this tab did not start (a handoff's B task, a workflow step): the agent gets a view with a block for
   * it, so its output and the Task Board follow; an agent that is not selected is watched until that task ends.
   */
  trackTask(dev, key, task, note, state = 'running') {
    const v = this._viewFor(dev, key, true);
    const b = v.block(task, null, note);
    if (!b.ended) {
      v.task = task;
      v.live = null;
      v.endLine = null;
      v.state = state;
      v.startedAt = Date.now();
      if (v !== this.view && !v.autoWatch) {
        v.autoWatch = true;
        this.hub.watch(dev, key, true);
      }
      this.onActivity(true);
    }
    if (this.view && this.view.deviceId === dev) this._update();
    return v;
  }

  /** A followed task is known to be over (e.g. from `workflow.state`) although its task.end never reached this tab. */
  settleTask(dev, key, task) {
    const v = this._viewFor(dev, key, false);
    if (!v || v.task !== task || !v.running) return;
    v.state = 'idle';
    if (v.autoWatch && v !== this.view) {
      v.autoWatch = false;
      this.hub.watch(dev, key, false);
    }
    this.onActivity(this._anyRunning());
    if (this.view === v) this._update();
  }

  _costAuthFor(dev) {
    const d = this.hub.device(dev);
    return d && d.info && d.info.engines && d.info.engines.claude ? d.info.engines.claude.auth : null;
  }

  /** After a reconnect (§11.6): fetch the latest tasks of watched agents and show results we missed. */
  async _resync(dev) {
    let shownAny = false;
    for (const v of this.views.values()) {
      if (v.deviceId !== dev || !v.task) continue;
      try {
        const r = await this.hub.request(dev, { type: 'task.list', agent: v.key, limit: 5 });
        const t = (r.tasks || []).find((x) => x.task === v.task);
        if (t && v.running && !['running', 'queued', 'confirm'].includes(t.status)) {
          const g = await this.hub.request(dev, { type: 'task.get', task: v.task });
          this._onEvent(dev, { type: 'task.end', task: v.task, agent: v.key, status: g.status, result: g.result,
            truncated: g.truncated, usage: g.usage, costUsd: g.costUsd, costBasis: g.costBasis,
            durationMs: g.endedAt && g.startedAt ? g.endedAt - g.startedAt : null, error: null });
          shownAny = true;
        }
      } catch { /* next reconnect */ }
    }
    if (this.view && this.view.deviceId === dev) this._loadUsage();
    if (shownAny || (this.view && this.view.deviceId === dev)) this.toast('Bağlantı yenilendi');
  }
}
