// Pixel Office v4 — Task Board (Kanban Whiteboard - A8, docs/V4_ANTIGRAVITY.md).
// Plain ES module: data model, canvas mini-board renderer, DOM modal dialog and accessibility.

import { BOARD_TILE, TILE } from './office.js';

export const BOARD_W = BOARD_TILE.w;
export const BOARD_H = BOARD_TILE.h;
export const BOARD_X = BOARD_TILE.x * TILE;                  // world px of the board on the top wall (office.js)
export const BOARD_Y = BOARD_TILE.y * TILE + TILE - BOARD_H; // bottom-aligned on its row: 8 = 1 * 16 + 16 - 24

export const COLUMNS = [
  { id: 'queued', label: 'Sırada', color: '#4a8fd6', lightColor: '#cfe2f7' },
  { id: 'working', label: 'Çalışıyor', color: '#5fae4f', lightColor: '#d6f0cf' },
  { id: 'waiting', label: 'Onay bekliyor', color: '#e58b3a', lightColor: '#fbe8d5' },
  { id: 'done', label: 'Bitti', color: '#8e5cc4', lightColor: '#eddff7' },
];

/** v4-C workflow step state → board column and card title. */
const WORKFLOW_COLUMN = { waiting: 'queued', running: 'working', approval: 'waiting' };
const WORKFLOW_TITLE = {
  waiting: 'İş akışı: sırada', running: 'İş akışı: çalışıyor', approval: 'Onayınız bekleniyor',
  done: 'İş akışı: bitti', error: 'İş akışı: hata', cancelled: 'İş akışı: iptal',
};

const money = (v) => `$${(Number.isFinite(v) ? v : 0).toFixed(2)}`;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

/**
 * State mapping for public agents in viewer mode or fallback.
 * Maps agent.state to one of the 4 columns: 'queued' | 'working' | 'waiting' | 'done'.
 */
export function stateToColumn(state) {
  switch (state) {
    case 'spawning':
      return 'queued';
    case 'working':
    case 'thinking':
      return 'working';
    case 'waiting_for_user':
      return 'waiting';
    case 'idle':
    case 'sleeping':
      return 'done';
    default:
      return 'done';
  }
}

/** Turkish label for an agent state in board cards. */
export function stateLabel(state, tool = '') {
  switch (state) {
    case 'spawning': return 'Katılıyor';
    case 'working': return tool ? `Çalışıyor (${tool})` : 'Çalışıyor';
    case 'thinking': return 'Düşünüyor';
    case 'waiting_for_user': return 'Onay bekliyor';
    case 'idle': return 'Kahve molasında';
    case 'sleeping': return 'Uyuyor';
    default: return 'Ofiste';
  }
}

/**
 * Categorize agents, tasks, and approvals into the 4 Kanban columns.
 *
 * Privacy guarantee (V4 A8 spec):
 * When `isPaired` is false (viewer / unauthenticated mode):
 * - NO task prompt, command, or approval summary text is ever exposed.
 * - Only public agent names and public state labels appear.
 * - Workflow steps (v4-C, `workflows` = [{device, run}] of active runs) are only used when paired.
 * - Boss plans waiting for approval (Patron Odası, `bossPlans` = boss.js BossStore.pendingPlans()) likewise; the
 *   card shows numbers only, never the plan's text.
 */
export function categorizeTasks({ agents = [], views = null, approvals = null, isPaired = false, workflows = null,
  bossPlans = null } = {}) {
  const result = {
    queued: [],
    working: [],
    waiting: [],
    done: [],
  };

  const seenAgentIds = new Set();

  // 1. In paired mode, collect pending approvals first (Phase 19 approval requests).
  if (isPaired && approvals && approvals.items) {
    for (const it of approvals.items.values()) {
      const a = it.a;
      if (!a) continue;
      result.waiting.push({
        id: `apv-${a.id}`,
        agentId: null,
        agentName: a.agent || 'Claude Code',
        title: a.ask === 'question' ? 'Seçmeli soru' : (a.tool ? `İzin: ${a.tool}` : 'İzin bekliyor'),
        detail: a.summary || a.project || '',
        isApproval: true,
        expiresAt: a.expiresAt || 0,
      });
    }
  }

  // 2. In paired mode, check active task views if available.
  if (isPaired && views) {
    for (const v of views.values()) {
      if (!v || !v.running) continue;
      const agentKey = v.key;
      // Match to an agent if present
      const ag = agents.find((x) => x.control && x.control.agent === agentKey);
      const agentId = ag ? ag.id : null;
      const agentName = ag ? ag.name : `Ajan ${agentKey.slice(0, 8)}`;
      if (agentId) seenAgentIds.add(agentId);

      const item = {
        id: `task-${v.task || agentKey}`,
        agentId,
        agentName,
        title: v.state === 'confirm' ? 'Onay bekliyor' : (v.state === 'queued' ? 'Kuyrukta' : 'Görev çalışıyor'),
        detail: v.recent || '',
        isApproval: false,
      };

      if (v.state === 'confirm') {
        result.waiting.push(item);
      } else if (v.state === 'queued' || v.state === 'sending') {
        result.queued.push(item);
      } else {
        result.working.push(item);
      }
    }
  }

  // 2b. v4-C workflow steps (paired only): waiting → Sırada, the human checkpoint → Onay bekliyor, finished → Bitti. A
  //     running step is the task view above when this tab follows it, else a Çalışıyor card here.
  if (isPaired && Array.isArray(workflows)) {
    const viewTasks = new Set();
    for (const v of views ? views.values() : []) if (v && v.running && v.task) viewTasks.add(v.task);
    for (const { device, run } of workflows) {
      if (!run || !Array.isArray(run.steps)) continue;
      run.steps.forEach((s, i) => {
        if (s.state === 'running' && s.task && viewTasks.has(s.task)) return;
        const ag = s.agent ? agents.find((x) => x && x.control && x.control.agent === s.agent
          && (!device || x.control.device === device)) : null;
        if (ag) seenAgentIds.add(ag.id);
        const col = WORKFLOW_COLUMN[s.state] || 'done';
        result[col].push({
          id: `wf-${run.id}-${s.id}`,
          agentId: ag ? ag.id : null,
          agentName: s.type === 'approval' ? 'Siz' : (ag ? ag.name : s.role || 'Ajan'),
          title: WORKFLOW_TITLE[s.state] || 'İş akışı',
          detail: `${run.name || 'İş akışı'} · ${i + 1}/${run.steps.length}: ${s.role || s.id}`,
          isApproval: s.state === 'approval',
        });
      });
    }
  }

  // 2c. Patron Odası (paired only): a plan waiting for the user's approval → "Plan onayı · Siz"; the card opens the
  //     Boss's panel (target).
  if (isPaired && Array.isArray(bossPlans)) {
    for (const { job, plan } of bossPlans) {
      if (!job || !plan) continue;
      const n = plan.orders.length;
      result.waiting.push({
        id: `boss-${job.job}`,
        agentId: null,
        agentName: 'Siz',
        title: 'Plan onayı',
        detail: `${plan.round ? 'Patron düzeltme planı' : 'Patron planı'} · ${n} iş emri · tahmini ${money(plan.estimateUsd)} / tavan ${money(plan.maxCostUsd)}`,
        isApproval: true,
        target: 'boss',
      });
    }
  }

  // 3. Process remaining/all agents from the public room snapshot.
  for (const ag of agents) {
    if (!ag || ag.phase === 'gone' || seenAgentIds.has(ag.id)) continue;

    const colId = stateToColumn(ag.state);
    const item = {
      id: `ag-${ag.id}`,
      agentId: ag.id,
      agentName: ag.name || 'İsimsiz Ajan',
      title: stateLabel(ag.state, ag.tool),
      detail: isPaired && ag.project ? ag.project : '',
      isApproval: false,
      colorSeed: ag.colorSeed || 0,
    };

    result[colId].push(item);
  }

  const counts = {
    queued: result.queued.length,
    working: result.working.length,
    waiting: result.waiting.length,
    done: result.done.length,
  };

  return { ...result, counts };
}

/**
 * Draw dynamic mini cards (up to 3 per column) and count indicators onto the board on the canvas.
 * Called inside Renderer when drawing the 'board' furniture item.
 */
export function drawBoardOverlay(ctx, dx, dy, data) {
  if (!data || !data.counts) return;

  const colWidth = 11;
  const colStarts = [dx + 2, dx + 13, dx + 24, dx + 35];
  const cardH = 3;
  const cardW = 9;

  for (let c = 0; c < 4; c++) {
    const colDef = COLUMNS[c];
    const x = colStarts[c];
    const count = data.counts[colDef.id] || 0;
    const cardsToDraw = Math.min(3, count);

    // Draw small sticky-note cards
    for (let k = 0; k < cardsToDraw; k++) {
      const cy = dy + 6 + k * 4;
      ctx.fillStyle = colDef.lightColor;
      ctx.fillRect(x + 1, cy, cardW, cardH);
      ctx.fillStyle = colDef.color;
      ctx.fillRect(x + 1, cy, 2, cardH); // Left accent bar on card
    }

    // If count > 3, draw a subtle extra indicator dot at the bottom
    if (count > 3) {
      ctx.fillStyle = colDef.color;
      ctx.fillRect(x + 4, dy + 19, 3, 2);
    }
  }
}

/**
 * TaskBoard manages the DOM dialog, keyboard accessibility, and user interactions.
 */
export class TaskBoard {
  /**
   * @param {object} opts
   * @param {HTMLDialogElement} opts.dialog
   * @param {(agentId: string) => void} opts.onSelectAgent
   * @param {object} [opts.sound]
   * @param {() => boolean} [opts.isPaired]
   * @param {(target: string) => void} [opts.onOpenTarget]   a card with a `target` (e.g. 'boss': the Boss's panel)
   */
  constructor({ dialog, onSelectAgent, sound = null, isPaired = () => false, onOpenTarget = null }) {
    this.dialog = dialog;
    this.onSelectAgent = onSelectAgent;
    this.onOpenTarget = onOpenTarget;
    this.sound = sound;
    this.isPaired = isPaired;
    this.isOpen = false;
    this.lastCounts = { queued: -1, working: -1, waiting: -1, done: -1 };

    this.container = dialog.querySelector('#board-content');
    this.liveEl = dialog.querySelector('#board-live');
    this.closeBtn = dialog.querySelector('#board-close');

    if (this.closeBtn) {
      this.closeBtn.addEventListener('click', () => this.close());
    }

    this.dialog.addEventListener('close', () => {
      this.isOpen = false;
    });

    this.dialog.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        this.close();
      }
    });
  }

  /**
   * Check if a world-coordinate point hits the board on the office wall.
   */
  static hitTest(wx, wy) {
    return wx >= BOARD_X && wx < BOARD_X + BOARD_W && wy >= BOARD_Y && wy < BOARD_Y + BOARD_H;
  }

  open() {
    if (this.isOpen) return;
    if (typeof this.dialog.showModal === 'function') {
      try {
        this.dialog.showModal();
      } catch {
        this.dialog.setAttribute('open', '');
      }
    } else {
      this.dialog.setAttribute('open', '');
    }
    this.isOpen = true;
    if (this.sound && typeof this.sound.playBlip === 'function') {
      this.sound.playBlip();
    }
  }

  close() {
    if (!this.isOpen) return;
    if (typeof this.dialog.close === 'function') {
      try {
        this.dialog.close();
      } catch {
        this.dialog.removeAttribute('open');
      }
    } else {
      this.dialog.removeAttribute('open');
    }
    this.isOpen = false;
  }

  /**
   * Update screen reader live region if counts changed.
   */
  _announceCounts(counts) {
    if (!this.liveEl) return;
    if (
      counts.queued === this.lastCounts.queued &&
      counts.working === this.lastCounts.working &&
      counts.waiting === this.lastCounts.waiting &&
      counts.done === this.lastCounts.done
    ) {
      return;
    }
    this.lastCounts = { ...counts };
    const text = `Görev tahtası: ${counts.queued} sırada, ${counts.working} çalışıyor, ${counts.waiting} onay bekliyor, ${counts.done} bitti.`;
    this.liveEl.textContent = text;
  }

  /**
   * Re-render the Kanban columns in the modal dialog using textContent only.
   */
  render(data) {
    this._announceCounts(data.counts);

    if (!this.container) return;
    this.container.replaceChildren();

    for (const colDef of COLUMNS) {
      const colEl = el('div', `board-col col-${colDef.id}`);
      const header = el('header', 'col-head');
      const titleWrap = el('div', 'col-title-wrap');

      const dot = el('span', 'col-dot');
      dot.style.backgroundColor = colDef.color;
      const title = el('h4', 'col-title', colDef.label);
      titleWrap.append(dot, title);

      const count = el('span', 'col-count', String(data.counts[colDef.id] || 0));
      header.append(titleWrap, count);
      colEl.appendChild(header);

      const cardList = el('div', 'col-cards');
      const items = data[colDef.id] || [];

      if (items.length === 0) {
        cardList.appendChild(el('div', 'card-empty', 'Kart yok'));
      } else {
        for (const it of items) {
          const card = el('div', `board-card ${it.isApproval ? 'is-approval' : ''}`);
          card.setAttribute('tabindex', '0');
          card.setAttribute('role', 'button');

          const cardHead = el('div', 'card-head');
          const name = el('b', 'card-agent-name', it.agentName);
          cardHead.appendChild(name);

          const status = el('div', 'card-status', it.title);
          card.append(cardHead, status);

          if (it.detail) {
            const detail = el('div', 'card-detail', it.detail);
            card.appendChild(detail);
          }

          const onCardAction = () => {
            if (it.target && this.onOpenTarget) {
              this.close();
              this.onOpenTarget(it.target);
            } else if (it.agentId && this.onSelectAgent) {
              this.onSelectAgent(it.agentId);
              this.close();
            }
          };

          card.addEventListener('click', onCardAction);
          card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onCardAction();
            }
          });

          cardList.appendChild(card);
        }
      }

      colEl.appendChild(cardList);
      this.container.appendChild(colEl);
    }
  }
}
