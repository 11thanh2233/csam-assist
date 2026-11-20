"""Kiểm thử bản 0.9.2: chép / bôi đen trong app, Release một file exe, panel mới."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_windows_allow_text_selection_and_runs_are_marked():
    app = (ROOT / "desktop" / "app.py").read_text(encoding="utf-8")
    assert app.count("text_select=True") == 2, "bảng điều khiển và cửa sổ CVAT đều bôi đen được"
    assert 'log.info("===== %s app %s starting =====' in app
    html = (ROOT / "desktop" / "ui" / "control.html").read_text(encoding="utf-8")
    assert "ok = await api().copy_text(text)" in html, "chép qua app trước"


def test_copy_text_is_safe_off_windows():
    from desktop import control

    api = control.ControlApi.__new__(control.ControlApi)
    assert control.ControlApi.copy_text.__wrapped__(api, "x") in (True, False)


def test_release_has_one_installer():
    ci = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
    create = ci[ci.index("gh release create"):]
    create = create[:create.index("\n", create.index("--notes-file"))] if "--notes-file" in create else create[:400]
    assert "$legacy" not in ci and "SAM3-CVAT-Assist-" not in create, "Release chỉ có một file exe"
