// E2 (Faz 2): No-code workflow template builder (docs/EVRENSEL_PLATFORM_ARASTIRMA_RAPORU.md §2.2).
//
// Validates custom workflow templates against the exact allow-list and limits of
// pixel_connector/workflows.py (`validate_template`), stores drafts locally in the browser,
// exports/copies/downloads `workflows.json` (`{"v": 1, "templates": [...]}`) for
// `~/.pixel-office/connector/workflows.json`, and reloads connector templates via the
// existing `workflow.list` RPC.
//
// Security: every DOM node is constructed with document.createElement and populated via
// textContent / value. Never uses innerHTML, insertAdjacentHTML or document.write.

import { t as tt } from './i18n.js';

export const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;
export const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$/;
export const RESERVED_TEMPLATE_IDS = Object.freeze(['statik-site', 'patron']);
export const ENGINES = Object.freeze(['claude', 'ollama']);
export const PROFILES = Object.freeze(['readonly', 'edit', 'dev', 'auto']);
export const STEP_TYPES = Object.freeze(['agent', 'approval']);

export const MAX_STEPS = 8;
export const NAME_MAX = 60;
export const DESCRIPTION_MAX = 300;
export const ROLE_MAX = 24;
export const TITLE_MAX = 80;
export const PROMPT_MAX = 4096;
export const FOLDER_MAX = 200;

export const CUSTOM_TEMPLATES_KEY = 'po-custom-templates';
export const DEFAULT_GUARDRAILS = Object.freeze({
  maxCostUsd: 1.5,
  maxTurns: 30,
  timeoutS: 900,
});

const TEMPLATE_FIELDS = new Set(['id', 'name', 'description', 'folder', 'guardrails', 'steps']);
const GUARDRAIL_FIELDS = new Set(['maxCostUsd', 'maxTurns', 'timeoutS']);
const AGENT_STEP_FIELDS = new Set([
  'id', 'type', 'role', 'engine', 'engines', 'profile', 'model', 'prompt', 'usePrevious', 'useBrief',
]);
const APPROVAL_STEP_FIELDS = new Set(['id', 'type', 'title', 'timeoutS']);

const _ENCODER = new TextEncoder();

export function utf8ByteLength(str) {
  return _ENCODER.encode(String(str ?? '')).byteLength;
}

/**
 * Mirrors `_hidden_char` in `pixel_connector/workflows.py`:
 * Rejects control characters (Cc), format/bidi/zero-width characters (Cf),
 * and Unicode line/paragraph separators (Zl/Zp: U+2028, U+2029).
 */
export function hasHiddenChar(text, allowNewlines = false) {
  if (typeof text !== 'string') return true;
  for (let i = 0; i < text.length; ) {
    const cp = text.codePointAt(i);
    i += cp > 0xFFFF ? 2 : 1;
    if (allowNewlines && (cp === 0x0A || cp === 0x0D || cp === 0x09)) continue;
    // Cc control characters
    if (cp <= 0x1F || (cp >= 0x7F && cp <= 0x9F)) return true;
    // Zl / Zp line & paragraph separators
    if (cp === 0x2028 || cp === 0x2029) return true;
    // Cf format / bidi / zero-width characters
    if (
      cp === 0x00AD ||
      (cp >= 0x0600 && cp <= 0x0605) ||
      cp === 0x061C ||
      cp === 0x06DD ||
      cp === 0x070F ||
      cp === 0x0890 ||
      cp === 0x0891 ||
      cp === 0x08E2 ||
      cp === 0x180E ||
      (cp >= 0x200B && cp <= 0x200F) ||
      (cp >= 0x202A && cp <= 0x202E) ||
      (cp >= 0x2060 && cp <= 0x2064) ||
      (cp >= 0x2066 && cp <= 0x206F) ||
      cp === 0xFEFF ||
      (cp >= 0xFFF9 && cp <= 0xFFFB) ||
      (cp >= 0xE0001 && cp <= 0xE007F)
    ) {
      return true;
    }
  }
  return false;
}

const TR_CHAR_MAP = {
  'ç': 'c', 'Ç': 'c',
  'ğ': 'g', 'Ğ': 'g',
  'ı': 'i', 'I': 'i', 'İ': 'i',
  'ö': 'o', 'Ö': 'o',
  'ş': 's', 'Ş': 's',
  'ü': 'u', 'Ü': 'u',
};

export function slugify(text) {
  const raw = String(text || '');
  let mapped = '';
  for (const ch of raw) mapped += TR_CHAR_MAP[ch] ?? ch;
  const cleaned = mapped
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
    .replace(/-+$/g, '');
  return cleaned || 'sablon';
}

function cleanText(val, field, maxLen, { allowEmpty = false, allowNewlines = false } = {}) {
  if (typeof val !== 'string') {
    return { ok: false, code: 'bad_template', message: `${field} must be a string` };
  }
  if (hasHiddenChar(val, allowNewlines)) {
    return { ok: false, code: 'bad_template', message: `${field} contains control or hidden characters` };
  }
  const text = allowNewlines ? val : val.trim();
  if (!allowEmpty && !text.trim()) {
    return { ok: false, code: 'bad_template', message: `${field} is empty` };
  }
  if (text.length > maxLen || utf8ByteLength(text) > maxLen) {
    return { ok: false, code: 'bad_template', message: `${field} exceeds ${maxLen} characters` };
  }
  return { ok: true, value: text };
}

/**
 * Mirrors `_clean_folder` in `pixel_connector/workflows.py` and `validate_sub` in `pixel_connector/workspaces.py`.
 */
export function validateFolder(raw) {
  if (raw === null || raw === undefined || raw === '') {
    return { ok: true, value: null };
  }
  if (typeof raw !== 'string') {
    return { ok: false, code: 'bad_template', message: 'folder: sub must be a relative folder name' };
  }
  const s = raw.trim().replace(/^[/\\]+|[/\\]+$/g, '');
  if (!s) {
    // Leading slash like "/abs" had non-empty raw but started with "/" or "\"
    if (/^[/\\]/.test(raw.trim())) {
      return { ok: false, code: 'bad_template', message: 'folder: sub must be a relative folder name' };
    }
    return { ok: true, value: null };
  }
  const trimmed = raw.trim();
  if (
    trimmed.length > FOLDER_MAX ||
    trimmed.includes('\x00') ||
    trimmed.startsWith('/') ||
    trimmed.startsWith('\\') ||
    /^[A-Za-z]:/.test(trimmed)
  ) {
    return { ok: false, code: 'bad_template', message: 'folder: sub must be a relative folder name' };
  }
  const parts = s.replace(/\\/g, '/').split('/');
  for (const p of parts) {
    if (!p || p === '.' || p === '..') {
      return { ok: false, code: 'bad_template', message: 'folder: sub cannot escape the workspace root' };
    }
    for (let i = 0; i < p.length; i++) {
      const code = p.charCodeAt(i);
      if (code < 0x20 || code === 0x7F) {
        return { ok: false, code: 'bad_template', message: 'folder: sub contains control characters' };
      }
    }
  }
  return { ok: true, value: parts.join('/') };
}

/**
 * Mirrors `_clean_guardrails` in `pixel_connector/workflows.py`.
 */
export function validateGuardrails(raw) {
  if (raw === null || raw === undefined) {
    return { ok: true, value: { ...DEFAULT_GUARDRAILS } };
  }
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, code: 'bad_template', message: 'guardrails must be an object' };
  }
  for (const k of Object.keys(raw)) {
    if (!GUARDRAIL_FIELDS.has(k)) {
      return { ok: false, code: 'bad_template', message: `guardrails unknown field: ${k}` };
    }
  }
  const cost = raw.maxCostUsd !== undefined ? raw.maxCostUsd : DEFAULT_GUARDRAILS.maxCostUsd;
  const turns = raw.maxTurns !== undefined ? raw.maxTurns : DEFAULT_GUARDRAILS.maxTurns;
  const timeout = raw.timeoutS !== undefined ? raw.timeoutS : DEFAULT_GUARDRAILS.timeoutS;

  if (typeof cost === 'boolean' || typeof cost !== 'number' || !Number.isFinite(cost) || cost < 0.01 || cost > 20.0) {
    return { ok: false, code: 'bad_template', message: 'guardrails.maxCostUsd must be in 0.01..20.00' };
  }
  if (typeof turns === 'boolean' || !Number.isInteger(turns) || turns < 1 || turns > 500) {
    return { ok: false, code: 'bad_template', message: 'guardrails.maxTurns must be 1..500' };
  }
  if (typeof timeout === 'boolean' || !Number.isInteger(timeout) || timeout < 60 || timeout > 14400) {
    return { ok: false, code: 'bad_template', message: 'guardrails.timeoutS must be 60..14400' };
  }
  return {
    ok: true,
    value: {
      maxCostUsd: Math.round(cost * 10000) / 10000,
      maxTurns: turns,
      timeoutS: timeout,
    },
  };
}

/**
 * Mirrors `_clean_step` in `pixel_connector/workflows.py`.
 */
export function validateStep(raw, { seenIds = new Set(), hasEarlierAgent = false, autoEnabled = false } = {}) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, code: 'bad_template', message: 'step must be an object' };
  }
  const sid = raw.id;
  if (typeof sid !== 'string' || !SLUG_RE.test(sid)) {
    return { ok: false, code: 'bad_template', message: `step.id invalid: ${String(sid)}` };
  }
  if (seenIds.has(sid)) {
    return { ok: false, code: 'bad_template', message: `duplicate step.id: ${sid}` };
  }
  const stype = raw.type;
  if (!STEP_TYPES.includes(stype)) {
    return { ok: false, code: 'bad_template', message: `step ${sid}: type must be agent or approval` };
  }

  if (stype === 'approval') {
    for (const k of Object.keys(raw)) {
      if (!APPROVAL_STEP_FIELDS.has(k)) {
        return { ok: false, code: 'bad_template', message: `step ${sid} unknown field: ${k}` };
      }
    }
    const titleRes = cleanText(raw.title, `step ${sid}.title`, TITLE_MAX);
    if (!titleRes.ok) return titleRes;
    const timeout = raw.timeoutS !== undefined ? raw.timeoutS : 900;
    if (typeof timeout === 'boolean' || !Number.isInteger(timeout) || timeout < 30 || timeout > 3600) {
      return { ok: false, code: 'bad_template', message: `step ${sid}.timeoutS must be 30..3600` };
    }
    return {
      ok: true,
      value: { id: sid, type: 'approval', title: titleRes.value, timeoutS: timeout },
    };
  }

  for (const k of Object.keys(raw)) {
    if (!AGENT_STEP_FIELDS.has(k)) {
      return { ok: false, code: 'bad_template', message: `step ${sid} unknown field: ${k}` };
    }
  }
  const roleRes = cleanText(raw.role, `step ${sid}.role`, ROLE_MAX);
  if (!roleRes.ok) return roleRes;
  const engine = raw.engine !== undefined ? raw.engine : 'claude';
  if (!ENGINES.includes(engine)) {
    return { ok: false, code: 'bad_template', message: `step ${sid}.engine invalid` };
  }
  const rawEngines = raw.engines !== undefined ? raw.engines : [engine];
  if (
    !Array.isArray(rawEngines) ||
    rawEngines.length === 0 ||
    rawEngines.some((e) => !ENGINES.includes(e)) ||
    new Set(rawEngines).size !== rawEngines.length ||
    !rawEngines.includes(engine)
  ) {
    return { ok: false, code: 'bad_template', message: `step ${sid}.engines invalid` };
  }
  const profile = raw.profile !== undefined ? raw.profile : 'readonly';
  if (!PROFILES.includes(profile)) {
    return { ok: false, code: 'bad_template', message: `step ${sid}.profile invalid` };
  }
  if (profile === 'auto' && !autoEnabled) {
    return { ok: false, code: 'bad_template', message: `step ${sid}.profile auto is disabled` };
  }
  let model = raw.model !== undefined && raw.model !== '' ? raw.model : null;
  if (model !== null && (typeof model !== 'string' || !MODEL_RE.test(model))) {
    return { ok: false, code: 'bad_template', message: `step ${sid}.model invalid` };
  }
  const promptRes = cleanText(raw.prompt, `step ${sid}.prompt`, PROMPT_MAX, { allowNewlines: true });
  if (!promptRes.ok) return promptRes;
  const usePrev = raw.usePrevious !== undefined ? raw.usePrevious : hasEarlierAgent;
  const useBrief = raw.useBrief !== undefined ? raw.useBrief : true;
  if (typeof usePrev !== 'boolean' || typeof useBrief !== 'boolean') {
    return { ok: false, code: 'bad_template', message: `step ${sid} flags must be boolean` };
  }
  if (usePrev && !hasEarlierAgent) {
    return { ok: false, code: 'bad_template', message: `step ${sid}: usePrevious without an earlier agent step` };
  }
  return {
    ok: true,
    value: {
      id: sid,
      type: 'agent',
      role: roleRes.value,
      engine,
      engines: [...rawEngines],
      profile,
      model,
      prompt: promptRes.value,
      usePrevious: usePrev,
      useBrief,
    },
  };
}

/**
 * Mirrors `validate_template` in `pixel_connector/workflows.py`.
 */
export function validateTemplate(raw, { autoEnabled = false, existingIds = [] } = {}) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, code: 'bad_template', message: 'template must be an object' };
  }
  for (const k of Object.keys(raw)) {
    if (!TEMPLATE_FIELDS.has(k)) {
      return { ok: false, code: 'bad_template', message: `template unknown field: ${k}` };
    }
  }
  const tid = raw.id;
  if (typeof tid !== 'string' || !SLUG_RE.test(tid)) {
    return { ok: false, code: 'bad_template', message: `template.id invalid: ${String(tid)}` };
  }
  if (RESERVED_TEMPLATE_IDS.includes(tid)) {
    return { ok: false, code: 'bad_template', message: `reserved or duplicate template.id: ${tid}` };
  }
  if (Array.isArray(existingIds) && existingIds.includes(tid)) {
    return { ok: false, code: 'bad_template', message: `duplicate template.id: ${tid}` };
  }

  const nameRes = cleanText(raw.name, 'name', NAME_MAX);
  if (!nameRes.ok) return nameRes;

  const descRes = cleanText(raw.description !== undefined ? raw.description : '', 'description', DESCRIPTION_MAX, {
    allowEmpty: true,
  });
  if (!descRes.ok) return descRes;

  const folderRes = validateFolder(raw.folder);
  if (!folderRes.ok) return folderRes;

  const guardrailsRes = validateGuardrails(raw.guardrails);
  if (!guardrailsRes.ok) return guardrailsRes;

  const rawSteps = raw.steps;
  if (!Array.isArray(rawSteps) || rawSteps.length < 1 || rawSteps.length > MAX_STEPS) {
    return { ok: false, code: 'bad_template', message: `steps must be 1..${MAX_STEPS} items` };
  }

  const seenIds = new Set();
  const steps = [];
  let hasAgent = false;
  for (const item of rawSteps) {
    const stepRes = validateStep(item, { seenIds, hasEarlierAgent: hasAgent, autoEnabled });
    if (!stepRes.ok) return stepRes;
    seenIds.add(stepRes.value.id);
    if (stepRes.value.type === 'agent') hasAgent = true;
    steps.push(stepRes.value);
  }
  if (!hasAgent) {
    return { ok: false, code: 'bad_template', message: 'steps: no agent step' };
  }

  return {
    ok: true,
    value: {
      id: tid,
      name: nameRes.value,
      description: descRes.value,
      folder: folderRes.value,
      guardrails: guardrailsRes.value,
      steps,
    },
  };
}

/**
 * Mirrors `public_template` in `pixel_connector/workflows.py` (strips prompt text).
 */
export function toPublicTemplate(tpl) {
  const steps = (tpl.steps || []).map((s) => (
    s.type === 'agent'
      ? {
        id: s.id,
        type: 'agent',
        role: s.role,
        engine: s.engine,
        engines: [...(s.engines || [s.engine])],
        profile: s.profile,
        model: s.model ?? null,
        usePrevious: Boolean(s.usePrevious),
        useBrief: Boolean(s.useBrief),
      }
      : {
        id: s.id,
        type: 'approval',
        title: s.title,
        timeoutS: s.timeoutS,
      }
  ));
  return {
    id: tpl.id,
    name: tpl.name,
    description: tpl.description || '',
    builtIn: false,
    custom: true,
    folder: tpl.folder ?? null,
    guardrails: { ...(tpl.guardrails || DEFAULT_GUARDRAILS) },
    steps,
  };
}

/**
 * Validates an array of templates and produces the exact `workflows.json` structure
 * (`{"v": 1, "templates": [...]}`) read by `pixel_connector/workflows.py:load_templates`.
 */
export function buildWorkflowsJson(templates, { autoEnabled = false } = {}) {
  if (!Array.isArray(templates)) {
    return { ok: false, code: 'bad_template', message: 'templates must be a list' };
  }
  const seenIds = [];
  const cleaned = [];
  for (const item of templates) {
    const res = validateTemplate(item, { autoEnabled, existingIds: seenIds });
    if (!res.ok) return res;
    seenIds.push(res.value.id);
    cleaned.push(res.value);
  }
  const data = { v: 1, templates: cleaned };
  return {
    ok: true,
    data,
    json: JSON.stringify(data, null, 2),
  };
}

function el(doc, tag, cls, text) {
  const n = doc.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

function defaultAgentStep(index) {
  return {
    id: `adim-${index}`,
    type: 'agent',
    role: index === 1 ? tt('Araştırmacı') : `Uzman ${index}`,
    engine: 'claude',
    engines: ['claude', 'ollama'],
    profile: index === 1 ? 'readonly' : 'edit',
    model: null,
    prompt: '',
    usePrevious: index > 1,
    useBrief: true,
  };
}

function defaultApprovalStep(index) {
  return {
    id: `onay-${index}`,
    type: 'approval',
    title: tt('Devam edilsin mi?'),
    timeoutS: 900,
  };
}

function defaultDraft() {
  return {
    id: 'yeni-sablon',
    name: '',
    description: '',
    folder: '',
    guardrails: { ...DEFAULT_GUARDRAILS },
    steps: [defaultAgentStep(1)],
  };
}

export class TemplateBuilder {
  constructor({ container, storage = null, onReload = null, onChange = null } = {}) {
    this.container = container;
    this.doc = container?.ownerDocument || document;
    this.storage = storage || (typeof localStorage !== 'undefined' ? localStorage : null);
    this.onReload = onReload;
    this.onChange = onChange;

    this._templates = this._loadStored();
    this._draft = defaultDraft();
    this._idManuallyEdited = false;
    this._editingId = null;
    this._error = '';
    this._notice = '';
  }

  _loadStored() {
    if (!this.storage) return [];
    try {
      const raw = this.storage.getItem(CUSTOM_TEMPLATES_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      const list = Array.isArray(parsed?.templates) ? parsed.templates : (Array.isArray(parsed) ? parsed : []);
      const valid = [];
      const seen = [];
      for (const item of list) {
        const res = validateTemplate(item, { existingIds: seen });
        if (res.ok) {
          seen.push(res.value.id);
          valid.push(res.value);
        }
      }
      return valid;
    } catch {
      return [];
    }
  }

  _persist() {
    if (!this.storage) return;
    try {
      this.storage.setItem(CUSTOM_TEMPLATES_KEY, JSON.stringify({ v: 1, templates: this._templates }));
    } catch {
      // Ignore storage quota errors
    }
  }

  listTemplates() {
    return this._templates.map((t) => ({
      ...t,
      guardrails: { ...t.guardrails },
      steps: t.steps.map((s) => ({ ...s, ...(s.engines ? { engines: [...s.engines] } : {}) })),
    }));
  }

  listPublicTemplates() {
    return this._templates.map((t) => toPublicTemplate(t));
  }

  exportJson() {
    const res = buildWorkflowsJson(this._templates);
    return res.ok ? res.json : JSON.stringify({ v: 1, templates: [] }, null, 2);
  }

  resetDraft() {
    this._draft = defaultDraft();
    this._idManuallyEdited = false;
    this._editingId = null;
    this._error = '';
    this._notice = '';
    this.render();
  }

  loadIntoDraft(templateId) {
    const found = this._templates.find((t) => t.id === templateId);
    if (!found) return false;
    this._draft = {
      id: found.id,
      name: found.name,
      description: found.description || '',
      folder: found.folder || '',
      guardrails: { ...found.guardrails },
      steps: found.steps.map((s) => ({ ...s, ...(s.engines ? { engines: [...s.engines] } : {}) })),
    };
    this._idManuallyEdited = true;
    this._editingId = found.id;
    this._error = '';
    this._notice = tt('"{name}" düzenleniyor.', { name: found.name });
    this.render();
    return true;
  }

  saveTemplate(rawTemplate = this._draft, { editingId = this._editingId } = {}) {
    const existingIds = this._templates
      .filter((t) => t.id !== editingId)
      .map((t) => t.id);

    const candidate = {
      id: String(rawTemplate.id || '').trim(),
      name: String(rawTemplate.name || ''),
      description: String(rawTemplate.description || ''),
      folder: rawTemplate.folder ? String(rawTemplate.folder) : null,
      guardrails: {
        maxCostUsd: Number(rawTemplate.guardrails?.maxCostUsd ?? DEFAULT_GUARDRAILS.maxCostUsd),
        maxTurns: Number(rawTemplate.guardrails?.maxTurns ?? DEFAULT_GUARDRAILS.maxTurns),
        timeoutS: Number(rawTemplate.guardrails?.timeoutS ?? DEFAULT_GUARDRAILS.timeoutS),
      },
      steps: (rawTemplate.steps || []).map((s) => {
        if (s.type === 'approval') {
          return {
            id: String(s.id || '').trim(),
            type: 'approval',
            title: String(s.title || ''),
            timeoutS: Number(s.timeoutS ?? 900),
          };
        }
        const eng = s.engine || 'claude';
        const engs = Array.isArray(s.engines) && s.engines.length
          ? Array.from(new Set([eng, ...s.engines]))
          : [eng];
        return {
          id: String(s.id || '').trim(),
          type: 'agent',
          role: String(s.role || ''),
          engine: eng,
          engines: engs,
          profile: s.profile || 'readonly',
          model: s.model && String(s.model).trim() ? String(s.model).trim() : null,
          prompt: String(s.prompt || ''),
          usePrevious: Boolean(s.usePrevious),
          useBrief: Boolean(s.useBrief),
        };
      }),
    };

    const res = validateTemplate(candidate, { existingIds });
    if (!res.ok) {
      this._error = res.message;
      this._notice = '';
      this.render();
      return res;
    }

    const idx = editingId ? this._templates.findIndex((t) => t.id === editingId) : -1;
    if (idx >= 0) {
      this._templates[idx] = res.value;
    } else {
      const sameIdx = this._templates.findIndex((t) => t.id === res.value.id);
      if (sameIdx >= 0) this._templates[sameIdx] = res.value;
      else this._templates.push(res.value);
    }

    this._persist();
    this._draft = defaultDraft();
    this._idManuallyEdited = false;
    this._editingId = null;
    this._error = '';
    this._notice = tt('"{name}" kaydedildi. Cihazda çalıştırmak için workflows.json dosyasını indirip ~/.pixel-office/connector/workflows.json konumuna koyun ve "Cihazdan Yenile"ye basın.', { name: res.value.name });
    if (this.onChange) this.onChange(this.listTemplates());
    this.render();
    return res;
  }

  deleteTemplate(templateId) {
    const before = this._templates.length;
    this._templates = this._templates.filter((t) => t.id !== templateId);
    if (this._templates.length === before) return false;
    if (this._editingId === templateId) {
      this._editingId = null;
      this._draft = defaultDraft();
    }
    this._persist();
    this._error = '';
    this._notice = tt('Şablon silindi ({id}).', { id: templateId });
    if (this.onChange) this.onChange(this.listTemplates());
    this.render();
    return true;
  }

  _recomputeFirstAgentFlags() {
    let seenAgent = false;
    for (const s of this._draft.steps) {
      if (s.type === 'agent') {
        if (!seenAgent) {
          s.usePrevious = false;
          seenAgent = true;
        }
      }
    }
  }

  render() {
    if (!this.container) return;
    const doc = this.doc;
    const root = el(doc, 'div', 'tb-root');
    root.dataset.tbRoot = '1';

    const head = el(doc, 'div', 'tb-head');
    head.appendChild(el(doc, 'h4', 'tb-title', this._editingId ? tt('Şablonu Düzenle ({id})', { id: this._editingId }) : tt('Kodsuz Şablon Oluşturucu')));
    const sub = el(
      doc,
      'p',
      'muted small',
      tt('Adımları görsel olarak dizin. Bağlayıcı şablonları ~/.pixel-office/connector/workflows.json dosyasından okur; buradan dışa aktarıp "Cihazdan Yenile" ile anında yükleyebilirsiniz.'),
    );
    head.appendChild(sub);
    root.appendChild(head);

    if (this._error) {
      const errEl = el(doc, 'p', 'wf-error tb-error', tt('Doğrulama hatası: {error}', { error: this._error }));
      errEl.dataset.tbError = '1';
      root.appendChild(errEl);
    }
    if (this._notice) {
      const noteEl = el(doc, 'p', 'note small tb-notice', this._notice);
      noteEl.dataset.tbNotice = '1';
      root.appendChild(noteEl);
    }

    // Top metadata fields
    const metaGrid = el(doc, 'div', 'tb-grid');

    const nameLbl = el(doc, 'label', 'wf-field', tt('Şablon Adı'));
    const nameInp = el(doc, 'input', 'wf-input');
    nameInp.type = 'text';
    nameInp.maxLength = NAME_MAX;
    nameInp.placeholder = tt('ör. Blog ve Bülten Hattı');
    nameInp.value = this._draft.name;
    nameInp.dataset.tbField = 'name';

    const idLbl = el(doc, 'label', 'wf-field', tt('Şablon Kimliği (slug)'));
    const idInp = el(doc, 'input', 'wf-input mono');
    idInp.type = 'text';
    idInp.maxLength = 32;
    idInp.placeholder = tt('ör. blog-bulten');
    idInp.value = this._draft.id;
    idInp.dataset.tbField = 'id';

    nameInp.addEventListener('input', () => {
      this._draft.name = nameInp.value;
      if (!this._idManuallyEdited) {
        this._draft.id = slugify(nameInp.value);
        idInp.value = this._draft.id;
      }
    });
    idInp.addEventListener('input', () => {
      this._idManuallyEdited = true;
      this._draft.id = idInp.value.trim();
    });

    nameLbl.appendChild(nameInp);
    idLbl.appendChild(idInp);
    metaGrid.append(nameLbl, idLbl);

    const descLbl = el(doc, 'label', 'wf-field', tt('Açıklama (isteğe bağlı)'));
    const descInp = el(doc, 'input', 'wf-input');
    descInp.type = 'text';
    descInp.maxLength = DESCRIPTION_MAX;
    descInp.placeholder = tt('Kısa açıklama (en fazla 300 karakter)');
    descInp.value = this._draft.description;
    descInp.dataset.tbField = 'description';
    descInp.addEventListener('input', () => { this._draft.description = descInp.value; });
    descLbl.appendChild(descInp);

    const folderLbl = el(doc, 'label', 'wf-field', tt('Varsayılan Alt Klasör'));
    const folderInp = el(doc, 'input', 'wf-input mono');
    folderInp.type = 'text';
    folderInp.maxLength = FOLDER_MAX;
    folderInp.placeholder = tt('ör. icerik/bulten (boş bırakılabilir)');
    folderInp.value = this._draft.folder || '';
    folderInp.dataset.tbField = 'folder';
    folderInp.addEventListener('input', () => { this._draft.folder = folderInp.value; });
    folderLbl.appendChild(folderInp);

    metaGrid.append(descLbl, folderLbl);
    root.appendChild(metaGrid);

    // Guardrails row
    const grRow = el(doc, 'div', 'tb-guardrails');
    const costLbl = el(doc, 'label', 'wf-field', tt('Bütçe Sınırı (USD, 0.01–20)'));
    const costInp = el(doc, 'input', 'wf-input mono');
    costInp.type = 'number';
    costInp.min = '0.01';
    costInp.max = '20';
    costInp.step = '0.25';
    costInp.value = String(this._draft.guardrails.maxCostUsd);
    costInp.dataset.tbField = 'maxCostUsd';
    costInp.addEventListener('input', () => {
      this._draft.guardrails.maxCostUsd = Number(costInp.value);
    });
    costLbl.appendChild(costInp);

    const turnsLbl = el(doc, 'label', 'wf-field', tt('Tur Sınırı (1–500)'));
    const turnsInp = el(doc, 'input', 'wf-input mono');
    turnsInp.type = 'number';
    turnsInp.min = '1';
    turnsInp.max = '500';
    turnsInp.step = '1';
    turnsInp.value = String(this._draft.guardrails.maxTurns);
    turnsInp.dataset.tbField = 'maxTurns';
    turnsInp.addEventListener('input', () => {
      this._draft.guardrails.maxTurns = Number.parseInt(turnsInp.value, 10);
    });
    turnsLbl.appendChild(turnsInp);

    const toLbl = el(doc, 'label', 'wf-field', tt('Zaman Aşımı (sn, 60–14400)'));
    const toInp = el(doc, 'input', 'wf-input mono');
    toInp.type = 'number';
    toInp.min = '60';
    toInp.max = '14400';
    toInp.step = '60';
    toInp.value = String(this._draft.guardrails.timeoutS);
    toInp.dataset.tbField = 'timeoutS';
    toInp.addEventListener('input', () => {
      this._draft.guardrails.timeoutS = Number.parseInt(toInp.value, 10);
    });
    toLbl.appendChild(toInp);

    grRow.append(costLbl, turnsLbl, toLbl);
    root.appendChild(grRow);

    // Steps list
    const stepsWrap = el(doc, 'div', 'tb-steps');
    const stepsHead = el(doc, 'div', 'tb-steps-head');
    stepsHead.appendChild(el(doc, 'h5', '', tt('Adımlar ({n}/{max})', { n: this._draft.steps.length, max: MAX_STEPS })));

    const stepAddBtns = el(doc, 'div', 'tb-step-actions');
    const addAgentBtn = el(doc, 'button', 'btn small', tt('+ Ajan Adımı'));
    addAgentBtn.type = 'button';
    addAgentBtn.dataset.tbAction = 'add-agent';
    addAgentBtn.disabled = this._draft.steps.length >= MAX_STEPS;
    addAgentBtn.addEventListener('click', () => {
      if (this._draft.steps.length >= MAX_STEPS) return;
      this._draft.steps.push(defaultAgentStep(this._draft.steps.length + 1));
      this._recomputeFirstAgentFlags();
      this.render();
    });

    const addApprovalBtn = el(doc, 'button', 'btn small', tt('+ Onay Kapısı'));
    addApprovalBtn.type = 'button';
    addApprovalBtn.dataset.tbAction = 'add-approval';
    addApprovalBtn.disabled = this._draft.steps.length >= MAX_STEPS;
    addApprovalBtn.addEventListener('click', () => {
      if (this._draft.steps.length >= MAX_STEPS) return;
      this._draft.steps.push(defaultApprovalStep(this._draft.steps.length + 1));
      this.render();
    });

    stepAddBtns.append(addAgentBtn, addApprovalBtn);
    stepsHead.appendChild(stepAddBtns);
    stepsWrap.appendChild(stepsHead);

    let seenAgentBefore = false;
    this._draft.steps.forEach((step, idx) => {
      const card = this._renderStepEditor(step, idx, seenAgentBefore);
      if (step.type === 'agent') seenAgentBefore = true;
      stepsWrap.appendChild(card);
    });
    root.appendChild(stepsWrap);

    // Form actions
    const actions = el(doc, 'div', 'tb-actions');
    const saveBtn = el(doc, 'button', 'btn primary', this._editingId ? tt('Değişiklikleri Kaydet') : tt('Şablonu Kaydet'));
    saveBtn.type = 'button';
    saveBtn.dataset.tbAction = 'save';
    saveBtn.addEventListener('click', () => this.saveTemplate());
    actions.appendChild(saveBtn);

    if (this._editingId) {
      const cancelEditBtn = el(doc, 'button', 'btn small', tt('Yeni Şablona Geç'));
      cancelEditBtn.type = 'button';
      cancelEditBtn.dataset.tbAction = 'cancel-edit';
      cancelEditBtn.addEventListener('click', () => this.resetDraft());
      actions.appendChild(cancelEditBtn);
    }

    const copyJsonBtn = el(doc, 'button', 'btn small', 'workflows.json Kopyala');
    copyJsonBtn.type = 'button';
    copyJsonBtn.dataset.tbAction = 'copy-json';
    copyJsonBtn.addEventListener('click', () => {
      const jsonText = this.exportJson();
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(jsonText).catch(() => {});
      }
      this._notice = tt('workflows.json panoya kopyalandı.');
      this.render();
    });

    const dlJsonBtn = el(doc, 'button', 'btn small', tt('workflows.json İndir'));
    dlJsonBtn.type = 'button';
    dlJsonBtn.dataset.tbAction = 'download-json';
    dlJsonBtn.addEventListener('click', () => {
      this._downloadWorkflowsJson();
    });

    const reloadBtn = el(doc, 'button', 'btn small', tt('Cihazdan Yenile'));
    reloadBtn.type = 'button';
    reloadBtn.dataset.tbAction = 'reload-device';
    reloadBtn.addEventListener('click', () => {
      if (this.onReload) this.onReload();
      this._notice = tt('Cihazdan şablon listesi (workflow.list) istendi.');
      this.render();
    });

    actions.append(copyJsonBtn, dlJsonBtn, reloadBtn);
    root.appendChild(actions);

    // Saved custom templates list
    const savedSec = el(doc, 'div', 'tb-saved');
    savedSec.dataset.tbSavedList = '1';
    savedSec.appendChild(el(doc, 'h5', '', tt('Kaydedilen Taslak Şablonlar ({n})', { n: this._templates.length })));
    if (this._templates.length === 0) {
      savedSec.appendChild(el(doc, 'p', 'muted small', tt('Henüz kaydedilmiş özel şablon yok.')));
    } else {
      for (const tpl of this._templates) {
        const row = el(doc, 'div', 'tb-saved-item');
        const info = el(doc, 'div', 'tb-saved-info');
        info.appendChild(el(doc, 'strong', '', tpl.name));
        info.appendChild(el(doc, 'span', 'wf-badge mono', tpl.id));
        if (tpl.description) info.appendChild(el(doc, 'span', 'muted small', tpl.description));
        row.appendChild(info);

        const btns = el(doc, 'div', 'tb-saved-btns');
        const editB = el(doc, 'button', 'btn small', tt('Düzenle'));
        editB.type = 'button';
        editB.dataset.tbAction = 'edit-saved';
        editB.addEventListener('click', () => this.loadIntoDraft(tpl.id));

        const delB = el(doc, 'button', 'btn small danger-btn', 'Sil');
        delB.type = 'button';
        delB.dataset.tbAction = 'delete-saved';
        delB.addEventListener('click', () => this.deleteTemplate(tpl.id));

        btns.append(editB, delB);
        row.appendChild(btns);
        savedSec.appendChild(row);
      }
    }
    root.appendChild(savedSec);

    this.container.replaceChildren(root);
  }

  _renderStepEditor(step, idx, hasEarlierAgent) {
    const doc = this.doc;
    const card = el(doc, 'div', 'tb-step-card');
    card.dataset.tbStep = String(idx);

    const head = el(doc, 'div', 'tb-step-head');
    head.appendChild(
      el(doc, 'strong', '', `${idx + 1}. ${step.type === 'approval' ? tt('Onay Kapısı') : tt('Ajan Adımı')}`),
    );

    const moveBtns = el(doc, 'div', 'tb-step-move');
    const upBtn = el(doc, 'button', 'btn small', '↑');
    upBtn.type = 'button';
    upBtn.disabled = idx === 0;
    upBtn.setAttribute('aria-label', tt('Adımı yukarı taşı'));
    upBtn.addEventListener('click', () => {
      if (idx <= 0) return;
      const tmp = this._draft.steps[idx - 1];
      this._draft.steps[idx - 1] = this._draft.steps[idx];
      this._draft.steps[idx] = tmp;
      this._recomputeFirstAgentFlags();
      this.render();
    });

    const downBtn = el(doc, 'button', 'btn small', '↓');
    downBtn.type = 'button';
    downBtn.disabled = idx >= this._draft.steps.length - 1;
    downBtn.setAttribute('aria-label', tt('Adımı aşağı taşı'));
    downBtn.addEventListener('click', () => {
      if (idx >= this._draft.steps.length - 1) return;
      const tmp = this._draft.steps[idx + 1];
      this._draft.steps[idx + 1] = this._draft.steps[idx];
      this._draft.steps[idx] = tmp;
      this._recomputeFirstAgentFlags();
      this.render();
    });

    const rmBtn = el(doc, 'button', 'btn small danger-btn', 'Sil');
    rmBtn.type = 'button';
    rmBtn.disabled = this._draft.steps.length <= 1;
    rmBtn.addEventListener('click', () => {
      if (this._draft.steps.length <= 1) return;
      this._draft.steps.splice(idx, 1);
      this._recomputeFirstAgentFlags();
      this.render();
    });

    moveBtns.append(upBtn, downBtn, rmBtn);
    head.appendChild(moveBtns);
    card.appendChild(head);

    const grid = el(doc, 'div', 'tb-step-grid');
    const idLbl = el(doc, 'label', 'wf-field', tt('Adım Kimliği (slug)'));
    const idInp = el(doc, 'input', 'wf-input mono');
    idInp.type = 'text';
    idInp.maxLength = 32;
    idInp.value = step.id || '';
    idInp.dataset.tbStepField = 'id';
    idInp.addEventListener('input', () => { step.id = idInp.value.trim(); });
    idLbl.appendChild(idInp);
    grid.appendChild(idLbl);

    if (step.type === 'approval') {
      const titleLbl = el(doc, 'label', 'wf-field', tt('Onay Sorusu / Başlığı'));
      const titleInp = el(doc, 'input', 'wf-input');
      titleInp.type = 'text';
      titleInp.maxLength = TITLE_MAX;
      titleInp.value = step.title || '';
      titleInp.dataset.tbStepField = 'title';
      titleInp.addEventListener('input', () => { step.title = titleInp.value; });
      titleLbl.appendChild(titleInp);

      const toLbl = el(doc, 'label', 'wf-field', tt('Onay Zaman Aşımı (sn, 30–3600)'));
      const toInp = el(doc, 'input', 'wf-input mono');
      toInp.type = 'number';
      toInp.min = '30';
      toInp.max = '3600';
      toInp.value = String(step.timeoutS ?? 900);
      toInp.dataset.tbStepField = 'timeoutS';
      toInp.addEventListener('input', () => { step.timeoutS = Number.parseInt(toInp.value, 10); });
      toLbl.appendChild(toInp);

      grid.append(titleLbl, toLbl);
      card.appendChild(grid);
      return card;
    }

    const roleLbl = el(doc, 'label', 'wf-field', tt('Ajan Rolü'));
    const roleInp = el(doc, 'input', 'wf-input');
    roleInp.type = 'text';
    roleInp.maxLength = ROLE_MAX;
    roleInp.placeholder = tt('ör. Araştırmacı, Yazar, Denetçi');
    roleInp.value = step.role || '';
    roleInp.dataset.tbStepField = 'role';
    roleInp.addEventListener('input', () => { step.role = roleInp.value; });
    roleLbl.appendChild(roleInp);

    const engLbl = el(doc, 'label', 'wf-field', 'Motor');
    const engSel = el(doc, 'select', 'wf-input');
    engSel.dataset.tbStepField = 'engine';
    for (const eng of ENGINES) {
      const opt = el(doc, 'option', '', eng === 'claude' ? 'Claude Code' : tt('Ollama (Yerel)'));
      opt.value = eng;
      opt.selected = step.engine === eng;
      engSel.appendChild(opt);
    }
    engSel.addEventListener('change', () => {
      step.engine = engSel.value;
      if (!Array.isArray(step.engines) || !step.engines.includes(step.engine)) {
        step.engines = Array.from(new Set([step.engine, ...(step.engines || [])]));
      }
    });
    engLbl.appendChild(engSel);

    const profLbl = el(doc, 'label', 'wf-field', tt('Yetki Profili'));
    const profSel = el(doc, 'select', 'wf-input');
    profSel.dataset.tbStepField = 'profile';
    const profileLabels = {
      readonly: tt('Salt Okunur (readonly)'),
      edit: tt('Dosya Düzenleme (edit)'),
      dev: tt('Geliştirici / Komut (dev)'),
    };
    for (const p of ['readonly', 'edit', 'dev']) {
      const opt = el(doc, 'option', '', profileLabels[p]);
      opt.value = p;
      opt.selected = step.profile === p;
      profSel.appendChild(opt);
    }
    profSel.addEventListener('change', () => { step.profile = profSel.value; });
    profLbl.appendChild(profSel);

    const modLbl = el(doc, 'label', 'wf-field', tt('Model (isteğe bağlı)'));
    const modInp = el(doc, 'input', 'wf-input mono');
    modInp.type = 'text';
    modInp.maxLength = 64;
    modInp.placeholder = tt('varsayılan');
    modInp.value = step.model || '';
    modInp.dataset.tbStepField = 'model';
    modInp.addEventListener('input', () => {
      step.model = modInp.value.trim() || null;
    });
    modLbl.appendChild(modInp);

    grid.append(roleLbl, engLbl, profLbl, modLbl);
    card.appendChild(grid);

    const promptLbl = el(doc, 'label', 'wf-field', tt('Adım İstemi (Prompt)'));
    const promptInp = el(doc, 'textarea', 'wf-input wf-textarea');
    promptInp.rows = 2;
    promptInp.maxLength = PROMPT_MAX;
    promptInp.placeholder = tt('Bu adımdaki ajanın ne yapacağını yazın...');
    promptInp.value = step.prompt || '';
    promptInp.dataset.tbStepField = 'prompt';
    promptInp.addEventListener('input', () => { step.prompt = promptInp.value; });
    promptLbl.appendChild(promptInp);
    card.appendChild(promptLbl);

    const flagsRow = el(doc, 'div', 'tb-flags');
    const prevLbl = el(doc, 'label', 'check small');
    const prevChk = el(doc, 'input');
    prevChk.type = 'checkbox';
    prevChk.checked = hasEarlierAgent ? Boolean(step.usePrevious) : false;
    prevChk.disabled = !hasEarlierAgent;
    prevChk.dataset.tbStepField = 'usePrevious';
    prevChk.addEventListener('change', () => { step.usePrevious = prevChk.checked; });
    prevLbl.append(prevChk, doc.createTextNode(tt(' Önceki adımın çıktısını girdi olarak al')));

    const briefLbl = el(doc, 'label', 'check small');
    const briefChk = el(doc, 'input');
    briefChk.type = 'checkbox';
    briefChk.checked = step.useBrief !== false;
    briefChk.dataset.tbStepField = 'useBrief';
    briefChk.addEventListener('change', () => { step.useBrief = briefChk.checked; });
    briefLbl.append(briefChk, doc.createTextNode(tt(' Kullanıcı görev özetini (brief) ekle')));

    flagsRow.append(prevLbl, briefLbl);
    card.appendChild(flagsRow);
    return card;
  }

  _downloadWorkflowsJson() {
    const jsonText = this.exportJson();
    if (typeof Blob === 'undefined' || typeof URL === 'undefined' || !URL.createObjectURL) return;
    const blob = new Blob([jsonText], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = this.doc.createElement('a');
    a.href = url;
    a.download = 'workflows.json';
    this.doc.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

