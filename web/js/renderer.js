// Pixel Office — canvas renderer (ARCHITECTURE.md §6.4).
// Cached static background, y-sorted furniture + characters, bubbles, crisp labels.

import { getCharacterFrame, getTileSprite, getFurnitureSprite, getBubbleSprite, getToolIcon, getCatFrame, CHAR_W, CHAR_H,
  getBossFrame, getBossDeskSprite } from './sprites.js';
import { TILE, SCALE, CAT_TILE, BOSS_SEAT, BOSS_DESK, BOSS_DOOR, inBossRoom } from './office.js';
import { bossPose } from './boss_scene.js';
import { ParticleSystem, LightingEngine, AgentReactions, STEP_DUST_PER_S, CODE_SPARKLE_PER_S } from './fx.js';
import { drawBoardOverlay, BOARD_X, BOARD_Y, BOARD_W, BOARD_H } from './board.js';
import { SceneControls, ZOOM_MIN, ZOOM_MAX, ZOOM_STEP } from './scene_controls.js';

const DESK_ON_STATES = new Set(['working', 'thinking']);
export const CAT_ID = 'mascot-cat';
export const BOARD_ID = 'board';
export const COFFEE_ID = 'prop:coffee_counter';
export const WATER_ID = 'prop:water_cooler';
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

/**
 * The bubble to draw: a click emote replaces the state bubble for a moment, except the blinking "!" of an agent
 * that waits for the user, which must never be hidden (v2.md H35).
 */
export function bubbleWithEmote(stateBubble, emote) {
  if (!emote || (stateBubble && stateBubble.kind === 'alert')) return stateBubble;
  return { kind: emote };
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
    this.zoomLevel = 1.0;
    this._availW = 0;
    this._availH = 0;
    this._applySize(SCALE, null);
    this.background = this._buildBackground();
    this.particles = new ParticleSystem();
    this.lighting = new LightingEngine();
    this.reactions = new AgentReactions();
    this.cat = {
      id: CAT_ID,
      x: CAT_TILE.x * TILE,
      y: CAT_TILE.y * TILE,
      w: 16,
      h: 16,
      wakeTime: 0,
    };
    this.hoverBoss = false;
    this.hoverProp = null;
    this._lastCharacters = [];
    this._lastTime = 0;
    this.sceneControls = new SceneControls(this);
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
    this._availW = availW;
    this._availH = availH;
    const dpr = window.devicePixelRatio || 1;
    const baseFit = Math.max(0.25, Math.min(availW / this.worldW, availH / this.worldH));
    const fit = baseFit * (this.zoomLevel || 1.0);
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

  setZoom(level) {
    this.zoomLevel = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, level));
    if (this._availW && this._availH) {
      this._fitKey = null;
      this.fit(this._availW, this._availH);
    }
  }

  zoomIn(step = ZOOM_STEP) {
    return this.sceneControls ? this.sceneControls.zoomIn(step) : (this.setZoom(this.zoomLevel + step), this.zoomLevel);
  }

  zoomOut(step = ZOOM_STEP) {
    return this.sceneControls ? this.sceneControls.zoomOut(step) : (this.setZoom(this.zoomLevel - step), this.zoomLevel);
  }

  resetZoom() {
    return this.sceneControls ? this.sceneControls.resetZoom() : (this.setZoom(1.0), this.zoomLevel);
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
        else if (ch === 'd') name = 'door_front';
        else if (ch === '#') name = 'wall';
        else if (ch === 'W') name = 'window';
        else if (inBossRoom(x, y)) name = 'boss_floor';
        ctx.drawImage(getTileSprite(name), x * TILE, y * TILE);
      }
    }
    // The Boss's brass sign on his door's wall cap (docs/PATRON_ODASI.md §3.3).
    if (o.tileAt(BOSS_DOOR.x, BOSS_DOOR.y) === 'd') ctx.drawImage(getFurnitureSprite('boss_sign'), BOSS_DOOR.x * TILE, BOSS_DOOR.y * TILE);
    return c;
  }

  /** The office map has the Boss's seat and desk (an older/custom map may not). */
  _hasBoss() {
    const o = this.office;
    return o.tileAt(BOSS_SEAT.x, BOSS_SEAT.y) === 'L' && o.tileAt(BOSS_DESK.x, BOSS_DESK.y) === 'X';
  }

  /**
   * The Boss at his desk as one y-sorted item: chair → desk (one row lower, with `files` folders) → seated Boss, so his
   * torso covers the back of the desk top and his hands lie on it (docs/PATRON_ODASI.md §3.3). Never labelled.
   */
  _bossItem(pose) {
    const ctx = this.ctx;
    const sx = BOSS_SEAT.x * TILE, sy = BOSS_SEAT.y * TILE + TILE - CHAR_H;
    const desk = getBossDeskSprite(pose.files);
    const dx = BOSS_DESK.x * TILE, dy = BOSS_DESK.y * TILE + TILE - desk.height;
    const chair = getFurnitureSprite('boss_chair');
    const man = getBossFrame(pose.anim, 'down', pose.frame);
    return {
      sortY: (BOSS_DESK.y + 1) * TILE - 1,
      x: dx,
      draw: () => {
        ctx.drawImage(chair, sx, sy);
        ctx.drawImage(desk, dx, dy);
        ctx.drawImage(man, sx, sy);
      },
    };
  }

  /** Sprite rect (world px) of a character. */
  _spriteRect(ch) {
    if (ch.isSleepingOnBed()) return { x: ch.spot.x * TILE, y: ch.spot.y * TILE + 2, w: CHAR_W, h: CHAR_H };
    return { x: Math.round(ch.px) - 8, y: Math.round(ch.py) - 23, w: CHAR_W, h: CHAR_H };
  }

  /**
   * @param {import('./agents.js').Character[]} characters
   * @param {{selectedId?:string|null, hoverId?:string|null, hoverBoss?:boolean, time?:number, boardData?:object|null, boss?:object|null}} [opts]
   */
  render(characters, { selectedId = null, hoverId = null, hoverBoss = this.hoverBoss, time = 0, boardData = null, boss = null } = {}) {
    this._lastCharacters = characters;
    if (boardData !== null) this.boardData = boardData;
    const dt = this._lastTime ? Math.min(0.1, Math.max(0, time - this._lastTime)) : 0.016;
    this._lastTime = time;
    this.particles.update(dt);

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
      let isDeskActive = false;
      if (f.kind === 'desk' && f.spot && f.spot.occupant) {
        const c = byId.get(f.spot.occupant);
        if (c && c.phase === 'settled' && c.spot === f.spot && DESK_ON_STATES.has(c.agent.state)) {
          name = 'desk_on';
          isDeskActive = true;
        }
      }
      const img = getFurnitureSprite(name);
      const dx = f.x * TILE;
      if (f.kind === 'sofa') { items.push({ sortY: f.sortY, x: dx, draw: () => ctx.drawImage(img, dx, f.y * TILE) }); continue; }
      const dy = f.kind === 'bed' ? f.y * TILE : f.y * TILE + TILE - img.height;
      items.push({
        sortY: f.sortY,
        x: dx,
        draw: () => {
          if (isDeskActive) this.lighting.drawMonitorGlow(ctx, f.x, f.y);
          ctx.drawImage(img, dx, dy);
          if (f.kind === 'board' && this.boardData) {
            drawBoardOverlay(ctx, dx, dy, this.boardData);
          }
        },
      });
    }

    for (const c of characters) {
      if (c.alpha <= 0) continue;

      // FX particles: a rate per second (× frame time), so 60/120/144 Hz screens spawn the same amount (v2.md H15).
      if (c.anim === 'walk' && this.particles.chance(STEP_DUST_PER_S, dt)) {
        this.particles.spawnStepDust(c.px, c.py);
      } else if (c.anim === 'type' && this.particles.chance(CODE_SPARKLE_PER_S, dt)) {
        this.particles.spawnCodeSparkle(c.px + (Math.random() - 0.5) * 8, c.py - 12);
      }

      const r = this._spriteRect(c);
      const hop = this.reactions.getHopOffset(c.id);
      const sel = c.id === selectedId, hov = c.id === hoverId;
      items.push({
        sortY: c.sortY,
        x: r.x,
        draw: () => {
          if (sel || hov) {
            ctx.globalAlpha = sel ? 0.6 : 0.35;
            ctx.fillStyle = sel ? '#ffd84a' : '#ffffff';
            ctx.beginPath();
            if (c.isSleepingOnBed()) ctx.ellipse(r.x + 8, r.y + 12 + hop, 9, 13, 0, 0, Math.PI * 2);
            else ctx.ellipse(Math.round(c.px), Math.round(c.py), 7, 3, 0, 0, Math.PI * 2);
            ctx.fill();
          }
          ctx.globalAlpha = c.alpha;
          const f = c.currentFrame();
          ctx.drawImage(getCharacterFrame(c.palette, f.anim, f.dir, f.frame), r.x, r.y + hop);
          ctx.globalAlpha = 1;
        },
      });
    }

    // Mascot cat: curled up on the rug corner (CAT_TILE, not walkable), sits up now and then or when clicked.
    const cat = this.cat;
    const catHop = this.reactions.getHopOffset(cat.id);
    const isCatAwake = (time - cat.wakeTime) < 5.0 || ((Math.floor(time) % 24) < 4);
    const catAnim = isCatAwake ? 'sit' : 'sleep';
    const catFrame = isCatAwake ? Math.floor(time * 2) : Math.floor(time * 1.5);
    const catSprite = getCatFrame(catAnim, catFrame);
    items.push({
      sortY: cat.y + 16,
      x: cat.x,
      draw: () => {
        ctx.drawImage(catSprite, cat.x, cat.y + catHop);
      },
    });

    const bossNow = this._hasBoss() ? (boss || bossPose({ time })) : null;
    if (bossNow) items.push(this._bossItem(bossNow));

    items.sort((a, b) => a.sortY - b.sortY || a.x - b.x);
    for (const it of items) it.draw();

    // World-space FX particles
    this.particles.draw(ctx);

    // Bubbles (world space, above all sprites).
    for (const c of characters) {
      const b = bubbleWithEmote(c.bubble(), this.reactions.getEmote(c.id));
      if (!b || c.alpha < 1) continue;
      const phase = ((c.agent.colorSeed >>> 0) % 997) / 997;
      const bob = Math.round(Math.sin((time + phase) * Math.PI * 4));
      const hop = this.reactions.getHopOffset(c.id);
      let bx, by;
      if (c.isSleepingOnBed()) { bx = c.spot.x * TILE + 14; by = c.spot.y * TILE - 4; }
      else { bx = c.px; by = c.py - 27 + hop; }
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

    if (bossNow && bossNow.bubble) {
      const img = bossNow.bubble === 'alert'
        ? (Math.floor(time * 4) % 2 === 0 ? getBubbleSprite('alert', 0) : null)
        : getBubbleSprite(bossNow.bubble, Math.floor(time * 2));
      const bob = Math.round(Math.sin(time * Math.PI * 4));
      if (img) ctx.drawImage(img, BOSS_SEAT.x * TILE + 8 - img.width / 2, BOSS_SEAT.y * TILE + TILE - CHAR_H - 2 - img.height / 2 + bob);
    }

    // Cat mascot emote bubble
    const catEmote = this.reactions.getEmote(cat.id);
    if (catEmote) {
      const img = getBubbleSprite(catEmote, 0);
      if (img) ctx.drawImage(img, Math.round(cat.x + 8 - img.width / 2), Math.round(cat.y - 12 + catHop));
    }

    // Interactive office props (coffee machine & water cooler) reaction bubbles (G1)
    for (const [kind, propId] of [['coffee_counter', COFFEE_ID], ['water_cooler', WATER_ID]]) {
      const emote = this.reactions.getEmote(propId);
      if (!emote) continue;
      const f = this.office.furniture.find((it) => it.kind === kind);
      if (!f) continue;
      const img = getBubbleSprite(emote, Math.floor(time * 2));
      const bob = Math.round(Math.sin(time * Math.PI * 4));
      const bx = f.x * TILE + Math.round(f.w / 2);
      const by = f.y * TILE + TILE - f.h - 6 + bob;
      if (img) ctx.drawImage(img, Math.round(bx - img.width / 2), Math.round(by - img.height / 2));
    }

    // World atmosphere & window lighting
    this.lighting.drawAtmosphere(ctx, this.office, time);

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
    if (bossNow && (hoverBoss || hoverId === 'boss')) {
      const bx = Math.round((BOSS_SEAT.x * TILE + 8) * s);
      const by = Math.round(((BOSS_DESK.y + 1) * TILE + 2) * s);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = '#000';
      ctx.strokeText('Patron', bx, by);
      ctx.fillStyle = '#ffd84a';
      ctx.fillText('Patron', bx, by);
    }
    ctx.globalAlpha = 1;
  }

  /**
   * CSS px relative to the canvas → character id, CAT_ID or null. Agents come first (topmost by sortY wins);
   * the cat only when no agent is under the pointer (v2.md H3).
   */
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
    if (best) return best.id;
    const cat = this.cat;
    if (wx >= cat.x && wx < cat.x + cat.w && wy >= cat.y && wy < cat.y + cat.h) return cat.id;
    if (wx >= BOARD_X && wx < BOARD_X + BOARD_W && wy >= BOARD_Y && wy < BOARD_Y + BOARD_H) return BOARD_ID;
    return null;
  }

  /** CSS px relative to the canvas → true on the Boss or his desk (opens the Boss's panel). */
  pickBoss(canvasX, canvasY) {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height || !this._hasBoss()) return false;
    const wx = (canvasX * this.worldW) / rect.width;
    const wy = (canvasY * this.worldH) / rect.height;
    const x = BOSS_DESK.x * TILE, top = BOSS_SEAT.y * TILE + TILE - CHAR_H, bottom = (BOSS_DESK.y + 1) * TILE;
    return wx >= x && wx < x + BOSS_DESK.w && wy >= top && wy < bottom;
  }

  pickBoard(canvasX, canvasY) {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    const wx = (canvasX * this.worldW) / rect.width;
    const wy = (canvasY * this.worldH) / rect.height;
    return wx >= BOARD_X && wx < BOARD_X + BOARD_W && wy >= BOARD_Y && wy < BOARD_Y + BOARD_H;
  }

  /** CSS px relative to the canvas → 'coffee_counter' | 'water_cooler' | null (G1). */
  pickProp(canvasX, canvasY) {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const wx = (canvasX * this.worldW) / rect.width;
    const wy = (canvasY * this.worldH) / rect.height;
    for (const f of this.office.furniture) {
      if (f.kind !== 'coffee_counter' && f.kind !== 'water_cooler') continue;
      const fx = f.x * TILE;
      const fy = f.y * TILE + TILE - f.h;
      if (wx >= fx && wx < fx + f.w && wy >= fy && wy < fy + f.h) return f.kind;
    }
    return null;
  }

  /** Trigger visual & audio reaction for an interactive office prop (G1). */
  triggerProp(kind, soundPlayer = null) {
    const f = this.office.furniture.find((it) => it.kind === kind);
    if (!f) return;
    if (kind === 'coffee_counter') {
      this.reactions.trigger(COFFEE_ID, 'coffee');
      this.particles.spawnSteam(f.x * TILE + Math.round(f.w / 2), f.y * TILE);
      if (soundPlayer && typeof soundPlayer.playCoffee === 'function') soundPlayer.playCoffee();
    } else if (kind === 'water_cooler') {
      this.reactions.trigger(WATER_ID, 'happy');
      this.particles.spawnWaterSplash(f.x * TILE + Math.round(f.w / 2), f.y * TILE + 4);
      if (soundPlayer && typeof soundPlayer.playBlip === 'function') soundPlayer.playBlip();
    }
  }
}

