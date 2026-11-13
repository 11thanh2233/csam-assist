"""HTTP API của backend. Userscript trên trang CVAT gọi vào đây.

Chạy:  python -m backend            (hoặc scripts/run.sh, scripts/run.ps1)
"""
from __future__ import annotations

import base64
import hashlib
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any, Optional

import cv2
import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import __version__, pipeline, qa
from .config import settings
from .engines import Engine, create_engine
from .profiles import ProfileStore, resolve_labels

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sam3-assist")


class Runtime:
    engine: Optional[Engine] = None
    store: Optional[ProfileStore] = None
    cache: "OrderedDict[str, tuple[np.ndarray, Any]]" = OrderedDict()


rt = Runtime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    rt.store = ProfileStore(settings.profiles_dir)
    log.info("Đã nạp %d profile từ %s", len(rt.store.profiles), settings.profiles_dir)
    log.info("Đang nạp model (lần đầu có thể mất vài phút để tải checkpoint)...")
    rt.engine = create_engine(settings)
    log.info("Sẵn sàng. Engine: %s, thiết bị: %s", rt.engine.name, rt.engine.device)
    if settings.host not in ("127.0.0.1", "localhost") and not settings.api_key:
        log.warning("Backend mở ra ngoài máy này nhưng chưa đặt SAM3_API_KEY. Hãy đặt API key.")
    yield


app = FastAPI(title="SAM3 CVAT Assist", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def require_key(x_api_key: str = Header(default="")) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Sai hoặc thiếu API key (header X-API-Key).")


# ------------------------------------------------------------- schema -------
class LabelIn(BaseModel):
    id: int
    name: str
    type: str = "any"
    attributes: list[dict] = Field(default_factory=list)


class ResolveReq(BaseModel):
    profile_id: Optional[str] = None
    labels: list[LabelIn]
    overrides: dict[str, dict] = Field(default_factory=dict)


class ProposeReq(ResolveReq):
    image_b64: str
    existing: list[dict] = Field(default_factory=list)


class InteractiveReq(ResolveReq):
    image_b64: str
    label_id: int
    box: list[float]
    mode: str = "single"  # single | similar
    prompt: Optional[str] = None


class QaReq(ResolveReq):
    shapes: list[dict] = Field(default_factory=list)
    image_b64: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


# ------------------------------------------------------------- tiện ích -----
def decode_image(image_b64: str) -> np.ndarray:
    if "," in image_b64[:64]:  # chấp nhận cả dạng data:image/jpeg;base64,...
        image_b64 = image_b64.split(",", 1)[1]
    try:
        raw = np.frombuffer(base64.b64decode(image_b64), np.uint8)
        bgr = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Không đọc được ảnh: {exc}") from exc
    if bgr is None:
        raise HTTPException(status_code=400, detail="Dữ liệu ảnh không hợp lệ.")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def image_state(image_b64: str) -> tuple[np.ndarray, Any]:
    """Giải mã ảnh và tính đặc trưng; nhớ lại vài ảnh gần nhất để các lần gọi sau nhanh hơn."""
    key = hashlib.sha1(image_b64.encode()).hexdigest()
    if key in rt.cache:
        rt.cache.move_to_end(key)
        return rt.cache[key]
    image = decode_image(image_b64)
    state = rt.engine.encode(image)
    rt.cache[key] = (image, state)
    while len(rt.cache) > max(1, settings.cache_size):
        rt.cache.popitem(last=False)
    return image, state


def context(req: ResolveReq) -> tuple[dict, list[dict], list[dict]]:
    labels = [l.model_dump() for l in req.labels]
    profile = rt.store.get(req.profile_id, [l["name"] for l in labels])
    return profile, labels, resolve_labels(profile, labels, req.overrides)


# ------------------------------------------------------------- endpoint -----
@app.get("/health")
def health() -> dict:
    return {
        "ok": rt.engine is not None,
        "version": __version__,
        "engine": getattr(rt.engine, "name", None),
        "device": getattr(rt.engine, "device", None),
        "is_mock": getattr(rt.engine, "name", "") == "mock",
        "auth_required": bool(settings.api_key),
    }


@app.get("/profiles", dependencies=[Depends(require_key)])
def profiles() -> dict:
    return {"profiles": rt.store.summary()}


@app.post("/profiles/reload", dependencies=[Depends(require_key)])
def reload_profiles() -> dict:
    rt.store.reload()
    return {"profiles": rt.store.summary()}


@app.post("/resolve", dependencies=[Depends(require_key)])
def resolve(req: ResolveReq) -> dict:
    profile, _, cfgs = context(req)
    return {
        "profile_id": profile["id"],
        "title": profile.get("title", profile["id"]),
        "mode": profile.get("mode", "instance"),
        "labels": cfgs,
    }


@app.post("/propose", dependencies=[Depends(require_key)])
def propose(req: ProposeReq) -> dict:
    profile, _, cfgs = context(req)
    image, state = image_state(req.image_b64)
    result = pipeline.propose(rt.engine, state, image, profile, cfgs, req.existing)
    log.info("propose: profile=%s, %d đề xuất, %.1fs", profile["id"], len(result["proposals"]), result["seconds"])
    return result


@app.post("/interactive", dependencies=[Depends(require_key)])
def interactive(req: InteractiveReq) -> dict:
    profile, _, cfgs = context(req)
    cfg = next((c for c in cfgs if c["label_id"] == req.label_id), None)
    if cfg is None or not cfg["supported"]:
        raise HTTPException(status_code=400, detail="Label không hợp lệ hoặc chưa được hỗ trợ.")
    if len(req.box) != 4 or req.box[2] <= req.box[0] or req.box[3] <= req.box[1]:
        raise HTTPException(status_code=400, detail="Hộp không hợp lệ.")
    if profile.get("mode") == "semantic" or cfg["role"] in ("drivable_direct", "drivable_alt"):
        cfg = dict(cfg, role="object")
    image, state = image_state(req.image_b64)
    return pipeline.interactive(rt.engine, state, image, profile, cfg, req.box, req.mode, req.prompt)


@app.post("/qa", dependencies=[Depends(require_key)])
def run_qa(req: QaReq) -> dict:
    profile, labels, cfgs = context(req)
    width, height = req.width, req.height
    if req.image_b64:
        image = decode_image(req.image_b64)
        height, width = image.shape[:2]
    if not width or not height:
        raise HTTPException(status_code=400, detail="Thiếu kích thước ảnh.")
    return qa.run_qa(profile, cfgs, labels, req.shapes, int(width), int(height))


@app.get("/sam3-cvat-assist.user.js", response_class=PlainTextResponse)
def userscript() -> PlainTextResponse:
    if not settings.userscript_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy file userscript.")
    return PlainTextResponse(
        settings.userscript_path.read_text(encoding="utf-8"), media_type="text/javascript; charset=utf-8"
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    engine = getattr(rt.engine, "name", "?")
    device = getattr(rt.engine, "device", "?")
    return f"""<!doctype html><html lang="vi"><meta charset="utf-8"><title>SAM3 CVAT Assist</title>
<body style="font-family:system-ui;max-width:640px;margin:48px auto;padding:0 16px;line-height:1.6">
<h1>SAM3 CVAT Assist</h1>
<p>Backend đang chạy. Engine: <b>{engine}</b>, thiết bị: <b>{device}</b>, phiên bản {__version__}.</p>
<p><a href="/sam3-cvat-assist.user.js">Cài userscript vào Tampermonkey</a></p>
<p>Sau khi cài, mở một job trên CVAT. Panel "SAM3 Assist" hiện ở góc phải màn hình.</p>
</body></html>"""
