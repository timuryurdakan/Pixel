// F4-3 (headless Chrome): "Cihaza kaydet" in the template builder appears only for a connector that announces
// workflowSave, hands the saved template to the dialog, and the dialog sends workflow.save with a long timeout (the
// computer confirms it) and reloads the template list.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TemplateBuilder } from '../../web/js/template_builder.js';
import { supportsWorkflowSave, SAVE_TIMEOUT_MS, WorkflowDialog } from '../../web/js/workflows.js';

const TPL = { id: 'blog-hatti', name: 'Blog <b>hattı</b>', steps: [{ id: 'yaz', type: 'agent', role: 'Yazar', engine: 'claude',
  profile: 'edit', prompt: 'Yaz' }] };

function storage(list) {
  const data = { 'po-custom-templates': JSON.stringify({ v: 1, templates: list }) };
  return { getItem: (k) => data[k] ?? null, setItem: (k, v) => { data[k] = v; } };
}

test('the feature check needs a ready workflows device that lists workflowSave', () => {
  const dev = (features, ready = true) => ({ ready, info: { features } });
  assert.equal(supportsWorkflowSave(dev(['workflows', 'workflowSave'])), true);
  assert.equal(supportsWorkflowSave(dev(['workflows'])), false);
  assert.equal(supportsWorkflowSave(dev(['workflowSave'])), false);
  assert.equal(supportsWorkflowSave(dev(['workflows', 'workflowSave'], false)), false);
  assert.equal(supportsWorkflowSave(null), false);
});

test('the builder shows "Cihaza kaydet" only when the device can save, and passes the template', () => {
  const box = document.createElement('div');
  const sent = [];
  let can = false;
  const b = new TemplateBuilder({ container: box, storage: storage([TPL]), onSaveToDevice: (t) => sent.push(t),
    canSaveToDevice: () => can });
  b.render();
  assert.equal(box.querySelector('[data-tb-action="save-device"]'), null);
  can = true;
  b.render();
  const btn = box.querySelector('[data-tb-action="save-device"]');
  assert.ok(btn);
  btn.click();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].id, 'blog-hatti');
  assert.equal(box.querySelector('img, b'), null);                      // names stay text
});

test('the dialog sends workflow.save with the long timeout and reloads after the computer confirmed', async () => {
  const calls = [];
  const toasts = [];
  const dev = { id: 'd1', ready: true, info: { features: ['workflows', 'workflowSave'] } };
  const fake = { hub: { devices: new Map([['d1', dev]]), request: async (id, msg, o) => { calls.push([id, msg, o]); return { type: 'workflow.saved', template: msg.template.id }; } },
    f: { device: { value: 'd1' } }, toast: (t, o) => toasts.push([t, o]), _loadDevice: () => calls.push('reload') };
  await WorkflowDialog.prototype._saveToDevice.call(fake, TPL);
  assert.deepEqual(calls[0][1], { type: 'workflow.save', template: TPL });
  assert.equal(calls[0][2].timeout, SAVE_TIMEOUT_MS);
  assert.equal(calls[1], 'reload');
  fake.hub.devices.set('d1', { ...dev, info: { features: ['workflows'] } });
  await WorkflowDialog.prototype._saveToDevice.call(fake, TPL);
  assert.equal(calls.length, 2);                                         // older connector: nothing sent
  assert.ok(toasts.at(-1)[1].error);
});
