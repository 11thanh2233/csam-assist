"""Tag ảnh (label kiểu Tag của CVAT): gợi ý giá trị cho từng thuộc tính của tag (0.9.4).

Chỉ đoán những gì đo được từ chính ảnh, không cần model:
  - modality: ảnh hồng ngoại (gần như không có màu) hay ảnh màu;
  - timeofday / light: theo độ sáng trung bình (luôn nhắc bạn kiểm, nhất là lúc chạng vạng);
  - camera: ảnh fisheye (vòng ảnh giữa bốn góc tối) hay camera thường. Hướng của fisheye bạn chọn;
  - camera_blocked: ảnh gần như một màu.
Các thuộc tính khác (weather, source...) tool để giá trị mặc định và đánh dấu "bạn chọn".
"""
from __future__ import annotations

from typing import Callable, Optional

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
    return bool(spread < 6.0)


def fisheye_like(image: np.ndarray) -> bool:
    """Ảnh fisheye (0.9.5): vòng ảnh tròn, ít nhất hai góc tối hẳn. Trước đây đòi cả bốn góc tối nên bỏ sót ảnh
    camera gương / sau có thân xe sáng ở hai góc dưới."""
    if fisheye_circle(image) is not None:
        return True
    h, w = image.shape[:2]
    gray = _gray(image[::2, ::2])
    gh, gw = gray.shape
    k = max(4, min(gh, gw) // 20)
    corners = [gray[:k, :k], gray[:k, -k:], gray[-k:, :k], gray[-k:, -k:]]
    dark = sum(1 for c in corners if float(c.mean()) < 30)
    center = float(gray[gh // 3: 2 * gh // 3, gw // 3: 2 * gw // 3].mean())
    if dark < 2 or center < 35:
        return False
    # mép trái/phải ở giữa chiều cao vẫn sáng (ảnh tròn chạm mép), còn góc thì tối: dấu hiệu vòng ảnh fisheye
    edge = float(np.concatenate([gray[gh // 3: 2 * gh // 3, :k].ravel(), gray[gh // 3: 2 * gh // 3, -k:].ravel()]).mean())
    corner_mean = float(np.mean([c.mean() for c in corners]))
    return bool(edge > corner_mean + 15)


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
            cache["fisheye"] = fisheye_like(image)
        if cache["fisheye"]:
            return None, "ảnh fisheye: chọn hướng camera"
        front = next((v for v in values if norm(v) in ("front", "camera front", "front camera")), None)
        return front, ""
    if rule == "blocked":
        std = float(_gray(image[::4, ::4]).std())
        return _pick_value(values, "true" if std < 8 else "false"), ""
    return None, ""


def suggest(image: np.ndarray, cfgs: list[dict], guess: Optional[Callable] = None, filename: str = "") -> list[dict]:
    """guess(label, attr) -> (giá trị, nguồn) | None: giá trị học từ tên file / chuỗi ảnh bạn đã chốt (0.9.5).
    Có giá trị học được thì dùng nó trước luật đo ảnh."""
    out, cache = [], {}
    for cfg in cfgs:
        rules = cfg.get("tag_rules") or {}
        attrs, auto, review, notes, learned = {}, [], [], {}, {}
        for attr in cfg.get("label_attributes") or []:
            name = attr["name"]
            values = [str(v) for v in attr.get("values") or []]
            got = guess(cfg["name"], name) if guess else None
            if got and (not values or got[0] in values):
                attrs[norm(name)] = got[0]
                auto.append(name)
                learned[name] = got[1]
                notes[name] = ("theo các frame cùng chuỗi ảnh bạn đã chốt" if got[1] == "sequence"
                               else f"học từ cách bạn chốt: tên file có '{got[1]}'")
                continue
            rule = rules.get(name) or _rule_by_name(name, values)
            value, note = _value(rule, values, image, cache) if rule != "manual" else (None, "")
            if note:
                notes[name] = note
            if value is not None:
                attrs[norm(name)] = value
                auto.append(name)
            else:
                review.append(name)
        out.append({"label_id": cfg["label_id"], "label_name": cfg["name"], "attributes": attrs, "auto": auto,
                    "review": review, "notes": notes, "learned": learned})
    return out


def _rule_by_name(name: str, values: list[str]) -> str:
    """Profile không ghi luật cho tag này: đoán luật theo tên và giá trị (vd 'camera' có giá trị fisheye_*)."""
    n, vals = norm(name), {norm(v) for v in values}
    if n in ("modality", "sensor") or {"ir", "rgb"} <= vals:
        return "modality"
    if n in ("timeofday", "time of day", "light", "lighting") or {"day", "night"} <= vals:
        return "timeofday"
    if n == "camera" or any(v.startswith("fisheye") for v in vals):
        return "camera"
    if "blocked" in n:
        return "blocked"
    return "manual"
