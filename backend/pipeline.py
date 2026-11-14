"""Biến kết quả SAM3 thành các đề xuất nhãn đúng guideline.

Luồng chung:  prompt theo label -> mask -> khử trùng -> luật guideline -> hình học CVAT.
Mọi đề xuất chỉ là GỢI Ý: người annotate xem lại trên panel rồi mới ghi vào CVAT.
Các trường hợp không chắc được gắn cờ theo đúng bảng Issue type của guideline.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import cv2
import numpy as np

from . import geometry as G
from .engines import Engine
from .profiles import norm


log = logging.getLogger("sam3-assist")


def run_prompt(engine: Engine, state: Any, prompt: str, threshold: float, label: str, warnings: Optional[list]) -> list:
    """Chạy một prompt. Prompt lỗi thì bỏ qua và báo trên panel, không làm hỏng cả lượt gợi ý."""
    try:
        return engine.segment_text(state, prompt, threshold)
    except Exception as exc:  # noqa: BLE001 - lỗi của một prompt không được chặn các label khác
        shown = prompt if len(prompt) <= 60 else prompt[:57] + "..."
        message = f"Bỏ qua prompt \"{shown}\" của label '{label}': {str(exc)[:200]}"
        log.warning(message)
        if warnings is not None and message not in warnings:
            warnings.append(message)
        return []


@dataclass
class Det:
    cfg: dict
    mask: np.ndarray
    score: float
    prompt: str
    box: list[float]
    flags: list[dict] = field(default_factory=list)
    occluded: bool = False


def add_flag(flags: list[dict], code: str, message: str) -> None:
    if not any(f["code"] == code and f["message"] == message for f in flags):
        flags.append({"code": code, "message": message})


# ------------------------------------------------------------ phát hiện -----
def detect(engine: Engine, state: Any, cfgs: list[dict], clean: bool = True, warnings: Optional[list] = None) -> list[Det]:
    dets: list[Det] = []
    for cfg in cfgs:
        for prompt in cfg["prompts"]:
            for inst in run_prompt(engine, state, prompt, cfg["threshold"], cfg["name"], warnings):
                mask = inst.mask.astype(bool)
                if clean and cfg["role"] == "object":
                    mask = G.clean_mask(mask)
                if np.count_nonzero(mask) < cfg["min_area_px"]:
                    continue
                box = G.mask_bbox(mask)
                if box is not None:
                    dets.append(Det(cfg, mask, float(inst.score), prompt, box))
    return dets


def suppress(dets: list[Det], iou_thr: float, class_margin: float) -> list[Det]:
    """Giữ đề xuất điểm cao nhất khi hai mask trùng nhau (cùng label hoặc cùng nhóm loại trừ)."""
    kept: list[Det] = []
    for det in sorted(dets, key=lambda d: d.score, reverse=True):
        duplicate = False
        for other in kept:
            same_label = other.cfg["label_id"] == det.cfg["label_id"]
            same_group = det.cfg.get("group") and det.cfg.get("group") == other.cfg.get("group")
            if not (same_label or same_group) or G.box_inter(det.box, other.box) <= 0:
                continue
            if not same_label and det.cfg["role"] == "lane":
                # Vạch kẻ khác class: so theo phần nằm trong nhau (vạch đơn nằm trong vạch đôi).
                inter = np.count_nonzero(det.mask & other.mask)
                overlap = inter / max(1, min(np.count_nonzero(det.mask), np.count_nonzero(other.mask)))
            else:
                overlap = G.mask_iou(det.mask, other.mask)
            if overlap >= iou_thr:
                duplicate = True
                if not same_label and other.score - det.score < class_margin:
                    add_flag(
                        other.flags,
                        "UNCERTAIN_CLASS",
                        f"Cũng khớp '{det.cfg['name']}' ({det.score:.2f} so với {other.score:.2f}).",
                    )
                break
        if not duplicate:
            kept.append(det)
    return kept


# ------------------------------------------------------- luật guideline -----
def rider_split(dets: list[Det], rules: dict, cfg_by_name: dict[str, dict]) -> None:
    """Người ngồi trên xe hai bánh -> rider; người còn lại -> pedestrian/person."""
    rule = rules.get("rider_split") or {}
    if not rule.get("enabled"):
        return
    walker = cfg_by_name.get(norm(rule.get("pedestrian_label", "")))
    rider = cfg_by_name.get(norm(rule.get("rider_label", "")))
    if not walker or not rider or not walker["enabled"] or not rider["enabled"]:
        return
    vehicle_names = {norm(v) for v in rule.get("vehicle_labels", [])}
    vehicles = [d for d in dets if norm(d.cfg["name"]) in vehicle_names]
    if not vehicles:
        return  # không thấy xe hai bánh: giữ nguyên kết quả theo prompt
    min_overlap = float(rule.get("min_overlap", 0.2))
    for det in dets:
        if det.cfg["label_id"] not in (walker["label_id"], rider["label_id"]):
            continue
        best = 0.0
        for veh in vehicles:
            inter = G.box_inter(det.box, veh.box)
            if inter <= 0:
                continue
            # Phần dưới của người phải chồng lên xe (đang ngồi trên xe, không phải đứng sau xe).
            if det.box[3] < veh.box[1] + 0.2 * (veh.box[3] - veh.box[1]):
                continue
            best = max(best, inter / max(1.0, min(G.box_area(det.box), G.box_area(veh.box))))
        target = rider if best >= min_overlap else walker
        if target["label_id"] != det.cfg["label_id"]:
            det.cfg = target
        if min_overlap * 0.5 <= best < min_overlap * 2:
            add_flag(
                det.flags,
                "UNCERTAIN_CLASS",
                f"{walker['name']} hay {rider['name']}? Mức chồng lên xe hai bánh là {best:.0%}.",
            )


def mark_occluded(dets: list[Det], rules: dict) -> None:
    """Ước lượng occluded: bị một đối tượng ở gần camera hơn (đáy thấp hơn) đè lên."""
    rule = rules.get("occluded") or {}
    if not rule.get("enabled"):
        return
    min_ioa = float(rule.get("min_overlap", 0.15))
    for det in dets:
        area = G.box_area(det.box)
        for other in dets:
            if other is det or other.box[3] <= det.box[3]:
                continue
            if area > 0 and G.box_inter(det.box, other.box) / area >= min_ioa:
                det.occluded = True
                add_flag(
                    det.flags,
                    "ATTRIBUTE_CHECK",
                    f"Đặt occluded=true vì bị '{other.cfg['name']}' che. Cần kiểm tra lại.",
                )
                break


def object_attributes(det: Det, box: list[float], width: int, height: int, rules: dict, warnings: list[str]) -> dict:
    attrs: dict[str, str] = {}
    trunc = rules.get("truncated") or {}
    if trunc.get("enabled"):
        attr = norm(trunc.get("attribute", "truncated"))
        touching = G.touches_border(box, width, height, float(trunc.get("border_px", 2)))
        if attr in det.cfg["attributes"]:
            attrs[attr] = "true" if touching else "false"
        elif touching:
            msg = f"Label '{det.cfg['name']}' không có attribute '{attr}' trong task nên không gán được truncated."
            if msg not in warnings:
                warnings.append(msg)
    low = rules.get("low_visibility") or {}
    if low.get("enabled"):
        side = min(box[2] - box[0], box[3] - box[1])
        if side < float(low.get("min_side_px", 12)):
            add_flag(det.flags, "LOW_VISIBILITY", f"Đối tượng rất nhỏ (cạnh ngắn {side:.0f}px).")
        elif det.score < det.cfg["threshold"] + float(low.get("score_margin", 0.08)):
            add_flag(det.flags, "LOW_VISIBILITY", f"Độ tin cậy thấp ({det.score:.2f}).")
    return attrs


# ----------------------------------------------------------- đề xuất --------
def make_proposal(cfg: dict, kind: str, points: list, score: float, bbox: list[float], **extra: Any) -> dict:
    return {
        "label_id": cfg["label_id"],
        "label_name": cfg["name"],
        "type": kind,
        "points": points,
        "score": round(float(score), 3),
        "bbox": [round(float(v), 2) for v in bbox],
        "occluded": bool(extra.get("occluded", False)),
        "attributes": extra.get("attributes", {}),
        "z_order": int(extra.get("z_order", 0)),
        "flags": extra.get("flags", []),
        "checked": bool(extra.get("checked", True)),
        "preview": extra.get("preview"),
    }


def objects_to_proposals(dets: list[Det], width: int, height: int, rules: dict, warnings: list[str]) -> list[dict]:
    eps = float((rules.get("polygon") or {}).get("epsilon", 1.5))
    out = []
    for det in dets:
        box = G.clamp_box(det.box, width, height)
        attrs = object_attributes(det, box, width, height, rules, warnings)
        common = dict(occluded=det.occluded, attributes=attrs, flags=det.flags)
        shape = det.cfg["shape"]
        if shape == "rectangle":
            out.append(make_proposal(det.cfg, "rectangle", [round(v, 2) for v in box], det.score, box, **common))
        elif shape == "polygon":
            polys = G.mask_to_polygons(det.mask, eps, det.cfg["min_area_px"])
            if not polys:
                continue
            if len(polys) > 1:
                add_flag(
                    det.flags,
                    "UNCERTAIN_BOUNDARY",
                    f"Đối tượng bị tách thành {len(polys)} mảnh (có thể do che khuất); chỉ lấy mảnh lớn nhất.",
                )
            out.append(make_proposal(det.cfg, "polygon", polys[0], det.score, box, **common))
        elif shape == "mask":
            out.append(
                make_proposal(
                    det.cfg, "mask", G.encode_cvat_mask(det.mask), det.score, box,
                    preview=G.mask_to_polygons(det.mask, 1.5, 4), **common,
                )
            )
    return out


# -------------------------------------------------------------- vạch kẻ -----
def _lane_merge_radius(cfg: dict, lane_rule: dict) -> int:
    """Khoảng cách gom các mảnh mask cùng label thành một vạch."""
    if cfg.get("merge_px") is not None:
        return int(cfg["merge_px"])
    if cfg["polyline_mode"] == "long_edges":
        return int(lane_rule.get("crosswalk_merge_px", 40))  # các sọc của một crosswalk
    if cfg.get("lane") or cfg["polyline_mode"] == "edges":
        return int(lane_rule.get("double_merge_px", 12))  # hai nét của một vạch đôi
    return 0


def build_lanes(dets: list[Det], image: np.ndarray, rules: dict, cfg_by_name: dict[str, dict]) -> list[dict]:
    """Từ mask vạch kẻ ra polyline theo quy ước của từng label.

    centerline : một đường theo tim vạch (vạch đơn, curb)
    edges      : hai đường theo hai mép ngoài của cả vạch (vạch đôi)
    long_edges : hai đường thẳng theo hai mép dài của cả vùng (crosswalk)
    """
    lane_rule = rules.get("lane") or {}
    color_rule = rules.get("lane_color") or {}
    kind_rule = rules.get("lane_kind") or {}
    step = int(lane_rule.get("sample_step_px", 10))
    max_gap = int(lane_rule.get("max_gap_px", 30))
    join_gap = float(lane_rule.get("join_gap_px", 60))
    label_map = color_rule.get("labels") or {}

    def target_for(kind: str, color: str) -> Optional[dict]:
        name = (label_map.get(kind) or {}).get(color)
        target = cfg_by_name.get(norm(name)) if name else None
        return target if target and target["supported"] else None

    # 1. Màu đo từ pixel thật.
    entries = []
    for det in dets:
        cfg, flags = det.cfg, list(det.flags)
        info = cfg.get("lane") or {}
        if color_rule.get("enabled") and info.get("color"):
            seen = G.classify_marking_color(image, det.mask)
            if seen and seen != info["color"]:
                target = target_for(info.get("kind", ""), seen)
                if target:
                    add_flag(flags, "UNCERTAIN_CLASS",
                             f"Prompt cho '{cfg['name']}' nhưng màu đo được là '{seen}'; đã chuyển sang '{target['name']}'.")
                    cfg = target
        entries.append({"cfg": cfg, "mask": det.mask, "score": det.score, "flags": flags})

    # 2. Gom các mảnh cùng label nằm sát nhau: hai nét của vạch đôi, các sọc của một crosswalk.
    by_label: dict[int, list[dict]] = {}
    for entry in entries:
        by_label.setdefault(entry["cfg"]["label_id"], []).append(entry)
    clusters = []
    for group in by_label.values():
        cfg = group[0]["cfg"]
        for idx in G.cluster_masks([e["mask"] for e in group], _lane_merge_radius(cfg, lane_rule)):
            members = [group[i] for i in idx]
            flags: list[dict] = []
            for m in members:
                for f in m["flags"]:
                    add_flag(flags, f["code"], f["message"])
            clusters.append({
                "cfg": cfg,
                "mask": np.logical_or.reduce([m["mask"] for m in members]),
                "score": max(m["score"] for m in members),
                "flags": flags,
            })

    # 3. Đơn hay đôi: mask gồm hai dải song song thì là vạch đôi.
    if kind_rule.get("enabled", True):
        for c in clusters:
            info = c["cfg"].get("lane") or {}
            if info.get("kind") != "single":
                continue
            fraction = G.count_strips(c["mask"], step=8)
            target = target_for("double", info.get("color", ""))
            if target and fraction >= float(kind_rule.get("min_fraction", 0.5)):
                add_flag(c["flags"], "UNCERTAIN_CLASS",
                         f"Thấy hai nét song song ({fraction:.0%} chiều dài) nên đã chuyển '{c['cfg']['name']}' sang '{target['name']}'.")
                c["cfg"] = target

    # 4. Dựng polyline theo quy ước của label.
    buckets: dict[tuple, dict] = {}
    for c in clusters:
        cfg = c["cfg"]
        mode = cfg["polyline_mode"]
        if mode == "long_edges":
            lines = G.crosswalk_edges(c["mask"])
        elif mode == "edges":
            lines = G.mask_side_edges(c["mask"], step=float(lane_rule.get("edge_step_px", 8)), max_gap=join_gap)
        else:
            lines = [("center", l) for l in G.mask_to_centerlines(c["mask"], step, max_gap)]
        for side, pts in lines:
            bucket = buckets.setdefault((cfg["label_id"], side), {"cfg": cfg, "lines": [], "score": 0.0, "flags": []})
            bucket["lines"].append(pts)
            bucket["score"] = max(bucket["score"], c["score"])
            for f in c["flags"]:
                add_flag(bucket["flags"], f["code"], f["message"])

    items = []
    for bucket in buckets.values():
        cfg, lines = bucket["cfg"], bucket["lines"]
        if cfg["join_dashes"] and cfg["polyline_mode"] != "long_edges":
            lines = G.join_collinear(lines, join_gap)  # chỉ nối các đoạn cùng một mép
        for line in lines:
            line = G.simplify_polyline(line, float(lane_rule.get("epsilon", 1.5)))
            if len(line) >= 2 and G.polyline_length(line) >= float(lane_rule.get("min_length_px", 20)):
                items.append({"cfg": cfg, "pts": line, "score": bucket["score"], "flags": list(bucket["flags"])})
    return items


def lanes_to_proposals(items: list[dict], width: int, height: int) -> list[dict]:
    out = []
    for item in items:
        pts = item["pts"].copy()
        pts[:, 0] = np.clip(pts[:, 0], 0, width)
        pts[:, 1] = np.clip(pts[:, 1], 0, height)
        bbox = [pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()]
        out.append(
            make_proposal(
                item["cfg"], "polyline", [round(float(v), 2) for v in pts.ravel()], item["score"], bbox,
                flags=item["flags"],
            )
        )
    return out


# -------------------------------------------------------- drivable area -----
def drivable(
    engine: Engine, state: Any, rules: dict, direct_cfg: dict, alt_cfg: Optional[dict],
    obj_dets: list[Det], lane_items: list[dict], width: int, height: int, warnings: list[str],
) -> list[dict]:
    """area/direct = làn xe đang chạy; area/alternative = làn cùng chiều có thể chuyển sang.

    Lấy mask mặt đường, trừ đi các phương tiện/người, cắt theo vạch kẻ làn đã tìm được.
    Mảnh chứa vị trí xe (giữa mép dưới ảnh) là direct. Mảnh nằm bên kia vạch phân chiều bị loại.
    """
    rule = rules.get("drivable") or {}
    road = np.zeros((height, width), bool)
    score = 0.0
    for prompt in direct_cfg["prompts"]:
        for inst in run_prompt(engine, state, prompt, direct_cfg["threshold"], direct_cfg["name"], warnings):
            road |= inst.mask.astype(bool)
            score = max(score, float(inst.score))
    if not road.any():
        warnings.append("Không tìm thấy mặt đường nên không đề xuất drivable area.")
        return []

    if obj_dets:
        blocked = np.zeros((height, width), np.uint8)
        for det in obj_dets:
            blocked |= det.mask.astype(np.uint8)
        road &= cv2.dilate(blocked, np.ones((5, 5), np.uint8)) == 0

    thick = int(rule.get("barrier_px", 7))
    barrier = np.zeros((height, width), np.uint8)
    separator = np.zeros((height, width), np.uint8)
    separators = {norm(n) for n in rule.get("opposite_separators", [])}
    ignored = {norm(n) for n in rule.get("ignore_lanes", [])}
    for item in lane_items:
        name = norm(item["cfg"]["name"])
        pts = item["pts"]
        if name in ignored or abs(pts[-1][1] - pts[0][1]) < 0.35 * abs(pts[-1][0] - pts[0][0]):
            continue  # bỏ crosswalk và các vạch gần như nằm ngang (vạch dừng)
        ext = G.extend_polyline(pts, float(rule.get("extend_near_px", 1500)), float(rule.get("extend_far_px", 40)))
        line = [np.round(ext).astype(np.int32)]
        cv2.polylines(barrier, line, False, 1, thick)
        if name in separators:
            cv2.polylines(separator, line, False, 1, thick + 2)

    free = (road & (barrier == 0)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    min_area = float(rule.get("min_area_px", 1500))
    comps = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area]
    if not comps:
        warnings.append("Mặt đường tìm được quá nhỏ nên không đề xuất drivable area.")
        return []

    ego = rule.get("ego_point", [0.5, 0.97])
    ex, ey = min(width - 1, int(width * ego[0])), min(height - 1, int(height * ego[1]))
    direct = int(lab[ey, ex]) if int(lab[ey, ex]) in comps else None
    if direct is None:
        window = lab[int(height * 0.7):, int(width * 0.35): int(width * 0.65)]
        counts = {i: int(np.count_nonzero(window == i)) for i in comps}
        best = max(counts, key=counts.get)
        direct = best if counts[best] > 0 else None
    if direct is None:
        warnings.append("Không xác định được làn xe đang chạy (area/direct). Hãy vẽ tay.")
        return []

    grow = np.ones((2 * (thick // 2) + 1,) * 2, np.uint8)
    eps = float(rule.get("polygon_epsilon", 2.0))

    def region(index: int) -> np.ndarray:
        return (cv2.dilate((lab == index).astype(np.uint8), grow) > 0) & road

    def polygon(mask: np.ndarray) -> Optional[list[float]]:
        polys = G.mask_to_polygons(mask, eps, min_area)
        return polys[0] if polys else None

    out = []
    flags: list[dict] = []
    if not barrier.any():
        add_flag(flags, "UNCERTAIN_BOUNDARY", "Không thấy vạch làn để tách; vùng direct có thể đang gồm nhiều làn.")
    direct_mask = region(direct)
    poly = polygon(direct_mask)
    if poly:
        out.append(make_proposal(direct_cfg, "polygon", poly, score, G.mask_bbox(direct_mask), flags=flags))

    if not alt_cfg or not alt_cfg["enabled"]:
        return out
    has_direct = (lab == direct).any(axis=1)
    skipped = 0
    others = sorted((i for i in comps if i != direct), key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    for index in others[: int(rule.get("max_alternatives", 4))]:
        alt_flags: list[dict] = []
        common = np.flatnonzero(has_direct & (lab == index).any(axis=1))
        if common.size == 0:
            add_flag(alt_flags, "UNCERTAIN_SCOPE", "Không so được vị trí với làn direct; kiểm tra có đúng làn cùng chiều.")
        else:
            opposite = False
            for y in common[[common.size // 4, common.size // 2, (3 * common.size) // 4]]:
                xd = int(np.flatnonzero(lab[y] == direct).mean())
                xc = int(np.flatnonzero(lab[y] == index).mean())
                if separator[y, min(xd, xc): max(xd, xc) + 1].any():
                    opposite = True
                    break
            if opposite:
                skipped += 1
                continue
            if not separator.any():
                add_flag(alt_flags, "UNCERTAIN_SCOPE", "Không thấy vạch phân chiều; kiểm tra vùng này có cùng chiều không.")
        alt_mask = region(index)
        poly = polygon(alt_mask)
        if poly:
            out.append(
                make_proposal(
                    alt_cfg, "polygon", poly, score, G.mask_bbox(alt_mask),
                    flags=alt_flags, checked=not alt_flags,
                )
            )
    if skipped:
        warnings.append(f"Đã bỏ {skipped} vùng đường nằm bên kia vạch phân chiều (đường ngược chiều không phải alternative).")
    return out


# -------------------------------------------------- semantic segmentation ---
def semantic(
    engine: Engine, state: Any, profile: dict, cfgs: list[dict], existing: list[dict],
    width: int, height: int, warnings: list[str],
) -> list[dict]:
    """Mỗi pixel đúng một class: ghép mask theo thứ tự ưu tiên, không chồng lấn, không tự lấp vùng lớn."""
    rules = profile.get("rules", {})
    sem = rules.get("semantic") or {}
    active = [c for c in cfgs if c["enabled"] and c["supported"]]
    cfg_by_name = {norm(c["name"]): c for c in cfgs if c["supported"]}
    dets = detect(engine, state, active, clean=False, warnings=warnings)
    rider_split(dets, rules, cfg_by_name)

    # Ưu tiên cao vẽ sau cùng để đè lên nền (xe đè lên đường, người đè lên xe...).
    priority = [norm(n) for n in sem.get("priority", [])]

    def rank(cfg: dict) -> int:
        name = norm(cfg["name"])
        return priority.index(name) if name in priority else -1  # label lạ: ưu tiên cao nhất

    classes = sorted({d.cfg["label_id"]: d.cfg for d in dets}.values(), key=rank, reverse=True)
    index_of = {c["label_id"]: i + 1 for i, c in enumerate(classes)}
    best_score = {c["label_id"]: 0.0 for c in classes}
    labelmap = np.zeros((height, width), np.int32)
    for cfg in classes:
        for det in sorted((d for d in dets if d.cfg["label_id"] == cfg["label_id"]), key=lambda d: d.score):
            labelmap[det.mask] = index_of[cfg["label_id"]]
            best_score[cfg["label_id"]] = max(best_score[cfg["label_id"]], det.score)

    # Mảnh quá nhỏ: bỏ, rồi lấp các lỗ nhỏ bằng class gần nhất.
    min_region = int(sem.get("min_region_px", 40))
    for value in range(1, len(classes) + 1):
        n, lab, stats, _ = cv2.connectedComponentsWithStats((labelmap == value).astype(np.uint8), connectivity=8)
        small = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] < min_region)
        small = small[small > 0]
        if small.size:
            labelmap[np.isin(lab, small)] = 0

    occupied = np.zeros((height, width), bool)
    if sem.get("respect_existing", True):
        for shape in existing:
            mask = G.rasterize_shape(shape, width, height)
            if mask is not None:
                occupied |= mask
        if occupied.any():
            labelmap[occupied] = -1
            warnings.append("Frame đã có annotation: tool chỉ đề xuất cho các pixel chưa được gán.")

    fill_limit = int(sem.get("fill_gaps_px", 150))
    unlabeled = labelmap == 0
    if fill_limit > 0 and unlabeled.any() and not unlabeled.all():
        n, lab, stats, _ = cv2.connectedComponentsWithStats(unlabeled.astype(np.uint8), connectivity=4)
        small = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] <= fill_limit)
        small = small[small > 0]
        if small.size:
            _, nearest = cv2.distanceTransformWithLabels(
                unlabeled.astype(np.uint8), cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL
            )
            lut = np.zeros(int(nearest.max()) + 1, np.int32)
            lut[nearest[~unlabeled]] = labelmap[~unlabeled]
            holes = np.isin(lab, small)
            labelmap[holes] = lut[nearest[holes]]
    labelmap[labelmap < 0] = 0

    remaining = 100.0 * np.count_nonzero((labelmap == 0) & ~occupied) / (width * height)
    if remaining > float(sem.get("warn_unlabeled_pct", 1.0)):
        warnings.append(
            f"Còn {remaining:.1f}% pixel chưa có class. Tool không tự lấp vùng không chắc: "
            "hãy vẽ tay hoặc tạo Issue cho các vùng này."
        )

    eps = float(sem.get("polygon_epsilon", 1.0))
    merge = bool(sem.get("merge_instances", False))
    out = []
    for cfg in classes:
        class_mask = labelmap == index_of[cfg["label_id"]]
        if not class_mask.any():
            continue
        if merge and cfg["shape"] == "mask":
            pieces = [class_mask]
        else:
            n, lab = cv2.connectedComponents(class_mask.astype(np.uint8), connectivity=8)
            pieces = [lab == i for i in range(1, n)]
        for piece in pieces:
            bbox = G.mask_bbox(piece)
            score = best_score[cfg["label_id"]]
            if cfg["shape"] == "polygon":
                polys = G.mask_to_polygons(piece, eps, 1.0)
                if polys:
                    out.append(make_proposal(cfg, "polygon", polys[0], score, bbox))
            else:
                out.append(
                    make_proposal(
                        cfg, "mask", G.encode_cvat_mask(piece), score, bbox,
                        preview=G.mask_to_polygons(piece, 1.5, 1.0),
                    )
                )
    # Polygon không có lỗ: vùng nhỏ nằm trong vùng lớn phải có z_order cao hơn để không bị che.
    ordered = sorted(out, key=lambda p: -G.box_area(p["bbox"]))
    for z, proposal in enumerate(ordered):
        if proposal["type"] == "polygon":
            proposal["z_order"] = z
    return out


# ------------------------------------------------------------ điều phối -----
def shape_bbox(shape: dict) -> Optional[list[float]]:
    pts = shape.get("points") or []
    if shape.get("type") == "mask" and len(pts) >= 4:
        left, top, right, bottom = pts[-4:]
        return [left, top, right + 1, bottom + 1]
    if len(pts) >= 4:
        xs, ys = pts[0::2], pts[1::2]
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def mark_existing_duplicates(proposals: list[dict], existing: list[dict], iou_thr: float) -> None:
    for proposal in proposals:
        for shape in existing:
            if shape.get("label_id") != proposal["label_id"] or shape.get("type") != proposal["type"]:
                continue
            bbox = shape_bbox(shape)
            if bbox and G.box_iou(bbox, proposal["bbox"]) >= iou_thr:
                add_flag(proposal["flags"], "DUPLICATE", "Trùng với một annotation đã có trên frame.")
                proposal["checked"] = False
                break


def finalize(proposals: list[dict]) -> list[dict]:
    order = {"polygon": 0, "mask": 0, "polyline": 1, "rectangle": 2}
    proposals.sort(key=lambda p: (order.get(p["type"], 3), p["label_name"], -p["score"]))
    for i, proposal in enumerate(proposals):
        proposal["pid"] = i + 1
    return proposals


def propose(
    engine: Engine, state: Any, image: np.ndarray, profile: dict, cfgs: list[dict], existing: list[dict],
) -> dict:
    started = time.time()
    height, width = image.shape[:2]
    rules = profile.get("rules", {})
    warnings: list[str] = []
    active = [c for c in cfgs if c["enabled"] and c["supported"]]
    cfg_by_name = {norm(c["name"]): c for c in cfgs if c["supported"]}

    if profile.get("mode") == "semantic":
        proposals = semantic(engine, state, profile, cfgs, existing, width, height, warnings)
    else:
        dedup = rules.get("dedup") or {}
        dets = detect(engine, state, [c for c in active if c["role"] in ("object", "lane")], warnings=warnings)
        dets = suppress(dets, float(dedup.get("iou", 0.6)), float(dedup.get("class_margin", 0.15)))
        objects = [d for d in dets if d.cfg["role"] == "object"]
        rider_split(objects, rules, cfg_by_name)
        mark_occluded(objects, rules)
        lane_dets = [d for d in dets if d.cfg["role"] == "lane"]
        # Vạch nằm trong vùng crosswalk là sọc của crosswalk, không phải vạch làn.
        region_name = norm((rules.get("lane") or {}).get("exclusive_region_label", ""))
        crosswalks = [d.mask for d in lane_dets if norm(d.cfg["name"]) == region_name]
        if crosswalks:
            # Vùng crosswalk = phần giữa hai mép dài, kể cả khoảng trống giữa các sọc.
            region = np.zeros((height, width), bool)
            radius = int((rules.get("lane") or {}).get("crosswalk_merge_px", 40))
            for idx in G.cluster_masks(crosswalks, radius):
                part = np.logical_or.reduce([crosswalks[i] for i in idx])
                region |= part
                edges = G.crosswalk_edges(part)
                if len(edges) == 2:
                    region |= G.polygon_between(edges[0][1], edges[1][1], (height, width))
            lane_dets = [
                d for d in lane_dets
                if norm(d.cfg["name"]) == region_name
                or np.count_nonzero(d.mask & region) < 0.5 * np.count_nonzero(d.mask)
            ]
        lane_items = build_lanes(lane_dets, image, rules, cfg_by_name)
        proposals = objects_to_proposals(objects, width, height, rules, warnings)
        proposals += lanes_to_proposals(lane_items, width, height)
        direct = next((c for c in active if c["role"] == "drivable_direct"), None)
        alt = next((c for c in active if c["role"] == "drivable_alt"), None)
        if direct:
            proposals += drivable(engine, state, rules, direct, alt, objects, lane_items, width, height, warnings)
        elif alt:
            warnings.append(f"'{alt['name']}' được suy ra từ vùng direct; hãy bật cả label direct.")
        mark_existing_duplicates(proposals, existing, float(dedup.get("existing_iou", 0.7)))

    return {
        "width": width,
        "height": height,
        "profile_id": profile["id"],
        "proposals": finalize(proposals),
        "warnings": warnings,
        "seconds": round(time.time() - started, 2),
    }


def interactive(
    engine: Engine, state: Any, image: np.ndarray, profile: dict, cfg: dict,
    box: list[float], mode: str, prompt: Optional[str],
) -> dict:
    """Người dùng kéo một hộp quanh đối tượng: lấy đúng đối tượng đó ('single') hoặc mọi đối tượng giống nó ('similar')."""
    height, width = image.shape[:2]
    rules = profile.get("rules", {})
    warnings: list[str] = []
    dets = []
    for inst in engine.segment_box(state, box, prompt or None, 0.3):
        mask = G.clean_mask(inst.mask.astype(bool)) if cfg["shape"] != "polyline" else inst.mask.astype(bool)
        bbox = G.mask_bbox(mask)
        if bbox is not None:
            dets.append(Det(dict(cfg, role="lane" if cfg["shape"] == "polyline" else "object"), mask, float(inst.score), prompt or "", bbox))
    if mode == "single" and dets:
        best = max(dets, key=lambda d: G.box_iou(d.box, box))
        if G.box_iou(best.box, box) < 0.3:
            add_flag(best.flags, "UNCERTAIN_BOUNDARY", "Kết quả lệch nhiều so với hộp bạn vẽ.")
        dets = [best]
    if not dets:
        warnings.append("SAM3 không tìm thấy đối tượng trong hộp. Thử vẽ hộp sát hơn.")
    if cfg["shape"] == "polyline":
        items = build_lanes(dets, image, rules, {norm(cfg["name"]): cfg})
        proposals = lanes_to_proposals(items, width, height)
    else:
        proposals = objects_to_proposals(dets, width, height, rules, warnings)
    return {"width": width, "height": height, "proposals": finalize(proposals), "warnings": warnings}
