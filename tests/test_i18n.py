"""0.8.1: giao diện tiếng Anh, mã lỗi kỹ thuật cố định, nhật ký tiếng Anh, bản nháp guideline theo ngôn ngữ."""
import logging
import re
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from backend.i18n import EnglishLogFilter, error_code, set_lang, tr, translate_obj, translate_page, with_code
from conftest import ROOT

VI = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)


def _acorn_ok() -> bool:
    if not shutil.which("node"):
        return False
    return subprocess.run(["node", "-e", "require('acorn'); require('acorn-walk')"], capture_output=True).returncode == 0


def test_every_vietnamese_string_has_an_english_translation():
    """Thêm chữ tiếng Việt mới vào mã (backend, app, panel, profile) thì phải thêm bản dịch vào en.json."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import i18n_extract as ex

    found, skipped = ex.collect()
    cat = ex.load_catalog()["strings"]
    missing = sorted(k for k in found if k not in cat)
    assert not missing, f"{len(missing)} chuỗi chưa dịch, ví dụ: {missing[:5]} (python scripts/i18n_extract.py --missing)"
    if skipped:
        pytest.skip("thiếu node/acorn: chỉ kiểm phần Python và YAML")


@pytest.mark.skipif(not _acorn_ok(), reason="cần node + acorn")
def test_embedded_catalogs_are_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "i18n_build.py"), "--check"], capture_output=True, text=True,
                       cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_translation_engine():
    assert tr("Không tìm thấy ảnh.", "en") == "Image not found."
    assert tr("Không tìm thấy ảnh.", "vi") == "Không tìm thấy ảnh."
    assert tr("Còn 1200 MB VRAM, chưa đủ để nạp SAM 3 Tracker.", "en") == "Only 1200 MB of VRAM free, not enough to load SAM 3 Tracker."
    # mẫu lồng nhau: chỗ biến cũng được dịch
    assert tr("Không dùng được SAM 3 Tracker (thiếu VRAM cho SAM 3 Tracker), đã chuyển sang cách dựng hộp quanh điểm.", "en") \
        == "SAM 3 Tracker unavailable (not enough VRAM for SAM 3 Tracker); fell back to building a box around the points."
    # câu ghép, bảng Markdown, mã lỗi ở cuối giữ nguyên, chữ viết hoa đầu câu
    assert tr("Đã ghi 5 nhãn. Tải lại trang (F5) để thấy chúng trên canvas.", "en") == \
        "Saved 5 labels. Reload the page (F5) to see them on the canvas."
    assert tr("| Bài toán | Semantic segmentation (mỗi pixel một class) |", "en") == \
        "| Task | Semantic segmentation (one class per pixel) |"
    assert tr("Không tìm thấy ảnh. (E_NOT_FOUND)", "en") == "Image not found. (E_NOT_FOUND)"
    assert tr("Hông", "en") == "Hips"
    # dữ liệu người dùng không bị đụng tới
    assert tr("xe máy chở hàng", "en") == "xe máy chở hàng"
    assert translate_obj({"name": "Mắt", "detail": "Không tìm thấy."}, "en") == {"name": "Mắt", "detail": "Not found."}


def test_error_codes_are_language_independent():
    assert error_code("Không tìm thấy ảnh.", 404) == "E_NOT_FOUND"
    assert error_code("Image not found.", 404) == "E_NOT_FOUND"                    # mã theo HTTP status
    assert error_code("Còn 12 MB VRAM, chưa đủ để nạp SAM 3 Tracker.", 500) == "E_GPU_OOM"
    assert error_code("Tài khoản a đã gửi yêu cầu vào facebook/sam3 nhưng Meta chưa duyệt. Đợi email duyệt rồi thử lại.") \
        == "E_HF_PENDING"
    assert error_code("bất kỳ (E_HF_NOT_APPROVED)") == "E_HF_NOT_APPROVED"
    assert error_code("???", 422) == "E_VALIDATION"
    assert with_code("Lỗi", "E_X") == "Lỗi (E_X)" and with_code("Lỗi (E_X)", "E_X") == "Lỗi (E_X)"


def test_logs_are_always_english():
    rec = logging.LogRecord("sam3-assist", logging.INFO, __file__, 1, "Đã nhả model khỏi bộ nhớ%s.", (" (không dùng 15 phút)",), None)
    token = set_lang("vi")
    try:
        EnglishLogFilter().filter(rec)
    finally:
        from backend.i18n import _lang
        _lang.reset(token)
    assert rec.getMessage() == "Model released from memory (unused for 15 minutes)."
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "Label Studio: task %s lỗi: %s", ("7", "boom"), None)
    EnglishLogFilter().filter(rec)
    assert rec.getMessage() == "Label Studio: task 7 failed: boom"


def test_guideline_review_page_in_english():
    from backend.guideline.page import PAGE

    en = translate_page(PAGE, "en")
    assert not VI.search(en) and 'lang="en"' in en and "'X-Lang': 'en'" in en
    assert translate_page(PAGE, "vi") == PAGE


@pytest.fixture(scope="module")
def client():
    from backend.app import app

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_api_follows_x_lang_and_adds_codes(client):
    vi = client.post("/guideline/lessons/nope/status", json={"status": "confirmed"})
    en = client.post("/guideline/lessons/nope/status", json={"status": "confirmed"}, headers={"X-Lang": "en"})
    assert vi.status_code == en.status_code == 404
    assert VI.search(vi.json()["detail"]) and not VI.search(en.json()["detail"])
    assert vi.json()["code"] == en.json()["code"] == "E_NOT_FOUND"
    bad = client.post("/guideline/lessons/nope/status", json={"status": "?"}, headers={"X-Lang": "en"})
    assert bad.json()["code"].startswith("E_")
    page = client.get("/?lang=en").text
    assert "The backend is running" in page and not VI.search(page)
    assert "Backend đang chạy" in client.get("/").text
    # profile: tên và mô tả hiện trên panel cũng theo ngôn ngữ
    titles = [p.get("title", "") for p in client.get("/profiles", headers={"X-Lang": "en"}).json()["profiles"]]
    assert titles and not any(VI.search(t) for t in titles), titles


def test_english_draft_guideline(library):
    from backend.guideline.service import GuidelineService
    from test_guideline import run_survey

    token = set_lang("en")
    try:
        service = GuidelineService(library, None)
        run_id, view = run_survey(service)
    finally:
        from backend.i18n import _lang
        _lang.reset(token)
    md = view["markdown"]
    assert "## 1. Scope and data" in md and "## Appendix A. Survey figures" in md and "<!--r:bbox.tight-->" in md
    leftovers = [line for line in md.splitlines() if VI.search(line)]
    # chỉ còn tên người dùng tự đặt (tiêu đề "bộ thử" của bài test)
    assert all("bộ thử" in line for line in leftovers), leftovers[:5]
    # duyệt bản tiếng Anh không sinh bài học giả từ việc đổi ngôn ngữ
    res = service.approve(run_id, view["markdown"])
    assert res["learned"] == []


def test_changelog_has_an_english_twin_and_release_notes_split_by_language():
    from desktop import changelog

    vi = [e["version"] for e in changelog.parse((ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8"))]
    en = [e["version"] for e in changelog.parse((ROOT / "docs" / "CHANGELOG.en.md").read_text(encoding="utf-8"))]
    assert vi == en, "CHANGELOG.en.md phải có đúng các bản như CHANGELOG.md"
    for e in changelog.parse((ROOT / "docs" / "CHANGELOG.en.md").read_text(encoding="utf-8")):
        assert not VI.search(e["body"]), e["version"]
    notes = "- Sửa lỗi\n\nSHA-256 của bộ cài: `ab`\n\n<!-- en -->\n- Bug fix"
    assert changelog.pick_lang(notes, "en") == "- Bug fix"
    assert changelog.pick_lang(notes, "vi").startswith("- Sửa lỗi")
    assert changelog.pick_lang("- cũ", "en") == "- cũ"


def test_installer_offers_both_languages_and_passes_choice_to_app():
    iss = (ROOT / "installer" / "sam3-assist.iss").read_text(encoding="utf-8")
    assert 'Name: "vi"' in iss and 'Name: "en"' in iss and "--lang={language}" in iss
    msgs = iss.split("[Messages]")[1].split("[CustomMessages]")[0]
    for line in msgs.splitlines():
        if "=" in line and not line.startswith(";"):
            assert line.startswith(("vi.", "en.")), line          # tiếng Việt không đè lên bản tiếng Anh
    custom = iss.split("[CustomMessages]")[1].split("[Tasks]")[0]
    vi_keys = {l.split("=")[0][3:] for l in custom.splitlines() if l.startswith("vi.")}
    en_keys = {l.split("=")[0][3:] for l in custom.splitlines() if l.startswith("en.")}
    assert vi_keys == en_keys
    code = iss.split("[Code]")[1]
    assert not VI.search(code.split("procedure CurUninstallStepChanged")[0].replace("// cập nhật từ trong app: máy đã chạy được bản trước", ""))
    assert '"--lang=en"' in (ROOT / "installer" / "Launcher.cs").read_text(encoding="utf-8")
