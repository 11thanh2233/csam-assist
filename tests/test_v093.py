"""Kiểm thử bản 0.9.3: AI soạn guideline cấu hình trong app, chép không còn OverflowError, CVAT trên máy này."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ clipboard ---
class _Fn:
    """Hàm Windows giả: bắt buộc đã khai báo argtypes, và giữ nguyên số 64 bit như ctypes khi có khai báo."""

    def __init__(self, name, ret, calls):
        self.name, self.ret, self.calls, self.argtypes, self.restype = name, ret, calls, None, None

    def __call__(self, *args):
        if self.argtypes is None:
            raise AssertionError(f"{self.name} chưa khai báo argtypes: handle 64 bit sẽ OverflowError")
        assert len(args) == len(self.argtypes), self.name
        self.calls.append((self.name, args))
        return self.ret(*args) if callable(self.ret) else self.ret


class _Dll:
    def __init__(self, table, calls):
        for name, ret in table.items():
            setattr(self, name, _Fn(name, ret, calls))


def _fake_ctypes(calls, mem):
    import ctypes as real

    big = 0x7FF8_1234_5678          # handle trên Windows 64-bit lớn hơn int 32 bit
    dlls = {
        "user32": {"OpenClipboard": 1, "EmptyClipboard": 1, "CloseClipboard": 1, "SetClipboardData": big},
        "kernel32": {"GlobalAlloc": big, "GlobalLock": 0x1000, "GlobalUnlock": 0, "GlobalFree": 0},
    }

    class C:
        c_size_t = real.c_size_t

        @staticmethod
        def WinDLL(name, use_last_error=False):  # noqa: N802
            return _Dll(dlls[name], calls)

        @staticmethod
        def memmove(dst, src, n):
            mem.append((dst, bytes(src[:n])))

    return C, big


def test_clipboard_declares_every_signature_and_keeps_64bit_handles():
    from desktop.control import _win_set_clipboard

    calls, mem = [], []
    fake, big = _fake_ctypes(calls, mem)
    assert _win_set_clipboard(fake, "Lỗi ✓") is True
    names = [c[0] for c in calls]
    assert names == ["OpenClipboard", "EmptyClipboard", "GlobalAlloc", "GlobalLock", "GlobalUnlock", "SetClipboardData", "CloseClipboard"]
    assert ("GlobalUnlock", (big,)) in calls, "handle 64 bit đi qua nguyên vẹn (0.9.2 báo OverflowError ở đây)"
    assert ("SetClipboardData", (13, big)) in calls
    assert mem[0][1] == "Lỗi ✓".encode("utf-16-le") + b"\x00\x00"


def test_clipboard_frees_memory_when_windows_refuses():
    from desktop.control import _win_set_clipboard

    calls, mem = [], []
    fake, big = _fake_ctypes(calls, mem)
    orig = fake.WinDLL

    def refusing(name, use_last_error=False):
        dll = orig(name, use_last_error)
        if name == "user32":
            dll.SetClipboardData.ret = 0
        return dll

    fake.WinDLL = staticmethod(refusing)
    assert _win_set_clipboard(fake, "x") is False
    assert ("GlobalFree", (big,)) in calls and calls[-1][0] == "CloseClipboard"


def test_no_shared_windll_without_signatures():
    """Không còn gọi hàm Windows nhận handle qua ctypes.windll dùng chung mà thiếu khai báo kiểu."""
    app = (ROOT / "desktop" / "app.py").read_text(encoding="utf-8")
    control = (ROOT / "desktop" / "control.py").read_text(encoding="utf-8")
    assert "GlobalLock" not in app and "windll.kernel32.Global" not in control
    assert "_win_set_clipboard(ctypes" in app, "panel trong cửa sổ CVAT chép qua cùng hàm đã sửa"


# ------------------------------------------------------- AI soạn guideline ---
@pytest.fixture()
def keyring_mem(monkeypatch):
    from desktop import credstore

    box = {}
    monkeypatch.setattr(credstore, "available", lambda: True)
    monkeypatch.setattr(credstore, "get", lambda user=credstore.USER: box.get(user, ""))
    monkeypatch.setattr(credstore, "set", lambda token, user=credstore.USER: box.__setitem__(user, token))
    monkeypatch.setattr(credstore, "delete", lambda user=credstore.USER: box.pop(user, None))
    return box


class _Be:
    state = "running"

    def __init__(self):
        self.extra_env, self.auto_restart = {}, True


def _api(tmp_path, be=None, opened=None):
    from desktop.components import Components
    from desktop.control import ControlApi
    from desktop.paths import Paths

    paths = Paths(tmp_path / "data", app_dir=ROOT).ensure()
    return ControlApi(paths, Components(paths, manifest={}), be or _Be(), open_site=(opened if opened is not None else []).append,
                      on_quit=lambda: None)


def test_llm_settings_go_to_backend_env_and_key_stays_out_of_files(tmp_path, keyring_mem, monkeypatch):
    from backend.guideline import llm as L
    from desktop import credstore

    be = _Be()
    api = _api(tmp_path, be)
    assert api.llm_settings()["provider"] == "none" and not api.llm_settings()["active"]
    assert "GUIDELINE_LLM" not in be.extra_env, "chưa cấu hình: backend vẫn đọc biến môi trường của Windows như bản cũ"

    r = api.set_llm("anthropic", "", "", None)
    assert not r["ok"] and "API key" in r["message"], "Claude cần key"
    r = api.set_llm("anthropic", "", "", "sk-ant-test")
    assert r["ok"] and r["restart"], "backend đang chạy: nhắc bật lại"
    assert keyring_mem[credstore.LLM_USER] == "sk-ant-test" and keyring_mem.get(credstore.USER) is None, "không đụng token Hugging Face"
    assert "sk-ant" not in api._paths.settings.read_text(encoding="utf-8"), "key không ghi ra settings.json"
    assert be.extra_env["GUIDELINE_LLM"] == "anthropic" and be.extra_env["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert be.extra_env["GUIDELINE_LLM_MODEL"] == "claude-sonnet-5-5"
    for k, v in be.extra_env.items():
        monkeypatch.setenv(k, v)
    got = L.from_env()
    assert isinstance(got, L.AnthropicLLM) and got.model == "claude-sonnet-5-5", "backend đọc đúng cấu hình của app"
    st = api.llm_settings()
    assert st["active"] and st["has_key"] and "sk-ant" not in json.dumps(st), "giao diện không nhận lại key"

    assert not api.set_llm("openai", "qwen", "localhost:11434", None)["ok"], "địa chỉ phải có http://"
    assert not api.set_llm("openai", "", "http://localhost:11434/v1", None)["ok"], "cần model"
    assert api.set_llm("openai", "qwen2.5-vl:7b", "http://localhost:11434/v1", "")["ok"]
    assert credstore.LLM_USER not in keyring_mem, "ô key để trống khi lưu = xoá key"
    assert be.extra_env["GUIDELINE_LLM"] == "openai" and be.extra_env["ANTHROPIC_API_KEY"] == ""
    for k, v in be.extra_env.items():
        monkeypatch.setenv(k, v)
    got = L.from_env()
    assert isinstance(got, L.OpenAICompatLLM) and got.describe()["local"], "Ollama trên máy: ảnh ở lại máy"

    assert api.set_llm("none")["ok"]
    assert not any(k.startswith("GUIDELINE_") or k == "ANTHROPIC_API_KEY" for k in be.extra_env)
    assert not api.set_llm("bogus")["ok"]
    reopened = _api(tmp_path, _Be())
    assert reopened.llm_settings()["provider"] == "none"


def test_llm_settings_survive_restart_and_test_button(tmp_path, keyring_mem, monkeypatch):
    from backend.guideline import llm as L

    api = _api(tmp_path)
    api.set_llm("anthropic", "claude-opus-5-5", "", "sk-ant-x")
    be = _Be()
    again = _api(tmp_path, be)
    assert be.extra_env["GUIDELINE_LLM_MODEL"] == "claude-opus-5-5", "mở lại app: backend nhận cấu hình ngay khi bật"

    seen = {}

    class FakeLLM:
        def __init__(self, key, model, timeout=0):
            seen.update(key=key, model=model)

        def complete(self, system, text, images=None, max_tokens=0):
            assert images is None, "nút Kiểm tra không gửi ảnh"
            return "OK"

    monkeypatch.setattr(L, "AnthropicLLM", FakeLLM)
    r = again.test_llm()
    assert r["ok"] and "OK" in r["message"] and seen == {"key": "sk-ant-x", "model": "claude-opus-5-5"}

    class Broken(FakeLLM):
        def complete(self, *a, **k):
            raise L.LLMError("HTTP 401: invalid x-api-key")

    monkeypatch.setattr(L, "AnthropicLLM", Broken)
    r = again.test_llm()
    assert not r["ok"] and "401" in r["message"]
    again.set_llm("none")
    assert again.test_llm()["message"] == "Chưa cấu hình LLM."


def test_settings_with_wrong_llm_type_fall_back(tmp_path):
    from desktop.control import Settings

    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"llm": "anthropic", "cvat_dir": 5}), encoding="utf-8")
    s = Settings(f)
    assert s.data["llm"]["provider"] == "none" and s.data["cvat_dir"] == ""


def test_panel_points_to_the_app_setting():
    js = (ROOT / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
    assert "Backend chưa cấu hình LLM" not in js
    assert "Cài đặt → AI soạn guideline" in js
    html = (ROOT / "desktop" / "ui" / "control.html").read_text(encoding="utf-8")
    assert 'id="llmCard"' in html and "api().set_llm(" in html and "api().test_llm()" in html


# ------------------------------------------------------- CVAT trên máy này ---
WSL_OUT = "  NAME              STATE           VERSION\r\n* Ubuntu            Running         2\r\n  docker-desktop    Stopped         2\r\n"


class Machine:
    """Máy Windows giả: WSL2, Docker Desktop, các container CVAT."""

    def __init__(self, wsl=True, docker=True, engine=False, containers="stopped", project="cvat", folder=""):
        self.wsl, self.docker, self.engine, self.containers, self.project, self.folder = wsl, docker, engine, containers, project, folder
        self.cvat_up, self.cmds, self.launched = False, [], []

    def run(self, args, timeout=30, cwd=None):
        self.cmds.append((list(args), cwd))
        if args[0] == "wsl":
            return (0, WSL_OUT) if self.wsl else (127, "")
        sub = args[1]
        if not self.engine:
            return 1, "error during connect: Docker Desktop is not running"
        if sub == "info":
            return 0, "27.3.1"
        if sub == "ps":
            if self.containers == "none":
                return 0, ""
            state = "running" if self.containers == "running" else "exited"
            names = ["cvat_server", "cvat_db", "cvat_ui", "traefik", "cvat_redis_inmem"]
            other = f"portainer\trunning\tother\t\n"
            return 0, other + "".join(f"{n}\t{state}\t{self.project}\t{self.folder}\n" for n in names)
        if sub == "start":
            self.containers = "running"
            return 0, "\n".join(args[2:])
        if sub == "compose":
            self.containers = "running"
            return 0, "Container cvat_server Started"
        return 1, "?"

    def http(self, url, timeout=3):
        return self.engine and self.containers == "running"

    def popen(self, args, **kw):
        self.launched.append(args)
        self.engine = True


@pytest.fixture()
def machine(monkeypatch):
    from desktop import cvat_local as CL

    m = Machine()
    monkeypatch.setattr(CL, "docker_exe", lambda env=None: r"C:\Program Files\Docker\Docker\resources\bin\docker.exe" if m.docker else "")
    monkeypatch.setattr(CL, "docker_desktop_exe", lambda env=None: r"C:\Program Files\Docker\Docker\Docker Desktop.exe" if m.docker else "")
    return m


def _steps(res):
    return {s["key"]: s["ok"] for s in res["steps"]}


def test_parsers():
    from desktop import cvat_local as CL

    assert CL._decode(WSL_OUT.encode("utf-16-le")) == WSL_OUT, "wsl.exe in UTF-16"
    assert CL.parse_wsl(WSL_OUT) == [("Ubuntu", "Running", 2), ("docker-desktop", "Stopped", 2)]
    rows = CL.parse_containers("a\trunning\tother\t\ncvat-dev-cvat_server-1\texited\tcvat-dev\tD:\\cvat\nx\texited\tcvat-dev\t\n")
    assert [r["name"] for r in rows] == ["cvat-dev-cvat_server-1", "x"], "dự án compose tên khác (thư mục clone khác tên)"
    assert CL.is_this_machine("http://localhost:8080") and CL.is_this_machine("127.0.0.1:8080")
    assert not CL.is_this_machine("http://192.168.1.20:8080"), "máy khác trong mạng: app không bật hộ được"
    assert CL.origin_of("http://localhost:8080/tasks/1/jobs/2?frame=3") == "http://localhost:8080"


def test_check_reports_each_missing_piece(machine):
    from desktop import cvat_local as CL

    url = "http://localhost:8080"
    machine.docker = False
    r = CL.check(url, run=machine.run, http=machine.http)
    assert _steps(r) == {"wsl": True, "docker_cli": False, "docker_engine": None, "containers": None, "cvat_http": False}
    assert not r["can_start"] and r["steps"][1]["fix"] == {"link": "docker"}

    machine.docker, machine.wsl = True, False
    r = CL.check(url, run=machine.run, http=machine.http)
    assert _steps(r)["wsl"] is False and r["steps"][0]["fix"] == {"link": "wsl"}
    assert r["can_start"], "Docker Desktop đã cài (có thể chạy Hyper-V): vẫn thử khởi động"

    machine.wsl = True
    r = CL.check(url, run=machine.run, http=machine.http)
    assert _steps(r) == {"wsl": True, "docker_cli": True, "docker_engine": False, "containers": None, "cvat_http": False}
    assert r["can_start"], "Docker chưa chạy: app mở Docker Desktop"

    machine.engine = True
    r = CL.check(url, run=machine.run, http=machine.http)
    assert _steps(r)["containers"] is False and "0/5 đang chạy" in r["steps"][3]["detail"] and r["can_start"]
    assert len(r["containers"]) == 5, "chỉ container của CVAT, bỏ container khác"

    machine.containers = "none"
    r = CL.check(url, run=machine.run, http=machine.http)
    assert not r["can_start"] and r["steps"][3]["fix"] == {"link": "cvat", "pick": True}


def test_check_with_cvat_folder_and_ready(machine, tmp_path):
    from desktop import cvat_local as CL

    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    machine.engine, machine.containers = True, "none"
    r = CL.check("http://localhost:8080", run=machine.run, http=machine.http, cvat_dir=str(tmp_path))
    assert r["can_start"] and "docker compose up -d" in r["steps"][3]["detail"]
    machine.containers = "running"
    r = CL.check("http://localhost:8080", run=machine.run, http=machine.http)
    assert r["ready"] and len(r["steps"]) == 1, "CVAT đã chạy: không chạy lệnh nào"
    assert machine.cmds[-1][0][1] == "ps"


def test_starter_opens_docker_desktop_then_starts_containers(machine):
    from desktop import cvat_local as CL

    t = {"now": 0.0}
    st = CL.Starter(run=machine.run, http=machine.http, popen=machine.popen,
                    sleep=lambda s: t.__setitem__("now", t["now"] + s), clock=lambda: t["now"])
    ok, msg = st.run_steps("http://localhost:8080")
    assert ok and msg == "CVAT đã sẵn sàng."
    assert machine.launched == [[r"C:\Program Files\Docker\Docker\Docker Desktop.exe"]]
    started = [c for c, _ in machine.cmds if c[1] == "start"][0]
    assert started[2:] == ["cvat_server", "cvat_db", "cvat_ui", "traefik", "cvat_redis_inmem"], "chỉ bật container của CVAT"
    assert not any(c[1] == "compose" for c, _ in machine.cmds), "không có thư mục CVAT thì không chạy compose"
    assert any("Docker Desktop" in line for line in st.progress["log"])


def test_starter_uses_compose_folder_and_reports_timeouts(machine, tmp_path):
    from desktop import cvat_local as CL

    (tmp_path / "compose.yaml").write_text("services: {}\n")
    machine.engine, machine.folder = True, str(tmp_path)
    t = {"now": 0.0}
    tick = dict(sleep=lambda s: t.__setitem__("now", t["now"] + s), clock=lambda: t["now"])
    st = CL.Starter(run=machine.run, http=machine.http, popen=machine.popen, **tick)
    ok, _ = st.run_steps("http://localhost:8080")
    assert ok and ([c for c, cwd in machine.cmds if c[1] == "compose"][0][1:], machine.cmds[-1][1]) == (["compose", "up", "-d"], str(tmp_path))

    slow = Machine(engine=True)
    slow.http = lambda url, timeout=3: False
    st = CL.Starter(run=slow.run, http=slow.http, popen=slow.popen, **tick)
    ok, msg = st.run_steps("http://localhost:8080")
    assert not ok and "5 phút" in msg and "cvat_server" in msg

    stuck = Machine(engine=False)
    stuck.popen = lambda args, **kw: None                       # Docker Desktop không lên (lỗi WSL…)
    st = CL.Starter(run=stuck.run, http=stuck.http, popen=stuck.popen, **tick)
    ok, msg = st.run_steps("http://localhost:8080")
    assert not ok and "WSL" in msg


def test_starter_thread_and_cancel(machine):
    import threading

    from desktop import cvat_local as CL

    gate = threading.Event()
    machine.engine = True
    st = CL.Starter(run=machine.run, http=lambda url, timeout=3: gate.is_set(), popen=machine.popen, sleep=lambda s: gate.wait(0.01))
    assert st.start("http://localhost:8080") and not st.start("http://localhost:8080"), "không chạy hai lần cùng lúc"
    st.cancel.set()
    st.thread.join(5)
    assert st.progress["done"] and not st.progress["ok"] and st.progress["step"] == "Đã dừng."


def test_open_local_site_checks_first(tmp_path, monkeypatch, machine):
    from desktop import cvat_local as CL

    opened = []
    api = _api(tmp_path, opened=opened)
    api.add_site("CVAT máy này", "localhost:8080")
    monkeypatch.setattr(CL, "http_ok", lambda url, timeout=3: machine.http(url))
    r = api.open_site("http://localhost:8080")
    assert r["local_down"] and not opened, "CVAT chưa chạy: không mở trang lỗi kết nối"
    api._record_visit("http://localhost:8080/tasks/1/jobs/2")
    assert api.open_job("http://localhost:8080/tasks/1/jobs/2")["local_down"]
    assert api.open_site("https://cvat.edufun.ai")["ok"], "trang trên mạng: không kiểm Docker"
    api._cvat_starter = CL.Starter(run=machine.run, http=machine.http, popen=machine.popen, sleep=lambda s: None)
    monkeypatch.setattr(CL, "default_run", machine.run)
    res = api.cvat_local_check("http://localhost:8080")
    assert not res["ready"] and res["can_start"] and not res["starting"]
    assert not api.cvat_local_start("http://192.168.1.5:8080")["ok"], "chỉ bật CVAT của chính máy này"
    assert api.cvat_local_start("http://localhost:8080")["ok"]
    api._cvat_starter.thread.join(5)
    assert api.cvat_local_progress()["ok"]
    assert api.open_site("http://localhost:8080")["ok"] and opened[-1] == "http://localhost:8080"
    assert api.open_site("http://localhost:8080", True)["ok"]


def test_pick_cvat_folder(tmp_path):
    api = _api(tmp_path)
    empty, good = tmp_path / "empty", tmp_path / "cvat"
    empty.mkdir(), good.mkdir()
    (good / "docker-compose.yml").write_text("services: {}\n")
    api._pick_folder = lambda: str(empty)
    assert "docker-compose.yml" in api.cvat_local_pick_dir()["message"]
    api._pick_folder = lambda: str(good)
    assert api.cvat_local_pick_dir()["cvat_dir"] == str(good)
    assert json.loads(api._paths.settings.read_text(encoding="utf-8"))["cvat_dir"] == str(good)


def test_app_start_and_tray_check_local_cvat():
    app = (ROOT / "desktop" / "app.py").read_text(encoding="utf-8")
    run = app[app.index("def run(self)"):app.index("webview.start(")]
    assert run.index("cvat_local.http_ok(first, 2)") < run.index("create_window("), "kiểm tra trước khi quyết định cửa sổ nào hiện"
    assert "self._tray_open_cvat()" in app
    html = (ROOT / "desktop" / "ui" / "control.html").read_text(encoding="utf-8")
    assert html.count("if (r.local_down) openLocal(r.url);") == 2, "Mở trang và Mở tiếp job đều kiểm tra"
    assert "checkPendingLocal();" in html
