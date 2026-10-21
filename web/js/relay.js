// Pixel Office v3 — browser L1 relay client (V3.md §3, §4.5). Plain ES module, no dependencies.
//
// RelayClient fetches a short-lived ticket from Vercel (POST /api/relay_ticket, write key), opens
//   new WebSocket(url, ['pixel-relay.v1', 'tk.' + ticket])          (the ticket never goes into a URL)
// sends `hello`, keeps the device presence map, pings every 50 s, reconnects with backoff
// 1 → 2 → 4 → 8 → 16 → 30 s (±20% jitter, a fresh ticket every time) and disconnects 60 s after the page is hidden
// unless a task is running. It only carries L1 frames: `b` payloads are opaque here (E2E lives in e2e.js, Phase 13).
//
// Status: idle | connecting | live | reconnecting | quota. `quota` = close 4029, or two consecutive failed
// attempts without a welcome (a browser cannot see the HTTP 429 of a refused upgrade), shown as
// "Röle şu an meşgul ya da günlük kota doldu".
// If the page's Content-Security-Policy does not list the relay host (vercel.json `connect-src`, see
// tools/set_relay_host.py), the browser fires `securitypolicyviolation`; the client then stops for good with
// status `idle` and detail {error: 'csp', host} instead of retrying forever (V3.md §20, Phase 15).

export const SUBPROTOCOL = 'pixel-relay.v1';
export const APP = 'web/3.0.0';
export const PING_MS = 50_000;
export const HIDDEN_GRACE_MS = 60_000;
export const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];
const ROOM_RE = /^[a-z2-7]{26}$/;
const KEY_RE = /^[A-Za-z0-9_-]{32}$/;
const PEER_RE = /^[a-z2-7]{16}$/;
const URL_RE = /^(wss:\/\/[A-Za-z0-9.-]+(:\d{1,5})?|ws:\/\/127\.0\.0\.1(:\d{1,5})?)\/v1\/rooms\/[a-z2-7]{26}\/ws$/;
const OPEN = 1;

const ERROR_CODE_RE = /^[a-z_]{1,40}$/;

/**
 * Error code of a refused ticket request (v2.md H2): 401/403 always mean the write key in this browser is wrong
 * ('unauthorized'), otherwise the server's own code when it looks like one ('relay_not_configured' on 503, …),
 * else 'ticket'. The UI maps codes to Turkish text; the raw server message is never shown.
 */
export function ticketErrorCode(status, body) {
  if (status === 401 || status === 403) return 'unauthorized';
  const code = body && typeof body.error === 'string' ? body.error : '';
  return ERROR_CODE_RE.test(code) ? code : 'ticket';
}

export class RelayClient {
  /**
   * @param {object} o
   * @param {string} [o.base='']           origin of the Vercel API ('' = same origin)
   * @param {string} o.roomId
   * @param {string} o.writeKey
   * @param {'browser'} [o.role='browser']
   * @param {string} o.bid                 this browser's id (16 × [a-z2-7], from e2e.js in Phase 13)
   * @param {(frame: object) => void} [o.onFrame]   every L1 frame from the relay (welcome, presence, msg, left, err …)
   * @param {(status: string, detail?: object) => void} [o.onStatus]
   * @param {() => boolean} [o.isVisible]  page visible? (the PiP mini window counts as visible, like v2 polling)
   * @param {object} [o.scheduler=globalThis]  setTimeout / clearTimeout / setInterval / clearInterval (tests)
   * @param {Function} [o.WebSocketImpl], [o.fetchImpl], [o.random], [o.doc]   injectables for tests
   */
  constructor({ base = '', roomId, writeKey, role = 'browser', bid, onFrame = null, onStatus = null, isVisible = null,
    scheduler = globalThis, WebSocketImpl = globalThis.WebSocket, fetchImpl = null, random = Math.random,
    doc = globalThis.document } = {}) {
    if (!ROOM_RE.test(roomId || '') || !KEY_RE.test(writeKey || '') || !PEER_RE.test(bid || '') || role !== 'browser') {
      throw new Error('RelayClient: invalid roomId, writeKey, role or bid');
    }
    Object.assign(this, { base, roomId, writeKey, role, bid, onFrame, onStatus, scheduler, WebSocketImpl, random, doc });
    this.fetchImpl = fetchImpl || ((...a) => globalThis.fetch(...a));
    this.isVisible = isVisible || (() => !doc || doc.visibilityState !== 'hidden');
    this.ws = null;
    this.conn = null;
    this._status = 'idle';
    this._presence = new Map();
    this._needs = new Set();
    this._wanted = false;
    this._attempt = 0;
    this._failures = 0;          // consecutive attempts that ended without a welcome (or with 4029)
    this._timer = null;          // reconnect timer
    this._hideTimer = null;
    this._ping = null;
    this._gen = 0;               // invalidates callbacks of older sockets / attempts
    this._onVis = () => this._visibilityChanged();
    this._onCsp = (ev) => this._cspViolation(ev);
    this._lastUrl = null;        // relay URL of the latest attempt (CSP violation matching)
    this.lastError = null;       // 'csp' after a CSP block, or the ticket error code (cleared by the next welcome)
    if (doc && doc.addEventListener) {
      doc.addEventListener('visibilitychange', this._onVis);
      doc.addEventListener('securitypolicyviolation', this._onCsp);
    }
  }

  get status() { return this._status; }

  /** Map deviceId → {since, app} of connected devices (from the last `presence` frame). */
  get presence() { return new Map(this._presence); }

  /** Keep the connection while any reason ('devices', 'dialog', 'task', …) is active (V3.md §4.5). */
  need(reason, on = true) {
    if (on) this._needs.add(reason); else this._needs.delete(reason);
    if (this._needs.size) this.start();
    else this.stop();
  }

  async start() {
    if (this._wanted && this._status !== 'idle') return;
    this._wanted = true;
    this._clearHide();
    if (!this.isVisible() && !this._needs.has('task')) return;   // connects when the page becomes visible
    await this._connect();
  }

  stop() {
    this._wanted = false;
    this._needs.clear();
    this._teardown(1000);
    this._setStatus('idle');
  }

  /** Close for good (removes the visibility listener). */
  destroy() {
    this.stop();
    if (this.doc && this.doc.removeEventListener) {
      this.doc.removeEventListener('visibilitychange', this._onVis);
      this.doc.removeEventListener('securitypolicyviolation', this._onCsp);
    }
  }

  /** Send one L1 frame (object). → false when the socket is not open. */
  send(frame) {
    if (!this.ws || this.ws.readyState !== OPEN || this._status !== 'live') return false;
    try {
      this.ws.send(JSON.stringify(frame));
      return true;
    } catch {
      return false;
    }
  }

  // -- internals --
  _setStatus(s, detail) {
    if (s === this._status && !detail) return;
    this._status = s;
    if (this.onStatus) {
      try { this.onStatus(s, detail); } catch { /* UI errors stay in the UI */ }
    }
  }

  _teardown(code) {
    this._gen += 1;
    if (this._timer !== null) { this.scheduler.clearTimeout(this._timer); this._timer = null; }
    if (this._ping !== null) { this.scheduler.clearInterval(this._ping); this._ping = null; }
    this._clearHide();
    const ws = this.ws;
    this.ws = null;
    this.conn = null;
    this._presence = new Map();
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      try { ws.close(code); } catch { /* ignore */ }
    }
  }

  _clearHide() {
    if (this._hideTimer !== null) { this.scheduler.clearTimeout(this._hideTimer); this._hideTimer = null; }
  }

  async _connect() {
    this._teardown(1000);
    const gen = this._gen;
    this._setStatus(this._attempt === 0 ? 'connecting' : (this._failures >= 2 ? 'quota' : 'reconnecting'));
    let ticket;
    try {
      ticket = await this._fetchTicket();
    } catch (e) {
      if (gen !== this._gen) return;
      if (e && e.fatal) {                     // 401 / 404 / 503: retrying cannot help
        this._wanted = false;
        this.lastError = e.code;
        this._setStatus('idle', { error: e.code });
        return;
      }
      if (e && e.status === 429) this._failures += 1;
      this._retry(gen);
      return;
    }
    if (gen !== this._gen) return;
    let ws;
    this._lastUrl = ticket.url;
    try {
      ws = new this.WebSocketImpl(ticket.url, [SUBPROTOCOL, 'tk.' + ticket.ticket]);
    } catch {
      this._failures += 1;
      this._retry(gen);
      return;
    }
    this.ws = ws;
    let welcomed = false;
    ws.onopen = () => {
      if (gen !== this._gen) return;
      try { ws.send(JSON.stringify({ t: 'hello', v: 1, app: APP, caps: ['e2e1'] })); } catch { /* onclose follows */ }
    };
    ws.onmessage = (ev) => {
      if (gen !== this._gen || typeof ev.data !== 'string') return;
      let f;
      try { f = JSON.parse(ev.data); } catch { return; }
      if (!f || typeof f.t !== 'string') return;
      if (f.t === 'welcome') {
        welcomed = true;
        this.lastError = null;
        this.conn = typeof f.conn === 'string' ? f.conn : null;
        this._attempt = 0;
        this._failures = 0;
        this._ping = this.scheduler.setInterval(() => {
          if (this.ws === ws && ws.readyState === OPEN) {
            try { ws.send('{"t":"ping"}'); } catch { /* ignore */ }
          }
        }, PING_MS);
        this._setStatus('live');
      } else if (f.t === 'presence' && Array.isArray(f.devices)) {
        const m = new Map();
        for (const d of f.devices) {
          if (d && PEER_RE.test(d.id || '')) m.set(d.id, { since: Number(d.since) || 0, app: typeof d.app === 'string' ? d.app : null });
        }
        this._presence = m;
      } else if (f.t === 'pong') {
        return;
      }
      if (this.onFrame) {
        try { this.onFrame(f); } catch { /* UI errors stay in the UI */ }
      }
    };
    ws.onerror = () => { /* onclose carries the outcome */ };
    ws.onclose = (ev) => {
      if (gen !== this._gen) return;
      this.ws = null;
      if (this._ping !== null) { this.scheduler.clearInterval(this._ping); this._ping = null; }
      this._presence = new Map();
      if (!welcomed || (ev && ev.code === 4029)) this._failures += 1;
      if (!this._wanted) { this._setStatus('idle'); return; }
      this._retry(gen);
    };
  }

  async _fetchTicket() {
    const url = `${this.base}/api/relay_ticket?room=${this.roomId}`;
    let r;
    try {
      r = await this.fetchImpl(url, {
        method: 'POST', cache: 'no-store',
        headers: { Authorization: `Bearer ${this.writeKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: this.role, id: this.bid }),
      });
    } catch {
      throw { status: 0 };
    }
    let d = null;
    try { d = await r.json(); } catch { /* handled below */ }
    if (r.status !== 200) {
      throw { status: r.status, code: ticketErrorCode(r.status, d), fatal: [400, 401, 403, 404, 503].includes(r.status) };
    }
    if (!d || typeof d.ticket !== 'string' || typeof d.url !== 'string' || !URL_RE.test(d.url)) throw { status: 0 };
    return d;
  }

  _retry(gen) {
    if (gen !== this._gen || !this._wanted) return;
    const base = BACKOFF_MS[Math.min(this._attempt, BACKOFF_MS.length - 1)];
    const delay = Math.round(base * (0.8 + 0.4 * this.random()));
    this._attempt += 1;
    this._setStatus(this._failures >= 2 ? 'quota' : 'reconnecting');
    this._timer = this.scheduler.setTimeout(() => {
      this._timer = null;
      if (gen !== this._gen || !this._wanted) return;
      if (!this.isVisible() && !this._needs.has('task')) { this._teardown(1000); this._setStatus('idle'); return; }
      this._connect();
    }, delay);
  }

  _cspViolation(ev) {
    if (!ev || !this._lastUrl) return;
    const dir = String(ev.effectiveDirective || ev.violatedDirective || '');
    if (!dir.startsWith('connect-src')) return;
    let host = null;
    let blocked = null;
    try { host = new URL(this._lastUrl).host; } catch { return; }
    try { blocked = new URL(String(ev.blockedURI || '')).host; } catch { blocked = null; }
    if (blocked !== host) return;
    this._wanted = false;
    this.lastError = 'csp';
    this._teardown(1000);
    this._setStatus('idle', { error: 'csp', host });
  }

  _visibilityChanged() {
    if (!this._wanted) return;
    if (this.isVisible()) {
      this._clearHide();
      if (this._status === 'idle') { this._attempt = 0; this._connect(); }
      return;
    }
    if (this._needs.has('task') || this._hideTimer !== null) return;
    this._hideTimer = this.scheduler.setTimeout(() => {
      this._hideTimer = null;
      if (this.isVisible() || this._needs.has('task') || !this._wanted) return;
      this._teardown(1000);
      this._setStatus('idle');       // _wanted stays true: reconnects when visible again
    }, HIDDEN_GRACE_MS);
  }
}
