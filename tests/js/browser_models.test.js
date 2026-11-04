// 2026-11-04 (headless Chrome, tests/test_relay_js.py): a new Gemini key got "Model cihazda yüklü değil": the default
// model was closed to new accounts and the provider's 404 borrowed Ollama's text. The preset lists hold no retired
// model, saved agents move to the replacement, and a provider 404 has its own message.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MemoryStore } from '../../web/js/idb.js';
import { errorText } from '../../web/js/control.js';
import { endText } from '../../web/js/prompt.js';
import { BrowserDevice, BrowserDevicePanel, PROVIDER_PRESETS, RETIRED_MODELS, createMemoryKeystore } from '../../web/js/browser_device.js';

test('no preset offers a retired model; every replacement is on its provider list', () => {
  const offered = Object.values(PROVIDER_PRESETS).flatMap((p) => p.models);
  for (const [old, next] of Object.entries(RETIRED_MODELS)) {
    assert.ok(!offered.includes(old), old);
    assert.ok(offered.includes(next), next);
  }
  assert.equal(PROVIDER_PRESETS.gemini.models[0], 'gemini-3.5-flash-lite');
});

test('a saved agent moves to the replacement; a current or unknown model stays', async () => {
  const store = new MemoryStore();
  const agent = (key, engineId, model) => ({ key, name: key, engineId, model, profile: 'edit' });
  await store.put('e2e', 'browserAgents:browser', [agent('eski', 'gemini', 'gemini-2.5-flash'),
    agent('kalan', 'gemini', 'gemini-2.5-flash-lite'), agent('ozel', 'groq', 'benim-modelim')]);
  const dev = new BrowserDevice({ keystore: createMemoryKeystore(), store });
  await dev._loadAgents();
  assert.equal(dev.agents.get('eski').model, 'gemini-3.5-flash-lite');
  assert.equal(dev.agents.get('kalan').model, 'gemini-2.5-flash-lite');
  assert.equal(dev.agents.get('ozel').model, 'benim-modelim');
});

test('a provider 404 ends the task with its own code and text, not "yüklü değil"', async () => {
  const keystore = createMemoryKeystore();
  await keystore.set('gemini', 'sk-CANARY-models-0123456789', { remember: false });
  const fetchFn = async () => ({ status: 404, headers: { get: () => 'application/json' },
    text: async () => JSON.stringify({ error: { message: 'no longer available' } }) });
  const dev = new BrowserDevice({ keystore, store: new MemoryStore(), fetchFn });
  const r = await dev._runBossTask({ agentKey: 'wf-a', name: 'A', role: 'A', profile: 'edit', prompt: 'x', fresh: true,
    provider: 'gemini', model: 'gemini-2.5-flash' });
  assert.equal(r.status, 'error');
  assert.equal(r.error.code, 'model_unavailable');
  assert.ok(!errorText('model_unavailable').includes('yüklü değil'));
  assert.ok(errorText('model_unavailable').includes('Sağlayıcı'));
  const line = endText({ status: 'error', error: r.error });
  assert.ok(line.includes('no longer available'), line);                    // the provider's own words are visible
  assert.ok(!line.includes('CANARY'));
});

test('no key (a page reload clears a key not remembered) says so, not "motor bulunamadı"', async () => {
  const dev = new BrowserDevice({ keystore: createMemoryKeystore(), store: new MemoryStore(),
    fetchFn: async () => { throw new Error('must not call the provider without a key'); } });
  const r = await dev._runBossTask({ agentKey: 'wf-a', name: 'A', role: 'A', profile: 'edit', prompt: 'x', fresh: true,
    provider: 'gemini', model: 'gemini-3.5-flash-lite' });
  assert.equal(r.error.code, 'api_key_missing');
  assert.match(endText({ status: 'error', error: r.error }), /API anahtarı yok/);
  assert.equal(endText({ status: 'error', error: { code: 'engine_error' } }).includes('('), false);
});

test('"Bu cihazda hatırla" is a normal checkbox next to its text (it sat apart, stretched, and nobody saw it)', async () => {
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = '/web/css/style.css';
  const loaded = new Promise((r) => { link.onload = r; });
  document.head.appendChild(link);
  await loaded;
  const box = document.createElement('div');
  box.style.width = '600px';
  document.body.appendChild(box);
  try {
    const dev = new BrowserDevice({ keystore: createMemoryKeystore(), store: new MemoryStore() });
    await new BrowserDevicePanel({ container: box, device: dev }).render();
    const input = box.querySelector('input[name=remember]');
    const text = input.closest('label').querySelector('span');
    const a = input.getBoundingClientRect();
    const b = text.getBoundingClientRect();
    assert.ok(a.width < 40, `checkbox width ${a.width}`);
    assert.ok(a.right <= b.left && b.left - a.right < 30, `gap ${b.left - a.right}`);
    assert.ok(a.top < b.bottom && b.top < a.bottom, 'on the same line as its text');
    assert.ok(a.left - box.getBoundingClientRect().left < 30, 'at the start of the row');
  } finally {
    box.remove();
    link.remove();
  }
});
