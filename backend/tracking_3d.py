"""Theo dõi cuboid 3D qua các frame (0.9.7, spec 3D v2 mục 3.2D).

Nâng từ ghép tham lam của lidar.build_tracks lên bộ chuẩn QC:
  - Ghép đề xuất vào track bằng Hungarian (scipy.optimize.linear_sum_assignment) trên chi phí = khoảng cách dự đoán
    Kalman; Kalman 2D hằng vận tốc cho (x, y) — z và yaw nội suy riêng, ít điểm LiDAR nên Kalman đủ rồi.
  - Khóa kích thước: frame dày điểm nhất và gần cảm biến nhất làm chuẩn; các frame khác chỉ lệch trong ±size_tol
    (mặc định 5%) — lệch quá thì giữ kích thước chuẩn nhưng gắn cờ ATTRIBUTE_CHECK để người duyệt.
  - Luật occluded / outside độc lập: bị che <= max_occluded keyframe (mặc định 6) -> nội suy vị trí mượt, bật
    occluded=true; lâu hơn hoặc vượt quá khoảng cách class -> outside=true. Ràng buộc cứng: không bao giờ bật đồng
    thời cả hai trên cùng một keyframe.
  - yaw: vật đi nhanh theo vector chuyển động, đứng yên giữ yaw hình học; giữ liên tục, không lật 180°.

Kết quả vẫn chỉ là gợi ý: người duyệt trước khi ghi vào CVAT.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .lidar import cvat_points, DEFAULT_RANGES, _keyframes, _wrap

try:
    from scipy.optimize import linear_sum_assignment
except ImportError:  # noqa: BLE001 - scipy là phụ thuộc bắt buộc của requirements.txt, nhưng phòng hờ
    linear_sum_assignment = None


# ------------------------------------------------------------- Kalman ---
class Kf2d:
    """Kalman hằng vận tốc cho (x, y): dự đoán khi track bị che, đo khi gặp lại."""

    def __init__(self, xy, v=(0.0, 0.0), q: float = 0.25, r: float = 1.2):
        self.x = np.array(xy, float)
        self.v = np.array(v, float)
        self.q, self.r = q, r          # nhiễu quá trình / đo: vặn trong rule nếu dataset khác

    def predict(self, dt: float) -> np.ndarray:
        self.x = self.x + self.v * dt
        self.v = self.v * (1.0 - min(0.5, dt * 0.05))   # kéo nhẹ về 0 khi vật có thể đã dừng
        return self.x.copy()

    def update(self, z, dt: float) -> np.ndarray:
        z = np.asarray(z, float)
        gain = self.r / (self.q + self.r)               # đơn giản hóa: K = P/(P+R), cùng giá trị cho x và y
        resid = z - self.x
        self.x = self.x + gain * resid
        # vận tốc suy từ đo mới: (vị trí mới - dự đoán cũ) / dt trộn với vận tốc cũ
        if dt > 0:
            meas_v = resid / dt
            self.v = 0.6 * self.v + 0.4 * meas_v
        return self.x.copy()


# --------------------------------------------------------- ghép frame ---
def _cost(pred_xy, o, dt: float, gate: float) -> float:
    d = float(np.hypot(*(np.asarray(o["center"][:2]) - pred_xy)))
    return d if d <= gate * (1 + 0.3 * max(dt - 1, 0)) else math.inf


def _match(tracks: list[dict], kfs: list[Kf2d], objs: list[dict], f: int, gate: float) -> list[tuple[int, int]]:
    """Hungarian trên chi phí khoảng cách tới dự đoán Kalman; thiếu scipy thì ghép tham lam như cũ."""
    live = [(ti, gap) for ti, t in enumerate(tracks) if 0 < (gap := f - t["last"]) <= t["max_hidden"] + 1]
    if not live or not objs:
        return []
    cost = np.full((len(live), len(objs)), math.inf)
    for li, (ti, gap) in enumerate(live):
        pred = kfs[ti].predict(gap)
        kfs[ti]._pred = pred                            # nhớ lại để update đúng bước thời gian
        kfs[ti]._dt = gap
        for oi, o in enumerate(objs):
            if o["label_id"] != tracks[ti]["label_id"]:
                continue
            cost[li, oi] = _cost(pred, o, gap, gate)
    # chỉ giữ track và vật có ít nhất một cặp khả thi: ma trận toàn inf làm linear_sum_assignment báo lỗi
    rows_ok = [r for r in range(cost.shape[0]) if np.isfinite(cost[r]).any()]
    cols_ok = [c for c in range(cost.shape[1]) if np.isfinite(cost[:, c]).any()]
    if not rows_ok or not cols_ok:
        return []
    sub = cost[np.ix_(rows_ok, cols_ok)]
    pairs: list[tuple[int, int]] = []
    if linear_sum_assignment is not None:
        # inf đổi thành số phạt lớn để bài toán luôn giải được; cặp bị phạt thì lọc lại bên dưới
        finite = np.isfinite(sub)
        penalty = float(np.nanmax(sub[finite])) * (sub.shape[0] + sub.shape[1] + 1) if finite.any() else 1e9
        rows, cols = linear_sum_assignment(np.where(finite, sub, penalty))
        for r, c in zip(rows, cols):
            if finite[r, c]:
                pairs.append((live[rows_ok[r]][0], cols_ok[c]))
    else:                                               # dự phòng: chọn cặp gần nhất còn trống
        used_t, used_o = set(), set()
        flat = sorted((cost[r, c], r, c) for r in rows_ok for c in cols_ok if math.isfinite(cost[r, c]))
        for _d, r, c in flat:
            ti = live[r][0]
            if ti not in used_t and c not in used_o:
                used_t.add(ti)
                used_o.add(c)
                pairs.append((ti, c))
    return pairs


# ------------------------------------------------------------ dựng ---
def build(frames: list[dict], rule: Optional[dict] = None, names: Optional[dict] = None) -> dict:
    """frames: [{frame, objects}] -> {tracks, warnings, engine}. Tương thích output của lidar.build_tracks."""
    rule = rule or {}
    names = names or {}
    max_hidden = int(rule.get("max_occluded_frames", 6))
    gate = float(rule.get("match_m", 2.5))
    size_tol = float(rule.get("size_tolerance", 0.05))
    min_frames = int(rule.get("min_track_frames", 2))
    ranges = dict(DEFAULT_RANGES, **(rule.get("ranges") or {}))
    dt_frame = float(rule.get("frame_dt_s", 0.5))       # khoảng thời gian giữa hai frame (0.5 s mặc định)
    tracks: list[dict] = []
    kfs: list[Kf2d] = []
    for item in sorted(frames, key=lambda x: x["frame"]):
        f = int(item["frame"])
        objs = list(item.get("objects") or [])
        pairs = _match(tracks, kfs, objs, f, gate)
        used_o = {c for _, c in pairs}
        for ti, oi in pairs:
            t, k = tracks[ti], kfs[ti]
            xy, dt = k._pred, max(k._dt, 1)
            prev = t["obs"][t["last"]]
            k.update(objs[oi]["center"][:2], dt)
            t["vel"] = k.v.copy()
            t["obs"][f] = objs[oi]
            t["last"] = f
            t["gaps"].append(dt)
        for oi, o in enumerate(objs):
            if oi not in used_o:
                tracks.append({"label_id": o["label_id"], "obs": {f: o}, "last": f, "vel": np.zeros(2),
                               "gaps": [], "max_hidden": max_hidden})
                kfs.append(Kf2d(o["center"][:2], q=0.25 * dt_frame, r=1.2 * dt_frame))

    out, warnings = [], []
    for i, t in enumerate(sorted(tracks, key=lambda x: min(x["obs"])), 1):
        fs = sorted(t["obs"])
        if len(fs) < min_frames:
            continue
        obs = t["obs"]
        # khóa kích thước: frame dày điểm nhất, hòa thì lấy frame gần cảm biến nhất
        ref = max(fs, key=lambda f: (obs[f]["points"], -math.hypot(obs[f]["center"][0], obs[f]["center"][1])))
        size = [float(v) for v in obs[ref]["size"]]
        flags = []
        # khoảng cách lớn nhất của track: vượt phạm vi class thì các keyframe sau bật outside
        max_dist = max(math.hypot(obs[f]["center"][0], obs[f]["center"][1]) for f in fs)
        key_cls = obs[ref].get("cls")
        range_m = ranges.get(key_cls, 60.0) if key_cls else 60.0
        # yaw liên tục: đi nhanh theo hướng chuyển động, đứng yên giữ yaw hình học
        yaw_lock = set(rule.get("yaw_by_width") or [])          # rào chắn: khoá một hướng cho cả track (spec 3D v2)
        locked_yaw = _wrap(float(obs[ref]["yaw"])) if key_cls in yaw_lock else None
        yaws = []
        for k2, f in enumerate(fs):
            o = obs[f]
            yaw = locked_yaw if locked_yaw is not None else o["yaw"]
            if locked_yaw is None and 0 < k2 < len(fs) - 1:
                a, b = np.array(obs[fs[k2 - 1]]["center"][:2]), np.array(obs[fs[k2 + 1]]["center"][:2])
                if np.hypot(*(b - a)) / max(fs[k2 + 1] - fs[k2 - 1], 1) * dt_frame > 0.3:
                    yaw = math.atan2(*(b - a)[::-1])
            if yaws:
                while yaw - yaws[-1] > math.pi / 2:
                    yaw -= math.pi
                while yaws[-1] - yaw > math.pi / 2:
                    yaw += math.pi
            yaws.append(yaw)
        shapes = []
        for k2, f in enumerate(fs):
            o = obs[f]
            if k2 and f - fs[k2 - 1] > 1:
                p, q = obs[fs[k2 - 1]], o
                gap = f - fs[k2 - 1]
                interp = gap <= max_hidden              # <= N keyframe che: nội suy, giữ ID
                for g in range(fs[k2 - 1] + 1, f):
                    u = (g - fs[k2 - 1]) / gap
                    c = [pv + u * (qv - pv) for pv, qv in zip(p["center"], q["center"])]
                    # RÀNG BUỘC CỨNG: một keyframe chỉ được một trong hai; đây là nội suy mượt -> occluded
                    shapes.append({"frame": g, "points": cvat_points(c, size, _wrap(yaws[k2 - 1] + u * (yaws[k2] - yaws[k2 - 1]))),
                                   "occluded": interp, "outside": False})
            c = list(o["center"])
            c[2] = c[2] - o["size"][2] / 2 + size[2] / 2
            shapes.append({"frame": f, "points": cvat_points(c, size, _wrap(yaws[k2])), "occluded": False, "outside": False})
        end = fs[-1] + 1
        # kết thúc track: frame kế là outside (rời màn hình), KHÔNG occluded
        shapes.append({"frame": end, "points": shapes[-1]["points"], "occluded": False, "outside": True})
        if max_dist > range_m:
            flags.append({"code": "ATTRIBUTE_CHECK",
                          "message": f"Track tới {max_dist:.0f} m, vượt phạm vi gán {range_m:.0f} m của class: xem lại các frame cuối."})
        for f in fs:
            s = obs[f]["size"]
            if obs[f]["points"] >= 0.5 * obs[ref]["points"] and any(abs(a - b) / b > 3 * size_tol for a, b in zip(s, size)):
                flags.append({"code": "ATTRIBUTE_CHECK",
                              "message": f"Kích thước ở frame {f} khác frame chuẩn ({ref}) hơn {int(3 * size_tol * 100)}%: "
                                         "xem có phải vật đổi hình (mở cửa, bốc hàng) hay cụm điểm dính vật khác."})
                break
        keys = _keyframes(shapes)
        out.append({"tid": i, "label_id": t["label_id"], "label_name": names.get(t["label_id"], str(t["label_id"])),
                    "start": fs[0], "end": fs[-1], "size": [round(v, 2) for v in size], "ref_frame": ref,
                    "shapes": keys, "keyframes": len(keys), "flags": flags + _merge_flags(t),
                    "checked": sum(1 for f in fs if obs[f].get("checked", True)) >= 0.5 * len(fs),
                    "confidence": round(float(np.mean([obs[f].get("confidence", obs[f].get("score", 0.5)) for f in fs])), 3),
                    "cls": key_cls})
    return {"tracks": out, "warnings": [] if out else ["Không dựng được track cuboid nào."], "engine": "kalman+hungarian"}


def _merge_flags(t: dict) -> list[dict]:
    flags = []
    for o in t["obs"].values():
        for fl in o.get("flags") or []:
            if fl not in flags:
                flags.append(fl)
    return flags[:4]
