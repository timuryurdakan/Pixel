// Pixel Office v3 end-to-end crypto for the browser (V3.md §7). WebCrypto only, no JS crypto code.
//
//   ECDH P-256 + HKDF-SHA256 + AES-256-GCM, PBKDF2-SHA256 (600,000) for the pairing code, HMAC-SHA256 MACs.
//   The connector side is pixel_connector/crypto.py; tests/test_v3_browser.py cross-tests both in headless Chrome.
//
// Typical use (Phase 14 control.js):
//   const store = new IdbStore();                                  // ./idb.js
//   const me = await loadBrowserKey(store);                        // {privateKey (non-extractable), pub, bid}
//   const p = new Pairing(me, 'K7Q2-M4X5-Z6A9', 'Chrome · Linux'); // throws on a malformed code
//   relay.send(l1('pair:' + p.slot, null, 0, 'hs', await p.pair1()));
//   on pair2 → const {sas, pair3} = await p.onPair2(body) → show sas → relay.send(l1(p.deviceId, null, 1, 'hs', pair3))
//   on pair4 → const device = await p.onPair4(body); await saveDevice(store, device, room)
//   const hs = await Handshake.start(me, device);  send hs.hs1 to device.deviceId (k "hs", ch null)
//   on hs2 → const {channel, hs3} = await hs.finish(body); send hs3 with ch = channel.ch
//   const {seq, b} = await channel.seal({type: 'peer.hello', ...}); ... await channel.open(seq, b)
//
// All handshake bodies are plain objects; the caller JSON-encodes them into L1 `b` with encodeBody().

const subtle = globalThis.crypto && globalThis.crypto.subtle;
const enc = new TextEncoder();
const dec = new TextDecoder('utf-8', { fatal: true });

export const VERSION = 1;
export const PAIR_ITERATIONS = 600000;
export const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
export const PAD = 64;
const MAX_SEQ = 2 ** 32;

export class E2EError extends Error {
  constructor(code, message) { super(message || code); this.code = code; }
}

// -- bytes ------------------------------------------------------------------------------------------

export function b64u(bytes) {
  const u = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let s = '';
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function unb64u(s, length) {
  if (typeof s !== 'string' || !/^[A-Za-z0-9_-]*$/.test(s) || s.length % 4 === 1) throw new E2EError('format', 'bad base64url');
  const bin = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (s.length % 4)) % 4));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  if (b64u(out) !== s) throw new E2EError('format', 'non-canonical base64url');
  if (length !== undefined && out.length !== length) throw new E2EError('format', 'wrong length');
  return out;
}

export function concat(...parts) {
  const arrs = parts.map((p) => (typeof p === 'string' ? enc.encode(p) : p instanceof Uint8Array ? p : new Uint8Array(p)));
  const out = new Uint8Array(arrs.reduce((n, a) => n + a.length, 0));
  let o = 0;
  for (const a of arrs) { out.set(a, o); o += a.length; }
  return out;
}

export function randomBytes(n) { return crypto.getRandomValues(new Uint8Array(n)); }

function base32lower(bytes) {
  const alpha = 'abcdefghijklmnopqrstuvwxyz234567';
  let bits = 0, value = 0, out = '';
  for (const b of bytes) {
    value = (value << 8) | b; bits += 8;
    while (bits >= 5) { out += alpha[(value >>> (bits - 5)) & 31]; bits -= 5; }
  }
  if (bits > 0) out += alpha[(value << (5 - bits)) & 31];
  return out;
}

export async function sha256(data) { return new Uint8Array(await subtle.digest('SHA-256', data)); }

/** deviceId / bid = lowercase base32(SHA-256(raw public key))[:16]. */
export async function peerId(pubRaw) { return base32lower(await sha256(pubRaw)).slice(0, 16); }

export function encodeBody(obj) { return b64u(enc.encode(JSON.stringify(obj))); }
export function decodeBody(b) {
  const obj = JSON.parse(dec.decode(unb64u(b)));
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new E2EError('format', 'bad body');
  return obj;
}

// -- primitives -------------------------------------------------------------------------------------

const ECDH = { name: 'ECDH', namedCurve: 'P-256' };

export async function generateKeyPair(extractable = false) {
  const kp = await subtle.generateKey(ECDH, extractable, ['deriveBits']);
  const pub = new Uint8Array(await subtle.exportKey('raw', kp.publicKey));
  return { privateKey: kp.privateKey, pub };
}

/** Import a raw 65-byte public key. WebCrypto validates the point (rejects off-curve points). */
export async function importPublic(raw) {
  if (!(raw instanceof Uint8Array) || raw.length !== 65 || raw[0] !== 4) throw new E2EError('point', 'bad point encoding');
  try {
    return await subtle.importKey('raw', raw, ECDH, true, []);
  } catch (e) {
    throw new E2EError('point', 'invalid point');
  }
}

/** Import a known private scalar (tests / cross-checks only; production keys are generated non-extractable). */
export async function importPrivate(d, pubRaw, extractable = false) {
  const jwk = { kty: 'EC', crv: 'P-256', d: b64u(d), x: b64u(pubRaw.subarray(1, 33)), y: b64u(pubRaw.subarray(33, 65)),
    ext: extractable };
  return subtle.importKey('jwk', jwk, ECDH, extractable, ['deriveBits']);
}

export async function ecdh(privateKey, peerPubRaw) {
  const pub = await importPublic(peerPubRaw);
  return new Uint8Array(await subtle.deriveBits({ name: 'ECDH', public: pub }, privateKey, 256));
}

export async function hkdf(salt, ikm, info, length) {
  const k = await subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, k, length * 8));
}

export async function pbkdf2(password, salt, iterations, length = 32) {
  const k = await subtle.importKey('raw', password, 'PBKDF2', false, ['deriveBits']);
  return new Uint8Array(await subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations }, k, length * 8));
}

async function hmacKey(raw) { return subtle.importKey('raw', raw, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']); }
export async function hmac(rawKey, data) { return new Uint8Array(await subtle.sign('HMAC', await hmacKey(rawKey), data)); }
async function hmacVerify(rawKey, mac, data) { return subtle.verify('HMAC', await hmacKey(rawKey), mac, data); }

export async function aesKey(raw) { return subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']); }

export function ivFor(seq) {
  const iv = new Uint8Array(12);
  const dv = new DataView(iv.buffer);
  dv.setUint32(4, Math.floor(seq / 2 ** 32));
  dv.setUint32(8, seq >>> 0);
  return iv;
}

export function aadFor(ch, dir, seq) { return enc.encode(`pixel-e2e-v1|${ch}|${dir}|${seq}`); }

export function pad(bytes) {
  const out = new Uint8Array(bytes.length + ((PAD - (bytes.length % PAD)) % PAD));
  out.set(bytes);
  return out;
}

export function unpad(bytes) {
  let n = bytes.length;
  while (n > 0 && bytes[n - 1] === 0) n--;
  return bytes.subarray(0, n);
}

// -- browser key ------------------------------------------------------------------------------------

/** Load the browser's static key from `store` (idb.js), creating it on first use. */
export async function loadBrowserKey(store) {
  let rec = await store.get('e2e', 'browserKey');
  if (!rec || !rec.privateKey || !rec.pub) {
    const kp = await generateKeyPair(false);
    rec = { privateKey: kp.privateKey, pub: kp.pub, bid: await peerId(kp.pub), created: Date.now() };
    await store.put('e2e', 'browserKey', rec);
  }
  return { privateKey: rec.privateKey, pub: new Uint8Array(rec.pub), bid: rec.bid || await peerId(rec.pub) };
}

export async function saveDevice(store, device, room = null) {
  const rec = { deviceId: device.deviceId, pub: b64u(device.pub), name: device.name || null, pairedAt: Date.now(), room };
  await store.put('devices', device.deviceId, rec);
  return rec;
}

export async function listDevices(store) {
  return (await store.all('devices')).map((d) => ({ ...d, pub: unb64u(d.pub, 65) }));
}

export async function removeDevice(store, deviceId) { await store.del('devices', deviceId); }

// -- pairing (§7.3) ---------------------------------------------------------------------------------

export function normalizeCode(text) {
  const s = String(text || '').toUpperCase().replace(/[-\s]/g, '').replace(/O/g, '0').replace(/[IL]/g, '1');
  if (s.length !== 12 || [...s].some((c) => !CROCKFORD.includes(c))) throw new E2EError('code', 'bad pairing code');
  return { slot: s.slice(0, 4), secret: s.slice(4) };
}

export function formatCode(text) {
  const s = String(text || '').toUpperCase().replace(/[^0-9A-Z]/g, '').slice(0, 12);
  return s.replace(/(.{4})(?=.)/g, '$1-');
}

export async function pairingKey(slot, secret, iterations = PAIR_ITERATIONS) {
  return pbkdf2(enc.encode(secret), enc.encode('pixel-pair-v1|' + slot), iterations, 32);
}

export async function pairingTranscript(slot, deviceId, bid, qC, qB, nC, nB) {
  return sha256(concat('pixel-pair-v1', slot, deviceId, bid, qC, qB, nC, nB));
}

export async function sasOf(k, t) {
  const m = await hmac(k, concat('SAS', t));
  const v = new DataView(m.buffer).getUint32(0) % 1000000;
  const s = String(v).padStart(6, '0');
  return s.slice(0, 3) + ' ' + s.slice(3);
}

export class Pairing {
  /** me = loadBrowserKey() result; code = what the user typed; label ≤ 40 chars shown in the device terminal. */
  constructor(me, code, label = 'Tarayıcı', iterations = PAIR_ITERATIONS) {
    const { slot, secret } = normalizeCode(code);
    this.me = me;
    this.slot = slot;
    this.secret = secret;
    this.label = String(label).slice(0, 40);
    this.iterations = iterations;
    this.nB = randomBytes(16);
    this.state = 'new';
    this._k = null;
  }

  async key() {
    if (!this._k) this._k = await pairingKey(this.slot, this.secret, this.iterations);
    return this._k;
  }

  /** Body for L1 send to "pair:<slot>" (k "hs"). Starts PBKDF2 in the background. */
  async pair1() {
    this.state = 'sent1';
    this.key().catch(() => {});
    return { t: 'pair1', v: VERSION, slot: this.slot, qB: b64u(this.me.pub), nB: b64u(this.nB), label: this.label };
  }

  /** pair2 → {sas, pair3}. The pair3 body goes to the device id (k "hs"). */
  async onPair2(body) {
    if (this.state !== 'sent1' || !body || body.t !== 'pair2' || body.v !== VERSION) throw new E2EError('protocol', 'unexpected pair2');
    const qC = unb64u(body.qC, 65);
    const nC = unb64u(body.nC, 16);
    await importPublic(qC);
    if (typeof body.deviceId !== 'string' || body.deviceId !== await peerId(qC)) throw new E2EError('identity', 'device id mismatch');
    this.deviceId = body.deviceId;
    this.qC = qC;
    const k = await this.key();
    this.t = await pairingTranscript(this.slot, this.deviceId, this.me.bid, qC, this.me.pub, nC, this.nB);
    const macB = await hmac(k, concat('B', this.t));
    this.sas = await sasOf(k, this.t);
    this.state = 'sent3';
    return { sas: this.sas, pair3: { t: 'pair3', v: VERSION, macB: b64u(macB) } };
  }

  /** pair4 → {deviceId, pub, name}. Throws E2EError('refused' | 'code' | 'timeout' | 'mac'). */
  async onPair4(body) {
    if (this.state !== 'sent3' || !body || body.t !== 'pair4') throw new E2EError('protocol', 'unexpected pair4');
    this.state = 'done';
    if (body.ok !== true) throw new E2EError(['refused', 'code', 'timeout'].includes(body.reason) ? body.reason : 'refused');
    const ok = await hmacVerify(await this.key(), unb64u(body.macC, 32), concat('C', this.t));
    if (!ok) throw new E2EError('mac', 'device MAC invalid');
    const name = typeof body.name === 'string' ? body.name.slice(0, 32) : null;
    return { deviceId: this.deviceId, pub: this.qC, name };
  }
}

// -- session handshake (§7.4) ----------------------------------------------------------------------

export async function handshakeTranscript(deviceId, bid, qC, qB, eB, eC, nb, nc) {
  return sha256(concat('pixel-hs-v1', deviceId, bid, qC, qB, eB, eC, nb, nc));
}

export class Handshake {
  static async start(me, device) {
    const hs = new Handshake();
    hs.me = me;
    hs.device = device;
    const e = await generateKeyPair(false);
    hs.e = e;
    hs.nb = randomBytes(16);
    hs.hs1 = { t: 'hs1', v: VERSION, bid: me.bid, eB: b64u(e.pub), nb: b64u(hs.nb) };
    return hs;
  }

  /** hs2 → {channel, hs3}. Throws E2EError('mac' | 'protocol' | 'point'). */
  async finish(body) {
    if (!body || body.t !== 'hs2' || body.v !== VERSION) throw new E2EError('protocol', 'unexpected hs2');
    const eC = unb64u(body.eC, 65);
    const nc = unb64u(body.nc, 16);
    const t = await handshakeTranscript(this.device.deviceId, this.me.bid, this.device.pub, this.me.pub, this.e.pub, eC, this.nb, nc);
    const ee = await ecdh(this.e.privateKey, eC);
    const ss = await ecdh(this.me.privateKey, this.device.pub);
    const okm = await hkdf(t, concat(ee, ss), enc.encode('pixel-e2e-v1'), 96);
    const ch = b64u(t.subarray(0, 12));
    const kConf = okm.slice(64, 96);
    try {
      if (body.ch !== ch || !(await hmacVerify(kConf, unb64u(body.mac, 32), concat('C', t)))) throw new E2EError('mac', 'handshake MAC invalid');
      const channel = await Channel.create(ch, okm.subarray(0, 32), okm.subarray(32, 64), 'browser');
      const hs3 = { t: 'hs3', ch, mac: b64u(await hmac(kConf, concat('B', t))) };
      return { channel, hs3 };
    } finally {
      okm.fill(0);                   // raw key bytes leave memory; the channel keeps non-extractable CryptoKeys
      kConf.fill(0);
      this.e = null;
    }
  }
}

// -- envelope (§7.5) --------------------------------------------------------------------------------

export class Channel {
  static async create(ch, kB2c, kC2b, role = 'browser') {
    const c = new Channel();
    c.ch = ch;
    c.role = role;
    c.sendDir = role === 'browser' ? 'b2c' : 'c2b';
    c.recvDir = role === 'browser' ? 'c2b' : 'b2c';
    c.sendKey = await aesKey(role === 'browser' ? kB2c : kC2b);
    c.recvKey = await aesKey(role === 'browser' ? kC2b : kB2c);
    c.sendSeq = 0;
    c.recvSeq = 0;
    c.failures = 0;
    c.closed = false;
    c._sendChain = Promise.resolve();
    c._recvChain = Promise.resolve();
    return c;
  }

  /** → {seq, b}. Calls resolve in seq order; send each frame as soon as its promise resolves. */
  seal(obj) {
    if (this.closed) return Promise.reject(new E2EError('closed', 'channel closed'));
    const seq = this.sendSeq++;
    if (seq >= MAX_SEQ) return Promise.reject(new E2EError('rekey', 're-handshake required'));
    const run = async () => {
      const pt = pad(enc.encode(JSON.stringify(obj)));
      const ct = await subtle.encrypt({ name: 'AES-GCM', iv: ivFor(seq), additionalData: aadFor(this.ch, this.sendDir, seq), tagLength: 128 },
        this.sendKey, pt);
      return { seq, b: b64u(new Uint8Array(ct)) };
    };
    const p = this._sendChain.then(run);
    this._sendChain = p.catch(() => {});
    return p;
  }

  /** Decrypt the next inbound message (strict seq). 3 failures close the channel (E2EError 'closed'). */
  open(seq, b) {
    const run = async () => {
      if (this.closed) throw new E2EError('closed', 'channel closed');
      try {
        if (seq !== this.recvSeq) throw new E2EError('seq', 'unexpected seq');
        const pt = await subtle.decrypt({ name: 'AES-GCM', iv: ivFor(seq), additionalData: aadFor(this.ch, this.recvDir, seq), tagLength: 128 },
          this.recvKey, unb64u(b));
        const obj = JSON.parse(dec.decode(unpad(new Uint8Array(pt))));
        if (!obj || typeof obj !== 'object' || typeof obj.type !== 'string') throw new E2EError('format', 'bad inner message');
        this.recvSeq = seq + 1;
        return obj;
      } catch (e) {
        this.failures++;
        if (this.failures >= 3) {
          this.closed = true;
          throw new E2EError('closed', 'too many failures');
        }
        throw e instanceof E2EError ? e : new E2EError('auth', 'decryption failed');
      }
    };
    const p = this._recvChain.then(run);
    this._recvChain = p.catch(() => {});
    return p;
  }
}
