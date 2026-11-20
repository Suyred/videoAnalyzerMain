"""FastAPI API: upload, queue, monitor, cancel and download video jobs."""
from __future__ import annotations

import json
import hmac
from contextlib import asynccontextmanager
import os
import threading
import time
import uuid
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi import Request
from pydantic import BaseModel, Field

from . import config
from .association import AssociationConfig
from .detector import Detector
from .tracker import Tracker
from .video_processor import VideoCancelled, VideoProcessor
from . import persistence, security, reports

@asynccontextmanager
async def lifespan(_app: FastAPI):
    _load_jobs()
    _cleanup_expired()
    # Recover durable queued/processing jobs after a restart when files are on a persistent disk.
    for jid, job in list(jobs.items()):
        if job.get("status") in {"queued", "processing"}:
            upload = job.get("upload_path")
            if upload and Path(upload).is_file():
                job["status"] = "queued"
                job["error"] = None
                job["updated_at"] = _now()
                threading.Thread(target=_run_analysis, args=(jid, upload), daemon=True).start()
            else:
                job.update({"status":"failed", "error":"Upload file is unavailable after restart; check persistent disk configuration.", "updated_at":_now()})
                persistence.save_job(jid, job)
    yield


app = FastAPI(
    title="VIDEO-ANALYZER API",
    description="Detects visible phones and marks only people conservatively associated with them.",
    version="3.0.0",
    lifespan=lifespan,
)
@app.middleware("http")
async def authenticate_base44_bridge(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in {"/", "/health", "/docs", "/openapi.json", "/redoc"}:
        request.state.owner_id = "public"
    elif path.startswith("/download/") and request.query_params.get("token") and request.query_params.get("expires"):
        request.state.owner_id = "signed-link"
    elif path == "/analyses" or path.startswith("/analyses/"):
        # Base44 calls this API only from its server functions. The shared bearer
        # secret authenticates that server-to-server channel; the user header
        # scopes every worker job to the signed-in Base44 account.
        expected = os.getenv("BACKEND_API_KEY", "")
        supplied = request.headers.get("authorization", "")
        user_id = request.headers.get("x-base44-user", "").strip()
        if not expected or not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], expected):
            return JSONResponse(status_code=401, content={"detail": "Invalid backend API credentials."})
        if not user_id or len(user_id) > 256:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid Base44 user identity."})
        request.state.owner_id = user_id
    else:
        try:
            request.state.owner_id = security.bridge_identity(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


def _owned_job(job_id: str, owner_id: str):
    job = jobs.get(job_id)
    if not job or (security.BRIDGE_SECRET and job.get("owner_id") != owner_id):
        raise HTTPException(status_code=404, detail="Job not found or access denied.")
    return job


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.RLock()
job_semaphore = threading.BoundedSemaphore(config.MAX_CONCURRENT_JOBS)
cancel_events: dict[str, threading.Event] = {}
upload_reservations = 0


class AnalyzeResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: int = 0
    processed_frames: int = 0
    total_frames: Optional[int] = None
    current_frame: int = 0
    result_file: Optional[str] = None
    summary_file: Optional[str] = None
    download_url: Optional[str] = None
    summary_url: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    settings: dict[str, Any] = Field(default_factory=dict)
    eta_seconds: Optional[int] = None
    owner_id: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist_jobs_locked() -> None:
    """Atomically persist job metadata; actual video files remain local files."""
    temporary = config.JOBS_FILE.with_suffix(".json.tmp")
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(jobs, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, config.JOBS_FILE)
        for jid, record in jobs.items():
            persistence.save_job(jid, record)
    except (OSError, persistence.sqlite3.Error):
        temporary.unlink(missing_ok=True)


def _load_jobs() -> None:
    try:
        data = persistence.load_jobs()
        if not data and config.JOBS_FILE.is_file():
            data = json.loads(config.JOBS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        for job_id, job in data.items():
            if not isinstance(job, dict):
                continue
            jobs[job_id] = job
        with jobs_lock:
            _persist_jobs_locked()
    except (OSError, json.JSONDecodeError):
        # A corrupt metadata file must not prevent the API from starting.
        return


def _parse_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _cleanup_expired() -> None:
    cutoff = time.time() - config.RESULT_RETENTION_HOURS * 3600
    changed = False
    with jobs_lock:
        for job_id, job in list(jobs.items()):
            created = _parse_timestamp(job.get("created_at"))
            if created and created < cutoff and job.get("status") not in {"queued", "processing"}:
                for key in ("upload_path", "result_file", "summary_file"):
                    candidate = job.get(key)
                    if candidate:
                        try:
                            Path(candidate).unlink(missing_ok=True)
                        except OSError:
                            pass
                cancel_events.pop(job_id, None)
                del jobs[job_id]
                persistence.delete_job(job_id)
                changed = True
        if changed:
            _persist_jobs_locked()


def _validate_filename(filename: str | None) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="No filename was provided.")
    safe_name = Path(filename.replace("\\", "/")).name
    extension = Path(safe_name).suffix.lower().lstrip(".")
    if extension not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported video format. Allowed: mp4, mov, avi, mkv, webm.",
        )
    return safe_name


def _validate_float(name: str, value: float, minimum: float, maximum: float) -> float:
    if not minimum <= value <= maximum:
        raise HTTPException(status_code=422, detail=f"{name} must be between {minimum} and {maximum}.")
    return float(value)


async def _save_upload(file: UploadFile, destination: Path) -> None:
    total = 0
    try:
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > config.MAX_UPLOAD_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {config.MAX_UPLOAD_SIZE_MB} MB upload limit.",
                    )
                output.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def _progress_callback(job_id: str):
    def update(values: dict) -> None:
        with jobs_lock:
            job = jobs.get(job_id)
            if not job or job.get("status") != "processing":
                return
            job.update(values)
            job["updated_at"] = _now()
            progress = int(job.get("progress", 0))
            started = float(job.get("started_epoch", time.time()))
            if progress > 0:
                job["eta_seconds"] = max(0, int((time.time()-started) * (100-progress) / progress))
            _persist_jobs_locked()
            if job.get("base44_job_id") and progress % 5 == 0:
                threading.Thread(target=_base44_callback, args=(dict(job), job.get("status", "processing")), daemon=True).start()
    return update


@app.get("/", tags=["health"])
async def root():
    return {"service": "video-analyzer", "version": app.version, "docs": "/docs", "health": "/health"}


@app.get("/health", tags=["health"])
async def health():
    with jobs_lock:
        active = sum(job.get("status") in {"queued", "processing"} for job in jobs.values())
    return {
        "status": "healthy",
        "service": "video-analyzer",
        "version": app.version,
        "model_present": config.MODEL_PATH.is_file(),
        "model": config.MODEL_PATH.name,
        "active_jobs": active,
        "max_concurrent_jobs": config.MAX_CONCURRENT_JOBS,
    }


def _base44_url_is_allowed(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    allowed_hosts = [x.strip().lower() for x in os.getenv("BASE44_FILE_HOSTS", "").split(",") if x.strip()]
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and bool(host) and not parsed.username and not parsed.password
            and bool(allowed_hosts) and any(host == h or host.endswith("." + h.lstrip(".")) for h in allowed_hosts))


class _AllowlistedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _base44_url_is_allowed(newurl):
            raise HTTPException(400, "Video URL redirected to a host that is not allow-listed.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_base44_source(file_url: str, destination: Path) -> None:
    if not _base44_url_is_allowed(file_url):
        raise ValueError("Video URL must be HTTPS and its hostname must be listed in BASE44_FILE_HOSTS.")
    opener = urllib.request.build_opener(_AllowlistedRedirect())
    request = urllib.request.Request(file_url, headers={"User-Agent": "VIDEO-ANALYZER/4"})
    total = 0
    with opener.open(request, timeout=30) as response, destination.open("wb") as output:
        if not _base44_url_is_allowed(response.geturl()):
            raise ValueError("Final video URL host is not allow-listed.")
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > config.MAX_UPLOAD_SIZE_BYTES:
                raise ValueError(f"Video exceeds the {config.MAX_UPLOAD_SIZE_MB} MB upload limit.")
            output.write(chunk)
    if total == 0:
        raise ValueError("The source video is empty.")


@app.post("/analyses", tags=["Base44 API"])
async def create_analysis_for_base44(request: Request):
    """Base44-compatible JSON submission endpoint. Auth is server-to-server Bearer."""
    _cleanup_expired()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Request body must be valid JSON.")
    if not isinstance(body, dict):
        raise HTTPException(400, "Request body must be a JSON object.")
    file_url = str(body.get("video_url", ""))
    filename = _validate_filename(str(body.get("filename") or Path(urllib.parse.urlparse(file_url).path).name or "video.mp4"))
    mode = str(body.get("detection_mode", "phone_in_hand"))
    if mode not in {"phone_in_hand", "phone_usage"}:
        raise HTTPException(422, "detection_mode must be phone_in_hand or phone_usage.")
    settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
    confidence = settings.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        raise HTTPException(422, "settings.confidence must be a number.")
    _validate_float("confidence", confidence, 0.05, 0.95)
    sample_rate = settings.get("sample_rate", "2fps")
    stride_by_rate = {"every_frame": 1, "2fps": max(1, round(25 / 2)), "1fps": 25}
    if sample_rate not in stride_by_rate:
        raise HTTPException(422, "settings.sample_rate must be every_frame, 2fps or 1fps.")
    owner_id = request.state.owner_id
    if not persistence.reserve_request(owner_id, security.RATE_LIMIT):
        raise HTTPException(429, "Hourly request limit reached.")
    with jobs_lock:
        outstanding = sum(j.get("status") in {"queued", "processing"} for j in jobs.values())
        if outstanding >= config.MAX_QUEUED_JOBS + config.MAX_CONCURRENT_JOBS:
            raise HTTPException(429, "The analysis queue is full. Try again later.")
    job_id = str(uuid.uuid4())
    upload_path = config.UPLOAD_DIR / f"{job_id}_{filename}"
    try:
        await __import__("asyncio").to_thread(_download_base44_source, file_url, upload_path)
    except Exception as exc:
        upload_path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(400, f"Could not retrieve the private Base44 video: {str(exc)[:250]}")
    worker_settings = {
        "detection_mode": mode,
        "confidence": confidence,
        "person_confidence": max(0.05, min(0.95, confidence)),
        "phone_confidence": config.PHONE_CONFIDENCE_THRESHOLD,
        "sample_rate": sample_rate,
        "frame_stride": stride_by_rate[sample_rate],
        "max_width": config.INFERENCE_MAX_WIDTH,
        "max_duration": config.MAX_VIDEO_DURATION_SECONDS,
        "annotated_video": bool(settings.get("annotated_video", True)),
    }
    now = _now()
    job = {
        "status": "queued", "progress": 0, "processed_frames": 0,
        "total_frames": None, "current_frame": 0, "upload_path": str(upload_path),
        "result_file": None, "summary_file": None, "error": None,
        "settings": worker_settings, "detection_mode": mode, "owner_id": owner_id,
        "created_at": now, "updated_at": now, "_created_epoch": time.time(),
        "started_epoch": None,
    }
    with jobs_lock:
        jobs[job_id] = job
        cancel_events[job_id] = threading.Event()
        _persist_jobs_locked()
    threading.Thread(target=_run_analysis, args=(job_id, str(upload_path)), daemon=True).start()
    return {"id": job_id, "status": "queued", "progress": 0, "detection_mode": mode}


@app.get("/analyses/{analysis_id}", tags=["Base44 API"])
async def get_analysis_for_base44(analysis_id: str, request: Request):
    _cleanup_expired()
    with jobs_lock:
        job = _owned_job(analysis_id, request.state.owner_id)
        if job.get("owner_id") != request.state.owner_id:
            raise HTTPException(404, "Analysis not found.")
        data = dict(job)
    status_map = {"queued": "queued", "processing": "running", "completed": "done", "failed": "failed", "cancelled": "cancelled"}
    result_url = None
    if data.get("status") == "completed":
        expires = int(time.time()) + 3600
        if not security.DOWNLOAD_SECRET:
            raise HTTPException(503, "DOWNLOAD_SIGNING_SECRET is not configured.")
        token = security.make_download_token(analysis_id, request.state.owner_id, expires)
        base_url = os.getenv("PUBLIC_API_URL", "").rstrip("/")
        if not base_url:
            raise HTTPException(503, "PUBLIC_API_URL is not configured.")
        result_url = f"{base_url}/download/{analysis_id}?expires={expires}&token={token}"
    summary = {}
    summary_path = data.get("summary_file")
    if summary_path and Path(summary_path).is_file():
        try:
            summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        except Exception:
            summary = {}
    return {
        "id": analysis_id,
        "status": status_map.get(data.get("status"), "failed"),
        "progress": int(data.get("progress", 0)),
        "result_url": result_url,
        "error_message": data.get("error"),
        "results": summary,
        "detection_mode": data.get("detection_mode") or data.get("settings", {}).get("detection_mode", "phone_in_hand"),
        "eta_seconds": data.get("eta_seconds"),
    }


@app.post("/analyses/{analysis_id}/cancel", tags=["Base44 API"])
async def cancel_analysis_for_base44(analysis_id: str, request: Request):
    with jobs_lock:
        job = _owned_job(analysis_id, request.state.owner_id)
        if job.get("owner_id") != request.state.owner_id:
            raise HTTPException(404, "Analysis not found.")
        if job.get("status") not in {"completed", "failed", "cancelled"}:
            event = cancel_events.get(analysis_id)
            if event:
                event.set()
            if job.get("status") == "queued":
                job.update({"status": "cancelled", "error": "Cancelled before processing started.", "updated_at": _now()})
                Path(job.get("upload_path", "")).unlink(missing_ok=True)
            _persist_jobs_locked()
        return {"id": analysis_id, "status": job.get("status")}


@app.post("/analyze", response_model=AnalyzeResponse, tags=["video"])
async def analyze_video(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    person_confidence: float = Form(config.PERSON_CONFIDENCE_THRESHOLD),
    phone_confidence: float = Form(config.PHONE_CONFIDENCE_THRESHOLD),
    association_margin: float = Form(0.08),
    association_expand_x: float = Form(config.ASSOCIATION_EXPAND_X),
    association_expand_y: float = Form(config.ASSOCIATION_EXPAND_Y),
    tracker_max_age: int = Form(config.TRACKER_MAX_AGE),
    tracker_max_center_distance: float = Form(config.TRACKER_MAX_CENTER_DISTANCE),
    temporal_window: int = Form(config.ASSOCIATION_TEMPORAL_WINDOW),
    temporal_min_hits: int = Form(config.ASSOCIATION_TEMPORAL_MIN_HITS),
    frame_stride: int = Form(config.INFERENCE_FRAME_STRIDE),
    max_width: int = Form(config.INFERENCE_MAX_WIDTH),
    max_duration: int = Form(config.MAX_VIDEO_DURATION_SECONDS),
    start_seconds: float = Form(0.0),
    end_seconds: float = Form(0.0),
    output_width: int = Form(0),
):
    _cleanup_expired()
    safe_name = _validate_filename(file.filename)
    if file.content_type and file.content_type not in config.ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=415, detail="The uploaded content type is not a supported video type.")

    person_confidence = _validate_float("person_confidence", person_confidence, 0.05, 0.95)
    phone_confidence = _validate_float("phone_confidence", phone_confidence, 0.005, 0.95)
    association_margin = _validate_float("association_margin", association_margin, 0.0, 0.5)
    association_expand_x = _validate_float("association_expand_x", association_expand_x, 0.0, 0.5)
    association_expand_y = _validate_float("association_expand_y", association_expand_y, 0.0, 0.5)
    tracker_max_center_distance = _validate_float(
        "tracker_max_center_distance", tracker_max_center_distance, 0.02, 0.75
    )
    if not 1 <= tracker_max_age <= 60:
        raise HTTPException(status_code=422, detail="tracker_max_age must be between 1 and 60.")
    if not 1 <= temporal_window <= 30 or not 1 <= temporal_min_hits <= temporal_window:
        raise HTTPException(status_code=422, detail="Temporal hits must be between 1 and temporal_window (1–30).")
    if not 1 <= frame_stride <= 12:
        raise HTTPException(status_code=422, detail="frame_stride must be between 1 and 12.")
    if not 160 <= max_width <= 1920:
        raise HTTPException(status_code=422, detail="max_width must be between 160 and 1920.")
    if not 1 <= max_duration <= config.MAX_VIDEO_DURATION_SECONDS:
        raise HTTPException(status_code=422, detail=f"max_duration must be between 1 and {config.MAX_VIDEO_DURATION_SECONDS} seconds.")

    if start_seconds < 0 or end_seconds < 0 or (end_seconds and end_seconds <= start_seconds):
        raise HTTPException(status_code=422, detail="Invalid start_seconds/end_seconds range.")
    if output_width not in (0, 480, 720, 1080, 1280, 1920):
        raise HTTPException(status_code=422, detail="output_width must be 0, 480, 720, 1080, 1280 or 1920.")
    owner_id = request.state.owner_id
    if not persistence.reserve_request(owner_id, security.RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Hourly request limit reached.")
    global upload_reservations
    with jobs_lock:
        outstanding = sum(job.get("status") in {"queued", "processing"} for job in jobs.values())
        if outstanding + upload_reservations >= config.MAX_QUEUED_JOBS + config.MAX_CONCURRENT_JOBS:
            raise HTTPException(status_code=429, detail="The analysis queue is full. Try again later.")
        upload_reservations += 1

    job_id = str(uuid.uuid4())
    upload_path = config.UPLOAD_DIR / f"{job_id}_{safe_name}"
    try:
        await _save_upload(file, upload_path)
    except Exception:
        with jobs_lock:
            upload_reservations = max(0, upload_reservations - 1)
        raise

    settings = {
        "person_confidence": person_confidence,
        "phone_confidence": phone_confidence,
        "association_margin": association_margin,
        "association_expand_x": association_expand_x,
        "association_expand_y": association_expand_y,
        "tracker_max_age": tracker_max_age,
        "tracker_max_center_distance": tracker_max_center_distance,
        "temporal_window": temporal_window,
        "temporal_min_hits": temporal_min_hits,
        "frame_stride": frame_stride,
        "max_width": max_width,
        "max_duration": max_duration,
        "start_seconds": start_seconds, "end_seconds": end_seconds, "output_width": output_width,
    }
    now = _now()
    with jobs_lock:
        upload_reservations = max(0, upload_reservations - 1)
        jobs[job_id] = {
            "status": "queued", "progress": 0, "processed_frames": 0,
            "owner_id": owner_id, "_created_epoch": time.time(),
            "total_frames": None, "current_frame": 0,
            "upload_path": str(upload_path), "result_file": None,
            "summary_file": None, "error": None, "settings": settings,
            "created_at": now, "updated_at": now,
        }
        cancel_events[job_id] = threading.Event()
        _persist_jobs_locked()

    background_tasks.add_task(_run_analysis, job_id, str(upload_path))
    return AnalyzeResponse(job_id=job_id, status="queued", message="Video uploaded and added to the analysis queue.")


@app.get("/results/{job_id}", response_model=JobStatus, tags=["video"])
async def get_results(job_id: str, request: Request):
    _cleanup_expired()
    with jobs_lock:
        job = _owned_job(job_id, request.state.owner_id)
        status = dict(job)
    return JobStatus(
        job_id=job_id, status=status.get("status", "failed"),
        progress=int(status.get("progress", 0)),
        processed_frames=int(status.get("processed_frames", 0)),
        total_frames=status.get("total_frames"),
        current_frame=int(status.get("current_frame", 0)),
        result_file=Path(status["result_file"]).name if status.get("result_file") else None,
        summary_file=Path(status["summary_file"]).name if status.get("summary_file") else None,
        download_url=f"/download/{job_id}" if status.get("status") == "completed" else None,
        summary_url=f"/summary/{job_id}" if status.get("status") == "completed" else None,
        error=status.get("error"), created_at=status.get("created_at"),
        updated_at=status.get("updated_at"), settings=status.get("settings", {}),
        eta_seconds=(max(0, int((time.time()-status.get("started_epoch",time.time())) * (100-int(status.get("progress",0))) / max(1,int(status.get("progress",0))))) if status.get("status")=="processing" and status.get("progress",0)>0 else None),
    )


@app.get("/jobs", tags=["video"])
async def list_jobs(request: Request):
    _cleanup_expired()
    with jobs_lock:
        return [
            {"job_id": job_id, "status": job.get("status"), "progress": job.get("progress", 0),
             "created_at": job.get("created_at"), "updated_at": job.get("updated_at")}
            for job_id, job in sorted(jobs.items(), key=lambda item: item[1].get("created_at", ""), reverse=True)
            if not security.BRIDGE_SECRET or job.get("owner_id") == request.state.owner_id
        ]


@app.post("/cancel/{job_id}", tags=["video"])
async def cancel_job(job_id: str, request: Request):
    with jobs_lock:
        job = _owned_job(job_id, request.state.owner_id)
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return {"job_id": job_id, "status": job.get("status"), "message": "Job is already finished."}
        event = cancel_events.get(job_id)
        if event:
            event.set()
        if job.get("status") == "queued":
            job.update({"status": "cancelled", "error": "Cancelled before processing started.", "updated_at": _now()})
            Path(job.get("upload_path", "")).unlink(missing_ok=True)
        _persist_jobs_locked()
        return {"job_id": job_id, "status": job.get("status"), "message": "Cancellation requested."}


@app.get("/summary/{job_id}", tags=["video"])
async def get_summary(job_id: str, request: Request):
    with jobs_lock:
        job = _owned_job(job_id, request.state.owner_id)
        summary_path = job.get("summary_file")
    if not summary_path or not Path(summary_path).is_file():
        raise HTTPException(status_code=404, detail="Summary is not ready or no longer available.")
    return FileResponse(summary_path, media_type="application/json", filename=Path(summary_path).name)


@app.get("/download/{job_id}", tags=["video"])
async def download_result(job_id: str, request: Request, token: str | None = None, expires: int | None = None):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or (request.state.owner_id not in {"signed-link", job.get("owner_id")} and security.BRIDGE_SECRET):
            raise HTTPException(status_code=404, detail="Job not found or access denied.")
        if job.get("status") != "completed":
            raise HTTPException(status_code=404, detail="The result is not ready.")
        link_owner = str(job.get("owner_id", "local"))
        # Signed URLs are always cryptographically checked, even when the legacy
        # HMAC bridge is disabled but the direct Base44 API is in use.
        if request.state.owner_id == "signed-link" or security.BRIDGE_SECRET:
            if not token or not expires or not security.verify_download_token(job_id, link_owner, expires, token):
                raise HTTPException(status_code=403, detail="A valid temporary download link is required.")
        result_path = job.get("result_file")
    if not result_path or not Path(result_path).is_file():
        raise HTTPException(status_code=404, detail="The result file is no longer available.")
    return FileResponse(result_path, media_type="video/mp4", filename=Path(result_path).name)


@app.delete("/jobs/{job_id}", tags=["video"])
async def delete_job(job_id: str, request: Request):
    with jobs_lock:
        job = _owned_job(job_id, request.state.owner_id)
        if job.get("status") in {"queued", "processing"}:
            raise HTTPException(status_code=409, detail="Cancel an active job before deleting it.")
        for key in ("upload_path", "result_file", "summary_file"):
            candidate = job.get(key)
            if candidate:
                Path(candidate).unlink(missing_ok=True)
        jobs.pop(job_id, None)
        persistence.delete_job(job_id)
        cancel_events.pop(job_id, None)
        _persist_jobs_locked()
    return {"job_id": job_id, "deleted": True}


def _base44_callback(job: dict, status: str, **extra):
    callback_url = os.getenv("BASE44_CALLBACK_URL", "")
    callback_secret = os.getenv("WORKER_CALLBACK_SECRET", "")
    if not callback_url or not callback_secret or not job.get("base44_job_id"):
        return
    payload = {"job_id": job.get("worker_job_id"), "status": status,
               "progress": job.get("progress", 0), "eta_seconds": job.get("eta_seconds"), **extra}
    if status == "completed" and job.get("result_file") and security.DOWNLOAD_SECRET:
        expires = int(time.time()) + 600
        token = security.make_download_token(str(job.get("worker_job_id")), str(job.get("owner_id", "")), expires)
        public_url = os.getenv("PUBLIC_API_URL", "").rstrip("/")
        payload["result_file_url"] = f"{public_url}/download/{job.get('worker_job_id')}?expires={expires}&token={token}"
    try:
        req = urllib.request.Request(callback_url, data=json.dumps(payload).encode(),
            headers={"Content-Type":"application/json", "X-Worker-Callback-Secret":callback_secret}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        # Callback failures must not corrupt or stop the video analysis.
        return


@app.post("/internal/base44/jobs", tags=["Base44 bridge"])
async def submit_base44_job(request: Request):
    body = await request.json()
    file_url = str(body.get("file_url", ""))
    parsed = urllib.parse.urlparse(file_url)
    allowed_hosts = [x.strip().lower() for x in os.getenv("BASE44_FILE_HOSTS", "").split(",") if x.strip()]
    if parsed.scheme != "https" or not parsed.hostname or not allowed_hosts or not any(parsed.hostname.lower() == h or parsed.hostname.lower().endswith("."+h.lstrip(".")) for h in allowed_hosts):
        raise HTTPException(400, "File URL host is not allow-listed. Configure BASE44_FILE_HOSTS with the exact Base44 storage host.")
    safe_name = _validate_filename(Path(parsed.path).name or "upload.mp4")
    job_id = str(uuid.uuid4())
    upload_path = config.UPLOAD_DIR / f"{job_id}_{safe_name}"
    max_bytes = config.MAX_UPLOAD_SIZE_BYTES
    try:
        def fetch_source():
            total = 0
            req = urllib.request.Request(file_url, headers={"User-Agent":"VIDEO-ANALYZER/3"})
            with urllib.request.urlopen(req, timeout=30) as response, upload_path.open("wb") as out:
                while True:
                    chunk = response.read(1024*1024)
                    if not chunk: break
                    total += len(chunk)
                    if total > max_bytes: raise ValueError("Source file exceeds upload limit")
                    out.write(chunk)
            if total == 0: raise ValueError("Source file is empty")
        await __import__("asyncio").to_thread(fetch_source)
    except Exception as exc:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not fetch Base44 video file: {str(exc)[:300]}")
    owner_id = request.state.owner_id
    settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
    job = {"status":"queued", "progress":0, "processed_frames":0, "total_frames":None,
           "current_frame":0, "upload_path":str(upload_path), "result_file":None, "summary_file":None,
           "error":None, "settings":settings, "owner_id":owner_id, "base44_job_id":str(body.get("base44_job_id","")),
           "created_at":_now(), "updated_at":_now(), "_created_epoch":time.time()}
    job["worker_job_id"] = job_id
    with jobs_lock:
        if sum(j.get("status") in {"queued","processing"} for j in jobs.values()) >= config.MAX_QUEUED_JOBS + config.MAX_CONCURRENT_JOBS:
            upload_path.unlink(missing_ok=True)
            raise HTTPException(429, "Worker queue is full")
        jobs[job_id]=job
        cancel_events[job_id]=threading.Event()
        _persist_jobs_locked()
    _base44_callback(job, "queued")
    threading.Thread(target=_run_analysis, args=(job_id, str(upload_path)), daemon=True).start()
    return {"job_id":job_id, "status":"queued"}


@app.post("/download-link/{job_id}", tags=["security"])
async def create_download_link(job_id: str, request: Request, ttl_seconds: int = 600):
    if not 30 <= ttl_seconds <= 3600:
        raise HTTPException(422, "ttl_seconds must be between 30 and 3600")
    with jobs_lock:
        job = _owned_job(job_id, request.state.owner_id)
        if job.get("status") != "completed":
            raise HTTPException(409, "Result is not ready")
    if not security.DOWNLOAD_SECRET:
        raise HTTPException(503, "Set DOWNLOAD_SIGNING_SECRET before enabling temporary links.")
    expires = int(time.time()) + ttl_seconds
    token = security.make_download_token(job_id, request.state.owner_id, expires)
    base_url = os.getenv("PUBLIC_API_URL", "").rstrip("/")
    return {"url": f"{base_url}/download/{job_id}?expires={expires}&token={token}", "expires_at": expires}


@app.get("/report/{job_id}.csv", tags=["reports"])
async def download_csv_report(job_id: str, request: Request):
    with jobs_lock:
        job = _owned_job(job_id, request.state.owner_id)
        summary_path = job.get("summary_file")
    if not summary_path or not Path(summary_path).is_file():
        raise HTTPException(404, "Report is not ready")
    data = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    csv_data = reports.csv_report(data)
    return Response(csv_data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{job_id}-report.csv"'})


@app.get("/report/{job_id}/people", tags=["reports"])
async def people_report(job_id: str, request: Request):
    with jobs_lock:
        job = _owned_job(job_id, request.state.owner_id)
        summary_path = job.get("summary_file")
    if not summary_path or not Path(summary_path).is_file():
        raise HTTPException(404, "Report is not ready")
    data = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    return {"people": reports.person_statistics(data), "totals": data.get("totals", {})}


@app.post("/analyze/batch", tags=["video"])
async def analyze_batch(request: Request, background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    if not files or len(files) > security.MAX_BATCH:
        raise HTTPException(422, f"Upload between 1 and {security.MAX_BATCH} videos per batch")
    created=[]
    for uploaded in files:
        created.append(await analyze_video(
            request, background_tasks, uploaded,
            config.PERSON_CONFIDENCE_THRESHOLD, config.PHONE_CONFIDENCE_THRESHOLD,
            0.08, config.ASSOCIATION_EXPAND_X, config.ASSOCIATION_EXPAND_Y,
            config.TRACKER_MAX_AGE, config.TRACKER_MAX_CENTER_DISTANCE,
            config.ASSOCIATION_TEMPORAL_WINDOW, config.ASSOCIATION_TEMPORAL_MIN_HITS,
            config.INFERENCE_FRAME_STRIDE, config.INFERENCE_MAX_WIDTH,
            config.MAX_VIDEO_DURATION_SECONDS, 0.0, 0.0, 0,
        ))
    return {"jobs": [item.model_dump() for item in created]}


def _run_analysis(job_id: str, upload_path: str) -> None:
    acquired = False
    output_filename = f"{job_id}_analyzed.mp4"
    summary_path = config.RESULTS_DIR / f"{job_id}_analyzed.json"
    try:
        event = cancel_events.get(job_id, threading.Event())
        if event.is_set():
            return
        job_semaphore.acquire()
        acquired = True
        with jobs_lock:
            job = jobs.get(job_id)
            if not job or event.is_set() or job.get("status") == "cancelled":
                return
            job.update({"status": "processing", "progress": 0, "updated_at": _now(), "started_epoch": time.time()})
            settings = dict(job.get("settings", {}))
            _persist_jobs_locked()

        job_detector = Detector(
            person_threshold=settings.get("person_confidence"),
            phone_threshold=settings.get("phone_confidence"),
        )
        tracker = Tracker(
            max_age=settings.get("tracker_max_age", config.TRACKER_MAX_AGE),
            min_iou=config.TRACKER_MIN_IOU,
            max_center_distance=settings.get("tracker_max_center_distance", config.TRACKER_MAX_CENTER_DISTANCE),
        )
        association_config = AssociationConfig(
            expand_x=settings.get("association_expand_x", config.ASSOCIATION_EXPAND_X),
            expand_y=settings.get("association_expand_y", config.ASSOCIATION_EXPAND_Y),
            temporal_window=settings.get("temporal_window", config.ASSOCIATION_TEMPORAL_WINDOW),
            temporal_min_hits=settings.get("temporal_min_hits", config.ASSOCIATION_TEMPORAL_MIN_HITS),
            min_candidate_score=config.ASSOCIATION_MIN_CANDIDATE_SCORE,
            ambiguity_margin=settings.get("association_margin", 0.08),
        )
        processor = VideoProcessor(
            job_detector, tracker, config.RESULTS_DIR, association_config,
            frame_stride=settings.get("frame_stride"),
            max_inference_width=settings.get("max_width"),
            max_duration_seconds=settings.get("max_duration"),
            start_seconds=settings.get("start_seconds", 0), end_seconds=settings.get("end_seconds", 0),
            output_width=settings.get("output_width", 0),
            progress_callback=_progress_callback(job_id),
            cancel_event=event,
        )
        processor.process(upload_path, output_filename)
        result_path = config.RESULTS_DIR / output_filename
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id].update({
                    "status": "completed", "progress": 100,
                    "result_file": str(result_path), "summary_file": str(summary_path),
                    "error": None, "updated_at": _now(),
                })
                _persist_jobs_locked()
                jobs[job_id]["eta_seconds"] = 0
                threading.Thread(target=_base44_callback, args=(dict(jobs[job_id]), "completed"), kwargs={"summary":{"progress":100}}, daemon=True).start()
    except VideoCancelled as exc:
        (config.RESULTS_DIR / output_filename).unlink(missing_ok=True)
        summary_path.unlink(missing_ok=True)
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id].update({"status": "cancelled", "error": str(exc), "updated_at": _now()})
                _persist_jobs_locked()
                threading.Thread(target=_base44_callback, args=(dict(jobs[job_id]), "cancelled"), kwargs={"error":str(exc)}, daemon=True).start()
    except Exception as exc:
        (config.RESULTS_DIR / output_filename).unlink(missing_ok=True)
        summary_path.unlink(missing_ok=True)
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id].update({"status": "failed", "error": str(exc)[:1200], "updated_at": _now()})
                _persist_jobs_locked()
                threading.Thread(target=_base44_callback, args=(dict(jobs[job_id]), "failed"), kwargs={"error":str(exc)[:1200]}, daemon=True).start()
    finally:
        Path(upload_path).unlink(missing_ok=True)
        if acquired:
            job_semaphore.release()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=config.HOST, port=config.PORT, reload=config.DEBUG)
