
"""YOLOv8n ONNX object detector powered only by OpenCV DNN.

COCO classes used:
  0  = person
  67 = cell phone

The model is downloaded lazily if it is not already present. No PyTorch or
Ultralytics runtime is imported by this application.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import threading
import urllib.request
from pathlib import Path
from typing import List

import cv2
import numpy as np

from . import config


@dataclass
class Detection:
    class_name: str
    confidence: float
    x_min: int
    y_min: int
    x_max: int
    y_max: int


class Detector:
    def __init__(
        self,
        confidence_threshold: float | None = None,
        person_threshold: float | None = None,
        phone_threshold: float | None = None,
    ) -> None:
        self.confidence_threshold = 0.0 if confidence_threshold is None else confidence_threshold
        self.person_threshold = (
            config.PERSON_CONFIDENCE_THRESHOLD if person_threshold is None else person_threshold
        )
        self.phone_threshold = (
            config.PHONE_CONFIDENCE_THRESHOLD if phone_threshold is None else phone_threshold
        )
        self._net = None
        self._lock = threading.RLock()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _ensure_model_file(self) -> None:
        model_path = config.MODEL_PATH
        model_path.parent.mkdir(parents=True, exist_ok=True)
        if model_path.is_file() and model_path.stat().st_size > 1_000_000:
            expected = config.MODEL_SHA256
            if not expected or self._sha256(model_path) == expected:
                return
            model_path.unlink(missing_ok=True)

        temporary = model_path.with_suffix(model_path.suffix + ".download")
        request = urllib.request.Request(
            config.MODEL_URL,
            headers={"User-Agent": "VIDEO-ANALYZER/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            if temporary.stat().st_size < 1_000_000:
                raise RuntimeError("Downloaded ONNX model is unexpectedly small.")
            actual = self._sha256(temporary)
            if config.MODEL_SHA256 and actual.lower() != config.MODEL_SHA256.lower():
                raise RuntimeError("ONNX model checksum did not match the verified checksum.")
            temporary.replace(model_path)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(
                "ONNX model is missing and automatic download failed. "
                "Check the server's outbound internet access or place the model "
                f"at {model_path}. Details: {exc}"
            ) from exc

    def _load_model(self) -> None:
        if self._net is not None:
            return
        self._ensure_model_file()
        net = cv2.dnn.readNetFromONNX(str(config.MODEL_PATH))
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._net = net

    @staticmethod
    def _letterbox(frame: np.ndarray):
        height, width = frame.shape[:2]
        target_w, target_h = config.MODEL_INPUT_WIDTH, config.MODEL_INPUT_HEIGHT
        scale = min(target_w / width, target_h / height)
        new_w, new_h = int(round(width * scale)), int(round(height * scale))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((target_h, target_w, 3), 114, dtype=np.uint8)
        pad_x = (target_w - new_w) // 2
        pad_y = (target_h - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        return canvas, scale, pad_x, pad_y

    def detect(self, frame: np.ndarray) -> List[Detection]:
        if frame is None or frame.size == 0:
            return []

        # OpenCV's DNN Net is not guaranteed to be thread-safe. Serialize inference.
        with self._lock:
            self._load_model()
            canvas, scale, pad_x, pad_y = self._letterbox(frame)
            blob = cv2.dnn.blobFromImage(
                canvas, scalefactor=1.0 / 255.0,
                size=(config.MODEL_INPUT_WIDTH, config.MODEL_INPUT_HEIGHT),
                mean=(0, 0, 0), swapRB=True, crop=False,
            )
            self._net.setInput(blob)
            raw = self._net.forward()

        output = np.asarray(raw)
        output = np.squeeze(output)
        if output.ndim != 2:
            raise RuntimeError(f"Unexpected ONNX output shape: {np.asarray(raw).shape}")

        # YOLOv8 export commonly returns [84, N]; accept [N, 84] as well.
        if output.shape[0] <= 100 and output.shape[1] > output.shape[0]:
            output = output.T
        if output.shape[1] < 84:
            raise RuntimeError(f"Unexpected YOLOv8 class dimension: {output.shape}")

        height, width = frame.shape[:2]
        detections: List[Detection] = []
        per_class = {0: [], 67: []}

        for row in output:
            scores = row[4:84]
            if scores.size < 80:
                continue
            class_id = int(np.argmax(scores))
            if class_id not in per_class:
                continue
            confidence = float(scores[class_id])
            threshold = (
                self.person_threshold if class_id == 0
                else self.phone_threshold
            )
            threshold = max(threshold, self.confidence_threshold if class_id == 0 else 0.0)
            if confidence < threshold:
                continue

            cx, cy, box_w, box_h = map(float, row[:4])
            x1 = int(round((cx - box_w / 2 - pad_x) / scale))
            y1 = int(round((cy - box_h / 2 - pad_y) / scale))
            x2 = int(round((cx + box_w / 2 - pad_x) / scale))
            y2 = int(round((cy + box_h / 2 - pad_y) / scale))
            x1, x2 = max(0, min(width - 1, x1)), max(0, min(width, x2))
            y1, y2 = max(0, min(height - 1, y1)), max(0, min(height, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            per_class[class_id].append((x1, y1, x2 - x1, y2 - y1, confidence))

        for class_id, items in per_class.items():
            if not items:
                continue
            boxes = [[x, y, w, h] for x, y, w, h, _ in items]
            scores = [score for *_, score in items]
            indices = cv2.dnn.NMSBoxes(boxes, scores, 0.01, config.NMS_THRESHOLD)
            if len(indices) == 0:
                continue
            for idx in np.asarray(indices).reshape(-1):
                x, y, w, h, score = items[int(idx)]
                detections.append(Detection(
                    class_name="person" if class_id == 0 else "cell phone",
                    confidence=float(score),
                    x_min=x, y_min=y, x_max=x + w, y_max=y + h,
                ))
        return detections
