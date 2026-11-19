"""Bản desktop: tải file an toàn (tải tiếp, mã băm, token), cài thành phần, cầu nối trang CVAT, dữ liệu người dùng."""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import threading
import zipfile
from pathlib import Path

import pytest

from desktop import downloader as D
from desktop.bridge import Bridge, Store, normalize_site, origin_of
from desktop.components import Components, Gpu, seed_user_files
from desktop.control import ControlApi
from desktop.paths import Paths

TOKEN = "hf_good"
PENDING = "hf_pending"


class Fake:
    """Máy chủ giả: file thường, Hugging Face giả, chuyển hướng sang 'máy khác' (localhost) như CDN."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.drop_once: set[str] = set()      # lần đầu chỉ gửi một nửa rồi ngắt
        self.seen_auth: dict[str, str] = {}
        self.ranges: list[str] = []
        self.port = 0

    def serve(self):
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _auth(self):
                return (self.headers.get("Authorization") or "").removeprefix("Bearer ")

            def do_HEAD(self):
                self.do_GET(head=True)

            def do_GET(self, head=False):
                path = self.path.split("?")[0]
                fake.seen_auth[path] = self._auth()
                host = self.headers.get("Host", "").split(":")[0]
                if path == "/api/whoami-v2":
                    return self._json({"name": "an"} if self._auth() in (TOKEN, PENDING) else {"error": "x"},
                                      200 if self._auth() in (TOKEN, PENDING) else 401)
                if path.startswith("/api/models/"):
                    repo = path[len("/api/models/"):].split("/revision/")[0]
                    return self._json(fake.hf_info(repo))
                if "/resolve/" in path:
                    repo, rest = path.lstrip("/").split("/resolve/")
                    if repo == "facebook/sam3" and self._auth() != TOKEN:
                        msg = "Your request is awaiting a review" if self._auth() == PENDING else "Access restricted"
                        self.send_response(403)
                        self.send_header("X-Error-Message", msg)
                        self.end_headers()
                        return
                    fname = rest.split("/", 1)[1]
                    data = fake.files[f"{repo}/{fname}"]
                    if fname.endswith(".safetensors"):        # file lớn: chuyển sang "CDN" (máy khác)
                        self.send_response(302)
                        self.send_header("Location", f"http://localhost:{fake.port}/cdn/{repo}/{fname}")
                        self.end_headers()
                        return
                    return self._bytes(path, data, head)
                if path.startswith("/cdn/"):
                    assert host == "localhost"
                    return self._bytes(path, fake.files[path[len("/cdn/"):]], head)
                if path.startswith("/files/"):
                    return self._bytes(path, fake.files[path[len("/files/"):]], head)
                self.send_response(404)
                self.end_headers()

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _bytes(self, path, data, head):
                start = 0
                rng = self.headers.get("Range")
                if rng:
                    fake.ranges.append(rng)
                    start = int(rng.split("=")[1].split("-")[0])
                self.send_response(206 if rng else 200)
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                if head:
                    return
                chunk = data[start:]
                if path in fake.drop_once:
                    fake.drop_once.discard(path)
                    self.wfile.write(chunk[: len(chunk) // 2])
                    self.wfile.flush()
                    self.connection.close()
                    return
                self.wfile.write(chunk)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.srv = srv
        return self

    def hf_info(self, repo):
        sib = []
        for key, data in self.files.items():
            if not key.startswith(repo + "/"):
                continue
            name = key[len(repo) + 1:]
            if name.endswith((".safetensors", ".pt")):
                sib.append({"rfilename": name, "size": len(data), "lfs": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}})
            else:
                sib.append({"rfilename": name, "size": len(data),
                            "blobId": hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()})
        return {"sha": "abc123commit", "siblings": sib}

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture()
def fake(monkeypatch):
    f = Fake().serve()
    monkeypatch.setattr(D, "TOKEN_HOSTS", {"127.0.0.1"})      # 127.0.0.1 đóng vai huggingface.co, localhost là CDN
    yield f
    f.srv.shutdown()


HTTP = ("127.0.0.1", "localhost")


def test_download_resumes_and_checks_hash(fake, tmp_path):
    data = bytes(range(256)) * 4000
    fake.files["big.bin"] = data
    fake.drop_once.add("/files/big.bin")
    seen = []
    dest = D.download(f"{fake.base}/files/big.bin", tmp_path / "big.bin", sha256=hashlib.sha256(data).hexdigest(),
                      size=len(data), allow_http=HTTP, backoff=0, progress=lambda d, t: seen.append((d, t)))
    assert dest.read_bytes() == data and not (tmp_path / "big.bin.part").exists()
    assert fake.ranges and fake.ranges[0].startswith("bytes="), "rớt mạng giữa chừng thì tải tiếp, không tải lại từ đầu"
    assert seen[-1] == (len(data), len(data))

    # Sai mã băm: báo lỗi, không để lại file
    fake.files["bad.bin"] = b"hello"
    with pytest.raises(D.DownloadError, match="mã băm"):
        D.download(f"{fake.base}/files/bad.bin", tmp_path / "bad.bin", sha256="0" * 64, allow_http=HTTP)
    assert not (tmp_path / "bad.bin").exists() and not (tmp_path / "bad.bin.part").exists()
    # Không có mã băm thì không tải; http ra ngoài thì từ chối
    with pytest.raises(D.DownloadError, match="mã băm"):
        D.download(f"{fake.base}/files/bad.bin", tmp_path / "x.bin", allow_http=HTTP)
    with pytest.raises(D.DownloadError, match="HTTPS"):
        D.download("http://example.com/x", tmp_path / "y.bin", sha256="0" * 64)


def make_repo(fake):
    fake.files.update({
        "facebook/sam3/config.json": b'{"model_type": "sam3"}',
        "facebook/sam3/model.safetensors": b"W" * 50_000,
        "facebook/sam3/sam3.pt": b"P" * 60_000,                     # checkpoint trùng định dạng khác: bỏ
        "facebook/sam3/tokenizer.json": b"{}",
        "facebook/sam3/assets/demo.png": b"img",                    # thư mục con: bỏ
        "facebook/sam3/README.md": b"# readme",
        "facebook/sam3/LICENSE": b"SAM License",
    })


def manifest(fake, wheel: bytes = b"", wheel_name: str = "torch-0.0.1-py3-none-any.whl"):
    return {
        "torch": {"cuda": [{"name": "torch", "version": "0.0.1", "filename": wheel_name, "url": f"{fake.base}/files/{wheel_name}",
                            "sha256": hashlib.sha256(wheel).hexdigest(), "size": len(wheel)}], "cpu": []},
        "models": {"sam3": {"repo": "facebook/sam3", "gated": True, "approx_size": 3_400_000_000},
                   "vitpose": {"repo": "usyd-community/vitpose-base-simple", "approx_size": 360_000_000},
                   "face": {"url": f"{fake.base}/files/face.task", "sha256": hashlib.sha256(b"FACE").hexdigest(), "size": 4}},
    }


def test_hf_access_messages(fake):
    make_repo(fake)
    assert D.hf_check_access("facebook/sam3", "nope", base=fake.base, allow_http=HTTP)["code"] == "token_invalid"
    pend = D.hf_check_access("facebook/sam3", PENDING, base=fake.base, allow_http=HTTP)
    assert pend["code"] == "pending" and "chưa duyệt" in pend["message"]
    ok = D.hf_check_access("facebook/sam3", TOKEN, base=fake.base, allow_http=HTTP)
    assert ok["ok"] and ok["user"] == "an"


def test_install_sam3_only_needed_files_token_not_sent_to_cdn(fake, tmp_path):
    make_repo(fake)
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    comps = Components(paths, manifest(fake), allow_http=HTTP, hf_base=fake.base)
    with pytest.raises(D.DownloadError, match="chưa duyệt"):
        comps.install("sam3", gpu=Gpu(), token=PENDING)
    fake.drop_once.add("/cdn/facebook/sam3/model.safetensors")
    info = comps.install("sam3", gpu=Gpu(), token=TOKEN)
    assert sorted(info["files"]) == ["LICENSE", "config.json", "model.safetensors", "tokenizer.json"]
    assert info["commit"] == "abc123commit"
    folder = paths.sam3
    assert (folder / "model.safetensors").stat().st_size == 50_000 and (folder / "SAM_LICENSE.txt").exists()
    assert not (folder / "sam3.pt").exists() and not folder.with_name("sam3.download").exists()
    assert fake.seen_auth["/facebook/sam3/resolve/abc123commit/config.json"] == TOKEN
    assert fake.seen_auth["/cdn/facebook/sam3/model.safetensors"] == "", "token không được gửi sang máy chủ khác khi bị chuyển hướng"
    assert comps.installed("sam3") and not comps.status()["ready"]


def fake_wheel() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("torch/__init__.py", "__version__ = '0.0.1'\n")
        z.writestr("torch-0.0.1.dist-info/METADATA", "Metadata-Version: 2.1\nName: torch\nVersion: 0.0.1\n")
        z.writestr("torch-0.0.1.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        z.writestr("torch-0.0.1.dist-info/RECORD", "")
    return buf.getvalue()


def test_install_torch_face_and_backend_env(fake, tmp_path):
    wheel = fake_wheel()
    fake.files["torch-0.0.1-py3-none-any.whl"] = wheel
    fake.files["face.task"] = b"FACE"
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    comps = Components(paths, manifest(fake, wheel), allow_http=HTTP, hf_base=fake.base)
    est = comps.estimate(["torch", "face"], Gpu("RTX 2060", 6144, "560.1"))
    assert est["download"] == len(wheel) + 4 and est["enough"]
    info = comps.install("torch", gpu=Gpu("RTX 2060", 6144, "560.1"))
    assert info["variant"] == "cuda" and (paths.packages / "torch" / "__init__.py").exists()
    assert not list(paths.downloads.glob("*.whl")), "xoá file tải về sau khi cài để đỡ tốn ổ đĩa"
    comps.install("face", gpu=Gpu())
    assert paths.face.read_bytes() == b"FACE" and comps.installed("face")
    env = comps.backend_env()
    assert env["HF_HUB_OFFLINE"] == "1" and env["SAM3_NO_AUTO_DOWNLOAD"] == "1" and env["SAM3_HF_MODEL"] == str(paths.sam3)
    comps.remove("torch")
    assert not comps.installed("torch")


def test_seed_keeps_user_edits(tmp_path):
    app = tmp_path / "app"
    (app / "profiles").mkdir(parents=True)
    (app / "guideline_library" / "learning").mkdir(parents=True)
    (app / "profiles" / "a.yaml").write_text("v1")
    (app / "profiles" / "b.yaml").write_text("v1")
    (app / "guideline_library" / "knowledge.yaml").write_text("k1")
    (app / "guideline_library" / "learning" / "model.json").write_text("{}")
    paths = Paths(tmp_path / "data", app_dir=app).ensure()
    assert seed_user_files(paths)["copied"] == 3
    assert not (paths.library / "learning" / "model.json").exists(), "không chép dữ liệu tự học của máy build"
    (paths.profiles / "a.yaml").write_text("của tôi", encoding="utf-8")             # người dùng sửa
    (app / "profiles" / "a.yaml").write_text("v2")                # bản cập nhật
    (app / "profiles" / "b.yaml").write_text("v2")
    res = seed_user_files(paths)
    assert (paths.profiles / "a.yaml").read_text(encoding="utf-8") == "của tôi" and (paths.profiles / "b.yaml").read_text() == "v2"
    assert res == {"copied": 1, "kept": 1}


class FakeBackend:
    base_url = "http://127.0.0.1:8770"

    def __init__(self, state="running"):
        self.state, self.calls = state, []

    def info(self):
        return {"state": self.state, "message": "Chưa có Trọng số SAM 3."}

    def request(self, method, path, body, timeout=300):
        self.calls.append((method, path, body))
        return 200, '{"ok": true}'


def test_bridge_only_allowed_pages_and_backend(tmp_path):
    url = {"v": "https://cvat.edufun.ai/tasks/1/jobs/2"}
    be = FakeBackend()
    br = Bridge(Store(tmp_path / "store"), be, lambda: {"https://cvat.edufun.ai"}, lambda: url["v"])
    r = br.gm_request({"method": "POST", "url": "http://127.0.0.1:8765/propose", "data": '{"a":1}'})
    assert r["status"] == 200 and be.calls == [("POST", "/propose", '{"a":1}')]
    assert br.gm_request({"method": "GET", "url": "https://evil.example/x"})["error"] == "denied"
    assert br.gm_request({"method": "GET", "url": "http://127.0.0.1:8765/../x"})["error"] == "denied"
    url["v"] = "https://evil.example/page"                         # link ngoài mở trong cửa sổ CVAT
    assert br.gm_request({"method": "GET", "url": "http://127.0.0.1:8765/health"})["error"] == "denied"
    with pytest.raises(PermissionError):
        br.gm_set("cfg", {})
    url["v"] = "https://cvat.edufun.ai/"
    br.gm_set("cfg", {"backendUrl": "http://127.0.0.1:8765", "apiKey": "old", "lane": "x"})
    br.gm_set("undo", None)
    boot = br._boot_script("/*shim*/", "/*userscript*/", "0.6.0")
    data = json.loads(boot.split("window.__sam3Boot = ", 1)[1].split(";\n", 1)[0])
    assert data["values"]["cfg"] == {"backendUrl": be.base_url, "apiKey": "", "lane": "x"}, "backend của app, key không nằm trong trang"
    be.state = "missing"
    r = br.gm_request({"method": "POST", "url": "http://127.0.0.1:8765/propose"})
    assert r["status"] == 503 and "Chưa có Trọng số SAM 3" in json.loads(r["responseText"])["detail"]
    assert origin_of("https://CVAT.edufun.ai:443/a") == "https://cvat.edufun.ai"
    assert normalize_site("localhost:8080") == "http://localhost:8080" and normalize_site("cvat.abc.vn") == "https://cvat.abc.vn"
    assert normalize_site("javascript:alert(1)") == ""


def test_control_rules(tmp_path, fake):
    make_repo(fake)
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    comps = Components(paths, manifest(fake), allow_http=HTTP, hf_base=fake.base)
    api = ControlApi(paths, comps, FakeBackend("missing"), open_site=lambda o: None, on_quit=lambda: None)
    assert "giấy phép" in api.install(["torch", "sam3"])["message"]
    api.accept_license()
    assert "token" in api.install(["torch", "sam3"])["message"].lower()
    r = api.add_site("Nhà", "http://cvat.congty.com")
    assert r["ok"] and "http" in r["message"], "cảnh báo http trên mạng ngoài"
    assert api.add_site("", "localhost:8080")["message"] == ""
    assert not api.add_site("", "ftp://x")["ok"]
    assert "https://cvat.edufun.ai" in api._settings.origins()
    assert api.open_site("https://khac.com")["ok"] is False
    assert api.open_link("evil") is False
    assert "SAM License" in api.license_text()
