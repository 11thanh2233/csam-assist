"""Tìm trên máy những file model đã có sẵn để khỏi tải lại.

Nguồn được quét:
  - bộ nhớ đệm Hugging Face (bản cài tay 0.3.x - 0.5.x tải SAM 3, ViTPose vào đây): HF_HUB_CACHE, HF_HOME\\hub,
    %USERPROFILE%\\.cache\\huggingface\\hub;
  - thư mục tool cũ (sam3-cvat-assist, csam-assist) trong Downloads, Desktop, Documents, sâu tối đa 4 cấp,
    và các thư mục bạn tự thêm: lấy models\\face_landmarker.task;
  - thư mục model hiện tại của app (khi cập nhật trọng số: file không đổi thì giữ, chỉ tải file đổi).

Một file chỉ được dùng lại khi đúng dung lượng VÀ đúng mã băm mà Hugging Face (hoặc manifest) công bố.
Dùng lại bằng liên kết cứng (không tốn thêm ổ đĩa) nếu cùng ổ, không được thì chép.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Optional

from . import downloader as D

TOOL_DIR_NAMES = {"sam3-cvat-assist", "csam-assist"}
SEARCH_DEPTH = 4


def hf_cache_dirs() -> list[Path]:
    dirs = []
    for env in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.getenv(env):
            dirs.append(Path(os.environ[env]))
    if os.getenv("HF_HOME"):
        dirs.append(Path(os.environ["HF_HOME"]) / "hub")
    dirs.append(Path.home() / ".cache" / "huggingface" / "hub")
    seen, out = set(), []
    for d in dirs:
        try:
            key = d.resolve()
        except OSError:
            continue
        if key not in seen and d.is_dir():
            seen.add(key)
            out.append(d)
    return out


def repo_cache(repo: str, caches: Optional[Iterable[Path]] = None) -> list[Path]:
    """Thư mục models--<org>--<name> của repo trong các bộ nhớ đệm."""
    name = "models--" + repo.replace("/", "--")
    return [c / name for c in (caches if caches is not None else hf_cache_dirs()) if (c / name).is_dir()]


def find_tool_dirs(extra: Iterable[Path] = (), roots: Optional[Iterable[Path]] = None) -> list[Path]:
    """Thư mục tool cũ: tên quen thuộc trong Downloads/Desktop/Documents, cộng thư mục bạn tự thêm."""
    found = [Path(p) for p in extra if Path(p).is_dir()]
    home = Path.home()
    for root in (roots if roots is not None else [home / "Downloads", home / "Desktop", home / "Documents"]):
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for cur, dirs, _ in os.walk(root):
            depth = len(Path(cur).parts) - base_depth
            if depth >= SEARCH_DEPTH:
                dirs[:] = []
            hit = [d for d in dirs if d.lower() in TOOL_DIR_NAMES]
            found += [Path(cur) / d for d in hit]
            dirs[:] = [d for d in dirs if not d.startswith((".", "node_modules", "$")) and d not in hit]
    seen, out = set(), []
    for d in found:
        if d.resolve() not in seen:
            seen.add(d.resolve())
            out.append(d)
    return out


def _candidates(f: dict, sources: list[Path], repo_caches: list[Path]) -> list[Path]:
    """Các file trên máy có thể là `f` (theo tên hoặc theo mã băm trong bộ nhớ đệm HF)."""
    out = []
    for cache in repo_caches:
        h = f.get("sha256") or f.get("git_blob")
        if h:
            out.append(cache / "blobs" / h)
        snaps = cache / "snapshots"
        if snaps.is_dir():
            out += [s / f["path"] for s in sorted(snaps.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)]
    out += [s / f["path"] for s in sources]
    return out


def find_match(f: dict, sources: list[Path], repo_caches: list[Path], check_hash: bool = True) -> Optional[Path]:
    """File trên máy đúng dung lượng (và đúng mã băm nếu check_hash) với `f`, hoặc None."""
    for cand in _candidates(f, sources, repo_caches):
        try:
            real = cand.resolve()
            if not real.is_file() or (f.get("size") is not None and real.stat().st_size != f["size"]):
                continue
            if check_hash:
                D.verify(real, f.get("sha256", ""), f.get("git_blob", ""), f.get("size"))
            return real
        except (OSError, D.DownloadError):
            continue
    return None


def place(src: Path, dest: Path) -> str:
    """Đặt file đã kiểm vào chỗ mới: liên kết cứng nếu được (không tốn thêm ổ đĩa), không thì chép."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        os.link(src, dest)
        return "link"
    except OSError:
        shutil.copyfile(src, dest)
        return "copy"
