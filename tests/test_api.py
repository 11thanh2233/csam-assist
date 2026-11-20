"""Test HTTP API bằng engine giả lập (không cần GPU, không cần SAM3)."""
import base64

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import BDD_NAMES, cvat_labels


@pytest.fixture(scope="module")
def client():
    from backend.app import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


@pytest.fixture(scope="module")
def image_b64():
    image = np.full((720, 1280, 3), 90, np.uint8)
    ok, buf = cv2.imencode(".jpg", image)
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def test_health_and_install_page(client):
    body = client.get("/health").json()
    assert body["ok"] and body["engine"] == "mock" and body["is_mock"]
    assert "Tampermonkey" in client.get("/").text
    script = client.get("/sam3-cvat-assist.user.js")
    assert script.status_code == 200 and "==UserScript==" in script.text


def test_resolve_propose_interactive_qa(client, image_b64):
    labels = cvat_labels(BDD_NAMES)
    resolved = client.post("/resolve", json={"profile_id": "auto", "labels": labels}).json()
    assert resolved["profile_id"] == "bdd_bbox_polygon_polyline_v1"
    assert len(resolved["labels"]) == len(BDD_NAMES)

    body = {"profile_id": "auto", "labels": labels, "image_b64": image_b64}
    result = client.post("/propose", json=body).json()
    assert result["width"] == 1280 and result["height"] == 720
    kinds = {p["type"] for p in result["proposals"]}
    assert {"rectangle", "polygon", "polyline"} <= kinds
    assert [p["pid"] for p in result["proposals"]] == list(range(1, len(result["proposals"]) + 1))

    inter = client.post(
        "/interactive", json=dict(body, label_id=3, box=[100, 100, 300, 260], mode="single")
    ).json()
    assert len(inter["proposals"]) == 1 and inter["proposals"][0]["type"] == "rectangle"
    x1, y1, x2, y2 = inter["proposals"][0]["points"]
    assert 95 <= x1 <= 110 and 290 <= x2 <= 305

    shapes = [dict(p, id=i, attributes=[]) for i, p in enumerate(result["proposals"])]
    report = client.post("/qa", json={"profile_id": "auto", "labels": labels, "shapes": shapes, "width": 1280, "height": 720}).json()
    assert report["shape_count"] == len(shapes) and report["checklist"]


def test_bad_image_rejected(client):
    r = client.post("/propose", json={"labels": [], "image_b64": "not-an-image"})
    assert r.status_code == 400


def test_skeleton_propose_via_api(client, image_b64):
    from test_keypoints import FACE_NAMES, driver_labels

    labels = driver_labels()
    resolved = client.post("/resolve", json={"profile_id": "auto", "labels": labels}).json()
    assert resolved["profile_id"] == "driver_pose17_face50_v1"
    result = client.post("/propose", json={"profile_id": "auto", "labels": labels, "image_b64": image_b64}).json()
    count = {p["label_name"]: len(p["elements"]) for p in result["proposals"]}
    assert count.get("person") == 17 and sum(count[n] for n in FACE_NAMES) == 50, result
    # Vẽ hộp với một label mặt đang chọn: endpoint trả đủ 7 skeleton mặt
    eye = next(l for l in labels if l["name"] == "mattrai")
    res = client.post("/interactive", json={"profile_id": "auto", "labels": labels, "image_b64": image_b64,
                                            "label_id": eye["id"], "box": [100, 20, 1180, 700], "mode": "single"}).json()
    assert sorted(p["label_name"] for p in res["proposals"]) == sorted(FACE_NAMES), res


def test_renamed_skeleton_label_resolves_via_api(client, image_b64):
    from test_keypoints import driver_labels

    labels = driver_labels()[:1]
    labels[0]["name"] = "person_vf17"
    for profile_id in ("auto", "driver_pose17_face50_v1"):
        res = client.post("/resolve", json={"profile_id": profile_id, "labels": labels}).json()
        assert res["profile_id"] == "driver_pose17_face50_v1" and res["mode"] == "keypoints"
        cfg = res["labels"][0]
        assert cfg["supported"] and cfg["enabled"] and cfg["entry"] == "person" and cfg["match_how"] == "similar"
        assert [s["name"] for s in res["skeletons"]][0] == "person" and len(res["skeletons"]) == 8
    result = client.post("/propose", json={"profile_id": "auto", "labels": labels, "image_b64": image_b64}).json()
    assert [(p["label_name"], len(p["elements"])) for p in result["proposals"]] == [("person_vf17", 17)], result
    # Người dùng chỉ tay một skeleton khác cho label này: backend làm theo
    forced = client.post("/resolve", json={"profile_id": "auto", "labels": labels,
                                           "overrides": {str(labels[0]["id"]): {"entry": "person"}}}).json()
    assert forced["labels"][0]["match_how"] == "manual"


def test_tracking_via_api(client, image_b64):
    labels = [{"id": 93, "name": "person", "type": "rectangle", "attributes": []}]
    resolved = client.post("/resolve", json={"profile_id": "auto", "labels": labels}).json()
    assert resolved["track"]["profile_id"] == "mot_person_track_v1"
    frames = [{"frame": f, "image_b64": image_b64} for f in range(12)]
    det = client.post("/track/detect", json={"labels": labels, "frames": frames}).json()
    assert len(det["frames"]) == 12 and all(len(f["dets"]) == 1 for f in det["frames"])
    built = client.post("/track/build", json={"labels": labels, "frames": det["frames"], "width": 1280,
                                              "height": 720, "start": 0, "stop": 20}).json()
    assert len(built["tracks"]) == 1
    tr = built["tracks"][0]
    assert tr["shapes"][0]["frame"] == 0 and tr["shapes"][-1] == {**tr["shapes"][-1], "frame": 12, "outside": True}


def test_guideline_flow_via_api(client, image_b64):
    """Bắt đầu -> quét -> soạn nháp -> trả lời -> trang duyệt -> duyệt -> dùng làm profile."""
    from backend.config import settings

    labels = [{"id": 1, "name": "car", "type": "rectangle", "attributes": []},
              {"id": 2, "name": "lane/crosswalk", "type": "polyline", "attributes": []}]
    started = client.post("/guideline/start", json={"labels": labels, "job": {"id": 5, "mode": "annotation"},
                                                    "purpose": "Phát hiện xe cho camera hành trình", "extra_terms": ["tuk tuk"]}).json()
    run_id = started["run_id"]
    assert started["llm"]["provider"] == "none" and started["concepts"] > 40
    for start in (0, 5):
        frames = [{"frame": f, "image_b64": image_b64} for f in range(start, start + 5)]
        progress = client.post("/guideline/scan", json={"run_id": run_id, "frames": frames}).json()
    assert progress["scanned"] == 10 and progress["found"] > 3
    view = client.post("/guideline/draft", json={"run_id": run_id}).json()
    assert view["status"] == "drafted" and view["summary"]["images"] == 10
    assert "Phát hiện xe cho camera hành trình" in view["markdown"] and view["questions"]
    scope = next(q for q in view["questions"] if q["key"].startswith("scope:"))
    view = client.post("/guideline/answer", json={"run_id": run_id, "answers": {scope["key"]: "add"}}).json()
    assert next(q for q in view["questions"] if q["key"] == scope["key"])["answer"] == "add"

    page = client.get(f"/guideline/run/{run_id}")
    assert page.status_code == 200 and "Duyệt và lưu" in page.text
    assert client.get(f"/guideline/run/{run_id}/data").json()["run_id"] == run_id
    assert client.get(f"/guideline/run/{run_id}/image/frame_0.jpg").headers["content-type"] == "image/jpeg"
    assert client.get(f"/guideline/run/{run_id}/image/..%2Frun.json").status_code == 404
    assert client.get("/guideline/run/khong-co/data").status_code == 404

    approved = client.post("/guideline/approve", json={"run_id": run_id, "markdown": view["markdown"] + "\n- Xe đồ chơi không gán.\n"}).json()
    assert any("Xe đồ chơi" in l["text"] for l in approved["learned"]) and any(l.get("answer") == "add" for l in approved["learned"])
    lessons = client.get("/guideline/lessons").json()["lessons"]
    toy = next(l for l in lessons if "Xe đồ chơi" in l["text"])
    off = client.post(f"/guideline/lessons/{toy['id']}/status", json={"status": "disabled"}).json()
    assert off["lesson"]["status"] == "disabled"
    assert client.post("/guideline/feedback", json={"text": "ngắn"}).status_code == 400

    plan = client.post("/guideline/labels", json={"run_id": run_id}).json()
    assert scope["key"][6:] in [l["name"] for l in plan["missing"]] and plan["view"] is None
    synced = client.post("/guideline/labels", json={"run_id": run_id, "labels": labels + [
        {"id": 50 + i, "name": l["name"], "type": l["type"], "attributes": []} for i, l in enumerate(plan["missing"])]}).json()
    assert synced["missing"] == [] and synced["view"]["label_plan"]["missing"] == []

    used = client.post("/guideline/use_profile", json={"run_id": run_id}).json()
    try:
        ids = [p["id"] for p in client.get("/profiles").json()["profiles"]]
        assert used["profile_id"] in ids
        resolved = client.post("/resolve", json={"profile_id": used["profile_id"], "labels": labels}).json()
        assert resolved["profile_id"] == used["profile_id"]
        assert next(c for c in resolved["labels"] if c["name"] == "lane/crosswalk")["polyline_mode"] == "long_edges"
    finally:
        (settings.profiles_dir / f"{used['profile_id']}.yaml").unlink()
        client.post("/profiles/reload")


def test_profile_hide_show_delete(client):
    """Tab Label: ẩn, hiện lại, xoá profile. Test chạy trên thư mục profile tạm (conftest)."""
    import os
    from pathlib import Path

    folder = Path(os.environ["SAM3_PROFILES_DIR"])
    (folder / "extra_demo.yaml").write_text("id: extra_demo\ntitle: Demo\nmode: instance\nlabels:\n  - {name: cat, prompts: [cat]}\n", encoding="utf-8")
    client.post("/profiles/reload")
    listed = client.get("/profiles/manage").json()
    demo = next(p for p in listed["active"] if p["id"] == "extra_demo")
    assert demo["file"] == "extra_demo.yaml" and not demo["locked"]
    assert next(p for p in listed["active"] if p["id"] == "generic")["locked"]
    assert any(h["id"] == "coco_pose_face_lab_v1" and "driver_pose17_face50_v1" in h["note"] for h in listed["hidden"])

    res = client.post("/profiles/extra_demo/hide").json()
    assert res["file"] == "extra_demo.yaml" and "extra_demo" not in [p["id"] for p in res["profiles"]]
    assert (folder / "archive" / "extra_demo.yaml").exists() and not (folder / "extra_demo.yaml").exists()
    res = client.post("/profiles/hidden/extra_demo.yaml/show").json()
    assert "extra_demo" in [p["id"] for p in res["profiles"]] and (folder / "extra_demo.yaml").exists()

    # Ẩn hai lần file cùng tên: giữ cả hai bản
    client.post("/profiles/extra_demo/hide")
    (folder / "extra_demo.yaml").write_text("id: extra_demo\ntitle: Demo 2\nmode: instance\nlabels: []\n", encoding="utf-8")
    client.post("/profiles/reload")
    assert client.post("/profiles/extra_demo/hide").json()["file"] == "extra_demo_2.yaml"
    assert {p.name for p in (folder / "archive").glob("extra_demo*")} == {"extra_demo.yaml", "extra_demo_2.yaml"}
    # Hiện lại khi profiles/ đã có file cùng tên: từ chối, không ghi đè
    (folder / "extra_demo.yaml").write_text("id: extra_demo\ntitle: Demo 3\nmode: instance\nlabels: []\n", encoding="utf-8")
    r = client.post("/profiles/hidden/extra_demo.yaml/show")
    assert r.status_code == 400 and "đã có file extra_demo.yaml" in r.json()["detail"]
    # Xoá hẳn: bản đang dùng và các bản đang ẩn
    client.post("/profiles/reload")
    assert client.delete("/profiles/extra_demo").status_code == 200 and not (folder / "extra_demo.yaml").exists()
    for name in ("extra_demo.yaml", "extra_demo_2.yaml"):
        assert client.delete(f"/profiles/hidden/{name}").status_code == 200
    assert not list((folder / "archive").glob("extra_demo*"))
    # Bảo vệ: profile dự phòng, tên file lạ, đường dẫn ra ngoài thư mục
    assert client.post("/profiles/generic/hide").status_code == 400
    assert client.delete("/profiles/generic").status_code == 400
    assert client.delete("/profiles/khong_co").status_code == 404
    assert client.delete("/profiles/hidden/README.md").status_code == 404
    assert client.post("/profiles/hidden/..%2Fbdd_bbox_polygon_polyline_v1.yaml/show").status_code == 404
    assert (folder / "bdd_bbox_polygon_polyline_v1.yaml").exists()


def test_backend_is_not_reachable_from_other_websites(client):
    """Backend chạy trên máy người dùng: một trang web lạ đang mở trong trình duyệt không được gọi nó."""
    # Không có CORS: trình duyệt không cho trang khác đọc kết quả (không có Access-Control-Allow-Origin)
    r = client.get("/profiles", headers={"Origin": "https://trang-la.example"})
    assert r.status_code == 200 and "access-control-allow-origin" not in {k.lower() for k in r.headers}
    pre = client.options("/profiles/generic/hide", headers={"Origin": "https://trang-la.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in {k.lower() for k in pre.headers}
    # DNS rebinding: tên miền lạ trỏ về 127.0.0.1 -> Host lạ bị từ chối
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app, base_url="http://evil.example:8765") as other:
        r = other.get("/health")
        assert r.status_code == 400 and "không được phép" in r.text
    with TestClient(app, base_url="http://localhost:8765") as local:
        assert local.get("/health").status_code == 200


def test_job_wide_qa(client):
    labels = [{"id": 1, "name": "car", "type": "rectangle"}, {"id": 2, "name": "truck", "type": "rectangle"}]
    shapes = [
        {"id": 1, "frame": 0, "label_id": 1, "type": "rectangle", "points": [10, 10, 60, 60], "attributes": []},
        {"id": 2, "frame": 2, "label_id": 1, "type": "rectangle", "points": [-30, 10, 60, 60], "attributes": []},   # vượt biên
        {"id": 3, "frame": 2, "label_id": 1, "type": "rectangle", "points": [-30, 10, 60, 60], "attributes": []},   # trùng
    ]
    # shape 2 do tool tạo (có trong nhật ký): lỗi trên nó thành giả thuyết của phần tự học
    client.post("/learn/events", json={"events": [{"kind": "applied", "profile_id": "bdd_bbox_polygon_polyline_v1", "label_name": "car",
                                                   "label_id": 1, "job_id": 31, "frame": 2, "type": "rectangle", "score": 0.8,
                                                   "bbox": [0, 10, 60, 60], "cvat_id": 2, "threshold": 0.5}]})
    res = client.post("/qa/job", json={"profile_id": "bdd_bbox_polygon_polyline_v1", "labels": labels, "job_id": 31, "shapes": shapes,
                                       "frames": [0, 1, 2, 3], "sizes": {"2": [1280, 720]}, "width": 1280, "height": 720}).json()
    assert res["checked_frames"] == 2 and [f["frame"] for f in res["frames"]] == [2]
    codes = {f["code"] for f in res["frames"][0]["findings"]}
    assert {"GEOMETRY", "DUPLICATE"} <= codes, codes
    assert any(f["by_tool"] for f in res["frames"][0]["findings"])
    assert res["totals"]["error"] >= 1 and res["checklist"]
    jf = {f["code"]: f for f in res["job_findings"]}
    assert jf["COMPLETENESS"]["frames"] == [1, 3] and "truck" in jf["TAXONOMY"]["message"]
    assert res["tool_errors"]["car"]
    client.post("/learn/forget", json={})


def test_find_similar_with_negative_examples_and_existing(client, image_b64):
    labels = [{"id": 1, "name": "car", "type": "rectangle"}]
    body = {"profile_id": "bdd_bbox_polygon_polyline_v1", "labels": labels, "image_b64": image_b64, "label_id": 1,
            "box": [500, 300, 600, 380], "mode": "similar"}
    found = client.post("/interactive", json=body).json()["proposals"]
    assert len(found) == 3, "vật mẫu và hai vật tương tự (engine giả lập)"
    left = min(found, key=lambda p: p["bbox"][0])
    # Hộp mẫu âm: vật bên trái là nhận nhầm -> bị loại
    again = client.post("/interactive", json=dict(body, negatives=[left["bbox"]])).json()["proposals"]
    assert len(again) == 2 and all(p["bbox"][0] > left["bbox"][0] + 10 for p in again)
    # Nhãn đã có trên frame: đề xuất trùng bị bỏ tick
    existing = [{"id": 9, "type": "rectangle", "label_id": 1, "frame": 0, "points": left["bbox"]}]
    dup = client.post("/interactive", json=dict(body, existing=existing)).json()["proposals"]
    marked = [p for p in dup if any(f["code"] == "DUPLICATE" for f in p["flags"])]
    assert len(marked) == 1 and not marked[0]["checked"]
    # Chế độ một vật: bỏ qua hộp mẫu âm, chỉ lấy vật trong hộp
    single = client.post("/interactive", json=dict(body, mode="single", negatives=[[0, 0, 10, 10]])).json()["proposals"]
    assert len(single) == 1 and abs(single[0]["bbox"][0] - 500) < 5


def test_selftest_runs_model_on_sample_image(client):
    """0.9.1: kiểm tra nhanh sau khi cài chạy model thật một lượt trên ảnh mẫu và báo thời gian."""
    r = client.post("/selftest").json()
    assert r["ok"] is True and r["engine"] == "mock" and r["is_mock"] is True
    assert isinstance(r["found"], int) and r["seconds"] >= 0
