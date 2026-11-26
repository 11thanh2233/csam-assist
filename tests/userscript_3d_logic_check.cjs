/* Kiểm tra các hàm thuần của panel 3D: lấy ĐÚNG mã nguồn trong userscript (theo acorn) rồi chạy trong vm.
 * Không copy-paste hàm, nên sửa userscript là test này kiểm luôn.
 *   node tests/userscript_3d_logic_check.cjs
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const acorn = require('acorn');

const SRC = path.resolve(__dirname, '..', 'userscript', 'sam3-cvat-assist.user.js');
const src = fs.readFileSync(SRC, 'utf8');
const ast = acorn.parse(src, { ecmaVersion: 2022 });

function sourceOf(name) {
  let found = null;
  (function walk(node) {
    if (!node || typeof node !== 'object' || found) return;
    if (node.type === 'FunctionDeclaration' && node.id && node.id.name === name) { found = node; return; }
    for (const k of Object.keys(node)) {
      if (k === 'loc' || k === 'range') continue;
      const v = node[k];
      if (Array.isArray(v)) v.forEach(walk);
      else if (v && typeof v.type === 'string') walk(v);
    }
  })(ast);
  if (!found) throw new Error('không tìm thấy hàm ' + name);
  return src.slice(found.start, found.end);
}

const ctx = { console };
vm.createContext(ctx);
for (const name of ['lidarBuckets', 'bucketOrder', 'phaseText', 'trackNote']) {
  vm.runInContext(sourceOf(name), ctx, { filename: name });
}

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('PASS', name); } else { fail++; console.log('FAIL', name, extra === undefined ? '' : JSON.stringify(extra)); }
}

// --- 3 rổ ---
const tracks = [
  { tid: 1, bucket: 'high_confidence', flags: [] },
  { tid: 2, bucket: 'needs_review', flags: [{ code: 'UNCERTAIN_CLASS', message: 'Kích thước khớp nhiều class' }] },
  { tid: 3, bucket: 'suspected_noise', flags: [] },
  { tid: 4, flags: [], checked: true },
  { tid: 5, flags: [{ code: 'LOW_VISIBILITY' }], checked: true },
  { tid: 6, flags: [], checked: false },
];
const b = ctx.lidarBuckets(tracks);
check('lidarBuckets chia 3 rổ', b.high.length === 2 && b.review.length === 2 && b.noise.length === 2, {
  high: b.high.map((t) => t.tid), review: b.review.map((t) => t.tid), noise: b.noise.map((t) => t.tid),
});
check('track bị bỏ tick vào rổ Nghi rác', b.noise.some((t) => t.tid === 6));
check('track có cờ vào rổ Cần xem', b.review.some((t) => t.tid === 5));

// --- thứ tự sắp xếp: Cần xem trước, rồi Nghi rác, rồi Tin cậy ---
const order = tracks.slice().sort((x, y) => ctx.bucketOrder(x) - ctx.bucketOrder(y)).map((t) => t.bucket || 'x');
check('bucketOrder: Cần xem (0) trước Nghi rác (1) trước Tin cậy (2)',
  ctx.bucketOrder({ bucket: 'needs_review' }) === 0 && ctx.bucketOrder({ bucket: 'suspected_noise' }) === 1
  && ctx.bucketOrder({ bucket: 'high_confidence' }) === 2, order);

// --- dòng tiến độ từng pha ---
const t1 = ctx.phaseText('geometry', { done: 20, total: 40 });
const t2 = ctx.phaseText('vision', { done: 5, total: 12 });
const t3 = ctx.phaseText('llm', { done: 18, total: 32 });
check('phaseText pha 1', t1.includes('20/40') && t1.includes('1/3'), t1);
check('phaseText pha 2', t2.includes('5/12') && t2.includes('2/3'), t2);
check('phaseText pha 3', t3.includes('18/32') && t3.includes('3/3'), t3);
check('phaseText không ước lượng ngược', !/\d+\s*(giây|phút|s còn lại)/.test(t1 + t2 + t3));

// --- ghi chú tiếng Việt thay nhãn cờ kỹ thuật ---
check('trackNote ưu tiên lý do của Claude', ctx.trackNote({ llm_reason: 'Pickup with open cargo bed' }) === 'Pickup with open cargo bed');
check('trackNote dịch cờ LOW_VISIBILITY', ctx.trackNote({ flags: [{ code: 'LOW_VISIBILITY' }] }).includes('Ít điểm LiDAR'));
check('trackNote không lộ mã cờ thô', !/LOW_VISIBILITY|ATTRIBUTE_CHECK|UNCERTAIN_CLASS/.test(
  ctx.trackNote({ flags: [{ code: 'LOW_VISIBILITY' }, { code: 'ATTRIBUTE_CHECK', message: 'Cụm điểm dính vật khác: x' }] })));
check('trackNote thêm ghi chú nghi rác từ ảnh', ctx.trackNote({ flags: [], vision2d: { verdict: 'none', camera: 'CAM_FRONT' } })
  .includes('nghi rác'));

// --- 0.9.10: dòng báo phiên đã dừng + tiến độ gửi frame ---
vm.runInContext(sourceOf('pausedText'), ctx, { filename: 'pausedText' });
const up = ctx.phaseText('upload', { done: 12, total: 40 });
check('phaseText bước gửi frame', up.includes('12/40'), up);
const pz = ctx.pausedText({ phase: 'llm', done: 16, total: 40, error: '' });
check('pausedText nói dừng ở đâu và cách tiếp tục', pz.includes('16/40') && pz.includes('Tiếp tục từ chỗ dừng'), pz);
check('pausedText kèm lỗi', ctx.pausedText({ phase: 'llm', done: 0, total: 0, error: 'hết giờ' }).startsWith('Lỗi: hết giờ'));

// --- 0.9.10: chạy cả luồng phiên 3D với backend giả: dừng ở Pha 3, F5, tiếp tục chỉ chạy phần còn thiếu ---
async function sessionFlow() {
  const store = {};
  const calls = [];
  const sess = { frames: new Set(), tracks: 10, judged: 0, final: false, gone: new Set(), n: 0 };
  const env = {
    console, Set, Math, Object, Promise, String, Array, Date, JSON,
    S: { jobId: 7, lidarSession: null, stopRequested: false, tracks: [], trackMeta: null, busy: '' },
    GM_getValue: (k, d) => (k in store ? JSON.parse(JSON.stringify(store[k])) : d),
    GM_setValue: (k, v) => { store[k] = JSON.parse(JSON.stringify(v)); },
    jobRange: () => ({ from: 0, to: 9 }), jobFrames: () => [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    l3: () => ({ batch: 5, useLlm: true }), llmOn: () => true, lidarBody: (x) => x,
    fetchCloud: async (f) => ({ frame: f }), fetchContext: async () => '', rememberCamera() {},
    render() {}, notify() {}, persist() {}, setStatus: (t, k) => { env.lastStatus = [t, k]; },
    run: async (_t, fn) => { env.S.busy = 'x'; try { await fn(); } catch (e) { env.lastStatus = [e.message, 'error']; } env.S.busy = ''; },
    lidarBuckets: ctx.lidarBuckets, bucketOrder: ctx.bucketOrder,
    backend: async (method, url, body) => {
      calls.push(`${method} ${url}${body && body.max_batches ? ' x1' : ''}${body && body.retry_failed ? ' retry' : ''}`);
      const m = url.match(/session\/(S\d+)\//);
      if (m && sess.gone.has(m[1])) throw Object.assign(new Error('Phiên 3D không còn'), { status: 404 });
      if (url.endsWith('/init')) { body.frames.forEach((f) => sess.frames.add(f.frame)); return { session_id: `S${++sess.n}` }; }
      if (url.endsWith('/frames')) { body.frames.forEach((f) => sess.frames.add(f.frame)); return {}; }
      if (url.endsWith('/progress')) return { frame_list: [...sess.frames] };
      if (url.endsWith('/geometry')) return { remaining: 0, done: { geometry: sess.frames.size } };
      if (url.endsWith('/vision')) return { skipped: 'chưa có bộ dò 2D' };
      if (url.endsWith('/llm_batch')) {
        if (env.failNextLlm) { env.failNextLlm = false; throw new Error('Backend không phản hồi sau 5 phút.'); }
        sess.judged = Math.min(sess.tracks, sess.judged + 8); sess.final = sess.judged >= sess.tracks;
        if (env.stopAfterLlm) env.S.stopRequested = true;
        return { judged: sess.judged, total: sess.tracks, remaining: sess.tracks - sess.judged, final: sess.final,
          buckets: sess.final ? { high_confidence: 10, needs_review: 0, suspected_noise: 0 } : undefined };
      }
      if (url.endsWith('/result')) return { tracks: [{ tid: 1, bucket: 'high_confidence', shapes: [{ frame: 0, points: [] }] }], checked_by: { claude: 1 } };
      return {};
    },
  };
  const fctx = vm.createContext(env);
  for (const name of ['saveLidarSession', 'restoreLidarSession', 'lidarPause', 'lidarFetchResult', 'lidarUpload',
    'lidarTrackSession', 'lidarShowPartial', 'dropLidarSession', 'phaseText', 'pausedText']) {
    vm.runInContext(sourceOf(name), fctx, { filename: name });
  }
  // 1) chạy, Claude chạy 1 lô rồi bạn bấm dừng
  env.stopAfterLlm = true;
  await fctx.lidarTrackSession(false);
  const st = env.S.lidarSession;
  check('dừng ở Pha 3: phiên giữ lại, đánh dấu tạm dừng', st && st.sid === 'S1' && st.paused && st.phase === 'llm', st);
  check('Claude gọi từng lô (max_batches 1)', calls.filter((c) => c.includes('llm_batch')).every((c) => c.includes(' x1')), calls);
  check('nhớ phiên theo job', store.lidarSess && store.lidarSess[7] && store.lidarSess[7].sid === 'S1', store);
  // 2) F5: nạp lại trang -> phiên tạm dừng hiện lại
  env.S.lidarSession = null;
  fctx.restoreLidarSession();
  check('tải lại trang: thấy phiên tạm dừng', env.S.lidarSession && env.S.lidarSession.paused && env.S.lidarSession.sid === 'S1');
  // 3) Tiếp tục: không gửi lại frame, không init lại, chạy nốt Claude, ra kết quả, xoá phiên đã nhớ
  env.stopAfterLlm = false; calls.length = 0;
  await fctx.lidarTrackSession(true);
  check('tiếp tục: không tạo phiên mới, không gửi lại frame', !calls.some((c) => c.includes('/init') || c.includes('/frames')), calls);
  check('tiếp tục: chấm lại track Claude lỗi ở lô đầu', calls.some((c) => c.includes('llm_batch') && c.includes('retry')), calls);
  check('tiếp tục: xong, có kết quả, phiên đã đóng', env.S.lidarSession === null && env.S.tracks.length === 1 && calls.some((c) => c.endsWith('/close')), calls);
  check('xong: không còn nhớ phiên', !(store.lidarSess || {})[7]);
  // 4) lỗi hết giờ giữa chừng -> nút tiếp tục; backend khởi động lại (404) -> chạy lại từ đầu, báo rõ
  sess.judged = 0; sess.final = false; env.failNextLlm = true;
  await fctx.lidarTrackSession(false);
  check('hết giờ ở Pha 3: tạm dừng kèm lỗi', env.S.lidarSession && env.S.lidarSession.paused && /5 phút/.test(env.S.lidarSession.error), env.S.lidarSession);
  sess.gone.add(env.S.lidarSession.sid); sess.frames.clear(); calls.length = 0;
  await fctx.lidarTrackSession(true);
  check('phiên mất trên backend: chạy lại từ đầu', calls.some((c) => c.endsWith('/init')) && (env.S.trackMeta.warnings || []).some((w) => w.includes('khởi động lại')), calls);
}

sessionFlow().then(() => {
  console.log(`\n== userscript 3D logic: ${pass} pass, ${fail} fail ==`);
  process.exit(fail ? 1 : 0);
}).catch((e) => { console.log('FAIL sessionFlow', e && e.stack); process.exit(1); });
