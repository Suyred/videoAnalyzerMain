"""Video processing pipeline with progress, cancellation and frame diagnostics."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List

import cv2

from . import config
from .association import AssociationConfig, PersonPhoneAssociator
from .detector import Detector
from .tracker import Tracker, TrackedObject


@dataclass
class FrameResult:
    frame_number: int
    timestamp_seconds: float
    people_detected: int
    phones_detected: int
    associated_person_ids: List[int]
    associations: List[Dict]


@dataclass
class VideoResult:
    video_filename: str
    total_frames: int
    duration_seconds: float
    fps: float
    processed_frames: int
    frames: List[FrameResult]
    totals: Dict[str, int]


class VideoCancelled(Exception):
    """Raised when a user requests cancellation during video processing."""


class VideoProcessor:
    def __init__(
        self,
        detector: Detector,
        tracker: Tracker,
        output_dir: str | os.PathLike,
        association_config: AssociationConfig | None = None,
        *,
        frame_stride: int | None = None,
        max_inference_width: int | None = None,
        max_duration_seconds: int | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        start_seconds: float = 0.0,
        end_seconds: float = 0.0,
        output_width: int = 0,
    ) -> None:
        self.detector = detector
        self.tracker = tracker
        self.associator = PersonPhoneAssociator(association_config)
        self.output_dir = str(output_dir)
        os.makedirs(self.output_dir, exist_ok=True)
        self.frame_stride = max(1, int(frame_stride or config.INFERENCE_FRAME_STRIDE))
        self.max_inference_width = max(160, int(max_inference_width or config.INFERENCE_MAX_WIDTH))
        self.max_duration_seconds = max(1, int(max_duration_seconds or config.MAX_VIDEO_DURATION_SECONDS))
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event or threading.Event()
        self.start_seconds = max(0.0, float(start_seconds or 0.0))
        self.end_seconds = max(0.0, float(end_seconds or 0.0))
        self.output_width = max(0, int(output_width or 0))

    def _report(self, **values) -> None:
        if self.progress_callback:
            try:
                self.progress_callback(values)
            except Exception:
                # A monitoring callback must never break the video pipeline.
                pass

    @staticmethod
    def _resize_for_inference(frame, max_width: int | None = None):
        height, width = frame.shape[:2]
        max_width = max_width or config.INFERENCE_MAX_WIDTH
        if width <= max_width:
            return frame, 1.0, 1.0
        scale = max_width / width
        new_w, new_h = int(round(width * scale)), int(round(height * scale))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        return resized, width / new_w, height / new_h

    @staticmethod
    def _draw_associated_people(frame, people, associated_ids, scale_x, scale_y):
        # The product rule: draw only confirmed people; never draw phone boxes.
        for person in people:
            if person.age != 0 or person.track_id not in associated_ids:
                continue
            x1 = max(0, int(round(person.x_min * scale_x)))
            y1 = max(0, int(round(person.y_min * scale_y)))
            x2 = min(frame.shape[1] - 1, int(round(person.x_max * scale_x)))
            y2 = min(frame.shape[0] - 1, int(round(person.y_max * scale_y)))
            if x2 <= x1 or y2 <= y1:
                continue
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
            cv2.putText(
                frame, "PHONE", (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2, cv2.LINE_AA,
            )

    def process(self, input_path: str, output_filename: str) -> VideoResult:
        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            raise ValueError("The uploaded file could not be opened as a video.")

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0 or fps > 240:
            fps = 25.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width <= 0 or height <= 0:
            cap.release()
            raise ValueError("The video has invalid dimensions.")

        estimated_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        estimated_duration = estimated_frames / fps if estimated_frames else 0
        if self.start_seconds and estimated_duration and self.start_seconds >= estimated_duration:
            cap.release()
            raise ValueError("start_seconds is beyond the end of the video.")
        if self.end_seconds and estimated_duration and self.end_seconds > estimated_duration:
            cap.release()
            raise ValueError("end_seconds is beyond the end of the video.")
        if estimated_duration > self.max_duration_seconds and not (self.start_seconds or self.end_seconds):
            cap.release()
            raise ValueError(f"Video is longer than the {self.max_duration_seconds}-second limit.")

        if self.start_seconds:
            cap.set(cv2.CAP_PROP_POS_MSEC, self.start_seconds * 1000)
        if self.output_width and self.output_width < width:
            out_height = max(2, int(round(height * self.output_width / width)))
            out_size = (self.output_width, out_height)
        else:
            out_size = (width, height)
        output_path = os.path.join(self.output_dir, output_filename)
        writer = cv2.VideoWriter(
            output_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, out_size
        )
        if not writer.isOpened():
            cap.release()
            raise RuntimeError("Could not initialize the MP4 output writer.")

        frame_results: List[FrameResult] = []
        frame_number = 0
        processed_frames = 0
        last_people: List[TrackedObject] = []
        last_associated_ids: set[int] = set()
        last_scale_x = last_scale_y = 1.0
        total_associations = 0
        max_people_in_frame = 0
        max_phones_in_frame = 0
        completed = False
        last_reported_progress = -1

        try:
            while True:
                if self.cancel_event.is_set():
                    raise VideoCancelled("Analysis was cancelled by the user.")
                ok, frame = cap.read()
                if not ok:
                    completed = True
                    break
                absolute_time = self.start_seconds + frame_number / fps
                if self.end_seconds and absolute_time >= self.end_seconds:
                    completed = True
                    break

                if frame_number % self.frame_stride == 0:
                    inference_frame, scale_x, scale_y = self._resize_for_inference(
                        frame, self.max_inference_width
                    )
                    detections = self.detector.detect(inference_frame)
                    tracks = self.tracker.update(detections)
                    # Keep recently missed tracks for short occlusions, while
                    # only currently visible people can receive a box.
                    people = [t for t in tracks if t.class_name == "person"]
                    phones = [t for t in tracks if t.class_name == "cell phone" and t.age == 0]
                    associated_ids, pairs = self.associator.associate_with_pairs(
                        people, phones, frame_number
                    )
                    visible_people = [p for p in people if p.age == 0]
                    last_people = visible_people
                    last_associated_ids = associated_ids
                    last_scale_x, last_scale_y = scale_x, scale_y
                    total_associations += len(pairs)
                    max_people_in_frame = max(max_people_in_frame, len(visible_people))
                    max_phones_in_frame = max(max_phones_in_frame, len(phones))
                    frame_results.append(FrameResult(
                        frame_number=frame_number,
                        timestamp_seconds=round(absolute_time, 3),
                        people_detected=len(visible_people),
                        phones_detected=len(phones),
                        associated_person_ids=sorted(associated_ids),
                        associations=pairs,
                    ))
                    processed_frames += 1

                self._draw_associated_people(
                    frame, last_people, last_associated_ids, last_scale_x, last_scale_y
                )
                if out_size != (width, height):
                    frame = cv2.resize(frame, out_size, interpolation=cv2.INTER_AREA)
                writer.write(frame)
                frame_number += 1
                if frame_number > int(self.max_duration_seconds * fps):
                    raise ValueError(f"Video exceeded the {self.max_duration_seconds}-second processing limit.")

                if estimated_frames > 0:
                    percent = min(99, int(frame_number * 100 / estimated_frames))
                else:
                    percent = 0
                if percent != last_reported_progress:
                    self._report(
                        progress=percent,
                        processed_frames=processed_frames,
                        total_frames=estimated_frames or None,
                        current_frame=frame_number,
                    )
                    last_reported_progress = percent
        except Exception:
            if os.path.exists(output_path):
                os.remove(output_path)
            raise
        finally:
            cap.release()
            writer.release()
            self.tracker.reset()
            self.associator.reset()

        if not completed or frame_number == 0:
            if os.path.exists(output_path):
                os.remove(output_path)
            raise ValueError("The video contains no readable frames.")

        summary_filename = os.path.splitext(output_filename)[0] + ".json"
        summary_path = os.path.join(self.output_dir, summary_filename)
        result = VideoResult(
            video_filename=os.path.basename(input_path),
            total_frames=frame_number,
            duration_seconds=round(frame_number / fps, 3),
            fps=round(fps, 3),
            processed_frames=processed_frames,
            frames=frame_results,
            totals={
                "associations": total_associations,
                "max_people_in_frame": max_people_in_frame,
                "max_phones_in_frame": max_phones_in_frame,
                "frames_with_associations": sum(bool(item.associated_person_ids) for item in frame_results),
            },
        )
        temporary_summary = summary_path + ".tmp"
        with open(temporary_summary, "w", encoding="utf-8") as stream:
            json.dump(asdict(result), stream, ensure_ascii=False)
        os.replace(temporary_summary, summary_path)
        self._report(progress=100, processed_frames=processed_frames,
                     total_frames=estimated_frames or frame_number, current_frame=frame_number)
        return result
