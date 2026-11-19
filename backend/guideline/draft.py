"""Từ số liệu khảo sát tới guideline nháp: câu hỏi cần chốt, văn bản guideline, profile cho tool, tự kiểm tra.

Nguyên tắc thiết kế:
  - Luật của guideline là QUYẾT ĐỊNH của người. Tool không tự quyết: mỗi quyết định thành một câu hỏi
    kèm số liệu, ảnh ví dụ và một lựa chọn gợi ý (từ thống kê, guideline mẫu, hoặc lần trước).
  - Mọi dòng luật mang một mã ẩn (<!--r:...-->). Khi người duyệt sửa dòng đó, tool biết chính xác luật nào
    đã bị sửa và dùng lời của người duyệt ở các lần soạn sau.
  - Chạy được không cần LLM. Có LLM thì thêm định nghĩa class, luật cho cặp dễ nhầm và annotator ảo.
"""
from __future__ import annotations

from typing import Optional

from ..profiles import match_skeletons
from .library import Library, norm
from .survey import NOT_REAL

TASKS = {
    "detection": "Detection / instance (box, polygon, polyline theo từng đối tượng)",
    "semantic": "Semantic segmentation (mỗi pixel một class)",
    "keypoints": "Keypoint / skeleton",
    "tracking": "Tracking trên video (mỗi đối tượng một ID)",
}
SHAPE_OF_KIND = {"thing": "rectangle", "stuff": "polygon", "line": "polyline"}
ISSUE_TEXT = {
    "UNCERTAIN_CLASS": "Không chắc class", "UNCERTAIN_BOUNDARY": "Không rõ phạm vi do che khuất",
    "UNCERTAIN_SCOPE": "Không chắc có thuộc phạm vi", "LOW_VISIBILITY": "Khó quan sát",
}


def infer_task(labels: list[dict], job_mode: str, summary: dict) -> str:
    types = {(l.get("type") or "any") for l in labels}
    if "skeleton" in types:
        return "keypoints"
    if job_mode == "interpolation":
        return "tracking"
    if types and types <= {"mask"}:
        return "semantic"
    found = [c for c in summary["concepts"] if c["instances"]]
    stuff = sum(c["kind"] == "stuff" for c in found)
    if types <= {"mask", "polygon"} and found and stuff / len(found) >= 0.4:
        return "semantic"
    return "detection"


def skeleton_sets(lib: Library) -> dict:
    """Các bộ skeleton trong thư viện. Skeleton không thuộc bộ nào thì tự thành một bộ một label."""
    sets = dict(lib.knowledge.get("skeleton_sets") or {})
    grouped = {name for group in sets.values() for name in group["labels"]}
    for name, template in (lib.knowledge.get("skeletons") or {}).items():
        if name not in grouped:
            sets[name] = {"title": f"{name} ({len(template['points'])} điểm)", "labels": [name], "detect": name}
    return sets


def skeleton_canon(labels: list[dict], lib: Library) -> dict[str, str]:
    """{tên label skeleton trong task (đã chuẩn hoá): tên skeleton mẫu trong thư viện}.

    Task thật hay đặt tên khác guideline ('person_vf17' thay cho 'person'). Không nhận ra điều đó thì tool sẽ
    tưởng task thiếu label 'person' và đề nghị tạo thêm một skeleton trùng.
    """
    templates = lib.knowledge.get("skeletons") or {}
    candidates = [{"name": name, "aliases": template.get("aliases", []),
                   "keypoints": [({"id": int(p["name"])} if str(p["name"]).isdigit() else {}) for p in template["points"]]}
                  for name, template in templates.items()]
    found = match_skeletons(labels, candidates)
    return {norm(labels[i]["name"]): candidate["name"] for i, (candidate, _) in found.items()
            if (labels[i].get("type") or "any") == "skeleton"}


def placeholder_labels(labels: list[dict], summary: dict) -> bool:
    """Task chưa có taxonomy thật (ví dụ chỉ một label tạm): khi đó tool đề xuất class từ ảnh."""
    seen = {norm(c["name"]) for c in summary["concepts"] if c["source"] == "label" and c["instances"]}
    return len(labels) <= 1 and not seen


# ------------------------------------------------------------ câu hỏi ------
def build_questions(summary: dict, labels: list[dict], job_mode: str, lib: Library,
                    task_override: Optional[str] = None) -> list[dict]:
    inferred = infer_task(labels, job_mode, summary)
    task = task_override if task_override in TASKS else inferred
    questions: list[dict] = []
    label_names = {norm(l["name"]) for l in labels}
    no_taxonomy = placeholder_labels(labels, summary)
    concepts = {norm(c["name"]): c for c in summary["concepts"]}
    n = max(summary["images"], 1)

    def ask(key: str, text: str, options: list[tuple[str, str]], default: str, reason: str,
            evidence: str = "", examples: Optional[list] = None, tags: Optional[list] = None) -> None:
        recommended, source = default, reason
        remembered = lib.recall_answer(key, tags or [task])
        if remembered and any(o[0] == remembered["answer"] for o in options):
            recommended = remembered["answer"]
            origin = "guideline mẫu" if remembered["id"].startswith("seed-") else "lần trước bạn đã chốt"
            source = f"Theo {origin}: {remembered['text']}"
            remembered_id = remembered["id"]
        else:
            remembered_id = None
        questions.append({"key": key, "text": text, "evidence": evidence, "examples": examples or [],
                          "options": [{"id": o[0], "label": o[1]} for o in options],
                          "recommended": recommended, "reason": source, "lesson": remembered_id})

    ask("task_type", "Bộ dữ liệu này dùng cho bài toán nào?", list(TASKS.items()), inferred,
        "Suy từ kiểu label của task và loại job.", tags=["any"])

    # Keypoint: taxonomy là các skeleton. Mỗi BỘ skeleton (một guideline) là một câu hỏi: tạo đủ các label của bộ theo mẫu.
    if task == "keypoints":
        templates = lib.knowledge.get("skeletons") or {}
        have = label_names | {norm(v) for v in skeleton_canon(labels, lib).values()}
        for set_id, group in skeleton_sets(lib).items():
            missing = [name for name in group["labels"] if norm(name) not in have and name in templates]
            if not missing:
                continue
            detect = norm(group.get("detect", ""))
            seen = next((c for c in summary["concepts"] if norm(c["name"]) == detect or detect in [norm(p) for p in c.get("prompts") or []]), {})
            points = sum(len(templates[name]["points"]) for name in group["labels"] if name in templates)
            partly = f" Task đã có {len(group['labels']) - len(missing)}/{len(group['labels'])} label của bộ; tool chỉ thêm label còn thiếu." \
                if len(missing) < len(group["labels"]) else ""
            ask(f"skeleton:{set_id}", f"Tạo bộ label '{group['title']}' ({len(group['labels'])} skeleton, {points} điểm) không?",
                [("yes", "Có, tạo theo mẫu trong thư viện"), ("no", "Không cần")],
                "yes" if seen.get("instances") else "no",
                f"Mẫu skeleton theo guideline {group.get('guideline', set_id)}: tên điểm và thứ tự đúng như guideline quy định.{partly}",
                f"'{detect}' xuất hiện {seen.get('instances', 0)} lần trên {seen.get('images', 0)}/{n} ảnh mẫu.", seen.get("examples"))
        ask("kp_lr", "Điểm trái/phải (L/R) tính theo đâu?",
            [("image", "Theo phía của ẢNH: R ở bên phải ảnh, L ở bên trái ảnh"),
             ("anatomical", "Theo NGƯỜI trong ảnh: trái/phải giải phẫu của người đó")],
            "image", "Hai cách cho kết quả ngược nhau với người quay mặt về camera; cả nhóm phải dùng cùng một cách.")
        people = concepts.get("person", {})
        per_image = people.get("instances", 0) / max(people.get("images", 0), 1)
        ask("kp_scope", "Mỗi ảnh gán skeleton cho những ai?",
            [("driver", "Chỉ một người (người lái); người khác trong khung không gán"),
             ("all", "Mọi người nhìn thấy trong ảnh")],
            "driver" if per_image <= 1.5 else "all", "Suy từ số người trung bình mỗi ảnh.",
            f"Trung bình {per_image:.1f} người trên mỗi ảnh có người." if people.get("instances") else "")

    # Phạm vi: vật xuất hiện nhiều nhưng không có label
    # Chỉ hỏi về thứ cùng loại với taxonomy hiện có: task toàn object thì không hỏi về trời, đường, nhà.
    label_kinds = {c["kind"] for c in summary["concepts"] if c["source"] == "label"} or {"thing"}
    if task == "tracking" and no_taxonomy:
        label_kinds, no_taxonomy_kinds = {"thing"}, {"thing"}  # chỉ theo dõi được vật đếm được
    else:
        no_taxonomy_kinds = {"thing", "stuff", "line"}
    extra = [c for c in summary["concepts"]
             if task != "keypoints" and norm(c["name"]) not in label_names and c["instances"] >= 3 and c["image_pct"] >= 10
             and norm(c["name"]) not in NOT_REAL
             and (c["kind"] in no_taxonomy_kinds if no_taxonomy else c["kind"] in label_kinds)]
    extra_names = {norm(c["name"]) for c in extra[:10]}

    def absorbs(other: str, c: dict) -> bool:
        """`other` có thể nhận `c` làm một phần của nó: là label của task, hoặc (task chưa có taxonomy) là khái niệm
        phổ biến hơn trong khi `c` không phải class đã được guideline mẫu định nghĩa riêng."""
        if other in label_names:
            return True
        return (no_taxonomy and other in extra_names and concepts[other]["instances"] > c["instances"]
                and not lib.definition(c["name"]))

    for c in extra[:10]:
        partner = next((p for p in summary["pairs"]
                        if norm(c["name"]) in (norm(p["a"]), norm(p["b"])) and p["share_pct"] >= 50
                        and all(absorbs(o, c) for o in {norm(p["a"]), norm(p["b"])} - {norm(c["name"])})), None)
        options = [("out", "Ghi rõ là ngoài phạm vi (không gán)"), ("add", "Thêm thành class mới")]
        default, reason = ("add", "Task chưa có taxonomy; vật này xuất hiện thường xuyên.") if no_taxonomy else \
            ("out", "Guideline mẫu: chỉ annotate class trong taxonomy, không tự tạo class mới.")
        if partner:
            other = partner["b"] if norm(partner["a"]) == norm(c["name"]) else partner["a"]
            options.append((f"merge:{other}", f"Tính vào class '{other}'"))
            default, reason = f"merge:{other}", f"{partner['share_pct']:.0f}% trường hợp trùng với '{other}'."
        ask(f"scope:{c['name']}", f"'{c['name']}' có trong {c['image_pct']:.0f}% ảnh nhưng task không có label này. Xử lý thế nào?",
            options, default, reason, f"{c['instances']} lần xuất hiện trên {c['images']}/{n} ảnh.", c["examples"])

    # Cặp class dễ nhầm
    # Khái niệm đã được đề xuất gộp vào một class thì không hỏi lại dưới dạng "cặp dễ nhầm".
    merged = {norm(q["key"][6:]) for q in questions if q["key"].startswith("scope:") and q["recommended"].startswith("merge:")}
    for p in summary["pairs"][:8]:
        if task == "keypoints" or p["count"] < 2 or norm(p["a"]) in merged or norm(p["b"]) in merged:
            continue  # taxonomy của keypoint là các skeleton, không phải các class dễ nhầm với nhau
        known = lib.pair_rule(p["a"], p["b"])
        a, b = sorted((p["a"], p["b"]))
        ask(f"pair:{a}|{b}", f"'{p['a']}' và '{p['b']}' hay bị nhận lẫn nhau. Quy định thế nào?",
            [("rule", "Giữ hai class, kèm quy tắc phân biệt"), ("merge", "Gộp thành một class"),
             ("mentor", "Chưa có rule: mọi case tạo Issue UNCERTAIN_CLASS")],
            "rule" if known else "mentor",
            f"Guideline mẫu đã có quy tắc: {known}" if known else "Chưa có quy tắc phân biệt trong guideline mẫu.",
            f"{p['count']} vật khớp cả hai ({p['share_pct']:.0f}% số vật của class ít hơn).", p["examples"])

    things = [c for c in summary["concepts"] if c["kind"] == "thing" and c["instances"]
              and (norm(c["name"]) in label_names or no_taxonomy)]
    if things and task != "semantic":
        p10 = min(c["min_side_p10"] for c in things)
        suggested = int(max(8, round(p10 / 5.0) * 5))
        options = [("none", "Không đặt ngưỡng: gán mọi object xác định được class"),
                   (str(suggested), f"Bỏ qua object có cạnh ngắn dưới {suggested}px")]
        if summary["tiny_pct"] >= 5:
            ask("min_size", "Có đặt ngưỡng kích thước tối thiểu không?", options, "none",
                "Guideline mẫu không đặt ngưỡng; object quá bé dùng Issue LOW_VISIBILITY.",
                f"{summary['tiny_pct']:.0f}% object có cạnh ngắn dưới 12px; 10% nhỏ nhất có cạnh ngắn khoảng {p10:.0f}px.")
        if summary["occluded_pct"] >= 10 and task in ("detection", "tracking"):
            ask("occlusion_box", "Object bị che một phần thì box vẽ thế nào?",
                [("visible", "Bao sát phần cần annotate, bật occluded"), ("amodal", "Bao toàn bộ object ước lượng (kể cả phần bị che), bật occluded")],
                "amodal" if task == "tracking" else "visible", "Detection ảnh tĩnh thường vẽ phần nhìn thấy; tracking vẽ toàn thân.",
                f"Khoảng {summary['occluded_pct']:.0f}% object bị object khác che.")
        if summary["truncated_pct"] >= 5 and task == "detection":
            ask("truncated_attr", "Có dùng attribute truncated cho object bị cắt bởi biên ảnh không?",
                [("yes", "Có: bật truncated khi object bị cắt bởi biên ảnh"), ("no", "Không dùng")], "yes",
                "Guideline BBox mẫu dùng truncated.", f"Khoảng {summary['truncated_pct']:.0f}% object chạm biên ảnh.")

    # Loại shape và cách vẽ vạch
    if task == "detection":
        for c in summary["concepts"]:
            key = norm(c["name"])
            in_scope = key in label_names or no_taxonomy
            label_type = c.get("label_type") or "any"
            if not in_scope or not (c["instances"] or label_type == "polyline"):
                continue
            hint = SHAPE_OF_KIND[c["kind"]]
            if not c["instances"]:
                pass  # label polyline chưa thấy trong ảnh mẫu: vẫn cần chốt cách vẽ
            elif label_type == "any" and hint != "rectangle":
                ask(f"shape:{c['name']}", f"'{c['name']}' vẽ bằng shape nào?",
                    [("polygon", "Polygon"), ("polyline", "Polyline"), ("rectangle", "Rectangle"), ("mask", "Mask")], hint,
                    "Hình dạng đo được trong ảnh: " + ("vạch mảnh và dài." if c["kind"] == "line" else "vùng lớn, biên không đều."),
                    examples=c["examples"])
            elif label_type not in ("any", hint) and c["kind"] != "thing" and label_type == "rectangle":
                ask(f"shape:{c['name']}", f"Label '{c['name']}' đang là rectangle nhưng trong ảnh nó là "
                    + ("vạch mảnh." if c["kind"] == "line" else "vùng lớn.") + " Giữ hay đổi?",
                    [("rectangle", "Giữ rectangle"), (hint, f"Đổi sang {hint}")], "rectangle",
                    "Kiểu label do người tạo task chọn; đổi cần sửa task.", examples=c["examples"])
            if c["kind"] == "line" or label_type == "polyline":
                ask(f"line_mode:{c['name']}", f"Polyline của '{c['name']}' đi theo đâu?",
                    [("centerline", "Một đường theo tim vạch"), ("edges", "Hai đường theo hai mép ngoài"),
                     ("long_edges", "Hai đường thẳng theo hai mép dài của cả vùng")], "centerline",
                    "Guideline mẫu: polyline đi theo tim lane marking.", examples=c["examples"])

    nested = [c for c in summary["cases"] if c["code"] == "UNCERTAIN_SCOPE"]
    ask("reflections", "Phản chiếu, bóng đổ, poster, hình trên billboard/màn hình, tượng: có gán không?",
        [("not_label", "Không gán"), ("label", "Gán như object thật")], "not_label",
        "Cả ba guideline ảnh mẫu đều quy định không gán.",
        f"Tìm thấy {len(nested)} trường hợp object nằm trong billboard/màn hình/poster." if nested else "",
        [{"frame": c["frame"], "bbox": c["bbox"]} for c in nested[:4]], tags=["any"])
    if summary["dark_pct"] >= 15 or summary["blurry_pct"] >= 15 or summary["tiny_pct"] >= 10:
        ask("low_visibility", "Object khó quan sát (tối, mờ, quá bé) xử lý thế nào?",
            [("issue", "Vẫn gán nếu xác định được class, và tạo Issue LOW_VISIBILITY"), ("skip", "Không gán")], "issue",
            "Guideline mẫu: tạo Issue LOW_VISIBILITY, không đoán cho xong.",
            f"{summary['dark_pct']:.0f}% ảnh tối, {summary['blurry_pct']:.0f}% ảnh mờ, {summary['tiny_pct']:.0f}% object rất nhỏ.",
            tags=["any"])
    return questions


def decided(run: dict, key: str) -> tuple[Optional[str], bool]:
    """(lựa chọn đang dùng, đã được người chốt chưa). Chưa chốt thì dùng lựa chọn gợi ý."""
    if key in run.get("answers", {}):
        return run["answers"][key], True
    q = next((q for q in run["questions"] if q["key"] == key), None)
    return (q["recommended"], False) if q else (None, False)


def task_of(run: dict) -> str:
    return decided(run, "task_type")[0] or "detection"


# ------------------------------------------------------------ taxonomy -----
def taxonomy(run: dict, lib: Library) -> list[dict]:
    summary, labels = run["summary"], run["labels"]
    task = task_of(run)
    concepts = {norm(c["name"]): c for c in summary["concepts"]}
    no_taxonomy = placeholder_labels(labels, summary)
    llm_defs = {norm(k): v for k, v in (run.get("llm") or {}).get("definitions", {}).items()}
    classes = []

    def shape_for(name: str, label_type: str, kind: str) -> str:
        if label_type not in ("", "any"):
            chosen, _ = decided(run, f"shape:{name}")
            return chosen or label_type
        if task == "semantic":
            return "mask"
        if task == "tracking":
            return "rectangle (track)"
        chosen, _ = decided(run, f"shape:{name}")
        return chosen or SHAPE_OF_KIND.get(kind, "rectangle")

    set_defs = {norm(k): v for group in skeleton_sets(lib).values() for k, v in (group.get("definitions") or {}).items()} \
        if task == "keypoints" else {}

    detect_of = {norm(n): norm(group.get("detect") or "") for group in skeleton_sets(lib).values() for n in group["labels"]} \
        if task == "keypoints" else {}

    canon = skeleton_canon(labels, lib) if task == "keypoints" else {}

    def add(name: str, label: Optional[dict], concept: Optional[dict], new: bool) -> None:
        c = concept or {}
        known = norm(canon.get(norm(name), name))   # tên skeleton của guideline mà label này ứng với
        target = detect_of.get(known)
        if target and not c.get("instances"):
            # Skeleton được đếm qua đối tượng mang nó (người, khuôn mặt): các label cùng bộ dùng chung số đếm đó.
            found = next((x for x in summary["concepts"] if norm(x["name"]) == target
                          or target in [norm(p) for p in x.get("prompts") or []]), None)
            c = dict(found, prompts=c.get("prompts", [])) if found else c
        definition = set_defs.get(known) or lib.definition(canon.get(norm(name), name))
        if definition and known != norm(name):
            definition = f"(Ứng với skeleton `{canon[norm(name)]}` của guideline.) {definition}"
        origin = "guideline mẫu" if definition else ""
        if not definition and norm(name) in llm_defs:
            definition, origin = llm_defs[norm(name)], "LLM đề xuất"
        classes.append({
            "name": name, "canonical": canon.get(norm(name)), "new": new, "label_id": (label or {}).get("id"),
            "shape": shape_for(name, (label or {}).get("type") or "any", c.get("kind", "thing")),
            "kind": c.get("kind", "thing"), "prompts": c["prompts"] if "prompts" in c else [norm(name)],
            "definition": definition or "", "definition_source": origin,
            "instances": c.get("instances", 0), "image_pct": c.get("image_pct", 0.0),
            "merged": [],
        })

    if not no_taxonomy:
        for label in labels:
            add(label["name"], label, concepts.get(norm(label["name"])), False)
    for q in run["questions"]:
        if not q["key"].startswith("scope:"):
            continue
        name = q["key"][6:]
        choice, _ = decided(run, q["key"])
        if choice == "add":
            add(name, None, concepts.get(norm(name)), True)
        elif choice and choice.startswith("merge:"):
            target = next((c for c in classes if norm(c["name"]) == norm(choice[6:])), None)
            if target:
                target["merged"].append(name)
                target["prompts"] = list(dict.fromkeys(target["prompts"] + (concepts.get(norm(name), {}).get("prompts") or [])))
    sets = skeleton_sets(lib)
    have = {norm(c.get("canonical") or c["name"]) for c in classes}
    for q in run["questions"]:
        if q["key"].startswith("skeleton:") and decided(run, q["key"])[0] == "yes":
            for name in (sets.get(q["key"][9:]) or {}).get("labels", []):
                if norm(name) in have:
                    continue
                add(name, None, concepts.get(norm(name)), True)
                classes[-1]["shape"] = "skeleton"
                classes[-1]["prompts"] = []
    if no_taxonomy and classes:
        # Label tạm của task (chưa thấy trong ảnh) không thuộc taxonomy mới.
        classes = [c for c in classes if c["new"] or c["instances"]]
    return classes


# ------------------------------------------------------------- văn bản -----
class Writer:
    def __init__(self, run: dict, lib: Library):
        self.run, self.lib = run, lib
        self.task = task_of(run)
        self.tags = [self.task]
        self.lines: list[str] = []
        self.used: dict[str, dict] = {}
        self.dropped: list[str] = []

    def heading(self, level: int, text: str) -> None:
        self.lines += ["", "#" * level + " " + text, ""]

    def text(self, line: str = "") -> None:
        self.lines.append(line)

    def rule(self, rid: str, text: str, question: Optional[str] = None, note: str = "") -> None:
        """Một dòng luật. Lời của người duyệt ở lần trước (nếu có) thay cho lời mặc định."""
        override = self.lib.rule_override(rid, self.tags)
        if override is not None:
            self.used[override["id"]] = override
            if not override.get("replacement"):
                self.dropped.append(text)
                return
            text, note = override["replacement"], " *(theo lần trước)*"
        elif question:
            _, fixed = decided(self.run, question)
            lesson = next((q.get("lesson") for q in self.run["questions"] if q["key"] == question), None)
            if not fixed:
                note = " *(đề xuất, chưa chốt)*"
            if lesson and lesson in self.lib.lessons:
                self.used[lesson] = self.lib.lessons[lesson]
        self.lines.append(f"- {text}{note} <!--r:{rid}-->")

    def extras(self, section: str) -> None:
        for lesson in self.lib.extra_rules(section, self.tags):
            self.used[lesson["id"]] = lesson
            self.lines.append(f"- {lesson['text']} *(từ lần trước, xác nhận còn áp dụng)*")
        for item in (self.run.get("llm") or {}).get("extra_rules", []):
            if norm(item.get("section", "")) == norm(section):
                self.lines.append(f"- {item['text']} *(LLM đề xuất, chưa chốt)*")


def frames_text(examples: list[dict]) -> str:
    frames = sorted({e["frame"] for e in examples})
    return ", ".join(str(f) for f in frames[:5]) if frames else "—"


def render_markdown(run: dict, lib: Library) -> tuple[str, list[dict]]:
    s = run["summary"]
    w = Writer(run, lib)
    task = w.task
    classes = taxonomy(run, lib)
    shapes = sorted({c["shape"] for c in classes})
    title = run.get("title") or f"Job {run['job'].get('id', '?')}"

    w.text(f"# Annotation Guideline (bản nháp) — {title}")
    w.text()
    w.text(f"> Bản nháp do SAM3 CVAT Assist soạn từ {s['images']} ảnh mẫu. Số liệu và ca khó là đo trên ảnh; "
           "các luật ghi *(đề xuất, chưa chốt)* là gợi ý của tool và cần người phụ trách quyết định. "
           "Bản này chưa phải guideline chính thức cho tới khi được duyệt.")
    w.text()
    w.text("| Mục | Nội dung |")
    w.text("|---|---|")
    w.text(f"| Dữ liệu | {s['images']} ảnh mẫu, {s['width']} x {s['height']} px"
           + (f" (có {s['size_variants']} kích thước khác nhau)" if s["size_variants"] > 1 else "") + " |")
    w.text("| Công cụ | CVAT |")
    w.text(f"| Bài toán | {TASKS[task]} |")
    w.text(f"| Phạm vi | {', '.join(shapes) if shapes else 'chưa xác định'} |")
    if run.get("purpose"):
        w.text(f"| Mục đích | {run['purpose']} |")
    w.text("| Nguyên tắc | Làm theo guideline; các case không rõ phải đưa mentor review |")

    w.heading(2, "1. Phạm vi và dữ liệu")
    w.rule("scope.only", "Chỉ annotate các class được liệt kê ở mục 2; không tự tạo class mới, không đổi tên class, không gộp class theo cảm tính.")
    out = [q["key"][6:] for q in run["questions"] if q["key"].startswith("scope:") and decided(run, q["key"])[0] == "out"]
    for name in out:
        w.rule(f"scope.out:{name}", f"'{name}' xuất hiện trong ảnh nhưng nằm ngoài phạm vi: không gán.", f"scope:{name}")
    choice, _ = decided(run, "reflections")
    w.rule("scope.reflections",
           "Không gán reflection, bóng đổ, poster, hình trên billboard/màn hình, tượng thành object thật." if choice != "label"
           else "Reflection, poster, hình trên billboard/màn hình được gán như object thật.", "reflections")
    conditions = []
    if s["dark_pct"] >= 5:
        conditions.append(f"{s['dark_pct']:.0f}% ảnh tối")
    if s["blurry_pct"] >= 5:
        conditions.append(f"{s['blurry_pct']:.0f}% ảnh mờ")
    if conditions:
        low, _ = decided(run, "low_visibility")
        w.rule("scope.low_visibility", f"Bộ ảnh có {', '.join(conditions)}. "
               + ("Object khó quan sát vẫn gán nếu xác định được class, và tạo Issue LOW_VISIBILITY." if low != "skip"
                  else "Object không xác định được class do điều kiện ảnh thì không gán."), "low_visibility")
    w.extras("Phạm vi và dữ liệu")

    w.heading(2, "2. Taxonomy và loại shape")
    if not classes:
        w.text("*Chưa có class nào. Trả lời các câu hỏi về phạm vi để tool đề xuất taxonomy.*")
    else:
        w.text("| Class | CVAT shape | Quan sát trong ảnh mẫu |")
        w.text("|---|---|---|")
        for c in classes:
            seen = f"{c['instances']} lần, {c['image_pct']:.0f}% ảnh" if c["instances"] else "chưa thấy trong ảnh mẫu"
            name = f"{c['name']} *(mới, cần tạo label)*" if c["new"] else c["name"]
            w.text(f"| {name} | {c['shape']} | {seen} |")
        w.text()
        w.text("Định nghĩa:")
        w.text()
        for c in classes:
            text = c["definition"] or "⟨cần viết định nghĩa⟩"
            merged = f" Bao gồm cả: {', '.join(c['merged'])}." if c["merged"] else ""
            note = " *(LLM đề xuất, chưa chốt)*" if c["definition_source"] == "LLM đề xuất" else ""
            override = lib.rule_override(f"def:{c['name']}", w.tags)
            if override is not None and override.get("replacement"):
                w.used[override["id"]] = override
                w.text(f"- {override['replacement']} *(theo lần trước)* <!--r:def:{c['name']}-->")
            else:
                w.text(f"- **{c['name']}**: {text}{merged}{note} <!--r:def:{c['name']}-->")
    w.extras("Taxonomy và loại shape")

    w.heading(2, "3. Quy tắc hình học")
    kinds = " ".join(shapes)
    if "rectangle" in kinds:
        w.heading(3, "Bounding box")
        w.rule("bbox.tight", "Box bao sát đối tượng theo 4 extreme point (trái, phải, trên, dưới), không quá rộng hoặc quá chặt, không vượt biên ảnh.")
        w.rule("bbox.one", "Mỗi object một box riêng. Box được phép chồng lấn khi các object nằm gần hoặc che nhau.")
        occ = decided(run, "occlusion_box")[0] or ("amodal" if task == "tracking" else "visible")
        if occ == "amodal":
            w.rule("bbox.occlusion", "Object bị che một phần: box vẫn ở vị trí ước lượng của toàn bộ object, bật occluded. Không co box theo phần nhìn thấy.", "occlusion_box")
        else:
            w.rule("bbox.occlusion", "Object bị che một phần: box bao sát phần cần annotate, bật occluded. Bị che không quá 80% vẫn phải gán.", "occlusion_box")
        size, _ = decided(run, "min_size")
        if size and size != "none":
            w.rule("bbox.min_size", f"Object có cạnh ngắn dưới {size}px: không gán, đếm vào báo cáo.", "min_size")
        elif size == "none":
            w.rule("bbox.min_size", "Không đặt ngưỡng kích thước: object quá bé vẫn gán nếu xác định được class, kèm Issue LOW_VISIBILITY.", "min_size")
        w.extras("Bounding box")
    if "polygon" in kinds:
        w.heading(3, "Polygon")
        w.rule("poly.boundary", "Các điểm bám sát ranh giới và extreme point của vùng, bao phủ vừa đủ; các cạnh không tự giao cắt.")
        w.rule("poly.points", "Ít điểm ở đoạn thẳng, nhiều điểm ở đoạn cong và ranh giới phức tạp.")
        w.extras("Polygon")
    if "polyline" in kinds:
        w.heading(3, "Polyline")
        modes = {"centerline": "một polyline theo tim vạch", "edges": "hai polyline theo hai mép ngoài của cả vạch",
                 "long_edges": "hai polyline thẳng theo hai mép dài của cả vùng"}
        for c in classes:
            if c["shape"] == "polyline":
                mode, _ = decided(run, f"line_mode:{c['name']}")
                w.rule(f"line.mode:{c['name']}", f"'{c['name']}': vẽ {modes.get(mode or 'centerline')}.", f"line_mode:{c['name']}")
        w.rule("line.no_shortcut", "Không nối tắt qua vùng không có vạch; kết thúc polyline tại điểm không còn đủ bằng chứng thị giác.")
        w.rule("line.direction", "Giữ hướng vẽ nhất quán trong cùng dataset.")
        w.extras("Polyline")
    if "mask" in kinds:
        w.heading(3, "Mask / semantic")
        w.rule("sem.no_overlap", "Hai class khác nhau không được cùng chiếm một pixel.")
        w.rule("sem.no_gap", "Không để pixel chưa gán giữa các vùng liền kề đã rõ class; không lấp vùng chưa rõ bằng class suy đoán.")
        w.rule("sem.visible", "Biên bám sát vùng quan sát được; không tô phần bị che khuất hoặc nằm ngoài ảnh.")
        w.rule("sem.thin", "Giữ đúng hình dạng các object mảnh (cột, giá đỡ, xe hai bánh); tránh mask phình rộng.")
        w.extras("Mask / semantic")
    if "skeleton" in kinds:
        w.heading(3, "Skeleton")
        scope, _ = decided(run, "kp_scope")
        if scope == "all":
            w.rule("kp.scope", "Mỗi người nhìn thấy trong ảnh một skeleton.", "kp_scope")
        else:
            w.rule("kp.scope", "Mỗi ảnh chỉ gán một người: người lái. Không có người lái (hoặc không có mặt) thì bỏ qua ảnh; có người thứ hai trong khung thì không gán người đó.", "kp_scope")
        side, _ = decided(run, "kp_lr")
        if side == "anatomical":
            w.rule("kp.lr", "Trái/phải theo NGƯỜI trong ảnh (trái/phải giải phẫu), không theo vị trí trên ảnh.", "kp_lr")
        else:
            w.rule("kp.lr", "Trái/phải theo phía của ẢNH: điểm R nằm phía phải ảnh, điểm L nằm phía trái ảnh, bất kể người quay mặt, quay lưng, vặn mình hay ảnh xoay 90°. Không suy luận theo tay/chân giải phẫu. Với khuôn mặt: 'trai'/'phai' là phía trái/phải của khuôn mặt nhìn thấy trên ảnh, không quan tâm mirror effect.", "kp_lr")
        w.text()
        w.text("| Trạng thái | Cấu hình CVAT | Điều kiện | Toạ độ |")
        w.text("|---|---|---|---|")
        w.text("| Visible | Outside = 0, Occluded = 0 | Nhìn thấy trực tiếp | Phải đúng |")
        w.text("| Occluded | Occluded = 1 | Không thấy trực tiếp, nhưng điểm nằm trong khung hình và suy ra được vị trí từ các điểm liền kề, tư thế và tỉ lệ | Đúng theo ước lượng, nội suy hợp lý |")
        w.text("| Outside | Outside = 1 | Ngoài khung hình, hoặc bị che đến mức không còn căn cứ ước lượng | Không dùng đến |")
        w.text()
        w.rule("kp.vis", "Mỗi điểm phải có một trong ba trạng thái ở bảng trên, dùng property có sẵn của CVAT, không tạo attribute mới.")
        w.rule("kp.hidden", "Không dùng Hidden thay Outside: Hidden chỉ ẩn hiển thị, không lưu vào dữ liệu.")
        w.rule("kp.outside", "Điểm ngoài khung hình là Outside; không kéo điểm về mép ảnh.")
        w.rule("kp.tolerance", "Sai số cho phép: ±5 pixel cho vị trí Visible; ±20 pixel cho vị trí Occluded.")
        w.rule("kp.coincide", "Hai điểm Visible không thể trùng toạ độ: tách đúng vị trí, hoặc đánh lại trạng thái cho điểm không thực sự nhìn thấy (trừ các cặp mí mắt, môi được phép trùng khi mắt nhắm, miệng đóng).")
        size, _ = decided(run, "min_size")
        if size and size != "none":
            w.rule("kp.min_size", f"Người cao dưới {size}px: không gán, đếm vào báo cáo.", "min_size")
        names = {norm(c.get("canonical") or c["name"]) for c in classes if c["shape"] == "skeleton"}
        for set_id, group in skeleton_sets(lib).items():
            if not names & {norm(n) for n in group["labels"]} or not group.get("rules"):
                continue
            w.text()
            w.text(f"**{group['title']}**")
            w.text()
            for item in group["rules"]:
                w.rule(f"kp.{set_id}.{item['id']}", item["text"])
        w.extras("Skeleton")
    if "track" in kinds:
        w.heading(3, "Track")
        w.rule("tr.one_id", "Mỗi đối tượng thật một track ID xuyên suốt video.")
        w.rule("tr.outside", "Bị che hoàn toàn hoặc ra khỏi khung hình: bật outside từ frame đầu tiên không còn thấy.")
        w.rule("tr.reid", "Khuất không quá 60 frame và chắc chắn cùng đối tượng: giữ ID. Lâu hơn hoặc không chắc: track mới, ghi vào báo cáo.")
        w.rule("tr.keyframes", "Keyframe ở frame đầu và cuối, chỗ đổi hướng, đổi tốc độ, đổi kích thước, ngay trước và sau khi bị che. Box nội suy có IoU dưới 0,7 so với đối tượng là trôi: thêm keyframe.")
        w.extras("Track")

    occ_used = any(k in kinds for k in ("rectangle", "track"))
    trunc, _ = decided(run, "truncated_attr")
    if occ_used:
        w.heading(2, "4. Attributes")
        w.rule("attr.occluded", "occluded = true: object vẫn trong cảnh nhưng một phần bị object khác che.")
        if trunc == "yes":
            w.rule("attr.truncated", "truncated = true: object bị cắt bởi biên ảnh, phần còn lại nằm ngoài frame. Có thể bật cùng occluded.", "truncated_attr")
        w.extras("Attributes")

    pairs = [q for q in run["questions"] if q["key"].startswith("pair:")]
    llm_pairs = {frozenset((norm(p["a"]), norm(p["b"]))): p["rule"] for p in (run.get("llm") or {}).get("pairs", [])}
    if pairs:
        w.heading(2, "5. Các cặp class dễ nhầm")
        for q in pairs:
            a, b = q["key"][5:].split("|")
            choice, _ = decided(run, q["key"])
            known = lib.pair_rule(a, b)
            suggested = llm_pairs.get(frozenset((norm(a), norm(b))))
            if choice == "merge":
                text = f"**{a} / {b}**: gộp thành một class."
            elif choice == "rule" and known:
                text = f"**{a} / {b}**: {known}"
            elif choice == "rule" and suggested:
                text = f"**{a} / {b}**: {suggested} *(LLM đề xuất)*"
            elif choice == "rule":
                text = f"**{a} / {b}**: ⟨cần viết quy tắc phân biệt⟩"
            else:
                text = f"**{a} / {b}**: chưa có quy tắc; mọi case tạo Issue UNCERTAIN_CLASS để mentor chốt."
            w.rule(q["key"], f"{text} Ví dụ ở frame: {frames_text(q['examples'])}.", q["key"])
        w.extras("Các cặp class dễ nhầm")

    if s["cases"]:
        w.heading(2, "6. Ca khó trong bộ ảnh này")
        for code in ("UNCERTAIN_CLASS", "UNCERTAIN_BOUNDARY", "UNCERTAIN_SCOPE", "LOW_VISIBILITY"):
            cases = [c for c in s["cases"] if c["code"] == code]
            if cases:
                w.text(f"- **{ISSUE_TEXT[code]}** (`{code}`): frame {frames_text(cases)}. Ví dụ: {cases[0]['text']}")
        w.extras("Ca khó trong bộ ảnh này")

    w.heading(2, "7. Quy tắc khi class hoặc boundary không rõ")
    w.rule("issue.process", "Ưu tiên quy trình review thay vì đoán cho xong: majority voting trong nhóm trước, rồi tạo Issue theo bảng dưới gửi mentor. Không tự đặt quy tắc mới.")
    w.text()
    w.text("| Issue type | Ví dụ |")
    w.text("|---|---|")
    for item in lib.knowledge.get(f"issue_types_{task}") or lib.knowledge.get("issue_types", []):
        w.text(f"| {item['code']} | {item['example']} |")
    w.extras("Quy tắc khi class hoặc boundary không rõ")

    w.heading(2, "8. Checklist trước khi nộp")
    checks = ["Đúng class và shape theo taxonomy; không tự tạo hoặc đổi tên class.",
              "Không bỏ sót object thuộc phạm vi; không annotation trùng lặp."]
    if "rectangle" in kinds:
        checks.append("Box sát object, không vượt biên ảnh; attribute đã gán đúng.")
    if "polygon" in kinds or "polyline" in kinds:
        checks.append("Polygon/polyline bám đúng biên và đường quan sát được, không tự cắt, không nối qua vùng thiếu evidence.")
    if "mask" in kinds:
        checks.append("Không chồng lấn giữa các class; không sót pixel ở vùng đã rõ class.")
    if "skeleton" in kinds:
        side = decided(run, "kp_lr")[0]
        checks.append("Đúng số skeleton, đủ điểm, không thừa; mỗi điểm có trạng thái Visible / Occluded / Outside rõ ràng.")
        checks.append(("Trái/phải đúng theo người trong ảnh" if side == "anatomical" else "R ở bên phải ảnh, L ở bên trái ảnh")
                      + "; thứ tự điểm đúng, đường nối không bắt chéo.")
        checks.append("Vị trí từng điểm trong sai số cho phép; không đặt điểm lên vật che hoặc ra ngoài đối tượng.")
    if "track" in kinds:
        checks.append("Mỗi đối tượng một ID; outside đúng frame; box không trôi giữa hai keyframe.")
    checks += ["Các case không chắc đã tạo Issue cho reviewer.", "Đã check 02 lần toàn dữ liệu trước khi save."]
    for i, item in enumerate(checks):
        w.rule(f"check.{i}", f"[ ] {item}")
    w.extras("Checklist trước khi nộp")

    w.heading(2, "9. Tiêu chí đánh giá của reviewer")
    w.text("| Hạng mục | Reviewer kiểm tra |")
    w.text("|---|---|")
    for item in lib.knowledge.get(f"reviewer_criteria_{task}") or lib.knowledge.get("reviewer_criteria", []):
        w.text(f"| {item['name']} | {item['check']} |")

    w.heading(2, "Phụ lục A. Số liệu khảo sát")
    w.text(f"{s['images']} ảnh mẫu; trung bình {s['instances_per_image']} đối tượng/ảnh; {s['occluded_pct']:.0f}% object bị che, "
           f"{s['truncated_pct']:.0f}% chạm biên ảnh, {s['tiny_pct']:.0f}% rất nhỏ.")
    w.text()
    w.text("| Khái niệm | Nguồn | Số lần | % ảnh | Chiều cao giữa (px) | Model lưỡng lự |")
    w.text("|---|---|---|---|---|---|")
    for c in s["concepts"]:
        if c["instances"]:
            w.text(f"| {c['name']} | {c['source']} | {c['instances']} | {c['image_pct']:.0f}% | {c['height_p50']:.0f} | {c['borderline_pct']:.0f}% |")
    w.text()
    w.text("Số liệu do SAM3 đếm, có thể sót hoặc nhầm; dùng để định hướng, không phải thống kê chính xác.")

    open_q = [q for q in run["questions"] if q["key"] not in run.get("answers", {})]
    if open_q:
        w.heading(2, "Phụ lục B. Câu hỏi còn mở")
        for q in open_q:
            label = next((o["label"] for o in q["options"] if o["id"] == q["recommended"]), q["recommended"])
            w.text(f"- {q['text']} Đang tạm dùng: *{label}*. {q['reason']}")
    findings = run.get("findings") or []
    if findings:
        w.heading(2, "Phụ lục C. Lỗ hổng tool tự phát hiện")
        for f in findings:
            w.text(f"- {f['text']}")
    if w.used or w.dropped:
        w.heading(2, "Phụ lục D. Bài học từ các lần trước đã áp dụng")
        for lesson in w.used.values():
            w.text(f"- `{lesson['id']}` ({lesson['source']}): {lesson['text']}")
        for text in w.dropped:
            w.text(f"- Đã bỏ theo lần trước: {text}")
    return "\n".join(w.lines).strip() + "\n", list(w.used.values())


# -------------------------------------------------------------- label ------
LABEL_PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#469990",
                 "#9a6324", "#800000", "#808000", "#000075", "#fabed4", "#ffd8b1", "#aaffc3", "#dcbeff"]


def skeleton_svg(template: dict) -> str:
    """SVG mô tả skeleton theo định dạng của CVAT: các cạnh rồi tới các điểm; điểm tham chiếu sublabel bằng tên."""
    pts = template["points"]
    parts = []
    for a, b in template["edges"]:
        pa, pb = pts[a - 1], pts[b - 1]
        parts.append(f'<line x1="{pa["x"]}" y1="{pa["y"]}" x2="{pb["x"]}" y2="{pb["y"]}" data-type="edge" '
                     f'data-node-from="{a}" data-node-to="{b}"></line>')
    for i, p in enumerate(pts, 1):
        parts.append(f'<circle r="1.5" cx="{p["x"]}" cy="{p["y"]}" data-type="element node" data-element-id="{i}" '
                     f'data-node-id="{i}" data-label-name="{p["name"]}"></circle>')
    return "".join(parts)


def build_labels(run: dict, lib: Library) -> dict:
    """Bộ label CVAT ứng với taxonomy của bản guideline: dán được vào tab Raw khi tạo task, hoặc thêm vào task đang có.

    Tool chỉ THÊM label còn thiếu. Nó không xoá, không đổi tên, không đổi kiểu label đang có,
    vì những việc đó làm mất hoặc hỏng annotation đã làm.
    """
    task = task_of(run)
    classes = taxonomy(run, lib)
    existing = {norm(l["name"]): l for l in run["labels"]}
    colors = {norm(k): v for k, v in (lib.knowledge.get("colors") or {}).items()}
    templates = {norm(k): v for k, v in (lib.knowledge.get("skeletons") or {}).items()}
    truncated = decided(run, "truncated_attr")[0] == "yes"
    labels, missing, notes = [], [], []
    for i, c in enumerate(classes):
        kind = c["shape"].split()[0]
        key = norm(c["name"])
        spec = {"name": c["name"], "type": kind, "attributes": [],
                "color": colors.get(key) or colors.get(key.split("/")[-1]) or LABEL_PALETTE[i % len(LABEL_PALETTE)]}
        if kind == "rectangle" and task == "detection" and truncated:
            spec["attributes"].append({"name": "truncated", "mutable": False, "input_type": "checkbox",
                                       "default_value": "false", "values": ["false"]})
        if kind == "skeleton":
            template = templates.get(norm(c.get("canonical") or c["name"]))
            if not template:
                if key not in existing:
                    notes.append(f"Chưa có mẫu skeleton cho '{c['name']}': thêm mẫu vào guideline_library/knowledge.yaml hoặc tạo label này bằng tay.")
                    continue
            else:
                spec["color"] = template.get("color") or spec["color"]
                spec["sublabels"] = [{"name": p["name"], "type": "points", "attributes": [], "color": p.get("color") or "#f2c12e"}
                                     for p in template["points"]]
                spec["svg"] = skeleton_svg(template)
        labels.append(spec)
        have = existing.get(key)
        if not have:
            missing.append(spec)
        elif (have.get("type") or "any") not in ("any", kind):
            notes.append(f"Label '{c['name']}' trong task đang là {have['type']}, guideline quy định {kind}. "
                         "Tool không tự đổi kiểu label; người tạo task cần sửa nếu muốn đổi.")
    in_taxonomy = {norm(c["name"]) for c in classes} | {norm(m) for c in classes for m in c["merged"]}
    for key, label in existing.items():
        if key not in in_taxonomy and classes:
            notes.append(f"Label '{label['name']}' có trong task nhưng không thuộc taxonomy của guideline (tool không xoá label).")
    return {"labels": labels, "missing": missing, "notes": notes}


# ------------------------------------------------------------- profile -----
def build_profile(run: dict, lib: Library) -> dict:
    """Profile YAML để tool gợi ý nhãn dùng ngay theo bản guideline này."""
    task = task_of(run)
    classes = taxonomy(run, lib)
    mode = {"semantic": "semantic", "tracking": "tracking", "keypoints": "keypoints"}.get(task, "instance")
    labels = []
    for c in classes:
        shape = c["shape"].split()[0]
        if shape == "skeleton":
            continue  # skeleton dùng profile keypoints có sẵn (cần danh sách điểm)
        entry = {"name": c["name"], "prompts": c["prompts"]}
        if mode in ("instance", "tracking"):
            entry["shape"] = "rectangle" if mode == "tracking" else shape
        if shape == "polyline":
            entry["polyline_mode"] = decided(run, f"line_mode:{c['name']}")[0] or "centerline"
            entry["join_dashes"] = entry["polyline_mode"] != "long_edges"
        labels.append(entry)
    size = decided(run, "min_size")[0]
    rules: dict = {
        "dedup": {"iou": 0.6, "class_margin": 0.15, "existing_iou": 0.7},
        "truncated": {"enabled": decided(run, "truncated_attr")[0] == "yes", "attribute": "truncated", "border_px": 2},
        "occluded": {"enabled": True, "min_overlap": 0.15},
        "low_visibility": {"enabled": True, "min_side_px": int(size) if size and size.isdigit() else 12, "score_margin": 0.08},
        "polygon": {"epsilon": 1.5},
        "lane": {"sample_step_px": 10, "max_gap_px": 30, "join_gap_px": 60, "epsilon": 1.5, "min_length_px": 20},
        "qa": {"bounds_tolerance_px": 0.5, "min_box_side_px": 3, "min_polyline_px": 10, "duplicate_iou": 0.85},
    }
    if mode == "semantic":
        rules["semantic"] = {"output_shape": "mask", "priority": [c["name"] for c in sorted(classes, key=lambda c: c["kind"] != "thing")],
                             "min_region_px": 40, "fill_gaps_px": 150, "respect_existing": True}
    if mode == "tracking":
        rules["tracking"] = {"match_iou": 0.3, "reid_max_gap_frames": 60, "reid_min_appearance": 0.75, "min_track_frames": 5,
                             "amodal": {"enabled": decided(run, "occlusion_box")[0] != "visible"}, "keyframe_iou": 0.8, "max_keyframe_gap": 20}
    return {
        "id": f"auto_{run['id']}", "title": f"Guideline nháp — {run.get('title') or 'job ' + str(run['job'].get('id', ''))}",
        "description": "Sinh tự động từ bản guideline nháp. Kiểm tra prompt và ngưỡng ở tab Label.",
        "mode": mode, "match_labels": [c["name"] for c in classes[:3]],
        "defaults": {"threshold": 0.5, "min_area_px": 30}, "labels": labels, "rules": rules,
    }


# --------------------------------------------------------- tự kiểm tra -----
def self_check(run: dict, lib: Library) -> list[dict]:
    """Tìm chỗ guideline nháp còn mơ hồ hoặc mâu thuẫn với chính bộ ảnh. Đây là GIẢ THUYẾT cho người duyệt xem."""
    s = run["summary"]
    classes = taxonomy(run, lib)
    in_scope = {norm(c["name"]) for c in classes} | {norm(m) for c in classes for m in c["merged"]}
    findings: list[dict] = []

    def add(code: str, text: str, examples: Optional[list] = None, question: Optional[str] = None) -> None:
        findings.append({"code": code, "text": text, "examples": examples or [], "question": question})

    for c in classes:
        concept = next((x for x in s["concepts"] if norm(x["name"]) == norm(c["name"])), {})
        if not c["definition"]:
            add("no_definition", f"Class '{c['name']}' chưa có định nghĩa: hai annotator có thể hiểu khác nhau.", concept.get("examples"))
        if not c["instances"] and not c["new"] and c["prompts"]:
            add("unseen_class", f"Class '{c['name']}' không xuất hiện trong {s['images']} ảnh mẫu: kiểm tra có cần class này, hoặc tool chưa tìm đúng (sửa prompt).")
        if concept.get("borderline_pct", 0) >= 35 and concept.get("instances", 0) >= 5:
            add("fuzzy_class", f"Model lưỡng lự ở {concept['borderline_pct']:.0f}% trường hợp '{c['name']}': ranh giới class có thể chưa rõ, nên thêm ví dụ thuộc/không thuộc.",
                concept.get("examples"))
        if c["shape"] == "polyline" and c["kind"] == "stuff":
            add("shape_mismatch", f"'{c['name']}' là vùng lớn trong ảnh nhưng đang quy định vẽ polyline.", concept.get("examples"), f"shape:{c['name']}")
        if c["shape"] == "rectangle" and c["kind"] == "line":
            add("shape_mismatch", f"'{c['name']}' là vạch mảnh và dài: rectangle sẽ bao nhiều nền thừa.", concept.get("examples"), f"shape:{c['name']}")
    for q in run["questions"]:
        key = q["key"]
        answered = key in run.get("answers", {})
        if key.startswith("pair:"):
            a, b = key[5:].split("|")
            choice, _ = decided(run, key)
            has_rule = lib.pair_rule(a, b) or any({norm(p["a"]), norm(p["b"])} == {norm(a), norm(b)} for p in (run.get("llm") or {}).get("pairs", []))
            if choice == "rule" and not has_rule:
                add("pair_without_rule", f"Chọn giữ hai class '{a}' và '{b}' nhưng chưa viết quy tắc phân biệt.", q["examples"], key)
            elif not answered and not has_rule and norm(a) in in_scope and norm(b) in in_scope:
                add("pair_open", f"'{a}' và '{b}' hay bị nhận lẫn nhau và chưa được chốt cách xử lý.", q["examples"], key)
        if key.startswith("scope:") and not answered and q["recommended"] == "out":
            name = key[6:]
            concept = next((x for x in s["concepts"] if norm(x["name"]) == norm(name)), {})
            if concept.get("image_pct", 0) >= 30:
                add("scope_gap", f"'{name}' có trong {concept['image_pct']:.0f}% ảnh nhưng chưa chốt là trong hay ngoài phạm vi: annotator sẽ phân vân có gán không.",
                    concept.get("examples"), key)
    if s["tiny_pct"] >= 10 and "min_size" not in run.get("answers", {}):
        add("size_open", f"{s['tiny_pct']:.0f}% object rất nhỏ nhưng chưa chốt ngưỡng kích thước tối thiểu.", question="min_size")
    if s["occluded_pct"] >= 20 and "occlusion_box" not in run.get("answers", {}) and task_of(run) in ("detection", "tracking"):
        add("occlusion_open", f"{s['occluded_pct']:.0f}% object bị che nhưng chưa chốt box vẽ phần nhìn thấy hay toàn bộ.", question="occlusion_box")
    return findings
