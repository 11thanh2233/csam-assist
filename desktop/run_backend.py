"""Điểm chạy backend trong bản desktop: thêm thư mục PyTorch đã tải vào đường dẫn rồi chạy uvicorn."""
import os
import sys

pkgs = os.environ.get("SAM3_DESKTOP_PACKAGES")
if pkgs and os.path.isdir(pkgs):
    sys.path.insert(0, pkgs)

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("backend.app:app", host="127.0.0.1", port=int(os.environ.get("SAM3_PORT", "8765")), log_level="info")
