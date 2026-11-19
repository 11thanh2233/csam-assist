"""Tìm mọi chuỗi tiếng Việt trong mã nguồn và đối chiếu với bộ dịch tiếng Anh (backend/i18n/en.json).

Dùng:
  python scripts/i18n_extract.py             # in thống kê, số chuỗi chưa dịch
  python scripts/i18n_extract.py --missing   # in JSON các chuỗi chưa dịch (để dịch rồi thêm vào en.json)
  python scripts/i18n_extract.py --unused    # chuỗi có trong en.json nhưng không còn trong mã

Quét: backend/**/*.py, desktop/**/*.py (bỏ docstring), userscript, desktop/shim.js, desktop/ui/control.html.
Phần JavaScript cần Node + acorn (npm install --no-save acorn acorn-walk); thiếu thì bỏ qua phần JS và báo.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "backend" / "i18n" / "en.json"
VI = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
PY_DIRS = ["backend", "desktop"]
JS_FILES = ["userscript/sam3-cvat-assist.user.js", "desktop/shim.js", "desktop/ui/control.html"]
# Dữ liệu đi kèm tool có chữ hiện cho người dùng (tên profile, mẫu luật của guideline)
YAML_GLOBS = ["profiles/*.yaml", "guideline_library/knowledge.yaml"]


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _fstring(node: ast.JoinedStr) -> tuple[str, bool]:
    text, n = "", 0
    for part in node.values:
        if isinstance(part, ast.Constant):
            text += str(part.value).replace("{", "{{").replace("}", "}}")
        else:
            text += "{%d}" % n
            n += 1
    return text, n > 0


def scan_python(path: Path) -> list[dict]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstring_ids(tree)
    log_args: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS \
                and node.args:
            log_args.add(id(node.args[0]))
    out, inside_f = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                inside_f.add(id(part))
    # Nối chuỗi bằng "+" được gộp thành một mẫu {0}
    def is_str(n):
        return isinstance(n, ast.JoinedStr) or (isinstance(n, ast.Constant) and isinstance(n.value, str))

    def has_str(n):
        return is_str(n) or (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and (has_str(n.left) or has_str(n.right)))

    def flat(n, acc):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and has_str(n):
            flat(n.left, acc)
            flat(n.right, acc)
        else:
            acc.append(n)

    chains = [n for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and has_str(n)]
    inner = {id(c.left) for c in chains} | {id(c.right) for c in chains}
    for c in chains:
        if id(c) in inner:
            continue
        parts: list = []
        flat(c, parts)
        text, k, vi = "", 0, False
        for p in parts:
            if isinstance(p, ast.Constant) and isinstance(p.value, str):
                text += p.value.replace("{", "{{").replace("}", "}}")
                vi = vi or bool(VI.search(p.value))
                inside_f.add(id(p))
            elif isinstance(p, ast.JoinedStr):
                t, _ = _fstring(p)
                n_ph = t.count("{") - 2 * t.count("{{")
                t = re.sub(r"(?<!\{)\{(\d+)\}(?!\})", lambda m: "{%d}" % (int(m.group(1)) + k), t)
                k += n_ph
                text += t
                vi = vi or bool(VI.search(t))
                inside_f.add(id(p))
                for part in p.values:
                    inside_f.add(id(part))
            else:
                text += "{%d}" % k
                k += 1
        if vi:
            out.append({"text": text, "kind": "template" if k else "exact", "line": c.lineno, "log": id(c) in log_args})
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and id(node) not in inside_f:
            text, tmpl = _fstring(node)
            if VI.search(text):
                out.append({"text": text, "kind": "template" if tmpl else "exact", "line": node.lineno,
                            "log": id(node) in log_args})
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                and id(node) not in inside_f and VI.search(node.value):
            out.append({"text": node.value, "kind": "exact", "line": node.lineno, "log": id(node) in log_args})
    return out


class _HtmlText(HTMLParser):
    ATTRS = {"title", "placeholder", "aria-label", "alt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for k, v in attrs:
            if k in self.ATTRS and v and VI.search(v):
                self.items.append({"text": v.strip(), "kind": "exact", "line": self.getpos()[0]})

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        t = " ".join(data.split())
        if not self.skip and t and VI.search(t):
            self.items.append({"text": t, "kind": "exact", "line": self.getpos()[0]})


def scan_js(path: Path) -> list[dict] | None:
    node = shutil.which("node")
    if not node:
        return None
    r = subprocess.run([node, str(ROOT / "scripts" / "i18n_js.cjs"), str(path)], capture_output=True, text=True,
                       encoding="utf-8")
    if r.returncode != 0:
        if "Cannot find module" in r.stderr:
            return None
        raise RuntimeError(f"{path}: {r.stderr.strip()}")
    items = json.loads(r.stdout)
    if path.suffix == ".html":
        p = _HtmlText()
        p.feed(path.read_text(encoding="utf-8"))
        items += p.items
    return items


def scan_yaml(path: Path) -> list[dict]:
    import yaml

    out: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and VI.search(o):
            out.append({"text": o.strip(), "kind": "exact", "line": 0})

    walk(yaml.safe_load(path.read_text(encoding="utf-8")))
    return out


def collect() -> tuple[dict[str, dict], list[str]]:
    found: dict[str, dict] = {}
    skipped: list[str] = []

    def add(item, src):
        e = found.setdefault(item["text"], {"kind": item["kind"], "sources": [], "log": False})
        e["sources"].append(f"{src}:{item['line']}")
        e["log"] = e["log"] or item.get("log", False)

    for d in PY_DIRS:
        for f in sorted((ROOT / d).rglob("*.py")):
            if "i18n" in f.relative_to(ROOT).parts:      # chính bộ máy dịch
                continue
            for it in scan_python(f):
                add(it, f.relative_to(ROOT).as_posix())
    for pattern in YAML_GLOBS:
        for f in sorted(ROOT.glob(pattern)):
            for it in scan_yaml(f):
                add(it, f.relative_to(ROOT).as_posix())
    for rel in JS_FILES:
        items = scan_js(ROOT / rel)
        if items is None:
            skipped.append(rel)
            continue
        for it in items:
            add(it, rel)
    return found, skipped


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {"strings": {}, "codes": {}}


def main() -> int:
    found, skipped = collect()
    cat = load_catalog()["strings"]
    missing = {k: v for k, v in found.items() if k not in cat}
    if "--missing" in sys.argv:
        print(json.dumps({k: {"kind": v["kind"], "src": v["sources"][0], "log": v["log"]} for k, v in missing.items()},
                         ensure_ascii=False, indent=1))
        return 0
    if "--unused" in sys.argv:
        print(json.dumps(sorted(set(cat) - set(found)), ensure_ascii=False, indent=1))
        return 0
    print(f"Chuỗi tiếng Việt trong mã: {len(found)} · đã dịch: {len(found) - len(missing)} · chưa dịch: {len(missing)}")
    if skipped:
        print("Bỏ qua (thiếu node/acorn):", ", ".join(skipped))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
