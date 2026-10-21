// Pixel Office — FX, Ambient Lighting and Particle System (web/js/fx.js)
// Follows Clean Code & AGENTS.md rules: SRP, KISS, zero memory leaks, bounded arrays.

import { TILE } from './office.js';

/** Maximum number of active particles to guarantee constant memory & 60fps. */
export const MAX_PARTICLES = 48;

/** Particle spawn rates per second and character (were 0.22 / 0.08 per frame at 60 Hz). */
export const STEP_DUST_PER_S = 13.2;
export const CODE_SPARKLE_PER_S = 4.8;

/** How often the day/sunset/night phase is re-read from the clock. */
const PHASE_RECHECK_MS = 30000;

const DUST_COLORS = ['#d29a68', '#c68b59', '#e8cbb0'];
const SPARK_COLORS = ['#8fd3ff', '#ffd84a', '#4caf50', '#ffffff'];
const STAR_ON_1 = 'rgba(255, 255, 255, 0.9)';
const STAR_OFF_1 = 'rgba(255, 255, 255, 0.2)';
const STAR_ON_2 = 'rgba(210, 230, 255, 0.8)';
const STAR_OFF_2 = 'rgba(210, 230, 255, 0.1)';
const GLOW_COLOR = { night: 'rgba(76, 175, 80, 0.22)', sunset: 'rgba(255, 215, 64, 0.18)' };
const GLOW_RADIUS = 14;

// "Reduce motion": one MediaQueryList, read once and updated by its change event (v2.md H15: it used to be
// queried several times per frame).
let reducedMql;
let reducedMotion = false;

/** Current prefers-reduced-motion value (cached; follows changes of the OS setting). */
export function prefersReduced() {
  if (reducedMql === undefined) {
    reducedMql = typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    reducedMotion = !!(reducedMql && reducedMql.matches);
    if (reducedMql && reducedMql.addEventListener) {
      reducedMql.addEventListener('change', (e) => { reducedMotion = e.matches; });
    }
  }
  return reducedMotion;
}

// ---------------------------------------------------------------------------
// Particle System
// ---------------------------------------------------------------------------

class Particle {
  constructor() {
    this.active = false;
    this.x = 0;
    this.y = 0;
    this.vx = 0;
    this.vy = 0;
    this.life = 0;
    this.maxLife = 1;
    this.size = 1;
    this.color = '#ffffff';
  }

  init(x, y, vx, vy, maxLife, size, color) {
    this.x = x;
    this.y = y;
    this.vx = vx;
    this.vy = vy;
    this.life = maxLife;
    this.maxLife = maxLife;
    this.size = size;
    this.color = color;
    this.active = true;
  }

  update(dt) {
    if (!this.active) return false;
    this.life -= dt;
    if (this.life <= 0) {
      this.active = false;
      return false;
    }
    this.x += this.vx * dt;
    this.y += this.vy * dt;
    return true;
  }

  draw(ctx) {
    if (!this.active) return;
    const progress = this.life / this.maxLife;
    ctx.globalAlpha = Math.max(0, Math.min(1, progress));
    ctx.fillStyle = this.color;
    ctx.fillRect(Math.round(this.x), Math.round(this.y), this.size, this.size);
  }
}

export class ParticleSystem {
  /**
   * @param {object} [o]
   * @param {() => boolean} [o.isReduced]  reduced-motion check (tests inject one)
   * @param {() => number} [o.random]
   */
  constructor({ isReduced = prefersReduced, random = Math.random } = {}) {
    this.isReduced = isReduced;
    this.random = random;
    // Fixed object pool to prevent garbage collection spikes.
    this.pool = Array.from({ length: MAX_PARTICLES }, () => new Particle());
  }

  /** Number of live particles. */
  get activeCount() {
    let n = 0;
    for (const p of this.pool) if (p.active) n++;
    return n;
  }

  /** Should one particle be spawned in this frame for a rate of `perSecond` over `dt` seconds? */
  chance(perSecond, dt) {
    if (this.isReduced() || !(dt > 0)) return false;
    return this.random() < perSecond * dt;
  }

  _acquire() {
    if (this.isReduced()) return null;
    return this.pool.find((p) => !p.active) || null;
  }

  /** Spawn tiny dust puff behind walking agent feet. */
  spawnStepDust(x, y) {
    const p = this._acquire();
    if (!p) return;
    const rnd = this.random;
    const vx = (rnd() - 0.5) * 8;
    const vy = -rnd() * 4 - 2;
    const col = DUST_COLORS[Math.floor(rnd() * DUST_COLORS.length)];
    p.init(x + (rnd() - 0.5) * 4, y, vx, vy, 0.35, 1, col);
  }

  /** Spawn tiny coding sparks / keystroke piksels. */
  spawnCodeSparkle(x, y) {
    const p = this._acquire();
    if (!p) return;
    const rnd = this.random;
    const vx = (rnd() - 0.5) * 12;
    const vy = -rnd() * 10 - 4;
    const col = SPARK_COLORS[Math.floor(rnd() * SPARK_COLORS.length)];
    p.init(x, y, vx, vy, 0.45, 1, col);
  }

  /** Spawn subtle ambient dust mote in sunlight. */
  spawnDustMote(x, y) {
    const p = this._acquire();
    if (!p) return;
    p.init(x, y, (Math.random() - 0.5) * 2, (Math.random() - 0.5) * 2, 2.5, 1, 'rgba(255,255,220,0.6)');
  }

  update(dt) {
    for (const p of this.pool) {
      if (p.active) p.update(dt);
    }
  }

  draw(ctx) {
    for (const p of this.pool) {
      if (p.active) p.draw(ctx);
    }
    ctx.globalAlpha = 1;
  }
}

// ---------------------------------------------------------------------------
// Lighting & Atmosphere Engine
// ---------------------------------------------------------------------------

export class LightingEngine {
  /**
   * @param {object} [o]
   * @param {() => boolean} [o.isReduced]
   * @param {() => number} [o.now]   epoch ms (tests)
   */
  constructor({ isReduced = prefersReduced, now = Date.now } = {}) {
    /** @type {'auto' | 'day' | 'sunset' | 'night'} */
    this.mode = 'auto';
    this.isReduced = isReduced;
    this.now = now;
    this._autoPhase = 'day';
    this._phaseAt = -Infinity;
    /** Gradients are built once per phase/position and reused every frame (v2.md H15). */
    this._gradients = new Map();
  }

  /** Determine effective ambient lighting phase (the clock is re-read at most every 30 s). */
  currentPhase() {
    if (this.mode !== 'auto') return this.mode;
    const t = this.now();
    if (t - this._phaseAt >= PHASE_RECHECK_MS) {
      this._phaseAt = t;
      const hour = new Date(t).getHours();
      this._autoPhase = hour >= 20 || hour < 6 ? 'night' : hour >= 17 ? 'sunset' : 'day';
    }
    return this._autoPhase;
  }

  _gradient(key, make) {
    let g = this._gradients.get(key);
    if (!g) { g = make(); this._gradients.set(key, g); }
    return g;
  }

  /**
   * Render atmospheric tint, window night stars, and monitor glows.
   * Runs in world space coordinates.
   */
  drawAtmosphere(ctx, office, time) {
    if (this.isReduced()) return;
    const phase = this.currentPhase();
    if (phase === 'day') return;

    const w = office.width * TILE;
    const h = office.height * TILE;

    ctx.save();
    if (phase === 'sunset') {
      // Warm golden hour wash
      ctx.fillStyle = this._gradient(`sunset|${h}`, () => {
        const grad = ctx.createLinearGradient(0, 0, 0, h);
        grad.addColorStop(0, 'rgba(255, 140, 50, 0.16)');
        grad.addColorStop(1, 'rgba(180, 80, 40, 0.08)');
        return grad;
      });
      ctx.fillRect(0, 0, w, h);
    } else if (phase === 'night') {
      // Deep blue cozy night overlay
      ctx.fillStyle = 'rgba(12, 14, 34, 0.38)';
      ctx.fillRect(0, 0, w, h);

      // Twinkling stars in windows
      this._drawWindowStars(ctx, office, time);
    }
    ctx.restore();
  }

  _drawWindowStars(ctx, office, time) {
    for (let x = 0; x < office.width; x++) {
      if (office.tileAt(x, 0) === 'W') {
        const wx = x * TILE;
        // Two subtle star pixels per window pane
        ctx.fillStyle = Math.sin(time * 3 + x) > 0.3 ? STAR_ON_1 : STAR_OFF_1;
        ctx.fillRect(wx + 4, 3, 1, 1);
        ctx.fillStyle = Math.sin(time * 2.5 + x * 2) > 0.1 ? STAR_ON_2 : STAR_OFF_2;
        ctx.fillRect(wx + 11, 6, 1, 1);
      }
    }
  }

  /**
   * Draw glowing monitor halo around active desks during night/sunset.
   */
  drawMonitorGlow(ctx, deskX, deskY) {
    if (this.isReduced()) return;
    const phase = this.currentPhase();
    if (phase === 'day') return;

    const cx = deskX * TILE + 8;
    const cy = deskY * TILE + 10;

    ctx.save();
    ctx.fillStyle = this._gradient(`${phase}|${deskX}|${deskY}`, () => {
      const glow = ctx.createRadialGradient(cx, cy, 2, cx, cy, GLOW_RADIUS);
      glow.addColorStop(0, GLOW_COLOR[phase] || GLOW_COLOR.sunset);
      glow.addColorStop(1, 'rgba(0, 0, 0, 0)');
      return glow;
    });
    ctx.beginPath();
    ctx.arc(cx, cy, GLOW_RADIUS, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// Agent Click & Interaction Reactions
// ---------------------------------------------------------------------------

export class AgentReactions {
  constructor({ isReduced = prefersReduced } = {}) {
    /** Map of agentId -> { startTime, duration, type } */
    this.reactions = new Map();
    this.isReduced = isReduced;
  }

  /** Trigger a happy bounce & emote when user clicks on a character. */
  trigger(agentId, type = 'happy') {
    if (this.isReduced()) return;
    this.reactions.set(agentId, {
      startTime: performance.now(),
      duration: 1200, // 1.2 seconds total
      type,
    });
  }

  /** Returns vertical pixel offset for bouncing hop animation. */
  getHopOffset(agentId, now = performance.now()) {
    const r = this.reactions.get(agentId);
    if (!r) return 0;
    const elapsed = now - r.startTime;
    if (elapsed > r.duration) {
      this.reactions.delete(agentId);
      return 0;
    }
    // Damped harmonic bounce (up to 4px high)
    const t = elapsed / 400;
    if (t > 1) return 0; // hop finishes in first 400ms, emote stays for remaining
    return -Math.round(Math.abs(Math.sin(t * Math.PI)) * 4);
  }

  /** Returns active reaction emote if any. */
  getEmote(agentId, now = performance.now()) {
    const r = this.reactions.get(agentId);
    if (!r) return null;
    if (now - r.startTime > r.duration) {
      this.reactions.delete(agentId);
      return null;
    }
    return r.type;
  }
}
