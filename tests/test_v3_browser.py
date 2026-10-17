"""Phase 13: web/js/e2e.js (WebCrypto) ⇄ pixel_connector/crypto.py (pure Python) in headless Chrome (V3.md §7.6).

1. Python → browser: RFC/NIST/Wycheproof material and Python-made key pairs, transcripts, MACs, SAS and sealed
   messages are reproduced/opened by e2e.js (ECDH, HKDF, PBKDF2, HMAC, AES-GCM, envelope, pairing/handshake maths).
2. browser → Python: the page generates keys and seals messages; Python derives the same secrets and opens them.
3. A full pairing + handshake + encrypted request between the real connector and the real page through dev_relay and
   the dev server (ticket, Origin, IndexedDB with a non-extractable key): correct code, wrong code (slot burnt),
   SAS refusal.
4. Negative cases: tampered tag, wrong AAD, replayed / out-of-order seq, invalid points from the "relay", wrong
   device id in pair2.

Skipped when google-chrome / chromium is not installed. Chrome runs with a temp profile; nothing touches ~/.claude
or ~/.pixel-office.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E

import dev_server
from pixel_connector import crypto as C

ROOT = Path(__file__).resolve().parent.parent
VEC = ROOT / "tests" / "vectors"
CHROME = next((shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
               if shutil.which(n)), None)

PAGE = """<!doctype html><meta charset="utf-8"><title>v3 e2e cross</title>
<script type="module">
import * as E from '/web/js/e2e.js';
import { IdbStore } from '/web/js/idb.js';
const CFG = %s;
const out = [];
const hex = (u) => [...u].map((b) => b.toString(16).padStart(2, '0')).join('');
const unhex = (h) => new Uint8Array((h.match(/../g) || []).map((x) => parseInt(x, 16)));
const enc = new TextEncoder();
async function t(name, fn) {
  try { const r = await fn(); out.push({ name, ok: true, value: r === undefined ? null : r }); }
  catch (e) { out.push({ name, ok: false, error: String(e && (e.code || '') + ' ' + (e.stack || e)) }); }
}
function eq(a, b, what) { if (a !== b) throw new Error((what || 'mismatch') + ': ' + a + ' != ' + b); }
async function rejects(p, code) {
  try { await p; } catch (e) { if (code && e.code !== code) throw new Error('wrong error ' + e.code + ' ' + e); return; }
  throw new Error('expected rejection');
}
const post = (path, obj) => fetch(path, { method: 'POST', body: JSON.stringify(obj) }).then((r) => r.json());

// ---- 1. Python → browser ----------------------------------------------------------------------------------
const V = CFG.vectors;
await t('ecdh rfc5903 + cavp + python pairs', async () => {
  for (const v of V.ecdh) {
    const k = await E.importPrivate(unhex(v.d), unhex(v.pub));
    eq(hex(await E.ecdh(k, unhex(v.peer))), v.z, 'ecdh');
  }
  return V.ecdh.length;
});
await t('wycheproof invalid points rejected', async () => {
  const k = await E.importPrivate(unhex(V.ecdh[0].d), unhex(V.ecdh[0].pub));
  let n = 0;
  for (const p of V.invalidPoints) { await rejects(E.ecdh(k, unhex(p))); n++; }
  return n;
});
await t('hkdf rfc5869', async () => { for (const v of V.hkdf) eq(hex(await E.hkdf(unhex(v.salt), unhex(v.ikm), unhex(v.info), v.L)), v.okm); });
await t('pbkdf2 rfc7914', async () => { for (const v of V.pbkdf2) eq(hex(await E.pbkdf2(enc.encode(v.password), enc.encode(v.salt), v.c, v.dkLen)), v.dk); });
await t('hmac rfc4231', async () => { for (const v of V.hmac) eq(hex(await E.hmac(unhex(v.key), unhex(v.data))), v.mac); });
await t('aes-gcm nist + wycheproof', async () => {
  let n = 0;
  for (const v of V.gcm) {
    const k = await crypto.subtle.importKey('raw', unhex(v.key), 'AES-GCM', false, ['encrypt', 'decrypt']);
    const p = crypto.subtle.decrypt({ name: 'AES-GCM', iv: unhex(v.iv), additionalData: unhex(v.aad), tagLength: 128 }, k, unhex(v.ct + v.tag));
    if (v.valid) eq(hex(new Uint8Array(await p)), v.pt); else await rejects(p);
    n++;
  }
  return n;
});
await t('peer id', async () => eq(await E.peerId(unhex(V.ids.pub)), V.ids.id));
await t('pairing maths', async () => {
  const P = V.pairing;
  eq(hex(await E.pairingKey(P.slot, P.secret, P.iterations)), P.k, 'K');
  const T = await E.pairingTranscript(P.slot, P.deviceId, P.bid, unhex(P.qC), unhex(P.qB), unhex(P.nC), unhex(P.nB));
  eq(hex(T), P.t, 'T');
  eq(hex(await E.hmac(unhex(P.k), E.concat('B', T))), P.macB, 'macB');
  eq(await E.sasOf(unhex(P.k), T), P.sas, 'SAS');
  const n = E.normalizeCode('k7q2-m4x5 z6a9'); eq(n.slot + n.secret, 'K7Q2M4X5Z6A9');
  const n2 = E.normalizeCode('OIL0-0000-0000'); eq(n2.slot, '0110');
  for (const bad of ['K7Q2-M4X5', 'K7Q2-M4X5-Z6AU', '']) { let ok = false; try { E.normalizeCode(bad); } catch (e) { ok = e.code === 'code'; } if (!ok) throw new Error('accepted ' + bad); }
});
await t('handshake maths', async () => {
  const H = V.handshake;
  const T = await E.handshakeTranscript(H.deviceId, H.bid, unhex(H.qC), unhex(H.qB), unhex(H.eB), unhex(H.eC), unhex(H.nb), unhex(H.nc));
  eq(hex(T), H.t, 'T');
  const ee = await E.ecdh(await E.importPrivate(unhex(H.eBpriv), unhex(H.eB)), unhex(H.eC));
  const ss = await E.ecdh(await E.importPrivate(unhex(H.qBpriv), unhex(H.qB)), unhex(H.qC));
  const okm = await E.hkdf(T, E.concat(ee, ss), enc.encode('pixel-e2e-v1'), 96);
  eq(hex(okm), H.okm, 'okm');
  eq(E.b64u(T.subarray(0, 12)), H.ch, 'ch');
});
await t('open python-sealed messages + negatives', async () => {
  const S = V.sealed;
  const ch = await E.Channel.create(S.ch, unhex(S.kb2c), unhex(S.kc2b), 'browser');
  await rejects(ch.open(1, S.msgs[1].b), 'seq');                         // out of order
  eq(JSON.stringify(await ch.open(0, S.msgs[0].b)), JSON.stringify(S.msgs[0].obj));
  await rejects(ch.open(0, S.msgs[0].b), 'seq');                         // replay
  eq(JSON.stringify(await ch.open(1, S.msgs[1].b)), JSON.stringify(S.msgs[1].obj));
  const raw = E.unb64u(S.msgs[2].b); raw[raw.length - 1] ^= 1;
  await rejects(ch.open(2, E.b64u(raw)), 'closed');                       // tampered tag = 3rd failure → closed
  await rejects(ch.open(2, S.msgs[2].b), 'closed');
  const ch2 = await E.Channel.create('ZZZZZZZZZZZZZZZZ', unhex(S.kb2c), unhex(S.kc2b), 'browser');
  await rejects(ch2.open(0, S.msgs[0].b), 'auth');                       // wrong AAD (channel id)
});
await t('pair2 from a lying relay', async () => {
  const me = await E.generateKeyPair(true); me.bid = await E.peerId(me.pub);
  const p = new E.Pairing(me, 'K7Q2-M4X5-Z6A9', 'x', 1000); await p.pair1();
  await rejects(p.onPair2({ t: 'pair2', v: 1, deviceId: 'aaaaaaaaaaaaaaaa', qC: E.b64u(unhex(V.invalidPoints[0])), nC: E.b64u(new Uint8Array(16)) }), 'point');
  const p2 = new E.Pairing(me, 'K7Q2-M4X5-Z6A9', 'x', 1000); await p2.pair1();
  await rejects(p2.onPair2({ t: 'pair2', v: 1, deviceId: 'aaaaaaaaaaaaaaaa', qC: E.b64u(unhex(V.ids.pub)), nC: E.b64u(new Uint8Array(16)) }), 'identity');
});

// ---- 2. browser → Python ------------------------------------------------------------------------------------
await t('browser-made material', async () => {
  const kp = await E.generateKeyPair(true);
  const jwk = await crypto.subtle.exportKey('jwk', kp.privateKey);
  const z = await E.ecdh(kp.privateKey, unhex(V.ids.pub));
  const kb = crypto.getRandomValues(new Uint8Array(32)), kc = crypto.getRandomValues(new Uint8Array(32));
  const ch = await E.Channel.create('AbCdEfGhIjKlMnOp', kb, kc, 'browser');
  const msgs = [];
  for (const obj of [{ type: 'peer.hello', label: 'Çay ☕' }, { type: 'task.start', prompt: 'x'.repeat(3000) }]) msgs.push(await ch.seal(obj));
  return { pub: hex(kp.pub), d: hex(E.unb64u(jwk.d)), z: hex(z), kb: hex(kb), kc: hex(kc), msgs,
           pbkdf: hex(await E.pairingKey('ABCD', '12345678', 1000)), id: await E.peerId(kp.pub) };
});

// ---- 3. full pairing + handshake with the real connector through dev_relay ------------------------------------
if (CFG.live) {
  const store = new IdbStore('pixel-office-test');
  const me = await E.loadBrowserKey(store);
  await t('browser key is non-extractable and persisted', async () => {
    await rejects(crypto.subtle.exportKey('jwk', me.privateKey));
    const again = await E.loadBrowserKey(store);
    eq(again.bid, me.bid);
    return me.bid;
  });
  const tk = await fetch('/api/relay_ticket?room=' + CFG.room, { method: 'POST', headers: { Authorization: 'Bearer ' + CFG.key, 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: 'browser', id: me.bid }) }).then((r) => r.json());
  const ws = new WebSocket(tk.url, ['pixel-relay.v1', 'tk.' + tk.ticket]);
  const queue = []; let waiter = null;
  ws.onmessage = (ev) => { queue.push(JSON.parse(ev.data)); if (waiter) { const w = waiter; waiter = null; w(); } };
  async function next(pred, ms = 15000) {
    const end = Date.now() + ms;
    for (;;) {
      const i = queue.findIndex(pred);
      if (i >= 0) return queue.splice(i, 1)[0];
      if (Date.now() > end) throw new Error('timeout');
      await new Promise((res) => { waiter = res; setTimeout(res, 200); });
    }
  }
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const welcome = await next((f) => f.t === 'welcome');
  let seq = 0;
  const send = (to, k, body, ch = null, s = null) => ws.send(JSON.stringify({ t: 'send', to, ch, seq: s === null ? seq++ : s, k, b: k === 'hs' ? E.encodeBody(body) : body }));
  const hsFrom = (t) => (f) => f.t === 'msg' && f.k === 'hs' && E.decodeBody(f.b).t === t;
  async function pairScenario(name) {
    const { code } = await post('/__pair', { scenario: name });
    const p = new E.Pairing(me, code, 'Chrome · Headless');
    send('pair:' + p.slot, 'hs', await p.pair1());
    const p2 = E.decodeBody((await next(hsFrom('pair2'))).b);
    const { sas, pair3 } = await p.onPair2(p2);
    await post('/__sas', { scenario: name, sas });
    send(p.deviceId, 'hs', pair3);
    const p4 = E.decodeBody((await next(hsFrom('pair4'))).b);
    return await p.onPair4(p4);
  }
  await t('pair: wrong code burns the slot', async () => { await rejects(pairScenario('wrong'), 'code'); });
  await t('pair: SAS refused on the device', async () => { await rejects(pairScenario('refuse'), 'refused'); });
  let device = null;
  await t('pair: correct code', async () => { device = await pairScenario('ok'); await E.saveDevice(store, device, CFG.room); return device.deviceId; });
  await t('handshake + encrypted requests', async () => {
    const saved = (await E.listDevices(store)).find((d) => d.deviceId === device.deviceId);
    const hs = await E.Handshake.start(me, saved);
    send(saved.deviceId, 'hs', hs.hs1);
    const h2 = await next(hsFrom('hs2'));
    const { channel, hs3 } = await hs.finish(E.decodeBody(h2.b));
    send(saved.deviceId, 'hs', hs3, channel.ch);
    const req = async (obj) => {
      const { seq: s, b } = await channel.seal(obj);
      ws.send(JSON.stringify({ t: 'send', to: saved.deviceId, ch: channel.ch, seq: s, k: 'e', b }));
      const f = await next((f) => f.t === 'msg' && f.k === 'e' && f.ch === channel.ch);
      return channel.open(f.seq, f.b);
    };
    const info = await req({ type: 'peer.hello', id: 'q1', label: 'Chrome · Headless', app: 'web/3.0.0' });
    eq(info.type, 'device.info'); eq(info.re, 'q1');
    const list = await req({ type: 'peer.list', id: 'q2' });
    eq(list.peers.length, 1); eq(list.peers[0].self, true);
    return { device: info.name, conn: welcome.conn };
  });
  ws.close();
}
await post('/__result', { results: out });
</script>"""


def vectors() -> dict:
    p256 = json.loads((VEC / "p256_ecdh.json").read_text())
    ecdh = []
    r = p256["rfc5903"]
    ecdh.append({"d": r["i"], "pub": "04" + r["gix"] + r["giy"], "peer": "04" + r["grx"] + r["gry"], "z": r["girx"]})
    for v in p256["cavp"][:10]:
        ecdh.append({"d": v["dIUT"], "pub": "04" + v["qIUTx"] + v["qIUTy"], "peer": "04" + v["qCAVSx"] + v["qCAVSy"],
                     "z": v["z"]})
    for _ in range(3):
        d1, q1 = C.generate_keypair()
        d2, q2 = C.generate_keypair()
        ecdh.append({"d": "%064x" % d1, "pub": q1.hex(), "peer": q2.hex(), "z": C.ecdh(d1, q2).hex()})
    invalid = [v["public"] for v in p256["wycheproof"] if v["result"] == "invalid" and len(v["public"]) == 130
               and v["public"].startswith("04")]
    gcm = []
    aes = json.loads((VEC / "aes_gcm.json").read_text())
    for v in aes["nist"][::5]:
        gcm.append({k: v[k] for k in ("key", "iv", "aad", "pt", "ct", "tag", "valid")})
    for v in aes["wycheproof"]:
        gcm.append({"key": v["key"], "iv": v["iv"], "aad": v["aad"], "pt": v["msg"], "ct": v["ct"], "tag": v["tag"],
                    "valid": v["result"] == "valid"})
    # pairing + handshake material
    dC, qC = C.generate_keypair()
    dB, qB = C.generate_keypair()
    dev, bid = C.peer_id(qC), C.peer_id(qB)
    nC, nB = os.urandom(16), os.urandom(16)
    k = C.pairing_key("K7Q2", "M4X5Z6A9", 1000)
    t = C.pairing_transcript("K7Q2", dev, bid, qC, qB, nC, nB)
    deB, eB = C.generate_keypair()
    deC, eC = C.generate_keypair()
    nb, nc = os.urandom(16), os.urandom(16)
    th = C.handshake_transcript(dev, bid, qC, qB, eB, eC, nb, nc)
    okm = C.hkdf(th, C.ecdh(deC, eB) + C.ecdh(dC, qB), b"pixel-e2e-v1", 96)
    kb, kc = os.urandom(32), os.urandom(32)
    dch = C.Channel("AbCdEfGhIjKlMnOp", kb, kc, "device")
    objs = [{"type": "device.info", "name": "Masaüstü ✓"}, {"type": "task.chunk", "parts": [{"k": "text", "v": "ü" * 5000}]},
            {"type": "task.end", "status": "success"}]
    sealed = [{"obj": o, "b": dch.seal(o)[1]} for o in objs]
    return {"ecdh": ecdh, "invalidPoints": invalid, "gcm": gcm,
            "hkdf": json.loads((VEC / "hkdf.json").read_text())["vectors"],
            "pbkdf2": json.loads((VEC / "pbkdf2.json").read_text())["vectors"],
            "hmac": json.loads((VEC / "hmac.json").read_text())["vectors"],
            "ids": {"pub": qC.hex(), "id": dev},
            "pairing": {"slot": "K7Q2", "secret": "M4X5Z6A9", "iterations": 1000, "k": k.hex(), "deviceId": dev,
                        "bid": bid, "qC": qC.hex(), "qB": qB.hex(), "nC": nC.hex(), "nB": nB.hex(), "t": t.hex(),
                        "macB": C.pairing_mac(k, "B", t).hex(), "sas": C.sas(k, t)},
            "handshake": {"deviceId": dev, "bid": bid, "qC": qC.hex(), "qB": qB.hex(), "qBpriv": "%064x" % dB,
                          "eB": eB.hex(), "eBpriv": "%064x" % deB, "eC": eC.hex(), "nb": nb.hex(), "nc": nc.hex(),
                          "t": th.hex(), "okm": okm.hex(), "ch": C.b64u(th[:12])},
            "sealed": {"ch": "AbCdEfGhIjKlMnOp", "kb2c": kb.hex(), "kc2b": kc.hex(), "msgs": sealed},
            "_secret": {"qCpriv": dC}}


class _Handler(dev_server.DevHandler):
    def handle_any(self):
        path = self.path.split("?")[0]
        srv = self.server
        if path == "/__v3test.html":
            body = (PAGE % json.dumps(srv.page_cfg)).encode()
            return self._raw(200, "text/html; charset=utf-8", body)
        if path.startswith("/web/js/") and path.endswith(".js") and ".." not in path:
            return self._raw(200, "text/javascript; charset=utf-8", (ROOT / path.lstrip("/")).read_bytes())
        if self.command == "POST" and path.startswith("/__"):
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            res = srv.on_post(path, data)
            return self._raw(200, "application/json", json.dumps(res).encode())
        return super().handle_any()

    do_GET = do_POST = handle_any

    def _raw(self, status, ctype, body):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class CrossServer(dev_server.DevServer):
    def __init__(self, app, page_cfg, on_post):
        super().__init__(("127.0.0.1", 0), app, dev_server.WEB_ROOT, dev_server.SECURITY_HEADERS)
        self.RequestHandlerClass = _Handler
        self.page_cfg = page_cfg
        self.on_post = on_post


@unittest.skipUnless(CHROME, "headless Chrome not installed")
class WebCryptoCrossTests(unittest.TestCase):
    def setUp(self):
        self.env = E.V3Env()
        self.conn = self.env.connector(self.env.connector_home())
        self.vec = vectors()
        self.secret = self.vec.pop("_secret")
        self.result = None
        self.done = threading.Event()
        self.sas = {}
        self.pair_threads = []
        cfg = {"vectors": self.vec, "live": True, "room": self.env.room, "key": self.env.key}
        self.srv = CrossServer(self.env.app, cfg, self.on_post)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.env.relay.allowed = [self.env.base, self.base]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.conn.stop()
        self.env.stop()

    # -- page callbacks --------------------------------------------------------------------------------
    def on_post(self, path, data):
        if path == "/__result":
            self.result = data
            self.done.set()
            return {}
        if path == "/__pair":
            scen = data["scenario"]
            p = self.conn.router.start_pairing()
            p.wait({"open"}, 5)
            code = p.code if scen != "wrong" else p.code[:4] + ("00000000" if p.code[4:] != "00000000" else "11111111")

            def confirm():
                st = p.wait({"confirm", "failed"}, 30)
                if st == "confirm":
                    for _ in range(100):
                        if scen in self.sas:
                            break
                        time.sleep(0.05)
                    self.sas[scen + ":device"] = p.sas
                    p.confirm(scen == "ok" and self.sas.get(scen) == p.sas)
                else:
                    self.sas[scen + ":device"] = (st, p.reason)
            th = threading.Thread(target=confirm, daemon=True)
            th.start()
            self.pair_threads.append(th)
            return {"code": C.format_code(code)}
        if path == "/__sas":
            self.sas[data["scenario"]] = data["sas"]
            return {}
        return {}

    def run_chrome(self, timeout=120):
        profile = tempfile.mkdtemp(prefix="pixel-v3-chrome-")
        proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                 "--disable-extensions", "--user-data-dir=" + profile, "--remote-debugging-port=0",
                                 self.base + "/__v3test.html"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(self.done.wait(timeout), "headless Chrome did not report results")
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
            shutil.rmtree(profile, ignore_errors=True)
        return {r["name"]: r for r in self.result["results"]}

    def test_cross_implementation(self):
        res = self.run_chrome()
        failed = ["%s: %s" % (n, r.get("error")) for n, r in res.items() if not r["ok"]]
        self.assertFalse(failed, "\n".join(failed))
        expected = {"ecdh rfc5903 + cavp + python pairs", "wycheproof invalid points rejected", "hkdf rfc5869",
                    "pbkdf2 rfc7914", "hmac rfc4231", "aes-gcm nist + wycheproof", "peer id", "pairing maths",
                    "handshake maths", "open python-sealed messages + negatives", "pair2 from a lying relay",
                    "browser-made material", "browser key is non-extractable and persisted",
                    "pair: wrong code burns the slot", "pair: SAS refused on the device", "pair: correct code",
                    "handshake + encrypted requests"}
        self.assertEqual(set(res), expected)
        self.assertGreaterEqual(res["wycheproof invalid points rejected"]["value"], 15)
        self.assertGreater(res["aes-gcm nist + wycheproof"]["value"], 80)
        # 2. browser → Python
        b = res["browser-made material"]["value"]
        d = int(b["d"], 16)
        pub = bytes.fromhex(b["pub"])
        self.assertEqual(C.public_key(d), pub)
        self.assertEqual(C.peer_id(pub), b["id"])
        self.assertEqual(C.ecdh(self.secret["qCpriv"], pub).hex(), b["z"])
        self.assertEqual(C.ecdh(d, bytes.fromhex(self.vec["ids"]["pub"])).hex(), b["z"])
        self.assertEqual(C.pairing_key("ABCD", "12345678", 1000).hex(), b["pbkdf"])
        dev = C.Channel("AbCdEfGhIjKlMnOp", bytes.fromhex(b["kb"]), bytes.fromhex(b["kc"]), "device")
        self.assertEqual(dev.open(b["msgs"][0]["seq"], b["msgs"][0]["b"]), {"type": "peer.hello", "label": "Çay ☕"})
        self.assertEqual(dev.open(b["msgs"][1]["seq"], b["msgs"][1]["b"])["prompt"], "x" * 3000)
        # 3. the live scenarios, seen from the device
        for th in self.pair_threads:
            th.join(5)
        self.assertEqual(self.sas["wrong:device"], ("failed", "code"))
        self.assertEqual(self.sas["refuse:device"], self.sas["refuse"])
        self.assertEqual(self.sas["ok:device"], self.sas["ok"])
        bid = res["browser key is non-extractable and persisted"]["value"]
        self.assertIsNotNone(self.conn.peers.active(bid))
        self.assertEqual(res["handshake + encrypted requests"]["value"]["device"], "Test Cihazı")
        evs = [r["ev"] for r in self.conn.audit.read()]
        self.assertEqual(evs.count("pair.fail"), 2)
        self.assertIn("pair.ok", evs)
        self.assertIn("hs.ok", evs)


if __name__ == "__main__":
    unittest.main()
