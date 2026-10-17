// node:test tests for the Worker routing and the Durable Object room logic (core.js) with mocked DO state,
// WebSockets and stub — no workerd needed. Also run locally in headless Chrome by tests/test_relay_js.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vectors from './ticket_vectors.json' with { type: 'json' };
import { CLAIMS_HEADER, RoomCore, handleRequest } from '../src/core.js';
import { MAX_FRAME, PING_FRAME, PONG_FRAME, b64urlEncode, mintTicket } from '../src/lib.js';

const ROOM = 'abcdefghijklmnopqrstuvwxyz';
const ORIGIN = 'https://pixel-rouge-phi.vercel.app';

// -- mocks ----------------------------------------------------------------------------------------------------

class MockWS {
  constructor() {
    this.sent = [];
    this.readyState = 1;
    this.closed = null;
    this.att = null;
    this.tags = [];
  }

  send(text) {
    if (this.readyState !== 1) throw new Error('not open');
    this.sent.push(text);
  }

  close(code, reason) {
    if (this.readyState !== 1) throw new Error('already closing');
    this.readyState = 2;
    this.closed = { code, reason };
  }

  serializeAttachment(v) {
    this.att = JSON.parse(JSON.stringify(v));
  }

  deserializeAttachment() {
    return this.att === null ? null : JSON.parse(JSON.stringify(this.att));
  }

  frames() {
    const out = this.sent.map((s) => JSON.parse(s));
    this.sent = [];
    return out;
  }
}

class MockCtx {
  constructor() {
    this.sockets = [];
    this.auto = null;
  }

  acceptWebSocket(ws, tags) {
    ws.tags = tags;
    this.sockets.push(ws);
  }

  getWebSockets(tag) {
    return this.sockets.filter((ws) => ws.readyState !== 3 && (tag === undefined || ws.tags.includes(tag)));
  }

  setWebSocketAutoResponse(pair) {
    this.auto = pair;
  }
}

function mkRoom() {
  const clock = { t: 1_792_100_000_000 };
  const ctx = new MockCtx();
  const platform = {
    now: () => clock.t,
    newPair: () => [new MockWS(), new MockWS()],
    upgradeResponse: (client) => ({ status: 101, client }),
    autoResponse: (req, res) => ({ req, res }),
  };
  const core = new RoomCore(ctx, platform);
  const connect = async (role, id) => {
    const res = await core.fetch(fakeReq({ Upgrade: 'websocket', [CLAIMS_HEADER]: JSON.stringify({ r: ROOM, ro: role, id }) }));
    if (res.status !== 101) return { res };
    const server = ctx.sockets[ctx.sockets.length - 1];
    return { res, ws: server };
  };
  const disconnect = async (ws) => {
    ws.readyState = 3;
    await core.webSocketClose(ws, 1000, '', true);
  };
  return { clock, ctx, core, connect, disconnect };
}

function fakeReq(headers, url = `https://relay.test/v1/rooms/${ROOM}/ws`, method = 'GET') {
  return { url, method, headers: new Headers(headers) };   // plain object: browsers drop Upgrade/Origin from Request
}

const did = (i) => 'devicezzzzzzzzz' + 'abcdefghij'[i];   // 16 chars [a-z2-7]
const DEV1 = 'deviceaaaaaaaaa2';
const DEV2 = 'devicebbbbbbbbb3';
const BID = 'browseraaaaaaaa4';
const B = b64urlEncode(new Uint8Array([1, 2, 3, 4, 5, 6]));

// -- room logic -----------------------------------------------------------------------------------------------

test('auto-response for {"t":"ping"} is registered', () => {
  const { ctx } = mkRoom();
  assert.deepEqual(ctx.auto, { req: PING_FRAME, res: PONG_FRAME });
});

test('welcome, presence and routing both ways', async () => {
  const r = mkRoom();
  const { ws: dev } = await r.connect('device', DEV1);
  assert.deepEqual(dev.tags, ['dev', 'dev:' + DEV1]);
  const [w] = dev.frames();
  assert.deepEqual(w, { t: 'welcome', v: 1, conn: null, role: 'device', id: DEV1, serverTime: r.clock.t,
    limits: { maxFrame: MAX_FRAME, msgPerSec: 20 } });
  const { ws: br } = await r.connect('browser', BID);
  const [bw, bp] = br.frames();
  assert.equal(bw.role, 'browser');
  assert.match(bw.conn, /^c:[A-Za-z0-9_-]{12}$/);
  assert.deepEqual(br.tags, ['br', 'br:' + BID, 'conn:' + bw.conn]);
  assert.deepEqual(bp, { t: 'presence', devices: [{ id: DEV1, since: r.clock.t, app: null }] });

  await r.core.webSocketMessage(br, JSON.stringify({ t: 'send', to: DEV1, ch: null, seq: 0, k: 'hs', b: B }));
  assert.deepEqual(dev.frames(), [{ t: 'msg', from: bw.conn, bid: BID, ch: null, seq: 0, k: 'hs', b: B }]);
  await r.core.webSocketMessage(dev, JSON.stringify({ t: 'send', to: bw.conn, ch: 'C'.repeat(16), seq: 5, k: 'e', b: B }));
  assert.deepEqual(br.frames(), [{ t: 'msg', from: DEV1, bid: null, ch: 'C'.repeat(16), seq: 5, k: 'e', b: B }]);

  await r.core.webSocketMessage(dev, JSON.stringify({ t: 'hello', v: 1, app: 'connector/3.0.0' }));
  assert.deepEqual(br.frames(), [{ t: 'presence', devices: [{ id: DEV1, since: r.clock.t, app: 'connector/3.0.0' }] }]);
  await r.core.webSocketMessage(br, '{"t":"presence.get"}');
  assert.equal(br.frames()[0].devices[0].app, 'connector/3.0.0');

  await r.disconnect(br);
  assert.deepEqual(dev.frames(), [{ t: 'left', conn: bw.conn }]);
  const { ws: br2 } = await r.connect('browser', BID);
  br2.frames();
  await r.disconnect(dev);
  assert.deepEqual(br2.frames(), [{ t: 'presence', devices: [] }]);
});

test('offline targets, no queueing', async () => {
  const r = mkRoom();
  const { ws: br } = await r.connect('browser', BID);
  const { ws: dev } = await r.connect('device', DEV1);
  br.frames();
  dev.frames();
  await r.core.webSocketMessage(br, JSON.stringify({ t: 'send', to: DEV2, seq: 9, k: 'e', b: B }));
  assert.deepEqual(br.frames().map((f) => [f.t, f.code, f.ref]), [['err', 'device_offline', 9]]);
  await r.core.webSocketMessage(dev, JSON.stringify({ t: 'send', to: 'c:AAAAAAAAAAAA', seq: 3, k: 'e', b: B }));
  assert.deepEqual(dev.frames().map((f) => [f.code, f.ref]), [['conn_gone', 3]]);
});

test('device replaced → 4009, room caps → 429', async () => {
  const r = mkRoom();
  const { ws: br } = await r.connect('browser', BID);
  const { ws: d1 } = await r.connect('device', DEV1);
  const { ws: d2 } = await r.connect('device', DEV1);
  assert.deepEqual(d1.closed, { code: 4009, reason: 'replaced' });
  br.frames();
  await r.disconnect(d1);                      // the old socket's close must not drop the device from presence
  assert.deepEqual(br.frames(), []);
  await r.core.webSocketMessage(br, JSON.stringify({ t: 'send', to: DEV1, seq: 1, k: 'e', b: B }));
  assert.equal(d2.frames().filter((f) => f.t === 'msg').length, 1);

  for (let i = 0; i < 7; i++) assert.equal((await r.connect('device', did(i))).res.status, 101);
  const full = await r.connect('device', did(9));
  assert.equal(full.res.status, 429);
  assert.deepEqual(await full.res.json(), { error: 'room_full' });
  assert.equal((await r.connect('device', did(3))).res.status, 101);   // replacing itself is allowed when full
  for (let i = 0; i < 23; i++) assert.equal((await r.connect('browser', BID)).res.status, 101);
  assert.equal((await r.connect('browser', BID)).res.status, 429);
});

test('pairing slots: open, route, taken, limit, close, expiry, pair rate limit', async () => {
  const r = mkRoom();
  const { ws: dev } = await r.connect('device', DEV1);
  const { ws: br } = await r.connect('browser', BID);
  dev.frames();
  const conn = br.frames()[0].conn;
  await r.core.webSocketMessage(dev, '{"t":"pair.open","slot":"K7Q2"}');
  assert.deepEqual(dev.frames(), [{ t: 'pair.opened', slot: 'K7Q2', exp: r.clock.t + 600_000 }]);
  await r.core.webSocketMessage(br, JSON.stringify({ t: 'send', to: 'pair:K7Q2', seq: 0, k: 'hs', b: B }));
  assert.equal(dev.frames()[0].from, conn);
  const { ws: dev2 } = await r.connect('device', DEV2);
  dev2.frames();
  await r.core.webSocketMessage(dev2, '{"t":"pair.open","slot":"K7Q2"}');
  assert.equal(dev2.frames()[0].code, 'slot_taken');
  for (let i = 0; i < 3; i++) {
    const { ws } = await r.connect('device', did(i));
    await r.core.webSocketMessage(ws, `{"t":"pair.open","slot":"S${i}00"}`);
  }
  await r.core.webSocketMessage(dev2, '{"t":"pair.open","slot":"S900"}');
  assert.equal(dev2.frames().pop().code, 'slot_limit');
  br.frames();
  await r.core.webSocketMessage(br, JSON.stringify({ t: 'send', to: 'pair:K7Q2', seq: 1, k: 'hs', b: B }));
  await r.core.webSocketMessage(br, JSON.stringify({ t: 'send', to: 'pair:K7Q2', seq: 2, k: 'hs', b: B }));
  await r.core.webSocketMessage(br, JSON.stringify({ t: 'send', to: 'pair:K7Q2', seq: 3, k: 'hs', b: B }));
  assert.deepEqual(br.frames().map((f) => [f.code, f.ref]), [['rate_limited', 3]]);   // 3 per minute
  r.clock.t += 600_001;
  await r.core.webSocketMessage(br, JSON.stringify({ t: 'send', to: 'pair:K7Q2', seq: 4, k: 'hs', b: B }));
  assert.deepEqual(br.frames().map((f) => [f.code, f.ref]), [['slot_unknown', 4]]);
  await r.core.webSocketMessage(dev, '{"t":"pair.close","slot":"K7Q2"}');
  assert.equal(dev.frames().pop().code, 'slot_unknown');               // already expired
  await r.core.webSocketMessage(dev, '{"t":"pair.open","slot":"K7Q2"}');
  await r.core.webSocketMessage(dev, '{"t":"pair.close","slot":"K7Q2"}');
  assert.deepEqual(dev.frames().map((f) => f.t), ['pair.opened']);
});

test('limits: oversize → 1009, bad frames → 1008 after 5, rate limit → err then 4029', async () => {
  const r = mkRoom();
  const { ws: a } = await r.connect('browser', BID);
  await r.core.webSocketMessage(a, 'x'.repeat(MAX_FRAME + 1));
  assert.equal(a.closed.code, 1009);
  const { ws: u } = await r.connect('browser', BID);
  await r.core.webSocketMessage(u, '{"t":"send","b":"' + 'ğ'.repeat(MAX_FRAME / 2) + '"}');   // > MAX_FRAME bytes in UTF-8
  assert.equal(u.closed.code, 1009);

  const { ws: b } = await r.connect('browser', BID);
  b.frames();
  for (let i = 0; i < 4; i++) await r.core.webSocketMessage(b, 'junk');
  assert.deepEqual(b.frames().map((f) => f.code), ['bad_frame', 'bad_frame', 'bad_frame', 'bad_frame']);
  await r.core.webSocketMessage(b, new Uint8Array([1]));
  assert.equal(b.closed.code, 1008);

  const { ws: c } = await r.connect('browser', BID);
  c.frames();
  const frame = JSON.stringify({ t: 'send', to: DEV1, seq: 1, k: 'e', b: B });
  for (let i = 0; i < 30; i++) await r.core.webSocketMessage(c, frame);
  assert.ok(c.frames().every((f) => f.code === 'device_offline'));
  await r.core.webSocketMessage(c, frame);
  assert.deepEqual(c.frames().map((f) => [f.code, f.ref]), [['rate_limited', 1]]);
  r.clock.t += 200;                            // one token back
  await r.core.webSocketMessage(c, frame);
  assert.equal(c.frames()[0].code, 'device_offline');
  for (let i = 0; i < 19; i++) await r.core.webSocketMessage(c, frame);
  assert.equal(c.closed, null);
  await r.core.webSocketMessage(c, frame);
  assert.equal(c.closed.code, 4029);
});

test('ping reaching the object is answered and not rate limited; version gate', async () => {
  const r = mkRoom();
  const { ws } = await r.connect('device', DEV1);
  ws.frames();
  for (let i = 0; i < 200; i++) await r.core.webSocketMessage(ws, PING_FRAME);
  assert.equal(ws.frames().length, 200);
  await r.core.webSocketMessage(ws, '{"t":"hello","v":2}');
  assert.equal(ws.frames()[0].code, 'version');
});

test('fetch without trusted claims is refused', async () => {
  const r = mkRoom();
  assert.equal((await r.core.fetch(fakeReq({ Upgrade: 'websocket' }))).status, 400);
  assert.equal((await r.core.fetch(fakeReq({ Upgrade: 'websocket', [CLAIMS_HEADER]: '{"ro":"admin","id":"x"}' }))).status, 400);
  assert.equal((await r.core.fetch(fakeReq({ [CLAIMS_HEADER]: JSON.stringify({ ro: 'device', id: DEV1 }) }))).status, 426);
});

// -- Worker routing -------------------------------------------------------------------------------------------

function mkEnv() {
  const calls = [];
  const env = {
    RELAY_SECRET: vectors.secret,
    ALLOWED_ORIGINS: ORIGIN + ',https://pixeloffice.com.tr',
    ROOMS: {
      idFromName: (name) => ({ name }),
      get: (id) => ({ fetch: async (req) => { calls.push({ id, req }); return { status: 101 }; } }),
    },
  };
  return { env, calls };
}

async function ticket(role, id, iat, room = ROOM) {
  return mintTicket(vectors.secret, { r: room, ro: role, id, iat, exp: iat + 120, n: 'AAAAAAAAAAA' });
}

test('worker: /health and 404s', async () => {
  const { env } = mkEnv();
  const p = { now: () => 0 };
  const h = await handleRequest(fakeReq({}, 'https://relay.test/health'), env, p);
  assert.equal(h.status, 200);
  assert.deepEqual(await h.json(), { ok: true, v: 1 });
  assert.equal(h.headers.get('Cache-Control'), 'no-store');
  assert.equal((await handleRequest(fakeReq({}, 'https://relay.test/health', 'POST'), env, p)).status, 405);
  for (const path of ['/', '/v1/rooms/x/ws', `/v1/rooms/${ROOM}/ws/x`]) {
    assert.equal((await handleRequest(fakeReq({}, 'https://relay.test' + path), env, p)).status, 404, path);
  }
  assert.equal((await handleRequest(fakeReq({}), env, p)).status, 426);
  assert.equal((await handleRequest(fakeReq({ Upgrade: 'websocket' }), { ...env, RELAY_SECRET: '' }, p)).status, 503);
});

test('worker: ticket + origin checks, claims forwarded to the room DO', async () => {
  const now = 1_792_100_000;
  const p = { now: () => now * 1000 };
  const { env, calls } = mkEnv();
  const up = (proto, origin) => fakeReq({ Upgrade: 'websocket', 'Sec-WebSocket-Protocol': proto,
    ...(origin ? { Origin: origin } : {}), [CLAIMS_HEADER]: '{"ro":"device","id":"forgedforgedforg"}' });
  const tb = await ticket('browser', BID, now);
  const td = await ticket('device', DEV1, now);

  const res = await handleRequest(up(`pixel-relay.v1, tk.${tb}`, ORIGIN), env, p);
  assert.equal(res.status, 101);
  assert.deepEqual(calls[0].id, { name: ROOM });
  assert.deepEqual(JSON.parse(calls[0].req.headers.get(CLAIMS_HEADER)), { r: ROOM, ro: 'browser', id: BID });
  assert.equal((await handleRequest(up(`pixel-relay.v1, tk.${td}`, null), env, p)).status, 101);
  assert.deepEqual(JSON.parse(calls[1].req.headers.get(CLAIMS_HEADER)), { r: ROOM, ro: 'device', id: DEV1 });

  const rej = async (req, status, code) => {
    const r = await handleRequest(req, env, p);
    assert.equal(r.status, status);
    assert.deepEqual(await r.json(), { error: code });
  };
  await rej(up('pixel-relay.v1', ORIGIN), 401, 'bad_ticket');
  await rej(up(`tk.${tb}`, ORIGIN), 401, 'bad_ticket');
  await rej(up(`pixel-relay.v1, tk.${tb.slice(0, -2)}AA`, ORIGIN), 401, 'bad_ticket');
  await rej(up(`pixel-relay.v1, tk.${await ticket('browser', BID, now - 121)}`, ORIGIN), 401, 'bad_ticket');
  await rej(up(`pixel-relay.v1, tk.${await ticket('browser', BID, now + 31)}`, ORIGIN), 401, 'bad_ticket');
  await rej(up(`pixel-relay.v1, tk.${await ticket('browser', BID, now, 'zyxwvutsrqponmlkjihgfedcba')}`, ORIGIN), 401, 'bad_ticket');
  await rej(up(`pixel-relay.v1, tk.${tb}`, null), 403, 'bad_origin');
  await rej(up(`pixel-relay.v1, tk.${tb}`, 'https://evil.example'), 403, 'bad_origin');
  await rej(up(`pixel-relay.v1, tk.${td}`, ORIGIN), 403, 'bad_origin');
  assert.equal(calls.length, 2);
});
