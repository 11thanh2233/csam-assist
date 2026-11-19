"""Tiện ích lúc chạy: nạp model lười + tự nhả VRAM khi rảnh, chạy khởi động nóng, thông tin GPU cho panel."""
from __future__ import annotations

import gc
import logging
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

log = logging.getLogger("sam3-assist")


class EngineProxy:
    """Đứng thay cho engine SAM3: nạp khi cần, nhả khỏi VRAM khi không dùng một lúc (idle_min phút, 0 = không nhả).

    Mọi thuộc tính và hàm được chuyển cho engine thật. Lần dùng đầu sau khi nhả sẽ nạp lại (mất vài chục giây).
    """

    def __init__(self, factory: Callable[[], Any], idle_min: float = 0, on_unload: Optional[Callable[[], None]] = None):
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_engine", None)
        object.__setattr__(self, "_lock", threading.RLock())
        object.__setattr__(self, "_last", time.time())
        object.__setattr__(self, "_idle_min", float(idle_min or 0))
        object.__setattr__(self, "_on_unload", on_unload)
        object.__setattr__(self, "_meta", {})
        object.__setattr__(self, "loads", 0)

    # ------------------------------------------------------------- vòng đời ---
    def ensure(self):
        with self._lock:
            object.__setattr__(self, "_last", time.time())
            if self._engine is None:
                started = time.time()
                eng = self._factory()
                object.__setattr__(self, "_engine", eng)
                object.__setattr__(self, "loads", self.loads + 1)
                self._meta.update(name=getattr(eng, "name", None), device=getattr(eng, "device", None))
                if self.loads > 1:
                    log.info("Đã nạp lại model sau khi nghỉ (%.0fs).", time.time() - started)
            return self._engine

    def unload(self, reason: str = "") -> bool:
        with self._lock:
            if self._engine is None:
                return False
            eng = self._engine
            object.__setattr__(self, "_engine", None)
            for attr in ("model", "processor", "_vision_cache"):
                if hasattr(eng, attr):
                    try:
                        setattr(eng, attr, None)
                    except Exception:  # noqa: BLE001
                        pass
            del eng
            if self._on_unload:
                self._on_unload()
            gc.collect()
            free_cuda()
            log.info("Đã nhả model khỏi bộ nhớ%s.", f" ({reason})" if reason else "")
            return True

    def idle_check(self) -> None:
        if self._idle_min > 0 and self._engine is not None and time.time() - self._last > self._idle_min * 60:
            self.unload(f"không dùng {self._idle_min:g} phút")

    @property
    def loaded(self) -> bool:
        return self._engine is not None

    @property
    def name(self):
        return self._meta.get("name") if self._engine is None else getattr(self._engine, "name", None)

    @property
    def device(self):
        return self._meta.get("device") if self._engine is None else getattr(self._engine, "device", None)

    def __getattr__(self, item):
        return getattr(self.ensure(), item)

    def __setattr__(self, key, value):
        setattr(self.ensure(), key, value)


def free_cuda() -> None:
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


def warmup(engine: Any) -> float:
    """Chạy thử một lượt trên ảnh nhỏ để lần gợi ý đầu tiên của người dùng không phải chờ khởi tạo CUDA."""
    started = time.time()
    try:
        img = np.full((256, 256, 3), 127, np.uint8)
        state = engine.encode(img)
        engine.segment_text(state, "object", 0.5)
    except Exception as exc:  # noqa: BLE001 - khởi động nóng lỗi thì bỏ qua, không chặn backend
        log.info("Bỏ qua khởi động nóng: %s", exc)
    return time.time() - started


class IdleWatcher(threading.Thread):
    def __init__(self, targets: list[Any], every: float = 30):
        super().__init__(daemon=True, name="idle-watcher")
        self.targets, self.every = targets, every
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(self.every):
            for t in self.targets:
                try:
                    t.idle_check()
                except Exception:  # noqa: BLE001
                    log.exception("Kiểm tra rảnh lỗi")


_gpu_cache: dict = {"at": 0.0, "value": None}


def gpu_info() -> Optional[dict]:
    """VRAM (MB), nhiệt độ, tên card. Đọc nvidia-smi tối đa 10 giây một lần. Không có GPU NVIDIA thì None."""
    now = time.time()
    if now - _gpu_cache["at"] < 10:
        return _gpu_cache["value"]
    _gpu_cache["at"] = now
    value = None
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run([exe, "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                                 creationflags=0x08000000 if sys.platform == "win32" else 0).stdout.strip().splitlines()
            if out:
                name, used, total, temp, util = [x.strip() for x in out[0].split(",")[:5]]
                value = {"name": name, "used_mb": int(float(used)), "total_mb": int(float(total)),
                         "temp_c": int(float(temp)) if temp.replace(".", "").isdigit() else None,
                         "util_pct": int(float(util)) if util.replace(".", "").isdigit() else None}
        except (OSError, ValueError, subprocess.SubprocessError):
            value = None
    if value is None:
        torch = sys.modules.get("torch")
        try:
            if torch is not None and torch.cuda.is_available():
                free, total = torch.cuda.mem_get_info()
                value = {"name": torch.cuda.get_device_name(0), "used_mb": int((total - free) / 2**20),
                         "total_mb": int(total / 2**20), "temp_c": None, "util_pct": None}
        except Exception:  # noqa: BLE001
            value = None
    _gpu_cache["value"] = value
    return value
