"""Tải file an toàn cho trình cài: chỉ HTTPS, tải tiếp khi rớt mạng, kiểm tra mã băm trước khi dùng.

Quy tắc an toàn:
  - Chỉ tải qua HTTPS (trừ địa chỉ thử nghiệm được cho phép rõ ràng trong test).
  - Mọi file đều phải có mã băm biết trước (sha256, hoặc mã blob git cho file nhỏ trên Hugging Face).
    File tải xong mà sai mã băm bị xoá, không bao giờ được dùng.
  - Token Hugging Face chỉ gửi tới huggingface.co. Khi máy chủ chuyển hướng sang máy khác (CDN), token bị bỏ.
  - File đang tải nằm ở `<tên>.part`, chỉ đổi tên thành file thật sau khi kiểm tra xong.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

CHUNK = 1 << 20
HF = "https://huggingface.co"
TOKEN_HOSTS = {"huggingface.co"}
USER_AGENT = "sam3-cvat-assist-desktop"


class DownloadError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # tự xử lý chuyển hướng để kiểm soát header
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _check_url(url: str, allow_http: tuple[str, ...] = ()) -> urllib.parse.SplitResult:
    u = urllib.parse.urlsplit(url)
    if u.scheme == "https" or (u.scheme == "http" and u.hostname in allow_http):
        return u
    raise DownloadError(f"Chỉ tải qua HTTPS, địa chỉ bị từ chối: {url}")


def open_url(url: str, *, method: str = "GET", headers: Optional[dict] = None, token: str = "",
             allow_http: tuple[str, ...] = (), timeout: float = 60, max_redirects: int = 8):
    """Mở URL, tự theo chuyển hướng. Token (Authorization) chỉ gửi cho máy chủ trong TOKEN_HOSTS."""
    for _ in range(max_redirects + 1):
        u = _check_url(url, allow_http)
        h = {"User-Agent": USER_AGENT, **(headers or {})}
        if token and u.hostname in TOKEN_HOSTS:
            h["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, method=method, headers=h)
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                url = urllib.parse.urljoin(url, exc.headers["Location"])
                exc.close()
                continue
            raise
        return resp
    raise DownloadError("Quá nhiều lần chuyển hướng.")


def sha256_file(path: Path, h=None) -> "hashlib._Hash":
    h = h or hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h


def git_blob_sha1(path: Path) -> str:
    size = path.stat().st_size
    h = hashlib.sha1(f"blob {size}\0".encode())
    return sha256_file(path, h).hexdigest()


def verify(path: Path, sha256: str = "", git_blob: str = "", size: Optional[int] = None) -> None:
    if not (sha256 or git_blob):
        raise DownloadError(f"Thiếu mã băm để kiểm tra {path.name}: không dùng file không kiểm tra được.")
    if size is not None and path.stat().st_size != size:
        raise DownloadError(f"{path.name}: dung lượng {path.stat().st_size} khác {size} byte.")
    got = sha256_file(path).hexdigest() if sha256 else git_blob_sha1(path)
    want = (sha256 or git_blob).lower()
    if got != want:
        raise DownloadError(f"{path.name}: mã băm không khớp (có thể file hỏng hoặc bị thay đổi). Đã xoá file.")


def download(url: str, dest: Path, *, sha256: str = "", git_blob: str = "", size: Optional[int] = None,
             token: str = "", progress: Optional[Callable[[int, Optional[int]], None]] = None,
             cancel=None, retries: int = 4, allow_http: tuple[str, ...] = (), backoff: float = 2.0) -> Path:
    """Tải `url` về `dest`. Có sẵn file đúng mã băm thì bỏ qua. Rớt mạng thì tải tiếp từ chỗ dừng."""
    dest = Path(dest)
    if not (sha256 or git_blob):
        raise DownloadError(f"Thiếu mã băm cho {dest.name}.")
    if dest.exists():
        try:
            verify(dest, sha256, git_blob, size)
            if progress:
                progress(dest.stat().st_size, dest.stat().st_size)
            return dest
        except DownloadError:
            dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(retries + 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            _fetch(url, part, size=size, token=token, progress=progress, cancel=cancel, allow_http=allow_http)
            try:
                verify(part, sha256, git_blob, size)
            except DownloadError:
                part.unlink(missing_ok=True)
                raise
            part.replace(dest)
            return dest
        except Cancelled:
            raise
        except DownloadError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                raise DownloadError(f"Máy chủ trả lỗi {exc.code} khi tải {dest.name}.") from exc
            last = exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    raise DownloadError(f"Không tải được {dest.name} sau {retries + 1} lần thử: {last}")


def _fetch(url, part: Path, *, size, token, progress, cancel, allow_http) -> None:
    have = part.stat().st_size if part.exists() else 0
    if size is not None and have > size:
        part.unlink()
        have = 0
    if size is not None and have == size:
        return
    headers = {"Range": f"bytes={have}-"} if have else {}
    try:
        resp = open_url(url, headers=headers, token=token, allow_http=allow_http)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have:           # đã đủ byte
            return
        raise
    with resp:
        if have and resp.status != 206:        # máy chủ không hỗ trợ tải tiếp: tải lại từ đầu
            have = 0
        total = size
        if total is None:
            length = resp.headers.get("Content-Length")
            total = have + int(length) if length and length.isdigit() else None
        with open(part, "ab" if have else "wb") as f:
            done = have
            if progress:
                progress(done, total)
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if size is not None and part.stat().st_size < size:
        raise OSError("Kết nối bị ngắt trước khi tải xong.")


def get_json(url: str, token: str = "", allow_http: tuple[str, ...] = ()) -> dict:
    with open_url(url, headers={"Accept": "application/json"}, token=token, allow_http=allow_http) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ------------------------------------------------------------ Hugging Face ---

class AccessResult(dict):
    """{'ok': bool, 'code': 'ok'|'token_invalid'|'not_approved'|'pending'|'network'|'error', 'message': str, 'user': str}"""


def hf_check_access(repo: str, token: str, base: str = HF, allow_http: tuple[str, ...] = ()) -> AccessResult:
    """Kiểm tra token hợp lệ và đã được duyệt vào repo (repo có cổng như facebook/sam3)."""
    token = (token or "").strip()
    if not token:
        return AccessResult(ok=False, code="token_invalid", message="Chưa nhập token.", user="")
    try:
        who = get_json(f"{base}/api/whoami-v2", token=token, allow_http=allow_http)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return AccessResult(ok=False, code="token_invalid", user="",
                                message="Token không hợp lệ hoặc đã bị xoá. Tạo token loại Read mới rồi dán lại.")
        return AccessResult(ok=False, code="error", user="", message=f"Hugging Face trả lỗi {exc.code}.")
    except (OSError, urllib.error.URLError) as exc:
        return AccessResult(ok=False, code="network", user="", message=f"Không kết nối được Hugging Face: {exc}")
    user = who.get("name", "")
    try:
        with open_url(f"{base}/{repo}/resolve/main/config.json", method="HEAD", token=token, allow_http=allow_http):
            pass
    except urllib.error.HTTPError as exc:
        text = (exc.headers.get("X-Error-Message") or "").lower()
        if exc.code in (401, 403):
            if "pending" in text or "awaiting" in text or "review" in text:
                return AccessResult(ok=False, code="pending", user=user,
                                    message=f"Tài khoản {user} đã gửi yêu cầu vào {repo} nhưng Meta chưa duyệt. Đợi email duyệt rồi thử lại.")
            return AccessResult(ok=False, code="not_approved", user=user,
                                message=f"Tài khoản {user} chưa được duyệt vào {repo}. Mở {HF}/{repo}, đăng nhập, điền thông tin "
                                        "và đồng ý điều khoản, đợi duyệt rồi thử lại.")
        return AccessResult(ok=False, code="error", user=user, message=f"Hugging Face trả lỗi {exc.code} khi kiểm tra quyền.")
    except (OSError, urllib.error.URLError) as exc:
        return AccessResult(ok=False, code="network", user=user, message=f"Không kết nối được Hugging Face: {exc}")
    return AccessResult(ok=True, code="ok", user=user, message=f"Token hợp lệ ({user}), đã được duyệt vào {repo}.")


WEIGHT_EXT = (".safetensors",)
CONFIG_EXT = (".json", ".txt", ".model", ".tiktoken", ".jinja")


def hf_select(siblings: list[dict]) -> list[dict]:
    """Chỉ giữ file mà transformers cần: cấu hình, tokenizer, trọng số .safetensors.
    Bỏ checkpoint trùng định dạng khác (.pt, .pth, .bin) để đỡ tốn ổ đĩa, bỏ ảnh minh hoạ. Giữ file giấy phép."""
    names = [s["rfilename"] for s in siblings]
    out = []
    for s in siblings:
        name = s["rfilename"]
        low = name.lower()
        if "/" in low:            # thư mục con: ảnh minh hoạ, checkpoint khác... transformers không cần
            continue
        if low.startswith("license") or low.endswith(WEIGHT_EXT + CONFIG_EXT):
            out.append(s)
    if not any(s["rfilename"].endswith(".safetensors") for s in out):
        raise DownloadError("Repo không có trọng số .safetensors, không dùng được với transformers. Có: " + ", ".join(names[:20]))
    return out


def hf_files(repo: str, token: str = "", revision: str = "main", base: str = HF,
             allow_http: tuple[str, ...] = ()) -> tuple[str, list[dict]]:
    """(commit, [{path, url, size, sha256|git_blob}]) của các file cần tải, ghim theo đúng commit."""
    info = get_json(f"{base}/api/models/{repo}/revision/{urllib.parse.quote(revision)}?blobs=true",
                    token=token, allow_http=allow_http)
    commit = info.get("sha") or revision
    files = []
    for s in hf_select(info.get("siblings") or []):
        lfs = s.get("lfs") or {}
        item = {"path": s["rfilename"], "size": lfs.get("size", s.get("size")),
                "url": f"{base}/{repo}/resolve/{commit}/{urllib.parse.quote(s['rfilename'])}"}
        if lfs.get("sha256"):
            item["sha256"] = lfs["sha256"]
        elif s.get("blobId"):
            item["git_blob"] = s["blobId"]
        else:
            raise DownloadError(f"Hugging Face không cho mã băm của {s['rfilename']}, không tải.")
        files.append(item)
    return commit, files
