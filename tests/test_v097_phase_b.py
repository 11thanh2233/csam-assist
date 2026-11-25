"""0.9.7 Giai đoạn B: camera_rig (3 tầng hiệu chỉnh), components ONNX, lidar_vision."""
import math

import numpy as np

import backend.camera_rig as CR
import backend.lidar_vision as LV


# --------------------------------------------------------- camera_rig ---
def test_preset_for_nuscenes_six_cameras():
    names = ["CAM_FRONT.jpg", "CAM_FRONT_LEFT.jpg", "CAM_FRONT_RIGHT.jpg", "CAM_BACK.jpg", "CAM_BACK_LEFT.jpg", "CAM_BACK_RIGHT.jpg"]
    assert CR.preset_for(names) == "nuscenes"


def test_preset_for_unknown_names_returns_none():
    assert CR.preset_for(["cam_a.png", "cam_b.png"]) is None


def test_from_meta_handles_common_shapes():
    a = CR.from_meta({"cameras": {"CAM_FRONT": {"yaw_deg": 0, "fov_deg": 70}}})
    assert a["CAM_FRONT"]["yaw_deg"] == 0 and a["CAM_FRONT"]["fov_deg"] == 70
    b = CR.from_meta({"camera_calibration": [{"name": "CAM_BACK", "yaw_deg": 180}]})
    assert b["CAM_BACK"]["yaw_deg"] == 180
    assert b["CAM_BACK"]["fov_deg"] is None, "thiếu góc nhìn: build_rig sẽ bù bằng preset/tên camera"
    c = CR.from_meta({"CAM_FRONT": {"yaw": 12}})
    assert c["CAM_FRONT"]["yaw_deg"] == 12


def test_from_meta_fov_from_intrinsics():
    # fx = 1000 px, ảnh rộng 1000 px -> fov = 2*atan(0.5) ≈ 53.13°
    out = CR.from_meta({"CAM_FRONT": {"intrinsics": {"fx": 1000}, "width": 1000}})
    assert abs(out["CAM_FRONT"]["fov_deg"] - math.degrees(2 * math.atan(0.5))) < 0.1


def test_build_rig_tier1_calibration_wins():
    names = ["CAM_FRONT.jpg", "CAM_BACK.jpg"]
    calib = {"CAM_FRONT.jpg": {"yaw_deg": 10, "fov_deg": 60}, "CAM_BACK.jpg": {"yaw_deg": 190, "fov_deg": 90}}
    rig = CR.build_rig(names, calib)
    assert rig["source"] == "calibration"
    assert {c["name"] for c in rig["cameras"]} == set(names)


def test_build_rig_tier2_preset_when_no_calib():
    names = ["CAM_FRONT.jpg", "CAM_FRONT_LEFT.jpg", "CAM_FRONT_RIGHT.jpg", "CAM_BACK.jpg", "CAM_BACK_LEFT.jpg", "CAM_BACK_RIGHT.jpg"]
    rig = CR.build_rig(names, None)
    assert rig["source"] == "preset" and rig["preset"] == "nuscenes"
    front = next(c for c in rig["cameras"] if "FRONT." in c["name"].replace("_", "_"))
    assert front["yaw"] == 0.0


def test_build_rig_falls_back_to_name_guess():
    rig = CR.build_rig(["weird_name.png"], None)
    assert rig["cameras"][0]["yaw"] == 0.0 and rig["cameras"][0]["fov"] == 90.0
    assert rig["notes"], "camera lạ: phải có ghi chú hướng dẫn căn chỉnh từ box mẫu"


def test_project_matches_lidar_assist_semantics():
    cams = [{"name": "front", "yaw": 0.0, "fov": 70.0}]
    p = CR.project([10, 0, 1.0], [4, 2, 1.5], cams, 0.0)
    assert p and p["camera"] == "front" and abs(p["u"] - 0.5) < 0.01
    # phía sau camera: không thấy
    assert CR.project([-10, 0, 1.0], [4, 2, 1.5], cams, 0.0) is None


def test_calibrate_requires_min_hits():
    cams = [{"name": "front", "yaw": 0.0, "fov": 70.0, "source": "preset"}]
    objs = [{"center": [10, 0, 1.0], "size": [4, 2, 1.5], "prompt": "car"}]

    def empty(cam, prompt):
        return []

    res = CR.calibrate([], objs, empty, base=cams)
    assert res["source"] == "none" and res["hits"] == 0


def test_calibrate_finds_offset():
    cams = [{"name": "front", "yaw": 0.0, "fov": 70.0, "source": "preset"}]
    true_off = 15.0
    objs = [{"center": [10, -2, 1.0], "size": [4, 2, 1.5], "prompt": "car"},       # az ≈ -11.3°
            {"center": [8, 6, 1.0], "size": [4, 2, 1.5], "prompt": "car"},         # az ≈ 36.9°
            {"center": [12, 2, 1.0], "size": [4, 2, 1.5], "prompt": "car"}]        # az ≈ 9.5°

    def search(cam, prompt):
        # trả một "vật" đúng tại góc phương vị của cuboid khi đã cộng offset thật
        hits = []
        for o in objs:
            az = math.degrees(math.atan2(o["center"][1], o["center"][0])) + true_off
            p = CR.project(o["center"], o["size"], cams, true_off)
            if p:
                hits.append((p["u"] - p["half"], p["u"] + p["half"], 0.9))
        return hits

    res = CR.calibrate([], objs, search, base=cams)
    assert res["source"] == "fitted" and res["hits"] == 3, res


def test_to_panel_roundtrip():
    rig = CR.build_rig(["CAM_FRONT.jpg"], None)
    panel = CR.to_panel(rig)
    assert "CAM_FRONT.jpg" in panel and "yaw_deg" in panel["CAM_FRONT.jpg"]


# --------------------------------------------------------- components ---
def test_onnx_models_table_and_license_warning():
    from desktop import components as C

    t = C.onnx_models("onnx3d")
    assert t["choices"] and t["default"] in t["choices"]
    assert all(c.get("license") for c in t["choices"].values())
    assert C.license_warning("onnx3d", t["default"]) == ""       # giấy phép rõ ràng: không cảnh báo


def test_components_status_includes_onnx_kinds():
    from desktop import components as C

    comp = C.COMPONENTS
    assert "onnx2d" in comp and "onnx3d" in comp
    assert not comp["onnx2d"]["required"]


def test_coco_for_3d_maps_classes():
    from desktop.components import COCO_FOR_3D

    assert COCO_FOR_3D["car"] == 2 and COCO_FOR_3D["pedestrian"] == 0


# -------------------------------------------------------- lidar_vision ---
def _img(w=320, h=240):
    return np.zeros((h, w, 3), np.uint8)


def test_mock_vision_unavailable_paths():
    v = LV.MockVision()
    res = LV.verify(v, [("CAM_FRONT", _img())],
                    [{"id": 1, "center": [10, 0, 1], "size": [4, 2, 1.5], "cls": "car"}],
                    {"cameras": [{"name": "CAM_FRONT", "yaw": 0.0, "fov": 70.0}], "source": "preset"})
    assert res["results"][1]["verdict"] == "none"


def test_verify_confirmed_when_box_matches():
    # hộp 2D che đúng khu chiếu: cuboid tại az=0 -> u=0.5, ảnh 320 => x 128..192 (nửa rộng ~64px)
    box = [110, 80, 210, 200]
    v = LV.MockVision([{"box": box, "score": 0.9, "coco_id": 2}])
    res = LV.verify(v, [("CAM_FRONT", _img())],
                    [{"id": 1, "center": [10, 0, 1], "size": [4, 2, 1.5], "cls": "car"}],
                    {"cameras": [{"name": "CAM_FRONT", "yaw": 0.0, "fov": 70.0}], "source": "preset"})
    assert res["results"][1]["verdict"] == "confirmed", res


def test_verify_unknown_when_other_class():
    box = [110, 80, 210, 200]
    v = LV.MockVision([{"box": box, "score": 0.9, "coco_id": 7}])            # truck thay car
    res = LV.verify(v, [("CAM_FRONT", _img())],
                    [{"id": 1, "center": [10, 0, 1], "size": [4, 2, 1.5], "cls": "car"}],
                    {"cameras": [{"name": "CAM_FRONT", "yaw": 0.0, "fov": 70.0}], "source": "preset"})
    assert res["results"][1]["verdict"] == "unknown"


def test_apply_low_confidence_for_none():
    objs = [{"id": 1, "confidence": 0.8, "checked": True}]
    changed = LV.apply({"results": {1: {"verdict": "none", "camera": "CAM_FRONT", "iou": 0.0, "score": 0.0}}}, objs)
    assert changed == 1 and objs[0]["checked"] is False and objs[0]["confidence"] < 0.4
    assert objs[0]["flags"] and objs[0]["flags"][0]["code"] == "UNCERTAIN_CLASS"


def test_apply_boosts_confirmed():
    objs = [{"id": 1, "confidence": 0.6}]
    LV.apply({"results": {1: {"verdict": "confirmed", "camera": "c", "iou": 0.8, "score": 0.9}}}, objs)
    assert objs[0]["confidence"] == 0.8


def test_frustum_split_separates_two_directions():
    cam = {"name": "front", "yaw": 0.0, "fov": 70.0}
    # hộp [140..180] trên ảnh rộng 320 -> u 0.4375..0.5625 -> phương vị -4.375°..+4.375°
    n = 200
    r = np.full(n, 10.0)

    def pts(az_deg):
        a = np.radians(az_deg)
        return np.c_[r * np.cos(a), r * np.sin(a), np.zeros(n)]

    cluster = np.vstack([pts(np.zeros(n)), pts(np.full(n, 40.0))])            # 0° vào hộp, 40° ra ngoài
    inside, rest = LV.frustum_split(cluster, cam, [140, 0, 180, 240], 320, 240)
    assert len(inside) == n and len(rest) == n, (len(inside), len(rest))


def test_split_wide_clusters_with_mock():
    """Cụm rộng dính hai hướng: hộp 2D che một hướng -> tách được."""
    cam = {"name": "CAM_FRONT", "yaw": 0.0, "fov": 70.0}
    rig = {"cameras": [cam], "source": "preset"}
    n = 150
    a_in, a_out = 0.0, 30.0
    r = 10.0

    def pts(az_deg):
        a = np.radians(az_deg)
        return np.c_[np.full(n, r * np.cos(a)), np.full(n, r * np.sin(a)), np.linspace(0.2, 1.5, n)]

    cluster = np.vstack([pts(a_in), pts(a_out)])
    box = [140, 0, 180, 240]                                    # chỉ che hướng 0°
    v = LV.MockVision([{"box": box, "score": 0.95, "coco_id": 2}])
    imgs = {"CAM_FRONT": _img()}
    out = LV.split_wide_clusters(v, imgs, [], [cluster], rig)
    assert len(out) >= 2, "cụm rộng phải được tách"
    sizes = sorted(len(g) for g in out)
    assert sizes[0] >= 15


def test_split_wide_keeps_narrow_clusters():
    cluster = np.c_[np.linspace(9, 11, 100), np.full(100, 0.1), np.zeros(100)]    # dài 2 m: không tách
    v = LV.MockVision()
    out = LV.split_wide_clusters(v, {}, [], [cluster], {"cameras": [], "source": "none"})
    assert len(out) == 1


def test_decode_detr_output():
    boxes = np.array([[[0.5, 0.5, 0.2, 0.4], [0.2, 0.2, 0.1, 0.1]]])               # cxcywh chuẩn hoá
    logits = np.array([[[0.1, 5.0, 0.2], [3.0, 0.1, 0.1]]])
    out = LV._decode([boxes, logits], 0.3)
    assert len(out) == 2
    assert out[0]["id"] == 1 and abs(out[0]["box"][0] - 0.4) < 1e-6
    assert out[1]["id"] == 0


def test_decode_garbage_returns_empty():
    assert LV._decode([], 0.3) == []
    assert LV._decode([np.zeros((1, 2))], 0.3) == []
