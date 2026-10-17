// Minimal `node:assert/strict` stand-in for headless Chrome (tests/test_relay_js.py). Strict semantics:
// equal = Object.is, deepEqual = recursive with matching prototypes / typed array contents.
class AssertionError extends Error {}

function fmt(v) {
  try {
    return JSON.stringify(v, (k, x) => (x instanceof Uint8Array ? `Uint8Array(${[...x]})` : x));
  } catch {
    return String(v);
  }
}

function fail(message, actual, expected) {
  throw new AssertionError(`${message || 'assertion failed'}: actual ${fmt(actual)} expected ${fmt(expected)}`);
}

function deepEq(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
  if (Object.getPrototypeOf(a) !== Object.getPrototypeOf(b)) return false;
  if (ArrayBuffer.isView(a)) return a.length === b.length && a.every((x, i) => x === b[i]);
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && deepEq(a[k], b[k]));
}

const assert = {
  AssertionError,
  ok(v, message) {
    if (!v) fail(message, v, true);
  },
  equal(a, b, message) {
    if (!Object.is(a, b)) fail(message, a, b);
  },
  notEqual(a, b, message) {
    if (Object.is(a, b)) fail(message, a, `not ${fmt(b)}`);
  },
  deepEqual(a, b, message) {
    if (!deepEq(a, b)) fail(message, a, b);
  },
  match(s, re, message) {
    if (typeof s !== 'string' || !re.test(s)) fail(message, s, String(re));
  },
  throws(fn, expected, message) {
    try {
      fn();
    } catch (e) {
      if (typeof expected === 'function' && expected.prototype === undefined && expected(e) !== true) fail(message, e, 'validator');
      return;
    }
    fail(message || 'expected a throw', 'no throw', 'throw');
  },
  async rejects(p, validator, message) {
    try {
      await (typeof p === 'function' ? p() : p);
    } catch (e) {
      if (typeof validator === 'function' && validator(e) !== true) fail(message, e, 'validator');
      return;
    }
    fail(message || 'expected a rejection', 'resolved', 'rejection');
  },
};

export default assert;
