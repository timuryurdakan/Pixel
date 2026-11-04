// 2026-11-04 (headless Chrome, tests/test_relay_js.py): a new Gemini key got "Model cihazda yüklü değil": the default
// model was closed to new accounts and the provider's 404 borrowed Ollama's text. The preset lists hold no retired
// model, saved agents move to the replacement, and a provider 404 has its own message.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MemoryStore } from '../../web/js/idb.js';
import { errorText } from '../../web/js/control.js';
import { BrowserDevice, PROVIDER_PRESETS, RETIRED_MODELS, createMemoryKeystore } from '../../web/js/browser_device.js';

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
});
