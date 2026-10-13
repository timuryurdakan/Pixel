// Pixel Office — first-run setup guide (rehber.html): live pixel scenes built from sprites.js,
// scroll reveals, terminal + rename typing loops. Marks the guide as seen for main.js.
import {
  TILE, ANIMS, makeCharacterPalette, getCharacterFrame, getTileSprite, getFurnitureSprite,
  getBubbleSprite, getToolIcon,
} from './sprites.js';

const ROOM_RE = /^[a-z2-7]{26}$/;
export const GUIDE_SEEN_KEY = 'po-guide-seen';
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

try { localStorage.setItem(GUIDE_SEEN_KEY, '1'); } catch { /* private mode */ }

// "Ofise git" links go back to the room the visitor came from (key is already in localStorage).
const params = new URLSearchParams(location.search);
const room = params.get('room');
const back = room && ROOM_RE.test(room) ? `./?room=${room}` : './';
for (const a of document.querySelectorAll('[data-back]')) a.href = back;
if (params.get('ilk') === '1') document.getElementById('g-kicker').textContent = 'İlk kez mi buradasın? Önce kısa bir tur';

// --- scene helpers --------------------------------------------------------------------------------
const frameOf = (anim, t) => Math.floor(t * (ANIMS[anim].fps || 0)) % ANIMS[anim].frames;
const bob = (t, phase = 0) => Math.round(Math.sin(t * 3 + phase) * 1.2);

function floor(ctx, cols, rows, y0 = 0) {
  for (let y = y0; y < rows; y++) for (let x = 0; x < cols; x++) {
    ctx.drawImage(getTileSprite((x + y) % 2 ? 'floor_alt' : 'floor'), x * TILE, y * TILE);
  }
}
function wall(ctx, cols, windows = [], door = -1) {
  for (let x = 0; x < cols; x++) {
    ctx.drawImage(getTileSprite(x === door ? 'door' : windows.includes(x) ? 'window' : 'wall'), x * TILE, 0);
  }
}
/** Furniture of height H on tile (x,y), bottom-anchored like renderer.js (bed is top-anchored). */
function furn(ctx, name, x, y) {
  const img = getFurnitureSprite(name);
  ctx.drawImage(img, x * TILE, name === 'bed' ? y * TILE : y * TILE + TILE - img.height);
}
/** Character standing/sitting on tile (x,y): feet point (x*16+8, y*16+15). */
function char(ctx, pal, anim, dir, t, x, y) {
  ctx.drawImage(getCharacterFrame(pal, anim, dir, frameOf(anim, t)), Math.round(x), Math.round(y));
}
function bubble(ctx, img, cx, top, t, phase = 0) {
  ctx.drawImage(img, Math.round(cx - img.width / 2), Math.round(top - img.height + bob(t, phase)));
}

// Seated at desk tile (x, y): desk on row y, chair/seat on row y+1.
function deskWorker(ctx, pal, x, y, anim, t, bub) {
  furn(ctx, 'desk_on', x, y);
  const sx = x * TILE, sy = (y + 1) * TILE - 8;
  char(ctx, pal, anim, 'up', t, sx, sy);
  if (bub) bubble(ctx, bub, sx + 8, sy - 1, t, x);
}

const scenes = [];
function scene(id, cols, rows, draw) {
  const c = typeof id === 'string' ? document.getElementById(id) : id;
  if (!c) return;
  c.width = cols * TILE; c.height = rows * TILE;
  const ctx = c.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  scenes.push({ c, ctx, draw, visible: true });
}

const P = [11, 42, 7, 1337, 2024, 99, 314].map(makeCharacterPalette);

// Hero: a slice of the office — typing, coffee, sleeping, and someone strolling by.
scene('scene-hero', 15, 5, (ctx, t) => {
  wall(ctx, 15, [2, 7, 12]);
  floor(ctx, 15, 5, 1);
  furn(ctx, 'bookshelf', 0, 1);
  furn(ctx, 'plant', 5, 1);
  deskWorker(ctx, P[0], 1, 1, 'type', t, getToolIcon(['Bash', 'Edit', 'Read', 'WebFetch'][Math.floor(t / 3) % 4]));
  deskWorker(ctx, P[1], 3, 1, 'sit', t, getBubbleSprite('dots', Math.floor(t * 2)));
  furn(ctx, 'coffee_counter', 7, 1);
  char(ctx, P[2], 'drink', 'down', t, 8 * TILE, 2 * TILE - 8);
  bubble(ctx, getBubbleSprite('coffee', Math.floor(t * 2)), 8 * TILE + 8, 2 * TILE - 9, t, 2);
  furn(ctx, 'water_cooler', 10, 1);
  furn(ctx, 'bed', 12, 2); furn(ctx, 'bed', 13, 2);
  char(ctx, P[3], 'sleep', 'down', t, 12 * TILE, 2 * TILE + 2);
  bubble(ctx, getBubbleSprite('zzz', Math.floor(t)), 12 * TILE + 14, 2 * TILE + 2, t, 3);
  furn(ctx, 'plant', 14, 1);
  // Walker crosses the bottom row, then turns around.
  const span = 15 * TILE + 32, x = (t * 30) % (span * 2);
  const right = x < span;
  const px = right ? x - 16 : span * 2 - x - 16;
  char(ctx, P[4], 'walk', right ? 'right' : 'left', t, px, 4 * TILE - 8);
});

// Step 1: someone walks in through the door and waves (bubble).
scene('scene-door', 6, 4, (ctx, t) => {
  wall(ctx, 6, [4], 1);
  floor(ctx, 6, 4, 1);
  furn(ctx, 'plant', 0, 1);
  const T = 6, k = t % T;
  let x = TILE, y = -8, dir = 'down', anim = 'walk';
  if (k < 1.6) { y = -8 + k * 20; }                                 // out of the door
  else if (k < 3.6) { y = 24; x = TILE + (k - 1.6) * 24; dir = 'right'; }  // walk right
  else { y = 24; x = TILE + 48; dir = 'down'; anim = 'stand'; }
  char(ctx, P[5], anim, dir, t, x, y);
  if (k >= 3.6) bubble(ctx, getBubbleSprite('speech', Math.floor(t * 2)), x + 8, y - 1, t);
});

// Step 3: the tiny office inside the mini window.
scene('scene-mini', 4, 3, (ctx, t) => {
  wall(ctx, 4, [1]);
  floor(ctx, 4, 3, 1);
  deskWorker(ctx, P[0], 0, 1, 'type', t, null);
  char(ctx, P[2], 'drink', 'down', t, 3 * TILE - 4, TILE + 4);
});

// Step 5: one card per state.
const LEGEND = {
  working: [2, 3, (ctx, t) => { floor(ctx, 2, 3); deskWorker(ctx, P[0], 0, 1, 'type', t, getToolIcon(['Bash', 'Edit', 'Grep'][Math.floor(t / 2) % 3])); }],
  thinking: [2, 3, (ctx, t) => { floor(ctx, 2, 3); deskWorker(ctx, P[1], 0, 1, 'sit', t, getBubbleSprite('dots', Math.floor(t * 2))); }],
  waiting: [2, 3, (ctx, t) => { floor(ctx, 2, 3); deskWorker(ctx, P[3], 0, 1, 'sit', t, getBubbleSprite('alert', Math.floor(t * 2))); }],
  coffee: [3, 3, (ctx, t) => {
    floor(ctx, 3, 3); furn(ctx, 'coffee_counter', 0, 1);
    char(ctx, P[2], 'drink', 'down', t, TILE, 2 * TILE - 8);
    bubble(ctx, getBubbleSprite('coffee', Math.floor(t * 2)), TILE + 8, 2 * TILE - 9, t);
  }],
  sleep: [2, 3, (ctx, t) => {
    floor(ctx, 2, 3); furn(ctx, 'bed', 0, 1);
    char(ctx, P[4], 'sleep', 'down', t, 0, TILE + 2);
    bubble(ctx, getBubbleSprite('zzz', Math.floor(t)), 22, TILE + 4, t);
  }],
};
for (const c of document.querySelectorAll('[data-legend]')) {
  const [cols, rows, draw] = LEGEND[c.dataset.legend];
  scene(c, cols, rows, draw);
}

// Rename card avatar.
scene('scene-face', 1, 2, (ctx, t) => {
  ctx.clearRect(0, 0, 16, 32);
  char(ctx, P[6], 'stand', 'down', t, 0, 6 + (Math.floor(t * 2) % 2));
});

// CTA: a row of friends waving you in.
scene('scene-cta', 8, 2, (ctx, t) => {
  floor(ctx, 8, 2);
  for (let i = 0; i < 4; i++) {
    const x = 8 + i * 32, hop = Math.max(0, Math.sin(t * 4 + i * 0.9)) * 3;
    char(ctx, P[i + 1], 'stand', 'down', t, x, 8 - hop);
  }
  bubble(ctx, getBubbleSprite('speech', Math.floor(t * 2)), 8 + 3 * 32 + 8, 7, t);
});

// --- loop: only draw visible scenes ---------------------------------------------------------------
const io = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const s = scenes.find((x) => x.c === e.target);
    if (s) s.visible = e.isIntersecting;
  }
});
for (const s of scenes) io.observe(s.c);

function drawAll(t) {
  for (const s of scenes) {
    if (!s.visible) continue;
    s.ctx.clearRect(0, 0, s.c.width, s.c.height);
    s.draw(s.ctx, t);
  }
}
if (reduced) {
  for (const s of scenes) s.visible = true;
  drawAll(0.4);
} else {
  const t0 = performance.now();
  const tick = (now) => { drawAll((now - t0) / 1000); requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
}

// --- scroll reveal --------------------------------------------------------------------------------
const rv = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) { e.target.classList.add('in'); rv.unobserve(e.target); }
}, { threshold: 0.15 });
for (const el of document.querySelectorAll('.reveal')) rv.observe(el);

// --- terminal typing loop -------------------------------------------------------------------------
const term = document.getElementById('term');
const cmd = `curl -fsSL ${location.origin}/hook/install.sh | sh -s -- …`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function line(text, cls) {
  const s = document.createElement('span');
  if (cls) s.className = cls;
  s.textContent = text;
  term.append(s);
  return s;
}
async function termLoop() {
  for (;;) {
    term.textContent = '';
    line('# Ajan bağla → komutu kopyala, yapıştır:\n', 'dim');
    line('$ ');
    const typed = line('');
    const cur = line('█', 'blink');
    for (const ch of cmd) { typed.textContent += ch; await sleep(28); }
    await sleep(500);
    cur.remove();
    line('\n');
    for (const [txt, cls, ms] of [
      ['Pixel Office kancası kuruluyor…\n', 'dim', 700],
      ['✓ Claude Code ayarları yedeklendi\n', 'ok', 400],
      ['✓ Kanca eklendi\n', 'ok', 400],
      ['→ Claude Code\'u yeniden başlat. Ofiste görüşürüz! ☕\n', '', 3200],
    ]) { line(txt, cls); await sleep(ms); }
  }
}
if (reduced) {
  term.textContent = `$ ${cmd}\n✓ Kanca eklendi\n→ Claude Code'u yeniden başlat.`;
} else {
  termLoop();
}

// --- rename typing loop ---------------------------------------------------------------------------
const rn = document.getElementById('rename-text');
const NAMES = ['Kodcu Kedi', 'Bulut Usta', 'Gece Kuşu', 'Mini Mühendis'];
async function renameLoop() {
  for (let i = 0; ; i++) {
    await sleep(1600);
    while (rn.textContent.length) { rn.textContent = rn.textContent.slice(0, -1); await sleep(60); }
    for (const ch of NAMES[i % NAMES.length]) { rn.textContent += ch; await sleep(90); }
  }
}
if (!reduced) renameLoop();
