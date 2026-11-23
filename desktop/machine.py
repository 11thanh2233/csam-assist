"""Kiểm tra máy (0.9.4): những thứ ngoài app mà app hoặc CVAT local cần.

Chỉ đọc, không cài gì: bản Windows, ổ đĩa trống, RAM, Microsoft Visual C++ Redistributable,
WebView2, WSL2 và Docker (hai thứ cuối chỉ cần khi chạy CVAT trên chính máy này).
Mỗi mục trả về {key, title, kind: ok|warn|info|fail, detail, fix}; fix là tên trang mở được
qua ControlApi.open_link hoặc "wsl_install" (cài WSL2, Windows hỏi quyền admin).
Các hàm đọc máy đều thay được (tham số probe) để test chạy trên Linux.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

MIN_BUILD_WSL2 = 19041          # Windows 10 2004: WSL2 chạy được
MIN_FREE_GB = 15                # PyTorch CUDA + model SAM 3 + chỗ tạm khi giải nén
MIN_RAM_GB = 8
VC_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _row(key, title, kind, detail, fix=None):
    return {"key": key, "title": title, "kind": kind, "detail": detail, "fix": fix}


# ------------------------------------------------------------------ probes ---
def windows_build() -> Optional[int]:
    if sys.platform != "win32":
        return None
    try:
        return int(sys.getwindowsversion().build)
    except Exception:  # noqa: BLE001
        return None


def free_gb(path: Path) -> Optional[float]:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(str(p)).free / 1024 ** 3
    except OSError:
        return None


def ram_gb() -> Optional[float]:
    if sys.platform == "win32":
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MS()
            st.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullTotalPhys / 1024 ** 3
        except Exception:  # noqa: BLE001
            return None
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (ValueError, OSError, AttributeError):
        return None


def vc_missing(system32: Optional[Path] = None) -> Optional[list]:
    """Tên các DLL Visual C++ còn thiếu; None khi không phải Windows."""
    if system32 is None:
        if sys.platform != "win32":
            return None
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return [d for d in VC_DLLS if not (Path(system32) / d).exists()]


def webview2_version() -> Optional[str]:
    """Bản WebView2 Runtime đã cài ('' = chưa cài); None khi không phải Windows."""
    if sys.platform != "win32":
        return None
    import winreg

    keys = [(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}")]
    for hive, sub in keys:
        try:
            with winreg.OpenKey(hive, sub) as k:
                v = str(winreg.QueryValueEx(k, "pv")[0])
                if v and v != "0.0.0.0":
                    return v
        except OSError:
            continue
    return ""


def wsl_present() -> Optional[bool]:
    if sys.platform != "win32":
        return None
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return (root / "System32" / "wsl.exe").exists()


def docker_present() -> Optional[bool]:
    if sys.platform != "win32":
        return None
    from . import cvat_local

    return bool(cvat_local.docker_exe() or cvat_local.docker_desktop_exe())


PROBES: dict = {"build": windows_build, "free_gb": free_gb, "ram_gb": ram_gb, "vc_missing": vc_missing,
                "webview2": webview2_version, "wsl": wsl_present, "docker": docker_present}


# ------------------------------------------------------------------- check ---
def check(data_dir: Path, probe: Optional[dict] = None) -> list:
    p = dict(PROBES, **(probe or {}))
    call: Callable = lambda k, *a: p[k](*a)  # noqa: E731
    rows = []

    build = call("build")
    if build is not None:
        if build >= 22000:
            rows.append(_row("windows", "Windows", "ok", f"Windows 11 (build {build})"))
        elif build >= MIN_BUILD_WSL2:
            rows.append(_row("windows", "Windows", "ok", f"Windows 10 (build {build})"))
        else:
            rows.append(_row("windows", "Windows", "warn",
                             f"Build {build} cũ hơn 19041 (Windows 10 2004): app vẫn chạy nhưng không chạy được CVAT local (WSL2). "
                             "Nên cập nhật Windows."))

    free = call("free_gb", data_dir)
    if free is not None:
        kind = "ok" if free >= MIN_FREE_GB else ("warn" if free >= 6 else "fail")
        rows.append(_row("disk", "Ổ đĩa trống", kind,
                         f"Còn {free:.0f} GB ở ổ chứa dữ liệu app" +
                         ("" if kind == "ok" else f". Cần khoảng {MIN_FREE_GB} GB cho PyTorch và model SAM 3.")))

    ram = call("ram_gb")
    if ram is not None:
        rows.append(_row("ram", "RAM", "ok" if ram >= MIN_RAM_GB - 0.5 else "warn",
                         f"{ram:.0f} GB" + ("" if ram >= MIN_RAM_GB - 0.5 else
                                             ". Dưới 8 GB: nạp model và CVAT local cùng lúc có thể chậm hoặc thiếu bộ nhớ.")))

    missing = call("vc_missing")
    if missing is not None:
        rows.append(_row("vcredist", "Microsoft Visual C++ Redistributable", "ok", "Đã có (PyTorch cần)") if not missing else
                    _row("vcredist", "Microsoft Visual C++ Redistributable", "fail",
                         "Thiếu " + ", ".join(missing) + ": PyTorch sẽ không chạy. Bộ cài của app thường tự cài; nếu vẫn thiếu, tải và cài bản x64.",
                         "vcredist"))

    wv = call("webview2")
    if wv is not None:
        rows.append(_row("webview2", "Microsoft Edge WebView2", "ok", f"Bản {wv}") if wv else
                    _row("webview2", "Microsoft Edge WebView2", "warn",
                         "Chưa thấy. Cửa sổ app và CVAT trong app cần nó; bộ cài app tự cài khi có mạng.", "webview2"))

    wsl, docker = call("wsl"), call("docker")
    if wsl is not None:
        rows.append(_row("wsl", "WSL2 (chỉ cho CVAT local)", "ok" if wsl else "info",
                         "Đã có lệnh wsl" if wsl else "Chưa có. Chỉ cần khi chạy CVAT trên chính máy này.",
                         None if wsl else ("wsl_install" if (build or 0) >= MIN_BUILD_WSL2 else "wsl")))
    if docker is not None:
        rows.append(_row("docker", "Docker Desktop (chỉ cho CVAT local)", "ok" if docker else "info",
                         "Đã cài" if docker else
                         "Chưa cài. Chỉ cần khi chạy CVAT trên chính máy này. Docker Desktop miễn phí cho cá nhân, học tập "
                         "và công ty nhỏ; công ty lớn cần giấy phép trả phí của Docker.",
                         None if docker else "docker"))
    return rows


def install_wsl(launch: Optional[Callable] = None) -> bool:
    """Chạy 'wsl --install --no-distribution' với quyền admin (Windows hỏi UAC). Cần khởi động lại máy sau đó."""
    if launch is None:
        if sys.platform != "win32":
            return False
        import ctypes

        def launch(exe, args):
            return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1) > 32
    return bool(launch("wsl.exe", "--install --no-distribution"))
