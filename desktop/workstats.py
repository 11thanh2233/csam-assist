"""Thống kê công việc cho trang Tổng quan (0.9.1).

Panel đếm theo ngày (userscript: bumpStats), lưu ở khoá "stats" trong bộ nhớ của panel (thư mục store).
Ở đây chỉ đọc ra để hiện trên bảng điều khiển và xuất CSV; không gửi đi đâu.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

EMPTY = {"applied": 0, "rejected": 0, "shown": 0, "frames": [], "saved": 0, "jobs": {}}


def load(store_dir: Path) -> dict:
    f = Path(store_dir) / (hashlib.sha1(b"stats").hexdigest() + ".json")
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
        value = rec.get("value") if rec.get("key") == "stats" else None
    except (OSError, ValueError, AttributeError):
        return {}
    return value if isinstance(value, dict) else {}


def _day(stats: dict, day: str) -> dict:
    d = stats.get(day)
    return {**EMPTY, **d} if isinstance(d, dict) else dict(EMPTY)


def summary(stats: dict, today: dt.date | None = None, days: int = 7) -> dict:
    """Hôm nay và 7 ngày gần nhất: nhãn đã ghi, frame, phút tiết kiệm ước tính, tỉ lệ đề xuất được giữ."""
    today = today or dt.date.today()
    week = []
    for i in range(days - 1, -1, -1):
        day = (today - dt.timedelta(days=i)).isoformat()
        d = _day(stats, day)
        week.append({"day": day, "applied": int(d["applied"]), "minutes": round(d["saved"] / 60)})
    t = _day(stats, today.isoformat())
    shown = max(int(t["shown"]), int(t["applied"]))
    return {
        "today": {"applied": int(t["applied"]), "frames": len(t["frames"]), "minutes": round(t["saved"] / 60),
                  "kept": round(100 * t["applied"] / shown) if shown else None, "jobs": len(t["jobs"])},
        "week": week,
        "week_applied": sum(w["applied"] for w in week),
        "week_minutes": sum(w["minutes"] for w in week),
        "has_data": any(isinstance(v, dict) and v.get("applied") for v in stats.values()),
    }


def csv_text(stats: dict) -> str:
    """Cùng cột với nút "Xuất báo cáo" trong panel, để gộp được với nhau."""
    rows = [["ngay", "job", "nhan_da_ghi", "de_xuat_bo", "de_xuat_da_hien", "frame_da_lam_ca_ngay", "phut_tiet_kiem_uoc_tinh_ca_ngay"]]
    for day in sorted(k for k, v in stats.items() if isinstance(v, dict)):
        d = _day(stats, day)
        frames, minutes = len(d["frames"]), round(d["saved"] / 60)
        jobs = sorted((d["jobs"] or {}).items())
        if not jobs:
            rows.append([day, "", d["applied"], d["rejected"], d["shown"], frames, minutes])
        for job, j in jobs:
            rows.append([day, job, j.get("applied", 0), j.get("rejected", 0), j.get("shown", 0), frames, minutes])
    body = "\r\n".join(",".join('"' + str(v).replace('"', '""') + '"' for v in r) for r in rows)
    return "﻿" + body
