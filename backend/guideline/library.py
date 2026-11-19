"""Thư viện guideline: guideline mẫu, kiến thức rút từ chúng, và các BÀI HỌC tool tích luỹ qua từng lần soạn.

Bài học có ba dạng, đều là văn bản đọc được (không huấn luyện lại model):
  - trả lời câu hỏi  (question + answer): lần sau câu hỏi cùng loại được điền sẵn;
  - sửa một luật     (rule + text):       lần sau luật đó dùng lời của người duyệt, hoặc bị bỏ nếu text rỗng;
  - ghi chú tự do    (text [+ section]):  lần sau được đưa vào đúng mục, đánh dấu "từ lần trước".

Chống tự củng cố sai: chỉ bài học có nguồn từ NGƯỜI (duyệt, trả lời, phản hồi) mới ở trạng thái
`confirmed` và được tự áp dụng. Điều tool tự suy ra (tự kiểm tra) là `hypothesis`: chỉ được nêu
thành câu hỏi, không tự thành luật. Mọi bài học đều tắt được.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

import yaml

RULE_TAG = re.compile(r"\s*<!--r:([^>]+?)-->\s*$")


def norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(text).strip().lower())


def strip_tag(line: str) -> tuple[str, Optional[str]]:
    """Tách mã luật ẩn ở cuối dòng: 'text <!--r:bbox.tight-->' -> ('text', 'bbox.tight')."""
    m = RULE_TAG.search(line)
    return (line[: m.start()].rstrip(), m.group(1)) if m else (line.rstrip(), None)


class Library:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.runs_dir = self.root / "runs"
        self.lessons_file = self.root / "lessons.jsonl"
        self.reload()

    # ------------------------------------------------------------ nạp ------
    def reload(self) -> None:
        path = self.root / "knowledge.yaml"
        self.knowledge = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}) or {}
        self.definitions = {norm(k): v for k, v in (self.knowledge.get("definitions") or {}).items()}
        # Guideline người dùng nạp vào (tab Guideline > Nạp guideline): định nghĩa của nó thắng định nghĩa mẫu cùng tên,
        # vì đó là guideline của bài đang làm. Nạp sau thắng nạp trước.
        self.imported: list[dict] = []
        for path in sorted((self.root / "imported").glob("*.yaml"), key=lambda p: p.stat().st_mtime):
            try:
                record = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            self.imported.append(record)
            for label in record.get("labels") or []:
                if label.get("definition"):
                    self.definitions[norm(label["name"])] = label["definition"]
        self.pairs = self.knowledge.get("pairs") or []
        self.lessons: dict[str, dict] = {}
        for seed in self.knowledge.get("seed_lessons") or []:
            # Mã theo NỘI DUNG, không theo vị trí: thêm bài học vào giữa danh sách không làm lệch mã của các bài khác
            # (mã lệch thì việc "tắt bài học" người dùng đã làm sẽ rơi nhầm sang bài khác).
            key = json.dumps([seed.get("question"), seed.get("answer"), seed.get("text")], ensure_ascii=False)
            lesson = dict(seed, id="seed-" + hashlib.sha1(key.encode()).hexdigest()[:8], source="guideline mẫu",
                          status="confirmed", uses=0, created=0)
            lesson.setdefault("tags", ["any"])
            self.lessons[lesson["id"]] = lesson
        if self.lessons_file.exists():
            for line in self.lessons_file.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("id") in self.lessons:  # bản ghi sau ghi đè bản trước (nhật ký chỉ nối thêm)
                    self.lessons[record["id"]].update(record)
                elif "text" in record:
                    self.lessons[record["id"]] = record

    def references(self, max_chars: int = 12000) -> list[tuple[str, str]]:
        # `retired_references`: guideline đã thôi dùng. File cũ có thể còn sót trong thư mục sau khi cập nhật đè.
        retired = set(self.knowledge.get("retired_references") or [])
        out = []
        for path in sorted((self.root / "reference").glob("*.md")):
            if path.stem not in retired:
                out.append((path.stem, path.read_text(encoding="utf-8")[:max_chars]))
        return out

    def definition(self, name: str) -> Optional[str]:
        return self.definitions.get(norm(name).split("/")[-1]) or self.definitions.get(norm(name))

    def pair_rule(self, a: str, b: str) -> Optional[str]:
        key = {norm(a), norm(b)}
        for item in self.pairs:
            if {norm(item["a"]), norm(item["b"])} == key:
                return item["rule"]
        return None

    # --------------------------------------------------------- bài học -----
    def _append(self, record: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lessons_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def add_lesson(self, text: str, tags: list[str], source: str, status: str = "confirmed", **extra) -> dict:
        text = " ".join(str(text).split())[:600]
        key = json.dumps([text, extra.get("question"), extra.get("answer"), extra.get("rule"), sorted(tags)], ensure_ascii=False)
        lesson_id = "L" + hashlib.sha1(key.encode()).hexdigest()[:8]
        with self.lock:
            existing = self.lessons.get(lesson_id)
            if existing:
                existing["uses"] = existing.get("uses", 0) + 1
                existing["created"] = time.time()
                if status == "confirmed":
                    existing["status"] = "confirmed" if existing.get("status") != "disabled" else "disabled"
                self._append({"id": lesson_id, "uses": existing["uses"], "created": existing["created"], "status": existing["status"]})
                return existing
            lesson = {"id": lesson_id, "text": text, "tags": sorted(set(tags)) or ["any"], "source": source,
                      "status": status, "uses": 1, "created": time.time()}
            lesson.update({k: v for k, v in extra.items() if v is not None})
            self.lessons[lesson_id] = lesson
            self._append(lesson)
            return lesson

    def set_status(self, lesson_id: str, status: str) -> Optional[dict]:
        if status not in ("confirmed", "hypothesis", "disabled"):
            raise ValueError("Trạng thái không hợp lệ.")
        with self.lock:
            lesson = self.lessons.get(lesson_id)
            if lesson:
                lesson["status"] = status
                self._append({"id": lesson_id, "status": status})
            return lesson

    @staticmethod
    def _applies(lesson: dict, tags: list[str]) -> bool:
        have = set(lesson.get("tags") or ["any"])
        return "any" in have or bool(have & set(tags))

    def recall(self, tags: list[str], confirmed_only: bool = False) -> list[dict]:
        """Bài học áp dụng cho bối cảnh này, mới nhất trước; bài học của người đứng trước bài học mẫu."""
        out = [l for l in self.lessons.values()
               if l.get("status") != "disabled" and self._applies(l, tags)
               and (not confirmed_only or l.get("status") == "confirmed")]
        return sorted(out, key=lambda l: (l.get("status") != "confirmed", -float(l.get("created", 0))))

    def recall_answer(self, question: str, tags: list[str]) -> Optional[dict]:
        """Câu trả lời đã chốt cho một câu hỏi cùng loại. Khớp chính xác > khớp một phần > khớp 'loại:*'."""
        prefix, _, suffix = question.partition(":")
        best, best_rank = None, None
        for lesson in self.recall(tags, confirmed_only=True):
            q = lesson.get("question")
            if not q or lesson.get("answer") is None:
                continue
            qp, _, qs = q.partition(":")
            if q == question:
                rank = 0
            elif qp == prefix and qs and qs != "*" and norm(qs) in norm(suffix):
                rank = 1
            elif qp == prefix and qs == "*":
                rank = 2
            else:
                continue
            rank = (rank, lesson["id"].startswith("seed-"), -float(lesson.get("created", 0)))
            if best_rank is None or rank < best_rank:
                best, best_rank = lesson, rank
        return best

    def rule_override(self, rule: str, tags: list[str]) -> Optional[dict]:
        for lesson in self.recall(tags, confirmed_only=True):
            if lesson.get("rule") == rule:
                return lesson
        return None

    def extra_rules(self, section: str, tags: list[str]) -> list[dict]:
        return [l for l in self.recall(tags, confirmed_only=True)
                if l.get("section") == section and not l.get("rule") and not l.get("question")
                and not l["id"].startswith("seed-")]

    # ----------------------------------------- học từ bản người đã duyệt ---
    def learn_from_edit(self, draft: str, approved: str, tags: list[str], run_id: str) -> list[dict]:
        """So bản nháp với bản người duyệt đã sửa; mỗi chỗ khác nhau thành một bài học `confirmed`."""
        old, new = draft.splitlines(), approved.splitlines()
        heading_at = []
        current = ""
        for line in new:
            if line.startswith("#"):
                current = line.lstrip("# ").strip()
            heading_at.append(current)
        old_heading = []
        current = ""
        for line in old:
            if line.startswith("#"):
                current = line.lstrip("# ").strip()
            old_heading.append(current)

        def meaningful(line: str) -> bool:
            text = strip_tag(line)[0].strip(" |-*>[]#")
            return len(text) >= 8 and not set(text) <= set("-|: ")

        learned = []
        matcher = difflib.SequenceMatcher(a=[strip_tag(l)[0] for l in old], b=[strip_tag(l)[0] for l in new], autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal" or len(learned) >= 25:
                continue
            removed = [l for l in old[i1:i2] if meaningful(l)]
            added = [l for l in new[j1:j2] if meaningful(l)]
            section = heading_at[min(j1, len(heading_at) - 1)] if heading_at else ""
            if not section and old_heading:
                section = old_heading[min(i1, len(old_heading) - 1)]
            section = re.sub(r"^\d+\.\s*", "", section)
            pairs = list(zip(removed, added))
            for before, after in pairs:
                b_text, b_rule = strip_tag(before)
                a_text = strip_tag(after)[0]
                if b_text.strip() == a_text.strip():
                    continue
                clean = a_text.strip().lstrip("-* ").strip()
                learned.append(self.add_lesson(
                    f"Mục «{section}»: người duyệt sửa «{b_text.strip().lstrip('-* ')[:160]}» thành «{clean[:260]}».",
                    tags, "người duyệt sửa", rule=b_rule, replacement=clean if b_rule else None, section=section, run_id=run_id))
            for before in removed[len(pairs):]:
                b_text, b_rule = strip_tag(before)
                learned.append(self.add_lesson(
                    f"Mục «{section}»: người duyệt bỏ «{b_text.strip().lstrip('-* ')[:200]}».",
                    tags, "người duyệt sửa", rule=b_rule, replacement="" if b_rule else None, section=section, run_id=run_id))
            for after in added[len(pairs):]:
                clean = strip_tag(after)[0].strip().lstrip("-* ").strip()
                if clean.startswith("|") or clean.startswith("#"):
                    continue  # hàng bảng hoặc tiêu đề mới: không phải một luật độc lập
                learned.append(self.add_lesson(clean[:400], tags, "người duyệt thêm", section=section, run_id=run_id))
        return learned

    # -------------------------------------------------------------- run ----
    def save_run(self, run: dict) -> None:
        folder = self.runs_dir / run["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")

    def load_run(self, run_id: str) -> Optional[dict]:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{4,64}", run_id or ""):
            return None
        path = self.runs_dir / run_id / "run.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def run_file(self, run_id: str, name: str) -> Path:
        folder = self.runs_dir / run_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder / name
