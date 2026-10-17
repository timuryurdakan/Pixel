"""Phase 13: pixel_connector/crypto.py against official vectors (V3.md §7.1, §17.2).

tests/vectors/ (generated from the downloaded originals, sources and dates inside each file):
- p256_ecdh.json: RFC 5903 §8.1, NIST CAVP KAS ECC CDH P-256 (25), Wycheproof ecdh_secp256r1_ecpoint_test (355,
  including invalid-curve points, wrong encodings, compressed points)
- aes_gcm.json: NIST CAVP gcmEncryptExtIV256/gcmDecrypt256 (96-bit IV, 128-bit tag; incl. FAIL cases), Wycheproof
  aes_gcm_test (keySize 256, ivSize 96: valid, special cases and modified tags)
- hkdf.json: RFC 5869 A.1–A.3; pbkdf2.json: RFC 7914 §11; hmac.json: RFC 4231 (SHA-256)
"""

import json
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import crypto as C

V = Path(__file__).resolve().parent / "vectors"


def load(name):
    return json.loads((V / name).read_text(encoding="utf-8"))


H = bytes.fromhex


class P256Tests(unittest.TestCase):
    def test_rfc5903(self):
        r = load("p256_ecdh.json")["rfc5903"]
        self.assertEqual(C.public_key(int(r["i"], 16)).hex(), "04" + r["gix"] + r["giy"])
        self.assertEqual(C.public_key(int(r["r"], 16)).hex(), "04" + r["grx"] + r["gry"])
        self.assertEqual(C.ecdh(int(r["i"], 16), H("04" + r["grx"] + r["gry"])).hex(), r["girx"])
        self.assertEqual(C.ecdh(int(r["r"], 16), H("04" + r["gix"] + r["giy"])).hex(), r["girx"])

    def test_nist_cavp_cdh(self):
        vs = load("p256_ecdh.json")["cavp"]
        self.assertEqual(len(vs), 25)
        for v in vs:
            with self.subTest(count=v["count"]):
                self.assertEqual(C.public_key(H(v["dIUT"])).hex(), "04" + v["qIUTx"] + v["qIUTy"])
                self.assertEqual(C.ecdh(int(v["dIUT"], 16), H("04" + v["qCAVSx"] + v["qCAVSy"])).hex(), v["z"])

    def test_wycheproof_ecpoint(self):
        vs = load("p256_ecdh.json")["wycheproof"]
        self.assertGreater(len(vs), 300)
        counts = {"valid": 0, "invalid": 0, "acceptable": 0}
        for v in vs:
            counts[v["result"]] += 1
            with self.subTest(tc=v["tcId"], comment=v["comment"]):
                try:
                    z = C.ecdh(int(v["private"], 16), H(v["public"])).hex()
                except C.CryptoError:
                    z = None
                if v["result"] == "valid":
                    self.assertEqual(z, v["shared"])
                elif v["result"] == "invalid":
                    self.assertIsNone(z, "invalid point accepted")
                else:                                   # compressed points: we only accept uncompressed
                    self.assertIsNone(z)
        self.assertGreaterEqual(counts["invalid"], 20)
        self.assertGreater(len([v for v in vs if "InvalidCurveAttack" in v["flags"]]), 10)

    def test_point_validation(self):
        g = C.public_key(1)
        self.assertEqual(g, C.encode_point(C.GX, C.GY))
        x, y = C.decode_point(g)
        for bad in (g[:-1], b"\x02" + g[1:], b"\x04" + b"\x00" * 64,
                    b"\x04" + C.P.to_bytes(32, "big") + g[33:],                 # x = p
                    b"\x04" + g[1:33] + ((y + 1) % C.P).to_bytes(32, "big"),    # off curve
                    b"\x04" + g[1:33] + (C.P - y + C.P).to_bytes(33, "big")[1:]):
            with self.assertRaises(C.CryptoError):
                C.decode_point(bad)
        for d in (0, C.N, C.N + 1, -1):
            with self.assertRaises(C.CryptoError):
                C.public_key(d)
        self.assertEqual(C.public_key(C.N - 1), C.encode_point(C.GX, C.P - C.GY))     # -G

    def test_keypair_agreement_and_ids(self):
        d1, q1 = C.generate_keypair()
        d2, q2 = C.generate_keypair()
        self.assertEqual(C.ecdh(d1, q2), C.ecdh(d2, q1))
        pid = C.peer_id(q1)
        self.assertRegex(pid, r"^[a-z2-7]{16}$")


class KdfMacTests(unittest.TestCase):
    def test_hkdf_rfc5869(self):
        for v in load("hkdf.json")["vectors"]:
            with self.subTest(case=v["case"]):
                self.assertEqual(C.hkdf_extract(H(v["salt"]), H(v["ikm"])).hex(), v["prk"])
                self.assertEqual(C.hkdf(H(v["salt"]), H(v["ikm"]), H(v["info"]), v["L"]).hex(), v["okm"])

    def test_hkdf_lengths(self):
        for n in (1, 31, 32, 33, 96, 255 * 32):
            self.assertEqual(len(C.hkdf(b"s", b"k", b"i", n)), n)
        with self.assertRaises(ValueError):
            C.hkdf(b"s", b"k", b"i", 255 * 32 + 1)

    def test_pbkdf2_rfc7914(self):
        for v in load("pbkdf2.json")["vectors"]:
            with self.subTest(c=v["c"]):
                self.assertEqual(C.pbkdf2_sha256(v["password"].encode(), v["salt"].encode(), v["c"], v["dkLen"]).hex(),
                                 v["dk"])

    def test_hmac_rfc4231(self):
        for v in load("hmac.json")["vectors"]:
            with self.subTest(case=v["case"]):
                self.assertEqual(C.hmac_sha256(H(v["key"]), H(v["data"])).hex(), v["mac"])


class AesGcmTests(unittest.TestCase):
    def test_fips197_aes256(self):
        self.assertEqual(C.aes256_encrypt_block(bytes(range(32)), H("00112233445566778899aabbccddeeff")).hex(),
                         "8ea2b7ca516745bfeafc49904b496089")

    def test_nist_gcm(self):
        vs = load("aes_gcm.json")["nist"]
        self.assertGreater(len(vs), 100)
        self.assertTrue(any(not v["valid"] for v in vs))
        for i, v in enumerate(vs):
            with self.subTest(i=i, kind=v["kind"]):
                g = C.AESGCM(H(v["key"]))
                if v["valid"]:
                    self.assertEqual(g.encrypt(H(v["iv"]), H(v["pt"]), H(v["aad"])).hex(), v["ct"] + v["tag"])
                    self.assertEqual(g.decrypt(H(v["iv"]), H(v["ct"] + v["tag"]), H(v["aad"])).hex(), v["pt"])
                else:
                    with self.assertRaises(C.CryptoError):
                        g.decrypt(H(v["iv"]), H(v["ct"] + v["tag"]), H(v["aad"]))

    def test_wycheproof_gcm(self):
        vs = load("aes_gcm.json")["wycheproof"]
        self.assertTrue(any("ModifiedTag" in v["flags"] for v in vs))
        for v in vs:
            with self.subTest(tc=v["tcId"], comment=v["comment"]):
                g = C.AESGCM(H(v["key"]))
                if v["result"] == "valid":
                    self.assertEqual(g.encrypt(H(v["iv"]), H(v["msg"]), H(v["aad"])).hex(), v["ct"] + v["tag"])
                    self.assertEqual(g.decrypt(H(v["iv"]), H(v["ct"] + v["tag"]), H(v["aad"])).hex(), v["msg"])
                else:
                    with self.assertRaises(C.CryptoError):
                        g.decrypt(H(v["iv"]), H(v["ct"] + v["tag"]), H(v["aad"]))

    def test_tamper_every_byte_and_bad_inputs(self):
        g = C.AESGCM(b"\x01" * 32)
        iv = C.iv_for(7)
        ct = g.encrypt(iv, b"secret message 123", b"aad")
        for i in range(len(ct)):
            t = bytearray(ct)
            t[i] ^= 0x01
            with self.assertRaises(C.CryptoError):
                g.decrypt(iv, bytes(t), b"aad")
        with self.assertRaises(C.CryptoError):
            g.decrypt(iv, ct, b"aaD")
        with self.assertRaises(C.CryptoError):
            g.decrypt(C.iv_for(8), ct, b"aad")
        with self.assertRaises(C.CryptoError):
            g.decrypt(iv, ct[:15])
        with self.assertRaises(C.CryptoError):
            g.encrypt(b"\x00" * 16, b"x")
        with self.assertRaises(C.CryptoError):
            C.AESGCM(b"\x00" * 16)

    def test_iv_construction(self):
        self.assertEqual(C.iv_for(0), b"\x00" * 12)
        self.assertEqual(C.iv_for(1).hex(), "000000000000000000000001")
        self.assertEqual(C.iv_for(2 ** 32 + 5).hex(), "000000000000000100000005")
        self.assertEqual(C.aad_for("AbCdEfGhIjKlMnOp", "b2c", 12), b"pixel-e2e-v1|AbCdEfGhIjKlMnOp|b2c|12")


class EnvelopeTests(unittest.TestCase):
    def pair(self):
        kb, kc = b"\x11" * 32, b"\x22" * 32
        return C.Channel("AbCdEfGhIjKlMnOp", kb, kc, "browser"), C.Channel("AbCdEfGhIjKlMnOp", kb, kc, "device")

    def test_padding_roundtrip(self):
        for n in (0, 1, 63, 64, 65, 1000):
            data = b"x" * n
            p = C.pad(data)
            self.assertEqual(len(p) % 64, 0)
            self.assertEqual(C.unpad(p), data)
        b, d = self.pair()
        seq, blob = b.seal({"type": "ping", "x": "ü" * 10})
        self.assertEqual((len(C.unb64u(blob)) - 16) % 64, 0)
        self.assertEqual(d.open(seq, blob), {"type": "ping", "x": "ü" * 10})

    def test_replay_out_of_order_and_close_after_three(self):
        b, d = self.pair()
        m0 = b.seal({"type": "a"})
        m1 = b.seal({"type": "b"})
        m2 = b.seal({"type": "c"})
        with self.assertRaises(C.CryptoError):          # out of order
            d.open(*m1)
        self.assertEqual(d.open(*m0)["type"], "a")
        with self.assertRaises(C.CryptoError):          # replay
            d.open(*m0)
        self.assertEqual(d.open(*m1)["type"], "b")
        with self.assertRaises(C.ChannelClosed):        # third failure closes
            d.open(m2[0], m2[1][:-2] + ("AA" if m2[1][-2:] != "AA" else "BA"))
        with self.assertRaises(C.ChannelClosed):
            d.open(*m2)

    def test_direction_separation(self):
        b, d = self.pair()
        seq, blob = d.seal({"type": "from-device"})
        with self.assertRaises(C.CryptoError):          # a device frame is not valid as browser→device
            d.open(seq, blob)
        self.assertEqual(b.open(seq, blob)["type"], "from-device")

    def test_code_helpers(self):
        code = C.new_pairing_code()
        self.assertRegex(code, r"^[0-9A-HJKMNP-TV-Z]{12}$")
        self.assertEqual(C.normalize_code("k7q2-m4x5 z6a9"), "K7Q2M4X5Z6A9")
        self.assertEqual(C.normalize_code("OIL0-0000-0000"), "0110" + "0" * 8)
        for bad in ("K7Q2-M4X5", "K7Q2-M4X5-Z6AU", "", None):
            with self.assertRaises(ValueError):
                C.normalize_code(bad)
        self.assertEqual(C.format_code("K7Q2M4X5Z6A9"), "K7Q2-M4X5-Z6A9")
        self.assertRegex(C.sas(b"k" * 32, b"t" * 32), r"^\d{3} \d{3}$")


if __name__ == "__main__":
    unittest.main()
