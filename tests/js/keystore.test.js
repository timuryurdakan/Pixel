// E4b (headless Chrome, tests/test_relay_js.py): the browser's API key store (docs/FAZ3_TASARIM.md D3, §3.2).
// Real WebCrypto and a real IndexedDB database with a test-only name. Default: memory only (gone with the page);
// "remember" keeps an AES-GCM ciphertext whose key is a non-extractable CryptoKey. Providers and keys are allow-listed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { KeyStore, IdbKeyBackend, PROVIDERS, keyHint, KeyStoreError } from '../../web/js/keystore.js';

const CANARY = 'sk-CANARY-e4b-0123456789abcdef';
let dbSeq = 0;
const freshBackend = () => new IdbKeyBackend(`pixel-office-keys-test-${Date.now()}-${dbSeq++}`);

async function rawRecords(backend) {
  return backend.dump();
}

test('providers are the fixed D4 list', () => {
  assert.deepEqual([...PROVIDERS], ['gemini', 'groq', 'openrouter', 'openai', 'deepseek', 'mistral']);
});

test('default: memory only — nothing reaches IndexedDB, a new store (page reload) has no key', async () => {
  const backend = freshBackend();
  const ks = new KeyStore({ backend });
  await ks.set('groq', CANARY);
  assert.equal(await ks.get('groq'), CANARY);
  assert.deepEqual(await rawRecords(backend), []);
  assert.equal(await new KeyStore({ backend }).get('groq'), null);
});

test('remember: only a ciphertext is stored, the wrapping key is non-extractable, a reload can decrypt', async () => {
  const backend = freshBackend();
  await new KeyStore({ backend }).set('gemini', CANARY, { remember: true });
  const recs = await rawRecords(backend);
  const text = JSON.stringify(recs, (k, v) => (v instanceof Uint8Array ? [...v] : v));
  assert.ok(!text.includes(CANARY));
  assert.ok(!text.includes('CANARY'));
  const wrap = recs.find((r) => r.key === 'wrap');
  assert.ok(wrap && wrap.value instanceof CryptoKey);
  assert.equal(wrap.value.extractable, false);
  assert.deepEqual([...wrap.value.usages].sort(), ['decrypt', 'encrypt']);
  await assert.rejects(crypto.subtle.exportKey('raw', wrap.value));
  assert.equal(await new KeyStore({ backend }).get('gemini'), CANARY);
});

test('a ciphertext moved to another provider does not decrypt (provider is the AAD)', async () => {
  const backend = freshBackend();
  await new KeyStore({ backend }).set('openai', CANARY, { remember: true });
  const rec = (await rawRecords(backend)).find((r) => r.key === 'k:openai');
  await backend.put('k:groq', rec.value);
  assert.equal(await new KeyStore({ backend }).get('groq'), null);
});

test('forget removes memory and stored copies; set without remember drops an older stored copy', async () => {
  const backend = freshBackend();
  const ks = new KeyStore({ backend });
  await ks.set('mistral', CANARY, { remember: true });
  await ks.forget('mistral');
  assert.equal(await ks.get('mistral'), null);
  assert.equal(await new KeyStore({ backend }).get('mistral'), null);
  await ks.set('mistral', CANARY, { remember: true });
  await ks.set('mistral', `${CANARY}x`);
  assert.equal(await new KeyStore({ backend }).get('mistral'), null);
  assert.equal(await ks.get('mistral'), `${CANARY}x`);
  await ks.set('groq', CANARY, { remember: true });
  await ks.forgetAll();
  assert.deepEqual((await rawRecords(backend)).map((r) => r.key), []);
});

test('unknown providers and malformed keys are refused without echoing the key', async () => {
  const ks = new KeyStore({ backend: freshBackend() });
  for (const p of ['', 'anthropic', 'Groq', 'http://x', null, 1, '__proto__']) {
    await assert.rejects(ks.set(p, CANARY), (e) => e instanceof KeyStoreError && e.code === 'bad_provider');
    await assert.rejects(ks.get(p), (e) => e instanceof KeyStoreError && e.code === 'bad_provider');
  }
  for (const k of ['', 'short', 'a b c d e f g h', `${CANARY}\n`, 'ş'.repeat(20), 'x'.repeat(513), null, 42]) {
    await assert.rejects(ks.set('groq', k), (e) => {
      assert.ok(e instanceof KeyStoreError && e.code === 'bad_key');
      assert.ok(!String(e.message).includes(String(k)) || String(k).length < 8);
      return true;
    });
  }
});

test('hint shows only the last four characters', async () => {
  assert.equal(keyHint(CANARY), '…cdef');
  assert.equal(keyHint(null), '');
  const ks = new KeyStore({ backend: freshBackend() });
  await ks.set('openrouter', CANARY);
  assert.equal(await ks.hint('openrouter'), '…cdef');
  assert.equal(await ks.hint('groq'), '');
});

test('the key never appears in errors, JSON or string forms of the store', async () => {
  const ks = new KeyStore({ backend: freshBackend() });
  await ks.set('deepseek', CANARY, { remember: true });
  assert.ok(!JSON.stringify(ks).includes(CANARY));
  assert.ok(!String(ks).includes(CANARY));
  assert.ok(!document.body.textContent.includes(CANARY));
});

test('without IndexedDB, remember fails closed and memory still works', async () => {
  const ks = new KeyStore({ backend: new IdbKeyBackend('x', null) });
  await assert.rejects(ks.set('groq', CANARY, { remember: true }), (e) => e.code === 'storage_unavailable');
  assert.equal(await ks.get('groq'), null);
  await ks.set('groq', CANARY);
  assert.equal(await ks.get('groq'), CANARY);
});
