"""Phase 13 test environment: dev_relay + the Vercel-style dev server (MemoryStorage) on free ports, a fresh room,
connector homes in temp dirs, and `FakeBrowser`: the browser side of pairing/handshake/E2E written with the
connector's own crypto (the WebCrypto side is cross-tested separately in test_v3_browser.py)."""

import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import dev_relay  # noqa: E402
import dev_server  # noqa: E402
from pixel_cloud.app import App  # noqa: E402
from pixel_cloud.storage import MemoryStorage  # noqa: E402
from pixel_connector import config as CFG  # noqa: E402
from pixel_connector import crypto as C  # noqa: E402
from pixel_connector import ws as W  # noqa: E402
from pixel_connector.session import decode_body, encode_body  # noqa: E402

RELAY_SECRET = "x" * 43


class V3Env:
    def __init__(self, relay_secret: str = RELAY_SECRET) -> None:
        self.tmp = tempfile.mkdtemp(prefix="pixel-v3-")
        self.relay = dev_relay.Relay(relay_secret, "")
        self.relay_srv, self.relay_url = dev_relay.serve_in_thread(self.relay, "127.0.0.1", 0)
        self.relay_frames = []                 # every L1 text frame the relay received (privacy canary)
        self._wrap_relay()
        self.app = App(storage=MemoryStorage(), env={"PIXEL_SECRET": "d" * 40, "RELAY_SECRET": relay_secret,
                                                     "PIXEL_RELAY_URL": self.relay_url}, mapper=None)
        self.status_bodies = []
        self.notified = []
        self._wrap_app()
        self.web = dev_server.DevServer(("127.0.0.1", 0), self.app, dev_server.WEB_ROOT,
                                        dev_server.security_headers(self.relay_url))
        self.port = self.web.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port
        self.origin = self.base
        self.relay.allowed = [self.base]
        threading.Thread(target=self.web.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.room, self.key = self.create_room()

    def _wrap_relay(self) -> None:
        orig = self.relay.on_message
        frames = self.relay_frames

        def on_message(room, peer, data):
            frames.append(data if isinstance(data, str) else repr(data))
            return orig(room, peer, data)
        self.relay.on_message = on_message

    def _wrap_app(self) -> None:
        orig = self.app.dispatch
        bodies = self.status_bodies

        def dispatch(req):
            if req.path.startswith("/api/status") and req.method == "POST":
                if req.body is None and req.rfile is not None:
                    n = int(req.headers.get("content-length") or 0)
                    req.body = req.rfile.read(n) if 0 < n <= 65536 else b""
                    req.rfile = None
                    req.body_consumed = True
                bodies.append(req.body or b"")
            return orig(req)
        self.app.dispatch = dispatch

    def http(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def create_room(self):
        st, d = self.http("POST", "/api/rooms", {"name": "V3 Test"})
        assert st == 201, (st, d)
        return d["roomId"], d["writeKey"]

    def state(self):
        st, d = self.http("GET", "/api/state?room=" + self.room)
        assert st == 200, d
        return d

    def browser_ticket(self, bid):
        st, d = self.http("POST", "/api/relay_ticket?room=" + self.room, {"role": "browser", "id": bid},
                          {"Authorization": "Bearer " + self.key})
        assert st == 200, (st, d)
        return d

    def connector_home(self, name="dev", workspaces=None, **overrides) -> str:
        home = os.path.join(self.tmp, name, "connector")
        CFG.ensure_dir(home)
        ws_dir = os.path.join(self.tmp, name, "projects")
        os.makedirs(os.path.join(ws_dir, "pixel", "sub"), exist_ok=True)
        cfg = CFG.initial_config(self.base, self.room, self.key, "Timur", "Test Cihazı", None)
        cfg["workspaces"] = workspaces if workspaces is not None else [
            {"id": "pixel", "name": "Pixel", "path": os.path.join(ws_dir, "pixel"), "children": True}]
        cfg["approvalPort"] = 0
        cfg["engines"]["claude"]["enabled"] = False
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = dict(cfg[k], **v)
            else:
                cfg[k] = v
        CFG.write_json(os.path.join(home, CFG.CONFIG_FILE), cfg)
        return home

    def connector(self, home, start_ctl=False, **kw):
        from pixel_connector.app import Connector
        from pixel_connector.relay import RelayClient
        cfg = CFG.load_config(home, user_home=self.tmp)
        assert cfg.ok, cfg.errors
        conn = Connector(home, cfg, start_ctl=start_ctl, user_home=self.tmp,
                         relay_factory=lambda **a: RelayClient(backoff=(0.2, 0.4, 0.8), **a), **kw)
        conn.approvals.notify_fn = self.notified.append      # no real desktop notifications from tests
        conn.start()
        assert conn.relay.connected.wait(10), conn.relay.last_error
        return conn

    def stop(self):
        try:
            self.web.shutdown()
            self.web.server_close()
        finally:
            self.relay_srv.shutdown()
            self.relay_srv.server_close()
            shutil.rmtree(self.tmp, ignore_errors=True)


class FakeBrowser:
    """Browser side of §7.3–§7.5 over a real relay connection (Python crypto)."""

    def __init__(self, env: V3Env, label: str = "Test · Linux", priv=None) -> None:
        self.env = env
        self.label = label
        if priv is None:
            self.priv, self.pub = C.generate_keypair()
        else:
            self.priv, self.pub = priv, C.public_key(priv)
        self.bid = C.peer_id(self.pub)
        self.frames: "queue.Queue" = queue.Queue()
        self.msgs: "queue.Queue" = queue.Queue()
        self.channels = {}            # deviceId → Channel
        self.devices = {}             # deviceId → pub
        self.seq = 0
        self.ws = None
        self.connect()

    def connect(self) -> None:
        t = self.env.browser_ticket(self.bid)
        self.ws = W.connect(t["url"], protocols=["pixel-relay.v1", "tk." + t["ticket"]], origin=self.env.origin)
        w = json.loads(self.ws.recv(timeout=5))
        assert w["t"] == "welcome", w
        self.conn = w["conn"]
        self.channels = {}
        self._reader = threading.Thread(target=self._read, args=(self.ws,), daemon=True)
        self._reader.start()

    def _read(self, ws) -> None:
        while True:
            try:
                m = ws.recv()
            except (W.ConnectionClosed, OSError):
                return
            try:
                f = json.loads(m)
            except ValueError:
                continue
            if f.get("t") == "msg" and f.get("k") == "e":
                ch = self.channels.get(f["from"])
                if ch is not None and ch.ch == f.get("ch"):
                    try:
                        self.msgs.put(ch.open(f["seq"], f["b"]))
                    except (C.CryptoError, C.ChannelClosed) as e:
                        self.msgs.put({"type": "__decrypt_error", "error": str(e)})
                    continue
            self.frames.put(f)

    def close(self) -> None:
        try:
            self.ws.close(1000, "", wait=0.5)
        except Exception:
            pass

    # -- L1 ----------------------------------------------------------------------------------------
    def send(self, to, k, body=None, b=None, ch=None, seq=None) -> None:
        if seq is None:
            seq = self.seq
            self.seq += 1
        frame = {"t": "send", "to": to, "ch": ch, "seq": seq, "k": k, "b": b if b is not None else encode_body(body)}
        self.ws.send_text(json.dumps(frame))

    def presence(self):
        self.ws.send_text('{"t":"presence.get"}')
        return self.wait_frame(lambda f: f.get("t") == "presence")

    def wait_frame(self, pred, timeout=10.0):
        end = time.time() + timeout
        skipped = []
        try:
            while True:
                left = end - time.time()
                if left <= 0:
                    raise AssertionError("frame not received")
                try:
                    f = self.frames.get(timeout=left)
                except queue.Empty:
                    raise AssertionError("frame not received") from None
                if pred(f):
                    return f
                skipped.append(f)
        finally:
            for f in skipped:
                self.frames.put(f)

    def hs_body(self, timeout=10.0, t=None):
        f = self.wait_frame(lambda f: f.get("t") == "msg" and f.get("k") == "hs"
                            and (t is None or decode_body(f["b"]).get("t") == t), timeout)
        return f, decode_body(f["b"])

    # -- pairing ---------------------------------------------------------------------------------
    def pair_start(self, code, iterations=C.PAIR_ITERATIONS, tamper=None):
        """Send pair1, wait pair2, send pair3. → (deviceId, sas). `tamper(stage, obj)` may modify bodies."""
        slot, secret = C.split_code(code)
        n_b = os.urandom(16)
        body = {"t": "pair1", "v": 1, "slot": slot, "qB": C.b64u(self.pub), "nB": C.b64u(n_b), "label": self.label}
        if tamper:
            body = tamper("pair1", body) or body
        self.send("pair:" + slot, "hs", body)
        f, p2 = self.hs_body(t="pair2")
        if tamper:
            p2 = tamper("pair2", p2) or p2
        q_c = C.unb64u(p2["qC"], 65)
        n_c = C.unb64u(p2["nC"], 16)
        assert C.peer_id(q_c) == p2["deviceId"]
        k = C.pairing_key(slot, secret, iterations)
        t = C.pairing_transcript(slot, p2["deviceId"], self.bid, q_c, self.pub, n_c, n_b)
        self._pair = (k, t, p2["deviceId"], q_c)
        self.send(p2["deviceId"], "hs", {"t": "pair3", "v": 1, "macB": C.b64u(C.pairing_mac(k, "B", t))})
        return p2["deviceId"], C.sas(k, t)

    def pair_finish(self, timeout=15.0) -> dict:
        _, p4 = self.hs_body(timeout, t="pair4")
        if p4.get("ok"):
            k, t, dev, q_c = self._pair
            assert C.ct_equal(C.unb64u(p4["macC"], 32), C.pairing_mac(k, "C", t)), "bad macC"
            self.devices[dev] = q_c
        return p4

    # -- handshake ---------------------------------------------------------------------------------
    def handshake(self, device_id, timeout=10.0):
        q_c = self.devices[device_id]
        e_priv, e_pub = C.generate_keypair()
        n_b = os.urandom(16)
        self.send(device_id, "hs", {"t": "hs1", "v": 1, "bid": self.bid, "eB": C.b64u(e_pub), "nb": C.b64u(n_b)})
        f, h2 = self.hs_body(timeout, t="hs2")
        e_c = C.unb64u(h2["eC"], 65)
        n_c = C.unb64u(h2["nc"], 16)
        t = C.handshake_transcript(device_id, self.bid, q_c, self.pub, e_pub, e_c, n_b, n_c)
        k_b2c, k_c2b, k_conf, ch = C.handshake_keys(t, C.ecdh(e_priv, e_c), C.ecdh(self.priv, q_c))
        assert h2["ch"] == ch
        assert C.ct_equal(C.unb64u(h2["mac"], 32), C.hmac_sha256(k_conf, b"C" + t)), "device MAC"
        chan = C.Channel(ch, k_b2c, k_c2b, "browser")
        self.channels[f["from"]] = chan
        self.send(device_id, "hs", {"t": "hs3", "ch": ch, "mac": C.b64u(C.hmac_sha256(k_conf, b"B" + t))}, ch=ch)
        return chan

    def connect_device(self, device_id):
        chan = self.handshake(device_id)
        info = self.request(device_id, {"type": "peer.hello", "label": self.label, "app": "web/3.0.0"})
        assert info["type"] == "device.info", info
        return chan, info

    # -- encrypted messages --------------------------------------------------------------------------
    def send_e(self, device_id, obj):
        chan = self.channels[device_id]
        with chan.send_lock:
            seq, b = chan.seal(obj)
            self.send(device_id, "e", b=b, ch=chan.ch, seq=seq)

    def next_msg(self, pred=lambda m: True, timeout=10.0):
        end = time.time() + timeout
        skipped = []
        try:
            while True:
                left = end - time.time()
                if left <= 0:
                    raise AssertionError("message not received")
                try:
                    m = self.msgs.get(timeout=left)
                except queue.Empty:
                    raise AssertionError("message not received") from None
                if pred(m):
                    return m
                skipped.append(m)
        finally:
            for m in skipped:
                self.msgs.put(m)

    def request(self, device_id, obj, timeout=15.0):
        self._rid = getattr(self, "_rid", 0) + 1
        rid = "r%d" % self._rid
        self.send_e(device_id, dict(obj, id=rid))
        return self.next_msg(lambda m: m.get("re") == rid, timeout)

    def task_events(self, task, timeout=20.0):
        """Collect task.chunk* until task.end → (chunks, end)."""
        chunks = []
        while True:
            m = self.next_msg(lambda m: m.get("task") == task and m.get("type") in ("task.chunk", "task.end"), timeout)
            if m["type"] == "task.end":
                return chunks, m
            chunks.append(m)


def pair(env, conn, browser, confirm=True, iterations=None):
    """Full pairing between a running connector and a FakeBrowser. → (deviceId, sas, pair4)."""
    if iterations:
        conn.router.pair_iterations = iterations
    p = conn.router.start_pairing()
    assert p.wait({"open"}, 5) == "open", p.state
    dev, sas = browser.pair_start(p.code, iterations or C.PAIR_ITERATIONS)
    assert p.wait({"confirm", "failed"}, 10) == "confirm", (p.state, p.reason)
    assert p.sas == sas
    p.confirm(confirm)
    return dev, sas, browser.pair_finish()


def text_of(chunks):
    return "".join(p.get("v", "") for c in chunks for p in c["parts"] if p.get("k") == "text")
