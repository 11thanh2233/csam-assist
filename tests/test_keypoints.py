"""Skeleton theo guideline HumanPose-17 (người lái) và Facial Landmark-50 (7 skeleton mặt), bằng model giả lập."""
import copy
import json
from pathlib import Path

import numpy as np

from backend import keypoints, qa
from backend.engines.pose import LazyEngine, MockBodyPose, MockFace
from backend.profiles import resolve_labels

from conftest import H, W, ScriptedEngine, rect

LABELS_FILE = Path(__file__).parent / "data" / "driver_skeleton_labels.json"
FACE_NAMES = ["longmaytrai", "longmayphai", "songmui", "mattrai", "matphai", "moingoai", "moitrong"]
IMAGE = np.zeros((H, W, 3), np.uint8)


def driver_labels():
    return json.loads(LABELS_FILE.read_text(encoding="utf-8"))


def setup(store, labels=None):
    labels = labels or driver_labels()
    profile = store.get("auto", [l["name"] for l in labels], "frame", [l["type"] for l in labels])
    assert profile["id"] == "driver_pose17_face50_v1"
    return labels, profile, resolve_labels(profile, labels)


def lazy(engine):
    return LazyEngine(lambda: engine, engine.name)


def run(profile, cfgs, script=None, body=None, face=None, existing=None):
    return keypoints.propose(ScriptedEngine(script or {}), {}, IMAGE, profile, cfgs, existing or [],
                             lazy(body or MockBodyPose()), lazy(face or MockFace()))


def el(p, name):
    return next(e for e in p["elements"] if e["name"] == str(name))


def by_label(result):
    return {p["label_name"]: p for p in result["proposals"]}


def to_shape(p, shape_id=1):
    return {"id": shape_id, "type": "skeleton", "label_id": p["label_id"], "points": [],
            "elements": [{"label_id": e["label_id"], "points": [e["x"], e["y"]], "outside": e["outside"],
                          "occluded": e["occluded"]} for e in p["elements"]]}


# ------------------------------------------------------------ ghép label ---
def test_labels_map_by_number(store):
    _, _, cfgs = setup(store)
    person = cfgs[0]
    assert all(c["supported"] and c["enabled"] and not c["missing"] and not c["matched_by_order"] for c in cfgs)
    assert len(person["keypoints"]) == 17 and len(person["edges"]) == 18
    assert [k.get("side") for k in person["keypoints"]] == [None] + ["R", "L"] * 8  # số chẵn R, số lẻ L
    sizes = {c["name"]: (len(c["keypoints"]), len(c["edges"])) for c in cfgs[1:]}
    assert sizes == {"longmaytrai": (5, 4), "longmayphai": (5, 4), "songmui": (4, 3), "mattrai": (8, 8),
                     "matphai": (8, 8), "moingoai": (12, 12), "moitrong": (8, 8)}
    assert sum(n for n, _ in sizes.values()) == 50


def test_other_sublabel_namings_still_map(store):
    labels = driver_labels()
    named = copy.deepcopy(labels[:1])
    coco = ["nose", "right_eye", "left_eye", "right_ear", "left_ear", "right_shoulder", "left_shoulder", "right_elbow",
            "left_elbow", "right_wrist", "left_wrist", "right_hip", "left_hip", "right_knee", "left_knee",
            "right_ankle", "left_ankle"]
    for sub, name in zip(named[0]["sublabels"], coco):
        sub["name"] = name
    _, _, cfgs = setup(store, named)
    assert [k["index"] for k in cfgs[0]["keypoints"]] == [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]

    # "2 - Right Eye (R)" kiểu ghi chú dài: vẫn ghép theo số
    verbose = copy.deepcopy(labels[:1])
    for sub in verbose[0]["sublabels"]:
        sub["name"] = f"{sub['name']} - kp"
    _, _, cfgs = setup(store, verbose)
    assert not cfgs[0]["missing"] and cfgs[0]["keypoints"][1]["index"] == 2

    # Task đánh số 0-16 (lệch 1 so với guideline): KHÔNG ghép theo số, ghép theo thứ tự và báo để người dùng kiểm tra
    zero = copy.deepcopy(labels[:1])
    for i, sub in enumerate(zero[0]["sublabels"]):
        sub["name"] = str(i)
    _, _, cfgs = setup(store, zero)
    assert cfgs[0]["matched_by_order"] and "thứ tự" in cfgs[0]["note"]
    assert cfgs[0]["keypoints"][0]["index"] == 0 and cfgs[0]["keypoints"][1]["index"] == 2

    # Skeleton mặt đánh số lại từ 0 trong từng skeleton: ghép theo thứ tự
    local = copy.deepcopy(labels[1:])
    for label in local:
        for i, sub in enumerate(label["sublabels"]):
            sub["name"] = str(i)
    _, _, cfgs = setup(store, local)
    assert all(not c["missing"] and len(c["keypoints"]) == len(l["sublabels"]) for c, l in zip(cfgs, local))
    assert all(k.get("path") for c in cfgs for k in c["keypoints"])


def resolve_auto(store, labels, overrides=None):
    """Như backend làm: tự chọn profile từ label đầy đủ của task, rồi ghép label."""
    profile = store.get("auto", [l["name"] for l in labels], "frame", [l["type"] for l in labels], labels)
    return profile, resolve_labels(profile, labels, overrides)


def test_task_label_named_person_vf17_is_recognised(store):
    """Task thật đặt tên label là 'person_vf17', không phải 'person' như trong guideline."""
    labels = driver_labels()[:1]
    labels[0]["name"] = "person_vf17"
    profile, cfgs = resolve_auto(store, labels)
    assert profile["id"] == "driver_pose17_face50_v1", "tự chọn profile phải nhận ra task skeleton này"
    person = cfgs[0]
    assert person["supported"] and person["enabled"] and person["matched"] and not person["missing"]
    assert person["entry"] == "person" and person["match_how"] == "similar" and "'person'" in person["note"]
    assert person["role"] == "body" and person["prompts"] == ["person", "doll"] and len(person["edges"]) == 18
    p = by_label(run(profile, cfgs, {"person": [(rect(500, 200, 620, 560), 0.9)]}))["person_vf17"]
    assert len(p["elements"]) == 17 and el(p, 6)["x"] > el(p, 7)["x"]
    # Chọn tay profile (như người dùng đã làm) cũng phải ra cùng kết quả
    chosen = store.get("driver_pose17_face50_v1", ["person_vf17"], "frame", ["skeleton"], labels)
    assert resolve_labels(chosen, labels)[0]["supported"]


def test_skeleton_labels_are_recognised_by_structure_when_names_do_not_help(store):
    # Tên không liên quan, điểm đánh số 1-17: nhận theo dãy số của các điểm
    labels = driver_labels()[:1]
    labels[0]["name"] = "vf17"
    profile, cfgs = resolve_auto(store, labels)
    assert profile["id"] == "driver_pose17_face50_v1" and cfgs[0]["entry"] == "person" and cfgs[0]["match_how"] == "ids"
    # Tên không liên quan, điểm đặt tên chữ: nhận theo số điểm (chỉ person có 17 điểm)
    for sub, name in zip(labels[0]["sublabels"], "abcdefghijklmnopq"):
        sub["name"] = "kp_" + name
    profile, cfgs = resolve_auto(store, labels)
    assert cfgs[0]["entry"] == "person" and cfgs[0]["match_how"] == "count" and cfgs[0]["matched_by_order"]

    # 7 skeleton mặt: tên có hậu tố, tên tiếng Việt có dấu, tên tiếng Anh
    face = driver_labels()[1:]
    for label, name in zip(face, ["longmaytrai_vf50", "Lông mày phải", "nose_bridge", "left_eye", "Right-Eye", "outer_lip_vf50", "Môi trong"]):
        label["name"] = name
    profile, cfgs = resolve_auto(store, face)
    assert profile["id"] == "driver_pose17_face50_v1"
    assert [c["entry"] for c in cfgs] == FACE_NAMES and all(c["supported"] for c in cfgs)
    assert [c["match_how"] for c in cfgs] == ["similar", "similar", "exact", "exact", "exact", "similar", "similar"]
    brows = driver_labels()[1:3] + driver_labels()[4:6]
    for label, name in zip(brows, ["left_eyebrow", "right_eyebrow", "left_eye", "right_eye"]):
        label["name"] = name
    _, cfgs = resolve_auto(store, brows)
    assert [c["entry"] for c in cfgs] == ["longmaytrai", "longmayphai", "mattrai", "matphai"], "'left_eyebrow' không được nhận nhầm là 'left eye'"

    # Tên vô nghĩa nhưng Point ID chạy liên tục 0-49 như guideline: nhận cả 7 theo dãy số
    face = driver_labels()[1:]
    for i, label in enumerate(face):
        label["name"] = f"sk{i + 1}"
    profile, cfgs = resolve_auto(store, face)
    assert [c["entry"] for c in cfgs] == FACE_NAMES and {c["match_how"] for c in cfgs} == {"ids"}


def test_unrecognised_skeleton_can_be_mapped_by_hand(store):
    """Tên vô nghĩa VÀ điểm đánh số lại từ 0 trong từng skeleton: chỉ songmui (4 điểm) và moingoai (12 điểm) là rõ."""
    face = driver_labels()[1:]
    for i, label in enumerate(face):
        label["name"] = f"sk{i + 1}"
        for j, sub in enumerate(label["sublabels"]):
            sub["name"] = str(j)
    profile = store.get("driver_pose17_face50_v1", [l["name"] for l in face], "frame", ["skeleton"] * 7, face)
    cfgs = resolve_labels(profile, face)
    assert [c["entry"] for c in cfgs] == [None, None, "songmui", None, None, "moingoai", None]
    unknown = cfgs[0]
    assert not unknown["supported"] and not unknown["enabled"] and "Chọn ở ô bên dưới" in unknown["note"]
    # Người dùng chỉ tay ở tab Label: 5 label còn lại
    manual = {str(face[i]["id"]): {"entry": name} for i, name in ((0, "longmaytrai"), (1, "longmayphai"), (3, "mattrai"), (4, "matphai"), (6, "moitrong"))}
    cfgs = resolve_labels(profile, face, manual)
    assert [c["entry"] for c in cfgs] == FACE_NAMES and all(c["supported"] and c["enabled"] for c in cfgs)
    assert cfgs[0]["match_how"] == "manual" and cfgs[2]["match_how"] == "count"
    assert cfgs[1]["matched_by_order"] and "thứ tự" in cfgs[1]["note"]   # điểm 0-4 của task ứng với Point ID 5-9: ghép theo thứ tự
    f = by_label(run(profile, cfgs))
    assert sum(len(f[f"sk{i}"]["elements"]) for i in range(1, 8)) == 50
    assert f["sk4"]["bbox"][0] < f["sk5"]["bbox"][0], "sk4 = mắt bên trái ảnh, sk5 = mắt bên phải ảnh"
    # Kiểm tra chéo giữa các skeleton vẫn chạy dù tên label khác guideline: môi trong (sk7) phải nằm trong môi ngoài (sk6)
    shapes = [to_shape(f[f"sk{i}"], i) for i in range(1, 8)]
    assert not qa.run_qa(profile, cfgs, face, shapes, W, H)["findings"]
    shapes[6]["elements"][2]["points"][1] -= 60
    shapes[3], shapes[4] = dict(shapes[3], label_id=shapes[4]["label_id"], elements=[dict(e, label_id=o["label_id"]) for e, o in zip(shapes[3]["elements"], shapes[4]["elements"])]), \
        dict(shapes[4], label_id=shapes[3]["label_id"], elements=[dict(e, label_id=o["label_id"]) for e, o in zip(shapes[4]["elements"], shapes[3]["elements"])])
    text = " | ".join(x["message"] for x in qa.run_qa(profile, cfgs, face, shapes, W, H)["findings"])
    assert "nằm ngoài 'sk6'" in text and "'sk4' đang nằm bên phải 'sk5'" in text, text


def test_legacy_call_without_full_labels_still_works(store):
    # Bản userscript cũ / chỗ gọi cũ không gửi label đầy đủ: không lỗi, chỉ không tự nhận được tên lạ
    assert store.get("auto", ["person_vf17"], "frame", ["skeleton"])["id"] == "generic"
    assert store.get("auto", ["person"], "frame", ["skeleton"])["id"] == "driver_pose17_face50_v1"


# ---------------------------------------------------------- HumanPose-17 ---
def test_body_left_right_follow_the_image(store):
    labels, profile, cfgs = setup(store)
    result = run(profile, cfgs, {"person": [(rect(500, 200, 620, 560), 0.9)]})
    p = by_label(result)["person"]
    assert p["type"] == "skeleton" and len(p["elements"]) == 17
    assert {e["label_id"] for e in p["elements"]} == {s["id"] for s in labels[0]["sublabels"]}
    # R (số chẵn) ở bên phải ảnh, L (số lẻ) ở bên trái ảnh, cho mọi cặp khớp
    for r in range(2, 17, 2):
        if not el(p, r)["outside"] and not el(p, r + 1)["outside"]:
            assert el(p, r)["x"] > el(p, r + 1)["x"], f"cặp {r}/{r + 1}"
    assert el(p, 6)["side"] == "R" and el(p, 7)["side"] == "L" and el(p, 1)["side"] is None
    # Trạng thái: model giả lập có một cổ tay tin cậy 0.45 (Occluded) và một cổ chân 0.1 (Outside)
    assert el(p, 1)["occluded"] is False and el(p, 1)["outside"] is False
    assert el(p, 10)["occluded"] is True and el(p, 17)["outside"] is True
    assert any(f["code"] == "ATTRIBUTE_CHECK" and "Occluded" in f["message"] for f in p["flags"])
    assert not any(f["code"] == "UNCERTAIN_CLASS" for f in p["flags"])


class BackView(MockBodyPose):
    """Người quay lưng: trái của người nằm bên trái ảnh."""

    def estimate(self, image_rgb, boxes_xyxy):
        out = super().estimate(image_rgb, boxes_xyxy)
        for (pts, _), (x1, _, x2, _) in zip(out, boxes_xyxy):
            pts[:, 0] = x1 + x2 - pts[:, 0]
        return out


def test_back_view_keeps_the_image_convention(store):
    _, profile, cfgs = setup(store)
    p = by_label(run(profile, cfgs, {"person": [(rect(500, 200, 620, 560), 0.9)]}, body=BackView()))["person"]
    assert el(p, 6)["x"] > el(p, 7)["x"] and el(p, 12)["x"] > el(p, 13)["x"]
    # chuỗi vai-khuỷu-cổ tay mỗi phía liền mạch: khuỷu R cùng phía với vai R
    assert el(p, 8)["x"] > el(p, 9)["x"]


class Twisted(MockBodyPose):
    """Vặn mình: vai và hông cho hai hướng khác nhau."""

    def estimate(self, image_rgb, boxes_xyxy):
        out = super().estimate(image_rgb, boxes_xyxy)
        for pts, scores in out:
            for l, r in ((11, 12), (13, 14), (15, 16)):
                pts[[l, r]] = pts[[r, l]]
            scores[:] = 0.9
        return out


def test_twisted_body_is_flagged_and_each_chain_follows_its_root(store):
    _, profile, cfgs = setup(store)
    p = by_label(run(profile, cfgs, {"person": [(rect(500, 100, 620, 560), 0.9)]}, body=Twisted()))["person"]
    assert el(p, 6)["x"] > el(p, 7)["x"] and el(p, 12)["x"] > el(p, 13)["x"]  # vai và hông đều: R bên phải ảnh
    assert any(f["code"] == "UNCERTAIN_CLASS" and "vặn mình" in f["message"] for f in p["flags"])


def test_only_the_driver_is_labelled(store):
    _, profile, cfgs = setup(store)
    script = {"person": [(rect(200, 100, 620, 700), 0.8), (rect(900, 300, 1000, 500), 0.95)]}  # người lái to, hành khách nhỏ
    result = run(profile, cfgs, script)
    people = [p for p in result["proposals"] if p["label_name"] == "person"]
    assert len(people) == 1 and people[0]["bbox"][0] < 700, "chọn người chiếm diện tích lớn nhất"
    assert any(f["code"] == "UNCERTAIN_SCOPE" and "2 người" in f["message"] for f in people[0]["flags"])
    assert not any("60px" in w for w in result["warnings"]), "guideline mới không có ngưỡng 60px"


class OffImageBody(MockBodyPose):
    def estimate(self, image_rgb, boxes_xyxy):
        out = super().estimate(image_rgb, boxes_xyxy)
        for pts, scores in out:
            pts[15] = [pts[15][0], H + 30]   # một cổ chân ngoài ảnh nhưng model rất tự tin
            scores[15] = 0.95
            pts[8] = pts[6] + 0.5            # khuỷu và vai cùng phía gần như trùng toạ độ, đều Visible
        return out


def test_outside_frame_and_coincident_visible_points(store):
    _, profile, cfgs = setup(store)
    p = by_label(run(profile, cfgs, {"person": [(rect(500, 400, 620, 719), 0.9)]}, body=OffImageBody()))["person"]
    ankles = [el(p, 16), el(p, 17)]
    assert sum(e["outside"] for e in ankles) == 2  # một ngoài khung hình, một do độ tin cậy 0.1
    assert any(f["code"] == "ATTRIBUTE_CHECK" and "trùng toạ độ" in f["message"] for f in p["flags"])


# ------------------------------------------------------ Facial Landmark-50 ---
def test_face_50_points_in_guideline_order(store):
    _, profile, cfgs = setup(store)
    result = run(profile, cfgs)
    f = by_label(result)
    assert all(name in f for name in FACE_NAMES)
    assert sum(len(f[name]["elements"]) for name in FACE_NAMES) == 50
    assert all(not e["outside"] and not e["occluded"] for name in FACE_NAMES for e in f[name]["elements"])
    x = lambda name, i: el(f[name], i)["x"]  # noqa: E731
    y = lambda name, i: el(f[name], i)["y"]  # noqa: E731
    # Lông mày: trái ảnh 0 (đuôi ngoài) -> 4 (đầu trong); phải ảnh 5 (đầu trong) -> 9 (đuôi ngoài)
    assert x("longmaytrai", 0) < x("longmaytrai", 2) < x("longmaytrai", 4) < x("longmayphai", 5) < x("longmayphai", 9)
    # Sống mũi: 10 trên cùng, 13 dưới cùng nhưng chưa tới chóp mũi (MediaPipe điểm 1)
    assert y("songmui", 10) < y("songmui", 11) < y("songmui", 12) < y("songmui", 13)
    tip = MockFace().detect(IMAGE)[0][1]
    assert y("songmui", 13) < tip[1] - 3
    # Mắt: 14 khoé ngoài (trái nhất), 18 khoé trong; mí trên 15-17 trái sang phải, mí dưới 19-21 vòng về
    assert x("mattrai", 14) < x("mattrai", 15) < x("mattrai", 16) < x("mattrai", 17) < x("mattrai", 18)
    assert x("mattrai", 19) > x("mattrai", 20) > x("mattrai", 21) > x("mattrai", 14)
    assert y("mattrai", 16) < y("mattrai", 20), "mí trên ở trên mí dưới"
    assert x("mattrai", 18) < x("matphai", 22) < x("matphai", 26) and y("matphai", 24) < y("matphai", 28)
    # 1/4, 1/2, 3/4 chiều rộng mắt
    w = x("mattrai", 18) - x("mattrai", 14)
    for i, t in ((15, 0.25), (16, 0.5), (17, 0.75), (19, 0.75), (20, 0.5), (21, 0.25)):
        assert abs((x("mattrai", i) - x("mattrai", 14)) / w - t) < 0.02
    # Môi ngoài: 30 và 36 là hai khoé; bờ trên theo tỉ lệ 0,18 / 0,36 / 0,50 / 0,66 / 0,84
    w = x("moingoai", 36) - x("moingoai", 30)
    for i, t in ((31, 0.18), (32, 0.36), (33, 0.5), (34, 0.66), (35, 0.84)):
        assert abs((x("moingoai", i) - x("moingoai", 30)) / w - t) < 0.02
    assert x("moingoai", 37) > x("moingoai", 39) > x("moingoai", 41) and y("moingoai", 33) < y("moingoai", 39)
    # Môi trong nằm trong môi ngoài; 42 và 46 là hai khoé, 44 và 48 là mốc giữa
    assert x("moingoai", 30) < x("moitrong", 42) < x("moitrong", 44) < x("moitrong", 46) < x("moingoai", 36)
    assert y("moingoai", 33) < y("moitrong", 44) <= y("moitrong", 48) < y("moingoai", 39)
    # QA không thấy lỗi nào trên chính đề xuất này (chiều kim đồng hồ, không bắt chéo, trái/phải, môi trong/ngoài)
    shapes = [to_shape(f[name], i) for i, name in enumerate(FACE_NAMES)]
    labels = driver_labels()
    report = qa.run_qa(profile, cfgs, labels, shapes, W, H)
    assert not report["findings"], report["findings"]


class TurnedFace(MockFace):
    def detect(self, image_rgb):
        lm = super().detect(image_rgb)[0]
        lm[1, 0] = lm[33, 0] + 4   # chóp mũi lệch hẳn về phía trái ảnh: nửa mặt bên trái ảnh ở xa camera
        return [lm]


def test_turned_face_far_side_is_occluded_not_squeezed(store):
    _, profile, cfgs = setup(store)
    f = by_label(run(profile, cfgs, face=TurnedFace()))
    for name in ("longmaytrai", "mattrai"):
        assert all(e["occluded"] and not e["outside"] for e in f[name]["elements"])
        assert any(fl["code"] == "ATTRIBUTE_CHECK" and "nghiêng" in fl["message"] for fl in f[name]["flags"])
    for name in ("longmayphai", "matphai", "songmui", "moingoai"):
        assert not any(e["occluded"] for e in f[name]["elements"]) and not f[name]["flags"]


class SlightlyTurnedFace(MockFace):
    def detect(self, image_rgb):
        lm = super().detect(image_rgb)[0]
        left, right = lm[33, 0], lm[263, 0]
        lm[1, 0] = left + (right - left) / 4.0   # mũi cách khoé trái 1 phần, khoé phải 3 phần: tỉ lệ 3
        return [lm]


def test_moderately_turned_face_is_flagged_but_stays_visible(store):
    _, profile, cfgs = setup(store)
    f = by_label(run(profile, cfgs, face=SlightlyTurnedFace()))
    for name in ("longmaytrai", "mattrai"):
        assert not any(e["occluded"] or e["outside"] for e in f[name]["elements"])
        assert [fl["code"] for fl in f[name]["flags"]] == ["UNCERTAIN_BOUNDARY"]
    assert not f["matphai"]["flags"] and not f["longmayphai"]["flags"]


def test_sunglasses_put_both_eyes_outside_and_hand_occludes_part_of_the_mouth(store):
    _, profile, cfgs = setup(store)
    plain = by_label(run(profile, cfgs))
    eyes = plain["mattrai"]["bbox"], plain["matphai"]["bbox"]
    glasses = rect(int(eyes[0][0]) - 6, int(eyes[0][1]) - 6, int(eyes[1][2]) + 6, int(eyes[1][3]) + 6)
    mouth = plain["moingoai"]["bbox"]
    hand = rect(int(mouth[0]) - 5, int(mouth[1]) - 5, int(mouth[0] + (mouth[2] - mouth[0]) * 0.3), int(mouth[3]) + 5)
    f = by_label(run(profile, cfgs, {"sunglasses": [(glasses, 0.9)], "hand": [(hand, 0.9)]}))
    # Kính râm che cả mắt: 0/8 điểm thấy, dưới ngưỡng 4/8 -> Outside cả skeleton, kèm cờ để người annotate quyết định
    for name in ("mattrai", "matphai"):
        assert all(e["outside"] and not e["occluded"] for e in f[name]["elements"])
        assert any(fl["code"] == "ATTRIBUTE_CHECK" and "dưới ngưỡng 4/8" in fl["message"] for fl in f[name]["flags"])
    # Tay che một góc miệng: còn thấy từ 6/12 -> đặt đủ, điểm bị che là Occluded
    lips = f["moingoai"]["elements"]
    assert 0 < sum(e["occluded"] for e in lips) <= 6 and not any(e["outside"] for e in lips)
    assert any("hand" in fl["message"] for fl in f["moingoai"]["flags"])
    assert not f["longmaytrai"]["flags"]
    # Gọng kính râm che phần trên sống mũi: vẫn còn thấy từ 2/4 điểm nên giữ skeleton, không Outside
    assert not any(e["outside"] for e in f["songmui"]["elements"]) and sum(e["occluded"] for e in f["songmui"]["elements"]) <= 2


class TwoFaces(MockFace):
    """Mặt người lái (to) và mặt hành khách (nhỏ), ở vị trí cố định trên ảnh gốc; `offset` = góc của vùng cắt."""

    offset = np.zeros(2)

    def detect(self, image_rgb):
        big = MockFace().detect(IMAGE)[0]
        small = (big - big.mean(0)) * 0.4 + [200.0, 500.0]
        return [small - self.offset, big - self.offset]


def test_only_the_largest_face_is_labelled(store):
    _, profile, cfgs = setup(store)
    result = run(profile, cfgs, face=TwoFaces())
    faces = [p for p in result["proposals"] if p["label_name"] in FACE_NAMES]
    assert len(faces) == 7
    assert all(any(fl["code"] == "UNCERTAIN_SCOPE" for fl in p["flags"]) for p in faces)
    big = MockFace().detect(IMAGE)[0]
    assert abs(by_label(result)["mattrai"]["bbox"][0] - big[33][0]) < 1


class PassengerCloser(MockFace):
    """Mặt hành khách to hơn (ngồi gần camera) ở góc trái; mặt người lái nhỏ hơn nằm trên vai người lái."""

    def detect(self, image_rgb):
        base = MockFace().detect(IMAGE)[0]
        passenger = (base - base.mean(0)) * 1.2 + [250.0, 300.0]
        driver = (base - base.mean(0)) * 0.5 + [900.0, 210.0]
        return [passenger, driver]


def test_face_follows_the_chosen_driver_not_just_the_largest_face(store):
    _, profile, cfgs = setup(store)
    result = run(profile, cfgs, {"person": [(rect(780, 150, 1020, 700), 0.9)]}, face=PassengerCloser())
    f = by_label(result)
    assert 780 < f["mattrai"]["bbox"][0] < 1020, "mặt được chọn phải là mặt nằm trên người lái"
    assert any(fl["code"] == "UNCERTAIN_SCOPE" for fl in f["mattrai"]["flags"])
    # Task chỉ có label mặt (không có person): không có người lái để dựa vào -> mặt lớn nhất, vẫn kèm cờ
    labels = driver_labels()[1:]
    _, profile, face_cfgs = setup(store, labels)
    f = by_label(run(profile, face_cfgs, face=PassengerCloser()))
    assert f["mattrai"]["bbox"][0] < 400 and any(fl["code"] == "UNCERTAIN_SCOPE" for fl in f["mattrai"]["flags"])


# ------------------------------------------------------------- vận hành ---
def test_missing_model_is_a_warning_not_a_crash(store):
    _, profile, cfgs = setup(store)

    def broken():
        raise ImportError("No module named 'mediapipe'")

    result = keypoints.propose(ScriptedEngine({"person": [(rect(500, 200, 620, 560), 0.9)]}), {}, IMAGE, profile, cfgs, [],
                               lazy(MockBodyPose()), LazyEngine(broken, "khuôn mặt (MediaPipe)"))
    assert [p["label_name"] for p in result["proposals"]] == ["person"]
    assert any("mediapipe" in w for w in result["warnings"])


def test_interactive_box_and_existing_duplicate(store):
    _, profile, cfgs = setup(store)
    person = cfgs[0]
    res = keypoints.interactive(IMAGE, profile, person, cfgs, [300, 100, 420, 500], lazy(MockBodyPose()), lazy(MockFace()))
    assert len(res["proposals"]) == 1
    existing = [to_shape(res["proposals"][0])]
    again = run(profile, cfgs, {"person": [(rect(300, 100, 420, 500), 0.9)]}, existing=existing)
    dup = by_label(again)["person"]
    assert not dup["checked"] and any(f["code"] == "DUPLICATE" for f in dup["flags"])
    # Vẽ hộp quanh mặt với label mattrai đang chọn: nhận đủ 7 skeleton của khuôn mặt trong hộp
    big = MockFace().detect(IMAGE)[0]
    box = [float(big[:, 0].min()), float(big[:, 1].min()), float(big[:, 0].max()), float(big[:, 1].max())]
    eye = next(c for c in cfgs if c["name"] == "mattrai")
    two = TwoFaces()
    two.offset = np.array([int(box[0] - 0.3 * (box[2] - box[0])), int(max(0, box[1] - 0.3 * (box[3] - box[1])))], float)
    res = keypoints.interactive(IMAGE, profile, eye, cfgs, box, lazy(MockBodyPose()), lazy(two))
    assert sorted(p["label_name"] for p in res["proposals"]) == sorted(FACE_NAMES)
    assert abs(by_label(res)["mattrai"]["bbox"][0] - big[33][0]) < 1.5, "lấy mặt nằm trong hộp, không phải mặt khác"


def test_qa_catches_guideline_violations(store):
    labels, profile, cfgs = setup(store)
    f = by_label(run(profile, cfgs, {"person": [(rect(500, 200, 620, 560), 0.9)]}))

    def swap(shape, a, b):
        e = shape["elements"]
        e[a]["points"], e[b]["points"] = e[b]["points"], e[a]["points"]

    person = to_shape(f["person"], 1)
    swap(person, 5, 6)                        # đổi vai R/L: điểm L sang bên phải ảnh
    person["elements"][0]["points"] = [0.0, 300.0]   # mũi kéo về mép trái "cho có"
    eye = to_shape(f["mattrai"], 2)
    coords = [e["points"] for e in eye["elements"]]
    for e, pts in zip(eye["elements"], [coords[0]] + coords[:0:-1]):
        e["points"] = pts                     # vẫn bắt đầu từ khoé trái nhưng đi mí dưới trước: ngược chiều kim đồng hồ
    inner = to_shape(f["moitrong"], 3)
    inner["elements"][2]["points"][1] -= 60   # điểm 44 vọt ra ngoài môi ngoài
    brow_l, brow_r = to_shape(f["longmaytrai"], 4), to_shape(f["longmayphai"], 5)
    for a, b in zip(brow_l["elements"], brow_r["elements"]):
        a["points"], b["points"] = b["points"], a["points"]   # nhầm lông mày trái/phải
    lips = to_shape(f["moingoai"], 6)
    lips["elements"][3]["outside"] = True     # một điểm Outside lẻ giữa contour
    second = to_shape(f["person"], 7)
    shapes = [person, eye, inner, brow_l, brow_r, lips, second]   # thiếu songmui, matphai; thừa một person
    report = qa.run_qa(profile, cfgs, labels, shapes, W, H)
    text = " | ".join(f"{x['code']}: {x['message']}" for x in report["findings"])
    for expected in ("LEFT_RIGHT: 'person': Điểm L của vai", "STATE: 'person': 1 điểm nằm sát mép",
                     "ngược chiều kim đồng hồ", "nằm ngoài 'moingoai'", "'longmaytrai' đang nằm bên phải 'longmayphai'",
                     "điểm Outside lẻ", "SCOPE: Có 2 skeleton 'person'", "Thiếu 2/7 skeleton mặt: songmui, matphai"):
        assert expected in text, f"thiếu: {expected}\n{text}"
    assert "GEOMETRY" not in text
    items = " ".join(c["item"] for c in report["checklist"])
    assert "±5px" in items and "02 lần" in items and "người lái" in items and "vật che" in items
    status = {c["item"][:20]: c["status"] for c in report["checklist"] if c["auto"]}
    assert set(status.values()) == {"fail"} or "warn" in status.values()


def test_archived_lab4_profile_still_works_when_restored(tmp_path):
    """README hướng dẫn khôi phục quy ước Lab 4 bằng cách chuyển profile cũ về profiles/: việc đó phải chạy được."""
    import shutil

    from backend.profiles import ProfileStore
    from conftest import ROOT

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    shutil.copy(ROOT / "profiles" / "archive" / "coco_pose_face_lab_v1.yaml", profiles)
    store = ProfileStore(profiles)
    labels = json.loads((Path(__file__).parent / "data" / "archive" / "lab4_skeleton_labels.json").read_text(encoding="utf-8"))
    profile = store.get("coco_pose_face_lab_v1", [l["name"] for l in labels], "frame", [l["type"] for l in labels])
    cfgs = resolve_labels(profile, labels)
    assert profile["id"] == "coco_pose_face_lab_v1" and all(c["supported"] and not c["missing"] for c in cfgs)
    result = run(profile, cfgs, {"person": [(rect(500, 200, 620, 560), 0.9)]})
    f = by_label(result)
    assert len(f["person"]["elements"]) == 17 and len(f["face"]["elements"]) == 13
    assert all(not e["outside"] for e in f["face"]["elements"])
    # Quy ước Lab 4: trái/phải theo NGƯỜI. Người quay mặt về camera -> left_* nằm bên phải ảnh
    assert el(f["person"], "left_shoulder")["x"] > el(f["person"], "right_shoulder")["x"]
    assert el(f["face"], "left_eye_outer")["x"] > el(f["face"], "right_eye_outer")["x"]
