"""Chạy backend thành một tiến trình riêng (không có cửa sổ console) và hỏi thăm sức khoẻ của nó.

Backend chỉ nghe trên 127.0.0.1, có API key ngẫu nhiên cho mỗi lần chạy. Key chỉ nằm trong app (bridge thêm vào
mỗi yêu cầu), trang web không thấy được, nên trang hay chương trình khác trên máy không gọi được backend.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from backend.i18n import tr

from .components import Components

_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # không đi qua proxy hệ thống


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int = 8765) -> int:
    for p in [preferred] + list(range(preferred + 1, preferred + 40)):
        if port_free(p):
            return p
    raise RuntimeError("Không còn cổng trống từ 8765 tới 8804 cho backend.")


def hint(log: str) -> str:
    """Gợi ý cách sửa cho các lỗi hay gặp trên Windows."""
    low = log.lower()
    if "winerror 126" in low or "c10.dll" in low or "msvcp140" in low:
        return ("Có thể máy thiếu Microsoft Visual C++ Redistributable (x64): tải tại https://aka.ms/vs/17/release/vc_redist.x64.exe, "
                "cài rồi bật lại backend. ")
    if "out of memory" in low or "outofmemory" in low:
        return "GPU hết bộ nhớ: đóng bớt ứng dụng dùng GPU rồi bật lại backend. "
    if "no kernel image" in low or "sm_" in low and "not compatible" in low:
        return "Bản PyTorch không hỗ trợ card này: báo lỗi kèm tên card cho người phát triển. "
    return ""


class BackendProcess:
    def __init__(self, comps: Components, preferred_port: int = 8765, python: Optional[str] = None):
        self.comps = comps
        self.paths = comps.paths
        self.preferred_port = preferred_port
        self.python = python or self._python()
        self.port: Optional[int] = None
        self.key = ""
        self.proc: Optional[subprocess.Popen] = None
        self.state = "stopped"          # stopped | starting | running | error | missing
        self.message = ""
        self.health: dict = {}
        self._lock = threading.Lock()
        self._log_file = None
        self.extra_env: dict = {}          # cài đặt của app cho backend (ví dụ SAM3_IDLE_UNLOAD_MIN)
        self.share_key = ""                # khác rỗng: chia sẻ backend cho máy khác trong LAN với key cố định này
        self.remote: Optional[dict] = None # {"base", "key"}: dùng backend ở máy khác, không chạy backend trên máy này

    @staticmethod
    def _python() -> str:
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
            exe = exe.with_name("python.exe")      # python.exe + CREATE_NO_WINDOW: có stdout để ghi log
        return str(exe)

    @property
    def base_url(self) -> str:
        if self.remote:
            return self.remote["base"]
        return f"http://127.0.0.1:{self.port or self.preferred_port}"

    @property
    def page_url(self) -> str:
        """Địa chỉ panel trong trang dùng (cầu nối chỉ nhận 127.0.0.1, rồi tự chuyển tới backend thật)."""
        return f"http://127.0.0.1:{self.port or self.preferred_port}"

    @property
    def request_key(self) -> str:
        return self.remote["key"] if self.remote else self.key

    def log_path(self) -> Path:
        return self.paths.logs / "backend.log"

    def start(self) -> dict:
        if self.remote:
            h = self.get_health()
            self.health = h
            if h.get("ok"):
                self.state, self.message = "running", f"Dùng backend ở máy khác ({self.remote['base']}): {h.get('engine')} trên {h.get('device')}"
            else:
                self.state, self.message = "error", f"Không kết nối được backend ở {self.remote['base']}."
            return self.info()
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return self.info()
            st = self.comps.status()
            if not st["ready"]:
                missing = [st[k]["title"] for k in ("torch", "sam3") if not st[k]["installed"]]
                self.state, self.message = "missing", "Chưa có " + " và ".join(missing) + ". Mở mục Thành phần để tải."
                return self.info()
            self.port = pick_port(self.preferred_port)
            self.key = self.share_key or secrets.token_urlsafe(32)
            log = self.log_path()
            if log.exists() and log.stat().st_size > 5_000_000:
                log.replace(log.with_suffix(".log.1"))
            self._log_file = open(log, "a", encoding="utf-8", errors="replace")
            self._log_file.write(tr(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} khởi động backend, cổng {self.port} =====\n", "en"))
            self._log_file.flush()
            env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "SAM3_", "HF_", "TRANSFORMERS_"))}
            env.update(self.comps.backend_env())
            env.update(self.extra_env)
            env.update({"SAM3_HOST": "0.0.0.0" if self.share_key else "127.0.0.1", "SAM3_PORT": str(self.port), "SAM3_API_KEY": self.key,
                        "SAM3_ALLOWED_HOSTS": "*" if self.share_key else "127.0.0.1,localhost",
                        "SAM3_DESKTOP_PACKAGES": str(self.paths.packages), "SAM3_DESKTOP_DATA": str(self.paths.data)})
            flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
            # Bản cài (Python nhúng): -I cô lập khỏi Python khác trên máy. Chạy từ mã nguồn: dùng site-packages của người dùng.
            flags = ["-X", "utf8"] if self.comps.system else ["-I", "-X", "utf8"]
            self.proc = subprocess.Popen([self.python, *flags, "-m", "desktop.run_backend"], cwd=str(self.paths.app), env=env,
                                         stdout=self._log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **flags)
            self.state, self.message, self.health = "starting", "Đang nạp model (lần đầu có thể mất 1-2 phút)...", {}
        threading.Thread(target=self._watch, daemon=True).start()
        return self.info()

    def _watch(self) -> None:
        proc = self.proc
        deadline = time.time() + 900
        while proc is self.proc and proc is not None and proc.poll() is None and time.time() < deadline:
            h = self.get_health()
            if h.get("ok"):
                self.state, self.health = "running", h
                self.message = f"Đang chạy: {h.get('engine')} trên {h.get('device')}"
                break
            time.sleep(1.5)
        if proc is not None and proc.poll() is not None and proc is self.proc:
            self.state = "error"
            self.message = "Backend dừng khi đang khởi động. " + hint(self.tail(60)) + self.tail(12)

    def get_health(self) -> dict:
        try:
            with _local.open(f"{self.base_url}/health", timeout=3) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            return {}

    def stop(self) -> None:
        with self._lock:
            proc, self.proc = self.proc, None
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            self.state, self.message, self.health = "stopped", "Đã tắt.", {}

    def restart(self) -> dict:
        self.stop()
        return self.start()

    def tail(self, n: int = 40) -> str:
        try:
            lines = self.log_path().read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n:])

    def info(self) -> dict:
        if self.remote:
            return {"state": self.state, "message": self.message, "port": None, "url": self.remote["base"], "health": self.health,
                    "remote": True}
        if self.state == "running" and self.proc is not None and self.proc.poll() is not None:
            self.state, self.message = "error", "Backend đã dừng bất ngờ. " + self.tail(12)
        return {"state": self.state, "message": self.message, "port": self.port, "url": self.base_url if self.port else "",
                "health": self.health}

    def request(self, method: str, path: str, body: Optional[str], content_type: str = "application/json",
                timeout: float = 300) -> tuple[int, str]:
        """Gửi yêu cầu tới backend kèm API key. Trả (mã HTTP, nội dung)."""
        data = body.encode("utf-8") if isinstance(body, str) else body
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method,
                                     headers={"Content-Type": content_type, "X-API-Key": self.request_key})
        try:
            with _local.open(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")
