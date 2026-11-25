"""Cuboid 3D trên point cloud LiDAR (CVAT 3D), 0.9.4.

Không cần model 3D (các model 3D có sẵn khó cài trên Windows và nhiều bộ trọng số cấm dùng thương mại). Tool làm bằng
hình học, rồi để người duyệt:

1. Đọc point cloud của frame (.pcd ascii / binary / binary_compressed, .bin kiểu KITTI, hoặc file zip chứa chúng).
2. Tách mặt đường bằng RANSAC (mặt phẳng thấp nhất), bỏ điểm của chính xe (quanh gốc toạ độ) và điểm quá xa.
3. Gom cụm điểm trên lưới nhìn từ trên xuống (BEV).
4. Mỗi cụm một cuboid: hình chữ nhật nhỏ nhất bao các điểm (cho dài, rộng, góc yaw), đáy chạm mặt đường, cao tới
   điểm cao nhất.
5. Đoán class theo bảng kích thước của guideline (Car, Truck, Bus, Pedestrian...). Hai class cùng khớp thì gắn cờ
   UNCERTAIN_CLASS; ít điểm thì LOW_VISIBILITY.
6. Theo dõi qua các frame (guideline 3D Cuboid): mỗi track khoá kích thước theo frame có nhiều điểm nhất (±5%), yaw theo
   hướng chuyển động, bị che tối đa N frame thì nội suy và bật occluded, lâu hơn thì kết thúc track.

Kết quả cuboid theo định dạng CVAT 3D: [x, y, z, rx, ry, rz, dài, rộng, cao] + 7 số 0.
"""
from __future__ import annotations

import io
import math
import re
import struct
import zipfile
from typing import Optional

import numpy as np

from .profiles import _ascii, norm

# Bảng kích thước (mét) của guideline 3D Cuboid V1 (mục 4.3): dùng khi guideline của project không có bảng riêng.
DEFAULT_SIZES: dict[str, list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]] = {
    "car": [((3.8, 5.0), (1.70, 1.90), (1.40, 1.60)), ((4.4, 5.3), (1.80, 2.10), (1.65, 2.10))],
    "truck": [((5.2, 6.2), (1.85, 2.10), (1.80, 2.00)), ((5.5, 9.0), (2.00, 2.55), (2.40, 3.50)), ((6.0, 12.0), (2.40, 2.60), (3.00, 4.00))],
    "trailer": [((8.0, 16.0), (2.50, 2.60), (3.50, 4.10)), ((2.0, 5.0), (1.50, 2.20), (0.80, 2.50))],
    "bus": [((10.0, 12.5), (2.50, 2.60), (3.00, 3.50)), ((17.0, 18.5), (2.50, 2.60), (3.00, 3.40))],
    "construction vehicle": [((4.0, 12.0), (2.00, 3.20), (2.50, 4.00))],
    "pedestrian": [((0.4, 0.9), (0.5, 0.9), (1.50, 1.95)), ((0.3, 0.6), (0.3, 0.6), (0.90, 1.40))],
    "two wheels": [((1.6, 1.9), (0.50, 0.70), (1.60, 1.90)), ((1.8, 2.3), (0.70, 0.95), (1.40, 1.80)), ((1.6, 2.2), (0.50, 0.90), (1.00, 1.30))],
    "barrier": [((1.0, 3.0), (0.40, 0.70), (0.80, 1.10))],
}
ALIASES = {"motorcycle": "two wheels", "bicycle": "two wheels", "two wheeler": "two wheels", "cyclist": "two wheels",
           "person": "pedestrian", "people": "pedestrian", "van": "car", "suv": "car"}


# ------------------------------------------------------------- đọc file ---
def _lzf_decompress(data: bytes, out_len: int) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        ctrl = data[i]
        i += 1
        if ctrl < 32:
            out += data[i:i + ctrl + 1]
            i += ctrl + 1
        else:
            length = ctrl >> 5
            ref = len(out) - ((ctrl & 0x1F) << 8) - 1
            if length == 7:
                length += data[i]
                i += 1
            ref -= data[i]
            i += 1
            for _ in range(length + 2):
                out.append(out[ref])
                ref += 1
    if len(out) != out_len:
        raise ValueError("PCD nén hỏng (độ dài sau giải nén không khớp).")
    return bytes(out)


def parse_pcd(data: bytes) -> np.ndarray:
    """PCD -> mảng N x 3 (x, y, z), bỏ điểm NaN."""
    header, pos = {}, 0
    while True:
        end = data.index(b"\n", pos)
        line = data[pos:end].decode("ascii", "replace").strip()
        pos = end + 1
        if not line or line.startswith("#"):
            continue
        key, _, rest = line.partition(" ")
        header[key.upper()] = rest.split()
        if key.upper() == "DATA":
            break
    fields = [f.lower() for f in header["FIELDS"]]
    sizes = [int(s) for s in header["SIZE"]]
    types = header["TYPE"]
    counts = [int(c) for c in header.get("COUNT", ["1"] * len(fields))]
    n = int(header["POINTS"][0]) if "POINTS" in header else int(header["WIDTH"][0]) * int(header.get("HEIGHT", ["1"])[0])
    kind = header["DATA"][0].lower()
    np_type = {("F", 4): "<f4", ("F", 8): "<f8", ("I", 1): "<i1", ("I", 2): "<i2", ("I", 4): "<i4", ("U", 1): "<u1",
               ("U", 2): "<u2", ("U", 4): "<u4", ("I", 8): "<i8", ("U", 8): "<u8"}
    dtype = np.dtype([(f"{f}_{k}" if c > 1 else f, np_type[(t, s)]) for f, s, t, c in zip(fields, sizes, types, counts) for k in range(c)])
    if kind == "ascii":
        rows = np.loadtxt(io.StringIO(data[pos:].decode("ascii", "replace")), ndmin=2)
        cols = {name: i for i, name in enumerate(dtype.names)}
        pts = np.stack([rows[:, cols["x"]], rows[:, cols["y"]], rows[:, cols["z"]]], 1).astype(np.float32)
    elif kind == "binary":
        arr = np.frombuffer(data[pos:pos + n * dtype.itemsize], dtype=dtype, count=n)
        pts = np.stack([arr["x"], arr["y"], arr["z"]], 1).astype(np.float32)
    elif kind == "binary_compressed":
        comp, raw = struct.unpack("<II", data[pos:pos + 8])
        buf = _lzf_decompress(data[pos + 8:pos + 8 + comp], raw)
        cols, off = {}, 0
        for name in dtype.names:              # dạng nén lưu theo cột
            dt = dtype.fields[name][0]
            cols[name] = np.frombuffer(buf[off:off + n * dt.itemsize], dtype=dt, count=n)
            off += n * dt.itemsize
        pts = np.stack([cols["x"], cols["y"], cols["z"]], 1).astype(np.float32)
    else:
        raise ValueError(f"Kiểu dữ liệu PCD '{kind}' chưa hỗ trợ.")
    return pts[np.isfinite(pts).all(axis=1)]


def read_points(data: bytes, name: str = "") -> np.ndarray:
    """Point cloud từ dữ liệu CVAT trả về: PCD, .bin (x, y, z, cường độ float32) hoặc zip chứa một trong hai."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            inner = next((n for n in z.namelist() if n.lower().endswith((".pcd", ".bin"))), None)
            if inner is None:
                raise ValueError("File zip không có point cloud (.pcd / .bin).")
            return read_points(z.read(inner), inner)
    head = data[:200].lstrip()
    if head.startswith(b"#") or head.upper().startswith(b"VERSION") or b"FIELDS" in head[:200]:
        return parse_pcd(data)
    if name.lower().endswith(".bin") or len(data) % 16 == 0:
        arr = np.frombuffer(data[: len(data) // 16 * 16], dtype="<f4").reshape(-1, 4)[:, :3]
        return arr[np.isfinite(arr).all(axis=1)].astype(np.float32)
    raise ValueError("Không nhận ra định dạng point cloud.")


# --------------------------------------------------------- mặt đường ---
def ground_plane(points: np.ndarray, iterations: int = 120, tol: float = 0.15, seed: int = 0) -> tuple[np.ndarray, float]:
    """RANSAC trên các điểm thấp: mặt phẳng z = ax + by + c (pháp tuyến gần thẳng đứng). Trả (a, b, c), tỉ lệ điểm khớp."""
    rng = np.random.default_rng(seed)
    if len(points) < 50:
        return np.array([0.0, 0.0, float(np.percentile(points[:, 2], 5)) if len(points) else 0.0]), 0.0
    low = points[points[:, 2] <= np.percentile(points[:, 2], 40)]
    best, best_n = np.array([0.0, 0.0, float(np.median(low[:, 2]))]), 0
    for _ in range(iterations):
        s = low[rng.choice(len(low), 3, replace=False)]
        A = np.c_[s[:, 0], s[:, 1], np.ones(3)]
        try:
            coef = np.linalg.solve(A, s[:, 2])
        except np.linalg.LinAlgError:
            continue
        if abs(coef[0]) > 0.15 or abs(coef[1]) > 0.15:      # dốc quá 8.5 độ: không phải mặt đường
            continue
        n = int(np.count_nonzero(np.abs(low[:, 0] * coef[0] + low[:, 1] * coef[1] + coef[2] - low[:, 2]) < tol))
        if n > best_n:
            best, best_n = coef, n
    inl = low[np.abs(low[:, 0] * best[0] + low[:, 1] * best[1] + best[2] - low[:, 2]) < tol]
    if len(inl) >= 3:                                         # làm khớp lại bằng bình phương tối thiểu
        best = np.linalg.lstsq(np.c_[inl[:, 0], inl[:, 1], np.ones(len(inl))], inl[:, 2], rcond=None)[0]
    return best, best_n / max(len(low), 1)


def ground_z(plane: np.ndarray, x: float, y: float) -> float:
    return float(plane[0] * x + plane[1] * y + plane[2])


# ------------------------------------------------- mặt đất cục bộ (0.9.6, numba 0.9.7) ---
try:
    from numba import njit as _njit
    HAS_NUMBA = True
except ImportError:  # noqa: BLE001 - numba là tuỳ chọn: máy không cài được vẫn chạy bản numpy
    _njit = None
    HAS_NUMBA = False


if HAS_NUMBA:
    @_njit(cache=True)
    def _ground_cells(ij: np.ndarray, z: np.ndarray, min_pts: int, pct: float) -> tuple[np.ndarray, np.ndarray]:
        """Mỗi ô lưới: mức thấp percentile của điểm gần mặt phẳng. Numba: nhanh gấp ~20 lần vòng lặp numpy."""
        # ij đã sort theo (i, j); chạy dò biên từng ô
        keys = np.empty((len(ij), 2), np.int64)
        vals = np.empty(len(ij), np.float64)
        n = 0
        k = 0
        while k < len(ij):
            i0, j0 = ij[k, 0], ij[k, 1]
            e = k
            while e < len(ij) and ij[e, 0] == i0 and ij[e, 1] == j0:
                e += 1
            if e - k >= min_pts:
                seg = np.sort(z[k:e])
                pos = int(pct / 100.0 * (len(seg) - 1))
                keys[n, 0], keys[n, 1], vals[n] = i0, j0, seg[pos]
                n += 1
            k = e
        return keys[:n], vals[:n]


class LocalGround:
    """Mặt đất theo từng ô 8 m (đường dốc, lề cao, bãi đỗ thấp hơn mặt đường): mỗi ô lấy mức thấp của các điểm gần
    mặt phẳng chung; ô ít điểm dùng mặt phẳng chung. Đáy cuboid đặt theo ô dưới vật, không theo một mặt phẳng."""

    def __init__(self, points: np.ndarray, plane: np.ndarray, cell: float = 8.0, band: float = 0.6):
        self.plane, self.cell = plane, cell
        self.grid: dict[tuple[int, int], float] = {}
        if not len(points):
            return
        pz = points[:, 0] * plane[0] + points[:, 1] * plane[1] + plane[2]
        near = np.abs(points[:, 2] - pz) < band
        pts = points[near]
        if not len(pts):
            return
        ij = np.floor(pts[:, :2] / cell).astype(np.int64)
        order = np.lexsort((ij[:, 1], ij[:, 0]))
        ij, z = ij[order], pts[order, 2]
        if HAS_NUMBA and len(ij) > 2000:               # bản numba chỉ đáng giá khi nhiều ô
            keys, vals = _ground_cells(np.ascontiguousarray(ij), np.ascontiguousarray(z), 15, 20.0)
            for (a, b), v in zip(keys, vals):
                self.grid[(int(a), int(b))] = float(v)
        else:
            keys, start = np.unique(ij, axis=0, return_index=True)
            bounds = list(start[1:]) + [len(z)]
            for k, a, b in zip(keys, start, bounds):
                if b - a >= 15:
                    self.grid[(int(k[0]), int(k[1]))] = float(np.percentile(z[a:b], 20))

    def z(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        base = self.plane[0] * x + self.plane[1] * y + self.plane[2]
        if not self.grid:
            return base
        i, j = np.floor(x / self.cell).astype(np.int64), np.floor(y / self.cell).astype(np.int64)
        flat_i, flat_j, flat_b = np.atleast_1d(i), np.atleast_1d(j), np.atleast_1d(base).astype(float).copy()
        for n in range(len(flat_b)):
            g = self.grid.get((int(flat_i[n]), int(flat_j[n])))
            if g is not None:
                flat_b[n] = g
        return flat_b.reshape(np.shape(base)) if np.ndim(base) else float(flat_b[0])


# --------------------------------------------------------------- cụm ---
def clusters(points: np.ndarray, ground, *, cell: float = 0.25, max_range: float = 60.0, ego: float = 2.5,
             min_height: float = 0.25, max_height: float = 5.0, min_points: int = 3) -> list[np.ndarray]:
    """Điểm không thuộc mặt đất -> các cụm (lưới BEV, ô 25 cm, nối 8 hướng). ground: LocalGround hoặc mặt phẳng."""
    from scipy import ndimage

    if not len(points):
        return []
    gz = ground.z(points[:, 0], points[:, 1]) if hasattr(ground, "z") else \
        points[:, 0] * ground[0] + points[:, 1] * ground[1] + ground[2]
    hz = points[:, 2] - gz
    r = np.hypot(points[:, 0], points[:, 1])
    keep = (hz > min_height) & (hz < max_height) & (r < max_range) & ~((np.abs(points[:, 0]) < ego) & (np.abs(points[:, 1]) < ego * 0.6))
    pts = points[keep]
    if not len(pts):
        return []
    ij = np.floor(pts[:, :2] / cell).astype(np.int64)
    mn = ij.min(axis=0)
    ij -= mn
    grid = np.zeros(tuple(ij.max(axis=0) + 1), bool)
    grid[ij[:, 0], ij[:, 1]] = True
    lab, n = ndimage.label(grid, structure=np.ones((3, 3), bool))
    ids = lab[ij[:, 0], ij[:, 1]]
    groups = [pts[ids == k] for k in range(1, n + 1)]
    groups = _merge_sparse([g for g in groups if len(g)])
    return [c for c in groups if len(c) >= min_points]


def _merge_sparse(groups: list[np.ndarray]) -> list[np.ndarray]:
    """0.9.6: vật ở xa có điểm thưa nên bị vỡ thành nhiều cụm nhỏ: nối các cụm nhỏ nằm sát nhau, khoảng nối tăng theo
    khoảng cách (25 cm + 1 cm mỗi mét). Cụm lớn (>= 200 điểm) giữ nguyên để không dính hai xe đỗ sát nhau.

    0.9.7: tìm cặp ứng viên bằng scipy cKDTree thay vì so mọi cặp (O(n²) chậm khi nhiều cụm); kết quả không đổi.
    """
    small = [i for i, g in enumerate(groups) if len(g) < 200]
    if len(small) < 2:
        return groups
    boxes = {i: (groups[i][:, 0].min(), groups[i][:, 1].min(), groups[i][:, 0].max(), groups[i][:, 1].max()) for i in small}
    parent = {i: i for i in small}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def far(i) -> float:
        b = boxes[i]
        return math.hypot((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

    # ứng viên = cụm có điểm nằm trong bán kính nối lớn nhất có thể (khoảng cách hộp bao >= khoảng cách điểm, nên
    # lọc bằng KDTree là tập cha của các cặp đủ gần; lọc lại theo hộp bao bên dưới)
    eps_max = 0.25 + 0.01 * max(far(i) for i in small) + 1e-6
    try:
        from scipy.spatial import cKDTree

        pos = {g: n for n, g in enumerate(small)}          # chỉ số cụm -> vị trí trong `small`
        allp = np.vstack([groups[i][:, :2] for i in small]).astype(np.float64)
        own = np.concatenate([np.full(len(groups[i]), pos[i], np.int64) for i in small])
        pairs = cKDTree(allp).query_pairs(eps_max, output_type="ndarray")
        cand = set()
        for a, b in pairs:
            pa, pb = int(own[a]), int(own[b])
            if pa != pb:
                cand.add((small[pa], small[pb]) if pa < pb else (small[pb], small[pa]))
    except (ImportError, ValueError, MemoryError, IndexError):   # thiếu scipy hoặc quá nhiều điểm: giữ cách cũ
        cand = {(a, b) for pos2, a in enumerate(small) for b in small[pos2 + 1:]}
    for a, b in sorted(cand):
        ba, bb = boxes[a], boxes[b]
        dx = max(bb[0] - ba[2], ba[0] - bb[2], 0.0)
        dy = max(bb[1] - ba[3], ba[1] - bb[3], 0.0)
        if math.hypot(dx, dy) <= min(0.25 + 0.01 * far(a), 0.25 + 0.01 * far(b)):
            parent[find(a)] = find(b)
    merged: dict[int, list[np.ndarray]] = {}
    for i in small:
        merged.setdefault(find(i), []).append(groups[i])
    big = [g for i, g in enumerate(groups) if i not in parent]
    return big + [np.vstack(v) for v in merged.values()]


def lshape_fit(c: np.ndarray) -> Optional[tuple[float, float, float, float, float]]:
    """L-shape fitting (0.9.7, spec 3D v2 mục 3.2B): khung chữ nhật ôm sát cạnh vật nhất.

    Lấy bao lồi các điểm nhìn từ trên, thử khung theo từng cạnh bao lồi. Khác cv2.minAreaRect ở chỗ minAreaRect chỉ
    tối thiểu diện tích nên hay phình/co box khi cụm dính vật khác hoặc thiếu điểm một phía.

    Tiêu chí chấm điểm (trên TẤT CẢ điểm cụm, không chỉ đỉnh bao lồi):
      - độ khít = trung vị khoảng cách từ điểm tới biên khung gần nhất: khung ôm sát vật thì phần lớn điểm nằm sát
        biên; khung rộng hơn vật nhiều (chồm qua vật khác) thì trung vị lớn -> loại (0.45 m);
      - trong các khung đủ khít, chọn diện tích nhỏ nhất (ổn định, ít phụ thuộc nhiễu).

    Trả (cx, cy, dài, rộng, yaw) hoặc None nếu không đủ điểm."""
    if len(c) < 5:
        return None
    pts = c[:, :2].astype(np.float64)
    try:
        from scipy.spatial import ConvexHull

        hull = ConvexHull(pts)
        corners = pts[hull.vertices]
        edges = np.roll(corners, -1, axis=0) - corners
    except Exception:  # noqa: BLE001 - điểm thẳng hàng / quá ít: không phải hull
        return None
    lens = np.hypot(edges[:, 0], edges[:, 1])
    good = lens > 0.3                                    # cạnh ngắn hơn 30 cm: bỏ (nhiễu, hoặc mép cắt mẫu)
    if not good.any():
        return None
    best = None
    for ang in np.arctan2(edges[good, 1], edges[good, 0]):
        ca, sa = math.cos(ang), math.sin(ang)
        u = pts @ np.array([ca, sa])
        v = pts @ np.array([-sa, ca])
        span_u, span_v = float(u.max() - u.min()), float(v.max() - v.min())
        if span_u < 0.4 or span_v < 0.4:                 # cụm dẹt thành đường: không phải vật
            continue
        area = span_u * span_v
        # độ khít trên mọi điểm: khoảng cách tới biên khung gần nhất, lấy trung vị
        du = np.minimum(u.max() - u, u - u.min())
        dv = np.minimum(v.max() - v, v - v.min())
        near = np.minimum(du, dv)
        fit = float(np.median(near))
        if fit > 0.45:                                   # khung rộng hơn vật nhiều: không phải cạnh vật
            continue
        # ổn định hơn diện tích trần: diện tích nhân hệ số phạt theo độ khít
        score = area * (1.0 + 2.0 * fit)
        if best is not None and score >= best[0]:
            continue
        cu, cv_ = (u.max() + u.min()) / 2, (v.max() + v.min()) / 2
        cx, cy = cu * ca - cv_ * sa, cu * sa + cv_ * ca
        length, width, yaw = span_u, span_v, ang
        if width > length:
            length, width, yaw = width, length, yaw + math.pi / 2
        best = (score, cx, cy, length, width, yaw)
    if best is None:
        return None
    _, cx, cy, length, width, yaw = best
    return float(cx), float(cy), length, width, float(yaw)


def fit_box(c: np.ndarray, ground, lshape: bool = True) -> dict:
    """Cuboid cho một cụm: chữ nhật nhỏ nhất bao điểm nhìn từ trên (dài, rộng, yaw), đáy chạm mặt đất cục bộ.
    `gap`: điểm thấp nhất của cụm cao hơn mặt đất bao nhiêu mét (vật lơ lửng: biển, tán cây).

    0.9.7: mặc định thử L-shape fitting trước (khung ôm sát cạnh vật); cụm quá ít điểm hoặc không khít thì quay về
    cv2.minAreaRect như cũ."""
    import cv2

    result = lshape_fit(c) if (lshape and len(c) >= 20) else None
    if result is not None:
        cx, cy, length, width, yaw = result
    else:
        (cx, cy), (a, b), ang = cv2.minAreaRect(c[:, :2].astype(np.float32))
        yaw = math.radians(ang)
        length, width = float(a), float(b)
        if width > length:
            length, width, yaw = width, length, yaw + math.pi / 2
    base = float(ground.z(cx, cy)) if hasattr(ground, "z") else ground_z(ground, cx, cy)
    top = float(c[:, 2].max())
    height = max(top - base, 0.2)
    return {"center": [float(cx), float(cy), base + height / 2], "size": [max(length, 0.2), max(width, 0.2), height],
            "yaw": _wrap(yaw), "points": int(len(c)), "gap": round(float(c[:, 2].min()) - base, 2)}


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


# ------------------------------------------------------------ class ---
VEHICLES = ("car", "truck", "trailer", "bus", "construction vehicle")
VRU = ("pedestrian", "two wheels")
# Phạm vi gán của guideline 3D Cuboid: xe <= 50 m, người / xe hai bánh <= 40 m, rào chắn <= 30 m
DEFAULT_RANGES = {**{k: 50.0 for k in VEHICLES}, **{k: 40.0 for k in VRU}, "barrier": 30.0}
# Số điểm tối thiểu theo khoảng cách (càng xa càng thưa): [đến m, số điểm]; người / xe hai bánh cần một nửa
DEFAULT_MIN_POINTS = [[20.0, 25], [35.0, 12], [1000.0, 6]]
SPLITTABLE = ("barrier",)          # một dãy rào chắn liền nhau: tách thành từng tấm


def class_key(name: str) -> str:
    key = norm(_ascii(name)).replace("_", " ")
    return ALIASES.get(key, key)


def class_group(key: str) -> str:
    return "vehicle" if key in VEHICLES else ("vru" if key in VRU else key)


def parse_size_table(markdown: str) -> dict[str, list]:
    """Bảng tham chiếu kích thước của guideline: | Loại | Class | Length | Width | Height |, số kiểu '3,8 – 5,0'."""
    from .guideline.importer import tables

    out: dict[str, list] = {}
    rng = re.compile(r"(\d+(?:[.,]\d+)?)\s*[–-]\s*(\d+(?:[.,]\d+)?)")
    for tbl in tables(markdown):
        head = [h.lower() for h in tbl[0]]
        try:
            ci = next(i for i, h in enumerate(head) if "class" in h)
            li = next(i for i, h in enumerate(head) if "length" in h or h.startswith("dài"))
            wi = next(i for i, h in enumerate(head) if "width" in h or h.startswith("rộng"))
            hi = next(i for i, h in enumerate(head) if "height" in h or h.startswith("cao"))
        except StopIteration:
            continue
        for row in tbl[1:]:
            vals = []
            for i in (li, wi, hi):
                m = rng.search(row[i] if i < len(row) else "")
                if not m:
                    break
                vals.append((float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))))
            if len(vals) == 3 and ci < len(row) and row[ci]:
                out.setdefault(class_key(row[ci]), []).append(tuple(vals))
    return out


def parse_ranges(markdown: str) -> dict[str, float]:
    """Phạm vi gán theo nhóm class nếu guideline ghi ('Vehicle ... trong 50 m', 'VRU ≤ 40m', 'Barrier: 30 m')."""
    out: dict[str, float] = {}
    groups = {"vehicle": VEHICLES, "ô tô": VEHICLES, "phương tiện": VEHICLES, "vru": VRU, "pedestrian": ("pedestrian",), "người": ("pedestrian",),
              "two wheel": ("two wheels",), "barrier": ("barrier",), "rào": ("barrier",)}
    for sent in re.split(r"(?<=[.!?\n])", markdown or ""):
        low = sent.lower()
        m = re.search(r"(\d{2,3})\s*m\b", low)
        if not m or not re.search(r"range|phạm vi|khoảng cách|within|trong vòng|≤|<=|tối đa|max", low):
            continue
        for word, keys in groups.items():
            if word in low:
                for k in keys:
                    out.setdefault(k, float(m.group(1)))
    return out


def parse_track_rules(markdown: str) -> dict:
    """Luật track đọc động từ guideline (0.9.7, spec 3D v2 mục 3.2A):
    số keyframe bị che tối đa, sai lệch kích thước cho phép, và class khoá yaw theo trục bề ngang (rào chắn)."""
    out: dict = {}
    text = markdown or ""
    low = text.lower()
    m = re.search(r"(?:occlu\w*|bị che|che khuất)[^.\n]{0,60}?(?:tối đa|max|không quá|≤|<=)[^\d\n]{0,12}(\d{1,2})\b", low)
    if not m:
        m = re.search(r"(?:occlu\w*|bị che|che khuất)[^.\n]{0,80}?(?:keyframe|frame)[^.\n]{0,20}?(\d{1,2})\b", low)
    if not m:
        m = re.search(r"(?:keyframe|frame)[^.\n]{0,30}?(?:tối đa|max|≤|<=)[^\d\n]{0,10}(\d{1,2})\b", low)
    if m:
        out["max_occluded_frames"] = int(m.group(1))
    m = re.search(r"(?:±|\+/-|sai lệch|tolerance)[^\d\n]{0,12}(\d{1,2}(?:[.,]\d+)?)\s*%", low)
    if m:
        out["size_tolerance"] = round(float(m.group(1).replace(",", ".")) / 100, 4)
    by_width = []
    for sent in re.split(r"(?<=[.!?\n])", text):
        s = sent.lower()
        if not re.search(r"yaw|hướng|heading|orientation", s):
            continue
        if re.search(r"bề ngang|chiều ngang|width|ngang|bề rộng", s):
            for name in re.findall(r"[A-Za-z_][A-Za-z_ ]{2,24}", sent):
                key = class_key(name)
                if key in DEFAULT_SIZES and key not in by_width:
                    by_width.append(key)
    if by_width:
        out["yaw_by_width"] = by_width
    return out


def min_points_at(dist: float, key: str, rule: dict) -> int:
    """Số điểm tối thiểu của một cụm ở khoảng cách dist. rule['min_points_scale'][class]: hệ số đã tự chỉnh theo
    cuboid bạn đánh (0.9.6, mục Tự chỉnh của job 3D)."""
    table = rule.get("min_points_by_range") or DEFAULT_MIN_POINTS
    n = next((int(c) for d, c in table if dist <= float(d)), int(table[-1][1]))
    if key in VRU:
        n = n // 2
    scale = float((rule.get("min_points_scale") or {}).get(key, 1.0))
    return max(3, int(round(n * scale)))


def _dim_score(v: float, lo: float, hi: float, tol: float, partial: bool) -> float:
    """1 khi nằm trong khoảng (nới tol); nhỏ hơn: được phép khi một phần bị che (partial), điểm giảm theo tỉ lệ."""
    if lo * (1 - tol) <= v <= hi * (1 + tol):
        return 1.0
    if v > hi * (1 + tol):
        return 0.0                                    # lớn hơn bảng: không phải class này
    if partial:
        return max(0.0, min(1.0, v / lo))
    return max(0.0, 1.0 - (lo - v) / lo * 3)


def classify(size: list[float], table: dict[str, list], allowed: dict[str, int], tol: float = 0.15,
             height_tol: float = 0.12) -> tuple[Optional[int], list[str], float]:
    """(label_id, các class cùng khớp, điểm 0..1).

    0.9.6 (cuboid lệch class): CHIỀU CAO phải khớp bảng (LiDAR gần như luôn thấy đỉnh vật); dài / rộng được nhỏ hơn bảng
    (vật bị che một phần, chỉ thấy một mặt) nhưng không được lớn hơn. Cụm không khớp class nào thì bỏ, không gán bừa."""
    length, width, height = size
    scored = []
    for key, label_id in allowed.items():
        best, partial = 0.0, False
        for ranges in table.get(key) or []:
            (llo, lhi), (wlo, whi), (hlo, hhi) = ranges
            if (wlo + whi) > (llo + lhi):                 # bảng ghi rộng > dài (người): so theo cạnh lớn / nhỏ
                (llo, lhi), (wlo, whi) = (wlo, whi), (llo, lhi)
            hs = _dim_score(height, hlo, hhi, height_tol, partial=False)
            if hs < 0.75:
                continue
            # chỉ thấy một mặt (đuôi / đầu xe): cạnh dài đo được là bề RỘNG của vật -> thử cả hai cách ghép cạnh
            for a, b in ((length, width), (width, length)):
                ls = _dim_score(a, llo, lhi, tol, partial=True)
                ws = _dim_score(b, wlo, whi, tol, partial=True)
                if ls == 0.0 or ws == 0.0 or max(ls, ws) < 1.0:
                    continue                              # lớn hơn bảng; hoặc không cạnh nào đủ (bị che vẫn thấy trọn một mặt)
                # vật cụ thể hơn thắng khi hai class cùng khớp: khoảng hẹp và số đo gần giữa khoảng (xe con 4.5 m là
                # car, không phải trailer 2–5 m)
                spec = []
                for v, (lo, hi) in ((a, (llo, lhi)), (b, (wlo, whi)), (height, (hlo, hhi))):
                    mid, half = (lo + hi) / 2, max((hi - lo) / 2, 1e-3)
                    spec.append(max(0.0, 1 - abs(v - mid) / half) / (1 + (hi - lo) / mid))
                val = 0.5 * hs + 0.25 * ls + 0.25 * ws + 0.2 * float(np.mean(spec))
                if val > best:
                    best, partial = val, min(ls, ws) < 1.0
        if best > 0:
            scored.append((best, key, label_id, partial))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.6:
        return None, [], 0.0
    # gần bằng nhau thì gắn cờ để xem ảnh camera; vật thấy trọn và khớp hẳn một class thì không gắn
    # (một bên chỉ khớp một phần: có thể là vật lớn hơn bị che -> nới khoảng so sánh)
    top, top_partial = scored[0][0], scored[0][3]
    close = [k for s, k, _, p in scored if s >= top - (0.15 if (p or top_partial) else 0.03)]
    return scored[0][2], close, min(1.0, scored[0][0])


def complete_partial(box: dict, rows: list, tol: float = 0.15) -> Optional[str]:
    """Cụm chỉ thấy một phần vật (đuôi xe, nửa xe bị che): xoay cho đúng hướng nếu cạnh dài đo được là bề rộng, kéo
    cạnh thiếu tới cỡ nhỏ nhất của bảng, đẩy tâm ra xa cảm biến (phần khuất nằm phía sau mặt thấy được).
    Trả câu mô tả nếu có sửa."""
    length, width, height = box["size"]
    best = None
    for ranges in rows:
        (llo, lhi), (wlo, whi), _ = ranges
        if (wlo + whi) > (llo + lhi):
            (llo, lhi), (wlo, whi) = (wlo, whi), (llo, lhi)
        for swap, (a, b) in ((False, (length, width)), (True, (width, length))):
            ls, ws = _dim_score(a, llo, lhi, tol, True), _dim_score(b, wlo, whi, tol, True)
            if ls and ws and max(ls, ws) >= 1.0 and (best is None or ls + ws > best[0]):
                best = (ls + ws, swap, a, b, llo, wlo)
    if best is None:
        return None
    _, swap, a, b, llo, wlo = best
    yaw = box["yaw"] + (math.pi / 2 if swap else 0.0)
    new_l, new_w = max(a, llo), max(b, wlo)
    if not swap and new_l == a and new_w == b:
        return None
    ux, uy = math.cos(yaw), math.sin(yaw)               # trục dài mới
    cx, cy = box["center"][:2]
    for grow, (ax, ay) in ((new_l - a, (ux, uy)), (new_w - b, (-uy, ux))):
        if grow > 0:
            sign = 1.0 if cx * ax + cy * ay >= 0 else -1.0     # phía xa cảm biến
            cx, cy = cx + sign * ax * grow / 2, cy + sign * ay * grow / 2
    box["center"] = [float(cx), float(cy), box["center"][2]]
    box["size"] = [float(new_l), float(new_w), height]
    box["yaw"] = _wrap(yaw)
    if new_l > a or new_w > b:
        return f"Chỉ thấy một phần vật: kích thước kéo theo bảng ({new_l:.1f} × {new_w:.1f} m), tâm dời ra phía khuất. Kiểm tra ở Top view."
    return None


def split_row(c: np.ndarray, box: dict, ground, piece: float) -> list[np.ndarray]:
    """Một dãy vật liền nhau (rào chắn): chia cụm theo chiều dài thành từng đoạn cỡ một vật."""
    n = max(2, int(round(box["size"][0] / piece)))
    axis = np.array([math.cos(box["yaw"]), math.sin(box["yaw"])])
    t = (c[:, :2] - np.array(box["center"][:2])) @ axis
    edges = np.linspace(t.min(), t.max() + 1e-6, n + 1)
    return [c[(t >= a) & (t < b)] for a, b in zip(edges, edges[1:])]


# ------------------------------------------------------------- frame ---
REASONS = {"no_class": "kích thước không khớp class nào (chiều cao sai, hoặc lớn hơn bảng)",
           "range": "xa hơn phạm vi gán của class", "few_points": "quá ít điểm so với khoảng cách",
           "floating": "lơ lửng trên mặt đất (biển, tán cây, mái)", "too_big": "quá lớn (tường, nhà, cây)",
           "remembered": "giống vật bạn đã bỏ nhiều lần"}


def _looks_dropped(key: str, size: list[float], dropped: list[dict]) -> int:
    n = 0
    for d in dropped or []:
        if d.get("cls") == key and all(abs(a - b) <= 0.1 * max(b, 0.3) for a, b in zip(size, d.get("size") or [])):
            n += int(d.get("count", 1))
    return n


def detect(points: np.ndarray, labels: dict[str, int], table: dict[str, list], rule: Optional[dict] = None,
           debug: bool = False) -> dict:
    """Một frame: cuboid đề xuất kèm class. labels: {class_key: label_id} của các label cuboid trong task.

    0.9.6: mặt đất cục bộ, phạm vi gán theo class, số điểm tối thiểu theo khoảng cách, bỏ cụm lơ lửng, tách dãy rào
    chắn, bỏ cụm không khớp class nào, hạ cụm giống vật bạn đã bỏ. debug=True trả thêm điểm nhìn từ trên (BEV) và
    các cụm bị bỏ kèm lý do (bản đồ nhỏ và mục "Kiểm tra dữ liệu job 3D" ở panel).

    0.9.7: L-shape fitting cho khung ôm sát cạnh vật (tắt bằng rule['lshape']=False)."""
    rule = rule or {}
    lshape = rule.get("lshape", True) is not False
    plane, ratio = ground_plane(points)
    ground = LocalGround(points, plane) if rule.get("local_ground", True) else plane
    ranges = dict(DEFAULT_RANGES, **(rule.get("ranges") or {}))
    max_gap = float(rule.get("max_base_gap_m", 0.5))
    by_label = {v: k for k, v in labels.items()}
    want_range = max([ranges.get(k, 60.0) for k in labels] or [60.0])
    out, warnings, rejected = [], [], []
    stats = {k: 0 for k in REASONS}
    if ratio < 0.2:
        warnings.append("Không tìm được mặt đường rõ ràng: đáy cuboid có thể lệch, kiểm tra ở Side view.")

    def reject(box, why):
        stats[why] += 1
        if debug and len(rejected) < 300:
            rejected.append({"center": [round(v, 2) for v in box["center"]], "size": [round(v, 2) for v in box["size"]],
                             "yaw": round(box["yaw"], 3), "points": box["points"], "gap": box["gap"], "reason": why})

    def consider(c, box, allow_split=True):
        if box["size"][0] > 20 or box["size"][2] > 4.6:
            return reject(box, "too_big")
        if box["gap"] > max_gap:
            return reject(box, "floating")
        label_id, close, score = classify(box["size"], table, labels)
        if label_id is None:
            # dãy rào chắn liền nhau: tách rồi xét từng tấm
            for key in SPLITTABLE:
                if allow_split and key in labels and table.get(key):
                    (llo, lhi), (wlo, whi), (hlo, hhi) = table[key][0]
                    if box["size"][0] > lhi * 1.2 and box["size"][1] <= whi * 1.3 and hlo * 0.85 <= box["size"][2] <= hhi * 1.15:
                        for part in split_row(c, box, ground, (llo + lhi) / 2):
                            if len(part) >= 3:
                                consider(part, fit_box(part, ground, lshape=lshape), allow_split=False)
                        return None
            return reject(box, "no_class")
        key = by_label[label_id]
        note = complete_partial(box, table.get(key) or [])
        dist = math.hypot(box["center"][0], box["center"][1])
        if dist > ranges.get(key, 60.0):
            return reject(box, "range")
        need = min_points_at(dist, key, rule)
        if box["points"] < need:
            return reject(box, "few_points")
        flags = []
        if len(close) > 1:
            flags.append({"code": "UNCERTAIN_CLASS", "message": f"Kích thước khớp nhiều class: {', '.join(close)}. Xem ảnh camera để chọn."})
        if box["points"] < 2 * need:
            flags.append({"code": "LOW_VISIBILITY", "message": f"Chỉ {box['points']} điểm LiDAR ở {dist:.0f} m: kích thước, hướng có thể sai."})
        if note:
            flags.append({"code": "ATTRIBUTE_CHECK", "message": note})
        if box["gap"] > 0.25:
            flags.append({"code": "ATTRIBUTE_CHECK", "message": f"Phần dưới không có điểm (đáy cách đất {box['gap']:.2f} m): có thể bị che, đáy đặt theo mặt đất."})
        conf = score * min(1.0, box["points"] / (3.0 * need))
        checked = True
        seen = _looks_dropped(key, box["size"], rule.get("dropped") or [])
        if seen >= 2:
            checked = False
            stats["remembered"] += 1
            flags.append({"code": "LEARNED", "message": f"Giống vật bạn đã bỏ {seen} lần (cùng class, cùng cỡ): tool bỏ tick sẵn."})
            conf *= 0.5
        out.append(dict(box, label_id=label_id, cls=key, score=round(score, 3), confidence=round(conf, 3),
                        distance=round(dist, 1), min_points=need, flags=flags, checked=checked))
        return None

    for c in clusters(points, ground, max_range=want_range + (12 if debug else 2)):
        consider(c, fit_box(c, ground, lshape=lshape))
    res = {"objects": out, "warnings": warnings, "ground_ratio": round(ratio, 3), "stats": stats}
    if debug:
        sample = points[np.hypot(points[:, 0], points[:, 1]) < want_range + 5]
        if len(sample) > 4000:
            sample = sample[np.random.default_rng(0).choice(len(sample), 4000, replace=False)]
        gz = ground.z(sample[:, 0], sample[:, 1]) if hasattr(ground, "z") else sample[:, 0] * plane[0] + sample[:, 1] * plane[1] + plane[2]
        res["bev"] = {"points": [[round(float(x), 1), round(float(y), 1), int(h > 0.25)] for (x, y, _), h in zip(sample, sample[:, 2] - gz)],
                      "range": want_range}
        res["rejected"] = rejected
        r = np.hypot(points[:, 0], points[:, 1]) if len(points) else np.zeros(1)
        res["info"] = {"points": int(len(points)), "max_range_m": round(float(r.max()), 1),
                       "ground_z": round(float(plane[2]), 2), "ground_cells": len(getattr(ground, "grid", {}) or {}),
                       "ego_hole": bool(len(points) and np.count_nonzero(r < 2.0) < 0.002 * len(points)),
                       "x_span": [round(float(points[:, 0].min()), 1), round(float(points[:, 0].max()), 1)] if len(points) else [0, 0],
                       "y_span": [round(float(points[:, 1].min()), 1), round(float(points[:, 1].max()), 1)] if len(points) else [0, 0]}
    return res


def cvat_points(center, size, yaw) -> list[float]:
    """Cuboid của CVAT 3D: vị trí (3), góc quay rx, ry, rz (3), kích thước (3), rồi 7 số 0."""
    return [round(float(v), 4) for v in (*center, 0.0, 0.0, yaw, *size)] + [0.0] * 7


# ------------------------------------------------------------- track ---
def build_tracks(frames: list[dict], rule: Optional[dict] = None, names: Optional[dict] = None) -> dict:
    """frames: [{frame, objects}] -> track cuboid theo luật guideline 3D Cuboid (mục 4)."""
    rule = rule or {}
    names = names or {}
    max_hidden = int(rule.get("max_occluded_frames", 6))
    gate = float(rule.get("match_m", 2.5))
    size_tol = float(rule.get("size_tolerance", 0.05))
    tracks: list[dict] = []
    for item in sorted(frames, key=lambda x: x["frame"]):
        f = int(item["frame"])
        objs = item.get("objects") or []
        cand = []
        for ti, t in enumerate(tracks):
            gap = f - t["last"]
            if gap <= 0 or gap > max_hidden + 1:
                continue
            pred = np.array(t["obs"][t["last"]]["center"][:2]) + t["vel"] * gap
            for oi, o in enumerate(objs):
                if o["label_id"] != t["label_id"]:
                    continue
                d = float(np.hypot(*(np.array(o["center"][:2]) - pred)))
                if d <= gate * (1 + 0.3 * (gap - 1)):
                    cand.append((d, ti, oi))
        used_t, used_o = set(), set()
        for d, ti, oi in sorted(cand):
            if ti in used_t or oi in used_o:
                continue
            used_t.add(ti)
            used_o.add(oi)
            t = tracks[ti]
            prev = t["obs"][t["last"]]
            step = f - t["last"]
            v = (np.array(objs[oi]["center"][:2]) - np.array(prev["center"][:2])) / step
            t["vel"] = 0.6 * t["vel"] + 0.4 * v if len(t["obs"]) > 1 else v
            t["obs"][f] = objs[oi]
            t["last"] = f
        for oi, o in enumerate(objs):
            if oi not in used_o:
                tracks.append({"label_id": o["label_id"], "obs": {f: o}, "last": f, "vel": np.zeros(2)})
    out = []
    for i, t in enumerate(sorted(tracks, key=lambda x: min(x["obs"])), 1):
        fs = sorted(t["obs"])
        if len(fs) < int(rule.get("min_track_frames", 2)):
            continue
        ref = max(fs, key=lambda f: t["obs"][f]["points"])           # frame dày điểm nhất: khoá kích thước
        size = list(t["obs"][ref]["size"])
        flags = []
        for f in fs:
            s = t["obs"][f]["size"]
            if t["obs"][f]["points"] >= 0.5 * t["obs"][ref]["points"] and any(abs(a - b) / b > 3 * size_tol for a, b in zip(s, size)):
                flags.append({"code": "ATTRIBUTE_CHECK", "message": f"Kích thước ở frame {f} khác frame chuẩn ({ref}) hơn {int(3 * size_tol * 100)}%: "
                                                                     "xem có phải vật đổi hình (mở cửa, bốc hàng) hay cụm điểm dính vật khác."})
                break
        # yaw: theo hướng chuyển động khi vật đi đủ nhanh, không thì theo hình hộp; giữ liên tục, không lật 180°
        yaws = []
        for k, f in enumerate(fs):
            o = t["obs"][f]
            yaw = o["yaw"]
            if 0 < k < len(fs) - 1:
                a, b = np.array(t["obs"][fs[k - 1]]["center"][:2]), np.array(t["obs"][fs[k + 1]]["center"][:2])
                if np.hypot(*(b - a)) / max(fs[k + 1] - fs[k - 1], 1) > 0.3:
                    yaw = math.atan2(*(b - a)[::-1])
            if yaws:
                while yaw - yaws[-1] > math.pi / 2:
                    yaw -= math.pi
                while yaws[-1] - yaw > math.pi / 2:
                    yaw += math.pi
            yaws.append(yaw)
        shapes = []
        for k, f in enumerate(fs):
            o = t["obs"][f]
            if k and f - fs[k - 1] > 1:                    # bị che <= max_hidden frame: nội suy, giữ kích thước, bật occluded
                p, q = t["obs"][fs[k - 1]], o
                for g in range(fs[k - 1] + 1, f):
                    u = (g - fs[k - 1]) / (f - fs[k - 1])
                    c = [pv + u * (qv - pv) for pv, qv in zip(p["center"], q["center"])]
                    shapes.append({"frame": g, "points": cvat_points(c, size, _wrap(yaws[k - 1] + u * (yaws[k] - yaws[k - 1]))),
                                   "occluded": True, "outside": False})
            c = list(o["center"])
            c[2] = c[2] - o["size"][2] / 2 + size[2] / 2      # giữ đáy chạm đất khi đổi về chiều cao chuẩn
            shapes.append({"frame": f, "points": cvat_points(c, size, _wrap(yaws[k])), "occluded": False, "outside": False})
        end = fs[-1] + 1
        shapes.append({"frame": end, "points": shapes[-1]["points"], "occluded": False, "outside": True})
        keys = _keyframes(shapes)
        out.append({"tid": i, "label_id": t["label_id"], "label_name": names.get(t["label_id"], str(t["label_id"])),
                    "start": fs[0], "end": fs[-1], "size": [round(v, 2) for v in size], "ref_frame": ref,
                    "shapes": keys, "keyframes": len(keys), "flags": flags + _merge_flags(t),
                    # 0.9.6: phần lớn các frame bị bỏ tick (giống vật bạn hay bỏ, ảnh camera thấy vật khác) thì track cũng bỏ tick
                    "checked": sum(1 for f in fs if t["obs"][f].get("checked", True)) >= 0.5 * len(fs),
                    "confidence": round(float(np.mean([t["obs"][f].get("confidence", t["obs"][f].get("score", 0.5)) for f in fs])), 3),
                    "cls": t["obs"][ref].get("cls")})
    return {"tracks": out, "warnings": [] if out else ["Không dựng được track cuboid nào."]}


def _merge_flags(t: dict) -> list[dict]:
    flags = []
    for o in t["obs"].values():
        for fl in o.get("flags") or []:
            if fl not in flags:
                flags.append(fl)
    return flags[:4]


def _keyframes(shapes: list[dict], pos_m: float = 0.25, yaw_deg: float = 5.0) -> list[dict]:
    """Ít keyframe nhất sao cho nội suy tuyến tính lệch tâm < 25 cm và yaw < 5 độ; luôn giữ chỗ đổi occluded/outside."""
    if len(shapes) <= 2:
        return shapes
    idx = {0, len(shapes) - 1}
    for i in range(1, len(shapes)):
        if shapes[i]["occluded"] != shapes[i - 1]["occluded"] or shapes[i]["outside"] != shapes[i - 1]["outside"]:
            idx.update({i - 1, i})
    changed = True
    while changed:
        changed = False
        ks = sorted(idx)
        for a, b in zip(ks, ks[1:]):
            if b - a <= 1:
                continue
            pa, pb = np.array(shapes[a]["points"]), np.array(shapes[b]["points"])
            worst, wi = 0.0, None
            for j in range(a + 1, b):
                u = (shapes[j]["frame"] - shapes[a]["frame"]) / max(shapes[b]["frame"] - shapes[a]["frame"], 1)
                guess = pa + u * (pb - pa)
                pj = np.array(shapes[j]["points"])
                err = max(float(np.hypot(*(guess[:2] - pj[:2]))) / pos_m, abs(math.degrees(guess[5] - pj[5])) / yaw_deg)
                if err > worst:
                    worst, wi = err, j
            if wi is not None and worst > 1.0:
                idx.add(wi)
                changed = True
    return [shapes[i] for i in sorted(idx)]
