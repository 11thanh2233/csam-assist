"""Profile = guideline viết dưới dạng máy đọc được (YAML trong thư mục profiles/).

Mỗi profile ánh xạ tên label trong CVAT sang: prompt cho SAM3, loại shape, ngưỡng tin cậy
và các luật hậu xử lý. Label không có trong profile vẫn dùng được: prompt mặc định là
chính tên label.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger("sam3-assist")

SUPPORTED_SHAPES = ("rectangle", "polygon", "polyline", "mask")

# Thứ tự 17 điểm COCO, cũng là thứ tự đầu ra của model tư thế.
COCO17 = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]

# Profile dự phòng cho từng mục đích khi task không khớp profile nào.
FALLBACK = {"frame": "generic", "track": "generic_track"}


def norm(name: str) -> str:
    """Chuẩn hoá tên label để so khớp: 'Traffic_Light' == 'traffic light'."""
    return re.sub(r"[\s_\-]+", " ", str(name).strip().lower())


def _ascii(text: str) -> str:
    """Bỏ dấu tiếng Việt: 'Lông mày trái' -> 'Long may trai'."""
    text = str(text).replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")


def _key(text: str) -> str:
    """Tên label rút gọn để so gần đúng: 'Person_VF17' -> 'personvf17', 'Mắt trái' -> 'mattrai'."""
    return re.sub(r"[^a-z0-9]", "", _ascii(text).lower())


MATCH_HOW = {
    "exact": "",
    "similar": "theo tên gần giống",
    "ids": "theo dãy số của các điểm",
    "count": "theo số điểm",
    "manual": "bạn chọn tay ở tab Label",
}


def match_skeletons(labels: list[dict], candidates: list[dict], forced: Optional[dict[int, str]] = None) -> dict[int, tuple[dict, str]]:
    """Ghép label skeleton của task với các skeleton đã biết (của một profile, hoặc của thư viện mẫu).

    Tên label trong task thật thường không trùng tên trong guideline ('person_vf17' thay cho 'person'), nên ngoài
    khớp đúng tên còn ba cách nữa, theo thứ tự tin cậy giảm dần. Mỗi skeleton đã biết chỉ ghép với một label.
      exact   : trùng tên hoặc trùng một alias
      similar : tên label CHỨA tên/alias của skeleton ('person_vf17' chứa 'person'; bỏ dấu, bỏ gạch, không phân hoa thường)
      ids     : tên các điểm là số và đúng bằng dãy Point ID của skeleton (14..21 -> mattrai)
      count   : số điểm chỉ khớp đúng một skeleton còn lại (17 điểm -> person)
    `candidates`: [{"name", "aliases"?, "keypoints": [{"id"?}, ...]}]. `forced`: {vị trí label: tên skeleton} do người dùng chọn.
    Trả về {vị trí label: (candidate, cách ghép)}.
    """
    out: dict[int, tuple[dict, str]] = {}
    free = list(range(len(candidates)))

    def take(li: int, ci: int, how: str) -> None:
        out[li] = (candidates[ci], how)
        free.remove(ci)

    def names(ci: int) -> list[str]:
        return [candidates[ci]["name"], *candidates[ci].get("aliases", [])]

    for li, wanted in (forced or {}).items():
        ci = next((c for c in free if norm(candidates[c]["name"]) == norm(wanted)), None)
        if ci is not None and li < len(labels):
            take(li, ci, "manual")
    for li, label in enumerate(labels):
        if li in out:
            continue
        ci = next((c for c in free if norm(label["name"]) in {norm(n) for n in names(c)}), None)
        if ci is not None:
            take(li, ci, "exact")

    def is_skeleton(li: int) -> bool:
        return (labels[li].get("type") or "any").lower() == "skeleton"

    # similar: tên dài hơn được xét trước, để 'left_eyebrow' không bị nhận nhầm là 'left eye'
    pairs = []
    for li, label in enumerate(labels):
        if li in out or not is_skeleton(li):
            continue
        key = _key(label["name"])
        for ci in free:
            hit = max((len(_key(n)) for n in names(ci) if len(_key(n)) >= 4 and _key(n) in key), default=0)
            if hit:
                pairs.append((-hit, li, ci))
    for _, li, ci in sorted(pairs):
        if li not in out and ci in free:
            take(li, ci, "similar")

    def point_ids(label: dict) -> Optional[set[int]]:
        ids = set()
        for sub in label.get("sublabels") or []:
            numbers = re.findall(r"\d+", str(sub.get("name", "")))
            if len(numbers) != 1 or re.sub(r"[\d\W_]", "", str(sub.get("name", ""))):
                return None  # tên điểm không thuần là số
            ids.add(int(numbers[0]))
        return ids or None

    def cand_ids(ci: int) -> set[int]:
        return {int(k["id"]) for k in candidates[ci].get("keypoints") or [] if isinstance(k, dict) and "id" in k}

    def cand_count(ci: int) -> int:
        return len(candidates[ci].get("keypoints") or [])

    # ids: chỉ tin dãy số khi các skeleton của task KHÔNG dùng chung số nào. Nếu mỗi skeleton đánh số lại từ 0
    # (0-4, 0-4, 0-3...) thì dãy số không nói lên skeleton nào là skeleton nào.
    all_ids = {li: point_ids(label) for li, label in enumerate(labels) if is_skeleton(li)}
    for li, label in enumerate(labels):
        ids = all_ids.get(li)
        if li in out or not ids or any(other != li and ids & (theirs or set()) for other, theirs in all_ids.items()):
            continue
        ci = next((c for c in free if cand_ids(c) == ids and cand_count(c) == len(label.get("sublabels") or [])), None)
        if ci is not None:
            take(li, ci, "ids")
    for li, label in enumerate(labels):
        if li in out or not is_skeleton(li):
            continue
        n = len(label.get("sublabels") or [])
        same_cands = [c for c in free if n and cand_count(c) == n]
        same_labels = [x for x in range(len(labels)) if x not in out and is_skeleton(x) and len(labels[x].get("sublabels") or []) == n]
        if len(same_cands) == 1 and len(same_labels) == 1:
            take(li, same_cands[0], "count")
    return out


class ProfileStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.profiles: dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        self.profiles = {}
        for path in sorted(self.directory.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data.setdefault("id", path.stem)
            data["_path"] = str(path)
            data.setdefault("labels", [])
            data.setdefault("rules", {})
            data.setdefault("defaults", {})
            data["_index"] = {}
            for entry in data["labels"]:
                for key in [entry["name"], *entry.get("aliases", [])]:
                    data["_index"][norm(key)] = entry
            self.profiles[data["id"]] = data
        # Profile ghi `supersedes: [id_cũ]` thay cho profile cũ. Khi cả hai cùng nằm trong thư mục (ví dụ sau khi
        # giải nén bản mới đè lên bản cũ), bản cũ bị bỏ qua, để hai quy ước ngược nhau không cùng tranh một label.
        self.superseded: dict[str, str] = {}
        for profile in list(self.profiles.values()):
            for old_id in profile.get("supersedes") or []:
                if old_id in self.profiles and old_id != profile["id"]:
                    del self.profiles[old_id]
                    self.superseded[old_id] = profile["id"]
                    log.warning("Bỏ qua profile '%s': đã được thay bằng '%s'. Xoá hoặc chuyển file cũ vào profiles/archive/ "
                                "để hết thông báo này.", old_id, profile["id"])
        for pid, mode in (("generic", "instance"), ("generic_track", "tracking")):
            if pid not in self.profiles:
                self.profiles[pid] = {
                    "id": pid, "title": pid, "mode": mode, "labels": [], "rules": {}, "defaults": {}, "_index": {},
                }

    # ------------------------------------------------ quản lý profile (tab Label) ---
    BUILTIN = ("generic", "generic_track")   # profile dự phòng: luôn có, không ẩn/xoá

    @property
    def hidden_dir(self) -> Path:
        return self.directory / "archive"

    @staticmethod
    def kind_of(profile_id: str) -> str:
        if profile_id in ProfileStore.BUILTIN:
            return "dự phòng"
        if profile_id.startswith("gl_"):
            return "nạp từ guideline"
        if profile_id.startswith("auto_"):
            return "sinh từ bản nháp"
        return "profile"

    def manage_list(self) -> dict:
        active = []
        for p in self.profiles.values():
            path = p.get("_path")
            active.append({"id": p["id"], "title": p.get("title", p["id"]), "mode": p.get("mode", "instance"),
                           "file": Path(path).name if path else None, "kind": self.kind_of(p["id"]),
                           "locked": p["id"] in self.BUILTIN or not path})
        hidden = []
        for path in sorted(self.hidden_dir.glob("*.yaml")) if self.hidden_dir.exists() else []:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
            pid = data.get("id", path.stem)
            item = {"id": pid, "title": data.get("title", pid), "mode": data.get("mode", "instance"), "file": path.name,
                    "kind": self.kind_of(pid)}
            replaced_by = next((p["id"] for p in self.profiles.values() if pid in (p.get("supersedes") or [])), None)
            if replaced_by:
                item["note"] = f"Đã được thay bằng '{replaced_by}': hiện lại thì phải ẩn '{replaced_by}' mới dùng được."
            hidden.append(item)
        return {"active": active, "hidden": hidden}

    def _active_path(self, profile_id: str) -> Path:
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise KeyError(f"Không có profile '{profile_id}'.")
        if profile_id in self.BUILTIN or not profile.get("_path"):
            raise PermissionError("Đây là profile dự phòng của tool, không ẩn hay xoá được.")
        return Path(profile["_path"])

    def _hidden_path(self, filename: str) -> Path:
        path = self.hidden_dir / Path(filename).name
        if path.name != filename or path.suffix != ".yaml" or not path.exists():
            raise KeyError(f"Không có profile ẩn '{filename}'.")
        return path

    def hide(self, profile_id: str) -> str:
        source = self._active_path(profile_id)
        self.hidden_dir.mkdir(parents=True, exist_ok=True)
        target = self.hidden_dir / source.name
        n = 2
        while target.exists():   # đã có bản ẩn cùng tên: giữ cả hai
            target = self.hidden_dir / f"{source.stem}_{n}.yaml"
            n += 1
        source.replace(target)
        self.reload()
        return target.name

    def show(self, filename: str) -> str:
        source = self._hidden_path(filename)
        target = self.directory / source.name
        if target.exists():
            raise FileExistsError(f"Thư mục profiles/ đã có file {target.name}. Ẩn hoặc xoá file đó trước.")
        source.replace(target)
        self.reload()
        return target.name

    def delete(self, profile_id: Optional[str] = None, hidden_file: Optional[str] = None) -> str:
        path = self._hidden_path(hidden_file) if hidden_file else self._active_path(profile_id or "")
        path.unlink()
        self.reload()
        return path.name

    def summary(self) -> list[dict]:
        return [
            {
                "id": p["id"],
                "title": p.get("title", p["id"]),
                "description": p.get("description", ""),
                "mode": p.get("mode", "instance"),
            }
            for p in self.profiles.values()
        ]

    @staticmethod
    def fits(profile: dict, purpose: str) -> bool:
        """Profile tracking chỉ dùng cho việc theo dõi; các profile khác cho từng frame."""
        return (profile.get("mode") == "tracking") == (purpose == "track")

    def pick(self, label_names: list[str], purpose: str = "frame", label_types: Optional[list[str]] = None,
             labels: Optional[list[dict]] = None) -> dict:
        """Chọn profile khớp nhiều label nhất; không đủ khớp thì dùng profile dự phòng.

        `labels` (label đầy đủ của task, kèm các điểm của skeleton) cho phép nhận profile keypoints cả khi tên label
        trong task khác tên trong guideline, ví dụ 'person_vf17'.
        """
        names = [norm(n) for n in label_names]
        fallback = self.profiles[FALLBACK.get(purpose, "generic")]
        skeleton = purpose == "frame" and "skeleton" in (label_types or [])
        best, best_score = fallback, 0.0
        for profile in self.profiles.values():
            if not profile["labels"] or not names or not self.fits(profile, purpose) or profile.get("auto_pick") is False:
                continue  # auto_pick: false = chỉ dùng khi được chọn đích danh
            if skeleton != (profile.get("mode") == "keypoints"):
                continue  # task có skeleton thì chỉ xét profile keypoints, và ngược lại
            if skeleton and labels:
                hits = len(match_skeletons(labels, profile["labels"]))
            else:
                hits = sum(1 for n in names if n in profile["_index"])
            coverage = hits / len(names)
            signature = [norm(s) for s in profile.get("match_labels", [])]
            bonus = 0.25 if signature and all(s in names for s in signature) else 0.0
            if coverage + bonus > best_score:
                best, best_score = profile, coverage + bonus
        return best if best_score >= 0.5 else fallback

    def get(
        self, profile_id: Optional[str], label_names: list[str], purpose: str = "frame",
        label_types: Optional[list[str]] = None, labels: Optional[list[dict]] = None,
    ) -> dict:
        chosen = self.profiles.get(profile_id or "")
        if chosen and profile_id != "auto" and self.fits(chosen, purpose):
            return chosen
        return self.pick(label_names, purpose, label_types, labels)


def resolve_labels(profile: dict, cvat_labels: list[dict], overrides: Optional[dict] = None) -> list[dict]:
    """Ghép label của task CVAT với profile, áp các chỉnh sửa người dùng nhập trên panel."""
    overrides = overrides or {}
    defaults = profile.get("defaults", {})
    mode = profile.get("mode", "instance")
    semantic = mode == "semantic"
    sem_shape = (profile.get("rules", {}).get("semantic", {}) or {}).get("output_shape", "mask")
    resolved = []
    # Profile keypoints: ghép label với skeleton của profile theo tên, và khi tên khác thì theo cấu trúc (xem match_skeletons).
    assigned: dict[int, tuple[dict, str]] = {}
    if mode == "keypoints":
        forced = {i: str(overrides[str(l["id"])]["entry"]) for i, l in enumerate(cvat_labels)
                  if (overrides.get(str(l["id"])) or {}).get("entry")}
        assigned = match_skeletons(cvat_labels, profile["labels"], forced)
    for position, label in enumerate(cvat_labels):
        entry, how = assigned.get(position, (None if mode == "keypoints" else profile["_index"].get(norm(label["name"])), "exact"))
        cvat_type = (label.get("type") or "any").lower()
        note = ""
        if cvat_type == "skeleton" or (mode == "keypoints" and (entry or {}).get("shape") == "skeleton"):
            shape = "skeleton"
            supported = mode == "keypoints" and cvat_type in ("skeleton", "any") and entry is not None
            if mode != "keypoints":
                note = "Label skeleton: chọn profile keypoints để tool gợi ý được."
            elif entry is None:
                note = ("Tool chưa nhận ra skeleton này là skeleton nào của guideline (tên label và số điểm đều không khớp). "
                        "Chọn ở ô bên dưới.")
            elif how != "exact":
                note = f"Tool coi label này là '{entry['name']}' của guideline ({MATCH_HOW[how]})."
        elif mode == "keypoints":
            shape, supported = cvat_type, False
            note = "Profile keypoints chỉ gợi ý label skeleton."
        elif mode == "tracking":
            shape = "rectangle"
            supported = cvat_type in ("any", "rectangle")
            if not supported:
                note = f"Theo dõi chỉ hỗ trợ rectangle; label này là '{cvat_type}'."
        elif semantic:
            shape = cvat_type if cvat_type in ("mask", "polygon") else sem_shape
            supported = cvat_type in ("any", "mask", "polygon")
            if not supported:
                note = f"Label kiểu '{cvat_type}' không dùng được cho semantic segmentation."
        else:
            shape = (entry or {}).get("shape") or (cvat_type if cvat_type in SUPPORTED_SHAPES else "rectangle")
            supported = cvat_type == "any" or cvat_type in SUPPORTED_SHAPES
            if supported and cvat_type != "any" and cvat_type != shape:
                note = f"Guideline yêu cầu '{shape}' nhưng label trong task chỉ cho phép '{cvat_type}'."
                shape = cvat_type
            if not supported:
                note = f"Label kiểu '{cvat_type}' chưa được hỗ trợ."

        role = (entry or {}).get("role") or (
            "semantic" if semantic else ("lane" if shape == "polyline" else "object")
        )
        if shape == "skeleton":
            role = (entry or {}).get("kind", "body")
        cfg: dict[str, Any] = {
            "label_id": label["id"],
            "name": label["name"],
            "cvat_type": cvat_type,
            "attributes": [norm(a.get("name", "")) for a in label.get("attributes", [])],
            "matched": entry is not None,
            "entry": (entry or {}).get("name"),   # tên skeleton/label trong profile mà label này được ghép vào
            "match_how": how if entry is not None else None,
            "supported": supported,
            # Label có trong guideline thì bật sẵn. Với profile 'generic' bật hết.
            "enabled": supported and (entry is not None or not profile["labels"]),
            "shape": shape,
            "role": role,
            # Profile ghi `prompts: []` nghĩa là label này được SUY RA bằng luật (vd. rider, area/alternative).
            "prompts": list(entry["prompts"] or []) if entry and "prompts" in entry
            else ([] if role == "drivable_alt" else [norm(label["name"]).replace("/", " ")]),
            "threshold": float((entry or {}).get("threshold", defaults.get("threshold", 0.5))),
            "group": (entry or {}).get("group"),
            "lane": (entry or {}).get("lane"),
            "polyline_mode": (entry or {}).get("polyline_mode", "centerline"),
            "merge_px": (entry or {}).get("merge_px"),
            "join_dashes": (entry or {}).get("join_dashes", defaults.get("join_dashes", True)),
            "min_area_px": float((entry or {}).get("min_area_px", defaults.get("min_area_px", 30))),
            "note": note,
        }
        if shape == "skeleton":
            cfg.update(skeleton_config(entry or {}, label))
            if cfg.get("missing"):
                note = (note + " " if note else "") + "Task không có điểm: " + ", ".join(cfg["missing"][:8]) + "."
            if cfg.get("matched_by_order"):
                note = (note + " " if note else "") + "Tên điểm không khớp profile: tool ghép theo thứ tự điểm. Kiểm tra kỹ kết quả đầu tiên."
            cfg["note"] = note

        ov = overrides.get(str(label["id"])) or {}
        if "enabled" in ov:
            cfg["enabled"] = bool(ov["enabled"]) and supported
        if ov.get("prompts"):
            cfg["prompts"] = [str(p).strip() for p in ov["prompts"] if str(p).strip()]
        if ov.get("threshold") is not None:
            cfg["threshold"] = min(0.99, max(0.05, float(ov["threshold"])))
        if mode == "instance" and ov.get("shape") in SUPPORTED_SHAPES and cvat_type in ("any", ov["shape"]):
            cfg["shape"] = ov["shape"]
            if cfg["role"] in ("object", "lane"):
                cfg["role"] = "lane" if cfg["shape"] == "polyline" else "object"
        resolved.append(cfg)
    return resolved


def _plain(text: str) -> str:
    """Tên rút gọn để so khớp: bỏ phần trong ngoặc, dấu cách, gạch; 'Right Eye (R)' -> 'righteye'."""
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", str(text).lower()))


def _body_aliases(source: str) -> set[str]:
    """Các cách viết thường gặp của một khớp: right_eye, r_eye, eye_r, RightEye..."""
    side, _, joint = source.partition("_")
    if side not in ("left", "right") or not joint:
        return {_plain(source)}
    joint = _plain(joint)
    return {side + joint, joint + side, side[0] + joint, joint + side[0]}


def skeleton_config(entry: dict, label: dict) -> dict:
    """Ghép các điểm trong profile với các điểm (sublabel) của skeleton trong task.

    Thứ tự ưu tiên: khớp theo TÊN (kể cả các cách viết thường gặp), rồi theo SỐ trong tên
    ('2', 'kp_2', '2 - Right Eye'), cuối cùng theo THỨ TỰ nếu số điểm bằng nhau mà không tên nào khớp.
    Điểm của task không ghép được với điểm nào trong profile luôn là outside.

    Mỗi điểm trong profile có một trong hai nguồn toạ độ:
      - `source`: tên khớp COCO của model tư thế (người), ví dụ right_eye;
      - `path` + `t`: vị trí trên một đường nối các điểm của MediaPipe (mặt), ví dụ 1/4 chiều rộng mắt.
    """
    specs = []
    for i, item in enumerate(entry.get("keypoints") or []):
        if isinstance(item, str):
            item = {"name": item}
        item = dict(item)
        item.setdefault("name", str(item.get("id", i)))
        source = item.get("source") or (item["name"] if item["name"] in COCO17 else None)
        item["index"] = COCO17.index(source) if source in COCO17 else item.get("index")
        names = {_plain(item["name"])} | {_plain(a) for a in item.get("aliases", [])}
        if source in COCO17:
            names |= _body_aliases(source)
            item.setdefault("side", {"left": "L", "right": "R"}.get(source.split("_")[0]))
        item["_names"] = {n for n in names if n}
        item["_pos"] = i
        specs.append(item)

    subs = label.get("sublabels") or []
    matched: dict[int, dict] = {}  # vị trí sublabel -> spec
    taken: set[int] = set()
    for si, sub in enumerate(subs):  # 1. theo tên
        plain = _plain(sub["name"])
        if plain.isdigit():
            continue  # tên chỉ là số: để bước 2 xử lý cả bộ một lượt
        letters = re.sub(r"\d", "", plain)
        hit = next((sp for sp in specs if sp["_pos"] not in taken and (plain in sp["_names"] or (letters and letters in sp["_names"]))), None)
        if hit:
            matched[si] = hit
            taken.add(hit["_pos"])
    # 2. theo số trong tên. Chỉ nhận khi MỌI điểm còn lại đều ghép được: nếu task đánh số 0-16 còn guideline
    #    đánh 1-17 thì ghép theo số sẽ lệch cả bộ một cách âm thầm, nên khi đó bỏ, để bước 3 ghép theo thứ tự.
    by_number: dict[int, dict] = {}
    used = set(taken)
    for si, sub in enumerate(subs):
        if si in matched:
            continue
        numbers = re.findall(r"\d+", str(sub["name"]))
        hit = None
        if len(numbers) == 1:
            hit = next((sp for sp in specs if sp["_pos"] not in used and "id" in sp and int(sp["id"]) == int(numbers[0])), None)
        if hit is None:
            by_number = {}
            break
        by_number[si] = hit
        used.add(hit["_pos"])
    if by_number:
        matched.update(by_number)
        taken = used
    by_order = False
    if not matched and subs and len(subs) == len(specs):  # 3. theo thứ tự
        matched = {si: specs[si] for si in range(len(subs))}
        taken = set(range(len(specs)))
        by_order = True

    points = []
    for si, sub in enumerate(subs):
        sp = matched.get(si)
        point = {"label_id": sub["id"], "name": sub["name"], "index": None}
        if sp:
            point.update({k: sp[k] for k in ("index", "side", "path", "t", "by", "id") if sp.get(k) is not None})
            point["spec"] = sp["_pos"]
        points.append(point)
    where = {p["spec"]: i for i, p in enumerate(points) if "spec" in p}
    pairs = entry.get("edges")
    if pairs is None and entry.get("contour") and specs:
        n = len(specs)
        pairs = [[i, i + 1] for i in range(1, n)] + ([[n, 1]] if entry["contour"] == "closed" else [])
    edges = [[where[a - 1], where[b - 1]] for a, b in pairs or [] if a - 1 in where and b - 1 in where]
    return {
        "keypoints": points,
        "edges": edges,
        "missing": [sp["name"] for sp in specs if sp["_pos"] not in taken],
        "matched_by_order": by_order,
        "closed": entry.get("contour") == "closed",
        "inside_of": entry.get("inside_of"),
        "face_side": entry.get("face_side"),
        "min_visible": entry.get("min_visible"),
    }
