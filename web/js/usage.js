// Pixel Office v3 — token counters, cost labels and budget gauges (V3.md §10, §11.3). Plain ES module.
// Number formatting is tr-TR: grouping "182.340", compact "12,4 B" (bin) / "1,2 Mn" (milyon), currency "0,18 $".
// Every function returns plain strings or DOM nodes built with textContent (device data is untrusted).

const NF0 = new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 0 });
const NF1 = new Intl.NumberFormat('tr-TR', { minimumFractionDigits: 0, maximumFractionDigits: 1 });
const NF2 = new Intl.NumberFormat('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const NF4 = new Intl.NumberFormat('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 4 });

const fin = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** 182340 → "182.340" */
export function fmtInt(n) {
  const v = fin(n);
  return v === null ? '—' : NF0.format(Math.round(v));
}

/** 950 → "950", 12400 → "12,4 B", 1240000 → "1,2 Mn", 3.1e9 → "3,1 Mr" */
export function fmtCompact(n) {
  const v = fin(n);
  if (v === null) return '—';
  const a = Math.abs(v);
  if (a < 1000) return NF0.format(Math.round(v));
  if (a < 1e6) return `${NF1.format(v / 1e3)} B`;
  if (a < 1e9) return `${NF1.format(v / 1e6)} Mn`;
  return `${NF1.format(v / 1e9)} Mr`;
}

/** 0.18 → "0,18 $", 0.0042 → "0,0042 $", null → "—" */
export function fmtUsd(x) {
  const v = fin(x);
  if (v === null) return '—';
  if (v !== 0 && Math.abs(v) < 0.01) return `${NF4.format(v)} $`;
  return `${NF2.format(v)} $`;
}

/** Budget/token metric (§10.4): in + out + cacheWrite + cacheRead × 0.1. */
export function weightedTokens(u) {
  if (!u) return 0;
  const n = (k) => (fin(u[k]) || 0);
  return n('inTok') + n('outTok') + n('cacheWrite') + n('cacheRead') * 0.1;
}

export const WEIGHT_NOTE = 'Token = giriş + çıkış + önbelleğe yazılan + önbellekten okunan × 0,1 (önbellek okumaları ucuz olduğu için 0,1 ağırlıkla sayılır).';

/** 125000 → "2 dk 5 sn"; 4000 → "4 sn"; 3 700 000 → "1 sa 1 dk" */
export function fmtDuration(ms) {
  const sec = Math.max(0, Math.floor((fin(ms) || 0) / 1000));
  if (sec < 60) return `${sec} sn`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} dk ${sec % 60} sn`;
  return `${Math.floor(min / 60)} sa ${min % 60} dk`;
}

/** Running clock "1:12" / "1:02:03". */
export function fmtClock(ms) {
  const sec = Math.max(0, Math.floor((fin(ms) || 0) / 1000));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const two = (x) => String(x).padStart(2, '0');
  return h ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`;
}

export const ENGINE_LABELS = { claude: 'Claude Code', ollama: 'Ollama', openai: 'OpenAI uyumlu' };

/**
 * Cost text with the honesty labels of §10.1/§11.3.
 * @param {number|null} usd
 * @param {'claude-estimate'|'local'|'unknown'|string|null} basis
 * @param {'subscription'|'api_key'|'other'|'unknown'|null} auth  device.info.engines.claude.auth
 * → {text, title}
 */
export function costLabel(usd, basis, auth = null) {
  if (basis === 'local') {
    return { text: 'maliyet yok (yerel model)', title: 'Yerel model: maliyet yok. Token sayıları Ollama\'dan gelir.' };
  }
  if (basis === 'claude-estimate') {
    const claudeTip = 'Bu tutar Claude Code\'un kendi tahminidir. Abonelikte bu tutar faturalanmaz; abonelik limitinizin ne kadarının kaldığı bilinemez.';
    if (fin(usd) === null) return { text: 'maliyet bilinmiyor', title: claudeTip };
    const tag = auth === 'subscription' ? 'API eşdeğeri tahmini' : 'tahmini';
    return { text: `≈ ${fmtUsd(usd)} (${tag})`, title: claudeTip };
  }
  if (fin(usd) !== null && usd > 0) return { text: `≈ ${fmtUsd(usd)} (tahmini)`, title: 'Tahmini değer.' };
  return { text: 'maliyet bilinmiyor', title: 'Bu motor için maliyet bilgisi yok.' };
}

/** Cost label for a sum over a period (agent or device): basis follows the engine(s). */
export function totalCostLabel(usd, engine, auth) {
  if (engine === 'ollama' || engine === 'openai') return costLabel(usd, 'local', auth);
  if (!fin(usd)) return { text: '≈ 0,00 $', title: 'Bu dönemde Claude maliyeti yok.' };
  return costLabel(usd, 'claude-estimate', auth);
}

/** Live counter line: "12,4 B token" (+ title with the components). */
export function liveTokensText(live) {
  if (!live) return { text: '0 token', title: WEIGHT_NOTE };
  const w = weightedTokens(live);
  const title = `Giriş ${fmtInt(live.inTok || 0)} · çıkış ${fmtInt(live.outTok || 0)} · önbelleğe yazılan ${fmtInt(live.cacheWrite || 0)} · önbellekten okunan ${fmtInt(live.cacheRead || 0)}\n${WEIGHT_NOTE}`;
  return { text: `${fmtCompact(w)} token${live.estimated ? ' (tahmini)' : ''}`, title };
}

const PERIOD_ADJ = { day: 'Günlük', week: 'Haftalık', month: 'Aylık' };
const PERIOD_RESET = { day: 'gece yarısı sıfırlanır', week: 'pazartesi sıfırlanır', month: 'ay başında sıfırlanır' };
export const PERIOD_NOUN = { day: 'Bugün', week: 'Bu hafta', month: 'Bu ay' };

/** "Günlük bütçe %62 (1,2 Mn / 2 Mn token)" etc. → {label, state, pct, note} */
export function budgetText(b) {
  const pct = Math.max(0, Math.min(999, Math.round(fin(b.pct) ?? 0)));
  const state = ['ok', 'warn', 'over'].includes(b.state) ? b.state : (pct >= 100 ? 'over' : pct >= 80 ? 'warn' : 'ok');
  const amount = b.unit === 'usd'
    ? `${fmtUsd(b.used)} / ${fmtUsd(b.limit)}`
    : `${fmtCompact(b.used)} / ${fmtCompact(b.limit)} token`;
  const label = `${PERIOD_ADJ[b.period] || 'Bütçe'} bütçe %${pct} (${amount})`;
  let note = '';
  if (state === 'over') {
    note = b.action === 'block'
      ? `Bütçe doldu: yeni görev kabul edilmiyor (${PERIOD_RESET[b.period] || 'dönem başında sıfırlanır'})`
      : 'Bütçe aşıldı (yalnızca uyarı, görevler sürüyor)';
  } else if (state === 'warn') {
    note = 'Bütçenin %80\'i geçildi';
  }
  return { label, state, pct, note };
}

const SCOPE_LABEL = (scope) => {
  if (scope === 'device') return 'Cihaz';
  if (typeof scope === 'string' && scope.startsWith('engine:')) return ENGINE_LABELS[scope.slice(7)] || scope.slice(7);
  if (typeof scope === 'string' && scope.startsWith('agent:')) return 'Ajan';
  return '';
};

/**
 * Gauge element (only for an existing budget, §10.4 "percentage where it means something").
 * Colour is never the only signal: the label text carries the percentage and the state note.
 */
export function gaugeEl(b, { showScope = false } = {}) {
  const t = budgetText(b);
  const wrap = document.createElement('div');
  wrap.className = `gauge gauge-${t.state}`;
  wrap.dataset.state = t.state;
  const lab = document.createElement('div');
  lab.className = 'gauge-label';
  const scope = showScope ? SCOPE_LABEL(b.scope) : '';
  lab.textContent = (scope ? `${scope} · ` : '') + t.label + (b.locked ? ' 🔒' : '');
  if (b.locked) lab.title = 'Bu bütçe cihazdaki connector.json dosyasında tanımlı, buradan değiştirilemez.';
  if (b.unit !== 'usd') lab.title = (lab.title ? lab.title + '\n' : '') + WEIGHT_NOTE;
  const bar = document.createElement('div');
  bar.className = 'gauge-bar';
  bar.setAttribute('role', 'meter');
  bar.setAttribute('aria-valuemin', '0');
  bar.setAttribute('aria-valuemax', '100');
  bar.setAttribute('aria-valuenow', String(Math.min(100, t.pct)));
  bar.setAttribute('aria-label', t.label);
  const fill = document.createElement('i');
  fill.style.width = `${Math.min(100, t.pct)}%`;
  bar.appendChild(fill);
  wrap.append(lab, bar);
  if (t.note) {
    const n = document.createElement('div');
    n.className = 'gauge-note';
    n.textContent = t.note;
    wrap.appendChild(n);
  }
  return wrap;
}

/** Text for usage.claudeLimit (verbatim, with its time) or the "no information" line (§11.3). */
export function claudeLimitText(lim) {
  if (!lim || typeof lim !== 'object') return 'Claude abonelik limiti hakkında bilgi yok.';
  const at = fin(lim.at) ? new Date(lim.at).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }) : '?';
  const st = { allowed: 'sınırın altında', allowed_warning: 'sınıra yakın', rejected: 'sınıra ulaşıldı' }[lim.status]
    || (typeof lim.status === 'string' ? lim.status.slice(0, 40) : 'durum bilinmiyor');
  const parts = [st];
  const u = fin(lim.utilization);
  if (u !== null) parts.push(`%${NF0.format(u <= 1 ? u * 100 : u)} kullanıldı`);
  const r = fin(lim.resetsAt);
  if (r !== null && r > 0) {
    const ms = r > 1e12 ? r : r * 1000;
    parts.push(`${new Date(ms).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })}'da sıfırlanır`);
  }
  return `Claude bildirdi (${at}): ${parts.join(', ')}.`;
}
