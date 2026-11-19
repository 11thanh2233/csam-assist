"""Tự học từ cách người dùng xử lý đề xuất: ngưỡng, chỉnh hộp, cặp hay nhầm, bỏ sót."""
from backend.learning import Learner, apply_learned

from test_api import client, image_b64  # noqa: F401 - fixture dùng chung

PROFILE = "bdd_bbox_polygon_polyline_v1"


def car_job(learner, job_id=7, n=20, threshold=0.4):
    """Tool đề xuất n 'car'; điểm thấp (< 0.55) là sai và bị người dùng xoá; hộp đúng hay bị kéo dài cạnh dưới 10%."""
    events, shapes = [], []
    for i in range(n):
        score = 0.4 + 0.5 * i / (n - 1)
        box = [100.0 + i, 100.0, 200.0 + i, 200.0]
        events.append({"kind": "applied", "profile_id": PROFILE, "label_name": "car", "label_id": 1, "job_id": job_id,
                       "frame": i, "type": "rectangle", "score": score, "bbox": box, "cvat_id": 1000 + i,
                       "threshold": threshold, "existing_ids": [5000 + i]})
        if score < 0.55:
            continue   # người dùng xoá
        after = [box[0], box[1], box[2], box[3] + (10.0 if i % 2 == 0 else 0.0)]
        shapes.append({"id": 1000 + i, "label_id": 1, "frame": i, "type": "rectangle", "points": after})
        shapes.append({"id": 5000 + i, "label_id": 1, "frame": i, "type": "rectangle", "points": [600, 300, 700, 400]})  # có sẵn từ trước
    learner.record(events)
    return shapes


def test_learns_threshold_and_box_correction_from_user_edits(tmp_path):
    learner = Learner(tmp_path)
    shapes = car_job(learner)
    res = learner.review(7, shapes)
    row = res["summary"]["car"]
    assert row["deleted"] == 6 and row["kept"] + row["edited"] == 14 and row["missed"] == 0, "nhãn có sẵn từ trước không tính là bỏ sót"
    car = next(l for l in res["learned"]["labels"] if l["label"] == "car")
    assert 0.532 < car["threshold"] < 0.558 and "nâng ngưỡng" in car["threshold_reason"], "ngưỡng nằm giữa đề xuất sai cao nhất và đúng thấp nhất"
    assert car["box_adjust"][3] > 0.03 and abs(car["box_adjust"][0]) < 0.005, "cạnh dưới hay bị kéo xuống"
    # Đối chiếu lại nhiều lần trên cùng dữ liệu: kết quả không trôi
    again = learner.review(7, shapes)["learned"]["labels"][0]
    assert again["threshold"] == car["threshold"] and again["box_adjust"] == car["box_adjust"]

    # Áp vào cấu hình label; chỉnh tay ở tab Label luôn thắng
    cfgs = [{"name": "car", "label_id": 1, "threshold": 0.4, "shape": "rectangle"}]
    apply_learned(cfgs, learner.for_profile(PROFILE), {})
    assert cfgs[0]["threshold"] == car["threshold"] and cfgs[0]["box_adjust"] == car["box_adjust"]
    assert cfgs[0]["learned"]["threshold_from"] == 0.4
    cfgs = [{"name": "car", "label_id": 1, "threshold": 0.45, "shape": "rectangle"}]
    apply_learned(cfgs, learner.for_profile(PROFILE), {"1": {"threshold": 0.45}})
    assert cfgs[0]["threshold"] == 0.45 and "threshold" not in cfgs[0]["learned"]
    # Profile khác không bị ảnh hưởng
    assert learner.for_profile("generic") == {}

    # Tắt điều đã học cho label này, rồi quên hẳn
    learner.set_enabled(PROFILE, "car", False)
    assert learner.for_profile(PROFILE) == {}
    assert learner.forget(PROFILE, "car") == 20
    assert learner.summary()["labels"] == []


def test_missed_objects_lower_the_threshold_step_by_step(tmp_path):
    learner = Learner(tmp_path)
    events, shapes = [], []
    for i in range(10):   # 10 'truck' đúng hết, nhưng người dùng phải tự vẽ thêm 11 'truck' tool bỏ sót
        events.append({"kind": "applied", "profile_id": PROFILE, "label_name": "truck", "label_id": 2, "job_id": 9, "frame": i,
                       "type": "rectangle", "score": 0.8, "bbox": [10, 10, 50, 50], "cvat_id": 100 + i, "threshold": 0.5})
        shapes.append({"id": 100 + i, "label_id": 2, "frame": i, "type": "rectangle", "points": [10, 10, 50, 50]})
    for i in range(11):
        shapes.append({"id": 900 + i, "label_id": 2, "frame": i % 10, "type": "rectangle", "points": [300 + i, 300, 380 + i, 360]})
    learner.record(events)
    truck = learner.review(9, shapes)["learned"]["labels"][0]
    assert truck["counts"]["missed"] == 11 and truck["threshold"] == 0.45 and "hạ thử" in truck["threshold_reason"]
    # Đối chiếu lại mà không có kết quả mới: không hạ tiếp
    assert learner.review(9, shapes)["learned"]["labels"][0]["threshold"] == 0.45
    assert any("bỏ sót" in n["text"] for n in learner.summary()["notes"])


def test_relabel_and_rejected_become_notes_and_false_positives(tmp_path):
    learner = Learner(tmp_path)
    events, shapes = [], []
    for i in range(6):
        events.append({"kind": "applied", "profile_id": PROFILE, "label_name": "car", "label_id": 1, "job_id": 3, "frame": i,
                       "type": "rectangle", "score": 0.7, "bbox": [0, 0, 40, 40], "cvat_id": 10 + i, "threshold": 0.5})
        shapes.append({"id": 10 + i, "label_id": 2 if i < 4 else 1, "frame": i, "type": "rectangle", "points": [0, 0, 40, 40]})
    events.append({"kind": "applied", "profile_id": PROFILE, "label_name": "truck", "label_id": 2, "job_id": 3, "frame": 0,
                   "type": "rectangle", "score": 0.9, "bbox": [100, 100, 150, 150], "cvat_id": 50, "threshold": 0.5})
    shapes.append({"id": 50, "label_id": 2, "frame": 0, "type": "rectangle", "points": [100, 100, 150, 150]})
    events.append({"kind": "rejected", "profile_id": PROFILE, "label_name": "car", "label_id": 1, "job_id": 3, "frame": 0,
                   "type": "rectangle", "score": 0.6, "bbox": [200, 200, 240, 240], "threshold": 0.5})
    learner.record(events)
    res = learner.review(3, shapes)
    assert res["summary"]["car"] == {"kept": 2, "edited": 0, "relabeled": 4, "deleted": 0, "rejected": 1, "missed": 0}
    assert any(n["kind"] == "confusion" and "'car' hay bị bạn đổi thành 'truck'" in n["text"] for n in res["learned"]["notes"])
    # Rà cả job: lỗi trên nhãn do tool tạo chỉ là giả thuyết
    learner.tool_findings(PROFILE, {"car": {"GEOMETRY": 3, "BOUNDS": 1}})
    notes = learner.summary()["notes"]
    assert any(n["kind"] == "tool" and "Giả thuyết" in n["text"] for n in notes)
    learner.relearn()
    assert any(n["kind"] == "tool" for n in learner.summary()["notes"]), "học lại không xoá giả thuyết của tool"


def test_learned_settings_reach_proposals_via_api(client, image_b64):
    labels = [{"id": 1, "name": "car", "type": "rectangle"}]
    from backend.app import rt

    base = client.post("/propose", json={"profile_id": PROFILE, "labels": labels, "image_b64": image_b64}).json()["proposals"]
    before = [p for p in base if p["label_name"] == "car"][0]
    # Học: đủ hộp 'car' đúng mà người dùng đều kéo cạnh phải ra 20%
    events, shapes = [], []
    for i in range(12):
        events.append({"kind": "applied", "profile_id": PROFILE, "label_name": "car", "label_id": 1, "job_id": 77, "frame": i,
                       "type": "rectangle", "score": 0.9, "bbox": [0, 0, 100, 100], "cvat_id": 70 + i, "threshold": 0.5})
        shapes.append({"id": 70 + i, "label_id": 1, "frame": i, "type": "rectangle", "points": [0, 0, 120, 100]})
    assert client.post("/learn/events", json={"events": events}).json()["recorded"] == 12
    res = client.post("/learn/review", json={"job_id": 77, "shapes": shapes}).json()
    assert res["summary"]["car"]["edited"] == 12
    resolved = client.post("/resolve", json={"profile_id": PROFILE, "labels": labels}).json()
    car = resolved["labels"][0]
    assert car["learned"]["box_adjust"][2] == 0.2 and "kéo lại" in car["learned"]["box_reason"]
    after = [p for p in client.post("/propose", json={"profile_id": PROFILE, "labels": labels, "image_b64": image_b64}).json()["proposals"]
             if p["label_name"] == "car"][0]
    w = before["bbox"][2] - before["bbox"][0]
    assert abs((after["bbox"][2] - before["bbox"][2]) - 0.2 * w) < 1.5 and after["box_adjust"] == [0.0, 0.0, 0.2, 0.0]
    # Tắt "Dùng điều tool đã học": về như cũ
    off = client.post("/propose", json={"profile_id": PROFILE, "labels": labels, "image_b64": image_b64, "use_learned": False}).json()
    assert [p for p in off["proposals"] if p["label_name"] == "car"][0]["bbox"] == before["bbox"]
    assert client.post("/learn/forget", json={}).json()["labels"] == []
    assert rt.learner.for_profile(PROFILE) == {}
