// Pixel Office relay — pure helpers (V3.md §3, §4, §6.2). No `cloudflare:*` imports, no I/O, no logging:
// this module runs unchanged under node:test (Workers Builds), in headless Chrome (local tests) and in workerd.
//
// The relay never parses, logs or stores the `b` field of a frame. It only checks that `b` is well-formed
// base64url of an allowed length.

export const PROTOCOL_VERSION = 1;
export const SUBPROTOCOL = 'pixel-relay.v1';
export const TICKET_PROTOCOL_PREFIX = 'tk.';
export const TICKET_VERSION = 'v1';
export const TICKET_MAC_PREFIX = 'pixel-relay-ticket:v1:';
export const TICKET_TTL = 120;            // s, exp - iat
export const MAX_FUTURE_SKEW = 30;        // s
export const MAX_TICKET_LEN = 512;
export const MAX_PROTOCOL_HEADER = 1024;

export const MAX_FRAME = 98304;           // bytes per text frame (96 KiB, V3.md §20 Phase 12 deviation 1)
export const MAX_B_BROWSER = 49152;       // decoded bytes of `b` from a browser (48 KiB)
export const MAX_B_DEVICE = 61440;        // decoded bytes of `b` from a device (60 KiB)
export const MAX_DEVICES = 8;             // device sockets per room
export const MAX_BROWSERS = 24;           // browser sockets per room
export const MAX_SLOTS = 4;               // open pairing slots per room
export const SLOT_TTL_MS = 600_000;       // pair.open lifetime
export const RATE = {                     // token buckets per socket: [tokens per second, burst]
  device: [20, 100],
  browser: [5, 30],
  pair: [3 / 60, 3],                      // `send` to a pair: slot, per browser socket
};
export const BAD_FRAME_LIMIT = 5;         // bad frames within BAD_FRAME_WINDOW_MS → close 1008
export const BAD_FRAME_WINDOW_MS = 60_000;
export const RATE_STRIKE_LIMIT = 20;      // consecutive rate-limited frames → close 4029

export const CLOSE = { NORMAL: 1000, POLICY: 1008, TOO_BIG: 1009, EXPIRED: 4001, REPLACED: 4009, RATE: 4029 };
export const PING_FRAME = '{"t":"ping"}';
export const PONG_FRAME = '{"t":"pong"}';

export const ROLES = ['browser', 'device'];
export const ROOM_RE = /^[a-z2-7]{26}$/;
export const PEER_ID_RE = /^[a-z2-7]{16}$/;
export const CONN_RE = /^c:[A-Za-z0-9_-]{12}$/;
export const SLOT_RE = /^[0-9A-HJKMNP-TV-Z]{4}$/;
export const CH_RE = /^[A-Za-z0-9_-]{16}$/;
export const NONCE_RE = /^[A-Za-z0-9_-]{1,32}$/;
export const B64URL_RE = /^[A-Za-z0-9_-]*$/;
export const APP_RE = /^[\x20-\x7e]{1,40}$/;
export const CAP_RE = /^[A-Za-z0-9_.:-]{1,32}$/;
export const ROOM_PATH_RE = /^\/v1\/rooms\/([a-z2-7]{26})\/ws$/;

const ERR_TEXT = {
  bad_frame: 'Malformed frame',
  too_large: 'Payload too large',
  rate_limited: 'Too many messages',
  device_offline: 'Device is not connected',
  conn_gone: 'Browser connection is gone',
  slot_unknown: 'Unknown or expired pairing slot',
  slot_taken: 'Pairing slot is in use',
  slot_limit: 'Too many pairing slots',
  forbidden: 'Not allowed for this role',
  version: 'Unsupported protocol version',
};

// -- base64url (RFC 4648 §5, no padding) -----------------------------------------------------------------

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const B64_INDEX = (() => {
  const m = new Int16Array(128).fill(-1);
  for (let i = 0; i < 64; i++) m[B64.charCodeAt(i)] = i;
  return m;
})();

export function b64urlEncode(bytes) {
  let out = '';
  let i = 0;
  for (; i + 2 < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out += B64[n >> 18] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + B64[n & 63];
  }
  if (i < bytes.length) {
    const n = (bytes[i] << 16) | ((i + 1 < bytes.length ? bytes[i + 1] : 0) << 8);
    out += B64[n >> 18] + B64[(n >> 12) & 63];
    if (i + 1 < bytes.length) out += B64[(n >> 6) & 63];
  }
  return out;
}

/** Strict decode: alphabet, no length ≡ 1 (mod 4), canonical (zero) trailing bits. Throws on error. */
export function b64urlDecode(s) {
  if (typeof s !== 'string' || !B64URL_RE.test(s) || s.length % 4 === 1) throw new Error('bad base64url');
  const out = new Uint8Array(Math.floor((s.length * 3) / 4));
  let o = 0;
  let acc = 0;
  let bits = 0;
  for (let i = 0; i < s.length; i++) {
    acc = (acc << 6) | B64_INDEX[s.charCodeAt(i)];
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[o++] = (acc >> bits) & 255;
    }
    acc &= (1 << bits) - 1;
  }
  if (acc !== 0) throw new Error('non-canonical base64url');
  return out;
}

/** Decoded byte length of a well-formed unpadded base64url string. */
export function b64urlDecodedLen(s) {
  return Math.floor((s.length * 3) / 4);
}

// -- tickets (V3.md §6.2) ----------------------------------------------------------------------------------

const enc = new TextEncoder();
const keyCache = new Map();   // secret → Promise<CryptoKey> (one entry in practice)

function hmacKey(secret) {
  let p = keyCache.get(secret);
  if (!p) {
    if (keyCache.size > 4) keyCache.clear();
    p = crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
    keyCache.set(secret, p);
  }
  return p;
}

export class TicketError extends Error {
  constructor(reason) {
    super(reason);
    this.reason = reason;   // format | mac | claims | lifetime | not_yet_valid | expired | room
  }
}

/** Test/vector helper (Vercel mints the real tickets in pixel_cloud/relay.py). claims keys in this order. */
export async function mintTicket(secret, { r, ro, id, iat, exp, n }) {
  const payload = b64urlEncode(enc.encode(JSON.stringify({ r, ro, id, iat, exp, n })));
  const mac = await crypto.subtle.sign('HMAC', await hmacKey(secret), enc.encode(TICKET_MAC_PREFIX + payload));
  return `${TICKET_VERSION}.${payload}.${b64urlEncode(new Uint8Array(mac))}`;
}

/**
 * Verify a ticket. Same checks and order as pixel_cloud/relay.py `verify_ticket`:
 * format → MAC (crypto.subtle.verify, constant time, before any JSON parsing) → claim formats → lifetime → time → room.
 * @returns {Promise<{r, ro, id, iat, exp}>} or throws TicketError.
 */
export async function verifyTicket(secret, ticket, nowSec, room = null) {
  if (!secret || typeof ticket !== 'string' || ticket.length > MAX_TICKET_LEN) throw new TicketError('format');
  const parts = ticket.split('.');
  if (parts.length !== 3 || parts[0] !== TICKET_VERSION || !parts[1]) throw new TicketError('format');
  const [, payload, macS] = parts;
  let payloadBytes;
  let mac;
  try {
    payloadBytes = b64urlDecode(payload);
    mac = b64urlDecode(macS);
  } catch {
    throw new TicketError('format');
  }
  if (mac.length !== 32) throw new TicketError('mac');
  const ok = await crypto.subtle.verify('HMAC', await hmacKey(secret), mac, enc.encode(TICKET_MAC_PREFIX + payload));
  if (!ok) throw new TicketError('mac');
  let claims;
  try {
    claims = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(payloadBytes));
  } catch {
    throw new TicketError('claims');
  }
  if (claims === null || typeof claims !== 'object' || Array.isArray(claims)) throw new TicketError('claims');
  const { r, ro, id, iat, exp, n } = claims;
  if (typeof r !== 'string' || !ROOM_RE.test(r) || !ROLES.includes(ro) || typeof id !== 'string' || !PEER_ID_RE.test(id)
      || !Number.isSafeInteger(iat) || !Number.isSafeInteger(exp) || typeof n !== 'string' || !NONCE_RE.test(n)) {
    throw new TicketError('claims');
  }
  if (exp < iat || exp - iat > TICKET_TTL) throw new TicketError('lifetime');
  if (iat > nowSec + MAX_FUTURE_SKEW) throw new TicketError('not_yet_valid');
  if (exp < nowSec) throw new TicketError('expired');
  if (room !== null && r !== room) throw new TicketError('room');
  return { r, ro, id, iat, exp };
}

/** `Sec-WebSocket-Protocol: pixel-relay.v1, tk.<ticket>` → ticket, else null (needs both, exactly one tk.). */
export function ticketFromProtocols(header) {
  if (!header || header.length > MAX_PROTOCOL_HEADER) return null;
  const items = header.split(',').map((p) => p.trim());
  const tickets = items.filter((p) => p.startsWith(TICKET_PROTOCOL_PREFIX)).map((p) => p.slice(TICKET_PROTOCOL_PREFIX.length));
  if (!items.includes(SUBPROTOCOL) || tickets.length !== 1 || !tickets[0]) return null;
  return tickets[0];
}

export function parseOrigins(value) {
  return String(value || '').split(',').map((o) => o.trim().replace(/\/+$/, '')).filter(Boolean);
}

/** Browsers must send an allow-listed Origin; devices (the connector) must send none (V3.md §3.1). */
export function originAllowed(role, origin, allowed) {
  if (role === 'device') return origin === null || origin === undefined;
  return typeof origin === 'string' && allowed.includes(origin);
}

export function matchRoomPath(pathname) {
  const m = ROOM_PATH_RE.exec(pathname);
  return m ? m[1] : null;
}

// -- frames (V3.md §3.2) -----------------------------------------------------------------------------------

/** UTF-8 byte length of a string without allocating for the common ASCII case. */
export function utf8Length(s) {
  let n = s.length;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 0x80) continue;
    if (c < 0x800) n += 1;
    else if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length && (s.charCodeAt(i + 1) & 0xfc00) === 0xdc00) { n += 2; i++; }
    else n += 2;
  }
  return n;
}

export function errFrame(code, ref = null, message = null) {
  return JSON.stringify({ t: 'err', code, ref, message: message || ERR_TEXT[code] || code });
}

function fail(code, ref = null) {
  return { ok: false, code, ref };
}

/**
 * Parse + validate one client text frame for `role`. Returns
 *   {ok: true, frame}                    frame = normalised object with only the known fields
 *   {ok: false, code, ref}               code ∈ bad_frame | too_large | forbidden | version
 * `text` must already be a string ≤ MAX_FRAME bytes (the caller closes with 1009 otherwise).
 */
export function validateFrame(text, role) {
  let f;
  try {
    f = JSON.parse(text);
  } catch {
    return fail('bad_frame');
  }
  if (f === null || typeof f !== 'object' || Array.isArray(f) || typeof f.t !== 'string') return fail('bad_frame');
  const ref = Number.isSafeInteger(f.seq) && f.seq >= 0 ? f.seq : null;
  switch (f.t) {
    case 'hello': {
      if (f.v !== PROTOCOL_VERSION) return fail('version');
      const app = f.app === undefined || f.app === null ? null : f.app;
      if (app !== null && (typeof app !== 'string' || !APP_RE.test(app))) return fail('bad_frame');
      let caps = [];
      if (f.caps !== undefined && f.caps !== null) {
        if (!Array.isArray(f.caps) || f.caps.length > 16 || !f.caps.every((c) => typeof c === 'string' && CAP_RE.test(c))) {
          return fail('bad_frame');
        }
        caps = f.caps;
      }
      return { ok: true, frame: { t: 'hello', v: 1, app, caps } };
    }
    case 'send': {
      const { to, seq, k, b } = f;
      const ch = f.ch === undefined ? null : f.ch;
      if (typeof to !== 'string' || ref === null || (k !== 'hs' && k !== 'e') || typeof b !== 'string'
          || !(ch === null || (typeof ch === 'string' && CH_RE.test(ch)))) {
        return fail('bad_frame', ref);
      }
      let target;
      if (PEER_ID_RE.test(to)) target = { kind: 'device', id: to };
      else if (CONN_RE.test(to)) target = { kind: 'conn', id: to };
      else if (to.startsWith('pair:') && SLOT_RE.test(to.slice(5))) target = { kind: 'pair', id: to.slice(5) };
      else return fail('bad_frame', ref);
      if (b.length === 0 || !B64URL_RE.test(b) || b.length % 4 === 1) return fail('bad_frame', ref);
      if (b64urlDecodedLen(b) > (role === 'device' ? MAX_B_DEVICE : MAX_B_BROWSER)) return fail('too_large', ref);
      if (role === 'browser' && target.kind === 'conn') return fail('forbidden', ref);
      if (role === 'device' && target.kind !== 'conn') return fail('forbidden', ref);
      return { ok: true, frame: { t: 'send', to, target, ch, seq, k, b } };
    }
    case 'pair.open':
    case 'pair.close':
      if (typeof f.slot !== 'string' || !SLOT_RE.test(f.slot)) return fail('bad_frame');
      if (role !== 'device') return fail('forbidden');
      return { ok: true, frame: { t: f.t, slot: f.slot } };
    case 'presence.get':
      if (role !== 'browser') return fail('forbidden');
      return { ok: true, frame: { t: 'presence.get' } };
    case 'ping':
      return { ok: true, frame: { t: 'ping' } };
    default:
      return fail('bad_frame');
  }
}

// -- rate limiting -----------------------------------------------------------------------------------------

export class TokenBucket {
  constructor(rate, burst, nowMs) {
    this.rate = rate;          // tokens per second
    this.burst = burst;
    this.tokens = burst;
    this.t = nowMs;
  }

  take(nowMs, n = 1) {
    const dt = Math.max(0, nowMs - this.t) / 1000;
    this.t = Math.max(this.t, nowMs);
    this.tokens = Math.min(this.burst, this.tokens + dt * this.rate);
    if (this.tokens >= n) {
      this.tokens -= n;
      return true;
    }
    return false;
  }
}

// -- ids ---------------------------------------------------------------------------------------------------

export function randomConn() {
  const bytes = new Uint8Array(9);
  crypto.getRandomValues(bytes);
  return 'c:' + b64urlEncode(bytes);   // 9 bytes → 12 chars
}

export function jsonResponseInit(status, extra = {}) {
  return { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...extra } };
}
