"""Profile riêng cho từng project, tự tạo từ label của task CVAT và guideline (0.9.4).

Trước 0.9.4 tool chỉ gợi ý tốt khi job khớp một profile viết tay (MOT, pose 17, face 50, BDD...). Job dùng guideline
riêng (face 13 điểm, job gộp box + skeleton, label có attribute riêng, tag ảnh) thì tool không đánh được. Ở đây tool
dựng một profile "mixed" cho project:

1. Nguồn label là chính task CVAT đang mở (có attribute, các điểm của skeleton): không cần file taxonomy.json.
2. Mỗi label lấy cách làm theo thứ tự: profile có sẵn khớp label đó (giữ kinh nghiệm đã viết tay) -> guideline
   (bảng, hoặc LLM nếu bạn cho phép) -> tên label.
3. Skeleton lạ: ghép từng điểm với điểm của model theo tên hoặc định nghĩa (thân: 17 khớp COCO của ViTPose;
   mặt: 478 điểm MediaPipe). Điểm không ghép được thì báo "đánh tay".
4. Attribute: mỗi attribute một luật. Luật đo được từ ảnh thì tool tự điền (chạm mép, bị che, vùng viền ảnh fisheye,
   màu đèn, kích thước nhỏ). Luật cần hiểu nội dung thì tool để giá trị mặc định và nhắc bạn chọn.
5. Tag ảnh: mỗi thuộc tính một luật đo từ ảnh (ảnh hồng ngoại, sáng/tối, fisheye, camera bị che) hoặc để bạn chọn.

Kết quả là bản xem trước: bạn xem từng dòng, thử trên frame đang mở, rồi mới lưu.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from .judge import guide_notes
from .profiles import COCO17, _ascii, match_skeletons, norm

# --------------------------------------------------------------- điểm mặt ---
# Chỉ số MediaPipe Face Landmarker (478 điểm), theo phía của NGƯỜI trong ảnh (R = phải của người = bên trái ảnh khi
# người nhìn thẳng camera). Tool vẽ thử lên ảnh để bạn xác nhận trước khi lưu.
FACE: dict[str, list[int]] = {
    "right_eye_outer": [33], "right_eye_inner": [133], "right_eye_upper": [159], "right_eye_lower": [145], "right_eye_center": [468],
    "left_eye_outer": [263], "left_eye_inner": [362], "left_eye_upper": [386], "left_eye_lower": [374], "left_eye_center": [473],
    "right_brow_outer": [70], "right_brow_mid": [105], "right_brow_inner": [107],
    "left_brow_outer": [300], "left_brow_mid": [334], "left_brow_inner": [336],
    "mouth_right": [61], "mouth_left": [291],
    "lip_upper": [0], "lip_lower": [17], "lip_upper_inner": [13], "lip_lower_inner": [14],
    "nose_tip": [1], "nose_bridge": [168], "chin": [152], "forehead": [10],
    "jaw_right": [234], "jaw_left": [454],
}
FACE_CONCEPTS = sorted(FACE)
BODY_CONCEPTS = list(COCO17)

SIDE_WORDS = {"left": "left", "l": "left", "trai": "left", "right": "right", "r": "right", "phai": "right"}
PART_WORDS = {
    "eye": "eye", "eyes": "eye", "mat": "eye", "brow": "brow", "eyebrow": "brow", "longmay": "brow", "may": "brow",
    "mouth": "mouth", "mieng": "mouth", "lip": "lip", "lips": "lip", "moi": "lip", "nose": "nose", "mui": "nose",
    "chin": "chin", "cam": "chin", "jaw": "jaw", "ham": "jaw", "forehead": "forehead", "tran": "forehead",
    "ear": "ear", "tai": "ear", "shoulder": "shoulder", "vai": "shoulder", "elbow": "elbow", "khuyu": "elbow", "khuytay": "elbow",
    "wrist": "wrist", "cotay": "wrist", "hip": "hip", "hong": "hip", "knee": "knee", "goi": "knee", "ankle": "ankle",
    "cochan": "ankle",
}
POS_WORDS = {
    "outer": "outer", "out": "outer", "ngoai": "outer", "lateral": "outer", "inner": "inner", "in": "inner", "trong": "inner",
    "medial": "inner", "upper": "upper", "top": "upper", "tren": "upper", "lower": "lower", "bottom": "lower", "duoi": "lower",
    "center": "center", "centre": "center", "pupil": "center", "iris": "center", "tam": "center", "giua": "mid", "mid": "mid",
    "middle": "mid", "tip": "tip", "chop": "tip", "corner": "corner", "khoe": "corner", "bridge": "bridge", "song": "bridge",
}
BODY_PARTS = {"shoulder", "elbow", "wrist", "hip", "knee", "ankle", "ear"}
FACE_ONLY_PARTS = {"brow", "mouth", "lip", "chin", "jaw", "forehead"}


def _tokens(name: str) -> list[str]:
    text = _ascii(name).lower()
    text = re.sub(r"([a-z])([0-9])|([0-9])([a-z])", r"\1\3 \2\4", text)
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def _parse(name: str) -> dict:
    """'left_eye_outer', 'Khoé ngoài mắt trái', 'L-eye-out' -> {side, part, pos}."""
    out: dict[str, Optional[str]] = {"side": None, "part": None, "pos": None}
    toks = _tokens(name)
    joined = "".join(toks)
    for tok in toks:
        if tok in SIDE_WORDS and out["side"] is None:
            out["side"] = SIDE_WORDS[tok]
        elif tok in PART_WORDS and out["part"] is None:
            out["part"] = PART_WORDS[tok]
        elif tok in POS_WORDS and out["pos"] in (None, "corner"):
            out["pos"] = POS_WORDS[tok]   # 'khoé ngoài': vị trí cụ thể (ngoài) thắng chữ chung (khoé)
    if out["part"] is None:      # tên viết liền kiểu 'longmaytrai', 'cotaytrai'
        for word in sorted(PART_WORDS, key=len, reverse=True):
            if len(word) >= 4 and word in joined:
                out["part"] = PART_WORDS[word]
                break
    if out["side"] is None:
        for word in ("trai", "phai", "left", "right"):
            if joined.endswith(word) or joined.startswith(word):
                out["side"] = SIDE_WORDS[word]
                break
    return out


def point_concept(name: str, kind: str) -> Optional[str]:
    """Tên điểm -> khái niệm của model (COCO17 cho thân, khoá của FACE cho mặt). Không chắc thì None."""
    p = _parse(name)
    side, part, pos = p["side"], p["part"], p["pos"]
    if kind == "body":
        if part == "nose" or (part is None and "nose" in _tokens(name)):
            return "nose"
        if part in ("eye", "ear", "shoulder", "elbow", "wrist", "hip", "knee", "ankle") and side:
            return f"{side}_{part}"
        return None
    # mặt
    if part == "eye" and side:
        return f"{side}_eye_{pos if pos in ('outer', 'inner', 'upper', 'lower', 'center') else 'center'}"
    if part == "brow" and side:
        return f"{side}_brow_{pos if pos in ('outer', 'inner') else 'mid'}"
    if part in ("mouth", "lip") and side and pos in (None, "corner"):
        return f"mouth_{side}"
    if part in ("mouth", "lip") and pos in ("upper", "lower"):
        inner = "inner" in _tokens(name) or "trong" in _tokens(name)
        return f"lip_{pos}" + ("_inner" if inner else "")
    if part == "nose":
        return "nose_bridge" if pos == "bridge" else "nose_tip"
    if part == "chin":
        return "chin"
    if part == "forehead":
        return "forehead"
    if part == "jaw" and side:
        return f"jaw_{side}"
    return None


def skeleton_kind(point_names: list[str]) -> str:
    parts = [_parse(n)["part"] for n in point_names]
    body = sum(1 for p in parts if p in BODY_PARTS)
    face = sum(1 for p in parts if p in FACE_ONLY_PARTS) + sum(1 for n in point_names if _parse(n)["pos"] in ("outer", "inner", "upper", "lower"))
    return "body" if body > face else "face"


def _flip(concept: str) -> str:
    return concept.replace("left", "\0").replace("right", "left").replace("\0", "right")


def map_skeleton(label: dict, definitions: Optional[dict[str, str]] = None, llm_points: Optional[dict[str, str]] = None,
                 lr: str = "anatomical") -> dict:
    """Ghép các điểm (sublabel) của một skeleton trong task với điểm của model.

    lr = "image": guideline gọi trái/phải theo phía của ảnh. Thân: để model xếp lại (luật pose.lr_convention).
    Mặt: MediaPipe theo phía người, nên đổi bên (giả định người nhìn về camera; tool gắn cờ nhắc kiểm).
    """
    subs = [s["name"] for s in label.get("sublabels") or []]
    definitions = definitions or {}
    llm_points = llm_points or {}
    kind = skeleton_kind(subs + list(definitions.values()))
    allowed = BODY_CONCEPTS if kind == "body" else FACE_CONCEPTS
    points, mapped, how = [], 0, {}
    for name in subs:
        concept = point_concept(name, kind) or point_concept(definitions.get(norm(name), ""), kind)
        source = "tên" if point_concept(name, kind) else ("định nghĩa" if concept else None)
        if concept is None and llm_points.get(norm(name)) in allowed:
            concept, source = llm_points[norm(name)], "LLM"
        if concept and kind == "face" and lr == "image":
            concept = _flip(concept)
        spec: dict[str, Any] = {"name": name}
        if concept:
            mapped += 1
            if kind == "body":
                spec["source"] = concept
            else:
                spec["path"] = list(FACE[concept])
            spec["concept"] = concept
        points.append(spec)
        how[name] = source
    return {"kind": kind, "keypoints": points, "mapped": mapped, "total": len(subs), "how": how}


# ------------------------------------------------------------- attribute ---
def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _ascii(str(text)).lower())


BOOL_TRUE = ("true", "yes", "1", "co")
ATTR_RULES = {
    # luật đo được từ ảnh: tên attribute (đã chuẩn hoá) -> loại luật
    "truncated": "border", "truncation": "border", "bicat": "border", "catmep": "border",
    "occluded": "overlap", "occlusion": "overlap", "biche": "overlap", "chekhuat": "overlap",
    "edgezone": "fisheye_edge", "edge": "fisheye_edge", "vienanh": "fisheye_edge",
}
RULE_TEXT = {
    "border": "tự điền: chạm mép ảnh",
    "overlap": "tự điền: bị vật gần camera hơn che",
    "fisheye_edge": "tự điền: tâm hộp ngoài 80% bán kính vòng ảnh fisheye",
    "light_color": "tự đoán: màu đang sáng trong hộp (kiểm lại)",
    "manual": "bạn chọn (tool để giá trị mặc định)",
    "unknown_default": "bạn chọn; không thấy thì 'unknown'",
}


def attr_rule(label_name: str, attr: dict, llm_rule: Optional[str] = None) -> dict:
    """Luật tự điền cho một attribute của label."""
    key = _plain(attr.get("name", ""))
    values = [str(v) for v in attr.get("values") or []]
    plain_values = {_plain(v) for v in values}
    rule = ATTR_RULES.get(key)
    if rule is None and key == "color" and {"red", "green"} <= plain_values and "light" in _plain(label_name):
        rule = "light_color"
    if rule is None and llm_rule in RULE_TEXT:
        rule = llm_rule
    if rule is None:
        rule = "unknown_default" if "unknown" in plain_values else "manual"
    if rule in ("border", "overlap", "fisheye_edge") and values and not (plain_values & {"true", "false", "yes", "no"}):
        rule = "manual"   # attribute không phải dạng có/không: không tự điền được
    return {"rule": rule, "text": RULE_TEXT[rule]}


TAG_RULES = {
    "modality": "modality", "camera": "camera", "timeofday": "timeofday", "light": "timeofday", "lighting": "timeofday",
    "camerablocked": "blocked", "blocked": "blocked", "weather": "manual", "source": "manual",
}
TAG_TEXT = {
    "modality": "tự đoán: ảnh hồng ngoại hay ảnh màu",
    "timeofday": "tự đoán theo độ sáng (kiểm lại lúc chạng vạng)",
    "camera": "tự đoán: ảnh fisheye hay camera thường (hướng fisheye bạn chọn)",
    "blocked": "tự đoán: ảnh gần như một màu / bị che",
    "manual": "bạn chọn",
}


def tag_rule(label_name: str, attr: dict) -> dict:
    key = _plain(attr.get("name", ""))
    if key in ("value", "values", "giatri"):
        key = _plain(label_name)      # Road: label 'camera' kiểu tag có một thuộc tính 'value'
    rule = TAG_RULES.get(key, "manual")
    return {"rule": rule, "text": TAG_TEXT[rule]}


# ----------------------------------------------------------- guideline ---
def lr_convention(markdown: str) -> Optional[str]:
    text = _ascii(markdown).lower()
    image = re.search(r"(trai|phai)[^.\n]{0,40}theo (phia cua )?anh|theo phia cua anh|image[- ]side|left/right of the image", text)
    person = re.search(r"(trai|phai)[^.\n]{0,40}theo (nguoi|giai phau)|cua nguoi trong anh|anatomical|theo nguoi trong anh", text)
    if person and not image:
        return "anatomical"
    if image and not person:
        return "image"
    return None


def min_size_px(markdown: str, label_name: str) -> Optional[int]:
    """'Gán mọi cụm đèn và tấm biển cao từ 8 px', 'Mặt rộng dưới 60 px thì chỉ vẽ box face'."""
    text = _ascii(markdown).lower().replace("*", "").replace("`", "")
    name = _ascii(label_name).lower()
    for line in text.splitlines():
        if not re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", line) and name.replace("_", " ") not in line:
            continue
        m = re.search(r"(?:tu|duoi|toi thieu|it nhat|at least|>=|under|below)\s*(\d{1,3})\s*px", line)
        if m:
            return int(m.group(1))
    return None


STRATEGIES = ("instance", "region", "ground", "edge", "centerline")
STRATEGY_TEXT = {
    "region": "gộp các mảnh gần nhau thành một vùng, lấy viền ngoài (vd cả vùng vạch qua đường là một polygon)",
    "ground": "vùng mặt đường: lấy phần đường liền với vị trí xe, trừ xe và người; các làn khác tách riêng",
    "edge": "mép của vùng mặt đường (nơi không có vạch sơn), kèm cờ để bạn kiểm",
    "centerline": "đường giữa của vệt sơn, nối các đoạn thẳng hàng",
}
STRATEGY_PROMPTS = {"ground": ["road"], "edge": ["curb"], "region": [], "centerline": []}


def shape_strategy(name: str, shape: str, definition: str) -> Optional[str]:
    """Đoán cách dựng hình từ tên + định nghĩa (dùng khi không có Claude; có Claude thì Claude chọn khi đọc guideline)."""
    text = f"{name} {definition}".lower().replace("_", " ")
    if shape == "polygon":
        if re.search(r"crosswalk|zebra|ngựa v[ằạa]n|bao các|cả vùng|một polygon bao|whole area|enclos", text):
            return "region"
        if re.search(r"drivable|free ?space|road ?surface|mặt đường|vùng lái|đi được", text):
            return "ground"
    if shape == "polyline":
        if re.search(r"\bcurb|kerb|vỉa|lề đường|road ?edge|mép đường", text):
            return "edge"
    return None


LLM_SCHEMA = (
    'Trả về DUY NHẤT JSON: {"lr_convention": "anatomical"|"image"|null, '
    '"labels": [{"name": tên đúng như task, "prompts": [1-3 cụm tiếng Anh ngắn cho SAM3, rỗng nếu là skeleton/tag], '
    '"definition": định nghĩa ngắn tiếng Việt, "min_px": số px tối thiểu hoặc null, '
    '"attributes": [{"name": tên attribute, "rule": một trong ' + json.dumps(sorted(RULE_TEXT)) + '}], '
    '"points": [{"name": tên điểm đúng như task, "concept": một trong DANH SÁCH ĐIỂM hoặc null}], '
    '"strategy": cách dựng hình, một trong "instance" (mỗi vật một hình), "region" (gộp các mảnh thành một vùng), '
    '"ground" (vùng mặt đường liền với xe), "edge" (mép của vùng mặt đường), "centerline" (đường giữa vệt sơn)}], '
    '"exclude": [những thứ guideline dặn KHÔNG gán, ví dụ poster, gương], '
    '"tracking": {"reid_max_gap_frames": số hoặc null, "max_occluded_keyframes": số hoặc null}}'
)


def llm_extract(llm, markdown: str, task_labels: list[dict]) -> dict:
    """Nhờ LLM đọc guideline (chỉ văn bản) để điền những chỗ luật không đọc được."""
    from .guideline.llm import parse_json

    labels = [{"name": l["name"], "type": l.get("type"), "attributes": [a.get("name") for a in l.get("attributes") or []],
               "points": [s["name"] for s in l.get("sublabels") or []]} for l in task_labels]
    ask = (f"GUIDELINE:\n{markdown[:24000]}\n\nLABEL CỦA TASK CVAT:\n{json.dumps(labels, ensure_ascii=False)}\n\n"
           f"DANH SÁCH ĐIỂM THÂN: {', '.join(BODY_CONCEPTS)}\nDANH SÁCH ĐIỂM MẶT: {', '.join(FACE_CONCEPTS)}\n\n{LLM_SCHEMA}")
    system = ("Bạn đọc guideline gán nhãn và điền cấu hình cho công cụ gợi ý nhãn. Chỉ dùng thông tin có trong guideline; "
              "không chắc thì để null. Không đổi tên label, attribute hay điểm.")
    data = parse_json(llm.complete(system, ask, max_tokens=6000))
    return data if isinstance(data, dict) else {}


# -------------------------------------------------------------- dựng ---
DEFAULT_TRACKING = {"match_iou": 0.3, "reid_max_gap_frames": 60, "reid_min_appearance": 0.75, "reid_max_distance": 1.5,
                    "min_track_frames": 5, "smooth_window": 5, "amodal": {"enabled": True, "shrink_ratio": 0.8, "history": 15},
                    "occluded_overlap": 0.15, "merge_iou": 0.6, "keyframe_iou": 0.8, "max_keyframe_gap": 20,
                    "dense_keyframes_per_100": 50}
DEFAULT_RULES = {
    "dedup": {"iou": 0.6, "class_margin": 0.15, "existing_iou": 0.7},
    "occluded": {"enabled": True, "min_overlap": 0.15},
    "low_visibility": {"enabled": True, "min_side_px": 12, "score_margin": 0.08},
    "polygon": {"epsilon": 1.5},
    "qa": {"bounds_tolerance_px": 0.5, "min_box_side_px": 3, "min_polyline_px": 10, "duplicate_iou": 0.85},
}


def _known_entry(profiles: dict[str, dict], label: dict) -> tuple[Optional[dict], Optional[dict]]:
    """Mục của profile có sẵn (không phải profile project khác) khớp label này: (entry, profile)."""
    cvat_type = (label.get("type") or "any").lower()
    best = (None, None)
    for profile in profiles.values():
        if profile.get("auto_project") or profile.get("id", "").startswith("proj_"):
            continue
        if cvat_type == "skeleton":
            if profile.get("mode") != "keypoints":
                continue
            found = match_skeletons([label], profile["labels"])
            if 0 in found:
                entry, how = found[0]
                if how in ("exact", "similar", "ids"):
                    return entry, profile
        elif profile.get("mode") in ("instance", "semantic", "tracking"):
            entry = profile["_index"].get(norm(label["name"]))
            if entry and entry.get("shape") != "skeleton":
                if best[0] is None or profile.get("mode") == "instance":
                    best = (entry, profile)
    return best


def _prompt(name: str) -> list[str]:
    text = _ascii(name).replace("_", " ").replace("-", " ").strip().lower()
    return [text] if re.fullmatch(r"[a-z0-9 ]{2,40}", text) else []


def build(task_labels: list[dict], profiles: dict[str, dict], *, project: Optional[dict] = None, markdown: str = "",
          analysis: Optional[dict] = None, llm_data: Optional[dict] = None,
          prompt_lookup: Optional[Callable[[str], Optional[list[str]]]] = None) -> dict:
    """Dựng profile của project. Trả {profile, rows, warnings, lr}: rows là bảng xem trước cho panel."""
    project = project or {}
    llm_data = llm_data or {}
    analysis = analysis or {}
    defs = {norm(l["name"]): l.get("definition", "") for l in analysis.get("labels") or []}
    llm_labels = {norm(l.get("name", "")): l for l in llm_data.get("labels") or [] if isinstance(l, dict)}
    lr = llm_data.get("lr_convention") if llm_data.get("lr_convention") in ("anatomical", "image") else None
    lr = lr or lr_convention(markdown)
    lr_known = lr is not None          # guideline nói rõ trái/phải theo người hay theo ảnh
    lr = lr or "anatomical"
    rules: dict[str, Any] = json.loads(json.dumps(DEFAULT_RULES))
    entries, rows, warnings = [], [], []
    used_profiles: set[str] = set()
    for label in task_labels:
        name, cvat_type = label["name"], (label.get("type") or "any").lower()
        hint = llm_labels.get(norm(name), {})
        known, known_profile = _known_entry(profiles, label)
        row: dict[str, Any] = {"label": name, "type": cvat_type, "status": "ok", "how": "", "source": "", "details": []}
        if cvat_type == "tag":
            tag_rules = {a["name"]: tag_rule(name, a) for a in label.get("attributes") or []}
            entry = {"name": name, "shape": "tag", "tag_rules": {k: v["rule"] for k, v in tag_rules.items()}}
            auto = sum(1 for v in tag_rules.values() if v["rule"] != "manual")
            row.update(how=f"Tag ảnh: tool đoán {auto}/{len(tag_rules)} thuộc tính", source="tên thuộc tính",
                       details=[{"name": k, "text": v["text"]} for k, v in tag_rules.items()],
                       status="ok" if auto else "warn")
            if not tag_rules:
                row.update(how="Tag ảnh không có thuộc tính: tool gợi ý gắn tag cho mọi frame", status="ok")
        elif cvat_type == "skeleton":
            if known is not None:
                entry = dict({k: v for k, v in known.items() if not k.startswith("_")}, name=name,
                             aliases=list({*known.get("aliases", []), known["name"]} - {name}))
                for key in ("pose", "face"):
                    if key in (known_profile.get("rules") or {}):
                        rules.setdefault(key, json.loads(json.dumps(known_profile["rules"][key])))
                if entry.get("kind", "body") == "body" and "pose" in rules:
                    before = rules["pose"].get("lr_convention", "anatomical")
                    if lr_known and before != lr:
                        rules["pose"]["lr_convention"] = lr
                        warnings.append(f"'{name}': profile có sẵn gọi trái/phải theo {'ảnh' if before == 'image' else 'người'}, "
                                        f"guideline này theo {'ảnh' if lr == 'image' else 'người'}: tool dùng quy ước của guideline.")
                    elif not lr_known:
                        warnings.append(f"'{name}': guideline không nói rõ trái/phải theo người hay theo ảnh; tool dùng quy ước "
                                        f"của profile có sẵn ({'theo ảnh' if before == 'image' else 'theo người'}). Kiểm tra lại.")
                    rules["pose"]["max_people"] = 0 if lr_known else rules["pose"].get("max_people", 0)
                used_profiles.add(known_profile["id"])
                row.update(how=f"Skeleton {entry.get('kind', 'body')} theo profile '{known_profile.get('title', known_profile['id'])}'",
                           source="profile có sẵn")
            else:
                llm_points = {norm(p.get("name", "")): p.get("concept") for p in hint.get("points") or [] if isinstance(p, dict)}
                mapped = map_skeleton(label, {norm(k): v for k, v in (hint.get("point_definitions") or {}).items()}, llm_points, lr)
                kind = mapped["kind"]
                entry = {"name": name, "shape": "skeleton", "kind": kind, "prompts": ["person"] if kind == "body" else [],
                         "keypoints": [{k: v for k, v in p.items() if k != "concept"} for p in mapped["keypoints"]]}
                min_px = hint.get("min_px") or min_size_px(markdown, name)
                if kind == "body":
                    pose = rules.setdefault("pose", {"lr_convention": lr, "max_people": 0, "visible_score": 0.6, "occluded_score": 0.35})
                    if min_px:
                        pose["min_person_height_px"] = int(min_px)
                else:
                    face = rules.setdefault("face", {"max_faces": 0, "min_face_px": 20,
                                                     "yaw": {"nose": 1, "left_corner": 33, "right_corner": 263, "ratio": 2.5,
                                                             "occluded_ratio": 5.0}})
                    if min_px:
                        face["min_face_px"] = int(min_px)
                missing = [p["name"] for p in mapped["keypoints"] if "concept" not in p]
                row.update(how=f"Skeleton {'thân (ViTPose)' if kind == 'body' else 'mặt (MediaPipe)'}: "
                               f"{mapped['mapped']}/{mapped['total']} điểm tool đặt được",
                           source="tên điểm" + (" + LLM" if llm_points else ""),
                           details=[{"name": p["name"], "text": (p.get("concept") or "đánh tay") + (f" ({mapped['how'][p['name']]})" if mapped["how"].get(p["name"]) else "")}
                                    for p in mapped["keypoints"]],
                           status="ok" if not missing else ("warn" if mapped["mapped"] else "bad"))
                if kind == "face" and lr == "image":
                    warnings.append(f"'{name}': guideline gọi trái/phải theo ảnh; tool đổi bên giả định người nhìn về camera. "
                                    "Mặt quay ngang thì kiểm kỹ.")
                if missing:
                    row["note"] = f"{len(missing)} điểm tool không đặt được, bạn đánh tay: {', '.join(missing[:6])}"
        elif cvat_type == "cuboid":
            from . import lidar

            entry = {"name": name, "shape": "cuboid"}
            known_class = lidar.class_key(name) in (lidar.parse_size_table(markdown) or lidar.DEFAULT_SIZES)
            row.update(how=("Cuboid 3D: trên job CVAT 3D tool dựng từ point cloud theo bảng kích thước" if known_class
                            else "Cuboid 3D: tên label không có trong bảng kích thước, tool chưa đoán được class này"),
                       source="bảng kích thước của guideline" if lidar.parse_size_table(markdown) else "bảng mặc định (guideline 3D Cuboid)",
                       status="ok" if known_class else "warn",
                       note="" if known_class else "Thêm dòng kích thước cho class này vào bảng của guideline rồi tạo lại profile.")
        elif cvat_type in ("points", "ellipse"):
            entry = {"name": name, "shape": cvat_type}
            row.update(how=f"Kiểu '{cvat_type}' chưa được hỗ trợ: bạn đánh tay", status="bad")
        else:
            shape = cvat_type if cvat_type in ("rectangle", "polygon", "polyline", "mask") else None
            if known is not None:
                entry = dict({k: v for k, v in known.items() if not k.startswith("_")}, name=name)
                if shape:
                    entry["shape"] = shape
                used_profiles.add(known_profile["id"])
                row.update(how=f"SAM3 tìm '{', '.join(entry.get('prompts') or []) or name}' (theo profile '{known_profile.get('title', known_profile['id'])}')",
                           source="profile có sẵn")
            else:
                prompts = [str(p).strip() for p in hint.get("prompts") or [] if str(p).strip()][:3]
                source = "LLM" if prompts else ""
                if not prompts and prompt_lookup:
                    prompts = prompt_lookup(name) or []
                    source = "profile có sẵn" if prompts else ""
                if not prompts:
                    prompts = _prompt(name)
                    source = "tên label" if prompts else ""
                entry = {"name": name, "shape": shape or (analysis_shape(analysis, name) or "rectangle"), "prompts": prompts}
                if entry["shape"] == "polyline":
                    entry["polyline_mode"], entry["join_dashes"] = "centerline", True
                # 0.9.5: cách dựng hình theo định nghĩa trong guideline (gộp vùng, mặt đường, mép đường), không theo tên label
                strategy = hint.get("strategy") if hint.get("strategy") in STRATEGIES else None
                strategy = strategy or shape_strategy(name, entry["shape"], str(hint.get("definition") or defs.get(norm(name)) or ""))
                if strategy and strategy != "instance":
                    entry["strategy"] = strategy
                    extra = STRATEGY_PROMPTS.get(strategy) or []
                    if source in ("tên label", "") or not prompts:
                        entry["prompts"] = list(dict.fromkeys(extra + prompts))[:3]
                    row["details"].append({"name": "cách dựng hình", "text": STRATEGY_TEXT[strategy]})
                row.update(how=f"SAM3 tìm '{', '.join(prompts)}'" if prompts else "Chưa có prompt tiếng Anh: điền ở tab Label",
                           source=source, status="ok" if prompts else "bad")
            min_px = hint.get("min_px") or min_size_px(markdown, name)
            if min_px:
                entry["min_side_px"] = int(min_px)
                row["details"].append({"name": "kích thước tối thiểu", "text": f"{int(min_px)} px (nhỏ hơn: không gợi ý)"})
        # attribute (mọi loại trừ tag)
        if cvat_type != "tag" and label.get("attributes"):
            llm_attr = {_plain(a.get("name", "")): a.get("rule") for a in hint.get("attributes") or [] if isinstance(a, dict)}
            attr_rules = {a["name"]: attr_rule(name, a, llm_attr.get(_plain(a["name"]))) for a in label["attributes"]}
            entry["attr_rules"] = {k: v["rule"] for k, v in attr_rules.items()}
            row["details"] += [{"name": f"attribute {k}", "text": v["text"]} for k, v in attr_rules.items()]
        if hint.get("definition") or defs.get(norm(name)):
            entry["definition"] = str(hint.get("definition") or defs.get(norm(name)))[:400]
        entries.append(entry)
        rows.append(row)
    tracking = dict(DEFAULT_TRACKING)
    track_hint = llm_data.get("tracking") or {}
    if isinstance(track_hint, dict) and track_hint.get("reid_max_gap_frames"):
        tracking["reid_max_gap_frames"] = int(track_hint["reid_max_gap_frames"])
    if isinstance(track_hint, dict) and track_hint.get("max_occluded_keyframes"):
        tracking["max_occluded_frames"] = int(track_hint["max_occluded_keyframes"])
    rules["tracking"] = tracking
    if any((l.get("type") or "").lower() == "cuboid" for l in task_labels):
        from . import lidar

        sizes = lidar.parse_size_table(markdown)
        lid: dict[str, Any] = {"max_occluded_frames": 6, "size_tolerance": 0.05}
        if isinstance(track_hint, dict) and track_hint.get("max_occluded_keyframes"):
            lid["max_occluded_frames"] = int(track_hint["max_occluded_keyframes"])
        if sizes:
            lid["sizes"] = {k: [list(map(list, r)) for r in v] for k, v in sizes.items()}
        rules["lidar"] = lid
    for pid in sorted(used_profiles):
        base = profiles.get(pid) or {}
        for key in ("lane", "semantic", "rider", "dedup", "qa", "low_visibility", "polygon", "occluded"):
            if key in (base.get("rules") or {}) and key not in ("dedup",):
                rules[key] = base["rules"][key]
    if any(e.get("shape") == "skeleton" and e.get("kind", "body") == "body" for e in entries) and "pose" in rules:
        rules["pose"].setdefault("lr_convention", lr)
        min_px = next((min_size_px(markdown, e["name"]) for e in entries if e.get("shape") == "skeleton"
                       and e.get("kind", "body") == "body" and min_size_px(markdown, e["name"])), None)
        if min_px:
            rules["pose"]["min_person_height_px"] = int(min_px)
    key = project.get("project_id") or project.get("task_id") or "local"
    scope = "project" if project.get("project_id") else "task"
    title = project.get("name") or (analysis.get("title") if analysis else "") or f"{scope} {key}"
    profile = {
        "id": f"proj_{scope[0]}{key}", "title": f"Project: {title}"[:80],
        "description": "Tự tạo từ label của task" + (f" và guideline {analysis.get('file')}" if analysis.get("file") else "")
                       + ". Sửa ở tab Label hoặc tạo lại.",
        "mode": "mixed", "auto_project": True,
        "cvat_project": project.get("project_id"), "cvat_task": None if project.get("project_id") else project.get("task_id"),
        "guideline": analysis.get("file") or "", "guideline_id": analysis.get("id") or "",
        "lr_convention": lr,
        "defaults": {"threshold": 0.5, "min_area_px": 30, "join_dashes": True},
        "labels": entries, "rules": rules,
        "checklist": analysis.get("checklist") or [],
        "exclude": [str(x) for x in llm_data.get("exclude") or []][:20],
        # 0.9.5: các đoạn guideline Claude cần khi xem ảnh (occluded/outside, thuộc tính, tag, những thứ không gán)
        "guide_notes": guide_notes(markdown) if markdown else "",
    }
    if not markdown:
        warnings.append("Chưa có guideline: tool dựng theo label của task và profile có sẵn. Thêm guideline để tool hiểu "
                        "định nghĩa, kích thước tối thiểu và quy ước trái/phải.")
    return {"profile": profile, "rows": rows, "warnings": warnings, "lr": lr}


def analysis_shape(analysis: dict, name: str) -> Optional[str]:
    for l in analysis.get("labels") or []:
        if norm(l.get("name", "")) == norm(name):
            return l.get("shape")
    return None
