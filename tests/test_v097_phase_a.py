"""0.9.7: Giai đoạn A — CUDA an toàn (A1) + hình học/tracking 3D (A2)."""
import math

import numpy as np
import pytest

import backend.lidar as L
import backend.tracking_3d as T3
from backend import runtime_tools as RT


# ---------------------------------------------------------------- A1 ---
def test_release_for_3d_releases_loaded_lazies():
    class Fake:
        def __init__(self, loaded, what="model"):
            self.loaded, self.what, self.calls = loaded, what, []

        def release(self, why):
            self.calls.append(why)
            return True

    on, off = Fake(True, "pose"), Fake(False, "reid")
    released = RT.release_for_3d([on, off, None])
    assert released == ["pose"]
    assert on.calls and not off.calls


def test_release_for_3d_unloads_sam3_proxy():
    class Proxy:
        loaded = True

        @staticmethod
        def unload(why):
            Proxy.unloaded = why

    Proxy.unloaded = None
    out = RT.release_for_3d([], engine=Proxy())
    assert out == ["sam3"] and Proxy.unloaded == "chuyển sang job 3D"


def test_check_onnx_conflict_message():
    # không sập trên máy nào cả: trả None hoặc chuỗi cảnh báo
    out = RT.check_onnx_conflict()
    assert out is None or "onnxruntime" in out


def test_pick_onnx_providers_always_has_cpu_fallback():
    providers, opts = RT.pick_onnx_providers()
    assert providers[-1] == "CPUExecutionProvider"
    assert isinstance(opts, dict)


def test_pick_onnx_providers_cuda_config():
    providers, opts = RT.pick_onnx_providers(gpu_mem_limit_mb=1024)
    if "CUDAExecutionProvider" in providers:
        cfg = opts["cuda"]
        assert cfg["arena_extend_strategy"] == "kSameAsRequested"
        assert cfg["gpu_mem_limit"] == 1024 * 2 ** 20


def test_lidar_assist_check_skips_when_engine_not_loaded():
    from backend import lidar_assist

    class NotLoaded:
        loaded = False

    img = np.zeros((4, 4, 3), np.uint8)
    res = lidar_assist.check(NotLoaded(), [("CAM_FRONT", img)], [{"id": 1, "center": [5, 0, 1], "size": [4, 2, 1.5], "label_id": 1, "cls": "car"}], {1: "car"})
    assert res["results"] == {} and res["note"], "không nạp SAM3 giữa request 3D: trả note"


def test_lidar_assist_check_skips_when_no_engine():
    from backend import lidar_assist

    img = np.zeros((4, 4, 3), np.uint8)
    res = lidar_assist.check(None, [("CAM_FRONT", img)], [{"id": 1, "center": [5, 0, 1], "size": [4, 2, 1.5], "label_id": 1, "cls": "car"}], {1: "car"})
    assert res["results"] == {}


# ---------------------------------------------------------------- A2 ---
def _box_car(cx, cy, n=400, yaw=0.0, z0=0.2, height=1.5):
    """Cụm điểm hình hộp: dài 4.4, rộng 1.8."""
    rng = np.random.default_rng(3)
    l, w = 4.4, 1.8
    pts = np.c_[rng.uniform(-l / 2, l / 2, n), rng.uniform(-w / 2, w / 2, n)]
    ca, sa = math.cos(yaw), math.sin(yaw)
    xy = np.c_[pts[:, 0] * ca - pts[:, 1] * sa + cx, pts[:, 0] * sa + pts[:, 1] * ca + cy]
    z = rng.uniform(z0, z0 + height, n)
    return np.c_[xy, z]


def test_lshape_fit_recovers_yaw_and_size():
    yaw = math.radians(30)
    c = _box_car(10, 5, yaw=yaw)
    out = L.lshape_fit(c)
    assert out is not None
    cx, cy, length, width, ang = out
    assert abs(length - 4.4) < 0.6, f"dài lệch: {length:.2f}"
    assert abs(width - 1.8) < 0.6
    diff = abs(((ang - yaw + math.pi) % (2 * math.pi)) - math.pi)
    assert diff < 0.2, f"yaw lệch: {math.degrees(diff):.1f}°"


def test_lshape_fit_small_cluster_returns_none():
    c = _box_car(0, 0, n=4)                                # dưới 5 điểm: không dựng khung
    assert L.lshape_fit(c) is None


def test_fit_box_falls_back_for_tiny_cluster():
    """Cụm < 20 điểm không dùng L-shape (dễ bị nhiễu) nhưng vẫn phải ra cuboid như trước."""
    ground = np.array([0.0, 0.0, 0.0])
    c = _box_car(6, 2, n=12)
    box = L.fit_box(c, ground)
    assert box["size"][0] > 0 and box["size"][2] > 0


def test_fit_box_uses_lshape_when_enough_points():
    ground = np.array([0.0, 0.0, 0.0])
    c = _box_car(8, 3, yaw=math.radians(12), n=600)
    box = L.fit_box(c, ground)
    assert abs(box["size"][0] - 4.4) < 0.8


def _brute_merge(groups):
    """Bản quét mọi cặp của 0.9.6: dùng làm chuẩn để so với bản cKDTree."""
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

    for pos, a in enumerate(small):
        ba = boxes[a]
        eps = 0.25 + 0.01 * math.hypot((ba[0] + ba[2]) / 2, (ba[1] + ba[3]) / 2)
        for b in small[pos + 1:]:
            bb = boxes[b]
            dx = max(bb[0] - ba[2], ba[0] - bb[2], 0.0)
            dy = max(bb[1] - ba[3], ba[1] - bb[3], 0.0)
            if math.hypot(dx, dy) <= eps:
                parent[find(a)] = find(b)
    merged = {}
    for i in small:
        merged.setdefault(find(i), []).append(i)
    return sorted(sorted(v) for v in merged.values())


def _block(x0, y0, n=6):
    """Lưới điểm xác định trong ô 1 x 1 m: khe hở giữa hai cụm kiểm soát được chính xác."""
    gx, gy = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
    return np.c_[gx.ravel() + x0, gy.ravel() + y0, np.zeros(n * n)]


def test_merge_sparse_joins_close_clusters():
    g1, g2 = _block(0, 0), _block(1.1, 0)                 # khe 0.1 m: nối
    g3 = np.c_[np.random.default_rng(0).uniform(20, 21, 300), np.random.default_rng(1).uniform(20, 21, 300), np.zeros(300)]
    out = L._merge_sparse([g1, g2, g3])
    assert len(out) == 2, f"g1+g2 phải nối (khe 0.1 m), g3 cụm lớn giữ riêng: {[len(g) for g in out]}"


def test_merge_sparse_keeps_far_clusters():
    g1, g2 = _block(0, 0), _block(1.9, 0)                 # khe 0.9 m: không nối
    assert len(L._merge_sparse([g1, g2])) == 2


def test_merge_sparse_keeps_big_clusters_separate():
    """Cụm >= 200 điểm giữ nguyên để hai xe đỗ sát nhau không dính thành một."""
    rng = np.random.default_rng(5)
    a = np.c_[rng.uniform(0, 1, 300), rng.uniform(0, 1, 300), np.zeros(300)]
    b = np.c_[rng.uniform(1.05, 2, 300), rng.uniform(0, 1, 300), np.zeros(300)]
    assert len(L._merge_sparse([a, b])) == 2


def test_merge_sparse_kdtree_matches_brute_force():
    """Bản cKDTree phải cho đúng cùng cách gom cụm như bản quét mọi cặp của 0.9.6."""
    rng = np.random.default_rng(11)
    for trial in range(6):
        groups = []
        for k in range(6):
            cx, cy = rng.uniform(0, 6), rng.uniform(0, 6)
            n = int(rng.integers(20, 60))
            groups.append(np.c_[cx + rng.uniform(-0.5, 0.5, n), cy + rng.uniform(-0.5, 0.5, n), np.zeros(n)])
        got = L._merge_sparse([g.copy() for g in groups])
        # đối chiếu theo số điểm từng cụm sau khi nối (thứ tự không quan trọng)
        mine = sorted(sorted(len(g) for g in got))
        # chuẩn: áp lại đúng luật 0.9.6 rồi đếm
        ref_groups = _merge_reference(groups)
        assert mine == sorted(sorted(len(g) for g in ref_groups)), f"lần {trial}"


def _merge_reference(groups):
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

    for pos, a in enumerate(small):
        ba = boxes[a]
        eps = 0.25 + 0.01 * math.hypot((ba[0] + ba[2]) / 2, (ba[1] + ba[3]) / 2)
        for b in small[pos + 1:]:
            bb = boxes[b]
            dx = max(bb[0] - ba[2], ba[0] - bb[2], 0.0)
            dy = max(bb[1] - ba[3], ba[1] - bb[3], 0.0)
            if math.hypot(dx, dy) <= eps:
                parent[find(a)] = find(b)
    merged = {}
    for i in small:
        merged.setdefault(find(i), []).append(groups[i])
    return [np.vstack(v) for v in merged.values()]


def test_localground_numba_matches_numpy():
    rng = np.random.default_rng(7)
    pts = np.c_[rng.uniform(0, 40, 5000), rng.uniform(0, 40, 5000), rng.uniform(-0.1, 0.3, 5000) + rng.uniform(0, 30, 5000) * 0]
    plane = np.array([0.0, 0.0, 0.5])
    g = L.LocalGround(pts, plane)
    if L.HAS_NUMBA:
        # hai đường tính cùng một grid: dựng lại bằng bản numpy và so giá trị từng ô
        grid_np = {}
        pz = pts[:, 0] * plane[0] + pts[:, 1] * plane[1] + plane[2]
        near = np.abs(pts[:, 2] - pz) < 0.6
        p2 = pts[near]
        ij = np.floor(p2[:, :2] / 8.0).astype(np.int64)
        for k in np.unique(ij, axis=0):
            sel = (ij == k).all(axis=1)
            if sel.sum() >= 15:
                grid_np[(int(k[0]), int(k[1]))] = float(np.percentile(p2[sel, 2], 20))
        for key, val in grid_np.items():
            assert abs(g.grid[key] - val) < 1e-6


def test_kf2d_predict_update():
    k = T3.Kf2d([0, 0], v=(2, 0))
    p = k.predict(1.0)
    assert np.allclose(p, [2, 0])
    x = k.update([4, 0], 1.0)
    assert x[0] > 2.5, "đo mới kéo vị trí về phía đo"


def test_hungarian_prefers_correct_pairing():
    """Hai track, hai vật: Hungarian ghép đúng cặp gần nhất, ghép tham lam có thể sai."""
    t1 = {"label_id": 1, "last": 0, "obs": {0: {"center": [0, 0, 1], "size": [4, 2, 1.5]}}, "vel": np.zeros(2), "gaps": [], "max_hidden": 6}
    t2 = {"label_id": 1, "last": 0, "obs": {0: {"center": [30, 0, 1], "size": [4, 2, 1.5]}}, "vel": np.zeros(2), "gaps": [], "max_hidden": 6}
    kfs = [T3.Kf2d([0, 0]), T3.Kf2d([30, 0])]
    objs = [{"label_id": 1, "center": [30.5, 0.2, 1], "size": [4, 2, 1.5]},
            {"label_id": 1, "center": [0.3, 0.1, 1], "size": [4, 2, 1.5]}]
    pairs = T3._match([t1, t2], kfs, objs, 1, 2.5)
    got = {ti: oi for ti, oi in pairs}
    assert got[0] == 1 and got[1] == 0, "track 1 (x=0) nhận vật tại 0.3, track 2 (x=30) nhận vật tại 30.5"


def test_size_locked_to_densest_frame():
    frames = []
    for f in range(4):
        size = [4.4, 1.8, 1.5] if f == 2 else [6.0, 2.5, 1.9]   # frame 2 dày điểm nhất
        frames.append({"frame": f, "objects": [{"label_id": 1, "center": [10 + 0.5 * f, 0, 0.8], "size": size,
                                                "yaw": 0.0, "points": 500 if f == 2 else 100, "cls": "car",
                                                "confidence": 0.9, "checked": True}]})
    res = T3.build(frames, {"size_tolerance": 0.05}, {1: "Car"})
    assert len(res["tracks"]) == 1
    tr = res["tracks"][0]
    assert tr["size"] == [4.4, 1.8, 1.5], "kích thước khóa theo frame dày điểm nhất"


def test_never_occluded_and_outside_together():
    """Track bị che 3 frame: các frame nội suy occluded=True, outside=False; frame cuối track là outside."""
    frames = []
    for f in (0, 4):
        frames.append({"frame": f, "objects": [{"label_id": 1, "center": [10 + 0.2 * f, 0, 0.8], "size": [4.4, 1.8, 1.5],
                                                "yaw": 0.0, "points": 300, "cls": "car", "confidence": 0.9, "checked": True}]})
    res = T3.build(frames, {}, {1: "Car"})
    tr = res["tracks"][0]
    interp = [s for s in tr["shapes"] if s["frame"] in (1, 2, 3)]
    assert interp and all(s["occluded"] and not s["outside"] for s in interp)
    last = tr["shapes"][-1]
    assert last["outside"] and not last["occluded"]
    for s in tr["shapes"]:                                  # ràng buộc cứng trên mọi keyframe
        assert not (s["occluded"] and s["outside"])


def test_track_split_when_hidden_too_long():
    frames = []
    for f in range(11):
        if 2 <= f < 9:                                     # mất 7 frame -> quá ngưỡng 6: cắt, đoạn sau thành track mới
            continue
        frames.append({"frame": f, "objects": [{"label_id": 1, "center": [10, 0, 0.8], "size": [4.4, 1.8, 1.5],
                                                "yaw": 0.0, "points": 300, "cls": "car", "confidence": 0.9, "checked": True}]})
    res = T3.build(frames, {"max_occluded_frames": 6, "min_track_frames": 2}, {1: "Car"})
    starts = sorted(t["start"] for t in res["tracks"])
    assert starts == [0, 9], f"khuất quá max_hidden: phải cắt thành 2 track, được {starts}"


def test_track_split_strictly_beyond_threshold():
    """Đúng 6 frame che vẫn nội suy (<= N), 7 mới cắt."""
    frames = []
    for f in range(9):
        if 2 <= f <= 7:                                     # 6 frame che
            continue
        frames.append({"frame": f, "objects": [{"label_id": 1, "center": [10, 0, 0.8], "size": [4.4, 1.8, 1.5],
                                                "yaw": 0.0, "points": 300, "cls": "car", "confidence": 0.9, "checked": True}]})
    res = T3.build(frames, {"max_occluded_frames": 6, "min_track_frames": 2}, {1: "Car"})
    assert len(res["tracks"]) == 1


def test_yaw_by_width_locks_barrier():
    frames = []
    for f in range(3):
        yaw = 0.1 * f                                       # cố xoay dần: rào chắn không được xoay theo chuyển động
        frames.append({"frame": f, "objects": [{"label_id": 1, "center": [10, 0, 0.5], "size": [2.0, 0.6, 1.0],
                                                "yaw": yaw, "points": 200, "cls": "barrier", "confidence": 0.9, "checked": True}]})
    res = T3.build(frames, {"yaw_by_width": ["barrier"]}, {1: "Barrier"})
    tr = res["tracks"][0]
    yaws = [s["points"][5] for s in tr["shapes"] if s["frame"] in (0, 1, 2)]
    assert max(yaws) - min(yaws) < 1e-6, "barrier khoá yaw theo guideline"


def test_parse_track_rules_from_guideline():
    md = ("**Occluded:** bị che tối đa 6 keyframe thì nội suy. Kích thước sai lệch cho phép ±5%. "
          "Barrier: yaw căn theo bề ngang rào.")
    out = L.parse_track_rules(md)
    assert out.get("max_occluded_frames") == 6
    assert out.get("size_tolerance") == 0.05
    assert "barrier" in (out.get("yaw_by_width") or [])


def test_parse_track_rules_no_match():
    assert L.parse_track_rules("Không có luật track nào ở đây.") == {}


@pytest.mark.skipif(not L.HAS_NUMBA, reason="numba chưa cài")
def test_numba_ground_cells_direct():
    ij = np.array([[0, 0], [0, 0], [0, 0], [1, 0]], np.int64)
    z = np.array([1.0, 2.0, 3.0, 5.0])
    keys, vals = L._ground_cells(ij, z, 3, 20.0)
    assert keys.tolist() == [[0, 0]]
    assert abs(vals[0] - 1.4) < 1e-6
