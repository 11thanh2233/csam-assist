"""0.9.6: model phụ mới: pose toàn thân (tay, bàn chân), nhận lại người (re-ID), độ sâu (ai đứng trước ai)."""
import numpy as np
from conftest import ScriptedEngine, rect

from test_api import image_b64  # noqa: F401 - fixture dùng chung

from backend import keypoints as K
from backend import tracking
from backend.engines.extras import MockDepth, MockReID, MockWholeBody, RTMWEngine, box_closeness, decode_simcc
from backend.engines.pose import LazyEngine
from backend.profiles import WHOLEBODY, skeleton_config, wholebody_source

W, H = 1280, 720


class Lazy:
    def __init__(self, model, what="test"):
        self.model, self.what, self.error = model, what, None

    def get(self):
        return self.model


# ---------------------------------------------------------- tên điểm tay / chân --
def test_wholebody_point_names():
    assert len(WHOLEBODY) == 133 and WHOLEBODY[91] == "left_hand_root" and WHOLEBODY[132] == "right_pinky_finger4"
    cases = {"L_thumb_tip": "left_thumb4", "right index finger 2": "right_index_finger2", "left big toe": "left_big_toe",
             "R wrist root": "right_hand_root", "LThumb4": "left_thumb4", "RIndexTip": "right_index_finger4",
             "left_middle_pip": "left_middle_finger2", "R_heel": "right_heel", "left eye": None, "nose": None}
    for name, want in cases.items():
        assert wholebody_source(name) == want, name


def test_skeleton_with_hand_points_maps_to_wholebody():
    entry = {"keypoints": ["left_wrist", "left_thumb4", "left_index_finger4", "right_big_toe"]}
    label = {"sublabels": [{"id": 1, "name": "left_wrist"}, {"id": 2, "name": "L thumb tip"},
                           {"id": 3, "name": "left index tip"}, {"id": 4, "name": "right big toe"}]}
    cfg = skeleton_config(entry, label)
    idx = {p["name"]: p["index"] for p in cfg["keypoints"]}
    assert idx == {"left_wrist": 9, "L thumb tip": WHOLEBODY.index("left_thumb4"),
                   "left index tip": WHOLEBODY.index("left_index_finger4"), "right big toe": WHOLEBODY.index("right_big_toe")}
    assert K.needs_wholebody(cfg)


def _hand_cfg():
    names = ["left_wrist", "left_thumb4", "right_big_toe"]
    kps = [{"label_id": 300 + i, "name": n, "index": WHOLEBODY.index(n)} for i, n in enumerate(names)]
    return {"label_id": 9, "name": "person", "prompts": ["person"], "threshold": 0.5, "keypoints": kps, "edges": [],
            "shape": "skeleton", "supported": True, "enabled": True, "attributes": [], "role": "body", "min_area_px": 30}


def test_hand_points_use_wholebody_model_and_fall_back_without_it():
    from backend.engines.pose import MockBodyPose

    image = np.zeros((H, W, 3), np.uint8)
    cfg = _hand_cfg()
    body = Lazy(MockBodyPose().load())
    model, lazy = K.pick_body_model(cfg, body, Lazy(MockWholeBody().load()), [])
    props = K.body_proposals(None, None, image, cfg, {}, model, [], boxes=[[100, 100, 300, 600]])
    thumb = next(e for e in props[0]["elements"] if e["name"] == "left_thumb4")
    assert not thumb["outside"], "có pose toàn thân: điểm tay có toạ độ"

    warnings = []
    missing = LazyEngine(lambda: (_ for _ in ()).throw(RuntimeError("chưa cài")), "pose toàn thân")
    model, lazy = K.pick_body_model(cfg, body, missing, warnings)
    assert lazy is body and any("pose toàn thân" in w for w in warnings)
    props = K.body_proposals(None, None, image, cfg, {}, model, [], boxes=[[100, 100, 300, 600]])
    thumb = next(e for e in props[0]["elements"] if e["name"] == "left_thumb4")
    assert thumb["outside"], "không có model: điểm tay để outside"


def test_image_side_swap_moves_hands_with_arms():
    coords = np.zeros((133, 2))
    scores = np.ones(133)
    coords[5], coords[6] = (300, 100), (100, 100)       # vai trái của người nằm bên phải ảnh (quay mặt về camera)
    coords[11], coords[12] = (280, 300), (120, 300)
    coords[91:112] = (320, 200)                         # bàn tay trái của người: bên phải ảnh
    coords[112:133] = (80, 200)
    out, _, _ = K.to_image_sides(coords, scores, 0.3)
    assert out[91][0] < out[112][0], "đổi phía thì bàn tay đi theo tay: 'left' là bên trái ảnh"


def test_hand_box_side_from_wholebody_pose():
    engine = ScriptedEngine({"person": [(rect(400, 100, 600, 650), 0.9)]})
    wb = MockWholeBody().load()
    (coords, _), = wb.estimate(np.zeros((H, W, 3), np.uint8), [[400, 100, 600, 650]])
    lh = coords[91:112].mean(0)
    cfgs = [{"label_id": 5, "name": "hand", "label_attributes": [{"name": "side", "values": ["left", "right"]}]}]
    proposals = [{"label_id": 5, "type": "rectangle", "bbox": [lh[0] - 15, lh[1] - 15, lh[0] + 15, lh[1] + 15], "flags": []}]
    n = K.hand_sides(engine, {}, np.zeros((H, W, 3), np.uint8), proposals, cfgs, Lazy(wb), [])
    assert n == 1 and proposals[0]["attributes"]["side"] == "left"
    assert proposals[0]["auto_attributes"]["side"] == "left" and proposals[0]["flags"]


# -------------------------------------------------------------- RTMW (ONNX) --
def test_simcc_decode_and_crop_mapping():
    eng = RTMWEngine.__new__(RTMWEngine)
    eng.input_w, eng.input_h = 192, 256
    img = np.zeros((H, W, 3), np.uint8)
    img[300:310, 500:510] = 255
    inp, center, scale = eng._prep(img, [400, 100, 600, 500])
    assert inp.shape == (3, 256, 192)
    sx, sy = np.zeros((1, 384)), np.zeros((1, 512))
    sx[0, 192] = 0.9          # giữa input (x = 96)
    sy[0, 256] = 0.5          # giữa input (y = 128)
    pts, sc = decode_simcc(sx, sy, center, scale, (192, 256), gain=1.0)
    assert np.allclose(pts[0], center, atol=1.0) and abs(sc[0] - 0.5) < 1e-6


# --------------------------------------------------------- re-ID và độ sâu --
def test_reid_separates_people_with_same_colour_layout():
    img = np.zeros((H, W, 3), np.uint8)
    img[100:400, 100:200] = (200, 30, 30)                # áo đỏ, quần đỏ
    img[100:250, 400:500] = (200, 30, 30)                # áo đỏ, quần xanh
    img[250:400, 400:500] = (30, 30, 200)
    a, b, a2 = MockReID().load().embed(img, [[100, 100, 200, 400], [400, 100, 500, 400], [100, 100, 200, 400]])
    assert tracking.emb_similarity(a, a2) > 0.99 and tracking.emb_similarity(a, b) < 0.9


def test_depth_marks_only_the_person_behind_as_occluded(store):
    """Hai người chồng nhau: người đứng trước (gần camera) visible, người đứng sau occluded."""
    from backend.profiles import resolve_labels

    profile = store.get("auto", ["person"], "track", ["rectangle"])
    cfgs = resolve_labels(profile, [{"id": 1, "name": "person", "type": "rectangle", "attributes": []}])
    red, blue = [0.0] * 32, [0.0] * 32
    red[1], blue[20] = 1.0, 1.0
    frames = []
    for f in range(60):
        front = [500, 250, 650, 650]
        back_x = 300 + f * 4
        back = [back_x, 260, back_x + 140, 640]
        dets = [{"label_id": 1, "box": front, "score": 0.9, "hist": red, "near": 0.8},
                {"label_id": 1, "box": back, "score": 0.9, "hist": blue, "near": 0.4}]
        frames.append({"frame": f, "dets": dets})
    result = tracking.build_tracks(frames, cfgs, profile, W, H, 0, 59)
    by_hist = {t["shapes"][0]["points"][0] < 450: t for t in result["tracks"]}
    behind, ahead = by_hist[True], by_hist[False]
    occ = lambda t, f: next(s for s in reversed(t["shapes"]) if s["frame"] <= f)  # noqa: E731
    assert occ(behind, 40)["occluded"] or occ(behind, 40)["outside"], "phía sau người đứng trước: bị che"
    assert not occ(ahead, 40)["occluded"] and not occ(ahead, 40)["outside"], "người đứng trước không bị tính là bị che"


def test_box_closeness():
    d = MockDepth().load().depth(np.zeros((100, 50, 3), np.uint8))
    assert box_closeness(d, [0, 80, 50, 100]) > box_closeness(d, [0, 0, 50, 20])


def test_track_detect_adds_embedding_and_depth_by_quality(image_b64):  # noqa: F811
    from fastapi.testclient import TestClient

    from backend.app import app

    body = {"profile_id": "mot_person_track_v1", "labels": [{"id": 1, "name": "person", "type": "rectangle"}]}
    frames = [{"frame": 0, "image_b64": image_b64}]
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        light = client.post("/track/detect", json=dict(body, quality="light", frames=frames)).json()
        rich = client.post("/track/detect", json=dict(body, quality="max", frames=frames)).json()
        assert all("emb" not in d for d in light["frames"][0]["dets"])
        dets = rich["frames"][0]["dets"]
        assert dets and all(d.get("emb") for d in dets)
        if len(dets) > 1:
            assert all(d.get("near") is not None for d in dets)
        loaded = client.get("/health").json()["vram"]
        assert "loaded" in loaded
