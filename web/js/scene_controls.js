// Pixel Office — Scene Controls: zoom (buttons, Ctrl+wheel, two-finger pinch),
// atmosphere selector (auto/day/sunset/night), clickable coffee machine & water cooler,
// and Boss ("Patron") hover tooltip (docs/EVRENSEL_PLATFORM_ARASTIRMA_RAPORU.md §1.2, G1).
// Pure DOM API + textContent only; never uses innerHTML.

import { ATMOSPHERE_MODES } from './fx.js';
import { sound } from './audio.js';
import { t as tt } from './i18n.js';

export const ZOOM_MIN = 0.8;
export const ZOOM_MAX = 2.5;
export const ZOOM_STEP = 0.25;

export const ATMOSPHERE_LABELS = Object.freeze({
  auto: tt('Otomatik'),
  day: tt('Gündüz'),
  sunset: tt('Gün batımı'),
  night: tt('Gece'),
});

function touchDistance(touches) {
  if (!touches || touches.length < 2) return 0;
  const dx = touches[1].clientX - touches[0].clientX;
  const dy = touches[1].clientY - touches[0].clientY;
  return Math.hypot(dx, dy);
}

function canvasPoint(canvas, e) {
  const r = canvas.getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}

export class SceneControls {
  /**
   * @param {import('./renderer.js').Renderer} renderer
   * @param {{ container?: HTMLElement|null, soundPlayer?: typeof sound }} [opts]
   */
  constructor(renderer, { container = undefined, soundPlayer = sound } = {}) {
    this.renderer = renderer;
    this.canvas = renderer.canvas;
    this.sound = soundPlayer;
    this._pinchStartDist = 0;
    this._pinchStartZoom = 1.0;

    const host = container !== undefined
      ? container
      : (this.canvas && typeof this.canvas.closest === 'function' ? this.canvas.closest('#stage') : null);
    this.el = host ? this._buildToolbar(host) : null;
    this._bindCanvasEvents();
    this.syncUI();
  }

  _buildToolbar(host) {
    const existing = host.querySelector('.scene-controls');
    if (existing) existing.remove();

    const bar = document.createElement('div');
    bar.className = 'scene-controls';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', tt('Sahne kontrolleri'));

    const mkBtn = (action, label, title) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'scene-ctl-btn';
      b.dataset.sceneAction = action;
      b.textContent = label;
      b.title = title;
      b.setAttribute('aria-label', title);
      return b;
    };

    this.btnZoomOut = mkBtn('zoom-out', '−', tt('Uzaklaştır'));
    this.btnZoomFit = mkBtn('zoom-fit', tt('Sığdır'), tt('Sahneyi sığdır'));
    this.btnZoomIn = mkBtn('zoom-in', '+', tt('Yakınlaştır'));
    this.btnAtmosphere = mkBtn('atmosphere', ATMOSPHERE_LABELS.auto, tt('Atmosfer modunu değiştir'));

    this.btnZoomOut.addEventListener('click', () => this.zoomOut());
    this.btnZoomFit.addEventListener('click', () => this.resetZoom());
    this.btnZoomIn.addEventListener('click', () => this.zoomIn());
    this.btnAtmosphere.addEventListener('click', () => this.cycleAtmosphere());

    bar.append(this.btnZoomOut, this.btnZoomFit, this.btnZoomIn, this.btnAtmosphere);
    host.appendChild(bar);
    return bar;
  }

  zoomIn(step = ZOOM_STEP) {
    const next = Math.round((this.renderer.zoomLevel + step) * 100) / 100;
    this.renderer.setZoom(next);
    this.syncUI();
    return this.renderer.zoomLevel;
  }

  zoomOut(step = ZOOM_STEP) {
    const next = Math.round((this.renderer.zoomLevel - step) * 100) / 100;
    this.renderer.setZoom(next);
    this.syncUI();
    return this.renderer.zoomLevel;
  }

  resetZoom() {
    this.renderer.setZoom(1.0);
    this.syncUI();
    return this.renderer.zoomLevel;
  }

  setAtmosphere(mode) {
    const next = ATMOSPHERE_MODES.includes(mode) ? mode : 'auto';
    this.renderer.lighting.setMode(next);
    this.syncUI();
    return this.renderer.lighting.mode;
  }

  cycleAtmosphere() {
    this.renderer.lighting.cycleMode();
    this.syncUI();
    return this.renderer.lighting.mode;
  }

  syncUI() {
    if (!this.el) return;
    const z = this.renderer.zoomLevel;
    this.btnZoomOut.disabled = z <= ZOOM_MIN;
    this.btnZoomIn.disabled = z >= ZOOM_MAX;
    this.btnZoomFit.textContent = z === 1.0 ? tt('Sığdır') : `${Math.round(z * 100)}%`;

    const mode = this.renderer.lighting.mode || 'auto';
    const label = ATMOSPHERE_LABELS[mode] || ATMOSPHERE_LABELS.auto;
    this.btnAtmosphere.dataset.atmosphereMode = mode;
    this.btnAtmosphere.textContent = label;
    this.btnAtmosphere.title = `Atmosfer: ${label}`;
  }

  _bindCanvasEvents() {
    const canvas = this.canvas;
    if (!canvas || typeof canvas.addEventListener !== 'function') return;

    canvas.addEventListener('wheel', (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      if (e.cancelable) e.preventDefault();
      if (e.deltaY < 0) this.zoomIn();
      else if (e.deltaY > 0) this.zoomOut();
    }, { passive: false });

    canvas.addEventListener('touchstart', (e) => {
      if (e.touches && e.touches.length === 2) {
        const d = touchDistance(e.touches);
        if (d > 0) {
          this._pinchStartDist = d;
          this._pinchStartZoom = this.renderer.zoomLevel;
        }
      } else {
        this._pinchStartDist = 0;
      }
    }, { passive: true });

    canvas.addEventListener('touchmove', (e) => {
      if (!e.touches || e.touches.length !== 2 || !(this._pinchStartDist > 0)) return;
      const d = touchDistance(e.touches);
      if (!(d > 0)) return;
      if (e.cancelable) e.preventDefault();
      const ratio = d / this._pinchStartDist;
      const target = Math.round(this._pinchStartZoom * ratio * 100) / 100;
      this.renderer.setZoom(target);
      this.syncUI();
    }, { passive: false });

    const endPinch = (e) => {
      if (!e.touches || e.touches.length < 2) this._pinchStartDist = 0;
    };
    canvas.addEventListener('touchend', endPinch, { passive: true });
    canvas.addEventListener('touchcancel', endPinch, { passive: true });

    canvas.addEventListener('mousemove', (e) => {
      const [x, y] = canvasPoint(canvas, e);
      const overBoss = this.renderer.pickBoss(x, y);
      const overProp = !overBoss ? this.renderer.pickProp(x, y) : null;
      this.renderer.hoverBoss = overBoss;
      this.renderer.hoverProp = overProp;
      if (overBoss) {
        canvas.title = 'Patron';
        canvas.style.cursor = 'pointer';
      } else {
        if (canvas.title === 'Patron') canvas.title = '';
        if (overProp) canvas.style.cursor = 'pointer';
      }
    });

    canvas.addEventListener('mouseleave', () => {
      this.renderer.hoverBoss = false;
      this.renderer.hoverProp = null;
      if (canvas.title === 'Patron') canvas.title = '';
    });

    canvas.addEventListener('click', (e) => {
      const [x, y] = canvasPoint(canvas, e);
      const chars = (typeof window !== 'undefined' && window.pixelOffice && window.pixelOffice.manager)
        ? window.pixelOffice.manager.list()
        : (this.renderer._lastCharacters || []);
      if (this.renderer.pickCharacter(chars, x, y)) return;
      if (this.renderer.pickBoss(x, y)) return;
      const prop = this.renderer.pickProp(x, y);
      if (prop) this.renderer.triggerProp(prop, this.sound);
    });
  }
}
