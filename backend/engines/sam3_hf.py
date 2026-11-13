"""Engine dùng SAM3 qua thư viện Hugging Face `transformers` (Sam3Model / Sam3Processor).

Dùng khi không muốn cài gói `sam3` chính thức. API theo tài liệu transformers:
    inputs = processor(images=image, text="car", return_tensors="pt")
    outputs = model(**inputs)
    processor.post_process_instance_segmentation(outputs, threshold, mask_threshold, target_sizes)
"""
from __future__ import annotations

import threading
from typing import Any, Optional

import numpy as np

from .base import Engine, Instance, resolve_device


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
            outputs = self.model(vision_embeds=state["vision_embeds"], **text_inputs)
            return self._post(outputs, state["target_sizes"], threshold)

    def segment_box(
        self, state: Any, box_xyxy: list[float], prompt: Optional[str], threshold: float
    ) -> list[Instance]:
        kwargs: dict[str, Any] = {
            "images": state["image"],
            "input_boxes": [[[float(v) for v in box_xyxy]]],
            "input_boxes_labels": [[1]],
            "return_tensors": "pt",
        }
        if prompt:
            kwargs["text"] = prompt
        with self._lock, self.torch.no_grad():
            inputs = self.processor(**kwargs).to(self.device)
            outputs = self.model(**inputs)
            return self._post(outputs, inputs.get("original_sizes").tolist(), threshold)
