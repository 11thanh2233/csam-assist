// SAM3 Assist Desktop: thay các hàm GM_* của Tampermonkey bằng cầu nối tới app (window.pywebview.api).
// Chạy trước userscript, trong trang CVAT. window.__sam3Boot do app chèn ngay trước đoạn này.
(function () {
  if (window.__sam3ShimReady) return;
  var boot = window.__sam3Boot || { values: {}, version: '' };
  var store = boot.values || {};
  var pending = [];

  function api() { return window.pywebview && window.pywebview.api; }
  function whenReady(fn) {
    if (api() && api().gm_request) return fn(api());
    pending.push(fn);
  }
  function flush() {
    if (!(api() && api().gm_request)) return;
    var list = pending; pending = [];
    list.forEach(function (fn) { try { fn(api()); } catch (e) { console.error('[SAM3 Assist]', e); } });
  }
  window.addEventListener('pywebviewready', flush);
  var poll = setInterval(function () { if (api() && api().gm_request) { clearInterval(poll); flush(); } }, 100);
  setTimeout(function () { clearInterval(poll); }, 60000);

  function clone(v) { return v === undefined ? v : JSON.parse(JSON.stringify(v)); }

  window.GM_getValue = function (key, def) {
    return Object.prototype.hasOwnProperty.call(store, key) ? clone(store[key]) : def;
  };
  window.GM_setValue = function (key, value) {
    store[key] = clone(value);
    whenReady(function (a) { a.gm_set(key, value === undefined ? null : value).catch(function (e) { console.error('[SAM3 Assist] lưu', key, e); }); });
  };
  window.GM_xmlhttpRequest = function (o) {
    var done = false;
    var timer = o.timeout ? setTimeout(function () { if (!done) { done = true; o.ontimeout && o.ontimeout({}); } }, o.timeout + 5000) : null;
    function finish(cb, r) { if (done) return; done = true; if (timer) clearTimeout(timer); cb && cb(r); }
    whenReady(function (a) {
      a.gm_request({ method: o.method, url: o.url, data: o.data === undefined ? null : o.data, timeout: o.timeout || 0 })
        .then(function (r) {
          if (r.error === 'timeout') finish(o.ontimeout, r);
          else if (r.error === 'network') finish(o.onerror, r);
          else finish(o.onload, r);
        })
        .catch(function (e) { finish(o.onerror, { error: String(e), status: 0, responseText: '' }); });
    });
    return { abort: function () { done = true; } };
  };
  window.GM_setClipboard = function (text) {
    var t = String(text);
    var viaApp = function () { whenReady(function (a) { a.clipboard(t); }); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).catch(viaApp);
    else viaApp();
  };

  // Lệnh trong menu Tampermonkey -> nút nhỏ ở góc dưới bên trái.
  var commands = [];
  window.GM_registerMenuCommand = function (name, fn) {
    commands.push({ name: name, fn: fn });
    var btn = document.getElementById('sam3-desktop-menu');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = 'sam3-desktop-menu';
      btn.textContent = 'SAM3';
      btn.title = 'SAM3 Assist';
      btn.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:2147483646;padding:3px 8px;font:12px sans-serif;' +
        'border:1px solid #888;border-radius:6px;background:#1f2937;color:#fff;opacity:.75;cursor:pointer';
      btn.addEventListener('click', function () {
        if (commands.length === 1) return commands[0].fn();
        var names = commands.map(function (c, i) { return (i + 1) + '. ' + c.name; }).join('\n');
        var pick = parseInt(window.prompt(names, '1'), 10);
        if (commands[pick - 1]) commands[pick - 1].fn();
      });
      (document.body || document.documentElement).appendChild(btn);
    }
    return commands.length;
  };
  window.GM_notification = function (o, title) {
    var opt = typeof o === 'string' ? { text: o, title: title || 'SAM3 Assist' } : (o || {});
    whenReady(function (a) { a.notify(opt.title || 'SAM3 Assist', opt.text || ''); });
  };
  window.GM_info = { script: { name: 'SAM3 CVAT Assist', version: boot.version }, scriptHandler: 'SAM3 Assist Desktop' };
  // Nút "SAM3" ở góc trái: thêm lệnh mở bảng điều khiển của app (cập nhật, thành phần, job gần đây).
  setTimeout(function () { window.GM_registerMenuCommand('Bảng điều khiển của app', function () { whenReady(function (a) { a.open_control(); }); }); }, 0);
  window.__sam3ShimReady = true;
})();
