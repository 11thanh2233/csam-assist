/*
 * Chạy thử userscript với CVAT giả + backend mock cho ba luồng mới:
 *   A. Job skeleton (HumanPose-17: person 17 điểm; Facial Landmark-50: 7 skeleton mặt, 50 điểm): gợi ý một frame, ghi, gợi ý cho cả job, duyệt frame có cờ.
 *   B. Job video: theo dõi cả đoạn frame, ghi thành track, hoàn tác.
 *   C. Soạn guideline nháp: quét ảnh mẫu, chốt câu hỏi, xem bằng chứng, mở trang duyệt, dùng làm profile.
 *
 *   npm install jsdom
 *   node tests/userscript_smoke_v2.cjs
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');
const { spawn, execFileSync } = require('child_process');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8798;
const BACKEND = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function until(fn, what, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try { const v = fn(); if (v) return v; } catch (e) { /* thử lại */ }
    await sleep(100);
  }
  throw new Error('Hết thời gian chờ: ' + what);
}

// Label skeleton kiểu CVAT /api/labels: label cha không kèm sublabels, các điểm là label con có parent_id.
function skeletonLabels() {
  const raw = JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'driver_skeleton_labels.json'), 'utf8'));
  // Task thật trên cvat.edufun.ai đặt tên label người là 'person_vf17', không phải 'person' như trong guideline.
  raw.find((l) => l.name === 'person').name = 'person_vf17';
  const out = [];
  for (const l of raw) {
    out.push({ id: l.id, name: l.name, type: 'skeleton', color: l.color, attributes: [], parent_id: null, has_parent: false });
    for (const s of l.sublabels) out.push({ id: s.id, name: s.name, type: 'points', color: s.color, attributes: [], parent_id: l.id, has_parent: true });
  }
  return { list: out, raw };
}

function fakeCvat(win, opts, frameBytes) {
  const store = { shapes: [], tracks: [], nextId: 1, calls: [] };
  const json = (data, status = 200) => ({ ok: status < 300, status, json: async () => data, text: async () => JSON.stringify(data) });
  const job = `/api/jobs/${opts.jobId}`;
  win.fetch = async (url, o = {}) => {
    const u = new URL(url);
    const method = o.method || 'GET';
    const body = o.body ? JSON.parse(o.body) : undefined;
    store.calls.push({ method, path: u.pathname + u.search, body });
    if (!['application/vnd.cvat+json', '*/*'].includes(o.headers.Accept)) return json({ detail: 'Could not satisfy the request Accept header.' }, 406);
    if (u.pathname === job) return json({ id: opts.jobId, task_id: 9, start_frame: 0, stop_frame: opts.stop, mode: opts.mode });
    if (u.pathname === '/api/labels') return json({ count: opts.labels.length, next: null, results: opts.labels });
    if (u.pathname === '/api/tasks/9' && method === 'PATCH') {
      // Giống CVAT: label không có id là label mới của task.
      for (const l of body.labels) opts.labels.push({ ...l, id: 900 + opts.labels.length, parent_id: null, has_parent: false });
      return json({ id: 9, labels: { count: opts.labels.length } });
    }
    if (u.pathname === '/api/issues' && method === 'POST') { (store.issues = store.issues || []).push(body); return json({ id: 700 + store.issues.length, ...body }); }
    if (u.pathname === `${job}/data/meta`) return json(Object.assign({ start_frame: 0, stop_frame: opts.stop, frame_filter: '', deleted_frames: [], included_frames: null }, opts.meta || {}));
    if (u.pathname === `${job}/data`) {
      store.frames = (store.frames || 0) + 1;
      return { ok: true, status: 200, blob: async () => new win.Blob([frameBytes], { type: 'image/jpeg' }) };
    }
    if (u.pathname === `${job}/annotations` && method === 'GET') return json({ version: 1, tags: [], shapes: store.shapes, tracks: store.tracks });
    if (u.pathname === `${job}/annotations` && method === 'PATCH') {
      const action = u.searchParams.get('action');
      if (action === 'create') {
        const shapes = body.shapes.map((s) => ({ ...s, id: store.nextId++ }));
        const tracks = body.tracks.map((t) => ({ ...t, id: store.nextId++ }));
        store.shapes.push(...shapes);
        store.tracks.push(...tracks);
        return json({ version: 2, tags: [], shapes, tracks });
      }
      if (action === 'delete') {
        const ids = new Set([...body.shapes, ...body.tracks].map((s) => s.id));
        store.shapes = store.shapes.filter((s) => !ids.has(s.id));
        store.tracks = store.tracks.filter((s) => !ids.has(s.id));
        return json({ version: 3, tags: [], shapes: body.shapes, tracks: body.tracks });
      }
    }
    return json({ detail: 'not found' }, 404);
  };
  return store;
}

function openPage(opts, frameBytes) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errors.push(String(e.message || e)));
  vc.on('error', (...a) => errors.push(a.map(String).join(' ')));
  const dom = new JSDOM(
    opts.html || `<!doctype html><body>
       <div class="cvat-canvas-container"><canvas id="cvat_canvas_background" width="1280" height="720"></canvas></div>
       <div class="cvat-player-frame-selector"><input value="${opts.frame}"></div>
     </body>`,
    { url: `${opts.origin || 'https://cvat.edufun.ai'}/tasks/9/jobs/${opts.jobId}?frame=${opts.frame}`, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc },
  );
  const win = dom.window;
  win.document.cookie = 'csrftoken=abc123';
  const store = fakeCvat(win, opts, frameBytes);
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
  const clipboard = [];
  win.GM_setClipboard = (text) => { clipboard.push(text); };
  win.GM_xmlhttpRequest = (o) => {
    fetch(o.url, { method: o.method, headers: o.headers, body: o.data })
      .then(async (r) => o.onload({ status: r.status, responseText: await r.text() }))
      .catch(() => o.onerror());
  };
  win.GM_setValue('cfg', { backendUrl: BACKEND, apiKey: 'smoke-key', reloadAfterApply: false });
  win.eval(fs.readFileSync(path.join(ROOT, 'userscript', 'sam3-cvat-assist.user.js'), 'utf8'));
  const root = win.document.getElementById('sam3-cvat-assist').shadowRoot;
  const TAB_NAMES = ['Gợi ý', 'Cả job', 'Kiểm tra', 'Guideline', 'Label', 'Tự học', 'Suggest', 'Whole job', 'Check', 'Labels', 'Learning'];
  // 0.9.2: bỏ qua tiêu đề mục gập (cũng là nút) và quay lại từ Cài đặt khi cần bấm tab
  const button = (text) => {
    if (TAB_NAMES.includes(text) && root.querySelector('nav.back')) root.querySelector('nav.back button').click();
    return [...root.querySelectorAll('button:not(.sec-h)')].find((b) => b.textContent.trim().startsWith(text));
  };
  const status = () => root.querySelector('.status').textContent;
  return { dom, win, root, store, button, status, errors, clipboard };
}

async function skeletonScenario(frameBytes) {
  const { list, raw } = skeletonLabels();
  const page = openPage({ jobId: 20, frame: 3, stop: 4, mode: 'annotation', labels: list }, frameBytes);
  const { root, store, button, status, win } = page;
  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel skeleton sẵn sàng: ' + status());
  assert.match(root.querySelector('.meta').textContent, /HumanPose-17/);

  button('Label').click();
  const table = root.querySelector('tbody').textContent;
  assert.match(table, /skeleton 17 điểm/);
  assert.match(table, /skeleton 12 điểm/);
  assert.strictEqual((table.match(/skeleton \d+ điểm/g) || []).length, 8, 'person + 7 skeleton mặt');
  assert.doesNotMatch(table, /ghép theo thứ tự|không có điểm|chưa nhận ra/, 'sublabel 1-17 và 0-49 phải ghép được theo số');
  assert.match(table, /person_vf17Tool coi label này là 'person' của guideline \(theo tên gần giống\)/, 'phải nhận person_vf17 là person: ' + table.slice(0, 200));
  // Label nhận theo tên gần giống có ô để người dùng chỉ tay skeleton khác; label trùng tên thì không cần
  const pickers = [...root.querySelectorAll('tbody select')].filter((s) => /skeleton nào/.test(s.title));
  assert.strictEqual(pickers.length, 1);
  assert.deepStrictEqual([...pickers[0].options].map((o) => o.value).slice(0, 3), ['', 'person', 'longmaytrai']);
  button('Gợi ý').click();

  // A1. Gợi ý một frame: 1 skeleton person + 7 skeleton mặt
  button('Gợi ý nhãn').click();
  await until(() => root.querySelectorAll('li.item').length >= 8, 'đề xuất skeleton: ' + status());
  const items = [...root.querySelectorAll('li.item')].map((li) => li.textContent);
  assert.match(items[0], /\/17 điểm · 1 occluded/, 'person đứng đầu, đúng thứ tự label của task: ' + items[0]);
  assert.match(items[0], /Kiểm tra attribute/);
  assert.match(root.querySelector('ul').textContent, /12\/12 điểm/);
  const circles = win.document.querySelectorAll('svg circle').length;
  assert.strictEqual(circles, 16 + 50, 'vẽ 16 điểm người (1 điểm outside không vẽ) và 50 điểm mặt, có ' + circles);
  assert.ok(win.document.querySelectorAll('svg line').length >= 47, 'phải vẽ đường nối');
  // Màu theo phía của ẢNH: điểm R (xanh) nằm bên phải điểm L (đỏ)
  const dots = [...win.document.querySelectorAll('svg circle')];
  const xs = (color) => dots.filter((c) => [c.getAttribute('fill'), c.getAttribute('stroke')].includes(color)).map((c) => Number(c.getAttribute('cx')));
  const avg = (a) => a.reduce((s, v) => s + v, 0) / a.length;
  assert.ok(xs('#2e6fd6').length >= 7 && xs('#e8473c').length >= 7, 'phải có điểm R và điểm L');
  assert.ok(avg(xs('#2e6fd6')) > avg(xs('#e8473c')), 'điểm R (xanh) phải ở bên phải ảnh so với điểm L (đỏ)');

  button('Chỉ ghi').click();
  const patch = await until(() => store.calls.find((c) => c.method === 'PATCH' && c.path.includes('create')), 'ghi skeleton: ' + status());
  const byLabel = Object.fromEntries(patch.body.shapes.map((s) => [s.label_id, s]));
  assert.strictEqual(patch.body.shapes.length, 8);
  for (const l of raw) {
    const s = byLabel[l.id];
    assert.ok(s, 'thiếu skeleton ' + l.name);
    assert.strictEqual(s.type, 'skeleton');
    assert.deepStrictEqual(s.points, []);
    assert.strictEqual(s.frame, 3);
    assert.strictEqual(s.source, undefined, 'mặc định không ghi source (CVAT ghi manual)');
    assert.deepStrictEqual(s.elements.map((e) => e.label_id).sort(), l.sublabels.map((x) => x.id).sort());
    for (const e of s.elements) {
      assert.strictEqual(e.type, 'points');
      assert.strictEqual(e.points.length, 2);
      assert.strictEqual(e.frame, 3);
      assert.strictEqual(typeof e.outside, 'boolean');
    }
  }
  const personLabel = raw.find((l) => l.name === 'person_vf17');
  const person = byLabel[personLabel.id];
  const el = (name) => person.elements.find((e) => e.label_id === personLabel.sublabels.find((x) => x.name === name).id);
  assert.strictEqual(el('10').occluded, true, 'điểm 10 (cổ tay R) Occluded theo độ tin cậy của model giả lập');
  assert.strictEqual(el('17').outside, true, 'điểm 17 (cổ chân L) Outside');
  assert.ok(el('6').points[0] > el('7').points[0], 'vai R (6) ở bên phải ảnh so với vai L (7)');
  await until(() => !root.querySelectorAll('li.item').length, 'xoá đề xuất sau khi ghi');

  // A2. Gợi ý cho cả job: frame 3 đã có nhãn nên được bỏ qua
  button('Cả job').click();
  button('Gợi ý cho').click();
  await until(() => /4 frame có kết quả/.test(root.querySelector('.body').textContent), 'gợi ý cả job: ' + status());
  assert.match(status(), /bỏ qua 1 frame đã có đủ label/);
  // Skeleton người luôn có cờ (model giả lập có một điểm Occluded) -> mặc định chỉ ghi 7 skeleton mặt của mỗi frame
  const writeAll = button('Ghi ');
  assert.match(writeAll.textContent, /Ghi 28 nhãn/);
  const before = store.calls.filter((c) => c.path.includes('action=create')).length;
  writeAll.click();
  await until(() => store.calls.filter((c) => c.path.includes('action=create')).length === before + 1, 'ghi cả job');
  const batchPatch = store.calls.filter((c) => c.path.includes('action=create')).pop();
  assert.deepStrictEqual([...new Set(batchPatch.body.shapes.map((s) => s.frame))].sort(), [0, 1, 2, 4]);
  assert.ok(batchPatch.body.shapes.every((s) => s.label_id !== personLabel.id));

  // A3. Frame có cờ còn lại để duyệt: bấm số frame -> tab Gợi ý hiện ngay đề xuất của frame đó
  await until(() => /Frame cần duyệt/.test(root.querySelector('.body').textContent), 'danh sách frame cần duyệt: ' + status());
  [...root.querySelectorAll('button')].find((b) => b.textContent.trim() === '1').click();
  await until(() => /Frame 1/.test(root.querySelector('.meta').textContent), 'chuyển tới frame 1');
  button('Gợi ý').click();
  assert.match(root.querySelector('.body').textContent, /lượt gợi ý cho cả job/);
  assert.strictEqual(root.querySelectorAll('li.item').length, 1);
  page.dom.window.close();
  return page.errors;
}

async function trackingScenario(frameBytes) {
  const labels = [{ id: 93, name: 'person', type: 'rectangle', color: '#c06060', attributes: [], parent_id: null, has_parent: false }];
  const page = openPage({ jobId: 30, frame: 5, stop: 11, mode: 'interpolation', labels }, frameBytes);
  const { root, store, button, status, win } = page;
  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel video sẵn sàng: ' + status());
  button('Cả job').click();
  const headings = [...root.querySelectorAll('.sec-h > span, h2')].map((x) => x.textContent);   // 0.9.2: mục gập có tiêu đề ở nút
  assert.match(headings[0], /Theo dõi/, 'job video: phần theo dõi đứng đầu');
  button('Theo dõi 12 frame').click();
  await until(() => /1 track/.test(root.querySelector('.body').textContent), 'theo dõi xong: ' + status());
  assert.strictEqual(store.calls.filter((c) => c.path.includes('/data?')).length, 12);
  assert.match(root.querySelector('ul').textContent, /#1 person · frame 0–11/);
  const rects = [...win.document.querySelectorAll('svg rect')];
  assert.ok(rects.length >= 1 && /#1/.test(win.document.querySelector('svg text').textContent), 'vẽ track trên frame đang xem');

  button('Ghi 1 track').click();
  const patch = await until(() => store.calls.find((c) => c.method === 'PATCH' && c.path.includes('create')), 'ghi track: ' + status());
  assert.strictEqual(patch.body.shapes.length, 0);
  assert.strictEqual(patch.body.tracks.length, 1);
  const tr = patch.body.tracks[0];
  assert.strictEqual(tr.label_id, 93);
  assert.strictEqual(tr.frame, 0);
  assert.ok(!('source' in tr));
  assert.ok(tr.shapes.length >= 2 && tr.shapes.every((s) => s.type === 'rectangle' && s.points.length === 4));
  assert.ok(tr.shapes.every((s) => !s.outside), 'người có mặt tới frame cuối của job: không có outside');
  await until(() => store.tracks.length === 1 && /Đã ghi 1 track/.test(status()), 'đã ghi track');

  await until(() => !button('Hoàn tác').disabled, 'panel rảnh');
  button('Hoàn tác').click();
  await until(() => store.tracks.length === 0, 'hoàn tác track: ' + status());
  page.dom.window.close();
  return page.errors;
}

async function guidelineScenario(frameBytes) {
  const names = ['car', 'truck', 'lane/crosswalk'];
  const labels = names.map((name, i) => ({ id: 200 + i, name, type: i === 2 ? 'polyline' : 'rectangle', color: '#33aa55', attributes: [], parent_id: null, has_parent: false }));
  const page = openPage({ jobId: 40, frame: 2, stop: 19, mode: 'annotation', labels }, frameBytes);
  const { root, store, button, status, win } = page;
  const opened = [];
  win.open = (url) => { opened.push(url); return null; };
  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel sẵn sàng: ' + status());
  button('Guideline').click();
  assert.match(root.querySelector('.body').textContent, /Backend chưa cấu hình LLM/);
  const sample = root.querySelector('input[type="number"]');
  sample.value = '10';
  sample.dispatchEvent(new win.Event('change'));
  await until(() => button('Quét 10 ảnh'), 'đổi số ảnh mẫu');
  button('Quét 10 ảnh').click();
  await until(() => /Câu hỏi cần chốt/.test(root.querySelector('.body').textContent), 'soạn nháp xong: ' + status(), 60000);
  assert.strictEqual(new Set(store.calls.filter((c) => c.path.includes('/data?')).map((c) => c.path)).size, 10, 'tải đúng 10 ảnh mẫu khác nhau');
  const body = () => root.querySelector('.body').textContent;
  assert.match(body(), /Bản nháp · 10 ảnh/);
  assert.match(body(), /Phản chiếu, bóng đổ, poster/);
  assert.match(body(), /hai mép dài của cả vùng/, 'gợi ý cách vẽ crosswalk theo bài học đã có');

  // Chốt một câu hỏi -> bản nháp cập nhật
  const scope = [...root.querySelectorAll('li.item')].find((li) => /task không có label này/.test(li.textContent));
  const select = scope.querySelector('select');
  select.value = 'add';
  select.dispatchEvent(new win.Event('change'));
  await until(() => /Đã cập nhật bản nháp/.test(status()), 'trả lời câu hỏi: ' + status());
  const openBefore = Number(body().match(/còn (\d+)\//)[1]);
  assert.ok(openBefore >= 1);

  // Label theo guideline: class vừa chốt "thêm" chưa có trong task -> thêm vào task, không đụng label cũ
  const added = scope.textContent.match(/^'([^']+)'/)[1];
  assert.match(body(), new RegExp(`Task còn thiếu: ${added} \\(rectangle`));
  await until(() => button('Thêm 1 label') && !button('Thêm 1 label').disabled, 'nút thêm label');
  button('Thêm 1 label').click();
  const patchTask = await until(() => store.calls.find((c) => c.method === 'PATCH' && c.path === '/api/tasks/9'), 'PATCH task: ' + status());
  assert.deepStrictEqual(patchTask.body.labels.map((l) => [l.name, l.type]), [[added, 'rectangle']]);
  assert.ok(/^#[0-9a-f]{6}$/i.test(patchTask.body.labels[0].color) && !('id' in patchTask.body.labels[0]));
  await until(() => /Task đã có đủ label/.test(body()), 'đồng bộ sau khi thêm label: ' + status());
  button('Label').click();
  assert.strictEqual(root.querySelectorAll('tbody tr').length, 4, 'panel nhận label mới');
  button('Guideline').click();
  await until(() => button('Chép JSON label') && !button('Chép JSON label').disabled, 'panel rảnh');
  button('Chép JSON label').click();
  await until(() => page.clipboard.length === 1, 'chép JSON label: ' + status());
  assert.deepStrictEqual(JSON.parse(page.clipboard[0]).map((l) => l.name).sort(), ['car', 'lane/crosswalk', 'truck', added].sort());

  // Bằng chứng: bấm số frame -> nhảy tới frame và khoanh vùng trên canvas
  const frameBtn = [...root.querySelectorAll('button')].find((b) => /^frame \d+$/.test(b.textContent.trim()));
  const target = Number(frameBtn.textContent.trim().split(' ')[1]);
  frameBtn.click();
  await until(() => new RegExp(`Frame ${target}(?!\\d)`).test(root.querySelector('.meta').textContent), 'tới frame bằng chứng');
  assert.ok(win.document.querySelector('svg rect'), 'khoanh vùng ví dụ trên ảnh');

  button('Mở bản nháp').click();
  assert.match(opened[0], new RegExp(`^${BACKEND}/guideline/run/[\\w-]+\\?key=smoke-key&lang=vi$`));
  const pageHtml = await (await fetch(opened[0])).text();
  assert.match(pageHtml, /Duyệt và lưu/);

  await until(() => !button('Dùng làm profile').disabled, 'panel rảnh');
  button('Dùng làm profile').click();
  await until(() => /đang dùng profile sinh từ bản nháp/.test(status()), 'dùng profile: ' + status());
  assert.match(root.querySelector('.meta select').value, /^auto_/);
  const profileId = root.querySelector('.meta select').value;
  assert.ok(fs.existsSync(path.join(PROFILES_DIR, `${profileId}.yaml`)), 'profile ghi vào thư mục profile của backend');
  assert.ok(!fs.existsSync(path.join(ROOT, 'profiles', `${profileId}.yaml`)), 'test không ghi vào thư mục profiles/ thật');

  const box = [...root.querySelectorAll('textarea')].pop();
  box.value = 'Reviewer trả bài: xe ba bánh chở hàng phải gán truck.';
  button('Ghi nhận').click();
  await until(() => /Đã ghi nhận/.test(status()), 'ghi nhận sai sót: ' + status());
  page.dom.window.close();
  return page.errors;
}

// Tự học từ thao tác của người dùng, Tìm tương tự + Loại trừ, Rà cả job, theo dõi bằng SAM3 Video.
async function learnScenario(frameBytes) {
  const labels = [{ id: 501, name: 'car', type: 'rectangle', color: '#33aa55', attributes: [], parent_id: null, has_parent: false },
    { id: 502, name: 'truck', type: 'rectangle', color: '#3355aa', attributes: [], parent_id: null, has_parent: false }];
  const page = openPage({ jobId: 70, frame: 0, stop: 3, mode: 'annotation', labels }, frameBytes);
  const { root, store, button, status, win } = page;
  const body = () => root.querySelector('.body').textContent;
  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel sẵn sàng: ' + status());
  assert.ok([...root.querySelectorAll('nav button')].some((b) => b.textContent === 'Tự học'));

  // 1. Gợi ý, bỏ tick truck (người dùng từ chối), đổi class? không; ghi car
  button('Gợi ý nhãn cho frame này').click();
  await until(() => root.querySelectorAll('li.item').length >= 2, 'đề xuất: ' + status());
  const truckItem = [...root.querySelectorAll('li.item')].find((li) => li.querySelector('select').value === '502');
  const box = truckItem.querySelector('input[type="checkbox"]');
  box.checked = false;
  box.dispatchEvent(new win.Event('change'));
  button('Chỉ ghi').click();
  const created = await until(() => store.calls.find((c) => c.method === 'PATCH' && c.path.includes('create')), 'ghi: ' + status());
  assert.ok(created.body.shapes.every((s) => s.label_id === 501));
  await until(() => /Đã ghi/.test(status()), 'đã ghi');
  // 2. Người dùng sửa trong CVAT: kéo cạnh phải hộp car ra 30px
  const car = store.shapes.find((s) => s.label_id === 501);
  car.points = [car.points[0], car.points[1], car.points[2] + 30, car.points[3]];
  // 3. Tab Tự học: đối chiếu
  button('Tự học').click();
  await until(() => /Dùng điều tool đã học/.test(body()), 'tab tự học');
  button('Đối chiếu job này ngay').click();
  await until(() => /Đã đối chiếu 2 đề xuất/.test(status()), 'đối chiếu: ' + status());
  await until(() => /sửa hình 1/.test(body()) && /bỏ tick 1/.test(body()), 'bảng tự học: ' + body());
  assert.match(body(), /Cần thêm dữ liệu/);

  // 4. Tìm tương tự + Loại trừ (engine giả lập trả vật trong hộp và hai vật bên cạnh)
  button('Gợi ý').click();
  button('Tìm tương tự').click();
  await until(() => /Kéo chuột/.test(status()), 'chờ vẽ hộp');
  const svg = win.document.querySelector('svg');
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1280, height: 720, right: 1280, bottom: 720 });
  const host = svg.parentElement;
  const mouse = (type, x, y) => host.dispatchEvent(new win.MouseEvent(type, { clientX: x, clientY: y, button: 0, bubbles: true }));
  mouse('mousedown', 500, 300); mouse('mousemove', 600, 380); mouse('mouseup', 600, 380);
  await until(() => /Thêm 3 đề xuất/.test(status()), 'tìm tương tự: ' + status());
  const excl = () => [...root.querySelectorAll('button')].filter((b) => b.textContent === 'Loại trừ');
  assert.strictEqual(excl().length, 3);
  excl()[2].click();
  await until(() => /Thêm 2 đề xuất.*loại trừ 1 mẫu/.test(status()), 'loại trừ: ' + status());
  assert.strictEqual(root.querySelectorAll('li.item').length, 2, 'lượt tìm lại thay kết quả cũ');
  button('Bỏ đề xuất').click();

  // 5. Rà cả job: frame 1-3 chưa có nhãn; hộp car vượt biên -> lỗi; tạo Issue
  store.shapes.push({ id: 9001, type: 'rectangle', label_id: 501, frame: 2, points: [-40, 10, 60, 60], attributes: [], occluded: false });
  button('Kiểm tra').click();
  button('Rà cả job').click();
  await until(() => /Rà 2 frame có nhãn/.test(status()), 'rà cả job: ' + status());
  assert.match(body(), /frame chưa có nhãn nào: 1, 3/);
  assert.match(body(), /Frame 2/);
  button('Tạo Issue cho mọi lỗi').click();
  await until(() => /Đã tạo \d+ Issue/.test(status()), 'tạo issue: ' + status());
  assert.ok(store.issues.length >= 1 && store.issues.every((i) => i.job === 70 && i.frame === 2 && /^\[/.test(i.message)));
  page.dom.window.close();
  return page.errors;
}

async function videoScenario(frameBytes) {
  const labels = [{ id: 93, name: 'person', type: 'rectangle', color: '#c06060', attributes: [], parent_id: null, has_parent: false }];
  const page = openPage({ jobId: 31, frame: 0, stop: 7, mode: 'interpolation', labels }, frameBytes);
  const { root, button, status } = page;
  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel video: ' + status());
  button('Cả job').click();
  const engine = [...root.querySelectorAll('label.field')].find((l) => /Cách theo dõi/.test(l.textContent)).querySelector('select');
  engine.value = 'sam3_video';
  engine.dispatchEvent(new page.win.Event('change'));
  button('Theo dõi 8 frame').click();
  await until(() => /theo dõi bằng SAM3 Video/.test(status()), 'theo dõi SAM3 Video: ' + status());
  assert.match(root.querySelector('ul').textContent, /#1 person · frame 0–7/);
  page.dom.window.close();
  return page.errors;
}

// Gợi ý cả job chỉ cho label mới: job đã gán xong 'car', guideline thêm 'truck'.
async function newLabelBatchScenario(frameBytes) {
  const labels = [{ id: 401, name: 'car', type: 'rectangle', color: '#33aa55', attributes: [], parent_id: null, has_parent: false },
    { id: 402, name: 'truck', type: 'rectangle', color: '#3355aa', attributes: [], parent_id: null, has_parent: false }];
  const page = openPage({ jobId: 60, frame: 0, stop: 5, mode: 'annotation', labels, meta: { deleted_frames: [3] } }, frameBytes);
  const { root, store, button, status } = page;
  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel sẵn sàng: ' + status());
  // car đã gán ở mọi frame; truck đã gán ở frame 2
  for (let f = 0; f <= 5; f++) store.shapes.push({ id: 900 + f, type: 'rectangle', label_id: 401, frame: f, points: [10, 10, 60, 60], attributes: [], occluded: false, z_order: 0, rotation: 0 });
  store.shapes.push({ id: 950, type: 'rectangle', label_id: 402, frame: 2, points: [100, 100, 300, 260], attributes: [], occluded: false, z_order: 0, rotation: 0 });
  button('Cả job').click();
  const body = () => root.querySelector('.body').textContent;
  assert.match(body(), /Label cần gợi ý \(2\/2\)/);
  button('Chỉ label chưa có trong job').click();
  await until(() => /Label nào trong job cũng đã có nhãn/.test(status()), 'mọi label đã có nhãn: ' + status());
  button('Bỏ chọn hết').click();
  const truck = [...root.querySelectorAll('label.check')].find((l) => l.textContent === 'truck').querySelector('input');
  truck.checked = true;
  truck.dispatchEvent(new page.win.Event('change'));
  assert.match(body(), /Label cần gợi ý \(1\/2\)/);
  assert.match(button('Gợi ý cho').textContent, /5 frame · 1 label/, 'job 6 frame, frame 3 đã xoá trong CVAT');
  assert.strictEqual(root.querySelector('label.field select').value, 'label', 'mặc định: chỉ bỏ qua label frame đã có');
  button('Gợi ý cho').click();
  await until(() => /4 frame có 4 đề xuất, bỏ qua 1 frame đã có đủ các label đã chọn/.test(status()), 'gợi ý label mới: ' + status());
  button('Ghi ').click();
  const patch = await until(() => store.calls.find((c) => c.method === 'PATCH' && c.path.includes('create')), 'ghi: ' + status());
  assert.deepStrictEqual(patch.body.shapes.map((s) => [s.frame, s.label_id]), [[0, 402], [1, 402], [4, 402], [5, 402]],
    'chỉ đề xuất truck, không đè thêm car, bỏ frame 2 đã có truck và frame 3 đã xoá');
  assert.ok(!store.calls.some((c) => c.path.includes('/data?') && /number=3\b/.test(c.path)), 'không tải ảnh của frame đã xoá');

  // Tab Label: quản lý profile. Ẩn profile tracking, hiện lại, rồi xoá một profile tự viết.
  fs.writeFileSync(path.join(PROFILES_DIR, 'demo_xoa.yaml'), 'id: demo_xoa\ntitle: Profile thử để xoá\nmode: instance\nlabels: []\n');
  button('Label').click();
  button('Quản lý profile').click();
  await until(() => /Profile đang dùng/.test(body()), 'danh sách profile: ' + status());
  const rowOf = (title) => [...root.querySelectorAll('table.profiles tr')].find((tr) => tr.textContent.includes(title));
  assert.ok(rowOf('Profile thử để xoá'), 'profile mới thả vào thư mục có trong danh sách');
  assert.match(rowOf('generic_track').textContent, /luôn có/);
  const hiddenCount = () => Number(body().match(/Profile đang ẩn \((\d+)\)/)[1]);
  const before = hiddenCount();
  const hideBtn = [...rowOf('Profile thử để xoá').querySelectorAll('button')].find((b) => b.textContent === 'Ẩn');
  hideBtn.click();
  await until(() => /Đã ẩn profile/.test(status()), 'ẩn: ' + status());
  assert.ok(fs.existsSync(path.join(PROFILES_DIR, 'archive', 'demo_xoa.yaml')));
  assert.ok(![...root.querySelectorAll('.meta option')].some((o) => o.value === 'demo_xoa'), 'profile ẩn không còn trong ô chọn');
  assert.strictEqual(hiddenCount(), before + 1);
  [...rowOf('Profile thử để xoá').querySelectorAll('button')].find((b) => b.textContent === 'Hiện lại').click();
  await until(() => /Đã hiện lại/.test(status()), 'hiện lại: ' + status());
  assert.ok([...root.querySelectorAll('.meta option')].some((o) => o.value === 'demo_xoa'));
  [...rowOf('Profile thử để xoá').querySelectorAll('button')].find((b) => b.textContent === 'Xoá').click();
  await until(() => /Đã xoá profile/.test(status()), 'xoá: ' + status());
  assert.ok(!fs.existsSync(path.join(PROFILES_DIR, 'demo_xoa.yaml')) && !fs.existsSync(path.join(PROFILES_DIR, 'archive', 'demo_xoa.yaml')));
  page.dom.window.close();
  return page.errors;
}

// Nạp guideline có sẵn cho một task khác, trên một CVAT chạy ở máy (localhost:8080).
const NEW_GUIDELINE = `# Annotation Guideline: Hàng hoá trong kho

| Nhóm nhãn | CVAT shape | Class áp dụng |
|---|---|---|
| Hàng | Bounding Box | box, pallet, xe nâng |
| Vùng | Polygon | area/walkway |

| Nhóm | Sub-category | Khái niệm |
|---|---|---|
| Hàng | box | Thùng carton, kể cả thùng bị che một phần. |
| Vùng | walkway | Lối đi được sơn vạch cho người đi bộ. |

| Issue type | Ví dụ |
|---|---|
| UNCERTAIN_CLASS | box vs pallet |
| LOW_VISIBILITY | quá tối |

| ✓ | Checklist |
|---|---|
| ☐ | Mỗi thùng một box riêng. |
| ☐ | Đã check 02 lần. |
`;

async function importScenario(frameBytes) {
  // Trang không phải CVAT trên localhost (script được phép chạy ở mọi cổng localhost): không hiện panel.
  const other = openPage({ jobId: 50, frame: 0, stop: 3, mode: 'annotation', labels: [], origin: 'http://localhost:3000',
    html: '<!doctype html><body><div id="app">Ứng dụng khác</div></body>' }, frameBytes);
  await sleep(1500);
  assert.strictEqual(other.win.document.getElementById('sam3-cvat-assist').style.display, 'none', 'trang không phải CVAT: không hiện panel');
  other.dom.window.close();

  const labels = [{ id: 300, name: 'box', type: 'rectangle', color: '#33aa55', attributes: [], parent_id: null, has_parent: false }];
  const page = openPage({ jobId: 51, frame: 0, stop: 3, mode: 'annotation', labels, origin: 'http://localhost:8080' }, frameBytes);
  const { root, store, button, status, win } = page;
  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel trên CVAT local sẵn sàng: ' + status());
  assert.ok(store.calls.length && store.calls.every((c) => c.path.startsWith('/api/')), 'gọi API của chính CVAT local');
  button('Guideline').click();
  const headings = [...root.querySelectorAll('.sec-h > span, h2')].map((x) => x.textContent);
  assert.match(headings[0], /Nạp guideline có sẵn/);
  const input = root.querySelector('input[type="file"]');
  Object.defineProperty(input, 'files', { value: [new win.File([NEW_GUIDELINE], 'Kho_Guideline_V1.md', { type: 'text/markdown' })] });
  input.dispatchEvent(new win.Event('change'));
  await until(() => /Xem trước: Annotation Guideline: Hàng hoá trong kho/.test(root.querySelector('.body').textContent), 'xem trước: ' + status());
  const rows = () => [...root.querySelectorAll('table.labels tbody tr')];
  assert.deepStrictEqual(rows().map((r) => r.querySelector('input').value), ['box', 'pallet', 'xe nâng', 'area/walkway']);
  assert.match(root.querySelector('.body').textContent, /chưa có prompt tiếng Anh cho SAM3 \(xe nâng\)/);
  assert.match(root.querySelector('.body').textContent, /2 Issue type · 2 mục checklist/);
  // Điền prompt tiếng Anh cho "xe nâng"
  const prompt = rows()[2].querySelectorAll('input')[1];
  prompt.value = 'forklift';
  prompt.dispatchEvent(new win.Event('change'));
  button('Lưu vào tool').click();
  await until(() => /Đã nạp 4 label/.test(status()), 'lưu guideline: ' + status());
  await until(() => /Guideline đã nạp/.test(root.querySelector('.body').textContent), 'danh sách guideline đã nạp');
  assert.ok([...root.querySelectorAll('.meta option')].some((o) => o.value === 'gl_kho_guideline_v1'), 'profile mới có trong ô chọn profile');
  button('Dùng cho job này').click();
  await until(() => /Đang dùng profile của/.test(status()), 'dùng profile: ' + status());
  assert.match(status(), /khớp 1\/1 label/);
  button('Thêm label còn thiếu').click();
  const patch = await until(() => store.calls.find((c) => c.method === 'PATCH' && c.path === '/api/tasks/9'), 'thêm label: ' + status());
  assert.deepStrictEqual(patch.body.labels.map((l) => [l.name, l.type]), [['pallet', 'rectangle'], ['xe nâng', 'rectangle'], ['area/walkway', 'polygon']]);
  await until(() => /Đã thêm 3 label/.test(status()), 'đã thêm label: ' + status());
  // Tab Kiểm tra dùng checklist của guideline mới
  button('Kiểm tra').click();
  button('Kiểm tra annotation').click();
  await until(() => /Mỗi thùng một box riêng/.test(root.querySelector('.body').textContent), 'checklist của guideline mới: ' + status());
  // Gỡ guideline
  button('Guideline').click();
  button('Gỡ').click();
  await until(() => /Đã gỡ/.test(status()), 'gỡ guideline: ' + status());
  assert.ok(![...root.querySelectorAll('.meta option')].some((o) => o.value === 'gl_kho_guideline_v1'));

  // Guideline không có bảng taxonomy + file taxonomy.json riêng, chọn cùng lúc: label lấy từ JSON, văn bản và checklist từ guideline
  const noTable = '# Guideline: Kho hàng V2\n\nVẽ box sát thùng hàng.\n\n| ✓ | Checklist |\n|---|---|\n| ☐ | Mỗi thùng một box. |\n';
  const taxonomy = JSON.stringify([{ name: 'box', type: 'rectangle', attributes: [] }, { name: 'pallet', type: 'rectangle', attributes: [] },
    { name: 'walkway', type: 'polygon', attributes: [] }]);
  const pick = (files) => {
    const input2 = root.querySelector('input[type="file"]');
    Object.defineProperty(input2, 'files', { value: files, configurable: true });
    input2.dispatchEvent(new win.Event('change'));
  };
  pick([new win.File([taxonomy], 'taxonomy.json', { type: 'application/json' }), new win.File([noTable], 'Kho_V2.md', { type: 'text/markdown' })]);
  await until(() => /Đã ghép 3 label mới/.test(status()), 'ghép taxonomy.json vào guideline: ' + status());
  assert.match(root.querySelector('.body').textContent, /Xem trước: Guideline: Kho hàng V2/);
  assert.deepStrictEqual(rows().map((r) => [r.querySelector('input').value, r.querySelector('select').value]),
    [['box', 'rectangle'], ['pallet', 'rectangle'], ['walkway', 'polygon']]);
  assert.match(root.querySelector('.body').textContent, /1 mục checklist/);
  assert.doesNotMatch(root.querySelector('.body').textContent, /Không tìm thấy bảng taxonomy/);
  button('Huỷ').click();
  // Chỉ có guideline: lấy label từ task đang mở
  pick([new win.File([noTable], 'Kho_V2.md', { type: 'text/markdown' })]);
  await until(() => /Không tìm thấy bảng taxonomy/.test(root.querySelector('.body').textContent), 'guideline không có bảng: ' + status());
  button('Lấy label từ task đang mở').click();
  assert.deepStrictEqual(rows().map((r) => r.querySelector('input').value), ['box', 'pallet', 'xe nâng', 'area/walkway']);
  assert.strictEqual(rows()[3].querySelector('select').value, 'polygon');
  button('Huỷ').click();
  page.dom.window.close();
  return page.errors;
}

// Thư mục profile tạm cho backend của test: profile sinh ra trong test không rơi vào profiles/ thật.
const PROFILES_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'sam3-assist-profiles-'));
fs.cpSync(path.join(ROOT, 'profiles'), PROFILES_DIR, { recursive: true });

async function main() {
  const jpg = path.join(os.tmpdir(), `sam3-assist-smoke2-${process.pid}.jpg`);
  execFileSync('python3', ['-c', `import cv2, numpy; cv2.imwrite(r"${jpg}", numpy.full((720, 1280, 3), 90, numpy.uint8))`]);
  const frameBytes = fs.readFileSync(jpg);
  fs.unlinkSync(jpg);
  // Thư viện guideline tạm: không ghi bài học của test vào thư mục thật.
  const libDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sam3-assist-lib-'));
  fs.cpSync(path.join(ROOT, 'guideline_library'), libDir, { recursive: true, filter: (src) => !/runs$|lessons\.jsonl$/.test(src) });
  const server = spawn('python3', ['-m', 'backend'], {
    cwd: ROOT, stdio: 'ignore',
    env: { ...process.env, SAM3_ENGINE: 'mock', SAM3_PORT: String(PORT), SAM3_API_KEY: 'smoke-key', GUIDELINE_LLM: 'none', SAM3_LIBRARY_DIR: libDir,
      SAM3_PROFILES_DIR: PROFILES_DIR },
  });
  try {
    for (let i = 0; i < 100; i++) {
      try { if ((await fetch(BACKEND + '/health')).ok) break; } catch (e) { await sleep(200); }
    }
    const errors = [...(await skeletonScenario(frameBytes)), ...(await trackingScenario(frameBytes)), ...(await guidelineScenario(frameBytes)),
      ...(await importScenario(frameBytes)), ...(await newLabelBatchScenario(frameBytes)),
      ...(await learnScenario(frameBytes)), ...(await videoScenario(frameBytes))];
    const real = errors.filter((e) => !/Not implemented: navigation/.test(e));
    assert.deepStrictEqual(real, [], 'lỗi console: ' + real.join(' | '));
    console.log('SMOKE TEST V2: PASS');
  } finally {
    server.kill();
  }
}

main().catch((err) => { console.error('SMOKE TEST V2: FAIL\n', err); process.exit(1); });
