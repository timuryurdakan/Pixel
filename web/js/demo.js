// Pixel Office — live demo office (G2, /?demo=1). Runs only in the browser: no transport, no room, no request.
// A fixed cast of agents changes state on its own, and a Boss job plans, sends two specialists to the Boss's desk,
// reviews and reports, over and over. The output has the shapes the real feeds use (v1 Agent JSON, `boss.state`,
// `boss.event`), so main.js hands it to the same CharacterManager and BossScene.

import { t } from './i18n.js';

export const DEMO_DEVICE = 'demo';
export const BOSS_CYCLE_S = 70;            // one Boss job, then a pause before the next one
const STATE_MIN_S = 5;                     // an agent keeps a state this long at least …
const STATE_SPREAD_S = 9;                  // … plus up to this much more

const CAST = [
  { key: 'k1', name: 'Ayşe', project: 'web-shop', seed: 11 },
  { key: 'k2', name: 'Mert', project: 'api-gateway', seed: 23 },
  { key: 'k3', name: 'Deniz', project: 'docs-site', seed: 37 },
  { key: 'k4', name: 'Can', project: 'ml-pipeline', seed: 41 },
];
export const DEMO_AGENT_COUNT = CAST.length;

// Weighted so the office looks busy: mostly working, some thinking, now and then waiting for the user or a break.
const STATE_WEIGHTS = [['working', 5], ['thinking', 3], ['waiting_for_user', 1], ['idle', 1]];
const WORK = [
  { tool: 'Edit', detail: t('sepet.js düzenleniyor') },
  { tool: 'Bash', detail: t('testler çalışıyor') },
  { tool: 'Read', detail: t('README.md okunuyor') },
  { tool: 'Grep', detail: t('TODO aranıyor') },
];

// The Boss's script: [second in the cycle, job state, events]. Specialists k2 and k3 take their orders and deliver.
const BOSS_SCRIPT = [
  [0, 'planning', []],
  [6, 'approval', []],
  [12, 'running', [['take', 'k2']]],
  [15, 'running', [['take', 'k3']]],
  [32, 'running', [['deliver', 'k2']]],
  [38, 'reviewing', [['deliver', 'k3']]],
  [46, 'reporting', []],
  [52, 'done', []],
  [56, null, []],
];

function pickWeighted(random) {
  const total = STATE_WEIGHTS.reduce((s, [, w]) => s + w, 0);
  let r = random() * total;
  for (const [state, w] of STATE_WEIGHTS) {
    if ((r -= w) < 0) return state;
  }
  return STATE_WEIGHTS[0][0];
}

function bossJob(state, startedAt) {
  const orderStates = state === 'running' || state === 'reviewing'
    ? [{ id: 'o1', role: 'kod', state: state === 'running' ? 'running' : 'done' },
      { id: 'o2', role: 'docs', state: state === 'running' ? 'running' : 'done' }]
    : [];
  return { type: 'boss.state', job: 'b_DEMODEMO01', state, orders: 2, orderStates, startedAt };
}

export class DemoOffice {
  /** @param {{random?: () => number}} [o] */
  constructor({ random = Math.random } = {}) {
    this.random = random;
    this.agents = new Map();      // key → Agent JSON
    this.nextChange = new Map();  // key → ms
    this.started = null;
    this.last = null;
    this.bossStep = 0;            // next BOSS_SCRIPT entry of the current cycle
    this.cycle = 0;
  }

  /** The first snapshot (Agent JSON list). */
  start(now) {
    this.started = now;
    this.last = now;
    for (const [i, c] of CAST.entries()) {
      const a = {
        id: `demo-${c.key}`, name: c.name, project: c.project, projectPath: null, title: null,
        state: 'working', tool: null, detail: null, lastMessage: null, lastPrompt: null, isSubagent: false,
        parentId: null, agentType: null, model: null, colorSeed: c.seed, toolCount: 0,
        startedAt: now - (i + 1) * 60000, stateSince: now, updatedAt: now, nameSource: 'auto', defaultName: c.name,
        control: { device: DEMO_DEVICE, agent: c.key },
      };
      this._setState(a, pickWeighted(this.random), now);
      this.agents.set(c.key, a);
      this.nextChange.set(c.key, now + this._holdMs());
    }
    return [...this.agents.values()].map((a) => ({ ...a }));
  }

  /**
   * Advance to `now` (ms). → {upserts: changed Agent JSON, boss: {job?: boss.state | null, events: boss.event[]}}.
   * `boss.job` is present only when the Boss's state changed (null = idle).
   */
  step(now) {
    const out = { upserts: [], boss: { events: [] } };
    if (this.started === null || now < this.last) return out;
    this.last = now;
    for (const [key, a] of this.agents) {
      if (now < this.nextChange.get(key)) continue;
      this._setState(a, pickWeighted(this.random), now);
      this.nextChange.set(key, now + this._holdMs());
      out.upserts.push({ ...a });
    }
    this._stepBoss(now, out.boss);
    return out;
  }

  _stepBoss(now, boss) {
    const elapsed = (now - this.started) / 1000;
    const cycle = Math.floor(elapsed / BOSS_CYCLE_S);
    if (cycle !== this.cycle) { this.cycle = cycle; this.bossStep = 0; }
    const at = elapsed - cycle * BOSS_CYCLE_S;
    const startedAt = this.started + cycle * BOSS_CYCLE_S * 1000;
    while (this.bossStep < BOSS_SCRIPT.length && BOSS_SCRIPT[this.bossStep][0] <= at) {
      const [, state, events] = BOSS_SCRIPT[this.bossStep++];
      boss.job = state ? bossJob(state, startedAt) : null;
      for (const [event, agent] of events) boss.events.push({ type: 'boss.event', event, agent, job: 'b_DEMODEMO01' });
    }
  }

  _holdMs() {
    return (STATE_MIN_S + this.random() * STATE_SPREAD_S) * 1000;
  }

  _setState(a, state, now) {
    const work = state === 'working' ? WORK[Math.floor(this.random() * WORK.length) % WORK.length] : null;
    a.state = state;
    a.tool = work ? work.tool : null;
    a.detail = work ? work.detail : null;
    a.stateSince = now;
    a.updatedAt = now;
    if (work) a.toolCount += 1;
  }
}
