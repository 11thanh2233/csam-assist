"""Kiểm thử bản 0.9.4."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ chép ---
def test_copy_falls_back_to_clip_exe(monkeypatch):
    from desktop import control as C

    monkeypatch.setattr(C, "_win_set_clipboard", lambda ctypes, text: False)
    got = {}

    def fake_clip(text, run=None):
        got["text"] = text
        return True

    monkeypatch.setattr(C, "_clip_exe", fake_clip)
    assert C.win_copy("Nhật ký ✓") and got["text"] == "Nhật ký ✓"

    def boom(ctypes, text):
        raise OSError("access denied")

    monkeypatch.setattr(C, "_win_set_clipboard", boom)
    got.clear()
    assert C.win_copy("x") and got["text"] == "x", "API Windows lỗi hẳn: vẫn chép được qua clip.exe"


def test_clip_exe_sends_utf16_with_bom():
    from desktop import control as C

    seen = {}

    class R:
        returncode = 0

    def run(args, **kw):
        seen.update(args=args, **kw)
        return R()

    assert C._clip_exe("Lỗi", run=run)
    assert seen["args"][0].lower().endswith("clip.exe")
    assert seen["input"] == b"\xff\xfe" + "Lỗi".encode("utf-16-le")


def test_control_copies_inside_the_click_first():
    html = (ROOT / "desktop" / "ui" / "control.html").read_text(encoding="utf-8")
    body = html[html.index("async function copyText"):]
    body = body[:body.index("\n}\n")]
    assert body.index("copySync(text)") < body.index("api().copy_text"), "chép ngay trong thao tác bấm, trước mọi lệnh chờ"
    assert "document.execCommand('copy')" in html


# ---------------------------------------------------- profile của project ---
ATT = "/mnt/user-data/attachments/5ab6319d-fe04-572e-8b80-28851b5cf6be/"
GUIDES = ROOT / "tests" / "data" / "guidelines"
F13 = ["left_eye_outer", "left_eye_inner", "left_eye_upper", "left_eye_lower", "right_eye_outer", "right_eye_inner",
       "right_eye_upper", "right_eye_lower", "nose_tip", "mouth_left", "mouth_right", "lip_upper", "lip_lower"]
COCO = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
        "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle"]


def _attr(name, values, kind="select", aid=None):
    return {"id": aid or abs(hash(name)) % 100000, "name": name, "values": values, "input_type": kind, "default_value": values[0]}


def cabin_labels():
    return [
        {"id": 1, "name": "person", "type": "rectangle", "attributes": [_attr("seat", ["driver", "front_passenger"]),
                                                                          _attr("seatbelt", ["worn_correct", "not_worn", "unknown"])]},
        {"id": 2, "name": "face", "type": "rectangle", "attributes": [_attr("eye_left", ["open", "closed", "unknown"])]},
        {"id": 3, "name": "face13", "type": "skeleton", "sublabels": [{"id": 100 + i, "name": n} for i, n in enumerate(F13)]},
        {"id": 4, "name": "hand", "type": "rectangle"},
        {"id": 6, "name": "frame_meta", "type": "tag", "attributes": [_attr("modality", ["rgb", "ir"]),
                                                                       _attr("light", ["day", "night", "unknown"]),
                                                                       _attr("camera_blocked", ["yes", "no"])]},
    ]


def road_labels():
    return [
        {"id": 11, "name": "lane", "type": "polyline", "attributes": [_attr("position", ["L1", "R1", "L2", "R2", "other"])]},
        {"id": 12, "name": "drivable", "type": "polygon", "attributes": [_attr("area_type", ["direct", "alternative"])]},
        {"id": 13, "name": "traffic_light", "type": "rectangle", "attributes": [_attr("color", ["red", "yellow", "green", "off", "unknown"]),
                                                                                _attr("edge_zone", ["false", "true"], "checkbox")]},
        {"id": 14, "name": "camera", "type": "tag", "attributes": [_attr("value", ["front", "fisheye_front", "fisheye_left"])]},
        {"id": 15, "name": "weather", "type": "tag", "attributes": [_attr("value", ["clear", "overcast", "rain"])]},
    ]


@pytest.fixture(scope="module")
def store():
    from backend.config import settings
    from backend.profiles import ProfileStore

    return ProfileStore(settings.profiles_dir)


def test_point_names_map_to_model_points():
    from backend import autoprofile as A

    assert A.point_concept("left_eye_outer", "face") == "left_eye_outer"
    assert A.point_concept("Khoé ngoài mắt trái", "face") == "left_eye_outer"
    assert A.point_concept("R-eye-in", "face") == "right_eye_inner"
    assert A.point_concept("mouth_left", "face") == "mouth_left"
    assert A.point_concept("lip_lower", "face") == "lip_lower"
    assert A.point_concept("longmaytrai", "face") == "left_brow_mid"
    assert A.point_concept("nose_tip", "face") == "nose_tip"
    assert A.point_concept("left_knee", "body") == "left_knee"
    assert A.point_concept("goi trai", "body") == "left_knee"
    assert A.point_concept("kp_7", "body") is None
    assert A.skeleton_kind(F13) == "face" and A.skeleton_kind(COCO) == "body"


def test_face13_skeleton_maps_every_point_and_flips_for_image_convention():
    from backend import autoprofile as A

    label = {"name": "face13", "sublabels": [{"id": i, "name": n} for i, n in enumerate(F13)]}
    m = A.map_skeleton(label)
    assert m["kind"] == "face" and m["mapped"] == 13
    assert m["keypoints"][0]["path"] == A.FACE["left_eye_outer"]
    flipped = A.map_skeleton(label, lr="image")
    assert flipped["keypoints"][0]["path"] == A.FACE["right_eye_outer"], "theo ảnh: 'trái' là bên trái ảnh = mắt phải của người"


def test_cabin_job_mixing_boxes_skeleton_and_tag_gets_one_profile(store):
    from backend import autoprofile as A
    from backend.profiles import resolve_labels

    md = (ATT + "503dcbb6-GUIDE.md")
    md = Path(md).read_text(encoding="utf-8") if Path(md).exists() else "Trái/phải theo người trong ảnh. `face13` (nếu mặt rộng ít nhất 60 px)"
    built = A.build(cabin_labels(), store.profiles, project={"project_id": 7, "name": "Cabin"}, markdown=md)
    p = built["profile"]
    assert p["id"] == "p7" or p["id"] == "proj_p7"
    assert p["mode"] == "mixed" and p["cvat_project"] == 7 and built["lr"] == "anatomical"
    rows = {r["label"]: r for r in built["rows"]}
    assert all(r["status"] == "ok" for r in rows.values()), rows
    assert "13/13" in rows["face13"]["how"] and "3/3" in rows["frame_meta"]["how"]
    assert p["rules"]["face"]["min_face_px"] == 60, "guideline: mặt dưới 60 px không vẽ face13"
    store.profiles[p["id"]] = __import__("backend.app", fromlist=["inline_profile"]).inline_profile(p)
    cfgs = {c["name"]: c for c in resolve_labels(store.profiles[p["id"]], cabin_labels())}
    assert cfgs["face13"]["shape"] == "skeleton" and cfgs["face13"]["role"] == "face" and cfgs["face13"]["enabled"]
    assert cfgs["person"]["shape"] == "rectangle" and cfgs["person"]["enabled"], "box và skeleton cùng một job"
    assert cfgs["frame_meta"]["shape"] == "tag" and cfgs["frame_meta"]["tag_rules"]["modality"] == "modality"
    assert cfgs["person"]["attr_rules"]["seatbelt"] == "unknown_default"
    assert all(k["path"] for k in cfgs["face13"]["keypoints"])
    track = {c["name"]: c for c in resolve_labels(store.profiles[p["id"]], cabin_labels(), purpose="track")}
    assert track["person"]["supported"] and not track["face13"]["supported"] and not track["frame_meta"]["supported"]
    del store.profiles[p["id"]]


def test_road_attributes_and_tags(store):
    from backend import autoprofile as A

    md_path = Path(ATT + "41a2ff14-guideline_road_v1.0.md")
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else "Gán mọi cụm đèn và tấm biển cao từ 8 px trở lên. traffic_light"
    built = A.build(road_labels(), store.profiles, project={"task_id": 3}, markdown=md,
                    llm_data={"labels": [{"name": "traffic_light", "min_px": 8}]})
    p = built["profile"]
    assert p["id"] == "proj_t3" and p["cvat_task"] == 3
    light = next(e for e in p["labels"] if e["name"] == "traffic_light")
    assert light["attr_rules"] == {"color": "light_color", "edge_zone": "fisheye_edge"}
    assert light["min_side_px"] == 8
    tags = {e["name"]: e for e in p["labels"] if e["shape"] == "tag"}
    assert tags["camera"]["tag_rules"]["value"] == "camera" and tags["weather"]["tag_rules"]["value"] == "manual"
    lane = next(e for e in p["labels"] if e["name"] == "lane")
    assert lane["shape"] == "polyline"


def test_known_profile_convention_follows_the_new_guideline(store):
    from backend import autoprofile as A

    labels = [{"id": 8, "name": "person", "type": "skeleton", "sublabels": [{"id": 200 + i, "name": n} for i, n in enumerate(COCO)]}]
    built = A.build(labels, store.profiles, markdown="Trái/phải theo người trong ảnh.")
    assert built["profile"]["rules"]["pose"]["lr_convention"] == "anatomical"
    assert any("theo ảnh" in w for w in built["warnings"])
    unknown = A.build(labels, store.profiles, markdown="")
    assert unknown["profile"]["rules"]["pose"]["lr_convention"] == "image", "không nói rõ: giữ quy ước của profile có sẵn, có cảnh báo"


def test_tag_rules_from_the_image():
    import numpy as np

    from backend import tags as T

    gray = np.full((240, 320, 3), 40, np.uint8)
    cfg = {"label_id": 6, "name": "frame_meta", "tag_rules": {"modality": "modality", "light": "timeofday", "camera_blocked": "blocked"},
           "label_attributes": [{"name": "modality", "values": ["rgb", "ir"]}, {"name": "light", "values": ["day", "night", "unknown"]},
                                {"name": "camera_blocked", "values": ["yes", "no"]}]}
    out = T.suggest(gray, [cfg])[0]
    assert out["attributes"]["modality"] == "ir" and out["attributes"]["camera blocked"] == "yes"
    assert "light" in out["review"], "ảnh hồng ngoại: không đoán ngày/đêm theo độ sáng"
    color = np.random.default_rng(1).integers(0, 255, (240, 320, 3)).astype(np.uint8)
    out = T.suggest(color, [cfg])[0]
    assert out["attributes"]["modality"] == "rgb" and out["attributes"]["camera blocked"] == "no" and out["attributes"]["light"] == "day"
    fish = np.zeros((240, 320, 3), np.uint8)
    import cv2

    cv2.circle(fish, (160, 120), 115, (120, 90, 60), -1)
    cam = {"label_id": 14, "name": "camera", "tag_rules": {"value": "camera"},
           "label_attributes": [{"name": "value", "values": ["front", "fisheye_front"]}]}
    assert "value" in T.suggest(fish, [cam])[0]["review"], "fisheye: hướng do người chọn"
    assert T.suggest(color, [cam])[0]["attributes"]["value"] == "front"


def test_light_color_and_fisheye_edge():
    import cv2
    import numpy as np

    from backend import pipeline as P

    img = np.zeros((200, 200, 3), np.uint8)
    cv2.circle(img, (50, 40), 8, (255, 30, 20), -1)   # RGB đỏ
    assert P.light_color(img, [30, 20, 70, 100]) == "red"
    assert P.light_color(img, [120, 120, 180, 180]) == "off"
    cfgs = [{"label_id": 13, "attr_rules": {"color": "light_color", "edge_zone": "fisheye_edge", "type": "manual"},
             "label_attributes": [{"name": "color", "values": ["red", "green", "off"]}, {"name": "edge_zone", "values": ["false", "true"]},
                                  {"name": "type", "values": ["vehicle"]}]}]
    props = [{"label_id": 13, "bbox": [30, 20, 70, 100], "attributes": {}}]
    P.apply_attr_rules(props, img, cfgs)
    assert props[0]["attributes"]["color"] == "red" and props[0]["review_attributes"] == ["type"]
    assert "edge zone" not in props[0]["attributes"], "không phải ảnh fisheye: không đoán edge_zone"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def _img_b64(color=(90, 90, 90)):
    import base64

    import cv2
    import numpy as np

    image = np.full((720, 1280, 3), color, np.uint8)
    ok, buf = cv2.imencode(".jpg", image)
    return base64.b64encode(buf.tobytes()).decode()


def test_project_profile_preview_save_and_use(client):
    import base64

    md = "# Cabin\n\nTrái/phải theo người trong ảnh.\n\n| Label | Dạng CVAT | Vẽ cái gì |\n|---|---|---|\n| `person` | rectangle | người |\n| `face` | rectangle | mặt |\n| `seatbelt_tool` | rectangle | dây |\n"
    body = {"labels": cabin_labels(), "project_id": 42, "task_id": 5, "name": "Cabin K5", "filename": "cabin.md",
            "data_b64": base64.b64encode(md.encode()).decode(), "use_llm": True}
    prev = client.post("/profile/project/preview", json=body).json()
    assert prev["profile"]["id"] == "proj_p42" and not prev["exists"]
    assert any("chưa bật AI" in n for n in prev["notes"]), "bật LLM mà backend chưa cấu hình: nói rõ"
    assert any("seatbelt_tool" in w for w in prev["warnings"]), "label guideline có mà task chưa có"
    assert prev["guideline"]["file"] == "cabin.md"
    # thử trên frame trước khi lưu
    tried = client.post("/propose", json={"labels": cabin_labels(), "profile_inline": prev["profile"], "image_b64": _img_b64()}).json()
    assert tried["profile_id"] == "proj_p42" and "tags" in tried
    assert tried["tags"][0]["label_name"] == "frame_meta"
    saved = client.post("/profile/project/save", json={"profile": prev["profile"], "guideline_id": prev["guideline"]["id"]}).json()
    assert saved["profile_id"] == "proj_p42"
    res = client.post("/resolve", json={"profile_id": "auto", "labels": cabin_labels(), "project_id": 42, "task_id": 9}).json()
    assert res["profile_id"] == "proj_p42" and res["project_profile"], "job khác cùng project dùng luôn profile"
    assert res["track"]["profile_id"] == "proj_p42"
    other = client.post("/resolve", json={"profile_id": "auto", "labels": cabin_labels(), "project_id": 43}).json()
    assert other["profile_id"] != "proj_p42", "project khác không dùng nhầm"
    cur = client.post("/profile/project/current", json={"labels": [], "project_id": 42}).json()
    assert cur["profile"]["id"] == "proj_p42"
    out = client.post("/propose", json={"profile_id": "auto", "labels": cabin_labels(), "project_id": 42, "image_b64": _img_b64()}).json()
    names = {p["label_name"] for p in out["proposals"]}
    assert "face13" in names, out["warnings"]
    bad = client.post("/profile/project/save", json={"profile": dict(prev["profile"], id="../evil")})
    assert bad.status_code == 400
    client.delete("/profiles/proj_p42")
    client.delete(f"/guideline/imported/{prev['guideline']['id']}")


# ------------------------------------------------------------- tracking ---
def _det(box, label=1, fill=0.6, hist=None):
    import numpy as np

    h = hist if hist is not None else (np.ones(48) / 48).tolist()
    return {"label_id": label, "box": list(map(float, box)), "score": 0.9, "hist": h, "fill": fill}


def _profile(**rule):
    from backend.autoprofile import DEFAULT_TRACKING

    return {"rules": {"tracking": dict(DEFAULT_TRACKING, **rule)}}


CFGS = [{"label_id": 1, "name": "person"}]


def walker(frames, x0=100, step=4, y=200, w=60, h=160, skip=(), fill=0.6, fills=None, label=1, hist=None):
    out = {}
    for f in frames:
        if f in skip:
            continue
        x = x0 + step * f
        out[f] = _det([x, y, x + w, y + h], label, (fills or {}).get(f, fill), hist)
    return out


def _frames(*walkers, n=40):
    return [{"frame": f, "dets": [w[f] for w in walkers if f in w]} for f in range(n)]


def test_short_miss_is_bridged_not_outside():
    from backend import tracking as T

    a = walker(range(40), skip={20, 21})
    res = T.build_tracks(_frames(a), CFGS, _profile(), 1280, 720, 0, 39)
    t = res["tracks"][0]
    assert not any(s["outside"] for s in t["shapes"][:-1]), "bỏ sót 2 frame: nối qua, không outside"
    assert t["gaps"] == 0 and any("Nối qua 2 frame" in f["message"] for f in t["flags"])
    long_gap = walker(range(40), skip=set(range(15, 25)))
    res = T.build_tracks(_frames(long_gap), CFGS, _profile(), 1280, 720, 0, 39)
    assert any(s["outside"] for t in res["tracks"] for s in t["shapes"]), "khuất lâu (MOT): outside rồi hiện lại"
    res = T.build_tracks(_frames(long_gap), CFGS, _profile(max_occluded_frames=12), 1280, 720, 0, 39)
    t = res["tracks"][0]
    assert not any(s["outside"] for s in t["shapes"]) and any(s["occluded"] for s in t["shapes"]), "guideline cho giữ box khi bị che"


def test_leaving_the_frame_is_outside_even_for_short_gaps():
    from backend import tracking as T

    # người ở sát mép phải (hộp bị cắt ở 1280 từ frame 12): mất 3 frame = đi ra khỏi khung
    a = {f: _det([1150 + 4 * f, 200, min(1280, 1232 + 4 * f), 360]) for f in range(30) if f not in (20, 21, 22)}
    res = T.build_tracks(_frames(a, n=30), CFGS, _profile(), 1280, 720, 0, 29)
    assert any(s["outside"] and s["frame"] == 20 for t in res["tracks"] for s in t["shapes"])
    # chỉ mất 1 frame ở mép: model bỏ sót, nối qua
    b = {f: d for f, d in a.items()} | {21: _det([1234, 200, 1280, 360]), 22: _det([1238, 200, 1280, 360])}
    res = T.build_tracks(_frames(b, n=30), CFGS, _profile(), 1280, 720, 0, 29)
    assert not any(s["outside"] and s["frame"] == 20 for t in res["tracks"] for s in t["shapes"])


def test_hole_in_the_mask_marks_occluded():
    from backend import tracking as T

    a = walker(range(30), fills={f: 0.25 for f in range(12, 18)})
    res = T.build_tracks(_frames(a, n=30), CFGS, _profile(), 1280, 720, 0, 29)
    occ = [s for s in res["tracks"][0]["shapes"] if s["occluded"]]
    assert occ and 12 <= occ[0]["frame"] <= 17, "cột / xe che giữa người: mask thủng dù hộp không co"


def test_only_chosen_people_and_zones_and_static_poster():
    import numpy as np

    from backend import tracking as T

    red = np.zeros(48); red[0] = 1
    blue = np.zeros(48); blue[20] = 1
    p1 = walker(range(60), hist=red.tolist())
    p2 = walker(range(60), y=420, hist=blue.tolist())
    poster = {f: _det([900, 100, 950, 230]) for f in range(60)}
    mirror = {f: _det([30 + f, 40, 60 + f, 120]) for f in range(60)}
    frames = _frames(p1, p2, poster, mirror, n=60)
    res = T.build_tracks(frames, CFGS, _profile(), 1280, 720, 0, 59)
    names = {t["tid"]: t for t in res["tracks"]}
    still = [t for t in res["tracks"] if any("Đứng yên" in f["message"] for f in t["flags"])]
    assert len(still) == 1 and not still[0]["checked"], "người trên poster: gắn cờ, không tick sẵn"
    res = T.build_tracks(frames, CFGS, _profile(), 1280, 720, 0, 59, zones=[[0, 0, 200, 200]])
    assert len(res["tracks"]) == 3 and any("vùng bỏ qua" in w for w in res["warnings"])
    target = {"name": "P1", "frame": 30, "box": [220, 200, 280, 360], "label_id": 1}
    res = T.build_tracks(frames, CFGS, _profile(), 1280, 720, 0, 59, targets=[target])
    assert [t["name"] for t in res["tracks"]] == ["P1"], res["warnings"]
    assert res["tracks"][0]["start"] == 0 and res["tracks"][0]["end"] == 59, "theo dõi cả trước lẫn sau frame chọn"
    assert any("Bỏ 3 track" in w for w in res["warnings"])
    split = dict(p1)
    for f in range(25, 35):
        split.pop(f)
    split2 = {f: d for f, d in split.items() if f >= 35}
    first = {f: d for f, d in split.items() if f < 25}
    # người P1 mất dấu 10 frame rồi hiện lại xa chỗ cũ: vẫn nối vào P1 nhờ ngoại hình
    for f in split2:
        b = split2[f]["box"]
        split2[f] = dict(split2[f], box=[b[0] + 300, b[1], b[2] + 300, b[3]])
    frames = [{"frame": f, "dets": [d for d in (first.get(f), split2.get(f), p2.get(f)) if d]} for f in range(60)]
    res = T.build_tracks(frames, CFGS, _profile(), 1280, 720, 0, 59, targets=[dict(target, frame=10, box=[140, 200, 200, 360])])
    assert len(res["tracks"]) == 1 and res["tracks"][0]["end"] == 59 and any(f["code"] == "id_check" for f in res["tracks"][0]["flags"])


def test_track_cancel_and_cuda_fault(client, monkeypatch):
    import backend.app as A

    assert client.post("/track/cancel", json={"session_id": "abc"}).json()["ok"]
    assert "abc" in A.rt.cancelled
    assert A.is_cuda_fault(RuntimeError("CUDA error: an illegal memory access was encountered"))
    assert not A.is_cuda_fault(ValueError("bad image"))
    exits = []
    monkeypatch.setattr(A, "restart_after_cuda_fault", lambda: exits.append(1))
    monkeypatch.setattr(A.settings, "auto_restart_on_gpu_fault", True)

    @A.app.get("/__boom")
    def boom():
        raise RuntimeError("CUDA error: an illegal memory access was encountered")

    from fastapi.testclient import TestClient

    with TestClient(A.app, raise_server_exceptions=False, base_url="http://127.0.0.1:8765") as c:
        r = c.get("/__boom")
    assert r.status_code == 503 and r.json()["code"] == "E_GPU_FAULT" and exits == [1]


def test_kernels_notice_is_filtered():
    import logging

    from backend.engines import sam3_video as V

    V.quiet_kernels_notice()
    rec = logging.LogRecord("transformers.models.sam3_video", logging.WARNING, "", 0,
                            "[transformers] kernels library is not installed. NMS post-processing...", None, None)
    assert not V._NoKernelsNotice().filter(rec)


def test_desktop_turns_on_gpu_fault_restart_only_with_auto_restart(tmp_path):
    api = _api_desktop(tmp_path)
    assert api._backend.extra_env["SAM3_RESTART_ON_GPU_FAULT"] == "1"
    api.set_setting("auto_restart_backend", False)
    assert api._backend.extra_env["SAM3_RESTART_ON_GPU_FAULT"] == "0"


def _api_desktop(tmp_path):
    from desktop.components import Components
    from desktop.control import ControlApi
    from desktop.paths import Paths

    class Be:
        state = "stopped"

        def __init__(self):
            self.extra_env, self.auto_restart = {}, True

    paths = Paths(tmp_path / "data", app_dir=ROOT).ensure()
    return ControlApi(paths, Components(paths, manifest={}), Be(), open_site=lambda u: None, on_quit=lambda: None)


# ------------------------------------------------------------- tự học ---
def test_learning_from_tracks_points_and_attributes(tmp_path):
    from backend.learning import Learner, apply_learned

    L = Learner(tmp_path)
    ev = []
    for i in range(12):
        ev.append({"kind": "applied", "profile_id": "p", "label_name": "person", "label_id": 1, "job_id": 5, "frame": i, "type": "track",
                   "score": 0.8, "bbox": [0, 0, 10, 10], "cvat_id": 100 + i, "track_shapes": 4})
        ev.append({"kind": "applied", "profile_id": "p", "label_name": "face13", "label_id": 3, "job_id": 5, "frame": i, "type": "skeleton",
                   "score": 0.9, "bbox": [100, 100, 200, 200], "cvat_id": 200 + i, "points": {"31": [100, 100], "32": [200, 200]}})
        ev.append({"kind": "applied", "profile_id": "p", "label_name": "car", "label_id": 2, "job_id": 5, "frame": i, "type": "rectangle",
                   "score": 0.9, "bbox": [0, 0, 50, 50], "cvat_id": 300 + i, "auto_attributes": {"truncated": "true"}})
    assert L.record(ev) == 36
    tracks = [{"id": 100 + i, "label_id": 1, "shapes": [{}] * (4 if i < 6 else 7)} for i in range(10)]   # 2 track bị xoá
    shapes = [{"id": 200 + i, "frame": i, "label_id": 3, "type": "skeleton",
               "elements": [{"label_id": 31, "points": [110, 100]}, {"label_id": 32, "points": [210, 200]}]} for i in range(12)]
    shapes += [{"id": 300 + i, "frame": i, "label_id": 2, "type": "rectangle", "points": [0, 0, 50, 50],
                "attributes": [{"spec_id": 9, "value": "false"}]} for i in range(12)]
    res = L.review(5, shapes, tracks, {"9": "truncated"})
    labels = {e["label"]: e for e in res["learned"]["labels"]}
    assert labels["person"]["tracks"] == {"kept": 6, "edited": 4, "deleted": 2, "relabeled": 0}
    assert labels["face13"]["point_adjust"] == {"31": [0.1, 0.0], "32": [0.1, 0.0]}
    assert labels["car"]["attr_off"] == ["truncated"]
    cfgs = [{"name": "face13", "label_id": 3, "shape": "skeleton", "threshold": 0.5},
            {"name": "car", "label_id": 2, "shape": "rectangle", "threshold": 0.5, "attr_rules": {"truncated": "border"}}]
    apply_learned(cfgs, L.for_profile("p"), {})
    assert cfgs[0]["point_adjust"]["31"] == [0.1, 0.0] and cfgs[1]["attr_off"] == ["truncated"] and not cfgs[1]["attr_rules"]


def test_point_adjust_moves_skeleton_points():
    from backend import keypoints as K

    cfg = {"label_id": 3, "name": "f", "point_adjust": {"31": [0.1, 0.0]}}
    els = [{"label_id": 31, "x": 100.0, "y": 100.0, "outside": False}, {"label_id": 32, "x": 200.0, "y": 200.0, "outside": False}]
    p = K._proposal(cfg, els, 1.0, [], [100, 100, 200, 200])
    assert p["elements"][0]["x"] == 110.0 and p["elements"][1]["x"] == 200.0


def test_detector_explanations_and_component(tmp_path, monkeypatch):
    from backend import custom_model as M

    monkeypatch.delenv("SAM3_DETECTOR_BASE", raising=False)
    meta = {"classes": ["car", "bus"], "metrics": {"car": {"recall": 0.0}, "bus": {"recall": 0.0}}, "counts": {"car": 40, "bus": 5},
            "usable": [], "base": False, "val_images": 3}
    notes = " ".join(M.explain(meta))
    assert "con số 0" in notes and "bus" in notes, "0% vì học từ đầu: nói rõ và chỉ cách sửa"
    from desktop.components import COMPONENTS, Components
    from desktop.paths import Paths

    paths = Paths(tmp_path / "d", app_dir=ROOT).ensure()
    comps = Components(paths, manifest={"models": {}})
    assert "detector" in COMPONENTS and not comps.installed("detector")
    plan = comps._plan_file("detector", None, "", False)
    assert plan["action"] == "none", "bản cài cũ chưa có thành phần này: không lỗi"
    env = comps.backend_env()
    assert env.get("SAM3_DETECTOR_BASE", "").endswith(".pth") or comps.system


# ------------------------------------------------------------- cuboid 3D ---
def _scene(car_x=10.0, seed=0, ped=True, car=True):
    import numpy as np

    rng = np.random.default_rng(seed)
    g = rng.uniform([-30, -30], [30, 30], (6000, 2))
    ground = np.c_[g, rng.normal(-1.8, 0.02, len(g))]                 # mặt đường thấp hơn LiDAR 1.8 m
    parts = [ground]
    if car:    # xe con 4.5 x 1.8 x 1.5 m, đầu xe theo trục x
        c = rng.uniform([car_x - 2.25, 3 - 0.9, -1.8 + 0.05], [car_x + 2.25, 3 + 0.9, -1.8 + 1.5], (900, 3))
        parts.append(c)
    if ped:    # người 0.6 x 0.6 x 1.75 m
        parts.append(rng.uniform([-6.3, -8.3, -1.75], [-5.7, -7.7, -1.8 + 1.75], (150, 3)))
    parts.append(rng.uniform([27, -25, -1.75], [27.4, 25, 3.0], (2000, 3)))    # bức tường dài: không phải vật cần gán
    return np.vstack(parts).astype("float32")


def _pcd(points, kind="binary"):
    import struct

    header = (f"# .PCD v0.7\nVERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nWIDTH {len(points)}\nHEIGHT 1\n"
              f"VIEWPOINT 0 0 0 1 0 0 0\nPOINTS {len(points)}\nDATA {kind}\n").encode()
    if kind == "ascii":
        return header + "\n".join(" ".join(f"{v:.4f}" for v in p) for p in points).encode()
    if kind == "binary":
        return header + points.astype("<f4").tobytes()
    raw = b"".join(points[:, i].astype("<f4").tobytes() for i in range(3))   # nén theo cột, LZF chỉ dùng đoạn chép nguyên
    comp = bytearray()
    for i in range(0, len(raw), 32):
        chunk = raw[i:i + 32]
        comp.append(len(chunk) - 1)
        comp += chunk
    return header + struct.pack("<II", len(comp), len(raw)) + bytes(comp)


def test_pcd_formats_and_zip():
    import io
    import zipfile

    import numpy as np

    from backend import lidar as L

    pts = _scene()[:500]
    for kind in ("ascii", "binary", "binary_compressed"):
        got = L.read_points(_pcd(pts, kind))
        assert got.shape == pts.shape and np.allclose(got, pts, atol=1e-3), kind
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("frame_000001.pcd", _pcd(pts))
    assert L.read_points(buf.getvalue()).shape == pts.shape
    kitti = np.c_[pts, np.ones(len(pts))].astype("<f4").tobytes()
    assert L.read_points(kitti, "000001.bin").shape == pts.shape


def test_cuboids_from_lidar_follow_the_size_table():
    import math

    from backend import lidar as L

    labels = {"car": 1, "pedestrian": 2, "truck": 3}
    res = L.detect(_scene(), labels, L.DEFAULT_SIZES)
    by = {}
    for o in res["objects"]:
        by.setdefault(o["label_id"], []).append(o)
    assert len(by.get(1, [])) == 1 and len(by.get(2, [])) == 1, res["objects"]
    car = by[1][0]
    assert abs(car["center"][0] - 10) < 0.3 and abs(car["center"][1] - 3) < 0.3
    assert abs(car["size"][0] - 4.5) < 0.3 and abs(car["size"][1] - 1.8) < 0.3 and abs(car["size"][2] - 1.5) < 0.2
    assert abs(car["center"][2] - car["size"][2] / 2 - (-1.8)) < 0.1, "đáy cuboid chạm mặt đường"
    assert min(abs(car["yaw"]), abs(abs(car["yaw"]) - math.pi)) < 0.1
    assert all(o["size"][0] < 20 for o in res["objects"]), "bức tường dài bị bỏ"


def test_cuboid_track_locks_size_and_keeps_id_through_short_occlusion():
    from backend import lidar as L

    labels = {"car": 1}
    frames = []
    for f in range(12):
        if f in (5, 6, 7):          # xe bị xe buýt che 3 frame
            frames.append({"frame": f, "objects": []})
            continue
        res = L.detect(_scene(car_x=2 + 1.5 * f, seed=f, ped=False), labels, L.DEFAULT_SIZES)
        frames.append({"frame": f, "objects": res["objects"]})
    out = L.build_tracks(frames, {"max_occluded_frames": 6}, {1: "Car"})
    assert len(out["tracks"]) == 1, out
    t = out["tracks"][0]
    assert t["start"] == 0 and t["end"] == 11
    sizes = {tuple(s["points"][6:9]) for s in t["shapes"]}
    assert len(sizes) == 1, "kích thước khoá theo frame dày điểm nhất"
    occ = [s for s in t["shapes"] if s["occluded"]]
    assert occ and all(5 <= s["frame"] <= 7 for s in occ), "bị che ngắn: nội suy, bật occluded"
    assert t["shapes"][-1]["outside"] and t["shapes"][-1]["frame"] == 12
    assert all(len(s["points"]) == 16 for s in t["shapes"]), "định dạng cuboid của CVAT 3D"
    assert t["keyframes"] < 13, "đi thẳng đều: ít keyframe"
    gone = [dict(fr, objects=[]) if 3 <= fr["frame"] <= 9 else fr for fr in frames]
    assert len(L.build_tracks(gone, {"max_occluded_frames": 6}, {1: "Car"})["tracks"]) == 2, "khuất > 6 frame: track mới"


def test_lidar_api(client):
    import base64

    labels = [{"id": 1, "name": "Car", "type": "cuboid"}, {"id": 2, "name": "Pedestrian", "type": "cuboid"}]
    frames = [{"frame": f, "data_b64": base64.b64encode(_pcd(_scene(car_x=2 + f, seed=f))).decode()} for f in range(3)]
    det = client.post("/lidar/detect", json={"labels": labels, "frames": frames}).json()
    assert [len(f["objects"]) for f in det["frames"]] == [2, 2, 2], det
    tr = client.post("/lidar/track", json={"labels": labels, "frames": det["frames"]}).json()
    assert {t["label_name"] for t in tr["tracks"]} == {"Car", "Pedestrian"}
    bad = client.post("/lidar/detect", json={"labels": [{"id": 5, "name": "lane", "type": "polyline"}], "frames": frames})
    assert bad.status_code == 400


# ------------------------------------------------------------ F: kiểm tra máy, bộ cài ---
def test_machine_check_rows(tmp_path):
    from desktop import machine

    probe = {"build": lambda: 19045, "free_gb": lambda d: 40.0, "ram_gb": lambda: 16.0, "vc_missing": lambda: [],
             "webview2": lambda: "131.0.2903.70", "wsl": lambda: False, "docker": lambda: False}
    rows = {r["key"]: r for r in machine.check(tmp_path, probe)}
    assert rows["windows"]["kind"] == "ok" and "19045" in rows["windows"]["detail"]
    assert rows["disk"]["kind"] == "ok" and rows["ram"]["kind"] == "ok"
    assert rows["vcredist"]["kind"] == "ok" and rows["webview2"]["kind"] == "ok"
    assert rows["wsl"]["fix"] == "wsl_install", "Windows đủ mới: có nút cài WSL2 thẳng"
    assert rows["docker"]["fix"] == "docker" and "giấy phép" in rows["docker"]["detail"]

    bad = dict(probe, build=lambda: 18363, free_gb=lambda d: 4.0, ram_gb=lambda: 4.0,
               vc_missing=lambda: ["vcruntime140_1.dll"], webview2=lambda: "", wsl=lambda: False)
    rows = {r["key"]: r for r in machine.check(tmp_path, bad)}
    assert rows["windows"]["kind"] == "warn" and rows["disk"]["kind"] == "fail" and rows["ram"]["kind"] == "warn"
    assert rows["vcredist"]["kind"] == "fail" and rows["vcredist"]["fix"] == "vcredist"
    assert rows["webview2"]["fix"] == "webview2"
    assert rows["wsl"]["fix"] == "wsl", "Windows cũ: chỉ mở trang hướng dẫn"


def test_machine_check_linux_and_vc(tmp_path):
    from desktop import machine

    rows = machine.check(tmp_path)          # máy test là Linux: chỉ ổ đĩa và RAM
    assert {r["key"] for r in rows} <= {"disk", "ram"} and any(r["key"] == "disk" for r in rows)
    sys32 = tmp_path / "System32"
    sys32.mkdir()
    (sys32 / "vcruntime140.dll").write_bytes(b"")
    assert machine.vc_missing(sys32) == ["vcruntime140_1.dll", "msvcp140.dll"]
    calls = []
    assert machine.install_wsl(lambda exe, args: calls.append((exe, args)) or True)
    assert calls == [("wsl.exe", "--install --no-distribution")]


def test_control_machine_api(tmp_path, monkeypatch):
    from desktop import control, machine

    monkeypatch.setattr(machine, "install_wsl", lambda launch=None: True)
    opened = []
    api = control.ControlApi.__new__(control.ControlApi)
    api._paths = type("P", (), {"data": tmp_path})()
    api._settings = type("S", (), {"data": {"lang": "vi"}})()
    api.open_link = lambda k: opened.append(k) or True
    assert any(r["key"] == "disk" for r in api.machine_check())
    assert api.machine_fix("wsl_install") == {"ok": True, "restart": True}
    assert api.machine_fix("vcredist") == {"ok": True} and opened == ["vcredist"]


def test_installer_webview2_and_wsl():
    iss = (ROOT / "installer" / "sam3-assist.iss").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
    assert 'Source: "{#BuildDir}\\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy' in iss
    assert "ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe')" in iss and "'/silent /install'" in iss
    assert 'Name: "webview2"' in iss and "Check: NeedWebView2" in iss
    assert 'Name: "wsl"' in iss and "Flags: unchecked; Check: CanOfferWsl" in iss, "WSL2 không chọn sẵn"
    assert "--install --no-distribution" in iss and "WizardIsTaskSelected('wsl')" in iss
    i = ci.index("MicrosoftEdgeWebview2Setup.exe -OutFile") if "MicrosoftEdgeWebview2Setup.exe -OutFile" in ci else ci.index("-OutFile build\\MicrosoftEdgeWebview2Setup.exe")
    assert ci.index("Get-AuthenticodeSignature build\\MicrosoftEdgeWebview2Setup.exe") > i
    assert ci.index("MicrosoftEdgeWebview2Setup") < ci.index("Đóng gói bằng Inno Setup")


# --------------------------------------------- đối chiếu 8 hình người dùng gửi (0.9.4) ---
def test_scope_question_follows_purpose_and_merges_twins(library):
    """Hình 2: 'bus' có trong mục đích thì mặc định Thêm class; 'person' và 'pedestrian' trùng nhau chỉ hỏi một lần."""
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from conftest import ScriptedEngine, rect, H, W
    import numpy as np
    from backend.guideline.service import GuidelineService

    walker = rect(500, 300, 540, 420)
    script = {"car": [(rect(100, 300, 300, 420), 0.9)], "bus": [(rect(600, 200, 900, 420), 0.9)],
              "person": [(walker, 0.8)], "pedestrian": [(walker, 0.75)]}
    svc = GuidelineService(library, None)
    started = svc.start([{"id": 1, "name": "car", "type": "rectangle"}, {"id": 2, "name": "truck", "type": "rectangle"}],
                        {"id": 7, "mode": "annotation"}, "Phát hiện các loại xe như car, bus, truck", ["pedestrian"],
                        False, {"car": ["car"], "truck": ["truck"]}, "t")
    eng = ScriptedEngine(script)
    for f in range(10):
        svc.scan(started["run_id"], eng, [(f, np.full((H, W, 3), 120, np.uint8))])
    view = svc.draft(started["run_id"])
    q = {x["key"]: x for x in view["questions"]}
    assert q["scope:bus"]["recommended"] == "add" and "Mục đích" in q["scope:bus"]["reason"]
    asked = [k for k in q if k in ("scope:person", "scope:pedestrian")]
    assert len(asked) == 1, asked
    assert "hỏi chung" in q[asked[0]]["evidence"]


def test_importer_reads_lab_guidelines():
    """Hình 4, 8: skeleton theo mục + bảng tên điểm; dòng 'Tag ảnh' tách thành từng tag; video đoán label từ câu 'Nhãn: `person`'."""
    from backend.guideline import importer as I

    skel = """# Quy ước nhãn
## 1. Skeleton người: 17 điểm COCO
| # | Tên trên CVAT | Nghĩa |
|---:|---|---|
| 1 | `nose` | mũi |
| 2 | `left_eye` | mắt trái |
| 3 | `right_eye` | mắt phải |

Mẫu `person` trên CVAT dựng theo người quay mặt về camera.

## 2. Skeleton khuôn mặt: 13 điểm của lab
| # | Tên | Đặt ở đâu |
|---:|---|---|
| 1 | `left_eye_outer` | khóe ngoài |
| 2 | `left_eye_inner` | khóe trong |
| 3 | `nose_tip` | chóp mũi |
"""
    r = I.analyze(skel, "s.md", lambda n: None)
    assert [(l["name"], l["shape"], len(l["points"])) for l in r["labels"]] == [("person", "skeleton", 3), ("face", "skeleton", 3)]
    road = """| Label | Dạng trong CVAT | Vẽ cái gì | Thuộc tính |
| --- | --- | --- | --- |
| `traffic_light` | rectangle | vỏ đèn | color red, green · edge_zone (ô tick) |
| Tag ảnh | tag (cả ảnh) | mỗi ảnh một lần | camera front, fisheye_left · timeofday day, night |
| tag `frame_meta` | tag | một lần | `modality`: rgb, ir · `eye_left`, `eye_right`: open, closed |
"""
    r = {l["name"]: l for l in I.analyze(road, "r.md", lambda n: None)["labels"]}
    assert "ảnh" not in r and r["camera"]["shape"] == "tag" and r["timeofday"]["attributes"][0]["values"] == ["day", "night"]
    assert [a["name"] for a in r["frame_meta"]["attributes"]] == ["modality", "eye_left", "eye_right"]
    assert r["traffic_light"]["attributes"][1] == {"name": "edge_zone", "values": ["false", "true"], "checkbox": True}
    video = "# Tracking\n## 1. Đối tượng\n- Nhãn: `person`, công cụ **Rectangle**, chế độ **Track**.\n- Label học viên tự đặt.\n"
    assert [(l["name"], l["shape"]) for l in I.analyze(video, "v.md", lambda n: None)["labels"]] == [("person", "rectangle")]


def test_id_check_one_flag_with_top_candidates():
    """Hình 7: track mới gần nhiều track vừa mất chỉ có MỘT cờ 'Kiểm tra ID', tối đa 3 ứng viên."""
    src = (ROOT / "backend" / "tracking.py").read_text(encoding="utf-8")
    loop = src[src.index("for later in out:"):src.index("out.sort(key=lambda x: (x[\"start\"], x[\"tid\"]))")]
    assert loop.count("add_flag(") == 1 and "cands[:3]" in loop and "Có thể cùng người với track" in loop


def test_tag_label_in_plain_profile_points_to_project_profile():
    """Hình 8: tag trong profile thường hiện đúng kiểu 'tag' và chỉ tới Profile cho project."""
    from backend.profiles import ProfileStore, resolve_labels

    store = ProfileStore(ROOT / "profiles")
    prof = store.get("generic", ["car", "weather"])
    res = resolve_labels(prof, [{"id": 1, "name": "car", "type": "rectangle"}, {"id": 2, "name": "weather", "type": "tag"}], {})
    tag = next(c for c in res if c["name"] == "weather")
    assert tag["shape"] == "tag" and not tag["supported"] and "Profile cho project" in tag["note"]


def test_panel_fixes_from_screenshots():
    js = (ROOT / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
    assert "const key = `${S.jobId}:${start}:${stop}`" in js, "hình 6: khoảng frame không giữ của job trước"
    assert "allDiffer" in js and "g.frames.map" in js, "hình 1: gom frame cùng lý do, ẩn keyframe vô nghĩa"
    assert "flagGroups(" in js and "×${g.items.length}" in js, "hình 7: chip cờ gom theo loại"
    assert "cfg.profileByTask" in js, "hình 8: profile chọn tay nhớ theo task"
    assert "table.profiles { table-layout: fixed" in js, "hình 5: bảng profile không tràn ngang"
    assert "max-height: 7.5em; overflow: auto" in js, "hình 1, 4: thanh trạng thái không bị cắt"
    assert "Mở Profile cho project" in js
