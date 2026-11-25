"""Thêm bản dịch tiếng Anh cho các chuỗi 0.9.8 (chạy một lần; như scripts/i18n_add_097.py).

Bản dịch nằm ở scripts/i18n_098.json ({tiếng Việt: tiếng Anh}).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "backend" / "i18n" / "en.json"
SOURCE = Path(__file__).with_name("i18n_098.json")


def main() -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    strings = data.setdefault("strings", {})
    added = 0
    for part in json.loads(SOURCE.read_text(encoding="utf-8")).values():
        for vi, en in part.items():
            if strings.get(vi) != en:
                strings[vi] = en
                added += 1
    CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"Đã thêm/cập nhật {added} chuỗi. Tổng: {len(strings)}.")


if __name__ == "__main__":
    main()
