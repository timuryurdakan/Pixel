// Pixel Office v3 — DeviceHub: paired devices, relay presence, one E2E channel per device, request/response with
// timeouts, task event routing, agent.watch and pairing (V3.md §3.4, §7.3, §7.4, §11.1, §11.7). Plain ES module.
//
//   relay.js  (L1 frames, presence, reconnect)  ─┐
//   e2e.js    (pairing, handshake, Channel)      ─┼─► DeviceHub ─► events: change | event | resync | notice
//   idb.js    (browser key, paired devices)      ─┘
//
// Nothing here touches the DOM. Device-provided strings are passed on as data; the UI renders them with textContent.

import { RelayClient } from './relay.js';
import { IdbStore } from './idb.js';
import * as E from './e2e.js';
import { t as tt } from './i18n.js';

export const REQUEST_TIMEOUT_MS = 15000;     // §3.5
export const HANDSHAKE_TIMEOUT_MS = 10000;   // §7.4
export const PAIR2_TIMEOUT_MS = 20000;
export const PAIR4_TIMEOUT_MS = 90000;       // the terminal question waits 60 s

/** §11.7 + the remaining device codes. */
export const ERROR_TEXT = {
  device_offline: tt('Cihaz çevrimdışı.'),
  conn_gone: tt('Bağlantı koptu.'),
  rate_limited: tt('Çok hızlı. Biraz bekleyip tekrar deneyin.'),
  busy: tt('Ajan meşgul.'),
  budget_exceeded: tt('Bütçe sınırına ulaşıldı.'),
  workspace_denied: tt('Bu klasöre izin verilmiyor.'),
  engine_unavailable: tt('Motor cihazda bulunamadı ya da çalışmıyor.'),
  model_not_found: tt('Model cihazda yüklü değil.'),
  confirm_denied: tt('Cihazda reddedildi.'),
  confirm_timeout: tt('Cihazda onay süresi doldu.'),
  runtime_unknown: tt('Bu ortam cihazın listesinde yok.'),
  runtime_failed: tt('Çalışma ortamı komutu başarısız oldu.'),
  not_allowed: tt('Bu işlem cihaz ayarlarında kapalı.'),
  too_large: tt('Metin çok uzun (en fazla 32 KB).'),
  version: tt('Bağlayıcı sürümü uyumsuz. Bağlayıcıyı güncelleyin.'),
  handshake: tt('Cihazla güvenli bağlantı kurulamadı.'),
  timeout: tt('Cihaz yanıt vermedi.'),
  internal: tt('Cihazda beklenmeyen bir hata oluştu.'),
  unknown_task: tt('Görev bulunamadı ya da zaten bitti.'),
  unknown_agent: tt('Ajan cihazda bulunamadı.'),
  bad_request: tt('Cihaz isteği geçersiz buldu.'),
  max_turns: tt('Tur sınırına ulaşıldı.'),
  engine_exit: tt('Motor beklenmedik biçimde kapandı.'),
  relay: tt('Röleye bağlanılamadı.'),
  relay_csp: tt('Röle adresi sitenin güvenlik politikasında (CSP) yok. Yönetici vercel.json dosyasındaki röle adresini düzeltmeli (README: "Röle adresini ayarlama").'),
  not_paired: tt('Bu tarayıcı cihazla eşleşmemiş.'),
  unknown_approval: tt('Bu istek başka yerden cevaplandı ya da süresi doldu.'),
  unknown_template: tt('Şablon cihazda bulunamadı.'),
  unknown_workflow: tt('İş akışı bulunamadı ya da zaten bitti.'),
};

/** Turkish text for an error code (unknown codes → generic text, never the raw device message). */
export function errorText(code) {
  return Object.hasOwn(ERROR_TEXT, code) ? ERROR_TEXT[code] : tt('İşlem başarısız oldu.');
}

export class ControlError extends Error {
  constructor(code, message = '') {
    super(message || code);
    this.code = code;
  }
  get text() { return errorText(this.code); }
}

/** Pairing failures → Turkish (§11.5). */
export const PAIR_ERROR_TEXT = {
  bad_code: tt('Kod 12 karakter olmalı (ör. K7Q2-M4X5-Z6A9).'),
  slot: tt('Kod bulunamadı ya da süresi doldu.'),
  code: tt('Kod yanlış. Bu kod artık geçersiz; cihazda yeni kod üretin.'),
  refused: tt('Eşleştirme cihazda reddedildi.'),
  timeout: tt('Cihazda onay süresi doldu. Cihazda yeni kod üretin.'),
  mac: tt('Cihazın kimliği doğrulanamadı (araya girilmiş olabilir). Cihazda yeni kod üretin.'),
  relay: tt('Röleye bağlanılamadı. İnternet bağlantınızı kontrol edin.'),
  busy: tt('Başka bir eşleştirme sürüyor.'),
  unauthorized: tt('Bu tarayıcıdaki oda anahtarı geçersiz. Odanın katılım bağlantısını (#key=… içeren) yeniden açın.'),
  relay_not_configured: tt('Sunucuda röle ayarlı değil.'),
  relay_csp: ERROR_TEXT.relay_csp,
  relay_ticket: tt('Sunucu röle bileti vermedi. Biraz sonra tekrar deneyin.'),
};

/**
 * v2.md H2: the relay client stopped for good (detail.error of an `idle` status) → pairing error code.
 * The wrong write key and a server without relay settings get their own text; network failures keep 'relay'.
 */
export function relayErrorCode(error) {
  if (!error) return 'relay';
  if (error === 'unauthorized' || error === 'relay_not_configured') return error;
  if (error === 'csp') return 'relay_csp';
  return 'relay_ticket';
}

/** "Chrome · Linux", "Safari · iPhone" … (≤ 40 chars, shown in the device terminal). */
export function browserLabel(ua = (globalThis.navigator && navigator.userAgent) || '') {
  const b = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox'
    : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : tt('Tarayıcı');
  const os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android'
    : /Windows/.test(ua) ? 'Windows' : /Mac OS X|Macintosh/.test(ua) ? 'macOS' : /CrOS/.test(ua) ? 'ChromeOS'
      : /Linux/.test(ua) ? 'Linux' : '';
  return (os ? `${b} · ${os}` : b).slice(0, 40);
}

const REQ_PREFIX = 'r';

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

/**
 * One paired device as seen by this tab.
 * status: 'offline' (not in relay presence) | 'connecting' (handshake) | 'ready' (channel open) | 'error'
 */
class Device {
  constructor(rec) {
    this.id = rec.deviceId;
    this.pub = rec.pub;                 // Uint8Array(65)
    this.name = rec.name || null;       // from pair4 / device.info
    this.pairedAt = rec.pairedAt || 0;
    this.room = rec.room || null;
    this.online = false;
    this.status = 'offline';
    this.error = null;
    this.info = null;                   // device.info
    this.agents = new Map();            // key → Agent JSON (agent.list)
    this.channel = null;
    this.watching = new Set();
    this.connects = 0;                  // successful handshakes in this page (>1 = reconnect)
    this._hs = null;                    // pending handshake {hs, d}
  }
  get ready() { return this.status === 'ready' && !!this.channel && !this.channel.closed; }
  get label() { return this.name || (this.info && this.info.name) || `Cihaz ${this.id.slice(0, 6)}`; }
}

export class DeviceHub {
  /**
   * @param {object} o
   * @param {string} o.roomId
   * @param {string} o.writeKey
   * @param {object} [o.store]           idb.js IdbStore / MemoryStore
   * @param {Function} [o.relayFactory]  (opts) → RelayClient-like (tests)
   * @param {() => boolean} [o.isVisible]
   * @param {string} [o.label]           browser label for peer.hello / pair1
   * @param {number} [o.timeoutMs]
   */
  constructor({ roomId, writeKey, store = null, relayFactory = null, isVisible = null, label = null,
    timeoutMs = REQUEST_TIMEOUT_MS, hsTimeoutMs = HANDSHAKE_TIMEOUT_MS, pairIterations = undefined } = {}) {
    this.roomId = roomId;
    this.writeKey = writeKey;
    this.store = store || new IdbStore();
    this.relayFactory = relayFactory || ((o) => new RelayClient(o));
    this.isVisible = isVisible;
    this.label = label || browserLabel();
    this.timeoutMs = timeoutMs;
    this.hsTimeoutMs = hsTimeoutMs;
    this.pairIterations = pairIterations;
    this.me = null;
    this.relay = null;
    this.devices = new Map();
    this.pending = new Map();          // request id → {resolve, reject, timer, deviceId, quiet}
    this._rid = 0;
    this._hsSeq = 0;
    this._listeners = new Map();
    this._pairing = null;
    this._lastStatus = 'idle';
    this.relayError = null;            // why the relay client stopped: 'csp', 'unauthorized', 'relay_not_configured' …
    this._roomNeeds = false;
    this._roomAgents = [];
  }

  // -- events --------------------------------------------------------------------------------------
  /** type ∈ change | event (deviceId, msg) | resync (deviceId) | notice (text) | status (relayStatus) */
  on(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, new Set());
    this._listeners.get(type).add(fn);
    return () => this._listeners.get(type).delete(fn);
  }

  _emit(type, ...args) {
    for (const fn of this._listeners.get(type) || []) {
      try { fn(...args); } catch (e) { console.error('[pixel-office] hub listener', e); }
    }
  }

  _changed() { this._emit('change'); }

  // -- setup -----------------------------------------------------------------------------------------
  async init() {
    this.me = await E.loadBrowserKey(this.store);
    for (const rec of await E.listDevices(this.store)) this.devices.set(rec.deviceId, new Device(rec));
    this.relay = this.relayFactory({
      roomId: this.roomId, writeKey: this.writeKey, bid: this.me.bid,
      onFrame: (f) => this._onFrame(f), onStatus: (s, d) => this._onStatus(s, d),
      ...(this.isVisible ? { isVisible: this.isVisible } : {}),
    });
    this._changed();
    return this;
  }

  get bid() { return this.me ? this.me.bid : null; }
  get relayStatus() { return this.relay ? this.relay.status : 'idle'; }
  get pairedCount() { return this.devices.size; }

  /** Keep the relay open for `reason` ('room' | 'devices' | 'task' | 'pair' | …). */
  need(reason, on = true) { if (this.relay) this.relay.need(reason, on); }

  /**
   * Called with every room snapshot: connect only when needed (V3.md §4.5) — a paired device has an agent in
   * this room or was paired from this room.
   */
  updateRoom(agents = null) {
    if (agents) this._roomAgents = agents;
    let want = false;
    for (const d of this.devices.values()) if (d.room === this.roomId) want = true;
    for (const a of this._roomAgents || []) {
      if (a && a.control && this.devices.has(a.control.device)) want = true;
    }
    if (want !== this._roomNeeds) {
      this._roomNeeds = want;
      this.need('room', want);
    }
  }

  device(id) { return this.devices.get(id) || null; }
  isPaired(id) { return this.devices.has(id); }
  isReady(id) { const d = this.devices.get(id); return !!(d && d.ready); }

  /** Agent JSON from the device (agent.list) for an office agent's control field. */
  agentInfo(control) {
    if (!control) return null;
    const d = this.devices.get(control.device);
    return d ? d.agents.get(control.agent) || null : null;
  }

  // -- relay callbacks -------------------------------------------------------------------------------
  _onStatus(s, detail) {
    const was = this._lastStatus;
    this._lastStatus = s;
    if (s === 'live' || s === 'connecting') this.relayError = null;   // a new attempt: forget the old reason
    else if (detail && detail.error) this.relayError = detail.error;
    if (detail && detail.error === 'csp') this._emit('notice', ERROR_TEXT.relay_csp);
    if (s !== 'live') {
      for (const d of this.devices.values()) d.online = false;
      if (was === 'live') this._dropAll('device_offline');
    }
    this._emit('status', s, detail || null);
    this._changed();
  }

  _dropAll(code) {
    for (const d of this.devices.values()) this._dropDevice(d, code);
    if (this._pairing) this._pairing.fail('relay');
  }

  /** Close the device's channel and fail its pending requests. status: 'error' while still online, else 'offline'. */
  _dropDevice(d, code = 'device_offline', status = null) {
    if (d.channel) d.channel.closed = true;
    d.channel = null;
    if (d._hs) { d._hs.d.reject(new ControlError('handshake')); d._hs = null; }
    d.status = status || (d.online ? 'error' : 'offline');
    for (const [id, p] of this.pending) {
      if (p.deviceId === d.id) {
        clearTimeout(p.timer);
        this.pending.delete(id);
        if (p.quiet) p.resolve(null); else p.reject(new ControlError(code));
      }
    }
  }

  _onFrame(f) {
    switch (f.t) {
      case 'presence': return this._onPresence(f.devices || []);
      case 'msg': return this._onMsg(f);
      case 'err': return this._onErr(f);
      default: return undefined;
    }
  }

  _onPresence(list) {
    const ids = new Set(list.map((x) => x && x.id));
    for (const d of this.devices.values()) {
      const on = ids.has(d.id);
      if (on && !d.online) {
        d.online = true;
        if (!d.ready && !d._hs) this.connect(d.id).catch(() => {});
      } else if (!on && d.online) {
        d.online = false;
        this._dropDevice(d);
      }
    }
    this._changed();
  }

  _onErr(f) {
    if (f.code === 'slot_unknown' && this._pairing && this._pairing.stage === 'pair1') {
      this._pairing.fail('slot');
    } else if (f.code === 'rate_limited') {
      this._emit('notice', ERROR_TEXT.rate_limited);
    }
  }

  async _onMsg(f) {
    const d = this.devices.get(f.from);
    if (f.k === 'hs') {
      let body;
      try { body = E.decodeBody(f.b); } catch { return; }
      if (body.t === 'pair2' || body.t === 'pair4') {
        if (this._pairing) this._pairing.onBody(body, f.from);
      } else if (body.t === 'hs2' && d && d._hs) {
        d._hs.d.resolve(body);
      }
      return;
    }
    if (f.k !== 'e' || !d || !d.channel || f.ch !== d.channel.ch) return;
    const ch = d.channel;
    let msg;
    try {
      msg = await ch.open(f.seq, f.b);
    } catch (e) {
      if (ch.closed && d.channel === ch) {
        d.error = 'handshake';
        this._dropDevice(d, 'handshake');
        this._changed();
      }
      return;
    }
    this._dispatch(d, msg);
  }

  _dispatch(d, msg) {
    if (typeof msg.re === 'string' && this.pending.has(msg.re)) {
      const p = this.pending.get(msg.re);
      this.pending.delete(msg.re);
      clearTimeout(p.timer);
      if (msg.type === 'error') p.reject(new ControlError(typeof msg.code === 'string' ? msg.code : 'internal', msg.message));
      else p.resolve(msg);
      return;
    }
    if (msg.type === 'error') return;          // late error for a request that already timed out
    if (msg.type === 'peer.revoked' && this.me && msg.bid === this.me.bid) {
      this._revokedByDevice(d);
      return;
    }
    if (msg.type === 'device.info') this._setInfo(d, msg);
    if (msg.type === 'agent.deleted' && typeof msg.agent === 'string' && d.agents.delete(msg.agent)) this._changed();
    this._emit('event', d.id, msg);
  }

  _setInfo(d, info) {
    d.info = info;
    if (typeof info.name === 'string' && info.name && info.name !== d.name) {
      d.name = info.name.slice(0, 32);
      this.store.get('devices', d.id).then((rec) => {
        if (rec) { rec.name = d.name; return this.store.put('devices', d.id, rec); }
        return null;
      }).catch(() => {});
    }
  }

  // -- channel ---------------------------------------------------------------------------------------
  _sendL1(to, k, b, ch = null, seq = null) {
    if (!this.relay) return false;
    return this.relay.send({ t: 'send', to, ch, seq: seq === null ? this._hsSeq++ : seq, k, b });
  }

  /** Handshake + peer.hello + agent.list. Resolves with the Device (status 'ready'). */
  async connect(deviceId) {
    const d = this.devices.get(deviceId);
    if (!d) throw new ControlError('not_paired');
    if (d.ready) return d;
    if (d._connecting) return d._connecting;
    const run = async () => {
      d.status = 'connecting';
      d.error = null;
      this._changed();
      const hs = await E.Handshake.start(this.me, { deviceId: d.id, pub: d.pub });
      const wait = deferred();
      d._hs = { hs, d: wait };
      const timer = setTimeout(() => wait.reject(new ControlError('handshake')), this.hsTimeoutMs);
      if (!this._sendL1(d.id, 'hs', E.encodeBody(hs.hs1))) {
        clearTimeout(timer);
        d._hs = null;
        throw new ControlError('device_offline');
      }
      let body;
      try { body = await wait.promise; } finally { clearTimeout(timer); d._hs = null; }
      const { channel, hs3 } = await hs.finish(body);
      this._sendL1(d.id, 'hs', E.encodeBody(hs3), channel.ch);
      d.channel = channel;
      d.status = 'ready';
      const info = await this.request(d.id, { type: 'peer.hello', label: this.label, app: 'web/3.0.0' });
      this._setInfo(d, info);
      await this.refreshAgents(d.id).catch(() => {});
      for (const key of d.watching) this._send(d, { type: 'agent.watch', agent: key, on: true });
      d.connects += 1;
      this._changed();
      if (d.connects > 1) this._emit('resync', d.id);
      return d;
    };
    d._connecting = run().catch((e) => {
      if (d.channel) d.channel.closed = true;
      d.channel = null;
      d.status = d.online ? 'error' : 'offline';
      d.error = e instanceof ControlError ? e.code : 'handshake';
      if (d.error !== 'device_offline') d.error = 'handshake';
      this._changed();
      throw e instanceof ControlError ? e : new ControlError('handshake');
    }).finally(() => { d._connecting = null; });
    return d._connecting;
  }

  /** Seal + send one inner message (in seq order). */
  _send(d, obj) {
    const ch = d.channel;
    if (!ch || ch.closed) return Promise.reject(new ControlError('device_offline'));
    return ch.seal(obj).then(({ seq, b }) => {
      if (!this._sendL1(d.id, 'e', b, ch.ch, seq)) throw new ControlError('device_offline');
    });
  }

  /**
   * Request/response (§3.4). Rejects with ControlError(code) on an `error` reply, timeout or a lost channel.
   * quiet: some requests (task.cancel, agent.watch) have no success reply → resolves null after `timeout`.
   */
  request(deviceId, obj, { timeout = this.timeoutMs, quiet = false } = {}) {
    const d = this.devices.get(deviceId);
    if (!d) return Promise.reject(new ControlError('not_paired'));
    if (!d.channel || d.channel.closed) return Promise.reject(new ControlError(d.online ? 'handshake' : 'device_offline'));
    const id = REQ_PREFIX + (++this._rid).toString(36);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        if (quiet) resolve(null); else reject(new ControlError('timeout'));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer, deviceId, quiet });
      this._send(d, { ...obj, id }).catch((e) => {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(e instanceof ControlError ? e : new ControlError('device_offline'));
      });
    });
  }

  async refreshAgents(deviceId) {
    const r = await this.request(deviceId, { type: 'agent.list' });
    const d = this.devices.get(deviceId);
    if (d && r && Array.isArray(r.agents)) {
      d.agents = new Map(r.agents.filter((a) => a && typeof a.key === 'string').map((a) => [a.key, a]));
      this._changed();
    }
    return r;
  }

  /** Remember an agent update/creation from a reply without a full agent.list. */
  putAgent(deviceId, agent) {
    const d = this.devices.get(deviceId);
    if (d && agent && typeof agent.key === 'string') { d.agents.set(agent.key, agent); this._changed(); }
  }

  /** agent.delete (E11): the device cancels the agent's tasks, forgets it and tells the other paired tabs. */
  async deleteAgent(deviceId, key) {
    const r = await this.request(deviceId, { type: 'agent.delete', agent: key });
    const d = this.devices.get(deviceId);
    if (d) {
      d.watching.delete(key);
      if (d.agents.delete(key)) this._changed();
    }
    return r;
  }

  /** agent.watch {on} (§11.2): remembered, so it is re-sent after a reconnect. */
  watch(deviceId, key, on) {
    const d = this.devices.get(deviceId);
    if (!d) return;
    if (on) d.watching.add(key); else d.watching.delete(key);
    if (d.ready) this._send(d, { type: 'agent.watch', agent: key, on: !!on }).catch(() => {});
  }

  // -- pairing (§7.3) --------------------------------------------------------------------------------
  /**
   * Pair this browser with a device using the code shown by `pixel-connector pair`.
   * onSas(sas) is called with "482 913" once the code is verified; resolves with the Device.
   * Rejects with {code} ∈ PAIR_ERROR_TEXT keys.
   */
  async pair(codeText, { onSas = null, onStage = null } = {}) {
    if (this._pairing) throw new ControlError('busy');
    let p;
    try {
      p = new E.Pairing(this.me, codeText, this.label, this.pairIterations);
    } catch {
      throw new ControlError('bad_code');
    }
    const st = { stage: 'relay', d: null, timer: null };
    const step = (stage, ms) => {
      st.stage = stage;
      st.d = deferred();
      clearTimeout(st.timer);
      st.timer = setTimeout(() => st.d.reject(new ControlError(stage === 'pair1' ? 'slot' : 'timeout')), ms);
      return st.d.promise;
    };
    this._pairing = {
      get stage() { return st.stage; },
      fail: (code) => { if (st.d) st.d.reject(new ControlError(code)); },
      onBody: (body, from) => { if (st.d && ((st.stage === 'pair1' && body.t === 'pair2') || (st.stage === 'pair3' && body.t === 'pair4'))) st.d.resolve({ body, from }); },
    };
    this.need('pair', true);
    try {
      if (onStage) onStage('relay');
      await this._waitLive(15000);
      const pair1 = await p.pair1();
      const w2 = step('pair1', PAIR2_TIMEOUT_MS);
      if (!this._sendL1('pair:' + p.slot, 'hs', E.encodeBody(pair1))) throw new ControlError('relay');
      const { body: b2 } = await w2;
      if (onStage) onStage('verify');
      let res;
      try { res = await p.onPair2(b2); } catch { throw new ControlError('mac'); }
      if (onSas) onSas(res.sas);
      const w4 = step('pair3', PAIR4_TIMEOUT_MS);
      this._sendL1(p.deviceId, 'hs', E.encodeBody(res.pair3));
      const { body: b4 } = await w4;
      let dev;
      try { dev = await p.onPair4(b4); } catch (e) { throw new ControlError(e && e.code && PAIR_ERROR_TEXT[e.code] ? e.code : 'mac'); }
      const rec = await E.saveDevice(this.store, dev, this.roomId);
      const d = new Device({ ...rec, pub: dev.pub });
      const old = this.devices.get(d.id);
      if (old) this._dropDevice(old);
      this.devices.set(d.id, d);
      d.online = !!(this.relay && this.relay.presence.has(d.id));
      this.updateRoom();
      this._changed();
      if (d.online) await this.connect(d.id).catch(() => {});
      return d;
    } finally {
      clearTimeout(st.timer);
      this._pairing = null;
      this.need('pair', false);
    }
  }

  /**
   * Resolves once the relay is live. Rejects at once when the relay client gives up (v2.md H2: ticket refused with
   * 401/503, CSP block → its own code) or reports `quota`; otherwise after `ms` with 'relay' (network).
   */
  _waitLive(ms) {
    if (this.relayStatus === 'live') return Promise.resolve();
    return new Promise((resolve, reject) => {
      const done = (err) => { off(); clearTimeout(t); if (err) reject(err); else resolve(); };
      const off = this.on('status', (s, detail) => {
        if (s === 'live') done(null);
        else if (s === 'quota') done(new ControlError('relay'));
        else if (s === 'idle' && detail && detail.error) done(new ControlError(relayErrorCode(detail.error)));
      });
      const t = setTimeout(() => done(new ControlError('relay')), ms);
    });
  }

  // -- unpair / revoke (§6.8) ------------------------------------------------------------------------
  /** Remove this browser's pairing: asks the device (if reachable) and forgets the device locally. */
  async unpair(deviceId) {
    const d = this.devices.get(deviceId);
    if (!d) return { remote: false };
    let remote = false;
    if (d.ready) {
      try {
        await this.request(deviceId, { type: 'peer.revoke', bid: this.me.bid });
        remote = true;
      } catch { /* device unreachable: forget locally anyway */ }
    }
    this._dropDevice(d);
    this.devices.delete(deviceId);
    await E.removeDevice(this.store, deviceId).catch(() => {});
    this.updateRoom();
    this._changed();
    return { remote };
  }

  /**
   * S29-02: the device removed this browser's pairing (on the computer or from another browser) and said so before
   * closing the channel. Forget it locally, as unpair() does, instead of showing its last state until a reconnect
   * fails. Only the paired device itself can send this (E2E channel).
   */
  _revokedByDevice(d) {
    if (this.devices.get(d.id) !== d) return;
    this._dropDevice(d, 'not_paired');
    this.devices.delete(d.id);
    E.removeDevice(this.store, d.id).catch((e) => console.error('[pixel-office] removeDevice', e));
    this.updateRoom();
    this._changed();
    this._emit('notice', tt('Bu tarayıcının «{name}» ile eşleşmesi kaldırıldı (bilgisayarda ya da başka bir tarayıcıdan).', { name: d.name || d.id }));
  }

  destroy() {
    for (const d of this.devices.values()) this._dropDevice(d);
    if (this.relay && this.relay.destroy) this.relay.destroy();
  }
}
