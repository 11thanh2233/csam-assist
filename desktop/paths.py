"""Thư mục của bản desktop.

- APP_DIR: code của tool (thư mục cài đặt). Bộ cài ghi đè khi cập nhật, nên không chứa dữ liệu của người dùng.
- DATA_DIR: dữ liệu của người dùng, giữ nguyên qua các lần cập nhật:
    packages/    PyTorch tải khi cài (gói nặng, không nằm trong bộ cài)
    models/      trọng số SAM 3, ViTPose, model khuôn mặt
    profiles/    profile (bản có sẵn được chép vào lần đầu, profile bạn tạo nằm cùng chỗ)
    guideline_library/  guideline, bài học, dữ liệu tự học (learning/)
    store/       cài đặt của bảng trong CVAT (thay cho bộ nhớ của Tampermonkey)
    webview/     cookie đăng nhập CVAT của cửa sổ app
    logs/        nhật ký
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    if os.getenv("SAM3_DESKTOP_DATA"):
        return Path(os.environ["SAM3_DESKTOP_DATA"])
    if sys.platform == "win32" and os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "SAM3CvatAssist"
    return Path.home() / ".sam3-cvat-assist"


class Paths:
    def __init__(self, data_dir: Path | None = None, app_dir: Path | None = None):
        self.app = Path(app_dir or APP_DIR)
        self.data = Path(data_dir or _default_data_dir())
        self.packages = self.data / "packages"
        self.models = self.data / "models"
        self.sam3 = self.models / "sam3"
        self.vitpose = self.models / "vitpose"
        self.face = self.models / "face_landmarker.task"
        self.downloads = self.data / "downloads"
        self.profiles = self.data / "profiles"
        self.library = self.data / "guideline_library"
        self.store = self.data / "store"
        self.webview = self.data / "webview"
        self.logs = self.data / "logs"
        self.settings = self.data / "settings.json"
        self.state = self.data / "components.json"

    def ensure(self) -> "Paths":
        for p in (self.packages, self.models, self.downloads, self.profiles, self.library, self.store, self.webview, self.logs):
            p.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def manifest(self) -> Path:
        return self.app / "desktop" / "manifest.lock.json"

    @property
    def sam_license(self) -> Path:
        return self.app / "desktop" / "licenses" / "SAM_LICENSE.txt"
