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

console.log(`\n== userscript 3D logic: ${pass} pass, ${fail} fail ==`);
process.exit(fail ? 1 : 0);
