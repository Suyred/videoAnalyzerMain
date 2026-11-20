"""Environment-backed configuration for VIDEO-ANALYZER."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(ROOT_DIR / "uploads")))
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", str(ROOT_DIR / "results")))
MODEL_DIR = Path(os.getenv("MODEL_DIR", str(ROOT_DIR / "models")))
JOBS_FILE = Path(os.getenv("JOBS_FILE", str(RESULTS_DIR / "jobs.json")))

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "200"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_VIDEO_DURATION_SECONDS = int(os.getenv("MAX_VIDEO_DURATION_SECONDS", "180"))
ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm"}
ALLOWED_MIME_TYPES = {
    "video/mp4", "video/quicktime", "video/x-msvideo",
    "video/matroska", "video/x-matroska", "video/webm",
    "application/octet-stream",
}

MODEL_PATH = Path(os.getenv("ONNX_MODEL_PATH", str(MODEL_DIR / "yolov8n.onnx")))
MODEL_URL = os.getenv(
    "ONNX_MODEL_URL",
    "https://huggingface.co/unileon-robotics/YOLO8-ONNX/resolve/main/yolov8n.onnx",
)
MODEL_SHA256 = os.getenv(
    "ONNX_MODEL_SHA256",
    "fa2c856b0effedce4f49fffd121f438247a64c8f633bf0e6d5e5960e869ccc5a",
)
MODEL_INPUT_WIDTH = int(os.getenv("MODEL_INPUT_WIDTH", "640"))
MODEL_INPUT_HEIGHT = int(os.getenv("MODEL_INPUT_HEIGHT", "480"))
PERSON_CONFIDENCE_THRESHOLD = float(os.getenv("PERSON_CONFIDENCE_THRESHOLD", "0.40"))
PHONE_CONFIDENCE_THRESHOLD = float(os.getenv("PHONE_CONFIDENCE_THRESHOLD", "0.02"))
NMS_THRESHOLD = float(os.getenv("NMS_THRESHOLD", "0.45"))

TRACKER_MAX_AGE = int(os.getenv("TRACKER_MAX_AGE", "12"))
TRACKER_MIN_IOU = float(os.getenv("TRACKER_MIN_IOU", "0.05"))
TRACKER_MAX_CENTER_DISTANCE = float(os.getenv("TRACKER_MAX_CENTER_DISTANCE", "0.22"))

ASSOCIATION_EXPAND_X = float(os.getenv("ASSOCIATION_EXPAND_X", "0.12"))
ASSOCIATION_EXPAND_Y = float(os.getenv("ASSOCIATION_EXPAND_Y", "0.08"))
ASSOCIATION_TEMPORAL_WINDOW = int(os.getenv("ASSOCIATION_TEMPORAL_WINDOW", "5"))
ASSOCIATION_TEMPORAL_MIN_HITS = int(os.getenv("ASSOCIATION_TEMPORAL_MIN_HITS", "3"))
ASSOCIATION_MIN_CANDIDATE_SCORE = float(os.getenv("ASSOCIATION_MIN_CANDIDATE_SCORE", "0.20"))

INFERENCE_MAX_WIDTH = int(os.getenv("INFERENCE_MAX_WIDTH", "960"))
INFERENCE_FRAME_STRIDE = max(1, int(os.getenv("INFERENCE_FRAME_STRIDE", "2")))
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "1")))
MAX_QUEUED_JOBS = max(1, int(os.getenv("MAX_QUEUED_JOBS", "5")))
RESULT_RETENTION_HOURS = max(1, int(os.getenv("RESULT_RETENTION_HOURS", "24")))
CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()]

for directory in (UPLOAD_DIR, RESULTS_DIR, MODEL_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def _require_range(name: str, value: float, minimum: float, maximum: float) -> None:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}; received {value}.")


if MAX_UPLOAD_SIZE_MB < 1 or MAX_VIDEO_DURATION_SECONDS < 1:
    raise ValueError("Upload size and maximum video duration must be positive.")
if MODEL_INPUT_WIDTH < 160 or MODEL_INPUT_HEIGHT < 160:
    raise ValueError("ONNX model input dimensions must each be at least 160 pixels.")
_require_range("PERSON_CONFIDENCE_THRESHOLD", PERSON_CONFIDENCE_THRESHOLD, 0.0, 1.0)
_require_range("PHONE_CONFIDENCE_THRESHOLD", PHONE_CONFIDENCE_THRESHOLD, 0.0, 1.0)
_require_range("NMS_THRESHOLD", NMS_THRESHOLD, 0.0, 1.0)
_require_range("TRACKER_MIN_IOU", TRACKER_MIN_IOU, 0.0, 1.0)
_require_range("TRACKER_MAX_CENTER_DISTANCE", TRACKER_MAX_CENTER_DISTANCE, 0.0, 2.0)
_require_range("ASSOCIATION_EXPAND_X", ASSOCIATION_EXPAND_X, 0.0, 1.0)
_require_range("ASSOCIATION_EXPAND_Y", ASSOCIATION_EXPAND_Y, 0.0, 1.0)
_require_range("ASSOCIATION_MIN_CANDIDATE_SCORE", ASSOCIATION_MIN_CANDIDATE_SCORE, 0.0, 1.0)
if TRACKER_MAX_AGE < 1:
    raise ValueError("TRACKER_MAX_AGE must be at least 1.")
if ASSOCIATION_TEMPORAL_WINDOW < 1 or not 1 <= ASSOCIATION_TEMPORAL_MIN_HITS <= ASSOCIATION_TEMPORAL_WINDOW:
    raise ValueError("Association temporal hit settings are inconsistent.")
