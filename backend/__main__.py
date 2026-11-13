"""Điểm khởi động: `python -m backend`."""
import uvicorn

from .config import settings

if __name__ == "__main__":
    uvicorn.run("backend.app:app", host=settings.host, port=settings.port, log_level="info")
