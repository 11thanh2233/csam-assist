"""Trang sửa và duyệt bản guideline nháp (mở từ panel trên CVAT, chạy trên chính backend)."""

PAGE = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Duyệt guideline nháp</title>
<style>
  :root {
    --bg: #ffffff; --bg-soft: #f4f5f7; --line: #d9dce1; --text: #1d2433; --muted: #5b6475;
    --accent: #1b6ef3; --accent-text: #ffffff; --ok: #0f7b3f; --warn: #a15c00; --err: #b42318;
    --radius: 6px; --gap: 8px;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg-soft); color: var(--text); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { background: var(--bg); border-bottom: 1px solid var(--line); padding: 10px 16px; display: flex; gap: var(--gap); align-items: center; flex-wrap: wrap; position: sticky; top: 0; z-index: 2; }
  header h1 { font-size: 16px; margin: 0; flex: 1; min-width: 200px; }
  main { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; padding: 16px; }
  @media (max-width: 900px) { main { grid-template-columns: minmax(0, 1fr); } }
  section { background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius); padding: 12px; min-width: 0; }
  section h2 { font-size: 14px; margin: 0 0 8px; }
  textarea { width: 100%; height: 70vh; font: 13px/1.5 ui-monospace, Consolas, monospace; color: var(--text); background: var(--bg);
    border: 1px solid var(--line); border-radius: var(--radius); padding: 8px; resize: vertical; }
  #preview { height: 70vh; overflow: auto; }
  #preview table { border-collapse: collapse; width: 100%; margin: 8px 0; }
  #preview th, #preview td { border: 1px solid var(--line); padding: 4px 6px; text-align: left; vertical-align: top; }
  #preview blockquote { margin: 8px 0; padding: 6px 10px; border-left: 3px solid var(--warn); background: var(--bg-soft); }
  #preview h1 { font-size: 20px; } #preview h2 { font-size: 16px; margin-top: 18px; } #preview h3 { font-size: 14px; }
  #preview code { background: var(--bg-soft); padding: 0 3px; border-radius: 3px; }
  button { font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius); padding: 6px 10px; cursor: pointer; }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:focus-visible, textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  button.primary { background: var(--accent); color: var(--accent-text); border-color: var(--accent); font-weight: 600; }
  button.small { padding: 2px 6px; font-size: 12px; }
  .muted { color: var(--muted); } .small { font-size: 12px; }
  #status { min-height: 20px; } #status.ok { color: var(--ok); } #status.error { color: var(--err); }
  .wide { grid-column: 1 / -1; }
  ul.plain { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  ul.plain li { border: 1px solid var(--line); border-radius: var(--radius); padding: 6px 8px; display: flex; gap: var(--gap); align-items: flex-start; }
  ul.plain li span.grow { flex: 1; min-width: 0; }
  .badge { font-size: 11px; padding: 1px 6px; border-radius: 10px; background: var(--bg-soft); border: 1px solid var(--line); color: var(--warn); white-space: nowrap; }
  .badge.ok { color: var(--ok); } .badge.off { color: var(--muted); }
  .cases { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
  .cases figure { margin: 0; border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
  .cases img { width: 100%; display: block; background: var(--bg-soft); }
  .cases figcaption { padding: 6px 8px; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1 id="title">Guideline nháp</h1>
  <span id="status" role="status" class="muted"></span>
  <button id="download" type="button">Tải bản .md</button>
  <button id="approve" type="button" class="primary">Duyệt và lưu</button>
</header>
<main>
  <section>
    <h2>Sửa bản nháp (Markdown)</h2>
    <p class="muted small" style="margin:0 0 8px">Sửa thẳng vào đây. Khi bạn bấm Duyệt, mỗi chỗ bạn sửa, thêm hoặc bỏ sẽ thành một bài học cho các lần soạn sau. Phần <code>&lt;!--r:…--&gt;</code> ở cuối dòng là mã của luật; giữ nguyên nó để tool biết bạn sửa luật nào.</p>
    <textarea id="editor" spellcheck="false" aria-label="Nội dung guideline"></textarea>
  </section>
  <section>
    <h2>Xem trước</h2>
    <div id="preview"></div>
  </section>
  <section class="wide" id="cases-box" hidden>
    <h2>Ca khó tìm thấy trong ảnh mẫu</h2>
    <div class="cases" id="cases"></div>
  </section>
  <section class="wide">
    <h2>Bài học tool đang nhớ</h2>
    <p class="muted small" style="margin:0 0 8px">"Đã xác nhận" là bài học từ người duyệt hoặc guideline mẫu và được tự áp dụng. "Giả thuyết" là điều tool tự suy ra: chỉ được nhắc lại, không tự thành luật. Tắt bài học sai để nó không lặp lại.</p>
    <ul class="plain" id="lessons"></ul>
  </section>
</main>
<script>
(function () {
  'use strict';
  const params = new URLSearchParams(location.search);
  const key = params.get('key') || '';
  const runId = location.pathname.split('/').filter(Boolean).pop();
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function setStatus(text, kind) { $('status').textContent = text || ''; $('status').className = kind || 'muted'; }

  async function api(method, path, body) {
    const res = await fetch(path, { method, headers: { 'Content-Type': 'application/json', 'X-API-Key': key },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || ('Lỗi ' + res.status));
    return data;
  }

  function inline(text) {
    return esc(text).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>');
  }

  // Bộ dựng Markdown tối giản: tiêu đề, bảng, danh sách, trích dẫn, đoạn văn.
  function render(markdown) {
    const lines = markdown.replace(/\s*<!--r:[^>]+-->/g, '').split('\n');
    const out = [];
    let list = false, table = false;
    const close = () => { if (list) { out.push('</ul>'); list = false; } if (table) { out.push('</table>'); table = false; } };
    for (const raw of lines) {
      const line = raw.trimEnd();
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) { close(); out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); continue; }
      if (/^\|.*\|$/.test(line)) {
        if (/^\|[\s:|-]+\|$/.test(line)) continue;
        const cells = line.slice(1, -1).split('|').map((c) => c.trim());
        if (list) { out.push('</ul>'); list = false; }
        const tag = table ? 'td' : 'th';
        if (!table) { out.push('<table>'); table = true; }
        out.push('<tr>' + cells.map((c) => `<${tag}>${inline(c)}</${tag}>`).join('') + '</tr>');
        continue;
      }
      const item = line.match(/^\s*[-*]\s+(.*)$/);
      if (item) {
        if (table) { out.push('</table>'); table = false; }
        if (!list) { out.push('<ul>'); list = true; }
        out.push('<li>' + inline(item[1]).replace(/^\[ \]/, '☐').replace(/^\[x\]/i, '☑') + '</li>');
        continue;
      }
      close();
      if (line.startsWith('>')) out.push('<blockquote>' + inline(line.replace(/^>\s?/, '')) + '</blockquote>');
      else if (line.trim()) out.push('<p>' + inline(line) + '</p>');
    }
    close();
    return out.join('\n');
  }

  function showLessons(lessons) {
    const names = { confirmed: ['Đã xác nhận', 'ok'], hypothesis: ['Giả thuyết', ''], disabled: ['Đã tắt', 'off'] };
    $('lessons').replaceChildren(...lessons.map((l) => {
      const li = document.createElement('li');
      const [label, cls] = names[l.status] || [l.status, ''];
      li.innerHTML = `<span class="badge ${cls}">${label}</span><span class="grow">${esc(l.text)}<br><span class="muted small">${esc(l.source)} · ${esc((l.tags || []).join(', '))}</span></span>`;
      const add = (text, status) => {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'small'; b.textContent = text;
        b.onclick = async () => {
          try { await api('POST', `/guideline/lessons/${l.id}/status`, { status }); await loadLessons(); }
          catch (err) { setStatus(err.message, 'error'); }
        };
        li.append(b);
      };
      if (l.status === 'disabled') add('Bật lại', 'confirmed');
      else { if (l.status === 'hypothesis') add('Xác nhận', 'confirmed'); add('Tắt', 'disabled'); }
      return li;
    }));
  }

  async function loadLessons() { showLessons((await api('GET', '/guideline/lessons')).lessons); }

  async function load() {
    try {
      const run = await api('GET', `/guideline/run/${runId}/data`);
      $('title').textContent = (run.approved ? 'Guideline đã duyệt' : 'Guideline nháp') + (run.title ? ' — ' + run.title : '');
      $('editor').value = run.markdown;
      $('preview').innerHTML = render(run.markdown);
      if (run.cases.length) {
        $('cases-box').hidden = false;
        $('cases').replaceChildren(...run.cases.map((c, i) => {
          const fig = document.createElement('figure');
          fig.innerHTML = `<img alt="Ca khó ở frame ${c.frame}" loading="lazy" src="/guideline/run/${runId}/image/case_${i}.jpg?key=${encodeURIComponent(key)}"><figcaption><strong>Frame ${c.frame}</strong> · ${esc(c.code)}<br>${esc(c.text)}</figcaption>`;
          return fig;
        }));
      }
      await loadLessons();
    } catch (err) { setStatus(err.message, 'error'); }
  }

  $('editor').addEventListener('input', () => { $('preview').innerHTML = render($('editor').value); });
  $('download').addEventListener('click', () => {
    const text = $('editor').value.replace(/\s*<!--r:[^>]+-->/g, '');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }));
    a.download = `guideline_${runId}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  $('approve').addEventListener('click', async () => {
    $('approve').disabled = true;
    setStatus('Đang lưu…');
    try {
      const res = await api('POST', '/guideline/approve', { run_id: runId, markdown: $('editor').value });
      setStatus(`Đã duyệt. Tool ghi nhận ${res.learned.length} bài học từ các quyết định và chỗ bạn sửa.`, 'ok');
      await load();
    } catch (err) { setStatus(err.message, 'error'); }
    $('approve').disabled = false;
  });
  load();
})();
</script>
</body>
</html>
"""
