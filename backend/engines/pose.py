"""Model phụ cho skeleton: tư thế người (ViTPose) và điểm mốc khuôn mặt (MediaPipe).

SAM3 chỉ tìm người; nó không đặt được điểm khớp. Hai model ở đây chạy sau SAM3:
  - ViTPose (qua `transformers`, đã cài sẵn cùng SAM3): 17 điểm COCO cho mỗi box người.
  - MediaPipe FaceLandmarker: 478 điểm trên mỗi khuôn mặt; profile tính các landmark của guideline từ đó.
Cả hai chỉ được nạp khi job có label skeleton.
"""
from __future__ import annotations

import logging
import threading
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np

from .base import resolve_device

log = logging.getLogger("sam3-assist")

FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)


class BodyPoseEngine:
    name = "body"

    def estimate(self, image_rgb: np.ndarray, boxes_xyxy: list[list[float]]) -> list[tuple[np.ndarray, np.ndarray]]:
        """Mỗi box -> (toạ độ 17x2 theo pixel ảnh gốc, độ tin cậy 17)."""
        raise NotImplementedError


class FaceEngine:
    name = "face"

    def detect(self, image_rgb: np.ndarray) -> list[np.ndarray]:
        """Mỗi khuôn mặt -> mảng Nx2 toạ độ pixel (N = 478 với MediaPipe)."""
        raise NotImplementedError


class ViTPoseEngine(BodyPoseEngine):
    name = "vitpose"

    def __init__(self, model_id: str, device: str = "auto"):
        self.model_id = model_id
        self.requested_device = device
        self.lock = threading.Lock()

    def load(self) -> "ViTPoseEngine":
        try:
            import scipy  # noqa: F401
        except ImportError as exc:
            # Thiếu scipy thì transformers vẫn nạp được ViTPose, rồi mới lỗi khó hiểu lúc chạy: NameError: name 'inv' is not defined.
            raise ImportError("thiếu gói scipy. Chạy trong môi trường của backend: pip install scipy  rồi khởi động lại backend.") from exc
        import torch
        from transformers import AutoProcessor, VitPoseForPoseEstimation

        self.torch = torch
        self.device = resolve_device(self.requested_device)
        self.processor = AutoProcessor.from_pretrained(self.model_id)
        self.model = VitPoseForPoseEstimation.from_pretrained(self.model_id).to(self.device).eval()
        return self

    def estimate(self, image_rgb, boxes_xyxy):
        if not boxes_xyxy:
            return []
        from PIL import Image

        image = Image.fromarray(image_rgb)
        coco = [[float(x1), float(y1), float(x2 - x1), float(y2 - y1)] for x1, y1, x2, y2 in boxes_xyxy]
        with self.lock, self.torch.no_grad():
            inputs = self.processor(image, boxes=[coco], return_tensors="pt").to(self.device)
            outputs = self.model(**inputs)
        results = self.processor.post_process_pose_estimation(outputs, boxes=[coco])[0]
        out = []
        for item in results:
            kp = item["keypoints"]
            sc = item["scores"]
            kp = kp.detach().float().cpu().numpy() if hasattr(kp, "detach") else np.asarray(kp, float)
            sc = sc.detach().float().cpu().numpy() if hasattr(sc, "detach") else np.asarray(sc, float)
            out.append((kp.reshape(-1, 2), sc.reshape(-1)))
        return out


class MediaPipeFaceEngine(FaceEngine):
    name = "mediapipe"

    def __init__(self, model_path: Path, max_faces: int = 20):
        self.model_path = Path(model_path)
        self.max_faces = max_faces
        self.lock = threading.Lock()

    def load(self) -> "MediaPipeFaceEngine":
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        if not self.model_path.exists():
            log.info("Đang tải model khuôn mặt của MediaPipe về %s ...", self.model_path)
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.model_path.with_suffix(".part")
            urllib.request.urlretrieve(FACE_MODEL_URL, tmp)
            tmp.replace(self.model_path)
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=self.model_path.read_bytes()),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=self.max_faces,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
        )
        self.mp = mp
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        return self

    def detect(self, image_rgb):
        h, w = image_rgb.shape[:2]
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(image_rgb))
        with self.lock:
            result = self.landmarker.detect(image)
        return [np.array([[p.x * w, p.y * h] for p in face], float) for face in result.face_landmarks]


# ------------------------------------------------------------ giả lập ------
# Người quay mặt về camera: điểm left_* nằm bên PHẢI ảnh (toạ độ chuẩn hoá trong box).
_BODY_TEMPLATE = np.array([
    [0.50, 0.08], [0.53, 0.06], [0.47, 0.06], [0.57, 0.08], [0.43, 0.08], [0.64, 0.22], [0.36, 0.22],
    [0.70, 0.38], [0.30, 0.38], [0.73, 0.53], [0.27, 0.53], [0.58, 0.52], [0.42, 0.52],
    [0.60, 0.73], [0.40, 0.73], [0.60, 0.94], [0.40, 0.94],
])
_BODY_SCORES = np.array([0.9] * 9 + [0.45, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.1])


class MockBodyPose(BodyPoseEngine):
    """KHÔNG phải model thật: đặt một dáng đứng mẫu vào mỗi box (cổ tay trái mờ, cổ chân phải mất)."""

    name = "mock"
    device = "cpu"

    def load(self):
        return self

    def estimate(self, image_rgb, boxes_xyxy):
        out = []
        for x1, y1, x2, y2 in boxes_xyxy:
            pts = np.stack([x1 + _BODY_TEMPLATE[:, 0] * (x2 - x1), y1 + _BODY_TEMPLATE[:, 1] * (y2 - y1)], 1)
            out.append((pts, _BODY_SCORES.copy()))
        return out


# Vị trí chuẩn hoá (0..1 trong khung mặt) của các điểm MediaPipe mà profile dùng, lấy từ mô hình mặt chuẩn của
# MediaPipe (canonical_face_model, giấy phép Apache-2.0), mặt nhìn thẳng. Chỉ dùng cho engine giả lập.
_FACE_TEMPLATE = {
    0: (0.5000, 0.6605), 1: (0.5000, 0.5315), 4: (0.5000, 0.4939), 5: (0.5000, 0.4470), 6: (0.5000, 0.3277),
    7: (0.2254, 0.3218), 13: (0.5000, 0.6938), 14: (0.5000, 0.7248), 17: (0.5000, 0.7714), 33: (0.2129, 0.3169),
    37: (0.4541, 0.6562), 39: (0.4076, 0.6659), 40: (0.3763, 0.6830), 61: (0.3414, 0.7135), 63: (0.1780, 0.1958),
    66: (0.3218, 0.1789), 70: (0.1306, 0.2268), 78: (0.3610, 0.7098), 80: (0.4051, 0.6962), 81: (0.4339, 0.6935),
    82: (0.4656, 0.6937), 84: (0.4548, 0.7673), 87: (0.4623, 0.7234), 88: (0.4007, 0.7181), 91: (0.3813, 0.7410),
    95: (0.3820, 0.7167), 105: (0.2426, 0.1784), 107: (0.4099, 0.1840), 133: (0.3801, 0.3213), 144: (0.2630, 0.3341),
    145: (0.2948, 0.3378), 146: (0.3582, 0.7280), 153: (0.3241, 0.3366), 154: (0.3522, 0.3319), 155: (0.3710,
    0.3264), 157: (0.3522, 0.3043), 158: (0.3241, 0.3000), 159: (0.2948, 0.2999), 160: (0.2630, 0.3020),
    161: (0.2405, 0.3059), 163: (0.2405, 0.3271), 168: (0.5000, 0.2825), 173: (0.3710, 0.3124), 178: (0.4290,
    0.7201), 181: (0.4144, 0.7568), 185: (0.3524, 0.6970), 191: (0.3809, 0.6997), 195: (0.5000, 0.4077),
    197: (0.5000, 0.3698), 246: (0.2254, 0.3108), 249: (0.7746, 0.3218), 263: (0.7871, 0.3169), 267: (0.5459,
    0.6562), 269: (0.5924, 0.6659), 270: (0.6237, 0.6830), 291: (0.6586, 0.7135), 293: (0.8220, 0.1958),
    296: (0.6782, 0.1789), 300: (0.8694, 0.2268), 308: (0.6390, 0.7098), 310: (0.5949, 0.6962), 311: (0.5661,
    0.6935), 312: (0.5344, 0.6937), 314: (0.5452, 0.7673), 317: (0.5377, 0.7234), 318: (0.5993, 0.7181),
    321: (0.6187, 0.7410), 324: (0.6180, 0.7167), 334: (0.7574, 0.1784), 336: (0.5901, 0.1840), 362: (0.6199,
    0.3213), 373: (0.7370, 0.3341), 374: (0.7052, 0.3378), 375: (0.6418, 0.7280), 380: (0.6759, 0.3366),
    381: (0.6478, 0.3319), 382: (0.6290, 0.3264), 384: (0.6478, 0.3043), 385: (0.6759, 0.3000), 386: (0.7052,
    0.2999), 387: (0.7370, 0.3020), 388: (0.7595, 0.3059), 390: (0.7595, 0.3271), 398: (0.6290, 0.3124),
    402: (0.5710, 0.7201), 405: (0.5856, 0.7568), 409: (0.6476, 0.6970), 415: (0.6191, 0.6997), 466: (0.7746,
    0.3108),
}


class MockFace(FaceEngine):
    """KHÔNG phải model thật: một khuôn mặt nhìn thẳng ở giữa phần trên ảnh."""

    name = "mock"

    def load(self):
        return self

    def detect(self, image_rgb):
        h, w = image_rgb.shape[:2]
        size = min(w, h) * 0.4
        x0, y0 = w * 0.5 - size / 2, h * 0.08
        lm = np.tile([[x0 + size / 2, y0 + size / 2]], (478, 1)).astype(float)
        for idx, (u, v) in _FACE_TEMPLATE.items():
            lm[idx] = (x0 + u * size, y0 + v * size)
        return [lm]


def create_body_pose(mock: bool, model_id: str, device: str) -> BodyPoseEngine:
    return MockBodyPose().load() if mock else ViTPoseEngine(model_id, device).load()


def create_face(mock: bool, model_path: Path, max_faces: int = 20) -> FaceEngine:
    return MockFace().load() if mock else MediaPipeFaceEngine(model_path, max_faces).load()


class LazyEngine:
    """Nạp model ở lần dùng đầu; lỗi được nhớ để báo lại cho panel thay vì làm sập request."""

    def __init__(self, factory, what: str):
        self.factory = factory
        self.what = what
        self.engine: Optional[object] = None
        self.error: Optional[str] = None
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if self.engine is None and self.error is None:
                try:
                    log.info("Đang nạp %s ...", self.what)
                    self.engine = self.factory()
                    log.info("Đã nạp %s.", self.what)
                except Exception as exc:  # noqa: BLE001
                    self.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                    log.warning("Không nạp được %s: %s", self.what, self.error)
            return self.engine

    def reset(self) -> None:
        with self.lock:
            self.engine, self.error = None, None
