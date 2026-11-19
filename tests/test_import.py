"""Nạp guideline có sẵn (.docx/.md) vào tool: đọc file, rút label/shape/định nghĩa/checklist, sinh profile."""
import base64
import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient

from backend.guideline import importer as I

ATTACH = Path("/mnt/user-data/attachments/5ab6319d-fe04-572e-8b80-28851b5cf6be")
NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(blocks) -> bytes:
    """Tạo .docx tối giản bằng thư viện chuẩn. blocks: ("h1", text) | ("p", text) | ("li", text) | ("table", [[ô, ...], ...]).
    Ô có nhiều dòng: dùng list chuỗi (mỗi chuỗi một đoạn, như khi gõ Enter trong ô của Word)."""
    def para(text, style=None, bullet=False):
        props = ""
        if style or bullet:
            props = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + ("<w:numPr><w:numId w:val=\"1\"/></w:numPr>" if bullet else "") + "</w:pPr>"
        return f"<w:p>{props}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"

    body = []
    for kind, value in blocks:
        if kind == "h1":
            body.append(para(value, "Heading1"))
        elif kind == "p":
            body.append(para(value))
        elif kind == "li":
            body.append(para(value, bullet=True))
        elif kind == "table":
            rows = []
            for row in value:
                cells = "".join("<w:tc>" + "".join(para(x) for x in (c if isinstance(c, list) else [c])) + "</w:tc>" for c in row)
                rows.append(f"<w:tr>{cells}</w:tr>")
            body.append("<w:tbl>" + "".join(rows) + "</w:tbl>")
        elif kind == "sdt":   # bảng nằm trong content control
            body.append("<w:sdt><w:sdtContent>" + "".join(
                "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in r) + "</w:tr>" for r in value) + "</w:tbl>") + "</w:sdtContent></w:sdt>")
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document {NS}><w:body>{"".join(body)}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


# Dựng theo đúng khung của BBox_Polygon_Polyline_Annotation_Guideline_V1 (tài liệu trong project).
BBOX = [
    ("p", "ANNOTATION GUIDELINE"), ("p", "Bounding Box, Polygon & Polyline"), ("p", "Student Annotation Guideline • CVAT"),
    ("table", [["Dữ liệu", "Ảnh đường phố giao thông"], ["Công cụ", "CVAT"]]),
    ("h1", "Taxonomy và loại shape bắt buộc"),
    ("table", [["Nhóm nhãn", "CVAT shape", "Class áp dụng"],
               ["Object instance", "Rectangle / Bounding Box", "pedestrian, rider, car, truck, bus, train, motorcycle, bicycle, traffic light, traffic sign"],
               ["Drivable area", "Polygon", "area/direct, area/alternative"],
               ["Lane marking", "Polyline", "lane/crosswalk, lane/double white, lane/double yellow,lane/double other, lane/road curb, "
                                            "lane/single white, lane/single yellow, lane/single other"]]),
    ("table", [["Nhóm nhãn", "Sub-category", "Khái niệm / nguyên tắc annotation"],
               ["Object instance", "pedestrian", "Người đi bộ, bao gồm người đang đứng, đi bộ, chạy, ngồi."],
               ["", "car", "Bao gồm ô tô con, và xe van dùng để chở người."],
               ["Drivable area", "direct", "Vùng mặt đường mà xe đang di chuyển trực tiếp."],
               ["Lane marking", "road curb", "Đường biên bó vỉa."]]),
    ("h1", "Quy tắc khi class hoặc boundary không rõ"),
    ("table", [["Issue type", "Ví dụ"], ["UNCERTAIN_CLASS", "car/van vs truck"], ["UNCERTAIN_BOUNDARY", "không rõ extent"],
               ["LOW_VISIBILITY", "thiếu sáng"]]),
    ("h1", "Checklist chất lượng trước khi Submit"),
    ("table", [["✓", "Checklist"], ["☐", "Gán đúng class và shape theo taxonomy."], ["☐", "Đã check 02 lần toàn dữ liệu trước khi save."]]),
    ("sdt", [["Hạng mục", "Reviewer kiểm tra"], ["Taxonomy", "Đúng class"], ["Geometry Accuracy", "BBox sát"]]),
]


def test_docx_bbox_guideline_is_understood():
    md = I.read_document("1a2b3c4d-Road_Guideline_V2.docx", make_docx(BBOX))
    prompts = {"pedestrian": ["person"], "rider": []}
    a = I.analyze(md, "1a2b3c4d-Road_Guideline_V2.docx", lambda n: prompts.get(n))
    assert a["id"] == "road_guideline_v2" and a["title"] == "Bounding Box, Polygon & Polyline" and a["task"] == "instance"
    shapes = {l["name"]: l["shape"] for l in a["labels"]}
    assert len(shapes) == 20
    assert shapes["traffic sign"] == "rectangle" and shapes["area/alternative"] == "polygon" and shapes["lane/double other"] == "polyline"
    by = {l["name"]: l for l in a["labels"]}
    assert by["area/direct"]["definition"].startswith("Vùng mặt đường") and by["area/direct"]["aliases"] == ["direct"]
    assert by["lane/road curb"]["definition"] == "Đường biên bó vỉa."
    assert by["pedestrian"]["prompts"] == ["person"] and by["pedestrian"]["prompt_source"] == "profile có sẵn"
    assert by["rider"]["prompts"] == []                       # profile có sẵn cố ý để rỗng: giữ nguyên
    assert by["lane/double yellow"]["prompts"] == ["double yellow"] and "kiểm tra" in by["lane/double yellow"]["prompt_source"]
    assert by["lane/crosswalk"]["polyline_mode"] == "centerline"
    assert [x["code"] for x in a["issue_types"]] == ["UNCERTAIN_CLASS", "UNCERTAIN_BOUNDARY", "LOW_VISIBILITY"]
    assert a["checklist"] == ["Gán đúng class và shape theo taxonomy.", "Đã check 02 lần toàn dữ liệu trước khi save."]
    assert [c["name"] for c in a["reviewer_criteria"]] == ["Taxonomy", "Geometry Accuracy"], "bảng trong content control cũng phải đọc được"
    profile = I.build_profile(a)
    assert profile["id"] == "gl_road_guideline_v2" and profile["mode"] == "instance" and len(profile["labels"]) == 20
    assert profile["checklist"] == a["checklist"]


def test_vietnamese_label_names_need_an_english_prompt():
    md = I.read_document("g.md", "| Nhóm | CVAT shape | Class |\n|---|---|---|\n| Xe | Bounding box | xe máy, ô tô, truck |\n".encode())
    a = I.analyze(md, "Xe_Guideline.md", lambda n: None)
    by = {l["name"]: l for l in a["labels"]}
    assert by["xe máy"]["prompts"] == [] and by["truck"]["prompts"] == ["truck"]
    assert any("chưa có prompt tiếng Anh" in w and "xe máy" in w for w in a["warnings"])


@pytest.mark.skipif(not (ATTACH / "0b019990-Face_Landmark50_Annotation_Guideline_V1.docx").exists(), reason="cần file guideline thật")
def test_real_skeleton_guidelines():
    face = I.analyze(I.read_document("f.docx", (ATTACH / "0b019990-Face_Landmark50_Annotation_Guideline_V1.docx").read_bytes()),
                     "0b019990-Face_Landmark50_Annotation_Guideline_V1.docx", lambda n: None)
    assert face["title"] == "Facial Landmark-50" and face["task"] == "keypoints"
    assert [l["name"] for l in face["labels"]] == ["longmaytrai", "longmayphai", "songmui", "mattrai", "matphai", "moingoai", "moitrong"]
    assert all(l["definition"] for l in face["labels"]), "ô có 2 tên trên 2 dòng không được dính thành 'longmaytrailongmayphai'"
    assert I.build_profile(face) is None and any("skeleton" in w for w in face["warnings"])
    pose = I.analyze(I.read_document("p.docx", (ATTACH / "9243e8a4-HumanPose17_Annotation_Guideline_V1.docx").read_bytes()),
                     "9243e8a4-HumanPose17_Annotation_Guideline_V1.docx", lambda n: None)
    assert pose["title"] == "HumanPose-17 Body Landmarks" and pose["task"] == "keypoints"
    assert [(l["name"], l["shape"]) for l in pose["labels"]] == [("person", "skeleton")]   # "1 – Nose" là điểm, không phải label
    assert len(pose["issue_types"]) == 5 and len(pose["checklist"]) == 5 and len(pose["reviewer_criteria"]) == 5


def test_bad_files_are_rejected_with_a_reason():
    with pytest.raises(ValueError, match="docx"):
        I.read_document("x.docx", b"not a zip")
    with pytest.raises(ValueError, match="Chỉ nhận"):
        I.read_document("x.xlsx", b"abc")


@pytest.fixture(scope="module")
def client():
    from backend.app import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_import_flow_via_api(client):
    data = base64.b64encode(make_docx(BBOX)).decode()
    pre = client.post("/guideline/import/preview", json={"filename": "Road_Guideline_V2.docx", "data_b64": data}).json()
    a = pre["analysis"]
    assert a["id"] == "road_guideline_v2" and pre["profile_id"] == "gl_road_guideline_v2" and not pre["exists"]
    assert pre["overlap"] and "BBox" in pre["overlap"], "phải báo profile có sẵn đã khớp cùng bộ label"
    assert next(l for l in a["labels"] if l["name"] == "car")["prompts"] == ["car"]   # lấy prompt đã chỉnh của profile BDD
    # Người dùng sửa trước khi lưu: đổi prompt, đổi cách vẽ vạch, bỏ một label
    a["labels"] = [l for l in a["labels"] if l["name"] != "train"]
    for l in a["labels"]:
        if l["name"] == "lane/crosswalk":
            l["polyline_mode"] = "long_edges"
        if l["name"] == "lane/road curb":
            l["prompts"] = ["curb", "road curb"]
    saved = client.post("/guideline/import/save", json={"analysis": a}).json()
    assert saved["profile_id"] == "gl_road_guideline_v2" and saved["labels"] == 19 and len(saved["files"]) == 3
    profiles = {p["id"]: p for p in client.get("/profiles").json()["profiles"]}
    assert "gl_road_guideline_v2" in profiles
    labels = [{"id": i + 1, "name": l["name"], "type": l["shape"]} for i, l in enumerate(a["labels"])]
    res = client.post("/resolve", json={"profile_id": "gl_road_guideline_v2", "labels": labels}).json()
    cfg = {c["name"]: c for c in res["labels"]}
    assert all(c["matched"] for c in res["labels"]) and cfg["lane/road curb"]["prompts"] == ["curb", "road curb"]
    shapes = [{"id": 1, "type": "rectangle", "label_id": 1, "points": [10, 10, 50, 50], "frame": 0, "attributes": []}]
    qa = client.post("/qa", json={"profile_id": "gl_road_guideline_v2", "labels": labels, "shapes": shapes, "width": 1280, "height": 720}).json()
    assert "Đã check 02 lần toàn dữ liệu trước khi save." in [c["item"] for c in qa["checklist"]]
    # Định nghĩa của guideline nạp vào thắng định nghĩa mẫu cùng tên
    items = client.get("/guideline/imported").json()["items"]
    assert [(i["id"], i["profile_id"], i["labels"]) for i in items] == [("road_guideline_v2", "gl_road_guideline_v2", 19)]
    from backend.app import rt
    assert rt.guide.lib.definition("car") == "Bao gồm ô tô con, và xe van dùng để chở người."
    assert "road_guideline_v2" in {name for name, _ in rt.guide.lib.references()}
    # Thêm label còn thiếu vào task: chỉ những label task chưa có
    plan = client.post("/guideline/imported/road_guideline_v2/labels", json={"labels": labels[:5]}).json()
    assert len(plan["labels"]) == 19 and len(plan["missing"]) == 14
    assert {l["type"] for l in plan["missing"]} == {"rectangle", "polygon", "polyline"} and all(l["color"].startswith("#") for l in plan["missing"])
    # Lưu lần hai mà chưa đọc lại file: từ chối
    assert client.post("/guideline/import/save", json={"analysis": a}).status_code == 400
    # Gỡ: chuyển vào archive, profile biến mất
    removed = client.delete("/guideline/imported/road_guideline_v2").json()
    assert len(removed["moved"]) == 3
    assert "gl_road_guideline_v2" not in {p["id"] for p in client.get("/profiles").json()["profiles"]}
    assert client.delete("/guideline/imported/..%2F..%2Fetc").status_code in (400, 404)


@pytest.mark.skipif(not (ATTACH / "0b019990-Face_Landmark50_Annotation_Guideline_V1.docx").exists(), reason="cần file guideline thật")
def test_import_skeleton_guideline_gives_labels_but_no_profile(client):
    data = base64.b64encode((ATTACH / "0b019990-Face_Landmark50_Annotation_Guideline_V1.docx").read_bytes()).decode()
    pre = client.post("/guideline/import/preview", json={"filename": "Face_Landmark50_Annotation_Guideline_V1.docx", "data_b64": data}).json()
    assert pre["profile_id"] is None and pre["analysis"]["task"] == "keypoints"
    saved = client.post("/guideline/import/save", json={"analysis": pre["analysis"]}).json()
    assert saved["profile_id"] is None and len(saved["files"]) == 2
    plan = client.post(f"/guideline/imported/{saved['id']}/labels", json={"labels": [{"id": 1, "name": "mattrai_vf50", "type": "skeleton"}]}).json()
    assert [l["name"] for l in plan["missing"]] == ["longmaytrai", "longmayphai", "songmui", "matphai", "moingoai", "moitrong"]
    assert all(l["svg"] and len(l["sublabels"]) >= 4 for l in plan["missing"])
    client.delete(f"/guideline/imported/{saved['id']}")


# ------------------------------------------------------------ taxonomy.json -
CVAT_RAW = [  # JSON label ở tab Raw của task CVAT
    {"name": "car", "id": 11, "color": "#fa3253", "type": "rectangle",
     "attributes": [{"id": 5, "name": "occluded_flag", "mutable": True, "input_type": "checkbox", "default_value": "false", "values": ["false"]}]},
    {"name": "area/direct", "id": 12, "color": "#33ddff", "type": "polygon", "attributes": []},
    {"name": "lane/double yellow", "id": 13, "color": "#ffcc33", "type": "polyline", "attributes": []},
    {"name": "person_vf17", "id": 14, "color": "#ff6a4d", "type": "skeleton", "attributes": [],
     "sublabels": [{"name": str(i), "id": 100 + i, "type": "points", "attributes": [], "color": "#fff"} for i in range(1, 4)],
     "svg": '<circle r="0.75" cx="10" cy="10" data-type="element node" data-element-id="1" data-node-id="1" data-label-id="101" data-label-name="1"></circle>'},
]


def test_taxonomy_json_formats():
    import json

    labels = I.read_taxonomy_json(json.dumps(CVAT_RAW).encode())
    assert [(l["name"], l["shape"]) for l in labels] == [("car", "rectangle"), ("area/direct", "polygon"),
                                                         ("lane/double yellow", "polyline"), ("person_vf17", "skeleton")]
    car = labels[0]["cvat"]
    assert car["attributes"][0]["name"] == "occluded_flag" and "id" not in car["attributes"][0] and "id" not in car
    sk = labels[3]["cvat"]
    assert [s["name"] for s in sk["sublabels"]] == ["1", "2", "3"] and all("id" not in s for s in sk["sublabels"])
    assert "data-label-id" not in sk["svg"] and 'data-label-name="1"' in sk["svg"]

    # /api/labels: điểm skeleton là label con có parent_id
    api = {"count": 3, "results": [{"id": 1, "name": "hand", "type": "skeleton", "attributes": [], "parent_id": None},
                                   {"id": 2, "name": "thumb", "type": "points", "attributes": [], "parent_id": 1},
                                   {"id": 3, "name": "index", "type": "points", "attributes": [], "parent_id": 1}]}
    labels = I.read_taxonomy_json(json.dumps(api).encode())
    assert [(l["name"], l["shape"], [s["name"] for s in l["cvat"]["sublabels"]]) for l in labels] == [("hand", "skeleton", ["thumb", "index"])]

    # COCO
    coco = {"categories": [{"id": 1, "name": "person", "supercategory": "person", "keypoints": ["nose", "left_eye"], "skeleton": [[1, 2]]},
                           {"id": 2, "name": "dog", "supercategory": "animal"}]}
    labels = I.read_taxonomy_json(json.dumps(coco).encode())
    assert [(l["name"], l["shape"]) for l in labels] == [("person", "skeleton"), ("dog", "rectangle")]

    # Tự viết: nhóm theo shape, dict tên -> shape, danh sách chuỗi, kèm định nghĩa và prompt
    custom = {"groups": [{"shape": "bbox", "classes": ["truck", {"name": "xe nâng", "prompt": "forklift", "description": "Xe nâng hàng."}]},
                         {"shape": "polygon", "classes": ["walkway"]}]}
    labels = I.read_taxonomy_json(json.dumps(custom, ensure_ascii=False).encode())
    assert [(l["name"], l["shape"]) for l in labels] == [("truck", "rectangle"), ("xe nâng", "rectangle"), ("walkway", "polygon")]
    assert labels[1]["prompts"] == ["forklift"] and labels[1]["definition"] == "Xe nâng hàng."
    assert [(l["name"], l["shape"]) for l in I.read_taxonomy_json(b'{"car": "rectangle", "road": "mask"}')] == [("car", "rectangle"), ("road", "mask")]
    assert [l["name"] for l in I.read_taxonomy_json(b'["car", "bus"]')] == ["car", "bus"]
    with pytest.raises(ValueError, match="JSON"):
        I.read_taxonomy_json(b"{not json")
    with pytest.raises(ValueError, match="name"):
        I.read_taxonomy_json(b'{"version": 2}')


def test_taxonomy_json_via_api_keeps_cvat_attributes(client):
    import json

    data = base64.b64encode(json.dumps(CVAT_RAW[:3]).encode()).decode()
    pre = client.post("/guideline/import/preview", json={"filename": "taxonomy.json", "data_b64": data}).json()
    a = pre["analysis"]
    assert a["source"] == "taxonomy" and a["task"] == "instance" and len(a["labels"]) == 3 and pre["profile_id"] == "gl_taxonomy"
    saved = client.post("/guideline/import/save", json={"analysis": a}).json()
    plan = client.post("/guideline/imported/taxonomy/labels", json={"labels": []}).json()
    car = next(l for l in plan["missing"] if l["name"] == "car")
    assert car["color"] == "#fa3253" and car["attributes"][0]["name"] == "occluded_flag", "giữ attribute của file taxonomy"
    client.delete(f"/guideline/imported/{saved['id']}")
