// Every library module under web/js loads as an ES module in a real browser (a syntax error in a file no other suite
// imports would otherwise ship unnoticed). Page entry points with side effects (main, guide, tanitim,
// sprite-preview) are left out; tests/test_release.py checks that this list covers every other file.
import { test } from 'node:test';
import assert from 'node:assert/strict';

export const MODULES = [
  'agents.js',
  'approvals.js',
  'audio.js',
  'board.js',
  'boss.js',
  'boss_scene.js',
  'browser_device.js',
  'browser_office.js',
  'cloud.js',
  'control.js',
  'demo.js',
  'devices.js',
  'e2e.js',
  'fx.js',
  'i18n.js',
  'i18n_en.js',
  'idb.js',
  'keystore.js',
  'notify.js',
  'office.js',
  'pathfinding.js',
  'pip.js',
  'preview.js',
  'prompt.js',
  'relay.js',
  'renderer.js',
  'scene_controls.js',
  'sprites.js',
  'template_builder.js',
  'transport.js',
  'ui.js',
  'update_banner.js',
  'usage.js',
  'workflows.js',
];

test('every web/js library module loads', async () => {
  for (const m of MODULES) {
    try {
      await import(`/web/js/${m}`);
    } catch (e) {
      assert.fail(`${m}: ${e && e.message}`);
    }
  }
});
