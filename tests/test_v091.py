"""Kiểm thử bản 0.9.1: tự cài Visual C++, kiểm tra nhanh sau khi cài, an toàn khi cập nhật, trang Giới thiệu,
thống kê công việc, báo lệch bản."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "installer" / "sam3-assist.iss").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")


def test_installer_bundles_and_installs_vcredist():
    """Máy thiếu Microsoft Visual C++: bộ cài tự cài (xin quyền admin), không chỉ báo lỗi."""
    assert 'Source: "{#BuildDir}\\vc_redist.x64.exe"; Flags: dontcopy' in ISS
    assert "ExtractTemporaryFile('vc_redist.x64.exe')" in ISS
    assert "ShellExec('runas', exe, '/install /quiet /norestart'" in ISS
    assert re.search(r"if \(CurStep = ssInstall\) and not IsUpdate\(\) then\s+(begin\s+)?InstallVcRedist\(\);", ISS)
    # không còn báo "thiếu" ngay khi mở bộ cài: chỉ báo khi cài không được
    init = ISS[ISS.index("function InitializeSetup"):ISS.index("procedure InstallVcRedist")]
    assert "NoVcRedist" not in init
    for lang in ("vi", "en"):
        assert f"{lang}.InstallingVcRedist=" in ISS and f"{lang}.NoVcRedist=" in ISS
    # CI tải từ Microsoft và từ chối file không có chữ ký số hợp lệ của Microsoft
    i = CI.index("vc_redist.x64.exe -OutFile build\\vc_redist.x64.exe")
    assert i < CI.index("& $iscc"), "tải trước khi đóng gói"
    assert "Get-AuthenticodeSignature build\\vc_redist.x64.exe" in CI and "O=Microsoft Corporation" in CI


# ------------------------------------------------------------ cập nhật an toàn ---
def test_update_script_keeps_old_version_and_rolls_back():
    from desktop.safe_update import BOOT_OK, ROLLED_BACK, update_script

    s = update_script(Path(r"C:\Users\An\AppData\Local\SAM3CvatAssist\updates\SAM3-Label-Assist-Setup-1.0.0.exe"),
                      Path(r"C:\Users\An\AppData\Local\Programs\SAM3 Label Assist"),
                      Path(r"C:\Users\An\AppData\Local\SAM3CvatAssist"), "0.9.1", "1.0.0")
    lines = s.split("\r\n")
    order = [next(i for i, l in enumerate(lines) if needle in l) for needle in (
        'move "%APP%\\app" "%APP%\\app.prev"', 'start "" /wait', f'if exist "%DATA%\\{BOOT_OK}" goto ok',
        ':rollback', 'move "%APP%\\runtime.prev" "%APP%\\runtime"', ROLLED_BACK, ':ok', ':plain')]
    assert order == sorted(order), "thứ tự: giữ bản cũ -> cài -> đợi app mở -> quay về nếu không mở"
    assert 'set "APP=C:\\Users\\An\\AppData\\Local\\Programs\\SAM3 Label Assist"' in s, "đường dẫn có dấu cách được bọc ngoặc"
    assert 'if not "%RC%"=="0" goto rollback' in s, "bộ cài lỗi cũng quay về"
    assert '"from": "0.9.1", "to": "1.0.0"' in s
    assert s.count("/update=1") == 2 and s.endswith("\r\n")


def test_rollback_note_is_read_once_and_skips_that_version(tmp_path):
    from desktop import safe_update

    (tmp_path / safe_update.ROLLED_BACK).write_text('{"from": "0.9.1", "to": "1.0.0", "installer_exit": 0}\r\n', encoding="utf-8")
    assert safe_update.take_rollback_note(tmp_path)["to"] == "1.0.0"
    assert safe_update.take_rollback_note(tmp_path) is None, "chỉ báo một lần"
    safe_update.mark_boot_ok(tmp_path, "0.9.1")
    assert (tmp_path / safe_update.BOOT_OK).read_text() == "0.9.1"


def test_auto_backup_before_update_keeps_last_three(tmp_path):
    import zipfile

    from desktop import safe_update
    from desktop.paths import Paths

    paths = Paths(data_dir=tmp_path / "data", app_dir=ROOT).ensure()
    (paths.profiles / "mine.yaml").write_text("id: mine\n", encoding="utf-8")
    made = []
    for i in range(5):
        made.append(safe_update.auto_backup(paths, "0.9.1", f"1.0.{i}"))
        import os
        os.utime(made[-1], (1_000_000 + i, 1_000_000 + i))
    left = sorted(p.name for p in safe_update.backups_dir(paths.data).glob("*.zip"))
    assert len(left) == 3 and all("1.0.2" in n or "1.0.3" in n or "1.0.4" in n for n in left)
    with zipfile.ZipFile(made[-1]) as z:
        assert "profiles/mine.yaml" in z.namelist() and "backup.json" in z.namelist()


def test_install_update_backs_up_first_and_refuses_without_backup(tmp_path, monkeypatch):
    from desktop import control, safe_update

    calls = []
    api = control.ControlApi.__new__(control.ControlApi)
    api._paths = type("P", (), {"app": tmp_path / "Programs" / "SAM3 Label Assist" / "app", "data": tmp_path / "data"})()
    ready = tmp_path / "SAM3-Label-Assist-Setup-1.0.0.exe"
    ready.write_bytes(b"MZ")
    api._updater = type("U", (), {"p": {"ready": str(ready), "version": "1.0.0"}, "launch": lambda self, *a: calls.append(("launch",) + a)})()
    api._installer = type("I", (), {"p": {"running": False}})()
    api._backend = type("B", (), {"stop": lambda self: calls.append(("stop",))})()
    api._on_quit = lambda: calls.append(("quit",))
    monkeypatch.setattr(safe_update, "auto_backup", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    r = control.ControlApi.install_update.__wrapped__(api)
    assert not r["ok"] and "Chưa cập nhật" in r["message"] and calls == []
    monkeypatch.setattr(safe_update, "auto_backup", lambda *a: calls.append(("backup",) + a[1:]) or tmp_path / "b.zip")
    r = control.ControlApi.install_update.__wrapped__(api)
    assert r["ok"] and [c[0] for c in calls] == ["backup", "stop", "launch", "quit"]
    assert calls[0][1:] == (control.__version__, "1.0.0")
    assert calls[2][2] == tmp_path / "Programs" / "SAM3 Label Assist", "thư mục cài (cha của app)"


# ---------------------------------------------------------- đọc cài đặt cũ ---
OLD_SETTINGS = {
    "0.6.0": {"sites": [{"name": "CVAT edufun", "url": "https://cvat.edufun.ai"}], "autostart_backend": True, "license_accepted": "x"},
    "0.8.2": {"sites": [{"name": "CVAT edufun", "url": "https://cvat.edufun.ai"}], "lang": "en", "start_mode": "control",
              "idle_unload_min": 30, "recent_jobs": [{"job": 1, "task": 2, "site": "s", "url": "u", "at": "2026-11-01 10:00"}],
              "last_seen_version": "0.8.2", "skip_version": "0.8.3"},
    "0.9.0": {"sites": [{"name": "A", "url": "https://a.example", "platform": "cvat"}], "theme": "dark", "auto_restart_backend": False,
              "last_seen_version": "0.9.0"},
}


def test_settings_from_every_older_version_still_load(tmp_path):
    from desktop.control import SETTINGS_SCHEMA, Settings
    import json

    for ver, data in OLD_SETTINGS.items():
        f = tmp_path / f"{ver}.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        s = Settings(f)
        for k, v in data.items():
            if k != "sites":
                assert s.data[k] == v, (ver, k)            # không đổi nghĩa / không mất mục cũ
        assert [x["url"] for x in s.data["sites"]] == [x["url"] for x in data["sites"]]
        assert all(x["platform"] == "cvat" for x in s.data["sites"])
        assert s.data["schema"] == SETTINGS_SCHEMA and s.data["lang"] in ("vi", "en")
        s.save()
        assert Settings(f).data["sites"] == s.data["sites"]


def test_settings_from_newer_version_or_broken_file_lose_nothing(tmp_path):
    from desktop.control import Settings
    import json

    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"schema": 7, "future_key": {"a": 1}, "lang": "en", "sites": "oops"}), encoding="utf-8")
    s = Settings(f)
    assert s.data["schema"] == 7 and s.data["future_key"] == {"a": 1}, "giữ mục của bản mới hơn (sau khi quay về bản cũ)"
    assert isinstance(s.data["sites"], list) and s.data["sites"], "sai kiểu thì chỉ đặt lại mục đó"
    f.write_text('{"lang": "en", "sites": [', encoding="utf-8")       # file hỏng
    s = Settings(f)
    assert s.data["lang"] == "vi"
    kept = list(tmp_path.glob("settings.broken-*.json"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8").startswith('{"lang": "en"'), "giữ file hỏng, không ghi đè mất"


# ------------------------------------------------------------ giới thiệu ---
def test_about_lists_licenses_and_only_opens_listed_https_pages(tmp_path, monkeypatch):
    import json
    import webbrowser

    from desktop import control

    data = json.loads((ROOT / "desktop" / "licenses" / "third_party.json").read_text(encoding="utf-8"))
    names = " ".join(x["name"] for x in data["models"] + data["software"])
    for must in ("SAM 3", "ViTPose", "MediaPipe", "PyTorch", "Python", "Transformers", "pystray", "Visual C++"):
        assert must in names, must
    assert next(x for x in data["models"] if x["name"].startswith("SAM 3"))["license"] == "SAM License"
    assert all(x["url"].startswith("https://") and x["license"] for x in data["models"] + data["software"])
    api = control.ControlApi.__new__(control.ControlApi)
    api._paths = type("P", (), {"app": ROOT})()
    about = control.ControlApi.about.__wrapped__(api)
    assert about["version"] == control.__version__ and about["tool"]["name"] == "SAM3 Label Assist"
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    assert control.ControlApi.open_url_fixed.__wrapped__(api, "m", 0) and opened == [data["models"][0]["url"]]
    assert not control.ControlApi.open_url_fixed.__wrapped__(api, "s", 999)
    assert not control.ControlApi.open_url_fixed.__wrapped__(api, "m", "x")
    build = (ROOT / "installer" / "build.py").read_text(encoding="utf-8")
    assert '"LICENSE"' in build, "file giấy phép của tool đi theo bộ cài"


# ---------------------------------------------------------- thống kê công việc ---
def test_work_stats_summary_and_csv(tmp_path):
    import datetime as dt
    import hashlib
    import json

    from desktop import workstats

    today = dt.date(2026, 11, 20)
    stats = {"2026-11-20": {"applied": 126, "rejected": 9, "shown": 140, "frames": ["30:1", "30:2"], "saved": 2880,
                            "jobs": {"30": {"applied": 100, "rejected": 9, "shown": 110}, "31": {"applied": 26}}},
             "2026-11-18": {"applied": 60, "shown": 0, "frames": [], "saved": 600},
             "2026-10-01": {"applied": 5, "frames": [], "saved": 0}, "junk": "x"}
    assert workstats.load(tmp_path) == {}, "chưa có dữ liệu"
    (tmp_path / (hashlib.sha1(b"stats").hexdigest() + ".json")).write_text(json.dumps({"key": "stats", "value": stats}))
    loaded = workstats.load(tmp_path)
    s = workstats.summary(loaded, today=today)
    assert s["today"] == {"applied": 126, "frames": 2, "minutes": 48, "kept": 90, "jobs": 2}
    assert [w["day"] for w in s["week"]][0] == "2026-11-14" and s["week"][-1]["day"] == "2026-11-20"
    assert s["week_applied"] == 186 and s["week_minutes"] == 58 and s["has_data"]
    assert workstats.summary({}, today=today)["today"]["kept"] is None
    csv = workstats.csv_text(loaded)
    assert csv.startswith("﻿\"ngay\",\"job\"") and '"2026-11-20","30","100","9","110","2","48"' in csv
    assert '"2026-11-18","","60"' in csv and "junk" not in csv, "bỏ mục hỏng"


def test_panel_counts_days_in_local_time():
    us = (ROOT / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
    stats = us[us.index("function statsAll()"):us.index("function downloadText")]
    assert "toISOString" not in stats, "ngày theo giờ máy, không theo UTC"
    assert "localDay()" in stats


# ------------------------------------------------------------ hướng dẫn ---
def test_user_guide_images_and_links_exist():
    guide = (ROOT / "docs" / "HUONG_DAN.md").read_text(encoding="utf-8")
    images = re.findall(r"\]\((images/[^)]+)\)", guide)
    assert len(images) >= 8
    for img in images:
        f = ROOT / "docs" / img
        assert f.exists() and f.stat().st_size < 400_000, img       # ảnh gọn cho GitHub
    for link in re.findall(r"\]\(([A-Z_]+\.md)\)", guide):
        assert (ROOT / "docs" / link).exists(), link
    # phím tắt trong hướng dẫn khớp phím mặc định của panel
    us = (ROOT / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
    keys = re.search(r"const DEFAULT_KEYS = (\{[^}]+\})", us).group(1)
    for action, key in re.findall(r"(\w+): '([^']+)'", keys):
        name = "Alt+" + (key.upper() if len(key) == 1 else key.capitalize())
        assert f"`{name}`" in guide, f"hướng dẫn thiếu phím {name} ({action})"
    privacy = (ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    assert "## English" in privacy and "telemetry" in privacy
