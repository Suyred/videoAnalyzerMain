
import numpy as np
from backend.video_processor import VideoProcessor
from backend.tracker import TrackedObject


class DummyDetector:
    pass


def test_rendering_draws_only_associated_person():
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    people = [
        TrackedObject(1, "person", 10, 10, 80, 180, age=0),
        TrackedObject(2, "person", 120, 10, 200, 180, age=0),
    ]
    VideoProcessor._draw_associated_people(frame, people, {1}, 1.0, 1.0)
    # Pixel on first person's top border is red in BGR; second remains black.
    assert tuple(frame[10, 20]) == (0, 0, 255)
    assert tuple(frame[10, 150]) == (0, 0, 0)
