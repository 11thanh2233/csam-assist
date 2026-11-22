// ==UserScript==
// @name         SAM3 CVAT Assist
// @namespace    sam3-cvat-assist
// @version      0.9.3
// @description  Gợi ý nhãn bằng SAM3 ngay trên giao diện CVAT: xem trước, duyệt, rồi ghi vào job. Không cần import/export file.
// ---- Trang CVAT mà tool chạy trên đó -------------------------------------------------------
// CVAT của khoá học:
// @match        https://cvat.edufun.ai/*
// CVAT online chính thức:
// @match        https://app.cvat.ai/*
// CVAT chạy trên máy bạn hoặc trong mạng nội bộ (mọi cổng: localhost:8080, 127.0.0.1:8080, 192.168.x.x:8080...):
// @match        http://localhost/*
// @match        http://127.0.0.1/*
// @include      /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\]|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?\/.*$/
//
// THÊM CVAT KHÁC: chọn một dòng mẫu bên dưới, xoá chữ "THEM " ở đầu (để dòng bắt đầu bằng "// @match"),
// rồi thay tên miền bằng địa chỉ CVAT của bạn. Giữ "/*" ở cuối. Lưu script (Ctrl+S) rồi tải lại trang CVAT.
// THEM @match        https://cvat.ten-mien-thu-nhat.com/*
// THEM @match        https://cvat.ten-mien-thu-hai.vn/*
// THEM @match        http://203.0.113.10:8080/*
// (Cách không bị mất khi cài lại script: Tampermonkey > script này > Settings > User matches > thêm địa chỉ.)
// --------------------------------------------------------------------------------------------
// @run-at       document-idle
// @noframes
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @grant        GM_setClipboard
// @grant        GM_notification
// @connect      127.0.0.1
// @connect      localhost
// @connect      *
// ==/UserScript==

/*
 * Cách hoạt động (tóm tắt):
 *   1. Đọc job và frame đang mở từ URL / thanh player của CVAT.
 *   2. Lấy ảnh frame và danh sách label qua REST API của CVAT (dùng phiên đăng nhập sẵn có).
 *   3. Gửi ảnh sang backend SAM3 của bạn; backend trả về các ĐỀ XUẤT nhãn theo guideline.
 *   4. Vẽ đề xuất (box, polygon, polyline, mask, skeleton, track) đè lên canvas để bạn duyệt; chỉ những đề xuất được tick mới ghi vào job
 *      bằng PATCH /api/jobs/{id}/annotations?action=create.
 * Script không gửi dữ liệu đi đâu ngoài backend bạn cấu hình trong tab "Cài đặt".
 */
(function () {
  'use strict';

  const VERSION = '0.9.3';
  const DEFAULTS = {
    backendUrl: 'http://127.0.0.1:8765',
    apiKey: '',
    profileId: 'auto',
    autoPropose: false,
    reloadAfterApply: true,
    confirmApply: true,
    // false: không gửi trường source, CVAT tự ghi giá trị mặc định của nó.
    markSemiAuto: false,
    batchSkipExisting: true,   // cũ, thay bằng batchSkip
    batchSkip: 'label',        // 'label': frame đã có label đó | 'frame': frame đã có bất kỳ nhãn nào | 'none': không bỏ qua
    batchIncludeFlagged: false,
    guideSample: 40,
    guideSendImages: false,
    importUseLlm: false,
    learnEnabled: true,      // ghi lại cách bạn xử lý đề xuất để tool tự học
    useLearned: true,        // áp ngưỡng / chỉnh hộp tool đã học
    qaFindMissing: false,    // Rà cả job: chạy thêm SAM3 để tìm đối tượng có thể bị sót
    trackEngine: 'classic',  // 'classic' | 'sam3_video'
    guidePurpose: '',
    guideTerms: '',
    opacity: 0.3,
    collapsed: false,
    hidden: false,
    canvasSelector: '#cvat_canvas_background',
    overrides: {},
    pos: null,
    // 0.8.0
    prefetch: true,          // chạy sẵn gợi ý cho frame tiếp theo trong lúc bạn duyệt frame này
    keys: {},                // phím tắt tự đặt (Alt + phím), trống = mặc định
    fontScale: 1,
    highContrast: false,
    lang: 'vi',              // 'vi' | 'en'
    tourDone: false,
    preApplyQa: true,        // kiểm luật QA trên các đề xuất đã tick trước khi ghi
    neighborHints: true,     // nhắc khi frame này ít vật hơn frame bên cạnh
    reviewerMode: false,     // tô nhãn do tool ghi mà chưa ai sửa
  };
  const cfg = Object.assign({}, DEFAULTS, GM_getValue('cfg', {}));
  const saveCfg = () => GM_setValue('cfg', cfg);

  const FLAG_NAMES = {
    UNCERTAIN_CLASS: 'Không chắc class',
    UNCERTAIN_BOUNDARY: 'Không chắc biên',
    UNCERTAIN_SCOPE: 'Không chắc phạm vi',
    ATTRIBUTE_CHECK: 'Kiểm tra attribute',
    LOW_VISIBILITY: 'Khó quan sát',
    DUPLICATE: 'Trùng lặp',
    STATE: 'Trạng thái điểm',
    LEFT_RIGHT: 'Trái/phải?',
    TOPOLOGY: 'Thứ tự điểm',
    SCOPE: 'Phạm vi',
    id_check: 'Kiểm tra ID',
    false_track: 'Track nhầm?',
    drift: 'Keyframe dày',
    flag: 'Kiểm tra occluded',
  };
  // Màu điểm skeleton trên lớp phủ: đỏ = điểm L, xanh = điểm R, vàng = điểm giữa.
  // L/R là phía của ẢNH hay của NGƯỜI do profile quy định (guideline HumanPose-17: phía của ảnh).
  const SIDE_COLORS = { left: '#e8473c', right: '#2e6fd6', mid: '#f2c12e' };
  const SHAPES = ['rectangle', 'polygon', 'polyline', 'mask'];
  const PALETTE = ['#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4', '#42d4f4', '#f032e6', '#bfef45', '#469990', '#9a6324'];

  const S = {
    jobId: null, frame: null, job: null, labels: [], resolved: null, profiles: [],
    proposals: [], propFrame: null, warnings: [], imgW: 0, imgH: 0,
    qa: null, qaFrame: null, highlight: null, health: null,
    busy: '', status: '', statusKind: 'info', tab: 'propose', box: null, boxLabelId: null,
    frameCache: null, seq: 0, labelDiag: [], loadError: '',
    batch: {}, tracks: [], trackMeta: null, longTask: '', stopRequested: false, range: null, propSource: '',
    guide: null, guideRunId: null, evidence: null,
    imp: null, imported: null,
    batchPick: null,
    profMgr: null,
    learn: null,       // tóm tắt điều tool đã học (tab Tự học)
    existingIds: {},   // id các nhãn có sẵn trên frame lúc tool đề xuất: để phần tự học không tính chúng là "bỏ sót"
    similar: null,     // lượt "Tìm tương tự" gần nhất: {frame, box, labelId, negatives}
    jobQa: null,       // kết quả Rà cả job     // danh sách profile (đang dùng / đang ẩn) khi mở mục Quản lý profile   // id các label cần gợi ý ở tab Cả job (null = mọi label đang bật)   // guideline đang nạp (bản xem trước) và danh sách guideline đã nạp
  };

  // ------------------------------------------------------------ tiện ích ---
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const normName = (s) => String(s).trim().toLowerCase().replace(/[\s_-]+/g, ' ');

  // Thay toàn bộ con của một phần tử. Bỏ qua null/false: replaceChildren gốc in chúng thành chữ "null" (lỗi trước 0.9.1).
  function put(el, ...children) {
    el.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  }

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'style') el.style.cssText = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k in el && k !== 'list') el[k] = v;
      else el.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  function svgEl(tag, attrs) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }

  function cookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : '';
  }

  function storedToken() {
    try {
      const t = localStorage.getItem('token');
      return t ? t.replace(/^"|"$/g, '') : '';
    } catch (e) {
      return '';
    }
  }

  // ------------------------------------------------------------ CVAT API ---
  async function cvat(method, path, body, asBlob) {
    const attempt = (useToken) => {
      // CVAT chỉ trả JSON dưới kiểu riêng của nó; 'application/json' bị từ chối với lỗi 406.
      const headers = { Accept: asBlob ? '*/*' : 'application/vnd.cvat+json' };
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      const csrf = cookie('csrftoken');
      if (csrf && method !== 'GET') headers['X-CSRFToken'] = csrf;
      if (useToken) headers.Authorization = 'Token ' + storedToken();
      return fetch(location.origin + path, {
        method, headers, credentials: 'include',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    };
    let res = await attempt(false);
    if ((res.status === 401 || res.status === 403) && storedToken()) res = await attempt(true);
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.text()).slice(0, 300); } catch (e) { /* bỏ qua */ }
      throw Object.assign(new Error(I18N.withCode(`CVAT trả lỗi ${res.status} cho ${method} ${path.split('?')[0]}. ${detail}`, 'E_CVAT_HTTP')), { code: 'E_CVAT_HTTP', status: res.status });
    }
    if (asBlob) return res.blob();
    return res.status === 204 ? null : res.json();
  }

  function currentOrg() {
    // Giao diện CVAT lưu organization đang chọn ở đây; job thuộc organization cần tham số này.
    try { return (localStorage.getItem('currentOrganization') || '').replace(/^"|"$/g, ''); } catch (e) { return ''; }
  }

  async function listLabels(query) {
    let path = `/api/labels?${query}&page_size=500`;
    const out = [];
    while (path) {
      const page = await cvat('GET', path);
      out.push(...(Array.isArray(page) ? page : (page && page.results) || []));
      if (page && page.next) {
        const u = new URL(page.next, location.origin);
        path = u.pathname + u.search;
      } else path = null;
    }
    return out;
  }

  // Thử lần lượt: label theo job, theo task, theo project; mỗi cách có và không kèm organization.
  async function fetchLabels(job) {
    const org = currentOrg();
    const scopes = [`job_id=${job.id}`];
    if (job.task_id) scopes.push(`task_id=${job.task_id}`);
    if (job.project_id) scopes.push(`project_id=${job.project_id}`);
    const tried = [];
    for (const scope of scopes) {
      for (const query of org ? [scope, `${scope}&org=${encodeURIComponent(org)}`] : [scope]) {
        try {
          const all = await listLabels(query);
          const top = all.filter((l) => !l.parent_id && !l.has_parent);
          for (const l of top) {
            if (l.type === 'skeleton' && !(l.sublabels && l.sublabels.length)) {
              l.sublabels = all.filter((x) => x.parent_id === l.id || x.parent === l.id);
            }
          }
          tried.push(`/api/labels?${query} → ${all.length} label`);
          if (top.length) return { labels: top, tried };
        } catch (err) {
          tried.push(`/api/labels?${query} → ${String(err.message || err).slice(0, 120)}`);
        }
      }
    }
    return { labels: [], tried };
  }

  async function fetchFrame(number) {
    const blob = await cvat('GET', `/api/jobs/${S.jobId}/data?type=frame&quality=original&number=${number}`, undefined, true);
    const b64 = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(',')[1]);
      r.onerror = () => reject(r.error);
      r.readAsDataURL(blob);
    });
    const bmp = await createImageBitmap(blob);
    const out = { b64, w: bmp.width, h: bmp.height };
    if (bmp.close) bmp.close();
    return out;
  }

  async function getFrame() {
    const key = `${S.jobId}:${S.frame}`;
    if (S.frameCache && S.frameCache.key === key) return S.frameCache;
    S.frameCache = Object.assign({ key }, await fetchFrame(S.frame));
    return S.frameCache;
  }

  async function frameShapes() {
    const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
    return {
      shapes: (ann.shapes || []).filter((s) => s.frame === S.frame),
      tracks: (ann.tracks || []).length,
      existing: existingAt(ann, S.frame),
    };
  }

  // Nhãn đang có ở một frame, để backend không đề xuất trùng: shape của frame đó, cộng với vị trí của các track
  // tại frame đó (CVAT nội suy tuyến tính giữa hai keyframe; hết track hoặc 'outside' thì không tính).
  function existingAt(ann, frame) {
    const out = (ann.shapes || []).filter((s) => s.frame === frame);
    for (const tr of ann.tracks || []) {
      const keys = (tr.shapes || []).slice().sort((a, b) => a.frame - b.frame);
      let k0 = null; let k1 = null;
      for (const k of keys) { if (k.frame <= frame) k0 = k; else { k1 = k; break; } }
      if (!k0 || k0.outside) continue;
      let points = k0.points;
      if (k1 && k1.frame !== k0.frame && k0.type === 'rectangle' && k1.points && k1.points.length === k0.points.length) {
        const u = (frame - k0.frame) / (k1.frame - k0.frame);
        points = k0.points.map((v, i) => v + u * (k1.points[i] - v));
      }
      out.push({ type: k0.type, label_id: tr.label_id, frame, points, elements: [], from_track: true });
    }
    return out;
  }

  // ------------------------------------------------------------- backend ---
  function backend(method, path, body) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: cfg.backendUrl.replace(/\/+$/, '') + path,
        headers: { 'Content-Type': 'application/json', 'X-API-Key': cfg.apiKey || '', 'X-Lang': cfg.lang || 'vi' },
        data: body === undefined ? undefined : JSON.stringify(body),
        timeout: 300000,
        onload: (r) => {
          let data = null;
          try { data = JSON.parse(r.responseText); } catch (e) { /* không phải JSON */ }
          if (r.status >= 200 && r.status < 300) return resolve(data);
          const detail = data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : '';
          // Lỗi luôn kèm mã kỹ thuật cố định (E_...), lời nhắn theo ngôn ngữ đã chọn
          const code = (data && data.code) || (detail ? I18N.code(detail, r.status) : 'E_BACKEND_HTTP');
          const err = new Error(I18N.withCode(detail || `Backend trả lỗi ${r.status}. `, code));
          err.code = code;
          reject(err);
        },
        onerror: () => reject(Object.assign(new Error(I18N.withCode(`Không kết nối được backend tại ${cfg.backendUrl}. Kiểm tra backend đã chạy và địa chỉ trong Cài đặt panel (nút bánh răng).`, 'E_BACKEND_UNREACHABLE')), { code: 'E_BACKEND_UNREACHABLE' })),
        ontimeout: () => reject(Object.assign(new Error(I18N.withCode('Backend không phản hồi sau 5 phút.', 'E_BACKEND_TIMEOUT')), { code: 'E_BACKEND_TIMEOUT' })),
      });
    });
  }

  function labelPayload() {
    return S.labels.map((l) => ({
      id: l.id, name: l.name, type: l.type || 'any',
      sublabels: (l.sublabels || []).map((x) => ({ id: x.id, name: x.name })),
      attributes: (l.attributes || []).map((a) => ({
        id: a.id, name: a.name, input_type: a.input_type, default_value: a.default_value, values: a.values,
      })),
    }));
  }

  function overridePayload() {
    const out = {};
    for (const l of S.labels) {
      const ov = cfg.overrides[normName(l.name)];
      if (ov) out[String(l.id)] = ov;
    }
    return out;
  }

  const baseBody = () => ({ profile_id: cfg.profileId, labels: labelPayload(), overrides: overridePayload(), use_learned: cfg.useLearned !== false, user: S.user || '', use_custom: !!cfg.useCustom });

  function persist(kind) {
    if (!S.jobId) return;
    const key = `${kind}:${S.jobId}`;
    const value = kind === 'batch' ? S.batch : { tracks: S.tracks, meta: S.trackMeta };
    const empty = kind === 'batch' ? !Object.keys(S.batch).length : !S.tracks.length;
    try {
      const text = JSON.stringify(value);
      if (empty) GM_setValue(key, null);
      else if (text.length < 12e6) GM_setValue(key, value);
      else setStatus('Kết quả quá lớn để giữ lại khi tải lại trang. Hãy ghi hoặc duyệt trước khi tải lại.', 'error');
    } catch (e) { /* bỏ qua */ }
  }

  function restore() {
    S.batch = GM_getValue(`batch:${S.jobId}`, null) || {};
    S.batchPick = null;
    const t = GM_getValue(`tracks:${S.jobId}`, null);
    S.tracks = (t && t.tracks) || [];
    S.trackMeta = (t && t.meta) || null;
    S.guideRunId = GM_getValue(`guide:${S.jobId}`, null);
    S.guide = null; S.evidence = null;
  }

  // ------------------------------------------------------- ngữ cảnh CVAT ---
  // Script được phép chạy trên localhost và mạng nội bộ, nơi có thể có trang khác không phải CVAT:
  // chỉ hiện panel khi trang thật sự là CVAT.
  let cvatSeen = false;
  function isCvatPage() {
    if (!cvatSeen) cvatSeen = !!document.querySelector('[class^="cvat-"], [class*=" cvat-"], #cvat_canvas_wrapper');
    return cvatSeen;
  }

  function readCtx() {
    const m = location.pathname.match(/\/tasks\/(\d+)\/jobs\/(\d+)/);
    if (!m || !isCvatPage()) return null;
    let frame = null;
    const input = document.querySelector('.cvat-player-frame-selector input');
    if (input) {
      // Đang gõ số frame thì chưa tính là đã chuyển frame.
      if (/^\d+$/.test(input.value) && document.activeElement !== input) frame = Number(input.value);
    } else {
      const q = new URLSearchParams(location.search).get('frame');
      if (q !== null && /^\d+$/.test(q)) frame = Number(q);
    }
    return { jobId: Number(m[2]), frame };
  }

  function labelById(id) { return S.labels.find((l) => l.id === id); }

  function goToFrame(number) {
    // Gõ số frame vào ô của thanh player, như khi bạn tự gõ rồi bấm Enter.
    const input = document.querySelector('.cvat-player-frame-selector input');
    if (!input) return setStatus(`Không tìm thấy ô số frame. Hãy tự chuyển tới frame ${number}.`, 'error');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    input.focus();
    setter.call(input, String(number));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.blur();
  }

  // Frame thật của job trong khoảng [from, to]: bỏ frame đã xoá trong CVAT, và với job ground truth chỉ lấy các frame
  // được chọn vào job (included_frames). Không đọc được data/meta thì lấy mọi frame trong khoảng.
  function jobFrames(from, to) {
    const meta = S.jobMeta || {};
    const deleted = new Set(meta.deleted_frames || []);
    const included = Array.isArray(meta.included_frames) ? new Set(meta.included_frames) : null;
    const out = [];
    for (let f = from; f <= to; f++) if (!deleted.has(f) && (!included || included.has(f))) out.push(f);
    return out;
  }

  async function readJobMeta() {
    try { S.jobMeta = await cvat('GET', `/api/jobs/${S.jobId}/data/meta`); } catch (err) { S.jobMeta = null; }
  }

  function jobRange() {
    const start = S.job && Number.isFinite(S.job.start_frame) ? S.job.start_frame : 0;
    const stop = S.job && Number.isFinite(S.job.stop_frame) ? S.job.stop_frame : (S.frame || 0);
    if (!S.range || S.range.job !== S.jobId) S.range = { job: S.jobId, from: start, to: stop };
    return { start, stop, from: Math.max(start, S.range.from), to: Math.min(stop, S.range.to) };
  }

  function labelColor(id) {
    const l = labelById(id);
    if (l && /^#[0-9a-f]{6}$/i.test(l.color || '')) return l.color;
    return PALETTE[Math.abs(id) % PALETTE.length];
  }

  // ------------------------------------------------------------ trạng thái -
  function setStatus(text, kind) {
    S.status = text || '';
    S.statusKind = kind || 'info';
    renderStatus();
  }

  async function run(busyText, fn) {
    if (S.busy) return;
    S.busy = busyText;
    setStatus(busyText, 'busy');
    render();
    try {
      await fn();
      if (S.statusKind === 'busy') setStatus('');
    } catch (err) {
      console.error('[SAM3 Assist]', err);
      setStatus(err && err.message ? err.message : String(err), 'error');
    } finally {
      S.busy = '';
      render();
    }
  }

  // --------------------------------------------------------------- hành động
  async function checkBackend() {
    S.health = null;
    try {
      S.health = await backend('GET', '/health');
      const list = await backend('GET', '/profiles');
      S.profiles = (list && list.profiles) || [];
    } catch (err) {
      S.health = { ok: false, error: err.message };
    }
    renderHeader();
    return S.health && S.health.ok;
  }

  async function resolveLabels() {
    if (!S.labels.length) return;
    S.resolved = await backend('POST', '/resolve', baseBody());
  }

  async function readJob() {
    const m = location.pathname.match(/\/tasks\/(\d+)\/jobs\/(\d+)/);
    S.loadError = '';
    try {
      S.job = await cvat('GET', `/api/jobs/${S.jobId}`);
    } catch (err) {
      S.loadError = String(err.message || err);
      S.job = { id: S.jobId, task_id: m ? Number(m[1]) : null }; // vẫn thử đọc label theo địa chỉ trang
    }
    const res = await fetchLabels(S.job);
    S.labels = res.labels; S.labelDiag = res.tried;
    if (S.frame === null) S.frame = S.job.start_frame || 0;
    if (!S.labels.some((l) => l.id === S.boxLabelId)) S.boxLabelId = S.labels.length ? S.labels[0].id : null;
  }

  const NO_LABELS = 'CVAT không trả về label nào cho job này. Xem chi tiết ở tab Label.';

  async function loadJob() {
    S.job = null; S.labels = []; S.resolved = null; S.jobMeta = null;
    clearProposals();
    await run('Đang đọc job và label từ CVAT…', async () => {
      if (!S.user) { try { S.user = (await cvat('GET', '/api/users/self')).username || ''; } catch (e) { S.user = ''; } }
      await readJob();
      await readJobMeta();
      restore();
      loadBatchFrame();
      const ok = await checkBackend();
      if (ok) await resolveLabels();
      if (!S.labels.length) setStatus(NO_LABELS, 'error');
      else if (!ok) setStatus(S.health.error, 'error');
      // Tự học: so các nhãn tool đã ghi ở lần trước với những gì bạn đã sửa. Lỗi ở đây không được chặn việc mở job.
      if (ok && S.labels.length) { try { await reviewLearning(true); } catch (err) { /* bỏ qua */ } }
    });
    if (cfg.reviewerMode) loadReviewer();
    if (S.resolved && S.health && S.health.ok && consumeAutoNext()) propose();   // vừa "Ghi và sang frame tiếp"
  }

  function clearProposals() {
    S.proposals = []; S.warnings = []; S.propFrame = null; S.highlight = null; S.propSource = '';
    S.qa = null; S.qaFrame = null;
    drawOverlay();
  }

  // Frame có kết quả từ lượt "gợi ý cho cả job": hiện ngay để duyệt, không chạy lại model.
  function loadBatchFrame() {
    const entry = S.batch[S.frame];
    if (!entry || (S.proposals.length && S.propFrame === S.frame)) return;
    S.proposals = entry.proposals; S.warnings = entry.warnings || [];
    S.propFrame = S.frame; S.imgW = entry.width; S.imgH = entry.height; S.propSource = 'batch';
    drawOverlay();
  }

  async function propose() {
    await run('SAM3 đang phân tích frame…', async () => {
      const seq = ++S.seq;
      const frame = S.frame;
      const t0 = Date.now();
      let ann = null;
      try { ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`); } catch (e) { /* vẫn chạy được khi không đọc được annotation */ }
      const existing = ann ? existingAt(ann, frame) : [];
      let res = pfTake(frame, existing);
      let from = res ? 'prefetch' : '';
      if (!res && pfInflight[frame]) {                     // đang chạy sẵn đúng frame này: đợi nó thay vì chạy lại
        setStatus('Đang đợi gợi ý đã chạy sẵn cho frame này…', 'busy');
        const out = await pfInflight[frame];
        if (out && exSig(out.existing) === exSig(existing)) { res = out.res; from = 'prefetch'; }
      }
      if (!res) {
        const fr = await getFrame();
        res = await backend('POST', '/propose', Object.assign(baseBody(), { image_b64: fr.b64, existing }));
      }
      if (seq !== S.seq || frame !== S.frame) return; // người dùng đã chuyển frame
      S.proposals = stamp(res.proposals || [], 'frame');
      S.existingIds[frame] = existing.filter((x) => x.id !== undefined).map((x) => x.id);
      S.warnings = res.warnings || [];
      S.propFrame = frame; S.imgW = res.width; S.imgH = res.height;
      S.qa = null; S.sel = null;
      S.lastAnn = ann;
      S.hints = neighborHints(ann, frame);
      bumpStats({ shown: S.proposals.length, job: { id: S.jobId, add: { shown: S.proposals.length } } });
      const flagged = S.proposals.filter((p) => p.flags.length).length;
      const took = from === 'prefetch' ? 'ngay (đã chạy sẵn)' : `trong ${res.seconds}s`;
      setStatus(`${S.proposals.length} đề xuất ${took}, ${flagged} cần xem kỹ. Duyệt rồi bấm "Ghi" (hoặc ${keyName(keyOf('applyNext'))}: ghi và sang frame tiếp).`, 'ok');
      drawOverlay();
      schedulePrefetch(frame);
      if (Date.now() - t0 > 0) S.lastProposeMs = Date.now() - t0;
    });
  }

  function startBox(mode) {
    if (!canvasEl()) {
      setStatus('Không tìm thấy canvas của CVAT nên không vẽ hộp được. Xem mục "Xử lý sự cố" trong README.', 'error');
      return;
    }
    run('Đang lấy ảnh frame…', async () => {
      const fr = await getFrame();
      S.imgW = fr.w; S.imgH = fr.h;
      S.box = { mode, start: null, cur: null };
      setStatus('Kéo chuột trên ảnh để vẽ một hộp quanh đối tượng. Esc để huỷ.', 'info');
      drawOverlay();
    });
  }

  async function finishBox(box, mode, negatives) {
    await run(negatives ? 'SAM3 đang tìm lại, bỏ các vật giống hộp loại trừ…' : 'SAM3 đang tìm đối tượng trong hộp…', async () => {
      const frame = S.frame;
      const fr = await getFrame();
      let existing = [];
      if (mode === 'similar') {
        try { existing = (await frameShapes()).existing; } catch (e) { /* không đọc được annotation: vẫn tìm */ }
        S.existingIds[frame] = existing.filter((x) => x.id !== undefined).map((x) => x.id);
      }
      const labelId = negatives && S.similar ? S.similar.labelId : S.boxLabelId;
      const res = await backend('POST', '/interactive', Object.assign(baseBody(), {
        image_b64: fr.b64, label_id: labelId, box, mode, existing, negatives: negatives || [],
      }));
      if (frame !== S.frame) return;
      if (S.propFrame !== frame) { S.proposals = []; S.warnings = []; }
      S.propFrame = frame;
      if (mode === 'similar') {
        // Lượt tìm mới (hoặc tìm lại sau khi loại trừ): thay kết quả của lượt trước
        S.proposals = S.proposals.filter((p) => p.source !== 'similar');
        S.similar = { frame, box, labelId, negatives: negatives || [] };
      }
      let next = S.proposals.reduce((m, p) => Math.max(m, p.pid), 0);
      for (const p of stamp(res.proposals || [], mode === 'similar' ? 'similar' : 'box')) { p.pid = ++next; S.proposals.push(p); }
      S.warnings = S.warnings.concat(res.warnings || []);
      const extra = mode === 'similar' ? ' Vật nào bị nhận nhầm: bấm "Loại trừ" ở dòng của nó để SAM3 bỏ mọi vật giống vậy.' : '';
      setStatus(`Thêm ${(res.proposals || []).length} đề xuất từ hộp vừa vẽ.${negatives && negatives.length ? ` Đang loại trừ ${negatives.length} mẫu.` : ''}${extra}`, 'ok');
      drawOverlay();
    });
  }

  // "Loại trừ": vật này bị nhận nhầm -> thành hộp mẫu âm, tìm lại. Cũng là một tín hiệu cho phần tự học (đề xuất sai).
  async function excludeSimilar(p) {
    const sim = S.similar;
    if (!sim || sim.frame !== S.frame) return setStatus('Lượt "Tìm tương tự" này đã cũ. Vẽ lại hộp mẫu.', 'error');
    sendLearnEvents([learnEvent('rejected', p, S.frame)]);
    await finishBox(sim.box, 'similar', sim.negatives.concat([p.bbox]));
  }

  // ------------------------------------------------------------- tự học ---
  // Đánh dấu đề xuất vừa nhận: nguồn (frame/box/similar/batch) và label ban đầu (để biết người dùng có đổi class không).
  function stamp(list, source) {
    for (const p of list) { p.source = source; p.orig_label_id = p.label_id; p.orig_label_name = p.label_name; }
    return list;
  }

  function cfgOf(labelId) {
    return ((S.resolved && S.resolved.labels) || []).find((c) => c.label_id === labelId) || {};
  }

  function learnEvent(kind, p, frame, extra) {
    const labelId = kind === 'rejected' ? (p.orig_label_id || p.label_id) : p.label_id;
    const c = cfgOf(labelId);
    return Object.assign({
      kind, profile_id: S.resolved && S.resolved.profile_id, job_id: S.jobId, frame,
      label_id: labelId, label_name: kind === 'rejected' ? (p.orig_label_name || p.label_name) : p.label_name,
      type: p.type, score: p.score, bbox: p.bbox, threshold: c.threshold, source: p.source || 'frame',
      box_adjust: p.box_adjust || null, existing_ids: S.existingIds[frame] || [],
    }, extra || {});
  }

  // Sau khi ghi: đề xuất đã ghi (kèm id CVAT trả về, theo đúng thứ tự gửi), đề xuất bạn bỏ tick, đề xuất bạn đổi class.
  function learnFromApply(written, created, others, frameOf) {
    const events = [];
    written.forEach((p, i) => {
      const shape = created[i];
      const relabeled = p.orig_label_id && p.orig_label_id !== p.label_id;
      events.push(learnEvent('applied', p, frameOf(p), { cvat_id: shape && shape.id, score: relabeled ? null : p.score }));
      if (relabeled) events.push(learnEvent('rejected', p, frameOf(p), { new_label: p.label_name }));
    });
    for (const p of others) if (!p.checked && p.userTouched) events.push(learnEvent('rejected', p, frameOf(p)));
    sendLearnEvents(events);
  }

  async function sendLearnEvents(events) {
    if (cfg.learnEnabled === false || !events.length || !(S.resolved && S.resolved.profile_id)) return;
    try { await backend('POST', '/learn/events', { events, user: S.user || '' }); } catch (err) { /* tự học không được làm hỏng việc ghi nhãn */ }
  }

  // Đối chiếu nhãn tool đã ghi với annotation hiện tại của job (sau khi bạn sửa), rồi học lại.
  async function reviewLearning(silent) {
    if (cfg.learnEnabled === false || !S.jobId || !(S.health && S.health.ok)) return null;
    const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
    const shapes = (ann.shapes || []).map((x) => ({ id: x.id, frame: x.frame, label_id: x.label_id, type: x.type,
      points: x.type === 'mask' ? (x.points || []).slice(-4) : x.points, elements: x.elements }));
    const res = await backend('POST', '/learn/review', { job_id: S.jobId, shapes, user: S.user || '' });
    S.learn = res.learned;
    if (res.reviewed) await resolveLabels();
    if (!silent) {
      setStatus(res.reviewed ? `Đã đối chiếu ${res.reviewed} đề xuất của job này với nhãn hiện tại và học lại.`
        : 'Job này chưa có đề xuất nào do tool ghi (hoặc bạn đã tắt ghi nhận).', 'ok');
    }
    return res;
  }

  async function loadLearn() {
    try { S.learn = await backend('GET', `/learn/summary?user=${encodeURIComponent(S.user || '')}`); } catch (err) { S.learn = { labels: [], notes: [] }; }
    render();
  }

  async function learnAction(path, body, msg) {
    await run('Đang cập nhật phần tự học…', async () => {
      S.learn = await backend('POST', path, Object.assign({ user: S.user || '' }, body));
      await resolveLabels();
      setStatus(msg, 'ok');
    });
  }

  function renderLearn() {
    const out = [h('p', { class: 'muted small-text', style: 'margin:0', text:
      'Tool học từ cách BẠN xử lý đề xuất: nhãn giữ nguyên, nhãn bị kéo lại, đổi class, xoá, bỏ tick, và nhãn bạn tự vẽ thêm. '
      + 'Từ đó nó chỉnh ngưỡng tin cậy và cách đặt hộp cho từng label. Model SAM3 không bị huấn luyện lại; '
      + 'chỉnh tay ở tab Label luôn thắng điều tool đã học.' })];
    const check = (label, key, after) => h('label', { class: 'check' },
      h('input', { type: 'checkbox', checked: cfg[key] !== false, onchange: (ev) => { cfg[key] = ev.target.checked; saveCfg(); if (after) after(); } }),
      h('span', { text: label }));
    const st = statsToday();
    out.push(h('div', { class: 'note' }, h('strong', { text: 'Hôm nay' }),
      h('div', { class: 'small-text', text: `${st.applied} nhãn đã ghi · ${st.rejected} đề xuất bị bỏ · ${st.frames} frame · ước tính tiết kiệm ${st.minutes} phút` }),
      h('div', { class: 'row' }, h('button', { class: 'small', type: 'button', text: 'Xuất báo cáo CSV', onclick: exportReport }))));
    out.push(...renderCustomModel());
    out.push(check('Ghi lại cách tôi xử lý đề xuất để tool học', 'learnEnabled'));
    out.push(check('Dùng điều tool đã học khi gợi ý (ngưỡng, chỉnh hộp)', 'useLearned', () => { clearProposals(); run('Đang cập nhật label…', resolveLabels); }));
    out.push(h('div', { class: 'row' },
      h('button', { type: 'button', disabled: !!S.busy || cfg.learnEnabled === false, text: 'Đối chiếu job này ngay',
        title: 'So các nhãn tool đã ghi với nhãn hiện tại (sau khi bạn sửa) rồi học lại. Tool cũng tự làm việc này mỗi lần mở job.',
        onclick: () => run('Đang đối chiếu…', () => reviewLearning(false)) })));
    if (!S.learn) { loadLearn(); return out; }
    const pid = S.resolved && S.resolved.profile_id;
    const rows = (S.learn.labels || []).filter((l) => l.profile_id === pid);
    out.push(h('h2', { text: `Đã học cho profile đang dùng (${rows.length} label)` }));
    if (!rows.length) {
      out.push(h('p', { class: 'muted', style: 'margin:0', text: 'Chưa có dữ liệu. Ghi vài nhãn bằng tool, sửa chúng như bình thường trong CVAT, lưu lại, rồi mở lại job (hoặc bấm Đối chiếu).' }));
    }
    for (const l of rows) {
      const c = l.counts;
      const lines = [];
      if (l.threshold) lines.push(`Ngưỡng ${l.base_threshold ?? '?'} → ${l.threshold}: ${l.threshold_reason}`);
      if (l.box_adjust) lines.push(`Chỉnh hộp: ${l.box_reason}`);
      if (!lines.length) lines.push(l.n < 12 ? `Cần thêm dữ liệu (${l.n}/12 đề xuất đã có kết quả).` : 'Chưa thấy cần chỉnh gì.');
      out.push(h('div', { class: 'item', style: 'padding:6px 0;border-bottom:1px solid var(--line, #3a3f47)' },
        h('div', { class: 'row' },
          h('strong', { class: 'grow', text: l.label }),
          h('span', { class: 'muted small-text', text: `đúng ${Math.round(l.precision * 100)}% · tìm được ${Math.round(l.recall * 100)}%` }),
          h('button', { class: 'small', type: 'button', disabled: !!S.busy, text: l.enabled ? 'Tắt' : 'Bật',
            title: l.enabled ? 'Không áp điều đã học cho label này (vẫn giữ dữ liệu)' : 'Áp lại điều đã học',
            onclick: () => learnAction('/learn/toggle', { profile_id: l.profile_id, label: l.label, enabled: !l.enabled }, `${l.enabled ? 'Đã tắt' : 'Đã bật'} điều đã học cho "${l.label}".`) }),
          h('button', { class: 'small', type: 'button', disabled: !!S.busy, text: 'Quên', title: 'Xoá dữ liệu và điều đã học của label này',
            onclick: async () => await uiConfirm(`Xoá mọi điều tool đã học về "${l.label}"?`) && learnAction('/learn/forget', { profile_id: l.profile_id, label: l.label }, `Đã quên "${l.label}".`) })),
        h('div', { class: 'muted small-text', text: `Giữ ${c.kept} · sửa hình ${c.edited} · đổi class ${c.relabeled} · xoá ${c.deleted} · bỏ tick ${c.rejected} · bạn tự vẽ thêm ${c.missed}` }),
        ...lines.map((t) => h('div', { class: 'small-text', style: l.enabled ? '' : 'opacity:.6', text: (l.enabled ? '' : '(đang tắt) ') + t }))));
    }
    const notes = (S.learn.notes || []).filter((n) => !pid || n.profile_id === pid);
    if (notes.length) {
      out.push(h('h2', { text: 'Gợi ý (chưa tự áp dụng)' }));
      for (const n of notes) out.push(h('div', { class: 'note', text: (n.kind === 'tool' ? 'Tool tự thấy: ' : '') + n.text }));
    }
    out.push(h('div', { class: 'row' }, h('button', { type: 'button', disabled: !!S.busy || !(S.learn.labels || []).length, text: 'Quên tất cả',
      onclick: async () => await uiConfirm('Xoá mọi điều tool đã học (mọi profile)?') && learnAction('/learn/forget', {}, 'Đã xoá mọi điều tool đã học.') })));
    return out;
  }

  // ------------------------------------------------------------ rà cả job --
  function frameSizes(frames) {
    const meta = S.jobMeta || {};
    const list = meta.frames || [];
    const sizes = {};
    if (list.length === frames.length) frames.forEach((f, i) => { sizes[f] = [list[i].width, list[i].height]; });
    else if (list.length === 1) for (const f of frames) sizes[f] = [list[0].width, list[0].height];
    return sizes;
  }

  async function runJobQa() {
    const r = jobRange();
    const frames = jobFrames(r.start, r.stop);
    S.stopRequested = false;
    S.longTask = 'qa';
    await run('Đang rà cả job…', async () => {
      await saveCvatUi();
      const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      let fallback = null;
      const sizes = frameSizes(frames);
      if (Object.keys(sizes).length < frames.length) { const fr = await getFrame(); fallback = [fr.w, fr.h]; }
      const res = await backend('POST', '/qa/job', Object.assign(baseBody(), {
        job_id: S.jobId, shapes: ann.shapes || [], frames, sizes,
        width: fallback ? fallback[0] : 0, height: fallback ? fallback[1] : 0,
      }));
      res.missing = 0;
      if (cfg.qaFindMissing) {
        const byFrame = new Map(res.frames.map((x) => [x.frame, x]));
        let done = 0;
        const started = Date.now();
        for (const f of frames) {
          if (S.stopRequested) break;
          const image = await fetchFrame(f);
          const found = await backend('POST', '/propose', Object.assign(baseBody(), { image_b64: image.b64, existing: existingAt(ann, f) }));
          const missed = (found.proposals || []).filter((p) => p.checked && !p.flags.some((x) => x.code === 'DUPLICATE') && p.score >= 0.6);
          if (missed.length) {
            const entry = byFrame.get(f) || { frame: f, findings: [] };
            for (const p of missed) {
              entry.findings.push({ severity: 'warning', code: 'MISSING', bbox: p.bbox,
                message: `Có thể sót "${p.label_name}" (SAM3 tin ${Math.round(p.score * 100)}%, chưa có nhãn nào ở đó).` });
            }
            if (!byFrame.has(f)) { byFrame.set(f, entry); res.frames.push(entry); }
            res.missing += missed.length;
          }
          done += 1;
          const per = (Date.now() - started) / done / 1000;
          setStatus(`Đang tìm đối tượng có thể bị sót: ${done}/${frames.length} frame. Còn khoảng ${Math.round(per * (frames.length - done))} giây.`, 'busy');
        }
        res.frames.sort((a, b) => a.frame - b.frame);
      }
      S.jobQa = res;
      const t = res.totals;
      notify('Rà cả job xong', `Job ${S.jobId}: ${res.checked_frames} frame, ${t.error} lỗi, ${t.warning} cảnh báo.`);
      setStatus(`Rà ${res.checked_frames} frame có nhãn: ${t.error} lỗi, ${t.warning} cảnh báo${cfg.qaFindMissing ? `, ${res.missing} chỗ có thể sót` : ''}.`
        + (res.job_findings.length ? ` ${res.job_findings.length} lưu ý cho cả job.` : ''), t.error ? 'error' : 'ok');
    });
    S.longTask = '';
    render();
  }

  async function issuesForAll() {
    const items = [];
    for (const fr of (S.jobQa && S.jobQa.frames) || []) for (const f of fr.findings) if (f.severity === 'error' || f.code === 'MISSING') items.push([fr.frame, f]);
    if (!items.length) return setStatus('Không có lỗi nào để tạo Issue.', 'ok');
    if (!await uiConfirm(`Tạo ${items.length} Issue trên CVAT (mỗi lỗi một Issue, đặt đúng frame và vị trí)?`)) return;
    await run('Đang tạo Issue…', async () => {
      let n = 0;
      for (const [frame, f] of items) {
        const position = f.bbox && f.bbox.length === 4 ? f.bbox : [0, 0, 10, 10];
        await cvat('POST', '/api/issues', { job: S.jobId, frame, position, message: `[${f.code}] ${f.message}` });
        n += 1;
        setStatus(`Đã tạo ${n}/${items.length} Issue…`, 'busy');
      }
      setStatus(`Đã tạo ${n} Issue. Tải lại trang (F5) để thấy chúng trong CVAT.`, 'ok');
    });
  }

  function renderJobQa() {
    const busy = !!S.busy;
    const agreeOut = renderAgreement(busy);
    const out = [h('h2', { text: 'Rà cả job' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'Chạy mọi kiểm tra ở trên cho tất cả frame của job, cộng: frame chưa có nhãn, label chưa dùng lần nào. Lưu (Ctrl+S) trước khi rà.' }),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!cfg.qaFindMissing, onchange: (ev) => { cfg.qaFindMissing = ev.target.checked; saveCfg(); } }),
        h('span', { text: 'Tìm cả đối tượng có thể bị sót (chạy SAM3 trên từng frame, chậm như Gợi ý cho cả job)' })),
      h('button', { class: 'primary', type: 'button', disabled: busy || !S.resolved, onclick: runJobQa, text: 'Rà cả job' }), ...agreeOut];
    if (S.longTask === 'qa') out.push(h('button', { type: 'button', text: 'Dừng', onclick: () => { S.stopRequested = true; setStatus('Đang dừng sau frame hiện tại…', 'busy'); } }));
    const q = S.jobQa;
    if (!q) return out;
    for (const f of q.job_findings) out.push(h('div', { class: 'note', text: f.message }));
    out.push(h('div', { class: 'row' },
      h('span', { class: 'grow', text: `${q.frames.length} frame cần xem · ${q.totals.error} lỗi · ${q.totals.warning} cảnh báo${q.missing ? ` · ${q.missing} có thể sót` : ''}` }),
      h('button', { class: 'small', type: 'button', disabled: busy, text: 'Tạo Issue cho mọi lỗi', onclick: issuesForAll })));
    out.push(h('ul', {}, q.frames.slice(0, 200).map((fr) => {
      const errors = fr.findings.filter((f) => f.severity === 'error').length;
      return h('li', { class: 'item' },
        h('div', { class: 'row' },
          h('button', { class: 'small', type: 'button', text: `Frame ${fr.frame}`, onclick: () => goToFrame(fr.frame) }),
          h('span', { class: 'grow muted small-text', text: `${errors} lỗi · ${fr.findings.length - errors} khác` + (fr.findings.some((f) => f.by_tool) ? ' · có nhãn do tool tạo' : '') })),
        ...fr.findings.slice(0, 6).map((f) => h('div', { class: 'small-text', text: `${f.severity === 'error' ? '✗' : '•'} ${f.message}` })));
    })));
    if (q.frames.length > 200) out.push(h('p', { class: 'muted small-text', text: `… và ${q.frames.length - 200} frame nữa.` }));
    return out;
  }

  function attributeValues(label, given, onlyImmutable) {
    return (label.attributes || []).filter((a) => !onlyImmutable || !a.mutable).map((a) => {
      let value = given ? given[normName(a.name)] : undefined;
      if (value === undefined) value = a.default_value === undefined || a.default_value === null ? '' : a.default_value;
      if (value === '' && a.input_type !== 'text' && a.values && a.values.length) value = a.values[0];
      return { spec_id: a.id, value: String(value) };
    });
  }

  function toCvatShape(p, frame) {
    const at = frame === undefined ? S.propFrame : frame;
    const label = labelById(p.label_id) || { attributes: [] };
    const shape = {
      type: p.type, frame: at, label_id: p.label_id, points: p.points,
      occluded: !!p.occluded, outside: false, z_order: p.z_order || 0, rotation: 0, group: 0,
      attributes: attributeValues(label, p.attributes, false), elements: [],
    };
    if (p.type === 'skeleton') {
      // Skeleton của CVAT: shape cha không có toạ độ, mỗi điểm là một element kiểu points.
      shape.points = [];
      shape.elements = (p.elements || []).map((e) => ({
        type: 'points', frame: at, label_id: e.label_id, points: [e.x, e.y],
        occluded: !!e.occluded, outside: !!e.outside, z_order: 0, rotation: 0, group: 0, attributes: [],
      }));
    }
    if (cfg.markSemiAuto) shape.source = 'semi-auto';
    return shape;
  }

  async function saveCvatUi() {
    const btn = document.querySelector('.cvat-annotation-header-save-button');
    if (btn && !btn.disabled) { btn.click(); await sleep(1500); }
  }

  function reloadAtFrame(frame) {
    const url = new URL(location.href);
    url.searchParams.set('frame', String(frame));
    history.replaceState(null, '', url);
    location.reload();
  }

  async function applyProposals(opts) {
    const o = opts && opts.nextFrame !== undefined ? opts : {};
    const chosen = S.proposals.filter((p) => p.checked && visible(p));
    if (!chosen.length) return setStatus('Chưa tick đề xuất nào (đề xuất đang bị ẩn bởi bộ lọc không được ghi).', 'error');
    if (S.propFrame !== S.frame) return setStatus('Đề xuất thuộc frame khác. Hãy chạy lại gợi ý cho frame này.', 'error');
    const flagged = chosen.filter((p) => p.flags.length).length;
    if (cfg.confirmApply && o.nextFrame === undefined) {
      const note = flagged ? `\n${flagged} nhãn đang có cờ cần xem kỹ.` : '';
      if (!await uiConfirm(`Ghi ${chosen.length} nhãn vào frame ${S.frame} của job ${S.jobId}?${note}`)) return;
    }
    await run('Đang ghi nhãn vào CVAT…', async () => {
      if (!(await preApplyCheck(chosen))) { setStatus('Đã dừng ghi để bạn sửa các lỗi kiểm tra.', 'info'); return; }
      await saveCvatUi(); // lưu các thay đổi bạn đang làm dở trước khi ghi thêm
      const shapes = chosen.map((p) => toCvatShape(p));
      const res = await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=create`, {
        version: 0, tags: [], shapes, tracks: [],
      });
      pushUndo({ jobId: S.jobId, frame: S.propFrame, shapes: (res && res.shapes) || [], tracks: [], at: Date.now() });
      const frame = S.propFrame;
      learnFromApply(chosen, (res && res.shapes) || [], S.proposals, () => frame);
      statsAfterApply(chosen, S.proposals);
      if (S.batch[frame]) { delete S.batch[frame]; persist('batch'); }
      clearProposals();
      const target = o.nextFrame !== undefined ? o.nextFrame : frame;
      if (o.nextFrame !== undefined) GM_setValue('autoNext', { jobId: S.jobId, frame: target, at: Date.now() });
      if (cfg.reloadAfterApply) {
        setStatus(`Đã ghi ${shapes.length} nhãn. Đang tải lại trang${o.nextFrame !== undefined ? ` ở frame ${target}` : ''}…`, 'ok');
        await sleep(300);
        reloadAtFrame(target);
      } else {
        setStatus(`Đã ghi ${shapes.length} nhãn.${o.nextFrame !== undefined ? '' : ' Tải lại trang (F5) để thấy chúng trên canvas.'}`, 'ok');
        if (o.nextFrame !== undefined) goToFrame(target);
      }
    });
  }

  async function undoLast() {
    const stack = undoStackOf(S.jobId);
    const u = stack[stack.length - 1];
    const nShapes = u && u.shapes ? u.shapes.length : 0;
    const nTracks = u && u.tracks ? u.tracks.length : 0;
    if (!u || !(nShapes + nTracks)) {
      if (u) popUndo(S.jobId);
      return setStatus('Không có lần ghi nào của job này để hoàn tác.', 'error');
    }
    const what = [nShapes ? `${nShapes} nhãn` : '', nTracks ? `${nTracks} track` : ''].filter(Boolean).join(' và ');
    const more = stack.length > 1 ? ` (còn ${stack.length - 1} lần ghi trước đó có thể hoàn tác tiếp)` : '';
    if (!await uiConfirm(`Xoá ${what} mà tool đã ghi ở frame ${u.frame}${more}?`)) return;
    await run('Đang hoàn tác…', async () => {
      await saveCvatUi();
      for (let i = 0; i < Math.max(nShapes, nTracks, 1); i += 300) {
        await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=delete`, {
          version: 0, tags: [], shapes: (u.shapes || []).slice(i, i + 300), tracks: (u.tracks || []).slice(i, i + 300),
        });
      }
      popUndo(S.jobId);
      bumpStats({ applied: -nShapes, job: { id: S.jobId, add: { applied: -nShapes } } });
      setStatus('Đã xoá. Đang tải lại trang…', 'ok');
      await sleep(400);
      reloadAtFrame(u.frame);
    });
  }

  // Đổi source của các nhãn đã lưu từ "semi-auto" sang "manual" mà không vẽ lại.
  async function convertSource(scope) {
    await run('Đang đọc annotation của job…', async () => {
      await saveCvatUi();
      const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      const inScope = (frame) => scope === 'job' || frame === S.frame;
      const shapes = (ann.shapes || []).filter((s) => s.source === 'semi-auto' && inScope(s.frame));
      const tracks = (ann.tracks || []).filter((t) => t.source === 'semi-auto' && inScope(t.frame));
      const where = scope === 'job' ? `cả job ${S.jobId}` : `frame ${S.frame}`;
      if (!shapes.length && !tracks.length) return setStatus(`Không có nhãn semi-auto nào trong ${where}.`, 'ok');
      const count = shapes.length + tracks.length;
      if (!await uiConfirm(`Đổi source của ${count} nhãn trong ${where} từ "semi-auto" sang "manual"?\nGồm cả nhãn tạo bằng công cụ AI có sẵn của CVAT, nếu có.`)) return setStatus('Đã huỷ.');
      setStatus(`Đang đổi source của ${count} nhãn…`, 'busy');
      const manual = (o) => Object.assign({}, o, { source: 'manual' });
      await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=update`, {
        version: ann.version || 0, tags: [], shapes: shapes.map(manual), tracks: tracks.map(manual),
      });
      const after = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      const left = (after.shapes || []).concat(after.tracks || []).filter((o) => o.source === 'semi-auto' && inScope(o.frame)).length;
      if (left) return setStatus(`CVAT nhận yêu cầu nhưng vẫn còn ${left} nhãn semi-auto trong ${where}. Máy chủ có thể không cho đổi trường này.`, 'error');
      clearUndo(S.jobId); // id nhãn có thể đã đổi, các lần hoàn tác cũ không còn đúng
      setStatus(`Đã đổi ${count} nhãn sang manual. Đang tải lại trang…`, 'ok');
      await sleep(600);
      reloadAtFrame(S.frame);
    });
  }

  // ------------------------------------------------------ cả job: gợi ý --
  function batchStats() {
    const frames = Object.keys(S.batch).map(Number).sort((a, b) => a - b);
    let total = 0, flagged = 0, ready = 0;
    const flaggedFrames = [];
    for (const f of frames) {
      const props = S.batch[f].proposals || [];
      total += props.length;
      const nf = props.filter((p) => p.flags.length).length;
      flagged += nf;
      ready += props.filter((p) => p.checked && (cfg.batchIncludeFlagged || !p.flags.length)).length;
      if (nf) flaggedFrames.push(f);
    }
    return { frames, total, flagged, ready, flaggedFrames };
  }

  // Label có thể gợi ý ở tab Cả job: label đang bật và tool biết cách gán.
  function batchCandidates() {
    return ((S.resolved && S.resolved.labels) || []).filter((c) => c.enabled && c.supported);
  }

  function batchPicked() {
    const ids = batchCandidates().map((c) => c.label_id);
    return S.batchPick ? ids.filter((id) => S.batchPick.includes(id)) : ids;
  }

  function batchSkipMode() {
    if (cfg.batchSkip) return cfg.batchSkip;
    return cfg.batchSkipExisting === false ? 'none' : 'label';
  }

  // Label nào đã có trên từng frame: shape, và track còn hiện ở frame đó (từ keyframe đầu tới khi outside).
  function labelsByFrame(ann) {
    const out = {};
    const add = (f, id) => { (out[f] = out[f] || new Set()).add(id); };
    for (const sh of ann.shapes || []) add(sh.frame, sh.label_id);
    for (const tr of ann.tracks || []) {
      const keys = (tr.shapes || []).slice().sort((a, b) => a.frame - b.frame);
      for (let k = 0; k < keys.length; k++) {
        if (keys[k].outside) continue;
        const end = k + 1 < keys.length ? keys[k + 1].frame - 1 : jobRange().to;
        for (let f = keys[k].frame; f <= end; f++) add(f, tr.label_id);
      }
    }
    return out;
  }

  async function pickNewBatchLabels() {
    await run('Đang tìm label chưa có trong job…', async () => {
      const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      const used = new Set([...(ann.shapes || []), ...(ann.tracks || [])].map((x) => x.label_id));
      const fresh = batchCandidates().filter((c) => !used.has(c.label_id)).map((c) => c.label_id);
      S.batchPick = fresh;
      setStatus(fresh.length ? `Đã chọn ${fresh.length} label chưa có nhãn nào trong job.` : 'Label nào trong job cũng đã có nhãn; chọn tay label cần gợi ý.', fresh.length ? 'ok' : 'error');
    });
  }

  async function runBatch(resume, only) {
    const saved = resume === true ? GM_getValue(`batchrun:${S.jobId}`, null) : null;
    const r = jobRange();
    let frames = saved ? saved.frames : (Array.isArray(only) && only.length ? only.slice() : jobFrames(r.from, r.to));
    if (!frames.length) return setStatus('Khoảng frame không hợp lệ.', 'error');
    const picked = saved ? saved.picked : batchPicked();
    if (!picked.length) return setStatus('Chưa chọn label nào để gợi ý.', 'error');
    const mode = saved ? saved.mode : batchSkipMode();
    const doneBefore = new Set(saved ? saved.done : []);
    const run0 = saved || { frames, picked, mode, done: [], total: frames.length, at: Date.now() };
    if (saved) frames = frames.filter((f) => !doneBefore.has(f));
    GM_setValue(`batchrun:${S.jobId}`, run0);
    const allIds = batchCandidates().map((c) => c.label_id);
    S.stopRequested = false;
    S.longTask = 'batch';
    await run(`Đang gợi ý cho ${frames.length} frame…`, async () => {
      const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      const present = labelsByFrame(ann);
      // Label cần gợi ý ở frame f: rỗng nghĩa là bỏ qua frame.
      const wanted = (f) => {
        if (mode === 'frame') return present[f] && present[f].size ? [] : picked;
        if (mode === 'label') return picked.filter((id) => !(present[f] && present[f].has(id)));
        return picked;
      };
      let done = 0, skipped = 0, made = 0;
      const started = Date.now();
      let next = null;
      for (let i = 0; i < frames.length; i++) {
        if (S.stopRequested) break;
        const f = frames[i];
        const ids = wanted(f);
        if (!ids.length) { skipped++; run0.done.push(f); continue; }
        const image = next && next.frame === f ? await next.promise : await fetchFrame(f);
        const following = frames.slice(i + 1).find((x) => wanted(x).length);
        next = following === undefined ? null : { frame: following, promise: fetchFrame(following) };  // tải trước frame kế
        const body = baseBody();
        // Chỉ gợi ý các label cần: tắt tạm các label còn lại cho lần gọi này (không đổi cài đặt ở tab Label).
        for (const id of allIds) {
          if (!ids.includes(id)) body.overrides[String(id)] = Object.assign({}, body.overrides[String(id)] || {}, { enabled: false });
        }
        const res = await backend('POST', '/propose', Object.assign(body, { image_b64: image.b64, existing: existingAt(ann, f) }));
        S.batch[f] = { proposals: stamp(res.proposals || [], 'batch'), warnings: res.warnings || [], width: res.width, height: res.height };
        S.existingIds[f] = existingAt(ann, f).filter((x) => x.id !== undefined).map((x) => x.id);
        made += (res.proposals || []).length;
        done++;
        run0.done.push(f);
        const per = (Date.now() - started) / done / 1000;
        const left = frames.length - i - 1;
        const eta = per * left;
        const etaText = eta >= 90 ? `${Math.round(eta / 60)} phút` : `${Math.round(eta)} giây`;
        setStatus(`Frame ${f}: ${run0.done.length}/${run0.total} frame, ${made} đề xuất. Còn khoảng ${etaText}. Bấm Dừng để tạm dừng, tiếp tục sau (kể cả khi tắt trình duyệt).`, 'busy');
        if (done % 5 === 0) { persist('batch'); GM_setValue(`batchrun:${S.jobId}`, run0); }
      }
      persist('batch');
      const finished = !S.stopRequested;
      GM_setValue(`batchrun:${S.jobId}`, finished ? null : run0);
      if (S.frame in S.batch) { S.proposals = []; loadBatchFrame(); }
      const st = batchStats();
      const stopped = S.stopRequested ? `Đã tạm dừng (${run0.done.length}/${run0.total} frame). Bấm "Tiếp tục" để chạy nốt. ` : '';
      if (finished) notify('Gợi ý cả job xong', `Job ${S.jobId}: ${run0.done.length} frame, ${made} đề xuất, ${st.flagged} mục có cờ.`);
      const why = mode === 'label' ? (picked.length < allIds.length ? 'đã có đủ các label đã chọn' : 'đã có đủ label') : 'đã có nhãn';
      setStatus(`${stopped}${done} frame có ${made} đề xuất${skipped ? `, bỏ qua ${skipped} frame ${why}` : ''}. ${st.flagged} mục có cờ cần duyệt.`, 'ok');
    });
    S.longTask = '';
    render();
  }

  async function applyBatch() {
    const st = batchStats();
    if (!st.ready) return setStatus('Không có đề xuất nào sẵn sàng để ghi.', 'error');
    const note = cfg.batchIncludeFlagged ? '' : `\nMục có cờ (${st.flagged}) được giữ lại để bạn duyệt từng frame.`;
    if (cfg.confirmApply && !await uiConfirm(`Ghi ${st.ready} nhãn trên ${st.frames.length} frame vào job ${S.jobId}?${note}`)) return;
    await run('Đang ghi nhãn của cả job…', async () => {
      await saveCvatUi();
      const queue = [];
      for (const f of st.frames) {
        for (const p of S.batch[f].proposals) {
          if (p.checked && (cfg.batchIncludeFlagged || !p.flags.length)) queue.push({ f, p, shape: toCvatShape(p, f) });
        }
      }
      const created = [];
      for (let i = 0; i < queue.length; i += 200) {
        const part = queue.slice(i, i + 200);
        const res = await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=create`, {
          version: 0, tags: [], shapes: part.map((q) => q.shape), tracks: [],
        });
        created.push(...((res && res.shapes) || []));
        learnFromApply(part.map((q) => q.p), (res && res.shapes) || [], [], (p) => part.find((q) => q.p === p).f);
        for (const q of part) {
          const entry = S.batch[q.f];
          entry.proposals = entry.proposals.filter((x) => x !== q.p);
          if (!entry.proposals.length) delete S.batch[q.f];
        }
        persist('batch');
        setStatus(`Đã ghi ${Math.min(i + 200, queue.length)}/${queue.length} nhãn…`, 'busy');
      }
      pushUndo({ jobId: S.jobId, frame: S.frame, shapes: created, tracks: [], at: Date.now() });
      clearProposals();
      setStatus(`Đã ghi ${queue.length} nhãn.${cfg.reloadAfterApply ? ' Đang tải lại trang…' : ' Tải lại trang (F5) để thấy chúng.'}`, 'ok');
      if (cfg.reloadAfterApply) { await sleep(500); reloadAtFrame(S.frame); }
    });
  }

  function clearBatch() {
    S.batch = {}; persist('batch');
    if (S.propSource === 'batch') clearProposals();
    setStatus('Đã bỏ kết quả gợi ý của cả job.');
    render();
  }

  // ---------------------------------------------------- cả job: theo dõi --
  function trackLabels() {
    return ((S.resolved && S.resolved.track && S.resolved.track.labels) || []).filter((c) => c.enabled && c.supported);
  }

  async function runTracking() {
    const r = jobRange();
    if (!trackLabels().length) return setStatus('Job không có label rectangle nào để theo dõi.', 'error');
    const frames = jobFrames(r.from, r.to);
    S.stopRequested = false;
    S.longTask = 'track';
    await run(`Đang theo dõi ${frames.length} frame…`, async () => {
      const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      const existing = (ann.tracks || []).length;
      const detected = [];
      let size = null, warnings = [];
      const started = Date.now();
      const body = Object.assign(baseBody(), { profile_id: 'auto' });
      // SAM3 Video (thử nghiệm): model giữ ID qua các frame. Không dùng được (thiếu VRAM, transformers cũ...) thì dùng cách thường.
      let video = null;
      let engineNote = '';
      if (cfg.trackEngine === 'sam3_video') {
        try {
          video = await backend('POST', '/track/video/start', body);
        } catch (err) {
          engineNote = ` Không dùng được SAM3 Video (${String(err.message || err).slice(0, 160)}), đã theo dõi bằng cách thường.`;
        }
      }
      for (let i = 0; i < frames.length; i += 4) {
        if (S.stopRequested) break;
        const part = frames.slice(i, i + 4);
        const images = await Promise.all(part.map((f) => fetchFrame(f)));
        size = size || { w: images[0].w, h: images[0].h };
        const payload = { frames: part.map((f, k) => ({ frame: f, image_b64: images[k].b64 })) };
        let res;
        if (video) {
          try {
            res = await backend('POST', '/track/video/frames', Object.assign({}, body, payload, { session_id: video.session_id }));
          } catch (err) {
            // Hỏng giữa chừng: ID của SAM3 Video và cách thường không nối được với nhau, nên làm lại từ đầu bằng cách thường.
            video = null;
            engineNote = ` SAM3 Video lỗi giữa chừng (${String(err.message || err).slice(0, 160)}), đã làm lại bằng cách thường.`;
            detected.length = 0;
            i = -4;
            continue;
          }
        } else {
          res = await backend('POST', '/track/detect', Object.assign({}, body, payload));
        }
        for (const fr of res.frames) detected.push({ frame: fr.frame, dets: fr.dets });
        warnings = warnings.concat(res.warnings || []);
        const per = (Date.now() - started) / detected.length / 1000;
        setStatus(`Đang tìm đối tượng: ${detected.length}/${frames.length} frame. Còn khoảng ${Math.round(per * (frames.length - detected.length))} giây.`, 'busy');
      }
      if (video) { try { await backend('POST', '/track/video/end', { session_id: video.session_id }); } catch (err) { /* bỏ qua */ } }
      if (!detected.length) return;
      setStatus('Đang nối ID và chọn keyframe…', 'busy');
      const last = detected[detected.length - 1].frame;
      const built = await backend('POST', '/track/build', Object.assign({}, body, {
        frames: detected, width: size.w, height: size.h, start: r.from, stop: r.start === r.from && r.to === r.stop ? r.stop : last,
      }));
      S.tracks = built.tracks || [];
      S.trackMeta = {
        from: r.from, to: last, width: size.w, height: size.h, existing,
        warnings: [...new Set(warnings.concat(built.warnings || []))],
        title: (S.resolved.track && S.resolved.track.title) || '',
      };
      S.imgW = size.w; S.imgH = size.h;
      persist('tracks');
      drawOverlay();
      const flagged = S.tracks.filter((t) => t.flags.length).length;
      const stopped = S.stopRequested ? 'Đã dừng ở frame ' + last + '. ' : '';
      notify('Theo dõi đối tượng xong', `Job ${S.jobId}: ${S.tracks.length} track, ${flagged} track có cờ.`);
      setStatus(`${stopped}${S.tracks.length} track, ${flagged} track có cờ cần xem${video ? ' (theo dõi bằng SAM3 Video)' : ''}. Tua qua các frame để kiểm tra rồi bấm Ghi.${engineNote}`, engineNote ? 'error' : 'ok');
    });
    S.longTask = '';
    render();
  }

  function trackColor(tid) { return PALETTE[(tid - 1) % PALETTE.length]; }

  // Box CVAT sẽ hiện ở một frame: nội suy tuyến tính giữa hai keyframe, null nếu outside.
  function trackBoxAt(track, frame) {
    const shapes = track.shapes;
    let k0 = null, k1 = null;
    for (const s of shapes) {
      if (s.frame <= frame) k0 = s;
      else { k1 = s; break; }
    }
    if (!k0 || k0.outside) return null;
    if (!k1 || k0.frame === frame) return { box: k0.points, occluded: k0.occluded };
    const u = (frame - k0.frame) / (k1.frame - k0.frame);
    return { box: k0.points.map((v, i) => v + u * (k1.points[i] - v)), occluded: k0.occluded };
  }

  async function applyTracks() {
    const chosen = S.tracks.filter((t) => t.checked);
    if (!chosen.length) return setStatus('Chưa tick track nào.', 'error');
    const flagged = chosen.filter((t) => t.flags.length).length;
    if (cfg.confirmApply && !await uiConfirm(`Ghi ${chosen.length} track vào job ${S.jobId}?${flagged ? `\n${flagged} track đang có cờ cần xem.` : ''}`)) return;
    await run('Đang ghi track vào CVAT…', async () => {
      await saveCvatUi();
      const tracks = chosen.map((t) => {
        const label = labelById(t.label_id) || { attributes: [] };
        const track = {
          frame: t.shapes[0].frame, label_id: t.label_id, group: 0,
          attributes: attributeValues(label, null, true),
          shapes: t.shapes.map((s) => ({
            type: 'rectangle', frame: s.frame, points: s.points, occluded: !!s.occluded, outside: !!s.outside,
            z_order: 0, rotation: 0, attributes: [],
          })),
          elements: [],
        };
        if (cfg.markSemiAuto) track.source = 'semi-auto';
        return track;
      });
      const res = await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=create`, {
        version: 0, tags: [], shapes: [], tracks,
      });
      pushUndo({ jobId: S.jobId, frame: S.frame, shapes: [], tracks: (res && res.tracks) || [], at: Date.now() });
      S.tracks = S.tracks.filter((t) => !t.checked);
      persist('tracks');
      setStatus(`Đã ghi ${tracks.length} track.${cfg.reloadAfterApply ? ' Đang tải lại trang…' : ' Tải lại trang (F5) để thấy chúng.'}`, 'ok');
      if (cfg.reloadAfterApply) { await sleep(500); reloadAtFrame(S.frame); }
    });
  }

  // ---------------------------------------------------- soạn guideline ----
  // Chọn ảnh mẫu rải đều cả khoảng frame; vài ảnh đầu (chặng khám phá) cũng rải đều để đa dạng.
  function sampleFrames(from, to, count) {
    const total = to - from + 1;
    const n = Math.max(1, Math.min(count, total));
    const picked = [];
    for (let i = 0; i < n; i++) picked.push(from + Math.round((i * (total - 1)) / Math.max(1, n - 1)));
    const unique = [...new Set(picked)];
    const first = [];
    const k = Math.min(8, unique.length);
    for (let i = 0; i < k; i++) first.push(unique[Math.round((i * (unique.length - 1)) / Math.max(1, k - 1))]);
    const head = [...new Set(first)];
    return head.concat(unique.filter((f) => !head.includes(f)));
  }

  async function runGuideline() {
    const r = jobRange();
    const frames = sampleFrames(r.from, r.to, Number(cfg.guideSample) || 40);
    S.stopRequested = false;
    S.longTask = 'guide';
    await run(`Đang quét ${frames.length} ảnh mẫu…`, async () => {
      const started = await backend('POST', '/guideline/start', {
        labels: labelPayload(),
        job: { id: S.jobId, task_id: S.job && S.job.task_id, mode: (S.job && S.job.mode) || 'annotation' },
        purpose: cfg.guidePurpose || '', title: `Job ${S.jobId}`,
        extra_terms: String(cfg.guideTerms || '').split(',').map((x) => x.trim()).filter(Boolean),
        send_images: !!cfg.guideSendImages,
      });
      const t0 = Date.now();
      let done = 0;
      for (let i = 0; i < frames.length; i += 2) {
        if (S.stopRequested && done >= 2) break;
        const part = frames.slice(i, i + 2);
        const images = await Promise.all(part.map((f) => fetchFrame(f)));
        const res = await backend('POST', '/guideline/scan', {
          run_id: started.run_id, frames: part.map((f, k) => ({ frame: f, image_b64: images[k].b64 })),
        });
        done = res.scanned;
        const per = (Date.now() - t0) / done / 1000;
        setStatus(`Đã quét ${done}/${frames.length} ảnh, thấy ${res.found} loại đối tượng. Còn khoảng ${Math.round(per * (frames.length - done))} giây.`, 'busy');
      }
      setStatus('Đang soạn bản nháp và tự kiểm tra…', 'busy');
      S.guide = await backend('POST', '/guideline/draft', { run_id: started.run_id });
      S.guideRunId = started.run_id;
      GM_setValue(`guide:${S.jobId}`, started.run_id);
      const open = S.guide.questions.filter((q) => !q.answer).length;
      setStatus(`Đã soạn bản nháp từ ${S.guide.summary.images} ảnh: ${open} câu hỏi cần chốt, ${S.guide.findings.length} lỗ hổng tool tự thấy.`, 'ok');
    });
    S.longTask = '';
    render();
  }

  async function loadGuide() {
    if (!S.guideRunId || S.guide || S.busy) return;
    await run('Đang nạp bản nháp guideline…', async () => {
      try {
        S.guide = await backend('GET', `/guideline/run/${S.guideRunId}/data`);
      } catch (err) {
        S.guideRunId = null; GM_setValue(`guide:${S.jobId}`, null);  // lượt cũ không còn trên backend
      }
    });
  }

  async function answerGuide(key, value) {
    await run('Đang cập nhật bản nháp…', async () => {
      S.guide = await backend('POST', '/guideline/answer', { run_id: S.guideRunId, answers: { [key]: value || null } });
      setStatus('Đã cập nhật bản nháp theo lựa chọn của bạn.', 'ok');
    });
  }

  function openGuidePage() {
    const url = `${cfg.backendUrl.replace(/\/+$/, '')}/guideline/run/${S.guideRunId}?key=${encodeURIComponent(cfg.apiKey || '')}&lang=${cfg.lang || 'vi'}`;
    window.open(url, '_blank', 'noopener');
  }

  function downloadGuide() {
    const text = S.guide.markdown.replace(/\s*<!--r:[^>]+-->/g, '');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }));
    a.download = `guideline_job_${S.jobId}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function useGuideProfile() {
    await run('Đang tạo profile từ bản nháp…', async () => {
      const res = await backend('POST', '/guideline/use_profile', { run_id: S.guideRunId });
      cfg.profileId = res.profile_id; saveCfg();
      await checkBackend();
      clearProposals();
      await resolveLabels();
      setStatus(res.note ? `${res.note} Đổi lại ở ô chọn profile phía trên.`
        : 'Tool gợi ý nhãn đang dùng profile sinh từ bản nháp này. Đổi lại ở ô chọn profile phía trên.', 'ok');
    });
  }

  // Thêm vào task các label mà guideline có nhưng task chưa có. Chỉ thêm: không xoá, không đổi tên, không đổi kiểu.
  async function addGuideLabels() {
    const missing = (S.guide.label_plan && S.guide.label_plan.missing) || [];
    if (!missing.length) return setStatus('Task đã có đủ label theo taxonomy.', 'ok');
    const taskId = S.job && S.job.task_id;
    const projectId = S.job && S.job.project_id;
    const where = projectId ? `project ${projectId} (label của task này nằm ở project)` : `task ${taskId}`;
    if (!await uiConfirm(`Thêm ${missing.length} label vào ${where}?\n${missing.map((l) => l.name).join(', ')}\n\nTool chỉ thêm label mới; label đang có được giữ nguyên.`)) return;
    await run('Đang thêm label vào CVAT…', async () => {
      const plan = await backend('POST', '/guideline/labels', { run_id: S.guideRunId });
      try {
        await cvat('PATCH', projectId ? `/api/projects/${projectId}` : `/api/tasks/${taskId}`, { labels: plan.missing });
      } catch (err) {
        throw new Error(`${err.message} Nếu tài khoản của bạn không có quyền sửa task, bấm "Chép JSON label" và gửi cho người tạo task.`);
      }
      await readJob();
      const have = new Set(S.labels.map((l) => normName(l.name)));
      const lost = plan.missing.filter((l) => !have.has(normName(l.name))).map((l) => l.name);
      const synced = await backend('POST', '/guideline/labels', { run_id: S.guideRunId, labels: labelPayload() });
      S.guide = synced.view;
      await resolveLabels();
      if (lost.length) return setStatus(`CVAT nhận yêu cầu nhưng chưa thấy label: ${lost.join(', ')}. Kiểm tra lại phần label của task.`, 'error');
      setStatus(`Đã thêm ${plan.missing.length} label.${cfg.reloadAfterApply ? ' Đang tải lại trang để CVAT nhận label mới…' : ' Tải lại trang (F5) để CVAT nhận label mới.'}`, 'ok');
      if (cfg.reloadAfterApply) { await sleep(600); reloadAtFrame(S.frame); }
    });
  }

  // ---------------------------------------------- nạp guideline có sẵn ---
  function readFileB64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
      reader.onerror = () => reject(new Error('Không đọc được file.'));
      reader.readAsDataURL(file);
    });
  }

  async function previewImport(file) {
    if (!file) return;
    await run(`Đang đọc ${file.name}…`, async () => {
      const data = await readFileB64(file);
      const res = await backend('POST', '/guideline/import/preview', { filename: file.name, data_b64: data, use_llm: !!cfg.importUseLlm });
      // File taxonomy (.json) chọn SAU file guideline: ghép label vào bản xem trước của guideline, giữ văn bản, checklist của guideline.
      if (S.imp && res.analysis.source === 'taxonomy' && S.imp.analysis.source !== 'taxonomy') {
        const n = mergeLabels(S.imp.analysis, res.analysis.labels, file.name);
        if (!S.imp.analysis.labelsFromGuide) S.imp.analysis.task = res.analysis.task;
        S.imp.analysis.warnings = (S.imp.analysis.warnings || []).filter((w) => !/Không tìm thấy bảng taxonomy/.test(w)).concat(res.analysis.warnings || []);
        return setStatus(`Đã ghép ${n.added} label mới và cập nhật ${n.updated} label từ ${file.name} vào guideline "${S.imp.analysis.title}". Kiểm tra rồi bấm Lưu.`, 'ok');
      }
      S.imp = res;
      const a = S.imp.analysis;
      a.labelsFromGuide = a.source !== 'taxonomy' && a.labels.length > 0;
      setStatus(a.source === 'taxonomy'
        ? `Đã đọc ${a.labels.length} label từ ${file.name}. Nếu có file guideline (.docx), chọn nó ngay sau đây: tool sẽ ghép label này với văn bản và checklist của guideline.`
        : `Đã đọc "${a.title}": ${a.labels.length} label, ${a.checklist.length} mục checklist.${a.labels.length ? '' : ' Guideline không có bảng taxonomy: chọn thêm file taxonomy (.json), hoặc bấm "Lấy label từ task đang mở".'} Kiểm tra bảng bên dưới rồi bấm Lưu.`, 'ok');
    });
  }

  // Ghép label (từ file taxonomy hoặc từ task) vào bản xem trước: label trùng tên lấy shape/attribute của nguồn mới,
  // giữ định nghĩa và prompt đã có.
  function mergeLabels(a, labels, from) {
    const byName = new Map(a.labels.map((l) => [normName(l.name), l]));
    let added = 0; let updated = 0;
    for (const l of labels) {
      const old = byName.get(normName(l.name));
      if (old) {
        old.shape = l.shape || old.shape;
        if (l.cvat) old.cvat = l.cvat;
        if (!old.definition && l.definition) old.definition = l.definition;
        if ((!old.prompts || !old.prompts.length) && l.prompts && l.prompts.length) { old.prompts = l.prompts; old.prompt_source = l.prompt_source; }
        updated += 1;
      } else {
        a.labels.push({ ...l, prompt_source: l.prompt_source || from });
        byName.set(normName(l.name), l);
        added += 1;
      }
    }
    return { added, updated };
  }

  // Task đang mở đã có đủ label: lấy luôn danh sách đó làm taxonomy của guideline.
  function labelsFromTask() {
    const a = S.imp.analysis;
    const known = new Map(((S.resolved && S.resolved.labels) || []).map((c) => [c.label_id, c]));
    const labels = S.labels.map((l) => {
      const c = known.get(l.id) || {};
      const shape = l.type && l.type !== 'any' ? l.type : (c.shape || 'rectangle');
      return { name: l.name, shape, prompts: c.prompts || [], prompt_source: 'tab Label của job', definition: '', aliases: [] };
    });
    const n = mergeLabels(a, labels, 'task đang mở');
    if (!a.labelsFromGuide && labels.some((l) => l.shape === 'skeleton')) a.task = 'keypoints';
    a.warnings = (a.warnings || []).filter((w) => !/Không tìm thấy bảng taxonomy/.test(w));
    setStatus(`Đã lấy ${n.added} label từ task đang mở (${n.updated} label đã có trong bản xem trước).`, 'ok');
    render();
  }

  async function loadImported() {
    try { S.imported = (await backend('GET', '/guideline/imported')).items || []; } catch (err) { S.imported = []; }
  }

  async function saveImport() {
    const a = S.imp.analysis;
    if (S.imp.exists && !await uiConfirm(`Guideline "${a.title}" đã được nạp trước đây. Ghi đè bản cũ (cả profile gl_${a.id}, nếu bạn đã sửa tay)?`)) return;
    await run('Đang lưu guideline vào tool…', async () => {
      const res = await backend('POST', '/guideline/import/save', { analysis: a });
      S.imp = null;
      await checkBackend();
      await loadImported();
      setStatus(res.profile_id
        ? `Đã nạp ${res.labels} label. Profile "${res.profile_id}" đã có trong ô chọn profile; bấm "Dùng cho job này" ở danh sách bên dưới nếu job đang mở thuộc guideline này.`
        : `Đã nạp văn bản, định nghĩa và checklist (${res.labels} label). Guideline skeleton chưa có profile gợi ý tự động.`, 'ok');
    });
  }

  async function useImportedProfile(item) {
    await run('Đang áp profile…', async () => {
      cfg.profileId = item.profile_id; saveCfg();
      clearProposals();
      await resolveLabels();
      const matched = (S.resolved && S.resolved.labels || []).filter((c) => c.matched).length;
      setStatus(`Đang dùng profile của "${item.title}": khớp ${matched}/${S.labels.length} label của job.`, matched ? 'ok' : 'error');
    });
  }

  async function importedLabels(item, mode) {
    const plan = await backend('POST', `/guideline/imported/${item.id}/labels`, { labels: labelPayload() });
    if (mode === 'copy') {
      GM_setClipboard(JSON.stringify(plan.labels, null, 2), 'text');
      return setStatus(`Đã chép JSON của ${plan.labels.length} label. Khi tạo task mới, dán vào tab Raw ở phần label.${plan.notes.length ? ' ' + plan.notes.join(' ') : ''}`, 'ok');
    }
    if (!plan.missing.length) return setStatus(`Task đã có đủ label theo guideline.${plan.notes.length ? ' ' + plan.notes.join(' ') : ''}`, 'ok');
    const taskId = S.job && S.job.task_id;
    const projectId = S.job && S.job.project_id;
    const where = projectId ? `project ${projectId}` : `task ${taskId}`;
    if (!await uiConfirm(`Thêm ${plan.missing.length} label vào ${where}?\n${plan.missing.map((l) => l.name).join(', ')}\n\nTool chỉ thêm label mới; label đang có được giữ nguyên.`)) return;
    try {
      await cvat('PATCH', projectId ? `/api/projects/${projectId}` : `/api/tasks/${taskId}`, { labels: plan.missing });
    } catch (err) {
      throw new Error(`${err.message} Nếu tài khoản của bạn không có quyền sửa task, bấm "Chép JSON label" và gửi cho người tạo task.`);
    }
    await readJob();
    await resolveLabels();
    setStatus(`Đã thêm ${plan.missing.length} label.${plan.notes.length ? ' ' + plan.notes.join(' ') : ''} Tải lại trang (F5) để CVAT nhận label mới.`, 'ok');
  }

  async function removeImported(item) {
    if (!await uiConfirm(`Gỡ guideline "${item.title}" khỏi tool? File được chuyển vào thư mục archive, không bị xoá hẳn.`)) return;
    await run('Đang gỡ…', async () => {
      await backend('DELETE', `/guideline/imported/${item.id}`);
      if (cfg.profileId === item.profile_id) { cfg.profileId = 'auto'; saveCfg(); }
      await checkBackend();
      await loadImported();
      await resolveLabels();
      setStatus(`Đã gỡ "${item.title}".`, 'ok');
    });
  }

  function renderImport() {
    const busy = !!S.busy;
    const llm = (S.health && S.health.guideline_llm) || { provider: 'none' };
    const out = [h('h2', { text: 'Nạp guideline có sẵn cho task khác' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text:
        'Chọn file guideline (.docx, .md, .txt) và/hoặc file taxonomy (.json, ví dụ JSON label ở tab Raw của task CVAT); '
        + 'chọn cả hai cùng lúc được. Tool đọc bảng taxonomy, định nghĩa, Issue type, checklist, tiêu chí reviewer; '
        + 'bạn kiểm tra rồi lưu. Sau đó tool có profile gợi ý nhãn, checklist ở tab Kiểm tra, và dùng guideline này khi soạn nháp.' })];
    const picker = h('input', { type: 'file', accept: '.docx,.md,.txt,.markdown,.pdf,.json', disabled: busy, multiple: true,
      onchange: async (ev) => {
        // Chọn cùng lúc guideline và taxonomy.json được: đọc guideline trước, rồi ghép taxonomy vào.
        const files = [...(ev.target.files || [])].sort((x, y) => /\.json$/i.test(x.name) - /\.json$/i.test(y.name));
        for (const f of files) await previewImport(f);
      } });
    out.push(h('div', { class: 'row' }, picker));
    if (llm.provider !== 'none') {
      out.push(h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!cfg.importUseLlm,
        onchange: (ev) => { cfg.importUseLlm = ev.target.checked; saveCfg(); } }),
        h('span', { text: `Cho LLM bổ sung prompt tiếng Anh và định nghĩa (gửi VĂN BẢN guideline tới ${llm.provider}${llm.local ? ', chạy trên máy' : ''})` })));
    }
    if (S.imp) out.push(...renderImportPreview());
    if (S.imported === null) { loadImported().then(render); return out; }
    if (S.imported.length) {
      out.push(h('h3', { text: 'Guideline đã nạp' }));
      for (const item of S.imported) {
        out.push(h('div', { class: 'row', style: 'flex-wrap:wrap;gap:4px' },
          h('span', { class: 'grow', text: `${item.title} · ${item.task} · ${item.labels} label` }),
          item.profile_id ? h('button', { class: 'small', type: 'button', disabled: busy || cfg.profileId === item.profile_id,
            text: cfg.profileId === item.profile_id ? 'Đang dùng' : 'Dùng cho job này', onclick: () => useImportedProfile(item) }) : null,
          h('button', { class: 'small', type: 'button', disabled: busy, text: 'Thêm label còn thiếu',
            onclick: () => run('Đang thêm label vào CVAT…', () => importedLabels(item, 'add')) }),
          h('button', { class: 'small', type: 'button', disabled: busy, text: 'Chép JSON label',
            onclick: () => run('Đang tạo JSON label…', () => importedLabels(item, 'copy')) }),
          h('button', { class: 'small', type: 'button', disabled: busy, text: 'Gỡ', onclick: () => removeImported(item) })));
      }
    }
    return out;
  }

  function renderImportPreview() {
    const busy = !!S.busy;
    const a = S.imp.analysis;
    const out = [h('h3', { text: `Xem trước: ${a.title}` })];
    out.push(h('label', { class: 'field' }, h('span', { text: `Loại bài toán (tool đoán: ${a.task_reason})` }),
      (() => {
        const sel = h('select', { disabled: busy, onchange: (ev) => { a.task = ev.target.value; render(); } },
          [['instance', 'Detection: box / polygon / polyline'], ['semantic', 'Semantic segmentation'],
            ['tracking', 'Video tracking'], ['keypoints', 'Keypoint / skeleton']].map(([v, t]) => h('option', { value: v, text: t })));
        sel.value = a.task;
        return sel;
      })()));
    for (const w of a.warnings || []) out.push(h('div', { class: 'note', text: w }));
    for (const n of S.imp.notes || []) out.push(h('div', { class: 'note', text: n }));
    if (S.imp.overlap) {
      out.push(h('div', { class: 'note', text: `Profile có sẵn "${S.imp.overlap}" đã khớp các label này. "Tự chọn profile" sẽ giữ profile có sẵn; `
        + 'muốn dùng guideline mới thì chọn tay profile của nó ở đầu panel (hoặc bấm "Dùng cho job này").' }));
    }
    const shapes = ['rectangle', 'polygon', 'polyline', 'mask', 'skeleton', 'points'];
    const rows = a.labels.map((l, i) => h('tr', { title: l.definition || '' },
      h('td', {}, h('input', { type: 'text', value: l.name, style: 'width:100%', onchange: (ev) => { l.name = ev.target.value.trim(); } })),
      h('td', {}, (() => {
        const sel = h('select', { onchange: (ev) => { l.shape = ev.target.value; render(); } }, shapes.map((x) => h('option', { value: x, text: x })));
        sel.value = l.shape;
        return sel;
      })()),
      h('td', {}, l.shape === 'skeleton' ? h('span', { class: 'muted small-text', text: 'skeleton: không dùng prompt' })
        : h('input', { type: 'text', value: (l.prompts || []).join(', '), placeholder: 'prompt tiếng Anh', style: 'width:100%',
          title: l.prompt_source || '',
          onchange: (ev) => { l.prompts = ev.target.value.split(',').map((x) => x.trim()).filter(Boolean); l.prompt_source = 'bạn sửa'; } }),
      l.shape === 'polyline' ? (() => {
        const sel = h('select', { title: 'Cách vẽ vạch', onchange: (ev) => { l.polyline_mode = ev.target.value; } },
          [['centerline', 'theo tim vạch'], ['edges', 'hai mép (vạch kép)'], ['long_edges', 'hai cạnh dài (crosswalk)']].map(([v, t]) => h('option', { value: v, text: t })));
        sel.value = l.polyline_mode || 'centerline';
        return sel;
      })() : null),
      h('td', {}, h('button', { class: 'small', type: 'button', text: '✕', title: 'Bỏ label này', onclick: () => { a.labels.splice(i, 1); render(); } }))));
    out.push(h('table', { class: 'labels' },
      h('thead', {}, h('tr', {}, h('th', { text: 'Label' }), h('th', { text: 'Shape' }), h('th', { text: 'Prompt cho SAM3' }), h('th', { text: '' }))),
      h('tbody', {}, rows)));
    out.push(h('p', { class: 'muted small-text', style: 'margin:0', text:
      `${a.issue_types.length} Issue type · ${a.checklist.length} mục checklist · ${a.reviewer_criteria.length} tiêu chí reviewer. `
      + 'Prompt là cụm tiếng Anh ngắn SAM3 dùng để tìm vật (rê chuột lên ô để xem nguồn); để trống thì tool không tự tìm label đó.' }));
    out.push(h('div', { class: 'row' },
      h('button', { class: 'small', type: 'button', disabled: busy, text: 'Thêm label',
        onclick: () => { a.labels.push({ name: '', shape: 'rectangle', prompts: [], definition: '', aliases: [] }); render(); } }),
      h('button', { class: 'small', type: 'button', disabled: busy || !S.labels.length, text: 'Lấy label từ task đang mở',
        title: 'Dùng danh sách label của task đang mở làm taxonomy', onclick: labelsFromTask }),
      h('span', { class: 'grow' }),
      h('button', { type: 'button', disabled: busy, text: 'Huỷ', onclick: () => { S.imp = null; setStatus(''); render(); } }),
      h('button', { class: 'primary', type: 'button', disabled: busy || !a.labels.some((l) => l.name), text: 'Lưu vào tool', onclick: saveImport })));
    return out;
  }

  async function copyGuideLabels() {
    await run('Đang tạo JSON label…', async () => {
      const plan = await backend('POST', '/guideline/labels', { run_id: S.guideRunId });
      GM_setClipboard(JSON.stringify(plan.labels, null, 2), 'text');
      setStatus(`Đã chép JSON của ${plan.labels.length} label. Khi tạo task mới, dán vào tab Raw ở phần label.`, 'ok');
    });
  }

  async function sendGuideFeedback(text) {
    await run('Đang ghi nhận…', async () => {
      await backend('POST', '/guideline/feedback', { text, tags: [(S.guide && S.guide.task) || 'any'], run_id: S.guideRunId });
      setStatus('Đã ghi nhận. Bài học này sẽ được dùng ở các lần soạn sau.', 'ok');
    });
  }

  function showEvidence(example) {
    S.evidence = { frame: example.frame, bbox: example.bbox };
    if (S.guide && S.guide.summary.width) { S.imgW = S.guide.summary.width; S.imgH = S.guide.summary.height; }
    if (S.frame !== example.frame) goToFrame(example.frame);
    drawOverlay();
  }

  function clearTracks() {
    S.tracks = []; S.trackMeta = null; persist('tracks');
    setStatus('Đã bỏ kết quả theo dõi.');
    render(); drawOverlay();
  }

  async function runQa() {
    await run('Đang kiểm tra annotation của frame…', async () => {
      const frame = S.frame;
      const fr = await getFrame();
      const { shapes, tracks } = await frameShapes();
      const res = await backend('POST', '/qa', Object.assign(baseBody(), { shapes, width: fr.w, height: fr.h }));
      if (frame !== S.frame) return;
      res.tracks = tracks;
      S.qa = res; S.qaFrame = frame; S.imgW = fr.w; S.imgH = fr.h;
      const errors = res.findings.filter((f) => f.severity === 'error').length;
      setStatus(`Đã kiểm tra ${res.shape_count} annotation: ${errors} lỗi, ${res.findings.length - errors} cảnh báo.`, errors ? 'error' : 'ok');
      drawOverlay();
    });
  }

  async function createIssue(code, text, bbox) {
    const message = await uiPrompt('Nội dung Issue gửi mentor/reviewer:', `[${code}] ${text}`);
    if (!message) return;
    await run('Đang tạo Issue…', async () => {
      const position = bbox && bbox.length === 4 ? bbox : [0, 0, S.imgW || 1, S.imgH || 1];
      await cvat('POST', '/api/issues', { job: S.jobId, frame: S.frame, position, message });
      setStatus('Đã tạo Issue trên frame này.', 'ok');
    });
  }

  // -------------------------------------------------------------- overlay --
  const overlayHost = h('div', { style: 'position:fixed;left:0;top:0;width:0;height:0;overflow:hidden;pointer-events:none;z-index:900;' });
  const overlaySvg = svgEl('svg', { preserveAspectRatio: 'none' });
  overlaySvg.style.cssText = 'position:absolute;left:0;top:0;';
  overlayHost.append(overlaySvg);

  function canvasEl() {
    try { return document.querySelector(cfg.canvasSelector || DEFAULTS.canvasSelector); } catch (e) { return null; }
  }

  function overlayActive() {
    const tracks = S.tab === 'job' && S.tracks.length;
    const evidence = S.tab === 'guide' && S.evidence;
    return !cfg.hidden && S.imgW > 0 && (S.proposals.length || S.box || S.points || cfg.reviewerMode || tracks || evidence || (S.qa && S.qa.findings.length));
  }

  function positionOverlay() {
    requestAnimationFrame(positionOverlay);
    const canvas = overlayActive() ? canvasEl() : null;
    if (!canvas) {
      if (overlayHost.style.display !== 'none') overlayHost.style.display = 'none';
      return;
    }
    const container = document.querySelector('.cvat-canvas-container') || canvas.parentElement;
    const c = container.getBoundingClientRect();
    const r = canvas.getBoundingClientRect();
    overlayHost.style.display = 'block';
    overlayHost.style.left = c.left + 'px';
    overlayHost.style.top = c.top + 'px';
    overlayHost.style.width = c.width + 'px';
    overlayHost.style.height = c.height + 'px';
    overlaySvg.style.left = (r.left - c.left) + 'px';
    overlaySvg.style.top = (r.top - c.top) + 'px';
    overlaySvg.style.width = r.width + 'px';
    overlaySvg.style.height = r.height + 'px';
  }

  function pairs(points) {
    const out = [];
    for (let i = 0; i + 1 < points.length; i += 2) out.push(points[i] + ',' + points[i + 1]);
    return out.join(' ');
  }

  function drawOverlay() {
    put(overlaySvg, );
    overlayHost.style.pointerEvents = S.box || S.points ? 'auto' : 'none';
    overlayHost.style.cursor = S.box || S.points ? 'crosshair' : 'default';
    if (!S.imgW) return;
    overlaySvg.setAttribute('viewBox', `0 0 ${S.imgW} ${S.imgH}`);
    const base = { 'vector-effect': 'non-scaling-stroke', 'stroke-linejoin': 'round' };
    const showProps = S.propFrame === S.frame && S.tab !== 'qa';
    for (const p of showProps ? S.proposals.filter(visible) : []) {
      const color = labelColor(p.label_id);
      const hot = S.highlight === 'p' + p.pid || S.sel === p.pid;
      const style = Object.assign({}, base, {
        stroke: color, 'stroke-width': hot ? 4 : 2, fill: color,
        'fill-opacity': p.type === 'polyline' ? 0 : (p.type === 'rectangle' ? cfg.opacity / 3 : cfg.opacity),
        opacity: p.checked ? 1 : 0.35,
      });
      if (!p.checked) style['stroke-dasharray'] = '6 4';
      if (p.type === 'polyline') style.fill = 'none';
      const g = svgEl('g', {});
      if (p.type === 'rectangle') {
        const [x1, y1, x2, y2] = p.points;
        g.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, style)));
      } else if (p.type === 'polygon') {
        g.append(svgEl('polygon', Object.assign({ points: pairs(p.points) }, style)));
      } else if (p.type === 'polyline') {
        g.append(svgEl('polyline', Object.assign({ points: pairs(p.points) }, style, { 'stroke-width': hot ? 5 : 3 })));
      } else if (p.type === 'mask') {
        for (const poly of p.preview || []) g.append(svgEl('polygon', Object.assign({ points: pairs(poly) }, style)));
      } else if (p.type === 'skeleton') {
        drawSkeleton(g, p, color, hot, p.checked ? 1 : 0.35);
      }
      if (hot) {
        const [x1, y1, x2, y2] = p.bbox;
        g.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, base, {
          stroke: '#ffffff', 'stroke-width': 1.5, 'stroke-dasharray': '4 3', fill: 'none',
        })));
      }
      overlaySvg.append(g);
    }
    if (S.tab === 'guide' && S.evidence && S.evidence.frame === S.frame && S.evidence.bbox) {
      const [x1, y1, x2, y2] = S.evidence.bbox;
      overlaySvg.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, base, {
        stroke: '#ffffff', 'stroke-width': 3, 'stroke-dasharray': '8 4', fill: 'rgba(255,255,255,0.10)',
      })));
    }
    if (S.tab === 'job' && S.tracks.length) {
      const font = Math.max(12, S.imgH * 0.022);
      for (const t of S.tracks) {
        const at = trackBoxAt(t, S.frame);
        if (!at) continue;
        const [x1, y1, x2, y2] = at.box;
        const color = trackColor(t.tid);
        const hot = S.highlight === 't' + t.tid;
        overlaySvg.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, base, {
          stroke: color, 'stroke-width': hot ? 4 : 2, fill: color, 'fill-opacity': hot ? 0.15 : 0.04,
          opacity: t.checked ? 1 : 0.35, 'stroke-dasharray': at.occluded ? '7 4' : 'none',
        })));
        const label = svgEl('text', { x: x1 + 3, y: Math.max(font, y1 - 4), fill: color, 'font-size': font, 'font-weight': '700',
          'font-family': 'system-ui, sans-serif', opacity: t.checked ? 1 : 0.5, 'paint-order': 'stroke', stroke: '#000000', 'stroke-width': font / 8 });
        label.textContent = `#${t.tid}${at.occluded ? ' (occluded)' : ''}`;
        overlaySvg.append(label);
      }
    }
    if (S.qa && S.qaFrame === S.frame && S.tab === 'qa') {
      S.qa.findings.forEach((f, i) => {
        if (!f.bbox) return;
        const hot = S.highlight === 'q' + i;
        const [x1, y1, x2, y2] = f.bbox;
        overlaySvg.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, base, {
          stroke: f.severity === 'error' ? '#d92d20' : '#f79009', 'stroke-width': hot ? 4 : 2,
          'stroke-dasharray': '8 4', fill: hot ? 'rgba(217,45,32,0.15)' : 'none',
        })));
      });
    }
    if (cfg.reviewerMode) {
      // Chế độ reviewer: nhãn do tool ghi mà chưa ai sửa -> viền tím + chữ "AI"
      const font = Math.max(11, S.imgH * 0.018);
      for (const x of reviewerMarks()) {
        const pts = x.type === 'rectangle' ? x.points : null;
        const xs = (x.points || []).filter((_, i) => i % 2 === 0), ys = (x.points || []).filter((_, i) => i % 2 === 1);
        if (!xs.length) continue;
        const [x1, y1, x2, y2] = pts || [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
        overlaySvg.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, base, {
          stroke: '#b14cff', 'stroke-width': 2, 'stroke-dasharray': '3 3', fill: 'none' })));
        const t = svgEl('text', { x: x1 + 2, y: y1 + font, fill: '#b14cff', 'font-size': font, 'font-weight': '700', 'font-family': 'system-ui, sans-serif',
          'paint-order': 'stroke', stroke: '#000000', 'stroke-width': font / 7 });
        t.textContent = 'AI';
        overlaySvg.append(t);
      }
    }
    if (S.points) {
      const r = Math.max(4, S.imgW * 0.006);
      for (const [pt, ok] of S.points.pos.map((q) => [q, true]).concat(S.points.neg.map((q) => [q, false]))) {
        overlaySvg.append(svgEl('circle', { cx: pt[0], cy: pt[1], r, fill: ok ? '#12b76a' : '#f04438', stroke: '#ffffff', 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke' }));
      }
    }
    if (S.box && S.box.start && S.box.cur) {
      const [x1, y1, x2, y2] = boxFrom(S.box.start, S.box.cur);
      overlaySvg.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, base, {
        stroke: '#ffffff', 'stroke-width': 2, 'stroke-dasharray': '6 4', fill: 'rgba(255,255,255,0.12)',
      })));
    }
  }

  // "17/17 điểm", "8/8 điểm · 3 occluded", hoặc "Outside cả skeleton" (bộ phận bị che quá ngưỡng của guideline).
  function skeletonText(p) {
    const els = p.elements || [];
    const shown = els.filter((e) => !e.outside);
    if (els.length && !shown.length) return `Outside cả skeleton (${els.length} điểm)`;
    const occluded = shown.filter((e) => e.occluded).length;
    return `${shown.length}/${els.length} điểm${occluded ? ` · ${occluded} occluded` : ''}`;
  }

  function sideColor(e) {
    if (e.side === 'L') return SIDE_COLORS.left;
    if (e.side === 'R') return SIDE_COLORS.right;
    const n = normName(e.name);
    if (n.startsWith('left') || n.endsWith('left')) return SIDE_COLORS.left;
    if (n.startsWith('right') || n.endsWith('right')) return SIDE_COLORS.right;
    return SIDE_COLORS.mid;
  }

  // Skeleton: điểm thấy = chấm đặc, occluded = vòng rỗng, outside = không vẽ (như ảnh trong guideline).
  function drawSkeleton(g, p, color, hot, opacity) {
    const els = p.elements || [];
    const r = Math.max(2.5, Math.min(S.imgW, S.imgH) * (hot ? 0.007 : 0.005));
    for (const [a, b] of p.edges || []) {
      const ea = els[a], eb = els[b];
      if (!ea || !eb || ea.outside || eb.outside) continue;
      g.append(svgEl('line', { x1: ea.x, y1: ea.y, x2: eb.x, y2: eb.y, stroke: color, 'stroke-width': hot ? 3 : 2,
        'vector-effect': 'non-scaling-stroke', opacity }));
    }
    // Skeleton có điểm trái/phải (người): tô theo L/R. Skeleton không có (lông mày, mắt, môi...): tô màu label,
    // riêng điểm đầu tiên tô vàng để thấy chiều đánh số.
    const sided = els.some((e) => e.side || /left|right/i.test(e.name));
    for (const [i, e] of els.entries()) {
      if (e.outside) continue;
      const c = sided ? sideColor(e) : (i === 0 ? SIDE_COLORS.mid : color);
      g.append(svgEl('circle', { cx: e.x, cy: e.y, r, fill: e.occluded ? 'none' : c, stroke: e.occluded ? c : '#ffffff',
        'stroke-width': e.occluded ? 2 : 1, 'vector-effect': 'non-scaling-stroke', opacity }));
      // Đề xuất đang trỏ tới: ghi số/tên điểm để so với hình đánh số trong guideline.
      if (hot && String(e.name).length <= 3) {
        const t = svgEl('text', { x: e.x + r * 1.4, y: e.y - r * 0.9, fill: '#ffffff', stroke: '#000000', 'stroke-width': r * 0.35,
          'paint-order': 'stroke', 'font-size': r * 3.2, 'font-weight': '700' });
        t.textContent = e.name;
        g.append(t);
      }
    }
  }

  function boxFrom(a, b) {
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  }

  function imagePoint(ev) {
    const r = overlaySvg.getBoundingClientRect();
    const x = ((ev.clientX - r.left) / r.width) * S.imgW;
    const y = ((ev.clientY - r.top) / r.height) * S.imgH;
    return [Math.min(Math.max(x, 0), S.imgW), Math.min(Math.max(y, 0), S.imgH)];
  }

  // ---- bấm điểm để tách vật (trái: điểm thuộc vật, phải hoặc Shift+trái: điểm KHÔNG thuộc vật) ----
  function startPoints() {
    if (!canvasEl()) return setStatus('Không tìm thấy canvas của CVAT nên không bấm điểm được.', 'error');
    run('Đang lấy ảnh frame…', async () => {
      const fr = await getFrame();
      S.imgW = fr.w; S.imgH = fr.h;
      S.box = null;
      S.points = { pos: [], neg: [], frame: S.frame, labelId: S.boxLabelId, pid: null };
      setStatus('Bấm lên vật cần tách. Bấm phải (hoặc Shift+bấm) vào chỗ KHÔNG thuộc vật để sửa. Enter hoặc "Xong" để giữ, Esc để huỷ.', 'info');
      drawOverlay();
    });
  }
  function endPoints(keep) {
    if (!S.points) return;
    if (!keep && S.points.pid) S.proposals = S.proposals.filter((p) => p.pid !== S.points.pid);
    S.points = null;
    setStatus(keep ? 'Đã giữ vật vừa tách. Duyệt rồi ghi như các đề xuất khác.' : 'Đã huỷ bấm điểm.', keep ? 'ok' : 'info');
    render(); drawOverlay();
  }
  function renderPointsBar() {
    const n = S.points.pos.length + S.points.neg.length;
    return h('div', { class: 'note row' }, h('span', { class: 'grow small-text', text: `Bấm điểm: ${S.points.pos.length} điểm thuộc vật, ${S.points.neg.length} điểm loại trừ.` }),
      h('button', { class: 'small', type: 'button', disabled: !n, text: 'Bỏ điểm cuối', onclick: () => { const last = S.points.lastNeg ? S.points.neg : S.points.pos; last.pop(); segmentPoints(); } }),
      h('button', { class: 'small primary', type: 'button', disabled: !S.points.pid, text: 'Xong', onclick: () => endPoints(true) }),
      h('button', { class: 'small', type: 'button', text: 'Huỷ', onclick: () => endPoints(false) }));
  }
  let pointsSeq = 0;
  async function segmentPoints() {
    const pts = S.points;
    if (!pts) return;
    drawOverlay(); render();
    if (!pts.pos.length) { if (pts.pid) { S.proposals = S.proposals.filter((p) => p.pid !== pts.pid); pts.pid = null; } drawOverlay(); return; }
    const seq = ++pointsSeq;
    try {
      const fr = await getFrame();
      setStatus('SAM3 đang tách vật theo các điểm…', 'busy');
      const res = await backend('POST', '/interactive', Object.assign(baseBody(), {
        image_b64: fr.b64, label_id: pts.labelId, box: [0, 0, 0, 0], mode: 'points', points: pts.pos, negative_points: pts.neg,
      }));
      if (seq !== pointsSeq || S.points !== pts || pts.frame !== S.frame) return;
      if (S.propFrame !== S.frame) { S.proposals = []; S.warnings = []; S.propFrame = S.frame; }
      S.proposals = S.proposals.filter((p) => p.pid !== pts.pid);
      const got = stamp(res.proposals || [], 'points')[0];
      if (got) {
        got.pid = S.proposals.reduce((m, p) => Math.max(m, p.pid), 0) + 1;
        got.checked = true;
        S.proposals.push(got); pts.pid = got.pid; S.sel = got.pid;
      }
      setStatus(got ? `Đã tách vật (${Math.round((got.score || 0) * 100)}%). Bấm thêm điểm để sửa, Enter để giữ.` : 'Không tách được vật ở các điểm này. Thử bấm vào giữa vật.', got ? 'ok' : 'error');
      (res.warnings || []).forEach((w) => S.warnings.includes(w) || S.warnings.push(w));
    } catch (err) {
      setStatus(err.message || String(err), 'error');
    }
    render(); drawOverlay();
  }
  overlayHost.addEventListener('contextmenu', (ev) => { if (S.points) ev.preventDefault(); });
  overlayHost.addEventListener('mousedown', (ev) => {
    if (!S.points) return;
    ev.preventDefault(); ev.stopPropagation();
    const pt = imagePoint(ev);
    const negative = ev.button === 2 || ev.shiftKey;
    (negative ? S.points.neg : S.points.pos).push(pt);
    S.points.lastNeg = negative;
    segmentPoints();
  }, true);
  document.addEventListener('keydown', (ev) => {
    if (!S.points) return;
    if (ev.key === 'Escape') { ev.preventDefault(); endPoints(false); }
    if (ev.key === 'Enter' && !ev.altKey) { ev.preventDefault(); endPoints(!!S.points.pid); }
  }, true);

  overlayHost.addEventListener('mousedown', (ev) => {
    if (!S.box || ev.button !== 0) return;
    ev.preventDefault(); ev.stopPropagation();
    S.box.start = imagePoint(ev); S.box.cur = S.box.start;
  });
  overlayHost.addEventListener('mousemove', (ev) => {
    if (!S.box || !S.box.start) return;
    S.box.cur = imagePoint(ev);
    drawOverlay();
  });
  overlayHost.addEventListener('mouseup', (ev) => {
    if (!S.box || !S.box.start) return;
    ev.preventDefault(); ev.stopPropagation();
    const box = boxFrom(S.box.start, imagePoint(ev));
    const mode = S.box.mode;
    S.box = null;
    drawOverlay();
    if (box[2] - box[0] < 3 || box[3] - box[1] < 3) return setStatus('Hộp quá nhỏ, hãy vẽ lại.', 'error');
    finishBox(box, mode);
  });

  // ---------------------------------------------------------------- panel --
  const CSS = `
    :host {
      --bg: #ffffff; --bg-soft: #f4f5f7; --line: #d9dce1; --text: #1d2433; --muted: #5b6475;
      --accent: #2456d6; --accent-text: #ffffff; --ok: #0f7b3f; --warn: #a15c00; --err: #b42318;
      --radius: 6px; --gap: 8px; --font: 13px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      all: initial; position: fixed; z-index: 990; top: 72px; right: 16px; width: 380px;
      font: var(--font); color: var(--text);
    }
    * { box-sizing: border-box; }
    .panel { background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius);
      box-shadow: 0 6px 24px rgba(0,0,0,.18); display: flex; flex-direction: column; max-height: calc(100vh - 96px); }
    header { display: flex; align-items: center; gap: var(--gap); padding: 8px 10px; border-bottom: 1px solid var(--line);
      cursor: move; user-select: none; }
    header h1 { font: inherit; font-weight: 600; margin: 0; flex: 1; }
    .logo { width: 20px; height: 20px; flex: none; display: block; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); flex: none; }
    .dot.ok { background: var(--ok); } .dot.bad { background: var(--err); } .dot.mock { background: var(--warn); }
    .meta { padding: 6px 10px; color: var(--muted); border-bottom: 1px solid var(--line); display: flex; gap: var(--gap); align-items: center; flex-wrap: wrap; }
    nav { display: flex; border-bottom: 1px solid var(--line); }
    nav button { flex: 1; border: 0; border-radius: 0; background: var(--bg-soft); padding: 7px 2px; border-bottom: 2px solid transparent; font-size: 12px; white-space: nowrap; }
    nav button[aria-selected="true"] { background: var(--bg); border-bottom-color: var(--accent); font-weight: 600; }
    .body { padding: 10px; overflow: auto; display: flex; flex-direction: column; gap: var(--gap); }
    button { font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius);
      padding: 6px 10px; cursor: pointer; }
    button:hover:not(:disabled) { border-color: var(--accent); }
    button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    button.primary { background: var(--accent); color: var(--accent-text); border-color: var(--accent); font-weight: 600; }
    button.small { padding: 2px 6px; font-size: 12px; }
    textarea { width: 100%; resize: vertical; }
    input[type="text"], input[type="number"], input[type="password"], select, textarea { font: inherit; color: var(--text); background: var(--bg);
      border: 1px solid var(--line); border-radius: var(--radius); padding: 4px 6px; min-width: 0; }
    label.field { display: flex; flex-direction: column; gap: 3px; }
    label.check { display: flex; gap: 6px; align-items: center; }
    .row { display: flex; gap: var(--gap); align-items: center; flex-wrap: wrap; }
    .grow { flex: 1; min-width: 0; }
    .muted { color: var(--muted); } .small-text { font-size: 12px; }
    .status { padding: 6px 10px; border-top: 1px solid var(--line); min-height: 30px; }
    .status.ok { color: var(--ok); } .status.error { color: var(--err); } .status.busy { color: var(--accent); }
    .note { background: var(--bg-soft); border-left: 3px solid var(--warn); padding: 6px 8px; border-radius: var(--radius); }
    .version-note { display: flex; align-items: center; gap: 8px; } .version-note span { flex: 1; }
    ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
    li.item { border: 1px solid var(--line); border-radius: var(--radius); padding: 5px 6px; display: flex; flex-direction: column; gap: 3px; }
    li.item:hover, li.item.hot { border-color: var(--accent); background: var(--bg-soft); }
    .swatch { width: 10px; height: 10px; border-radius: 2px; flex: none; }
    .badge { font-size: 11px; padding: 1px 5px; border-radius: 10px; background: var(--bg-soft); border: 1px solid var(--line); color: var(--warn); }
    .badge.error { color: var(--err); } .badge.pass { color: var(--ok); }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 3px 4px; border-bottom: 1px solid var(--line); vertical-align: middle; }
    th { color: var(--muted); font-weight: 600; font-size: 12px; }
    td input[type="text"] { width: 100%; } td input[type="number"] { width: 58px; }
    h2 { font: inherit; font-weight: 600; margin: 4px 0 0; }
    li.item.sel { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
    .chip { display: inline-flex; align-items: center; gap: 2px; } .chip.off { opacity: .45; text-decoration: line-through; }
    .filter { display: flex; flex-direction: column; gap: 4px; }
    .tour { border-left-color: var(--accent); display: flex; flex-direction: column; gap: 4px; }
    .tour-target { outline: 3px solid var(--accent) !important; outline-offset: 2px; }
    .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }
    .tile { border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; display: flex; flex-direction: column; gap: 2px; }
    .tile.current { border-color: var(--accent); } .tile .row { padding: 2px 4px; }
    /* panel dialog (replaces browser confirm/prompt): Esc, click outside or Cancel closes it */
    .dlg-back { position: fixed; inset: 0; z-index: 1000; background: rgba(9,14,28,.45); display: flex; align-items: center; justify-content: center; padding: 20px; }
    .dlg { background: var(--bg); color: var(--text); border-radius: 10px; width: 100%; max-width: 440px; max-height: 80vh; display: flex; flex-direction: column;
      box-shadow: 0 12px 40px rgba(0,0,0,.3); font: var(--font); }
    .dlg p { margin: 0; padding: 18px 18px 6px; white-space: pre-wrap; overflow: auto; font-size: 13.5px; }
    .dlg textarea { margin: 6px 18px 0; min-height: 84px; resize: vertical; }
    .dlg .dlg-foot { display: flex; align-items: center; gap: 8px; padding: 12px 18px; margin-top: 10px; border-top: 1px solid var(--line); }
    .dlg .dlg-foot small { flex: 1; color: var(--muted); font-size: 11.5px; }
    /* 0.9.2 layout: roomier header, one-line context, icon tabs, fixed save bar, collapsible sections, grouped settings */
    :host { --radius: 8px; --gap: 10px; }
    .panel { border-radius: 12px; overflow: hidden; }
    header { height: 46px; padding: 0 6px 0 12px; gap: 8px; }
    header h1 { font-size: 14px; }
    .ico { width: 18px; height: 18px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .ico.s { width: 15px; height: 15px; }
    .hchip { display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px; border-radius: 11px; font-size: 11.5px; font-weight: 600;
      background: var(--bg-soft); color: var(--muted); white-space: nowrap; }
    .hchip::before { content: ""; width: 7px; height: 7px; border-radius: 4px; background: currentColor; }
    .hchip.ok { background: #e3f4ea; color: var(--ok); } .hchip.mock { background: #fdf1de; color: var(--warn); } .hchip.bad { background: #fde1dd; color: var(--err); }
    button.ib { width: 30px; height: 30px; padding: 0; border-color: transparent; background: none; color: var(--muted); display: inline-flex; align-items: center; justify-content: center; flex: none; }
    button.ib:hover:not(:disabled) { background: var(--bg-soft); border-color: transparent; color: var(--text); }
    button.ib[aria-pressed="true"] { background: var(--bg-soft); color: var(--accent); }
    .meta { padding: 7px 12px; gap: 8px; flex-wrap: nowrap; background: var(--bg-soft); font-size: 12.5px; }
    .meta .job { white-space: nowrap; } .meta .job b { color: var(--text); font-weight: 600; }
    .meta select.prof { margin-left: auto; max-width: 205px; height: 28px; font-size: 12px; padding: 0 6px; }
    .meta-note { padding: 8px 12px; border-bottom: 1px solid var(--line); }
    .meta-note:empty { display: none; }
    nav { display: grid; grid-template-columns: repeat(6, 1fr); }
    nav button { display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 7px 0 6px; font-size: 11px; color: var(--muted); background: var(--bg); }
    nav button[aria-selected="true"] { color: var(--accent); }
    nav.back { display: flex; align-items: center; gap: 6px; padding: 6px 8px; }
    nav.back b { font-size: 14px; }
    .body { padding: 12px; gap: 12px; }
    button.big { width: 100%; height: 42px; font-size: 14px; display: flex; align-items: center; justify-content: center; gap: 8px; }
    kbd { font: 11px Consolas, monospace; padding: 1px 5px; border-radius: 4px; background: rgba(255,255,255,.22); }
    button:not(.primary) kbd { background: var(--bg-soft); color: var(--muted); }
    h4 { margin: 0; font-size: 11.5px; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
    .tools { display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto; gap: 6px; }
    .tools button { white-space: nowrap; padding: 6px 8px; font-size: 12px; display: inline-flex; align-items: center; gap: 5px; }
    .empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 16px 10px; color: var(--muted); text-align: center;
      border: 1px dashed var(--line); border-radius: 10px; font-size: 12.5px; }
    .empty b { color: var(--text); }
    .actions { padding: 10px 12px; border-top: 1px solid var(--line); background: var(--bg-soft); display: flex; flex-direction: column; gap: 6px; }
    .actions:empty { display: none; }
    .actions .row { flex-wrap: nowrap; gap: 6px; }
    .actions button.primary { display: flex; align-items: center; justify-content: center; gap: 8px; }
    .actions .row button:not(.primary) { flex: 1; padding: 5px 6px; font-size: 12px; white-space: nowrap; }
    .sec { border: 1px solid var(--line); border-radius: 10px; }
    .sec > .sec-h { width: 100%; display: flex; align-items: center; gap: 8px; border: 0; border-radius: 10px; background: none; padding: 10px 12px; font-weight: 600; text-align: left; }
    .sec > .sec-h > span { flex: 1; min-width: 0; }
    .sec > .sec-h small { font-weight: 400; color: var(--muted); font-size: 12px; white-space: nowrap; }
    .sec > .sec-h .chev { transition: transform .15s; }
    .sec.open > .sec-h .chev { transform: rotate(90deg); }
    .sec > .sec-b { padding: 0 12px 12px; display: flex; flex-direction: column; gap: var(--gap); }
    .sec:not(.open) > .sec-b { display: none; }
    .group { display: flex; flex-direction: column; gap: 10px; }
    label.check.sw { justify-content: space-between; gap: 12px; cursor: pointer; }
    label.check.sw > span { order: -1; flex: 1; }
    label.check.sw input { appearance: none; -webkit-appearance: none; width: 32px; height: 18px; border-radius: 9px; background: #c9cfdb; position: relative;
      flex: none; margin: 0; cursor: pointer; transition: background .15s; }
    label.check.sw input::after { content: ""; position: absolute; left: 2px; top: 2px; width: 14px; height: 14px; border-radius: 7px; background: #fff; transition: transform .15s; }
    label.check.sw input:checked { background: var(--accent); }
    label.check.sw input:checked::after { transform: translateX(14px); }
    .sub { display: block; color: var(--muted); font-size: 11.5px; }
  `;

  const host = h('div', { id: 'sam3-cvat-assist' });
  const root = host.attachShadow({ mode: 'open' });
  root.append(h('style', { text: CSS }));
  const els = {
    dot: h('span', { class: 'dot', title: 'Trạng thái backend' }),
    meta: h('div', { class: 'meta' }),
    nav: h('nav', { role: 'tablist' }),
    body: h('div', { class: 'body' }),
    status: h('div', { class: 'status', role: 'status' }),
    collapse: h('button', { class: 'small', type: 'button', onclick: () => { cfg.collapsed = !cfg.collapsed; saveCfg(); render(); } }),
  };
  // Logo của tool (chữ S nét polyline có đỉnh), vẽ bằng SVG để không phụ thuộc ảnh ngoài trang CVAT
  function logoSvg() {
    const NS = 'http://www.w3.org/2000/svg';
    const el = (tag, attrs) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
    const svg = el('svg', { viewBox: '0 0 28 28', class: 'logo', 'aria-hidden': 'true' });
    svg.append(el('rect', { width: 28, height: 28, rx: 7, fill: '#2F6BFF' }),
      el('path', { d: 'M19 8H10l-2 3 2 3h8l2 3-2 3H9', stroke: '#fff', 'stroke-width': 2.4, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    for (const [cx, cy] of [[19, 8], [8, 11], [18, 14], [20, 17], [9, 20]]) svg.append(el('circle', { cx, cy, r: 1.7, fill: '#fff' }));
    return svg;
  }
  // Bộ icon của panel: nét 1,8 trên lưới 24, cùng bộ với bảng điều khiển của app
  const ICONS = {
    spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z|M19 15l.7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7z',
    layers: 'M12 3l9 5-9 5-9-5z|M3 13l9 5 9-5',
    check: 'M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z|M8.5 12l2.5 2.5 4.5-5',
    book: 'M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z|M4 19V5M8 7h7',
    tag: 'M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z|M7.5 6a1.5 1.5 0 1 0 0 3 1.5 1.5 0 1 0 0-3z',
    brain: 'M12 5a3 3 0 0 0-5.6-1.5A3 3 0 0 0 4 8a3 3 0 0 0 0 6 3 3 0 0 0 3 4 3 3 0 0 0 5 1zM12 5a3 3 0 0 1 5.6-1.5A3 3 0 0 1 20 8a3 3 0 0 1 0 6 3 3 0 0 1-3 4 3 3 0 0 1-5 1',
    gear: 'M12 9a3 3 0 1 0 0 6 3 3 0 1 0 0-6z|M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1',
    min: 'M5 12h14', max: 'M6 15l6-6 6 6',
    left: 'M15 6l-6 6 6 6', right: 'M9 6l6 6-6 6',
    box: 'M4 5h16v14H4z', point: 'M12 9a3 3 0 1 0 0 6 3 3 0 1 0 0-6z|M12 2v4M12 18v4M2 12h4M18 12h4',
    similar: 'M3 3h8v8H3z|M13 13h8v8h-8z|M15 7h4M17 5v4',
    undo: 'M9 14L4 9l5-5|M4 9h10a6 6 0 0 1 0 12h-3',
  };
  function ico(name, cls) {
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('class', 'ico' + (cls ? ' ' + cls : '')); svg.setAttribute('aria-hidden', 'true');
    for (const d of ICONS[name].split('|')) { const p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); svg.append(p); }
    if (name === 'box') svg.firstChild.setAttribute('stroke-dasharray', '3 2.5');
    return svg;
  }
  els.chip = h('span', { class: 'hchip' });
  els.gear = h('button', { class: 'ib', type: 'button', onclick: () => { S.tab = S.tab === 'settings' ? (S.prevTab || 'propose') : (S.prevTab = S.tab, 'settings'); render(); } }, ico('gear'));
  els.collapse.className = 'ib';
  els.metaNote = h('div', { class: 'meta-note' });
  els.actions = h('div', { class: 'actions' });
  const headerEl = h('header', {}, logoSvg(), h('h1', { text: 'SAM3 Label Assist' }), els.chip, els.gear, els.collapse);
  root.append(h('div', { class: 'panel' }, headerEl, els.meta, els.metaNote, els.nav, els.body, els.actions, els.status));
  // Phím gõ trong panel không được lọt ra thành phím tắt của CVAT.
  for (const type of ['keydown', 'keyup', 'keypress']) host.addEventListener(type, (ev) => ev.stopPropagation());

  function applyPos() {
    if (cfg.pos) { host.style.left = cfg.pos[0] + 'px'; host.style.top = cfg.pos[1] + 'px'; host.style.right = 'auto'; }
  }
  headerEl.addEventListener('mousedown', (ev) => {
    if (ev.target.closest('button')) return;
    const r = host.getBoundingClientRect();
    const dx = ev.clientX - r.left, dy = ev.clientY - r.top;
    const move = (e) => {
      cfg.pos = [Math.max(0, Math.min(window.innerWidth - 80, e.clientX - dx)), Math.max(0, Math.min(window.innerHeight - 40, e.clientY - dy))];
      applyPos();
    };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); saveCfg(); };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
    ev.preventDefault();
  });

  function renderHeader() {
    const hl = S.health;
    els.chip.className = 'hchip' + (!hl ? '' : !hl.ok ? ' bad' : hl.is_mock ? ' mock' : ' ok');
    els.chip.textContent = translateText(!hl ? 'Đang kiểm tra' : !hl.ok ? 'Chưa nối backend' : hl.is_mock ? 'Giả lập' : 'Sẵn sàng');
    const gpu = gpuLine();
    els.chip.title = translateText(!hl ? 'Chưa kiểm tra backend' : !hl.ok ? 'Backend chưa sẵn sàng: mở Cài đặt để kiểm tra kết nối'
      : `Backend: ${hl.engine} trên ${hl.device}` + (gpu ? ` · ${gpu.textContent}` : ''));
    els.gear.title = translateText('Cài đặt panel');
    els.gear.setAttribute('aria-label', translateText('Cài đặt panel'));
    els.gear.setAttribute('aria-pressed', String(S.tab === 'settings'));
    put(els.collapse, ico(cfg.collapsed ? 'max' : 'min'));
    els.collapse.title = translateText(cfg.collapsed ? 'Mở rộng panel' : 'Thu gọn panel') + ` (${keyName(keyOf('panel'))})`;
    els.collapse.setAttribute('aria-label', els.collapse.title);
  }

  function renderStatus() {
    els.status.className = 'status ' + S.statusKind;
    els.status.textContent = translateText(S.status);
  }

  function renderMeta() {
    const profileSelect = h('select', {
      title: 'Profile guideline',
      disabled: !!S.busy,
      onchange: (ev) => {
        cfg.profileId = ev.target.value; saveCfg(); clearProposals();
        run('Đang áp profile…', resolveLabels);
      },
    }, h('option', { value: 'auto', text: 'Tự chọn profile' }),
    S.profiles.map((p) => h('option', { value: p.id, text: p.title })));
    profileSelect.value = cfg.profileId;
    profileSelect.classList.add('prof');
    if (S.resolved) profileSelect.title = translateText(`Profile guideline · đang dùng: ${S.resolved.title}`);
    if (cfg.profileId === 'auto' && S.resolved) profileSelect.options[0].text = translateText(`Tự chọn: ${S.resolved.title}`);
    const r = S.job ? jobRange() : null;
    put(els.meta,
      h('span', { class: 'job' }, h('b', { text: `Job ${S.jobId ?? '?'}` }), ` · Frame ${S.frame ?? '?'}${r ? '/' + r.stop : ''}`),
      profileSelect,
    );
    put(els.metaNote, versionNotice());
  }

  function renderNav() {
    if (S.tab === 'settings') {               // Cài đặt mở bằng nút bánh răng, thay thanh tab bằng dòng quay lại
      els.nav.className = 'back';
      els.nav.setAttribute('role', 'navigation');
      put(els.nav, h('button', { class: 'ib', type: 'button', title: 'Quay lại', 'aria-label': 'Quay lại',
        onclick: () => { S.tab = S.prevTab || 'propose'; render(); } }, ico('left')), h('b', { text: 'Cài đặt panel' }));
      return;
    }
    els.nav.className = '';
    els.nav.setAttribute('role', 'tablist');
    const tabs = [['propose', 'Gợi ý', 'spark'], ['job', 'Cả job', 'layers'], ['qa', 'Kiểm tra', 'check'], ['guide', 'Guideline', 'book'],
      ['labels', 'Label', 'tag'], ['learn', 'Tự học', 'brain']];
    put(els.nav, ...tabs.map(([id, name, icon]) => h('button', {
      type: 'button', role: 'tab', 'aria-selected': String(S.tab === id),
      onclick: () => { S.tab = id; S.highlight = null; render(); drawOverlay(); if (id === 'guide') loadGuide(); if (id === 'learn') loadLearn(); },
    }, ico(icon), h('span', { text: name }))));
  }

  function flagBadges(flags) {
    return flags.map((f) => h('span', { class: 'badge', title: f.message, text: FLAG_NAMES[f.code] || f.code }));
  }

  function hover(li, key) {
    li.addEventListener('mouseenter', () => { S.highlight = key; li.classList.add('hot'); drawOverlay(); });
    li.addEventListener('mouseleave', () => { S.highlight = null; li.classList.remove('hot'); drawOverlay(); });
  }

  function renderPropose() {
    const out = [tourCard()];
    const ready = S.resolved && S.health && S.health.ok;
    if (S.health && S.health.ok && S.health.is_mock) {
      out.push(h('div', { class: 'note', text: 'Backend đang chạy chế độ GIẢ LẬP (mock): kết quả không phải của SAM3, chỉ để thử giao diện.' }));
    }
    out.push(h('button', { class: 'primary big ' + tourOn('propose'), type: 'button', disabled: !ready || !!S.busy, onclick: propose,
      title: keyName(keyOf('propose')) }, ico('spark'), h('span', { text: 'Gợi ý nhãn cho frame này' }), h('kbd', { text: keyName(keyOf('propose')) })));
    const boxLabel = h('select', { title: 'Label cho hộp vẽ tay', onchange: (ev) => { S.boxLabelId = Number(ev.target.value); } },
      S.labels.map((l) => h('option', { value: String(l.id), text: l.name })));
    if (S.boxLabelId) boxLabel.value = String(S.boxLabelId);
    out.push(h('div', { class: 'group', style: 'gap:6px' }, h('h4', { text: 'Tự vẽ một nhãn' }), h('div', { class: 'tools' },
      boxLabel,
      h('button', { type: 'button', disabled: !ready || !!S.busy, title: 'Vẽ một hộp, SAM3 lấy đúng đối tượng đó', onclick: () => startBox('single') }, ico('box', 's'), h('span', { text: 'Vẽ hộp' })),
      h('button', { type: 'button', disabled: !ready || !!S.busy, title: 'Bấm một điểm lên vật (bấm thêm điểm để sửa), SAM3 tách đúng vật đó', onclick: () => startPoints() }, ico('point', 's'), h('span', { text: 'Bấm điểm' })),
      h('button', { type: 'button', disabled: !ready || !!S.busy, title: 'Vẽ một hộp làm mẫu, SAM3 tìm mọi đối tượng giống vậy', onclick: () => startBox('similar') }, ico('similar', 's'), h('span', { text: 'Tìm tương tự' })),
    )));
    if (S.points) out.push(renderPointsBar());
    for (const w of S.warnings) out.push(h('div', { class: 'note', text: w }));
    if (S.propSource === 'batch' && S.propFrame === S.frame) {
      out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: 'Đề xuất này lấy từ lượt gợi ý cho cả job (tab Cả job).' }));
    }
    if (S.prefetching !== null && cfg.prefetch) out.push(h('div', { class: 'muted small-text', text: `Đang chạy sẵn frame ${S.prefetching}…` }));

    if (S.proposals.length && S.propFrame === S.frame) {
      const shown = S.proposals.filter(visible);
      const checked = shown.filter((p) => p.checked).length;
      const hiddenCount = S.proposals.length - shown.length;
      // Bộ lọc tức thì: ngưỡng điểm + bật/tắt từng class (không chạy lại model)
      const ids = [...new Set(S.proposals.map((p) => p.label_id))];
      out.push(h('div', { class: 'filter ' + tourOn('filter') },
        h('label', { class: 'row small-text' }, h('span', { text: `Ngưỡng ${Math.round(S.minScore * 100)}%` }),
          h('input', { class: 'grow', type: 'range', min: '0', max: '0.95', step: '0.05', value: String(S.minScore), title: 'Ẩn đề xuất có điểm thấp hơn',
            oninput: (ev) => { S.minScore = Number(ev.target.value); render(); drawOverlay(); } })),
        ids.length > 1 ? h('div', { class: 'row small-text' }, ids.map((id) => {
          const l = labelById(id);
          const n = S.proposals.filter((p) => p.label_id === id).length;
          const off = S.hiddenLabels.has(id);
          return h('button', { class: 'chip small' + (off ? ' off' : ''), type: 'button', title: off ? 'Đang ẩn class này: bấm để hiện' : 'Bấm để ẩn class này',
            onclick: () => { if (off) S.hiddenLabels.delete(id); else S.hiddenLabels.add(id); render(); drawOverlay(); } },
          h('span', { class: 'swatch', style: `background:${labelColor(id)}` }), ` ${l ? l.name : id} (${n})`);
        })) : null));
      out.push(h('div', { class: 'row' },
        h('span', { class: 'grow muted', text: `${shown.length} đề xuất · ${checked} đã chọn${hiddenCount ? ` · ${hiddenCount} đang ẩn` : ''}` }),
        h('button', { class: 'small', type: 'button', text: 'Chọn hết', onclick: () => { shown.forEach((p) => { p.checked = true; }); render(); drawOverlay(); } }),
        h('button', { class: 'small', type: 'button', text: 'Bỏ mục có cờ', onclick: () => { shown.forEach((p) => { if (p.flags.length) p.checked = false; }); render(); drawOverlay(); } }),
      ));
      for (const t of S.hints || []) out.push(h('div', { class: 'note small-text', text: t }));
      out.push(h('ul', { class: tourOn('list') }, shown.map((p) => {
        const options = S.labels.filter((l) => !l.type || l.type === 'any' || l.type === p.type || l.id === p.label_id);
        const select = h('select', { class: 'grow', title: 'Đổi class', onchange: (ev) => { p.label_id = Number(ev.target.value); p.label_name = (labelById(p.label_id) || {}).name; p.userTouched = true; render(); drawOverlay(); } },
          options.map((l) => h('option', { value: String(l.id), text: l.name })));
        select.value = String(p.label_id);
        const li = h('li', { class: 'item' + (S.sel === p.pid ? ' sel' : ''), 'data-pid': String(p.pid) },
          h('div', { class: 'row' },
            h('input', { type: 'checkbox', checked: p.checked, title: `Ghi nhãn này vào CVAT (${keyName(keyOf('toggle'))}, hoặc giữ Alt rồi bấm lên ảnh)`, onchange: (ev) => { toggleProposal(p, ev.target.checked); } }),
            h('span', { class: 'swatch', style: `background:${labelColor(p.label_id)}` }),
            select,
            h('span', { class: 'muted small-text', text: p.type === 'skeleton' ? skeletonText(p) : `${p.type} · ${Math.round(p.score * 100)}%` }),
            p.source === 'similar' && S.similar && S.similar.frame === S.frame ? h('button', { class: 'small', type: 'button', text: 'Loại trừ',
              title: 'Vật này bị nhận nhầm: dùng nó làm mẫu ÂM, SAM3 tìm lại và bỏ mọi vật giống nó', disabled: !!S.busy, onclick: () => excludeSimilar(p) }) : null,
          ),
          p.box_adjust ? h('div', { class: 'muted small-text', text: 'Hộp đã được chỉnh theo cách bạn hay sửa (tab Tự học).' }) : null,
          p.auto_attributes && Object.keys(p.auto_attributes).length ? h('div', { class: 'muted small-text',
            text: 'Tự điền: ' + Object.entries(p.auto_attributes).map(([k, v]) => `${k} = ${v}`).join(', ') + ' (đổi được trong CVAT sau khi ghi)' }) : null,
          p.flags.length || p.occluded ? h('div', { class: 'row' },
            p.occluded ? h('span', { class: 'badge', text: 'occluded' }) : null,
            flagBadges(p.flags),
            p.flags.length ? h('button', { class: 'small', type: 'button', text: 'Tạo Issue', title: 'Gửi case này cho mentor qua Issue của CVAT',
              onclick: () => createIssue(p.flags[0].code, `${p.label_name}: ${p.flags.map((f) => f.message).join(' ')}`, p.bbox) }) : null,
          ) : null,
          p.flags.length ? h('div', { class: 'muted small-text', text: p.flags.map((f) => f.message).join(' ') }) : null,
        );
        li.addEventListener('click', (ev) => { if (!ev.target.closest('input,select,button')) { S.sel = p.pid; render(); drawOverlay(); } });
        hover(li, 'p' + p.pid);
        return li;
      })));
      out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: `Bấm một dòng để xem trên ảnh · ${keyName(keyOf('toggle'))} bật/tắt dòng đang chọn · giữ Alt rồi bấm lên ảnh để tick` }));
    } else if (ready) {
      out.push(h('div', { class: 'empty' }, ico('spark'), h('b', { text: 'Chưa có đề xuất cho frame này' }),
        h('span', { text: 'Tool chỉ gợi ý. Bạn duyệt từng nhãn rồi mới ghi vào CVAT.' })));
    }
    const st = statsToday();
    if (st.applied) out.push(h('div', { class: 'muted small-text', text: `Hôm nay: ${st.applied} nhãn, ${st.frames} frame, ước tính tiết kiệm ${st.minutes} phút.` }));
    return out;
  }

  // Thanh Ghi luôn nằm dưới cùng (không phải cuộn tìm): nút chính ghi rồi sang frame tiếp; hàng dưới: chỉ ghi, hoàn tác, bỏ đề xuất
  function renderActions() {
    if (S.tab !== 'propose') return [];
    const nUndo = undoStackOf(S.jobId).length;
    const undo = h('button', { class: tourOn('undo'), type: 'button', disabled: !!S.busy || !nUndo, onclick: undoLast,
      title: `${keyName(keyOf('undo'))}. Bấm nhiều lần để lùi thêm (tối đa 20 lần ghi).` }, ico('undo', 's'), h('span', { text: nUndo ? `Hoàn tác (${nUndo})` : 'Hoàn tác' }));
    if (!(S.proposals.length && S.propFrame === S.frame)) return nUndo ? [h('div', { class: 'row' }, undo)] : [];
    const checked = S.proposals.filter(visible).filter((p) => p.checked).length;
    const next = nextFrameOf(S.frame);
    const drop = h('button', { type: 'button', disabled: !!S.busy, text: 'Bỏ đề xuất', onclick: () => {
      if (S.batch[S.frame]) { delete S.batch[S.frame]; persist('batch'); }
      clearProposals(); render();
    } });
    if (next === null) {                       // frame cuối: chỉ có ghi
      return [h('button', { class: 'primary big ' + tourOn('apply'), type: 'button', disabled: !!S.busy || !checked, onclick: () => applyProposals(),
        title: keyName(keyOf('apply')) }, h('span', { text: `Ghi ${checked} nhãn` }), h('kbd', { text: keyName(keyOf('apply')) })),
      h('div', { class: 'row' }, undo, drop)];
    }
    return [
      h('button', { class: 'primary big ' + tourOn('apply'), type: 'button', disabled: !!S.busy, onclick: applyAndNext,
        title: `${keyName(keyOf('applyNext'))}: ghi rồi sang frame ${next}, đề xuất của frame đó đã chạy sẵn` },
      h('span', { text: checked ? `Ghi ${checked}, sang frame tiếp` : 'Sang frame tiếp' }), h('kbd', { text: keyName(keyOf('applyNext')) })),
      h('div', { class: 'row' },
        h('button', { type: 'button', disabled: !!S.busy || !checked, onclick: () => applyProposals(), title: `${keyName(keyOf('apply'))}: ghi nhưng ở lại frame này`,
          text: `Chỉ ghi ${checked} nhãn` }),
        undo, drop),
    ];
  }

  function setOverride(label, patch) {
    const key = normName(label.name);
    cfg.overrides[key] = Object.assign({}, cfg.overrides[key], patch);
    saveCfg();
    clearProposals();
    run('Đang cập nhật label…', resolveLabels);
  }

  function renderLabels() {
    if (!S.resolved) {
      const reload = h('button', { type: 'button', disabled: !!S.busy, text: 'Đọc lại label từ CVAT', onclick: loadJob });
      if (S.labels.length) {
        return [h('p', { class: 'muted', style: 'margin:0', text: `Đã đọc ${S.labels.length} label từ CVAT nhưng chưa nối được backend. Kiểm tra ở Cài đặt panel (nút bánh răng).` }), reload];
      }
      return [
        h('div', { class: 'note', text: 'CVAT không trả về label nào cho job này, nên tool chưa gợi ý được.' }),
        S.loadError ? h('p', { style: 'margin:0;color:var(--err)', text: S.loadError }) : null,
        S.labelDiag.length ? h('p', { class: 'muted small-text', style: 'margin:0', text: 'Các cách đã thử:' }) : null,
        h('ul', {}, S.labelDiag.map((line) => h('li', { class: 'small-text', style: 'word-break:break-all', text: line }))),
        reload,
      ];
    }
    const semantic = S.resolved.mode === 'semantic';
    const rows = S.resolved.labels.map((c) => {
      const label = labelById(c.label_id) || { name: c.name };
      const shape = h('select', { disabled: semantic || !c.supported, title: 'Loại shape', onchange: (ev) => setOverride(label, { shape: ev.target.value }) },
        SHAPES.filter((s) => c.cvat_type === 'any' || c.cvat_type === s || s === c.shape).map((s) => h('option', { value: s, text: s })));
      shape.value = c.shape;
      const derived = !c.prompts.length;
      if (c.shape === 'skeleton') {
        // Tên label trong task thường khác tên trong guideline (person_vf17 thay cho person). Tool tự nhận theo tên gần
        // giống hoặc số điểm; khi nó không nhận ra, hoặc nhận sai, người dùng chỉ tay ở ô này.
        const known = S.resolved.skeletons || [];
        const guessed = c.matched && c.match_how !== 'exact';
        let pickEntry = null;
        if (known.length && (!c.matched || guessed)) {
          pickEntry = h('select', { title: 'Label này là skeleton nào của guideline', style: 'max-width:100%;margin-top:3px',
            onchange: (ev) => setOverride(label, { entry: ev.target.value || null }) },
            [h('option', { value: '', text: c.matched ? 'Để tool tự nhận' : 'Chọn skeleton của guideline…' })]
              .concat(known.map((k) => h('option', { value: k.name, text: `${k.name} (${k.points} điểm)` }))));
          pickEntry.value = c.match_how === 'manual' ? c.entry : '';
        }
        return h('tr', { title: c.note || '' },
          h('td', {}, h('input', { type: 'checkbox', checked: c.enabled, disabled: !c.supported, title: 'Bật label này', onchange: (ev) => setOverride(label, { enabled: ev.target.checked }) })),
          h('td', {}, h('div', { text: c.name }),
            c.note ? h('div', { class: 'small-text', style: `color:var(${c.supported && !c.matched_by_order && !(c.missing || []).length ? '--muted' : '--warn'})`, text: c.note }) : null,
            pickEntry),
          h('td', { text: `skeleton ${c.keypoints.length} điểm` }),
          h('td', {}, c.role === 'face' ? h('span', { class: 'muted small-text', text: 'model khuôn mặt' })
            : h('input', { type: 'text', value: c.prompts.join(', '), title: 'Prompt SAM3 để tìm người',
              onchange: (ev) => setOverride(label, { prompts: ev.target.value.split(',').map((x) => x.trim()).filter(Boolean) }) })),
          h('td', {}, c.role === 'face' ? null : h('input', { type: 'number', min: '0.05', max: '0.99', step: '0.05', value: String(c.threshold),
            title: 'Ngưỡng tin cậy khi tìm người', onchange: (ev) => setOverride(label, { threshold: Number(ev.target.value) }) })),
        );
      }
      return h('tr', { title: c.note || '' },
        h('td', {}, h('input', { type: 'checkbox', checked: c.enabled, disabled: !c.supported, title: 'Bật label này', onchange: (ev) => setOverride(label, { enabled: ev.target.checked }) })),
        h('td', {}, h('div', { text: c.name }), c.note ? h('div', { class: 'small-text', style: 'color:var(--warn)', text: c.note }) : null,
          !c.matched && S.resolved.profile_id !== 'generic' ? h('div', { class: 'small-text muted', text: 'Không có trong guideline' }) : null),
        h('td', {}, shape),
        h('td', {}, h('input', { type: 'text', value: c.prompts.join(', '), placeholder: derived ? 'suy ra bằng luật' : 'prompt tiếng Anh',
          title: 'Prompt cho SAM3, cách nhau bằng dấu phẩy',
          onchange: (ev) => setOverride(label, { prompts: ev.target.value.split(',').map((s) => s.trim()).filter(Boolean) }) })),
        h('td', {}, h('input', { type: 'number', min: '0.05', max: '0.99', step: '0.05', value: String(c.threshold),
          title: c.learned && c.learned.threshold ? `Tool đã học: ${c.learned.threshold_from} → ${c.learned.threshold} (${c.learned.threshold_reason}). Gõ số khác để đặt tay.` : 'Ngưỡng tin cậy',
          onchange: (ev) => setOverride(label, { threshold: Number(ev.target.value) }) }),
          c.learned ? h('div', { class: 'small-text', style: 'color:var(--accent, #4a90d9)', text: 'tự học', title: [c.learned.threshold_reason, c.learned.box_reason].filter(Boolean).join(' · ') }) : null),
      );
    });
    return [
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'Label lấy từ task CVAT. Prompt nên là cụm danh từ tiếng Anh ngắn. Thay đổi được nhớ theo tên label.' }),
      h('table', {}, h('thead', {}, h('tr', {}, ['', 'Label', 'Shape', 'Prompt', 'Ngưỡng'].map((t) => h('th', { text: t })))), h('tbody', {}, rows)),
      h('div', { class: 'row' },
        h('button', { type: 'button', disabled: !!S.busy, text: 'Về mặc định của profile', onclick: () => { cfg.overrides = {}; saveCfg(); clearProposals(); run('Đang cập nhật label…', resolveLabels); } }),
        h('button', { type: 'button', disabled: !!S.busy, text: 'Nạp lại profile', title: 'Đọc lại các file YAML trong thư mục profiles/ của backend',
          onclick: () => run('Đang nạp lại profile…', async () => { await backend('POST', '/profiles/reload'); await checkBackend(); await resolveLabels(); if (S.profMgr) await loadProfMgr(); }) }),
        h('button', { type: 'button', disabled: !!S.busy, text: S.profMgr ? 'Đóng quản lý profile' : 'Quản lý profile',
          onclick: () => (S.profMgr ? (S.profMgr = null, render()) : run('Đang tải danh sách profile…', loadProfMgr)) }),
      ),
      ...renderProfMgr(),
    ];
  }

  // ------------------------------------------------------ quản lý profile ---
  async function loadProfMgr() {
    S.profMgr = await backend('GET', '/profiles/manage');
    await checkBackend();   // ô chọn profile khớp với thư mục vừa đọc lại
  }

  async function profileAction(what, item) {
    const label = `"${item.title}" (${item.file})`;
    const ask = {
      hide: null,
      delete: `Xoá hẳn profile ${label}? File bị xoá khỏi máy, không hoàn tác được.`
        + (item.kind === 'nạp từ guideline' ? '\nVăn bản và định nghĩa của guideline vẫn còn; gỡ cả guideline ở tab Guideline.' : '')
        + (item.kind === 'profile' ? '\nProfile có sẵn của tool sẽ quay lại khi bạn giải nén bản cập nhật mới.' : ''),
    }[what];
    if (ask && !await uiConfirm(ask)) return;
    await run('Đang cập nhật profile…', async () => {
      const path = {
        hide: ['POST', `/profiles/${encodeURIComponent(item.id)}/hide`],
        show: ['POST', `/profiles/hidden/${encodeURIComponent(item.file)}/show`],
        delete: item.hiddenItem ? ['DELETE', `/profiles/hidden/${encodeURIComponent(item.file)}`] : ['DELETE', `/profiles/${encodeURIComponent(item.id)}`],
      }[what];
      const res = await backend(path[0], path[1]);
      S.profMgr = { active: res.active, hidden: res.hidden };
      if (what !== 'show' && cfg.profileId === item.id) { cfg.profileId = 'auto'; saveCfg(); clearProposals(); }
      await checkBackend();
      await resolveLabels();
      const done = { hide: 'Đã ẩn', show: 'Đã hiện lại', delete: 'Đã xoá' }[what];
      setStatus(`${done} profile ${label}.${what === 'show' && item.note ? ' ' + item.note : ''}`, 'ok');
    });
  }

  function renderProfMgr() {
    if (!S.profMgr) return [];
    const busy = !!S.busy;
    const modeName = { instance: 'detection', semantic: 'semantic', keypoints: 'skeleton', tracking: 'video' };
    const row = (item, hiddenItem) => h('tr', { title: item.note || '' },
      h('td', {}, h('div', { text: item.title }),
        h('div', { class: 'muted small-text', text: `${item.file || 'có sẵn trong tool'} · ${modeName[item.mode] || item.mode} · ${item.kind}` }),
        item.note ? h('div', { class: 'small-text', style: 'color:var(--warn)', text: item.note }) : null),
      h('td', { style: 'white-space:nowrap;text-align:right' },
        item.locked ? h('span', { class: 'muted small-text', text: 'luôn có' }) : [
          hiddenItem
            ? h('button', { class: 'small', type: 'button', disabled: busy, text: 'Hiện lại', onclick: () => profileAction('show', item) })
            : h('button', { class: 'small', type: 'button', disabled: busy, text: 'Ẩn', title: 'Chuyển file vào profiles/archive/, hiện lại được',
              onclick: () => profileAction('hide', item) }),
          ' ',
          h('button', { class: 'small', type: 'button', disabled: busy, text: 'Xoá', title: 'Xoá hẳn file profile',
            onclick: () => profileAction('delete', Object.assign({}, item, { hiddenItem })) }),
        ]));
    const out = [h('h3', { text: `Profile đang dùng (${S.profMgr.active.length})` }),
      h('table', { class: 'profiles' }, h('tbody', {}, S.profMgr.active.map((x) => row(x, false))))];
    out.push(h('h3', { text: `Profile đang ẩn (${S.profMgr.hidden.length})` }));
    out.push(S.profMgr.hidden.length
      ? h('table', { class: 'profiles' }, h('tbody', {}, S.profMgr.hidden.map((x) => row(x, true))))
      : h('p', { class: 'muted small-text', style: 'margin:0', text: 'Chưa có profile nào bị ẩn.' }));
    out.push(h('p', { class: 'muted small-text', style: 'margin:0', text:
      'Ẩn = chuyển file vào thư mục profiles/archive/ của backend; profile biến khỏi ô chọn và khỏi "Tự chọn profile", hiện lại được. '
      + 'Xoá = xoá hẳn file. Hai profile dự phòng (generic, generic_track) luôn có.' }));
    return out;
  }

  function renderJob() {
    const out = [];
    const r = jobRange();
    const busy = !!S.busy;
    const num = (key, value) => h('input', { type: 'number', min: String(r.start), max: String(r.stop), value: String(value),
      style: 'width:80px', onchange: (ev) => { S.range[key] = Math.round(Number(ev.target.value)); render(); } });
    out.push(h('div', { class: 'row' }, h('span', { text: 'Từ frame' }), num('from', r.from), h('span', { text: 'tới' }), num('to', r.to),
      h('span', { class: 'muted small-text', text: `(job: ${r.start}–${r.stop})` })));
    if (S.longTask) {
      out.push(h('button', { type: 'button', text: 'Dừng', onclick: () => { S.stopRequested = true; setStatus('Đang dừng sau frame hiện tại…', 'busy'); } }));
    }
    const isVideo = S.job && S.job.mode === 'interpolation';
    const sections = [renderBatchSection(r, busy), renderTrackSection(r, busy)];
    if (isVideo) sections.reverse();
    for (const part of sections) out.push(...part);
    out.push(...renderSmartSection(r, busy));
    out.push(h('button', { type: 'button', disabled: busy, onclick: undoLast, text: 'Hoàn tác lần ghi gần nhất' }));
    return out;
  }

  function renderBatchSection(r, busy) {
    const out = [h('h2', { text: 'Gợi ý cho cả job' })];
    out.push(h('p', { class: 'muted small-text', style: 'margin:0', text:
      'Chạy gợi ý lần lượt cho mọi frame trong khoảng. Kết quả được giữ lại: mở frame nào thì tab Gợi ý hiện ngay đề xuất của frame đó.' }));
    // Chọn label cần gợi ý: ví dụ chỉ các label mới thêm sau khi đã gán xong các label cũ.
    const cands = batchCandidates();
    const picked = new Set(batchPicked());
    if (cands.length > 1) {
      const toggle = (id, on) => {
        const now = new Set(batchPicked());
        if (on) now.add(id); else now.delete(id);
        S.batchPick = [...now];
        render();
      };
      out.push(h('div', { class: 'row small-text', style: 'flex-wrap:wrap;gap:4px 10px' },
        h('span', { class: 'muted', text: `Label cần gợi ý (${picked.size}/${cands.length}):` }),
        cands.map((c) => h('label', { class: 'check', style: 'margin:0' },
          h('input', { type: 'checkbox', checked: picked.has(c.label_id), disabled: busy, onchange: (ev) => toggle(c.label_id, ev.target.checked) }),
          h('span', { text: c.name })))));
      out.push(h('div', { class: 'row' },
        h('button', { class: 'small', type: 'button', disabled: busy, text: 'Tất cả', onclick: () => { S.batchPick = null; render(); } }),
        h('button', { class: 'small', type: 'button', disabled: busy, text: 'Bỏ chọn hết', onclick: () => { S.batchPick = []; render(); } }),
        h('button', { class: 'small', type: 'button', disabled: busy, text: 'Chỉ label chưa có trong job',
          title: 'Chọn các label chưa có nhãn nào trong job (ví dụ label vừa thêm theo guideline mới)', onclick: pickNewBatchLabels })));
    }
    const skip = h('select', { disabled: busy, onchange: (ev) => { cfg.batchSkip = ev.target.value; saveCfg(); } },
      [['label', 'Bỏ qua label đã có trên frame (chỉ gợi ý label frame còn thiếu)'],
        ['frame', 'Bỏ qua frame đã có bất kỳ nhãn nào'],
        ['none', 'Không bỏ qua (đề xuất trùng nhãn cũ bị bỏ tick)']].map(([v, t]) => h('option', { value: v, text: t })));
    skip.value = batchSkipMode();
    out.push(h('label', { class: 'field' }, h('span', { text: 'Frame đã có nhãn' }), skip));
    const paused = GM_getValue(`batchrun:${S.jobId}`, null);
    if (paused && !busy) {
      const left = paused.frames.length - paused.done.length;
      out.push(h('div', { class: 'note row' }, h('span', { class: 'grow small-text', text: `Lượt gợi ý cả job đang tạm dừng: còn ${left}/${paused.total} frame.` }),
        h('button', { class: 'small primary', type: 'button', disabled: !S.resolved, text: 'Tiếp tục', onclick: () => runBatch(true) }),
        h('button', { class: 'small', type: 'button', text: 'Bỏ', onclick: () => { GM_setValue(`batchrun:${S.jobId}`, null); render(); } })));
    }
    out.push(h('button', { class: 'primary', type: 'button', disabled: busy || !S.resolved,
      onclick: () => runBatch(false), text: `Gợi ý cho ${jobFrames(r.from, r.to).length} frame${cands.length > 1 && picked.size < cands.length ? ` · ${picked.size} label` : ''}` }));
    const st = batchStats();
    if (!st.frames.length) return out;
    out.push(h('button', { type: 'button', disabled: busy, text: S.grid ? 'Đóng lưới ảnh' : `Duyệt dạng lưới ảnh (${st.frames.length} frame)`,
      onclick: () => { S.grid = S.grid ? null : { page: 0 }; render(); } }));
    if (S.grid) out.push(renderGrid(st));
    out.push(h('div', { class: 'row' }, h('span', { class: 'grow', text: `${st.frames.length} frame có kết quả · ${st.total} đề xuất · ${st.flagged} mục có cờ` })));
    if (st.flaggedFrames.length) {
      out.push(h('div', { class: 'row small-text' }, h('span', { class: 'muted', text: 'Frame cần duyệt:' }),
        st.flaggedFrames.slice(0, 40).map((f) => h('button', { class: 'small', type: 'button', text: String(f), onclick: () => goToFrame(f) })),
        st.flaggedFrames.length > 40 ? h('span', { class: 'muted', text: `+${st.flaggedFrames.length - 40}` }) : null));
    }
    out.push(h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: cfg.batchIncludeFlagged,
      onchange: (ev) => { cfg.batchIncludeFlagged = ev.target.checked; saveCfg(); render(); } }), h('span', { text: 'Ghi cả mục có cờ' })));
    out.push(h('div', { class: 'row' },
      h('button', { class: 'primary grow', type: 'button', disabled: busy || !st.ready, onclick: applyBatch, text: `Ghi ${st.ready} nhãn vào CVAT` }),
      h('button', { type: 'button', disabled: busy, onclick: clearBatch, text: 'Bỏ kết quả' })));
    return out;
  }

  function renderTrackSection(r, busy) {
    const out = [h('h2', { text: 'Theo dõi đối tượng (video)' })];
    const labels = trackLabels();
    const title = S.resolved && S.resolved.track ? S.resolved.track.title : '';
    out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: labels.length
      ? `Ghi thành track: mỗi đối tượng một ID qua các frame, CVAT nội suy giữa các keyframe. Label: ${labels.map((c) => c.name).join(', ')}. Profile: ${title}.`
      : 'Job không có label rectangle nào để theo dõi.' }));
    const engine = h('select', { disabled: busy, title: 'Cách theo dõi', onchange: (ev) => { cfg.trackEngine = ev.target.value; saveCfg(); } },
      [['classic', 'Cách thường: SAM3 từng frame, nối ID theo vị trí và màu'], ['sam3_video', 'SAM3 Video (thử nghiệm): model tự giữ ID, tốn VRAM hơn']]
        .map(([v, t]) => h('option', { value: v, text: t })));
    engine.value = cfg.trackEngine || 'classic';
    out.push(h('label', { class: 'field' }, h('span', { text: 'Cách theo dõi' }), engine));
    out.push(h('button', { class: 'primary', type: 'button', disabled: busy || !labels.length,
      onclick: runTracking, text: `Theo dõi ${jobFrames(r.from, r.to).length} frame` }));
    if (!S.tracks.length) return out;
    const m = S.trackMeta || {};
    for (const w of m.warnings || []) out.push(h('div', { class: 'note', text: w }));
    if (m.existing) out.push(h('div', { class: 'note', text: `Job đã có ${m.existing} track. Track mới có thể trùng với track cũ.` }));
    const checked = S.tracks.filter((t) => t.checked).length;
    out.push(h('div', { class: 'row' },
      h('span', { class: 'grow muted', text: `${S.tracks.length} track (frame ${m.from}–${m.to}) · ${checked} đã chọn` }),
      h('button', { class: 'small', type: 'button', text: 'Bỏ track có cờ', onclick: () => { S.tracks.forEach((t) => { if (t.flags.length) t.checked = false; }); persist('tracks'); render(); drawOverlay(); } })));
    out.push(h('ul', {}, S.tracks.map((t) => {
      const visibleNow = trackBoxAt(t, S.frame);
      const li = h('li', { class: 'item' },
        h('div', { class: 'row' },
          h('input', { type: 'checkbox', checked: t.checked, title: 'Ghi track này', onchange: (ev) => { t.checked = ev.target.checked; persist('tracks'); render(); drawOverlay(); } }),
          h('span', { class: 'swatch', style: `background:${trackColor(t.tid)}` }),
          h('span', { class: 'grow', text: `#${t.tid} ${t.label_name} · frame ${t.start}–${t.end}` }),
          h('span', { class: 'muted small-text', text: `${t.keyframes} keyframe${t.gaps ? ` · khuất ${t.gaps} lần` : ''}` }),
          h('button', { class: 'small', type: 'button', text: 'Tới frame đầu', onclick: () => goToFrame(t.start) })),
        t.flags.length ? h('div', { class: 'row' }, flagBadges(t.flags)) : null,
        t.flags.length ? h('div', { class: 'muted small-text', text: t.flags.map((f) => f.message).join(' ') }) : null,
        visibleNow ? null : h('div', { class: 'muted small-text', text: 'Không có trên frame đang xem.' }));
      hover(li, 't' + t.tid);
      return li;
    })));
    out.push(h('div', { class: 'row' },
      h('button', { class: 'primary grow', type: 'button', disabled: busy || !checked, onclick: applyTracks, text: `Ghi ${checked} track vào CVAT` }),
      h('button', { type: 'button', disabled: busy, onclick: clearTracks, text: 'Bỏ kết quả' })));
    return out;
  }

  function frameButtons(examples) {
    const seen = new Set();
    return (examples || []).filter((e) => !seen.has(e.frame) && seen.add(e.frame)).slice(0, 4).map((e) =>
      h('button', { class: 'small', type: 'button', text: `frame ${e.frame}`, title: 'Tới frame này và khoanh vùng ví dụ', onclick: () => showEvidence(e) }));
  }

  // Tab Guideline: nạp guideline có sẵn (cho task khác), rồi soạn guideline nháp từ ảnh của job.
  function renderGuide() {
    return [...renderImport(), h('hr', { style: 'border:none;border-top:1px solid var(--line, #3a3f47);margin:10px 0' }), ...renderGuideDraft()];
  }

  function renderGuideDraft() {
    const busy = !!S.busy;
    const r = jobRange();
    const llm = (S.health && S.health.guideline_llm) || { provider: 'none' };
    const out = [h('h2', { text: 'Soạn guideline nháp từ ảnh của job' })];
    out.push(h('p', { class: 'muted small-text', style: 'margin:0', text:
      'Tool quét một số ảnh mẫu, đo xem trong ảnh có gì và chỗ nào dễ nhầm, rồi soạn bản nháp theo khung của các guideline mẫu. Luật là quyết định của người: tool hỏi, bạn chốt.' }));
    out.push(h('label', { class: 'field' }, h('span', { text: 'Mục đích của bộ dữ liệu (model sẽ dùng nhãn này để làm gì)' }),
      h('textarea', { rows: 2, value: cfg.guidePurpose, placeholder: 'Ví dụ: phát hiện người đi bộ cho camera hành trình',
        onchange: (ev) => { cfg.guidePurpose = ev.target.value.trim(); saveCfg(); } })));
    out.push(h('div', { class: 'row' },
      h('label', { class: 'field' }, h('span', { text: 'Số ảnh mẫu' }),
        h('input', { type: 'number', min: '4', max: '300', value: String(cfg.guideSample), style: 'width:80px',
          onchange: (ev) => { cfg.guideSample = Math.max(4, Math.round(Number(ev.target.value) || 40)); saveCfg(); render(); } })),
      h('label', { class: 'field grow' }, h('span', { text: 'Từ khoá thêm (tiếng Anh, cách nhau bằng dấu phẩy)' }),
        h('input', { type: 'text', value: cfg.guideTerms, placeholder: 'tuk tuk, street vendor',
          onchange: (ev) => { cfg.guideTerms = ev.target.value; saveCfg(); } }))));
    if (llm.provider === 'none') {
      out.push(h('p', { class: 'muted small-text', style: 'margin:0', text:
        'Chưa bật AI soạn guideline: bản nháp vẫn tạo được từ số liệu, guideline mẫu và bài học đã có, nhưng class mới bạn tự viết định nghĩa.' }));
      out.push(h('p', { class: 'muted small-text', style: 'margin:0', text:
        'Muốn AI viết giúp: mở app SAM3 Label Assist trên máy chạy backend → Cài đặt → AI soạn guideline, rồi bật lại backend.' }));
    } else {
      out.push(h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: cfg.guideSendImages,
        onchange: (ev) => { cfg.guideSendImages = ev.target.checked; saveCfg(); } }),
        h('span', { text: `Cho phép gửi ảnh mẫu tới LLM (${llm.provider}${llm.local ? ', chạy trên máy' : ', ảnh sẽ rời máy bạn'})` })));
    }
    const count = Math.min(Number(cfg.guideSample) || 40, Math.max(0, r.to - r.from + 1));
    out.push(h('div', { class: 'row' },
      h('button', { class: 'primary grow', type: 'button', disabled: busy || !S.labels.length, onclick: runGuideline, text: `Quét ${count} ảnh và soạn nháp` }),
      S.longTask === 'guide' ? h('button', { type: 'button', text: 'Dừng', onclick: () => { S.stopRequested = true; setStatus('Đang dừng quét, sẽ soạn nháp từ các ảnh đã quét…', 'busy'); } }) : null));

    const g = S.guide;
    if (!g) {
      if (S.guideRunId) out.push(h('button', { type: 'button', disabled: busy, onclick: loadGuide, text: 'Nạp bản nháp gần nhất của job này' }));
      return out;
    }
    const s = g.summary;
    out.push(h('div', { class: 'row' },
      h('span', { class: 'grow', text: `${g.approved ? 'Đã duyệt' : 'Bản nháp'} · ${s.images} ảnh · ${g.concepts.length} loại đối tượng · ${s.occluded_pct}% bị che · ${s.tiny_pct}% rất nhỏ` }),
      h('button', { class: 'small', type: 'button', disabled: busy, text: 'Nạp lại', onclick: () => { S.guide = null; loadGuide(); } })));
    for (const wmsg of g.warnings || []) out.push(h('div', { class: 'note', text: wmsg }));
    out.push(h('div', { class: 'row' },
      h('button', { class: 'primary grow', type: 'button', onclick: openGuidePage, text: 'Mở bản nháp để sửa và duyệt' }),
      h('button', { type: 'button', onclick: downloadGuide, text: 'Tải .md' }),
      h('button', { type: 'button', disabled: busy, onclick: useGuideProfile, title: 'Tạo profile từ bản nháp để tool gợi ý nhãn theo đúng các luật này', text: 'Dùng làm profile' })));

    const plan = g.label_plan;
    if (plan) {
      const describe = (l) => `${l.name} (${l.type}${l.points ? ` ${l.points} điểm` : ''}${l.attributes.length ? `, ${l.attributes.join(', ')}` : ''})`;
      out.push(h('h2', { text: `Label CVAT theo guideline (${plan.total})` }));
      if (plan.missing.length) {
        out.push(h('p', { class: 'small-text', style: 'margin:0', text: `Task còn thiếu: ${plan.missing.map(describe).join('; ')}.` }));
      } else {
        out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: 'Task đã có đủ label theo taxonomy của bản nháp.' }));
      }
      for (const note of plan.notes) out.push(h('div', { class: 'note', text: note }));
      out.push(h('div', { class: 'row' },
        plan.missing.length ? h('button', { class: 'primary grow', type: 'button', disabled: busy, onclick: addGuideLabels,
          text: `Thêm ${plan.missing.length} label còn thiếu vào task` }) : null,
        h('button', { type: 'button', disabled: busy, onclick: copyGuideLabels, title: 'Chép cả bộ label dạng JSON để dán vào tab Raw khi tạo task mới', text: 'Chép JSON label' })));
      out.push(h('div', { class: 'row' },
        h('span', { class: 'grow muted small-text', text: 'Khi task đã đủ label và bạn đã bấm "Dùng làm profile": tool gán nhãn được cho mọi frame (ảnh, skeleton) hoặc theo dõi (video).' }),
        h('button', { class: 'small', type: 'button', text: 'Sang tab Cả job', onclick: () => { S.tab = 'job'; render(); drawOverlay(); } })));
    }

    const open = g.questions.filter((q) => !q.answer).length;
    out.push(h('h2', { text: `Câu hỏi cần chốt (còn ${open}/${g.questions.length})` }));
    out.push(h('ul', {}, g.questions.map((q) => {
      const recommended = q.options.find((o) => o.id === q.recommended);
      const select = h('select', { disabled: busy, style: 'width:100%', title: 'Chốt lựa chọn', onchange: (ev) => answerGuide(q.key, ev.target.value) },
        h('option', { value: '', text: `Chưa chốt (tạm dùng: ${recommended ? recommended.label : q.recommended})` }),
        q.options.map((o) => h('option', { value: o.id, text: o.label })));
      select.value = q.answer || '';
      return h('li', { class: 'item' },
        h('div', { text: q.text }),
        q.evidence ? h('div', { class: 'small-text', text: q.evidence }) : null,
        select,
        h('div', { class: 'muted small-text', text: q.reason }),
        q.examples.length ? h('div', { class: 'row' }, frameButtons(q.examples)) : null);
    })));

    if (g.findings.length) {
      out.push(h('h2', { text: `Lỗ hổng tool tự phát hiện (${g.findings.length})` }));
      out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: 'Đây là giả thuyết của tool, không phải kết luận. Xem rồi sửa bản nháp nếu đúng.' }));
      out.push(h('ul', {}, g.findings.map((f) => h('li', { class: 'item' },
        h('div', { text: f.text }), f.examples && f.examples.length ? h('div', { class: 'row' }, frameButtons(f.examples)) : null))));
    }
    if (g.cases.length) {
      out.push(h('h2', { text: `Ca khó trong ảnh mẫu (${g.cases.length})` }));
      out.push(h('ul', {}, g.cases.slice(0, 16).map((c) => h('li', { class: 'item' }, h('div', { class: 'row' },
        h('span', { class: 'badge', text: FLAG_NAMES[c.code] || c.code }),
        h('span', { class: 'grow', text: c.text }),
        frameButtons([c]))))));
    }
    if (g.lessons_used.length) {
      out.push(h('h2', { text: `Bài học từ các lần trước đã dùng (${g.lessons_used.length})` }));
      out.push(h('ul', {}, g.lessons_used.slice(0, 12).map((l) => h('li', { class: 'small-text', text: `${l.text} (${l.source})` }))));
      out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: 'Tắt bài học sai ở trang duyệt bản nháp.' }));
    }
    out.push(h('h2', { text: 'Ghi nhận sai sót sau khi dùng guideline' }));
    const box = h('textarea', { rows: 2, placeholder: 'Ví dụ: reviewer trả bài vì xe ba bánh chở hàng phải gán truck' });
    out.push(box);
    out.push(h('button', { type: 'button', disabled: busy, text: 'Ghi nhận để lần sau không lặp lại',
      onclick: () => { if (box.value.trim().length < 8) return setStatus('Hãy mô tả sai sót cụ thể hơn.', 'error'); sendGuideFeedback(box.value.trim()); } }));
    return out;
  }

  function renderQa() {
    const out = [h('button', { class: 'primary', type: 'button', disabled: !S.resolved || !!S.busy, onclick: runQa, text: 'Kiểm tra annotation của frame này' })];
    if (!S.qa || S.qaFrame !== S.frame) {
      out.push(h('p', { class: 'muted', style: 'margin:0', text: 'Đối chiếu annotation đã lưu của frame với checklist trong guideline. Hãy lưu (Ctrl+S) trước khi kiểm tra.' }));
      return out.concat(renderJobQa());
    }
    const names = { pass: 'Đạt', warn: 'Cảnh báo', fail: 'Lỗi', manual: 'Tự rà' };
    out.push(h('h2', { text: 'Checklist' }));
    out.push(h('ul', {}, S.qa.checklist.map((c) => h('li', { class: 'row' },
      h('span', { class: 'badge ' + (c.status === 'fail' ? 'error' : c.status === 'pass' ? 'pass' : ''), text: names[c.status] || c.status }),
      h('span', { class: 'grow', text: c.item })))));
    if (S.qa.tracks) out.push(h('div', { class: 'note', text: `Job có ${S.qa.tracks} track; tool chỉ kiểm tra shape, chưa kiểm tra track.` }));
    out.push(h('h2', { text: `Phát hiện (${S.qa.findings.length})` }));
    if (!S.qa.findings.length) out.push(h('p', { class: 'muted', style: 'margin:0', text: 'Không thấy lỗi đo được bằng máy. Các mục "Tự rà" vẫn cần bạn kiểm tra.' }));
    out.push(h('ul', {}, S.qa.findings.map((f, i) => {
      const li = h('li', { class: 'item' }, h('div', { class: 'row' },
        h('span', { class: 'badge ' + (f.severity === 'error' ? 'error' : ''), text: f.severity === 'error' ? 'Lỗi' : 'Cảnh báo' }),
        h('span', { class: 'grow', text: f.message }),
        h('button', { class: 'small', type: 'button', text: 'Tạo Issue', onclick: () => createIssue(f.code, f.message, f.bbox) })));
      hover(li, 'q' + i);
      return li;
    })));
    return out.concat(renderJobQa());
  }

  function renderSettings() {
    const text = (label, key, type, hint) => h('label', { class: 'field' }, h('span', { text: label }),
      h('input', { type: type || 'text', value: String(cfg[key] ?? ''), onchange: (ev) => { cfg[key] = ev.target.value.trim(); saveCfg(); } }),
      hint ? h('span', { class: 'muted small-text', text: hint }) : null);
    // Công tắc: chữ bên trái, công tắc bên phải; dòng phụ giải thích ngắn
    const sw = (label, key, sub, onchange) => h('label', { class: 'check sw' },
      h('input', { type: 'checkbox', role: 'switch', checked: !!cfg[key], onchange: (ev) => { cfg[key] = ev.target.checked; saveCfg(); if (onchange) onchange(ev.target.checked); } }),
      h('span', {}, label, sub ? h('span', { class: 'sub', text: sub }) : null));
    const group = (title, ...kids) => h('div', { class: 'group' }, h('h4', { text: title }), ...kids);
    const hl = S.health;
    const advanced = [
      text('API key (nếu backend có đặt)', 'apiKey', 'password'),
      text('CSS selector của ảnh nền canvas CVAT', 'canvasSelector', 'text', 'Chỉ đổi khi phần xem trước không hiện. Mặc định: #cvat_canvas_background'),
      sw('Đánh dấu nhãn do tool ghi là source = "semi-auto"', 'markSemiAuto'),
      h('div', { class: 'row' },
        h('span', { class: 'grow', text: 'Đổi nhãn semi-auto đã lưu thành manual' }),
        h('button', { type: 'button', disabled: !!S.busy, text: 'Frame này', onclick: () => convertSource('frame') }),
        h('button', { type: 'button', disabled: !!S.busy, text: 'Cả job', onclick: () => convertSource('job') })),
      h('h4', { text: 'Phím tắt (giữ Alt + phím)' }),
      renderKeyEditor(),
      h('div', { class: 'row' }, h('button', { type: 'button', text: 'Xem lại hướng dẫn', onclick: () => { cfg.tourDone = false; saveCfg(); S.tour = 0; S.tab = 'propose'; render(); } }),
        h('button', { type: 'button', text: 'Xuất báo cáo năng suất (CSV)', onclick: exportReport })),
    ];
    return [
      group('Kết nối',
        h('div', { class: 'row', style: 'flex-wrap:nowrap' },
          h('input', { class: 'grow', type: 'text', 'aria-label': 'Địa chỉ backend SAM3', value: String(cfg.backendUrl ?? ''),
            onchange: (ev) => { cfg.backendUrl = ev.target.value.trim(); saveCfg(); } }),
          h('span', { class: 'hchip' + (!hl ? '' : hl.ok ? (hl.is_mock ? ' mock' : ' ok') : ' bad'),
            text: !hl ? 'chưa kiểm tra' : hl.ok ? `${hl.engine} · ${hl.device}` : 'chưa kết nối' })),
        h('div', { class: 'row' },
          h('span', { class: 'grow muted small-text', text: 'Dùng app trên máy này thì không cần sửa. Colab / máy khác: dán địa chỉ https của tunnel.' }),
          h('button', { type: 'button', disabled: !!S.busy, text: 'Kiểm tra kết nối', onclick: () => run('Đang kiểm tra backend…', async () => {
            if (!(await checkBackend())) return setStatus(S.health.error, 'error');
            if (!S.labels.length) await readJob();
            await resolveLabels();
            if (!S.labels.length) setStatus(`Backend kết nối tốt (${S.health.engine} trên ${S.health.device}), nhưng ${NO_LABELS}`, 'error');
            else setStatus(`Kết nối tốt: ${S.health.engine} trên ${S.health.device}, phiên bản ${S.health.version}. Đã đọc ${S.labels.length} label.`, 'ok');
          }) }))),
      group('Khi duyệt',
        sw('Chạy sẵn gợi ý cho frame tiếp theo', 'prefetch', 'Chuyển frame là có đề xuất ngay'),
        sw('Tự gợi ý khi chuyển frame', 'autoPropose'),
        sw('Nhắc khi frame có ít vật hơn frame bên cạnh', 'neighborHints', 'Có thể bạn đã sót vật')),
      group('Khi ghi vào CVAT',
        sw('Kiểm luật QA trước khi ghi', 'preApplyQa', 'Trên các đề xuất đã tick'),
        sw('Hỏi lại trước khi ghi', 'confirmApply'),
        sw('Tải lại trang sau khi ghi', 'reloadAfterApply', 'Để thấy ngay nhãn mới trong CVAT'),
        sw('Chế độ reviewer', 'reviewerMode', 'Viền tím "AI" cho nhãn tool ghi mà chưa ai sửa',
          (on) => { if (on) loadReviewer(); else { S.review = null; drawOverlay(); } })),
      group('Hiển thị',
        h('label', { class: 'row' }, h('span', { class: 'grow', text: 'Độ đậm vùng tô' }),
          h('input', { type: 'range', min: '0', max: '0.8', step: '0.05', value: String(cfg.opacity), style: 'width:150px',
            oninput: (ev) => { cfg.opacity = Number(ev.target.value); saveCfg(); drawOverlay(); } })),
        h('label', { class: 'row' }, h('span', { class: 'grow', text: `Cỡ chữ ${Math.round((Number(cfg.fontScale) || 1) * 100)}%` }),
          h('input', { type: 'range', min: '0.8', max: '1.6', step: '0.1', value: String(cfg.fontScale || 1), style: 'width:150px',
            onchange: (ev) => { cfg.fontScale = Number(ev.target.value); saveCfg(); render(); } })),
        h('label', { class: 'row' }, h('span', { class: 'grow', text: 'Ngôn ngữ / Language' }),
          (() => { const sel = h('select', { onchange: (ev) => { cfg.lang = ev.target.value; saveCfg(); render(); } },
            h('option', { value: 'vi', text: 'Tiếng Việt' }), h('option', { value: 'en', text: 'English' })); sel.value = cfg.lang || 'vi'; return sel; })()),
        sw('Tương phản cao', 'highContrast', '', () => render())),
      h('h2', { text: 'Nâng cao' }),
      ...advanced,
      h('p', { class: 'muted small-text', style: 'margin:0', text: `SAM3 Label Assist ${VERSION}. Giữ Alt rồi bấm lên ảnh để tick / bỏ tick đề xuất.` }),
    ];
  }

  function renderKeyEditor() {
    const rows = Object.entries(KEY_ACTIONS).map(([action, label]) => {
      const input = h('input', { type: 'text', value: keyOf(action), maxlength: 10, style: 'width:80px', title: 'Bấm phím muốn dùng (cùng Alt)',
        onkeydown: (ev) => {
          ev.preventDefault();
          const k = ev.key.toLowerCase();
          if (['alt', 'shift', 'control', 'meta', 'tab'].includes(k)) return;
          const clash = Object.keys(DEFAULT_KEYS).find((a) => a !== action && keyOf(a) === k);
          if (clash) { setStatus(`Phím Alt+${k} đang dùng cho "${KEY_ACTIONS[clash]}".`, 'error'); return; }
          if (['f4', 'tab', 'arrowleft', 'arrowright', 'd', 'f', 'e', 'v'].includes(k)) {
            setStatus(`Alt+${k} trùng phím của trình duyệt/Windows (menu, quay lại trang...). Chọn phím khác.`, 'error'); return;
          }
          cfg.keys = Object.assign({}, cfg.keys, { [action]: k }); saveCfg(); render();
        } });
      return h('tr', {}, h('td', { text: label }), h('td', {}, input), h('td', { class: 'muted small-text', text: keyName(keyOf(action)) }));
    });
    return h('div', {}, h('table', {}, rows),
      h('button', { class: 'small', type: 'button', text: 'Về phím mặc định', onclick: () => { cfg.keys = {}; saveCfg(); render(); } }));
  }

  function render() {
    host.style.display = S.jobId && !cfg.hidden ? 'block' : 'none';
    applyLook();
    if (!S.jobId) return;
    renderHeader(); renderStatus();
    const open = !cfg.collapsed;
    for (const el of [els.meta, els.metaNote, els.nav, els.body, els.actions]) el.style.display = open ? '' : 'none';
    if (!open) return;
    renderMeta(); renderNav();
    const active = root.activeElement;
    if (active && els.body.contains(active) && /^(INPUT|SELECT|TEXTAREA)$/.test(active.tagName) && active.type !== 'checkbox' && S.busy) return;
    let view = { propose: renderPropose, job: renderJob, guide: renderGuide, labels: renderLabels, qa: renderQa, learn: renderLearn, settings: renderSettings }[S.tab]();
    view = view.filter(Boolean);
    if (['job', 'qa', 'guide', 'learn'].includes(S.tab)) view = foldSections(S.tab, view);
    if (S.tab === 'settings') view = foldAdvanced(view);
    put(els.body, ...view);
    put(els.actions, ...renderActions());
    if (cfg.lang === 'en') translateTree(root);
  }


  // Tab dài: gom nội dung theo từng tiêu đề (h2) thành mục gập được. Mặc định chỉ mở mục đầu; panel nhớ mục bạn mở / đóng.
  // Mục đang có việc chạy (nút Dừng, thanh tiến độ) luôn mở.
  function foldSections(tab, nodes) {
    const out = [], secs = [];
    let cur = null;
    for (const n of nodes) {
      if (n && n.tagName === 'H2') { cur = { title: n.textContent, nodes: [] }; secs.push(cur); continue; }
      if (cur) cur.nodes.push(n); else out.push(n);
    }
    if (secs.length < 2) return nodes;
    cfg.openSecs = cfg.openSecs || {};
    secs.forEach((sec, i) => {
      const id = tab + ':' + sec.title.replace(/\d+/g, '#');     // số đếm đổi theo dữ liệu: không dùng làm khoá
      const busyHere = sec.nodes.some((n) => n && n.querySelector && n.querySelector('progress, [data-running]'));
      const open = busyHere || (id in cfg.openSecs ? cfg.openSecs[id] : i === 0);
      const box = h('div', { class: 'sec' + (open ? ' open' : '') });
      const head = h('button', { class: 'sec-h', type: 'button', 'aria-expanded': String(open),
        onclick: () => { cfg.openSecs[id] = !box.classList.contains('open'); saveCfg(); box.classList.toggle('open'); head.setAttribute('aria-expanded', String(cfg.openSecs[id])); } },
      h('span', { text: sec.title }), ico('right', 's chev'));
      box.append(head, h('div', { class: 'sec-b' }, ...sec.nodes.filter(Boolean)));
      out.push(box);
    });
    return out;
  }

  // Cài đặt: phần "Nâng cao" (API key, phím tắt, nguồn nhãn…) gập lại, mặc định đóng
  function foldAdvanced(nodes) {
    const i = nodes.findIndex((n) => n.tagName === 'H2');
    if (i < 0) return nodes;
    const tail = nodes.slice(i + 1, -1);
    cfg.openSecs = cfg.openSecs || {};
    const open = !!cfg.openSecs['settings:advanced'];
    const box = h('div', { class: 'sec' + (open ? ' open' : '') });
    const head = h('button', { class: 'sec-h', type: 'button', 'aria-expanded': String(open),
      onclick: () => { cfg.openSecs['settings:advanced'] = !box.classList.contains('open'); saveCfg(); box.classList.toggle('open'); head.setAttribute('aria-expanded', String(cfg.openSecs['settings:advanced'])); } },
    ico('gear', 's'), h('span', { text: 'Nâng cao' }), h('small', { text: 'API key, phím tắt, nguồn nhãn…' }), ico('right', 's chev'));
    box.append(head, h('div', { class: 'sec-b' }, ...tail));
    return [...nodes.slice(0, i), box, nodes[nodes.length - 1]];
  }

  // ---- duyệt cả job dạng lưới ảnh nhỏ ----
  const thumbs = new Map();          // `${job}:${frame}` -> object URL ảnh nén
  async function thumbUrl(frame) {
    const key = `${S.jobId}:${frame}`;
    if (thumbs.has(key)) return thumbs.get(key);
    const p = cvat('GET', `/api/jobs/${S.jobId}/data?type=frame&quality=compressed&number=${frame}`, undefined, true)
      .then((blob) => URL.createObjectURL(blob)).catch(() => '');
    thumbs.set(key, p);
    if (thumbs.size > 200) { const [k, v] = thumbs.entries().next().value; thumbs.delete(k); Promise.resolve(v).then((u) => u && URL.revokeObjectURL(u)); }
    return p;
  }
  const GRID_PAGE = 12;
  function renderGrid(st) {
    const frames = st.frames;
    const pages = Math.max(1, Math.ceil(frames.length / GRID_PAGE));
    S.grid.page = Math.min(S.grid.page, pages - 1);
    const slice = frames.slice(S.grid.page * GRID_PAGE, (S.grid.page + 1) * GRID_PAGE);
    const tiles = slice.map((f) => {
      const entry = S.batch[f];
      const props = entry.proposals || [];
      const on = props.filter((p) => p.checked).length;
      const flagged = props.filter((p) => p.flags.length).length;
      const img = h('img', { alt: `frame ${f}`, style: 'width:100%;display:block;aspect-ratio:16/9;object-fit:contain;background:#222' });
      thumbUrl(f).then((u) => { if (u) img.src = u; });
      const svg = svgEl('svg', { viewBox: `0 0 ${entry.width} ${entry.height}`, preserveAspectRatio: 'xMidYMid meet' });
      svg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none';
      for (const p of props) {
        if (!p.bbox) continue;
        const [x1, y1, x2, y2] = p.bbox;
        svg.append(svgEl('rect', { x: x1, y: y1, width: x2 - x1, height: y2 - y1, fill: 'none', stroke: labelColor(p.label_id),
          'stroke-width': Math.max(2, entry.width / 300), opacity: p.checked ? 1 : 0.3, 'stroke-dasharray': p.flags.length ? '8 5' : 'none' }));
      }
      const box = h('input', { type: 'checkbox', checked: on > 0, title: 'Giữ / bỏ mọi đề xuất của frame này',
        onchange: (ev) => { props.forEach((p) => { p.checked = ev.target.checked && (cfg.batchIncludeFlagged || !p.flags.length || p.checked); p.userTouched = true; }); persist('batch'); render(); } });
      return h('div', { class: 'tile' + (f === S.frame ? ' current' : '') },
        h('div', { style: 'position:relative;cursor:pointer', title: 'Mở frame này', onclick: () => goToFrame(f) }, img, svg),
        h('div', { class: 'row small-text' }, box, h('span', { class: 'grow', text: `#${f} · ${on}/${props.length}` }),
          flagged ? h('span', { class: 'badge', text: `${flagged} cờ` }) : null));
    });
    return h('div', { class: 'grid-wrap' },
      h('div', { class: 'grid' }, tiles),
      h('div', { class: 'row small-text' },
        h('button', { class: 'small', type: 'button', disabled: S.grid.page === 0, text: '‹ Trước', onclick: () => { S.grid.page--; render(); } }),
        h('span', { class: 'grow muted', style: 'text-align:center', text: `Trang ${S.grid.page + 1}/${pages}` }),
        h('button', { class: 'small', type: 'button', disabled: S.grid.page >= pages - 1, text: 'Sau ›', onclick: () => { S.grid.page++; render(); } })));
  }

  // ---- thông báo khi việc dài xong (Windows toast trong app desktop, thông báo của Tampermonkey ngoài trình duyệt) ----
  function notify(title, text) {
    try { if (typeof GM_notification === 'function') GM_notification({ title: translateText(title), text: translateText(text), timeout: 8000 }); } catch (e) { /* bỏ qua */ }
  }


  // ---- dữ liệu thông minh: frame nên làm trước, keyframe theo chuyển động, tìm frame bằng chữ ----
  S.smart = null;         // {job, feats:{frame: features}, ranking, keyframes}
  S.search = null;        // {job, query, matches, done, total}
  function sampleRange(frames, cap) {
    if (frames.length <= cap) return frames;
    const step = frames.length / cap;
    return Array.from({ length: cap }, (_, i) => frames[Math.floor(i * step)]);
  }
  async function compressedB64(frame) {
    const blob = await cvat('GET', `/api/jobs/${S.jobId}/data?type=frame&quality=compressed&number=${frame}`, undefined, true);
    return await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(',')[1]); r.onerror = () => reject(r.error); r.readAsDataURL(blob); });
  }
  async function analyzeJob() {
    const r = jobRange();
    const frames = sampleRange(jobFrames(r.from, r.to), 400);
    S.stopRequested = false; S.longTask = 'smart';
    await run(`Đang phân tích ${frames.length} frame…`, async () => {
      const feats = (S.smart && S.smart.job === S.jobId && S.smart.feats) || {};
      const todo = frames.filter((f) => !feats[f]);
      for (let i = 0; i < todo.length; i += 12) {
        if (S.stopRequested) break;
        const chunk = todo.slice(i, i + 12);
        const imgs = await Promise.all(chunk.map(async (f) => ({ frame: f, image_b64: await compressedB64(f) })));
        const res = await backend('POST', '/data/features', { frames: imgs });
        for (const x of res.frames) feats[x.frame] = x.features;
        setStatus(`Đang phân tích frame: ${Object.keys(feats).length}/${frames.length}…`, 'busy');
      }
      const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      const count = labelsByFrame(ann);
      const list = frames.filter((f) => feats[f]).map((f) => {
        const b = S.batch[f];
        const props = (b && b.proposals) || [];
        return { frame: f, features: feats[f], labeled: count[f] ? count[f].size : 0,
          flagged: props.filter((p) => p.flags.length).length, low: props.filter((p) => typeof p.score === 'number' && p.score < 0.5).length };
      });
      const res = await backend('POST', '/data/rank', { frames: list, k: Math.min(30, list.length) });
      S.smart = { job: S.jobId, feats, ranking: res.ranking, keyframes: res.keyframes, analyzed: list.length };
      setStatus(`Đã phân tích ${list.length} frame: ${res.keyframes.length} keyframe theo chuyển động. Xem danh sách "Nên làm trước".`, 'ok');
    });
    S.longTask = ''; render();
  }
  async function searchFrames(query) {
    const q = (query || '').trim();
    if (!q) return setStatus('Gõ thứ cần tìm (tiếng Anh), ví dụ: motorbike carrying boxes', 'error');
    const r = jobRange();
    const frames = jobFrames(r.from, r.to);
    S.search = { job: S.jobId, query: q, matches: [], done: 0, total: frames.length };
    S.stopRequested = false; S.longTask = 'search';
    await run(`SAM3 đang tìm "${q}" trong ${frames.length} frame…`, async () => {
      for (let i = 0; i < frames.length; i += 4) {
        if (S.stopRequested) break;
        const chunk = frames.slice(i, i + 4);
        const imgs = await Promise.all(chunk.map(async (f) => ({ frame: f, image_b64: await compressedB64(f) })));
        const res = await backend('POST', '/data/search', { query: q, frames: imgs });
        S.search.matches = S.search.matches.concat(res.matches).sort((a, b) => b.score - a.score);
        S.search.done = Math.min(frames.length, i + 4);
        setStatus(`Tìm "${q}": ${S.search.done}/${frames.length} frame, thấy ${S.search.matches.length}.`, 'busy');
        if (S.tab === 'job' && i % 16 === 0) render();
      }
      const stopped = S.stopRequested ? 'Đã dừng. ' : '';
      setStatus(`${stopped}"${q}": thấy ở ${S.search.matches.length} frame.`, 'ok');
      if (!S.stopRequested) notify('Tìm frame xong', `"${q}": ${S.search.matches.length} frame.`);
    });
    S.longTask = ''; render();
  }
  function renderSmartSection(r, busy) {
    const out = [h('h2', { text: 'Dữ liệu thông minh' })];
    out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: 'Phân tích ảnh nén của các frame (không dùng GPU): frame nào khác lạ / khó nên làm trước, và keyframe theo chuyển động cho video.' }));
    out.push(h('button', { type: 'button', disabled: busy, text: S.smart && S.smart.job === S.jobId ? 'Phân tích lại' : 'Phân tích frame của job', onclick: analyzeJob }));
    if (S.smart && S.smart.job === S.jobId) {
      out.push(h('div', { class: 'small-text', text: `Nên làm trước (${S.smart.ranking.length}):` }));
      out.push(h('ul', {}, S.smart.ranking.slice(0, 15).map((x) => h('li', { class: 'item' }, h('div', { class: 'row' },
        h('button', { class: 'small', type: 'button', text: `#${x.frame}`, onclick: () => goToFrame(x.frame) }),
        h('span', { class: 'grow small-text muted', text: x.reasons.join(', ') }))))));
      const keys = S.smart.keyframes || [];
      out.push(h('div', { class: 'row small-text' }, h('span', { class: 'grow', text: `Keyframe theo chuyển động: ${keys.length}/${S.smart.analyzed} frame` }),
        h('button', { class: 'small', type: 'button', disabled: busy || !keys.length || !S.resolved, text: 'Gợi ý cả job chỉ trên keyframe',
          title: 'Chỉ chạy SAM3 trên các frame có thay đổi đáng kể', onclick: () => runBatch(false, keys) })));
      out.push(h('div', { class: 'row small-text' }, keys.slice(0, 40).map((f) => h('button', { class: 'small', type: 'button', text: String(f), onclick: () => goToFrame(f) })),
        keys.length > 40 ? h('span', { class: 'muted', text: `+${keys.length - 40}` }) : null));
    }
    const input = h('input', { type: 'text', class: 'grow', placeholder: 'Tìm frame có… (tiếng Anh, vd: motorbike carrying boxes)', value: (S.search && S.search.query) || '',
      onkeydown: (ev) => { if (ev.key === 'Enter') searchFrames(ev.target.value); } });
    out.push(h('div', { class: 'row' }, input, h('button', { type: 'button', disabled: busy || !(S.health && S.health.ok), text: 'Tìm', onclick: () => searchFrames(input.value) })));
    if (S.search && S.search.job === S.jobId && S.search.matches.length) {
      out.push(h('div', { class: 'row small-text' }, S.search.matches.slice(0, 40).map((m) => h('button', { class: 'small', type: 'button',
        title: `${m.count} vật, điểm ${Math.round(m.score * 100)}%`, text: `${m.frame} (${Math.round(m.score * 100)}%)`, onclick: () => goToFrame(m.frame) }))));
    }
    return out;
  }

  // ---- độ đồng thuận giữa hai job (hai người gán cùng frame) ----
  S.agree = null;
  async function runAgreement(otherJob) {
    const other = Number(otherJob);
    if (!other || other === S.jobId) return setStatus('Nhập số job khác (cùng task, cùng frame) để so.', 'error');
    await run(`Đang so job ${S.jobId} với job ${other}…`, async () => {
      const [a, b] = await Promise.all([cvat('GET', `/api/jobs/${S.jobId}/annotations`), cvat('GET', `/api/jobs/${other}/annotations`)]);
      const fa = new Set((a.shapes || []).map((x) => x.frame)), fb = new Set((b.shapes || []).map((x) => x.frame));
      const common = new Set([...fa].filter((f) => fb.has(f)));
      if (!common.size) throw new Error(`Hai job không có frame chung nào có nhãn (job ${S.jobId}: ${fa.size} frame, job ${other}: ${fb.size} frame).`);
      const pick = (list) => (list || []).filter((x) => common.has(x.frame)).map((x) => ({ frame: x.frame, label_id: x.label_id, type: x.type, points: x.type === 'mask' ? (x.points || []).slice(-4) : x.points, elements: x.elements }));
      const res = await backend('POST', '/qa/agreement', { a: pick(a.shapes), b: pick(b.shapes), labels: S.labels.map((l) => ({ id: l.id, name: l.name })) });
      S.agree = Object.assign({ other }, res);
      setStatus(`Đồng thuận chung ${Math.round(res.overall * 100)}% trên ${res.frames} frame chung.`, res.overall >= 0.8 ? 'ok' : 'error');
    });
  }
  function renderAgreement(busy) {
    const input = h('input', { type: 'number', min: '1', style: 'width:100px', placeholder: 'số job', value: S.agree ? String(S.agree.other) : '' });
    const out = [h('h2', { text: 'Độ đồng thuận giữa hai người gán' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'So nhãn của job này với một job khác gán cùng các frame (ví dụ job kiểm tra chéo). Class đồng thuận thấp thường là chỗ guideline còn mơ hồ.' }),
      h('div', { class: 'row' }, h('span', { text: 'So với job' }), input, h('button', { type: 'button', disabled: busy, text: 'So sánh', onclick: () => runAgreement(input.value) }))];
    if (S.agree) {
      out.push(h('table', {}, h('tr', {}, ['Label', 'Đồng thuận', 'Khớp', 'Chỉ job này', `Chỉ job ${S.agree.other}`, 'IoU'].map((t) => h('th', { text: t }))),
        S.agree.labels.map((l) => h('tr', {}, h('td', { text: l.label }), h('td', { text: `${Math.round(l.agreement * 100)}%`, style: l.agreement < 0.7 ? 'color:var(--err)' : '' }),
          h('td', { text: String(l.matched) }), h('td', { text: String(l.only_a) }), h('td', { text: String(l.only_b) }), h('td', { text: l.mean_iou === null ? '—' : String(l.mean_iou) })))));
      if (S.agree.confusion.length) out.push(h('div', { class: 'note small-text', text: 'Hay gán khác nhau: ' + S.agree.confusion.slice(0, 6).map((c) => `${c.pair} (${c.count})`).join(', ') }));
      if (S.agree.worst_frames.length) out.push(h('div', { class: 'row small-text' }, h('span', { class: 'muted', text: 'Frame lệch nhiều:' }),
        S.agree.worst_frames.slice(0, 15).map((f) => h('button', { class: 'small', type: 'button', text: `${f.frame} (${f.disagree})`, onclick: () => goToFrame(f.frame) }))));
    }
    return out;
  }


  // ---- model riêng của dự án (học từ nhãn hộp đã duyệt) ----
  S.custom = null; let customPoll = null;
  async function loadCustom() {
    try { S.custom = await backend('POST', '/custom/info', baseBody()); } catch (e) { S.custom = { error: e.message }; }
    if (S.custom && S.custom.training && S.custom.training.running && !customPoll) {
      customPoll = setInterval(async () => {
        await loadCustom();
        if (!(S.custom.training && S.custom.training.running)) {
          clearInterval(customPoll); customPoll = null;
          const t = S.custom.training || {};
          notify('Huấn luyện model riêng xong', t.message || '');
        }
        if (S.tab === 'learn') render();
      }, 2500);
    }
    if (S.tab === 'learn') render();
  }
  async function collectCustom() {
    S.stopRequested = false; S.longTask = 'custom';
    await run('Đang thu thập ảnh + hộp đã duyệt của job…', async () => {
      const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      const byFrame = {};
      for (const x of ann.shapes || []) {
        if (x.type !== 'rectangle' || !labelById(x.label_id)) continue;
        const [x1, y1, x2, y2] = x.points;
        (byFrame[x.frame] = byFrame[x.frame] || []).push({ label_id: x.label_id, bbox: [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)] });
      }
      const frames = sampleRange(Object.keys(byFrame).map(Number).sort((a, b) => a - b), 200);
      if (!frames.length) throw new Error('Job này chưa có nhãn hộp (rectangle) nào để học.');
      let sent = 0, last = null;
      for (let i = 0; i < frames.length; i += 4) {
        if (S.stopRequested) break;
        const chunk = frames.slice(i, i + 4);
        const samples = await Promise.all(chunk.map(async (f) => ({ key: `job${S.jobId}_f${f}`, image_b64: (await fetchFrame(f)).b64, boxes: byFrame[f] })));
        last = await backend('POST', '/custom/dataset/add', Object.assign(baseBody(), { samples }));
        sent += chunk.length;
        setStatus(`Đã gửi ${sent}/${frames.length} frame cho model riêng…`, 'busy');
      }
      if (last) setStatus(`Dữ liệu model riêng: ${last.dataset.images} ảnh, ${last.dataset.boxes} hộp.`, 'ok');
    });
    S.longTask = ''; await loadCustom();
  }
  function renderCustomModel() {
    const out = [h('h2', { text: 'Model riêng của dự án' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'Một detector nhỏ học từ chính các nhãn hộp đã duyệt của bạn. Class nào đạt chuẩn (đủ ví dụ, đúng ≥ 70%, tìm được ≥ 60% trên ảnh giữ lại để kiểm) mới được dùng, nhanh hơn SAM3 nhiều lần; class khác vẫn do SAM3 làm.' })];
    if (!S.custom) { loadCustom(); return out; }
    if (S.custom.error) { out.push(h('div', { class: 'note small-text', text: S.custom.error })); return out; }
    const d = S.custom.dataset || { images: 0, boxes: 0, per_class: {} };
    const t = S.custom.training || {};
    const m = S.custom.model;
    out.push(h('div', { class: 'small-text', text: `Dữ liệu: ${d.images} ảnh, ${d.boxes} hộp` + (Object.keys(d.per_class).length ? ` (${Object.entries(d.per_class).slice(0, 6).map(([k, v]) => `${k} ${v}`).join(', ')})` : '') }));
    const busy = !!S.busy;
    out.push(h('div', { class: 'row' },
      h('button', { class: 'small', type: 'button', disabled: busy || t.running, text: 'Thu thập từ job này', onclick: collectCustom }),
      h('button', { class: 'small primary', type: 'button', disabled: busy || t.running || d.images < 8, text: 'Huấn luyện', title: d.images < 8 ? 'Cần ít nhất 8 ảnh' : 'Chạy nền, vẫn gán nhãn được trong lúc chờ',
        onclick: () => run('Đang bắt đầu huấn luyện…', async () => { await backend('POST', '/custom/train', Object.assign(baseBody(), { epochs: 12 })); setStatus('Đang huấn luyện model riêng (chạy nền).', 'ok'); await loadCustom(); }) }),
      t.running ? h('button', { class: 'small', type: 'button', text: 'Dừng', onclick: async () => { await backend('POST', '/custom/stop'); loadCustom(); } }) : null,
      h('button', { class: 'small', type: 'button', disabled: busy || t.running || (!d.images && !m), text: 'Xoá',
        onclick: async () => await uiConfirm('Xoá dữ liệu và model riêng của profile này?') && run('Đang xoá…', async () => { await backend('POST', '/custom/clear', baseBody()); await loadCustom(); }) })));
    if (t.running) out.push(h('div', { class: 'small-text', text: `${t.message || 'Đang huấn luyện'}${t.loss ? ` · loss ${t.loss}` : ''}` }));
    if (t.error) out.push(h('div', { class: 'note small-text', text: t.error }));
    if (m) {
      out.push(h('table', {}, h('tr', {}, ['Class', 'Đúng', 'Tìm được', 'Ví dụ', 'Dùng'].map((x) => h('th', { text: x }))),
        m.classes.map((c) => { const k = m.metrics[c] || {}; const ok = m.usable.includes(c);
          return h('tr', {}, h('td', { text: c }), h('td', { text: `${Math.round((k.precision || 0) * 100)}%` }), h('td', { text: `${Math.round((k.recall || 0) * 100)}%` }),
            h('td', { text: String((m.counts || {})[c] || 0) }), h('td', { text: ok ? '✓' : '—', style: ok ? 'color:var(--ok)' : '' })); })));
      out.push(h('div', { class: 'muted small-text', text: `Huấn luyện lúc ${m.trained_at} trên ${m.device}, ${m.train_images} ảnh học, ${m.val_images} ảnh kiểm.` }));
    }
    out.push(h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!cfg.useCustom, disabled: !(m && m.usable && m.usable.length),
      onchange: (ev) => { cfg.useCustom = ev.target.checked; saveCfg(); clearProposals(); } }),
      h('span', { text: 'Dùng model riêng cho các class đạt chuẩn khi gợi ý' })));
    return out;
  }

  // ====================================================== 0.8.0: thao tác nhanh ===
  // Phím tắt (Alt + phím), tự đặt được ở Cài đặt panel → Nâng cao.
  const KEY_ACTIONS = {
    propose: 'Gợi ý nhãn cho frame này', apply: 'Ghi các đề xuất đã tick', applyNext: 'Ghi rồi sang frame tiếp (đề xuất đã chạy sẵn)',
    next: 'Chọn đề xuất tiếp theo', prev: 'Chọn đề xuất trước', toggle: 'Tick / bỏ tick đề xuất đang chọn',
    reject: 'Bỏ (loại) đề xuất đang chọn', undo: 'Hoàn tác lần ghi gần nhất', panel: 'Ẩn / hiện panel',
  };
  const DEFAULT_KEYS = { propose: 'g', apply: 'a', applyNext: 'enter', next: 'j', prev: 'k', toggle: 'x', reject: 'delete', undo: 'z', panel: 'p' };
  const keyOf = (action) => String((cfg.keys && cfg.keys[action]) || DEFAULT_KEYS[action]).toLowerCase();
  const keyName = (k) => 'Alt+' + (k.length === 1 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1));

  // ---- bộ lọc: ngưỡng tức thì + ẩn class (không gọi lại backend) ----
  S.minScore = 0; S.hiddenLabels = new Set(); S.sel = null; S.hints = []; S.prefetching = null;
  function visible(p) {
    return !S.hiddenLabels.has(p.label_id) && (p.type === 'skeleton' || typeof p.score !== 'number' || p.score >= S.minScore);
  }
  function shownProposals() { return S.propFrame === S.frame ? S.proposals.filter(visible) : []; }

  // ---- hoàn tác nhiều bước: tối đa 20 lần ghi gần nhất, theo từng job ----
  function pushUndo(entry) {
    const stack = (GM_getValue('undoStack', null) || []).filter((u) => Date.now() - u.at < 7 * 864e5);
    stack.push(entry);
    GM_setValue('undoStack', stack.slice(-20));
  }
  function undoStackOf(jobId) { return (GM_getValue('undoStack', null) || []).filter((u) => u.jobId === jobId); }
  function popUndo(jobId) {
    const stack = GM_getValue('undoStack', null) || [];
    for (let i = stack.length - 1; i >= 0; i--) if (stack[i].jobId === jobId) { const [u] = stack.splice(i, 1); GM_setValue('undoStack', stack); return u; }
    return null;
  }
  function clearUndo(jobId) { GM_setValue('undoStack', (GM_getValue('undoStack', null) || []).filter((u) => u.jobId !== jobId)); }
  { const legacy = GM_getValue('undo', null); if (legacy && legacy.jobId) { pushUndo(legacy); GM_setValue('undo', null); } }

  // ---- gợi ý sẵn frame tiếp theo ----
  // Kết quả lưu theo job + frame + "chữ ký" cấu hình (profile, chỉnh tay, label). Chỉ dùng khi nhãn có sẵn của frame
  // không đổi so với lúc chạy sẵn. Giữ qua lần tải lại trang (sau khi ghi) nhờ bộ nhớ của script.
  const pfInflight = {};
  function pfSig() { const b = baseBody(); return JSON.stringify([b.profile_id, b.overrides, b.use_learned, b.use_custom, S.labels.map((l) => l.id)]); }
  function exSig(existing) { return existing.map((x) => `${x.id ?? 't'}:${x.label_id}:${(x.points || []).slice(0, 4).map(Math.round).join(',')}`).sort().join('|'); }
  function pfStore() { const all = GM_getValue('prefetch', null); return all && all.job === S.jobId ? all : { job: S.jobId, frames: {} }; }
  function pfPut(frame, entry) {
    const all = pfStore();
    all.frames[frame] = entry;
    for (const k of Object.keys(all.frames).sort((a, b) => all.frames[b].at - all.frames[a].at).slice(3)) delete all.frames[k];
    try { GM_setValue('prefetch', all); } catch (e) { /* quá lớn: chỉ giữ trong phiên */ }
  }
  function pfTake(frame, existing) {
    const all = pfStore();
    const e = all.frames[frame];
    if (!e || e.sig !== pfSig() || Date.now() - e.at > 30 * 60e3 || e.ex !== exSig(existing)) return null;
    return e.res;
  }
  function nextFrameOf(frame) {
    const { stop } = jobRange();
    const list = jobFrames(frame + 1, Math.min(stop, frame + 200));
    return list.length ? list[0] : null;
  }
  async function computeAt(frame, ann) {
    const fr = await fetchFrame(frame);
    const existing = existingAt(ann, frame);
    const res = await backend('POST', '/propose', Object.assign(baseBody(), { image_b64: fr.b64, existing }));
    return { res, existing, w: fr.w, h: fr.h };
  }
  function schedulePrefetch(fromFrame) {
    if (!cfg.prefetch || !S.resolved || !S.jobId) return;
    const target = nextFrameOf(fromFrame);
    if (target === null || pfInflight[target] || S.batch[target]) return;
    const job = S.jobId, sig = pfSig();
    S.prefetching = target;
    pfInflight[target] = (async () => {
      try {
        const ann = await cvat('GET', `/api/jobs/${job}/annotations`);
        if (job !== S.jobId) return null;
        const out = await computeAt(target, ann);
        if (job === S.jobId && sig === pfSig()) pfPut(target, { sig, at: Date.now(), ex: exSig(out.existing), res: out.res });
        return out;
      } catch (err) { return null; } finally {
        delete pfInflight[target];
        if (S.prefetching === target) S.prefetching = null;
        if (S.tab === 'propose') renderStatusLine();
      }
    })();
  }
  function renderStatusLine() { /* dòng nhỏ dưới nút gợi ý: được vẽ lại cùng render() */ if (!S.busy) render(); }

  // ---- nhắc vật có thể bị sót: so số vật theo label với frame trước/sau ----
  function neighborHints(ann, frame) {
    if (!cfg.neighborHints || !ann) return [];
    const count = (list) => list.reduce((m, s) => { m[s.label_id] = (m[s.label_id] || 0) + 1; return m; }, {});
    const here = count(existingAt(ann, frame).concat(S.proposals.filter((p) => p.checked && visible(p) && S.propFrame === frame)));
    const frames = jobFrames(Math.max(0, frame - 1), frame + 1).filter((f) => f !== frame);
    const out = [];
    for (const f of frames) {
      const there = count(existingAt(ann, f));
      for (const [lid, n] of Object.entries(there)) {
        const m = here[lid] || 0;
        if (n > m) {
          const l = labelById(Number(lid));
          out.push(`Frame ${f} có ${n} "${l ? l.name : lid}", frame này mới có ${m}: có thể còn sót ${n - m}.`);
        }
      }
    }
    return [...new Set(out)].slice(0, 4);
  }

  // ---- kiểm trước khi ghi ----
  async function preApplyCheck(chosen) {
    if (!cfg.preApplyQa) return true;
    let res;
    try {
      const fr = await getFrame();
      const current = (await frameShapes()).shapes;
      const shapes = current.concat(chosen.map((p) => Object.assign(toCvatShape(p), { id: -p.pid })));
      res = await backend('POST', '/qa', Object.assign(baseBody(), { shapes, width: fr.w, height: fr.h }));
    } catch (err) { return true; }      // không kiểm được thì không chặn việc ghi
    const ids = new Set(chosen.map((p) => -p.pid));
    const mine = (res.findings || []).filter((f) => f.severity === 'error' && (f.shape_ids || [f.shape_id]).some((id) => ids.has(id)));
    if (!mine.length) return true;
    const lines = mine.slice(0, 8).map((f) => '• ' + (f.message || f.code));
    return await uiConfirm(`Kiểm tra trước khi ghi: ${mine.length} lỗi trên các đề xuất đã tick.\n\n${lines.join('\n')}\n\nVẫn ghi?`);
  }

  // ---- chọn / tick đề xuất bằng bàn phím và Alt+bấm trên ảnh ----
  function moveSel(step) {
    const list = shownProposals();
    if (!list.length) return;
    const i = list.findIndex((p) => p.pid === S.sel);
    const next = list[(i + step + list.length) % list.length];
    S.sel = next.pid; S.highlight = 'p' + next.pid;
    render(); drawOverlay();
    const li = els.body.querySelector(`li[data-pid="${next.pid}"]`);
    if (li && li.scrollIntoView) li.scrollIntoView({ block: 'nearest' });
  }
  function selected() { return S.proposals.find((p) => p.pid === S.sel && visible(p)); }
  function toggleProposal(p, value) {
    p.checked = value === undefined ? !p.checked : value; p.userTouched = true;
    render(); drawOverlay();
  }
  function proposalAt(pt) {
    const hits = shownProposals().filter((p) => p.bbox && pt[0] >= p.bbox[0] && pt[0] <= p.bbox[2] && pt[1] >= p.bbox[1] && pt[1] <= p.bbox[3]);
    hits.sort((a, b) => (a.bbox[2] - a.bbox[0]) * (a.bbox[3] - a.bbox[1]) - (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]));
    return hits[0] || null;
  }
  function pointOnImage(ev) {
    const r = overlaySvg.getBoundingClientRect();
    if (!r.width || ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom) return null;
    return [((ev.clientX - r.left) / r.width) * S.imgW, ((ev.clientY - r.top) / r.height) * S.imgH];
  }
  // Alt + bấm trên ảnh: tick / bỏ tick đề xuất dưới con trỏ (hộp nhỏ nhất). Không giữ Alt thì CVAT nhận chuột như thường.
  document.addEventListener('mousedown', (ev) => {
    if (!ev.altKey || ev.button !== 0 || S.box || !overlayActive() || host.contains(ev.target)) return;
    const pt = pointOnImage(ev);
    const p = pt && proposalAt(pt);
    if (!p) return;
    ev.preventDefault(); ev.stopPropagation();
    S.sel = p.pid; toggleProposal(p);
  }, true);
  let hoverTimer = null;
  document.addEventListener('mousemove', (ev) => {
    if (!ev.altKey || S.box || !overlayActive()) return;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      const pt = pointOnImage(ev);
      const p = pt && proposalAt(pt);
      const key = p ? 'p' + p.pid : null;
      if (key !== S.highlight) { S.highlight = key; drawOverlay(); }
    }, 30);
  }, true);

  // ---- ghi rồi sang frame tiếp ----
  async function applyAndNext() {
    const next = nextFrameOf(S.frame);
    if (next === null) return setStatus('Đây là frame cuối của job.', 'info');
    const shown = shownProposals().filter((p) => p.checked);
    if (!shown.length) {                       // không có gì để ghi: chỉ sang frame tiếp
      GM_setValue('autoNext', { jobId: S.jobId, frame: next, at: Date.now() });
      goToFrame(next);
      return;
    }
    await applyProposals({ nextFrame: next });
  }
  function consumeAutoNext() {
    const a = GM_getValue('autoNext', null);
    if (!a || a.jobId !== S.jobId || a.frame !== S.frame || Date.now() - a.at > 120e3) return false;
    GM_setValue('autoNext', null);
    return true;
  }

  // ---- thống kê phiên (theo ngày) ----
  const MANUAL_SECONDS = { rectangle: 7, polygon: 25, polyline: 15, mask: 30, skeleton: 45, points: 5 };
  function statsAll() { return GM_getValue('stats', null) || {}; }
  // Ngày theo giờ của máy (trước 0.9.1 dùng giờ UTC: ở Việt Nam việc làm lúc 0h-7h sáng bị tính sang hôm trước)
  function localDay(d = new Date()) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  function bumpStats(patch) {
    const day = localDay();
    const all = statsAll();
    const d = all[day] || { applied: 0, rejected: 0, shown: 0, frames: [], saved: 0, jobs: {} };
    for (const [k, v] of Object.entries(patch)) {
      if (k === 'frame') { const key = `${S.jobId}:${v}`; if (!d.frames.includes(key)) d.frames.push(key); }
      else if (k === 'job') { d.jobs[v.id] = d.jobs[v.id] || { applied: 0, rejected: 0, shown: 0 }; for (const [a, b] of Object.entries(v.add)) d.jobs[v.id][a] = (d.jobs[v.id][a] || 0) + b; }
      else d[k] = (d[k] || 0) + v;
    }
    d.frames = d.frames.slice(-5000);
    all[day] = d;
    for (const k of Object.keys(all).sort().slice(0, -60)) delete all[k];   // giữ 60 ngày
    GM_setValue('stats', all);
  }
  function statsAfterApply(written, others) {
    const saved = written.reduce((sum, p) => sum + Math.max(0, (MANUAL_SECONDS[p.type] || 10) - 2), 0);
    const rejected = others.filter((p) => !p.checked && p.userTouched).length;
    bumpStats({ applied: written.length, rejected, saved, frame: S.propFrame, job: { id: S.jobId, add: { applied: written.length, rejected } } });
  }
  function statsToday() {
    const d = statsAll()[localDay()] || { applied: 0, rejected: 0, shown: 0, frames: [], saved: 0 };
    const kept = d.shown ? Math.round(100 * d.applied / Math.max(d.shown, d.applied)) : 0;
    return { applied: d.applied, rejected: d.rejected, frames: d.frames.length, minutes: Math.round(d.saved / 60), kept };
  }
  function downloadText(name, text, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: type || 'text/plain;charset=utf-8' }));
    a.download = name;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
  function exportReport() {
    const rows = [['ngay', 'job', 'nhan_da_ghi', 'de_xuat_bo', 'de_xuat_da_hien', 'frame_da_lam_ca_ngay', 'phut_tiet_kiem_uoc_tinh_ca_ngay']];
    for (const [day, d] of Object.entries(statsAll()).sort()) {
      const jobs = Object.entries(d.jobs || {});
      if (!jobs.length) rows.push([day, '', d.applied, d.rejected, d.shown, d.frames.length, Math.round(d.saved / 60)]);
      for (const [job, j] of jobs) rows.push([day, job, j.applied || 0, j.rejected || 0, j.shown || 0, d.frames.length, Math.round(d.saved / 60)]);
    }
    const csv = '﻿' + rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\r\n');
    downloadText(`sam3-assist-bao-cao-${localDay()}.csv`, csv, 'text/csv;charset=utf-8');
  }

  // ---- chế độ reviewer: nhãn do tool ghi mà chưa ai sửa ----
  S.review = null;   // {job, ids:Set, frameShapes:[...] }
  async function loadReviewer() {
    if (!cfg.reviewerMode || !S.jobId) { S.review = null; return; }
    try {
      const [ids, ann] = await Promise.all([backend('GET', `/learn/tool_shapes?job_id=${S.jobId}&user=${encodeURIComponent(S.user || '')}`), cvat('GET', `/api/jobs/${S.jobId}/annotations`)]);
      const untouched = new Set(ids.untouched || []);
      S.review = { job: S.jobId, ann, untouched, total: (ids.untouched || []).length };
    } catch (err) { S.review = null; }
    drawOverlay();
  }
  function reviewerMarks() {
    if (!S.review || S.review.job !== S.jobId) return [];
    return (S.review.ann.shapes || []).filter((x) => x.frame === S.frame && S.review.untouched.has(x.id));
  }

  // ---- hướng dẫn lần đầu ----
  const TOUR = [
    ['propose', 'Bấm "Gợi ý nhãn cho frame này" (hoặc Alt+G). SAM3 đề xuất nhãn, hiện dạng xem trước trên ảnh.'],
    ['list', 'Duyệt từng đề xuất: tick để giữ, đổi class nếu sai. Alt+J / Alt+K chọn, Alt+X tick, giữ Alt rồi bấm lên ảnh cũng tick được.'],
    ['filter', 'Kéo thanh "Ngưỡng" để ẩn đề xuất điểm thấp, bấm tên class để ẩn/hiện cả class.'],
    ['apply', 'Bấm "Ghi" để lưu vào CVAT, hoặc Alt+Enter để ghi rồi sang frame tiếp: frame sau đã được chạy sẵn.'],
    ['undo', 'Lỡ tay? "Hoàn tác" (Alt+Z) xoá lần ghi gần nhất, bấm tiếp để lùi thêm. Tab Tự học cho biết tool đã học gì từ cách bạn sửa.'],
  ];
  S.tour = cfg.tourDone ? null : 0;
  function tourCard() {
    if (S.tour === null || S.tour === undefined || S.tab !== 'propose') return null;
    const [, text] = TOUR[S.tour];
    return h('div', { class: 'note tour', role: 'dialog', 'aria-label': 'Hướng dẫn' },
      h('div', { class: 'small-text muted', text: `Hướng dẫn ${S.tour + 1}/${TOUR.length}` }),
      h('div', { text }),
      h('div', { class: 'row' }, h('span', { class: 'grow' }),
        h('button', { class: 'small', type: 'button', text: 'Bỏ qua', onclick: () => { S.tour = null; cfg.tourDone = true; saveCfg(); render(); } }),
        h('button', { class: 'small primary', type: 'button', text: S.tour + 1 < TOUR.length ? 'Tiếp' : 'Xong',
          onclick: () => { S.tour = S.tour + 1 < TOUR.length ? S.tour + 1 : null; if (S.tour === null) { cfg.tourDone = true; saveCfg(); } render(); } })));
  }
  const tourOn = (id) => S.tour !== null && S.tour !== undefined && S.tab === 'propose' && TOUR[S.tour][0] === id ? 'tour-target' : '';

  // ---- đồng hồ GPU ----
  // Panel và backend lệch bản (Tampermonkey cũ, backend chưa khởi động lại, máy chia sẻ chưa cập nhật) là nguyên nhân
  // hay gặp của "sửa rồi mà vẫn lỗi". Báo rõ bên nào cũ hơn và cách cập nhật đúng trường hợp.
  function cmpVersion(a, b) {
    const pa = String(a).split('.').map((x) => parseInt(x, 10) || 0), pb = String(b).split('.').map((x) => parseInt(x, 10) || 0);
    for (let i = 0; i < 3; i++) if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) < (pb[i] || 0) ? -1 : 1;
    return 0;
  }
  function versionNotice() {
    const bv = S.health && S.health.ok && S.health.version;
    if (!bv || cmpVersion(bv, VERSION) === 0) return null;
    const desktop = typeof GM_info !== 'undefined' && GM_info.scriptHandler === 'SAM3 Assist Desktop';
    let text, action = null;
    if (desktop) {
      // app desktop: panel và backend trên máy này luôn cùng bản, lệch là đang dùng backend của máy chia sẻ
      text = `Backend ở máy chia sẻ đang chạy bản ${bv}, app này là bản ${VERSION}. Nhờ người quản lý máy đó cập nhật app cho cùng bản; trong lúc chờ, vài tính năng có thể không chạy.`;
    } else if (cmpVersion(VERSION, bv) < 0) {
      text = `Panel (userscript) bản ${VERSION} cũ hơn backend bản ${bv}. Bấm "Cài bản mới" rồi chọn Update trong Tampermonkey, sau đó tải lại trang.`;
      action = h('button', { class: 'small', type: 'button', text: 'Cài bản mới',
        onclick: () => window.open(`${cfg.backendUrl.replace(/\/+$/, '')}/sam3-cvat-assist.user.js`, '_blank', 'noopener') });
    } else {
      text = `Backend bản ${bv} cũ hơn panel bản ${VERSION}. Cập nhật backend (cài app bản mới, hoặc git pull nếu chạy từ mã nguồn) rồi khởi động lại backend.`;
    }
    return h('div', { class: 'note warn version-note', role: 'status' }, h('span', { text }), action);
  }

  function gpuLine() {
    const g = S.health && S.health.gpu;
    if (!g || !g.total_mb) return null;
    const pct = Math.round(100 * g.used_mb / g.total_mb);
    const warn = pct >= 90 || (g.temp_c && g.temp_c >= 85);
    return h('span', { class: 'small-text', style: warn ? 'color:var(--err)' : '', title: g.name || 'GPU',
      text: `GPU ${(g.used_mb / 1024).toFixed(1)}/${(g.total_mb / 1024).toFixed(1)} GB${g.temp_c ? ` · ${g.temp_c}°C` : ''}${g.model_loaded === false ? ' · model đang nghỉ' : ''}` });
  }
  setInterval(async () => {
    if (!S.jobId || cfg.hidden || S.busy || !(S.health && S.health.ok)) return;
    try { const hl = await backend('GET', '/health'); S.health = Object.assign({}, S.health, hl); if (!cfg.collapsed) renderMeta(); } catch (e) { /* bỏ qua */ }
  }, 15000);

  // ---- giao diện: cỡ chữ, tương phản ----
  function applyLook() {
    const scale = Math.min(1.6, Math.max(0.8, Number(cfg.fontScale) || 1));
    host.style.setProperty('--font', `${(13 * scale).toFixed(1)}px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`);
    host.style.width = `${Math.round(380 * Math.max(1, scale))}px`;
    const hc = !!cfg.highContrast;
    for (const [k, v] of Object.entries({ '--bg': '#000000', '--bg-soft': '#1a1a1a', '--line': '#ffffff', '--text': '#ffffff', '--muted': '#e0e0e0', '--accent': '#ffd400', '--accent-text': '#000000' })) {
      if (hc) host.style.setProperty(k, v); else host.style.removeProperty(k);
    }
  }


  // ---- đa ngôn ngữ: toàn bộ chữ của panel có bản tiếng Anh (bộ dịch sinh từ backend/i18n/en.json) ----
  // <i18n-catalog> phần này do scripts/i18n_build.py sinh, đừng sửa tay
  const I18N_DATA = {"codes":{"Backend không phản hồi sau 5 phút.":"E_BACKEND_TIMEOUT","Backend trả lỗi {0}. ":"E_BACKEND_HTTP","CVAT trả lỗi {0} cho {1} {2}. {3}":"E_CVAT_HTTP","Không kết nối được backend tại {0}. Kiểm tra backend đã chạy và địa chỉ trong Cài đặt panel (nút bánh răng).":"E_BACKEND_UNREACHABLE"},"strings":{"\nMục có cờ ({0}) được giữ lại để bạn duyệt từng frame.":"\nFlagged items ({0}) are kept for you to review frame by frame.","\nProfile có sẵn của tool sẽ quay lại khi bạn giải nén bản cập nhật mới.":"\nThe tool's built-in profile comes back when you unpack a new update.","\nVăn bản và định nghĩa của guideline vẫn còn; gỡ cả guideline ở tab Guideline.":"\nThe guideline's text and definitions remain; remove the guideline itself in the Guideline tab.","\n{0} nhãn đang có cờ cần xem kỹ.":"\n{0} labels are flagged for a closer look.","\n{0} track đang có cờ cần xem.":"\n{0} tracks are flagged for review."," (còn {0} lần ghi trước đó có thể hoàn tác tiếp)":" ({0} earlier saves can still be undone)"," (theo dõi bằng SAM3 Video)":" (tracked with SAM3 Video)"," Guideline không có bảng taxonomy: chọn thêm file taxonomy (.json), hoặc bấm \"Lấy label từ task đang mở\".":" The guideline has no taxonomy table: also choose a taxonomy file (.json), or press \"Take labels from the open task\"."," Không dùng được SAM3 Video ({0}), đã theo dõi bằng cách thường.":" SAM3 Video unavailable ({0}); tracked the regular way."," SAM3 Video lỗi giữa chừng ({0}), đã làm lại bằng cách thường.":" SAM3 Video failed midway ({0}); redone the regular way."," Tải lại trang (F5) để CVAT nhận label mới.":" Reload the page (F5) so CVAT picks up the new labels."," Tải lại trang (F5) để thấy chúng trên canvas.":" Reload the page (F5) to see them on the canvas."," Tải lại trang (F5) để thấy chúng.":" Reload the page (F5) to see them."," Vật nào bị nhận nhầm: bấm \"Loại trừ\" ở dòng của nó để SAM3 bỏ mọi vật giống vậy.":" If an object is wrong: press \"Exclude\" on its row so SAM3 drops every object like it."," và ":" and "," {0} lưu ý cho cả job.":" {0} notes for the whole job."," {0} điểm":" {0} points"," · có nhãn do tool tạo":" · has tool-created labels"," · khuất {0} lần":" · occluded {0} times"," · model đang nghỉ":" · model idle"," · {0} có thể sót":" · {0} possibly missed"," · {0} đang ẩn":" · {0} hidden"," Đang loại trừ {0} mẫu.":" Excluding {0} examples."," Đang tải lại trang để CVAT nhận label mới…":" Reloading the page so CVAT picks up the new labels…"," Đang tải lại trang…":" Reloading the page…"," ở frame {0}":" at frame {0}","(đang tắt) ":"(off) ",", bỏ qua {0} frame {1}":", skipped {0} frames that {1}",", chạy trên máy":", running locally",", {0} chỗ có thể sót":", {0} possibly missed",", ảnh sẽ rời máy bạn":", images will leave your machine","API key (nếu backend có đặt)":"API key (if the backend has one)","API key, phím tắt, nguồn nhãn…":"API key, shortcuts, label source…","Alt+{0} trùng phím của trình duyệt/Windows (menu, quay lại trang...). Chọn phím khác.":"Alt+{0} clashes with a browser/Windows shortcut (menu, page back...). Choose another key.","Backend bản {0} cũ hơn panel bản {1}. Cập nhật backend (cài app bản mới, hoặc git pull nếu chạy từ mã nguồn) rồi khởi động lại backend.":"Backend version {0} is older than panel version {1}. Update the backend (install the new app, or git pull when running from source), then restart the backend.","Backend chưa sẵn sàng: mở Cài đặt để kiểm tra kết nối":"Backend not ready: open Settings to check the connection","Backend không phản hồi sau 5 phút.":"The backend did not respond within 5 minutes.","Backend kết nối tốt ({0} trên {1}), nhưng {2}":"Backend connection OK ({0} on {1}), but {2}","Backend trả lỗi {0}. ":"Backend returned error {0}. ","Backend đang chạy chế độ GIẢ LẬP (mock): kết quả không phải của SAM3, chỉ để thử giao diện.":"The backend is running in MOCK mode: results are not from SAM3, for trying the UI only.","Backend ở máy chia sẻ đang chạy bản {0}, app này là bản {1}. Nhờ người quản lý máy đó cập nhật app cho cùng bản; trong lúc chờ, vài tính năng có thể không chạy.":"The shared machine's backend runs version {0}, this app is version {1}. Ask whoever manages that machine to update to the same version; until then some features may not work.","Backend: {0} trên {1}{2}":"Backend: {0} on {1}{2}","Bài học từ các lần trước đã dùng ({0})":"Lessons from previous runs used ({0})","Bản nháp":"Draft","Bấm \"Ghi\" để lưu vào CVAT, hoặc Alt+Enter để ghi rồi sang frame tiếp: frame sau đã được chạy sẵn.":"Press \"Save\" to store in CVAT, or Alt+Enter to save and go to the next frame: it is already pre-computed.","Bấm \"Gợi ý nhãn cho frame này\" (hoặc Alt+G). SAM3 đề xuất nhãn, hiện dạng xem trước trên ảnh.":"Press \"Suggest labels for this frame\" (or Alt+G). SAM3 suggests labels, shown as a preview on the image.","Bấm lên vật cần tách. Bấm phải (hoặc Shift+bấm) vào chỗ KHÔNG thuộc vật để sửa. Enter hoặc \"Xong\" để giữ, Esc để huỷ.":"Click on the object to segment. Right-click (or Shift+click) on areas NOT part of the object to correct. Enter or \"Done\" to keep, Esc to cancel.","Bấm một dòng để xem trên ảnh · {0} bật/tắt dòng đang chọn · giữ Alt rồi bấm lên ảnh để tick":"Click a row to see it on the image · {0} toggles the selected row · hold Alt and click the image to tick","Bấm một điểm lên vật (bấm thêm điểm để sửa), SAM3 tách đúng vật đó":"Click a point on the object (more points to correct) and SAM3 segments exactly that object","Bấm phím muốn dùng (cùng Alt)":"Press the key to use (with Alt)","Bấm điểm":"Click points","Bấm điểm: {0} điểm thuộc vật, {1} điểm loại trừ.":"Click points: {0} on the object, {1} excluded.","Bấm để ẩn class này":"Click to hide this class","Bật":"On","Bật label này":"Enable this label","Bỏ":"Discard","Bỏ (loại) đề xuất đang chọn":"Drop (reject) the selected suggestion","Bỏ chọn hết":"Select none","Bỏ kết quả":"Discard results","Bỏ label này":"Remove this label","Bỏ mục có cờ":"Untick flagged","Bỏ qua":"Skip","Bỏ qua frame đã có bất kỳ nhãn nào":"Skip frames with any annotation","Bỏ qua label đã có trên frame (chỉ gợi ý label frame còn thiếu)":"Skip labels already on the frame (only suggest labels the frame is missing)","Bỏ track có cờ":"Untick flagged tracks","Bỏ điểm cuối":"Remove last point","Bỏ đề xuất":"Discard","CSS selector của ảnh nền canvas CVAT":"CSS selector of the CVAT canvas image","CVAT không trả về label nào cho job này, nên tool chưa gợi ý được.":"CVAT returned no labels for this job, so the tool cannot suggest yet.","CVAT không trả về label nào cho job này. Xem chi tiết ở tab Label.":"CVAT returned no labels for this job. See details in the Labels tab.","CVAT nhận yêu cầu nhưng chưa thấy label: {0}. Kiểm tra lại phần label của task.":"CVAT accepted the request but the labels are not there: {0}. Check the task's labels.","CVAT nhận yêu cầu nhưng vẫn còn {0} nhãn semi-auto trong {1}. Máy chủ có thể không cho đổi trường này.":"CVAT accepted the request but {0} semi-auto labels remain in {1}. The server may not allow changing this field.","CVAT trả lỗi {0} cho {1} {2}. {3}":"CVAT returned error {0} for {1} {2}. {3}","Ca khó trong ảnh mẫu ({0})":"Hard cases in sample images ({0})","Cho LLM bổ sung prompt tiếng Anh và định nghĩa (gửi VĂN BẢN guideline tới {0}{1})":"Let the LLM add English prompts and definitions (sends the guideline TEXT to {0}{1})","Cho phép gửi ảnh mẫu tới LLM ({0}{1})":"Allow sending sample images to the LLM ({0}{1})","Chuyển file vào profiles/archive/, hiện lại được":"Move the file to profiles/archive/; can be shown again","Chuyển frame là có đề xuất ngay":"Suggestions are ready when you switch frames","Chép JSON label":"Copy label JSON","Chép cả bộ label dạng JSON để dán vào tab Raw khi tạo task mới":"Copy the whole label set as JSON to paste into the Raw tab when creating a new task","Chưa bật AI soạn guideline: bản nháp vẫn tạo được từ số liệu, guideline mẫu và bài học đã có, nhưng class mới bạn tự viết định nghĩa.":"AI guideline drafting is off: drafts are still built from figures, sample guidelines and existing lessons, but you write definitions for new classes yourself.","Chưa chọn label nào để gợi ý.":"No labels chosen to suggest.","Chưa chốt (tạm dùng: {0})":"Not decided (currently using: {0})","Chưa có dữ liệu. Ghi vài nhãn bằng tool, sửa chúng như bình thường trong CVAT, lưu lại, rồi mở lại job (hoặc bấm Đối chiếu).":"No data yet. Save a few labels with the tool, edit them as usual in CVAT, save, then reopen the job (or press Review).","Chưa có profile nào bị ẩn.":"No hidden profiles.","Chưa có đề xuất cho frame này":"No suggestions for this frame yet","Chưa kiểm tra backend":"Backend not checked yet","Chưa nối backend":"No backend","Chưa thấy cần chỉnh gì.":"Nothing to adjust yet.","Chưa tick track nào.":"No tracks ticked.","Chưa tick đề xuất nào (đề xuất đang bị ẩn bởi bộ lọc không được ghi).":"No suggestions ticked (suggestions hidden by filters are not saved).","Chạy gợi ý lần lượt cho mọi frame trong khoảng. Kết quả được giữ lại: mở frame nào thì tab Gợi ý hiện ngay đề xuất của frame đó.":"Runs suggestions for every frame in the range. Results are kept: open any frame and the Suggest tab shows its suggestions right away.","Chạy mọi kiểm tra ở trên cho tất cả frame của job, cộng: frame chưa có nhãn, label chưa dùng lần nào. Lưu (Ctrl+S) trước khi rà.":"Runs every check above on all frames of the job, plus: frames without labels, labels never used. Save (Ctrl+S) before checking.","Chạy nền, vẫn gán nhãn được trong lúc chờ":"Runs in the background; you can keep labelling while waiting","Chạy sẵn gợi ý cho frame tiếp theo":"Pre-run suggestions for the next frame","Chế độ reviewer":"Reviewer mode","Chỉ chạy SAM3 trên các frame có thay đổi đáng kể":"Only run SAM3 on frames with significant change","Chỉ ghi {0} nhãn":"Save {0} only","Chỉ job này":"This job only","Chỉ job {0}":"Job {0} only","Chỉ label chưa có trong job":"Only labels not yet in the job","Chỉ đổi khi phần xem trước không hiện. Mặc định: #cvat_canvas_background":"Only change this if the preview does not show. Default: #cvat_canvas_background","Chỉnh hộp: {0}":"Box adjustment: {0}","Chọn các label chưa có nhãn nào trong job (ví dụ label vừa thêm theo guideline mới)":"Select labels with no annotations in the job (e.g. labels just added for a new guideline)","Chọn file guideline (.docx, .md, .txt) và/hoặc file taxonomy (.json, ví dụ JSON label ở tab Raw của task CVAT); chọn cả hai cùng lúc được. Tool đọc bảng taxonomy, định nghĩa, Issue type, checklist, tiêu chí reviewer; bạn kiểm tra rồi lưu. Sau đó tool có profile gợi ý nhãn, checklist ở tab Kiểm tra, và dùng guideline này khi soạn nháp.":"Choose a guideline file (.docx, .md, .txt) and/or a taxonomy file (.json, e.g. the label JSON in the Raw tab of a CVAT task); you can choose both at once. The tool reads the taxonomy table, definitions, Issue types, checklist and reviewer criteria; you check and save. The tool then has a suggestion profile, a checklist in the Check tab, and uses this guideline when drafting.","Chọn hết":"Select all","Chọn skeleton của guideline…":"Choose the guideline skeleton…","Chọn đề xuất tiếp theo":"Select next suggestion","Chọn đề xuất trước":"Select previous suggestion","Chốt lựa chọn":"Confirm choice","Ctrl+Enter để gửi · Esc để huỷ":"Ctrl+Enter to send · Esc to cancel","Cài bản mới":"Install new version","Cài đặt panel":"Panel settings","Các cách đã thử:":"Attempts made:","Cách theo dõi":"Tracking method","Cách thường: SAM3 từng frame, nối ID theo vị trí và màu":"Regular: SAM3 per frame, IDs linked by position and colour","Cách vẽ vạch":"How to draw markings","Câu hỏi cần chốt (còn {0}/{1})":"Questions to decide ({0}/{1} left)","Có thể bạn đã sót vật":"You may have missed an object","Có thể sót \"{0}\" (SAM3 tin {1}%, chưa có nhãn nào ở đó).":"\"{0}\" may be missing (SAM3 {1}% confident, no label there yet).","Cả job":"Whole job","Cảnh báo":"Warning","Cần thêm dữ liệu ({0}/12 đề xuất đã có kết quả).":"More data needed ({0}/12 suggestions with known outcome).","Cần ít nhất 8 ảnh":"At least 8 images needed","Cỡ chữ {0}%":"Font size {0}%","Duyệt dạng lưới ảnh ({0} frame)":"Review as a thumbnail grid ({0} frames)","Duyệt từng đề xuất: tick để giữ, đổi class nếu sai. Alt+J / Alt+K chọn, Alt+X tick, giữ Alt rồi bấm lên ảnh cũng tick được.":"Review each suggestion: tick to keep, change the class if wrong. Alt+J / Alt+K select, Alt+X ticks; holding Alt and clicking the image also ticks.","Dùng":"Use","Dùng app trên máy này thì không cần sửa. Colab / máy khác: dán địa chỉ https của tunnel.":"No change needed when using the app on this machine. Colab / another machine: paste the tunnel's https address.","Dùng cho job này":"Use for this job","Dùng danh sách label của task đang mở làm taxonomy":"Use the open task's label list as the taxonomy","Dùng làm profile":"Use as profile","Dùng model riêng cho các class đạt chuẩn khi gợi ý":"Use the custom model for classes that reach the bar when suggesting","Dùng điều tool đã học khi gợi ý (ngưỡng, chỉnh hộp)":"Use what the tool learned (thresholds, box adjustments)","Dừng":"Stop","Dữ liệu model riêng: {0} ảnh, {1} hộp.":"Custom model data: {0} images, {1} boxes.","Dữ liệu thông minh":"Smart data","Dữ liệu: {0} ảnh, {1} hộp{2}":"Data: {0} images, {1} boxes{2}","Enter để đồng ý · Esc để huỷ":"Enter to confirm · Esc to cancel","Frame cần duyệt:":"Frames to review:","Frame lệch nhiều:":"Frames with large differences:","Frame này":"This frame","Frame {0} có {1} \"{2}\", frame này mới có {3}: có thể còn sót {4}.":"Frame {0} has {1} \"{2}\", this frame only has {3}: {4} may be missing.","Frame {0}: {1}/{2} frame, {3} đề xuất. Còn khoảng {4}. Bấm Dừng để tạm dừng, tiếp tục sau (kể cả khi tắt trình duyệt).":"Frame {0}: {1}/{2} frames, {3} suggestions. About {4} left. Press Stop to pause and continue later (even after closing the browser).","Frame đã có nhãn":"Frames already labelled","Ghi các đề xuất đã tick":"Save ticked suggestions","Ghi cả mục có cờ":"Include flagged items","Ghi lại cách tôi xử lý đề xuất để tool học":"Record how I handle suggestions so the tool can learn","Ghi nhãn này vào CVAT ({0}, hoặc giữ Alt rồi bấm lên ảnh)":"Save this label to CVAT ({0}, or hold Alt and click the image)","Ghi nhận sai sót sau khi dùng guideline":"Record a mistake found after using the guideline","Ghi nhận để lần sau không lặp lại":"Record so it does not happen again","Ghi rồi sang frame tiếp (đề xuất đã chạy sẵn)":"Save, then go to the next frame (suggestions pre-computed)","Ghi thành track: mỗi đối tượng một ID qua các frame, CVAT nội suy giữa các keyframe. Label: {0}. Profile: {1}.":"Saves as tracks: one ID per object across frames, CVAT interpolates between keyframes. Labels: {0}. Profile: {1}.","Ghi track này":"Save this track","Ghi {0} nhãn":"Save {0} labels","Ghi {0} nhãn trên {1} frame vào job {2}?{3}":"Save {0} labels on {1} frames to job {2}?{3}","Ghi {0} nhãn vào CVAT":"Save {0} labels to CVAT","Ghi {0} nhãn vào frame {1} của job {2}?{3}":"Save {0} labels to frame {1} of job {2}?{3}","Ghi {0} track vào CVAT":"Save {0} tracks to CVAT","Ghi {0} track vào job {1}?{2}":"Save {0} tracks to job {1}?{2}","Ghi {0}, sang frame tiếp":"Save {0}, next frame","Giả lập":"Mock","Giữ / bỏ mọi đề xuất của frame này":"Keep / drop all suggestions of this frame","Giữ {0} · sửa hình {1} · đổi class {2} · xoá {3} · bỏ tick {4} · bạn tự vẽ thêm {5}":"Kept {0} · reshaped {1} · class changed {2} · deleted {3} · unticked {4} · drawn by you {5}","Guideline \"{0}\" đã được nạp trước đây. Ghi đè bản cũ (cả profile gl_{1}, nếu bạn đã sửa tay)?":"Guideline \"{0}\" was imported before. Overwrite the old version (including profile gl_{1}, if you edited it by hand)?","Guideline đã nạp":"Imported guidelines","Gõ thứ cần tìm (tiếng Anh), ví dụ: motorbike carrying boxes":"Type what to look for (English), e.g.: motorbike carrying boxes","Gỡ":"Remove","Gỡ guideline \"{0}\" khỏi tool? File được chuyển vào thư mục archive, không bị xoá hẳn.":"Remove guideline \"{0}\" from the tool? The file is moved to the archive folder, not deleted.","Gợi ý":"Suggest","Gợi ý (chưa tự áp dụng)":"Notes (not applied automatically)","Gợi ý cho cả job":"Suggest for whole job","Gợi ý cho {0} frame{1}":"Suggest for {0} frames{1}","Gợi ý cả job chỉ trên keyframe":"Suggest for whole job on keyframes only","Gợi ý cả job xong":"Whole-job suggestions finished","Gợi ý nhãn cho frame này":"Suggest labels for this frame","Gửi":"Send","Gửi case này cho mentor qua Issue của CVAT":"Send this case to the mentor via a CVAT Issue","Hai job không có frame chung nào có nhãn (job {0}: {1} frame, job {2}: {3} frame).":"The two jobs share no labelled frames (job {0}: {1} frames, job {2}: {3} frames).","Hay gán khác nhau: {0}":"Often labelled differently: {0}","Hiển thị":"Display","Hiện lại":"Show","Hoàn tác":"Undo","Hoàn tác ({0})":"Undo ({0})","Hoàn tác lần ghi gần nhất":"Undo last save","Huấn luyện":"Train","Huấn luyện lúc {0} trên {1}, {2} ảnh học, {3} ảnh kiểm.":"Trained at {0} on {1}, {2} training images, {3} test images.","Huấn luyện model riêng xong":"Custom model training finished","Huỷ":"Cancel","Hãy mô tả sai sót cụ thể hơn.":"Please describe the mistake more specifically.","Hôm nay":"Today","Hôm nay: {0} nhãn, {1} frame, ước tính tiết kiệm {2} phút.":"Today: {0} labels, {1} frames, about {2} minutes saved.","Hướng dẫn":"Tour","Hướng dẫn {0}/{1}":"Tour {0}/{1}","Hỏi lại trước khi ghi":"Ask before saving","Hộp quá nhỏ, hãy vẽ lại.":"Box too small, please draw again.","Hộp đã được chỉnh theo cách bạn hay sửa (tab Tự học).":"The box was adjusted the way you usually fix it (Learning tab).","Job có {0} track; tool chỉ kiểm tra shape, chưa kiểm tra track.":"The job has {0} tracks; the tool only checks shapes, not tracks yet.","Job không có label rectangle nào để theo dõi.":"The job has no rectangle labels to track.","Job này chưa có nhãn hộp (rectangle) nào để học.":"This job has no box (rectangle) labels to learn from.","Job này chưa có đề xuất nào do tool ghi (hoặc bạn đã tắt ghi nhận).":"This job has no suggestions saved by the tool yet (or you turned recording off).","Job {0}: {1} frame, {2} lỗi, {3} cảnh báo.":"Job {0}: {1} frames, {2} errors, {3} warnings.","Job {0}: {1} frame, {2} đề xuất, {3} mục có cờ.":"Job {0}: {1} frames, {2} suggestions, {3} flagged items.","Job {0}: {1} track, {2} track có cờ.":"Job {0}: {1} tracks, {2} flagged tracks.","Job đã có {0} track. Track mới có thể trùng với track cũ.":"The job already has {0} tracks. New tracks may duplicate old ones.","Keyframe dày":"Dense keyframes","Keyframe theo chuyển động: {0}/{1} frame":"Motion keyframes: {0}/{1} frames","Khi duyệt":"While reviewing","Khi ghi vào CVAT":"When saving to CVAT","Khi task đã đủ label và bạn đã bấm \"Dùng làm profile\": tool gán nhãn được cho mọi frame (ảnh, skeleton) hoặc theo dõi (video).":"Once the task has all labels and you have pressed \"Use as profile\": the tool can label every frame (images, skeletons) or track (video).","Khoảng frame không hợp lệ.":"Invalid frame range.","Khó quan sát":"Hard to see","Không bỏ qua (đề xuất trùng nhãn cũ bị bỏ tick)":"Do not skip (suggestions duplicating existing labels are unticked)","Không chắc biên":"Uncertain boundary","Không chắc class":"Uncertain class","Không chắc phạm vi":"Uncertain scope","Không có lần ghi nào của job này để hoàn tác.":"No saves in this job to undo.","Không có lỗi nào để tạo Issue.":"No errors to create Issues for.","Không có nhãn semi-auto nào trong {0}.":"No semi-auto labels in {0}.","Không có trong guideline":"Not in the guideline","Không có trên frame đang xem.":"Not on the current frame.","Không có đề xuất nào sẵn sàng để ghi.":"No suggestions ready to save.","Không kết nối được backend tại {0}. Kiểm tra backend đã chạy và địa chỉ trong Cài đặt panel (nút bánh răng).":"Cannot connect to the backend at {0}. Check the backend is running and the address in Panel settings (gear button).","Không thấy lỗi đo được bằng máy. Các mục \"Tự rà\" vẫn cần bạn kiểm tra.":"No machine-measurable errors found. The \"Self-check\" items still need your review.","Không tách được vật ở các điểm này. Thử bấm vào giữa vật.":"Could not segment an object at these points. Try clicking the middle of the object.","Không tìm thấy canvas của CVAT nên không bấm điểm được.":"CVAT canvas not found, so points cannot be clicked.","Không tìm thấy canvas của CVAT nên không vẽ hộp được. Xem mục \"Xử lý sự cố\" trong README.":"CVAT canvas not found, so a box cannot be drawn. See \"Troubleshooting\" in the README.","Không tìm thấy ô số frame. Hãy tự chuyển tới frame {0}.":"Frame number box not found. Please go to frame {0} yourself.","Không áp điều đã học cho label này (vẫn giữ dữ liệu)":"Do not apply what was learned for this label (data is kept)","Không đọc được file.":"Cannot read the file.","Khớp":"Matched","Kiểm luật QA trước khi ghi":"Check QA rules before saving","Kiểm tra":"Check","Kiểm tra ID":"Check ID","Kiểm tra annotation của frame này":"Check annotations on this frame","Kiểm tra attribute":"Check attributes","Kiểm tra kết nối":"Test connection","Kiểm tra occluded":"Check occluded","Kiểm tra trước khi ghi: {0} lỗi trên các đề xuất đã tick.\n\n{1}\n\nVẫn ghi?":"Pre-save check: {0} errors on the ticked suggestions.\n\n{1}\n\nSave anyway?","Kéo chuột trên ảnh để vẽ một hộp quanh đối tượng. Esc để huỷ.":"Drag on the image to draw a box around the object. Esc to cancel.","Kéo thanh \"Ngưỡng\" để ẩn đề xuất điểm thấp, bấm tên class để ẩn/hiện cả class.":"Drag the \"Threshold\" slider to hide low-score suggestions; click a class name to hide/show the whole class.","Kết nối":"Connect","Kết nối tốt: {0} trên {1}, phiên bản {2}. Đã đọc {3} label.":"Connection OK: {0} on {1}, version {2}. Read {3} labels.","Kết quả quá lớn để giữ lại khi tải lại trang. Hãy ghi hoặc duyệt trước khi tải lại.":"Result too large to keep across a page reload. Save or review it before reloading.","Label cho hộp vẽ tay":"Label for hand-drawn boxes","Label cần gợi ý ({0}/{1}):":"Labels to suggest ({0}/{1}):","Label lấy từ task CVAT. Prompt nên là cụm danh từ tiếng Anh ngắn. Thay đổi được nhớ theo tên label.":"Labels come from the CVAT task. Prompts should be short English noun phrases. Changes are remembered by label name.","Label nào trong job cũng đã có nhãn; chọn tay label cần gợi ý.":"Every label in the job already has annotations; choose the labels to suggest by hand.","Label này là skeleton nào của guideline":"Which guideline skeleton this label is","Loại bài toán (tool đoán: {0})":"Task type (tool guess: {0})","Loại shape":"Shape type","Loại trừ":"Exclude","Lưu vào tool":"Save to tool","Lượt \"Tìm tương tự\" này đã cũ. Vẽ lại hộp mẫu.":"This \"Find similar\" session is stale. Draw the example box again.","Lượt gợi ý cả job đang tạm dừng: còn {0}/{1} frame.":"The whole-job run is paused: {0}/{1} frames left.","Lấy label từ task đang mở":"Take labels from the open task","Lỗ hổng tool tự phát hiện ({0})":"Gaps the tool found ({0})","Lỗi":"Error","Lỡ tay? \"Hoàn tác\" (Alt+Z) xoá lần ghi gần nhất, bấm tiếp để lùi thêm. Tab Tự học cho biết tool đã học gì từ cách bạn sửa.":"Made a mistake? \"Undo\" (Alt+Z) deletes the last save; press again to go further back. The Learning tab shows what the tool learned from your edits.","Model riêng của dự án":"Project custom model","Muốn AI viết giúp: mở app SAM3 Label Assist trên máy chạy backend → Cài đặt → AI soạn guideline, rồi bật lại backend.":"To let AI help: open the SAM3 Label Assist app on the computer running the backend → Settings → AI guideline drafting, then restart the backend.","Một detector nhỏ học từ chính các nhãn hộp đã duyệt của bạn. Class nào đạt chuẩn (đủ ví dụ, đúng ≥ 70%, tìm được ≥ 60% trên ảnh giữ lại để kiểm) mới được dùng, nhanh hơn SAM3 nhiều lần; class khác vẫn do SAM3 làm.":"A small detector that learns from your own reviewed box labels. Only classes that reach the bar (enough examples, precision ≥ 70%, recall ≥ 60% on held-out images) are used, many times faster than SAM3; other classes are still handled by SAM3.","Mở bản nháp để sửa và duyệt":"Open the draft to edit and approve","Mở frame này":"Open this frame","Mở rộng panel":"Expand panel","Mục đích của bộ dữ liệu (model sẽ dùng nhãn này để làm gì)":"Purpose of the dataset (what the model will use these labels for)","Ngôn ngữ / Language":"Language / Ngôn ngữ","Ngưỡng":"Threshold","Ngưỡng tin cậy":"Confidence threshold","Ngưỡng tin cậy khi tìm người":"Confidence threshold when finding people","Ngưỡng {0} → {1}: {2}":"Threshold {0} → {1}: {2}","Ngưỡng {0}%":"Threshold {0}%","Nhập số job khác (cùng task, cùng frame) để so.":"Enter another job number (same task, same frames) to compare.","Nhắc khi frame có ít vật hơn frame bên cạnh":"Warn when a frame has fewer objects than its neighbors","Nâng cao":"Advanced","Nên làm trước ({0}):":"Do first ({0}):","Nạp bản nháp gần nhất của job này":"Load this job's latest draft","Nạp guideline có sẵn cho task khác":"Import a guideline for another task","Nạp lại":"Reload","Nạp lại profile":"Reload profiles","Nội dung Issue gửi mentor/reviewer:":"Issue text for the mentor/reviewer:","Outside cả skeleton ({0} điểm)":"Whole skeleton Outside ({0} points)","Panel (userscript) bản {0} cũ hơn backend bản {1}. Bấm \"Cài bản mới\" rồi chọn Update trong Tampermonkey, sau đó tải lại trang.":"The panel (userscript) version {0} is older than backend version {1}. Click \"Install new version\", choose Update in Tampermonkey, then reload the page.","Phát hiện ({0})":"Findings ({0})","Phân tích frame của job":"Analyse the job's frames","Phân tích lại":"Analyse again","Phân tích ảnh nén của các frame (không dùng GPU): frame nào khác lạ / khó nên làm trước, và keyframe theo chuyển động cho video.":"Analyses compressed frame images (no GPU): which frames are unusual / hard and worth doing first, and motion keyframes for video.","Phím Alt+{0} đang dùng cho \"{1}\".":"Alt+{0} is already used for \"{1}\".","Phím tắt (giữ Alt + phím)":"Shortcuts (hold Alt + key)","Phạm vi":"Scope","Profile có sẵn \"{0}\" đã khớp các label này. \"Tự chọn profile\" sẽ giữ profile có sẵn; muốn dùng guideline mới thì chọn tay profile của nó ở đầu panel (hoặc bấm \"Dùng cho job này\").":"Existing profile \"{0}\" already matches these labels. \"Auto-select profile\" keeps the existing profile; to use the new guideline, choose its profile by hand at the top of the panel (or press \"Use for this job\").","Profile guideline · đang dùng: {0}":"Guideline profile · in use: {0}","Profile đang dùng ({0})":"Active profiles ({0})","Profile đang ẩn ({0})":"Hidden profiles ({0})","Prompt SAM3 để tìm người":"SAM3 prompt to find people","Prompt cho SAM3, cách nhau bằng dấu phẩy":"Prompts for SAM3, separated by commas","Quay lại":"Back","Quét {0} ảnh và soạn nháp":"Scan {0} images and draft","Quên":"Forget","Quên tất cả":"Forget all","Quản lý profile":"Manage profiles","Rà cả job":"Check whole job","Rà cả job xong":"Whole-job check finished","Rà {0} frame có nhãn: {1} lỗi, {2} cảnh báo{3}.{4}":"Checked {0} labelled frames: {1} errors, {2} warnings{3}.{4}","SAM3 Assist: hiện/ẩn panel":"SAM3 Assist: show/hide panel","SAM3 Label Assist {0}. Giữ Alt rồi bấm lên ảnh để tick / bỏ tick đề xuất.":"SAM3 Label Assist {0}. Hold Alt and click the image to tick / untick suggestions.","SAM3 Video (thử nghiệm): model tự giữ ID, tốn VRAM hơn":"SAM3 Video (experimental): the model keeps IDs itself, uses more VRAM","SAM3 đang phân tích frame…":"SAM3 is analysing the frame…","SAM3 đang tách vật theo các điểm…":"SAM3 is segmenting the object from the points…","SAM3 đang tìm \"{0}\" trong {1} frame…":"SAM3 is searching for \"{0}\" in {1} frames…","SAM3 đang tìm lại, bỏ các vật giống hộp loại trừ…":"SAM3 is searching again, excluding objects like the excluded boxes…","SAM3 đang tìm đối tượng trong hộp…":"SAM3 is looking for the object in the box…","Sang frame tiếp":"Next frame","Sang tab Cả job":"Go to the Whole job tab","So các nhãn tool đã ghi với nhãn hiện tại (sau khi bạn sửa) rồi học lại. Tool cũng tự làm việc này mỗi lần mở job.":"Compare the labels the tool saved with the current labels (after your edits) and re-learn. The tool also does this every time a job opens.","So nhãn của job này với một job khác gán cùng các frame (ví dụ job kiểm tra chéo). Class đồng thuận thấp thường là chỗ guideline còn mơ hồ.":"Compares this job's labels with another job that labelled the same frames (e.g. a cross-check job). Classes with low agreement usually point to vague parts of the guideline.","So sánh":"Compare","So với job":"Compare with job","Soạn guideline nháp từ ảnh của job":"Draft a guideline from job images","Sẵn sàng":"Ready","Số ảnh mẫu":"Number of sample images","Task còn thiếu: {0}.":"Task is missing: {0}.","Task đã có đủ label theo guideline.{0}":"The task already has all labels from the guideline.{0}","Task đã có đủ label theo taxonomy của bản nháp.":"The task already has all labels from the draft's taxonomy.","Task đã có đủ label theo taxonomy.":"The task already has all taxonomy labels.","Theo dõi {0} frame":"Track {0} frames","Theo dõi đối tượng (video)":"Track objects (video)","Theo dõi đối tượng xong":"Object tracking finished","Thu gọn panel":"Collapse panel","Thu thập từ job này":"Collect from this job","Thêm label":"Add label","Thêm label còn thiếu":"Add missing labels","Thêm {0} label còn thiếu vào task":"Add {0} missing labels to the task","Thêm {0} label vào {1}?\n{2}\n\nTool chỉ thêm label mới; label đang có được giữ nguyên.":"Add {0} labels to {1}?\n{2}\n\nThe tool only adds new labels; existing labels are kept.","Thêm {0} đề xuất từ hộp vừa vẽ.{1}{2}":"Added {0} suggestions from the box just drawn.{1}{2}","Thứ tự điểm":"Point order","Tick / bỏ tick đề xuất đang chọn":"Tick / untick the selected suggestion","Tiếng Việt":"Tiếng Việt","Tiếp":"Next","Tiếp tục":"Continue","Tool chỉ gợi ý. Bạn duyệt từng nhãn rồi mới ghi vào CVAT.":"The tool only suggests. You review each label before it is saved to CVAT.","Tool gợi ý nhãn đang dùng profile sinh từ bản nháp này. Đổi lại ở ô chọn profile phía trên.":"Suggestions now use the profile generated from this draft. Change it back in the profile selector above.","Tool học từ cách BẠN xử lý đề xuất: nhãn giữ nguyên, nhãn bị kéo lại, đổi class, xoá, bỏ tick, và nhãn bạn tự vẽ thêm. Từ đó nó chỉnh ngưỡng tin cậy và cách đặt hộp cho từng label. Model SAM3 không bị huấn luyện lại; chỉnh tay ở tab Label luôn thắng điều tool đã học.":"The tool learns from how YOU handle suggestions: labels kept, labels dragged, class changes, deletions, unticks, and labels you draw yourself. From that it adjusts the confidence threshold and box placement for each label. The SAM3 model is not retrained; manual settings in the Labels tab always win over what the tool learned.","Tool quét một số ảnh mẫu, đo xem trong ảnh có gì và chỗ nào dễ nhầm, rồi soạn bản nháp theo khung của các guideline mẫu. Luật là quyết định của người: tool hỏi, bạn chốt.":"The tool scans some sample images, measures what is in them and what is easy to confuse, then drafts a guideline in the structure of the sample guidelines. Rules are human decisions: the tool asks, you decide.","Tool tự thấy: ":"The tool noticed: ","Tool đã học: {0} → {1} ({2}). Gõ số khác để đặt tay.":"The tool learned: {0} → {1} ({2}). Type another number to set it by hand.","Track nhầm?":"Wrong track?","Trái/phải?":"Left/right?","Trên các đề xuất đã tick":"On the ticked suggestions","Trùng lặp":"Duplicate","Trạng thái backend":"Backend status","Trạng thái điểm":"Point state","Tìm":"Find","Tìm \"{0}\": {1}/{2} frame, thấy {3}.":"Searching \"{0}\": {1}/{2} frames, found {3}.","Tìm cả đối tượng có thể bị sót (chạy SAM3 trên từng frame, chậm như Gợi ý cho cả job)":"Also look for possibly missed objects (runs SAM3 on every frame, as slow as Suggest for whole job)","Tìm frame có… (tiếng Anh, vd: motorbike carrying boxes)":"Find frames with… (English, e.g. motorbike carrying boxes)","Tìm frame xong":"Frame search finished","Tìm tương tự":"Find similar","Tìm được":"Recall","Tương phản cao":"High contrast","Tạo Issue":"Create issue","Tạo Issue cho mọi lỗi":"Create issues for all errors","Tạo profile từ bản nháp để tool gợi ý nhãn theo đúng các luật này":"Create a profile from the draft so the tool suggests labels by exactly these rules","Tạo {0} Issue trên CVAT (mỗi lỗi một Issue, đặt đúng frame và vị trí)?":"Create {0} Issues in CVAT (one Issue per error, at the right frame and position)?","Tải .md":"Download .md","Tải lại trang sau khi ghi":"Reload the page after saving","Tất cả":"All","Tắt":"Off","Tắt bài học sai ở trang duyệt bản nháp.":"Turn off wrong lessons on the draft review page.","Tới frame này và khoanh vùng ví dụ":"Go to this frame and highlight the example","Tới frame đầu":"Go to first frame","Từ frame":"From frame","Từ khoá thêm (tiếng Anh, cách nhau bằng dấu phẩy)":"Extra keywords (English, separated by commas)","Tự chọn profile":"Auto-select profile","Tự chọn: {0}":"Auto: {0}","Tự gợi ý khi chuyển frame":"Suggest automatically when changing frame","Tự học":"Learning","Tự rà":"Self-check","Tự vẽ một nhãn":"Draw a label yourself","Tự điền: {0} (đổi được trong CVAT sau khi ghi)":"Auto-filled: {0} (can be changed in CVAT after saving)","Viền tím \"AI\" cho nhãn tool ghi mà chưa ai sửa":"Purple \"AI\" outline on labels the tool saved that nobody has edited","Ví dụ":"Examples","Ví dụ: phát hiện người đi bộ cho camera hành trình":"E.g. pedestrian detection for dash cams","Ví dụ: reviewer trả bài vì xe ba bánh chở hàng phải gán truck":"E.g. the reviewer rejected work because cargo tricycles must be labelled truck","Vật này bị nhận nhầm: dùng nó làm mẫu ÂM, SAM3 tìm lại và bỏ mọi vật giống nó":"This object is wrong: use it as a NEGATIVE example; SAM3 searches again and drops every object like it","Vẽ hộp":"Draw box","Vẽ một hộp làm mẫu, SAM3 tìm mọi đối tượng giống vậy":"Draw an example box and SAM3 finds every similar object","Vẽ một hộp, SAM3 lấy đúng đối tượng đó":"Draw a box and SAM3 takes exactly that object","Về mặc định của profile":"Reset to profile defaults","Về phím mặc định":"Reset shortcuts","Xem lại hướng dẫn":"Show the tour again","Xem trước: {0}":"Preview: {0}","Xoá":"Delete","Xoá dữ liệu và model riêng của profile này?":"Delete this profile's custom model and data?","Xoá dữ liệu và điều đã học của label này":"Delete this label's data and what was learned","Xoá hẳn file profile":"Permanently delete the profile file","Xoá hẳn profile {0}? File bị xoá khỏi máy, không hoàn tác được.{1}{2}":"Permanently delete profile {0}? The file is removed from the machine and cannot be undone.{1}{2}","Xoá mọi điều tool đã học (mọi profile)?":"Delete everything the tool learned (all profiles)?","Xoá mọi điều tool đã học về \"{0}\"?":"Delete everything the tool learned about \"{0}\"?","Xoá {0} mà tool đã ghi ở frame {1}{2}?":"Delete {0} that the tool saved at frame {1}{2}?","Xuất báo cáo CSV":"Export CSV report","Xuất báo cáo năng suất (CSV)":"Export productivity report (CSV)","bạn sửa":"edited by you","chưa kiểm tra":"not checked","chưa kết nối":"not connected","có sẵn trong tool":"built into the tool","cả job {0}":"whole job {0}","hai cạnh dài (crosswalk)":"two long sides (crosswalk)","hai mép (vạch kép)":"two edges (double line)","luôn có":"always present","model khuôn mặt":"face model","ngay (đã chạy sẵn)":"instantly (pre-computed)","nạp từ guideline":"imported from guideline","project {0} (label của task này nằm ở project)":"project {0} (this task's labels live in the project)","prompt tiếng Anh":"English prompt","skeleton {0} điểm":"{0}-point skeleton","skeleton: không dùng prompt":"skeleton: no prompt used","suy ra bằng luật":"derived by rules","số job":"job number","tab Label của job":"the job's Labels tab","task đang mở":"the open task","theo tim vạch":"along the centre","tới":"to","tự học":"learned","{0} ({1} điểm)":"{0} ({1} points)","{0} Issue type · {1} mục checklist · {2} tiêu chí reviewer. Prompt là cụm tiếng Anh ngắn SAM3 dùng để tìm vật (rê chuột lên ô để xem nguồn); để trống thì tool không tự tìm label đó.":"{0} Issue types · {1} checklist items · {2} reviewer criteria. The prompt is the short English phrase SAM3 uses to find objects (hover to see its source); if empty, the tool will not look for that label.","{0} Nếu tài khoản của bạn không có quyền sửa task, bấm \"Chép JSON label\" và gửi cho người tạo task.":"{0} If your account cannot edit the task, press \"Copy label JSON\" and send it to the task creator.","{0} cờ":"{0} flags","{0} frame có kết quả · {1} đề xuất · {2} mục có cờ":"{0} frames with results · {1} suggestions · {2} flagged items","{0} frame cần xem · {1} lỗi · {2} cảnh báo{3}":"{0} frames to review · {1} errors · {2} warnings{3}","{0} giây":"{0} seconds","{0} lỗi · {1} khác{2}":"{0} errors · {1} other{2}","{0} nhãn":"{0} labels","{0} nhãn đã ghi · {1} đề xuất bị bỏ · {2} frame · ước tính tiết kiệm {3} phút":"{0} labels saved · {1} suggestions dropped · {2} frames · about {3} minutes saved","{0} phút":"{0} minutes","{0} track (frame {1}–{2}) · {3} đã chọn":"{0} tracks (frames {1}–{2}) · {3} selected","{0} vật, điểm {1}%":"{0} objects, score {1}%","{0} · {1} ảnh · {2} loại đối tượng · {3}% bị che · {4}% rất nhỏ":"{0} · {1} images · {2} kinds of objects · {3}% occluded · {4}% very small","{0} Đổi lại ở ô chọn profile phía trên.":"{0} Change it back in the profile selector above.","{0} điều đã học cho \"{1}\".":"{0} learned for \"{1}\".","{0} đề xuất {1}, {2} cần xem kỹ. Duyệt rồi bấm \"Ghi\" (hoặc {3}: ghi và sang frame tiếp).":"{0} suggestions {1}, {2} need a closer look. Review, then press \"Save\" (or {3}: save and go to the next frame).","{0} đề xuất · {1} đã chọn{2}":"{0} suggestions · {1} selected{2}","{0}\"{1}\": thấy ở {2} frame.":"{0}\"{1}\": found in {2} frames.","{0}. Bấm nhiều lần để lùi thêm (tối đa 20 lần ghi).":"{0}. Press repeatedly to go further back (up to 20 saves).","{0}/{1} điểm{2}":"{0}/{1} points{2}","{0}: ghi nhưng ở lại frame này":"{0}: save but stay on this frame","{0}: ghi rồi sang frame {1}, đề xuất của frame đó đã chạy sẵn":"{0}: save, then go to frame {1}, whose suggestions are already pre-computed","{0}{1} frame có {2} đề xuất{3}. {4} mục có cờ cần duyệt.":"{0}{1} frames have {2} suggestions{3}. {4} flagged items need review.","{0}{1} track, {2} track có cờ cần xem{3}. Tua qua các frame để kiểm tra rồi bấm Ghi.{4}":"{0}{1} tracks, {2} flagged tracks to review{3}. Scrub through the frames to check, then press Save.{4}","Áp lại điều đã học":"Apply what was learned again","Đang bắt đầu huấn luyện…":"Starting training…","Đang chạy sẵn frame {0}…":"Pre-computing frame {0}…","Đang cập nhật bản nháp…":"Updating the draft…","Đang cập nhật label…":"Updating labels…","Đang cập nhật phần tự học…":"Updating learning data…","Đang cập nhật profile…":"Updating profile…","Đang dùng":"In use","Đang dùng profile của \"{0}\": khớp {1}/{2} label của job.":"Using the profile of \"{0}\": matches {1}/{2} labels of the job.","Đang dừng quét, sẽ soạn nháp từ các ảnh đã quét…":"Stopping the scan; the draft will use the images scanned so far…","Đang dừng sau frame hiện tại…":"Stopping after the current frame…","Đang ghi nhãn của cả job…":"Saving labels for the whole job…","Đang ghi nhãn vào CVAT…":"Saving labels to CVAT…","Đang ghi nhận…":"Recording…","Đang ghi track vào CVAT…":"Saving tracks to CVAT…","Đang gỡ…":"Removing…","Đang gợi ý cho {0} frame…":"Suggesting for {0} frames…","Đang hoàn tác…":"Undoing…","Đang huấn luyện":"Training","Đang huấn luyện model riêng (chạy nền).":"Training the custom model (in the background).","Đang kiểm tra":"Checking","Đang kiểm tra annotation của frame…":"Checking the frame's annotations…","Đang kiểm tra backend…":"Checking the backend…","Đang lưu guideline vào tool…":"Saving the guideline to the tool…","Đang lấy ảnh frame…":"Getting the frame image…","Đang nạp bản nháp guideline…":"Loading the draft guideline…","Đang nạp lại profile…":"Reloading profiles…","Đang nối ID và chọn keyframe…":"Linking IDs and choosing keyframes…","Đang phân tích frame: {0}/{1}…":"Analysing frames: {0}/{1}…","Đang phân tích {0} frame…":"Analysing {0} frames…","Đang quét {0} ảnh mẫu…":"Scanning {0} sample images…","Đang rà cả job…":"Checking the whole job…","Đang so job {0} với job {1}…":"Comparing job {0} with job {1}…","Đang soạn bản nháp và tự kiểm tra…":"Writing the draft and self-checking…","Đang theo dõi {0} frame…":"Tracking {0} frames…","Đang thu thập ảnh + hộp đã duyệt của job…":"Collecting reviewed images + boxes from the job…","Đang thêm label vào CVAT…":"Adding labels to CVAT…","Đang tìm label chưa có trong job…":"Looking for labels not yet used in the job…","Đang tìm đối tượng có thể bị sót: {0}/{1} frame. Còn khoảng {2} giây.":"Looking for possibly missed objects: {0}/{1} frames. About {2} seconds left.","Đang tìm đối tượng: {0}/{1} frame. Còn khoảng {2} giây.":"Looking for objects: {0}/{1} frames. About {2} seconds left.","Đang tạo Issue…":"Creating Issues…","Đang tạo JSON label…":"Creating label JSON…","Đang tạo profile từ bản nháp…":"Creating a profile from the draft…","Đang tải danh sách profile…":"Loading profile list…","Đang xoá…":"Deleting…","Đang áp profile…":"Applying profile…","Đang đọc annotation của job…":"Reading the job's annotations…","Đang đọc job và label từ CVAT…":"Reading job and labels from CVAT…","Đang đọc {0}…":"Reading {0}…","Đang đối chiếu…":"Comparing…","Đang đổi source của {0} nhãn…":"Changing the source of {0} labels…","Đang đợi gợi ý đã chạy sẵn cho frame này…":"Waiting for the pre-computed suggestions for this frame…","Đang ẩn class này: bấm để hiện":"This class is hidden: click to show","Đánh dấu nhãn do tool ghi là source = \"semi-auto\"":"Mark tool labels as source = \"semi-auto\"","Đây là frame cuối của job.":"This is the last frame of the job.","Đây là giả thuyết của tool, không phải kết luận. Xem rồi sửa bản nháp nếu đúng.":"These are the tool's hypotheses, not conclusions. Review them and fix the draft if they are right.","Đã bật":"Turned on","Đã bỏ kết quả gợi ý của cả job.":"Discarded the whole-job suggestions.","Đã bỏ kết quả theo dõi.":"Discarded the tracking results.","Đã chép JSON của {0} label. Khi tạo task mới, dán vào tab Raw ở phần label.":"Copied JSON for {0} labels. When creating a new task, paste it into the Raw tab of the labels section.","Đã chép JSON của {0} label. Khi tạo task mới, dán vào tab Raw ở phần label.{1}":"Copied JSON for {0} labels. When creating a new task, paste it into the Raw tab of the labels section.{1}","Đã chọn {0} label chưa có nhãn nào trong job.":"Selected {0} labels that have no annotations in the job.","Đã cập nhật bản nháp theo lựa chọn của bạn.":"Updated the draft with your choices.","Đã duyệt":"Approved","Đã dừng ghi để bạn sửa các lỗi kiểm tra.":"Saving stopped so you can fix the check errors.","Đã dừng ở frame {0}. ":"Stopped at frame {0}. ","Đã dừng. ":"Stopped. ","Đã ghi nhận. Bài học này sẽ được dùng ở các lần soạn sau.":"Recorded. This lesson will be used in future drafts.","Đã ghi {0} nhãn. Đang tải lại trang{1}…":"Saved {0} labels. Reloading the page{1}…","Đã ghi {0} nhãn.{1}":"Saved {0} labels.{1}","Đã ghi {0} track.{1}":"Saved {0} tracks.{1}","Đã ghi {0}/{1} nhãn…":"Saved {0}/{1} labels…","Đã ghép {0} label mới và cập nhật {1} label từ {2} vào guideline \"{3}\". Kiểm tra rồi bấm Lưu.":"Merged {0} new labels and updated {1} labels from {2} into guideline \"{3}\". Check, then press Save.","Đã giữ vật vừa tách. Duyệt rồi ghi như các đề xuất khác.":"Kept the segmented object. Review and save it like other suggestions.","Đã gỡ \"{0}\".":"Removed \"{0}\".","Đã gửi {0}/{1} frame cho model riêng…":"Sent {0}/{1} frames to the custom model…","Đã hiện lại":"Shown again","Đã huỷ bấm điểm.":"Cancelled point clicking.","Đã huỷ vẽ hộp.":"Box drawing cancelled.","Đã huỷ.":"Cancelled.","Đã học cho profile đang dùng ({0} label)":"Learned for the current profile ({0} labels)","Đã kiểm tra {0} annotation: {1} lỗi, {2} cảnh báo.":"Checked {0} annotations: {1} errors, {2} warnings.","Đã lấy {0} label từ task đang mở ({1} label đã có trong bản xem trước).":"Took {0} labels from the open task ({1} labels were already in the preview).","Đã nạp văn bản, định nghĩa và checklist ({0} label). Guideline skeleton chưa có profile gợi ý tự động.":"Imported text, definitions and checklist ({0} labels). Skeleton guidelines do not get an automatic suggestion profile yet.","Đã nạp {0} label. Profile \"{1}\" đã có trong ô chọn profile; bấm \"Dùng cho job này\" ở danh sách bên dưới nếu job đang mở thuộc guideline này.":"Imported {0} labels. Profile \"{1}\" is now in the profile selector; press \"Use for this job\" in the list below if the open job belongs to this guideline.","Đã phân tích {0} frame: {1} keyframe theo chuyển động. Xem danh sách \"Nên làm trước\".":"Analysed {0} frames: {1} motion keyframes. See the \"Do first\" list.","Đã quét {0}/{1} ảnh, thấy {2} loại đối tượng. Còn khoảng {3} giây.":"Scanned {0}/{1} images, found {2} kinds of objects. About {3} seconds left.","Đã quên \"{0}\".":"Forgot \"{0}\".","Đã soạn bản nháp từ {0} ảnh: {1} câu hỏi cần chốt, {2} lỗ hổng tool tự thấy.":"Drafted from {0} images: {1} questions to decide, {2} gaps the tool found.","Đã thêm {0} label.{1}":"Added {0} labels.{1}","Đã thêm {0} label.{1} Tải lại trang (F5) để CVAT nhận label mới.":"Added {0} labels.{1} Reload the page (F5) so CVAT picks up the new labels.","Đã tách vật ({0}%). Bấm thêm điểm để sửa, Enter để giữ.":"Object segmented ({0}%). Click more points to correct, Enter to keep.","Đã tạm dừng ({0}/{1} frame). Bấm \"Tiếp tục\" để chạy nốt. ":"Paused ({0}/{1} frames). Press \"Continue\" to finish. ","Đã tạo Issue trên frame này.":"Created an Issue on this frame.","Đã tạo {0} Issue. Tải lại trang (F5) để thấy chúng trong CVAT.":"Created {0} Issues. Reload the page (F5) to see them in CVAT.","Đã tạo {0}/{1} Issue…":"Created {0}/{1} Issues…","Đã tắt":"Turned off","Đã xoá":"Deleted","Đã xoá mọi điều tool đã học.":"Deleted everything the tool learned.","Đã xoá. Đang tải lại trang…":"Deleted. Reloading the page…","Đã đọc \"{0}\": {1} label, {2} mục checklist.{3} Kiểm tra bảng bên dưới rồi bấm Lưu.":"Read \"{0}\": {1} labels, {2} checklist items.{3} Check the table below, then press Save.","Đã đọc {0} label từ CVAT nhưng chưa nối được backend. Kiểm tra ở Cài đặt panel (nút bánh răng).":"Read {0} labels from CVAT but cannot reach the backend yet. Check Panel settings (gear button).","Đã đọc {0} label từ {1}. Nếu có file guideline (.docx), chọn nó ngay sau đây: tool sẽ ghép label này với văn bản và checklist của guideline.":"Read {0} labels from {1}. If you have a guideline file (.docx), choose it next: the tool will merge these labels with the guideline's text and checklist.","Đã đối chiếu {0} đề xuất của job này với nhãn hiện tại và học lại.":"Compared {0} suggestions of this job with the current labels and re-learned.","Đã đổi {0} nhãn sang manual. Đang tải lại trang…":"Changed {0} labels to manual. Reloading the page…","Đã ẩn":"Hidden","Đóng lưới ảnh":"Close thumbnail grid","Đóng quản lý profile":"Close profile manager","Đúng":"Precision","Đạt":"Passed","Đề xuất này lấy từ lượt gợi ý cho cả job (tab Cả job).":"This suggestion comes from the whole-job run (Whole job tab).","Đề xuất thuộc frame khác. Hãy chạy lại gợi ý cho frame này.":"These suggestions belong to another frame. Run suggestions again for this frame.","Để thấy ngay nhãn mới trong CVAT":"To see new labels in CVAT right away","Để tool tự nhận":"Let the tool detect it","Địa chỉ backend SAM3":"SAM3 backend address","Đọc lại các file YAML trong thư mục profiles/ của backend":"Re-read the YAML files in the backend's profiles/ folder","Đọc lại label từ CVAT":"Reload labels from CVAT","Đối chiếu annotation đã lưu của frame với checklist trong guideline. Hãy lưu (Ctrl+S) trước khi kiểm tra.":"Compares the frame's saved annotations with the guideline checklist. Save (Ctrl+S) before checking.","Đối chiếu job này ngay":"Review this job now","Đồng thuận":"Agreement","Đồng thuận chung {0}% trên {1} frame chung.":"Overall agreement {0}% over {1} shared frames.","Đồng ý":"OK","Đổi class":"Change class","Đổi nhãn semi-auto đã lưu thành manual":"Change saved semi-auto labels to manual","Đổi source của {0} nhãn trong {1} từ \"semi-auto\" sang \"manual\"?\nGồm cả nhãn tạo bằng công cụ AI có sẵn của CVAT, nếu có.":"Change the source of {0} labels in {1} from \"semi-auto\" to \"manual\"?\nIncludes labels made with CVAT's built-in AI tools, if any.","Độ đậm vùng tô":"Fill opacity","Độ đồng thuận giữa hai người gán":"Agreement between two annotators","đã có nhãn":"already labelled","đã có đủ các label đã chọn":"already has all selected labels","đã có đủ label":"already has all labels","đúng {0}% · tìm được {1}%":"precision {0}% · recall {1}%","Ẩn":"Hide","Ẩn / hiện panel":"Hide / show panel","Ẩn = chuyển file vào thư mục profiles/archive/ của backend; profile biến khỏi ô chọn và khỏi \"Tự chọn profile\", hiện lại được. Xoá = xoá hẳn file. Hai profile dự phòng (generic, generic_track) luôn có.":"Hide = move the file into the backend's profiles/archive/ folder; the profile disappears from the selector and from \"Auto-select profile\" and can be shown again. Delete = delete the file permanently. The two fallback profiles (generic, generic_track) are always present.","Ẩn đề xuất có điểm thấp hơn":"Hide suggestions with a lower score","… và {0} frame nữa.":"… and {0} more frames.","‹ Trước":"‹ Previous"}};
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
  // </i18n-catalog>
  const I18N = makeI18n(I18N_DATA);
  function translateText(t) { return cfg.lang === 'en' ? I18N.tr(t) : t; }
  function translateTree(node) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    const texts = [];
    while (walker.nextNode()) texts.push(walker.currentNode);
    for (const tn of texts) { const v = I18N.tr(tn.nodeValue); if (v !== tn.nodeValue) tn.nodeValue = v; }
    node.querySelectorAll('[title],[placeholder],[aria-label]').forEach((el) => {
      for (const a of ['title', 'placeholder', 'aria-label']) { const v = el.getAttribute(a); if (v) { const t = I18N.tr(v); if (t !== v) el.setAttribute(a, t); } }
    });
  }
  // Hộp thoại của panel (theo ngôn ngữ đã chọn, cùng kiểu với panel). Esc, bấm ra ngoài hay Huỷ: không làm gì.
  // Hỏi xác nhận: Enter là đồng ý. Hỏi nội dung: Ctrl+Enter là gửi (Enter để xuống dòng).
  function uiDialog(msg, def) {
    const asking = typeof def === 'string';
    return new Promise((resolve) => {
      const back0 = document.activeElement;
      const ta = asking ? h('textarea', { 'aria-label': translateText(msg) }) : null;
      if (ta) ta.value = def;
      let closed = false;
      const done = (v) => {
        if (closed) return;
        closed = true;
        back.remove();
        if (back0 && back0.focus) try { back0.focus(); } catch (e) { /* bỏ qua */ }
        resolve(v);
      };
      const ok = h('button', { class: 'primary', type: 'button', text: translateText(asking ? 'Gửi' : 'Đồng ý'), onclick: () => done(asking ? ta.value : true) });
      const cancel = h('button', { type: 'button', text: translateText('Huỷ'), onclick: () => done(asking ? null : false) });
      const hint = h('small', { text: translateText(asking ? 'Ctrl+Enter để gửi · Esc để huỷ' : 'Enter để đồng ý · Esc để huỷ') });
      const back = h('div', { class: 'dlg-back' },
        h('div', { class: 'dlg', role: 'alertdialog', 'aria-modal': 'true' }, h('p', { text: translateText(msg) }), ta, h('div', { class: 'dlg-foot' }, hint, cancel, ok)));
      back.addEventListener('mousedown', (ev) => { if (ev.target === back) done(asking ? null : false); });
      back.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape') { ev.preventDefault(); done(asking ? null : false); }
        else if (ev.key === 'Enter' && (!asking || ev.ctrlKey || ev.metaKey) && ev.target !== cancel) { ev.preventDefault(); ok.click(); }
        else if (ev.key === 'Tab') {                          // giữ tiêu điểm trong hộp thoại
          const f = [ta, cancel, ok].filter(Boolean), i = f.indexOf(root.activeElement);
          ev.preventDefault(); f[(i + (ev.shiftKey ? f.length - 1 : 1)) % f.length].focus();
        }
      });
      root.append(back);
      (ta || ok).focus();
    });
  }
  const uiConfirm = (msg) => uiDialog(msg);
  const uiPrompt = (msg, def) => uiDialog(msg, def == null ? '' : String(def));

  // --------------------------------------------------------- vòng theo dõi -
  let autoTimer = null;
  function watch() {
    const ctx = readCtx();
    if (!ctx) {
      if (S.jobId !== null) { S.jobId = null; S.frame = null; clearProposals(); render(); }
      return;
    }
    if (ctx.jobId !== S.jobId) {
      if (S.busy) return; // đợi việc đang chạy xong rồi mới nạp job mới
      S.jobId = ctx.jobId; S.frame = ctx.frame; S.frameCache = null;
      loadJob();
      return;
    }
    const frame = ctx.frame !== null ? ctx.frame : S.frame;
    if (frame !== null && frame !== S.frame) {
      S.frame = frame; S.seq++; S.box = null; S.points = null; S.sel = null; S.hints = [];
      clearProposals(); loadBatchFrame();
      if (!S.busy) setStatus('');
      render(); drawOverlay();
      clearTimeout(autoTimer);
      const auto = consumeAutoNext() || cfg.autoPropose;
      if (auto && S.resolved && !S.batch[frame]) {
        // Đã chạy sẵn thì hiện ngay, không thì đợi bạn dừng lại ở frame này một chút rồi mới chạy
        const ready = pfStore().frames[frame] || pfInflight[frame];
        autoTimer = setTimeout(() => { if (!S.busy) propose(); else autoTimer = setTimeout(() => !S.busy && propose(), 800); }, ready ? 0 : 700);
      } else if (S.propSource === 'batch') schedulePrefetch(frame);
    }
  }

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && S.box) { S.box = null; setStatus('Đã huỷ vẽ hộp.'); drawOverlay(); return; }
    if (!S.jobId || !ev.altKey || ev.ctrlKey || ev.metaKey || ev.repeat) return;
    const key = ev.key.toLowerCase();
    const action = Object.keys(DEFAULT_KEYS).find((a) => keyOf(a) === key);
    if (!action) return;
    if (cfg.hidden && action !== 'panel') return;
    ev.preventDefault(); ev.stopPropagation();
    const free = !S.busy;
    if (action === 'panel') { cfg.hidden = !cfg.hidden; saveCfg(); render(); drawOverlay(); }
    else if (action === 'propose') { if (S.resolved && free) propose(); }
    else if (action === 'apply') { if (free) applyProposals(); }
    else if (action === 'applyNext') { if (free && S.resolved) applyAndNext(); }
    else if (action === 'undo') { if (free) undoLast(); }
    else if (action === 'next') moveSel(1);
    else if (action === 'prev') moveSel(-1);
    else if (action === 'toggle') { const p = selected(); if (p) toggleProposal(p); }
    else if (action === 'reject') {
      const p = selected();
      if (p) { toggleProposal(p, false); moveSel(1); }
    }
  }, true);

  GM_registerMenuCommand(translateText('SAM3 Assist: hiện/ẩn panel'), () => { cfg.hidden = !cfg.hidden; saveCfg(); render(); drawOverlay(); });

  document.body.append(overlayHost, host);
  applyPos();
  render();
  setInterval(watch, 400);
  watch();
  requestAnimationFrame(positionOverlay);
})();
