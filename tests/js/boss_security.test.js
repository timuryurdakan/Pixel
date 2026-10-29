// Patron Odası security review S29 (docs/SECURITY_AUDIT.md §13; headless Chrome, tests/test_relay_js.py). Written
// first, confirmed failing before the fixes.
// S29-02: a `peer.revoked` for this browser's own bid (sent by the connector before it closes the channel of a browser
//         revoked on the computer or by another browser) makes the page forget that device at once, like its own
//         "Eşleşmeyi kaldır"; a notice for another bid, or the reply to its own unpair request, changes nothing.
// S29-03: the approval before a Test order says that the tests run the specialists' code with the user's own rights,
//         not limited to the folder or the job's caps.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DeviceHub, ControlError } from '../../web/js/control.js';
import { MemoryStore } from '../../web/js/idb.js';
import { BossPanel, GATE_TEXT } from '../../web/js/boss.js';

function hubWithDevice() {
  const store = new MemoryStore();
  const hub = new DeviceHub({ roomId: 'abcdefghijklmnopqrstuvwxyz', writeKey: 'K'.repeat(32), store });
  hub.me = { bid: 'Bme000000000' };
  hub.updateRoom = () => {};                    // no relay in this test
  const d = { id: 'd1', name: 'Masaüstü', online: true, ready: true, status: 'ready', channel: { closed: false },
    agents: new Map(), watching: new Set() };
  hub.devices.set('d1', d);
  return { hub, store, d };
}

test('S29-02: peer.revoked for this browser forgets the device, fails its requests and tells the user', async () => {
  const { hub, store, d } = hubWithDevice();
  await store.put('devices', 'd1', { deviceId: 'd1' });
  const notices = [];
  let changes = 0;
  hub.on('notice', (t) => notices.push(t));
  hub.on('change', () => { changes += 1; });
  const pending = new Promise((resolve, reject) => {
    hub.pending.set('r1', { resolve, reject, timer: setTimeout(() => {}, 0), deviceId: 'd1', quiet: false });
  });
  hub._dispatch(d, { type: 'peer.revoked', bid: 'Bme000000000' });
  await assert.rejects(pending, (e) => e instanceof ControlError && e.code === 'not_paired');
  assert.equal(hub.devices.size, 0);
  assert.equal(hub.pairedCount, 0);
  assert.equal(d.channel, null);
  assert.ok(changes >= 1);
  assert.equal(notices.length, 1);
  assert.match(notices[0], /Masaüstü/);
  assert.match(notices[0], /eşleşmesi.*kaldırıldı/);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(await store.get('devices', 'd1'), undefined);
});

test('S29-02: a notice for another bid or the reply to its own unpair request changes nothing', () => {
  const { hub, d } = hubWithDevice();
  const events = [];
  hub.on('event', (dev, m) => events.push(m.type));
  hub._dispatch(d, { type: 'peer.revoked', bid: 'Bother000000' });           // another browser of the user
  assert.equal(hub.devices.size, 1);
  assert.deepEqual(events, ['peer.revoked']);
  let got = null;
  hub.pending.set('q1', { resolve: (m) => { got = m; }, reject: () => {}, timer: setTimeout(() => {}, 0), deviceId: 'd1' });
  hub._dispatch(d, { type: 'peer.revoked', bid: 'Bme000000000', re: 'q1' });  // unpair(): its own request's reply
  assert.equal(got.type, 'peer.revoked');
  assert.equal(hub.devices.size, 1);                                          // unpair() itself forgets it
});

test('S29-03: the Test order approval says what running the tests allows', () => {
  for (const words of ['sizin yetkilerinizle', 'klasörle', 'tavan']) assert.ok(GATE_TEXT.includes(words), words);
  const fake = { inflight: new Set(), _act() {} };
  const box = BossPanel.prototype._gate.call(fake, { run: 'w_RRRRRRRRRR' }, { id: 'o2' });
  assert.ok(box.textContent.includes(GATE_TEXT));
  assert.ok(box.querySelector('.boss-gate-approve') && box.querySelector('.boss-gate-deny'));
});
