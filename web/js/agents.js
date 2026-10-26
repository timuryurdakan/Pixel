// Pixel Office — Character + CharacterManager (ARCHITECTURE.md §6.3).
// Server state → target spot → A* walk → settled animation.

import { ANIMS, makeCharacterPalette } from './sprites.js';

const HEX6 = /^#[0-9a-f]{6}$/i;
/** Valid `#rrggbb` owner colour from the server (cloud mode, CLOUD.md §6.4), else null. */
export const ownerColorOf = (agent) => (agent && typeof agent.ownerColor === 'string' && HEX6.test(agent.ownerColor) ? agent.ownerColor : null);
function darkenHex(hex, amt = 0.15) {
  const n = parseInt(hex.slice(1), 16);
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.round(v * (1 - amt)));
  return '#' + c.map((v) => v.toString(16).padStart(2, '0')).join('');
}
import { TILE, DOOR } from './office.js';
import { findPath } from './pathfinding.js';

export const WALK_SPEED = 40;   // px/s (2.5 tiles/s)
export const MIN_DWELL = 2.0;   // s at a spot before re-targeting (except leaving)
export const FADE_TIME = 0.4;   // s
export const HANDOFF_GIVE_S = 1.6;   // s both characters show the paper bubble during a handoff (v4-B)
export const ERRAND_QUEUE_S = 30;    // s a queued errand (character still entering/walking) may wait before it is dropped

export const STATE_TARGET = {
  working:          { spot: 'desk',   anim: 'type',  bubble: 'tool' },
  thinking:         { spot: 'desk',   anim: 'sit',   bubble: 'dots' },
  waiting_for_user: { spot: 'desk',   anim: 'sit',   bubble: 'alert' },
  idle:             { spot: 'coffee', anim: 'drink', bubble: 'steam' },
  sleeping:         { spot: 'sleep',  anim: 'sleep', bubble: 'zzz' },
};

const targetOf = (state) => STATE_TARGET[state] || STATE_TARGET.thinking;
const tileCenter = (t) => ({ px: t.x * TILE + 8, py: t.y * TILE + 15 });
const NEIGHBOURS = [[0, 1], [-1, 0], [1, 0], [0, -1]];

export class Character {
  /**
   * @param {object} agent server JSON (§3.3)
   * @param {import('./office.js').Office} office
   */
  constructor(agent, office, { entering = true, parentPalette = null, time = 0 } = {}) {
    this.id = agent.id;
    this.agent = agent;
    this.office = office;
    this.palette = makeCharacterPalette(agent.colorSeed >>> 0);
    if (parentPalette) this.setParentPalette(parentPalette);
    this._applyOwnerColor();
    this.dir = 'down';
    this.anim = 'stand';
    this.animTime = 0;
    this.path = [];
    this.step = null;            // tile currently walked towards
    this.spot = null;
    this.settledAt = time;
    this.time = time;
    this.retargetPending = false;
    this.repath = false;
    this.lastIndex = {};         // spot kind → last regular index used
    this.teleport = null;        // {stage:'out'|'in'} when no path exists
    this.exiting = false;        // leaving: reached the door, fading out
    this.errand = null;          // v4-B handoff / Boss file walk: {goal, stage:'go'|'give', until, peer, face, bubble, onGive}
    this.queuedErrand = null;    // {goals, opts, at}: started once the character is free (just entered or settled)
    this.paperUntil = -1;        // show the paper bubble until this time (handoff)
    this.paperKind = 'paper';    // …or the Boss's folder (reduced motion: bubbles instead of the walk)
    if (entering) {
      ({ px: this.px, py: this.py } = tileCenter(DOOR));
      this.alpha = 0;
      this.dir = 'up';
      this.phase = 'entering';
    } else {
      this.alpha = 1;
      this.phase = 'settled';
      this._reserveTarget();
      ({ px: this.px, py: this.py } = tileCenter(this.spot));
      this._settle(time - MIN_DWELL);
    }
  }

  /** Subagents wear their parent's shirt colour (an owner colour takes priority, cloud mode). */
  setParentPalette(pp) {
    this.hasParentPalette = true;
    if (this.ownerColor) return;
    this.palette = { ...this.palette, shirt: pp.shirt, shirtShade: pp.shirtShade };
    this.palette.key = `${this.palette.key}|p${pp.shirt}`;
  }

  /** Cloud: shirt = the owner's colour (CLOUD.md §9.4). No-op for local agents (no ownerColor). */
  _applyOwnerColor() {
    const col = ownerColorOf(this.agent);
    if (!col || col === this.ownerColor) return;
    this.ownerColor = col;
    this.palette = { ...this.palette, shirt: col, shirtShade: darkenHex(col) };
    this.palette.key = `${this.palette.key}|o${col}`;
  }

  get tile() {
    return { x: Math.floor(this.px / TILE), y: Math.floor(this.py / TILE) };
  }

  setAgent(agent) {
    this.agent = agent;
    this._applyOwnerColor();
    if (this.phase === 'leaving' || this.phase === 'gone' || this.phase === 'entering') return;
    if (this.errand) { this.retargetPending = true; return; }    // applied when the handoff walk is over
    const kind = targetOf(agent.state).spot;
    if (this.spot && this.spot.kind === kind) {
      // Same kind of spot (e.g. working ↔ thinking): just switch the animation.
      this.retargetPending = false;
      if (this.phase === 'settled') this._setAnim(this._settledAnim());
      return;
    }
    if (this.teleport) { this.retargetPending = true; return; }
    if (this.phase === 'settled') {
      if (this.time - this.settledAt >= MIN_DWELL) this._retarget();
      else this.retargetPending = true;
    } else {
      this._retarget();
    }
  }

  /** Release spot, walk to the door, fade out, then phase 'gone'. */
  leave() {
    if (this.phase === 'leaving' || this.phase === 'gone') return;
    this.errand = null;
    this.queuedErrand = null;
    this.office.releaseSpot(this.id);
    this.spot = null;
    this.retargetPending = false;
    this.phase = 'leaving';
    this.teleport = null;
    this.alpha = Math.max(this.alpha, 0.01);
    this._planPath(DOOR, true);
  }

  /** Re-added (same id) while leaving: turn around. */
  comeBack() {
    if (this.phase !== 'leaving') return;
    this.exiting = false;
    this.phase = 'walking';
    this._retarget();
  }

  _reserveTarget(preferIndex = -1) {
    const kind = targetOf(this.agent.state).spot;
    const prefer = preferIndex >= 0 ? preferIndex : (this.lastIndex[kind] ?? -1);
    let spot = this.office.reserveSpot(kind, this.id, prefer);
    if (!spot) spot = this.office.reserveFloorSpot(kind, this.id);
    if (!spot.overflow) this.lastIndex[kind] = spot.index;
    this.spot = spot;
    return spot;
  }

  _retarget(preferIndex = -1) {
    this.retargetPending = false;
    const spot = this._reserveTarget(preferIndex);
    if (this.phase === 'settled') this.phase = 'walking';
    this._planPath(spot, false);
  }

  /**
   * Settled on an overflow (or ad-hoc floor) spot while a regular spot of the same kind is free:
   * walk over to it. Returns true if the character started moving.
   */
  tryUpgradeSpot() {
    const cur = this.spot;
    if (this.phase !== 'settled' || this.teleport || this.retargetPending || !cur || !cur.overflow) return false;
    if (this.time - this.settledAt < MIN_DWELL || cur.kind !== targetOf(this.agent.state).spot) return false;
    // Overflow → regular spot; an ad-hoc floor spot (index -1) also takes a free overflow spot.
    const free = (this.office.spots[cur.kind] || []).find((s) => !s.occupant && (!s.overflow || cur.index < 0));
    if (!free) return false;
    this._retarget(free.index);
    return true;
  }

  /**
   * v4-B: walk to a free tile next to `peer`'s desk, hand over (both show the paper bubble for HANDOFF_GIVE_S), then walk
   * back. Returns false (caller falls back to bubbles only) when this character is not settled or no path exists.
   */
  startErrand(peer) {
    const at = peer.spot || peer.tile;
    const goals = NEIGHBOURS.map(([dx, dy]) => ({ x: at.x + dx, y: at.y + dy }));
    return this.startErrandTo(goals, { peer, face: at });
  }

  /**
   * Walk to the first reachable free tile of `goals` (an array, or a function returning one when the walk starts), stand there facing `face` for HANDOFF_GIVE_S showing `bubble`
   * (`onGive(until)` is called on arrival), then go back to the own spot (or to the one the current state asks for).
   * Shared by the A9 handoff (peer's desk, paper) and the Boss's room (drop tile in front of his desk, folder).
   * Possible when settled, or right after entering (no spot yet). → false when busy or nothing is reachable.
   */
  startErrandTo(goals, { peer = null, face = null, bubble = 'paper', onGive = null } = {}) {
    if (!this._errandFree()) return false;
    const start = this.tile;
    const pass = (goal) => (x, y) => this.office.isPassable(x, y, start, goal);
    for (const goal of typeof goals === 'function' ? goals() : goals) {
      if (!this.office.isWalkable(goal.x, goal.y) || (goal.x === start.x && goal.y === start.y)) continue;
      const path = findPath(this.office.width, this.office.height, pass(goal), start, goal);
      if (!path || !path.length) continue;
      this.errand = { goal: { x: goal.x, y: goal.y }, stage: 'go', until: 0, peer, face: face || (peer && (peer.spot || peer.tile)) || goal, bubble, onGive };
      this.phase = 'walking';
      this.retargetPending = false;
      this.goal = this.errand.goal;
      this.path = path;
      this._nextStep();
      return true;
    }
    return false;
  }

  /**
   * startErrandTo now if the character is free, else once it is (entering → walking in, walking → settled).
   * A queued errand older than ERRAND_QUEUE_S is dropped. → 'walk' | 'queued' | false (leaving/gone, or no path).
   */
  queueErrandTo(goals, opts = {}) {
    if (this.phase === 'leaving' || this.phase === 'gone') return false;
    if (this._errandFree()) return this.startErrandTo(goals, opts) ? 'walk' : false;
    this.queuedErrand = { goals, opts, at: this.time };
    return 'queued';
  }

  _errandFree() {
    if (this.teleport || this.errand || this.exiting) return false;
    if (this.phase === 'settled') return !!this.spot;
    return this.phase === 'walking' && !this.spot && !this.step;   // just faded in at the door
  }

  /** Start the queued errand if the character is free; true if it started walking. */
  _runQueuedErrand() {
    const q = this.queuedErrand;
    if (!q || !this._errandFree()) return false;
    this.queuedErrand = null;
    if (this.time - q.at > ERRAND_QUEUE_S) return false;
    return this.startErrandTo(q.goals, q.opts);
  }

  _errandArrived() {
    const e = this.errand;
    e.stage = 'give';
    e.until = this.time + HANDOFF_GIVE_S;
    if (e.peer) { e.peer.paperUntil = Math.max(e.peer.paperUntil, e.until); e.peer.paperKind = 'paper'; }
    if (e.onGive) e.onGive(e.until);
    const at = e.face;
    const t = this.tile;
    this.dir = at.x > t.x ? 'right' : at.x < t.x ? 'left' : at.y < t.y ? 'up' : 'down';
    this._setAnim('stand');
  }

  _endErrand() {
    this.errand = null;
    if (this.retargetPending || !this.spot) { this._retarget(); return; }
    this._planPath(this.spot, false);
  }

  /** Path to `goal`; when mid-step, the path is computed once the current step ends. */
  _planPath(goal, leaving) {
    this.goal = { x: goal.x, y: goal.y };
    if (this.step) { this.repath = true; return; }
    this._computePath(leaving);
  }

  _computePath() {
    this.repath = false;
    const start = this.tile;
    const goal = this.goal;
    const path = findPath(this.office.width, this.office.height,
      (x, y) => this.office.isPassable(x, y, start, goal), start, goal);
    if (path === null) {
      this.path = [];
      this.teleport = { stage: 'out' };
      return;
    }
    this.path = path;
    if (!path.length) this._arrive();
    else this._nextStep();
  }

  _nextStep() {
    this.step = this.path.shift() || null;
    if (!this.step) return;
    const t = this.tile;
    const dx = this.step.x - t.x, dy = this.step.y - t.y;
    this.dir = dx > 0 ? 'right' : dx < 0 ? 'left' : dy < 0 ? 'up' : 'down';
    this._setAnim('walk');
  }

  _arrive() {
    this.step = null;
    if (this.errand && this.errand.stage === 'go') { this._errandArrived(); return; }
    if (this.phase === 'leaving') {
      this.exiting = true;
      this.dir = 'down';
      this._setAnim('stand');
      return;
    }
    if (!this.spot) return;
    ({ px: this.px, py: this.py } = tileCenter(this.spot));
    this._settle(this.time);
  }

  _settle(time) {
    this.phase = 'settled';
    this.settledAt = time;
    this.dir = this.spot.facing;
    this._setAnim(this._settledAnim());
    this._runQueuedErrand();
  }

  _settledAnim() {
    const t = targetOf(this.agent.state);
    if (this.spot && this.spot.kind === 'desk' && this.spot.overflow) {
      // Standing desk: typing while working, otherwise just standing (facing up).
      return this.agent.state === 'working' ? 'type' : 'stand';
    }
    return t.anim;
  }

  _setAnim(anim) {
    if (anim !== this.anim) { this.anim = anim; this.animTime = 0; }
  }

  update(dt, time) {
    this.time = time;
    this.animTime += dt;
    if (this.phase === 'gone') return;

    if (this.teleport) {
      if (this.teleport.stage === 'out') {
        this.alpha = Math.max(0, this.alpha - dt / FADE_TIME);
        if (this.alpha <= 0) {
          if (this.phase === 'leaving') { this.phase = 'gone'; return; }
          ({ px: this.px, py: this.py } = tileCenter(this.goal));
          this.teleport.stage = 'in';
          if (this.spot) this._settle(time);
        }
      } else {
        this.alpha = Math.min(1, this.alpha + dt / FADE_TIME);
        if (this.alpha >= 1) { this.teleport = null; this._runQueuedErrand(); }
      }
      return;
    }

    if (this.phase === 'entering') {
      this.alpha = Math.min(1, this.alpha + dt / FADE_TIME);
      if (this.alpha >= 1) {
        this.phase = 'walking';
        if (!this._runQueuedErrand()) this._retarget();
      }
      return;
    }

    if (this.exiting) {
      this.alpha = Math.max(0, this.alpha - dt / FADE_TIME);
      if (this.alpha <= 0) this.phase = 'gone';
      return;
    }

    if (this.phase === 'settled') {
      if (this.retargetPending && time - this.settledAt >= MIN_DWELL) this._retarget();
      return;
    }

    if (this.errand && this.errand.stage === 'give') {
      if (time >= this.errand.until) this._endErrand();
      return;
    }

    // Walking (or leaving): move towards the current step's tile centre.
    // (A character that turned back while fading out at the door fades in again.)
    if (this.phase === 'walking' && this.alpha < 1) this.alpha = Math.min(1, this.alpha + dt / FADE_TIME);
    let budget = WALK_SPEED * dt;
    while (budget > 0 && this.step) {
      const { px: tx, py: ty } = tileCenter(this.step);
      const dx = tx - this.px, dy = ty - this.py;
      const dist = Math.abs(dx) + Math.abs(dy); // axis-aligned movement
      if (dist <= budget) {
        this.px = tx; this.py = ty;
        budget -= dist;
        this.step = null;
        if (this.repath) this._computePath();
        else if (this.path.length) this._nextStep();
        else this._arrive();
      } else {
        this.px += Math.sign(dx) * Math.min(Math.abs(dx), budget);
        this.py += Math.sign(dy) * Math.min(Math.abs(dy), budget);
        budget = 0;
      }
    }
    if (!this.step && this.phase !== 'settled' && !this.exiting && !this.teleport && this.repath) this._computePath();
  }

  currentFrame() {
    const a = ANIMS[this.anim] || ANIMS.stand;
    return { anim: this.anim, dir: this.dir, frame: Math.floor(this.animTime * (a.fps || 0)) };
  }

  bubble() {
    if (this.teleport) return null;
    const giving = this.errand && this.errand.stage === 'give';
    const paper = giving || this.paperUntil > this.time;
    // The blinking "!" of an agent waiting for the user is never hidden (v2.md H35), not even by the handoff paper.
    if (paper && !(this.phase === 'settled' && this.agent.state === 'waiting_for_user')) {
      return { kind: giving ? this.errand.bubble : this.paperKind };
    }
    if (this.phase !== 'settled') return null;
    const t = targetOf(this.agent.state);
    if (t.bubble === 'tool') return { kind: 'tool', tool: this.agent.tool || '' };
    return { kind: t.bubble };
  }

  isSleepingOnBed() {
    return this.phase === 'settled' && !!this.spot && this.spot.kind === 'sleep' && this.anim === 'sleep';
  }

  /** y-sort key for the renderer (§6.4). */
  get sortY() {
    return this.isSleepingOnBed() ? this.spot.y * TILE + 2 : this.py;
  }
}

export class CharacterManager {
  constructor(office) {
    this.office = office;
    /** @type {Map<string, Character>} */
    this.characters = new Map();
    this.time = 0;
    this._nextUpgradeCheck = 0;
  }

  _parentPalette(agent) {
    if (!agent.isSubagent || !agent.parentId) return null;
    const p = this.characters.get(agent.parentId);
    return p ? p.palette : null;
  }

  /** Full snapshot: upsert all; missing → leave(). */
  sync(agents, { initial = false } = {}) {
    const ids = new Set(agents.map((a) => a.id));
    for (const [id, ch] of this.characters) if (!ids.has(id)) ch.leave();
    // Main sessions first so subagents can inherit the parent's shirt colour.
    const ordered = [...agents].sort((a, b) => Number(!!a.isSubagent) - Number(!!b.isSubagent));
    for (const a of ordered) this.upsert(a, { entering: !initial });
  }

  upsert(agent, { entering = true } = {}) {
    let ch = this.characters.get(agent.id);
    if (ch) {
      if (ch.phase === 'leaving') ch.comeBack();
      if (ch.phase === 'gone') { this.characters.delete(agent.id); ch = null; }
    }
    if (ch) {
      ch.setAgent(agent);
      if (!ch.hasParentPalette) {
        const pp = this._parentPalette(agent);
        if (pp) ch.setParentPalette(pp);
      }
      return ch;
    }
    ch = new Character(agent, this.office, { entering, parentPalette: this._parentPalette(agent), time: this.time });
    this.characters.set(agent.id, ch);
    // A parent that shows up after its subagents: recolour them.
    if (!agent.isSubagent) {
      for (const c of this.characters.values()) {
        if (c.agent.isSubagent && c.agent.parentId === agent.id && !c.hasParentPalette) c.setParentPalette(ch.palette);
      }
    }
    return ch;
  }

  remove(id) {
    const ch = this.characters.get(id);
    if (ch) ch.leave();
  }

  /**
   * v4-B handoff from office agent `fromId` to `toId`: A walks to B's desk and back, both hold up a paper bubble.
   * reducedMotion (or no path / A busy walking): no walk, only the bubbles. → 'walk' | 'bubbles' | null (unknown ids).
   */
  handoff(fromId, toId, { reducedMotion = false } = {}) {
    const a = this.characters.get(fromId);
    const b = this.characters.get(toId);
    if (!a || !b || a === b || a.phase === 'gone' || a.phase === 'leaving' || b.phase === 'gone' || b.phase === 'leaving') return null;
    if (!reducedMotion && a.startErrand(b)) return 'walk';
    const until = this.time + HANDOFF_GIVE_S;
    a.paperUntil = until;
    b.paperUntil = until;
    a.paperKind = 'paper';
    b.paperKind = 'paper';
    return 'bubbles';
  }

  update(dt, time) {
    this.time = time;
    for (const [id, ch] of this.characters) {
      ch.update(dt, time);
      if (ch.phase === 'gone') {
        this.office.releaseSpot(id);
        this.characters.delete(id);
      }
    }
    // A few times per second: move characters off overflow spots once a regular one frees up.
    if (time >= this._nextUpgradeCheck) {
      this._nextUpgradeCheck = time + 0.5;
      for (const ch of this.characters.values()) ch.tryUpgradeSpot();
    }
  }

  list() {
    return [...this.characters.values()];
  }
}
