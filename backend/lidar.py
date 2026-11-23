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


# --------------------------------------------------------------- cụm ---
def clusters(points: np.ndarray, plane: np.ndarray, *, cell: float = 0.25, max_range: float = 60.0, ego: float = 2.5,
             min_height: float = 0.25, max_height: float = 5.0, min_points: int = 5) -> list[np.ndarray]:
    """Điểm không thuộc mặt đường -> các cụm (lưới BEV, ô 25 cm, nối 8 hướng)."""
    from scipy import ndimage

    if not len(points):
        return []
    hz = points[:, 2] - (points[:, 0] * plane[0] + points[:, 1] * plane[1] + plane[2])
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
    out = []
    for k in range(1, n + 1):
        c = pts[ids == k]
        if len(c) >= min_points:
            out.append(c)
    return out


def fit_box(c: np.ndarray, plane: np.ndarray) -> dict:
    """Cuboid cho một cụm: chữ nhật nhỏ nhất bao điểm nhìn từ trên (dài, rộng, yaw), đáy chạm mặt đường."""
    import cv2

    (cx, cy), (a, b), ang = cv2.minAreaRect(c[:, :2].astype(np.float32))
    yaw = math.radians(ang)
    length, width = float(a), float(b)
    if width > length:
        length, width, yaw = width, length, yaw + math.pi / 2
    base = ground_z(plane, cx, cy)
    top = float(c[:, 2].max())
    height = max(top - base, 0.2)
    return {"center": [float(cx), float(cy), base + height / 2], "size": [max(length, 0.2), max(width, 0.2), height],
            "yaw": _wrap(yaw), "points": int(len(c))}


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


# ------------------------------------------------------------ class ---
def class_key(name: str) -> str:
    key = norm(_ascii(name)).replace("_", " ")
    return ALIASES.get(key, key)


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


def classify(size: list[float], table: dict[str, list], allowed: dict[str, int], tol: float = 0.15) -> tuple[Optional[int], list[str], float]:
    """(label_id, các class cùng khớp, điểm 0..1). Cho phép lệch 15% vì cụm điểm thường nhỏ hơn vật thật một chút."""
    scored = []
    for key, label_id in allowed.items():
        best = 0.0
        for ranges in table.get(key) or []:
            s = 0.0
            for v, (lo, hi) in zip(size, ranges):
                if lo * (1 - tol) <= v <= hi * (1 + tol):
                    s += 1.0
                else:
                    gap = (lo - v) / lo if v < lo else (v - hi) / hi
                    s += max(0.0, 1.0 - gap * 3)
            best = max(best, s / 3)
        if best > 0:
            scored.append((best, key, label_id))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.6:
        return None, [], 0.0
    close = [k for s, k, _ in scored if s >= scored[0][0] - 0.05]
    return scored[0][2], close, scored[0][0]


# ------------------------------------------------------------- frame ---
def detect(points: np.ndarray, labels: dict[str, int], table: dict[str, list], rule: Optional[dict] = None) -> dict:
    """Một frame: cuboid đề xuất kèm class. labels: {class_key: label_id} của các label cuboid trong task."""
    rule = rule or {}
    plane, ratio = ground_plane(points)
    out, warnings = [], []
    if ratio < 0.2:
        warnings.append("Không tìm được mặt đường rõ ràng: đáy cuboid có thể lệch, kiểm tra ở Side view.")
    for c in clusters(points, plane, max_range=float(rule.get("max_range_m", 60)), min_points=int(rule.get("min_points", 5))):
        box = fit_box(c, plane)
        if box["size"][0] > 20 or box["size"][2] > 4.6:
            continue                                        # tường, nhà, cây: không phải vật cần gán
        label_id, close, score = classify(box["size"], table, labels)
        if label_id is None:
            continue
        flags = []
        if len(close) > 1:
            flags.append({"code": "UNCERTAIN_CLASS", "message": f"Kích thước khớp nhiều class: {', '.join(close)}. Xem ảnh camera để chọn."})
        if box["points"] < int(rule.get("sparse_points", 30)):
            flags.append({"code": "LOW_VISIBILITY", "message": f"Chỉ {box['points']} điểm LiDAR: kích thước, hướng có thể sai."})
        out.append(dict(box, label_id=label_id, score=round(score, 3), flags=flags))
    return {"objects": out, "warnings": warnings, "ground_ratio": round(ratio, 3)}


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
                    "shapes": keys, "keyframes": len(keys), "flags": flags + _merge_flags(t), "checked": True})
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
