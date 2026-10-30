// Pixel Office — Browser Notification Manager, Tab Title Approval Badge & Markdown Export (G3, §1.5).
// Security & Privacy constraints (AGENTS.md):
// 1. Notification.requestPermission() is ONLY invoked on an explicit user button click.
// 2. Notification title/body NEVER include prompt, command, project, or output text — only fixed generic strings.
// 3. All DOM rendering uses createElement + textContent only; never innerHTML.

import { PreviewDrawer } from './preview.js';
import { t } from './i18n.js';

export const NOTIFY_STORAGE_KEY = 'po-notify-enabled';
export const GENERIC_APPROVAL_TEXT = t('Onay bekliyor');
export const GENERIC_DONE_TEXT = t('Görev tamamlandı');
const DEFAULT_BASE_TITLE = 'Pixel Office';

function safeFileName(raw = 'cikti.md') {
  const cleaned = String(raw || 'cikti')
    .replace(/[^a-zA-Z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64) || 'cikti';
  return cleaned.endsWith('.md') ? cleaned : `${cleaned}.md`;
}

/**
 * Copy plain text to the clipboard without touching innerHTML.
 * @param {string} text
 * @param {{ clipboard?: { writeText?: (t: string) => Promise<void> } }} [opts]
 * @returns {Promise<boolean>}
 */
export async function copyText(text, { clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : null } = {}) {
  const str = String(text ?? '');
  if (clipboard && typeof clipboard.writeText === 'function') {
    try {
      await clipboard.writeText(str);
      return true;
    } catch {}
  }
  if (typeof document === 'undefined') return false;
  const ta = document.createElement('textarea');
  ta.value = str;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  ta.style.pointerEvents = 'none';
  document.body.appendChild(ta);
  try {
    ta.select();
    return Boolean(document.execCommand && document.execCommand('copy'));
  } catch {
    return false;
  } finally {
    ta.remove();
  }
}

/**
 * Download plain text as a `.md` file using Blob + object URL.
 * @param {string} text
 * @param {string} [filename]
 * @param {{ triggerDownload?: boolean }} [opts]
 */
export function downloadMarkdown(text, filename = 'cikti.md', { triggerDownload = true } = {}) {
  const str = String(text ?? '');
  const safeName = safeFileName(filename);
  const blob = new Blob([str], { type: 'text/markdown;charset=utf-8' });
  if (triggerDownload && typeof document !== 'undefined' && typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = safeName;
    a.hidden = true;
    document.body.appendChild(a);
    try {
      a.click();
    } finally {
      a.remove();
      setTimeout(() => { try { URL.revokeObjectURL(url); } catch {} }, 1000);
    }
  }
  return { filename: safeName, size: blob.size, blob };
}

/**
 * Build a DOM action bar with "Önizle", "Kopyala" and ".md indir" buttons (textContent only).
 * @param {() => string} getText
 * @param {string} [filenamePrefix]
 * @param {{ btnClass?: string, containerClass?: string }} [opts]
 */
export function makeExportActions(getText, filenamePrefix = 'cikti', {
  btnClass = 'btn small ghost',
  containerClass = 'export-actions',
} = {}) {
  const bar = document.createElement('div');
  bar.className = containerClass;

  const btnPreview = document.createElement('button');
  btnPreview.type = 'button';
  btnPreview.className = btnClass;
  btnPreview.dataset.action = 'preview';
  btnPreview.textContent = t('Önizle');

  const btnCopy = document.createElement('button');
  btnCopy.type = 'button';
  btnCopy.className = btnClass;
  btnCopy.dataset.action = 'copy';
  btnCopy.textContent = 'Kopyala';

  const btnDl = document.createElement('button');
  btnDl.type = 'button';
  btnDl.className = btnClass;
  btnDl.dataset.action = 'download-md';
  btnDl.textContent = t('.md indir');

  btnPreview.addEventListener('click', () => {
    const doc = bar.ownerDocument || document;
    let dlg = doc.getElementById('dlg-preview');
    if (!dlg && doc.body) {
      dlg = doc.createElement('dialog');
      dlg.id = 'dlg-preview';
      dlg.className = 'dlg wide preview-dlg';
      doc.body.appendChild(dlg);
    }
    if (!dlg) return;
    if (!dlg._previewDrawer) {
      dlg._previewDrawer = new PreviewDrawer({ dialog: dlg, doc });
    }
    dlg._previewDrawer.open({
      title: String(filenamePrefix || t('Eser Önizleme')),
      content: String(getText() ?? ''),
    });
  });

  let resetTimer = 0;
  btnCopy.addEventListener('click', async () => {
    const ok = await copyText(getText());
    btnCopy.textContent = ok ? t('Kopyalandı ✓') : t('Kopyalanamadı');
    if (resetTimer) clearTimeout(resetTimer);
    resetTimer = setTimeout(() => { btnCopy.textContent = 'Kopyala'; resetTimer = 0; }, 1500);
  });

  btnDl.addEventListener('click', () => {
    downloadMarkdown(getText(), `${filenamePrefix}.md`);
  });

  bar.append(btnPreview, btnCopy, btnDl);
  return bar;
}

export class Notifier {
  /**
   * @param {object} [opts]
   * @param {typeof Notification | null} [opts.NotificationImpl]
   * @param {string} [opts.baseTitle]
   * @param {() => boolean} [opts.isHidden]
   */
  constructor({
    NotificationImpl = typeof window !== 'undefined' && 'Notification' in window ? window.Notification : null,
    baseTitle = undefined,
    isHidden = () => (typeof document !== 'undefined' && (document.visibilityState === 'hidden' || (typeof document.hasFocus === 'function' && !document.hasFocus()))),
  } = {}) {
    this.NotificationImpl = NotificationImpl;
    this.baseTitle = baseTitle || (typeof document !== 'undefined' && document.title ? document.title.replace(/^\(\d+\s+Onay\)\s*/, '') : DEFAULT_BASE_TITLE) || DEFAULT_BASE_TITLE;
    this.isHidden = isHidden;
    this.sources = new Map();
    this.buttons = new Set();

    let saved = false;
    try {
      saved = typeof localStorage !== 'undefined' && localStorage.getItem(NOTIFY_STORAGE_KEY) === '1';
    } catch {}
    this._userEnabled = saved;
  }

  get supported() {
    return Boolean(this.NotificationImpl);
  }

  get permission() {
    return this.NotificationImpl ? this.NotificationImpl.permission : 'denied';
  }

  get enabled() {
    return Boolean(this._userEnabled && this.supported && this.permission === 'granted');
  }

  get totalApprovals() {
    let sum = 0;
    for (const v of this.sources.values()) sum += Math.max(0, Number(v) || 0);
    return sum;
  }

  /** Update pending approval count for a given source ('approvals' | 'boss') and refresh document.title. */
  setSourceCount(source, count) {
    const n = Math.max(0, Math.floor(Number(count) || 0));
    if (n === 0) this.sources.delete(source);
    else this.sources.set(source, n);
    this._syncTitle();
  }

  _syncTitle() {
    if (typeof document === 'undefined') return;
    const currentClean = document.title ? document.title.replace(/^\(\d+\s+Onay\)\s*/, '') : '';
    if (currentClean) this.baseTitle = currentClean;
    const total = this.totalApprovals;
    document.title = total > 0 ? `(${total} Onay) ${this.baseTitle}` : this.baseTitle;
  }

  /**
   * Explicitly called ONLY from a user click handler on a notification toggle button.
   * Requests browser notification permission if needed and toggles user preference.
   */
  async toggleFromUser() {
    if (!this.supported) return false;
    if (this.enabled) {
      this._userEnabled = false;
      try { localStorage.setItem(NOTIFY_STORAGE_KEY, '0'); } catch {}
      this._syncButtons();
      return false;
    }
    let perm = this.permission;
    if (perm === 'default' && typeof this.NotificationImpl.requestPermission === 'function') {
      perm = await this.NotificationImpl.requestPermission();
    }
    this._userEnabled = perm === 'granted';
    try { localStorage.setItem(NOTIFY_STORAGE_KEY, this._userEnabled ? '1' : '0'); } catch {}
    this._syncButtons();
    return this.enabled;
  }

  /** Create and bind a toggle button (textContent only). */
  createToggleButton(cls = 'btn small ghost apv-notify') {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = cls;
    b.dataset.notifyToggle = '1';
    b.addEventListener('click', () => { this.toggleFromUser(); });
    this.buttons.add(b);
    this._syncButton(b);
    return b;
  }

  _syncButtons() {
    for (const b of this.buttons) this._syncButton(b);
  }

  _syncButton(b) {
    if (!this.supported) {
      b.hidden = true;
      return;
    }
    b.hidden = false;
    if (this.permission === 'denied') {
      b.textContent = t('Bildirim engelli');
      b.disabled = true;
    } else if (this.enabled) {
      b.textContent = t('Bildirim açık');
      b.disabled = false;
    } else {
      b.textContent = t('Bildirim aç');
      b.disabled = false;
    }
  }

  /**
   * Fire a generic approval notification. Any arguments passed are intentionally ignored
   * so prompt/command/summary text can NEVER leak into OS notifications.
   */
  notifyApproval() {
    this._send(GENERIC_APPROVAL_TEXT, 'po-approval');
  }

  /**
   * Fire a generic completion notification. Any arguments passed are intentionally ignored
   * so prompt/output/report text can NEVER leak into OS notifications.
   */
  notifyDone() {
    this._send(GENERIC_DONE_TEXT, 'po-done');
  }

  _send(genericBody, tag) {
    if (!this.enabled) return null;
    if (this.isHidden && !this.isHidden()) return null;
    const safeBody = genericBody === GENERIC_DONE_TEXT ? GENERIC_DONE_TEXT : GENERIC_APPROVAL_TEXT;
    try {
      return new this.NotificationImpl(DEFAULT_BASE_TITLE, {
        body: safeBody,
        tag,
      });
    } catch {
      return null;
    }
  }
}

export const defaultNotifier = new Notifier();

