import cv2
import numpy as np

from backend import geometry as G
from backend import pipeline, qa
from backend.profiles import resolve_labels

from conftest import BDD_NAMES, H, SEG_NAMES, W, ScriptedEngine, cvat_labels, rect


def line_mask(x_bottom, x_top, thickness=8):
    canvas = np.zeros((H, W), np.uint8)
    cv2.line(canvas, (x_bottom, H - 5), (x_top, 380), 1, thickness)
    return canvas.astype(bool)


def road_mask():
    canvas = np.zeros((H, W), np.uint8)
    cv2.fillPoly(canvas, [np.array([[0, H - 1], [480, 360], [800, 360], [W - 1, H - 1]])], 1)
    return canvas.astype(bool)


def street_image():
    image = np.full((H, W, 3), 90, np.uint8)
    image[line_mask(420, 560)] = (240, 240, 240)   # vạch trắng bên trái làn xe
    image[line_mask(860, 720)] = (240, 240, 240)   # vạch trắng bên phải làn xe
    image[line_mask(150, 500)] = (235, 195, 40)    # vạch vàng phân chiều, xa bên trái
    return image


def by_label(result, name):
    return [p for p in result["proposals"] if p["label_name"] == name]


def run_bdd(store, script, existing=()):
    labels = cvat_labels(BDD_NAMES)
    profile = store.get("auto", BDD_NAMES)
    assert profile["id"] == "bdd_bbox_polygon_polyline_v1"
    cfgs = resolve_labels(profile, labels)
    return pipeline.propose(ScriptedEngine(script), {}, street_image(), profile, cfgs, list(existing))


def test_objects_rider_truncated_and_class_conflict(store):
    script = {
        "person": [(rect(600, 300, 640, 400), 0.9), (rect(300, 250, 340, 330), 0.9)],
        "motorcycle": [(rect(290, 300, 350, 380), 0.85)],
        "car": [(rect(0, 400, 200, 520), 0.8)],          # chạm mép trái -> truncated
        "truck": [(rect(0, 400, 200, 520), 0.75)],        # cùng vị trí, điểm sát -> UNCERTAIN_CLASS
    }
    result = run_bdd(store, script)
    assert len(by_label(result, "pedestrian")) == 1
    riders = by_label(result, "rider")
    assert len(riders) == 1 and riders[0]["points"] == [300.0, 250.0, 340.0, 330.0]
    cars = by_label(result, "car")
    assert len(cars) == 1 and not by_label(result, "truck")
    assert cars[0]["attributes"] == {"truncated": "true"}
    assert any(f["code"] == "UNCERTAIN_CLASS" for f in cars[0]["flags"])
    assert by_label(result, "pedestrian")[0]["attributes"] == {"truncated": "false"}
    assert all(p["type"] == "rectangle" for p in result["proposals"])


def test_occluded_and_existing_duplicate(store):
    script = {"car": [(rect(400, 300, 600, 420), 0.9), (rect(500, 340, 760, 500), 0.9)]}
    existing = [{"type": "rectangle", "label_id": 3, "points": [500, 340, 760, 500]}]
    result = run_bdd(store, script, existing)
    cars = sorted(by_label(result, "car"), key=lambda p: p["bbox"][0])
    assert cars[0]["occluded"] and any(f["code"] == "ATTRIBUTE_CHECK" for f in cars[0]["flags"])
    assert not cars[1]["occluded"]
    assert not cars[1]["checked"] and any(f["code"] == "DUPLICATE" for f in cars[1]["flags"])


def test_lanes_and_drivable(store):
    script = {
        "white lane line": [(line_mask(420, 560), 0.9), (line_mask(860, 720), 0.9), (line_mask(150, 500), 0.8)],
        "road": [(road_mask(), 0.95)],
    }
    result = run_bdd(store, script)
    whites = by_label(result, "lane/single white")
    yellows = by_label(result, "lane/single yellow")
    assert len(whites) == 2
    # Vạch thứ ba được prompt là trắng nhưng pixel là vàng -> đổi class + gắn cờ
    assert len(yellows) == 1 and any(f["code"] == "UNCERTAIN_CLASS" for f in yellows[0]["flags"])
    for line in whites + yellows:
        assert line["type"] == "polyline" and len(line["points"]) >= 4
        assert line["points"][1] > line["points"][-1]  # hướng vẽ nhất quán: gần -> xa

    direct = by_label(result, "area/direct")
    alts = by_label(result, "area/alternative")
    assert len(direct) == 1 and direct[0]["type"] == "polygon"
    ego = G.rasterize_shape(direct[0], W, H)
    assert ego[H - 30, W // 2] and not ego[H - 30, 200] and not ego[H - 30, 1100]
    # Bên phải là làn cùng chiều; vùng giữa vạch trắng trái và vạch vàng cũng cùng chiều;
    # vùng ngoài cùng bên trái nằm sau vạch vàng -> bị loại.
    assert len(alts) == 2
    covered = np.zeros((H, W), bool)
    for alt in alts:
        covered |= G.rasterize_shape(alt, W, H)
    assert covered[H - 30, 1100] and covered[H - 30, 300] and not covered[H - 5, 20]
    assert any("ngược chiều" in w for w in result["warnings"])
    for shape in direct + alts:
        assert not G.polygon_self_intersects(np.array(shape["points"]).reshape(-1, 2))


def test_semantic_no_overlap_no_guessing(store):
    labels = cvat_labels(SEG_NAMES, with_truncated=False)
    profile = store.get("auto", SEG_NAMES)
    assert profile["id"] == "semseg_cityscapes19_v1"
    cfgs = resolve_labels(profile, labels)
    script = {
        "sky": [(rect(0, 0, W, 250), 0.9)],
        "building": [(rect(0, 200, W, 400), 0.9)],          # chồng lên sky 50px: building thắng
        "road": [(rect(0, 400, W, 700), 0.9)],              # 20 hàng cuối để trống -> không được tự lấp
        "car": [(rect(500, 450, 700, 560), 0.9)],
        "person": [(rect(300, 420, 330, 500), 0.9), (rect(900, 430, 930, 500), 0.9)],
        "bicycle": [(rect(890, 470, 950, 520), 0.9)],
    }
    result = pipeline.propose(ScriptedEngine(script), {}, np.zeros((H, W, 3), np.uint8), profile, cfgs, [])
    owner = np.zeros((H, W), np.int32)
    for p in result["proposals"]:
        assert p["type"] == "mask"
        mask = G.decode_cvat_mask(p["points"], W, H)
        assert not (owner[mask] != 0).any(), "hai class cùng chiếm một pixel"
        owner[mask] = p["label_id"]
    ids = {l["name"]: l["id"] for l in labels}
    assert owner[225, 100] == ids["building"] and owner[100, 100] == ids["sky"]
    assert owner[500, 600] == ids["car"] and owner[600, 100] == ids["road"]
    assert owner[450, 310] == ids["person"] and owner[450, 910] == ids["rider"]
    assert (owner[700:] == 0).all()
    assert any("chưa có class" in w for w in result["warnings"])

    shapes = [dict(p, id=i) for i, p in enumerate(result["proposals"])]
    report = qa.run_qa(profile, cfgs, labels, shapes, W, H)
    codes = {f["code"] for f in report["findings"]}
    assert "OVERLAP" not in codes and "UNLABELED" in codes


def test_qa_catches_rubric_violations(store):
    labels = cvat_labels(BDD_NAMES)
    profile = store.get("auto", BDD_NAMES)
    cfgs = resolve_labels(profile, labels)
    shapes = [
        {"id": 1, "type": "rectangle", "label_id": 3, "points": [0, 100, 200, 300], "attributes": []},
        {"id": 2, "type": "rectangle", "label_id": 3, "points": [1, 101, 200, 300], "attributes": []},
        {"id": 3, "type": "polygon", "label_id": 3, "points": [10, 10, 60, 10, 60, 60]},
        {"id": 4, "type": "polygon", "label_id": 11, "points": [0, 0, 100, 100, 100, 0, 0, 100]},
        {"id": 5, "type": "rectangle", "label_id": 4, "points": [1200, 600, 1300, 700], "attributes": []},
    ]
    report = qa.run_qa(profile, cfgs, labels, shapes, W, H)
    found = {(f["code"], f["shape_id"]) for f in report["findings"]}
    assert ("ATTRIBUTES", 1) in found      # chạm biên nhưng truncated=false
    assert ("DUPLICATE", 2) in found
    assert ("TAXONOMY", 3) in found        # car phải là rectangle
    assert ("GEOMETRY", 4) in found        # polygon tự cắt
    assert ("GEOMETRY", 5) in found        # vượt biên ảnh
    assert next(c for c in report["checklist"] if "trùng lặp" in c["item"])["status"] == "fail"


def test_unknown_labels_fall_back_to_generic(store):
    labels = cvat_labels(["forklift", "pallet"], kind="polygon")
    profile = store.get("auto", ["forklift", "pallet"])
    assert profile["id"] == "generic"
    cfgs = resolve_labels(profile, labels, {"2": {"prompts": ["wooden pallet"], "threshold": 0.3}})
    assert all(c["enabled"] and c["shape"] == "polygon" for c in cfgs)
    assert cfgs[0]["prompts"] == ["forklift"] and cfgs[1]["prompts"] == ["wooden pallet"]
    script = {"forklift": [(rect(100, 100, 300, 300), 0.9)], "wooden pallet": [(rect(500, 500, 600, 560), 0.35)]}
    result = pipeline.propose(ScriptedEngine(script), {}, np.zeros((H, W, 3), np.uint8), profile, cfgs, [])
    assert sorted(p["label_name"] for p in result["proposals"]) == ["forklift", "pallet"]
    assert all(p["type"] == "polygon" for p in result["proposals"])
