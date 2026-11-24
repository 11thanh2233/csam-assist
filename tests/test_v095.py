"""Kiểm thử bản 0.9.5: Claude xem ảnh, trí nhớ của project, occluded theo chính vật, cách dựng hình theo guideline,
tag theo tên file, theo dõi nhanh hơn."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from conftest import H, W, ScriptedEngine, rect

ROOT = Path(__file__).resolve().parent.parent


def _attr(name, values, kind="select"):
    return {"id": abs(hash(name)) % 10000, "name": name, "input_type": kind, "values": values, "default_value": values[0], "mutable": False}


# ------------------------------------------------------------- trí nhớ ---
def test_memory_learns_filename_tokens_and_sequences(tmp_path):
    from backend.memory import ProjectMemory, name_tokens, sequence_key

    assert name_tokens("B05_05780_MVL.png") == ["b05", "mvl"]
    assert sequence_key("B05_05780_MVL.png") == "b05_#_mvl"
    m = ProjectMemory(tmp_path)
    for f, v in [("B05_00001_MVL.png", "fisheye_left"), ("B07_01216_MVR.png", "fisheye_right"),
                 ("B10_01153_MVL.png", "fisheye_left"), ("B09_02686_FV.png", "fisheye_front"), ("B12_05885_MVR.png", "fisheye_right")]:
        m.record("p", [{"label": "camera", "kind": "tag", "final": {"value": v}, "tool": {"value": "front"}, "filename": f}])
    assert m.token_guess("p", "camera", "value", "B99_00042_MVL.png") == ("fisheye_left", "mvl")
    assert m.token_guess("p", "camera", "value", "B98_00001_MVR.png")[0] == "fisheye_right"
    assert m.token_guess("p", "camera", "value", "X01_0001_FV.png") is None, "FV mới gặp 1 lần: chưa đủ để học"
    # chuỗi ảnh: frame sau của cùng chuỗi
    assert m.token_guess("p", "camera", "value", "B09_02687_FV.png") == ("fisheye_front", "sequence")
    s = m.summary("p")
    assert s["corrections"] == 5 and any(r["token"] == "mvl" and r["value"] == "fisheye_left" for r in s["file_rules"])
    ex = m.examples("p", ["camera"])
    assert ex and ex[-1]["tool"] == "front"


def test_memory_corrections_lessons_and_distill(tmp_path):
    from backend.memory import ProjectMemory

    m = ProjectMemory(tmp_path)
    n = m.record("p", [
        {"label": "hand", "kind": "object", "final": {"side": "right", "whose": "driver"}, "tool": {"side": "left", "whose": "driver"},
         "occluded": False, "tool_occluded": True, "where": "bottom-right of image"},
        {"label": "person", "rejected": True, "reason": "người trong ảnh chụp"},
    ])
    assert n == 3, "side đổi + occluded đổi + đề xuất bị bỏ"
    assert m.prior("p", "hand", "whose") is None, "dưới 3 lần: chưa đủ thống kê"
    lessons = m.add_lessons("p", ["Tay cầm vô lăng thì on_wheel = true"], source="you")
    assert lessons[0]["from"] == "you"
    assert m.delete_lesson("p", lessons[0]["id"]) == []

    class FakeLLM:
        def complete(self, system, text, images=None, max_tokens=0):
            assert "hand.side: tool 'left' -> annotator 'right'" in text
            return '{"lessons": ["hand: side theo tay của chính người, không theo phía ảnh"]}'

    got = m.distill("p", FakeLLM())
    assert got[0]["text"].startswith("hand: side") and m.summary("p")["since_lesson"] == 0


# --------------------------------------------------------- Claude xem ảnh ---
def test_part_groups_by_point_name():
    from backend.judge import part_groups, part_of

    assert part_of("left_eye_outer") == "left_eye" and part_of("right_eye_lower") == "right_eye"
    assert part_of("lip_upper") == "mouth" and part_of("mouth_left") == "mouth" and part_of("nose_tip") == "nose"
    assert part_of("left_wrist") == "left_wrist"
    els = [{"name": n} for n in ["left_eye_outer", "left_eye_inner", "nose_tip", "lip_upper", "mouth_left"]]
    assert part_groups(els) == {"left_eye": [0, 1], "nose": [2], "mouth": [3, 4]}


def _face_proposal():
    names = ["left_eye_outer", "left_eye_inner", "right_eye_outer", "right_eye_inner", "nose_tip", "lip_upper", "lip_lower"]
    els = [{"label_id": 100 + i, "name": n, "x": 400 + 20 * i, "y": 300 + 10 * i, "occluded": False, "outside": True if i < 4 else False}
           for i, n in enumerate(names)]
    return {"label_id": 3, "label_name": "face13", "type": "skeleton", "bbox": [380, 290, 560, 380], "elements": els,
            "attributes": {}, "flags": [], "checked": True, "occluded": False}


def _cfgs():
    return [
        {"label_id": 2, "name": "face", "shape": "rectangle", "enabled": True, "supported": True, "cvat_type": "rectangle",
         "label_attributes": [{"name": "eye_left", "values": ["open", "closed", "unknown"]}, {"name": "glasses", "values": ["none", "glasses", "sunglasses"]}]},
        {"label_id": 3, "name": "face13", "shape": "skeleton", "enabled": True, "supported": True, "cvat_type": "skeleton", "label_attributes": []},
        {"label_id": 4, "name": "hand", "shape": "rectangle", "enabled": True, "supported": True, "cvat_type": "rectangle", "prompts": ["hand"],
         "label_attributes": [{"name": "side", "values": ["left", "right"]}]},
        {"label_id": 6, "name": "camera", "shape": "tag", "enabled": True, "supported": True, "cvat_type": "tag",
         "label_attributes": [{"name": "value", "values": ["front", "fisheye_left", "fisheye_right"]}]},
    ]


def test_judge_request_and_apply():
    from backend import judge as J

    image = np.full((H, W, 3), 120, np.uint8)
    face = {"label_id": 2, "label_name": "face", "type": "rectangle", "bbox": [380, 280, 580, 400], "attributes": {"eye left": "open"},
            "flags": [], "checked": True, "occluded": True}
    poster = {"label_id": 4, "label_name": "hand", "type": "rectangle", "bbox": [10, 10, 60, 60], "attributes": {}, "flags": [], "checked": True, "occluded": False}
    props = [face, _face_proposal(), poster]
    tags = [{"label_id": 6, "label_name": "camera", "attributes": {"value": "front"}, "auto": ["value"], "review": [], "notes": {}}]
    profile = {"labels": [{"name": "face", "definition": "khuôn mặt người lái"}], "exclude": ["người trong poster"],
               "guide_notes": "Bộ phận bị che vẫn đặt occluded nếu ước lượng được."}
    memory = {"lessons": [{"text": "kính râm thì eye_left = unknown"}],
              "examples": [{"label": "face", "field": "glasses", "tool": "none", "you": "sunglasses", "where": "middle-center of image"}]}
    text, images, items = J.build_request(image, props, _cfgs(), profile, tags, [_cfgs()[3]], memory, "B05_00001_MVL.png", {"fisheye": True})
    assert len(images) >= 2, "ảnh đánh số + ảnh phóng to cho vật nhỏ"
    for want in ("eye_left ∈ {open, closed, unknown}", "khuôn mặt người lái", "người trong poster", "kính râm thì eye_left",
                 "glasses: tool 'none' -> annotator chose 'sunglasses'", "left_eye[left_eye_outer, left_eye_inner]=outside",
                 "B05_00001_MVL.png", "camera: value"):
        assert want in text, want
    answer = {
        "objects": [
            {"id": 1, "keep": True, "attributes": {"eye_left": "unknown", "glasses": "sunglasses", "bogus": "x"},
             "confidence": {"eye_left": 0.4}, "state": "visible"},
            {"id": 2, "parts": {"left_eye": "occluded", "right_eye": "occluded", "mouth": "visible"}},
            {"id": 3, "keep": False, "why": "tay trong poster"},
        ],
        "tags": {"camera": {"value": "fisheye_left"}},
        "missing": [{"label": "hand", "box": [100, 500, 200, 700], "why": "tay trên vô lăng"}, {"label": "nope", "box": [0, 0, 1, 1]},
                    {"label": "face13", "box": [0, 0, 500, 500]}],
    }
    done = J.apply(answer, props, items, _cfgs(), tags, [_cfgs()[3]], W, H)
    assert face["attributes"] == {"eye left": "unknown", "glasses": "sunglasses"}, "giá trị lạ (bogus) bị bỏ"
    assert face["review_attributes"] == ["eye_left"], "Claude không chắc: nhắc bạn kiểm"
    assert face["occluded"] is False, "occluded theo chính vật: Claude thấy đủ thì về visible"
    sk = props[1]["elements"]
    assert all(e["occluded"] and not e["outside"] for e in sk[:4]), "mắt bị che nhưng ước lượng được: occluded, không phải outside"
    assert not sk[5]["occluded"] and not sk[5]["outside"], "miệng không bị che: giữ visible"
    assert poster["checked"] is False and any(f["code"] == "LLM_DROP" for f in poster["flags"])
    assert tags[0]["attributes"]["value"] == "fisheye_left"
    assert len(done["missing"]) == 1 and done["missing"][0]["label_id"] == 4, "label lạ / skeleton không nhận"
    assert np.allclose(done["missing"][0]["box"], [128, 360, 256, 504]), "toạ độ 0-1000 đổi ra pixel"


def test_judge_keeps_learned_tag_and_usage_limit(tmp_path):
    from backend import judge as J
    from backend.guideline.llm import LLMError

    tags = [{"label_id": 6, "label_name": "camera", "attributes": {"value": "fisheye_left"}, "learned": {"value": "mvl"}, "auto": ["value"]}]
    J.apply({"tags": {"camera": {"value": "front"}}}, [], [], _cfgs(), tags, [_cfgs()[3]], W, H)
    assert tags[0]["attributes"]["value"] == "fisheye_left", "giá trị học từ cách bạn chốt thắng câu trả lời của Claude"

    class LLM:
        sees_images = True
        last_usage = {"input_tokens": 1000, "output_tokens": 50}
        calls = 0

        def complete(self, system, text, images=None, max_tokens=0):
            LLM.calls += 1
            return '```json\n{"objects": []}\n```'

    usage = J.Usage(tmp_path / "u.json", 1)
    j = J.Judge(LLM(), usage)
    assert j.ask("a", [b"x"]) == {"objects": []}
    assert j.ask("a", [b"x"]) == {"objects": []} and LLM.calls == 1, "cùng ảnh, cùng đề xuất: dùng lại kết quả"
    with pytest.raises(LLMError):
        j.ask("b", [b"y"])
    assert usage.status()["calls"] == 1 and usage.status()["input_tokens"] == 1000


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def _img_b64(image=None):
    import base64

    import cv2

    image = image if image is not None else np.full((720, 1280, 3), 90, np.uint8)
    ok, buf = cv2.imencode(".jpg", image)
    return base64.b64encode(buf.tobytes()).decode()


def _labels():
    return [{"id": 4, "name": "hand", "type": "rectangle", "attributes": [_attr("side", ["left", "right"])]},
            {"id": 6, "name": "camera", "type": "tag", "attributes": [_attr("value", ["front", "fisheye_left", "fisheye_right"])]}]


def test_propose_with_claude_and_memory_endpoints(client, monkeypatch):
    from backend import app as A
    from backend.judge import Judge, Usage

    seen = {}

    class LLM:
        sees_images = True
        provider, model = "anthropic", "claude"
        last_usage = None

        def complete(self, system, text, images=None, max_tokens=0):
            seen["text"], seen["images"] = text, len(images or [])
            return json.dumps({"objects": [], "tags": {"camera": {"value": "fisheye_right"}},
                               "missing": [{"label": "hand", "box": [100, 100, 300, 400]}]})

        def describe(self):
            return {"provider": "anthropic", "model": "claude"}

    monkeypatch.setattr(A.rt, "judge", Judge(LLM(), Usage(Path(A.settings.library_dir) / "memory" / "_u.json", 0)))
    profile = {"id": "proj_t77", "mode": "mixed", "labels": [{"name": "hand", "shape": "rectangle", "prompts": ["hand"]},
                                                              {"name": "camera", "shape": "tag"}], "rules": {}, "defaults": {}}
    body = {"labels": _labels(), "profile_inline": profile, "image_b64": _img_b64(), "use_llm": True, "filename": "B07_01216_MVR.png"}
    out = client.post("/propose", json=body).json()
    assert out["llm"]["used"] and seen["images"] >= 1 and "B07_01216_MVR.png" in seen["text"]
    assert out["tags"][0]["attributes"]["value"] == "fisheye_right"
    assert out["llm"]["added"] >= 0
    # trí nhớ: ghi 2 lần MVR -> lần sau tool tự biết, kể cả khi không có Claude
    for f in ("B07_01216_MVR.png", "B12_05885_MVR.png"):
        r = client.post("/memory/record", json={"profile_id": "preview" if False else "proj_t77",
                                                "items": [{"label": "camera", "kind": "tag", "final": {"value": "fisheye_right"},
                                                           "tool": {"value": "front"}, "filename": f}]}).json()
    assert r["summary"]["corrections"] == 2
    out = client.post("/propose", json=dict(body, use_llm=False, filename="B20_00001_MVR.png")).json()
    tag = out["tags"][0]
    assert tag["attributes"]["value"] == "fisheye_right" and tag["learned"] == {"value": "mvr"}
    got = client.get("/memory/proj_t77").json()
    assert got["llm"]["available"] and got["summary"]["file_rules"][0]["token"] == "mvr"
    added = client.post("/memory/proj_t77/lessons", json={"text": "luôn đặt side theo tay của người"}).json()["lessons"]
    assert client.delete(f"/memory/proj_t77/lessons/{added[0]['id']}").json()["lessons"] == []
    # không có Claude: báo rõ, vẫn trả kết quả
    monkeypatch.setattr(A.rt, "judge", Judge(None, Usage(Path(A.settings.library_dir) / "memory" / "_u2.json", 0)))
    out = client.post("/propose", json=body).json()
    assert any("Chưa bật Claude" in w for w in out["warnings"])


# ------------------------------------------------ occluded theo chính vật ---
def test_overlap_no_longer_sets_occluded():
    from backend import pipeline as P

    cfg = {"name": "person", "label_id": 1}
    back = P.Det(cfg, rect(100, 100, 300, 500), 0.9, "person", [100, 100, 300, 500])
    front = P.Det(dict(cfg, name="car"), rect(200, 300, 600, 600), 0.9, "car", [200, 300, 600, 600])
    P.mark_occluded([back, front], {"occluded": {"enabled": True, "min_overlap": 0.15}})
    assert back.occluded is False and back.flags, "chỉ gắn cờ để xem, không tự đặt occluded"
    back2 = P.Det(cfg, rect(100, 100, 300, 500), 0.9, "person", [100, 100, 300, 500])
    P.mark_occluded([back2, front], {"occluded": {"enabled": True, "set": True}})
    assert back2.occluded, "profile muốn giữ cách cũ thì ghi set: true"


def test_face_skeleton_keeps_uncovered_parts():
    """Hình 17, 18: kính râm che mắt, mũi và môi vẫn giữ (trước đây Outside cả skeleton)."""
    from backend import keypoints as K

    kps = [{"label_id": 100 + i, "name": n, "index": i} for i, n in enumerate(
        ["left_eye_outer", "left_eye_inner", "left_eye_upper", "left_eye_lower", "right_eye_outer", "right_eye_inner",
         "right_eye_upper", "right_eye_lower", "nose_tip", "mouth_left", "mouth_right", "lip_upper", "lip_lower"])]
    landmarks = np.array([[400 + 10 * i, 300 + (40 if i >= 8 else 0) + 5 * (i % 3)] for i in range(13)], float)
    glasses = np.zeros((H, W), bool)
    glasses[280:330, 380:500] = True
    prop = K.face_skeleton({"keypoints": kps, "label_id": 3, "name": "face13", "edges": []}, landmarks, W, H, (None, False),
                           [("sunglasses", glasses)])
    els = prop["elements"]
    assert sum(e["occluded"] for e in els[:8]) == 8 and not any(e["outside"] for e in els)
    assert not any(e["occluded"] or e["outside"] for e in els[8:]), "mũi, miệng không bị che: visible"
    # guideline ghi ngưỡng cho chính skeleton này: luật Outside cả skeleton vẫn áp
    prop = K.face_skeleton({"keypoints": kps, "label_id": 3, "name": "face13", "edges": [], "min_visible": 7}, landmarks, W, H,
                           (None, False), [("sunglasses", glasses)])
    assert all(e["outside"] for e in prop["elements"])


def test_body_filter_low_visibility_and_picture():
    from backend import keypoints as K

    class Body:
        def estimate(self, image, boxes):
            out = []
            for b in boxes:
                coords = np.array([[b[0] + 5 * i, b[1] + 10 * i] for i in range(17)], float)
                scores = np.full(17, 0.9) if b[0] < 600 else np.array([0.9] * 2 + [0.1] * 15)
                out.append((coords, scores))
            return out

    engine = ScriptedEngine({"person": [(rect(100, 100, 300, 600), 0.9), (rect(700, 100, 800, 300), 0.8), (rect(1000, 150, 1100, 350), 0.85)],
                             "picture frame": [(rect(980, 120, 1150, 400), 0.9)]})
    kps = [{"label_id": 200 + i, "name": n, "index": i} for i, n in enumerate(K.SOURCE_OF_INDEX)]
    cfg = {"label_id": 9, "name": "person", "prompts": ["person"], "threshold": 0.5, "keypoints": kps, "edges": [], "shape": "skeleton",
           "supported": True, "enabled": True, "attributes": [], "role": "body", "min_area_px": 30}
    props = K.body_proposals(engine, {}, np.zeros((H, W, 3), np.uint8), cfg, {"pose": {"lr_convention": "anatomical"}}, Body(), [])
    by_x = sorted(props, key=lambda p: p["bbox"][0])
    assert by_x[0]["checked"]
    assert not by_x[1]["checked"] and any(f["code"] == "LOW_VISIBILITY" for f in by_x[1]["flags"]), "chỉ thấy 2/17 điểm"
    assert not by_x[2]["checked"] and any("khung ảnh" in f["message"] for f in by_x[2]["flags"]), "người trong khung ảnh"


# ------------------------------------------------ cách dựng hình theo guideline ---
def test_strategy_from_guideline_definitions():
    from backend.autoprofile import shape_strategy

    assert shape_strategy("crosswalk", "polygon", "Cả vùng vạch ngựa vạn, một polygon bao các vạch") == "region"
    assert shape_strategy("zone_x", "polygon", "a polygon enclosing all stripes") == "region"
    assert shape_strategy("drivable", "polygon", "Phần mặt đường xe đi được") == "ground"
    assert shape_strategy("road_curb", "polyline", "Mép đường là vỉa hè hoặc lề") == "edge"
    assert shape_strategy("lane", "polyline", "vạch kẻ làn") is None
    assert shape_strategy("car", "rectangle", "") is None


def _road_cfgs():
    from backend.profiles import resolve_labels

    profile = {"id": "road", "mode": "mixed", "rules": {}, "defaults": {}, "labels": [
        {"name": "crosswalk", "shape": "polygon", "prompts": ["crosswalk"], "strategy": "region"},
        {"name": "lane", "shape": "polyline", "prompts": ["lane marking"], "polyline_mode": "centerline"},
        {"name": "drivable", "shape": "polygon", "prompts": ["road"], "strategy": "ground"},
        {"name": "road_curb", "shape": "polyline", "prompts": ["curb"], "strategy": "edge"},
    ]}
    from backend.profiles import ProfileStore

    from backend.profiles import norm

    profile["_index"] = {norm(e["name"]): e for e in profile["labels"]}
    labels = [{"id": 1, "name": "crosswalk", "type": "polygon"}, {"id": 2, "name": "lane", "type": "polyline"},
              {"id": 3, "name": "drivable", "type": "polygon", "attributes": [_attr("area_type", ["direct", "alternative"])]},
              {"id": 4, "name": "road_curb", "type": "polyline"}]
    return profile, resolve_labels(profile, labels, {})


def test_crosswalk_region_lane_exclusion_ground_and_edge():
    """Hình 12, 13: crosswalk là MỘT polygon bao các sọc; sọc không thành lane; drivable tự ra; mép đường thành road_curb."""
    from backend import pipeline as P

    profile, cfgs = _road_cfgs()
    stripes = [(rect(400 + 60 * i, 400, 440 + 60 * i, 520), 0.8) for i in range(5)]
    road = np.zeros((H, W), bool)
    road[300:720, 300:1000] = True
    lane = rect(900, 300, 910, 720)
    script = {"crosswalk": stripes, "lane marking": stripes[:2] + [(lane, 0.8)], "road": [(road, 0.9)], "curb": []}
    out = P.propose(ScriptedEngine(script), {}, np.zeros((H, W, 3), np.uint8), profile, cfgs, [])
    props = out["proposals"]
    cw = [p for p in props if p["label_name"] == "crosswalk"]
    assert len(cw) == 1, [p["label_name"] for p in props]
    xs = cw[0]["points"][0::2]
    assert min(xs) <= 402 and max(xs) >= 678, "một polygon bao hết các sọc"
    lanes = [p for p in props if p["label_name"] == "lane"]
    assert len(lanes) == 1 and min(lanes[0]["points"][0::2]) >= 890, "sọc crosswalk không bị nhận là lane"
    drv = [p for p in props if p["label_name"] == "drivable"]
    assert drv and drv[0]["attributes"].get("area type") == "direct", out["warnings"]
    curbs = [p for p in props if p["label_name"] == "road_curb"]
    assert curbs and all(any(f["code"] == "UNCERTAIN_BOUNDARY" for f in c["flags"]) for c in curbs), "mép vùng đường làm road_curb, có cờ"


# ------------------------------------------------------------------ tag ---
def test_fisheye_like_two_dark_corners():
    from backend import tags as T

    img = np.full((480, 640, 3), 140, np.uint8)
    yy, xx = np.mgrid[0:480, 0:640]
    outside = (xx - 320) ** 2 / 340 ** 2 + (yy - 300) ** 2 / 330 ** 2 > 1
    img[outside] = 5
    img[400:, :] = 150      # thân xe sáng ở hai góc dưới (hình 14)
    assert T.fisheye_circle(img) is None, "luật cũ (đòi 4 góc tối) không nhận ra"
    assert T.fisheye_like(img)
    cfg = {"label_id": 1, "name": "camera", "label_attributes": [{"name": "value", "values": ["front", "fisheye_left"]}]}
    got = T.suggest(img, [cfg])[0]
    assert "value" in got["review"], "fisheye mà không biết hướng: để bạn chọn, không ghi 'front'"
    got = T.suggest(img, [cfg], guess=lambda label, attr: ("fisheye_left", "mvl"))[0]
    assert got["attributes"]["value"] == "fisheye_left" and "mvl" in got["notes"]["value"]
    assert T.suggest(np.full((480, 640, 3), 140, np.uint8), [cfg])[0]["attributes"]["value"] == "front"


# ---------------------------------------------------------------- tracking ---
def test_smart_tracking_scheduler():
    node = shutil.which("node")
    if not node:
        pytest.skip("cần node")
    js = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('  function boxIou(a, b) {');
const end = src.indexOf('  function trackColor(tid) {');
const S = { stopRequested: false };
const mod = new Function('S', src.slice(start, end) + '\nreturn { smartTrackDetect, matchDets };')(S);
(async () => {
  const frames = Array.from({ length: 150 }, (_, i) => i);
  const truth = (f) => {
    const d = [{ label_id: 1, box: [100 + 2 * f, 100, 160 + 2 * f, 260], score: 0.9 }];
    if (f >= 60 && f < 70) d.push({ label_id: 1, box: [500, 100, 560, 260], score: 0.8 });
    if (f >= 100) d[0].box = [400 + 15 * Math.sin(f), 100, 460 + 15 * Math.sin(f), 260];
    return d;
  };
  const smooth = await mod.smartTrackDetect(frames.slice(0, 60), async (l) => l.map((f) => ({ frame: f, dets: truth(f) })), () => {});
  const r = await mod.smartTrackDetect(frames, async (l) => l.map((f) => ({ frame: f, dets: truth(f) })), () => {});
  let worst = 1;
  for (const d of r.detected) {
    const m = mod.matchDets(d.dets, truth(d.frame), 0);
    if (!m) { worst = 0; continue; }
    for (const [a, b] of m) worst = Math.min(worst, (Math.min(a.box[2], b.box[2]) - Math.max(a.box[0], b.box[0])) / (a.box[2] - a.box[0]));
  }
  console.log(JSON.stringify({ smoothRan: smooth.ran, ran: r.ran, covered: r.detected.length, worst }));
})();
"""
    out = subprocess.run([node, "-e", js, str(ROOT / "userscript" / "sam3-cvat-assist.user.js")], capture_output=True, text=True, timeout=60)
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["covered"] == 150, "frame nào cũng có kết quả"
    assert res["smoothRan"] <= 30, f"đoạn chuyển động đều: chạy SAM3 ít hơn một nửa số frame ({res['smoothRan']}/60)"
    assert res["worst"] >= 0.85, "chỗ nội suy vẫn sát kết quả SAM3"
    assert res["ran"] < 150


def test_sam3_half_precision_choice():
    from backend.engines.sam3_hf import Sam3TransformersEngine

    class Cuda:
        @staticmethod
        def is_bf16_supported():
            return False

    class Torch:
        float32, float16, bfloat16 = "f32", "f16", "bf16"
        cuda = Cuda

    e = Sam3TransformersEngine("cuda")
    e.device = "cuda"
    assert e._pick_dtype(Torch) == "f16", "card không có bf16: fp16"
    Cuda.is_bf16_supported = staticmethod(lambda: True)
    assert e._pick_dtype(Torch) == "bf16"
    e.device = "cpu"
    assert e._pick_dtype(Torch) == "f32", "CPU luôn fp32"
    e.device, e._requested_dtype = "cuda", "float32"
    assert e._pick_dtype(Torch) == "f32", "SAM3_DTYPE=float32 để về như cũ"


def test_guide_notes_keep_visibility_rules():
    from backend.judge import guide_notes

    md = "# Road\n\nGiới thiệu chung.\n\nVật bị che một phần vẫn gán, đặt occluded.\n\nKhông gán người trong poster hoặc gương.\n\nMàu sắc của biển."
    notes = guide_notes(md)
    assert "occluded" in notes and "poster" in notes and "Giới thiệu" not in notes


def test_app_llm_daily_limit(tmp_path, monkeypatch):
    import test_v093 as T

    store = {}
    from desktop import credstore

    monkeypatch.setattr(credstore, "available", lambda: True)
    monkeypatch.setattr(credstore, "get", lambda user=None: store.get(user))
    monkeypatch.setattr(credstore, "set", lambda value, user=None: store.__setitem__(user, value))
    monkeypatch.setattr(credstore, "delete", lambda user=None: store.pop(user, None))
    be = T._Be()
    api = T._api(tmp_path, be)
    assert api.set_llm("anthropic", "", "", "sk-ant-x")["ok"]
    assert be.extra_env["SAM3_LLM_DAILY_LIMIT"] == "500" and api.llm_settings()["daily_limit"] == 500
    assert api.set_llm("anthropic", "", "", None, "120")["ok"] and be.extra_env["SAM3_LLM_DAILY_LIMIT"] == "120"
    assert not api.set_llm("anthropic", "", "", None, "nhiều")["ok"]
    assert api.set_llm("anthropic", "", "", None)["ok"] and be.extra_env["SAM3_LLM_DAILY_LIMIT"] == "120", "để trống: giữ giới hạn cũ"
    assert api.set_llm("none")["ok"] and "SAM3_LLM_DAILY_LIMIT" not in be.extra_env
