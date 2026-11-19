"""Model riêng của dự án: một detector nhỏ học từ chính các nhãn hộp đã được người duyệt.

- Kiến trúc: Faster R-CNN MobileNetV3 (torchvision, giấy phép BSD). Không dùng YOLO của Ultralytics (AGPL).
- Dữ liệu: ảnh + hộp của các label dạng hộp trong job (nhãn đã nằm trong CVAT, tức đã qua tay người).
  Lưu ở <thư viện>/custom/<profile>/data/: ảnh .jpg + labels.jsonl.
- Đánh giá: giữ lại 20% ảnh để kiểm, tính precision/recall từng class (IoU >= 0.5, điểm >= 0.5).
  Chỉ class đạt chuẩn (đủ ví dụ, precision >= 0.7, recall >= 0.6) mới được dùng khi gợi ý; class còn lại vẫn do SAM3 làm.
- Dùng: nhanh hơn SAM3 nhiều lần cho class hay gặp. Có trọng số khởi đầu COCO (SAM3_DETECTOR_BASE) thì học nhanh
  và tốt hơn nhiều; không có thì học từ đầu (cần nhiều dữ liệu hơn).
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from . import geometry as G

log = logging.getLogger("sam3-assist")
MIN_BOXES = 30
MIN_PRECISION, MIN_RECALL = 0.7, 0.6


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)[:80] or "profile"


class CustomStore:
    """Dữ liệu + model riêng theo profile."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def dir(self, profile_id: str) -> Path:
        d = self.root / _slug(profile_id)
        (d / "data").mkdir(parents=True, exist_ok=True)
        return d

    def add_samples(self, profile_id: str, samples: list[tuple[str, np.ndarray, list[dict]]]) -> dict:
        """samples: [(khoá duy nhất, ảnh RGB, [{label, bbox}])]. Khoá trùng thì ghi đè (gom lại cùng một frame không bị nhân đôi)."""
        d = self.dir(profile_id) / "data"
        index = self._index(profile_id)
        for key, image, boxes in samples:
            name = _slug(key) + ".jpg"
            cv2.imwrite(str(d / name), cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
            index[name] = {"file": name, "w": int(image.shape[1]), "h": int(image.shape[0]),
                           "boxes": [{"label": str(b["label"]), "bbox": [float(v) for v in b["bbox"]]} for b in boxes if len(b.get("bbox", [])) == 4]}
        (d / "labels.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        return self.dataset_info(profile_id)

    def _index(self, profile_id: str) -> dict:
        f = self.dir(profile_id) / "data" / "labels.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def dataset_info(self, profile_id: str) -> dict:
        index = self._index(profile_id)
        counts: dict[str, int] = {}
        for item in index.values():
            for b in item["boxes"]:
                counts[b["label"]] = counts.get(b["label"], 0) + 1
        return {"images": len(index), "boxes": sum(counts.values()), "per_class": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}

    def clear(self, profile_id: str) -> None:
        import shutil

        shutil.rmtree(self.dir(profile_id), ignore_errors=True)

    def model_path(self, profile_id: str) -> Path:
        return self.dir(profile_id) / "model.pt"

    def meta(self, profile_id: str) -> Optional[dict]:
        f = self.dir(profile_id) / "model.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def _box_iou(a, b) -> float:
    return G.box_iou(list(a), list(b))


def evaluate(preds: list[list[tuple[str, list[float], float]]], truths: list[list[tuple[str, list[float]]]], classes: list[str],
             score_thr: float = 0.5, iou_thr: float = 0.5) -> dict:
    """Precision/recall từng class trên tập kiểm."""
    stats = {c: {"tp": 0, "fp": 0, "fn": 0} for c in classes}
    for pred, truth in zip(preds, truths):
        pred = [p for p in pred if p[2] >= score_thr]
        used = set()
        for name, box, score in sorted(pred, key=lambda p: -p[2]):
            best, j = 0.0, -1
            for k, (tn, tb) in enumerate(truth):
                if k in used or tn != name:
                    continue
                v = _box_iou(box, tb)
                if v > best:
                    best, j = v, k
            if best >= iou_thr:
                used.add(j)
                stats[name]["tp"] += 1
            elif name in stats:
                stats[name]["fp"] += 1
        for k, (tn, _) in enumerate(truth):
            if k not in used and tn in stats:
                stats[tn]["fn"] += 1
    out = {}
    for c, s in stats.items():
        p = s["tp"] / (s["tp"] + s["fp"]) if s["tp"] + s["fp"] else 0.0
        r = s["tp"] / (s["tp"] + s["fn"]) if s["tp"] + s["fn"] else 0.0
        out[c] = {"precision": round(p, 3), "recall": round(r, 3), **s}
    return out


class Trainer:
    """Huấn luyện ở luồng nền, một lượt mỗi lúc. Hỏi tiến độ qua status()."""

    def __init__(self, store: CustomStore, device: str = "auto"):
        self.store, self.device_pref = store, device
        self.thread: Optional[threading.Thread] = None
        self.stop = threading.Event()
        self.state: dict = {"running": False}
        self.models: dict[str, Any] = {}          # profile -> (model, meta), nạp lười

    def status(self) -> dict:
        return dict(self.state)

    def start(self, profile_id: str, epochs: int = 12) -> dict:
        if self.thread and self.thread.is_alive():
            raise RuntimeError("Đang huấn luyện một model khác, đợi xong hoặc bấm Dừng.")
        info = self.store.dataset_info(profile_id)
        if info["images"] < 8 or info["boxes"] < 20:
            raise ValueError(f"Cần ít nhất 8 ảnh và 20 hộp (đang có {info['images']} ảnh, {info['boxes']} hộp). Thu thập thêm từ các job đã gán.")
        try:
            import torch  # noqa: F401
            import torchvision  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Cần PyTorch + torchvision để huấn luyện (bản desktop đã có sẵn).") from exc
        self.stop.clear()
        self.state = {"running": True, "profile_id": profile_id, "epoch": 0, "epochs": epochs, "loss": None, "message": "Đang chuẩn bị dữ liệu…",
                      "started": time.time()}
        self.thread = threading.Thread(target=self._run, args=(profile_id, epochs), daemon=True)
        self.thread.start()
        return self.status()

    def _device(self, torch):
        if self.device_pref in ("cpu", "cuda"):
            return self.device_pref
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _run(self, profile_id: str, epochs: int) -> None:
        try:
            meta = train(self.store, profile_id, epochs, self._device_name(), self.stop, self._progress)
            self.models.pop(profile_id, None)
            self.state.update(running=False, finished=True, meta=meta,
                              message=f"Xong. Dùng được cho {len(meta['usable'])} class: {', '.join(meta['usable']) or 'chưa class nào đạt chuẩn'}.")
        except Exception as exc:  # noqa: BLE001
            log.exception("Huấn luyện model riêng lỗi")
            self.state.update(running=False, finished=True, error=f"{type(exc).__name__}: {exc}", message="Huấn luyện lỗi.")

    def _device_name(self) -> str:
        import torch

        return self._device(torch)

    def _progress(self, **kw) -> None:
        self.state.update(kw)

    def model_for(self, profile_id: str):
        meta = self.store.meta(profile_id)
        if not meta or not meta.get("usable"):
            return None
        cached = self.models.get(profile_id)
        if cached and cached[1].get("trained_at") == meta.get("trained_at"):
            return cached
        model = load_model(self.store.model_path(profile_id), meta, self._device_name())
        self.models[profile_id] = (model, meta)
        return self.models[profile_id]


def _build(num_classes: int, torch):
    from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_fpn
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

    model = fasterrcnn_mobilenet_v3_large_fpn(weights=None, weights_backbone=None, num_classes=91, min_size=512, max_size=896)
    base = os.getenv("SAM3_DETECTOR_BASE", "")
    if base and Path(base).exists():
        state = torch.load(base, map_location="cpu", weights_only=True)
        model.load_state_dict(state, strict=False)
        log.info("Model riêng: khởi đầu từ trọng số %s", base)
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)
    return model


def train(store: CustomStore, profile_id: str, epochs: int, device: str, stop: threading.Event, progress) -> dict:
    import torch

    index = list(store._index(profile_id).values())
    classes = sorted({b["label"] for it in index for b in it["boxes"]})
    cid = {c: i + 1 for i, c in enumerate(classes)}
    random.Random(0).shuffle(index)
    n_val = max(1, int(len(index) * 0.2))
    val, tr = index[:n_val], index[n_val:]
    folder = store.dir(profile_id) / "data"

    def load(item):
        img = cv2.cvtColor(cv2.imread(str(folder / item["file"])), cv2.COLOR_BGR2RGB)
        boxes = [b for b in item["boxes"] if b["bbox"][2] - b["bbox"][0] > 1 and b["bbox"][3] - b["bbox"][1] > 1]
        t = {"boxes": torch.tensor([b["bbox"] for b in boxes], dtype=torch.float32).reshape(-1, 4),
             "labels": torch.tensor([cid[b["label"]] for b in boxes], dtype=torch.int64)}
        return torch.from_numpy(img).permute(2, 0, 1).float() / 255.0, t

    model = _build(len(classes) + 1, torch).to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.SGD(params, lr=0.01, momentum=0.9, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, epochs))
    batch = 4 if device == "cuda" else 2
    for epoch in range(epochs):
        if stop.is_set():
            break
        model.train()
        random.shuffle(tr)
        total, steps = 0.0, 0
        for i in range(0, len(tr), batch):
            if stop.is_set():
                break
            imgs, targets = zip(*[load(it) for it in tr[i:i + batch]])
            imgs = [x.to(device) for x in imgs]
            targets = [{k: v.to(device) for k, v in t.items()} for t in targets]
            losses = model(imgs, targets)
            loss = sum(losses.values())
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 10.0)
            opt.step()
            total, steps = total + float(loss), steps + 1
        sched.step()
        progress(epoch=epoch + 1, loss=round(total / max(1, steps), 4), message=f"Vòng {epoch + 1}/{epochs}")
    progress(message="Đang đánh giá trên ảnh giữ lại…")
    model.eval()
    preds, truths = [], []
    names = ["__bg__"] + classes
    with torch.no_grad():
        for it in val:
            img, t = load(it)
            out = model([img.to(device)])[0]
            preds.append([(names[int(l)], [float(v) for v in b], float(s)) for b, l, s in zip(out["boxes"].cpu(), out["labels"].cpu(), out["scores"].cpu())])
            truths.append([(names[int(l)], [float(v) for v in b]) for b, l in zip(t["boxes"], t["labels"])])
    metrics = evaluate(preds, truths, classes)
    counts = store.dataset_info(profile_id)["per_class"]
    usable = [c for c in classes if counts.get(c, 0) >= MIN_BOXES and metrics[c]["precision"] >= MIN_PRECISION and metrics[c]["recall"] >= MIN_RECALL]
    meta = {"profile_id": profile_id, "classes": classes, "metrics": metrics, "usable": usable, "counts": counts,
            "train_images": len(tr), "val_images": len(val), "epochs": epochs, "device": device,
            "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"), "base": bool(os.getenv("SAM3_DETECTOR_BASE"))}
    torch.save(model.state_dict(), store.model_path(profile_id))
    (store.dir(profile_id) / "model.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def load_model(path: Path, meta: dict, device: str):
    import torch

    model = _build(len(meta["classes"]) + 1, torch)
    model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    return model.to(device).eval()


def predict(model, meta: dict, image: np.ndarray, classes: set[str], score_thr: float = 0.5) -> list[tuple[str, list[float], float]]:
    import torch

    device = next(model.parameters()).device
    names = ["__bg__"] + meta["classes"]
    with torch.no_grad():
        out = model([torch.from_numpy(image).permute(2, 0, 1).float().div(255.0).to(device)])[0]
    res = []
    for b, l, s in zip(out["boxes"].cpu(), out["labels"].cpu(), out["scores"].cpu()):
        name = names[int(l)]
        if name in classes and float(s) >= score_thr:
            res.append((name, [float(v) for v in b], float(s)))
    return res
