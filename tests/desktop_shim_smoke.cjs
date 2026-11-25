/*
 * Bản desktop: chạy userscript trong trang CVAT giả qua shim GM_* + Bridge thật (như trong app), không có Tampermonkey.
 * Backend chạy ở chế độ mock và BẮT BUỘC có API key: đề xuất đi được nghĩa là Bridge đã thêm key, trang không cần biết key.
 *
 *   npm install jsdom
 *   node tests/desktop_shim_smoke.cjs
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');
const { spawn, execFileSync } = require('child_process');
const { JSDOM, VirtualConsole } = require('jsdom');

// Python của máy đang chạy test: Windows chỉ có 'python' (không có 'python3'), Linux/macOS có 'python3'.
// Đặt biến môi trường PYTHON để dùng trình thông dịch khác (venv, py -3.12...).
const PY = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');

const ROOT = path.resolve(__dirname, '..');
const BE_PORT = 8797, HELPER_PORT = 8796, KEY = 'desktop-smoke-key';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { try { const v = fn(); if (v) return v; } catch (e) { /* thử lại */ } await sleep(100); }
  throw new Error('Hết thời gian chờ: ' + what);
}
async function call(method, ...args) {
  const r = await fetch(`http://127.0.0.1:${HELPER_PORT}/`, { method: 'POST', body: JSON.stringify({ method, args }) });
  const out = await r.json();
  if (out && out.__error) throw new Error(out.__error);
  return out;
}
const LABELS = ['car', 'pedestrian', 'traffic light'].map((name, i) => ({ id: 100 + i, name, type: 'any', color: '#33aa55', attributes: [] }));

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sam3-desktop-smoke-'));
  const jpg = path.join(tmp, 'f.jpg');
  execFileSync(PY, ['-c', `import cv2, numpy; cv2.imwrite(r"${jpg}", numpy.full((720, 1280, 3), 90, numpy.uint8))`]);
  const frameBytes = fs.readFileSync(jpg);
  const bootFile = path.join(tmp, 'boot.js');
  const backend = spawn(PY, ['-m', 'backend'], { cwd: ROOT, stdio: 'ignore',
    env: { ...process.env, SAM3_ENGINE: 'mock', SAM3_PORT: String(BE_PORT), SAM3_API_KEY: KEY, SAM3_LIBRARY_DIR: path.join(tmp, 'lib') } });
  const helper = spawn(PY, [path.join(ROOT, 'tests', 'desktop_bridge_helper.py'), String(HELPER_PORT), String(BE_PORT), KEY, tmp, bootFile],
    { cwd: ROOT, stdio: 'inherit' });
  try {
    for (let i = 0; i < 150; i++) { try { if ((await fetch(`http://127.0.0.1:${BE_PORT}/health`)).ok) break; } catch (e) { await sleep(200); } }
    await until(() => fs.existsSync(bootFile), 'helper sẵn sàng');
    for (let i = 0; i < 50; i++) { try { await call('_store'); break; } catch (e) { await sleep(200); } }
    // Không có key thì backend từ chối: chứng minh key nằm ở Bridge
    assert.strictEqual((await fetch(`http://127.0.0.1:${BE_PORT}/profiles`)).status, 401);
    await scenario(fs.readFileSync(bootFile, 'utf8'), frameBytes);
    console.log('DESKTOP SHIM SMOKE: PASS');
  } finally {
    backend.kill(); helper.kill();
  }
}

async function scenario(boot, frameBytes) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errors.push(String(e.message || e)));
  const dom = new JSDOM(`<!doctype html><body>
       <div class="cvat-canvas-container"><canvas id="cvat_canvas_background" width="1280" height="720"></canvas></div>
       <div class="cvat-player-frame-selector"><input value="3"></div></body>`,
  { url: 'https://cvat.edufun.ai/tasks/5/jobs/12?frame=3', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc });
  const win = dom.window;
  win.document.cookie = 'csrftoken=abc123';
  const store = { shapes: [], nextId: 1 };
  const json = (data, status = 200) => ({ ok: status < 300, status, json: async () => data, text: async () => JSON.stringify(data) });
  win.fetch = async (url, opts = {}) => {
    const u = new URL(url);
    assert.strictEqual(u.origin, 'https://cvat.edufun.ai', 'trang chỉ gọi CVAT, backend đi qua Bridge');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    if (u.pathname === '/api/jobs/12') return json({ id: 12, task_id: 5, start_frame: 0, stop_frame: 9 });
    if (u.pathname === '/api/labels') return json({ count: LABELS.length, next: null, results: LABELS });
    if (u.pathname === '/api/jobs/12/data/meta') return json({ frames: Array(10).fill({ width: 1280, height: 720 }), deleted_frames: [] });
    if (u.pathname === '/api/jobs/12/data') return { ok: true, status: 200, blob: async () => new win.Blob([frameBytes], { type: 'image/jpeg' }) };
    if (u.pathname === '/api/jobs/12/annotations' && method === 'GET') return json({ version: 1, tags: [], shapes: store.shapes, tracks: [] });
    if (u.pathname === '/api/jobs/12/annotations' && method === 'PATCH') {
      const created = (body.shapes || []).map((s) => ({ ...s, id: store.nextId++ }));
      store.shapes.push(...created);
      return json({ version: 2, tags: [], shapes: created, tracks: [] });
    }
    return json({ detail: 'not found' }, 404);
  };
  win.createImageBitmap = async () => ({ width: 1280, height: 720, close() {} });
  win.confirm = () => true;
  win.prompt = (_q, def) => def;
  // window.pywebview.api như trong app: mỗi hàm trả Promise
  setTimeout(() => {
    win.pywebview = { api: { gm_request: (o) => call('gm_request', o), gm_set: (k, v) => call('gm_set', k, v), clipboard: (t) => call('clipboard', t) } };
    win.dispatchEvent(new win.Event('pywebviewready'));
  }, 300);                                   // api có sau khi trang chạy script: shim phải chờ được

  win.eval(boot);
  assert.ok(win.__sam3ShimReady && win.__sam3ScriptRan, 'shim và userscript đã chạy');
  win.eval(boot);                            // tiêm lần hai (trang tải lại sự kiện loaded): không chạy trùng
  assert.strictEqual(win.document.querySelectorAll('#sam3-cvat-assist').length, 1);

  const root = win.document.getElementById('sam3-cvat-assist').shadowRoot;
  const button = (text) => [...root.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(text));
  const statusText = () => (root.querySelector('.status') || {}).textContent;
  await until(() => button('Gợi ý nhãn') && !button('Gợi ý nhãn').disabled, 'panel sẵn sàng: ' + statusText());
  assert.match(root.textContent, /GIẢ LẬP/);
  button('Gợi ý nhãn').click();
  await until(() => root.querySelectorAll('li.item').length > 0, 'có đề xuất qua Bridge: ' + statusText());

  // GM_setValue đi xuống Store của app
  win.GM_setValue('desktop-test', { a: 1 });
  await until(async () => true, 'ghi', 300);
  await sleep(300);
  const saved = await call('_store');
  assert.deepStrictEqual(saved['desktop-test'], { a: 1 });
  assert.strictEqual(saved.cfg === undefined || saved.cfg.apiKey === '' || saved.cfg.apiKey === undefined, true, 'key không bị lưu trong trang');

  // Trang lạ (link ngoài trong cùng cửa sổ) không gọi được
  await call('_seturl', 'https://evil.example/');
  const denied = await call('gm_request', { method: 'GET', url: `http://127.0.0.1:${BE_PORT}/health` });
  assert.strictEqual(denied.error, 'denied');
  assert.deepStrictEqual(errors, [], 'không có lỗi JS: ' + errors.join('\n'));
}

main().then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });  // trang giả còn hẹn giờ: thoát hẳn
