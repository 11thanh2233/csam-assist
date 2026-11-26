"""0.9.9: SAM3 ở job 3D không còn đòi cứng 6 GB VRAM trống.

- Ngưỡng tính theo card: min(3.5 GB, 55% tổng VRAM), hoặc số bạn đặt (SAM3_3D_SAM3_MIN_FREE_MB).
- Chế độ trong Cài đặt của app: auto | always (luôn nạp) | off (không dùng SAM3 ở job 3D).
- Nạp / chạy SAM3 mà thiếu VRAM: nhả SAM3, báo lý do, vẫn trả cuboid (không 500, backend không sập).
- Panel biết đang lọc bằng gì: /health -> lidar3d.sam3_mode, kết quả frame -> camera.engine (rtdetr | sam3 | skip).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import backend.app as A
import backend.runtime_tools as RT

ROOT = Path(__file__).resolve().parents[1]


class Proxy:
    """Như EngineProxy: loaded / ensure / unload. ensure() có thể ném lỗi thiếu VRAM."""

    def __init__(self, loaded=False, fail=None):
        self.loaded, self.fail, self.ensured, self.unloaded = loaded, fail, 0, []

    def ensure(self):
        self.ensured += 1
        if self.fail:
            raise self.fail
        self.loaded = True
        return self

    def unload(self, reason=""):
        self.unloaded.append(reason)
        self.loaded = False
        return True


@pytest.fixture
def sam3(monkeypatch):
    def make(mode="auto", free=None, total=None, min_free=0, **kw):
        eng = Proxy(**kw)
        monkeypatch.setattr(A.rt, "engine", eng)
        monkeypatch.setattr(A.settings, "sam3_3d_mode", mode)
        monkeypatch.setattr(A.settings, "sam3_3d_min_free_mb", min_free)
        mem = None if free is None else {"free_mb": free, "total_mb": total or free}
        monkeypatch.setattr(RT, "cuda_mem", lambda: mem)
        monkeypatch.setattr(RT, "free_cuda", lambda: None)
        return eng
    return make


# ------------------------------------------------------------- ngưỡng ---
def test_need_follows_card_size(monkeypatch):
    monkeypatch.setattr(A.settings, "sam3_3d_min_free_mb", 0)
    assert A._sam3_3d_need(6144) == 3379, "card 6 GB: 55% tổng VRAM, không còn đòi 6000 MB"
    assert A._sam3_3d_need(4096) == 2252
    assert A._sam3_3d_need(8192) == 3500 and A._sam3_3d_need(24576) == 3500, "card lớn: 3.5 GB là đủ"
    monkeypatch.setattr(RT, "cuda_mem", lambda: None)
    assert A._sam3_3d_need(None) == 3500, "không đọc được tổng VRAM: 3.5 GB"
    monkeypatch.setattr(A.settings, "sam3_3d_min_free_mb", 5000)
    assert A._sam3_3d_need(6144) == 5000, "số bạn đặt bằng biến môi trường thắng"


def test_config_reads_mode_and_defaults(monkeypatch):
    from backend.config import Settings

    monkeypatch.delenv("SAM3_3D_SAM3_MODE", raising=False)
    monkeypatch.delenv("SAM3_3D_SAM3_MIN_FREE_MB", raising=False)
    s = Settings()
    assert s.sam3_3d_mode == "auto" and s.sam3_3d_min_free_mb == 0, "mặc định: tự động, ngưỡng theo card"
    monkeypatch.setenv("SAM3_3D_SAM3_MODE", "ALWAYS")
    assert Settings().sam3_3d_mode == "always"
    monkeypatch.setenv("SAM3_3D_SAM3_MODE", "bừa")
    assert Settings().sam3_3d_mode == "auto", "giá trị lạ: về tự động"


# ------------------------------------------------------------ chế độ ---
def test_auto_loads_on_6gb_card_with_4gb_free(sam3):
    """0.9.8: card 6 GB không bao giờ có đủ 6000 MB trống. Giờ 4 GB trống là nạp."""
    eng = sam3("auto", free=4000, total=6144)
    assert A._sam3_for_3d() == (True, "") and eng.ensured == 1


def test_auto_skips_when_vram_short_and_says_why(sam3):
    eng = sam3("auto", free=1500, total=6144)
    ok, why = A._sam3_for_3d()
    assert not ok and eng.ensured == 0
    assert "1500 MB < 3379 MB" in why and "Luôn nạp SAM3" in why and "RT-DETR" in why


def test_auto_without_gpu_does_not_load(sam3):
    eng = sam3("auto", free=None)
    ok, why = A._sam3_for_3d()
    assert not ok and eng.ensured == 0 and "Không đọc được VRAM" in why


def test_always_loads_even_when_vram_short(sam3):
    eng = sam3("always", free=900, total=6144)
    assert A._sam3_for_3d() == (True, "") and eng.ensured == 1, "Luôn nạp: bỏ qua ngưỡng"
    eng = sam3("always", free=None)
    assert A._sam3_for_3d()[0] and eng.ensured == 1, "Luôn nạp: cả khi không đọc được VRAM"


def test_off_never_uses_sam3_even_if_loaded(sam3):
    eng = sam3("off", free=20000, total=24576, loaded=True)
    ok, why = A._sam3_for_3d()
    assert not ok and eng.ensured == 0 and "tắt SAM3" in why


def test_already_loaded_is_used_without_vram_check(sam3):
    eng = sam3("auto", free=100, total=6144, loaded=True)
    assert A._sam3_for_3d() == (True, "") and eng.ensured == 0


# ------------------------------------------------------- chống sập ---
def test_oom_while_loading_releases_and_reports(sam3):
    eng = sam3("always", free=900, total=6144, fail=RuntimeError("CUDA out of memory. Tried to allocate 1.2 GiB"))
    ok, why = A._sam3_for_3d()
    assert not ok and eng.unloaded == ["thiếu VRAM ở job 3D"]
    assert why.startswith("Thiếu VRAM khi nạp SAM3: đã nhả SAM3") and "cuboid vẫn giữ" in why


def test_other_load_error_is_reported_not_raised(sam3):
    sam3("always", free=900, fail=OSError("model.safetensors missing"))
    ok, why = A._sam3_for_3d()
    assert not ok and why.startswith("Không nạp được SAM3") and "safetensors" in why


def test_is_oom_variants():
    class OutOfMemoryError(RuntimeError):
        pass

    assert A._is_oom(OutOfMemoryError("x")) and A._is_oom(MemoryError()) and A._is_oom(RuntimeError("CUDA out of memory"))
    assert not A._is_oom(ValueError("bad image")) and not A._is_oom(RuntimeError("CUDA error: illegal memory access"))


def _frame_args():
    img = np.zeros((90, 160, 3), np.uint8)
    images = [("CAM_FRONT.jpg", img)]
    objects = [{"id": 1, "center": [10.0, 0.0, -1.0], "size": [4.4, 1.8, 1.5], "yaw": 0.0, "label_id": 1, "cls": "car"}]
    info = {"wanted": {"car": 1}, "prompts": {1: "car"}, "calib": None}
    return images, objects, info


def test_camera_check_frame_survives_oom_inside_sam3(sam3, monkeypatch):
    import backend.lidar_assist as LA

    eng = sam3("auto", loaded=True)

    def boom(*a, **k):
        raise RuntimeError("CUDA out of memory while encoding image")

    monkeypatch.setattr(LA, "check", boom)
    images, objects, info = _frame_args()
    chk = A._camera_check_frame(images, objects, info, {}, None, None)
    assert chk["engine"] == "skip" and chk["note"].startswith("Thiếu VRAM khi SAM3 xem ảnh camera")
    assert eng.unloaded and objects[0].get("camera") is None, "cuboid giữ nguyên, chỉ không có kết quả camera"


def test_camera_check_frame_other_errors_still_raise(sam3, monkeypatch):
    import backend.lidar_assist as LA

    def bad(*a, **k):
        raise ValueError("bad calib")

    sam3("auto", loaded=True)
    monkeypatch.setattr(LA, "check", bad)
    images, objects, info = _frame_args()
    with pytest.raises(ValueError):
        A._camera_check_frame(images, objects, info, {}, None, None)


def test_camera_check_frame_marks_skip_and_sam3(sam3, monkeypatch):
    import backend.lidar_assist as LA

    sam3("off")
    images, objects, info = _frame_args()
    chk = A._camera_check_frame(images, objects, info, {}, None, None)
    assert chk["engine"] == "skip" and "tắt SAM3" in chk["note"]
    sam3("auto", loaded=True)
    monkeypatch.setattr(LA, "check", lambda *a, **k: {"offset": 0.0, "offset_source": "default", "cameras": ["CAM_FRONT"], "results": {}})
    assert A._camera_check_frame(images, objects, info, {}, None, None)["engine"] == "sam3"


def test_health_reports_sam3_mode(monkeypatch):
    from fastapi.testclient import TestClient

    with TestClient(A.app, base_url="http://127.0.0.1:8765") as c:
        monkeypatch.setattr(A.settings, "sam3_3d_mode", "always")
        assert c.get("/health").json()["lidar3d"]["sam3_mode"] == "always"



# ------------------------------------------------------------- desktop ---
def _api(tmp_path):
    from desktop.components import Components
    from desktop.control import ControlApi
    from desktop.paths import Paths

    class Be:
        state = "stopped"

        def __init__(self):
            self.extra_env, self.auto_restart = {}, True

    paths = Paths(tmp_path / "data", app_dir=ROOT).ensure()
    return ControlApi(paths, Components(paths, manifest={}), Be(), open_site=lambda u: None, on_quit=lambda: None)


def test_desktop_setting_passes_mode_to_backend(tmp_path):
    api = _api(tmp_path)
    assert api._backend.extra_env["SAM3_3D_SAM3_MODE"] == "auto", "mặc định: Tự động"
    assert api.set_setting("sam3_3d_mode", "always") and api._backend.extra_env["SAM3_3D_SAM3_MODE"] == "always"
    assert not api.set_setting("sam3_3d_mode", "bừa") and api._backend.extra_env["SAM3_3D_SAM3_MODE"] == "always"
    again = _api(tmp_path)
    assert again._backend.extra_env["SAM3_3D_SAM3_MODE"] == "always", "mở lại app: giữ lựa chọn"
    assert again._settings.data["sam3_3d_mode"] == "always"
    assert again.set_setting("sam3_3d_mode", "off") and again._backend.extra_env["SAM3_3D_SAM3_MODE"] == "off"


def test_desktop_bad_saved_mode_falls_back_to_auto(tmp_path):
    api = _api(tmp_path)
    api._settings.data["sam3_3d_mode"] = 42
    api._settings.save()
    assert _api(tmp_path)._backend.extra_env["SAM3_3D_SAM3_MODE"] == "auto"
    api._settings.data["sam3_3d_mode"] = "maybe"
    api._settings.save()
    assert _api(tmp_path)._backend.extra_env["SAM3_3D_SAM3_MODE"] == "auto"


def test_settings_page_has_mode_select():
    html = (ROOT / "desktop" / "ui" / "control.html").read_text(encoding="utf-8")
    assert 'id="sam3Mode3d"' in html and "setSetting('sam3_3d_mode'" in html
    for v in ("auto", "always", "off"):
        assert f'<option value="{v}">' in html


def test_panel_line_names_the_filter():
    js = (ROOT / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
    assert "function engines3d(perFrame)" in js and "engines3d(true)" in js
    for s in ("Lọc rác bằng ảnh: SAM3", "Lọc rác bằng ảnh: đang bỏ qua", "SAM3 nếu đủ VRAM"):
        assert s in js
