// Bộ máy dịch dùng chung cho panel và bảng điều khiển của app. Cách khớp giống hệt backend/i18n/__init__.py.
// makeI18n({strings: {vi: en}, codes: {vi: 'E_...'}}) -> { tr(text), code(text, status), withCode(msg, code) }
function makeI18n(data) {
  'use strict';
  const VI = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i;
  const PH = /(?<!\{)\{(\d+)\}(?!\})/g;
  const DEFAULT_CODES = { 400: 'E_BAD_REQUEST', 401: 'E_AUTH', 403: 'E_FORBIDDEN', 404: 'E_NOT_FOUND', 409: 'E_CONFLICT',
    413: 'E_TOO_LARGE', 422: 'E_VALIDATION', 429: 'E_BUSY', 500: 'E_INTERNAL', 503: 'E_UNAVAILABLE' };
  const lit = (s) => s.replace(/\{\{/g, '{').replace(/\}\}/g, '}');
  // khoảng trắng trong mẫu khớp mềm (chữ đã vào trang có thể mất/thêm dấu cách ở đầu, cuối)
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s*');
  const wordsOf = (lits) => [...new Set(lits.join(' ').split(/\s+/).filter(Boolean))].sort((x, y) => y.length - x.length);
  const exact = new Map();
  const codes = new Map();
  const templates = [];
  const strings = (data && data.strings) || {};
  const codeOf = (data && data.codes) || {};
  for (const vi of Object.keys(strings)) {
    const en = strings[vi];
    const code = codeOf[vi] || null;
    PH.lastIndex = 0;
    if (PH.test(vi)) {
      const pieces = vi.split(/(?<!\{)\{(\d+)\}(?!\})/);
      const lits = pieces.filter((_, i) => i % 2 === 0).map(lit);
      if (!lits.some((x) => VI.test(x))) continue;
      const idx = pieces.filter((_, i) => i % 2 === 1).map(Number);
      const pattern = lits.map((l, i) => reEsc(l) + (i < lits.length - 1 ? '([\\s\\S]*?)' : '')).join('');
      templates.push({ words: wordsOf(lits), rx: new RegExp('^\\s*' + pattern + '\\s*$'), en, code, idx, nl: vi.includes('\n'), weight: lits.join('').length });
    } else {
      const braces = vi.includes('{{') || vi.includes('}}');
      const key = (braces ? lit(vi) : vi).trim();
      exact.set(key, (braces ? lit(en) : en).trim());
      if (code) codes.set(key, code);
    }
  }
  templates.sort((a, b) => b.weight - a.weight);
  // Mẫu có thẻ HTML: thêm từng đoạn chữ giữa các thẻ, để chữ đã nằm trong trang (từng nút chữ) vẫn dịch được
  const TAG = /<[^>]+>/;
  for (const vi of Object.keys(strings)) {
    if (!vi.includes('<')) continue;
    const a = vi.split(TAG), b = String(strings[vi]).split(TAG);
    if (a.length !== b.length) continue;
    a.forEach((seg, i) => {
      if (!VI.test(seg) || !seg.trim()) return;
      PH.lastIndex = 0;
      if (PH.test(seg)) {                               // đoạn có chỗ biến: thêm thành mẫu
        const pieces = seg.split(/(?<!\{)\{(\d+)\}(?!\})/);
        const lits = pieces.filter((_, j) => j % 2 === 0).map(lit);
        const idx = pieces.filter((_, j) => j % 2 === 1).map(Number);
        const pattern = lits.map((l, j) => reEsc(l) + (j < lits.length - 1 ? '([\\s\\S]*?)' : '')).join('');
        templates.push({ words: wordsOf(lits), rx: new RegExp('^\\s*' + pattern + '\\s*$'), en: b[i].trim(), code: null, idx, nl: seg.includes('\n'), weight: lits.join('').length });
      } else if (!exact.has(lit(seg).trim())) exact.set(lit(seg).trim(), lit(b[i]).trim());
    });
  }
  templates.sort((a, b) => b.weight - a.weight);

  function fill(t, groups, depth) {
    const vals = {};
    let clean = true;
    t.idx.forEach((n, i) => { const v = tr0(groups[i], depth + 1); if (VI.test(v)) clean = false; vals[n] = v; });
    const text = t.en.replace(PH, (m, n) => (n in vals ? vals[n] : m)).replace(/\{\{/g, '{').replace(/\}\}/g, '}');
    return [text, clean];
  }
  const mmemo = new Map(), tmemo = new Map();
  function match(core, depth) {
    if (mmemo.has(core)) return mmemo.get(core);
    const res = matchRaw(core, depth);
    if (mmemo.size > 20000) mmemo.clear();
    mmemo.set(core, res);
    return res;
  }
  function matchRaw(core, depth) {
    if (exact.has(core)) return [exact.get(core), true];
    const low = core.charAt(0).toLowerCase() + core.slice(1);
    if (low !== core && exact.has(low)) { const en = exact.get(low); return [en.charAt(0).toUpperCase() + en.slice(1), true]; }
    let partial = null;
    const multi = core.includes('\n');
    for (const t of templates) {
      if (multi && !t.nl) continue;                   // nhiều dòng: chỉ mẫu nhiều dòng, còn lại tách dòng
      if (!t.words.every((w) => core.includes(w))) continue;   // lọc nhanh: mọi chữ cố định phải có mặt
      const m = t.rx.exec(core);
      if (!m) continue;
      const [text, clean] = fill(t, m.slice(1), depth);
      if (clean) return [text.trim(), true];
      if (partial === null) partial = text.trim();
    }
    return [partial, false];
  }
  const SENT = /(?<=[.!?])\s+/;
  const CLAUSE = /(?<=[:;])\s+/;
  const SUFFIX = /^([\s\S]*?)(\s*(?:<!--[\s\S]*?-->|\*\([^)]*\)\*))$/;
  const VIG = new RegExp(VI.source, 'gi');
  const COMMA = /,\s+/;
  const MD = /^(\s*(?:#{1,6}\s+|[-*]\s+(?:\[[ x]\]\s+)?|>\s?|\d+\.\s+)?)([\s\S]*?)(\s*)$/;
  function tr0(text, depth) {
    if (!text || typeof text !== 'string' || !VI.test(text) || depth > 40) return text;
    if (tmemo.has(text)) return tmemo.get(text);
    const res = tr1(text, depth);
    if (tmemo.size > 20000) tmemo.clear();
    tmemo.set(text, res);
    return res;
  }
  function tr1(text, depth) {
    const lead = text.slice(0, text.length - text.trimStart().length);
    const trail = text.slice(text.trimEnd().length);
    const core0 = text.trim();
    const cm = /^([\s\S]*?)(\s*\(E_[A-Z0-9_]+\))$/.exec(core0);   // giữ nguyên mã lỗi ở cuối: "... (E_GPU_OOM)"
    if (cm && cm[1]) return lead + tr0(cm[1], depth) + cm[2] + trail;
    const core = core0;
    const [hit, clean] = match(core, depth);
    if (clean) return lead + hit + trail;
    let best = hit;
    if (core.includes('\n')) {
      const flat = core.replace(/\s+/g, ' ');            // đoạn văn xuống dòng trong HTML
      const [h2, c2] = match(flat, depth);
      if (c2) return lead + h2 + trail;
    }
    if (core.includes('\n')) return lead + core.split('\n').map((l) => tr0(l, depth + 1)).join('\n') + trail;
    const seg = segment(core, depth);
    if (best === null || count(seg) < count(best)) best = seg;   // khớp một phần hay tách đoạn: lấy bản ít tiếng Việt hơn
    return lead + best + trail;
  }
  const count = (x) => (x.match(VIG) || []).length;
  // Tách ở ranh giới câu/vế, rồi ghép lại đoạn liền nhau dài nhất có trong bộ dịch
  function spans(core, depth) {
    const pieces = core.split(/(?<=[.!?:;])(\s+)/);
    const texts = pieces.filter((_, i) => i % 2 === 0), seps = pieces.filter((_, i) => i % 2 === 1).concat(['']);
    const n = texts.length;
    if (n < 2) return null;
    const out = [];
    let i = 0;
    while (i < n) {
      let j = n;
      for (j = Math.min(n, i + 8); j > i; j--) {     // đoạn ghép tối đa 8 câu/vế
        let cand = '';
        for (let k = i; k < j; k++) cand += texts[k] + (k < j - 1 ? seps[k] : '');
        if (j - i === 1) { out.push(tr0(cand, depth + 1) + seps[j - 1]); break; }
        const [hit, clean] = match(cand.trim(), depth);
        if (clean) { out.push(hit + seps[j - 1]); break; }
      }
      i = j;
    }
    return out.join('');
  }
  // Không khớp nguyên câu: tách theo cấu trúc (đầu dòng Markdown, đuôi chú thích, ô bảng, câu, vế, danh sách, ngoặc)
  function segment(core, depth) {
    const m = MD.exec(core);
    if (m && m[1] && m[2]) return m[1] + tr0(m[2], depth + 1) + m[3];
    const sm = SUFFIX.exec(core);
    if (sm && sm[1].trim()) return tr0(sm[1], depth + 1) + tr0(sm[2], depth + 1);
    if (core.startsWith('|') && core.endsWith('|') && core.split('|').length - 1 >= 3) {
      return '|' + core.slice(1, -1).split('|').map((c) => tr0(c, depth + 1)).join('|') + '|';
    }
    let out = spans(core, depth);
    if (out === null) { const parts = core.split(COMMA); out = parts.length > 1 ? parts.map((p) => tr0(p, depth + 1)).join(', ') : core; }
    if (VI.test(out)) out = out.replace(/\(([^()]*)\)/g, (mm, inner) => { const t = tr0(inner, depth + 1); return '(' + (VI.test(t) ? inner : t) + ')'; });
    return out;
  }
  const cache = new Map();
  function tr(text) {
    if (typeof text !== 'string' || !VI.test(text)) return text;
    if (cache.has(text)) return cache.get(text);
    const out = tr0(text, 0);
    if (cache.size > 5000) cache.clear();
    cache.set(text, out);
    return out;
  }
  function code(text, status) {
    if (typeof text === 'string' && text.trim()) {
      const core = text.trim();
      const tail = /\((E_[A-Z0-9_]+)\)$/.exec(core);   // thông báo đã kèm mã
      if (tail) return tail[1];
      if (codes.has(core)) return codes.get(core);
      for (const t of templates) if (t.code && t.words.every((w) => core.includes(w)) && t.rx.test(core)) return t.code;
      for (const part of core.split(SENT)) if (codes.has(part.trim())) return codes.get(part.trim());
    }
    return DEFAULT_CODES[status || 500] || `E_HTTP_${status}`;
  }
  const withCode = (msg, c) => (c && !String(msg).includes(c) ? `${msg} (${c})` : String(msg));
  return { tr, code, withCode, hasVietnamese: (s) => VI.test(s || '') };
}
