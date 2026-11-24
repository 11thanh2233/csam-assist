"""0.9.6: trạng thái visible / occluded / outside theo % diện tích toàn thân bị che, ngưỡng theo profile, tìm lại người
bị mất dấu, giữ ID khi hiện lại, quản lý VRAM, huỷ lượt chạy."""
import numpy as np
import pytest

from test_api import image_b64  # noqa: F401 - fixture dùng chung

from backend import autoprofile, tracking
from backend.profiles import resolve_labels
from backend.runtime_tools import QUALITY_KEEP_S, Resources

W, H = 1280, 720
RED = [0.0] * 32
RED[1] = 1.0
BLUE = [0.0] * 32
BLUE[20] = 1.0


def walker(f):
    """Đi chậm từ trái sang phải, luôn trong khung."""
    x = 200 + f * 3
    return [x, 300, x + 120, 600]


def cut(box, hidden):
    """SAM3 chỉ thấy phần trên: `hidden` là phần chiều cao bị che từ dưới lên."""
    return [box[0], box[1], box[2], box[3] - (box[3] - box[1]) * hidden]


def state_at(track, frame):
    shapes = [s for s in track["shapes"] if s["frame"] <= frame]
    if not shapes:
        return None
    s = shapes[-1]
    return "outside" if s["outside"] else ("occluded" if s["occluded"] else "visible")


def run(store, frames, rules=None):
    profile = store.get("auto", ["person"], "track", ["rectangle"])
    if rules:
        profile = dict(profile, rules=dict(profile.get("rules") or {}))
        profile["rules"]["tracking"] = dict(profile["rules"].get("tracking") or {}, **rules)
    cfgs = resolve_labels(profile, [{"id": 1, "name": "person", "type": "rectangle", "attributes": []}])
    return tracking.build_tracks(frames, cfgs, profile, W, H, 0, len(frames) - 1)


def scenario(hidden_by_frame, n=90, other=True):
    frames = []
    for f in range(n):
        dets = []
        hid = hidden_by_frame(f)
        if hid is not None:
            dets.append({"label_id": 1, "box": cut(walker(f), hid), "score": 0.9, "hist": RED})
        if other:
            x = 1100 - f * 2
            dets.append({"label_id": 1, "box": [x, 320, x + 100, 580], "score": 0.85, "hist": BLUE})
        frames.append({"frame": f, "dets": dets})
    return frames


def main_track(result):
    return next(t for t in result["tracks"] if t["shapes"][0]["points"][0] < 640)


# --------------------------------------------------------------- trạng thái --
def test_visibility_states_hysteresis_and_blips():
    st = tracking.visibility_states([0.1, 0.36, 0.33, 0.36, 0.2, 0.1], 0.35, 0.6)
    # 0.33 nằm trong vùng trễ: vẫn occluded; 0.2 rõ ràng dưới ngưỡng: về visible
    assert st == [0, 1, 1, 1, 0, 0]
    # một frame lẻ vượt ngưỡng không làm đổi trạng thái
    assert tracking.visibility_states([0.1, 0.1, 0.5, 0.1, 0.1], 0.35, 0.6) == [0, 0, 0, 0, 0]
    assert tracking.visibility_states([0.1, 0.7, 0.7, 0.7, 0.1, 0.1], 0.35, 0.6) == [0, 2, 2, 2, 0, 0]


def test_mostly_visible_is_not_occluded_and_half_hidden_is(store):
    """Bị che 15% (vẫn thấy gần hết): visible. Bị che 50%: occluded. Không còn luật chồng khung."""
    frames = scenario(lambda f: 0.15 if 20 <= f < 40 else (0.5 if 50 <= f < 65 else 0.0))
    t = main_track(run(store, frames))
    assert state_at(t, 30) == "visible"
    assert state_at(t, 57) == "occluded"
    assert state_at(t, 75) == "visible"


def test_heavily_hidden_is_outside_then_same_id_returns(store):
    """Bị che 70% từ frame 30 đến 44: outside ở đó, rồi hiện lại cùng ID (không outside tới hết video)."""
    frames = scenario(lambda f: 0.7 if 30 <= f < 45 else 0.0)
    result = run(store, frames)
    left = [t for t in result["tracks"] if t["shapes"][0]["points"][0] < 640]
    assert len(left) == 1, [(t["tid"], t["start"], t["end"]) for t in left]
    t = left[0]
    assert state_at(t, 20) == "visible"
    assert state_at(t, 37) == "outside"
    assert state_at(t, 60) == "visible" and t["end"] == 89


def test_thresholds_follow_profile(store):
    """Cùng dữ liệu (che 50%), profile khác ngưỡng cho kết quả khác."""
    frames = scenario(lambda f: 0.5 if 30 <= f < 45 else 0.0)
    loose = main_track(run(store, frames, {"occluded_hidden": 0.6, "outside_hidden": 0.9}))
    strict = main_track(run(store, frames, {"occluded_hidden": 0.2, "outside_hidden": 0.45}))
    assert state_at(loose, 37) == "visible"
    assert state_at(strict, 37) == "outside"
    assert run(store, frames)["thresholds"] == {"occluded_hidden": 0.35, "outside_hidden": 0.6}


def test_guideline_thresholds_are_read():
    md = ("Đối tượng bị che hơn 35% diện tích thì gán occluded.\n"
          "Nếu bị che trên 60% thì đặt outside.")
    assert autoprofile.hidden_thresholds(md) == (35.0, 60.0)
    assert autoprofile.hidden_thresholds("Không có ngưỡng.") == (None, None)


# ----------------------------------------------------------------- tìm lại --
def test_missed_frames_become_rescue_requests(store):
    """Model bỏ sót người 10 frame giữa video, người vẫn ở giữa khung: xin SAM3 tìm lại ở vị trí dự đoán."""
    frames = scenario(lambda f: None if 40 <= f < 50 else 0.0)
    result = run(store, frames)
    asked = sorted({r["frame"] for r in result["rescue"]})
    assert asked == list(range(40, 50))
    r = next(r for r in result["rescue"] if r["frame"] == 45)
    assert abs(r["box"][0] - walker(45)[0]) < 5

    # tìm lại được (bị che 50%): frame đó occluded thay vì outside
    for fr in frames:
        if 40 <= fr["frame"] < 50:
            fr["dets"].append({"label_id": 1, "box": cut(walker(fr["frame"]), 0.5), "score": 0.4, "hist": RED, "rescued": True})
    t = main_track(run(store, frames))
    assert state_at(t, 45) == "occluded"


def test_rescue_endpoint(image_b64):  # noqa: F811
    from fastapi.testclient import TestClient

    from backend.app import app

    labels = [{"id": 1, "name": "person", "type": "rectangle"}]
    body = {"profile_id": "mot_person_track_v1", "labels": labels}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        det = client.post("/track/detect", json=dict(body, frames=[{"frame": 0, "image_b64": image_b64}])).json()
        boxes = [{"label_id": d["label_id"], "box": d["box"]} for d in det["frames"][0]["dets"]]
        res = client.post("/track/rescue", json=dict(body, frames=[{"frame": 3, "image_b64": image_b64, "boxes": boxes}])).json()
        assert res["frames"][0]["frame"] == 3
        for d in res["frames"][0]["dets"]:
            assert d["rescued"] and "fill" in d and len(d["hist"]) == 32
        # huỷ lượt: backend không chạy tiếp
        client.post("/track/cancel", json={"session_id": "abc"})
        stopped = client.post("/track/detect", json=dict(body, request_id="abc", frames=[{"frame": 0, "image_b64": image_b64}])).json()
        assert stopped["cancelled"] and stopped["frames"] == []
        # ngưỡng: resolve báo nguồn; build nhận ngưỡng panel gửi
        info = client.post("/resolve", json=body).json()["track"]["thresholds"]
        assert info["source"] in ("guideline", "default") and 0 < info["occluded_hidden"] < info["outside_hidden"] <= 1
        built = client.post("/track/build", json=dict(body, frames=det["frames"], width=det["frames"][0]["width"],
                                                      height=det["frames"][0]["height"], start=0, stop=0,
                                                      thresholds={"occluded_hidden": 0.3, "outside_hidden": 0.7})).json()
        assert built["thresholds_source"] == "panel" and built["thresholds"]["outside_hidden"] == 0.7
        health = client.get("/health").json()
        assert "vram" in health


# -------------------------------------------------------------------- VRAM --
class FakeLazy:
    def __init__(self, need):
        self.need_mb, self.loaded, self.last, self.released = need, True, 0.0, 0

    def release(self, reason=""):
        self.loaded = False
        self.released += 1


def test_resources_free_least_recent_models(monkeypatch):
    res = Resources()
    a, b = FakeLazy(800), FakeLazy(4000)
    res.register("body", a)
    res.register("video", b)
    a.last, b.last = 10.0, 5.0
    free = {"v": 1000}
    monkeypatch.setattr(res, "mem", lambda: {"free_mb": free["v"], "total_mb": 8000, "allocated_mb": 0, "reserved_mb": 0})

    def fake_release(lazy):
        free["v"] += lazy.need_mb
    for lz in (a, b):
        orig = lz.release
        lz.release = (lambda o, l: (lambda reason="": (fake_release(l), o(reason))))(orig, lz)
    res.make_room(2500, keep={"body"})
    assert b.released == 1 and a.released == 0, "nhả model lâu không dùng nhất, giữ model đang cần"


def test_resources_idle_release_follows_quality(monkeypatch):
    import time as _t

    res = Resources()
    lz = FakeLazy(800)
    res.register("body", lz)
    lz.last = _t.time() - 120
    res.touch("max")
    res.last_activity -= 3600
    res.idle_check()
    assert lz.loaded, "mức Tối đa giữ model"
    res.touch("light")
    res.idle_check()
    assert lz.loaded, "backend vừa có việc: chưa nhả"
    res.last_activity -= 60          # rảnh 60 giây
    res.idle_check()
    assert not lz.loaded, f"mức Nhẹ nhả model sau {QUALITY_KEEP_S['light']} giây"


@pytest.mark.parametrize("q", ["light", "balanced", "quality", "max"])
def test_quality_levels_known(q):
    assert q in QUALITY_KEEP_S
