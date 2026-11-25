"""0.9.8: phiên 3D chạy hết với track THẬT (không track giả), ảnh camera, bộ dò 2D, Claude, rổ trung thực,
bộ dò 2D RT-DETR (giải mã), CenterPoint ONNX (pillar, giải mã), Claude cho từng frame, thành phần desktop."""
from __future__ import annotations

import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pytest

import backend.lidar_onnx as LO
import backend.lidar_session as LS
import backend.lidar_vision as LV

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- dữ liệu ---
def _car(x, y, n=900, seed=0):
    """Xe con 4.4 x 1.8 x 1.5 m đứng trên mặt đất z = -1.8 (hệ toạ độ LiDAR kiểu nuScenes)."""
    rng = np.random.default_rng(seed)
    return np.c_[rng.uniform(-2.2, 2.2, n) + x, rng.uniform(-0.9, 0.9, n) + y, rng.uniform(-1.75, -0.35, n)]


def _ground(seed=0, n=6000):
    rng = np.random.default_rng(seed)
    return np.c_[rng.uniform(-40, 40, n), rng.uniform(-40, 40, n), rng.normal(-1.8, 0.02, n)]


def _frame(f, cars):
    parts = [_ground(f)] + [_car(x + 0.3 * f, y, seed=f * 10 + i) for i, (x, y) in enumerate(cars)]
    return np.vstack(parts).astype(np.float32)


class FakeLLM:
    def __init__(self, reply=None, fail=None):
        self.reply, self.fail, self.calls = reply, fail, []

    def complete(self, system, text, images, max_tokens=4000):
        self.calls.append((text, len(images)))
        if self.fail:
            raise self.fail
        if self.reply is not None:
            return self.reply
        ids = [int(s.split("id=")[1].split()[0]) for s in text.splitlines() if " id=" in s]
        return json.dumps({"tracks": [{"id": i, "keep": True, "label": "Car", "confidence": "high"} for i in ids]})


def _session(n_frames=10, cars=((12, 0), (0, 12)), with_images=True):
    from backend.lidar import DEFAULT_SIZES

    sess = LS.create({"car": 1}, {1: "Car"}, dict(DEFAULT_SIZES), {})
    img = np.zeros((90, 160, 3), np.uint8)
    for f in range(n_frames):
        ctx = {f: [("CAM_FRONT.jpg", img), ("CAM_FRONT_LEFT.jpg", img), ("CAM_BACK.jpg", img)]} if with_images else {}
        LS.add_frames(sess, [(f, _frame(f, cars))], ctx)
    LS.run_geometry(sess, limit=100)
    return sess


# ------------------------------------------------- A2: track thật -> Claude ---
def test_real_tracks_feed_claude_without_keyerror():
    """0.9.7 trả 500 KeyError: 'center' khi bật Claude vì track không có center / id. Giờ chạy với track THẬT."""
    sess = _session()
    assert sess.track["tracks"], "phải dựng được track"
    assert all("_obs" in t for t in sess.track["tracks"])
    llm = FakeLLM()
    out = LS.run_llm(sess, llm, {"rules": {"lidar": {}}}, None, 8)
    assert "error" not in out, out
    assert out["judged"] == len(sess.track["tracks"])
    text, n_images = llm.calls[0]
    assert "id=" in text and "dist=" in text and "points=0 " not in text, "Claude nhận đủ id, khoảng cách, số điểm"
    assert n_images >= 2, "BEV + ảnh camera của track"
    res = LS.result(sess)
    assert all("_obs" not in t for t in res["tracks"]), "_obs chỉ nằm trong RAM backend"
    assert res["checked_by"]["claude"] == len(res["tracks"])


def test_claude_failure_never_500_and_explains():
    sess = _session(n_frames=4)
    out = LS.run_llm(sess, FakeLLM(fail=RuntimeError("Error code: 401 - invalid x-api-key")), {"rules": {}}, None, 8)
    assert out["errors"] and "API key" in out["errors"][0], out
    res = LS.result(sess)
    assert {t["bucket"] for t in res["tracks"]} == {"needs_review"}


def test_view_has_geometry_of_densest_frame():
    sess = _session(n_frames=6)
    t = sess.track["tracks"][0]
    v = LS.view(t)
    assert v["id"] == t["tid"] and len(v["center"]) == 3 and v["points"] > 100
    assert v["ref_frame"] in t["_obs"] and v["frames_seen"] == len(t["_obs"])


# ------------------------------------------------- B1: camera thật sự chạy ---
RIG3 = {"cameras": [{"name": "CAM_FRONT.jpg", "yaw": 0.0, "fov": 70.0}, {"name": "CAM_FRONT_LEFT.jpg", "yaw": 55.0, "fov": 70.0},
                    {"name": "CAM_BACK.jpg", "yaw": 180.0, "fov": 110.0}], "source": "preset"}


def test_vision_runs_on_real_tracks_and_flags_noise():
    sess = _session(n_frames=6)
    out = LS.run_vision(sess, LV.MockVision(), RIG3, 0.0, limit=50)        # bộ dò không thấy gì -> nghi rác
    assert out["remaining"] == 0 and out["checked"] >= 1
    res = LS.result(sess)
    assert any(t["bucket"] == "suspected_noise" for t in res["tracks"])
    assert res["checked_by"]["camera"] >= 1


def test_vision_confirms_car():
    sess = _session(n_frames=6, cars=((12, 0),))
    rig = {"cameras": [{"name": "CAM_FRONT.jpg", "yaw": 0.0, "fov": 70.0}], "source": "preset"}
    vision = LV.MockVision([{"box": [40, 10, 120, 80], "score": 0.9, "coco_id": 2}])
    LS.run_vision(sess, vision, rig, 0.0, limit=50)
    res = LS.result(sess)
    car = max(res["tracks"], key=lambda t: t["end"] - t["start"])
    assert car["vision2d"]["verdict"] == "confirmed" and car["bucket"] == "high_confidence", car


def test_vision_without_images_marks_tracks_done():
    sess = _session(n_frames=4, with_images=False)
    out = LS.run_vision(sess, LV.MockVision(), {"cameras": [], "source": "none"}, 0.0, limit=50)
    assert out["remaining"] == 0, "track không có ảnh vẫn được đánh dấu đã xét, vòng lặp panel kết thúc"


# ------------------------------------------------------- C1 / C2 / C3: rổ ---
def test_default_bucket_is_honest():
    assert LS.default_bucket({"checked": True, "cls": "car"}) == "needs_review", "chưa ai kiểm: không 'Tin cậy cao'"
    assert LS.default_bucket({"checked": True, "cls": "car", "vision2d": {"verdict": "confirmed"}}) == "high_confidence"
    assert LS.default_bucket({"checked": False}) == "needs_review"
    assert LS.default_bucket({"bucket_hint": "suspected_noise"}) == "suspected_noise"


def test_small_trailer_needs_confirmed_tow_vehicle():
    trailer = {"tid": 1, "cls": "trailer", "checked": True, "ref_frame": 0,
               "_obs": {0: {"center": [10.0, 0.0, -1.0], "size": [3.0, 1.8, 1.5]}}}
    car = {"tid": 2, "cls": "car", "checked": True, "_obs": {0: {"center": [14.5, 0.0, -1.0], "size": [4.4, 1.8, 1.5]}}}
    assert LS.default_bucket(trailer, [trailer, car]) == "needs_review", "xe kéo chưa được camera xác nhận"
    car["vision2d"] = {"verdict": "confirmed"}
    assert LS.default_bucket(trailer, [trailer, car]) == "high_confidence"
    car["_obs"][0]["center"] = [40.0, 0.0, -1.0]
    assert LS.default_bucket(trailer, [trailer, car]) == "needs_review", "xe ở xa không phải xe kéo"


def test_debris_tracks_go_to_noise():
    tracks = [{"tid": 1, "_obs": {0: {"flags": []}, 1: {"flags": []}}},
              {"tid": 2, "_obs": {f: {"flags": []} for f in range(20)}}]
    assert LS.mark_debris(tracks, 20) == 1
    assert tracks[0]["bucket_hint"] == "suspected_noise" and tracks[0]["checked"] is False
    assert "bucket_hint" not in tracks[1]


def test_error_summary_groups_repeats():
    errs = [f"frame {i} ảnh camera lỗi: boom" for i in range(40)]
    assert LS._error_summary(errs) == ["ảnh camera lỗi: boom (×40)"]


# -------------------------------------------------- B1: giải mã RT-DETR ---
def test_rtdetr_decode_sigmoid_and_output_order():
    """onnx-community/rtdetr_r18vd trả [logits (1,300,80), pred_boxes (1,300,4)], điểm sigmoid."""
    logits = np.full((1, 3, 80), -9.0)
    logits[0, 0, 2] = 3.0                         # car, sigmoid ~0.95
    logits[0, 1, 0] = 0.0                         # person, sigmoid 0.5
    boxes = np.array([[[0.5, 0.5, 0.2, 0.4], [0.2, 0.2, 0.1, 0.1], [0.9, 0.9, 0.1, 0.1]]])
    out = LV._decode([logits, boxes], 0.4)
    assert [d["id"] for d in out] == [2, 0]
    assert abs(out[0]["score"] - 1 / (1 + math.exp(-3))) < 1e-6
    assert abs(out[0]["box"][0] - 0.4) < 1e-9


def test_rtdetr_boxes_scaled_to_original_image():
    class Sess:
        def get_inputs(self):
            class I:
                name = "pixel_values"
            return [I()]

        def run(self, _names, feed):
            assert feed["pixel_values"].shape == (1, 3, 640, 640)
            logits = np.full((1, 1, 80), -9.0)
            logits[0, 0, 2] = 4.0
            return [logits, np.array([[[0.5, 0.5, 0.5, 0.5]]])]

    v = LV.Vision2D("x.onnx", ["CPUExecutionProvider"])
    v.session = Sess()
    dets = v.detect(np.zeros((480, 1000, 3), np.uint8), 0.5)
    assert dets and [round(x) for x in dets[0]["box"]] == [250, 120, 750, 360], "toạ độ chuẩn hoá nhân với cỡ ảnh gốc"


# --------------------------------------------------- F1: CenterPoint ONNX ---
def test_voxelize_shapes_and_coords():
    pts = np.array([[0.05, 0.05, 0.0], [0.07, 0.01, 0.5], [10.0, -5.0, 1.0], [100.0, 0.0, 0.0]], np.float32)
    vox, num, coors = LO.voxelize(pts)
    assert vox.shape == (2, 20, 5) and sorted(num.tolist()) == [1, 2]
    by = {tuple(c[2:]): n for c, n in zip(coors.tolist(), num.tolist())}
    assert by[(256, 256)] == 2, "điểm (0.05, 0.05) rơi vào ô (y=256, x=256)"
    assert by[(231, 306)] == 1, "(10, -5) -> x=(10+51.2)/0.2=306, y=(-5+51.2)/0.2=231"
    assert coors[:, 0].max() == 0 and coors[:, 1].max() == 0, "batch 0, z 0 (pillar)"
    assert vox[:, :, 3:].max() == 0, "không có cường độ / độ trễ sweep: để 0"


def _fake_heads(cx, cy, cls=0, score_logit=4.0, z=-0.9, dims=(4.4, 1.8, 1.5), yaw=0.3):
    heat = np.full((1, 10, 128, 128), -9.0, np.float32)
    box = np.zeros((1, 48, 128, 128), np.float32)
    rot = np.zeros((1, 12, 128, 128), np.float32)
    task = next(i for i, t in enumerate(LO.TASKS) if cls in t)
    gx, gy = (cx + 51.2) / 0.8, (cy + 51.2) / 0.8
    ix, iy = int(gx), int(gy)
    heat[0, cls, iy, ix] = score_logit
    base = task * 8
    box[0, base, iy, ix], box[0, base + 1, iy, ix] = gx - ix, gy - iy
    box[0, base + 2, iy, ix] = z
    box[0, base + 3:base + 6, iy, ix] = np.log(dims)
    rot[0, 2 * task, iy, ix], rot[0, 2 * task + 1, iy, ix] = math.sin(yaw), math.cos(yaw)
    return [rot, heat, box]                         # thứ tự lộn: phải nhận theo số kênh


def test_centerpoint_decode_matches_mmdet3d_math():
    out = LO.decode(_fake_heads(12.3, -4.1), score_thr=0.3)
    assert len(out) == 1
    d = out[0]
    assert d["name"] == "car" and abs(d["center"][0] - 12.3) < 1e-4 and abs(d["center"][1] + 4.1) < 1e-4
    assert [round(v, 3) for v in d["size"]] == [4.4, 1.8, 1.5] and abs(d["yaw"] - 0.3) < 1e-5


def test_centerpoint_decode_second_class_of_task():
    out = LO.decode(_fake_heads(-20.0, 8.0, cls=8, dims=(0.7, 0.7, 1.75)), score_thr=0.3)
    assert len(out) == 1 and out[0]["name"] == "pedestrian"


def test_circle_nms_drops_near_duplicate():
    xy = np.array([[0.0, 0.0], [1.0, 0.0], [5.0, 0.0]])
    assert LO.circle_nms(xy, np.array([0.9, 0.8, 0.7]), 4.0) == [0, 2]



def test_centerpoint_detect_maps_class_and_restores_height():
    class Model:
        def run(self, pts):
            self.z0 = float(np.percentile(pts[:, 2], 5))
            return [{"center": [12.0, 0.0, -1.05], "size": [4.4, 1.8, 1.5], "yaw": 0.0, "score": 0.8, "name": "car"},
                    {"center": [5.0, 5.0, -1.4], "size": [0.4, 0.4, 0.9], "yaw": 0.0, "score": 0.9, "name": "traffic_cone"}]

    lift = 2.0                                       # dữ liệu có mặt đất ở z = +0.2 (cảm biến thấp hơn nuScenes)
    pts = np.vstack([_ground(n=4000) + [0, 0, lift], _car(12, 0) + [0, 0, lift]]).astype(np.float32)
    m = Model()
    res = LO.detect(m, pts, {"car": 1}, {})
    assert abs(m.z0 - LO.NUSC_GROUND_Z) < 0.1, "point cloud được dời cho mặt đất về độ cao nuScenes"
    assert len(res["objects"]) == 1, "traffic_cone không có trong task: bỏ"
    o = res["objects"][0]
    assert o["cls"] == "car" and o["label_id"] == 1 and o["source"] == "centerpoint"
    assert abs(o["center"][2] - (-1.05 + (-1.8 - LO.NUSC_GROUND_Z) + lift)) < 0.1, "chiều cao box dời về hệ toạ độ gốc"
    assert o["points"] > 100, "đếm điểm trong box ở hệ toạ độ gốc"


def _tiny_centerpoint(tmp_path, heads):
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import TensorProto, helper, numpy_helper

    rot, heat, box = heads
    nodes, inits = [], []
    for name, arr in (("heatmap", heat), ("box", box), ("rot", rot)):
        inits.append(numpy_helper.from_array(arr, name + "_c"))
        nodes.append(helper.make_node("Identity", [name + "_c"], [name]))
    graph = helper.make_graph(nodes, "cp", [helper.make_tensor_value_info("voxels", TensorProto.FLOAT, ["n", 20, 5]),
                                            helper.make_tensor_value_info("num_points", TensorProto.INT32, ["n"]),
                                            helper.make_tensor_value_info("coors", TensorProto.INT32, ["n", 4])],
                              [helper.make_tensor_value_info(n, TensorProto.FLOAT, None) for n in ("heatmap", "box", "rot")], inits)
    path = tmp_path / "cp.onnx"
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(path))
    return path


def test_centerpoint_onnx_end_to_end_with_real_onnxruntime(tmp_path):
    """Một model ONNX nhỏ có đúng giao diện (voxels, num_points, coors -> 3 head) chạy qua onnxruntime thật."""
    path = _tiny_centerpoint(tmp_path, _fake_heads(12.0, 0.0))
    m = LO.CenterPointONNX(str(path)).load()
    assert m.ready, m.error
    found = m.run(_frame(0, [(12, 0)]))
    assert found and found[0]["name"] == "car" and abs(found[0]["center"][0] - 12.0) < 1e-3


def test_centerpoint_rejects_wrong_model(tmp_path):
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import TensorProto, helper

    g = helper.make_graph([helper.make_node("Identity", ["x"], ["y"])], "g",
                          [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])],
                          [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1])])
    p = tmp_path / "bad.onnx"
    model = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(p))
    m = LO.CenterPointONNX(str(p)).load()
    assert not m.ready and "voxels" in m.error


def test_session_geometry_uses_model_when_given():
    from backend.lidar import DEFAULT_SIZES

    sess = LS.create({"car": 1}, {1: "Car"}, dict(DEFAULT_SIZES), {})
    for f in range(3):
        LS.add_frames(sess, [(f, _frame(f, [(12, 0)]))], {})
    calls = []

    def predict(points):
        calls.append(len(points))
        return {"objects": [{"center": [12.0, 0.0, -1.0], "size": [4.4, 1.8, 1.5], "yaw": 0.0, "points": 500, "label_id": 1,
                             "cls": "car", "score": 0.9, "confidence": 0.9, "flags": [], "checked": True}], "engine": "centerpoint"}

    LS.run_geometry(sess, limit=10, predict=predict)
    assert len(calls) == 3 and len(sess.track["tracks"]) == 1



# ------------------------------------------------- API: phiên + từng frame ---
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def _pcd_b64(points):
    import base64

    head = (f"# .PCD v0.7\nVERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nWIDTH {len(points)}\nHEIGHT 1\n"
            f"VIEWPOINT 0 0 0 1 0 0 0\nPOINTS {len(points)}\nDATA binary\n").encode()
    return base64.b64encode(head + np.asarray(points, "<f4").tobytes()).decode()


def _context_b64():
    import base64

    import cv2

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in ("CAM_FRONT.jpg", "CAM_BACK.jpg"):
            z.writestr(name, cv2.imencode(".jpg", np.full((90, 160, 3), 80, np.uint8))[1].tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def test_session_api_keeps_camera_images(client):
    """A1: 0.9.7 mất mọi ảnh camera vì thiếu import lidar_assist ('name lidar_assist is not defined')."""
    labels = [{"id": 1, "name": "Car", "type": "cuboid"}]
    frames = [{"frame": f, "data_b64": _pcd_b64(_frame(f, [(12, 0)])), "context_b64": _context_b64()} for f in range(4)]
    init = client.post("/lidar/v2/session/init", json={"labels": labels, "frames": frames[:2]}).json()
    assert init["contexts"] == 2 and not init["errors"], init
    assert init["camera_names"] == ["CAM_BACK.jpg", "CAM_FRONT.jpg"]
    sid = init["session_id"]
    more = client.post(f"/lidar/v2/session/{sid}/frames", json={"labels": labels, "frames": frames[2:]}).json()
    assert more["contexts"] == 4, more
    client.post(f"/lidar/v2/session/{sid}/geometry", json={"labels": labels, "limit": 20})
    off = client.post(f"/lidar/v2/session/{sid}/llm_batch", json={"labels": labels, "use_llm": False}).json()
    assert "tắt" in off["skipped"], "bỏ tick Claude: backend không gọi Claude"
    res = client.post(f"/lidar/v2/session/{sid}/result", json={"labels": labels}).json()
    assert res["tracks"] and all(t["bucket"] != "high_confidence" for t in res["tracks"]), "chưa ai kiểm: không 'Tin cậy cao'"
    assert not any("lidar_assist" in w for w in res["warnings"])
    client.post(f"/lidar/v2/session/{sid}/close", json={"labels": labels})


def test_health_reports_3d_engines(client):
    h = client.get("/health").json()
    assert set(h["lidar3d"]) == {"model3d", "vision2d"}


def test_detect_frame_with_claude(client, monkeypatch):
    """D2: Claude cho từng frame ở job 3D: bỏ vật Claude bảo bỏ, ghi chú vào warnings."""
    import backend.app as A

    llm = FakeLLM(reply=json.dumps({"tracks": [{"id": 1, "keep": False, "label": "Car", "reason": "dumpster"}]}))
    monkeypatch.setattr(A, "_llm_for_3d", lambda: llm)
    labels = [{"id": 1, "name": "Car", "type": "cuboid"}]
    body = {"labels": labels, "use_llm": True, "frames": [{"frame": 0, "data_b64": _pcd_b64(_frame(0, [(12, 0)]))}]}
    det = client.post("/lidar/detect", json=body).json()
    objs = det["frames"][0]["objects"]
    assert objs and llm.calls, det
    first = next(o for o in objs if o["id"] == 1)
    assert first["checked"] is False and first["llm"]["keep"] is False
    assert any(w.startswith("Claude đã xem") for w in det["warnings"]), det["warnings"]


def test_detect_frame_claude_off_when_no_key(client, monkeypatch):
    import backend.app as A

    monkeypatch.setattr(A, "_llm_for_3d", lambda: None)
    labels = [{"id": 1, "name": "Car", "type": "cuboid"}]
    body = {"labels": labels, "use_llm": True, "frames": [{"frame": 0, "data_b64": _pcd_b64(_frame(0, [(12, 0)]))}]}
    det = client.post("/lidar/detect", json=body).json()
    assert any("API key" in w for w in det["warnings"])


# ------------------------------------------------------------- desktop ---
def test_rtdetr_component_is_a_pinned_file(tmp_path):
    from desktop.components import COMPONENTS, FILE_MODELS, ORDER, Components
    from desktop.paths import Paths

    assert "rtdetr" in COMPONENTS and "rtdetr" in FILE_MODELS and "rtdetr" in ORDER
    paths = Paths(tmp_path / "data", app_dir=ROOT).ensure()
    comps = Components(paths, manifest={"models": {"rtdetr": {"url": "https://x/model.onnx", "sha256": "0" * 64, "size": 10}}})
    assert comps.backend_env()["SAM3_RTDETR_MODEL"] == str(paths.rtdetr)
    assert comps._plan_file("rtdetr", None, "", False)["action"] == "install"
    build = (ROOT / "installer" / "build.py").read_text(encoding="utf-8")
    assert "11843b02455cc24009aed24d4c40db721b1093be5ccd6bbe7b9c441abb1d0558" in build, "mã băm công bố trên Hugging Face"
    assert "f2bee7444f4c29003df7e9ef07c4c1c6632eca6f" in build, "ghim theo commit"
    html = (ROOT / "desktop" / "ui" / "control.html").read_text(encoding="utf-8")
    assert "'rtdetr'" in html.split("const KEYS = ")[1].split("\n")[0]


def test_explain_llm_error_messages():
    from backend.judge_3d import explain_llm_error

    assert "API key" in explain_llm_error(RuntimeError("Error code: 401 - authentication_error"))
    assert "429" in explain_llm_error(RuntimeError("Error code: 429 rate_limit_error"))
    assert "quá tải" in explain_llm_error(RuntimeError("Error code: 529 overloaded_error"))


def test_userscript_claude_chip_follows_checkbox():
    js = (ROOT / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
    assert "Claude AI: tắt (bạn đã tắt)" in js and "llmUse = llmOk && l3().useLlm !== false" in js
    assert "use_llm: true" in js and "frameLlm" in js, "Claude cho cả job (có cờ) và cho từng frame"
