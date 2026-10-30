// Pixel Office — side panel: agent list, details, connection status (ARCHITECTURE.md §6.5).
// v2 additions (CLOUD.md §9.3, §15.1): cloud mode (owner chips, grouping by owner, owner/source in the
// details), inline rename in the details panel, toast. UI language: Turkish.
// Every untrusted string reaches the DOM only through esc() (or textContent / input.value).

import { makeCharacterPalette } from './sprites.js';
import { t as tt } from './i18n.js';

export const STATE_LABELS = {
  working: tt('Çalışıyor'),
  thinking: tt('Düşünüyor'),
  waiting_for_user: tt('Kullanıcıyı bekliyor'),
  idle: tt('Kahve molasında'),
  sleeping: tt('Uyuyor'),
};

export const STATE_COLORS = {
  working: '#4caf50',
  thinking: '#42a5f5',
  waiting_for_user: '#ef5350',
  idle: '#ffb74d',
  sleeping: '#9575cd',
};

const stateColor = (st) => (Object.hasOwn(STATE_COLORS, st) ? STATE_COLORS[st] : '#888');
const stateLabel = (st) => (Object.hasOwn(STATE_LABELS, st) ? STATE_LABELS[st] : String(st ?? '?'));

const STATE_ORDER = ['working', 'thinking', 'waiting_for_user', 'idle', 'sleeping'];

const CONN_TEXT = {
  connecting: tt('Bağlanıyor…'),
  live: tt('Bağlı'),
  reconnecting: tt('Yeniden bağlanıyor…'),
  notfound: tt('Oda bulunamadı'),
};

/** Short counts for the mini window strip: "3 çalışıyor · 1 bekliyor · 2 kahvede". */
const SHORT_LABELS = {
  working: tt('çalışıyor'), thinking: tt('düşünüyor'), waiting_for_user: tt('bekliyor'), idle: tt('kahvede'),
  sleeping: tt('uyuyor'),
};
export function summaryText(agents) {
  if (!agents.length) return tt('Ofis boş');
  const counts = Object.fromEntries(STATE_ORDER.map((s) => [s, 0]));
  for (const a of agents) if (Object.hasOwn(counts, a.state)) counts[a.state]++;
  return STATE_ORDER.filter((s) => counts[s]).map((s) => `${counts[s]} ${SHORT_LABELS[s]}`).join(' · ');
}

const SOURCE_LABELS = {
  'claude-code': 'Claude Code', custom: tt('Özel (kütüphane)'), 'pixel-run': 'pixel-run', 'pixel-proxy': 'pixel-proxy', antigravity: 'Antigravity', api: tt('Genel API'),
};
const sourceLabel = (s) => (Object.hasOwn(SOURCE_LABELS, s) ? SOURCE_LABELS[s] : String(s));
const RENAMED_SOURCES = new Set(['custom', 'project', 'owner']);
const HEX6 = /^#[0-9a-f]{6}$/i;
export const NAME_MAX = 24;

/** Server numbers (ms timestamps, counts) → finite number, so nothing else reaches the markup. */
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

/** 75 000 ms → "1 dk 15 sn" */
export function formatDuration(ms) {
  const sec = Math.max(0, Math.floor(ms / 1000));
  if (sec < 60) return tt('{s} sn', { s: sec });
  const min = Math.floor(sec / 60);
  if (min < 60) return tt('{m} dk {s} sn', { m: min, s: sec % 60 });
  const h = Math.floor(min / 60);
  if (h < 24) return tt('{h} sa {m} dk', { h, m: min % 60 });
  return tt('{d} gün {h} sa', { d: Math.floor(h / 24), h: h % 24 });
}

function formatClock(ms) {
  if (!ms) return '—';
  const d = new Date(ms);
  return d.toLocaleTimeString(tt('tr-TR'), { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/** Attributes that identify a focusable element across re-renders (list rows, details links, rename actions). */
const FOCUS_KEYS = ['data-id', 'data-select', 'data-act'];

/**
 * Replace a container's markup while keeping keyboard focus and scroll position (v2.md H14): the list and the
 * details are rebuilt on every update, which used to drop the focus of a row chosen with the keyboard.
 * Unchanged markup is not touched at all.
 */
const lastMarkup = new WeakMap();

export function replaceMarkup(container, html) {
  if (lastMarkup.get(container) === html && container.childNodes.length) return;
  lastMarkup.set(container, html);
  const active = container.ownerDocument.activeElement;
  let focusKey = null;
  if (active && active !== container && container.contains(active)) {
    const attr = FOCUS_KEYS.find((a) => active.hasAttribute(a));
    if (attr) focusKey = [attr, active.getAttribute(attr)];
  }
  const scrolled = [];
  for (let el = container; el && el.nodeType === 1; el = el.parentElement) {
    if (el.scrollTop) scrolled.push([el, el.scrollTop]);
  }
  container.innerHTML = html;
  for (const [el, top] of scrolled) el.scrollTop = top;
  if (!focusKey) return;
  const [attr, value] = focusKey;
  for (const el of container.querySelectorAll(`[${attr}]`)) {
    if (el.getAttribute(attr) === value) { el.focus({ preventScroll: true }); return; }
  }
}

export class UI {
  /**
   * @param {{list:HTMLElement, details:HTMLElement, status:HTMLElement, mode:HTMLElement, banner?:HTMLElement, summary?:HTMLElement}} els
   * @param {{onSelect:(id:string|null)=>void, colorOf?:(agent:object)=>string}} opts
   */
  constructor(els, { onSelect, colorOf = null, onRename = null } = {}) {
    this.els = els;
    this.onSelect = onSelect || (() => {});
    this.colorOf = colorOf;
    /** (agent, {name|null, future:boolean}) → Promise<boolean>; null name = reset (CLOUD.md §15.1). */
    this.onRename = onRename;
    /** agent → bool: show ✎ (cloud with key; local with the "rename" feature). */
    this.canRename = () => false;
    /** agent → label of the "future sessions" checkbox, or null to hide it. */
    this.futureLabel = () => null;
    this.mode = null;
    this.editing = null;     // {id} while the inline rename input is open
    this.emptyHint = null;   // custom empty-office text (cloud)
    /** v3 (V3.md §11.1, §11.6): agent → null | {tokens: string|null}. Set only when device control is on. */
    this.ctlInfo = null;
    this.agents = [];
    this.byId = new Map();
    this.selectedId = null;
    this.clockOffset = 0; // serverTime - Date.now()
    this._lastTick = 0;

    els.list.addEventListener('click', (e) => {
      const li = e.target.closest('[data-id]');
      if (!li) return;
      this.onSelect(li.dataset.id === this.selectedId ? null : li.dataset.id);
    });
    els.list.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const li = e.target.closest('[data-id]');
      if (li) { e.preventDefault(); this.onSelect(li.dataset.id); }
    });
    els.details.addEventListener('click', (e) => {
      const act = e.target.closest('[data-act]');
      if (act) {
        e.preventDefault();
        if (act.dataset.act === 'rename') this._startEdit();
        else if (act.dataset.act === 'reset') this._reset();
        return;
      }
      const a = e.target.closest('[data-select]');
      if (a) { e.preventDefault(); this.onSelect(a.dataset.select || null); }
    });
    this.setConnection('connecting');
    this._renderDetails();
  }

  setServerTime(serverTime) {
    if (typeof serverTime === 'number') this.clockOffset = serverTime - Date.now();
  }

  _now() { return Date.now() + this.clockOffset; }

  _color(a) {
    if (typeof a.ownerColor === 'string' && HEX6.test(a.ownerColor)) return a.ownerColor;
    if (this.colorOf) {
      const c = this.colorOf(a);
      if (c) return c;
    }
    return makeCharacterPalette(a.colorSeed >>> 0).shirt;
  }

  /** Re-render the list (main sessions, subagents indented under their parent) + counts. */
  setAgents(agents) {
    this.agents = agents;
    this.byId = new Map(agents.map((a) => [a.id, a]));
    const els = this.els;

    // State counts summary.
    if (els.summary) {
      const counts = Object.fromEntries(STATE_ORDER.map((s) => [s, 0]));
      for (const a of agents) if (a.state in counts) counts[a.state]++;
      els.summary.innerHTML = agents.length
        ? STATE_ORDER.filter((s) => counts[s]).map((s) =>
          `<span class="chip" style="--c:${STATE_COLORS[s]}" title="${esc(STATE_LABELS[s])}"><i></i>${counts[s]} ${esc(STATE_LABELS[s])}</span>`).join('')
        : '';
    }

    if (!agents.length) {
      replaceMarkup(els.list, this.mode === 'cloud'
        ? `<li class="empty">
        <div class="empty-icon">☕</div>
        <div><b>${esc(tt('Henüz ajan yok.'))}</b></div>
        <div class="muted">${esc(this.emptyHint || tt("'Ajan bağla' ile ekleyin."))}</div>
      </li>`
        : `<li class="empty">
        <div class="empty-icon">☕</div>
        <div><b>${esc(tt('Ofis şu an boş.'))}</b></div>
        <div class="muted">${esc(tt('Bir Claude Code oturumu başlatınca burada belirecek.'))}<br>${esc(tt('Denemek için:'))} <code>python3 server.py --demo</code></div>
      </li>`);
      this._renderDetails();
      return;
    }

    let mains = agents.filter((a) => !a.isSubagent || !this.byId.has(a.parentId));
    if (this.ctlInfo) {
      // v3: controllable agents first (stable otherwise), so their owner group comes first too.
      const ctl = (a) => (this.ctlInfo(a) ? 0 : 1);
      mains = mains.map((a, i) => [a, i]).sort((x, y) => ctl(x[0]) - ctl(y[0]) || x[1] - y[1]).map((x) => x[0]);
    }
    const children = new Map();
    for (const a of agents) {
      if (a.isSubagent && this.byId.has(a.parentId)) {
        if (!children.has(a.parentId)) children.set(a.parentId, []);
        children.get(a.parentId).push(a);
      }
    }
    const rows = [];
    const pushTree = (m) => {
      rows.push(this._row(m, false));
      for (const c of children.get(m.id) || []) rows.push(this._row(c, true));
    };
    if (agents.some((a) => a.owner)) {
      // Cloud: grouped by owner (first appearance order), subagents under their parent.
      const groups = new Map();
      for (const m of mains) {
        const key = String(m.ownerId || m.owner || '?');
        if (!groups.has(key)) groups.set(key, { owner: m.owner || '?', color: this._color(m), items: [] });
        groups.get(key).items.push(m);
      }
      for (const g of groups.values()) {
        const n = g.items.reduce((k, m) => k + 1 + (children.get(m.id) || []).length, 0);
        rows.push(`<li class="owner-head" role="presentation"><span class="swatch" style="background:${esc(g.color)}"></span><span class="owner-name">${esc(g.owner)}</span><span class="muted">${n} ajan</span></li>`);
        for (const m of g.items) pushTree(m);
      }
    } else {
      for (const m of mains) pushTree(m);
    }
    replaceMarkup(els.list, rows.join(''));
    this._renderDetails();
  }

  _row(a, indented) {
    const now = this._now();
    const sel = a.id === this.selectedId ? ' selected' : '';
    const tool = a.tool ? `<span class="tool" title="${esc(a.detail || '')}">🔧 ${esc(a.tool)}${a.detail ? ` · ${esc(a.detail)}` : ''}</span>` : '';
    const ci = this.ctlInfo ? this.ctlInfo(a) : null;
    const ctl = ci ? `<span class="ctl-badge" title="${esc(tt('Bu ajana görev verebilirsiniz'))}" aria-label="${esc(tt('Bu ajana görev verebilirsiniz'))}">⌨</span>` : '';
    const tok = ci && ci.tokens ? `<span class="ctl-tok" title="${esc(tt('Bugün'))}">${esc(ci.tokens)}</span>` : '';
    return `<li class="agent${indented ? ' sub' : ''}${sel}" data-id="${esc(a.id)}" tabindex="0" role="button" aria-pressed="${sel ? 'true' : 'false'}">
      <span class="swatch" style="background:${esc(this._color(a))}"></span>
      <div class="agent-main">
        <div class="agent-top">
          <span class="agent-name">${indented ? '↳ ' : ''}${esc(a.name)}</span>${ctl}
          <span class="badge" style="--c:${stateColor(a.state)}">${esc(stateLabel(a.state))}</span>
        </div>
        <div class="agent-sub">
          ${a.owner
    ? `<span class="project" title="${esc(a.owner)} · ${esc(a.project)}"><i class="owner-dot" style="background:${esc(this._color(a))}"></i>${esc(a.owner)}${a.project && a.project !== '?' ? ` · ${esc(a.project)}` : ''}</span>`
    : `<span class="project" title="${esc(a.projectPath || '')}">📁 ${esc(a.project)}</span>`}
          <span class="elapsed" data-since="${num(a.stateSince)}">${formatDuration(now - (num(a.stateSince) || now))}</span>
        </div>
        ${tool ? `<div class="agent-tool">${tool}</div>` : ''}${tok ? `<div class="agent-tool">${tok}</div>` : ''}
      </div>
    </li>`;
  }

  setSelected(id) {
    if (this.editing && this.editing.id !== id) this.editing = null;
    this.selectedId = id && this.byId.has(id) ? id : (id || null);
    for (const li of this.els.list.querySelectorAll('[data-id]')) {
      const on = li.dataset.id === this.selectedId;
      li.classList.toggle('selected', on);
      li.setAttribute('aria-pressed', on ? 'true' : 'false');
      if (on && typeof li.scrollIntoView === 'function') li.scrollIntoView({ block: 'nearest' });
    }
    this._renderDetails();
  }

  _renderDetails() {
    const el = this.els.details;
    const a = this.selectedId ? this.byId.get(this.selectedId) : null;
    if (a && this.editing && this.editing.id === a.id && el.querySelector('.rename-input')) return; // keep the open input
    if (!a) this.editing = null;
    if (!a) {
      el.classList.add('placeholder');
      replaceMarkup(el, this.agents.length
        ? `<p class="muted">${esc(tt('Ayrıntıları görmek için ofisteki bir karaktere ya da listedeki bir ajana tıklayın.'))}</p>`
        : '');
      return;
    }
    el.classList.remove('placeholder');
    const now = this._now();
    const parent = a.parentId ? this.byId.get(a.parentId) : null;
    const subs = this.agents.filter((x) => x.parentId === a.id);
    const row = (k, v, cls = '') => (v === null || v === undefined || v === '' ? '' : `<dt>${esc(tt(k))}</dt><dd class="${cls}">${v}</dd>`);
    replaceMarkup(el, `
      <div class="det-head">
        <span class="swatch big" style="background:${esc(this._color(a))}"></span>
        <div>
          ${this._nameBlock(a)}
          <div class="muted" title="${esc(a.projectPath || '')}">📁 ${esc(a.project)}${a.projectPath ? ` <span class="path">${esc(a.projectPath)}</span>` : ''}</div>
        </div>
        <button class="close" data-select="" title="${esc(tt('Kapat (Esc)'))}" aria-label="${esc(tt('Kapat'))}">✕</button>
      </div>
      ${this._resetLine(a)}
      ${a.title ? `<div class="det-title">${esc(a.title)}</div>` : ''}
      <dl>
        ${row('Durum', `<span class="badge" style="--c:${stateColor(a.state)}">${esc(stateLabel(a.state))}</span> <span class="elapsed" data-since="${num(a.stateSince)}">${formatDuration(now - (num(a.stateSince) || now))}</span>`)}
        ${row('Sahip', a.owner ? `<span class="owner-tag"><i class="owner-dot" style="background:${esc(this._color(a))}"></i>${esc(a.owner)}</span>` : null)}
        ${row('Araç', a.tool ? `<b>${esc(a.tool)}</b>${a.detail ? ` — ${esc(a.detail)}` : ''}` : null)}
        ${row('Not', !a.tool && a.detail ? esc(a.detail) : null)}
        ${row('Son mesaj', a.lastMessage ? esc(a.lastMessage) : null, 'quote')}
        ${row('Son istek', a.lastPrompt ? esc(a.lastPrompt) : null, 'quote')}
        ${row('Model', a.model ? `<code>${esc(a.model)}</code>` : null)}
        ${row('Araç sayısı', String(num(a.toolCount)))}
        ${row('Ajan türü', a.agentType ? esc(a.agentType) : null)}
        ${row('Kaynak', a.source ? esc(sourceLabel(a.source)) : null)}
        ${row('Ana ajan', a.parentId ? (parent ? `<a href="#" data-select="${esc(parent.id)}">${esc(parent.name)}</a>` : `<span class="muted">${esc(a.parentId.slice(0, 8))}…</span>`) : null)}
        ${row('Alt ajanlar', subs.length ? subs.map((s) => `<a href="#" data-select="${esc(s.id)}">${esc(s.name)}</a>`).join(', ') : null)}
        ${row('Başladı', formatClock(num(a.startedAt)))}
        ${row('Son etkinlik', `<span class="ago" data-at="${num(a.updatedAt)}">${esc(tt('{d} önce', { d: formatDuration(now - (num(a.updatedAt) || now)) }))}</span>`)}
        ${row('Kimlik', `<code class="id">${esc(a.id)}</code>`)}
      </dl>`);
  }

  setConnection(status) {
    const text = CONN_TEXT[status] || status;
    const st = this.els.status;
    st.textContent = text;
    st.dataset.status = status;
    if (this.els.banner) {
      this.els.banner.textContent = status === 'live' ? '' : text;
      this.els.banner.classList.toggle('show', status !== 'live');
    }
  }

  setMode(mode) {
    const m = this.els.mode;
    this.mode = mode;
    if (mode === 'cloud') {
      m.textContent = tt('BULUT ODA');
      m.title = tt('Paylaşılan bulut odası');
    } else {
      m.textContent = mode === 'demo' ? tt('DEMO MOD') : tt('CANLI MOD');
      m.title = mode === 'demo' ? tt('Sahte ajanlarla demo modu') : tt('Gerçek Claude Code oturumları');
    }
    m.dataset.mode = mode;
  }

  // --- rename (CLOUD.md §15.1) ------------------------------------------------------------------
  _nameBlock(a) {
    const edit = this.canRename(a)
      ? ` <button class="edit-name" data-act="rename" title="${esc(tt('Yeniden adlandır'))}" aria-label="${esc(tt('Yeniden adlandır'))}">✎</button>`
      : '';
    return `<div class="det-name"><span class="det-name-text">${a.isSubagent ? '↳ ' : ''}${esc(a.name)}</span>${edit}</div>`;
  }

  _resetLine(a) {
    if (!this.canRename(a) || !RENAMED_SOURCES.has(a.nameSource)) return '';
    const def = a.defaultName && a.defaultName !== a.name ? ` <span class="default-name">(${esc(a.defaultName)})</span>` : '';
    const why = a.nameSource === 'custom' ? tt('Bu ajana verilen ad') : a.nameSource === 'owner' ? tt('Sahibin varsayılan adı') : tt('Projenin varsayılan adı');
    return `<div class="rename-meta"><span class="muted">${esc(why)}.</span> <a href="#" data-act="reset">${esc(tt('Varsayılana dön'))}</a>${def}</div>`;
  }

  _startEdit() {
    const a = this.selectedId ? this.byId.get(this.selectedId) : null;
    if (!a || !this.canRename(a)) return;
    this.editing = { id: a.id };
    const holder = this.els.details.querySelector('.det-name');
    if (!holder) return;
    const futureText = this.futureLabel(a);
    const box = document.createElement('div');
    box.className = 'rename-box';
    const input = document.createElement('input');
    input.className = 'rename-input';
    input.type = 'text';
    input.maxLength = NAME_MAX;
    input.value = a.name || '';
    input.setAttribute('aria-label', tt('Yeni ad'));
    input.spellcheck = false;
    box.appendChild(input);
    let check = null;
    if (futureText) {
      const lab = document.createElement('label');
      lab.className = 'rename-future';
      check = document.createElement('input');
      check.type = 'checkbox';
      check.checked = false;
      lab.appendChild(check);
      lab.appendChild(document.createTextNode(' ' + futureText));   // owner name as plain text
      box.appendChild(lab);
    }
    const hint = document.createElement('div');
    hint.className = 'rename-hint muted';
    hint.textContent = tt('Enter: kaydet · Esc: vazgeç');
    box.appendChild(hint);
    holder.replaceWith(box);
    lastMarkup.delete(this.els.details);   // the DOM no longer matches the last markup
    input.focus();
    input.select();

    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      this.editing = null;
      const name = input.value.trim();
      const future = !!(check && check.checked);
      if (save && name && (name !== a.name || future) && this.onRename) {
        this.onRename(a, { name, future });
      }
      this._renderDetails();
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    box.addEventListener('focusout', (e) => {
      if (e.relatedTarget && box.contains(e.relatedTarget)) return;   // moving to the checkbox
      // Defer: a click on the checkbox label may move focus a tick later.
      setTimeout(() => { if (!box.contains(document.activeElement)) finish(true); }, 0);
    });
    if (check) check.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
      else if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    });
  }

  _reset() {
    const a = this.selectedId ? this.byId.get(this.selectedId) : null;
    if (!a || !this.canRename(a) || !this.onRename) return;
    this.onRename(a, { name: null, future: false });
  }

  /** Short message at the bottom of the screen (e.g. "Ad kaydedilemedi"). */
  toast(text, { error = false, ms = 3000 } = {}) {
    let t = document.getElementById('toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'toast';
      t.setAttribute('role', 'status');
      t.setAttribute('aria-live', 'polite');
      document.body.appendChild(t);
    }
    t.textContent = text;
    t.classList.toggle('error', !!error);
    t.classList.add('show');
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => t.classList.remove('show'), ms);
  }

  /** Refresh relative times once per second. */
  tick(nowMs) {
    if (nowMs - this._lastTick < 1000) return;
    this._lastTick = nowMs;
    const now = nowMs + this.clockOffset;
    for (const el of document.querySelectorAll('.elapsed[data-since]')) {
      const since = Number(el.dataset.since) || now;
      el.textContent = formatDuration(now - since);
    }
    for (const el of document.querySelectorAll('.ago[data-at]')) {
      const at = Number(el.dataset.at) || now;
      el.textContent = tt('{d} önce', { d: formatDuration(now - at) });
    }
  }
}
