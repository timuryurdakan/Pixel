// Pixel Office — bootstrap, transport, fixed-timestep loop, input (ARCHITECTURE.md §6.6, CLOUD.md §9).
// Local mode: SseTransport('/events'), v1 behaviour. Cloud mode: landing or PollingTransport per room.
// The loop and the polling timers run on the PiP window while the mini window is open (§9.2, §11).

import { Office } from './office.js';
import { CharacterManager } from './agents.js';
import { Renderer } from './renderer.js';
import { UI, summaryText } from './ui.js';
import { SseTransport, PollingTransport } from './transport.js';
import { MiniWindow } from './pip.js';
import * as cloud from './cloud.js';

export const STEP = 1 / 60;   // fixed timestep (s)
export const MAX_STEPS = 5;   // per animation frame (excess is dropped)
export const HIDDEN_GRACE_MS = 30000;   // §9.2: pause polling 30 s after the page became hidden (no PiP)
export const OVERRIDE_MS = 60000;       // §15.1: optimistic rename kept at most 60 s

const params = new URLSearchParams(location.search);
const debug = params.has('debug');
const viewMini = params.get('view') === 'mini';
const errors = [];

let office, manager, renderer, ui, canvas;
let selectedId = null, hoverId = null;
let gameTime = 0;
let firstSnapshot = true;
let transport = null;
let backend = { kind: 'local' };
let room = null;            // cloud: {id, key, ui}
let mini = null;            // MiniWindow
let rafHost = window;
let loopToken = 0;
/** @type {Map<string, object>} latest server JSON per agent id */
const agents = new Map();
/** Optimistic renames: id → {name, nameSource, defaultName, until} (CLOUD.md §15.1). */
const overrides = new Map();
/** v3 device control (V3.md §11): set up only for a cloud room with the write key and a configured relay. */
let v3 = null;              // {hub, panel, devices, wizard, newAgent, pendingSelect, setTab}

cloud.captureInstallPrompt();

function sortedAgents() {
  return [...agents.values()].sort((a, b) => (a.startedAt || 0) - (b.startedAt || 0) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

function refreshUI() {
  const list = sortedAgents();
  if (v3) v3.hub.updateRoom(list);
  ui.setAgents(list);
  if (selectedId && !agents.has(selectedId)) select(null);
  const strip = document.getElementById('mini-strip');
  if (strip) strip.textContent = summaryText(list);
  if (v3) v3Sync();
}

function select(id) {
  selectedId = id || null;
  ui.setSelected(selectedId);
  if (v3) v3Sync();
}

/** v3: details-panel control section, phone sheet and toolbar state follow the selection and the hub. */
function v3Sync() {
  const a = selectedId ? agents.get(selectedId) : null;
  if (v3.pendingSelect) {
    const p = v3.pendingSelect;
    const hit = [...agents.values()].find((x) => x.control && x.control.device === p.device && x.control.agent === p.agent);
    if (hit) { v3.pendingSelect = null; select(hit.id); return; }
  }
  v3.panel.show(a || null);
  const panel = document.getElementById('panel');
  panel.classList.toggle('has-ctl', !!(a && a.control));
  document.body.classList.toggle('has-sel', !!a);
  document.getElementById('sheet-head').hidden = !a;
  document.getElementById('sheet-name').textContent = a ? a.name : '';
  const anyReady = [...v3.hub.devices.values()].some((d) => d.ready);
  document.getElementById('btn-newagent').hidden = !anyReady;
}

/** Apply a pending optimistic rename; dropped once the server shows the name, or after 60 s. */
function withOverride(a) {
  const o = overrides.get(a.id);
  if (!o) return a;
  if (a.name === o.name || Date.now() > o.until) { overrides.delete(a.id); return a; }
  return { ...a, name: o.name, nameSource: o.nameSource, defaultName: o.defaultName ?? a.defaultName };
}

// --- transport handlers (same bodies as the v1 SSE client) ------------------------------------
const handlers = {
  onStatus: (st) => ui.setConnection(st),
  onSnapshot: (d) => {
    ui.setServerTime(d.serverTime);
    ui.setMode(d.mode);
    if (room && d.room && typeof d.room.name === 'string') room.ui.setRoomName(d.room.name);
    agents.clear();
    const list = (d.agents || []).map(withOverride);
    for (const a of list) agents.set(a.id, a);
    manager.sync(list, { initial: firstSnapshot });
    firstSnapshot = false;
    refreshUI();
  },
  onUpsert: (raw) => {
    const a = withOverride(raw);
    agents.set(a.id, a);
    manager.upsert(a);
    refreshUI();
  },
  onRemove: (id) => {
    agents.delete(id);
    manager.remove(id);
    refreshUI();
  },
  onError: (code) => {
    if (code === 'not_found') {
      ui.setConnection('notfound');
      ui.toast('Oda bulunamadı', { error: true, ms: 8000 });
    }
  },
};

/** Kept for compatibility with v1 callers (window.pixelOffice / tests): (re)start the transport. */
export function connect() {
  if (transport) transport.stop();
  transport.start(handlers);
}

// --- rename (CLOUD.md §15.1, §15.3, §15.4) ----------------------------------------------------------
async function postRename(body) {
  const cloudMode = backend.kind === 'cloud';
  const url = cloudMode ? `/api/rename?room=${encodeURIComponent(room.id)}` : '/api/agents/rename';
  const headers = { 'Content-Type': 'application/json' };
  if (cloudMode) headers.Authorization = `Bearer ${room.key}`;
  const r = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body), cache: 'no-store' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json().catch(() => ({}));
}

/**
 * ✎ save / "Varsayılana dön". name null = reset of the winning rename layer.
 * future = also the owner's (cloud) / project's (local) next sessions.
 */
async function onRename(agent, { name, future }) {
  const cloudMode = backend.kind === 'cloud';
  const futureScope = cloudMode ? 'owner' : 'project';
  const ops = [];
  const optimistic = [];   // [agentId, {name, nameSource, defaultName}]
  const sameGroup = (x) => !x.isSubagent && (cloudMode ? x.ownerId === agent.ownerId : (!!x.projectPath && x.projectPath === agent.projectPath));
  const op = (scope, value) => (cloudMode
    ? (scope === 'agent' ? { scope, agentId: agent.id, name: value } : { scope, ownerId: agent.ownerId, name: value })
    : { id: agent.id, scope, name: value });

  if (name === null) {
    if (agent.nameSource === 'custom') {
      ops.push(op('agent', null));
      optimistic.push([agent.id, { name: agent.defaultName || agent.name, nameSource: 'auto', defaultName: agent.defaultName }]);
    } else if (agent.nameSource === 'owner' || agent.nameSource === 'project') {
      ops.push(op(futureScope, null));
      for (const x of agents.values()) {
        if (sameGroup(x) && x.nameSource === agent.nameSource) optimistic.push([x.id, { name: x.defaultName || x.name, nameSource: 'auto', defaultName: x.defaultName }]);
      }
    }
  } else if (future) {
    if (agent.nameSource === 'custom') ops.push(op('agent', null));   // let the default show on this agent too
    ops.push(op(futureScope, name));
    optimistic.push([agent.id, { name, nameSource: futureScope, defaultName: agent.defaultName }]);
    for (const x of agents.values()) {
      if (x.id !== agent.id && sameGroup(x) && x.nameSource !== 'custom') optimistic.push([x.id, { name, nameSource: futureScope, defaultName: x.defaultName }]);
    }
  } else {
    ops.push(op('agent', name));
    optimistic.push([agent.id, { name, nameSource: 'custom', defaultName: agent.nameSource === 'custom' ? agent.defaultName : agent.name }]);
  }
  if (!ops.length) return false;

  const until = Date.now() + OVERRIDE_MS;
  for (const [id, o] of optimistic) {
    overrides.set(id, { ...o, until });
    const cur = agents.get(id);
    if (cur) { const a = { ...cur, name: o.name, nameSource: o.nameSource, defaultName: o.defaultName ?? cur.defaultName }; agents.set(id, a); manager.upsert(a); }
  }
  refreshUI();

  try {
    let last = null;
    for (const body of ops) last = await postRename(body);
    if (cloudMode) transport.pollNow({ bust: true });   // one cache-busting poll (§15.3)
    else if (last && last.agent && last.agent.id) handlers.onUpsert(last.agent);   // SSE agent_updated follows anyway
    return true;
  } catch (err) {
    for (const [id] of optimistic) overrides.delete(id);
    ui.toast('Ad kaydedilemedi', { error: true });
    if (cloudMode) transport.pollNow({ bust: true });
    else {
      // Revert to the last server truth (an SSE event may not come for unchanged agents).
      try {
        const r = await fetch('/api/agents', { cache: 'no-store' });
        if (r.ok) handlers.onSnapshot(await r.json());
      } catch { /* the next SSE event repairs it */ }
    }
    return false;
  }
}

// --- layout --------------------------------------------------------------------------------------
function fitCanvas() {
  const FRAME = 24; // wooden frame padding + border + shadow around the canvas
  const pip = mini && mini.window;
  if (pip) {
    const strip = pip.document.getElementById('mini-strip');
    const sh = strip ? strip.offsetHeight : 0;
    renderer.fit(Math.max(100, pip.innerWidth - 12), Math.max(80, pip.innerHeight - sh - 12));
    return;
  }
  const stage = document.getElementById('stage');
  const cs = getComputedStyle(stage);
  const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
  const availW = stage.clientWidth - padX;
  if (viewMini) {
    renderer.fit(Math.max(100, availW - 12), Math.max(80, stage.clientHeight - padY - 12));
    return;
  }
  const stacked = window.matchMedia('(max-width: 1100px)').matches;
  const header = document.getElementById('topbar');
  const availH = stacked
    ? Math.max(200, window.innerHeight - (header ? header.offsetHeight : 0) - 24)
    : stage.clientHeight - padY;
  renderer.fit(Math.max(100, availW - FRAME), Math.max(100, availH - FRAME));
}

function canvasPoint(e) {
  const r = canvas.getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}

// --- loop (hosted by the PiP window while it is open) ---------------------------------------------
let last = 0, acc = 0;
let fpsFrames = 0, fpsTime = 0, fps = 0;
function scheduleFrame() {
  const token = loopToken;
  rafHost.requestAnimationFrame((ts) => { if (token === loopToken) loop(ts); });
}

function setRafHost(win) {
  rafHost = win || window;
  loopToken++;
  last = 0;   // the new host has its own time origin
  scheduleFrame();
}

function loop(ts) {
  const now = ts / 1000;
  if (!last) last = now;
  acc += Math.min(Math.max(0, now - last), 0.25);
  fpsFrames++; fpsTime += now - last;
  if (fpsTime >= 1) { fps = fpsFrames / fpsTime; fpsFrames = 0; fpsTime = 0; }
  last = now;
  let steps = 0;
  while (acc >= STEP && steps < MAX_STEPS) {
    gameTime += STEP;
    manager.update(STEP, gameTime);
    acc -= STEP;
    steps++;
  }
  if (steps === MAX_STEPS) acc = Math.min(acc, STEP);
  try {
    renderer.render(manager.list(), { selectedId, hoverId, time: gameTime });
  } catch (err) {
    reportError(err);
  }
  ui.tick(Date.now());
  if (debug) drawDebug();
  scheduleFrame();
}

// --- debug overlay (?debug) ------------------------------------------------
let debugEl = null;
function reportError(err) {
  const msg = String(err && err.stack ? err.stack : err);
  if (errors.length < 50) errors.push(msg);
  console.error('[pixel-office]', msg);
}
window.addEventListener('error', (e) => reportError(e.error || e.message));
window.addEventListener('unhandledrejection', (e) => reportError(e.reason));

let lastDebug = 0;
function drawDebug() {
  const t = performance.now();
  if (t - lastDebug < 250) return;
  lastDebug = t;
  if (!debugEl) {
    debugEl = document.createElement('pre');
    debugEl.id = 'debug';
    document.body.appendChild(debugEl);
  }
  const lines = [`fps ${fps.toFixed(0)}  t=${gameTime.toFixed(1)}s  errors=${errors.length}`];
  for (const c of manager.list()) {
    const sp = c.spot ? `${c.spot.kind}${c.spot.overflow ? '*' : ''}#${c.spot.index}@${c.spot.x},${c.spot.y}` : '-';
    lines.push(`${c.agent.name.padEnd(9)} ${c.agent.state.padEnd(16)} ${c.phase.padEnd(8)} ${c.anim.padEnd(5)} ${sp}`);
  }
  for (const e of errors) lines.push('ERR ' + e.split('\n')[0]);
  debugEl.textContent = lines.join('\n');
}

// --- visibility: pause cloud polling when nothing is visible (§9.2) ----------------------------------
let hiddenTimer = 0;
function updateActive() {
  if (!(transport instanceof PollingTransport)) return;
  clearTimeout(hiddenTimer);
  const visible = document.visibilityState === 'visible' || !!(mini && mini.window);
  if (visible) { transport.setActive(true); return; }
  hiddenTimer = setTimeout(() => {
    if (document.visibilityState !== 'visible' && !(mini && mini.window)) transport.setActive(false);
  }, HIDDEN_GRACE_MS);
}

// --- mini window (§11) ------------------------------------------------------------------------------
let pipResize = null;
function setupMiniButton() {
  const btn = document.getElementById('btn-mini');
  const pipOk = MiniWindow.supported();
  const popupOk = MiniWindow.popupSupported() && backend.kind === 'cloud';   // popup needs ?room= (cloud)
  if (viewMini || (!pipOk && !popupOk)) return;
  btn.hidden = false;
  document.getElementById('toolbar').hidden = false;
  if (!pipOk) btn.title = 'Sağ altta küçük bir pencere açar. Bu tarayıcıda her zaman üstte kalmaz.';
  mini = new MiniWindow({
    frameEl: document.querySelector('#stage .frame'),
    stripEl: document.getElementById('mini-strip'),
    onOpen: (w) => {
      setRafHost(w);
      if (transport) transport.setScheduler(w);
      pipResize = new w.ResizeObserver(() => fitCanvas());
      pipResize.observe(w.document.body);
      w.addEventListener('keydown', (e) => { if (e.key === 'Escape') select(null); });
      btn.textContent = 'Mini pencereyi kapat';
      document.body.classList.add('pip-open');
      fitCanvas();
      updateActive();
    },
    onClose: () => {
      if (pipResize) { try { pipResize.disconnect(); } catch { /* window gone */ } pipResize = null; }
      setRafHost(window);
      if (transport) transport.setScheduler(window);
      btn.textContent = 'Mini pencere';
      document.body.classList.remove('pip-open');
      renderer._fitKey = null;
      fitCanvas();
      updateActive();
    },
  });
  btn.addEventListener('click', async () => {
    if (mini.window) { mini.close(); return; }
    if (pipOk) {
      try {
        await mini.open();
        ui.toast('Mini pencereyi ekranın sağ altına sürükleyin. Tarayıcı konumu genelde hatırlar.', { ms: 6000 });
        return;
      } catch (err) {
        console.warn('[pixel-office] Document PiP failed, using a popup', err);
        if (!popupOk) { ui.toast('Mini pencere açılamadı', { error: true }); return; }
      }
    }
    const w = MiniWindow.openPopup(`/?room=${encodeURIComponent(room.id)}&view=mini`);
    if (!w) ui.toast('Açılır pencere engellendi. Tarayıcıda açılır pencerelere izin verin.', { error: true, ms: 6000 });
    else ui.toast('Mini pencere sağ altta açıldı. Bu tarayıcıda her zaman üstte kalmaz.', { ms: 6000 });
  });
}

// --- boot --------------------------------------------------------------------
function bootCommon() {
  canvas = document.getElementById('office');
  office = new Office();
  manager = new CharacterManager(office);
  renderer = new Renderer(canvas, office);
  ui = new UI({
    list: document.getElementById('agent-list'),
    details: document.getElementById('agent-details'),
    status: document.getElementById('conn-status'),
    mode: document.getElementById('mode-badge'),
    banner: document.getElementById('conn-banner'),
    summary: document.getElementById('summary'),
  }, {
    onSelect: (id) => select(id),
    colorOf: (a) => manager.characters.get(a.id)?.palette.shirt || null,
    onRename,
  });

  canvas.addEventListener('click', (e) => {
    const [x, y] = canvasPoint(e);
    select(renderer.pickCharacter(manager.list(), x, y));
  });
  canvas.addEventListener('mousemove', (e) => {
    const [x, y] = canvasPoint(e);
    hoverId = renderer.pickCharacter(manager.list(), x, y);
    canvas.style.cursor = hoverId ? 'pointer' : 'default';
  });
  canvas.addEventListener('mouseleave', () => { hoverId = null; });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') select(null); });

  if (viewMini) document.body.classList.add('mini');
  fitCanvas();
  if (typeof ResizeObserver === 'function') new ResizeObserver(() => { if (!(mini && mini.window)) fitCanvas(); }).observe(document.getElementById('stage'));
  window.addEventListener('resize', () => { if (!(mini && mini.window)) fitCanvas(); });

  window.pixelOffice = { office, manager, renderer, ui, agents, errors, overrides,
    get transport() { return transport; }, get backend() { return backend; }, get room() { return room; },
    get mini() { return mini; } };
  scheduleFrame();
}

async function bootLocal() {
  transport = new SseTransport('/events');
  transport.start(handlers);
  setupMiniButton();
  // ✎ only when the local server advertises the rename feature (§15.4): from /api/config (v2 local
  // server, see CLOUD.md §17.9), else from /api/health.
  try {
    let h = backend.config;
    if (!h || !Array.isArray(h.features)) {
      const r = await fetch('/api/health', { cache: 'no-store' });
      h = r.ok ? await r.json() : null;
    }
    if (h && Array.isArray(h.features) && h.features.includes('rename')) {
      ui.canRename = () => true;
      ui.futureLabel = (a) => (!a.isSubagent && a.projectPath ? 'Bu projedeki yeni oturumlara da bu adı ver' : null);
      ui.setSelected(selectedId);
    }
  } catch { /* v1 server: no rename */ }
}

const BOOST_MS = 3 * 60 * 1000;

// First visit → the setup guide (rehber.html). guide.js sets the flag when it is shown.
const GUIDE_SEEN_KEY = 'po-guide-seen';
function guideSeen() { try { return !!localStorage.getItem(GUIDE_SEEN_KEY); } catch { return true; } }
function markGuideSeen() { try { localStorage.setItem(GUIDE_SEEN_KEY, '1'); } catch { /* ignore */ } }

function bootCloud(config) {
  const roomParam = params.get('room');
  if (!roomParam) {
    // PWA launch (start_url /?source=pwa): reopen the last room directly.
    const lastRoom = params.get('source') === 'pwa' ? cloud.lastRoomId() : null;
    if (lastRoom) { location.replace(`/?room=${lastRoom}`); return; }
    if (!guideSeen()) { location.replace('/rehber.html?ilk=1'); return; }
    ui.setMode('cloud');
    ui.setConnection('live');
    cloud.showLanding({ config });
    return;
  }
  if (!cloud.ROOM_RE.test(roomParam)) {
    ui.setMode('cloud');
    cloud.showLanding({ config, error: 'Bağlantıdaki oda kimliği geçersiz.' });
    return;
  }
  const key = cloud.takeKeyFromHash(roomParam);   // also strips #key= from the address bar
  cloud.rememberRoom(roomParam, {});
  if (params.get('new') === '1') markGuideSeen();      // the room creator has already been through the landing
  else if (!viewMini && !guideSeen()) { location.replace(`/rehber.html?room=${roomParam}&ilk=1`); return; }
  ui.setMode('cloud');
  // Fast first appearance (CLOUD.md §17.10): poll every 5 s while the "Ajan bağla" dialog is open and for
  // 3 min after it closes, and for the first 3 min after opening a room while it is still empty.
  const onSetupDialog = (open) => {
    if (transport && transport.boost) transport.boost(open ? { hold: true } : { hold: false, ms: BOOST_MS });
  };
  const roomUi = cloud.setupRoomUI({ roomId: roomParam, key, ui, onSetupDialog });
  document.getElementById('btn-guide').href = `/rehber.html?room=${roomParam}`;
  room = { id: roomParam, key, ui: roomUi };
  if (key) {
    ui.canRename = () => true;
    ui.futureLabel = (a) => (a.isSubagent ? null : `${a.owner || 'Bu kişi'} adlı kişinin yeni oturumlarına da bu adı ver`);
  }
  ui.emptyHint = key ? "'Ajan bağla' ile ekleyin." : 'Ajanlar bağlandığında burada görünür.';
  transport = new PollingTransport({ roomId: roomParam });
  transport.boost({ emptyMs: BOOST_MS });
  transport.start(handlers);
  document.addEventListener('visibilitychange', updateActive);
  updateActive();
  setupMiniButton();
  cloud.enableInstallButton();
  if (!viewMini) cloud.registerServiceWorker();
  if (key && config && config.relay && !viewMini) setupV3(roomParam, key).catch((e) => console.warn('[pixel-office] v3 controls unavailable', e));
  if (params.get('new') === '1') {
    // Just created: drop the marker and open the invite dialog once.
    try { history.replaceState(history.state, '', `/?room=${roomParam}`); } catch { /* ignore */ }
    if (key) document.getElementById('btn-invite').click();
  }
}

// --- v3: device control (V3.md §11) ---------------------------------------------------------------------
async function setupV3(roomId, key) {
  const [{ DeviceHub }, { PromptPanel }, { DevicesPage, PairWizard, NewAgentDialog }, { fmtCompact: fmtTok }] = await Promise.all([
    import('./control.js'), import('./prompt.js'), import('./devices.js'), import('./usage.js')]);
  const $ = (id) => document.getElementById(id);
  const hub = new DeviceHub({ roomId, writeKey: key,
    isVisible: () => document.visibilityState !== 'hidden' || !!(mini && mini.window) });
  try {
    await hub.init();
  } catch (err) {
    console.warn('[pixel-office] no key store for device pairing', err);
    return;   // e.g. IndexedDB disabled: the v2 room keeps working, no v3 controls
  }
  const toast = (t, o) => ui.toast(t, o);
  const wizard = new PairWizard({ dialog: $('dlg-pair'), hub, toast, copy: (t) => cloud.copyText(t),
    commands: (name) => cloud.buildConnectorCommands({ origin: location.origin, room: roomId, key, name: name || cloud.getName() }),
    onPaired: () => { if (devices.isOpen) devices.render(); } });
  const pairName = $('pair-name');
  if (pairName) pairName.value = cloud.getName();
  const newAgent = new NewAgentDialog({ dialog: $('dlg-agent'), hub, toast,
    onCreated: (ctl) => {
      v3.pendingSelect = ctl;
      if (devices.isOpen) { devices.close(); setTab(phone() ? 'agents' : 'office'); }
      if (transport && transport.boost) transport.boost({ ms: 60000 });
      if (transport) transport.pollNow({ bust: true });
    } });
  const setTab = (t) => {
    document.body.dataset.tab = t;
    for (const b of document.querySelectorAll('#tabbar [data-tab]')) {
      if (b.dataset.tab === t) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    }
    if (t === 'devices') devices.open(); else if (devices.isOpen) devices.close();
    if (t !== 'devices') fitCanvas();
  };
  const phone = () => window.matchMedia('(max-width: 600px)').matches;
  const devices = new DevicesPage({ hub, root: $('devices-page'), wizard, newAgent, toast,
    onSelectAgent: (ctl) => {
      const hit = [...agents.values()].find((x) => x.control && x.control.device === ctl.device && x.control.agent === ctl.agent);
      devices.close();
      setTab(phone() ? 'agents' : 'office');
      if (hit) select(hit.id); else { v3.pendingSelect = ctl; toast('Ajan ofise girince açılacak'); }
    },
    onToggle: (open) => { if (!open && document.body.dataset.tab === 'devices') setTab('office'); } });
  let boosted = false;
  const panel = new PromptPanel({ hub, slot: $('agent-control'), toast, headStop: $('sheet-stop'),
    onPair: () => wizard.open(2), onDevices: () => devices.open(),
    onActivity: (running) => {
      hub.need('task', running);
      if (running && !boosted && transport && transport.boost) transport.boost({ ms: 120000 });
      if (!running && transport && transport.boost) transport.boost({ ms: 30000 });
      boosted = running;
    } });
  v3 = { hub, panel, devices, wizard, newAgent, pendingSelect: null, setTab };
  window.pixelOffice.v3 = v3;

  ui.ctlInfo = (a) => {
    if (!a.control || !hub.isReady(a.control.device)) return null;
    const info = hub.agentInfo(a.control);
    const t = info && info.today && Number.isFinite(info.today.tokens) ? info.today.tokens : null;
    return { tokens: t === null ? null : `bugün ${fmtTok(t)} token` };
  };
  hub.on('change', () => refreshUI());
  hub.on('notice', (t) => toast(t, { error: true }));

  document.body.classList.add('v3');
  document.body.dataset.tab = 'office';
  $('btn-devices').hidden = false;
  $('btn-devices').addEventListener('click', () => (devices.isOpen ? devices.close() : devices.open()));
  $('btn-newagent').addEventListener('click', () => newAgent.open());
  $('tabbar').hidden = false;
  for (const b of document.querySelectorAll('#tabbar [data-tab]')) {
    b.addEventListener('click', () => { if (selectedId) select(null); setTab(b.dataset.tab); });
  }
  $('sheet-back').addEventListener('click', () => select(null));
  refreshUI();
}

async function boot() {
  bootCommon();
  backend = await cloud.detectBackend({ onRetry: () => ui.setConnection('reconnecting') });
  if (backend.kind === 'cloud') bootCloud(backend.config);
  else await bootLocal();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
