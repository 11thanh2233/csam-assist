"""Hỗ trợ và chia sẻ: gói báo lỗi một chạm, sao lưu / khôi phục, đồng bộ qua thư mục chung, backend dùng chung trong LAN."""
from __future__ import annotations

import json
import platform
import re
import secrets
import shutil
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from backend.i18n import tr

from .paths import Paths

SECRET_RE = re.compile(r"(hf_[A-Za-z0-9]{8,}|(?<=X-API-Key: )\S+|(?<=api_key=)\S+|(?<=\"key\": \")[^\"]+)")
SETTINGS_SECRET_KEYS = {"lan_key", "remote_backend", "update_cache"}


def redact(text: str) -> str:
    return SECRET_RE.sub("<đã ẩn>", text)


def _settings_public(paths: Paths) -> dict:
    try:
        data = json.loads(paths.settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k not in SETTINGS_SECRET_KEYS}


def system_info(paths: Paths, extra: Optional[dict] = None) -> dict:
    from .components import detect_gpu

    gpu = detect_gpu()
    usage = shutil.disk_usage(paths.data)
    return dict({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "os": platform.platform(), "python": sys.version.split()[0],
        "gpu": {"name": gpu.name, "vram_mb": gpu.vram_mb, "driver": gpu.driver},
        "disk_free_gb": round(usage.free / 1e9, 1), "data_dir": str(paths.data),
    }, **(extra or {}))


def report_bundle(paths: Paths, extra: Optional[dict] = None, out_dir: Optional[Path] = None) -> Path:
    """Gói nhật ký + thông tin máy để gửi người hỗ trợ. KHÔNG có: token, API key, cookie đăng nhập, ảnh, dữ liệu panel."""
    out_dir = Path(out_dir or (Path.home() / "Downloads" if (Path.home() / "Downloads").is_dir() else paths.data))
    out = out_dir / f"sam3-assist-bao-loi-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for log in sorted(paths.logs.glob("*.log*")):
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            z.writestr(f"logs/{log.name}", redact(text[-2_000_000:]))
        z.writestr("system.json", json.dumps(system_info(paths, extra), ensure_ascii=False, indent=2))
        z.writestr("settings.json", redact(json.dumps(_settings_public(paths), ensure_ascii=False, indent=2)))
        if paths.state.exists():
            z.writestr("components.json", paths.state.read_text(encoding="utf-8"))
        # Gói báo lỗi viết bằng tiếng Anh kỹ thuật (như file nhật ký) để ai hỗ trợ cũng đọc được
        z.writestr("README.txt", tr("Gói báo lỗi của SAM3 CVAT Assist: nhật ký, thông tin máy, danh sách thành phần.\n"
                                    "Không chứa token Hugging Face, API key, cookie đăng nhập CVAT hay ảnh.\n", "en"))
    return out


# --------------------------------------------------------------- sao lưu ---
BACKUP_PARTS = ("profiles", "guideline_library")
BACKUP_SKIP = ("runs",)


def _iter_files(root: Path, skip=BACKUP_SKIP):
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if f.is_file() and not any(part in skip or part == "__pycache__" for part in rel.parts):
            yield f, rel


def backup(paths: Paths, dest: Path) -> dict:
    """Sao lưu profile, guideline, bài học, dữ liệu tự học và cài đặt của panel ra một file zip."""
    dest = Path(dest)
    n = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for part in BACKUP_PARTS:
            for f, rel in _iter_files(paths.data / part):
                z.write(f, f"{part}/{rel.as_posix()}")
                n += 1
        cfg_file = next((f for f in paths.store.glob("*.json") if '"key": "cfg"' in f.read_text(encoding="utf-8")[:40]), None)
        if cfg_file:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            data["value"] = {k: v for k, v in (data.get("value") or {}).items() if k not in ("apiKey", "backendUrl", "pos")}
            z.writestr("panel-cfg.json", json.dumps(data["value"], ensure_ascii=False))
        z.writestr("settings.json", json.dumps(_settings_public(paths), ensure_ascii=False, indent=2))
        z.writestr("backup.json", json.dumps({"app": "sam3-cvat-assist", "at": time.strftime("%Y-%m-%d %H:%M:%S"), "files": n}))
    return {"file": str(dest), "files": n}


def restore(paths: Paths, src: Path, store=None) -> dict:
    """Khôi phục từ file sao lưu. Trước khi ghi, tự sao lưu dữ liệu hiện tại (file 'truoc-khi-khoi-phuc-*.zip')."""
    src = Path(src)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        if "backup.json" not in names:
            raise ValueError("File này không phải bản sao lưu của SAM3 CVAT Assist.")
        safety = backup(paths, paths.data / f"truoc-khi-khoi-phuc-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        n = 0
        for name in names:
            parts = Path(name).parts
            if not parts or parts[0] not in BACKUP_PARTS or name.endswith("/"):
                continue
            if ".." in parts or Path(name).is_absolute() or ":" in name:
                raise ValueError(f"Đường dẫn không an toàn trong file sao lưu: {name}")
            target = paths.data / Path(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as fsrc, open(target, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            n += 1
        if "panel-cfg.json" in names and store is not None:
            cfg = json.loads(z.read("panel-cfg.json").decode("utf-8"))
            current = store.all().get("cfg") or {}
            store.set("cfg", dict(current, **{k: v for k, v in cfg.items() if k not in ("apiKey", "backendUrl")}))
    return {"restored": n, "safety_backup": safety["file"]}


# ----------------------------------------------------- thư mục chung (nhóm) ---
SYNC_SKIP = ("runs", "learning", "users")        # dữ liệu tự học là của từng người: không đồng bộ


def sync(paths: Paths, shared: Path, direction: str) -> dict:
    """Đồng bộ profile + guideline với thư mục chung (OneDrive, Google Drive, ổ mạng...).

    push: chép bản của mình lên (file mới hơn hoặc chưa có ở thư mục chung).
    pull: lấy về file mới hơn. Không bao giờ xoá file. File cùng tên mà hai bên đều sửa khác nhau: giữ bản của mình,
          bản ở thư mục chung được lưu cạnh với đuôi '.tu-thu-muc-chung'.
    """
    shared = Path(shared) / "SAM3Assist"
    copied, conflicts = [], []
    for part in BACKUP_PARTS:
        local_root, remote_root = paths.data / part, shared / part
        src_root, dst_root = (local_root, remote_root) if direction == "push" else (remote_root, local_root)
        if not src_root.exists():
            continue
        for f, rel in _iter_files(src_root, SYNC_SKIP):
            dst = dst_root / rel
            if dst.exists():
                if dst.read_bytes() == f.read_bytes():
                    continue
                if f.stat().st_mtime <= dst.stat().st_mtime:
                    if direction == "pull":
                        side = dst.with_name(dst.name + ".tu-thu-muc-chung")
                        shutil.copy2(f, side)
                        conflicts.append(f"{part}/{rel.as_posix()}")
                    continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            copied.append(f"{part}/{rel.as_posix()}")
    if direction == "push":
        shared.mkdir(parents=True, exist_ok=True)
        (shared / "LAST_PUSH.txt").write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {socket.gethostname()}\n", encoding="utf-8")
    return {"direction": direction, "copied": copied, "conflicts": conflicts, "folder": str(shared)}


# --------------------------------------------------------- backend dùng chung ---
def lan_addresses() -> list[str]:
    """Địa chỉ IP trong mạng nội bộ của máy này (để máy khác nhập vào)."""
    out = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            out.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            out.add(ip)
    except OSError:
        pass
    return sorted(ip for ip in out if not ip.startswith("127."))


def new_lan_key() -> str:
    return secrets.token_urlsafe(24)


def check_remote(url: str, key: str, timeout: float = 6) -> dict:
    """Thử kết nối backend ở máy khác: địa chỉ có hợp lệ không, có trả lời không, key có đúng không."""
    u = urllib.parse.urlsplit((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        return {"ok": False, "message": "Địa chỉ không hợp lệ. Ví dụ: http://192.168.1.20:8765"}
    from .bridge import is_local

    if u.scheme == "http" and not is_local(f"http://{u.hostname}"):
        return {"ok": False, "message": "Backend qua Internet phải dùng https. http chỉ cho máy trong mạng nội bộ (LAN)."}
    base = f"{u.scheme}://{u.netloc}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(base + "/health"), timeout=timeout) as r:
            health = json.loads(r.read().decode("utf-8"))
        with opener.open(urllib.request.Request(base + "/profiles", headers={"X-API-Key": key or ""}), timeout=timeout):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return {"ok": False, "message": "Kết nối được nhưng key không đúng."}
        return {"ok": False, "message": f"Backend trả lỗi {exc.code}."}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"Không kết nối được {base}: {exc}. Kiểm tra máy kia đã bật chia sẻ và tường lửa cho phép."}
    return {"ok": True, "message": f"Kết nối tốt: {health.get('engine')} trên {health.get('device')}, bản {health.get('version')}.",
            "base": base, "health": health}


__all__ = ["report_bundle", "backup", "restore", "sync", "lan_addresses", "new_lan_key", "check_remote", "redact"]
