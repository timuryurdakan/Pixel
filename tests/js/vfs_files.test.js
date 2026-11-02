// F4-4 (headless Chrome, tests/test_relay_js.py): files the user adds to the browser's virtual folder follow the
// model's write rules (text only, 512 KB, names, characters, folder caps) and never replace an existing file; the ZIP
// holds every file as stored, including ones over the model's 256 KB read cap; the panel offers the buttons.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MemoryStore } from '../../web/js/idb.js';
import { VirtualWorkspace, BrowserDevice, BrowserDevicePanel, createMemoryKeystore, importUserFile, workspaceZip,
  WRITE_MAX, IMPORT_TEXT } from '../../web/js/browser_device.js';

const file = (name, body) => new File([body], name, { type: 'text/plain' });

test('importUserFile: text files in, everything else refused with a reason', async () => {
  const ws = new VirtualWorkspace({ store: new MemoryStore() });
  assert.deepEqual(await importUserFile(ws, file('notlar.md', '# Not\nçğü')), { path: 'notlar.md', code: null });
  assert.equal(await ws.readFile('notlar.md'), '# Not\nçğü');
  const cases = [
    [file('notlar.md', 'başka'), 'exists'],
    [file('resim.png', new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0xff, 0xfe, 0x00])), 'binary'],
    [file('nul.txt', 'a\0b'), 'binary'],
    [file('büyük.txt', 'a'.repeat(WRITE_MAX + 1)), 'too_large'],
    [file('.env', 'KEY=x'), 'denied'],
    [file('id_rsa', 'x'), 'denied'],
    [file('kotu:ad.txt', 'x'), 'bad_path'],
    [file('gizli.txt', 'a‮b'), 'bad_content'],
  ];
  for (const [f, code] of cases) {
    const r = await importUserFile(ws, f);
    assert.equal(r.code, code, f.name);
    assert.ok(IMPORT_TEXT[code]);
  }
  assert.equal(await ws.readFile('notlar.md'), '# Not\nçğü');                // not replaced
  assert.deepEqual((await ws.listFiles()).map((f) => f.path), ['notlar.md']);
  const full = new VirtualWorkspace({ store: new MemoryStore(), maxFiles: 1 });
  await importUserFile(full, file('a.txt', 'x'));
  assert.equal((await importUserFile(full, file('b.txt', 'y'))).code, 'too_many_files');
});

test('workspaceZip: every stored file, names with folders, contents over the model read cap', async () => {
  const ws = new VirtualWorkspace({ store: new MemoryStore() });
  await ws.writeFile('site/index.html', '<p>Merhaba</p>');
  const big = 'b'.repeat(300 * 1024);                                       // over READ_MAX (256 KB)
  await ws.writeFile('büyük.txt', big);
  const zip = await workspaceZip(ws);
  const text = new TextDecoder('latin1').decode(zip);
  assert.equal(new DataView(zip.buffer).getUint32(0, true), 0x04034b50);
  assert.ok(text.includes('site/index.html'));
  const names = [];
  const view = new DataView(zip.buffer);
  const end = zip.length - 22;
  assert.equal(view.getUint32(end, true), 0x06054b50);
  let at = view.getUint32(end + 16, true);
  for (let i = 0; i < view.getUint16(end + 10, true); i += 1) {
    const n = view.getUint16(at + 28, true);
    names.push(new TextDecoder().decode(zip.slice(at + 46, at + 46 + n)));
    if (names.at(-1) === 'büyük.txt') assert.equal(view.getUint32(at + 24, true), big.length);
    at += 46 + n;
  }
  assert.deepEqual(names, ['büyük.txt', 'site/index.html']);
});

test('the panel offers ZIP download (only with files) and adding files', async () => {
  const dev = new BrowserDevice({ keystore: createMemoryKeystore(), store: new MemoryStore() });
  const box = document.createElement('div');
  const toasts = [];
  const panel = new BrowserDevicePanel({ container: box, device: dev, toast: (t, o) => toasts.push([t, o]) });
  await panel.render();
  assert.equal(box.querySelector('[data-action="download-vfs-zip"]').disabled, true);
  assert.ok(box.querySelector('[data-action="add-vfs-files"]'));
  assert.equal(box.querySelector('input[type=file]').multiple, true);
  await panel._import([file('a.md', 'x'), file('.env', 'y')]);
  assert.match(toasts.at(-1)[0], /1 dosya eklendi, 1 eklenmedi: \.env: bu dosya adına izin yok/);
  assert.equal(toasts.at(-1)[1].error, true);
  assert.equal(box.querySelector('[data-action="download-vfs-zip"]').disabled, false);
  assert.ok([...box.querySelectorAll('.vfs-path')].some((n) => n.textContent.startsWith('a.md')));
});
