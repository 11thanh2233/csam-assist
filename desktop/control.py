"""Logic của bảng điều khiển và trình cài lần đầu (không phụ thuộc giao diện, kiểm thử được trên mọi máy)."""
from __future__ import annotations

import functools
import logging
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from backend import __version__
from backend.i18n import norm_lang, translate_obj

from . import credstore
from . import downloader as D
from .backend_proc import BackendProcess
from .bridge import is_local, normalize_site
from . import changelog
from . import updater as U
from .components import COMPONENTS, Components, Gpu, detect_gpu, human, seed_user_files
from .paths import Paths

DEFAULT_SITES = [
    {"name": "CVAT edufun", "url": "https://cvat.edufun.ai", "platform": "cvat"},
    {"name": "CVAT trên máy này", "url": "http://localhost:8080", "platform": "cvat"},
]
# Nền tảng gán nhãn panel hỗ trợ. Mỗi nền tảng cần một bộ chuyển đổi riêng trong panel (đọc ảnh, ghi nhãn);
# hiện mới có CVAT. Nền tảng khác sẽ thêm vào đây khi có bộ chuyển đổi.
PLATFORMS = {"cvat": {"name": "CVAT", "supported": True}}
SAM3_REPO = "facebook/sam3"


log = logging.getLogger("sam3-desktop")


# Phiên bản định dạng settings.json. Từ 1.0: bản mới luôn đọc được file của bản cũ (thêm mục, không đổi nghĩa mục cũ),
# và giữ nguyên mục lạ (file của bản mới hơn sau khi quay về bản cũ). Xem docs/COMPAT.md.
SETTINGS_SCHEMA = 1
SETTINGS_TYPES = {"sites": list, "recent_jobs": list, "scan_dirs": list, "autostart_backend": bool, "auto_update_check": bool,
                  "auto_restart_backend": bool, "idle_unload_min": (int, float), "start_mode": str, "lang": str, "theme": str}


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.existed = path.exists()
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict):
                raise ValueError("settings.json is not an object")
        except OSError:
            self.data = {}
        except ValueError:
            # File hỏng (mất điện khi đang ghi…): giữ một bản để không mất gì, rồi chạy với cài đặt mặc định
            broken = path.with_name(f"settings.broken-{time.strftime('%Y%m%d-%H%M%S')}.json")
            try:
                path.replace(broken)
                log.warning("settings.json is unreadable, kept as %s", broken.name)
            except OSError:
                pass
            self.data = {}
        for key, kind in SETTINGS_TYPES.items():          # sai kiểu: bỏ riêng mục đó để dùng mặc định
            if key in self.data and not isinstance(self.data[key], kind):
                log.warning("settings.json: %s has wrong type, using default", key)
                del self.data[key]
        self.data["schema"] = max(SETTINGS_SCHEMA, int(self.data.get("schema") or 0))
        self.data.setdefault("sites", list(DEFAULT_SITES))
        self.data.setdefault("autostart_backend", True)
        self.data.setdefault("license_accepted", "")
        self.data.setdefault("auto_update_check", True)
        self.data.setdefault("scan_dirs", [])
        self.data.setdefault("recent_jobs", [])
        self.data.setdefault("start_mode", "cvat")          # cvat: mở thẳng CVAT | control: mở bảng điều khiển
        self.data.setdefault("idle_unload_min", 15)         # nhả model khỏi VRAM sau N phút không dùng (0 = không nhả)
        self.data.setdefault("lang", "vi")                   # ngôn ngữ giao diện: vi | en (panel và bảng điều khiển dùng chung)
        self.data.setdefault("theme", "system")             # giao diện: system (theo Windows) | light | dark
        self.data.setdefault("auto_restart_backend", True)  # backend tắt bất thường: tự bật lại (tối đa 3 lần / 10 phút)
        self.data["sites"] = [s for s in self.data["sites"] if isinstance(s, dict) and s.get("url")]
        for site in self.data["sites"]:
            site.setdefault("platform", "cvat")              # cài đặt từ bản trước 0.9: mọi trang đều là CVAT
            site.setdefault("name", site["url"])
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
                  "log": [], "results": {}, "order": [], "items": {}, "rate": 0.0}
        self._last: Optional[tuple[float, int]] = None

    def start(self, keys: list[str], gpu: Gpu, token: str) -> bool:
        if self.thread and self.thread.is_alive():
            return False
        self.cancel.clear()
        order = [k for k in ("torch", "sam3", "vitpose", "face") if k in keys]
        # Trạng thái từng thành phần cho trang tải: wait | active | skip (đã có) | done | error
        self.p.update(running=True, finished=False, error="", step="Chuẩn bị...", file="", done=0, total=None, log=[], results={},
                      order=order, items={k: "wait" for k in order}, rate=0.0)
        self._last = None
        self.thread = threading.Thread(target=self._run, args=(keys, gpu, token), daemon=True)
        self.thread.start()
        return True

    def _progress(self, **kw) -> None:
        if kw.get("step") and kw["step"] != self.p["step"]:
            self.p["log"].append(f"{time.strftime('%H:%M:%S')} {kw['step']}")
            self.p["log"] = self.p["log"][-200:]
        if "done" in kw and kw.get("done"):
            now, done = time.time(), int(kw["done"])
            if self._last and done > self._last[1] and now > self._last[0]:
                inst = (done - self._last[1]) / (now - self._last[0])
                self.p["rate"] = inst if not self.p["rate"] else 0.8 * self.p["rate"] + 0.2 * inst   # tốc độ làm mượt
            if not self._last or now - self._last[0] >= 0.5 or done < self._last[1]:
                self._last = (now, done)
        self.p.update({k: v for k, v in kw.items() if k in ("step", "file", "done", "total")})

    def _run(self, keys, gpu, token) -> None:
        order = [k for k in ("torch", "sam3", "vitpose", "face") if k in keys]
        try:
            self._progress(step="Quét máy: tìm file đã có, so với bản cần dùng...")
            plan = self.comps.plan(order, gpu, token)
            for key in order:
                if plan[key]["action"] == "none":
                    self.p["items"][key] = "skip"
                    self._progress(step=f"{COMPONENTS[key]['title']}: đã có, đúng bản, không tải")
                    continue
                self.p["items"][key] = "active"
                self._last, self.p["rate"] = None, 0.0
                self.p["results"][key] = self.comps.install(key, gpu=gpu, token=token, progress=self._progress, cancel=self.cancel)
                self.p["items"][key] = "done"
                self._progress(step=f"{COMPONENTS[key]['title']}: xong")
            self._progress(step="Hoàn tất.", file="", done=0, total=None)
        except D.Cancelled:
            self.p["error"] = "Đã dừng. Bấm cài lại để tải tiếp từ chỗ dừng."
            self._mark_active("wait")
        except Exception as exc:  # noqa: BLE001 - hiện lý do cho người dùng
            self.p["error"] = str(exc)
            self.p["log"].append(f"LỖI: {exc}")
            self._mark_active("error")
        finally:
            self.p.update(running=False, finished=True)

    def _mark_active(self, state: str) -> None:
        for k, v in self.p["items"].items():
            if v == "active":
                self.p["items"][k] = state


class ControlApi:
    """Gắn vào cửa sổ bảng điều khiển (pywebview js_api). Chỉ hàm không bắt đầu bằng '_' được gọi từ trang."""

    def __init__(self, paths: Paths, comps: Components, backend: BackendProcess, *, open_site: Callable[[str], None],
                 on_quit: Callable[[], None], setup_mode: bool = False):
        self._paths, self._comps, self._backend = paths, comps, backend
        self._settings = Settings(paths.settings)
        comps.extra_dirs = [Path(d) for d in self._settings.data["scan_dirs"]]
        backend.extra_env = {"SAM3_IDLE_UNLOAD_MIN": str(self._settings.data["idle_unload_min"])}
        backend.auto_restart = bool(self._settings.data["auto_restart_backend"])
        self._installer = Installer(comps)
        self._updater = U.Updater(paths.downloads / "update")
        self._update_info: dict = self._settings.data.get("update_cache") or {}
        self._pick_folder: Optional[Callable[[], Optional[str]]] = None
        self._pick_file: Optional[Callable[[bool, str], Optional[str]]] = None      # (lưu?, tên gợi ý) -> đường dẫn
        st = self._settings.data
        if st.get("lan_share") and st.get("lan_key"):
            backend.share_key = st["lan_key"]
        if st.get("remote_backend"):
            backend.remote = dict(st["remote_backend"])
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
            "recent_jobs": self._settings.data["recent_jobs"], "start_mode": self._settings.data["start_mode"],
            "idle_unload_min": self._settings.data["idle_unload_min"], "sync_dir": self._settings.data.get("sync_dir", ""),
            "autostart_backend": bool(self._settings.data.get("autostart_backend")),
            "auto_restart_backend": bool(self._settings.data.get("auto_restart_backend")),
            "auto_update_check": bool(self._settings.data.get("auto_update_check", True)),
            "lang": self._lang(), "platforms": PLATFORMS, "theme": self._settings.data.get("theme", "system"),
            "last_update_check": self._settings.data.get("last_update_check", 0),
            "last_selftest": self._settings.data.get("last_selftest"),
            "rolled_back": getattr(self, "_rolled_back", None),
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
        info["notes"] = changelog.pick_lang(info.get("notes") or "", self._lang())
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
        from . import safe_update

        to_version = self._updater.p.get("version") or "?"
        try:
            backup = safe_update.auto_backup(self._paths, __version__, to_version)
        except Exception as exc:  # noqa: BLE001 - chưa sao lưu được thì không cập nhật
            log.exception("Backup before update failed")
            return {"ok": False, "message": f"Không sao lưu được dữ liệu trước khi cập nhật ({exc}). Chưa cập nhật."}
        log.info("Backup before update: %s", backup)
        self._backend.stop()
        app_dir = self._paths.app.parent if self._paths.app.name == "app" else self._paths.app
        self._updater.launch(Path(ready), app_dir, self._paths.data, __version__, to_version)
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

    # ----------------------------------------------------------- ngôn ngữ ---
    def ui_lang(self):
        return self._lang()

    def _lang(self) -> str:
        return norm_lang(self._settings.data.get("lang"))

    def _changelog_path(self) -> Path:
        """Nhật ký thay đổi theo ngôn ngữ giao diện (bản tiếng Anh: docs/CHANGELOG.en.md)."""
        docs = self._paths.app / "docs"
        en = docs / "CHANGELOG.en.md"
        return en if self._lang() == "en" and en.exists() else docs / "CHANGELOG.md"

    # ----------------------------------------------------------- có gì mới ---
    def whats_new(self):
        seen = self._settings.data.get("last_seen_version", __version__)
        entries = changelog.between(self._changelog_path(), seen, __version__)
        return {"show": bool(entries) and not self._setup_mode, "from": seen, "to": __version__, "entries": entries}

    def mark_seen(self):
        self._settings.data["last_seen_version"] = __version__
        self._settings.save()
        return True

    def changelog_all(self):
        return changelog.between(self._changelog_path(), "0.0.0", __version__)

    def license_text(self):
        return self._paths.sam_license.read_text(encoding="utf-8")

    def about(self):
        """Trang Giới thiệu: phiên bản, giấy phép của tool và của phần mềm, model đi kèm (desktop/licenses/third_party.json)."""
        data = json.loads((self._paths.app / "desktop" / "licenses" / "third_party.json").read_text(encoding="utf-8"))
        lic = self._paths.app / data["tool"]["license_file"]
        data["tool"]["license_text"] = lic.read_text(encoding="utf-8") if lic.exists() else ""
        data["version"] = __version__
        return data

    def open_url_fixed(self, kind, index):
        """Mở trang chính thức của một model / phần mềm trong danh sách giấy phép (chỉ địa chỉ có sẵn trong danh sách)."""
        data = json.loads((self._paths.app / "desktop" / "licenses" / "third_party.json").read_text(encoding="utf-8"))
        items = data["models"] if kind == "m" else data["software"]
        try:
            url = items[int(index)]["url"]
        except (IndexError, ValueError, KeyError):
            return False
        if not url.startswith("https://"):
            return False
        import webbrowser

        webbrowser.open(url)
        return True

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
        p["items"], p["order"] = dict(p.get("items") or {}), list(p.get("order") or [])
        p["titles"] = {k: COMPONENTS[k]["title"] for k in p["order"]}
        # Tiến độ tổng: số thành phần đã xong + phần đang tải của thành phần hiện tại
        n = len(p["order"])
        finished = sum(1 for k in p["order"] if p["items"].get(k) in ("done", "skip"))
        frac = (p["done"] / p["total"]) if p["total"] else 0
        p["overall"] = round(100 * (finished + (frac if p["running"] else 0)) / n) if n else (100 if p["finished"] else 0)
        if p["finished"] and not p["error"]:
            p["overall"] = 100
        rate = p.get("rate") or 0
        p["rate_h"] = (human(int(rate)) + "/s") if rate > 1024 and p["running"] and p["total"] else ""
        left = (p["total"] - p["done"]) / rate if rate > 1024 and p["total"] else None
        p["eta_h"] = ("" if left is None else f"còn khoảng {int(left // 60) + 1} phút" if left >= 60 else "còn dưới 1 phút")
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

    def work_stats(self):
        """Số liệu công việc (panel đếm) cho trang Tổng quan."""
        from . import workstats

        return workstats.summary(workstats.load(self._paths.store))

    def export_stats(self):
        from . import workstats

        name = f"sam3-assist-bao-cao-{time.strftime('%Y-%m-%d')}.csv"
        dest = self._pick_file(True, name) if self._pick_file else str(Path.home() / name)
        if not dest:
            return {"ok": False, "message": ""}
        Path(dest).write_text(workstats.csv_text(workstats.load(self._paths.store)), encoding="utf-8")
        return {"ok": True, "message": f"Đã xuất báo cáo vào {dest}."}

    def note_rollback(self, note: dict) -> None:
        """Bản mới không mở được, script đã trả bản cũ về: không mời cập nhật lên bản đó nữa, báo trên bảng điều khiển."""
        self._rolled_back = dict(note)
        if note.get("to"):
            self._settings.data["skip_version"] = note["to"]
            self._settings.save()

    def selftest(self):
        """Kiểm tra nhanh sau khi cài (và nút ở trang Hỗ trợ): model chạy được trên máy này chưa. Lưu kết quả lần cuối."""
        r = self._backend.selftest()
        r["at"] = time.strftime("%Y-%m-%d %H:%M")
        self._settings.data["last_selftest"] = {k: r.get(k) for k in ("ok", "at", "seconds", "found", "engine", "device", "error", "is_mock")}
        self._settings.save()
        log.info("Self-test: %s", "ok" if r.get("ok") else r.get("error"))
        return r

    def read_log(self, which="backend", n=400):
        """Dòng cuối của nhật ký cho trang Hỗ trợ (chỉ hai file nhật ký của app)."""
        from .backend_proc import read_tail

        path = {"backend": self._paths.logs / "backend.log", "app": self._paths.logs / "app.log"}.get(which)
        if path is None:
            return {"lines": [], "file": ""}
        return {"lines": read_tail(path, max(20, min(2000, int(n)))), "file": str(path)}

    def add_site(self, name, url):
        origin = normalize_site(url)
        if not origin:
            return {"ok": False, "message": "Địa chỉ không hợp lệ. Ví dụ: https://cvat.ten-mien.com hoặc localhost:8080"}
        warn = "" if origin.startswith("https://") or is_local(origin) else \
            "Trang dùng http (không mã hoá) trên mạng ngoài: mật khẩu CVAT có thể bị đọc trộm. Nên dùng https."
        sites = [s for s in self._settings.data["sites"] if normalize_site(s["url"]) != origin]
        sites.append({"name": (name or "").strip()[:60] or origin, "url": origin, "platform": "cvat"})
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
        if self._backend.state in ("stopped", "error", "missing") and (self._comps.status()["ready"] or getattr(self._backend, 'remote', None)):
            self._backend.start()
        self._open_site(origin)
        return {"ok": True}

    # ------------------------------------------------- hỗ trợ và chia sẻ ---
    def report(self, note: str = ""):
        """Gói báo lỗi một chạm: nhật ký + thông tin máy (+ mô tả nếu người dùng viết), không có token/key/cookie/ảnh."""
        from . import support

        out = support.report_bundle(self._paths, {"app_version": __version__, "backend": self._backend.info(),
                                                  "components": self._comps.status()}, note=str(note or ""))
        self._reveal(out)
        return {"ok": True, "file": str(out)}

    def backup(self):
        from . import support

        name = f"sam3-assist-sao-luu-{time.strftime('%Y%m%d')}.zip"
        dest = self._pick_file(True, name) if self._pick_file else str(Path.home() / name)
        if not dest:
            return {"ok": False, "message": ""}
        res = support.backup(self._paths, Path(dest))
        return {"ok": True, "message": f"Đã sao lưu {res['files']} file vào {res['file']}."}

    def restore(self):
        from . import support
        from .bridge import Store

        src = self._pick_file(False, "") if self._pick_file else None
        if not src:
            return {"ok": False, "message": ""}
        try:
            res = support.restore(self._paths, Path(src), Store(self._paths.store))
        except (ValueError, OSError) as exc:
            return {"ok": False, "message": str(exc)}
        self._backend.restart() if self._backend.state == "running" else None
        return {"ok": True, "message": f"Đã khôi phục {res['restored']} file. Dữ liệu trước đó được lưu ở {res['safety_backup']}."}

    def set_sync_folder(self):
        folder = self._pick_folder() if self._pick_folder else None
        if folder:
            self._settings.data["sync_dir"] = folder
            self._settings.save()
        return self._settings.data.get("sync_dir", "")

    def sync(self, direction):
        from . import support

        folder = self._settings.data.get("sync_dir")
        if not folder or direction not in ("push", "pull"):
            return {"ok": False, "message": "Chưa chọn thư mục chung."}
        try:
            res = support.sync(self._paths, Path(folder), direction)
        except OSError as exc:
            return {"ok": False, "message": f"Không đồng bộ được: {exc}"}
        if direction == "pull" and res["copied"] and self._backend.state == "running" and not getattr(self._backend, 'remote', None):
            self._backend.request("POST", "/profiles/reload", "{}")
        what = "Đã gửi lên" if direction == "push" else "Đã lấy về"
        extra = f" {len(res['conflicts'])} file hai bên sửa khác nhau: bản ở thư mục chung được lưu cạnh với đuôi .tu-thu-muc-chung." if res["conflicts"] else ""
        return {"ok": True, "message": f"{what} {len(res['copied'])} file.{extra}", **res}

    def lan_info(self):
        from . import support

        st = self._settings.data
        return {"share": bool(st.get("lan_share")), "key": st.get("lan_key", "") if st.get("lan_share") else "",
                "addresses": support.lan_addresses(), "port": getattr(self._backend, "port", None) or getattr(self._backend, "preferred_port", 8765),
                "remote": {"url": (st.get("remote_backend") or {}).get("base", "")}}

    def set_lan_share(self, on):
        from . import support

        st = self._settings.data
        st["lan_share"] = bool(on)
        if on and not st.get("lan_key"):
            st["lan_key"] = support.new_lan_key()
        self._settings.save()
        self._backend.share_key = st["lan_key"] if on else ""
        if self._backend.state in ("running", "starting") and not getattr(self._backend, 'remote', None):
            self._backend.restart()
        return self.lan_info()

    def set_remote(self, url, key):
        from . import support

        res = support.check_remote(url, key)
        if not res["ok"]:
            return res
        self._settings.data["remote_backend"] = {"base": res["base"], "key": key}
        self._settings.save()
        self._backend.stop()
        self._backend.remote = {"base": res["base"], "key": key}
        self._backend.start()
        return res

    def clear_remote(self):
        self._settings.data.pop("remote_backend", None)
        self._settings.save()
        self._backend.stop()
        self._backend.remote = None
        if self._comps.status()["ready"]:
            self._backend.start()
        return True

    def _reveal(self, path: Path) -> None:
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
        except OSError:
            pass

    # ------------------------------------------------------------ job gần đây ---
    def _record_visit(self, url):
        """Gọi khi cửa sổ CVAT mở một trang: nhớ địa chỉ gần nhất và các job vừa làm."""
        origin = normalize_site(url)
        if origin not in self._settings.origins():
            return
        st = self._settings.data
        st["last_url"] = url
        m = re.search(r"/tasks/(\d+)/jobs/(\d+)", url or "")
        if m:
            clean = f"{origin}/tasks/{m.group(1)}/jobs/{m.group(2)}"
            site = next((x["name"] for x in st["sites"] if normalize_site(x["url"]) == origin), origin)
            item = {"url": clean, "task": int(m.group(1)), "job": int(m.group(2)), "site": site, "at": time.strftime("%Y-%m-%d %H:%M")}
            st["recent_jobs"] = [item] + [j for j in st["recent_jobs"] if j["url"] != clean][:7]
        self._settings.save()

    def recent_jobs(self):
        return self._settings.data["recent_jobs"]

    def open_job(self, url):
        if normalize_site(url) not in self._settings.origins() or not re.search(r"/tasks/\d+/jobs/\d+", url or ""):
            return {"ok": False, "message": "Địa chỉ job không thuộc danh sách CVAT."}
        if self._backend.state in ("stopped", "error") and self._comps.status()["ready"]:
            self._backend.start()
        self._open_site(url)
        return {"ok": True}

    def set_setting(self, key, value):
        """Các cài đặt đơn giản của app (chỉ những khoá biết trước)."""
        allowed = {"start_mode": ("cvat", "control"), "idle_unload_min": None, "autostart_backend": None, "lang": ("vi", "en"),
                   "auto_restart_backend": None, "theme": ("system", "light", "dark")}
        if key not in allowed:
            return False
        if key == "idle_unload_min":
            value = max(0, min(240, int(value)))
            self._backend.extra_env["SAM3_IDLE_UNLOAD_MIN"] = str(value)
        elif key in ("autostart_backend", "auto_restart_backend"):
            value = bool(value)
            if key == "auto_restart_backend":
                self._backend.auto_restart = value
        elif allowed[key] and value not in allowed[key]:
            return False
        self._settings.data[key] = value
        self._settings.save()
        return True

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
               "nvidia": "https://www.nvidia.com/Download/index.aspx",
               "vcredist": "https://aka.ms/vs/17/release/vc_redist.x64.exe",
               "repo": f"https://github.com/{U.REPO}", "releases": f"https://github.com/{U.REPO}/releases",
               "guide": f"https://github.com/{U.REPO}/blob/main/docs/HUONG_DAN.md",
               "privacy": f"https://github.com/{U.REPO}/blob/main/docs/PRIVACY.md"}.get(which)
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
        if self._settings.data.get("autostart_backend") and (self._comps.status()["ready"] or getattr(self._backend, 'remote', None)) and not self._setup_mode:
            self._backend.start()
        return res


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "SAM3 Label Assist"
LEGACY_RUN_NAMES = ("SAM3 CVAT Assist",)    # tên trước 0.9.1: bản cũ đã bật tự chạy thì vẫn nhận ra, và được dọn khi bật/tắt lại


def _launcher() -> str:
    return os.environ.get("SAM3_LAUNCHER", "")


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            for name in (RUN_NAME, *LEGACY_RUN_NAMES):
                try:
                    winreg.QueryValueEx(k, name)
                    return True
                except OSError:
                    pass
    except OSError:
        pass
    return False


def set_autostart(on: bool) -> None:
    if sys.platform != "win32" or not _launcher():
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        for name in ((RUN_NAME, *LEGACY_RUN_NAMES) if not on else LEGACY_RUN_NAMES):
            try:
                winreg.DeleteValue(k, name)
            except OSError:
                pass
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{_launcher()}" --tray')


def _translated(fn):
    """Kết quả trả cho bảng điều khiển được dịch theo ngôn ngữ giao diện (thông báo, trạng thái, tên thành phần).

    Lỗi bất ngờ được ghi vào app.log (kèm traceback) rồi báo lên trang, để nút bấm không bao giờ "im lặng"."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return translate_obj(fn(self, *args, **kwargs), self._lang())
        except Exception:
            log.exception("Control panel action %s failed", fn.__name__)
            raise
    return wrapper


for _name, _fn in list(vars(ControlApi).items()):
    if not _name.startswith("_") and callable(_fn) and _name not in ("license_text", "ui_lang"):
        setattr(ControlApi, _name, _translated(_fn))
