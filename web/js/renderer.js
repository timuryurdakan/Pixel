// Pixel Office — canvas renderer (ARCHITECTURE.md §6.4).
// Cached static background, y-sorted furniture + characters, bubbles, crisp labels.

import { getCharacterFrame, getTileSprite, getFurnitureSprite, getBubbleSprite, getToolIcon, CHAR_W, CHAR_H } from './sprites.js';
import { TILE, SCALE } from './office.js';

const DESK_ON_STATES = new Set(['working', 'thinking']);
export const LABEL_MAX = 18;

/**
 * Canvas label (drawn with fillText, never parsed as markup). Local agents: name (v1).
 * Cloud agents (have `owner`): "name·project", max 18 chars with an ellipsis; subagents "↳ name" (CLOUD.md §9.3, §15.1).
 */
export function labelText(agent) {
  const name = String(agent.name || '?');
  if (agent.isSubagent) return clip('↳ ' + name, agent.owner ? LABEL_MAX : Infinity);
  if (!agent.owner) return name;
  const proj = agent.project && agent.project !== '?' ? String(agent.project) : '';
  return clip(proj ? `${name}·${proj}` : name, LABEL_MAX);
}
function clip(s, max) {
  const cps = [...s];
  return cps.length > max ? cps.slice(0, max - 1).join('') + '…' : s;
}

export class Renderer {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {import('./office.js').Office} office
   */
  constructor(canvas, office) {
    this.canvas = canvas;
    this.office = office;
    this.worldW = office.width * TILE;   // 352
    this.worldH = office.height * TILE;  // 224
    this.ctx = canvas.getContext('2d');
    this.scale = SCALE;                  // device px per world px
    this._applySize(SCALE, null);
    this.background = this._buildBackground();
  }

  _applySize(s, cssScale) {
    this.scale = s;
    this.canvas.width = this.worldW * s;
    this.canvas.height = this.worldH * s;
    if (cssScale) {
      this.canvas.style.width = `${this.worldW * cssScale}px`;
    }
    this.ctx.imageSmoothingEnabled = false;
  }

  /**
   * Fit the canvas into availW × availH CSS px. Uses an integer number of device pixels per
   * world pixel when that wastes ≤ 15 % of the space (perfectly crisp), otherwise fills the
   * space exactly with a backing store rendered at the next integer scale.
   */
  fit(availW, availH) {
    const dpr = window.devicePixelRatio || 1;
    const fit = Math.max(0.25, Math.min(availW / this.worldW, availH / this.worldH));
    const dev = fit * dpr;
    let s = Math.floor(dev);
    let cssScale;
    if (s >= 1 && s / dev >= 0.85) cssScale = s / dpr;
    else { s = Math.max(1, Math.ceil(dev)); cssScale = fit; }
    const key = `${s}|${cssScale.toFixed(4)}`;
    if (key === this._fitKey) return;
    this._fitKey = key;
    this._applySize(s, cssScale);
  }

  _buildBackground() {
    const c = document.createElement('canvas');
    c.width = this.worldW; c.height = this.worldH;
    const ctx = c.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    const o = this.office;
    for (let y = 0; y < o.height; y++) {
      for (let x = 0; x < o.width; x++) {
        const ch = o.tileAt(x, y);
        let name = (x + y) % 2 === 1 ? 'floor_alt' : 'floor';
        if (ch === '=') name = 'rug';
        else if (ch === 'D') name = 'door';
        else if (ch === '#') name = 'wall';
        else if (ch === 'W') name = 'window';
        ctx.drawImage(getTileSprite(name), x * TILE, y * TILE);
      }
    }
    return c;
  }

  /** Sprite rect (world px) of a character. */
  _spriteRect(ch) {
    if (ch.isSleepingOnBed()) return { x: ch.spot.x * TILE, y: ch.spot.y * TILE + 2, w: CHAR_W, h: CHAR_H };
    return { x: Math.round(ch.px) - 8, y: Math.round(ch.py) - 23, w: CHAR_W, h: CHAR_H };
  }

  /**
   * @param {import('./agents.js').Character[]} characters
   * @param {{selectedId:string|null, hoverId:string|null, time:number}} opts
   */
  render(characters, { selectedId = null, hoverId = null, time = 0 } = {}) {
    const ctx = this.ctx;
    const s = this.scale;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.globalAlpha = 1;
    ctx.setTransform(s, 0, 0, s, 0, 0);
    ctx.drawImage(this.background, 0, 0);

    const byId = new Map(characters.map((c) => [c.id, c]));
    const items = [];

    for (const f of this.office.furniture) {
      let name = f.kind;
      if (f.kind === 'desk' && f.spot && f.spot.occupant) {
        const c = byId.get(f.spot.occupant);
        if (c && c.phase === 'settled' && c.spot === f.spot && DESK_ON_STATES.has(c.agent.state)) name = 'desk_on';
      }
      const img = getFurnitureSprite(name);
      const dx = f.x * TILE;
      const dy = f.kind === 'bed' ? f.y * TILE : f.y * TILE + TILE - img.height;
      items.push({ sortY: f.sortY, x: dx, draw: () => ctx.drawImage(img, dx, dy) });
    }

    for (const c of characters) {
      if (c.alpha <= 0) continue;
      const r = this._spriteRect(c);
      const sel = c.id === selectedId, hov = c.id === hoverId;
      items.push({
        sortY: c.sortY, x: r.x,
        draw: () => {
          if (sel || hov) {
            ctx.globalAlpha = sel ? 0.6 : 0.35;
            ctx.fillStyle = sel ? '#ffd84a' : '#ffffff';
            ctx.beginPath();
            if (c.isSleepingOnBed()) ctx.ellipse(r.x + 8, r.y + 12, 9, 13, 0, 0, Math.PI * 2);
            else ctx.ellipse(Math.round(c.px), Math.round(c.py), 7, 3, 0, 0, Math.PI * 2);
            ctx.fill();
          }
          ctx.globalAlpha = c.alpha;
          const f = c.currentFrame();
          ctx.drawImage(getCharacterFrame(c.palette, f.anim, f.dir, f.frame), r.x, r.y);
          ctx.globalAlpha = 1;
        },
      });
    }

    items.sort((a, b) => a.sortY - b.sortY || a.x - b.x);
    for (const it of items) it.draw();

    // Bubbles (world space, above all sprites).
    for (const c of characters) {
      const b = c.bubble();
      if (!b || c.alpha < 1) continue;
      const phase = ((c.agent.colorSeed >>> 0) % 997) / 997;
      const bob = Math.round(Math.sin((time + phase) * Math.PI * 4));
      let bx, by;
      if (c.isSleepingOnBed()) { bx = c.spot.x * TILE + 14; by = c.spot.y * TILE - 4; }
      else { bx = c.px; by = c.py - 27; }
      let img = null;
      if (b.kind === 'tool') img = getToolIcon(b.tool);
      else if (b.kind === 'alert') { if (Math.floor(time * 4) % 2 === 0) img = getBubbleSprite('alert', 0); }
      else if (b.kind === 'steam') {
        // Coffee break: a cup bubble above the head plus a steam wisp rising from the mug.
        img = getBubbleSprite('coffee', Math.floor(time * 2));
        const steam = getBubbleSprite('steam', Math.floor(time * 3));
        ctx.globalAlpha = 0.85;
        ctx.drawImage(steam, Math.round(c.px + 1), Math.round(c.py - 20 - (Math.floor(time * 3) % 2)));
        ctx.globalAlpha = 1;
      } else img = getBubbleSprite(b.kind, Math.floor(time * 2));
      if (img) ctx.drawImage(img, Math.round(bx - img.width / 2), Math.round(by - img.height / 2 + bob));
    }

    // Labels (device-pixel space, so text is crisp at any scale).
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    const dpr = window.devicePixelRatio || 1;
    const fontPx = Math.round(Math.max(9 * Math.min(dpr, s), (11 * s) / 3));
    ctx.font = `bold ${fontPx}px ui-monospace, Menlo, Consolas, monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.lineJoin = 'round';
    ctx.lineWidth = Math.max(2, Math.round(fontPx / 4));
    for (const c of characters) {
      if (c.alpha <= 0) continue;
      const sel = c.id === selectedId, hov = c.id === hoverId;
      const sleeping = c.isSleepingOnBed();
      if (sleeping && !sel && !hov) continue;
      const text = labelText(c.agent);
      let lx = c.px, ly = c.py + 2;
      if (sleeping) { lx = c.spot.x * TILE + 8; ly = c.spot.y * TILE + 26; }
      const X = Math.round(lx * s), Y = Math.round(ly * s);
      ctx.globalAlpha = c.alpha;
      ctx.strokeStyle = '#000';
      ctx.strokeText(text, X, Y);
      ctx.fillStyle = sel ? '#ffd84a' : '#ffffff';
      ctx.fillText(text, X, Y);
    }
    ctx.globalAlpha = 1;
  }

  /** CSS px relative to the canvas → character id or null (topmost wins). */
  pickCharacter(characters, canvasX, canvasY) {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const wx = (canvasX * this.worldW) / rect.width;
    const wy = (canvasY * this.worldH) / rect.height;
    let best = null;
    for (const c of characters) {
      if (c.alpha <= 0 || c.phase === 'gone') continue;
      const r = this._spriteRect(c);
      if (wx >= r.x && wx < r.x + r.w && wy >= r.y && wy < r.y + r.h) {
        if (!best || c.sortY >= best.sortY) best = c;
      }
    }
    return best ? best.id : null;
  }
}
