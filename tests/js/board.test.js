// Task Board tests (v4-A / A8 Kanban whiteboard - docs/V4_ANTIGRAVITY.md).
// Runs in headless Chrome through tests/test_relay_js.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateSprites, getFurnitureSprite, FURNITURE_NAMES } from '../../web/js/sprites.js';
import { Office, SPAWN, BOARD_TILES, BOARD_TILE } from '../../web/js/office.js';
import { findPath } from '../../web/js/pathfinding.js';
import { TaskBoard, categorizeTasks, stateToColumn, stateLabel, BOARD_X, BOARD_Y, BOARD_W, BOARD_H, COLUMNS } from '../../web/js/board.js';

test('sprites validation passes with board furniture sprite included', () => {
  assert.ok(FURNITURE_NAMES.includes('board'), 'FURNITURE_NAMES must include board');
  const sprite = getFurnitureSprite('board');
  assert.ok(sprite, 'board furniture sprite should exist');
  assert.equal(sprite.width, 48);
  assert.equal(sprite.height, 24);
  const errors = validateSprites();
  assert.deepEqual(errors, [], `validateSprites failed with errors: ${errors.join(', ')}`);
});

test('board tiles are unwalkable and impassable on the office wall', () => {
  const office = new Office();
  assert.equal(BOARD_TILES.length, 3);
  for (const t of BOARD_TILES) {
    assert.equal(office.isWalkable(t.x, t.y), false, `Tile (${t.x}, ${t.y}) should not be walkable`);
    assert.equal(office.isPassable(t.x, t.y), false, `Tile (${t.x}, ${t.y}) should not be passable`);
  }
});

test('pathfinding never routes agents through board wall tiles', () => {
  const office = new Office();
  const boardSet = new Set(BOARD_TILES.map((t) => `${t.x},${t.y}`));

  // Check path from spawn to desk 1, 2, 3, water cooler, coffee counter, couch, etc.
  const targets = [
    { x: 1, y: 3 },
    { x: 5, y: 3 },
    { x: 9, y: 3 },
    { x: 16, y: 2 }, // Near coffee
    { x: 18, y: 5 }, // Near couch
  ];

  for (const target of targets) {
    const path = findPath(office.width, office.height, (x, y) => office.isPassable(x, y, SPAWN, target), SPAWN, target);
    assert.ok(path, `Path from spawn to (${target.x}, ${target.y}) must exist`);
    for (const step of path) {
      assert.ok(!boardSet.has(`${step.x},${step.y}`), `Path stepped into board tile: (${step.x}, ${step.y})`);
    }
  }
});

test('TaskBoard.hitTest accurately identifies board boundaries', () => {
  assert.equal(TaskBoard.hitTest(BOARD_X, BOARD_Y), true);
  assert.equal(TaskBoard.hitTest(BOARD_X + 24, BOARD_Y + 12), true);
  assert.equal(TaskBoard.hitTest(BOARD_X + BOARD_W - 1, BOARD_Y + BOARD_H - 1), true);

  // Outside boundaries
  assert.equal(TaskBoard.hitTest(BOARD_X - 1, BOARD_Y), false);
  assert.equal(TaskBoard.hitTest(BOARD_X, BOARD_Y - 1), false);
  assert.equal(TaskBoard.hitTest(BOARD_X + BOARD_W, BOARD_Y), false);
  assert.equal(TaskBoard.hitTest(BOARD_X, BOARD_Y + BOARD_H), false);
});

test('stateToColumn categorizes agent states accurately', () => {
  assert.equal(stateToColumn('spawning'), 'queued');
  assert.equal(stateToColumn('working'), 'working');
  assert.equal(stateToColumn('thinking'), 'working');
  assert.equal(stateToColumn('waiting_for_user'), 'waiting');
  assert.equal(stateToColumn('idle'), 'done');
  assert.equal(stateToColumn('sleeping'), 'done');
  assert.equal(stateToColumn('offline'), 'done');
});

test('privacy: viewer mode (unpaired) never exposes task prompts, commands, or approval text', () => {
  const agents = [
    {
      id: 'a1',
      name: 'Claude Code',
      state: 'working',
      tool: 'bash',
      project: 'secret-repo',
      taskPrompt: 'cat ~/.ssh/id_rsa and delete files',
    },
    {
      id: 'a2',
      name: 'Codex',
      state: 'waiting_for_user',
      taskPrompt: 'deploy to production password=secret',
    },
  ];

  const fakeViews = new Map([
    ['v1', { key: 'a1', running: true, state: 'working', recent: 'rm -rf /private' }],
  ]);

  const fakeApprovals = {
    items: new Map([
      ['apv-1', { a: { id: 'apv-1', agent: 'Claude', tool: 'bash', summary: 'curl evil.com' } }],
    ]),
  };

  const data = categorizeTasks({
    agents,
    views: fakeViews,
    approvals: fakeApprovals,
    isPaired: false, // Viewer mode!
  });

  assert.equal(data.counts.working, 1);
  assert.equal(data.counts.waiting, 1);
  assert.equal(data.counts.queued, 0);

  // Check that NO card contains private strings
  const allCards = [...data.queued, ...data.working, ...data.waiting, ...data.done];
  for (const card of allCards) {
    assert.equal(card.detail, '', 'Card detail must be empty in viewer mode');
    const fullText = JSON.stringify(card);
    assert.ok(!fullText.includes('secret-repo'));
    assert.ok(!fullText.includes('id_rsa'));
    assert.ok(!fullText.includes('password'));
    assert.ok(!fullText.includes('rm -rf'));
    assert.ok(!fullText.includes('evil.com'));
  }
});

test('paired mode populates approval items and active tasks', () => {
  const agents = [
    { id: 'a1', name: 'Claude', state: 'working', project: 'my-project', control: { agent: 'claude-key' } },
  ];
  const approvals = {
    items: new Map([
      ['ap1', { a: { id: '1', agent: 'Claude', tool: 'bash', summary: 'npm test' } }],
    ]),
  };
  const views = new Map([
    ['claude-key', { key: 'claude-key', running: true, state: 'working', recent: 'Running tests...' }],
  ]);

  const data = categorizeTasks({
    agents,
    views,
    approvals,
    isPaired: true,
  });

  assert.equal(data.counts.waiting, 1);
  assert.equal(data.waiting[0].title, 'İzin: bash');
  assert.equal(data.waiting[0].detail, 'npm test');
  assert.equal(data.counts.working, 1);
  assert.equal(data.working[0].detail, 'Running tests...');
});

test('TaskBoard dialog lifecycle, keyboard navigation, and aria-live announcements', () => {
  const dialog = document.createElement('dialog');
  const liveEl = document.createElement('div');
  liveEl.id = 'board-live';
  const content = document.createElement('div');
  content.id = 'board-content';
  const closeBtn = document.createElement('button');
  closeBtn.id = 'board-close';
  dialog.append(closeBtn, liveEl, content);
  document.body.appendChild(dialog);

  let selected = null;
  const board = new TaskBoard({
    dialog,
    onSelectAgent: (id) => { selected = id; },
    isPaired: () => false,
  });

  assert.equal(board.isOpen, false);
  board.open();
  assert.equal(board.isOpen, true);

  const sampleData = {
    queued: [{ id: '1', agentId: 'ag-1', agentName: 'Agent1', title: 'Katılıyor' }],
    working: [{ id: '2', agentId: 'ag-2', agentName: 'Agent2', title: 'Çalışıyor' }],
    waiting: [],
    done: [],
    counts: { queued: 1, working: 1, waiting: 0, done: 0 },
  };

  board.render(sampleData);

  // Verify live region announcement
  assert.ok(liveEl.textContent.includes('1 sırada'));
  assert.ok(liveEl.textContent.includes('1 çalışıyor'));

  // Verify columns rendered
  const cols = content.querySelectorAll('.board-col');
  assert.equal(cols.length, 4);

  // Click on a card
  const card1 = content.querySelector('.board-card');
  assert.ok(card1);
  assert.equal(card1.getAttribute('role'), 'button');
  assert.equal(card1.getAttribute('tabindex'), '0');

  card1.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(selected, 'ag-1');
  assert.equal(board.isOpen, false);

  // Test Enter key on card
  board.open();
  const card2 = content.querySelectorAll('.board-card')[1];
  card2.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  assert.equal(selected, 'ag-2');
  assert.equal(board.isOpen, false);

  // Close with close button
  board.open();
  closeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(board.isOpen, false);

  dialog.remove();
});
