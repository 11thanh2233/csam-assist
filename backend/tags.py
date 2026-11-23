"""Tag ảnh (label kiểu Tag của CVAT): gợi ý giá trị cho từng thuộc tính của tag (0.9.4).

Chỉ đoán những gì đo được từ chính ảnh, không cần model:
  - modality: ảnh hồng ngoại (gần như không có màu) hay ảnh màu;
  - timeofday / light: theo độ sáng trung bình (luôn nhắc bạn kiểm, nhất là lúc chạng vạng);
  - camera: ảnh fisheye (vòng ảnh giữa bốn góc tối) hay camera thường. Hướng của fisheye bạn chọn;
  - camera_blocked: ảnh gần như một màu.
Các thuộc tính khác (weather, source...) tool để giá trị mặc định và đánh dấu "bạn chọn".
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .pipeline import _pick_value, fisheye_circle
from .profiles import norm


def _gray(image: np.ndarray) -> np.ndarray:
    return image.mean(axis=2) if image.ndim == 3 else image.astype(float)


def is_infrared(image: np.ndarray) -> bool:
    if image.ndim != 3:
        return True
    small = image[::4, ::4].astype(float)
    spread = np.abs(small[..., 0] - small[..., 1]).mean() + np.abs(small[..., 1] - small[..., 2]).mean()
    return spread < 6.0


def brightness(image: np.ndarray) -> float:
    gray = _gray(image[::4, ::4])
    circle = fisheye_circle(image)
    if circle:   # fisheye: chỉ tính trong vòng ảnh, bỏ bốn góc đen
        h, w = gray.shape
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy, r = circle[0] / 4, circle[1] / 4, circle[2] / 4
        inside = (xx - cx) ** 2 + (yy - cy) ** 2 <= (0.9 * r) ** 2
        if inside.any():
            return float(gray[inside].mean())
    return float(gray.mean())


def _value(rule: str, values: list[str], image: np.ndarray, cache: dict) -> tuple[Optional[str], str]:
    """(giá trị, ghi chú). Giá trị None = tool không đoán, bạn chọn."""
    if rule == "modality":
        if "ir" not in cache:
            cache["ir"] = is_infrared(image)
        return _pick_value(values, "ir" if cache["ir"] else "rgb"), ""
    if rule == "timeofday":
        if "light" not in cache:
            cache["light"] = brightness(image)
        luma = cache["light"]
        if cache.get("ir") and _pick_value(values, "unknown"):
            return None, "ảnh hồng ngoại: độ sáng không cho biết ngày hay đêm"
        want = "day" if luma >= 95 else ("dusk" if luma >= 55 and _pick_value(values, "dusk") else "night")
        return _pick_value(values, want), f"độ sáng trung bình {luma:.0f}/255"
    if rule == "camera":
        if "fisheye" not in cache:
            cache["fisheye"] = fisheye_circle(image) is not None
        if cache["fisheye"]:
            return None, "ảnh fisheye: chọn hướng camera"
        front = next((v for v in values if norm(v) in ("front", "camera front", "front camera")), None)
        return front, ""
    if rule == "blocked":
        std = float(_gray(image[::4, ::4]).std())
        return _pick_value(values, "true" if std < 8 else "false"), ""
    return None, ""


def suggest(image: np.ndarray, cfgs: list[dict]) -> list[dict]:
    out, cache = [], {}
    for cfg in cfgs:
        rules = cfg.get("tag_rules") or {}
        attrs, auto, review, notes = {}, [], [], {}
        for attr in cfg.get("label_attributes") or []:
            name = attr["name"]
            values = [str(v) for v in attr.get("values") or []]
            rule = rules.get(name, "manual")
            value, note = _value(rule, values, image, cache) if rule != "manual" else (None, "")
            if note:
                notes[name] = note
            if value is not None:
                attrs[norm(name)] = value
                auto.append(name)
            else:
                review.append(name)
        out.append({"label_id": cfg["label_id"], "label_name": cfg["name"], "attributes": attrs, "auto": auto,
                    "review": review, "notes": notes})
    return out
