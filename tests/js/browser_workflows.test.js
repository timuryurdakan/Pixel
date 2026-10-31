// F4-3b (headless Chrome, tests/test_relay_js.py): the browser workflow runner validates templates, lists the user's
// library and builds step prompts exactly like the connector (tests/workflow_vectors.json, expected values computed by
// pixel_connector/workflows.py), and its state machine follows WorkflowEngine: steps in order, approvals, caps,
// cancel, and runs a closed page left behind.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateTemplate, loadTemplates, stepPrompt, templatePublic, BrowserWorkflows, WorkflowError, PAGE_CLOSED,
  NEEDS_COMMANDS } from '../../web/js/browser_workflows.js';
import { validateTemplate as builderValidate } from '../../web/js/template_builder.js';
import { WORKFLOW_RULES as R } from '../../web/js/workflow_rules.js';

async function vectors() {
  return (await fetch('/tests/workflow_vectors.json')).json();
}

const outcome = (fn) => {
  try {
    return { ok: fn() };
  } catch (e) {
    assert.ok(e instanceof WorkflowError, String(e));
    return { code: e.code, detail: e.detail };
  }
};

test('template vectors: same accept/refuse, normalized copy and field path as the connector', async () => {
  const v = await vectors();
  assert.ok(v.templates.length > 70);
  for (const c of v.templates) {
    assert.deepEqual(outcome(() => validateTemplate(c.template, { autoEnabled: c.autoEnabled })), c.expect, c.name);
  }
});

test('library vectors: built-ins first, bad, duplicate and surplus entries counted like load_templates', async () => {
  const v = await vectors();
  for (const c of v.libraries) {
    const got = loadTemplates(c.templates);
    assert.deepEqual({ ids: got.templates.map((t) => t.id), invalid: got.invalid }, c.expect, c.name);
  }
});

test('prompt vectors: the step instruction, the request and the previous output fenced as data', async () => {
  const v = await vectors();
  for (const c of v.prompts) {
    const spec = { prompt: c.note, useBrief: false, usePrevious: false, ...c.step };
    const prev = c.previous === null ? null : { result: c.previous, name: c.previousName };
    let got;
    try {
      const p = stepPrompt(spec, c.brief, prev, c.limit, c.fence);
      got = { ok: p.prompt, truncated: p.truncated };
    } catch (e) {
      got = { code: e.code };
    }
    assert.deepEqual(got, c.expect, c.name);
  }
});

test('whatever the template builder accepts, the connector accepts too (no more "description": "")', async () => {
  const v = await vectors();
  let checked = 0;
  // The builder always writes `type`; give it the steps the way it would, so most vectors reach both validators.
  const typed = (t) => (t && Array.isArray(t.steps)
    ? { ...t, steps: t.steps.map((s) => (s && typeof s === 'object' && !('type' in s) ? { ...s, type: 'agent' } : s)) } : t);
  for (const c of v.templates) {
    const b = builderValidate(typed(c.template), { autoEnabled: c.autoEnabled });
    if (!b.ok) continue;
    checked += 1;
    assert.deepEqual(Object.keys(outcome(() => validateTemplate(b.value, { autoEnabled: c.autoEnabled }))), ['ok'], c.name);
  }
  assert.ok(checked > 10);
  const empty = builderValidate({ id: 'x', name: 'X', description: '', steps: [{ id: 'a', type: 'agent', role: 'R',
    engine: 'claude', prompt: 'bir\r\niki' }] });
  assert.equal(empty.value.description, null);
  assert.equal(empty.value.steps[0].prompt, 'bir\niki');
  assert.equal(builderValidate(empty.value).ok, true);                  // the stored copy loads again
});

test('templatePublic hides instructions and marks a template with a dev step as unavailable here (C3)', () => {
  const dev = validateTemplate({ id: 'kur', name: 'Kur', steps: [{ id: 'a', role: 'R', engine: 'claude', profile: 'dev', prompt: 'gizli talimat' }] });
  const pub = templatePublic(dev, false);
  assert.equal(pub.unavailable, NEEDS_COMMANDS);
  assert.ok(!JSON.stringify(pub).includes('gizli talimat'));
  const site = templatePublic(validateTemplate(R.builtinTemplates[0]), true);
  assert.equal(site.unavailable, null);
  assert.equal(site.limits.maxOutTok, 60000);
  assert.ok(site.limits.timeoutS <= 1800);
});

// -- the runner -----------------------------------------------------------------------------------------------------
const TWO = { id: 'iki', name: 'İki adım', steps: [
  { id: 'yaz', role: 'Yazar', engine: 'claude', profile: 'edit', prompt: 'Taslak yaz.', useBrief: true },
  { id: 'onay', type: 'approval', title: 'Devam?' },
  { id: 'oku', role: 'Okur', engine: 'claude', prompt: 'İncele.', usePrevious: true }] };

function harness({ library = [TWO], replies = null, boss = false } = {}) {
  const events = [];
  const tasks = [];
  const timers = [];
  let now = 1000;
  let saved = null;
  const pending = [];
  const w = new BrowserWorkflows({
    emit: (m) => events.push(JSON.parse(JSON.stringify(m))),
    runTask: (t) => {
      tasks.push(t);
      t.onStart(`bt_${tasks.length}`);
      if (replies) return Promise.resolve(replies(t, tasks.length));
      return new Promise((resolve) => pending.push(resolve));
    },
    cancelTask: () => { const r = pending.shift(); if (r) r({ status: 'cancelled', result: null, usage: { outTok: 1, turns: 1 } }); },
    library: () => library,
    otherBusy: () => boss,
    persist: { load: async () => saved, save: (runs) => { saved = JSON.parse(JSON.stringify(runs)); } },
    workspaceId: 'tarayici',
    providers: ['groq'],
    clock: () => now,
    fence: () => 'feedc0de',
    setTimer: (fn, ms) => { timers.push({ fn, at: now + ms }); return timers.length - 1; },
    clearTimer: (h) => { if (h !== null && timers[h]) timers[h].fn = null; },
  });
  const tick = async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); await new Promise((r) => setTimeout(r, 0)); };
  return { w, events, tasks, timers, pending, tick, advance: (ms) => { now += ms; }, saved: () => saved,
    setSaved: (v) => { saved = v; }, last: () => events.at(-1).run };
}

const START = { type: 'workflow.start', template: 'iki', workspace: { id: 'tarayici', sub: null }, brief: 'Kedi yazısı',
  model: { provider: 'groq', model: 'llama-3.3-70b-versatile' } };
const ok = (text, outTok = 100, turns = 2) => ({ status: 'success', result: text, usage: { outTok, turns } });

test('a run: steps in order, the approval waits for the user, the previous output enters fenced, events carry no text', async () => {
  const h = harness({ replies: (t, n) => ok(n === 1 ? 'TASLAK-ÇIKTISI' : 'İnceleme tamam') });
  const started = h.w.handle(START);
  assert.equal(started.type, 'workflow.started');
  assert.match(started.run.id, /^w_[A-Za-z0-9_-]{10}$/);
  await h.tick();
  assert.equal(h.last().state, 'approval');
  assert.equal(h.tasks.length, 1);
  assert.equal(h.tasks[0].agentKey, 'wf-yaz');
  assert.equal(h.tasks[0].fresh, true);
  assert.equal(h.tasks[0].provider, 'groq');
  assert.ok(h.tasks[0].prompt.endsWith(`${R.text.briefHead}\nKedi yazısı`));
  assert.equal(h.last().steps[0].task, 'bt_1');
  assert.equal(h.last().steps[1].state, 'approval');
  assert.ok(h.last().steps[1].expiresAt > 1000);
  assert.deepEqual(h.w.handle({ type: 'workflow.answer', run: started.run.id, decision: 'approve' }),
    { type: 'workflow.ok', run: started.run.id });
  await h.tick();
  assert.equal(h.last().state, 'done');
  assert.equal(h.tasks.length, 2);
  assert.ok(h.tasks[1].prompt.includes('<<<PIXEL-DEVIR-feedc0de BAŞLANGIÇ>>>\nTASLAK-ÇIKTISI\n<<<PIXEL-DEVIR-feedc0de SON>>>'));
  assert.equal(h.last().outTok, 200);
  assert.equal(h.last().turns, 4);
  const all = JSON.stringify(h.events) + JSON.stringify(h.saved());
  assert.ok(!all.includes('TASLAK-ÇIKTISI') && !all.includes('Kedi yazısı') && !all.includes('Taslak yaz.'));
  assert.equal(h.events.at(-1).type, 'workflow.state');
});

test('deny and approval timeout end the run cancelled; the next step never starts', async () => {
  for (const [act, code] of [['deny', 'denied'], ['timeout', 'approval_timeout']]) {
    const h = harness({ replies: () => ok('x') });
    const id = h.w.handle(START).run.id;
    await h.tick();
    if (act === 'deny') h.w.handle({ type: 'workflow.answer', run: id, decision: 'deny' });
    else h.timers.filter((t) => t.fn).at(-1).fn();
    await h.tick();
    assert.equal(h.last().state, 'cancelled', act);
    assert.equal(h.last().code, code);
    assert.deepEqual(h.last().steps.map((s) => s.state), ['done', 'cancelled', 'cancelled']);
    assert.equal(h.tasks.length, 1);
    assert.throws(() => h.w.handle({ type: 'workflow.answer', run: id, decision: 'approve' }), (e) => e.code === 'unknown_approval');
  }
});

test('cancel while a step runs: the task is aborted and the run ends cancelled when it reports back', async () => {
  const h = harness();
  const id = h.w.handle(START).run.id;
  await h.tick();
  assert.equal(h.last().steps[0].state, 'running');
  h.w.handle({ type: 'workflow.cancel', run: id });
  await h.tick();
  assert.equal(h.last().state, 'cancelled');
  assert.equal(h.last().code, 'cancelled');
  assert.throws(() => h.w.handle({ type: 'workflow.cancel', run: id }), (e) => e.code === 'unknown_workflow');
});

test('the whole-run timer stops a running step with error timeout', async () => {
  const h = harness();
  h.w.handle(START);
  await h.tick();
  h.timers[0].fn();
  await h.tick();
  assert.equal(h.last().state, 'error');
  assert.equal(h.last().code, 'timeout');
});

test('caps: turns over the template limit and output tokens over 60.000 stop the run (C4)', async () => {
  const turns = harness({ library: [{ ...TWO, guardrails: { maxTurns: 3 } }], replies: () => ok('x', 10, 5) });
  turns.w.handle(START);
  await turns.tick();
  assert.equal(turns.last().state, 'error');
  assert.equal(turns.last().code, 'max_turns');
  const tok = harness({ replies: () => ok('x', 60001, 1) });
  tok.w.handle(START);
  await tok.tick();
  assert.equal(tok.last().code, 'budget_exceeded');
});

test('a failed step ends the run with the device code; an odd code becomes the generic one', async () => {
  for (const [code, want] of [['model_not_found', 'model_not_found'], ['Bad Code!', 'error']]) {
    const h = harness({ replies: () => ({ status: 'error', result: null, usage: { outTok: 0 }, error: { code } }) });
    h.w.handle(START);
    await h.tick();
    assert.equal(h.last().state, 'error');
    assert.equal(h.last().code, want);
  }
});

test('the boundary: unknown fields, other folders, sub-folders, bad models, dev templates, busy and the Boss (C6)', async () => {
  const dev = { id: 'kur', name: 'Kur', steps: [{ id: 'a', role: 'R', engine: 'claude', profile: 'dev', prompt: 'p' }] };
  const h = harness({ library: [TWO, dev] });
  const bad = [[{ ...START, maxCostUsd: 1 }, 'bad_request'], [{ ...START, steps: {} }, 'bad_request'],
    [{ ...START, workspace: { id: 'baska' } }, 'workspace_denied'], [{ ...START, workspace: { id: 'tarayici', sub: 'a' } }, 'workspace_denied'],
    [{ ...START, model: { provider: 'evil', model: 'm' } }, 'bad_request'], [{ ...START, model: { provider: 'groq', model: '-x' } }, 'bad_request'],
    [{ ...START, brief: '   ' }, 'bad_request'], [{ ...START, brief: 'ş'.repeat(2049) }, 'too_large'],
    [{ ...START, template: 'yok' }, 'unknown_template'], [{ ...START, template: '../x' }, 'bad_request'],
    [{ ...START, template: 'kur' }, 'not_allowed']];
  for (const [msg, code] of bad) assert.throws(() => h.w.handle(msg), (e) => e.code === code, JSON.stringify(msg).slice(0, 80));
  h.w.handle(START);
  assert.equal(h.w.busy(), true);
  assert.throws(() => h.w.handle(START), (e) => e.code === 'busy');
  const b = harness({ boss: true });
  assert.throws(() => b.w.handle(START), (e) => e.code === 'busy');
  assert.throws(() => h.w.handle({ type: 'workflow.answer', run: 'w_XXXXXXXXXX', decision: 'approve' }), (e) => e.code === 'unknown_workflow');
  assert.throws(() => h.w.handle({ type: 'workflow.save' }), (e) => e.code === 'bad_request');
});

test('a broken library entry is counted, not run; the list shows the built-in template and the runs', async () => {
  const h = harness({ library: [TWO, { id: 'bozuk', name: '', steps: [] }, 'çöp'] });
  const l = h.w.handle({ type: 'workflow.list' });
  assert.deepEqual(l.templates.map((t) => t.id), ['statik-site', 'iki']);
  assert.equal(l.invalid, 2);
  assert.deepEqual(l.runs, []);
  h.w.handle(START);
  assert.equal(h.w.handle({ type: 'workflow.list' }).runs.length, 1);
});

test('C8: a run the closed page left active comes back stopped, nothing resumes', async () => {
  const h = harness();
  const id = h.w.handle(START).run.id;
  await h.tick();
  const left = h.saved();
  assert.equal(left.at(-1).state, 'running');
  const again = harness();
  again.setSaved(left);
  await again.w.restore();
  const runs = again.w.handle({ type: 'workflow.list' }).runs;
  assert.equal(runs.length, 1);
  assert.equal(runs[0].id, id);
  assert.equal(runs[0].state, 'cancelled');
  assert.equal(runs[0].code, PAGE_CLOSED);
  assert.ok(runs[0].steps.every((s) => ['done', 'cancelled', 'error'].includes(s.state)));
  assert.equal(again.tasks.length, 0);
  assert.equal(again.w.busy(), false);
  assert.throws(() => again.w.handle({ type: 'workflow.cancel', run: id }), (e) => e.code === 'unknown_workflow');
});
