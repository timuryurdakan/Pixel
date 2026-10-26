// Pixel Office — the Boss on stage (docs/PATRON_ODASI.md §9 P5).
// A scene character like the cat: drawn by the browser, never reported to the cloud room. Every viewer sees him at
// his desk with his idle animations; a paired browser also gets `boss.state` / `boss.event` (E2E, ids and numbers
// only, V3.md §20) and shows the job's general state: planning → writes, approval → "!" and the plan's files on the
// desk, running → the files still waiting, review → reads. `boss.event` take/deliver sends the specialist to the drop
// tile in front of his desk with a folder (the A9 errand walk) and back. No text is ever drawn for him.

import { BOSS_DROP_TILES, BOSS_SEAT } from './office.js';
import { HANDOFF_GIVE_S } from './agents.js';
import { BOSS_ANIMS, BOSS_DESK_MAX_FILES } from './sprites.js';

export const BOSS_ID = 'boss';
export const BOSS_FEATURE = 'boss';
export const IDLE_SWITCH_S = 8;        // idle: boss_write ↔ boss_read every 8 s
export const PENDING_EVENT_S = 30;     // a take/deliver for an agent not (yet) in the office waits this long

const JOB_STATES = new Set(['idle', 'planning', 'approval', 'approved', 'running', 'reviewing', 'reporting', 'done',
  'error', 'cancelled']);
const ACTIVE_STATES = new Set(['planning', 'approval', 'approved', 'running', 'reviewing', 'reporting']);
const WAITING_ORDER = new Set(['waiting', 'approval']);    // order not taken yet: its file is still on the desk
const WALK_EVENTS = new Set(['take', 'deliver']);
const BOSS_EVENTS = new Set(['enter', 'take', 'deliver', 'leave']);
const AGENT_KEY = /^[A-Za-z0-9_-]{1,24}$/;                 // pixel_connector/agents.py KEY_RE
const MAX_ORDERS = 16;

const clampFiles = (n) => Math.max(0, Math.min(BOSS_DESK_MAX_FILES, Math.floor(n) || 0));

/**
 * Untrusted `boss.state` (or `boss.get.result.job`) → {state, orders, waiting} or null. Only the state, the plan's
 * order count and how many orders still wait are kept; anything malformed is dropped.
 */
export function readJob(m) {
  if (!m || typeof m !== 'object' || !JOB_STATES.has(m.state)) return null;
  const orders = Number.isInteger(m.orders) && m.orders >= 0 && m.orders <= MAX_ORDERS ? m.orders : 0;
  const list = Array.isArray(m.orderStates) ? m.orderStates.slice(0, MAX_ORDERS) : [];
  const waiting = list.filter((o) => o && typeof o === 'object' && WAITING_ORDER.has(o.state)).length;
  return { state: m.state, orders, waiting };
}

/**
 * What the Boss shows: {anim, frame, files, bubble}. `job` from readJob (null = viewer / no job: idle).
 * Reduced motion: always `boss_sit` (files and bubbles stay).
 */
export function bossPose({ job = null, time = 0, reducedMotion = false, giveUntil = -1 } = {}) {
  const st = job && ACTIVE_STATES.has(job.state) ? job.state : 'idle';
  let anim;
  let files = 0;
  let bubble = null;
  if (st === 'planning') { anim = 'boss_write'; bubble = 'dots'; }
  else if (st === 'approval') { anim = 'boss_sit'; files = job.orders; bubble = 'alert'; }
  else if (st === 'approved' || st === 'running') { anim = 'boss_write'; files = job.waiting; }
  else if (st === 'reviewing' || st === 'reporting') { anim = 'boss_read'; bubble = 'dots'; }
  else anim = Math.floor(time / IDLE_SWITCH_S) % 2 === 0 ? 'boss_write' : 'boss_read';
  if (giveUntil > time) anim = 'boss_give';
  if (reducedMotion) anim = 'boss_sit';
  const a = BOSS_ANIMS[anim];
  return { anim, frame: Math.floor(time * (a.fps || 0)), files: clampFiles(files), bubble };
}

/** The Boss's state per paired device and the specialists' walks to his desk. */
export class BossScene {
  /**
   * @param {object} o
   * @param {import('./agents.js').CharacterManager} o.manager
   * @param {(device:string, agentKey:string) => string|null} o.resolve   office agent id of a device's agent
   * @param {() => boolean} [o.reducedMotion]
   */
  constructor({ manager, resolve, reducedMotion = () => false }) {
    this.manager = manager;
    this.resolve = resolve;
    this.reducedMotion = reducedMotion;
    this.jobs = new Map();      // device → {job, at}
    this.pending = [];          // [{device, key, at}] take/deliver for agents not in the office yet
    this.giveUntil = -1;
    this.time = 0;
    this._seq = 0;
  }

  setJob(device, raw) {
    const job = readJob(raw);
    if (job) this.jobs.set(device, { job, at: ++this._seq });
    else this.jobs.delete(device);
  }

  clear(device) {
    this.jobs.delete(device);
    this.pending = this.pending.filter((p) => p.device !== device);
  }

  /** The job shown: the most recently updated active one, else null (idle). */
  job() {
    let best = null;
    for (const v of this.jobs.values()) {
      if (ACTIVE_STATES.has(v.job.state) && (!best || v.at > best.at)) best = v;
    }
    return best ? best.job : null;
  }

  /**
   * `boss.event` (untrusted). take/deliver → the specialist walks to the Boss and back ('walk' | 'queued' |
   * 'bubbles' | 'pending' when the agent is not in the office yet). enter/leave: the office snapshot already brings
   * the agent in through the main door and takes it out again; a leave drops its pending walks. → null if ignored.
   */
  onEvent(device, m) {
    if (!m || typeof m !== 'object' || !BOSS_EVENTS.has(m.event) || typeof m.agent !== 'string'
      || !AGENT_KEY.test(m.agent)) return null;
    if (m.event === 'leave') {
      this.pending = this.pending.filter((p) => !(p.device === device && p.key === m.agent));
      return null;
    }
    if (!WALK_EVENTS.has(m.event)) return null;
    const ch = this._character(device, m.agent);
    if (!ch) {
      this.pending.push({ device, key: m.agent, at: this.time });
      return 'pending';
    }
    return this._send(ch);
  }

  _character(device, key) {
    const id = this.resolve(device, key);
    const ch = id ? this.manager.characters.get(id) : null;
    return ch && ch.phase !== 'gone' && ch.phase !== 'leaving' ? ch : null;
  }

  /** Free drop tiles first (another specialist may stand at one), the busy ones last. */
  _dropTiles(self) {
    const busy = new Set();
    for (const c of this.manager.characters.values()) {
      if (c !== self && c.errand) busy.add(`${c.errand.goal.x},${c.errand.goal.y}`);
    }
    const free = BOSS_DROP_TILES.filter((t) => !busy.has(`${t.x},${t.y}`));
    return free.length ? free : [...BOSS_DROP_TILES];
  }

  _send(ch) {
    const onGive = (until) => { this.giveUntil = Math.max(this.giveUntil, until); };
    if (!this.reducedMotion()) {
      const r = ch.queueErrandTo(() => this._dropTiles(ch), { face: BOSS_SEAT, bubble: 'folder', onGive });
      if (r) return r;
    }
    const until = this.time + HANDOFF_GIVE_S;
    ch.paperUntil = until;
    ch.paperKind = 'folder';
    return 'bubbles';
  }

  /** Every frame (game time): retry the walks of agents that entered meanwhile, drop the stale ones. */
  update(time) {
    this.time = time;
    if (!this.pending.length) return;
    const keep = [];
    for (const p of this.pending) {
      if (time - p.at > PENDING_EVENT_S) continue;
      const ch = this._character(p.device, p.key);
      if (ch) this._send(ch);
      else keep.push(p);
    }
    this.pending = keep;
  }

  pose(time = this.time) {
    return bossPose({ job: this.job(), time, reducedMotion: this.reducedMotion(), giveUntil: this.giveUntil });
  }
}

const supportsBoss = (d) => !!(d && d.ready && d.info && Array.isArray(d.info.features) && d.info.features.includes(BOSS_FEATURE));

/**
 * Feed a BossScene from the device hub: `boss.state` events, `boss.event`, and one `boss.get` per connection of a
 * ready device whose connector has the "boss" feature (state after a reload / reconnect). Devices that went away or
 * stopped being ready are forgotten.
 */
export function connectBossScene(scene, hub) {
  const loaded = new Set();
  const load = (dev) => {
    loaded.add(dev);
    hub.request(dev, { type: 'boss.get' }, { quiet: true })
      .then((r) => { if (loaded.has(dev)) scene.setJob(dev, r && r.job); })
      .catch(() => loaded.delete(dev));
  };
  const sweep = () => {
    for (const id of [...scene.jobs.keys(), ...loaded]) {
      const d = hub.devices.get(id);
      if (!d || !d.ready) { loaded.delete(id); scene.clear(id); }
    }
    for (const d of hub.devices.values()) if (supportsBoss(d) && !loaded.has(d.id)) load(d.id);
  };
  hub.on('event', (dev, m) => {
    if (!m || m.re) return;
    if (m.type === 'boss.state') scene.setJob(dev, m);
    else if (m.type === 'boss.event') scene.onEvent(dev, m);
  });
  hub.on('change', sweep);
  hub.on('resync', (dev) => { loaded.delete(dev); sweep(); });
  sweep();
}
