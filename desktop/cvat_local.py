"""CVAT chạy trên chính máy này (0.9.3): kiểm tra máy đã sẵn sàng chưa và giúp khởi động.

CVAT local trên Windows chạy bằng Docker Desktop, Docker Desktop chạy trên WSL2. Khi bạn bấm "Mở" một trang CVAT
ở localhost mà trang không trả lời, app kiểm tra lần lượt:

1. WSL2 đã cài (lệnh ``wsl -l -v``)
2. Docker Desktop đã cài (có lệnh ``docker``)
3. Docker đang chạy (``docker info``)
4. Các container của CVAT đã có (đã chạy ``docker compose up`` ít nhất một lần)
5. CVAT trả lời (``/api/server/about``)

Bước "Khởi động" chỉ làm những việc an toàn, lặp lại được: mở Docker Desktop, ``docker start`` các container CVAT
đã có (hoặc ``docker compose up -d`` trong thư mục CVAT bạn chọn), rồi đợi CVAT trả lời. App không cài WSL, Docker
hay CVAT thay bạn (cần quyền admin và khởi động lại máy): thiếu thì có nút mở hướng dẫn chính thức.

Mọi lệnh đi qua ``run`` để test được trên máy không có Docker.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("sam3-desktop")

LOOPBACK = ("localhost", "127.0.0.1", "::1")
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_DIR = "com.docker.compose.project.working_dir"
DOCKER_WAIT_S = 180          # Docker Desktop mở lần đầu sau khi bật máy có thể mất 1-2 phút
CVAT_WAIT_S = 300            # cvat_server chạy migrate khi khởi động, máy chậm mất vài phút
COMPOSE_UP_TIMEOUT_S = 3600  # lần đầu phải tải image (vài GB)

GUIDES = {
    "wsl": "https://learn.microsoft.com/windows/wsl/install",
    "docker": "https://docs.docker.com/desktop/setup/install/windows-install/",
    "cvat": "https://docs.cvat.ai/docs/administration/basics/installation/#windows-10",
}

Runner = Callable[[list, int, Optional[str]], tuple]   # (args, timeout, cwd) -> (exit code, output)


def is_this_machine(url: str) -> bool:
    """Trang ở chính máy này (localhost). Địa chỉ mạng nội bộ của máy khác thì app không bật hộ được."""
    return (urllib.parse.urlsplit(url if "//" in url else "http://" + url).hostname or "") in LOOPBACK


def origin_of(url: str) -> str:
    parts = urllib.parse.urlsplit(url if "//" in url else "http://" + url)
    return f"{parts.scheme}://{parts.netloc}"


def http_ok(url: str, timeout: float = 3.0) -> bool:
    """CVAT đã trả lời chưa. Traefik chạy mà cvat_server chưa xong thì trả 502/404, chưa tính là xong."""
    try:
        with urllib.request.urlopen(origin_of(url) + "/api/server/about", timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code == 401          # vài bản CVAT đòi đăng nhập cả trang about: vẫn là CVAT đã chạy
    except (OSError, ValueError):
        return False


def _decode(raw: bytes) -> str:
    # wsl.exe in ra UTF-16LE, docker in UTF-8
    if raw[:2] in (b"\xff\xfe",) or (len(raw) > 3 and raw[1:2] == b"\x00"):
        return raw.decode("utf-16-le", "replace").lstrip("\ufeff")
    return raw.decode("utf-8", "replace")


def default_run(args: list, timeout: int = 30, cwd: Optional[str] = None) -> tuple:
    flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}    # không hiện cửa sổ đen
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL, **flags)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as exc:
        return 126, str(exc)
    return p.returncode, (_decode(p.stdout or b"") + _decode(p.stderr or b"")).replace("\x00", "")


def docker_exe(env=os.environ) -> str:
    found = shutil.which("docker")
    if found:
        return found
    for base in (env.get("ProgramFiles", r"C:\Program Files"), env.get("ProgramW6432", "")):
        p = Path(base) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe" if base else None
        if p and p.exists():
            return str(p)
    return ""


def docker_desktop_exe(env=os.environ) -> str:
    for base in (env.get("ProgramFiles", r"C:\Program Files"), env.get("ProgramW6432", "")):
        p = Path(base) / "Docker" / "Docker" / "Docker Desktop.exe" if base else None
        if p and p.exists():
            return str(p)
    return ""


def parse_wsl(out: str) -> list:
    """``wsl -l -v`` -> [(tên, trạng thái, phiên bản)]."""
    rows = []
    for line in out.splitlines():
        parts = line.replace("*", " ").split()
        if len(parts) >= 3 and parts[-1] in ("1", "2"):
            rows.append((" ".join(parts[:-2]), parts[-2], int(parts[-1])))
    return rows


def parse_containers(out: str) -> list:
    """``docker ps -a`` (định dạng của app) -> container của CVAT: [{name, state, project, dir}]."""
    rows = []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 2 or not cols[0].strip():
            continue
        name, state = cols[0].strip(), cols[1].strip().lower()
        project = cols[2].strip() if len(cols) > 2 else ""
        folder = cols[3].strip() if len(cols) > 3 else ""
        rows.append({"name": name, "state": state, "project": project, "dir": folder})
    # Dự án compose có cvat_server là dự án CVAT (tên dự án tuỳ tên thư mục bạn clone về)
    projects = {r["project"] for r in rows if r["name"] == "cvat_server" or r["name"].endswith("-cvat_server-1")}
    if not projects:
        projects = {"cvat"}
    return [r for r in rows if r["project"] in projects or (not r["project"] and r["name"].startswith("cvat_"))]


PS_FORMAT = "{{.Names}}\t{{.State}}\t{{.Label \"" + COMPOSE_PROJECT + "\"}}\t{{.Label \"" + COMPOSE_DIR + "\"}}"


def _step(key, title, ok, detail="", fix=None):
    return {"key": key, "title": title, "ok": ok, "detail": detail, "fix": fix}


def check(url: str, *, run: Optional[Runner] = None, http: Optional[Callable[[str], bool]] = None, env=os.environ,
          cvat_dir: str = "") -> dict:
    """Kiểm tra từng bước. ok: True xong · False thiếu · None chưa kiểm (bước trước chưa xong)."""
    run, http = run or default_run, http or http_ok
    steps = []
    if http(url):
        return {"ready": True, "can_start": False, "steps": [_step("cvat_http", "CVAT trả lời", True, origin_of(url))],
                "containers": []}

    rc, out = run(["wsl", "-l", "-v"], 20, None)
    distros = parse_wsl(out) if rc == 0 else []
    wsl2 = [d for d in distros if d[2] == 2]
    if rc == 127:
        steps.append(_step("wsl", "WSL2", False, "Máy chưa có WSL.", {"link": "wsl"}))
    elif not wsl2:
        steps.append(_step("wsl", "WSL2", False, "Chưa có bản Linux nào chạy WSL2." if rc == 0 else "Chưa bật WSL.", {"link": "wsl"}))
    else:
        steps.append(_step("wsl", "WSL2", True, ", ".join(d[0] for d in wsl2)))

    exe = docker_exe(env)
    if not exe:
        steps.append(_step("docker_cli", "Docker Desktop đã cài", False, "Không thấy lệnh docker.", {"link": "docker"}))
        steps += [_step("docker_engine", "Docker đang chạy", None), _step("containers", "Container của CVAT", None),
                  _step("cvat_http", "CVAT trả lời", False, origin_of(url))]
        return {"ready": False, "can_start": False, "steps": steps, "containers": []}
    steps.append(_step("docker_cli", "Docker Desktop đã cài", True, exe))

    rc, _ = run([exe, "info", "--format", "{{.ServerVersion}}"], 20, None)
    engine = rc == 0
    desktop = docker_desktop_exe(env)
    steps.append(_step("docker_engine", "Docker đang chạy", engine,
                       "" if engine else ("App sẽ mở Docker Desktop khi bạn bấm Khởi động." if desktop
                                          else "Mở Docker Desktop rồi kiểm tra lại."),
                       None if engine else {"action": "start"}))

    containers = []
    if engine:
        rc, out = run([exe, "ps", "-a", "--format", PS_FORMAT], 20, None)
        containers = parse_containers(out) if rc == 0 else []
        running = [c for c in containers if c["state"] == "running"]
        compose_dir = _compose_dir(containers, cvat_dir)
        if containers:
            steps.append(_step("containers", "Container của CVAT", len(running) == len(containers),
                               f"{len(running)}/{len(containers)} đang chạy", None if len(running) == len(containers) else {"action": "start"}))
        elif compose_dir:
            steps.append(_step("containers", "Container của CVAT", False, f"Chưa tạo. App sẽ chạy docker compose up -d trong {compose_dir}.",
                               {"action": "start"}))
        else:
            steps.append(_step("containers", "Container của CVAT", False,
                               "Chưa thấy container nào. Chọn thư mục CVAT (có docker-compose.yml) hoặc cài CVAT theo hướng dẫn.",
                               {"link": "cvat", "pick": True}))
    else:
        steps.append(_step("containers", "Container của CVAT", None))
    steps.append(_step("cvat_http", "CVAT trả lời", False, origin_of(url)))

    # Khởi động được: Docker chạy rồi (hoặc app mở được Docker Desktop), và đã có container hay thư mục CVAT.
    # WSL2 không bắt buộc ở đây: Docker Desktop cũng chạy được bằng Hyper-V.
    if engine:
        can_start = bool(containers) or bool(_compose_dir(containers, cvat_dir))
    else:
        can_start = bool(desktop)
    return {"ready": False, "can_start": can_start, "steps": steps, "containers": containers}


def _compose_dir(containers: list, cvat_dir: str = "") -> str:
    for c in containers:
        if c.get("dir") and _has_compose(c["dir"]):
            return c["dir"]
    return cvat_dir if cvat_dir and _has_compose(cvat_dir) else ""


def _has_compose(folder: str) -> bool:
    return any((Path(folder) / n).is_file() for n in ("docker-compose.yml", "compose.yml", "docker-compose.yaml", "compose.yaml"))


class Starter:
    """Khởi động CVAT local ở luồng riêng; giao diện hỏi tiến độ qua progress."""

    def __init__(self, run: Optional[Runner] = None, http: Optional[Callable[[str], bool]] = None, env=os.environ,
                 sleep: Callable[[float], None] = time.sleep, popen=None, clock: Callable[[], float] = time.monotonic):
        self.run, self.http = run or default_run, http or http_ok
        self.env, self.sleep, self.popen, self.clock = env, sleep, popen or _launch, clock
        self.cancel = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.progress = {"running": False, "done": False, "ok": False, "step": "", "log": [], "url": ""}

    def _say(self, msg: str) -> None:
        self.progress["step"] = msg
        self.progress["log"] = (self.progress["log"] + [f"{time.strftime('%H:%M:%S')} {msg}"])[-60:]
        log.info("cvat-local: %s", msg)

    def start(self, url: str, cvat_dir: str = "") -> bool:
        if self.thread and self.thread.is_alive():
            return False
        self.cancel.clear()
        self.progress = {"running": True, "done": False, "ok": False, "step": "", "log": [], "url": url}
        self.thread = threading.Thread(target=self._guard, args=(url, cvat_dir), daemon=True)
        self.thread.start()
        return True

    def _guard(self, url, cvat_dir):
        try:
            ok, msg = self.run_steps(url, cvat_dir)
        except Exception as exc:  # noqa: BLE001 - báo lỗi lên giao diện thay vì làm chết luồng
            log.exception("cvat-local start failed")
            ok, msg = False, f"Lỗi khi khởi động: {exc}"
        self._say(msg)
        self.progress.update(running=False, done=True, ok=ok)

    def _wait(self, test: Callable[[], bool], seconds: int) -> bool:
        end = self.clock() + seconds
        while self.clock() < end:
            if self.cancel.is_set():
                return False
            if test():
                return True
            self.sleep(3)
        return test()

    def run_steps(self, url: str, cvat_dir: str = "") -> tuple:
        exe = docker_exe(self.env)
        if not exe:
            return False, "Chưa cài Docker Desktop."
        engine = lambda: self.run([exe, "info", "--format", "{{.ServerVersion}}"], 20, None)[0] == 0  # noqa: E731
        if not engine():
            desktop = docker_desktop_exe(self.env)
            if not desktop:
                return False, "Docker chưa chạy và không thấy Docker Desktop. Mở Docker Desktop rồi thử lại."
            self._say("Đang mở Docker Desktop…")
            self.popen([desktop], close_fds=True)
            self._say("Đợi Docker sẵn sàng (lần đầu sau khi bật máy có thể mất 1-2 phút)…")
            if not self._wait(engine, DOCKER_WAIT_S):
                return False, ("Đã dừng." if self.cancel.is_set() else
                               "Docker chưa sẵn sàng sau 3 phút. Xem cửa sổ Docker Desktop có báo lỗi WSL hay không.")
        self._say("Docker đang chạy.")

        rc, out = self.run([exe, "ps", "-a", "--format", PS_FORMAT], 20, None)
        containers = parse_containers(out) if rc == 0 else []
        folder = _compose_dir(containers, cvat_dir)
        if folder:
            self._say(f"Đang chạy docker compose up -d trong {folder}…")
            if not containers:
                self._say("Lần đầu phải tải image của CVAT (vài GB), có thể mất lâu.")
            rc, out = self.run([exe, "compose", "up", "-d"], COMPOSE_UP_TIMEOUT_S, folder)
            if rc != 0:
                return False, "docker compose up lỗi: " + _tail(out)
        elif containers:
            stopped = [c["name"] for c in containers if c["state"] != "running"]
            if stopped:
                self._say(f"Đang bật {len(stopped)} container của CVAT…")
                rc, out = self.run([exe, "start", *stopped], 300, None)
                if rc != 0:
                    return False, "docker start lỗi: " + _tail(out)
        else:
            return False, "Chưa có container CVAT nào. Chọn thư mục CVAT (có docker-compose.yml) hoặc cài CVAT trước."

        self._say("Đợi CVAT trả lời (lần đầu có thể mất vài phút)…")
        if not self._wait(lambda: self.http(url), CVAT_WAIT_S):
            return False, ("Đã dừng." if self.cancel.is_set() else
                           "CVAT chưa trả lời sau 5 phút. Xem nhật ký container cvat_server trong Docker Desktop.")
        return True, "CVAT đã sẵn sàng."


def _launch(args, **kw):
    """Mở Docker Desktop như người dùng bấm biểu tượng (không gắn với tiến trình của app)."""
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(args, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kw)


def _tail(out: str, n: int = 300) -> str:
    out = (out or "").strip()
    return out[-n:] if out else "(không có thông báo)"
