"""Model phụ của 0.9.6 (đều tuỳ chọn, chỉ nạp khi cần, VRAM do runtime_tools.Resources quản lý):

  re-ID      DINOv2-small (facebook/dinov2-small, Apache-2.0): véc-tơ ngoại hình của từng người, để nhận lại đúng
             người sau khi khuất (thay cho so màu áo đơn thuần, vốn nhầm giữa hai người mặc giống nhau).
  độ sâu     Depth Anything V2 Small (depth-anything/Depth-Anything-V2-Small-hf, Apache-2.0): ai đứng trước ai,
             để biết người bị chồng hộp là người đứng SAU (mới bị che), không phải người đứng trước.
  toàn thân  RTMW (OpenMMLab RTMPose whole-body, Apache-2.0, chạy ONNX trên CPU): 133 điểm COCO-WholeBody
             (thân 17, bàn chân 6, mặt 68, mỗi bàn tay 21), dùng cho skeleton có điểm tay / chân và để biết
             hộp bàn tay là tay trái hay tay phải của ai.
Không có model (chưa cài thành phần): tool chạy như cũ và báo một dòng.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .base import resolve_device

# ------------------------------------------------------------------ re-ID ---


class ReIDEngine:
    name = "dinov2"

    def __init__(self, model_id: str, device: str = "auto"):
        self.model_id, self.requested_device = model_id, device
        self.lock = threading.Lock()

    def load(self) -> "ReIDEngine":
        _offline_guard(self.model_id, "Nhận lại người (DINOv2)")
        import torch
        from transformers import AutoImageProcessor, AutoModel

        self.torch = torch
        self.device = resolve_device(self.requested_device)
        self.processor = AutoImageProcessor.from_pretrained(self.model_id)
        self.model = AutoModel.from_pretrained(self.model_id).to(self.device).eval()
        return self

    def embed(self, image_rgb: np.ndarray, boxes: list[list[float]]) -> list[list[float]]:
        crops = [_square_crop(image_rgb, b) for b in boxes]
        if not crops:
            return []
        with self.lock, self.torch.no_grad():
            inputs = self.processor(images=crops, return_tensors="pt", do_center_crop=False,
                                    size={"height": 224, "width": 224}).to(self.device)
            out = self.model(**inputs)
            feat = out.pooler_output if getattr(out, "pooler_output", None) is not None else out.last_hidden_state[:, 0]
            feat = self.torch.nn.functional.normalize(feat.float(), dim=-1)
        return [[round(float(v), 4) for v in row] for row in feat.cpu().numpy()]


class MockReID:
    """KHÔNG phải model thật: véc-tơ từ màu trung bình theo 4 dải ngang của người (đủ để test nối ID)."""

    name = "mock"

    def load(self):
        return self

    def embed(self, image_rgb, boxes):
        out = []
        for b in boxes:
            crop = _crop(image_rgb, b)
            if crop.size == 0:
                out.append([0.0] * 12)
                continue
            bands = np.array_split(crop.astype(float), 4, axis=0)
            v = np.concatenate([band.reshape(-1, 3).mean(0) if band.size else np.zeros(3) for band in bands])
            v = v - v.mean()
            n = np.linalg.norm(v) or 1.0
            out.append([round(float(x), 4) for x in v / n])
        return out


# ---------------------------------------------------------------- độ sâu ---


class DepthEngine:
    name = "depth-anything-v2"

    def __init__(self, model_id: str, device: str = "auto"):
        self.model_id, self.requested_device = model_id, device
        self.lock = threading.Lock()

    def load(self) -> "DepthEngine":
        _offline_guard(self.model_id, "Độ sâu (Depth Anything V2)")
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        self.torch = torch
        self.device = resolve_device(self.requested_device)
        self.processor = AutoImageProcessor.from_pretrained(self.model_id)
        self.model = AutoModelForDepthEstimation.from_pretrained(self.model_id).to(self.device).eval()
        return self

    def depth(self, image_rgb: np.ndarray) -> np.ndarray:
        """Độ gần tương đối (càng lớn càng gần camera), cùng cỡ ảnh, chuẩn hoá 0..1."""
        from PIL import Image

        h, w = image_rgb.shape[:2]
        with self.lock, self.torch.no_grad():
            inputs = self.processor(images=Image.fromarray(image_rgb), return_tensors="pt").to(self.device)
            pred = self.model(**inputs).predicted_depth
            pred = self.torch.nn.functional.interpolate(pred.unsqueeze(1).float(), size=(h, w), mode="bilinear",
                                                        align_corners=False)[0, 0]
        d = pred.cpu().numpy()
        lo, hi = float(np.percentile(d, 1)), float(np.percentile(d, 99))
        return np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1).astype(np.float32)


class MockDepth:
    """KHÔNG phải model thật: càng xuống dưới ảnh càng gần (mặt đất phẳng)."""

    name = "mock"

    def load(self):
        return self

    def depth(self, image_rgb):
        h, w = image_rgb.shape[:2]
        return np.repeat(np.linspace(0, 1, h, dtype=np.float32)[:, None], w, axis=1)


def box_closeness(depth: Optional[np.ndarray], box: list[float]) -> Optional[float]:
    """Độ gần của một vật: trung vị độ sâu ở phần giữa hộp (bỏ viền, nơi hay lẫn nền)."""
    if depth is None:
        return None
    h, w = depth.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    xs = slice(int(max(0, x1 + 0.25 * bw)), int(min(w, x2 - 0.25 * bw)) or 1)
    ys = slice(int(max(0, y1 + 0.2 * bh)), int(min(h, y2 - 0.2 * bh)) or 1)
    region = depth[ys, xs]
    return round(float(np.median(region)), 4) if region.size else None


# ---------------------------------------------------------- pose toàn thân ---
# COCO-WholeBody: 0-16 thân (COCO17), 17-22 bàn chân, 23-90 mặt (68 điểm), 91-111 tay trái, 112-132 tay phải.
FEET = ["left_big_toe", "left_small_toe", "left_heel", "right_big_toe", "right_small_toe", "right_heel"]
FINGERS = ["thumb", "index_finger", "middle_finger", "ring_finger", "pinky_finger"]


def _hand(side: str) -> list[str]:
    return [f"{side}_hand_root"] + [f"{side}_{f}{k}" for f in FINGERS for k in range(1, 5)]


WHOLEBODY_EXTRA = FEET + [f"face_{i}" for i in range(68)] + _hand("left") + _hand("right")
# Cặp trái/phải ngoài COCO17 (để đổi phía khi guideline đặt R/L theo ảnh)
WB_PAIRS = [(17 + i, 20 + i) for i in range(3)] + [(91 + i, 112 + i) for i in range(21)]


class RTMWEngine:
    """RTMW whole-body (ONNX). Mỗi hộp người -> 133 điểm + độ tin cậy."""

    name = "rtmw"
    input_w, input_h = 192, 256
    mean = np.array([123.675, 116.28, 103.53], np.float32)
    std = np.array([58.395, 57.12, 57.375], np.float32)
    score_gain = 1.6          # điểm SimCC thấp hơn heatmap ViTPose: kéo về cùng thang ngưỡng của profile

    def __init__(self, model_path: Path):
        self.model_path = Path(model_path)
        self.lock = threading.Lock()

    def load(self) -> "RTMWEngine":
        if not self.model_path.exists():
            raise RuntimeError("Chưa có model pose toàn thân (RTMW). Mở bảng điều khiển của app, mục Thành phần, "
                               "tải 'Pose toàn thân (tay, chân)'.")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ImportError("thiếu gói onnxruntime (pip install onnxruntime)") from exc
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
        self._session = ort.InferenceSession(str(self.model_path), opts, providers=["CPUExecutionProvider"])
        shape = self._session.get_inputs()[0].shape
        if isinstance(shape[2], int) and isinstance(shape[3], int):
            self.input_h, self.input_w = shape[2], shape[3]
        self.device = "cpu"
        return self

    def _prep(self, image_rgb, box):
        import cv2

        x1, y1, x2, y2 = box
        center = np.array([(x1 + x2) / 2, (y1 + y2) / 2], np.float32)
        scale = np.array([(x2 - x1) * 1.25, (y2 - y1) * 1.25], np.float32)
        aspect = self.input_w / self.input_h
        if scale[0] > scale[1] * aspect:
            scale[1] = scale[0] / aspect
        else:
            scale[0] = scale[1] * aspect
        src = np.array([center, center + [0, -scale[1] / 2], center + [-scale[0] / 2, -scale[1] / 2]], np.float32)
        dst = np.array([[self.input_w / 2, self.input_h / 2], [self.input_w / 2, 0], [0, 0]], np.float32)
        warp = cv2.getAffineTransform(src, dst)
        img = cv2.warpAffine(image_rgb, warp, (self.input_w, self.input_h), flags=cv2.INTER_LINEAR)
        img = (img.astype(np.float32) - self.mean) / self.std
        return img.transpose(2, 0, 1), center, scale

    def estimate(self, image_rgb: np.ndarray, boxes_xyxy: list[list[float]]) -> list[tuple[np.ndarray, np.ndarray]]:
        out = []
        for box in boxes_xyxy:
            inp, center, scale = self._prep(image_rgb, box)
            with self.lock:
                sx, sy = self._session.run(None, {self._session.get_inputs()[0].name: inp[None]})[:2]
            out.append(decode_simcc(sx[0], sy[0], center, scale, (self.input_w, self.input_h), self.score_gain))
        return out

    def close(self):
        self._session = None


def decode_simcc(simcc_x: np.ndarray, simcc_y: np.ndarray, center, scale, input_size, gain: float = 1.0,
                 split: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    """SimCC (K x W*split, K x H*split) -> toạ độ trên ảnh gốc và độ tin cậy."""
    x = np.argmax(simcc_x, axis=1).astype(np.float32) / split
    y = np.argmax(simcc_y, axis=1).astype(np.float32) / split
    score = np.minimum(np.max(simcc_x, axis=1), np.max(simcc_y, axis=1))
    pts = np.stack([x, y], 1) / np.array(input_size, np.float32) * scale + center - scale / 2
    return pts, np.clip(score * gain, 0, 1)


class MockWholeBody:
    """KHÔNG phải model thật: dáng đứng mẫu, thêm bàn chân và bàn tay quanh cổ chân / cổ tay."""

    name = "mock"
    device = "cpu"

    def load(self):
        from .pose import MockBodyPose

        self.body = MockBodyPose().load()
        return self

    def estimate(self, image_rgb, boxes_xyxy):
        out = []
        for (pts, sc), (x1, y1, x2, y2) in zip(self.body.estimate(image_rgb, boxes_xyxy), boxes_xyxy):
            w = x2 - x1
            extra = np.zeros((116, 2))
            esc = np.full(116, 0.8)
            for i, ank in enumerate((15, 15, 15, 16, 16, 16)):
                extra[i] = pts[ank] + [(-1) ** i * 0.03 * w, 0.02 * w]
                esc[i] = sc[ank]
            fx, fy = pts[0]
            for i in range(68):
                a = 2 * np.pi * i / 68
                extra[6 + i] = (fx + 0.08 * w * np.cos(a), fy + 0.1 * w * np.sin(a))
            for side, wrist in ((0, 9), (1, 10)):
                for k in range(21):
                    extra[74 + side * 21 + k] = pts[wrist] + [(k % 5 - 2) * 0.01 * w, 0.01 * w * (k // 5 + 1)]
                    esc[74 + side * 21 + k] = sc[wrist]
            out.append((np.vstack([pts, extra]), np.concatenate([sc, esc])))
        return out


# -------------------------------------------------------------- dùng chung ---
def create_reid(mock: bool, model_id: str, device: str):
    return MockReID().load() if mock else ReIDEngine(model_id, device).load()


def create_depth(mock: bool, model_id: str, device: str):
    return MockDepth().load() if mock else DepthEngine(model_id, device).load()


def create_wholebody(mock: bool, model_path: Path):
    return MockWholeBody().load() if mock else RTMWEngine(model_path).load()


def _offline_guard(model_id: str, title: str) -> None:
    if os.getenv("SAM3_NO_AUTO_DOWNLOAD") and not Path(model_id).exists():
        raise RuntimeError(f"Model {title} chưa được tải. Mở bảng điều khiển của app, mục Thành phần, bấm tải.")


def _crop(image, box):
    h, w = image.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    return image[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]


def _square_crop(image, box):
    from PIL import Image

    crop = _crop(image, box)
    if crop.size == 0:
        crop = np.full((8, 8, 3), 127, np.uint8)
    h, w = crop.shape[:2]
    side = max(h, w)
    canvas = np.full((side, side, 3), 127, np.uint8)
    canvas[(side - h) // 2:(side - h) // 2 + h, (side - w) // 2:(side - w) // 2 + w] = crop
    return Image.fromarray(canvas)
