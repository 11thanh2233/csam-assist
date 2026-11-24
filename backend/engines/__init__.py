"""Chọn và nạp engine theo cấu hình."""
from __future__ import annotations

import logging

from ..config import Settings
from .base import Engine, Instance

log = logging.getLogger("sam3-assist")

__all__ = ["Engine", "Instance", "create_engine"]


def create_engine(settings: Settings) -> Engine:
    choice = settings.engine
    errors: list[str] = []

    if choice == "mock":
        from .mock import MockEngine

        engine: Engine = MockEngine()
        engine.load()
        log.warning("Đang chạy engine GIẢ LẬP (mock). Kết quả không phải của SAM3.")
        return engine

    if choice in ("auto", "sam3"):
        try:
            from .sam3_official import Sam3OfficialEngine

            engine = Sam3OfficialEngine(settings.device, settings.checkpoint, settings.min_conf)
            engine.load()
            return engine
        except Exception as exc:  # noqa: BLE001 - báo lỗi gộp ở cuối
            errors.append(f"sam3: {type(exc).__name__}: {exc}")
            if choice == "sam3":
                raise

    if choice in ("auto", "transformers"):
        try:
            from .sam3_hf import Sam3TransformersEngine

            engine = Sam3TransformersEngine(settings.device, settings.hf_model, settings.min_conf, settings.dtype)
            engine.load()
            return engine
        except Exception as exc:  # noqa: BLE001
            errors.append(f"transformers: {type(exc).__name__}: {exc}")
            if choice == "transformers":
                raise

    raise RuntimeError(
        "Không nạp được SAM3. Chi tiết:\n  - "
        + "\n  - ".join(errors or [f"SAM3_ENGINE='{choice}' không hợp lệ"])
        + "\nXem README mục 'Cài SAM3'. Muốn chạy thử giao diện không cần model: SAM3_ENGINE=mock"
    )
