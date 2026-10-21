"""Phase 13: pairing (§7.3), handshake (§7.4) and envelope (§7.5) on the device side.

`RouterHarness` drives session.Router directly with an in-memory "relay" (the test plays the relay, so it can
substitute keys, replay and reorder frames). `FullStackTests` run the real connector against dev_relay + the dev
server with a FakeBrowser (same crypto) over real WebSockets.
"""

import json
import os
import tempfile
import time
import unittest

import _helpers  # noqa: F401  (sys.path)
import _v3_env as E

from pixel_connector import crypto as C
from pixel_connector.audit import AuditLog
from pixel_connector.peers import DeviceKey, PeerStore
from pixel_connector.session import Router, decode_body, encode_body

ITER = 1000          # PBKDF2 iterations for the fast router-level tests (full 600k in FullStackTests)
CONN = "c:AAAAAAAAAAAA"


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class Browser:
    def __init__(self):
        self.priv, self.pub = C.generate_keypair()
        self.bid = C.peer_id(self.pub)


class RouterHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.key = DeviceKey.create(self.tmp.name)
        self.peers = PeerStore(self.tmp.name, self.clock)
        self.audit = AuditLog(os.path.join(self.tmp.name, "audit.log"), clock=self.clock)
        self.out = []
        self.inbox = []
        self.router = Router(self.key, self.peers, self.audit, self._send, lambda p, m: self.inbox.append((p, m)),
                             "Masaüstü", self.clock)
        self.router.pair_iterations = ITER

    def tearDown(self):
        self.tmp.cleanup()

    def _send(self, frame):
        self.out.append(frame)
        return True

    def deliver(self, br, body=None, k="hs", ch=None, seq=0, b=None, conn=CONN, bid=None):
        self.router.handle_frame({"t": "msg", "from": conn, "bid": bid or br.bid, "ch": ch, "seq": seq, "k": k,
                                  "b": b if b is not None else encode_body(body)})

    def sent_bodies(self, t=None):
        out = []
        for f in self.out:
            if f.get("t") == "send" and f.get("k") == "hs":
                body = decode_body(f["b"])
                if t is None or body["t"] == t:
                    out.append((f, body))
        return out

    def events(self, ev):
        return [r for r in self.audit.read() if r["ev"] == ev]

    # -- pairing helpers ---------------------------------------------------------------------------
    def open_pairing(self):
        p = self.router.start_pairing()
        self.assertEqual(self.out[-1], {"t": "pair.open", "slot": p.slot})
        self.router.handle_frame({"t": "pair.opened", "slot": p.slot, "exp": 0})
        self.assertEqual(p.state, "open")
        return p

    def browser_pair(self, p, br, code=None, n_b=None, q_b=None, bid=None):
        slot, secret = C.split_code(code or p.code)
        n_b = n_b or os.urandom(16)
        self.deliver(br, {"t": "pair1", "v": 1, "slot": slot, "qB": C.b64u(q_b or br.pub), "nB": C.b64u(n_b),
                          "label": "Chrome · Linux"}, bid=bid)
        _, p2 = self.sent_bodies("pair2")[-1]
        q_c = C.unb64u(p2["qC"])
        k = C.pairing_key(slot, secret, ITER)
        t = C.pairing_transcript(slot, p2["deviceId"], br.bid, q_c, br.pub, C.unb64u(p2["nC"]), n_b)
        self.deliver(br, {"t": "pair3", "v": 1, "macB": C.b64u(C.pairing_mac(k, "B", t))}, bid=bid)
        return k, t

    def paired(self):
        br = Browser()
        p = self.open_pairing()
        k, t = self.browser_pair(p, br)
        self.assertEqual(p.wait({"confirm"}, 5), "confirm")
        self.assertEqual(p.sas, C.sas(k, t))
        p.confirm(True)
        return br

    def handshake(self, br, conn=CONN):
        e_priv, e_pub = C.generate_keypair()
        n_b = os.urandom(16)
        self.deliver(br, {"t": "hs1", "v": 1, "bid": br.bid, "eB": C.b64u(e_pub), "nb": C.b64u(n_b)}, conn=conn)
        f, h2 = self.sent_bodies("hs2")[-1]
        self.assertEqual(f["to"], conn)
        e_c = C.unb64u(h2["eC"], 65)
        t = C.handshake_transcript(self.key.device_id, br.bid, self.key.pub, br.pub, e_pub, e_c, n_b,
                                   C.unb64u(h2["nc"], 16))
        kb, kc, kconf, ch = C.handshake_keys(t, C.ecdh(e_priv, e_c), C.ecdh(br.priv, self.key.pub))
        self.assertEqual(h2["ch"], ch)
        self.assertTrue(C.ct_equal(C.unb64u(h2["mac"]), C.hmac_sha256(kconf, b"C" + t)))
        self.deliver(br, {"t": "hs3", "ch": ch, "mac": C.b64u(C.hmac_sha256(kconf, b"B" + t))}, ch=ch, conn=conn)
        return C.Channel(ch, kb, kc, "browser")


class PairingTests(RouterHarness):
    def test_happy_path(self):
        br = self.paired()
        _, p4 = self.sent_bodies("pair4")[-1]
        self.assertTrue(p4["ok"])
        self.assertEqual(p4["name"], "Masaüstü")
        self.assertIsNotNone(self.peers.active(br.bid))
        self.assertEqual(self.events("pair.ok")[0]["bid"], br.bid)
        self.assertIn({"t": "pair.close", "slot": self.router.pairing.slot}, self.out)   # burnt on first pair1

    def test_wrong_code_burns_slot(self):
        br = Browser()
        p = self.open_pairing()
        wrong = p.code[:4] + ("00000000" if p.code[4:] != "00000000" else "11111111")
        self.browser_pair(p, br, code=wrong)
        self.assertEqual(p.state, "failed")
        self.assertEqual(p.reason, "code")
        _, p4 = self.sent_bodies("pair4")[-1]
        self.assertEqual(p4, {"t": "pair4", "ok": False, "reason": "code"})
        self.assertIsNone(self.peers.active(br.bid))
        self.assertIn({"t": "pair.close", "slot": p.slot}, self.out)
        # single attempt: a second (correct) try on the same code is ignored
        n = len(self.out)
        self.deliver(br, {"t": "pair1", "v": 1, "slot": p.slot, "qB": C.b64u(br.pub), "nB": C.b64u(os.urandom(16))})
        self.assertEqual(len(self.out), n)
        self.assertEqual(self.events("pair.fail")[0]["reason"], "code")

    def test_sas_refusal(self):
        br = Browser()
        p = self.open_pairing()
        self.browser_pair(p, br)
        p.wait({"confirm"}, 5)
        p.confirm(False)
        self.assertEqual(self.sent_bodies("pair4")[-1][1], {"t": "pair4", "ok": False, "reason": "refused"})
        self.assertIsNone(self.peers.active(br.bid))

    def test_sas_timeout(self):
        br = Browser()
        p = self.open_pairing()
        self.browser_pair(p, br)
        p.wait({"confirm"}, 5)
        self.clock.t += 61
        self.router.tick()
        self.assertEqual((p.state, p.reason), ("failed", "timeout"))
        self.assertEqual(self.sent_bodies("pair4")[-1][1]["reason"], "timeout")

    def test_expiry(self):
        p = self.open_pairing()
        self.clock.t += 599
        self.router.tick()
        self.assertEqual(p.state, "open")
        self.clock.t += 2
        self.router.tick()
        self.assertEqual(p.state, "expired")
        self.assertEqual(self.out[-1], {"t": "pair.close", "slot": p.slot})
        br = Browser()
        self.deliver(br, {"t": "pair1", "v": 1, "slot": p.slot, "qB": C.b64u(br.pub), "nB": C.b64u(os.urandom(16))})
        self.assertEqual(self.sent_bodies("pair2"), [])

    def test_mitm_substituted_browser_key(self):
        """The relay swaps qB (and the ticket bid) for its own key: the browser's MAC no longer matches."""
        br, attacker = Browser(), Browser()
        p = self.open_pairing()
        slot, secret = C.split_code(p.code)
        n_b = os.urandom(16)
        self.deliver(attacker, {"t": "pair1", "v": 1, "slot": slot, "qB": C.b64u(attacker.pub), "nB": C.b64u(n_b)})
        _, p2 = self.sent_bodies("pair2")[-1]
        # the real browser computes its transcript with its own qB
        k = C.pairing_key(slot, secret, ITER)
        t = C.pairing_transcript(slot, p2["deviceId"], br.bid, C.unb64u(p2["qC"]), br.pub, C.unb64u(p2["nC"]), n_b)
        self.deliver(attacker, {"t": "pair3", "v": 1, "macB": C.b64u(C.pairing_mac(k, "B", t))})
        self.assertEqual((p.state, p.reason), ("failed", "code"))
        self.assertIsNone(self.peers.active(attacker.bid))

    def test_mitm_substituted_device_key(self):
        """The relay swaps qC in pair2: the browser's transcript uses the fake key, the device rejects macB."""
        br, fake_dev = Browser(), Browser()
        p = self.open_pairing()
        slot, secret = C.split_code(p.code)
        n_b = os.urandom(16)
        self.deliver(br, {"t": "pair1", "v": 1, "slot": slot, "qB": C.b64u(br.pub), "nB": C.b64u(n_b)})
        _, p2 = self.sent_bodies("pair2")[-1]
        k = C.pairing_key(slot, secret, ITER)
        t = C.pairing_transcript(slot, fake_dev.bid, br.bid, fake_dev.pub, br.pub, C.unb64u(p2["nC"]), n_b)
        self.deliver(br, {"t": "pair3", "v": 1, "macB": C.b64u(C.pairing_mac(k, "B", t))})
        self.assertEqual((p.state, p.reason), ("failed", "code"))

    def test_pair1_bid_must_match_key(self):
        br, other = Browser(), Browser()
        p = self.open_pairing()
        self.deliver(br, {"t": "pair1", "v": 1, "slot": p.slot, "qB": C.b64u(br.pub), "nB": C.b64u(os.urandom(16))},
                     bid=other.bid)
        self.assertEqual(p.state, "failed")
        self.assertEqual(self.sent_bodies("pair2"), [])

    def test_invalid_point_in_pair1(self):
        br = Browser()
        p = self.open_pairing()
        bad = b"\x04" + b"\x01" * 64
        self.deliver(br, {"t": "pair1", "v": 1, "slot": p.slot, "qB": C.b64u(bad), "nB": C.b64u(os.urandom(16))},
                     bid=C.peer_id(bad))
        self.assertEqual(p.state, "failed")
        self.assertEqual(self.sent_bodies("pair2"), [])


class HandshakeTests(RouterHarness):
    def test_handshake_and_messages(self):
        br = self.paired()
        chan = self.handshake(br)
        self.assertIn(CONN, self.router.channels)
        self.assertEqual(self.events("hs.ok")[0]["bid"], br.bid)
        seq, b = chan.seal({"type": "agent.list", "id": "1"})
        self.deliver(br, k="e", ch=chan.ch, seq=seq, b=b)
        self.assertEqual(self.inbox[-1][1], {"type": "agent.list", "id": "1"})
        peer = self.inbox[-1][0]
        self.router.send(peer, {"type": "agent.list.result", "re": "1", "agents": []})
        f = self.out[-1]
        self.assertEqual((f["to"], f["ch"], f["k"]), (CONN, chan.ch, "e"))
        self.assertEqual(chan.open(f["seq"], f["b"])["type"], "agent.list.result")

    def test_unpaired_or_revoked_bid_gets_no_answer(self):
        stranger = Browser()
        n = len(self.out)
        e_priv, e_pub = C.generate_keypair()
        self.deliver(stranger, {"t": "hs1", "v": 1, "bid": stranger.bid, "eB": C.b64u(e_pub),
                                "nb": C.b64u(os.urandom(16))})
        self.assertEqual(len(self.out), n)                         # no error oracle
        self.assertEqual(self.events("hs.fail")[-1]["reason"], "not paired or revoked")
        br = self.paired()
        self.peers.revoke(br.bid)
        n = len(self.out)
        self.deliver(br, {"t": "hs1", "v": 1, "bid": br.bid, "eB": C.b64u(e_pub), "nb": C.b64u(os.urandom(16))})
        self.assertEqual(len(self.out), n)

    def test_wrong_browser_key_fails_hs3(self):
        br = self.paired()
        impostor = Browser()
        impostor.bid = br.bid                        # knows the bid, not the private key
        e_priv, e_pub = C.generate_keypair()
        n_b = os.urandom(16)
        self.deliver(impostor, {"t": "hs1", "v": 1, "bid": br.bid, "eB": C.b64u(e_pub), "nb": C.b64u(n_b)})
        _, h2 = self.sent_bodies("hs2")[-1]
        e_c = C.unb64u(h2["eC"])
        t = C.handshake_transcript(self.key.device_id, br.bid, self.key.pub, br.pub, e_pub, e_c, n_b,
                                   C.unb64u(h2["nc"]))
        _, _, kconf, ch = C.handshake_keys(t, C.ecdh(e_priv, e_c), C.ecdh(impostor.priv, self.key.pub))
        self.deliver(impostor, {"t": "hs3", "ch": h2["ch"], "mac": C.b64u(C.hmac_sha256(kconf, b"B" + t))})
        self.assertNotIn(CONN, self.router.channels)
        self.assertEqual(self.events("hs.fail")[-1]["reason"], "mac")

    def test_replay_out_of_order_tamper_aad_and_close(self):
        br = self.paired()
        chan = self.handshake(br)
        m0 = chan.seal({"type": "agent.list", "id": "a"})
        m1 = chan.seal({"type": "agent.list", "id": "b"})
        m2 = chan.seal({"type": "agent.list", "id": "c"})
        self.deliver(br, k="e", ch=chan.ch, seq=m1[0], b=m1[1])           # out of order
        self.assertEqual(self.inbox, [])
        self.deliver(br, k="e", ch=chan.ch, seq=m0[0], b=m0[1])
        self.assertEqual(len(self.inbox), 1)
        self.deliver(br, k="e", ch=chan.ch, seq=m0[0], b=m0[1])           # replay
        self.assertEqual(len(self.inbox), 1)
        self.assertIn(CONN, self.router.channels)
        # AAD swap: a valid ciphertext for seq 1 presented with another channel id never reaches the handler
        other = C.Channel("ZZZZZZZZZZZZZZZZ", b"\x00" * 32, b"\x00" * 32, "browser")
        self.deliver(br, k="e", ch=chan.ch, seq=1, b=other.seal({"type": "x"})[1])
        self.assertEqual(len(self.inbox), 1)
        self.assertNotIn(CONN, self.router.channels)                     # 3 failures → closed
        self.assertEqual(self.events("tamper")[0]["bid"], br.bid)
        self.deliver(br, k="e", ch=chan.ch, seq=m1[0], b=m1[1])
        self.assertEqual(len(self.inbox), 1)
        del m2

    def test_tampered_tag(self):
        br = self.paired()
        chan = self.handshake(br)
        seq, b = chan.seal({"type": "agent.list"})
        raw = bytearray(C.unb64u(b))
        raw[-1] ^= 0x80
        self.deliver(br, k="e", ch=chan.ch, seq=seq, b=C.b64u(bytes(raw)))
        self.assertEqual(self.inbox, [])
        self.deliver(br, k="e", ch=chan.ch, seq=seq, b=b)               # the genuine one still works
        self.assertEqual(len(self.inbox), 1)

    def test_rehandshake_and_left(self):
        br = self.paired()
        c1 = self.handshake(br)
        c2 = self.handshake(br)                                          # reconnect: new keys, new ch
        self.assertNotEqual(c1.ch, c2.ch)
        seq, b = c1.seal({"type": "agent.list"})
        self.deliver(br, k="e", ch=c1.ch, seq=seq, b=b)
        self.assertEqual(self.inbox, [])
        seq, b = c2.seal({"type": "agent.list"})
        self.deliver(br, k="e", ch=c2.ch, seq=seq, b=b)
        self.assertEqual(len(self.inbox), 1)
        self.router.handle_frame({"t": "left", "conn": CONN})
        self.assertNotIn(CONN, self.router.channels)

    def test_revoke_closes_channels(self):
        br = self.paired()
        self.handshake(br)
        self.peers.revoke(br.bid)
        self.assertEqual(self.router.close_bid(br.bid), 1)
        self.assertEqual(self.router.channels, {})

    def test_handshake_rate_limit(self):
        self.router.hs_limit = 3
        br = self.paired()
        for _ in range(3):
            self.handshake(br)
        n = len(self.out)
        e_priv, e_pub = C.generate_keypair()
        self.deliver(br, {"t": "hs1", "v": 1, "bid": br.bid, "eB": C.b64u(e_pub), "nb": C.b64u(os.urandom(16))})
        self.assertEqual(len(self.out), n)
        self.assertEqual(self.events("denied")[-1]["reason"], "handshake rate limit")


class FullStackTests(unittest.TestCase):
    """Real connector + dev_relay + dev server + FakeBrowser, full 600,000-iteration PBKDF2."""

    def setUp(self):
        self.env = E.V3Env()
        self.conn = self.env.connector(self.env.connector_home())

    def tearDown(self):
        self.conn.stop()
        self.env.stop()

    def test_pair_handshake_and_info(self):
        br = E.FakeBrowser(self.env)
        pres = br.presence()
        self.assertEqual([d["id"] for d in pres["devices"]], [self.conn.key.device_id])
        dev, sas, p4 = E.pair(self.env, self.conn, br)
        self.assertTrue(p4["ok"])
        self.assertRegex(sas, r"^\d{3} \d{3}$")
        chan, info = br.connect_device(dev)
        self.assertEqual(info["name"], "Test Cihazı")
        self.assertEqual(info["connector"], "3.2.1")
        self.assertEqual(info["build"], "dev")              # source checkout; the .pyz carries its build id
        self.assertIsNone(info["pyz"])
        self.assertEqual([p["id"] for p in info["profiles"]], ["readonly", "edit", "dev", "auto"])
        peers = br.request(dev, {"type": "peer.list"})["peers"]
        self.assertEqual([(p["bid"], p["self"]) for p in peers], [(br.bid, True)])
        br.close()

    def test_wrong_code_single_attempt(self):
        br = E.FakeBrowser(self.env)
        p = self.conn.router.start_pairing()
        self.assertEqual(p.wait({"open"}, 5), "open")
        wrong = p.code[:4] + ("00000000" if p.code[4:] != "00000000" else "11111111")
        br.pair_start(wrong)
        self.assertEqual(br.pair_finish(), {"t": "pair4", "ok": False, "reason": "code"})
        self.assertEqual((p.state, p.reason), ("failed", "code"))
        # the slot is gone at the relay: the right code now finds nobody
        br2 = E.FakeBrowser(self.env)
        slot, _ = C.split_code(p.code)
        br2.send("pair:" + slot, "hs", {"t": "pair1", "v": 1, "slot": slot, "qB": C.b64u(br2.pub),
                                        "nB": C.b64u(os.urandom(16))})
        err = br2.wait_frame(lambda f: f.get("t") == "err")
        self.assertEqual(err["code"], "slot_unknown")
        br.close()
        br2.close()

    def test_sas_refused_end_to_end(self):
        br = E.FakeBrowser(self.env)
        dev, sas, p4 = E.pair(self.env, self.conn, br, confirm=False)
        self.assertEqual(p4, {"t": "pair4", "ok": False, "reason": "refused"})
        self.assertIsNone(self.conn.peers.active(br.bid))
        br.close()

    def test_device_revoke_and_reconnect_rehandshake(self):
        br = E.FakeBrowser(self.env)
        dev, _, _ = E.pair(self.env, self.conn, br)
        br.connect_device(dev)
        # browser reconnects (new conn): a fresh handshake is required and works
        br.close()
        br.connect()
        br.connect_device(dev)
        self.assertTrue(self.conn.revoke(br.bid))
        time.sleep(0.2)
        self.assertEqual(self.conn.router.channels, {})
        with self.assertRaises(AssertionError):
            br.handshake(dev, timeout=1.5)
        audit = [r["ev"] for r in self.conn.audit.read()]
        self.assertIn("peer.revoke", audit)
        br.close()


if __name__ == "__main__":
    unittest.main()
