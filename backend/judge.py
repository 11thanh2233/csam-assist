"""Claude xem ảnh (0.9.5): duyệt đề xuất của tool theo guideline và label của CHÍNH job đang làm.

Không có luật riêng cho từng label. Mỗi frame tool gửi một lần:
  - ảnh có đánh số từng đề xuất (khung + số), cộng vài ảnh phóng to cho vật nhỏ (mặt, tay...);
  - danh sách label của task: kiểu hình, định nghĩa, thuộc tính và các giá trị được phép, các nhóm điểm của skeleton;
  - ghi chú guideline của project (occluded, outside, những thứ không gán...), bài học và các lần bạn đã sửa;
  - tag của ảnh và gợi ý đo được (độ sáng, hồng ngoại, fisheye, tên file).
Claude trả về JSON: giữ/bỏ từng đề xuất, giá trị thuộc tính, occluded/outside của chính vật, nhóm điểm nào bị che,
giá trị tag, và vật bị sót (kèm khung gần đúng để SAM3 vẽ lại). Tool chỉ nhận giá trị nằm trong danh sách được phép.
Kết quả vẫn là gợi ý: bạn duyệt trước khi ghi.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .profiles import norm

MAX_SIDE = 1568            # cạnh dài tối đa của ảnh gửi đi (Claude tự thu nhỏ ảnh lớn hơn)
MAX_CROPS = 6
CROP_MIN = 384
POS_WORDS = {"outer", "inner", "upper", "lower", "top", "bottom", "tip", "center", "centre", "corner", "mid", "middle",
             "start", "end", "base", "point", "pt", "kp"}
PART_ALIAS = {"lip": "mouth", "lips": "mouth", "brow": "eyebrow", "eyebrows": "eyebrow", "pupil": "eye", "iris": "eye"}


def part_of(point_name: str) -> str:
    """Nhóm bộ phận của một điểm skeleton theo tên: left_eye_outer -> left_eye, lip_upper -> mouth, nose_tip -> nose."""
    words = [w for w in re.split(r"[^a-z0-9]+", point_name.lower()) if w and not w.isdigit()]
    kept = [PART_ALIAS.get(w, w) for w in words if w not in POS_WORDS]
    if "mouth" in kept:
        return "mouth"
    return "_".join(kept) or point_name.lower()


def part_groups(elements: list[dict]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for i, e in enumerate(elements):
        groups.setdefault(part_of(str(e.get("name") or "")), []).append(i)
    return groups


def guide_notes(markdown: str, limit: int = 6000) -> str:
    """Các đoạn guideline tool cần khi xem ảnh: occluded/outside, thuộc tính, tag, những thứ không gán, kích thước."""
    keys = re.compile(r"occlu|outside|che khuất|bị che|visible|thuộc tính|attribute|\btag\b|không gán|không đánh|bỏ qua|"
                      r"ignore|exclude|tối thiểu|minimum|truncat|cắt mép|poster|gương|mirror|phản chiếu|ảnh chụp", re.I)
    out, size = [], 0
    for para in re.split(r"\n\s*\n", markdown or ""):
        para = para.strip()
        if para and keys.search(para):
            para = para[:900]
            if size + len(para) > limit:
                break
            out.append(para)
            size += len(para) + 2
    return "\n\n".join(out)


class Usage:
    """Đếm số lần gọi Claude mỗi ngày, có giới hạn để không tốn tiền ngoài ý muốn."""

    def __init__(self, path: Path, daily_limit: int):
        self.path, self.limit = Path(path), int(daily_limit)
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        today = time.strftime("%Y-%m-%d")
        if data.get("day") != today:
            data = {"day": today, "calls": 0, "input_tokens": 0, "output_tokens": 0}
        return data

    def status(self) -> dict:
        d = self._load()
        return dict(d, limit=self.limit, left=max(0, self.limit - d["calls"]) if self.limit > 0 else None)

    def allow(self) -> bool:
        return self.limit <= 0 or self._load()["calls"] < self.limit

    def add(self, usage: Optional[dict]) -> None:
        with self.lock:
            d = self._load()
            d["calls"] += 1
            d["input_tokens"] += int((usage or {}).get("input_tokens") or 0)
            d["output_tokens"] += int((usage or {}).get("output_tokens") or 0)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(d), encoding="utf-8")


# ------------------------------------------------------------- dựng ảnh ---
def _jpeg(img: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


COLORS = [(255, 64, 64), (64, 200, 255), (255, 200, 0), (120, 255, 120), (255, 120, 255), (255, 255, 255)]


def marked_image(image: np.ndarray, items: list[dict]) -> tuple[np.ndarray, float]:
    h, w = image.shape[:2]
    scale = min(1.0, MAX_SIDE / max(h, w))
    out = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else image.copy()
    out = np.ascontiguousarray(out)
    thick = max(2, int(round(max(out.shape[:2]) / 600)))
    for it in items:
        x1, y1, x2, y2 = [int(round(v * scale)) for v in it["bbox"]]
        color = COLORS[(it["id"] - 1) % len(COLORS)]
        if it.get("type") == "polyline" and it.get("points"):
            pts = (np.asarray(it["points"], float).reshape(-1, 2) * scale).astype(np.int32)
            cv2.polylines(out, [pts], False, color, thick)
        elif it.get("type") == "polygon" and it.get("points"):
            pts = (np.asarray(it["points"], float).reshape(-1, 2) * scale).astype(np.int32)
            cv2.polylines(out, [pts], True, color, thick)
        else:
            cv2.rectangle(out, (x1, y1), (x2, y2), color, thick)
        for e in it.get("elements") or []:
            if not e.get("outside"):
                cv2.circle(out, (int(e["x"] * scale), int(e["y"] * scale)), thick + 1, color, -1)
        label = str(it["id"])
        fs = 0.5 * thick
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, thick)
        ty = max(th + 4, y1)
        cv2.rectangle(out, (x1, ty - th - 4), (x1 + tw + 6, ty), (0, 0, 0), -1)
        cv2.putText(out, label, (x1 + 3, ty - 3), cv2.FONT_HERSHEY_SIMPLEX, fs, color, thick)
    return out, scale


def crop(image: np.ndarray, box: list[float], pad: float = 0.25) -> np.ndarray:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    x1, y1 = max(0, int(x1 - pad * bw)), max(0, int(y1 - pad * bh))
    x2, y2 = min(w, int(x2 + pad * bw)), min(h, int(y2 + pad * bh))
    c = image[y1:y2, x1:x2]
    if c.size == 0:
        return c
    s = max(1.0, CROP_MIN / max(c.shape[:2]))
    return cv2.resize(c, (int(c.shape[1] * s), int(c.shape[0] * s)), interpolation=cv2.INTER_CUBIC) if s > 1 else c


# --------------------------------------------------------------- lời nhắn ---
SYSTEM = (
    "You are a data-annotation reviewer checking proposals made by a labelling tool in CVAT. Follow the task's guideline "
    "and label list exactly. General rules:\n"
    "- occluded/outside are judged on THE OBJECT ITSELF: partly hidden but its shape/position can still be estimated -> "
    "occluded; only a tiny part is visible and it cannot be estimated -> outside (do not annotate). Two overlapping boxes "
    "do NOT mean the object behind is occluded.\n"
    "- For skeletons judge each part group. A part hidden by something else (sunglasses, hat, hand, a moustache over the "
    "mouth...) -> occluded if its position can still be estimated, outside if not. Parts that are not hidden stay visible. "
    "Clear glasses are still visible.\n"
    "- keep=false when the guideline says not to annotate it (a person in a photo/poster/screen/mirror, too small, too little "
    "visible) or the proposal is the wrong object.\n"
    "- Only use attribute values from the allowed list. If unsure, still give your best value with low confidence.\n"
    "- missing: objects of the task's labels that the tool did not propose, box in 0-1000 coordinates of image 1.\n"
    "- Write any free text (why) in Vietnamese.\n"
    "Return only JSON in the requested format."
)


def _schema_lines(cfgs: list[dict], profile: dict, present: set) -> list[str]:
    defs = {norm(e.get("name", "")): e.get("definition", "") for e in profile.get("labels") or []}
    lines = []
    for c in cfgs:
        if not c.get("enabled") or norm(c["name"]) not in present:
            continue
        line = f"- {c['name']} ({c.get('cvat_type') or c['shape']})"
        d = defs.get(norm(c["name"])) or defs.get(norm(c.get("entry") or ""))
        if d:
            line += f": {d[:240]}"
        attrs = [a for a in c.get("label_attributes") or [] if a.get("name")]
        if attrs:
            line += "\n    attributes: " + "; ".join(
                f"{a['name']} ∈ {{{', '.join(str(v) for v in a.get('values') or []) or 'free text'}}}" for a in attrs)
        lines.append(line)
    return lines


def _example_lines(examples: list[dict]) -> list[str]:
    out = []
    for c in examples:
        if c["field"] == "_keep":
            out.append(f"- {c['label']}: tool proposed it, the annotator DELETED it" + (f" ({c.get('why')})" if c.get("why") else "")
                       + (f" — {c['where']}" if c.get("where") else ""))
        else:
            out.append(f"- {c['label']}.{c['field']}: tool '{c['tool']}' -> annotator chose '{c['you']}'"
                       + (f" — {c['where']}" if c.get("where") else "") + (f" [file {c['file']}]" if c.get("file") else ""))
    return out


def where_text(box: list[float], width: int, height: int) -> str:
    cx, cy = (box[0] + box[2]) / 2 / max(width, 1), (box[1] + box[3]) / 2 / max(height, 1)
    hx = "left" if cx < 0.36 else ("right" if cx > 0.64 else "center")
    vy = "top" if cy < 0.36 else ("bottom" if cy > 0.64 else "middle")
    size = (box[2] - box[0]) * (box[3] - box[1]) / max(width * height, 1)
    return f"{vy}-{hx} of image, {size:.0%} of area"


def build_request(image: np.ndarray, proposals: list[dict], cfgs: list[dict], profile: dict, tags: list[dict],
                  tag_cfgs: list[dict], memory: dict, filename: str = "", hints: Optional[dict] = None,
                  max_crops: int = MAX_CROPS) -> tuple[str, list[bytes], list[dict]]:
    h, w = image.shape[:2]
    items = []
    for i, p in enumerate(proposals, 1):
        if not p.get("bbox"):
            continue
        items.append({"id": i, "index": i - 1, "bbox": p["bbox"], "type": p.get("type"), "points": p.get("points"),
                      "elements": p.get("elements") or []})
    marked, scale = marked_image(image, items)
    images = [_jpeg(marked)]
    crops_note = []
    small = sorted((it for it in items if (it["bbox"][2] - it["bbox"][0]) < 0.3 * w and (it["bbox"][3] - it["bbox"][1]) < 0.3 * h
                    and (proposals[it["index"]].get("elements") or any(cfg_by_id(cfgs, proposals[it["index"]]).get("label_attributes") or []))),
                   key=lambda it: -(it["bbox"][2] - it["bbox"][0]) * (it["bbox"][3] - it["bbox"][1]))[:max(0, int(max_crops))]
    for it in small:
        c = crop(image, it["bbox"])
        if c.size:
            images.append(_jpeg(c, 90))
            crops_note.append(f"Image {len(images)} is a zoomed crop around proposal #{it['id']}.")
    present = {norm(p.get("label_name") or "") for p in proposals} | {norm(c["name"]) for c in cfgs if c.get("enabled") and c.get("supported")}
    lines = ["TASK LABELS:"] + _schema_lines([c for c in cfgs if c.get("shape") != "tag"], profile, present)
    if profile.get("exclude"):
        lines.append("GUIDELINE SAYS DO NOT ANNOTATE: " + "; ".join(profile["exclude"]))
    if profile.get("guide_notes"):
        lines += ["", "GUIDELINE NOTES:", profile["guide_notes"][:6000]]
    if memory.get("lessons"):
        lines += ["", "PROJECT LESSONS (confirmed by the annotator):"] + [f"- {l['text']}" for l in memory["lessons"]]
    if memory.get("examples"):
        lines += ["", "PAST CORRECTIONS BY THE ANNOTATOR (follow the annotator's way):"] + _example_lines(memory["examples"])
    lines += ["", f"TOOL PROPOSALS (numbers on image 1){' - file ' + filename if filename else ''}:"]
    for it in items:
        p = proposals[it["index"]]
        desc = f"#{it['id']} {p.get('label_name')} {p.get('type')} ({where_text(p['bbox'], w, h)})"
        if p.get("attributes"):
            desc += " tool attributes: " + json.dumps(p["attributes"], ensure_ascii=False)
        if p.get("occluded"):
            desc += " tool set occluded"
        if p.get("elements"):
            groups = part_groups(p["elements"])
            states = []
            for g, idx in groups.items():
                st = {"outside" if p["elements"][i]["outside"] else ("occluded" if p["elements"][i]["occluded"] else "visible") for i in idx}
                states.append(f"{g}[{', '.join(p['elements'][i]['name'] for i in idx)}]={'/'.join(sorted(st))}")
            desc += "\n    part groups: " + "; ".join(states)
        lines.append(desc)
    lines += crops_note
    if tag_cfgs:
        lines += ["", "IMAGE TAGS (choose a value for each attribute):"]
        by_label = {t["label_id"]: t for t in tags or []}
        for c in tag_cfgs:
            cur = by_label.get(c["label_id"]) or {}
            attrs = c.get("label_attributes") or []
            lines.append(f"- {c['name']}: " + "; ".join(
                f"{a['name']} ∈ {{{', '.join(map(str, a.get('values') or []))}}}" for a in attrs)
                + (f" (tool guess: {json.dumps(cur.get('attributes'), ensure_ascii=False)})" if cur.get("attributes") else ""))
        if hints:
            lines.append("Tool measurements: " + json.dumps(hints, ensure_ascii=False))
    labels_list = ", ".join(c["name"] for c in cfgs if c.get("enabled") and c.get("supported") and c.get("shape") not in ("tag", "skeleton"))
    lines += ["", 'Return JSON: {"objects": [{"id": number, "keep": true/false, "why": "short reason if dropped", '
              '"attributes": {"name": "value"}, "confidence": {"name": 0..1}, "state": "visible|occluded|outside", '
              '"parts": {"group": "visible|occluded|outside"}}], '
              '"tags": {"tag name": {"attribute name": "value"}}, '
              f'"missing": [{{"label": one of [{labels_list}], "box": [x1, y1, x2, y2], "why": "..."}}]}}. '
              "List an object only if something changes or it has attributes; at most 10 missing."]
    return "\n".join(lines), images, items


def cfg_by_id(cfgs: list[dict], p: dict) -> dict:
    return next((c for c in cfgs if c.get("label_id") == p.get("label_id")), {})


# --------------------------------------------------------------- áp kết quả ---
def _valid(values: list, value) -> Optional[str]:
    if value is None:
        return None
    if not values:
        return str(value)[:200]
    for v in values:
        if norm(str(v)) == norm(str(value)):
            return str(v)
    return None


def apply(result: dict, proposals: list[dict], items: list[dict], cfgs: list[dict], tags: list[dict],
          tag_cfgs: list[dict], width: int, height: int, source: str = "Claude") -> dict:
    """Áp câu trả lời của Claude lên đề xuất. Trả {changed, dropped, missing:[{label_id, box}]}."""
    from .pipeline import add_flag

    by_id = {it["id"]: proposals[it["index"]] for it in items}
    changed = dropped = 0
    for o in result.get("objects") or []:
        p = by_id.get(int(o.get("id") or -1)) if str(o.get("id", "")).lstrip("#").isdigit() else None
        if p is None:
            continue
        cfg = cfg_by_id(cfgs, p)
        if o.get("keep") is False:
            p["checked"] = False
            p["llm_drop"] = str(o.get("why") or "")[:200]
            add_flag(p["flags"], "LLM_DROP", f"Claude đề nghị bỏ: {p['llm_drop'] or 'không đúng guideline'}.")
            dropped += 1
            continue
        conf = o.get("confidence") or {}
        llm_attrs = {}
        for a in cfg.get("label_attributes") or []:
            name = a.get("name")
            got = next((v for k, v in (o.get("attributes") or {}).items() if norm(k) == norm(name)), None)
            value = _valid(a.get("values") or [], got)
            if value is None:
                continue
            p.setdefault("attributes", {})[norm(name)] = value
            llm_attrs[name] = value
            c = conf.get(name)
            if isinstance(c, (int, float)) and c < 0.6:
                p.setdefault("review_attributes", [])
                if name not in p["review_attributes"]:
                    p["review_attributes"].append(name)
        if llm_attrs:
            p["llm_attributes"] = llm_attrs
            p["auto_attributes"] = dict(p.get("auto_attributes") or {}, **{norm(k): v for k, v in llm_attrs.items()})
            changed += 1
        state = str(o.get("state") or "").lower()
        if p.get("type") != "skeleton" and state in ("visible", "occluded", "outside"):
            if state == "outside":
                p["checked"] = False
                add_flag(p["flags"], "LLM_DROP", "Claude: chỉ thấy phần rất nhỏ, không ước lượng được (outside), đề nghị không gán.")
                dropped += 1
            elif bool(p.get("occluded")) != (state == "occluded"):
                p["occluded"] = state == "occluded"
                p["llm_occluded"] = True
                changed += 1
        parts = o.get("parts") or {}
        if p.get("elements") and parts:
            groups = part_groups(p["elements"])
            for g, st in parts.items():
                st = str(st).lower()
                idx = groups.get(part_of(str(g))) or groups.get(str(g).lower())
                if not idx or st not in ("visible", "occluded", "outside"):
                    continue
                for i in idx:
                    e = p["elements"][i]
                    in_image = 0 <= e["x"] < width and 0 <= e["y"] < height
                    e["occluded"] = st == "occluded" and in_image
                    e["outside"] = st == "outside" or not in_image
            p["llm_parts"] = {str(k): str(v) for k, v in parts.items()}
            changed += 1
    # tag
    by_tag = {t["label_id"]: t for t in tags}
    for name, values in (result.get("tags") or {}).items():
        cfg = next((c for c in tag_cfgs if norm(c["name"]) == norm(name)), None)
        if cfg is None or not isinstance(values, dict):
            continue
        tag = by_tag.get(cfg["label_id"])
        if tag is None:
            tag = {"label_id": cfg["label_id"], "label_name": cfg["name"], "attributes": {}, "auto": [], "review": [], "notes": {}}
            tags.append(tag)
            by_tag[cfg["label_id"]] = tag
        for a in cfg.get("label_attributes") or []:
            got = next((v for k, v in values.items() if norm(k) == norm(a["name"])), None)
            value = _valid(a.get("values") or [], got)
            if value is None:
                continue
            if (tag.get("learned") or {}).get(a["name"]):
                continue   # giá trị học từ tên file / chuỗi ảnh bạn đã chốt: tin cái đó hơn
            tag["attributes"][norm(a["name"])] = value
            tag.setdefault("notes", {})[a["name"]] = source
            if a["name"] not in tag.setdefault("auto", []):
                tag["auto"].append(a["name"])
            if a["name"] in tag.get("review", []):
                tag["review"].remove(a["name"])
    missing = []
    for m in (result.get("missing") or [])[:10]:
        cfg = next((c for c in cfgs if norm(c["name"]) == norm(str(m.get("label") or "")) and c.get("enabled")
                    and c.get("supported") and c.get("shape") not in ("tag", "skeleton")), None)
        box = m.get("box")
        if cfg is None or not isinstance(box, list) or len(box) != 4:
            continue
        try:
            b = [float(box[0]) / 1000 * width, float(box[1]) / 1000 * height, float(box[2]) / 1000 * width, float(box[3]) / 1000 * height]
        except (TypeError, ValueError):
            continue
        if b[2] - b[0] < 4 or b[3] - b[1] < 4:
            continue
        missing.append({"label_id": cfg["label_id"], "box": b, "why": str(m.get("why") or "")[:160]})
    return {"changed": changed, "dropped": dropped, "missing": missing}


class Judge:
    def __init__(self, llm, usage: Usage, cache_size: int = 64):
        self.llm, self.usage = llm, usage
        self.cache: OrderedDict = OrderedDict()

    def available(self) -> bool:
        return self.llm is not None and getattr(self.llm, "sees_images", True)

    def ask(self, text: str, images: list[bytes]) -> dict:
        from .guideline.llm import LLMError, log_bad_reply, parse_json

        key = hashlib.sha1(text.encode() + b"".join(hashlib.sha1(i).digest() for i in images)).hexdigest()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if not self.usage.allow():
            raise LLMError(f"Đã dùng hết {self.usage.limit} lượt Claude hôm nay (đổi giới hạn trong Cài đặt của app).")
        raw = self.llm.complete(SYSTEM, text, images, max_tokens=4000)
        self.usage.add(getattr(self.llm, "last_usage", None))
        try:
            out = parse_json(raw)
            if not isinstance(out, dict):
                raise LLMError("Claude không trả về JSON dạng object.")
        except LLMError as exc:
            log_bad_reply(self.llm, raw, "2D", str(exc))           # 0.9.12: ghi nhật ký để biết vì sao
            raise
        self.cache[key] = out
        while len(self.cache) > 64:
            self.cache.popitem(last=False)
        return out
