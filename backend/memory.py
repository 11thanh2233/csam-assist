"""Trí nhớ của từng project (0.9.5): tool học theo cách BẠN chốt nhãn, không theo luật viết sẵn cho từng label.

Mỗi lần bạn bấm Ghi, panel gửi giá trị cuối cùng bạn để lại (thuộc tính, tag, occluded, đề xuất bị bỏ) kèm giá trị
tool đã gợi ý. Từ đó tool học bốn thứ, riêng cho từng profile (mỗi project một profile):

  1. Thống kê giá trị: thuộc tính nào của label nào bạn hay chọn giá trị gì (dùng khi không có Claude).
  2. Tên file: phần chữ trong tên file (vd `MVL` trong `B05_05780_MVL.png`) gắn với giá trị tag nào. Khi một phần chữ
     luôn đi với một giá trị (đủ số lần, gần như không ngoại lệ), tool dùng nó cho các ảnh sau.
  3. Các lần bạn sửa: đưa cho Claude làm ví dụ ở các lần xem ảnh sau.
  4. Bài học: khi đủ nhiều lần sửa, Claude tóm thành vài câu luật cho project. Bạn xem và xoá được ở tab Tự học.
Không lưu ảnh. Mọi thứ nằm trong thư mục dữ liệu của app trên máy bạn.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from .profiles import norm

TOKEN_MIN = 2          # số lần tối thiểu một phần chữ của tên file đi với cùng một giá trị
TOKEN_PURITY = 0.9     # tỉ lệ tối thiểu
LESSON_EVERY = 15      # đủ chừng này lần sửa mới thì gợi ý tóm bài học
MAX_CORRECTIONS = 400


def name_tokens(filename: str) -> list[str]:
    """Phần chữ có nghĩa trong tên file: `B05_05780_MVL.png` -> ['b05', 'mvl']. Bỏ phần chỉ có số (số thứ tự frame)."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", Path(str(filename or "")).name)
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", stem) if p]
    return sorted({p.lower() for p in parts if not p.isdigit() and len(p) <= 24})


def sequence_key(filename: str) -> str:
    """Chuỗi ảnh: tên file bỏ số thứ tự frame. `B05_05780_MVL` -> `b05_#_mvl`."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", Path(str(filename or "")).name).lower()
    return re.sub(r"\d{3,}", "#", stem)


class ProjectMemory:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    # ------------------------------------------------------------ lưu trữ ---
    def _path(self, profile_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(profile_id))[:80] or "default"
        return self.root / f"{safe}.json"

    def load(self, profile_id: str) -> dict:
        p = self._path(profile_id)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("values", {})        # "label|attr" -> {value: count}
        data.setdefault("tokens", {})        # "tag|attr" -> {token: {value: count}}
        data.setdefault("sequences", {})     # "tag|attr" -> {sequence: value}
        data.setdefault("corrections", [])   # [{label, field, tool, you, where, at}]
        data.setdefault("lessons", [])       # [{id, text, at, from}]
        data.setdefault("since_lesson", 0)
        return data

    def _save(self, profile_id: str, data: dict) -> None:
        data["corrections"] = data["corrections"][-MAX_CORRECTIONS:]
        tmp = self._path(profile_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(profile_id))

    # ------------------------------------------------------------- ghi nhận ---
    def record(self, profile_id: str, items: list[dict]) -> int:
        """items: [{label, kind: object|tag, final: {attr: value}, tool: {attr: value}, occluded?, tool_occluded?,
        rejected?, reason?, filename?, where?}]. Trả số lần sửa mới."""
        if not items:
            return 0
        new = 0
        with self.lock:
            data = self.load(profile_id)
            for it in items:
                label = str(it.get("label") or "")
                if not label:
                    continue
                final = {str(k): str(v) for k, v in (it.get("final") or {}).items() if v not in (None, "")}
                tool = {str(k): str(v) for k, v in (it.get("tool") or {}).items() if v not in (None, "")}
                where = str(it.get("where") or "")[:160]
                if it.get("rejected"):
                    data["corrections"].append({"label": label, "field": "_keep", "tool": "keep", "you": "drop",
                                                "where": where, "why": str(it.get("reason") or "")[:160], "at": time.time()})
                    new += 1
                    continue
                for attr, value in final.items():
                    key = f"{norm(label)}|{norm(attr)}"
                    counts = data["values"].setdefault(key, {})
                    counts[value] = counts.get(value, 0) + 1
                    if it.get("kind") == "tag" and it.get("filename"):
                        toks = data["tokens"].setdefault(key, {})
                        for t in name_tokens(it["filename"]):
                            per = toks.setdefault(t, {})
                            per[value] = per.get(value, 0) + 1
                        data["sequences"].setdefault(key, {})[sequence_key(it["filename"])] = value
                    if attr in tool and tool[attr] != value:
                        data["corrections"].append({"label": label, "field": attr, "tool": tool[attr], "you": value,
                                                    "where": where, "file": str(it.get("filename") or "")[:80], "at": time.time()})
                        new += 1
                if it.get("tool_occluded") is not None and it.get("occluded") is not None \
                        and bool(it["tool_occluded"]) != bool(it["occluded"]):
                    data["corrections"].append({"label": label, "field": "occluded", "tool": str(bool(it["tool_occluded"])).lower(),
                                                "you": str(bool(it["occluded"])).lower(), "where": where, "at": time.time()})
                    new += 1
            data["since_lesson"] = int(data.get("since_lesson", 0)) + new
            self._save(profile_id, data)
        return new

    # ----------------------------------------------------------- dùng lại ---
    def token_guess(self, profile_id: str, label: str, attr: str, filename: str, data: Optional[dict] = None) -> Optional[tuple[str, str]]:
        """(giá trị, phần chữ) nếu một phần chữ trong tên file luôn đi với một giá trị; hoặc theo chuỗi ảnh đã chốt."""
        data = data or self.load(profile_id)
        key = f"{norm(label)}|{norm(attr)}"
        best = None
        for t in name_tokens(filename):
            per = (data["tokens"].get(key) or {}).get(t) or {}
            total = sum(per.values())
            if not total:
                continue
            value, count = max(per.items(), key=lambda kv: kv[1])
            if count >= TOKEN_MIN and count / total >= TOKEN_PURITY:
                # phần chữ phân biệt được nhiều giá trị hơn (vd MVL) đáng tin hơn phần chữ chung (vd tên chuỗi)
                other_values = {v for tok, pv in (data["tokens"].get(key) or {}).items() if tok != t for v in pv}
                rank = (count / total, len(other_values), count)
                if best is None or rank > best[0]:
                    best = (rank, value, t)
        if best:
            return best[1], best[2]
        seq = (data["sequences"].get(key) or {}).get(sequence_key(filename))
        return (seq, "sequence") if seq else None

    def prior(self, profile_id: str, label: str, attr: str, data: Optional[dict] = None) -> Optional[tuple[str, float]]:
        data = data or self.load(profile_id)
        counts = data["values"].get(f"{norm(label)}|{norm(attr)}") or {}
        total = sum(counts.values())
        if total < 3:
            return None
        value, count = max(counts.items(), key=lambda kv: kv[1])
        return value, count / total

    def examples(self, profile_id: str, labels: list[str], limit: int = 24, data: Optional[dict] = None) -> list[dict]:
        """Các lần sửa gần nhất của những label đang có trên frame: ví dụ cho Claude."""
        data = data or self.load(profile_id)
        want = {norm(l) for l in labels}
        picked, seen = [], Counter()
        for c in reversed(data["corrections"]):
            k = (norm(c["label"]), c["field"])
            if norm(c["label"]) in want and seen[k] < 4:
                picked.append(c)
                seen[k] += 1
            if len(picked) >= limit:
                break
        return list(reversed(picked))

    def lessons(self, profile_id: str) -> list[dict]:
        return self.load(profile_id)["lessons"]

    def add_lessons(self, profile_id: str, texts: list[str], source: str = "claude") -> list[dict]:
        with self.lock:
            data = self.load(profile_id)
            have = {norm(l["text"]) for l in data["lessons"]}
            for t in texts:
                t = " ".join(str(t).split())[:300]
                if t and norm(t) not in have:
                    data["lessons"].append({"id": f"l{int(time.time() * 1000) % 10**10}{len(data['lessons'])}", "text": t,
                                            "at": time.time(), "from": source})
                    have.add(norm(t))
            data["lessons"] = data["lessons"][-40:]
            data["since_lesson"] = 0
            self._save(profile_id, data)
            return data["lessons"]

    def delete_lesson(self, profile_id: str, lesson_id: str) -> list[dict]:
        with self.lock:
            data = self.load(profile_id)
            data["lessons"] = [l for l in data["lessons"] if l["id"] != lesson_id]
            self._save(profile_id, data)
            return data["lessons"]

    def summary(self, profile_id: str) -> dict:
        data = self.load(profile_id)
        rules = []
        for key, toks in data["tokens"].items():
            label, attr = key.split("|", 1)
            for t, per in toks.items():
                total = sum(per.values())
                value, count = max(per.items(), key=lambda kv: kv[1])
                if count >= TOKEN_MIN and count / total >= TOKEN_PURITY:
                    rules.append({"label": label, "attr": attr, "token": t, "value": value, "count": count})
        return {"corrections": len(data["corrections"]), "since_lesson": data.get("since_lesson", 0),
                "lessons": data["lessons"], "file_rules": sorted(rules, key=lambda r: -r["count"])[:30],
                "suggest_lessons": data.get("since_lesson", 0) >= LESSON_EVERY}

    # ------------------------------------------------- cuboid 3D (0.9.6) ---
    def record_lidar(self, profile_id: str, dropped: list[dict], kept: list[dict]) -> dict:
        """Cuboid bạn bỏ khi ghi (thùng rác, nhà vệ sinh di động... bị đoán là xe): nhớ class + cỡ. Cuboid bạn giữ
        cùng cỡ làm giảm số lần đếm (để không bỏ nhầm vật thật)."""
        with self.lock:
            data = self.load(profile_id)
            items = data.setdefault("lidar_drops", [])

            def same(a, b):
                return a.get("cls") == b.get("cls") and all(abs(x - y) <= 0.1 * max(y, 0.3) for x, y in zip(a["size"], b["size"]))

            for d in dropped or []:
                if not d.get("cls") or len(d.get("size") or []) != 3:
                    continue
                hit = next((x for x in items if same(x, d)), None)
                if hit:
                    hit["count"] += 1
                else:
                    items.append({"cls": d["cls"], "size": [round(float(v), 2) for v in d["size"]], "count": 1, "at": time.time()})
            for k in kept or []:
                for x in items:
                    if len(k.get("size") or []) == 3 and same(x, k):
                        x["count"] = max(0, x["count"] - 1)
            data["lidar_drops"] = [x for x in items if x["count"] > 0][-200:]
            self._save(profile_id, data)
            return {"drops": len(data["lidar_drops"])}

    def lidar_drops(self, profile_id: str) -> list[dict]:
        return self.load(profile_id).get("lidar_drops") or []

    def clear_lidar(self, profile_id: str) -> None:
        with self.lock:
            data = self.load(profile_id)
            data["lidar_drops"] = []
            self._save(profile_id, data)

    # --------------------------------------------------------- tóm bài học ---
    def distill(self, profile_id: str, llm, guide_notes: str = "") -> list[dict]:
        data = self.load(profile_id)
        recent = data["corrections"][-120:]
        if not recent:
            return data["lessons"]
        lines = [f"- {c['label']}.{c['field']}: tool '{c['tool']}' -> annotator '{c['you']}'"
                 + (f" ({c['where']})" if c.get("where") else "") + (f" [file {c['file']}]" if c.get("file") else "")
                 + (f" reason: {c['why']}" if c.get("why") else "") for c in recent]
        system = ("You help a labelling tool learn how its annotator finalises labels. From the list of corrections the "
                  "annotator made to the tool's output, write at most 8 short, specific, checkable rules, in Vietnamese. "
                  "Only write a rule supported by at least 2 corrections. Do not repeat existing rules.")
        text = ("EXISTING RULES:\n" + "\n".join(f"- {l['text']}" for l in data["lessons"]) + "\n\nGUIDELINE NOTES:\n"
                + guide_notes[:3000] + "\n\nCORRECTIONS:\n" + "\n".join(lines)
                + '\n\nReturn ONLY JSON: {"lessons": ["rule 1", "rule 2"]}')
        from .guideline.llm import parse_json

        out = parse_json(llm.complete(system, text, max_tokens=1200))
        return self.add_lessons(profile_id, [str(x) for x in (out.get("lessons") or [])][:8])
