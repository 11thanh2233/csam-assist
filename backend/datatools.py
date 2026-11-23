"""Dữ liệu thông minh: đặc trưng ảnh nhỏ gọn, xếp hạng frame đáng làm trước, keyframe theo chuyển động,
tìm frame bằng chữ (qua SAM3), độ đồng thuận giữa hai bộ nhãn."""
from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np

from . import geometry as G

THUMB = (64, 36)          # ảnh thu nhỏ để so chuyển động / độ khác nhau


def features(image: np.ndarray) -> dict:
    """Đặc trưng nhẹ của một frame: ảnh xám thu nhỏ (chuyển động), histogram màu HSV (khung cảnh: ngày/đêm/mưa),
    mật độ cạnh và độ sáng (độ khó)."""
    small = cv2.resize(image, THUMB, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    hsv = cv2.cvtColor(cv2.resize(image, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [4, 3, 3], [0, 180, 0, 256, 0, 256]).ravel()
    hist = hist / max(1.0, hist.sum())
    edges = cv2.Canny(cv2.cvtColor(cv2.resize(image, (320, 180)), cv2.COLOR_RGB2GRAY), 80, 160)
    return {"gray": [round(float(v), 3) for v in gray.ravel()], "hist": [round(float(v), 4) for v in hist],
            "edge": round(float(edges.mean() / 255.0), 4), "bright": round(float(gray.mean()), 4)}


def _vec(f: dict) -> np.ndarray:
    # Khung cảnh (histogram) nặng hơn bố cục (ảnh xám) khi so độ "khác lạ"
    return np.concatenate([np.asarray(f["hist"], np.float32) * 6.0, np.asarray(f["gray"], np.float32) * 0.5,
                           np.asarray([f["edge"] * 4.0, f["bright"] * 2.0], np.float32)])


def rank(frames: list[dict], k: Optional[int] = None) -> list[dict]:
    """Xếp hạng frame nên gán trước.

    frames: [{frame, features, labeled: số nhãn đã có, flagged: số đề xuất có cờ, low: số đề xuất điểm thấp}]
    Điểm = độ khác lạ (chọn tham lam kiểu k-center: frame xa nhất so với các frame đã chọn/đã gán)
         + độ khó (đề xuất có cờ/điểm thấp, ảnh tối/nhiều chi tiết) ; frame đã có nhãn bị đẩy xuống.
    """
    if not frames:
        return []
    vecs = np.stack([_vec(f["features"]) for f in frames])
    labeled = np.array([f.get("labeled", 0) > 0 for f in frames])
    hard = np.array([min(1.0, 0.25 * f.get("flagged", 0) + 0.1 * f.get("low", 0)) + (0.3 if f["features"]["bright"] < 0.25 else 0.0)
                     + min(0.3, f["features"]["edge"] * 2.0) for f in frames])
    chosen: list[int] = []
    anchors = vecs[labeled] if labeled.any() else vecs[:0]
    dist = np.full(len(frames), np.inf) if not len(anchors) else np.min(np.linalg.norm(vecs[:, None] - anchors[None], axis=2), axis=1)
    if not len(anchors):
        dist = np.linalg.norm(vecs - vecs.mean(0), axis=1)
    out = []
    k = min(k or len(frames), len(frames))
    for _ in range(k):
        scale = float(np.max(dist[np.isfinite(dist)])) if np.isfinite(dist).any() else 1.0
        score = np.where(np.isfinite(dist), dist / max(scale, 1e-6), 1.0) + 0.5 * hard - 1.5 * labeled
        if chosen:
            score[chosen] = -np.inf
        i = int(np.argmax(score))
        if not np.isfinite(score[i]):
            break
        chosen.append(i)
        reasons = []
        if dist[i] / max(scale, 1e-6) > 0.6 or not np.isfinite(dist[i]):
            reasons.append("khung cảnh khác các frame đã làm" if len(anchors) else "khung cảnh khác phần lớn job")
        if frames[i].get("flagged"):
            reasons.append(f"{frames[i]['flagged']} đề xuất có cờ")
        if frames[i]["features"]["bright"] < 0.25:
            reasons.append("ảnh tối")
        if frames[i]["features"]["edge"] > 0.12:
            reasons.append("nhiều chi tiết")
        if labeled[i]:
            reasons.append("đã có nhãn")
        out.append({"frame": frames[i]["frame"], "score": round(float(score[i]), 3), "reasons": reasons or ["đại diện cho nhóm frame giống nhau"]})
        dist = np.minimum(dist, np.linalg.norm(vecs - vecs[i], axis=1))
    return out


def keyframes(frames: list[dict], threshold: float = 0.06, max_gap: int = 30) -> dict:
    """Keyframe theo chuyển động: frame đầu, frame mà thay đổi tích luỹ từ keyframe trước vượt ngưỡng, và không để
    quá max_gap frame không có keyframe. Trả {keyframes, changes} (changes: mức thay đổi so với frame trước)."""
    if not frames:
        return {"keyframes": [], "changes": []}
    frames = sorted(frames, key=lambda f: f["frame"])
    grays = [np.asarray(f["features"]["gray"], np.float32) for f in frames]
    keys = [frames[0]["frame"]]
    changes = [0.0]
    last_key = grays[0]
    since = 0
    for i in range(1, len(frames)):
        diff = float(np.mean(np.abs(grays[i] - grays[i - 1])))
        changes.append(round(diff, 4))
        since += 1
        if float(np.mean(np.abs(grays[i] - last_key))) >= threshold or since >= max_gap:
            keys.append(frames[i]["frame"])
            last_key, since = grays[i], 0
    return {"keyframes": keys, "changes": [{"frame": f["frame"], "change": c} for f, c in zip(frames, changes)]}


def search(engine: Any, images: list[tuple[int, np.ndarray]], query: str, threshold: float = 0.35) -> list[dict]:
    """Tìm frame chứa thứ được mô tả bằng chữ (tiếng Anh) bằng chính SAM3: điểm cao nhất và số vật khớp."""
    out = []
    for frame, image in images:
        state = engine.encode(image)
        found = [i for i in engine.segment_text(state, query, threshold) if i.mask.any()]
        if found:
            best = max(found, key=lambda i: i.score)
            out.append({"frame": frame, "score": round(float(best.score), 3), "count": len(found),
                        "bbox": [round(float(v), 1) for v in (G.mask_bbox(best.mask.astype(bool)) or [0, 0, 0, 0])]})
    out.sort(key=lambda r: (-r["score"], -r["count"]))
    return out


def _bbox(shape: dict) -> Optional[list[float]]:
    pts = shape.get("points") or []
    if shape.get("type") == "rectangle" and len(pts) >= 4:
        return [min(pts[0], pts[2]), min(pts[1], pts[3]), max(pts[0], pts[2]), max(pts[1], pts[3])]
    if shape.get("type") == "skeleton":
        pts = [v for e in shape.get("elements") or [] for v in (e.get("points") or [])[:2]]
    if len(pts) >= 4 and shape.get("type") != "mask":
        xs, ys = pts[0::2], pts[1::2]
        return [min(xs), min(ys), max(xs), max(ys)]
    if shape.get("type") == "mask" and len(pts) >= 4:
        return list(pts[-4:])
    return None


def agreement(a: list[dict], b: list[dict], labels: dict[int, str], iou: float = 0.5) -> dict:
    """Độ đồng thuận giữa hai bộ nhãn trên cùng các frame (hai người gán, hoặc job gốc với job kiểm tra).

    Ghép theo IoU hộp bao ≥ iou trên từng frame (tham lam, cặp IoU cao trước). Tính cho từng label: số khớp, chỉ A có,
    chỉ B có, IoU trung bình; cộng các cặp class bị gán khác nhau và các frame lệch nhiều nhất."""
    frames = sorted({s["frame"] for s in a} | {s["frame"] for s in b})
    per: dict[str, dict] = {}
    confusion: dict[str, int] = {}
    frame_rows = []

    def row(name: str) -> dict:
        return per.setdefault(name, {"label": name, "matched": 0, "only_a": 0, "only_b": 0, "iou_sum": 0.0})

    for f in frames:
        sa = [(s, _bbox(s)) for s in a if s["frame"] == f]
        sb = [(s, _bbox(s)) for s in b if s["frame"] == f]
        sa = [x for x in sa if x[1]]
        sb = [x for x in sb if x[1]]
        pairs = sorted(((G.box_iou(x[1], y[1]), i, j) for i, x in enumerate(sa) for j, y in enumerate(sb)), reverse=True)
        used_a, used_b = set(), set()
        bad = 0
        for v, i, j in pairs:
            if v < iou or i in used_a or j in used_b:
                continue
            used_a.add(i)
            used_b.add(j)
            la, lb = labels.get(sa[i][0]["label_id"], str(sa[i][0]["label_id"])), labels.get(sb[j][0]["label_id"], str(sb[j][0]["label_id"]))
            if la == lb:
                r = row(la)
                r["matched"] += 1
                r["iou_sum"] += v
            else:
                confusion[f"{la} ↔ {lb}"] = confusion.get(f"{la} ↔ {lb}", 0) + 1
                row(la)["only_a"] += 1
                row(lb)["only_b"] += 1
                bad += 1
        for i, x in enumerate(sa):
            if i not in used_a:
                row(labels.get(x[0]["label_id"], str(x[0]["label_id"])))["only_a"] += 1
                bad += 1
        for j, y in enumerate(sb):
            if j not in used_b:
                row(labels.get(y[0]["label_id"], str(y[0]["label_id"])))["only_b"] += 1
                bad += 1
        frame_rows.append({"frame": f, "disagree": bad, "a": len(sa), "b": len(sb)})
    rows = []
    tm = ta = tb = 0
    for r in per.values():
        n = r["matched"] + r["only_a"] + r["only_b"]
        r["agreement"] = round(r["matched"] / n, 3) if n else 1.0
        r["mean_iou"] = round(r["iou_sum"] / r["matched"], 3) if r["matched"] else None
        del r["iou_sum"]
        rows.append(r)
        tm, ta, tb = tm + r["matched"], ta + r["only_a"], tb + r["only_b"]
    rows.sort(key=lambda r: r["agreement"])
    total = tm + ta + tb
    return {"frames": len(frames), "overall": round(tm / total, 3) if total else 1.0, "labels": rows,
            "confusion": [{"pair": k, "count": v} for k, v in sorted(confusion.items(), key=lambda kv: -kv[1])],
            "worst_frames": sorted([fr for fr in frame_rows if fr["disagree"]], key=lambda fr: -fr["disagree"])[:20]}
