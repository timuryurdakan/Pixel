// Pixel Office — interface language (F4-2, docs/ARASTIRMA2_ORTAK.md). Turkish is the source language: the Turkish
// text itself is the key (`t('Görevler')`), `i18n_en.js` maps it to English, and a missing translation shows the
// Turkish text (never a key, never a crash). Every result is plain text: callers write it with textContent, value or
// an attribute, never as HTML. `{name}` placeholders are filled with String(value), no markup.
//
// Choice: localStorage 'po-lang' (the selector), else the browser's language ("tr…" → Turkish, anything else →
// English). Changing it reloads the page, so every screen is drawn once in one language.

import { EN } from './i18n_en.js';

export const LANG_KEY = 'po-lang';
export const LANGS = Object.freeze(['tr', 'en']);

function detect() {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (LANGS.includes(saved)) return saved;
  } catch { /* private window: fall back to the browser's language */ }
  const nav = (typeof navigator !== 'undefined' && (navigator.language || '')) || '';
  return nav.toLowerCase().startsWith('tr') ? 'tr' : 'en';
}

let current = detect();
const missing = new Set();

export function lang() { return current; }

/** For tests only: switch without storing or reloading. */
export function setLangForTest(l) { current = LANGS.includes(l) ? l : 'tr'; }

function fill(text, vars) {
  if (!vars) return text;
  return text.replace(/\{([a-zA-Z0-9_]+)\}/g, (m, k) => (Object.hasOwn(vars, k) ? String(vars[k]) : m));
}

/** The text in the current language. `tr` is the Turkish source (also the key). */
export function t(tr, vars) {
  let out = tr;
  if (current === 'en') {
    if (Object.hasOwn(EN, tr)) out = EN[tr];
    else if (!missing.has(tr)) {
      missing.add(tr);
      console.warn('[pixel-office] missing English text:', tr);
    }
  }
  return fill(out, vars);
}

/** Store the choice and reload (every screen is rebuilt in the new language). */
export function setLang(l) {
  if (!LANGS.includes(l) || l === current) return;
  try { localStorage.setItem(LANG_KEY, l); } catch { /* private window: this page only */ }
  location.reload();
}

const ATTRS = ['title', 'aria-label', 'placeholder'];

/**
 * Static markup (index.html, rehber.html …), run once at start before scripts draw anything: every text node whose
 * trimmed text has an English entry is replaced (surrounding whitespace kept), and so are title / aria-label /
 * placeholder attributes. Exact matches only; script and style contents are never touched.
 */
export function applyStatic(root = document) {
  if (root.documentElement) root.documentElement.lang = current;
  if (current === 'tr') return;
  const doc = root.ownerDocument || root;
  const walker = doc.createTreeWalker(root.body || root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement && !n.parentElement.closest('script, style, code, pre')
      ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const n of nodes) {
    const src = n.nodeValue.trim();
    if (!src || !Object.hasOwn(EN, src)) continue;
    const lead = n.nodeValue.match(/^\s*/)[0];
    const tail = n.nodeValue.match(/\s*$/)[0];
    n.nodeValue = lead + EN[src] + tail;
  }
  for (const name of ATTRS) {
    for (const el of root.querySelectorAll(`[${name}]`)) {
      const key = `i18nSrc${name.replace(/(^|-)([a-z])/g, (m, d, c) => c.toUpperCase())}`;
      const src = el.dataset[key] ?? el.getAttribute(name);
      if (!Object.hasOwn(EN, src)) continue;
      el.dataset[key] = src;
      el.setAttribute(name, EN[src]);
    }
  }
}

/** The "TR | EN" selector: two buttons, the current one pressed. */
export function langSwitch(doc = document) {
  const box = doc.createElement('span');
  box.className = 'lang-switch';
  box.setAttribute('role', 'group');
  box.setAttribute('aria-label', t('Dil'));
  for (const l of LANGS) {
    const b = doc.createElement('button');
    b.type = 'button';
    b.className = 'btn small ghost';
    b.textContent = l.toUpperCase();
    b.setAttribute('aria-pressed', String(l === current));
    b.title = l === 'tr' ? 'Türkçe' : 'English';
    b.addEventListener('click', () => setLang(l));
    box.appendChild(b);
  }
  return box;
}
