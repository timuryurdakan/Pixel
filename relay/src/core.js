// Pixel Office relay — Worker routing and Durable Object room logic, written against small injected interfaces
// so that it runs without `cloudflare:*` imports (node:test in Workers Builds, headless Chrome locally).
// room.js / index.js are the thin Cloudflare glue around this file (V3.md §4.2, §4.3, §20 Phase 12 deviations).
//
// Invariants (V3.md §3.3): the relay never parses, logs or stores `b`; there is no storage use and there are no
// alarms or timers (they would prevent hibernation); every `send` goes to exactly one socket; nothing is queued.

import {
  BAD_FRAME_LIMIT, BAD_FRAME_WINDOW_MS, CLOSE, MAX_BROWSERS, MAX_DEVICES, MAX_FRAME, MAX_SLOTS, PEER_ID_RE,
  PING_FRAME, PONG_FRAME, PROTOCOL_VERSION, RATE, RATE_STRIKE_LIMIT, ROLES, SLOT_TTL_MS, SUBPROTOCOL, TokenBucket,
  errFrame, jsonResponseInit, matchRoomPath, originAllowed, parseOrigins, randomConn, ticketFromProtocols, utf8Length,
  validateFrame, verifyTicket,
} from './lib.js';

export const CLAIMS_HEADER = 'X-Pixel-Claims';

/** Cloudflare runtime bindings, resolved lazily so this module also loads where they don't exist. */
export function cloudflarePlatform() {
  return {
    now: () => Date.now(),
    newPair: () => {
      const pair = new globalThis.WebSocketPair();
      return [pair[0], pair[1]];
    },
    upgradeResponse: (client) => new Response(null, {
      status: 101, webSocket: client, headers: { 'Sec-WebSocket-Protocol': SUBPROTOCOL },
    }),
    autoResponse: (req, res) => new globalThis.WebSocketRequestResponsePair(req, res),
  };
}

function jsonError(status, code, extra = {}) {
  return new Response(JSON.stringify({ error: code }), jsonResponseInit(status, extra));
}

// -- Worker fetch (V3.md §3.1, §4.2, §6.2) -------------------------------------------------------------------

export async function handleRequest(req, env, platform) {
  const url = new URL(req.url);
  if (url.pathname === '/health') {
    if (req.method !== 'GET' && req.method !== 'HEAD') return jsonError(405, 'method_not_allowed', { Allow: 'GET, HEAD' });
    return new Response(JSON.stringify({ ok: true, v: PROTOCOL_VERSION }), jsonResponseInit(200));
  }
  const rid = matchRoomPath(url.pathname);
  if (!rid) return jsonError(404, 'not_found');
  if (req.method !== 'GET') return jsonError(405, 'method_not_allowed', { Allow: 'GET' });
  if ((req.headers.get('Upgrade') || '').toLowerCase() !== 'websocket') return jsonError(426, 'upgrade_required');
  if (!env.RELAY_SECRET) return jsonError(503, 'relay_not_configured');
  const ticket = ticketFromProtocols(req.headers.get('Sec-WebSocket-Protocol'));
  if (!ticket) return jsonError(401, 'bad_ticket');
  let claims;
  try {
    claims = await verifyTicket(env.RELAY_SECRET, ticket, Math.floor(platform.now() / 1000), rid);
  } catch {
    return jsonError(401, 'bad_ticket');
  }
  if (!originAllowed(claims.ro, req.headers.get('Origin'), parseOrigins(env.ALLOWED_ORIGINS))) {
    return jsonError(403, 'bad_origin');
  }
  const headers = new Headers(req.headers);
  headers.set(CLAIMS_HEADER, JSON.stringify({ r: claims.r, ro: claims.ro, id: claims.id }));   // always overwritten
  const stub = env.ROOMS.get(env.ROOMS.idFromName(rid));
  return stub.fetch(new Request(req.url, { method: 'GET', headers }));
}

// -- Durable Object room (V3.md §4.3) ------------------------------------------------------------------------

const OPEN = 1;

export class RoomCore {
  constructor(ctx, platform) {
    this.ctx = ctx;
    this.p = platform;
    this.limits = new Map();   // socket key → {main, pair, strikes, bad: [ms…]}; in memory, reset on hibernation
    if (platform.autoResponse) ctx.setWebSocketAutoResponse(platform.autoResponse(PING_FRAME, PONG_FRAME));
  }

  // -- helpers --
  att(ws) {
    try {
      return ws.deserializeAttachment() || null;
    } catch {
      return null;
    }
  }

  live(tag, exclude = null) {
    return this.ctx.getWebSockets(tag).filter((ws) => {
      if (ws === exclude || (ws.readyState !== undefined && ws.readyState !== OPEN)) return false;
      const a = this.att(ws);
      return a && !a.replaced;
    });
  }

  send(ws, text) {
    try {
      ws.send(text);
      return true;
    } catch {
      return false;
    }
  }

  close(ws, code, reason) {
    try {
      ws.close(code, reason);
    } catch { /* already closed */ }
  }

  key(a) {
    return a.conn || 'dev:' + a.id;
  }

  limitState(a, now) {
    const k = this.key(a);
    let s = this.limits.get(k);
    if (!s) {
      const [rate, burst] = RATE[a.role];
      s = { main: new TokenBucket(rate, burst, now), pair: null, strikes: 0, bad: [] };
      this.limits.set(k, s);
    }
    return s;
  }

  presenceFrame(exclude = null) {
    const byId = new Map();
    for (const ws of this.live('dev', exclude)) {
      const a = this.att(ws);
      const prev = byId.get(a.id);
      if (!prev || a.since >= prev.since) byId.set(a.id, { id: a.id, since: a.since, app: a.app || null });
    }
    return JSON.stringify({ t: 'presence', devices: [...byId.values()].sort((x, y) => x.since - y.since) });
  }

  broadcastPresence(exclude = null) {
    const frame = this.presenceFrame(exclude);
    for (const ws of this.live('br', exclude)) this.send(ws, frame);
  }

  // -- upgrade --
  async fetch(req) {
    let claims = null;
    try {
      claims = JSON.parse(req.headers.get(CLAIMS_HEADER) || 'null');
    } catch { /* fallthrough */ }
    if (!claims || !ROLES.includes(claims.ro) || typeof claims.id !== 'string' || !PEER_ID_RE.test(claims.id)) {
      return jsonError(400, 'bad_request');
    }
    if ((req.headers.get('Upgrade') || '').toLowerCase() !== 'websocket') return jsonError(426, 'upgrade_required');
    const now = this.p.now();
    const role = claims.ro;
    const id = claims.id;
    let replaced = [];
    if (role === 'device') {
      replaced = this.live('dev:' + id);
      const others = this.live('dev').filter((ws) => !replaced.includes(ws));
      if (others.length >= MAX_DEVICES) return jsonError(429, 'room_full');
    } else if (this.live('br').length >= MAX_BROWSERS) {
      return jsonError(429, 'room_full');
    }
    const [client, server] = this.p.newPair();
    const conn = role === 'browser' ? randomConn() : null;
    const tags = role === 'device' ? ['dev', 'dev:' + id] : ['br', 'br:' + id, 'conn:' + conn];
    this.ctx.acceptWebSocket(server, tags);
    server.serializeAttachment({ role, id, conn, since: now, app: null, slot: null, slotExp: 0 });
    for (const old of replaced) {
      const oa = this.att(old);
      if (oa) {
        try {
          old.serializeAttachment({ ...oa, replaced: true, slot: null, slotExp: 0 });
        } catch { /* ignore */ }
        this.limits.delete(this.key(oa));
      }
      this.close(old, CLOSE.REPLACED, 'replaced');
    }
    const [rate] = RATE[role];
    this.send(server, JSON.stringify({
      t: 'welcome', v: PROTOCOL_VERSION, conn, role, id, serverTime: now, limits: { maxFrame: MAX_FRAME, msgPerSec: rate },
    }));
    if (role === 'browser') this.send(server, this.presenceFrame());
    else this.broadcastPresence();
    return this.p.upgradeResponse(client);
  }

  // -- messages --
  async webSocketMessage(ws, data) {
    const a = this.att(ws);
    if (!a || a.replaced) return;
    const now = this.p.now();
    if (typeof data !== 'string') {
      this.badFrame(ws, a, now, 'bad_frame', null);
      return;
    }
    if (data.length > MAX_FRAME || (data.length > MAX_FRAME / 3 && utf8Length(data) > MAX_FRAME)) {
      this.close(ws, CLOSE.TOO_BIG, 'too big');
      return;
    }
    if (data === PING_FRAME) {   // normally answered by the auto-response without waking the object
      this.send(ws, PONG_FRAME);
      return;
    }
    const v = validateFrame(data, a.role);
    const s = this.limitState(a, now);
    if (!s.main.take(now)) {
      this.rateLimited(ws, s, v.ok ? (v.frame.seq ?? null) : v.ref);
      return;
    }
    s.strikes = 0;
    if (!v.ok) {
      this.badFrame(ws, a, now, v.code, v.ref);
      return;
    }
    const f = v.frame;
    switch (f.t) {
      case 'hello':
        a.app = f.app;
        ws.serializeAttachment(a);
        if (a.role === 'device') this.broadcastPresence();
        return;
      case 'ping':
        this.send(ws, PONG_FRAME);
        return;
      case 'presence.get':
        this.send(ws, this.presenceFrame());
        return;
      case 'pair.open':
        this.pairOpen(ws, a, f.slot, now);
        return;
      case 'pair.close':
        if (a.slot === f.slot && a.slotExp > now) {
          a.slot = null;
          a.slotExp = 0;
          ws.serializeAttachment(a);
        } else {
          this.send(ws, errFrame('slot_unknown'));
        }
        return;
      case 'send':
        this.route(ws, a, f, s, now);
        return;
      default:
        this.badFrame(ws, a, now, 'bad_frame', null);
    }
  }

  rateLimited(ws, s, ref) {
    s.strikes += 1;
    if (s.strikes >= RATE_STRIKE_LIMIT) this.close(ws, CLOSE.RATE, 'rate limited');
    else this.send(ws, errFrame('rate_limited', ref));
  }

  badFrame(ws, a, now, code, ref) {
    const s = this.limitState(a, now);
    s.bad = s.bad.filter((t) => now - t < BAD_FRAME_WINDOW_MS);
    s.bad.push(now);
    if (s.bad.length >= BAD_FRAME_LIMIT) {
      this.close(ws, CLOSE.POLICY, 'bad frames');
      return;
    }
    this.send(ws, errFrame(code, ref));
  }

  pairOpen(ws, a, slot, now) {
    let active = 0;
    for (const other of this.live('dev')) {
      if (other === ws) continue;
      const oa = this.att(other);
      if (oa.slot && oa.slotExp > now) {
        if (oa.slot === slot) {
          this.send(ws, errFrame('slot_taken'));
          return;
        }
        active += 1;
      }
    }
    if (active >= MAX_SLOTS) {
      this.send(ws, errFrame('slot_limit'));
      return;
    }
    a.slot = slot;                     // at most one slot per device: a new pair.open replaces the old one
    a.slotExp = now + SLOT_TTL_MS;
    ws.serializeAttachment(a);
    this.send(ws, JSON.stringify({ t: 'pair.opened', slot, exp: a.slotExp }));
  }

  findSlot(slot, now) {
    for (const ws of this.live('dev')) {
      const a = this.att(ws);
      if (a.slot === slot && a.slotExp > now) return ws;
    }
    return null;
  }

  route(ws, a, f, s, now) {
    let target = null;
    let missing = 'device_offline';
    if (f.target.kind === 'device') {
      const list = this.live('dev:' + f.target.id);
      target = list.length ? list[list.length - 1] : null;
    } else if (f.target.kind === 'pair') {
      if (!s.pair) s.pair = new TokenBucket(RATE.pair[0], RATE.pair[1], now);
      if (!s.pair.take(now)) {
        this.rateLimited(ws, s, f.seq);
        return;
      }
      target = this.findSlot(f.target.id, now);
      missing = 'slot_unknown';
    } else {
      const list = this.live('conn:' + f.target.id);
      target = list.length ? list[0] : null;
      missing = 'conn_gone';
    }
    const msg = target && JSON.stringify({
      t: 'msg', from: a.role === 'device' ? a.id : a.conn, bid: a.role === 'browser' ? a.id : null,
      ch: f.ch, seq: f.seq, k: f.k, b: f.b,
    });
    if (!target || !this.send(target, msg)) this.send(ws, errFrame(missing, f.seq));
  }

  // -- close --
  async webSocketClose(ws, code, reason, wasClean) {
    this.gone(ws);
  }

  async webSocketError(ws, error) {
    this.gone(ws);
  }

  gone(ws) {
    const a = this.att(ws);
    if (!a) return;
    if (a.replaced) return;
    this.limits.delete(this.key(a));
    try {
      ws.serializeAttachment({ ...a, replaced: true });   // never counted or routed to again
    } catch { /* ignore */ }
    this.close(ws, CLOSE.NORMAL, '');
    if (a.role === 'device') {
      this.broadcastPresence(ws);
    } else {
      const frame = JSON.stringify({ t: 'left', conn: a.conn });
      for (const d of this.live('dev', ws)) this.send(d, frame);
    }
  }
}
