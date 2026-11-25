// ==UserScript==
// @name         SAM3 CVAT Assist
// @namespace    sam3-cvat-assist
// @version      0.9.9
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

  const VERSION = '0.9.9';
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
    guideSampleAuto: true,      // 0.9.4: tool tự tính số ảnh mẫu theo độ dài và độ đa dạng của job
    guideSendImages: false,
    importUseLlm: false,
    learnEnabled: true,      // ghi lại cách bạn xử lý đề xuất để tool tự học
    useLearned: true,        // áp ngưỡng / chỉnh hộp tool đã học
    qaFindMissing: false,    // Rà cả job: chạy thêm SAM3 để tìm đối tượng có thể bị sót
    trackEngine: 'classic',  // 'classic' | 'sam3_video'
    trackSampling: 'smart',  // 0.9.5: 'smart' (keyframe + kiểm tra giữa khoảng) | 'all' (mọi frame)
    trackThresholds: {},     // 0.9.6: task id -> { occ, out } (% diện tích toàn thân bị che)
    quality: 'auto',         // 0.9.6: 'auto' | 'light' | 'balanced' | 'quality' | 'max'
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
  const saveCfg = () => {
    // 0.9.4: profile chọn tay được nhớ theo task (trước đây nhớ chung: chọn "Tracking người" ở job video thì
    // job ảnh đường phố của task khác cũng dùng profile đó)
    if (cfg.profileTask) { cfg.profileByTask = cfg.profileByTask || {}; cfg.profileByTask[cfg.profileTask] = cfg.profileId; }
    GM_setValue('cfg', cfg);
  };

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
    LLM_DROP: 'Claude đề nghị bỏ',
    LLM_ADDED: 'Claude tìm thêm',
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

  // ---- 0.9.4: job CVAT 3D (point cloud) ----
  function is3d() { return !!(S.job && String(S.job.dimension || '').toLowerCase() === '3d'); }
  async function fetchCloud(number) {
    const blob = await cvat('GET', `/api/jobs/${S.jobId}/data?type=frame&quality=original&number=${number}`, undefined, true);
    const b64 = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(',')[1]);
      r.onerror = () => reject(r.error);
      r.readAsDataURL(blob);
    });
    return { frame: number, data_b64: b64 };
  }
  function cuboidLabels() { return S.labels.filter((l) => (l.type || 'any') === 'cuboid' || ((l.type || 'any') === 'any' && is3d())); }

  // 0.9.6: chỉnh của bạn cho job 3D, nhớ theo task: bảng cỡ, phạm vi, số điểm, prompt camera, hướng camera
  function l3() { cfg.lidar3d = cfg.lidar3d || {}; return cfg.lidar3d[(S.job && S.job.task_id) || 'local'] || {}; }
  function saveL3(patch) {
    cfg.lidar3d = cfg.lidar3d || {};
    const key = (S.job && S.job.task_id) || 'local';
    cfg.lidar3d[key] = Object.assign({}, l3(), patch);
    saveCfg();
  }
  function lidarBody(extra) {
    const m = l3();
    const lidar = {};
    for (const k of ['sizes', 'ranges', 'min_points_scale', 'prompts', 'max_base_gap_m', 'offset_deg', 'cameras']) if (m[k] !== undefined && m[k] !== null) lidar[k] = m[k];
    return Object.assign(baseBody(), { lidar }, extra || {});
  }
  const CAM_STEP = { light: 0, balanced: 4, quality: 1, max: 1 };     // dựng track: đối chiếu camera mỗi N frame
  async function fetchContext(number) {
    if (S.noContext === S.jobId || l3().camera === false) return '';
    try {
      const blob = await cvat('GET', `/api/jobs/${S.jobId}/data?type=context_image&quality=original&number=${number}`, undefined, true);
      if (!blob || !blob.size) return '';
      return await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(',')[1]);
        r.onerror = () => reject(r.error);
        r.readAsDataURL(blob);
      });
    } catch (e) {
      S.noContext = S.jobId;            // job không có ảnh camera: không hỏi lại mỗi frame
      return '';
    }
  }
  async function loadLidarCfg() {
    if (!S.health || !S.health.ok || !cuboidLabels().length) return;
    S.lidarCfg = await backend('POST', '/lidar/config', lidarBody());
  }
  function rememberCamera(cam) {
    if (!cam) return;
    S.lidarCamera = cam;
    if (cam.offset_source === 'estimated' && l3().offset_deg === undefined) saveL3({ offset_deg: cam.offset });
  }

  async function lidarFrame() {
    await run('Đang dựng cuboid từ point cloud của frame này…', async () => {
      const frame = S.frame;
      const [cloud, ctx] = await Promise.all([fetchCloud(frame), fetchContext(frame)]);
      // 0.9.8: debug chỉ khi không có model 3D (bản đồ cụm bị bỏ là của cách hình học); Claude cho từng frame nếu bạn tick
      const debug = !(S.health && S.health.lidar3d && S.health.lidar3d.model3d);
      const useLlm = llmOn() && l3().frameLlm === true;
      if (useLlm) setStatus('Đang dựng cuboid và chờ Claude xem ảnh camera của frame này…', 'busy');
      const res = await backend('POST', '/lidar/detect', lidarBody({ debug, use_llm: useLlm, frames: [Object.assign(cloud, { context_b64: ctx })] }), { timeout: 600000 });
      if (frame !== S.frame) return;
      const fr = res.frames[0] || { objects: [] };
      rememberCamera(res.camera);
      S.cuboids = { frame, list: fr.objects.map((o) => Object.assign(o, { wasChecked: o.checked !== false, checked: o.checked !== false })),
        warnings: res.warnings || [], bev: fr.bev, rejected: fr.rejected || [], stats: fr.stats || {}, info: fr.info, reasons: res.reasons || {} };
      S.cubSel = null;
      const shown = S.cuboids.list.filter((o) => !weakCuboid(o)).length;
      setStatus(`${fr.objects.length} cuboid đề xuất ở frame ${frame} (${shown} rõ, ${fr.objects.length - shown} yếu đang ẩn). `
        + 'Ghi xong kiểm tra trong cửa sổ 3D của CVAT (Top / Side / Front).', 'ok');
    });
  }
  function weakCuboid(o) { return !o.checked || (o.confidence !== undefined && o.confidence < 0.45); }

  async function applyCuboids() {
    const c = S.cuboids;
    const chosen = c.list.filter((o) => o.checked);
    if (!chosen.length) return setStatus('Chưa tick cuboid nào.', 'error');
    await run('Đang ghi cuboid vào CVAT…', async () => {
      await saveCvatUi();
      const shapes = chosen.map((o) => {
        const label = labelById(o.label_id) || { attributes: [] };
        return { type: 'cuboid', frame: c.frame, label_id: o.label_id, points: [...o.center, 0, 0, o.yaw, ...o.size, 0, 0, 0, 0, 0, 0, 0],
          occluded: false, outside: false, z_order: 0, rotation: 0, group: 0, attributes: attributeValues(label, null, false), elements: [] };
      });
      const res = await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=create`, { version: 0, tags: [], shapes, tracks: [] });
      pushUndo({ jobId: S.jobId, frame: c.frame, shapes: (res && res.shapes) || [], tracks: [], at: Date.now() });
      // tool nhớ vật bạn bỏ (thùng rác, nhà vệ sinh di động… bị đoán là xe) để lần sau bỏ tick sẵn
      const dropped = c.list.filter((o) => o.wasChecked && !o.checked).map((o) => ({ cls: o.cls, size: o.size }));
      const kept = c.list.filter((o) => !o.wasChecked && o.checked).map((o) => ({ cls: o.cls, size: o.size }));
      if (dropped.length || kept.length) { try { await backend('POST', '/lidar/feedback', lidarBody({ dropped, kept })); } catch (e) { /* bỏ qua */ } }
      S.cuboids = null;
      setStatus(`Đã ghi ${shapes.length} cuboid.${dropped.length ? ` Tool nhớ ${dropped.length} vật bạn bỏ.` : ''} Tải lại trang (F5) để thấy trong cửa sổ 3D.`, 'ok');
    });
  }

  async function lidarTrack() {
    const r = jobRange();
    const frames = jobFrames(r.from, r.to);
    const camStep = CAM_STEP[qualityLevel()] || 0;
    S.stopRequested = false; S.longTask = 'track';
    await run(`Đang dựng cuboid cho ${frames.length} frame…`, async () => {
      const detected = [];
      let warnings = [];
      const started = Date.now();
      for (let i = 0; i < frames.length; i += 2) {
        if (S.stopRequested) break;
        const part = await Promise.all(frames.slice(i, i + 2).map(async (f, k) => {
          const cloud = await fetchCloud(f);
          if (camStep && (i + k) % camStep === 0) cloud.context_b64 = await fetchContext(f);
          return cloud;
        }));
        const res = await backend('POST', '/lidar/detect', lidarBody({ frames: part }), { timeout: 600000 });
        rememberCamera(res.camera);
        for (const fr of res.frames) detected.push({ frame: fr.frame, objects: fr.objects });
        warnings = warnings.concat((res.warnings || []).filter((w) => !warnings.includes(w)));
        const per = (Date.now() - started) / detected.length / 1000;
        setStatus(`Đang dựng cuboid: ${detected.length}/${frames.length} frame. Còn khoảng ${Math.round(per * (frames.length - detected.length))} giây.`, 'busy');
      }
      if (!detected.length) return;
      setStatus('Đang nối track, khoá kích thước, chọn keyframe…', 'busy');
      const built = await backend('POST', '/lidar/track', lidarBody({ frames: detected }));
      S.tracks = (built.tracks || []).map((t) => Object.assign(t, { shapes: t.shapes.map((s) => Object.assign(s, { type: 'cuboid' })) }))
        .sort((a, b) => (b.checked - a.checked) || ((b.confidence || 0) - (a.confidence || 0)));
      S.trackMeta = { from: r.from, to: detected[detected.length - 1].frame, warnings: warnings.concat(built.warnings || []), title: 'Cuboid 3D', lidar: true };
      persist('tracks');
      const flagged = S.tracks.filter((t) => t.flags.length).length;
      notify('Dựng cuboid xong', `Job ${S.jobId}: ${S.tracks.length} track, ${flagged} track có cờ.`);
      setStatus(`${S.tracks.length} track cuboid, ${flagged} track có cờ cần xem. Bấm Ghi rồi kiểm tra trong cửa sổ 3D của CVAT.`, 'ok');
    });
    S.longTask = ''; render();
  }

  // ---------------------------- 0.9.7: luồng 3 bước cho job 3D (spec 3D v2 mục 5) ----------------------------
  function lidarBuckets(tracks) {
    const b = { high: [], review: [], noise: [] };
    for (const t of tracks) {
      const bucket = t.bucket || (t.checked === false ? 'suspected_noise' : (t.flags && t.flags.length ? 'needs_review' : 'high_confidence'));
      (bucket === 'high_confidence' ? b.high : bucket === 'suspected_noise' ? b.noise : b.review).push(t);
    }
    return b;
  }
  function trackNote(t) {
    // ghi chú tiếng Việt dễ hiểu thay cho nhãn cờ kỹ thuật ("Thuộc tính? ×2", "Class?")
    if (t.llm_reason) return t.llm_reason;
    const notes = (t.notes || []).slice(0, 1);
    const v = t.vision2d && t.vision2d.verdict;
    if (v === 'confirmed') notes.push(`Ảnh ${t.vision2d.camera}: thấy đúng loại vật`);
    else if (v === 'unknown') notes.push(`Ảnh ${t.vision2d.camera}: thấy vật khác loại — xem lại class`);
    else if (v === 'partial') notes.push(`Ảnh ${t.vision2d.camera}: chỉ thấy một phần`);
    else if (!v || v === 'no_image') notes.push('Chưa kiểm bằng ảnh camera');
    if (['trailer', 'construction vehicle'].includes(String(t.cls || '').toLowerCase()) && v !== 'confirmed') notes.push('Class dễ nhầm với vật cố định: cần xem');
    for (const f of t.flags || []) {
      if (f.code === 'UNCERTAIN_CLASS') notes.push((f.message || '').split(':')[0]);
      else if (f.code === 'LOW_VISIBILITY') notes.push('Ít điểm LiDAR — kích thước/hướng có thể sai');
      else if (f.code === 'ATTRIBUTE_CHECK') notes.push((f.message || '').split(':')[0]);
      else if (f.code === 'LEARNED') notes.push('Giống vật bạn đã bỏ trước đây');
    }
    if (t.vision2d && t.vision2d.verdict === 'none') notes.unshift(`Ảnh ${t.vision2d.camera}: khu chiếu không có xe / người — nghi rác`);
    return notes.slice(0, 2).join(' · ');
  }
  function renderLidar3Step(busy) {
    const ok = !!(S.health && S.health.ok);
    const r = jobRange();
    const nFrames = jobFrames(r.from, r.to).length;
    const cam = S.lidarCamera;
    const camText = cam && cam.cameras && cam.cameras.length
      ? `${cam.cameras.length} camera · ${({ estimated: `tự dò ${cam.offset}°`, panel: `đã lưu ${cam.offset}°`, calibration: 'khớp hiệu chỉnh', fitted: 'căn từ box mẫu', default: 'chưa dò được' })[cam.offset_source] || cam.offset_source}`
      : 'không có ảnh camera';
    const llmOk = llmOn();
    const llmUse = llmOk && l3().useLlm !== false;
    const out = [];
    // Bước 1 — Thiết lập nhanh. 0.9.8: chip Claude theo ô tick của bạn (trước đây theo việc app có API key)
    const llmChip = !llmOk ? ['mock', 'Claude AI: chưa có API key'] : llmUse ? ['ok', 'Claude AI: bật'] : ['mock', 'Claude AI: tắt (bạn đã tắt)'];
    out.push(h('div', { class: 'group', style: 'gap:6px' },
      h('h4', { text: 'Bước 1 · Thiết lập nhanh' }),
      h('div', { class: 'row small-text', style: 'flex-wrap:wrap;gap:4px 10px' },
        h('span', { class: 'hchip' + (ok ? ' ok' : ' bad'), text: `● ${nFrames} frame · ${camText}` }),
        h('span', { class: 'hchip ' + llmChip[0], text: llmChip[1] })),
      engines3d(),
      h('label', { class: 'check' },
        h('input', { type: 'checkbox', checked: llmUse, disabled: busy || !llmOk,
          onchange: (ev) => { saveL3({ useLlm: ev.target.checked }); render(); } }),
        h('span', { class: 'small-text', text: 'Dùng Claude AI hỗ trợ' })),
      llmOk ? h('p', { class: 'muted small-text', style: 'margin:0', text: 'Tự nhận diện và kiểm duyệt lại toàn bộ nhãn theo guideline.' })
        : h('p', { class: 'muted small-text', style: 'margin:0', text: 'Thêm API key Anthropic ở Cài đặt của app để bật Claude.' }),
      advanced3d(busy)));
    // Bước 2 — Chạy dựng & Kiểm duyệt: 1 nút chính, tiến độ phân pha, không ước lượng ngược gây hoang mang
    const st = S.lidarSession || {};
    const phase = st.phase;
    const prog = phase ? h('div', { class: 'qprog', style: 'margin-top:4px' },
      h('div', { class: 'muted small-text', text: phaseText(phase, st) }),
      h('div', { class: 'bar' }, h('i', { class: 'done', style: `width:${Math.min(100, st.pct || 0)}%` })),
      h('div', { class: 'row', style: 'margin-top:4px' },
        h('button', { class: 'small', type: 'button', text: 'Tạm dừng / Hủy', onclick: cancelLidarSession }))) : null;
    out.push(h('div', { class: 'group', style: 'gap:6px' },
      h('h4', { text: 'Bước 2 · Chạy dựng & Kiểm duyệt' }),
      h('button', { class: 'primary big', type: 'button', disabled: busy || !ok,
        onclick: lidarTrackSession, title: 'Chạy theo lô có lưu tiến độ: dừng giữa chừng vẫn giữ phần đã chạy' },
        h('span', { text: `Bắt đầu dựng Cuboid 3D (${nFrames} frame)` })),
      prog));
    // Bước 3 — Duyệt thông minh & Ghi
    if (S.tracks.length && S.trackMeta && S.trackMeta.lidar) {
      const b = lidarBuckets(S.tracks);
      const cur = S.trackFilter || 'review';
      const pill = (key, label, n) => h('button', { class: 'small' + (cur === key ? ' primary' : ''), type: 'button',
        text: `${label} (${n})`, onclick: () => { S.trackFilter = key; render(); } });
      out.push(h('div', { class: 'group', style: 'gap:6px' },
        h('h4', { text: 'Bước 3 · Duyệt & Ghi' }),
        h('div', { class: 'row', style: 'flex-wrap:wrap;gap:4px' },
          pill('review', 'Cần xem', b.review.length), pill('high', 'Tin cậy cao', b.high.length),
          pill('noise', 'Nghi rác', b.noise.length), pill('all', 'Tất cả', S.tracks.length)),
        h('div', { class: 'row', style: 'flex-wrap:wrap;gap:4px' },
          h('button', { class: 'small', type: 'button', text: `Chọn hết Tin cậy cao (${b.high.length})`,
            onclick: () => { b.high.forEach((t) => { t.checked = true; }); persist('tracks'); render(); } }),
          h('button', { class: 'small', type: 'button', text: `Bỏ chọn Nghi rác (${b.noise.length})`,
            onclick: () => { b.noise.forEach((t) => { t.checked = false; }); persist('tracks'); render(); } })),
        renderTrackList3d(busy, cur)));
    } else if (!phase) {
      out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: 'Chưa có kết quả: bấm "Bắt đầu dựng Cuboid 3D" ở bước 2.' }));
    }
    return out;
  }
  // 0.9.8: dòng cho biết tool đang dựng / kiểm bằng gì (backend báo qua /health -> lidar3d)
  // 0.9.9: gợi ý từng frame (perFrame) dùng được SAM3 khi chưa có RT-DETR: ghi rõ đang lọc bằng gì, hay đang bỏ qua và vì sao
  function engines3d(perFrame) {
    const e = (S.health && S.health.lidar3d) || null;
    if (!e) return null;
    const build = e.model3d ? 'Dựng cuboid: model 3D (CenterPoint)' : 'Dựng cuboid: hình học (chưa có model 3D)';
    const cam = perFrame ? S.lidarCamera : null;
    let check = 'Lọc rác bằng ảnh: chưa có bộ dò 2D', why = '';
    if (e.vision2d) check = 'Lọc rác bằng ảnh: RT-DETR';
    else if (!perFrame) check = 'Lọc rác bằng ảnh: chưa có bộ dò 2D';
    else if (cam && cam.engine === 'sam3') check = 'Lọc rác bằng ảnh: SAM3';
    else if (cam && cam.engine === 'skip') { check = 'Lọc rác bằng ảnh: đang bỏ qua'; why = cam.note || ''; }
    else if (e.sam3_mode === 'off') check = 'Lọc rác bằng ảnh: tắt (chưa có RT-DETR, SAM3 đã tắt trong Cài đặt)';
    else if (e.sam3_mode === 'always') check = 'Lọc rác bằng ảnh: SAM3 (luôn nạp)';
    else if (e.sam3_mode) check = 'Lọc rác bằng ảnh: SAM3 nếu đủ VRAM';
    return h('div', { class: 'muted small-text', title: why || 'Cài / chọn model ở trang Thành phần của app', text: `${build} · ${check}${why ? ' — ' + why : ''}` });
  }
  function phaseText(phase, st) {
    if (phase === 'geometry') return `Pha 1/3: Quét LiDAR & dựng Track (${st.done}/${st.total} frame)`;
    if (phase === 'vision') return `Pha 2/3: Đối chiếu camera (${st.done}/${st.total} track)`;
    if (phase === 'llm') return st.total ? `Pha 3/3: Claude nhận diện & kiểm duyệt (${st.done}/${st.total} track)` : 'Pha 3/3: Claude nhận diện & kiểm duyệt…';
    return 'Đang chạy…';
  }
  function advanced3d(busy) {
    // mục "Nâng cao" mặc định đóng kín: khoảng cách, ngưỡng, timeout, căn chỉnh từ box mẫu
    const open = !!S.adv3d;
    return h('details', { class: 'help', open, ontoggle: (ev) => { S.adv3d = ev.target.open; } },
      h('summary', { text: 'Nâng cao' }),
      h('div', { class: 'group', style: 'gap:6px; margin-top:6px' },
        h('div', { class: 'row small-text' },
          h('span', { text: 'Frame mỗi lô:' }),
          h('input', { type: 'number', min: 5, max: 40, value: String(l3().batch || 20), disabled: busy, style: 'width:4.5em',
            onchange: (ev) => saveL3({ batch: Math.max(5, Math.min(40, Number(ev.target.value) || 20)) }) }),
          h('span', { text: '· Timeout mỗi lô (giây):' }),
          h('input', { type: 'number', min: 60, max: 300, value: String(l3().timeout || 150), disabled: busy, style: 'width:4.5em',
            onchange: (ev) => saveL3({ timeout: Math.max(60, Math.min(300, Number(ev.target.value) || 150)) }) })),
        h('div', { class: 'row' },
          h('button', { class: 'small', type: 'button', disabled: busy || !ok2(), onclick: lidarDiagnose, text: 'Kiểm tra dữ liệu job 3D' }),
          h('button', { class: 'small', type: 'button', disabled: busy || !ok2(), onclick: lidarEvaluate, text: 'Tự chỉnh theo cuboid đã đánh' }),
          h('button', { class: 'small', type: 'button', disabled: busy, onclick: () => { S.tab = 'labels'; render(); }, text: 'Bảng cỡ, phạm vi…' })),
        renderDiag(), renderEval()));
  }
  function ok2() { return !!(S.health && S.health.ok); }
  function renderTrackList3d(busy, cur) {
    const b = lidarBuckets(S.tracks);
    const listed = cur === 'all' ? S.tracks : (cur === 'high' ? b.high : cur === 'noise' ? b.noise : b.review);
    const out = [];
    for (const w of (S.trackMeta && S.trackMeta.warnings) || []) out.push(h('div', { class: 'note', text: w }));
    if (!listed.length) out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: 'Rổ này trống.' }));
    out.push(h('ul', {}, listed.map((t) => {
      const note = trackNote(t);
      const li = h('li', { class: 'item' },
        h('div', { class: 'row' },
          h('input', { type: 'checkbox', checked: t.checked, title: 'Ghi track này', onchange: (ev) => { t.checked = ev.target.checked; persist('tracks'); render(); drawOverlay(); } }),
          h('span', { class: 'swatch', style: `background:${trackColor(t.tid)}` }),
          h('div', { class: 'grow', style: 'min-width:0' },
            h('div', { text: `#${t.tid} ${t.label_name} · frame ${t.start}–${t.end} · ${t.size ? t.size.join('×') + ' m' : ''}` }),
            note ? h('div', { class: 'muted small-text', text: note }) : null)),
        h('div', { class: 'row', style: 'flex-wrap:wrap;gap:4px' },
          h('button', { class: 'small link', type: 'button', text: 'Tới frame rõ nhất',
            title: 'Nhảy tới keyframe dày điểm nhất', onclick: () => goToFrame(t.ref_frame || t.start) }),
          h('button', { class: 'small link', type: 'button', text: 'Tới frame đầu', onclick: () => goToFrame(t.start) })));
      hover(li, 't' + t.tid);
      return li;
    })));
    // 0.9.7: nút ghi nằm ở thanh hành động ghim đáy (renderActions); ở đây chỉ có "Bỏ kết quả"
    out.push(h('div', { class: 'row' },
      h('button', { type: 'button', disabled: busy, onclick: clearTracks, text: 'Bỏ kết quả' })));
    return out;
  }

  // ---- phiên 3D theo lô (0.9.7): gọi /lidar/v2/session/*, lưu tiến độ, hủy an toàn, thử lại đúng lô lỗi ----
  async function lidarTrackSession() {
    const r = jobRange();
    const frames = jobFrames(r.from, r.to);
    const batch = Math.max(5, Math.min(40, l3().batch || 20));
    const timeout = Math.max(60000, Math.min(300000, (l3().timeout || 150) * 1000));
    const useLlm = l3().useLlm !== false && llmOn();
    S.stopRequested = false;
    S.lidarSession = { phase: 'geometry', done: 0, total: frames.length, pct: 0 };
    render();
    await run('Đang dựng cuboid 3D theo lô…', async () => {
      const st = S.lidarSession;
      try {
        // init: gửi lô frame đầu
        const initFrames = [];
        for (let i = 0; i < Math.min(batch, frames.length); i++) {
          const cloud = await fetchCloud(frames[i]);
          cloud.context_b64 = await fetchContext(frames[i]);
          initFrames.push(cloud);
        }
        const sess = await backend('POST', '/lidar/v2/session/init', lidarBody({ frames: initFrames }), { timeout });
        st.sid = sess.session_id;
        if (sess.camera_names && sess.camera_names.length) rememberCamera({ cameras: sess.camera_names, offset_source: 'calibration' });
        // geometry: các lô tiếp theo
        for (let i = batch; i < frames.length && !S.stopRequested; i += batch) {
          const part = [];
          for (const f of frames.slice(i, i + batch)) {
            const cloud = await fetchCloud(f);
            cloud.context_b64 = await fetchContext(f);
            part.push(cloud);
          }
          await backend('POST', `/lidar/v2/session/${st.sid}/frames`, lidarBody({ frames: part }), { timeout });
        }
        st.phase = 'geometry'; st.done = 0; st.total = frames.length;
        let remaining = 1;
        while (remaining > 0 && !S.stopRequested) {
          const g = await backend('POST', `/lidar/v2/session/${st.sid}/geometry`, lidarBody({ limit: batch }), { timeout });
          st.done = frames.length - (g.remaining || 0); st.pct = Math.round(100 * st.done / Math.max(1, frames.length));
          remaining = g.remaining || 0;
          setStatus(phaseText('geometry', st), 'busy');
        }
        if (S.stopRequested) { await backend('POST', `/lidar/v2/session/${st.sid}/cancel`, lidarBody({}), { timeout: 10000 }); }
        // vision: đối chiếu camera theo lô track. 0.9.8: lặp tới khi hết track (trước chỉ chạy 50 track đầu)
        const notes = [];
        st.phase = 'vision'; st.done = 0; st.pct = 0;
        for (let round = 0; round < 40 && !S.stopRequested; round++) {
          const v = await backend('POST', `/lidar/v2/session/${st.sid}/vision`, lidarBody({ limit: 30 }), { timeout });
          if (v.skipped) { notes.push(`Đối chiếu camera: ${v.skipped}`); break; }
          if (v.error) { notes.push(`Đối chiếu camera lỗi: ${v.error}`); break; }
          st.done = (v.done && v.done.vision) || 0; st.total = st.done + (v.remaining || 0);
          st.pct = Math.round(100 * st.done / Math.max(1, st.total));
          setStatus(phaseText('vision', st), 'busy'); render();
          if (!v.remaining) break;
        }
        // llm: Claude 2 pha (chỉ khi bạn tick "Dùng Claude AI hỗ trợ")
        if (useLlm && !S.stopRequested) {
          st.phase = 'llm'; st.done = 0; st.total = 0; st.pct = 20; render();     // một request: không đếm được từng track
          const l = await backend('POST', `/lidar/v2/session/${st.sid}/llm_batch`, lidarBody({ batch_size: 8, use_llm: true }), { timeout: 300000 });
          if (l.skipped) notes.push(`Claude: ${l.skipped}`);
          else if (l.error) notes.push(l.error);
          else if (l.buckets) setStatus(`Claude xong: ${l.buckets.needs_review} cần xem, ${l.buckets.suspected_noise} nghi rác`, 'busy');
          for (const e of l.errors || []) notes.push(e);
        }
        // result: lấy track + 3 rổ
        const res = await backend('POST', `/lidar/v2/session/${st.sid}/result`, lidarBody({}), { timeout });
        S.tracks = (res.tracks || []).map((t) => Object.assign(t, {
          shapes: t.shapes.map((s) => Object.assign(s, { type: 'cuboid' })),
          bucket: t.bucket || 'needs_review', checked: t.checked !== false && t.bucket !== 'suspected_noise',
        })).sort((a, b) => bucketOrder(a) - bucketOrder(b) || ((b.confidence || 0) - (a.confidence || 0)));
        S.trackFilter = S.tracks.some((t) => t.bucket === 'needs_review') ? 'review' : 'all';
        const cb = res.checked_by || {};
        if (!cb.camera && !cb.claude && S.tracks.length) {
          notes.unshift('Chưa có bước kiểm nào chạy được (camera, Claude): mọi track chỉ dựa vào kích thước nên đều ở rổ "Cần xem".');
        }
        S.trackMeta = { from: r.from, to: r.to, warnings: notes.concat(res.warnings || []), title: 'Cuboid 3D', lidar: true,
          checked_by: cb, engine: res.engine };
        persist('tracks');
        const b = lidarBuckets(S.tracks);
        notify('Dựng cuboid xong', `Job ${S.jobId}: ${b.review.length} cần xem, ${b.noise.length} nghi rác, ${b.high.length} tin cậy cao.`);
        setStatus(`${S.tracks.length} track: ${b.review.length} cần xem · ${b.noise.length} nghi rác · ${b.high.length} tin cậy cao. Duyệt ở bước 3 rồi ghi.`, 'ok');
        await backend('POST', `/lidar/v2/session/${st.sid}/close`, lidarBody({}), { timeout: 10000 }).catch(() => {});
      } catch (e) {
        // lỗi giữa chừng: giữ phiên để "Thử lại lô lỗi" chạy tiếp, không phải chạy lại từ đầu
        if (S.lidarSession && S.lidarSession.sid) {
          S.lidarSession.error = String(e && e.message || e).slice(0, 200);
          setStatus(`Lỗi giữa chừng — kết quả đã chạy vẫn giữ. ${S.lidarSession.error}`, 'error');
        } else { throw e; }
      }
    });
    render();
  }
  function bucketOrder(t) { return t.bucket === 'needs_review' ? 0 : t.bucket === 'suspected_noise' ? 1 : 2; }
  async function cancelLidarSession() {
    S.stopRequested = true;
    const st = S.lidarSession;
    if (st && st.sid) { try { await backend('POST', `/lidar/v2/session/${st.sid}/cancel`, lidarBody({}), { timeout: 10000 }); } catch (e) { /* bỏ qua */ } }
    setStatus('Đang dừng sau lô hiện tại… (kết quả đã chạy vẫn giữ)', 'busy');
  }

  // ---- 0.9.6: Kiểm tra dữ liệu job 3D (frame đang xem) ----
  async function lidarDiagnose() {
    await run('Đang kiểm tra dữ liệu point cloud và ảnh camera của frame này…', async () => {
      const frame = S.frame;
      const [cloud, ctx] = await Promise.all([fetchCloud(frame), fetchContext(frame)]);
      const res = await backend('POST', '/lidar/detect', lidarBody({ debug: true, frames: [Object.assign(cloud, { context_b64: ctx })] }), { timeout: 600000 });
      const fr = res.frames[0] || {};
      rememberCamera(res.camera);
      if (!S.lidarCfg) await loadLidarCfg();
      S.lidarDiag = { frame, info: fr.info || {}, stats: fr.stats || {}, reasons: res.reasons || {}, camera: res.camera, hasContext: !!ctx,
        found: (fr.objects || []).length, rule: res.rule || {}, missing: ((S.lidarCfg && S.lidarCfg.labels) || []).filter((l) => !l.in_table).map((l) => l.name) };
      S.cuboids = { frame, list: (fr.objects || []).map((o) => Object.assign(o, { wasChecked: o.checked !== false, checked: o.checked !== false })),
        warnings: res.warnings || [], bev: fr.bev, rejected: fr.rejected || [], stats: fr.stats || {}, info: fr.info, reasons: res.reasons || {} };
      setStatus('Đã kiểm tra dữ liệu. Xem kết quả ngay dưới các nút.', 'ok');
    });
  }
  function renderDiag() {
    const d = S.lidarDiag;
    if (!d) return null;
    const i = d.info, lines = [];
    lines.push(['Point cloud', `${(i.points || 0).toLocaleString()} điểm, xa nhất ${i.max_range_m || '?'} m, mặt đất cục bộ ${i.ground_cells || 0} ô`]);
    lines.push(['Hệ toạ độ', i.ego_hole ? 'quanh xe (gốc toạ độ là xe, có vùng trống quanh cảm biến)' : 'có thể là toạ độ thế giới: phạm vi gán tính từ gốc, có thể lệch']);
    const cam = d.camera;
    lines.push(['Ảnh camera', !d.hasContext ? 'job không có ảnh camera đi kèm (hoặc CVAT không trả về)'
      : cam && cam.cameras && cam.cameras.length ? `${cam.cameras.length} camera (${cam.cameras.slice(0, 6).join(', ')}); hướng: ${({ estimated: `tự dò, lệch ${cam.offset}°`, panel: `đã lưu, lệch ${cam.offset}°`, calibration: 'theo hiệu chỉnh', default: 'chưa dò được (cần frame có ≥ 2 xe rõ)' })[cam.offset_source] || cam.offset_source}`
        : (cam && cam.note) || 'không nhận ra tên camera']);
    const st = d.stats || {};
    const rej = Object.entries(st).filter(([, n]) => n).map(([k, n]) => `${n} ${d.reasons[k] || k}`);
    lines.push(['Cụm điểm', `${d.found} thành đề xuất${rej.length ? '; bỏ: ' + rej.join('; ') : ''}`]);
    if (d.missing.length) lines.push(['Label chưa có cỡ', `${d.missing.join(', ')}: thêm cỡ ở tab Label để tool gợi ý`]);
    const tips = [];
    if ((st.no_class || 0) > d.found * 2 && d.found) tips.push('Nhiều cụm không khớp class: kiểm tra bảng cỡ ở tab Label (chiều cao phải đúng).');
    if ((st.few_points || 0) > 3) tips.push('Nhiều cụm bị bỏ vì ít điểm: dùng "Tự chỉnh theo cuboid bạn đã đánh" để hạ ngưỡng.');
    if (!i.ego_hole) tips.push('Point cloud không xoay quanh xe: phạm vi 50 / 40 / 30 m có thể không đúng chỗ.');
    if (d.hasContext && cam && cam.offset_source === 'default') tips.push('Chưa dò được hướng camera: thử lại ở frame có vài xe gần.');
    return h('div', { class: 'group', style: 'gap:4px' },
      h('h4', { text: `Kiểm tra dữ liệu · frame ${d.frame}` }),
      h('table', { class: 'kv small-text' }, h('tbody', {}, lines.map(([k, v]) => h('tr', {}, h('th', { text: k, style: 'text-align:left;white-space:nowrap;padding-right:8px;vertical-align:top' }), h('td', { text: v }))))),
      tips.length ? h('ul', { class: 'small-text', style: 'margin:2px 0 0 16px;padding:0' }, tips.map((t) => h('li', { text: t }))) : null,
      h('button', { class: 'small link', type: 'button', text: 'Đóng', onclick: () => { S.lidarDiag = null; render(); } }));
  }

  // ---- 0.9.6: tự chỉnh theo cuboid bạn đã đánh trong job ----
  async function lidarEvaluate() {
    await run('Đang so tool với các cuboid bạn đã đánh…', async () => {
      const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
      const ids = new Set(cuboidLabels().map((l) => l.id));
      const truth = [];
      for (const sh of ann.shapes || []) if (sh.type === 'cuboid' && ids.has(sh.label_id)) truth.push({ frame: sh.frame, label_id: sh.label_id, points: sh.points });
      for (const tr of ann.tracks || []) {
        if (!ids.has(tr.label_id)) continue;
        for (const sh of tr.shapes || []) if (!sh.outside && (sh.type || 'cuboid') === 'cuboid') truth.push({ frame: sh.frame, label_id: tr.label_id, points: sh.points });
      }
      if (!truth.length) throw new Error('Chưa có cuboid nào bạn đã đánh trong job này. Đánh tay vài frame rồi bấm lại.');
      const count = {};
      for (const t of truth) count[t.frame] = (count[t.frame] || 0) + 1;
      const frames = Object.keys(count).map(Number).sort((a, b) => count[b] - count[a]).slice(0, 6).sort((a, b) => a - b);
      const clouds = [];
      for (const f of frames) clouds.push(await fetchCloud(f));
      const res = await backend('POST', '/lidar/evaluate', lidarBody({ frames: clouds, truth: truth.filter((t) => frames.includes(t.frame)) }), { timeout: 600000 });
      S.lidarEval = res;
      setStatus(`So trên ${res.frames} frame, ${res.truth} cuboid của bạn: tool tìm đúng ${Math.round((res.recall || 0) * 100)}%.`, 'ok');
    });
  }
  function applyTune() {
    const sug = (S.lidarEval && S.lidarEval.suggest) || {};
    const m = l3();
    const patch = {};
    if (sug.ranges) patch.ranges = Object.assign({}, m.ranges, sug.ranges);
    if (sug.min_points_scale) patch.min_points_scale = Object.assign({}, m.min_points_scale, sug.min_points_scale);
    if (sug.max_base_gap_m) patch.max_base_gap_m = sug.max_base_gap_m;
    if (sug.sizes) {
      const byCls = {};
      for (const l of (S.lidarCfg && S.lidarCfg.labels) || []) byCls[l.cls] = l.name;
      patch.sizes = Object.assign({}, m.sizes);
      for (const [cls, rows] of Object.entries(sug.sizes)) patch.sizes[byCls[cls] || cls] = rows;
    }
    saveL3(patch);
    S.lidarEval = null; S.lidarCfg = null;
    run('Đang cập nhật…', loadLidarCfg);
    setStatus('Đã áp dụng. Bấm "Gợi ý cuboid" để xem kết quả mới; về mặc định ở tab Label.', 'ok');
  }
  function renderEval() {
    const e = S.lidarEval;
    if (!e) return null;
    const sug = e.suggest || {};
    const lines = [];
    if (sug.min_points_scale) lines.push('Số điểm tối thiểu: ' + Object.entries(sug.min_points_scale).map(([k, v]) => `${k} ×${v}`).join(', '));
    if (sug.ranges) lines.push('Phạm vi: ' + Object.entries(sug.ranges).map(([k, v]) => `${k} ${v} m`).join(', ') + ' (vượt phạm vi của guideline: chỉ áp nếu guideline cho phép)');
    if (sug.max_base_gap_m) lines.push(`Đáy cách đất tối đa: ${sug.max_base_gap_m} m`);
    if (sug.sizes) lines.push('Bảng cỡ: thêm dòng theo cuboid của bạn cho ' + Object.keys(sug.sizes).join(', '));
    return h('div', { class: 'group', style: 'gap:4px' },
      h('h4', { text: `Tự chỉnh · tìm đúng ${Math.round((e.recall || 0) * 100)}% · đúng trong số tool đề xuất ${e.precision === null ? '–' : Math.round(e.precision * 100) + '%'}` }),
      h('table', { class: 'small-text' }, h('thead', {}, h('tr', {}, ['Class', 'Của bạn', 'Tool tìm đúng', 'Sai class', 'Thừa', 'Sót vì'].map((t) => h('th', { text: t })))),
        h('tbody', {}, (e.rows || []).map((r) => h('tr', {}, h('td', { text: r.class }), h('td', { text: String(r.truth) }), h('td', { text: String(r.found) }),
          h('td', { text: String(r.wrong_class) }), h('td', { text: String(r.extra) }),
          h('td', { text: Object.entries(r.missed || {}).map(([k, n]) => `${n} ${k === 'no_cluster' ? 'không có cụm điểm' : (e.reasons[k] || k)}`).join('; ') || '–' }))))),
      lines.length ? h('ul', { class: 'small-text', style: 'margin:2px 0 0 16px;padding:0' }, lines.map((t) => h('li', { text: t })))
        : h('div', { class: 'muted small-text', text: 'Không có gì cần chỉnh.' }),
      h('div', { class: 'row' },
        lines.length ? h('button', { class: 'primary small', type: 'button', text: 'Áp dụng', onclick: applyTune }) : null,
        h('button', { class: 'small', type: 'button', text: 'Đóng', onclick: () => { S.lidarEval = null; render(); } })));
  }

  // ---- 0.9.6: bản đồ nhỏ nhìn từ trên (BEV) ----
  const CLS_COLOR = { car: '#4363d8', truck: '#f58231', bus: '#911eb4', trailer: '#9a6324', 'construction vehicle': '#808000',
    pedestrian: '#e6194b', 'two wheels': '#3cb44b', barrier: '#f032e6' };
  function clsColor(cls) { return CLS_COLOR[cls] || PALETTE[(String(cls).length * 7) % PALETTE.length]; }
  function renderBev(c) {
    if (!c || !c.bev) return null;
    const size = 300, half = size / 2;
    const range = (c.bev.range || 50) + 2;
    const scale = half / range;
    const off = ((l3().offset_deg !== undefined ? l3().offset_deg : (S.lidarCamera && S.lidarCamera.offset)) || 0) * Math.PI / 180;
    const toScr = (x, y) => {           // trục xe: hướng trước lên trên, bên trái sang trái
      const vx = x * Math.cos(off) - y * Math.sin(off), vy = x * Math.sin(off) + y * Math.cos(off);
      return [half - vy * scale, half - vx * scale];
    };
    const corners = (o) => {
      const [l, w] = o.size, cs = Math.cos(o.yaw), sn = Math.sin(o.yaw);
      return [[l, w], [l, -w], [-l, -w], [-l, w]].map(([a, b]) => toScr(o.center[0] + (a / 2) * cs - (b / 2) * sn, o.center[1] + (a / 2) * sn + (b / 2) * cs));
    };
    const cv = h('canvas', { width: size * 2, height: size * 2, style: `width:${size}px;height:${size}px;max-width:100%;border-radius:8px;background:#11151c;cursor:pointer`,
      title: 'Nhìn từ trên: hướng trước của xe ở trên. Bấm vào một hộp để chọn đề xuất đó.', 'aria-label': 'Bản đồ nhìn từ trên' });
    const ctx = cv.getContext && cv.getContext('2d');
    if (ctx) {
      ctx.scale(2, 2);
      ctx.strokeStyle = '#2a3140'; ctx.lineWidth = 1;
      for (const rr of [10, 20, 30, 40, 50].filter((v) => v < range)) { ctx.beginPath(); ctx.arc(half, half, rr * scale, 0, 2 * Math.PI); ctx.stroke(); }
      ctx.fillStyle = '#5b6578'; ctx.font = '9px sans-serif';
      for (const rr of [10, 30, 50].filter((v) => v < range)) ctx.fillText(`${rr} m`, half + 2, half - rr * scale + 10);
      for (const [x, y, up] of c.bev.points) {
        const [sx, sy] = toScr(x, y);
        ctx.fillStyle = up ? '#aab4c4' : '#3b4352';
        ctx.fillRect(sx, sy, up ? 1.4 : 1, up ? 1.4 : 1);
      }
      if (S.cubShowRejected) for (const r of c.rejected || []) {
        const p = corners(r); ctx.strokeStyle = '#6b7280'; ctx.setLineDash([2, 2]); ctx.beginPath();
        p.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.stroke(); ctx.setLineDash([]);
      }
      for (const o of c.list) {
        const p = corners(o), sel = S.cubSel === o.id;
        ctx.strokeStyle = clsColor(o.cls); ctx.lineWidth = sel ? 2.5 : 1.4;
        ctx.setLineDash(o.checked ? [] : [3, 2]);
        ctx.beginPath(); p.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.stroke();
        if (sel) { ctx.fillStyle = clsColor(o.cls) + '55'; ctx.fill(); }
      }
      ctx.setLineDash([]);
      ctx.fillStyle = '#ffd166'; ctx.beginPath(); ctx.moveTo(half, half - 6); ctx.lineTo(half - 4, half + 4); ctx.lineTo(half + 4, half + 4); ctx.closePath(); ctx.fill();
    }
    cv.addEventListener('click', (ev) => {
      const rect = cv.getBoundingClientRect();
      const mx = (ev.clientX - rect.left) * size / rect.width, my = (ev.clientY - rect.top) * size / rect.height;
      let best = null, bd = 14;
      for (const o of c.list) {
        const [sx, sy] = toScr(o.center[0], o.center[1]);
        const d = Math.hypot(sx - mx, sy - my);
        if (d < bd) { bd = d; best = o; }
      }
      S.cubSel = best ? best.id : null;
      if (best && weakCuboid(best)) S.cubShowWeak = true;
      render();
      const el = best && document.querySelector(`[data-cub="${best.id}"]`);
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    });
    const legend = [...new Set(c.list.map((o) => o.cls))].map((k) => h('span', { class: 'small-text', style: 'display:inline-flex;align-items:center;gap:3px' },
      h('span', { class: 'swatch', style: `background:${clsColor(k)}` }), k));
    return h('div', { class: 'group', style: 'gap:4px;align-items:center' }, cv,
      h('div', { class: 'row', style: 'flex-wrap:wrap;gap:8px;justify-content:center' }, legend,
        h('label', { class: 'check small-text' }, h('input', { type: 'checkbox', checked: !!S.cubShowRejected, onchange: (ev) => { S.cubShowRejected = ev.target.checked; render(); } }),
          h('span', { text: `cụm bị bỏ (${(c.rejected || []).length})` }))));
  }

  function cuboidItem(o, labels) {
    const sel = h('select', { title: 'Đổi class', onchange: (ev) => { o.label_id = Number(ev.target.value); } }, labels.map((l) => h('option', { value: String(l.id), text: l.name })));
    sel.value = String(o.label_id);
    const conf = Math.round((o.confidence !== undefined ? o.confidence : o.score || 0) * 100);
    const cam = o.camera && o.camera.verdict;
    const camChip = cam === 'confirmed' ? h('span', { class: 'chip small ok', title: `Ảnh ${o.camera.camera}: thấy ${o.camera.prompt}`, text: 'camera ✓' })
      : cam === 'contradicted' ? h('span', { class: 'chip small bad', title: `Ảnh ${o.camera.camera}: giống ${o.camera.seen}`, text: `camera: ${o.camera.seen}` })
        : cam === 'unknown' ? h('span', { class: 'chip small', title: `Ảnh ${o.camera.camera}: không thấy rõ`, text: 'camera ?' }) : null;
    // 0.9.8: Claude đã chấm cuboid này (gợi ý từng frame)
    const llmChip = o.llm && o.llm.keep !== undefined && o.llm.keep !== null
      ? h('span', { class: 'chip small' + (o.llm.keep === false ? ' bad' : ' ok'), title: o.llm.reason || '', text: o.llm.keep === false ? 'Claude: bỏ' : 'Claude ✓' }) : null;
    const li = h('li', { class: 'item' + (S.cubSel === o.id ? ' hot' : ''), 'data-cub': String(o.id) },
      h('div', { class: 'row' },
        h('input', { type: 'checkbox', checked: o.checked, title: 'Ghi cuboid này', onchange: (ev) => { o.checked = ev.target.checked; render(); } }),
        h('span', { class: 'swatch', style: `background:${clsColor(o.cls)}` }),
        sel,
        h('span', { class: 'grow' }),
        h('span', { class: 'small-text', title: 'Độ tin của tool (cỡ khớp bảng, số điểm, ảnh camera)', text: `${conf}%` })),
      h('div', { class: 'row muted small-text', style: 'gap:6px;flex-wrap:wrap' },
        h('span', { text: `${o.size.map((v) => v.toFixed(1)).join(' × ')} m` }),
        h('span', { text: `${(o.distance !== undefined ? o.distance : Math.hypot(o.center[0], o.center[1])).toFixed(0)} m` }),
        h('span', { text: `${o.points} điểm` }), camChip, llmChip),
      o.flags.length ? h('div', { class: 'row fchips' }, flagBadges(o.flags, 'c' + o.id)) : null,
      o.flags.length ? flagDetails(o.flags, 'c' + o.id) : null);
    li.addEventListener('click', (ev) => { if (ev.target.tagName === 'LI' || ev.target.classList.contains('row')) { S.cubSel = o.id; render(); } });
    return li;
  }

  function renderLidar() {
    const busy = !!S.busy;
    const labels = cuboidLabels();
    const ok = !!(S.health && S.health.ok);
    const out = [];
    if (!labels.length) { out.push(h('div', { class: 'note', text: 'Task không có label kiểu cuboid.' })); return out; }
    if (!S.lidarCfg && ok && !S.lidarCfgLoading) {
      S.lidarCfgLoading = true;
      loadLidarCfg().then(() => render()).catch(() => {}).finally(() => { S.lidarCfgLoading = false; });
    }
    out.push(h('p', { class: 'muted small-text', style: 'margin:0', text: 'Tool tách mặt đất, gom cụm điểm, đoán class theo bảng cỡ (chiều cao phải khớp), '
      + 'đối chiếu ảnh camera nếu có. Ghi xong kiểm tra ở Top / Side / Front của CVAT.' }));
    out.push(h('div', { class: 'row' },
      h('button', { class: 'primary grow', type: 'button', disabled: busy || !ok, onclick: lidarFrame }, ico('spark', 's'), h('span', { text: 'Gợi ý cuboid frame này' })),
      h('button', { type: 'button', disabled: busy || !ok, text: 'Sang bước 2 (cả job)', onclick: () => { S.tab = 'job'; render(); } })));
    const cam = S.lidarCamera;
    out.push(h('label', { class: 'check small-text' },
      h('input', { type: 'checkbox', checked: l3().camera !== false, disabled: busy, onchange: (ev) => { saveL3({ camera: ev.target.checked }); S.noContext = null; render(); } }),
      h('span', { text: 'Đối chiếu ảnh camera' + (S.noContext === S.jobId ? ' (job này không có ảnh camera)'
        : cam && cam.cameras && cam.cameras.length ? ` · ${cam.cameras.length} camera, hướng ${({ estimated: 'tự dò', panel: 'đã lưu', calibration: 'theo hiệu chỉnh', default: 'chưa dò được' })[cam.offset_source] || ''}` : '') })));
    // 0.9.8 (D2): Claude cũng chấm được từng frame, không chỉ cả job
    const llmOk = llmOn();
    out.push(h('label', { class: 'check small-text', title: llmOk ? 'Claude xem ảnh camera có khung chiếu của từng cuboid: bỏ vật không phải xe / người, sửa class' : 'Cần API key Anthropic trong Cài đặt của app' },
      h('input', { type: 'checkbox', checked: llmOk && l3().frameLlm === true, disabled: busy || !llmOk, onchange: (ev) => { saveL3({ frameLlm: ev.target.checked }); render(); } }),
      h('span', { text: llmOk ? 'Dùng Claude AI cho frame này (chậm hơn vài giây, tốn lượt gọi)' : 'Dùng Claude AI (chưa có API key)' })));
    out.push(engines3d(true));
    const c = S.cuboids;
    if (c && c.frame === S.frame) {
      for (const w of c.warnings) out.push(h('div', { class: 'note', text: w }));
      out.push(renderBev(c));
      const strong = c.list.filter((o) => !weakCuboid(o)), weak = c.list.filter((o) => weakCuboid(o));
      const listed = S.cubShowWeak ? c.list : strong;
      const groups = {};
      for (const o of listed) (groups[o.cls] = groups[o.cls] || []).push(o);
      const checked = c.list.filter((o) => o.checked).length;
      out.push(h('div', { class: 'row' },
        h('span', { class: 'grow small-text', text: `${c.list.length} đề xuất · ${checked} đã tick` }),
        weak.length ? h('button', { class: 'small link', type: 'button', text: S.cubShowWeak ? 'Ẩn đề xuất yếu' : `Hiện ${weak.length} đề xuất yếu`,
          onclick: () => { S.cubShowWeak = !S.cubShowWeak; render(); } }) : null));
      for (const [cls, items] of Object.entries(groups).sort((a, b) => b[1].length - a[1].length)) {
        items.sort((a, b) => (b.checked - a.checked) || ((b.confidence || 0) - (a.confidence || 0)));
        const allOn = items.every((o) => o.checked);
        out.push(h('div', { class: 'row', style: 'margin-top:4px' },
          h('span', { class: 'swatch', style: `background:${clsColor(cls)}` }), h('b', { class: 'grow', text: `${cls} (${items.length})` }),
          h('button', { class: 'small link', type: 'button', text: allOn ? 'Bỏ tick cả nhóm' : 'Tick cả nhóm', onclick: () => { items.forEach((o) => { o.checked = !allOn; }); render(); } })));
        out.push(h('ul', {}, items.map((o) => cuboidItem(o, labels))));
      }
      if (!listed.length && weak.length) out.push(h('div', { class: 'muted small-text', text: 'Chỉ có đề xuất yếu ở frame này.' }));
      // 0.9.8 (C4): nhắc rằng bỏ tick là dạy tool
      out.push(h('div', { class: 'muted small-text', text: 'Bỏ tick thùng rác, container, nhà vệ sinh… rồi Ghi: tool nhớ cỡ của chúng và tự bỏ tick lần sau.' }));
      out.push(h('button', { class: 'primary', type: 'button', disabled: busy || !checked, onclick: applyCuboids, text: `Ghi ${checked} cuboid` }));
    }
    if (S.tracks.length && S.trackMeta && S.trackMeta.lidar) {
      out.push(h('div', { class: 'row' }, h('span', { class: 'grow muted small-text', text: `${S.tracks.length} track cuboid (frame ${S.trackMeta.from}–${S.trackMeta.to}). Duyệt ở tab Cả job rồi bấm Ghi.` }),
        h('button', { class: 'small', type: 'button', text: 'Sang tab Cả job', onclick: () => { S.tab = 'job'; render(); } })));
    }
    const remembered = S.lidarCfg ? S.lidarCfg.remembered : 0;
    out.push(h('div', { class: 'group', style: 'gap:6px' },
      h('h4', { text: 'Công cụ job 3D' }),
      h('div', { class: 'row', style: 'flex-wrap:wrap' },
        h('button', { class: 'small', type: 'button', disabled: busy || !ok, onclick: lidarDiagnose, text: 'Kiểm tra dữ liệu job 3D' }),
        h('button', { class: 'small', type: 'button', disabled: busy || !ok, onclick: lidarEvaluate, text: 'Tự chỉnh theo cuboid bạn đã đánh' }),
        h('button', { class: 'small', type: 'button', disabled: busy, onclick: () => { S.tab = 'labels'; render(); }, text: 'Bảng cỡ, phạm vi…' })),
      remembered ? h('div', { class: 'row small-text' }, h('span', { class: 'grow muted', text: `Tool nhớ ${remembered} kiểu vật bạn hay bỏ (bỏ tick sẵn khi gặp lại).` }),
        h('button', { class: 'small link', type: 'button', disabled: busy, text: 'Quên hết',
          onclick: () => run('Đang xoá…', async () => { await backend('POST', '/lidar/feedback', lidarBody({ clear: true })); await loadLidarCfg(); }) })) : null,
      renderDiag(), renderEval()));
    return out;
  }

  // ---- 0.9.6: tab Label của job 3D ----
  function rangeText(r) { return `${r[0]}–${r[1]}`; }
  function parseRange(text) {
    const m = String(text).replace(/,/g, '.').match(/(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)/);
    if (!m) return null;
    const a = Number(m[1]), b = Number(m[2]);
    return a > 0 && b >= a ? [a, b] : null;
  }
  function renderLabels3d() {
    const lc = S.lidarCfg;
    if (!lc) {
      if (S.health && S.health.ok && !S.lidarCfgLoading) { S.lidarCfgLoading = true; loadLidarCfg().then(() => render()).catch((e) => setStatus(e.message, 'error')).finally(() => { S.lidarCfgLoading = false; }); }
      return [h('p', { class: 'muted', text: S.health && S.health.ok ? 'Đang đọc cấu hình cuboid…' : 'Chưa nối được backend.' })];
    }
    const m = l3();
    const setSizes = (l, rows) => { saveL3({ sizes: Object.assign({}, m.sizes, { [l.name]: rows }) }); S.lidarCfg = null; render(); };
    const rows = lc.labels.map((l) => {
      const sizeRows = (l.sizes || []).map((row, ri) => h('div', { class: 'row small-text', style: 'gap:4px;flex-wrap:nowrap' },
        ['D', 'R', 'C'].map((k, di) => h('label', { style: 'display:inline-flex;align-items:center;gap:2px', title: ['Dài (m)', 'Rộng (m)', 'Cao (m): phải khớp, tool bỏ cụm sai chiều cao'][di] },
          h('span', { class: 'muted', text: k }),
          h('input', { type: 'text', value: rangeText(row[di]), style: 'width:5.2em', onchange: (ev) => {
            const v = parseRange(ev.target.value);
            if (!v) { ev.target.value = rangeText(row[di]); return setStatus('Nhập dạng 3.8–5.0', 'error'); }
            const next = l.sizes.map((r) => r.map((x) => x.slice()));
            next[ri][di] = v; setSizes(l, next);
          } }))),
        h('button', { class: 'x', type: 'button', text: '✕', title: 'Bỏ dòng cỡ này', onclick: () => setSizes(l, l.sizes.filter((_, k) => k !== ri)) })));
      const minSel = (() => {
        const sel = h('select', { title: 'Số điểm tối thiểu so với mặc định', onchange: (ev) => { saveL3({ min_points_scale: Object.assign({}, m.min_points_scale, { [l.cls]: Number(ev.target.value) }) }); S.lidarCfg = null; render(); } },
          [[0.5, '×0.5 (nhận cả vật thưa)'], [0.75, '×0.75'], [1, '×1 mặc định'], [1.5, '×1.5'], [2, '×2 (chặt)']].map(([v, t]) => h('option', { value: String(v), text: t })));
        sel.value = String([0.5, 0.75, 1, 1.5, 2].reduce((a, b) => (Math.abs(b - l.min_points_scale) < Math.abs(a - l.min_points_scale) ? b : a), 1));
        return sel;
      })();
      // mỗi label một thẻ: đọc từ trên xuống, không phải cuộn ngang
      return h('div', { class: 'group', style: 'gap:6px' },
        h('div', { class: 'row' }, h('b', { class: 'grow', text: l.name }),
          h('span', { class: 'muted small-text', text: l.in_table ? `class: ${l.cls}` : 'chưa có cỡ: thêm cỡ để tool gợi ý label này' })),
        h('div', { class: 'muted small-text', text: 'Cỡ (m): dài · rộng · cao' }),
        sizeRows,
        h('button', { class: 'small link', type: 'button', style: 'align-self:flex-start', text: '+ thêm cỡ', onclick: () => setSizes(l, (l.sizes || []).concat([[[1, 2], [0.5, 1], [0.8, 1.5]]])) }),
        h('div', { class: 'row small-text', style: 'flex-wrap:wrap;gap:8px' },
          h('label', { class: 'row', style: 'gap:4px' }, h('span', { text: 'Phạm vi' }),
            h('input', { type: 'number', min: '5', max: '150', step: '5', value: String(l.range), style: 'width:4.5em', title: 'Phạm vi gán (m), tính từ xe',
              onchange: (ev) => { saveL3({ ranges: Object.assign({}, m.ranges, { [l.cls]: Number(ev.target.value) }) }); S.lidarCfg = null; render(); } }), h('span', { text: 'm' })),
          h('label', { class: 'row', style: 'gap:4px' }, h('span', { text: 'Số điểm' }), minSel)),
        h('label', { class: 'row small-text', style: 'gap:4px' }, h('span', { text: 'Prompt camera' }),
          h('input', { class: 'grow', type: 'text', value: l.prompt, title: 'Prompt tiếng Anh để SAM3 tìm vật này trên ảnh camera',
            onchange: (ev) => { saveL3({ prompts: Object.assign({}, m.prompts, { [String(l.id)]: ev.target.value.trim() }) }); S.lidarCfg = null; } })));
    });
    const mp = lc.min_points_by_range || [];
    const mpText = mp.map(([d, n], i) => (i === mp.length - 1 ? `xa hơn: ${n}` : `≤ ${d} m: ${n}`)).join(' · ');
    return [
      h('p', { class: 'muted small-text', style: 'margin:0', text: `Job cuboid 3D. Bảng cỡ ${lc.sizes_source === 'guideline' ? 'lấy từ guideline' : 'mặc định (guideline 3D Cuboid V1)'}; `
        + 'sửa ô nào thì tool dùng số của bạn cho task này. Chiều cao phải khớp bảng; dài / rộng được nhỏ hơn (vật bị che) nhưng không lớn hơn.' }),
      ...rows,
      h('div', { class: 'small-text muted', text: `Số điểm tối thiểu mặc định: ${mpText} (người, xe hai bánh: một nửa).` }),
      h('label', { class: 'row small-text' }, h('span', { class: 'grow', text: 'Đáy cụm cách mặt đất tối đa (m): cao hơn là vật lơ lửng (biển, tán cây), bỏ' }),
        h('input', { type: 'number', min: '0.1', max: '2', step: '0.1', value: String(m.max_base_gap_m || lc.max_base_gap_m), style: 'width:4.5em',
          onchange: (ev) => { saveL3({ max_base_gap_m: Number(ev.target.value) }); S.lidarCfg = null; } })),
      m.offset_deg !== undefined ? h('div', { class: 'row small-text' }, h('span', { class: 'grow', text: `Hướng camera đã dò: lệch ${m.offset_deg}° so với trục point cloud.` }),
        h('button', { class: 'small link', type: 'button', text: 'Dò lại', onclick: () => { const n = Object.assign({}, l3()); delete n.offset_deg; cfg.lidar3d[(S.job && S.job.task_id) || 'local'] = n; saveCfg(); render(); } })) : null,
      h('div', { class: 'row' },
        h('button', { type: 'button', disabled: !!S.busy, text: 'Về mặc định của profile', onclick: () => { cfg.lidar3d[(S.job && S.job.task_id) || 'local'] = {}; saveCfg(); S.lidarCfg = null; render(); } })),
    ];
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
  function backend(method, path, body, opts) {
    const timeoutMs = (opts && opts.timeout) || 300000;
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: cfg.backendUrl.replace(/\/+$/, '') + path,
        headers: { 'Content-Type': 'application/json', 'X-API-Key': cfg.apiKey || '', 'X-Lang': cfg.lang || 'vi' },
        data: body === undefined ? undefined : JSON.stringify(body),
        timeout: timeoutMs,
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
        ontimeout: () => reject(Object.assign(new Error(I18N.withCode(timeoutMs > 300000 ? `Backend không phản hồi sau ${Math.round(timeoutMs / 60000)} phút.` : 'Backend không phản hồi sau 5 phút.', 'E_BACKEND_TIMEOUT')), { code: 'E_BACKEND_TIMEOUT' })),
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

  const baseBody = () => ({ profile_id: cfg.profileId, labels: labelPayload(), overrides: overridePayload(), use_learned: cfg.useLearned !== false, user: S.user || '', use_custom: !!cfg.useCustom,
    project_id: (S.job && S.job.project_id) || null, task_id: (S.job && S.job.task_id) || null, ignore_zones: zonesOf().map((z) => z.box),
    quality: qualityLevel() });
  // 0.9.6: mức cấu hình (giống chỉnh đồ hoạ trong game): Tự động chọn theo VRAM của máy
  const QUALITY = {
    light: { name: 'Nhẹ', step: 16, batch: 2, prefetch: false, note: 'Máy yếu / không GPU: ít lần chạy SAM3, 1 prompt mỗi label, nhả model phụ ngay' },
    balanced: { name: 'Cân bằng', step: 8, batch: 4, prefetch: true, note: '6–8 GB VRAM: theo dõi thông minh, 2 prompt mỗi label' },
    quality: { name: 'Chất lượng', step: 4, batch: 4, prefetch: true, note: '10–12 GB VRAM: kiểm tra dày hơn khi theo dõi, đủ prompt, tìm lại người bị mất' },
    max: { name: 'Tối đa', step: 0, batch: 4, prefetch: true, note: '16 GB VRAM trở lên: chạy SAM3 mọi frame, giữ mọi model trên GPU' },
  };
  function autoQuality() {
    const g = (S.health && S.health.gpu) || null;
    const total = (g && g.total_mb) || (S.health && S.health.vram && S.health.vram.total_mb) || 0;
    if (S.health && S.health.is_mock) return 'balanced';   // engine giả (demo, test): không tốn VRAM
    if (!total || (S.health && S.health.device === 'cpu')) return 'light';
    return total < 6000 ? 'light' : total < 9000 ? 'balanced' : total < 14000 ? 'quality' : 'max';
  }
  function qualityLevel() { return cfg.quality && cfg.quality !== 'auto' && QUALITY[cfg.quality] ? cfg.quality : autoQuality(); }
  // Vùng bỏ qua (gương, kính, quảng cáo, màn hình): nhớ theo task vì cùng một camera
  function zonesOf() { cfg.zones = cfg.zones || {}; return (S.job && cfg.zones[S.job.task_id]) || []; }
  // Người cần theo dõi: nhớ theo job
  function targetsOf() { return (GM_getValue('targets', {}) || {})[S.jobId] || []; }
  function saveTargets(list) {
    const all = GM_getValue('targets', {}) || {};
    if (list.length) all[S.jobId] = list; else delete all[S.jobId];
    GM_setValue('targets', all);
  }

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

  // 0.9.5: tên file của frame (tool học quy ước tên file, vd _MVL = camera gương trái)
  function frameName(f) {
    const m = S.jobMeta || {};
    const list = Array.isArray(m.frames) ? m.frames : [];
    const start = Number.isFinite(m.start_frame) ? m.start_frame : (S.job && Number.isFinite(S.job.start_frame) ? S.job.start_frame : 0);
    const item = list[f - start] || (list.length === 1 ? list[0] : null);
    return (item && item.name) || '';
  }
  // 0.9.5: Claude xem ảnh khi app có API key và bạn không tắt
  function llmOn() { return cfg.useLlm !== false && !!(S.health && S.health.vision_llm); }
  function proposeExtra(f) { return { use_llm: llmOn(), filename: frameName(f) }; }

  async function readJobMeta() {
    try { S.jobMeta = await cvat('GET', `/api/jobs/${S.jobId}/data/meta`); } catch (err) { S.jobMeta = null; }
  }

  function jobRange() {
    const start = S.job && Number.isFinite(S.job.start_frame) ? S.job.start_frame : 0;
    const stop = S.job && Number.isFinite(S.job.stop_frame) ? S.job.stop_frame : (S.frame || 0);
    // 0.9.4: khoá theo cả biên của job. Trước đây chuyển job khi thông tin job mới chưa tải xong thì khoảng frame
    // của job cũ (vd 0–12) bị giữ lại cho job mới (nút "Quét 13 ảnh" dù đặt 50).
    const key = `${S.jobId}:${start}:${stop}`;
    if (!S.range || S.range.job !== S.jobId || (S.range.key && S.range.key !== key)) S.range = { job: S.jobId, key, from: start, to: stop };
    else if (!S.range.key) S.range.key = key;
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
    const task = S.job && S.job.task_id;
    if (task && cfg.profileTask !== task) {
      const first = cfg.profileTask === undefined;      // lần đầu chạy 0.9.4: giữ lựa chọn cũ cho task đang mở
      cfg.profileTask = task;
      if (!first) cfg.profileId = (cfg.profileByTask || {})[task] || 'auto';
      saveCfg();
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

  // Tag ảnh gợi ý (profile của project): frame đã có tag đó thì không tick sẵn
  function tagSuggestions(list, ann, frame) {
    const have = new Set(((ann && ann.tags) || []).filter((t) => t.frame === frame).map((t) => t.label_id));
    return (list || []).map((t) => Object.assign({}, t, { values: Object.assign({}, t.attributes), toolValues: Object.assign({}, t.attributes),
      checked: !have.has(t.label_id), exists: have.has(t.label_id) }));
  }

  function clearProposals() {
    S.tags = []; S.flagFilter = null;
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
        res = await backend('POST', '/propose', Object.assign(baseBody(), { image_b64: fr.b64, existing }, proposeExtra(frame)));
      }
      if (seq !== S.seq || frame !== S.frame) return; // người dùng đã chuyển frame
      S.proposals = stamp(res.proposals || [], 'frame');
      S.tags = tagSuggestions(res.tags, ann, frame);
      S.existingIds[frame] = existing.filter((x) => x.id !== undefined).map((x) => x.id);
      S.warnings = res.warnings || [];
      S.propFrame = frame; S.imgW = res.width; S.imgH = res.height;
      S.qa = null; S.sel = null;
      S.lastAnn = ann;
      S.hints = neighborHints(ann, frame);
      bumpStats({ shown: S.proposals.length, job: { id: S.jobId, add: { shown: S.proposals.length } } });
      const flagged = S.proposals.filter((p) => p.flags.length).length;
      const took = from === 'prefetch' ? 'ngay (đã chạy sẵn)' : `trong ${res.seconds}s`;
      S.llm = res.llm || null;
      const ai = res.llm && res.llm.used ? ` Claude đã xem: sửa ${res.llm.changed}, đề nghị bỏ ${res.llm.dropped}, tìm thêm ${res.llm.added}.` : '';
      setStatus(`${S.proposals.length} đề xuất ${took}, ${flagged} cần xem kỹ.${ai} Duyệt rồi bấm "Ghi" (hoặc ${keyName(keyOf('applyNext'))}: ghi và sang frame tiếp).`, 'ok');
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
      setStatus(mode === 'zone' ? 'Kéo chuột khoanh vùng cần bỏ qua (gương, kính, biển quảng cáo, màn hình). Esc để huỷ.'
        : mode === 'target' ? 'Kéo chuột vẽ hộp quanh người cần theo dõi (chọn ở frame người đó rõ nhất). Esc để huỷ.'
          : 'Kéo chuột trên ảnh để vẽ một hộp quanh đối tượng. Esc để huỷ.', 'info');
      drawOverlay();
    });
  }

  async function finishBox(box, mode, negatives) {
    if (mode === 'target') {
      const list = targetsOf().slice();
      const used = new Set(list.map((t) => t.name));
      let n = 1; while (used.has(`P${n}`)) n += 1;
      const trackLabel = (trackLabels()[0] || {}).label_id || null;
      list.push({ name: `P${n}`, frame: S.frame, box: box.map((v) => Math.round(v * 10) / 10), label_id: trackLabel });
      saveTargets(list);
      setStatus(`Đã chọn P${n} ở frame ${S.frame}. Chọn thêm người khác, hoặc bấm "Theo dõi" để tool chỉ theo dõi những người đã chọn (cả trước và sau frame này).`, 'ok');
      render(); drawOverlay();
      return;
    }
    if (mode === 'zone') {
      cfg.zones = cfg.zones || {};
      const list = zonesOf().slice();
      list.push({ box: box.map((v) => Math.round(v)), frame: S.frame });
      cfg.zones[S.job.task_id] = list; saveCfg();
      setStatus(`Đã thêm vùng bỏ qua thứ ${list.length}. Mọi phát hiện có tâm nằm trong vùng này bị bỏ (cả gợi ý từng frame và theo dõi) cho mọi job của task.`, 'ok');
      clearProposals(); render(); drawOverlay();
      return;
    }
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
    for (const p of list) {
      p.source = source; p.orig_label_id = p.label_id; p.orig_label_name = p.label_name;
      // 0.9.5: giá trị tool gợi ý lúc đầu, để biết bạn sửa gì khi bấm Ghi (tool học theo đó)
      if (p.toolAttrs === undefined) { p.toolAttrs = Object.assign({}, p.attributes || {}); p.toolOccluded = !!p.occluded; p.toolChecked = p.checked !== false; }
    }
    return list;
  }

  // 0.9.5: chọn giá trị thuộc tính ngay trên panel (tool, Claude hoặc giá trị học được đã điền sẵn), khỏi sửa trong CVAT
  function attrEditor(p) {
    const label = labelById(p.label_id);
    const attrs = ((label && label.attributes) || []).filter((a) => (a.values || []).length > 1 || a.input_type === 'checkbox');
    if (!attrs.length) return null;
    p.attributes = p.attributes || {};
    const review = new Set(p.review_attributes || []);
    const ai = p.llm_attributes || {};
    return h('div', { class: 'attrs' }, attrs.map((a) => {
      const key = normName(a.name);
      const values = a.input_type === 'checkbox' ? ['false', 'true'] : a.values;
      const cur = p.attributes[key] !== undefined ? String(p.attributes[key]) : (a.default_value || values[0]);
      const auto = p.toolAttrs && p.toolAttrs[key] !== undefined;
      const sel = h('select', { class: 'attr' + (review.has(a.name) || !auto ? ' needs' : ''), title: ai[a.name] !== undefined ? 'Claude chọn' : auto ? 'Tool tự điền' : 'Bạn chọn',
        onchange: (ev) => { p.attributes[key] = ev.target.value; p.userTouched = true; render(); } },
      values.map((v) => h('option', { value: String(v), text: String(v) })));
      sel.value = cur;
      return h('label', { class: 'attr-row' }, h('span', { class: 'small-text muted', text: a.name + (ai[a.name] !== undefined ? ' ✦' : '') }), sel);
    }));
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
      auto_attributes: p.auto_attributes || null,
      points: p.type === 'skeleton' ? Object.fromEntries((p.elements || []).filter((e) => !e.outside).map((e) => [String(e.label_id), [e.x, e.y]])) : null,
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
    recordMemory(written, others, [], frameOf);
  }

  // 0.9.5: gửi giá trị bạn chốt (thuộc tính, tag, occluded, đề xuất bỏ) cho trí nhớ của project
  function attrsByName(labelId, values) {
    const l = labelById(labelId) || { attributes: [] };
    const out = {};
    for (const a of l.attributes || []) { const v = (values || {})[normName(a.name)]; if (v !== undefined && v !== null && v !== '') out[a.name] = String(v); }
    return out;
  }
  function whereOf(p) {
    if (!p.bbox || !S.imgW || !S.imgH) return '';
    const cx = (p.bbox[0] + p.bbox[2]) / 2 / S.imgW, cy = (p.bbox[1] + p.bbox[3]) / 2 / S.imgH;
    const hx = cx < 0.36 ? 'left' : cx > 0.64 ? 'right' : 'center', vy = cy < 0.36 ? 'top' : cy > 0.64 ? 'bottom' : 'middle';
    return `${vy}-${hx} of image`;
  }
  function recordMemory(written, others, tags, frameOf) {
    if (cfg.learnEnabled === false || !(S.resolved && S.resolved.profile_id)) return;
    const items = [];
    for (const p of written) {
      items.push({ label: p.label_name, kind: 'object', final: attrsByName(p.label_id, p.attributes), tool: attrsByName(p.orig_label_id || p.label_id, p.toolAttrs),
        occluded: !!p.occluded, tool_occluded: p.toolOccluded, where: whereOf(p), filename: frameName(frameOf(p)) });
    }
    for (const p of others) {
      if (!p.checked && p.toolChecked) items.push({ label: p.orig_label_name || p.label_name, kind: 'object', rejected: true, where: whereOf(p),
        reason: (p.flags || []).map((f) => FLAG_SHORT[f.code] || f.code).join(', ') });
    }
    for (const t of tags) {
      items.push({ label: t.label_name, kind: 'tag', final: attrsByName(t.label_id, t.values), tool: attrsByName(t.label_id, t.toolValues), filename: frameName(S.propFrame) });
    }
    if (!items.length) return;
    backend('POST', '/memory/record', { profile_id: S.resolved.profile_id, items }).then((r) => { S.memory = r.summary; }).catch(() => {});
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
      points: x.type === 'mask' ? (x.points || []).slice(-4) : x.points, elements: x.elements, attributes: x.attributes }));
    // 0.9.4: track (để học từ việc theo dõi) và tên thuộc tính (để biết bạn có đổi giá trị tool tự điền không)
    const tracks = (ann.tracks || []).map((t) => ({ id: t.id, label_id: t.label_id, shapes: (t.shapes || []).map((s) => ({ frame: s.frame, outside: s.outside, occluded: s.occluded })) }));
    const attrNames = {};
    for (const l of S.labels) for (const a of l.attributes || []) attrNames[String(a.id)] = a.name;
    const res = await backend('POST', '/learn/review', { job_id: S.jobId, shapes, tracks, attr_names: attrNames, user: S.user || '' });
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
    out.push(...renderMemory());
    out.push(...renderCustomModel());
    out.push(check('Ghi lại cách tôi xử lý đề xuất để tool học', 'learnEnabled'));
    out.push(check('Dùng điều tool đã học khi gợi ý (ngưỡng, chỉnh hộp)', 'useLearned', () => { clearProposals(); run('Đang cập nhật label…', resolveLabels); }));
    out.push(h('div', { class: 'row' },
      h('button', { type: 'button', disabled: !!S.busy || cfg.learnEnabled === false, text: 'Đối chiếu job này ngay',
        title: 'So các nhãn tool đã ghi với nhãn hiện tại (sau khi bạn sửa) rồi học lại. Tool cũng tự làm việc này mỗi lần mở job.',
        onclick: () => run('Đang đối chiếu…', () => reviewLearning(false)) })));
    if (!S.learn) { loadLearn(); return out; }
    const pid = S.resolved && S.resolved.profile_id;
    const trackPid = S.resolved && S.resolved.track && S.resolved.track.profile_id;
    const rows = (S.learn.labels || []).filter((l) => l.profile_id === pid || (trackPid && l.profile_id === trackPid && l.tracks));
    out.push(h('h2', { text: `Đã học cho profile đang dùng (${rows.length} label)` }));
    if (!rows.length) {
      out.push(h('p', { class: 'muted', style: 'margin:0', text: 'Chưa có dữ liệu. Ghi vài nhãn bằng tool, sửa chúng như bình thường trong CVAT, lưu lại, rồi mở lại job (hoặc bấm Đối chiếu).' }));
    }
    for (const l of rows) {
      const c = l.counts;
      const lines = [];
      if (l.threshold) lines.push(`Ngưỡng ${l.base_threshold ?? '?'} → ${l.threshold}: ${l.threshold_reason}`);
      if (l.box_adjust) lines.push(`Chỉnh hộp: ${l.box_reason}`);
      if (l.point_adjust) lines.push(`Chỉnh điểm skeleton: ${l.point_reason}`);
      if (l.attr_off) lines.push(`Thôi tự điền thuộc tính: ${l.attr_reason}`);
      if (l.tracks) lines.push(`Track tool đã ghi: giữ ${l.tracks.kept} · bạn sửa ${l.tracks.edited} · xoá ${l.tracks.deleted}`);
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
    const chosenTags = (S.tags || []).filter((t) => t.checked);
    if (!chosen.length && !chosenTags.length) return setStatus('Chưa tick đề xuất nào (đề xuất đang bị ẩn bởi bộ lọc không được ghi).', 'error');
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
      const tags = chosenTags.map((t) => ({ frame: S.propFrame, label_id: t.label_id, group: 0,
        attributes: attributeValues(labelById(t.label_id) || { attributes: [] }, t.values, false) }));
      const res = await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=create`, {
        version: 0, tags, shapes, tracks: [],
      });
      pushUndo({ jobId: S.jobId, frame: S.propFrame, shapes: (res && res.shapes) || [], tracks: [], tags: (res && res.tags) || [], at: Date.now() });
      const frame = S.propFrame;
      learnFromApply(chosen, (res && res.shapes) || [], S.proposals, () => frame);
      if (chosenTags.length) recordMemory([], [], chosenTags, () => frame);
      statsAfterApply(chosen, S.proposals);
      if (S.batch[frame]) { delete S.batch[frame]; persist('batch'); }
      clearProposals();
      const target = o.nextFrame !== undefined ? o.nextFrame : frame;
      if (o.nextFrame !== undefined) GM_setValue('autoNext', { jobId: S.jobId, frame: target, at: Date.now() });
      if (cfg.reloadAfterApply) {
        setStatus(`Đã ghi ${shapes.length} nhãn${tags.length ? `, ${tags.length} tag` : ''}. Đang tải lại trang${o.nextFrame !== undefined ? ` ở frame ${target}` : ''}…`, 'ok');
        await sleep(300);
        reloadAtFrame(target);
      } else {
        setStatus(`Đã ghi ${shapes.length} nhãn${tags.length ? `, ${tags.length} tag` : ''}.${o.nextFrame !== undefined ? '' : ' Tải lại trang (F5) để thấy chúng trên canvas.'}`, 'ok');
        if (o.nextFrame !== undefined) goToFrame(target);
      }
    });
  }

  async function undoLast() {
    const stack = undoStackOf(S.jobId);
    const u = stack[stack.length - 1];
    const nShapes = u && u.shapes ? u.shapes.length : 0;
    const nTracks = u && u.tracks ? u.tracks.length : 0;
    const nTags = u && u.tags ? u.tags.length : 0;
    if (!u || !(nShapes + nTracks + nTags)) {
      if (u) popUndo(S.jobId);
      return setStatus('Không có lần ghi nào của job này để hoàn tác.', 'error');
    }
    const what = [nShapes ? `${nShapes} nhãn` : '', nTracks ? `${nTracks} track` : '', nTags ? `${nTags} tag` : ''].filter(Boolean).join(', ');
    const more = stack.length > 1 ? ` (còn ${stack.length - 1} lần ghi trước đó có thể hoàn tác tiếp)` : '';
    if (!await uiConfirm(`Xoá ${what} mà tool đã ghi ở frame ${u.frame}${more}?`)) return;
    await run('Đang hoàn tác…', async () => {
      await saveCvatUi();
      for (let i = 0; i < Math.max(nShapes, nTracks, nTags, 1); i += 300) {
        await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=delete`, {
          version: 0, tags: (u.tags || []).slice(i, i + 300), shapes: (u.shapes || []).slice(i, i + 300), tracks: (u.tracks || []).slice(i, i + 300),
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
        const res = await backend('POST', '/propose', Object.assign(body, { image_b64: image.b64, existing: existingAt(ann, f) }, proposeExtra(f)));
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
      // 0.9.4: SAM3 Video chạy từng 2 frame một lượt (mỗi lượt có tối đa 10 phút) để lượt nào cũng xong sớm và báo tiến độ đều;
      // panel bỏ cuộc (hết giờ, bấm Dừng) thì báo backend dừng phiên ngay, không để GPU chạy tiếp lượt cũ.
      const cancelVideo = async () => {
        if (!video) return;
        try { await backend('POST', '/track/cancel', { session_id: video.session_id }); } catch (e) { /* bỏ qua */ }
      };
      // 0.9.6: kết quả từng frame được lưu lại (đứt giữa chừng thì lần sau chạy tiếp, không làm lại); mỗi lượt gửi có
      // mã riêng để panel hết giờ chờ thì báo backend dừng đúng lượt đó (không để GPU chạy tiếp việc không ai nhận).
      const q = QUALITY[qualityLevel()];
      const progKey = `trackprog:${S.jobId}`;
      const sig = JSON.stringify([r.from, r.to, (S.resolved.track && S.resolved.track.profile_id) || '', trackLabels().map((c) => `${c.label_id}:${(c.prompts || []).join('|')}`), zonesOf().length]);
      const prevProg = GM_getValue(progKey, null);
      const cache = new Map(prevProg && prevProg.sig === sig && !video ? prevProg.done : []);
      if (prevProg && prevProg.sig === sig && prevProg.size) size = prevProg.size;
      const resumed = cache.size;
      const persistProg = () => { try { GM_setValue(progKey, { sig, done: [...cache.entries()], size, at: Date.now() }); } catch (e) { /* đầy bộ nhớ: bỏ qua */ } };
      const sendDetect = async (list, timeout) => {
        const images = await Promise.all(list.map((f) => fetchFrame(f)));
        size = size || { w: images[0].w, h: images[0].h };
        const rid = `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
        try {
          return await backend('POST', '/track/detect', Object.assign({}, body, { request_id: rid, frames: list.map((f, k) => ({ frame: f, image_b64: images[k].b64 })) }), { timeout });
        } catch (err) {
          try { await backend('POST', '/track/cancel', { session_id: rid }); } catch (e) { /* bỏ qua */ }
          throw err;
        }
      };
      const detectList = async (list) => {
        const todo = list.filter((f) => !cache.has(f));
        for (let i = 0; i < todo.length; i += q.batch) {
          if (S.stopRequested) break;
          const part = todo.slice(i, i + q.batch);
          let res;
          try {
            res = await sendDetect(part, 300000);
          } catch (err) {
            if (err.code !== 'E_BACKEND_TIMEOUT' || S.stopRequested) throw err;
            // quá 5 phút: thử lại từng frame, mỗi frame tối đa 10 phút (thường do VRAM đầy, backend đã được báo dừng lượt cũ)
            setStatus('Một lượt chạy quá lâu, đang thử lại từng frame (tiến độ đã lưu)…', 'busy');
            res = { frames: [], warnings: [] };
            for (const f of part) {
              const one = await sendDetect([f], 600000);
              res.frames = res.frames.concat(one.frames);
            }
          }
          for (const fr of res.frames) cache.set(fr.frame, fr.dets);
          warnings = warnings.concat(res.warnings || []);
          persistProg();
        }
        return list.filter((f) => cache.has(f)).map((f) => ({ frame: f, dets: cache.get(f) }));
      };
      let sampled = null;
      const smart = !video && cfg.trackSampling !== 'all' && q.step > 0 && frames.length > 8;
      if (smart) {
        // 0.9.5: không chạy SAM3 trên mọi frame. Chạy keyframe cách đều, rồi kiểm tra ở giữa mỗi khoảng: chỗ nào nội suy
        // khớp với SAM3 thì giữ nội suy, chỗ nào lệch thì chia đôi và chạy tiếp tới từng frame.
        sampled = await smartTrackDetect(frames, detectList, (done, ran) => {
          const per = (Date.now() - started) / Math.max(ran - resumed, 1) / 1000;
          setStatus(`Đang tìm đối tượng: đã xong ${done}/${frames.length} frame (SAM3 chạy ${ran} frame, phần còn lại nội suy đã kiểm tra). `
            + `Khoảng ${Math.round(per)} giây mỗi lần chạy.${resumed ? ` Tiếp tục từ lần trước (${resumed} frame có sẵn).` : ''}`, 'busy');
        }, q.step);
        for (const d of sampled.detected) detected.push(d);
      } else if (!video) {
        for (let i = 0; i < frames.length; i += 16) {
          if (S.stopRequested) break;
          for (const d of await detectList(frames.slice(i, i + 16))) detected.push(d);
          const ran = Math.max(detected.length - resumed, 1);
          const per = (Date.now() - started) / ran / 1000;
          setStatus(`Đang tìm đối tượng: ${detected.length}/${frames.length} frame. Còn khoảng ${Math.round(per * (frames.length - detected.length))} giây.`, 'busy');
        }
      }
      for (let i = video ? 0 : frames.length; i < frames.length; i += 2) {
        if (S.stopRequested) { await cancelVideo(); break; }
        const part = frames.slice(i, i + 2);
        const images = await Promise.all(part.map((f) => fetchFrame(f)));
        size = size || { w: images[0].w, h: images[0].h };
        const payload = { frames: part.map((f, k) => ({ frame: f, image_b64: images[k].b64 })) };
        let res;
        if (video) {
          try {
            res = await backend('POST', '/track/video/frames', Object.assign({}, body, payload, { session_id: video.session_id }), { timeout: 600000 });
          } catch (err) {
            // Hỏng giữa chừng: ID của SAM3 Video và cách thường không nối được với nhau, nên làm lại từ đầu bằng cách thường.
            await cancelVideo();
            if (S.stopRequested) break;
            video = null;
            engineNote = ` SAM3 Video lỗi giữa chừng (${String(err.message || err).slice(0, 160)}), đã làm lại bằng cách thường.`;
            detected.length = 0;
            for (const d of await detectList(frames)) detected.push(d);
            break;
          }
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
      const buildBody = () => Object.assign({}, body, {
        frames: detected, width: size.w, height: size.h, start: r.from, stop: r.start === r.from && r.to === r.stop ? r.stop : last,
        targets: targetsOf(), thresholds: trackThresholds(),
      });
      let built = await backend('POST', '/track/build', buildBody());
      // 0.9.6: frame model mất người giữa hai đoạn của track: SAM3 tìm lại đúng chỗ dự đoán. Thấy thì tính % bị che,
      // không thấy mới là outside. (Mức Nhẹ bỏ qua bước này.)
      const rescue = (built.rescue || []).filter((x) => !S.stopRequested);
      if (rescue.length && qualityLevel() !== 'light' && !video) {
        const byFrame = new Map();
        for (const x of rescue) { if (!byFrame.has(x.frame)) byFrame.set(x.frame, []); byFrame.get(x.frame).push({ label_id: x.label_id, box: x.box }); }
        const list = [...byFrame.keys()];
        let found = 0;
        for (let i = 0; i < list.length && !S.stopRequested; i += 4) {
          setStatus(`Tìm lại người bị mất dấu: ${Math.min(i + 4, list.length)}/${list.length} frame…`, 'busy');
          const part = list.slice(i, i + 4);
          const images = await Promise.all(part.map((f) => fetchFrame(f)));
          const res = await backend('POST', '/track/rescue', Object.assign({}, body, {
            frames: part.map((f, k) => ({ frame: f, image_b64: images[k].b64, boxes: byFrame.get(f) })) }), { timeout: 600000 });
          for (const fr of res.frames || []) {
            if (!fr.dets.length) continue;
            found += fr.dets.length;
            const entry = detected.find((d) => d.frame === fr.frame);
            if (entry) entry.dets = entry.dets.concat(fr.dets); else detected.push({ frame: fr.frame, dets: fr.dets });
          }
        }
        if (found) {
          detected.sort((a, b) => a.frame - b.frame);
          setStatus('Đang nối lại ID sau khi tìm lại…', 'busy');
          built = await backend('POST', '/track/build', buildBody());
          warnings.push(`Tìm lại được người ở ${found} chỗ model bỏ sót (tính % bị che thay vì đặt outside).`);
        }
      }
      if (!S.stopRequested) GM_setValue(progKey, null);
      S.tracks = built.tracks || [];
      S.trackMeta = {
        from: r.from, to: last, width: size.w, height: size.h, existing,
        warnings: [...new Set(warnings.concat(built.warnings || []))],
        title: (S.resolved.track && S.resolved.track.title) || '',
        thresholds: built.thresholds || null, thresholdsSource: built.thresholds_source || '',
      };
      S.imgW = size.w; S.imgH = size.h;
      persist('tracks');
      drawOverlay();
      const flagged = S.tracks.filter((t) => t.flags.length).length;
      const stopped = S.stopRequested ? 'Đã dừng ở frame ' + last + '. ' : '';
      notify('Theo dõi đối tượng xong', `Job ${S.jobId}: ${S.tracks.length} track, ${flagged} track có cờ.`);
      const saved = sampled ? ` SAM3 chạy ${sampled.ran}/${frames.length} frame, ${frames.length - sampled.ran} frame nội suy đã kiểm tra.` : '';
      setStatus(`${stopped}${S.tracks.length} track, ${flagged} track có cờ cần xem${video ? ' (theo dõi bằng SAM3 Video)' : ''}. Tua qua các frame để kiểm tra rồi bấm Ghi.${saved}${engineNote}`, engineNote ? 'error' : 'ok');
    });
    S.longTask = '';
    render();
  }

  // ---- 0.9.5: chọn frame cần chạy SAM3 khi theo dõi ----
  function boxIou(a, b) {
    const iw = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])), ih = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
    const inter = iw * ih, u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter;
    return u > 0 ? inter / u : 0;
  }
  // Ghép phát hiện của hai frame theo label (tham lam theo IoU). null nếu không ghép được hết (có người mới, người mất).
  function matchDets(a, b, minIou) {
    if (a.length !== b.length) return null;
    const used = new Set(), pairs = [];
    for (const da of a) {
      let best = -1, bi = -1;
      b.forEach((db, j) => { if (!used.has(j) && db.label_id === da.label_id) { const v = boxIou(da.box, db.box); if (v > best) { best = v; bi = j; } } });
      if (bi < 0 || best < minIou) return null;
      used.add(bi); pairs.push([da, b[bi]]);
    }
    return pairs;
  }
  function lerpDets(pairs, u) {
    return pairs.map(([a, b]) => Object.assign({}, u < 0.5 ? a : b, {
      box: a.box.map((v, i) => Math.round((v + u * (b.box[i] - v)) * 100) / 100), score: Math.min(a.score, b.score), interp: true,
    }));
  }
  async function smartTrackDetect(frames, detect, progress, step = 8) {
    const res = new Map();
    let ran = 0;
    const runBatch = async (list) => {
      for (let i = 0; i < list.length; i += 4) {
        if (S.stopRequested) return;
        for (const fr of await detect(list.slice(i, i + 4))) { res.set(fr.frame, fr.dets); ran += 1; }
        progress(res.size, ran);
      }
    };
    const idx = [];
    for (let i = 0; i < frames.length; i += step) idx.push(i);
    if (idx[idx.length - 1] !== frames.length - 1) idx.push(frames.length - 1);
    await runBatch(idx.map((i) => frames[i]));
    let todo = [];
    for (let k = 0; k + 1 < idx.length; k++) if (idx[k + 1] - idx[k] > 1) todo.push([idx[k], idx[k + 1]]);
    const filled = new Map();
    while (todo.length && !S.stopRequested) {
      const mids = todo.map(([a, b]) => Math.floor((a + b) / 2));
      // khoảng dài: kiểm thêm một điểm ở 1/4 khoảng, để chuyển động giật (đi, quay người) không lọt qua một lần kiểm
      const quarters = todo.map(([a, b], k) => (b - a > 4 ? Math.floor((a + mids[k]) / 2) : null));
      await runBatch(mids.concat(quarters.filter((q) => q !== null)).map((i) => frames[i]).filter((f) => !res.has(f)));
      const next = [];
      todo.forEach(([a, b], k) => {
        const m = mids[k], q = quarters[k], fa = frames[a], fb = frames[b], fm = frames[m];
        const da = res.get(fa), db = res.get(fb), dm = res.get(fm);
        if (!da || !db || !dm) return;
        const pairs = matchDets(da, db, 0.2);
        const near = (i) => { const p = pairs && lerpDets(pairs, (i - a) / (b - a)); return p && matchDets(p, res.get(frames[i]) || [], 0.8); };
        // nội suy khớp chặt với SAM3 ở các điểm kiểm: giữ nội suy cho cả khoảng; lệch thì chia nhỏ tiếp
        if (pairs && near(m) && (q === null || near(q))) {
          for (let i = a + 1; i < b; i++) if (i !== m && !res.has(frames[i])) {
            const left = i < m ? matchDets(da, dm, 0.2) : matchDets(dm, db, 0.2);
            const lo = i < m ? a : m, hi = i < m ? m : b;
            if (left) filled.set(frames[i], lerpDets(left, (i - lo) / (hi - lo)));
          }
        } else {
          if (m - a > 1) next.push([a, m]);
          if (b - m > 1) next.push([m, b]);
        }
      });
      todo = next;
    }
    for (const [f, d] of filled) if (!res.has(f)) res.set(f, d);
    progress(res.size, ran);
    const detected = frames.filter((f) => res.has(f)).map((f) => ({ frame: f, dets: res.get(f) }));
    return { detected, ran };
  }

  // 0.9.6: ngưỡng % diện tích toàn thân bị che để đặt occluded / outside: theo profile, bạn sửa được (nhớ theo task)
  function trackThresholds() {
    const t = ((cfg.trackThresholds || {})[S.job && S.job.task_id]) || null;
    return t ? { occluded_hidden: t.occ / 100, outside_hidden: t.out / 100 } : {};
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
            type: s.type || 'rectangle', frame: s.frame, points: s.points, occluded: !!s.occluded, outside: !!s.outside,
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
      // 0.9.4: tự học cả từ track (lần đối chiếu sau: track bị xoá, đổi class hay bị sửa keyframe)
      const made = (res && res.tracks) || [];
      const trackProfile = S.resolved && S.resolved.track && S.resolved.track.profile_id;
      if (cfg.learnEnabled !== false && trackProfile && made.length === chosen.length) {
        try {
          await backend('POST', '/learn/events', { user: S.user || '', events: chosen.map((t, i) => ({
            kind: 'applied', profile_id: trackProfile, job_id: S.jobId, frame: t.start, label_id: t.label_id, label_name: t.label_name,
            type: 'track', score: t.score, bbox: t.shapes[0].points, cvat_id: made[i].id, source: 'track', track_shapes: t.shapes.length,
          })) });
        } catch (err) { /* tự học không được làm hỏng việc ghi */ }
      }
      S.tracks = S.tracks.filter((t) => !t.checked);
      persist('tracks');
      setStatus(`Đã ghi ${tracks.length} track.${cfg.reloadAfterApply ? ' Đang tải lại trang…' : ' Tải lại trang (F5) để thấy chúng.'}`, 'ok');
      if (cfg.reloadAfterApply) { await sleep(500); reloadAtFrame(S.frame); }
    });
  }

  // ---------------------------------------------------- soạn guideline ----
  // Chọn ảnh mẫu rải đều cả khoảng frame; vài ảnh đầu (chặng khám phá) cũng rải đều để đa dạng.
  function sampleFrames(from, to, count) {
    // Có phân tích frame: lấy các frame ở đầu mỗi đoạn thay đổi rõ (đa dạng hơn rải đều), thiếu thì bù bằng rải đều
    const smart = S.smart && S.smart.job === S.jobId ? (S.smart.keyframes || []).filter((f) => f >= from && f <= to) : [];
    if (smart.length >= Math.min(count, 8)) {
      const step = smart.length / Math.min(count, smart.length);
      const picked = [...new Set(Array.from({ length: Math.min(count, smart.length) }, (_, i) => smart[Math.floor(i * step)]))];
      if (picked.length < count) {
        for (const f of sampleFrames.uniform(from, to, count)) { if (picked.length >= count) break; if (!picked.includes(f)) picked.push(f); }
      }
      return picked;
    }
    return sampleFrames.uniform(from, to, count);
  }
  sampleFrames.uniform = function uniformFrames(from, to, count) {
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

  // Số ảnh mẫu tự tính: job dài thì nhiều hơn (tăng theo căn bậc hai), có phân tích frame thì theo số cảnh khác nhau.
  function autoSample(r) {
    const total = Math.max(0, r.to - r.from + 1);
    const smart = S.smart && S.smart.job === S.jobId ? S.smart : null;
    const scenes = smart ? (smart.keyframes || []).filter((f) => f >= r.from && f <= r.to).length : 0;
    let n = Math.ceil(Math.sqrt(total) * 2.5);
    if (scenes) n = Math.max(Math.min(n, scenes * 2), Math.min(scenes, 80));
    n = Math.min(total, Math.max(Math.min(12, total), Math.min(80, n)));
    const why = scenes ? `job có ${total} frame, ${scenes} đoạn thay đổi rõ` : `job có ${total} frame`;
    return { n, why };
  }
  function sampleCount(r) {
    return cfg.guideSampleAuto !== false ? autoSample(r).n : Math.min(Number(cfg.guideSample) || 40, Math.max(0, r.to - r.from + 1));
  }

  async function runGuideline() {
    const r = jobRange();
    const frames = sampleFrames(r.from, r.to, sampleCount(r));
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
    const out = [h('h2', { text: 'Nạp guideline (chỉ văn bản, checklist)' }),
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
    for (const z of S.job ? zonesOf() : []) {      // vùng bỏ qua: gạch chéo xám
      const [x1, y1, x2, y2] = z.box;
      overlaySvg.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1, stroke: '#9aa3b2', 'stroke-width': 2,
        'stroke-dasharray': '8 5', fill: '#9aa3b2', 'fill-opacity': 0.18 }, base)));
    }
    for (const t of S.tab === 'job' ? targetsOf().filter((x) => x.frame === S.frame) : []) {   // người cần theo dõi
      const [x1, y1, x2, y2] = t.box;
      overlaySvg.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1, stroke: '#ffb000', 'stroke-width': 3, fill: 'none' }, base)));
      const tx = svgEl('text', { x: x1 + 4, y: y1 + 18, fill: '#ffb000', 'font-size': 16, 'font-weight': 700 });
      tx.textContent = t.name;
      overlaySvg.append(tx);
    }
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
      box-shadow: 0 6px 24px rgba(0,0,0,.18); display: flex; flex-direction: column; max-height: var(--panel-h, calc(100vh - 96px)); position: relative; }
    .grip-w { position: absolute; left: -4px; top: 0; bottom: 0; width: 8px; cursor: ew-resize; z-index: 3; }
    .grip-h { position: absolute; left: 0; right: 0; bottom: -4px; height: 8px; cursor: ns-resize; z-index: 3; }
    .grip-w:hover, .grip-h:hover { background: color-mix(in srgb, var(--accent) 25%, transparent); }
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
    .body { padding: 10px; overflow: auto; min-height: 0; flex: 1 1 auto; display: flex; flex-direction: column; gap: var(--gap); }
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
    table.profiles { table-layout: fixed; width: 100%; } table.profiles td:first-child { overflow-wrap: anywhere; } table.profiles td:last-child { width: 96px; }
    .attrs { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 4px 8px; margin-top: 4px; }
    .attr-row { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    select.attr { font-size: 12px; padding: 2px 4px; min-width: 0; }
    select.attr.needs { border-color: #d9a400; background: rgba(217, 164, 0, .12); }
    .status { padding: 6px 10px; border-top: 1px solid var(--line); min-height: 30px; flex-shrink: 0; max-height: 7.5em; overflow: auto; overflow-wrap: anywhere; }
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
    .qprog .bar { height: 6px; border-radius: 3px; background: var(--line); display: flex; overflow: hidden; }
    .qprog .bar i { display: block; height: 100%; } .qprog .bar i.done { background: #2e9d5b; } .qprog .bar i.skip { background: #c9cfdb; }
    li.q .qmark { width: 18px; height: 18px; border-radius: 9px; flex: none; display: inline-flex; align-items: center; justify-content: center;
      font-size: 11px; font-weight: 700; color: #fff; background: #d9a400; }
    li.q .qmark.done { background: #2e9d5b; } li.q .qmark.skip { background: #9aa3b2; }
    li.q.done, li.q.skip { padding-top: 4px; padding-bottom: 4px; }
    li.q.flash { outline: 2px solid var(--accent); }
    .qopts { display: flex; flex-direction: column; gap: 4px; }
    .qopt { text-align: left; display: flex; flex-direction: column; gap: 2px; padding: 6px 8px; }
    .qopt small { color: var(--muted); font-weight: 400; white-space: normal; }
    .qopt.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); }
    .chip .x { border: 0; background: none; padding: 0 2px; font-size: 11px; color: var(--muted); }
    .chip.small { padding: 0 6px; border-radius: 9px; font-size: 11px; border: 1px solid var(--line); }
    .chip.ok { color: #2e9d5b; border-color: #2e9d5b66; } .chip.bad { color: var(--err); border-color: currentColor; }
    table.kv th, table.kv td { border: 0; padding: 2px 0; font-weight: normal; } table.kv th { color: var(--muted); }
    li.item.hot { outline: 2px solid var(--accent, #4a90d9); outline-offset: -2px; }
    .chip .link { border: 0; background: none; padding: 0; color: inherit; font-size: 12px; }
    .fchips { gap: 4px; flex-wrap: wrap; }
    .fchip { display: inline-flex; align-items: center; gap: 4px; padding: 1px 8px 1px 3px; border-radius: 10px; font-size: 11.5px;
      border: 1px solid #e3b341; background: #fff6dc; color: #6b4b00; line-height: 18px; }
    .fchip .fi { width: 14px; height: 14px; border-radius: 7px; background: #d9a400; color: #fff; font-size: 10px; font-weight: 700;
      display: inline-flex; align-items: center; justify-content: center; }
    .fchip.on { background: #ffe9a8; }
    .fdetail { border-left: 3px solid #e3b341; padding: 2px 8px; color: var(--muted); display: flex; flex-direction: column; gap: 2px; }
    button.small.issue { padding: 1px 8px; font-size: 11.5px; }
    button.small.on { border-color: var(--accent); color: var(--accent); }
    details.help > summary { cursor: pointer; color: var(--muted); font-size: 12px; list-style: none; }
    details.help > summary::before { content: "ⓘ "; }
    details.help[open] > summary { margin-bottom: 4px; }
    .tagfs { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 6px; margin-top: 6px; }
    .tagf { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--muted); }
    .tagf.need select { border-color: #d9a400; box-shadow: 0 0 0 1px #d9a40055; }
    .prow { display: flex; align-items: flex-start; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--line); }
    .prow:last-child { border-bottom: 0; }
    .prow .dot { width: 8px; height: 8px; border-radius: 4px; margin-top: 6px; flex: none; background: #2e9d5b; }
    .prow .dot.warn { background: #d9a400; } .prow .dot.bad { background: #d64545; }
    .prow details summary { cursor: pointer; color: var(--muted); font-size: 12px; }
    .prow details ul { margin: 4px 0 0; padding-left: 16px; font-size: 12px; }
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
  // 0.9.4: kéo mép trái đổi độ rộng, mép dưới đổi chiều cao; bấm đúp về mặc định
  const gripW = h('div', { class: 'grip-w', title: 'Kéo để đổi độ rộng panel (bấm đúp: về mặc định)' });
  const gripH = h('div', { class: 'grip-h', title: 'Kéo để đổi chiều cao panel (bấm đúp: về mặc định)' });
  root.append(h('div', { class: 'panel' }, gripW, gripH, headerEl, els.meta, els.metaNote, els.nav, els.body, els.actions, els.status));
  function dragResize(ev, axis) {
    ev.preventDefault(); ev.stopPropagation();
    const r = host.getBoundingClientRect();
    const scale = Math.max(1, Math.min(1.6, Number(cfg.fontScale) || 1));
    const move = (e) => {
      if (axis === 'w') {
        const right = r.right;
        const w = Math.max(300, Math.min(window.innerWidth - 40, right - e.clientX));
        cfg.panelWidth = Math.round(w / scale);
        if (cfg.pos) { cfg.pos = [Math.max(0, right - w), cfg.pos[1]]; applyPos(); } else host.style.right = `${Math.max(0, window.innerWidth - right)}px`;
      } else {
        cfg.panelHeight = Math.max(260, Math.min(window.innerHeight - r.top - 8, e.clientY - r.top));
      }
      applyLook();
    };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); saveCfg(); };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }
  gripW.addEventListener('mousedown', (ev) => dragResize(ev, 'w'));
  gripH.addEventListener('mousedown', (ev) => dragResize(ev, 'h'));
  gripW.addEventListener('dblclick', () => { cfg.panelWidth = 380; saveCfg(); applyLook(); });
  gripH.addEventListener('dblclick', () => { cfg.panelHeight = null; saveCfg(); applyLook(); });
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
    // 0.9.7: thu gọn panel ở job 3D thành thanh nhỏ: hiện tiến độ hoặc số track chờ duyệt (spec 5.4)
    if (cfg.collapsed && is3d()) {
      const st = S.lidarSession;
      if (st && st.phase && (st.pct || 0) < 100) {
        els.chip.textContent = translateText(phaseText(st.phase, st));
      } else if (S.tracks.length) {
        const b = lidarBuckets(S.tracks);
        els.chip.textContent = translateText(`${S.tracks.length} track · ${b.review.length} cần xem`);
      }
    }
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

  // Tag của ảnh: mỗi thuộc tính một ô chọn; giá trị tool tự đoán có dấu ✓ nhỏ, giá trị bạn cần chọn tô vàng
  function renderTags() {
    return h('div', { class: 'group tags', style: 'gap:6px' }, h('h4', { text: 'Tag của ảnh' }), ...S.tags.map((t) => {
      const label = labelById(t.label_id) || { attributes: [] };
      const fields = (label.attributes || []).map((a) => {
        const key = normName(a.name);
        const auto = (t.auto || []).includes(a.name);
        const sel = h('select', { title: (t.notes && t.notes[a.name]) || (auto ? 'Tool tự đoán từ ảnh: kiểm lại' : 'Bạn chọn'),
          onchange: (ev) => { t.values[key] = ev.target.value; t.checked = true; t.touched = (t.touched || []).concat(a.name); render(); } },
        (a.values || []).map((v) => h('option', { value: v, text: v })));
        sel.value = t.values[key] !== undefined ? t.values[key] : (a.default_value || (a.values || [])[0] || '');
        const need = !auto && !(t.touched || []).includes(a.name);
        return h('label', { class: 'tagf' + (need ? ' need' : '') }, h('span', { text: a.name + (auto ? ' ✓' : '') }), sel);
      });
      return h('div', { class: 'item' }, h('div', { class: 'row' },
        h('input', { type: 'checkbox', checked: t.checked, title: 'Ghi tag này vào CVAT', onchange: (ev) => { t.checked = ev.target.checked; render(); } }),
        h('b', { class: 'grow', text: t.label_name }),
        t.exists ? h('span', { class: 'muted small-text', text: 'frame đã có tag này' }) : null),
      fields.length ? h('div', { class: 'tagfs' }, fields) : null);
    }));
  }

  // 0.9.4: cờ là chip ngắn có biểu tượng; rê chuột xem giải thích, bấm để hiện giải thích ngay dưới dòng
  const FLAG_SHORT = { UNCERTAIN_CLASS: 'Class?', UNCERTAIN_BOUNDARY: 'Biên?', UNCERTAIN_SCOPE: 'Phạm vi?', ATTRIBUTE_CHECK: 'Thuộc tính?',
    LOW_VISIBILITY: 'Khó thấy', DUPLICATE: 'Trùng?', STATE: 'Điểm?', LEFT_RIGHT: 'Trái/phải?', TOPOLOGY: 'Thứ tự?', SCOPE: 'Phạm vi?',
    id_check: 'ID?', false_track: 'Nhầm?', drift: 'Keyframe', flag: 'Che?', fragmentation: 'Đứt ID?', lost: 'Mất?', merge: 'Gộp?', id_switch: 'Đổi ID?',
    LLM_DROP: 'Bỏ?', LLM_ADDED: 'Mới thêm' };
  S.openFlags = S.openFlags || new Set();
  // Gom cờ cùng loại thành một chip có số đếm (trước đây một track có thể có 7 chip "Kiểm tra ID" giống nhau)
  function flagGroups(flags) {
    const groups = [];
    for (const f of flags || []) {
      const g = groups.find((x) => x.code === f.code);
      if (g) g.items.push(f); else groups.push({ code: f.code, items: [f] });
    }
    return groups;
  }
  function flagBadges(flags, key) {
    return flagGroups(flags).map((g) => {
      const k = `${key}:${g.code}`, name = FLAG_SHORT[g.code] || FLAG_NAMES[g.code] || g.code;
      return h('button', { type: 'button', class: 'fchip' + (key && S.openFlags.has(k) ? ' on' : ''),
        title: `${FLAG_NAMES[g.code] || g.code}: ${g.items.map((f) => f.message).join(' · ')}`,
        onclick: (ev) => { ev.stopPropagation(); if (!key) return; if (S.openFlags.has(k)) S.openFlags.delete(k); else S.openFlags.add(k); render(); } },
      h('span', { class: 'fi', text: '!' }), g.items.length > 1 ? `${name} ×${g.items.length}` : name);
    });
  }
  function flagDetails(flags, key) {
    const open = flagGroups(flags).filter((g) => S.openFlags.has(`${key}:${g.code}`));
    const lines = [...new Set(open.flatMap((g) => g.items.map((f) => f.message)))];
    return lines.length ? h('div', { class: 'fdetail small-text' }, lines.map((m) => h('div', { text: m }))) : null;
  }

  function hover(li, key) {
    li.addEventListener('mouseenter', () => { S.highlight = key; li.classList.add('hot'); drawOverlay(); });
    li.addEventListener('mouseleave', () => { S.highlight = null; li.classList.remove('hot'); drawOverlay(); });
  }

  function renderPropose() {
    if (is3d()) return renderLidar();
    const out = [tourCard()];
    const ready = S.resolved && S.health && S.health.ok;
    if (S.health && S.health.ok && S.health.is_mock) {
      out.push(h('div', { class: 'note', text: 'Backend đang chạy chế độ GIẢ LẬP (mock): kết quả không phải của SAM3, chỉ để thử giao diện.' }));
    }
    out.push(h('button', { class: 'primary big ' + tourOn('propose'), type: 'button', disabled: !ready || !!S.busy, onclick: propose,
      title: keyName(keyOf('propose')) }, ico('spark'), h('span', { text: 'Gợi ý nhãn cho frame này' }), h('kbd', { text: keyName(keyOf('propose')) })));
    // 0.9.5: Claude xem ảnh: duyệt đề xuất theo guideline, điền thuộc tính, tag, occluded, tìm vật sót
    const vision = !!(S.health && S.health.vision_llm);
    const usage = (S.llm && S.llm.usage) || (S.health && S.health.llm_usage);
    out.push(h('label', { class: 'check small-text', title: vision ? 'Mỗi lần gợi ý gửi ảnh frame (thu nhỏ) và ảnh cắt nhỏ cho Claude' : 'Cần Anthropic API key trong Cài đặt của app' },
      h('input', { type: 'checkbox', checked: vision && cfg.useLlm !== false, disabled: !vision,
        onchange: (ev) => { cfg.useLlm = ev.target.checked; saveCfg(); render(); } }),
      h('span', { text: vision
        ? `Claude xem ảnh: duyệt theo guideline, điền thuộc tính, tag, tìm vật sót${usage ? ` (hôm nay ${usage.calls}${usage.limit ? '/' + usage.limit : ''} lượt)` : ''}`
        : 'Claude xem ảnh: chưa bật (cần Anthropic API key trong Cài đặt của app)' })));
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
    if ((S.tags || []).length && S.propFrame === S.frame) out.push(renderTags());

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
      // 0.9.4: gom cờ: mỗi loại một chip kèm số lượng; bấm để chỉ xem các dòng có cờ đó (không đổi việc ghi)
      const codes = {};
      for (const p of shown) for (const f of p.flags) codes[f.code] = (codes[f.code] || 0) + 1;
      if (Object.keys(codes).length) {
        out.push(h('div', { class: 'row fchips' }, h('span', { class: 'small-text muted', text: 'Cần xem:' }),
          Object.entries(codes).map(([code, n]) => h('button', { type: 'button', class: 'fchip' + (S.flagFilter === code ? ' on' : ''), title: FLAG_NAMES[code] || code,
            onclick: () => { S.flagFilter = S.flagFilter === code ? null : code; render(); } }, h('span', { class: 'fi', text: '!' }), `${FLAG_SHORT[code] || code} ${n}`))));
      }
      const listed = S.flagFilter ? shown.filter((p) => p.flags.some((f) => f.code === S.flagFilter)) : shown;
      if (S.flagFilter && listed.length < shown.length) {
        out.push(h('div', { class: 'muted small-text', text: `Đang chỉ xem ${listed.length} dòng có cờ "${FLAG_SHORT[S.flagFilter] || S.flagFilter}"; ${shown.length - listed.length} dòng khác vẫn được ghi nếu đang tick.` }));
      }
      out.push(h('ul', { class: tourOn('list') }, listed.map((p) => {
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
          attrEditor(p),
          p.flags.length || p.type !== 'skeleton' ? h('div', { class: 'row fchips' },
            p.type !== 'skeleton' ? h('button', { class: 'badge', type: 'button', text: p.occluded ? 'occluded' : 'visible', title: 'Bấm để đổi occluded / visible',
              onclick: (ev) => { ev.stopPropagation(); p.occluded = !p.occluded; p.userTouched = true; render(); } }) : null,
            flagBadges(p.flags, 'p' + p.pid),
            p.flags.length ? h('button', { class: 'small issue', type: 'button', text: 'Issue', title: 'Tạo Issue trên CVAT: gửi case này cho mentor',
              onclick: () => createIssue(p.flags[0].code, `${p.label_name}: ${p.flags.map((f) => f.message).join(' ')}`, p.bbox) }) : null,
          ) : null,
          p.flags.length ? flagDetails(p.flags, 'p' + p.pid) : null,
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
    // 0.9.7: job 3D — thanh ghi ghim đáy ở tab Cả job (bước 3)
    if (is3d() && S.tab === 'job' && S.tracks.length) {
      const checked = S.tracks.filter((t) => t.checked).length;
      return [h('button', { class: 'primary big', type: 'button', disabled: !!S.busy || !checked, onclick: applyTracks,
        text: `Ghi ${checked} track đã chọn vào CVAT` })];
    }
    if (S.tab !== 'propose') return [];
    const nUndo = undoStackOf(S.jobId).length;
    const undo = h('button', { class: tourOn('undo'), type: 'button', disabled: !!S.busy || !nUndo, onclick: undoLast,
      title: `${keyName(keyOf('undo'))}. Bấm nhiều lần để lùi thêm (tối đa 20 lần ghi).` }, ico('undo', 's'), h('span', { text: nUndo ? `Hoàn tác (${nUndo})` : 'Hoàn tác' }));
    if (!((S.proposals.length || (S.tags || []).length) && S.propFrame === S.frame)) return nUndo ? [h('div', { class: 'row' }, undo)] : [];
    const checked = S.proposals.filter(visible).filter((p) => p.checked).length + (S.tags || []).filter((t) => t.checked).length;
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
    if (is3d() && cuboidLabels().length && S.health && S.health.ok) return renderLabels3d();
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
        h('td', {}, c.shape === 'tag' ? h('span', { class: 'muted', text: 'tag' }) : shape),
        h('td', {}, c.shape === 'tag' ? null : h('input', { type: 'text', value: c.prompts.join(', '), placeholder: derived ? 'suy ra bằng luật' : 'prompt tiếng Anh',
          title: 'Prompt cho SAM3, cách nhau bằng dấu phẩy',
          onchange: (ev) => setOverride(label, { prompts: ev.target.value.split(',').map((s) => s.trim()).filter(Boolean) }) })),
        h('td', {}, c.shape === 'tag' ? null : h('input', { type: 'number', min: '0.05', max: '0.99', step: '0.05', value: String(c.threshold),
          title: c.learned && c.learned.threshold ? `Tool đã học: ${c.learned.threshold_from} → ${c.learned.threshold} (${c.learned.threshold_reason}). Gõ số khác để đặt tay.` : 'Ngưỡng tin cậy',
          onchange: (ev) => setOverride(label, { threshold: Number(ev.target.value) }) }),
          c.learned ? h('div', { class: 'small-text', style: 'color:var(--accent, #4a90d9)', text: 'tự học', title: [c.learned.threshold_reason, c.learned.box_reason].filter(Boolean).join(' · ') }) : null),
      );
    });
    // 0.9.4: label không gợi ý được (tag, skeleton lạ, box trong job skeleton...) mà job chưa có profile cho project:
    // chỉ thẳng tới chỗ tạo, vì profile đó gợi ý theo từng label của task
    const blocked = S.resolved.labels.filter((c) => !c.supported || (c.shape === 'skeleton' && !c.matched));
    const banner = blocked.length && !S.resolved.project_profile ? h('div', { class: 'note' },
      h('div', { text: `${blocked.length} label profile "${S.resolved.title || S.resolved.profile_id}" chưa gợi ý được (${blocked.slice(0, 4).map((c) => c.name).join(', ')}${blocked.length > 4 ? '…' : ''}). `
        + 'Profile cho project dựng cách gợi ý theo từng label của task, kể cả tag và skeleton.' }),
      h('button', { class: 'small', type: 'button', style: 'margin-top:6px', text: 'Mở Profile cho project',
        onclick: () => { cfg.openSecs = cfg.openSecs || {}; cfg.openSecs['guide:Profile cho project'] = true; saveCfg(); S.tab = 'guide'; render(); loadGuide(); } })) : null;
    return [
      banner,
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
    if (is3d()) {           // 0.9.7: job 3D dùng giao diện 3 bước riêng; ẩn hoàn toàn mục 2D ("Gợi ý cho cả job")
      out.push(h('div', { class: 'row small-text' }, h('span', { text: 'Từ frame' }), num('from', r.from),
        h('span', { text: 'tới' }), num('to', r.to), h('span', { class: 'muted', text: `(job: ${r.start}–${r.stop})` })));
      out.push(...renderLidar3Step(busy));
      out.push(h('button', { type: 'button', disabled: busy, onclick: undoLast, text: 'Hoàn tác lần ghi gần nhất' }));
      return out;
    }
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
    if (is3d()) {      // 0.9.7: luồng 3 bước là giao diện chính ở tab Cả job; mục này giữ cho hàm cũ dùng
      const out3 = [h('h2', { text: 'Track cuboid 3D (LiDAR)' }),
        h('p', { class: 'muted small-text', style: 'margin:0', text: 'Mỗi vật một track: kích thước khoá theo frame có nhiều điểm nhất, yaw theo hướng đi, bị che tối đa 6 frame thì nội suy và bật occluded.' }),
        h('button', { class: 'primary', type: 'button', disabled: busy || !cuboidLabels().length, onclick: lidarTrackSession, text: `Dựng track cho ${jobFrames(r.from, r.to).length} frame` })];
      return S.tracks.length ? out3.concat(renderTrackList(busy)) : out3;
    }
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
    if ((cfg.trackEngine || 'classic') === 'classic') {
      const sampling = h('select', { disabled: busy, title: 'Frame nào chạy SAM3', onchange: (ev) => { cfg.trackSampling = ev.target.value; saveCfg(); } },
        [['smart', 'Thông minh: keyframe + kiểm tra giữa khoảng, chỗ lệch thì chạy từng frame (nhanh hơn nhiều)'],
          ['all', 'Mọi frame (chậm nhất)']].map(([v, t]) => h('option', { value: v, text: t })));
      sampling.value = cfg.trackSampling || 'smart';
      out.push(h('label', { class: 'field' }, h('span', { text: 'Chạy SAM3 trên' }), sampling));
    }
    // 0.9.4: chỉ theo dõi người bạn chọn; vùng bỏ qua cho phản chiếu, quảng cáo
    const targets = targetsOf();
    const zones = zonesOf();
    out.push(h('div', { class: 'group', style: 'gap:6px' },
      h('h4', { text: 'Người cần theo dõi' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text: targets.length
        ? 'Tool chỉ theo dõi những người này, cả trước và sau frame bạn chọn. Người khác, phản chiếu, quảng cáo bị bỏ.'
        : 'Chưa chọn ai: tool theo dõi mọi người tìm được. Chọn người ở frame họ rõ nhất để tool bỏ người khác, phản chiếu, quảng cáo.' }),
      targets.length ? h('div', { class: 'row', style: 'flex-wrap:wrap;gap:4px' }, targets.map((t, i) => h('span', { class: 'chip small' },
        h('button', { class: 'link', type: 'button', text: `${t.name} · frame ${t.frame}`, title: 'Tới frame này', onclick: () => goToFrame(t.frame) }),
        h('button', { class: 'x', type: 'button', text: '✕', title: `Bỏ ${t.name}`, onclick: () => { const l = targetsOf().slice(); l.splice(i, 1); saveTargets(l); render(); drawOverlay(); } })))) : null,
      h('div', { class: 'row' },
        h('button', { type: 'button', disabled: busy || !labels.length, onclick: () => startBox('target') }, ico('box', 's'), h('span', { text: 'Chọn người ở frame này' })),
        targets.length ? h('button', { class: 'small', type: 'button', disabled: busy, text: 'Bỏ chọn hết', onclick: () => { saveTargets([]); render(); drawOverlay(); } }) : null)));
    out.push(h('div', { class: 'group', style: 'gap:6px' },
      h('h4', { text: 'Vùng bỏ qua' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'Khoanh gương, kính, biển quảng cáo, màn hình: mọi phát hiện có tâm trong vùng bị bỏ, cho mọi job của task này.' }),
      zones.length ? h('div', { class: 'row', style: 'flex-wrap:wrap;gap:4px' }, zones.map((z, i) => h('span', { class: 'chip small' },
        h('span', { text: `Vùng ${i + 1}` }),
        h('button', { class: 'x', type: 'button', text: '✕', title: 'Bỏ vùng này', onclick: () => { const l = zonesOf().slice(); l.splice(i, 1); cfg.zones[S.job.task_id] = l; saveCfg(); clearProposals(); render(); drawOverlay(); } })))) : null,
      h('button', { type: 'button', disabled: busy, onclick: () => startBox('zone') }, ico('box', 's'), h('span', { text: 'Khoanh vùng bỏ qua' }))));
    out.push(renderThresholds(busy));
    const prog = GM_getValue(`trackprog:${S.jobId}`, null);
    out.push(h('div', { class: 'row muted small-text' },
      h('span', { class: 'grow' }, h('span', { text: 'Mức cấu hình:' }), ' ', h('b', { text: QUALITY[qualityLevel()].name }),
        (cfg.quality || 'auto') === 'auto' ? h('span', { text: ' (tự động)' }) : null),
      h('button', { class: 'small link', type: 'button', text: 'Đổi', onclick: () => { S.prevTab = S.tab; S.tab = 'settings'; render(); } })));
    if (prog && prog.done && prog.done.length && !busy) {
      out.push(h('div', { class: 'note' }, h('span', { text: `Lần trước dừng giữa chừng: đã có ${prog.done.length} frame. Bấm Theo dõi để chạy tiếp (cùng khoảng frame, label). ` }),
        h('button', { class: 'small link', type: 'button', text: 'Bỏ tiến độ cũ', onclick: () => { GM_setValue(`trackprog:${S.jobId}`, null); render(); } })));
    }
    out.push(h('button', { class: 'primary', type: 'button', disabled: busy || !labels.length,
      onclick: runTracking, text: targets.length ? `Theo dõi ${targets.length} người qua ${jobFrames(r.from, r.to).length} frame` : `Theo dõi ${jobFrames(r.from, r.to).length} frame` }));
    if (!S.tracks.length) return out;
    return out.concat(renderTrackList(busy));
  }

  // 0.9.6: ngưỡng occluded / outside theo % diện tích toàn thân bị che. Lấy từ guideline (profile), không có thì mặc định;
  // bạn sửa được, tool nhớ theo task. Trạng thái đổi theo từng frame (một frame lẻ không làm đổi trạng thái).
  function renderThresholds(busy) {
    const prof = (S.resolved && S.resolved.track && S.resolved.track.thresholds) || { occluded_hidden: 0.35, outside_hidden: 0.6, source: 'default' };
    const tid = S.job && S.job.task_id;
    const mine = (cfg.trackThresholds || {})[tid] || null;
    const occ = mine ? mine.occ : Math.round(prof.occluded_hidden * 100);
    const outv = mine ? mine.out : Math.round(prof.outside_hidden * 100);
    const source = mine ? 'bạn đặt cho task này' : prof.source === 'guideline' ? 'theo guideline của profile' : 'mặc định (guideline không ghi)';
    const save = (o, u) => {
      cfg.trackThresholds = cfg.trackThresholds || {};
      o = Math.max(5, Math.min(95, Math.round(Number(o) || 0)));
      u = Math.max(o + 5, Math.min(100, Math.round(Number(u) || 0)));
      cfg.trackThresholds[tid] = { occ: o, out: u };
      saveCfg(); render();
    };
    const num = (value, onchange, title) => h('input', { type: 'number', min: 5, max: 100, step: 5, value, disabled: busy, title,
      style: 'width:4.2em', onchange: (ev) => onchange(ev.target.value) });
    return h('div', { class: 'group', style: 'gap:6px' },
      h('h4', { text: 'Ngưỡng che khuất' }),
      h('div', { class: 'row', style: 'flex-wrap:wrap;gap:6px;align-items:center' },
        h('span', { text: 'occluded từ' }), num(occ, (v) => save(v, outv), 'Phần trăm diện tích toàn thân bị che để đặt occluded'), h('span', { text: '%' }),
        h('span', { class: 'muted', text: '·' }),
        h('span', { text: 'outside từ' }), num(outv, (v) => save(occ, v), 'Phần trăm diện tích toàn thân bị che để đặt outside'), h('span', { text: '%' })),
      h('p', { class: 'muted small-text', style: 'margin:0', text: `Nguồn: ${source}. Tính trên diện tích toàn thân ước lượng (cả phần bị che), đổi theo từng frame; người quay lại vẫn giữ ID cũ.` }),
      mine ? h('button', { class: 'small link', type: 'button', disabled: busy, text: 'Về ngưỡng của profile',
        onclick: () => { delete cfg.trackThresholds[tid]; saveCfg(); render(); } }) : null);
  }

  function renderTrackList(busy) {
    const out = [];
    const m = S.trackMeta || {};
    for (const w of m.warnings || []) out.push(h('div', { class: 'note', text: w }));
    if (m.existing) out.push(h('div', { class: 'note', text: `Job đã có ${m.existing} track. Track mới có thể trùng với track cũ.` }));
    const checked = S.tracks.filter((t) => t.checked).length;
    out.push(h('div', { class: 'row' },
      h('span', { class: 'grow muted', text: `${S.tracks.length} track (frame ${m.from}–${m.to}) · ${checked} đã chọn` }),
      h('button', { class: 'small', type: 'button', text: 'Bỏ track có cờ', onclick: () => { S.tracks.forEach((t) => { if (t.flags.length) t.checked = false; }); persist('tracks'); render(); drawOverlay(); } })));
    // 0.9.4: track không chọn (thường là track 1–2 frame, phát hiện nhầm) gom lại cuối danh sách, mặc định ẩn
    const off = S.tracks.filter((t) => !t.checked);
    if (off.length && checked) {
      out.push(h('label', { class: 'check small-text' }, h('input', { type: 'checkbox', checked: !!S.showOffTracks,
        onchange: (ev) => { S.showOffTracks = ev.target.checked; render(); } }), h('span', { text: `Hiện cả ${off.length} track không chọn` })));
    }
    const listed = (off.length && checked && !S.showOffTracks) ? S.tracks.filter((t) => t.checked) : S.tracks;
    out.push(h('ul', {}, listed.map((t) => {
      const visibleNow = trackBoxAt(t, S.frame);
      const li = h('li', { class: 'item' },
        h('div', { class: 'row' },
          h('input', { type: 'checkbox', checked: t.checked, title: 'Ghi track này', onchange: (ev) => { t.checked = ev.target.checked; persist('tracks'); render(); drawOverlay(); } }),
          h('span', { class: 'swatch', style: `background:${trackColor(t.tid)}` }),
          // 0.9.4: số keyframe xuống dòng dưới tên, để tên track không bị bóp thành một chữ mỗi dòng
          h('div', { class: 'grow', style: 'min-width:0' },
            h('div', { text: `${t.name ? t.name + ' · ' : ''}#${t.tid} ${t.label_name} · frame ${t.start}–${t.end}` }),
            h('div', { class: 'muted small-text', text: `${t.keyframes} keyframe${t.gaps ? ` · khuất ${t.gaps} lần` : ''}` })),
          h('button', { class: 'small', type: 'button', style: 'flex-shrink:0', text: 'Tới frame đầu', onclick: () => goToFrame(t.start) })),
        t.flags.length ? h('div', { class: 'row fchips' }, flagBadges(t.flags, 't' + t.tid)) : null,
        t.flags.length ? flagDetails(t.flags, 't' + t.tid) : null,
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
    return [...renderProjectProfile(), ...renderImport(), h('hr', { style: 'border:none;border-top:1px solid var(--line, #3a3f47);margin:10px 0' }), ...renderGuideDraft()];
  }

  // ---- profile của project (0.9.4): dựng từ label của task + guideline, không cần file taxonomy ----
  function projectInfo() {
    return { project_id: (S.job && S.job.project_id) || null, task_id: (S.job && S.job.task_id) || null };
  }

  async function previewProjectProfile() {
    const file = S.projFile || null;
    await run(file ? `Đang đọc ${file.name} và label của task…` : 'Đang dựng profile từ label của task…', async () => {
      if (!S.projName) {        // tên project (hoặc task) để đặt tên profile cho dễ nhận
        const info = projectInfo();
        try { S.projName = info.project_id ? (await cvat('GET', `/api/projects/${info.project_id}`)).name : (await cvat('GET', `/api/tasks/${info.task_id}`)).name; } catch (e) { S.projName = ''; }
      }
      const body = Object.assign({ labels: labelPayload(), name: (S.projName || ''), use_llm: !!cfg.importUseLlm }, projectInfo());
      if (file) { body.filename = file.name; body.data_b64 = await readFileB64(file); } else if (S.projUseDraft && S.guide && S.guide.markdown) {
        // Bản nháp guideline vừa soạn ở mục dưới: dùng luôn làm guideline của profile
        body.filename = `ban-nhap-${S.jobId}.md`;
        body.data_b64 = btoa(unescape(encodeURIComponent(S.guide.markdown)));
      }
      S.proj = await backend('POST', '/profile/project/preview', body);
      const bad = S.proj.rows.filter((r) => r.status !== 'ok').length;
      setStatus(`Đã dựng profile cho ${S.proj.rows.length} label${bad ? `, ${bad} label cần bạn xem` : ''}. Bấm "Thử trên frame này" rồi "Lưu cho project".`, 'ok');
    });
  }

  async function tryProjectProfile() {
    await run('Đang thử profile mới trên frame này…', async () => {
      const frame = S.frame;
      let ann = null;
      try { ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`); } catch (e) { /* vẫn thử được */ }
      const existing = ann ? existingAt(ann, frame) : [];
      const fr = await getFrame();
      const res = await backend('POST', '/propose', Object.assign(baseBody(), { image_b64: fr.b64, existing, profile_inline: S.proj.profile }, proposeExtra(S.frame)));
      if (frame !== S.frame) return;
      S.proposals = stamp(res.proposals || [], 'frame');
      S.tags = tagSuggestions(res.tags, ann, frame);
      S.warnings = res.warnings || [];
      S.propFrame = frame; S.imgW = res.width; S.imgH = res.height;
      S.tab = 'propose';
      setStatus(`Thử profile mới: ${S.proposals.length} đề xuất, ${(S.tags || []).length} tag. Chưa lưu: quay lại tab Guideline để lưu hoặc sửa.`, 'ok');
      drawOverlay();
    });
  }

  async function saveProjectProfile() {
    const p = S.proj;
    if (p.exists && !await uiConfirm('Project này đã có profile riêng. Ghi đè bằng bản mới?')) return;
    await run('Đang lưu profile cho project…', async () => {
      const res = await backend('POST', '/profile/project/save', { profile: p.profile, guideline_id: (p.guideline && p.guideline.id) || '' });
      S.proj = null; S.projFile = null; S.projCurrent = null;
      cfg.profileId = 'auto'; saveCfg();
      clearProposals();
      await resolveLabels();
      setStatus(`Đã lưu profile ${res.profile_id}. Mọi job của ${projectInfo().project_id ? 'project' : 'task'} này tự dùng profile này.`, 'ok');
    });
  }

  function renderProjectProfile() {
    const busy = !!S.busy;
    const llm = (S.health && S.health.guideline_llm) || { provider: 'none' };
    const r = S.resolved || {};
    const out = [h('h2', { text: 'Profile cho project' })];
    out.push(h('p', { class: 'muted small-text', style: 'margin:0', text:
      'Tool dựng cách gợi ý riêng cho project từ chính label của task (kể cả attribute, skeleton, tag). '
      + 'Thêm guideline để tool hiểu định nghĩa, kích thước tối thiểu và quy ước trái/phải. Không cần file taxonomy.' }));
    out.push(h('div', { class: 'row small-text' }, h('span', { class: 'grow', text: r.profile_id
      ? `Job này đang dùng: ${r.title || r.profile_id}${r.project_profile ? ' (riêng của project)' : ''}` : 'Chưa đọc được label của job.' })));
    const picker = h('input', { type: 'file', accept: '.docx,.md,.txt,.markdown,.pdf', disabled: busy,
      onchange: (ev) => { S.projFile = (ev.target.files || [])[0] || null; render(); } });
    out.push(h('label', { class: 'field' }, h('span', { text: 'Guideline (tuỳ chọn)' }), picker));
    if (S.projFile) out.push(h('div', { class: 'muted small-text', text: `Đã chọn: ${S.projFile.name}` }));
    else if (S.guide && S.guide.markdown) {
      out.push(h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!S.projUseDraft, onchange: (ev) => { S.projUseDraft = ev.target.checked; } }),
        h('span', { text: 'Dùng bản nháp guideline vừa soạn (mục Soạn guideline nháp bên dưới)' })));
    }
    if (llm.provider !== 'none') {
      out.push(h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!cfg.importUseLlm,
        onchange: (ev) => { cfg.importUseLlm = ev.target.checked; saveCfg(); } }),
        h('span', { text: `Cho AI đọc guideline (gửi VĂN BẢN tới ${llm.provider}${llm.local ? ', chạy trên máy' : ''}, không gửi ảnh)` })));
    } else {
      out.push(h('div', { class: 'muted small-text', text: 'Chưa bật AI soạn guideline: tool đọc guideline bằng luật (bảng, câu chữ). Bật ở app: Cài đặt → AI soạn guideline.' }));
    }
    out.push(h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', disabled: busy || !S.labels.length || !(S.health && S.health.ok),
      text: r.project_profile ? 'Tạo lại profile cho project' : 'Tạo profile cho project', onclick: previewProjectProfile })));
    if (!S.proj) return out;
    const p = S.proj;
    for (const w of p.warnings || []) out.push(h('div', { class: 'note', text: w }));
    for (const n of p.notes || []) out.push(h('div', { class: 'note', text: n }));
    if (p.guideline) {
      out.push(h('div', { class: 'muted small-text', text: `Guideline ${p.guideline.file}: ${p.guideline.checklist} mục checklist, ${p.guideline.issue_types} loại issue (dùng ở tab Kiểm tra).` }));
    }
    const label = { ok: 'Tool gợi ý được', warn: 'Gợi ý được một phần', bad: 'Bạn đánh tay' };
    out.push(h('div', { class: 'group', style: 'gap:0' }, p.rows.map((row) => h('div', { class: 'prow' },
      h('span', { class: 'dot ' + row.status, title: label[row.status] || '' }),
      h('div', { class: 'grow', style: 'min-width:0' },
        h('div', { class: 'row', style: 'gap:6px' }, h('b', { text: row.label }), h('span', { class: 'muted small-text', text: row.type })),
        h('div', { class: 'small-text', text: row.how }),
        row.note ? h('div', { class: 'small-text', style: 'color:#b07800', text: row.note }) : null,
        row.details && row.details.length ? h('details', {}, h('summary', { text: `Chi tiết (${row.details.length})` }),
          h('ul', {}, row.details.map((d) => h('li', { text: `${d.name}: ${d.text}` })))) : null)))));
    out.push(h('div', { class: 'row' },
      h('button', { type: 'button', disabled: busy, text: 'Huỷ', onclick: () => { S.proj = null; render(); } }),
      h('span', { class: 'grow' }),
      h('button', { type: 'button', disabled: busy, text: 'Thử trên frame này', onclick: tryProjectProfile }),
      h('button', { class: 'primary', type: 'button', disabled: busy, text: 'Lưu cho project', onclick: saveProjectProfile })));
    return out;
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
        cfg.guideSampleAuto !== false
          ? h('div', { class: 'row', style: 'gap:6px' }, h('b', { text: String(autoSample(r).n) }),
            h('button', { class: 'small', type: 'button', text: 'Tự chọn số', title: 'Nhập số ảnh mẫu bằng tay',
              onclick: () => { cfg.guideSampleAuto = false; cfg.guideSample = autoSample(r).n; saveCfg(); render(); } }))
          : h('div', { class: 'row', style: 'gap:6px' },
            h('input', { type: 'number', min: '4', max: '300', value: String(cfg.guideSample), style: 'width:80px',
              onchange: (ev) => { cfg.guideSample = Math.max(4, Math.round(Number(ev.target.value) || 40)); saveCfg(); render(); } }),
            h('button', { class: 'small', type: 'button', text: 'Tự động', title: 'Để tool tự tính theo độ dài và độ đa dạng của job',
              onclick: () => { cfg.guideSampleAuto = true; saveCfg(); render(); } }))),
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
    const count = sampleCount(r);
    if (cfg.guideSampleAuto !== false) {
      out.push(h('div', { class: 'muted small-text', text: `Tool tự chọn ${count} ảnh mẫu (${autoSample(r).why}). `
        + (S.smart && S.smart.job === S.jobId ? '' : 'Chạy "Phân tích frame của job" ở tab Cả job để tool chọn theo độ đa dạng của ảnh.') }));
    }
    if (S.labels.length <= 1) {
      out.push(h('div', { class: 'note', text: 'Task mới có label tạm (CVAT bắt buộc ít nhất 1 label): tool tự tìm các loại vật trong ảnh và đề xuất danh sách class. '
        + 'Duyệt xong, bấm "Thêm label còn thiếu" để tool thêm vào task (không xoá label tạm).' }));
    }
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

    // 0.9.4: mỗi câu có trạng thái (đã chốt / bỏ qua / chưa trả lời), câu đã chốt gọn một dòng, mỗi lựa chọn cho xem
    // trước guideline sẽ thêm hoặc đổi dòng nào.
    S.guideSkip = S.guideSkip || {};
    const skip = S.guideSkip[g.run_id] = S.guideSkip[g.run_id] || new Set();
    const state = (q) => (q.answer ? 'done' : skip.has(q.key) ? 'skip' : 'todo');
    const nDone = g.questions.filter((q) => state(q) === 'done').length;
    const nSkip = g.questions.filter((q) => state(q) === 'skip').length;
    const nTodo = g.questions.length - nDone - nSkip;
    out.push(h('h2', { text: `Câu hỏi cần chốt (còn ${nTodo}/${g.questions.length})` }));
    out.push(h('div', { class: 'qprog' }, h('div', { class: 'bar' },
      h('i', { class: 'done', style: `width:${(100 * nDone) / Math.max(1, g.questions.length)}%` }),
      h('i', { class: 'skip', style: `width:${(100 * nSkip) / Math.max(1, g.questions.length)}%` })),
    h('div', { class: 'row small-text' }, h('span', { class: 'grow', text: `Đã chốt ${nDone} · Bỏ qua ${nSkip} · Còn ${nTodo}` }),
      nTodo ? h('button', { class: 'small', type: 'button', text: 'Tới câu chưa trả lời', onclick: () => {
        const el = root.querySelector('li.q.todo');
        if (el) { el.scrollIntoView({ block: 'center' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 900); }
      } }) : null)));
    S.qOpen = S.qOpen || new Set();
    out.push(h('ul', {}, g.questions.map((q) => {
      const st = state(q);
      const recommended = q.options.find((o) => o.id === q.recommended);
      const chosen = q.options.find((o) => o.id === q.answer);
      const mark = { done: '✓', skip: '–', todo: '?' }[st];
      if (st !== 'todo' && !S.qOpen.has(q.key)) {
        return h('li', { class: 'item q ' + st }, h('div', { class: 'row' },
          h('span', { class: 'qmark ' + st, text: mark }),
          h('span', { class: 'grow small-text', text: q.text }),
          h('b', { class: 'small-text', text: st === 'done' ? chosen.label : `bỏ qua (tạm dùng: ${recommended ? recommended.label : q.recommended})` }),
          h('button', { class: 'small', type: 'button', text: 'Sửa', onclick: () => { S.qOpen.add(q.key); render(); } })));
      }
      const effects = q.effects || {};
      const opts = h('div', { class: 'qopts' }, q.options.map((o) => {
        const lines = effects[o.id] || [];
        return h('button', { type: 'button', class: 'qopt' + (q.answer === o.id ? ' on' : '') + (o.id === q.recommended ? ' rec' : ''), disabled: busy,
          title: lines.length ? 'Guideline sẽ ghi:\n' + lines.join('\n') : '',
          onclick: () => { S.qOpen.delete(q.key); skip.delete(q.key); answerGuide(q.key, o.id); } },
        h('span', { text: o.label + (o.id === q.recommended ? ' (tool đề xuất)' : '') }),
        lines.length ? h('small', { text: '→ ' + lines[0] }) : null);
      }));
      return h('li', { class: 'item q ' + st },
        h('div', { class: 'row' }, h('span', { class: 'qmark ' + st, text: mark }), h('b', { class: 'grow', text: q.text })),
        q.evidence ? h('div', { class: 'small-text', text: q.evidence }) : null,
        opts,
        h('div', { class: 'row' }, h('span', { class: 'grow muted small-text', text: q.reason }),
          q.answer ? h('button', { class: 'small', type: 'button', text: 'Bỏ chốt', onclick: () => { S.qOpen.delete(q.key); answerGuide(q.key, ''); } })
            : h('button', { class: 'small', type: 'button', text: 'Bỏ qua', title: 'Để sau: tool tạm dùng lựa chọn đề xuất',
              onclick: () => { skip.add(q.key); S.qOpen.delete(q.key); render(); } })),
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

  // 0.9.6: mức cấu hình giống chỉnh đồ hoạ trong game. Mỗi mức là một dòng chọn được, có giải thích ngắn;
  // dưới cùng là VRAM backend đang dùng và các model đang nạp.
  function vramText() {
    const v = S.health && S.health.vram;
    if (!v || !v.total_mb) return '';
    const used = v.total_mb - (v.free_mb || 0);
    const loaded = (v.loaded || []).map((n) => ({ body: 'pose người', face: 'điểm mặt', video: 'SAM3 Video', reid: 'nhận lại người', depth: 'độ sâu', wholebody: 'pose toàn thân' }[n] || n));
    return `VRAM: ${(used / 1024).toFixed(1)}/${(v.total_mb / 1024).toFixed(1)} GB · model phụ đang nạp: ${loaded.length ? loaded.join(', ') : 'không'}`
      + (v.refreshes ? ` · đã dọn ${v.refreshes} lần` : '');
  }
  function renderQuality() {
    const auto = autoQuality();
    const opts = [['auto', `Tự động (máy này: ${translateText(QUALITY[auto].name)})`, 'Chọn theo VRAM của GPU backend']]
      .concat(Object.entries(QUALITY).map(([k, q]) => [k, q.name, q.note]));
    const cur = cfg.quality || 'auto';
    const v = S.health && S.health.vram;
    const low = v && v.total_mb && v.free_mb < 800;
    return h('div', { class: 'group', role: 'radiogroup', 'aria-label': 'Mức cấu hình' },
      h('h4', { text: 'Mức cấu hình' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'Giống chỉnh đồ hoạ trong game: mức cao chính xác hơn nhưng tốn VRAM và chậm hơn. Backend tự nhả model không dùng tới để không chậm dần.' }),
      opts.map(([k, name, note]) => h('label', { class: 'check' + (cur === k ? ' on' : '') },
        h('input', { type: 'radio', name: 'sam3q', checked: cur === k, onchange: () => { cfg.quality = k; saveCfg(); render(); } }),
        h('span', {}, name, h('span', { class: 'sub', text: note })))),
      vramText() ? h('div', { class: 'muted small-text', text: vramText() }) : null,
      low ? h('div', { class: 'note', text: 'VRAM gần đầy: nên hạ một mức, hoặc đóng chương trình khác đang dùng GPU.' }) : null);
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
      renderQuality(),
      group('Khi duyệt',
        sw('Chạy sẵn gợi ý cho frame tiếp theo', 'prefetch', qualityLevel() === 'light' ? 'Tắt ở mức Nhẹ để đỡ VRAM' : 'Chuyển frame là có đề xuất ngay'),
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
        sw('Tương phản cao', 'highContrast', '', () => render()),
        h('div', { class: 'row' }, h('span', { class: 'grow', text: 'Kích thước panel' }),
          ...[['Gọn', 320], ['Vừa', 380], ['Rộng', 480]].map(([t, w]) => h('button', { class: 'small' + ((Number(cfg.panelWidth) || 380) === w ? ' on' : ''), type: 'button', text: t,
            onclick: () => { cfg.panelWidth = w; cfg.panelHeight = null; saveCfg(); applyLook(); render(); } }))),
        h('p', { class: 'muted small-text', style: 'margin:0', text: 'Hoặc kéo mép trái panel để đổi độ rộng, mép dưới để đổi chiều cao. Bấm đúp mép để về cỡ mặc định.' })),
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
      // 0.9.4: đoạn giải thích dài đầu mục thu vào "Giải thích" để mục gọn, đọc lướt được
      const nodes = sec.nodes.filter(Boolean).map((n) => (n.tagName === 'P' && n.classList.contains('muted') && n.textContent.length > 120
        ? h('details', { class: 'help' }, h('summary', { text: 'Giải thích' }), n) : n));
      box.append(head, h('div', { class: 'sec-b' }, ...nodes));
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
      // 0.9.4: gom các frame cùng lý do vào một dòng (trước đây mỗi frame một thẻ, chữ lặp lại cả trang)
      const top = S.smart.ranking.slice(0, 15), groups = [];
      for (const x of top) {
        const why = x.reasons.join(', ');
        const g = groups.find((y) => y.why === why);
        if (g) g.frames.push(x.frame); else groups.push({ why, frames: [x.frame] });
      }
      out.push(h('div', { class: 'small-text', text: `Nên làm trước (${top.length} frame, theo thứ tự ưu tiên):` }));
      out.push(h('ul', {}, groups.map((g) => h('li', { class: 'item' },
        h('div', { class: 'small-text muted', text: g.why }),
        h('div', { class: 'row', style: 'flex-wrap:wrap;gap:4px;margin-top:4px' }, g.frames.map((f) =>
          h('button', { class: 'small', type: 'button', text: `#${f}`, onclick: () => goToFrame(f) })))))));
      const keys = S.smart.keyframes || [];
      // Ảnh rời (không phải video): gần như frame nào cũng là keyframe, danh sách không giúp gì
      const allDiffer = S.smart.analyzed > 3 && keys.length >= 0.8 * S.smart.analyzed;
      // 0.9.4: job video (tracking) thì chạy theo dõi và ra track; gợi ý trên keyframe chỉ ra shape rời từng frame
      const videoJob = S.job && S.job.mode === 'interpolation' && trackLabels().length;
      if (allDiffer && !videoJob) {
        out.push(h('div', { class: 'muted small-text', text: `Keyframe: ${keys.length}/${S.smart.analyzed} frame khác hẳn nhau (ảnh rời, không phải video), `
          + 'nên không lọc bớt được. Dùng "Gợi ý cả job" như bình thường.' }));
      } else {
      out.push(h('div', { class: 'row small-text' }, h('span', { class: 'grow', text: `Keyframe theo chuyển động: ${keys.length}/${S.smart.analyzed} frame` }),
        videoJob
          ? h('button', { class: 'small', type: 'button', disabled: busy || !S.resolved, text: 'Theo dõi cả job (ra track)',
            title: 'Job video: theo dõi để có track mỗi đối tượng một ID; keyframe do tool chọn theo chuyển động', onclick: runTracking })
          : h('button', { class: 'small', type: 'button', disabled: busy || !keys.length || !S.resolved, text: 'Gợi ý cả job chỉ trên keyframe',
            title: 'Chỉ chạy SAM3 trên các frame có thay đổi đáng kể (ra shape trên từng frame, không phải track)', onclick: () => runBatch(false, keys) })));
      out.push(h('div', { class: 'row small-text' }, keys.slice(0, 40).map((f) => h('button', { class: 'small', type: 'button', text: String(f), onclick: () => goToFrame(f) })),
        keys.length > 40 ? h('span', { class: 'muted', text: `+${keys.length - 40}` }) : null));
      }
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
        // 0.9.4: học cả polygon và mask (lấy hộp bao); khi gợi ý, SAM3 tách đúng vật trong hộp model riêng tìm được
        if (!['rectangle', 'polygon', 'mask'].includes(x.type) || !labelById(x.label_id)) continue;
        let bbox;
        if (x.type === 'rectangle') { const [x1, y1, x2, y2] = x.points; bbox = [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)]; }
        else if (x.type === 'mask') { const [l, t, r, b] = (x.points || []).slice(-4); bbox = [l, t, r + 1, b + 1]; }
        else { const xs = x.points.filter((_, i) => i % 2 === 0), ys = x.points.filter((_, i) => i % 2 === 1); bbox = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]; }
        if (!(bbox[2] - bbox[0] > 1 && bbox[3] - bbox[1] > 1)) continue;
        (byFrame[x.frame] = byFrame[x.frame] || []).push({ label_id: x.label_id, bbox });
      }
      const frames = sampleRange(Object.keys(byFrame).map(Number).sort((a, b) => a - b), 200);
      if (!frames.length) throw new Error('Job này chưa có nhãn hộp, polygon hay mask nào để học.');
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
  // 0.9.5: trí nhớ của project: bài học, quy ước tên file, các lần bạn sửa
  async function loadMemory() {
    const pid = S.resolved && S.resolved.profile_id;
    if (!pid) return;
    S.memoryPid = pid;      // đặt trước: lỗi mạng cũng không làm panel gọi lại liên tục mỗi lần vẽ
    try { const r = await backend('GET', `/memory/${encodeURIComponent(pid)}`); S.memory = r.summary; S.memoryLlm = r.llm; } catch (e) { S.memory = { corrections: 0, lessons: [], file_rules: [], since_lesson: 0 }; }
    render();
  }
  function renderMemory() {
    const pid = S.resolved && S.resolved.profile_id;
    if (!pid) return [];
    if (S.memoryPid !== pid && !S.memoryLoading) { S.memoryLoading = true; loadMemory().finally(() => { S.memoryLoading = false; }); }
    const m = S.memory;
    const out = [h('h2', { text: 'Trí nhớ của project' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'Mỗi lần bạn bấm Ghi, tool nhớ giá trị bạn chốt (thuộc tính, tag, occluded, đề xuất bị bỏ) '
        + 'so với giá trị tool gợi ý. Claude đọc các lần sửa và bài học này ở mọi lần xem ảnh sau; không có Claude thì tool vẫn dùng quy ước tên file và giá trị bạn hay chọn.' })];
    if (!m) { out.push(h('div', { class: 'muted small-text', text: 'Đang tải…' })); return out; }
    out.push(h('div', { class: 'small-text', text: `${m.corrections} lần sửa đã ghi nhận · ${m.lessons.length} bài học · ${m.file_rules.length} quy ước tên file` }));
    if (m.file_rules.length) {
      out.push(h('div', { class: 'note small-text' }, h('strong', { text: 'Quy ước tên file tool đã học' }),
        m.file_rules.slice(0, 8).map((r) => h('div', { text: `'${r.token}' → ${r.label}.${r.attr} = ${r.value} (${r.count} lần)` }))));
    }
    out.push(h('ul', {}, m.lessons.map((l) => h('li', { class: 'item' }, h('div', { class: 'row' },
      h('span', { class: 'grow small-text', text: l.text }),
      h('span', { class: 'muted small-text', text: l.from === 'you' ? 'bạn viết' : 'Claude tóm' }),
      h('button', { class: 'small', type: 'button', text: 'Xoá', onclick: async () => {
        const r = await backend('DELETE', `/memory/${encodeURIComponent(pid)}/lessons/${encodeURIComponent(l.id)}`); m.lessons = r.lessons; render();
      } }))))));
    const input = h('input', { type: 'text', class: 'grow', placeholder: 'Tự viết một luật cho project, vd: hand cầm vô lăng thì on_wheel = true' });
    out.push(h('div', { class: 'row' }, input, h('button', { class: 'small', type: 'button', text: 'Thêm luật', onclick: async () => {
      if (!input.value.trim()) return;
      const r = await backend('POST', `/memory/${encodeURIComponent(pid)}/lessons`, { text: input.value.trim() }); m.lessons = r.lessons; render();
    } })));
    const canAi = S.memoryLlm && S.memoryLlm.available;
    out.push(h('div', { class: 'row' }, h('button', { type: 'button', disabled: !!S.busy || !canAi || !m.corrections,
      title: canAi ? 'Claude đọc các lần bạn sửa và rút ra vài luật ngắn' : 'Cần Anthropic API key trong Cài đặt của app',
      text: m.suggest_lessons ? `Tóm bài học (${m.since_lesson} lần sửa mới)` : 'Tóm bài học từ các lần sửa',
      onclick: () => run('Claude đang tóm bài học…', async () => {
        const r = await backend('POST', `/memory/${encodeURIComponent(pid)}/distill`, {}); S.memory = r.summary;
        setStatus(`Có ${r.lessons.length} bài học cho project. Xoá luật nào không đúng.`, 'ok');
      }) })));
    return out;
  }

  function renderCustomModel() {
    const out = [h('h2', { text: 'Model riêng của dự án' }),
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'Khác với "học từ cách bạn sửa" ở trên: đây là một detector nhỏ được huấn luyện từ chính các nhãn đã duyệt (hộp, polygon, mask). Class nào đạt chuẩn (đủ ví dụ, đúng ≥ 70%, tìm được ≥ 60% trên ảnh giữ lại để kiểm) mới được dùng, nhanh hơn SAM3 nhiều lần; class khác vẫn do SAM3 làm.' })];
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
    if (S.custom.has_base === false) {
      out.push(h('div', { class: 'note small-text', text: 'Máy chưa có trọng số khởi đầu cho model riêng: huấn luyện sẽ học từ con số 0 và thường ra 0%. Cài thành phần "Trọng số khởi đầu cho model riêng" ở trang Thành phần của app trước.' }));
    }
    if (m) {
      out.push(h('table', {}, h('tr', {}, ['Class', 'Đúng', 'Tìm được', 'Ví dụ', 'Dùng'].map((x) => h('th', { text: x }))),
        m.classes.map((c) => { const k = m.metrics[c] || {}; const ok = m.usable.includes(c);
          return h('tr', {}, h('td', { text: c }), h('td', { text: `${Math.round((k.precision || 0) * 100)}%` }), h('td', { text: `${Math.round((k.recall || 0) * 100)}%` }),
            h('td', { text: String((m.counts || {})[c] || 0) }), h('td', { text: ok ? '✓' : '—', style: ok ? 'color:var(--ok)' : '' })); })));
      out.push(h('div', { class: 'muted small-text', text: `Huấn luyện lúc ${m.trained_at} trên ${m.device}, ${m.train_images} ảnh học, ${m.val_images} ảnh kiểm.` }));
      for (const note of m.explain || []) out.push(h('div', { class: 'note small-text', text: note }));
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
    const res = await backend('POST', '/propose', Object.assign(baseBody(), { image_b64: fr.b64, existing }, proposeExtra(frame)));
    return { res, existing, w: fr.w, h: fr.h };
  }
  function schedulePrefetch(fromFrame) {
    if (!cfg.prefetch || !QUALITY[qualityLevel()].prefetch || !S.resolved || !S.jobId) return;
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
    if (!shown.length && !(S.tags || []).some((t) => t.checked)) {   // không có gì để ghi: chỉ sang frame tiếp
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
    // 0.9.4: rộng theo cỡ bạn chọn (hoặc kéo mép trái panel); chữ to thì tự rộng thêm cho vừa
    const base = Number(cfg.panelWidth) || 380;
    host.style.width = `${Math.round(Math.min(Math.max(base * Math.max(1, scale), 300), window.innerWidth - 40))}px`;
    if (cfg.panelHeight) host.style.setProperty('--panel-h', `${Math.round(cfg.panelHeight)}px`); else host.style.removeProperty('--panel-h');
    const hc = !!cfg.highContrast;
    for (const [k, v] of Object.entries({ '--bg': '#000000', '--bg-soft': '#1a1a1a', '--line': '#ffffff', '--text': '#ffffff', '--muted': '#e0e0e0', '--accent': '#ffd400', '--accent-text': '#000000' })) {
      if (hc) host.style.setProperty(k, v); else host.style.removeProperty(k);
    }
  }


  // ---- đa ngôn ngữ: toàn bộ chữ của panel có bản tiếng Anh (bộ dịch sinh từ backend/i18n/en.json) ----
  // <i18n-catalog> phần này do scripts/i18n_build.py sinh, đừng sửa tay
  const I18N_DATA = {"codes":{"Backend không phản hồi sau 5 phút.":"E_BACKEND_TIMEOUT","Backend trả lỗi {0}. ":"E_BACKEND_HTTP","CVAT trả lỗi {0} cho {1} {2}. {3}":"E_CVAT_HTTP","Không kết nối được backend tại {0}. Kiểm tra backend đã chạy và địa chỉ trong Cài đặt panel (nút bánh răng).":"E_BACKEND_UNREACHABLE"},"strings":{"\nMục có cờ ({0}) được giữ lại để bạn duyệt từng frame.":"\nFlagged items ({0}) are kept for you to review frame by frame.","\nProfile có sẵn của tool sẽ quay lại khi bạn giải nén bản cập nhật mới.":"\nThe tool's built-in profile comes back when you unpack a new update.","\nVăn bản và định nghĩa của guideline vẫn còn; gỡ cả guideline ở tab Guideline.":"\nThe guideline's text and definitions remain; remove the guideline itself in the Guideline tab.","\n{0} nhãn đang có cờ cần xem kỹ.":"\n{0} labels are flagged for a closer look.","\n{0} track đang có cờ cần xem.":"\n{0} tracks are flagged for review."," (còn {0} lần ghi trước đó có thể hoàn tác tiếp)":" ({0} earlier saves can still be undone)"," (hôm nay {0}{1} lượt)":" (today {0}{1} calls)"," (job này không có ảnh camera)":" (this job has no camera images)"," (riêng của project)":" (project's own)"," (theo dõi bằng SAM3 Video)":" (tracked with SAM3 Video)"," (tool đề xuất)":" (suggested by the tool)"," (tự động)":" (auto)"," Claude đã xem: sửa {0}, đề nghị bỏ {1}, tìm thêm {2}.":" Claude reviewed: changed {0}, suggests dropping {1}, found {2} more."," Guideline không có bảng taxonomy: chọn thêm file taxonomy (.json), hoặc bấm \"Lấy label từ task đang mở\".":" The guideline has no taxonomy table: also choose a taxonomy file (.json), or press \"Take labels from the open task\"."," Không dùng được SAM3 Video ({0}), đã theo dõi bằng cách thường.":" SAM3 Video unavailable ({0}); tracked the regular way."," SAM3 Video lỗi giữa chừng ({0}), đã làm lại bằng cách thường.":" SAM3 Video failed midway ({0}); redone the regular way."," SAM3 chạy {0}/{1} frame, {2} frame nội suy đã kiểm tra.":" SAM3 ran on {0}/{1} frames, {2} frames interpolated and checked."," Tiếp tục từ lần trước ({0} frame có sẵn).":" Resuming from last time ({0} frames already done)."," Tool nhớ {0} vật bạn bỏ.":" The tool remembers {0} objects you dropped."," Tải lại trang (F5) để CVAT nhận label mới.":" Reload the page (F5) so CVAT picks up the new labels."," Tải lại trang (F5) để thấy chúng trên canvas.":" Reload the page (F5) to see them on the canvas."," Tải lại trang (F5) để thấy chúng.":" Reload the page (F5) to see them."," Vật nào bị nhận nhầm: bấm \"Loại trừ\" ở dòng của nó để SAM3 bỏ mọi vật giống vậy.":" If an object is wrong: press \"Exclude\" on its row so SAM3 drops every object like it."," {0} lưu ý cho cả job.":" {0} notes for the whole job."," {0} điểm":" {0} points"," · có nhãn do tool tạo":" · has tool-created labels"," · khuất {0} lần":" · occluded {0} times"," · model đang nghỉ":" · model idle"," · {0} camera, hướng {1}":" · {0} cameras, heading {1}"," · {0} có thể sót":" · {0} possibly missed"," · {0} đang ẩn":" · {0} hidden"," · đã dọn {0} lần":" · cleaned up {0} times"," Đang loại trừ {0} mẫu.":" Excluding {0} examples."," Đang tải lại trang để CVAT nhận label mới…":" Reloading the page so CVAT picks up the new labels…"," Đang tải lại trang…":" Reloading the page…"," ở frame {0}":" at frame {0}","'{0}' → {1}.{2} = {3} ({4} lần)":"'{0}' → {1}.{2} = {3} ({4} times)","(đang tắt) ":"(off) ","+ thêm cỡ":"+ add size",", bỏ qua {0} frame {1}":", skipped {0} frames that {1}",", chạy trên máy":", running locally",", {0} chỗ có thể sót":", {0} possibly missed",", {0} label cần bạn xem":", {0} labels need your review",", ảnh sẽ rời máy bạn":", images will leave your machine","10–12 GB VRAM: kiểm tra dày hơn khi theo dõi, đủ prompt, tìm lại người bị mất":"10–12 GB VRAM: denser checks when tracking, all prompts, finds lost people again","16 GB VRAM trở lên: chạy SAM3 mọi frame, giữ mọi model trên GPU":"16 GB VRAM or more: SAM3 on every frame, all models kept on the GPU","6–8 GB VRAM: theo dõi thông minh, 2 prompt mỗi label":"6–8 GB VRAM: smart tracking, 2 prompts per label","; bỏ: {0}":"; dropped: {0}","API key (nếu backend có đặt)":"API key (if the backend has one)","API key, phím tắt, nguồn nhãn…":"API key, shortcuts, label source…","Alt+{0} trùng phím của trình duyệt/Windows (menu, quay lại trang...). Chọn phím khác.":"Alt+{0} clashes with a browser/Windows shortcut (menu, page back...). Choose another key.","Backend bản {0} cũ hơn panel bản {1}. Cập nhật backend (cài app bản mới, hoặc git pull nếu chạy từ mã nguồn) rồi khởi động lại backend.":"Backend version {0} is older than panel version {1}. Update the backend (install the new app, or git pull when running from source), then restart the backend.","Backend chưa sẵn sàng: mở Cài đặt để kiểm tra kết nối":"Backend not ready: open Settings to check the connection","Backend không phản hồi sau 5 phút.":"The backend did not respond within 5 minutes.","Backend không phản hồi sau {0} phút.":"The backend did not respond after {0} minutes.","Backend kết nối tốt ({0} trên {1}), nhưng {2}":"Backend connection OK ({0} on {1}), but {2}","Backend trả lỗi {0}. ":"Backend returned error {0}. ","Backend đang chạy chế độ GIẢ LẬP (mock): kết quả không phải của SAM3, chỉ để thử giao diện.":"The backend is running in MOCK mode: results are not from SAM3, for trying the UI only.","Backend ở máy chia sẻ đang chạy bản {0}, app này là bản {1}. Nhờ người quản lý máy đó cập nhật app cho cùng bản; trong lúc chờ, vài tính năng có thể không chạy.":"The shared machine's backend runs version {0}, this app is version {1}. Ask whoever manages that machine to update to the same version; until then some features may not work.","Backend: {0} trên {1}{2}":"Backend: {0} on {1}{2}","Biên?":"Edge?","Bài học từ các lần trước đã dùng ({0})":"Lessons from previous runs used ({0})","Bước 1 · Thiết lập nhanh":"Step 1 · Quick setup","Bước 2 · Chạy dựng & Kiểm duyệt":"Step 2 · Build & audit","Bước 3 · Duyệt & Ghi":"Step 3 · Review & write","Bạn chọn":"You choose","Bạn đánh tay":"You annotate","Bản nháp":"Draft","Bản đồ nhìn từ trên":"Top-view map","Bảng cỡ, phạm vi…":"Sizes, ranges…","Bảng cỡ: thêm dòng theo cuboid của bạn cho {0}":"Size table: add a row from your cuboids for {0}","Bấm \"Ghi\" để lưu vào CVAT, hoặc Alt+Enter để ghi rồi sang frame tiếp: frame sau đã được chạy sẵn.":"Press \"Save\" to store in CVAT, or Alt+Enter to save and go to the next frame: it is already pre-computed.","Bấm \"Gợi ý nhãn cho frame này\" (hoặc Alt+G). SAM3 đề xuất nhãn, hiện dạng xem trước trên ảnh.":"Press \"Suggest labels for this frame\" (or Alt+G). SAM3 suggests labels, shown as a preview on the image.","Bấm lên vật cần tách. Bấm phải (hoặc Shift+bấm) vào chỗ KHÔNG thuộc vật để sửa. Enter hoặc \"Xong\" để giữ, Esc để huỷ.":"Click on the object to segment. Right-click (or Shift+click) on areas NOT part of the object to correct. Enter or \"Done\" to keep, Esc to cancel.","Bấm một dòng để xem trên ảnh · {0} bật/tắt dòng đang chọn · giữ Alt rồi bấm lên ảnh để tick":"Click a row to see it on the image · {0} toggles the selected row · hold Alt and click the image to tick","Bấm một điểm lên vật (bấm thêm điểm để sửa), SAM3 tách đúng vật đó":"Click a point on the object (more points to correct) and SAM3 segments exactly that object","Bấm phím muốn dùng (cùng Alt)":"Press the key to use (with Alt)","Bấm điểm":"Click points","Bấm điểm: {0} điểm thuộc vật, {1} điểm loại trừ.":"Click points: {0} on the object, {1} excluded.","Bấm để đổi occluded / visible":"Click to switch occluded / visible","Bấm để ẩn class này":"Click to hide this class","Bật":"On","Bật label này":"Enable this label","Bắt đầu dựng Cuboid 3D ({0} frame)":"Start building 3D cuboids ({0} frames)","Bỏ":"Discard","Bỏ (loại) đề xuất đang chọn":"Drop (reject) the selected suggestion","Bỏ chọn Nghi rác ({0})":"Deselect suspected noise ({0})","Bỏ chọn hết":"Select none","Bỏ chốt":"Unanswer","Bỏ dòng cỡ này":"Remove this size row","Bỏ kết quả":"Discard results","Bỏ label này":"Remove this label","Bỏ mục có cờ":"Untick flagged","Bỏ qua":"Skip","Bỏ qua frame đã có bất kỳ nhãn nào":"Skip frames with any annotation","Bỏ qua label đã có trên frame (chỉ gợi ý label frame còn thiếu)":"Skip labels already on the frame (only suggest labels the frame is missing)","Bỏ tick cả nhóm":"Untick group","Bỏ tick thùng rác, container, nhà vệ sinh… rồi Ghi: tool nhớ cỡ của chúng và tự bỏ tick lần sau.":"Untick dumpsters, containers, toilets… then Save: the tool remembers their size and unticks them next time.","Bỏ tiến độ cũ":"Discard old progress","Bỏ track có cờ":"Untick flagged tracks","Bỏ vùng này":"Remove this zone","Bỏ {0}":"Remove {0}","Bỏ điểm cuối":"Remove last point","Bỏ đề xuất":"Discard","Bỏ?":"Drop?","CSS selector của ảnh nền canvas CVAT":"CSS selector of the CVAT canvas image","CVAT không trả về label nào cho job này, nên tool chưa gợi ý được.":"CVAT returned no labels for this job, so the tool cannot suggest yet.","CVAT không trả về label nào cho job này. Xem chi tiết ở tab Label.":"CVAT returned no labels for this job. See details in the Labels tab.","CVAT nhận yêu cầu nhưng chưa thấy label: {0}. Kiểm tra lại phần label của task.":"CVAT accepted the request but the labels are not there: {0}. Check the task's labels.","CVAT nhận yêu cầu nhưng vẫn còn {0} nhãn semi-auto trong {1}. Máy chủ có thể không cho đổi trường này.":"CVAT accepted the request but {0} semi-auto labels remain in {1}. The server may not allow changing this field.","CVAT trả lỗi {0} cho {1} {2}. {3}":"CVAT returned error {0} for {1} {2}. {3}","Ca khó trong ảnh mẫu ({0})":"Hard cases in sample images ({0})","Cao (m): phải khớp, tool bỏ cụm sai chiều cao":"Height (m): must match; clusters with the wrong height are dropped","Chi tiết ({0})":"Details ({0})","Cho AI đọc guideline (gửi VĂN BẢN tới {0}{1}, không gửi ảnh)":"Let AI read the guideline (sends the TEXT to {0}{1}, no images)","Cho LLM bổ sung prompt tiếng Anh và định nghĩa (gửi VĂN BẢN guideline tới {0}{1})":"Let the LLM add English prompts and definitions (sends the guideline TEXT to {0}{1})","Cho phép gửi ảnh mẫu tới LLM ({0}{1})":"Allow sending sample images to the LLM ({0}{1})","Chuyển file vào profiles/archive/, hiện lại được":"Move the file to profiles/archive/; can be shown again","Chuyển frame là có đề xuất ngay":"Suggestions are ready when you switch frames","Chép JSON label":"Copy label JSON","Chép cả bộ label dạng JSON để dán vào tab Raw khi tạo task mới":"Copy the whole label set as JSON to paste into the Raw tab when creating a new task","Chưa bật AI soạn guideline: bản nháp vẫn tạo được từ số liệu, guideline mẫu và bài học đã có, nhưng class mới bạn tự viết định nghĩa.":"AI guideline drafting is off: drafts are still built from figures, sample guidelines and existing lessons, but you write definitions for new classes yourself.","Chưa bật AI soạn guideline: tool đọc guideline bằng luật (bảng, câu chữ). Bật ở app: Cài đặt → AI soạn guideline.":"AI guideline drafting is off: the tool reads the guideline with rules (tables, wording). Turn it on in the app: Settings → AI guideline drafting.","Chưa chọn ai: tool theo dõi mọi người tìm được. Chọn người ở frame họ rõ nhất để tool bỏ người khác, phản chiếu, quảng cáo.":"Nobody chosen: the tool follows everyone it finds. Choose people on the frame where they are clearest so the tool drops others, reflections and ads.","Chưa chọn label nào để gợi ý.":"No labels chosen to suggest.","Chưa có bước kiểm nào chạy được (camera, Claude): mọi track chỉ dựa vào kích thước nên đều ở rổ \"Cần xem\".":"No check could run (camera, Claude): every track is based on size only, so all are in \"To review\".","Chưa có cuboid nào bạn đã đánh trong job này. Đánh tay vài frame rồi bấm lại.":"You have not drawn any cuboids in this job yet. Draw a few frames by hand, then try again.","Chưa có dữ liệu. Ghi vài nhãn bằng tool, sửa chúng như bình thường trong CVAT, lưu lại, rồi mở lại job (hoặc bấm Đối chiếu).":"No data yet. Save a few labels with the tool, edit them as usual in CVAT, save, then reopen the job (or press Review).","Chưa có kết quả: bấm \"Bắt đầu dựng Cuboid 3D\" ở bước 2.":"No result yet: press \"Start building 3D cuboids\" in step 2.","Chưa có profile nào bị ẩn.":"No hidden profiles.","Chưa có đề xuất cho frame này":"No suggestions for this frame yet","Chưa dò được hướng camera: thử lại ở frame có vài xe gần.":"Camera heading not detected yet: try a frame with a few nearby cars.","Chưa kiểm bằng ảnh camera":"Not checked with camera images","Chưa kiểm tra backend":"Backend not checked yet","Chưa nối backend":"No backend","Chưa nối được backend.":"Backend not connected yet.","Chưa thấy cần chỉnh gì.":"Nothing to adjust yet.","Chưa tick cuboid nào.":"No cuboid ticked.","Chưa tick track nào.":"No tracks ticked.","Chưa tick đề xuất nào (đề xuất đang bị ẩn bởi bộ lọc không được ghi).":"No suggestions ticked (suggestions hidden by filters are not saved).","Chưa đọc được label của job.":"Could not read the job's labels yet.","Chạy \"Phân tích frame của job\" ở tab Cả job để tool chọn theo độ đa dạng của ảnh.":"Run \"Analyse job frames\" in the Whole job tab so the tool picks by image variety.","Chạy SAM3 trên":"Run SAM3 on","Chạy gợi ý lần lượt cho mọi frame trong khoảng. Kết quả được giữ lại: mở frame nào thì tab Gợi ý hiện ngay đề xuất của frame đó.":"Runs suggestions for every frame in the range. Results are kept: open any frame and the Suggest tab shows its suggestions right away.","Chạy mọi kiểm tra ở trên cho tất cả frame của job, cộng: frame chưa có nhãn, label chưa dùng lần nào. Lưu (Ctrl+S) trước khi rà.":"Runs every check above on all frames of the job, plus: frames without labels, labels never used. Save (Ctrl+S) before checking.","Chạy nền, vẫn gán nhãn được trong lúc chờ":"Runs in the background; you can keep labelling while waiting","Chạy sẵn gợi ý cho frame tiếp theo":"Pre-run suggestions for the next frame","Chạy theo lô có lưu tiến độ: dừng giữa chừng vẫn giữ phần đã chạy":"Runs in batches and saves progress: stopping midway keeps what has run","Chất lượng":"Quality","Chế độ reviewer":"Reviewer mode","Chỉ chạy SAM3 trên các frame có thay đổi đáng kể (ra shape trên từng frame, không phải track)":"Run SAM3 only on frames with significant change (gives shapes per frame, not tracks)","Chỉ có đề xuất yếu ở frame này.":"Only weak suggestions in this frame.","Chỉ ghi {0} nhãn":"Save {0} only","Chỉ job này":"This job only","Chỉ job {0}":"Job {0} only","Chỉ label chưa có trong job":"Only labels not yet in the job","Chỉ đổi khi phần xem trước không hiện. Mặc định: #cvat_canvas_background":"Only change this if the preview does not show. Default: #cvat_canvas_background","Chỉnh hộp: {0}":"Box adjustment: {0}","Chỉnh điểm skeleton: {0}":"Adjust skeleton points: {0}","Chọn các label chưa có nhãn nào trong job (ví dụ label vừa thêm theo guideline mới)":"Select labels with no annotations in the job (e.g. labels just added for a new guideline)","Chọn file guideline (.docx, .md, .txt) và/hoặc file taxonomy (.json, ví dụ JSON label ở tab Raw của task CVAT); chọn cả hai cùng lúc được. Tool đọc bảng taxonomy, định nghĩa, Issue type, checklist, tiêu chí reviewer; bạn kiểm tra rồi lưu. Sau đó tool có profile gợi ý nhãn, checklist ở tab Kiểm tra, và dùng guideline này khi soạn nháp.":"Choose a guideline file (.docx, .md, .txt) and/or a taxonomy file (.json, e.g. the label JSON in the Raw tab of a CVAT task); you can choose both at once. The tool reads the taxonomy table, definitions, Issue types, checklist and reviewer criteria; you check and save. The tool then has a suggestion profile, a checklist in the Check tab, and uses this guideline when drafting.","Chọn hết":"Select all","Chọn hết Tin cậy cao ({0})":"Select all high confidence ({0})","Chọn người ở frame này":"Choose a person on this frame","Chọn skeleton của guideline…":"Choose the guideline skeleton…","Chọn theo VRAM của GPU backend":"Chosen from the backend GPU's VRAM","Chọn đề xuất tiếp theo":"Select next suggestion","Chọn đề xuất trước":"Select previous suggestion","Class dễ nhầm với vật cố định: cần xem":"Class easily confused with fixed objects: needs review","Claude AI: bật":"Claude AI: on","Claude AI: chưa có API key":"Claude AI: no API key","Claude AI: tắt (bạn đã tắt)":"Claude AI: off (you turned it off)","Claude chọn":"Chosen by Claude","Claude tìm thêm":"Found by Claude","Claude tóm":"summarised by Claude","Claude xem ảnh camera có khung chiếu của từng cuboid: bỏ vật không phải xe / người, sửa class":"Claude looks at the camera images with each cuboid's projected box: drops objects that are not vehicles / people, fixes classes","Claude xem ảnh: chưa bật (cần Anthropic API key trong Cài đặt của app)":"Claude image review: off (needs an Anthropic API key in the app's Settings)","Claude xem ảnh: duyệt theo guideline, điền thuộc tính, tag, tìm vật sót{0}":"Claude reviews the image: checks against the guideline, fills attributes and tags, finds missed objects{0}","Claude xong: {0} cần xem, {1} nghi rác":"Claude finished: {0} to review, {1} suspected noise","Claude đang tóm bài học…":"Claude is summarising lessons…","Claude đề nghị bỏ":"Claude suggests dropping","Claude đọc các lần bạn sửa và rút ra vài luật ngắn":"Claude reads your corrections and writes a few short rules","Claude: bỏ":"Claude: drop","Ctrl+Enter để gửi · Esc để huỷ":"Ctrl+Enter to send · Esc to cancel","Cài / chọn model ở trang Thành phần của app":"Install / choose models on the app's Components page","Cài bản mới":"Install new version","Cài đặt panel":"Panel settings","Các cách đã thử:":"Attempts made:","Cách theo dõi":"Tracking method","Cách thường: SAM3 từng frame, nối ID theo vị trí và màu":"Regular: SAM3 per frame, IDs linked by position and colour","Cách vẽ vạch":"How to draw markings","Cân bằng":"Balanced","Câu hỏi cần chốt (còn {0}/{1})":"Questions to decide ({0}/{1} left)","Có thể bạn đã sót vật":"You may have missed an object","Có thể sót \"{0}\" (SAM3 tin {1}%, chưa có nhãn nào ở đó).":"\"{0}\" may be missing (SAM3 {1}% confident, no label there yet).","Có {0} bài học cho project. Xoá luật nào không đúng.":"The project has {0} lessons. Delete any rule that is wrong.","Công cụ job 3D":"3D job tools","Cả job":"Whole job","Cảnh báo":"Warning","Cần API key Anthropic trong Cài đặt của app":"Needs an Anthropic API key in the app Settings","Cần Anthropic API key trong Cài đặt của app":"Needs an Anthropic API key in the app's Settings","Cần thêm dữ liệu ({0}/12 đề xuất đã có kết quả).":"More data needed ({0}/12 suggestions with known outcome).","Cần xem":"Needs review","Cần xem:":"Review:","Cần ít nhất 8 ảnh":"At least 8 images needed","Cỡ (m): dài · rộng · cao":"Size (m): length · width · height","Cỡ chữ {0}%":"Font size {0}%","Cụm điểm":"Point clusters","Của bạn":"Yours","Duyệt dạng lưới ảnh ({0} frame)":"Review as a thumbnail grid ({0} frames)","Duyệt từng đề xuất: tick để giữ, đổi class nếu sai. Alt+J / Alt+K chọn, Alt+X tick, giữ Alt rồi bấm lên ảnh cũng tick được.":"Review each suggestion: tick to keep, change the class if wrong. Alt+J / Alt+K select, Alt+X ticks; holding Alt and clicking the image also ticks.","Dài (m)":"Length (m)","Dò lại":"Detect again","Dùng":"Use","Dùng Claude AI (chưa có API key)":"Use Claude AI (no API key)","Dùng Claude AI cho frame này (chậm hơn vài giây, tốn lượt gọi)":"Use Claude AI for this frame (a few seconds slower, uses calls)","Dùng Claude AI hỗ trợ":"Use Claude AI assistance","Dùng app trên máy này thì không cần sửa. Colab / máy khác: dán địa chỉ https của tunnel.":"No change needed when using the app on this machine. Colab / another machine: paste the tunnel's https address.","Dùng bản nháp guideline vừa soạn (mục Soạn guideline nháp bên dưới)":"Use the guideline draft just written (Draft a guideline section below)","Dùng cho job này":"Use for this job","Dùng danh sách label của task đang mở làm taxonomy":"Use the open task's label list as the taxonomy","Dùng làm profile":"Use as profile","Dùng model riêng cho các class đạt chuẩn khi gợi ý":"Use the custom model for classes that reach the bar when suggesting","Dùng điều tool đã học khi gợi ý (ngưỡng, chỉnh hộp)":"Use what the tool learned (thresholds, box adjustments)","Dừng":"Stop","Dữ liệu model riêng: {0} ảnh, {1} hộp.":"Custom model data: {0} images, {1} boxes.","Dữ liệu thông minh":"Smart data","Dữ liệu: {0} ảnh, {1} hộp{2}":"Data: {0} images, {1} boxes{2}","Dựng cuboid xong":"Cuboids built","Dựng cuboid: hình học (chưa có model 3D)":"Build cuboids: geometry (no 3D model yet)","Dựng cuboid: model 3D (CenterPoint)":"Build cuboids: 3D model (CenterPoint)","Dựng track cho {0} frame":"Build tracks for {0} frames","Enter để đồng ý · Esc để huỷ":"Enter to confirm · Esc to cancel","Frame cần duyệt:":"Frames to review:","Frame lệch nhiều:":"Frames with large differences:","Frame mỗi lô:":"Frames per batch:","Frame nào chạy SAM3":"Which frames run SAM3","Frame này":"This frame","Frame {0} có {1} \"{2}\", frame này mới có {3}: có thể còn sót {4}.":"Frame {0} has {1} \"{2}\", this frame only has {3}: {4} may be missing.","Frame {0}: {1}/{2} frame, {3} đề xuất. Còn khoảng {4}. Bấm Dừng để tạm dừng, tiếp tục sau (kể cả khi tắt trình duyệt).":"Frame {0}: {1}/{2} frames, {3} suggestions. About {4} left. Press Stop to pause and continue later (even after closing the browser).","Frame đã có nhãn":"Frames already labelled","Ghi cuboid này":"Save this cuboid","Ghi các đề xuất đã tick":"Save ticked suggestions","Ghi cả mục có cờ":"Include flagged items","Ghi lại cách tôi xử lý đề xuất để tool học":"Record how I handle suggestions so the tool can learn","Ghi nhãn này vào CVAT ({0}, hoặc giữ Alt rồi bấm lên ảnh)":"Save this label to CVAT ({0}, or hold Alt and click the image)","Ghi nhận sai sót sau khi dùng guideline":"Record a mistake found after using the guideline","Ghi nhận để lần sau không lặp lại":"Record so it does not happen again","Ghi rồi sang frame tiếp (đề xuất đã chạy sẵn)":"Save, then go to the next frame (suggestions pre-computed)","Ghi tag này vào CVAT":"Save this tag to CVAT","Ghi thành track: mỗi đối tượng một ID qua các frame, CVAT nội suy giữa các keyframe. Label: {0}. Profile: {1}.":"Saves as tracks: one ID per object across frames, CVAT interpolates between keyframes. Labels: {0}. Profile: {1}.","Ghi track này":"Save this track","Ghi {0} nhãn":"Save {0} labels","Ghi {0} nhãn trên {1} frame vào job {2}?{3}":"Save {0} labels on {1} frames to job {2}?{3}","Ghi {0} nhãn vào CVAT":"Save {0} labels to CVAT","Ghi {0} nhãn vào frame {1} của job {2}?{3}":"Save {0} labels to frame {1} of job {2}?{3}","Ghi {0} track vào CVAT":"Save {0} tracks to CVAT","Ghi {0} track vào job {1}?{2}":"Save {0} tracks to job {1}?{2}","Ghi {0} track đã chọn vào CVAT":"Write {0} selected tracks to CVAT","Ghi {0}, sang frame tiếp":"Save {0}, next frame","Giả lập":"Mock","Giải thích":"Explanation","Giống chỉnh đồ hoạ trong game: mức cao chính xác hơn nhưng tốn VRAM và chậm hơn. Backend tự nhả model không dùng tới để không chậm dần.":"Like game graphics settings: higher levels are more accurate but use more VRAM and are slower. The backend releases unused models so it does not slow down over time.","Giống vật bạn đã bỏ trước đây":"Looks like an object you dropped before","Giữ / bỏ mọi đề xuất của frame này":"Keep / drop all suggestions of this frame","Giữ {0} · sửa hình {1} · đổi class {2} · xoá {3} · bỏ tick {4} · bạn tự vẽ thêm {5}":"Kept {0} · reshaped {1} · class changed {2} · deleted {3} · unticked {4} · drawn by you {5}","Guideline \"{0}\" đã được nạp trước đây. Ghi đè bản cũ (cả profile gl_{1}, nếu bạn đã sửa tay)?":"Guideline \"{0}\" was imported before. Overwrite the old version (including profile gl_{1}, if you edited it by hand)?","Guideline (tuỳ chọn)":"Guideline (optional)","Guideline sẽ ghi:\n{0}":"The guideline will say:\n{0}","Guideline {0}: {1} mục checklist, {2} loại issue (dùng ở tab Kiểm tra).":"Guideline {0}: {1} checklist items, {2} issue types (used in the Check tab).","Guideline đã nạp":"Imported guidelines","Gõ thứ cần tìm (tiếng Anh), ví dụ: motorbike carrying boxes":"Type what to look for (English), e.g.: motorbike carrying boxes","Gọn":"Compact","Gộp?":"Merged?","Gỡ":"Remove","Gỡ guideline \"{0}\" khỏi tool? File được chuyển vào thư mục archive, không bị xoá hẳn.":"Remove guideline \"{0}\" from the tool? The file is moved to the archive folder, not deleted.","Gợi ý":"Suggest","Gợi ý (chưa tự áp dụng)":"Notes (not applied automatically)","Gợi ý cho cả job":"Suggest for whole job","Gợi ý cho {0} frame{1}":"Suggest for {0} frames{1}","Gợi ý cuboid frame này":"Suggest cuboids for this frame","Gợi ý cả job chỉ trên keyframe":"Suggest for whole job on keyframes only","Gợi ý cả job xong":"Whole-job suggestions finished","Gợi ý nhãn cho frame này":"Suggest labels for this frame","Gợi ý được một phần":"Partly suggested","Gửi":"Send","Hai job không có frame chung nào có nhãn (job {0}: {1} frame, job {2}: {3} frame).":"The two jobs share no labelled frames (job {0}: {1} frames, job {2}: {3} frames).","Hay gán khác nhau: {0}":"Often labelled differently: {0}","Hiển thị":"Display","Hiện cả {0} track không chọn":"Show all {0} unticked tracks","Hiện lại":"Show","Hiện {0} đề xuất yếu":"Show {0} weak suggestions","Hoàn tác":"Undo","Hoàn tác ({0})":"Undo ({0})","Hoàn tác lần ghi gần nhất":"Undo last save","Hoặc kéo mép trái panel để đổi độ rộng, mép dưới để đổi chiều cao. Bấm đúp mép để về cỡ mặc định.":"Or drag the panel's left edge to change the width and its bottom edge to change the height. Double-click an edge to go back to the default size.","Huấn luyện":"Train","Huấn luyện lúc {0} trên {1}, {2} ảnh học, {3} ảnh kiểm.":"Trained at {0} on {1}, {2} training images, {3} test images.","Huấn luyện model riêng xong":"Custom model training finished","Huỷ":"Cancel","Hãy mô tả sai sót cụ thể hơn.":"Please describe the mistake more specifically.","Hôm nay":"Today","Hôm nay: {0} nhãn, {1} frame, ước tính tiết kiệm {2} phút.":"Today: {0} labels, {1} frames, about {2} minutes saved.","Hướng camera đã dò: lệch {0}° so với trục point cloud.":"Detected camera heading: offset {0}° from the point cloud axes.","Hướng dẫn":"Tour","Hướng dẫn {0}/{1}":"Tour {0}/{1}","Hệ toạ độ":"Coordinates","Hỏi lại trước khi ghi":"Ask before saving","Hộp quá nhỏ, hãy vẽ lại.":"Box too small, please draw again.","Hộp đã được chỉnh theo cách bạn hay sửa (tab Tự học).":"The box was adjusted the way you usually fix it (Learning tab).","Job cuboid 3D. Bảng cỡ {0}; sửa ô nào thì tool dùng số của bạn cho task này. Chiều cao phải khớp bảng; dài / rộng được nhỏ hơn (vật bị che) nhưng không lớn hơn.":"Cuboid 3D job. Size table {0}; any cell you edit is used for this task. Height must match the table; length / width may be smaller (occluded) but not larger.","Job có {0} track; tool chỉ kiểm tra shape, chưa kiểm tra track.":"The job has {0} tracks; the tool only checks shapes, not tracks yet.","Job không có label rectangle nào để theo dõi.":"The job has no rectangle labels to track.","Job này chưa có nhãn hộp, polygon hay mask nào để học.":"This job has no boxes, polygons or masks to learn from yet.","Job này chưa có đề xuất nào do tool ghi (hoặc bạn đã tắt ghi nhận).":"This job has no suggestions saved by the tool yet (or you turned recording off).","Job này đang dùng: {0}{1}":"This job uses: {0}{1}","Job video: theo dõi để có track mỗi đối tượng một ID; keyframe do tool chọn theo chuyển động":"Video job: track so each object gets one track ID; the tool picks keyframes by motion","Job {0}: {1} cần xem, {2} nghi rác, {3} tin cậy cao.":"Job {0}: {1} to review, {2} suspected noise, {3} high confidence.","Job {0}: {1} frame, {2} lỗi, {3} cảnh báo.":"Job {0}: {1} frames, {2} errors, {3} warnings.","Job {0}: {1} frame, {2} đề xuất, {3} mục có cờ.":"Job {0}: {1} frames, {2} suggestions, {3} flagged items.","Job {0}: {1} track, {2} track có cờ.":"Job {0}: {1} tracks, {2} flagged tracks.","Job đã có {0} track. Track mới có thể trùng với track cũ.":"The job already has {0} tracks. New tracks may duplicate old ones.","Keyframe dày":"Dense keyframes","Keyframe theo chuyển động: {0}/{1} frame":"Motion keyframes: {0}/{1} frames","Keyframe: {0}/{1} frame khác hẳn nhau (ảnh rời, không phải video), nên không lọc bớt được. Dùng \"Gợi ý cả job\" như bình thường.":"Keyframes: {0}/{1} frames all differ (separate images, not a video), so nothing can be skipped. Use \"Suggest whole job\" as usual.","Khi duyệt":"While reviewing","Khi ghi vào CVAT":"When saving to CVAT","Khi task đã đủ label và bạn đã bấm \"Dùng làm profile\": tool gán nhãn được cho mọi frame (ảnh, skeleton) hoặc theo dõi (video).":"Once the task has all labels and you have pressed \"Use as profile\": the tool can label every frame (images, skeletons) or track (video).","Khoanh gương, kính, biển quảng cáo, màn hình: mọi phát hiện có tâm trong vùng bị bỏ, cho mọi job của task này.":"Outline mirrors, glass, billboards, screens: every detection centred inside is dropped, for all jobs of this task.","Khoanh vùng bỏ qua":"Outline an ignore zone","Khoảng frame không hợp lệ.":"Invalid frame range.","Khác với \"học từ cách bạn sửa\" ở trên: đây là một detector nhỏ được huấn luyện từ chính các nhãn đã duyệt (hộp, polygon, mask). Class nào đạt chuẩn (đủ ví dụ, đúng ≥ 70%, tìm được ≥ 60% trên ảnh giữ lại để kiểm) mới được dùng, nhanh hơn SAM3 nhiều lần; class khác vẫn do SAM3 làm.":"Different from \"learn from how you fix\" above: this is a small detector trained on your reviewed labels (boxes, polygons, masks). Only classes that pass (enough examples, ≥ 70% correct, ≥ 60% found on held-out test images) are used, many times faster than SAM3; SAM3 still handles the rest.","Khó quan sát":"Hard to see","Khó thấy":"Hard to see","Không bỏ qua (đề xuất trùng nhãn cũ bị bỏ tick)":"Do not skip (suggestions duplicating existing labels are unticked)","Không chắc biên":"Uncertain boundary","Không chắc class":"Uncertain class","Không chắc phạm vi":"Uncertain scope","Không có gì cần chỉnh.":"Nothing to tune.","Không có lần ghi nào của job này để hoàn tác.":"No saves in this job to undo.","Không có lỗi nào để tạo Issue.":"No errors to create Issues for.","Không có nhãn semi-auto nào trong {0}.":"No semi-auto labels in {0}.","Không có trong guideline":"Not in the guideline","Không có trên frame đang xem.":"Not on the current frame.","Không có đề xuất nào sẵn sàng để ghi.":"No suggestions ready to save.","Không kết nối được backend tại {0}. Kiểm tra backend đã chạy và địa chỉ trong Cài đặt panel (nút bánh răng).":"Cannot connect to the backend at {0}. Check the backend is running and the address in Panel settings (gear button).","Không thấy lỗi đo được bằng máy. Các mục \"Tự rà\" vẫn cần bạn kiểm tra.":"No machine-measurable errors found. The \"Self-check\" items still need your review.","Không tách được vật ở các điểm này. Thử bấm vào giữa vật.":"Could not segment an object at these points. Try clicking the middle of the object.","Không tìm thấy canvas của CVAT nên không bấm điểm được.":"CVAT canvas not found, so points cannot be clicked.","Không tìm thấy canvas của CVAT nên không vẽ hộp được. Xem mục \"Xử lý sự cố\" trong README.":"CVAT canvas not found, so a box cannot be drawn. See \"Troubleshooting\" in the README.","Không tìm thấy ô số frame. Hãy tự chuyển tới frame {0}.":"Frame number box not found. Please go to frame {0} yourself.","Không áp điều đã học cho label này (vẫn giữ dữ liệu)":"Do not apply what was learned for this label (data is kept)","Không đọc được file.":"Cannot read the file.","Khớp":"Matched","Kiểm luật QA trước khi ghi":"Check QA rules before saving","Kiểm tra":"Check","Kiểm tra ID":"Check ID","Kiểm tra annotation của frame này":"Check annotations on this frame","Kiểm tra attribute":"Check attributes","Kiểm tra dữ liệu job 3D":"Check 3D job data","Kiểm tra dữ liệu · frame {0}":"Data check · frame {0}","Kiểm tra kết nối":"Test connection","Kiểm tra occluded":"Check occluded","Kiểm tra trước khi ghi: {0} lỗi trên các đề xuất đã tick.\n\n{1}\n\nVẫn ghi?":"Pre-save check: {0} errors on the ticked suggestions.\n\n{1}\n\nSave anyway?","Kéo chuột khoanh vùng cần bỏ qua (gương, kính, biển quảng cáo, màn hình). Esc để huỷ.":"Drag to outline an area to ignore (mirror, glass, billboard, screen). Esc to cancel.","Kéo chuột trên ảnh để vẽ một hộp quanh đối tượng. Esc để huỷ.":"Drag on the image to draw a box around the object. Esc to cancel.","Kéo chuột vẽ hộp quanh người cần theo dõi (chọn ở frame người đó rõ nhất). Esc để huỷ.":"Drag a box around the person to follow (pick the frame where they are clearest). Esc to cancel.","Kéo thanh \"Ngưỡng\" để ẩn đề xuất điểm thấp, bấm tên class để ẩn/hiện cả class.":"Drag the \"Threshold\" slider to hide low-score suggestions; click a class name to hide/show the whole class.","Kéo để đổi chiều cao panel (bấm đúp: về mặc định)":"Drag to change the panel height (double-click: default)","Kéo để đổi độ rộng panel (bấm đúp: về mặc định)":"Drag to change the panel width (double-click: default)","Kích thước panel":"Panel size","Kết nối":"Connect","Kết nối tốt: {0} trên {1}, phiên bản {2}. Đã đọc {3} label.":"Connection OK: {0} on {1}, version {2}. Read {3} labels.","Kết quả quá lớn để giữ lại khi tải lại trang. Hãy ghi hoặc duyệt trước khi tải lại.":"Result too large to keep across a page reload. Save or review it before reloading.","Label cho hộp vẽ tay":"Label for hand-drawn boxes","Label chưa có cỡ":"Labels without sizes","Label cần gợi ý ({0}/{1}):":"Labels to suggest ({0}/{1}):","Label lấy từ task CVAT. Prompt nên là cụm danh từ tiếng Anh ngắn. Thay đổi được nhớ theo tên label.":"Labels come from the CVAT task. Prompts should be short English noun phrases. Changes are remembered by label name.","Label nào trong job cũng đã có nhãn; chọn tay label cần gợi ý.":"Every label in the job already has annotations; choose the labels to suggest by hand.","Label này là skeleton nào của guideline":"Which guideline skeleton this label is","Loại bài toán (tool đoán: {0})":"Task type (tool guess: {0})","Loại shape":"Shape type","Loại trừ":"Exclude","Lưu cho project":"Save for project","Lưu vào tool":"Save to tool","Lượt \"Tìm tương tự\" này đã cũ. Vẽ lại hộp mẫu.":"This \"Find similar\" session is stale. Draw the example box again.","Lượt gợi ý cả job đang tạm dừng: còn {0}/{1} frame.":"The whole-job run is paused: {0}/{1} frames left.","Lấy label từ task đang mở":"Take labels from the open task","Lần trước dừng giữa chừng: đã có {0} frame. Bấm Theo dõi để chạy tiếp (cùng khoảng frame, label). ":"Last run stopped midway: {0} frames already done. Click Track to continue (same frame range, labels). ","Lọc rác bằng ảnh: RT-DETR":"Noise filter with images: RT-DETR","Lọc rác bằng ảnh: SAM3":"Noise filter with images: SAM3","Lọc rác bằng ảnh: SAM3 (luôn nạp)":"Noise filter with images: SAM3 (always load)","Lọc rác bằng ảnh: SAM3 nếu đủ VRAM":"Noise filter with images: SAM3 if enough VRAM","Lọc rác bằng ảnh: chưa có bộ dò 2D":"Noise filter with images: no 2D detector yet","Lọc rác bằng ảnh: tắt (chưa có RT-DETR, SAM3 đã tắt trong Cài đặt)":"Noise filter with images: off (no RT-DETR, SAM3 turned off in Settings)","Lọc rác bằng ảnh: đang bỏ qua":"Noise filter with images: skipped","Lỗ hổng tool tự phát hiện ({0})":"Gaps the tool found ({0})","Lỗi":"Error","Lỗi giữa chừng — kết quả đã chạy vẫn giữ. {0}":"Error midway — the batches already run are kept. {0}","Lỡ tay? \"Hoàn tác\" (Alt+Z) xoá lần ghi gần nhất, bấm tiếp để lùi thêm. Tab Tự học cho biết tool đã học gì từ cách bạn sửa.":"Made a mistake? \"Undo\" (Alt+Z) deletes the last save; press again to go further back. The Learning tab shows what the tool learned from your edits.","Model riêng của dự án":"Project custom model","Muốn AI viết giúp: mở app SAM3 Label Assist trên máy chạy backend → Cài đặt → AI soạn guideline, rồi bật lại backend.":"To let AI help: open the SAM3 Label Assist app on the computer running the backend → Settings → AI guideline drafting, then restart the backend.","Máy chưa có trọng số khởi đầu cho model riêng: huấn luyện sẽ học từ con số 0 và thường ra 0%. Cài thành phần \"Trọng số khởi đầu cho model riêng\" ở trang Thành phần của app trước.":"The starting weights for the custom model are not installed: training starts from scratch and usually scores 0%. Install the \"Starting weights for the custom model\" component on the app's Components page first.","Máy yếu / không GPU: ít lần chạy SAM3, 1 prompt mỗi label, nhả model phụ ngay":"Weak machine / no GPU: fewer SAM3 runs, 1 prompt per label, extra models released right away","Mất?":"Lost?","Mọi frame (chậm nhất)":"Every frame (slowest)","Mỗi lần bạn bấm Ghi, tool nhớ giá trị bạn chốt (thuộc tính, tag, occluded, đề xuất bị bỏ) so với giá trị tool gợi ý. Claude đọc các lần sửa và bài học này ở mọi lần xem ảnh sau; không có Claude thì tool vẫn dùng quy ước tên file và giá trị bạn hay chọn.":"Each time you press Save, the tool remembers the values you settle on (attributes, tags, occluded, dropped proposals) compared with what it suggested. Claude reads these corrections and lessons on every later review; without Claude the tool still uses file-name conventions and the values you usually pick.","Mỗi lần gợi ý gửi ảnh frame (thu nhỏ) và ảnh cắt nhỏ cho Claude":"Each suggestion sends the frame (downscaled) and small crops to Claude","Mỗi vật một track: kích thước khoá theo frame có nhiều điểm nhất, yaw theo hướng đi, bị che tối đa 6 frame thì nội suy và bật occluded.":"One track per object: size locked from the frame with the most points, yaw from the direction of travel, hidden up to 6 frames is interpolated and set occluded.","Một lượt chạy quá lâu, đang thử lại từng frame (tiến độ đã lưu)…":"A run took too long; retrying frame by frame (progress saved)…","Mới thêm":"New","Mở Profile cho project":"Open Project profile","Mở bản nháp để sửa và duyệt":"Open the draft to edit and approve","Mở frame này":"Open this frame","Mở rộng panel":"Expand panel","Mục đích của bộ dữ liệu (model sẽ dùng nhãn này để làm gì)":"Purpose of the dataset (what the model will use these labels for)","Mức cấu hình":"Quality level","Mức cấu hình:":"Quality level:","Nghi rác":"Suspected noise","Nguồn: {0}. Tính trên diện tích toàn thân ước lượng (cả phần bị che), đổi theo từng frame; người quay lại vẫn giữ ID cũ.":"Source: {0}. Measured on the estimated full-body area (including the hidden part), per frame; people who come back keep their ID.","Ngôn ngữ / Language":"Language / Ngôn ngữ","Người cần theo dõi":"People to follow","Ngưỡng":"Threshold","Ngưỡng che khuất":"Occlusion thresholds","Ngưỡng tin cậy":"Confidence threshold","Ngưỡng tin cậy khi tìm người":"Confidence threshold when finding people","Ngưỡng {0} → {1}: {2}":"Threshold {0} → {1}: {2}","Ngưỡng {0}%":"Threshold {0}%","Nhiều cụm bị bỏ vì ít điểm: dùng \"Tự chỉnh theo cuboid bạn đã đánh\" để hạ ngưỡng.":"Many clusters were dropped for too few points: use \"Tune from your cuboids\" to lower the threshold.","Nhiều cụm không khớp class: kiểm tra bảng cỡ ở tab Label (chiều cao phải đúng).":"Many clusters match no class: check the size table in the Label tab (height must be right).","Nhìn từ trên: hướng trước của xe ở trên. Bấm vào một hộp để chọn đề xuất đó.":"Top view: the car's forward direction is up. Click a box to select that suggestion.","Nhảy tới keyframe dày điểm nhất":"Jump to the keyframe with the most points","Nhầm?":"Wrong?","Nhập dạng 3.8–5.0":"Enter like 3.8–5.0","Nhập số job khác (cùng task, cùng frame) để so.":"Enter another job number (same task, same frames) to compare.","Nhập số ảnh mẫu bằng tay":"Enter the number of sample images yourself","Nhắc khi frame có ít vật hơn frame bên cạnh":"Warn when a frame has fewer objects than its neighbors","Nhẹ":"Light","Nâng cao":"Advanced","Nên làm trước ({0} frame, theo thứ tự ưu tiên):":"Do first ({0} frames, in priority order):","Nạp bản nháp gần nhất của job này":"Load this job's latest draft","Nạp guideline (chỉ văn bản, checklist)":"Load a guideline (text and checklist only)","Nạp lại":"Reload","Nạp lại profile":"Reload profiles","Nội dung Issue gửi mentor/reviewer:":"Issue text for the mentor/reviewer:","Outside cả skeleton ({0} điểm)":"Whole skeleton Outside ({0} points)","Panel (userscript) bản {0} cũ hơn backend bản {1}. Bấm \"Cài bản mới\" rồi chọn Update trong Tampermonkey, sau đó tải lại trang.":"The panel (userscript) version {0} is older than backend version {1}. Click \"Install new version\", choose Update in Tampermonkey, then reload the page.","Pha 1/3: Quét LiDAR & dựng Track ({0}/{1} frame)":"Phase 1/3: LiDAR scan & track building ({0}/{1} frames)","Pha 2/3: Đối chiếu camera ({0}/{1} track)":"Phase 2/3: camera cross-check ({0}/{1} tracks)","Pha 3/3: Claude nhận diện & kiểm duyệt ({0}/{1} track)":"Phase 3/3: Claude recognition & audit ({0}/{1} tracks)","Pha 3/3: Claude nhận diện & kiểm duyệt…":"Phase 3/3: Claude recognises & audits…","Phát hiện ({0})":"Findings ({0})","Phân tích frame của job":"Analyse the job's frames","Phân tích lại":"Analyse again","Phân tích ảnh nén của các frame (không dùng GPU): frame nào khác lạ / khó nên làm trước, và keyframe theo chuyển động cho video.":"Analyses compressed frame images (no GPU): which frames are unusual / hard and worth doing first, and motion keyframes for video.","Phím Alt+{0} đang dùng cho \"{1}\".":"Alt+{0} is already used for \"{1}\".","Phím tắt (giữ Alt + phím)":"Shortcuts (hold Alt + key)","Phạm vi":"Scope","Phạm vi gán (m), tính từ xe":"Labelling range (m) from the car","Phạm vi: {0} (vượt phạm vi của guideline: chỉ áp nếu guideline cho phép)":"Range: {0} (beyond the guideline's range: apply only if the guideline allows it)","Phạm vi?":"Scope?","Phần trăm diện tích toàn thân bị che để đặt occluded":"Percent of the full body hidden to set occluded","Phần trăm diện tích toàn thân bị che để đặt outside":"Percent of the full body hidden to set outside","Point cloud không xoay quanh xe: phạm vi 50 / 40 / 30 m có thể không đúng chỗ.":"The point cloud is not centred on the car: the 50 / 40 / 30 m ranges may be in the wrong place.","Profile có sẵn \"{0}\" đã khớp các label này. \"Tự chọn profile\" sẽ giữ profile có sẵn; muốn dùng guideline mới thì chọn tay profile của nó ở đầu panel (hoặc bấm \"Dùng cho job này\").":"Existing profile \"{0}\" already matches these labels. \"Auto-select profile\" keeps the existing profile; to use the new guideline, choose its profile by hand at the top of the panel (or press \"Use for this job\").","Profile guideline · đang dùng: {0}":"Guideline profile · in use: {0}","Profile đang dùng ({0})":"Active profiles ({0})","Profile đang ẩn ({0})":"Hidden profiles ({0})","Project này đã có profile riêng. Ghi đè bằng bản mới?":"This project already has its own profile. Overwrite it with the new one?","Prompt SAM3 để tìm người":"SAM3 prompt to find people","Prompt cho SAM3, cách nhau bằng dấu phẩy":"Prompts for SAM3, separated by commas","Prompt tiếng Anh để SAM3 tìm vật này trên ảnh camera":"English prompt for SAM3 to find this object in the camera images","Quay lại":"Back","Quy ước tên file tool đã học":"File-name rules the tool learned","Quét {0} ảnh và soạn nháp":"Scan {0} images and draft","Quên":"Forget","Quên hết":"Forget all","Quên tất cả":"Forget all","Quản lý profile":"Manage profiles","Rà cả job":"Check whole job","Rà cả job xong":"Whole-job check finished","Rà {0} frame có nhãn: {1} lỗi, {2} cảnh báo{3}.{4}":"Checked {0} labelled frames: {1} errors, {2} warnings{3}.{4}","Rổ này trống.":"This bucket is empty.","Rộng":"Wide","Rộng (m)":"Width (m)","SAM3 Assist: hiện/ẩn panel":"SAM3 Assist: show/hide panel","SAM3 Label Assist {0}. Giữ Alt rồi bấm lên ảnh để tick / bỏ tick đề xuất.":"SAM3 Label Assist {0}. Hold Alt and click the image to tick / untick suggestions.","SAM3 Video (thử nghiệm): model tự giữ ID, tốn VRAM hơn":"SAM3 Video (experimental): the model keeps IDs itself, uses more VRAM","SAM3 đang phân tích frame…":"SAM3 is analysing the frame…","SAM3 đang tách vật theo các điểm…":"SAM3 is segmenting the object from the points…","SAM3 đang tìm \"{0}\" trong {1} frame…":"SAM3 is searching for \"{0}\" in {1} frames…","SAM3 đang tìm lại, bỏ các vật giống hộp loại trừ…":"SAM3 is searching again, excluding objects like the excluded boxes…","SAM3 đang tìm đối tượng trong hộp…":"SAM3 is looking for the object in the box…","Sang bước 2 (cả job)":"Go to step 2 (whole job)","Sang frame tiếp":"Next frame","Sang tab Cả job":"Go to the Whole job tab","So các nhãn tool đã ghi với nhãn hiện tại (sau khi bạn sửa) rồi học lại. Tool cũng tự làm việc này mỗi lần mở job.":"Compare the labels the tool saved with the current labels (after your edits) and re-learn. The tool also does this every time a job opens.","So nhãn của job này với một job khác gán cùng các frame (ví dụ job kiểm tra chéo). Class đồng thuận thấp thường là chỗ guideline còn mơ hồ.":"Compares this job's labels with another job that labelled the same frames (e.g. a cross-check job). Classes with low agreement usually point to vague parts of the guideline.","So sánh":"Compare","So trên {0} frame, {1} cuboid của bạn: tool tìm đúng {2}%.":"Compared on {0} frames, {1} of your cuboids: the tool found {2}%.","So với job":"Compare with job","Soạn guideline nháp từ ảnh của job":"Draft a guideline from job images","Sót vì":"Missed because","Sẵn sàng":"Ready","Số điểm":"Points","Số điểm tối thiểu mặc định: {0} (người, xe hai bánh: một nửa).":"Default minimum points: {0} (pedestrians, two-wheelers: half).","Số điểm tối thiểu so với mặc định":"Minimum points relative to the default","Số điểm tối thiểu: {0}":"Minimum points: {0}","Số ảnh mẫu":"Number of sample images","Sửa":"Edit","Tag của ảnh":"Image tags","Task còn thiếu: {0}.":"Task is missing: {0}.","Task không có label kiểu cuboid.":"The task has no cuboid labels.","Task mới có label tạm (CVAT bắt buộc ít nhất 1 label): tool tự tìm các loại vật trong ảnh và đề xuất danh sách class. Duyệt xong, bấm \"Thêm label còn thiếu\" để tool thêm vào task (không xoá label tạm).":"New task with a placeholder label (CVAT requires at least 1 label): the tool finds the kinds of objects in the images and proposes a class list. After review, press \"Add missing labels\" so the tool adds them to the task (the placeholder is not deleted).","Task đã có đủ label theo guideline.{0}":"The task already has all labels from the guideline.{0}","Task đã có đủ label theo taxonomy của bản nháp.":"The task already has all labels from the draft's taxonomy.","Task đã có đủ label theo taxonomy.":"The task already has all taxonomy labels.","Theo dõi cả job (ra track)":"Track the whole job (as tracks)","Theo dõi {0} frame":"Track {0} frames","Theo dõi {0} người qua {1} frame":"Track {0} people across {1} frames","Theo dõi đối tượng (video)":"Track objects (video)","Theo dõi đối tượng xong":"Object tracking finished","Thu gọn panel":"Collapse panel","Thu thập từ job này":"Collect from this job","Thuộc tính?":"Attribute?","Thêm API key Anthropic ở Cài đặt của app để bật Claude.":"Add an Anthropic API key in the app Settings to turn Claude on.","Thêm label":"Add label","Thêm label còn thiếu":"Add missing labels","Thêm luật":"Add rule","Thêm {0} label còn thiếu vào task":"Add {0} missing labels to the task","Thêm {0} label vào {1}?\n{2}\n\nTool chỉ thêm label mới; label đang có được giữ nguyên.":"Add {0} labels to {1}?\n{2}\n\nThe tool only adds new labels; existing labels are kept.","Thêm {0} đề xuất từ hộp vừa vẽ.{1}{2}":"Added {0} suggestions from the box just drawn.{1}{2}","Thôi tự điền thuộc tính: {0}":"Stop auto-filling attributes: {0}","Thông minh: keyframe + kiểm tra giữa khoảng, chỗ lệch thì chạy từng frame (nhanh hơn nhiều)":"Smart: keyframes + a check in each gap; where it drifts, every frame runs (much faster)","Thứ tự điểm":"Point order","Thứ tự?":"Order?","Thừa":"Extra","Thử profile mới: {0} đề xuất, {1} tag. Chưa lưu: quay lại tab Guideline để lưu hoặc sửa.":"New profile trial: {0} proposals, {1} tags. Not saved yet: go back to the Guideline tab to save or edit.","Thử trên frame này":"Try on this frame","Tick / bỏ tick đề xuất đang chọn":"Tick / untick the selected suggestion","Tick cả nhóm":"Tick group","Tin cậy cao":"High confidence","Tiếng Việt":"Tiếng Việt","Tiếp":"Next","Tiếp tục":"Continue","Tool chỉ gợi ý. Bạn duyệt từng nhãn rồi mới ghi vào CVAT.":"The tool only suggests. You review each label before it is saved to CVAT.","Tool chỉ theo dõi những người này, cả trước và sau frame bạn chọn. Người khác, phản chiếu, quảng cáo bị bỏ.":"The tool follows only these people, before and after the frame you chose. Other people, reflections and ads are dropped.","Tool dựng cách gợi ý riêng cho project từ chính label của task (kể cả attribute, skeleton, tag). Thêm guideline để tool hiểu định nghĩa, kích thước tối thiểu và quy ước trái/phải. Không cần file taxonomy.":"The tool builds project-specific suggestions from the task's own labels (including attributes, skeletons, tags). Add a guideline so the tool knows definitions, minimum size and the left/right convention. No taxonomy file needed.","Tool gợi ý nhãn đang dùng profile sinh từ bản nháp này. Đổi lại ở ô chọn profile phía trên.":"Suggestions now use the profile generated from this draft. Change it back in the profile selector above.","Tool gợi ý được":"The tool can suggest","Tool học từ cách BẠN xử lý đề xuất: nhãn giữ nguyên, nhãn bị kéo lại, đổi class, xoá, bỏ tick, và nhãn bạn tự vẽ thêm. Từ đó nó chỉnh ngưỡng tin cậy và cách đặt hộp cho từng label. Model SAM3 không bị huấn luyện lại; chỉnh tay ở tab Label luôn thắng điều tool đã học.":"The tool learns from how YOU handle suggestions: labels kept, labels dragged, class changes, deletions, unticks, and labels you draw yourself. From that it adjusts the confidence threshold and box placement for each label. The SAM3 model is not retrained; manual settings in the Labels tab always win over what the tool learned.","Tool nhớ {0} kiểu vật bạn hay bỏ (bỏ tick sẵn khi gặp lại).":"The tool remembers {0} kinds of objects you often drop (unticked when seen again).","Tool quét một số ảnh mẫu, đo xem trong ảnh có gì và chỗ nào dễ nhầm, rồi soạn bản nháp theo khung của các guideline mẫu. Luật là quyết định của người: tool hỏi, bạn chốt.":"The tool scans some sample images, measures what is in them and what is easy to confuse, then drafts a guideline in the structure of the sample guidelines. Rules are human decisions: the tool asks, you decide.","Tool tách mặt đất, gom cụm điểm, đoán class theo bảng cỡ (chiều cao phải khớp), đối chiếu ảnh camera nếu có. Ghi xong kiểm tra ở Top / Side / Front của CVAT.":"The tool removes the ground, groups points into clusters, picks the class from the size table (height must match) and checks the camera images when there are any. After saving, check in CVAT's Top / Side / Front views.","Tool tìm đúng":"Tool found","Tool tự chọn {0} ảnh mẫu ({1}). {2}":"The tool picks {0} sample images ({1}). {2}","Tool tự thấy: ":"The tool noticed: ","Tool tự điền":"Auto-filled by the tool","Tool tự đoán từ ảnh: kiểm lại":"Guessed from the image: check it","Tool đã học: {0} → {1} ({2}). Gõ số khác để đặt tay.":"The tool learned: {0} → {1} ({2}). Type another number to set it by hand.","Track nhầm?":"Wrong track?","Track tool đã ghi: giữ {0} · bạn sửa {1} · xoá {2}":"Tracks saved by the tool: kept {0} · you edited {1} · deleted {2}","Trái/phải?":"Left/right?","Trên các đề xuất đã tick":"On the ticked suggestions","Trí nhớ của project":"Project memory","Trùng lặp":"Duplicate","Trùng?":"Duplicate?","Trạng thái backend":"Backend status","Trạng thái điểm":"Point state","Tìm":"Find","Tìm \"{0}\": {1}/{2} frame, thấy {3}.":"Searching \"{0}\": {1}/{2} frames, found {3}.","Tìm cả đối tượng có thể bị sót (chạy SAM3 trên từng frame, chậm như Gợi ý cho cả job)":"Also look for possibly missed objects (runs SAM3 on every frame, as slow as Suggest for whole job)","Tìm frame có… (tiếng Anh, vd: motorbike carrying boxes)":"Find frames with… (English, e.g. motorbike carrying boxes)","Tìm frame xong":"Frame search finished","Tìm lại người bị mất dấu: {0}/{1} frame…":"Finding lost people again: {0}/{1} frames…","Tìm lại được người ở {0} chỗ model bỏ sót (tính % bị che thay vì đặt outside).":"Found people again at {0} spots the model missed (hidden % computed instead of setting outside).","Tìm tương tự":"Find similar","Tìm được":"Recall","Tóm bài học ({0} lần sửa mới)":"Summarise lessons ({0} new corrections)","Tóm bài học từ các lần sửa":"Summarise lessons from corrections","Tương phản cao":"High contrast","Tạm dừng / Hủy":"Pause / Cancel","Tạo Issue":"Create issue","Tạo Issue cho mọi lỗi":"Create issues for all errors","Tạo Issue trên CVAT: gửi case này cho mentor":"Create a CVAT Issue: send this case to the mentor","Tạo lại profile cho project":"Recreate the project profile","Tạo profile cho project":"Create a project profile","Tạo profile từ bản nháp để tool gợi ý nhãn theo đúng các luật này":"Create a profile from the draft so the tool suggests labels by exactly these rules","Tạo {0} Issue trên CVAT (mỗi lỗi một Issue, đặt đúng frame và vị trí)?":"Create {0} Issues in CVAT (one Issue per error, at the right frame and position)?","Tải .md":"Download .md","Tải lại trang sau khi ghi":"Reload the page after saving","Tất cả":"All","Tắt":"Off","Tắt bài học sai ở trang duyệt bản nháp.":"Turn off wrong lessons on the draft review page.","Tắt ở mức Nhẹ để đỡ VRAM":"Off at Light to save VRAM","Tối đa":"Max","Tới câu chưa trả lời":"Go to the next unanswered question","Tới frame này":"Go to this frame","Tới frame này và khoanh vùng ví dụ":"Go to this frame and highlight the example","Tới frame rõ nhất":"Go to clearest frame","Tới frame đầu":"Go to first frame","Từ frame":"From frame","Từ khoá thêm (tiếng Anh, cách nhau bằng dấu phẩy)":"Extra keywords (English, separated by commas)","Tự chỉnh theo cuboid bạn đã đánh":"Tune from your cuboids","Tự chỉnh theo cuboid đã đánh":"Self-tune from your cuboids","Tự chỉnh · tìm đúng {0}% · đúng trong số tool đề xuất {1}":"Tuning · found {0}% · correct among tool suggestions {1}","Tự chọn profile":"Auto-select profile","Tự chọn số":"Set the number","Tự chọn: {0}":"Auto: {0}","Tự gợi ý khi chuyển frame":"Suggest automatically when changing frame","Tự học":"Learning","Tự nhận diện và kiểm duyệt lại toàn bộ nhãn theo guideline.":"Recognises objects and re-audits every label against the guideline.","Tự rà":"Self-check","Tự viết một luật cho project, vd: hand cầm vô lăng thì on_wheel = true":"Write a rule for the project, e.g. a hand holding the wheel means on_wheel = true","Tự vẽ một nhãn":"Draw a label yourself","Tự động":"Automatic","Tự động (máy này: {0})":"Auto (this machine: {0})","VRAM gần đầy: nên hạ một mức, hoặc đóng chương trình khác đang dùng GPU.":"VRAM is almost full: go down one level, or close other programs using the GPU.","VRAM: {0}/{1} GB · model phụ đang nạp: {2}{3}":"VRAM: {0}/{1} GB · extra models loaded: {2}{3}","Viền tím \"AI\" cho nhãn tool ghi mà chưa ai sửa":"Purple \"AI\" outline on labels the tool saved that nobody has edited","Ví dụ":"Examples","Ví dụ: phát hiện người đi bộ cho camera hành trình":"E.g. pedestrian detection for dash cams","Ví dụ: reviewer trả bài vì xe ba bánh chở hàng phải gán truck":"E.g. the reviewer rejected work because cargo tricycles must be labelled truck","Vùng bỏ qua":"Ignore zones","Vùng {0}":"Zone {0}","Vật này bị nhận nhầm: dùng nó làm mẫu ÂM, SAM3 tìm lại và bỏ mọi vật giống nó":"This object is wrong: use it as a NEGATIVE example; SAM3 searches again and drops every object like it","Vẽ hộp":"Draw box","Vẽ một hộp làm mẫu, SAM3 tìm mọi đối tượng giống vậy":"Draw an example box and SAM3 finds every similar object","Vẽ một hộp, SAM3 lấy đúng đối tượng đó":"Draw a box and SAM3 takes exactly that object","Về mặc định của profile":"Reset to profile defaults","Về ngưỡng của profile":"Back to the profile's thresholds","Về phím mặc định":"Reset shortcuts","Vừa":"Medium","Xem lại hướng dẫn":"Show the tour again","Xem trước: {0}":"Preview: {0}","Xoá":"Delete","Xoá dữ liệu và model riêng của profile này?":"Delete this profile's custom model and data?","Xoá dữ liệu và điều đã học của label này":"Delete this label's data and what was learned","Xoá hẳn file profile":"Permanently delete the profile file","Xoá hẳn profile {0}? File bị xoá khỏi máy, không hoàn tác được.{1}{2}":"Permanently delete profile {0}? The file is removed from the machine and cannot be undone.{1}{2}","Xoá mọi điều tool đã học (mọi profile)?":"Delete everything the tool learned (all profiles)?","Xoá mọi điều tool đã học về \"{0}\"?":"Delete everything the tool learned about \"{0}\"?","Xoá {0} mà tool đã ghi ở frame {1}{2}?":"Delete {0} that the tool saved at frame {1}{2}?","Xuất báo cáo CSV":"Export CSV report","Xuất báo cáo năng suất (CSV)":"Export productivity report (CSV)","bạn sửa":"edited by you","bạn viết":"written by you","bạn đặt cho task này":"set by you for this task","bỏ qua (tạm dùng: {0})":"skipped (using for now: {0})","chưa có cỡ: thêm cỡ để tool gợi ý label này":"no sizes yet: add sizes so the tool can suggest this label","chưa dò được":"not detected yet","chưa dò được (cần frame có ≥ 2 xe rõ)":"not detected yet (needs a frame with ≥ 2 clear cars)","chưa kiểm tra":"not checked","chưa kết nối":"not connected","có sẵn trong tool":"built into the tool","có thể là toạ độ thế giới: phạm vi gán tính từ gốc, có thể lệch":"may be world coordinates: ranges are measured from the origin and may be off","căn từ box mẫu":"fitted from sample boxes","cả job {0}":"whole job {0}","cụm bị bỏ ({0})":"dropped clusters ({0})","frame đã có tag này":"the frame already has this tag","hai cạnh dài (crosswalk)":"two long sides (crosswalk)","hai mép (vạch kép)":"two edges (double line)","job có {0} frame":"the job has {0} frames","job có {0} frame, {1} đoạn thay đổi rõ":"the job has {0} frames, {1} clear scene changes","job không có ảnh camera đi kèm (hoặc CVAT không trả về)":"the job has no camera images (or CVAT did not return them)","không":"none","không có cụm điểm":"no point cluster","không có ảnh camera":"no camera images","không nhận ra tên camera":"camera names not recognised","khớp hiệu chỉnh":"matches calibration","luôn có":"always present","lấy từ guideline":"from the guideline","model khuôn mặt":"face model","mặc định (guideline 3D Cuboid V1)":"default (3D Cuboid guideline V1)","mặc định (guideline không ghi)":"default (not in the guideline)","ngay (đã chạy sẵn)":"instantly (pre-computed)","nhận lại người":"re-identification","nạp từ guideline":"imported from guideline","occluded từ":"occluded from","outside từ":"outside from","pose người":"body pose","pose toàn thân":"whole-body pose","project {0} (label của task này nằm ở project)":"project {0} (this task's labels live in the project)","prompt tiếng Anh":"English prompt","quanh xe (gốc toạ độ là xe, có vùng trống quanh cảm biến)":"around the car (origin at the car, empty area around the sensor)","skeleton {0} điểm":"{0}-point skeleton","skeleton: không dùng prompt":"skeleton: no prompt used","suy ra bằng luật":"derived by rules","số job":"job number","tab Label của job":"the job's Labels tab","task đang mở":"the open task","theo guideline của profile":"from the profile's guideline","theo hiệu chỉnh":"from calibration","theo tim vạch":"along the centre","tới":"to","tự dò":"auto-detected","tự dò {0}°":"auto-detected {0}°","tự dò, lệch {0}°":"auto-detected, offset {0}°","tự học":"learned","xa hơn: {0}":"farther: {0}","{0} ({1} điểm)":"{0} ({1} points)","{0} Issue type · {1} mục checklist · {2} tiêu chí reviewer. Prompt là cụm tiếng Anh ngắn SAM3 dùng để tìm vật (rê chuột lên ô để xem nguồn); để trống thì tool không tự tìm label đó.":"{0} Issue types · {1} checklist items · {2} reviewer criteria. The prompt is the short English phrase SAM3 uses to find objects (hover to see its source); if empty, the tool will not look for that label.","{0} Nếu tài khoản của bạn không có quyền sửa task, bấm \"Chép JSON label\" và gửi cho người tạo task.":"{0} If your account cannot edit the task, press \"Copy label JSON\" and send it to the task creator.","{0} camera ({1}); hướng: {2}":"{0} cameras ({1}); heading: {2}","{0} cuboid đề xuất ở frame {1} ({2} rõ, {3} yếu đang ẩn). Ghi xong kiểm tra trong cửa sổ 3D của CVAT (Top / Side / Front).":"{0} cuboid suggestions in frame {1} ({2} clear, {3} weak hidden). After saving, check in CVAT's 3D view (Top / Side / Front).","{0} cờ":"{0} flags","{0} frame có kết quả · {1} đề xuất · {2} mục có cờ":"{0} frames with results · {1} suggestions · {2} flagged items","{0} frame cần xem · {1} lỗi · {2} cảnh báo{3}":"{0} frames to review · {1} errors · {2} warnings{3}","{0} giây":"{0} seconds","{0} label profile \"{1}\" chưa gợi ý được ({2}{3}). Profile cho project dựng cách gợi ý theo từng label của task, kể cả tag và skeleton.":"{0} labels the profile \"{1}\" cannot suggest ({2}{3}). A project profile builds suggestions per task label, including tags and skeletons.","{0} lần sửa đã ghi nhận · {1} bài học · {2} quy ước tên file":"{0} corrections recorded · {1} lessons · {2} file-name rules","{0} lỗi · {1} khác{2}":"{0} errors · {1} other{2}","{0} nhãn":"{0} labels","{0} nhãn đã ghi · {1} đề xuất bị bỏ · {2} frame · ước tính tiết kiệm {3} phút":"{0} labels saved · {1} suggestions dropped · {2} frames · about {3} minutes saved","{0} phút":"{0} minutes","{0} thành đề xuất{1}":"{0} became suggestions{1}","{0} track (frame {1}–{2}) · {3} đã chọn":"{0} tracks (frames {1}–{2}) · {3} selected","{0} track cuboid (frame {1}–{2}). Duyệt ở tab Cả job rồi bấm Ghi.":"{0} cuboid tracks (frames {1}–{2}). Review in the Whole job tab, then press Save.","{0} track cuboid, {1} track có cờ cần xem. Bấm Ghi rồi kiểm tra trong cửa sổ 3D của CVAT.":"{0} cuboid tracks, {1} flagged for review. Press Save, then check in CVAT's 3D view.","{0} track · {1} cần xem":"{0} tracks · {1} to review","{0} track: {1} cần xem · {2} nghi rác · {3} tin cậy cao. Duyệt ở bước 3 rồi ghi.":"{0} tracks: {1} to review · {2} suspected noise · {3} high confidence. Review in step 3, then write.","{0} vật, điểm {1}%":"{0} objects, score {1}%","{0} · {1} ảnh · {2} loại đối tượng · {3}% bị che · {4}% rất nhỏ":"{0} · {1} images · {2} kinds of objects · {3}% occluded · {4}% very small","{0} Đổi lại ở ô chọn profile phía trên.":"{0} Change it back in the profile selector above.","{0} điều đã học cho \"{1}\".":"{0} learned for \"{1}\".","{0} điểm":"{0} points","{0} điểm, xa nhất {1} m, mặt đất cục bộ {2} ô":"{0} points, farthest {1} m, local ground {2} cells","{0} đề xuất {1}, {2} cần xem kỹ.{3} Duyệt rồi bấm \"Ghi\" (hoặc {4}: ghi và sang frame tiếp).":"{0} suggestions {1}, {2} need a closer look.{3} Review, then press \"Save\" (or {4}: save and go to the next frame).","{0} đề xuất · {1} đã chọn{2}":"{0} suggestions · {1} selected{2}","{0} đề xuất · {1} đã tick":"{0} suggestions · {1} ticked","{0}\"{1}\": thấy ở {2} frame.":"{0}\"{1}\": found in {2} frames.","{0}. Bấm nhiều lần để lùi thêm (tối đa 20 lần ghi).":"{0}. Press repeatedly to go further back (up to 20 saves).","{0}/{1} điểm{2}":"{0}/{1} points{2}","{0}: ghi nhưng ở lại frame này":"{0}: save but stay on this frame","{0}: ghi rồi sang frame {1}, đề xuất của frame đó đã chạy sẵn":"{0}: save, then go to frame {1}, whose suggestions are already pre-computed","{0}: thêm cỡ ở tab Label để tool gợi ý":"{0}: add sizes in the Label tab so the tool can suggest them","{0}{1} frame có {2} đề xuất{3}. {4} mục có cờ cần duyệt.":"{0}{1} frames have {2} suggestions{3}. {4} flagged items need review.","{0}{1} track, {2} track có cờ cần xem{3}. Tua qua các frame để kiểm tra rồi bấm Ghi.{4}{5}":"{0}{1} tracks, {2} flagged for review{3}. Step through the frames to check, then press Save.{4}{5}","· Timeout mỗi lô (giây):":"· Timeout per batch (seconds):","Áp dụng":"Apply","Áp lại điều đã học":"Apply what was learned again","Ít điểm LiDAR — kích thước/hướng có thể sai":"Few LiDAR points — size/heading may be wrong","×0.5 (nhận cả vật thưa)":"×0.5 (accept sparse objects)","×1 mặc định":"×1 default","×2 (chặt)":"×2 (strict)","Đang bắt đầu huấn luyện…":"Starting training…","Đang chạy sẵn frame {0}…":"Pre-computing frame {0}…","Đang chạy…":"Running…","Đang chỉ xem {0} dòng có cờ \"{1}\"; {2} dòng khác vẫn được ghi nếu đang tick.":"Showing only {0} rows flagged \"{1}\"; {2} other rows are still saved if ticked.","Đang cập nhật bản nháp…":"Updating the draft…","Đang cập nhật label…":"Updating labels…","Đang cập nhật phần tự học…":"Updating learning data…","Đang cập nhật profile…":"Updating profile…","Đang cập nhật…":"Updating…","Đang dùng":"In use","Đang dùng profile của \"{0}\": khớp {1}/{2} label của job.":"Using the profile of \"{0}\": matches {1}/{2} labels of the job.","Đang dừng quét, sẽ soạn nháp từ các ảnh đã quét…":"Stopping the scan; the draft will use the images scanned so far…","Đang dừng sau frame hiện tại…":"Stopping after the current frame…","Đang dừng sau lô hiện tại… (kết quả đã chạy vẫn giữ)":"Stopping after the current batch… (batches already run are kept)","Đang dựng cuboid 3D theo lô…":"Building 3D cuboids in batches…","Đang dựng cuboid cho {0} frame…":"Building cuboids for {0} frames…","Đang dựng cuboid từ point cloud của frame này…":"Building cuboids from this frame's point cloud…","Đang dựng cuboid và chờ Claude xem ảnh camera của frame này…":"Building cuboids and waiting for Claude to look at this frame's camera images…","Đang dựng cuboid: {0}/{1} frame. Còn khoảng {2} giây.":"Building cuboids: {0}/{1} frames. About {2} seconds left.","Đang dựng profile từ label của task…":"Building the profile from the task's labels…","Đang ghi cuboid vào CVAT…":"Saving cuboids to CVAT…","Đang ghi nhãn của cả job…":"Saving labels for the whole job…","Đang ghi nhãn vào CVAT…":"Saving labels to CVAT…","Đang ghi nhận…":"Recording…","Đang ghi track vào CVAT…":"Saving tracks to CVAT…","Đang gỡ…":"Removing…","Đang gợi ý cho {0} frame…":"Suggesting for {0} frames…","Đang hoàn tác…":"Undoing…","Đang huấn luyện":"Training","Đang huấn luyện model riêng (chạy nền).":"Training the custom model (in the background).","Đang kiểm tra":"Checking","Đang kiểm tra annotation của frame…":"Checking the frame's annotations…","Đang kiểm tra backend…":"Checking the backend…","Đang kiểm tra dữ liệu point cloud và ảnh camera của frame này…":"Checking the point cloud and camera images of this frame…","Đang lưu guideline vào tool…":"Saving the guideline to the tool…","Đang lưu profile cho project…":"Saving the project profile…","Đang lấy ảnh frame…":"Getting the frame image…","Đang nạp bản nháp guideline…":"Loading the draft guideline…","Đang nạp lại profile…":"Reloading profiles…","Đang nối ID và chọn keyframe…":"Linking IDs and choosing keyframes…","Đang nối lại ID sau khi tìm lại…":"Relinking IDs after the rescue pass…","Đang nối track, khoá kích thước, chọn keyframe…":"Linking tracks, locking sizes, choosing keyframes…","Đang phân tích frame: {0}/{1}…":"Analysing frames: {0}/{1}…","Đang phân tích {0} frame…":"Analysing {0} frames…","Đang quét {0} ảnh mẫu…":"Scanning {0} sample images…","Đang rà cả job…":"Checking the whole job…","Đang so job {0} với job {1}…":"Comparing job {0} with job {1}…","Đang so tool với các cuboid bạn đã đánh…":"Comparing the tool with your cuboids…","Đang soạn bản nháp và tự kiểm tra…":"Writing the draft and self-checking…","Đang theo dõi {0} frame…":"Tracking {0} frames…","Đang thu thập ảnh + hộp đã duyệt của job…":"Collecting reviewed images + boxes from the job…","Đang thêm label vào CVAT…":"Adding labels to CVAT…","Đang thử profile mới trên frame này…":"Trying the new profile on this frame…","Đang tìm label chưa có trong job…":"Looking for labels not yet used in the job…","Đang tìm đối tượng có thể bị sót: {0}/{1} frame. Còn khoảng {2} giây.":"Looking for possibly missed objects: {0}/{1} frames. About {2} seconds left.","Đang tìm đối tượng: {0}/{1} frame. Còn khoảng {2} giây.":"Looking for objects: {0}/{1} frames. About {2} seconds left.","Đang tìm đối tượng: đã xong {0}/{1} frame (SAM3 chạy {2} frame, phần còn lại nội suy đã kiểm tra). Khoảng {3} giây mỗi lần chạy.{4}":"Finding objects: {0}/{1} frames done (SAM3 ran on {2} frames, the rest are checked interpolations). About {3} seconds per run.{4}","Đang tạo Issue…":"Creating Issues…","Đang tạo JSON label…":"Creating label JSON…","Đang tạo profile từ bản nháp…":"Creating a profile from the draft…","Đang tải danh sách profile…":"Loading profile list…","Đang tải…":"Loading…","Đang xoá…":"Deleting…","Đang áp profile…":"Applying profile…","Đang đọc annotation của job…":"Reading the job's annotations…","Đang đọc cấu hình cuboid…":"Reading cuboid settings…","Đang đọc job và label từ CVAT…":"Reading job and labels from CVAT…","Đang đọc {0} và label của task…":"Reading {0} and the task's labels…","Đang đọc {0}…":"Reading {0}…","Đang đối chiếu…":"Comparing…","Đang đổi source của {0} nhãn…":"Changing the source of {0} labels…","Đang đợi gợi ý đã chạy sẵn cho frame này…":"Waiting for the pre-computed suggestions for this frame…","Đang ẩn class này: bấm để hiện":"This class is hidden: click to show","Điểm?":"Point?","Đánh dấu nhãn do tool ghi là source = \"semi-auto\"":"Mark tool labels as source = \"semi-auto\"","Đáy cách đất tối đa: {0} m":"Max base above ground: {0} m","Đáy cụm cách mặt đất tối đa (m): cao hơn là vật lơ lửng (biển, tán cây), bỏ":"Max height of a cluster's base above the ground (m): higher is floating (signs, tree crowns) and dropped","Đây là frame cuối của job.":"This is the last frame of the job.","Đây là giả thuyết của tool, không phải kết luận. Xem rồi sửa bản nháp nếu đúng.":"These are the tool's hypotheses, not conclusions. Review them and fix the draft if they are right.","Đã bật":"Turned on","Đã bỏ kết quả gợi ý của cả job.":"Discarded the whole-job suggestions.","Đã bỏ kết quả theo dõi.":"Discarded the tracking results.","Đã chép JSON của {0} label. Khi tạo task mới, dán vào tab Raw ở phần label.":"Copied JSON for {0} labels. When creating a new task, paste it into the Raw tab of the labels section.","Đã chép JSON của {0} label. Khi tạo task mới, dán vào tab Raw ở phần label.{1}":"Copied JSON for {0} labels. When creating a new task, paste it into the Raw tab of the labels section.{1}","Đã chọn P{0} ở frame {1}. Chọn thêm người khác, hoặc bấm \"Theo dõi\" để tool chỉ theo dõi những người đã chọn (cả trước và sau frame này).":"Chose P{0} at frame {1}. Choose more people, or press \"Track\" so the tool follows only the chosen people (before and after this frame).","Đã chọn {0} label chưa có nhãn nào trong job.":"Selected {0} labels that have no annotations in the job.","Đã chọn: {0}":"Chosen: {0}","Đã chốt {0} · Bỏ qua {1} · Còn {2}":"Answered {0} · Skipped {1} · Left {2}","Đã cập nhật bản nháp theo lựa chọn của bạn.":"Updated the draft with your choices.","Đã duyệt":"Approved","Đã dừng ghi để bạn sửa các lỗi kiểm tra.":"Saving stopped so you can fix the check errors.","Đã dừng ở frame {0}. ":"Stopped at frame {0}. ","Đã dừng. ":"Stopped. ","Đã dựng profile cho {0} label{1}. Bấm \"Thử trên frame này\" rồi \"Lưu cho project\".":"Built a profile for {0} labels{1}. Press \"Try on this frame\", then \"Save for project\".","Đã ghi nhận. Bài học này sẽ được dùng ở các lần soạn sau.":"Recorded. This lesson will be used in future drafts.","Đã ghi {0} cuboid.{1} Tải lại trang (F5) để thấy trong cửa sổ 3D.":"Saved {0} cuboids.{1} Reload the page (F5) to see them in the 3D view.","Đã ghi {0} nhãn.{1}":"Saved {0} labels.{1}","Đã ghi {0} nhãn{1}. Đang tải lại trang{2}…":"Saved {0} labels{1}. Reloading the page{2}…","Đã ghi {0} nhãn{1}.{2}":"Saved {0} labels{1}.{2}","Đã ghi {0} track.{1}":"Saved {0} tracks.{1}","Đã ghi {0}/{1} nhãn…":"Saved {0}/{1} labels…","Đã ghép {0} label mới và cập nhật {1} label từ {2} vào guideline \"{3}\". Kiểm tra rồi bấm Lưu.":"Merged {0} new labels and updated {1} labels from {2} into guideline \"{3}\". Check, then press Save.","Đã giữ vật vừa tách. Duyệt rồi ghi như các đề xuất khác.":"Kept the segmented object. Review and save it like other suggestions.","Đã gỡ \"{0}\".":"Removed \"{0}\".","Đã gửi {0}/{1} frame cho model riêng…":"Sent {0}/{1} frames to the custom model…","Đã hiện lại":"Shown again","Đã huỷ bấm điểm.":"Cancelled point clicking.","Đã huỷ vẽ hộp.":"Box drawing cancelled.","Đã huỷ.":"Cancelled.","Đã học cho profile đang dùng ({0} label)":"Learned for the current profile ({0} labels)","Đã kiểm tra dữ liệu. Xem kết quả ngay dưới các nút.":"Data checked. See the results right below the buttons.","Đã kiểm tra {0} annotation: {1} lỗi, {2} cảnh báo.":"Checked {0} annotations: {1} errors, {2} warnings.","Đã lưu profile {0}. Mọi job của {1} này tự dùng profile này.":"Saved profile {0}. Every job of this {1} uses it automatically.","Đã lấy {0} label từ task đang mở ({1} label đã có trong bản xem trước).":"Took {0} labels from the open task ({1} labels were already in the preview).","Đã nạp văn bản, định nghĩa và checklist ({0} label). Guideline skeleton chưa có profile gợi ý tự động.":"Imported text, definitions and checklist ({0} labels). Skeleton guidelines do not get an automatic suggestion profile yet.","Đã nạp {0} label. Profile \"{1}\" đã có trong ô chọn profile; bấm \"Dùng cho job này\" ở danh sách bên dưới nếu job đang mở thuộc guideline này.":"Imported {0} labels. Profile \"{1}\" is now in the profile selector; press \"Use for this job\" in the list below if the open job belongs to this guideline.","Đã phân tích {0} frame: {1} keyframe theo chuyển động. Xem danh sách \"Nên làm trước\".":"Analysed {0} frames: {1} motion keyframes. See the \"Do first\" list.","Đã quét {0}/{1} ảnh, thấy {2} loại đối tượng. Còn khoảng {3} giây.":"Scanned {0}/{1} images, found {2} kinds of objects. About {3} seconds left.","Đã quên \"{0}\".":"Forgot \"{0}\".","Đã soạn bản nháp từ {0} ảnh: {1} câu hỏi cần chốt, {2} lỗ hổng tool tự thấy.":"Drafted from {0} images: {1} questions to decide, {2} gaps the tool found.","Đã thêm vùng bỏ qua thứ {0}. Mọi phát hiện có tâm nằm trong vùng này bị bỏ (cả gợi ý từng frame và theo dõi) cho mọi job của task.":"Added ignore zone {0}. Every detection whose centre is inside it is dropped (per-frame suggestions and tracking) for all jobs of the task.","Đã thêm {0} label.{1}":"Added {0} labels.{1}","Đã thêm {0} label.{1} Tải lại trang (F5) để CVAT nhận label mới.":"Added {0} labels.{1} Reload the page (F5) so CVAT picks up the new labels.","Đã tách vật ({0}%). Bấm thêm điểm để sửa, Enter để giữ.":"Object segmented ({0}%). Click more points to correct, Enter to keep.","Đã tạm dừng ({0}/{1} frame). Bấm \"Tiếp tục\" để chạy nốt. ":"Paused ({0}/{1} frames). Press \"Continue\" to finish. ","Đã tạo Issue trên frame này.":"Created an Issue on this frame.","Đã tạo {0} Issue. Tải lại trang (F5) để thấy chúng trong CVAT.":"Created {0} Issues. Reload the page (F5) to see them in CVAT.","Đã tạo {0}/{1} Issue…":"Created {0}/{1} Issues…","Đã tắt":"Turned off","Đã xoá":"Deleted","Đã xoá mọi điều tool đã học.":"Deleted everything the tool learned.","Đã xoá. Đang tải lại trang…":"Deleted. Reloading the page…","Đã áp dụng. Bấm \"Gợi ý cuboid\" để xem kết quả mới; về mặc định ở tab Label.":"Applied. Click \"Suggest cuboids\" to see the new result; reset in the Label tab.","Đã đọc \"{0}\": {1} label, {2} mục checklist.{3} Kiểm tra bảng bên dưới rồi bấm Lưu.":"Read \"{0}\": {1} labels, {2} checklist items.{3} Check the table below, then press Save.","Đã đọc {0} label từ CVAT nhưng chưa nối được backend. Kiểm tra ở Cài đặt panel (nút bánh răng).":"Read {0} labels from CVAT but cannot reach the backend yet. Check Panel settings (gear button).","Đã đọc {0} label từ {1}. Nếu có file guideline (.docx), chọn nó ngay sau đây: tool sẽ ghép label này với văn bản và checklist của guideline.":"Read {0} labels from {1}. If you have a guideline file (.docx), choose it next: the tool will merge these labels with the guideline's text and checklist.","Đã đối chiếu {0} đề xuất của job này với nhãn hiện tại và học lại.":"Compared {0} suggestions of this job with the current labels and re-learned.","Đã đổi {0} nhãn sang manual. Đang tải lại trang…":"Changed {0} labels to manual. Reloading the page…","Đã ẩn":"Hidden","Đóng":"Close","Đóng lưới ảnh":"Close thumbnail grid","Đóng quản lý profile":"Close profile manager","Đúng":"Precision","Đạt":"Passed","Đề xuất này lấy từ lượt gợi ý cho cả job (tab Cả job).":"This suggestion comes from the whole-job run (Whole job tab).","Đề xuất thuộc frame khác. Hãy chạy lại gợi ý cho frame này.":"These suggestions belong to another frame. Run suggestions again for this frame.","Để sau: tool tạm dùng lựa chọn đề xuất":"Later: the tool uses the suggested choice for now","Để thấy ngay nhãn mới trong CVAT":"To see new labels in CVAT right away","Để tool tự nhận":"Let the tool detect it","Để tool tự tính theo độ dài và độ đa dạng của job":"Let the tool decide from the job's length and variety","Địa chỉ backend SAM3":"SAM3 backend address","Đọc lại các file YAML trong thư mục profiles/ của backend":"Re-read the YAML files in the backend's profiles/ folder","Đọc lại label từ CVAT":"Reload labels from CVAT","Đối chiếu annotation đã lưu của frame với checklist trong guideline. Hãy lưu (Ctrl+S) trước khi kiểm tra.":"Compares the frame's saved annotations with the guideline checklist. Save (Ctrl+S) before checking.","Đối chiếu camera lỗi: {0}":"Camera cross-check error: {0}","Đối chiếu camera: {0}":"Camera cross-check: {0}","Đối chiếu job này ngay":"Review this job now","Đối chiếu ảnh camera{0}":"Check camera images{0}","Đồng thuận":"Agreement","Đồng thuận chung {0}% trên {1} frame chung.":"Overall agreement {0}% over {1} shared frames.","Đồng ý":"OK","Đổi":"Change","Đổi ID?":"ID switch?","Đổi class":"Change class","Đổi nhãn semi-auto đã lưu thành manual":"Change saved semi-auto labels to manual","Đổi source của {0} nhãn trong {1} từ \"semi-auto\" sang \"manual\"?\nGồm cả nhãn tạo bằng công cụ AI có sẵn của CVAT, nếu có.":"Change the source of {0} labels in {1} from \"semi-auto\" to \"manual\"?\nIncludes labels made with CVAT's built-in AI tools, if any.","Độ tin của tool (cỡ khớp bảng, số điểm, ảnh camera)":"Tool confidence (size matches the table, point count, camera images)","Độ đậm vùng tô":"Fill opacity","Độ đồng thuận giữa hai người gán":"Agreement between two annotators","Đứt ID?":"ID split?","điểm mặt":"face points","đã có nhãn":"already labelled","đã có đủ các label đã chọn":"already has all selected labels","đã có đủ label":"already has all labels","đã lưu":"saved","đã lưu {0}°":"saved {0}°","đã lưu, lệch {0}°":"saved, offset {0}°","đúng {0}% · tìm được {1}%":"precision {0}% · recall {1}%","độ sâu":"depth","Ảnh camera":"Camera images","Ảnh {0}: chỉ thấy một phần":"Image {0}: only partly visible","Ảnh {0}: giống {1}":"Image {0}: looks like {1}","Ảnh {0}: khu chiếu không có xe / người — nghi rác":"Image {0}: no vehicle / person in the projected area — suspected noise","Ảnh {0}: không thấy rõ":"Image {0}: not clearly visible","Ảnh {0}: thấy vật khác loại — xem lại class":"Image {0}: a different kind of object is visible — check the class","Ảnh {0}: thấy {1}":"Image {0}: saw {1}","Ảnh {0}: thấy đúng loại vật":"Image {0}: the right kind of object is visible","Ẩn":"Hide","Ẩn / hiện panel":"Hide / show panel","Ẩn = chuyển file vào thư mục profiles/archive/ của backend; profile biến khỏi ô chọn và khỏi \"Tự chọn profile\", hiện lại được. Xoá = xoá hẳn file. Hai profile dự phòng (generic, generic_track) luôn có.":"Hide = move the file into the backend's profiles/archive/ folder; the profile disappears from the selector and from \"Auto-select profile\" and can be shown again. Delete = delete the file permanently. The two fallback profiles (generic, generic_track) are always present.","Ẩn đề xuất có điểm thấp hơn":"Hide suggestions with a lower score","Ẩn đề xuất yếu":"Hide weak suggestions","… và {0} frame nữa.":"… and {0} more frames.","‹ Trước":"‹ Previous"}};
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
