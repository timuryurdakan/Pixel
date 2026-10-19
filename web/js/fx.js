// Pixel Office — FX, Ambient Lighting and Particle System (web/js/fx.js)
// Follows Clean Code & AGENTS.md rules: SRP, KISS, zero memory leaks, bounded arrays.

import { TILE } from './office.js';

/** Maximum number of active particles to guarantee constant memory & 60fps. */
const MAX_PARTICLES = 48;

/** Check for reduced motion preference. */
const prefersReduced = () =>
  typeof window !== 'undefined' &&
  window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

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
  constructor() {
    // Fixed object pool to prevent garbage collection spikes.
    this.pool = Array.from({ length: MAX_PARTICLES }, () => new Particle());
  }

  _acquire() {
    if (prefersReduced()) return null;
    return this.pool.find((p) => !p.active) || null;
  }

  /** Spawn tiny dust puff behind walking agent feet. */
  spawnStepDust(x, y) {
    const p = this._acquire();
    if (!p) return;
    const vx = (Math.random() - 0.5) * 8;
    const vy = -Math.random() * 4 - 2;
    const colors = ['#d29a68', '#c68b59', '#e8cbb0'];
    const col = colors[Math.floor(Math.random() * colors.length)];
    p.init(x + (Math.random() - 0.5) * 4, y, vx, vy, 0.35, 1, col);
  }

  /** Spawn tiny coding sparks / keystroke piksels. */
  spawnCodeSparkle(x, y) {
    const p = this._acquire();
    if (!p) return;
    const vx = (Math.random() - 0.5) * 12;
    const vy = -Math.random() * 10 - 4;
    const colors = ['#8fd3ff', '#ffd84a', '#4caf50', '#ffffff'];
    const col = colors[Math.floor(Math.random() * colors.length)];
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
  constructor() {
    /** @type {'auto' | 'day' | 'sunset' | 'night'} */
    this.mode = 'auto';
  }

  /** Determine effective ambient lighting phase. */
  currentPhase() {
    if (this.mode !== 'auto') return this.mode;
    const hour = new Date().getHours();
    if (hour >= 20 || hour < 6) return 'night';
    if (hour >= 17 && hour < 20) return 'sunset';
    return 'day';
  }

  /**
   * Render atmospheric tint, window night stars, and monitor glows.
   * Runs in world space coordinates.
   */
  drawAtmosphere(ctx, office, time) {
    if (prefersReduced()) return;
    const phase = this.currentPhase();
    if (phase === 'day') return;

    const w = office.width * TILE;
    const h = office.height * TILE;

    ctx.save();
    if (phase === 'sunset') {
      // Warm golden hour wash
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, 'rgba(255, 140, 50, 0.16)');
      grad.addColorStop(1, 'rgba(180, 80, 40, 0.08)');
      ctx.fillStyle = grad;
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
        const blink1 = Math.sin(time * 3 + x) > 0.3 ? 0.9 : 0.2;
        const blink2 = Math.sin(time * 2.5 + x * 2) > 0.1 ? 0.8 : 0.1;
        ctx.fillStyle = `rgba(255, 255, 255, ${blink1})`;
        ctx.fillRect(wx + 4, 3, 1, 1);
        ctx.fillStyle = `rgba(210, 230, 255, ${blink2})`;
        ctx.fillRect(wx + 11, 6, 1, 1);
      }
    }
  }

  /**
   * Draw glowing monitor halo around active desks during night/sunset.
   */
  drawMonitorGlow(ctx, deskX, deskY) {
    if (prefersReduced()) return;
    const phase = this.currentPhase();
    if (phase === 'day') return;

    const cx = deskX * TILE + 8;
    const cy = deskY * TILE + 10;
    const radius = 14;

    ctx.save();
    const glow = ctx.createRadialGradient(cx, cy, 2, cx, cy, radius);
    glow.addColorStop(0, phase === 'night' ? 'rgba(76, 175, 80, 0.22)' : 'rgba(255, 215, 64, 0.18)');
    glow.addColorStop(1, 'rgba(0, 0, 0, 0)');
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// Agent Click & Interaction Reactions
// ---------------------------------------------------------------------------

export class AgentReactions {
  constructor() {
    /** Map of agentId -> { startTime, duration, type } */
    this.reactions = new Map();
  }

  /** Trigger a happy bounce & emote when user clicks on a character. */
  trigger(agentId, type = 'happy') {
    if (prefersReduced()) return;
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
