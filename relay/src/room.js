// Durable Object `RoomRelay`: one per room (idFromName(roomId)), WebSocket Hibernation API (V3.md §4.3).
// Thin Cloudflare glue; the logic lives in core.js (RoomCore) so it can be unit-tested without workerd.
import { DurableObject } from 'cloudflare:workers';
import { RoomCore, cloudflarePlatform } from './core.js';

export class RoomRelay extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new RoomCore(ctx, cloudflarePlatform());
  }

  fetch(request) {
    return this.core.fetch(request);
  }

  webSocketMessage(ws, message) {
    return this.core.webSocketMessage(ws, message);
  }

  webSocketClose(ws, code, reason, wasClean) {
    return this.core.webSocketClose(ws, code, reason, wasClean);
  }

  webSocketError(ws, error) {
    return this.core.webSocketError(ws, error);
  }
}
