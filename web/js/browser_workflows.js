// F4-3b — workflow templates in the browser device (docs/F43B_TASARIM.md).
// Answers workflow.list / start / answer / cancel like the connector (pixel_connector/workflows.py) and emits the same
// `workflow.state` events, so the workflow dialog, the Task Board and the office walk work unchanged. Templates are the
// built-in ones and this browser's template-builder library; both pass the connector's validator (ported here and
// pinned by tests/workflow_vectors.json) before anything runs. Steps run one after another through the device's
// transient agents with one provider and model for the whole run (C2); a step's output reaches the next step only
// fenced as data (tasks.handoff_prompt) and never leaves this page in an event.

import { WORKFLOW_RULES as R } from './workflow_rules.js';
import { BROWSER_PROFILES, JOB_MAX_OUT_TOKENS, JOB_MAX_MS, parseModel } from './browser_boss.js';

export class WorkflowError extends Error {
  constructor(code, detail) { super(detail || code); this.code = code; this.detail = detail || code; }
}

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$/;          // = config.MODEL_RE
const RUN_ID_RE = /^w_[A-Za-z0-9_-]{10}$/;
const TEMPLATE_FIELDS = new Set(['id', 'name', 'description', 'folder', 'guardrails', 'steps']);
const GUARD_FIELDS = new Set(['maxCostUsd', 'maxTurns', 'timeoutS']);
const AGENT_STEP_FIELDS = new Set(['id', 'type', 'role', 'engine', 'engines', 'profile', 'model', 'prompt',
  'usePrevious', 'useBrief']);
const APPROVAL_STEP_FIELDS = new Set(['id', 'type', 'title', 'timeoutS']);
const START_FIELDS = new Set(['type', 'id', 'template', 'workspace', 'brief', 'model']);
const TERMINAL = new Set(['done', 'error', 'cancelled']);
const CODE_RE = /^[a-z][a-z0-9_]{0,31}$/;
export const PAGE_CLOSED = 'page_closed';           // C8: a run the closed page left behind
export const NEEDS_COMMANDS = 'commands';           // C3: a template with a `dev` or `auto` step cannot run here

// -- Python's str semantics the validator depends on --------------------------------------------------------------
const PY_SPACE = '\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const PY_STRIP = new RegExp(`^[${PY_SPACE}]+|[${PY_SPACE}]+$`, 'g');
const pyStrip = (s) => s.replace(PY_STRIP, '');
const HIDDEN = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;
const NOT_PRINTABLE = /[\p{C}\p{Z}]/u;
const codePoints = (s) => [...s];
const isPlain = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const has = (o, k) => Object.hasOwn(o, k);
export const utf8Bytes = (s) => new TextEncoder().encode(s).length;
const fmt = (text, ...args) => { let i = 0; return text.replace(/%[sd]/g, () => String(args[i++])); };

function hiddenChar(ch, allowNewlines) {
  if (allowNewlines && (ch === '\n' || ch === '\t')) return false;
  return HIDDEN.test(ch);
}

function text(raw, where, maxLen, allowNewlines = false, maxBytes = null) {
  if (typeof raw !== 'string') throw new WorkflowError('bad_template', where);
  const s = pyStrip(raw);
  const chars = codePoints(s);
  if (!s || chars.length > maxLen || chars.some((ch) => hiddenChar(ch, allowNewlines))) throw new WorkflowError('bad_template', where);
  if (maxBytes !== null && utf8Bytes(s) > maxBytes) throw new WorkflowError('bad_template', where);
  return s;
}

function number(raw, where, [lo, hi], integer) {
  if (typeof raw !== 'number' || !Number.isFinite(raw)) throw new WorkflowError('bad_template', where);
  if (integer && !Number.isInteger(raw)) throw new WorkflowError('bad_template', where);
  if (!(lo <= raw && raw <= hi)) throw new WorkflowError('bad_template', where);
  return raw;
}

function bool(raw, where, dflt) {
  if (raw === undefined || raw === null) return dflt;
  if (typeof raw !== 'boolean') throw new WorkflowError('bad_template', where);
  return raw;
}

function allowed(raw, fields, where) {
  if (!isPlain(raw) || !Object.keys(raw).every((k) => fields.has(k))) throw new WorkflowError('bad_template', where);
}

/** workspaces.validate_sub: normalised relative sub-path or null; Error otherwise. */
export function validateSub(sub) {
  if (sub === undefined || sub === null || sub === '') return null;
  if (typeof sub !== 'string' || codePoints(sub).length > R.maxSub || sub.includes('\0')) throw new Error('bad sub path');
  const s = sub.replace(/\\/g, '/');
  if (s.startsWith('/') || /^[A-Za-z]:/.test(s)) throw new Error('absolute sub path');
  const parts = s.split('/').filter((p) => p !== '' && p !== '.');
  if (!parts.length || parts.includes('..')) throw new Error('parent components');
  if (parts.some((p) => [...p].some((c) => c.codePointAt(0) < 0x20))) throw new Error('control characters');
  return parts.join('/');
}

function guardrails(raw) {
  const out = { ...R.defaultGuardrails };
  if (raw === undefined || raw === null) return out;
  allowed(raw, GUARD_FIELDS, 'guardrails');
  if (has(raw, 'maxCostUsd')) out.maxCostUsd = number(raw.maxCostUsd, 'guardrails.maxCostUsd', R.costBounds, false);
  if (has(raw, 'maxTurns')) out.maxTurns = number(raw.maxTurns, 'guardrails.maxTurns', R.turnBounds, true);
  if (has(raw, 'timeoutS')) out.timeoutS = number(raw.timeoutS, 'guardrails.timeoutS', R.timeoutBounds, true);
  return out;
}

function agentStep(raw, where, autoEnabled, hasPrevious) {
  allowed(raw, AGENT_STEP_FIELDS, where);
  const engine = raw.engine;
  if (!R.stepEngines.includes(engine)) throw new WorkflowError('bad_template', `${where}.engine`);
  const engines = has(raw, 'engines') ? raw.engines : [engine];
  if (!Array.isArray(engines) || !engines.length || new Set(engines.map((e) => JSON.stringify(e))).size !== engines.length
      || engines.some((e) => !R.stepEngines.includes(e)) || !engines.includes(engine)) {
    throw new WorkflowError('bad_template', `${where}.engines`);
  }
  const profile = has(raw, 'profile') ? raw.profile : 'readonly';
  if (!R.profiles.includes(profile)) throw new WorkflowError('bad_template', `${where}.profile`);
  if (profile === 'auto' && !autoEnabled) throw new WorkflowError('bad_template', `${where}.profile: auto is disabled on this device`);
  const model = has(raw, 'model') ? raw.model : null;
  if (model !== null && (typeof model !== 'string' || !MODEL_RE.test(model))) throw new WorkflowError('bad_template', `${where}.model`);
  const usePrevious = bool(raw.usePrevious, `${where}.usePrevious`, false);
  if (usePrevious && !hasPrevious) throw new WorkflowError('bad_template', `${where}.usePrevious: no earlier agent step`);
  // Same order as the connector's dict literal: role, then prompt, then useBrief.
  const role = text(raw.role, `${where}.role`, R.roleMax);
  const prompt = text(raw.prompt, `${where}.prompt`, R.promptMax, true, R.promptMax);
  return { id: raw.id, type: 'agent', role, engine, engines: R.stepEngines.filter((e) => engines.includes(e)), profile,
    model, prompt, usePrevious, useBrief: bool(raw.useBrief, `${where}.useBrief`, false) };
}

function approvalStep(raw, where) {
  allowed(raw, APPROVAL_STEP_FIELDS, where);
  let timeout = R.defaultApprovalTimeoutS;
  if (has(raw, 'timeoutS')) timeout = number(raw.timeoutS, `${where}.timeoutS`, R.approvalTimeoutBounds, true);
  return { id: raw.id, type: 'approval', title: text(raw.title, `${where}.title`, R.titleMax), timeoutS: timeout };
}

/** workflows.validate_template: one template → normalized copy, or WorkflowError('bad_template', where). */
export function validateTemplate(raw, { autoEnabled = false } = {}) {
  allowed(raw, TEMPLATE_FIELDS, 'template');
  const tid = raw.id;
  if (typeof tid !== 'string' || !SLUG_RE.test(tid)) throw new WorkflowError('bad_template', 'id');
  const desc = has(raw, 'description') ? raw.description : null;
  let folder;
  try {
    folder = validateSub(has(raw, 'folder') ? raw.folder : null);
  } catch {
    throw new WorkflowError('bad_template', 'folder');
  }
  const stepsRaw = raw.steps;
  if (!Array.isArray(stepsRaw) || stepsRaw.length < 1 || stepsRaw.length > R.maxSteps) throw new WorkflowError('bad_template', 'steps');
  const steps = [];
  const seen = new Set();
  let agents = 0;
  stepsRaw.forEach((s, i) => {
    const where = `steps[${i}]`;
    if (!isPlain(s) || typeof s.id !== 'string' || !SLUG_RE.test(s.id) || seen.has(s.id)) throw new WorkflowError('bad_template', `${where}.id`);
    seen.add(s.id);
    const kind = has(s, 'type') ? s.type : 'agent';
    if (kind === 'agent') {
      steps.push(agentStep(s, where, autoEnabled, agents > 0));
      agents += 1;
    } else if (kind === 'approval') {
      steps.push(approvalStep(s, where));
    } else {
      throw new WorkflowError('bad_template', `${where}.type`);
    }
  });
  if (!agents) throw new WorkflowError('bad_template', 'steps: no agent step');
  return { id: tid, name: text(raw.name, 'name', R.nameMax),
    description: desc !== null ? text(desc, 'description', R.descriptionMax) : null,
    folder, guardrails: guardrails(raw.guardrails), steps };
}

/** workflows.load_templates over a list (this browser's library): built-ins first; bad, duplicate or surplus entries
 * are left out and counted. → {templates, invalid} */
export function loadTemplates(userList) {
  const out = R.builtinTemplates.map((t) => validateTemplate(t));
  const ids = new Set(out.map((t) => t.id));
  let invalid = 0;
  (Array.isArray(userList) ? userList : []).forEach((t, i) => {
    try {
      if (i >= R.maxTemplates) throw new WorkflowError('bad_template', 'too many templates');
      const v = validateTemplate(t);
      if (ids.has(v.id)) throw new WorkflowError('bad_template', 'duplicate id');
      ids.add(v.id);
      out.push(v);
    } catch (e) {
      if (!(e instanceof WorkflowError)) throw e;
      invalid += 1;
    }
  });
  return { templates: out, invalid };
}

/** C3: why this template cannot run in a browser, or null. */
export function unavailableReason(t) {
  return t.steps.some((s) => s.type === 'agent' && !BROWSER_PROFILES.includes(s.profile)) ? NEEDS_COMMANDS : null;
}

/** C4: the run's caps in a browser. */
export function browserLimits(t) {
  return { maxCostUsd: 0, maxTurns: t.guardrails.maxTurns, timeoutS: Math.min(t.guardrails.timeoutS, JOB_MAX_MS / 1000),
    maxOutTok: JOB_MAX_OUT_TOKENS };
}

/** workflows.template_public (no step instructions) + the browser's `unavailable` reason. */
export function templatePublic(t, builtin) {
  const steps = t.steps.map((s) => (s.type === 'agent'
    ? { id: s.id, type: 'agent', role: s.role, engine: s.engine, engines: [...s.engines], profile: s.profile, model: s.model }
    : { id: s.id, type: 'approval', role: s.title, timeoutS: s.timeoutS }));
  return { id: t.id, name: t.name, description: t.description, folder: t.folder, builtin, limits: browserLimits(t),
    needsBrief: t.steps.some((s) => s.type === 'agent' && s.useBrief), steps, unavailable: unavailableReason(t) };
}

// -- step prompts ---------------------------------------------------------------------------------------------------
function cleanName(name) {
  const kept = codePoints(String(name || '?')).filter((ch) => (ch === ' ' || !NOT_PRINTABLE.test(ch))
    && !(ch >= '‪' && ch <= '‮') && !(ch >= '⁦' && ch <= '⁩'));
  return kept.slice(0, 24).join('') || '?';
}

function cutUtf8(s, limit) {
  const b = new TextEncoder().encode(s);
  let n = Math.max(0, Math.min(limit, b.length));
  while (n > 0 && n < b.length && (b[n] & 0xc0) === 0x80) n -= 1;
  return new TextDecoder().decode(b.slice(0, n));
}

/** tasks.handoff_prompt: the note, then the earlier reply fenced and labelled as data. → {prompt, outLen, truncated} */
export function handoffPrompt(note, output, fromName, limit, fenceHex) {
  const out = typeof output === 'string' ? output : '';
  const outLen = utf8Bytes(out);
  const fence = `<<<PIXEL-DEVIR-${fenceHex} `;
  const head = `${note || R.text.handoffDefaultNote}\n\n${fmt(R.text.handoffDataHead, cleanName(fromName))}\n${fence}BAŞLANGIÇ>>>\n`;
  const tail = `\n${fence}SON>>>`;
  const cutNote = `\n${fmt(R.text.handoffCutNote, R.handoffOutputMax, 10 ** 12)}`;
  const room = limit - utf8Bytes(head + tail + cutNote);
  if (room < 0) throw new WorkflowError('too_large', 'handoff prompt too large');
  const allowedBytes = Math.min(R.handoffOutputMax, room);
  if (outLen <= allowedBytes) return { prompt: head + out + tail, outLen, truncated: false };
  const kept = cutUtf8(out, allowedBytes);
  return { prompt: `${head}${kept}${tail}\n${fmt(R.text.handoffCutNote, utf8Bytes(kept), outLen)}`, outLen, truncated: true };
}

/** WorkflowEngine._step_prompt. `previous`: {result, name} of the last finished agent step, or null. → {prompt, truncated} */
export function stepPrompt(spec, brief, previous, limit, fenceHex) {
  let note = spec.prompt;
  if (spec.useBrief) note = `${note}\n\n${R.text.briefHead}\n${brief}`;
  if (spec.usePrevious && previous) {
    const info = handoffPrompt(note, previous.result, previous.name, limit, fenceHex);
    return { prompt: info.prompt, truncated: info.truncated };
  }
  if (utf8Bytes(note) > limit) throw new WorkflowError('too_large', 'step prompt too large');
  return { prompt: note, truncated: false };
}

// -- workflow.start at the boundary ---------------------------------------------------------------------------------
/** workflow.start (untrusted, from this page) for `template` → {brief, provider, model}. */
export function parseStart(msg, template, { workspaceId, providers }) {
  if (!isPlain(msg) || !Object.keys(msg).every((k) => START_FIELDS.has(k))) throw new WorkflowError('bad_request', 'unknown field');
  const ws = msg.workspace;
  if (!isPlain(ws) || !Object.keys(ws).every((k) => k === 'id' || k === 'sub') || typeof ws.id !== 'string') {
    throw new WorkflowError('bad_request', 'workspace');
  }
  if (ws.id !== workspaceId || (ws.sub !== undefined && ws.sub !== null)) throw new WorkflowError('workspace_denied', 'workspace');
  let brief = msg.brief === undefined || msg.brief === null ? '' : msg.brief;
  if (typeof brief !== 'string') throw new WorkflowError('bad_request', 'brief');
  if (utf8Bytes(brief) > R.briefMax) throw new WorkflowError('too_large', 'brief too large');
  brief = pyStrip(brief);
  if (!brief && template.steps.some((s) => s.type === 'agent' && s.useBrief)) throw new WorkflowError('bad_request', 'brief required');
  const m = parseModel(msg.model, providers);
  if (!m) throw new WorkflowError('bad_request', 'model');
  return { brief, ...m };
}

// == the runner ==================================================================================================
function randomId(n, alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-') {
  const b = crypto.getRandomValues(new Uint8Array(n));
  return [...b].map((x) => alphabet[x % alphabet.length]).join('');
}

export const stepAgentKey = (stepId) => `wf-${stepId}`;

class Step {
  constructor(spec, index, provider, model) {
    Object.assign(this, { spec, index, id: spec.id, kind: spec.type, provider, model, agent: null, task: null,
      state: 'waiting', code: null, turns: 0, outTok: 0, started: null, ended: null, expires: null, result: null,
      truncated: false, decide: null, timer: null });
    if (this.kind === 'agent') this.agent = stepAgentKey(spec.id);
  }

  setState(state, code = null) {
    if (!R.stepTransitions[this.state] || !R.stepTransitions[this.state].includes(state)) {
      throw new WorkflowError('bad_transition', `${this.state} → ${state}`);
    }
    this.state = state;
    if (code !== null) this.code = code;
  }

  wire() {
    return { id: this.id, type: this.kind, role: this.kind === 'agent' ? this.spec.role : this.spec.title,
      engine: this.kind === 'agent' ? this.provider : null, model: this.kind === 'agent' ? this.model : null,
      profile: this.kind === 'agent' ? this.spec.profile : null, agent: this.agent, task: this.task, state: this.state,
      code: this.code, costUsd: 0, turns: this.turns, outTok: this.outTok, startedAt: this.started,
      endedAt: this.ended, expiresAt: this.expires, truncated: this.truncated };
  }
}

export class BrowserWorkflows {
  /**
   * @param {object} o
   * @param {(msg: object) => void} o.emit                 workflow.state to this page
   * @param {(t: object) => Promise<{status:string, result:?string, usage:object, error:?object}>} o.runTask
   *        one agent task in the device ({agentKey, name, role, profile, prompt, fresh, provider, model, onStart})
   * @param {() => void} o.cancelTask                     aborts the task that is running
   * @param {() => Array} o.library                       this browser's template-builder list (untrusted)
   * @param {() => boolean} o.otherBusy                   C6: a Boss job is active
   * @param {(agentKey: string, state: string) => void} [o.office]
   * @param {{load: () => Promise<Array>, save: (runs: Array) => void}} [o.persist]  C8
   */
  constructor({ emit, runTask, cancelTask, library, otherBusy = () => false, office = () => {}, persist = null,
    workspaceId, workspaceName = null, providers, clock = () => Date.now(), runId = () => `w_${randomId(10)}`,
    fence = () => randomId(8, '0123456789abcdef'), setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = (h) => clearTimeout(h) }) {
    Object.assign(this, { emit, runTask, cancelTask, library, otherBusy, office, persist, workspaceId, workspaceName,
      providers, clock, runId, fence, setTimer, clearTimer });
    this.run = null;            // the active or last run object
    this.finished = [];         // wires of earlier runs, newest last (at most keepFinished)
  }

  /** C8: runs a closed page left active come back stopped; nothing resumes. */
  async restore() {
    if (!this.persist) return;
    let saved = [];
    try {
      saved = await this.persist.load();
    } catch {
      saved = [];
    }
    const wires = (Array.isArray(saved) ? saved : []).filter((w) => isPlain(w) && typeof w.id === 'string'
      && RUN_ID_RE.test(w.id) && Array.isArray(w.steps));
    this.finished = wires.slice(-R.keepFinished).map((w) => {
      if (TERMINAL.has(w.state)) return w;
      const steps = w.steps.map((s) => (isPlain(s) && !TERMINAL.has(s.state) ? { ...s, state: 'cancelled', code: PAGE_CLOSED } : s));
      return { ...w, state: 'cancelled', code: PAGE_CLOSED, current: null, steps };
    });
    this._save();
  }

  busy() { return !!(this.run && this._active(this.run)); }

  handle(msg) {
    switch (msg && msg.type) {
      case 'workflow.list': return this._list();
      case 'workflow.start': return this._start(msg);
      case 'workflow.answer': return this._answer(msg);
      case 'workflow.cancel': return this._cancel(msg);
      default: throw new WorkflowError('bad_request', 'type');
    }
  }

  _templates() {
    let lib = [];
    try {
      lib = this.library() || [];
    } catch {
      lib = [];
    }
    return loadTemplates(lib);
  }

  _list() {
    const { templates, invalid } = this._templates();
    const builtin = new Set(R.builtinTemplates.map((t) => t.id));
    return { type: 'workflow.list.result', templates: templates.map((t) => templatePublic(t, builtin.has(t.id))),
      invalid, runs: this._wires() };
  }

  _wires() {
    const out = [...this.finished];
    if (this.run && !out.some((w) => w.id === this.run.id)) out.push(this._wire(this.run));
    return out;
  }

  _active(run) { return !TERMINAL.has(run.state); }

  _start(msg) {
    if (!isPlain(msg) || typeof msg.template !== 'string' || !SLUG_RE.test(msg.template)) throw new WorkflowError('bad_request', 'template');
    const tpl = this._templates().templates.find((t) => t.id === msg.template);
    if (!tpl) throw new WorkflowError('unknown_template', 'unknown template');
    const opts = parseStart(msg, tpl, { workspaceId: this.workspaceId, providers: this.providers });
    if (unavailableReason(tpl)) throw new WorkflowError('not_allowed', 'this template runs commands; a browser cannot');
    if (this.busy()) throw new WorkflowError('busy', 'a workflow is already running');
    if (this.otherBusy()) throw new WorkflowError('busy', 'a Boss job is active');
    const now = this.clock();
    const run = { id: this.runId(), template: tpl, brief: opts.brief, provider: opts.provider, model: opts.model,
      state: 'waiting', code: null, started: now, ended: null, turns: 0, outTok: 0, limits: browserLimits(tpl),
      stop: null, timer: null };
    run.steps = tpl.steps.map((s, i) => new Step(s, i, opts.provider, opts.model));
    if (this.run) this._keep(this._wire(this.run));
    this.run = run;
    run.timer = this.setTimer(() => this._stopRun(run, 'error', 'timeout'), run.limits.timeoutS * 1000);
    this._drive(run).catch(() => this._finish(run, 'error', 'internal'));
    return { type: 'workflow.started', run: this._wire(run) };
  }

  _setRun(run, state, code = null) {
    if (!R.runTransitions[run.state] || !R.runTransitions[run.state].includes(state)) {
      throw new WorkflowError('bad_transition', `${run.state} → ${state}`);
    }
    run.state = state;
    if (code !== null) run.code = code;
  }

  /** WorkflowEngine._advance, one step after another until the run ends. */
  async _drive(run) {
    this._emit(run);
    while (this._active(run)) {
      if (run.stop) { this._finish(run, run.stop.state, run.stop.code); break; }
      const nxt = run.steps.find((s) => s.state === 'waiting');
      if (!nxt) { this._finish(run, 'done', null); break; }
      if (this.clock() - run.started >= run.limits.timeoutS * 1000) { this._finish(run, 'error', 'timeout'); break; }
      if (nxt.kind === 'approval') {
        await this._approval(run, nxt);
      } else {
        if (run.turns >= run.limits.maxTurns) { this._finish(run, 'error', 'max_turns'); break; }
        if (run.outTok >= run.limits.maxOutTok) { this._finish(run, 'error', 'budget_exceeded'); break; }
        await this._agent(run, nxt);
      }
      this._emit(run);
    }
    this._emit(run);
  }

  _previous(run, index) {
    for (let i = index - 1; i >= 0; i -= 1) {
      const s = run.steps[i];
      if (s.kind === 'agent' && s.state === 'done') return s;
    }
    return null;
  }

  async _agent(run, st) {
    st.setState('running');
    st.started = this.clock();
    if (run.state !== 'running') this._setRun(run, 'running');
    let prompt;
    try {
      const prev = this._previous(run, st.index);
      const p = stepPrompt(st.spec, run.brief, prev ? { result: prev.result, name: prev.spec.role } : null,
        R.maxPromptBytes, this.fence());
      prompt = p.prompt;
      st.truncated = p.truncated;
    } catch (e) {
      if (!(e instanceof WorkflowError)) throw e;
      this._stepFailed(run, st, e.code);
      return;
    }
    this._emit(run);
    let r;
    try {
      r = await this.runTask({ agentKey: st.agent, name: st.spec.role, role: st.spec.role, profile: st.spec.profile,
        prompt, fresh: true, provider: run.provider, model: run.model,
        onStart: (task) => { if (st.state === 'running') { st.task = task; this._emit(run); } } });
    } catch (e) {
      r = { status: 'error', result: null, usage: {}, error: { code: (e && e.code) || 'engine_error' } };
    }
    this._taskEnded(run, st, r || { status: 'error', usage: {}, error: { code: 'engine_error' } });
  }

  /** WorkflowEngine._task_ended (money is not known here: tokens and turns are counted instead, C4). */
  _taskEnded(run, st, r) {
    if (st.state !== 'running') return;
    const usage = isPlain(r.usage) ? r.usage : {};
    st.outTok = Math.max(0, Number(usage.outTok) || 0);
    st.turns = Math.max(0, Math.floor(Number(usage.turns) || 0));
    run.outTok += st.outTok;
    run.turns += st.turns;
    st.result = typeof r.result === 'string' ? r.result : null;
    let [state, code] = has(R.taskOutcome, r.status) ? R.taskOutcome[r.status] : ['error', 'error'];
    const err = isPlain(r.error) && typeof r.error.code === 'string' && CODE_RE.test(r.error.code) ? r.error.code : null;
    if (state !== 'done' && err && ['error', 'denied', 'cancelled'].includes(r.status)) code = err;
    st.setState(state, code);
    st.ended = this.clock();
    if (run.stop) this._finish(run, run.stop.state, run.stop.code);
    else if (state !== 'done') this._finish(run, state, code);
    else if (run.outTok > run.limits.maxOutTok) this._finish(run, 'error', 'budget_exceeded');
    else if (run.turns > run.limits.maxTurns) this._finish(run, 'error', 'max_turns');
  }

  _stepFailed(run, st, code) {
    st.setState('error', code);
    st.ended = this.clock();
    this._finish(run, 'error', code);
  }

  /** WorkflowEngine._start_approval / _approval_done: the user of this page answers on the run card (C5). */
  _approval(run, st) {
    st.setState('approval');
    st.started = this.clock();
    this._setRun(run, 'approval');
    const left = run.limits.timeoutS - (st.started - run.started) / 1000;
    const timeoutS = Math.max(1, Math.min(st.spec.timeoutS, left));
    st.expires = st.started + Math.round(timeoutS * 1000);
    const prev = this._previous(run, st.index);
    if (prev) this.office(prev.agent, 'waiting_for_user');
    this._emit(run);
    return new Promise((resolve) => {
      st.decide = (result) => {
        if (st.decide === null) return;
        st.decide = null;
        this.clearTimer(st.timer);
        st.timer = null;
        if (st.state === 'approval') {
          st.ended = this.clock();
          if (prev) this.office(prev.agent, 'idle');
          if (result === 'ok') {
            st.setState('done');
          } else if (result !== 'stopped') {
            const code = result === 'denied' ? 'denied' : 'approval_timeout';
            st.setState('cancelled', code);
            this._finish(run, 'cancelled', code);
          }
        }
        resolve();
      };
      st.timer = this.setTimer(() => st.decide && st.decide('timeout'), timeoutS * 1000);
    });
  }

  _finish(run, state, code) {
    if (!this._active(run)) return;
    this.clearTimer(run.timer);
    run.timer = null;
    for (const s of run.steps) {
      if (s.state === 'waiting') {
        s.setState('cancelled');
      } else if (s.state === 'approval') {
        s.setState(state === 'cancelled' ? 'cancelled' : 'error', code);
        s.ended = this.clock();
        if (s.decide) s.decide('stopped');
      }
    }
    this._setRun(run, state, code);
    run.ended = this.clock();
    this._emit(run);
  }

  /** WorkflowEngine._stop_run: a waiting run or a pending approval ends at once; a running task is cancelled and the
   * run ends when it reports back. */
  _stopRun(run, state, code) {
    if (!this._active(run)) return;
    if (!run.stop) run.stop = { state, code };
    const cur = run.steps.find((s) => s.state === 'running' || s.state === 'approval');
    if (cur && cur.kind === 'agent') {
      try { this.cancelTask(); } catch { /* the task already ended */ }
      return;
    }
    this._finish(run, state, code);
  }

  _runOf(msg) {
    const id = isPlain(msg) ? msg.run : null;
    if (typeof id !== 'string' || !RUN_ID_RE.test(id)) throw new WorkflowError('unknown_workflow', 'unknown workflow');
    if (this.run && this.run.id === id) return this.run;
    if (this.finished.some((w) => w.id === id)) throw new WorkflowError('unknown_workflow', 'workflow already finished');
    throw new WorkflowError('unknown_workflow', 'unknown workflow');
  }

  _cancel(msg) {
    const run = this._runOf(msg);
    if (!this._active(run)) throw new WorkflowError('unknown_workflow', 'workflow already finished');
    this._stopRun(run, 'cancelled', 'cancelled');
    return { type: 'workflow.ok', run: run.id };
  }

  _answer(msg) {
    const run = this._runOf(msg);
    if (msg.decision !== 'approve' && msg.decision !== 'deny') throw new WorkflowError('bad_request', 'decision');
    if (msg.step !== undefined && msg.step !== null && (typeof msg.step !== 'string' || msg.step.length < 1 || msg.step.length > 32)) {
      throw new WorkflowError('bad_request', 'step');
    }
    const cur = run.steps.find((s) => s.state === 'approval' && s.decide && (msg.step == null || s.id === msg.step));
    if (!cur) throw new WorkflowError('unknown_approval', 'no approval pending');
    cur.decide(msg.decision === 'approve' ? 'ok' : 'denied');
    return { type: 'workflow.ok', run: run.id };
  }

  _wire(run) {
    const cur = run.steps.find((s) => s.state === 'running' || s.state === 'approval');
    return { id: run.id, template: run.template.id, name: run.template.name, state: run.state, code: run.code,
      workspace: { id: this.workspaceId, name: this.workspaceName, sub: null }, startedAt: run.started,
      endedAt: run.ended, costUsd: 0, turns: run.turns, outTok: run.outTok, limits: { ...run.limits },
      current: cur ? cur.id : null, steps: run.steps.map((s) => s.wire()) };
  }

  _keep(wire) {
    this.finished = [...this.finished.filter((w) => w.id !== wire.id), wire].slice(-R.keepFinished);
  }

  _emit(run) {
    const wire = this._wire(run);
    this.emit({ type: 'workflow.state', run: wire });
    this._save();
  }

  _save() {
    if (!this.persist) return;
    try {
      this.persist.save(this._wires().slice(-R.keepFinished));
    } catch { /* storage full or unavailable: the runs are still shown from memory */ }
  }
}
