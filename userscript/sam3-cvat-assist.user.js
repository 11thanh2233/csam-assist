// ==UserScript==
// @name         SAM3 CVAT Assist
// @namespace    sam3-cvat-assist
// @version      0.1.2
// @description  Gợi ý nhãn bằng SAM3 ngay trên giao diện CVAT: xem trước, duyệt, rồi ghi vào job. Không cần import/export file.
// @match        https://cvat.edufun.ai/*
// @run-at       document-idle
// @noframes
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @connect      *
// ==/UserScript==

/*
 * Cách hoạt động (tóm tắt):
 *   1. Đọc job và frame đang mở từ URL / thanh player của CVAT.
 *   2. Lấy ảnh frame và danh sách label qua REST API của CVAT (dùng phiên đăng nhập sẵn có).
 *   3. Gửi ảnh sang backend SAM3 của bạn; backend trả về các ĐỀ XUẤT nhãn theo guideline.
 *   4. Vẽ đề xuất đè lên canvas để bạn duyệt; chỉ những đề xuất được tick mới ghi vào job
 *      bằng PATCH /api/jobs/{id}/annotations?action=create (source = "semi-auto").
 * Script không gửi dữ liệu đi đâu ngoài backend bạn cấu hình trong tab "Cài đặt".
 */
(function () {
  'use strict';

  const VERSION = '0.1.2';
  const DEFAULTS = {
    backendUrl: 'http://127.0.0.1:8765',
    apiKey: '',
    profileId: 'auto',
    autoPropose: false,
    reloadAfterApply: true,
    confirmApply: true,
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
  };
  const SHAPES = ['rectangle', 'polygon', 'polyline', 'mask'];
  const PALETTE = ['#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4', '#42d4f4', '#f032e6', '#bfef45', '#469990', '#9a6324'];

  const S = {
    jobId: null, frame: null, job: null, labels: [], resolved: null, profiles: [],
    proposals: [], propFrame: null, warnings: [], imgW: 0, imgH: 0,
    qa: null, qaFrame: null, highlight: null, health: null,
    busy: '', status: '', statusKind: 'info', tab: 'propose', box: null, boxLabelId: null,
    frameCache: null, seq: 0, labelDiag: [], loadError: '',
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
          tried.push(`/api/labels?${query} → ${all.length} label`);
          if (top.length) return { labels: top, tried };
        } catch (err) {
          tried.push(`/api/labels?${query} → ${String(err.message || err).slice(0, 120)}`);
        }
      }
    }
    return { labels: [], tried };
  }

  async function getFrame() {
    const key = `${S.jobId}:${S.frame}`;
    if (S.frameCache && S.frameCache.key === key) return S.frameCache;
    const blob = await cvat('GET', `/api/jobs/${S.jobId}/data?type=frame&quality=original&number=${S.frame}`, undefined, true);
    const b64 = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(',')[1]);
      r.onerror = () => reject(r.error);
      r.readAsDataURL(blob);
    });
    const bmp = await createImageBitmap(blob);
    S.frameCache = { key, b64, w: bmp.width, h: bmp.height };
    if (bmp.close) bmp.close();
    return S.frameCache;
  }

  async function frameShapes() {
    const ann = await cvat('GET', `/api/jobs/${S.jobId}/annotations`);
    return {
      shapes: (ann.shapes || []).filter((s) => s.frame === S.frame),
      tracks: (ann.tracks || []).length,
    };
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

  const baseBody = () => ({ profile_id: cfg.profileId, labels: labelPayload(), overrides: overridePayload() });

  // ------------------------------------------------------- ngữ cảnh CVAT ---
  function readCtx() {
    const m = location.pathname.match(/\/tasks\/(\d+)\/jobs\/(\d+)/);
    if (!m) return null;
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
    S.job = null; S.labels = []; S.resolved = null;
    clearProposals();
    await run('Đang đọc job và label từ CVAT…', async () => {
      await readJob();
      const ok = await checkBackend();
      if (ok) await resolveLabels();
      if (!S.labels.length) setStatus(NO_LABELS, 'error');
      else if (!ok) setStatus(S.health.error, 'error');
    });
  }

  function clearProposals() {
    S.proposals = []; S.warnings = []; S.propFrame = null; S.highlight = null;
    S.qa = null; S.qaFrame = null;
    drawOverlay();
  }

  async function propose() {
    await run('SAM3 đang phân tích frame…', async () => {
      const seq = ++S.seq;
      const frame = S.frame;
      const fr = await getFrame();
      let existing = [];
      try { existing = (await frameShapes()).shapes; } catch (e) { /* vẫn chạy được khi không đọc được annotation */ }
      const res = await backend('POST', '/propose', Object.assign(baseBody(), { image_b64: fr.b64, existing }));
      if (seq !== S.seq || frame !== S.frame) return; // người dùng đã chuyển frame
      S.proposals = res.proposals || [];
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

  async function finishBox(box, mode) {
    await run('SAM3 đang tìm đối tượng trong hộp…', async () => {
      const frame = S.frame;
      const fr = await getFrame();
      const res = await backend('POST', '/interactive', Object.assign(baseBody(), {
        image_b64: fr.b64, label_id: S.boxLabelId, box, mode,
      }));
      if (frame !== S.frame) return;
      if (S.propFrame !== frame) { S.proposals = []; S.warnings = []; }
      S.propFrame = frame;
      let next = S.proposals.reduce((m, p) => Math.max(m, p.pid), 0);
      for (const p of res.proposals || []) { p.pid = ++next; S.proposals.push(p); }
      S.warnings = S.warnings.concat(res.warnings || []);
      setStatus(`Thêm ${(res.proposals || []).length} đề xuất từ hộp vừa vẽ.`, 'ok');
      drawOverlay();
    });
  }

  function toCvatShape(p) {
    const label = labelById(p.label_id) || { attributes: [] };
    const attributes = (label.attributes || []).map((a) => {
      let value = p.attributes ? p.attributes[normName(a.name)] : undefined;
      if (value === undefined) value = a.default_value === undefined || a.default_value === null ? '' : a.default_value;
      if (value === '' && a.input_type !== 'text' && a.values && a.values.length) value = a.values[0];
      return { spec_id: a.id, value: String(value) };
    });
    return {
      type: p.type, frame: S.propFrame, label_id: p.label_id, points: p.points,
      occluded: !!p.occluded, outside: false, z_order: p.z_order || 0, rotation: 0, group: 0,
      source: 'semi-auto', attributes, elements: [],
    };
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
      const shapes = chosen.map(toCvatShape);
      const res = await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=create`, {
        version: 0, tags: [], shapes, tracks: [],
      });
      GM_setValue('undo', { jobId: S.jobId, frame: S.propFrame, shapes: (res && res.shapes) || [], at: Date.now() });
      const frame = S.propFrame;
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
    if (!u || u.jobId !== S.jobId || !u.shapes || !u.shapes.length) {
      return setStatus('Không có lần ghi nào của job này để hoàn tác.', 'error');
    }
    if (!window.confirm(`Xoá ${u.shapes.length} nhãn tool đã ghi vào frame ${u.frame}?`)) return;
    await run('Đang hoàn tác…', async () => {
      await saveCvatUi();
      await cvat('PATCH', `/api/jobs/${S.jobId}/annotations?action=delete`, {
        version: 0, tags: [], shapes: u.shapes, tracks: [],
      });
      GM_setValue('undo', null);
      setStatus('Đã xoá. Đang tải lại trang…', 'ok');
      await sleep(400);
      reloadAtFrame(u.frame);
    });
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
    return !cfg.hidden && S.imgW > 0 && (S.proposals.length || S.box || (S.qa && S.qa.findings.length));
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
      }
      if (hot) {
        const [x1, y1, x2, y2] = p.bbox;
        g.append(svgEl('rect', Object.assign({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, base, {
          stroke: '#ffffff', 'stroke-width': 1.5, 'stroke-dasharray': '4 3', fill: 'none',
        })));
      }
      overlaySvg.append(g);
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
    nav button { flex: 1; border: 0; border-radius: 0; background: var(--bg-soft); padding: 7px 4px; border-bottom: 2px solid transparent; }
    nav button[aria-selected="true"] { background: var(--bg); border-bottom-color: var(--accent); font-weight: 600; }
    .body { padding: 10px; overflow: auto; display: flex; flex-direction: column; gap: var(--gap); }
    button { font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius);
      padding: 6px 10px; cursor: pointer; }
    button:hover:not(:disabled) { border-color: var(--accent); }
    button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    button.primary { background: var(--accent); color: var(--accent-text); border-color: var(--accent); font-weight: 600; }
    button.small { padding: 2px 6px; font-size: 12px; }
    input[type="text"], input[type="number"], input[type="password"], select { font: inherit; color: var(--text); background: var(--bg);
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
    );
  }

  function renderNav() {
    const tabs = [['propose', 'Gợi ý'], ['labels', 'Label'], ['qa', 'Kiểm tra'], ['settings', 'Cài đặt']];
    els.nav.replaceChildren(...tabs.map(([id, name]) => h('button', {
      type: 'button', role: 'tab', 'aria-selected': String(S.tab === id), text: name,
      onclick: () => { S.tab = id; S.highlight = null; render(); drawOverlay(); },
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

    if (S.proposals.length && S.propFrame === S.frame) {
      const checked = S.proposals.filter((p) => p.checked).length;
      out.push(h('div', { class: 'row' },
        h('span', { class: 'grow muted', text: `${S.proposals.length} đề xuất · ${checked} đã chọn` }),
        h('button', { class: 'small', type: 'button', text: 'Chọn hết', onclick: () => { S.proposals.forEach((p) => { p.checked = true; }); render(); drawOverlay(); } }),
        h('button', { class: 'small', type: 'button', text: 'Bỏ mục có cờ', onclick: () => { S.proposals.forEach((p) => { if (p.flags.length) p.checked = false; }); render(); drawOverlay(); } }),
      ));
      out.push(h('ul', {}, S.proposals.map((p) => {
        const options = S.labels.filter((l) => !l.type || l.type === 'any' || l.type === p.type || l.id === p.label_id);
        const select = h('select', { class: 'grow', title: 'Đổi class', onchange: (ev) => { p.label_id = Number(ev.target.value); p.label_name = (labelById(p.label_id) || {}).name; render(); drawOverlay(); } },
          options.map((l) => h('option', { value: String(l.id), text: l.name })));
        select.value = String(p.label_id);
        const li = h('li', { class: 'item' },
          h('div', { class: 'row' },
            h('input', { type: 'checkbox', checked: p.checked, title: 'Ghi nhãn này vào CVAT', onchange: (ev) => { p.checked = ev.target.checked; render(); drawOverlay(); } }),
            h('span', { class: 'swatch', style: `background:${labelColor(p.label_id)}` }),
            select,
            h('span', { class: 'muted small-text', text: `${p.type} · ${Math.round(p.score * 100)}%` }),
          ),
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
        h('button', { type: 'button', disabled: !!S.busy, onclick: () => { clearProposals(); render(); }, text: 'Bỏ đề xuất' }),
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
      return h('tr', { title: c.note || '' },
        h('td', {}, h('input', { type: 'checkbox', checked: c.enabled, disabled: !c.supported, title: 'Bật label này', onchange: (ev) => setOverride(label, { enabled: ev.target.checked }) })),
        h('td', {}, h('div', { text: c.name }), c.note ? h('div', { class: 'small-text', style: 'color:var(--warn)', text: c.note }) : null,
          !c.matched && S.resolved.profile_id !== 'generic' ? h('div', { class: 'small-text muted', text: 'Không có trong guideline' }) : null),
        h('td', {}, shape),
        h('td', {}, h('input', { type: 'text', value: c.prompts.join(', '), placeholder: derived ? 'suy ra bằng luật' : 'prompt tiếng Anh',
          title: 'Prompt cho SAM3, cách nhau bằng dấu phẩy',
          onchange: (ev) => setOverride(label, { prompts: ev.target.value.split(',').map((s) => s.trim()).filter(Boolean) }) })),
        h('td', {}, h('input', { type: 'number', min: '0.05', max: '0.99', step: '0.05', value: String(c.threshold), title: 'Ngưỡng tin cậy',
          onchange: (ev) => setOverride(label, { threshold: Number(ev.target.value) }) })),
      );
    });
    return [
      h('p', { class: 'muted small-text', style: 'margin:0', text: 'Label lấy từ task CVAT. Prompt nên là cụm danh từ tiếng Anh ngắn. Thay đổi được nhớ theo tên label.' }),
      h('table', {}, h('thead', {}, h('tr', {}, ['', 'Label', 'Shape', 'Prompt', 'Ngưỡng'].map((t) => h('th', { text: t })))), h('tbody', {}, rows)),
      h('div', { class: 'row' },
        h('button', { type: 'button', disabled: !!S.busy, text: 'Về mặc định của profile', onclick: () => { cfg.overrides = {}; saveCfg(); clearProposals(); run('Đang cập nhật label…', resolveLabels); } }),
        h('button', { type: 'button', disabled: !!S.busy, text: 'Nạp lại profile', title: 'Đọc lại các file YAML trong thư mục profiles/ của backend',
          onclick: () => run('Đang nạp lại profile…', async () => { await backend('POST', '/profiles/reload'); await checkBackend(); await resolveLabels(); }) }),
      ),
    ];
  }

  function renderQa() {
    const out = [h('button', { class: 'primary', type: 'button', disabled: !S.resolved || !!S.busy, onclick: runQa, text: 'Kiểm tra annotation của frame này' })];
    if (!S.qa || S.qaFrame !== S.frame) {
      out.push(h('p', { class: 'muted', style: 'margin:0', text: 'Đối chiếu annotation đã lưu của frame với checklist trong guideline. Hãy lưu (Ctrl+S) trước khi kiểm tra.' }));
      return out;
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
    return out;
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
      h('label', { class: 'field' }, h('span', { text: 'Độ đậm vùng tô xem trước' }),
        h('input', { type: 'range', min: '0', max: '0.8', step: '0.05', value: String(cfg.opacity), oninput: (ev) => { cfg.opacity = Number(ev.target.value); saveCfg(); drawOverlay(); } })),
      text('CSS selector của ảnh nền canvas CVAT', 'canvasSelector', 'text', 'Chỉ đổi khi phần xem trước không hiện. Mặc định: #cvat_canvas_background'),
      h('p', { class: 'muted small-text', style: 'margin:0', text: `SAM3 CVAT Assist ${VERSION}. Nhãn do tool ghi có source = "semi-auto". Phím tắt: Alt+G gợi ý, Alt+A ghi.` }),
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
    if (active && els.body.contains(active) && /^(INPUT|SELECT)$/.test(active.tagName) && active.type !== 'checkbox' && S.busy) return;
    const view = { propose: renderPropose, labels: renderLabels, qa: renderQa, settings: renderSettings }[S.tab]();
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
      clearProposals(); setStatus(''); render();
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
