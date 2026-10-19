// web/js/relay.js (RelayClient) with a fake WebSocket, fetch and scheduler (headless Chrome via test_relay_js.py).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RelayClient, PING_MS, HIDDEN_GRACE_MS } from '../../web/js/relay.js';

const ROOM = 'abcdefghijklmnopqrstuvwxyz';
const KEY = 'K'.repeat(32);
const BID = 'browseraaaaaaaa4';
const URL = `wss://pixel-relay.test.workers.dev/v1/rooms/${ROOM}/ws`;

class Sched {
  constructor() { this.t = 0; this.n = 0; this.timers = new Map(); }
  setTimeout(fn, ms) { const id = ++this.n; this.timers.set(id, { fn, at: this.t + ms, every: 0 }); return id; }
  setInterval(fn, ms) { const id = ++this.n; this.timers.set(id, { fn, at: this.t + ms, every: ms }); return id; }
  clearTimeout(id) { this.timers.delete(id); }
  clearInterval(id) { this.timers.delete(id); }
  pending() { return [...this.timers.values()].map((x) => x.at - this.t).sort((a, b) => a - b); }
  async advance(ms) {
    const end = this.t + ms;
    for (;;) {
      let next = null;
      for (const [id, x] of this.timers) if (x.at <= end && (!next || x.at < next[1].at)) next = [id, x];
      if (!next) break;
      const [id, x] = next;
      this.t = x.at;
      if (x.every) x.at += x.every; else this.timers.delete(id);
      x.fn();
      await flush();
    }
    this.t = end;
  }
}

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

function mk({ ticketStatus = () => 200, visible = { v: true } } = {}) {
  const sockets = [];
  const fetches = [];
  class FakeWS {
    constructor(url, protocols) {
      this.url = url; this.protocols = protocols; this.readyState = 0; this.sent = []; this.closedWith = null;
      sockets.push(this);
    }
    send(s) { if (this.readyState !== 1) throw new Error('not open'); this.sent.push(s); }
    close(code) { this.closedWith = code; this.readyState = 3; }
    open() { this.readyState = 1; this.onopen && this.onopen(); }
    recv(obj) { this.onmessage && this.onmessage({ data: JSON.stringify(obj) }); }
    drop(code = 1006) { this.readyState = 3; this.onclose && this.onclose({ code }); }
  }
  const fetchImpl = async (url, init) => {
    fetches.push({ url, init });
    const status = ticketStatus(fetches.length);
    const body = status === 200 ? { url: URL, ticket: 'v1.p.m' + fetches.length, exp: 0 } : { error: 'x' };
    return { status, json: async () => body };
  };
  const sched = new Sched();
  const statuses = [];
  const frames = [];
  const c = new RelayClient({ roomId: ROOM, writeKey: KEY, bid: BID, scheduler: sched, WebSocketImpl: FakeWS,
    fetchImpl, random: () => 0.5, doc: null, isVisible: () => visible.v,
    onStatus: (s) => statuses.push(s), onFrame: (f) => frames.push(f) });
  return { c, sockets, fetches, sched, statuses, frames, visible };
}

const welcome = { t: 'welcome', v: 1, conn: 'c:AAAAAAAAAAAA', role: 'browser', id: BID, serverTime: 1, limits: {} };

test('ticket request, subprotocols, hello, live, presence, send', async () => {
  const { c, sockets, fetches, statuses, frames } = mk();
  await c.start();
  await flush();
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0].url, `/api/relay_ticket?room=${ROOM}`);
  assert.equal(fetches[0].init.method, 'POST');
  assert.equal(fetches[0].init.headers.Authorization, `Bearer ${KEY}`);
  assert.deepEqual(JSON.parse(fetches[0].init.body), { role: 'browser', id: BID });
  const ws = sockets[0];
  assert.equal(ws.url, URL);                                  // no ticket in the URL
  assert.deepEqual(ws.protocols, ['pixel-relay.v1', 'tk.v1.p.m1']);
  assert.equal(c.send({ t: 'presence.get' }), false);         // not live yet
  ws.open();
  assert.deepEqual(JSON.parse(ws.sent[0]), { t: 'hello', v: 1, app: 'web/3.0.0', caps: ['e2e1'] });
  ws.recv(welcome);
  ws.recv({ t: 'presence', devices: [{ id: 'abcdefghijklmnop', since: 5, app: 'connector/3.0.0' }, { id: 'BAD' }] });
  assert.equal(c.status, 'live');
  assert.equal(c.conn, 'c:AAAAAAAAAAAA');
  assert.deepEqual([...c.presence], [['abcdefghijklmnop', { since: 5, app: 'connector/3.0.0' }]]);
  assert.equal(c.send({ t: 'presence.get' }), true);
  assert.deepEqual(statuses, ['connecting', 'live']);
  assert.deepEqual(frames.map((f) => f.t), ['welcome', 'presence']);
  c.stop();
  assert.equal(ws.closedWith, 1000);
  assert.equal(c.status, 'idle');
});

test('app-level ping every 50 s while live', async () => {
  const { c, sockets, sched } = mk();
  await c.start();
  await flush();
  sockets[0].open();
  sockets[0].recv(welcome);
  await sched.advance(PING_MS * 2);
  assert.deepEqual(sockets[0].sent.slice(1), ['{"t":"ping"}', '{"t":"ping"}']);
  sockets[0].recv({ t: 'pong' });
  c.stop();
});

test('reconnect with backoff and a fresh ticket each time; quota after two failed attempts', async () => {
  const { c, sockets, fetches, sched, statuses } = mk();
  await c.start();
  await flush();
  sockets[0].open();
  sockets[0].recv(welcome);
  sockets[0].drop(1006);                                      // was live: not a failure
  assert.equal(c.status, 'reconnecting');
  assert.deepEqual(sched.pending(), [1000]);                  // 1 s (jitter 0.5 → ×1.0)
  await sched.advance(1000);
  assert.equal(fetches.length, 2);
  assert.equal(sockets[1].protocols[1], 'tk.v1.p.m2');
  sockets[1].drop(1006);                                      // refused upgrade (e.g. 429): failure 1
  assert.deepEqual(sched.pending(), [2000]);
  assert.equal(c.status, 'reconnecting');
  await sched.advance(2000);
  sockets[2].drop(1006);                                      // failure 2 → quota
  assert.equal(c.status, 'quota');
  assert.deepEqual(sched.pending(), [4000]);
  await sched.advance(4000);
  sockets[3].open();
  sockets[3].recv(welcome);
  assert.equal(c.status, 'live');
  sockets[3].drop(4029);                                      // explicit rate-limit close counts too
  await sched.advance(1000);
  sockets[4].drop(4029);
  assert.equal(c.status, 'quota');
  assert.ok(statuses.includes('quota'));
  c.stop();
  assert.deepEqual(sched.pending(), []);
});

test('backoff caps at 30 s', async () => {
  const { c, sockets, sched } = mk();
  await c.start();
  await flush();
  const seen = [];
  for (let i = 0; i < 8; i++) {
    sockets.at(-1).drop(1006);
    seen.push(sched.pending()[0]);
    await sched.advance(seen.at(-1));
  }
  assert.deepEqual(seen, [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);
  c.stop();
});

test('fatal ticket answers stop the client; 429 retries', async () => {
  const a = mk({ ticketStatus: () => 503 });
  await a.c.start();
  await flush();
  assert.equal(a.c.status, 'idle');
  assert.deepEqual(a.sched.pending(), []);
  const b = mk({ ticketStatus: (n) => (n === 1 ? 429 : 200) });
  await b.c.start();
  await flush();
  assert.equal(b.sockets.length, 0);
  assert.deepEqual(b.sched.pending(), [1000]);
  await b.sched.advance(1000);
  assert.equal(b.sockets.length, 1);
  b.c.stop();
});

test('hidden page: disconnect after 60 s unless a task runs; reconnect when visible', async () => {
  const { c, sockets, sched, visible } = mk();
  c.need('dialog');
  await flush();
  sockets[0].open();
  sockets[0].recv(welcome);
  visible.v = false;
  c._visibilityChanged();
  await sched.advance(HIDDEN_GRACE_MS - 1);
  assert.equal(c.status, 'live');
  await sched.advance(1);
  assert.equal(c.status, 'idle');
  assert.equal(sockets[0].closedWith, 1000);
  visible.v = true;
  c._visibilityChanged();
  await flush();
  assert.equal(sockets.length, 2);
  sockets[1].open();
  sockets[1].recv(welcome);
  c.need('task');
  visible.v = false;
  c._visibilityChanged();
  await sched.advance(HIDDEN_GRACE_MS * 3);
  assert.equal(c.status, 'live');                             // a running task keeps the socket
  c.need('task', false);
  c.need('dialog', false);
  assert.equal(c.status, 'idle');
});

test('rejects bad constructor input and bad ticket URLs', async () => {
  assert.throws(() => new RelayClient({ roomId: 'x', writeKey: KEY, bid: BID, doc: null }));
  assert.throws(() => new RelayClient({ roomId: ROOM, writeKey: KEY, bid: 'x', doc: null }));
  const { c, sockets, sched } = mk();
  c.fetchImpl = async () => ({ status: 200, json: async () => ({ url: 'wss://evil.example/x?tk=1', ticket: 't' }) });
  await c.start();
  await flush();
  assert.equal(sockets.length, 0);
  assert.equal(sched.pending().length, 1);
  c.stop();
});

test('CSP violation for the relay host stops the client with error csp (Phase 15)', async () => {
  const listeners = {};
  const doc = {
    visibilityState: 'visible',
    addEventListener: (t, fn) => { listeners[t] = fn; },
    removeEventListener: (t, fn) => { if (listeners[t] === fn) delete listeners[t]; },
  };
  const { c, sockets, sched, statuses } = mk();
  c.doc = doc;
  c.destroy();                                   // re-create with the fake document
  const details = [];
  const c2 = new RelayClient({ roomId: ROOM, writeKey: KEY, bid: BID, scheduler: sched, WebSocketImpl: c.WebSocketImpl,
    fetchImpl: c.fetchImpl, random: () => 0.5, doc, onStatus: (s, d) => { statuses.push(s); details.push(d); } });
  assert.equal(typeof listeners.securitypolicyviolation, 'function');
  await c2.start();
  await flush();
  assert.equal(sockets.length, 1);
  // a violation for some other host is ignored
  listeners.securitypolicyviolation({ effectiveDirective: 'connect-src', blockedURI: 'wss://other.example' });
  assert.equal(c2.status, 'connecting');
  sockets[0].drop(1006);                         // the browser fails the blocked socket …
  listeners.securitypolicyviolation({ effectiveDirective: 'connect-src', blockedURI: URL });   // … and reports it
  assert.equal(c2.status, 'idle');
  assert.equal(c2.lastError, 'csp');
  assert.deepEqual(details[details.length - 1], { error: 'csp', host: 'pixel-relay.test.workers.dev' });
  await sched.advance(120_000);
  assert.equal(sockets.length, 1);               // no retries
  c2.destroy();
  assert.equal(listeners.securitypolicyviolation, undefined);
});
