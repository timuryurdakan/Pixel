// Minimal `node:test` stand-in for running relay/test/*.test.js in headless Chrome (tests/test_relay_js.py).
// Supports what the relay tests use: test(name, fn) with sync or async fn, run sequentially.
const tests = [];

export function test(name, fn) {
  tests.push({ name, fn });
}

export async function run() {
  const results = [];
  for (const t of tests.splice(0)) {
    try {
      await t.fn();
      results.push({ name: t.name, ok: true });
    } catch (e) {
      results.push({ name: t.name, ok: false, error: String((e && e.stack) || e).slice(0, 2000) });
    }
  }
  return results;
}

export default test;
