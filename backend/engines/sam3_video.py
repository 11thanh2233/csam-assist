"""Theo dõi video bằng SAM3 Video (transformers: Sam3VideoModel + Sam3VideoProcessor), chế độ streaming.

Khác cách cũ (SAM3 tìm vật trên từng frame rồi nối các hộp bằng IoU/màu sắc): SAM3 Video có bộ nhớ kiểu SAM2,
tự giữ ID của từng vật qua các frame, nên ít đổi ID khi vật bị che hay đi cắt nhau.

Đây là tuỳ chọn thử nghiệm:
  - Model video là một model riêng (~3,4 GB ở float32). Trên GPU 8 GB, tool nạp nó ở float16 và để dữ liệu video,
    trạng thái theo dõi trên RAM; vẫn có thể thiếu VRAM khi đang nạp cả SAM3 ảnh.
  - Streaming (đưa từng frame vào) tắt một số heuristic lọc track trùng của SAM3 Video, nên có thể có track thừa:
    tool vẫn chạy bước dựng track/keyframe/cờ như cũ để lọc.
  - Lỗi bất kỳ (thiếu model, thiếu VRAM, transformers cũ) -> userscript tự quay về cách cũ.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Optional

import numpy as np

log = logging.getLogger("sam3-assist")
KERNELS_NOTICE = "kernels library is not installed"


class _NoKernelsNotice(logging.Filter):
    """transformers nhắc cài gói `kernels` (lọc trùng, lấp lỗ, xoá đốm cho mask). Gói đó không có bản cho Windows;
    tool tự làm ba việc này bằng OpenCV (clean_video_objects), nên bỏ dòng nhắc khỏi nhật ký."""

    def filter(self, record: logging.LogRecord) -> bool:
        return KERNELS_NOTICE not in record.getMessage()


def quiet_kernels_notice() -> None:
    import warnings

    warnings.filterwarnings("ignore", message=f".*{KERNELS_NOTICE}.*")
    f = _NoKernelsNotice()
    for name in ("transformers", ""):
        lg = logging.getLogger(name)
        lg.addFilter(f)
        for handler in lg.handlers:
            handler.addFilter(f)


def clean_video_objects(objects: list[dict], masks: Optional[list] = None, iou_dup: float = 0.8, hole_px: int = 0) -> list[dict]:
    """Thay cho bước hậu xử lý cần gói `kernels` (0.9.4): lấp lỗ, xoá đốm nhỏ trong mask, tính lại hộp từ mask sạch,
    rồi bỏ vật trùng (cùng prompt, hộp trùng >= iou_dup, giữ vật điểm cao hơn)."""
    import cv2

    from .. import geometry as G

    out = []
    for i, obj in enumerate(objects):
        mask = masks[i] if masks is not None and i < len(masks) else None
        if mask is not None:
            m = np.asarray(mask).astype(bool)
            if m.ndim == 3:
                m = m[0]
            if m.any():
                m = G.clean_mask(m)                           # xoá đốm
                filled = m.astype(np.uint8)
                contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(filled, contours, -1, 1, thickness=-1)   # lấp lỗ bên trong
                box = G.mask_bbox(filled.astype(bool))
                if box is not None:
                    obj = dict(obj, box=[float(v) for v in box])
        out.append(obj)
    kept: list[dict] = []
    for obj in sorted(out, key=lambda o: -o["score"]):
        if any(k["prompt"] == obj["prompt"] and G.box_iou(k["box"], obj["box"]) >= iou_dup for k in kept):
            continue
        kept.append(obj)
    return kept


class VideoTrackerError(RuntimeError):
    pass


class Sam3VideoTracker:
    name = "sam3_video"

    def __init__(self, model_id: str, device: str, dtype: str = "auto"):
        self.model_id, self.requested_device, self.dtype_name = model_id, device, dtype
        self.lock = threading.Lock()
        self.model = None
        self.sessions: dict[str, dict] = {}

    def load(self) -> "Sam3VideoTracker":
        import torch
        from transformers import Sam3VideoModel, Sam3VideoProcessor

        from .base import resolve_device

        self.torch = torch
        self.device = resolve_device(self.requested_device)
        if self.dtype_name == "auto":
            self.dtype = torch.float16 if self.device == "cuda" else torch.float32   # GPU dòng RTX 20 không có bfloat16
        else:
            self.dtype = getattr(torch, self.dtype_name)
        quiet_kernels_notice()
        self.processor = Sam3VideoProcessor.from_pretrained(self.model_id)
        self.model = Sam3VideoModel.from_pretrained(self.model_id, torch_dtype=self.dtype).to(self.device).eval()
        return self

    def start(self, prompts: list[str]) -> str:
        """Mở một phiên theo dõi cho các prompt. Mỗi lúc chỉ giữ một phiên (đỡ tốn bộ nhớ)."""
        with self.lock:
            self.sessions.clear()
            session = self.processor.init_video_session(
                inference_device=self.device, processing_device="cpu", video_storage_device="cpu",
                inference_state_device="cpu" if self.device == "cuda" else self.device, dtype=self.dtype)
            session = self.processor.add_text_prompt(session, list(prompts))
            sid = uuid.uuid4().hex[:12]
            self.sessions[sid] = {"session": session, "prompts": list(prompts)}
            return sid

    def add_frame(self, sid: str, image_rgb: np.ndarray) -> list[dict]:
        """Đưa một frame vào phiên. Trả về [{obj_id, prompt, box, score}] (box theo pixel ảnh gốc)."""
        from PIL import Image

        with self.lock, self.torch.no_grad():
            entry = self.sessions.get(sid)
            if entry is None:
                raise VideoTrackerError("Phiên theo dõi đã kết thúc hoặc backend vừa khởi động lại.")
            inputs = self.processor(images=Image.fromarray(image_rgb), device=self.device, return_tensors="pt")
            outputs = self.model(inference_session=entry["session"], frame=inputs.pixel_values[0].to(self.dtype), reverse=False)
            post = self.processor.postprocess_outputs(entry["session"], outputs, original_sizes=inputs.original_sizes)
        prompt_of = {}
        for prompt, ids in (post.get("prompt_to_obj_ids") or {}).items():
            for oid in (ids.tolist() if hasattr(ids, "tolist") else ids):
                prompt_of[int(oid)] = prompt
        ids = post["object_ids"].tolist() if hasattr(post["object_ids"], "tolist") else list(post["object_ids"])
        boxes = post["boxes"].float().cpu().numpy() if hasattr(post["boxes"], "cpu") else np.asarray(post["boxes"], float)
        scores = post["scores"].float().cpu().numpy() if hasattr(post["scores"], "cpu") else np.asarray(post["scores"], float)
        masks = post.get("masks")
        if masks is not None and hasattr(masks, "cpu"):
            masks = masks.cpu().numpy()
        objects = [{"obj_id": int(oid), "prompt": prompt_of.get(int(oid)), "box": [float(v) for v in boxes[i]], "score": float(scores[i])}
                   for i, oid in enumerate(ids)]
        return clean_video_objects(objects, list(masks) if masks is not None and len(masks) == len(objects) else None)

    def end(self, sid: Optional[str] = None) -> None:
        with self.lock:
            if sid is None:
                self.sessions.clear()
            else:
                self.sessions.pop(sid, None)
            if getattr(self, "torch", None) is not None and self.torch.cuda.is_available():
                self.torch.cuda.empty_cache()


class MockVideoTracker:
    """KHÔNG phải model thật: dùng engine giả lập trên từng frame, ID = prompt + thứ tự vật (ổn định vì engine giả lập cố định)."""

    name = "mock_video"

    def __init__(self, engine: Any):
        self.engine = engine
        self.sessions: dict[str, list[str]] = {}

    def load(self) -> "MockVideoTracker":
        return self

    def start(self, prompts: list[str]) -> str:
        sid = uuid.uuid4().hex[:12]
        self.sessions = {sid: list(prompts)}
        return sid

    def add_frame(self, sid: str, image_rgb: np.ndarray) -> list[dict]:
        from .. import geometry as G

        if sid not in self.sessions:
            raise VideoTrackerError("Phiên theo dõi đã kết thúc hoặc backend vừa khởi động lại.")
        state = self.engine.encode(image_rgb)
        out = []
        for pi, prompt in enumerate(self.sessions[sid]):
            for k, inst in enumerate(self.engine.segment_text(state, prompt, 0.3)):
                box = G.mask_bbox(inst.mask.astype(bool))
                if box is not None:
                    out.append({"obj_id": pi * 100 + k + 1, "prompt": prompt, "box": [float(v) for v in box], "score": inst.score})
        return out

    def end(self, sid: Optional[str] = None) -> None:
        self.sessions.clear()
