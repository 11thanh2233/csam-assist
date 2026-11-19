"""Tự kiểm bản mới trên GitHub Releases và cập nhật app.

An toàn:
  - Chỉ hỏi đúng repo của tool, chỉ tải file bộ cài đúng tên từ github.com qua HTTPS.
  - Bộ cài phải có mã SHA-256 (GitHub công bố cho từng file của Release, hoặc dòng "SHA-256" trong ghi chú).
    Tải xong mà sai mã thì xoá, không chạy.
  - Không bao giờ hạ phiên bản.
  - Cập nhật chỉ thay phần app; PyTorch, trọng số, profile và dữ liệu tự học giữ nguyên.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import downloader as D

REPO = "11thanh2233/csam-assist"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_RE = re.compile(r"^SAM3-CVAT-Assist-Setup-(\d+\.\d+\.\d+)\.exe$")
SHA_RE = re.compile(r"SHA-?256[^`\n]*`([0-9a-fA-F]{64})`")


def parse_version(v: str) -> tuple:
    nums = re.findall(r"\d+", (v or "").split("+")[0].split("-")[0])
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def check(current: str, api: str = API, allow_http: tuple[str, ...] = (), prefix: str = "") -> dict:
    """{'available': bool, 'version', 'notes', 'url', 'size', 'sha256', 'auto': bool, 'reason', 'page'}"""
    rel = D.get_json(api, allow_http=allow_http)
    tag = str(rel.get("tag_name") or "")
    version = tag.lstrip("vV")
    out = {"available": False, "current": current, "version": version, "notes": rel.get("body") or "",
           "page": rel.get("html_url") or f"https://github.com/{REPO}/releases", "auto": False, "reason": "",
           "published": rel.get("published_at") or ""}
    if rel.get("draft") or rel.get("prerelease") or parse_version(version) <= parse_version(current):
        return out
    out["available"] = True
    asset = next((a for a in rel.get("assets") or [] if ASSET_RE.match(a.get("name", ""))
                  and ASSET_RE.match(a["name"]).group(1) == version), None)
    if asset is None:
        out["reason"] = "Bản này chưa có file bộ cài trên GitHub."
        return out
    url = asset.get("browser_download_url") or ""
    if not url.startswith(prefix or f"https://github.com/{REPO}/releases/download/"):
        out["reason"] = "Địa chỉ tải bộ cài lạ, không tự cập nhật."
        return out
    digest = (asset.get("digest") or "").lower()
    sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else ""
    if not sha:
        m = SHA_RE.search(out["notes"])
        sha = m.group(1).lower() if m else ""
    if not sha:
        out["reason"] = "Bản này không công bố mã SHA-256 của bộ cài, không tự cập nhật được. Tải tay ở trang Releases."
        return out
    out.update(auto=True, url=url, size=asset.get("size"), sha256=sha, name=asset["name"])
    return out


class Updater:
    def __init__(self, folder: Path, allow_http: tuple[str, ...] = ()):
        self.folder = Path(folder)
        self.allow_http = allow_http
        self.cancel = threading.Event()
        self.p = {"running": False, "done": 0, "total": None, "error": "", "ready": "", "version": ""}
        self.thread: Optional[threading.Thread] = None

    def start(self, info: dict) -> bool:
        if not info.get("auto") or (self.thread and self.thread.is_alive()):
            return False
        self.cancel.clear()
        self.p.update(running=True, done=0, total=info.get("size"), error="", ready="", version=info["version"])
        self.thread = threading.Thread(target=self._run, args=(info,), daemon=True)
        self.thread.start()
        return True

    def _run(self, info: dict) -> None:
        try:
            for old in self.folder.glob("SAM3-CVAT-Assist-Setup-*.exe"):     # bộ cài cũ: xoá cho đỡ chật
                if old.name != info["name"]:
                    old.unlink(missing_ok=True)
            dest = D.download(info["url"], self.folder / info["name"], sha256=info["sha256"], size=info.get("size"),
                              cancel=self.cancel, allow_http=self.allow_http,
                              progress=lambda d, t: self.p.update(done=d, total=t))
            with open(dest, "rb") as f:
                if f.read(2) != b"MZ":
                    dest.unlink()
                    raise D.DownloadError("File tải về không phải bộ cài Windows.")
            self.p["ready"] = str(dest)
        except D.Cancelled:
            self.p["error"] = "Đã dừng tải."
        except Exception as exc:  # noqa: BLE001
            self.p["error"] = str(exc)
        finally:
            self.p["running"] = False

    def launch(self, setup: Path) -> None:
        """Chạy bộ cài ở chế độ cập nhật sau khi app đã thoát (đợi vài giây để app nhả file)."""
        setup = Path(setup)
        script = self.folder / "run-update.cmd"
        script.write_text("@echo off\r\nchcp 65001 >nul\r\ntimeout /t 4 /nobreak >nul\r\n"
                          f'start "" "{setup}" /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /update=1\r\n',
                          encoding="utf-8")
        flags = 0x08000000 | 0x00000008 if sys.platform == "win32" else 0         # CREATE_NO_WINDOW | DETACHED_PROCESS
        subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True)


def due(last_check: float, hours: float = 20) -> bool:
    return time.time() - (last_check or 0) > hours * 3600
