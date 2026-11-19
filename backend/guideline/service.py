"""Điều phối một lần soạn guideline: bắt đầu -> quét ảnh -> soạn nháp -> trả lời câu hỏi -> duyệt -> học."""
from __future__ import annotations

import copy
import json
import re
import secrets
import threading
import time
from typing import Optional

import cv2
import numpy as np
import yaml

from ..engines import Engine
from ..i18n import get_lang, tr
from . import draft as D
from .library import Library, norm
from .llm import LLM, LLMError, parse_json
from .survey import DISCOVERY, Concept, Survey

SECTIONS = ["Phạm vi và dữ liệu", "Taxonomy và loại shape", "Bounding box", "Polygon", "Polyline", "Mask / semantic",
            "Skeleton", "Track", "Attributes", "Các cặp class dễ nhầm", "Quy tắc khi class hoặc boundary không rõ"]


class _PlainDumper(yaml.SafeDumper):
    """Không dùng &anchor/*alias khi ghi profile: file sinh ra dễ đọc và sửa tay hơn."""

    def ignore_aliases(self, data):
        return True


class GuidelineService:
    def __init__(self, library: Library, llm: Optional[LLM]):
        self.lib = library
        self.llm = llm
        self.live: dict[str, dict] = {}  # run_id -> {"survey": Survey, "send_images": bool}
        self.lock = threading.Lock()

    # ------------------------------------------------------------ bắt đầu --
    def start(self, labels: list[dict], job: dict, purpose: str, extra_terms: list[str], send_images: bool,
              known_prompts: dict[str, list[str]], title: str = "") -> dict:
        concepts: list[Concept] = []
        taken = set()
        # Label skeleton: tool đếm ĐỐI TƯỢNG mang skeleton (người, khuôn mặt), không tìm theo tên label.
        # Các label cùng một bộ (7 skeleton mặt) dùng chung một lần đếm, không đếm 7 lần cùng một khuôn mặt.
        detect_of = {norm(name): norm(group.get("detect") or "person")
                     for group in D.skeleton_sets(self.lib).values() for name in group["labels"]}
        canon = D.skeleton_canon(labels, self.lib)   # 'person_vf17' -> 'person'
        for label in labels:
            if (label.get("type") or "any") == "skeleton":
                known = norm(canon.get(norm(label["name"]), label["name"]))
                target = detect_of.get(known, "face" if known == "face" else "person")
                prompts, kind = ([] if target in taken else [target]), "thing"
            else:
                prompts = known_prompts.get(norm(label["name"]))
                prompts = [norm(label["name"]).replace("/", " ")] if prompts is None else list(prompts)
                kind = next((k for p, k in DISCOVERY if p in prompts), "thing")
            concepts.append(Concept(label["name"], prompts, kind, "label", label.get("id"), label.get("type") or "any"))
            taken.update(norm(p) for p in prompts)
        for term in extra_terms:
            term = " ".join(term.split())[:40]
            if term and norm(term) not in taken:
                concepts.append(Concept(term, [term], "thing", "người dùng"))
                taken.add(norm(term))
        for prompt, kind in DISCOVERY:
            if norm(prompt) not in taken:
                concepts.append(Concept(prompt, [prompt], kind, "từ vựng"))
        run_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        run = {
            "id": run_id, "created": time.time(), "status": "scanning", "title": title[:120], "purpose": purpose.strip()[:600],
            "job": job, "labels": [{"id": l.get("id"), "name": l["name"], "type": l.get("type") or "any"} for l in labels],
            "summary": None, "questions": [], "answers": {}, "findings": [], "llm": {}, "warnings": [],
            "markdown": "", "approved": None, "lessons_used": [], "send_images": bool(send_images),
            "lang": get_lang(),                       # bản nháp và câu hỏi gửi LLM theo ngôn ngữ người soạn
        }
        with self.lock:
            self.live[run_id] = {"survey": Survey(concepts), "send_images": bool(send_images), "llm_discovered": False}
        self.lib.save_run(run)
        return {"run_id": run_id, "concepts": len(concepts), "llm": self.llm_info(send_images)}

    def llm_info(self, send_images: bool) -> dict:
        if not self.llm:
            return {"provider": "none", "model": "", "images": False}
        return dict(self.llm.describe(), images=bool(send_images))

    # --------------------------------------------------------------- quét --
    def _thumb(self, run_id: str, name: str, image: np.ndarray, box: Optional[list[float]] = None) -> None:
        h, w = image.shape[:2]
        if box is not None:
            pad_x, pad_y = (box[2] - box[0]) * 0.6 + 20, (box[3] - box[1]) * 0.6 + 20
            x1, y1 = int(max(0, box[0] - pad_x)), int(max(0, box[1] - pad_y))
            x2, y2 = int(min(w, box[2] + pad_x)), int(min(h, box[3] + pad_y))
            image = image[y1:y2, x1:x2].copy()
            cv2.rectangle(image, (int(box[0] - x1), int(box[1] - y1)), (int(box[2] - x1), int(box[3] - y1)), (255, 60, 60), 2)
            h, w = image.shape[:2]
        scale = min(1.0, 640.0 / max(w, h, 1))
        if scale < 1.0:
            image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self.lib.run_file(run_id, name).write_bytes(buf.tobytes())

    def scan(self, run_id: str, engine: Engine, frames: list[tuple[int, np.ndarray]]) -> dict:
        live = self.live.get(run_id)
        if not live:
            raise KeyError("Lượt quét này không còn trong bộ nhớ (backend đã khởi động lại). Hãy quét lại.")
        survey: Survey = live["survey"]
        warnings: list[str] = []
        for frame, image in frames:
            before = len(survey.cases)
            survey.scan(engine, image, frame, warnings)
            self._thumb(run_id, f"frame_{frame}.jpg", image)
            for i in range(before, len(survey.cases)):
                self._thumb(run_id, f"case_{i}.jpg", image, survey.cases[i]["bbox"])
            if (len(survey.frames) == survey.discovery_frames and self.llm and live["send_images"]
                    and not live["llm_discovered"]):
                live["llm_discovered"] = True
                self._discover(run_id, survey, warnings)
        found = sum(1 for s in survey.stats.values() if s.instances)
        return {"scanned": len(survey.frames), "active_concepts": len(survey.active()), "found": found,
                "warnings": sorted(set(warnings))[:5]}

    def _images(self, run_id: str, names: list[str]) -> list[bytes]:
        out = []
        for name in names:
            path = self.lib.run_file(run_id, name)
            if path.exists():
                out.append(path.read_bytes())
        return out

    def _discover(self, run_id: str, survey: Survey, warnings: list[str]) -> None:
        """Khắc phục việc SAM3 chỉ tìm được thứ được gọi tên: nhờ LLM nhìn vài ảnh và kể tên các loại vật thấy được."""
        frames = [f["frame"] for f in survey.frames][:4]
        try:
            text = self.llm.complete(
                "Bạn giúp khảo sát một bộ ảnh trước khi viết annotation guideline. Chỉ trả lời bằng JSON.",
                "Liệt kê các loại vật thể và vùng nền nhìn thấy trong các ảnh này, mỗi loại là một cụm danh từ tiếng Anh ngắn "
                "(1-3 từ, số ít, chữ thường) dùng được làm prompt cho model phân đoạn. Tối đa 25 mục. "
                'Trả về JSON: {"concepts": [{"name": "...", "kind": "thing|stuff|line"}]}',
                self._images(run_id, [f"frame_{f}.jpg" for f in frames]), 800)
            items = parse_json(text).get("concepts", [])
            new = []
            for item in items[:25]:
                name = " ".join(str(item.get("name", "")).lower().split())[:40]
                if re.fullmatch(r"[a-z][a-z \-]{1,39}", name):
                    kind = item.get("kind") if item.get("kind") in ("thing", "stuff", "line") else "thing"
                    new.append(Concept(name, [name], kind, "LLM"))
            added = survey.add_concepts(new)
            if added:
                survey.discovery_frames = len(survey.frames) + 4  # thử các khái niệm mới trên thêm vài ảnh
        except (LLMError, AttributeError, ValueError) as exc:
            warnings.append(f"LLM không đề xuất được khái niệm mới: {exc}")

    # ----------------------------------------------------------- soạn nháp -
    def _load(self, run_id: str) -> dict:
        run = self.lib.load_run(run_id)
        if not run:
            raise KeyError("Không tìm thấy lượt soạn guideline này.")
        return run

    def _render(self, run: dict) -> dict:
        run["findings"] = D.self_check(run, self.lib) + [f for f in run.get("findings", []) if f.get("code", "").startswith("annotator")]
        markdown, used = D.render_markdown(run, self.lib)
        run["markdown"] = markdown
        run["lessons_used"] = [l["id"] for l in used]
        self.lib.save_run(run)
        return self.view(run)

    def _rebuild_questions(self, run: dict) -> None:
        """Bộ câu hỏi phụ thuộc bài toán: đổi bài toán (ví dụ sang keypoint) thì hỏi lại cho đúng."""
        chosen = run.get("answers", {}).get("task_type")
        run["questions"] = D.build_questions(run["summary"], run["labels"], run["job"].get("mode", ""), self.lib, chosen)
        run["answers"] = {k: v for k, v in run.get("answers", {}).items() if any(q["key"] == k for q in run["questions"])}

    def draft(self, run_id: str) -> dict:
        run = self._load(run_id)
        live = self.live.get(run_id)
        if live:
            survey: Survey = live["survey"]
            if not survey.frames:
                raise ValueError("Chưa quét ảnh nào.")
            run["summary"] = survey.summary()
        if not run.get("summary"):
            raise KeyError("Lượt quét này không còn trong bộ nhớ (backend đã khởi động lại). Hãy quét lại.")
        self._rebuild_questions(run)
        run["status"] = "drafted"
        run["warnings"] = []
        if self.llm:
            self._enrich(run)
            if run.get("send_images"):
                self._virtual_annotators(run)
        return self._render(run)

    def _context(self, run: dict) -> str:
        s = run["summary"]
        compact = {
            "so_anh": s["images"], "kich_thuoc": [s["width"], s["height"]], "anh_toi_pct": s["dark_pct"], "anh_mo_pct": s["blurry_pct"],
            "bi_che_pct": s["occluded_pct"], "cham_bien_pct": s["truncated_pct"], "rat_nho_pct": s["tiny_pct"],
            "khai_niem": [{k: c.get(k) for k in ("name", "kind", "source", "instances", "image_pct", "height_p50", "borderline_pct")}
                          for c in s["concepts"] if c["instances"]][:30],
            "cap_de_nham": [{k: p[k] for k in ("a", "b", "count", "share_pct")} for p in s["pairs"][:10]],
            "ca_kho": [{"code": c["code"], "frame": c["frame"], "mo_ta": c["text"]} for c in s["cases"][:16]],
        }
        lessons = [l["text"] for l in self.lib.recall([D.task_of(run), "any"])[:20]]
        return tr(f"Mục đích bộ dữ liệu: {run.get('purpose') or '(người dùng chưa nêu)'}\n"
                f"Bài toán: {D.TASKS[D.task_of(run)]}\n"
                f"Label của task: {', '.join(l['name'] + ' (' + l['type'] + ')' for l in run['labels']) or '(chưa có)'}\n"
                f"Số liệu khảo sát (SAM3 đếm, có thể sót/nhầm): {json.dumps(compact, ensure_ascii=False)}\n"
                f"Bài học từ các lần trước: {json.dumps(lessons, ensure_ascii=False)}", run.get("lang", "vi"))

    def _enrich(self, run: dict) -> None:
        """LLM viết phần tool không tự viết được: định nghĩa class mới, quy tắc cho cặp dễ nhầm, luật bổ sung."""
        classes = D.taxonomy(run, self.lib)
        need_defs = [c["name"] for c in classes if not c["definition"] or c["definition_source"] == "LLM đề xuất"]
        need_pairs = [q["key"][5:].split("|") for q in run["questions"]
                      if q["key"].startswith("pair:") and not self.lib.pair_rule(*q["key"][5:].split("|"))]
        references = "\n\n".join(f"--- GUIDELINE MẪU: {name} ---\n{text[:3500]}" for name, text in self.lib.references())
        system = ("Bạn là người viết annotation guideline cho dữ liệu thị giác máy tính. Viết tiếng Việt, ngắn, cụ thể, "
                  "theo đúng văn phong của các guideline mẫu. Mỗi luật phải kiểm chứng được trên ảnh. "
                  "Không bịa số liệu. Điều gì phụ thuộc mục đích dự án mà bạn không biết thì nêu thành luật đề xuất, không khẳng định. "
                  "Chỉ trả lời bằng JSON.")
        ask = (f"{self._context(run)}\n\n{references}\n\n"
               f"Hãy viết:\n1. Định nghĩa cho các class: {json.dumps(need_defs, ensure_ascii=False)} "
               "(nêu rõ thuộc và không thuộc, một hai câu).\n"
               f"2. Quy tắc phân biệt cho các cặp: {json.dumps(need_pairs, ensure_ascii=False)}.\n"
               f"3. Tối đa 6 luật bổ sung mà bộ ảnh này cần nhưng số liệu cho thấy còn thiếu; mỗi luật thuộc một trong các mục: {json.dumps(SECTIONS, ensure_ascii=False)}.\n"
               'Trả về JSON: {"definitions": {"class": "..."}, "pairs": [{"a": "...", "b": "...", "rule": "..."}], '
               '"extra_rules": [{"section": "...", "text": "..."}]}')
        system, ask = tr(system, run.get("lang", "vi")), tr(ask, run.get("lang", "vi"))
        images = []
        if run.get("send_images"):
            frames = sorted({e["frame"] for c in run["summary"]["concepts"][:8] for e in c.get("examples", [])})[:6]
            images = self._images(run["id"], [f"frame_{f}.jpg" for f in frames])
        try:
            data = parse_json(self.llm.complete(system, ask, images, 3000))
            run["llm"] = {
                "definitions": {str(k): " ".join(str(v).split())[:400] for k, v in (data.get("definitions") or {}).items()},
                "pairs": [{"a": str(p["a"]), "b": str(p["b"]), "rule": " ".join(str(p["rule"]).split())[:400]}
                          for p in data.get("pairs") or [] if isinstance(p, dict) and {"a", "b", "rule"} <= set(p)],
                "extra_rules": [{"section": str(r["section"]), "text": " ".join(str(r["text"]).split())[:400]}
                                for r in (data.get("extra_rules") or [])[:6] if isinstance(r, dict) and {"section", "text"} <= set(r)],
            }
        except (LLMError, AttributeError, TypeError, KeyError) as exc:
            run["warnings"].append(f"LLM không viết được phần bổ sung, bản nháp dùng mẫu sẵn có: {exc}")

    def _virtual_annotators(self, run: dict) -> None:
        """Tự kiểm tra bằng hai annotator ảo: cùng đọc bản nháp, cùng gán các ca khó; chỗ lệch nhau là chỗ guideline mơ hồ."""
        cases = run["summary"]["cases"][:6]
        images = self._images(run["id"], [f"case_{i}.jpg" for i in range(len(cases))])
        if len(images) < 2:
            return
        guideline, _ = D.render_markdown(run, self.lib)
        guideline = re.sub(r"\s*<!--r:[^>]+-->", "", re.split(r"## (?:Phụ lục|Appendix) A", guideline)[0])
        ask = (f"GUIDELINE:\n{guideline}\n\nCó {len(images)} ảnh ca khó, theo thứ tự 1..{len(images)}; đối tượng cần xét nằm trong khung đỏ. "
               "Với mỗi ảnh, gán nhãn CHỈ dựa vào guideline trên. "
               'Trả về JSON: [{"case": 1, "label": "<tên class> | không gán | không quyết được", "rule": "<luật đã dùng, trích ngắn>", '
               '"missing": "<điều guideline chưa nói mà bạn cần, hoặc rỗng>"}]')
        personas = ["Bạn là annotator làm đúng từng chữ của guideline, không suy diễn thêm. Chỉ trả lời bằng JSON.",
                    "Bạn là annotator ưu tiên không bỏ sót đối tượng; khi guideline không nói rõ, bạn chọn cách bạn cho là hợp lý nhất. Chỉ trả lời bằng JSON."]
        lang = run.get("lang", "vi")
        ask, personas = tr(ask, lang), [tr(p, lang) for p in personas]
        try:
            answers = []
            for persona in personas:
                data = parse_json(self.llm.complete(persona, ask, images, 1500))
                answers.append({int(item["case"]): item for item in data if isinstance(item, dict) and "case" in item})
        except (LLMError, ValueError, TypeError, KeyError) as exc:
            run["warnings"].append(f"Không chạy được annotator ảo: {exc}")
            return
        findings = []
        for i, case in enumerate(cases[:len(images)], 1):
            a, b = answers[0].get(i), answers[1].get(i)
            if not a or not b:
                continue
            la, lb = norm(a.get("label", "")), norm(b.get("label", ""))
            missing = "; ".join(m for m in (str(a.get("missing") or ""), str(b.get("missing") or "")) if m.strip())
            if la != lb or {"không quyết được", "cannot decide"} & {la, lb}:
                findings.append({
                    "code": "annotator_disagree", "question": None,
                    "examples": [{"frame": case["frame"], "bbox": case["bbox"]}],
                    "text": f"Hai annotator ảo gán khác nhau ở frame {case['frame']} ({case['text']}): "
                            f"'{a.get('label')}' so với '{b.get('label')}'." + (f" Guideline còn thiếu: {missing[:300]}" if missing else ""),
                })
        run["findings"] = findings

    # ------------------------------------------------- trả lời, duyệt, học --
    def answer(self, run_id: str, answers: dict[str, str]) -> dict:
        run = self._load(run_id)
        for key, value in answers.items():
            q = next((q for q in run["questions"] if q["key"] == key), None)
            if not q:
                continue
            if value in ("", None):
                run["answers"].pop(key, None)
            elif any(o["id"] == value for o in q["options"]):
                run["answers"][key] = value
        if "task_type" in answers:
            self._rebuild_questions(run)
        return self._render(run)

    def sync_labels(self, run_id: str, labels: list[dict]) -> dict:
        """Task vừa được thêm label: cập nhật để bản nháp và danh sách label còn thiếu khớp với task."""
        run = self._load(run_id)
        run["labels"] = [{"id": l.get("id"), "name": l["name"], "type": l.get("type") or "any"} for l in labels]
        self._rebuild_questions(run)  # label vừa thêm không còn là "vật chưa có label"
        return self._render(run)

    def labels(self, run_id: str) -> dict:
        return D.build_labels(self._load(run_id), self.lib)

    def approve(self, run_id: str, markdown: str) -> dict:
        """Người duyệt chốt bản guideline. Mọi chỗ họ quyết định hoặc sửa thành bài học `confirmed` cho các lần sau."""
        run = self._load(run_id)
        task = D.task_of(run)
        learned = []
        for key, value in run["answers"].items():
            q = next((q for q in run["questions"] if q["key"] == key), None)
            if not q or key == "task_type":
                continue
            label = next((o["label"] for o in q["options"] if o["id"] == value), value)
            learned.append(self.lib.add_lesson(f"{q['text']} Người duyệt chốt: {label}.", [task], "người duyệt trả lời",
                                               question=key, answer=value, run_id=run_id))
        learned += self.lib.learn_from_edit(run["markdown"], markdown, [task], run_id)
        for finding in run.get("findings", []):
            # Lỗ hổng tool tự thấy chỉ là giả thuyết: được nhắc lại lần sau, không tự thành luật.
            self.lib.add_lesson(finding["text"], [task], "tool tự kiểm tra", status="hypothesis", run_id=run_id)
        run["approved"] = markdown
        run["status"] = "approved"
        self.lib.save_run(run)
        self.lib.run_file(run_id, "guideline.md").write_text(re.sub(r"\s*<!--r:[^>]+-->", "", markdown), encoding="utf-8")
        self.lib.run_file(run_id, "labels.json").write_text(
            json.dumps(D.build_labels(run, self.lib)["labels"], ensure_ascii=False, indent=2), encoding="utf-8")
        return {"learned": learned, "file": str(self.lib.run_file(run_id, "guideline.md"))}

    def profile_yaml(self, run: dict) -> str:
        profile = D.build_profile(run, self.lib)
        header = (f"# Sinh tự động từ guideline nháp của lượt {run['id']}.\n"
                  "# Kiểm tra lại prompt và ngưỡng ở tab Label trước khi dùng cho bài thật.\n")
        return header + yaml.safe_dump(profile, allow_unicode=True, sort_keys=False, width=120)

    def keypoint_profile(self, run: dict, base: Optional[dict]) -> dict:
        """Profile cho job skeleton. Danh sách điểm và cách đặt điểm lấy từ profile keypoints có sẵn (`base`);
        bản guideline chỉ quyết định hai thứ tool cần biết: trái/phải theo ảnh hay theo người, và gán một người hay mọi người.
        Hai thứ đó đã giống profile có sẵn thì dùng luôn profile đó; khác thì ghi một bản sao đã chỉnh."""
        if not base or base.get("mode") != "keypoints":
            return {"profile_id": "auto", "yaml": None,
                    "note": "Chưa có profile keypoints nào khớp các skeleton của bản guideline này: tool chưa biết đặt điểm. "
                            "Xem docs/PROFILES.md để viết profile (danh sách điểm và nguồn toạ độ)."}
        side = D.decided(run, "kp_lr")[0] or "image"
        scope = D.decided(run, "kp_scope")[0] or "driver"
        pose = (base.get("rules") or {}).get("pose") or {}
        words = ("trái/phải theo " + ("phía của ảnh" if side == "image" else "người trong ảnh")
                 + ", " + ("chỉ gán người lái" if scope == "driver" else "gán mọi người"))
        if pose.get("lr_convention", "anatomical") == side and (int(pose.get("max_people", 0)) == 1) == (scope == "driver"):
            return {"profile_id": base["id"], "yaml": None,
                    "note": f"Profile '{base.get('title', base['id'])}' đã đúng theo bản guideline này ({words}): dùng luôn profile đó."}
        derived = {k: copy.deepcopy(v) for k, v in base.items() if not k.startswith("_")}
        derived.update(id=f"auto_{run['id']}", auto_pick=False,
                       title=f"{base.get('title', base['id'])} — chỉnh theo guideline nháp {run['id']}",
                       description=f"Bản sao của profile {base['id']}, đổi: {words}.")
        rules = derived.setdefault("rules", {})
        rules.setdefault("pose", {}).update(lr_convention=side, max_people=1 if scope == "driver" else 0)
        rules.setdefault("face", {})["max_faces"] = 1 if scope == "driver" else 0
        rules.setdefault("qa", {})["one_per_label"] = scope == "driver"
        header = (f"# Sinh tự động từ guideline nháp của lượt {run['id']}: bản sao của {base['id']}, đổi {words}.\n"
                  "# Chỉ dùng khi được chọn đích danh ở tab Cài đặt (auto_pick: false), không tự áp cho job khác.\n")
        text = header + yaml.dump(derived, Dumper=_PlainDumper, allow_unicode=True, sort_keys=False, width=140)
        return {"profile_id": derived["id"], "yaml": text,
                "note": f"Đã tạo profile riêng từ '{base.get('title', base['id'])}' với quy ước của bản guideline này ({words})."}

    def feedback(self, text: str, tags: list[str], run_id: Optional[str]) -> dict:
        """Sai sót phát hiện SAU khi dùng guideline (reviewer trả bài, mentor sửa luật): bài học đáng tin nhất."""
        text = text.strip()
        if len(text) < 8:
            raise ValueError("Mô tả sai sót quá ngắn.")
        return self.lib.add_lesson(text, tags or ["any"], "phản hồi sau khi dùng", run_id=run_id)

    def _label_plan(self, run: dict) -> Optional[dict]:
        if not run.get("questions"):
            return None
        plan = D.build_labels(run, self.lib)
        brief = lambda s: {"name": s["name"], "type": s["type"], "points": len(s.get("sublabels") or []),  # noqa: E731
                           "attributes": [a["name"] for a in s["attributes"]]}
        return {"total": len(plan["labels"]), "missing": [brief(s) for s in plan["missing"]], "notes": plan["notes"]}

    # ---------------------------------------------------------------- xem --
    def view(self, run: dict) -> dict:
        s = run.get("summary") or {}
        lessons = [self.lib.lessons[i] for i in run.get("lessons_used", []) if i in self.lib.lessons]
        return {
            "run_id": run["id"], "status": run["status"], "title": run.get("title", ""), "purpose": run.get("purpose", ""),
            "task": D.task_of(run) if run.get("questions") else None,
            "summary": {k: s.get(k) for k in ("images", "width", "height", "dark_pct", "blurry_pct", "occluded_pct",
                                              "truncated_pct", "tiny_pct", "instances_per_image")},
            "concepts": [c for c in s.get("concepts", []) if c["instances"]][:40],
            "cases": s.get("cases", []),
            "questions": [dict(q, answer=run["answers"].get(q["key"])) for q in run.get("questions", [])],
            "findings": run.get("findings", []), "warnings": run.get("warnings", []),
            "markdown": run.get("approved") or run.get("markdown", ""), "approved": run.get("approved") is not None,
            "lessons_used": lessons, "llm": self.llm_info(run.get("send_images", False)),
            "label_plan": self._label_plan(run),
        }
