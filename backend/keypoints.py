"""Gợi ý skeleton theo profile keypoints: thân người (ViTPose) và landmark khuôn mặt (MediaPipe).

Trạng thái mỗi điểm theo guideline HumanPose-17 / Facial Landmark-50:
  Visible   thấy trực tiếp                                   -> điểm bình thường
  Occluded  bị che nhưng còn suy ra được vị trí              -> occluded
  Outside   ngoài khung hình, hoặc không còn căn cứ ước lượng -> outside
Tool không "nhìn" được như người: nó suy trạng thái từ độ tin cậy của model (thân người) và từ vật che do
SAM3 tìm thấy (mặt). Mọi điểm không phải Visible đều kèm cờ ATTRIBUTE_CHECK để người annotate quyết định.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Optional

import numpy as np

from . import geometry as G
from .engines import Engine
from .pipeline import add_flag, detect, run_prompt, shape_bbox, suppress

# Chỉ số COCO của các cặp khớp trái/phải (đầu ra của model tư thế là trái/phải THEO NGƯỜI).
HEAD, ARMS, LEGS = [(1, 2), (3, 4)], [(5, 6), (7, 8), (9, 10)], [(11, 12), (13, 14), (15, 16)]


def _proposal(cfg: dict, elements: list[dict], score: float, flags: list[dict], box: list[float]) -> dict:
    adjust = cfg.get("point_adjust") or {}
    if adjust:
        # 0.9.4: điểm bạn hay kéo lệch về cùng một hướng (tab Tự học): dịch theo cỡ skeleton
        pts = [(e["x"], e["y"]) for e in elements if not e["outside"]]
        if pts:
            xs, ys = zip(*pts)
            w, h = max(max(xs) - min(xs), 1.0), max(max(ys) - min(ys), 1.0)
            for e in elements:
                d = adjust.get(str(e["label_id"]))
                if d and not e["outside"]:
                    e["x"], e["y"] = round(e["x"] + d[0] * w, 2), round(e["y"] + d[1] * h, 2)
    shown = [(e["x"], e["y"]) for e in elements if not e["outside"]]
    if shown:
        xs, ys = zip(*shown)
        bbox = [min(xs), min(ys), max(xs), max(ys)]
    else:
        bbox = list(box)
    return {
        "label_id": cfg["label_id"], "label_name": cfg["name"], "type": "skeleton", "points": [],
        "elements": elements, "edges": cfg.get("edges", []),
        "score": round(float(score), 3), "bbox": [round(float(v), 2) for v in bbox],
        "occluded": False, "attributes": {}, "z_order": 0, "flags": flags, "checked": True, "preview": None,
    }


def _element(kp: dict, x: float, y: float, state: str, score: float, width: int, height: int) -> dict:
    return {
        "label_id": kp["label_id"], "name": kp["name"], "side": kp.get("side"), "pid": kp.get("id"),
        "x": round(min(max(float(x), 0.0), width - 1.0), 2), "y": round(min(max(float(y), 0.0), height - 1.0), 2),
        "occluded": state == "occluded", "outside": state == "outside", "score": round(float(score), 3),
    }


def _named(elements: list[dict]) -> str:
    names = [e["name"] for e in elements]
    return ", ".join(names[:6]) + ("…" if len(names) > 6 else "")


# ------------------------------------------------------------ thân người ---
def to_image_sides(coords: np.ndarray, scores: np.ndarray, min_score: float,
                   box_width: float = 0.0) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Xếp lại để ô 'right_*' là khớp nằm bên PHẢI ảnh và 'left_*' là khớp bên TRÁI ảnh.

    Guideline HumanPose-17: R/L theo phía của ảnh, bất kể người quay mặt, quay lưng hay vặn mình, nhưng
    chuỗi vai–khuỷu–cổ tay và hông–gối–cổ chân mỗi phía phải liền mạch. Vì vậy tool quyết định theo từng
    CHUỖI (tay theo hai vai, chân theo hai hông, đầu theo hai mắt), không theo từng điểm riêng lẻ.
    """
    def anatomical_left_is_on_image_right(l: int, r: int) -> Optional[bool]:
        if scores[l] >= min_score and scores[r] >= min_score and abs(coords[l][0] - coords[r][0]) > 2:
            return bool(coords[l][0] > coords[r][0])
        return None

    arms = anatomical_left_is_on_image_right(5, 6)
    legs = anatomical_left_is_on_image_right(11, 12)
    head = anatomical_left_is_on_image_right(1, 2)
    if head is None:
        head = anatomical_left_is_on_image_right(3, 4)
    notes = []
    if arms is not None and legs is not None and arms != legs:
        notes.append("Vai và hông cho hai hướng trái/phải khác nhau (người vặn mình): kiểm tra R/L của tay và chân.")
    if arms is None and legs is None and head is None:
        notes.append("Không đủ điểm tin cậy để xác định R/L: tool giả định người quay mặt về camera.")
    elif box_width > 0 and scores[5] >= min_score and scores[6] >= min_score \
            and abs(coords[5][0] - coords[6][0]) < 0.08 * box_width:
        notes.append("Hai vai gần như thẳng hàng theo chiều ngang (người nghiêng hẳn sang một bên): kiểm tra R/L của tay.")
    base = arms if arms is not None else (legs if legs is not None else (head if head is not None else True))
    arms = base if arms is None else arms
    legs = arms if legs is None else legs
    head = arms if head is None else head
    coords, scores = coords.copy(), scores.copy()
    for swap, pairs in ((head, HEAD), (arms, ARMS), (legs, LEGS)):
        if swap:  # trái của người đang ở bên phải ảnh -> đổi chỗ để 'left' là trái ảnh
            for l, r in pairs:
                coords[[l, r]] = coords[[r, l]]
                scores[[l, r]] = scores[[r, l]]
    return coords, scores, notes


def _crossed(elements: list[dict], a: str, b: str, c: str, d: str) -> bool:
    by_source = {e.get("source"): e for e in elements if not e["outside"]}
    pts = [by_source.get(k) for k in (a, b, c, d)]
    if any(p is None for p in pts):
        return False
    return G._segments_cross(*[(p["x"], p["y"]) for p in pts])


def body_checks(elements: list[dict], rule: dict, flags: list[dict]) -> None:
    """Các lỗi topology mà guideline nêu: R/L sai phía, chân bắt chéo, hai khớp Visible trùng toạ độ."""
    by_source = {e.get("source"): e for e in elements if not e["outside"]}
    if rule.get("lr_convention", "anatomical") == "image":
        for joint, label in (("shoulder", "vai"), ("hip", "hông"), ("eye", "mắt")):
            l, r = by_source.get(f"left_{joint}"), by_source.get(f"right_{joint}")
            if l and r and l["x"] - r["x"] > 3:
                add_flag(flags, "UNCERTAIN_CLASS", f"Điểm L của {label} đang nằm bên phải điểm R: guideline quy định R ở bên phải ảnh, L ở bên trái ảnh.")
    else:
        ls, rs, lh, rh = (by_source.get(k) for k in ("left_shoulder", "right_shoulder", "left_hip", "right_hip"))
        if ls and rs and lh and rh:
            ds, dh = ls["x"] - rs["x"], lh["x"] - rh["x"]
            if abs(ds) > 3 and abs(dh) > 3 and (ds > 0) != (dh > 0):
                add_flag(flags, "UNCERTAIN_CLASS", "Thứ tự trái/phải ở vai và ở hông ngược nhau.")
    if _crossed(elements, "left_knee", "left_ankle", "right_knee", "right_ankle"):
        add_flag(flags, "UNCERTAIN_CLASS", "Hai cẳng chân bắt chéo nhau: kiểm tra R/L.")
    limit = float(rule.get("coincident_px", 0))
    if limit > 0:
        visible = [e for e in elements if not e["outside"] and not e["occluded"]]
        for i, a in enumerate(visible):
            for b in visible[i + 1:]:
                if math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < limit:
                    add_flag(flags, "ATTRIBUTE_CHECK", f"Hai điểm Visible trùng toạ độ ({a['name']} và {b['name']}): tách đúng vị trí, hoặc đánh lại trạng thái cho điểm không thực sự thấy.")


SOURCE_OF_INDEX = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
                   "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
                   "left_knee", "right_knee", "left_ankle", "right_ankle"]


def body_skeleton(cfg: dict, coords: np.ndarray, scores: np.ndarray, box: list[float], score: float,
                  width: int, height: int, rule: dict) -> dict:
    visible, occluded = float(rule.get("visible_score", 0.6)), float(rule.get("occluded_score", 0.35))
    flags: list[dict] = []
    if rule.get("lr_convention", "anatomical") == "image":
        coords, scores, notes = to_image_sides(np.asarray(coords, float), np.asarray(scores, float), occluded, box[2] - box[0])
        for note in notes:
            add_flag(flags, "UNCERTAIN_CLASS", note)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    elements, uncertain = [], []
    for kp in cfg["keypoints"]:
        idx = kp.get("index")
        if idx is None or idx >= len(coords):
            elements.append(_element(kp, cx, cy, "outside", 0.0, width, height))
            continue
        x, y, s = float(coords[idx][0]), float(coords[idx][1]), float(scores[idx])
        if not (0 <= x < width and 0 <= y < height):
            state = "outside"  # ngoài khung hình
        elif s >= visible:
            state = "visible"
        elif s >= occluded:
            state = "occluded"
        else:
            state = "outside"
        element = _element(kp, x, y, state, s, width, height)
        element["source"] = SOURCE_OF_INDEX[idx]
        elements.append(element)
        if state == "occluded":
            uncertain.append(element)
    if uncertain:
        add_flag(flags, "ATTRIBUTE_CHECK",
                 f"{len(uncertain)} điểm đặt Occluded theo độ tin cậy của model ({_named(uncertain)}): "
                 "kiểm tra vị trí ước lượng; không còn căn cứ để nội suy thì đổi sang Outside.")
    body_checks(elements, rule, flags)
    for e in elements:
        e.pop("source", None)
    return _proposal(cfg, elements, score, flags, box)


def body_proposals(engine: Optional[Engine], state: Any, image: np.ndarray, cfg: dict, rules: dict, body,
                   warnings: list[str], boxes: Optional[list[list[float]]] = None) -> list[dict]:
    rule = rules.get("pose") or {}
    height, width = image.shape[:2]
    min_h = float(rule.get("min_person_height_px", 0))
    limit = int(rule.get("max_people", 0))
    scored: list[tuple[list[float], float]] = []
    extra = 0
    if boxes is None:
        dets = suppress(detect(engine, state, [dict(cfg, role="object", shape="rectangle")], warnings=warnings), 0.6, 1.0)
        skipped = 0
        for det in dets:
            box = G.clamp_box(det.box, width, height)
            if min_h and box[3] - box[1] < min_h:
                skipped += 1
                continue
            scored.append((box, det.score))
        if skipped:
            warnings.append(f"{skipped} người cao dưới {min_h:.0f}px không gán (đếm vào báo cáo).")
        if limit and len(scored) > limit:
            scored.sort(key=lambda item: -G.box_area(item[0]))
            extra = len(scored) - limit
            scored = scored[:limit]
    else:
        scored = [(G.clamp_box(b, width, height), 1.0) for b in boxes]
    if not scored:
        return []
    results = body.estimate(image, [b for b, _ in scored])
    out = []
    for (box, score), (coords, scores) in zip(scored, results):
        proposal = body_skeleton(cfg, np.asarray(coords, float), np.asarray(scores, float), box, score, width, height, rule)
        if extra:
            add_flag(proposal["flags"], "UNCERTAIN_SCOPE",
                     f"Thấy {extra + limit} người; tool chọn người chiếm diện tích lớn nhất làm người lái. Nếu sai, vẽ hộp quanh đúng người lái.")
        if min_h and box[3] - box[1] < min_h * 1.2:
            add_flag(proposal["flags"], "LOW_VISIBILITY", f"Người chỉ cao {box[3] - box[1]:.0f}px, sát ngưỡng {min_h:.0f}px.")
        out.append(proposal)
    return out


# -------------------------------------------------------------- khuôn mặt --
def path_point(landmarks: np.ndarray, path: list[int], t: float, by: str) -> np.ndarray:
    """Vị trí `t` (0..1) trên đường nối các điểm `path`: theo chiều rộng giữa hai đầu mút (chord) hoặc theo chiều dài."""
    pts = landmarks[path].astype(float)
    if len(pts) == 1:
        return pts[0]
    if by == "chord":
        axis = pts[-1] - pts[0]
        length2 = float(axis @ axis) or 1.0
        param = ((pts - pts[0]) @ axis) / length2
    else:
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        total = float(seg.sum()) or 1.0
        param = np.concatenate([[0.0], np.cumsum(seg) / total])
    t = min(max(float(t), 0.0), 1.0)
    for i in range(len(pts) - 1):
        a, b = param[i], param[i + 1]
        if (a <= t <= b or b <= t <= a) and a != b:
            return pts[i] + (pts[i + 1] - pts[i]) * ((t - a) / (b - a))
    return pts[0] if t <= param[0] else pts[-1]


def face_far_side(landmarks: np.ndarray, rule: dict) -> tuple[Optional[str], bool]:
    """Mặt nghiêng: (phía trái/phải THEO ẢNH của nửa mặt ở xa camera, nghiêng tới mức phía đó bị che chưa).

    Đo bằng tỉ lệ khoảng cách ngang từ chóp mũi tới hai khoé mắt ngoài. Mặt nhìn thẳng: tỉ lệ gần 1.
      - từ `ratio`: nghiêng rõ, nửa mặt phía xa bị thu ngắn -> chỉ gắn cờ để người annotate soi kỹ;
      - từ `occluded_ratio`: chóp mũi gần như đè lên khoé mắt phía xa -> lông mày và mắt phía xa coi là bị che.
    """
    yaw = rule.get("yaw") or {}
    if not yaw:
        return None, False
    try:
        nose, left, right = landmarks[int(yaw["nose"])], landmarks[int(yaw["left_corner"])], landmarks[int(yaw["right_corner"])]
    except (KeyError, IndexError):
        return None, False
    dl, dr = abs(nose[0] - left[0]), abs(nose[0] - right[0])
    ratio = max(dl, dr) / max(min(dl, dr), 1e-6)
    if ratio < float(yaw.get("ratio", 2.5)):
        return None, False
    side = "left" if dl < dr else "right"  # chóp mũi lệch về phía nào thì nửa mặt phía đó ở xa camera
    return side, ratio >= float(yaw.get("occluded_ratio", 5.0))


def face_skeleton(cfg: dict, landmarks: np.ndarray, width: int, height: int, far: tuple[Optional[str], bool],
                  occluders: list[tuple[str, np.ndarray]]) -> Optional[dict]:
    elements, covered_by = [], {}
    placed = []
    for kp in cfg["keypoints"]:
        # `path` + `t`: vị trí trên một đường nối các điểm MediaPipe. Profile kiểu cũ chỉ ghi `index` (một điểm MediaPipe).
        path = kp.get("path") or ([int(kp["index"])] if kp.get("index") is not None else None)
        if not path or max(path) >= len(landmarks):
            elements.append(None)
            continue
        x, y = path_point(landmarks, path, kp.get("t", 0.0), kp.get("by", "length"))
        placed.append((x, y))
        state = "visible"
        if not (0 <= x < width and 0 <= y < height):
            state = "outside"
        else:
            for name, mask in occluders:
                if mask[int(y), int(x)]:
                    state = "occluded"
                    covered_by[name] = covered_by.get(name, 0) + 1
                    break
        elements.append(_element(kp, x, y, state, 1.0, width, height))
    if not placed:
        return None
    xs, ys = zip(*placed)
    box = [min(xs), min(ys), max(xs), max(ys)]
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    elements = [e if e is not None else _element(kp, cx, cy, "outside", 0.0, width, height)
                for e, kp in zip(elements, cfg["keypoints"])]
    flags: list[dict] = []
    total = len(elements)
    need = int(cfg.get("min_visible") or math.ceil(total / 2))
    seen = sum(1 for e in elements if not e["occluded"] and not e["outside"])
    if covered_by:
        what = ", ".join(f"{name} ({count} điểm)" for name, count in covered_by.items())
        if seen < need:
            # Guideline mục 3.1: thấy dưới ngưỡng thì Outside cả skeleton, trừ khi có đủ bằng chứng thị giác.
            for e in elements:
                e["occluded"], e["outside"] = False, True
            add_flag(flags, "ATTRIBUTE_CHECK",
                     f"Chỉ thấy {seen}/{total} điểm (bị che bởi {what}), dưới ngưỡng {need}/{total}: đặt Outside cả skeleton. "
                     "Nếu vẫn có đủ bằng chứng thị giác thì đặt đủ điểm và đánh Occluded.")
        else:
            add_flag(flags, "ATTRIBUTE_CHECK",
                     f"Bị che bởi {what}: các điểm đó đặt Occluded ở vị trí ước lượng. Kiểm tra lại; kính trong suốt thì vẫn là Visible.")
    far_side, hidden = far
    if far_side and cfg.get("face_side") == far_side and not all(e["outside"] for e in elements):
        if hidden:
            for e in elements:
                if not e["outside"]:
                    e["occluded"] = True
            add_flag(flags, "ATTRIBUTE_CHECK",
                     "Mặt nghiêng mạnh: skeleton phía xa camera đặt Occluded. Kiểm tra các điểm không bị dồn về giữa mặt; "
                     "không ước lượng được thì đặt Outside cả skeleton.")
        else:
            add_flag(flags, "UNCERTAIN_BOUNDARY",
                     "Mặt nghiêng: skeleton này ở nửa mặt phía xa camera. Phóng to kiểm tra các điểm không bị dồn về giữa mặt; "
                     "điểm nào không thấy trực tiếp thì đổi sang Occluded.")
    return _proposal(cfg, elements, 1.0, flags, box)


def face_proposals(image: np.ndarray, cfgs: list[dict], rules: dict, face, warnings: list[str],
                   occluders: Optional[Callable[[], list[tuple[str, np.ndarray]]]] = None,
                   region: Optional[list[float]] = None, driver: Optional[list[float]] = None) -> list[dict]:
    """Tìm mặt một lần cho cả ảnh rồi dựng skeleton cho từng label mặt (7 skeleton của Facial Landmark-50).

    Nhiều mặt hơn giới hạn: ưu tiên mặt nằm trong vùng của người lái đã chọn (`driver`), rồi tới mặt lớn nhất.
    `occluders` là hàm tìm vật che bằng SAM3; chỉ được gọi khi ảnh có mặt, để ảnh không có mặt khỏi tốn thời gian.
    """
    rule = rules.get("face") or {}
    height, width = image.shape[:2]
    offset, crop = np.zeros(2), image
    if region is not None:
        x1, y1, x2, y2 = region
        pad_x, pad_y = (x2 - x1) * 0.3, (y2 - y1) * 0.3
        x1, y1 = int(max(0, x1 - pad_x)), int(max(0, y1 - pad_y))
        x2, y2 = int(min(width, x2 + pad_x)), int(min(height, y2 + pad_y))
        crop, offset = image[y1:y2, x1:x2], np.array([x1, y1], float)
    faces = [np.asarray(lm, float) + offset for lm in (face.detect(np.ascontiguousarray(crop)) if crop.size else [])]
    used = sorted({i for cfg in cfgs for kp in cfg["keypoints"]
                   for i in (kp.get("path") or ([kp["index"]] if kp.get("index") is not None else []))})

    def extent(lm: np.ndarray) -> list[float]:
        pts = lm[[i for i in used if i < len(lm)]]
        return [float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())]

    faces = [(lm, extent(lm)) for lm in faces if len(lm) and used]
    min_px = float(rule.get("min_face_px", 20))
    small = [f for f in faces if f[1][2] - f[1][0] < min_px]
    faces = [f for f in faces if f[1][2] - f[1][0] >= min_px]
    if small:
        warnings.append(f"Bỏ qua {len(small)} khuôn mặt nhỏ hơn {min_px:.0f}px.")
    limit = int(rule.get("max_faces", 0))
    extra = 0
    if region is not None and faces:
        faces = sorted(faces, key=lambda f: -G.box_inter(f[1], region))[:1]
    elif limit and len(faces) > limit:
        def on_driver(box: list[float]) -> bool:
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            return bool(driver) and driver[0] <= cx <= driver[2] and driver[1] <= cy <= driver[3]

        faces.sort(key=lambda f: (not on_driver(f[1]), -G.box_area(f[1])))
        extra, faces = len(faces) - limit, faces[:limit]
    out = []
    covers = occluders() if occluders and faces else []
    for landmarks, _ in faces:
        far = face_far_side(landmarks, rule)
        for cfg in cfgs:
            proposal = face_skeleton(cfg, landmarks, width, height, far, covers)
            if proposal is None:
                continue
            if extra:
                add_flag(proposal["flags"], "UNCERTAIN_SCOPE",
                         f"Thấy {extra + limit} khuôn mặt; tool chọn mặt của người đã chọn làm người lái, hoặc mặt lớn nhất. "
                         "Nếu sai, vẽ hộp quanh đúng mặt người lái.")
            out.append(proposal)
    return out


def find_occluders(engine: Engine, state: Any, rules: dict, warnings: list[str]) -> list[tuple[str, np.ndarray]]:
    """Vật che mặt (kính râm, tay, khẩu trang...) do SAM3 tìm: điểm nằm dưới chúng không thể là Visible."""
    out = []
    for prompt in (rules.get("face") or {}).get("occluders") or []:
        masks = [inst.mask.astype(bool) for inst in run_prompt(engine, state, prompt, 0.5, prompt, warnings)]
        if masks:
            out.append((prompt, np.logical_or.reduce(masks)))
    return out


# ------------------------------------------------------------- điều phối ---
def _existing_duplicates(proposals: list[dict], existing: list[dict], iou_thr: float) -> None:
    for proposal in proposals:
        for shape in existing:
            if shape.get("type") != "skeleton" or shape.get("label_id") != proposal["label_id"]:
                continue
            bbox = shape_bbox(shape)
            if bbox and G.box_iou(bbox, proposal["bbox"]) >= iou_thr:
                add_flag(proposal["flags"], "DUPLICATE", "Trùng với một skeleton đã có trên frame (ví dụ pre-label).")
                proposal["checked"] = False
                break


def _in_label_order(proposals: list[dict], cfgs: list[dict]) -> list[dict]:
    """Xếp đề xuất theo thứ tự label của task (person, rồi 7 skeleton mặt theo thứ tự Point ID), không theo bảng chữ cái."""
    position = {c["label_id"]: i for i, c in enumerate(cfgs)}
    proposals.sort(key=lambda p: position.get(p["label_id"], len(position)))
    for i, proposal in enumerate(proposals):
        proposal["pid"] = i + 1
    return proposals


def _split(cfgs: list[dict]) -> tuple[list[dict], list[dict]]:
    active = [c for c in cfgs if c["enabled"] and c["supported"] and c["shape"] == "skeleton"]
    return [c for c in active if c["role"] == "body"], [c for c in active if c["role"] == "face"]


def propose(engine: Engine, state: Any, image: np.ndarray, profile: dict, cfgs: list[dict],
            existing: list[dict], body_engine, face_engine) -> dict:
    """body_engine / face_engine là LazyEngine: chỉ nạp khi job có skeleton tương ứng."""
    started = time.time()
    height, width = image.shape[:2]
    rules = profile.get("rules", {})
    warnings: list[str] = []
    proposals: list[dict] = []
    bodies, faces = _split(cfgs)
    if bodies:
        model = body_engine.get()
        if model is None:
            warnings.append(f"Chưa gợi ý được skeleton người: không nạp được model {body_engine.what}. {body_engine.error or ''}")
        else:
            for cfg in bodies:
                proposals += body_proposals(engine, state, image, cfg, rules, model, warnings)
    if faces:
        model = face_engine.get()
        if model is None:
            warnings.append(f"Chưa gợi ý được skeleton mặt: không nạp được model {face_engine.what}. {face_engine.error or ''}")
        else:
            people = [p for p in proposals if any(not e["outside"] for e in p["elements"])]
            driver = people[0]["bbox"] if len(people) == 1 else None
            found = face_proposals(image, faces, rules, model, warnings,
                                   lambda: find_occluders(engine, state, rules, warnings), driver=driver)
            if not found:
                warnings.append("Không tìm thấy khuôn mặt nào. Ảnh không có mặt thì không cần gán; nếu có, vẽ hộp quanh mặt.")
            proposals += found
    _existing_duplicates(proposals, existing, float((rules.get("dedup") or {}).get("existing_iou", 0.5)))
    return {
        "width": width, "height": height, "profile_id": profile["id"],
        "proposals": _in_label_order(proposals, cfgs), "warnings": warnings, "seconds": round(time.time() - started, 2),
    }


def interactive(image: np.ndarray, profile: dict, cfg: dict, cfgs: list[dict], box: list[float], body_engine, face_engine) -> dict:
    """Người dùng vẽ hộp quanh người lái (hoặc khuôn mặt người lái): đặt skeleton cho đúng đối tượng đó.

    Với mặt, một hộp cho ra mọi skeleton mặt đang bật (cả 7 skeleton), không chỉ label đang chọn.
    """
    height, width = image.shape[:2]
    rules = profile.get("rules", {})
    warnings: list[str] = []
    lazy = body_engine if cfg["role"] == "body" else face_engine
    model = lazy.get()
    proposals: list[dict] = []
    if model is None:
        warnings.append(f"Không nạp được model {lazy.what}. {lazy.error or ''}")
    elif cfg["role"] == "body":
        proposals = body_proposals(None, None, image, cfg, rules, model, warnings, boxes=[box])
    else:
        faces = _split(cfgs)[1] or [cfg]
        proposals = face_proposals(image, faces, rules, model, warnings, region=box)
        if not proposals:
            warnings.append("Không tìm thấy khuôn mặt trong hộp. Thử vẽ hộp rộng hơn một chút.")
    return {"width": width, "height": height, "proposals": _in_label_order(proposals, cfgs), "warnings": warnings}
