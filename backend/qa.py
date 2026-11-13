"""Kiểm tra annotation đang có trên frame theo checklist và tiêu chí reviewer trong guideline.

Chỉ kiểm tra được những gì đo được bằng hình học. Các mục cần mắt người (đúng class,
bỏ sót đối tượng) được đánh dấu 'manual' để người annotate tự rà.
"""
from __future__ import annotations

import numpy as np

from . import geometry as G
from .pipeline import shape_bbox
from .profiles import norm


def _finding(severity: str, code: str, message: str, shape: dict | None = None, bbox=None) -> dict:
    return {
        "severity": severity,  # error | warning | info
        "code": code,
        "message": message,
        "shape_id": (shape or {}).get("id"),
        "bbox": bbox or (shape_bbox(shape) if shape else None),
    }


def _attr_value(shape: dict, label: dict, attr_name: str):
    spec = next((a for a in label.get("attributes", []) if norm(a.get("name", "")) == attr_name), None)
    if not spec:
        return None
    for item in shape.get("attributes", []):
        if item.get("spec_id") == spec.get("id"):
            return str(item.get("value", "")).lower() == "true"
    return str(spec.get("default_value", "")).lower() == "true"


def run_qa(profile: dict, cfgs: list[dict], labels: list[dict], shapes: list[dict], width: int, height: int) -> dict:
    rules = profile.get("rules", {})
    qa = rules.get("qa") or {}
    semantic = profile.get("mode") == "semantic"
    cfg_by_id = {c["label_id"]: c for c in cfgs}
    label_by_id = {l["id"]: l for l in labels}
    findings: list[dict] = []
    tol = float(qa.get("bounds_tolerance_px", 0.5))

    for shape in shapes:
        cfg = cfg_by_id.get(shape.get("label_id"))
        label = label_by_id.get(shape.get("label_id"), {})
        name = (cfg or {}).get("name", f"label #{shape.get('label_id')}")
        kind, pts = shape.get("type"), shape.get("points") or []
        bbox = shape_bbox(shape)

        # --- Taxonomy: đúng class và đúng loại shape
        if cfg and profile["labels"] and not cfg["matched"]:
            findings.append(_finding("warning", "TAXONOMY", f"'{name}' không có trong guideline của profile này.", shape))
        if cfg and cfg["matched"] and kind != cfg["shape"] and not (semantic and kind in ("mask", "polygon")):
            findings.append(
                _finding("error", "TAXONOMY", f"'{name}' phải vẽ bằng {cfg['shape']} nhưng đang là {kind}.", shape)
            )

        # --- Hình học chung
        if kind != "mask" and bbox:
            if bbox[0] < -tol or bbox[1] < -tol or bbox[2] > width + tol or bbox[3] > height + tol:
                findings.append(_finding("error", "GEOMETRY", f"'{name}' vượt ra ngoài biên ảnh.", shape))

        if kind == "rectangle" and bbox:
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if w <= 0 or h <= 0:
                findings.append(_finding("error", "GEOMETRY", f"Bounding box '{name}' có diện tích bằng 0.", shape))
            elif min(w, h) < float(qa.get("min_box_side_px", 3)):
                findings.append(_finding("warning", "GEOMETRY", f"Bounding box '{name}' rất nhỏ ({w:.0f}x{h:.0f}px).", shape))
            trunc = rules.get("truncated") or {}
            if trunc.get("enabled"):
                value = _attr_value(shape, label, norm(trunc.get("attribute", "truncated")))
                touching = G.touches_border(bbox, width, height, float(trunc.get("border_px", 2)))
                if value is not None and touching and not value:
                    findings.append(_finding("warning", "ATTRIBUTES", f"'{name}' chạm biên ảnh nhưng truncated=false.", shape))
                elif value and not touching:
                    findings.append(_finding("warning", "ATTRIBUTES", f"'{name}' có truncated=true nhưng không chạm biên ảnh.", shape))
        elif kind == "polygon":
            if len(pts) < 6:
                findings.append(_finding("error", "GEOMETRY", f"Polygon '{name}' có ít hơn 3 đỉnh.", shape))
            elif G.polygon_self_intersects(np.asarray(pts).reshape(-1, 2)):
                findings.append(_finding("error", "GEOMETRY", f"Polygon '{name}' tự cắt chính nó.", shape))
        elif kind == "polyline":
            if len(pts) < 4:
                findings.append(_finding("error", "GEOMETRY", f"Polyline '{name}' có ít hơn 2 điểm.", shape))
            elif G.polyline_length(np.asarray(pts).reshape(-1, 2)) < float(qa.get("min_polyline_px", 10)):
                findings.append(_finding("warning", "GEOMETRY", f"Polyline '{name}' rất ngắn.", shape))

    # --- Trùng lặp: cùng label, cùng loại, gần như cùng vị trí
    dup_iou = float(qa.get("duplicate_iou", 0.85))
    for i, a in enumerate(shapes):
        for b in shapes[i + 1:]:
            if a.get("label_id") != b.get("label_id") or a.get("type") != b.get("type"):
                continue
            box_a, box_b = shape_bbox(a), shape_bbox(b)
            if box_a and box_b and G.box_iou(box_a, box_b) >= dup_iou:
                name = cfg_by_id.get(a.get("label_id"), {}).get("name", "?")
                findings.append(_finding("error", "DUPLICATE", f"Hai annotation '{name}' gần như trùng nhau.", b))

    # --- Semantic: chồng lấn và pixel bị bỏ sót
    coverage = None
    if semantic:
        owner = np.zeros((height, width), np.int32)
        overlap = np.zeros((height, width), bool)
        for shape in sorted(shapes, key=lambda s: s.get("z_order", 0)):
            mask = G.rasterize_shape(shape, width, height)
            if mask is None:
                continue
            label_id = int(shape.get("label_id", 0))
            if shape.get("type") == "mask":
                overlap |= mask & (owner != 0) & (owner != label_id)
            owner[mask] = label_id
        overlap_px = int(np.count_nonzero(overlap))
        if overlap_px > int(qa.get("max_overlap_px", 0)):
            findings.append(
                _finding("error", "OVERLAP", f"{overlap_px} pixel bị hai class khác nhau cùng chiếm.", bbox=G.mask_bbox(overlap))
            )
        unlabeled = owner == 0
        coverage = 100.0 * (1 - np.count_nonzero(unlabeled) / (width * height))
        if unlabeled.any():
            sev = "warning" if coverage >= float(qa.get("min_coverage_pct", 99.0)) else "error"
            findings.append(
                _finding(sev, "UNLABELED", f"{100 - coverage:.2f}% pixel chưa được gán class.", bbox=G.mask_bbox(unlabeled))
            )

    def status(*codes: str) -> str:
        hits = [f for f in findings if f["code"] in codes]
        if any(f["severity"] == "error" for f in hits):
            return "fail"
        return "warn" if hits else "pass"

    checklist = [
        {"item": "Đúng class và shape theo taxonomy", "status": status("TAXONOMY"), "auto": True},
        {"item": "Không annotation trùng lặp", "status": status("DUPLICATE"), "auto": True},
        {"item": "Hình học hợp lệ, không vượt biên ảnh, không tự cắt", "status": status("GEOMETRY"), "auto": True},
        {"item": "Attribute occluded/truncated", "status": status("ATTRIBUTES"), "auto": True},
    ]
    if semantic:
        checklist.append({"item": "Không chồng lấn giữa các class", "status": status("OVERLAP"), "auto": True})
        checklist.append({"item": "Không bỏ sót pixel", "status": status("UNLABELED"), "auto": True})
    checklist += [
        {"item": "Không bỏ sót đối tượng thuộc scope", "status": "manual", "auto": False},
        {"item": "Case không chắc đã tạo Issue cho mentor", "status": "manual", "auto": False},
        {"item": "Đã rà 02 lần toàn bộ dữ liệu trước khi save", "status": "manual", "auto": False},
    ]
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    return {
        "width": width,
        "height": height,
        "shape_count": len(shapes),
        "coverage_pct": None if coverage is None else round(coverage, 2),
        "findings": findings,
        "checklist": checklist,
    }
