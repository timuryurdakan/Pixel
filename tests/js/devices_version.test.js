// Phase 20 / Package 2 (v2.md E1): connector version label and update hint on the device card (web/js/devices.js).
// Runs in headless Chrome through tests/test_relay_js.py. Pure functions only; the card renders them with textContent.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { connectorLabel, connectorOutdated, parsePublishedSha, UPDATE_HINT, MIN_CONNECTOR } from '../../web/js/devices.js';

const A = 'a'.repeat(64);
const B = 'b'.repeat(64);

test('SHA256SUMS parsing', () => {
  const sums = `${'1'.repeat(64)}  install.ps1\n${'2'.repeat(64)}  install.sh\n${A}  pixel-connector.pyz\n`;
  assert.equal(parsePublishedSha(sums), A);
  assert.equal(parsePublishedSha(sums.replace(/\n/g, '\r\n')), A);
  assert.equal(parsePublishedSha(`${A}  pixel-connector.pyz.bak\n`), null);
  assert.equal(parsePublishedSha(`${'Z'.repeat(64)}  pixel-connector.pyz\n`), null);
  assert.equal(parsePublishedSha('<html>not found</html>'), null);
  assert.equal(parsePublishedSha(null), null);
});

test('version label', () => {
  assert.equal(MIN_CONNECTOR, '3.1.0');
  assert.equal(connectorLabel({ connector: '3.1.0' }), 'Bağlayıcı 3.1.0');
  assert.equal(connectorLabel({ connector: '3.1.0<img src=x onerror=1>xxxxxxxx' }).length, 'Bağlayıcı '.length + 16);
  assert.equal(connectorLabel({}), '');
  assert.equal(connectorLabel(null), '');
  assert.equal(connectorLabel({ connector: 7 }), '');
});

test('outdated: different published build, or a pre-3.1.0 connector', () => {
  assert.equal(connectorOutdated({ connector: '3.1.0', pyz: A }, A), false);
  assert.equal(connectorOutdated({ connector: '3.1.0', pyz: A }, B), true);
  assert.equal(connectorOutdated({ connector: '3.1.0', pyz: A }, null), false);       // SHA256SUMS unavailable
  assert.equal(connectorOutdated({ connector: '3.1.0', pyz: null }, B), false);       // running from source
  assert.equal(connectorOutdated({ connector: '3.1.0', pyz: 'x' }, B), false);
  assert.equal(connectorOutdated({ connector: '3.0.0' }, null), true);                 // older connectors: no pyz
  assert.equal(connectorOutdated({ connector: '2.9.10' }, A), true);
  assert.equal(connectorOutdated({ connector: '3.10.0', pyz: A }, A), false);          // numeric, not string order
  assert.equal(connectorOutdated({ connector: 'weird' }, A), false);
  assert.equal(connectorOutdated(null, A), false);
  assert.ok(UPDATE_HINT.startsWith('Güncelleme var: bu bilgisayarda kurulum komutunu yeniden çalıştırın'));
});

test('3.5.0: update command or install command, by connector version', async () => {
  const { hasUpdateCommand, updateHint, UPDATE_HINT_CMD, UPDATE_CMD } = await import('../../web/js/devices.js');
  assert.equal(UPDATE_CMD, 'pixel-connector update');
  assert.equal(hasUpdateCommand({ connector: '3.5.0' }), true);
  assert.equal(hasUpdateCommand({ connector: '3.10.0' }), true);
  assert.equal(hasUpdateCommand({ connector: '4.0.0' }), true);
  assert.equal(hasUpdateCommand({ connector: '3.4.1' }), false);
  assert.equal(hasUpdateCommand({ connector: 'weird' }), false);
  assert.equal(hasUpdateCommand(null), false);
  assert.equal(updateHint({ connector: '3.5.0' }), UPDATE_HINT_CMD);
  assert.ok(UPDATE_HINT_CMD.includes('pixel-connector update'));
  assert.equal(updateHint({ connector: '3.4.1' }), UPDATE_HINT);
  // SHA256SUMS also lists the hook files since 3.5.0; only the .pyz line counts
  const sums = `${B}  ../hook/pixel_agent.py\n${B}  ../hook/pixel_hook.py\n${'1'.repeat(64)}  install.ps1\n${A}  pixel-connector.pyz\n`;
  assert.equal(parsePublishedSha(sums), A);
});
