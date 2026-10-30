// Pixel Office — the browser's API keys for the browser device (E4b, docs/FAZ3_TASARIM.md D3, §3.2).
//
// Default: a key lives only in this page's memory and is gone when the tab closes. With {remember: true} it is kept
// in IndexedDB as an AES-GCM ciphertext; the AES key is a non-extractable CryptoKey stored next to it, so script can
// use it but never read its bytes, and the provider name is the additional data (a ciphertext moved to another
// provider does not open). This protects the copy on disk, not against script running in this page: the defence
// there is no XSS (textContent only, sandboxed previews, CSP).
//
// A key never goes into a message, a log, an error text, a URL or localStorage. Errors carry a code only.
// Its own database ("pixel-office-keys"), so the E2E database's version and stores stay untouched.

export const PROVIDERS = Object.freeze(['gemini', 'groq', 'openrouter', 'openai', 'deepseek', 'mistral']);
const KEY_RE = /^[\x21-\x7e]{8,512}$/;          // printable ASCII, no spaces: every listed provider's key format
const DB_NAME = 'pixel-office-keys';
const STORE = 'keys';
const WRAP = 'wrap';
const IV_BYTES = 12;

export class KeyStoreError extends Error {
  constructor(code) { super(code); this.code = code; }
}

function checkProvider(p) {
  if (typeof p !== 'string' || !PROVIDERS.includes(p)) throw new KeyStoreError('bad_provider');
}

/** "…abcd" for a settings row; never more of the key. */
export function keyHint(key) {
  return typeof key === 'string' && key.length >= 8 ? `…${key.slice(-4)}` : '';
}

/** IndexedDB key/value backend (one object store). `factory` null = no IndexedDB (private windows, tests). */
export class IdbKeyBackend {
  constructor(name = DB_NAME, factory = globalThis.indexedDB) {
    this.name = name;
    this.factory = factory;
    this._db = null;
  }

  _open() {
    if (!this.factory) return Promise.reject(new KeyStoreError('storage_unavailable'));
    if (!this._db) {
      this._db = new Promise((resolve, reject) => {
        const req = this.factory.open(this.name, 1);
        req.onupgradeneeded = () => { req.result.createObjectStore(STORE); };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(new KeyStoreError('storage_unavailable'));
        req.onblocked = () => reject(new KeyStoreError('storage_unavailable'));
      });
      this._db.catch(() => { this._db = null; });
    }
    return this._db;
  }

  async _tx(mode, fn) {
    const db = await this._open();
    return new Promise((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      let result;
      const req = fn(t.objectStore(STORE));
      if (req) req.onsuccess = () => { result = req.result; };
      t.oncomplete = () => resolve(result);
      t.onerror = () => reject(new KeyStoreError('storage_error'));
      t.onabort = () => reject(new KeyStoreError('storage_error'));
    });
  }

  get(k) { return this._tx('readonly', (s) => s.get(k)); }
  put(k, v) { return this._tx('readwrite', (s) => s.put(v, k)); }
  del(k) { return this._tx('readwrite', (s) => s.delete(k)); }
  clear() { return this._tx('readwrite', (s) => s.clear()); }

  /** [{key, value}] of every record (tests and the "forget all" check). */
  async dump() {
    const db = await this._open();
    return new Promise((resolve, reject) => {
      const out = [];
      const req = db.transaction(STORE, 'readonly').objectStore(STORE).openCursor();
      req.onsuccess = () => {
        const c = req.result;
        if (!c) { resolve(out); return; }
        out.push({ key: c.key, value: c.value });
        c.continue();
      };
      req.onerror = () => reject(new KeyStoreError('storage_error'));
    });
  }
}

export class KeyStore {
  /** @param {{backend?: IdbKeyBackend, subtle?: SubtleCrypto}} [o] */
  constructor({ backend = new IdbKeyBackend(), subtle = globalThis.crypto && globalThis.crypto.subtle } = {}) {
    // Private fields: JSON.stringify / String() / a debugger's shallow view of the instance never show the keys.
    this.#backend = backend;
    this.#subtle = subtle;
  }

  #backend;
  #subtle;
  #memory = new Map();

  toJSON() { return { providers: [...this.#memory.keys()] }; }
  toString() { return '[KeyStore]'; }

  /** The key for `provider`, or null. A remembered key is decrypted once and then kept in memory. */
  async get(provider) {
    checkProvider(provider);
    if (this.#memory.has(provider)) return this.#memory.get(provider);
    let rec;
    try {
      rec = await this.#backend.get(`k:${provider}`);
    } catch {
      return null;
    }
    if (!rec || !(rec.iv instanceof Uint8Array) || !(rec.ct instanceof ArrayBuffer || rec.ct instanceof Uint8Array)) return null;
    try {
      const wrap = await this.#backend.get(WRAP);
      if (!wrap) return null;
      const plain = await this.#subtle.decrypt({ name: 'AES-GCM', iv: rec.iv, additionalData: new TextEncoder().encode(provider) },
        wrap, rec.ct);
      const key = new TextDecoder().decode(plain);
      if (!KEY_RE.test(key)) return null;
      this.#memory.set(provider, key);
      return key;
    } catch {
      return null;                      // wrong provider (AAD), tampered record or lost wrapping key: no key
    }
  }

  /** Keep `key` for `provider`. remember=false (default) also deletes an older remembered copy. */
  async set(provider, key, { remember = false } = {}) {
    checkProvider(provider);
    if (typeof key !== 'string' || !KEY_RE.test(key)) throw new KeyStoreError('bad_key');
    if (remember) {
      const wrap = await this.#wrapKey();
      const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
      const ct = await this.#subtle.encrypt({ name: 'AES-GCM', iv, additionalData: new TextEncoder().encode(provider) },
        wrap, new TextEncoder().encode(key));
      await this.#backend.put(`k:${provider}`, { iv, ct });
    } else {
      await this.#dropStored(provider);
    }
    this.#memory.set(provider, key);
  }

  async forget(provider) {
    checkProvider(provider);
    this.#memory.delete(provider);
    await this.#dropStored(provider);
  }

  async forgetAll() {
    this.#memory.clear();
    try { await this.#backend.clear(); } catch (e) { if (e.code !== 'storage_unavailable') throw e; }
  }

  async hint(provider) {
    return keyHint(await this.get(provider));
  }

  async #dropStored(provider) {
    try {
      await this.#backend.del(`k:${provider}`);
    } catch (e) {
      if (e.code !== 'storage_unavailable') throw e;    // nothing was ever stored without IndexedDB
    }
  }

  async #wrapKey() {
    let wrap = await this.#backend.get(WRAP);
    if (!wrap) {
      wrap = await this.#subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
      await this.#backend.put(WRAP, wrap);
    }
    return wrap;
  }
}
