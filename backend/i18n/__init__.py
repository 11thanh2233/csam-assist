"""Đa ngôn ngữ: mã nguồn viết tiếng Việt, bộ dịch tiếng Anh ở en.json.

- tr(text, lang): dịch một câu (khớp nguyên câu, khớp mẫu có chỗ biến {0}, rồi tách dòng/câu/ô bảng nếu cần).
- Ngôn ngữ của yêu cầu hiện tại: set_lang()/get_lang() (backend đặt theo header X-Lang của panel).
- error_code(text, status): mã lỗi kỹ thuật cố định (E_...), không phụ thuộc ngôn ngữ.
- EnglishLogFilter: nhật ký luôn ghi tiếng Anh kỹ thuật.

Bộ máy dịch giống hệt bản JavaScript trong i18n.js (panel và bảng điều khiển dùng chung cách khớp).
Thêm chuỗi tiếng Việt mới vào mã thì phải thêm bản dịch (test kiểm bằng scripts/i18n_extract.py).
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
VI = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
LANGS = ("vi", "en")
_PH = re.compile(r"(?<!\{)\{(\d+)\}(?!\})")
_lang: contextvars.ContextVar[str] = contextvars.ContextVar("sam3_lang", default="vi")

DEFAULT_CODES = {400: "E_BAD_REQUEST", 401: "E_AUTH", 403: "E_FORBIDDEN", 404: "E_NOT_FOUND", 409: "E_CONFLICT",
                 413: "E_TOO_LARGE", 422: "E_VALIDATION", 429: "E_BUSY", 500: "E_INTERNAL", 503: "E_UNAVAILABLE"}


def norm_lang(value: Optional[str]) -> str:
    v = (value or "").strip().lower()[:2]
    return v if v in LANGS else "vi"


def set_lang(value: Optional[str]) -> contextvars.Token:
    return _lang.set(norm_lang(value))


def get_lang() -> str:
    return _lang.get()


def _literal(part: str) -> str:
    return part.replace("{{", "{").replace("}}", "}")


class Catalog:
    def __init__(self, data: dict):
        self.exact: dict[str, str] = {}
        self.codes: dict[str, str] = {}
        self.templates: list[tuple] = []   # (chữ cố định dài nhất, regex, en, code, thứ tự chỗ biến)
        self.html_pairs = [tuple(p) for p in data.get("html_pairs", [])]
        codes = data.get("codes", {})
        for vi, en in data.get("strings", {}).items():
            code = codes.get(vi)
            if _PH.search(vi):
                pieces = _PH.split(vi)                 # chữ, số, chữ, số, ..., chữ
                lits = [_literal(p) for p in pieces[0::2]]
                if not any(VI.search(x) for x in lits):
                    continue
                pattern = "".join(re.sub(r"(\\\s)+|\s+", r"\\s*", re.escape(lit)) + ("(.*?)" if i < len(lits) - 1 else "") for i, lit in enumerate(lits))
                idx = [int(n) for n in pieces[1::2]]
                words = tuple(sorted({w for lit in lits for w in lit.split()}, key=len, reverse=True))
                self.templates.append((words, re.compile(rf"^\s*{pattern}\s*$", re.S), en, code, idx, "\n" in vi))
            else:
                key = _literal(vi).strip() if "{{" in vi or "}}" in vi else vi.strip()
                self.exact[key] = (_literal(en) if "{{" in vi else en).strip()
                if code:
                    self.codes[key] = code
        # mẫu cụ thể (nhiều chữ cố định) thử trước
        self.templates.sort(key=lambda t: -sum(len(x) for x in t[1].pattern.split("(.*?)")))


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    return Catalog(json.loads((HERE / "en.json").read_text(encoding="utf-8")))


def _fill(en: str, idx: list[int], groups: tuple, depth: int) -> tuple[str, bool]:
    vals = {n: g for n, g in zip(idx, groups)}
    clean = True
    out_vals = {}
    for n, g in vals.items():
        t = _tr(g, depth + 1)
        if VI.search(t):
            clean = False
        out_vals[n] = t
    text = _PH.sub(lambda m: out_vals.get(int(m.group(1)), m.group(0)), en)
    return text.replace("{{", "{").replace("}}", "}"), clean


def _match(core: str, depth: int) -> tuple[Optional[str], bool]:
    if core in _MMEMO:
        return _MMEMO[core]
    res = _match_raw(core, depth)
    _MMEMO[core] = res
    return res


def _match_raw(core: str, depth: int) -> tuple[Optional[str], bool]:
    cat = catalog()
    if core in cat.exact:
        return cat.exact[core], True
    low = core[:1].lower() + core[1:]
    if low != core and low in cat.exact:          # cùng câu nhưng viết hoa chữ đầu
        en = cat.exact[low]
        return en[:1].upper() + en[1:], True
    partial = None
    multi = "\n" in core
    for words, rx, en, _code, idx, nl in cat.templates:
        if multi and not nl:                        # văn bản nhiều dòng: chỉ mẫu nhiều dòng, còn lại tách dòng
            continue
        if not all(w in core for w in words):       # lọc nhanh: mọi chữ cố định của mẫu phải có mặt
            continue
        m = rx.match(core)
        if not m:
            continue
        text, clean = _fill(en, idx, m.groups(), depth)
        if clean:
            return text.strip(), True
        if partial is None:
            partial = text.strip()
    return partial, False


_SENT = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r"(?<=[:;])\s+")
_BOUND = re.compile(r"(?<=[.!?:;])(\s+)")
_COMMA = re.compile(r",\s+")
_SUFFIX = re.compile(r"^(.*?)(\s*(?:<!--.*?-->|\*\([^)]*\)\*))$", re.S)
_PAREN = re.compile(r"\(([^()]*)\)")
_CODE_TAIL = re.compile(r"^(.*?)(\s*\(E_[A-Z0-9_]+\))$", re.S)
_MD = re.compile(r"^(\s*(?:#{1,6}\s+|[-*]\s+(?:\[[ x]\]\s+)?|>\s?|\d+\.\s+)?)(.*?)(\s*)$", re.S)


_MEMO: dict[str, str] = {}
_MMEMO: dict[str, tuple] = {}


def _tr(text: str, depth: int = 0) -> str:
    if not text or not VI.search(text) or depth > 40:
        return text
    if text in _MEMO:
        return _MEMO[text]
    if len(_MEMO) > 50000:
        _MEMO.clear()
        _MMEMO.clear()
    out = _tr_raw(text, depth)
    _MEMO[text] = out
    return out


def _tr_raw(text: str, depth: int) -> str:
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    core = text.strip()
    cm = _CODE_TAIL.match(core)                    # giữ nguyên mã lỗi ở cuối: "... (E_GPU_OOM)"
    if cm and cm.group(1):
        return lead + _tr(cm.group(1), depth) + cm.group(2) + trail
    hit, clean = _match(core, depth)
    if clean:
        return lead + hit + trail
    best = hit
    if "\n" in core:
        flat_hit, flat_clean = _match(" ".join(core.split()), depth)
        if flat_clean:
            return lead + flat_hit + trail
        return lead + "\n".join(_tr(line, depth + 1) for line in core.split("\n")) + trail
    seg = _segment(core, depth)
    if best is None or len(VI.findall(seg)) < len(VI.findall(best)):
        best = seg                                 # mẫu khớp một phần (còn dữ liệu người dùng) hay tách đoạn: lấy bản ít tiếng Việt hơn
    return lead + best + trail


def _spans(core: str, depth: int) -> Optional[str]:
    """Tách ở ranh giới câu/vế (. ! ? : ;), rồi ghép lại đoạn liền nhau dài nhất có trong bộ dịch.

    Ví dụ: một luật hai câu của guideline mẫu nằm giữa dòng "**car / truck**: <luật>. Ví dụ ở frame: 3." """
    pieces = _BOUND.split(core)                    # chữ, khoảng trắng, chữ, ...
    texts, seps = pieces[0::2], pieces[1::2] + [""]
    n = len(texts)
    if n < 2:
        return None
    out, i = [], 0
    while i < n:
        for j in range(min(n, i + 8), i, -1):        # đoạn ghép tối đa 8 câu/vế
            cand = "".join(texts[k] + (seps[k] if k < j - 1 else "") for k in range(i, j))
            if j - i == 1:
                out.append(_tr(cand, depth + 1) + seps[j - 1])
                break
            hit, clean = _match(cand.strip(), depth)
            if clean:
                out.append(hit + seps[j - 1])
                break
        i = j
    return "".join(out)


def _segment(core: str, depth: int) -> str:
    """Không khớp nguyên câu: tách theo cấu trúc (đầu dòng Markdown, đuôi chú thích, ô bảng, câu, vế, danh sách, ngoặc)."""
    m = _MD.match(core)                            # "- ", "## ", "1. ", "- [ ] " ... giữ nguyên
    if m and m.group(1) and m.group(2):
        return m.group(1) + _tr(m.group(2), depth + 1) + m.group(3)
    sm = _SUFFIX.match(core)                       # đuôi " *(đề xuất, chưa chốt)*", " <!--r:...-->" dịch riêng
    if sm and sm.group(1).strip():
        return _tr(sm.group(1), depth + 1) + _tr(sm.group(2), depth + 1)
    if core.startswith("|") and core.endswith("|") and core.count("|") >= 3:
        return "|" + "|".join(_tr(c, depth + 1) for c in core[1:-1].split("|")) + "|"
    out = _spans(core, depth)
    if out is None:
        parts = _COMMA.split(core)                 # danh sách cách nhau bằng dấu phẩy
        out = ", ".join(_tr(p, depth + 1) for p in parts) if len(parts) > 1 else core

    def paren(mm):                                 # chữ trong ngoặc: "(guideline mẫu)", "(không dùng 15 phút)"
        inner = _tr(mm.group(1), depth + 1)
        return "(" + (inner if not VI.search(inner) else mm.group(1)) + ")"

    return _PAREN.sub(paren, out) if VI.search(out) else out


@lru_cache(maxsize=8192)
def _tr_cached(text: str) -> str:
    return _tr(text)


def tr(text: Any, lang: Optional[str] = None) -> Any:
    """Dịch text sang ngôn ngữ lang (mặc định: ngôn ngữ của yêu cầu hiện tại). Không phải chuỗi thì trả nguyên."""
    if not isinstance(text, str) or norm_lang(lang or get_lang()) != "en":
        return text
    return _tr_cached(text) if len(text) < 4000 else _tr(text)


def T(text: str) -> str:
    """Như tr() với ngôn ngữ hiện tại; dùng cho câu gửi LLM để LLM trả lời đúng ngôn ngữ người dùng."""
    return tr(text)


# Giá trị là mã/định danh/dữ liệu người dùng: không dịch (panel gửi lại cho backend để so khớp)
SKIP_KEYS = frozenset({"id", "key", "name", "label", "labels_used", "url", "path", "file", "prompt", "prompts", "code",
                       "value", "profile_id", "run_id", "answer", "recommended", "markdown_source", "token", "title_raw",
                       "site", "dir", "data_dir", "sync_dir", "scan_dirs", "folder", "username", "user"})


def translate_obj(obj: Any, lang: Optional[str] = None, _depth: int = 0) -> Any:
    """Dịch mọi chuỗi trong dữ liệu JSON (dict/list). Khoá và giá trị định danh (SKIP_KEYS) giữ nguyên."""
    if norm_lang(lang or get_lang()) != "en" or _depth > 40:
        return obj
    if isinstance(obj, str):
        return tr(obj, "en")
    if isinstance(obj, list):
        return [translate_obj(v, "en", _depth + 1) for v in obj]
    if isinstance(obj, dict):
        return {k: (v if k in SKIP_KEYS else translate_obj(v, "en", _depth + 1)) for k, v in obj.items()}
    return obj


def translate_page(html: str, lang: Optional[str] = None) -> str:
    """Trang HTML viết sẵn (ví dụ trang duyệt guideline): thay từng cụm theo html_pairs."""
    if norm_lang(lang or get_lang()) != "en":
        return html
    for a, b in catalog().html_pairs:
        html = html.replace(a, b)
    return html


def error_code(text: Any, status: int = 500) -> str:
    """Mã lỗi cố định cho một thông báo (tiếng Việt gốc hoặc đã dịch), hoặc mã theo HTTP status."""
    if isinstance(text, str) and text.strip():
        core = text.strip()
        tail = _CODE_TAIL.match(core)
        if tail:                                    # thông báo đã kèm mã
            return tail.group(2).strip()[1:-1]
        cat = catalog()
        if core in cat.codes:
            return cat.codes[core]
        for words, rx, _en, code, _idx, _nl in cat.templates:
            if code and all(w in core for w in words) and rx.match(core):
                return code
        # câu ghép: lấy mã của câu đầu tiên có mã
        for part in _SENT.split(core):
            if part.strip() in cat.codes:
                return cat.codes[part.strip()]
    return DEFAULT_CODES.get(int(status or 500), f"E_HTTP_{status}")


def with_code(message: str, code: str) -> str:
    """Thông báo kèm mã: 'Không kết nối được backend… (E_BACKEND_UNREACHABLE)'."""
    return f"{message} ({code})" if code and code not in message else message


class EnglishLogFilter(logging.Filter):
    """Ghi nhật ký bằng tiếng Anh: dịch chuỗi định dạng (%s) nếu có trong bộ dịch, không thì dịch cả câu đã ghép."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg
        if isinstance(msg, str) and VI.search(msg):
            en = tr(msg, "en")
            if en != msg:
                record.msg = en
            elif record.args:
                try:
                    record.msg, record.args = tr(msg % record.args, "en"), ()
                except (TypeError, ValueError):
                    pass
        if record.args:
            record.args = tuple(tr(a, "en") if isinstance(a, str) else a for a in record.args) \
                if isinstance(record.args, tuple) else record.args
        return True


def install_log_filter(logger: Optional[logging.Logger] = None) -> None:
    """Gắn bộ lọc vào mọi handler của logger (mặc định: root) để file log luôn là tiếng Anh."""
    lg = logger or logging.getLogger()
    for h in lg.handlers:
        if not any(isinstance(f, EnglishLogFilter) for f in h.filters):
            h.addFilter(EnglishLogFilter())


def catalog_for(sources: tuple[str, ...]) -> dict:
    """Phần bộ dịch cho giao diện web (bảng điều khiển). sources: tên file mã nguồn; rỗng = tất cả."""
    data = json.loads((HERE / "en.json").read_text(encoding="utf-8"))
    return {"strings": data.get("strings", {}), "codes": data.get("codes", {})}
