// E4a (Faz 3, docs/FAZ3_TASARIM.md §3): In-browser virtual device (web/js/browser_device.js).
// Tests cover D1-D7 and Rules 1-6:
// 1. Local attachment to DeviceHub with zero relay/Vercel/room traffic (D6).
// 2. Fixed provider allow-list (Gemini, Groq, OpenRouter, OpenAI, DeepSeek, Mistral) matching config.py (D4, D5).
// 3. Keystore interface stubbing & canary key isolation ("sk-CANARY-...") from DOM, hub messages, URLs, storage (D3).
// 4. Virtual workspace in IndexedDB (idb.js) with splitPath parity and 200 files / 20 MB / 256 KB / 512 KB caps (D7).
// 5. Tool loop (_tool_loop parity), RESULT_HEAD framing, and 20 turns / 40 tool calls / 8000 out tokens / 30 tasks/hr caps (D7).
// 6. Model output and virtual file preview rendered only via textContent or PreviewDrawer sandbox="" iframe.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MemoryStore } from '../../web/js/idb.js';
import { DeviceHub, ControlError } from '../../web/js/control.js';
import { PromptPanel } from '../../web/js/prompt.js';
import { PreviewDrawer } from '../../web/js/preview.js';
import {
  PROVIDER_PRESETS,
  ALLOWED_PROVIDERS,
  READ_MAX,
  WRITE_MAX,
  MAX_FILES,
  MAX_TOTAL_BYTES,
  MAX_TOOL_CALLS,
  MAX_TOOL_TURNS,
  MAX_OUTPUT_TOKENS,
  MAX_TASKS_PER_HOUR,
  RESULT_HEAD,
  ToolError,
  splitPath,
  deniedName,
  toolSpecs,
  VirtualWorkspace,
  BrowserDevice,
  BrowserDevicePanel,
  createMemoryKeystore,
} from '../../web/js/browser_device.js';

const CANARY_KEY = 'sk-CANARY-9876543210-ULTRA-SECRET-TOKEN';
const CANARY_TAIL = '9876543210-ULTRA-SECRET-TOKEN';
const XSS_PAYLOAD = '<img src=x onerror="window.__pwnedE4a=1"><script>window.__pwnedE4a=2</script>';

function openaiReply({ content = '', calls = [], promptTokens = 12, completionTokens = 8 } = {}) {
  const message = { role: 'assistant', content };
  if (calls.length) {
    message.tool_calls = calls.map(([name, args], i) => ({
      id: `call_${i}`,
      type: 'function',
      function: {
        name,
        arguments: typeof args === 'string' ? args : JSON.stringify(args),
      },
    }));
  }
  return {
    status: 200,
    body: {
      choices: [{ message }],
      usage: { prompt_tokens: promptTokens, completion_tokens: completionTokens },
    },
  };
}

function makeFakeFetch(scriptedReplies) {
  const queue = [...scriptedReplies];
  const calls = [];
  const fetchFn = async (url, init = {}) => {
    const bodyText = init.body ? String(init.body) : '';
    let parsedBody = null;
    try { parsedBody = bodyText ? JSON.parse(bodyText) : null; } catch {}
    calls.push({
      url: String(url),
      method: init.method || 'GET',
      headers: { ...(init.headers || {}) },
      body: parsedBody,
      rawBody: bodyText,
    });
    const next = queue.length ? queue.shift() : { status: 500, body: { error: { message: 'no scripted reply' } } };
    const resolved = typeof next === 'function' ? await next(url, init, parsedBody) : next;
    const status = resolved.status || 200;
    const isSse = Boolean(resolved.sse);
    const respText = isSse
      ? resolved.sse
      : (typeof resolved.body === 'string' ? resolved.body : JSON.stringify(resolved.body ?? {}));
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: {
        get(name) {
          if (String(name).toLowerCase() === 'content-type') {
            return isSse ? 'text/event-stream' : 'application/json';
          }
          return null;
        },
      },
      async text() { return respText; },
      async json() { return JSON.parse(respText); },
    };
  };
  fetchFn.calls = calls;
  return fetchFn;
}

const until = async (cond, ms = 2000) => {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error('condition timeout');
    await new Promise((r) => setTimeout(r, 10));
  }
};

test('E4a — PROVIDER_PRESETS matches pixel_connector/config.py (D4, D5: 6 https providers, no localhost/Ollama/custom URL)', () => {
  assert.deepEqual(ALLOWED_PROVIDERS, ['gemini', 'groq', 'openrouter', 'openai', 'deepseek', 'mistral']);
  assert.equal(PROVIDER_PRESETS.gemini.url, 'https://generativelanguage.googleapis.com/v1beta/openai');
  assert.equal(PROVIDER_PRESETS.groq.url, 'https://api.groq.com/openai/v1');
  assert.equal(PROVIDER_PRESETS.openrouter.url, 'https://openrouter.ai/api/v1');
  assert.equal(PROVIDER_PRESETS.openai.url, 'https://api.openai.com/v1');
  assert.equal(PROVIDER_PRESETS.deepseek.url, 'https://api.deepseek.com/v1');
  assert.equal(PROVIDER_PRESETS.mistral.url, 'https://api.mistral.ai/v1');

  for (const p of Object.values(PROVIDER_PRESETS)) {
    assert.ok(p.url.startsWith('https://'), `${p.id} must use https://`);
    assert.ok(!p.url.includes('127.0.0.1') && !p.url.includes('localhost'));
    assert.ok(Array.isArray(p.models) && p.models.length >= 1);
  }
});

test('E4a — splitPath and deniedName match pixel_connector/engines/tools.py', () => {
  assert.deepEqual(splitPath('docs/a.md'), ['docs', 'a.md']);
  assert.deepEqual(splitPath('docs/'), ['docs']);
  assert.deepEqual(splitPath(null, true), []);
  assert.deepEqual(splitPath('.', true), []);
  assert.deepEqual(splitPath('', true), []);

  const badPaths = [
    '', ' ', '/etc/passwd', '../x', 'docs/../../x', 'a//b', './a',
    'C:\\x', 'C:/x', 'a\\b', '~/x', 'a\u202eb', 'a\nb', 'a\0b',
    'x'.repeat(401), 1, null, ['a'],
  ];
  for (const bad of badPaths) {
    assert.throws(
      () => splitPath(bad),
      (err) => err instanceof ToolError && err.code === 'bad_path',
      `expected bad_path for ${JSON.stringify(bad)}`,
    );
  }

  const deniedPaths = [
    '.git/config', 'src/.git/hooks/pre-commit', '.claude/settings.json',
    '.mcp.json', '.vscode/x.json', 'a.code-workspace', '.envrc', '.env',
    '.env.local', 'conf/.ENV', 'keys/server.pem', 'id_rsa', 'id_ed25519.pub',
    '.npmrc', '.bashrc', '.IDEA/x', 'x.KEY', '.pixel-office/keys.json',
  ];
  for (const bad of deniedPaths) {
    assert.throws(
      () => splitPath(bad),
      (err) => err instanceof ToolError && err.code === 'denied',
      `expected denied for ${bad}`,
    );
  }

  assert.deepEqual(splitPath('docs/environment.md'), ['docs', 'environment.md']);
  assert.deepEqual(splitPath('keyboard.md'), ['keyboard.md']);
  assert.equal(deniedName('.env'), true);
  assert.equal(deniedName('environment.md'), false);
});

test('E4a — VirtualWorkspace in IndexedDB (idb.js): list_dir, read_file, write_file, readonly profile, and size/count caps (200 files, 20 MB, 256 KB, 512 KB)', async () => {
  assert.equal(READ_MAX, 256 * 1024);
  assert.equal(WRITE_MAX, 512 * 1024);
  assert.equal(MAX_FILES, 200);
  assert.equal(MAX_TOTAL_BYTES, 20 * 1024 * 1024);

  const store = new MemoryStore();
  const ws = new VirtualWorkspace({ store });

  await ws.writeFile('docs/a.md', '# Başlık\nmerhaba', 'edit');
  assert.equal(await ws.listDir(), 'docs/');
  assert.equal(await ws.listDir('docs'), 'a.md');
  assert.equal(await ws.readFile('docs/a.md'), '# Başlık\nmerhaba');

  const writeRes = await ws.writeFile('yeni/alt/b.md', 'içerik', 'edit');
  assert.equal(writeRes, `${new TextEncoder().encode('içerik').byteLength} bayt yazıldı`);

  // Persisted in store: a fresh VirtualWorkspace instance over the same store reads the files
  const ws2 = new VirtualWorkspace({ store });
  assert.equal(await ws2.readFile('yeni/alt/b.md'), 'içerik');
  assert.equal(await ws2.listDir(), 'docs/\nyeni/');

  // Readonly profile cannot write
  await assert.rejects(
    () => ws.writeFile('x.md', 'x', 'readonly'),
    (err) => err instanceof ToolError && err.code === 'read_only',
  );
  assert.equal(toolSpecs('readonly').some((s) => s.function.name === 'write_file'), false);
  assert.equal(toolSpecs('edit').some((s) => s.function.name === 'write_file'), true);

  // Content limits: >512 KB, NUL, bidi format chars, non-string
  await assert.rejects(
    () => ws.writeFile('big.txt', 'a'.repeat(WRITE_MAX + 1), 'edit'),
    (err) => err instanceof ToolError && err.code === 'too_large',
  );
  await assert.rejects(
    () => ws.writeFile('bidi.txt', 'gizli \u202e yön', 'edit'),
    (err) => err instanceof ToolError && err.code === 'bad_content',
  );
  await assert.rejects(
    () => ws.writeFile('nul.txt', 'merhaba\0dünya', 'edit'),
    (err) => err instanceof ToolError && err.code === 'bad_content',
  );
  await assert.rejects(
    () => ws.writeFile('num.txt', 12345, 'edit'),
    (err) => err instanceof ToolError && err.code === 'bad_content',
  );

  // Read limits: >256 KB file written via 300 KB write_file can be written (<=512 KB) but read_file refuses (>256 KB)
  await ws.writeFile('oversize_read.txt', 'a'.repeat(READ_MAX + 10), 'edit');
  await assert.rejects(
    () => ws.readFile('oversize_read.txt'),
    (err) => err instanceof ToolError && err.code === 'too_large',
  );
  await ws.deleteFile('oversize_read.txt');

  // Directory vs file conflicts
  await assert.rejects(
    () => ws.readFile('docs'),
    (err) => err instanceof ToolError && err.code === 'not_a_file',
  );
  await assert.rejects(
    () => ws.readFile('yok.md'),
    (err) => err instanceof ToolError && err.code === 'not_found',
  );

  // run() allow-list and error formatting
  assert.deepEqual(await ws.run('read_file', { path: 'docs/a.md' }, 'readonly'), [true, '# Başlık\nmerhaba']);
  assert.deepEqual(await ws.run('bash', { cmd: 'ls' }, 'dev'), [false, 'hata: unknown_tool']);
  assert.deepEqual(await ws.run('read_file', { path: 'docs/a.md', extra: 1 }, 'readonly'), [false, 'hata: bad_arguments']);
  assert.deepEqual(await ws.run('read_file', { path: '../gizli.txt' }, 'readonly'), [false, 'hata: bad_path']);
  assert.deepEqual(await ws.run('read_file', { path: '.env' }, 'readonly'), [false, 'hata: denied']);
  assert.deepEqual(await ws.run('write_file', { path: 'x.md', content: 'x' }, 'readonly'), [false, 'hata: read_only']);

  // 200 file cap
  const wsCapFiles = new VirtualWorkspace({ store: new MemoryStore(), maxFiles: 3 });
  await wsCapFiles.writeFile('f1.md', '1', 'edit');
  await wsCapFiles.writeFile('f2.md', '2', 'edit');
  await wsCapFiles.writeFile('f3.md', '3', 'edit');
  // Overwriting an existing file when at cap is allowed
  await wsCapFiles.writeFile('f3.md', '3-updated', 'edit');
  // Creating a 4th file is refused
  await assert.rejects(
    () => wsCapFiles.writeFile('f4.md', '4', 'edit'),
    (err) => err instanceof ToolError && err.code === 'too_many_files',
  );

  // 20 MB total byte cap
  const wsCapBytes = new VirtualWorkspace({ store: new MemoryStore(), maxTotalBytes: 100 });
  await wsCapBytes.writeFile('a.txt', 'x'.repeat(60), 'edit');
  await assert.rejects(
    () => wsCapBytes.writeFile('b.txt', 'y'.repeat(50), 'edit'),
    (err) => err instanceof ToolError && err.code === 'too_large',
  );
});

test('E4a — BrowserDevice attaches locally to DeviceHub, sends NOTHING to relay or server (D6), and runs multi-turn tool loop with RESULT_HEAD framing', async () => {
  const store = new MemoryStore();
  const relayFrames = [];
  const relayNeeds = [];
  const hub = new DeviceHub({
    roomId: 'a'.repeat(26),
    writeKey: 'k'.repeat(24),
    store,
    relayFactory: (opts) => ({
      status: 'idle',
      presence: new Set(),
      need(reason, on) { relayNeeds.push([reason, on]); },
      send(frame) { relayFrames.push(frame); return true; },
      destroy() {},
      _opts: opts,
    }),
  });
  await hub.init();

  const keystore = createMemoryKeystore();
  await keystore.set('gemini', CANARY_KEY, { remember: false });

  const fetchFn = makeFakeFetch([
    openaiReply({ calls: [['write_file', { path: 'notlar/ozet.md', content: '# Özet\nMerhaba dünya' }]], promptTokens: 20, completionTokens: 10 }),
    openaiReply({ calls: [['read_file', { path: 'notlar/ozet.md' }]], promptTokens: 30, completionTokens: 10 }),
    openaiReply({ content: 'Özet dosyası hazırlandı ve doğrulandı.', promptTokens: 40, completionTokens: 15 }),
  ]);

  const officeUpserts = [];
  const officeRemovals = [];
  const hubEvents = [];
  hub.on('event', (dev, msg) => hubEvents.push({ dev, msg }));

  const bdev = new BrowserDevice({
    keystore,
    store,
    fetchFn,
    onUpsert: (a) => officeUpserts.push(a),
    onRemove: (id) => officeRemovals.push(id),
  });
  await bdev.attach(hub);

  assert.equal(hub.isPaired(bdev.id), true);
  assert.equal(hub.isReady(bdev.id), true);

  // Create a browser agent via DeviceHub
  const created = await hub.request(bdev.id, {
    type: 'agent.create',
    name: 'Tarayıcı Kâtibi',
    engine: 'openai',
    engineId: 'gemini',
    model: 'gemini-2.5-flash',
    workspace: { id: 'tarayici', sub: null },
    profile: 'edit',
  });
  assert.equal(created.type, 'agent.created');
  assert.equal(created.agent.name, 'Tarayıcı Kâtibi');
  assert.equal(officeUpserts.length, 1);
  assert.equal(officeUpserts[0].control.device, bdev.id);
  assert.equal(officeUpserts[0].state, 'idle');

  // Even when updateRoom is called with the browser agent, relay is NEVER needed or contacted (D6)
  hub.updateRoom([officeUpserts[0]]);
  assert.deepEqual(relayFrames, []);
  assert.equal(relayNeeds.some(([reason, on]) => reason === 'room' && on === true), false);

  // Watch the agent and start a task
  hub.watch(bdev.id, created.agent.key, true);
  const ack = await hub.request(bdev.id, {
    type: 'task.start',
    agent: created.agent.key,
    prompt: 'Bir özet yaz ve oku',
    fresh: false,
  });
  assert.equal(ack.type, 'task.ack');
  assert.ok(ack.task);

  await until(() => hubEvents.some((e) => e.msg.type === 'task.end' && e.msg.task === ack.task));
  const endEv = hubEvents.find((e) => e.msg.type === 'task.end' && e.msg.task === ack.task).msg;
  assert.equal(endEv.status, 'success');
  assert.equal(endEv.result, 'Özet dosyası hazırlandı ve doğrulandı.');
  assert.equal(endEv.usage.inTok, 90);
  assert.equal(endEv.usage.outTok, 35);

  // Verify tool loop requests to Gemini preset URL and RESULT_HEAD framing
  assert.equal(fetchFn.calls.length, 3);
  assert.equal(fetchFn.calls[0].url, 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions');
  assert.equal(fetchFn.calls[0].headers.Authorization, `Bearer ${CANARY_KEY}`);
  assert.equal(fetchFn.calls[0].body.max_tokens, MAX_OUTPUT_TOKENS);

  // Second request has the write_file tool result framed with RESULT_HEAD
  const secondMsgs = fetchFn.calls[1].body.messages;
  const writeToolMsg = secondMsgs[secondMsgs.length - 1];
  assert.equal(writeToolMsg.role, 'tool');
  assert.ok(writeToolMsg.content.startsWith(RESULT_HEAD));

  // Third request has the read_file tool result framed with RESULT_HEAD
  const thirdMsgs = fetchFn.calls[2].body.messages;
  const readToolMsg = thirdMsgs[thirdMsgs.length - 1];
  assert.equal(readToolMsg.role, 'tool');
  assert.ok(readToolMsg.content.startsWith(RESULT_HEAD));
  assert.ok(readToolMsg.content.includes('# Özet\nMerhaba dünya'));

  // Office state transitions used 'Write' and 'Read' tool names, never leaking file paths or contents
  const officeTools = officeUpserts.map((u) => u.tool).filter(Boolean);
  assert.ok(officeTools.includes('Write'));
  assert.ok(officeTools.includes('Read'));
  assert.equal(JSON.stringify(officeUpserts).includes('notlar/ozet.md'), false, 'file path must not leak to office');
  assert.equal(JSON.stringify(officeUpserts).includes('Merhaba dünya'), false, 'file content must not leak to office');

  // Relay still has 0 frames
  assert.deepEqual(relayFrames, []);
});

test('E4a — Canary API key ("sk-CANARY-...") NEVER appears in any DOM text, hub message, error message, URL, or storage', async () => {
  const store = new MemoryStore();
  const hub = new DeviceHub({
    roomId: 'b'.repeat(26),
    writeKey: 'k'.repeat(24),
    store,
    relayFactory: () => ({ status: 'idle', presence: new Set(), need() {}, send() { return true; }, destroy() {} }),
  });
  await hub.init();

  const keystore = createMemoryKeystore();
  const hubTraffic = [];
  hub.on('event', (dev, msg) => hubTraffic.push(msg));
  const origRequest = hub.request.bind(hub);

  // Scripted fetch: first call returns HTTP 401 where the upstream server echoes the canary key in its error JSON!
  // Second call returns an assistant message with XSS payload.
  const fetchFn = makeFakeFetch([
    {
      status: 401,
      body: { error: { message: `Incorrect API key provided: ${CANARY_KEY}. Please check your key.` } },
    },
    openaiReply({ content: `Sonuç: ${XSS_PAYLOAD}` }),
  ]);

  const bdev = new BrowserDevice({ keystore, store, fetchFn });
  await bdev.attach(hub);

  const trackedRequest = async (devId, req, opts) => {
    hubTraffic.push(req);
    const res = await hub.request(devId, req, opts);
    hubTraffic.push(res);
    return res;
  };
  void origRequest;

  // Render the BrowserDevicePanel UI and configure the canary key through it
  const host = document.createElement('div');
  const slot = document.createElement('div');
  document.body.append(host, slot);

  try {
    const uiPanel = new BrowserDevicePanel({ container: host, device: bdev, keystore });
    await uiPanel.render();

    const keyInput = host.querySelector('input[name="apiKey"]');
    const rememberBox = host.querySelector('input[name="remember"]');
    assert.ok(keyInput);
    assert.equal(rememberBox.checked, false, 'remember must default to false (D3)');

    keyInput.value = CANARY_KEY;
    host.querySelector('form[data-form="keystore"]').dispatchEvent(new Event('submit'));
    await until(() => keyInput.value === '');

    assert.equal(await keystore.get('gemini'), CANARY_KEY);

    // Create agent and attach PromptPanel
    const { agent } = await trackedRequest(bdev.id, {
      type: 'agent.create',
      name: 'Güvenlik Ajanı',
      engine: 'openai',
      engineId: 'gemini',
      model: 'gemini-2.5-flash',
      workspace: { id: 'tarayici', sub: null },
      profile: 'edit',
    });

    const promptPanel = new PromptPanel({
      hub,
      slot,
      toast: () => {},
      onPair: () => {},
      onDevices: () => {},
      onActivity: () => {},
    });
    promptPanel.show({
      id: `browser-${agent.key}`,
      name: agent.name,
      state: 'idle',
      control: { device: bdev.id, agent: agent.key },
    });

    // Task 1: fails with 401 echoing CANARY_KEY from upstream
    const ack1 = await trackedRequest(bdev.id, {
      type: 'task.start',
      agent: agent.key,
      prompt: 'Merhaba',
      fresh: false,
    });
    await until(() => hubTraffic.some((m) => m && m.type === 'task.end' && m.task === ack1.task));

    // Task 2: succeeds with XSS payload in model output
    const ack2 = await trackedRequest(bdev.id, {
      type: 'task.start',
      agent: agent.key,
      prompt: 'XSS testi',
      fresh: false,
    });
    await until(() => hubTraffic.some((m) => m && m.type === 'task.end' && m.task === ack2.task));

    const taskGet = await trackedRequest(bdev.id, { type: 'task.get', task: ack1.task });
    const devInfo = await trackedRequest(bdev.id, { type: 'peer.hello', label: 'Test' });
    void taskGet;
    void devInfo;

    // 1. Canary key NEVER appears in any hub request, reply, or event
    const allHubJson = JSON.stringify(hubTraffic);
    assert.equal(allHubJson.includes(CANARY_KEY), false, 'canary key must never appear in hub messages');
    assert.equal(allHubJson.includes(CANARY_TAIL), false, 'canary key tail must never appear in hub messages');

    // 2. Canary key NEVER appears in any DOM text or attributes
    assert.equal(host.textContent.includes(CANARY_KEY), false, 'canary key must never appear in settings DOM');
    assert.equal(host.textContent.includes(CANARY_TAIL), false, 'canary tail must never appear in settings DOM');
    assert.equal(slot.textContent.includes(CANARY_KEY), false, 'canary key must never appear in PromptPanel DOM');
    assert.equal(slot.textContent.includes(CANARY_TAIL), false, 'canary tail must never appear in PromptPanel DOM');

    // 3. Canary key NEVER appears in fetch URLs or localStorage
    for (const c of fetchFn.calls) {
      assert.equal(c.url.includes(CANARY_KEY), false);
      assert.equal(c.url.includes(CANARY_TAIL), false);
    }
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      const v = localStorage.getItem(k) || '';
      assert.equal(v.includes(CANARY_KEY), false);
      assert.equal(v.includes(CANARY_TAIL), false);
    }

    // 4. XSS payload in model output did NOT execute or create elements in PromptPanel
    assert.equal(window.__pwnedE4a, undefined);
    assert.equal(slot.querySelectorAll('img, script').length, 0);
  } finally {
    host.remove();
    slot.remove();
  }
});

test('E4a — D7 task caps: 20 turns (max_turns), 40 tool calls (tool_limit), 8000 output tokens (max_tokens), and 30 tasks/hour (rate_limited)', async () => {
  assert.equal(MAX_TOOL_TURNS, 20);
  assert.equal(MAX_TOOL_CALLS, 40);
  assert.equal(MAX_OUTPUT_TOKENS, 8000);
  assert.equal(MAX_TASKS_PER_HOUR, 30);

  const store = new MemoryStore();
  const keystore = createMemoryKeystore();
  await keystore.set('groq', CANARY_KEY, { remember: false });

  // 1. max_turns cap (20 turns)
  {
    const loopReplies = Array.from({ length: MAX_TOOL_TURNS + 2 }, () =>
      openaiReply({ calls: [['list_dir', {}]], promptTokens: 5, completionTokens: 5 }),
    );
    const fetchFn = makeFakeFetch(loopReplies);
    const hub = new DeviceHub({
      roomId: 'c'.repeat(26),
      writeKey: 'k'.repeat(24),
      store,
      relayFactory: () => ({ status: 'idle', presence: new Set(), need() {}, send() { return true; }, destroy() {} }),
    });
    await hub.init();
    const events = [];
    hub.on('event', (_, m) => events.push(m));
    const bdev = new BrowserDevice({ keystore, store, fetchFn });
    await bdev.attach(hub);
    const { agent } = await hub.request(bdev.id, {
      type: 'agent.create', name: 'TurTest', engine: 'openai', engineId: 'groq', model: 'llama-3.3-70b-versatile',
    });
    const ack = await hub.request(bdev.id, { type: 'task.start', agent: agent.key, prompt: 'Sürekli listele' });
    await until(() => events.some((m) => m.type === 'task.end' && m.task === ack.task));
    const end = events.find((m) => m.type === 'task.end' && m.task === ack.task);
    assert.equal(end.status, 'error');
    assert.equal(end.error.code, 'max_turns');
    assert.equal(fetchFn.calls.length, MAX_TOOL_TURNS);
  }

  // 2. tool_limit cap (40 tool calls)
  {
    const fortyOneCalls = Array.from({ length: MAX_TOOL_CALLS + 1 }, () => ['list_dir', {}]);
    const fetchFn = makeFakeFetch([openaiReply({ calls: fortyOneCalls, promptTokens: 10, completionTokens: 10 })]);
    const hub = new DeviceHub({
      roomId: 'd'.repeat(26),
      writeKey: 'k'.repeat(24),
      store,
      relayFactory: () => ({ status: 'idle', presence: new Set(), need() {}, send() { return true; }, destroy() {} }),
    });
    await hub.init();
    const events = [];
    hub.on('event', (_, m) => events.push(m));
    const bdev = new BrowserDevice({ keystore, store, fetchFn });
    await bdev.attach(hub);
    const { agent } = await hub.request(bdev.id, {
      type: 'agent.create', name: 'AraçTest', engine: 'openai', engineId: 'groq', model: 'llama-3.3-70b-versatile',
    });
    const ack = await hub.request(bdev.id, { type: 'task.start', agent: agent.key, prompt: '41 araç çağır' });
    await until(() => events.some((m) => m.type === 'task.end' && m.task === ack.task));
    const end = events.find((m) => m.type === 'task.end' && m.task === ack.task);
    assert.equal(end.status, 'error');
    assert.equal(end.error.code, 'tool_limit');
  }

  // 3. max_tokens cap (8000 output tokens)
  {
    const fetchFn = makeFakeFetch([
      openaiReply({ calls: [['list_dir', {}]], promptTokens: 10, completionTokens: MAX_OUTPUT_TOKENS }),
      openaiReply({ content: 'Bu tura geçilmemeli', promptTokens: 10, completionTokens: 10 }),
    ]);
    const hub = new DeviceHub({
      roomId: 'e'.repeat(26),
      writeKey: 'k'.repeat(24),
      store,
      relayFactory: () => ({ status: 'idle', presence: new Set(), need() {}, send() { return true; }, destroy() {} }),
    });
    await hub.init();
    const events = [];
    hub.on('event', (_, m) => events.push(m));
    const bdev = new BrowserDevice({ keystore, store, fetchFn });
    await bdev.attach(hub);
    const { agent } = await hub.request(bdev.id, {
      type: 'agent.create', name: 'JetonTest', engine: 'openai', engineId: 'groq', model: 'llama-3.3-70b-versatile',
    });
    const ack = await hub.request(bdev.id, { type: 'task.start', agent: agent.key, prompt: 'Çok jeton üret' });
    await until(() => events.some((m) => m.type === 'task.end' && m.task === ack.task));
    const end = events.find((m) => m.type === 'task.end' && m.task === ack.task);
    assert.equal(end.status, 'error');
    assert.equal(end.error.code, 'max_tokens');
    assert.equal(fetchFn.calls.length, 1);
  }

  // 4. 30 tasks per hour rate limit (D7)
  {
    let nowMs = 1_700_000_000_000;
    const fetchFn = makeFakeFetch(
      Array.from({ length: MAX_TASKS_PER_HOUR + 5 }, () => openaiReply({ content: 'Tamam' })),
    );
    const hub = new DeviceHub({
      roomId: 'f'.repeat(26),
      writeKey: 'k'.repeat(24),
      store,
      relayFactory: () => ({ status: 'idle', presence: new Set(), need() {}, send() { return true; }, destroy() {} }),
    });
    await hub.init();
    const events = [];
    hub.on('event', (_, m) => events.push(m));
    const bdev = new BrowserDevice({ keystore, store, fetchFn, clock: () => nowMs });
    await bdev.attach(hub);
    const { agent } = await hub.request(bdev.id, {
      type: 'agent.create', name: 'HızTest', engine: 'openai', engineId: 'groq', model: 'llama-3.3-70b-versatile',
    });

    for (let i = 0; i < MAX_TASKS_PER_HOUR; i++) {
      const ack = await hub.request(bdev.id, { type: 'task.start', agent: agent.key, prompt: `Görev ${i + 1}` });
      await until(() => events.some((m) => m.type === 'task.end' && m.task === ack.task));
    }

    // 31st task within the same hour must be rejected with rate_limited
    await assert.rejects(
      () => hub.request(bdev.id, { type: 'task.start', agent: agent.key, prompt: 'Görev 31' }),
      (err) => err instanceof ControlError && err.code === 'rate_limited',
    );

    // Advance clock by 61 minutes -> task is allowed again
    nowMs += 61 * 60 * 1000;
    const ackAfter = await hub.request(bdev.id, { type: 'task.start', agent: agent.key, prompt: 'Saat sonrası görev' });
    await until(() => events.some((m) => m.type === 'task.end' && m.task === ackAfter.task));
  }
});

test('E4a — BrowserDevice rejects unknown providers, custom URLs, auto profile, and previews virtual files in sandbox="" PreviewDrawer', async () => {
  const store = new MemoryStore();
  const keystore = createMemoryKeystore();
  await keystore.set('openai', CANARY_KEY, { remember: false });

  const hub = new DeviceHub({
    roomId: 'g'.repeat(26),
    writeKey: 'k'.repeat(24),
    store,
    relayFactory: () => ({ status: 'idle', presence: new Set(), need() {}, send() { return true; }, destroy() {} }),
  });
  await hub.init();

  const bdev = new BrowserDevice({ keystore, store, fetchFn: makeFakeFetch([]) });
  await bdev.attach(hub);

  // Unknown provider or ollama is rejected
  await assert.rejects(
    () => hub.request(bdev.id, { type: 'agent.create', name: 'Kötü', engine: 'ollama', model: 'llama3' }),
    (err) => err instanceof ControlError && err.code === 'engine_unavailable',
  );
  await assert.rejects(
    () => hub.request(bdev.id, { type: 'agent.create', name: 'Kötü2', engine: 'openai', engineId: 'custom_url', model: 'gpt-4o' }),
    (err) => err instanceof ControlError && err.code === 'engine_unavailable',
  );
  // 'auto' profile is rejected
  await assert.rejects(
    () => hub.request(bdev.id, { type: 'agent.create', name: 'Oto', engine: 'openai', engineId: 'openai', model: 'gpt-4o-mini', profile: 'auto' }),
    (err) => err instanceof ControlError && err.code === 'not_allowed',
  );

  // Write an HTML/Markdown file with XSS into the virtual workspace and preview it via BrowserDevicePanel
  await bdev.workspace.writeFile('rapor.md', `# Sanal Dosya\n\n${XSS_PAYLOAD}`, 'edit');

  const host = document.createElement('div');
  const previewDlg = document.createElement('dialog');
  previewDlg.id = 'dlg-preview';
  document.body.append(host, previewDlg);

  try {
    const preview = new PreviewDrawer({ dialog: previewDlg, doc: document });
    const uiPanel = new BrowserDevicePanel({ container: host, device: bdev, keystore, preview });
    await uiPanel.render();

    const previewBtn = host.querySelector('[data-action="preview-vfs-file"]');
    assert.ok(previewBtn, 'virtual workspace file row should have Önizle button');
    previewBtn.click();
    await until(() => previewDlg.hasAttribute('open'));

    const iframe = previewDlg.querySelector('iframe.preview-frame');
    assert.ok(iframe);
    assert.equal(iframe.getAttribute('sandbox'), '');
    assert.ok(iframe.srcdoc.includes('<h1>Sanal Dosya</h1>'));
    assert.ok(iframe.srcdoc.includes('&lt;script&gt;window.__pwnedE4a=2&lt;/script&gt;'));
    assert.equal(window.__pwnedE4a, undefined);
    assert.equal(host.querySelectorAll('img, script').length, 0);
  } finally {
    if (previewDlg.open) previewDlg.close();
    previewDlg.remove();
    host.remove();
  }
});

