import threading

import cv2
import numpy as np
import pytest

from backend.association import AssociationConfig
from backend.detector import Detection
from backend.tracker import Tracker
from backend.video_processor import VideoCancelled, VideoProcessor


class FixedDetector:
    def detect(self, frame):
        return [
            Detection("person", 0.95, 30, 20, 180, 220),
            Detection("cell phone", 0.90, 105, 110, 125, 145),
        ]


def make_video(path, frame_count=8):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 8, (240, 240))
    assert writer.isOpened()
    for _ in range(frame_count):
        writer.write(np.zeros((240, 240, 3), dtype=np.uint8))
    writer.release()


def test_processor_creates_video_summary_and_progress(tmp_path):
    source = tmp_path / "input.mp4"
    make_video(source)
    updates = []
    processor = VideoProcessor(
        FixedDetector(), Tracker(), tmp_path / "out",
        AssociationConfig(temporal_window=3, temporal_min_hits=2),
        frame_stride=1, max_inference_width=240,
        progress_callback=updates.append,
    )
    result = processor.process(str(source), "result.mp4")
    assert result.total_frames == 8
    assert result.processed_frames == 8
    assert result.totals["associations"] > 0
    assert (tmp_path / "out" / "result.mp4").is_file()
    assert (tmp_path / "out" / "result.json").is_file()
    assert updates[-1]["progress"] == 100


def test_processor_honors_pre_requested_cancellation(tmp_path):
    source = tmp_path / "input.mp4"
    make_video(source)
    event = threading.Event()
    event.set()
    processor = VideoProcessor(
        FixedDetector(), Tracker(), tmp_path / "out",
        cancel_event=event,
    )
    with pytest.raises(VideoCancelled):
        processor.process(str(source), "cancelled.mp4")
    assert not (tmp_path / "out" / "cancelled.mp4").exists()
