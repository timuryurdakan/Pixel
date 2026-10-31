// F4-3a (headless Chrome, tests/test_relay_js.py): the browser Boss's plan schema and reply parsing give exactly the
// connector's outcome on the shared vectors (tests/boss_plan_vectors.json, expected values computed by
// pixel_connector/boss.py), and the prompts are built from the generated rules.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validatePlan, jsonBlock, extractPlan, parseReview, planMarkers, reviewMarkers, planningPrompt, reviewPrompt,
  retryPrompt, parseJsonStrict, JsonFloat, BossError } from '../../web/js/browser_boss.js';
import { BOSS_RULES } from '../../web/js/boss_rules.js';

const plain = (v) => JSON.parse(JSON.stringify(v, (k, x) => (x instanceof JsonFloat ? { float: x.value } : x)));

async function vectors() {
  return parseJsonStrict(await (await fetch('/tests/boss_plan_vectors.json')).text());
}

test('plan vectors: same accept/refuse, code and field path as the connector', async () => {
  const v = await vectors();
  assert.ok(v.plans.length > 40);
  for (const c of v.plans) {
    let got;
    try {
      got = { ok: validatePlan(c.plan, c.options) };
    } catch (e) {
      assert.ok(e instanceof BossError, `${c.name}: ${e}`);
      got = { code: e.code, detail: e.detail };
    }
    assert.deepEqual(plain(got), plain(c.expect), c.name);
  }
});

test('block vectors: the reply\'s JSON block like boss._json_block', async () => {
  const v = await vectors();
  for (const c of v.blocks) {
    let got;
    try {
      got = { ok: jsonBlock(c.output, ...planMarkers(c.nonce)) };
    } catch (e) {
      got = { code: e.code, detail: e.detail };
    }
    assert.deepEqual(plain(got), plain(c.expect), c.name);
  }
});

test('strict JSON: duplicate keys, constants, trailing data and floats', () => {
  assert.throws(() => parseJsonStrict('{"a":1,"a":2}'));
  assert.throws(() => parseJsonStrict('{"a":NaN}'));
  assert.throws(() => parseJsonStrict('{"a":1} x'));
  assert.throws(() => parseJsonStrict('{"a":01}'));
  assert.ok(parseJsonStrict('{"v":1.0}').v instanceof JsonFloat);
  assert.equal(parseJsonStrict('{"v":1}').v, 1);
  const proto = parseJsonStrict('{"__proto__":{"x":1}}');          // an own key, never the prototype
  assert.ok(Object.hasOwn(proto, '__proto__'));
  assert.equal(Object.getPrototypeOf(proto), Object.prototype);
  assert.equal({}.x, undefined);
});

test('extractPlan and the planning prompt carry this request\'s markers and the department\'s roles only', () => {
  const n = 'abcd1234';
  const [s, e] = planMarkers(n);
  const plan = { v: 1, summary: 'Yazı', orders: [{ id: 'o1', role: 'yazar', title: 'T', goal: 'G', accept: ['A'] }] };
  const got = extractPlan(`ok\n${s}\n${JSON.stringify(plan)}\n${e}`, n, { department: 'icerik' });
  assert.equal(got.orders[0].role, 'yazar');
  assert.throws(() => extractPlan(`${s}\n${JSON.stringify(plan)}\n${e}`, n), (x) => x.code === 'bad_plan');
  const p = planningPrompt('İstek', n, 'icerik');
  assert.ok(p.includes(s) && p.includes(e) && p.endsWith('İstek'));
  assert.ok(p.includes(BOSS_RULES.roles.yazar.instruction));
  assert.ok(!p.includes(BOSS_RULES.roles.frontend.instruction));
  assert.ok(retryPrompt('block', n).includes(BOSS_RULES.extractReasons.block));
  assert.ok(retryPrompt('orders[0].role', n).includes('alan orders[0].role'));
});

test('parseReview: accept, fix with a valid plan, and unreadable or invalid decisions fail closed', () => {
  const n = 'cafe0001';
  const [s, e] = reviewMarkers(n);
  const plan = { v: 1, summary: 'Düz', orders: [{ id: 'o1', role: 'docs', title: 'T', goal: 'G', accept: ['A'] }] };
  assert.deepEqual(parseReview(`Rapor\n${s}\n{"v":1,"decision":"accept"}\n${e}`, n),
    { decision: 'accept', plan: null, text: 'Rapor', note: null });
  const fix = parseReview(`R\n${s}\n${JSON.stringify({ v: 1, decision: 'fix', plan })}\n${e}`, n);
  assert.equal(fix.decision, 'fix');
  assert.equal(fix.plan.orders[0].id, 'o1');
  for (const bad of [`${s}\n{"v":1,"decision":"accept","plan":{}}\n${e}`, `${s}\n{"v":1.0,"decision":"accept"}\n${e}`,
    `${s}\n{"v":1,"decision":"maybe"}\n${e}`, 'blok yok', null]) {
    assert.equal(parseReview(bad, n).note, 'review_unreadable');
  }
  const big = { ...plan, orders: [1, 2, 3].map((i) => ({ ...plan.orders[0], id: `o${i}` })) };
  assert.equal(parseReview(`${s}\n${JSON.stringify({ v: 1, decision: 'fix', plan: big })}\n${e}`, n).note, 'fix_invalid');
  const prompt = reviewPrompt(plan, n, false);
  assert.ok(prompt.includes(BOSS_RULES.text.reviewNoFix) && prompt.includes(s));
});
