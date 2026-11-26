"""0.9.11:
- Model CenterPoint xuất từ mmdet3d lỗi ở bước tối ưu khi nạp (MatMulBnFusion_Gemm): backend thử lại với tối ưu tắt.
- Ghi dạng "shape từng frame": phiên trả cuboid của mọi frame (thấy thật / nội suy) + vật chỉ thấy ở 1 frame.
- Claude đọc guideline của project: định nghĩa label + đoạn liên quan (hoặc cả guideline / không gửi), gửi ở system
  để Anthropic cache được; không có guideline thì prompt giữ như cũ.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import backend.judge_3d as J3
import backend.lidar_onnx as LO
import backend.lidar_session as LS
import backend.tracking_3d as T3

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------- 1. nạp model 3D ---
class FakeOrt:
    """onnxruntime giả: lần nạp có tối ưu mặc định ném lỗi như file thật xuất từ mmdet3d."""

    class GraphOptimizationLevel:
        ORT_DISABLE_ALL = "off"

    class SessionOptions:
        graph_optimization_level = None

    def __init__(self, fail_default=True, fail_all=False):
        self.calls, self.fail_default, self.fail_all = [], fail_default, fail_all

    def InferenceSession(self, path, sess_options=None, providers=None, **kw):
        level = getattr(sess_options, "graph_optimization_level", None)
        self.calls.append(level)
        if self.fail_all or (level is None and self.fail_default):
            raise RuntimeError("[ONNXRuntimeError] : 1 : FAIL : Node (MatMulBnFusion_Gemm) Op (Gemm) "
                               "[ShapeInferenceError] First input does not have rank 2")

        class Sess:
            def get_inputs(self):
                return [type("I", (), {"name": n, "type": "tensor(float)"})() for n in ("voxels", "num_points", "coors")]
        return Sess()


@pytest.fixture
def fake_ort(monkeypatch):
    import sys

    def make(**kw):
        ort = FakeOrt(**kw)
        monkeypatch.setitem(sys.modules, "onnxruntime", ort)
        return ort
    return make


def test_model3d_retries_without_graph_optimization(fake_ort):
    ort = fake_ort()
    m = LO.CenterPointONNX("cp.onnx").load()
    assert m.ready and not m.error and m.optimized is False, m.error
    assert ort.calls == [None, "off"], "lần 2 tắt tối ưu đồ thị (ORT_DISABLE_ALL)"


def test_model3d_normal_file_keeps_default_optimization(fake_ort):
    ort = fake_ort(fail_default=False)
    m = LO.CenterPointONNX("cp.onnx").load()
    assert m.ready and m.optimized is True and ort.calls == [None]


def test_model3d_broken_file_reports_first_error(fake_ort):
    fake_ort(fail_all=True)
    m = LO.CenterPointONNX("cp.onnx").load()
    assert not m.ready and "MatMulBnFusion_Gemm" in m.error


def test_health_reports_model3d_error(monkeypatch):
    import backend.app as A

    class M:
        error = "không nạp được model 3D ONNX: hỏng"

    monkeypatch.setattr(A, "_onnx3d_path", lambda: "x.onnx")
    monkeypatch.setitem(A._lidar3d, "path", "x.onnx")
    monkeypatch.setitem(A._lidar3d, "obj", M())
    assert "hỏng" in A._model3d_error()
    monkeypatch.setitem(A._lidar3d, "path", "khac.onnx")
    assert A._model3d_error() == "", "file khác file đang chọn: chưa thử nạp, không báo lỗi cũ"


def test_export_script_and_notebook():
    src = (ROOT / "scripts" / "export_centerpoint_onnx.py").read_text(encoding="utf-8")
    assert "/content/py310/bin/python" in src and "ORT_DISABLE_ALL" in src and "uv venv -p 3.10" in src
    assert "~20 MB" not in src and "mim install" not in src
    nb = json.loads((ROOT / "scripts" / "export_centerpoint_onnx.ipynb").read_text(encoding="utf-8"))
    code = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert "/content/py310/bin/python export_centerpoint_onnx.py" in code and "files.download" in code
    assert "{PY}" not in code, "không dùng biến PY (mất khi runtime khởi động lại)"


# ------------------------------------------------- 2. shape từng frame ---
def _obj(x, f, pts=400):
    return {"label_id": 1, "center": [x + 0.5 * f, 0.0, -1.0], "size": [4.4, 1.8, 1.5], "yaw": 0.0, "points": pts,
            "cls": "car", "confidence": 0.9, "checked": True}


def test_build_keeps_every_frame_with_seen_flag():
    frames = [{"frame": f, "objects": [_obj(10, f)] if f not in (3, 4) else []} for f in range(8)]
    res = T3.build(frames, {"max_occluded_frames": 6, "min_track_frames": 2}, {1: "Car"}, keep_obs=True)
    t = res["tracks"][0]
    got = {x["frame"]: x["seen"] for x in t["_frames"]}
    assert sorted(got) == list(range(8)), "mọi frame từ đầu tới cuối track (không gồm frame outside)"
    assert got[3] is False and got[4] is False and got[0] and got[7], "frame bị che = nội suy (seen False)"
    assert all(len(x["points"]) == 16 for x in t["_frames"])
    sizes = {tuple(x["points"][6:9]) for x in t["_frames"]}
    assert len(sizes) == 1, "kích thước khoá cho cả track"
    assert "_frames" not in T3.build(frames, {"min_track_frames": 2}, {1: "Car"})["tracks"][0], "chỉ khi keep_obs"


def _session(n=5):
    from backend.lidar import DEFAULT_SIZES

    sess = LS.create({"car": 1}, {1: "Car"}, dict(DEFAULT_SIZES), {"min_track_frames": 2})
    for f in range(n):
        sess.frames.append((f, np.zeros((1, 3), np.float32)))
        objs = [_obj(10, f)]
        if f == 2:
            objs.append(dict(_obj(-15, 0), center=[-15.0, 5.0, -1.0]))     # chỉ thấy ở frame 2
        sess.detected[f] = {"objects": objs}
    frames_in = [{"frame": f, "objects": sess.detected[f]["objects"]} for f in sorted(sess.detected)]
    sess.track = T3.build(frames_in, sess.rule, sess.names, keep_obs=True)
    return sess


def test_result_per_frame_and_singles():
    sess = _session()
    plain = LS.result(sess)
    assert "frames" not in plain["tracks"][0] and "singles" not in plain, "mặc định như cũ"
    res = LS.result(sess, per_frame=True)
    t = res["tracks"][0]
    assert [x["frame"] for x in t["frames"]] == [0, 1, 2, 3, 4] and all(x["seen"] for x in t["frames"])
    assert all(not k.startswith("_") for tr in res["tracks"] for k in tr), "_obs / _frames không gửi về panel"
    assert len(res["singles"]) == 1 and res["singles"][0]["frame"] == 2 and len(res["singles"][0]["points"]) == 16


def test_result_per_frame_keeps_claude_label():
    sess = _session()
    sess.names[2] = "Truck"
    tid = sess.track["tracks"][0]["tid"]
    sess.llm = {tid: {"bucket": "high_confidence", "final_label": "Truck"}}
    t = LS.result(sess, per_frame=True)["tracks"][0]
    assert t["label_id"] == 2 and t["frames"], "ghi shape dùng label Claude đã sửa"


def test_api_result_per_frame(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import app

    sess = _session()
    labels = [{"id": 1, "name": "Car", "type": "cuboid"}]
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        r = c.post(f"/lidar/v2/session/{sess.id}/result", json={"labels": labels, "per_frame": True}).json()
        assert r["tracks"][0]["frames"] and "singles" in r
        r2 = c.post(f"/lidar/v2/session/{sess.id}/result", json={"labels": labels}).json()
        assert "frames" not in r2["tracks"][0]


# ------------------------------------------------- 3. Claude đọc guideline ---
FILLER = "\n".join(f"Đoạn mô tả chung số {i} về quy trình làm việc của nhóm, không nói tới vật nào." for i in range(400))
GUIDE_MD = ("<!-- Nạp từ g.docx qua tab Guideline. -->\n\n# Giới thiệu\nTài liệu cho dự án.\n\n"
            "## Quy định chung\n" + FILLER + "\n\n"
            "## Cuboid 3D cho Truck\nPickup có thùng hở gán là Truck, không phải Car. Xe ba gác: Truck.\n\n"
            "## Car\nXe con 4-7 chỗ. Xe đỗ trong garage có mái che vẫn gán.\n\n"
            "## Thanh toán\nKhông liên quan tới nhãn.\n")
GUIDE = {"id": "du_an_3d", "title": "Guideline 3D Cuboid", "markdown": GUIDE_MD,
         "definitions": {"Car": "Xe con chở người", "Truck": "Xe tải, pickup có thùng hở", "Bus": "Xe buýt"}}
LABELS = {1: "Car", 2: "Truck"}


def test_guideline_related_has_definitions_and_relevant_sections():
    text, info = J3.guideline_context(GUIDE, LABELS, "related")
    assert "- Car: Xe con chở người" in text and "- Truck: Xe tải" in text
    assert "Xe buýt" not in text, "chỉ định nghĩa label của task"
    assert "Pickup có thùng hở gán là Truck" in text and "Xe đỗ trong garage" in text
    assert "Thanh toán" not in text, "đoạn không liên quan không gửi"
    assert "<!--" not in text
    assert len(text) <= J3.GUIDE_LIMITS["related"] and info["chars"] == len(text)
    assert info["mode"] == "related" and info["definitions"] == 2 and info["id"] == "du_an_3d"


def test_guideline_full_and_off():
    full, info = J3.guideline_context(GUIDE, LABELS, "full")
    assert len(full) <= J3.GUIDE_LIMITS["full"] and info["mode"] == "full" and "Giới thiệu" in full
    assert J3.guideline_context(GUIDE, LABELS, "off") == ("", None)
    assert J3.guideline_context(None, LABELS, "related") == ("", None)


class RecLLM:
    def __init__(self):
        self.calls = []

    def complete(self, system, text, images, max_tokens=4000, context=""):
        self.calls.append({"system": system, "text": text, "context": context})
        if "Second-pass" in text:
            return json.dumps({"tracks": []})
        ids = [int(s.split("id=")[1].split()[0]) for s in text.splitlines() if " id=" in s]
        return json.dumps({"tracks": [{"id": i, "keep": True, "label": "Truck", "confidence": "high"} for i in ids]})


class OldLLM:
    """LLM cũ không nhận `context`: không có guideline thì vẫn gọi được như trước."""

    def complete(self, system, text, images, max_tokens=4000):
        return json.dumps({"tracks": [{"id": 1, "keep": True, "label": "Car", "confidence": "high"}]})


def _tracks():
    return [{"id": 1, "center": [10, 0, 0], "size": [5.2, 1.9, 1.8], "yaw": 0.0, "points": 400, "cls": "car", "start": 0, "end": 3}]


def test_phases_send_guideline_as_context_same_for_every_batch():
    text, _ = J3.guideline_context(GUIDE, LABELS, "related")
    llm = RecLLM()
    tracks = _tracks() + [dict(_tracks()[0], id=2), dict(_tracks()[0], id=3)]
    p1 = J3.phase1(llm, tracks, LABELS, {}, None, None, 2, guide=text)
    assert len(llm.calls) == 2 and all(c["context"] == text for c in llm.calls), "guideline giống hệt nhau mọi lô"
    assert all("PROJECT GUIDELINE" in c["text"] for c in llm.calls) and text not in llm.calls[0]["text"]
    J3.phase2(llm, tracks, LABELS, p1, {}, guide=text)
    assert llm.calls[-1]["context"] == text and "Second-pass" in llm.calls[-1]["text"], "Pha 2 cũng có guideline"


def test_no_guideline_prompt_unchanged():
    llm = RecLLM()
    J3.phase1(llm, _tracks(), LABELS, {}, None, None, 8)
    assert llm.calls[0]["context"] == "" and "PROJECT GUIDELINE" not in llm.calls[0]["text"]
    assert J3.phase1(OldLLM(), _tracks(), LABELS, {}, None, None, 8)[1]["keep"] is True, "LLM cũ không có `context` vẫn chạy"


def test_anthropic_caches_guideline_block(monkeypatch):
    from backend.guideline import llm as L

    sent = {}

    def fake_post(url, headers, body, timeout):
        sent.clear()
        sent.update(body)
        return {"content": [{"type": "text", "text": "{}"}], "usage": {},
                "choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(L, "_post", fake_post)
    a = L.AnthropicLLM("k", "m")
    a.complete("SYS", "hi", None, context="GUIDE TEXT")
    assert sent["system"] == [{"type": "text", "text": "SYS"},
                              {"type": "text", "text": "GUIDE TEXT", "cache_control": {"type": "ephemeral"}}]
    a.complete("SYS", "hi", None)
    assert sent["system"] == "SYS", "không có guideline: gửi như cũ"
    o = L.OpenAICompatLLM("http://127.0.0.1:1/v1", "m")
    o.complete("SYS", "hi", None, context="GUIDE TEXT")
    assert sent["messages"][0]["content"] == "SYS\n\nGUIDE TEXT", "máy chủ OpenAI-compatible: ghép vào system"


@pytest.fixture
def guide_lib(tmp_path, monkeypatch):
    import yaml

    import backend.app as A

    root = tmp_path / "lib"
    (root / "reference").mkdir(parents=True)
    (root / "imported").mkdir()
    (root / "reference" / "du_an_3d.md").write_text(GUIDE_MD, encoding="utf-8")
    (root / "imported" / "du_an_3d.yaml").write_text(yaml.safe_dump(
        {"id": "du_an_3d", "title": "Guideline 3D Cuboid", "file": "du_an_3d.docx",
         "labels": [{"name": "Car", "definition": "Xe con chở người"}, {"name": "Truck", "definition": "Xe tải"}]},
        allow_unicode=True), encoding="utf-8")
    fake = type("G", (), {"lib": type("L", (), {"root": root})()})()
    monkeypatch.setattr(A.rt, "guide", fake)          # rt.guide chỉ có khi app khởi động: test dùng bản giả có thư viện
    return A


@pytest.mark.parametrize("profile", [
    {"id": "proj_p7", "guideline": "du_an_3d.docx", "guideline_id": "du_an_3d"},   # profile của project
    {"id": "gl_du_an_3d", "guideline": "du_an_3d.docx"},                           # nạp guideline ở tab Guideline
    {"id": "khac", "guideline": "du_an_3d.docx"},                                 # chỉ có tên file
])
def test_guideline_found_from_profile(guide_lib, profile):
    g = guide_lib._guideline_for(profile)
    assert g and g["id"] == "du_an_3d" and g["title"] == "Guideline 3D Cuboid"
    assert g["definitions"] == {"Car": "Xe con chở người", "Truck": "Xe tải"} and "Pickup" in g["markdown"]
    text, info = guide_lib._guide_for_3d(profile, LABELS, "related")
    assert "Pickup có thùng hở" in text and info["title"] == "Guideline 3D Cuboid"
    assert guide_lib._guide_summary(profile, LABELS)["definitions"] == 2


def test_no_guideline_for_job(guide_lib):
    assert guide_lib._guideline_for({"id": "generic"}) is None
    assert guide_lib._guideline_for({"id": "x", "guideline_id": "../../etc/passwd"}) is None, "không đọc ngoài thư mục"
    text, info = guide_lib._guide_for_3d({"id": "generic"}, LABELS, "related")
    assert text == "" and info["missing"] and info["id"] is None
    assert guide_lib._guide_summary({"id": "generic"}, LABELS) is None


def test_panel_has_write_mode_and_guideline_line():
    js = (ROOT / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
    for s in ("function applyLidarShapes", "function tracksToShapes", "Shape từng frame", "Ghi cả frame nội suy (bị che)",
              "per_frame: true", "function guideLine3d", "guide_mode: guideMode3d()", "Chưa có guideline: nạp ở tab Guideline",
              "Dựng cuboid: model 3D lỗi — đang dùng hình học"):
        assert s in js, s
