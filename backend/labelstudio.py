"""Đầu nối Label Studio: backend này đóng vai "ML backend" của Label Studio để gợi ý nhãn (pre-annotation).

Cách nối (Label Studio → Settings → Model → Connect Model):
  URL:  http://<máy chạy backend>:8765/ls
  Basic auth (nếu backend đặt SAM3_API_KEY): user bất kỳ, password = SAM3_API_KEY
Ảnh trong task ở dạng /data/upload/...: đặt LABEL_STUDIO_URL và LABEL_STUDIO_API_KEY cho backend để tải được ảnh.

Hỗ trợ <RectangleLabels> và <PolygonLabels> (các label khác bị bỏ qua và báo trong log).
"""
from __future__ import annotations

import base64
import logging
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger("sam3-assist")
SUPPORTED = {"RectangleLabels": "rectangle", "PolygonLabels": "polygon"}


def parse_config(xml: str) -> dict:
    """Từ label config của Label Studio: các tag gán nhãn được hỗ trợ, tên ảnh, danh sách label."""
    root = ET.fromstring(xml)
    images = {el.get("name"): el.get("value", "").lstrip("$") for el in root.iter("Image")}
    controls, labels, next_id = [], [], 1
    for el in root.iter():
        kind = SUPPORTED.get(el.tag)
        if not kind:
            continue
        to = el.get("toName")
        ctl = {"from_name": el.get("name"), "to_name": to, "type": el.tag.lower(), "shape": kind, "data_key": images.get(to, "image"), "labels": []}
        for lab in el.iter("Label"):
            value = lab.get("value")
            if not value:
                continue
            labels.append({"id": next_id, "name": value, "type": kind, "control": ctl["from_name"]})
            ctl["labels"].append(next_id)
            next_id += 1
        controls.append(ctl)
    return {"controls": controls, "labels": labels}


def fetch_image(url: str) -> np.ndarray:
    """Tải ảnh của task: URL đầy đủ, đường dẫn /data/... của Label Studio, hoặc data: URI."""
    if url.startswith("data:"):
        raw = base64.b64decode(url.split(",", 1)[1])
    else:
        ls_url = os.getenv("LABEL_STUDIO_URL", "").rstrip("/")
        if url.startswith("/"):
            if not ls_url:
                raise ValueError("Ảnh ở dạng /data/...: đặt LABEL_STUDIO_URL (và LABEL_STUDIO_API_KEY) cho backend.")
            url = ls_url + url
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https"):
            raise ValueError("Chỉ tải ảnh qua http(s).")
        headers = {}
        token = os.getenv("LABEL_STUDIO_API_KEY", "")
        if token and ls_url and url.startswith(ls_url):           # token chỉ gửi cho chính Label Studio
            headers["Authorization"] = f"Token {token}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
            raw = r.read()
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Không đọc được ảnh của task.")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def to_results(proposals: list[dict], config: dict, width: int, height: int) -> list[dict]:
    """Đề xuất của tool -> kết quả theo định dạng Label Studio (toạ độ phần trăm)."""
    by_id = {l["id"]: l for l in config["labels"]}
    ctl = {c["from_name"]: c for c in config["controls"]}
    out = []
    for i, p in enumerate(proposals):
        lab = by_id.get(p["label_id"])
        if not lab or not p.get("checked", True):
            continue
        c = ctl[lab["control"]]
        common = {"id": f"sam3_{i}", "from_name": c["from_name"], "to_name": c["to_name"], "type": c["type"],
                  "score": float(p.get("score", 0)), "original_width": width, "original_height": height, "image_rotation": 0}
        if p["type"] == "rectangle":
            x1, y1, x2, y2 = p["points"]
            value = {"x": 100 * x1 / width, "y": 100 * y1 / height, "width": 100 * (x2 - x1) / width, "height": 100 * (y2 - y1) / height,
                     "rotation": 0, "rectanglelabels": [lab["name"]]}
        elif p["type"] == "polygon":
            pts = p["points"]
            value = {"points": [[100 * pts[j] / width, 100 * pts[j + 1] / height] for j in range(0, len(pts) - 1, 2)],
                     "polygonlabels": [lab["name"]], "closed": True}
        else:
            continue
        out.append(dict(common, value=value))
    return out


def predict(tasks: list[dict], label_config: str, propose: Callable[[np.ndarray, list[dict]], dict]) -> list[dict]:
    """Gợi ý cho từng task. `propose(ảnh, labels)` là hàm gợi ý của backend (cùng profile, cùng tự học)."""
    config = parse_config(label_config)
    if not config["labels"]:
        raise ValueError("Label config không có RectangleLabels/PolygonLabels nào.")
    preds = []
    for task in tasks:
        data = task.get("data") or {}
        key = config["controls"][0]["data_key"]
        try:
            image = fetch_image(str(data.get(key) or next(iter(data.values()), "")))
            res = propose(image, config["labels"])
            results = to_results(res["proposals"], config, res["width"], res["height"])
            score = float(np.mean([r["score"] for r in results])) if results else 0.0
            preds.append({"result": results, "score": score, "model_version": res.get("model_version", "sam3-assist")})
        except Exception as exc:  # noqa: BLE001 - một task lỗi không làm hỏng cả lô
            log.warning("Label Studio: task %s lỗi: %s", task.get("id"), exc)
            preds.append({"result": [], "score": 0.0, "model_version": "sam3-assist"})
    return preds


__all__ = ["parse_config", "fetch_image", "to_results", "predict"]
