"""Phiên dựng cuboid 3D chạy theo lô có lưu tiến độ (0.9.7, spec 3D v2 mục 4).

Thay kiểu gọi một request `/lidar/track` nguyên khối (dễ timeout với job 40-200 frame) bằng phiên có trạng thái:

  init      -> nạp profile, kiểm calibration/GPU, trả session_id + tóm tắt (số frame, camera, góc chiếu, thiết bị)
  geometry  -> chạy LiDAR + hình học + tracking theo lô frame (mặc định 10-20 frame/lần), lưu checkpoint sau mỗi lô
  vision    -> đối chiếu camera (ONNX 2D) theo lô, lưu checkpoint
  llm_batch -> (chỉ khi bật Claude) Pha 1 theo lô 6-10 track, rồi Pha 2 một lượt
  cancel    -> dừng an toàn, GIỮ kết quả các lô đã xong để người dùng xem

Mọi hàm ở đây chạy đồng bộ; app.py bọc trong endpoint. Trạng thái nằm trong bộ nhớ backend (mất khi khởi động lại
backend, giống các phiên 2D hiện có) — dữ liệu point cloud KHÔNG được ghi ra đĩa (job không cho export/import).
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Optional

import numpy as np

from . import lidar, lidar_assist, tracking_3d

STAGE_NAMES = {0: "geometry", 1: "vision", 2: "llm"}
MAX_SESSIONS = 8                     # giữ tối đa 8 phiên trong bộ nhớ (mỗi phiên vài chục MB với 40 frame)


class Session:
    """Một phiên dựng cuboid 3D. `frames` chỉ giữ trong RAM, không ghi ra đĩa."""

    def __init__(self, sid: str, labels: dict[str, int], names: dict[int, str], table: dict, rule: dict,
                 label_ids: dict[int, str]):
        self.id = sid
        self.labels = labels                     # {class_key: label_id}
        self.names = names                       # {label_id: tên}
        self.label_ids = label_ids               # {label_id: tên} cho judge_3d
        self.table = table
        self.rule = rule
        self.frames: list[tuple[int, np.ndarray]] = []          # [(số frame, point cloud)]
        self.contexts: dict[int, list] = {}                     # {số frame: [(tên camera, ảnh RGB)]}
        self.detected: dict[int, dict] = {}                     # {số frame: kết quả detect()}
        self.track: Optional[dict] = None                       # kết quả tracking gần nhất
        self.llm: dict[int, dict] = {}                          # {track_id: kết quả Pha 1+2}
        # 0.9.10: Claude chạy từng lô (mỗi request một lô): kết quả Pha 1 tích luỹ ở đây, Pha 2 chạy một lần khi đủ
        self.llm_p1: dict[int, dict] = {}
        self.llm_final = False
        self.step_lock = threading.Lock()                       # một lô Claude tại một thời điểm (panel hết giờ rồi bấm tiếp)
        self.created = time.time()
        self.touched = self.created
        self.cancelled = False
        self.lock = threading.Lock()
        self.errors: list[str] = []
        self.done = {"geometry": 0, "vision": 0, "llm": 0}

    # ---------------------------------------------------------- trạng thái ---
    def touch(self) -> None:
        self.touched = time.time()

    def summary(self, gpu: Optional[dict] = None, rig: Optional[dict] = None) -> dict:
        return {"session_id": self.id, "frames": len(self.frames), "contexts": len(self.contexts),
                "labels": [{"id": i, "name": n} for i, n in sorted(self.names.items()) if n],
                "done": dict(self.done), "cancelled": self.cancelled, "errors": self.errors[-5:],
                "tracks": len((self.track or {}).get("tracks") or []),
                "buckets": self.buckets(), "camera": {k: v for k, v in (rig or {}).items() if k != "cameras"} or None,
                "gpu": gpu}

    def buckets(self) -> dict:
        counts = {"high_confidence": 0, "needs_review": 0, "suspected_noise": 0}
        for r in self.llm.values():
            b = r.get("bucket")
            if b in counts:
                counts[b] += 1
        return counts


_sessions: dict[str, Session] = {}


def create(labels: dict[str, int], names: dict[int, str], table: dict, rule: dict) -> Session:
    """Mở phiên mới; dọn phiên cũ nhất nếu quá MAX_SESSIONS."""
    while len(_sessions) >= MAX_SESSIONS:
        oldest = min(_sessions.values(), key=lambda s: s.touched)
        _sessions.pop(oldest.id, None)
    sid = uuid.uuid4().hex[:12]
    sess = Session(sid, labels, names, table, rule, {i: n for i, n in names.items()})
    _sessions[sid] = sess
    return sess


def get(sid: str) -> Optional[Session]:
    return _sessions.get(sid)


def drop(sid: str) -> bool:
    return _sessions.pop(sid, None) is not None


# --------------------------------------------------------------- các lô ---
def add_frames(sess: Session, batch: list[tuple[int, np.ndarray]], contexts: dict[int, list]) -> dict:
    """Nhận lô frame (đã đọc point cloud ở app.py) + ảnh camera của lô. Frame đã có thì bỏ qua (panel gửi lại lô)."""
    with sess.lock:
        have = {f for f, _ in sess.frames}
        added = 0
        for f, pts in batch:
            if f in have:                       # đã có: không thêm lại (tránh nhân đôi frame khi gửi lại lô)
                continue
            sess.frames.append((int(f), pts))
            have.add(f)
            added += 1
        sess.contexts.update(contexts)
        sess.frames.sort(key=lambda x: x[0])
        sess.touch()
        return {"session_id": sess.id, "frames": len(sess.frames), "added": added, "contexts": len(sess.contexts)}


def run_geometry(sess: Session, frames: Optional[list[int]] = None, limit: int = 20, predict=None,
                 after=None) -> dict:
    """Dựng cuboid + tracking theo lô. frames: chỉ chạy các frame này (None = các frame chưa chạy).

    predict(points) -> {objects,...} để app.py truyền bộ dò ONNX 3D vào (None = hình học thuần như 0.9.6).
    after(frame) -> None gọi sau mỗi frame (để endpoint lưu checkpoint / kiểm hủy).
    """
    with sess.lock:
        todo = [f for f, _ in sess.frames if f not in sess.detected]
        if frames:
            todo = [f for f in todo if f in set(frames)]
        batch = todo[:max(1, int(limit))]
        by_frame = dict(sess.frames)
        if not batch and sess.track is not None:
            # 0.9.10: không còn frame nào: KHÔNG dựng lại track (dựng lại làm mất kết quả đối chiếu camera đã chạy,
            # nên "Tiếp tục từ chỗ dừng" phải làm lại từ đầu)
            sess.touch()
            return {"session_id": sess.id, "ran": 0, "done": dict(sess.done),
                    "tracks": len(sess.track.get("tracks") or []), "remaining": 0,
                    "cancelled": sess.cancelled, "warnings": sess.track.get("warnings") or []}
    done = 0
    for f in batch:
        if sess.cancelled:
            break
        pts = by_frame.get(f)
        if pts is None:
            continue
        try:
            res = predict(pts) if predict else lidar.detect(pts, sess.labels, sess.table, sess.rule, debug=False)
        except Exception as exc:  # noqa: BLE001 - một frame lỗi không làm hỏng cả phiên
            sess.errors.append(f"frame {f}: {type(exc).__name__}: {str(exc)[:160]}")
            continue
        for i, o in enumerate(res.get("objects") or [], 1):
            o["id"] = i
        with sess.lock:
            sess.detected[f] = res
            sess.done["geometry"] = len(sess.detected)
        done += 1
        if after:
            after(f)
    # dựng lại track trên toàn bộ frame đã có (rẻ: thuần số học). 0.9.8: giữ vật thể từng frame (`_obs`) cho bước
    # đối chiếu camera / Claude, và gắn gợi ý rổ cho track vụn (C3).
    with sess.lock:
        frames_in = [{"frame": f, "objects": sess.detected[f]["objects"]} for f in sorted(sess.detected)]
        sess.track = tracking_3d.build(frames_in, sess.rule, sess.names, keep_obs=True)
        mark_debris(sess.track.get("tracks") or [], len(sess.detected))
        if done:
            # có frame mới: track dựng lại, số hiệu track có thể đổi -> kết quả camera / Claude cũ không còn khớp
            sess.llm, sess.llm_p1, sess.llm_final = {}, {}, False
            sess.done["vision"] = sess.done["llm"] = 0
        sess.touch()
        return {"session_id": sess.id, "ran": done, "done": dict(sess.done),
                "tracks": len(sess.track.get("tracks") or []), "remaining": max(0, len(todo) - done),
                "cancelled": sess.cancelled, "warnings": sess.track.get("warnings") or []}


# ------------------------------------------------------ 0.9.8: track cho Claude / camera ---
def ref_frame(t: dict) -> Optional[int]:
    """Keyframe rõ nhất của track: nhiều điểm LiDAR nhất, hoà thì gần cảm biến nhất."""
    import math

    obs = t.get("_obs") or {}
    if not obs:
        return t.get("ref_frame")
    return max(obs, key=lambda f: (obs[f].get("points", 0), -math.hypot(*obs[f]["center"][:2])))


def view(t: dict) -> dict:
    """Track (dạng tracking_3d.build) -> dạng judge_3d / lidar_vision cần: id, center, yaw, size, points, speed, obs.

    Trước 0.9.8 hai bước đó đọc t['center'] / t['id'] / t['obs'] mà track không có -> KeyError: 'center' khi bật
    Claude, và đối chiếu camera không tìm được keyframe nào."""
    import math

    obs = t.get("_obs") or {}
    f = ref_frame(t)
    o = obs.get(f) if f is not None else None
    fs = sorted(obs)
    speed = 0.0
    if len(fs) >= 2:
        a, b = obs[fs[0]]["center"], obs[fs[-1]]["center"]
        speed = math.hypot(b[0] - a[0], b[1] - a[1]) / max(fs[-1] - fs[0], 1) / float(t.get("frame_dt_s") or 0.5)
    out = {k: v for k, v in t.items() if not k.startswith("_")}
    out.update(id=t.get("tid"), obs=obs, ref_frame=f, speed=round(speed, 2),
               center=list(o["center"]) if o else [0.0, 0.0, 0.0], yaw=float(o.get("yaw", 0.0)) if o else 0.0,
               points=int(o.get("points", 0)) if o else 0, frames_seen=len(fs))
    return out


def mark_debris(tracks: list[dict], n_frames: int) -> int:
    """C3: track vụn (thấy ở rất ít frame của một job dài, hoặc luôn quá ít điểm) -> gợi ý rổ, không xoá.

    Người duyệt vẫn thấy chúng trong rổ Nghi rác / Cần xem và tick lại được."""
    marked = 0
    for t in tracks:
        obs = t.get("_obs") or {}
        seen = len(obs)
        if not seen:
            continue
        low = all(any(fl.get("code") == "LOW_VISIBILITY" for fl in (o.get("flags") or [])) for o in obs.values())
        if n_frames >= 8 and seen <= 2:
            t["bucket_hint"] = "suspected_noise"
            t["checked"] = False
            t.setdefault("notes", []).append(f"Chỉ thấy ở {seen}/{n_frames} frame: có thể là mảnh vụn (cây, tường, cột).")
            marked += 1
        elif low and seen <= max(3, n_frames // 4):
            t["bucket_hint"] = "suspected_noise"
            t["checked"] = False
            t.setdefault("notes", []).append("Luôn rất ít điểm LiDAR và chỉ thấy ở vài frame: nghi rác.")
            marked += 1
        elif n_frames >= 8 and seen < 0.15 * n_frames:
            t.setdefault("bucket_hint", "needs_review")
            t.setdefault("notes", []).append(f"Chỉ thấy ở {seen}/{n_frames} frame: xem có phải vật thật không.")
            marked += 1
    return marked


def _frames_with_image(sess: Session, t: dict) -> list[int]:
    """Các frame của track có ảnh camera, rõ nhất trước (nhiều điểm, gần cảm biến)."""
    import math

    obs = t.get("_obs") or {}
    return sorted((f for f in obs if sess.contexts.get(int(f))),
                  key=lambda f: (-obs[f].get("points", 0), math.hypot(*obs[f]["center"][:2])))


def run_vision(sess: Session, vision, rig: dict, offset: Optional[float] = None, limit: int = 10,
               after=None) -> dict:
    """Đối chiếu camera cho các track đã dựng, theo lô track. Không có bộ dò 2D thì bỏ qua, không lỗi.

    0.9.8: chiếu ở keyframe rõ nhất CÓ ảnh camera (thử tối đa 2 frame, lấy kết quả tốt hơn); track không có ảnh nào
    vẫn được đánh dấu đã xét (verdict no_image) để vòng lặp của panel kết thúc."""
    from . import lidar_vision

    if not sess.track:
        return {"session_id": sess.id, "skipped": "chưa dựng geometry"}
    tracks = sess.track.get("tracks") or []
    todo = [t for t in tracks if not t.get("vision2d")][:max(1, int(limit))]
    allowed = {lidar_vision.COCO_FOR_3D[k] for k in sess.labels if k in lidar_vision.COCO_FOR_3D} or None
    changed = 0
    for t in todo:
        if sess.cancelled:
            break
        obs = t.get("_obs") or {}
        best = None
        for f in _frames_with_image(sess, t)[:2]:
            o = obs[f]
            try:
                res = lidar_vision.verify(vision, sess.contexts[int(f)],
                                          [{"id": 1, "center": o["center"], "size": o["size"], "cls": o.get("cls") or t.get("cls")}],
                                          rig, offset or 0.0, allowed)
            except Exception as exc:  # noqa: BLE001 - một track lỗi không làm hỏng cả lô
                sess.errors.append(f"track {t.get('tid')}: đối chiếu camera lỗi: {str(exc)[:120]}")
                continue
            r = (res.get("results") or {}).get(1)
            if r and (best is None or lidar_vision._VERDICT_RANK[r["verdict"]] > lidar_vision._VERDICT_RANK[best["verdict"]]):
                best = dict(r, frame=int(f))
            if best and best["verdict"] == "confirmed":
                break
        if best is None:
            t["vision2d"] = {"verdict": "no_image"}
        else:
            t["vision2d"] = best
            if best["verdict"] == "none":
                t["bucket_hint"] = "suspected_noise"
                t["checked"] = False
            elif best["verdict"] in ("unknown", "partial") and t.get("bucket_hint") != "suspected_noise":
                t["bucket_hint"] = "needs_review"
            changed += 1
        with sess.lock:
            sess.done["vision"] = sum(1 for x in tracks if x.get("vision2d"))
        if after:
            after(t.get("tid"))
    return {"session_id": sess.id, "checked": changed, "done": dict(sess.done),
            "remaining": max(0, len(tracks) - len([t for t in tracks if t.get("vision2d")])),
            "cancelled": sess.cancelled}


def _llm_failed(r: dict) -> bool:
    return str(r.get("reason") or "").startswith(("Claude lỗi", "Claude không chạy được"))


def run_llm(sess: Session, llm, autoprofile: dict, memory: Optional[dict] = None, batch_size: int = 8,
            after=None, max_batches: Optional[int] = None, retry_failed: bool = False, guide: str = "") -> dict:
    """Chạy Claude 2 pha theo lô track. Không có llm thì trả về lý do, không lỗi.

    0.9.10: chạy được từng phần. max_batches = số lô Pha 1 trong lần gọi này (None = hết), kết quả tích luỹ trong
    phiên; khi mọi track đã có kết quả Pha 1 thì chạy Pha 2 một lần. Panel gọi lặp tới khi `remaining == 0` và
    `final`, nên mỗi request chỉ dài khoảng một lần gọi Claude (0.9.9: một request cho mọi track -> quá 5 phút với job
    nhiều track). Dừng (`cancelled`) giữa các lô: phần đã chấm vẫn giữ. retry_failed: chấm lại các track lần trước
    Claude lỗi (khi bạn bấm "Tiếp tục từ chỗ dừng")."""
    from . import judge_3d

    if llm is None:
        return {"session_id": sess.id, "skipped": "chưa bật Claude (cần ANTHROPIC_API_KEY hoặc LLM local)"}
    if not sess.track:
        return {"session_id": sess.id, "skipped": "chưa dựng geometry"}
    batch_size = max(1, int(batch_size))
    with sess.step_lock:
        raw = list(sess.track.get("tracks") or [])
        tracks = [view(t) for t in raw]               # 0.9.8: đủ id / center / yaw / points cho judge_3d
        by_id = {t["id"]: t for t in tracks}
        raw_by_id = {t.get("tid"): t for t in raw}
        with sess.lock:
            if retry_failed:
                failed = [tid for tid, r in sess.llm_p1.items() if _llm_failed(r)]
                for tid in failed:
                    sess.llm_p1.pop(tid, None)
                if failed:
                    sess.llm_final = False
            todo = [t for t in tracks if t["id"] not in sess.llm_p1]
        ran = 0
        try:
            while todo and not sess.cancelled and (max_batches is None or ran < max_batches):
                group, todo = todo[:batch_size], todo[batch_size:]
                cam_images = _camera_images(sess, [raw_by_id[t["id"]] for t in group if t["id"] in raw_by_id])
                p1 = judge_3d.phase1(llm, group, sess.label_ids, autoprofile, cam_images, memory, batch_size, guide=guide)
                with sess.lock:
                    sess.llm_p1.update(p1)
                    sess.llm = {**sess.llm, **p1}              # kết quả tạm: dừng giữa chừng vẫn thấy phần đã chấm
                    sess.done["llm"] = len(sess.llm_p1)
                    sess.llm_final = False
                    sess.touch()
                ran += 1
                if after:
                    after(None)
            if not todo and not sess.cancelled and not sess.llm_final:
                p2 = judge_3d.phase2(llm, [by_id[i] for i in by_id if i in sess.llm_p1], sess.label_ids,
                                     dict(sess.llm_p1), sess.rule, guide=guide)
                with sess.lock:
                    sess.llm = p2
                    sess.llm_final = True
                    sess.touch()
        except Exception as exc:  # noqa: BLE001 - lỗi bất ngờ: track chưa chấm vào Cần xem kèm lý do, phiên không trả 500
            msg = f"Claude không chạy được: {type(exc).__name__}: {str(exc)[:160]}"
            sess.errors.append(msg)
            with sess.lock:
                for t in tracks:
                    if t["id"] not in sess.llm_p1:
                        sess.llm_p1[t["id"]] = {"keep": None, "bucket": "needs_review", "reason": msg}
                sess.llm = {**sess.llm_p1, **sess.llm}
                sess.done["llm"] = len(sess.llm_p1)
            return {"session_id": sess.id, "error": msg, "judged": len(sess.llm_p1), "total": len(tracks),
                    "remaining": 0, "final": False, "done": dict(sess.done), "cancelled": sess.cancelled}
        with sess.lock:
            judged = len([i for i in by_id if i in sess.llm_p1])
            final = sess.llm_final
            current = dict(sess.llm)
    errors = sorted({str(r.get("reason")) for r in current.values() if _llm_failed(r)})
    out = {"session_id": sess.id, "judged": judged, "total": len(tracks), "remaining": len(tracks) - judged,
           "final": final, "ran": ran, "done": dict(sess.done), "cancelled": sess.cancelled, "errors": errors[:3]}
    if final:
        out["buckets"] = judge_3d.summarize(current)
    return out


def progress(sess: Session) -> dict:
    """0.9.10: phiên đã chạy tới đâu, để panel "Tiếp tục từ chỗ dừng" chỉ chạy phần còn thiếu."""
    tracks = (sess.track or {}).get("tracks") or []
    with sess.lock:
        return {"session_id": sess.id, "frames": len(sess.frames), "frame_list": [f for f, _ in sess.frames],
                "geometry": len(sess.detected),
                "tracks": len(tracks), "vision": sum(1 for t in tracks if t.get("vision2d")),
                "llm": len([t for t in tracks if t.get("tid") in sess.llm_p1]), "llm_final": sess.llm_final,
                "llm_failed": sum(1 for r in sess.llm_p1.values() if _llm_failed(r)),
                "contexts": len(sess.contexts), "cancelled": sess.cancelled}


def _camera_images(sess: Session, raw_tracks: list[dict]) -> dict[int, dict]:
    """Ảnh camera của keyframe rõ nhất (có ảnh) từng track, kèm box 2D đã chiếu: {tid: {camera: (ảnh, box)}}."""
    from . import camera_rig

    rig = camera_rig.build_rig(sorted({n for ctx in sess.contexts.values() for n, _ in ctx}),
                               sess.rule.get("cameras") or {}, sess.rule.get("camera_meta"))
    offset = float(sess.rule.get("offset_deg") or 0.0)
    out: dict[int, dict] = {}
    for t in raw_tracks:
        obs = t.get("_obs") or {}
        for f in _frames_with_image(sess, t)[:1]:
            o = obs[f]
            p = camera_rig.project(o["center"], o["size"], rig["cameras"], offset)
            if not p:
                continue
            img = dict(sess.contexts[int(f)]).get(p["camera"])
            if img is None:
                continue
            h, w = img.shape[:2]
            box = [max(0.0, (p["u"] - p["half"]) * w), 0.3 * h, min(float(w), (p["u"] + p["half"]) * w), 0.7 * h]
            out[t["tid"]] = {p["camera"]: (img, box)}
    return out


def singles(sess: Session) -> list[dict]:
    """0.9.11: vật chỉ thấy ở MỘT frame (tracking bỏ vì ngắn hơn min_track_frames). Khi ghi dạng shape từng frame,
    panel hiện chúng ở rổ Nghi rác, mặc định không tick, để bạn tự chọn."""
    from .lidar import cvat_points

    used: set[tuple[int, int]] = set()
    for t in (sess.track or {}).get("tracks") or []:
        for f, o in (t.get("_obs") or {}).items():
            used.add((int(f), id(o)))
    out = []
    for f in sorted(sess.detected):
        for o in sess.detected[f].get("objects") or []:
            if (int(f), id(o)) in used:
                continue
            out.append({"frame": int(f), "label_id": o["label_id"], "label_name": sess.names.get(o["label_id"], str(o["label_id"])),
                        "cls": o.get("cls"), "size": [round(float(v), 2) for v in o["size"]], "points_n": int(o.get("points", 0)),
                        "points": cvat_points(o["center"], o["size"], float(o.get("yaw", 0.0))),
                        "confidence": round(float(o.get("confidence", o.get("score", 0.5))), 3)})
    return out


def result(sess: Session, limit: Optional[int] = None, per_frame: bool = False) -> dict:
    """Kết quả gộp: tracks (kèm bucket + Claude sửa) + rổ đếm + cảnh báo, để panel hiển thị và ghi vào CVAT.

    0.9.11: per_frame=True thêm `frames` ([{frame, points, seen}]) cho mỗi track và `singles` (vật chỉ thấy ở 1 frame),
    để panel ghi dạng "shape từng frame" thay cho track."""
    tracks = list((sess.track or {}).get("tracks") or [])
    if limit is not None:
        tracks = tracks[:max(0, int(limit))]
    out = []
    for t in tracks:
        r = sess.llm.get(t.get("tid")) or {}
        item = {k: v for k, v in t.items() if not k.startswith("_")}      # `_obs` chỉ nằm trong RAM backend
        if per_frame:
            item["frames"] = list(t.get("_frames") or [])
        item["bucket"] = r.get("bucket") or default_bucket(t, sess.track.get("tracks") or [])
        if t.get("notes"):
            item["notes"] = list(t["notes"])
        if r.get("final_label"):
            item["label_name"] = r["final_label"]
            match = next((i for i, n in sess.names.items() if str(n).strip().lower() == str(r["final_label"]).strip().lower()), None)
            if match is not None:
                item["label_id"] = match                                   # Claude đổi class: ghi đúng label
        if r.get("reason"):
            item["llm_reason"] = r["reason"]
        if r.get("heading_hint"):
            item["heading_hint"] = r["heading_hint"]
        if r.get("keep") is False:
            item["checked"] = False
        out.append(item)
    from .judge_3d import summarize

    buckets = {"high_confidence": 0, "needs_review": 0, "suspected_noise": 0}
    for item in out:
        if item["bucket"] in buckets:
            buckets[item["bucket"]] += 1
    rule = {k: v for k, v in sess.rule.items() if k not in ("dropped",)}
    res = {"session_id": sess.id, "tracks": out, "buckets": buckets,
           "warnings": list((sess.track or {}).get("warnings") or []) + _error_summary(sess.errors),
           "frames": sorted(sess.detected), "rule": rule, "engine": (sess.track or {}).get("engine"),
           "checked_by": {"camera": sum(1 for t in tracks if (t.get("vision2d") or {}).get("verdict") not in (None, "no_image")),
                          "claude": sum(1 for t in tracks if t.get("tid") in sess.llm)}}
    if per_frame:
        res["singles"] = singles(sess)
    return res


# Class mà kích thước một mình không đủ phân biệt với vật cố định (thùng rác, container, nhà vệ sinh di động, tường):
# chỉ vào "Tin cậy cao" khi camera hoặc Claude xác nhận (0.9.8, C2).
NEEDS_PROOF = ("trailer", "construction vehicle")


def default_bucket(t: dict, all_tracks: Optional[list] = None) -> str:
    """Rổ khi Claude chưa chấm (0.9.8, C1): không có bằng chứng thì không 'Tin cậy cao'.

    - gợi ý từ bước đối chiếu camera / lọc mảnh vụn thắng trước
    - camera xác nhận đúng class -> Tin cậy cao
    - bỏ tick / cờ nghi ngờ / class cần bằng chứng (trailer, construction vehicle) -> Cần xem
    - chưa ai kiểm (không ảnh, không model 2D) -> Cần xem"""
    if t.get("bucket_hint"):
        return t["bucket_hint"]
    if t.get("checked") is False:
        return "needs_review"
    codes = {str(f.get("code") or "") for f in (t.get("flags") or [])}
    if codes & {"UNCERTAIN_CLASS", "LEARNED"}:
        return "needs_review"
    if (t.get("vision2d") or {}).get("verdict") == "confirmed":
        return "high_confidence"
    # trailer nhỏ (C2): không có ảnh xác nhận chính nó, nhưng xe kéo đứng ngay trước ĐÃ được camera xác nhận
    if str(t.get("cls") or "").lower() == "trailer" and _tow_vehicle_ahead(t, all_tracks or []):
        return "high_confidence"
    return "needs_review"


def _tow_vehicle_ahead(t: dict, tracks: list[dict]) -> bool:
    """Trailer có xe kéo (car / truck, đã được camera xác nhận) đứng sát (≤ 3 m tính từ mép) ở cùng frame chuẩn."""
    import math

    mine = (t.get("_obs") or {}).get(t.get("ref_frame"))
    if not mine:
        return False
    for o in tracks:
        if o is t or str(o.get("cls") or "").lower() not in ("car", "truck"):
            continue
        if (o.get("vision2d") or {}).get("verdict") != "confirmed":
            continue
        other = (o.get("_obs") or {}).get(t.get("ref_frame"))
        if not other:
            continue
        gap = math.hypot(mine["center"][0] - other["center"][0], mine["center"][1] - other["center"][1])
        if gap <= (mine["size"][0] + other["size"][0]) / 2 + 3.0:
            return True
    return False


def _error_summary(errors: list[str]) -> list[str]:
    """Gộp lỗi lặp (40 frame cùng một lỗi ảnh camera) thành một dòng có đếm."""
    import re

    counts: dict[str, int] = {}
    for e in errors:
        key = re.sub(r"^(frame|track) \d+:?\s*", "", e)
        counts[key] = counts.get(key, 0) + 1
    return [f"{k} (×{n})" if n > 1 else k for k, n in list(counts.items())[-4:]]
