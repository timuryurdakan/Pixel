// Pixel Office — transports (CLOUD.md §9.2).
// SseTransport: the v1 EventSource client moved out of main.js, behaviour identical (local mode).
// PollingTransport: cloud mode, adaptive polling of GET /api/state?room=… with jitter, back-off,
// pause while nothing is visible and a switchable timer host (PiP window).

/**
 * handlers: {onSnapshot(data), onUpsert(agent), onRemove(id), onStatus(status), onError?(code)}
 * status ∈ 'connecting' | 'live' | 'reconnecting'.
 */

const SSE_BACKOFF_MIN = 1000, SSE_BACKOFF_MAX = 10000;

function parse(e) {
  try { return JSON.parse(e.data); } catch (err) { console.error('[pixel-office] bad SSE payload', err); return null; }
}

export class SseTransport {
  constructor(url = '/events') {
    this.url = url;
    this.es = null;
    this.backoff = SSE_BACKOFF_MIN;
    this.retryTimer = 0;
    this.gotSnapshot = false;
    this.h = null;
  }

  start(handlers) {
    this.h = handlers;
    this._connect();
  }

  stop() {
    clearTimeout(this.retryTimer);
    if (this.es) this.es.close();
    this.es = null;
    this.h = null;
  }

  // Interface parity with PollingTransport (no-ops for SSE).
  setScheduler() {}
  setActive() {}
  pollNow() {}

  _connect() {
    const h = this.h;
    if (!h) return;
    clearTimeout(this.retryTimer);
    if (this.es) this.es.close();
    h.onStatus(this.gotSnapshot ? 'reconnecting' : 'connecting');
    const es = this.es = new EventSource(this.url);
    es.onopen = () => {
      this.backoff = SSE_BACKOFF_MIN;
      h.onStatus('live');
    };
    es.onerror = () => {
      h.onStatus('reconnecting');
      // We manage reconnection ourselves (exponential backoff 1 s → 10 s).
      es.close();
      if (this.es === es) this.es = null;
      clearTimeout(this.retryTimer);
      this.retryTimer = setTimeout(() => this._connect(), this.backoff);
      this.backoff = Math.min(SSE_BACKOFF_MAX, this.backoff * 2);
    };
    es.addEventListener('snapshot', (e) => {
      const d = parse(e);
      if (!d) return;
      h.onStatus('live');
      h.onSnapshot(d);
      this.gotSnapshot = true;
    });
    const upsert = (e) => {
      const a = parse(e);
      if (a && a.id) h.onUpsert(a);
    };
    es.addEventListener('agent_added', upsert);
    es.addEventListener('agent_updated', upsert);
    es.addEventListener('agent_removed', (e) => {
      const d = parse(e);
      if (d && d.id) h.onRemove(d.id);
    });
  }
}

export const POLL_MIN_MS = 3000, POLL_MAX_MS = 120000, POLL_DEFAULT_MS = 10000;
/** Boost interval (CLOUD.md §17.10): right after opening an empty room, and while/after the "Ajan bağla"
 *  dialog, so a newly connected agent shows up within ~5–10 s instead of waiting for the 60 s empty-room poll. */
export const POLL_FAST_MS = 5000;
const POLL_BACKOFF = [2000, 4000, 8000, 16000, 30000];

export class PollingTransport {
  /** @param {{roomId:string, base?:string, scheduler?:{setTimeout:Function, clearTimeout:Function}}} opts */
  constructor({ roomId, base = '', scheduler = window }) {
    this.roomId = roomId;
    this.base = base;
    this.scheduler = scheduler;
    this.h = null;
    this.timer = 0;
    this.timerHost = null;
    this.dueAt = 0;
    this.active = true;
    this.running = false;
    this.inflight = false;
    this.queued = false;      // a pollNow() arrived while a request was in flight
    this.bustNext = false;
    this.errors = 0;
    this.gotSnapshot = false;
    this.stopped = false;
    this.requests = 0;        // counter (tests: no requests while hidden)
    this.fastUntil = 0;       // Date.now() bound: poll every POLL_FAST_MS
    this.fastEmptyUntil = 0;  // … but only while the room has no agents
    this.fastHold = false;    // fast while held (setup dialog open)
    this.lastCount = -1;      // agents in the last snapshot (-1 = none yet)
  }

  /**
   * Poll every POLL_FAST_MS for a while (§17.10). `ms`: always fast until now+ms; `emptyMs`: fast until
   * now+emptyMs only while the room is empty; `hold`: fast until released with `hold: false`.
   * A pending slow timer is pulled in; hidden pages stay paused (setActive wins).
   */
  boost({ ms = 0, emptyMs = 0, hold } = {}) {
    const now = Date.now();
    if (ms > 0) this.fastUntil = Math.max(this.fastUntil, now + ms);
    if (emptyMs > 0) this.fastEmptyUntil = Math.max(this.fastEmptyUntil, now + emptyMs);
    if (typeof hold === 'boolean') this.fastHold = hold;
    if (this.timer && this._fast() && this.dueAt - now > POLL_FAST_MS) this._arm(POLL_FAST_MS);
  }

  _fast() {
    const now = Date.now();
    return this.fastHold || now < this.fastUntil || (now < this.fastEmptyUntil && this.lastCount <= 0);
  }

  start(handlers) {
    this.h = handlers;
    this.running = true;
    this.stopped = false;
    handlers.onStatus('connecting');
    this._poll();
  }

  stop() {
    this.running = false;
    this.stopped = true;
    this._clear();
    this.h = null;
  }

  /** Switch the timer host (PiP window ↔ opener). Re-arms the pending timer on the new host. */
  setScheduler(win) {
    const next = win || window;
    if (next === this.scheduler) return;
    const remaining = this.timer ? Math.max(0, this.dueAt - Date.now()) : -1;
    this._clear();
    this.scheduler = next;
    if (remaining >= 0 && this.running && this.active) this._arm(remaining);
  }

  /** false = pause (hidden page and no PiP window); true = resume with an immediate poll. */
  setActive(on) {
    on = !!on;
    if (on === this.active) return;
    this.active = on;
    if (!on) { this._clear(); return; }
    if (this.running) this.pollNow();
  }

  /** Poll right away. `bust` adds `&_=<ms>` to skip the CDN cache once (after a rename, §15.3). */
  pollNow({ bust = false } = {}) {
    if (!this.running) return;
    if (bust) this.bustNext = true;
    if (this.inflight) { this.queued = true; return; }
    this._clear();
    this._poll();
  }

  _clear() {
    if (this.timer) {
      try { (this.timerHost || this.scheduler).clearTimeout(this.timer); } catch { /* closed PiP window */ }
    }
    this.timer = 0;
    this.timerHost = null;
  }

  _arm(ms) {
    this._clear();
    if (!this.running || !this.active) return;
    const host = this.scheduler;
    this.dueAt = Date.now() + ms;
    this.timerHost = host;
    this.timer = host.setTimeout(() => { this.timer = 0; this.timerHost = null; this._poll(); }, ms);
  }

  async _poll() {
    if (!this.running || !this.active || this.inflight) return;
    this.inflight = true;
    this.queued = false;
    let url = `${this.base}/api/state?room=${encodeURIComponent(this.roomId)}`;
    if (this.bustNext) { url += `&_=${Date.now()}`; this.bustNext = false; }
    this.requests++;
    let data = null, status = 0;
    try {
      const r = await fetch(url, { cache: 'no-store', headers: { Accept: 'application/json' } });
      status = r.status;
      if (r.ok) data = await r.json();
    } catch {
      status = 0;
    }
    this.inflight = false;
    if (!this.running) return;
    const h = this.h;
    if (status === 404) {
      this.running = false;
      h.onStatus('reconnecting');
      if (h.onError) h.onError('not_found');
      return;
    }
    if (data && typeof data === 'object' && Array.isArray(data.agents)) {
      this.errors = 0;
      this.lastCount = data.agents.length;
      h.onSnapshot(data);
      this.gotSnapshot = true;
      h.onStatus('live');
      let next = Number(data.pollMs);
      if (!Number.isFinite(next)) next = POLL_DEFAULT_MS;
      next = Math.min(POLL_MAX_MS, Math.max(POLL_MIN_MS, next));
      if (this._fast()) next = Math.min(next, POLL_FAST_MS);
      next *= 1 + (Math.random() * 0.2 - 0.1);            // ± 10 % jitter
      if (this.queued) next = 0;
      this._arm(Math.round(next));
    } else {
      h.onStatus('reconnecting');
      const wait = POLL_BACKOFF[Math.min(this.errors, POLL_BACKOFF.length - 1)];
      this.errors++;
      this._arm(this.queued ? 0 : wait);
    }
  }
}
