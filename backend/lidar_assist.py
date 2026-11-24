"""Job cuboid 3D (0.9.6): ảnh camera đi kèm point cloud và tự chỉnh theo cuboid bạn đã đánh.

Ảnh camera (context image của CVAT 3D)
  CVAT lưu ảnh camera cạnh mỗi frame point cloud, thường không kèm thông số hiệu chỉnh. Tool:
  - có thông số (bạn nhập, hoặc profile ghi): dùng góc hướng và góc nhìn của từng camera;
  - không có: đoán theo tên camera (CAM_FRONT, CAM_BACK_LEFT... kiểu nuScenes: trước 0°, trước-trái 55°, sau 180°...),
    rồi tự dò trục của point cloud (x hướng trước hay y hướng trước) bằng cách thử 4 góc lệch, chọn góc khớp nhiều
    vật nhất giữa LiDAR và SAM3 trên ảnh.
  Mỗi cuboid được chiếu ra cột ảnh của camera nhìn thấy nó; SAM3 tìm class đó (và các vật hay bị nhầm: thùng rác,
  nhà vệ sinh di động...) quanh cột đó. Thấy đúng class: xác nhận. Chỉ thấy vật hay bị nhầm: bỏ tick, gắn cờ.

Tự chỉnh
  Bạn đã đánh cuboid ở vài frame: tool chạy lại trên chính các frame đó, so với cuboid của bạn, rồi đề nghị chỉnh số
  điểm tối thiểu, phạm vi, độ lệch đáy, bảng kích thước. Bạn bấm Áp dụng mới đổi.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from typing import Any, Optional

import numpy as np

from . import geometry as G
from . import lidar as L

# Hướng (độ, ngược chiều kim đồng hồ từ hướng trước của xe) và góc nhìn ngang của bộ 6 camera kiểu nuScenes
CAMERA_YAW = {"front": 0.0, "front left": 55.0, "front right": -55.0, "back": 180.0, "back left": 110.0,
              "back right": -110.0, "left": 90.0, "right": -90.0, "rear": 180.0, "rear left": 110.0, "rear right": -110.0}
CAMERA_FOV = {"back": 110.0, "rear": 110.0}
DEFAULT_FOV = 70.0
CLASS_PROMPTS = {"car": "car", "truck": "truck", "trailer": "trailer", "bus": "bus", "construction vehicle": "excavator",
                 "pedestrian": "person", "two wheels": "motorcycle", "barrier": "traffic barrier"}
LOOKALIKES = ["dumpster", "portable toilet", "trash bin", "shipping container", "kiosk"]
OFFSETS = (0.0, 90.0, 180.0, -90.0)


def camera_key(name: str) -> Optional[str]:
    """'CAM_BACK_LEFT.jpg' -> 'back left'; không nhận ra -> None."""
    low = re.sub(r"\.[a-z0-9]{2,4}$", "", str(name).lower())
    low = re.sub(r"[^a-z]+", " ", low).replace("cam ", " ").replace("camera", " ")
    words = [w for w in low.split() if w in ("front", "back", "rear", "left", "right")]
    if not words:
        return None
    key = " ".join(w for w in ("front", "back", "rear") if w in words) or ""
    side = next((w for w in ("left", "right") if w in words), "")
    key = (key + " " + side).strip()
    return key if key in CAMERA_YAW else None


def read_context(data: bytes) -> list[tuple[str, np.ndarray]]:
    """Ảnh camera của một frame: file zip của CVAT (nhiều ảnh) hoặc một ảnh. -> [(tên, ảnh RGB)]."""
    from PIL import Image

    out = []
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in sorted(z.namelist()):
                if n.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
                    out.append((n.rsplit("/", 1)[-1], np.asarray(Image.open(io.BytesIO(z.read(n))).convert("RGB"))))
    else:
        out.append(("camera", np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))))
    return out


def cameras_of(names: list[str], calib: Optional[dict] = None) -> list[dict]:
    """Thông số từng camera: từ hiệu chỉnh nếu có ({tên: {yaw_deg, fov_deg}}), không thì đoán theo tên."""
    calib = calib or {}
    out = []
    for n in names:
        c = calib.get(n) or calib.get(camera_key(n) or "")
        if c:
            out.append({"name": n, "yaw": float(c.get("yaw_deg", 0)), "fov": float(c.get("fov_deg", DEFAULT_FOV)), "source": "calibration"})
            continue
        key = camera_key(n)
        if key:
            base = key.split()[0]
            out.append({"name": n, "yaw": CAMERA_YAW[key], "fov": CAMERA_FOV.get(base, DEFAULT_FOV), "source": "name"})
    if not out and len(names) == 1:
        out.append({"name": names[0], "yaw": 0.0, "fov": 90.0, "source": "guess"})
    return out


def project(center: list[float], size: list[float], cams: list[dict], offset: float) -> Optional[dict]:
    """Cuboid -> camera nhìn thấy nó, cột giữa (0..1) và nửa bề rộng (0..1) trên ảnh."""
    az = math.degrees(math.atan2(center[1], center[0])) + offset
    dist = max(math.hypot(center[0], center[1]), 0.5)
    half = math.degrees(math.atan2(max(size[0], size[1]) / 2, dist))
    best = None
    for cam in cams:
        rel = (az - cam["yaw"] + 180) % 360 - 180
        lim = cam["fov"] / 2
        if abs(rel) > lim * 0.97:
            continue
        t = math.tan(math.radians(lim))
        u = 0.5 - math.tan(math.radians(rel)) / (2 * t)
        w = abs(math.tan(math.radians(min(rel + half, 89))) - math.tan(math.radians(max(rel - half, -89)))) / (4 * t)
        if best is None or abs(rel) < best["rel"]:
            best = {"camera": cam["name"], "u": round(u, 3), "half": round(max(w, 0.01), 3), "rel": abs(rel)}
    return best


def _masks_x(engine, state, prompt: str, width: int, height: int, thr: float = 0.35) -> list[tuple[float, float, float]]:
    """Khoảng cột (0..1) của các vật SAM3 tìm thấy theo prompt, kèm điểm."""
    out = []
    try:
        found = engine.segment_text(state, prompt, thr)
    except Exception:  # noqa: BLE001
        return out
    for inst in found:
        b = G.mask_bbox(inst.mask.astype(bool))
        if b and (b[3] - b[1]) > 0.02 * height:
            out.append((b[0] / width, b[2] / width, float(inst.score)))
    return out


def check(engine, images: list[tuple[str, np.ndarray]], objects: list[dict], prompts: dict[int, str],
          calib: Optional[dict] = None, offset: Optional[float] = None, lookalikes: Optional[list[str]] = None) -> dict:
    """Đối chiếu cuboid với ảnh camera. objects: [{id, center, size, label_id, cls}].
    Trả {offset, offset_source, cameras, results: {id: {camera, u, verdict, seen}}}."""
    cams = cameras_of([n for n, _ in images], calib)
    if not cams:
        return {"offset": offset, "offset_source": "none", "cameras": [], "results": {},
                "note": "Không nhận ra tên camera (CAM_FRONT, CAM_BACK...). Nhập hướng camera ở tab Label."}
    by_name = dict(images)
    cache: dict[tuple[str, str], list] = {}
    states: dict[str, Any] = {}

    def found(cam_name: str, prompt: str):
        if (cam_name, prompt) not in cache:
            img = by_name[cam_name]
            if cam_name not in states:
                states[cam_name] = engine.encode(img)
            cache[(cam_name, prompt)] = _masks_x(engine, states[cam_name], prompt, img.shape[1], img.shape[0])
        return cache[(cam_name, prompt)]

    def verdicts(off: float) -> dict:
        res = {}
        for o in objects:
            p = project(o["center"], o["size"], cams, off)
            if p is None:
                res[o["id"]] = {"verdict": "unseen"}
                continue
            prompt = prompts.get(o["label_id"]) or CLASS_PROMPTS.get(o.get("cls", ""), o.get("cls", "object"))
            lo, hi = p["u"] - p["half"] - 0.03, p["u"] + p["half"] + 0.03
            hit = [s for a, b, s in found(p["camera"], prompt) if b >= lo and a <= hi]
            entry = {"camera": p["camera"], "u": p["u"], "prompt": prompt}
            if hit:
                entry.update(verdict="confirmed", score=round(max(hit), 3))
            else:
                other = None
                for word in lookalikes if lookalikes is not None else LOOKALIKES:
                    if any(b >= lo and a <= hi for a, b, _ in found(p["camera"], word)):
                        other = word
                        break
                entry.update(verdict="contradicted" if other else "unknown", seen=other)
            res[o["id"]] = entry
        return res

    source = "panel" if offset is not None else ("calibration" if any(c["source"] == "calibration" for c in cams) else "")
    if offset is None and source == "calibration":
        offset = 0.0
    if offset is None:
        # tự dò trục: thử 4 góc, chọn góc có nhiều vật được xác nhận nhất (cần ít nhất 2 và hơn hẳn góc khác)
        tries = sorted(((sum(v["verdict"] == "confirmed" for v in verdicts(off).values()), off) for off in OFFSETS), reverse=True)
        best_n, best_off = tries[0]
        if best_n >= 2 and best_n > tries[1][0]:
            offset, source = best_off, "estimated"
        else:
            offset, source = 0.0, "default"
    results = verdicts(offset)
    return {"offset": offset, "offset_source": source, "cameras": [c["name"] for c in cams], "results": results}


def apply_camera(objects: list[dict], check_result: dict) -> int:
    """Áp kết quả đối chiếu camera vào đề xuất: xác nhận thì tăng độ tin, vật hay bị nhầm thì bỏ tick + cờ."""
    changed = 0
    for o in objects:
        r = (check_result.get("results") or {}).get(o.get("id"))
        if not r:
            continue
        o["camera"] = r
        if r["verdict"] == "confirmed":
            o["confidence"] = round(min(1.0, float(o.get("confidence", o.get("score", 0.5))) + 0.2), 3)
        elif r["verdict"] == "contradicted":
            o["checked"] = False
            o["confidence"] = round(float(o.get("confidence", 0.5)) * 0.4, 3)
            o.setdefault("flags", []).append({"code": "UNCERTAIN_CLASS",
                                              "message": f"Ảnh {r['camera']}: chỗ này giống '{r['seen']}' hơn '{r['prompt']}'. Tool bỏ tick."})
            changed += 1
    return changed


# ------------------------------------------------------------------ tự chỉnh ---
def truth_from_cvat(points: list[float]) -> dict:
    return {"center": [float(v) for v in points[0:3]], "yaw": float(points[5]), "size": [float(v) for v in points[6:9]]}


def evaluate(frames: list[tuple[int, np.ndarray]], truth: list[dict], labels: dict[str, int], table: dict, rule: dict,
             match_m: float = 1.0) -> dict:
    """Chạy lại trên các frame bạn đã đánh, so với cuboid của bạn. truth: [{frame, label_id, center, size, yaw}]."""
    by_label = {v: k for k, v in labels.items()}
    per: dict[str, dict] = {}
    miss_points: dict[str, list[float]] = {}
    miss_range: dict[str, list[float]] = {}
    gaps: list[float] = []
    out_of_table: dict[str, list[list[float]]] = {}

    def row(key):
        return per.setdefault(key, {"class": key, "truth": 0, "found": 0, "wrong_class": 0, "extra": 0, "missed": {}})

    for f, pts in frames:
        det = L.detect(pts, labels, table, rule, debug=True)
        mine = [t for t in truth if int(t["frame"]) == f and t["label_id"] in by_label]
        used = set()
        for t in mine:
            key = by_label[t["label_id"]]
            r = row(key)
            r["truth"] += 1
            if not L.classify(_sorted_size(t["size"]), table, {key: t["label_id"]})[0]:
                out_of_table.setdefault(key, []).append([round(v, 2) for v in t["size"]])
            best = min(((_to_box(o["center"], t), i) for i, o in enumerate(det["objects"]) if i not in used),
                       default=(1e9, None))
            if best[0] <= match_m:
                used.add(best[1])
                if det["objects"][best[1]]["label_id"] == t["label_id"]:
                    r["found"] += 1
                else:
                    r["wrong_class"] += 1
                continue
            near = min(((_to_box(x["center"], t), x) for x in det["rejected"]), default=(1e9, None), key=lambda a: a[0])
            why = near[1]["reason"] if near[0] <= match_m else "no_cluster"
            r["missed"][why] = r["missed"].get(why, 0) + 1
            dist = math.hypot(*t["center"][:2])
            if why == "few_points":
                need = L.min_points_at(dist, key, rule)
                miss_points.setdefault(key, []).append(near[1]["points"] / max(need, 1))
            elif why == "range":
                miss_range.setdefault(key, []).append(dist)
            elif why == "floating":
                gaps.append(float(near[1].get("gap", 0.6)))
        for i, o in enumerate(det["objects"]):
            if i not in used:
                row(o["cls"])["extra"] += 1
    suggest: dict[str, Any] = {}
    scale = dict(rule.get("min_points_scale") or {})
    for key, ratios in miss_points.items():
        scale[key] = round(max(0.2, min(ratios) * 0.9 * float(scale.get(key, 1.0))), 2)
    if miss_points:
        suggest["min_points_scale"] = scale
    if miss_range:
        suggest["ranges"] = {k: math.ceil(max(v) + 2) for k, v in miss_range.items()}
    if gaps:
        suggest["max_base_gap_m"] = round(min(1.2, max(gaps) + 0.1), 2)
    sizes = {}
    for key, boxes in out_of_table.items():
        arr = np.array([_sorted_size(b) for b in boxes])
        rows = [list(map(list, r)) for r in table.get(key) or []]
        lo, hi = arr.min(0) * 0.95, arr.max(0) * 1.05
        sizes[key] = rows + [[[round(float(lo[i]), 2), round(float(hi[i]), 2)] for i in range(3)]]
    if sizes:
        suggest["sizes"] = sizes
    rows = sorted(per.values(), key=lambda r: -r["truth"])
    total = sum(r["truth"] for r in rows)
    found = sum(r["found"] for r in rows)
    extra = sum(r["extra"] for r in rows)
    return {"rows": rows, "recall": round(found / total, 3) if total else None,
            "precision": round(found / (found + extra), 3) if found + extra else None,
            "suggest": suggest, "frames": len(frames), "truth": total, "reasons": L.REASONS}


def _to_box(point: list[float], t: dict) -> float:
    """Khoảng cách (m, nhìn từ trên) từ một điểm tới hình chữ nhật đáy cuboid của bạn: cụm chỉ thấy một mặt vật vẫn
    khớp với cuboid đủ kích thước bạn vẽ."""
    dx, dy = point[0] - t["center"][0], point[1] - t["center"][1]
    c, s = math.cos(-t.get("yaw", 0.0)), math.sin(-t.get("yaw", 0.0))
    lx, ly = abs(dx * c - dy * s), abs(dx * s + dy * c)
    return math.hypot(max(lx - t["size"][0] / 2, 0.0), max(ly - t["size"][1] / 2, 0.0))


def _sorted_size(size: list[float]) -> list[float]:
    l, w, h = size
    return [max(l, w), min(l, w), h]
