"""0.8.3: logo mới (chữ S nét polyline), cửa sổ cài đặt theo bản mẫu, icon và tên app riêng trên taskbar."""
import re
import time
import types

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import ROOT


@pytest.fixture(scope="module")
def client_mock():
    from backend.app import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_icons_have_every_size_windows_needs():
    ico = Image.open(ROOT / "installer" / "icon.ico")
    sizes = ico.info["sizes"]
    for s in (16, 24, 32, 48, 256):
        assert (s, s) in sizes, s
    assert (ROOT / "desktop" / "ui" / "icon.ico").read_bytes() == (ROOT / "installer" / "icon.ico").read_bytes()
    assert Image.open(ROOT / "desktop" / "ui" / "icon.png").size == (256, 256)
    for name, size in (("wizard.bmp", (164, 314)), ("wizard-2x.bmp", (328, 628)), ("wizard-small.bmp", (55, 58)),
                       ("wizard-small-2x.bmp", (110, 116))):
        assert Image.open(ROOT / "installer" / name).size == size, name


def test_same_app_id_for_windows_and_shortcuts():
    from desktop.app import APP_ID, READY_EVENT

    iss = (ROOT / "installer" / "sam3-assist.iss").read_text(encoding="utf-8")
    assert iss.count(f'AppUserModelID: "{APP_ID}"') == 2, "lối tắt Start và Desktop"
    assert "WizardImageFile=wizard.bmp,wizard-2x.bmp" in iss
    launcher = (ROOT / "installer" / "Launcher.cs").read_text(encoding="utf-8")
    assert READY_EVENT.replace("\\\\", "\\") in launcher, "màn hình chờ đợi đúng event app báo"
    wf = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
    assert "/r:System.Drawing.dll" in wf


def test_logo_is_the_same_everywhere():
    logo = re.sub(r"\s+", " ", (ROOT / "desktop" / "ui" / "logo.svg").read_text(encoding="utf-8"))
    path = re.search(r'd="([^"]+)"', logo).group(1)
    for f in ("desktop/ui/control.html", "userscript/sam3-cvat-assist.user.js", "backend/app.py", "backend/guideline/page.py"):
        assert path in (ROOT / f).read_text(encoding="utf-8"), f


def test_favicon(client_mock):
    r = client_mock.get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and b"<svg" in r.content


def test_install_progress_per_component_speed_and_eta():
    from desktop.control import ControlApi, Installer

    inst = Installer(comps=None)
    inst.p.update(running=True, finished=False, order=["torch", "sam3", "vitpose"],
                  items={"torch": "done", "sam3": "active", "vitpose": "skip"})
    inst._progress(done=0, total=1000 * 2**20)
    t0 = time.time()
    inst._last = (t0 - 2, 0)
    inst._progress(done=40 * 2**20)                       # 40 MB trong 2 giây
    assert 15 * 2**20 < inst.p["rate"] < 25 * 2**20
    fake = types.SimpleNamespace(_installer=inst, _settings=types.SimpleNamespace(data={}), _setup_mode=True)
    p = ControlApi.install_progress.__wrapped__(fake)
    assert p["titles"]["sam3"] and p["items"]["vitpose"] == "skip"
    assert 66 <= p["overall"] <= 70, p["overall"]         # 2/3 xong + 4% của thành phần đang tải
    assert p["rate_h"].endswith("/s") and "phút" in p["eta_h"]
    inst.p.update(running=False, finished=True, error="")
    assert ControlApi.install_progress.__wrapped__(fake)["overall"] == 100


def test_report_includes_user_note_with_secrets_hidden(tmp_path):
    import zipfile
    from pathlib import Path

    from desktop import support
    from desktop.paths import Paths

    paths = Paths(tmp_path / "data", app_dir=Path(ROOT)).ensure()
    out = support.report_bundle(paths, {}, out_dir=tmp_path, note="Bật backend thì tự tắt, token hf_abcdefghijklmnop")
    with zipfile.ZipFile(out) as z:
        text = z.read("description.txt").decode("utf-8")
    assert "tự tắt" in text and "hf_abcdefghijklmnop" not in text
    out2 = support.report_bundle(paths, {}, out_dir=tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path)
    with zipfile.ZipFile(out2) as z:
        assert "description.txt" not in z.namelist(), "không viết gì thì không có file mô tả"


def test_backend_exit_codes_are_explained():
    from backend.i18n import tr
    from desktop.backend_proc import describe_exit

    assert "STATUS_DLL_NOT_FOUND" in describe_exit(-1073741515)          # Windows trả số âm
    assert "0xC0000005" in describe_exit(3221225477)
    assert describe_exit(1) == "mã 1 (0x00000001)" and describe_exit(None) == ""
    assert tr(describe_exit(-1073741515), "en").startswith("code -1073741515 (0xC0000135, STATUS_DLL_NOT_FOUND: a DLL is missing")
