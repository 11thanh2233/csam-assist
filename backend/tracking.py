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


from typing import Optional

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


def emb_similarity(a, b) -> Optional[float]:
    """Độ giống theo đặc trưng re-ID (0.9.6, model DINOv2 nhỏ): cosine đưa về 0..1. None nếu thiếu."""
    if a is None or b is None:
        return None
    a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if not na or not nb:
        return None
    return float(max(0.0, (a @ b) / (na * nb)))


def look_alike(track, det) -> float:
    """Ngoại hình giống nhau: có đặc trưng re-ID thì dùng chủ yếu nó (ổn định hơn màu áo khi đổi ánh sáng, bị che)."""
    app = similarity(track.hist, det["hist"])
    e = emb_similarity(getattr(track, "emb", None), det.get("emb"))
    return app if e is None else 0.3 * app + 0.7 * e


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
            # phần mask phủ trong hộp: người bị cột, xe che ở giữa thì mask thủng dù hộp không co (0.9.4)
            "fill": round(float(np.count_nonzero(det.mask)) / max(G.box_area(box), 1.0), 3),
        })
    return out


def in_zones(box, zones) -> bool:
    """Tâm hộp nằm trong một vùng bỏ qua (gương, kính, biển quảng cáo, màn hình) do người dùng khoanh."""
    if not zones:
        return False
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return any(z[0] <= cx <= z[2] and z[1] <= cy <= z[3] for z in zones)


# ------------------------------------------------------------ nối ID -------
class _Track:
    def __init__(self, tid: int, label_id: int, frame: int, det: dict):
        self.tid = tid
        self.label_id = label_id
        self.obs: dict[int, np.ndarray] = {frame: np.asarray(det["box"], float)}
        self.fill: dict[int, float] = {frame: float(det.get("fill") or 0.0)}
        self.target: Optional[str] = None
        self.scores: list[float] = [det["score"]]
        self.hist = np.asarray(det["hist"], float)
        self.last = frame
        self.reids: list[tuple[int, int, float]] = []  # (frame hiện lại, số frame khuất, độ giống)
        self.hidden: dict[int, float] = {}
        self.emb = np.asarray(det["emb"], float) if det.get("emb") is not None else None

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
        self.fill[frame] = float(det.get("fill") or 0.0)
        self.scores.append(det["score"])
        mixed = 0.85 * self.hist + 0.15 * np.asarray(det["hist"], float)
        self.hist = mixed / (mixed.sum() or 1.0)
        if det.get("emb") is not None:
            e = np.asarray(det["emb"], float)
            self.emb = e if self.emb is None else 0.9 * self.emb + 0.1 * e
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
                app = look_alike(track, det)
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


def amodal(frames: list[int], boxes: np.ndarray, width: int, height: int, rule: dict,
           fills: Optional[list[float]] = None) -> tuple[np.ndarray, list[bool]]:
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
    # Mask thủng ở giữa (bị cột, xe, đồ vật che) mà hộp không co: so phần mask phủ trong hộp với lúc thấy rõ.
    if fills and any(fills):
        drop = float(cfg.get("fill_ratio", 0.7))
        clear = [f for f, o in zip(fills, occ) if f and not o]
        if len(clear) >= 3:
            ref = float(np.median(clear))
            for i, f in enumerate(fills):
                if f and f < drop * ref and not G.touches_border(out[i], width, height, 2):
                    occ[i] = True
    return out, occ


DEFAULT_OCCLUDED_HIDDEN = 0.35   # mặc định khi profile không ghi: bị che từ 35% diện tích toàn thân thì occluded
DEFAULT_OUTSIDE_HIDDEN = 0.60    # bị che từ 60% (không còn ước lượng được) thì outside


def hidden_fraction(boxes: np.ndarray, amodal_boxes: np.ndarray, fills: list[float], width: int, height: int,
                    raw_ref: Optional[tuple[float, float]] = None) -> list[float]:
    """Phần trăm diện tích TOÀN THÂN bị che ở từng frame (0.9.6), theo chính vật:

    - hộp toàn thân (amodal) ước lượng từ các frame thấy rõ; phần hộp nhìn thấy so với hộp toàn thân;
    - phần mask phủ trong hộp so với lúc thấy rõ (bị cột, xe che ở giữa: hộp không co nhưng mask thủng);
    - ra mép ảnh không tính là bị che (đó là ra khỏi khung: outside khi không còn thấy).
    """
    clear = [f for f, b, a in zip(fills, boxes, amodal_boxes) if f and G.box_iou(b, a) > 0.95]
    ref_fill = float(np.median(clear)) if len(clear) >= 3 else (max(fills) if any(fills) else 0.0)
    out = []
    for b, a, f in zip(boxes, amodal_boxes, fills):
        a = np.asarray(a, float)
        full = max((a[2] - a[0]) * (a[3] - a[1]), 1.0)
        seen = G.box_area(G.clamp_box(list(b), width, height))
        frac = min(1.0, seen / full)
        if f and ref_fill:
            frac *= min(1.0, f / ref_fill)
        out.append(round(1.0 - frac, 3))
    return out


def visibility_states(hidden: list[float], occ_thr: float, out_thr: float, margin: float = 0.05) -> list[int]:
    """0 visible, 1 occluded, 2 outside theo ngưỡng của profile, có trễ (hysteresis): vào trạng thái nặng hơn khi
    vượt ngưỡng, chỉ quay lại trạng thái nhẹ hơn khi xuống dưới ngưỡng trừ `margin`. Thêm: bỏ chập chờn 1 frame."""
    st: list[int] = []
    cur = None
    for h in hidden:
        raw = 2 if h >= out_thr else (1 if h >= occ_thr else 0)
        if cur is None or raw > cur:
            cur = raw
        elif raw < cur:
            # chỉ hạ trạng thái khi đã rõ ràng dưới ngưỡng của trạng thái hiện tại
            lower = out_thr if cur == 2 else occ_thr
            if h < lower - margin:
                cur = raw
        st.append(cur)
    for i in range(1, len(st) - 1):
        if st[i - 1] == st[i + 1] != st[i]:
            st[i] = st[i - 1]
    return st


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


def _merge(into: "_Track", other: "_Track") -> None:
    for f in other.obs:
        into.obs[f] = other.obs[f]
        into.fill[f] = other.fill.get(f, 0.0)
    into.scores += other.scores
    into.reids += other.reids
    into.last = max(into.last, other.last)


def assign_targets(tracks: list["_Track"], targets: list[dict], rule: dict) -> tuple[list["_Track"], list[str]]:
    """Chỉ giữ người bạn đã chọn (0.9.4).

    Mỗi người chọn = một hộp ở một frame. Track có hộp trùng (IoU >= 0.4) ở frame đó (hoặc frame gần nhất trong
    ±3) là người đó. Các đoạn track khác (người đó bị mất dấu rồi hiện lại) được nối vào nếu không trùng thời gian
    và ngoại hình giống. Phần còn lại (người khác, phản chiếu, quảng cáo) bị bỏ.
    """
    notes: list[str] = []
    chosen: dict[str, _Track] = {}
    for tg in targets:
        f0, box = int(tg["frame"]), np.asarray(tg["box"], float)
        best, best_iou = None, 0.4
        for t in tracks:
            if t in chosen.values() or (tg.get("label_id") and t.label_id != tg["label_id"]):
                continue
            near = [f for f in t.obs if abs(f - f0) <= 3]
            if not near:
                continue
            f = min(near, key=lambda x: abs(x - f0))
            iou = G.box_iou(t.obs[f], box)
            if iou > best_iou:
                best, best_iou = t, iou
        if best is None:
            notes.append(f"Không tìm thấy {tg.get('name', 'người đã chọn')} ở frame {f0}: model không phát hiện được người ở chỗ bạn khoanh.")
            continue
        best.target = tg.get("name") or f"P{len(chosen) + 1}"
        chosen[best.target] = best
    reid_app = float(rule.get("reid_min_appearance", 0.75))
    max_gap = int(rule.get("reid_max_gap_frames", 60))
    rest = [t for t in tracks if t.target is None]
    # 0.9.6: đoạn hiện lại của người đã chọn được nối vào nếu (a) đúng quỹ đạo (vị trí dự đoán) và ngoại hình tạm giống,
    # hoặc (b) ngoại hình rất giống. Trùng vài frame (phát hiện đôi lúc chồng nhau) thì bỏ phần trùng, không vứt cả đoạn.
    for t in sorted(rest, key=lambda x: min(x.obs)):
        best, best_score, best_app = None, 0.0, 0.0
        for name, tt in chosen.items():
            if t.label_id != tt.label_id:
                continue
            overlap = set(t.obs) & set(tt.obs)
            if len(overlap) > 3:
                continue
            first = min(f for f in t.obs if f not in overlap) if len(overlap) < len(t.obs) else None
            if first is None:
                continue
            before = [f for f in tt.obs if f < first]
            gap = first - max(before) if before else None
            app = look_alike(tt, {"hist": t.hist, "emb": None if t.emb is None else t.emb})
            score = 0.0
            if gap is not None and 0 < gap <= max_gap + 1:
                pred = tt.predict(first)
                h = max(pred[3] - pred[1], 1.0)
                dist = float(np.linalg.norm(_center(pred) - _center(t.obs[first]))) / h
                if dist <= 1.0 * (1 + gap / 30) and app >= 0.5:
                    score = 1.0 + app - 0.2 * dist
            if not score and app >= reid_app:
                score = app
            if score > best_score:
                best, best_score, best_app = tt, score, app
        if best is not None:
            for f in set(t.obs) & set(best.obs):
                t.obs.pop(f, None)
                t.fill.pop(f, None)
            gap = min(abs(f - g) for f in t.obs for g in best.obs)
            best.reids.append((min(t.obs), gap, best_app))
            _merge(best, t)
            t.target = "merged"
    dropped = sum(1 for t in tracks if t.target is None)
    if dropped:
        notes.append(f"Bỏ {dropped} track không phải người bạn đã chọn.")
    return [t for t in tracks if t.target not in (None, "merged")], notes


def build_tracks(frames: list[dict], cfgs: list[dict], profile: dict, width: int, height: int,
                 start: int, stop: int, targets: Optional[list[dict]] = None, zones: Optional[list[list[float]]] = None) -> dict:
    rule = (profile.get("rules") or {}).get("tracking") or {}
    names = {c["label_id"]: c["name"] for c in cfgs}
    zone_drop = 0
    if zones:
        clean = []
        for item in frames:
            dets = [d for d in item.get("dets") or [] if not in_zones(d["box"], zones)]
            zone_drop += len(item.get("dets") or []) - len(dets)
            clean.append(dict(item, dets=dets))
        frames = clean
    tracks = associate(frames, rule)
    target_notes: list[str] = []
    if targets:
        tracks, target_notes = assign_targets(tracks, targets, rule)
    max_gap = int(rule.get("reid_max_gap_frames", 60))
    min_len = int(rule.get("min_track_frames", 5))
    kf_iou = float(rule.get("keyframe_iou", 0.8))
    kf_gap = int(rule.get("max_keyframe_gap", 20))
    dense = float(rule.get("dense_keyframes_per_100", 50))
    miss_gap = int(rule.get("miss_gap_frames", 2))          # model bỏ sót vài frame: nối qua, không đặt outside
    hidden_gap = int(rule.get("max_occluded_frames", 0))     # bị che hẳn tối đa bấy nhiêu frame thì giữ box (cuboid: 6)
    occ_thr = float(rule.get("occluded_hidden", DEFAULT_OCCLUDED_HIDDEN))
    out_thr = float(rule.get("outside_hidden", DEFAULT_OUTSIDE_HIDDEN))
    warnings: list[str] = list(target_notes)
    if zone_drop:
        warnings.append(f"Bỏ {zone_drop} phát hiện nằm trong vùng bỏ qua.")

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
        fills = [t.fill.get(f, 0.0) for f in frames_seen]
        boxes, _shrunk = amodal(frames_seen, smoothed, width, height, rule, None)
        # 0.9.6: trạng thái theo % diện tích toàn thân bị che, ngưỡng lấy từ profile (guideline của project)
        # đo trên hộp gốc (chưa làm mượt) để frame bị che rõ không bị trung bình hoá; phần thân ra ngoài mép ảnh là
        # "ra khỏi khung" (guideline: outside khi không còn thấy), không tính là bị che
        seen_raw = np.array([G.clamp_box(list(bx), width, height) for bx in raw])
        hidden = hidden_fraction(seen_raw, amodal(frames_seen, seen_raw, width, height, rule, None)[0], fills, width, height)
        states = visibility_states(hidden, occ_thr, out_thr)
        occ = [s_ == 1 for s_ in states]
        frames_seen, boxes, occ, bridged = bridge_gaps(frames_seen, boxes, occ, width, height, miss_gap, hidden_gap)
        seen_state = dict(zip([f for f in sorted(t.obs)], states))
        flags: list[dict] = []
        if bridged:
            add_flag(flags, "flag", f"Nối qua {bridged} frame model không thấy (bỏ sót hoặc bị che ngắn): kiểm tra occluded ở các frame đó.")
        # frame bị che quá ngưỡng outside: thành khoảng trống của track (outside), hiện lại thì track chạy tiếp cùng ID
        drop = {f for f, s_ in seen_state.items() if s_ == 2}
        if drop and len(drop) < len(frames_seen):
            keep_idx = [i for i, f in enumerate(frames_seen) if f not in drop]
            frames_seen = [frames_seen[i] for i in keep_idx]
            boxes = boxes[keep_idx]
            occ = [occ[i] for i in keep_idx]
            add_flag(flags, "flag", f"{len(drop)} frame bị che từ {out_thr:.0%} thân trở lên: đặt outside, hiện lại thì giữ ID.")
        t.hidden = {f: h for f, h in zip(sorted(t.obs), hidden)}
        if static_track(frames_seen, boxes, start, stop, width, height):
            add_flag(flags, "false_track", "Đứng yên gần như suốt job: có thể là người trong ảnh, poster, quảng cáo hoặc phản chiếu. "
                                           "Bỏ tick nếu không phải người thật cần gán.")
        built.append({"t": t, "frames": frames_seen, "boxes": boxes, "occ": occ, "flags": flags})

    built.sort(key=lambda b: (b["frames"][0], b["t"].tid))
    for i, b in enumerate(built, 1):
        b["t"].tid = i  # đánh số lại theo thứ tự xuất hiện: #1 là người xuất hiện sớm nhất

    # 0.9.6: bỏ luật "hộp chồng hộp thì occluded" (occluded xét theo chính người đó, ở trên); giữ cờ đi sát nhau.
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
            "name": t.target if t.target not in (None, "merged") else None,
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
            "checked": len(frames_seen) >= min_len and not any(f["code"] == "false_track" and "Đứng yên" in f["message"] for f in flags),
        })

    # Gợi ý "fragmentation": track mới bắt đầu gần chỗ một track khác vừa mất, nhưng chưa đủ giống để tự nối.
    # 0.9.4: một cờ cho mỗi track, tối đa 3 ứng viên gần nhất (trước đây mỗi ứng viên một cờ, có track 7 cờ lặp chữ).
    for later in out:
        cands = []
        for earlier in out:
            if earlier is later or earlier["label_id"] != later["label_id"]:
                continue
            gap = later["start"] - earlier["end"] - 1
            if 0 < gap <= max_gap:
                last = np.array(earlier["shapes"][-1]["points"])
                first = np.array(later["shapes"][0]["points"])
                h = max(last[3] - last[1], 1.0)
                d = float(np.linalg.norm(_center(last) - _center(first)) / h)
                if d <= 1.5 * (1 + gap / 30):
                    cands.append((d + gap / 60, earlier, gap))
        if cands:
            cands.sort(key=lambda c: c[0])
            parts = [f"#{e['tid']} (mất ở frame {e['end']}, cách {g} frame)" for _, e, g in cands[:3]]
            more = f" và {len(cands) - 3} track khác" if len(cands) > 3 else ""
            add_flag(later["flags"], "id_check",
                     f"Có thể cùng người với track {', '.join(parts)}{more}. Nếu đúng thì gộp thành một ID.")
    out.sort(key=lambda x: (x["start"], x["tid"]))
    if not out:
        warnings.append("Không tìm thấy đối tượng nào trong đoạn frame đã chọn.")
    return {"tracks": out, "warnings": warnings, "start": start, "stop": stop,
            "rescue": rescue_requests(built, width, height, max_gap),
            "thresholds": {"occluded_hidden": occ_thr, "outside_hidden": out_thr}}


def rescue_requests(built: list[dict], width: int, height: int, max_gap: int, limit: int = 240) -> list[dict]:
    """Frame giữa hai đoạn của một track mà model không thấy người (0.9.6): vị trí dự đoán còn trong khung thì
    nhờ SAM3 tìm lại đúng chỗ đó với ngưỡng thấp. Tìm thấy: tính % bị che; không thấy: mới là outside."""
    out = []
    for b in built:
        t = b["t"]
        seen = sorted(t.obs)
        for f0, f1 in zip(seen, seen[1:]):
            gap = f1 - f0 - 1
            if gap <= 0 or gap > max_gap:
                continue
            for f in range(f0 + 1, f1):
                u = (f - f0) / (f1 - f0)
                box = _interp(t.obs[f0], t.obs[f1], u)
                inside = G.box_area(G.clamp_box(list(box), width, height)) / max(G.box_area(list(box)), 1.0)
                if inside >= 0.5:
                    out.append({"frame": f, "label_id": t.label_id, "box": [round(float(v), 1) for v in box]})
    out.sort(key=lambda r: r["frame"])
    return out[:limit]


def bridge_gaps(frames: list[int], boxes: np.ndarray, occ: list[bool], width: int, height: int,
                miss_gap: int, hidden_gap: int) -> tuple[list[int], np.ndarray, list[bool], int]:
    """Nối các khoảng ngắn trong một track (0.9.4).

    - Khoảng <= miss_gap frame: model bỏ sót, nối bằng nội suy; occluded theo hai đầu.
    - Khoảng <= hidden_gap frame: bị che hẳn một lúc rồi hiện lại đúng chỗ, giữ box nội suy và đặt occluded
      (guideline cuboid: tối đa 6 keyframe). Mặc định 0: theo MOT, bị che hẳn thì outside.
    - Đang ở mép ảnh rồi mất hơn 1 frame: là ra khỏi khung, luôn outside, không nối.
    """
    if len(frames) < 2:
        return frames, boxes, occ, 0
    nf, nb, no, bridged = [frames[0]], [boxes[0]], [occ[0]], 0
    for i in range(1, len(frames)):
        gap = frames[i] - frames[i - 1] - 1
        # Đang ở mép ảnh rồi mất hơn 1 frame: là đi ra khỏi khung (outside), không nối.
        leaving = G.touches_border(boxes[i - 1], width, height, 2) and gap > 1
        if 0 < gap and not leaving and (gap <= miss_gap or gap <= hidden_gap):
            for k in range(1, gap + 1):
                u = k / (gap + 1)
                nf.append(frames[i - 1] + k)
                nb.append(_interp(boxes[i - 1], boxes[i], u))
                no.append(gap > miss_gap or occ[i - 1] or occ[i])
            bridged += gap
        nf.append(frames[i])
        nb.append(boxes[i])
        no.append(occ[i])
    return nf, np.array(nb), no, bridged


def static_track(frames: list[int], boxes: np.ndarray, start: int, stop: int, width: int, height: int) -> bool:
    """Track có mặt >= 60% đoạn và gần như không nhúc nhích: thường là ảnh người trên poster / quảng cáo / gương."""
    span = stop - start + 1
    if span < 30 or len(frames) < 0.6 * span:
        return False
    c = np.stack([(boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2], 1)
    diag = float(np.hypot(width, height))
    hs = boxes[:, 3] - boxes[:, 1]
    return float(c.std(axis=0).max()) < 0.004 * diag and float(hs.std()) < 0.03 * max(float(hs.mean()), 1.0)


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
