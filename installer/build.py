"""Dựng thư mục cho bộ cài Windows (chạy trên máy build Windows của GitHub Actions, Python 3.12 x64).

  python installer/build.py --out build [--verify-torch]

Kết quả:
  build/runtime/   Python 3.12 bản nhúng (embeddable) của python.org + các thư viện nhỏ (đã kiểm mã băm)
  build/app/       code của tool + desktop/manifest.lock.json (địa chỉ và mã băm của phần tải khi cài)
  build/build-info.json

An toàn chuỗi cung ứng:
  - Python bản nhúng: kiểm chữ ký Sigstore của python.org trước khi dùng.
  - Thư viện: pip phân giải một lần, ghi lại mã băm sha256 do chỉ mục công bố, rồi cài với --require-hashes.
  - PyTorch (tải trên máy người dùng): ghim đúng file wheel + sha256 + dung lượng vào manifest. Với --verify-torch,
    máy build tải thật bản CUDA, kiểm mã băm và kiểm có hỗ trợ GPU dòng RTX 20 (sm_75).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_VERSION = "3.12.10"
PY_SIGNER = ("thomas@python.org", "https://accounts.google.com")      # người phát hành Python 3.12
TORCH_INDEXES = ["cu126", "cu128", "cu129", "cu130"]
NEED_ARCH = "sm_75"                                                  # RTX 20xx / GTX 16xx
FACE_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
# 0.9.6: pose toàn thân RTMW-x-l 256x192 (OpenMMLab, Apache-2.0), bản ONNX của mmdeploy
WHOLEBODY_URL = ("https://download.openmmlab.com/mmpose/v1/projects/rtmw/onnx_sdk/"
                 "rtmw-dw-x-l_simcc-cocktail14_270e-256x192_20231122.zip")
# 0.9.4: trọng số khởi đầu (COCO) cho model riêng ở tab Tự học; torchvision, giấy phép BSD-3-Clause
DETECTOR_URL = "https://download.pytorch.org/models/fasterrcnn_mobilenet_v3_large_fpn-fb6a3cc7.pth"
# 0.9.8: bộ dò vật 2D cho job 3D: RT-DETR r18vd (COCO, Apache-2.0), bản ONNX của onnx-community, ghim theo commit +
# mã băm công bố trên Hugging Face (kiểm khi build và khi tải trên máy người dùng)
RTDETR_URL = ("https://huggingface.co/onnx-community/rtdetr_r18vd/resolve/"
              "f2bee7444f4c29003df7e9ef07c4c1c6632eca6f/onnx/model.onnx")
RTDETR_SHA256 = "11843b02455cc24009aed24d4c40db721b1093be5ccd6bbe7b9c441abb1d0558"
RTDETR_SIZE = 82_572_357
PLATFORM = ["--platform", "win_amd64", "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
            "--only-binary=:all:"]
HEAVY = {"torch", "torchvision"}
SDIST_ONLY = {"proxy-tools": "0.1.0"}          # phụ thuộc của pywebview, PyPI chỉ có mã nguồn: tự dựng wheel
APP_PARTS = ["backend", "desktop", "profiles", "guideline_library", "userscript", "README.md", "docs", "LICENSE"]
SKIP = {"__pycache__", ".pytest_cache", "runs", "learning", "lessons.jsonl", "manifest.lock.json"}


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def fetch(url: str, dest: Path) -> Path:
    log(f"tải {url}")
    with urllib.request.urlopen(url, timeout=600) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    return dest


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def head_size(url: str) -> int | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except OSError:
        return None


# ------------------------------------------------------------------ python ---
def build_runtime(out: Path, verify_sig: bool) -> Path:
    runtime = out / "runtime"
    shutil.rmtree(runtime, ignore_errors=True)
    tmp = Path(tempfile.mkdtemp())
    name = f"python-{PY_VERSION}-embed-amd64.zip"
    url = f"https://www.python.org/ftp/python/{PY_VERSION}/{name}"
    z = fetch(url, tmp / name)
    if verify_sig:
        bundle = fetch(url + ".sigstore", tmp / (name + ".sigstore"))
        subprocess.run([sys.executable, "-m", "sigstore", "verify", "identity", "--bundle", str(bundle),
                        "--cert-identity", PY_SIGNER[0], "--cert-oidc-issuer", PY_SIGNER[1], str(z)], check=True)
        log("chữ ký Sigstore của Python bản nhúng: hợp lệ")
    with zipfile.ZipFile(z) as zf:
        zf.extractall(runtime)
    pth = next(runtime.glob("python3*._pth"))
    zipname = next(runtime.glob("python3*.zip")).name
    # Đường dẫn import cố định: thư viện + code của tool. Có file ._pth thì Python bỏ qua PYTHONPATH của máy.
    pth.write_text(f"{zipname}\n.\nLib\\site-packages\n..\\app\nimport site\n", encoding="utf-8")
    (runtime / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    return runtime


# ---------------------------------------------------------------- resolve ---
WHEELHOUSE: Path | None = None


def build_sdist_wheels(tmp: Path) -> Path:
    """Dựng wheel cho gói PyPI chỉ có mã nguồn. File nguồn được kiểm theo mã băm PyPI công bố."""
    house = tmp / "wheelhouse"
    house.mkdir(parents=True, exist_ok=True)
    for name, ver in SDIST_ONLY.items():
        data = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json", timeout=60))
        sd = next(f for f in data["urls"] if f["packagetype"] == "sdist")
        src = fetch(sd["url"], tmp / sd["filename"])
        if sha256(src) != sd["digests"]["sha256"]:
            raise SystemExit(f"Mã băm mã nguồn {sd['filename']} không khớp PyPI.")
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--quiet", "-w", str(house), str(src)], check=True)
    return house


def _links() -> list[str]:
    return ["--find-links", str(WHEELHOUSE)] if WHEELHOUSE else []


def pip_report(reqs: list[str], index: str | None, extra: list[str] | None = None) -> list[dict]:
    with tempfile.TemporaryDirectory() as d:
        rep = Path(d) / "report.json"
        cmd = [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet", "--report", str(rep),
               "--target", str(Path(d) / "t"), "--disable-pip-version-check", *PLATFORM, *_links(), *(extra or [])]
        if index:
            cmd += ["--index-url", index]
        subprocess.run(cmd + reqs, check=True)
        data = json.loads(rep.read_text(encoding="utf-8"))
    out = []
    for item in data["install"]:
        meta, dl = item["metadata"], item["download_info"]
        hashes = (dl.get("archive_info") or {}).get("hashes") or {}
        h = hashes.get("sha256") or ((dl.get("archive_info") or {}).get("hash") or "").removeprefix("sha256=")
        if not h and dl["url"].startswith("file:"):          # wheel tự dựng trong wheelhouse
            h = sha256(Path(urllib.request.url2pathname(urllib.parse.urlsplit(dl["url"]).path)))
        out.append({"name": meta["name"].lower().replace("_", "-"), "version": meta["version"], "url": dl["url"],
                    "sha256": h, "filename": urllib.parse.unquote(dl["url"].rsplit("/", 1)[-1].split("#")[0])})   # %2B -> + (torch+cu126)
    return out


def torch_candidates() -> list[tuple[str, dict, dict]]:
    """Các bản PyTorch CUDA cho Windows/Python 3.12, mới nhất trước; cùng bản thì CUDA thấp trước (hợp nhiều driver hơn)."""
    # Ghim bản PyTorch (installer/torch-pin.txt): bản app mới không bắt người dùng tải lại 2,5 GB chỉ vì
    # PyTorch vừa ra bản mới. Muốn nâng PyTorch thì sửa file này; app sẽ báo cần cập nhật.
    pin_file = ROOT / "installer" / "torch-pin.txt"
    pin = pin_file.read_text(encoding="utf-8").strip() if pin_file.exists() else ""
    found = []
    for tag in TORCH_INDEXES:
        try:
            items = pip_report(["torch", "torchvision"], f"https://download.pytorch.org/whl/{tag}")
        except subprocess.CalledProcessError:
            log(f"{tag}: không có bản cho Windows/Python 3.12")
            continue
        t = next(i for i in items if i["name"] == "torch")
        tv = next(i for i in items if i["name"] == "torchvision")
        if pin and t["version"].split("+")[0] != pin:
            log(f"{tag}: mới nhất là {t['version']}, khác bản ghim {pin}")
            try:
                items = pip_report([f"torch=={pin}", "torchvision"], f"https://download.pytorch.org/whl/{tag}")
            except subprocess.CalledProcessError:
                log(f"{tag}: không có torch {pin}")
                continue
            t = next(i for i in items if i["name"] == "torch")
            tv = next(i for i in items if i["name"] == "torchvision")
        base = tuple(int(x) for x in re.findall(r"\d+", t["version"].split("+")[0])[:3])
        log(f"{tag}: torch {t['version']}, torchvision {tv['version']}")
        found.append((base, TORCH_INDEXES.index(tag), tag, t, tv))
    if not found:
        raise SystemExit("Không tìm được PyTorch CUDA cho Windows/Python 3.12.")
    found.sort(key=lambda f: (tuple(-x for x in f[0]), f[1]))
    return [(tag, t, tv) for _, _, tag, t, tv in found]


def fill_hash_and_size(item: dict, tmp: Path, download: bool = False) -> dict:
    if download or not item["sha256"]:
        f = fetch(item["url"], tmp / item["filename"])
        item["sha256"] = sha256(f) if not item["sha256"] else item["sha256"]
        if sha256(f) != item["sha256"]:
            raise SystemExit(f"Mã băm của {item['filename']} không khớp chỉ mục.")
        item["size"] = f.stat().st_size
        item["_file"] = str(f)
    else:
        item["size"] = head_size(item["url"])
    return item


def verify_torch_arch(wheel: Path) -> str:
    with tempfile.TemporaryDirectory() as d:
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--quiet", "--target", d, str(wheel)], check=True)
        out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import torch; "
                              "print(torch.version.cuda); print(torch._C._cuda_getArchFlags())", d],
                             capture_output=True, text=True, check=True).stdout.split()
    if NEED_ARCH not in " ".join(out):
        raise SystemExit(f"Bản PyTorch này không hỗ trợ {NEED_ARCH} (RTX 20xx): {out}")
    return " ".join(out)


def requirements() -> list[str]:
    reqs = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            reqs.append(line.replace("uvicorn[standard]", "uvicorn"))   # bản nhúng chỉ cần uvicorn cơ bản
    return reqs + (ROOT / "desktop" / "requirements-desktop.txt").read_text(encoding="utf-8").split()


def mediapipe_wheel() -> dict:
    data = json.load(urllib.request.urlopen("https://pypi.org/pypi/mediapipe/json", timeout=60))
    for f in data["urls"]:
        if f["filename"].endswith("win_amd64.whl") and ("py3-none" in f["filename"] or "cp312" in f["filename"]):
            return {"name": "mediapipe", "version": data["info"]["version"], "url": f["url"], "sha256": f["digests"]["sha256"],
                    "filename": f["filename"], "size": f["size"]}
    raise SystemExit("Không thấy wheel mediapipe cho Windows.")


def install_bundled(runtime: Path, items: list[dict]) -> None:
    lock = runtime.parent / "bundled.lock.txt"
    lock.write_text("".join(f"{i['name']}=={i['version']} --hash=sha256:{i['sha256']}\n" for i in items), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--require-hashes", "--quiet", "--no-compile",
                    "--disable-pip-version-check", "--target", str(runtime / "Lib" / "site-packages"), *PLATFORM,
                    *_links(), "-r", str(lock)], check=True)


# -------------------------------------------------------------------- app ---
def copy_app(out: Path) -> Path:
    app = out / "app"
    shutil.rmtree(app, ignore_errors=True)
    for part in APP_PARTS:
        src = ROOT / part
        if src.is_dir():
            shutil.copytree(src, app / part, ignore=lambda d, names: [n for n in names if n in SKIP or n.endswith(".pyc")])
        elif src.exists():
            app.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, app / part)
    return app


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "backend" / "__init__.py").read_text()).group(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build")
    ap.add_argument("--verify-torch", action="store_true", help="tải bản PyTorch CUDA để kiểm mã băm và sm_75")
    ap.add_argument("--no-sigstore", action="store_true")
    args = ap.parse_args()
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())

    global WHEELHOUSE
    runtime = build_runtime(out, verify_sig=not args.no_sigstore)
    WHEELHOUSE = build_sdist_wheels(tmp)
    candidates = torch_candidates()
    arch = "chưa kiểm"
    tag, t_cuda, tv_cuda = candidates[0]
    if args.verify_torch:                    # chọn bản đầu tiên tải được, đúng mã băm và có sm_75
        for tag, t_cuda, tv_cuda in candidates:
            fill_hash_and_size(t_cuda, tmp, download=True)
            try:
                arch = verify_torch_arch(Path(t_cuda["_file"]))
                break
            except SystemExit as exc:
                log(f"bỏ {t_cuda['version']}: {exc}")
                Path(t_cuda["_file"]).unlink(missing_ok=True)
        else:
            raise SystemExit("Không bản PyTorch CUDA nào hỗ trợ RTX 20xx (sm_75).")
    else:
        fill_hash_and_size(t_cuda, tmp)
    cuda = [t_cuda, fill_hash_and_size(tv_cuda, tmp)]

    base_t, base_tv = t_cuda["version"].split("+")[0], tv_cuda["version"].split("+")[0]
    items = pip_report(requirements() + [f"torch=={base_t}", f"torchvision=={base_tv}"], None)
    missing = [i["name"] for i in items if not i["sha256"]]
    if missing:
        raise SystemExit(f"Chỉ mục không công bố mã băm cho: {missing}")
    bundled = [i for i in items if i["name"] not in HEAVY]
    cpu = [fill_hash_and_size(i, tmp) for i in items if i["name"] in HEAVY]
    log(f"cài {len(bundled)} thư viện nhỏ vào bản nhúng (kiểm mã băm)")
    install_bundled(runtime, bundled)
    mp = mediapipe_wheel()
    install_bundled(runtime, [mp])            # --no-deps: dùng opencv-python-headless sẵn có thay cho opencv-contrib-python

    log(f"PyTorch {t_cuda['version']} ({tag}), CUDA/arch: {arch}")
    face = fetch(FACE_URL, tmp / "face_landmarker.task")
    detector = fetch(DETECTOR_URL, tmp / "fasterrcnn_mobilenet_v3_large_fpn_coco.pth")
    wholebody = fetch(WHOLEBODY_URL, tmp / "rtmw-wholebody.zip")
    import zipfile

    with zipfile.ZipFile(wholebody) as z:
        wb_member = next((n for n in z.namelist() if n.endswith(".onnx")), None)
    if not wb_member:
        raise SystemExit("File zip RTMW không có model .onnx")
    # torchvision ghi 8 ký tự đầu của mã băm sha256 vào tên file: kiểm khớp trước khi ghi vào manifest
    assert sha256(detector).startswith(DETECTOR_URL.rsplit("-", 1)[1].split(".")[0]), "mã băm trọng số khởi đầu không khớp tên file"

    app = copy_app(out)
    clean = lambda ws: [{k: v for k, v in w.items() if not k.startswith("_")} for w in ws]  # noqa: E731
    manifest = {
        "version": version(), "python": PY_VERSION,
        "torch": {"pin": base_t, "cuda_index": tag, "cuda_arch": arch, "cuda": clean(cuda), "cpu": clean(cpu)},
        "models": {
            "sam3": {"repo": "facebook/sam3", "revision": "main", "gated": True, "approx_size": 3_450_000_000},
            "vitpose": {"repo": "usyd-community/vitpose-base-simple", "revision": "main", "approx_size": 360_000_000},
            "face": {"url": FACE_URL, "sha256": sha256(face), "size": face.stat().st_size},
            "detector": {"url": DETECTOR_URL, "sha256": sha256(detector), "size": detector.stat().st_size},
            "reid": {"repo": "facebook/dinov2-small", "revision": "main", "approx_size": 90_000_000},
            "depth": {"repo": "depth-anything/Depth-Anything-V2-Small-hf", "revision": "main", "approx_size": 100_000_000},
            "wholebody": {"url": WHOLEBODY_URL, "sha256": sha256(wholebody), "size": wholebody.stat().st_size,
                          "filename": "rtmw-wholebody.zip", "zip_member": wb_member},
            "rtdetr": {"url": RTDETR_URL, "sha256": RTDETR_SHA256, "size": RTDETR_SIZE},
        },
    }
    (app / "desktop" / "manifest.lock.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    info = {"version": version(), "python": PY_VERSION, "bundled": {i["name"]: i["version"] for i in bundled + [mp]},
            "torch": t_cuda["version"], "torchvision": tv_cuda["version"], "cuda_index": tag, "cuda_arch": arch}
    (out / "build-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    log(json.dumps(info, indent=1))


if __name__ == "__main__":
    main()
