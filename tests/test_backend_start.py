"""Bật backend thật bằng BackendProcess (đúng đường nút "Bật" và "Mở CVAT" của app đi qua).

Lỗi 0.8.0–0.8.3: tham số của subprocess bị ghi đè nên bấm Bật / Mở CVAT không có phản ứng gì. Test này chạy tiến trình
backend thật (engine giả lập) cho tới khi /health trả lời, rồi tắt.
"""
import sys
import time
import types

from conftest import ROOT


def test_backend_process_really_starts_and_stops(tmp_path):
    from desktop.backend_proc import BackendProcess

    logs = tmp_path / "logs"
    logs.mkdir()
    paths = types.SimpleNamespace(app=ROOT, logs=logs, packages=tmp_path / "pkgs", data=tmp_path / "data")
    comps = types.SimpleNamespace(paths=paths, system=True, status=lambda: {"ready": True}, backend_env=lambda: {})
    be = BackendProcess(comps, preferred_port=8899, python=sys.executable)
    be.extra_env = {"SAM3_ENGINE": "mock", "SAM3_WARMUP": "0"}
    info = be.start()
    try:
        assert info["state"] == "starting", info
        deadline = time.time() + 90
        while time.time() < deadline and be.state != "running":
            assert be.state != "error", be.message
            time.sleep(0.5)
        assert be.state == "running", (be.state, be.message, be.tail(30))
        assert be.health.get("ok")
        status, body = be.request("GET", "/health", None)
        assert status == 200
    finally:
        be.stop()
    assert be.state == "stopped" and be.proc is None
    assert "starting backend" in (logs / "backend.log").read_text(encoding="utf-8")


def test_start_failure_is_reported_not_silent(tmp_path):
    from desktop.backend_proc import BackendProcess

    logs = tmp_path / "logs"
    logs.mkdir()
    paths = types.SimpleNamespace(app=ROOT, logs=logs, packages=tmp_path, data=tmp_path)
    comps = types.SimpleNamespace(paths=paths, system=True, status=lambda: {"ready": True}, backend_env=lambda: {})
    be = BackendProcess(comps, preferred_port=8898, python=str(tmp_path / "khong-co-python.exe"))
    info = be.start()
    assert info["state"] == "error" and "Không chạy được backend" in info["message"]
    assert "Could not start backend process" in (logs / "backend.log").read_text(encoding="utf-8")
