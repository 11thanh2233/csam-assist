"""HTTP API của backend. Userscript trên trang CVAT gọi vào đây.

Chạy:  python -m backend            (hoặc scripts/run.sh, scripts/run.ps1)
"""
from __future__ import annotations

import base64
import hashlib
from xml.etree.ElementTree import ParseError as ET_ParseError
import json
import logging
import re
import shutil
import time
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any, Optional

import cv2
import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from . import __version__, datatools, keypoints, pipeline, qa, tracking
from . import geometry as G
from .config import settings
from .engines import Engine, create_engine
from .engines.pose import LazyEngine, create_body_pose, create_face
from .guideline import llm as guideline_llm
from .guideline.library import Library
from .guideline.page import PAGE as GUIDELINE_PAGE
from .i18n import error_code, install_log_filter, set_lang, tr, translate_obj, translate_page, _lang
from .guideline.service import GuidelineService
from .learning import Learner, apply_learned
from .profiles import ProfileStore, norm, resolve_labels
from .runtime_tools import EngineProxy, IdleWatcher, gpu_info, warmup, release_for_3d, pick_onnx_providers, check_onnx_conflict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sam3-assist")
install_log_filter()                     # nhật ký luôn ghi tiếng Anh kỹ thuật, dù giao diện đang là tiếng Việt


class Runtime:
    engine: Optional[Engine] = None
    store: Optional[ProfileStore] = None
    body: Optional[LazyEngine] = None
    face: Optional[LazyEngine] = None
    guide: Optional[GuidelineService] = None
    learner: Optional[Learner] = None
    custom: Any = None          # CustomStore: dữ liệu + model riêng của dự án
    trainer: Any = None
    video: Optional[LazyEngine] = None   # SAM3 Video, chỉ nạp khi người dùng bật
    cache: "OrderedDict[str, tuple[np.ndarray, Any]]" = OrderedDict()
    cancelled: set = set()               # phiên SAM3 Video panel đã bỏ cuộc (0.9.4)
    memory: Any = None                   # trí nhớ của từng project (0.9.5)
    judge: Any = None                    # Claude xem ảnh (0.9.5)
    res: Any = None                      # quản lý VRAM (0.9.6)
    reid: Optional[LazyEngine] = None    # nhận lại người (0.9.6)
    depth: Optional[LazyEngine] = None   # độ sâu: ai đứng trước ai (0.9.6)
    wholebody: Optional[LazyEngine] = None   # pose toàn thân 133 điểm (0.9.6)


rt = Runtime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    rt.store = ProfileStore(settings.profiles_dir)
    log.info("Đã nạp %d profile từ %s", len(rt.store.profiles), settings.profiles_dir)
    log.info("Đang nạp model (lần đầu có thể mất vài phút để tải checkpoint)...")

    def on_unload() -> None:          # model vừa được nhả: bỏ cả ảnh/embedding đang giữ và các model phụ
        rt.cache.clear()
        for lazy in (rt.body, rt.face, rt.video, rt.reid, rt.depth, rt.wholebody):
            if lazy is not None:
                lazy.reset()

    from .runtime_tools import Resources

    rt.res = Resources()

    def make_engine():
        # 0.9.6: nạp lại SAM3 ảnh thì nhả SAM3 Video trước (hai model SAM3 cùng nằm trên GPU dễ tràn VRAM)
        if rt.video is not None and rt.video.engine is not None:
            rt.video.release("nạp lại SAM3 ảnh")
        return create_engine(settings)

    rt.engine = EngineProxy(make_engine, settings.idle_unload_min, on_unload)
    rt.engine.ensure()
    mock = rt.engine.name == "mock"
    if settings.warmup and not mock:
        log.info("Khởi động nóng xong sau %.1fs.", warmup(rt.engine))
    log.info("Sẵn sàng. Engine: %s, thiết bị: %s", rt.engine.name, rt.engine.device)
    watcher = IdleWatcher([rt.engine, rt.res])
    watcher.start()                     # luôn chạy: còn nhả SAM 3 Tracker (bấm điểm) khi lâu không dùng
    if settings.idle_unload_min > 0:
        log.info("Tự nhả model sau %g phút không dùng.", settings.idle_unload_min)
    rt.guide = GuidelineService(Library(settings.library_dir), guideline_llm.from_env())
    rt.learner = Learner(settings.library_dir / "learning")
    from .judge import Judge, Usage
    from .memory import ProjectMemory

    rt.memory = ProjectMemory(settings.library_dir / "memory")
    rt.judge = Judge(rt.guide.llm, Usage(settings.library_dir / "memory" / "_usage.json", settings.llm_daily_limit))
    from .custom_model import CustomStore, Trainer

    rt.custom = CustomStore(settings.library_dir / "custom")
    rt.trainer = Trainer(rt.custom, settings.device)
    info = rt.guide.llm_info(False)
    log.info("Soạn guideline: %d bài học trong thư viện, LLM: %s %s", len(rt.guide.lib.lessons), info["provider"], info["model"])
    rt.body = rt.res.register("body", LazyEngine(lambda: create_body_pose(mock, settings.pose_model, settings.device),
                                                 "tư thế người (ViTPose)" if not mock else "tư thế người (giả lập)", 800))
    rt.face = rt.res.register("face", LazyEngine(lambda: create_face(mock, settings.face_model),
                                                 "khuôn mặt (MediaPipe)" if not mock else "khuôn mặt (giả lập)", 200))
    from .engines.extras import create_depth, create_reid, create_wholebody

    rt.reid = rt.res.register("reid", LazyEngine(lambda: create_reid(mock, settings.reid_model, settings.device),
                                                 "nhận lại người (DINOv2)" if not mock else "nhận lại người (giả lập)", 400))
    rt.depth = rt.res.register("depth", LazyEngine(lambda: create_depth(mock, settings.depth_model, settings.device),
                                                   "độ sâu (Depth Anything V2)" if not mock else "độ sâu (giả lập)", 600))
    rt.wholebody = rt.res.register("wholebody", LazyEngine(lambda: create_wholebody(mock, settings.wholebody_model),
                                                           "pose toàn thân (RTMW)" if not mock else "pose toàn thân (giả lập)", 0))
    if settings.host not in ("127.0.0.1", "localhost") and not settings.api_key:
        log.warning("Backend mở ra ngoài máy này nhưng chưa đặt SAM3_API_KEY. Hãy đặt API key.")
    yield
    watcher.stop.set()


app = FastAPI(title="SAM3 Label Assist", version=__version__, lifespan=lifespan)
# Không bật CORS: userscript gọi backend qua GM_xmlhttpRequest (không bị CORS chặn) và trang duyệt guideline
# được backend phục vụ cùng origin. Bật CORS "*" sẽ cho MỌI trang web đang mở trong trình duyệt gọi được backend
# (đọc ảnh, ghi/xoá profile, ghi guideline) khi chưa đặt API key.


@app.middleware("http")
async def local_host_only(request, call_next):
    """Backend chỉ nghe trên máy này: chỉ nhận Host là localhost/127.0.0.1, chặn kiểu tấn công DNS rebinding
    (một trang web trỏ tên miền của nó về 127.0.0.1 để gọi backend như cùng origin)."""
    allowed = settings.allowed_hosts
    if allowed and "*" not in allowed:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in allowed:
            from fastapi.responses import PlainTextResponse

            return PlainTextResponse(tr(f"Host '{host}' không được phép. Mở backend bằng http://127.0.0.1:{settings.port} "
                                        "hoặc đặt SAM3_ALLOWED_HOSTS."), status_code=400)
    return await call_next(request)


@app.middleware("http")
async def language(request, call_next):
    """Ngôn ngữ theo header X-Lang của panel (hoặc ?lang= cho trang mở trong trình duyệt).

    - Phản hồi JSON được dịch sang ngôn ngữ đó (thông báo, cảnh báo, cờ của đề xuất).
    - Phản hồi lỗi luôn kèm "code": mã kỹ thuật cố định (E_...), giống nhau ở mọi ngôn ngữ.
    """
    token = set_lang(request.headers.get("x-lang") or request.query_params.get("lang"))
    try:
        response = await call_next(request)
        lang = _lang.get()
        ctype = response.headers.get("content-type", "")
        if not ctype.startswith("application/json") or (lang != "en" and response.status_code < 400):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            data = json.loads(body)
        except ValueError:
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers))
        if response.status_code >= 400 and isinstance(data, dict) and "code" not in data:
            data["code"] = error_code(data.get("detail") if isinstance(data.get("detail"), str) else "", response.status_code)
        data = translate_obj(data, lang)
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return JSONResponse(content=data, status_code=response.status_code, headers=headers)
    finally:
        _lang.reset(token)


def is_cuda_fault(exc: BaseException) -> bool:
    """Lỗi làm hỏng hẳn GPU của tiến trình (illegal memory access...): mọi lệnh GPU sau đó đều lỗi."""
    text = f"{type(exc).__name__}: {exc}"
    return "AcceleratorError" in text or "CUDA error" in text or "cudaError" in text


def restart_after_cuda_fault() -> None:
    """Thoát tiến trình để app desktop tự bật lại backend (tự bật lại khi backend tắt bất thường)."""
    import os
    import threading

    log.error("GPU gặp lỗi không phục hồi được trong tiến trình này: backend tự thoát để được bật lại.")
    threading.Timer(1.5, lambda: os._exit(3)).start()


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    """Lỗi chưa lường trước: trả lý do về panel thay vì chỉ một mã 500 trống."""
    log.exception("Lỗi khi xử lý yêu cầu")
    if is_cuda_fault(exc) and settings.auto_restart_on_gpu_fault:
        restart_after_cuda_fault()
        return JSONResponse(status_code=503, content={
            "detail": "GPU gặp lỗi (CUDA). Backend đang tự khởi động lại, đợi khoảng 1 phút rồi thử lại. "
                      "Lặp lại nhiều lần: cập nhật driver NVIDIA, tắt ép xung, đóng bớt phần mềm dùng GPU.",
            "code": "E_GPU_FAULT"})
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {str(exc)[:400]}",
                                                  "code": error_code(str(exc), 500)})


def require_key(x_api_key: str = Header(default="")) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Sai hoặc thiếu API key (header X-API-Key).")


# ------------------------------------------------------------- schema -------
class LabelIn(BaseModel):
    id: int
    name: str
    type: str = "any"
    attributes: list[dict] = Field(default_factory=list)
    sublabels: list[dict] = Field(default_factory=list)  # các điểm của label skeleton


class ResolveReq(BaseModel):
    profile_id: Optional[str] = None
    labels: list[LabelIn]
    overrides: dict[str, dict] = Field(default_factory=dict)
    use_learned: bool = True   # áp ngưỡng / chỉnh hộp tool đã học từ cách bạn sửa đề xuất
    user: str = ""             # tên đăng nhập CVAT: mỗi người một bộ "tự học" (khi nhiều người dùng chung backend)
    project_id: Optional[int] = None   # 0.9.4: project / task của job, để dùng profile đã tạo riêng cho project
    task_id: Optional[int] = None
    profile_inline: Optional[dict] = None   # profile chưa lưu (bản xem trước "Thử trên frame này")
    ignore_zones: list[list[float]] = Field(default_factory=list)   # vùng bỏ qua [x1,y1,x2,y2] (gương, quảng cáo...)
    quality: str = ""          # 0.9.6: mức cấu hình của panel: light | balanced | quality | max
    lidar: Optional[dict] = None   # 0.9.6: chỉnh của bạn cho job 3D (bảng cỡ, phạm vi, số điểm, prompt camera, hướng camera)


class ProposeReq(ResolveReq):
    image_b64: str
    existing: list[dict] = Field(default_factory=list)
    use_custom: bool = False   # dùng model riêng của dự án cho các class đã đạt chuẩn (nhanh hơn SAM3)
    use_llm: bool = False      # 0.9.5: cho Claude xem ảnh để duyệt đề xuất, điền thuộc tính, tag, tìm vật sót
    filename: str = ""         # tên file của frame: tool học quy ước tên file (vd _MVL = camera gương trái)


class InteractiveReq(ResolveReq):
    image_b64: str
    label_id: int
    box: list[float]
    mode: str = "single"  # single | similar
    prompt: Optional[str] = None
    existing: list[dict] = Field(default_factory=list)   # nhãn đã có trên frame: "Tìm tương tự" không đề xuất lại chúng
    negatives: list[list[float]] = Field(default_factory=list)   # hộp mẫu âm: vật bị nhận nhầm, SAM3 bỏ những vật giống chúng
    points: list[list[float]] = Field(default_factory=list)            # mode 'points': điểm thuộc vật
    negative_points: list[list[float]] = Field(default_factory=list)   # mode 'points': điểm KHÔNG thuộc vật


class TrackFrame(BaseModel):
    frame: int
    image_b64: str


class TrackDetectReq(ResolveReq):
    frames: list[TrackFrame]
    request_id: Optional[str] = None     # 0.9.6: panel bỏ cuộc (hết giờ, Dừng) thì backend dừng ở frame kế tiếp


class TrackBuildReq(ResolveReq):
    frames: list[dict]  # [{frame, dets}] từ /track/detect
    width: int
    height: int
    start: int
    stop: int
    targets: list[dict] = Field(default_factory=list)   # người bạn chọn: [{name, frame, box, label_id}] (0.9.4)
    thresholds: dict = Field(default_factory=dict)       # 0.9.6: ngưỡng che bạn sửa ở panel {occluded_hidden, outside_hidden}


class GuideStartReq(BaseModel):
    labels: list[LabelIn] = Field(default_factory=list)
    job: dict = Field(default_factory=dict)
    purpose: str = ""
    title: str = ""
    extra_terms: list[str] = Field(default_factory=list)
    send_images: bool = False


class GuideScanReq(BaseModel):
    run_id: str
    frames: list[TrackFrame]


class GuideRunReq(BaseModel):
    run_id: str


class GuideAnswerReq(GuideRunReq):
    answers: dict[str, Optional[str]]


class GuideLabelsReq(GuideRunReq):
    labels: Optional[list[LabelIn]] = None  # label hiện có của task, gửi lên sau khi vừa thêm label


class GuideApproveReq(GuideRunReq):
    markdown: str


class GuideFeedbackReq(BaseModel):
    text: str
    tags: list[str] = Field(default_factory=list)
    run_id: Optional[str] = None


class ImportPreviewReq(BaseModel):
    filename: str
    data_b64: str
    use_llm: bool = False          # người dùng cho phép gửi VĂN BẢN guideline tới LLM


class ImportSaveReq(BaseModel):
    analysis: dict


class ProjectProfileReq(BaseModel):
    labels: list[LabelIn]                  # label của task đang mở (nguồn taxonomy, không cần file JSON)
    project_id: Optional[int] = None
    task_id: Optional[int] = None
    name: str = ""                         # tên project / task
    filename: str = ""                     # guideline (tuỳ chọn)
    data_b64: str = ""
    use_llm: bool = False                  # cho phép gửi VĂN BẢN guideline tới LLM


class ProjectProfileSaveReq(BaseModel):
    profile: dict
    guideline_id: str = ""                 # guideline đi kèm (đã đọc ở bước xem trước): lưu văn bản + checklist


class LessonStatusReq(BaseModel):
    status: str


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


def context(req: ResolveReq, purpose: str = "frame") -> tuple[dict, list[dict], list[dict]]:
    if rt.res is not None:
        rt.res.touch(req.quality or None)
    labels = [l.model_dump() for l in req.labels]
    if req.profile_inline:
        profile = inline_profile(req.profile_inline)
    else:
        profile = rt.store.get(req.profile_id, [l["name"] for l in labels], purpose, [l["type"] for l in labels], labels,
                               project_id=req.project_id, task_id=req.task_id)
    cfgs = resolve_labels(profile, labels, req.overrides, purpose)
    limit = PROMPTS_BY_QUALITY.get(req.quality or "")
    if limit:
        # 0.9.6: mức cấu hình thấp chạy ít prompt hơn mỗi label (mỗi prompt là một lượt SAM3)
        for c in cfgs:
            if len(c.get("prompts") or []) > limit and not (req.overrides.get(str(c["label_id"])) or {}).get("prompts"):
                c["prompts"] = c["prompts"][:limit]
    if req.use_learned and rt.learner is not None:
        apply_learned(cfgs, learner_for(req.user).for_profile(profile["id"]), req.overrides)
    return profile, labels, cfgs


PROMPTS_BY_QUALITY = {"light": 1, "balanced": 2}
CROPS_BY_QUALITY = {"light": 0, "balanced": 3, "quality": 6, "max": 6}


def inline_profile(data: dict) -> dict:
    """Profile chưa lưu: chuẩn hoá như khi đọc từ file (chỉ mục theo tên)."""
    profile = json.loads(json.dumps(data))
    profile.setdefault("id", "preview")
    profile.setdefault("labels", [])
    profile.setdefault("rules", {})
    profile.setdefault("defaults", {})
    profile["_index"] = {}
    for entry in profile["labels"]:
        for key in [entry.get("name", ""), *entry.get("aliases", [])]:
            profile["_index"][norm(key)] = entry
    return profile


# ------------------------------------------------------------- tự học ------
_learners: dict[str, Learner] = {}


def _user_key(user: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", (user or "").strip())[:64]


def learner_for(user: str = "") -> Learner:
    """Bộ tự học của từng người (theo tên đăng nhập CVAT). Không có tên thì dùng bộ chung.
    Lần đầu có tên người dùng mà chưa ai có bộ riêng: chuyển dữ liệu của bộ chung sang người đó (máy một người dùng)."""
    key = _user_key(user)
    if not key or rt.learner is None:
        return rt.learner
    if key not in _learners:
        users = rt.learner.root / "users"
        mine = users / key
        if not mine.exists() and not (users.exists() and any(users.iterdir())) and (rt.learner.root / "events.jsonl").exists():
            mine.mkdir(parents=True)
            for name in ("events.jsonl", "model.json"):
                if (rt.learner.root / name).exists():
                    shutil.copy2(rt.learner.root / name, mine / name)
            if (rt.learner.root / "outcomes").exists():
                shutil.copytree(rt.learner.root / "outcomes", mine / "outcomes", dirs_exist_ok=True)
        _learners[key] = Learner(mine)
    return _learners[key]


class LearnEventsReq(BaseModel):
    events: list[dict]
    user: str = ""


class LearnReviewReq(BaseModel):
    user: str = ""
    job_id: int
    shapes: list[dict] = Field(default_factory=list)
    tracks: list[dict] = Field(default_factory=list)
    attr_names: dict[str, str] = Field(default_factory=dict)    # {spec_id: tên attribute}


class LearnToggleReq(BaseModel):
    user: str = ""
    profile_id: str
    label: str
    enabled: bool


class LearnForgetReq(BaseModel):
    user: str = ""
    profile_id: Optional[str] = None
    label: Optional[str] = None


@app.post("/learn/events", dependencies=[Depends(require_key)])
def learn_events(req: LearnEventsReq) -> dict:
    """Userscript gửi lúc người dùng bấm Ghi: đề xuất đã ghi (kèm id CVAT) và đề xuất bị bỏ tick."""
    return {"recorded": learner_for(req.user).record(req.events)}


@app.post("/learn/review", dependencies=[Depends(require_key)])
def learn_review(req: LearnReviewReq) -> dict:
    """So nhãn tool đã ghi với annotation hiện tại của job (sau khi người dùng sửa), rồi học lại."""
    return learner_for(req.user).review(req.job_id, req.shapes, req.tracks, req.attr_names)


@app.get("/learn/summary", dependencies=[Depends(require_key)])
def learn_summary(user: str = "") -> dict:
    return learner_for(user).summary()


@app.get("/learn/tool_shapes", dependencies=[Depends(require_key)])
def learn_tool_shapes(job_id: int, user: str = "") -> dict:
    """Chế độ reviewer: nhãn tool đã ghi cho job và những nhãn chưa ai sửa."""
    return learner_for(user).tool_shapes(job_id)


@app.post("/learn/toggle", dependencies=[Depends(require_key)])
def learn_toggle(req: LearnToggleReq) -> dict:
    lr = learner_for(req.user)
    lr.set_enabled(req.profile_id, req.label, req.enabled)
    return lr.summary()


@app.post("/learn/forget", dependencies=[Depends(require_key)])
def learn_forget(req: LearnForgetReq) -> dict:
    lr = learner_for(req.user)
    removed = lr.forget(req.profile_id, req.label)
    return dict(lr.summary(), removed=removed)


# ---------------------------------------------------- đầu nối Label Studio ---
def require_ls_auth(authorization: str = Header(default=""), x_api_key: str = Header(default="")) -> None:
    """Label Studio chỉ gửi được Basic auth: password = SAM3_API_KEY (hoặc header X-API-Key như các endpoint khác)."""
    if not settings.api_key or x_api_key == settings.api_key:
        return
    if authorization.lower().startswith("basic "):
        try:
            _, _, password = base64.b64decode(authorization[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            password = ""
        if password == settings.api_key:
            return
    raise HTTPException(status_code=401, detail="Sai hoặc thiếu key (Label Studio: Basic auth, password = SAM3_API_KEY).")


class LsPredictReq(BaseModel):
    tasks: list[dict] = Field(default_factory=list)
    label_config: str = ""
    project: Optional[str] = None
    params: dict = Field(default_factory=dict)


@app.get("/ls/health")
@app.get("/ls/")
def ls_health() -> dict:
    return {"status": "UP", "model_class": "SAM3CvatAssist", "version": __version__}


@app.post("/ls/setup", dependencies=[Depends(require_ls_auth)])
def ls_setup() -> dict:
    return {"model_version": f"sam3-assist-{__version__}"}


@app.post("/ls/predict", dependencies=[Depends(require_ls_auth)])
def ls_predict(req: LsPredictReq) -> dict:
    from . import labelstudio

    def run(image: np.ndarray, labels: list[dict]) -> dict:
        r = ResolveReq(labels=[LabelIn(id=l["id"], name=l["name"], type=l["type"]) for l in labels])
        profile, _, cfgs = context(r)
        state = rt.engine.encode(image)
        res = pipeline.propose(rt.engine, state, image, profile, cfgs, [])
        res["model_version"] = f"sam3-assist-{__version__}"
        return res

    try:
        return {"results": labelstudio.predict(req.tasks, req.label_config, run)}
    except (ValueError, ET_ParseError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/ls/webhook", dependencies=[Depends(require_ls_auth)])
def ls_webhook() -> dict:
    return {"status": "ok"}


# ------------------------------------------------------ dữ liệu thông minh ---
class FrameImage(BaseModel):
    frame: int
    image_b64: str


class FeaturesReq(BaseModel):
    frames: list[FrameImage] = Field(default_factory=list, max_length=48)


class RankFrame(BaseModel):
    frame: int
    features: dict
    labeled: int = 0
    flagged: int = 0
    low: int = 0


class RankReq(BaseModel):
    frames: list[RankFrame]
    k: Optional[int] = None
    motion_threshold: float = 0.06
    max_gap: int = 30


class SearchReq(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    frames: list[FrameImage] = Field(default_factory=list, max_length=16)
    threshold: float = 0.35


class AgreementReq(BaseModel):
    a: list[dict]
    b: list[dict]
    labels: list[dict] = Field(default_factory=list)
    iou: float = 0.5


@app.post("/data/features", dependencies=[Depends(require_key)])
def data_features(req: FeaturesReq) -> dict:
    """Đặc trưng nhẹ của từng frame (để xếp hạng frame đáng làm và tìm keyframe). Không dùng GPU."""
    return {"frames": [{"frame": f.frame, "features": datatools.features(decode_image(f.image_b64))} for f in req.frames]}


@app.post("/data/rank", dependencies=[Depends(require_key)])
def data_rank(req: RankReq) -> dict:
    frames = [f.model_dump() for f in req.frames]
    return {"ranking": datatools.rank(frames, req.k), **datatools.keyframes(frames, req.motion_threshold, req.max_gap)}


@app.post("/data/search", dependencies=[Depends(require_key)])
def data_search(req: SearchReq) -> dict:
    """Tìm frame bằng chữ (tiếng Anh) bằng SAM3. Panel gửi từng lô ảnh để có tiến độ và dừng được."""
    images = [(f.frame, decode_image(f.image_b64)) for f in req.frames]
    return {"query": req.query, "matches": datatools.search(rt.engine, images, req.query.strip(), req.threshold)}


@app.post("/qa/agreement", dependencies=[Depends(require_key)])
def qa_agreement(req: AgreementReq) -> dict:
    names = {int(l["id"]): str(l.get("name", l["id"])) for l in req.labels if "id" in l}
    return datatools.agreement(req.a, req.b, names, req.iou)


# ------------------------------------------------------------- endpoint -----
@app.get("/health")
def health() -> dict:
    return {
        "ok": rt.engine is not None,
        "version": __version__,
        "engine": getattr(rt.engine, "name", None),
        "device": getattr(rt.engine, "device", None),
        "is_mock": getattr(rt.engine, "name", "") == "mock",
        "guideline_llm": {k: v for k, v in rt.guide.llm_info(False).items() if k != "images"} if rt.guide else None,
        "auth_required": bool(settings.api_key),
        "gpu": gpu_info(),
        "model_loaded": getattr(rt.engine, "loaded", True),
        "idle_unload_min": settings.idle_unload_min,
        "vision_llm": bool(rt.judge and rt.judge.available()),
        "vram": rt.res.status() if rt.res else None,
        "llm_usage": rt.judge.usage.status() if rt.judge else None,
        # 0.9.8: job 3D dựng / lọc bằng gì (panel hiện một dòng ở Bước 1)
        "lidar3d": {"model3d": bool(_onnx3d_path()), "vision2d": bool(_onnx2d_path()), "sam3_mode": settings.sam3_3d_mode},
    }


@app.post("/selftest", dependencies=[Depends(require_key)])
def selftest() -> dict:
    """Kiểm tra nhanh sau khi cài: chạy model thật một lượt trên ảnh mẫu tự vẽ (vòng tròn đỏ trên nền sáng).

    Đạt khi model nạp được và chạy xong không lỗi; số vật tìm thấy chỉ để tham khảo (ảnh mẫu không phải ảnh thật)."""
    import time

    image = np.full((480, 640, 3), 235, np.uint8)
    cv2.circle(image, (320, 240), 90, (220, 40, 40), -1)
    started = time.time()
    try:
        state = rt.engine.encode(image)
        found = rt.engine.segment_text(state, "red circle", 0.3)
    except Exception as exc:  # noqa: BLE001 - báo nguyên nhân cho bảng điều khiển, không làm backend dừng
        log.exception("Self-test failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "engine": getattr(rt.engine, "name", None)}
    return {"ok": True, "found": len(found), "seconds": round(time.time() - started, 2),
            "engine": getattr(rt.engine, "name", None), "device": getattr(rt.engine, "device", None),
            "is_mock": getattr(rt.engine, "name", "") == "mock", "gpu": gpu_info()}


@app.get("/profiles", dependencies=[Depends(require_key)])
def profiles() -> dict:
    return {"profiles": rt.store.summary()}


@app.post("/profiles/reload", dependencies=[Depends(require_key)])
def reload_profiles() -> dict:
    rt.store.reload()
    return {"profiles": rt.store.summary()}


def _profile_op(fn, *args, **kwargs) -> dict:
    try:
        name = fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except (PermissionError, FileExistsError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dict(rt.store.manage_list(), file=name, profiles=rt.store.summary())


@app.get("/profiles/manage", dependencies=[Depends(require_key)])
def profiles_manage() -> dict:
    rt.store.reload()   # đọc lại thư mục: file người dùng vừa thả vào hoặc vừa xoá tay cũng hiện đúng
    return rt.store.manage_list()


@app.post("/profiles/{profile_id}/hide", dependencies=[Depends(require_key)])
def profile_hide(profile_id: str) -> dict:
    """Ẩn: chuyển file profile vào profiles/archive/. Hiện lại được bất cứ lúc nào."""
    return _profile_op(rt.store.hide, profile_id)


@app.post("/profiles/hidden/{filename}/show", dependencies=[Depends(require_key)])
def profile_show(filename: str) -> dict:
    return _profile_op(rt.store.show, filename)


@app.delete("/profiles/hidden/{filename}", dependencies=[Depends(require_key)])
def profile_delete_hidden(filename: str) -> dict:
    return _profile_op(rt.store.delete, hidden_file=filename)


@app.delete("/profiles/{profile_id}", dependencies=[Depends(require_key)])
def profile_delete(profile_id: str) -> dict:
    """Xoá hẳn file profile (không hoàn tác được)."""
    return _profile_op(rt.store.delete, profile_id)


@app.post("/resolve", dependencies=[Depends(require_key)])
def resolve(req: ResolveReq) -> dict:
    profile, labels, cfgs = context(req)
    track_profile, _, track_cfgs = context(req, "track")
    return {
        "profile_id": profile["id"],
        "title": profile.get("title", profile["id"]),
        "mode": profile.get("mode", "instance"),
        "labels": cfgs,
        # Các skeleton profile biết đặt điểm: tab Label dùng để người dùng chỉ tay khi tool không tự nhận ra label.
        "skeletons": [{"name": e["name"], "points": len(e.get("keypoints") or [])} for e in profile["labels"]
                      if profile.get("mode") in ("keypoints", "mixed") and e.get("shape") == "skeleton"],
        "project_profile": bool(profile.get("auto_project")),
        "checklist": len(profile.get("checklist") or []),
        "track": {
            "profile_id": track_profile["id"],
            "title": track_profile.get("title", track_profile["id"]),
            "labels": track_cfgs,
            # 0.9.6: ngưỡng % bị che của profile (guideline ghi, hoặc mặc định) để panel hiện và cho sửa
            "thresholds": _track_thresholds(track_profile),
        },
    }


def _track_thresholds(profile: dict) -> dict:
    rule = (profile.get("rules") or {}).get("tracking") or {}
    return {"occluded_hidden": float(rule.get("occluded_hidden", tracking.DEFAULT_OCCLUDED_HIDDEN)),
            "outside_hidden": float(rule.get("outside_hidden", tracking.DEFAULT_OUTSIDE_HIDDEN)),
            "source": "guideline" if "occluded_hidden" in rule else "default"}


@app.post("/propose", dependencies=[Depends(require_key)])
def propose(req: ProposeReq) -> dict:
    profile, _, cfgs = context(req)
    image, state = image_state(req.image_b64)
    if profile.get("mode") == "keypoints":
        result = keypoints.propose(rt.engine, state, image, profile, cfgs, req.existing, rt.body, rt.face, rt.wholebody)
    elif profile.get("mode") == "mixed":
        result = propose_mixed(req, profile, cfgs, image, state)
    else:
        custom = custom_proposals(req, profile, cfgs, image) if req.use_custom else None
        if custom:
            done_ids = {p["label_id"] for p in custom["proposals"]} | custom["label_ids"]
            rest = [dict(c, enabled=False) if c["label_id"] in done_ids else c for c in cfgs]
            result = pipeline.propose(rt.engine, state, image, profile, rest, req.existing)
            result["proposals"] = pipeline.finalize(result["proposals"] + custom["proposals"])
            result["warnings"] = result.get("warnings", []) + custom["warnings"]
            result["custom_labels"] = sorted(custom["names"])
        else:
            result = pipeline.propose(rt.engine, state, image, profile, cfgs, req.existing)
    if req.quality in REID_QUALITY and profile.get("mode") != "keypoints":
        # 0.9.6: hộp bàn tay có thuộc tính trái / phải: điền theo pose toàn thân (Claude xem sau vẫn sửa được)
        keypoints.hand_sides(rt.engine, state, image, result["proposals"], cfgs, rt.wholebody, result.setdefault("warnings", []))
    if req.use_llm:
        llm_review(req, profile, cfgs, image, state, result)
    if req.ignore_zones:
        before = len(result["proposals"])
        result["proposals"] = [p for p in result["proposals"] if not tracking.in_zones(p.get("bbox") or [0, 0, 0, 0], req.ignore_zones)]
        if before - len(result["proposals"]):
            result.setdefault("warnings", []).append(f"Bỏ {before - len(result['proposals'])} đề xuất nằm trong vùng bỏ qua.")
    log.info("propose: profile=%s, %d đề xuất, %.1fs", profile["id"], len(result["proposals"]), result["seconds"])
    rt.res.after_heavy()
    return result


def propose_mixed(req: ProposeReq, profile: dict, cfgs: list[dict], image: np.ndarray, state: Any) -> dict:
    """Profile của project: box/polygon/polyline bằng SAM3, skeleton bằng model tư thế/mặt, tag bằng luật tag ảnh."""
    from . import tags as tagrules

    started = time.time()
    skel = [c for c in cfgs if c["shape"] == "skeleton"]
    others = [c if c["shape"] not in ("skeleton", "tag") else dict(c, enabled=False) for c in cfgs]
    custom = custom_proposals(req, profile, others, image) if req.use_custom else None
    if custom:
        done_ids = {p["label_id"] for p in custom["proposals"]} | custom["label_ids"]
        others = [dict(c, enabled=False) if c["label_id"] in done_ids else c for c in others]
    result = pipeline.propose(rt.engine, state, image, profile, others, req.existing)
    if custom:
        result["proposals"] = pipeline.finalize(result["proposals"] + custom["proposals"])
        result["warnings"] = result.get("warnings", []) + custom["warnings"]
        result["custom_labels"] = sorted(custom["names"])
    if any(c["enabled"] and c["supported"] for c in skel):
        kp = keypoints.propose(rt.engine, state, image, profile, cfgs, req.existing, rt.body, rt.face, rt.wholebody)
        result["proposals"] += kp["proposals"]
        result["warnings"] = result.get("warnings", []) + kp["warnings"]
    mem = rt.memory.load(profile["id"]) if rt.memory else None
    guess = (lambda label, attr: rt.memory.token_guess(profile["id"], label, attr, req.filename, mem)) if mem and req.filename else None
    result["tags"] = tagrules.suggest(image, [c for c in cfgs if c["shape"] == "tag" and c["enabled"] and c["supported"]],
                                      guess=guess, filename=req.filename)
    result["seconds"] = round(time.time() - started, 2)
    return result


def llm_review(req: ProposeReq, profile: dict, cfgs: list[dict], image: np.ndarray, state: Any, result: dict) -> None:
    """0.9.5: Claude xem frame đã đánh số đề xuất, duyệt theo guideline + label của task + những gì project đã học."""
    from . import judge as J
    from . import tags as tagrules

    warnings = result.setdefault("warnings", [])
    if rt.judge is None or not rt.judge.available():
        warnings.append("Chưa bật Claude (cần Anthropic API key trong Cài đặt của app): tool chỉ dùng cách đo trên ảnh.")
        return
    started = time.time()
    h, w = image.shape[:2]
    tag_cfgs = [c for c in cfgs if c["shape"] == "tag" and c["enabled"] and c["supported"]]
    tags = result.setdefault("tags", [])
    labels = sorted({p.get("label_name") or "" for p in result["proposals"]} | {c["name"] for c in tag_cfgs})
    mem = rt.memory.load(profile["id"])
    memory = {"lessons": mem["lessons"], "examples": rt.memory.examples(profile["id"], labels, data=mem)}
    hints = {}
    if tag_cfgs:
        hints = {"mean_brightness_0_255": int(round(tagrules.brightness(image))), "infrared": bool(tagrules.is_infrared(image)),
                 "fisheye": bool(tagrules.fisheye_like(image))}
    text, images, items = J.build_request(image, result["proposals"], cfgs, profile, tags, tag_cfgs, memory,
                                          req.filename, hints, max_crops=CROPS_BY_QUALITY.get(req.quality or "", 6))
    try:
        answer = rt.judge.ask(text, images)
    except Exception as exc:  # noqa: BLE001 - Claude lỗi thì giữ kết quả của tool
        warnings.append(f"Claude không xem được ảnh: {str(exc)[:200]}")
        result["llm"] = {"used": False, "error": str(exc)[:200], "usage": rt.judge.usage.status()}
        return
    done = J.apply(answer, result["proposals"], items, cfgs, tags, tag_cfgs, w, h)
    added = fill_missing(state, image, profile, cfgs, done["missing"], result["proposals"])
    result["llm"] = {"used": True, "changed": done["changed"], "dropped": done["dropped"], "added": added,
                     "seconds": round(time.time() - started, 2), "usage": rt.judge.usage.status()}


def fill_missing(state: Any, image: np.ndarray, profile: dict, cfgs: list[dict], missing: list[dict], proposals: list[dict]) -> int:
    """Vật Claude thấy bị sót: SAM3 vẽ lại trong khung Claude chỉ, rồi dựng hình theo kiểu của label."""
    if not missing:
        return 0
    h, w = image.shape[:2]
    rules = profile.get("rules", {})
    by_id = {c["label_id"]: c for c in cfgs}
    dets, lanes = [], []
    for m in missing:
        cfg = by_id.get(m["label_id"])
        box = G.clamp_box(m["box"], w, h)
        if any(p.get("label_id") == cfg["label_id"] and p.get("bbox") and G.box_iou(p["bbox"], box) > 0.5 for p in proposals):
            continue
        try:
            found = rt.engine.segment_box(state, box, (cfg.get("prompts") or [None])[0], 0.2, None)
        except Exception:  # noqa: BLE001
            found = []
        if not found:
            continue
        best = max(found, key=lambda inst: G.box_iou(G.mask_bbox(inst.mask.astype(bool)) or [0, 0, 0, 0], box) + 0.2 * inst.score)
        mask = G.clean_mask(best.mask.astype(bool))
        mbox = G.mask_bbox(mask) or box
        det = pipeline.Det(cfg, mask, float(best.score), "claude", mbox)
        pipeline.add_flag(det.flags, "LLM_ADDED", "Claude thấy vật này bị sót; SAM3 vẽ lại trong vùng Claude chỉ. Kiểm tra kỹ."
                          + (f" ({m['why']})" if m.get("why") else ""))
        (lanes if cfg["shape"] == "polyline" else dets).append(det)
    new = pipeline.objects_to_proposals(dets, w, h, rules, [])
    if lanes:
        items = pipeline.build_lanes(lanes, image, rules, {norm(c["name"]): c for c in cfgs if c["supported"]})
        new += pipeline.lanes_to_proposals(items, w, h)
    for p in new:
        p["llm_added"] = True
    proposals += new
    return len(new)


# ------------------------------------------------- trí nhớ của project ---
class MemoryRecordReq(BaseModel):
    profile_id: str
    items: list[dict] = Field(default_factory=list)


class LessonReq(BaseModel):
    text: str


@app.post("/memory/record", dependencies=[Depends(require_key)])
def memory_record(req: MemoryRecordReq) -> dict:
    """Panel gửi giá trị bạn chốt lúc bấm Ghi (thuộc tính, tag, occluded, đề xuất bị bỏ) kèm giá trị tool đã gợi ý."""
    new = rt.memory.record(req.profile_id, req.items[:500])
    return {"corrections": new, "summary": rt.memory.summary(req.profile_id)}


@app.get("/memory/{profile_id}", dependencies=[Depends(require_key)])
def memory_get(profile_id: str) -> dict:
    return {"summary": rt.memory.summary(profile_id),
            "llm": {"available": bool(rt.judge and rt.judge.available()), **(rt.guide.llm_info(False) if rt.guide else {})},
            "usage": rt.judge.usage.status() if rt.judge else None}


@app.post("/memory/{profile_id}/distill", dependencies=[Depends(require_key)])
def memory_distill(profile_id: str) -> dict:
    """Claude tóm các lần bạn sửa thành vài luật ngắn cho project."""
    if rt.judge is None or not rt.judge.available():
        raise HTTPException(status_code=400, detail="Cần bật Claude (Anthropic API key trong Cài đặt của app) để tóm bài học.")
    if not rt.judge.usage.allow():
        raise HTTPException(status_code=429, detail="Đã dùng hết lượt Claude hôm nay.")
    profile = rt.store.profiles.get(profile_id) or {}
    try:
        lessons = rt.memory.distill(profile_id, rt.judge.llm, profile.get("guide_notes") or "")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Claude không tóm được bài học: {str(exc)[:200]}") from exc
    rt.judge.usage.add(getattr(rt.judge.llm, "last_usage", None))
    return {"lessons": lessons, "summary": rt.memory.summary(profile_id)}


@app.post("/memory/{profile_id}/lessons", dependencies=[Depends(require_key)])
def memory_add_lesson(profile_id: str, req: LessonReq) -> dict:
    """Bạn tự viết một luật cho project (Claude đọc luật này ở mọi lần xem ảnh sau)."""
    return {"lessons": rt.memory.add_lessons(profile_id, [req.text], source="you")}


@app.delete("/memory/{profile_id}/lessons/{lesson_id}", dependencies=[Depends(require_key)])
def memory_delete_lesson(profile_id: str, lesson_id: str) -> dict:
    return {"lessons": rt.memory.delete_lesson(profile_id, lesson_id)}


# ------------------------------------------------------------ model riêng ---
def custom_proposals(req: ProposeReq, profile: dict, cfgs: list[dict], image: np.ndarray) -> Optional[dict]:
    """Đề xuất của model riêng cho các class dạng hộp đã đạt chuẩn; None nếu chưa có model dùng được."""
    try:
        got = rt.trainer.model_for(profile["id"]) if rt.trainer else None
    except Exception as exc:  # noqa: BLE001 - model riêng lỗi thì SAM3 làm như bình thường
        log.warning("Không nạp được model riêng: %s", exc)
        return None
    if not got:
        return None
    model, meta = got
    usable = set(meta["usable"])
    # 0.9.4: model riêng tìm hộp; label polygon/mask thì SAM3 tách đúng vật trong hộp đó (nhanh hơn chạy prompt cả ảnh)
    targets = {c["name"]: c for c in cfgs if c["enabled"] and c["supported"] and c["shape"] in ("rectangle", "polygon", "mask")
               and c["role"] == "object" and c["name"] in usable}
    if not targets:
        return None
    from . import custom_model

    h, w = image.shape[:2]
    dets = []
    state = None
    for name, box, score in custom_model.predict(model, meta, image, set(targets), 0.3):
        cfg = targets[name]
        if score < cfg["threshold"]:
            continue
        box = G.clamp_box(box, w, h)
        mask = np.zeros((1, 1), bool)
        if cfg["shape"] != "rectangle":
            if state is None:
                _, state = image_state(req.image_b64)
            found = rt.engine.segment_box(state, box, None, 0.3, None)
            if not found:
                continue
            best = max(found, key=lambda inst: G.box_iou(G.mask_bbox(inst.mask.astype(bool)) or [0, 0, 0, 0], box))
            mask = G.clean_mask(best.mask.astype(bool))
        dets.append(pipeline.Det(cfg, mask, score, "custom", box))
    warnings: list[str] = []
    rules = profile.get("rules", {})
    props = pipeline.objects_to_proposals(dets, w, h, rules, warnings)
    for p in props:
        p["from_custom"] = True
    pipeline.mark_existing_duplicates(props, req.existing, float((rules.get("dedup") or {}).get("existing_iou", 0.7)))
    return {"proposals": props, "warnings": warnings, "names": set(targets), "label_ids": {c["label_id"] for c in targets.values()}}


class CustomSample(BaseModel):
    key: str
    image_b64: str
    boxes: list[dict] = Field(default_factory=list)       # [{label_id, bbox:[x1,y1,x2,y2]}]


class CustomAddReq(ResolveReq):
    samples: list[CustomSample] = Field(default_factory=list, max_length=16)


class CustomTrainReq(ResolveReq):
    epochs: int = Field(12, ge=1, le=100)


@app.post("/custom/dataset/add", dependencies=[Depends(require_key)])
def custom_add(req: CustomAddReq) -> dict:
    profile, labels, _ = context(req)
    names = {l["id"]: l["name"] for l in labels}
    samples = []
    for smp in req.samples:
        boxes = [{"label": names[b["label_id"]], "bbox": b["bbox"]} for b in smp.boxes if b.get("label_id") in names and len(b.get("bbox") or []) == 4]
        if boxes:
            samples.append((smp.key, decode_image(smp.image_b64), boxes))
    return {"profile_id": profile["id"], "dataset": rt.custom.add_samples(profile["id"], samples)}


@app.post("/custom/info", dependencies=[Depends(require_key)])
def custom_info(req: ResolveReq) -> dict:
    profile, _, _ = context(req)
    from . import custom_model

    meta = rt.custom.meta(profile["id"])
    if meta and "explain" not in meta:          # model huấn luyện bằng bản cũ: giải thích lại
        meta = dict(meta, explain=custom_model.explain(meta))
    return {"profile_id": profile["id"], "dataset": rt.custom.dataset_info(profile["id"]), "model": meta,
            "training": rt.trainer.status(), "has_base": bool(custom_model.base_weights())}


@app.post("/custom/train", dependencies=[Depends(require_key)])
def custom_train(req: CustomTrainReq) -> dict:
    profile, _, _ = context(req)
    try:
        return rt.trainer.start(profile["id"], req.epochs)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/custom/stop", dependencies=[Depends(require_key)])
def custom_stop() -> dict:
    rt.trainer.stop.set()
    return rt.trainer.status()


@app.post("/custom/clear", dependencies=[Depends(require_key)])
def custom_clear(req: ResolveReq) -> dict:
    profile, _, _ = context(req)
    rt.custom.clear(profile["id"])
    rt.trainer.models.pop(profile["id"], None)
    return {"profile_id": profile["id"], "dataset": rt.custom.dataset_info(profile["id"]), "model": None}


@app.post("/interactive", dependencies=[Depends(require_key)])
def interactive(req: InteractiveReq) -> dict:
    profile, _, cfgs = context(req)
    cfg = next((c for c in cfgs if c["label_id"] == req.label_id), None)
    if cfg is None or not cfg["supported"]:
        raise HTTPException(status_code=400, detail="Label không hợp lệ hoặc chưa được hỗ trợ.")
    if req.mode == "points":
        pos = [q[:2] for q in req.points if len(q) >= 2][:20]
        neg = [q[:2] for q in req.negative_points if len(q) >= 2][:20]
        if not pos:
            raise HTTPException(status_code=400, detail="Cần ít nhất một điểm thuộc vật.")
        if cfg["shape"] == "skeleton":
            raise HTTPException(status_code=400, detail="Bấm điểm chưa dùng được cho skeleton: hãy dùng Vẽ hộp.")
        if profile.get("mode") == "semantic" or cfg["role"] in ("drivable_direct", "drivable_alt"):
            cfg = dict(cfg, role="object")
        image, state = image_state(req.image_b64)
        return pipeline.interactive_points(rt.engine, state, image, profile, cfg, pos, neg, req.prompt)
    if len(req.box) != 4 or req.box[2] <= req.box[0] or req.box[3] <= req.box[1]:
        raise HTTPException(status_code=400, detail="Hộp không hợp lệ.")
    if cfg["shape"] == "skeleton":
        return keypoints.interactive(decode_image(req.image_b64), profile, cfg, cfgs, req.box, rt.body, rt.face, rt.wholebody)
    if profile.get("mode") == "semantic" or cfg["role"] in ("drivable_direct", "drivable_alt"):
        cfg = dict(cfg, role="object")
    image, state = image_state(req.image_b64)
    negatives = [b for b in req.negatives if len(b) == 4 and b[2] > b[0] and b[3] > b[1]][:10]
    return pipeline.interactive(rt.engine, state, image, profile, cfg, req.box, req.mode, req.prompt, req.existing, negatives)


@app.post("/track/detect", dependencies=[Depends(require_key)])
def track_detect(req: TrackDetectReq) -> dict:
    """Bước 1 của theo dõi: tìm đối tượng trên một nhóm frame (userscript gửi vài frame mỗi lần)."""
    profile, _, cfgs = context(req, "track")
    out, warnings = [], []
    for item in req.frames:
        if req.request_id and req.request_id in rt.cancelled:
            break
        image = decode_image(item.image_b64)
        dets = tracking.detect_frame(rt.engine, image, cfgs, warnings)
        enrich_dets(image, dets, req.quality, warnings)
        out.append({"frame": item.frame, "width": image.shape[1], "height": image.shape[0], "dets": dets})
    rt.res.after_heavy()
    return {"profile_id": profile["id"], "frames": out, "warnings": sorted(set(warnings)), "cancelled": len(out) < len(req.frames)}


class RescueFrame(BaseModel):
    frame: int
    image_b64: str
    boxes: list[dict] = Field(default_factory=list)      # [{label_id, box}] vị trí dự đoán


class TrackRescueReq(ResolveReq):
    frames: list[RescueFrame]
    request_id: Optional[str] = None


@app.post("/track/rescue", dependencies=[Depends(require_key)])
def track_rescue(req: TrackRescueReq) -> dict:
    """0.9.6: frame model mất người giữa hai đoạn của track: SAM3 tìm lại đúng vị trí dự đoán (ngưỡng thấp).
    Tìm thấy thì trả phát hiện (kèm phần mask) để tính % bị che; không thấy thì frame đó mới là outside."""
    _, _, cfgs = context(req, "track")
    by_id = {c["label_id"]: c for c in cfgs}
    out = []
    for item in req.frames:
        if req.request_id and req.request_id in rt.cancelled:
            break
        image = decode_image(item.image_b64)
        h, w = image.shape[:2]
        state = rt.engine.encode(image)
        dets = []
        for b in item.boxes:
            cfg = by_id.get(b.get("label_id"))
            if cfg is None:
                continue
            box = [float(v) for v in b["box"]]
            pad_w, pad_h = 0.1 * (box[2] - box[0]), 0.1 * (box[3] - box[1])
            probe = G.clamp_box([box[0] - pad_w, box[1] - pad_h, box[2] + pad_w, box[3] + pad_h], w, h)
            try:
                found = rt.engine.segment_box(state, probe, (cfg.get("prompts") or [None])[0], 0.25, None)
            except Exception:  # noqa: BLE001
                found = []
            best, best_iou = None, 0.2
            for inst in found:
                mb = G.mask_bbox(inst.mask.astype(bool))
                if mb and G.box_iou(mb, box) > best_iou:
                    best, best_iou = (inst, mb), G.box_iou(mb, box)
            if best is None:
                continue
            inst, mb = best
            mb = G.clamp_box(mb, w, h)
            dets.append({"label_id": cfg["label_id"], "box": [round(v, 2) for v in mb], "score": round(float(inst.score), 3),
                         "hist": tracking.appearance(image, mb), "rescued": True,
                         "fill": round(float(np.count_nonzero(inst.mask)) / max(G.box_area(mb), 1.0), 3)})
        enrich_dets(image, dets, req.quality, [])
        out.append({"frame": item.frame, "dets": dets})
    rt.res.after_heavy()
    return {"frames": out}


REID_QUALITY = ("balanced", "quality", "max")     # mức có véc-tơ ngoại hình DINOv2
DEPTH_QUALITY = ("quality", "max")                # mức có độ sâu (ai đứng trước ai)


def enrich_dets(image: np.ndarray, dets: list[dict], quality: Optional[str], warnings: list[str]) -> None:
    """0.9.6: thêm véc-tơ ngoại hình (nhận lại người sau khi khuất) và độ gần camera (ai che ai) cho mỗi phát hiện,
    theo mức cấu hình. Model chưa cài thì bỏ qua, báo một dòng; tool vẫn nối ID bằng màu như cũ."""
    if not dets or not quality:
        return
    if quality in REID_QUALITY and rt.reid is not None:
        model = rt.reid.get()
        if model is None:
            warnings.append("Chưa có model nhận lại người (DINOv2): nối ID theo màu áo. Cài ở mục Thành phần của app.")
        else:
            for d, emb in zip(dets, model.embed(image, [d["box"] for d in dets])):
                d["emb"] = emb
    if quality in DEPTH_QUALITY and rt.depth is not None and len(dets) > 1:
        model = rt.depth.get()
        if model is None:
            warnings.append("Chưa có model độ sâu (Depth Anything V2): không xét ai đứng trước ai. Cài ở mục Thành phần của app.")
        else:
            from .engines.extras import box_closeness

            depth = model.depth(image)
            for d in dets:
                d["near"] = box_closeness(depth, d["box"])


class TrackVideoStartReq(ResolveReq):
    pass


class TrackVideoFramesReq(ResolveReq):
    session_id: str
    frames: list[TrackFrame]


class TrackVideoEndReq(BaseModel):
    session_id: Optional[str] = None


def _video_tracker():
    if rt.video is None:
        if rt.engine is not None and rt.engine.name == "mock":
            from .engines.sam3_video import MockVideoTracker

            rt.video = rt.res.register("video", LazyEngine(lambda: MockVideoTracker(rt.engine).load(), "SAM3 Video (giả lập)", 4000))
        else:
            from .engines.sam3_video import Sam3VideoTracker

            rt.video = rt.res.register("video", LazyEngine(
                lambda: Sam3VideoTracker(settings.hf_model, settings.device, settings.video_dtype).load(), "SAM3 Video", 4000))
    if rt.video.engine is None and hasattr(rt.engine, "unload") and getattr(rt.engine, "name", "") != "mock":
        # 0.9.4: hai model SAM3 cùng nằm trên GPU dễ đầy VRAM (Windows mượn RAM thường, chạy chậm hàng chục lần):
        # nhả model gợi ý nhãn trước khi nạp SAM3 Video; lần gợi ý sau tự nạp lại.
        rt.engine.unload("nạp SAM3 Video")
    tracker = rt.video.get()
    if tracker is None:
        raise HTTPException(status_code=400, detail=f"Không dùng được SAM3 Video: {rt.video.error}. Tool chuyển về cách theo dõi thường.")
    return tracker


@app.post("/track/video/start", dependencies=[Depends(require_key)])
def track_video_start(req: TrackVideoStartReq) -> dict:
    """Theo dõi bằng SAM3 Video (thử nghiệm): mở phiên với prompt của các label đang bật."""
    _, _, cfgs = context(req, "track")
    prompts = sorted({p for c in cfgs if c["enabled"] and c["supported"] for p in c.get("prompts") or []})
    if not prompts:
        raise HTTPException(status_code=400, detail="Không có label nào có prompt để theo dõi.")
    tracker = _video_tracker()
    try:
        return {"session_id": tracker.start(prompts), "engine": tracker.name, "prompts": prompts}
    except Exception as exc:  # noqa: BLE001 - thiếu VRAM, bản transformers khác API...
        log.warning("SAM3 Video không mở được phiên: %s", exc)
        raise HTTPException(status_code=400, detail=f"Không dùng được SAM3 Video: {type(exc).__name__}: {str(exc)[:300]}") from exc


@app.post("/track/video/frames", dependencies=[Depends(require_key)])
def track_video_frames(req: TrackVideoFramesReq) -> dict:
    """Đưa tiếp vài frame (theo đúng thứ tự) vào phiên. Trả về cùng dạng /track/detect, phát hiện có thêm obj_id."""
    profile, _, cfgs = context(req, "track")
    tracker = _video_tracker()
    out = []
    try:
        for item in sorted(req.frames, key=lambda x: x.frame):
            if req.session_id in rt.cancelled:
                raise HTTPException(status_code=409, detail="Đã dừng theo dõi.")
            image = decode_image(item.image_b64)
            raw = tracker.add_frame(req.session_id, image)
            out.append({"frame": item.frame, "width": image.shape[1], "height": image.shape[0],
                        "dets": tracking.video_dets(raw, image, cfgs)})
    except HTTPException:
        tracker.end(req.session_id)
        raise
    except Exception as exc:  # noqa: BLE001
        tracker.end(req.session_id)
        log.warning("SAM3 Video lỗi giữa chừng: %s", exc)
        if is_cuda_fault(exc):
            raise
        raise HTTPException(status_code=400, detail=f"SAM3 Video lỗi: {type(exc).__name__}: {str(exc)[:300]}") from exc
    return {"profile_id": profile["id"], "frames": out, "warnings": []}


@app.post("/track/cancel", dependencies=[Depends(require_key)])
def track_cancel(req: TrackVideoEndReq) -> dict:
    """Panel bỏ cuộc (hết giờ, bấm Dừng): backend dừng phiên SAM3 Video ngay ở frame kế tiếp, không giữ GPU (0.9.4)."""
    if req.session_id:
        rt.cancelled.add(req.session_id)
        if len(rt.cancelled) > 50:
            rt.cancelled.pop()
    if rt.video is not None and rt.video.engine is not None:
        rt.video.engine.end(req.session_id)
    return {"ok": True}


@app.post("/track/video/end", dependencies=[Depends(require_key)])
def track_video_end(req: TrackVideoEndReq) -> dict:
    if rt.video is not None and rt.video.engine is not None:
        rt.video.engine.end(req.session_id)
    return {"ok": True}


@app.post("/track/build", dependencies=[Depends(require_key)])
def track_build(req: TrackBuildReq) -> dict:
    """Bước 2: nối các phát hiện thành track, áp luật box/occluded/outside/keyframe của guideline."""
    profile, _, cfgs = context(req, "track")
    custom = {k: float(v) for k, v in (req.thresholds or {}).items()
              if k in ("occluded_hidden", "outside_hidden") and isinstance(v, (int, float)) and 0 < float(v) <= 1}
    if custom:
        profile = dict(profile, rules=dict(profile.get("rules") or {}))
        profile["rules"]["tracking"] = dict(profile["rules"].get("tracking") or {}, **custom)
    result = tracking.build_tracks(req.frames, cfgs, profile, req.width, req.height, req.start, req.stop,
                                   targets=req.targets, zones=req.ignore_zones)
    track_rule = (profile.get("rules") or {}).get("tracking") or {}
    result["thresholds_source"] = "panel" if custom else ("guideline" if "occluded_hidden" in track_rule else "default")
    log.info("track: profile=%s, %d track", profile["id"], len(result["tracks"]))
    return dict(result, profile_id=profile["id"])


# ------------------------------------------------------ cuboid 3D (0.9.4) -----
class LidarFrame(BaseModel):
    frame: int
    data_b64: str
    name: str = ""
    context_b64: str = ""       # 0.9.6: ảnh camera của frame (zip context image của CVAT)


class LidarDetectReq(ResolveReq):
    frames: list[LidarFrame] = Field(default_factory=list, max_length=8)
    debug: bool = False         # 0.9.6: trả thêm điểm nhìn từ trên + cụm bị bỏ (bản đồ nhỏ, Kiểm tra dữ liệu)
    use_llm: bool = False       # 0.9.8: Claude chấm các cuboid của frame (gợi ý từng frame)


class LidarTrackReq(ResolveReq):
    frames: list[dict]          # [{frame, objects}] từ /lidar/detect


class LidarEvalReq(ResolveReq):
    frames: list[LidarFrame] = Field(default_factory=list, max_length=8)
    truth: list[dict] = Field(default_factory=list)      # [{frame, label_id, points}] cuboid bạn đã đánh


class LidarFeedbackReq(ResolveReq):
    dropped: list[dict] = Field(default_factory=list)    # [{cls, size}]
    kept: list[dict] = Field(default_factory=list)
    clear: bool = False


# ---- 0.9.7: phiên 3D chạy theo lô (spec 3D v2 mục 4) ----
class LidarSessionInitReq(ResolveReq):
    frames: list[LidarFrame] = Field(default_factory=list, max_length=40)   # lô đầu (có thể rỗng)
    use_llm: bool = True


class LidarSessionFramesReq(ResolveReq):
    session_id: str = ""                    # không bắt buộc: đường dẫn đã có; panel có gửi thì kiểm khớp
    frames: list[LidarFrame] = Field(default_factory=list, max_length=40)


class LidarSessionStepReq(ResolveReq):
    session_id: str = ""
    frames: Optional[list[int]] = None      # chỉ chạy các frame này (None = các frame chưa chạy)
    limit: int = 20                         # số frame mỗi lô (10-20 theo spec)
    offset_deg: Optional[float] = None


class LidarSessionLlmReq(ResolveReq):
    session_id: str = ""
    batch_size: int = 8                     # số track mỗi lô gửi Claude (6-10 theo spec)
    use_llm: bool = True


class LidarSessionCancelReq(BaseModel):
    session_id: str


class LidarSessionIdReq(BaseModel):
    session_id: str


LIDAR_KEYS = ("ranges", "min_points_scale", "min_points_by_range", "max_base_gap_m", "local_ground")


def _enter_3d() -> list[str]:
    """0.9.7: dọn VRAM trước khi chạy 3D. Nhả model phụ 2D đang giữ GPU (pose, mặt, SAM3 Video, re-ID, độ sâu, pose toàn
    thân) để (a) 3D có chỗ, (b) không còn luồng 2D tranh CUDA context với luồng 3D — nguyên nhân `cudaErrorIllegalAddress`.
    SAM3 ảnh được giữ nếu đang nạp sẵn (cần cho đối chiếu camera); không bao giờ nạp mới ở đây."""
    released = release_for_3d([rt.body, rt.face, rt.video, rt.reid, rt.depth, rt.wholebody])
    if released:
        log.info("Job 3D: đã nhả %s để dành VRAM.", ", ".join(released))
    return released


def _lidar_setup(req: ResolveReq) -> tuple[dict, dict, dict, dict]:
    from . import lidar

    labels = [l.model_dump() for l in req.labels]
    profile = inline_profile(req.profile_inline) if req.profile_inline else rt.store.get(
        req.profile_id, [l["name"] for l in labels], "frame", [l["type"] for l in labels], labels, project_id=req.project_id, task_id=req.task_id)
    rule = dict((profile.get("rules") or {}).get("lidar") or {})
    mine = req.lidar or {}
    for k in LIDAR_KEYS:                                # chỉnh của bạn (tab Label, Tự chỉnh) thắng profile
        if mine.get(k) is not None:
            rule[k] = dict(rule.get(k) or {}, **mine[k]) if isinstance(mine[k], dict) else mine[k]
    table = dict(rule.get("sizes") or lidar.DEFAULT_SIZES)
    for k, rows in (mine.get("sizes") or {}).items():
        if rows:
            table[lidar.class_key(k)] = rows
    if rt.memory is not None:
        rule["dropped"] = rt.memory.lidar_drops(profile["id"])
    wanted, prompts = {}, {}
    for l in labels:
        if (l.get("type") or "any").lower() in ("cuboid", "any"):
            key = lidar.class_key(l["name"])
            if key in table:
                wanted[key] = l["id"]
            p = (mine.get("prompts") or {}).get(str(l["id"])) or (mine.get("prompts") or {}).get(l["name"])
            if p:
                prompts[l["id"]] = str(p)
    names = {l["id"]: l["name"] for l in labels}
    return profile, rule, {k: [tuple(map(tuple, r)) for r in v] for k, v in table.items()}, \
        {"wanted": wanted, "names": names, "prompts": prompts, "calib": mine.get("cameras") or {}, "offset": mine.get("offset_deg")}


def _read_cloud(item: LidarFrame):
    from . import lidar

    try:
        return lidar.read_points(base64.b64decode(item.data_b64.split(",", 1)[-1]), item.name)
    except (ValueError, KeyError, IndexError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=f"Không đọc được point cloud của frame {item.frame}: {exc}") from exc


@app.post("/lidar/config", dependencies=[Depends(require_key)])
def lidar_config(req: ResolveReq) -> dict:
    """0.9.6: tab Label của job 3D: bảng cỡ, phạm vi, số điểm, prompt camera của từng label cuboid (đã gồm chỉnh của bạn)."""
    from . import lidar, lidar_assist

    profile, rule, table, info = _lidar_setup(req)
    from_guide = bool(((profile.get("rules") or {}).get("lidar") or {}).get("sizes"))
    ranges = dict(lidar.DEFAULT_RANGES, **(rule.get("ranges") or {}))
    scale = rule.get("min_points_scale") or {}
    rows = []
    for l in req.labels:
        if (l.type or "any").lower() not in ("cuboid", "any"):
            continue
        key = lidar.class_key(l.name)
        rows.append({"id": l.id, "name": l.name, "cls": key, "in_table": key in table,
                     "sizes": [[list(r) for r in row] for row in table.get(key) or []],
                     "range": ranges.get(key, 60.0), "min_points_scale": float(scale.get(key, 1.0)),
                     "prompt": info["prompts"].get(l.id) or lidar_assist.CLASS_PROMPTS.get(key, key)})
    return {"profile_id": profile["id"], "labels": rows, "sizes_source": "guideline" if from_guide else "default",
            "min_points_by_range": rule.get("min_points_by_range") or lidar.DEFAULT_MIN_POINTS,
            "max_base_gap_m": float(rule.get("max_base_gap_m", 0.5)), "remembered": len(rule.get("dropped") or []),
            "lookalikes": lidar_assist.LOOKALIKES}


@app.post("/lidar/detect", dependencies=[Depends(require_key)])
def lidar_detect(req: LidarDetectReq) -> dict:
    """Cuboid đề xuất cho từng frame point cloud (CVAT 3D). Có ảnh camera thì đối chiếu thêm bằng SAM3 (0.9.6)."""
    from . import lidar, lidar_assist

    profile, rule, table, info = _lidar_setup(req)
    if not info["wanted"]:
        raise HTTPException(status_code=400, detail="Task không có label cuboid nào khớp bảng kích thước (Car, Truck, Bus, Pedestrian...). "
                                                    "Thêm kích thước cho label ở tab Label, hoặc tạo profile kèm guideline có bảng kích thước.")
    released = _enter_3d()
    out, warnings = [], []
    camera_info = None
    offset = info["offset"]
    model3d = None
    if _onnx3d_path():
        model3d = _get_lidar3d(_onnx3d_path())
        if not model3d.ready:
            warnings.append(f"Model 3D ONNX không dùng được, dùng hình học: {model3d.error or '?'}")
            model3d = None
    vision = None
    if _onnx2d_path():
        vision = _get_vision2d(_onnx2d_path())
        if not vision.ready:
            warnings.append(f"Bộ dò vật 2D không nạp được: {vision.error or '?'}")
            vision = None
    llm = _llm_for_3d() if req.use_llm else None
    if req.use_llm and llm is None:
        warnings.append("Claude AI chưa sẵn sàng: cần API key Anthropic trong Cài đặt của app.")
    for item in sorted(req.frames, key=lambda x: x.frame):
        points = _read_cloud(item)
        res = None
        if model3d is not None and not req.debug:
            try:
                res = lidar_onnx_detect(model3d, points, info["wanted"], rule)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Model 3D lỗi ở frame {item.frame}, dùng hình học: {str(exc)[:120]}")
        if res is None:
            res = lidar.detect(points, info["wanted"], table, rule, debug=req.debug)
        for i, o in enumerate(res["objects"], 1):
            o["id"] = i
        images = []
        if item.context_b64 and res["objects"]:
            try:
                images = lidar_assist.read_context(base64.b64decode(item.context_b64.split(",", 1)[-1]))
            except Exception as exc:  # noqa: BLE001 - ảnh camera lỗi thì bỏ bước này, vẫn trả cuboid
                warnings.append(f"Không đọc được ảnh camera của frame {item.frame}: {str(exc)[:160]}")
        if images:
            try:
                chk = _camera_check_frame(images, res["objects"], info, rule, offset, vision)
                offset = chk.get("offset", offset)          # các frame sau dùng lại hướng đã dò
                camera_info = {k: chk.get(k) for k in ("offset", "offset_source", "cameras", "note", "engine")}
                if chk.get("note") and chk["note"] not in warnings:
                    warnings.append(chk["note"])
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Không đối chiếu được ảnh camera của frame {item.frame}: {str(exc)[:160]}")
        if llm is not None and res["objects"]:
            note = _llm_judge_frame(llm, res["objects"], images, info, rule, offset, req)
            if note and note not in warnings:
                warnings.append(note)
        frame_out = {"frame": item.frame, "objects": res["objects"], "points": int(len(points)), "stats": res.get("stats"),
                     "engine": res.get("engine", "geometry")}
        for k in ("bev", "rejected", "info"):
            if k in res:
                frame_out[k] = res[k]
        out.append(frame_out)
        warnings += [w for w in res["warnings"] if w not in warnings]
    rt.res.after_heavy()
    return {"profile_id": profile["id"], "frames": out, "warnings": warnings, "camera": camera_info, "freed": released,
            "rule": {"ranges": dict(lidar.DEFAULT_RANGES, **(rule.get("ranges") or {})),
                     "min_points_by_range": rule.get("min_points_by_range") or lidar.DEFAULT_MIN_POINTS,
                     "min_points_scale": rule.get("min_points_scale") or {}, "max_base_gap_m": rule.get("max_base_gap_m", 0.5),
                     "remembered": len(rule.get("dropped") or [])},
            "reasons": lidar.REASONS}


def lidar_onnx_detect(model, points, wanted: dict, rule: dict) -> dict:
    from . import lidar_onnx

    return lidar_onnx.detect(model, points, wanted, rule)


SAM3_3D_NEED_MB = 3500          # 0.9.9: SAM3 xem một ảnh camera cần khoảng 3-3.5 GB (trước đặt cứng 6 GB)


def _sam3_3d_need(total_mb: Optional[int] = None) -> int:
    """Ngưỡng VRAM trống để nạp SAM3 ở job 3D (chế độ auto): số bạn đặt, không thì min(3.5 GB, 55% tổng VRAM)."""
    if settings.sam3_3d_min_free_mb > 0:
        return settings.sam3_3d_min_free_mb
    if total_mb is None:
        from .runtime_tools import cuda_mem

        total_mb = (cuda_mem() or {}).get("total_mb")
    return min(SAM3_3D_NEED_MB, int(0.55 * total_mb)) if total_mb else SAM3_3D_NEED_MB


def _is_oom(exc: BaseException) -> bool:
    return isinstance(exc, MemoryError) or "OutOfMemory" in type(exc).__name__ or "out of memory" in str(exc).lower()


def _release_sam3_after_oom(where: str) -> str:
    """0.9.9: SAM3 thiếu VRAM ở job 3D: nhả SAM3, dọn GPU, trả câu báo. Cuboid vẫn trả về như thường."""
    from .runtime_tools import free_cuda

    try:
        unload = getattr(rt.engine, "unload", None)
        if unload is not None:
            unload("thiếu VRAM ở job 3D")
    except Exception:  # noqa: BLE001
        pass
    free_cuda()
    log.warning("Job 3D: SAM3 thiếu VRAM khi %s, đã nhả.", where)
    return (f"Thiếu VRAM khi {where}: đã nhả SAM3, cuboid vẫn giữ nhưng chưa được đối chiếu ảnh camera. "
            "Đóng bớt ứng dụng dùng GPU, hoặc cài RT-DETR ở trang Thành phần (nhẹ hơn nhiều).")


def _sam3_for_3d() -> tuple[bool, str]:
    """Đối chiếu camera bằng SAM3 ở job 3D (khi chưa có RT-DETR). 0.9.9: theo chế độ trong Cài đặt của app
    (SAM3_3D_SAM3_MODE): auto = nạp khi VRAM trống >= ngưỡng tính theo card; always = luôn nạp; off = không dùng.
    Nạp mà thiếu VRAM thì nhả SAM3 và báo lý do, không làm backend sập."""
    from .runtime_tools import cuda_mem

    if rt.engine is None:
        return False, "Backend chưa có engine SAM3: bỏ qua bước đối chiếu ảnh camera."
    if settings.sam3_3d_mode == "off":
        return False, ("Bạn đã tắt SAM3 cho job 3D (Cài đặt của app): bỏ đối chiếu ảnh camera. Cài RT-DETR ở trang "
                       "Thành phần để vẫn lọc rác bằng ảnh.")
    if getattr(rt.engine, "loaded", True):
        return True, ""
    if settings.sam3_3d_mode != "always":
        m = cuda_mem() or {}
        free, need = m.get("free_mb"), _sam3_3d_need(m.get("total_mb"))
        if free is None:
            return False, ("Không đọc được VRAM (máy không có GPU NVIDIA?): không nạp SAM3 cho job 3D. Chọn \"Luôn nạp "
                           "SAM3\" trong Cài đặt của app nếu vẫn muốn, hoặc cài RT-DETR ở trang Thành phần.")
        if free < need:
            return False, (f"VRAM trống {free} MB < {need} MB: chưa nạp SAM3, bỏ đối chiếu ảnh camera. Đóng bớt ứng dụng "
                           "dùng GPU, chọn \"Luôn nạp SAM3\" trong Cài đặt của app, hoặc cài RT-DETR ở trang Thành phần.")
    try:
        rt.engine.ensure()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        if _is_oom(exc):
            return False, _release_sam3_after_oom("nạp SAM3")
        return False, f"Không nạp được SAM3 cho đối chiếu camera: {str(exc)[:120]}"


def _camera_check_frame(images, objects, info, rule, offset, vision) -> dict:
    """Đối chiếu cuboid của một frame với ảnh camera: bộ dò 2D ONNX trước (nhẹ, không đụng SAM3); không có thì SAM3."""
    from . import camera_rig, lidar_assist, lidar_vision

    names = [n for n, _ in images]
    if vision is not None:
        rig = camera_rig.build_rig(names, info["calib"], None)
        if not rig["cameras"]:
            return {"offset": offset, "note": "Không nhận ra tên camera (CAM_FRONT, CAM_BACK...). Nhập hướng camera ở tab Label."}
        allowed = {lidar_vision.COCO_FOR_3D[k] for k in info["wanted"] if k in lidar_vision.COCO_FOR_3D} or None
        res = lidar_vision.verify(vision, images, objects, rig, float(offset or 0.0), allowed)
        lidar_vision.apply(res, objects)
        for o in objects:                                   # panel đọc o.camera (chip "camera ✓ / ?")
            r = (res.get("results") or {}).get(o["id"])
            if r:
                o["camera"] = {"verdict": {"confirmed": "confirmed", "none": "contradicted"}.get(r["verdict"], "unknown"),
                               "camera": r.get("camera"), "prompt": o.get("cls"), "seen": "không có xe / người",
                               "engine": "rtdetr"}
        return {"offset": offset, "offset_source": rig["source"], "cameras": names, "engine": "rtdetr",
                "note": "; ".join(rig.get("notes") or [])}
    ok, why = _sam3_for_3d()
    if not ok:
        return {"offset": offset, "offset_source": "none", "cameras": names, "note": why, "engine": "skip"}
    try:
        chk = lidar_assist.check(rt.engine, images, objects, info["prompts"], info["calib"], offset)
    except Exception as exc:  # noqa: BLE001 - 0.9.9: thiếu VRAM giữa chừng thì nhả SAM3, vẫn trả cuboid
        if not _is_oom(exc):
            raise
        return {"offset": offset, "offset_source": "none", "cameras": names, "engine": "skip",
                "note": _release_sam3_after_oom("SAM3 xem ảnh camera")}
    lidar_assist.apply_camera(objects, chk)
    chk["engine"] = "sam3"
    return chk


def _llm_for_3d():
    return rt.guide.llm if (rt.guide is not None and rt.guide.llm is not None) else None


def _llm_judge_frame(llm, objects, images, info, rule, offset, req) -> str:
    """D2 (0.9.8): Claude chấm các cuboid của MỘT frame (gợi ý từng frame) bằng ảnh camera có khung chiếu + BEV.
    Claude bảo bỏ -> bỏ tick; đổi class -> đổi label (nếu task có label đó). Trả câu ghi chú cho panel."""
    from . import camera_rig, judge_3d

    profile, _rule, _table, _info = _lidar_setup(req)
    names = info["names"]
    tracks, cams = [], {}
    rig = camera_rig.build_rig([n for n, _ in images], info["calib"], None) if images else {"cameras": []}
    for o in objects:
        tracks.append({"id": o["id"], "center": o["center"], "size": o["size"], "yaw": o.get("yaw", 0.0),
                       "points": o.get("points", 0), "cls": o.get("cls"), "label_name": names.get(o["label_id"]),
                       "start": 0, "end": 0, "flags": o.get("flags") or [], "checked": o.get("checked", True)})
        p = camera_rig.project(o["center"], o["size"], rig["cameras"], float(offset or 0.0)) if rig["cameras"] else None
        img = dict(images).get(p["camera"]) if p else None
        if img is not None:
            h, w = img.shape[:2]
            cams[o["id"]] = {p["camera"]: (img, [max(0.0, (p["u"] - p["half"]) * w), 0.3 * h, min(float(w), (p["u"] + p["half"]) * w), 0.7 * h])}
    try:
        res = judge_3d.phase1(llm, tracks, names, profile, cams, None, 8)
    except Exception as exc:  # noqa: BLE001 - D3: lỗi Claude không làm hỏng gợi ý
        return f"Claude lỗi: {type(exc).__name__}: {str(exc)[:120]}"
    by_name = {str(n).strip().lower(): i for i, n in names.items()}
    changed = dropped = 0
    errors = set()
    for o in objects:
        r = res.get(o["id"]) or {}
        if str(r.get("reason") or "").startswith("Claude lỗi"):
            errors.add(r["reason"])
            continue
        o["llm"] = {k: r.get(k) for k in ("keep", "label", "confidence", "reason", "bucket")}
        if r.get("keep") is False:
            o["checked"] = False
            o.setdefault("flags", []).append({"code": "UNCERTAIN_CLASS", "message": f"Claude: {r.get('reason') or 'không phải vật cần gán'}"})
            dropped += 1
        elif r.get("label") and by_name.get(str(r["label"]).strip().lower()) not in (None, o["label_id"]):
            o["label_id"] = by_name[str(r["label"]).strip().lower()]
            o.setdefault("flags", []).append({"code": "UNCERTAIN_CLASS", "message": f"Claude đổi class: {r['label']} ({r.get('reason') or ''})"})
            changed += 1
    if errors:
        return sorted(errors)[0]
    return f"Claude đã xem {len(objects)} cuboid: đổi class {changed}, đề nghị bỏ {dropped}."


@app.post("/lidar/track", dependencies=[Depends(require_key)])
def lidar_track(req: LidarTrackReq) -> dict:
    from . import lidar, tracking_3d

    profile, rule, _, info = _lidar_setup(req)
    _enter_3d()
    # 0.9.7: Kalman + Hungarian thay cho ghép tham lam; luật occluded/outside và khóa kích thước ±5% đọc từ profile
    res = tracking_3d.build(req.frames, rule, info["names"])
    rt.res.after_heavy()
    return dict(res, profile_id=profile["id"], engine=res.get("engine"))


@app.post("/lidar/evaluate", dependencies=[Depends(require_key)])
def lidar_evaluate(req: LidarEvalReq) -> dict:
    """0.9.6: chạy lại trên các frame bạn đã đánh cuboid, so với cuboid của bạn, đề nghị chỉnh (bạn bấm Áp dụng mới đổi)."""
    from . import lidar_assist

    profile, rule, table, info = _lidar_setup(req)
    if not info["wanted"]:
        raise HTTPException(status_code=400, detail="Task không có label cuboid nào khớp bảng kích thước.")
    frames = [(item.frame, _read_cloud(item)) for item in req.frames]
    truth = []
    for t in req.truth:
        pts = t.get("points") or []
        if len(pts) >= 9:
            truth.append(dict(lidar_assist.truth_from_cvat(pts), frame=int(t["frame"]), label_id=int(t["label_id"])))
    if not truth:
        raise HTTPException(status_code=400, detail="Chưa có cuboid nào bạn đã đánh ở các frame này.")
    return dict(lidar_assist.evaluate(frames, truth, info["wanted"], table, rule), profile_id=profile["id"])


@app.post("/lidar/feedback", dependencies=[Depends(require_key)])
def lidar_feedback(req: LidarFeedbackReq) -> dict:
    """0.9.6: cuboid bạn bỏ / giữ lúc ghi: nhớ để lần sau bỏ tick sẵn vật hay bị nhầm (thùng rác, nhà vệ sinh di động...)."""
    profile, _, _, _ = _lidar_setup(req)
    if rt.memory is None:
        return {"drops": 0}
    if req.clear:
        rt.memory.clear_lidar(profile["id"])
        return {"drops": 0}
    return rt.memory.record_lidar(profile["id"], req.dropped, req.kept)


# --------------------------- 0.9.7: phiên 3D theo lô (/lidar/v2/session/*) ---------------------------
def _session_of(sid: str, req) -> tuple[Any, dict, dict, dict, Any]:
    """Nạp phiên + profile. Lỗi khi session_id sai hoặc phiên đã bị dọn.

    Phiên lấy theo `sid` trong đường dẫn: `session_id` trong body là tuỳ chọn (panel không gửi, mặc định ""),
    có gửi thì phải khớp với đường dẫn."""
    from . import lidar_session

    if getattr(req, "session_id", "") and req.session_id != sid:
        raise HTTPException(status_code=400, detail="session_id trong đường dẫn và thân request không khớp.")
    sess = lidar_session.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="Phiên 3D không còn trong bộ nhớ (backend đã khởi động lại, hoặc "
                                                    "quá 8 phiên). Hãy tạo phiên mới.")
    profile, rule, table, info = _lidar_setup(req)
    return sess, profile, rule, table, info


@app.post("/lidar/v2/session/init", dependencies=[Depends(require_key)])
def lidar_v2_init(req: LidarSessionInitReq) -> dict:
    """Mở phiên 3D: nhận lô frame đầu, trả session_id + tóm tắt (số frame, camera, hướng chiếu, GPU)."""
    from . import camera_rig, lidar_assist, lidar_session    # 0.9.8: thiếu lidar_assist làm mất mọi ảnh camera

    profile, rule, table, info = _lidar_setup(req)
    if not info["wanted"]:
        raise HTTPException(status_code=400, detail="Task không có label cuboid nào khớp bảng kích thước.")
    released = _enter_3d()
    sess = lidar_session.create(info["wanted"], info["names"], table, rule)
    # ảnh camera của lô đầu (để dò trục chiếu ngay)
    contexts = {}
    cam_names: set[str] = set()
    for item in req.frames:
        pts = _read_cloud(item)
        ctx = []
        if item.context_b64:
            try:
                ctx = lidar_assist.read_context(base64.b64decode(item.context_b64.split(",", 1)[-1]))
                cam_names.update(n for n, _ in ctx)
            except Exception as exc:  # noqa: BLE001
                sess.errors.append(f"frame {item.frame} ảnh camera lỗi: {str(exc)[:120]}")
        lidar_session.add_frames(sess, [(item.frame, pts)], {item.frame: ctx} if ctx else {})
    rig = camera_rig.build_rig(sorted(cam_names), info["calib"], None) if cam_names else {"cameras": [], "source": "none", "notes": []}
    rule["cameras"] = camera_rig.to_panel(rig) if rig["cameras"] else None
    rule["camera_meta"] = None
    summary = sess.summary(gpu_info(), {"source": rig["source"], "notes": rig["notes"], "preset": rig.get("preset")})
    summary["freed"] = released
    summary["camera_names"] = sorted(cam_names)
    return summary


@app.post("/lidar/v2/session/{sid}/frames", dependencies=[Depends(require_key)])
def lidar_v2_frames(sid: str, req: LidarSessionFramesReq) -> dict:
    """Nhận thêm lô frame (point cloud + ảnh camera). Gọi nhiều lần cho tới khi đủ cả job."""
    from . import lidar_assist, lidar_session

    sess, _profile, _rule, _table, info = _session_of(sid, req)
    batch, contexts = [], {}
    for item in req.frames:
        pts = _read_cloud(item)
        ctx = []
        if item.context_b64:
            try:
                ctx = lidar_assist.read_context(base64.b64decode(item.context_b64.split(",", 1)[-1]))
            except Exception as exc:  # noqa: BLE001
                sess.errors.append(f"frame {item.frame} ảnh camera lỗi: {str(exc)[:120]}")
        batch.append((item.frame, pts))
        if ctx:
            contexts[item.frame] = ctx
    return lidar_session.add_frames(sess, batch, contexts)


@app.post("/lidar/v2/session/{sid}/geometry", dependencies=[Depends(require_key)])
def lidar_v2_geometry(sid: str, req: LidarSessionStepReq) -> dict:
    """Dựng cuboid + tracking theo lô frame. Gọi lại tới khi remaining == 0. Hủy an toàn giữa chừng."""
    from . import lidar_session

    sess, profile, rule, table, info = _session_of(sid, req)
    if req.offset_deg is not None:
        sess.rule["offset_deg"] = float(req.offset_deg)
    sess.cancelled = False
    predict = _lidar_predict(sess)
    return lidar_session.run_geometry(sess, req.frames, req.limit, predict=predict,
                                      after=lambda _f: rt.res.after_heavy())


@app.post("/lidar/v2/session/{sid}/vision", dependencies=[Depends(require_key)])
def lidar_v2_vision(sid: str, req: LidarSessionStepReq) -> dict:
    """Đối chiếu camera (bộ dò 2D ONNX) theo lô track. Chưa chọn model ONNX thì bỏ qua, không lỗi."""
    from . import camera_rig, lidar_session, lidar_vision

    sess, profile, rule, table, info = _session_of(sid, req)
    if not sess.contexts:
        return {"session_id": sess.id, "skipped": "job không có ảnh camera — bỏ qua đối chiếu camera"}
    path = _onnx2d_path()
    if not path:
        return {"session_id": sess.id, "skipped": "chưa có bộ dò vật 2D (RT-DETR) — cài ở trang Thành phần của app"}
    vision = _get_vision2d(path)
    if not getattr(vision, "ready", False):
        return {"session_id": sess.id, "skipped": f"bộ dò vật 2D không nạp được: {vision.error or '?'}"}
    cams = sorted({n for ctx in sess.contexts.values() for n, _ in ctx})
    rig = camera_rig.build_rig(cams, sess.rule.get("cameras") or {}, sess.rule.get("camera_meta"))
    offset = req.offset_deg if req.offset_deg is not None else sess.rule.get("offset_deg")
    try:
        return lidar_session.run_vision(sess, vision, rig, offset, req.limit)
    except Exception as exc:  # noqa: BLE001 - A3: lỗi bất ngờ không làm phiên trả 500, panel đi tiếp
        log.exception("Đối chiếu camera phiên 3D lỗi")
        sess.errors.append(f"đối chiếu camera lỗi: {type(exc).__name__}: {str(exc)[:120]}")
        for t in (sess.track or {}).get("tracks") or []:
            t.setdefault("vision2d", {"verdict": "no_image"})
        return {"session_id": sess.id, "error": str(exc)[:200], "remaining": 0, "done": dict(sess.done)}


@app.post("/lidar/v2/session/{sid}/llm_batch", dependencies=[Depends(require_key)])
def lidar_v2_llm(sid: str, req: LidarSessionLlmReq) -> dict:
    """Claude Pha 1+2 trên cấp track (chỉ gọi khi người dùng bật LLM). Không có LLM thì trả lý do."""
    from . import lidar_session

    sess, profile, rule, table, info = _session_of(sid, req)
    if not req.use_llm:
        return {"session_id": sess.id, "skipped": "bạn đã tắt Claude AI cho job này"}
    llm = _llm_for_3d()
    mem = None
    if rt.memory is not None:
        try:
            mem = {"lessons": rt.memory.lessons(profile["id"])}
        except Exception:  # noqa: BLE001 - trí nhớ lỗi: vẫn chạy Claude không lessons
            mem = None
    return lidar_session.run_llm(sess, llm, profile, mem, max(4, min(10, req.batch_size)))


@app.post("/lidar/v2/session/{sid}/result", dependencies=[Depends(require_key)])
def lidar_v2_result(sid: str, req: ResolveReq) -> dict:
    """Kết quả gộp của phiên: tracks + 3 rổ + cảnh báo, để panel duyệt rồi ghi vào CVAT."""
    from . import lidar_session

    sess = lidar_session.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="Phiên 3D không còn trong bộ nhớ. Hãy tạo phiên mới.")
    return lidar_session.result(sess)


@app.post("/lidar/v2/session/{sid}/cancel", dependencies=[Depends(require_key)])
def lidar_v2_cancel(sid: str, req: ResolveReq) -> dict:
    """Hủy an toàn: dừng các lô tiếp theo, GIỮ kết quả các lô đã xong để người dùng xem."""
    from . import lidar_session

    sess = lidar_session.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên.")
    sess.cancelled = True
    return {"session_id": sess.id, "cancelled": True, "done": dict(sess.done)}


@app.post("/lidar/v2/session/{sid}/close", dependencies=[Depends(require_key)])
def lidar_v2_close(sid: str, req: ResolveReq) -> dict:
    """Ghi xong: xóa phiên để trả bộ nhớ (point cloud chỉ nằm trong RAM backend)."""
    from . import lidar_session

    ok = lidar_session.drop(sid)
    return {"session_id": sid, "closed": ok}


# bộ dò 2D ONNX dùng chung cho job 3D: nạp một lần, nhả khi phiên 3D xong
_vision2d: dict = {"path": None, "obj": None}


def _onnx2d_path() -> Optional[str]:
    """Đường dẫn model 2D ONNX (None nếu chưa có). 0.9.8: file bạn chọn trong app (SAM3_ONNX2D_PATH) thắng, không thì
    RT-DETR mà bộ cài tải về (SAM3_RTDETR_MODEL), cuối cùng models/rtdetr_r18vd.onnx khi chạy từ mã nguồn."""
    import os
    from pathlib import Path

    for p in (os.getenv("SAM3_ONNX2D_PATH", ""), os.getenv("SAM3_RTDETR_MODEL", ""), str(settings.rtdetr_model)):
        if p and Path(p).is_file():
            return p
    return None


def _onnx3d_path() -> Optional[str]:
    """0.9.8: model LiDAR ONNX (CenterPoint pillar nuScenes) bạn chọn trong app; None thì dùng hình học như cũ."""
    import os
    from pathlib import Path

    p = os.getenv("SAM3_ONNX3D_PATH", "")
    return p if p and Path(p).is_file() else None


_lidar3d: dict = {"path": None, "obj": None}


def _get_lidar3d(path: str):
    """Bộ dò 3D ONNX dùng chung (nạp một lần, đổi file thì nạp lại)."""
    from . import lidar_onnx, runtime_tools

    if _lidar3d["obj"] is not None and _lidar3d["path"] == path:
        return _lidar3d["obj"]
    if _lidar3d["obj"] is not None:
        _lidar3d["obj"].release("đổi model")
    providers, options = runtime_tools.pick_onnx_providers()
    m = lidar_onnx.CenterPointONNX(path, providers, options).load()
    _lidar3d["path"], _lidar3d["obj"] = path, m
    if m.error:
        log.warning("Model 3D ONNX: %s", m.error)
    return m


def _lidar_predict(sess):
    """0.9.8 (F1): có model CenterPoint thì dựng cuboid bằng model, lỗi một frame thì frame đó quay về hình học."""
    from . import lidar, lidar_onnx

    path = _onnx3d_path()
    if not path:
        return None
    model = _get_lidar3d(path)
    if not model.ready:
        sess.errors.append(f"model 3D ONNX không dùng được, dùng hình học: {model.error or '?'}")
        return None

    def predict(points):
        try:
            return lidar_onnx.detect(model, points, sess.labels, sess.rule)
        except Exception as exc:  # noqa: BLE001
            sess.errors.append(f"model 3D lỗi, frame này dùng hình học: {type(exc).__name__}: {str(exc)[:100]}")
            return lidar.detect(points, sess.labels, sess.table, sess.rule, debug=False)

    return predict


def _get_vision2d(path: str):
    from . import lidar_vision, runtime_tools

    if _vision2d["obj"] is not None and _vision2d["path"] == path:
        return _vision2d["obj"]
    if _vision2d["obj"] is not None:
        _vision2d["obj"].release("đổi model")
    providers, options = runtime_tools.pick_onnx_providers()
    v = lidar_vision.Vision2D(path, providers, options).load()
    _vision2d["path"], _vision2d["obj"] = path, v
    if v.error:
        log.warning("Model 2D ONNX: %s", v.error)
    return v


# ------------------------------------------------------- soạn guideline -----
def require_key_or_query(x_api_key: str = Header(default=""), key: str = Query(default="")) -> None:
    """Ảnh trong trang duyệt được tải bằng thẻ <img> nên khoá đi theo địa chỉ thay vì header."""
    if settings.api_key and settings.api_key not in (x_api_key, key):
        raise HTTPException(status_code=401, detail="Sai hoặc thiếu API key.")


def guide_call(fn, *args):
    try:
        return fn(*args)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0]) if exc.args else "Không tìm thấy.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def known_prompts() -> dict[str, list[str]]:
    """Prompt đã được chỉnh cho từng tên label trong các profile sẵn có (dùng lại khi khảo sát)."""
    from .profiles import norm

    out: dict[str, list[str]] = {}
    for profile in rt.store.profiles.values():
        if profile.get("mode") in ("tracking", "keypoints"):
            continue
        for entry in profile.get("labels", []):
            if "prompts" in entry:
                for name in [entry["name"], *entry.get("aliases", [])]:
                    out.setdefault(norm(name), list(entry["prompts"] or []))
    return out


@app.post("/guideline/start", dependencies=[Depends(require_key)])
def guideline_start(req: GuideStartReq) -> dict:
    labels = [l.model_dump() for l in req.labels]
    return rt.guide.start(labels, req.job, req.purpose, req.extra_terms, req.send_images, known_prompts(), req.title)


@app.post("/guideline/scan", dependencies=[Depends(require_key)])
def guideline_scan(req: GuideScanReq) -> dict:
    frames = [(item.frame, decode_image(item.image_b64)) for item in req.frames]
    return guide_call(rt.guide.scan, req.run_id, rt.engine, frames)


@app.post("/guideline/draft", dependencies=[Depends(require_key)])
def guideline_draft(req: GuideRunReq) -> dict:
    return guide_call(rt.guide.draft, req.run_id)


@app.post("/guideline/answer", dependencies=[Depends(require_key)])
def guideline_answer(req: GuideAnswerReq) -> dict:
    return guide_call(rt.guide.answer, req.run_id, req.answers)


@app.post("/guideline/approve", dependencies=[Depends(require_key)])
def guideline_approve(req: GuideApproveReq) -> dict:
    return guide_call(rt.guide.approve, req.run_id, req.markdown)


@app.post("/guideline/labels", dependencies=[Depends(require_key)])
def guideline_labels(req: GuideLabelsReq) -> dict:
    """Bộ label CVAT ứng với bản guideline: toàn bộ (để tạo task mới) và phần còn thiếu (để thêm vào task này)."""
    view = None
    if req.labels is not None:
        view = guide_call(rt.guide.sync_labels, req.run_id, [l.model_dump() for l in req.labels])
    plan = guide_call(rt.guide.labels, req.run_id)
    return dict(plan, view=view)


@app.post("/guideline/use_profile", dependencies=[Depends(require_key)])
def guideline_use_profile(req: GuideRunReq) -> dict:
    """Lưu profile sinh từ bản nháp vào thư mục profiles/ để tool gợi ý nhãn dùng ngay."""
    from .guideline import draft as guideline_draft_rules

    run = guide_call(rt.guide._load, req.run_id)
    if guideline_draft_rules.task_of(run) == "keypoints":
        # Skeleton cần danh sách điểm và model riêng: lấy từ profile keypoints có sẵn khớp bộ label của bản guideline.
        plan = guide_call(rt.guide.labels, req.run_id)
        base = rt.store.pick([l["name"] for l in plan["labels"]], "frame", [l["type"] for l in plan["labels"]], plan["labels"])
        result = rt.guide.keypoint_profile(run, base)
        path = None
        if result["yaml"]:
            path = settings.profiles_dir / f"{result['profile_id']}.yaml"
            path.write_text(result["yaml"], encoding="utf-8")
            rt.store.reload()
        return dict(result, path=str(path) if path else None)
    text = rt.guide.profile_yaml(run)
    path = settings.profiles_dir / f"auto_{run['id']}.yaml"
    path.write_text(text, encoding="utf-8")
    rt.store.reload()
    return {"profile_id": f"auto_{run['id']}", "path": str(path), "yaml": text}


@app.post("/guideline/feedback", dependencies=[Depends(require_key)])
def guideline_feedback(req: GuideFeedbackReq) -> dict:
    return {"lesson": guide_call(rt.guide.feedback, req.text, req.tags, req.run_id)}


# ----------------------------------------------------- nạp guideline có sẵn -
def _import_paths(guide_id: str) -> dict:
    lib = rt.guide.lib.root
    return {"reference": lib / "reference" / f"{guide_id}.md", "record": lib / "imported" / f"{guide_id}.yaml",
            "pending": lib / "imported" / ".pending" / f"{guide_id}.md", "profile": settings.profiles_dir / f"gl_{guide_id}.yaml"}


def _safe_id(guide_id: str) -> str:
    from .guideline.importer import slug

    if slug(guide_id) != guide_id:
        raise HTTPException(status_code=400, detail="Mã guideline không hợp lệ.")
    return guide_id


@app.post("/guideline/import/preview", dependencies=[Depends(require_key)])
def guideline_import_preview(req: ImportPreviewReq) -> dict:
    """Đọc file guideline và rút ra label, shape, định nghĩa, checklist. Chưa ghi gì vào tool: người dùng xem và sửa trước."""
    from .guideline import importer
    from .profiles import norm

    prompts = known_prompts()
    lookup = lambda name: prompts.get(norm(name))  # noqa: E731
    try:
        data = base64.b64decode(req.data_b64.split(",", 1)[-1], validate=False)
        if req.filename.lower().endswith(".json"):   # file taxonomy (JSON label của CVAT, COCO categories...)
            analysis, markdown = importer.analyze_taxonomy(data, req.filename, lookup)
        else:
            markdown = importer.read_document(req.filename, data)
            analysis = importer.analyze(markdown, req.filename, lookup)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    notes: list[str] = []
    if req.use_llm and rt.guide.llm is not None and analysis.get("source") != "taxonomy":
        try:
            notes = importer.llm_enrich(rt.guide.llm, markdown, analysis)
        except Exception as exc:  # noqa: BLE001 - LLM lỗi thì vẫn trả bản rút bằng luật
            notes = [f"LLM lỗi, chỉ dùng phần rút bằng luật: {str(exc)[:200]}"]
    paths = _import_paths(analysis["id"])
    paths["pending"].parent.mkdir(parents=True, exist_ok=True)
    paths["pending"].write_text(markdown, encoding="utf-8")
    profile = importer.build_profile(analysis)
    # Profile có sẵn nào đã khớp bộ label này? Khi hoà điểm, "Tự chọn profile" giữ profile có sẵn.
    names = [l["name"] for l in analysis["labels"]]
    current = rt.store.pick(names, "frame", [l["shape"] for l in analysis["labels"]]) if names else None
    overlap = current["title"] if current and current.get("labels") and current["id"] not in ("generic", f"gl_{analysis['id']}") else None
    return {
        "analysis": analysis, "notes": notes, "excerpt": markdown[:1500], "overlap": overlap,
        "exists": paths["record"].exists() or paths["reference"].exists(),
        "profile_id": profile["id"] if profile else None, "profile_exists": paths["profile"].exists(),
        "llm": rt.guide.llm_info(False),
    }


# ------------------------------------------------ profile của project (0.9.4) ---
@app.post("/profile/project/preview", dependencies=[Depends(require_key)])
def project_profile_preview(req: ProjectProfileReq) -> dict:
    """Dựng profile cho project từ label của task (+ guideline nếu có). Chưa ghi gì: người dùng xem, thử rồi mới lưu."""
    from . import autoprofile
    from .guideline import importer

    labels = [l.model_dump() for l in req.labels]
    if not labels:
        raise HTTPException(status_code=400, detail="Task chưa có label nào.")
    markdown, analysis, notes = "", {}, []
    if req.data_b64 and req.filename:
        prompts = known_prompts()
        try:
            data = base64.b64decode(req.data_b64.split(",", 1)[-1], validate=False)
            markdown = importer.read_document(req.filename, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        analysis = importer.analyze(markdown, req.filename, lambda name: prompts.get(norm(name)))
        paths = _import_paths(analysis["id"])
        paths["pending"].parent.mkdir(parents=True, exist_ok=True)
        paths["pending"].write_text(markdown, encoding="utf-8")
    llm_data: dict = {}
    if markdown and req.use_llm and rt.guide.llm is not None:
        try:
            llm_data = autoprofile.llm_extract(rt.guide.llm, markdown, labels)
            notes.append("LLM đã đọc guideline để điền prompt, định nghĩa, luật attribute và điểm skeleton.")
        except Exception as exc:  # noqa: BLE001 - LLM lỗi thì vẫn dựng bằng luật
            notes.append(f"LLM lỗi, chỉ dùng phần đọc bằng luật: {str(exc)[:200]}")
    elif markdown and req.use_llm:
        notes.append("Backend chưa bật AI soạn guideline (Cài đặt của app): tool đọc guideline bằng luật.")
    prompts = known_prompts()
    built = autoprofile.build(labels, rt.store.profiles, project={"project_id": req.project_id, "task_id": req.task_id,
                                                                  "name": req.name},
                              markdown=markdown, analysis=analysis, llm_data=llm_data,
                              prompt_lookup=lambda name: prompts.get(norm(name)))
    profile = built["profile"]
    task_names = {norm(l["name"]) for l in labels}
    extra = [l["name"] for l in analysis.get("labels") or [] if norm(l["name"]) not in task_names]
    if extra:
        built["warnings"].append(f"Guideline có {len(extra)} label task chưa có ({', '.join(extra[:8])}). "
                                 "Thêm vào task bằng nút 'Thêm label còn thiếu' sau khi lưu.")
    return {"profile": profile, "rows": built["rows"], "warnings": built["warnings"], "notes": notes,
            "guideline": {"id": analysis.get("id", ""), "title": analysis.get("title", ""), "file": req.filename,
                          "checklist": len(analysis.get("checklist") or []), "issue_types": len(analysis.get("issue_types") or [])}
            if analysis else None,
            "exists": profile["id"] in rt.store.profiles, "llm": rt.guide.llm_info(False)}


@app.post("/profile/project/save", dependencies=[Depends(require_key)])
def project_profile_save(req: ProjectProfileSaveReq) -> dict:
    import yaml

    from .guideline import importer

    profile = {k: v for k, v in req.profile.items() if not str(k).startswith("_")}
    pid = str(profile.get("id", ""))
    if not re.fullmatch(r"proj_[pt]\w{1,40}", pid) or profile.get("mode") != "mixed" or not profile.get("auto_project"):
        raise HTTPException(status_code=400, detail="Profile của project không hợp lệ.")
    if req.guideline_id:
        guide_id = _safe_id(req.guideline_id)
        paths = _import_paths(guide_id)
        if paths["pending"].exists():
            for key in ("reference", "record"):
                paths[key].parent.mkdir(parents=True, exist_ok=True)
            markdown = paths["pending"].read_text(encoding="utf-8")
            analysis = importer.analyze(markdown, profile.get("guideline") or guide_id, lambda name: None)
            analysis["id"] = guide_id
            paths["reference"].write_text(f"<!-- Nạp kèm profile {pid}. -->\n\n" + markdown, encoding="utf-8")
            paths["record"].write_text(yaml.safe_dump(importer.knowledge_record(analysis), allow_unicode=True, sort_keys=False, width=140),
                                       encoding="utf-8")
            paths["pending"].unlink(missing_ok=True)
            rt.guide.lib.reload()
    path = settings.profiles_dir / f"{pid}.yaml"
    header = ("# Profile của project, tạo ở tab Guideline của panel (0.9.4). Tạo lại sẽ ghi đè file này.\n"
              "# Chỉnh prompt / ngưỡng ở tab Label; chỉnh luật ở đây.\n")
    path.write_text(header + yaml.safe_dump(profile, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
    rt.store.reload()
    return {"profile_id": pid, "file": str(path)}


@app.post("/profile/project/current", dependencies=[Depends(require_key)])
def project_profile_current(req: ProjectProfileReq) -> dict:
    own = rt.store.for_project(req.project_id, req.task_id)
    if own is None:
        return {"profile": None}
    return {"profile": {"id": own["id"], "title": own.get("title"), "guideline": own.get("guideline") or "",
                        "labels": len(own.get("labels") or []), "file": own.get("_path")}}


@app.post("/guideline/import/save", dependencies=[Depends(require_key)])
def guideline_import_save(req: ImportSaveReq) -> dict:
    import yaml

    from .guideline import importer

    analysis = req.analysis
    guide_id = _safe_id(str(analysis.get("id", "")))
    paths = _import_paths(guide_id)
    if not paths["pending"].exists():
        raise HTTPException(status_code=400, detail="Chưa đọc file guideline này (hoặc backend đã khởi động lại): chọn lại file.")
    shapes = {s for _, s in importer.SHAPES}
    labels = []
    for label in analysis.get("labels") or []:
        name = str(label.get("name", "")).strip()
        if not name:
            continue
        shape = label.get("shape") if label.get("shape") in shapes else "rectangle"
        prompts = [p.strip() for p in label.get("prompts") or [] if isinstance(p, str) and p.strip()]
        labels.append(dict(label, name=name, shape=shape, prompts=prompts))
    if analysis.get("task") not in ("instance", "semantic", "tracking", "keypoints"):
        raise HTTPException(status_code=400, detail="Loại bài toán không hợp lệ.")
    analysis = dict(analysis, id=guide_id, labels=labels)
    for key in ("reference", "record"):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
    paths["reference"].write_text(f"<!-- Nạp từ {analysis.get('file', '')} qua tab Guideline. -->\n\n"
                                  + paths["pending"].read_text(encoding="utf-8"), encoding="utf-8")
    paths["record"].write_text(yaml.safe_dump(importer.knowledge_record(analysis), allow_unicode=True, sort_keys=False, width=140),
                               encoding="utf-8")
    paths["pending"].unlink(missing_ok=True)
    profile = importer.build_profile(analysis)
    profile_id = None
    if profile:
        header = (f"# Sinh khi nạp guideline {analysis.get('file', '')} ({analysis.get('title', '')}).\n"
                  "# Sửa prompt, shape, ngưỡng ở đây hoặc ở tab Label. Nạp lại cùng guideline sẽ ghi đè file này.\n")
        paths["profile"].write_text(header + yaml.safe_dump(profile, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
        rt.store.reload()
        profile_id = profile["id"]
    rt.guide.lib.reload()
    return {"id": guide_id, "profile_id": profile_id, "labels": len(labels),
            "files": [str(paths[k]) for k in ("reference", "record", "profile") if paths[k].exists()]}


@app.get("/guideline/imported", dependencies=[Depends(require_key)])
def guideline_imported() -> dict:
    return {"items": [{"id": r.get("id"), "title": r.get("title"), "file": r.get("file"), "task": r.get("task"),
                       "imported": r.get("imported"), "labels": len(r.get("labels") or []),
                       "profile_id": f"gl_{r.get('id')}" if f"gl_{r.get('id')}" in rt.store.profiles else None}
                      for r in rt.guide.lib.imported]}


class ImportedLabelsReq(BaseModel):
    labels: list[LabelIn] = Field(default_factory=list)   # label đang có trong task


@app.post("/guideline/imported/{guide_id}/labels", dependencies=[Depends(require_key)])
def guideline_imported_labels(guide_id: str, req: ImportedLabelsReq) -> dict:
    from .guideline import importer

    guide_id = _safe_id(guide_id)
    record = next((r for r in rt.guide.lib.imported if r.get("id") == guide_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy guideline đã nạp này.")
    return importer.cvat_labels(record, [l.model_dump() for l in req.labels], rt.guide.lib)


@app.delete("/guideline/imported/{guide_id}", dependencies=[Depends(require_key)])
def guideline_imported_remove(guide_id: str) -> dict:
    """Gỡ guideline đã nạp: chuyển văn bản, bản rút ra và profile vào thư mục archive (không xoá hẳn)."""
    paths = _import_paths(_safe_id(guide_id))
    moved = []
    for key, archive in (("reference", rt.guide.lib.root / "archive"), ("record", rt.guide.lib.root / "archive"),
                         ("profile", settings.profiles_dir / "archive")):
        if paths[key].exists():
            archive.mkdir(parents=True, exist_ok=True)
            target = archive / paths[key].name
            paths[key].replace(target)
            moved.append(str(target))
    if not moved:
        raise HTTPException(status_code=404, detail="Không tìm thấy guideline đã nạp này.")
    rt.store.reload()
    rt.guide.lib.reload()
    return {"moved": moved}


@app.get("/guideline/lessons", dependencies=[Depends(require_key)])
def guideline_lessons() -> dict:
    lessons = sorted(rt.guide.lib.lessons.values(), key=lambda l: (l["id"].startswith("seed-"), -float(l.get("created", 0))))
    return {"lessons": lessons}


@app.post("/guideline/lessons/{lesson_id}/status", dependencies=[Depends(require_key)])
def guideline_lesson_status(lesson_id: str, req: LessonStatusReq) -> dict:
    lesson = guide_call(rt.guide.lib.set_status, lesson_id, req.status)
    if not lesson:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài học.")
    return {"lesson": lesson}


@app.get("/guideline/run/{run_id}", response_class=HTMLResponse)
def guideline_page(run_id: str) -> str:
    return translate_page(GUIDELINE_PAGE)


@app.get("/guideline/run/{run_id}/data", dependencies=[Depends(require_key)])
def guideline_data(run_id: str) -> dict:
    return rt.guide.view(guide_call(rt.guide._load, run_id))


@app.get("/guideline/run/{run_id}/image/{name}", dependencies=[Depends(require_key_or_query)])
def guideline_image(run_id: str, name: str) -> FileResponse:
    import re

    if not re.fullmatch(r"(frame|case)_\d+\.jpg", name) or not rt.guide.lib.load_run(run_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh.")
    path = rt.guide.lib.run_file(run_id, name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh.")
    return FileResponse(path, media_type="image/jpeg")


class QaJobReq(ResolveReq):
    job_id: Optional[int] = None
    shapes: list[dict] = Field(default_factory=list)     # mọi shape của job
    frames: list[int] = Field(default_factory=list)       # frame thật của job (bỏ frame đã xoá)
    sizes: dict[str, list[int]] = Field(default_factory=dict)   # {frame: [rộng, cao]} từ /data/meta; thiếu thì dùng width/height
    width: int = 0
    height: int = 0


@app.post("/qa/job", dependencies=[Depends(require_key)])
def run_qa_job(req: QaJobReq) -> dict:
    """Rà cả job: chạy kiểm tra của tab Kiểm tra cho mọi frame trong một lần gọi, cộng vài kiểm tra trên toàn job."""
    profile, labels, cfgs = context(req)
    by_frame: dict[int, list[dict]] = {}
    for shape in req.shapes:
        by_frame.setdefault(int(shape.get("frame", 0)), []).append(shape)
    tool_ids: dict = {}
    if req.job_id is not None and rt.learner is not None:
        tool_ids = {e["cvat_id"]: e["label_name"] for e in rt.learner._events(req.job_id) if e["kind"] == "applied" and e.get("cvat_id")}
    frames_out, checklist = [], None
    per_label_tool: dict[str, dict[str, int]] = {}
    totals = {"error": 0, "warning": 0, "info": 0}
    for frame, shapes in sorted(by_frame.items()):
        w, h = req.sizes.get(str(frame)) or [req.width, req.height]
        if not w or not h:
            continue
        res = qa.run_qa(profile, cfgs, labels, shapes, int(w), int(h))
        checklist = checklist or res["checklist"]
        for f in res["findings"]:
            totals[f["severity"]] += 1
            label = tool_ids.get(f.get("shape_id"))
            if label and f["severity"] != "info":
                per_label_tool.setdefault(label, {})
                per_label_tool[label][f["code"]] = per_label_tool[label].get(f["code"], 0) + 1
            f["by_tool"] = bool(label)
        if res["findings"]:
            frames_out.append({"frame": frame, "findings": res["findings"]})
    job_findings = []
    empty = [f for f in req.frames if f not in by_frame]
    if empty:
        job_findings.append({"severity": "warning", "code": "COMPLETENESS",
                             "message": f"{len(empty)} frame chưa có nhãn nào: {', '.join(map(str, empty[:30]))}{'…' if len(empty) > 30 else ''}. "
                                        "Kiểm tra lại, hoặc chạy Gợi ý cho cả job.", "frames": empty})
    used = {s.get("label_id") for s in req.shapes}
    unused = [c["name"] for c in cfgs if c.get("enabled") and c["label_id"] not in used]
    if unused and req.shapes:
        job_findings.append({"severity": "info", "code": "TAXONOMY",
                             "message": f"Label chưa dùng lần nào trong job: {', '.join(unused)}. Đúng là job không có, hay bị sót?"})
    if per_label_tool and rt.learner is not None:
        rt.learner.tool_findings(profile["id"], per_label_tool)
    return {"profile_id": profile["id"], "frames": frames_out, "job_findings": job_findings, "totals": totals,
            "checked_frames": len(by_frame), "checklist": checklist or [], "tool_errors": per_label_tool}


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


LOGO_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28"><rect width="28" height="28" rx="7" fill="#2F6BFF"/>'
            '<path d="M19 8H10l-2 3 2 3h8l2 3-2 3H9" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round" '
            'stroke-linejoin="round"/><g fill="#fff"><circle cx="19" cy="8" r="1.7"/><circle cx="8" cy="11" r="1.7"/>'
            '<circle cx="18" cy="14" r="1.7"/><circle cx="20" cy="17" r="1.7"/><circle cx="9" cy="20" r="1.7"/></g></svg>')


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Logo của tool trên tab trình duyệt (trang chủ backend, trang duyệt guideline)."""
    return Response(LOGO_SVG, media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    engine = getattr(rt.engine, "name", "?")
    device = getattr(rt.engine, "device", "?")
    return tr(f"""<!doctype html><html lang="vi"><meta charset="utf-8"><title>SAM3 Label Assist</title>
<body style="font-family:system-ui;max-width:640px;margin:48px auto;padding:0 16px;line-height:1.6">
<h1>SAM3 Label Assist</h1>
<p>Backend đang chạy. Engine: <b>{engine}</b>, thiết bị: <b>{device}</b>, phiên bản {__version__}.</p>
<p><a href="/sam3-cvat-assist.user.js">Cài userscript vào Tampermonkey</a></p>
<p>Sau khi cài, mở một job trên CVAT. Panel "SAM3 Assist" hiện ở góc phải màn hình.</p>
</body></html>""")
