"""0.9.10: phiên 3D tiếp tục được từ chỗ dừng.

- Claude chạy từng lô (max_batches): mỗi request ngắn, đếm được tiến độ, Pha 2 chạy một lần khi đủ track.
- Dừng (cancel) giữa các lô: phần đã chấm giữ lại, chạy tiếp chỉ chấm track còn thiếu.
- "Tiếp tục" không làm lại bước đã xong: geometry không dựng lại track khi không có frame mới (giữ kết quả camera).
- /progress cho panel biết phiên tới đâu; 404 khi backend đã khởi động lại.
- Panel có nút "Tiếp tục từ chỗ dừng", nhớ mã phiên theo job (tải lại trang vẫn tiếp tục được).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import backend.lidar_session as LS

ROOT = Path(__file__).resolve().parents[1]
CARS = ((12, 0), (0, 12), (-12, 0), (0, -12), (20, 8), (-20, -8))


def _car(x, y, n=900, seed=0):
    rng = np.random.default_rng(seed)
    return np.c_[rng.uniform(-2.2, 2.2, n) + x, rng.uniform(-0.9, 0.9, n) + y, rng.uniform(-1.75, -0.35, n)]


def _frame(f, cars=CARS):
    rng = np.random.default_rng(f)
    ground = np.c_[rng.uniform(-40, 40, 6000), rng.uniform(-40, 40, 6000), rng.normal(-1.8, 0.02, 6000)]
    parts = [ground] + [_car(x + 0.3 * f, y, seed=f * 10 + i) for i, (x, y) in enumerate(cars)]
    return np.vstack(parts).astype(np.float32)


class FakeLLM:
    """Mỗi lần gọi trả 'giữ, Car' cho mọi id trong prompt. fail_on: các lần gọi (đếm từ 1) bị lỗi 529."""

    def __init__(self, fail_on=()):
        self.calls, self.fail_on = [], set(fail_on)

    def complete(self, system, text, images, max_tokens=4000):
        self.calls.append(text)
        if len(self.calls) in self.fail_on:
            raise RuntimeError("Error code: 529 - overloaded")
        ids = [int(s.split("id=")[1].split()[0]) for s in text.splitlines() if " id=" in s]
        return json.dumps({"tracks": [{"id": i, "keep": True, "label": "Car", "confidence": "high"} for i in ids]})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)          # ask_llm chờ khi thử lại: test không cần chờ thật


def _session(n_frames=6):
    from backend.lidar import DEFAULT_SIZES

    sess = LS.create({"car": 1}, {1: "Car"}, dict(DEFAULT_SIZES), {})
    for f in range(n_frames):
        LS.add_frames(sess, [(f, _frame(f))], {})
    LS.run_geometry(sess, limit=100)
    assert len(sess.track["tracks"]) >= 4, "cần nhiều track để chia lô"
    return sess


def _p1_ids(llm):
    """id track đã gửi Claude ở Pha 1 (prompt Pha 2 / audit không tính)."""
    out = []
    for text in llm.calls:
        if "Second-pass" in text:                               # prompt kiểm duyệt Pha 2
            continue
        out += [int(s.split("id=")[1].split()[0]) for s in text.splitlines() if " id=" in s]
    return out


# ------------------------------------------------------- Claude từng lô ---
def test_llm_one_batch_per_call_until_final():
    sess = _session()
    n = len(sess.track["tracks"])
    llm = FakeLLM()
    out = LS.run_llm(sess, llm, {"rules": {}}, None, batch_size=2, max_batches=1)
    assert out["judged"] == 2 and out["total"] == n and out["remaining"] == n - 2 and not out["final"], out
    assert "buckets" not in out, "chưa xong: chưa có 3 rổ cuối"
    calls = 1
    while not out["final"]:
        out = LS.run_llm(sess, llm, {"rules": {}}, None, batch_size=2, max_batches=1)
        calls += 1
        assert calls <= n + 2, "phải kết thúc"
    assert out["judged"] == n and out["remaining"] == 0 and sum(out["buckets"].values()) == n, out
    assert sorted(_p1_ids(llm)) == sorted(t["tid"] for t in sess.track["tracks"]), "mỗi track gửi Claude đúng một lần"
    assert LS.result(sess)["checked_by"]["claude"] == n


def test_llm_without_max_batches_runs_everything_like_before():
    sess = _session()
    out = LS.run_llm(sess, FakeLLM(), {"rules": {}}, None, 8)
    assert out["final"] and out["remaining"] == 0 and out["judged"] == len(sess.track["tracks"])


def test_cancel_between_batches_keeps_judged_and_resume_only_runs_rest():
    sess = _session()
    n = len(sess.track["tracks"])
    llm = FakeLLM()
    LS.run_llm(sess, llm, {"rules": {}}, None, batch_size=2, max_batches=1)
    sess.cancelled = True
    stopped = LS.run_llm(sess, llm, {"rules": {}}, None, batch_size=2)
    assert stopped["ran"] == 0 and stopped["judged"] == 2 and not stopped["final"], "dừng: không gọi Claude thêm"
    assert len(llm.calls) == 1
    assert LS.result(sess)["checked_by"]["claude"] == 2, "xem kết quả đã có: thấy 2 track đã chấm"
    sess.cancelled = False
    done = LS.run_llm(sess, llm, {"rules": {}}, None, batch_size=2)
    assert done["final"] and done["judged"] == n
    ids = _p1_ids(llm)
    assert len(ids) == len(set(ids)) == n, "chạy tiếp: track đã chấm không gửi lại"


def test_claude_error_in_one_batch_keeps_others_and_retry_rejudges():
    sess = _session()
    n = len(sess.track["tracks"])
    llm = FakeLLM(fail_on={1, 2, 3})                    # lô đầu lỗi (ask_llm thử lại 2 lần = 3 lần gọi)
    out = LS.run_llm(sess, llm, {"rules": {}}, None, batch_size=2, max_batches=1)
    assert out["judged"] == 2 and out["errors"], "lô lỗi: 2 track vào Cần xem kèm lý do, phiên không dừng"
    assert LS.progress(sess)["llm_failed"] == 2
    while not out["final"]:
        out = LS.run_llm(sess, llm, {"rules": {}}, None, batch_size=2, max_batches=1)
    assert out["errors"], "còn track Claude lỗi: vẫn báo khi xong"
    again = LS.run_llm(sess, llm, {"rules": {}}, None, batch_size=2, retry_failed=True)
    assert again["final"] and not again["errors"] and again["judged"] == n, again
    assert LS.progress(sess)["llm_failed"] == 0


# ------------------------------------------------- không làm lại việc đã xong ---
def test_geometry_without_new_frames_keeps_camera_and_claude_results():
    sess = _session()
    t = sess.track["tracks"][0]
    t["vision2d"] = {"verdict": "confirmed", "camera": "CAM_FRONT"}
    sess.llm_p1 = {t["tid"]: {"keep": True, "bucket": "high_confidence"}}
    r = LS.run_geometry(sess, limit=20)
    assert r["remaining"] == 0 and r["ran"] == 0
    assert sess.track["tracks"][0].get("vision2d", {}).get("verdict") == "confirmed", "tiếp tục: không mất kết quả camera"
    assert sess.llm_p1, "không mất kết quả Claude"


def test_new_frames_reset_stale_checks():
    sess = _session(n_frames=4)
    sess.llm_p1, sess.llm_final = {1: {"keep": True}}, True
    LS.add_frames(sess, [(4, _frame(4))], {})
    assert LS.run_geometry(sess, limit=20)["ran"] == 1
    assert sess.llm_p1 == {} and not sess.llm_final, "track dựng lại: số hiệu đổi, kết quả Claude cũ bỏ"


def test_progress_reports_each_step():
    sess = _session(n_frames=3)
    p = LS.progress(sess)
    assert p["frames"] == 3 and p["geometry"] == 3 and p["frame_list"] == [0, 1, 2]
    assert p["tracks"] >= 1 and p["vision"] == 0 and p["llm"] == 0 and not p["llm_final"]


# ------------------------------------------------------------------ API ---
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


LABELS = [{"id": 1, "name": "Car", "type": "cuboid"}]


def test_api_llm_batch_step_by_step_then_resume_after_cancel(client, monkeypatch):
    import backend.app as A

    llm = FakeLLM()
    monkeypatch.setattr(A, "_llm_for_3d", lambda: llm)
    frames = [{"frame": f, "data_b64": _pcd_b64(_frame(f))} for f in range(4)]
    sid = client.post("/lidar/v2/session/init", json={"labels": LABELS, "frames": frames}).json()["session_id"]
    client.post(f"/lidar/v2/session/{sid}/geometry", json={"labels": LABELS, "limit": 20})
    p = client.get(f"/lidar/v2/session/{sid}/progress").json()
    assert p["frame_list"] == [0, 1, 2, 3] and p["tracks"] >= 5, p
    one = client.post(f"/lidar/v2/session/{sid}/llm_batch",
                      json={"labels": LABELS, "batch_size": 4, "max_batches": 1}).json()
    assert one["judged"] == 4 and one["total"] == p["tracks"] and not one["final"], one
    # bấm dừng rồi "Tiếp tục": endpoint xoá cờ dừng (như bước geometry), chạy nốt phần còn thiếu
    client.post(f"/lidar/v2/session/{sid}/cancel", json={"labels": LABELS})
    rest = client.post(f"/lidar/v2/session/{sid}/llm_batch", json={"labels": LABELS, "batch_size": 4}).json()
    assert rest["final"] and rest["remaining"] == 0 and rest["judged"] == p["tracks"], rest
    assert client.get(f"/lidar/v2/session/{sid}/progress").json()["llm_final"]
    client.post(f"/lidar/v2/session/{sid}/close", json={"labels": LABELS})
    assert client.get(f"/lidar/v2/session/{sid}/progress").status_code == 404, "phiên mất: panel biết để chạy lại"


def test_api_rejects_bad_max_batches(client):
    r = client.post("/lidar/v2/session/abc/llm_batch", json={"labels": LABELS, "max_batches": 0})
    assert r.status_code == 422


# ---------------------------------------------------------------- panel ---
def test_panel_has_resume_controls():
    js = (ROOT / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
    for s in ("Tiếp tục từ chỗ dừng", "Xem kết quả đã có", "Bỏ phiên này", "function restoreLidarSession",
              "function saveLidarSession", "restoreLidarSession();", "max_batches: 1", "/progress`"):
        assert s in js, s
    assert "use_llm: true }), { timeout: 300000 }" not in js, "không còn một request Claude cho mọi track"
    assert "lidarTrackSessionOld" not in js and "@@" not in js.split("I18N_DATA")[0]
