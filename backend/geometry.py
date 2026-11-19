"""Chuyển mask của model thành hình học CVAT: bounding box, polygon, polyline, mask RLE.

Quy ước toạ độ CVAT: gốc (0, 0) ở góc trên trái, pixel (x, y) phủ vùng [x, x+1) x [y, y+1).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import cv2
import numpy as np

Box = list[float]  # [x1, y1, x2, y2]


# ----------------------------------------------------------------- hộp ------
def box_area(b: Sequence[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def box_inter(a: Sequence[float], b: Sequence[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    inter = box_inter(a, b)
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def clamp_box(b: Sequence[float], width: int, height: int) -> Box:
    return [
        float(min(max(b[0], 0), width)),
        float(min(max(b[1], 0), height)),
        float(min(max(b[2], 0), width)),
        float(min(max(b[3], 0), height)),
    ]


def touches_border(b: Sequence[float], width: int, height: int, margin: float) -> bool:
    return b[0] <= margin or b[1] <= margin or b[2] >= width - margin or b[3] >= height - margin


# ---------------------------------------------------------------- mask ------
def clean_mask(mask: np.ndarray, min_rel: float = 0.02, min_abs: int = 8) -> np.ndarray:
    """Bỏ các mảnh vụn rất nhỏ so với mảnh lớn nhất để bbox không bị kéo rộng."""
    m = mask.astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 2:
        return m.astype(bool)
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.zeros(n, bool)
    keep[1:] = areas >= max(min_abs, areas.max() * min_rel)
    return keep[lab]


def mask_bbox(mask: np.ndarray) -> Optional[Box]:
    """Hộp sát mask theo 4 điểm cực (trái, phải, trên, dưới)."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)]


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.count_nonzero(a & b)
    if inter == 0:
        return 0.0
    return inter / float(np.count_nonzero(a | b))


def encode_cvat_mask(mask: np.ndarray) -> list[int]:
    """Mask nhị phân -> `points` của shape kiểu mask trong CVAT.

    Định dạng: RLE theo hàng trong phạm vi hộp bao, bắt đầu bằng số pixel 0,
    theo sau là [left, top, right, bottom] (right/bottom tính cả pixel cuối).
    """
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return []
    left, top, right, bottom = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    sub = mask[top : bottom + 1, left : right + 1].astype(np.uint8).ravel()
    change = np.flatnonzero(np.diff(sub)) + 1
    edges = np.concatenate(([0], change, [sub.size]))
    runs = np.diff(edges).tolist()
    if sub[0] == 1:
        runs = [0] + runs
    return [int(r) for r in runs] + [left, top, right, bottom]


def decode_cvat_mask(points: Sequence[float], width: int, height: int) -> np.ndarray:
    out = np.zeros((height, width), bool)
    if len(points) < 5:
        return out
    left, top, right, bottom = (int(round(v)) for v in points[-4:])
    bw, bh = right - left + 1, bottom - top + 1
    if bw <= 0 or bh <= 0:
        return out
    runs = np.asarray(points[:-4], dtype=np.int64)
    values = np.zeros(runs.size, np.uint8)
    values[1::2] = 1
    flat = np.repeat(values, runs)
    if flat.size < bw * bh:
        flat = np.pad(flat, (0, bw * bh - flat.size))
    sub = flat[: bw * bh].reshape(bh, bw).astype(bool)
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(right + 1, width), min(bottom + 1, height)
    if x1 > x0 and y1 > y0:
        out[y0:y1, x0:x1] = sub[y0 - top : y1 - top, x0 - left : x1 - left]
    return out


def rasterize_shape(shape: dict, width: int, height: int) -> Optional[np.ndarray]:
    """Vẽ một shape CVAT (rectangle / polygon / mask) thành mask nhị phân."""
    kind, pts = shape.get("type"), shape.get("points") or []
    if kind == "mask":
        return decode_cvat_mask(pts, width, height)
    canvas = np.zeros((height, width), np.uint8)
    if kind == "rectangle" and len(pts) >= 4:
        x1, y1, x2, y2 = (int(round(v)) for v in pts[:4])
        cv2.rectangle(canvas, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), 1, -1)
    elif kind == "polygon" and len(pts) >= 6:
        poly = np.asarray(pts, np.float32).reshape(-1, 2)
        cv2.fillPoly(canvas, [np.round(poly - 0.5).astype(np.int32)], 1)
    else:
        return None
    return canvas.astype(bool)


# ------------------------------------------------------------- polygon ------
def _segments_cross(p1, p2, p3, p4) -> bool:
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and 0 not in (d1, d2, d3, d4)


def polygon_self_intersects(points: np.ndarray, max_vertices: int = 600) -> bool:
    """True nếu có hai cạnh không kề nhau cắt nhau. Bỏ qua polygon quá nhiều đỉnh."""
    pts = np.asarray(points, float).reshape(-1, 2)
    n = len(pts)
    if n < 4 or n > max_vertices:
        return False
    for i in range(n):
        a1, a2 = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_cross(a1, a2, pts[j], pts[(j + 1) % n]):
                return True
    return False


def mask_to_polygons(mask: np.ndarray, epsilon: float = 1.5, min_area: float = 20.0) -> list[list[float]]:
    """Đường bao ngoài của từng mảnh mask, giản lược đỉnh ở đoạn thẳng, giữ đỉnh ở đoạn cong."""
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    polygons: list[list[float]] = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        if cv2.contourArea(contour) < min_area:
            continue
        eps, poly = epsilon, None
        for _ in range(4):
            approx = cv2.approxPolyDP(contour, eps, True).reshape(-1, 2)
            if len(approx) >= 3 and not polygon_self_intersects(approx):
                poly = approx
                break
            eps /= 2
        if poly is None:
            poly = contour.reshape(-1, 2)
            if len(poly) < 3:
                continue
        polygons.append([round(float(v), 2) for v in (poly.astype(float) + 0.5).ravel()])
    return polygons


# ------------------------------------------------------------ polyline ------
def mask_to_centerlines(mask: np.ndarray, step: int = 10, max_gap: int = 30) -> list[np.ndarray]:
    """Tim của vạch kẻ: quét theo trục dài của từng mảnh, lấy điểm giữa mỗi lát cắt.

    Không nối qua khoảng trống dài hơn `max_gap` pixel. Điểm được xếp từ gần xe tới xa
    (dưới lên trên) với vạch dọc, từ trái sang phải với vạch ngang.
    """
    n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    lines: list[np.ndarray] = []
    for idx in range(1, n):
        ys, xs = np.nonzero(lab == idx)
        if xs.size < 3:
            continue
        vertical = (ys.max() - ys.min()) >= (xs.max() - xs.min())
        main, cross = (ys, xs) if vertical else (xs, ys)
        m0 = int(main.min())
        count = np.bincount(main - m0)
        total = np.bincount(main - m0, weights=cross)
        present = np.flatnonzero(count)
        breaks = np.flatnonzero(np.diff(present) > max_gap) + 1
        for run in np.split(present, breaks):
            if run.size < 2:
                continue
            picks = sorted(set(list(range(0, run.size, max(1, step))) + [run.size - 1]))
            rows = run[picks]
            centers = total[rows] / count[rows] + 0.5
            along = rows + m0 + 0.5
            pts = np.stack([centers, along], 1) if vertical else np.stack([along, centers], 1)
            if vertical:
                pts = pts[::-1]  # dưới lên trên
            lines.append(pts)
    return lines


def mask_long_edges(mask: np.ndarray) -> list[np.ndarray]:
    """Hai cạnh dài của hình chữ nhật xoay bao quanh mask (dùng cho crosswalk kiểu 'hai mép')."""
    pts = cv2.findNonZero(mask.astype(np.uint8))
    if pts is None or len(pts) < 3:
        return []
    box = cv2.boxPoints(cv2.minAreaRect(pts)) + 0.5
    d01 = np.linalg.norm(box[0] - box[1])
    d12 = np.linalg.norm(box[1] - box[2])
    pairs = [(0, 1), (3, 2)] if d01 >= d12 else [(1, 2), (0, 3)]
    return [np.stack([box[a], box[b]]) for a, b in pairs]


def simplify_polyline(points: np.ndarray, epsilon: float = 1.5) -> np.ndarray:
    pts = np.asarray(points, np.float32).reshape(-1, 1, 2)
    if len(pts) <= 2:
        return pts.reshape(-1, 2).astype(float)
    return cv2.approxPolyDP(pts, epsilon, False).reshape(-1, 2).astype(float)


def polyline_length(points: np.ndarray) -> float:
    pts = np.asarray(points, float).reshape(-1, 2)
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()) if len(pts) > 1 else 0.0


def _end_direction(pts: np.ndarray, at_end: bool) -> np.ndarray:
    """Vector đơn vị chỉ hướng đi ra khỏi đầu (hoặc cuối) polyline."""
    k = min(3, len(pts) - 1)
    vec = (pts[-1] - pts[-1 - k]) if at_end else (pts[0] - pts[k])
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else np.zeros(2)


def join_collinear(lines: list[np.ndarray], max_gap: float, max_angle_deg: float = 20.0) -> list[np.ndarray]:
    """Nối các đoạn nét đứt thẳng hàng thành một polyline (cuối đoạn này -> đầu đoạn kia)."""
    lines = [np.asarray(l, float) for l in lines if len(l) >= 2]
    cos_limit = math.cos(math.radians(max_angle_deg))
    merged = True
    while merged and len(lines) > 1:
        merged = False
        for i in range(len(lines)):
            best, best_dist = None, max_gap
            for j in range(len(lines)):
                if i == j:
                    continue
                gap = lines[j][0] - lines[i][-1]
                dist = float(np.linalg.norm(gap))
                if dist == 0 or dist > best_dist:
                    continue
                unit = gap / dist
                if (
                    float(np.dot(_end_direction(lines[i], True), unit)) >= cos_limit
                    and float(np.dot(-_end_direction(lines[j], False), unit)) >= cos_limit
                ):
                    best, best_dist = j, dist
            if best is not None:
                lines[i] = np.concatenate([lines[i], lines[best]])
                lines.pop(best)
                merged = True
                break
    return lines


def extend_polyline(points: np.ndarray, start_px: float, end_px: float) -> np.ndarray:
    """Kéo dài polyline theo hướng ở hai đầu (dùng để tách làn trong vùng drivable)."""
    pts = np.asarray(points, float).reshape(-1, 2)
    if len(pts) < 2:
        return pts
    head = pts[0] + _end_direction(pts, False) * start_px
    tail = pts[-1] + _end_direction(pts, True) * end_px
    return np.concatenate([[head], pts, [tail]])


# ------------------------------------------------------------- màu vạch -----
def classify_marking_color(image_rgb: np.ndarray, mask: np.ndarray) -> Optional[str]:
    """Đoán màu vạch kẻ từ các pixel sáng nhất trong mask: 'white', 'yellow' hoặc 'other'."""
    pixels = image_rgb[mask]
    if len(pixels) < 10:
        return None
    hsv = cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_RGB2HSV).reshape(-1, 3).astype(float)
    bright = hsv[hsv[:, 2] >= np.percentile(hsv[:, 2], 50)]
    hue, sat, val = np.median(bright, axis=0)
    if sat >= 70 and 12 <= hue <= 40:
        return "yellow"
    if sat <= 60 and val >= 110:
        return "white"
    return "other"


# ------------------------------------------------- vạch đôi và crosswalk ----
def canonical_axis(u: np.ndarray) -> np.ndarray:
    """Hướng chuẩn của một vạch: dọc ảnh thì chỉ lên trên (gần xe -> xa), ngang thì chỉ sang phải."""
    u = np.asarray(u, float)
    u = u / (np.linalg.norm(u) or 1.0)
    if abs(u[1]) >= abs(u[0]):
        return -u if u[1] > 0 else u
    return -u if u[0] < 0 else u


def principal_axis(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Tâm, hướng trục dài và độ thon dài (tỉ lệ hai trục) của một đám pixel."""
    pts = np.stack([xs, ys], 1).astype(float) + 0.5
    mean = pts.mean(0)
    if len(pts) < 3:
        return mean, np.array([0.0, -1.0]), 1.0
    w, vec = np.linalg.eigh(np.cov((pts - mean).T))
    ratio = float(np.sqrt(max(w[1], 1e-9) / max(w[0], 1e-9)))
    return mean, canonical_axis(vec[:, int(np.argmax(w))]), ratio


def cluster_masks(masks: list[np.ndarray], radius: int) -> list[list[int]]:
    """Gom các mask nằm sát nhau (cách nhau không quá `radius` px) thành nhóm, trả về chỉ số mask của mỗi nhóm."""
    if not masks:
        return []
    if radius <= 0 or len(masks) == 1:
        return [[i] for i in range(len(masks))]
    union = np.zeros(masks[0].shape, np.uint8)
    for m in masks:
        union |= m.astype(np.uint8)
    k = 2 * int(radius) + 1
    grown = cv2.dilate(union, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    _, lab = cv2.connectedComponents(grown, connectivity=8)
    groups: dict[int, list[int]] = {}
    for i, m in enumerate(masks):
        ids = lab[m.astype(bool)]
        ids = ids[ids > 0]
        key = int(np.bincount(ids).argmax()) if ids.size else -(i + 1)
        groups.setdefault(key, []).append(i)
    return list(groups.values())


def _frame(mask: np.ndarray, u: Optional[np.ndarray] = None):
    ys, xs = np.nonzero(mask)
    mean, axis, _ = principal_axis(xs, ys)
    u = canonical_axis(axis if u is None else u)
    v = np.array([-u[1], u[0]])
    d = np.stack([xs, ys], 1).astype(float) + 0.5 - mean
    return mean, u, v, d @ u, d @ v


def _bin_extremes(a: np.ndarray, b: np.ndarray, step: float):
    a0 = a.min()
    bins = np.floor((a - a0) / step).astype(np.int64)
    n = int(bins.max()) + 1
    lo = np.full(n, np.inf)
    hi = np.full(n, -np.inf)
    np.minimum.at(lo, bins, b)
    np.maximum.at(hi, bins, b)
    present = np.flatnonzero(np.isfinite(lo))
    centers = a0 + (present + 0.5) * step
    return present, centers, lo[present], hi[present], bins


def _median_smooth(values: np.ndarray, half: int) -> np.ndarray:
    if half <= 0 or len(values) < 3:
        return values
    return np.array([np.median(values[max(0, i - half): i + half + 1]) for i in range(len(values))])


def count_strips(mask: np.ndarray, step: float = 8.0, min_gap: float = 2.5) -> float:
    """Tỉ lệ lát cắt ngang có từ hai dải trở lên: gần 1 nghĩa là mask gồm hai vạch song song (vạch đôi)."""
    if np.count_nonzero(mask) < 20:
        return 0.0
    _, _, _, a, b = _frame(mask)
    bins = np.floor((a - a.min()) / step).astype(np.int64)
    order = np.lexsort((b, bins))
    bins, b = bins[order], b[order]
    starts = np.flatnonzero(np.r_[True, np.diff(bins) != 0])
    ends = np.r_[starts[1:], len(bins)]
    total = double = 0
    for s, e in zip(starts, ends):
        if e - s < 2:
            continue
        total += 1
        if np.any(np.diff(b[s:e]) > min_gap):
            double += 1
    return double / total if total else 0.0


def mask_side_edges(mask: np.ndarray, step: float = 8.0, max_gap: float = 60.0, smooth: int = 2) -> list[tuple[str, np.ndarray]]:
    """Hai mép ngoài của một vạch (hoặc cặp vạch đôi), đi theo đúng đường cong của vạch.

    Trả về [(side, điểm)] với side là 'left' hoặc 'right' nhìn theo hướng vẽ (gần xe -> xa).
    Không nối qua khoảng trống dài hơn `max_gap` px dọc theo vạch.
    """
    if np.count_nonzero(mask) < 10:
        return []
    mean, u, v, a, b = _frame(mask)
    present, centers, lo, hi, _ = _bin_extremes(a, b, step)
    out: list[tuple[str, np.ndarray]] = []
    breaks = np.flatnonzero(np.diff(present) * step > max_gap) + 1
    for run in np.split(np.arange(len(present)), breaks):
        if len(run) < 2:
            continue
        ac = centers[run]
        # Kéo hai đầu ra tới mép thật của vạch thay vì dừng ở giữa lát cắt đầu/cuối.
        ac = ac.copy()
        ac[0] -= step / 2
        ac[-1] += step / 2
        for side, values in (("left", _median_smooth(lo[run], smooth) - 0.5), ("right", _median_smooth(hi[run], smooth) + 0.5)):
            pts = mean + np.outer(ac, u) + np.outer(values, v)
            out.append((side, pts[::-1] if _reversed(pts) else pts))
    return out


def _reversed(pts: np.ndarray) -> bool:
    """True nếu cần đảo để vẽ từ gần xe ra xa (dọc) hoặc từ trái sang phải (ngang)."""
    d = pts[-1] - pts[0]
    return d[1] > 0 if abs(d[1]) >= abs(d[0]) else d[0] < 0


def _robust_line(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    if len(x) < 2:
        return np.array([0.0, float(y.mean()) if len(y) else 0.0])
    coef = np.polyfit(x, y, 1)
    for _ in range(2):
        res = y - np.polyval(coef, x)
        mad = np.median(np.abs(res - np.median(res))) or 1.0
        keep = np.abs(res) <= 3 * 1.4826 * mad + 1.0
        if keep.sum() < 2:
            break
        coef = np.polyfit(x[keep], y[keep], 1)
    return coef


def _envelope_line(x: np.ndarray, y: np.ndarray, outer: int) -> np.ndarray:
    """Đường thẳng ôm phía ngoài của một dãy điểm (outer=-1: phía y nhỏ, +1: phía y lớn).

    Các lát cắt nằm ở cạnh xiên của sọc cho giá trị lọt vào trong; chỉ giữ nửa ngoài để
    đường không bị kéo vào giữa crosswalk. Điểm vọt ra ngoài quá xa (nhiễu) bị bỏ.
    """
    coef = _robust_line(x, y)
    for _ in range(3):
        res = (y - np.polyval(coef, x)) * outer
        mad = np.median(np.abs(res - np.median(res))) or 1.0
        keep = (res >= np.median(res)) & (res <= np.median(res) + 4 * 1.4826 * mad + 2.0)
        if keep.sum() < 2:
            break
        coef = np.polyfit(x[keep], y[keep], 1)
    return coef


def crosswalk_axis(mask: np.ndarray) -> np.ndarray:
    """Hướng chạy của crosswalk (băng qua đường): vuông góc với các sọc nếu thấy được nhiều sọc riêng."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    angles, weights = [], []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 20:
            continue
        ys, xs = np.nonzero(lab == i)
        _, axis, ratio = principal_axis(xs, ys)
        if ratio >= 1.8:
            angles.append(np.arctan2(axis[1], axis[0]))
            weights.append(stats[i, cv2.CC_STAT_AREA])
    if len(angles) >= 2:
        ang = np.asarray(angles) * 2  # hướng không có chiều: trung bình theo góc nhân đôi
        mean_ang = np.arctan2(np.average(np.sin(ang), weights=weights), np.average(np.cos(ang), weights=weights)) / 2
        stripe = np.array([np.cos(mean_ang), np.sin(mean_ang)])
        return canonical_axis(np.array([-stripe[1], stripe[0]]))
    ys, xs = np.nonzero(mask)
    return principal_axis(xs, ys)[1]


def crosswalk_edges(mask: np.ndarray, step: float = 6.0) -> list[tuple[str, np.ndarray]]:
    """Hai mép dài của cả vùng crosswalk, kéo suốt từ sọc đầu tới sọc cuối (đường thẳng)."""
    if np.count_nonzero(mask) < 20:
        return []
    mean, u, v, a, b = _frame(mask, crosswalk_axis(mask))
    present, centers, lo, hi, _ = _bin_extremes(a, b, step)
    if len(present) < 2:
        return []
    a_lo, a_hi = float(a.min()), float(a.max())
    out = []
    for side, values, pad in (("left", lo, -0.5), ("right", hi, 0.5)):
        coef = _envelope_line(centers, values, -1 if side == "left" else 1)
        ends = np.array([a_lo, a_hi])
        pts = mean + np.outer(ends, u) + np.outer(np.polyval(coef, ends) + pad, v)
        out.append((side, pts[::-1] if _reversed(pts) else pts))
    return out


def polygon_between(left: np.ndarray, right: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Vùng nằm giữa hai mép (dùng làm vùng của crosswalk)."""
    canvas = np.zeros(shape, np.uint8)
    poly = np.concatenate([left, right[::-1]])
    cv2.fillPoly(canvas, [np.round(poly - 0.5).astype(np.int32)], 1)
    return canvas.astype(bool)


def smooth_mask(mask: np.ndarray, px: int) -> np.ndarray:
    """Làm mượt mép mask (bỏ răng cưa nhỏ hơn ~px) trước khi lấy polygon."""
    k = max(1, int(px)) | 1
    m = mask.astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)
    blurred = cv2.GaussianBlur(m.astype(np.float32), (k * 2 + 1, k * 2 + 1), 0)
    out = blurred >= 0.5
    return out if out.any() else mask.astype(bool)


def limit_points(poly: list[float], max_points: int) -> list[float]:
    """Giảm số đỉnh polygon về tối đa max_points (guideline hay giới hạn số đỉnh), giữ hình dạng nhất có thể."""
    pts = np.asarray(poly, dtype=np.float32).reshape(-1, 1, 2)
    if len(pts) <= max_points:
        return poly
    perimeter = cv2.arcLength(pts, True)
    lo, hi, best = 0.0, perimeter / 4, None
    for _ in range(30):
        mid = (lo + hi) / 2
        approx = cv2.approxPolyDP(pts, mid, True)
        if len(approx) > max_points:
            lo = mid
        else:
            best, hi = approx, mid
    if best is None or len(best) < 3:
        return poly
    return [round(float(v), 2) for v in best.reshape(-1)]
