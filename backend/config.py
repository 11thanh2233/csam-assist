"""Cấu hình backend, đọc từ biến môi trường hoặc file .env ở gốc repo."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Đọc file .env đơn giản (KEY=VALUE), không ghi đè biến đã có sẵn."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


class Settings:
    def __init__(self) -> None:
        # auto | sam3 | transformers | mock
        self.engine = os.getenv("SAM3_ENGINE", "auto").lower()
        # auto | cuda | cpu | mps
        self.device = os.getenv("SAM3_DEVICE", "auto").lower()
        self.host = os.getenv("SAM3_HOST", "127.0.0.1")
        self.port = int(os.getenv("SAM3_PORT", "8765"))
        # Nếu đặt, mọi request (trừ /health và trang cài đặt) phải có header X-API-Key.
        self.api_key = os.getenv("SAM3_API_KEY", "")
        # Tên Host được nhận. Mặc định: chỉ localhost khi backend nghe trên máy này; mở ra mạng (0.0.0.0, Docker) thì nhận mọi tên.
        hosts = os.getenv("SAM3_ALLOWED_HOSTS", "")
        if hosts:
            self.allowed_hosts = [h.strip().lower() for h in hosts.split(",") if h.strip()]
        elif self.host in ("127.0.0.1", "localhost", "::1"):
            self.allowed_hosts = ["127.0.0.1", "localhost", "::1"]
        else:
            self.allowed_hosts = ["*"]
        self.hf_model = os.getenv("SAM3_HF_MODEL", "facebook/sam3")
        # Kiểu số của SAM3 Video (theo dõi thử nghiệm): auto = float16 trên GPU, float32 trên CPU.
        self.video_dtype = os.getenv("SAM3_VIDEO_DTYPE", "auto")
        # Đường dẫn checkpoint cục bộ (tuỳ chọn, chỉ dùng cho engine "sam3").
        self.checkpoint = os.getenv("SAM3_CHECKPOINT", "")
        # Ngưỡng tin cậy thấp nhất mà model trả về; từng label lọc tiếp theo ngưỡng riêng.
        self.min_conf = float(os.getenv("SAM3_MIN_CONF", "0.25"))
        self.cache_size = int(os.getenv("SAM3_CACHE_SIZE", "4"))
        self.profiles_dir = Path(os.getenv("SAM3_PROFILES_DIR", str(ROOT / "profiles")))
        self.userscript_path = ROOT / "userscript" / "sam3-cvat-assist.user.js"
        # Thư viện guideline: guideline mẫu, bài học đã tích luỹ, các lượt soạn nháp.
        self.library_dir = Path(os.getenv("SAM3_LIBRARY_DIR") or str(ROOT / "guideline_library"))
        # Model phụ cho skeleton (chỉ nạp khi job có label skeleton).
        self.pose_model = os.getenv("SAM3_POSE_MODEL", "usyd-community/vitpose-base-simple")
        self.face_model = Path(os.getenv("SAM3_FACE_MODEL") or str(ROOT / "models" / "face_landmarker.task"))


settings = Settings()
