"""Engine dùng gói `sam3` chính thức của Meta (github.com/facebookresearch/sam3).

API dùng ở đây theo README của repo:
    model = build_sam3_image_model()
    processor = Sam3Processor(model, confidence_threshold=...)
    state = processor.set_image(pil_image)
    state = processor.set_text_prompt(state=state, prompt="car")
    state["masks"], state["boxes"], state["scores"]
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Optional

import numpy as np

from .base import Engine, Instance, resolve_device


class Sam3OfficialEngine(Engine):
    name = "sam3"

    def __init__(self, device: str = "auto", checkpoint: str = "", min_conf: float = 0.25):
        self._requested_device = device
        self._checkpoint = checkpoint
        self._min_conf = min_conf
        self._lock = threading.Lock()
        self.processor = None
        self.torch = None

    def load(self) -> None:
        import torch
        from sam3.model.sam3_image_processor import Sam3Processor
        from sam3.model_builder import build_sam3_image_model

        self.torch = torch
        self.device = resolve_device(self._requested_device)
        if self.device == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True

        kwargs: dict[str, Any] = {"device": self.device}
        if self._checkpoint:
            kwargs.update(checkpoint_path=self._checkpoint, load_from_HF=False)
        try:
            model = build_sam3_image_model(**kwargs)
        except TypeError:
            # Phiên bản sam3 khác chữ ký hàm: quay về mặc định (tự tải checkpoint từ Hugging Face).
            model = build_sam3_image_model()
        try:
            self.processor = Sam3Processor(
                model, device=self.device, confidence_threshold=self._min_conf
            )
        except TypeError:
            self.processor = Sam3Processor(model, confidence_threshold=self._min_conf)

    @contextmanager
    def _ctx(self):
        torch = self.torch
        with torch.inference_mode():
            if self.device == "cuda":
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    yield
            else:
                yield

    def encode(self, image_rgb: np.ndarray) -> Any:
        from PIL import Image

        with self._lock, self._ctx():
            state = self.processor.set_image(Image.fromarray(image_rgb))
        state["_assist_hw"] = image_rgb.shape[:2]
        return state

    def _collect(self, out: dict, threshold: float) -> list[Instance]:
        masks, scores = out.get("masks"), out.get("scores")
        if masks is None or scores is None or len(scores) == 0:
            return []
        masks_np = masks.detach().float().cpu().numpy()
        scores_np = scores.detach().float().cpu().numpy().reshape(-1)
        if masks_np.ndim == 4:  # (N, 1, H, W)
            masks_np = masks_np[:, 0]
        result = []
        for mask, score in zip(masks_np, scores_np):
            if float(score) >= threshold:
                result.append(Instance(mask=mask > 0.5, score=float(score)))
        return result

    def segment_text(self, state: Any, prompt: str, threshold: float) -> list[Instance]:
        with self._lock, self._ctx():
            self.processor.reset_all_prompts(state)
            out = self.processor.set_text_prompt(state=state, prompt=prompt)
            return self._collect(out, threshold)

    def segment_box(
        self, state: Any, box_xyxy: list[float], prompt: Optional[str], threshold: float
    ) -> list[Instance]:
        height, width = state["_assist_hw"]
        x1, y1, x2, y2 = box_xyxy
        # Sam3Processor nhận hộp dạng [cx, cy, w, h] đã chuẩn hoá về 0..1.
        norm = [
            (x1 + x2) / 2 / width,
            (y1 + y2) / 2 / height,
            (x2 - x1) / width,
            (y2 - y1) / height,
        ]
        with self._lock, self._ctx():
            self.processor.reset_all_prompts(state)
            if prompt:
                self.processor.set_text_prompt(state=state, prompt=prompt)
            out = self.processor.add_geometric_prompt(box=norm, label=True, state=state)
            return self._collect(out, threshold)
