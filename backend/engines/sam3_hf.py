"""Engine dùng SAM3 qua thư viện Hugging Face `transformers` (Sam3Model / Sam3Processor).

Dùng khi không muốn cài gói `sam3` chính thức. API theo tài liệu transformers:
    inputs = processor(images=image, text="car", return_tensors="pt")
    outputs = model(**inputs)
    processor.post_process_instance_segmentation(outputs, threshold, mask_threshold, target_sizes)
"""
from __future__ import annotations

import logging
import gc
import threading
import time
from typing import Any, Optional

import numpy as np

from .base import Engine, Instance, PromptError, resolve_device

log = logging.getLogger("sam3-assist")


class Sam3TransformersEngine(Engine):
    name = "transformers"

    def __init__(self, device: str = "auto", model_id: str = "facebook/sam3", min_conf: float = 0.25, dtype: str = "auto"):
        self._requested_dtype = (dtype or "auto").lower()
        self.dtype = None
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
        self.dtype = self._pick_dtype(torch)
        self.model = Sam3Model.from_pretrained(self._model_id, torch_dtype=self.dtype).to(self.device).eval()
        self.processor = Sam3Processor.from_pretrained(self._model_id)
        log.info("SAM3 dtype: %s", str(self.dtype).replace("torch.", ""))

    def _pick_dtype(self, torch):
        """0.9.5: trên GPU chạy bf16 (hoặc fp16 nếu card không có bf16): VRAM giảm khoảng một nửa so với fp32.
        Thiếu VRAM là lý do chính làm theo dõi cả job chậm hàng chục lần trên Windows (VRAM tràn sang RAM thường)."""
        want = self._requested_dtype
        if self.device != "cuda" or want in ("float32", "fp32"):
            return torch.float32
        if want in ("float16", "fp16"):
            return torch.float16
        if want in ("bfloat16", "bf16"):
            return torch.bfloat16
        try:
            return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        except Exception:  # noqa: BLE001
            return torch.float16

    def _half_failed(self, exc: Exception) -> bool:
        """Lỗi do nửa độ chính xác (bản transformers không hỗ trợ): nạp lại fp32 một lần, chạy tiếp."""
        msg = str(exc).lower()
        if self.dtype in (None, self.torch.float32) or not any(k in msg for k in ("dtype", "half", "bfloat16", "scalar type", "same type")):
            return False
        log.warning("SAM3 %s lỗi (%s); chuyển về float32.", self.dtype, str(exc)[:200])
        self.model = self.model.to(self.torch.float32)
        self.dtype = self.torch.float32
        self._requested_dtype = "float32"
        return True

    def _cast(self, inputs):
        """Đưa mọi tensor số thực của đầu vào về đúng dtype của model."""
        for k, v in list(inputs.items()):
            if hasattr(v, "is_floating_point") and v.is_floating_point() and v.dtype != self.dtype:
                inputs[k] = v.to(self.dtype)
        return inputs

    # ------------------------------------------------ SAM 3 Tracker: bấm điểm ---
    TRACKER_IDLE_S = 300            # không bấm điểm 5 phút thì nhả model tracker khỏi VRAM
    TRACKER_MIN_FREE_MB = 2500      # còn ít VRAM hơn mức này thì không nạp, để tool quay về cách dựng hộp

    def _tracker(self):
        """Nạp SAM 3 Tracker (phần tương tác kiểu SAM 2, cùng checkpoint facebook/sam3) khi cần, float16 trên GPU."""
        if getattr(self, "_trk", None) is None:
            torch = self.torch
            if self.device == "cuda":
                free, _ = torch.cuda.mem_get_info()
                if free / 2**20 < self.TRACKER_MIN_FREE_MB:
                    raise MemoryError(f"Còn {free / 2**20:.0f} MB VRAM, chưa đủ để nạp SAM 3 Tracker.")
            from transformers import Sam3TrackerModel, Sam3TrackerProcessor

            dtype = torch.float16 if self.device == "cuda" else torch.float32
            self._trk_proc = Sam3TrackerProcessor.from_pretrained(self._model_id)
            self._trk = Sam3TrackerModel.from_pretrained(self._model_id, torch_dtype=dtype).to(self.device).eval()
            self._trk_dtype = dtype
            self._trk_cache = None
        self._trk_last = time.time()
        return self._trk, self._trk_proc

    def release_tracker(self) -> bool:
        if getattr(self, "_trk", None) is None:
            return False
        self._trk = self._trk_proc = self._trk_cache = None
        gc.collect()
        if self.torch is not None and self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()
        return True

    def release_idle(self) -> None:
        """Gọi định kỳ: nhả SAM 3 Tracker nếu lâu không bấm điểm."""
        if getattr(self, "_trk", None) is not None and time.time() - getattr(self, "_trk_last", 0) > self.TRACKER_IDLE_S:
            with self._lock:
                self.release_tracker()

    def segment_points(self, state: Any, positives: list, negatives: list, prompt: Any = None) -> list[Instance]:
        """Tách vật theo điểm bấm bằng SAM 3 Tracker. Thiếu VRAM hoặc lỗi: nhả tracker và báo lỗi để pipeline quay về cách cũ."""
        torch = self.torch
        image = state["image"]
        points = [[list(map(float, p)) for p in positives] + [list(map(float, p)) for p in negatives]]
        labels = [[1] * len(positives) + [0] * len(negatives)]
        with self._lock, torch.no_grad():
            try:
                model, proc = self._tracker()
                inputs = proc(images=image, input_points=[points], input_labels=[labels], return_tensors="pt").to(self.device)
                pixel = inputs.pop("pixel_values").to(self._trk_dtype)
                cache = self._trk_cache
                kwargs = {}
                if cache and cache[0] is state:
                    kwargs["image_embeddings"] = cache[1]          # cùng ảnh: không mã hoá lại, mỗi điểm chỉ chạy phần giải mã
                try:
                    out = model(pixel_values=pixel, multimask_output=False, **inputs, **kwargs)
                except (TypeError, ValueError):
                    out = model(pixel_values=pixel, multimask_output=False, **inputs)
                if getattr(out, "image_embeddings", None) is not None:
                    self._trk_cache = (state, out.image_embeddings)   # giữ chính state: không nhầm sang ảnh khác
                masks = proc.post_process_masks(out.pred_masks.float().cpu(), inputs["original_sizes"].cpu())[0]
                scores = out.iou_scores.float().cpu().reshape(-1).tolist()
            except Exception as exc:
                oom = isinstance(exc, MemoryError) or "out of memory" in str(exc).lower()
                self.release_tracker()
                raise RuntimeError(("thiếu VRAM cho SAM 3 Tracker" if oom else f"SAM 3 Tracker lỗi: {exc}")[:300]) from exc
        m = masks.reshape(-1, masks.shape[-2], masks.shape[-1]).numpy() > 0.5
        return [Instance(mask=m[i], score=float(scores[i] if i < len(scores) else 0.9)) for i in range(len(m)) if m[i].any()]

    def encode(self, image_rgb: np.ndarray) -> Any:
        from PIL import Image

        image = Image.fromarray(image_rgb)
        with self._lock, self.torch.no_grad():
            img_inputs = self.processor(images=image, return_tensors="pt").to(self.device)
            try:
                vision_embeds = self.model.get_vision_features(pixel_values=img_inputs.pixel_values.to(self.dtype or self.torch.float32))
            except RuntimeError as exc:
                if not self._half_failed(exc):
                    raise
                vision_embeds = self.model.get_vision_features(pixel_values=img_inputs.pixel_values.float())
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
        for key in ("pred_masks", "pred_boxes", "pred_logits", "presence_logits", "semantic_seg"):
            v = getattr(outputs, key, None)
            if v is not None and hasattr(v, "float") and v.dtype != self.torch.float32:
                setattr(outputs, key, v.float())
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
            outputs = self.model(vision_embeds=state["vision_embeds"], **self._cast(text_inputs))
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
            inputs = self._cast(self.processor(**kwargs).to(self.device))
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
