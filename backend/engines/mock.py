"""Engine giả lập: KHÔNG phải SAM3, chỉ để chạy thử giao diện và test khi chưa có GPU.

Sinh ra các vùng cố định theo từ khoá trong prompt (đường ở nửa dưới ảnh, trời ở trên,
vạch kẻ là các dải chéo, còn lại là một hình elip đặt theo mã băm của prompt).
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

import cv2
import numpy as np

from .base import Engine, Instance


class MockEngine(Engine):
    name = "mock"
    device = "cpu"

    def encode(self, image_rgb: np.ndarray) -> Any:
        return {"h": image_rgb.shape[0], "w": image_rgb.shape[1]}

    def segment_text(self, state: Any, prompt: str, threshold: float) -> list[Instance]:
        h, w = state["h"], state["w"]
        text = prompt.lower()
        canvas = np.zeros((h, w), np.uint8)
        if "sky" in text:
            canvas[: int(h * 0.3)] = 1
        elif any(k in text for k in ("road", "drivable", "street")) and "curb" not in text:
            pts = np.array(
                [[0, h - 1], [int(w * 0.38), int(h * 0.5)], [int(w * 0.62), int(h * 0.5)], [w - 1, h - 1]]
            )
            cv2.fillPoly(canvas, [pts], 1)
        elif any(k in text for k in ("lane", "line", "marking", "curb")):
            shift = (int(hashlib.sha1(text.encode()).hexdigest(), 16) % 5) - 2
            x_bottom = int(w * (0.5 + 0.11 * shift))
            x_top = int(w * (0.5 + 0.03 * shift))
            cv2.line(canvas, (x_bottom, h - 10), (x_top, int(h * 0.55)), 1, 7)
        elif "crosswalk" in text:
            cv2.rectangle(canvas, (int(w * 0.3), int(h * 0.62)), (int(w * 0.7), int(h * 0.68)), 1, -1)
        else:
            seed = int(hashlib.sha1(text.encode()).hexdigest(), 16)
            cx = int(w * (0.15 + (seed % 70) / 100))
            cy = int(h * (0.45 + ((seed >> 8) % 30) / 100))
            cv2.ellipse(canvas, (cx, cy), (int(w * 0.05), int(h * 0.07)), 0, 0, 360, 1, -1)
        score = 0.9
        return [Instance(mask=canvas.astype(bool), score=score)] if score >= threshold else []

    def segment_box(
        self, state: Any, box_xyxy: list[float], prompt: Optional[str], threshold: float
    ) -> list[Instance]:
        h, w = state["h"], state["w"]
        x1, y1, x2, y2 = [int(round(v)) for v in box_xyxy]
        canvas = np.zeros((h, w), np.uint8)
        center = ((x1 + x2) // 2, (y1 + y2) // 2)
        axes = (max(1, (x2 - x1) // 2 - 1), max(1, (y2 - y1) // 2 - 1))
        cv2.ellipse(canvas, center, axes, 0, 0, 360, 1, -1)
        return [Instance(mask=canvas.astype(bool), score=0.95)]
