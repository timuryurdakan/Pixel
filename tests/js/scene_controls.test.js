// G1 (docs/EVRENSEL_PLATFORM_ARASTIRMA_RAPORU.md §1.2): scene zoom controls (buttons, Ctrl+wheel, two-finger pinch),
// atmosphere selector (auto/day/sunset/night + localStorage), clickable coffee machine & water cooler,
// and "Patron" hover tooltip/label. Runs in headless browser via tests/test_relay_js.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Office, TILE, CAT_TILE, BOSS_SEAT, BOSS_DESK } from '../../web/js/office.js';
import { Renderer, CAT_ID, COFFEE_ID, WATER_ID } from '../../web/js/renderer.js';
import { ParticleSystem, LightingEngine, ATMOSPHERE_MODES, ATMOSPHERE_STORAGE_KEY } from '../../web/js/fx.js';
import { SceneControls, ZOOM_MIN, ZOOM_MAX, ZOOM_STEP, ATMOSPHERE_LABELS } from '../../web/js/scene_controls.js';

function makeStage() {
  const stage = document.createElement('div');
  stage.id = 'stage';
  const frame = document.createElement('div');
  frame.className = 'frame';
  const canvas = document.createElement('canvas');
  frame.appendChild(canvas);
  stage.appendChild(frame);
  document.body.appendChild(stage);
  const office = new Office();
  const renderer = new Renderer(canvas, office);
  canvas.style.width = `${renderer.worldW}px`;
  canvas.style.height = `${renderer.worldH}px`;
  return {
    stage,
    frame,
    canvas,
    office,
    renderer,
    cleanup() {
      stage.remove();
      try { localStorage.removeItem(ATMOSPHERE_STORAGE_KEY); } catch {}
    },
  };
}

test('zoom controls: buttons zoom in, zoom out, and fit within [ZOOM_MIN, ZOOM_MAX]', () => {
  const env = makeStage();
  try {
    const { stage, renderer } = env;
    const bar = stage.querySelector('.scene-controls');
    assert.ok(bar, 'scene-controls toolbar mounted inside #stage');

    const btnIn = bar.querySelector('[data-scene-action="zoom-in"]');
    const btnOut = bar.querySelector('[data-scene-action="zoom-out"]');
    const btnFit = bar.querySelector('[data-scene-action="zoom-fit"]');
    assert.ok(btnIn && btnOut && btnFit);

    assert.equal(renderer.zoomLevel, 1.0);
    btnIn.click();
    assert.equal(renderer.zoomLevel, 1.0 + ZOOM_STEP);
    btnIn.click();
    assert.equal(renderer.zoomLevel, 1.0 + ZOOM_STEP * 2);

    btnOut.click();
    assert.equal(renderer.zoomLevel, 1.0 + ZOOM_STEP);

    btnFit.click();
    assert.equal(renderer.zoomLevel, 1.0);

    for (let i = 0; i < 20; i++) btnIn.click();
    assert.equal(renderer.zoomLevel, ZOOM_MAX);

    for (let i = 0; i < 30; i++) btnOut.click();
    assert.equal(renderer.zoomLevel, ZOOM_MIN);
  } finally {
    env.cleanup();
  }
});

test('zoom controls: Ctrl+wheel zooms in/out while plain wheel does not', () => {
  const env = makeStage();
  try {
    const { canvas, renderer } = env;
    assert.equal(renderer.zoomLevel, 1.0);

    // Plain wheel without Ctrl/Meta must not change zoom.
    const plain = new WheelEvent('wheel', { deltaY: -100, ctrlKey: false, metaKey: false, cancelable: true });
    canvas.dispatchEvent(plain);
    assert.equal(renderer.zoomLevel, 1.0);
    assert.equal(plain.defaultPrevented, false);

    // Ctrl + wheel up zooms in.
    const ctrlUp = new WheelEvent('wheel', { deltaY: -100, ctrlKey: true, cancelable: true });
    canvas.dispatchEvent(ctrlUp);
    assert.equal(renderer.zoomLevel, 1.0 + ZOOM_STEP);
    assert.equal(ctrlUp.defaultPrevented, true);

    // Ctrl + wheel down zooms out.
    const ctrlDown = new WheelEvent('wheel', { deltaY: 100, ctrlKey: true, cancelable: true });
    canvas.dispatchEvent(ctrlDown);
    assert.equal(renderer.zoomLevel, 1.0);
    assert.equal(ctrlDown.defaultPrevented, true);
  } finally {
    env.cleanup();
  }
});

test('zoom controls: two-finger pinch updates zoom proportionally and clamps to bounds', () => {
  const env = makeStage();
  try {
    const { canvas, renderer } = env;
    const controls = renderer.sceneControls;
    assert.ok(controls instanceof SceneControls);

    const dispatchTouch = (type, pts) => {
      const ev = new Event(type, { bubbles: true, cancelable: true });
      ev.touches = pts.map(([x, y]) => ({ clientX: x, clientY: y }));
      canvas.dispatchEvent(ev);
      return ev;
    };

    // Single finger touch does not zoom.
    dispatchTouch('touchstart', [[10, 10]]);
    dispatchTouch('touchmove', [[50, 50]]);
    assert.equal(renderer.zoomLevel, 1.0);

    // Two-finger spread from distance 100 to 150 -> 1.5x zoom.
    dispatchTouch('touchstart', [[0, 0], [100, 0]]);
    const moveEv = dispatchTouch('touchmove', [[0, 0], [150, 0]]);
    assert.equal(renderer.zoomLevel, 1.5);
    assert.equal(moveEv.defaultPrevented, true);

    // Spread huge distance -> clamped to ZOOM_MAX.
    dispatchTouch('touchmove', [[0, 0], [500, 0]]);
    assert.equal(renderer.zoomLevel, ZOOM_MAX);

    // Pinch close -> clamped to ZOOM_MIN.
    dispatchTouch('touchmove', [[0, 0], [10, 0]]);
    assert.equal(renderer.zoomLevel, ZOOM_MIN);

    dispatchTouch('touchend', []);
  } finally {
    env.cleanup();
  }
});

test('atmosphere selector: cycles auto -> day -> sunset -> night -> auto and persists in localStorage', () => {
  try { localStorage.removeItem(ATMOSPHERE_STORAGE_KEY); } catch {}
  const env = makeStage();
  try {
    const { stage, renderer } = env;
    const btnAtm = stage.querySelector('[data-scene-action="atmosphere"]');
    assert.ok(btnAtm);
    assert.deepEqual(ATMOSPHERE_MODES, ['auto', 'day', 'sunset', 'night']);
    assert.equal(renderer.lighting.mode, 'auto');
    assert.match(btnAtm.textContent, new RegExp(ATMOSPHERE_LABELS.auto));

    btnAtm.click();
    assert.equal(renderer.lighting.mode, 'day');
    assert.equal(localStorage.getItem(ATMOSPHERE_STORAGE_KEY), 'day');
    assert.match(btnAtm.textContent, new RegExp(ATMOSPHERE_LABELS.day));

    btnAtm.click();
    assert.equal(renderer.lighting.mode, 'sunset');
    assert.equal(localStorage.getItem(ATMOSPHERE_STORAGE_KEY), 'sunset');
    assert.match(btnAtm.textContent, new RegExp(ATMOSPHERE_LABELS.sunset));

    btnAtm.click();
    assert.equal(renderer.lighting.mode, 'night');
    assert.equal(localStorage.getItem(ATMOSPHERE_STORAGE_KEY), 'night');
    assert.match(btnAtm.textContent, new RegExp(ATMOSPHERE_LABELS.night));

    btnAtm.click();
    assert.equal(renderer.lighting.mode, 'auto');
    assert.equal(localStorage.getItem(ATMOSPHERE_STORAGE_KEY), 'auto');

    // Invalid mode falls back safely to 'auto'.
    const light2 = new LightingEngine();
    light2.setMode('invalid-mode');
    assert.equal(light2.mode, 'auto');
  } finally {
    env.cleanup();
  }
});

test('clickable coffee machine and water cooler: hit-testing and reactions without breaking cat or agents', () => {
  const env = makeStage();
  try {
    const { canvas, office, renderer } = env;
    renderer.particles = new ParticleSystem({ isReduced: () => false });
    renderer.reactions.isReduced = () => false;

    const coffee = office.furniture.find((f) => f.kind === 'coffee_counter');
    const water = office.furniture.find((f) => f.kind === 'water_cooler');
    assert.ok(coffee && water);

    const coffeeX = coffee.x * TILE + 8;
    const coffeeY = coffee.y * TILE + 4;
    const waterX = water.x * TILE + 8;
    const waterY = water.y * TILE + 4;

    assert.equal(renderer.pickProp(coffeeX, coffeeY), 'coffee_counter');
    assert.equal(renderer.pickProp(waterX, waterY), 'water_cooler');
    assert.equal(renderer.pickProp(2, 2), null);

    // Existing cat is untouched and still pickable at CAT_TILE.
    assert.equal(renderer.pickCharacter([], CAT_TILE.x * TILE + 8, CAT_TILE.y * TILE + 8), CAT_ID);
    // pickCharacter never returns furniture props so main.js agent selection is unaffected.
    assert.equal(renderer.pickCharacter([], coffeeX, coffeeY), null);
    assert.equal(renderer.pickCharacter([], waterX, waterY), null);

    // Clicking the coffee machine triggers coffee reaction and steam particles.
    const rect = canvas.getBoundingClientRect();
    canvas.dispatchEvent(new MouseEvent('click', {
      bubbles: true,
      clientX: rect.left + coffeeX,
      clientY: rect.top + coffeeY,
    }));
    assert.equal(renderer.reactions.getEmote(COFFEE_ID), 'coffee');
    assert.ok(renderer.particles.activeCount > 0, 'steam particles spawned');

    // Clicking the water cooler triggers happy reaction and water particles.
    renderer.particles.update(10); // clear active particles
    canvas.dispatchEvent(new MouseEvent('click', {
      bubbles: true,
      clientX: rect.left + waterX,
      clientY: rect.top + waterY,
    }));
    assert.equal(renderer.reactions.getEmote(WATER_ID), 'happy');
    assert.ok(renderer.particles.activeCount > 0, 'water splash particles spawned');
  } finally {
    env.cleanup();
  }
});

test('Boss hover: hovering the Boss sets "Patron" tooltip and draws "Patron" label only while hovered', () => {
  const env = makeStage();
  try {
    const { canvas, renderer } = env;
    const bossX = BOSS_SEAT.x * TILE + 8;
    const bossY = BOSS_SEAT.y * TILE + 8;
    const rect = canvas.getBoundingClientRect();

    // Track canvas fillText calls during render.
    const drawnTexts = [];
    const origFillText = renderer.ctx.fillText.bind(renderer.ctx);
    renderer.ctx.fillText = (text, x, y) => {
      drawnTexts.push(String(text));
      origFillText(text, x, y);
    };

    // Not hovered initially: "Patron" is never drawn.
    renderer.render([], { time: 1 });
    assert.equal(renderer.hoverBoss, false);
    assert.equal( drawnTexts.includes('Patron'), false );

    // Hover over the Boss.
    canvas.dispatchEvent(new MouseEvent('mousemove', {
      bubbles: true,
      clientX: rect.left + bossX,
      clientY: rect.top + bossY,
    }));
    assert.equal(renderer.hoverBoss, true);
    assert.equal(canvas.title, 'Patron');

    drawnTexts.length = 0;
    renderer.render([], { time: 1.1 });
    assert.equal(drawnTexts.includes('Patron'), true);

    // Move away from the Boss: tooltip and canvas label disappear.
    canvas.dispatchEvent(new MouseEvent('mousemove', {
      bubbles: true,
      clientX: rect.left + 2,
      clientY: rect.top + 2,
    }));
    assert.equal(renderer.hoverBoss, false);
    assert.equal(canvas.title, '');

    drawnTexts.length = 0;
    renderer.render([], { time: 1.2 });
    assert.equal(drawnTexts.includes('Patron'), false);
  } finally {
    env.cleanup();
  }
});
