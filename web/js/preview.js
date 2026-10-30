// E2 (Faz 2): Sandboxed artifact preview drawer (docs/EVRENSEL_PLATFORM_ARASTIRMA_RAPORU.md §2.4).
//
// Renders Markdown and HTML artifacts strictly inside an `<iframe sandbox="">` (empty sandbox
// attribute — zero permissions, no scripts, no same-origin access, no forms, no popups) via
// `iframe.srcdoc`, reinforced with an inner Content-Security-Policy meta tag (`default-src 'none'`).
// Host page DOM elements are built exclusively with `document.createElement` and `textContent`.

import { utf8ByteLength, slugify } from './template_builder.js';
import { t as tt } from './i18n.js';

export const PREVIEW_MAX_BYTES = 262144; // 256 KB
export const PREVIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none';";

const _DECODER = new TextDecoder('utf-8', { fatal: false });
const _ENCODER = new TextEncoder();

export function escapeHtmlText(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function truncateToMaxBytes(text, maxBytes = PREVIEW_MAX_BYTES) {
  const s = String(text ?? '');
  const bytes = _ENCODER.encode(s);
  if (bytes.byteLength <= maxBytes) {
    return { text: s, truncated: false };
  }
  const sliced = _DECODER.decode(bytes.subarray(0, maxBytes));
  return { text: sliced, truncated: true };
}

export function detectArtifactKind(text, hint = '') {
  const h = String(hint || '').toLowerCase().trim();
  if (h === 'html' || h === 'htm') return 'html';
  if (h === 'markdown' || h === 'md') return 'markdown';
  const trimmed = String(text ?? '').trim();
  if (/^<!doctype\s+html\b/i.test(trimmed) || /^<html\b/i.test(trimmed)) {
    return 'html';
  }
  return 'markdown';
}

function formatInlineMarkdown(rawLine) {
  // Escape all HTML special characters FIRST so raw tags (<script>, <img onerror=...>) become inert text.
  let safe = escapeHtmlText(rawLine);

  // Extract inline code spans (`code`) into placeholders so bold/italic don't touch them
  const codeSpans = [];
  safe = safe.replace(/`([^`]+)`/g, (_, code) => {
    const idx = codeSpans.push(`<code>${code}</code>`) - 1;
    return `\u0000CODE_${idx}\u0000`;
  });

  // Safe links: [label](https://...) — only allow http:// or https://
  safe = safe.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => {
    return `<a href="${url}" rel="noopener noreferrer">${label}</a>`;
  });

  // Bold (**text**) and italic (*text*)
  safe = safe.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  safe = safe.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');

  // Restore inline code spans
  safe = safe.replace(/\u0000CODE_(\d+)\u0000/g, (_, i) => codeSpans[Number(i)] || '');
  return safe;
}

function isTableSeparator(line) {
  const trimmed = line.trim();
  if (!trimmed.includes('|')) return false;
  const cells = trimmed.replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  return cells.length >= 2 && cells.every((c) => /^:?-{3,}:?$/.test(c));
}

function splitTableRow(line) {
  return line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((c) => formatInlineMarkdown(c.trim()));
}

/**
 * Converts a Markdown string into a safe HTML body string for rendering inside `<iframe sandbox="" srcdoc>`.
 * Every text fragment is entity-escaped via `escapeHtmlText` before structural tags are added.
 */
export function markdownToSafeHtmlBody(md) {
  const norm = String(md ?? '').replace(/\r\n?/g, '\n');
  const lines = norm.split('\n');
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block ```
    if (/^\s*```/.test(line)) {
      const lang = line.trim().slice(3).trim().replace(/[^a-zA-Z0-9_-]/g, '');
      const codeLines = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        codeLines.push(escapeHtmlText(lines[i]));
        i += 1;
      }
      if (i < lines.length) i += 1; // skip closing ```
      const cls = lang ? ` class="lang-${lang}"` : '';
      out.push(`<pre><code${cls}>${codeLines.join('\n')}</code></pre>`);
      continue;
    }

    // Blank line
    if (!line.trim()) {
      i += 1;
      continue;
    }

    // Horizontal rule
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      out.push('<hr>');
      i += 1;
      continue;
    }

    // Heading #..######
    const hMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (hMatch) {
      const level = hMatch[1].length;
      out.push(`<h${level}>${formatInlineMarkdown(hMatch[2].trim())}</h${level}>`);
      i += 1;
      continue;
    }

    // Markdown table
    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headers = splitTableRow(line);
      i += 2; // skip header and separator
      const rows = [];
      while (i < lines.length && lines[i].trim().includes('|') && lines[i].trim() !== '') {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      const thead = `<thead><tr>${headers.map((h) => `<th>${h}</th>`).join('')}</tr></thead>`;
      const tbody = rows.length
        ? `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>`
        : '';
      out.push(`<table>${thead}${tbody}</table>`);
      continue;
    }

    // Blockquote
    if (/^\s*>\s?/.test(line)) {
      const qLines = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        qLines.push(formatInlineMarkdown(lines[i].replace(/^\s*>\s?/, '')));
        i += 1;
      }
      out.push(`<blockquote>${qLines.join('<br>')}</blockquote>`);
      continue;
    }

    // Unordered list
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(`<li>${formatInlineMarkdown(lines[i].replace(/^\s*[-*+]\s+/, ''))}</li>`);
        i += 1;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      continue;
    }

    // Ordered list
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(`<li>${formatInlineMarkdown(lines[i].replace(/^\s*\d+\.\s+/, ''))}</li>`);
        i += 1;
      }
      out.push(`<ol>${items.join('')}</ol>`);
      continue;
    }

    // Paragraph
    const pLines = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^\s*```/.test(lines[i]) &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !(lines[i].includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1]))
    ) {
      pLines.push(formatInlineMarkdown(lines[i]));
      i += 1;
    }
    out.push(`<p>${pLines.join('<br>')}</p>`);
  }

  return out.join('\n');
}

const SRCDOC_STYLE = `
  :root { color-scheme: dark; }
  body {
    margin: 0;
    padding: 16px 20px;
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    background: #141821;
    color: #e6edf3;
    word-wrap: break-word;
  }
  h1, h2, h3, h4, h5, h6 {
    color: #ffd166;
    margin: 1.1em 0 0.45em;
    line-height: 1.3;
  }
  h1 { font-size: 1.45rem; border-bottom: 1px solid #2b3245; padding-bottom: 6px; }
  h2 { font-size: 1.22rem; border-bottom: 1px solid #2b3245; padding-bottom: 4px; }
  h3 { font-size: 1.05rem; }
  p, ul, ol, blockquote, table, pre { margin: 0.65em 0; }
  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    background: #1f2633;
    color: #9cdcfe;
    padding: 2px 5px;
    border-radius: 3px;
    font-size: 0.9em;
  }
  pre {
    background: #0d1117;
    border: 1px solid #2b3245;
    padding: 10px 12px;
    overflow-x: auto;
    border-radius: 4px;
  }
  pre code { background: none; padding: 0; color: #e6edf3; }
  blockquote {
    border-left: 3px solid #ffd166;
    padding-left: 12px;
    color: #9aa5b4;
  }
  table {
    border-collapse: collapse;
    width: 100%;
  }
  th, td {
    border: 1px solid #2b3245;
    padding: 6px 10px;
    text-align: left;
  }
  th { background: #1f2633; color: #ffd166; }
  hr { border: none; border-top: 1px solid #2b3245; margin: 1em 0; }
  a { color: #58a6ff; text-decoration: underline; }
`;

export function buildPreviewSrcdoc(content, { kind = 'auto', title = tt('Önizleme') } = {}) {
  const raw = String(content ?? '');
  const empty = raw.trim().length === 0;
  const { text, truncated } = truncateToMaxBytes(raw, PREVIEW_MAX_BYTES);
  const resolvedKind = detectArtifactKind(text, kind === 'auto' ? '' : kind);
  const safeTitle = escapeHtmlText(title || tt('Önizleme'));
  const cspMeta = `<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}">`;

  if (resolvedKind === 'html') {
    // Wrap or prepend strict CSP meta inside the sandboxed srcdoc
    let srcdoc;
    if (/<head\b[^>]*>/i.test(text)) {
      srcdoc = text.replace(/<head\b[^>]*>/i, (m) => `${m}${cspMeta}`);
    } else {
      srcdoc = `<!doctype html><html><head>${cspMeta}<title>${safeTitle}</title></head><body>${text}</body></html>`;
    }
    return { srcdoc, kind: 'html', truncated, empty, text };
  }

  const bodyHtml = empty
    ? `<p class="empty-msg">${escapeHtmlText(tt('Önizlenecek içerik boş.'))}</p>`
    : markdownToSafeHtmlBody(text);
  const srcdoc = `<!doctype html><html><head>${cspMeta}<title>${safeTitle}</title><style>${SRCDOC_STYLE}</style></head><body>${bodyHtml}</body></html>`;
  return { srcdoc, kind: 'markdown', truncated, empty, text };
}

function el(doc, tag, cls, text) {
  const n = doc.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

export class PreviewDrawer {
  constructor({ dialog = null, doc = document } = {}) {
    this.doc = dialog?.ownerDocument || doc;
    this.dialog = dialog || this.doc.getElementById('dlg-preview');
    this._title = tt('Eser Önizleme');
    this._content = '';
    this._kind = 'markdown';
    this._mode = 'rendered'; // 'rendered' | 'source'
    this._built = false;

    this._els = {};
    if (this.dialog) {
      this._ensureStructure();
    }
  }

  _ensureStructure() {
    if (this._built || !this.dialog) return;
    const doc = this.doc;
    this.dialog.replaceChildren();

    const head = el(doc, 'form', 'dlg-head preview-head');
    head.method = 'dialog';

    const titleWrap = el(doc, 'div', 'preview-title-wrap');
    const titleEl = el(doc, 'h2', 'preview-title', this._title);
    titleEl.id = 'dlg-preview-title';
    titleEl.dataset.previewTitle = '1';
    const kindBadge = el(doc, 'span', 'wf-badge mono', 'MARKDOWN');
    kindBadge.dataset.previewKindBadge = '1';
    titleWrap.append(titleEl, kindBadge);
    head.appendChild(titleWrap);

    const closeBtn = el(doc, 'button', 'close', '✕');
    closeBtn.value = 'close';
    closeBtn.setAttribute('aria-label', 'Kapat');
    head.appendChild(closeBtn);

    const toolbar = el(doc, 'div', 'preview-toolbar');

    const modeGroup = el(doc, 'div', 'preview-btn-group');
    const renderedBtn = el(doc, 'button', 'btn small primary', tt('Önizleme'));
    renderedBtn.type = 'button';
    renderedBtn.dataset.previewAction = 'mode-rendered';
    renderedBtn.addEventListener('click', () => this.setMode('rendered'));

    const sourceBtn = el(doc, 'button', 'btn small', tt('Kaynak Kodu'));
    sourceBtn.type = 'button';
    sourceBtn.dataset.previewAction = 'mode-source';
    sourceBtn.addEventListener('click', () => this.setMode('source'));
    modeGroup.append(renderedBtn, sourceBtn);

    const kindGroup = el(doc, 'div', 'preview-btn-group');
    const mdKindBtn = el(doc, 'button', 'btn small', 'Markdown');
    mdKindBtn.type = 'button';
    mdKindBtn.dataset.previewAction = 'kind-markdown';
    mdKindBtn.addEventListener('click', () => this.setKind('markdown'));

    const htmlKindBtn = el(doc, 'button', 'btn small', 'HTML');
    htmlKindBtn.type = 'button';
    htmlKindBtn.dataset.previewAction = 'kind-html';
    htmlKindBtn.addEventListener('click', () => this.setKind('html'));
    kindGroup.append(mdKindBtn, htmlKindBtn);

    const exportGroup = el(doc, 'div', 'preview-btn-group');
    const copyBtn = el(doc, 'button', 'btn small', 'Kopyala');
    copyBtn.type = 'button';
    copyBtn.dataset.previewAction = 'copy';
    copyBtn.addEventListener('click', () => this.copySource());

    const dlMdBtn = el(doc, 'button', 'btn small', tt('.md İndir'));
    dlMdBtn.type = 'button';
    dlMdBtn.dataset.previewAction = 'dl-md';
    dlMdBtn.addEventListener('click', () => this.downloadAs('md'));

    const dlHtmlBtn = el(doc, 'button', 'btn small', tt('.html İndir'));
    dlHtmlBtn.type = 'button';
    dlHtmlBtn.dataset.previewAction = 'dl-html';
    dlHtmlBtn.addEventListener('click', () => this.downloadAs('html'));
    exportGroup.append(copyBtn, dlMdBtn, dlHtmlBtn);

    toolbar.append(modeGroup, kindGroup, exportGroup);

    const noticeEl = el(doc, 'p', 'note small preview-notice', '');
    noticeEl.hidden = true;
    noticeEl.dataset.previewNotice = '1';

    const body = el(doc, 'div', 'preview-body');
    const iframe = el(doc, 'iframe', 'preview-frame');
    // Mandatory Rule 2: sandbox attribute MUST be empty string (zero permissions)
    iframe.setAttribute('sandbox', '');
    iframe.setAttribute('referrerpolicy', 'no-referrer');
    iframe.setAttribute('title', tt('Güvenli Eser Önizleme'));
    iframe.srcdoc = '';

    const sourcePre = el(doc, 'pre', 'preview-source mono', '');
    sourcePre.hidden = true;

    body.append(iframe, sourcePre);
    this.dialog.append(head, toolbar, noticeEl, body);

    this._els = {
      titleEl,
      kindBadge,
      renderedBtn,
      sourceBtn,
      mdKindBtn,
      htmlKindBtn,
      copyBtn,
      noticeEl,
      iframe,
      sourcePre,
    };
    this._built = true;
  }

  open({ title = tt('Eser Önizleme'), content = '', kind = 'auto' } = {}) {
    if (!this.dialog) return;
    this._ensureStructure();
    this._title = String(title || tt('Eser Önizleme'));
    this._content = String(content ?? '');
    this._kind = detectArtifactKind(this._content, kind === 'auto' ? '' : kind);
    this._mode = 'rendered';
    this._updateView();
    if (!this.dialog.open) {
      if (this.dialog.showModal) {
        try {
          this.dialog.showModal();
        } catch {
          this.dialog.setAttribute('open', '');
        }
      } else {
        this.dialog.setAttribute('open', '');
      }
    }
  }

  close() {
    if (!this.dialog) return;
    if (this.dialog.close) {
      try {
        this.dialog.close();
      } catch {
        this.dialog.removeAttribute('open');
      }
    } else {
      this.dialog.removeAttribute('open');
    }
  }

  setMode(mode) {
    this._mode = mode === 'source' ? 'source' : 'rendered';
    this._updateView();
  }

  setKind(kind) {
    this._kind = kind === 'html' ? 'html' : 'markdown';
    this._updateView();
  }

  _updateView() {
    if (!this._built) return;
    const {
      titleEl, kindBadge, renderedBtn, sourceBtn, mdKindBtn, htmlKindBtn, noticeEl, iframe, sourcePre,
    } = this._els;

    titleEl.textContent = this._title;
    kindBadge.textContent = this._kind.toUpperCase();

    renderedBtn.classList.toggle('primary', this._mode === 'rendered');
    sourceBtn.classList.toggle('primary', this._mode === 'source');
    mdKindBtn.classList.toggle('primary', this._kind === 'markdown');
    htmlKindBtn.classList.toggle('primary', this._kind === 'html');

    // Always enforce empty sandbox attribute before setting srcdoc
    iframe.setAttribute('sandbox', '');
    const res = buildPreviewSrcdoc(this._content, { kind: this._kind, title: this._title });
    iframe.srcdoc = res.srcdoc;
    sourcePre.textContent = res.text;

    if (res.empty) {
      noticeEl.hidden = false;
      noticeEl.textContent = tt('Bu adımda henüz önizlenecek çıktı metni yok.');
    } else if (res.truncated) {
      noticeEl.hidden = false;
      noticeEl.textContent = tt('Çıktı güvenlik sınırını ({kb} KB) aştığı için önizlemede kısaltıldı.', { kb: Math.round(PREVIEW_MAX_BYTES / 1024) });
    } else {
      noticeEl.hidden = true;
      noticeEl.textContent = '';
    }

    iframe.hidden = this._mode !== 'rendered';
    sourcePre.hidden = this._mode !== 'source';
  }

  copySource() {
    const text = String(this._content ?? '');
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).catch(() => {});
    }
    if (this._els.copyBtn) {
      this._els.copyBtn.textContent = tt('Kopyalandı');
      setTimeout(() => {
        if (this._els.copyBtn) this._els.copyBtn.textContent = 'Kopyala';
      }, 1500);
    }
  }

  downloadAs(ext = 'md') {
    const cleanSlug = slugify(this._title || 'eser');
    const isHtml = ext === 'html';
    const payload = isHtml
      ? buildPreviewSrcdoc(this._content, { kind: this._kind, title: this._title }).srcdoc
      : String(this._content ?? '');
    const mime = isHtml ? 'text/html;charset=utf-8' : 'text/markdown;charset=utf-8';
    const filename = `${cleanSlug}.${isHtml ? 'html' : 'md'}`;

    if (typeof Blob === 'undefined' || typeof URL === 'undefined' || !URL.createObjectURL) return;
    const blob = new Blob([payload], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = this.doc.createElement('a');
    a.href = url;
    a.download = filename;
    this.doc.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
