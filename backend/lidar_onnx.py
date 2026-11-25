"""Bộ dò vật 3D trong point cloud bằng model ONNX (0.9.8): CenterPoint pillar 0.2 m, huấn luyện trên nuScenes.

Thay bước "gom cụm rồi đoán class theo bảng cỡ" của lidar.detect bằng một model học máy thật sự nhận ra xe / người
trong point cloud. Không có file model thì backend dùng hình học như cũ.

File .onnx xuất bằng scripts/export_centerpoint_onnx.py (chạy trên Colab) từ checkpoint của MMDetection3D
  centerpoint_pillar02_second_secfpn_head-circlenms_8xb4-cyclic-20e_nus-3d (code Apache-2.0; dữ liệu nuScenes: chỉ
  dùng phi thương mại / nghiên cứu).
Đồ thị ONNX chỉ gồm phần mạng; hai đầu nằm ở đây, theo đúng cấu hình gốc:
  - trước: chia point cloud thành pillar 0.2 x 0.2 m trong vùng [-51.2, 51.2] m, tối đa 20 điểm / pillar, 40000 pillar;
    mỗi điểm 5 giá trị (x, y, z, cường độ, độ trễ sweep) -> voxels (N, 20, 5), num_points (N), coors (N, 4) = [b, z, y, x]
  - sau: 3 output (heatmap 10 lớp; box 6 task x [reg 2, z 1, dim 3, vel 2]; hướng 6 task x [sin, cos]) trên lưới
    128x128 (stride 4) -> sigmoid, top-K từng task, toạ độ = (ô + reg) * 4 * 0.2 - 51.2, kích thước = exp(dim),
    yaw = atan2(sin, cos), lọc tâm trong [-61.2, 61.2], ngưỡng điểm, circle NMS theo bán kính của từng task.
Dữ liệu khác nuScenes: tool dời point cloud cho mặt đất về độ cao mặt đất của nuScenes (cảm biến cao ~1.84 m) trước khi
chạy rồi dời box về lại; cường độ điểm không có thì để 0 (model vẫn chạy, kém hơn một chút).
"""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

# ------------------------------------------------------------- cấu hình gốc ---
PC_RANGE = (-51.2, -51.2, -5.0, 51.2, 51.2, 3.0)
VOXEL = (0.2, 0.2, 8.0)
MAX_POINTS = 20
MAX_VOXELS = 40000
OUT_STRIDE = 4
POST_RANGE = (-61.2, -61.2, -10.0, 61.2, 61.2, 10.0)
CLASS_NAMES = ("car", "truck", "construction_vehicle", "bus", "trailer", "barrier", "motorcycle", "bicycle",
               "pedestrian", "traffic_cone")
TASKS = ((0,), (1, 2), (3, 4), (5,), (6, 7), (8, 9))            # chỉ số lớp của từng task, theo thứ tự heatmap
MIN_RADIUS = (4.0, 12.0, 10.0, 1.0, 0.85, 0.175)                 # circle NMS: khoảng cách bình phương (m²) mỗi task
POST_MAX = 83
NUSC_GROUND_Z = -1.84                                            # mặt đất trong hệ toạ độ LiDAR nuScenes
# lớp nuScenes -> class_key của tool (lidar.class_key). traffic_cone không có ở tool: bỏ.
TO_TOOL = {"car": "car", "truck": "truck", "construction_vehicle": "construction vehicle", "bus": "bus",
           "trailer": "trailer", "barrier": "barrier", "motorcycle": "two wheels", "bicycle": "two wheels",
           "pedestrian": "pedestrian"}


# --------------------------------------------------------------- pillar ---
def voxelize(points: np.ndarray, intensity: Optional[np.ndarray] = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """points (N, 3+) -> voxels (V, 20, 5) float32, num_points (V,) int32, coors (V, 4) int32 [0, z, y, x]."""
    pts = np.asarray(points, dtype=np.float32)
    x0, y0, z0, x1, y1, z1 = PC_RANGE
    keep = (pts[:, 0] >= x0) & (pts[:, 0] < x1) & (pts[:, 1] >= y0) & (pts[:, 1] < y1) & (pts[:, 2] >= z0) & (pts[:, 2] < z1)
    pts = pts[keep]
    feats = np.zeros((len(pts), 5), np.float32)
    feats[:, :3] = pts[:, :3]
    if intensity is not None:
        feats[:, 3] = np.asarray(intensity, np.float32)[keep]
    elif pts.shape[1] >= 4:
        feats[:, 3] = pts[:, 3]
    nx = int(round((x1 - x0) / VOXEL[0]))
    ny = int(round((y1 - y0) / VOXEL[1]))
    if not len(pts):
        return np.zeros((0, MAX_POINTS, 5), np.float32), np.zeros((0,), np.int32), np.zeros((0, 4), np.int32)
    ix = np.clip(((pts[:, 0] - x0) / VOXEL[0]).astype(np.int64), 0, nx - 1)
    iy = np.clip(((pts[:, 1] - y0) / VOXEL[1]).astype(np.int64), 0, ny - 1)
    key = iy * nx + ix
    order = np.argsort(key, kind="stable")
    k = key[order]
    uniq, start, counts = np.unique(k, return_index=True, return_counts=True)
    sel = np.ones(len(uniq), bool)
    if len(uniq) > MAX_VOXELS:                                  # giữ pillar dày điểm nhất
        sel[:] = False
        sel[np.argsort(-counts, kind="stable")[:MAX_VOXELS]] = True
    group = np.repeat(np.arange(len(uniq)), counts)
    rank = np.arange(len(k)) - np.repeat(start, counts)
    remap = np.cumsum(sel) - 1
    ok = (rank < MAX_POINTS) & sel[group]
    n = int(sel.sum())
    voxels = np.zeros((n, MAX_POINTS, 5), np.float32)
    voxels[remap[group[ok]], rank[ok]] = feats[order][ok]
    num_points = np.minimum(counts[sel], MAX_POINTS).astype(np.int32)
    coors = np.zeros((n, 4), np.int32)
    coors[:, 2] = (uniq[sel] // nx).astype(np.int32)
    coors[:, 3] = (uniq[sel] % nx).astype(np.int32)
    return voxels, num_points, coors


# --------------------------------------------------------------- giải mã ---
def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def _split_outputs(outs: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Nhận 3 output theo số kênh (10 heatmap, 6*8 hoặc 6*6 box, 12 hướng), không theo tên hay thứ tự.

    Thứ tự kênh trong box của mỗi task: reg (2), height (1), dim (3), vel (2) — đúng như scripts/export_centerpoint_onnx.py
    ghép; rot mỗi task: [sin, cos] (CenterHead của mmdet3d)."""
    arrs = [np.asarray(o, np.float32) for o in outs]
    arrs = [a[0] if a.ndim == 4 else a for a in arrs]
    heat = next((a for a in arrs if a.ndim == 3 and a.shape[0] == len(CLASS_NAMES)), None)
    rot = next((a for a in arrs if a.ndim == 3 and a.shape[0] == 2 * len(TASKS)), None)
    box = next((a for a in arrs if a.ndim == 3 and a.shape[0] in (8 * len(TASKS), 6 * len(TASKS))), None)
    if heat is None or rot is None or box is None:
        raise ValueError("Output của model 3D không đúng dạng CenterPoint nuScenes (cần 10 / 48 / 12 kênh): "
                         + ", ".join(str(a.shape) for a in arrs))
    return heat, box, rot


def circle_nms(xy: np.ndarray, scores: np.ndarray, thresh: float, post_max: int = POST_MAX) -> list[int]:
    """Giữ box điểm cao, bỏ box có tâm cách gần hơn sqrt(thresh) mét (như circle_nms của mmdet3d). Trả chỉ số."""
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    removed = np.zeros(len(order), bool)
    for a, i in enumerate(order):
        if removed[a]:
            continue
        keep.append(int(i))
        if len(keep) >= post_max:
            break
        d2 = ((xy[order[a + 1:]] - xy[i]) ** 2).sum(axis=1)
        removed[a + 1:] |= d2 <= thresh
    return keep


def decode(outs: list, score_thr: float = 0.25, max_num: int = 500) -> list[dict]:
    """Output mạng -> [{center (x, y, z giữa), size (l, w, h), yaw, score, name}] trong hệ toạ độ đầu vào."""
    heat, box, rot = _split_outputs(outs)
    per = box.shape[0] // len(TASKS)                             # 8 (có vận tốc) hoặc 6
    _, H, W = heat.shape
    out: list[dict] = []
    for ti, classes in enumerate(TASKS):
        hm = _sigmoid(heat[list(classes)])                       # (k, H, W)
        flat = hm.reshape(-1)
        k = min(max_num, flat.size)
        idx = np.argpartition(-flat, k - 1)[:k]
        idx = idx[flat[idx] >= score_thr]
        if not len(idx):
            continue
        cls_local, cell = np.divmod(idx, H * W)
        ys, xs = np.divmod(cell, W)
        b = box[ti * per:(ti + 1) * per]
        cx = (xs + b[0, ys, xs]) * OUT_STRIDE * VOXEL[0] + PC_RANGE[0]
        cy = (ys + b[1, ys, xs]) * OUT_STRIDE * VOXEL[1] + PC_RANGE[1]
        z = b[2, ys, xs]                                         # tâm theo chiều cao (mmdet3d trừ h/2 sau khi decode)
        dims = np.exp(np.clip(b[3:6, ys, xs], -10, 6)).T          # norm_bbox=True: mạng dự đoán log(kích thước)
        yaw = np.arctan2(rot[2 * ti, ys, xs], rot[2 * ti + 1, ys, xs])
        sc = flat[idx]
        inside = (cx >= POST_RANGE[0]) & (cx <= POST_RANGE[3]) & (cy >= POST_RANGE[1]) & (cy <= POST_RANGE[4]) & \
                 (z >= POST_RANGE[2]) & (z <= POST_RANGE[5])
        sel = np.nonzero(inside)[0]
        if not len(sel):
            continue
        for j in sel[circle_nms(np.c_[cx[sel], cy[sel]], sc[sel], MIN_RADIUS[ti])]:
            out.append({"center": [float(cx[j]), float(cy[j]), float(z[j])], "size": [float(v) for v in dims[j]],
                        "yaw": float(yaw[j]), "score": float(sc[j]), "name": CLASS_NAMES[classes[int(cls_local[j])]]})
    out.sort(key=lambda d: -d["score"])
    return out


def points_in_box(points: np.ndarray, center: list[float], size: list[float], yaw: float) -> int:
    """Số điểm nằm trong cuboid (xoay quanh trục z)."""
    if not len(points):
        return 0
    d = np.asarray(points, np.float32)[:, :3] - np.asarray(center, np.float32)
    c, s = math.cos(-yaw), math.sin(-yaw)
    lx, ly = d[:, 0] * c - d[:, 1] * s, d[:, 0] * s + d[:, 1] * c
    return int(np.count_nonzero((np.abs(lx) <= size[0] / 2) & (np.abs(ly) <= size[1] / 2) & (np.abs(d[:, 2]) <= size[2] / 2)))



# ------------------------------------------------------------------ model ---
class CenterPointONNX:
    """Phiên ONNX Runtime của CenterPoint. Provider GPU nếu có (runtime_tools.pick_onnx_providers), CPU dự phòng."""

    def __init__(self, path: str, providers: Optional[list[str]] = None, options: Optional[dict] = None, session: Any = None):
        self.path = path
        self.providers = providers or ["CPUExecutionProvider"]
        self.options = options or {}
        self.session = session
        self.error: Optional[str] = None
        self.inputs: dict[str, str] = {}

    @property
    def ready(self) -> bool:
        return self.session is not None

    def load(self) -> "CenterPointONNX":
        if self.session is None:
            try:
                import onnxruntime as ort

                kwargs: dict[str, Any] = {"providers": self.providers}
                if "CUDAExecutionProvider" in self.providers and self.options.get("cuda"):
                    kwargs["provider_options"] = [self.options["cuda"]] + [{}] * (len(self.providers) - 1)
                self.session = ort.InferenceSession(self.path, **kwargs)
            except Exception as exc:  # noqa: BLE001 - file hỏng / thiếu onnxruntime: báo, dùng hình học
                self.error = f"không nạp được model 3D ONNX: {exc}"
                self.session = None
                return self
        names = [i.name for i in self.session.get_inputs()]
        if not {"voxels", "num_points", "coors"} <= set(names):
            self.error = f"model 3D ONNX cần đầu vào voxels / num_points / coors, file này có: {', '.join(names)}"
            self.session = None
            return self
        self.inputs = {i.name: str(i.type) for i in self.session.get_inputs()}
        return self

    def release(self, reason: str = "") -> bool:
        if self.session is None:
            return False
        self.session = None
        return True

    def run(self, points: np.ndarray) -> list[dict]:
        voxels, num_points, coors = voxelize(points)
        if not len(voxels):
            return []

        def cast(name: str, arr: np.ndarray) -> np.ndarray:
            return arr.astype(np.int64) if "int64" in self.inputs.get(name, "") else arr

        feed = {"voxels": voxels, "num_points": cast("num_points", num_points), "coors": cast("coors", coors)}
        return decode(self.session.run(None, feed))


def detect(model: CenterPointONNX, points: np.ndarray, labels: dict[str, int], rule: Optional[dict] = None,
           score_thr: float = 0.3) -> dict:
    """Một frame bằng model 3D. Cùng dạng kết quả với lidar.detect: {objects, warnings, ground_ratio, stats}."""
    from . import lidar as L

    rule = rule or {}
    plane, ratio = L.ground_plane(points)
    dz = NUSC_GROUND_Z - float(plane[2])                         # dời mặt đất về độ cao mặt đất nuScenes
    shifted = np.asarray(points, np.float32).copy()
    shifted[:, 2] += dz
    found = model.run(shifted)
    ranges = dict(L.DEFAULT_RANGES, **(rule.get("ranges") or {}))
    stats = {k: 0 for k in L.REASONS}
    out, warnings = [], []
    for d in found:
        key = TO_TOOL.get(d["name"])
        if key is None or key not in labels or d["score"] < score_thr:
            continue
        center = [d["center"][0], d["center"][1], d["center"][2] - dz]
        dist = math.hypot(center[0], center[1])
        if dist > ranges.get(key, 60.0):
            stats["range"] += 1
            continue
        n = points_in_box(points, center, d["size"], d["yaw"])
        need = L.min_points_at(dist, key, rule)
        flags = []
        if n < need:
            flags.append({"code": "LOW_VISIBILITY", "message": f"Chỉ {n} điểm LiDAR ở {dist:.0f} m: kích thước, hướng có thể sai."})
        checked = d["score"] >= 0.4
        conf = d["score"]
        seen = L._looks_dropped(key, d["size"], rule.get("dropped") or [])
        if seen >= 2:
            checked = False
            stats["remembered"] += 1
            conf *= 0.5
            flags.append({"code": "LEARNED", "message": f"Giống vật bạn đã bỏ {seen} lần (cùng class, cùng cỡ): tool bỏ tick sẵn."})
        out.append({"center": [round(v, 3) for v in center], "size": [round(v, 3) for v in d["size"]],
                    "yaw": round(L._wrap(d["yaw"]), 4), "points": n, "gap": 0.0, "label_id": labels[key], "cls": key,
                    "score": round(d["score"], 3), "confidence": round(conf, 3), "distance": round(dist, 1),
                    "min_points": need, "flags": flags, "checked": checked, "source": "centerpoint"})
    if ratio < 0.2:
        warnings.append("Không tìm được mặt đường rõ ràng: đáy cuboid có thể lệch, kiểm tra ở Side view.")
    return {"objects": out, "warnings": warnings, "ground_ratio": round(ratio, 3), "stats": stats, "engine": "centerpoint"}
