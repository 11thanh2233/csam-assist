// Trích chuỗi tiếng Việt từ mã JavaScript (dùng acorn). In ra JSON: [{text, kind, line}]
// kind: "exact" cho chuỗi thường, "template" cho template literal có ${...} (chỗ biến ghi là {0}, {1}...).
// Dùng: node scripts/i18n_js.cjs <file.js|file.html>
'use strict';
const fs = require('fs');
const acorn = require('acorn');
const walk = require('acorn-walk');

const VI = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i;

function isStr(n) { return (n.type === 'Literal' && typeof n.value === 'string') || n.type === 'TemplateLiteral'; }

// Nối chuỗi bằng "+" ('Đã ghi ' + n + ' nhãn') được gộp thành một mẫu {0}, giống template literal.
function flatten(node, out) {
  if (node.type === 'BinaryExpression' && node.operator === '+' && hasStr(node)) { flatten(node.left, out); flatten(node.right, out); }
  else out.push(node);
}
function hasStr(n) {
  if (isStr(n)) return true;
  return n.type === 'BinaryExpression' && n.operator === '+' && (hasStr(n.left) || hasStr(n.right));
}
const esc = (p) => p.replace(/[{}]/g, (c) => c + c);

function scan(code, lineOffset, out) {
  let ast;
  try {
    ast = acorn.parse(code, { ecmaVersion: 'latest', sourceType: 'script', locations: true, allowHashBang: true });
  } catch (e) {
    console.error(`parse error: ${e.message}`);
    process.exit(2);
  }
  const consumed = new Set();
  const emit = (text, tmpl, node) => out.push({ text, kind: tmpl ? 'template' : 'exact', line: node.loc.start.line + lineOffset });
  const isChain = (n) => n && n.type === 'BinaryExpression' && n.operator === '+' && hasStr(n);
  // Lượt 1: gộp các chuỗi nối bằng "+" (acorn-walk đi con trước cha nên phải làm riêng một lượt)
  walk.fullAncestor(ast, (node, _st, anc) => {
    if (!isChain(node) || isChain(anc[anc.length - 2])) return;
    const parts = [];
    flatten(node, parts);
    let text = '', k = 0, vi = false;
    for (const p of parts) {
      if (p.type === 'Literal' && typeof p.value === 'string') { text += esc(p.value); vi = vi || VI.test(p.value); consumed.add(p); }
      else if (p.type === 'TemplateLiteral') {
        consumed.add(p);
        p.quasis.forEach((q, i) => { text += esc(q.value.cooked); vi = vi || VI.test(q.value.cooked); if (i < p.expressions.length) text += `{${k++}}`; });
      } else text += `{${k++}}`;
    }
    if (vi) emit(text, k > 0, node);
  });
  // Lượt 2: chuỗi và template literal còn lại
  walk.full(ast, (node) => {
    if (consumed.has(node)) return;
    if (node.type === 'Literal' && typeof node.value === 'string' && VI.test(node.value)) emit(node.value, false, node);
    else if (node.type === 'TemplateLiteral') {
      const parts = node.quasis.map((q) => q.value.cooked);
      if (!parts.some((p) => VI.test(p))) return;
      let text = '';
      parts.forEach((p, i) => { text += esc(p); if (i < parts.length - 1) text += `{${i}}`; });
      emit(text, node.expressions.length > 0, node);
    }
  });
}

const file = process.argv[2];
// Bỏ qua phần bộ dịch đã nhúng (giữ số dòng)
const src = fs.readFileSync(file, 'utf8').replace(/\/\/ <i18n-catalog>[\s\S]*?\/\/ <\/i18n-catalog>/g, (m) => m.replace(/[^\n]/g, ' '));
const out = [];
if (file.endsWith('.html')) {
  const re = /<script[^>]*>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(src))) {
    const before = src.slice(0, m.index + m[0].indexOf('>') + 1);
    scan(m[1], before.split('\n').length - 1, out);
  }
} else {
  scan(src, 0, out);
}
process.stdout.write(JSON.stringify(out));
