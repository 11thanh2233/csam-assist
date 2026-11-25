"""0.9.7 Giai đoạn C: judge_3d (Claude Vision 3D 2 pha) + phiên theo lô /lidar/v2/session."""
import math

import numpy as np

import backend.judge_3d as J3
import backend.lidar_session as LS


# ------------------------------------------------------------ ảnh ---
def test_marked_image_draws_boxes():
    img = np.full((120, 160, 3), 200, np.uint8)
    out = J3.marked_image(img, [{"id": 1, "box": [20, 20, 80, 80]}])
    assert out.shape == img.shape
    # trong khung có màu đỏ (255,64,64) sau khi chuyển RGB->BGR khi encode; ở đây so trực tiếp
    assert np.any((out[:, :, 0] == 255) & (out[:, :, 2] == 64)) or np.any((out[:, :, 2] == 255))


def test_crop_track_pads_and_clips():
    img = np.zeros((200, 300, 3), np.uint8)
    c = J3.crop_track(img, [100, 50, 180, 120], pad=0.5)
    assert c.shape[0] > 70 and c.shape[1] > 80, str(c.shape)
    c2 = J3.crop_track(img, [-50, -50, 50, 50])           # vươn ra ngoài: cắt vào mép
    assert c2.size > 0


def test_bev_image_draws_tracks():
    t = [{"id": 1, "center": [10, 0, 1.0], "size": [4.4, 1.8, 1.5], "yaw": 0.3, "bev_points": [(10, 0), (11, 1)]}]
    img = J3.bev_image(t)
    assert img is not None and img.shape == (480, 480, 3)
    assert np.any((img == 190).all(axis=2)), "điểm LiDAR xám phải được vẽ"
    assert J3.bev_image([]) is None


def test_pick_keyframes_prefers_densest():
    tr = {"obs": {0: {"points": 100, "center": [5, 0, 1]}, 3: {"points": 900, "center": [8, 0, 1]},
                  7: {"points": 200, "center": [30, 0, 1]}}}
    assert J3.pick_keyframes(tr, limit=2) == [3, 7]


# ----------------------------------------------------------- prompt ---
def test_class_definitions_with_sizes():
    prof = {"rules": {"lidar": {"sizes": {"car": [((3.8, 5.0), (1.7, 1.9), (1.4, 1.6))]}}}}
    lines = J3.class_definitions(prof, {1: "Car"})
    assert len(lines) == 1 and "3.8-5.0" in lines[0] and "id 1" in lines[0]


def test_build_track_prompt_structure():
    prof = {"rules": {"lidar": {"ranges": {"car": 50.0}, "max_occluded_frames": 6}}, "exclude": ["poles"]}
    tracks = [{"id": 5, "cls": "car", "size": [4.4, 1.8, 1.5], "center": [10, 2, 0.8], "points": 320,
               "speed": 2.0, "start": 0, "end": 9}]
    text = J3.build_track_prompt(tracks, {1: "Car"}, prof, {"lessons": [{"text": "never label trash bins as Car"}]})
    assert "TASK LABELS" in text and "#1 id=5" in text and "moving" in text
    assert "RANGE LIMITS: car <= 50.0m" in text and "poles" in text
    assert "never label trash bins" in text


def test_build_audit_prompt():
    conf = [{"id": 5, "cls": "car", "size": [4.4, 1.8, 1.5], "center": [10, 0, 1],
             "issue": "tool says car, Claude says Truck", "candidates": ["car", "Truck"]}]
    text = J3.build_audit_prompt({1: "Car", 7: "Truck"}, conf)
    assert "Second-pass QA audit" in text and "tool says car" in text


# --------------------------------------------------------------- llm ---
class FakeLLM:
    def __init__(self, reply: str, fail_times: int = 0):
        self.reply, self.fail_times, self.calls = reply, fail_times, 0

    def complete(self, system, text, images=None, max_tokens=3000):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("mạng rớt (giả lập)")
        return self.reply


def test_ask_llm_retries_then_succeeds():
    llm = FakeLLM('{"tracks": [{"id": 1, "keep": true}]}', fail_times=1)
    out = J3.ask_llm(llm, "x", [])
    assert out["tracks"][0]["id"] == 1 and llm.calls == 2


def test_ask_llm_exhausts_retries():
    import backend.guideline.llm as GL

    llm = FakeLLM("{}", fail_times=5)
    try:
        J3.ask_llm(llm, "x", [], retries=1)
        assert False, "phải ném LLMError"
    except GL.LLMError:
        pass


def test_bucket_of_priorities():
    assert J3.bucket_of({}, {"keep": False}) == "suspected_noise"
    assert J3.bucket_of({}, {"keep": True, "uncertain": True}) == "needs_review"
    assert J3.bucket_of({"checked": False}, None) == "needs_review"
    assert J3.bucket_of({"flags": [{"code": "LOW_VISIBILITY"}], "checked": True}, None) == "high_confidence"


# -------------------------------------------------------------- pha 1 ---
def _track(tid, cls="car"):
    return {"id": tid, "tid": tid, "cls": cls, "size": [4.4, 1.8, 1.5], "center": [10, 2, 0.8], "points": 300,
            "start": 0, "end": 5, "checked": True}


def test_phase1_assigns_buckets_and_ignores_unknown_label():
    reply = ('{"tracks": [{"id": 1, "keep": true, "label": "Car", "confidence": "high"}, '
             '{"id": 2, "keep": false, "label": "FlyingSaucer", "confidence": "high"}, '
             '{"id": 3, "keep": true, "label": "Truck", "confidence": "low", "uncertain": true}]}')
    llm = FakeLLM(reply)
    res = J3.phase1(llm, [_track(1), _track(2), _track(3)], {1: "Car"}, {"rules": {"lidar": {}}})
    assert res[1]["bucket"] == "high_confidence" and res[1]["label"] == "Car"
    assert res[2]["label"] is None, "class ngoài danh sách: bỏ, không gán bừa"
    assert res[3]["bucket"] == "needs_review"


def test_phase1_llm_error_marks_needs_review():
    llm = FakeLLM("{}", fail_times=5)
    res = J3.phase1(llm, [_track(1)], {1: "Car"}, {"rules": {"lidar": {}}})
    assert res[1]["bucket"] == "needs_review" and res[1]["keep"] is None


def test_phase1_missing_track_gets_review():
    llm = FakeLLM('{"tracks": [{"id": 1, "keep": true, "label": "Car"}]}')
    res = J3.phase1(llm, [_track(1), _track(2)], {1: "Car"}, {"rules": {"lidar": {}}})
    assert res[2]["bucket"] == "needs_review"


# -------------------------------------------------------------- pha 2 ---
def test_phase2_audits_class_conflict():
    llm = FakeLLM('{"tracks": [{"id": 1, "final_label": "Truck", "bucket": "high_confidence"}]}')
    p1 = {1: {"keep": True, "label": "Truck", "bucket": "high_confidence"}}
    out = J3.phase2(llm, [_track(1, "car")], {1: "Car", 7: "Truck"}, p1)
    assert out[1]["bucket"] == "high_confidence" and out[1]["final_label"] == "Truck"


def test_phase2_keeps_phase1_when_llm_fails():
    llm = FakeLLM("{}", fail_times=5)
    p1 = {1: {"keep": True, "label": None, "bucket": "needs_review"}}
    out = J3.phase2(llm, [_track(1)], {1: "Car"}, p1)
    assert out[1]["bucket"] == "needs_review"


def test_summarize_counts():
    res = {1: {"bucket": "high_confidence"}, 2: {"bucket": "needs_review"}, 3: {"bucket": "suspected_noise"}}
    assert J3.summarize(res) == {"high_confidence": 1, "needs_review": 1, "suspected_noise": 1}


# ------------------------------------------------------- phiên theo lô ---
def _mk_session():
    labels = {"car": 1}
    return LS.create(labels, {1: "Car"}, dict(__import__("backend.lidar", fromlist=["DEFAULT_SIZES"]).DEFAULT_SIZES), {})


def _cloud(x=10.0, n=600, seed=0):
    rng = np.random.default_rng(seed)
    return np.c_[rng.uniform(-2.2, 2.2, n) + x, rng.uniform(-0.9, 0.9, n), rng.uniform(0.2, 1.7, n)]


def test_session_create_and_frames():
    sess = _mk_session()
    out = LS.add_frames(sess, [(0, _cloud(10, seed=0)), (1, _cloud(11, seed=1)), (0, _cloud(10, seed=2))], {})
    assert out["frames"] == 2, "frame trùng: bỏ qua"
    assert LS.get(sess.id) is sess
    assert LS.drop(sess.id) and LS.get(sess.id) is None


def test_session_geometry_batch_and_cancel():
    import backend.lidar as L

    sess = _mk_session()
    for f in range(4):
        LS.add_frames(sess, [(f, _cloud(10 + 0.5 * f, seed=f))], {})
    r = LS.run_geometry(sess, limit=2)
    assert r["ran"] == 2 and r["remaining"] == 2 and r["done"]["geometry"] == 2
    r2 = LS.run_geometry(sess, limit=10)
    assert r2["ran"] == 2 and r2["remaining"] == 0 and r2["tracks"] >= 1
    # hủy giữa chừng
    sess2 = _mk_session()
    for f in range(4):
        LS.add_frames(sess2, [(f, _cloud(10, seed=f))], {})
    sess2.cancelled = True
    r3 = LS.run_geometry(sess2, limit=10)
    assert r3["ran"] == 0 and r3["cancelled"]


def test_session_result_buckets_and_labels():
    import backend.lidar as L

    sess = _mk_session()
    for f in range(4):
        LS.add_frames(sess, [(f, _cloud(10 + 0.5 * f, seed=f))], {})
    LS.run_geometry(sess, limit=10)
    tid = sess.track["tracks"][0]["tid"]
    sess.llm = {tid: {"bucket": "high_confidence", "final_label": "Truck", "reason": "pickup with cargo bed"}}
    res = LS.result(sess)
    t = res["tracks"][0]
    assert t["bucket"] == "high_confidence"
    assert t["label_name"] == "Truck", "Claude chốt lại class: panel phải thấy"
    assert t["llm_reason"] == "pickup with cargo bed"
    assert sum(res["buckets"].values()) == len(res["tracks"])


def test_session_result_without_llm():
    sess = _mk_session()
    LS.add_frames(sess, [(0, _cloud(10, seed=0)), (1, _cloud(11, seed=1))], {})
    LS.run_geometry(sess, limit=10)
    res = LS.result(sess)
    for t in res["tracks"]:
        assert t["bucket"] in ("high_confidence", "needs_review", "suspected_noise")


def test_session_evicts_oldest():
    made = []
    for _ in range(LS.MAX_SESSIONS + 2):
        made.append(_mk_session())
    assert len(LS._sessions) <= LS.MAX_SESSIONS
    assert made[0].id not in LS._sessions, "phiên cũ nhất bị dọn"
