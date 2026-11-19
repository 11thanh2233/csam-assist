"""Engine dùng SAM3 qua thư viện Hugging Face `transformers` (Sam3Model / Sam3Processor).

Dùng khi không muốn cài gói `sam3` chính thức. API theo tài liệu transformers:
    inputs = processor(images=image, text="car", return_tensors="pt")
    outputs = model(**inputs)
    processor.post_process_instance_segmentation(outputs, threshold, mask_threshold, target_sizes)
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import numpy as np

from .base import Engine, Instance, PromptError, resolve_device

log = logging.getLogger("sam3-assist")


class Sam3TransformersEngine(Engine):
    name = "transformers"

    def __init__(self, device: str = "auto", model_id: str = "facebook/sam3", min_conf: float = 0.25):
        self._requested_device = device
        self._model_id = model_id
        self._min_conf = min_conf
        self._lock = threading.Lock()
        self.model = None
        self.processor = None
        self.torch = None
        self._reuse_vision: Optional[bool] = None

    def load(self) -> None:
        import torch
        from transformers import Sam3Model, Sam3Processor

        self.torch = torch
        self.device = resolve_device(self._requested_device)
        self.model = Sam3Model.from_pretrained(self._model_id).to(self.device).eval()
        self.processor = Sam3Processor.from_pretrained(self._model_id)

    def encode(self, image_rgb: np.ndarray) -> Any:
        from PIL import Image

        image = Image.fromarray(image_rgb)
        with self._lock, self.torch.no_grad():
            img_inputs = self.processor(images=image, return_tensors="pt").to(self.device)
            vision_embeds = self.model.get_vision_features(pixel_values=img_inputs.pixel_values)
        return {
            "image": image,
            "vision_embeds": vision_embeds,
            "target_sizes": img_inputs.get("original_sizes").tolist(),
        }

    def _check_prompt(self, inputs: Any, prompt: str) -> None:
        """Bộ mã hoá chữ của SAM3 chỉ nhận prompt ngắn; báo lỗi rõ ràng thay vì để model sập."""
        limit = getattr(getattr(self.model.config, "text_config", None), "max_position_embeddings", None)
        ids = inputs.get("input_ids") if hasattr(inputs, "get") else None
        if limit and ids is not None and ids.shape[-1] > limit:
            raise PromptError(
                f"prompt quá dài cho SAM3 ({ids.shape[-1]} token, tối đa {limit}). "
                "Hãy dùng cụm danh từ tiếng Anh ngắn, ví dụ \"traffic sign\"."
            )

    def _post(self, outputs: Any, target_sizes: Any, threshold: float) -> list[Instance]:
        results = self.processor.post_process_instance_segmentation(
            outputs,
            threshold=min(threshold, max(self._min_conf, 0.05)),
            mask_threshold=0.5,
            target_sizes=target_sizes,
        )[0]
        instances = []
        for mask, score in zip(results["masks"], results["scores"]):
            score = float(score)
            if score >= threshold:
                instances.append(Instance(mask=mask.detach().cpu().numpy() > 0, score=score))
        return instances

    def segment_text(self, state: Any, prompt: str, threshold: float) -> list[Instance]:
        with self._lock, self.torch.no_grad():
            text_inputs = self.processor(text=prompt, return_tensors="pt").to(self.device)
            self._check_prompt(text_inputs, prompt)
            outputs = self.model(vision_embeds=state["vision_embeds"], **text_inputs)
            return self._post(outputs, state["target_sizes"], threshold)

    def segment_box(
        self, state: Any, box_xyxy: list[float], prompt: Optional[str], threshold: float,
        negatives: Optional[list[list[float]]] = None,
    ) -> list[Instance]:
        boxes = [[float(v) for v in box_xyxy]] + [[float(v) for v in b] for b in (negatives or [])]
        kwargs: dict[str, Any] = {
            "images": state["image"],
            "input_boxes": [boxes],
            "input_boxes_labels": [[1] + [0] * (len(boxes) - 1)],   # 1 = mẫu dương, 0 = mẫu âm (loại trừ)
            "return_tensors": "pt",
        }
        if prompt:
            kwargs["text"] = prompt
        with self._lock, self.torch.no_grad():
            inputs = self.processor(**kwargs).to(self.device)
            if prompt:
                self._check_prompt(inputs, prompt)
            if self._reuse_vision is not False and state.get("vision_embeds") is not None:
                # Dùng lại đặc trưng ảnh đã tính lúc encode: không chạy lại bộ mã hoá ảnh (phần nặng nhất) mỗi lần vẽ hộp.
                reuse = {k: v for k, v in inputs.items() if k not in ("pixel_values", "original_sizes", "reshaped_input_sizes")}
                try:
                    outputs = self.model(vision_embeds=state["vision_embeds"], **reuse)
                    self._reuse_vision = True
                    return self._post(outputs, inputs.get("original_sizes").tolist(), threshold)
                except (TypeError, ValueError, RuntimeError) as exc:   # bản transformers không hỗ trợ: quay về cách cũ
                    if self._reuse_vision:   # đã chạy được trước đó: lỗi thật, không nuốt
                        raise
                    self._reuse_vision = False
                    log.info("Không dùng lại được đặc trưng ảnh cho hộp mẫu (%s); chạy lại bộ mã hoá ảnh.", exc)
            outputs = self.model(**inputs)
            return self._post(outputs, inputs.get("original_sizes").tolist(), threshold)
