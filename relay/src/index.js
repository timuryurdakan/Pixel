// Pixel Office realtime relay — Cloudflare Worker entry (V3.md §4.2).
//   GET /health                      → 200 {"ok":true,"v":1}
//   GET /v1/rooms/<roomId>/ws         → ticket (Sec-WebSocket-Protocol) + Origin check → Durable Object RoomRelay
//   anything else                     → 404
// Secrets: RELAY_SECRET (dashboard). Vars: ALLOWED_ORIGINS (wrangler.jsonc). Nothing is logged.
import { handleRequest, cloudflarePlatform } from './core.js';

export { RoomRelay } from './room.js';

const platform = cloudflarePlatform();

export default {
  fetch(request, env) {
    return handleRequest(request, env, platform);
  },
};
