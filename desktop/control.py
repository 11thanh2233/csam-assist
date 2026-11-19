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
from . import changelog
from . import updater as U
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
        self.existed = path.exists()
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("sites", list(DEFAULT_SITES))
        self.data.setdefault("autostart_backend", True)
        self.data.setdefault("license_accepted", "")
        self.data.setdefault("auto_update_check", True)
        self.data.setdefault("scan_dirs", [])
        if "last_seen_version" not in self.data:
            # Cài mới: không có gì để báo. Có cài đặt mà chưa có mục này: đang cập nhật từ 0.6.0 (bản desktop đầu tiên).
            self.data["last_seen_version"] = "0.6.0" if self.existed else __version__

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
            self._progress(step="Quét máy: tìm file đã có, so với bản cần dùng...")
            plan = self.comps.plan(order, gpu, token)
            for key in order:
                if plan[key]["action"] == "none":
                    self._progress(step=f"{COMPONENTS[key]['title']}: đã có, đúng bản, không tải")
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
        comps.extra_dirs = [Path(d) for d in self._settings.data["scan_dirs"]]
        self._installer = Installer(comps)
        self._updater = U.Updater(paths.downloads / "update")
        self._update_info: dict = self._settings.data.get("update_cache") or {}
        self._pick_folder: Optional[Callable[[], Optional[str]]] = None
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

    # ------------------------------------------------------ quét thành phần ---
    def scan(self, refresh=False):
        """Đã có gì, đúng bản chưa, dùng lại được gì từ bản cũ, cần tải bao nhiêu."""
        gpu = self._gpu_info()
        self._comps.tool_dirs(refresh=bool(refresh))
        plan = self._comps.plan(list(COMPONENTS), gpu, self._token)
        for p in plan.values():
            p["download_h"], p["reuse_h"] = human(p["download"]), human(p["reuse"])
        todo = [k for k, p in plan.items() if p["action"] != "none" and (p.get("required") or self._comps.installed(k))]
        from . import reuse as R
        return {"plan": plan, "todo": todo, "required": [k for k in todo if plan[k].get("required")],
                "tool_dirs": [str(d) for d in self._comps.tool_dirs()], "hf_caches": [str(d) for d in R.hf_cache_dirs()],
                "scan_dirs": self._settings.data["scan_dirs"], "python": sys.version.split()[0], "app": __version__}

    def add_scan_folder(self):
        folder = self._pick_folder() if self._pick_folder else None
        if not folder:
            return self.scan()
        dirs = [d for d in self._settings.data["scan_dirs"] if d != folder] + [folder]
        self._settings.data["scan_dirs"] = dirs
        self._settings.save()
        self._comps.extra_dirs = [Path(d) for d in dirs]
        return self.scan(refresh=True)

    def remove_scan_folder(self, folder):
        self._settings.data["scan_dirs"] = [d for d in self._settings.data["scan_dirs"] if d != folder]
        self._settings.save()
        self._comps.extra_dirs = [Path(d) for d in self._settings.data["scan_dirs"]]
        return self.scan(refresh=True)

    # ------------------------------------------------------------ cập nhật ---
    def check_update(self, force=False):
        st = self._settings.data
        if not force and (not st.get("auto_update_check") or not U.due(st.get("last_update_check", 0))):
            return self._update_view()
        try:
            info = U.check(__version__)
            info["checked"] = time.strftime("%Y-%m-%d %H:%M")
            self._update_info = info
            st["update_cache"], st["last_update_check"] = info, time.time()
            st.pop("update_error", None)
        except Exception as exc:  # noqa: BLE001 - mất mạng: thử lại lần sau
            st["update_error"] = f"Không kiểm được bản mới: {exc}"
        self._settings.save()
        return self._update_view()

    def _update_view(self):
        info = dict(self._update_info or {})
        if U.parse_version(info.get("version", "0")) <= U.parse_version(__version__):
            info["available"] = False                 # vừa cập nhật xong: thông tin cũ không còn đúng
        info["skipped"] = info.get("version") == self._settings.data.get("skip_version")
        info["size_h"] = human(info.get("size"))
        info["error"] = self._settings.data.get("update_error", "")
        info["auto_check"] = self._settings.data.get("auto_update_check", True)
        info["current"] = __version__
        return info

    def set_auto_update(self, on):
        self._settings.data["auto_update_check"] = bool(on)
        self._settings.save()
        return bool(on)

    def skip_update(self, version):
        self._settings.data["skip_version"] = str(version)
        self._settings.save()
        return True

    def download_update(self):
        info = self._update_info or {}
        if not info.get("available") or not info.get("auto"):
            return {"ok": False, "message": info.get("reason") or "Không có bản mới để tải."}
        return {"ok": self._updater.start(info), "message": ""}

    def update_progress(self):
        p = dict(self._updater.p)
        p["done_h"], p["total_h"] = human(p["done"]), human(p["total"])
        p["percent"] = round(100 * p["done"] / p["total"], 1) if p["total"] else None
        return p

    def install_update(self):
        ready = self._updater.p.get("ready")
        if not ready or not Path(ready).exists():
            return {"ok": False, "message": "Chưa tải xong bộ cài."}
        if self._installer.p["running"]:
            return {"ok": False, "message": "Đang tải thành phần, đợi xong rồi cập nhật."}
        self._backend.stop()
        self._updater.launch(Path(ready))
        self._on_quit()
        return {"ok": True}

    def open_release_page(self):
        page = (self._update_info or {}).get("page") or ""
        if not page.startswith(f"https://github.com/{U.REPO}/"):
            page = f"https://github.com/{U.REPO}/releases"
        import webbrowser

        webbrowser.open(page)
        return True

    def cancel_update(self):
        self._updater.cancel.set()
        return True

    # ----------------------------------------------------------- có gì mới ---
    def whats_new(self):
        seen = self._settings.data.get("last_seen_version", __version__)
        entries = changelog.between(self._paths.app / "docs" / "CHANGELOG.md", seen, __version__)
        return {"show": bool(entries) and not self._setup_mode, "from": seen, "to": __version__, "entries": entries}

    def mark_seen(self):
        self._settings.data["last_seen_version"] = __version__
        self._settings.save()
        return True

    def changelog_all(self):
        return changelog.between(self._paths.app / "docs" / "CHANGELOG.md", "0.0.0", __version__)

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
        plan = self._comps.plan(keys, self._gpu_info(), self._token)
        todo = [k for k in keys if plan[k]["action"] != "none"]
        if not todo:
            return {"ok": False, "message": "Các thành phần đã chọn đều đã có và đúng bản, không cần tải gì."}
        if "sam3" in todo and not self._settings.data.get("license_accepted"):
            return {"ok": False, "message": "Cần đồng ý giấy phép SAM trước khi cài trọng số SAM 3."}
        if "sam3" in todo and plan["sam3"].get("needs_token") and not self._token:
            return {"ok": False, "message": "Cần kiểm tra token Hugging Face trước (một phần trọng số SAM 3 phải tải)."}
        est = self._comps.estimate(todo, self._gpu_info(), self._token)
        if not est["enough"]:
            return {"ok": False, "message": f"Ổ đĩa còn {human(est['free'])}, cần khoảng {human(est['disk'])}. Dọn bớt rồi thử lại."}
        self._backend.stop()                         # backend đang giữ file PyTorch / trọng số sắp được thay
        return {"ok": self._installer.start(todo, self._gpu_info(), self._token), "message": ""}

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
