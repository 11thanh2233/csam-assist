"""Tự học từ cách người dùng xử lý đề xuất của tool.

Nguồn học (đều là tín hiệu của NGƯỜI, không phải của chính model):
  1. Lúc ghi: đề xuất được ghi vào CVAT (kèm id shape CVAT trả về) và đề xuất người dùng bỏ tick (bị từ chối).
  2. Lúc đối chiếu: so các shape tool đã ghi với annotation hiện tại của job, sau khi người dùng đã sửa:
       giữ nguyên  -> đúng (TP), hộp gần như không đổi
       sửa hình    -> đúng nhưng lệch: ghi lại độ lệch từng cạnh để học cách chỉnh hộp
       đổi label   -> sai class (FP của label cũ), ghi lại cặp hay nhầm
       bị xoá      -> sai (FP)
       người tự vẽ thêm (không có trong đề xuất, không có sẵn từ trước) -> tool bỏ sót (FN)
  3. Tool tự thấy (rà cả job): lỗi của chính các shape tool đã tạo. Chỉ ghi thành GIẢ THUYẾT để người xem, không tự đổi gì.

Từ đó, cho mỗi (profile, label):
  - ngưỡng tin cậy: chọn ngưỡng cho F1 cao nhất trên các đề xuất đã có kết quả. Chỉ nâng được ngưỡng một cách chắc chắn
    (đề xuất dưới ngưỡng cũ không bao giờ được thấy); khi bỏ sót nhiều mà gần như không sai, hạ thử từng bước nhỏ rồi
    xem lần đối chiếu sau.
  - chỉnh hộp: độ lệch trung bình (theo tỉ lệ chiều rộng/cao) mà người dùng hay kéo các cạnh hộp.
Kết quả áp tự động khi bật "Dùng điều tool đã học" (mặc định bật), luôn thua chỉnh tay của người dùng ở tab Label,
và xem/tắt/xoá được ở tab Tự học.
"""
from __future__ import annotations

import json
import statistics
import threading
import time
from pathlib import Path
from typing import Optional

from . import geometry as G
from .pipeline import shape_bbox
from .profiles import norm

MIN_OUTCOMES = 12           # số đề xuất đã có kết quả tối thiểu trước khi đổi ngưỡng
MIN_BOX_SAMPLES = 10        # số hộp đúng tối thiểu trước khi học cách chỉnh hộp
KEEP_IOU = 0.95             # hộp sau khi người dùng xem vẫn khớp >= mức này: coi là giữ nguyên
MATCH_IOU = 0.5             # nhãn người tự vẽ trùng một đề xuất đã ghi >= mức này: không tính là bỏ sót
STEP_DOWN = 0.05            # bước hạ ngưỡng thử khi bỏ sót nhiều
FLOOR = 0.2                 # không bao giờ tự hạ ngưỡng dưới mức này


def _key(profile_id: str, label: str) -> str:
    return f"{profile_id}::{norm(label)}"


class Learner:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "outcomes").mkdir(exist_ok=True)
        self.model = self._load_json(self.root / "model.json", {"labels": {}, "notes": [], "updated": 0})

    # ------------------------------------------------------------ lưu trữ ---
    @staticmethod
    def _load_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _save_model(self) -> None:
        tmp = self.root / "model.json.tmp"
        tmp.write_text(json.dumps(self.model, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.root / "model.json")

    def _events(self, job_id: Optional[int] = None) -> list[dict]:
        path = self.root / "events.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if job_id is None or event.get("job_id") == job_id:
                out.append(event)
        return out

    # ------------------------------------------------------------ ghi nhận ---
    def record(self, events: list[dict]) -> int:
        """Ghi các sự kiện lúc người dùng bấm Ghi: 'applied' (đã ghi, có cvat_id) và 'rejected' (bị bỏ tick)."""
        keep = []
        now = time.time()
        for e in events:
            if e.get("kind") not in ("applied", "rejected") or not e.get("label_name") or not e.get("profile_id"):
                continue
            keep.append({
                "kind": e["kind"], "at": now, "profile_id": str(e["profile_id"]), "label_name": str(e["label_name"]),
                "label_id": e.get("label_id"), "job_id": e.get("job_id"), "frame": e.get("frame"),
                "type": e.get("type"), "score": float(e["score"]) if e.get("score") is not None else None, "bbox": [float(v) for v in (e.get("bbox") or [])][:4],
                "cvat_id": e.get("cvat_id"), "source": e.get("source", "frame"),
                "existing_ids": [int(i) for i in e.get("existing_ids") or [] if isinstance(i, (int, float))],
                # chỉnh hộp đã học đang áp lên đề xuất này (nếu có): để lần học sau cộng dồn đúng
                "box_adjust": [float(v) for v in e.get("box_adjust") or []][:4] or None,
                # ngưỡng đang dùng lúc đề xuất: đề xuất dưới ngưỡng này chưa bao giờ được người dùng nhìn thấy
                "threshold": float(e["threshold"]) if e.get("threshold") is not None else None,
                # 0.9.4: thuộc tính tool tự điền ({tên: giá trị}) và vị trí từng điểm skeleton ({id điểm: [x, y]}) lúc ghi
                "auto_attributes": {str(k): str(v) for k, v in (e.get("auto_attributes") or {}).items()} or None,
                "points": {str(k): [float(v[0]), float(v[1])] for k, v in (e.get("points") or {}).items() if len(v or []) >= 2} or None,
                "track_shapes": int(e["track_shapes"]) if e.get("track_shapes") is not None else None,
            })
        if keep:
            with self.lock, (self.root / "events.jsonl").open("a", encoding="utf-8") as f:
                for e in keep:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return len(keep)

    # ------------------------------------------------------------ đối chiếu -
    def tool_shapes(self, job_id: int) -> dict:
        """Nhãn tool đã ghi cho job: id CVAT, và những nhãn chưa ai sửa (lần đối chiếu gần nhất là 'giữ nguyên' hoặc chưa đối chiếu)."""
        applied = [e for e in self._events(job_id) if e["kind"] == "applied" and e.get("cvat_id") is not None]
        data = self._load_json(self.root / "outcomes" / f"{int(job_id)}.json", {})
        result = {o.get("cvat_id"): o.get("result") for o in data.get("outcomes", []) if o.get("cvat_id") is not None}
        ids = [e["cvat_id"] for e in applied]
        untouched = [i for i in ids if result.get(i) in (None, "kept")]
        return {"job_id": job_id, "ids": ids, "untouched": untouched, "reviewed_at": data.get("at")}

    def review(self, job_id: int, shapes: list[dict], tracks: Optional[list[dict]] = None,
               attr_names: Optional[dict] = None) -> dict:
        """So các shape (và track, 0.9.4) tool đã ghi cho job với annotation hiện tại; ghi kết quả rồi học lại.

        attr_names: {spec_id: tên attribute} để so giá trị thuộc tính tool tự điền với giá trị bạn để lại.
        """
        events = self._events(job_id)
        if not events:
            return {"job_id": job_id, "reviewed": 0, "summary": {}, "learned": self.summary()}
        by_id = {s.get("id"): s for s in shapes if s.get("id") is not None}
        track_ids = {t.get("id"): t for t in tracks or [] if t.get("id") is not None}
        attr_names = {str(k): v for k, v in (attr_names or {}).items()}
        applied = [e for e in events if e["kind"] == "applied" and e.get("cvat_id") is not None]
        tool_ids = {e["cvat_id"] for e in applied}
        outcomes = []
        for e in applied:
            out = {"profile_id": e["profile_id"], "label_name": e["label_name"], "score": e["score"], "frame": e["frame"],
                   "type": e["type"], "cvat_id": e["cvat_id"], "box_adjust": e.get("box_adjust"),
                   "threshold": e.get("threshold")}
            if e.get("type") == "track":
                # Track: bị xoá = sai; số keyframe thay đổi = bạn đã sửa track (thêm, bớt keyframe, đổi occluded/outside)
                tr = track_ids.get(e["cvat_id"])
                if tr is None:
                    out["result"] = "deleted"
                elif tr.get("label_id") != e.get("label_id"):
                    out["result"], out["new_label_id"] = "relabeled", tr.get("label_id")
                else:
                    changed = len(tr.get("shapes") or []) != (e.get("track_shapes") or 0)
                    out["result"] = "edited" if changed else "kept"
                outcomes.append(out)
                continue
            now = by_id.get(e["cvat_id"])
            if now is None:
                out["result"] = "deleted"
            elif now.get("label_id") != e.get("label_id"):
                out["result"], out["new_label_id"] = "relabeled", now.get("label_id")
            else:
                after = shape_bbox(now)
                before = e["bbox"]
                if after and len(before) == 4 and G.box_iou(after, before) < KEEP_IOU:
                    out["result"] = "edited"
                    w, h = max(before[2] - before[0], 1.0), max(before[3] - before[1], 1.0)
                    out["delta"] = [(after[0] - before[0]) / w, (after[1] - before[1]) / h,
                                    (after[2] - before[2]) / w, (after[3] - before[3]) / h]
                else:
                    out["result"], out["delta"] = "kept", [0.0, 0.0, 0.0, 0.0]
                # Thuộc tính tool tự điền: bạn để nguyên hay đổi
                if e.get("auto_attributes") and attr_names:
                    have = {norm(attr_names.get(str(a.get("spec_id")), "")): str(a.get("value")) for a in now.get("attributes") or []}
                    out["attr_changed"] = {k: have.get(norm(k)) not in (None, v) for k, v in e["auto_attributes"].items() if norm(k) in have}
                # Skeleton: độ lệch từng điểm (theo cỡ hộp skeleton) giữa lúc tool ghi và lúc bạn sửa xong
                if e.get("points") and now.get("type") == "skeleton" and len(before) == 4:
                    w, h = max(before[2] - before[0], 1.0), max(before[3] - before[1], 1.0)
                    moved = {}
                    for el in now.get("elements") or []:
                        p0 = e["points"].get(str(el.get("label_id")))
                        p1 = el.get("points") or []
                        if p0 and len(p1) >= 2 and not el.get("outside"):
                            moved[str(el["label_id"])] = [(p1[0] - p0[0]) / w, (p1[1] - p0[1]) / h]
                    if moved:
                        out["point_delta"] = moved
            outcomes.append(out)
        for e in events:
            if e["kind"] == "rejected":
                outcomes.append({"profile_id": e["profile_id"], "label_name": e["label_name"], "score": e["score"],
                                 "frame": e["frame"], "type": e["type"], "result": "rejected", "threshold": e.get("threshold")})
        # Bỏ sót: nhãn người dùng tự vẽ thêm trên các frame tool đã gợi ý (không phải nhãn có sẵn từ trước, không trùng đề xuất).
        frames: dict[int, dict] = {}
        for e in events:
            if e.get("frame") is None:
                continue
            info = frames.setdefault(e["frame"], {"existing": set(), "profile_id": e["profile_id"], "labels": {}, "boxes": []})
            info["existing"].update(e.get("existing_ids") or [])
            if e.get("label_id") is not None:
                info["labels"][e["label_id"]] = e["label_name"]
            if e["kind"] == "applied" and e.get("bbox"):
                info["boxes"].append((e.get("label_id"), e["bbox"]))
        for s in shapes:
            info = frames.get(s.get("frame"))
            if not info or s.get("id") in tool_ids or s.get("id") in info["existing"] or s.get("label_id") not in info["labels"]:
                continue
            box = shape_bbox(s)
            if box and any(lid == s.get("label_id") and G.box_iou(box, b) >= MATCH_IOU for lid, b in info["boxes"]):
                continue
            outcomes.append({"profile_id": info["profile_id"], "label_name": info["labels"][s["label_id"]], "score": None,
                             "frame": s.get("frame"), "type": s.get("type"), "result": "missed"})
        # Cặp hay nhầm: label đề xuất -> label người dùng đổi thành
        names = {e.get("label_id"): e["label_name"] for e in events if e.get("label_id") is not None}
        for o in outcomes:
            if o["result"] == "relabeled":
                o["new_label"] = names.get(o.get("new_label_id"), str(o.get("new_label_id")))
        with self.lock:
            (self.root / "outcomes" / f"{int(job_id)}.json").write_text(
                json.dumps({"job_id": job_id, "at": time.time(), "outcomes": outcomes}, ensure_ascii=False), encoding="utf-8")
        learned = self.relearn()
        summary: dict[str, dict] = {}
        for o in outcomes:
            row = summary.setdefault(o["label_name"], {"kept": 0, "edited": 0, "relabeled": 0, "deleted": 0, "rejected": 0, "missed": 0})
            row[o["result"]] += 1
        return {"job_id": job_id, "reviewed": len(outcomes), "summary": summary, "learned": learned}

    # ------------------------------------------------------------- học ------
    def _all_outcomes(self) -> list[dict]:
        out = []
        for path in (self.root / "outcomes").glob("*.json"):
            out += self._load_json(path, {}).get("outcomes", [])
        return out

    @staticmethod
    def best_threshold(tp: list[float], fp: list[float], fn: int, current: float) -> tuple[float, float, float]:
        """Ngưỡng cho F1 cao nhất trên các đề xuất đã thấy. Trả về (ngưỡng, F1 ở ngưỡng hiện tại, F1 ở ngưỡng mới)."""
        positives = len(tp) + fn

        def f1(t: float) -> float:
            hit = sum(1 for s in tp if s >= t)
            wrong = sum(1 for s in fp if s >= t)
            if not hit:
                return 0.0
            precision, recall = hit / (hit + wrong), hit / max(positives, 1)
            return 2 * precision * recall / (precision + recall)

        # Ứng viên: điểm giữa hai điểm số liền nhau (ngưỡng nằm giữa đề xuất đúng thấp nhất và đề xuất sai cao nhất)
        seen = sorted({s for s in tp + fp if s >= current})
        candidates = sorted({current} | {round((a + b) / 2, 3) for a, b in zip(seen, seen[1:])})
        best = max(candidates, key=lambda t: (round(f1(t), 4), -t))
        return best, f1(current), f1(best)

    def relearn(self) -> dict:
        groups: dict[str, list[dict]] = {}
        for o in self._all_outcomes():
            groups.setdefault(_key(o["profile_id"], o["label_name"]), []).append(o)
        previous = self.model.get("labels", {})
        labels, notes = {}, []
        for key, items in groups.items():
            prev = previous.get(key, {})
            profile_id, label = key.split("::", 1)
            name = items[0]["label_name"]
            count = {r: sum(1 for o in items if o["result"] == r) for r in ("kept", "edited", "relabeled", "deleted", "rejected", "missed")}
            tp = [o["score"] for o in items if o["result"] in ("kept", "edited") and o["score"] is not None]
            fp = [o["score"] for o in items if o["result"] in ("deleted", "relabeled", "rejected") and o["score"] is not None]
            fn = count["missed"]
            precision = len(tp) / max(len(tp) + len(fp), 1)
            recall = len(tp) / max(len(tp) + fn, 1)
            # Ngưỡng thấp nhất từng dùng: dưới mức đó tool chưa từng cho người dùng xem đề xuất nào, nên không học được.
            used = [o["threshold"] for o in items if o.get("threshold") is not None]
            scores = tp + fp
            base = round(min(used), 3) if used else (round(min(scores), 2) if scores else None)
            entry = {"profile_id": profile_id, "label": name, "counts": count, "precision": round(precision, 3),
                     "recall": round(recall, 3), "n": len(tp) + len(fp) + fn,
                     "enabled": prev.get("enabled", True), "base_threshold": base}
            if len(scores) + fn >= MIN_OUTCOMES and base is not None:
                t, before, after = self.best_threshold(tp, fp, fn, base)
                reason = ""
                if after - before >= 0.02 and t > base:
                    reason = f"nâng ngưỡng: F1 {before:.2f} -> {after:.2f} trên {len(scores) + fn} đề xuất đã có kết quả"
                elif recall < 0.7 and precision >= 0.9 and fn >= 3:
                    # Hạ từng bước, và chỉ hạ tiếp khi đã có thêm kết quả mới kể từ lần hạ trước
                    # (đối chiếu lại cùng dữ liệu nhiều lần không được làm ngưỡng tụt dần).
                    stepped = prev.get("stepped_at_n")
                    if stepped is None or entry["n"] >= stepped + 5:
                        t = max(FLOOR, round(min(prev.get("threshold", base), base) - STEP_DOWN, 2))
                        entry["stepped_at_n"] = entry["n"]
                    else:
                        t, entry["stepped_at_n"] = prev.get("threshold", base), stepped
                    reason = (f"hạ thử ngưỡng: tool bỏ sót {fn} nhãn (recall {recall:.2f}) nhưng gần như không sai "
                              f"(precision {precision:.2f}); xem lại sau lần đối chiếu tới")
                if reason and abs(t - base) >= 0.01:
                    entry["threshold"], entry["threshold_reason"] = round(t, 3), reason
            # Chỉnh hộp: độ lệch cạnh mà người dùng muốn = phần tool đã tự chỉnh lúc đề xuất + phần người dùng còn kéo thêm.
            # Lấy trung bình trên mọi hộp đúng (hộp giữ nguyên tính là không cần kéo), để không chỉnh quá tay.
            boxes = [o for o in items if o["result"] in ("kept", "edited") and o.get("type") == "rectangle" and o.get("delta")]
            if len(boxes) >= MIN_BOX_SAMPLES:
                wanted = [[(o.get("box_adjust") or [0, 0, 0, 0])[i] + o["delta"][i] for i in range(4)] for o in boxes]
                mean = [statistics.fmean(v[i] for v in wanted) for i in range(4)]
                edited = sum(1 for o in boxes if o["result"] == "edited")
                if max(abs(v) for v in mean) >= 0.01 and (edited >= max(4, len(boxes) * 0.3) or any(o.get("box_adjust") for o in boxes)):
                    entry["box_adjust"] = [round(v, 4) for v in mean]
                    entry["box_reason"] = (f"từ {len(boxes)} hộp đúng ({edited} hộp bạn kéo lại): cạnh trái/trên/phải/dưới chỉnh "
                                           + ", ".join(f"{v * 100:+.1f}%" for v in mean) + " theo kích thước hộp")
            # 0.9.4: điểm skeleton bạn hay kéo về cùng một hướng -> tool tự dịch điểm đó lần sau
            per_point: dict[str, list[list[float]]] = {}
            for o in items:
                for pid, d in (o.get("point_delta") or {}).items():
                    per_point.setdefault(pid, []).append(d)
            adjust = {}
            for pid, ds in per_point.items():
                if len(ds) >= MIN_BOX_SAMPLES:
                    mx, my = statistics.fmean(d[0] for d in ds), statistics.fmean(d[1] for d in ds)
                    if max(abs(mx), abs(my)) >= 0.01:
                        adjust[pid] = [round(mx, 4), round(my, 4)]
            if adjust:
                entry["point_adjust"] = adjust
                entry["point_reason"] = f"từ {max(len(v) for v in per_point.values())} skeleton bạn đã sửa: dịch {len(adjust)} điểm hay bị kéo lệch"
            # 0.9.4: thuộc tính tool tự điền mà bạn thường đổi -> tắt tự điền thuộc tính đó
            per_attr: dict[str, list[bool]] = {}
            for o in items:
                for a, changed in (o.get("attr_changed") or {}).items():
                    per_attr.setdefault(a, []).append(bool(changed))
            off = sorted(a for a, v in per_attr.items() if len(v) >= MIN_BOX_SAMPLES and sum(v) / len(v) >= 0.5)
            if off:
                entry["attr_off"] = off
                entry["attr_reason"] = "bạn đổi giá trị tool tự điền từ một nửa số lần trở lên: " + ", ".join(off)
            tracks_n = [o for o in items if o.get("type") == "track"]
            if tracks_n:
                entry["tracks"] = {r: sum(1 for o in tracks_n if o["result"] == r) for r in ("kept", "edited", "deleted", "relabeled")}
            confusions: dict[str, int] = {}
            for o in items:
                if o["result"] == "relabeled":
                    confusions[o.get("new_label", "?")] = confusions.get(o.get("new_label", "?"), 0) + 1
            for other, n in confusions.items():
                if n >= 3 and n >= 0.2 * max(len(scores), 1):
                    notes.append({"kind": "confusion", "profile_id": profile_id, "label": name,
                                  "text": f"'{name}' hay bị bạn đổi thành '{other}' ({n} lần): thêm prompt phân biệt hoặc tách ngưỡng hai label."})
            if fn >= 5 and recall < 0.5:
                notes.append({"kind": "missed", "profile_id": profile_id, "label": name,
                              "text": f"Tool bỏ sót nhiều '{name}' (recall {recall:.2f}, {fn} nhãn bạn tự vẽ): thử thêm prompt khác ở tab Label."})
            labels[key] = entry
        notes += [n for n in self.model.get("notes", []) if n.get("kind") == "tool"]   # giả thuyết tool tự thấy, giữ lại
        with self.lock:
            self.model = {"labels": labels, "notes": notes, "updated": time.time()}
            self._save_model()
        return self.summary()

    def tool_findings(self, profile_id: str, per_label: dict[str, dict[str, int]]) -> None:
        """Rà cả job: lỗi trên các shape do tool tạo. Chỉ là giả thuyết để người xem, không đổi ngưỡng hay hộp."""
        notes = [n for n in self.model.get("notes", []) if not (n.get("kind") == "tool" and n.get("profile_id") == profile_id)]
        for label, codes in per_label.items():
            total = sum(codes.values())
            if total >= 3:
                worst = max(codes, key=codes.get)
                notes.append({"kind": "tool", "profile_id": profile_id, "label": label,
                              "text": f"Rà cả job: {total} lỗi trên nhãn '{label}' do tool tạo, nhiều nhất là {worst} ({codes[worst]}). "
                                      "Giả thuyết, chưa đổi gì: xem lại prompt/luật của label này."})
        with self.lock:
            self.model["notes"] = notes
            self._save_model()

    # ------------------------------------------------------------- dùng -----
    def summary(self) -> dict:
        return {"labels": list(self.model.get("labels", {}).values()), "notes": self.model.get("notes", []),
                "updated": self.model.get("updated", 0)}

    def for_profile(self, profile_id: str) -> dict[str, dict]:
        return {norm(e["label"]): e for e in self.model.get("labels", {}).values()
                if e["profile_id"] == profile_id and e.get("enabled", True)
                and (e.get("threshold") or e.get("box_adjust") or e.get("point_adjust") or e.get("attr_off"))}

    def set_enabled(self, profile_id: str, label: str, enabled: bool) -> None:
        key = _key(profile_id, label)
        with self.lock:
            if key in self.model.get("labels", {}):
                self.model["labels"][key]["enabled"] = bool(enabled)
                self._save_model()

    def forget(self, profile_id: Optional[str] = None, label: Optional[str] = None) -> int:
        """Xoá những gì đã học (và kết quả đối chiếu tương ứng) cho một label, một profile, hoặc tất cả."""
        removed = 0
        with self.lock:
            for path in (self.root / "outcomes").glob("*.json"):
                data = self._load_json(path, {})
                kept = [o for o in data.get("outcomes", []) if not ((profile_id is None or o["profile_id"] == profile_id)
                                                                    and (label is None or norm(o["label_name"]) == norm(label)))]
                removed += len(data.get("outcomes", [])) - len(kept)
                data["outcomes"] = kept
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            events = self.root / "events.jsonl"
            if events.exists():
                lines = [l for l in events.read_text(encoding="utf-8").splitlines() if l.strip()]
                keep = []
                for l in lines:
                    try:
                        e = json.loads(l)
                    except ValueError:
                        continue
                    if not ((profile_id is None or e["profile_id"] == profile_id) and (label is None or norm(e["label_name"]) == norm(label))):
                        keep.append(l)
                events.write_text("".join(x + "\n" for x in keep), encoding="utf-8")
        self.relearn()
        return removed


def apply_learned(cfgs: list[dict], learned: dict[str, dict], overrides: dict) -> None:
    """Áp điều đã học vào cấu hình label. Chỉnh tay ở tab Label (overrides) luôn thắng."""
    for cfg in cfgs:
        entry = learned.get(norm(cfg["name"]))
        if not entry:
            continue
        own = overrides.get(str(cfg["label_id"])) or {}
        info = {"n": entry["n"], "precision": entry["precision"], "recall": entry["recall"]}
        if entry.get("threshold") and own.get("threshold") is None:
            info["threshold_from"] = cfg["threshold"]
            cfg["threshold"] = float(entry["threshold"])
            info["threshold"], info["threshold_reason"] = cfg["threshold"], entry.get("threshold_reason", "")
        if entry.get("box_adjust") and cfg.get("shape") == "rectangle":
            cfg["box_adjust"] = entry["box_adjust"]
            info["box_adjust"], info["box_reason"] = entry["box_adjust"], entry.get("box_reason", "")
        if entry.get("point_adjust") and cfg.get("shape") == "skeleton":
            cfg["point_adjust"] = entry["point_adjust"]
            info["point_reason"] = entry.get("point_reason", "")
        if entry.get("attr_off"):
            cfg["attr_off"] = list(entry["attr_off"])
            for a in entry["attr_off"]:
                (cfg.get("attr_rules") or {}).pop(a, None)
            info["attr_reason"] = entry.get("attr_reason", "")
        if len(info) > 3:
            cfg["learned"] = info
