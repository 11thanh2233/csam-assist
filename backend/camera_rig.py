"""Hiệu chỉnh camera cho job 3D — 3 tầng tự động (0.9.7, spec 3D v2 mục 3.1).

Chuyển giữa toạ độ mét (x, y, z) và cột ảnh (u) cần biết mỗi camera nhìn về hướng nào và rộng bao nhiêu. CVAT 3D
thường chỉ trả ảnh camera, không kèm ma trận hiệu chỉnh. Ba tầng, dùng tầng nào có dữ liệu thì dùng:

  Tầng 1 — Từ CVAT:  nếu metadata của job/task có `intrinsics`/`extrinsics` (hoặc panel đã lưu) thì dùng thẳng.
  Tầng 2 — Preset:   khớp tên camera với bộ cảm biến chuẩn (nuScenes 6 camera, KITTI, Waymo) — không cần dữ liệu.
  Tầng 3 — Box mẫu:  người dùng vẽ tay 2-3 cuboid rõ nét ở frame đầu; tool dò hướng/tiêu cự cho từng camera sao cho
                     khung chiếu khớp vật nhất. Gọi từ nút "Căn chỉnh từ box mẫu" ở panel.

Kết quả tầng 3 được lưu vào cấu hình job (`lidar3d[task].cameras`) nên các lần sau dùng lại, không phải căn lại.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Optional

from .lidar_assist import CAMERA_FOV, CAMERA_YAW, DEFAULT_FOV, camera_key

# --------------------------------------------------------------- preset ---
# (góc hướng°, góc nhìn ngang°) — cùng quy ước với lidar_assist: 0° là hướng trước của xe, dương là ngược chiều
# kim đồng hồ (bên trái).
PRESETS: dict[str, dict[str, tuple[float, float]]] = {
    "nuscenes": {"front": (0.0, 70.0), "front left": (55.0, 70.0), "front right": (-55.0, 70.0),
                 "back": (180.0, 110.0), "back left": (110.0, 70.0), "back right": (-110.0, 70.0)},
    "kitti": {"front": (0.0, 90.0), "front left": (45.0, 90.0), "front right": (-45.0, 90.0), "back": (180.0, 90.0)},
    "waymo": {"front": (0.0, 50.0), "front left": (30.0, 50.0), "front right": (-30.0, 50.0),
              "left": (90.0, 75.0), "right": (-90.0, 75.0), "back": (180.0, 50.0),
              "back left": (140.0, 60.0), "back right": (-140.0, 60.0)},
}
PRESET_NAMES = {"nuscenes": "nuScenes (6 camera)", "kitti": "KITTI (4 camera)", "waymo": "Waymo (8 camera)"}
# tên khác của cùng một hướng, để khớp preset chặt hơn
SIDE_ALIAS = {"left": "left", "right": "right", "trai": "left", "phai": "right"}
TIER_NAMES = {"cvat": "theo dữ liệu CVAT", "calibration": "theo hiệu chỉnh của bạn", "preset": "theo bộ cảm biến chuẩn",
              "name": "đoán theo tên camera", "guess": "đoán (một camera)", "fitted": "căn từ box mẫu", "none": "chưa có"}


def preset_for(names: list[str]) -> Optional[str]:
    """Tên preset khớp nhất với danh sách camera (khớp hết tên, không thì khớp số lượng + hướng)."""
    keys = [camera_key(n) for n in names]
    if not keys or any(k is None for k in keys):
        return None
    for pid, table in PRESETS.items():
        if set(keys) == set(table):
            return pid
    # tên khác nhưng cùng số camera và cùng tập hướng: vẫn coi là bộ chuẩn đó
    for pid, table in PRESETS.items():
        if len(table) == len(keys) and set(table) == set(keys):
            return pid
    return None


def from_meta(meta: Optional[dict]) -> dict[str, dict]:
    """Tầng 1: đọc hiệu chỉnh có sẵn. Nhận các dạng hay gặp, chuẩn hoá về {tên: {yaw_deg, fov_deg}}.

    Chấp nhận:
      {"cameras": {"CAM_FRONT": {"yaw_deg": 0, "fov_deg": 70}}}
      {"camera_calibration": [{"name": "CAM_FRONT", "yaw_deg": 0, "fov_deg": 70}]}
      {"CAM_FRONT": {"intrinsics": {"fx": 1000}, "width": 1000}}      (chỉ có tiêu cự: suy ra góc nhìn)
    Giá trị nào không suy được thì để None; build_rig sẽ bù bằng preset / tên camera.
    """
    out: dict[str, dict] = {}
    if not isinstance(meta, dict):
        return out
    raw = meta.get("cameras") or meta.get("camera_calibration") or meta
    items: list[tuple[str, dict]] = []
    if isinstance(raw, dict):
        items = [(str(k), v) for k, v in raw.items() if isinstance(v, dict)]
    elif isinstance(raw, list):
        items = [(str(v.get("name") or v.get("camera") or ""), v) for v in raw if isinstance(v, dict)]
    for name, cfg in items:
        yaw, fov = _yaw_fov(cfg)
        if not name or (yaw is None and fov is None):
            continue
        out[name] = {"yaw_deg": yaw, "fov_deg": fov}
    return out


def _yaw_fov(cfg: dict) -> tuple[Optional[float], Optional[float]]:
    """Góc hướng / góc nhìn từ nhiều kiểu tên khoá; có extrinsics thì suy hướng từ đó."""
    yaw = _num(cfg.get("yaw_deg", cfg.get("yaw", cfg.get("heading_deg"))))
    fov = _num(cfg.get("fov_deg", cfg.get("fov", cfg.get("hfov_deg"))))
    if yaw is None:
        ext = cfg.get("extrinsics") or cfg.get("extrinsic") or cfg.get("rotation")
        if isinstance(ext, (list, tuple)) and len(ext) >= 9:
            # ma trận xoay 3x3 hàng ưu tiên (row-major): trục nhìn là cột thứ ba
            forward = (float(ext[2]), float(ext[5]))
            if any(forward):
                yaw = math.degrees(math.atan2(forward[1], forward[0]))
    if fov is None:
        intr = cfg.get("intrinsics") or cfg.get("intrinsic")
        width = _num(cfg.get("width") or cfg.get("image_width"))
        if isinstance(intr, dict):
            fx = _num(intr.get("fx"))
            width = _num(intr.get("width") or intr.get("w")) or width
            if fx and width:
                fov = math.degrees(2 * math.atan(width / (2 * fx)))
        elif isinstance(intr, (list, tuple)) and len(intr) >= 3:
            fx = _num(intr[0])
            if fx and width:
                fov = math.degrees(2 * math.atan(width / (2 * fx)))
    return yaw, fov


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_rig(names: list[str], calib: Optional[dict] = None, meta: Optional[dict] = None) -> dict:
    """Tầng 1 (hiệu chỉnh có sẵn) -> tầng 2 (preset bộ cảm biến chuẩn) -> đoán theo tên.

    Tầng 1 cho camera nào thiếu góc/góc nhìn thì bù từ preset (hoặc theo tên) cho đủ trước khi dùng.
    Trả {"cameras": [{name, yaw, fov, source}], "source": tầng chung, "preset": tên preset, "notes": [...]}."""
    notes: list[str] = []
    cams: list[dict] = []
    stored = from_meta(calib) if calib else {}
    if stored:
        for n in names:
            c = stored.get(n) or stored.get(n.rsplit("/", 1)[-1])
            if c is None:
                cams = []
                break
            yaw = c.get("yaw_deg")
            fov = c.get("fov_deg")
            key = camera_key(n) or ""
            if yaw is None:
                yaw = CAMERA_YAW.get(key, 0.0)
            if fov is None:
                base = key.split()[0] if key else ""
                fov = CAMERA_FOV.get(base, DEFAULT_FOV)
            cams.append({"name": n, "yaw": float(yaw), "fov": float(fov), "source": "calibration"})
        if len(cams) == len(names):
            return {"cameras": cams, "source": "calibration", "preset": None, "notes": notes}
        cams = []                                   # thiếu camera nào thì bỏ, làm lại từ tầng 2

    given = from_meta(meta) if meta else {}
    if given:
        for n in names:
            c = given.get(n)
            if c is None:
                cams = []
                break
            key = camera_key(n) or ""
            yaw = c.get("yaw_deg")
            if yaw is None:
                yaw = CAMERA_YAW.get(key, 0.0)
            fov = c.get("fov_deg")
            if fov is None:
                base = key.split()[0] if key else ""
                fov = CAMERA_FOV.get(base, DEFAULT_FOV)
            cams.append({"name": n, "yaw": float(yaw), "fov": float(fov), "source": "cvat"})
        if len(cams) == len(names):
            return {"cameras": cams, "source": "cvat", "preset": None, "notes": notes}
        cams = []

    pid = preset_for(names)
    if pid:
        table = PRESETS[pid]
        for n in names:
            key = camera_key(n)
            yaw, fov = table.get(key, (None, None))
            if yaw is None:
                continue
            cams.append({"name": n, "yaw": yaw, "fov": fov, "source": "preset"})
        if len(cams) == len(names):
            return {"cameras": cams, "source": "preset", "preset": pid, "notes": notes}

    # cuối: đoán theo tên như 0.9.6
    for n in names:
        key = camera_key(n)
        if key:
            base = key.split()[0]
            cams.append({"name": n, "yaw": CAMERA_YAW[key], "fov": CAMERA_FOV.get(base, DEFAULT_FOV), "source": "name"})
    if not cams and len(names) == 1:
        cams.append({"name": names[0], "yaw": 0.0, "fov": 90.0, "source": "guess"})
        notes.append("Chỉ có một camera và không rõ tên: coi như nhìn thẳng trước (0°, 90°). "
                     "Nếu khung chiếu lệch, dùng 'Căn chỉnh từ box mẫu'.")
    elif len(cams) < len(names):
        notes.append(f"Nhận ra {len(cams)}/{len(names)} camera theo tên. Camera còn lại bị bỏ qua khi chiếu.")
    return {"cameras": cams, "source": "name" if cams else "none", "preset": None, "notes": notes}


# -------------------------------------------------------------- chiếu ---
def project(center: list[float], size: list[float], cameras: list[dict], offset: float = 0.0) -> Optional[dict]:
    """Cuboid -> camera nhìn thấy nó: cột giữa (0..1), nửa bề rộng (0..1), góc lệch tâm (độ)."""
    az = math.degrees(math.atan2(center[1], center[0])) + offset
    dist = max(math.hypot(center[0], center[1]), 0.5)
    half = math.degrees(math.atan2(max(size[0], size[1]) / 2, dist))
    best = None
    for cam in cameras:
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


def to_panel(rig: dict) -> dict:
    """Dạng lưu vào cấu hình job (panel gửi lại ở `lidar.cameras`)."""
    return {c["name"]: {"yaw_deg": round(float(c["yaw"]), 2), "fov_deg": round(float(c["fov"]), 2)} for c in rig["cameras"]}


# ------------------------------------------------- tầng 3: box mẫu ---
def calibrate(images: list[tuple[str, Any]], objects: list[dict], search: Callable[[str, str], list[tuple[float, float, float]]],
              base: Optional[list[dict]] = None, offsets: Optional[list[float]] = None,
              fov_scales: Optional[list[float]] = None, min_hits: int = 2) -> dict:
    """Căn chỉnh từ box mẫu: dò (lệch trục, hệ số góc nhìn) cho khớp nhiều vật nhất.

    `search(cam_name, prompt)` trả các khoảng cột (u_lo, u_hi, score) mà model 2D tìm thấy cho prompt đó. Nhận cả
    bộ dò ONNX của `lidar_vision` (không cần SAM3, không đụng VRAM của SAM3) lẫn SAM3 đã nạp sẵn.
    `base`: rig hiện tại (tầng 2). Trả {"cameras": [...], "offset": .., "fov_scale": .., "hits": n, "source": "fitted"}.
    """
    offsets = offsets if offsets is not None else [o * 5.0 for o in range(-36, 37)]        # -180..180, bước 5°
    fov_scales = fov_scales if fov_scales is not None else [0.8, 0.9, 1.0, 1.1, 1.2]
    cams = [dict(c) for c in (base or [])]
    if not cams or not objects:
        return {"cameras": cams, "offset": 0.0, "fov_scale": 1.0, "hits": 0, "source": "none",
                "note": "Cần ít nhất một camera và một cuboid mẫu để căn chỉnh."}
    best = (0, 0.0, 1.0)
    for scale in fov_scales:
        trial = [dict(c, fov=min(179.0, c["fov"] * scale)) for c in cams]
        for off in offsets:
            hits = 0
            for o in objects:
                p = project(o["center"], o["size"], trial, off)
                if p is None:
                    continue
                lo, hi = p["u"] - p["half"] - 0.03, p["u"] + p["half"] + 0.03
                prompt = o.get("prompt") or o.get("cls") or "object"
                if any(b >= lo and a <= hi for a, b, _ in search(p["camera"], str(prompt))):
                    hits += 1
            if hits > best[0]:
                best = (hits, off, scale)
    hits, off, scale = best
    if hits < min_hits:
        return {"cameras": cams, "offset": 0.0, "fov_scale": 1.0, "hits": hits, "source": "none",
                "note": f"Chỉ khớp {hits} vật: chưa đủ để căn chỉnh (cần ≥ {min_hits}). "
                        "Vẽ thêm 2-3 cuboid rõ nét ở frame đầu, hoặc chọn frame có nhiều xe."}
    # góc nhìn riêng từng camera: thử nới/thu 10% cho từng camera, giữ nếu khớp nhiều hơn
    fitted = [dict(c, fov=min(179.0, c["fov"] * scale)) for c in cams]
    for c in fitted:
        best_cam = (hits, c["fov"])
        for k in (0.9, 1.1):
            trial = [dict(x, fov=min(179.0, x["fov"] * k)) if x["name"] == c["name"] else x for x in fitted]
            n = sum(1 for o in objects
                    if (p := project(o["center"], o["size"], trial, off)) is not None
                    and any(b >= p["u"] - p["half"] - 0.03 and a <= p["u"] + p["half"] + 0.03
                            for a, b, _ in search(p["camera"], str(o.get("prompt") or o.get("cls") or "object"))))
            if n > best_cam[0]:
                best_cam = (n, min(179.0, c["fov"] * k))
        c["fov"] = best_cam[1]
    for c in fitted:
        c["source"] = "fitted"
    return {"cameras": fitted, "offset": round(off, 1), "fov_scale": scale, "hits": hits, "source": "fitted"}


def sam3_search(engine, images: list[tuple[str, Any]], prompts: dict[str, str]) -> Callable[[str, str], list[tuple[float, float, float]]]:
    """Bộ dò dùng SAM3 đã nạp sẵn (dùng khi bạn đang ở job 2D hoặc SAM3 còn trong VRAM)."""
    by_name = dict(images)
    cache: dict[tuple[str, str], list] = {}
    states: dict[str, Any] = {}

    def search(cam: str, prompt: str):
        key = (cam, prompt)
        if key in cache:
            return cache[key]
        img = by_name.get(cam)
        if img is None:
            return []
        from . import geometry as G

        if cam not in states:
            states[cam] = engine.encode(img)
        out = []
        try:
            for inst in engine.segment_text(states[cam], prompt, 0.35):
                b = G.mask_bbox(inst.mask.astype(bool))
                if b and (b[3] - b[1]) > 0.02 * img.shape[0]:
                    out.append((b[0] / img.shape[1], b[2] / img.shape[1], float(inst.score)))
        except Exception:  # noqa: BLE001 - prompt dài / model lỗi: bỏ, không làm sập căn chỉnh
            out = []
        cache[key] = out
        return out

    return search
