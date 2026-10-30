// F4-2 (headless Chrome, tests/test_relay_js.py): the interface language. Turkish is the source text and the key;
// English comes from i18n_en.js; a missing entry falls back to Turkish; placeholders are filled as plain text;
// static markup is translated by exact text only, never script contents; the selector is two buttons.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { t, lang, setLangForTest, applyStatic, langSwitch, LANGS } from '../../web/js/i18n.js';
import { EN } from '../../web/js/i18n_en.js';

const XSS = '<img src=x onerror=window.__pwnedI18n=1>';

test('the test runner chooses Turkish (po-lang), so the other suites see the Turkish source texts', () => {
  assert.equal(lang(), 'tr');
  assert.deepEqual([...LANGS], ['tr', 'en']);
});

test('t: Turkish source by default, English from the table, Turkish when an entry is missing', () => {
  setLangForTest('tr');
  assert.equal(t('Görevler'), 'Görevler');
  setLangForTest('en');
  try {
    assert.equal(t('Görevler'), 'Tasks');
    assert.equal(t('Bu metnin çevirisi yok'), 'Bu metnin çevirisi yok');
  } finally { setLangForTest('tr'); }
});

test('placeholders are filled as plain text and unknown ones stay visible', () => {
  setLangForTest('en');
  try {
    assert.equal(t('{d} önce', { d: XSS }), `${XSS} ago`);
    assert.equal(t('{s} sn', {}), '{s}s');
    assert.equal(t('{d} önce', { constructor: 1 }), '{d} ago');
  } finally { setLangForTest('tr'); }
});

test('every English entry keeps the placeholders of its Turkish key and contains no markup', () => {
  for (const [tr, en] of Object.entries(EN)) {
    const ph = (s) => (s.match(/\{[a-zA-Z0-9_]+\}/g) || []).sort().join(',');
    assert.equal(ph(en), ph(tr), tr);
    assert.ok(!/<[a-z/!]/i.test(en), `markup in the English text of "${tr}"`);
    assert.equal(typeof en, 'string');
  }
});

test('applyStatic translates exact text nodes and attributes only, never scripts', () => {
  const doc = document.implementation.createHTMLDocument('x');
  doc.body.innerHTML = '<p>  Görevler  </p><p>Görevler ekle</p><button title="Davet et">Davet et</button>'
    + '<script>var x = "Görevler";</script><code>Görevler</code>';
  setLangForTest('en');
  try {
    applyStatic(doc);
    const ps = doc.querySelectorAll('p');
    assert.equal(ps[0].textContent, '  Tasks  ');
    assert.equal(ps[1].textContent, 'Görevler ekle');                 // not an exact match: left alone
    assert.equal(doc.querySelector('button').textContent, 'Invite');
    assert.equal(doc.querySelector('button').title, 'Invite');
    assert.ok(doc.querySelector('script').textContent.includes('"Görevler"'));
    assert.equal(doc.querySelector('code').textContent, 'Görevler');
    assert.equal(doc.documentElement.lang, 'en');
  } finally { setLangForTest('tr'); }
});

test('the selector is two plain buttons with the current language pressed', () => {
  const box = langSwitch(document);
  const bs = [...box.querySelectorAll('button')];
  assert.deepEqual(bs.map((b) => b.textContent), ['TR', 'EN']);
  assert.deepEqual(bs.map((b) => b.getAttribute('aria-pressed')), ['true', 'false']);
  for (const b of bs) for (const at of b.getAttributeNames()) assert.ok(!at.startsWith('on'), at);
});
