import numpy as np

from backend.detector import Detector


class FakeNet:
    def __init__(self, output):
        self.output = output

    def setInput(self, _blob):
        pass

    def forward(self):
        return self.output


def test_detector_decodes_person_and_phone_classes(monkeypatch):
    output = np.zeros((1, 84, 100), dtype=np.float32)
    # YOLOv8 output columns: cx, cy, width, height, then 80 class scores.
    output[0, :4, 0] = [320, 240, 100, 200]
    output[0, 4, 0] = 0.95
    output[0, :4, 1] = [320, 300, 24, 36]
    output[0, 4 + 67, 1] = 0.90

    detector = Detector(person_threshold=0.4, phone_threshold=0.25)
    detector._net = FakeNet(output)
    monkeypatch.setattr(detector, "_load_model", lambda: None)

    detections = detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))
    assert {item.class_name for item in detections} == {"person", "cell phone"}
    assert all(0 <= item.x_min < item.x_max <= 640 for item in detections)


def test_detector_returns_empty_for_empty_frame():
    detector = Detector()
    assert detector.detect(np.zeros((0, 0, 3), dtype=np.uint8)) == []
