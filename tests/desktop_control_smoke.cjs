/*
 * Bảng điều khiển của bản desktop (desktop/ui/control.html) chạy trong trình duyệt giả với ControlApi thật:
 * máy giả lập đã cài 0.6.0 với PyTorch cũ -> khung "Có gì mới", báo cần cập nhật PyTorch, có bản app mới.
 *   node tests/desktop_control_smoke.cjs
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');
const { spawn } = require('child_process');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8795;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { try { const v = fn(); if (v) return v; } catch (e) { /* thử lại */ } await sleep(100); }
  throw new Error('Hết thời gian chờ: ' + what);
}
const apiErrors = [];
async function call(method, args) {
  const r = await fetch(`http://127.0.0.1:${PORT}/`, { method: 'POST', body: JSON.stringify({ method, args }) });
  const out = await r.json();
  if (out && out.__error) { apiErrors.push(method + ': ' + out.__error); throw new Error(method + ': ' + out.__error); }
  return out;
}
process.on('unhandledRejection', (e) => { apiErrors.push(String(e && e.message || e)); });

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sam3-control-smoke-'));
  const helper = spawn('python3', [path.join(ROOT, 'tests', 'desktop_control_helper.py'), String(PORT), tmp], { cwd: ROOT, stdio: 'inherit' });
  try {
    for (let i = 0; i < 60; i++) { try { await call('status', []); break; } catch (e) { await sleep(200); } }
    const errors = [];
    const vc = new VirtualConsole();
    vc.on('jsdomError', (e) => errors.push(String(e.message || e)));
    const html = fs.readFileSync(path.join(ROOT, 'desktop', 'ui', 'control.html'), 'utf8');
    const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc, url: 'http://127.0.0.1/' });
    const win = dom.window, doc = win.document;
    win.alert = (m) => errors.push('alert: ' + m);
    win.confirm = () => true;
    win.pywebview = { api: new Proxy({}, { get: (_, name) => (...args) => call(name, args) }) };
    win.dispatchEvent(new win.Event('pywebviewready'));

    // Khung "Có gì mới" (đang lên từ 0.6.0) với mục 0.7.0 của CHANGELOG
    await until(() => !doc.getElementById('newsModal').classList.contains('hidden'), 'khung có gì mới');
    const version = fs.readFileSync(path.join(ROOT, 'backend', '__init__.py'), 'utf8').match(/__version__ = "([^"]+)"/)[1];
    assert.ok(doc.getElementById('newsTitle').textContent.includes(version), 'tiêu đề có bản hiện tại');
    assert.match(doc.getElementById('newsBody').textContent, /Không tải lại thứ đã có/);
    assert.ok(doc.querySelectorAll('#newsBody li').length >= 4, 'danh sách thay đổi được hiện thành mục');
    // 0.9: đóng bằng bấm ra ngoài khung (không phải kéo xuống nút Đã xem)
    doc.getElementById('newsModal').dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true }));
    assert.ok(doc.getElementById('newsModal').classList.contains('hidden'), 'bấm ngoài khung thì đóng');
    await sleep(300);
    assert.strictEqual((await call('whats_new', [])).show, false, 'đã xem thì không hiện lại');

    // Bảng điều khiển: PyTorch cần cập nhật (bản ghim 2.14.1, máy có 2.13.0), có bản app mới
    await until(() => !doc.getElementById('todoBanner').classList.contains('hidden'), 'báo cần cập nhật PyTorch');
    assert.match(doc.getElementById('todoBanner').textContent, /PyTorch 2\.14\.1, máy đang có 2\.13\.0/);
    assert.match(doc.getElementById('comps').textContent, /Có bản mới/);
    assert.strictEqual(doc.querySelectorAll('#comps .ccard').length, 4, 'mỗi thành phần một thẻ');
    await until(() => /Có bản mới 99\.0\.0/.test(doc.getElementById('updDetail').textContent), 'thông tin bản mới');
    assert.ok(doc.querySelector('#updDetail .notes b'), 'ghi chú bản mới hiện dạng định dạng');
    assert.ok(!doc.getElementById('updBadge').classList.contains('hidden'));
    assert.ok(!doc.getElementById('dot-settings').classList.contains('hidden'), 'chấm báo ở mục Cài đặt khi có bản mới');
    // 0.9: Tổng quan có khối trạng thái backend với số liệu GPU
    assert.match(doc.getElementById('hero').textContent, /Backend đang chạy/);
    assert.match(doc.getElementById('hero').textContent, /SAM 3 trên NVIDIA GeForce RTX 2060/);
    assert.match(doc.getElementById('hero').textContent, /3,0 GB \/ 6,0 GB/);
    assert.match(doc.getElementById('pill').textContent, /Backend đang chạy/);

    // Trình cài: bước quét liệt kê từng thành phần, bước chọn chỉ chọn sẵn cái cần
    win.openWizard(1);
    await until(() => doc.querySelectorAll('#scanBox tr').length === 4, 'kết quả quét');
    assert.match(doc.getElementById('scanBox').textContent, /cần cập nhật/);
    assert.match(doc.getElementById('scanBox').textContent, /đã có, đúng bản|chưa có/);
    win.go(4);
    await until(() => doc.querySelectorAll('#pick input[data-k]').length === 4, 'bảng chọn thành phần');
    const torch = doc.querySelector('#pick input[data-k="torch"]');
    assert.ok(torch.checked && torch.disabled, 'PyTorch bắt buộc cập nhật: chọn sẵn, không bỏ được');
    await until(() => /Tải về khoảng/.test(doc.getElementById('estimate').textContent), 'ước lượng dung lượng');
    // 0.8.3: cột trái hiện các bước (bước qua có dấu ✓, bước đang làm đánh dấu), trang hoàn tất, mục lục ở bảng điều khiển
    assert.ok(!doc.getElementById('stepbar').classList.contains('hidden') && doc.getElementById('dashnav').classList.contains('hidden'));
    assert.strictEqual(doc.querySelectorAll('#stepbar li.done').length, 3, 'bước 4: ba bước trước đã xong');
    assert.match(doc.querySelector('#stepbar li[aria-current="step"]').textContent, /Thành phần/);
    assert.ok(doc.querySelectorAll('#machine .check').length >= 2, 'kiểm tra máy dạng danh sách');
    win.go(6);
    assert.strictEqual(doc.querySelectorAll('#stepbar li.done').length, 5);
    assert.match(doc.querySelector('.step[data-step="6"]').textContent, /Sẵn sàng/);
    assert.ok(!doc.getElementById('doneDash').classList.contains('hidden'), 'mở từ app (không phải bộ cài): có nút về bảng điều khiển');
    assert.strictEqual(doc.getElementById('doneCvat').textContent, 'Mở CVAT');
    win.showDash();
    assert.ok(!doc.getElementById('dashnav').classList.contains('hidden') && doc.getElementById('stepbar').classList.contains('hidden'));
    // 0.9: thanh bên sáu trang, mỗi lúc chỉ hiện một trang, nhớ trang đang mở
    const navs = [...doc.querySelectorAll('#dashnav button')];
    assert.strictEqual(navs.length, 6);
    for (const b of navs) {
      b.click();
      const shown = [...doc.querySelectorAll('#dash .page')].filter((p) => !p.classList.contains('hidden'));
      assert.deepStrictEqual(shown.map((p) => p.dataset.page), [b.dataset.page], 'chỉ hiện trang ' + b.dataset.page);
      assert.strictEqual(b.getAttribute('aria-current'), 'page');
    }
    assert.strictEqual(win.localStorage.getItem('sam3.page'), 'settings', 'nhớ trang đang mở');
    // Alt+2: chuyển trang bằng phím
    doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: '2', altKey: true, bubbles: true }));
    assert.strictEqual(win.eval('PAGE'), 'sites');
    assert.match(doc.getElementById('sites').textContent, /CVAT edufun.*CVAT · https:\/\/cvat\.edufun\.ai/s, 'trang có nhãn nền tảng');
    win.goPage('home');
    assert.ok(doc.querySelectorAll('#dash svg.ico').length > 20, 'bộ icon đã vẽ');
    assert.strictEqual(doc.querySelectorAll('i[data-ic]').length, 0, 'không còn chỗ icon chưa vẽ');
    // 0.8.0: job gần đây, dùng chung trong nhóm, hỗ trợ/sao lưu/đồng bộ
    await until(() => /Job 12/.test(doc.getElementById('recent').textContent), 'job gần đây');
    assert.match(doc.getElementById('recent').textContent, /Mở tiếp/);

    // 0.9: backend lỗi -> khối lỗi nói nguyên nhân, các bước sửa có nút, chấm đỏ ở Tổng quan
    await call('_test_crash', []);
    await win.refresh();
    const prob = doc.getElementById('problemCard');
    assert.ok(!prob.classList.contains('hidden') && doc.getElementById('hero').classList.contains('hidden'));
    assert.match(prob.textContent, /Máy thiếu thư viện Microsoft Visual C\+\+/);
    assert.match(prob.textContent, /E_BACKEND_EXIT · 0xC0000135 STATUS_DLL_NOT_FOUND/, 'mã lỗi gọn như bản mẫu');
    assert.match(prob.textContent, /Tải từ Microsoft/);
    assert.ok(!doc.getElementById('dot-home').classList.contains('hidden'));
    assert.match(doc.getElementById('pill').className, /bad/);
    [...prob.querySelectorAll('button')].find((b) => /Thử lại/.test(b.textContent)).click();
    await until(() => doc.getElementById('problemCard').classList.contains('hidden'), 'thử lại thì hết lỗi');
    await until(() => /Đang khởi động lại backend|Đang bật backend/.test(doc.getElementById('toasts').textContent), 'nút có phản hồi');

    // 0.9: trang Hỗ trợ có khung xem nhật ký, lọc chỉ lỗi
    win.goPage('support');
    await until(() => doc.querySelectorAll('#logView div').length >= 3, 'nhật ký backend');
    win.toggleErrOnly();
    assert.strictEqual(doc.querySelectorAll('#logView div').length, 1, 'chỉ dòng lỗi');
    assert.match(doc.getElementById('logView').textContent, /exited with/);
    assert.match(doc.getElementById('errOnlyText').textContent, /Chỉ lỗi · 1/);
    win.toggleErrOnly();
    doc.getElementById('logFilter').value = 'profiles';
    win.renderLog();
    assert.ok(doc.querySelector('#logView mark'), 'tô chữ đang lọc');

    // 0.9: Ctrl+K mở bảng lệnh, gõ để lọc (không dấu cũng được), Enter chạy, Esc đóng
    doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));
    assert.ok(!doc.getElementById('palette').classList.contains('hidden'), 'Ctrl+K mở bảng lệnh');
    assert.strictEqual(doc.activeElement, doc.getElementById('palInput'), 'gõ được ngay');
    doc.getElementById('palInput').value = 'thanh phan';
    win.renderPalette();
    assert.match(doc.getElementById('palList').textContent, /Đi tới Thành phần/);
    doc.getElementById('palInput').dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    assert.ok(doc.getElementById('palette').classList.contains('hidden'));
    assert.strictEqual(win.eval('PAGE'), 'comps');
    win.openPalette();
    doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    assert.ok(doc.getElementById('palette').classList.contains('hidden'), 'Esc đóng bảng lệnh');

    // 0.9: xoá trang làm ngay, có Hoàn tác; hỏi xác nhận bằng hộp thoại của app (Esc là huỷ)
    win.goPage('sites');
    await win.removeSite(0);
    assert.strictEqual((await call('status', [])).sites.length, 0);
    const undo = [...doc.querySelectorAll('#toasts button')].find((b) => b.textContent === 'Hoàn tác');
    assert.ok(undo, 'có nút Hoàn tác');
    undo.click();
    await until(async () => true, '');
    await sleep(300);
    assert.strictEqual((await call('status', [])).sites.length, 1, 'hoàn tác thì trang quay lại');
    const asking = win.removeComp('face');
    assert.ok(!doc.getElementById('askModal').classList.contains('hidden'));
    assert.strictEqual(doc.activeElement, doc.getElementById('askOk'), 'tiêu điểm ở nút chính: Enter là đồng ý');
    doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    assert.strictEqual(await asking, undefined);
    assert.ok(doc.getElementById('askModal').classList.contains('hidden'), 'Esc huỷ hộp xác nhận');
    win.setLanShare(true);
    await until(() => /key: /.test(doc.getElementById('lanBox').textContent), 'thông tin chia sẻ LAN');
    assert.ok(doc.getElementById('lanShare').checked);
    win.setLanShare(false);
    await until(() => doc.getElementById('lanBox').textContent === '', 'tắt chia sẻ');
    win.doReport();
    await until(() => /bao-loi-.*\.zip/.test(doc.getElementById('supportMsg').textContent), 'tạo gói báo lỗi');
    assert.strictEqual(doc.querySelector('#startSeg [aria-pressed="true"]').dataset.v, 'cvat');
    assert.strictEqual(doc.getElementById('idleMin').value, '15');
    assert.ok(doc.getElementById('autoRestart').checked && doc.getElementById('autoBackend').checked);
    await win.setSetting('auto_restart_backend', false);
    assert.strictEqual((await call('status', [])).auto_restart_backend, false);
    await win.setTheme('dark');
    assert.strictEqual(doc.documentElement.dataset.theme, 'dark');
    assert.strictEqual((await call('status', [])).theme, 'dark');
    await win.setTheme('system');
    assert.strictEqual(doc.documentElement.dataset.theme, undefined);
    // 0.8.1: đổi sang English ngay trong bảng điều khiển, không còn chữ tiếng Việt; đổi lại thì về tiếng Việt
    const VI = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i;
    const leftovers = () => {
      const texts = [];
      const walker = doc.createTreeWalker(doc.body, win.NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) if (walker.currentNode.parentNode.tagName !== 'SCRIPT') texts.push(walker.currentNode.nodeValue);
      doc.querySelectorAll('[title],[placeholder],[aria-label]').forEach((el) => ['title', 'placeholder', 'aria-label'].forEach((a) => el.getAttribute(a) && texts.push(el.getAttribute(a))));
      return texts.map((t) => ['Tiếng Việt', 'Ngôn ngữ'].reduce((x, w) => x.split(w).join(''), t)).filter((t) => VI.test(t));
    };
    await win.setLang('en');
    await until(() => /Recent jobs/.test(doc.body.textContent) && /Share this machine/.test(doc.body.textContent), 'giao diện English');
    assert.strictEqual(doc.querySelectorAll('#dashnav button').length, 6, 'đổi ngôn ngữ vẫn còn thanh bên');
    assert.strictEqual(doc.querySelectorAll('i[data-ic]').length, 0, 'đổi ngôn ngữ vẫn còn icon');
    // giờ tương đối dịch được dù đứng chung đoạn với tên trang (không phụ thuộc múi giờ của máy chạy test)
    const now = new Date(), pad = (n) => String(n).padStart(2, '0');
    const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
    doc.getElementById('recent').innerHTML = win.jobRow({ job: 7, task: 1, site: 'CVAT edufun', at: stamp }, 0);
    assert.match(doc.getElementById('recent').textContent, /CVAT edufun · (just now|1 min ago)$/m);
    win.openPalette();
    assert.match(doc.getElementById('palList').textContent, /Go to Components/);
    win.closeModal('palette');
    await sleep(600);
    assert.strictEqual(doc.documentElement.lang, 'en');
    assert.deepStrictEqual(leftovers(), [], 'bảng điều khiển English còn tiếng Việt');
    assert.strictEqual((await call('ui_lang', [])), 'en', 'app nhớ ngôn ngữ');
    await win.setLang('vi');
    await until(() => /Job gần đây/.test(doc.body.textContent), 'về tiếng Việt');
    assert.deepStrictEqual(errors, [], 'không có lỗi JS: ' + errors.join('\n'));
    assert.deepStrictEqual(apiErrors, [], 'không có lỗi API: ' + apiErrors.join('\n'));
    console.log('DESKTOP CONTROL SMOKE: PASS');
  } finally {
    helper.kill();
  }
}
main().then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
