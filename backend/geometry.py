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
