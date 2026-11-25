"""Claude Vision 3D toàn trình (0.9.7, spec 3D v2 mục 3.3): chạy theo cấp TRACK, không gửi từng frame rời.

Khi người dùng bật "Dùng Claude AI hỗ trợ" ở job 3D, tool chạy 2 pha:
  Pha 1 — Nhận diện, lọc rác & gán nhãn: với mỗi track, chọn 1-2 keyframe rõ nhất (nhiều điểm LiDAR nhất, gần nhất,
          ít bị che), gửi ảnh camera có vẽ khung chiếu đánh số + ảnh crop phóng to + ảnh BEV. Claude trả về JSON:
          giữ/bỏ, class đúng, hướng đầu (heading hint), độ tin cậy, lý do.
  Pha 2 — Kiểm duyệt toàn bộ trackset: kiểm luật bằng code (nhất quán nhãn trong track, khoảng cách, ±5% kích thước,
          occluded/outside) rồi gửi các ca biên giới/mâu thuẫn cho Claude chốt lại. Phân loại thành 3 rổ:
          high_confidence / needs_review / suspected_noise cho UI duyệt nhanh.

Prompt tiếng Anh, sinh động từ autoprofile (định nghĩa class + luật guideline của project). Tool chỉ gợi ý: người
dùng duyệt trước khi ghi vào CVAT.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import cv2
import numpy as np

MAX_SIDE = 1568
MAX_TRACKS_PER_BATCH = 8
SYSTEM = ("You are a senior LiDAR 3D annotation QA expert. You review cuboid track proposals for autonomous driving "
          "datasets. Answer strictly as JSON, in English. Be decisive about obvious noise (poles, trash bins, "
          "vegetation, fixed objects) and conservative about vehicle classes when geometry is ambiguous.")


# ------------------------------------------------------------ ảnh phụ trợ ---
def _jpeg(img: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


COLORS = [(255, 64, 64), (64, 200, 255), (255, 200, 0), (120, 255, 120), (255, 120, 255), (255, 255, 255)]


def marked_image(image: np.ndarray, items: list[dict]) -> np.ndarray:
    """Vẽ khung chiếu 2D của các track lên ảnh camera kèm số #1, #2... để Claude thấy bối cảnh."""
    out = np.ascontiguousarray(image.copy())
    h, w = out.shape[:2]
    thick = max(2, int(round(max(h, w) / 500)))
    font = cv2.FONT_HERSHEY_SIMPLEX
    for it in items:
        x0, y0, x1, y1 = [int(round(v)) for v in it["box"]]
        color = COLORS[(it["id"] - 1) % len(COLORS)]
        cv2.rectangle(out, (x0, y0), (x1, y1), color, thick)
        label = f"#{it['id']}"
        (tw, th), _ = cv2.getTextSize(label, font, 0.7, 2)
        ty = max(y0 - 6, th + 4)
        cv2.rectangle(out, (x0, ty - th - 4), (x0 + tw + 4, ty + 2), color, -1)
        cv2.putText(out, label, (x0 + 2, ty - 2), font, 0.7, (0, 0, 0), 2)
    return out


def crop_track(image: np.ndarray, box: list[float], pad: float = 0.3) -> np.ndarray:
    """Crop phóng to quanh một track (kèm viền 30%)."""
    h, w = image.shape[:2]
    x0, y0, x1, y1 = box
    pw, ph = int((x1 - x0) * pad), int((y1 - y0) * pad)
    a = max(0, int(x0) - pw)
    b = min(w, int(x1) + pw)
    c = max(0, int(y0) - ph)
    d = min(h, int(y1) + ph)
    if b - a < 8 or d - c < 8:
        return np.zeros((0, 0, 3), np.uint8)
    return np.ascontiguousarray(image[c:d, a:b])


def bev_image(tracks: list[dict], center: Optional[list[float]] = None, radius: float = 40.0, size: int = 480) -> Optional[np.ndarray]:
    """Bản đồ BEV quanh khu vực track: điểm LiDAR xám, khung cuboid màu theo số thứ tự track.

    tracks: [{id, center, size, yaw, bev_points}] — bev_points là danh sách (x, y) tuỳ chọn."""
    if not tracks:
        return None
    img = np.full((size, size, 3), 250, np.uint8)
    scale = (size / 2 - 10) / radius
    cx, cy = (center or [0.0, 0.0])[:2]

    def to_px(x, y):
        return int(size / 2 + (x - cx) * scale), int(size / 2 - (y - cy) * scale)

    for t in tracks:
        for p in (t.get("bev_points") or []):
            px, py = to_px(p[0], p[1])
            if 0 <= px < size and 0 <= py < size:
                img[py, px] = (190, 190, 190)
    for i, t in enumerate(tracks, 1):
        c = t["center"][:2]
        yaw, (l, wd, _h) = t.get("yaw", 0.0), t["size"]
        ca, sa = math.cos(yaw), math.sin(yaw)
        corners = []
        for dx, dy in ((l / 2, wd / 2), (l / 2, -wd / 2), (-l / 2, -wd / 2), (-l / 2, wd / 2)):
            corners.append(to_px(c[0] + dx * ca - dy * sa, c[1] + dx * sa + dy * ca))
        color = COLORS[(i - 1) % len(COLORS)]
        pts = np.array(corners, np.int32)
        cv2.polylines(img, [pts], True, color, 2)
        cv2.putText(img, f"#{i}", corners[0], cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    return img


# ------------------------------------------------------- chọn keyframe ---
def pick_keyframes(track: dict, limit: int = 2) -> list[int]:
    """1-2 frame rõ nhất của track: dày điểm nhất, hoà thì lấy gần cảm biến nhất."""
    obs = track.get("obs") or {}
    if not obs:
        return [track.get("start", 0)]
    ranked = sorted(obs.items(), key=lambda kv: (-kv[1].get("points", 0), math.hypot(kv[1]["center"][0], kv[1]["center"][1])))
    return [int(f) for f, _ in ranked[:limit]]


# ------------------------------------------------------------ prompt ---
def class_definitions(autoprofile: dict, labels: dict[int, str]) -> list[str]:
    """Định nghĩa class tiếng Anh sinh động từ profile: mô tả + kích thước từ bảng size (nếu có)."""
    out = []
    sizes = (autoprofile.get("rules") or {}).get("lidar", {}).get("sizes") or {}
    for label_id, name in sorted(labels.items()):
        key = name.strip().lower()
        rows = sizes.get(key) or []
        if rows:
            spans = ["%.1f-%.1f" % (r[0][0], r[0][1]) + "m long, " + "%.1f-%.1f" % (r[1][0], r[1][1]) + "m wide, "
                     + "%.1f-%.1f" % (r[2][0], r[2][1]) + "m tall" for r in rows]
            out.append(f"- {name} (id {label_id}): size {', or '.join(spans)}")
        else:
            out.append(f"- {name} (id {label_id})")
    return out


def build_track_prompt(tracks: list[dict], labels: dict[int, str], autoprofile: dict,
                       memory: Optional[dict] = None) -> str:
    """Prompt tiếng Anh Pha 1: định nghĩa class + luật + danh sách track kèm hình học gọn."""
    lines = ["TASK LABELS (class the cuboids must get):"]
    lines += class_definitions(autoprofile, labels)
    rule = (autoprofile.get("rules") or {}).get("lidar") or {}
    if rule.get("ranges"):
        lines.append("RANGE LIMITS: " + ", ".join(f"{k} <= {v}m" for k, v in rule["ranges"].items()))
    if rule.get("max_occluded_frames"):
        lines.append(f"OCCLUSION RULE: a track hidden up to {rule['max_occluded_frames']} keyframes stays one track "
                     "(occluded=true); longer gaps end the track.")
    exclude = autoprofile.get("exclude") or []
    if exclude:
        lines.append("DO NOT ANNOTATE / REMOVE AS NOISE: " + "; ".join(exclude))
    if memory:
        lessons = memory.get("lessons") or []
        if lessons:
            lines.append("PROJECT LESSONS (confirmed by the annotator):")
            lines += [f"- {l['text'] if isinstance(l, dict) else l}" for l in lessons[:12]]
    lines += ["", "TRACK PROPOSALS (numbers on the images, same # as BEV map):"]
    for i, t in enumerate(tracks, 1):
        c = t["center"]
        dist = math.hypot(c[0], c[1])
        moving = "moving" if abs(t.get("speed", 0.0)) > 0.3 else "stationary"
        occl = "occluded" if t.get("occluded") else "clear"
        lines.append(f"#{i} id={t['id']} tool_class={t.get('cls') or t.get('label_name')} "
                     f"size={t['size'][0]:.1f}x{t['size'][1]:.1f}x{t['size'][2]:.1f}m dist={dist:.0f}m "
                     f"points={t.get('points', 0)} {moving} {occl} frames={t.get('start')}-{t.get('end')}")
    lines += ["", 'Return JSON: {"tracks": [{"id": number, "keep": true/false, "label": one of the task labels above, '
              '"heading_hint": "facing_away_from_camera|facing_towards_camera|facing_left|facing_right|unknown", '
              '"confidence": "high|medium|low", "uncertain": true/false, "reason": "short reason"}]}. '
              "Judge every track. Put fixed objects (poles, trees, buildings, parked containers, trash bins) under "
              "keep=false with reason."]
    return "\n".join(lines)


def build_audit_prompt(labels: dict[int, str], conflicts: list[dict]) -> str:
    """Prompt tiếng Anh Pha 2: các ca biên giới/mâu thuẫn cần chốt lại."""
    lines = ["Second-pass QA audit. Geometry checks already ran; re-judge only the borderline cases below.",
             "TASK LABELS: " + ", ".join(f"{n} (id {i})" for i, n in sorted(labels.items()))]
    if conflicts:
        lines.append("")
        lines.append("BORDERLINE / CONFLICTING TRACKS:")
        for c in conflicts[:20]:
            lines.append(f"- id={c['id']} tool_class={c.get('cls')} size={c['size'][0]:.1f}x{c['size'][1]:.1f}x{c['size'][2]:.1f}m "
                         f"dist={math.hypot(c['center'][0], c['center'][1]):.0f}m issue={c.get('issue')} "
                         f"(class candidates: {', '.join(c.get('candidates') or ['?'])})")
    lines += ["", 'Return JSON: {"tracks": [{"id": number, "final_label": one of the task labels, '
              '"bucket": "high_confidence|needs_review|suspected_noise", "reason": "short"}]}']
    return "\n".join(lines)


# ------------------------------------------------------------- gọi LLM ---
def ask_llm(llm, text: str, images: list[bytes], retries: int = 2) -> dict:
    """Gọi Claude với thử lại exponential backoff khi lỗi mạng tạm thời / 429 / 5xx (spec 3D v2 mục 4.2).

    Lỗi JSON hỏng là lỗi cứng: không thử lại, báo ngay."""
    import time as _t

    from .guideline.llm import LLMError, parse_json

    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            raw = llm.complete(SYSTEM, text, images, max_tokens=4000)
            out = parse_json(raw)
            if not isinstance(out, dict):
                raise LLMError("Claude không trả về JSON dạng object.")
            return out
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - lỗi mạng / 429 / 5xx: chờ rồi thử lại
            last = exc
            if attempt < retries:
                _t.sleep(min(2 ** attempt, 4))
    raise LLMError(f"Claude lỗi sau {retries + 1} lần thử: {last}")


def bucket_of(track: dict, phase1: Optional[dict] = None) -> str:
    """3 rổ hiển thị: Tin cậy cao / Cần xem / Nghi rác (ưu tiên dấu hiệu rác > phân vân > tin)."""
    if phase1 is not None:
        if phase1.get("keep") is False:
            return "suspected_noise"
        if phase1.get("uncertain") or str(phase1.get("confidence")) == "low":
            return "needs_review"
    flags = [str(f.get("code") or "") for f in (track.get("flags") or [])]
    if any("LEARNED" in f or "UNCERTAIN" in f for f in flags) or track.get("checked") is False:
        return "needs_review"
    return "high_confidence"


# ----------------------------------------------------------- hai pha ---
def phase1(llm, tracks: list[dict], labels: dict[int, str], autoprofile: dict,
           camera_images: Optional[dict[int, dict[str, Any]]] = None, memory: Optional[dict] = None,
           batch_size: int = MAX_TRACKS_PER_BATCH) -> dict[int, dict]:
    """Pha 1: gửi 6-10 track/lô. camera_images: {track_id: {tên camera: (ảnh RGB, box2d)}} của keyframe rõ nhất.

    Trả {track_id: {keep, label, heading_hint, confidence, uncertain, reason, bucket}}."""
    camera_images = camera_images or {}
    results: dict[int, dict] = {}
    for start in range(0, len(tracks), batch_size):
        group = tracks[start:start + batch_size]
        text = build_track_prompt(group, labels, autoprofile, memory)
        images: list[bytes] = []
        notes: list[str] = []
        bev = bev_image([{"id": t["id"], "center": t["center"], "size": t["size"], "yaw": t.get("yaw", 0.0)} for t in group])
        if bev is not None:
            images.append(_jpeg(bev))
            notes.append("Image 1: top-down LiDAR map (BEV) of this batch, cuboid boxes numbered like the list.")
        for i, t in enumerate(group, 1):
            for name, (img, box) in (camera_images.get(t["id"]) or {}).items():
                if img is None or not getattr(img, "size", None):
                    continue
                if box:
                    marked = marked_image(img, [{"id": 1, "box": box}])
                    images.append(_jpeg(crop_track(marked, box) if marked.size else marked))
                else:
                    images.append(_jpeg(img))
                notes.append(f"Image {len(images)}: track #{i} in camera {name}.")
                break                                   # một ảnh cho mỗi track là đủ cho Pha 1
        if notes:
            text += "\n\nIMAGE NOTES:\n" + "\n".join(notes)
        try:
            res = ask_llm(llm, text, images)
        except Exception:  # noqa: BLE001 - Claude lỗi: track đó thành needs_review, không chặn luồng
            for t in group:
                results[t["id"]] = {"keep": None, "reason": "Claude lỗi: cần người xem", "bucket": "needs_review"}
            continue
        known = {n.lower() for n in labels.values()}
        for entry in res.get("tracks") or []:
            tid = entry.get("id")
            match = next((t for t in group if t["id"] == tid), None)
            if match is None:
                continue
            label = str(entry.get("label") or "")
            if label and label.strip().lower() not in known:
                entry["label"] = None                   # class ngoài danh sách: bỏ, không gán bừa
            entry["bucket"] = bucket_of(match, entry)
            results[match["id"]] = entry
        for t in group:                                 # track Claude bỏ sót: vẫn vào rổ cần xem
            if t["id"] not in results:
                results[t["id"]] = {"keep": None, "reason": "Claude không chấm: cần người xem", "bucket": "needs_review"}
    return results


def phase2(llm, tracks: list[dict], labels: dict[int, str], phase1_results: dict[int, dict],
           rule: Optional[dict] = None) -> dict[int, dict]:
    """Pha 2: kiểm luật bằng code trước, các ca mâu thuẫn gửi Claude chốt. Trả {track_id: bucket + final_label}."""
    rule = rule or {}
    audits: list[dict] = []
    out: dict[int, dict] = {}
    for t in tracks:
        p1 = phase1_results.get(t["id"]) or {}
        bucket = p1.get("bucket") or bucket_of(t)
        final = p1.get("label")
        tool_cls = str(t.get("cls") or "").strip().lower()
        if final and final.strip().lower() not in (tool_cls, "unknown"):
            # Claude đổi class khác tool: đưa vào audit để chốt lại
            audits.append({"id": t["id"], "cls": tool_cls or "?", "size": t["size"], "center": t["center"],
                           "issue": f"tool says {tool_cls or '?'}, Claude says {final}",
                           "candidates": [tool_cls or "?", final]})
        elif bucket == "needs_review" and p1.get("keep") is None:
            audits.append({"id": t["id"], "cls": tool_cls or "?", "size": t["size"], "center": t["center"],
                           "issue": "uncertain class", "candidates": [tool_cls or "?"]})
        out[t["id"]] = dict(p1, bucket=bucket, final_label=final)
    if audits and llm is not None:
        text = build_audit_prompt(labels, audits)
        try:
            res = ask_llm(llm, text, [])
        except Exception:  # noqa: BLE001 - Claude lỗi: giữ kết quả Pha 1
            res = {}
        for entry in res.get("tracks") or []:
            tid = entry.get("id")
            if tid in out and entry.get("bucket") in ("high_confidence", "needs_review", "suspected_noise"):
                out[tid]["bucket"] = entry["bucket"]
                if entry.get("final_label"):
                    out[tid]["final_label"] = entry["final_label"]
    return out


def summarize(phase2_results: dict[int, dict]) -> dict:
    """Đếm 3 rổ cho filter pills của UI."""
    counts = {"high_confidence": 0, "needs_review": 0, "suspected_noise": 0}
    for r in phase2_results.values():
        b = r.get("bucket")
        if b in counts:
            counts[b] += 1
    return counts
