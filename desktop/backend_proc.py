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


# Mã thoát hay gặp của Windows khi tiến trình chết trước khi kịp ghi gì (số âm/không dấu đều nhận)
EXIT_CODES = {
    0xC0000135: "STATUS_DLL_NOT_FOUND: thiếu DLL (thường là Microsoft Visual C++ Redistributable)",
    0xC0000005: "STATUS_ACCESS_VIOLATION: lỗi bộ nhớ trong thư viện C (PyTorch/CUDA/driver)",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN: thư viện C dừng khẩn (thường do DLL không khớp phiên bản)",
    0xC000001D: "STATUS_ILLEGAL_INSTRUCTION: CPU không hỗ trợ lệnh mà thư viện cần",
    0xC0000142: "STATUS_DLL_INIT_FAILED: một DLL không khởi tạo được",
    0xC0000022: "STATUS_ACCESS_DENIED: bị chặn quyền (phần mềm diệt virus?)",
}


def describe_exit(code: Optional[int]) -> str:
    """'mã 3221225781 (0xC0000135, STATUS_DLL_NOT_FOUND: ...)' cho nhật ký và bảng điều khiển."""
    if code is None:
        return ""
    u = code & 0xFFFFFFFF
    meaning = EXIT_CODES.get(u, "")
    return f"mã {code} (0x{u:08X}, {meaning})" if meaning else f"mã {code} (0x{u:08X})"


def hint(log: str) -> str:
    """Gợi ý cách sửa cho các lỗi hay gặp trên Windows (một câu, dùng trong thông báo chữ)."""
    p = diagnose(log, None)
    return p["detail"] + " " if p else ""


def diagnose(log: str, code: Optional[int], start_error: str = "") -> Optional[dict]:
    """Đoán nguyên nhân backend không chạy, kèm các bước sửa có nút bấm sẵn.

    Trả {"id", "title", "detail", "steps": [{"text", "action"}]} hoặc None nếu không đoán được.
    action: vcredist | nvidia | retry | components | logs | report | none (bảng điều khiển gắn nút tương ứng)."""
    low = (log or "").lower()
    u = (code & 0xFFFFFFFF) if code is not None else None
    retry = {"text": "Bật lại backend", "action": "retry"}
    if start_error:
        return {"id": "start_failed", "title": "Không chạy được tiến trình backend",
                "detail": "Lỗi nằm trong app, không phải do máy bạn. Hãy gửi báo lỗi để người phát triển sửa.",
                "steps": [{"text": "Gửi báo lỗi (tự gom nhật ký)", "action": "report"}, retry]}
    if u == 0xC0000135 or "winerror 126" in low or "c10.dll" in low or "msvcp140" in low or "vcruntime140" in low:
        return {"id": "vcredist", "title": "Máy thiếu thư viện Microsoft Visual C++",
                "detail": "PyTorch cần Microsoft Visual C++ Redistributable (x64). Máy mới cài Windows hay thiếu gói này.",
                "steps": [{"text": "Tải và cài Microsoft Visual C++ Redistributable (x64), khoảng 25 MB", "action": "vcredist"},
                          {"text": "Cài xong thì bật lại backend", "action": "retry"}]}
    if "out of memory" in low or "outofmemory" in low:
        return {"id": "gpu_oom", "title": "GPU hết bộ nhớ",
                "detail": "Một ứng dụng khác (game, trình duyệt, phần mềm dựng hình) đang dùng nhiều bộ nhớ GPU.",
                "steps": [{"text": "Đóng bớt ứng dụng dùng GPU", "action": "none"}, retry]}
    if "no kernel image" in low or ("sm_" in low and "not compatible" in low):
        return {"id": "gpu_arch", "title": "Bản PyTorch không hỗ trợ card đồ hoạ này",
                "detail": "Card đồ hoạ quá cũ hoặc quá mới so với bản PyTorch đã tải.",
                "steps": [{"text": "Gửi báo lỗi kèm tên card", "action": "report"}]}
    if "cuda driver version is insufficient" in low or "driver version" in low and "insufficient" in low:
        return {"id": "driver_old", "title": "Driver NVIDIA quá cũ",
                "detail": "Driver card đồ hoạ cũ hơn bản CUDA mà PyTorch cần.",
                "steps": [{"text": "Cập nhật driver NVIDIA", "action": "nvidia"}, retry]}
    if "modulenotfounderror" in low or "importerror" in low:
        return {"id": "broken_install", "title": "Thiếu hoặc hỏng thư viện",
                "detail": "Một gói Python của app bị thiếu hoặc tải chưa xong.",
                "steps": [{"text": "Mở Thành phần, cài lại PyTorch", "action": "components"}, retry]}
    if u in (0xC0000005, 0xC0000409, 0xC0000142):
        return {"id": "native_crash", "title": "Thư viện của PyTorch hoặc driver bị lỗi",
                "detail": "Backend dừng vì lỗi trong thư viện C (PyTorch, CUDA hoặc driver).",
                "steps": [{"text": "Cập nhật driver NVIDIA", "action": "nvidia"}, retry,
                          {"text": "Vẫn lỗi thì gửi báo lỗi", "action": "report"}]}
    if u == 0xC0000022 or "access is denied" in low or "permissionerror" in low:
        return {"id": "blocked", "title": "Backend bị chặn quyền",
                "detail": "Phần mềm diệt virus hoặc chính sách của máy đang chặn backend.",
                "steps": [{"text": "Cho phép thư mục cài app trong phần mềm diệt virus", "action": "none"}, retry]}
    return None


def read_tail(path: Path, n: int = 400) -> list[str]:
    """n dòng cuối của file nhật ký (đọc từ cuối file, không nạp cả file lớn)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()[-n:]


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
        self.problem: Optional[dict] = None   # chẩn đoán khi backend lỗi (xem diagnose)
        self.auto_restart = True              # backend đang chạy mà tắt bất thường: tự bật lại (tối đa 3 lần / 10 phút)
        self._restarts: list[float] = []
        self.started_at = 0.0
        self._health_at = 0.0
        self.on_change = None                 # gọi khi trạng thái đổi (app báo lên khay hệ thống)

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
                self.problem = {"id": "missing", "title": "Chưa tải đủ thành phần", "detail": self.message,
                                "steps": [{"text": "Mở Thành phần để tải", "action": "components"}]}
                return self.info()
            self.port = pick_port(self.preferred_port)
            self.key = self.share_key or secrets.token_urlsafe(32)
            log = self.log_path()
            if log.exists() and log.stat().st_size > 5_000_000:
                log.replace(log.with_suffix(".log.1"))
            if self._log_file:
                self._log_file.close()
            self._log_file = open(log, "a", encoding="utf-8", errors="replace")
            self._log_file.write(tr(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} khởi động backend, cổng {self.port} =====\n", "en"))
            self._log_file.flush()
            env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "SAM3_", "HF_", "TRANSFORMERS_"))}
            env.update(self.comps.backend_env())
            env.update(self.extra_env)
            env.update({"PYTHONUNBUFFERED": "1", "PYTHONFAULTHANDLER": "1"})   # ghi log ngay; lỗi tầng C cũng có traceback
            env.update({"SAM3_HOST": "0.0.0.0" if self.share_key else "127.0.0.1", "SAM3_PORT": str(self.port), "SAM3_API_KEY": self.key,
                        "SAM3_ALLOWED_HOSTS": "*" if self.share_key else "127.0.0.1,localhost",
                        "SAM3_DESKTOP_PACKAGES": str(self.paths.packages), "SAM3_DESKTOP_DATA": str(self.paths.data)})
            # Windows: không mở cửa sổ console cho backend
            popen_opts = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
            # Bản cài (Python nhúng): -I cô lập khỏi Python khác trên máy. Chạy từ mã nguồn: dùng site-packages của người dùng.
            py_flags = ["-X", "utf8"] if self.comps.system else ["-I", "-X", "utf8"]
            try:
                self.proc = subprocess.Popen([self.python, *py_flags, "-m", "desktop.run_backend"], cwd=str(self.paths.app),
                                             env=env, stdout=self._log_file, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, **popen_opts)
            except (OSError, ValueError, TypeError) as exc:
                # Không chạy được tiến trình: báo rõ trên bảng điều khiển và ghi nhật ký, không im lặng
                self._log_file.write(f"Could not start backend process: {type(exc).__name__}: {exc}\n")
                self._log_file.flush()
                self.proc = None
                self.state, self.message = "error", f"Không chạy được backend: {type(exc).__name__}: {exc}"
                self.problem = diagnose("", None, start_error=f"{type(exc).__name__}: {exc}")
                return self.info()
            self.state, self.message, self.health = "starting", "Đang nạp model (lần đầu có thể mất 1-2 phút)...", {}
            self.problem = None
            self.started_at = time.time()
        threading.Thread(target=self._watch, daemon=True).start()
        return self.info()

    def _watch(self) -> None:
        """Theo dõi tiến trình: đợi model nạp xong, rồi tiếp tục canh nếu nó tắt bất thường."""
        proc = self.proc
        deadline = time.time() + 900
        while proc is self.proc and proc is not None and proc.poll() is None and time.time() < deadline:
            h = self.get_health()
            if h.get("ok"):
                self.state, self.health = "running", h
                self.message = f"Đang chạy: {h.get('engine')} trên {h.get('device')}"
                self._changed()
                break
            time.sleep(1.5)
        was_running = self.state == "running"
        while was_running and proc is self.proc and proc is not None and proc.poll() is None:
            time.sleep(2)
        if proc is None or proc is not self.proc or proc.poll() is None:
            return                                  # người dùng đã tắt/bật lại: không phải tắt bất thường
        why = describe_exit(proc.returncode)
        self._note(f"Backend process exited with {tr(why, 'en')}")
        log = self.tail(80)
        self.problem = diagnose(log, proc.returncode) or {
            "id": "unknown", "title": "Backend dừng bất ngờ" if was_running else "Backend dừng khi đang khởi động",
            "detail": f"Tiến trình backend thoát với {why}.",
            "steps": [{"text": "Bật lại backend", "action": "retry"}, {"text": "Xem nhật ký", "action": "logs"},
                      {"text": "Gửi báo lỗi", "action": "report"}]}
        self.problem["exit"] = why
        self.state = "error"
        self.message = (f"Backend đã dừng bất ngờ, {why}. " if was_running else f"Backend dừng khi đang khởi động, {why}. ") + \
            hint(log) + self.tail(12)
        self._changed()
        # Đang chạy ổn rồi mới tắt (không phải lỗi khởi động lặp lại): tự bật lại, có giới hạn để không lặp vô hạn
        now = time.time()
        self._restarts = [t for t in self._restarts if now - t < 600]
        if was_running and self.auto_restart and len(self._restarts) < 3 and self.problem.get("id") not in ("vcredist", "broken_install"):
            self._restarts.append(now)
            self._note(f"Auto-restart {len(self._restarts)}/3")
            time.sleep(2)
            if proc is self.proc:
                with self._lock:
                    self.proc = None
                self.start()
                self.message = "Backend vừa tắt bất ngờ, đang tự bật lại..."

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change(self.state)
            except Exception:  # noqa: BLE001 - chỉ là thông báo
                pass

    def _note(self, line: str) -> None:
        """Dòng của app (không phải của backend) trong backend.log."""
        try:
            with open(self.log_path(), "a", encoding="utf-8") as f:
                f.write(f"[app] {time.strftime('%H:%M:%S')} {line}\n")
        except OSError:
            pass

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
            self.problem = None

    def restart(self) -> dict:
        self.stop()
        return self.start()

    def tail(self, n: int = 40) -> str:
        return "\n".join(read_tail(self.log_path(), n))

    def info(self) -> dict:
        if self.remote:
            return {"state": self.state, "message": self.message, "port": None, "url": self.remote["base"], "health": self.health,
                    "remote": True}
        if self.state == "running" and self.proc is not None and self.proc.poll() is None and time.time() - self._health_at > 5:
            self._health_at = time.time()             # số liệu GPU (VRAM, nhiệt độ) cho trang Tổng quan, tối đa 5 giây một lần
            self.health = self.get_health() or self.health
        if self.state == "running" and self.proc is not None and self.proc.poll() is not None:
            why = describe_exit(self.proc.returncode)
            self.state, self.message = "error", f"Backend đã dừng bất ngờ, {why}. " + self.tail(12)
        return {"state": self.state, "message": self.message, "port": self.port, "url": self.base_url if self.port else "",
                "health": self.health, "problem": self.problem, "auto_restart": self.auto_restart,
                "uptime": int(time.time() - self.started_at) if self.state == "running" and getattr(self, "started_at", 0) else 0}

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
