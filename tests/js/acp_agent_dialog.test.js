// F4-6a (headless Chrome, tests/test_relay_js.py): the new-agent dialog offers the computer's ACP agents, only with a
// work folder and the readonly / edit profiles, shows the A3 note, and sends engine "acp" with its engineId and no model.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NewAgentDialog } from '../../web/js/devices.js';

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

function setup() {
  const info = { engines: { claude: { available: true }, ollama: { available: false },
    acp: [{ id: 'codex', name: 'Codex <b>CLI</b>', available: true }, { id: 'kapali', name: 'Kapalı', available: false }] },
  profiles: ['readonly', 'edit', 'dev', 'auto'].map((id) => ({ id, enabled: id !== 'auto' })) };
  const dev = { id: 'd1', label: 'PC', ready: true, info };
  const sent = [];
  const hub = { devices: new Map([[dev.id, dev]]), device: (id) => (id === dev.id ? dev : null), isReady: () => true,
    putAgent() {},
    async request(id, msg) {
      sent.push(msg);
      if (msg.type === 'workspace.list') return { workspaces: [{ id: 'isler', name: 'İşler' }] };
      if (msg.type === 'agent.create') return { type: 'agent.created', agent: { key: 'a1', name: msg.name } };
      throw new Error('unexpected ' + msg.type);
    } };
  const dialog = document.createElement('dialog');
  dialog.innerHTML = DIALOG;
  document.body.appendChild(dialog);
  const d = new NewAgentDialog({ dialog, hub, toast: () => {}, onCreated: null });
  return { dialog, d, sent };
}

const tick = () => new Promise((r) => setTimeout(r, 20));

test('ACP agents: listed by the computer, folder and readonly/edit only, A3 note, engineId sent, no model', async () => {
  const { dialog, d, sent } = setup();
  d.open('d1');
  await tick();
  const radio = dialog.querySelector('input[name=engine][value="acp:codex"]');
  assert.ok(radio);
  assert.equal(radio.parentElement.textContent.includes('Codex <b>CLI</b> (ACP)'), true);     // text, not markup
  assert.equal(dialog.querySelector('#agent-engines b'), null);
  assert.equal(dialog.querySelector('input[value="acp:kapali"]').disabled, true);
  radio.checked = true;
  radio.dispatchEvent(new Event('change'));
  const ws = dialog.querySelector('select[name=workspace]');
  assert.ok(![...ws.options].some((o) => o.value === ''), 'no "chat only" choice for an ACP agent');
  const prof = dialog.querySelector('select[name=profile]');
  assert.equal(dialog.querySelector('#agent-profile-row').hidden, false);
  assert.deepEqual([...prof.options].filter((o) => !o.disabled).map((o) => o.value), ['readonly', 'edit']);
  assert.equal(dialog.querySelector('#agent-acp-note').hidden, false);
  prof.value = 'readonly';
  await d.create();
  const msg = sent.find((m) => m.type === 'agent.create');
  assert.deepEqual({ engine: msg.engine, engineId: msg.engineId, model: msg.model, profile: msg.profile, ws: msg.workspace.id },
    { engine: 'acp', engineId: 'codex', model: null, profile: 'readonly', ws: 'isler' });
  // back to Claude: dev is offered again, the note hides
  d.open('d1');
  await tick();
  const claude = dialog.querySelector('input[name=engine][value=claude]');
  claude.checked = true;
  claude.dispatchEvent(new Event('change'));
  assert.equal([...prof.options].find((o) => o.value === 'dev').disabled, false);
  assert.equal(dialog.querySelector('#agent-acp-note').hidden, true);
  dialog.remove();
});
