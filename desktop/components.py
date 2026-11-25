"""Các thành phần nặng tải khi cài (không nằm trong bộ cài) và trạng thái của chúng.

  torch    PyTorch + torchvision (bản CUDA nếu máy có card NVIDIA, bản CPU nếu không)   bắt buộc
  sam3     trọng số SAM 3 (facebook/sam3, repo có cổng: cần token đã được Meta duyệt)    bắt buộc
  vitpose  trọng số ViTPose cho skeleton người                                          tuỳ chọn
  face     model khuôn mặt MediaPipe (50 điểm mặt)                                      tuỳ chọn
  reid     DINOv2-small: nhận lại đúng người sau khi khuất (0.9.6)                      tuỳ chọn
  depth    Depth Anything V2 Small: ai đứng trước ai (0.9.6)                            tuỳ chọn
  wholebody RTMW (ONNX): pose toàn thân 133 điểm, có bàn tay, bàn chân (0.9.6)           tuỳ chọn

Danh sách file, địa chỉ và mã băm của PyTorch và model khuôn mặt do bước build ghim sẵn trong
desktop/manifest.lock.json. Trọng số trên Hugging Face được ghim theo commit lúc tải, mỗi file kiểm tra theo
mã băm mà Hugging Face công bố. Chỉ tải file cần cho transformers (bỏ checkpoint trùng .pt/.bin).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import downloader as D
from . import reuse as R
from .paths import Paths

COMPONENTS = {
    "torch": {"title": "PyTorch", "required": True},
    "sam3": {"title": "Trọng số SAM 3", "required": True},
    "vitpose": {"title": "Skeleton người (ViTPose)", "required": False},
    "face": {"title": "Skeleton mặt (MediaPipe)", "required": False},
    "detector": {"title": "Trọng số khởi đầu cho model riêng", "required": False},
    "reid": {"title": "Nhận lại người (DINOv2)", "required": False},
    "depth": {"title": "Độ sâu: ai đứng trước ai (Depth Anything V2)", "required": False},
    "wholebody": {"title": "Pose toàn thân: tay, bàn chân (RTMW)", "required": False},
    # 0.9.7: model ONNX cho job 3D (spec 3D v2 mục 2 & 3.2). Tải rời, chỉ dùng khi job 3D cần.
    "onnx2d": {"title": "Dò vật 2D cho job 3D (ONNX)", "required": False},
    "onnx3d": {"title": "Dò vật 3D LiDAR (CenterPoint/PointPillars ONNX)", "required": False},
}

# ---------------------------------------------------------------- ONNX 3D ---
# Bảng cấu hình rời (spec 3D v2 mục 2.4): địa chỉ tải + giấy phép của từng model, tách khỏi mã xử lý để chốt lại với
# mentor mà không phải sửa code. Mặc định ưu tiên giấy phép thoáng (Apache-2.0 / MIT). Model không có giấy phép rõ ràng
# thì ghi "chưa rõ" và tool cảnh báo trước khi tải.
ONNX_MODELS: dict[str, dict] = {
    "onnx2d": {
        "choices": {
            "rtdetr-l": {
                "role": "Dò vật 2D (RT-DETR-L, COCO 80 class)",
                "url": "",                       # để trống: người dùng trỏ tới file .onnx đã có, hoặc điền khi chốt model
                "license": "Apache-2.0",
                "classes": "coco80",
                "input": [1, 3, 640, 640],
                "note": "Nhanh, ổn định trên GPU; đủ cho xe/người/xe hai bánh.",
            },
            "dfine": {
                "role": "Dò vật 2D (D-FINE, COCO 80 class)",
                "url": "",
                "license": "Apache-2.0",
                "classes": "coco80",
                "input": [1, 3, 640, 640],
                "note": "Chính xác hơn ở vật nhỏ, chậm hơn RT-DETR một chút.",
            },
        },
        "default": "rtdetr-l",
    },
    "onnx3d": {
        "choices": {
            "pointpillars": {
                "role": "Dò vật 3D LiDAR (PointPillars, KITTI 3 class)",
                "url": "",
                "license": "Apache-2.0",
                "classes": "kitti3",
                "note": "Nhẹ, chạy tốt trên GPU 4 GB; cần hiệu chỉnh voxel theo dataset.",
            },
            "centerpoint": {
                "role": "Dò vật 3D LiDAR (CenterPoint, nuScenes/KITTI)",
                "url": "",
                "license": "Apache-2.0",
                "classes": "nuscenes10",
                "note": "Chính xác hơn PointPillars, cần nhiều VRAM hơn.",
            },
        },
        "default": "pointpillars",
    },
}
# Nhãn COCO mà bộ dò 2D dùng cho job 3D (class_key của lidar -> id COCO). Lớp không có ở đây thì bỏ qua.
COCO_FOR_3D = {"car": 2, "truck": 7, "bus": 5, "trailer": 7, "construction vehicle": 7,
               "pedestrian": 0, "two wheels": 3, "barrier": 0}


def onnx_models(kind: str) -> dict:
    """Bảng lựa chọn model ONNX của một loại ('onnx2d' / 'onnx3d') kèm giấy phép, để panel hiện và bạn chọn."""
    spec = ONNX_MODELS.get(kind) or {}
    return {"kind": kind, "default": spec.get("default"), "choices": spec.get("choices") or {}}


def license_warning(kind: str, choice: str) -> str:
    """Cảnh báo trước khi tải: model chưa rõ giấy phép thì nói rõ để bạn hỏi mentor."""
    spec = (ONNX_MODELS.get(kind) or {}).get("choices", {}).get(choice) or {}
    lic = str(spec.get("license") or "chưa rõ")
    if lic.lower() in ("chưa rõ", "", "unknown"):
        return f"Model '{choice}' chưa rõ giấy phép: hỏi mentor trước khi dùng cho dữ liệu thật."
    return ""

FILE_MODELS = ("face", "detector", "wholebody")
HF_MODELS = ("sam3", "vitpose", "reid", "depth")
# 0.9.7: model ONNX 3D tải theo file người dùng trỏ tới (hoặc URL chốt sau với mentor) — không nằm trong manifest
# build vì chưa chốt checkpoint; panel hiện bảng ONNX_MODELS để chọn, tool chỉ dùng khi job 3D cần.
ONNX_FILE_KINDS = ("onnx2d", "onnx3d")
ORDER = ("torch", "sam3", "vitpose", "face", "detector", "reid", "depth", "wholebody")          # thành phần là một file tải theo địa chỉ + mã băm trong manifest
TORCH_EXTRACT = 2.1          # bản cài PyTorch chiếm khoảng 2 lần file tải về
MIN_DRIVER = 528.0           # driver NVIDIA tối thiểu cho PyTorch CUDA 12 trên Windows


def load_manifest(paths: Paths) -> dict:
    if not paths.manifest.exists():
        raise D.DownloadError("Thiếu desktop/manifest.lock.json (bản build lỗi). Cài lại bằng bộ cài mới.")
    return json.loads(paths.manifest.read_text(encoding="utf-8"))


def _no_window() -> dict:
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}   # CREATE_NO_WINDOW


@dataclass
class Gpu:
    name: str = ""
    vram_mb: int = 0
    driver: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.name)

    def driver_too_old(self) -> bool:
        try:
            return float(self.driver.split()[0]) < MIN_DRIVER
        except (ValueError, IndexError):
            return False


def detect_gpu() -> Gpu:
    exe = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if sys.platform == "win32" else None)
    if not exe or not Path(exe).exists():
        return Gpu()
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15, **_no_window()).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return Gpu()
    if not out:
        return Gpu()
    parts = [p.strip() for p in out[0].split(",")]
    try:
        return Gpu(parts[0], int(float(parts[1])), parts[2])
    except (IndexError, ValueError):
        return Gpu(parts[0] if parts else "")


class Components:
    def __init__(self, paths: Paths, manifest: Optional[dict] = None, allow_http: tuple[str, ...] = (),
                 hf_base: str = D.HF, hf_caches: Optional[list[Path]] = None, tool_roots: Optional[list[Path]] = None):
        self.paths = paths.ensure()
        self._manifest = manifest
        self.allow_http, self.hf_base = allow_http, hf_base
        self.extra_dirs: list[Path] = []          # thư mục tool cũ bạn tự thêm để quét
        self._hf_caches, self._tool_roots = hf_caches, tool_roots
        self._tool_dirs: Optional[list[Path]] = None

    @property
    def system(self) -> bool:
        """Chạy từ mã nguồn (macOS/Linux, hoặc Windows không qua bộ cài): không có manifest; PyTorch và model do
        người dùng tự cài bằng pip / Hugging Face như bản cài tay."""
        return self._manifest is None and not self.paths.manifest.exists()

    @property
    def manifest(self) -> dict:
        if self._manifest is None:
            self._manifest = load_manifest(self.paths)
        return self._manifest

    # --------------------------------------------------------------- state ---
    def state(self) -> dict:
        try:
            return json.loads(self.paths.state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, key: str, value: Optional[dict]) -> None:
        st = self.state()
        if value is None:
            st.pop(key, None)
        else:
            st[key] = {**value, "installed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = self.paths.state.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.paths.state)

    def installed(self, key: str) -> bool:
        if self.system:
            import importlib.util

            if key == "torch":
                return importlib.util.find_spec("torch") is not None
            if key in ONNX_FILE_KINDS:
                return importlib.util.find_spec("onnxruntime") is not None
            return True
        st = self.state().get(key)
        if not st:
            return False
        if key == "torch":
            return (self.paths.packages / "torch" / "__init__.py").exists()
        if key in FILE_MODELS:
            return self._file_path(key).exists()
        if key in ONNX_FILE_KINDS:
            path = Path(st.get("path") or "")
            return bool(path.name) and path.exists()
        folder = self._hf_folder(key)
        return all((folder / f).exists() for f in st.get("files", [])) and bool(st.get("files"))

    def onnx_path(self, kind: str) -> Optional[Path]:
        """0.9.7: đường dẫn file .onnx đã chọn cho 'onnx2d' / 'onnx3d' (None nếu chưa chọn hoặc file mất)."""
        path = Path((self.state().get(kind) or {}).get("path") or "")
        return path if path.name and path.exists() else None

    def use_onnx(self, kind: str, path, choice: Optional[str] = None) -> dict:
        """Ghi nhận model ONNX người dùng đã chọn (không tải, không kiểm tra giấy phép thay bạn)."""
        if kind not in ONNX_FILE_KINDS:
            raise ValueError(f"loại model không hỗ trợ: {kind}")
        p = Path(path).expanduser()
        if not p.exists():
            raise D.DownloadError(f"Không thấy file ONNX: {p}")
        out = {"path": str(p), "choice": choice or (ONNX_MODELS.get(kind) or {}).get("default"),
               "license": ((ONNX_MODELS.get(kind) or {}).get("choices", {}).get(choice or "", {}).get("license") or "chưa rõ")}
        self._save(kind, out)
        return out

    def status(self) -> dict:
        st = self.state()
        out = {}
        for key, meta in COMPONENTS.items():
            out[key] = {**meta, "installed": self.installed(key), **{k: v for k, v in (st.get(key) or {}).items() if k != "files"}}
        out["ready"] = out["torch"]["installed"] and out["sam3"]["installed"]
        out["system"] = self.system
        return out

    # -------------------------------------------------------------- plans ---
    def torch_wheels(self, gpu: Gpu) -> tuple[str, list[dict]]:
        t = self.manifest["torch"]
        variant = "cuda" if gpu.ok else "cpu"
        return variant, t[variant]

    @staticmethod
    def _base(version: str) -> str:
        return (version or "").split("+")[0]

    def caches(self, repo: str) -> list[Path]:
        return R.repo_cache(repo, self._hf_caches)

    def tool_dirs(self, refresh: bool = False) -> list[Path]:
        if self._tool_dirs is None or refresh:
            self._tool_dirs = R.find_tool_dirs(self.extra_dirs, self._tool_roots)
        return self._tool_dirs

    def plan(self, keys: list[str], gpu: Gpu, token: str = "", check_hash: bool = False) -> dict:
        if self.system:
            return {k: {"action": "none" if self.installed(k) else "install", "download": 0, "reuse": 0, "required": k == "torch",
                        "reason": "chạy từ mã nguồn: tự cài bằng pip (xem README)" if not self.installed(k) else "dùng bản cài trên máy"} for k in keys}
        """Việc cần làm cho từng thành phần: none (đã có, đúng bản), install (chưa có), update (có nhưng cần bản khác).
        Kèm số byte phải tải và số byte dùng lại được từ file có sẵn trên máy.
        check_hash=False: chỉ so dung lượng (nhanh); khi cài thật mọi file đều được kiểm mã băm."""
        out = {}
        for key in keys:
            try:
                kind = "hf" if key in HF_MODELS else ("file" if key in FILE_MODELS else key)
                out[key] = getattr(self, f"_plan_{kind}")(key, gpu, token, check_hash)
            except Exception as exc:  # noqa: BLE001 - không kiểm được thì báo, không chặn
                out[key] = {"action": "install" if not self.installed(key) else "none", "download": 0, "reuse": 0,
                            "reason": f"Chưa kiểm được: {exc}", "required": not self.installed(key)}
        return out

    def _plan_torch(self, key, gpu, token, check_hash) -> dict:
        variant, wheels = self.torch_wheels(gpu)
        size = sum(w.get("size") or 0 for w in wheels)
        st = self.state().get("torch") or {}
        want = self._base(next(w["version"] for w in wheels if w["name"] == "torch"))
        have = self._base((st.get("versions") or {}).get("torch", ""))
        if not self.installed("torch"):
            return {"action": "install", "download": size, "reuse": 0, "required": True,
                    "reason": f"PyTorch {want} ({'CUDA' if variant == 'cuda' else 'CPU'})"}
        if have != want:
            return {"action": "update", "download": size, "reuse": 0, "required": True,
                    "reason": f"Bản app này dùng PyTorch {want}, máy đang có {have}"}
        if st.get("variant") == "cpu" and variant == "cuda":
            return {"action": "update", "download": size, "reuse": 0, "required": False,
                    "reason": "Máy đã có card NVIDIA: nên đổi sang PyTorch bản CUDA (nhanh hơn nhiều)"}
        return {"action": "none", "download": 0, "reuse": 0, "required": False, "reason": f"PyTorch {have}: đúng bản"}

    def _face_sources(self) -> list[Path]:
        return [d / "models" for d in self.tool_dirs()] + [self.paths.models]

    def _file_path(self, key: str) -> Path:
        return {"face": self.paths.face, "wholebody": self.paths.wholebody}.get(key, self.paths.detector)

    def _plan_file(self, key, gpu, token, check_hash) -> dict:
        f = (self.manifest.get("models") or {}).get(key)
        if not f:
            return {"action": "none", "download": 0, "reuse": 0, "required": False, "reason": "bản cài này chưa có thành phần này"}
        st = self.state().get(key) or {}
        if self.installed(key) and st.get("sha256") == f["sha256"]:
            return {"action": "none", "download": 0, "reuse": 0, "required": False, "reason": "đúng bản"}
        item = {"path": self._file_path(key).name, "sha256": f["sha256"], "size": f.get("size")}
        src = R.find_match(item, self._face_sources(), [], check_hash)
        action = "update" if self.installed(key) else "install"
        if src:
            return {"action": action, "download": 0, "reuse": f.get("size") or 0, "required": action == "update",
                    "reason": f"dùng lại file có sẵn: {src}", "reuse_from": [str(src.parent)]}
        return {"action": action, "download": f.get("size") or 4_000_000, "reuse": 0, "required": action == "update",
                "reason": ("model khuôn mặt bản mới" if key == "face" else "bản mới") if action == "update" else ""}

    _plan_face = _plan_file

    def _hf_folder(self, key: str) -> Path:
        return {"sam3": self.paths.sam3, "reid": self.paths.reid, "depth": self.paths.depth}.get(key, self.paths.vitpose)

    def _plan_hf(self, key, gpu, token, check_hash) -> dict:
        m = self.manifest["models"][key]
        installed = self.installed(key)
        try:
            commit, files = self.hf_files(key, token)
        except Exception:  # noqa: BLE001 - offline hoặc chưa có quyền xem danh sách file
            if installed:
                return {"action": "none", "download": 0, "reuse": 0, "required": False, "reason": "không kiểm được bản mới (offline)"}
            return {"action": "install", "download": m.get("approx_size", 0), "reuse": 0, "required": key == "sam3", "reason": ""}
        caches = self.caches(m["repo"])
        folder = self._hf_folder(key)
        download = reuse = 0
        reused_from = set()
        for f in files:
            src = R.find_match(f, [folder], caches, check_hash)
            if src:
                reuse += f.get("size") or 0
                if folder not in src.parents:
                    reused_from.add(str(next((c for c in caches if c in src.parents), src.parent)))
            else:
                download += f.get("size") or 0
        st = self.state().get(key) or {}
        if installed and download == 0:
            return {"action": "none", "download": 0, "reuse": 0, "required": False, "commit": commit,
                    "reason": "đúng bản mới nhất" if st.get("commit") == commit else "các file không đổi"}
        action = "update" if installed else "install"
        reason = ("có bản trọng số mới trên Hugging Face: chỉ tải các file đã đổi" if action == "update"
                  else ("dùng lại file đã tải ở bản cũ" if reuse else ""))
        return {"action": action, "download": download, "reuse": reuse, "commit": commit, "reason": reason,
                "required": action == "install" and key == "sam3", "reuse_from": sorted(reused_from),
                "needs_token": bool(m.get("gated")) and download > 0}

    def estimate(self, keys: list[str], gpu: Gpu, token: str = "") -> dict:
        """Dung lượng tải và dung lượng cần trên ổ đĩa cho những thành phần thật sự phải làm."""
        plan = self.plan(keys, gpu, token)
        download = disk = 0
        for key, p in plan.items():
            if p["action"] == "none":
                continue
            download += p["download"]
            disk += int(p["download"] * (1 + TORCH_EXTRACT)) if key == "torch" else p["download"]
        free = shutil.disk_usage(self.paths.data).free
        return {"download": download, "disk": disk, "free": free, "enough": free > disk * 1.1 + 500_000_000,
                "detail": {k: p["download"] for k, p in plan.items()}, "plan": plan}

    def hf_files(self, key: str, token: str = "") -> tuple[str, list[dict]]:
        m = self.manifest["models"][key]
        return D.hf_files(m["repo"], token, m.get("revision", "main"), base=self.hf_base, allow_http=self.allow_http)

    # ------------------------------------------------------------ install ---
    def install(self, key: str, *, gpu: Gpu, token: str = "", progress: Optional[Callable] = None, cancel=None) -> dict:
        progress = progress or (lambda **_: None)
        if key == "torch":
            return self._install_torch(gpu, progress, cancel)
        if key in FILE_MODELS:
            return self._install_file(key, progress, cancel)
        return self._install_hf(key, token, progress, cancel)

    def _install_torch(self, gpu: Gpu, progress, cancel) -> dict:
        variant, wheels = self.torch_wheels(gpu)
        files = []
        for i, w in enumerate(wheels):
            dest = self.paths.downloads / w["filename"]
            D.download(w["url"], dest, sha256=w["sha256"], size=w.get("size"), cancel=cancel, allow_http=self.allow_http,
                       progress=lambda done, total, n=w["filename"], i=i: progress(file=n, done=done, total=total,
                                                                                  step=f"Tải {n} ({i + 1}/{len(wheels)})"))
            files.append(dest)
        progress(file="", done=0, total=None, step="Đang cài PyTorch (vài phút)...")
        staging = self.paths.data / "packages.new"
        shutil.rmtree(staging, ignore_errors=True)
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe" and python.with_name("python.exe").exists():
            python = python.with_name("python.exe")
        cmd = [str(python), "-I", "-X", "utf8", "-m", "pip", "install", "--no-deps", "--no-index", "--no-cache-dir",
               "--disable-pip-version-check", "--target", str(staging)] + [str(f) for f in files]
        res = subprocess.run(cmd, capture_output=True, text=True, **_no_window())
        if res.returncode != 0:
            shutil.rmtree(staging, ignore_errors=True)
            raise D.DownloadError("Cài PyTorch thất bại:\n" + (res.stderr or res.stdout)[-1500:])
        old = self.paths.data / "packages.old"
        shutil.rmtree(old, ignore_errors=True)
        if self.paths.packages.exists():
            self.paths.packages.rename(old)
        staging.rename(self.paths.packages)
        shutil.rmtree(old, ignore_errors=True)
        for f in files:                       # đỡ tốn ổ đĩa: xoá file tải về sau khi cài xong
            f.unlink(missing_ok=True)
        info = {"variant": variant, "versions": {w["name"]: w["version"] for w in wheels}}
        self._save("torch", info)
        return info

    def _install_file(self, key: str, progress, cancel) -> dict:
        f = (self.manifest.get("models") or {}).get(key)
        if not f:
            raise D.DownloadError(f"Bản cài này chưa có thành phần {COMPONENTS[key]['title']}: cập nhật app lên bản mới.")
        dest = self._file_path(key)
        what = {"face": "model khuôn mặt", "wholebody": "model pose toàn thân"}.get(key, "trọng số khởi đầu")
        if f.get("zip_member") is not None:      # 0.9.6: model nằm trong file zip (RTMW của OpenMMLab)
            return self._install_zip(key, f, dest, what, progress, cancel)
        item = {"path": dest.name, "sha256": f["sha256"], "size": f.get("size")}
        src = R.find_match(item, [d / "models" for d in self.tool_dirs()], [])
        if src and src != dest.resolve():
            progress(file=src.name, done=0, total=None, step=f"Dùng lại {what} có sẵn: {src}")
            R.place(src, dest)
            info = {"sha256": f["sha256"], "reused_from": str(src)}
            self._save(key, info)
            return info
        D.download(f["url"], dest, sha256=f["sha256"], size=f.get("size"), cancel=cancel, allow_http=self.allow_http,
                   progress=lambda done, total: progress(file=dest.name, done=done, total=total, step=f"Tải {what}"))
        info = {"sha256": f["sha256"]}
        self._save(key, info)
        return info

    def _install_zip(self, key: str, f: dict, dest: Path, what: str, progress, cancel) -> dict:
        import zipfile

        archive = self.paths.downloads / (f.get("filename") or f"{key}.zip")
        D.download(f["url"], archive, sha256=f["sha256"], size=f.get("size"), cancel=cancel, allow_http=self.allow_http,
                   progress=lambda done, total: progress(file=archive.name, done=done, total=total, step=f"Tải {what}"))
        progress(file="", done=0, total=None, step=f"Giải nén {what}")
        with zipfile.ZipFile(archive) as z:
            member = f["zip_member"] or next(n for n in z.namelist() if n.endswith(".onnx"))
            tmp = dest.with_suffix(".part")
            with z.open(member) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
        tmp.replace(dest)
        archive.unlink(missing_ok=True)
        info = {"sha256": f["sha256"], "member": member}
        self._save(key, info)
        return info

    def _install_face(self, progress, cancel) -> dict:
        return self._install_file("face", progress, cancel)

    def _install_hf(self, key: str, token: str, progress, cancel) -> dict:
        m = self.manifest["models"][key]
        commit, files = self.hf_files(key, token)
        final = self._hf_folder(key)
        caches = self.caches(m["repo"])
        staging = final.with_name(final.name + ".download")      # giữ lại giữa các lần để tải tiếp
        marker = staging / ".commit"
        if staging.exists() and (not marker.exists() or marker.read_text().strip() != commit):
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        marker.write_text(commit)
        access_checked = not m.get("gated")
        reused = 0
        for i, f in enumerate(files):
            dest = staging / f["path"]
            if dest.exists():
                try:
                    D.verify(dest, f.get("sha256", ""), f.get("git_blob", ""), f.get("size"))
                    continue
                except D.DownloadError:
                    dest.unlink()
            progress(file=f["path"], done=0, total=None, step=f"Kiểm file có sẵn trên máy: {f['path']}")
            src = R.find_match(f, [final], caches)            # kiểm cả mã băm trước khi dùng lại
            if src:
                R.place(src, dest)
                reused += 1
                continue
            if not access_checked:                             # chỉ cần token khi thật sự phải tải
                acc = D.hf_check_access(m["repo"], token, base=self.hf_base, allow_http=self.allow_http)
                if not acc["ok"]:
                    raise D.DownloadError(acc["message"])
                access_checked = True
            D.download(f["url"], dest, sha256=f.get("sha256", ""), git_blob=f.get("git_blob", ""), size=f.get("size"),
                       token=token, cancel=cancel, allow_http=self.allow_http,
                       progress=lambda done, total, n=f["path"], i=i: progress(
                           file=n, done=done, total=total, step=f"Tải {m['repo']}: {n} ({i + 1}/{len(files)})"))
        marker.unlink()
        if key == "sam3":
            shutil.copyfile(self.paths.sam_license, staging / "SAM_LICENSE.txt")
        shutil.rmtree(final, ignore_errors=True)
        staging.rename(final)
        info = {"repo": m["repo"], "commit": commit, "files": [f["path"] for f in files], "reused_files": reused}
        self._save(key, info)
        return info

    def remove(self, key: str) -> None:
        if key == "torch":
            shutil.rmtree(self.paths.packages, ignore_errors=True)
            self.paths.packages.mkdir(parents=True, exist_ok=True)
        elif key in FILE_MODELS:
            self._file_path(key).unlink(missing_ok=True)
        else:
            final = self._hf_folder(key)
            shutil.rmtree(final, ignore_errors=True)
            shutil.rmtree(final.with_name(final.name + ".download"), ignore_errors=True)
        self._save(key, None)

    def backend_env(self) -> dict:
        """Biến môi trường cho backend: chạy hoàn toàn offline, mọi model lấy từ thư mục dữ liệu."""
        p = self.paths
        if self.system:          # chạy từ mã nguồn: model lấy như bản cài tay (Hugging Face cache)
            return {"SAM3_PROFILES_DIR": str(p.profiles), "SAM3_LIBRARY_DIR": str(p.library), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        return {
            "SAM3_ENGINE": "transformers",
            "SAM3_HF_MODEL": str(p.sam3),
            "SAM3_POSE_MODEL": str(p.vitpose),
            "SAM3_FACE_MODEL": str(p.face),
            "SAM3_DETECTOR_BASE": str(p.detector),
            "SAM3_REID_MODEL": str(p.reid),
            "SAM3_DEPTH_MODEL": str(p.depth),
            "SAM3_WHOLEBODY_MODEL": str(p.wholebody),
            "SAM3_PROFILES_DIR": str(p.profiles),
            "SAM3_LIBRARY_DIR": str(p.library),
            "SAM3_NO_AUTO_DOWNLOAD": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HOME": str(p.data / "hf"),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }


def seed_user_files(paths: Paths) -> dict:
    """Chép profile và thư viện guideline có sẵn vào thư mục dữ liệu.
    File bạn đã sửa không bị ghi đè khi cập nhật; file có sẵn bạn chưa đụng tới thì được cập nhật."""
    record_path = paths.data / "seeded.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    copied = kept = 0
    for src_root, dst_root, skip in ((paths.app / "profiles", paths.profiles, ()),
                                     (paths.app / "guideline_library", paths.library, ("runs", "learning", "lessons.jsonl"))):
        if not src_root.exists():
            continue
        for src in src_root.rglob("*"):
            rel = src.relative_to(src_root)
            if src.is_dir() or rel.parts[0] in skip or "__pycache__" in rel.parts:
                continue
            dst = dst_root / rel
            key = f"{dst_root.name}/{rel.as_posix()}"
            new = D.sha256_file(src).hexdigest()
            if dst.exists():
                cur = D.sha256_file(dst).hexdigest()
                if cur == new:
                    record[key] = new
                    continue
                if record.get(key) != cur:        # người dùng đã sửa (hoặc tự tạo trùng tên): giữ nguyên
                    kept += 1
                    continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            record[key] = new
            copied += 1
    record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return {"copied": copied, "kept": kept}


def human(n: Optional[float]) -> str:
    if not n:
        return "0 MB"
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


__all__ = ["COMPONENTS", "ONNX_MODELS", "ONNX_FILE_KINDS", "COCO_FOR_3D", "onnx_models", "license_warning",
           "Components", "Gpu", "detect_gpu", "seed_user_files", "human", "load_manifest"]
