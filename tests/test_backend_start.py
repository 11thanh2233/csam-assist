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


def _running_backend(tmp_path, port):
    from desktop.backend_proc import BackendProcess

    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    paths = types.SimpleNamespace(app=ROOT, logs=logs, packages=tmp_path / "pkgs", data=tmp_path / "data")
    comps = types.SimpleNamespace(paths=paths, system=True, status=lambda: {"ready": True}, backend_env=lambda: {})
    be = BackendProcess(comps, preferred_port=port, python=sys.executable)
    be.extra_env = {"SAM3_ENGINE": "mock", "SAM3_WARMUP": "0"}
    be.start()
    deadline = time.time() + 90
    while time.time() < deadline and be.state != "running":
        time.sleep(0.3)
    assert be.state == "running", be.tail(30)
    return be


def test_backend_crash_is_restarted_automatically(tmp_path):
    """0.9: backend đang chạy mà tắt bất thường thì tự bật lại, có ghi vào nhật ký."""
    be = _running_backend(tmp_path, 8897)
    seen = []
    be.on_change = seen.append
    try:
        first = be.proc
        first.kill()
        deadline = time.time() + 90
        while time.time() < deadline and not (be.state == "running" and be.proc is not first):
            time.sleep(0.3)
        assert be.state == "running" and be.proc is not first, (be.state, be.message)
        assert "error" in seen and "running" in seen
        assert "Auto-restart 1/3" in be.tail(50)
    finally:
        be.stop()


def test_crash_without_auto_restart_shows_problem_with_steps(tmp_path):
    be = _running_backend(tmp_path, 8896)
    be.auto_restart = False
    try:
        be.proc.kill()
        deadline = time.time() + 20
        while time.time() < deadline and be.state != "error":
            time.sleep(0.2)
        info = be.info()
        assert info["state"] == "error"
        p = info["problem"]
        assert p["title"] and p["steps"] and any(s["action"] == "retry" for s in p["steps"])
    finally:
        be.stop()
    assert be.info()["problem"] is None


def test_diagnose_known_causes():
    from desktop.backend_proc import diagnose

    assert diagnose("", 0xC0000135)["steps"][0]["action"] == "vcredist"
    assert diagnose("OSError: [WinError 126] ... c10.dll", 1)["id"] == "vcredist"
    assert diagnose("torch.OutOfMemoryError: CUDA out of memory", 1)["id"] == "gpu_oom"
    assert diagnose("ModuleNotFoundError: No module named 'torch'", 1)["id"] == "broken_install"
    assert diagnose("", 0, start_error="TypeError: boom")["steps"][0]["action"] == "report"
    assert diagnose("nothing useful", 1) is None
