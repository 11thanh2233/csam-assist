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
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import __version__, keypoints, pipeline, qa, tracking
from .config import settings
from .engines import Engine, create_engine
from .engines.pose import LazyEngine, create_body_pose, create_face
from .guideline import llm as guideline_llm
from .guideline.library import Library
from .guideline.page import PAGE as GUIDELINE_PAGE
from .guideline.service import GuidelineService
from .learning import Learner, apply_learned
from .profiles import ProfileStore, resolve_labels

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sam3-assist")


class Runtime:
    engine: Optional[Engine] = None
    store: Optional[ProfileStore] = None
    body: Optional[LazyEngine] = None
    face: Optional[LazyEngine] = None
    guide: Optional[GuidelineService] = None
    learner: Optional[Learner] = None
    video: Optional[LazyEngine] = None   # SAM3 Video, chỉ nạp khi người dùng bật
    cache: "OrderedDict[str, tuple[np.ndarray, Any]]" = OrderedDict()


rt = Runtime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    rt.store = ProfileStore(settings.profiles_dir)
    log.info("Đã nạp %d profile từ %s", len(rt.store.profiles), settings.profiles_dir)
    log.info("Đang nạp model (lần đầu có thể mất vài phút để tải checkpoint)...")
    rt.engine = create_engine(settings)
    log.info("Sẵn sàng. Engine: %s, thiết bị: %s", rt.engine.name, rt.engine.device)
    mock = rt.engine.name == "mock"
    rt.guide = GuidelineService(Library(settings.library_dir), guideline_llm.from_env())
    rt.learner = Learner(settings.library_dir / "learning")
    info = rt.guide.llm_info(False)
    log.info("Soạn guideline: %d bài học trong thư viện, LLM: %s %s", len(rt.guide.lib.lessons), info["provider"], info["model"])
    rt.body = LazyEngine(lambda: create_body_pose(mock, settings.pose_model, settings.device),
                         "tư thế người (ViTPose)" if not mock else "tư thế người (giả lập)")
    rt.face = LazyEngine(lambda: create_face(mock, settings.face_model),
                         "khuôn mặt (MediaPipe)" if not mock else "khuôn mặt (giả lập)")
    if settings.host not in ("127.0.0.1", "localhost") and not settings.api_key:
        log.warning("Backend mở ra ngoài máy này nhưng chưa đặt SAM3_API_KEY. Hãy đặt API key.")
    yield


app = FastAPI(title="SAM3 CVAT Assist", version=__version__, lifespan=lifespan)
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

            return PlainTextResponse(f"Host '{host}' không được phép. Mở backend bằng http://127.0.0.1:{settings.port} "
                                     "hoặc đặt SAM3_ALLOWED_HOSTS.", status_code=400)
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    """Lỗi chưa lường trước: trả lý do về panel thay vì chỉ một mã 500 trống."""
    log.exception("Lỗi khi xử lý yêu cầu")
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {str(exc)[:400]}"})


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


class ProposeReq(ResolveReq):
    image_b64: str
    existing: list[dict] = Field(default_factory=list)


class InteractiveReq(ResolveReq):
    image_b64: str
    label_id: int
    box: list[float]
    mode: str = "single"  # single | similar
    prompt: Optional[str] = None
    existing: list[dict] = Field(default_factory=list)   # nhãn đã có trên frame: "Tìm tương tự" không đề xuất lại chúng
    negatives: list[list[float]] = Field(default_factory=list)   # hộp mẫu âm: vật bị nhận nhầm, SAM3 bỏ những vật giống chúng


class TrackFrame(BaseModel):
    frame: int
    image_b64: str


class TrackDetectReq(ResolveReq):
    frames: list[TrackFrame]


class TrackBuildReq(ResolveReq):
    frames: list[dict]  # [{frame, dets}] từ /track/detect
    width: int
    height: int
    start: int
    stop: int


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
    labels = [l.model_dump() for l in req.labels]
    profile = rt.store.get(req.profile_id, [l["name"] for l in labels], purpose, [l["type"] for l in labels], labels)
    cfgs = resolve_labels(profile, labels, req.overrides)
    if req.use_learned and rt.learner is not None:
        apply_learned(cfgs, rt.learner.for_profile(profile["id"]), req.overrides)
    return profile, labels, cfgs


# ------------------------------------------------------------- tự học ------
class LearnEventsReq(BaseModel):
    events: list[dict]


class LearnReviewReq(BaseModel):
    job_id: int
    shapes: list[dict] = Field(default_factory=list)
    tracks: list[dict] = Field(default_factory=list)


class LearnToggleReq(BaseModel):
    profile_id: str
    label: str
    enabled: bool


class LearnForgetReq(BaseModel):
    profile_id: Optional[str] = None
    label: Optional[str] = None


@app.post("/learn/events", dependencies=[Depends(require_key)])
def learn_events(req: LearnEventsReq) -> dict:
    """Userscript gửi lúc người dùng bấm Ghi: đề xuất đã ghi (kèm id CVAT) và đề xuất bị bỏ tick."""
    return {"recorded": rt.learner.record(req.events)}


@app.post("/learn/review", dependencies=[Depends(require_key)])
def learn_review(req: LearnReviewReq) -> dict:
    """So nhãn tool đã ghi với annotation hiện tại của job (sau khi người dùng sửa), rồi học lại."""
    return rt.learner.review(req.job_id, req.shapes, req.tracks)


@app.get("/learn/summary", dependencies=[Depends(require_key)])
def learn_summary() -> dict:
    return rt.learner.summary()


@app.post("/learn/toggle", dependencies=[Depends(require_key)])
def learn_toggle(req: LearnToggleReq) -> dict:
    rt.learner.set_enabled(req.profile_id, req.label, req.enabled)
    return rt.learner.summary()


@app.post("/learn/forget", dependencies=[Depends(require_key)])
def learn_forget(req: LearnForgetReq) -> dict:
    removed = rt.learner.forget(req.profile_id, req.label)
    return dict(rt.learner.summary(), removed=removed)


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
    }


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
                      if profile.get("mode") == "keypoints" and e.get("shape") == "skeleton"],
        "track": {
            "profile_id": track_profile["id"],
            "title": track_profile.get("title", track_profile["id"]),
            "labels": track_cfgs,
        },
    }


@app.post("/propose", dependencies=[Depends(require_key)])
def propose(req: ProposeReq) -> dict:
    profile, _, cfgs = context(req)
    image, state = image_state(req.image_b64)
    if profile.get("mode") == "keypoints":
        result = keypoints.propose(rt.engine, state, image, profile, cfgs, req.existing, rt.body, rt.face)
    else:
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
    if cfg["shape"] == "skeleton":
        return keypoints.interactive(decode_image(req.image_b64), profile, cfg, cfgs, req.box, rt.body, rt.face)
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
        image = decode_image(item.image_b64)
        dets = tracking.detect_frame(rt.engine, image, cfgs, warnings)
        out.append({"frame": item.frame, "width": image.shape[1], "height": image.shape[0], "dets": dets})
    return {"profile_id": profile["id"], "frames": out, "warnings": sorted(set(warnings))}


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

            rt.video = LazyEngine(lambda: MockVideoTracker(rt.engine).load(), "SAM3 Video (giả lập)")
        else:
            from .engines.sam3_video import Sam3VideoTracker

            rt.video = LazyEngine(lambda: Sam3VideoTracker(settings.hf_model, settings.device, settings.video_dtype).load(), "SAM3 Video")
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
            image = decode_image(item.image_b64)
            raw = tracker.add_frame(req.session_id, image)
            out.append({"frame": item.frame, "width": image.shape[1], "height": image.shape[0],
                        "dets": tracking.video_dets(raw, image, cfgs)})
    except Exception as exc:  # noqa: BLE001
        tracker.end(req.session_id)
        log.warning("SAM3 Video lỗi giữa chừng: %s", exc)
        raise HTTPException(status_code=400, detail=f"SAM3 Video lỗi: {type(exc).__name__}: {str(exc)[:300]}") from exc
    return {"profile_id": profile["id"], "frames": out, "warnings": []}


@app.post("/track/video/end", dependencies=[Depends(require_key)])
def track_video_end(req: TrackVideoEndReq) -> dict:
    if rt.video is not None and rt.video.engine is not None:
        rt.video.engine.end(req.session_id)
    return {"ok": True}


@app.post("/track/build", dependencies=[Depends(require_key)])
def track_build(req: TrackBuildReq) -> dict:
    """Bước 2: nối các phát hiện thành track, áp luật box/occluded/outside/keyframe của guideline."""
    profile, _, cfgs = context(req, "track")
    result = tracking.build_tracks(req.frames, cfgs, profile, req.width, req.height, req.start, req.stop)
    log.info("track: profile=%s, %d track", profile["id"], len(result["tracks"]))
    return dict(result, profile_id=profile["id"])


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
    return GUIDELINE_PAGE


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
