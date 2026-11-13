import numpy as np

from backend import geometry as G


def test_mask_rle_roundtrip():
    rng = np.random.default_rng(0)
    mask = np.zeros((72, 128), bool)
    mask[10:40, 20:90] = rng.random((30, 70)) > 0.3
    mask[10, 20] = True  # RLE bắt đầu bằng pixel 1 -> phải có số 0 đứng đầu
    points = G.encode_cvat_mask(mask)
    assert points[0] == 0
    left, top, right, bottom = points[-4:]
    assert sum(points[:-4]) == (right - left + 1) * (bottom - top + 1)
    assert np.array_equal(G.decode_cvat_mask(points, 128, 72), mask)


def test_bbox_is_tight_on_extreme_points():
    mask = np.zeros((100, 100), bool)
    mask[10:20, 30:50] = True
    assert G.mask_bbox(mask) == [30.0, 10.0, 50.0, 20.0]


def test_clean_mask_drops_specks():
    mask = np.zeros((100, 100), bool)
    mask[10:60, 10:60] = True
    mask[90:92, 90:92] = True
    assert G.mask_bbox(G.clean_mask(mask)) == [10.0, 10.0, 60.0, 60.0]


def test_polygon_from_mask_is_simple_and_close():
    mask = np.zeros((200, 200), np.uint8)
    import cv2
    cv2.circle(mask, (100, 100), 60, 1, -1)
    polys = G.mask_to_polygons(mask.astype(bool), 1.5, 20)
    assert len(polys) == 1
    pts = np.array(polys[0]).reshape(-1, 2)
    assert 8 <= len(pts) < 80
    assert not G.polygon_self_intersects(pts)
    back = G.rasterize_shape({"type": "polygon", "points": polys[0]}, 200, 200)
    assert G.mask_iou(back, mask.astype(bool)) > 0.95


def test_self_intersection_detected():
    bow_tie = np.array([[0, 0], [10, 10], [10, 0], [0, 10]])
    square = np.array([[0, 0], [10, 0], [10, 10], [0, 10]])
    assert G.polygon_self_intersects(bow_tie)
    assert not G.polygon_self_intersects(square)


def test_centerline_follows_middle_and_respects_gaps():
    mask = np.zeros((300, 200), bool)
    mask[20:120, 95:105] = True    # đoạn 1
    mask[200:290, 95:105] = True   # đoạn 2, cách 80px
    lines = G.mask_to_centerlines(mask, step=10, max_gap=30)
    assert len(lines) == 2
    for line in lines:
        assert np.allclose(line[:, 0], 100.0)       # đúng tim vạch
        assert line[0][1] > line[-1][1]             # vẽ từ dưới lên
    joined = G.join_collinear(lines, max_gap=100)
    assert len(joined) == 1
    assert len(G.join_collinear(lines, max_gap=40)) == 2


def test_marking_color():
    image = np.zeros((50, 50, 3), np.uint8)
    mask = np.ones((50, 50), bool)
    image[:] = (240, 240, 240)
    assert G.classify_marking_color(image, mask) == "white"
    image[:] = (230, 190, 40)
    assert G.classify_marking_color(image, mask) == "yellow"
    image[:] = (40, 60, 220)
    assert G.classify_marking_color(image, mask) == "other"
