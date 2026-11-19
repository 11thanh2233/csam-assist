"""Logic của bảng điều khiển và trình cài lần đầu (không phụ thuộc giao diện, kiểm thử được trên mọi máy)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from backend import __version__

from . import credstore
from . import downloader as D
from .backend_proc import BackendProcess
from .bridge import is_local, normalize_site
from .components import COMPONENTS, Components, Gpu, detect_gpu, human, seed_user_files
from .paths import Paths

DEFAULT_SITES = [
    {"name": "CVAT edufun", "url": "https://cvat.edufun.ai"},
    {"name": "CVAT trên máy này", "url": "http://localhost:8080"},
]
SAM3_REPO = "facebook/sam3"


class Settings:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("sites", list(DEFAULT_SITES))
        self.data.setdefault("autostart_backend", True)
        self.data.setdefault("license_accepted", "")

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def origins(self) -> set:
        return {normalize_site(s["url"]) for s in self.data["sites"]} - {""}


class Installer:
    """Chạy việc tải và cài ở luồng riêng; giao diện hỏi tiến độ qua progress()."""

    def __init__(self, comps: Components):
        self.comps = comps
        self.cancel = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.p = {"running": False, "finished": False, "error": "", "step": "", "file": "", "done": 0, "total": None,
                  "log": [], "results": {}}

    def start(self, keys: list[str], gpu: Gpu, token: str) -> bool:
        if self.thread and self.thread.is_alive():
            return False
        self.cancel.clear()
        self.p.update(running=True, finished=False, error="", step="Chuẩn bị...", file="", done=0, total=None, log=[], results={})
        self.thread = threading.Thread(target=self._run, args=(keys, gpu, token), daemon=True)
        self.thread.start()
        return True

    def _progress(self, **kw) -> None:
        if kw.get("step") and kw["step"] != self.p["step"]:
            self.p["log"].append(f"{time.strftime('%H:%M:%S')} {kw['step']}")
            self.p["log"] = self.p["log"][-200:]
        self.p.update({k: v for k, v in kw.items() if k in ("step", "file", "done", "total")})

    def _run(self, keys, gpu, token) -> None:
        order = [k for k in ("torch", "sam3", "vitpose", "face") if k in keys]
        try:
            for key in order:
                if self.comps.installed(key):
                    self._progress(step=f"{COMPONENTS[key]['title']}: đã có, bỏ qua")
                    continue
                self.p["results"][key] = self.comps.install(key, gpu=gpu, token=token, progress=self._progress, cancel=self.cancel)
                self._progress(step=f"{COMPONENTS[key]['title']}: xong")
            self._progress(step="Hoàn tất.", file="", done=0, total=None)
        except D.Cancelled:
            self.p["error"] = "Đã dừng. Bấm cài lại để tải tiếp từ chỗ dừng."
        except Exception as exc:  # noqa: BLE001 - hiện lý do cho người dùng
            self.p["error"] = str(exc)
            self.p["log"].append(f"LỖI: {exc}")
        finally:
            self.p.update(running=False, finished=True)


class ControlApi:
    """Gắn vào cửa sổ bảng điều khiển (pywebview js_api). Chỉ hàm không bắt đầu bằng '_' được gọi từ trang."""

    def __init__(self, paths: Paths, comps: Components, backend: BackendProcess, *, open_site: Callable[[str], None],
                 on_quit: Callable[[], None], setup_mode: bool = False):
        self._paths, self._comps, self._backend = paths, comps, backend
        self._settings = Settings(paths.settings)
        self._installer = Installer(comps)
        self._open_site, self._on_quit = open_site, on_quit
        self._setup_mode = setup_mode
        self._gpu: Optional[Gpu] = None
        self._token = ""

    # ----------------------------------------------------------- reading ---
    def _gpu_info(self) -> Gpu:
        if self._gpu is None:
            self._gpu = detect_gpu()
        return self._gpu

    def status(self):
        gpu = self._gpu_info()
        st = self._comps.status()
        return {
            "version": __version__, "setup_mode": self._setup_mode,
            "gpu": {"name": gpu.name, "vram_mb": gpu.vram_mb, "driver": gpu.driver, "ok": gpu.ok,
                    "driver_old": gpu.driver_too_old(), "small": gpu.ok and gpu.vram_mb < 6000},
            "components": st, "backend": self._backend.info(), "sites": self._settings.data["sites"],
            "license_accepted": bool(self._settings.data.get("license_accepted")),
            "token_saved": credstore.available() and bool(credstore.get()),
            "keyring": credstore.available(), "autostart_windows": autostart_enabled(),
            "data_dir": str(self._paths.data), "installing": self._installer.p["running"],
        }

    def license_text(self):
        return self._paths.sam_license.read_text(encoding="utf-8")

    def accept_license(self):
        self._settings.data["license_accepted"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._settings.save()
        return True

    def check_token(self, token, remember=False):
        token = (token or "").strip() or (credstore.get() if credstore.available() else "")
        res = D.hf_check_access(SAM3_REPO, token)
        if res["ok"]:
            self._token = token
            if remember and credstore.available():
                credstore.set(token)
        return dict(res)

    def forget_token(self):
        self._token = ""
        if credstore.available():
            credstore.delete()
        return True

    def estimate(self, keys):
        est = self._comps.estimate(list(keys), self._gpu_info(), self._token)
        return {**est, "download_h": human(est["download"]), "disk_h": human(est["disk"]), "free_h": human(est["free"]),
                "detail_h": {k: human(v) for k, v in est["detail"].items()}}

    # ------------------------------------------------------------ actions ---
    def install(self, keys):
        keys = [k for k in keys if k in COMPONENTS]
        if not self._settings.data.get("license_accepted") and "sam3" in keys:
            return {"ok": False, "message": "Cần đồng ý giấy phép SAM trước khi tải trọng số SAM 3."}
        if "sam3" in keys and not self._comps.installed("sam3") and not self._token:
            return {"ok": False, "message": "Cần kiểm tra token Hugging Face trước."}
        est = self._comps.estimate(keys, self._gpu_info(), self._token)
        if not est["enough"]:
            return {"ok": False, "message": f"Ổ đĩa còn {human(est['free'])}, cần khoảng {human(est['disk'])}. Dọn bớt rồi thử lại."}
        if "torch" in keys and not self._comps.installed("torch"):
            self._backend.stop()                     # backend đang giữ file PyTorch cũ
        return {"ok": self._installer.start(keys, self._gpu_info(), self._token), "message": ""}

    def install_progress(self):
        p = dict(self._installer.p)
        p["done_h"], p["total_h"] = human(p["done"]), human(p["total"])
        p["percent"] = round(100 * p["done"] / p["total"], 1) if p["total"] else None
        if p["finished"] and not p["error"] and self._settings.data.get("autostart_backend") and self._comps.status()["ready"] \
                and self._backend.state in ("stopped", "missing", "error") and not self._setup_mode:
            self._backend.start()
        return p

    def cancel_install(self):
        self._installer.cancel.set()
        return True

    def remove_component(self, key):
        if key not in COMPONENTS:
            return False
        if key in ("torch", "sam3"):
            self._backend.stop()
        self._comps.remove(key)
        return True

    def backend(self, action):
        if action == "start":
            return self._backend.start()
        if action == "stop":
            self._backend.stop()
            return self._backend.info()
        if action == "restart":
            return self._backend.restart()
        if action == "log":
            return {"log": self._backend.tail(200)}
        return self._backend.info()

    def add_site(self, name, url):
        origin = normalize_site(url)
        if not origin:
            return {"ok": False, "message": "Địa chỉ không hợp lệ. Ví dụ: https://cvat.ten-mien.com hoặc localhost:8080"}
        warn = "" if origin.startswith("https://") or is_local(origin) else \
            "Trang dùng http (không mã hoá) trên mạng ngoài: mật khẩu CVAT có thể bị đọc trộm. Nên dùng https."
        sites = [s for s in self._settings.data["sites"] if normalize_site(s["url"]) != origin]
        sites.append({"name": (name or "").strip()[:60] or origin, "url": origin})
        self._settings.data["sites"] = sites
        self._settings.save()
        return {"ok": True, "message": warn, "sites": sites}

    def remove_site(self, url):
        origin = normalize_site(url)
        self._settings.data["sites"] = [s for s in self._settings.data["sites"] if normalize_site(s["url"]) != origin]
        self._settings.save()
        return self._settings.data["sites"]

    def open_site(self, url):
        origin = normalize_site(url)
        if origin not in self._settings.origins():
            return {"ok": False, "message": "Địa chỉ chưa có trong danh sách."}
        if self._backend.state in ("stopped", "error") and self._comps.status()["ready"]:
            self._backend.start()
        self._open_site(origin)
        return {"ok": True}

    def open_folder(self, which):
        target = {"data": self._paths.data, "profiles": self._paths.profiles, "library": self._paths.library,
                  "learning": self._paths.library / "learning", "logs": self._paths.logs}.get(which)
        if target is None:
            return False
        target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606 - mở thư mục của chính app
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return True

    def open_link(self, which):
        """Mở trang hướng dẫn trong trình duyệt của máy. Chỉ các trang cố định, trang không tự chọn được địa chỉ."""
        url = {"sam3": "https://huggingface.co/facebook/sam3", "tokens": "https://huggingface.co/settings/tokens",
               "signup": "https://huggingface.co/join", "webview2": "https://developer.microsoft.com/microsoft-edge/webview2/",
               "nvidia": "https://www.nvidia.com/Download/index.aspx"}.get(which)
        if url:
            import webbrowser

            webbrowser.open(url)
        return bool(url)

    def set_autostart(self, on):
        set_autostart(bool(on))
        return autostart_enabled()

    def finish_setup(self):
        self._on_quit()
        return True

    def quit(self):
        self._backend.stop()
        self._on_quit()
        return True

    # ---------------------------------------------------------------- app ---
    def _prepare(self) -> dict:
        res = seed_user_files(self._paths)
        if self._settings.data.get("autostart_backend") and self._comps.status()["ready"] and not self._setup_mode:
            self._backend.start()
        return res


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "SAM3 CVAT Assist"


def _launcher() -> str:
    return os.environ.get("SAM3_LAUNCHER", "")


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    if sys.platform != "win32" or not _launcher():
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{_launcher()}" --tray')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass
