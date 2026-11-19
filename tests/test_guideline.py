"""Soạn guideline nháp từ ảnh: khảo sát, câu hỏi cần chốt, văn bản, tự kiểm tra và vòng học từ người duyệt."""
import json
import re

import cv2
import numpy as np

from backend.guideline import draft as D
from backend.guideline.library import Library
from backend.guideline.llm import LLM
from backend.guideline.service import GuidelineService
from backend.profiles import ProfileStore, resolve_labels

from conftest import H, ROOT, W, ScriptedEngine, rect

LABELS = [{"id": 1, "name": "car", "type": "rectangle"}, {"id": 2, "name": "truck", "type": "rectangle"},
          {"id": 3, "name": "lane/double yellow", "type": "polyline"}, {"id": 4, "name": "lane/crosswalk", "type": "polyline"},
          {"id": 5, "name": "lane/single white", "type": "polyline"}]
KNOWN = {"car": ["car"], "truck": ["truck"], "lane/double yellow": ["double yellow line"],
         "lane/crosswalk": ["crosswalk"], "lane/single white": ["white lane line"]}


def line(x0, x1):
    canvas = np.zeros((H, W), np.uint8)
    cv2.line(canvas, (x0, H - 10), (x1, 380), 1, 7)
    return canvas.astype(bool)


def street_script():
    van = rect(700, 300, 880, 420)
    return {
        "car": [(rect(100, 300, 300, 420), 0.9), (rect(280, 320, 420, 430), 0.85), (van, 0.62)],
        "truck": [(van, 0.58)],                                  # cùng một vật khớp cả car lẫn truck
        "van": [(van, 0.8)],
        "person": [(rect(500, 330, 508, 350), 0.8), (rect(1000, 120, 1030, 190), 0.7)],  # một người rất nhỏ, một người trên billboard
        "billboard": [(rect(960, 80, 1100, 220), 0.9)],
        "double yellow line": [(line(400, 560), 0.9)],
        "tree": [(rect(0, 0, 400, 250), 0.9)],
    }


def run_survey(service, labels=LABELS, mode="annotation", frames=12, script=None, **kw):
    started = service.start(labels, {"id": 7, "mode": mode}, kw.get("purpose", ""), kw.get("extra", []),
                            kw.get("send_images", False), KNOWN, "bộ thử")
    engine = ScriptedEngine(script or street_script())
    image = np.full((H, W, 3), 120, np.uint8)
    for f in range(frames):
        service.scan(started["run_id"], engine, [(f * 3, image)])
    return started["run_id"], service.draft(started["run_id"])


def question(view, key):
    return next((q for q in view["questions"] if q["key"] == key), None)


def test_survey_finds_evidence_and_hard_cases(library):
    service = GuidelineService(library, None)
    run_id, view = run_survey(service)
    names = {c["name"]: c for c in view["concepts"]}
    assert names["car"]["instances"] == 36 and names["car"]["image_pct"] == 100.0
    assert names["person"]["source"] == "từ vựng" and names["lane/double yellow"]["kind"] == "line"
    assert "motorcycle" not in names                      # không thấy trong chặng khám phá -> không quét tiếp
    run = library.load_run(run_id)
    pairs = {(p["a"], p["b"]) for p in run["summary"]["pairs"]}
    assert ("car", "truck") in pairs
    codes = {c["code"] for c in view["cases"]}
    assert {"UNCERTAIN_CLASS", "UNCERTAIN_SCOPE", "LOW_VISIBILITY"} <= codes
    assert (library.runs_dir / run_id / "frame_0.jpg").exists() and (library.runs_dir / run_id / "case_0.jpg").exists()


def test_questions_carry_evidence_and_defaults_from_reference_guidelines(library):
    _, view = run_survey(GuidelineService(library, None))
    assert question(view, "task_type")["recommended"] == "detection"
    person = question(view, "scope:person")
    assert person["recommended"] == "out" and "100%" in person["text"] and person["examples"]
    assert question(view, "scope:van")["recommended"] == "merge:car"      # van trùng car ở 100% trường hợp
    pair = question(view, "pair:car|truck")
    assert pair["recommended"] == "rule" and "van chở hàng" in pair["reason"]
    assert question(view, "reflections")["recommended"] == "not_label"
    assert "billboard" in question(view, "reflections")["evidence"]
    # Bài học khởi đầu từ lần người duyệt sửa quy ước vạch kẻ
    assert question(view, "line_mode:lane/double yellow")["recommended"] == "edges"
    assert question(view, "line_mode:lane/crosswalk")["recommended"] == "long_edges"
    assert question(view, "line_mode:lane/single white")["recommended"] == "centerline"
    assert question(view, "scope:billboard") is None
    assert question(view, "scope:tree") is None          # task không có class vùng nền: không hỏi về cây, trời, đường


def test_draft_structure_and_self_check(library):
    _, view = run_survey(GuidelineService(library, None))
    md = view["markdown"]
    for heading in ("## 1. Phạm vi và dữ liệu", "## 2. Taxonomy và loại shape", "### Bounding box", "### Polyline",
                    "## 5. Các cặp class dễ nhầm", "## 6. Ca khó trong bộ ảnh này", "## 8. Checklist trước khi nộp",
                    "## Phụ lục A. Số liệu khảo sát", "## Phụ lục B. Câu hỏi còn mở"):
        assert heading in md, heading
    assert "Ô tô con và xe van dùng để chở người" in md               # định nghĩa lấy từ guideline mẫu
    assert "'person' xuất hiện trong ảnh nhưng nằm ngoài phạm vi" in md and "(đề xuất, chưa chốt)" in md
    assert "hai polyline theo hai mép ngoài" in md and "<!--r:bbox.tight-->" in md
    assert "bản nháp" in md.lower() and "chưa phải guideline chính thức" in md
    codes = {f["code"] for f in view["findings"]}
    assert "unseen_class" in codes and "scope_gap" in codes and "pair_open" not in codes
    assert view["llm"]["provider"] == "none"


def test_answers_change_the_guideline(library):
    service = GuidelineService(library, None)
    run_id, view = run_survey(service)
    view = service.answer(run_id, {"scope:person": "add", "pair:car|truck": "merge", "min_size": "none"})
    md = view["markdown"]
    assert "| person *(mới, cần tạo label)* | rectangle |" in md
    assert "**car / truck**: gộp thành một class." in md
    assert "'person' xuất hiện trong ảnh nhưng nằm ngoài phạm vi" not in md
    assert all(q["key"] != "scope:person" for q in view["questions"] if q["answer"] is None)


def test_learning_from_reviewer_edits_applies_next_time(library):
    service = GuidelineService(library, None)
    run_id, view = run_survey(service)
    view = service.answer(run_id, {"scope:person": "add"})
    edited = view["markdown"]
    edited = edited.replace("Box bao sát đối tượng theo 4 extreme point (trái, phải, trên, dưới), không quá rộng hoặc quá chặt, không vượt biên ảnh.",
                            "Box bao sát phần NHÌN THẤY của đối tượng, sai lệch tối đa 2px mỗi cạnh.")
    edited = "\n".join(l for l in edited.splitlines() if "<!--r:bbox.one-->" not in l)
    edited = edited.replace("### Polygon" if "### Polygon" in edited else "### Polyline",
                            "- Gương chiếu hậu tính là một phần của xe.\n\n" + ("### Polygon" if "### Polygon" in edited else "### Polyline"), 1)
    result = service.approve(run_id, edited)
    texts = " | ".join(l["text"] for l in result["learned"])
    assert "Người duyệt chốt: Thêm thành class mới" in texts and "sai lệch tối đa 2px" in texts
    assert "người duyệt bỏ" in texts and "Gương chiếu hậu" in texts
    assert (library.runs_dir / run_id / "guideline.md").exists()
    assert "<!--r:" not in (library.runs_dir / run_id / "guideline.md").read_text(encoding="utf-8")

    # Lần soạn sau cho một bộ cùng loại: không lặp lại những chỗ đã bị sửa
    _, second = run_survey(GuidelineService(Library(library.root), None))
    md = second["markdown"]
    person = question(second, "scope:person")
    assert person["recommended"] == "add" and "lần trước bạn đã chốt" in person["reason"]
    assert "sai lệch tối đa 2px mỗi cạnh. *(theo lần trước)*" in md
    assert "Mỗi object một box riêng" not in md.split("## Phụ lục")[0]
    assert "Gương chiếu hậu tính là một phần của xe. *(từ lần trước, xác nhận còn áp dụng)*" in md
    assert "## Phụ lục D. Bài học từ các lần trước đã áp dụng" in md and "Đã bỏ theo lần trước" in md

    # Bài học sai thì tắt được, và bản nháp quay về lời mặc định
    lib3 = Library(library.root)
    wrong = next(l for l in lib3.lessons.values() if l.get("rule") == "bbox.tight")
    lib3.set_status(wrong["id"], "disabled")
    _, third = run_survey(GuidelineService(Library(library.root), None))
    assert "sai lệch tối đa 2px" not in third["markdown"] and "4 extreme point" in third["markdown"]

    # Bài học của bài toán detection không tự áp sang tracking
    track_labels = [{"id": 1, "name": "car", "type": "rectangle"}]
    _, tracking = run_survey(GuidelineService(Library(library.root), None), labels=track_labels, mode="interpolation")
    assert tracking["task"] == "tracking" and "Gương chiếu hậu" not in tracking["markdown"]
    assert "toàn bộ object" in tracking["markdown"]       # tracking: box toàn thân theo guideline Lab 5


def test_tool_findings_stay_hypotheses(library):
    service = GuidelineService(library, None)
    run_id, view = run_survey(service)
    service.approve(run_id, view["markdown"])
    lib = Library(library.root)
    hypotheses = [l for l in lib.lessons.values() if l["status"] == "hypothesis"]
    assert hypotheses and all(l["source"] == "tool tự kiểm tra" for l in hypotheses)
    assert not set(l["id"] for l in hypotheses) & set(l["id"] for l in lib.recall(["detection"], confirmed_only=True))
    # Duyệt mà không sửa gì: không sinh bài học "người duyệt sửa"
    assert not [l for l in lib.lessons.values() if l["source"].startswith("người duyệt")]


def test_feedback_after_use_becomes_confirmed_lesson(library):
    service = GuidelineService(library, None)
    lesson = service.feedback("Reviewer trả bài: xe ba bánh chở hàng phải gán truck, không phải motorcycle.", ["detection"], None)
    assert lesson["status"] == "confirmed" and lesson["source"] == "phản hồi sau khi dùng"
    assert lesson["id"] in {l["id"] for l in Library(library.root).recall(["detection"])}


class FakeLLM(LLM):
    provider, model = "fake", "fake-1"

    def __init__(self):
        self.calls = []

    def complete(self, system, text, images=None, max_tokens=3000):
        self.calls.append({"system": system, "text": text, "images": len(images or [])})
        if "Liệt kê các loại vật thể" in text:
            return '```json\n{"concepts": [{"name": "traffic cone", "kind": "thing"}, {"name": "Xe Ba Gác!!", "kind": "thing"}, {"name": "rickshaw", "kind": "thing"}]}\n```'
        if "GUIDELINE:" in text:
            label = "car" if "đúng từng chữ" in system else "truck"
            return json.dumps([{"case": i, "label": label if i == 1 else "không gán", "rule": "mục 5", "missing": "van chở cả người lẫn hàng" if i == 1 else ""}
                               for i in range(1, 7)], ensure_ascii=False)
        return json.dumps({
            "definitions": {"barrier gate": "Thanh chắn nâng hạ ở lối ra vào; không gồm trụ đỡ.", "person": "Người thật trong cảnh."},
            "pairs": [{"a": "x", "b": "y", "rule": "z"}],
            "extra_rules": [{"section": "Bounding box", "text": "Xe đang mở cửa: box bao cả cánh cửa."}, "rác"],
        }, ensure_ascii=False)


def test_llm_enriches_draft_and_virtual_annotators_find_ambiguity(library):
    fake = FakeLLM()
    script = dict(street_script(), rickshaw=[(rect(600, 500, 680, 580), 0.9)])
    labels = LABELS + [{"id": 6, "name": "barrier gate", "type": "rectangle"}]
    _, view = run_survey(GuidelineService(library, fake), labels=labels, send_images=True, script=script)
    md = view["markdown"]
    assert "**barrier gate**: Thanh chắn nâng hạ ở lối ra vào; không gồm trụ đỡ. *(LLM đề xuất, chưa chốt)*" in md
    assert "**car**: Ô tô con và xe van dùng để chở người." in md     # định nghĩa đã có trong guideline mẫu thì giữ nguyên
    assert "Xe đang mở cửa: box bao cả cánh cửa. *(LLM đề xuất, chưa chốt)*" in md
    assert any(f["code"] == "annotator_disagree" and "'car' so với 'truck'" in f["text"] for f in view["findings"])
    # LLM bù cho việc SAM3 chỉ tìm được thứ được gọi tên: khái niệm mới được quét thật; tên rác bị loại
    names = {c["name"]: c for c in view["concepts"]}
    assert names["rickshaw"]["source"] == "LLM" and names["rickshaw"]["instances"] > 0
    assert not any("ba gác" in n.lower() for n in names)
    assert fake.calls[0]["images"] == 4 and view["llm"] == {"provider": "fake", "model": "fake-1", "local": False, "images": True}
    assert "GUIDELINE MẪU: lab5_tracking" in fake.calls[1]["text"] and "Bài học từ các lần trước" in fake.calls[1]["text"]


def test_images_are_not_sent_without_consent(library):
    fake = FakeLLM()
    _, view = run_survey(GuidelineService(library, fake), send_images=False)
    assert [c["images"] for c in fake.calls] == [0]        # chỉ một lần gọi, chỉ có số liệu
    assert not any(f["code"] == "annotator_disagree" for f in view["findings"])
    assert view["llm"]["images"] is False


def test_llm_failure_falls_back_to_template(library):
    class Broken(LLM):
        provider, model = "fake", "broken"

        def complete(self, *a, **k):
            return "Xin lỗi, tôi không thể."

    _, view = run_survey(GuidelineService(library, Broken()))
    assert "## 2. Taxonomy" in view["markdown"] and any("LLM không viết được" in w for w in view["warnings"])


def test_generated_profile_is_usable_by_the_assist_tool(library, tmp_path):
    service = GuidelineService(library, None)
    run_id, _ = run_survey(service)
    service.answer(run_id, {"scope:person": "add"})
    text = service.profile_yaml(library.load_run(run_id))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "auto.yaml").write_text(text, encoding="utf-8")
    store = ProfileStore(profiles)
    profile = store.get(f"auto_{run_id}", [l["name"] for l in LABELS])
    cfgs = {c["name"]: c for c in resolve_labels(profile, LABELS)}
    assert cfgs["car"]["prompts"] == ["car", "van"] and cfgs["car"]["shape"] == "rectangle"   # van đã gộp vào car
    assert cfgs["lane/double yellow"]["polyline_mode"] == "edges" and cfgs["lane/crosswalk"]["polyline_mode"] == "long_edges"
    assert any(l["name"] == "person" for l in profile["labels"])


def test_reference_library_and_seed_lessons_load():
    lib = Library(ROOT / "guideline_library")
    names = {name for name, _ in lib.references()}
    assert names >= {"bbox_polygon_polyline_v1", "semantic_segmentation_v1", "humanpose17_v1", "face_landmark50_v1", "lab5_tracking"}
    assert "lab4_keypoint_pose" not in names, "guideline skeleton cũ đã cất vào archive/, không còn là tài liệu nền"
    raw = (ROOT / "guideline_library" / "knowledge.yaml").read_text(encoding="utf-8")
    tops = re.findall(r"^([a-z_]+):", raw, flags=re.M)
    assert len(tops) == len(set(tops)), "knowledge.yaml có mục cấp cao bị lặp: YAML sẽ âm thầm lấy mục sau"
    # Bài học khởi đầu cho keypoint theo hai guideline mới: trái/phải theo ảnh, chỉ người lái, không ngưỡng 60px
    assert lib.recall_answer("kp_lr", ["keypoints"])["answer"] == "image"
    assert lib.recall_answer("kp_scope", ["keypoints"])["answer"] == "driver"
    assert lib.recall_answer("min_size", ["keypoints"])["answer"] == "none"
    assert not [l for l in lib.lessons.values() if "theo người trong ảnh" in l["text"] or "60px" in l["text"] and "keypoints" in l["tags"]]
    assert lib.definition("lane/crosswalk") and lib.pair_rule("truck", "car")
    seeds = [l["id"] for l in lib.lessons.values() if l["source"] == "guideline mẫu"]
    assert len(seeds) == len(set(seeds)) == 21 and all(re.fullmatch(r"seed-[0-9a-f]{8}", i) for i in seeds)
    assert lib.recall_answer("occlusion_box", ["tracking"])["answer"] == "amodal"
    assert lib.recall_answer("occlusion_box", ["detection"])["answer"] == "visible"


def test_upgrade_over_an_old_install_does_not_revive_the_lab4_skeleton_convention(tmp_path):
    """Giải nén bản mới đè lên bản cũ để lại file cũ. Quy ước Lab 4 (trái/phải theo người) không được vì thế mà sống lại."""
    import shutil

    lib_dir, profiles = tmp_path / "lib", tmp_path / "profiles"
    shutil.copytree(ROOT / "guideline_library", lib_dir, ignore=shutil.ignore_patterns("runs", "lessons.jsonl"))
    shutil.copy(lib_dir / "archive" / "lab4_keypoint_pose.md", lib_dir / "reference")          # file cũ còn sót
    # Người dùng từng tắt một bài học khởi đầu bằng mã theo vị trí của bản cũ: không được rơi nhầm sang bài khác
    (lib_dir / "lessons.jsonl").write_text('{"id": "seed-6", "status": "disabled"}\n', encoding="utf-8")
    lib = Library(lib_dir)
    assert "lab4_keypoint_pose" not in {name for name, _ in lib.references()}
    assert all(l["status"] == "confirmed" for l in lib.lessons.values() if l["id"].startswith("seed-"))
    assert lib.recall_answer("kp_lr", ["keypoints"])["answer"] == "image"
    # Tắt một bài học khởi đầu bằng mã mới thì giữ được qua lần nạp sau
    target = lib.recall_answer("kp_scope", ["keypoints"])["id"]
    lib.set_status(target, "disabled")
    assert Library(lib_dir).lessons[target]["status"] == "disabled" and Library(lib_dir).recall_answer("kp_scope", ["keypoints"]) is None

    shutil.copytree(ROOT / "profiles", profiles)
    shutil.copy(ROOT / "profiles" / "archive" / "coco_pose_face_lab_v1.yaml", profiles)         # file cũ còn sót
    store = ProfileStore(profiles)
    assert "coco_pose_face_lab_v1" not in store.profiles and store.superseded == {"coco_pose_face_lab_v1": "driver_pose17_face50_v1"}
    assert store.get("auto", ["person"], "frame", ["skeleton"])["id"] == "driver_pose17_face50_v1"
    assert store.get("coco_pose_face_lab_v1", ["person"], "frame", ["skeleton"])["id"] == "driver_pose17_face50_v1"


def test_drafter_recognises_a_renamed_skeleton_label(library):
    """Task thật: label 'person_vf17' (17 điểm). Tool không được tưởng task thiếu 'person' rồi đề nghị tạo skeleton trùng."""
    from test_keypoints import driver_labels

    labels = driver_labels()[:1]
    labels[0]["name"] = "person_vf17"
    service = GuidelineService(library, None)
    run_id, view = run_survey(service, labels=labels, script=driver_script())
    assert view["task"] == "keypoints"
    assert question(view, "skeleton:pose17") is None, "label person_vf17 chính là skeleton person của guideline"
    assert question(view, "skeleton:face50")["recommended"] == "yes"
    assert {c["name"]: c["instances"] for c in view["concepts"]}["person_vf17"] == 12   # đếm NGƯỜI, không tìm chữ 'person vf17'
    md = view["markdown"]
    assert "Ứng với skeleton `person` của guideline" in md and "Người lái. Một skeleton 17 keypoint" in md
    assert "10/11 – Wrist" in md, "luật của HumanPose-17 phải có mặt dù tên label khác"
    plan = service.labels(run_id)
    assert [l["name"] for l in plan["labels"]][0] == "person_vf17" and "person" not in [l["name"] for l in plan["missing"]]
    assert len(plan["missing"]) == 7 and not [n for n in plan["notes"] if "person_vf17" in n]
    # Profile cho tool: nhận ra bộ label này thuộc profile người lái
    store = ProfileStore(ROOT / "profiles")
    base = store.pick([l["name"] for l in plan["labels"]], "frame", [l["type"] for l in plan["labels"]], plan["labels"])
    assert service.keypoint_profile(library.load_run(run_id), base)["profile_id"] == "driver_pose17_face50_v1"


# ----------------------------------------------------------- sinh label ----
PLACEHOLDER = [{"id": 9, "name": "object", "type": "any"}]


def new_dataset_script():
    script = street_script()
    script["car"] = script["car"] + [(rect(0, 500, 180, 640), 0.9)]        # một xe bị cắt bởi biên ảnh
    script["lane line"] = [(line(400, 560), 0.9)]
    script["face"] = [(rect(1004, 124, 1024, 146), 0.8)]
    return script


def test_labels_generated_for_a_new_image_task(library):
    service = GuidelineService(library, None)
    run_id, view = run_survey(service, labels=PLACEHOLDER, script=new_dataset_script())
    assert view["task"] == "detection"
    plan = service.labels(run_id)
    by_name = {l["name"]: l for l in plan["labels"]}
    assert by_name["car"]["type"] == "rectangle" and by_name["car"]["color"] == "#00008e"      # màu theo guideline mẫu
    assert by_name["lane line"]["type"] == "polyline" and by_name["tree"]["type"] == "polygon"
    assert by_name["car"]["attributes"] == [{"name": "truncated", "mutable": False, "input_type": "checkbox",
                                             "default_value": "false", "values": ["false"]}]
    assert by_name["tree"]["attributes"] == []
    assert "van" not in by_name and "billboard" not in by_name and "object" not in by_name      # van gộp vào car
    assert [l["name"] for l in plan["missing"]] == [l["name"] for l in plan["labels"]]
    assert any("'object' có trong task nhưng không thuộc taxonomy" in n for n in plan["notes"])
    assert view["label_plan"]["total"] == len(plan["labels"]) and "*(mới, cần tạo label)*" in view["markdown"]

    # Sau khi label được thêm vào task: không còn gì thiếu, bản nháp bỏ ghi chú "cần tạo label"
    created = [{"id": 100 + i, "name": l["name"], "type": l["type"]} for i, l in enumerate(plan["labels"])]
    synced = service.sync_labels(run_id, PLACEHOLDER + created)
    assert synced["label_plan"]["missing"] == [] and "*(mới, cần tạo label)*" not in synced["markdown"]
    assert "| car | rectangle |" in synced["markdown"]


def driver_script():
    return {"person": [(rect(300, 100, 800, 700), 0.9)], "face": [(rect(480, 130, 640, 320), 0.9)]}


def test_skeleton_labels_generated_for_a_keypoint_task(library):
    service = GuidelineService(library, None)
    run_id, view = run_survey(service, labels=PLACEHOLDER, script=driver_script())
    view = service.answer(run_id, {"task_type": "keypoints"})
    assert view["task"] == "keypoints"
    keys = [q["key"] for q in view["questions"]]
    assert not [k for k in keys if k.startswith(("scope:", "pair:"))]     # taxonomy của keypoint là các skeleton
    assert "min_size" not in keys, "hai guideline keypoint không đặt ngưỡng kích thước"
    # Mỗi BỘ skeleton một câu hỏi; gợi ý 'có' vì ảnh mẫu có người và có mặt
    assert question(view, "skeleton:pose17")["recommended"] == "yes" and question(view, "skeleton:face50")["recommended"] == "yes"
    assert "7 skeleton, 50 điểm" in question(view, "skeleton:face50")["text"]
    # Trái/phải và phạm vi: điền sẵn theo guideline mẫu, kèm nguồn
    lr = question(view, "kp_lr")
    assert lr["recommended"] == "image" and "guideline mẫu" in lr["reason"] and "HumanPose-17" in lr["reason"]
    assert question(view, "kp_scope")["recommended"] == "driver"

    plan = service.labels(run_id)
    assert [(l["name"], l["type"], len(l["sublabels"])) for l in plan["labels"]] == [
        ("person", "skeleton", 17), ("longmaytrai", "skeleton", 5), ("longmayphai", "skeleton", 5), ("songmui", "skeleton", 4),
        ("mattrai", "skeleton", 8), ("matphai", "skeleton", 8), ("moingoai", "skeleton", 12), ("moitrong", "skeleton", 8)]
    by_name = {l["name"]: l for l in plan["labels"]}
    assert [s["name"] for s in by_name["person"]["sublabels"]] == [str(i) for i in range(1, 18)]
    ids = [s["name"] for l in plan["labels"][1:] for s in l["sublabels"]]
    assert ids == [str(i) for i in range(50)], "Point ID chạy liên tục 0-49 qua cả 7 skeleton"
    edges = lambda spec: [(int(a), int(b)) for a, b in re.findall(r'data-node-from="(\d+)" data-node-to="(\d+)"', spec["svg"])]  # noqa: E731
    assert len(edges(by_name["person"])) == 18 and (2, 3) not in edges(by_name["person"])     # 18 cạnh, không có cạnh mắt-mắt
    assert edges(by_name["mattrai"])[-1] == (8, 1) and edges(by_name["moingoai"])[-1] == (12, 1)   # contour kín
    assert edges(by_name["songmui"]) == [(1, 2), (2, 3), (3, 4)]                                    # contour hở
    for spec in plan["labels"]:
        assert all(s["type"] == "points" for s in spec["sublabels"])
        nodes = re.findall(r'data-element-id="(\d+)" data-node-id="(\d+)" data-label-name="([^"]+)"', spec["svg"])
        assert [n[2] for n in nodes] == [s["name"] for s in spec["sublabels"]] and all(a == b for a, b, _ in nodes)
        assert "data-label-id" not in spec["svg"]

    # Label tool sinh ra phải được chính profile của tool nhận đủ, ghép theo số, không lệch điểm nào
    store = ProfileStore(ROOT / "profiles")
    made = [dict(l, id=10 * i, sublabels=[dict(s, id=10 * i + j + 1) for j, s in enumerate(l["sublabels"])])
            for i, l in enumerate(plan["labels"], 1)]
    profile = store.get("auto", [l["name"] for l in made], "frame", [l["type"] for l in made])
    cfgs = resolve_labels(profile, made)
    assert profile["id"] == "driver_pose17_face50_v1"
    assert all(c["supported"] and not c["missing"] and not c["matched_by_order"] for c in cfgs)

    md = view["markdown"]
    for expected in ("### Skeleton", "R nằm phía phải ảnh", "Chỉ gán một người: người lái".lower(), "±5 pixel", "Không dùng Hidden thay Outside",
                     "| Occluded | Occluded = 1 |", "10/11 – Wrist", "Point ID chạy liên tục 0–49", "moingoai 6/12",
                     "Left/Right & Topology", "không chắc điểm thuộc R hay L"):
        assert expected in md or expected in md.lower(), expected
    assert "60px" not in md and "theo người trong ảnh" not in md.lower()
    assert "Người lái. Một skeleton 17 keypoint" in md and "Người đi bộ, đứng hoặc ngồi" not in md   # định nghĩa person của bài keypoint
    assert md.count("chưa thấy trong ảnh mẫu") == 0, "7 skeleton mặt dùng chung số đếm khuôn mặt"
    assert "'face' bị che" not in md, "khuôn mặt nằm trong hộp của người không phải là bị che"

    # Người dùng không cần bộ skeleton mặt
    view = service.answer(run_id, {"skeleton:face50": "no"})
    assert [l["name"] for l in service.labels(run_id)["labels"]] == ["person"]
    assert "Point ID chạy liên tục" not in view["markdown"] and "10/11 – Wrist" in view["markdown"]
    # Nhóm chốt quy ước khác: bản nháp đổi theo, và câu trả lời thành bài học cho lần sau
    view = service.answer(run_id, {"kp_lr": "anatomical", "kp_scope": "all"})
    assert "theo NGƯỜI trong ảnh" in view["markdown"] and "Mỗi người nhìn thấy trong ảnh một skeleton" in view["markdown"]


def test_keypoint_guideline_drives_the_tool_profile(library, tmp_path):
    """Bản guideline chốt quy ước trái/phải và phạm vi; tool gợi ý phải làm theo đúng quy ước đó."""
    import shutil

    from backend import keypoints
    from backend.engines.pose import LazyEngine, MockBodyPose, MockFace

    profiles = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", profiles)
    store = ProfileStore(profiles)
    service = GuidelineService(library, None)
    run_id, _ = run_survey(service, labels=PLACEHOLDER, script=driver_script())
    service.answer(run_id, {"task_type": "keypoints"})
    plan = service.labels(run_id)
    names, types = [l["name"] for l in plan["labels"]], [l["type"] for l in plan["labels"]]

    # Mặc định (theo hai guideline mẫu): profile có sẵn đã đúng, không sinh file mới
    same = service.keypoint_profile(library.load_run(run_id), store.pick(names, "frame", types))
    assert same["profile_id"] == "driver_pose17_face50_v1" and same["yaml"] is None and "đã đúng" in same["note"]

    # Nhóm chốt khác: trái/phải theo người, gán mọi người -> một bản sao đã chỉnh, chỉ dùng khi chọn đích danh
    service.answer(run_id, {"kp_lr": "anatomical", "kp_scope": "all"})
    made = service.keypoint_profile(library.load_run(run_id), store.pick(names, "frame", types))
    assert made["profile_id"] == f"auto_{run_id}" and "&" not in made["yaml"].split("\n", 2)[2]
    (profiles / f"{made['profile_id']}.yaml").write_text(made["yaml"], encoding="utf-8")
    store.reload()
    assert store.get("auto", names, "frame", types)["id"] == "driver_pose17_face50_v1", "bản sao không tự áp cho job khác"
    derived = store.get(made["profile_id"], names, "frame", types)
    assert derived["rules"]["pose"]["lr_convention"] == "anatomical" and derived["rules"]["pose"]["max_people"] == 0
    assert derived["rules"]["qa"]["one_per_label"] is False and len(derived["labels"]) == 8

    labels = [dict(l, id=10 * i, sublabels=[dict(s, id=10 * i + j + 1) for j, s in enumerate(l["sublabels"])])
              for i, l in enumerate(plan["labels"], 1)]
    script = {"person": [(rect(200, 100, 620, 700), 0.9), (rect(900, 300, 1000, 500), 0.9)]}
    lazy = lambda e: LazyEngine(lambda: e, "mock")  # noqa: E731
    out = {}
    for profile in (store.get("auto", names, "frame", types), derived):
        cfgs = resolve_labels(profile, labels)
        res = keypoints.propose(ScriptedEngine(script), {}, np.zeros((H, W, 3), np.uint8), profile, cfgs, [],
                                lazy(MockBodyPose()), lazy(MockFace()))
        out[profile["id"]] = [p for p in res["proposals"] if p["label_name"] == "person"]
    x = lambda p, name: next(e["x"] for e in p["elements"] if e["name"] == name)  # noqa: E731
    by_image, by_person = out["driver_pose17_face50_v1"], out[made["profile_id"]]
    assert len(by_image) == 1 and len(by_person) == 2
    # Model giả lập: người quay mặt về camera. Theo ảnh: điểm 6 (R) bên phải ảnh. Theo người: vai phải của người nằm bên trái ảnh.
    assert x(by_image[0], "6") > x(by_image[0], "7") and x(by_person[0], "6") < x(by_person[0], "7")


def test_keypoint_task_with_existing_labels_only_adds_what_is_missing(library):
    service = GuidelineService(library, None)
    existing = [{"id": 1, "name": "person", "type": "skeleton"}, {"id": 2, "name": "mattrai", "type": "skeleton"},
                {"id": 3, "name": "matphai", "type": "skeleton"}]
    run_id, view = run_survey(service, labels=existing, script=driver_script())
    assert view["task"] == "keypoints"
    assert question(view, "skeleton:pose17") is None                     # person đã có
    q = question(view, "skeleton:face50")
    assert q["recommended"] == "yes" and "2/7" in q["reason"]
    plan = service.labels(run_id)
    assert [l["name"] for l in plan["missing"]] == ["longmaytrai", "longmayphai", "songmui", "moingoai", "moitrong"]
    names = {c["name"]: c for c in view["concepts"]}
    assert names["person"]["instances"] == 12 and names["mattrai"]["instances"] == 12   # đếm khuôn mặt một lần cho cả bộ


def test_labels_generated_for_a_tracking_task(library):
    service = GuidelineService(library, None)
    run_id, view = run_survey(service, labels=PLACEHOLDER, mode="interpolation", script=new_dataset_script())
    assert view["task"] == "tracking"
    plan = service.labels(run_id)
    assert plan["labels"] and all(l["type"] == "rectangle" and l["attributes"] == [] for l in plan["labels"])
    assert {"car", "person"} <= {l["name"] for l in plan["labels"]} and "tree" not in {l["name"] for l in plan["labels"]}
    profile = yaml_load(service.profile_yaml(library.load_run(run_id)))
    assert profile["mode"] == "tracking" and profile["rules"]["tracking"]["reid_max_gap_frames"] == 60


def test_existing_labels_are_never_removed_or_retyped(library):
    labels = [{"id": 1, "name": "car", "type": "polygon"}, {"id": 2, "name": "lane/crosswalk", "type": "polyline"}]
    service = GuidelineService(library, None)
    run_id, _ = run_survey(service, labels=labels)
    service.answer(run_id, {"scope:person": "add"})
    plan = service.labels(run_id)
    assert [l["name"] for l in plan["missing"]] == ["person"]
    assert {l["name"]: l["type"] for l in plan["labels"]}["car"] == "polygon"     # giữ kiểu label người tạo task đã chọn


def yaml_load(text):
    import yaml

    return yaml.safe_load(text)
