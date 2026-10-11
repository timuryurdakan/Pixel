// Pixel Office — mini window (CLOUD.md §11).
// Document Picture-in-Picture: the existing .frame (canvas + banner) and a one-line status strip move
// into an always-on-top window; rAF/timers are hosted by that window while it is open (main.js).
// Fallback without Document PiP: a positioned popup with ?view=mini (not always-on-top).

export const PIP_SIZE = { width: 400, height: 290 };

export class MiniWindow {
  /**
   * @param {{frameEl:HTMLElement, stripEl?:HTMLElement, onOpen?:(win:Window)=>void, onClose?:()=>void}} opts
   */
  constructor({ frameEl, stripEl = null, onOpen = () => {}, onClose = () => {} }) {
    this.frameEl = frameEl;
    this.stripEl = stripEl;
    this.onOpen = onOpen;
    this.onClose = onClose;
    this._win = null;
    this._home = null;      // {parent, next} of the frame before it moved
    this._stripHome = null;
  }

  static supported() {
    return typeof window !== 'undefined' && 'documentPictureInPicture' in window;
  }

  /** Popup fallback available (desktop pointer). */
  static popupSupported() {
    return typeof window !== 'undefined' && typeof window.open === 'function'
      && !(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
  }

  get window() { return this._win; }

  /** Must be called from a click handler (user activation). */
  async open() {
    if (this._win) { try { this._win.focus(); } catch { /* ignore */ } return this._win; }
    const w = await window.documentPictureInPicture.requestWindow(PIP_SIZE);
    const doc = w.document;
    doc.documentElement.lang = 'tr';
    const title = doc.createElement('title');
    title.textContent = 'Pixel Office — mini';
    doc.head.appendChild(title);
    const link = doc.createElement('link');
    link.rel = 'stylesheet';
    link.href = new URL('css/style.css', document.baseURI).href;
    doc.head.appendChild(link);
    doc.body.className = 'mini pip';

    this._home = { parent: this.frameEl.parentNode, next: this.frameEl.nextSibling };
    const stage = doc.createElement('div');
    stage.id = 'stage';
    stage.className = 'pip-stage';
    stage.appendChild(this.frameEl);
    doc.body.appendChild(stage);
    if (this.stripEl) {
      this._stripHome = { parent: this.stripEl.parentNode, next: this.stripEl.nextSibling };
      doc.body.appendChild(this.stripEl);
    }
    this._win = w;
    w.addEventListener('pagehide', () => this._restore(), { once: true });
    this.onOpen(w);
    return w;
  }

  close() {
    if (!this._win) return;
    const w = this._win;
    this._restore();
    try { w.close(); } catch { /* ignore */ }
  }

  _restore() {
    if (!this._win) return;
    this._win = null;
    const put = (el, home) => {
      if (!el || !home || !home.parent) return;
      const next = home.next && home.next.parentNode === home.parent ? home.next : null;
      home.parent.insertBefore(el, next);
    };
    put(this.frameEl, this._home);
    put(this.stripEl, this._stripHome);
    this._home = this._stripHome = null;
    this.onClose();
  }

  /** Fallback: positioned popup in the bottom-right corner (not always-on-top). */
  static openPopup(url) {
    const s = window.screen || {};
    const aw = s.availWidth || 1280, ah = s.availHeight || 800;
    const al = s.availLeft || 0, at = s.availTop || 0;
    const feat = `popup,width=${PIP_SIZE.width},height=${PIP_SIZE.height},left=${Math.max(0, al + aw - PIP_SIZE.width - 16)},top=${Math.max(0, at + ah - PIP_SIZE.height - 30)}`;
    return window.open(url, 'po-mini', feat);
  }
}
