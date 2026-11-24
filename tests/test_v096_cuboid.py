"""0.9.6: job cuboid 3D: class theo chiều cao, phạm vi theo class, số điểm theo khoảng cách, mặt đất cục bộ, bỏ cụm
lơ lửng, tách dãy rào chắn, nhớ vật bạn bỏ, ảnh camera, tự chỉnh theo cuboid của bạn."""
import base64
import io
import math
import zipfile

import numpy as np
import pytest
from conftest import ScriptedEngine, rect

from test_v094 import _pcd

from backend import lidar as L
from backend import lidar_assist as A

LABELS = {"car": 1, "pedestrian": 2, "truck": 3, "trailer": 4, "barrier": 5}


def ground(rng, n=8000, z=-1.8, span=45, slope=0.0):
    g = rng.uniform([-span, -span], [span, span], (n, 2))
    return np.c_[g, z + slope * g[:, 0] + rng.normal(0, 0.02, n)]


def box(rng, cx, cy, l, w, h, n, z0=-1.8, lift=0.05):
    return rng.uniform([cx - l / 2, cy - w / 2, z0 + lift], [cx + l / 2, cy + w / 2, z0 + h], (n, 3))


def objs(res, label):
    return [o for o in res["objects"] if o["label_id"] == label]


def test_height_must_match_and_partial_view_allowed():
    rng = np.random.default_rng(1)
    parts = [ground(rng),
             box(rng, 12, 4, 4.5, 1.8, 1.5, 900),          # xe con đủ
             box(rng, -12, 6, 2.0, 1.8, 1.55, 500),        # xe con bị che nửa sau: chỉ thấy 2 m, vẫn là car
             box(rng, 15, -8, 1.1, 1.1, 2.3, 400),         # nhà vệ sinh di động: cao 2.3 m, không khớp class nào
             box(rng, -15, -10, 6.0, 1.9, 0.6, 600)]       # bệ thấp dài: không phải xe
    res = L.detect(np.vstack(parts).astype("float32"), LABELS, L.DEFAULT_SIZES, debug=True)
    cars = objs(res, 1)
    assert len(cars) == 1 and abs(cars[0]["center"][0] - 12) < 0.5, [(o["cls"], o["size"]) for o in res["objects"]]
    half = next(o for o in res["objects"] if abs(o["center"][0] + 12) < 2)
    if half["cls"] != "car":    # xe bị che nửa: theo bảng cũng giống trailer nhỏ -> phải gắn cờ để xem ảnh camera
        assert any(f["code"] == "UNCERTAIN_CLASS" and "car" in f["message"] for f in half["flags"])
    assert not any(abs(o["center"][0] - 15) < 1 for o in res["objects"]), "cao 2.3 m: bỏ, không gán bừa"
    assert res["stats"]["no_class"] >= 2
    assert {r["reason"] for r in res["rejected"]} >= {"no_class"}


def test_range_per_class_and_points_by_distance():
    rng = np.random.default_rng(2)
    parts = [ground(rng, span=60),
             box(rng, 45, 0, 4.5, 1.8, 1.5, 80),            # xe ở 45 m (<= 50): giữ
             box(rng, 0, 44, 0.6, 0.6, 1.75, 40),           # người ở 44 m (> 40): bỏ
             box(rng, 8, -3, 0.2, 1.8, 1.5, 12)]            # mặt sau xe ở 9 m, chỉ 12 điểm: quá ít so với khoảng cách
    res = L.detect(np.vstack(parts).astype("float32"), LABELS, L.DEFAULT_SIZES, debug=True)
    cars = objs(res, 1)
    assert len(cars) == 1 and abs(cars[0]["center"][0] - 45) < 1
    assert not objs(res, 2)
    assert res["stats"]["range"] >= 1 and res["stats"]["few_points"] >= 1
    assert L.min_points_at(10, "car", {}) > L.min_points_at(45, "car", {})
    assert L.min_points_at(10, "pedestrian", {}) < L.min_points_at(10, "car", {})


def test_local_ground_on_slope_and_floating_clusters():
    rng = np.random.default_rng(3)
    slope = 0.04                                           # dốc 4%: ở x = 30 m mặt đất cao hơn 1.2 m
    zg = lambda x: -1.8 + slope * x  # noqa: E731
    parts = [ground(rng, slope=slope),
             box(rng, 30, 3, 4.5, 1.8, 1.5, 700, z0=zg(30)),
             box(rng, 10, -6, 3.0, 2.0, 2.2, 300, z0=zg(10), lift=1.2)]   # biển / tán cây: đáy cao hơn đất 1.2 m
    res = L.detect(np.vstack(parts).astype("float32"), LABELS, L.DEFAULT_SIZES, debug=True)
    car = objs(res, 1)
    assert len(car) == 1
    base = car[0]["center"][2] - car[0]["size"][2] / 2
    assert abs(base - zg(30)) < 0.25, "đáy theo mặt đất cục bộ, không theo một mặt phẳng"
    assert abs(car[0]["size"][2] - 1.5) < 0.3
    assert res["stats"]["floating"] >= 1


def test_barrier_row_is_split():
    rng = np.random.default_rng(4)
    row = box(rng, 10, 8, 8.0, 0.5, 0.95, 1600)             # 4 tấm rào chắn liền nhau
    res = L.detect(np.vstack([ground(rng), row]).astype("float32"), LABELS, L.DEFAULT_SIZES)
    barriers = objs(res, 5)
    assert 3 <= len(barriers) <= 5 and all(b["size"][0] <= 3.3 for b in barriers)


def test_parse_ranges_from_guideline():
    md = "Chỉ gán Vehicle trong phạm vi 50 m. VRU (pedestrian, two wheels) tối đa 40m. Barrier trong vòng 30 m."
    r = L.parse_ranges(md)
    assert r["car"] == 50 and r["pedestrian"] == 40 and r["barrier"] == 30


# ------------------------------------------------------------- nhớ vật bỏ ---
def test_memory_of_dropped_static_objects(tmp_path):
    from backend.memory import ProjectMemory

    m = ProjectMemory(tmp_path)
    dumpster = {"cls": "trailer", "size": [2.1, 1.6, 1.3]}
    m.record_lidar("p", [dumpster, dict(dumpster, size=[2.05, 1.62, 1.28])], [])
    drops = m.lidar_drops("p")
    assert len(drops) == 1 and drops[0]["count"] == 2
    rng = np.random.default_rng(5)
    pts = np.vstack([ground(rng), box(rng, 9, 5, 2.1, 1.6, 1.3, 400)]).astype("float32")
    res = L.detect(pts, LABELS, L.DEFAULT_SIZES, {"dropped": drops})
    t = objs(res, 4)
    assert t and not t[0]["checked"] and any(f["code"] == "LEARNED" for f in t[0]["flags"])
    m.record_lidar("p", [], [dumpster, dumpster])         # bạn giữ vật cùng cỡ: quên dần
    assert m.lidar_drops("p") == []


# -------------------------------------------------------------- ảnh camera ---
def test_camera_names_and_projection():
    assert A.camera_key("CAM_BACK_LEFT.jpg") == "back left"
    assert A.camera_key("samples/CAM_FRONT/abc.png") == "front"
    assert A.camera_key("lidar_top") is None
    cams = A.cameras_of(["CAM_FRONT.jpg", "CAM_BACK.jpg"])
    front = A.project([20, 0, 0], [4.5, 1.8, 1.5], cams, 0)
    assert front["camera"] == "CAM_FRONT.jpg" and abs(front["u"] - 0.5) < 0.01
    left = A.project([20, 5, 0], [4.5, 1.8, 1.5], cams, 0)
    assert left["u"] < 0.5, "vật lệch trái thì nằm nửa trái ảnh"
    assert A.project([-20, 0, 0], [4.5, 1.8, 1.5], cams, 0)["camera"] == "CAM_BACK.jpg"


def _context_zip(images):
    from PIL import Image

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, arr in images.items():
            b = io.BytesIO()
            Image.fromarray(arr).save(b, "PNG")
            z.writestr(name, b.getvalue())
    return buf.getvalue()


def test_camera_check_estimates_axis_and_flags_lookalikes():
    H, W = 720, 1280
    # trục của point cloud lệch 90° (y là hướng trước, kiểu nuScenes): xe ở (0, 20) nằm giữa ảnh CAM_FRONT
    engine = ScriptedEngine({"car": [(rect(600, 300, 680, 400), 0.9), (rect(250, 300, 330, 400), 0.9)],
                             "dumpster": [(rect(900, 320, 980, 400), 0.8)]})
    images = [("CAM_FRONT.jpg", np.zeros((H, W, 3), np.uint8))]
    az_left = math.radians(25)                                # xe thứ hai lệch trái 25°
    objects = [{"id": 1, "center": [0, 20, 0], "size": [4.5, 1.8, 1.5], "label_id": 1, "cls": "car"},
               {"id": 2, "center": [-20 * math.sin(az_left), 20 * math.cos(az_left), 0], "size": [4.5, 1.8, 1.5], "label_id": 1, "cls": "car"},
               {"id": 3, "center": [-0 - 20 * math.sin(math.radians(-15)), 20 * math.cos(math.radians(-15)), 0],
                "size": [2.1, 1.6, 1.3], "label_id": 4, "cls": "trailer"}]
    res = A.check(engine, images, objects, {})
    assert res["offset"] == -90.0 and res["offset_source"] == "estimated", res
    assert res["results"][1]["verdict"] == "confirmed" and res["results"][2]["verdict"] == "confirmed"
    assert res["results"][3]["verdict"] == "contradicted" and res["results"][3]["seen"] == "dumpster"
    A.apply_camera(objects, res)
    assert objects[2].get("checked") is False


def test_lidar_api_with_camera_debug_eval_feedback(client):
    rng = np.random.default_rng(6)
    pts = np.vstack([ground(rng), box(rng, 12, 0, 4.5, 1.8, 1.5, 900), box(rng, -8, 7, 0.6, 0.6, 1.75, 150)]).astype("float32")
    labels = [{"id": 1, "name": "Car", "type": "cuboid"}, {"id": 2, "name": "Pedestrian", "type": "cuboid"},
              {"id": 9, "name": "Dumpster", "type": "cuboid"}]
    ctx = base64.b64encode(_context_zip({"CAM_FRONT.jpg": np.zeros((90, 160, 3), np.uint8)})).decode()
    frame = {"frame": 0, "data_b64": base64.b64encode(_pcd(pts)).decode(), "context_b64": ctx}
    mine = {"sizes": {"Dumpster": [[[1.5, 2.5], [1.2, 2.0], [1.0, 1.6]]]}, "ranges": {"car": 45}}
    det = client.post("/lidar/detect", json={"labels": labels, "frames": [frame], "debug": True, "lidar": mine}).json()
    fr = det["frames"][0]
    assert {o["cls"] for o in fr["objects"]} == {"car", "pedestrian"}
    assert fr["bev"]["points"] and "info" in fr and fr["info"]["points"] == len(pts)
    assert det["rule"]["ranges"]["car"] == 45, "phạm vi bạn chỉnh thắng mặc định"
    assert det["camera"] is not None and det["camera"]["cameras"] == ["CAM_FRONT.jpg"]
    car = next(o for o in fr["objects"] if o["cls"] == "car")
    truth = [{"frame": 0, "label_id": 1, "points": [*car["center"], 0, 0, car["yaw"], *car["size"]] + [0] * 7},
             {"frame": 0, "label_id": 2, "points": [-8, 7, -0.9, 0, 0, 0, 0.6, 0.6, 1.75] + [0] * 7}]
    ev = client.post("/lidar/evaluate", json={"labels": labels, "frames": [dict(frame, context_b64="")], "truth": truth}).json()
    assert ev["truth"] == 2 and ev["recall"] == 1.0
    fb = client.post("/lidar/feedback", json={"labels": labels, "dropped": [{"cls": "car", "size": [4.5, 1.8, 1.5]}]}).json()
    assert fb["drops"] == 1
    assert client.post("/lidar/feedback", json={"labels": labels, "clear": True}).json()["drops"] == 0


def test_evaluate_suggests_tuning():
    rng = np.random.default_rng(7)
    rear = box(rng, 5.85, -3, 0.2, 1.8, 1.5, 20)           # xe ở 8 m chỉ thấy mặt sau, 20 điểm (< 25)
    pts = np.vstack([ground(rng, span=60), rear, box(rng, 46, 2, 4.5, 1.8, 1.5, 120)]).astype("float32")
    truth = [{"frame": 0, "label_id": 1, "center": [8, -3, -1.05], "size": [4.5, 1.8, 1.5], "yaw": 0.0},
             {"frame": 0, "label_id": 1, "center": [46, 2, -1.05], "size": [4.5, 1.8, 1.5], "yaw": 0.0}]
    ev = A.evaluate([(0, pts)], truth, {"car": 1}, L.DEFAULT_SIZES, {"ranges": {"car": 40}})
    row = ev["rows"][0]
    assert row["truth"] == 2 and row["found"] == 0
    assert row["missed"].get("few_points") == 1 and row["missed"].get("range") == 1
    assert ev["suggest"]["min_points_scale"]["car"] < 1 and ev["suggest"]["ranges"]["car"] >= 47
    tuned = A.evaluate([(0, pts)], truth, {"car": 1}, L.DEFAULT_SIZES, dict(ev["suggest"]))
    assert tuned["recall"] == 1.0


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_rear_face_only_is_completed_to_table_size():
    rng = np.random.default_rng(8)
    rear = box(rng, 10.1, 3, 0.2, 1.8, 1.5, 300)            # chỉ thấy đuôi xe (xe nằm từ x = 10 tới 14.5)
    res = L.detect(np.vstack([ground(rng), rear]).astype("float32"), {"car": 1}, L.DEFAULT_SIZES)
    car = objs(res, 1)[0]
    assert car["size"][0] >= 3.8 and abs(car["size"][1] - 1.8) < 0.3
    assert car["center"][0] > 11, "tâm dời ra phía khuất (xa cảm biến)"
    assert min(abs(car["yaw"]), abs(abs(car["yaw"]) - math.pi)) < 0.15, "hướng theo trục dọc của xe"
    assert any("một phần" in f["message"] for f in car["flags"])
