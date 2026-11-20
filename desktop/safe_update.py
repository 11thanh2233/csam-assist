"""Cập nhật an toàn (0.9.1).

- Trước khi cài bản mới: tự sao lưu profile, guideline, dữ liệu tự học và cài đặt (giữ 3 bản gần nhất).
- Script cập nhật giữ lại bản đang chạy (đổi tên thư mục app/runtime thành *.prev), chạy bộ cài, rồi đợi bản mới
  báo "đã mở được" (file boot-ok). Bộ cài lỗi hoặc quá 120 giây không thấy boot-ok: trả bản cũ về chỗ, mở lại app,
  và để lại update-rolled-back.json để app báo cho người dùng.
- Dữ liệu của bạn (model, profile, tự học) nằm ở thư mục dữ liệu, không bị script đụng tới.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("sam3-desktop")

BOOT_OK = "boot-ok"
ROLLED_BACK = "update-rolled-back.json"
BOOT_WAIT_S = 120
KEEP_BACKUPS = 3
LAUNCHER = "SAM3 Assist.exe"


def backups_dir(data: Path) -> Path:
    return Path(data) / "backups"


def auto_backup(paths, from_version: str, to_version: str) -> Path:
    """Sao lưu trước khi cập nhật. Lỗi thì ném ra để không cập nhật khi chưa sao lưu được."""
    from . import support

    folder = backups_dir(paths.data)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"truoc-cap-nhat-{from_version}-len-{to_version}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    support.backup(paths, dest)
    olds = sorted(folder.glob("truoc-cap-nhat-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in olds[KEEP_BACKUPS:]:
        old.unlink(missing_ok=True)
    return dest


def mark_boot_ok(data: Path, version: str) -> None:
    """App đã mở được: script cập nhật đang đợi file này để biết bản mới chạy được."""
    try:
        (Path(data) / BOOT_OK).write_text(version, encoding="utf-8")
    except OSError:
        log.exception("Cannot write boot-ok marker")


def take_rollback_note(data: Path) -> Optional[dict]:
    """Lần cập nhật trước phải quay về bản cũ: đọc ghi chú (một lần) để báo người dùng."""
    f = Path(data) / ROLLED_BACK
    if not f.exists():
        return None
    try:
        note = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        note = {}
    f.unlink(missing_ok=True)
    return note


def update_script(setup: Path, app_dir: Path, data_dir: Path, from_version: str, to_version: str) -> str:
    """Nội dung run-update.cmd (Windows). Tách ra để test được trên mọi máy."""
    app, data = str(app_dir).rstrip("\\/"), str(data_dir).rstrip("\\/")
    args = "/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /update=1"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal",
        f'set "APP={app}"',
        f'set "DATA={data}"',
        'set "LOG=%DATA%\\logs\\update.log"',
        f'echo [%date% %time%] update {from_version} -^> {to_version}>> "%LOG%"',
        "timeout /t 4 /nobreak >nul",
        "rem 1. Keep the running version so we can roll back if the new one does not start",
        'if exist "%APP%\\app.prev" rmdir /s /q "%APP%\\app.prev"',
        'if exist "%APP%\\runtime.prev" rmdir /s /q "%APP%\\runtime.prev"',
        'move "%APP%\\app" "%APP%\\app.prev" >nul 2>&1 || goto plain',
        'move "%APP%\\runtime" "%APP%\\runtime.prev" >nul 2>&1 || (move "%APP%\\app.prev" "%APP%\\app" >nul & goto plain)',
        f'copy /y "%APP%\\{LAUNCHER}" "%APP%\\launcher.prev" >nul',
        f'del "%DATA%\\{BOOT_OK}" >nul 2>&1',
        "rem 2. Install the new version (the installer reopens the app when done)",
        f'start "" /wait "{setup}" {args}',
        'set "RC=%errorlevel%"',
        'echo installer exit code %RC%>> "%LOG%"',
        'if not "%RC%"=="0" goto rollback',
        f"rem 3. Wait for the new version to report it started (up to {BOOT_WAIT_S} seconds)",
        f"for /l %%i in (1,1,{BOOT_WAIT_S}) do (",
        f'  if exist "%DATA%\\{BOOT_OK}" goto ok',
        "  timeout /t 1 /nobreak >nul",
        ")",
        ":rollback",
        'echo new version did not start, rolling back>> "%LOG%"',
        "powershell -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '%APP%\\*' } "
        "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }\"",
        "timeout /t 2 /nobreak >nul",
        'if exist "%APP%\\app" rmdir /s /q "%APP%\\app"',
        'if exist "%APP%\\runtime" rmdir /s /q "%APP%\\runtime"',
        'move "%APP%\\app.prev" "%APP%\\app" >nul',
        'move "%APP%\\runtime.prev" "%APP%\\runtime" >nul',
        f'copy /y "%APP%\\launcher.prev" "%APP%\\{LAUNCHER}" >nul',
        'if not defined RC set "RC=0"',
        f'> "%DATA%\\{ROLLED_BACK}" echo {{"from": "{from_version}", "to": "{to_version}", "installer_exit": %RC%}}',
        f'start "" "%APP%\\{LAUNCHER}"',
        "goto end",
        ":ok",
        'echo new version started>> "%LOG%"',
        'rmdir /s /q "%APP%\\app.prev" "%APP%\\runtime.prev" >nul 2>&1',
        'del "%APP%\\launcher.prev" >nul 2>&1',
        "goto end",
        ":plain",
        'echo cannot keep the current version (files in use), updating without rollback>> "%LOG%"',
        f'start "" "{setup}" {args}',
        ":end",
        "endlocal",
    ]
    return "\r\n".join(lines) + "\r\n"
