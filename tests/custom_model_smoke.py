"""Chạy trong CI (máy có PyTorch): huấn luyện model riêng vài vòng trên ảnh tổng hợp, lưu, nạp lại, dự đoán.

  python tests/custom_model_smoke.py <thư mục packages của PyTorch>
"""
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from backend import custom_model as C  # noqa: E402

rng = np.random.default_rng(0)
store = C.CustomStore(Path(tempfile.mkdtemp()))
samples = []
for i in range(16):
    img = np.full((240, 320, 3), 40, np.uint8)
    boxes = []
    for _ in range(2):
        x, y = int(rng.integers(10, 220)), int(rng.integers(10, 150))
        red = rng.random() < 0.5
        img[y:y + 60, x:x + 80] = (220, 30, 30) if red else (30, 30, 220)
        boxes.append({"label": "red" if red else "blue", "bbox": [x, y, x + 80, y + 60]})
    samples.append((f"s{i}", img, boxes))
store.add_samples("p", samples)
progress = []
meta = C.train(store, "p", 3, "cpu", threading.Event(), lambda **k: progress.append(k))
assert meta["classes"] == ["blue", "red"] and store.model_path("p").exists() and progress
model = C.load_model(store.model_path("p"), meta, "cpu")
out = C.predict(model, meta, samples[0][1], {"red", "blue"}, 0.0)
print("model riêng OK:", {c: meta["metrics"][c] for c in meta["classes"]}, "dự đoán:", len(out))
