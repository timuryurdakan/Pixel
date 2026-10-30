// E2 (Faz 2): No-code workflow template builder (web/js/template_builder.js) +
// sandboxed artifact preview drawer (web/js/preview.js) + workflows.js integration.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  SLUG_RE, MODEL_RE, RESERVED_TEMPLATE_IDS, MAX_STEPS,
  NAME_MAX, DESCRIPTION_MAX, ROLE_MAX, TITLE_MAX, PROMPT_MAX, FOLDER_MAX,
  hasHiddenChar, utf8ByteLength, slugify,
  validateFolder, validateGuardrails, validateStep, validateTemplate,
  toPublicTemplate, buildWorkflowsJson, TemplateBuilder,
} from '../../web/js/template_builder.js';

import {
  PREVIEW_MAX_BYTES, PREVIEW_CSP,
  escapeHtmlText, detectArtifactKind, markdownToSafeHtmlBody,
  buildPreviewSrcdoc, PreviewDrawer,
} from '../../web/js/preview.js';

import { WorkflowStore, WorkflowDialog } from '../../web/js/workflows.js';

function makeDoc() {
  const doc = document.implementation.createHTMLDocument('e2-test');
  const wrap = doc.createElement('div');
  const dlg = doc.createElement('dialog');
  dlg.id = 'dlg-workflows';
  const body = doc.createElement('div');
  body.id = 'workflows-body';
  dlg.appendChild(body);
  const badge = doc.createElement('span');
  badge.id = 'workflows-badge';
  badge.hidden = true;
  const previewDlg = doc.createElement('dialog');
  previewDlg.id = 'dlg-preview';
  wrap.append(dlg, badge, previewDlg);
  doc.body.appendChild(wrap);
  for (const d of [dlg, previewDlg]) {
    if (!d.showModal) d.showModal = () => { d.setAttribute('open', ''); };
    if (!d.close) d.close = () => { d.removeAttribute('open'); };
  }
  return { doc, dlg, body, badge, previewDlg };
}

function makeFakeStorage() {
  const map = new Map();
  return {
    getItem(k) { return map.has(k) ? map.get(k) : null; },
    setItem(k, v) { map.set(k, String(v)); },
    removeItem(k) { map.delete(k); },
  };
}

function validSampleTemplate(overrides = {}) {
  return {
    id: 'blog-bulten',
    name: 'Blog ve Bülten Hattı',
    description: 'Araştırır, taslak yazar, onay alır ve son metni üretir.',
    folder: 'icerik/bulten',
    guardrails: { maxCostUsd: 2.5, maxTurns: 40, timeoutS: 1200 },
    steps: [
      {
        id: 'arastirma',
        type: 'agent',
        role: 'Araştırmacı',
        engine: 'claude',
        engines: ['claude', 'ollama'],
        profile: 'readonly',
        model: null,
        prompt: 'Konuyu araştır ve maddeler halinde özetle.',
        usePrevious: false,
        useBrief: true,
      },
      {
        id: 'onay-kapisi',
        type: 'approval',
        title: 'Taslak planı uygun mu?',
        timeoutS: 600,
      },
      {
        id: 'yazar',
        type: 'agent',
        role: 'Yazar',
        engine: 'claude',
        engines: ['claude'],
        profile: 'edit',
        model: 'claude-sonnet-4-6',
        prompt: 'Onaylanan taslağı tam makaleye dönüştür.',
        usePrevious: true,
        useBrief: true,
      },
    ],
    ...overrides,
  };
}

test('E2 — accepts a valid multi-step template and normalizes fields', () => {
    const res = validateTemplate(validSampleTemplate());
    assert.equal(res.ok, true, res.message);
    assert.equal(res.value.id, 'blog-bulten');
    assert.equal(res.value.name, 'Blog ve Bülten Hattı');
    assert.equal(res.value.folder, 'icerik/bulten');
    assert.deepEqual(res.value.guardrails, { maxCostUsd: 2.5, maxTurns: 40, timeoutS: 1200 });
    assert.equal(res.value.steps.length, 3);
    assert.equal(res.value.steps[0].type, 'agent');
    assert.equal(res.value.steps[1].type, 'approval');
    assert.equal(res.value.steps[2].usePrevious, true);
});

test('E2 — slugifies Turkish titles into connector-compatible SLUG_RE ids', () => {
    assert.equal(slugify('Şablon Oluşturucu & Önizleme!'), 'sablon-olusturucu-onizleme');
    assert.equal(slugify('  Çeviri / İnceleme Hattı 2026  '), 'ceviri-inceleme-hatti-2026');
    assert.ok(SLUG_RE.test(slugify('Çok Uzun Bir Türkçe Başlık Denemesi Burada Kesilmeli 123456')));
    assert.equal(slugify('Çok Uzun Bir Türkçe Başlık Denemesi Burada Kesilmeli 123456').length <= 32, true);
});

test('E2 — strips prompt from public template summary (toPublicTemplate)', () => {
    const res = validateTemplate(validSampleTemplate());
    assert.equal(res.ok, true);
    const pub = toPublicTemplate(res.value);
    assert.equal(pub.builtIn, false);
    assert.equal(pub.custom, true);
    assert.equal('prompt' in pub.steps[0], false);
    assert.equal('prompt' in pub.steps[2], false);
    assert.equal(pub.steps[1].title, 'Taslak planı uygun mu?');
});

test('E2 — builds workflows.json payload with v:1 and rejects duplicate template ids', () => {
    const t1 = validSampleTemplate({ id: 'hat-bir' });
    const t2 = validSampleTemplate({ id: 'hat-iki', name: 'İkinci Hat' });
    const built = buildWorkflowsJson([t1, t2]);
    assert.equal(built.ok, true);
    assert.equal(built.data.v, 1);
    assert.equal(built.data.templates.length, 2);
    const parsed = JSON.parse(built.json);
    assert.equal(parsed.v, 1);
    assert.equal(parsed.templates[0].id, 'hat-bir');

    const dup = buildWorkflowsJson([t1, validSampleTemplate({ id: 'hat-bir' })]);
    assert.equal(dup.ok, false);
    assert.equal(dup.code, 'bad_template');
    assert.match(dup.message, /duplicate/i);
});

test('E2 — rejects unknown fields, reserved ids, invalid slugs, and empty strings', () => {
    // Unknown top-level field
    const unknownTop = validateTemplate({ ...validSampleTemplate(), cmd: 'rm -rf /' });
    assert.equal(unknownTop.ok, false);
    assert.match(unknownTop.message, /unknown field/i);

    // Reserved built-in IDs
    for (const reserved of RESERVED_TEMPLATE_IDS) {
      const r = validateTemplate(validSampleTemplate({ id: reserved }));
      assert.equal(r.ok, false);
      assert.match(r.message, /reserved|duplicate/i);
    }

    // Invalid slugs
    for (const badId of ['', 'BüyükHarf', '-tire', 'a'.repeat(33), '../kacis', 'boşluk var']) {
      const r = validateTemplate(validSampleTemplate({ id: badId }));
      assert.equal(r.ok, false, `expected ${badId} to fail`);
    }

    // Empty name / role / prompt / approval title
    assert.equal(validateTemplate(validSampleTemplate({ name: '   ' })).ok, false);
    const emptyRole = validSampleTemplate();
    emptyRole.steps[0].role = '   ';
    assert.equal(validateTemplate(emptyRole).ok, false);

    const emptyPrompt = validSampleTemplate();
    emptyPrompt.steps[0].prompt = '  \n\t ';
    assert.equal(validateTemplate(emptyPrompt).ok, false);

    const emptyTitle = validSampleTemplate();
    emptyTitle.steps[1].title = '';
    assert.equal(validateTemplate(emptyTitle).ok, false);
});

test('E2 — rejects oversized fields including multi-byte UTF-8 prompt > 4096 bytes', () => {
    assert.equal(validateTemplate(validSampleTemplate({ name: 'a'.repeat(NAME_MAX + 1) })).ok, false);
    assert.equal(validateTemplate(validSampleTemplate({ description: 'a'.repeat(DESCRIPTION_MAX + 1) })).ok, false);

    const longRole = validSampleTemplate();
    longRole.steps[0].role = 'r'.repeat(ROLE_MAX + 1);
    assert.equal(validateTemplate(longRole).ok, false);

    const longTitle = validSampleTemplate();
    longTitle.steps[1].title = 't'.repeat(TITLE_MAX + 1);
    assert.equal(validateTemplate(longTitle).ok, false);

    // 2049 'ş' characters = 2049 chars (<= 4096 chars) but 4098 UTF-8 bytes (> 4096 bytes)!
    const multibytePrompt = validSampleTemplate();
    multibytePrompt.steps[0].prompt = 'ş'.repeat(2049);
    assert.equal(utf8ByteLength(multibytePrompt.steps[0].prompt), 4098);
    const res = validateTemplate(multibytePrompt);
    assert.equal(res.ok, false);
    assert.match(res.message, /prompt/i);
});

test('E2 — rejects hidden/control/bidi Unicode characters (Trojan Source defense)', () => {
    assert.equal(hasHiddenChar('Normal Türkçe metin 123'), false);
    assert.equal(hasHiddenChar('Satır\nsonu\tizinli', true), false);
    assert.equal(hasHiddenChar('Satır\nsonu yasak', false), true);
    assert.equal(hasHiddenChar('Bidi \u202e gizli', true), true);
    assert.equal(hasHiddenChar('Zero width \u200b boşluk', true), true);
    assert.equal(hasHiddenChar('Null \x00 byte', true), true);
    assert.equal(hasHiddenChar('Line sep \u2028 char', true), true);

    const bidiName = validSampleTemplate({ name: 'Normal\u202eAd' });
    assert.equal(validateTemplate(bidiName).ok, false);

    const zwspPrompt = validSampleTemplate();
    zwspPrompt.steps[0].prompt = 'İstem içinde \u200b görünmez karakter';
    assert.equal(validateTemplate(zwspPrompt).ok, false);
});

test('E2 — rejects path traversal in folder and out-of-range guardrails', () => {
    for (const badFolder of ['../etc', 'a/../../b', '/mutlak/yol', '\\windows\\yol', 'C:/klasor', 'a\x00b', 'a'.repeat(FOLDER_MAX + 1)]) {
      assert.equal(validateFolder(badFolder).ok, false, `folder ${badFolder} must fail`);
    }
    const cleanF = validateFolder('  alt\\klasor/icerik/ ');
    assert.equal(cleanF.ok, true);
    assert.equal(cleanF.value, 'alt/klasor/icerik');

    assert.equal(validateGuardrails({ maxCostUsd: 0 }).ok, false);
    assert.equal(validateGuardrails({ maxCostUsd: 20.01 }).ok, false);
    assert.equal(validateGuardrails({ maxTurns: 0 }).ok, false);
    assert.equal(validateGuardrails({ maxTurns: 501 }).ok, false);
    assert.equal(validateGuardrails({ maxTurns: true }).ok, false);
    assert.equal(validateGuardrails({ timeoutS: 59 }).ok, false);
    assert.equal(validateGuardrails({ timeoutS: 14401 }).ok, false);
    assert.equal(validateGuardrails({ extra: 1 }).ok, false);
});

test('E2 — enforces step ordering, count (1..8), agent presence, profile=auto gate, and model regex', () => {
    // 0 steps
    assert.equal(validateTemplate(validSampleTemplate({ steps: [] })).ok, false);

    // > 8 steps
    const nineSteps = Array.from({ length: MAX_STEPS + 1 }, (_, i) => ({
      id: `adim-${i + 1}`,
      type: 'agent',
      role: 'Rol',
      engine: 'claude',
      engines: ['claude'],
      profile: 'readonly',
      model: null,
      prompt: 'İstem',
      usePrevious: i > 0,
      useBrief: true,
    }));
    assert.equal(validateTemplate(validSampleTemplate({ steps: nineSteps })).ok, false);

    // Approval-only (no agent step)
    const approvalOnly = validSampleTemplate({
      steps: [{ id: 'onay', type: 'approval', title: 'Sadece onay', timeoutS: 300 }],
    });
    const noAgentRes = validateTemplate(approvalOnly);
    assert.equal(noAgentRes.ok, false);
    assert.match(noAgentRes.message, /no agent step/i);

    // usePrevious: true on first agent step
    const badPrev = validSampleTemplate();
    badPrev.steps[0].usePrevious = true;
    const prevRes = validateTemplate(badPrev);
    assert.equal(prevRes.ok, false);
    assert.match(prevRes.message, /usePrevious/i);

    // profile: 'auto' disabled by default, allowed when autoEnabled: true
    const autoStep = validSampleTemplate();
    autoStep.steps[0].profile = 'auto';
    assert.equal(validateTemplate(autoStep, { autoEnabled: false }).ok, false);
    assert.equal(validateTemplate(autoStep, { autoEnabled: true }).ok, true);

    // Invalid model name
    const badModel = validSampleTemplate();
    badModel.steps[0].model = '-bad-model;rm';
    assert.equal(validateTemplate(badModel).ok, false);
    assert.equal(MODEL_RE.test('qwen2.5-coder:7b'), true);
});

test('E2 — TemplateBuilder UI creates, edits, deletes custom templates and renders XSS payloads safely via textContent', () => {
    const { doc } = makeDoc();
    const host = doc.createElement('div');
    doc.body.appendChild(host);
    const storage = makeFakeStorage();
    let reloadCount = 0;

    const builder = new TemplateBuilder({
      container: host,
      storage,
      onReload: () => { reloadCount += 1; },
    });
    builder.render();

    // Fill form with an XSS attempt in name, description, and step role
    const nameInput = host.querySelector('[data-tb-field="name"]');
    const idInput = host.querySelector('[data-tb-field="id"]');
    const descInput = host.querySelector('[data-tb-field="description"]');
    assert.ok(nameInput && idInput && descInput);

    nameInput.value = '<img src=x onerror="window.__pwnedBuilder=1"> Güvenli Hat';
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    // Auto-slug generated a valid slug
    assert.ok(SLUG_RE.test(idInput.value), `slug was: ${idInput.value}`);

    descInput.value = '<script>window.__pwnedBuilder=2</script>Açıklama';
    descInput.dispatchEvent(new Event('input', { bubbles: true }));

    // Add an approval step as second step
    const addApprovalBtn = host.querySelector('[data-tb-action="add-approval"]');
    assert.ok(addApprovalBtn);
    addApprovalBtn.click();

    // Fill step 1 and step 2
    const roleInput = host.querySelector('[data-tb-step="0"] [data-tb-step-field="role"]');
    const promptInput = host.querySelector('[data-tb-step="0"] [data-tb-step-field="prompt"]');
    const titleInput = host.querySelector('[data-tb-step="1"] [data-tb-step-field="title"]');
    roleInput.value = '<b>Analist</b>';
    roleInput.dispatchEvent(new Event('input', { bubbles: true }));
    promptInput.value = 'Önce güvenlik analizi yap.';
    promptInput.dispatchEvent(new Event('input', { bubbles: true }));
    titleInput.value = '<svg onload="window.__pwnedBuilder=3">Onaylıyor musunuz?';
    titleInput.dispatchEvent(new Event('input', { bubbles: true }));

    // Save template
    const saveBtn = host.querySelector('[data-tb-action="save"]');
    saveBtn.click();

    assert.equal(window.__pwnedBuilder, undefined);
    assert.equal(host.querySelectorAll('img, script, svg').length, 0);
    assert.equal(builder.listTemplates().length, 1);
    assert.equal(builder.listTemplates()[0].steps.length, 2);

    // Saved template card displays literal XSS text safely
    const savedList = host.querySelector('[data-tb-saved-list]');
    assert.ok(savedList.textContent.includes('<img src=x onerror="window.__pwnedBuilder=1">'));

    // Reload button triggers connector workflow.list callback
    const reloadBtn = host.querySelector('[data-tb-action="reload-device"]');
    reloadBtn.click();
    assert.equal(reloadCount, 1);

    // Delete template
    const delBtn = host.querySelector('[data-tb-action="delete-saved"]');
    assert.ok(delBtn);
    delBtn.click();
    assert.equal(builder.listTemplates().length, 0);
});

test('E2 — PreviewDrawer renders Markdown and HTML only inside <iframe sandbox=""> with srcdoc and never executes scripts', () => {
    const { doc, previewDlg } = makeDoc();
    const drawer = new PreviewDrawer({ dialog: previewDlg, doc });

    const xssMarkdown = [
      '# Eser Başlığı',
      '',
      'Merhaba **dünya** ve `kod`!',
      '',
      '<script>window.__pwnedPreview = 1;</script>',
      '<img src="x" onerror="window.__pwnedPreview = 2">',
      '',
      '| Sütun A | Sütun B |',
      '| --- | --- |',
      '| Değer 1 | <script>alert(1)</script> |',
      '',
      '```js',
      'const x = "<script>window.__pwnedPreview = 3</script>";',
      '```',
    ].join('\n');

    drawer.open({
      title: '<img src=x onerror="window.__pwnedPreview=4"> Rapor.md',
      content: xssMarkdown,
      kind: 'markdown',
    });

    assert.equal(previewDlg.hasAttribute('open'), true);
    assert.equal(window.__pwnedPreview, undefined);

    // Host page never has injected <script> or <img> elements
    assert.equal(previewDlg.querySelectorAll('script, img').length, 0);

    // Title is rendered via textContent
    const titleEl = previewDlg.querySelector('[data-preview-title]');
    assert.equal(titleEl.textContent, '<img src=x onerror="window.__pwnedPreview=4"> Rapor.md');

    // Iframe MUST have sandbox="" (empty string, zero permissions)
    const iframe = previewDlg.querySelector('iframe.preview-frame');
    assert.ok(iframe, 'preview iframe must exist');
    assert.equal(iframe.hasAttribute('sandbox'), true);
    assert.equal(iframe.getAttribute('sandbox'), '');
    assert.equal(typeof iframe.srcdoc, 'string');
    assert.ok(iframe.srcdoc.includes("default-src 'none'"));

    // Raw HTML tags inside Markdown must be entity-escaped inside srcdoc
    assert.ok(iframe.srcdoc.includes('&lt;script&gt;window.__pwnedPreview = 1;&lt;/script&gt;'));
    assert.ok(!iframe.srcdoc.includes('<script>window.__pwnedPreview'));
    assert.ok(iframe.srcdoc.includes('<h1>Eser Başlığı</h1>'));
    assert.ok(iframe.srcdoc.includes('<strong>dünya</strong>'));
    assert.ok(iframe.srcdoc.includes('<table>'));

    // Switch to source view: raw text is shown via textContent in <pre>
    drawer.setMode('source');
    const sourcePre = previewDlg.querySelector('.preview-source');
    assert.equal(sourcePre.hidden, false);
    assert.equal(iframe.hidden, true);
    assert.equal(sourcePre.textContent, xssMarkdown);

    // Switch to HTML kind with raw HTML XSS payload
    drawer.setMode('rendered');
    const rawHtml = '<!doctype html><html><body><h1>Merhaba</h1><script>window.__pwnedPreview=99</script></body></html>';
    drawer.open({ title: 'index.html', content: rawHtml, kind: 'html' });
    assert.equal(iframe.getAttribute('sandbox'), '');
    assert.ok(iframe.srcdoc.includes("default-src 'none'"));
    assert.equal(window.__pwnedPreview, undefined);
});

test('E2 — PreviewDrawer handles empty and oversized (> 256 KB) inputs gracefully without crashing', () => {
    const { doc, previewDlg } = makeDoc();
    const drawer = new PreviewDrawer({ dialog: previewDlg, doc });

    // Empty input
    drawer.open({ title: 'Boş Çıktı', content: '   ', kind: 'markdown' });
    const notice = previewDlg.querySelector('[data-preview-notice]');
    assert.equal(notice.hidden, false);
    assert.ok(notice.textContent.length > 0);

    // Oversized input (> PREVIEW_MAX_BYTES)
    const huge = '# Büyük Belge\n' + 'A'.repeat(PREVIEW_MAX_BYTES + 5000);
    const built = buildPreviewSrcdoc(huge, { kind: 'markdown' });
    assert.equal(built.truncated, true);
    assert.equal(utf8ByteLength(built.text) <= PREVIEW_MAX_BYTES, true);

    drawer.open({ title: 'Büyük Çıktı', content: huge, kind: 'markdown' });
    assert.equal(notice.hidden, false);
    assert.match(notice.textContent, /kısaltıldı|sınır/i);
});

test('E2 — integrates with WorkflowDialog to open template builder and preview step output', async () => {
    const html = await (await fetch('/web/index.html')).text();
    const parsedDoc = new DOMParser().parseFromString(html, 'text/html');
    const dlg = document.importNode(parsedDoc.getElementById('dlg-workflows'), true);
    const previewDlg = document.createElement('dialog');
    previewDlg.id = 'dlg-preview';
    document.body.append(dlg, previewDlg);

    const sampleRun = {
      id: 'w_1234567890',
      template: 'statik-site',
      name: 'Statik Site Ekibi',
      workspace: { id: 'isler', name: 'Pixel Office İşleri', sub: 'site' },
      state: 'done',
      code: null,
      current: null,
      costUsd: 0.12,
      turns: 3,
      limits: { maxCostUsd: 1.5, maxTurns: 30, timeoutS: 900 },
      startedAt: 1,
      endedAt: 2,
      steps: [{
        id: 'tasarim',
        type: 'agent',
        role: 'Tasarımcı',
        engine: 'claude',
        model: 'haiku',
        agent: 'k1',
        task: 't_1234567890',
        state: 'done',
      }],
    };

    const dev = {
      id: 'd1',
      label: 'Masaüstü',
      ready: true,
      status: 'ready',
      info: {
        features: ['workflows'],
        remote: {},
        limits: { maxPrompt: 32768 },
        engines: { claude: { available: true }, ollama: { available: false, models: [] } },
      },
      agents: new Map(),
    };
    const listeners = new Map();
    const hub = {
      dev,
      devices: new Map([[dev.id, dev]]),
      on(t, fn) { if (!listeners.has(t)) listeners.set(t, []); listeners.get(t).push(fn); },
      emit(t, ...a) { for (const fn of listeners.get(t) || []) fn(...a); },
      isPaired: () => true,
      isReady: () => true,
      device: (id) => (id === dev.id ? dev : null),
      async request(id, msg) {
        if (msg.type === 'workflow.list') {
          return {
            type: 'workflow.list.result',
            templates: [{
              id: 'statik-site',
              name: 'Statik Site Ekibi',
              description: 'Demo',
              builtin: true,
              folder: 'site',
              limits: { maxCostUsd: 1.5, maxTurns: 30, timeoutS: 900 },
              needsBrief: true,
              steps: [{ id: 'tasarim', type: 'agent', role: 'Tasarımcı', engine: 'claude', engines: ['claude'], profile: 'edit', model: null }],
            }],
            invalid: 0,
            runs: [sampleRun],
          };
        }
        if (msg.type === 'workspace.list') {
          return { workspaces: [{ id: 'isler', name: 'İşler', children: [] }] };
        }
        if (msg.type === 'task.get' && msg.task === 't_1234567890') {
          return {
            task: 't_1234567890',
            status: 'success',
            prompt: 'Tasarım yap',
            result: '# Tasarım Özeti\n\nRenk paleti ve yerleşim hazır.\n\n<script>window.__pwnedWf=1</script>',
            truncated: false,
          };
        }
        return {};
      },
    };

    const store = new WorkflowStore({ hub });
    const wfDlg = new WorkflowDialog({
      dialog: dlg,
      hub,
      store,
      toast() {},
      previewDialog: previewDlg,
      storage: makeFakeStorage(),
    });

    wfDlg.open();
    // Wait for workflow.list to load
    await new Promise((r) => setTimeout(r, 30));

    // Toggle template builder inside workflows dialog
    const toggleBuilderBtn = dlg.querySelector('[data-action="toggle-builder"]');
    assert.ok(toggleBuilderBtn, 'toggle-builder button should exist');
    toggleBuilderBtn.click();
    const builderEl = dlg.querySelector('[data-tb-root]');
    assert.ok(builderEl && ! dlg.querySelector('#wf-builder').hidden, 'builder should be visible after toggle');

    // Click preview artifact button on finished step (fetches task.get result)
    const previewBtn = dlg.querySelector('[data-action="preview-artifact"]');
    assert.ok(previewBtn, 'preview-artifact button should exist on finished step');
    previewBtn.click();
    await new Promise((r) => setTimeout(r, 30));

    assert.equal(previewDlg.hasAttribute('open'), true);
    const iframe = previewDlg.querySelector('iframe.preview-frame');
    assert.equal(iframe.getAttribute('sandbox'), '');
    assert.ok(iframe.srcdoc.includes('<h1>Tasarım Özeti</h1>'));
    assert.ok(iframe.srcdoc.includes('&lt;script&gt;window.__pwnedWf=1&lt;/script&gt;'));
    assert.equal(window.__pwnedWf, undefined);

    // Ensure no inline on* handlers or <script> tags exist anywhere in dlg or previewDlg
    for (const root of [dlg, previewDlg]) {
      for (const el of root.querySelectorAll('*')) {
        for (const attr of el.getAttributeNames()) {
          assert.ok(!attr.toLowerCase().startsWith('on'), `inline handler ${attr}`);
        }
        assert.notEqual(el.tagName, 'SCRIPT');
      }
    }

    previewDlg.close();
    dlg.close();
    previewDlg.remove();
    dlg.remove();
});
