"""Giao diện chung cho mọi engine phân đoạn."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


@dataclass
class Instance:
    """Một đối tượng model tìm được."""

    mask: np.ndarray  # bool, HxW, cùng kích thước ảnh gốc
    score: float


class PromptError(ValueError):
    """Prompt không dùng được với model (ví dụ quá dài)."""


class Engine:
    name = "base"
    device = "cpu"

    def load(self) -> None:
        """Nạp model vào bộ nhớ. Gọi một lần khi server khởi động."""

    def encode(self, image_rgb: np.ndarray) -> Any:
        """Tính đặc trưng ảnh một lần; kết quả dùng lại cho nhiều prompt."""
        raise NotImplementedError

    def segment_text(self, state: Any, prompt: str, threshold: float) -> list[Instance]:
        """Tìm mọi đối tượng khớp với mô tả chữ."""
        raise NotImplementedError

    def segment_box(
        self, state: Any, box_xyxy: list[float], prompt: Optional[str], threshold: float,
        negatives: Optional[list[list[float]]] = None,
    ) -> list[Instance]:
        """Dùng một hộp làm ví dụ mẫu: trả về đối tượng trong hộp và các đối tượng tương tự.
        `negatives`: các hộp mẫu ÂM (vật SAM3 nhận nhầm): SAM3 bỏ những vật giống chúng."""
        raise NotImplementedError


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"
