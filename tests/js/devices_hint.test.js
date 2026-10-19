// Phase 17: the "no workspaces on this device" hint in web/js/devices.js (headless Chrome, tests/test_relay_js.py).
// The new-agent dialog runs against a fake hub; the hint is static text built with textContent only.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NewAgentDialog, NO_WORKSPACE_HINT, NO_WORKSPACE_CMD, noWorkspaces, workspaceHintEl } from '../../web/js/devices.js';

const DIALOG = `
<form id="agent-form" autocomplete="off">
  <label>Cihaz <select name="device"></select></label>
  <fieldset><div id="agent-engines"></div></fieldset>
  <label>Çalışma klasörü <select name="workspace"></select></label>
  <label id="agent-sub-row" hidden><input name="sub" list="agent-sub-list"><datalist id="agent-sub-list"></datalist></label>
  <label>Model <select name="model"></select></label>
  <label>Ad <input name="name"></label>
  <label id="agent-profile-row"><select name="profile"></select></label>
  <p id="agent-auto-note" hidden></p>
  <p id="agent-local-note" hidden></p>
  <input type="checkbox" name="useBudget"><input name="budgetLimit"><select name="budgetUnit"><option value="usd">$</option></select>
  <select name="budgetAction"><option value="warn">uyar</option></select>
  <p id="agent-error" hidden></p>
  <button type="button" data-agent-cancel>Vazgeç</button><button type="submit" name="create">Oluştur</button>
</form>`;

function fakeHub(workspaces) {
  const dev = { id: 'd1', label: 'PC', ready: true, info: { engines: { claude: { available: true }, ollama: { available: false } } } };
  const sent = [];
  return {
    sent,
    devices: new Map([[dev.id, dev]]),
    device: (id) => (id === dev.id ? dev : null),
    isReady: () => true,
    async request(id, msg) {
      sent.push(msg);
      if (msg.type === 'workspace.list') return { workspaces };
      throw new Error('unexpected ' + msg.type);
    },
  };
}

function makeDialog(workspaces) {
  const dialog = document.createElement('dialog');
  dialog.innerHTML = DIALOG;
  document.body.appendChild(dialog);
  const hub = fakeHub(workspaces);
  const d = new NewAgentDialog({ dialog, hub, toast: () => {}, onCreated: null });
  return { dialog, hub, d };
}

const tick = () => new Promise((r) => setTimeout(r, 20));

test('hint text and element', () => {
  assert.ok(NO_WORKSPACE_HINT.startsWith('Bu cihazda izinli klasör yok.'));
  assert.ok(NO_WORKSPACE_HINT.includes(NO_WORKSPACE_CMD));
  assert.ok(NO_WORKSPACE_HINT.includes('~/PixelOfficeIsler'));
  const p = workspaceHintEl();
  assert.equal(p.textContent, NO_WORKSPACE_HINT);
  assert.equal(p.querySelector('code').textContent, 'pixel-connector workspace add ~/projem');
  assert.equal(noWorkspaces([]), true);
  assert.equal(noWorkspaces(null), false);          // unknown (request failed) is not "empty"
  assert.equal(noWorkspaces(undefined), false);
  assert.equal(noWorkspaces([{ id: 'isler' }]), false);
});

test('new-agent dialog: empty allow-list shows the hint and blocks Claude with it', async () => {
  const { dialog, d, hub } = makeDialog([]);
  d.open('d1');
  await tick();
  const hint = dialog.querySelector('[data-ws-hint]');
  assert.ok(hint, 'hint element');
  assert.equal(hint.hidden, false);
  assert.equal(hint.textContent, NO_WORKSPACE_HINT);
  assert.equal(dialog.querySelector('select[name=workspace]').options[0].textContent, 'İzinli klasör yok');
  await d.create();
  assert.equal(dialog.querySelector('#agent-error').textContent, NO_WORKSPACE_HINT);
  assert.ok(!hub.sent.some((m) => m.type === 'agent.create'), 'no request without a workspace');
  dialog.close();
  dialog.remove();
});

test('new-agent dialog: a device with workspaces shows no hint', async () => {
  const { dialog, d } = makeDialog([{ id: 'isler', name: '<img src=x onerror=window.__xss=1>', children: [] }]);
  d.open('d1');
  await tick();
  assert.equal(dialog.querySelector('[data-ws-hint]').hidden, true);
  const opt = dialog.querySelector('select[name=workspace]').options[0];
  assert.equal(opt.value, 'isler');
  assert.equal(opt.textContent, '<img src=x onerror=window.__xss=1>');   // device text stays text
  assert.equal(dialog.querySelector('img'), null);
  dialog.close();
  dialog.remove();
});
