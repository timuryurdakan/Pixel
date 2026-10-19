// Pixel Office — procedural pixel-art sprites (Phase 2).
// Spec: docs/ARCHITECTURE.md §5. No imports, no side effects at import time:
// every sprite is a string pixel map (one char per pixel, '.' = transparent)
// plus a palette; canvases are built lazily and cached.

export const TILE = 16;
export const CHAR_W = 16;
export const CHAR_H = 24;

/** @type {Record<string,{frames:number,fps:number,dirs:string[]}>} */
export const ANIMS = {
  stand: { frames: 1, fps: 0, dirs: ['down', 'up', 'right', 'left'] },
  walk: { frames: 4, fps: 8, dirs: ['down', 'up', 'right', 'left'] },
  sit: { frames: 1, fps: 0, dirs: ['up'] },
  type: { frames: 2, fps: 6, dirs: ['up'] },
  drink: { frames: 2, fps: 1.5, dirs: ['down'] },
  sleep: { frames: 2, fps: 1, dirs: ['down'] },
};

export const TILE_NAMES = ['floor', 'floor_alt', 'rug', 'wall', 'window', 'door'];
export const FURNITURE_NAMES = ['desk', 'desk_on', 'chair', 'bookshelf', 'plant', 'coffee_counter', 'water_cooler', 'bed'];
export const BUBBLE_KINDS = ['zzz', 'dots', 'alert', 'steam', 'question', 'coffee', 'speech', 'heart', 'happy'];
export const TOOL_ICON_KINDS = ['terminal', 'eye', 'pencil', 'magnifier', 'globe', 'people', 'checklist', 'gear'];

// ---------------------------------------------------------------------------
// Map helpers (pure string operations)
// ---------------------------------------------------------------------------

const TRANSPARENT = '.';
const ERASE = '_'; // in overlay layers: force a pixel back to transparent

function mirror(rows) {
  return rows.map((r) => r.split('').reverse().join(''));
}

function hcat(...parts) {
  const h = parts[0].length;
  const out = [];
  for (let y = 0; y < h; y++) out.push(parts.map((p) => p[y]).join(''));
  return out;
}

function blank(w, h) {
  return Array.from({ length: h }, () => TRANSPARENT.repeat(w));
}

/** Overlay layers [{rows, x, y}] onto a w×h canvas map. '.' skips, '_' erases. */
function compose(w, h, layers) {
  const grid = Array.from({ length: h }, () => new Array(w).fill(TRANSPARENT));
  for (const { rows, x = 0, y = 0 } of layers) {
    for (let j = 0; j < rows.length; j++) {
      const gy = y + j;
      if (gy < 0 || gy >= h) continue;
      const row = rows[j];
      for (let i = 0; i < row.length; i++) {
        const gx = x + i;
        const ch = row[i];
        if (gx < 0 || gx >= w || ch === TRANSPARENT) continue;
        grid[gy][gx] = ch === ERASE ? TRANSPARENT : ch;
      }
    }
  }
  return grid.map((r) => r.join(''));
}

/** Tile a small motif to w×h. */
function tileMotif(motif, w, h) {
  const out = [];
  for (let y = 0; y < h; y++) {
    const m = motif[y % motif.length];
    let row = '';
    while (row.length < w) row += m;
    out.push(row.slice(0, w));
  }
  return out;
}

/** Add a 1-px 4-neighbour outline (char k) around all non-transparent pixels. */
function outlined(rows, k = 'K') {
  const h = rows.length, w = rows[0].length;
  return rows.map((row, y) => row.split('').map((ch, x) => {
    if (ch !== TRANSPARENT) return ch;
    const n = (xx, yy) => yy >= 0 && yy < h && xx >= 0 && xx < w && rows[yy][xx] !== TRANSPARENT;
    return (n(x - 1, y) || n(x + 1, y) || n(x, y - 1) || n(x, y + 1)) ? k : ch;
  }).join(''));
}

// ---------------------------------------------------------------------------
// Colour helpers
// ---------------------------------------------------------------------------

function hexToRgb(hex) {
  const v = parseInt(hex.slice(1, 7), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}
function rgbToHex([r, g, b]) {
  const c = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');
  return `#${c(r)}${c(g)}${c(b)}`;
}
function darken(hex, amt = 0.15) {
  return rgbToHex(hexToRgb(hex).map((v) => v * (1 - amt)));
}
function mix(a, b, t) {
  const A = hexToRgb(a), B = hexToRgb(b);
  return rgbToHex(A.map((v, i) => v + (B[i] - v) * t));
}

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------------------
// Palettes
// ---------------------------------------------------------------------------

const SKINS = ['#f5d0b0', '#e8b48f', '#d39a6a', '#b07a4f', '#8a5a3b', '#5e3d29'];
const HAIRS = ['#2b2b2b', '#5a3825', '#8b5a2b', '#d8b04a', '#b8432f', '#e8e0d0', '#3b4fa8', '#7a3fa0'];
const SHIRTS = ['#d64545', '#e07b39', '#e3c14b', '#5fae4f', '#3f9a8f', '#3f7fd0', '#5050c8', '#9a50c8', '#d050a0', '#7a7a7a', '#f0f0f0', '#2f3a4a'];
const PANTS = ['#2f3a5a', '#3a3a3a', '#5a4630', '#2a4a3a', '#4a4a6a', '#1f2a44'];

const CHAIR_FABRIC = '#3f7f9e';
const CHAIR_FABRIC_SHADE = '#2d5d76';
const CHAIR_METAL = '#6b7180';

// Fixed character colours (§5.2) + additions: r blush (derived from skin),
// C/c chair fabric and g chair metal (chair backrest drawn in sit/type frames).
const CHAR_FIXED = {
  K: '#1b1b2a', E: '#1b1b2a', W: '#f4f4f4', O: '#3a2a22', M: '#c8553d',
  B: '#5b7bd5', b: '#46609f', C: CHAIR_FABRIC, c: CHAIR_FABRIC_SHADE, g: CHAIR_METAL,
};
const CHAR_KEYS = new Set(['.', ...Object.keys(CHAR_FIXED), 'S', 's', 'H', 'h', 'T', 't', 'P', 'p', 'r']);

/** @returns {{skin,skinShade,hair,hairShade,shirt,shirtShade,pants,pantsShade,key:string}} */
export function makeCharacterPalette(seed) {
  const rng = mulberry32((Number(seed) >>> 0) || 0);
  const pick = (arr) => arr[Math.floor(rng() * arr.length) % arr.length];
  const skin = pick(SKINS);
  const hair = pick(HAIRS);
  const shirt = pick(SHIRTS);
  const pants = pick(PANTS);
  const pal = {
    skin, skinShade: darken(skin),
    hair, hairShade: darken(hair, 0.25),
    shirt, shirtShade: darken(shirt),
    pants, pantsShade: darken(pants),
    key: '',
  };
  pal.key = paletteKey(pal);
  return pal;
}

function paletteKey(p) {
  return [p.skin, p.skinShade, p.hair, p.hairShade, p.shirt, p.shirtShade, p.pants, p.pantsShade].join('');
}

function charColorMap(p) {
  return {
    ...CHAR_FIXED,
    S: p.skin, s: p.skinShade || darken(p.skin),
    H: p.hair, h: p.hairShade || darken(p.hair, 0.25),
    T: p.shirt, t: p.shirtShade || darken(p.shirt),
    P: p.pants, p: p.pantsShade || darken(p.pants),
    r: mix(p.skin, '#ff5a6e', 0.35),
  };
}

// Shared palette for tiles & furniture.
const PAL = {
  K: '#2b2233', // outline
  k: '#4a3326', // darkest wood / shelf interior
  d: '#7a4f33', // dark wood
  w: '#9c6640', // wood
  l: '#bf8452', // light wood
  y: '#d9a56e', // wood highlight
  g: '#9aa0ab', G: '#6b7180', a: '#c9ced6', A: '#eef0f4', W: '#ffffff',
  s: '#2c3347', u: '#55617f', // monitor off
  b: '#123a46', c: '#8ff5cf', C: '#4fc3e8', // monitor on
  e: '#4a8fd6', E: '#33639c',
  r: '#d64545', R: '#9e2f2f', o: '#e58b3a', Y: '#f2cf4a',
  n: '#5fae4f', N: '#3b7d3a', m: '#9ad66f',
  p: '#f4ead6', P: '#cfc0a2',
  t: '#c8653d', T: '#8f4128',
  v: '#8e5cc4', h: '#d86aa6',
  i: '#e2f4ff', I: '#a8daf5', j: '#62a9dc',
  x: '#3a2a22',
  B: '#5b7bd5', z: '#46609f',
  // walls
  M: '#4f4263', L: '#76658f', q: '#f1e4cb', Q: '#e5d4b4',
  // floor
  F: '#c68b59', f: '#b67b4b', H: '#d29a68', S: '#8e5a37',
};
const PAL_FLOOR_ALT = { ...PAL, F: '#bd8253', f: '#ad7345', H: '#c8905f', S: '#86532f' };
const PAL_RUG = { '.': null, 1: '#b5524a', 2: '#9a4039', 3: '#e6b85c', 4: '#f3dfb6', 5: '#7d3330' };
const PAL_CHAIR = { K: PAL.K, C: CHAIR_FABRIC, c: CHAIR_FABRIC_SHADE, g: CHAIR_METAL, G: '#4b505c', a: '#9aa0ab' };
const PAL_BUBBLE = {
  K: '#1b1b2a', W: '#ffffff', w: '#dfe4ee', x: '#3a3f55', z: '#e8f0ff', Z: '#9fb6ff',
  r: '#e53935', R: '#b71c1c', e: '#3f7fd0', E: '#2a5aa0', m: 'rgba(255,255,255,0.95)', M: 'rgba(170,184,206,0.9)',
  t: '#c8553d', T: '#8f3b2a', c: '#5a3a28', y: '#ffd84a',
};
const PAL_ICON = {
  K: '#1b1b2a', W: '#ffffff', x: '#2b2b3a', n: '#5fd068', N: '#3a9a48', g: '#9aa0ab', G: '#6b7180',
  Y: '#f2cf4a', o: '#e58b3a', h: '#e86a9a', p: '#f0c890', e: '#4a8fd6', E: '#2f5f9e', i: '#bfe6ff',
  S: '#f2c9a0', T: '#d64545', t: '#3f7fd0', r: '#e53935', a: '#c9ced6',
};

// ---------------------------------------------------------------------------
// Character parts (16 wide). Frame = 24 rows; feet on row 23.
// ---------------------------------------------------------------------------

const HEAD_DOWN = [
  '....KKKKKKKK....',
  '...KHHHHHHHHK...',
  '..KHHHHHHHHHhK..',
  '..KHHHHHHHHHhK..',
  '..KHHHHHHHHhhK..',
  '..KHSSHSSSSShK..',
  '..KSSSSSSSSSSK..',
  '..KSSESSSSESSK..',
  '..KSSESSSSESsK..',
  '..KsrSSssSSrsK..',
  '...KsSSSSSSsK...',
  '....KKKKKKKK....',
];
const HEAD_UP = [
  '....KKKKKKKK....',
  '...KHHHHHHHHK...',
  '..KHHHHHHHHHhK..',
  '..KHHHHHHHHHhK..',
  '..KHHHHHHHHHhK..',
  '..KHHHHHHHHHhK..',
  '..KhHHHHHHHHhK..',
  '..KhHHHHHHHhhK..',
  '..KhhHHHHHHhhK..',
  '..KShhhhhhhhSK..',
  '...KsSSSSSSsK...',
  '....KKKKKKKK....',
];
const HEAD_SIDE = [
  '....KKKKKKK.....',
  '...KHHHHHHHK....',
  '..KHHHHHHHHHK...',
  '..KHHHHHHHHHHK..',
  '..KhHHHHHHHHHK..',
  '..KhHHHHHSSSHK..',
  '..KhHHHSSSSSSK..',
  '..KhhHSSSSSESK..',
  '..KhhSsSSSSESSK.',
  '...KhSSSSSSrSK..',
  '....KsSSSSSSK...',
  '.....KKKKKKK....',
];

const TORSO_DOWN = [
  '...KTTTTTTTTK...',
  '..KTTTTTTTTTTK..',
  '..KTtTTTTTTtTK..',
  '..KTKTTTTTTKTK..',
  '..KSKtTTTTtKSK..',
  '..KKKPPPPPPKKK..',
];
// left (viewer) arm back, right arm forward
const TORSO_DOWN_SWING = [
  '...KTTTTTTTTK...',
  '..KTTTTTTTTTTK..',
  '..KSKTTTTTTtTK..',
  '..KKKTTTTTTKTK..',
  '...KKtTTTTtKTK..',
  '....KPPPPPPKSK..',
  '...........KKK..',
];
const TORSO_UP = [
  '...KTTTTTTTTK...',
  '..KTTTTTTTTTTK..',
  '..KTTTTTTTTTTK..',
  '..KTKtTTTTtKTK..',
  '..KSKttttttKSK..',
  '..KKKPPPPPPKKK..',
];
const TORSO_UP_SWING = [
  '...KTTTTTTTTK...',
  '..KTTTTTTTTTTK..',
  '..KSKTTTTTTTTK..',
  '..KKKtTTTTtKTK..',
  '...KKttttttKTK..',
  '....KPPPPPPKSK..',
  '...........KKK..',
];
const TORSO_SIDE = [
  '....KTTTTTTK....',
  '...KTTTTTTTTK...',
  '...KTTKTTKTtK...',
  '...KTTKTTKTtK...',
  '...KttKSSKttK...',
  '....KPPKKPPK....',
];
const TORSO_SIDE_FWD = [
  '....KTTTTTTK....',
  '...KTTTTTTTTK...',
  '...KTTKTTKTtK...',
  '...KTTtKTTKtK...',
  '...KttttKSSKK...',
  '....KPPPPKKKK...',
];
const TORSO_SIDE_BACK = [
  '....KTTTTTTK....',
  '...KTTTTTTTTK...',
  '...KTKTTKTTtK...',
  '...KKTTKTTTtK...',
  '..KSSKttttttK...',
  '..KKKKPPPPPK....',
];

const LEGS_STAND = [
  '....KPPPPPPK....',
  '....KPpKKpPK....',
  '....KPpKKpPK....',
  '...KOOOKKOOOK...',
  '....KKK..KKK....',
];
const LEGS_STEP = [ // viewer-left foot planted, right foot lifted
  '....KPPPPPPK....',
  '....KPpKKpPK....',
  '....KPpKKOOOK...',
  '...KOOOKKKKK....',
  '....KKK.........',
];
const LEGS_SIDE = [
  '....KPPPPPK.....',
  '.....KPPpK......',
  '.....KPPpK......',
  '.....KOOOOK.....',
  '.....KKKKK......',
];
const LEGS_SIDE_STEP = [
  '....KPPPPPPK....',
  '...KpPK.KPPK....',
  '..KpPK...KPPK...',
  '.KOOK....KOOOK..',
  '..KK......KKK...',
];
const LEGS_SIDE_STEP2 = [
  '....KPPPPPPK....',
  '...KPPK.KpPK....',
  '..KPPK...KpPK...',
  '.KOOK....KOOOK..',
  '..KK......KKK...',
];

// Seated (back to camera). Chair backrest matches the chair sprite rows 9..13
// (chair drawn at tile top, character frame at tile top - 8 → frame rows 17..21).
const CHAIR_BACK = [
  '..KKKKKKKKKKKK..',
  '..KCCCCCCCCCCK..',
  '..KCccccccccCK..',
  '..KccccccccccK..',
  '...KKKKKKKKKK...',
];
const TORSO_SIT = [
  '...KTTTTTTTTK...',
  '..KTTTTTTTTTTK..',
  '..KTtTTTTTTtTK..',
  '..KTtTTTTTTtTK..',
  '..KttttttttttK..',
  '..KKKKKKKKKKKK..',
];
const ARMS_TYPE = [ // left hand up, right hand down (rows 12..17)
  'KKKK............',
  'KSSK........KKKK',
  'KTTK........KSSK',
  '.KTK........KTTK',
  '............KTK.',
  '................',
];

const MUG_CHEST = [ // overlay rows 14..18
  '........KKKKK...',
  '........KWMMKK..',
  '........KMMMKSK.',
  '........KMMMKSK.',
  '........KKKKKKK.',
];
const MUG_MOUTH = [ // overlay rows 10..18 (arm raised, eyes closed)
  '................', // 8 eyes row handled separately
  '......KKKKK.....',
  '......KWMMKK....',
  '......KMMMKSK...',
  '......KMMMKSK...',
  '......KKKKKKTK..',
  '...........KTK..',
  '...........KTK..',
  '............K_..',
];
const EYES_CLOSED = [
  '.....S....S.....',
  '....EE....EE....',
];

const SLEEP_HEAD = [
  '................',
  '....KKKKKKKK....',
  '...KHHHHHHHHK...',
  '..KHHHHHHHHHHK..',
  '..KHHHHHHHHHhK..',
  '..KHSSHSSSSShK..',
  '..KSSSSSSSSSSK..',
  '..KSEESSSSEESK..',
  '..KsrSSSSSSrsK..',
  '...KsSSssSSsK...',
];
const SLEEP_BLANKET = [
  '..KKKKKKKKKKKK..',
  '..WWSSWWWWSSWW..',
  '..bBBBBBBBBBBb..',
  '..bBBBBBBBBBBb..',
  '..bBBBBBBBBBBb..',
  '..bBBBBbBBBBBb..',
  '..bBBBBbBBBBBb..',
  '..bbBBBbBBBBbb..',
  '..bbBBBBBBBBbb..',
  '..bbBBBBBBBBbb..',
  '..bbbBBBBBBbbb..',
  '..bbbBBBBBBbbb..',
  '..bbbbBBBBbbbb..',
  '..bbbbbbbbbbbb..',
];

function L(rows, y, x = 0) { return { rows, x, y }; }

const CHAR_FRAME_DEFS = {
  'stand:down:0': [L(LEGS_STAND, 19), L(TORSO_DOWN, 13), L(HEAD_DOWN, 1)],
  'stand:up:0': [L(LEGS_STAND, 19), L(TORSO_UP, 13), L(HEAD_UP, 1)],
  'stand:right:0': [L(LEGS_SIDE, 19), L(TORSO_SIDE, 13), L(HEAD_SIDE, 1)],

  'walk:down:0': [L(LEGS_STEP, 19), L(TORSO_DOWN_SWING, 14), L(HEAD_DOWN, 2)],
  'walk:down:1': [L(LEGS_STAND, 19), L(TORSO_DOWN, 13), L(HEAD_DOWN, 1)],
  'walk:down:2': [L(mirror(LEGS_STEP), 19), L(mirror(TORSO_DOWN_SWING), 14), L(HEAD_DOWN, 2)],
  'walk:down:3': [L(LEGS_STAND, 19), L(TORSO_DOWN, 13), L(HEAD_DOWN, 1)],

  'walk:up:0': [L(mirror(LEGS_STEP), 19), L(TORSO_UP_SWING, 14), L(HEAD_UP, 2)],
  'walk:up:1': [L(LEGS_STAND, 19), L(TORSO_UP, 13), L(HEAD_UP, 1)],
  'walk:up:2': [L(LEGS_STEP, 19), L(mirror(TORSO_UP_SWING), 14), L(HEAD_UP, 2)],
  'walk:up:3': [L(LEGS_STAND, 19), L(TORSO_UP, 13), L(HEAD_UP, 1)],

  'walk:right:0': [L(LEGS_SIDE_STEP, 19), L(TORSO_SIDE_BACK, 14), L(HEAD_SIDE, 2)],
  'walk:right:1': [L(LEGS_SIDE, 19), L(TORSO_SIDE, 13), L(HEAD_SIDE, 1)],
  'walk:right:2': [L(LEGS_SIDE_STEP2, 19), L(TORSO_SIDE_FWD, 14), L(HEAD_SIDE, 2)],
  'walk:right:3': [L(LEGS_SIDE, 19), L(TORSO_SIDE, 13), L(HEAD_SIDE, 1)],

  'sit:up:0': [L(TORSO_SIT, 15), L(HEAD_UP, 3), L(CHAIR_BACK, 17)],
  'type:up:0': [L(TORSO_SIT, 15), L(HEAD_UP, 3), L(ARMS_TYPE, 12), L(CHAIR_BACK, 17)],
  'type:up:1': [L(TORSO_SIT, 15), L(HEAD_UP, 4), L(mirror(ARMS_TYPE), 12), L(CHAIR_BACK, 17)],

  'drink:down:0': [L(LEGS_STAND, 19), L(TORSO_DOWN, 13), L(HEAD_DOWN, 1), L(MUG_CHEST, 14)],
  'drink:down:1': [L(LEGS_STAND, 19), L(TORSO_DOWN, 13), L(HEAD_DOWN, 1), L(EYES_CLOSED, 8), L(MUG_MOUTH, 9)],

  'sleep:down:0': [L(SLEEP_HEAD, 0), L(SLEEP_BLANKET, 10)],
  'sleep:down:1': [L(SLEEP_HEAD, 0), L(SLEEP_BLANKET, 9)],
};

const charMapCache = new Map();

function resolveDir(anim, dir) {
  const a = ANIMS[anim];
  if (a.dirs.length === 1) return a.dirs[0];
  return a.dirs.includes(dir) ? dir : 'down';
}

/** Pixel map (array of strings) for a character frame. */
function characterMap(anim, dir, frame) {
  const key = `${anim}:${dir}:${frame}`;
  let m = charMapCache.get(key);
  if (m) return m;
  if (dir === 'left' && anim !== 'sit' && anim !== 'type' && anim !== 'drink' && anim !== 'sleep') {
    m = mirror(characterMap(anim, 'right', frame));
  } else {
    const def = CHAR_FRAME_DEFS[key];
    if (!def) throw new Error(`no character frame ${key}`);
    m = compose(CHAR_W, CHAR_H, def);
  }
  charMapCache.set(key, m);
  return m;
}

// ---------------------------------------------------------------------------
// Tiles (16×16)
// ---------------------------------------------------------------------------

const FLOOR = [
  'HHHHHHHHHHHSHHHH',
  'FFFFFFFFFFFSFFFF',
  'FFfFFFFFFFFSFFFf',
  'SSSSSSSSSSSSSSSS',
  'HHHHSHHHHHHHHHHH',
  'FFFFSFFFFFFFfFFF',
  'FFFFSFFFFFFFFFFF',
  'SSSSSSSSSSSSSSSS',
  'HHHHHHHHHHHHHHSH',
  'FFFfFFFFFFFFFFSF',
  'FFFFFFFFFFFFFFSF',
  'SSSSSSSSSSSSSSSS',
  'HHHHHHHHSHHHHHHH',
  'FFFFFFFFSFFFFfFF',
  'FfFFFFFFSFFFFFFF',
  'SSSSSSSSSSSSSSSS',
];

const RUG_MOTIF = [
  '1112111111121111',
  '1121211111212111',
  '1213121112131211',
  '2134312121343121',
  '1213121112131211',
  '1121211111212111',
  '1112111111121111',
  '1111111311111111',
  '1111111311111111',
  '1111113431111111',
  '1111111311111111',
  '1111111311111111',
  '1112111111121111',
  '1121211111212111',
  '1213121112131211',
  '2134312121343121',
];

const WALL = [
  'MMMMMMMMMMMMMMMM',
  'LLLLLLLLLLLLLLLL',
  'LLLLLLLLLLLLLLLL',
  'MMMMMMMMMMMMMMMM',
  'KKKKKKKKKKKKKKKK',
  'qqqqqqqqqqqqqqqq',
  'qqQqqqqqqqQqqqqq',
  'qqQqqqqqqqQqqqqq',
  'qqQqqqqqqqQqqqqq',
  'qqQqqqqqqqQqqqqq',
  'qqQqqqqqqqQqqqqq',
  'qqQqqqqqqqQqqqqq',
  'QQQQQQQQQQQQQQQQ',
  'dddddddddddddddd',
  'wwwwwwwwwwwwwwww',
  'kkkkkkkkkkkkkkkk',
];

const WINDOW = [
  'MMMMMMMMMMMMMMMM',
  'LLLLLLLLLLLLLLLL',
  'LLLLLLLLLLLLLLLL',
  'MMMMMMMMMMMMMMMM',
  'KKKKKKKKKKKKKKKK',
  'qKKKKKKKKKKKKKKq',
  'qKiiIIIKKiiIIIKq',
  'qKiIIIjKKiIIIjKq',
  'qKIIIjjKKIIIjjKq',
  'qKKKKKKKKKKKKKKq',
  'qKIIIIjKKIIIIjKq',
  'qKIIijjKKIIijjKq',
  'QyyyyyyyyyyyyyyQ',
  'dllllllllllllllk',
  'wwwwwwwwwwwwwwww',
  'kkkkkkkkkkkkkkkk',
];

const DOOR = [
  'MLdFFFFFFFFFFdLM',
  'MLdFFFFFFFFFFdLM',
  'MLdSSSSSSSSSSdLM',
  'MLdFttttttttFdLM',
  'MLdFtRRRRRRtFdLM',
  'MLdFtRYYYYRtFdLM',
  'MLdFtRRRRRRtFdLM',
  'MLdFtRYYYYRtFdLM',
  'MLdFtRRRRRRtFdLM',
  'MLdFttttttttFdLM',
  'MLdFFFFFFFFFFdLM',
  'MLdSSSSSSSSSSdLM',
  'MLdFFFFFFFFFFdLM',
  'MLdffffffffffdLM',
  'MLdaaaaaaaaaadLM',
  'MLdggggggggggdLM',
];

// ---------------------------------------------------------------------------
// Furniture
// ---------------------------------------------------------------------------

const DESK_LEFT_TOP = [
  '..KKKKKKKKKKKK..',
  '..KGGGGGGGGGGK..',
  '..KG########GK..',
  '..KG########GK..',
  '..KG########GK..',
  '..KG########GK..',
  '..KG########GK..',
  '..KGGGGGGGGGGK..',
  '..KKKKKGGKKKKK..',
];
const DESK_LEFT_BOTTOM = [
  'KKKKKKKGGKKKKKKK',
  'KyyyyKKGGKKyyyyy',
  'KlllKggggggKllll',
  'KlllKKKKKKKKllll',
  'KllKaaaaaaaaKlll',
  'KllKaGaGaGaGKlll',
  'KllKKKKKKKKKKlll',
  'Klllllllllllllll',
  'Kddddddddddddddd',
  'Kwwwwwwwwwwwwwww',
  'Kwwwwwwwwwwwwwww',
  'Kddddddddddddddd',
  'KKKKKKKKKKKKKKKK',
  '.Kdk............',
  '.KKK............',
];
const DESK_RIGHT = [
  '................',
  '................',
  '................',
  '..........KK....',
  '.........KnmK...',
  '........KnNmnK..',
  '.........KNnK...',
  '.......KKKKKKK..',
  '.......KtttttK..',
  'KKKKKKKKKKKKKKKK',
  'yyyyyyyyyyyyyyyK',
  'lKKKKKKKllKKKKlK',
  'lKppppPKllKxxKlK',
  'lKpPPpPKllKWWKKK',
  'lKpppppKllKWWKlK',
  'lKKKKKKKllKKKKlK',
  'lllllllllllllllK',
  'dddddddddddddddK',
  'wwwwwwwkkwwwwwwK',
  'wwwwwwwwwwwwwwwK',
  'dddddddddddddddK',
  'KKKKKKKKKKKKKKKK',
  '............kdK.',
  '............KKK.',
];

function deskMap(on) {
  const screen = on
    ? ['bcccbCCb', 'bbCCCcbb', 'bcbCCbbb', 'bbccbCCb', 'bbbbbbbb']
    : ['uussssss', 'usssssss', 'ssssssss', 'ssssssss', 'ssssssss'];
  const top = DESK_LEFT_TOP.map((r, i) => (i >= 2 && i <= 6 ? r.replace('########', screen[i - 2]) : r));
  return hcat([...top, ...DESK_LEFT_BOTTOM], DESK_RIGHT);
}

const CHAIR = [
  '................',
  '................',
  '................',
  '................',
  '....KKKKKKKK....',
  '...KCCCCCCCCK...',
  '..KCCCCCCCCCCK..',
  '..KcCCCCCCCCcK..',
  '..KccccccccccK..',
  '..KKKKKKKKKKKK..',
  '..KCCCCCCCCCCK..',
  '..KCccccccccCK..',
  '..KccccccccccK..',
  '...KKKKKKKKKK...',
  '...KGKKggKKGK...',
  '...KK.KKKK.KK...',
];

const BOOKSHELF = (() => {
  const inner1 = [
    'kkkkkkkkkkkk',
    'rRkkkkYokkkk',
    'rReEkkYokvhk',
    'rReEnNYokvhk',
    'rReEnNYoKvhk',
    'rReEnNYoKvhk',
  ];
  const inner2 = [
    'kkkkkkkkkkkk',
    'kkkkkkkkkPpk',
    'eEkoYkkkkPpk',
    'eEkoYmNkrRpk',
    'eEkoYmNkrRpk',
    'eEkoYmNkrRpk',
  ];
  const sh = (s) => 'Kd' + s + 'dK';
  return [
    'KKKKKKKKKKKKKKKK',
    'KyyyyyyyyyyyyyyK',
    'KwwwwwwwwwwwwwwK',
    'KdKKKKKKKKKKKKdK',
    ...inner1.map(sh),
    sh('llllllllllll'),
    sh('dddddddddddd'),
    ...inner2.map(sh),
    sh('llllllllllll'),
    sh('dddddddddddd'),
    'KdwwwwwwwwwwwwdK',
    'KdwwwwkwwkwwwwdK',
    'KddddddddddddddK',
    'KKKKKKKKKKKKKKKK',
  ];
})();

const PLANT = [
  '.......KK.......',
  '......KmmK..KK..',
  '..KK..KmnK.KmmK.',
  '.KmmK.KmnK.KmnK.',
  '.KmnnKKnnKKmnNK.',
  '..KnnNKnNKnnNK..',
  '.KKKnNKnNKnNKKK.',
  'KmmmKnNnNnNKmmmK',
  '.KnnnNNnNnNNnnK.',
  '..KKnnNNNNnnKK..',
  '...KmKNNNNKmK...',
  '..KmnKKNNKKnmK..',
  '..KKKKKKKKKKKK..',
  '..KtttttttttTK..',
  '..KTTTTTTTTTTK..',
  '...KttttttttK...',
  '...KtyttttttK...',
  '...KtyttttTTK...',
  '...KttttttTTK...',
  '....KtttttTK....',
  '....KtttttTK....',
  '....KTTTTTTK....',
  '.....KKKKKK.....',
  '................',
];

const COUNTER_LEFT = [
  '................',
  '................',
  '................',
  '................',
  '................',
  '................',
  '...........KKK..',
  '..KKKK....KaaaK.',
  '..KWWKK...KxaxK.',
  '..KWWKWK..KxxxK.',
  'KKKWWKKKKKKxxxKK',
  'KyKKKKyyyyKKKKKy',
  'Klllllllllllllll',
  'Klllllllllllllll',
  'Kddddddddddddddd',
  'KKKKKKKKKKKKKKKK',
  'Kyyyyyyyyyyyyyyy',
  'Kwwwwwwwwwwwwwww',
  'Kwwwwwwwwwwwwkww',
  'Kwwwwwwwwwwwwkww',
  'Kwwwwwwwwwwwwwww',
  'Kddddddddddddddd',
  'Kkkkkkkkkkkkkkkk',
  'KKKKKKKKKKKKKKKK',
];
const COUNTER_MID = [
  '...KKKKKKKKKK...',
  '..KAAAAAAAAAAK..',
  '..KaaaaaaaaaaK..',
  '..KaKKKKKKKKaK..',
  '..KaKnnnKrYKaK..',
  '..KaKKKKKKKKaK..',
  '..KaaaaaaaaagK..',
  '..KgggKKKKgggK..',
  '..KgggGKKGgggK..',
  '..KggKWWWWKggK..',
  'KKKggKtttTKggKKK',
  'yyKggKtttTKggKyy',
  'llKgKKKKKKKKgKll',
  'llKKKKKKKKKKKKll',
  'dddddddddddddddd',
  'KKKKKKKKKKKKKKKK',
  'Kyyyyyyyyyyyyyyy',
  'Kwwwwwwwwwwwwwww',
  'Kwwwwwwkkwwwwwww',
  'Kwwwwwwkkwwwwwww',
  'Kwwwwwwwwwwwwwww',
  'Kddddddddddddddd',
  'Kkkkkkkkkkkkkkkk',
  'KKKKKKKKKKKKKKKK',
];
const COUNTER_RIGHT = compose(16, 24, [
  L(mirror(COUNTER_LEFT), 0),
  L([
    '..KK.......____.',
    '.KmnK..___.____.',
    'KmnNK..KKKK____.',
    '.KNK...KpPK____.',
    '.KtK...KpPKKK...',
    '.KTK...KpPK.....',
  ], 4),
]);

const WATER_COOLER = [
  '.....KKKKKK.....',
  '....KiIIIIjK....',
  '...KiIIIIIIjK...',
  '...KiIIIIIIjK...',
  '...KIjjjjjjjK...',
  '...KjjjjjjjjK...',
  '...KjIjjjjjEK...',
  '....KjjjjjEK....',
  '......KEEK......',
  '...KKKKKKKKKK...',
  '...KAAAAAAAaK...',
  '...KAaaaaaaaK...',
  '...KAKKAAKKaK...',
  '...KArKAAeKaK...',
  '...KAAAAAAAaK...',
  '...KAKKKKKKaK...',
  '...KAKGGGGKaK...',
  '...KAKKKKKKaK...',
  '...KAAAAAAAaK...',
  '...KAAAAAAAaK...',
  '...KaaaaaaaaK...',
  '...KaaaaaaaaK...',
  '...KGGGGGGGGK...',
  '...KKKKKKKKKK...',
];

const BED = [
  '.KKKKKKKKKKKKKK.',
  'KyyyyyyyyyyyyyyK',
  'KwwwwwwwwwwwwwwK',
  'KddddddddddddddK',
  'KkAAAAAAAAAAAAkK',
  'KkAKKKKKKKKKKAkK',
  'KkKWWWWWWWWWWKkK',
  'KkKWWWWWWWWWaKkK',
  'KkKaWWWWWWWaaKkK',
  'KkAKKKKKKKKKKAkK',
  'KkAAAAAAAAAAAAkK',
  'KkaaaaaaaaaaaakK',
  'KkKKKKKKKKKKKKkK',
  'KkWWWWWWWWWWWWkK',
  'KkBBBBBBBBBBBBkK',
  'KkBBBBBBBBBBBBkK',
  'KkBBzBBBBBBzBBkK',
  'KkBBBBBBBBBBBBkK',
  'KkBBBBBBBBBBBBkK',
  'KkBBBBBBBBBBBBkK',
  'KkBBzBBBBBBzBBkK',
  'KkBBBBBBBBBBBBkK',
  'KkBBBBBBBBBBBBkK',
  'KkBBBBBBBBBBBBkK',
  'KkBBzBBBBBBzBBkK',
  'KkzzzzzzzzzzzzkK',
  'KkzzzzzzzzzzzzkK',
  'KkKKKKKKKKKKKKkK',
  'KyyyyyyyyyyyyyyK',
  'KwwwwwwwwwwwwwwK',
  'KddddddddddddddK',
  'KKKKKKKKKKKKKKKK',
];

// ---------------------------------------------------------------------------
// Bubbles (2 frames each) & tool icons
// ---------------------------------------------------------------------------

const Z_A = [
  '............',
  '.......zzzz.',
  '.........z..',
  '........z...',
  '.......zzzz.',
  '.zzzzz......',
  '....z.......',
  '...z........',
  '..z.........',
  '.zzzzz......',
];
const Z_B = [
  '........zzz.',
  '.........z..',
  '........zzz.',
  '............',
  '.zzzzz......',
  '....z.......',
  '...z........',
  '..z.........',
  '.zzzzz......',
  '............',
];

const DOTS_A = [
  '..KKKKKKKKKK..',
  '.KWWWWWWWWWWK.',
  'KWWWWWWWWWWWWK',
  'KWWxxWxxWxxWWK',
  'KWWxxWxxWxxWWK',
  'KWWWWWWWWWWWWK',
  '.KWWWWWWWWWWK.',
  '..KKKKKKKKKK..',
  '..KWK.........',
  '...K..........',
];
const DOTS_B = [
  '..KKKKKKKKKK..',
  '.KWWWWWWWWWWK.',
  'KWWWWWxxWWWWWK',
  'KWWxxWxxWxxWWK',
  'KWWxxWWWWxxWWK',
  'KWWWWWWWWWWWWK',
  '.KWWWWWWWWWWK.',
  '..KKKKKKKKKK..',
  '..KWK.........',
  '...K..........',
];

const ALERT_A = [
  '..KKKKKK..',
  '.KrrrrrrK.',
  'KrrrWWrrrK',
  'KrrrWWrrrK',
  'KrrrWWrrrK',
  'KrrrWWrrrK',
  'KrrrrrrrrK',
  'KrrrWWrrrK',
  '.KRrrrrRK.',
  '..KKrrKK..',
  '...KrK....',
  '...KK.....',
];
const ALERT_B = ALERT_A.map((r) => r.replace(/r/g, 'y').replace(/R/g, 'Y').replace(/W/g, 'r').replace(/y/g, 'W').replace(/Y/g, 'w'));

const QUESTION_A = [
  '..KKKKKK..',
  '.KeeeeeeK.',
  'KeeWWWWeeK',
  'KeWWeeWWeK',
  'KeeeeeWWeK',
  'KeeeeWWeeK',
  'KeeeWWeeeK',
  'KeeeeeeeeK',
  '.KEeWWeEK.',
  '..KKeeKK..',
  '...KeK....',
  '...KK.....',
];
const QUESTION_B = QUESTION_A.map((r) => r.replace(/E/g, 'e'));

const STEAM_A = [
  '...m....',
  '..m..M..',
  '..m...M.',
  '...m..M.',
  '...m.M..',
  '..m..M..',
  '.m....M.',
  '..m.....',
];
const STEAM_B = mirror(STEAM_A);

const BUBBLE_FRAME_12 = [
  '.KKKKKKKKKK.',
  'KWWWWWWWWWWK',
  'KWWWWWWWWWWK',
  'KWWWWWWWWWWK',
  'KWWWWWWWWWWK',
  'KWWWWWWWWWWK',
  'KWWWWWWWWWWK',
  'KWWWWWWWWWWK',
  'KWWWWWWWWWWK',
  'KWWWWWWWWWWK',
  'KWWWWWWWWWWK',
  '.KKKKKKKKKK.',
];
const COFFEE_INNER_A = [
  '...m..m...',
  '..m..m....',
  '...m..m...',
  '.KKKKKK...',
  '.KcccctKK.',
  '.KttttKKtK',
  '.KttttK.tK',
  '.KTtttKKK.',
  '..KTTTK...',
  '...KKK....',
];
const COFFEE_INNER_B = [
  '..m..m....',
  '...m..m...',
  '..m..m....',
  ...COFFEE_INNER_A.slice(3),
];
const SPEECH_A = [
  '.KKKKKKKKKKKK.',
  'KWWWWWWWWWWWWK',
  'KWxxxWxxWWWWWK',
  'KWWWWWWWWWWWWK',
  'KWxxWxxxxxWWWK',
  'KWWWWWWWWWWWWK',
  '.KKKWKKKKKKKK.',
  '...KWK........',
  '...KK.........',
  '..............',
];
const SPEECH_B = [
  '.KKKKKKKKKKKK.',
  'KWWWWWWWWWWWWK',
  'KWxxWxxxWWWWWK',
  'KWWWWWWWWWWWWK',
  'KWxxxxWxxWWWWK',
  'KWWWWWWWWWWWWK',
  '.KKKWKKKKKKKK.',
  '...KWK........',
  '...KK.........',
  '..............',
];

const HEART_A = [
  '..KK...KK...',
  '.KrrK.KrrK..',
  'KrrrrKrrrrK.',
  'KrrrrrrrrrK.',
  'KrrrrrrrrrK.',
  '.KrrrrrrrK..',
  '..KrrrrrK...',
  '...KrrrK....',
  '....KrK.....',
  '.....K......',
];
const HEART_B = [
  '............',
  '..KK...KK...',
  '.KrrK.KrrK..',
  'KrrrrrrrrrK.',
  'KrrrrrrrrrK.',
  '.KrrrrrrrK..',
  '..KrrrrrK...',
  '...KrrrK....',
  '....KrK.....',
  '.....K......',
];

const HAPPY_A = [
  '..KKKKKKKK..',
  '.KyyyyyyyyK.',
  'KyyKyyyyKyyK',
  'KyyKyyyyKyyK',
  'KyyyyyyyyyyK',
  'KyyKyyyyKyyK',
  'KyyyKKKKyyyK',
  '.KyyyyyyyyK.',
  '..KKKKKKKK..',
  '............',
];
const HAPPY_B = [
  '..KKKKKKKK..',
  '.KyyyyyyyyK.',
  'KyyKyyyyKyyK',
  'KyyKyyyyKyyK',
  'KyyyyyyyyyyK',
  'KyyyKKKKyyyK',
  'KyyKyyyyKyyK',
  '.KyyyyyyyyK.',
  '..KKKKKKKK..',
  '............',
];

const ICONS = {
  terminal: [
    '..........',
    'xxxxxxxxxx',
    'xxxxxxxxxx',
    'xnxxxxxxxx',
    'xxnxxxxxxx',
    'xxxnxxxxxx',
    'xxnxxxxxxx',
    'xnxxxnnnxx',
    'xxxxxxxxxx',
    '..........',
  ],
  eye: [
    '..........',
    '..........',
    '...xxxx...',
    '.xxWeeWxx.',
    'xWWeExeWWx',
    'xWWeeEeWWx',
    '.xxWeeWxx.',
    '...xxxx...',
    '..........',
    '..........',
  ],
  pencil: [
    '.......KK.',
    '......KhhK',
    '.....KYKhK',
    '....KYYoK.',
    '...KYYoK..',
    '..KYYoK...',
    '.KpYoK....',
    '.KppK.....',
    '.KxK......',
    '..........',
  ],
  magnifier: [
    '.xxxx.....',
    'xiiiix....',
    'xiWiiix...',
    'xiWiiix...',
    'xiiiiix...',
    '.xiiixG...',
    '..xxxxGG..',
    '......GGG.',
    '.......GGG',
    '........G.',
  ],
  globe: [
    '...xxxx...',
    '.xxennexx.',
    '.xennneex.',
    'xennnneeex',
    'xeennneenx',
    'xeeeneennx',
    'xeeeeennnx',
    '.xeeeennx.',
    '.xxeeeexx.',
    '...xxxx...',
  ],
  people: [
    '......xxx.',
    '.xxx.xSSSx',
    'xSSSxxSSSx',
    'xSSSx.xxx.',
    '.xxx.xtttx',
    'xTTTxttttt',
    'TTTTTxtttt',
    'TTTTTxtttt',
    'TTTTT.....',
    '..........',
  ],
  checklist: [
    '..........',
    'xxx.......',
    'xnx.xxxxx.',
    'xxx.......',
    '..........',
    'xxx.......',
    'xnx.xxxx..',
    'xxx.......',
    '..........',
    '..........',
  ],
  gear: [
    '....GG....',
    '.G.GggG.G.',
    '..GggggG..',
    '.GggGGggG.',
    'GggG..GggG',
    'GggG..GggG',
    '.GggGGggG.',
    '..GggggG..',
    '.G.GggG.G.',
    '....GG....',
  ],
};

// ---------------------------------------------------------------------------
// Registry of static sprites: name → {w, h, rows (lazy), pal}
// ---------------------------------------------------------------------------

function def(w, h, pal, build) {
  let rows = null;
  return { w, h, pal, get rows() { return rows || (rows = build()); } };
}

const TILES = {
  floor: def(16, 16, PAL, () => FLOOR),
  floor_alt: def(16, 16, PAL_FLOOR_ALT, () => FLOOR),
  rug: def(16, 16, PAL_RUG, () => tileMotif(RUG_MOTIF, 16, 16)),
  wall: def(16, 16, PAL, () => WALL),
  window: def(16, 16, PAL, () => WINDOW),
  door: def(16, 16, PAL, () => DOOR),
};

const FURNITURE = {
  desk: def(32, 24, PAL, () => deskMap(false)),
  desk_on: def(32, 24, PAL, () => deskMap(true)),
  chair: def(16, 16, PAL_CHAIR, () => CHAIR),
  bookshelf: def(16, 24, PAL, () => BOOKSHELF),
  plant: def(16, 24, PAL, () => PLANT),
  coffee_counter: def(48, 24, PAL, () => hcat(COUNTER_LEFT, COUNTER_MID, COUNTER_RIGHT)),
  water_cooler: def(16, 24, PAL, () => WATER_COOLER),
  bed: def(16, 32, PAL, () => BED),
};

const zzz = (rows) => outlined(rows, 'K');
const iconBubble = (inner) => compose(12, 12, [L(BUBBLE_FRAME_12, 0), L(inner, 1)]);

const BUBBLES = {
  zzz: [def(12, 10, PAL_BUBBLE, () => zzz(Z_A)), def(12, 10, PAL_BUBBLE, () => zzz(Z_B))],
  dots: [def(14, 10, PAL_BUBBLE, () => DOTS_A), def(14, 10, PAL_BUBBLE, () => DOTS_B)],
  alert: [def(10, 12, PAL_BUBBLE, () => ALERT_A), def(10, 12, PAL_BUBBLE, () => ALERT_B)],
  steam: [def(8, 8, PAL_BUBBLE, () => STEAM_A), def(8, 8, PAL_BUBBLE, () => STEAM_B)],
  question: [def(10, 12, PAL_BUBBLE, () => QUESTION_A), def(10, 12, PAL_BUBBLE, () => QUESTION_B)],
  coffee: [def(12, 12, PAL_BUBBLE, () => iconBubble(COFFEE_INNER_A)), def(12, 12, PAL_BUBBLE, () => iconBubble(COFFEE_INNER_B))],
  speech: [def(14, 10, PAL_BUBBLE, () => SPEECH_A), def(14, 10, PAL_BUBBLE, () => SPEECH_B)],
  heart: [def(12, 10, PAL_BUBBLE, () => HEART_A), def(12, 10, PAL_BUBBLE, () => HEART_B)],
  happy: [def(12, 10, PAL_BUBBLE, () => HAPPY_A), def(12, 10, PAL_BUBBLE, () => HAPPY_B)],
};

const ICON_DEFS = Object.fromEntries(
  Object.entries(ICONS).map(([k, inner]) => [k, def(12, 12, { ...PAL_ICON }, () => iconBubble(inner))]),
);

const TOOL_TO_ICON = {
  Bash: 'terminal', BashOutput: 'terminal', KillShell: 'terminal', KillBash: 'terminal', Monitor: 'terminal', PowerShell: 'terminal',
  Read: 'eye', NotebookRead: 'eye', LS: 'eye',
  Edit: 'pencil', Write: 'pencil', MultiEdit: 'pencil', NotebookEdit: 'pencil',
  Grep: 'magnifier', Glob: 'magnifier', ToolSearch: 'magnifier',
  WebFetch: 'globe', WebSearch: 'globe',
  Agent: 'people', Task: 'people', SendMessage: 'people',
  TodoWrite: 'checklist', TaskCreate: 'checklist', TaskUpdate: 'checklist', ExitPlanMode: 'checklist',
};

/** Map any tool name to one of TOOL_ICON_KINDS. */
export function toolIconKind(toolName) {
  if (!toolName) return 'gear';
  if (TOOL_TO_ICON[toolName]) return TOOL_TO_ICON[toolName];
  if (/^Web/.test(toolName)) return 'globe';
  if (/Edit|Write/.test(toolName)) return 'pencil';
  return 'gear';
}

// ---------------------------------------------------------------------------
// Canvas building + caching
// ---------------------------------------------------------------------------

const canvasCache = new Map();
let fallbackCanvas = null;
const warned = new Set();

function makeCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

function renderMap(rows, pal) {
  const h = rows.length, w = rows[0].length;
  const c = makeCanvas(w, h);
  const ctx = c.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  for (let y = 0; y < h; y++) {
    const row = rows[y];
    let x = 0;
    while (x < w) {
      const ch = row[x];
      let end = x + 1;
      while (end < w && row[end] === ch) end++;
      const col = pal[ch];
      if (ch !== TRANSPARENT && col) {
        ctx.fillStyle = col;
        ctx.fillRect(x, y, end - x, 1);
      }
      x = end;
    }
  }
  return c;
}

function cached(key, build) {
  let c = canvasCache.get(key);
  if (!c) {
    c = build();
    canvasCache.set(key, c);
  }
  return c;
}

function fallback(what) {
  if (!warned.has(what)) {
    warned.add(what);
    console.warn(`[sprites] unknown sprite: ${what}`);
  }
  if (!fallbackCanvas) {
    fallbackCanvas = renderMap(tileMotif(['mx', 'xm'], 16, 16), { m: '#ff00ff', x: '#000000' });
  }
  return fallbackCanvas;
}

function renderDef(key, d) {
  return cached(key, () => renderMap(d.rows, d.pal));
}

/** @returns {HTMLCanvasElement} 16x24. frame is taken modulo ANIMS[anim].frames. */
export function getCharacterFrame(palette, anim, dir, frame) {
  const a = ANIMS[anim] || ANIMS.stand;
  if (!ANIMS[anim]) anim = 'stand';
  const d = resolveDir(anim, dir);
  const f = ((Math.floor(frame) || 0) % a.frames + a.frames) % a.frames;
  const pal = palette || makeCharacterPalette(0);
  const key = `c|${anim}|${d}|${f}|${paletteKey(pal)}`;
  return cached(key, () => renderMap(characterMap(anim, d, f), charColorMap(pal)));
}

/** name ∈ floor|floor_alt|rug|wall|window|door. @returns {HTMLCanvasElement} 16x16 */
export function getTileSprite(name) {
  const d = TILES[name];
  return d ? renderDef(`t|${name}`, d) : fallback(`tile:${name}`);
}

/** name ∈ desk|desk_on|chair|bookshelf|plant|coffee_counter|water_cooler|bed. */
export function getFurnitureSprite(name) {
  const d = FURNITURE[name];
  return d ? renderDef(`f|${name}`, d) : fallback(`furniture:${name}`);
}

/** kind ∈ zzz|dots|alert|steam|question (+ coffee, speech). */
export function getBubbleSprite(kind, frame = 0) {
  const frames = BUBBLES[kind];
  if (!frames) return fallback(`bubble:${kind}`);
  const f = ((Math.floor(frame) || 0) % frames.length + frames.length) % frames.length;
  return renderDef(`b|${kind}|${f}`, frames[f]);
}

const PAL_CAT = {
  K: '#1b1b2a',
  O: '#e67e22',
  o: '#ba5808',
  W: '#fdfefe',
  P: '#f1948a',
  E: '#1b1b2a',
  G: '#2ecc71',
};

const CAT_SLEEP_0 = [
  '................',
  '................',
  '................',
  '................',
  '................',
  '................',
  '.....KK...KK....',
  '....KPKK.KPKK...',
  '...KOOOKKOOOK...',
  '..KOOOOOOOOOOK..',
  '..KOOEEEOOOOOK..',
  '..KOOWWWWOOOOK..',
  '..KOOOOOOOOOOK..',
  '...KOOOOOOOOK...',
  '....KKKKKKKK....',
  '................',
];

const CAT_SLEEP_1 = [
  '................',
  '................',
  '................',
  '................',
  '................',
  '................',
  '.....KK...KK....',
  '....KPKK.KPKK...',
  '...KOOOKKOOOK...',
  '..KOOOOOOOOOOK..',
  '..KOOEEEOOOOOK..',
  '..KOOWWWWOOOOK..',
  '...KOOOOOOOOK...',
  '....KOOOOOOK....',
  '.....KKKKKK.....',
  '................',
];

const CAT_SIT_0 = [
  '................',
  '................',
  '....KK....KK....',
  '...KPKK..KPKK...',
  '..KOOOKKKKOOOK..',
  '..KOOOOOOOOOOK..',
  '..KOEGOOOOGEOK..',
  '..KOEEOOOOEEOK..',
  '..KOOOWPWOOOOK..',
  '...KOOWWWOOOK...',
  '...KOOOOOOOOK.K.',
  '..KOOWWWWOOOKKK.',
  '..KOOWWWWOOOOK..',
  '..KOOOOOOOOOOK..',
  '...KKWKKKKWKK...',
  '................',
];

const CAT_SIT_1 = [
  '................',
  '................',
  '....KK....KK....',
  '...KPKK..KPKK...',
  '..KOOOKKKKOOOK..',
  '..KOOOOOOOOOOK..',
  '..KOEGOOOOGEOK..',
  '..KOEEOOOOEEOK..',
  '..KOOOWPWOOOOK..',
  '...KOOWWWOOOK.K.',
  '...KOOOOOOOOKKK.',
  '..KOOWWWWOOOOK..',
  '..KOOWWWWOOOOK..',
  '..KOOOOOOOOOOK..',
  '...KKWKKKKWKK...',
  '................',
];

const CAT_SPRITES = {
  sleep: [
    { w: 16, h: 16, pal: PAL_CAT, rows: CAT_SLEEP_0 },
    { w: 16, h: 16, pal: PAL_CAT, rows: CAT_SLEEP_1 },
  ],
  sit: [
    { w: 16, h: 16, pal: PAL_CAT, rows: CAT_SIT_0 },
    { w: 16, h: 16, pal: PAL_CAT, rows: CAT_SIT_1 },
  ],
};

/** @returns {HTMLCanvasElement} 16x16 office mascot cat frame */
export function getCatFrame(anim = 'sleep', frame = 0) {
  const frames = CAT_SPRITES[anim] || CAT_SPRITES.sleep;
  const f = ((Math.floor(frame) || 0) % frames.length + frames.length) % frames.length;
  return renderDef(`cat|${anim}|${f}`, frames[f]);
}

/** @returns {HTMLCanvasElement} 12x12 icon in a white bubble. */
export function getToolIcon(toolName) {
  const kind = toolIconKind(toolName);
  return renderDef(`i|${kind}`, ICON_DEFS[kind]);
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

function checkMap(errors, label, rows, w, h, allowed) {
  if (!Array.isArray(rows)) { errors.push(`${label}: not an array`); return; }
  if (rows.length !== h) errors.push(`${label}: ${rows.length} rows, expected ${h}`);
  rows.forEach((r, y) => {
    if (typeof r !== 'string') { errors.push(`${label}[${y}]: not a string`); return; }
    if (r.length !== w) errors.push(`${label}[${y}]: width ${r.length}, expected ${w}: "${r}"`);
    for (const ch of r) {
      if (ch !== TRANSPARENT && !allowed(ch)) {
        errors.push(`${label}[${y}]: unknown palette char '${ch}'`);
        break;
      }
    }
  });
}

/** Dev self-check. @returns {string[]} errors ([] = ok) */
export function validateSprites() {
  const errors = [];
  const inPal = (pal) => (ch) => Object.prototype.hasOwnProperty.call(pal, ch) && pal[ch];
  const safe = (label, fn) => { try { fn(); } catch (e) { errors.push(`${label}: ${e.message}`); } };

  // character parts: 16 wide, chars from the character palette (+ '_' erase)
  const parts = {
    HEAD_DOWN, HEAD_UP, HEAD_SIDE, TORSO_DOWN, TORSO_DOWN_SWING, TORSO_UP, TORSO_UP_SWING,
    TORSO_SIDE, TORSO_SIDE_FWD, TORSO_SIDE_BACK, LEGS_STAND, LEGS_STEP, LEGS_SIDE, LEGS_SIDE_STEP,
    LEGS_SIDE_STEP2, CHAIR_BACK, TORSO_SIT, ARMS_TYPE, MUG_CHEST, MUG_MOUTH, EYES_CLOSED, SLEEP_HEAD, SLEEP_BLANKET,
  };
  for (const [n, rows] of Object.entries(parts)) {
    checkMap(errors, `part ${n}`, rows, 16, rows.length, (ch) => CHAR_KEYS.has(ch) || ch === ERASE);
  }
  for (const [anim, a] of Object.entries(ANIMS)) {
    for (const dir of a.dirs) {
      for (let f = 0; f < a.frames; f++) {
        safe(`char ${anim}:${dir}:${f}`, () => {
          checkMap(errors, `char ${anim}:${dir}:${f}`, characterMap(anim, dir, f), CHAR_W, CHAR_H, (ch) => CHAR_KEYS.has(ch));
        });
      }
    }
  }
  // chair backrest in sit frames must match the chair sprite (rows 9..13)
  safe('chair backrest', () => {
    const fr = characterMap('sit', 'up', 0);
    for (let i = 0; i < CHAIR_BACK.length; i++) {
      if (CHAIR[9 + i] !== CHAIR_BACK[i] && fr[17 + i] !== CHAIR[9 + i]) {
        errors.push(`chair backrest row ${i} differs from chair sprite row ${9 + i}`);
      }
    }
  });
  const expect = {
    floor: [16, 16], floor_alt: [16, 16], rug: [16, 16], wall: [16, 16], window: [16, 16], door: [16, 16],
    desk: [32, 24], desk_on: [32, 24], chair: [16, 16], bookshelf: [16, 24], plant: [16, 24],
    coffee_counter: [48, 24], water_cooler: [16, 24], bed: [16, 32],
  };
  for (const n of TILE_NAMES) if (!TILES[n]) errors.push(`missing tile ${n}`);
  for (const n of FURNITURE_NAMES) if (!FURNITURE[n]) errors.push(`missing furniture ${n}`);
  for (const [n, d] of Object.entries({ ...TILES, ...FURNITURE })) {
    const [w, h] = expect[n] || [d.w, d.h];
    if (d.w !== w || d.h !== h) errors.push(`${n}: declared ${d.w}x${d.h}, expected ${w}x${h}`);
    safe(n, () => checkMap(errors, n, d.rows, w, h, inPal(d.pal)));
  }
  const bubbleDims = { zzz: [12, 10], dots: [14, 10], alert: [10, 12], steam: [8, 8], question: [10, 12], coffee: [12, 12], speech: [14, 10], heart: [12, 10], happy: [12, 10] };
  for (const [k, frames] of Object.entries(BUBBLES)) {
    if (frames.length !== 2) errors.push(`bubble ${k}: ${frames.length} frames, expected 2`);
    frames.forEach((d, i) => {
      const [w, h] = bubbleDims[k];
      if (d.w !== w || d.h !== h) errors.push(`bubble ${k}: declared ${d.w}x${d.h}, expected ${w}x${h}`);
      safe(`bubble ${k}#${i}`, () => checkMap(errors, `bubble ${k}#${i}`, d.rows, w, h, inPal(d.pal)));
    });
  }
  for (const [k, inner] of Object.entries(ICONS)) {
    checkMap(errors, `icon ${k} (inner)`, inner, 10, 10, inPal(PAL_ICON));
    safe(`icon ${k}`, () => checkMap(errors, `icon ${k}`, ICON_DEFS[k].rows, 12, 12, inPal(PAL_ICON)));
  }
  for (const k of TOOL_ICON_KINDS) if (!ICON_DEFS[k]) errors.push(`missing icon ${k}`);
  // palette determinism / shape
  const p1 = makeCharacterPalette(2840193311), p2 = makeCharacterPalette(2840193311);
  if (p1.key !== p2.key) errors.push('makeCharacterPalette is not deterministic');
  for (const f of ['skin', 'skinShade', 'hair', 'hairShade', 'shirt', 'shirtShade', 'pants', 'pantsShade']) {
    if (!/^#[0-9a-f]{6}$/.test(p1[f])) errors.push(`palette field ${f} not #rrggbb: ${p1[f]}`);
  }
  return errors;
}

/** For the preview page / debugging: names of everything drawable. */
export function listSprites() {
  return {
    tiles: [...TILE_NAMES],
    furniture: [...FURNITURE_NAMES],
    bubbles: [...BUBBLE_KINDS],
    icons: [...TOOL_ICON_KINDS],
    anims: Object.fromEntries(Object.entries(ANIMS).map(([k, v]) => [k, { ...v, dirs: [...v.dirs] }])),
  };
}
