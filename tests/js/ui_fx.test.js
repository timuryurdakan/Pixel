// Phase 20 / Package 3 (v2.md H2, H3, H15, H33, H35, E2): office effects, sound, picking and relay error texts.
// Runs in headless Chrome through tests/test_relay_js.py (fresh profile: empty localStorage, no user gesture).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ParticleSystem, LightingEngine, AgentReactions, MAX_PARTICLES, STEP_DUST_PER_S } from '../../web/js/fx.js';
import { sound } from '../../web/js/audio.js';
import { Office, CAT_TILE, SPAWN, TILE } from '../../web/js/office.js';
import { findPath } from '../../web/js/pathfinding.js';
import { Renderer, CAT_ID, bubbleWithEmote } from '../../web/js/renderer.js';
import { DeviceHub, ControlError, PAIR_ERROR_TEXT, relayErrorCode } from '../../web/js/control.js';
import { relayStatusText } from '../../web/js/devices.js';
import { RelayClient, ticketErrorCode } from '../../web/js/relay.js';
import { MemoryStore } from '../../web/js/idb.js';
import { replaceMarkup } from '../../web/js/ui.js';

/** Deterministic random numbers in [0, 1). */
function lcg(seed = 7) {
  let x = seed >>> 0;
  return () => { x = (Math.imul(x, 1664525) + 1013904223) >>> 0; return x / 2 ** 32; };
}

test('sound is muted by default and never creates an AudioContext without a user gesture', () => {
  assert.equal(sound.muted, true);
  assert.equal(navigator.userActivation.isActive, false);
  sound.setMuted(false);
  try {
    sound.playBlip();
    sound.playHop();
    sound.playMeow();
    assert.equal(sound._ctx, null);
  } finally {
    sound.setMuted(true);
    localStorage.removeItem('po-audio-muted');
  }
});

test('reduced motion: no particles, no click reaction, no lighting', () => {
  const ps = new ParticleSystem({ isReduced: () => true, random: lcg() });
  for (let i = 0; i < 100; i++) { ps.spawnStepDust(10, 10); ps.spawnCodeSparkle(10, 10); }
  assert.equal(ps.activeCount, 0);
  assert.equal(ps.chance(1000, 1), false);
  const r = new AgentReactions({ isReduced: () => true });
  r.trigger('a');
  assert.equal(r.getEmote('a'), null);
  const calls = [];
  const ctx = new Proxy({}, { get: (_, k) => (...a) => calls.push(k) });
  const light = new LightingEngine({ isReduced: () => true });
  light.mode = 'night';
  light.drawMonitorGlow(ctx, 2, 3);
  assert.equal(calls.length, 0);
});

test('particle pool is capped', () => {
  const ps = new ParticleSystem({ isReduced: () => false, random: lcg() });
  for (let i = 0; i < 500; i++) ps.spawnStepDust(10, 10);
  assert.equal(ps.activeCount, MAX_PARTICLES);
  assert.equal(MAX_PARTICLES, 48);
  ps.update(1);
  assert.equal(ps.activeCount, 0);
});

test('particle spawning depends on time, not on the frame rate', () => {
  const spawnsPerSecond = (hz) => {
    const ps = new ParticleSystem({ isReduced: () => false, random: lcg(11) });
    let n = 0;
    for (let i = 0; i < hz * 60; i++) if (ps.chance(STEP_DUST_PER_S, 1 / hz)) n++;
    return n / 60;
  };
  for (const hz of [60, 120, 144]) {
    const rate = spawnsPerSecond(hz);
    assert.ok(Math.abs(rate - STEP_DUST_PER_S) < STEP_DUST_PER_S * 0.1, `${hz} Hz: ${rate}/s`);
  }
});

test('monitor glow gradients are built once and reused', () => {
  let made = 0;
  const grad = { addColorStop() {} };
  const ctx = {
    save() {}, restore() {}, beginPath() {}, arc() {}, fill() {}, fillRect() {},
    createRadialGradient() { made++; return grad; },
  };
  const light = new LightingEngine({ isReduced: () => false });
  light.mode = 'night';
  for (let i = 0; i < 100; i++) { light.drawMonitorGlow(ctx, 2, 3); light.drawMonitorGlow(ctx, 6, 3); }
  assert.equal(made, 2);
  assert.equal(ctx.fillStyle, grad);
});

test('the cat lies on a rug tile nobody walks on, and every spot is still reachable', () => {
  const o = new Office();
  assert.equal(o.tileAt(CAT_TILE.x, CAT_TILE.y), '=');
  assert.equal(o.isWalkable(CAT_TILE.x, CAT_TILE.y), false);
  assert.equal(o.isPassable(CAT_TILE.x, CAT_TILE.y, CAT_TILE, CAT_TILE), false);
  for (const list of Object.values(o.spots)) {
    for (const sp of list) {
      assert.ok(!(sp.x === CAT_TILE.x && sp.y === CAT_TILE.y), 'cat on a spot');
      const path = findPath(o.width, o.height, (x, y) => o.isPassable(x, y, SPAWN, sp), SPAWN, sp);
      assert.ok(path && path.length > 0, `spot ${sp.kind}@${sp.x},${sp.y} unreachable`);
    }
  }
  for (const f of o.furniture) {
    const w = Math.ceil(f.w / TILE);
    assert.ok(!(CAT_TILE.y === f.y && CAT_TILE.x >= f.x && CAT_TILE.x < f.x + w), `cat on ${f.kind}`);
  }
});

test('clicking an agent in front of the cat picks the agent; the cat only on its own', () => {
  const office = new Office();
  const canvas = document.createElement('canvas');
  document.body.appendChild(canvas);
  try {
    const r = new Renderer(canvas, office);
    canvas.style.width = `${r.worldW}px`;
    canvas.style.height = `${r.worldH}px`;
    const cx = r.cat.x + 8, cy = r.cat.y + 8;
    const agent = { id: 'a1', alpha: 1, phase: 'settled', px: cx, py: r.cat.y + 16, sortY: r.cat.y + 16,
      isSleepingOnBed: () => false };
    assert.equal(r.pickCharacter([agent], cx, cy), 'a1');
    assert.equal(r.pickCharacter([], cx, cy), CAT_ID);
    assert.equal(r.pickCharacter([], 2, 2), null);
  } finally {
    canvas.remove();
  }
});

test('a click emote never hides the waiting "!"', () => {
  const alert = { kind: 'alert' };
  assert.equal(bubbleWithEmote(alert, 'heart'), alert);
  assert.deepEqual(bubbleWithEmote({ kind: 'tool', tool: 'Bash' }, 'heart'), { kind: 'heart' });
  assert.deepEqual(bubbleWithEmote(null, 'happy'), { kind: 'happy' });
  const z = { kind: 'zzz' };
  assert.equal(bubbleWithEmote(z, null), z);
});

test('relay ticket errors get their own code and text', () => {
  assert.equal(ticketErrorCode(401, { error: 'unauthorized' }), 'unauthorized');
  assert.equal(ticketErrorCode(401, null), 'unauthorized');
  assert.equal(ticketErrorCode(503, { error: 'relay_not_configured' }), 'relay_not_configured');
  assert.equal(ticketErrorCode(503, { error: '<b>x</b>' }), 'ticket');
  assert.equal(relayErrorCode('unauthorized'), 'unauthorized');
  assert.equal(relayErrorCode('relay_not_configured'), 'relay_not_configured');
  assert.equal(relayErrorCode('csp'), 'relay_csp');
  assert.equal(relayErrorCode('not_found'), 'relay_ticket');
  assert.equal(relayErrorCode(null), 'relay');
  assert.match(PAIR_ERROR_TEXT.unauthorized, /oda anahtarı geçersiz.*katılım bağlantısını/);
  assert.equal(PAIR_ERROR_TEXT.relay_not_configured, 'Sunucuda röle ayarlı değil.');
  assert.equal(PAIR_ERROR_TEXT.relay, 'Röleye bağlanılamadı. İnternet bağlantınızı kontrol edin.');
  assert.match(relayStatusText('idle', 'unauthorized'), /oda anahtarı geçersiz/);
  assert.equal(relayStatusText('idle', 'relay_not_configured'), 'Röle: sunucuda röle ayarlı değil');
  assert.equal(relayStatusText('idle', null), 'Röle: bağlı değil');
  assert.equal(relayStatusText('live', 'unauthorized'), 'Röle: bağlı');
  assert.equal(relayStatusText('weird', null), 'Röle: bağlı değil');
});

test('a refused ticket stops the relay client with the reason', async () => {
  for (const [status, body, want] of [[401, { error: 'unauthorized' }, 'unauthorized'],
    [503, { error: 'relay_not_configured' }, 'relay_not_configured']]) {
    const details = [];
    const c = new RelayClient({ roomId: 'abcdefghijklmnopqrstuvwxyz', writeKey: 'K'.repeat(32), bid: 'browseraaaaaaaa4',
      doc: null, fetchImpl: async () => ({ status, json: async () => body }),
      onStatus: (s, d) => details.push([s, d || null]) });
    await c.start();
    assert.equal(c.status, 'idle');
    assert.equal(c.lastError, want);
    assert.deepEqual(details[details.length - 1], ['idle', { error: want }]);
  }
});

test('pairing does not wait 15 s when the relay gives up', async () => {
  const hub = new DeviceHub({ roomId: 'abcdefghijklmnopqrstuvwxyz', writeKey: 'K'.repeat(32), store: new MemoryStore() });
  hub.relay = { status: 'connecting' };
  for (const [error, code] of [['unauthorized', 'unauthorized'], ['relay_not_configured', 'relay_not_configured'],
    ['csp', 'relay_csp'], ['not_found', 'relay_ticket']]) {
    const t0 = performance.now();
    const wait = hub._waitLive(15000);
    hub._onStatus('connecting');
    hub._onStatus('idle', { error });
    await assert.rejects(wait, (e) => e instanceof ControlError && e.code === code);
    assert.ok(performance.now() - t0 < 1000);
    assert.equal(hub.relayError, error);
  }
  const quota = hub._waitLive(15000);
  hub._onStatus('quota');
  await assert.rejects(quota, (e) => e.code === 'relay');
  const live = hub._waitLive(15000);
  hub._onStatus('live');
  await live;
  assert.equal(hub.relayError, null);
});

test('re-rendering markup keeps the keyboard focus and skips unchanged markup', () => {
  const ul = document.createElement('ul');
  document.body.appendChild(ul);
  try {
    const rows = (t) => ['a', 'b', 'c'].map((id) => `<li data-id="${id}" tabindex="0">${id}${t}</li>`).join('');
    replaceMarkup(ul, rows(1));
    ul.querySelector('[data-id="b"]').focus();
    replaceMarkup(ul, rows(2));
    assert.equal(document.activeElement.dataset.id, 'b');
    assert.equal(document.activeElement.textContent, 'b2');
    const same = ul.firstChild;
    replaceMarkup(ul, rows(2));
    assert.equal(ul.firstChild, same);
  } finally {
    ul.remove();
  }
});
