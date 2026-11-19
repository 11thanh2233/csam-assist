import sys
import traceback


def _t(msg: str) -> str:
    """Dịch hộp thoại lỗi theo ngôn ngữ đã chọn; đọc thẳng file cài đặt vì app chưa chạy được."""
    try:
        import json

        from backend.i18n import tr

        from .paths import Paths

        lang = json.loads(Paths().settings.read_text(encoding="utf-8")).get("lang", "vi")
        return tr(msg, lang)
    except Exception:  # noqa: BLE001
        return msg


def _fatal(exc: BaseException) -> None:
    """pythonw không có console: lỗi lúc khởi động phải được ghi ra file và báo bằng hộp thoại."""
    text = "".join(traceback.format_exception(exc))
    try:
        from .paths import Paths

        p = Paths().ensure()
        (p.logs / "startup-error.log").write_text(text, encoding="utf-8")
        where = str(p.logs / "startup-error.log")
    except Exception:  # noqa: BLE001
        where = "(không ghi được nhật ký)"
    if sys.platform == "win32":
        import ctypes

        msg = f"SAM3 CVAT Assist không khởi động được:\n\n{type(exc).__name__}: {exc}\n\nChi tiết: {where}"
        if "webview" in text.lower() and ("webview2" in text.lower() or "edgechromium" in text.lower()):
            msg += "\n\nCó thể máy thiếu Microsoft Edge WebView2 Runtime. Tải tại: https://developer.microsoft.com/microsoft-edge/webview2/"
        ctypes.windll.user32.MessageBoxW(None, _t(msg), "SAM3 CVAT Assist", 0x10)
    else:
        print(text, file=sys.stderr)


try:
    from .app import main

    sys.exit(main())
except SystemExit:
    raise
except BaseException as exc:  # noqa: BLE001
    _fatal(exc)
    sys.exit(1)
