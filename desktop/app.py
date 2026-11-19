"""Cửa sổ của app (pywebview, dùng trình duyệt Edge WebView2 có sẵn trong Windows).

  python -m desktop            bảng điều khiển; mở CVAT từ đây
  python -m desktop --setup    trình cài lần đầu (bộ cài gọi sau khi chép file)
  python -m desktop --tray     chạy nền ở khay hệ thống (khi tự bật cùng Windows)
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from backend import __version__
from backend.i18n import install_log_filter, norm_lang, tr

from .backend_proc import BackendProcess
from .bridge import Bridge, Store, origin_of
from .components import Components
from .control import ControlApi
from .paths import Paths

log = logging.getLogger("sam3-desktop")
MUTEX_NAME = "SAM3CvatAssistDesktopMutex"


def single_instance() -> bool:
    """Giữ một mutex tên cố định (bộ cài dùng nó để nhắc tắt app trước khi cập nhật). False nếu app đã chạy."""
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    single_instance.handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


def set_clipboard(text: str) -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.restype = wintypes.LPVOID
    data = text.encode("utf-16-le") + b"\0\0"
    if not user32.OpenClipboard(None):
        return
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0042, len(data))
        ctypes.memmove(kernel32.GlobalLock(h), data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(13, h)              # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()


class DesktopApp:
    def __init__(self, setup_mode: bool = False, tray: bool = False):
        import webview

        self.webview = webview
        self.paths = Paths().ensure()
        logging.basicConfig(filename=self.paths.logs / "app.log", level=logging.INFO, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(message)s")
        install_log_filter()                         # app.log luôn là tiếng Anh kỹ thuật
        self.comps = Components(self.paths)
        self.backend = BackendProcess(self.comps)
        self.setup_mode, self.start_hidden = setup_mode, tray
        self.cvat_window = None
        self.tray_icon = None
        self.quitting = False
        self.api = ControlApi(self.paths, self.comps, self.backend, open_site=self.open_cvat, on_quit=self.quit,
                              setup_mode=setup_mode)
        self.shim = (self.paths.app / "desktop" / "shim.js").read_text(encoding="utf-8")
        self.userscript = (self.paths.app / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
        self.store = Store(self.paths.store)

    # ------------------------------------------------------------ windows ---
    def run(self) -> None:
        webview = self.webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        html = (self.paths.app / "desktop" / "ui" / "control.html").read_text(encoding="utf-8")
        # Gọn một cửa sổ: đã cài đủ thì mở thẳng CVAT, bảng điều khiển nằm ở khay hệ thống và nút "SAM3" trong trang.
        straight = (not self.setup_mode and not self.start_hidden and (self.comps.status()["ready"] or self.backend.remote)
                    and self.api._settings.data.get("start_mode", "cvat") == "cvat" and self.api._settings.data["sites"])
        self.control = webview.create_window(
            "SAM3 CVAT Assist" + (tr(" - Cài đặt", self.api._lang()) if self.setup_mode else ""), html=html, js_api=self.api,
            width=980, height=760, min_size=(760, 560), hidden=self.start_hidden or bool(straight))
        if straight:
            st = self.api._settings.data
            self.open_cvat(st.get("last_url") or st["sites"][0]["url"])
        self.control.events.closing += self._on_control_closing
        if not self.setup_mode:
            self._start_tray()
        webview.start(self._after_start, private_mode=False, storage_path=str(self.paths.webview), debug=False)
        self.backend.stop()
        if self.tray_icon:
            self.tray_icon.stop()

    def _after_start(self) -> None:
        self.api._pick_folder = self._pick_folder
        self.api._pick_file = self._pick_file
        try:
            self.api._prepare()
        except Exception:  # noqa: BLE001
            log.exception("Chuẩn bị dữ liệu lỗi")
        if not self.setup_mode:
            try:
                self.api.check_update()          # tối đa một lần mỗi ngày, tắt được trong bảng điều khiển
            except Exception:  # noqa: BLE001
                log.exception("Kiểm bản mới lỗi")

    def _pick_folder(self):
        kind = getattr(getattr(self.webview, "FileDialog", None), "FOLDER", None) or self.webview.FOLDER_DIALOG
        res = self.control.create_file_dialog(kind)
        return res[0] if res else None

    def _pick_file(self, save: bool, name: str):
        fd = getattr(self.webview, "FileDialog", None)
        kind = (getattr(fd, "SAVE", None) if save else getattr(fd, "OPEN", None)) or \
            (self.webview.SAVE_DIALOG if save else self.webview.OPEN_DIALOG)
        res = self.control.create_file_dialog(kind, save_filename=name or "", file_types=("Zip (*.zip)",))
        if not res:
            return None
        return res if isinstance(res, str) else res[0]

    def _on_control_closing(self):
        if self.setup_mode or self.quitting or self.tray_icon is None:
            self.quit(close_windows=False)
            return True
        self.control.hide()                       # còn biểu tượng ở khay: chỉ ẩn bảng điều khiển
        return False

    def open_cvat(self, origin: str) -> None:
        if origin_of(origin) not in self.api._settings.origins():
            return
        if self.cvat_window is not None:
            try:
                self.cvat_window.load_url(origin)
                self.cvat_window.show()
                return
            except Exception:  # noqa: BLE001 - cửa sổ đã bị đóng
                self.cvat_window = None
        bridge = Bridge(self.store, self.backend, self.api._settings.origins, self._cvat_url, set_clipboard,
                        notifier=self.notify, show_control=self.show_control,
                        lang=self.api._lang, set_lang=lambda v: self.api.set_setting("lang", v))
        self.bridge = bridge
        self.cvat_window = self.webview.create_window("CVAT - SAM3 Assist", url=origin, js_api=bridge,
                                                      width=1500, height=950, min_size=(900, 600))
        self.cvat_window.events.loaded += self._inject
        self.cvat_window.events.closed += self._cvat_closed

    def _cvat_url(self) -> str:
        try:
            return self.cvat_window.get_current_url() or ""
        except Exception:  # noqa: BLE001
            return ""

    def _cvat_closed(self) -> None:
        self.cvat_window = None

    def _inject(self) -> None:
        url = self._cvat_url()
        if origin_of(url) not in self.api._settings.origins():
            log.info("Không tiêm bảng vào %s (không nằm trong danh sách CVAT)", origin_of(url))
            return
        self.api._record_visit(url)
        code = self.bridge._boot_script(self.shim, self.userscript, __version__)
        try:
            run = getattr(self.cvat_window, "run_js", None) or self.cvat_window.evaluate_js
            run(code)
        except Exception:  # noqa: BLE001
            log.exception("Tiêm bảng vào CVAT lỗi")

    def show_control(self) -> None:
        try:
            self.control.show()
            self.control.restore()
        except Exception:  # noqa: BLE001
            pass

    def notify(self, title: str, text: str) -> None:
        """Thông báo Windows (qua biểu tượng ở khay hệ thống)."""
        icon = self.tray_icon
        try:
            if icon is not None and getattr(icon, "HAS_NOTIFICATION", True):
                icon.notify(tr(text, self.api._lang()), tr(title, self.api._lang()))
        except Exception:  # noqa: BLE001
            log.info("Không hiện được thông báo: %s - %s", title, text)

    # --------------------------------------------------------------- tray ---
    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image
        except Exception:  # noqa: BLE001 - không có khay thì đóng cửa sổ là thoát app
            return
        icon_path = self.paths.app / "desktop" / "ui" / "icon.png"
        image = Image.open(icon_path) if icon_path.exists() else Image.new("RGB", (64, 64), (37, 99, 235))
        lang = self.api._lang()
        menu = pystray.Menu(
            pystray.MenuItem(tr("Mở bảng điều khiển", lang), lambda: self.show_control(), default=True),
            pystray.MenuItem(tr("Mở CVAT", lang), lambda: self.open_cvat(self.api._settings.data.get("last_url")
                                                              or (self.api._settings.data["sites"] or [{"url": ""}])[0]["url"])),
            pystray.MenuItem(tr("Thoát", lang), lambda: self.quit()),
        )
        self.tray_icon = pystray.Icon("sam3-cvat-assist", image, "SAM3 CVAT Assist", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def quit(self, close_windows: bool = True) -> None:
        if self.quitting:
            return
        self.quitting = True
        self.backend.stop()
        if close_windows:
            for w in list(self.webview.windows):
                try:
                    w.destroy()
                except Exception:  # noqa: BLE001
                    pass


def _saved_lang() -> str:
    """Ngôn ngữ đã chọn (đọc thẳng file cài đặt, dùng khi app chưa mở cửa sổ)."""
    try:
        import json
        return json.loads(Paths().settings.read_text(encoding="utf-8")).get("lang", "vi")
    except Exception:  # noqa: BLE001
        return "vi"


def _save_lang(lang: str) -> None:
    """Ghi ngôn ngữ vào cài đặt của app (panel và bảng điều khiển dùng chung)."""
    import json
    path = Paths().ensure().settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["lang"] = lang
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="SAM3 Assist")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--tray", action="store_true")
    ap.add_argument("--lang", choices=("vi", "en"))      # ngôn ngữ chọn trong bộ cài
    args = ap.parse_args(argv)
    if args.lang:
        _save_lang(args.lang)
    if not args.setup and not single_instance():
        if sys.platform == "win32":
            import ctypes

            lang = norm_lang(_saved_lang())
            ctypes.windll.user32.MessageBoxW(None, tr("SAM3 CVAT Assist đang chạy. Bấm vào biểu tượng ở khay hệ thống để mở.", lang),
                                             "SAM3 CVAT Assist", 0x40)
        return 0
    DesktopApp(setup_mode=args.setup, tray=args.tray).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
