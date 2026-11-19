"""Cầu nối giữa bảng của tool (chạy trong trang CVAT) và phần Python của app. Thay cho Tampermonkey.

Bảo mật:
  - Chỉ trang thuộc các địa chỉ CVAT bạn đã thêm mới được tiêm bảng và gọi cầu nối. Trang khác (link ngoài mở
    trong cùng cửa sổ) gọi vào sẽ bị từ chối.
  - Cầu nối chỉ chuyển yêu cầu tới backend của app (127.0.0.1), không tới địa chỉ nào khác.
  - API key của backend do app thêm vào, trang web không đọc được.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

MAX_VALUE = 16_000_000
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def origin_of(url: str) -> str:
    try:
        u = urllib.parse.urlsplit(url or "")
        if u.scheme not in ("http", "https") or not u.hostname:
            return ""
        u.port
    except ValueError:
        return ""
    port = f":{u.port}" if u.port and not ((u.scheme == "https" and u.port == 443) or (u.scheme == "http" and u.port == 80)) else ""
    return f"{u.scheme}://{u.hostname.lower()}{port}"


def normalize_site(url: str) -> str:
    """Chuẩn hoá địa chỉ CVAT người dùng nhập. Trả origin, hoặc "" nếu không hợp lệ."""
    url = (url or "").strip()
    if url and "://" not in url:
        url = ("http://" if re.match(r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", url) else "https://") + url
    return origin_of(url)


def is_local(origin: str) -> bool:
    host = urllib.parse.urlsplit(origin).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host))


class Store:
    """Bộ nhớ của bảng (thay GM_getValue/GM_setValue): mỗi khoá một file JSON."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _file(self, key: str) -> Path:
        return self.folder / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def all(self) -> dict:
        out = {}
        for f in self.folder.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                out[rec["key"]] = rec["value"]
            except (OSError, ValueError, KeyError):
                continue
        return out

    def set(self, key: str, value) -> None:
        if not isinstance(key, str) or not key or len(key) > 300:
            raise ValueError("Khoá không hợp lệ.")
        f = self._file(key)
        if value is None:
            f.unlink(missing_ok=True)
            return
        text = json.dumps({"key": key, "value": value}, ensure_ascii=False)
        if len(text) > MAX_VALUE:
            raise ValueError("Giá trị quá lớn để lưu.")
        tmp = f.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(f)


class Bridge:
    """Đối tượng gắn vào cửa sổ CVAT (pywebview js_api). Chỉ hàm không bắt đầu bằng '_' được trang gọi."""

    def __init__(self, store: Store, backend, allowed_origins: Callable[[], set], current_url: Callable[[], str],
                 clipboard: Optional[Callable[[str], None]] = None, notifier: Optional[Callable[[str, str], None]] = None,
                 show_control: Optional[Callable[[], None]] = None):
        self._store, self._backend = store, backend
        self._allowed, self._current_url, self._clip = allowed_origins, current_url, clipboard
        self._notifier, self._show_control = notifier, show_control

    # -------------------------------------------------------------- checks ---
    def _check_page(self) -> None:
        if origin_of(self._current_url()) not in self._allowed():
            raise PermissionError("Trang này không nằm trong danh sách CVAT của app.")

    def _backend_path(self, url: str) -> str:
        u = urllib.parse.urlsplit(url or "")
        if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost"):
            raise PermissionError("Trong app, bảng chỉ gọi được backend của app (127.0.0.1).")
        path = u.path or "/"
        if not path.startswith("/") or ".." in path:
            raise PermissionError("Đường dẫn không hợp lệ.")
        return path + (f"?{u.query}" if u.query else "")

    # ---------------------------------------------------------- page API ---
    def gm_set(self, key, value):
        self._check_page()
        self._store.set(key, value)
        return True

    def gm_request(self, opts):
        """Thay GM_xmlhttpRequest. Trả {status, statusText, responseText} hoặc {error: 'network'|'denied'}."""
        try:
            self._check_page()
            opts = opts if isinstance(opts, dict) else {}
            method = str(opts.get("method") or "GET").upper()
            if method not in ALLOWED_METHODS:
                raise PermissionError("Phương thức không được phép.")
            path = self._backend_path(str(opts.get("url") or ""))
        except PermissionError as exc:
            return {"error": "denied", "status": 0, "responseText": json.dumps({"detail": str(exc)}, ensure_ascii=False)}
        info = self._backend.info()
        if info["state"] != "running":
            msg = info.get("message") or "Backend chưa chạy."
            return {"status": 503, "statusText": "Service Unavailable",
                    "responseText": json.dumps({"detail": f"{msg} (mở bảng điều khiển SAM3 Assist)"}, ensure_ascii=False)}
        data = opts.get("data")
        timeout = min(max(float(opts.get("timeout") or 300000) / 1000, 5), 900)
        try:
            status, text = self._backend.request(method, path, data if data is None or isinstance(data, str) else json.dumps(data),
                                                 timeout=timeout)
        except TimeoutError:
            return {"error": "timeout", "status": 0, "responseText": ""}
        except OSError as exc:
            return {"error": "network", "status": 0, "responseText": str(exc)}
        return {"status": status, "statusText": "", "responseText": text}

    def notify(self, title, text):
        """Thông báo Windows khi việc dài trong panel xong (gợi ý cả job, rà cả job, theo dõi)."""
        self._check_page()
        if self._notifier:
            self._notifier(str(title)[:80], str(text)[:250])
        return True

    def open_control(self):
        self._check_page()
        if self._show_control:
            self._show_control()
        return True

    def clipboard(self, text):
        self._check_page()
        if self._clip:
            self._clip(str(text))
        return True

    # ------------------------------------------------------- for the app ---
    def _boot_script(self, shim: str, userscript: str, version: str) -> str:
        """Mã tiêm vào trang: dữ liệu khởi động + shim GM_* + userscript."""
        page_url = getattr(self._backend, "page_url", self._backend.base_url)
        boot = {"values": self._store.all(), "version": version, "backendUrl": page_url}
        cfg = dict(boot["values"].get("cfg") or {})
        cfg["backendUrl"] = page_url                       # backend của app (cầu nối chuyển tiếp), cổng có thể khác 8765
        cfg["apiKey"] = ""                                 # key do app thêm, không lưu trong trang
        boot["values"]["cfg"] = cfg
        return ("window.__sam3Boot = " + json.dumps(boot, ensure_ascii=False) + ";\n" + shim + "\n"
                "if (window.__sam3ShimReady && !window.__sam3ScriptRan) { window.__sam3ScriptRan = true;\n"
                + userscript + "\n}\n")
