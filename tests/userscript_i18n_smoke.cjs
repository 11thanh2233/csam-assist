/*
 * 0.8.1: panel ở chế độ English không còn chữ tiếng Việt nào (trừ tên ngôn ngữ "Tiếng Việt"), mọi tab,
 * thông báo trạng thái, hộp thoại xác nhận; backend nhận header X-Lang và trả thông báo tiếng Anh.
 *
 *   npm install jsdom
 *   node tests/userscript_i18n_smoke.cjs
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');
const { spawn, execFileSync } = require('child_process');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8796;
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
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sam3-i18n-'));
  const jpg = path.join(tmp, 'f.jpg');
  execFileSync('python3', ['-c', `import cv2, numpy; cv2.imwrite(r"${jpg}", numpy.full((720, 1280, 3), 90, numpy.uint8))`]);
  const frameBytes = fs.readFileSync(jpg);
  const backend = spawn('python3', ['-m', 'backend'], { cwd: ROOT, stdio: 'ignore',
    env: { ...process.env, SAM3_ENGINE: 'mock', SAM3_PORT: String(PORT), SAM3_API_KEY: 'k3', SAM3_LIBRARY_DIR: path.join(tmp, 'lib') } });
  try {
    for (let i = 0; i < 150; i++) { try { if ((await fetch(`${BACKEND}/health`)).ok) break; } catch (e) { await sleep(200); } }
    await scenario(frameBytes);
    console.log('I18N SMOKE (0.8.1): PASS');
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
  win.GM_setValue('cfg', { backendUrl: BACKEND, apiKey: 'k3', reloadAfterApply: false, confirmApply: true, lang: 'en', tourDone: true });
  const dialogs = [];
  win.confirm = (m) => { dialogs.push(m); return true; };
  win.eval(fs.readFileSync(path.join(ROOT, 'userscript', 'sam3-cvat-assist.user.js'), 'utf8'));
  const root = doc.getElementById('sam3-cvat-assist').shadowRoot;
  const button = (t) => [...root.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(t));
  const status = () => root.querySelector('.status').textContent;
  const alt = (key, extra) => doc.dispatchEvent(new win.KeyboardEvent('keydown', Object.assign({ key, altKey: true, bubbles: true }, extra || {})));
  const proposeCalls = () => calls.filter((c) => c.path === '/propose');
  const frameInput = doc.querySelector('.cvat-player-frame-selector input');

  const VI = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i;
  const ALLOWED = ['Tiếng Việt', 'Ngôn ngữ'];
  const leftovers = () => {
    const texts = [];
    const walker = doc.createTreeWalker(root, win.NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) texts.push(walker.currentNode.nodeValue);
    root.querySelectorAll('[title],[placeholder],[aria-label]').forEach((el) => ['title', 'placeholder', 'aria-label'].forEach((a) => el.getAttribute(a) && texts.push(el.getAttribute(a))));
    return texts.map((t) => ALLOWED.reduce((s, w) => s.split(w).join(''), t)).filter((t) => VI.test(t));
  };

  await until(() => button('Suggest labels') && !button('Suggest labels').disabled, 'panel ready (English): ' + status());
  assert.deepStrictEqual(leftovers(), [], 'panel đầu tiên không còn tiếng Việt');
  assert.ok(calls.length > 0);
  button('Suggest labels').click();
  await until(() => root.querySelectorAll('li.item').length > 0, 'có đề xuất: ' + status());
  assert.match(status(), /suggestions/, 'trạng thái bằng tiếng Anh: ' + status());
  assert.deepStrictEqual(leftovers(), [], 'tab Suggest có đề xuất không còn tiếng Việt');

  // Mọi tab
  const tabs = [...root.querySelectorAll('nav button')];
  assert.ok(tabs.length >= 6, 'có đủ các tab');
  for (let i = 0; i < tabs.length; i++) {
    [...root.querySelectorAll('nav button')][i].click();
    await sleep(300);
    const left = leftovers();
    assert.deepStrictEqual(left, [], `tab ${i} (${[...root.querySelectorAll('nav button')][i].textContent}) còn tiếng Việt`);
  }

  // Hộp thoại xác nhận và lỗi từ backend
  [...root.querySelectorAll('nav button')][0].click();
  await sleep(200);
  const save = [...root.querySelectorAll('button')].find((b) => /^Save \d+ labels$/.test(b.textContent.trim()));
  assert.ok(save, 'nút Save N labels');
  save.click();
  await until(() => dialogs.length > 0, 'hỏi lại trước khi ghi');
  assert.ok(!VI.test(dialogs[0]) && /^Save \d+ labels to frame/.test(dialogs[0]), 'hộp thoại bằng tiếng Anh: ' + dialogs[0]);
  await until(() => /Saved \d+ labels/.test(status()), 'đã ghi: ' + status());

  // backend trả lỗi bằng tiếng Anh, kèm mã cố định
  const r = await fetch(`${BACKEND}/guideline/lessons/xyz/status`, { method: 'POST', headers: { 'X-API-Key': 'k3', 'X-Lang': 'en', 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'confirmed' }) });
  const err = await r.json();
  assert.strictEqual(r.status, 404);
  assert.ok(err.code && err.code.startsWith('E_'), 'có mã lỗi');
  assert.ok(!VI.test(err.detail), 'thông báo lỗi tiếng Anh: ' + err.detail);
  const rv = await (await fetch(`${BACKEND}/guideline/lessons/xyz/status`, { method: 'POST', headers: { 'X-API-Key': 'k3', 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'confirmed' }) })).json();
  assert.ok(VI.test(rv.detail) && rv.code === err.code, 'tiếng Việt khi không đổi ngôn ngữ, cùng mã lỗi');
  assert.deepStrictEqual(errors, [], 'không có lỗi JavaScript');
  dom.window.close();
}

main().then(() => process.exit(0)).catch((e) => { console.error('I18N SMOKE: FAIL\n', e); process.exit(1); });
