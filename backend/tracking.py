"""Theo dõi đối tượng qua cả job video: mỗi người một track ID, ghi thành track của CVAT.

Cách làm (tracking-by-detection):
  1. Mỗi frame: SAM3 tìm đối tượng theo prompt của label (cùng model đang dùng cho ảnh,
     nên không tốn thêm VRAM), kèm một "dấu vân" màu sắc của từng box.
  2. Nối các box qua frame thành track: ưu tiên chồng lấn với vị trí dự đoán ở frame kế,
     nối lại sau khi khuất nếu ngoại hình giống và không vượt ngưỡng số frame của guideline.
  3. Theo guideline tracking: box toàn thân khi bị che một phần (amodal) + occluded,
     outside từ frame mất hẳn hoặc ra khỏi khung, keyframe đặt sao cho nội suy tuyến tính
     của CVAT vẫn bám người (IoU >= ngưỡng).
Mọi trường hợp không chắc (nối lại sau khuất, hai người đi sát nhau, track ngắn) đều gắn cờ.
"""
from __future__ import annotations


import cv2
import numpy as np

from . import geometry as G
from .engines import Engine
from .pipeline import add_flag, detect, suppress


# --------------------------------------------------------- phát hiện -------
def appearance(image: np.ndarray, box: list[float]) -> list[float]:
    """Histogram màu (hue x saturation) của 70% phía trên box: đủ để phân biệt quần áo khác nhau."""
    h, w = image.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    y2 = y1 + max(1, int((y2 - y1) * 0.7))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, max(x2, x1 + 1)), min(h, max(y2, y1 + 1))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return [0.0] * 32
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256]).ravel()
    total = float(hist.sum()) or 1.0
    return [round(float(v) / total, 5) for v in hist]


def similarity(a, b) -> float:
    """1 = giống hệt, 0 = khác hẳn (dựa trên khoảng cách Bhattacharyya)."""
    a = np.asarray(a, np.float32)
    b = np.asarray(b, np.float32)
    if a.sum() <= 0 or b.sum() <= 0:
        return 0.0
    return float(1.0 - cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA))


def detect_frame(engine: Engine, image: np.ndarray, cfgs: list[dict], warnings: list[str]) -> list[dict]:
    height, width = image.shape[:2]
    active = [dict(c, role="object", shape="rectangle") for c in cfgs if c["enabled"] and c["supported"]]
    if not active:
        return []
    state = engine.encode(image)
    dets = suppress(detect(engine, state, active, warnings=warnings), 0.6, 1.0)
    out = []
    for det in dets:
        box = G.clamp_box(det.box, width, height)
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            continue
        out.append({
            "label_id": det.cfg["label_id"],
            "box": [round(v, 2) for v in box],
            "score": round(det.score, 3),
            "hist": appearance(image, box),
        })
    return out


# ------------------------------------------------------------ nối ID -------
class _Track:
    def __init__(self, tid: int, label_id: int, frame: int, det: dict):
        self.tid = tid
        self.label_id = label_id
        self.obs: dict[int, np.ndarray] = {frame: np.asarray(det["box"], float)}
        self.scores: list[float] = [det["score"]]
        self.hist = np.asarray(det["hist"], float)
        self.last = frame
        self.reids: list[tuple[int, int, float]] = []  # (frame hiện lại, số frame khuất, độ giống)

    def predict(self, frame: int) -> np.ndarray:
        """Vị trí dự đoán: tâm đi theo vận tốc gần đây, kích thước lấy trung vị gần đây.

        Dùng trung vị để một frame bị che (box co lại) không làm lệch dự đoán.
        """
        recent = sorted(self.obs)[-6:]
        boxes = np.array([self.obs[f] for f in recent])
        centers = np.stack([(boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2], 1)
        w = float(np.median(boxes[:, 2] - boxes[:, 0]))
        h = float(np.median(boxes[:, 3] - boxes[:, 1]))
        if len(recent) >= 2:
            steps = np.diff(np.array(recent, float))
            velocity = np.median(np.diff(centers, axis=0) / steps[:, None], axis=0)
        else:
            velocity = np.zeros(2)
        c = centers[-1] + velocity * min(frame - self.last, 10)  # không ngoại suy quá xa khi khuất lâu
        return np.array([c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2])

    def add(self, frame: int, det: dict) -> None:
        self.obs[frame] = np.asarray(det["box"], float)
        self.scores.append(det["score"])
        mixed = 0.85 * self.hist + 0.15 * np.asarray(det["hist"], float)
        self.hist = mixed / (mixed.sum() or 1.0)
        self.last = frame


def _center(b: np.ndarray) -> np.ndarray:
    return np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])


def associate_by_id(frames: list[dict]) -> list[_Track]:
    """Phát hiện đã mang ID của SAM3 Video: nối theo ID đó, không đoán bằng IoU/màu sắc."""
    tracks: dict[tuple, _Track] = {}
    for item in sorted(frames, key=lambda x: x["frame"]):
        f = int(item["frame"])
        for det in item.get("dets") or []:
            key = (det["label_id"], det["obj_id"])
            if key in tracks:
                tracks[key].add(f, det)
            else:
                tracks[key] = _Track(len(tracks) + 1, det["label_id"], f, det)
    return list(tracks.values())


def associate(frames: list[dict], rule: dict) -> list[_Track]:
    if any(det.get("obj_id") is not None for item in frames for det in item.get("dets") or []):
        return associate_by_id(frames)
    match_iou = float(rule.get("match_iou", 0.3))
    max_gap = int(rule.get("reid_max_gap_frames", 60))
    reid_app = float(rule.get("reid_min_appearance", 0.75))
    reid_dist = float(rule.get("reid_max_distance", 1.5))
    tracks: list[_Track] = []
    for item in sorted(frames, key=lambda x: x["frame"]):
        f = int(item["frame"])
        dets = item.get("dets") or []
        candidates = []
        for ti, track in enumerate(tracks):
            gap = f - track.last
            if gap <= 0 or gap > max_gap + 1:
                continue
            pred = track.predict(f)
            ph = max(pred[3] - pred[1], 1.0)
            for di, det in enumerate(dets):
                if det["label_id"] != track.label_id:
                    continue
                box = np.asarray(det["box"], float)
                iou = G.box_iou(pred, box)
                app = similarity(track.hist, det["hist"])
                dist = float(np.linalg.norm(_center(pred) - _center(box))) / ph
                # Bị che một phần: box nhìn thấy co lại nhưng vẫn nằm gọn trong vị trí dự đoán.
                inside = G.box_inter(pred, box) / max(G.box_area(box), 1.0)
                if gap == 1:
                    if iou >= match_iou or (dist < 0.5 and app >= 0.6) or (inside >= 0.7 and app >= 0.5):
                        candidates.append((1.0 + 0.6 * iou + 0.4 * app - 0.05 * dist, ti, di, gap, app))
                elif app >= reid_app and dist <= reid_dist * (1 + gap / 30):
                    candidates.append((0.7 * app - 0.1 * dist, ti, di, gap, app))
        used_t, used_d = set(), set()
        for _, ti, di, gap, app in sorted(candidates, key=lambda c: -c[0]):
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            if gap > 1:
                tracks[ti].reids.append((f, gap - 1, app))
            tracks[ti].add(f, dets[di])
        for di, det in enumerate(dets):
            if di not in used_d:
                tracks.append(_Track(len(tracks) + 1, det["label_id"], f, det))
    return tracks


# ---------------------------------------------------- box, cờ, keyframe ----
def _runs(frames: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for f in frames:
        if runs and f == runs[-1][-1] + 1:
            runs[-1].append(f)
        else:
            runs.append([f])
    return runs


def _smooth(boxes: np.ndarray, window: int) -> np.ndarray:
    half = window // 2
    if half <= 0 or len(boxes) < 3:
        return boxes
    return np.array([np.median(boxes[max(0, i - half): i + half + 1], axis=0) for i in range(len(boxes))])


def amodal(frames: list[int], boxes: np.ndarray, width: int, height: int, rule: dict) -> tuple[np.ndarray, list[bool]]:
    """Box co lại đột ngột (bị che một phần) -> giữ kích thước toàn thân trước đó, gắn occluded.

    Guideline: box vẫn ở vị trí ước lượng của toàn thân, không co theo phần nhìn thấy.
    Không áp dụng khi box chạm mép ảnh (đó là đang ra khỏi khung, không phải bị che).
    """
    cfg = rule.get("amodal") or {}
    if not cfg.get("enabled", True):
        return boxes, [False] * len(frames)
    ratio = float(cfg.get("shrink_ratio", 0.8))
    history = int(cfg.get("history", 15))
    out = boxes.copy()
    occ = [False] * len(frames)
    ref_h: list[float] = []
    ref_w: list[float] = []
    prev = None
    for i, b in enumerate(boxes):
        h, w = b[3] - b[1], b[2] - b[0]
        rh = float(np.median(ref_h[-history:])) if len(ref_h) >= 3 else None
        if rh and h < ratio * rh and not G.touches_border(b, width, height, 2) and prev is not None:
            rw = max(float(np.median(ref_w[-history:])), w)
            cx = (b[0] + b[2]) / 2
            if abs(b[1] - prev[1]) <= abs(b[3] - prev[3]):  # đỉnh đầu còn thấy -> giữ đỉnh, kéo dài xuống
                nb = [cx - rw / 2, b[1], cx + rw / 2, b[1] + rh]
            else:  # chân còn thấy -> giữ đáy, kéo dài lên
                nb = [cx - rw / 2, b[3] - rh, cx + rw / 2, b[3]]
            out[i] = G.clamp_box(nb, width, height)
            occ[i] = True
        else:
            ref_h.append(h)
            ref_w.append(w)
            prev = b
    return out, occ


def _interp(b0: np.ndarray, b1: np.ndarray, u: float) -> np.ndarray:
    return b0 + u * (b1 - b0)


def keyframes(frames: list[int], boxes: np.ndarray, occ: list[bool], iou_thr: float, max_gap: int) -> list[int]:
    """Ít keyframe nhất sao cho nội suy tuyến tính giữa hai keyframe vẫn có IoU >= ngưỡng với box thật.

    Luôn có keyframe ở frame đầu, frame cuối, chỗ đổi trạng thái occluded (ngay trước và ngay sau),
    và không để hai keyframe cách nhau quá `max_gap` frame (guideline: tua kiểm mỗi 10–20 frame).
    """
    idx = {0, len(frames) - 1}
    for i in range(1, len(frames)):
        if occ[i] != occ[i - 1]:
            idx.update({i - 1, i})
    changed = True
    while changed:
        changed = False
        keys = sorted(idx)
        for k0, k1 in zip(keys, keys[1:]):
            if k1 - k0 <= 1:
                continue
            worst, worst_iou = None, 1.0
            for j in range(k0 + 1, k1):
                u = (frames[j] - frames[k0]) / (frames[k1] - frames[k0])
                iou = G.box_iou(_interp(boxes[k0], boxes[k1], u), boxes[j])
                if iou < worst_iou:
                    worst, worst_iou = j, iou
            if worst is not None and worst_iou < iou_thr:
                idx.add(worst)
                changed = True
            elif frames[k1] - frames[k0] > max_gap:
                idx.add((k0 + k1) // 2)
                changed = True
    return sorted(idx)


def build_tracks(frames: list[dict], cfgs: list[dict], profile: dict, width: int, height: int,
                 start: int, stop: int) -> dict:
    rule = (profile.get("rules") or {}).get("tracking") or {}
    names = {c["label_id"]: c["name"] for c in cfgs}
    tracks = associate(frames, rule)
    max_gap = int(rule.get("reid_max_gap_frames", 60))
    min_len = int(rule.get("min_track_frames", 5))
    kf_iou = float(rule.get("keyframe_iou", 0.8))
    kf_gap = int(rule.get("max_keyframe_gap", 20))
    dense = float(rule.get("dense_keyframes_per_100", 50))
    warnings: list[str] = []

    built = []
    for t in tracks:
        frames_seen = sorted(t.obs)
        raw = np.array([t.obs[f] for f in frames_seen])
        smoothed = np.zeros_like(raw)
        pos = 0
        for run in _runs(frames_seen):
            n = len(run)
            smoothed[pos:pos + n] = _smooth(raw[pos:pos + n], int(rule.get("smooth_window", 5)))
            pos += n
        boxes, occ = amodal(frames_seen, smoothed, width, height, rule)
        built.append({"t": t, "frames": frames_seen, "boxes": boxes, "occ": occ, "flags": []})

    built.sort(key=lambda b: (b["frames"][0], b["t"].tid))
    for i, b in enumerate(built, 1):
        b["t"].tid = i  # đánh số lại theo thứ tự xuất hiện: #1 là người xuất hiện sớm nhất

    # Bị một người ở gần camera hơn che (đáy box thấp hơn) -> occluded, kể cả khi box không co.
    overlap = float(rule.get("occluded_overlap", 0.15))
    merge_iou = float(rule.get("merge_iou", 0.6))
    by_frame: dict[int, list[tuple[int, int]]] = {}
    for bi, b in enumerate(built):
        for k, f in enumerate(b["frames"]):
            by_frame.setdefault(f, []).append((bi, k))
    close: dict[tuple[int, int], list[int]] = {}
    for f, members in by_frame.items():
        for bi, k in members:
            box = built[bi]["boxes"][k]
            area = G.box_area(box)
            for bj, kj in members:
                if bj == bi:
                    continue
                other = built[bj]["boxes"][kj]
                if area > 0 and other[3] > box[3] and G.box_inter(box, other) / area >= overlap:
                    built[bi]["occ"][k] = True
                if bi < bj and G.box_iou(box, other) >= merge_iou:
                    close.setdefault((bi, bj), []).append(f)
    for (bi, bj), fs in close.items():
        if len(fs) >= 3:
            for a, b in ((bi, bj), (bj, bi)):
                add_flag(built[a]["flags"], "id_check",
                         f"Đi sát track #{built[b]['t'].tid} từ frame {min(fs)} ({len(fs)} frame): kiểm tra đổi ID hoặc một box bao hai người.")

    out = []
    for b in built:
        t, frames_seen, boxes, occ, flags = b["t"], b["frames"], b["boxes"], b["occ"], b["flags"]
        for f, gap, app in t.reids:
            add_flag(flags, "id_check", f"Nối lại ở frame {f} sau {gap} frame khuất (ngoại hình giống {app:.0%}): xác nhận đúng cùng người.")
        shapes = []
        pos = 0
        n_keys = 0
        runs = _runs(frames_seen)
        for r, run in enumerate(runs):
            n = len(run)
            rb, ro = boxes[pos:pos + n], occ[pos:pos + n]
            for k in keyframes(run, rb, ro, kf_iou, kf_gap):
                shapes.append({"frame": run[k], "points": [round(float(v), 2) for v in rb[k]],
                               "occluded": bool(ro[k]), "outside": False})
                n_keys += 1
            end = run[-1] + 1
            if end <= stop:
                # outside từ frame đầu tiên không còn thấy; hiện lại thì keyframe kế tiếp tắt outside.
                shapes.append({"frame": end, "points": [round(float(v), 2) for v in rb[-1]],
                               "occluded": False, "outside": True})
            pos += n
        span = frames_seen[-1] - frames_seen[0] + 1
        if len(frames_seen) < min_len:
            add_flag(flags, "false_track", f"Track chỉ có {len(frames_seen)} frame: có thể là phát hiện nhầm.")
        if span >= 20 and n_keys / span * 100 > dense:
            add_flag(flags, "drift", f"{n_keys} keyframe trên {span} frame là dày (guideline: >50/100 là quá dày); box có thể đang rung.")
        if any(occ):
            add_flag(flags, "flag", f"{sum(occ)} frame đặt occluded theo ước lượng (box toàn thân khi bị che): kiểm tra lại.")
        out.append({
            "tid": t.tid,
            "label_id": t.label_id,
            "label_name": names.get(t.label_id, str(t.label_id)),
            "start": frames_seen[0],
            "end": frames_seen[-1],
            "visible_frames": len(frames_seen),
            "keyframes": n_keys,
            "gaps": len(runs) - 1,
            "score": round(float(np.mean(t.scores)), 3),
            "shapes": shapes,
            "flags": flags,
            "checked": len(frames_seen) >= min_len,
        })

    # Gợi ý "fragmentation": track mới bắt đầu gần chỗ một track khác vừa mất, nhưng chưa đủ giống để tự nối.
    for later in out:
        for earlier in out:
            if earlier is later or earlier["label_id"] != later["label_id"]:
                continue
            gap = later["start"] - earlier["end"] - 1
            if 0 < gap <= max_gap:
                last = np.array(earlier["shapes"][-1]["points"])
                first = np.array(later["shapes"][0]["points"])
                h = max(last[3] - last[1], 1.0)
                if np.linalg.norm(_center(last) - _center(first)) / h <= 1.5 * (1 + gap / 30):
                    add_flag(later["flags"], "id_check",
                             f"Có thể là người của track #{earlier['tid']} (mất ở frame {earlier['end']}, cách {gap} frame). Nếu đúng thì gộp thành một ID.")
    out.sort(key=lambda x: (x["start"], x["tid"]))
    if not out:
        warnings.append("Không tìm thấy đối tượng nào trong đoạn frame đã chọn.")
    return {"tracks": out, "warnings": warnings, "start": start, "stop": stop}


def video_dets(raw: list[dict], image: np.ndarray, cfgs: list[dict]) -> list[dict]:
    """Kết quả SAM3 Video của một frame -> phát hiện cùng dạng /track/detect, giữ ID của model."""
    height, width = image.shape[:2]
    by_prompt: dict[str, dict] = {}
    for cfg in cfgs:
        if cfg["enabled"] and cfg["supported"]:
            for prompt in cfg.get("prompts") or []:
                by_prompt.setdefault(prompt, cfg)
    out = []
    for r in raw:
        cfg = by_prompt.get(r.get("prompt") or "")
        if cfg is None or r["score"] < cfg["threshold"]:
            continue
        box = G.clamp_box(r["box"], width, height)
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            continue
        out.append({"label_id": cfg["label_id"], "obj_id": int(r["obj_id"]), "box": [round(v, 2) for v in box],
                    "score": round(float(r["score"]), 3), "hist": appearance(image, box)})
    return out
