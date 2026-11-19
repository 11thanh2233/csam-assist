// ==UserScript==
// @name         SAM3 CVAT Assist
// @namespace    sam3-cvat-assist
// @version      0.7.0
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

  const VERSION = '0.7.0';
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
      throw new Error(`CVAT trả lỗi ${res.status} cho ${method} ${path.split('?')[0]}. ${detail}`);
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
        headers: { 'Content-Type': 'application/json', 'X-API-Key': cfg.apiKey || '' },
        data: body === undefined ? undefined : JSON.stringify(body),
        timeout: 300000,
        onload: (r) => {
          let data = null;
          try { data = JSON.parse(r.responseText); } catch (e) { /* không phải JSON */ }
          if (r.status >= 200 && r.status < 300) return resolve(data);
          const detail = data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : '';
          reject(new Error(`Backend trả lỗi ${r.status}. ${detail}`));
        },
        onerror: () => reject(new Error(`Không kết nối được backend tại ${cfg.backendUrl}. Kiểm tra backend đã chạy và địa chỉ trong tab Cài đặt.`)),
        ontimeout: () => reject(new Error('Backend không phản hồi sau 5 phút.')),
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

  const baseBody = () => ({ profile_id: cfg.profileId, labels: labelPayload(), overrides: overridePayload(), use_learned: cfg.useLearned !== false });

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
      const fr = await getFrame();
      let existing = [];
      try { existing = (await frameShapes()).existing; } catch (e) { /* vẫn chạy được khi không đọc được annotation */ }
      const res = await backend('POST', '/propose', Object.assign(baseBody(), { image_b64: fr.b64, existing }));
      if (seq !== S.seq || frame !== S.frame) return; // người dùng đã chuyển frame
      S.proposals = stamp(res.proposals || [], 'frame');
      S.existingIds[frame] = existing.filter((x) => x.id !== undefined).map((x) => x.id);
      S.warnings = res.warnings || [];
      S.propFrame = frame; S.imgW = res.width; S.imgH = res.height;
      S.qa = null;
      const flagged = S.proposals.filter((p) => p.flags.length).length;
      setStatus(`${S.proposals.length} đề xuất trong ${res.seconds}s, ${flagged} cần xem kỹ. Duyệt rồi bấm "Ghi vào CVAT".`, 'ok');
      drawOverlay();
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
    try { await backend('POST', '/learn/events', { events }); } catch (err) { /* tự học không được làm hỏng việc ghi nhãn */ }
  }

  // Đối chiếu nhãn tool đã ghi với annotation hiện tại của job (sau khi bạn sửa), rồi học lại.
  async function reviewLearning(silent) {
    if (cfg.learnEnabled === false || !S.jobId || !(S.health && S.health.ok)) return null;
    const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
    const shapes = (ann.shapes || []).map((x) => ({ id: x.id, frame: x.frame, label_id: x.label_id, type: x.type,
      points: x.type === 'mask' ? (x.points || []).slice(-4) : x.points, elements: x.elements }));
    const res = await backend('POST', '/learn/review', { job_id: S.jobId, shapes });
    S.learn = res.learned;
    if (res.reviewed) await resolveLabels();
    if (!silent) {
      setStatus(res.reviewed ? `Đã đối chiếu ${res.reviewed} đề xuất của job này với nhãn hiện tại và học lại.`
        : 'Job này chưa có đề xuất nào do tool ghi (hoặc bạn đã tắt ghi nhận).', 'ok');
    }
    return res;
  }

  async function loadLearn() {
    try { S.learn = await backend('GET', '/learn/summary'); } catch (err) { S.learn = { labels: [], notes: [] }; }
    render();
  }

  async function learnAction(path, body, msg) {
    await run('Đang cập nhật phần tự học…', async () => {
      S.learn = await backend('POST', path, body);
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
            onclick: () => window.confirm(`Xoá mọi điều tool đã học về "${l.label}"?`) && learnAction('/learn/forget', { profile_id: l.profile_id, label: l.label }, `Đã quên "${l.label}".`) })),
        h('div', { class: 'muted small-text', text: `Giữ ${c.kept} · sửa hình ${c.edited} · đổi class ${c.relabeled} · xoá ${c.deleted} · bỏ tick ${c.rejected} · bạn tự vẽ thêm ${c.missed}` }),
        ...lines.map((t) => h('div', { class: 'small-text', style: l.enabled ? '' : 'opacity:.6', text: (l.enabled ? '' : '(đang tắt) ') + t }))));
    }
    const notes = (S.learn.notes || []).filter((n) => !pid || n.profile_id === pid);
    if (notes.length) {
      out.push(h('h2', { text: 'Gợi ý (chưa tự áp dụng)' }));
      for (const n of notes) out.push(h('div', { class: 'note', text: (n.kind === 'tool' ? 'Tool tự thấy: ' : '') + n.text }));
    }
    out.push(h('div', { class: 'row' }, h('button', { type: 'button', disabled: !!S.busy || !(S.learn.labels || []).length, text: 'Quên tất cả',
      onclick: () => window.confirm('Xoá mọi điều tool đã học (mọi profile)?') && learnAction('/learn/forget', {}, 'Đã xoá mọi điều tool đã học.') })));
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
    if (!window.confirm(`Tạo ${items.length} Issue trên CVAT (mỗi lỗi một Issue, đặt đúng frame và vị trí)?`)) return;
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
    const out = [h('h2', { text: 'Rà cả job' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'Chạy mọi kiểm tra ở trên cho tất cả frame của job, cộng: frame chưa có nhãn, label chưa dùng lần nào. Lưu (Ctrl+S) trước khi rà.' }),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!cfg.qaFindMissing, onchange: (ev) => { cfg.qaFindMissing = ev.target.checked; saveCfg(); } }),
        h('span', { text: 'Tìm cả đối tượng có thể bị sót (chạy SAM3 trên từng frame, chậm như Gợi ý cho cả job)' })),
      h('button', { class: 'primary', type: 'button', disabled: busy || !S.resolved, onclick: runJobQa, text: 'Rà cả job' })];
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

  async function applyProposals() {
    const chosen = S.proposals.filter((p) => p.checked);
    if (!chosen.length) return setStatus('Chưa tick đề xuất nào.', 'error');
    if (S.propFrame !== S.frame) return setStatus('Đề xuất thuộc frame khác. Hãy chạy lại gợi ý cho frame này.', 'error');
    const flagged = chosen.filter((p) => p.flags.length).length;
    if (cfg.confirmApply) {
      const note = flagged ? `\n${flagged} nhãn đang có cờ cần xem kỹ.` : '';
      if (!window.confirm(`Ghi ${chosen.length} nhãn vào frame ${S.frame} của job ${S.jobId}?${note}`)) return;
    }
    await run('Đang ghi nhãn vào CVAT…', async () => {
      await saveCvatUi(); // lưu các thay đổi bạn đang làm dở trước khi ghi thêm
      const shapes = chosen.map((p) => toCvatShape(p));
      const res = await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=create`, {
        version: 0, tags: [], shapes, tracks: [],
      });
      GM_setValue('undo', { jobId: S.jobId, frame: S.propFrame, shapes: (res && res.shapes) || [], tracks: [], at: Date.now() });
      const frame = S.propFrame;
      learnFromApply(chosen, (res && res.shapes) || [], S.proposals, () => frame);
      if (S.batch[frame]) { delete S.batch[frame]; persist('batch'); }
      clearProposals();
      if (cfg.reloadAfterApply) {
        setStatus(`Đã ghi ${shapes.length} nhãn. Đang tải lại trang để hiển thị…`, 'ok');
        await sleep(400);
        reloadAtFrame(frame);
      } else {
        setStatus(`Đã ghi ${shapes.length} nhãn. Tải lại trang (F5) để thấy chúng trên canvas.`, 'ok');
      }
    });
  }

  async function undoLast() {
    const u = GM_getValue('undo', null);
    const nShapes = u && u.shapes ? u.shapes.length : 0;
    const nTracks = u && u.tracks ? u.tracks.length : 0;
    if (!u || u.jobId !== S.jobId || !(nShapes + nTracks)) {
      return setStatus('Không có lần ghi nào của job này để hoàn tác.', 'error');
    }
    const what = [nShapes ? `${nShapes} nhãn` : '', nTracks ? `${nTracks} track` : ''].filter(Boolean).join(' và ');
    if (!window.confirm(`Xoá ${what} mà tool đã ghi ở lần ghi gần nhất?`)) return;
    await run('Đang hoàn tác…', async () => {
      await saveCvatUi();
      for (let i = 0; i < Math.max(nShapes, nTracks, 1); i += 300) {
        await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=delete`, {
          version: 0, tags: [], shapes: (u.shapes || []).slice(i, i + 300), tracks: (u.tracks || []).slice(i, i + 300),
        });
      }
      GM_setValue('undo', null);
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
      if (!window.confirm(`Đổi source của ${count} nhãn trong ${where} từ "semi-auto" sang "manual"?\nGồm cả nhãn tạo bằng công cụ AI có sẵn của CVAT, nếu có.`)) return setStatus('Đã huỷ.');
      setStatus(`Đang đổi source của ${count} nhãn…`, 'busy');
      const manual = (o) => Object.assign({}, o, { source: 'manual' });
      await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=update`, {
        version: ann.version || 0, tags: [], shapes: shapes.map(manual), tracks: tracks.map(manual),
      });
      const after = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      const left = (after.shapes || []).concat(after.tracks || []).filter((o) => o.source === 'semi-auto' && inScope(o.frame)).length;
      if (left) return setStatus(`CVAT nhận yêu cầu nhưng vẫn còn ${left} nhãn semi-auto trong ${where}. Máy chủ có thể không cho đổi trường này.`, 'error');
      GM_setValue('undo', null); // id nhãn có thể đã đổi, lần hoàn tác cũ không còn đúng
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

  async function runBatch() {
    const r = jobRange();
    const frames = jobFrames(r.from, r.to);
    if (!frames.length) return setStatus('Khoảng frame không hợp lệ.', 'error');
    const picked = batchPicked();
    if (!picked.length) return setStatus('Chưa chọn label nào để gợi ý.', 'error');
    const mode = batchSkipMode();
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
        if (!ids.length) { skipped++; continue; }
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
        const per = (Date.now() - started) / done / 1000;
        const left = frames.length - i - 1;
        setStatus(`Frame ${f}: ${done} frame xong, ${made} đề xuất. Còn khoảng ${Math.round(per * left)} giây.`, 'busy');
        if (done % 10 === 0) persist('batch');
      }
      persist('batch');
      if (S.frame in S.batch) { S.proposals = []; loadBatchFrame(); }
      const st = batchStats();
      const stopped = S.stopRequested ? 'Đã dừng. ' : '';
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
    if (cfg.confirmApply && !window.confirm(`Ghi ${st.ready} nhãn trên ${st.frames.length} frame vào job ${S.jobId}?${note}`)) return;
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
      GM_setValue('undo', { jobId: S.jobId, frame: S.frame, shapes: created, tracks: [], at: Date.now() });
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
    if (cfg.confirmApply && !window.confirm(`Ghi ${chosen.length} track vào job ${S.jobId}?${flagged ? `\n${flagged} track đang có cờ cần xem.` : ''}`)) return;
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
      GM_setValue('undo', { jobId: S.jobId, frame: S.frame, shapes: [], tracks: (res && res.tracks) || [], at: Date.now() });
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
    const url = `${cfg.backendUrl.replace(/\/+$/, '')}/guideline/run/${S.guideRunId}?key=${encodeURIComponent(cfg.apiKey || '')}`;
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
    if (!window.confirm(`Thêm ${missing.length} label vào ${where}?\n${missing.map((l) => l.name).join(', ')}\n\nTool chỉ thêm label mới; label đang có được giữ nguyên.`)) return;
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
    if (S.imp.exists && !window.confirm(`Guideline "${a.title}" đã được nạp trước đây. Ghi đè bản cũ (cả profile gl_${a.id}, nếu bạn đã sửa tay)?`)) return;
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
    if (!window.confirm(`Thêm ${plan.missing.length} label vào ${where}?\n${plan.missing.map((l) => l.name).join(', ')}\n\nTool chỉ thêm label mới; label đang có được giữ nguyên.`)) return;
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
    if (!window.confirm(`Gỡ guideline "${item.title}" khỏi tool? File được chuyển vào thư mục archive, không bị xoá hẳn.`)) return;
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
    const message = window.prompt('Nội dung Issue gửi mentor/reviewer:', `[${code}] ${text}`);
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
    return !cfg.hidden && S.imgW > 0 && (S.proposals.length || S.box || tracks || evidence || (S.qa && S.qa.findings.length));
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
    overlaySvg.replaceChildren();
    overlayHost.style.pointerEvents = S.box ? 'auto' : 'none';
    overlayHost.style.cursor = S.box ? 'crosshair' : 'default';
    if (!S.imgW) return;
    overlaySvg.setAttribute('viewBox', `0 0 ${S.imgW} ${S.imgH}`);
    const base = { 'vector-effect': 'non-scaling-stroke', 'stroke-linejoin': 'round' };
    const showProps = S.propFrame === S.frame && S.tab !== 'qa';
    for (const p of showProps ? S.proposals : []) {
      const color = labelColor(p.label_id);
      const hot = S.highlight === 'p' + p.pid;
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
      --accent: #1b6ef3; --accent-text: #ffffff; --ok: #0f7b3f; --warn: #a15c00; --err: #b42318;
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
  const headerEl = h('header', {}, els.dot, h('h1', { text: 'SAM3 Assist' }), els.collapse);
  root.append(h('div', { class: 'panel' }, headerEl, els.meta, els.nav, els.body, els.status));
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
    els.dot.className = 'dot' + (!hl ? '' : !hl.ok ? ' bad' : hl.is_mock ? ' mock' : ' ok');
    els.dot.title = !hl ? 'Chưa kiểm tra backend' : !hl.ok ? 'Backend chưa sẵn sàng' : `Backend: ${hl.engine} trên ${hl.device}`;
    els.collapse.textContent = cfg.collapsed ? 'Mở' : 'Thu gọn';
  }

  function renderStatus() {
    els.status.className = 'status ' + S.statusKind;
    els.status.textContent = S.status;
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
    const using = S.resolved ? `Đang dùng: ${S.resolved.title}` : '';
    els.meta.replaceChildren(
      h('span', { text: `Job ${S.jobId ?? '?'} · Frame ${S.frame ?? '?'}` }),
      profileSelect,
      cfg.profileId === 'auto' && using ? h('span', { class: 'small-text', text: using }) : null,
      // Cài userscript mới mà quên cập nhật / khởi động lại backend (hoặc ngược lại) là nguyên nhân hay gặp của "sửa rồi mà vẫn lỗi".
      S.health && S.health.ok && S.health.version && S.health.version !== VERSION
        ? h('span', { class: 'small-text', style: 'color:var(--warn)',
          text: `Backend đang chạy bản ${S.health.version}, userscript là bản ${VERSION}: cập nhật cho cùng bản và khởi động lại backend.` })
        : null,
    );
  }

  function renderNav() {
    const tabs = [['propose', 'Gợi ý'], ['job', 'Cả job'], ['guide', 'Guideline'], ['labels', 'Label'], ['qa', 'Kiểm tra'], ['learn', 'Tự học'], ['settings', 'Cài đặt']];
    els.nav.replaceChildren(...tabs.map(([id, name]) => h('button', {
      type: 'button', role: 'tab', 'aria-selected': String(S.tab === id), text: name,
      onclick: () => { S.tab = id; S.highlight = null; render(); drawOverlay(); if (id === 'guide') loadGuide(); if (id === 'learn') loadLearn(); },
    })));
  }

  function flagBadges(flags) {
    return flags.map((f) => h('span', { class: 'badge', title: f.message, text: FLAG_NAMES[f.code] || f.code }));
  }

  function hover(li, key) {
    li.addEventListener('mouseenter', () => { S.highlight = key; li.classList.add('hot'); drawOverlay(); });
    li.addEventListener('mouseleave', () => { S.highlight = null; li.classList.remove('hot'); drawOverlay(); });
  }

  function renderPropose() {
    const out = [];
    const ready = S.resolved && S.health && S.health.ok;
    if (S.health && S.health.ok && S.health.is_mock) {
      out.push(h('div', { class: 'note', text: 'Backend đang chạy chế độ GIẢ LẬP (mock): kết quả không phải của SAM3, chỉ để thử giao diện.' }));
    }
    out.push(h('button', { class: 'primary', type: 'button', disabled: !ready || !!S.busy, onclick: propose, text: 'Gợi ý nhãn cho frame này' }));
    const boxLabel = h('select', { class: 'grow', title: 'Label cho hộp vẽ tay', onchange: (ev) => { S.boxLabelId = Number(ev.target.value); } },
      S.labels.map((l) => h('option', { value: String(l.id), text: l.name })));
    if (S.boxLabelId) boxLabel.value = String(S.boxLabelId);
    out.push(h('div', { class: 'row' },
      boxLabel,
      h('button', { type: 'button', disabled: !ready || !!S.busy, title: 'Vẽ một hộp, SAM3 lấy đúng đối tượng đó', onclick: () => startBox('single'), text: 'Vẽ hộp' }),
      h('button', { type: 'button', disabled: !ready || !!S.busy, title: 'Vẽ một hộp làm mẫu, SAM3 tìm mọi đối tượng giống vậy', onclick: () => startBox('similar'), text: 'Tìm tương tự' }),
    ));
    for (const w of S.warnings) out.push(h('div', { class: 'note', text: w }));
    if (S.propSource === 'batch' && S.propFrame === S.frame) {
      out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: 'Đề xuất này lấy từ lượt gợi ý cho cả job (tab Cả job).' }));
    }

    if (S.proposals.length && S.propFrame === S.frame) {
      const checked = S.proposals.filter((p) => p.checked).length;
      out.push(h('div', { class: 'row' },
        h('span', { class: 'grow muted', text: `${S.proposals.length} đề xuất · ${checked} đã chọn` }),
        h('button', { class: 'small', type: 'button', text: 'Chọn hết', onclick: () => { S.proposals.forEach((p) => { p.checked = true; }); render(); drawOverlay(); } }),
        h('button', { class: 'small', type: 'button', text: 'Bỏ mục có cờ', onclick: () => { S.proposals.forEach((p) => { if (p.flags.length) p.checked = false; }); render(); drawOverlay(); } }),
      ));
      out.push(h('ul', {}, S.proposals.map((p) => {
        const options = S.labels.filter((l) => !l.type || l.type === 'any' || l.type === p.type || l.id === p.label_id);
        const select = h('select', { class: 'grow', title: 'Đổi class', onchange: (ev) => { p.label_id = Number(ev.target.value); p.label_name = (labelById(p.label_id) || {}).name; p.userTouched = true; render(); drawOverlay(); } },
          options.map((l) => h('option', { value: String(l.id), text: l.name })));
        select.value = String(p.label_id);
        const li = h('li', { class: 'item' },
          h('div', { class: 'row' },
            h('input', { type: 'checkbox', checked: p.checked, title: 'Ghi nhãn này vào CVAT', onchange: (ev) => { p.checked = ev.target.checked; p.userTouched = true; render(); drawOverlay(); } }),
            h('span', { class: 'swatch', style: `background:${labelColor(p.label_id)}` }),
            select,
            h('span', { class: 'muted small-text', text: p.type === 'skeleton' ? skeletonText(p) : `${p.type} · ${Math.round(p.score * 100)}%` }),
            p.source === 'similar' && S.similar && S.similar.frame === S.frame ? h('button', { class: 'small', type: 'button', text: 'Loại trừ',
              title: 'Vật này bị nhận nhầm: dùng nó làm mẫu ÂM, SAM3 tìm lại và bỏ mọi vật giống nó', disabled: !!S.busy, onclick: () => excludeSimilar(p) }) : null,
          ),
          p.box_adjust ? h('div', { class: 'muted small-text', text: 'Hộp đã được chỉnh theo cách bạn hay sửa (tab Tự học).' }) : null,
          p.flags.length || p.occluded ? h('div', { class: 'row' },
            p.occluded ? h('span', { class: 'badge', text: 'occluded' }) : null,
            flagBadges(p.flags),
            p.flags.length ? h('button', { class: 'small', type: 'button', text: 'Tạo Issue', title: 'Gửi case này cho mentor qua Issue của CVAT',
              onclick: () => createIssue(p.flags[0].code, `${p.label_name}: ${p.flags.map((f) => f.message).join(' ')}`, p.bbox) }) : null,
          ) : null,
          p.flags.length ? h('div', { class: 'muted small-text', text: p.flags.map((f) => f.message).join(' ') }) : null,
        );
        hover(li, 'p' + p.pid);
        return li;
      })));
      out.push(h('div', { class: 'row' },
        h('button', { class: 'primary grow', type: 'button', disabled: !!S.busy || !checked, onclick: applyProposals, text: `Ghi ${checked} nhãn vào CVAT` }),
        h('button', { type: 'button', disabled: !!S.busy, onclick: () => {
          if (S.batch[S.frame]) { delete S.batch[S.frame]; persist('batch'); }
          clearProposals(); render();
        }, text: 'Bỏ đề xuất' }),
      ));
    } else if (ready) {
      out.push(h('p', { class: 'muted', style: 'margin:0', text: 'Chưa có đề xuất cho frame này. Tool chỉ gợi ý; bạn duyệt từng nhãn trước khi ghi.' }));
    }
    out.push(h('button', { type: 'button', disabled: !!S.busy, onclick: undoLast, text: 'Hoàn tác lần ghi gần nhất' }));
    return out;
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
        return [h('p', { class: 'muted', style: 'margin:0', text: `Đã đọc ${S.labels.length} label từ CVAT nhưng chưa nối được backend. Kiểm tra ở tab Cài đặt.` }), reload];
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
    if (ask && !window.confirm(ask)) return;
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
    out.push(h('button', { class: 'primary', type: 'button', disabled: busy || !S.resolved,
      onclick: runBatch, text: `Gợi ý cho ${jobFrames(r.from, r.to).length} frame${cands.length > 1 && picked.size < cands.length ? ` · ${picked.size} label` : ''}` }));
    const st = batchStats();
    if (!st.frames.length) return out;
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
        'Backend chưa cấu hình LLM: bản nháp dùng số liệu, guideline mẫu và bài học đã có. Class mới sẽ cần bạn tự viết định nghĩa.' }));
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
    const check = (label, key) => h('label', { class: 'check' },
      h('input', { type: 'checkbox', checked: !!cfg[key], onchange: (ev) => { cfg[key] = ev.target.checked; saveCfg(); } }), h('span', { text: label }));
    const hl = S.health;
    return [
      text('Địa chỉ backend SAM3', 'backendUrl', 'text', 'Máy này: http://127.0.0.1:8765. Colab/máy khác: dán địa chỉ https của tunnel.'),
      text('API key (nếu backend có đặt)', 'apiKey', 'password'),
      h('div', { class: 'row' },
        h('button', { type: 'button', disabled: !!S.busy, text: 'Kiểm tra kết nối', onclick: () => run('Đang kiểm tra backend…', async () => {
          if (!(await checkBackend())) return setStatus(S.health.error, 'error');
          if (!S.labels.length) await readJob();
          await resolveLabels();
          if (!S.labels.length) setStatus(`Backend kết nối tốt (${S.health.engine} trên ${S.health.device}), nhưng ${NO_LABELS}`, 'error');
          else setStatus(`Kết nối tốt: ${S.health.engine} trên ${S.health.device}, phiên bản ${S.health.version}. Đã đọc ${S.labels.length} label.`, 'ok');
        }) }),
        h('span', { class: 'muted', text: !hl ? '' : hl.ok ? `${hl.engine} · ${hl.device}` : 'chưa kết nối' })),
      check('Tự gợi ý khi chuyển frame', 'autoPropose'),
      check('Tải lại trang sau khi ghi để thấy nhãn mới', 'reloadAfterApply'),
      check('Hỏi lại trước khi ghi vào CVAT', 'confirmApply'),
      check('Đánh dấu nhãn do tool ghi là source = "semi-auto"', 'markSemiAuto'),
      h('div', { class: 'row' },
        h('span', { class: 'grow', text: 'Đổi nhãn semi-auto đã lưu thành manual' }),
        h('button', { type: 'button', disabled: !!S.busy, text: 'Frame này', onclick: () => convertSource('frame') }),
        h('button', { type: 'button', disabled: !!S.busy, text: 'Cả job', onclick: () => convertSource('job') })),
      h('label', { class: 'field' }, h('span', { text: 'Độ đậm vùng tô xem trước' }),
        h('input', { type: 'range', min: '0', max: '0.8', step: '0.05', value: String(cfg.opacity), oninput: (ev) => { cfg.opacity = Number(ev.target.value); saveCfg(); drawOverlay(); } })),
      text('CSS selector của ảnh nền canvas CVAT', 'canvasSelector', 'text', 'Chỉ đổi khi phần xem trước không hiện. Mặc định: #cvat_canvas_background'),
      h('p', { class: 'muted small-text', style: 'margin:0', text: `SAM3 CVAT Assist ${VERSION}. Phím tắt: Alt+G gợi ý, Alt+A ghi.` }),
    ];
  }

  function render() {
    host.style.display = S.jobId && !cfg.hidden ? 'block' : 'none';
    if (!S.jobId) return;
    renderHeader(); renderStatus();
    const open = !cfg.collapsed;
    els.meta.style.display = open ? '' : 'none';
    els.nav.style.display = open ? '' : 'none';
    els.body.style.display = open ? '' : 'none';
    if (!open) return;
    renderMeta(); renderNav();
    const active = root.activeElement;
    if (active && els.body.contains(active) && /^(INPUT|SELECT|TEXTAREA)$/.test(active.tagName) && active.type !== 'checkbox' && S.busy) return;
    const view = { propose: renderPropose, job: renderJob, guide: renderGuide, labels: renderLabels, qa: renderQa, learn: renderLearn, settings: renderSettings }[S.tab]();
    els.body.replaceChildren(...view.filter(Boolean));
  }

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
      S.frame = frame; S.seq++; S.box = null;
      clearProposals(); loadBatchFrame();
      if (!S.busy) setStatus('');
      render(); drawOverlay();
      clearTimeout(autoTimer);
      if (cfg.autoPropose && S.resolved) autoTimer = setTimeout(() => { if (!S.busy) propose(); }, 700);
    }
  }

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && S.box) { S.box = null; setStatus('Đã huỷ vẽ hộp.'); drawOverlay(); return; }
    if (!S.jobId || cfg.hidden || !ev.altKey || ev.ctrlKey || ev.metaKey) return;
    const key = ev.key.toLowerCase();
    if (key === 'g') { ev.preventDefault(); if (S.resolved && !S.busy) propose(); }
    if (key === 'a') { ev.preventDefault(); if (!S.busy) applyProposals(); }
  }, true);

  GM_registerMenuCommand('SAM3 Assist: hiện/ẩn panel', () => { cfg.hidden = !cfg.hidden; saveCfg(); render(); drawOverlay(); });

  document.body.append(overlayHost, host);
  applyPos();
  render();
  setInterval(watch, 400);
  watch();
  requestAnimationFrame(positionOverlay);
})();
