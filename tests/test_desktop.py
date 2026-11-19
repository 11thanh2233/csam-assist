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

    def stop(self):
        self.state = "stopped"

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
    assert data["values"]["cfg"] == {"backendUrl": be.base_url, "apiKey": "", "lane": "x", "lang": "vi"}, \
        "backend của app, key không nằm trong trang, ngôn ngữ chung với app"
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


# ------------------------------------------------------------------ 0.7.0 ---
from desktop import changelog, updater as U  # noqa: E402
from desktop.control import Settings  # noqa: E402


def hf_cache_from(fake, tmp_path, repo="facebook/sam3", windows_copies=False, skip=()):
    """Bộ nhớ đệm Hugging Face như bản cài tay cũ để lại: blobs/<mã băm> + snapshots/<commit>/<file>."""
    cache = tmp_path / "hfcache"
    base = cache / ("models--" + repo.replace("/", "--"))
    info = fake.hf_info(repo)
    for s in info["siblings"]:
        name = s["rfilename"]
        if name in skip:
            continue
        data = fake.files[f"{repo}/{name}"]
        h = (s.get("lfs") or {}).get("sha256") or s["blobId"]
        snap = base / "snapshots" / "oldcommit" / name
        snap.parent.mkdir(parents=True, exist_ok=True)
        if windows_copies:                                  # Windows không có quyền symlink: file nằm thẳng trong snapshots
            snap.write_bytes(data)
        else:
            (base / "blobs").mkdir(parents=True, exist_ok=True)
            (base / "blobs" / h).write_bytes(data)
            snap.symlink_to(base / "blobs" / h)
    return cache


def test_reuse_old_hf_cache_without_token(fake, tmp_path):
    make_repo(fake)
    cache = hf_cache_from(fake, tmp_path)
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    comps = Components(paths, manifest(fake), allow_http=HTTP, hf_base=fake.base, hf_caches=[cache], tool_roots=[])
    p = comps.plan(["sam3"], Gpu())["sam3"]
    assert p["action"] == "install" and p["download"] == 0 and p["reuse"] > 0 and not p["needs_token"]
    info = comps.install("sam3", gpu=Gpu(), token="")           # không token: không phải tải gì
    assert info["reused_files"] == 4 and comps.installed("sam3")
    assert not any("/resolve/" in k for k in fake.seen_auth), "đã có đủ trên máy thì không gọi tải"
    assert (paths.sam3 / "model.safetensors").read_bytes() == fake.files["facebook/sam3/model.safetensors"]
    assert comps.plan(["sam3"], Gpu())["sam3"]["action"] == "none"


def test_reuse_checks_hash_and_downloads_only_missing(fake, tmp_path):
    make_repo(fake)
    cache = hf_cache_from(fake, tmp_path, windows_copies=True, skip=("tokenizer.json",))
    bad = next(cache.rglob("config.json"))
    bad.write_bytes(b'{"model_type": "sam9"}'[: len(bad.read_bytes())].ljust(len(bad.read_bytes()), b" "))  # cùng cỡ, sai nội dung
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    comps = Components(paths, manifest(fake), allow_http=HTTP, hf_base=fake.base, hf_caches=[cache], tool_roots=[])
    assert comps.plan(["sam3"], Gpu(), check_hash=True)["sam3"]["needs_token"]
    with pytest.raises(D.DownloadError, match="chưa duyệt"):
        comps.install("sam3", gpu=Gpu(), token=PENDING)         # phải tải phần thiếu: lúc đó mới cần token
    info = comps.install("sam3", gpu=Gpu(), token=TOKEN)
    assert info["reused_files"] == 2                              # model.safetensors + LICENSE; config sai mã băm thì tải lại
    assert (paths.sam3 / "config.json").read_bytes() == fake.files["facebook/sam3/config.json"]
    assert "/facebook/sam3/resolve/abc123commit/config.json" in fake.seen_auth
    assert "/cdn/facebook/sam3/model.safetensors" not in fake.seen_auth, "file lớn có sẵn đúng mã băm: không tải"


def test_update_downloads_only_changed_weights(fake, tmp_path):
    make_repo(fake)
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    comps = Components(paths, manifest(fake), allow_http=HTTP, hf_base=fake.base, hf_caches=[], tool_roots=[])
    comps.install("sam3", gpu=Gpu(), token=TOKEN)
    fake.seen_auth.clear()
    fake.files["facebook/sam3/config.json"] = b'{"model_type": "sam3", "v": 2}'     # bản mới trên Hugging Face
    p = comps.plan(["sam3"], Gpu())["sam3"]
    assert p["action"] == "update" and p["download"] == len(fake.files["facebook/sam3/config.json"]) and not p["required"]
    comps.install("sam3", gpu=Gpu(), token=TOKEN)
    fetched = [k for k in fake.seen_auth if ("/resolve/" in k or "/cdn/" in k) and "/resolve/main/" not in k]  # main: kiểm quyền
    assert fetched == ["/facebook/sam3/resolve/abc123commit/config.json"]
    assert (paths.sam3 / "model.safetensors").exists()


def test_torch_pin_and_face_reuse(fake, tmp_path):
    wheel = fake_wheel()
    fake.files["torch-0.0.1-py3-none-any.whl"] = wheel
    old_tool = tmp_path / "Downloads" / "VinUni" / "tool" / "sam3-cvat-assist"
    (old_tool / "models").mkdir(parents=True)
    (old_tool / "models" / "face_landmarker.task").write_bytes(b"FACE")
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    m = manifest(fake, wheel)
    comps = Components(paths, m, allow_http=HTTP, hf_base=fake.base, hf_caches=[], tool_roots=[tmp_path / "Downloads"])
    gpu = Gpu("RTX 2060", 6144, "560.1")
    assert comps.tool_dirs() == [old_tool]
    face = comps.plan(["face"], gpu)["face"]
    assert face["download"] == 0 and face["reuse"] == 4
    comps.install("face", gpu=gpu)
    assert paths.face.read_bytes() == b"FACE" and comps.state()["face"]["reused_from"]
    comps.install("torch", gpu=gpu)
    assert comps.plan(["torch"], gpu)["torch"]["action"] == "none"
    m["torch"]["cuda"][0]["version"] = "0.0.2"                   # bản app mới ghim PyTorch khác
    t = comps.plan(["torch"], gpu)["torch"]
    assert t["action"] == "update" and t["required"] and "0.0.2" in t["reason"]
    m["torch"]["cuda"][0]["version"] = "0.0.1+cu126"              # cùng bản (nhãn CUDA không tính)
    assert comps.plan(["torch"], gpu)["torch"]["action"] == "none"


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    release: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/latest"):
            body = json.dumps(FakeGitHub.release).encode()
        else:
            body = FakeGitHub.payload
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_updater_checks_release_and_hash(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    FakeGitHub.payload = b"MZ" + b"\0" * 1000
    sha = hashlib.sha256(FakeGitHub.payload).hexdigest()
    asset = {"name": "SAM3-CVAT-Assist-Setup-0.7.1.exe", "size": len(FakeGitHub.payload),
             "browser_download_url": f"{base}/dl/SAM3-CVAT-Assist-Setup-0.7.1.exe"}
    kw = dict(api=f"{base}/api/latest", allow_http=("127.0.0.1",), prefix=f"{base}/dl/")
    FakeGitHub.release = {"tag_name": "v0.7.1", "body": "## 0.7.1\n- sửa lỗi", "assets": [dict(asset, digest=f"sha256:{sha}")]}
    info = U.check("0.7.0", **kw)
    assert info["available"] and info["auto"] and info["sha256"] == sha
    assert not U.check("0.7.1", **kw)["available"] and not U.check("0.8.0", **kw)["available"], "không hạ bản"
    FakeGitHub.release = {"tag_name": "v0.7.1", "body": f"SHA-256 của bộ cài: `{sha}`", "assets": [asset]}
    assert U.check("0.7.0", **kw)["sha256"] == sha, "lấy mã băm trong ghi chú khi GitHub không có digest"
    FakeGitHub.release = {"tag_name": "v0.7.1", "body": "", "assets": [asset]}
    assert not U.check("0.7.0", **kw)["auto"], "không có mã băm: không tự cập nhật"
    FakeGitHub.release = {"tag_name": "v0.7.1", "body": "", "assets": [dict(asset, digest=f"sha256:{sha}")]}
    assert not U.check("0.7.0", api=kw["api"], allow_http=kw["allow_http"])["auto"], "địa chỉ tải ngoài github.com: từ chối"

    up = U.Updater(tmp_path / "upd", allow_http=("127.0.0.1",))
    assert up.start(info)
    up.thread.join(10)
    assert Path(up.p["ready"]).read_bytes() == FakeGitHub.payload and not up.p["error"]
    FakeGitHub.payload = b"not an exe"
    bad = dict(info, sha256=hashlib.sha256(b"not an exe").hexdigest(), size=10, name="SAM3-CVAT-Assist-Setup-0.7.2.exe")
    up.start(bad)
    up.thread.join(10)
    assert "không phải bộ cài" in up.p["error"] and not (tmp_path / "upd" / bad["name"]).exists()
    srv.shutdown()


def test_whats_new_and_changelog(tmp_path):
    log = tmp_path / "CHANGELOG.md"
    log.write_text("# Nhật ký\n\n## 0.7.1 (x)\n- c\n\n## 0.7.0\n- b\n\n## 0.6.0\n- a\n", encoding="utf-8")
    assert [e["version"] for e in changelog.between(log, "0.6.0", "0.7.1")] == ["0.7.1", "0.7.0"]
    assert changelog.section(log, "0.7.0") == "- b"
    real = Path(__file__).resolve().parent.parent / "docs" / "CHANGELOG.md"
    from backend import __version__

    assert changelog.section(real, __version__), "CHANGELOG phải có mục cho bản hiện tại (bước build dùng làm ghi chú Release)"
    fresh = Settings(tmp_path / "s1.json")
    assert fresh.data["last_seen_version"] == __version__, "cài mới: không hiện khung có gì mới"
    (tmp_path / "s2.json").write_text('{"sites": []}', encoding="utf-8")
    assert Settings(tmp_path / "s2.json").data["last_seen_version"] == "0.6.0", "có cài đặt cũ: đang lên từ 0.6.0"


def test_control_install_skips_when_up_to_date(tmp_path, fake):
    make_repo(fake)
    cache = hf_cache_from(fake, tmp_path)
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    comps = Components(paths, manifest(fake), allow_http=HTTP, hf_base=fake.base, hf_caches=[cache], tool_roots=[])
    api = ControlApi(paths, comps, FakeBackend("missing"), open_site=lambda o: None, on_quit=lambda: None)
    api.accept_license()
    r = api.install(["sam3"])                    # đủ file trong bộ nhớ đệm: không cần token
    assert r["ok"], r
    api._installer.thread.join(20)
    assert not api._installer.p["error"] and comps.installed("sam3")
    assert "không cần tải" in api.install(["sam3"])["message"]
    sc = api.scan()
    assert sc["plan"]["sam3"]["action"] == "none" and "sam3" not in sc["todo"]


# ------------------------------------------------------------------ 0.8.0 ---
from desktop import support  # noqa: E402
from desktop.backend_proc import BackendProcess  # noqa: E402


def test_report_bundle_hides_secrets(tmp_path):
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent).ensure()
    (paths.logs / "backend.log").write_text("dùng token hf_abcdefghijklmnop\nX-API-Key: supersecret\n", encoding="utf-8")
    paths.settings.write_text(json.dumps({"sites": [], "lan_key": "LANKEY", "remote_backend": {"base": "x", "key": "RK"}}), encoding="utf-8")
    (paths.webview / "Cookies").write_text("session=abc", encoding="utf-8")
    out = support.report_bundle(paths, {"app_version": "0.8.0"}, out_dir=tmp_path)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        blob = " ".join(z.read(n).decode("utf-8") for n in names)
    assert "logs/backend.log" in names and "system.json" in names
    assert not any("webview" in n or "store" in n for n in names), "không gửi cookie đăng nhập hay dữ liệu panel"
    for secret in ("hf_abcdefghijklmnop", "supersecret", "LANKEY", "RK"):
        assert secret not in blob


def test_backup_restore_and_sync(tmp_path):
    app = Path(__file__).resolve().parent.parent
    a = Paths(tmp_path / "a", app_dir=app).ensure()
    (a.profiles / "p.yaml").write_text("v1", encoding="utf-8")
    (a.library / "learning").mkdir(parents=True)
    (a.library / "learning" / "model.json").write_text("{}", encoding="utf-8")
    Store(a.store).set("cfg", {"apiKey": "k", "opacity": 0.5})
    res = support.backup(a, tmp_path / "b.zip")
    assert res["files"] == 2
    b = Paths(tmp_path / "b", app_dir=app).ensure()
    store_b = Store(b.store)
    store_b.set("cfg", {"backendUrl": "http://127.0.0.1:9", "apiKey": "mine"})
    out = support.restore(b, tmp_path / "b.zip", store_b)
    assert out["restored"] == 2 and (b.profiles / "p.yaml").read_text() == "v1" and Path(out["safety_backup"]).exists()
    assert store_b.all()["cfg"] == {"backendUrl": "http://127.0.0.1:9", "apiKey": "mine", "opacity": 0.5}, "không ghi đè key/địa chỉ"
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("backup.json", "{}")
        z.writestr("profiles/../../x.txt", "x")
    with pytest.raises(ValueError):
        support.restore(b, evil)
    with pytest.raises(ValueError, match="không phải bản sao lưu"):
        support.restore(b, _plain_zip(tmp_path))

    shared = tmp_path / "shared"
    push = support.sync(a, shared, "push")
    assert "profiles/p.yaml" in push["copied"] and not any("learning" in c for c in push["copied"]), "không gửi dữ liệu tự học"
    (shared / "SAM3Assist" / "profiles" / "new.yaml").write_text("team", encoding="utf-8")
    pull = support.sync(b, shared, "pull")
    assert "profiles/new.yaml" in pull["copied"] and (b.profiles / "new.yaml").read_text() == "team"
    import os
    import time as _t

    (b.profiles / "p.yaml").write_text("của tôi", encoding="utf-8")
    future = _t.time() + 100
    os.utime(b.profiles / "p.yaml", (future, future))
    (shared / "SAM3Assist" / "profiles" / "p.yaml").write_text("bản nhóm", encoding="utf-8")
    pull2 = support.sync(b, shared, "pull")
    assert (b.profiles / "p.yaml").read_text(encoding="utf-8") == "của tôi" and pull2["conflicts"] == ["profiles/p.yaml"]
    assert (b.profiles / "p.yaml.tu-thu-muc-chung").read_text(encoding="utf-8") == "bản nhóm"


def _plain_zip(tmp_path):
    f = tmp_path / "plain.zip"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("hello.txt", "x")
    return f


class FakeRemote(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "engine": "sam3", "device": "cuda", "version": "0.8.0"})
        if self.headers.get("X-API-Key") != "team-key":
            return self._send(401, {"detail": "key"})
        self._send(200, {"profiles": [], "path": self.path})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.headers.get("X-API-Key") != "team-key":
            return self._send(401, {"detail": "key"})
        self._send(200, {"ok": True, "path": self.path})


def test_remote_backend_and_lan(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeRemote)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    assert not support.check_remote("ftp://x", "k")["ok"]
    assert "https" in support.check_remote("http://example.com:8765", "k")["message"], "qua Internet phải https"
    assert "key không đúng" in support.check_remote(url, "sai")["message"]
    assert support.check_remote(url, "team-key")["ok"]
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    comps = Components(paths, manifest={"torch": {}, "models": {}})
    be = BackendProcess(comps)
    api = ControlApi(paths, comps, be, open_site=lambda o: None, on_quit=lambda: None)
    r = api.set_remote(url, "team-key")
    assert r["ok"] and be.info()["state"] == "running" and be.info()["remote"]
    assert be.page_url.startswith("http://127.0.0.1:"), "trang vẫn gọi 127.0.0.1, cầu nối chuyển sang máy kia"
    status, text = be.request("POST", "/propose", "{}")
    assert status == 200 and json.loads(text)["path"] == "/propose", "yêu cầu đi tới máy kia kèm key nhóm"
    br = Bridge(Store(paths.store), be, lambda: {"https://cvat.edufun.ai"}, lambda: "https://cvat.edufun.ai/tasks/1/jobs/2")
    assert br.gm_request({"method": "POST", "url": "http://127.0.0.1:8765/propose", "data": "{}"})["status"] == 200
    reopened = ControlApi(paths, Components(paths, manifest={}), BackendProcess(Components(paths, manifest={})), open_site=lambda o: None, on_quit=lambda: None)
    assert reopened._backend.remote["base"] == url, "nhớ backend ở máy khác cho lần mở sau"
    api.clear_remote()
    assert be.remote is None
    info = api.set_lan_share(True)
    assert info["share"] and len(info["key"]) >= 20 and be.share_key == info["key"]
    assert api.set_lan_share(False)["key"] == "" and be.share_key == ""
    srv.shutdown()


def test_recent_jobs_settings_and_bridge_notify(tmp_path):
    paths = Paths(tmp_path / "data", app_dir=Path(__file__).resolve().parent.parent)
    opened = []
    be = FakeBackend()
    be.extra_env = {}
    api = ControlApi(paths, Components(paths, manifest={}), be, open_site=opened.append, on_quit=lambda: None)
    api._record_visit("https://cvat.edufun.ai/tasks/5/jobs/12?frame=3")
    api._record_visit("https://evil.example/tasks/1/jobs/1")
    api._record_visit("https://cvat.edufun.ai/tasks/6/jobs/20")
    api._record_visit("https://cvat.edufun.ai/tasks/5/jobs/12")
    jobs = api.recent_jobs()
    assert [j["job"] for j in jobs] == [12, 20] and jobs[0]["url"] == "https://cvat.edufun.ai/tasks/5/jobs/12"
    assert api.open_job(jobs[0]["url"])["ok"] and opened == ["https://cvat.edufun.ai/tasks/5/jobs/12"]
    assert not api.open_job("https://evil.example/tasks/1/jobs/1")["ok"]
    assert api.set_setting("idle_unload_min", 30) and be.extra_env["SAM3_IDLE_UNLOAD_MIN"] == "30"
    assert not api.set_setting("start_mode", "bogus") and not api.set_setting("lan_key", "x")
    notes, shown = [], []
    url = {"v": "https://cvat.edufun.ai/tasks/5/jobs/12"}
    br = Bridge(Store(paths.store), be, lambda: {"https://cvat.edufun.ai"}, lambda: url["v"],
                notifier=lambda t, x: notes.append((t, x)), show_control=lambda: shown.append(1))
    br.notify("Gợi ý cả job xong", "Job 12")
    br.open_control()
    assert notes == [("Gợi ý cả job xong", "Job 12")] and shown == [1]
    url["v"] = "https://evil.example/"
    with pytest.raises(PermissionError):
        br.notify("x", "y")
