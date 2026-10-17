// node:test unit tests for the relay's pure logic (V3.md §17.1). Run by Workers Builds (`npm test`) before every
// deploy; a failure blocks the deploy. Locally (no Node) tests/test_relay_js.py runs this same file in headless
// Chrome with tiny node:test / node:assert shims mapped in through an import map.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vectors from './ticket_vectors.json' with { type: 'json' };
import {
  MAX_B_BROWSER, MAX_B_DEVICE, TicketError, TokenBucket, b64urlDecode, b64urlEncode, matchRoomPath, mintTicket,
  originAllowed, parseOrigins, randomConn, ticketFromProtocols, utf8Length, validateFrame, verifyTicket, errFrame,
} from '../src/lib.js';

const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, '0')).join('');
const unhex = (h) => new Uint8Array((h.match(/../g) || []).map((x) => parseInt(x, 16)));

// -- shared ticket vectors (byte-for-byte with pixel_cloud/relay.py) ------------------------------------------

test('vectors: mint is byte-identical to Python', async () => {
  for (const m of vectors.mint) {
    assert.equal(await mintTicket(vectors.secret, m.claims), m.ticket);
  }
});

test('vectors: verify gives the same result and reason as Python', async () => {
  for (const c of vectors.verify) {
    if (c.ok) {
      assert.deepEqual(await verifyTicket(vectors.secret, c.ticket, c.now, c.room), c.claims, c.name);
    } else {
      await assert.rejects(verifyTicket(vectors.secret, c.ticket, c.now, c.room), (e) => {
        assert.ok(e instanceof TicketError, c.name);
        assert.equal(e.reason, c.reason, c.name);
        return true;
      });
    }
  }
});

test('vectors: protocol header parsing', () => {
  for (const p of vectors.protocols) assert.equal(ticketFromProtocols(p.header), p.ticket, p.header.slice(0, 40));
  assert.equal(ticketFromProtocols(null), null);
});

test('vectors: base64url', () => {
  for (const v of vectors.b64url) {
    assert.equal(b64urlEncode(unhex(v.hex)), v.b64);
    assert.equal(hex(b64urlDecode(v.b64)), v.hex);
  }
  for (const bad of ['A', 'AA==', 'A+', 'A/', 'AB', 'AAB']) {   // AB / AAB: non-canonical trailing bits
    assert.throws(() => b64urlDecode(bad), undefined, bad);
  }
});

test('verify: empty secret and non-string ticket are format errors', async () => {
  await assert.rejects(verifyTicket('', vectors.mint[0].ticket, 0), (e) => e.reason === 'format');
  await assert.rejects(verifyTicket(vectors.secret, 42, 0), (e) => e.reason === 'format');
});

// -- origins, paths, ids --------------------------------------------------------------------------------------

test('origins', () => {
  const allowed = parseOrigins(' https://a.example/ ,https://b.example,, ');
  assert.deepEqual(allowed, ['https://a.example', 'https://b.example']);
  assert.ok(originAllowed('browser', 'https://a.example', allowed));
  assert.ok(!originAllowed('browser', null, allowed));
  assert.ok(!originAllowed('browser', 'https://evil.example', allowed));
  assert.ok(!originAllowed('browser', 'https://a.example.evil.example', allowed));
  assert.ok(originAllowed('device', null, allowed));
  assert.ok(!originAllowed('device', 'https://a.example', allowed));
});

test('room path', () => {
  const rid = 'abcdefghijklmnopqrstuvwxyz';
  assert.equal(matchRoomPath(`/v1/rooms/${rid}/ws`), rid);
  for (const p of ['/v1/rooms/short/ws', `/v1/rooms/${rid}/ws/`, `/v1/rooms/${rid.toUpperCase()}/ws`, '/health', `/v1/rooms/${rid}1/ws`]) {
    assert.equal(matchRoomPath(p), null, p);
  }
});

test('conn ids', () => {
  const seen = new Set();
  for (let i = 0; i < 50; i++) {
    const c = randomConn();
    assert.match(c, /^c:[A-Za-z0-9_-]{12}$/);
    seen.add(c);
  }
  assert.equal(seen.size, 50);
});

test('utf8Length', () => {
  assert.equal(utf8Length('abc'), 3);
  assert.equal(utf8Length('çğ'), 4);
  assert.equal(utf8Length('€'), 3);
  assert.equal(utf8Length('😀'), 4);
});

// -- frame validation per role (every t, size limits, `to` formats) ----------------------------------------

const DEV = 'abcdefghijklmnop';
const CONN = 'c:AAAAAAAAAAAA';
const v = (obj, role) => validateFrame(typeof obj === 'string' ? obj : JSON.stringify(obj), role);
const send = (extra) => ({ t: 'send', to: DEV, ch: null, seq: 1, k: 'e', b: 'AAAA', ...extra });

test('frames: hello', () => {
  assert.deepEqual(v({ t: 'hello', v: 1, app: 'web/3.0.0', caps: ['e2e1'] }, 'browser'),
    { ok: true, frame: { t: 'hello', v: 1, app: 'web/3.0.0', caps: ['e2e1'] } });
  assert.equal(v({ t: 'hello', v: 1 }, 'device').ok, true);
  assert.equal(v({ t: 'hello', v: 2 }, 'device').code, 'version');
  assert.equal(v({ t: 'hello', v: true }, 'device').code, 'version');
  assert.equal(v({ t: 'hello', v: 1, app: 'x'.repeat(41) }, 'device').code, 'bad_frame');
  assert.equal(v({ t: 'hello', v: 1, app: 'tab\there' }, 'device').code, 'bad_frame');
  assert.equal(v({ t: 'hello', v: 1, caps: 'e2e1' }, 'device').code, 'bad_frame');
});

test('frames: send targets by role', () => {
  assert.equal(v(send(), 'browser').ok, true);
  assert.deepEqual(v(send(), 'browser').frame.target, { kind: 'device', id: DEV });
  assert.deepEqual(v(send({ to: 'pair:K7Q2', k: 'hs' }), 'browser').frame.target, { kind: 'pair', id: 'K7Q2' });
  assert.deepEqual(v(send({ to: CONN, ch: 'A'.repeat(16) }), 'device').frame.target, { kind: 'conn', id: CONN });
  assert.deepEqual(v(send({ to: CONN }), 'browser'), { ok: false, code: 'forbidden', ref: 1 });
  assert.deepEqual(v(send(), 'device'), { ok: false, code: 'forbidden', ref: 1 });
  assert.deepEqual(v(send({ to: 'pair:K7Q2' }), 'device'), { ok: false, code: 'forbidden', ref: 1 });
  for (const to of ['DEVICE', 'pair:K7QI', 'pair:k7q2', 'c:short', 'abcdefghijklmno1', 5]) {
    assert.equal(v(send({ to }), 'browser').code, 'bad_frame', String(to));
  }
});

test('frames: send fields', () => {
  for (const extra of [{ seq: -1 }, { seq: 1.5 }, { seq: '1' }, { seq: true }, { seq: 2 ** 53 }, { k: 'x' }, { b: '' },
    { b: 'A' }, { b: 'AA==' }, { b: 'A+B/' }, { b: 5 }, { ch: 'short' }, { ch: 5 }]) {
    assert.equal(v(send(extra), 'browser').code, 'bad_frame', JSON.stringify(extra));
  }
  const noSeq = send();
  delete noSeq.seq;
  assert.deepEqual(v(noSeq, 'browser'), { ok: false, code: 'bad_frame', ref: null });
  assert.equal(v(send({ seq: 0 }), 'browser').ok, true);
  const noCh = send();
  delete noCh.ch;
  assert.equal(v(noCh, 'browser').frame.ch, null);
  assert.equal(v(send({ k: 'hs' }), 'browser').ok, true);
});

test('frames: b size limits per role', () => {
  const b48 = b64urlEncode(new Uint8Array(MAX_B_BROWSER));
  const b48p = b64urlEncode(new Uint8Array(MAX_B_BROWSER + 1));
  const b60 = b64urlEncode(new Uint8Array(MAX_B_DEVICE));
  const b60p = b64urlEncode(new Uint8Array(MAX_B_DEVICE + 1));
  assert.equal(v(send({ b: b48 }), 'browser').ok, true);
  assert.deepEqual(v(send({ b: b48p, seq: 4 }), 'browser'), { ok: false, code: 'too_large', ref: 4 });
  assert.equal(v(send({ to: CONN, b: b60 }), 'device').ok, true);
  assert.equal(v(send({ to: CONN, b: b60p }), 'device').code, 'too_large');
});

test('frames: pairing, presence, ping, junk', () => {
  assert.deepEqual(v({ t: 'pair.open', slot: 'K7Q2' }, 'device'), { ok: true, frame: { t: 'pair.open', slot: 'K7Q2' } });
  assert.equal(v({ t: 'pair.close', slot: 'K7Q2' }, 'device').ok, true);
  assert.equal(v({ t: 'pair.open', slot: 'K7Q2' }, 'browser').code, 'forbidden');
  assert.equal(v({ t: 'pair.open', slot: 'k7q2' }, 'device').code, 'bad_frame');
  assert.equal(v({ t: 'pair.open', slot: 'ILOU' }, 'device').code, 'bad_frame');
  assert.equal(v({ t: 'presence.get' }, 'browser').ok, true);
  assert.equal(v({ t: 'presence.get' }, 'device').code, 'forbidden');
  assert.equal(v({ t: 'ping' }, 'device').ok, true);
  for (const junk of ['not json', '[1]', 'null', '"x"', '{"t":5}', '{}', '{"t":"nope"}', '{"t":"msg"}']) {
    assert.equal(v(junk, 'browser').code, 'bad_frame', junk);
  }
});

test('err frame shape', () => {
  assert.deepEqual(JSON.parse(errFrame('device_offline', 3)),
    { t: 'err', code: 'device_offline', ref: 3, message: 'Device is not connected' });
});

// -- token bucket ---------------------------------------------------------------------------------------------

test('token bucket: burst, refill, cap', () => {
  const b = new TokenBucket(5, 30, 0);
  let n = 0;
  while (b.take(0)) n++;
  assert.equal(n, 30);
  assert.equal(b.take(100), false);       // 0.5 token
  assert.equal(b.take(200), true);        // 1 token after 200 ms
  assert.equal(b.take(200), false);
  n = 0;
  while (b.take(3_600_000)) n++;
  assert.equal(n, 30);                    // never above burst
  const slow = new TokenBucket(3 / 60, 3, 0);
  assert.ok(slow.take(0) && slow.take(0) && slow.take(0));
  assert.equal(slow.take(19_000), false);
  assert.equal(slow.take(20_001), true);
  const back = new TokenBucket(1, 1, 1000);
  assert.ok(back.take(1000));
  assert.equal(back.take(500), false);    // a clock going backwards never mints tokens
});
