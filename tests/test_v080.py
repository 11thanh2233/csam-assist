"""0.8.0 phía backend: tự điền thuộc tính, giới hạn đỉnh polygon, bấm điểm, nhãn tool chưa sửa, tự học theo người,
nhả model khi rảnh, thông tin GPU."""
import time

import numpy as np

from backend import geometry as G
from backend import pipeline
from backend.engines.base import Instance
from backend.learning import Learner
from backend.runtime_tools import EngineProxy, gpu_info

from test_api import client, image_b64  # noqa: F401 - fixture dùng chung

W, H = 1280, 720


def car_cfg(**extra):
    return dict({"label_id": 1, "name": "car", "shape": "rectangle", "threshold": 0.3, "min_area_px": 20, "role": "object",
                 "attributes": ["truncated", "bị che"], "enabled": True, "supported": True}, **extra)


def det(box, cfg=None):
    m = np.zeros((H, W), bool)
    m[int(box[1]):int(box[3]), int(box[0]):int(box[2])] = True
    return pipeline.Det(cfg or car_cfg(), m, 0.9, "car", list(map(float, box)))


def test_auto_attributes_truncated_and_occluded():
    far = det([500, 300, 700, 420])           # bị xe gần hơn (đáy thấp hơn) che một phần
    near = det([600, 350, 800, 500])
    edge = det([0, 200, 150, 300])            # chạm mép trái
    props = pipeline.objects_to_proposals([far, near, edge], W, H, {}, [])
    a = [p["attributes"] for p in props]
    assert a[0] == {"truncated": "false", "bị che": "true"} and props[0]["auto_attributes"]["bị che"] == "true"
    assert a[1]["bị che"] == "false" and a[2]["truncated"] == "true"
    off = pipeline.objects_to_proposals([edge], W, H, {"auto_attributes": {"enabled": False}}, [])
    assert off[0]["attributes"] == {} and "auto_attributes" not in off[0], "profile tắt được"


def test_polygon_max_points_and_smoothing():
    m = np.zeros((H, W), np.uint8)
    pts = np.array([[640 + 200 * np.cos(t) * (1 + 0.15 * np.sin(9 * t)), 360 + 200 * np.sin(t)] for t in np.linspace(0, 2 * np.pi, 300)], np.int32)
    import cv2

    cv2.fillPoly(m, [pts], 1)
    d = det([440, 160, 840, 560], car_cfg(shape="polygon"))
    d.mask = m.astype(bool)
    many = pipeline.objects_to_proposals([d], W, H, {"polygon": {"epsilon": 0.5}}, [])[0]["points"]
    few = pipeline.objects_to_proposals([d], W, H, {"polygon": {"epsilon": 0.5, "max_points": 12, "smooth_px": 7}}, [])[0]["points"]
    assert len(many) // 2 > 12 >= len(few) // 2 >= 3
    assert G.limit_points([0, 0, 10, 0, 10, 10], 12) == [0, 0, 10, 0, 10, 10]


class BoxEngine:
    """Engine giả: trong hộp có 2 vật, trái và phải."""

    name = "box"

    def segment_box(self, state, box, prompt, threshold, negatives):
        out = []
        for x1, x2, score in ((300, 500, 0.9), (700, 900, 0.8)):
            m = np.zeros((H, W), bool)
            m[300:500, x1:x2] = True
            out.append(Instance(mask=m, score=score))
        return out


def test_points_pick_object_under_click():
    profile = {"rules": {}}
    right = pipeline.interactive_points(BoxEngine(), None, np.zeros((H, W, 3), np.uint8), profile, car_cfg(), [[800, 400]], [])
    assert len(right["proposals"]) == 1 and right["proposals"][0]["bbox"][0] >= 699
    both = pipeline.interactive_points(BoxEngine(), None, np.zeros((H, W, 3), np.uint8), profile, car_cfg(), [[400, 400], [800, 400]], [[800, 400]])
    assert both["proposals"][0]["bbox"][2] <= 501, "điểm âm loại vật bên phải"
    none = pipeline.interactive_points(BoxEngine(), None, np.zeros((H, W, 3), np.uint8), profile, car_cfg(), [[50, 50]], [])
    assert none["proposals"] == [] and none["warnings"]


def test_points_and_tool_shapes_via_api(client, image_b64):
    labels = [{"id": 1, "name": "car", "type": "rectangle"}]
    r = client.post("/interactive", json={"profile_id": "bdd_bbox_polygon_polyline_v1", "labels": labels, "image_b64": image_b64,
                                          "label_id": 1, "box": [0, 0, 0, 0], "mode": "points", "points": [[640, 360]]})
    assert r.status_code == 200, r.text
    assert client.post("/interactive", json={"labels": labels, "image_b64": image_b64, "label_id": 1, "box": [0, 0, 0, 0],
                                             "mode": "points", "points": []}).status_code == 400
    ev = {"kind": "applied", "profile_id": "p", "label_name": "car", "label_id": 1, "job_id": 41, "frame": 0, "type": "rectangle",
          "score": 0.9, "bbox": [0, 0, 10, 10], "cvat_id": 7, "threshold": 0.5}
    client.post("/learn/events", json={"events": [ev, dict(ev, cvat_id=8, bbox=[20, 20, 40, 40])], "user": "an"})
    assert client.get("/learn/tool_shapes?job_id=41&user=an").json()["untouched"] == [7, 8], "chưa đối chiếu: coi là chưa sửa"
    client.post("/learn/review", json={"job_id": 41, "user": "an", "shapes": [
        {"id": 7, "label_id": 1, "frame": 0, "type": "rectangle", "points": [0, 0, 10, 10]},
        {"id": 8, "label_id": 1, "frame": 0, "type": "rectangle", "points": [20, 20, 60, 60]}]})
    assert client.get("/learn/tool_shapes?job_id=41&user=an").json()["untouched"] == [7], "nhãn 8 đã bị sửa"
    assert client.get("/learn/tool_shapes?job_id=41&user=binh").json()["ids"] == [], "người khác có bộ tự học riêng"
    h = client.get("/health").json()
    assert "gpu" in h and h["model_loaded"] is True


def test_per_user_learning_migrates_shared_data(tmp_path, monkeypatch):
    from backend import app as A

    shared = Learner(tmp_path / "learning")
    shared.record([{"kind": "applied", "profile_id": "p", "label_name": "car", "label_id": 1, "job_id": 1, "frame": 0,
                    "type": "rectangle", "score": 0.9, "bbox": [0, 0, 1, 1], "cvat_id": 1}])
    monkeypatch.setattr(A.rt, "learner", shared)
    monkeypatch.setattr(A, "_learners", {})
    first = A.learner_for("an.nguyen")
    assert first.root == tmp_path / "learning" / "users" / "an.nguyen" and first._events(1), "máy một người: dữ liệu cũ chuyển sang người đầu tiên"
    second = A.learner_for("binh/../x")
    assert second.root.name == "binh_.._x" and not second._events(1), "tên người dùng được làm sạch, người sau bắt đầu trống"
    assert A.learner_for("") is shared


class Heavy:
    name, device = "heavy", "cuda"

    def __init__(self):
        self.model = object()

    def segment_text(self, *a):
        return ["ok"]


def test_engine_proxy_unloads_when_idle_and_reloads():
    made, unloaded = [], []
    proxy = EngineProxy(lambda: made.append(1) or Heavy(), idle_min=0.0005, on_unload=lambda: unloaded.append(1))
    assert proxy.segment_text() == ["ok"] and proxy.loaded and proxy.name == "heavy"
    time.sleep(0.05)
    proxy.idle_check()
    assert not proxy.loaded and unloaded == [1] and proxy.name == "heavy", "vẫn biết tên engine khi đang nghỉ"
    assert proxy.segment_text() == ["ok"] and len(made) == 2 and proxy.loads == 2, "dùng lại thì tự nạp"
    never = EngineProxy(lambda: Heavy(), idle_min=0)
    never.ensure()
    never.idle_check()
    assert never.loaded


def test_gpu_info_is_safe_without_gpu():
    info = gpu_info()
    assert info is None or {"used_mb", "total_mb"} <= set(info)


# ------------------------------------------------------- dữ liệu thông minh ---
from backend import datatools  # noqa: E402


def scene(brightness, shift=0, noise=0):
    img = np.full((H, W, 3), brightness, np.uint8)
    img[300:420, 200 + shift:400 + shift] = 255 - brightness
    if noise:
        rng = np.random.default_rng(noise)
        img = np.clip(img.astype(int) + rng.integers(-60, 60, img.shape), 0, 255).astype(np.uint8)
    return img


def test_rank_prefers_new_scenes_and_unlabeled():
    day = [{"frame": i, "features": datatools.features(scene(200, i * 2)), "labeled": 1 if i < 3 else 0} for i in range(6)]
    night = [{"frame": 10, "features": datatools.features(scene(25)), "labeled": 0}]
    ranked = datatools.rank(day + night, k=4)
    assert ranked[0]["frame"] == 10 and "ảnh tối" in ranked[0]["reasons"], "khung cảnh mới (đêm) lên đầu"
    assert all(r["frame"] >= 3 for r in ranked[:3]), "frame đã có nhãn bị đẩy xuống"


def test_keyframes_follow_motion():
    frames = [{"frame": i, "features": datatools.features(scene(150 if i < 5 else 60, i))} for i in range(10)]   # cảnh đổi ở frame 5
    res = datatools.keyframes(frames, threshold=0.06, max_gap=30)
    assert res["keyframes"] == [0, 5], "chỉ frame đầu và frame có cảnh đổi"
    assert datatools.keyframes(frames, 0.9, max_gap=3)["keyframes"] == [0, 3, 6, 9], "không để quá max_gap frame"


def test_search_and_agreement_via_api(client, image_b64):
    res = client.post("/data/features", json={"frames": [{"frame": 1, "image_b64": image_b64}]}).json()
    feat = res["frames"][0]["features"]
    assert len(feat["gray"]) == 64 * 36 and len(feat["hist"]) == 36
    rank = client.post("/data/rank", json={"frames": [{"frame": 1, "features": feat}, {"frame": 2, "features": feat, "labeled": 2}]}).json()
    assert rank["ranking"][0]["frame"] == 1 and rank["keyframes"][0] == 1
    found = client.post("/data/search", json={"query": "car", "frames": [{"frame": 7, "image_b64": image_b64}]}).json()
    assert found["matches"] and found["matches"][0]["frame"] == 7, "mock luôn thấy vật"
    a = [{"frame": 0, "label_id": 1, "type": "rectangle", "points": [0, 0, 100, 100]},
         {"frame": 0, "label_id": 1, "type": "rectangle", "points": [200, 200, 300, 300]},
         {"frame": 1, "label_id": 2, "type": "rectangle", "points": [0, 0, 50, 50]}]
    b = [{"frame": 0, "label_id": 1, "type": "rectangle", "points": [5, 5, 100, 100]},
         {"frame": 1, "label_id": 1, "type": "rectangle", "points": [0, 0, 50, 50]}]
    rep = client.post("/qa/agreement", json={"a": a, "b": b, "labels": [{"id": 1, "name": "car"}, {"id": 2, "name": "truck"}]}).json()
    car = next(l for l in rep["labels"] if l["label"] == "car")
    assert car["matched"] == 1 and car["only_a"] == 1 and car["only_b"] == 1 and car["mean_iou"] > 0.9
    assert rep["confusion"] == [{"pair": "truck ↔ car", "count": 1}] and rep["worst_frames"][0]["frame"] in (0, 1)


# ------------------------------------------------------------- model riêng ---
from backend import custom_model  # noqa: E402


def test_custom_evaluate_and_dataset(tmp_path):
    m = custom_model.evaluate(
        [[("car", [0, 0, 10, 10], 0.9), ("car", [50, 50, 60, 60], 0.8), ("bus", [0, 0, 10, 10], 0.2)]],
        [[("car", [0, 0, 10, 10]), ("bus", [100, 100, 120, 120])]], ["car", "bus"])
    assert m["car"] == {"precision": 0.5, "recall": 1.0, "tp": 1, "fp": 1, "fn": 0} and m["bus"]["recall"] == 0.0
    store = custom_model.CustomStore(tmp_path)
    img = np.zeros((60, 80, 3), np.uint8)
    info = store.add_samples("bdd/../x", [("j1_f1", img, [{"label": "car", "bbox": [1, 1, 20, 20]}]), ("j1_f1", img, [{"label": "car", "bbox": [1, 1, 20, 20]}])])
    assert info == {"images": 1, "boxes": 1, "per_class": {"car": 1}}, "cùng frame không bị nhân đôi"
    assert (tmp_path / "bdd_.._x").exists() and not (tmp_path.parent / "x").exists(), "tên profile được làm sạch"


def test_custom_endpoints_without_enough_data(client, image_b64):
    labels = [{"id": 1, "name": "car", "type": "rectangle"}]
    body = {"profile_id": "bdd_bbox_polygon_polyline_v1", "labels": labels}
    r = client.post("/custom/dataset/add", json=dict(body, samples=[{"key": "j9_f0", "image_b64": image_b64, "boxes": [{"label_id": 1, "bbox": [10, 10, 100, 100]}, {"label_id": 99, "bbox": [0, 0, 1, 1]}]}]))
    assert r.json()["dataset"]["boxes"] == 1, "bỏ hộp của label lạ"
    info = client.post("/custom/info", json=body).json()
    assert info["dataset"]["images"] == 1 and info["model"] is None and not info["training"]["running"]
    t = client.post("/custom/train", json=body)
    assert t.status_code == 400 and "ít nhất 8 ảnh" in t.json()["detail"]
    prop = client.post("/propose", json=dict(body, image_b64=image_b64, use_custom=True)).json()
    assert prop["proposals"] and "custom_labels" not in prop, "chưa có model dùng được: SAM3 làm như cũ"
    assert client.post("/custom/clear", json=body).json()["dataset"]["images"] == 0


# ---------------------------------------------------------- Label Studio ---
from backend import labelstudio as LS  # noqa: E402

LS_CONFIG = """<View>
  <Image name="img" value="$image"/>
  <RectangleLabels name="box" toName="img"><Label value="car"/><Label value="pedestrian"/></RectangleLabels>
  <PolygonLabels name="poly" toName="img"><Label value="road"/></PolygonLabels>
  <Choices name="weather" toName="img"><Choice value="rain"/></Choices>
</View>"""


def test_labelstudio_config_and_results():
    cfg = LS.parse_config(LS_CONFIG)
    assert [l["name"] for l in cfg["labels"]] == ["car", "pedestrian", "road"] and cfg["controls"][0]["data_key"] == "image"
    res = LS.to_results([{"label_id": 1, "type": "rectangle", "points": [128, 72, 256, 144], "score": 0.9},
                         {"label_id": 3, "type": "polygon", "points": [0, 0, 1280, 0, 640, 720], "score": 0.8},
                         {"label_id": 2, "type": "rectangle", "points": [0, 0, 1, 1], "score": 0.5, "checked": False}], cfg, 1280, 720)
    assert len(res) == 2, "đề xuất bị bỏ tick (trùng nhãn có sẵn) không gửi"
    assert res[0]["value"] == {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0, "rotation": 0, "rectanglelabels": ["car"]}
    assert res[1]["value"]["points"][2] == [50.0, 100.0] and res[1]["from_name"] == "poly"


def test_labelstudio_predict_via_api(client, image_b64, monkeypatch):
    assert client.get("/ls/health").json()["status"] == "UP"
    task = {"id": 1, "data": {"image": "data:image/jpeg;base64," + image_b64}}
    out = client.post("/ls/predict", json={"tasks": [task, {"id": 2, "data": {"image": "/data/upload/x.jpg"}}], "label_config": LS_CONFIG}).json()
    assert len(out["results"]) == 2 and out["results"][0]["result"], "task có ảnh: có gợi ý"
    assert out["results"][1]["result"] == [], "task lỗi (thiếu LABEL_STUDIO_URL) không làm hỏng cả lô"
    assert client.post("/ls/predict", json={"tasks": [task], "label_config": "<View><Image name='i' value='$image'/></View>"}).status_code == 400
    from backend import app as A

    monkeypatch.setattr(A.settings, "api_key", "s3cret")
    assert client.post("/ls/setup").status_code == 401
    good = "Basic " + base64.b64encode(b"any:s3cret").decode()
    assert client.post("/ls/setup", headers={"Authorization": good}).status_code == 200


import base64  # noqa: E402
