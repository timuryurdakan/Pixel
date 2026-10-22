// Developer page web/sprite-preview.html (v2.md H36: an external module, so the CSP's script-src 'self' allows it).
import * as S from './sprites.js';

const SCALE = 4;
const errs = S.validateSprites();
const st = document.getElementById('status');
st.textContent = errs.length ? `validateSprites(): ${errs.length} hata\n` + errs.join('\n') : 'validateSprites(): hata yok ✔';
st.className = errs.length ? 'bad' : 'ok';

function show(parent, src, label, scale = SCALE) {
  const cell = document.createElement('div');
  cell.className = 'cell';
  const c = document.createElement('canvas');
  c.className = 'checker';
  c.width = src.width * scale; c.height = src.height * scale;
  const ctx = c.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(src, 0, 0, c.width, c.height);
  cell.appendChild(c);
  const s = document.createElement('span'); s.textContent = label; cell.appendChild(s);
  parent.appendChild(cell);
  return c;
}

const SEEDS = [2840193311, 1, 42, 123456789, 987654321, 31337];
const pals = SEEDS.map((s) => S.makeCharacterPalette(s));

// --- static character frames
const charsEl = document.getElementById('chars');
for (const pal of pals) {
  const row = document.createElement('div'); row.className = 'grid'; row.style.marginBottom = '8px';
  for (const [anim, a] of Object.entries(S.ANIMS)) {
    for (const dir of a.dirs) {
      for (let f = 0; f < a.frames; f++) show(row, S.getCharacterFrame(pal, anim, dir, f), `${anim} ${dir[0]}${f}`);
    }
  }
  charsEl.appendChild(row);
}

// --- tiles, furniture, bubbles, icons
const L = S.listSprites();
for (const n of L.tiles) show(document.getElementById('tiles'), S.getTileSprite(n), n);
for (const n of L.furniture) show(document.getElementById('furniture'), S.getFurnitureSprite(n), n);
for (const k of L.bubbles) for (const f of [0, 1]) show(document.getElementById('bubbles'), S.getBubbleSprite(k, f), `${k} ${f}`);
const TOOLS = ['Bash', 'Read', 'Edit', 'Write', 'Grep', 'Glob', 'WebFetch', 'WebSearch', 'Agent', 'Task', 'TodoWrite', 'mcp__x__y'];
for (const t of TOOLS) show(document.getElementById('icons'), S.getToolIcon(t), t);

// --- live animations
const liveEl = document.getElementById('live');
const live = [];
for (const [anim, a] of Object.entries(S.ANIMS)) {
  for (const dir of a.dirs) {
    const c = show(liveEl, S.getCharacterFrame(pals[0], anim, dir, 0), `${anim} ${dir}`);
    live.push({ c, anim, dir, fps: a.fps || 1 });
  }
}

// --- office scene (same map / anchoring rules as ARCHITECTURE.md §4)
const MAP = [
  '##WW##WW##WW######WW##',
  '#KK.P...KK..P...kCk.Q#',
  '#...............ccc..#',
  '#.TT..TT..TT.........#',
  '#.h...h...h....======#',
  '#..............======#',
  '#.TT..TT..TT...======#',
  '#.h...h...h..........#',
  '#.............###.####',
  '#.TT..TT..TT..#......#',
  '#.h...h...h...#bb==bb#',
  '#.............#BB==BB#',
  '#P...........P#.....P#',
  '####D#################',
];
const scene = document.getElementById('scene');
const SC = 3;
scene.width = 22 * 16 * SC; scene.height = 14 * 16 * SC;
const sctx = scene.getContext('2d');

const people = [
  { seat: [2, 4], kind: 'type', pal: pals[0], tool: 'Bash' },
  { seat: [6, 4], kind: 'sit', pal: pals[1], bubble: 'dots' },
  { seat: [10, 7], kind: 'type', pal: pals[2], tool: 'Edit' },
  { seat: [6, 10], kind: 'sit', pal: pals[3], bubble: 'alert' },
  { stand: [17, 2], kind: 'drink', pal: pals[4], bubble: 'steam' },
  { bed: [15, 10], kind: 'sleep', pal: pals[5], bubble: 'zzz' },
  { bed: [20, 10], kind: 'sleep', pal: pals[2], bubble: 'zzz' },
  { walk: [8, 5], dir: 'right', pal: pals[1] },
  { walk: [4, 11], dir: 'up', pal: pals[3] },
  { walk: [13, 2], dir: 'down', pal: pals[5] },
  { walk: [18, 7], dir: 'left', pal: pals[0] },
];
const busyDesks = new Set(['2,3', '10,6']);

function drawScene(t) {
  const ctx = sctx;
  ctx.setTransform(SC, 0, 0, SC, 0, 0);
  ctx.imageSmoothingEnabled = false;
  for (let y = 0; y < 14; y++) for (let x = 0; x < 22; x++) {
    const ch = MAP[y][x];
    let tile = (x + y) % 2 ? 'floor_alt' : 'floor';
    if (ch === '=') tile = 'rug';
    if (ch === 'D') tile = 'door';
    if (ch === '#') tile = 'wall';
    if (ch === 'W') tile = 'window';
    ctx.drawImage(S.getTileSprite(tile), x * 16, y * 16);
  }
  const draw = [];
  const furn = (name, x, y, sortY) => {
    const img = S.getFurnitureSprite(name);
    const py = name === 'bed' ? y * 16 : y * 16 + 16 - img.height;
    draw.push({ sortY, x: x * 16, fn: () => ctx.drawImage(img, x * 16, py) });
  };
  for (let y = 0; y < 14; y++) for (let x = 0; x < 22; x++) {
    const ch = MAP[y][x], prev = MAP[y][x - 1];
    if (ch === 'T' && prev !== 'T') furn(busyDesks.has(`${x},${y}`) ? 'desk_on' : 'desk', x, y, (y + 1) * 16 - 1);
    if (ch === 'h') furn('chair', x, y, y * 16 + 1);
    if (ch === 'K') furn('bookshelf', x, y, (y + 1) * 16 - 1);
    if (ch === 'P') furn('plant', x, y, (y + 1) * 16 - 1);
    if (ch === 'k' && MAP[y][x + 1] === 'C') furn('coffee_counter', x, y, (y + 1) * 16 - 1);
    if (ch === 'Q') furn('water_cooler', x, y, (y + 1) * 16 - 1);
    if (ch === 'b') furn('bed', x, y, y * 16 + 1);
  }
  const bubbles = [];
  const frameOf = (anim) => Math.floor(t * (S.ANIMS[anim].fps || 1));
  for (const p of people) {
    let px, py, anim, dir, sortY, img, dx, dy;
    if (p.seat) {
      [px, py] = [p.seat[0] * 16 + 8, p.seat[1] * 16 + 15]; anim = p.kind; dir = 'up';
    } else if (p.stand) {
      [px, py] = [p.stand[0] * 16 + 8, p.stand[1] * 16 + 15]; anim = p.kind; dir = 'down';
    } else if (p.bed) {
      anim = 'sleep'; dir = 'down';
      img = S.getCharacterFrame(p.pal, anim, dir, frameOf(anim));
      dx = p.bed[0] * 16; dy = p.bed[1] * 16 + 2; sortY = p.bed[1] * 16 + 2;
      draw.push({ sortY, x: dx, fn: () => ctx.drawImage(img, dx, dy) });
      bubbles.push({ kind: p.bubble, x: dx + 8 + 6, y: p.bed[1] * 16 - 4 });
      continue;
    } else {
      [px, py] = [p.walk[0] * 16 + 8, p.walk[1] * 16 + 15]; anim = 'walk'; dir = p.dir;
    }
    img = S.getCharacterFrame(p.pal, anim, dir, frameOf(anim));
    const X = Math.round(px) - 8, Y = Math.round(py) - 23;
    draw.push({ sortY: py, x: X, fn: () => ctx.drawImage(img, X, Y) });
    if (p.tool) bubbles.push({ icon: p.tool, x: px, y: py - 27 });
    else if (p.bubble) bubbles.push({ kind: p.bubble, x: px, y: py - 27 });
  }
  draw.sort((a, b) => a.sortY - b.sortY || a.x - b.x).forEach((d) => d.fn());
  const bob = Math.round(Math.sin(t * Math.PI * 4)) ;
  for (const b of bubbles) {
    const img = b.icon ? S.getToolIcon(b.icon) : S.getBubbleSprite(b.kind, Math.floor(t * 2));
    ctx.drawImage(img, Math.round(b.x - img.width / 2), Math.round(b.y - img.height / 2 + bob));
  }
}

function loop(ms) {
  const t = ms / 1000;
  drawScene(t);
  for (const l of live) {
    const ctx = l.c.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, l.c.width, l.c.height);
    ctx.drawImage(S.getCharacterFrame(pals[0], l.anim, l.dir, Math.floor(t * l.fps)), 0, 0, l.c.width, l.c.height);
  }
  requestAnimationFrame(loop);
}
drawScene(0);
requestAnimationFrame(loop);
