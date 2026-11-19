"""Đồ dùng chung cho test: engine kịch bản (trả mask định sẵn theo prompt) và bộ label mẫu."""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SAM3_ENGINE"] = "mock"
os.environ["SAM3_API_KEY"] = ""

# Thư viện guideline tạm cho test: không ghi bài học vào thư mục thật của repo.
import shutil  # noqa: E402
import tempfile  # noqa: E402

_LIB = Path(tempfile.mkdtemp(prefix="sam3-assist-lib-"))
shutil.copytree(ROOT / "guideline_library", _LIB, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("runs", "lessons.jsonl"))
os.environ["SAM3_LIBRARY_DIR"] = str(_LIB)
# Profile tạm: nạp guideline trong test ghi profile mới, không được ghi vào thư mục profiles/ thật.
_PROFILES = Path(tempfile.mkdtemp(prefix="sam3-assist-profiles-"))
shutil.copytree(ROOT / "profiles", _PROFILES, dirs_exist_ok=True)
os.environ["SAM3_PROFILES_DIR"] = str(_PROFILES)
os.environ["GUIDELINE_LLM"] = "none"

from backend.engines.base import Engine, Instance  # noqa: E402
from backend.profiles import ProfileStore  # noqa: E402

W, H = 1280, 720


class ScriptedEngine(Engine):
    """Trả về đúng các mask đã khai báo cho từng prompt."""

    name = "scripted"

    def __init__(self, script):
        self.script = script

    def encode(self, image_rgb):
        return {}

    def segment_text(self, state, prompt, threshold):
        result = self.script.get(prompt, [])
        if isinstance(result, Exception):
            raise result
        return [Instance(m, s) for m, s in result if s >= threshold]

    def segment_box(self, state, box_xyxy, prompt, threshold, negatives=None):
        return [Instance(m, s) for m, s in self.script.get("__box__", [])]


def rect(x1, y1, x2, y2):
    mask = np.zeros((H, W), bool)
    mask[y1:y2, x1:x2] = True
    return mask


@pytest.fixture(scope="session")
def store():
    return ProfileStore(ROOT / "profiles")


def cvat_labels(names, kind="any", with_truncated=True):
    labels = []
    for i, name in enumerate(names):
        attrs = []
        if with_truncated:
            attrs = [{"id": 1000 + i, "name": "truncated", "input_type": "checkbox", "default_value": "false"}]
        labels.append({"id": i + 1, "name": name, "type": kind, "attributes": attrs})
    return labels


BDD_NAMES = [
    "pedestrian", "rider", "car", "truck", "bus", "train", "motorcycle", "bicycle", "traffic light",
    "traffic sign", "area/direct", "area/alternative", "lane/crosswalk", "lane/double white",
    "lane/double yellow", "lane/double other", "lane/road curb", "lane/single white",
    "lane/single yellow", "lane/single other",
]
SEG_NAMES = [
    "road", "sidewalk", "building", "wall", "fence", "pole", "traffic_light", "traffic_sign", "vegetation",
    "terrain", "sky", "person", "rider", "car", "truck", "bus", "train", "motorcycle", "bicycle",
]


@pytest.fixture()
def library(tmp_path):
    """Một thư viện guideline sạch (chỉ có guideline mẫu và bài học khởi đầu) cho từng test."""
    from backend.guideline.library import Library

    shutil.copytree(ROOT / "guideline_library", tmp_path / "lib", ignore=shutil.ignore_patterns("runs", "lessons.jsonl"))
    return Library(tmp_path / "lib")
