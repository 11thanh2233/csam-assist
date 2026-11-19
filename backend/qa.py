"""Kiểm tra annotation đang có trên frame theo checklist và tiêu chí reviewer trong guideline.

Chỉ kiểm tra được những gì đo được bằng hình học. Các mục cần mắt người (đúng class,
bỏ sót đối tượng) được đánh dấu 'manual' để người annotate tự rà.
"""
from __future__ import annotations

import re

import cv2
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
        elif kind == "skeleton":
            _skeleton_checks(shape, name, cfg, rules, width, height, findings)

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

    if profile.get("mode") == "keypoints":
        _skeleton_set_checks(shapes, cfgs, rules, findings)
        has_body = any(c["shape"] == "skeleton" and c["role"] == "body" for c in cfgs)
        has_face = any(c["shape"] == "skeleton" and c["role"] == "face" for c in cfgs)
        checklist = [
            {"item": "Đúng số skeleton, đủ điểm, không thừa; mỗi điểm có trạng thái", "status": status("TAXONOMY", "DUPLICATE", "SCOPE"), "auto": True},
            {"item": "Trái/phải theo phía của ảnh; thứ tự điểm đúng, không bắt chéo", "status": status("LEFT_RIGHT", "TOPOLOGY"), "auto": True},
            {"item": "Trạng thái Visible / Occluded / Outside hợp lệ (điểm sát mép, điểm trùng nhau, ngưỡng bị che)", "status": status("STATE"), "auto": True},
        ]
        if has_body:
            checklist.append({"item": "Chỉ gán người lái; ảnh không có người lái thì bỏ qua", "status": "manual", "auto": False})
            checklist.append({"item": "Khớp đặt ở tâm khớp (ống tai, nếp gấp cổ tay, tâm khớp háng), không đặt lên vô-lăng hay cạp quần", "status": "manual", "auto": False})
        if has_face:
            checklist.append({"item": "Không đặt điểm lên vật che (kính, tóc, tay, khẩu trang) hoặc ra ngoài vùng mặt", "status": "manual", "auto": False})
            checklist.append({"item": "Lông mày ở bờ trên, mắt ở bờ mi, môi ở ranh giới môi đỏ; điểm 13 chưa chạm chóp mũi", "status": "manual", "auto": False})
        checklist += [
            {"item": "Sai số: ±5px với điểm Visible, ±20px với điểm Occluded (phóng to để kiểm)", "status": "manual", "auto": False},
            {"item": "Case không chắc đã tạo Issue/comment và báo mentor", "status": "manual", "auto": False},
            {"item": "Đã rà 02 lần toàn bộ dữ liệu trước khi save", "status": "manual", "auto": False},
        ]
        order = {"error": 0, "warning": 1, "info": 2}
        findings.sort(key=lambda f: order[f["severity"]])
        return {"width": width, "height": height, "shape_count": len(shapes), "coverage_pct": None,
                "findings": findings, "checklist": checklist}

    checklist = [
        {"item": "Đúng class và shape theo taxonomy", "status": status("TAXONOMY"), "auto": True},
        {"item": "Không annotation trùng lặp", "status": status("DUPLICATE"), "auto": True},
        {"item": "Hình học hợp lệ, không vượt biên ảnh, không tự cắt", "status": status("GEOMETRY"), "auto": True},
        {"item": "Attribute occluded/truncated", "status": status("ATTRIBUTES"), "auto": True},
    ]
    if semantic:
        checklist.append({"item": "Không chồng lấn giữa các class", "status": status("OVERLAP"), "auto": True})
        checklist.append({"item": "Không bỏ sót pixel", "status": status("UNLABELED"), "auto": True})
    if profile.get("checklist"):
        # Profile nạp từ guideline có checklist riêng: dùng nguyên văn checklist đó cho phần tự kiểm.
        checklist += [{"item": str(item), "status": "manual", "auto": False} for item in profile["checklist"]]
    else:
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


def _elements(shape: dict, cfg: dict | None) -> list[dict]:
    from .keypoints import SOURCE_OF_INDEX

    spec = {k["label_id"]: k for k in (cfg or {}).get("keypoints", [])}
    out = []
    for e in shape.get("elements") or []:
        pts = e.get("points") or []
        if len(pts) < 2:
            continue
        kp = spec.get(e.get("label_id"), {})
        idx = kp.get("index")
        out.append({"label_id": e.get("label_id"), "name": kp.get("name", ""), "x": float(pts[0]), "y": float(pts[1]),
                    "source": SOURCE_OF_INDEX[idx] if isinstance(idx, int) and idx < len(SOURCE_OF_INDEX) else None,
                    "outside": bool(e.get("outside")), "occluded": bool(e.get("occluded"))})
    return out


def _ordered(elements: list[dict], cfg: dict) -> list[dict]:
    """Các điểm theo đúng thứ tự của label (thứ tự contour), bỏ điểm thiếu."""
    by_id = {e["label_id"]: e for e in elements}
    return [by_id[k["label_id"]] for k in cfg.get("keypoints", []) if k["label_id"] in by_id]


def _skeleton_checks(shape: dict, name: str, cfg: dict | None, rules: dict, width: int, height: int,
                     findings: list[dict]) -> None:
    """Kiểm tra một skeleton theo checklist của guideline HumanPose-17 và Facial Landmark-50."""
    from .keypoints import body_checks

    cfg = cfg or {}
    elements = _elements(shape, cfg)
    expected = len(cfg.get("keypoints", []))
    if expected and len({e["label_id"] for e in elements}) != expected:
        findings.append(_finding("error", "TAXONOMY",
                                 f"Skeleton '{name}' có {len(elements)} điểm, guideline yêu cầu đủ {expected} điểm (điểm không thấy đặt Occluded hoặc Outside, không bỏ).", shape))
    shown = [e for e in elements if not e["outside"]]
    role = cfg.get("role")
    if not shown:
        if role == "body":
            findings.append(_finding("warning", "SCOPE",
                                     f"Skeleton '{name}' Outside toàn bộ: ảnh không có người lái thì xoá skeleton, không để skeleton rỗng.", shape))
        return
    edge = [e["name"] or "?" for e in shown if e["x"] <= 1 or e["y"] <= 1 or e["x"] >= width - 2 or e["y"] >= height - 2]
    if edge:
        findings.append(_finding("warning", "STATE",
                                 f"'{name}': {len(edge)} điểm nằm sát mép ảnh ({', '.join(edge[:4])}). Điểm ngoài khung hình phải là Outside, không kéo về mép.", shape))
    if role == "body":
        flags: list[dict] = []
        body_checks(elements, rules.get("pose") or {}, flags)
        for f in flags:
            code = "STATE" if f["code"] == "ATTRIBUTE_CHECK" else "LEFT_RIGHT"
            findings.append(_finding("warning", code, f"'{name}': {f['message']}", shape))
        return

    # ---- skeleton mặt
    total = expected or len(elements)
    seen = sum(1 for e in shown if not e["occluded"])
    need = int(cfg.get("min_visible") or 0)
    if len(shown) < len(elements):
        findings.append(_finding("warning", "STATE",
                                 f"'{name}': {len(elements) - len(shown)} điểm Outside lẻ. Guideline: đặt đủ contour (điểm không thấy là Occluded), hoặc Outside CẢ skeleton; chỉ điểm ngoài khung hình mới Outside riêng.", shape))
    elif need and seen < need:
        findings.append(_finding("info", "STATE",
                                 f"'{name}': chỉ {seen}/{total} điểm Visible, dưới ngưỡng {need}/{total}. Chỉ giữ khi có đủ bằng chứng thị giác; không thì Outside cả skeleton.", shape))
    ordered = _ordered(elements, cfg)
    if len(shown) < len(elements) or len(ordered) < 3:
        return
    pts = np.array([[e["x"], e["y"]] for e in ordered], float)
    span = float(np.ptp(pts[:, 0])) or 1.0
    if cfg.get("closed"):
        area = 0.5 * float(np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1]))
        flat = abs(area) < 0.01 * span * span  # mắt nhắm, miệng đóng: hai bờ trùng nhau là hợp lệ
        if not flat and G.polygon_self_intersects(pts):
            findings.append(_finding("error", "TOPOLOGY", f"Contour '{name}' tự bắt chéo: kiểm tra thứ tự điểm.", shape))
        elif not flat and area < 0:
            findings.append(_finding("error", "TOPOLOGY",
                                     f"Contour '{name}' chạy ngược chiều kim đồng hồ. Guideline: bắt đầu từ điểm trái nhất, bờ trên từ trái sang phải rồi vòng về theo bờ dưới.", shape))
        if int(np.argmin(pts[:, 0])) != 0 and pts[0, 0] - pts[:, 0].min() > 0.15 * span:
            findings.append(_finding("warning", "TOPOLOGY", f"'{name}': điểm đầu tiên không phải điểm trái nhất của contour.", shape))
    elif cfg.get("face_side") and pts[0, 0] - pts[-1, 0] > 0.15 * span:
        findings.append(_finding("warning", "TOPOLOGY",
                                 f"'{name}': thứ tự điểm đang chạy từ phải sang trái. Guideline đánh số từ trái sang phải trên ảnh.", shape))


def _skeleton_set_checks(shapes: list[dict], cfgs: list[dict], rules: dict, findings: list[dict]) -> None:
    """Kiểm tra giữa các skeleton: mỗi label một skeleton, đủ bộ skeleton mặt, trái/phải, môi trong nằm trong môi ngoài."""
    qa = rules.get("qa") or {}
    cfg_by_id = {c["label_id"]: c for c in cfgs}
    by_label: dict[int, list[dict]] = {}
    for shape in shapes:
        if shape.get("type") == "skeleton" and shape.get("label_id") in cfg_by_id:
            by_label.setdefault(shape["label_id"], []).append(shape)
    if qa.get("one_per_label"):
        for label_id, group in by_label.items():
            if len(group) > 1:
                findings.append(_finding("error", "SCOPE",
                                         f"Có {len(group)} skeleton '{cfg_by_id[label_id]['name']}' trên frame. Guideline chỉ gán một (người lái).", group[1]))
    faces = [c for c in cfgs if c["shape"] == "skeleton" and c["role"] == "face" and c["matched"]]
    present = [c for c in faces if c["label_id"] in by_label]
    if present and len(present) < len(faces):
        missing = [c["name"] for c in faces if c["label_id"] not in by_label]
        findings.append(_finding("error", "TAXONOMY",
                                 f"Thiếu {len(missing)}/{len(faces)} skeleton mặt: {', '.join(missing)}. Bộ phận bị che vẫn phải có skeleton (Occluded, hoặc Outside cả skeleton).",
                                 by_label[present[0]["label_id"]][0]))

    def shown_points(cfg: dict) -> np.ndarray | None:
        group = by_label.get(cfg["label_id"])
        if not group:
            return None
        pts = [[e["x"], e["y"]] for e in _ordered(_elements(group[0], cfg), cfg) if not e["outside"]]
        return np.array(pts, float) if pts else None

    # trái/phải theo ảnh: skeleton 'trái' phải nằm bên trái skeleton 'phải' cùng loại
    def pair_key(cfg: dict) -> str:
        return re.sub(r"(trai|phai|left|right)$", "", norm(cfg.get("entry") or cfg["name"]).replace(" ", ""))

    rights = {pair_key(c): c for c in faces if c.get("face_side") == "right"}
    for left in (c for c in faces if c.get("face_side") == "left"):
        right = rights.get(pair_key(left))
        if right is None:
            continue
        a, b = shown_points(left), shown_points(right)
        if a is not None and b is not None and a[:, 0].mean() > b[:, 0].mean():
            findings.append(_finding("error", "LEFT_RIGHT",
                                     f"'{left['name']}' đang nằm bên phải '{right['name']}'. Trái/phải tính theo khuôn mặt nhìn thấy trên ảnh.",
                                     by_label[left["label_id"]][0]))
    for inner in faces:
        outer = next((c for c in faces if inner.get("inside_of") and norm(c.get("entry") or c["name"]) == norm(inner["inside_of"])), None)
        if outer is None:
            continue
        a, b = shown_points(inner), shown_points(outer)
        if a is None or b is None or len(b) < 3:
            continue
        poly = b.astype(np.float32).reshape(-1, 1, 2)
        tol = float(qa.get("inside_tolerance_px", 1.5))
        out = sum(1 for x, y in a if cv2.pointPolygonTest(poly, (float(x), float(y)), True) < -tol)
        if out:
            findings.append(_finding("error", "TOPOLOGY",
                                     f"{out} điểm của '{inner['name']}' nằm ngoài '{outer['name']}'. Guideline: môi trong luôn nằm hoàn toàn bên trong môi ngoài.",
                                     by_label[inner["label_id"]][0]))
