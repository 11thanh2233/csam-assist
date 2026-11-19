"""Nhúng bộ dịch tiếng Anh + bộ máy dịch vào userscript và bảng điều khiển của app.

Mỗi file chỉ nhận phần bộ dịch của chính nó (chuỗi tìm thấy trong file đó), chèn giữa hai dòng đánh dấu
"<i18n-catalog>" và "</i18n-catalog>".

  python scripts/i18n_build.py           # cập nhật
  python scripts/i18n_build.py --check   # chỉ kiểm, lệch thì trả mã 1 (test dùng)
  python scripts/i18n_build.py --prune   # bỏ bản dịch không còn dùng trong mã khỏi en.json

Cần Node + acorn như scripts/i18n_extract.py.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import i18n_extract as ex  # noqa: E402

ROOT = ex.ROOT
ENGINE = ROOT / "backend" / "i18n" / "i18n.js"
TARGETS = {
    "userscript/sam3-cvat-assist.user.js": ("userscript/sam3-cvat-assist.user.js",),
    "desktop/ui/control.html": ("desktop/ui/control.html",),
}
MARK = re.compile(r"(?P<head>[ \t]*// <i18n-catalog>[^\n]*\n)(?P<body>.*?)(?P<tail>[ \t]*// </i18n-catalog>)", re.S)


def build(check: bool) -> int:
    found, skipped = ex.collect()
    if skipped:
        print("Thiếu node/acorn, không dựng được:", ", ".join(skipped))
        return 2
    cat = ex.load_catalog()
    engine = ENGINE.read_text(encoding="utf-8").strip()
    bad = 0
    for target, sources in TARGETS.items():
        keys = sorted(k for k, v in found.items() if any(s.split(":")[0] in sources for s in v["sources"]))
        missing = [k for k in keys if k not in cat["strings"]]
        if missing:
            print(f"{target}: {len(missing)} chuỗi chưa dịch (chạy scripts/i18n_extract.py --missing)")
            bad = 1
        data = {"strings": {k: cat["strings"][k] for k in keys if k in cat["strings"]},
                "codes": {k: cat["codes"][k] for k in keys if k in cat.get("codes", {})}}
        body = ("  const I18N_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                + ";\n" + "\n".join("  " + line if line else "" for line in engine.splitlines()) + "\n")
        path = ROOT / target
        text = path.read_text(encoding="utf-8")
        m = MARK.search(text)
        if not m:
            print(f"{target}: thiếu dòng đánh dấu <i18n-catalog>")
            return 2
        if m.group("body") == body:
            continue
        if check:
            print(f"{target}: bộ dịch nhúng đã cũ, chạy python scripts/i18n_build.py")
            bad = 1
            continue
        path.write_text(text[: m.start("body")] + body + text[m.end("body"):], encoding="utf-8")
        print(f"{target}: đã cập nhật ({len(keys)} chuỗi)")
    return bad


def prune() -> int:
    found, skipped = ex.collect()
    if skipped:
        print("Thiếu node/acorn:", ", ".join(skipped))
        return 2
    cat = ex.load_catalog()
    unused = [k for k in cat["strings"] if k not in found]
    for k in unused:
        cat["strings"].pop(k, None)
        cat.get("codes", {}).pop(k, None)
    ex.CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Đã bỏ {len(unused)} bản dịch không còn dùng.")
    return 0


if __name__ == "__main__":
    if "--prune" in sys.argv:
        sys.exit(prune())
    sys.exit(build("--check" in sys.argv))
