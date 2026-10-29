// Patron Odası P5 (headless Chrome, tests/test_relay_js.py): the top-right Boss's room on the office map, the Boss as
// a scene character (pose from untrusted `boss.state`) and the specialists' file walks to his desk (`boss.event`).
// Real Office / CharacterManager / pathfinding; only the device hub is faked.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Office, MAP_ROWS, DOOR, SPAWN, CAT_TILE, BOARD_TILES, BOSS_ROOM, BOSS_SEAT, BOSS_DESK, BOSS_DOOR,
  BOSS_DROP_TILES, inBossRoom } from '../../web/js/office.js';
import { findPath } from '../../web/js/pathfinding.js';
import { CharacterManager, HANDOFF_GIVE_S } from '../../web/js/agents.js';
import { BossScene, readJob, bossPose, connectBossScene, IDLE_SWITCH_S, PENDING_EVENT_S } from '../../web/js/boss_scene.js';
import { BOSS_DESK_MAX_FILES } from '../../web/js/sprites.js';

const reachable = (o, from, to) => {
  const path = findPath(o.width, o.height, (x, y) => o.isPassable(x, y, from, to), from, to);
  return !!(path && path.length);
};

// ---- map ----------------------------------------------------------------------------------------------------------
test('every spot (overflow included) and every drop tile is reachable from the main door', () => {
  const o = new Office();
  assert.equal(o.tileAt(DOOR.x, DOOR.y), 'D');
  for (const list of Object.values(o.spots)) {
    for (const sp of list) assert.ok(reachable(o, SPAWN, sp), `spot ${sp.kind}@${sp.x},${sp.y} unreachable`);
  }
  for (const t of BOSS_DROP_TILES) {
    assert.ok(o.isWalkable(t.x, t.y), `drop tile ${t.x},${t.y} not walkable`);
    assert.ok(inBossRoom(t.x, t.y));
    assert.ok(reachable(o, SPAWN, t), `drop tile ${t.x},${t.y} unreachable`);
  }
});

test('no office agent gets a spot in the Boss\'s room, not even an ad-hoc floor spot', () => {
  const o = new Office();
  for (const list of Object.values(o.spots)) {
    for (const sp of list) assert.ok(!inBossRoom(sp.x, sp.y), `${sp.kind} spot @${sp.x},${sp.y} in the Boss's room`);
  }
  let i = 0;
  const rng = () => ((i++ * 0.6180339887) % 1);
  for (let n = 0; n < 500; n++) {
    const t = o.randomFloorTile(rng);
    assert.ok(!inBossRoom(t.x, t.y), `floor tile ${t.x},${t.y} in the Boss's room`);
  }
  for (let n = 0; n < 40; n++) {
    const sp = o.reserveFloorSpot('desk', `extra${n}`, rng);
    assert.ok(!inBossRoom(sp.x, sp.y));
  }
});

test('the room is walled, its only way in is its door, the seat and desk are where the renderer draws them', () => {
  const o = new Office();
  for (let y = BOSS_ROOM.y - 1; y <= BOSS_ROOM.y + BOSS_ROOM.h; y++) {
    for (let x = BOSS_ROOM.x - 1; x <= BOSS_ROOM.x + BOSS_ROOM.w; x++) {
      if (inBossRoom(x, y)) continue;
      const ch = o.tileAt(x, y);
      if (x === BOSS_DOOR.x && y === BOSS_DOOR.y) assert.equal(ch, 'd');
      else assert.ok(ch === '#' || ch === 'W', `room wall @${x},${y} is '${ch}'`);
    }
  }
  assert.equal(o.tileAt(BOSS_SEAT.x, BOSS_SEAT.y), 'L');
  assert.equal(o.isWalkable(BOSS_SEAT.x, BOSS_SEAT.y), false);
  for (let x = BOSS_DESK.x; x < BOSS_DESK.x + BOSS_DESK.w / 16; x++) {
    assert.equal(o.tileAt(x, BOSS_DESK.y), 'X');
    assert.equal(o.isWalkable(x, BOSS_DESK.y), false);
  }
  assert.equal(BOSS_DESK.y, BOSS_SEAT.y + 1);   // seat right above the desk: the Boss faces the viewer (§3.3)
  for (const t of BOSS_DROP_TILES) assert.equal(t.y, BOSS_DESK.y + 1);   // in front of the desk
  assert.equal(MAP_ROWS.length, 14);
  assert.ok(MAP_ROWS.every((r) => r.length === 22));
});

test('the cat and the Task Board are on free tiles outside the Boss\'s room', () => {
  const o = new Office();
  assert.ok(!inBossRoom(CAT_TILE.x, CAT_TILE.y));
  assert.equal(o.isWalkable(CAT_TILE.x, CAT_TILE.y), false);
  for (const list of Object.values(o.spots)) {
    for (const sp of list) assert.ok(!(sp.x === CAT_TILE.x && sp.y === CAT_TILE.y), 'cat on a spot');
  }
  for (const t of BOARD_TILES) {
    assert.ok(!inBossRoom(t.x, t.y));
    assert.equal(o.tileAt(t.x, t.y), '.');
    assert.equal(o.isWalkable(t.x, t.y), false);
  }
  const boss = o.furniture.filter((f) => ['file_cabinet', 'sofa'].includes(f.kind));
  assert.deepEqual(boss.map((f) => f.kind).sort(), ['file_cabinet', 'file_cabinet', 'sofa']);
  assert.ok(boss.every((f) => inBossRoom(f.x, f.y)));
});

// ---- Boss pose ----------------------------------------------------------------------------------------------------
test('readJob keeps only state and counts; anything malformed is dropped', () => {
  assert.equal(readJob(null), null);
  assert.equal(readJob('running'), null);
  assert.equal(readJob({ state: 'hacked' }), null);
  assert.deepEqual(readJob({ state: 'running', orders: 3, orderStates: [{ state: 'done' }, { state: 'waiting' },
    { state: 'approval' }, null, 'x'], brief: '<img src=x>' }), { state: 'running', orders: 3, waiting: 2 });
  assert.deepEqual(readJob({ state: 'approval', orders: 99 }), { state: 'approval', orders: 0, waiting: 0 });
  assert.deepEqual(readJob({ state: 'approval', orders: -1, orderStates: 'x' }), { state: 'approval', orders: 0, waiting: 0 });
  const many = Array.from({ length: 50 }, () => ({ state: 'waiting' }));
  assert.equal(readJob({ state: 'running', orders: 4, orderStates: many }).waiting, 16);
});

test('the Boss writes/reads when idle, and shows the job: plan, approval files, running files, review', () => {
  assert.equal(bossPose({ time: 0 }).anim, 'boss_write');
  assert.equal(bossPose({ time: IDLE_SWITCH_S + 0.1 }).anim, 'boss_read');
  assert.equal(bossPose({ time: 0 }).bubble, null);
  assert.equal(bossPose({ time: 0 }).files, 0);
  const planning = bossPose({ job: { state: 'planning', orders: 0, waiting: 0 } });
  assert.deepEqual([planning.anim, planning.bubble, planning.files], ['boss_write', 'dots', 0]);
  const approval = bossPose({ job: { state: 'approval', orders: 3, waiting: 3 } });
  assert.deepEqual([approval.anim, approval.bubble, approval.files], ['boss_sit', 'alert', 3]);
  const running = bossPose({ job: { state: 'running', orders: 4, waiting: 1 } });
  assert.deepEqual([running.anim, running.bubble, running.files], ['boss_write', null, 1]);
  const review = bossPose({ job: { state: 'reviewing', orders: 4, waiting: 0 } });
  assert.deepEqual([review.anim, review.bubble], ['boss_read', 'dots']);
  assert.equal(bossPose({ job: { state: 'done', orders: 4, waiting: 0 }, time: 0 }).bubble, null);   // over → idle
  assert.equal(bossPose({ job: { state: 'approval', orders: 16, waiting: 0 } }).files, BOSS_DESK_MAX_FILES);
  assert.equal(bossPose({ time: 1, giveUntil: 2 }).anim, 'boss_give');
  assert.equal(bossPose({ time: 3, giveUntil: 2 }).anim, 'boss_write');
});

test('reduced motion: the Boss only sits, files and bubbles stay', () => {
  for (const job of [null, { state: 'planning', orders: 0, waiting: 0 }, { state: 'approval', orders: 2, waiting: 2 },
    { state: 'reviewing', orders: 2, waiting: 0 }]) {
    for (const time of [0, IDLE_SWITCH_S + 1]) {
      assert.equal(bossPose({ job, time, reducedMotion: true, giveUntil: time + 1 }).anim, 'boss_sit');
    }
  }
  const p = bossPose({ job: { state: 'approval', orders: 2, waiting: 2 }, reducedMotion: true });
  assert.deepEqual([p.files, p.bubble], [2, 'alert']);
});

// ---- file walks -----------------------------------------------------------------------------------------------------
function scene({ reduced = false } = {}) {
  const office = new Office();
  const m = new CharacterManager(office);
  const ids = new Map([['d1:frontend', 'of'], ['d1:backend', 'ob']]);
  const s = new BossScene({ manager: m, resolve: (dev, key) => ids.get(`${dev}:${key}`) || null, reducedMotion: () => reduced });
  return { office, m, s };
}
const run = (m, s, seconds, t0) => {
  let t = t0;
  for (let i = 0; i < seconds * 60; i++) { t += 1 / 60; m.update(1 / 60, t); s.update(t); }
  return t;
};

test('take: the specialist walks to a drop tile with a folder, the Boss hands it over, the specialist walks back', () => {
  const { m, s } = scene();
  const a = m.upsert({ id: 'of', name: 'Frontend', state: 'working', colorSeed: 1 }, { entering: false });
  let t = run(m, s, 3, 10);
  const home = a.spot;
  assert.equal(s.onEvent('d1', { type: 'boss.event', event: 'take', agent: 'frontend' }), 'walk');
  let gave = false;
  for (let i = 0; i < 60 * 30 && !gave; i++) {
    t += 1 / 60; m.update(1 / 60, t); s.update(t);
    gave = !!(a.bubble() && a.bubble().kind === 'folder');
  }
  assert.ok(gave, 'the folder bubble showed');
  assert.ok(BOSS_DROP_TILES.some((d) => d.x === a.tile.x && d.y === a.tile.y), `stood at ${a.tile.x},${a.tile.y}`);
  assert.equal(a.dir, 'up');                                    // facing the Boss behind the desk
  assert.equal(s.pose(t).anim, 'boss_give');
  t = run(m, s, HANDOFF_GIVE_S + 30, t);
  assert.equal(a.errand, null);
  assert.equal(a.phase, 'settled');
  assert.equal(a.spot, home);
  assert.notEqual(s.pose(t).anim, 'boss_give');
});

test('two specialists at once take different drop tiles', () => {
  const { m, s } = scene();
  const a = m.upsert({ id: 'of', name: 'Frontend', state: 'working', colorSeed: 1 }, { entering: false });
  const b = m.upsert({ id: 'ob', name: 'Backend', state: 'working', colorSeed: 2 }, { entering: false });
  run(m, s, 3, 10);
  assert.equal(s.onEvent('d1', { event: 'deliver', agent: 'frontend' }), 'walk');
  assert.equal(s.onEvent('d1', { event: 'deliver', agent: 'backend' }), 'walk');
  assert.notEqual(`${a.errand.goal.x},${a.errand.goal.y}`, `${b.errand.goal.x},${b.errand.goal.y}`);
});

test('an agent still entering walks once it is in; an unknown one waits PENDING_EVENT_S, then the walk is dropped', () => {
  const { m, s } = scene();
  const a = m.upsert({ id: 'of', name: 'Frontend', state: 'working', colorSeed: 1 }, { entering: true });
  assert.equal(s.onEvent('d1', { event: 'take', agent: 'frontend' }), 'queued');
  let t = run(m, s, 1, 10);
  assert.ok(a.errand, 'walking to the Boss right after entering');

  assert.equal(s.onEvent('d1', { event: 'take', agent: 'backend' }), 'pending');
  t = run(m, s, 2, t);
  const b = m.upsert({ id: 'ob', name: 'Backend', state: 'working', colorSeed: 2 }, { entering: false });
  t = run(m, s, 1, t);
  assert.ok(b.errand || b.queuedErrand, 'the pending walk started');

  const c = scene();
  assert.equal(c.s.onEvent('d1', { event: 'take', agent: 'backend' }), 'pending');
  let t2 = run(c.m, c.s, PENDING_EVENT_S + 1, 10);
  assert.equal(c.s.pending.length, 0);
  const late = c.m.upsert({ id: 'ob', name: 'Backend', state: 'working', colorSeed: 2 }, { entering: false });
  t2 = run(c.m, c.s, 2, t2);
  assert.equal(late.errand, null);
  assert.equal(late.queuedErrand, null);
});

test('leave drops pending walks; malformed events are ignored; reduced motion only shows the folder bubble', () => {
  const { m, s } = scene();
  assert.equal(s.onEvent('d1', { event: 'take', agent: 'backend' }), 'pending');
  assert.equal(s.onEvent('d1', { event: 'leave', agent: 'backend' }), null);
  assert.equal(s.pending.length, 0);
  for (const bad of [null, 'take', {}, { event: 'take' }, { event: 'take', agent: '../x' }, { event: 'take', agent: 'a'.repeat(25) },
    { event: 'explode', agent: 'frontend' }, { event: 'take', agent: 7 }, { event: 'enter', agent: 'frontend' }]) {
    assert.equal(s.onEvent('d1', bad), null);
  }
  assert.equal(s.pending.length, 0);

  const r = scene({ reduced: true });
  const a = r.m.upsert({ id: 'of', name: 'Frontend', state: 'working', colorSeed: 1 }, { entering: false });
  let t = run(r.m, r.s, 3, 10);
  const pos = [a.px, a.py];
  assert.equal(r.s.onEvent('d1', { event: 'take', agent: 'frontend' }), 'bubbles');
  t = run(r.m, r.s, 0.1, t);
  assert.equal(a.bubble().kind, 'folder');
  assert.deepEqual([a.px, a.py], pos);
  run(r.m, r.s, HANDOFF_GIVE_S + 0.5, t);
  assert.notEqual((a.bubble() || {}).kind, 'folder');
});

test('the shown job is the latest active one; a device that goes away is forgotten', async () => {
  const { s } = scene();
  const listeners = new Map();
  const devices = new Map([['d1', { id: 'd1', ready: true, info: { features: ['boss'] } }],
    ['d2', { id: 'd2', ready: true, info: { features: [] } }]]);
  const asked = [];
  const hub = {
    devices,
    on(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
    emit(t, ...a) { for (const fn of listeners.get(t) || []) fn(...a); },
    async request(dev, msg) { asked.push([dev, msg.type]); return { type: 'boss.get.result', job: { state: 'approval', orders: 2 } }; },
  };
  connectBossScene(s, hub);
  assert.deepEqual(asked, [['d1', 'boss.get']]);               // only devices with the "boss" feature
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(s.job().state, 'approval');
  hub.emit('event', 'd1', { type: 'boss.state', state: 'running', orders: 2, orderStates: [{ state: 'waiting' }] });
  assert.deepEqual(s.job(), { state: 'running', orders: 2, waiting: 1 });
  hub.emit('event', 'd1', { type: 'boss.state', state: 'running', re: 'x' });   // a reply, not an event
  hub.emit('event', 'd1', { type: 'boss.state', state: 'done', orders: 2 });
  assert.equal(s.job(), null);
  hub.emit('event', 'd1', { type: 'boss.state', state: 'planning' });
  devices.get('d1').ready = false;
  hub.emit('change');
  assert.equal(s.job(), null);
  assert.equal(s.jobs.size, 0);
});
