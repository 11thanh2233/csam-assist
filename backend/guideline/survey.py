"""Khảo sát một bộ ảnh bằng SAM3: trong ảnh có gì, to nhỏ ra sao, bị che/cắt nhiều không, chỗ nào dễ nhầm.

SAM3 chỉ tìm được thứ được gọi tên, nên khảo sát đi theo hai chặng:
  1. KHÁM PHÁ (vài ảnh đầu): thử cả một bộ từ vựng rộng + label của task + từ khoá người dùng thêm
     (+ danh từ do LLM đề xuất nếu được phép gửi ảnh). Khái niệm nào xuất hiện thì giữ lại.
  2. ĐO ĐẾM (các ảnh còn lại): chỉ chạy các khái niệm đã thấy, nên nhanh hơn nhiều.
Kết quả là số liệu và các ca khó kèm số frame: bằng chứng cho từng luật của guideline nháp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from .. import geometry as G
from ..engines import Engine
from ..pipeline import run_prompt
from .library import norm

# Từ vựng khám phá mặc định cho ảnh đường phố / xe tự hành. kind: thing = vật đếm được,
# stuff = vùng nền, line = vạch/đường mảnh. Thêm từ khoá riêng ngay trên panel khi bộ ảnh thuộc lĩnh vực khác.
DISCOVERY = [
    ("person", "thing"), ("car", "thing"), ("van", "thing"), ("truck", "thing"), ("bus", "thing"),
    ("train", "thing"), ("tram", "thing"), ("motorcycle", "thing"), ("bicycle", "thing"), ("scooter", "thing"),
    ("trailer", "thing"), ("construction vehicle", "thing"), ("traffic light", "thing"), ("traffic sign", "thing"),
    ("traffic cone", "thing"), ("barrier", "thing"), ("pole", "thing"), ("street light", "thing"),
    ("billboard", "thing"), ("screen", "thing"), ("poster", "thing"), ("statue", "thing"),
    ("animal", "thing"), ("stroller", "thing"), ("wheelchair", "thing"), ("bench", "thing"), ("trash can", "thing"),
    ("fire hydrant", "thing"), ("face", "thing"),
    # 0.9.4: vật hay gặp ngoài đường phố (trong xe, trong nhà, kho), để task chỉ có label tạm vẫn được đề xuất class
    ("hand", "thing"), ("phone", "thing"), ("bag", "thing"), ("bottle", "thing"), ("box", "thing"),
    ("chair", "thing"), ("seat", "thing"), ("steering wheel", "thing"), ("dog", "thing"), ("boat", "thing"),
    ("road", "stuff"), ("sidewalk", "stuff"), ("building", "stuff"), ("wall", "stuff"), ("fence", "stuff"),
    ("tree", "stuff"), ("bush", "stuff"), ("grass", "stuff"), ("sky", "stuff"), ("water", "stuff"),
    ("bridge", "stuff"), ("tunnel", "stuff"),
    ("lane line", "line"), ("crosswalk", "line"), ("curb", "line"),
]
NOT_REAL = {"billboard", "screen", "poster", "statue"}  # vật chứa "người/xe không thật"
# Bộ phận nằm trong vật mang nó: không phải bị vật đó che (khuôn mặt luôn nằm trong hộp của người).
PART_OF = {"face": {"person", "pedestrian", "rider"}}
CASE_LIMIT = 8


@dataclass
class Concept:
    name: str                 # tên hiển thị (tên label của task nếu là label)
    prompts: list             # các cụm danh từ tiếng Anh gửi cho SAM3; rỗng = label suy ra bằng luật, không đếm được
    kind: str = "thing"
    source: str = "từ vựng"   # label | từ vựng | người dùng | LLM
    label_id: Optional[int] = None
    label_type: str = ""


@dataclass
class Stat:
    instances: int = 0
    frames: set = field(default_factory=set)
    heights: list = field(default_factory=list)
    min_sides: list = field(default_factory=list)
    borderline: int = 0      # số lần model lưỡng lự (điểm tin cậy sát ngưỡng)
    weak: int = 0            # số lần dưới ngưỡng (không tính là đối tượng)
    truncated: int = 0
    occluded: int = 0
    line_like: int = 0
    region_like: int = 0
    area: list = field(default_factory=list)
    examples: list = field(default_factory=list)


class Survey:
    def __init__(self, concepts: list[Concept], discovery_frames: int = 8, threshold: float = 0.5):
        self.concepts = {norm(c.name): c for c in concepts}
        self.discovery_frames = discovery_frames
        self.threshold = threshold
        self.stats: dict[str, Stat] = {}
        self.pairs: dict[tuple[str, str], dict] = {}
        self.cases: list[dict] = []
        self.frames: list[dict] = []

    # ------------------------------------------------------------ quét -----
    def add_concepts(self, concepts: list[Concept]) -> int:
        added = 0
        for c in concepts:
            if norm(c.name) not in self.concepts:
                self.concepts[norm(c.name)] = c
                added += 1
        return added

    def active(self) -> list[Concept]:
        """Chặng khám phá: tất cả. Sau đó: label của task, từ khoá người dùng, và khái niệm đã thấy."""
        if len(self.frames) < self.discovery_frames:
            return list(self.concepts.values())
        return [c for key, c in self.concepts.items()
                if c.source in ("label", "người dùng") or self.stats.get(key, Stat()).instances > 0]

    def _case(self, code: str, frame: int, box: list[float], text: str) -> None:
        same = [c for c in self.cases if c["code"] == code]
        if len(same) >= CASE_LIMIT or any(c["frame"] == frame for c in same):
            return  # mỗi loại ca khó giữ vài ví dụ, mỗi ví dụ ở một frame khác nhau
        self.cases.append({"code": code, "frame": frame, "bbox": [round(float(v), 1) for v in box], "text": text})

    def scan(self, engine: Engine, image: np.ndarray, frame: int, warnings: list[str]) -> int:
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        info = {"frame": frame, "width": width, "height": height,
                "brightness": float(gray.mean()), "sharpness": float(cv2.Laplacian(gray, cv2.CV_64F).var())}
        state = engine.encode(image)
        found = []
        for key, concept in [(norm(c.name), c) for c in self.active()]:
            hits = [inst for prompt in concept.prompts
                    for inst in run_prompt(engine, state, prompt, max(0.1, self.threshold - 0.1), concept.name, warnings)]
            for inst in hits:
                mask = inst.mask.astype(bool)
                area = int(np.count_nonzero(mask))
                box = G.mask_bbox(mask)
                if area < 16 or box is None:
                    continue
                if inst.score < self.threshold:  # dưới ngưỡng: chỉ ghi nhận là model lưỡng lự
                    stat = self.stats.setdefault(key, Stat())
                    stat.weak += 1
                    stat.borderline += 1
                    continue
                found.append({"key": key, "concept": concept, "mask": mask, "area": area, "box": box, "score": float(inst.score)})
        info["instances"] = len(found)
        self.frames.append(info)
        if info["brightness"] < 60:
            self._case("LOW_VISIBILITY", frame, [0, 0, width, height], "Ảnh tối (thiếu sáng hoặc ban đêm).")
        elif info["sharpness"] < 40:
            self._case("LOW_VISIBILITY", frame, [0, 0, width, height], "Ảnh mờ (motion blur hoặc mất nét).")

        # Hai khái niệm cùng nhận một vật -> cặp dễ nhầm
        confused = set()
        for i, a in enumerate(found):
            for b in found[i + 1:]:
                if a["key"] == b["key"] or G.box_inter(a["box"], b["box"]) <= 0:
                    continue
                if G.mask_iou(a["mask"], b["mask"]) >= 0.6:
                    pair = tuple(sorted((a["key"], b["key"])))
                    entry = self.pairs.setdefault(pair, {"count": 0, "examples": []})
                    entry["count"] += 1
                    if len(entry["examples"]) < 4 and all(e["frame"] != frame for e in entry["examples"]):
                        entry["examples"].append({"frame": frame, "bbox": [round(v, 1) for v in a["box"]]})
                    confused.update({(id(a), id(b)), (id(b), id(a))})
                    self._case("UNCERTAIN_CLASS", frame, a["box"],
                               f"Cùng một vật khớp cả '{a['concept'].name}' ({a['score']:.2f}) và '{b['concept'].name}' ({b['score']:.2f}).")

        for a in found:
            concept, box = a["concept"], a["box"]
            stat = self.stats.setdefault(a["key"], Stat())
            w, h = box[2] - box[0], box[3] - box[1]
            stat.instances += 1
            stat.frames.add(frame)
            stat.heights.append(h)
            stat.min_sides.append(min(w, h))
            stat.area.append(a["area"] / float(width * height))
            if a["score"] - self.threshold <= 0.1:
                stat.borderline += 1
            if G.touches_border(box, width, height, 2):
                stat.truncated += 1
            points = cv2.findNonZero(a["mask"].astype(np.uint8))
            (_, _), (rw, rh), _ = cv2.minAreaRect(points)
            short, long = max(min(rw, rh), 1.0), max(rw, rh)
            if long / short >= 6 and short <= 0.04 * min(width, height):
                stat.line_like += 1
            if a["area"] / float(width * height) >= 0.08:
                stat.region_like += 1
            if len(stat.examples) < 4 and all(e["frame"] != frame for e in stat.examples):
                stat.examples.append({"frame": frame, "bbox": [round(v, 1) for v in box]})
            if concept.kind != "thing":
                continue
            if min(w, h) < 12:
                self._case("LOW_VISIBILITY", frame, box, f"'{concept.name}' rất nhỏ ({w:.0f}x{h:.0f}px).")
            worst = 0.0
            for b in found:
                if b is a or b["concept"].kind != "thing" or (id(a), id(b)) in confused:
                    continue
                inter = G.box_inter(box, b["box"]) / max(G.box_area(box), 1.0)
                if b["key"] in NOT_REAL and inter >= 0.8 and a["key"] not in NOT_REAL:
                    self._case("UNCERTAIN_SCOPE", frame, box,
                               f"'{concept.name}' nằm trong '{b['concept'].name}': có thể là hình in/hình trên màn hình, không phải vật thật.")
                owners = PART_OF.get(a["key"]) or (PART_OF.get(norm(a["concept"].prompts[0])) if a["concept"].prompts else None) or set()
                if b["key"] in owners or (b["concept"].prompts and norm(b["concept"].prompts[0]) in owners):
                    continue
                if b["box"][3] > box[3] and b["key"] not in NOT_REAL:
                    worst = max(worst, inter)
            if worst >= 0.15:
                stat.occluded += 1
            if worst >= 0.5:
                self._case("UNCERTAIN_BOUNDARY", frame, box, f"'{concept.name}' bị che khoảng {worst:.0%}: không rõ phạm vi thật.")
        return len(found)

    # ---------------------------------------------------------- tổng hợp ---
    def summary(self) -> dict:
        n = max(len(self.frames), 1)

        def pct(a: float, b: float) -> float:
            return round(100.0 * a / b, 1) if b else 0.0

        def q(values: list, p: float) -> float:
            return round(float(np.percentile(values, p)), 1) if values else 0.0

        concepts = []
        for key, c in self.concepts.items():
            s = self.stats.get(key)
            if not s or not s.instances:
                if c.source == "label":
                    concepts.append({"name": c.name, "prompts": c.prompts, "kind": c.kind, "source": c.source,
                                     "label_id": c.label_id, "label_type": c.label_type, "instances": 0, "images": 0,
                                     "image_pct": 0.0, "examples": []})
                continue
            kind = c.kind
            if s.line_like / s.instances >= 0.5:
                kind = "line"
            elif s.region_like / s.instances >= 0.5 and c.kind != "thing":
                kind = "stuff"
            concepts.append({
                "name": c.name, "prompts": c.prompts, "kind": kind, "source": c.source,
                "label_id": c.label_id, "label_type": c.label_type,
                "instances": s.instances, "images": len(s.frames), "image_pct": pct(len(s.frames), n),
                "height_p10": q(s.heights, 10), "height_p50": q(s.heights, 50), "height_p90": q(s.heights, 90),
                "min_side_p10": q(s.min_sides, 10),
                "borderline_pct": pct(s.borderline, s.instances + s.weak), "truncated_pct": pct(s.truncated, s.instances),
                "occluded_pct": pct(s.occluded, s.instances), "area_pct": round(100 * float(np.mean(s.area)), 2),
                "examples": s.examples,
            })
        concepts.sort(key=lambda c: -c["instances"])
        pairs = []
        for (a, b), entry in self.pairs.items():
            na, nb = self.stats[a].instances, self.stats[b].instances
            pairs.append({"a": self.concepts[a].name, "b": self.concepts[b].name, "count": entry["count"],
                          "share_pct": pct(entry["count"], min(na, nb)), "examples": entry["examples"]})
        pairs.sort(key=lambda p: -p["count"])
        sizes: dict[tuple, int] = {}
        for f in self.frames:
            sizes[(f["width"], f["height"])] = sizes.get((f["width"], f["height"]), 0) + 1
        main = max(sizes, key=sizes.get) if sizes else (0, 0)
        things = [c for c in concepts if c["kind"] == "thing" and c["instances"]]
        total = sum(c["instances"] for c in things) or 1
        return {
            "images": len(self.frames), "width": main[0], "height": main[1], "size_variants": len(sizes),
            "dark_pct": pct(sum(f["brightness"] < 60 for f in self.frames), n),
            "blurry_pct": pct(sum(f["sharpness"] < 40 and f["brightness"] >= 60 for f in self.frames), n),
            "instances_per_image": round(sum(f["instances"] for f in self.frames) / n, 1),
            "occluded_pct": round(sum(c["occluded_pct"] * c["instances"] for c in things) / total, 1),
            "truncated_pct": round(sum(c["truncated_pct"] * c["instances"] for c in things) / total, 1),
            "tiny_pct": pct(sum(sum(v < 12 for v in self.stats[norm(c["name"])].min_sides) for c in things), total),
            "concepts": concepts, "pairs": pairs, "cases": self.cases,
        }
