// Tiny IndexedDB wrapper for v3 E2E state (V3.md §7.2). Database "pixel-office", stores:
//   e2e      key → value   ("browserKey": {privateKey: CryptoKey (non-extractable), pub: Uint8Array(65), bid})
//   devices  deviceId → {deviceId, pub (base64url), name, pairedAt, room}
// CryptoKey objects are structured-cloneable, so a non-extractable private key can be stored and used later
// without its bytes ever being readable by script.

const DB_NAME = 'pixel-office';
const DB_VERSION = 1;
export const STORES = ['e2e', 'devices'];

let dbPromise = null;

export function openDb(name = DB_NAME, factory = globalThis.indexedDB) {
  if (!factory) return Promise.reject(new Error('IndexedDB unavailable'));
  if (name === DB_NAME && dbPromise) return dbPromise;
  const p = new Promise((resolve, reject) => {
    const req = factory.open(name, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      for (const s of STORES) if (!db.objectStoreNames.contains(s)) db.createObjectStore(s);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('IndexedDB open failed'));
    req.onblocked = () => reject(new Error('IndexedDB blocked'));
  });
  if (name === DB_NAME) {
    dbPromise = p;
    p.catch(() => { dbPromise = null; });
  }
  return p;
}

function tx(db, store, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const s = t.objectStore(store);
    let result;
    const req = fn(s);
    if (req) req.onsuccess = () => { result = req.result; };
    t.oncomplete = () => resolve(result);
    t.onerror = () => reject(t.error || new Error('IndexedDB error'));
    t.onabort = () => reject(t.error || new Error('IndexedDB aborted'));
  });
}

/** Key/value store bound to one IndexedDB database. */
export class IdbStore {
  constructor(name = DB_NAME, factory = globalThis.indexedDB) {
    this.name = name;
    this.factory = factory;
  }
  db() { return openDb(this.name, this.factory); }
  async get(store, key) { return tx(await this.db(), store, 'readonly', (s) => s.get(key)); }
  async put(store, key, value) { await tx(await this.db(), store, 'readwrite', (s) => s.put(value, key)); }
  async del(store, key) { await tx(await this.db(), store, 'readwrite', (s) => s.delete(key)); }
  async all(store) { return (await tx(await this.db(), store, 'readonly', (s) => s.getAll())) || []; }
  async clear(store) { await tx(await this.db(), store, 'readwrite', (s) => s.clear()); }
}

/** In-memory twin (tests, private windows where IndexedDB is unavailable). */
export class MemoryStore {
  constructor() { this.data = new Map(STORES.map((s) => [s, new Map()])); }
  async get(store, key) { return this.data.get(store).get(key); }
  async put(store, key, value) { this.data.get(store).set(key, value); }
  async del(store, key) { this.data.get(store).delete(key); }
  async all(store) { return [...this.data.get(store).values()]; }
  async clear(store) { this.data.get(store).clear(); }
}
