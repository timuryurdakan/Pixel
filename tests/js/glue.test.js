// Loads the Cloudflare glue (relay/src/index.js, room.js) with a `cloudflare:workers` shim and fake runtime globals,
// so that syntax errors or wiring mistakes in the thin files are caught locally (headless Chrome only).
import { test } from 'node:test';
import assert from 'node:assert/strict';

class FakeWS {
  constructor() { this.sent = []; this.readyState = 1; this.att = null; }
  send(s) { this.sent.push(s); }
  close() { this.readyState = 3; }
  serializeAttachment(v) { this.att = JSON.parse(JSON.stringify(v)); }
  deserializeAttachment() { return this.att && JSON.parse(JSON.stringify(this.att)); }
}

test('index.js default export answers /health and exports RoomRelay', async () => {
  globalThis.WebSocketPair = class { constructor() { this[0] = new FakeWS(); this[1] = new FakeWS(); } };
  globalThis.WebSocketRequestResponsePair = class { constructor(req, res) { this.req = req; this.res = res; } };
  const mod = await import('../../relay/src/index.js');
  const res = await mod.default.fetch({ url: 'https://r.test/health', method: 'GET', headers: new Headers() }, {});
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true, v: 1 });
  assert.equal(typeof mod.RoomRelay, 'function');

  const sockets = [];
  const ctx = {
    auto: null,
    setWebSocketAutoResponse(p) { this.auto = p; },
    acceptWebSocket(ws, tags) { ws.tags = tags; sockets.push(ws); },
    getWebSockets(tag) { return sockets.filter((w) => !tag || w.tags.includes(tag)); },
  };
  const room = new mod.RoomRelay(ctx, {});
  assert.equal(room.ctx, ctx);
  assert.deepEqual([ctx.auto.req, ctx.auto.res], ['{"t":"ping"}', '{"t":"pong"}']);
  // the 101 Response cannot be built outside workerd; everything before it runs:
  await assert.rejects(room.fetch({ url: 'https://r.test/x', method: 'GET', headers: new Headers({
    Upgrade: 'websocket', 'X-Pixel-Claims': JSON.stringify({ ro: 'device', id: 'abcdefghijklmnop' }) }) }));
  assert.equal(sockets.length, 1);
  assert.deepEqual(sockets[0].tags, ['dev', 'dev:abcdefghijklmnop']);
  assert.equal(JSON.parse(sockets[0].sent[0]).t, 'welcome');
  for (const m of ['webSocketMessage', 'webSocketClose', 'webSocketError']) assert.equal(typeof room[m], 'function');
  await room.webSocketMessage(sockets[0], '{"t":"presence.get"}');
  assert.equal(JSON.parse(sockets[0].sent.at(-1)).code, 'forbidden');
});
