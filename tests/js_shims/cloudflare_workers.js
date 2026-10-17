// `cloudflare:workers` stand-in for loading relay/src/room.js and index.js in headless Chrome (tests/test_relay_js.py).
export class DurableObject {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
  }
}
