// Pixel Office — cloud mode (CLOUD.md §3.2, §9.1, §9.3, §7.5–§7.7, §10.2).
// Backend detection, landing (create / join / recent rooms), key handling (#key= fragment → localStorage,
// then stripped from the address bar), invite + "Ajan bağla" dialogs, service worker, install prompt.
// Untrusted strings (room names, owner names) only reach the DOM via textContent / input.value.

export const ROOM_RE = /^[a-z2-7]{26}$/;
export const KEY_RE = /^[A-Za-z0-9_-]{32}$/;
const OWNER_ID_RE = /^[A-Za-z0-9_-]{4,32}$/;
const LS_ROOMS = 'po.rooms', LS_NAME = 'po.name', LS_LAST = 'po.lastRoom', LS_OWNER_ID = 'po.ownerId';
const MAX_ROOMS = 10;

// --- storage (localStorage can throw in private modes: every access is guarded) ----------------
function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } }

export function loadRooms() {
  let arr;
  try { arr = JSON.parse(lsGet(LS_ROOMS) || '[]'); } catch { arr = []; }
  if (!Array.isArray(arr)) return [];
  return arr.filter((r) => r && typeof r.id === 'string' && ROOM_RE.test(r.id)).map((r) => ({
    id: r.id,
    name: typeof r.name === 'string' ? r.name.slice(0, 64) : '',
    key: typeof r.key === 'string' && KEY_RE.test(r.key) ? r.key : null,
    lastOpened: Number(r.lastOpened) || 0,
  }));
}

function saveRooms(rooms) {
  rooms.sort((a, b) => b.lastOpened - a.lastOpened);
  lsSet(LS_ROOMS, JSON.stringify(rooms.slice(0, MAX_ROOMS)));
}

export function getRoom(id) { return loadRooms().find((r) => r.id === id) || null; }

/** Insert/update a room. A key, once known, is kept unless a new valid key is given. */
export function rememberRoom(id, { name, key, touch = true } = {}) {
  if (!ROOM_RE.test(id)) return;
  const rooms = loadRooms();
  let r = rooms.find((x) => x.id === id);
  if (!r) { r = { id, name: '', key: null, lastOpened: 0 }; rooms.push(r); }
  if (typeof name === 'string' && name) r.name = name.slice(0, 64);
  if (typeof key === 'string' && KEY_RE.test(key)) r.key = key;
  if (touch) { r.lastOpened = Date.now(); lsSet(LS_LAST, id); }
  saveRooms(rooms);
}

export function forgetRoom(id) {
  saveRooms(loadRooms().filter((r) => r.id !== id));
  if (lsGet(LS_LAST) === id) lsSet(LS_LAST, '');
}

export function getName() { return (lsGet(LS_NAME) || '').slice(0, 24); }
export function setName(n) { lsSet(LS_NAME, String(n || '').slice(0, 24)); }

/** Opaque, stable per browser (used only by the HTTP-hook snippet). */
export function getOwnerId() {
  let id = lsGet(LS_OWNER_ID);
  if (!id || !OWNER_ID_RE.test(id)) {
    const b = new Uint8Array(9);
    crypto.getRandomValues(b);
    id = 'o_' + btoa(String.fromCharCode(...b)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    lsSet(LS_OWNER_ID, id);
  }
  return id;
}

// --- backend detection (§9.1) --------------------------------------------------------------------
/**
 * → {kind:'local', config|null} | {kind:'cloud', config}
 * Any HTTP answer decides (v1 local server: 404 → local). A network error (offline PWA start from the
 * service-worker cache) is retried with back-off 2 → 30 s instead of being mistaken for local mode.
 */
export async function detectBackend({ onRetry = null } = {}) {
  for (let attempt = 0; ; attempt++) {
    let r;
    try {
      r = await fetch('/api/config', { cache: 'no-store', headers: { Accept: 'application/json' } });
    } catch {
      if (onRetry) onRetry(attempt);
      await new Promise((res) => setTimeout(res, Math.min(30000, 2000 * 2 ** attempt)));
      continue;
    }
    if (r.ok) {
      try {
        const config = await r.json();
        if (config && config.mode === 'cloud') return { kind: 'cloud', config };
        if (config && typeof config === 'object') return { kind: 'local', config };   // v2 local server (mode "local")
      } catch { /* not JSON → local */ }
    }
    return { kind: 'local', config: null };
  }
}

// --- links ----------------------------------------------------------------------------------------
export const viewUrl = (roomId) => `${location.origin}/?room=${roomId}`;
export const joinUrl = (roomId, key) => `${viewUrl(roomId)}#key=${key}`;

/**
 * Read `#key=` for this room, store it, and strip the fragment from the address bar (§3.2).
 * @returns {string|null} the key for this room (from the fragment or from storage)
 */
export function takeKeyFromHash(roomId) {
  const hash = location.hash || '';
  if (hash.length > 1) {
    const params = new URLSearchParams(hash.slice(1));
    const key = params.get('key');
    if (key && KEY_RE.test(key)) rememberRoom(roomId, { key, touch: false });
    try { history.replaceState(history.state, '', location.pathname + location.search); } catch { /* ignore */ }
  }
  const r = getRoom(roomId);
  return r ? r.key : null;
}

/** Parse a pasted invite link or a bare room id → {roomId, key|null} or null. */
export function parseInvite(text) {
  const t = String(text || '').trim();
  if (ROOM_RE.test(t)) return { roomId: t, key: null };
  let u;
  try { u = new URL(t, location.origin); } catch { return null; }
  const roomId = u.searchParams.get('room');
  if (!roomId || !ROOM_RE.test(roomId)) return null;
  const key = new URLSearchParams((u.hash || '').slice(1)).get('key');
  return { roomId, key: key && KEY_RE.test(key) ? key : null };
}

// --- clipboard --------------------------------------------------------------------------------------
export async function copyText(text, inputEl = null) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    if (inputEl) {
      inputEl.focus();
      inputEl.select();
      try { return document.execCommand('copy'); } catch { return false; }
    }
    return false;
  }
}

// --- setup commands (§7.5, §7.7, §17) ------------------------------------------------------------
/** Name safe inside '…' in sh and PowerShell: letters, digits, space, . _ - only, not starting with - or . */
export function cmdName(name) {
  // A leading '-' would make argparse / PowerShell read the name as an option: strip leading dashes/dots.
  const s = String(name || '').normalize('NFC').replace(/[^\p{L}\p{N} ._-]/gu, '').replace(/\s+/g, ' ').trim()
    .replace(/^[\s.-]+/, '');
  return [...s].slice(0, 24).join('').trim() || 'Arkadas';
}

const HTTP_HOOK_EVENTS = ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PermissionRequest', 'Notification',
  'Stop', 'SubagentStart', 'SubagentStop', 'SessionEnd'];
const MATCHER_EVENTS = new Set(['PreToolUse', 'PermissionRequest']);

/** All copy-paste commands of the "Ajan bağla" dialog, filled in with origin/room/key/name. */
export function buildSetupCommands({ origin, room, key, name, ownerId }) {
  const n = cmdName(name);
  const o = origin;
  const statusUrl = `${o}/api/status?room=${room}`;
  const curlBody = JSON.stringify({ session: 'my-agent', owner: n, project: 'demo', state: 'working', tool: 'Build', label: 'Derleniyor', source: 'custom' });
  const curlOff = JSON.stringify({ session: 'my-agent', owner: n, state: 'offline' });
  const target = `--url ${o} --room ${room} --key ${key} --owner '${n}'`;
  const hook = { type: 'http', url: `${o}/api/cc_hook?room=${room}`,
    headers: { Authorization: `Bearer ${key}`, 'X-Pixel-Owner': encodeURIComponent(n), 'X-Pixel-Owner-Id': ownerId }, timeout: 3 };
  const hooks = {};
  for (const ev of HTTP_HOOK_EVENTS) hooks[ev] = [MATCHER_EVENTS.has(ev) ? { matcher: '*', hooks: [hook] } : { hooks: [hook] }];
  return {
    unix: `curl -fsSL ${o}/hook/install.sh | sh -s -- --server ${o} --room ${room} --key ${key} --name '${n}'`,
    'unix-doctor': 'python3 ~/.pixel-office/pixel_hook.py doctor',
    'unix-uninstall': 'python3 ~/.pixel-office/pixel_hook.py uninstall',
    win: `powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm ${o}/hook/install.ps1))) -Server ${o} -Room ${room} -Key ${key} -Name '${n}'"`,
    curl: `curl -X POST "${statusUrl}" \\\n  -H "Authorization: Bearer ${key}" -H "Content-Type: application/json" \\\n  -d '${curlBody}'`,
    'curl-off': `# bitince (karakter kapıdan çıkar):\ncurl -X POST "${statusUrl}" \\\n  -H "Authorization: Bearer ${key}" -H "Content-Type: application/json" \\\n  -d '${curlOff}'`,
    'agent-dl': `curl -fsSL ${o}/hook/pixel_agent.py -o pixel_agent.py`,
    'pixel-run': `python3 pixel_agent.py run ${target} --name aider -- aider --model ollama/qwen2.5-coder`,
    'pixel-proxy': `python3 pixel_agent.py proxy ${target} --listen 127.0.0.1:11435 --upstream http://127.0.0.1:11434 --name ollama`,
    antigravity: `curl -fsSL ${o}/hook/pixel_antigravity.py -o pixel_antigravity.py\npython3 pixel_antigravity.py ${target}`,
    'antigravity-service': 'python3 pixel_antigravity.py --install-service',
    'hook-status': 'python3 ~/.pixel-office/pixel_hook.py status --session cursor --state working --tool Edit --project web',
    'http-hook': JSON.stringify({ hooks }, null, 2),
  };
}

/** v3 "Cihaz ekle" step 1 (V3.md §5.4, §11.5): connector installers with this room's values, same quoting as above. */
export function buildConnectorCommands({ origin, room, key, name }) {
  const n = cmdName(name);
  const o = origin;
  const unix = `curl -fsSL ${o}/connector/install.sh | sh -s -- --server ${o} --room ${room} --key ${key} --name '${n}'`;
  return {
    linux: unix,
    mac: unix,
    win: `irm ${o}/connector/install.ps1 -OutFile install.ps1; powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Server ${o} -Room ${room} -Key ${key} -Name '${n}'`,
    pair: 'pixel-connector pair',
  };
}

// --- landing (§9.3) ---------------------------------------------------------------------------------
/**
 * Show the landing overlay. Resolves never (navigation replaces the page).
 * @param {{config:object, error?:string|null}} opts
 */
export function showLanding({ config, error = null }) {
  const el = document.getElementById('landing');
  el.hidden = false;
  document.body.classList.add('landing-open');
  const errEl = document.getElementById('landing-error');
  const showError = (msg) => { errEl.textContent = msg; errEl.hidden = !msg; };
  showError(error);

  const create = document.getElementById('create-form');
  create.elements.name.value = getName();
  const needsToken = !!(config && config.createRequiresToken);
  document.getElementById('create-token-row').hidden = !needsToken;
  create.elements.token.required = needsToken;
  create.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = create.querySelector('button[type=submit]');
    const name = create.elements.name.value.trim();
    if (name) setName(name);
    const body = { name: create.elements.room.value.trim() || 'Pixel Ofis' };
    if (needsToken) body.createToken = create.elements.token.value;
    btn.disabled = true;
    showError(null);
    try {
      const r = await fetch('/api/rooms', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (r.status === 201 && d && ROOM_RE.test(d.roomId) && KEY_RE.test(d.writeKey)) {
        rememberRoom(d.roomId, { name: typeof d.name === 'string' ? d.name : body.name, key: d.writeKey });
        location.assign(`/?room=${d.roomId}&new=1`);
        return;
      }
      showError(r.status === 403 ? 'Oluşturma parolası yanlış.'
        : r.status === 429 ? 'Çok fazla oda oluşturuldu. Biraz sonra yeniden deneyin.'
          : r.status === 400 ? 'Oda adı geçersiz.'
            : 'Oda oluşturulamadı. Sunucuya ulaşılamıyor olabilir.');
    } catch {
      showError('Oda oluşturulamadı. Sunucuya ulaşılamıyor.');
    }
    btn.disabled = false;
  });

  const join = document.getElementById('join-form');
  join.addEventListener('submit', (e) => {
    e.preventDefault();
    const inv = parseInvite(join.elements.link.value);
    if (!inv) { showError('Bağlantı tanınmadı. "?room=…" içeren davet bağlantısını yapıştırın.'); return; }
    rememberRoom(inv.roomId, { key: inv.key });
    location.assign(`/?room=${inv.roomId}`);
  });

  const rooms = loadRooms();
  const block = document.getElementById('recent-block');
  const list = document.getElementById('recent-list');
  block.hidden = !rooms.length;
  list.textContent = '';
  for (const r of rooms) {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = `/?room=${r.id}`;
    a.className = 'recent-name';
    a.textContent = r.name || 'Adsız oda';
    const role = document.createElement('span');
    role.className = 'tag ' + (r.key ? 'tag-key' : 'tag-view');
    role.textContent = r.key ? 'katılımcı' : 'izleyici';
    role.title = r.key ? 'Yazma anahtarı bu tarayıcıda kayıtlı' : 'Yalnızca izleme';
    const forget = document.createElement('button');
    forget.type = 'button';
    forget.className = 'btn ghost small';
    forget.textContent = 'Unut';
    forget.title = 'Bu odayı (ve anahtarını) bu tarayıcıdan sil';
    forget.addEventListener('click', () => { forgetRoom(r.id); li.remove(); if (!list.children.length) block.hidden = true; });
    li.append(a, role, forget);
    list.appendChild(li);
  }
}

// --- room toolbar + dialogs (§9.3) ------------------------------------------------------------------
/**
 * @param {{roomId:string, key:string|null, ui:object, onSetupDialog?:(open:boolean)=>void}} ctx
 *   onSetupDialog: called when "Ajan bağla" opens/closes (main.js boosts polling, CLOUD.md §17.10)
 * @returns {{setRoomName(name:string):void}}
 */
export function setupRoomUI({ roomId, key, ui, onSetupDialog = null }) {
  const $ = (id) => document.getElementById(id);
  for (const el of document.querySelectorAll('[data-cloud]')) el.hidden = false;
  $('toolbar').hidden = false;
  const roomName = $('room-name');
  roomName.hidden = false;
  const r0 = getRoom(roomId);
  roomName.textContent = (r0 && r0.name) || 'Oda';
  if (!key) {
    // v2.md E4: opened with the view link (no write key) → say so, instead of silently missing controls.
    roomName.title = 'Yalnızca izleme bağlantısı';
    $('view-only').hidden = false;
    $('view-only-hint').hidden = false;
  }

  // Copy buttons inside dialogs.
  for (const b of document.querySelectorAll('[data-copy]')) {
    b.addEventListener('click', async () => {
      const input = $(b.dataset.copy);
      const ok = await copyText(input.value, input);
      ui.toast(ok ? 'Panoya kopyalandı' : 'Kopyalanamadı, metni elle seçin', { error: !ok });
    });
  }

  // Invite.
  const dlgInvite = $('dlg-invite');
  $('btn-invite').addEventListener('click', () => {
    $('invite-view').value = viewUrl(roomId);
    $('invite-join-block').hidden = !key;
    $('invite-nokey').hidden = !!key;
    if (key) $('invite-join').value = joinUrl(roomId, key);
    dlgInvite.showModal();
  });

  // Ajan bağla.
  const dlgSetup = $('dlg-setup');
  $('setup-nokey').hidden = !!key;
  $('setup-body').hidden = !key;
  const nameInput = $('setup-name');
  nameInput.value = getName();
  for (const a of dlgSetup.querySelectorAll('a[data-href]')) {
    a.href = a.dataset.href;
    a.textContent = location.origin + a.dataset.href;
  }
  const fill = () => {
    if (!key) return;
    const cmds = buildSetupCommands({ origin: location.origin, room: roomId, key, name: nameInput.value || getName(), ownerId: getOwnerId() });
    for (const box of dlgSetup.querySelectorAll('.cmd[data-cmd]')) renderCmd(box, cmds[box.dataset.cmd] || '', ui);
  };
  nameInput.addEventListener('input', () => { setName(nameInput.value.trim()); fill(); });
  const tabs = [...dlgSetup.querySelectorAll('.tab')];
  for (const t of tabs) {
    t.addEventListener('click', () => {
      for (const x of tabs) x.setAttribute('aria-selected', x === t ? 'true' : 'false');
      for (const p of dlgSetup.querySelectorAll('.tab-pane')) p.hidden = p.dataset.pane !== t.dataset.tab;
    });
  }
  // Windows visitors get the Windows tab first.
  if (/Windows/i.test(navigator.userAgent)) tabs.find((t) => t.dataset.tab === 'win')?.click();
  const accept = $('adv-accept');
  accept.addEventListener('change', () => { $('adv-snippet').hidden = !accept.checked; });
  $('btn-setup').addEventListener('click', () => {
    accept.checked = false;
    $('adv-snippet').hidden = true;
    fill();
    dlgSetup.showModal();
    if (onSetupDialog) onSetupDialog(true);
  });
  dlgSetup.addEventListener('close', () => { if (onSetupDialog) onSetupDialog(false); });

  // Light-dismiss: a click on the backdrop closes the dialog.
  for (const d of [dlgInvite, dlgSetup]) {
    d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
  }

  let shownName = null;
  return {
    setRoomName(name) {
      if (typeof name !== 'string' || !name || name === shownName) return;
      shownName = name;
      roomName.textContent = name;
      document.title = `${name} · Pixel Office`;
      rememberRoom(roomId, { name, touch: false });
    },
  };
}

function renderCmd(box, text, ui) {
  box.textContent = '';
  const pre = document.createElement('pre');
  const code = document.createElement('code');
  code.textContent = text;
  pre.appendChild(code);
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn small copy-btn';
  btn.textContent = 'Kopyala';
  btn.addEventListener('click', async () => {
    const ok = await copyText(text);
    ui.toast(ok ? 'Komut kopyalandı' : 'Kopyalanamadı, metni elle seçin', { error: !ok });
  });
  box.append(pre, btn);
}

// --- PWA (§10) ---------------------------------------------------------------------------------------
/** Release of the files this page was loaded from; equal to sw.js VERSION (tests/test_release.py checks it). */
export const APP_VERSION = 'po-v4.0.10';
const SW_VERSION_RE = /^po-v[0-9.]{1,20}$/;

/** v2.md H16: a newer service worker took over while this (older) page was open → offer a reload. */
function showUpdateNotice() {
  if (document.getElementById('update-toast')) return;
  const box = document.createElement('div');
  box.id = 'update-toast';
  box.className = 'update-toast';
  box.setAttribute('role', 'status');
  const text = document.createElement('span');
  text.textContent = 'Yeni sürüm hazır';
  const reload = document.createElement('button');
  reload.type = 'button';
  reload.className = 'btn small accent';
  reload.textContent = 'Yenile';
  reload.addEventListener('click', () => location.reload());
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'btn small ghost';
  close.textContent = '✕';
  close.setAttribute('aria-label', 'Kapat');
  close.addEventListener('click', () => box.remove());
  box.append(text, reload, close);
  document.body.appendChild(box);
}

export function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return Promise.resolve(null);
  const sw = navigator.serviceWorker;
  sw.addEventListener('message', (ev) => {
    const d = ev.data;
    if (!d || d.type !== 'po-sw-version' || typeof d.version !== 'string' || !SW_VERSION_RE.test(d.version)) return;
    if (d.version !== APP_VERSION) showUpdateNotice();
  });
  if (typeof sw.startMessages === 'function') sw.startMessages();
  return sw.register('/sw.js', { scope: '/' }).catch((err) => {
    console.warn('[pixel-office] service worker registration failed', err);
    return null;
  });
}

let installEvent = null;
let installBtn = null;
/** Capture `beforeinstallprompt` as early as possible; the button is shown only in cloud mode. */
export function captureInstallPrompt() {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    installEvent = e;
    if (installBtn) installBtn.hidden = false;
  });
  window.addEventListener('appinstalled', () => {
    installEvent = null;
    if (installBtn) installBtn.hidden = true;
  });
}

export function enableInstallButton() {
  installBtn = document.getElementById('btn-install');
  installBtn.hidden = !installEvent;
  installBtn.addEventListener('click', async () => {
    if (!installEvent) return;
    const ev = installEvent;
    installEvent = null;
    installBtn.hidden = true;
    try { await ev.prompt(); } catch { /* ignore */ }
  });
}

export function lastRoomId() {
  const id = lsGet(LS_LAST);
  return id && ROOM_RE.test(id) ? id : null;
}
