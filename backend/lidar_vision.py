"""Đối chiếu camera cho job 3D bằng bộ dò 2D ONNX chạy GPU (0.9.7, spec 3D v2 mục 3.2C).

Tách khỏi SAM3 để job 3D không đụng CUDA context của SAM3 (nguồn lỗi cudaErrorIllegalAddress cũ):
  - Batch inference: gộp ảnh camera của một frame chạy một lượt qua model 2D ONNX (RT-DETR / D-FINE đã chọn trong
    app desktop). Không có model thì mọi hàm trả "không khả dụng": luồng hình học thuần LiDAR vẫn chạy như 0.9.6.
  - Frustum Splitting: cụm LiDAR quá rộng (xe đỗ sát container, xe khác) thì chiếu hộp 2D thành chóp chiếu vào không
    gian 3D và tách đúng tập điểm của chiếc xe.
  - 3D-to-2D Verification: chiếu mỗi cuboid lên camera của nó. Khu chiếu trống (cột điện, thân cây, thùng rác) thì hạ
    mạnh tin cậy và bỏ tick sẵn; khu chiếu có vật khác class thì gắn cờ để người duyệt xem.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from . import camera_rig

CONFIRM_IOU = 0.3            # IoU 2D tối thiểu để coi cuboid được bộ dò 2D xác nhận
MIN_IOU = 0.05               # dưới mức này: coi như không thấy gì ở camera đó
# class_key của lidar -> id lớp COCO mà model 2D dùng. Class không có trong bảng thì không đối chiếu được.
COCO_FOR_3D = {"pedestrian": 0, "two wheels": 3, "car": 2, "bus": 5, "truck": 7, "trailer": 7,
               "construction vehicle": 7, "barrier": 0}


# ------------------------------------------------------------- bộ dò 2D ---
class Vision2D:
    """Bộ dò vật 2D ONNX. Nạp lười; provider do runtime_tools.pick_onnx_providers chọn (GPU nếu có, CPU dự phòng)."""

    def __init__(self, path: str, providers: list[str], options: Optional[dict] = None):
        self.path = path
        self.providers = providers
        self.options = options or {}
        self.session: Optional[Any] = None
        self.in_hw: tuple[int, int] = (640, 640)
        self.error: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.session is not None

    def load(self) -> "Vision2D":
        try:
            import onnxruntime as ort
        except ImportError as exc:
            self.error = f"thiếu onnxruntime: {exc}"
            return self
        kwargs: dict[str, Any] = {"providers": self.providers}
        if "CUDAExecutionProvider" in self.providers and self.options.get("cuda"):
            kwargs["provider_options"] = [self.options["cuda"]] + [{}] * (len(self.providers) - 1)
        try:
            self.session = ort.InferenceSession(self.path, **kwargs)
            shape = self.session.get_inputs()[0].shape
            # cạnh ảnh động ("height"/"width"): RT-DETR / D-FINE huấn luyện ở 640x640
            h = shape[2] if len(shape) > 3 and isinstance(shape[2], int) and shape[2] > 0 else 640
            w = shape[3] if len(shape) > 3 and isinstance(shape[3], int) and shape[3] > 0 else 640
            self.in_hw = (int(h), int(w))
        except Exception as exc:  # noqa: BLE001 - model hỏng / thiếu DLL: báo lỗi, không làm sập request
            self.error = f"không nạp được model ONNX: {exc}"
            self.session = None
        return self

    def release(self, reason: str = "") -> bool:
        if self.session is None:
            return False
        self.session = None
        return True

    def detect(self, image_rgb: np.ndarray, thr: float = 0.35) -> list[dict]:
        """Ảnh RGB -> [{box: [x0, y0, x1, y1] pixel ảnh gốc, score, coco_id}]."""
        if self.session is None:
            return []
        import cv2

        h, w = image_rgb.shape[:2]
        ih, iw = self.in_hw
        x = cv2.resize(image_rgb, (iw, ih), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))[None]
        try:
            out = self.session.run(None, {self.session.get_inputs()[0].name: x})
        except Exception as exc:  # noqa: BLE001
            self.error = f"suy luận lỗi: {exc}"
            return []
        dets = _decode(out, thr)
        # 0.9.8: họ DETR trả toạ độ CHUẨN HOÁ 0..1 (cxcywh) -> nhân thẳng với cỡ ảnh gốc. Bản cũ nhân w/iw (coi là pixel
        # của ảnh 640) nên mọi box co về góc trái trên và không khớp khu chiếu nào. Bản export trả pixel đầu vào thì
        # vẫn quy đổi như cũ.
        normalized = all(max(d["box"]) <= 1.5 for d in dets)
        sx, sy = (w, h) if normalized else (w / iw, h / ih)
        return [{"box": [d["box"][0] * sx, d["box"][1] * sy, d["box"][2] * sx, d["box"][3] * sy],
                 "score": d["score"], "coco_id": d["id"]} for d in dets]

    def detect_batch(self, images: list[np.ndarray], thr: float = 0.35) -> list[list[dict]]:
        """Nhiều ảnh một lượt (mỗi ảnh một lần chạy; ONNX Runtime tự xếp hàng trên cùng session)."""
        return [self.detect(img, thr) for img in images]


class MockVision:
    """Bộ dò giả cho test và cho lúc chưa chọn model: mọi ảnh trả danh sách rỗng."""

    ready = False
    error = None
    in_hw = (640, 640)

    def __init__(self, boxes: Optional[list[dict]] = None):
        self.boxes = boxes or []

    def load(self):
        return self

    def release(self, reason: str = "") -> bool:
        return False

    def detect(self, image_rgb: np.ndarray, thr: float = 0.35) -> list[dict]:
        return [dict(b) for b in self.boxes]

    def detect_batch(self, images, thr: float = 0.35):
        return [self.detect(img, thr) for img in images]


def _decode(out: list, thr: float) -> list[dict]:
    """Đọc output họ DETR (RT-DETR / D-FINE): boxes (1,N,4) chuẩn hoá cxcywh và logits (1,N,số lớp).

    0.9.8: thứ tự output khác nhau giữa các bản export (bản onnx-community/rtdetr_r18vd trả [logits, pred_boxes]) nên
    nhận theo hình dạng: mảng có chiều cuối = 4 là box. Điểm dùng sigmoid (RT-DETR / D-FINE học bằng focal loss, mỗi
    lớp độc lập, không có lớp nền); softmax trên 80 lớp làm điểm bị bóp nhỏ, gần như không box nào qua ngưỡng."""
    if not out or len(out) < 2:
        return []
    arrs = [np.asarray(o, dtype=float) for o in out[:2]]
    if arrs[0].shape[-1] != 4 and arrs[1].shape[-1] == 4:
        arrs = arrs[::-1]
    if arrs[0].shape[-1] != 4:
        return []
    boxes = arrs[0].reshape(-1, 4)
    logits = arrs[1].reshape(-1, arrs[1].shape[-1])[:len(boxes)]
    if len(logits) != len(boxes) or not logits.size:
        return []
    probs = _sigmoid(logits)
    best, ids = probs.max(axis=1), probs.argmax(axis=1)
    res = []
    for (cx, cy, bw, bh), s, i in zip(boxes, best, ids):
        if s < thr or bw <= 0 or bh <= 0:
            continue
        res.append({"box": [float(cx - bw / 2), float(cy - bh / 2), float(cx + bw / 2), float(cy + bh / 2)],
                    "score": float(s), "id": int(i)})
    return res


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(axis=1, keepdims=True))
    return e / np.maximum(e.sum(axis=1, keepdims=True), 1e-9)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


# ------------------------------------------------------ frustum splitting ---
def frustum_split(cluster: np.ndarray, cam: dict, det_box: list[float], width: int, height: int,
                  offset: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Tách cụm LiDAR dính nhau theo chóp chiếu của hộp 2D (spec 3D v2 mục 3.2C).

    Điểm thuộc vật được dò nếu góc phương vị của nó nằm trong khoảng cột ảnh của hộp 2D. Phần còn lại là vật khác bị
    dính vào cụm. det_box theo pixel ảnh gốc; width/height là kích thước ảnh đó.
    Trả (điểm của vật, điểm dư).
    """
    if not len(cluster):
        return cluster, cluster[:0]
    u_lo, u_hi = det_box[0] / max(width, 1), det_box[2] / max(width, 1)
    az_lo = cam["yaw"] + (u_lo - 0.5) * cam["fov"]
    az_hi = cam["yaw"] + (u_hi - 0.5) * cam["fov"]
    az = np.degrees(np.arctan2(cluster[:, 1], cluster[:, 0])) + offset
    az = (az + 180.0) % 360.0 - 180.0
    inside = (az >= min(az_lo, az_hi) - 1e-9) & (az <= max(az_lo, az_hi) + 1e-9)
    return cluster[inside], cluster[~inside]


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def split_wide_clusters(vision: Vision2D, images: dict[str, np.ndarray], objects: list[dict], clusters: list[np.ndarray],
                        rig: dict, offset: float = 0.0, max_width_m: float = 3.0) -> list[np.ndarray]:
    """Cụm rộng hơn max_width_m: thử tách bằng hộp 2D của camera nhìn thấy nó. Tách được thì thay cụm cũ bằng 2 phần."""
    cams = {c["name"]: c for c in rig["cameras"]}
    out: list[np.ndarray] = []
    cache: dict[str, list[dict]] = {}
    for cluster in clusters:
        if not len(cluster) or len(cluster) < 20:
            out.append(cluster)
            continue
        span = float(max(cluster[:, 0].ptp() if hasattr(cluster[:, 0], "ptp") else np.ptp(cluster[:, 0]),
                         np.ptp(cluster[:, 1])))
        if span <= max_width_m:
            out.append(cluster)
            continue
        centre = [float(cluster[:, 0].mean()), float(cluster[:, 1].mean()), float(cluster[:, 2].mean())]
        size = [span, span, max(0.5, float(np.ptp(cluster[:, 2])))]
        p = camera_rig.project(centre, size, [dict(c) for c in cams.values()], offset)
        if p is None or p["camera"] not in images:
            out.append(cluster)
            continue
        name = p["camera"]
        img = images[name]
        if name not in cache:
            found = []
            if hasattr(vision, "detect"):
                try:
                    found = vision.detect(img) or []
                except Exception:  # noqa: BLE001 - model lỗi: coi như không thấy, không làm sập cả lượt
                    found = []
            cache[name] = found
        h, w = img.shape[:2]
        # hộp 2D nằm trong dải cột của cụm hiện tại
        lo, hi = (p["u"] - p["half"]) * w, (p["u"] + p["half"]) * w
        near = [d for d in cache[name] if d["box"][2] >= lo and d["box"][0] <= hi]
        parts: list[np.ndarray] = []
        rest = cluster
        for d in sorted(near, key=lambda d: -d["score"])[:2]:
            inside, rest = frustum_split(rest, cams[name], d["box"], w, h, offset)
            if len(inside) >= 15:
                parts.append(inside)
        # tách được ít nhất một vật: giữ vật đó, phần dư đủ lớn thì thành cụm riêng (vật khác bị dính)
        if parts and sum(len(x) for x in parts) >= 15:
            out.extend(parts)
            if len(rest) >= 30:
                out.append(rest)
        else:
            out.append(cluster)
    return out


def _box_iou(a: list[float], b: list[float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return inter / max(area_a + area_b - inter, 1e-6)


# -------------------------------------------------- 3D-to-2D verification ---
_VERDICT_RANK = {"confirmed": 3, "unknown": 2, "partial": 1, "none": 0}


def verify(vision, images: list[tuple[str, np.ndarray]], objects: list[dict], rig: dict,
           offset: float = 0.0, allowed_coco: Optional[set[int]] = None) -> dict:
    """Chiếu mỗi cuboid lên camera nhìn thấy nó, khớp với vật 2D đã dò.

    objects: [{id, center, size, cls, ...}]. Trả {results: {id: {camera, u, verdict, score, iou}}, offset, source}.
      confirmed — hộp 2D cùng class phủ khu chiếu (IoU >= CONFIRM_IOU)
      partial   — có vật 2D nhưng chỉ phủ một phần (bị che, hoặc cuboid hơi lệch)
      unknown   — có vật 2D nhưng khác class (dễ nhầm Car/Truck)
      none      — không camera nào thấy gì: nghi rác
    """
    out: dict = {"results": {}, "offset": offset, "source": rig.get("source", "none"), "notes": list(rig.get("notes") or [])}
    cams = {c["name"]: c for c in rig["cameras"]}
    imgs = dict(images)
    dets: dict[str, list[dict]] = {}

    def boxes_of(name: str) -> list[dict]:
        """Kết quả dò 2D của một camera (mỗi camera dò một lần cho cả frame)."""
        if name not in dets:
            found = []
            if name in imgs and hasattr(vision, "detect"):
                try:
                    found = vision.detect(imgs[name]) or []
                except Exception:  # noqa: BLE001 - model lỗi: coi như không thấy, không làm sập cả lượt
                    found = []
            if allowed_coco:
                found = [d for d in found if d["coco_id"] in allowed_coco]
            dets[name] = found
        return dets[name]

    for o in objects:
        p = camera_rig.project(o["center"], o["size"], list(cams.values()), offset)
        if p is None:
            out["results"][o["id"]] = {"verdict": "none", "score": 0.0, "iou": 0.0, "camera": None, "u": None}
            continue
        name = p["camera"]
        img = imgs.get(name)
        if img is None:
            out["results"][o["id"]] = {"verdict": "none", "score": 0.0, "iou": 0.0, "camera": name, "u": p["u"]}
            continue
        h, w = img.shape[:2]
        band = 0.3 * h                                        # dải dọc quanh tầm mắt: đủ rộng cho mọi cỡ vật
        proj = [(p["u"] - p["half"]) * w, 0.5 * h - band, (p["u"] + p["half"]) * w, 0.5 * h + band]
        want = COCO_FOR_3D.get(str(o.get("cls") or "").strip().lower())
        best, best_iou, same = None, 0.0, False
        for d in boxes_of(name):
            iou = _box_iou(proj, d["box"])
            if iou > best_iou:
                best, best_iou = d, iou
                same = want is None or d["coco_id"] == want
        if best is None or best_iou < MIN_IOU:
            entry = {"verdict": "none", "score": 0.0, "iou": 0.0}
        elif best_iou >= CONFIRM_IOU:
            entry = {"verdict": "confirmed" if same else "unknown", "score": round(float(best["score"]), 3), "iou": round(best_iou, 3)}
        else:
            entry = {"verdict": "partial", "score": round(float(best["score"]), 3), "iou": round(best_iou, 3)}
        entry.update(camera=name, u=p["u"])
        prev = out["results"].get(o["id"])
        if prev is None or _VERDICT_RANK[entry["verdict"]] > _VERDICT_RANK.get(prev["verdict"], 0):
            out["results"][o["id"]] = entry
    return out


def images_map(images: list[tuple[str, np.ndarray]]) -> dict[str, np.ndarray]:
    return dict(images)


def apply(vision_result: dict, objects: list[dict]) -> int:
    """Áp kết quả vào đề xuất: confirmed +0.2; unknown giữ nhưng gắn cờ; none bỏ tick + hạ 0.4 lần (nghi rác)."""
    changed = 0
    results = vision_result.get("results") or {}
    for o in objects:
        r = results.get(o.get("id"))
        if not r:
            continue
        o["vision2d"] = r
        conf = float(o.get("confidence", o.get("score", 0.5)))
        if r["verdict"] == "confirmed":
            o["confidence"] = round(min(1.0, conf + 0.2), 3)
        elif r["verdict"] == "none":
            o["checked"] = False
            o["confidence"] = round(conf * 0.4, 3)
            o.setdefault("flags", []).append({
                "code": "UNCERTAIN_CLASS",
                "message": f"Ảnh {r.get('camera')}: khu chiếu không có vật 2D nào. Có thể là rác (cột, thùng rác): tool bỏ tick."})
            changed += 1
        elif r["verdict"] == "unknown":
            o.setdefault("flags", []).append({
                "code": "UNCERTAIN_CLASS",
                "message": f"Ảnh {r.get('camera')}: thấy vật khác class tại chỗ này (IoU {r.get('iou')}) — xem lại class."})
            changed += 1
    return changed
