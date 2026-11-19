"""Đọc docs/CHANGELOG.md (đi kèm bộ cài) để hiện khung "Có gì mới" sau khi cập nhật."""
from __future__ import annotations

import re
from pathlib import Path

from .updater import parse_version

HEAD = re.compile(r"^##\s+v?(\d+\.\d+\.\d+)\s*(.*)$")


def parse(text: str) -> list[dict]:
    """[{version, title, body}] theo thứ tự trong file (mới nhất trước)."""
    out, cur = [], None
    for line in text.splitlines():
        m = HEAD.match(line)
        if m:
            cur = {"version": m.group(1), "title": m.group(2).strip(" -–()"), "lines": []}
            out.append(cur)
        elif cur is not None and not line.startswith("# "):
            cur["lines"].append(line)
    return [{"version": e["version"], "title": e["title"], "body": "\n".join(e["lines"]).strip()} for e in out]


def between(path: Path, after: str, upto: str) -> list[dict]:
    """Các bản mới hơn `after` và không mới hơn `upto` (bản đang chạy)."""
    try:
        entries = parse(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return []
    lo, hi = parse_version(after), parse_version(upto)
    return [e for e in entries if lo < parse_version(e["version"]) <= hi]


def section(path: Path, version: str) -> str:
    """Ghi chú của đúng một bản (dùng cho ghi chú Release khi build)."""
    for e in parse(Path(path).read_text(encoding="utf-8")):
        if e["version"] == version:
            return e["body"]
    return ""
