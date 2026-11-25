/* Chạy userscript trong sandbox vm với DOM/GM_* giả tối thiểu, để bắt lỗi tham chiếu ở mức nạp script.
 * Không thay được jsdom + CVAT giả, nhưng bắt được lỗi "not defined"/"not a function" ngay khi nạp.
 *   node tests/userscript_load_check.cjs
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'userscript', 'sam3-cvat-assist.user.js');

function el(tag) {
  const node = {
    tagName: String(tag || 'div').toUpperCase(), style: null, children: [], dataset: {}, attributes: {},
    textContent: '', innerHTML: '', title: '', value: '', checked: false, files: [], open: false, disabled: false,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(k, v) { this.attributes[k] = v; }, getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
    removeAttribute(k) { delete this.attributes[k]; }, hasAttribute(k) { return k in this.attributes; },
    appendChild(c) { this.children.push(c); return c; }, append(...c) { this.children.push(...c); },
    insertBefore(c) { this.children.push(c); return c; }, replaceChild(a, b) { return b; }, removeChild(c) { return c; },
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    querySelector() { return null; }, querySelectorAll() { return []; }, closest() { return null; },
    contains() { return false; }, remove() {}, focus() {}, blur() {}, click() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100 }; },
    cloneNode() { return el(tag); }, attachShadow() { const s = el('#shadow'); s.host = node; return s; },
    getRootNode() { return doc; }, scrollIntoView() {}, setSelectionRange() {}, select() {},
  };
  node.style = new Proxy({}, { get: (t, k) => (k in t ? t[k] : (k === 'setProperty' ? () => {} : '')), set: (t, k, v) => (t[k] = v, true) });
  node.style.setProperty = () => {};
  node.style.removeProperty = () => {};
  return node;
}

const doc = {
  createElement: el, createElementNS: (ns, t) => el(t), createTextNode: (t) => ({ nodeValue: t, textContent: t }),
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {},
  head: el('head'), body: el('body'), documentElement: el('html'), activeElement: null, cookie: '',
  location: { href: 'https://app.cvat.ai/jobs/1', hostname: 'app.cvat.ai', pathname: '/jobs/1', search: '' },
  title: '', readyState: 'complete', visibilityState: 'visible',
};

const sandbox = {
  console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
  setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  queueMicrotask() {}, requestAnimationFrame: () => 0, cancelAnimationFrame() {},
  fetch: async () => { throw new Error('không có mạng trong bộ kiểm này'); },
  GM_getValue: (k, d) => d, GM_setValue() {}, GM_deleteValue() {}, GM_listValues: () => [], GM_addStyle() {},
  GM_xmlhttpRequest() {}, GM_setClipboard() {}, GM_notification() {}, GM_registerMenuCommand() {},
  GM_info: { script: { version: '0.9.7' } },
  document: doc, navigator: { userAgent: 'node-load-check', language: 'vi' }, location: doc.location,
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
  Blob: function Blob() {}, FileReader: function FileReader() { this.readAsDataURL = () => {}; },
  IntersectionObserver: function IO() { return { observe() {}, disconnect() {} }; },
  ResizeObserver: function RO() { return { observe() {}, disconnect() {} }; },
  MutationObserver: function MO() { return { observe() {}, disconnect() {} }; },
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  Image: function Image() {}, CustomEvent: function CE() {}, Event: function E() {}, KeyboardEvent: function KE() {},
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.unsafeWindow = sandbox;
sandbox.globalThis = sandbox;
sandbox.top = sandbox;

function main() {
  const src = fs.readFileSync(SRC, 'utf8');
  vm.createContext(sandbox);
  try {
    vm.runInContext(src, sandbox, { timeout: 30000, filename: 'sam3-cvat-assist.user.js' });
    console.log('LOAD CHECK: PASS — script nạp không lỗi tham chiếu');
  } catch (e) {
    console.log('LOAD CHECK: FAIL —', e.message);
    console.log(String(e.stack || '').split('\n').slice(0, 6).join('\n'));
    process.exit(1);
  }
}

main();
