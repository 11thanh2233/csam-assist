"""Theo dõi qua cả job theo guideline Lab 5: cùng ID khi khuất ngắn, box toàn thân, outside, keyframe."""
import numpy as np

from test_api import image_b64  # noqa: F401 - fixture dùng chung

from backend import geometry as G
from backend import tracking
from backend.profiles import resolve_labels

W, H = 1280, 720
RED = [0.0] * 32
RED[1] = 1.0
BLUE = [0.0] * 32
BLUE[20] = 1.0
GREEN = [0.0] * 32
GREEN[10] = 1.0


def person_a(f):
    """Đi từ trái sang phải, lại gần camera dần (box to ra)."""
    h = 200 + f * 0.8
    x = 100 + f * 7       # 0.9.6: luôn nằm trong khung (box ra ngoài mép là đã ra khỏi khung: outside)
    return [x, 600 - h, x + h * 0.4, 600]


def person_b(f):
    """Đi từ phải sang trái, đứng xa hơn A (đáy box cao hơn)."""
    x = 1150 - f * 9
    return [x, 380, x + 70, 560]


def cvat_box(shapes, frame):
    """Box CVAT sẽ hiển thị ở `frame`: nội suy tuyến tính giữa hai keyframe, None nếu outside."""
    before = [s for s in shapes if s["frame"] <= frame]
    if not before:
        return None
    k0 = before[-1]
    if k0["outside"]:
        return None
    after = [s for s in shapes if s["frame"] > frame]
    if not after or k0["frame"] == frame:
        return k0["points"], k0["occluded"]
    k1 = after[0]
    u = (frame - k0["frame"]) / (k1["frame"] - k0["frame"])
    b = [a + u * (c - a) for a, c in zip(k0["points"], k1["points"])]
    return b, k0["occluded"]


def scenario():
    frames = []
    truth_a = {}
    for f in range(150):
        dets = []
        if not (60 <= f <= 80):            # A bị che hoàn toàn 21 frame (<= 60) -> phải giữ ID
            box = person_a(f)
            truth_a[f] = list(box)
            if 40 <= f <= 45:              # bị che nửa dưới: SAM3 chỉ thấy phần trên
                box = [box[0], box[1], box[2], box[1] + (box[3] - box[1]) * 0.55]
            dets.append({"label_id": 1, "box": box, "score": 0.9, "hist": RED})
        dets.append({"label_id": 1, "box": person_b(f), "score": 0.85, "hist": BLUE})
        if 10 <= f <= 12:                  # phát hiện nhầm 3 frame
            dets.append({"label_id": 1, "box": [900, 100, 930, 160], "score": 0.6, "hist": GREEN})
        frames.append({"frame": f, "dets": dets})
    return frames, truth_a


def build(store):
    profile = store.get("auto", ["person"], "track", ["rectangle"])
    assert profile["id"] == "mot_person_track_v1"
    cfgs = resolve_labels(profile, [{"id": 1, "name": "person", "type": "rectangle", "attributes": []}])
    frames, truth_a = scenario()
    return tracking.build_tracks(frames, cfgs, profile, W, H, 0, 149), truth_a


def test_ids_survive_crossing_and_short_occlusion(store):
    result, truth_a = build(store)
    tracks = result["tracks"]
    long_tracks = [t for t in tracks if t["visible_frames"] >= 5]
    assert len(long_tracks) == 2, [(t["tid"], t["start"], t["end"]) for t in tracks]
    a = next(t for t in long_tracks if t["start"] == 0 and t["shapes"][0]["points"][0] < 640)
    b = next(t for t in long_tracks if t is not a)
    # 0.9.6: B đi hẳn ra khỏi mép trái từ frame 136 (box toàn nằm ngoài ảnh) -> outside từ đó
    assert a["end"] == 149 and b["end"] == 135 and a["gaps"] == 1
    assert any(f["code"] == "id_check" and "khuất" in f["message"] for f in a["flags"])
    # B chạy hết 150 frame không đổi ID dù đi cắt ngang A ở khoảng frame 58
    assert b["visible_frames"] == 136 and b["shapes"][-1]["frame"] == 136 and b["shapes"][-1]["outside"]


def test_outside_during_full_occlusion_then_back(store):
    result, _ = build(store)
    a = next(t for t in result["tracks"] if t["gaps"] == 1)
    off = [s for s in a["shapes"] if s["outside"]]
    assert [s["frame"] for s in off] == [60]       # outside từ frame mất hẳn
    back = [s for s in a["shapes"] if s["frame"] == 81]
    assert back and not back[0]["outside"]          # hiện lại: keyframe mới, tắt outside
    assert cvat_box(a["shapes"], 70) is None


def test_amodal_box_and_occluded_flag(store):
    result, truth_a = build(store)
    a = next(t for t in result["tracks"] if t["gaps"] == 1)
    for f in range(41, 45):
        box, occluded = cvat_box(a["shapes"], f)
        assert occluded, f
        assert G.box_iou(box, truth_a[f]) >= 0.7, (f, box, truth_a[f])   # box toàn thân, không co theo phần thấy
    box, occluded = cvat_box(a["shapes"], 20)
    assert not occluded


def test_interpolation_never_drifts_and_keyframe_budget(store):
    result, truth_a = build(store)
    a = next(t for t in result["tracks"] if t["gaps"] == 1)
    for f, truth in truth_a.items():
        shown = cvat_box(a["shapes"], f)
        assert shown is not None, f
        assert G.box_iou(shown[0], truth) >= 0.7, (f, shown[0], truth)  # guideline: IoU < 0.7 là trôi
    gaps = np.diff([s["frame"] for s in a["shapes"]])
    assert gaps.max() <= 21
    assert 8 <= a["keyframes"] <= 30
    assert not any(f["code"] == "drift" for f in a["flags"])


def test_short_false_track_unchecked(store):
    result, _ = build(store)
    short = [t for t in result["tracks"] if t["visible_frames"] < 5]
    assert len(short) == 1 and not short[0]["checked"]
    assert any(f["code"] == "false_track" for f in short[0]["flags"])


def test_long_occlusion_gets_new_id(store):
    """Khuất quá 60 frame -> track mới (guideline mục 3), kèm gợi ý gộp để người annotate quyết."""
    profile = store.get("auto", ["person"], "track", ["rectangle"])
    cfgs = resolve_labels(profile, [{"id": 1, "name": "person", "type": "rectangle", "attributes": []}])
    frames = []
    for f in range(150):
        dets = []
        if f < 20 or f > 100:
            dets.append({"label_id": 1, "box": [600, 300, 680, 520], "score": 0.9, "hist": RED})
        frames.append({"frame": f, "dets": dets})
    tracks = tracking.build_tracks(frames, cfgs, profile, W, H, 0, 149)["tracks"]
    assert len(tracks) == 2 and tracks[0]["end"] == 19 and tracks[1]["start"] == 101
    # 81 frame khuất > 60: không tự gộp, cũng không gợi ý gộp (vượt ngưỡng của lớp)
    assert not any("#1" in f["message"] for f in tracks[1]["flags"])


def test_new_track_near_lost_one_gets_merge_hint(store):
    """Khuất <= 60 frame nhưng ngoại hình đổi (ví dụ ánh sáng): track mới, kèm gợi ý gộp với track cũ."""
    profile = store.get("auto", ["person"], "track", ["rectangle"])
    cfgs = resolve_labels(profile, [{"id": 1, "name": "person", "type": "rectangle", "attributes": []}])
    frames = []
    for f in range(150):
        dets = []
        if f < 40:
            dets.append({"label_id": 1, "box": [600, 300, 680, 520], "score": 0.9, "hist": RED})
        elif f > 70:
            dets.append({"label_id": 1, "box": [620, 300, 700, 520], "score": 0.9, "hist": GREEN})
        frames.append({"frame": f, "dets": dets})
    tracks = tracking.build_tracks(frames, cfgs, profile, W, H, 0, 149)["tracks"]
    assert len(tracks) == 2
    assert any(f["code"] == "id_check" and "#1" in f["message"] for f in tracks[1]["flags"])


def test_sam3_video_tracking_via_api_keeps_model_ids(image_b64):
    """Theo dõi bằng SAM3 Video (ở đây là bản giả lập): ID lấy từ model, dựng track như cách cũ."""
    from fastapi.testclient import TestClient

    from backend.app import app

    labels = [{"id": 1, "name": "person", "type": "rectangle"}]
    body = {"profile_id": "mot_person_track_v1", "labels": labels}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        start = client.post("/track/video/start", json=body).json()
        assert start["engine"] == "mock_video" and start["prompts"]
        frames = []
        for i in range(0, 12, 4):
            res = client.post("/track/video/frames", json=dict(body, session_id=start["session_id"],
                                                               frames=[{"frame": f, "image_b64": image_b64} for f in range(i, i + 4)])).json()
            frames += res["frames"]
        assert len(frames) == 12 and all(d.get("obj_id") for fr in frames for d in fr["dets"])
        built = client.post("/track/build", json=dict(body, frames=frames, width=frames[0]["width"], height=frames[0]["height"],
                                                      start=0, stop=11)).json()
        n_objects = len({d["obj_id"] for d in frames[0]["dets"]})
        assert len(built["tracks"]) == n_objects, "mỗi ID của model đúng một track"
        assert client.post("/track/video/end", json={"session_id": start["session_id"]}).json()["ok"]
        gone = client.post("/track/video/frames", json=dict(body, session_id=start["session_id"], frames=[{"frame": 12, "image_b64": image_b64}]))
        assert gone.status_code == 400 and "Phiên theo dõi" in gone.json()["detail"]


def test_sam3_video_tracker_reads_transformers_outputs():
    """Phần đọc kết quả của Sam3VideoTracker, với model/processor giả có cùng dạng đầu ra như tài liệu transformers."""
    import contextlib
    import types

    import numpy as np

    from backend.engines.sam3_video import Sam3VideoTracker

    class Arr(np.ndarray):
        def float(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.asarray(self)

    class Px:
        def to(self, dtype):
            return self

    class Proc:
        def init_video_session(self, **kw):
            return {"kw": kw}

        def add_text_prompt(self, session, prompts):
            return dict(session, prompts=prompts)

        def __call__(self, images, device, return_tensors):
            return types.SimpleNamespace(pixel_values=[Px()], original_sizes=[[720, 1280]])

        def postprocess_outputs(self, session, outputs, original_sizes):
            return {"object_ids": np.array([3, 7]), "scores": np.array([0.9, 0.6]).view(Arr),
                    "boxes": np.array([[1, 2, 30, 40], [50, 60, 90, 100]], float).view(Arr),
                    "prompt_to_obj_ids": {"person": np.array([3]), "car": np.array([7])}}

    tracker = Sam3VideoTracker("x", "cpu")
    tracker.processor, tracker.model, tracker.device, tracker.dtype = Proc(), (lambda **kw: "out"), "cpu", None
    tracker.torch = types.SimpleNamespace(no_grad=contextlib.nullcontext, cuda=types.SimpleNamespace(is_available=lambda: False))
    sid = tracker.start(["car", "person"])
    out = tracker.add_frame(sid, np.zeros((720, 1280, 3), np.uint8))
    assert out == [{"obj_id": 3, "prompt": "person", "box": [1.0, 2.0, 30.0, 40.0], "score": 0.9},
                   {"obj_id": 7, "prompt": "car", "box": [50.0, 60.0, 90.0, 100.0], "score": 0.6}]
    tracker.end(sid)
    assert not tracker.sessions
