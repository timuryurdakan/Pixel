// Pixel Office — Tanıtım (Landing) Sayfası (tanitim.html)
// Pure ES module; zero external CDN, no remote fonts or images.
import {
  TILE, ANIMS, makeCharacterPalette, getCharacterFrame, getTileSprite, getFurnitureSprite,
  getBubbleSprite, getToolIcon,
} from './sprites.js';

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

// --- Helper Functions for Canvas Scenes ---
const frameOf = (anim, t) => Math.floor(t * (ANIMS[anim].fps || 0)) % ANIMS[anim].frames;
const bob = (t, phase = 0) => Math.round(Math.sin(t * 3 + phase) * 1.2);

function drawFloor(ctx, cols, rows, y0 = 0) {
  for (let y = y0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      ctx.drawImage(getTileSprite((x + y) % 2 ? 'floor_alt' : 'floor'), x * TILE, y * TILE);
    }
  }
}

function drawWall(ctx, cols, windows = [], door = -1) {
  for (let x = 0; x < cols; x++) {
    const tileType = x === door ? 'door' : windows.includes(x) ? 'window' : 'wall';
    ctx.drawImage(getTileSprite(tileType), x * TILE, 0);
  }
}

function drawFurn(ctx, name, x, y) {
  const img = getFurnitureSprite(name);
  ctx.drawImage(img, x * TILE, name === 'bed' ? y * TILE : y * TILE + TILE - img.height);
}

function drawChar(ctx, pal, anim, dir, t, x, y) {
  ctx.drawImage(getCharacterFrame(pal, anim, dir, frameOf(anim, t)), Math.round(x), Math.round(y));
}

function drawBubble(ctx, img, cx, top, t, phase = 0) {
  ctx.drawImage(img, Math.round(cx - img.width / 2), Math.round(top - img.height + bob(t, phase)));
}

function drawDeskWorker(ctx, pal, x, y, anim, t, bub) {
  drawFurn(ctx, 'desk_on', x, y);
  const sx = x * TILE;
  const sy = (y + 1) * TILE - 8;
  drawChar(ctx, pal, anim, 'up', t, sx, sy);
  if (bub) {
    drawBubble(ctx, bub, sx + 8, sy - 1, t, x);
  }
}

// Scene Registry
const scenes = [];
function registerScene(canvasId, cols, rows, drawFn) {
  const c = typeof canvasId === 'string' ? document.getElementById(canvasId) : canvasId;
  if (!c) return;
  c.width = cols * TILE;
  c.height = rows * TILE;
  const ctx = c.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  scenes.push({ c, ctx, draw: drawFn, visible: true });
}

// Character Palettes
const P = [101, 204, 307, 412, 518, 629, 735].map(makeCharacterPalette);

// 1. Hero Office Scene (16x5 tiles)
registerScene('hero-canvas', 16, 5, (ctx, t) => {
  drawWall(ctx, 16, [2, 8, 13]);
  drawFloor(ctx, 16, 5, 1);
  drawFurn(ctx, 'bookshelf', 0, 1);
  drawFurn(ctx, 'plant', 5, 1);

  // Worker 1: Coding with tool icons changing
  const toolIcons = ['Bash', 'Edit', 'Read', 'WebFetch'];
  const currentTool = toolIcons[Math.floor(t / 2.5) % toolIcons.length];
  drawDeskWorker(ctx, P[0], 1, 1, 'type', t, getToolIcon(currentTool));

  // Worker 2: Thinking
  drawDeskWorker(ctx, P[1], 3, 1, 'sit', t, getBubbleSprite('dots', Math.floor(t * 2)));

  // Coffee spot
  drawFurn(ctx, 'coffee_counter', 7, 1);
  drawChar(ctx, P[2], 'drink', 'down', t, 8 * TILE, 2 * TILE - 8);
  drawBubble(ctx, getBubbleSprite('coffee', Math.floor(t * 2)), 8 * TILE + 8, 2 * TILE - 9, t, 1);

  // Water cooler
  drawFurn(ctx, 'water_cooler', 10, 1);

  // Beds area
  drawFurn(ctx, 'bed', 12, 2);
  drawFurn(ctx, 'bed', 13, 2);
  drawChar(ctx, P[3], 'sleep', 'down', t, 12 * TILE, 2 * TILE + 2);
  drawBubble(ctx, getBubbleSprite('zzz', Math.floor(t)), 12 * TILE + 14, 2 * TILE + 2, t, 2);

  drawFurn(ctx, 'plant', 15, 1);

  // Strolling agent
  const span = 16 * TILE + 32;
  const walkPos = (t * 28) % (span * 2);
  const isRight = walkPos < span;
  const px = isRight ? walkPos - 16 : span * 2 - walkPos - 16;
  drawChar(ctx, P[4], 'walk', isRight ? 'right' : 'left', t, px, 4 * TILE - 8);
});

// 2. Interactive State Demo Canvas (3x3 tiles)
let activeDemoState = 'working';
const demoDescriptions = {
  working: {
    title: 'Çalışıyor (Kodlama / Araç Kullanımı)',
    text: 'Ajan masasında harıl harıl klavyede yazar. Başının üstündeki baloncukta o an hangi aracı (Edit, Bash, Read) çalıştırdığı canlı görünür.',
  },
  thinking: {
    title: 'Düşünüyor (Model Akıl Yürütme)',
    text: 'Model yanıt üretirken ya da sonraki hamlesini planlarken masasında düşünce balonu ("...") ile odaklanır.',
  },
  waiting: {
    title: 'Kullanıcıyı Bekliyor (Onay Gerekiyor)',
    text: 'Ajan kritik bir işlem için senden izin veya yanıt istediğinde başının üzerinde kırmızı ünlem ("!") yanıp söner.',
  },
  coffee: {
    title: 'Kahve Molası (Boşta / Tur Bitti)',
    text: 'Verilen görevi tamamlayan veya boşta kalan ajan kahve makinesine yürür, sıradaki komutu beklerken kahvesini yudumlar.',
  },
  sleep: {
    title: 'Uyuyor (İş Bitti / Sessizlik)',
    text: 'Oturumu kapanan ya da uzun süre yeni komut almayan ajan dinlenme odasındaki yataklara geçer ve Zzz balonlarıyla uyur.',
  },
};

registerScene('demo-canvas', 3, 3, (ctx, t) => {
  drawFloor(ctx, 3, 3);
  if (activeDemoState === 'working') {
    const icon = getToolIcon(['Edit', 'Bash', 'Read'][Math.floor(t / 2) % 3]);
    drawDeskWorker(ctx, P[5], 1, 1, 'type', t, icon);
  } else if (activeDemoState === 'thinking') {
    drawDeskWorker(ctx, P[5], 1, 1, 'sit', t, getBubbleSprite('dots', Math.floor(t * 2)));
  } else if (activeDemoState === 'waiting') {
    drawDeskWorker(ctx, P[5], 1, 1, 'sit', t, getBubbleSprite('alert', Math.floor(t * 2)));
  } else if (activeDemoState === 'coffee') {
    drawFurn(ctx, 'coffee_counter', 0, 1);
    drawChar(ctx, P[5], 'drink', 'down', t, TILE, 2 * TILE - 8);
    drawBubble(ctx, getBubbleSprite('coffee', Math.floor(t * 2)), TILE + 8, 2 * TILE - 9, t);
  } else if (activeDemoState === 'sleep') {
    drawFurn(ctx, 'bed', 0, 1);
    drawChar(ctx, P[5], 'sleep', 'down', t, 0, TILE + 2);
    drawBubble(ctx, getBubbleSprite('zzz', Math.floor(t)), 22, TILE + 4, t);
  }
});

// Setup Demo Button Handlers
const demoBtns = document.querySelectorAll('.demo-btn');
const demoTitleEl = document.getElementById('demo-title');
const demoTextEl = document.getElementById('demo-desc-text');

demoBtns.forEach((btn) => {
  btn.addEventListener('click', () => {
    demoBtns.forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    const state = btn.dataset.state;
    if (state && demoDescriptions[state]) {
      activeDemoState = state;
      if (demoTitleEl) demoTitleEl.textContent = demoDescriptions[state].title;
      if (demoTextEl) demoTextEl.textContent = demoDescriptions[state].text;
    }
  });
});

// 3. Bottom CTA Canvas (8x2 tiles)
registerScene('cta-canvas', 8, 2, (ctx, t) => {
  drawFloor(ctx, 8, 2);
  for (let i = 0; i < 4; i++) {
    const x = 8 + i * 32;
    const hop = Math.max(0, Math.sin(t * 4 + i * 0.9)) * 3;
    drawChar(ctx, P[i + 1], 'stand', 'down', t, x, 8 - hop);
  }
  drawBubble(ctx, getBubbleSprite('speech', Math.floor(t * 2)), 8 + 3 * 32 + 8, 7, t);
});

// --- Visibility-Based Animation Loop ---
const observer = new IntersectionObserver((entries) => {
  entries.forEach((e) => {
    const found = scenes.find((s) => s.c === e.target);
    if (found) found.visible = e.isIntersecting;
  });
});
scenes.forEach((s) => observer.observe(s.c));

function renderAll(t) {
  scenes.forEach((s) => {
    if (!s.visible) return;
    s.ctx.clearRect(0, 0, s.c.width, s.c.height);
    s.draw(s.ctx, t);
  });
}

if (reduced) {
  scenes.forEach((s) => { s.visible = true; });
  renderAll(0.5);
} else {
  const startTime = performance.now();
  const loop = (now) => {
    renderAll((now - startTime) / 1000);
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

// --- Scroll Reveal Animations ---
const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach((e) => {
    if (e.isIntersecting) {
      e.target.classList.add('in');
      revealObserver.unobserve(e.target);
    }
  });
}, { threshold: 0.15 });

document.querySelectorAll('.reveal').forEach((el) => revealObserver.observe(el));
