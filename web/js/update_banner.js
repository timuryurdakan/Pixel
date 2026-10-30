// Connector 3.5.0: a dismissible strip above the office when a paired, connected computer runs an outdated connector
// (devices.js connectorOutdated). 3.5.0+ connectors get `pixel-connector update`; older ones have no such command, so
// the strip points to the install command (Cihazlar → Cihaz ekle). Only in a v3 room with a paired device: viewers,
// unpaired browsers and the mini window never build it. Text only (textContent); the dismissal is remembered per
// published SHA-256 in localStorage, so the next release shows the strip again.

import { connectorOutdated, publishedConnectorSha, hasUpdateCommand, UPDATE_CMD } from './devices.js';
import { t } from './i18n.js';

export const DISMISS_KEY = 'po-update-dismissed';
export const BANNER_TITLE = t('Bilgisayarınızdaki bağlayıcı eski.');
export const TEXT_CMD = t('Güncellemek için o bilgisayarda şunu çalıştırın:');
export const TEXT_INSTALL = t('Bu sürümde güncelleme komutu yok: Cihazlar → Cihaz ekle\'deki kurulum komutunu o '
  + 'bilgisayarda bir kez yeniden çalıştırın (ayarlar ve eşleşmeler korunur).');
const NO_SHA = 'min';                  // dismissal key when SHA256SUMS is unavailable (a pre-3.1.0 connector)
const MAX_NAMES = 3;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

/** localStorage that never throws (private mode, blocked storage). */
export const safeStorage = {
  get(key) { try { return window.localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { window.localStorage.setItem(key, value); } catch { /* not remembered */ } },
};

/** Connected devices whose connector is older than the published one. */
export function outdatedDevices(devices, publishedSha) {
  return [...devices].filter((d) => d && d.ready && connectorOutdated(d.info, publishedSha));
}

/** What the strip says for these devices: the update command only when every one of them has it. */
export function bannerModel(list) {
  if (!list.length) return null;
  const names = list.slice(0, MAX_NAMES).map((d) => String(d.label || '').slice(0, 32)).filter(Boolean);
  const more = list.length > MAX_NAMES ? ` +${list.length - MAX_NAMES}` : '';
  return { useCommand: list.every((d) => hasUpdateCommand(d.info)), devices: names.join(', ') + more };
}

export class UpdateBanner {
  /**
   * @param {object} o
   * @param {HTMLElement} o.root                  #update-banner (hidden until needed)
   * @param {import('./control.js').DeviceHub} o.hub
   * @param {(text:string) => Promise<boolean>} o.copy
   * @param {Function} o.toast
   * @param {() => void} o.onInstall             opens Cihazlar → Cihaz ekle (install command)
   * @param {() => void} [o.onLayout]            the strip appeared or went away (refit the canvas)
   */
  constructor({ root, hub, copy, toast, onInstall, onLayout = () => {}, storage = safeStorage,
    shaFn = publishedConnectorSha }) {
    Object.assign(this, { root, hub, copy, toast, onInstall, onLayout, storage, shaFn });
    this.sha = undefined;
    this.key = '';
    hub.on('change', () => this.refresh());
  }

  async refresh() {
    if (this.sha === undefined) this.sha = await this.shaFn();
    const model = bannerModel(outdatedDevices(this.hub.devices.values(), this.sha));
    this.key = this.sha || NO_SHA;
    if (!model || this.storage.get(DISMISS_KEY) === this.key) { this._show(false); return; }
    this._render(model);
    this._show(true);
  }

  dismiss() {
    this.storage.set(DISMISS_KEY, this.key);
    this._show(false);
  }

  _show(on) {
    if (this.root.hidden === !on) return;
    this.root.hidden = !on;
    document.body.classList.toggle('update-banner-on', on);
    this.onLayout();
  }

  _render(model) {
    const text = el('p', 'ub-text');
    text.setAttribute('role', 'status');
    text.append(el('b', 'ub-title', BANNER_TITLE));
    if (model.devices) text.append(' ', el('span', 'ub-dev', `(${model.devices})`));
    text.append(' ', el('span', null, model.useCommand ? TEXT_CMD : TEXT_INSTALL));
    const actions = el('div', 'ub-actions');
    if (model.useCommand) {
      const copyBtn = el('button', 'btn small ub-copy', t('Kopyala'));
      copyBtn.type = 'button';
      copyBtn.setAttribute('aria-label', t('Komutu kopyala: {cmd}', { cmd: UPDATE_CMD }));
      copyBtn.addEventListener('click', async () => {
        const ok = await this.copy(UPDATE_CMD);
        this.toast(ok ? t('Komut kopyalandı') : t('Kopyalanamadı, metni elle seçin'), { error: !ok });
      });
      actions.append(el('code', 'ub-cmd', UPDATE_CMD), copyBtn);
    } else {
      const go = el('button', 'btn small accent ub-install', t('Kurulum komutunu göster'));
      go.type = 'button';
      go.addEventListener('click', () => this.onInstall());
      actions.append(go);
    }
    const close = el('button', 'btn small ghost ub-close', '×');
    close.type = 'button';
    close.title = 'Kapat';
    close.setAttribute('aria-label', t('Güncelleme uyarısını kapat'));
    close.addEventListener('click', () => this.dismiss());
    actions.append(close);
    this.root.replaceChildren(text, actions);
  }
}
