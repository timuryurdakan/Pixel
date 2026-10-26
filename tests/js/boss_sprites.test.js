// Patron Odası P4 sprites (docs/PATRON_ODASI.md §3.3): the Boss character, his room's furniture/tiles and the
// `folder` bubble. Runs in headless Chrome through tests/test_relay_js.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  validateSprites, listSprites, TILE_NAMES, FURNITURE_NAMES, BUBBLE_KINDS, BOSS_ANIMS, BOSS_DESK_MAX_FILES,
  getBossFrame, getBossDeskSprite, getFurnitureSprite, getTileSprite, getBubbleSprite, getCharacterFrame,
  makeCharacterPalette, CHAR_W, CHAR_H,
} from '../../web/js/sprites.js';

const pixels = (canvas) => canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
const opaque = (canvas) => {
  const d = pixels(canvas);
  let n = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i]) n++;
  return n;
};
const same = (a, b) => {
  const x = pixels(a), y = pixels(b);
  return x.length === y.length && x.every((v, i) => v === y[i]);
};

test('validateSprites stays clean with the Boss room sprites', () => {
  assert.deepEqual(validateSprites(), []);
});

test('existing names are kept and the Boss room names are appended', () => {
  assert.deepEqual(TILE_NAMES.slice(0, 6), ['floor', 'floor_alt', 'rug', 'wall', 'window', 'door']);
  for (const n of ['boss_floor', 'door_front', 'door_side']) assert.ok(TILE_NAMES.includes(n), n);
  assert.deepEqual(FURNITURE_NAMES.slice(0, 9),
    ['desk', 'desk_on', 'chair', 'bookshelf', 'plant', 'coffee_counter', 'water_cooler', 'bed', 'board']);
  for (const n of ['boss_desk', 'boss_chair', 'file_cabinet', 'sofa', 'boss_sign']) assert.ok(FURNITURE_NAMES.includes(n), n);
  assert.equal(BUBBLE_KINDS[BUBBLE_KINDS.length - 1], 'folder');
  assert.ok(BUBBLE_KINDS.includes('paper'));
});

test('Boss room furniture, tiles and bubble have the documented sizes', () => {
  const dims = { boss_desk: [48, 24], boss_chair: [16, 24], file_cabinet: [16, 24], sofa: [32, 16], boss_sign: [16, 8] };
  for (const [n, [w, h]] of Object.entries(dims)) {
    const c = getFurnitureSprite(n);
    assert.equal(c.width, w, `${n} width`);
    assert.equal(c.height, h, `${n} height`);
    assert.ok(opaque(c) > 0, `${n} is not empty`);
  }
  for (const n of ['boss_floor', 'door_front', 'door_side']) {
    const c = getTileSprite(n);
    assert.equal(c.width, 16);
    assert.equal(c.height, 16);
    assert.equal(opaque(c), 256, `${n} tile is fully opaque`);
  }
  for (const f of [0, 1]) {
    const b = getBubbleSprite('folder', f);
    assert.equal(b.width, 12);
    assert.equal(b.height, 12);
  }
  assert.ok(!same(getBubbleSprite('folder', 0), getBubbleSprite('folder', 1)), 'folder bubble animates');
  assert.ok(!same(getBubbleSprite('folder', 0), getBubbleSprite('paper', 0)), 'folder differs from paper');
});

test('Boss animations: required frames, counts, 16x24, and walking directions', () => {
  const want = { boss_sit: 1, boss_write: 2, boss_read: 2, boss_give: 1, boss_stand: 1, boss_walk: 4 };
  for (const [anim, frames] of Object.entries(want)) {
    assert.ok(BOSS_ANIMS[anim], anim);
    assert.equal(BOSS_ANIMS[anim].frames, frames, `${anim} frames`);
  }
  for (const dir of ['down', 'up', 'right', 'left']) assert.ok(BOSS_ANIMS.boss_walk.dirs.includes(dir), `walk ${dir}`);
  for (const [anim, a] of Object.entries(BOSS_ANIMS)) {
    for (const dir of a.dirs) {
      for (let f = 0; f < a.frames; f++) {
        const c = getBossFrame(anim, dir, f);
        assert.equal(c.width, CHAR_W);
        assert.equal(c.height, CHAR_H);
        assert.ok(opaque(c) > 60, `${anim} ${dir} ${f} has a body`);
      }
    }
  }
  assert.ok(!same(getBossFrame('boss_write', 'down', 0), getBossFrame('boss_write', 'down', 1)), 'write animates');
  assert.ok(!same(getBossFrame('boss_read', 'down', 0), getBossFrame('boss_read', 'down', 1)), 'read animates');
  assert.ok(!same(getBossFrame('boss_walk', 'right', 0), getBossFrame('boss_walk', 'left', 0)), 'left is mirrored');
  // unknown input falls back instead of throwing; frame index wraps
  assert.equal(getBossFrame('nope', 'down', 0), getBossFrame('boss_sit', 'down', 0));
  assert.equal(getBossFrame('boss_write', 'down', 3), getBossFrame('boss_write', 'down', 1));
  assert.equal(getBossFrame('boss_sit', 'up', 0), getBossFrame('boss_sit', 'down', 0));
});

test('the Boss uses his own palette: navy suit and red tie, unlike any agent', () => {
  const has = (canvas, rgb) => {
    const d = pixels(canvas);
    for (let i = 0; i < d.length; i += 4) if (d[i] === rgb[0] && d[i + 1] === rgb[1] && d[i + 2] === rgb[2] && d[i + 3]) return true;
    return false;
  };
  const boss = getBossFrame('boss_stand', 'down', 0);
  assert.ok(has(boss, [0x2e, 0x3d, 0x70]), 'navy suit');
  assert.ok(has(boss, [0xd3, 0x2f, 0x2f]), 'red tie');
  for (const seed of [1, 42, 31337, 2840193311]) {
    assert.ok(!same(boss, getCharacterFrame(makeCharacterPalette(seed), 'stand', 'down', 0)));
  }
});

test('desk variants: 0..5 folders, clamped, each count looks different', () => {
  assert.equal(BOSS_DESK_MAX_FILES, 5);
  const seen = [];
  for (let n = 0; n <= BOSS_DESK_MAX_FILES; n++) {
    const c = getBossDeskSprite(n);
    assert.equal(c.width, 48);
    assert.equal(c.height, 24);
    for (const prev of seen) assert.ok(!same(prev, c), `desk with ${n} files differs`);
    seen.push(c);
  }
  assert.equal(getBossDeskSprite(0), getFurnitureSprite('boss_desk'));
  assert.equal(getBossDeskSprite(3), getFurnitureSprite('boss_desk_files_3'));
  assert.equal(getBossDeskSprite(-2), getBossDeskSprite(0));
  assert.equal(getBossDeskSprite(99), getBossDeskSprite(5));
  assert.equal(getBossDeskSprite('x'), getBossDeskSprite(0));
});

test('listSprites reports the Boss animations', () => {
  const l = listSprites();
  assert.deepEqual(Object.keys(l.bossAnims), Object.keys(BOSS_ANIMS));
  assert.equal(l.bossDeskMaxFiles, 5);
});
