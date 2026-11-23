"""Nạp một guideline có sẵn (.docx / .md / .txt) vào tool.

Một guideline mới cho task mới đi vào tool theo ba chỗ:
  1. guideline_library/reference/<id>.md      văn bản đầy đủ: tài liệu nền cho phần soạn guideline (và cho LLM nếu bật)
  2. guideline_library/imported/<id>.yaml     phần đã rút ra: label + shape + định nghĩa, Issue type, checklist, tiêu chí reviewer
  3. profiles/gl_<id>.yaml                    profile để tab Gợi ý dùng ngay (label, shape, prompt tiếng Anh cho SAM3)

Phần rút ra làm bằng luật đọc bảng (không cần LLM), vì guideline của khoá học viết theo cùng một khung:
bảng taxonomy (nhóm | CVAT shape | class), bảng định nghĩa (class | khái niệm), bảng Issue type, bảng checklist,
bảng tiêu chí reviewer. Có LLM (và người dùng cho phép gửi văn bản) thì LLM bổ sung prompt tiếng Anh và định nghĩa còn thiếu.
Người dùng luôn xem và sửa bản rút ra trước khi lưu.
"""
from __future__ import annotations

import io
import re
import time
import unicodedata
import zipfile
from typing import Callable, Optional
from xml.etree import ElementTree as ET

from ..i18n import tr
from .library import norm

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_BYTES = 30 * 1024 * 1024

SHAPES = [  # (từ khoá, shape của CVAT). Thứ tự quan trọng: "polyline" phải xét trước "polygon"/"line".
    (r"skeleton|keypoint|landmark", "skeleton"),
    (r"polyline|poly-line|đường gấp khúc", "polyline"),
    (r"polygon|đa giác", "polygon"),
    (r"bounding\s*box|bbox|rectangle|hình chữ nhật", "rectangle"),
    (r"\bmask\b|brush|segmentation mask", "mask"),
    (r"\bpoints?\b", "points"),
    (r"\bcuboid\b", "cuboid"),
    (r"\bellipse\b", "ellipse"),
    (r"^tag\b|\btag \(", "tag"),      # "tag (nhãn cho cả ảnh)" trong cột dạng CVAT
]
# Cột tên label, theo thứ tự ưu tiên. "Nhóm nhãn" là cột nhóm, không phải tên label.
NAME_HEADS = [r"class|áp dụng", r"sub-?category", r"skeleton|label|keypoint", r"^(?!.*nhóm).*(nhãn|lớp|đối tượng|tên)"]
DEF_HEAD = r"khái niệm|định nghĩa|definition|mô tả|description|nguyên tắc"
SHAPE_HEAD = r"shape|loại|kiểu|type|dạng"


# ------------------------------------------------------------- đọc file ---
def _docx_markdown(data: bytes) -> str:
    """Chuyển .docx sang markdown bằng thư viện chuẩn: tiêu đề, gạch đầu dòng, bảng. Bỏ hình."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError("File .docx hỏng hoặc không phải file Word (.docx). File .doc đời cũ: mở bằng Word rồi lưu lại thành .docx.") from exc

    def text_of(node) -> str:
        parts = []
        for el in node.iter():
            if el.tag == W + "t" and el.text:
                parts.append(el.text)
            elif el.tag in (W + "tab",):
                parts.append(" ")
            elif el.tag in (W + "br", W + "cr"):
                parts.append(" ")
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def paragraph(p) -> str:
        text = text_of(p)
        if not text:
            return ""
        style = p.find(f"{W}pPr/{W}pStyle")
        name = (style.get(W + "val") if style is not None else "") or ""
        level = re.search(r"(?:heading|title|tiêuđề)\s*(\d)?", name.replace(" ", "").lower())
        if level:
            return "#" * (int(level.group(1) or 1) if level.group(1) else 1) + " " + text
        if p.find(f"{W}pPr/{W}numPr") is not None:
            return "- " + text
        return text

    def cell(tc) -> str:
        # Một ô có thể có nhiều đoạn (mỗi tên label một dòng): nối bằng "; " để không dính "longmaytrailongmayphai".
        return "; ".join(t for t in (text_of(p) for p in tc.iter(W + "p")) if t).replace("|", "/")

    def table(tbl) -> str:
        rows = []
        for tr in tbl.findall(f"{W}tr"):
            cells = [cell(tc) for tc in tr.findall(f"{W}tc")]
            if any(cells):
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(lines)

    out: list[str] = []

    def walk(node) -> None:
        # Bảng và đoạn có thể nằm trong khối bọc (content control <w:sdt>...), không chỉ ngay dưới <w:body>.
        for child in list(node):
            if child.tag == W + "p":
                out.append(paragraph(child))
            elif child.tag == W + "tbl":
                out.append(table(child))
            else:
                walk(child)

    body = root.find(W + "body")
    walk(body if body is not None else root)
    return "\n\n".join(x for x in out if x).strip() + "\n"


def read_document(filename: str, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ValueError(f"File quá lớn ({len(data) // 1024 // 1024} MB). Giới hạn {MAX_BYTES // 1024 // 1024} MB; thử xoá bớt hình trong file.")
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "docx":
        return _docx_markdown(data)
    if ext in ("md", "txt", "markdown"):
        for enc in ("utf-8-sig", "utf-16", "cp1258", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
    if ext == "pdf":
        try:
            from pypdf import PdfReader  # gói tuỳ chọn
        except ImportError as exc:
            raise ValueError("Tool chưa đọc được PDF (thiếu gói pypdf). Mở PDF bằng Word rồi lưu thành .docx, hoặc chạy: pip install pypdf") from exc
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    raise ValueError("Chỉ nhận file .docx, .md, .txt (hoặc .pdf nếu đã cài pypdf).")


# ------------------------------------------------------------ đọc bảng ----
def _clean(cell: str) -> str:
    return re.sub(r"\s+", " ", cell.replace("**", "").replace("__", "").replace("`", "")).strip()


def tables(markdown: str) -> list[list[list[str]]]:
    out, current = [], []
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            cells = [_clean(c) for c in s[1:-1].split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) or not c for c in cells) and any(cells):
                continue  # dòng kẻ |---|---|
            current.append(cells)
        elif current:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


def _col(header: list[str], pattern: str, exclude: Optional[int] = None) -> Optional[int]:
    for i, h in enumerate(header):
        if i != exclude and re.search(pattern, h.lower()):
            return i
    return None


def _shape_in(text: str) -> Optional[str]:
    low = text.lower()
    for pattern, shape in SHAPES:
        if re.search(pattern, low):
            return shape
    return None


def _split_names(cell: str) -> list[str]:
    cell = re.sub(r"\(.*?\)", "", cell)
    parts = re.split(r"[,;\n]|\s+và\s+|\s+and\s+", cell)
    out = []
    for p in parts:
        p = re.sub(r"^tag\s+", "", p.strip(" .:-–"), flags=re.I)
        if 1 < len(p) <= 40 and not re.match(r"\d", p) and len(p.split()) <= 5:   # "2 / 3 – Eye" là số hiệu điểm, không phải label
            out.append(p)
    return out


def _attributes(cell: str) -> list[dict]:
    """"color red, yellow · type vehicle, pedestrian · edge_zone (ô tick)" -> [{name, values, checkbox?}]."""
    cell = _clean(cell or "")
    if not cell or re.fullmatch(r"(không có|none|-|—)", cell.lower()):
        return []
    out = []
    for part in re.split(r"\s+·\s+|;\s*", cell):
        part = part.strip()
        if ":" in part:     # "eye_left, eye_right: open, closed" -> hai thuộc tính cùng bộ giá trị
            head, rest = part.split(":", 1)
            names = re.findall(r"[A-Za-z][A-Za-z0-9_]*", head)
        else:
            m = re.match(r"([A-Za-z][A-Za-z0-9_]*)\s*(.*)$", part)
            if not m:
                continue
            names, rest = [m.group(1)], m.group(2)
        rest = rest.strip()
        if not names:
            continue
        if re.search(r"ô tick|checkbox|tick", rest.lower()) or not rest:
            out += [{"name": n, "values": ["false", "true"], "checkbox": True} for n in names]
            continue
        rest = re.sub(r"\(.*?\)", "", rest)
        values = [v.strip() for v in rest.split(",") if v.strip() and re.match(r"^[A-Za-z0-9_\-]+$", v.strip())]
        if values:
            out += [{"name": n, "values": values} for n in names]
    return out


SKELETON_NAMES = [(r"người|body|pose|person|thân", "person"), (r"mặt|face", "face"), (r"tay|hand", "hand")]


def skeleton_sections(markdown: str) -> list[dict]:
    """Mục "## Skeleton <tên>: N điểm" kèm bảng tên điểm (ô có `ten_diem`). Tên label: `x` trong câu "Mẫu `x`"
    của mục, nếu không thì theo chữ trong tiêu đề (người → person, mặt → face)."""
    out = []
    parts = re.split(r"^(#{2,4}\s+.*)$", markdown, flags=re.M)
    for i in range(1, len(parts) - 1, 2):
        head, body = parts[i], parts[i + 1]
        if not re.search(r"skeleton|keypoint|điểm", head.lower()):
            continue
        points = []
        for tbl in tables(body):
            hdr = [h.lower() for h in tbl[0]]
            cols = [k for k, h in enumerate(hdr) if re.search(r"tên|name", h)]
            for r in tbl[1:]:
                for k in cols:
                    if k < len(r):
                        m = re.fullmatch(r"([a-z][a-z0-9_]{1,40})", r[k].strip())
                        if m:
                            points.append(m.group(1))
            if points:
                break
        if len(points) < 3:
            continue
        m = re.search(r"(?:mẫu|label|nhãn)\s+`([A-Za-z][\w\-]{1,30})`", body, re.I)
        name = m.group(1) if m and m.group(1) not in points else None
        if not name:
            name = next((n for pat, n in SKELETON_NAMES if re.search(pat, head.lower())), None)
        if name:
            out.append({"name": name, "points": points})
    return out


def _ascii(text: str) -> str:
    text = str(text).replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")


def guess_task(markdown: str, labels: list[dict]) -> tuple[str, str]:
    low = markdown.lower()
    shapes = [l["shape"] for l in labels]
    if "skeleton" in shapes or "points" in shapes:
        return "keypoints", "guideline có label kiểu skeleton/points"
    kp = sum(low.count(w) for w in ("keypoint", "skeleton", "landmark"))
    if kp >= 3:
        return "keypoints", f"nhắc tới keypoint/skeleton {kp} lần"
    track = sum(low.count(w) for w in ("track id", "tracking", "mota", "idf1", "keyframe", "video"))
    if track >= 3:
        return "tracking", f"nhắc tới tracking/ID/keyframe {track} lần"
    semantic = sum(low.count(w) for w in ("semantic", "mỗi pixel", "every pixel", "per-pixel"))
    if semantic >= 2 or (shapes and shapes.count("mask") > len(shapes) / 2):
        return "semantic", "guideline semantic segmentation (mỗi pixel một class)"
    return "instance", "mỗi đối tượng một shape riêng"


def analyze(markdown: str, filename: str, prompt_lookup: Callable[[str], Optional[list[str]]]) -> dict:
    """Rút label, shape, định nghĩa, Issue type, checklist, tiêu chí reviewer từ các bảng của guideline."""
    found: dict[str, dict] = {}
    definitions: dict[str, str] = {}
    issues, checklist, criteria, warnings = [], [], [], []

    for tbl in tables(markdown):
        header = [h.lower() for h in tbl[0]]
        rows = tbl[1:]
        joined = " ".join(header)
        # Issue type: ô đầu là mã viết hoa (UNCERTAIN_CLASS ...)
        coded = [r for r in tbl if r and re.fullmatch(r"[A-Z][A-Z0-9_]{4,}", r[0])]
        if len(coded) >= 2:
            issues += [{"code": r[0], "example": " ".join(c for c in r[1:] if c)} for r in coded]
            continue
        if "checklist" in joined or any("☐" in c for r in rows for c in r):
            for r in rows:
                item = next((c for c in r if c and c not in ("☐", "✓", "☑", "✔")), "")
                if item:
                    checklist.append(item)
            continue
        if "reviewer" in joined or ("hạng mục" in joined and len(header) == 2):
            criteria += [{"name": r[0], "check": r[1]} for r in rows if len(r) >= 2 and r[0] and r[1]]
            continue
        name_col = next((c for c in (_col(header, pattern) for pattern in NAME_HEADS) if c is not None), None)
        def_col = _col(header, DEF_HEAD, exclude=name_col)
        shape_col = _col(header, r"cvat shape|shape|dạng( trong)? cvat", exclude=name_col)
        if shape_col is None:
            shape_col = _col(header, SHAPE_HEAD, exclude=name_col)
        if name_col is None:
            continue
        attr_col = _col(header, r"thuộc tính|attribute", exclude=name_col)
        header_shape = _shape_in(header[name_col])   # "Skeleton (CVAT shape)": cột tên kiêm luôn shape
        last_shape = None
        for r in rows:
            if name_col >= len(r) or not r[name_col]:
                continue
            shape = (_shape_in(r[shape_col]) if shape_col is not None and shape_col < len(r) else None) or header_shape
            if shape is None and shape_col is not None and shape_col < len(r) and not r[shape_col]:
                shape = last_shape   # ô shape trống ở dòng tiếp nối (ô gộp trong Word)
            last_shape = shape or last_shape
            names = _split_names(r[name_col]) if shape or def_col is not None else []
            if def_col is not None and def_col < len(r) and r[def_col]:
                for name in names:   # "longmaytrai (0–4), longmayphai (5–9)" dùng chung một định nghĩa
                    definitions.setdefault(norm(name), r[def_col])
            attrs = _attributes(r[attr_col]) if attr_col is not None and attr_col < len(r) else []
            if shape == "tag" and len(attrs) >= 2 and not any(re.fullmatch(r"[A-Za-z][\w\-/]*", n) for n in names):
                # 0.9.4: một dòng "Tag ảnh | tag | ... | camera front, fisheye_front · timeofday day, dusk, night · ..."
                # là nhiều tag (mỗi nhóm một label tag, giống task CVAT thật), không phải một tag tên "ảnh"
                for a in attrs:
                    found.setdefault(norm(a["name"]), {"name": a["name"], "shape": "tag", "attributes": [dict(a, name="value")]})
                    if def_col is not None and def_col < len(r) and r[def_col]:
                        definitions.setdefault(norm(a["name"]), r[def_col])
                continue
            if shape:
                for name in names:
                    item = found.setdefault(norm(name), {"name": name, "shape": shape})
                    if attrs and "attributes" not in item:
                        item["attributes"] = attrs

    # Checklist viết bằng ô tick markdown ("- [ ] Mọi ảnh đủ bốn tag")
    for m in re.finditer(r"^\s*[-*]\s*\[[ xX]\]\s+(.+)$", markdown, re.M):
        item = _clean(m.group(1))
        if item and item not in checklist:
            checklist.append(item)
    for sk in skeleton_sections(markdown):    # 0.9.4: "## Skeleton người: 17 điểm" + bảng tên điểm
        item = found.setdefault(norm(sk["name"]), {"name": sk["name"], "shape": "skeleton"})
        if item["shape"] == "skeleton" and not item.get("points"):
            item["points"] = sk["points"]
    labels = list(found.values())
    if not labels:
        # Không có bảng taxonomy: tìm câu kiểu "Chỉ dùng label person và 17 sublabel" hoặc "Nhãn: `person`, công cụ Rectangle".
        has_skeleton = re.search(r"skeleton|keypoint", markdown.lower())
        for m in re.finditer(r"(?:\b(?:label|class)\s+|\b[Nn]hãn\s*:\s*)`?([A-Za-z][A-Za-z0-9_/\-]{1,30})\b`?", markdown):
            name = m.group(1)
            if name.lower() not in ("type", "name", "new", "attribute", "and", "or", "set", "list") and norm(name) not in found:
                line = markdown[markdown.rfind("\n", 0, m.start()) + 1:markdown.find("\n", m.end()) if markdown.find("\n", m.end()) >= 0 else None]
                found[norm(name)] = {"name": name, "shape": _shape_in(line) or ("skeleton" if has_skeleton else "rectangle")}
        labels = list(found.values())
        if labels:
            warnings.append(f"Không có bảng taxonomy; tool đoán label từ câu chữ: {', '.join(l['name'] for l in labels)}. Kiểm tra lại.")
    for label in labels:
        key = norm(label["name"])
        tail = key.split("/")[-1]
        label["definition"] = definitions.get(key) or definitions.get(tail) or ""
    fill_prompts(labels, prompt_lookup)
    task, why = guess_task(markdown, labels)
    if not labels:
        warnings.append("Không tìm thấy bảng taxonomy (bảng có cột class/label/nhãn và cột shape). "
                        "Thêm label bằng tay ở bảng bên dưới, mỗi dòng một label.")
    missing = [l["name"] for l in labels if not l["prompts"] and l["shape"] not in ("skeleton", "tag")]
    if missing:
        warnings.append(f"{len(missing)} label chưa có prompt tiếng Anh cho SAM3 ({', '.join(missing[:6])}): điền vào cột Prompt, "
                        "không thì tool sẽ không tự tìm được các label này.")
    if task == "keypoints":
        warnings.append("Guideline có skeleton: dùng mục 'Profile cho project' ở tab Guideline để tool ghép từng điểm với "
                        "model (thân người, mặt) theo label của task. Văn bản, định nghĩa và checklist vẫn được nạp ở đây.")
    title = ""
    for line in markdown.splitlines():   # tên guideline: dòng ngắn đầu tiên trước bảng/mục đầu tiên, bỏ các dòng chung chung
        text = re.sub(r"^#+\s*", "", line).strip().strip("*").strip()
        if line.startswith("|") or re.match(r"#+\s*\d", line):
            break
        if 3 < len(text) <= 80 and not re.search(r"^annotation guideline$|student annotation|^cvat$", text.lower()):
            title = text
            break
    stem = filename.rsplit(".", 1)[0]
    return {
        "id": slug(stem), "title": title or stem, "file": filename, "task": task, "task_reason": why,
        "labels": labels, "issue_types": issues, "checklist": checklist, "reviewer_criteria": criteria,
        "warnings": warnings, "chars": len(markdown),
    }


def fill_prompts(labels: list[dict], prompt_lookup: Callable[[str], Optional[list[str]]]) -> None:
    """Prompt tiếng Anh cho SAM3: lấy từ profile có sẵn nếu trùng tên; tên không dấu thì dùng tạm; tên có dấu thì để trống."""
    for label in labels:
        key = norm(label["name"])
        tail = key.split("/")[-1]
        label.setdefault("definition", "")
        label["aliases"] = [tail] if tail != key else []
        if label.get("prompts"):
            label.setdefault("prompt_source", "file taxonomy")
        else:
            known = prompt_lookup(label["name"]) or prompt_lookup(tail)
            if known is not None:
                label["prompts"], label["prompt_source"] = list(known), "profile có sẵn"
            else:
                words = tail.replace("_", " ").replace("-", " ").strip()
                if words and words == _ascii(words):   # tên không dấu: dùng tạm làm prompt, người dùng kiểm tra
                    label["prompts"], label["prompt_source"] = [words], "lấy từ tên label, cần kiểm tra"
                else:
                    label["prompts"], label["prompt_source"] = [], "cần prompt tiếng Anh"
        if label["shape"] == "polyline":
            label.setdefault("polyline_mode", "centerline")


# ------------------------------------------------------- file taxonomy ----
CVAT_TYPES = {"rectangle", "polygon", "polyline", "points", "ellipse", "cuboid", "mask", "skeleton", "tag", "any"}
SHAPE_WORDS = {"bbox": "rectangle", "box": "rectangle", "bounding_box": "rectangle", "boundingbox": "rectangle", "rect": "rectangle",
               "line": "polyline", "lines": "polyline", "segmentation": "mask", "brush": "mask", "keypoints": "skeleton", "pose": "skeleton"}


def _shape_word(value) -> Optional[str]:
    v = str(value or "").strip().lower().replace(" ", "_")
    if v in CVAT_TYPES:
        return v
    return SHAPE_WORDS.get(v) or _shape_in(v.replace("_", " "))


def read_taxonomy_json(data: bytes) -> list[dict]:
    """Đọc danh sách label từ file JSON. Nhận các dạng hay gặp:
      - JSON label của CVAT (tab Raw của task/project, hoặc /api/labels): [{"name", "type", "attributes", "sublabels", "svg"}]
      - COCO: {"categories": [{"name", "keypoints", "skeleton"}]}
      - tự viết: {"labels"|"classes"|"taxonomy": [...]}, ["car", "truck"], {"car": "rectangle", ...}, hoặc nhóm lồng nhau
        {"groups": [{"shape": "polygon", "classes": [...]}]}
    Label của CVAT được giữ nguyên (attribute, điểm của skeleton) để khi thêm vào task không mất gì.
    """
    import json

    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"File JSON không đọc được: {exc}") from exc
    out: dict[str, dict] = {}

    def add(item, inherited: Optional[str] = None) -> None:
        if isinstance(item, str):
            name, info = item, {}
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            name, info = item["name"], item
        else:
            return
        name = name.strip()
        if not name or info.get("parent_id") or info.get("has_parent"):
            return   # điểm con của skeleton trong /api/labels
        shape = (_shape_word(info.get("type")) or _shape_word(info.get("shape")) or _shape_word(info.get("annotation_type"))
                 or ("skeleton" if info.get("keypoints") or info.get("sublabels") else None) or inherited or "rectangle")
        if shape == "any":
            shape = inherited or "rectangle"
        label = {"name": name, "shape": shape,
                 "definition": str(info.get("definition") or info.get("description") or info.get("desc") or "")[:600]}
        prompts = info.get("prompts") or info.get("prompt")
        if prompts:
            label["prompts"] = [prompts] if isinstance(prompts, str) else [str(p) for p in prompts if str(p).strip()]
        if "attributes" in info or "sublabels" in info or "svg" in info:
            spec = {k: v for k, v in info.items() if k in ("name", "color", "type", "attributes", "sublabels", "svg")}
            spec["type"] = shape
            if spec.get("svg"):   # id điểm là của task cũ; CVAT nối điểm theo data-label-name
                spec["svg"] = re.sub(r'\s*data-label-id="[^"]*"', "", str(spec["svg"]))
            for attr in spec.get("attributes") or []:
                attr.pop("id", None)
            spec["sublabels"] = [{k: v for k, v in sub.items() if k != "id"} for sub in spec.get("sublabels") or []]
            for sub in spec["sublabels"]:
                for attr in sub.get("attributes") or []:
                    attr.pop("id", None)
            if not spec["sublabels"]:
                spec.pop("sublabels")
            label["cvat"] = spec
        elif info.get("keypoints"):
            names = [str(k) for k in info["keypoints"]]
            label["cvat"] = {"name": name, "type": "skeleton", "attributes": [],
                             "sublabels": [{"name": k, "type": "points", "attributes": []} for k in names]}
        out.setdefault(norm(name), label)

    def walk(node, inherited: Optional[str] = None) -> None:
        if isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and "name" not in item and any(isinstance(v, list) for v in item.values()):
                    walk(item, inherited)   # nhóm lồng nhau
                else:
                    add(item, inherited)
        elif isinstance(node, dict):
            shape = _shape_word(node.get("shape") or node.get("type")) or inherited
            lists = [k for k in ("labels", "classes", "categories", "taxonomy", "groups", "items", "results") if isinstance(node.get(k), list)]
            if lists:
                for key in lists:
                    walk(node[key], shape)
            elif "name" in node:
                add(node, inherited)
            else:   # {"car": "rectangle", "area/direct": "polygon"} hoặc {"Object": [...], "Lane": [...]}
                for key, value in node.items():
                    if isinstance(value, (list, dict)):
                        walk(value, _shape_word(key) or shape)
                    elif isinstance(value, str) and _shape_word(value):   # chỉ nhận cặp tên -> shape, bỏ {"version": 2}...
                        add({"name": key, "type": value}, shape)

    # /api/labels của CVAT trả điểm của skeleton thành label riêng có parent_id: gắn lại vào skeleton cha.
    flat = doc.get("results") if isinstance(doc, dict) and isinstance(doc.get("results"), list) else doc
    if isinstance(flat, list):
        children: dict = {}
        for item in flat:
            if isinstance(item, dict) and item.get("parent_id"):
                children.setdefault(item["parent_id"], []).append(item)
        for item in flat:
            if isinstance(item, dict) and item.get("id") in children and not item.get("sublabels"):
                item["sublabels"] = [{k: v for k, v in c.items() if k in ("name", "color", "type", "attributes")} for c in children[item["id"]]]
    walk(doc)
    if not out:
        raise ValueError("Không thấy label nào trong file JSON. Cần một danh sách có trường \"name\" (ví dụ JSON label ở tab Raw của task CVAT).")
    return list(out.values())


def analyze_taxonomy(data: bytes, filename: str, prompt_lookup: Callable[[str], Optional[list[str]]]) -> tuple[dict, str]:
    """Phân tích file taxonomy JSON. Trả về (bản rút ra, văn bản markdown để lưu làm tài liệu nền)."""
    labels = read_taxonomy_json(data)
    fill_prompts(labels, prompt_lookup)
    task, why = guess_task("", labels)
    stem = filename.rsplit(".", 1)[0]
    lines = [f"# Taxonomy: {stem}", "", "| Label | CVAT shape | Định nghĩa |", "|---|---|---|"]
    lines += [f"| {l['name']} | {l['shape']} | {l.get('definition', '')} |" for l in labels]
    warnings = []
    missing = [l["name"] for l in labels if not l["prompts"] and l["shape"] not in ("skeleton", "tag")]
    if missing:
        warnings.append(f"{len(missing)} label chưa có prompt tiếng Anh cho SAM3 ({', '.join(missing[:6])}): điền vào cột Prompt.")
    if task == "keypoints":
        warnings.append("Có label skeleton: tool tạo được label (giữ nguyên các điểm trong file) nhưng chưa tự sinh profile gợi ý cho skeleton.")
    return ({"id": slug(stem), "title": stem, "file": filename, "task": task, "task_reason": f"từ shape trong file taxonomy ({why})",
             "labels": labels, "issue_types": [], "checklist": [], "reviewer_criteria": [], "warnings": warnings,
             "chars": len(data), "source": "taxonomy"}, "\n".join(lines) + "\n")


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", _ascii(text).lower()).strip("_")
    s = re.sub(r"^[0-9a-f]{8}_", "", s)   # tiền tố ngẫu nhiên khi file được tải lên qua chat
    return s[:60] or f"guideline_{int(time.time())}"


# ---------------------------------------------------------------- LLM -----
LLM_SYSTEM = (
    "Bạn giúp đưa một annotation guideline vào tool gán nhãn dùng SAM3. SAM3 tìm đối tượng bằng cụm từ TIẾNG ANH ngắn "
    "(1-3 từ, danh từ cụ thể nhìn thấy được, ví dụ 'car', 'traffic sign', 'road curb'). Chỉ trả về JSON."
)


def llm_enrich(llm, markdown: str, analysis: dict) -> list[str]:
    """Bổ sung prompt tiếng Anh, định nghĩa còn thiếu và label bị sót. Trả về danh sách ghi chú cho người dùng."""
    from .llm import parse_json

    current = [{"name": l["name"], "shape": l["shape"], "prompts": l["prompts"]} for l in analysis["labels"]]
    ask = (f"GUIDELINE:\n{markdown[:24000]}\n\nLABEL TOOL ĐÃ RÚT RA:\n{current}\n\n"
           "Trả về JSON: {\"labels\": [{\"name\": tên đúng như guideline, \"shape\": rectangle|polygon|polyline|mask|skeleton|points, "
           "\"prompts\": [1-3 cụm tiếng Anh cho SAM3, rỗng nếu label chỉ suy ra được từ label khác], "
           "\"definition\": định nghĩa ngắn bằng tiếng Việt theo guideline}]}. Giữ nguyên tên label của guideline, thêm label tool bỏ sót.")
    data = parse_json(llm.complete(tr(LLM_SYSTEM), tr(ask), max_tokens=4000))
    notes = []
    by_name = {norm(l["name"]): l for l in analysis["labels"]}
    for item in (data.get("labels") if isinstance(data, dict) else None) or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        key = norm(item["name"])
        label = by_name.get(key)
        if label is None:
            shape = item.get("shape") if item.get("shape") in {s for _, s in SHAPES} else "rectangle"
            label = {"name": str(item["name"]), "shape": shape, "definition": "", "aliases": [], "prompts": [], "prompt_source": ""}
            analysis["labels"].append(label)
            by_name[key] = label
            notes.append(f"LLM thêm label '{label['name']}' mà bảng không có: kiểm tra lại.")
        prompts = [str(p).strip() for p in item.get("prompts") or [] if str(p).strip()][:3]
        if prompts and label.get("prompt_source") != "profile có sẵn":
            label["prompts"], label["prompt_source"] = prompts, "LLM đề xuất"
        if item.get("definition") and not label.get("definition"):
            label["definition"] = str(item["definition"])[:400]
    analysis["warnings"] = [w for w in analysis["warnings"] if "chưa có prompt" not in w]
    return notes


# ------------------------------------------------------------- profile ----
def build_profile(analysis: dict) -> Optional[dict]:
    task = analysis["task"]
    if task == "keypoints":
        return None
    labels = []
    for l in analysis["labels"]:
        if l["shape"] in ("skeleton", "points", "cuboid", "ellipse"):
            continue
        entry = {"name": l["name"]}
        if l.get("aliases"):
            entry["aliases"] = l["aliases"]
        if task in ("instance", "tracking"):
            entry["shape"] = "rectangle" if task == "tracking" else l["shape"]
        entry["prompts"] = l.get("prompts") or []
        if l["shape"] == "polyline":
            entry["polyline_mode"] = l.get("polyline_mode") or "centerline"
            entry["join_dashes"] = entry["polyline_mode"] != "long_edges"
        labels.append(entry)
    if not labels:
        return None
    rules: dict = {
        "dedup": {"iou": 0.6, "class_margin": 0.15, "existing_iou": 0.7},
        "occluded": {"enabled": True, "min_overlap": 0.15},
        "low_visibility": {"enabled": True, "min_side_px": 12, "score_margin": 0.08},
        "polygon": {"epsilon": 1.5},
        "lane": {"sample_step_px": 10, "max_gap_px": 30, "join_gap_px": 60, "epsilon": 1.5, "min_length_px": 20},
        "qa": {"bounds_tolerance_px": 0.5, "min_box_side_px": 3, "min_polyline_px": 10, "duplicate_iou": 0.85},
    }
    if task == "semantic":
        rules["semantic"] = {"output_shape": "mask", "priority": [l["name"] for l in labels], "min_region_px": 40,
                             "fill_gaps_px": 150, "respect_existing": True}
    if task == "tracking":
        rules["tracking"] = {"match_iou": 0.3, "reid_max_gap_frames": 60, "reid_min_appearance": 0.75, "min_track_frames": 5,
                             "amodal": {"enabled": True}, "keyframe_iou": 0.8, "max_keyframe_gap": 20}
    return {
        "id": f"gl_{analysis['id']}", "title": analysis["title"][:80],
        "description": f"Nạp từ {analysis['file']}. Kiểm tra prompt và ngưỡng ở tab Label trước khi dùng cho bài thật.",
        "guideline": analysis["file"], "mode": task,
        "match_labels": [l["name"] for l in labels[:3]],
        "defaults": {"threshold": 0.5, "min_area_px": 30, "join_dashes": True},
        "labels": labels, "rules": rules,
        "checklist": analysis.get("checklist") or [],
    }


def knowledge_record(analysis: dict) -> dict:
    return {
        "id": analysis["id"], "title": analysis["title"], "file": analysis["file"], "task": analysis["task"],
        "imported": time.strftime("%Y-%m-%d %H:%M"),
        "labels": [dict({"name": l["name"], "shape": l["shape"], "definition": l.get("definition", "")},
                        **({"cvat": l["cvat"]} if l.get("cvat") else {})) for l in analysis["labels"]],
        "issue_types": analysis.get("issue_types") or [],
        "checklist": analysis.get("checklist") or [],
        "reviewer_criteria": analysis.get("reviewer_criteria") or [],
    }


def cvat_labels(record: dict, task_labels: list[dict], lib) -> dict:
    """Bộ label CVAT theo guideline đã nạp. Như phần soạn guideline: tool chỉ THÊM label còn thiếu, không xoá, không đổi."""
    from .draft import LABEL_PALETTE, skeleton_canon, skeleton_svg

    existing = {norm(l["name"]): l for l in task_labels}
    canon = skeleton_canon(task_labels, lib)               # 'person_vf17' trong task = 'person' của guideline
    have_canon = {norm(v) for v in canon.values()}
    colors = {norm(k): v for k, v in (lib.knowledge.get("colors") or {}).items()}
    templates = {}
    for name, template in (lib.knowledge.get("skeletons") or {}).items():
        for key in [name, *template.get("aliases", [])]:
            templates.setdefault(norm(key), template)
    labels, missing, notes = [], [], []
    for i, label in enumerate(record.get("labels") or []):
        key, kind = norm(label["name"]), label.get("shape") or "rectangle"
        spec = {"name": label["name"], "type": kind, "attributes": [],
                "color": colors.get(key) or colors.get(key.split("/")[-1]) or LABEL_PALETTE[i % len(LABEL_PALETTE)]}
        raw = label.get("cvat")
        if raw and (raw.get("type") or kind) == kind:
            # Label lấy từ file taxonomy của CVAT: giữ nguyên attribute, điểm và hình skeleton của file.
            spec = dict(raw, name=label["name"], type=kind, color=raw.get("color") or spec["color"])
            spec.setdefault("attributes", [])
            if kind == "skeleton" and spec.get("sublabels") and not spec.get("svg"):
                n = len(spec["sublabels"])
                spec["svg"] = skeleton_svg({"points": [{"name": sub["name"], "x": 10 + 80 * (j % 5) / 4, "y": 10 + 80 * (j // 5) / max(1, (n - 1) // 5)}
                                                       for j, sub in enumerate(spec["sublabels"])], "edges": []})
        elif kind == "skeleton":
            template = templates.get(key)
            if not template:
                if key not in existing and key not in have_canon:
                    notes.append(f"Chưa có mẫu skeleton cho '{label['name']}' (tên và vị trí các điểm): tạo label này bằng tay trong CVAT, "
                                 "hoặc thêm mẫu vào mục skeletons của guideline_library/knowledge.yaml.")
                continue
            spec["color"] = template.get("color") or spec["color"]
            spec["sublabels"] = [{"name": p["name"], "type": "points", "attributes": [], "color": p.get("color") or "#f2c12e"}
                                 for p in template["points"]]
            spec["svg"] = skeleton_svg(template)
        if kind == "skeleton" and not spec.get("sublabels"):
            continue
        labels.append(spec)
        have = existing.get(key)
        if not have and not (kind == "skeleton" and key in have_canon):
            missing.append(spec)
        elif have and (have.get("type") or "any") not in ("any", kind):
            notes.append(f"Label '{label['name']}' trong task đang là {have['type']}, guideline quy định {kind}. Tool không tự đổi kiểu label.")
    return {"labels": labels, "missing": missing, "notes": notes}
