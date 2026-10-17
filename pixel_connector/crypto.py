"""Vendored pure-Python crypto for the connector (V3.md §7). Python ≥ 3.8, stdlib only.

Primitives
- P-256 (secp256r1) ECDH: Jacobian coordinates, a fixed 256-step Montgomery ladder with an arithmetic conditional
  swap, SEC1 uncompressed point decoding with full validation (length 65, prefix 0x04, 0 ≤ x,y < p, on the curve;
  cofactor 1, so every valid point is in the prime-order group). The shared secret is the 32-byte x-coordinate.
- HKDF-SHA256 (RFC 5869) on `hmac` + `hashlib`; PBKDF2-HMAC-SHA256 is `hashlib.pbkdf2_hmac` (C code in CPython).
- AES-256 (FIPS-197) encryption with T-tables + GCM (SP 800-38D) with 96-bit IVs and 128-bit tags. GHASH is a
  bitwise GF(2^128) multiply with masks instead of branches (no key-dependent tables). Tags are compared with
  `hmac.compare_digest` before any plaintext is released.

Protocol helpers: pairing code, transcript hashes, MACs and SAS (§7.3), the session handshake key schedule (§7.4)
and the message envelope (§7.5, `Channel`).

Constant time (honest statement, see also V3.md §7.1 and §20 "Phase 13 deviations"):
- constant-time: all MAC/tag comparisons (`hmac.compare_digest`); the ladder runs exactly 256 iterations of one
  add + one double regardless of the scalar; the conditional swap and the GHASH multiply use arithmetic masks.
- NOT constant-time: CPython big-integer arithmetic (its run time depends on operand sizes), modular inversion
  via `pow(z, p-2, p)`, the special-case branches inside point addition (taken only for O / equal points, which a
  valid ladder input reaches only at the start), and the AES S-box/T-table lookups (cache timing). An attacker who
  can measure this process on the same machine could in theory learn key bits; remote timing through the relay is
  drowned in network jitter. A local attacker on the device is outside the threat model (§14 T14/T15).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
from typing import Optional, Tuple

# --------------------------------------------------------------------------------------------------
# Encoding helpers
# --------------------------------------------------------------------------------------------------


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(bytes(data)).decode("ascii").rstrip("=")


def unb64u(s: str, length: Optional[int] = None) -> bytes:
    """Strict base64url without padding (canonical). Raises ValueError."""
    if not isinstance(s, str) or len(s) % 4 == 1 or len(s) > 200000:
        raise ValueError("bad base64url")
    for ch in s:
        if not (ch.isascii() and (ch.isalnum() or ch in "-_")):
            raise ValueError("bad base64url")
    raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    if b64u(raw) != s:
        raise ValueError("non-canonical base64url")
    if length is not None and len(raw) != length:
        raise ValueError("wrong length")
    return raw


def peer_id(pub_raw: bytes) -> str:
    """deviceId / bid = lowercase base32(SHA-256(raw 65-byte public key))[:16] (80 bits)."""
    return base64.b32encode(hashlib.sha256(pub_raw).digest()).decode("ascii").lower()[:16]


# --------------------------------------------------------------------------------------------------
# P-256
# --------------------------------------------------------------------------------------------------

P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
A = P - 3
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
GX = 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296
GY = 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5

_INF = (1, 1, 0)        # Jacobian point at infinity (Z = 0)


class CryptoError(ValueError):
    """Invalid key, point, ciphertext or tag."""


def _on_curve(x: int, y: int) -> bool:
    return (y * y - (x * x * x + A * x + B)) % P == 0


def _jdouble(p1):
    x1, y1, z1 = p1
    if z1 == 0 or y1 == 0:
        return _INF
    # dbl-2001-b (a = -3)
    delta = z1 * z1 % P
    gamma = y1 * y1 % P
    beta = x1 * gamma % P
    alpha = 3 * (x1 - delta) * (x1 + delta) % P
    x3 = (alpha * alpha - 8 * beta) % P
    z3 = ((y1 + z1) * (y1 + z1) - gamma - delta) % P
    y3 = (alpha * (4 * beta - x3) - 8 * gamma * gamma) % P
    return (x3, y3, z3)


def _jadd(p1, p2):
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    if z1 == 0:
        return p2
    if z2 == 0:
        return p1
    # add-2007-bl
    z1z1 = z1 * z1 % P
    z2z2 = z2 * z2 % P
    u1 = x1 * z2z2 % P
    u2 = x2 * z1z1 % P
    s1 = y1 * z2 * z2z2 % P
    s2 = y2 * z1 * z1z1 % P
    h = (u2 - u1) % P
    r = 2 * (s2 - s1) % P
    if h == 0:
        return _jdouble(p1) if r == 0 else _INF
    i = 4 * h * h % P
    j = h * i % P
    v = u1 * i % P
    x3 = (r * r - j - 2 * v) % P
    y3 = (r * (v - x3) - 2 * s1 * j) % P
    z3 = ((z1 + z2) * (z1 + z2) - z1z1 - z2z2) * h % P
    return (x3, y3, z3)


def _cswap(bit: int, a, b):
    """Swap the two Jacobian points when bit == 1, using masks (no branch on the bit)."""
    mask = -bit                     # 0 or -1 (all ones)
    out_a, out_b = [], []
    for u, v in zip(a, b):
        t = (u ^ v) & mask
        out_a.append(u ^ t)
        out_b.append(v ^ t)
    return tuple(out_a), tuple(out_b)


def _ladder(k: int, x: int, y: int) -> Tuple[int, int]:
    """k·(x, y) with a fixed 256-step Montgomery ladder → affine (x, y). Raises CryptoError on infinity."""
    r0, r1 = _INF, (x, y, 1)
    for i in range(255, -1, -1):
        bit = (k >> i) & 1
        r0, r1 = _cswap(bit, r0, r1)
        r1 = _jadd(r0, r1)
        r0 = _jdouble(r0)
        r0, r1 = _cswap(bit, r0, r1)
    xr, yr, zr = r0
    if zr == 0:
        raise CryptoError("point at infinity")
    zi = pow(zr, P - 2, P)
    zi2 = zi * zi % P
    return xr * zi2 % P, yr * zi2 * zi % P


def decode_point(raw: bytes) -> Tuple[int, int]:
    """SEC1 uncompressed point with full validation. Raises CryptoError."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 65 or raw[0] != 4:
        raise CryptoError("bad point encoding")
    x = int.from_bytes(raw[1:33], "big")
    y = int.from_bytes(raw[33:65], "big")
    if x >= P or y >= P or not _on_curve(x, y):
        raise CryptoError("point not on curve")
    return x, y


def encode_point(x: int, y: int) -> bytes:
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def _scalar(d) -> int:
    k = int.from_bytes(d, "big") if isinstance(d, (bytes, bytearray)) else int(d)
    if not 1 <= k < N:
        raise CryptoError("private scalar out of range")
    return k


def public_key(d) -> bytes:
    """Raw 65-byte public key for the private scalar d (int or 32 bytes)."""
    x, y = _ladder(_scalar(d), GX, GY)
    return encode_point(x, y)


def generate_private() -> int:
    return secrets.randbelow(N - 1) + 1


def generate_keypair() -> Tuple[int, bytes]:
    d = generate_private()
    return d, public_key(d)


def ecdh(d, peer_pub: bytes) -> bytes:
    """32-byte shared secret (x-coordinate of d·Q). Validates Q first. Raises CryptoError."""
    qx, qy = decode_point(bytes(peer_pub))
    x, _ = _ladder(_scalar(d), qx, qy)
    return x.to_bytes(32, "big")


# --------------------------------------------------------------------------------------------------
# HMAC / HKDF / PBKDF2
# --------------------------------------------------------------------------------------------------

def hmac_sha256(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac_sha256(salt if salt else b"\x00" * 32, ikm)


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    if length > 255 * 32:
        raise ValueError("HKDF output too long")
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac_sha256(prk, t + info + bytes([i]))
        out += t
        i += 1
    return out[:length]


def hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return hkdf_expand(hkdf_extract(salt, ikm), info, length)


def pbkdf2_sha256(password: bytes, salt: bytes, iterations: int, length: int = 32) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, salt, iterations, length)


def ct_equal(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(bytes(a), bytes(b))


# --------------------------------------------------------------------------------------------------
# AES-256 (encryption direction only; GCM never needs the inverse cipher)
# --------------------------------------------------------------------------------------------------

def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _gmul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _build_tables():
    inv = [0] * 256
    for x in range(1, 256):          # x^254 = x^-1 in GF(2^8)
        r, base, e = 1, x, 254
        while e:
            if e & 1:
                r = _gmul(r, base)
            base = _gmul(base, base)
            e >>= 1
        inv[x] = r
    sbox = []
    for x in range(256):
        b = inv[x]
        s = b
        for k in range(1, 5):
            s ^= ((b << k) | (b >> (8 - k))) & 0xFF
        sbox.append(s ^ 0x63)
    t0 = [(_xtime(s) << 24) | (s << 16) | (s << 8) | (_xtime(s) ^ s) for s in sbox]

    def ror8(w):
        return ((w >> 8) | (w << 24)) & 0xFFFFFFFF
    t1 = [ror8(w) for w in t0]
    t2 = [ror8(w) for w in t1]
    t3 = [ror8(w) for w in t2]
    return sbox, t0, t1, t2, t3


SBOX, _T0, _T1, _T2, _T3 = _build_tables()


def _expand_key_256(key: bytes):
    if len(key) != 32:
        raise CryptoError("AES-256 key must be 32 bytes")
    w = list(struct.unpack(">8I", key))
    rcon = 1
    sb = SBOX
    for i in range(8, 60):
        t = w[i - 1]
        if i % 8 == 0:
            t = ((t << 8) | (t >> 24)) & 0xFFFFFFFF
            t = (sb[t >> 24] << 24) | (sb[(t >> 16) & 255] << 16) | (sb[(t >> 8) & 255] << 8) | sb[t & 255]
            t ^= rcon << 24
            rcon = _xtime(rcon)
        elif i % 8 == 4:
            t = (sb[t >> 24] << 24) | (sb[(t >> 16) & 255] << 16) | (sb[(t >> 8) & 255] << 8) | sb[t & 255]
        w.append(w[i - 8] ^ t)
    return w


def _encrypt_words(rk, s0: int, s1: int, s2: int, s3: int):
    T0, T1, T2, T3, sb = _T0, _T1, _T2, _T3, SBOX
    s0 ^= rk[0]
    s1 ^= rk[1]
    s2 ^= rk[2]
    s3 ^= rk[3]
    k = 4
    for _ in range(13):
        t0 = T0[s0 >> 24] ^ T1[(s1 >> 16) & 255] ^ T2[(s2 >> 8) & 255] ^ T3[s3 & 255] ^ rk[k]
        t1 = T0[s1 >> 24] ^ T1[(s2 >> 16) & 255] ^ T2[(s3 >> 8) & 255] ^ T3[s0 & 255] ^ rk[k + 1]
        t2 = T0[s2 >> 24] ^ T1[(s3 >> 16) & 255] ^ T2[(s0 >> 8) & 255] ^ T3[s1 & 255] ^ rk[k + 2]
        t3 = T0[s3 >> 24] ^ T1[(s0 >> 16) & 255] ^ T2[(s1 >> 8) & 255] ^ T3[s2 & 255] ^ rk[k + 3]
        s0, s1, s2, s3 = t0, t1, t2, t3
        k += 4
    o0 = ((sb[s0 >> 24] << 24) | (sb[(s1 >> 16) & 255] << 16) | (sb[(s2 >> 8) & 255] << 8) | sb[s3 & 255]) ^ rk[k]
    o1 = ((sb[s1 >> 24] << 24) | (sb[(s2 >> 16) & 255] << 16) | (sb[(s3 >> 8) & 255] << 8) | sb[s0 & 255]) ^ rk[k + 1]
    o2 = ((sb[s2 >> 24] << 24) | (sb[(s3 >> 16) & 255] << 16) | (sb[(s0 >> 8) & 255] << 8) | sb[s1 & 255]) ^ rk[k + 2]
    o3 = ((sb[s3 >> 24] << 24) | (sb[(s0 >> 16) & 255] << 16) | (sb[(s1 >> 8) & 255] << 8) | sb[s2 & 255]) ^ rk[k + 3]
    return o0, o1, o2, o3


def aes256_encrypt_block(key: bytes, block: bytes) -> bytes:
    rk = _expand_key_256(key)
    return struct.pack(">4I", *_encrypt_words(rk, *struct.unpack(">4I", block)))


# --------------------------------------------------------------------------------------------------
# GCM
# --------------------------------------------------------------------------------------------------

_R = 0xE1 << 120
_M128 = (1 << 128) - 1


def _gf_mult(x: int, y: int) -> int:
    """X·Y in GF(2^128) with GCM's bit order (SP 800-38D Alg. 1), branch-free on the data bits."""
    z = 0
    v = y
    for i in range(127, -1, -1):
        z ^= v & -((x >> i) & 1)
        v = (v >> 1) ^ (_R & -(v & 1))
    return z


class AESGCM:
    """AES-256-GCM with a 96-bit IV and a 128-bit tag."""

    TAG = 16

    def __init__(self, key: bytes) -> None:
        self._rk = _expand_key_256(bytes(key))
        self._h = int.from_bytes(self._block(b"\x00" * 16), "big")

    def _block(self, b: bytes) -> bytes:
        return struct.pack(">4I", *_encrypt_words(self._rk, *struct.unpack(">4I", b)))

    def _ghash(self, aad: bytes, ct: bytes) -> int:
        h = self._h
        y = 0
        for data in (aad, ct):
            for i in range(0, len(data), 16):
                blk = data[i:i + 16]
                if len(blk) < 16:
                    blk = blk + b"\x00" * (16 - len(blk))
                y = _gf_mult(y ^ int.from_bytes(blk, "big"), h)
        y = _gf_mult(y ^ ((len(aad) * 8) << 64 | (len(ct) * 8)), h)
        return y

    def _ctr(self, iv: bytes, data: bytes) -> bytes:
        rk = self._rk
        prefix = struct.unpack(">3I", iv)
        out = bytearray(len(data))
        ctr = 2
        for i in range(0, len(data), 16):
            k = struct.pack(">4I", *_encrypt_words(rk, prefix[0], prefix[1], prefix[2], ctr))
            chunk = data[i:i + 16]
            out[i:i + len(chunk)] = (int.from_bytes(chunk, "big") ^ int.from_bytes(k[:len(chunk)], "big")) \
                .to_bytes(len(chunk), "big")
            ctr = (ctr + 1) & 0xFFFFFFFF
        return bytes(out)

    def _tag(self, iv: bytes, aad: bytes, ct: bytes) -> bytes:
        s = self._ghash(aad, ct)
        ek = int.from_bytes(self._block(iv + b"\x00\x00\x00\x01"), "big")
        return ((s ^ ek) & _M128).to_bytes(16, "big")

    @staticmethod
    def _check_iv(iv: bytes) -> bytes:
        iv = bytes(iv)
        if len(iv) != 12:
            raise CryptoError("IV must be 96 bits")
        return iv

    def encrypt(self, iv: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
        """→ ciphertext ‖ tag."""
        iv = self._check_iv(iv)
        if len(plaintext) > (1 << 32) * 16:
            raise CryptoError("plaintext too long")
        ct = self._ctr(iv, bytes(plaintext))
        return ct + self._tag(iv, bytes(aad), ct)

    def decrypt(self, iv: bytes, data: bytes, aad: bytes = b"") -> bytes:
        """ciphertext ‖ tag → plaintext. The tag is checked (constant time) before decryption. Raises CryptoError."""
        iv = self._check_iv(iv)
        data = bytes(data)
        if len(data) < self.TAG:
            raise CryptoError("ciphertext too short")
        ct, tag = data[:-self.TAG], data[-self.TAG:]
        if not hmac.compare_digest(self._tag(iv, bytes(aad), ct), tag):
            raise CryptoError("authentication failed")
        return self._ctr(iv, ct)


# --------------------------------------------------------------------------------------------------
# Pairing (§7.3)
# --------------------------------------------------------------------------------------------------

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PAIR_ITERATIONS = 600000
PAIR_TTL_S = 600


def new_pairing_code() -> str:
    """12 Crockford base32 characters (4 slot + 8 secret = 40 bits)."""
    return "".join(CROCKFORD[secrets.randbelow(32)] for _ in range(12))


def format_code(code: str) -> str:
    return "%s-%s-%s" % (code[:4], code[4:8], code[8:12])


def normalize_code(text: str) -> str:
    """Uppercase, O→0, I/L→1, strip '-' and spaces. Raises ValueError unless 12 valid characters remain."""
    s = str(text or "").upper().replace("-", "").replace(" ", "").replace("O", "0").replace("I", "1").replace("L", "1")
    if len(s) != 12 or any(c not in CROCKFORD for c in s):
        raise ValueError("bad pairing code")
    return s


def split_code(code: str) -> Tuple[str, str]:
    c = normalize_code(code)
    return c[:4], c[4:]


def pairing_key(slot: str, secret: str, iterations: int = PAIR_ITERATIONS) -> bytes:
    """K = PBKDF2-SHA256(secret (8 ASCII chars), salt = "pixel-pair-v1|" + slot, 600000, 32 B)."""
    return pbkdf2_sha256(secret.encode("ascii"), ("pixel-pair-v1|" + slot).encode("ascii"), iterations, 32)


def pairing_transcript(slot: str, device_id: str, bid: str, q_c: bytes, q_b: bytes, n_c: bytes, n_b: bytes) -> bytes:
    return hashlib.sha256(b"pixel-pair-v1" + slot.encode("ascii") + device_id.encode("ascii") + bid.encode("ascii")
                          + q_c + q_b + n_c + n_b).digest()


def pairing_mac(k: bytes, who: str, t: bytes) -> bytes:
    return hmac_sha256(k, who.encode("ascii") + t)


def sas(k: bytes, t: bytes) -> str:
    """6-digit short authentication string, shown as "482 913"."""
    v = struct.unpack(">I", hmac_sha256(k, b"SAS" + t)[:4])[0] % 1000000
    s = "%06d" % v
    return s[:3] + " " + s[3:]


# --------------------------------------------------------------------------------------------------
# Session handshake (§7.4) and envelope (§7.5)
# --------------------------------------------------------------------------------------------------

def handshake_transcript(device_id: str, bid: str, q_c: bytes, q_b: bytes, e_b: bytes, e_c: bytes,
                         n_b: bytes, n_c: bytes) -> bytes:
    return hashlib.sha256(b"pixel-hs-v1" + device_id.encode("ascii") + bid.encode("ascii")
                          + q_c + q_b + e_b + e_c + n_b + n_c).digest()


def handshake_keys(t: bytes, ee: bytes, ss: bytes) -> Tuple[bytes, bytes, bytes, str]:
    """→ (k_b2c, k_c2b, k_conf, ch)."""
    okm = hkdf(t, ee + ss, b"pixel-e2e-v1", 96)
    return okm[0:32], okm[32:64], okm[64:96], b64u(t[:12])


PAD = 64
MAX_SEQ = 1 << 32


def iv_for(seq: int) -> bytes:
    return b"\x00\x00\x00\x00" + struct.pack(">Q", seq)


def aad_for(ch: str, direction: str, seq: int) -> bytes:
    return ("pixel-e2e-v1|%s|%s|%d" % (ch, direction, seq)).encode("utf-8")


def pad(data: bytes) -> bytes:
    return data + b"\x00" * (-len(data) % PAD)


def unpad(data: bytes) -> bytes:
    return data.rstrip(b"\x00")


def encode_inner(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ChannelClosed(Exception):
    pass


class Channel:
    """One E2E channel (one browser tab × device × WebSocket connection). `role` is "device" or "browser".
    Thread-safety: callers serialise `seal` + send under `send_lock` so frames leave in seq order."""

    MAX_FAILURES = 3

    def __init__(self, ch: str, k_b2c: bytes, k_c2b: bytes, role: str) -> None:
        import threading
        if role not in ("device", "browser"):
            raise ValueError("role")
        self.ch = ch
        self.role = role
        self.send_dir, self.recv_dir = ("c2b", "b2c") if role == "device" else ("b2c", "c2b")
        self._send = AESGCM(k_c2b if role == "device" else k_b2c)
        self._recv = AESGCM(k_b2c if role == "device" else k_c2b)
        self.send_seq = 0
        self.recv_seq = 0
        self.failures = 0
        self.closed = False
        self.send_lock = threading.Lock()

    def seal(self, obj) -> Tuple[int, str]:
        """→ (seq, b64url(ciphertext ‖ tag)). Caller holds send_lock while sealing and sending."""
        if self.closed:
            raise ChannelClosed()
        seq = self.send_seq
        if seq >= MAX_SEQ:
            raise ChannelClosed("re-handshake required")
        data = self._send.encrypt(iv_for(seq), pad(encode_inner(obj)), aad_for(self.ch, self.send_dir, seq))
        self.send_seq = seq + 1
        return seq, b64u(data)

    def open(self, seq, b: str):
        """Decrypt the next inbound message. Wrong seq / tag / JSON → CryptoError and a failure count; after 3
        failures the channel closes (ChannelClosed)."""
        if self.closed:
            raise ChannelClosed()
        try:
            if not isinstance(seq, int) or isinstance(seq, bool) or seq != self.recv_seq:
                raise CryptoError("unexpected seq")
            raw = unb64u(b)
            pt = self._recv.decrypt(iv_for(seq), raw, aad_for(self.ch, self.recv_dir, seq))
            obj = json.loads(unpad(pt).decode("utf-8"))
            if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
                raise CryptoError("bad inner message")
        except (CryptoError, ValueError, UnicodeDecodeError, RecursionError) as e:
            self.failures += 1
            if self.failures >= self.MAX_FAILURES:
                self.closed = True
                raise ChannelClosed(str(e)) from None
            raise CryptoError(str(e)) from None
        self.recv_seq = seq + 1
        return obj
