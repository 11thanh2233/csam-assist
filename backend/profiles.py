"""Profile = guideline viết dưới dạng máy đọc được (YAML trong thư mục profiles/).

Mỗi profile ánh xạ tên label trong CVAT sang: prompt cho SAM3, loại shape, ngưỡng tin cậy
và các luật hậu xử lý. Label không có trong profile vẫn dùng được: prompt mặc định là
chính tên label.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml

SUPPORTED_SHAPES = ("rectangle", "polygon", "polyline", "mask")


def norm(name: str) -> str:
    """Chuẩn hoá tên label để so khớp: 'Traffic_Light' == 'traffic light'."""
    return re.sub(r"[\s_\-]+", " ", str(name).strip().lower())


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
            data.setdefault("labels", [])
            data.setdefault("rules", {})
            data.setdefault("defaults", {})
            data["_index"] = {}
            for entry in data["labels"]:
                for key in [entry["name"], *entry.get("aliases", [])]:
                    data["_index"][norm(key)] = entry
            self.profiles[data["id"]] = data
        if "generic" not in self.profiles:
            self.profiles["generic"] = {
                "id": "generic", "title": "Generic", "labels": [], "rules": {}, "defaults": {}, "_index": {},
            }

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

    def pick(self, label_names: list[str]) -> dict:
        """Chọn profile khớp nhiều label nhất; không đủ khớp thì dùng 'generic'."""
        names = [norm(n) for n in label_names]
        best, best_score = self.profiles["generic"], 0.0
        for profile in self.profiles.values():
            if not profile["labels"] or not names:
                continue
            hits = sum(1 for n in names if n in profile["_index"])
            coverage = hits / len(names)
            signature = [norm(s) for s in profile.get("match_labels", [])]
            bonus = 0.25 if signature and all(s in names for s in signature) else 0.0
            if coverage + bonus > best_score:
                best, best_score = profile, coverage + bonus
        return best if best_score >= 0.5 else self.profiles["generic"]

    def get(self, profile_id: Optional[str], label_names: list[str]) -> dict:
        if profile_id and profile_id != "auto" and profile_id in self.profiles:
            return self.profiles[profile_id]
        return self.pick(label_names)


def resolve_labels(profile: dict, cvat_labels: list[dict], overrides: Optional[dict] = None) -> list[dict]:
    """Ghép label của task CVAT với profile, áp các chỉnh sửa người dùng nhập trên panel."""
    overrides = overrides or {}
    defaults = profile.get("defaults", {})
    semantic = profile.get("mode") == "semantic"
    sem_shape = (profile.get("rules", {}).get("semantic", {}) or {}).get("output_shape", "mask")
    resolved = []
    for label in cvat_labels:
        entry = profile["_index"].get(norm(label["name"]))
        cvat_type = (label.get("type") or "any").lower()
        note = ""
        if semantic:
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

        role = (entry or {}).get("role") or ("semantic" if semantic else ("lane" if shape == "polyline" else "object"))
        cfg: dict[str, Any] = {
            "label_id": label["id"],
            "name": label["name"],
            "cvat_type": cvat_type,
            "attributes": [norm(a.get("name", "")) for a in label.get("attributes", [])],
            "matched": entry is not None,
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
            "join_dashes": (entry or {}).get("join_dashes", defaults.get("join_dashes", True)),
            "min_area_px": float((entry or {}).get("min_area_px", defaults.get("min_area_px", 30))),
            "note": note,
        }

        ov = overrides.get(str(label["id"])) or {}
        if "enabled" in ov:
            cfg["enabled"] = bool(ov["enabled"]) and supported
        if ov.get("prompts"):
            cfg["prompts"] = [str(p).strip() for p in ov["prompts"] if str(p).strip()]
        if ov.get("threshold") is not None:
            cfg["threshold"] = min(0.99, max(0.05, float(ov["threshold"])))
        if not semantic and ov.get("shape") in SUPPORTED_SHAPES and cvat_type in ("any", ov["shape"]):
            cfg["shape"] = ov["shape"]
            if cfg["role"] in ("object", "lane"):
                cfg["role"] = "lane" if cfg["shape"] == "polyline" else "object"
        resolved.append(cfg)
    return resolved
