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
    # dựng lại track trên toàn bộ frame đã có (rẻ: thuần số học)
    with sess.lock:
        frames_in = [{"frame": f, "objects": sess.detected[f]["objects"]} for f in sorted(sess.detected)]
        sess.track = tracking_3d.build(frames_in, sess.rule, sess.names)
        sess.touch()
        return {"session_id": sess.id, "ran": done, "done": dict(sess.done),
                "tracks": len(sess.track.get("tracks") or []), "remaining": max(0, len(todo) - done),
                "cancelled": sess.cancelled, "warnings": sess.track.get("warnings") or []}


def run_vision(sess: Session, vision, rig: dict, offset: Optional[float] = None, limit: int = 10,
               after=None) -> dict:
    """Đối chiếu camera cho các track đã dựng, theo lô track. Không có bộ dò 2D thì bỏ qua, không lỗi."""
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
        # keyframe rõ nhất của track: chiếu cuboid ở frame đó lấy box 2D để đối chiếu
        obs = t.get("obs") or {}
        f = max(obs, key=lambda k: obs[k].get("points", 0)) if obs else t.get("ref_frame")
        ctx = sess.contexts.get(int(f)) if f is not None else None
        if not ctx:
            continue
        objs = [{"id": 1, "center": obs[f]["center"], "size": obs[f]["size"], "cls": obs[f].get("cls")}]
        res = lidar_vision.verify(vision, ctx, objs, rig, offset or 0.0, allowed)
        r = (res.get("results") or {}).get(1)
        if r:
            t["vision2d"] = r
            if r["verdict"] == "none":
                t["bucket_hint"] = "suspected_noise"
            elif r["verdict"] == "unknown":
                t["bucket_hint"] = "needs_review"
            changed += 1
        with sess.lock:
            sess.done["vision"] = sum(1 for x in tracks if x.get("vision2d"))
        if after:
            after(t.get("tid"))
    return {"session_id": sess.id, "checked": changed, "done": dict(sess.done),
            "remaining": max(0, len(tracks) - len([t for t in tracks if t.get("vision2d")])),
            "cancelled": sess.cancelled}


def run_llm(sess: Session, llm, autoprofile: dict, memory: Optional[dict] = None, batch_size: int = 8,
            after=None) -> dict:
    """Chạy Claude 2 pha theo lô track. Không có llm thì trả về lý do, không lỗi."""
    from . import judge_3d

    if llm is None:
        return {"session_id": sess.id, "skipped": "chưa bật Claude (cần ANTHROPIC_API_KEY hoặc LLM local)"}
    if not sess.track:
        return {"session_id": sess.id, "skipped": "chưa dựng geometry"}
    tracks = [dict(t) for t in sess.track.get("tracks") or []]
    # ảnh của keyframe rõ nhất từng track, kèm box 2D đã chiếu
    from . import camera_rig

    cam_images: dict[int, dict] = {}
    rig = camera_rig.build_rig(sorted({n for ctx in sess.contexts.values() for n, _ in ctx}),
                               sess.rule.get("cameras") or {}, sess.rule.get("camera_meta"))
    for t in tracks:
        obs = t.get("obs") or {}
        f = max(obs, key=lambda k: obs[k].get("points", 0)) if obs else None
        ctx = sess.contexts.get(int(f)) if f is not None else None
        if not ctx or f is None:
            continue
        o = obs[f]
        p = camera_rig.project(o["center"], o["size"], rig["cameras"], float(sess.rule.get("offset_deg") or 0.0))
        box = None
        if p:
            img = dict(ctx).get(p["camera"])
            if img is not None:
                h, w = img.shape[:2]
                box = [(p["u"] - p["half"]) * w, 0.3 * h, (p["u"] + p["half"]) * w, 0.7 * h]
                cam_images[t["tid"]] = {p["camera"]: (img, box)}
    p1 = judge_3d.phase1(llm, tracks, sess.label_ids, autoprofile, cam_images, memory, batch_size)
    with sess.lock:
        sess.llm = dict(p1)
        sess.done["llm"] = len(p1)
    if after:
        after(None)
    p2 = judge_3d.phase2(llm, tracks, sess.label_ids, p1, sess.rule)
    with sess.lock:
        sess.llm = p2
        sess.touch()
    return {"session_id": sess.id, "judged": len(p2), "buckets": judge_3d.summarize(p2),
            "done": dict(sess.done), "cancelled": sess.cancelled}


def result(sess: Session, limit: Optional[int] = None) -> dict:
    """Kết quả gộp: tracks (kèm bucket + Claude sửa) + rổ đếm + cảnh báo, để panel hiển thị và ghi vào CVAT."""
    tracks = list((sess.track or {}).get("tracks") or [])
    if limit is not None:
        tracks = tracks[:max(0, int(limit))]
    out = []
    for t in tracks:
        r = sess.llm.get(t.get("tid")) or {}
        item = dict(t)
        item["bucket"] = r.get("bucket") or t.get("bucket_hint") or (
            "needs_review" if t.get("checked") is False else "high_confidence")
        if r.get("final_label"):
            item["label_name"] = r["final_label"]
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
    return {"session_id": sess.id, "tracks": out, "buckets": buckets,
            "warnings": list((sess.track or {}).get("warnings") or []) + sess.errors[-3:],
            "frames": sorted(sess.detected), "rule": sess.rule, "engine": (sess.track or {}).get("engine")}
