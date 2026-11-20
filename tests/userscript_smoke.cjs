/*
 * Chạy thử userscript trong trình duyệt giả (jsdom) với một CVAT giả và backend thật ở chế độ mock.
 * Kiểm tra luồng: đọc job -> gợi ý -> ghi vào CVAT -> hoàn tác -> kiểm tra QA.
 *
 *   npm install jsdom
 *   node tests/userscript_smoke.cjs
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');
const { spawn, execFileSync } = require('child_process');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8799;
const BACKEND = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function until(fn, what, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try { const v = fn(); if (v) return v; } catch (e) { /* thử lại */ }
    await sleep(100);
  }
  throw new Error('Hết thời gian chờ: ' + what);
}

const OBJECTS = ['pedestrian', 'rider', 'car', 'truck', 'bus', 'train', 'motorcycle', 'bicycle', 'traffic light', 'traffic sign'];
const OTHERS = ['area/direct', 'area/alternative', 'lane/crosswalk', 'lane/double white', 'lane/double yellow', 'lane/double other',
  'lane/road curb', 'lane/single white', 'lane/single yellow', 'lane/single other'];
const LABELS = [...OBJECTS, ...OTHERS].map((name, i) => ({
  id: 100 + i, name, type: 'any', color: '#33aa55', parent_id: null, has_parent: false,
  attributes: OBJECTS.includes(name)
    ? [{ id: 900 + i, name: 'truncated', input_type: 'checkbox', default_value: 'false', values: ['false'], mutable: false }]
    : [],
}));

async function main() {
  const jpg = path.join(os.tmpdir(), `sam3-assist-smoke-${process.pid}.jpg`);
  execFileSync('python3', ['-c', `import cv2, numpy; cv2.imwrite(r"${jpg}", numpy.full((720, 1280, 3), 90, numpy.uint8))`]);
  const frameBytes = fs.readFileSync(jpg);
  fs.unlinkSync(jpg);

  const server = spawn('python3', ['-m', 'backend'], {
    cwd: ROOT, stdio: 'ignore',
    // Thư viện guideline / dữ liệu tự học tạm: test không ghi vào thư mục thật của repo.
    env: { ...process.env, SAM3_ENGINE: 'mock', SAM3_PORT: String(PORT), SAM3_API_KEY: 'smoke-key',
      SAM3_LIBRARY_DIR: require('fs').mkdtempSync(require('path').join(require('os').tmpdir(), 'sam3-smoke-lib-')) },
  });
  try {
    await until(() => true, 'khởi động', 1);
    for (let i = 0; i < 100; i++) {
      try { if ((await fetch(BACKEND + '/health')).ok) break; } catch (e) { await sleep(200); }
    }
    await scenario(frameBytes);
    console.log('SMOKE TEST: PASS');
  } finally {
    server.kill();
  }
}

async function scenario(frameBytes) {
  const consoleErrors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => consoleErrors.push(String(e.message || e)));
  vc.on('error', (...a) => consoleErrors.push(a.map(String).join(' ')));
  const dom = new JSDOM(
    `<!doctype html><body>
       <div class="cvat-canvas-container"><canvas id="cvat_canvas_background" width="1280" height="720"></canvas></div>
       <div class="cvat-player-frame-selector"><input value="3"></div>
       <button class="cvat-annotation-header-save-button"></button>
     </body>`,
    { url: 'https://cvat.edufun.ai/tasks/5/jobs/12?frame=3', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc },
  );
  const win = dom.window;
  const calls = [];
  const store = { shapes: [], nextId: 1, issues: [], saveClicks: 0 };
  win.document.querySelector('.cvat-annotation-header-save-button').addEventListener('click', () => { store.saveClicks++; });
  win.document.cookie = 'csrftoken=abc123';
  win.localStorage.setItem('currentOrganization', 'team1');

  const json = (data, status = 200) => ({ ok: status < 300, status, json: async () => data, text: async () => JSON.stringify(data) });
  win.fetch = async (url, opts = {}) => {
    const u = new URL(url);
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ method, path: u.pathname + u.search, body, headers: opts.headers || {} });
    if (method !== 'GET') assert.strictEqual(opts.headers['X-CSRFToken'], 'abc123', 'thiếu CSRF token');
    // Giống CVAT thật: chỉ chấp nhận kiểu JSON riêng của CVAT (hoặc */* khi tải ảnh).
    if (!['application/vnd.cvat+json', '*/*'].includes(opts.headers.Accept)) {
      return json({ detail: 'Could not satisfy the request Accept header.' }, 406);
    }
    if (u.pathname === '/api/jobs/12') return json({ id: 12, task_id: 5, start_frame: 0, stop_frame: 9 });
    if (u.pathname === '/api/labels') {
      const visible = u.searchParams.get('org') === 'team1' ? LABELS : [];
      return json({ count: visible.length, next: null, results: visible });
    }
    if (u.pathname === '/api/jobs/12/data') {
      assert.strictEqual(u.searchParams.get('number'), '3');
      return { ok: true, status: 200, blob: async () => new win.Blob([frameBytes], { type: 'image/jpeg' }) };
    }
    if (u.pathname === '/api/jobs/12/annotations' && method === 'GET') return json({ version: 1, tags: [], shapes: store.shapes, tracks: [] });
    if (u.pathname === '/api/jobs/12/annotations' && method === 'PATCH') {
      const action = u.searchParams.get('action');
      if (action === 'create') {
        const created = body.shapes.map((s) => ({ ...s, id: store.nextId++ }));
        store.shapes.push(...created);
        return json({ version: 2, tags: [], shapes: created, tracks: [] });
      }
      if (action === 'update') {
        for (const s of body.shapes) Object.assign(store.shapes.find((x) => x.id === s.id), s);
        return json({ version: 3, tags: [], shapes: body.shapes, tracks: [] });
      }
      if (action === 'delete') {
        const ids = new Set(body.shapes.map((s) => s.id));
        store.shapes = store.shapes.filter((s) => !ids.has(s.id));
        return json({ version: 3, tags: [], shapes: body.shapes, tracks: [] });
      }
    }
    if (u.pathname === '/api/issues' && method === 'POST') { store.issues.push(body); return json({ id: 1, ...body }, 201); }
    return json({ detail: 'not found' }, 404);
  };
  win.createImageBitmap = async () => ({ width: 1280, height: 720, close() {} });
  win.confirm = () => true;
  // 0.9: panel hỏi bằng hộp thoại riêng (không còn window.confirm/prompt): test tự bấm nút chính
  const __dialogs = [];
  setInterval(() => {
    const hostEl = win.document.getElementById('sam3-cvat-assist');
    const back = hostEl && hostEl.shadowRoot && hostEl.shadowRoot.querySelector('.dlg-back');
    if (back) { __dialogs.push(back.querySelector('p').textContent); back.querySelector('.dlg .primary').click(); }
  }, 20).unref();
  win.prompt = (_q, def) => def;

  const gm = {};
  win.GM_getValue = (k, d) => (k in gm ? JSON.parse(gm[k]) : d);
  win.GM_setValue = (k, v) => { gm[k] = JSON.stringify(v); };
  win.GM_registerMenuCommand = () => {};
  win.GM_xmlhttpRequest = (o) => {
    fetch(o.url, { method: o.method, headers: o.headers, body: o.data })
      .then(async (r) => o.onload({ status: r.status, responseText: await r.text() }))
      .catch(() => o.onerror());
  };
  win.GM_setValue('cfg', { backendUrl: BACKEND, apiKey: 'smoke-key' });

  win.eval(fs.readFileSync(path.join(ROOT, 'userscript', 'sam3-cvat-assist.user.js'), 'utf8'));
  const root = win.document.getElementById('sam3-cvat-assist').shadowRoot;
  const TAB_NAMES = ['Gợi ý', 'Cả job', 'Kiểm tra', 'Guideline', 'Label', 'Tự học', 'Suggest', 'Whole job', 'Check', 'Labels', 'Learning'];
  // 0.9.2: bỏ qua tiêu đề mục gập (cũng là nút) và quay lại từ Cài đặt khi cần bấm tab
  const button = (text) => {
    if (TAB_NAMES.includes(text) && root.querySelector('nav.back')) root.querySelector('nav.back button').click();
    return [...root.querySelectorAll('button:not(.sec-h)')].find((b) => b.textContent.trim().startsWith(text));
  };
  const statusText = () => root.querySelector('.status').textContent;

  // 1. Đọc job, label, chọn đúng profile
  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel sẵn sàng: ' + statusText());
  assert.match(root.querySelector('.meta').textContent, /Job 12 · Frame 3/);
  assert.match(root.querySelector('.meta').textContent, /BBox \/ Polygon \/ Polyline/);
  assert.match(root.textContent, /GIẢ LẬP/);

  // 2. Tab Label liệt kê đủ label của task
  button('Label').click();
  assert.strictEqual(root.querySelectorAll('tbody tr').length, LABELS.length);
  button('Gợi ý').click();

  // 3. Gợi ý
  button('Gợi ý nhãn').click();
  await until(() => root.querySelectorAll('li.item').length > 0, 'có đề xuất: ' + statusText());
  const count = root.querySelectorAll('li.item').length;
  const svg = win.document.querySelector('svg');
  assert.strictEqual(svg.getAttribute('viewBox'), '0 0 1280 720');
  assert.ok(svg.children.length >= count, 'overlay phải vẽ đủ đề xuất');

  // 4. Bỏ tick một đề xuất rồi ghi
  const ticked = () => [...root.querySelectorAll('li.item input[type="checkbox"]')].filter((c) => c.checked).length;
  const firstTicked = [...root.querySelectorAll('li.item input[type="checkbox"]')].filter((c) => c.checked).pop();
  const expected = ticked() - 1;
  firstTicked.click();
  await until(() => button('Chỉ ghi') && button('Chỉ ghi').textContent.includes(` ${expected} `), 'nút ghi cập nhật số lượng');
  button('Chỉ ghi').click();
  const patch = await until(() => calls.find((c) => c.method === 'PATCH' && c.path.includes('action=create')), 'PATCH create: ' + statusText());
  assert.strictEqual(patch.path, '/api/jobs/12/annotations?action=create');
  assert.strictEqual(patch.body.shapes.length, expected);
  assert.deepStrictEqual([patch.body.version, patch.body.tags, patch.body.tracks], [0, [], []]);
  assert.strictEqual(store.saveClicks, 1, 'phải bấm Save của CVAT trước khi ghi');
  const validIds = new Set(LABELS.map((l) => l.id));
  const kinds = new Set();
  for (const s of patch.body.shapes) {
    kinds.add(s.type);
    assert.ok(validIds.has(s.label_id));
    assert.strictEqual(s.frame, 3);
    assert.ok(!('source' in s), 'mặc định không gửi trường source');
    assert.strictEqual(typeof s.occluded, 'boolean');
    assert.ok(Array.isArray(s.points) && s.points.length >= 4 && s.points.every((v) => Number.isFinite(v)));
    const label = LABELS.find((l) => l.id === s.label_id);
    assert.deepStrictEqual(s.attributes.map((a) => a.spec_id), label.attributes.map((a) => a.id));
    for (const a of s.attributes) assert.ok(['true', 'false'].includes(a.value));
    if (s.type === 'rectangle') assert.strictEqual(s.points.length, 4);
    if (s.type === 'polygon') assert.ok(s.points.length >= 6);
  }
  assert.ok(kinds.has('rectangle') && kinds.has('polyline') && kinds.has('polygon'), 'đủ 3 loại shape: ' + [...kinds]);
  await until(() => store.shapes.length === expected && !root.querySelectorAll('li.item').length, 'đã ghi xong');

  // 5. Kiểm tra QA trên các shape vừa ghi
  await sleep(800);
  button('Kiểm tra').click();
  button('Kiểm tra annotation').click();
  await until(() => /Checklist/.test(root.textContent), 'kết quả QA: ' + statusText());
  assert.match(root.textContent, /Đã rà 02 lần/);

  // 6. Tạo Issue từ một phát hiện (nếu có) và hoàn tác lần ghi
  const issueBtn = button('Tạo Issue');
  if (issueBtn) {
    issueBtn.click();
    await until(() => store.issues.length === 1, 'tạo issue');
    assert.strictEqual(store.issues[0].job, 12);
    assert.strictEqual(store.issues[0].frame, 3);
    assert.strictEqual(store.issues[0].position.length, 4);
    await until(() => /Đã tạo Issue/.test(statusText()), 'issue xong');
  }
  // 6b. Đổi source semi-auto -> manual cho nhãn đã lưu, không đụng nhãn frame khác
  store.shapes[0].source = 'semi-auto';
  store.shapes[1].source = 'semi-auto';
  store.shapes.push({ id: 999, type: 'rectangle', frame: 7, label_id: 102, points: [1, 1, 9, 9], source: 'semi-auto' });
  const pointsBefore = JSON.stringify(store.shapes[0].points);
  const undoBefore = gm.undoStack;
  root.querySelector('button[aria-label="Cài đặt panel"]').click();
  button('Frame này').click();
  await until(() => store.shapes[0].source === 'manual' && store.shapes[1].source === 'manual', 'đổi source: ' + statusText());
  assert.strictEqual(store.shapes.find((s) => s.id === 999).source, 'semi-auto', 'không được đổi nhãn của frame khác');
  assert.strictEqual(JSON.stringify(store.shapes[0].points), pointsBefore, 'hình học phải giữ nguyên');
  const upd = calls.find((c) => c.path.includes('action=update'));
  assert.strictEqual(upd.body.shapes.length, 2);
  store.shapes = store.shapes.filter((s) => s.id !== 999);
  gm.undoStack = undoBefore; // khôi phục để kiểm tra tiếp bước hoàn tác (0.8.0: hoàn tác nhiều bước)
  await until(() => /Đã đổi 2 nhãn/.test(statusText()), 'đổi xong');
  await sleep(900);

  button('Gợi ý').click();
  button('Hoàn tác').click();
  await until(() => store.shapes.length === 0, 'hoàn tác: ' + statusText());

  const real = consoleErrors.filter((e) => !/Not implemented: navigation/.test(e));
  assert.deepStrictEqual(real, [], 'lỗi console: ' + real.join(' | '));
  dom.window.close();
}

main().catch((err) => { console.error('SMOKE TEST: FAIL\n', err); process.exit(1); });
