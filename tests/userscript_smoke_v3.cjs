/*
 * 0.8.0: chạy thử các tính năng thao tác nhanh của panel với CVAT giả + backend mock (có API key).
 *   hướng dẫn lần đầu, gợi ý sẵn frame tiếp, nhắc vật sót, thanh ngưỡng + ẩn class, phím tắt (Alt+J/X/Enter/Z),
 *   Alt+bấm trên ảnh, ghi rồi sang frame tiếp (dùng kết quả chạy sẵn, không gọi lại model), kiểm trước khi ghi,
 *   tự điền thuộc tính, hoàn tác nhiều bước, bấm điểm để tách vật, thống kê + xuất CSV, tiếng Anh, đổi phím tắt,
 *   chế độ reviewer.
 *
 *   npm install jsdom
 *   node tests/userscript_smoke_v3.cjs
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');
const { spawn, execFileSync } = require('child_process');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8793;
const BACKEND = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { try { const v = await fn(); if (v) return v; } catch (e) { /* thử lại */ } await sleep(80); }
  throw new Error('Hết thời gian chờ: ' + what);
}

const LABELS = [
  { id: 1, name: 'car', type: 'rectangle', color: '#ff0000', attributes: [
    { id: 11, name: 'truncated', input_type: 'checkbox', mutable: false, default_value: 'false', values: ['false'] },
    { id: 12, name: 'occluded', input_type: 'checkbox', mutable: false, default_value: 'false', values: ['false'] }] },
  { id: 2, name: 'pedestrian', type: 'rectangle', color: '#00ff00', attributes: [] },
  { id: 3, name: 'traffic light', type: 'rectangle', color: '#0000ff', attributes: [] },
];

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sam3-smoke3-'));
  const jpg = path.join(tmp, 'f.jpg');
  execFileSync('python3', ['-c', `import cv2, numpy; cv2.imwrite(r"${jpg}", numpy.full((720, 1280, 3), 90, numpy.uint8))`]);
  const frameBytes = fs.readFileSync(jpg);
  const backend = spawn('python3', ['-m', 'backend'], { cwd: ROOT, stdio: 'ignore',
    env: { ...process.env, SAM3_ENGINE: 'mock', SAM3_PORT: String(PORT), SAM3_API_KEY: 'k3', SAM3_LIBRARY_DIR: path.join(tmp, 'lib') } });
  try {
    for (let i = 0; i < 150; i++) { try { if ((await fetch(`${BACKEND}/health`)).ok) break; } catch (e) { await sleep(200); } }
    await scenario(frameBytes);
    console.log('SMOKE TEST V3 (0.8.0): PASS');
  } finally { backend.kill(); }
}

async function scenario(frameBytes) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errors.push(String(e.message || e)));
  const dom = new JSDOM(`<!doctype html><body>
      <div class="cvat-canvas-container"><canvas id="cvat_canvas_background" width="1280" height="720"></canvas></div>
      <div class="cvat-player-frame-selector"><input value="3"></div></body>`,
  { url: 'https://cvat.edufun.ai/tasks/9/jobs/30?frame=3', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc });
  const win = dom.window, doc = win.document;
  doc.cookie = 'csrftoken=abc';
  // jsdom không tính bố cục: cho ảnh/overlay một khung 1280x720 tại (0,0) để Alt+bấm và bấm điểm tính được toạ độ.
  win.Element.prototype.getBoundingClientRect = function () { return { left: 0, top: 0, right: 1280, bottom: 720, width: 1280, height: 720, x: 0, y: 0 }; };
  const downloads = [];
  win.URL.createObjectURL = (blob) => { downloads.push({ blob }); return 'blob:x'; };
  win.URL.revokeObjectURL = () => {};
  win.HTMLAnchorElement.prototype.click = function () { downloads[downloads.length - 1].name = this.download; };
  const store = { shapes: [], nextId: 100 };
  // frame 2 có 3 xe, frame 3 chưa có gì: tool phải nhắc có thể sót
  for (let i = 0; i < 3; i++) store.shapes.push({ id: store.nextId++, frame: 2, label_id: 1, type: 'rectangle', points: [10 + i * 100, 10, 60 + i * 100, 60], attributes: [] });
  const json = (data, status = 200) => ({ ok: status < 300, status, json: async () => data, text: async () => JSON.stringify(data) });
  win.fetch = async (url, o = {}) => {
    const u = new URL(url); const method = o.method || 'GET'; const body = o.body ? JSON.parse(o.body) : undefined;
    if (u.pathname === '/api/users/self') return json({ id: 5, username: 'an.nguyen' });
    if (u.pathname === '/api/jobs/30') return json({ id: 30, task_id: 9, start_frame: 0, stop_frame: 6, mode: 'annotation' });
    if (u.pathname === '/api/labels') return json({ count: LABELS.length, next: null, results: LABELS });
    if (u.pathname === '/api/jobs/30/data/meta') return json({ frames: Array(7).fill({ width: 1280, height: 720 }), deleted_frames: [] });
    if (u.pathname === '/api/jobs/30/data') return { ok: true, status: 200, blob: async () => new win.Blob([frameBytes], { type: 'image/jpeg' }) };
    if (u.pathname === '/api/jobs/30/annotations' && method === 'GET') return json({ version: 1, tags: [], shapes: store.shapes, tracks: [] });
    if (u.pathname === '/api/jobs/31/annotations') return json({ version: 1, tags: [], tracks: [], shapes: [   // người thứ hai gán frame 2
      { id: 1, frame: 2, label_id: 1, type: 'rectangle', points: [12, 10, 60, 60] }, { id: 2, frame: 2, label_id: 2, type: 'rectangle', points: [110, 10, 160, 60] }] });
    if (u.pathname === '/api/jobs/30/annotations' && method === 'PATCH') {
      if (u.searchParams.get('action') === 'create') { const c = body.shapes.map((s) => ({ ...s, id: store.nextId++ })); store.shapes.push(...c); return json({ shapes: c, tracks: [] }); }
      if (u.searchParams.get('action') === 'delete') { const ids = new Set(body.shapes.map((s) => s.id)); store.shapes = store.shapes.filter((s) => !ids.has(s.id)); return json({}); }
    }
    return json({ detail: 'not found' }, 404);
  };
  win.createImageBitmap = async () => ({ width: 1280, height: 720, close() {} });
  win.confirm = () => true;
  // 0.9: panel hỏi bằng hộp thoại riêng (không còn window.confirm/prompt): test tự bấm nút chính
  const __dialogs = [];
  setInterval(() => {
    const hostEl = win.document.getElementById('sam3-cvat-assist');
    const back = hostEl && hostEl.shadowRoot && hostEl.shadowRoot.querySelector('.dlg-back');
    if (back && !win.__manualDialog) { __dialogs.push(back.querySelector('p').textContent); back.querySelector('.dlg .primary').click(); }
  }, 20).unref();
  win.prompt = (_q, def) => def;
  const gm = {};
  win.GM_getValue = (k, d) => (k in gm ? JSON.parse(gm[k]) : d);
  win.GM_setValue = (k, v) => { gm[k] = JSON.stringify(v); };
  win.GM_registerMenuCommand = () => {};
  win.GM_setClipboard = () => {};
  const calls = [];
  win.GM_xmlhttpRequest = (o) => {
    calls.push({ method: o.method, path: o.url.replace(BACKEND, ''), body: o.data ? JSON.parse(o.data) : null });
    fetch(o.url, { method: o.method, headers: o.headers, body: o.data })
      .then(async (r) => o.onload({ status: r.status, responseText: await r.text() })).catch(() => o.onerror());
  };
  win.GM_setValue('cfg', { backendUrl: BACKEND, apiKey: 'k3', reloadAfterApply: false, confirmApply: true });
  win.eval(fs.readFileSync(path.join(ROOT, 'userscript', 'sam3-cvat-assist.user.js'), 'utf8'));
  const root = doc.getElementById('sam3-cvat-assist').shadowRoot;
  const button = (t) => [...root.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(t));
  const status = () => root.querySelector('.status').textContent;
  const alt = (key, extra) => doc.dispatchEvent(new win.KeyboardEvent('keydown', Object.assign({ key, altKey: true, bubbles: true }, extra || {})));
  const proposeCalls = () => calls.filter((c) => c.path === '/propose');
  const frameInput = doc.querySelector('.cvat-player-frame-selector input');

  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel sẵn sàng: ' + status());
  assert.ok(calls.some((c) => c.path === '/resolve' && c.body.user === 'an.nguyen'), 'gửi tên người dùng CVAT để tự học theo người');

  // 15. Hướng dẫn lần đầu
  assert.ok(root.querySelector('.tour'), 'hiện hướng dẫn lần đầu');
  assert.ok(root.querySelector('.tour-target'), 'tô sáng nút đang được hướng dẫn');
  button('Tiếp').click();
  assert.match(root.querySelector('.tour').textContent, /2\/5/);
  button('Bỏ qua').click();
  assert.ok(!root.querySelector('.tour') && JSON.parse(gm.cfg).tourDone, 'bỏ qua thì không hiện lại');

  // 1 + 21. Gợi ý, nhắc vật sót, chạy sẵn frame tiếp theo
  button('Gợi ý nhãn').click();
  await until(() => root.querySelectorAll('li.item').length > 0, 'có đề xuất: ' + status());
  assert.match(root.textContent, /Frame 2 có 3 "car", frame này mới có \d+/, 'nhắc vật có thể sót theo frame bên cạnh');
  await until(() => gm.prefetch && JSON.parse(gm.prefetch).frames[4], 'đã chạy sẵn frame 4');
  assert.strictEqual(proposeCalls().length, 2, 'frame 3 + chạy sẵn frame 4');

  // 18. Tự điền thuộc tính truncated/occluded cho label có chúng
  assert.match(root.textContent, /Tự điền: (truncated|occluded) = (true|false)/);

  // 4. Thanh ngưỡng + ẩn class (tức thì, không gọi lại backend)
  const total = root.querySelectorAll('li.item').length;
  const slider = root.querySelector('.filter input[type=range]');
  slider.value = '0.95'; slider.dispatchEvent(new win.Event('input'));   // mock cho mọi đề xuất điểm 0.9
  assert.ok(root.querySelectorAll('li.item').length < total, 'ngưỡng cao ẩn bớt đề xuất');
  assert.match(root.textContent, /đang ẩn/);
  const slider2 = root.querySelector('.filter input[type=range]');
  slider2.value = '0'; slider2.dispatchEvent(new win.Event('input'));
  const chip = [...root.querySelectorAll('button.chip')][0];
  if (chip) {
    chip.click();
    assert.ok(root.querySelector('button.chip.off'), 'ẩn được cả class');
    root.querySelector('button.chip.off').click();
  }
  assert.strictEqual(root.querySelectorAll('li.item').length, total);
  assert.strictEqual(proposeCalls().length, 2, 'lọc không gọi lại model');

  // 3. Phím tắt: Alt+J chọn, Alt+X bỏ tick, Alt+X tick lại
  alt('j');
  const sel = root.querySelector('li.item.sel');
  assert.ok(sel, 'Alt+J chọn đề xuất');
  const cb = () => root.querySelector('li.item.sel input[type=checkbox]');
  const before = cb().checked;
  alt('x'); assert.strictEqual(cb().checked, !before, 'Alt+X đổi tick');
  alt('x'); assert.strictEqual(cb().checked, before);

  // 3. Giữ Alt + bấm lên ảnh: tick/bỏ tick đề xuất dưới con trỏ
  const items = root.querySelectorAll('li.item').length;
  const firstChecked = [...root.querySelectorAll('li.item input[type=checkbox]')].filter((x) => x.checked).length;
  const target = win.document.querySelector('canvas');
  // điểm giữa hộp của đề xuất đầu tiên trong danh sách (mock: hộp nằm trong ảnh)
  const ev = new win.MouseEvent('mousedown', { clientX: 0, clientY: 0, altKey: true, button: 0, bubbles: true });
  const pick = await until(() => calls.find((c) => c.path === '/propose'), 'có kết quả');
  void pick;
  // Lấy đề xuất từ trạng thái hiển thị: thử bấm lần lượt các điểm lưới cho tới khi số tick đổi
  let toggled = false;
  for (let y = 40; y < 720 && !toggled; y += 60) for (let x = 40; x < 1280 && !toggled; x += 60) {
    target.dispatchEvent(new win.MouseEvent('mousedown', { clientX: x, clientY: y, altKey: true, button: 0, bubbles: true }));
    const now = [...root.querySelectorAll('li.item input[type=checkbox]')].filter((c) => c.checked).length;
    if (now !== firstChecked) { toggled = true; target.dispatchEvent(new win.MouseEvent('mousedown', { clientX: x, clientY: y, altKey: true, button: 0, bubbles: true })); }
  }
  void ev;
  assert.ok(toggled, 'Alt+bấm lên ảnh đổi tick đề xuất');
  assert.strictEqual(root.querySelectorAll('li.item').length, items);

  // 2 + 17. Alt+Enter: kiểm trước khi ghi, ghi, sang frame 4, hiện ngay kết quả chạy sẵn (không gọi lại model)
  const createdBefore = store.shapes.length;
  alt('Enter');
  await until(() => calls.some((c) => c.path === '/qa'), 'kiểm QA trước khi ghi');
  await until(() => store.shapes.length > createdBefore, 'đã ghi nhãn');
  const written = store.shapes.filter((s) => s.frame === 3);
  assert.ok(written.some((s) => s.label_id === 1 ? s.attributes.length === 2 : true), 'nhãn car ghi kèm 2 thuộc tính');
  await until(() => frameInput.value === '4', 'đã sang frame 4');
  await until(() => /đã chạy sẵn/.test(status()), 'frame 4 hiện ngay đề xuất chạy sẵn: ' + status());
  assert.strictEqual(proposeCalls().filter((c) => c.body && c.body.existing !== undefined).length >= 2, true);
  await until(() => gm.prefetch && JSON.parse(gm.prefetch).frames[5], 'chạy sẵn tiếp frame 5');
  assert.strictEqual(proposeCalls().length, 3, 'chỉ 3 lượt model: frame 3, chạy sẵn frame 4, chạy sẵn frame 5 (frame 4 không chạy lại)');

  // 0.9: hộp thoại xác nhận của panel: Esc huỷ, bấm ra ngoài huỷ, Enter đồng ý; huỷ thì không ghi gì
  win.__manualDialog = true;
  const dlg = () => root.querySelector('.dlg-back');
  const frame4 = () => store.shapes.filter((s) => s.frame === 4).length;
  button('Ghi ').click();
  await until(() => dlg(), 'hiện hộp thoại xác nhận trong panel');
  assert.match(dlg().textContent, /Ghi \d+ nhãn vào frame 4/);
  assert.strictEqual(root.activeElement, dlg().querySelector('.primary'), 'tiêu điểm ở nút Đồng ý');
  dlg().querySelector('.dlg').dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.ok(!dlg(), 'Esc đóng hộp thoại');
  button('Ghi ').click();
  await until(() => dlg(), 'hỏi lại');
  dlg().dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true }));
  assert.ok(!dlg(), 'bấm ra ngoài thì đóng');
  await new Promise((r) => setTimeout(r, 200));
  assert.strictEqual(frame4(), 0, 'huỷ thì không ghi');
  button('Ghi ').click();
  await until(() => dlg(), 'hỏi lần ba');
  dlg().querySelector('.dlg').dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  win.__manualDialog = false;

  // 5. Hoàn tác nhiều bước: ghi thêm ở frame 4 (Enter ở trên là đồng ý), rồi lùi 2 lần
  await until(() => store.shapes.some((s) => s.frame === 4), 'ghi ở frame 4');
  await until(() => /Hoàn tác lần ghi gần nhất \(2\)/.test(root.textContent), 'có 2 lần ghi để hoàn tác');
  const n4 = store.shapes.length;
  alt('z');
  await until(() => store.shapes.length < n4, 'hoàn tác lần 1');
  await until(() => !button('Hoàn tác').disabled && /\(1\)/.test(button('Hoàn tác').textContent), 'còn 1 lần');
  button('Hoàn tác').click();
  await until(() => !store.shapes.some((s) => s.frame === 3 && s.id >= 103), 'hoàn tác lần 2 (frame 3)');

  // 19. Bấm điểm để tách vật
  await until(() => button('Bấm điểm') && !button('Bấm điểm').disabled, 'sẵn sàng bấm điểm');
  button('Bấm điểm').click();
  await until(() => /Bấm lên vật cần tách/.test(status()), 'vào chế độ bấm điểm');
  const overlayHost = [...doc.body.children].find((el) => el.querySelector && el.querySelector('svg'));
  overlayHost.dispatchEvent(new win.MouseEvent('mousedown', { clientX: 400, clientY: 300, button: 0, bubbles: true }));
  await until(() => calls.some((c) => c.path === '/interactive' && c.body.mode === 'points'), 'gọi tách vật theo điểm');
  await until(() => /Đã tách vật|Không tách được/.test(status()), 'kết quả bấm điểm: ' + status());
  overlayHost.dispatchEvent(new win.MouseEvent('mousedown', { clientX: 900, clientY: 600, button: 2, bubbles: true }));
  await until(() => calls.filter((c) => c.path === '/interactive').length >= 2 && calls.slice(-1)[0].body.negative_points.length === 1, 'điểm loại trừ');
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

  // 16 + 25. Thống kê + xuất CSV
  button('Tự học').click();
  await until(() => /Hôm nay/.test(root.textContent), 'thẻ thống kê');
  button('Xuất báo cáo CSV').click();
  assert.ok(downloads.length && /\.csv$/.test(downloads[0].name), 'tải file CSV');
  const csv = await downloads[0].blob.text();
  assert.match(csv, /"ngay","job"/);
  assert.match(csv, /"30","0",/, 'hoàn tác trừ lại số nhãn đã ghi của job');

  // 36. Đổi phím tắt: "Tick / bỏ tick" thành Alt+Y
  button('Cài đặt').click();
  const row = [...root.querySelectorAll('tr')].find((tr) => /Tick \/ bỏ tick/.test(tr.textContent));
  row.querySelector('input').dispatchEvent(new win.KeyboardEvent('keydown', { key: 'y', bubbles: true }));
  assert.strictEqual(JSON.parse(gm.cfg).keys.toggle, 'y');
  const row2 = [...root.querySelectorAll('tr')].find((tr) => /Ghi các đề xuất đã tick/.test(tr.textContent));
  row2.querySelector('input').dispatchEvent(new win.KeyboardEvent('keydown', { key: 'y', bubbles: true }));
  assert.match(status(), /đang dùng cho/, 'không cho trùng phím');

  // 24. Chế độ reviewer: viền "AI" cho nhãn do tool ghi mà chưa ai sửa
  const reviewer = [...root.querySelectorAll('label.check')].find((l) => /Chế độ reviewer/.test(l.textContent)).querySelector('input');
  reviewer.click();
  await until(() => calls.some((c) => c.path.startsWith('/learn/tool_shapes?job_id=30&user=an.nguyen')), 'đọc nhãn do tool ghi');

  // 29. Tiếng Anh
  const lang = [...root.querySelectorAll('select')].find((s) => [...s.options].some((o) => o.value === 'en'));
  lang.value = 'en'; lang.dispatchEvent(new win.Event('change'));
  assert.match(root.querySelector('nav').textContent, /Suggest.*Settings/);
  lang.value = 'vi'; lang.dispatchEvent(new win.Event('change'));

  // cỡ chữ + tương phản cao
  const font = [...root.querySelectorAll('input[type=range]')].find((r) => r.max === '1.6');
  font.value = '1.3'; font.dispatchEvent(new win.Event('change'));
  assert.match(doc.getElementById('sam3-cvat-assist').style.getPropertyValue('--font'), /^16\.9px/);

  // 11 + 12 + 10. Cả job: tạm dừng giữa chừng, tiếp tục, duyệt lưới ảnh, thông báo khi xong
  const notes = [];
  win.GM_notification = (o) => notes.push(o);
  button('Cả job').click();
  await until(() => button('Gợi ý cho 7 frame'), 'nút gợi ý cả job');
  button('Gợi ý cho 7 frame').click();
  await until(() => calls.filter((c) => c.path === '/propose').length >= 5, 'đang chạy cả job');
  button('Dừng').click();
  await until(() => /Đã tạm dừng/.test(status()), 'tạm dừng: ' + status());
  assert.ok(gm['batchrun:30'] && JSON.parse(gm['batchrun:30']).done.length < 7, 'nhớ tiến độ để chạy tiếp');
  await until(() => button('Tiếp tục'), 'nút Tiếp tục');
  const before2 = calls.filter((c) => c.path === '/propose').length;
  button('Tiếp tục').click();
  await until(() => !gm['batchrun:30'] || JSON.parse(gm['batchrun:30']) === null, 'chạy nốt xong');
  await until(() => notes.some((n) => /Gợi ý cả job xong/.test(n.title)), 'thông báo khi xong');
  const ran = calls.filter((c) => c.path === '/propose').length - before2;
  assert.ok(ran > 0 && ran < 7, `chỉ chạy các frame còn lại (${ran})`);
  await until(() => button('Duyệt dạng lưới ảnh'), 'nút lưới ảnh');
  button('Duyệt dạng lưới ảnh').click();
  await until(() => root.querySelectorAll('.tile').length > 0, 'lưới ảnh');
  const tileBox = root.querySelector('.tile input[type=checkbox]');
  if (tileBox.checked) { tileBox.click(); assert.ok(!root.querySelector('.tile input[type=checkbox]').checked, 'bỏ cả frame trong lưới'); }

  // 31 + 34 + 33. Dữ liệu thông minh: phân tích frame, keyframe, tìm frame bằng chữ
  button('Cả job').click();
  await until(() => button('Phân tích frame của job'), 'nút phân tích');
  button('Phân tích frame của job').click();
  await until(() => /Đã phân tích 7 frame/.test(status()), 'phân tích xong: ' + status());
  assert.ok(calls.some((c) => c.path === '/data/features') && calls.some((c) => c.path === '/data/rank'));
  assert.match(root.textContent, /Nên làm trước/);
  assert.match(root.textContent, /Keyframe theo chuyển động: \d+\/7 frame/);
  const searchBox = [...root.querySelectorAll('input[type=text]')].find((i) => /Tìm frame có/.test(i.placeholder));
  searchBox.value = 'car';
  button('Tìm').click();
  await until(() => /"car": thấy ở \d+ frame/.test(status()), 'tìm xong: ' + status());
  assert.ok(calls.filter((c) => c.path === '/data/search').length === 2, 'tìm theo lô 4 frame');
  await until(() => notes.some((n) => /Tìm frame xong/.test(n.title)), 'thông báo tìm xong');
  const propsBefore = calls.filter((c) => c.path === '/propose').length;
  button('Gợi ý cả job chỉ trên keyframe').click();
  await until(() => /frame có \d+ đề xuất/.test(status()), 'gợi ý trên keyframe xong: ' + status());
  assert.ok(calls.filter((c) => c.path === '/propose').length - propsBefore <= 7);

  // 35. Độ đồng thuận với job khác
  button('Kiểm tra').click();
  const jobInput = root.querySelector('input[placeholder="số job"]');
  jobInput.value = '31';
  button('So sánh').click();
  await until(() => /Đồng thuận chung \d+% trên 1 frame chung/.test(status()), 'so sánh xong: ' + status());
  assert.match(root.textContent, /Chỉ job 31/);
  assert.match(root.textContent, /Hay gán khác nhau|Frame lệch nhiều/);

  const real = errors.filter((e) => !/Not implemented: navigation/.test(e));   // hoàn tác tải lại trang: jsdom không làm được
  assert.deepStrictEqual(real, [], 'lỗi JS: ' + real.join('\n'));
  dom.window.close();
}

main().then(() => process.exit(0)).catch((e) => { console.error('SMOKE TEST V3: FAIL\n', e); process.exit(1); });
