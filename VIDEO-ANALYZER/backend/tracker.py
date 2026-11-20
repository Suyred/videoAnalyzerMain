
"""Lightweight class-aware IoU/centroid tracker with temporary IDs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List
import math

from .detector import Detection


@dataclass
class TrackedObject:
    track_id: int
    class_name: str
    x_min: int
    y_min: int
    x_max: int
    y_max: int
    age: int = 0
    confidence: float = 0.0
    vx: float = 0.0
    vy: float = 0.0


class Tracker:
    def __init__(
        self,
        max_age: int = 12,
        min_iou: float = 0.05,
        max_center_distance: float = 0.22,
    ) -> None:
        self.max_age = max_age
        self.min_iou = min_iou
        self.max_center_distance = max_center_distance
        self._tracks: Dict[int, TrackedObject] = {}
        self._next_id = 1

    @staticmethod
    def _iou(a, b) -> float:
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        x2, y2 = min(a[2], b[2]), min(a[3], b[3])
        intersection = max(0, x2 - x1) * max(0, y2 - y1)
        area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
        area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
        union = area_a + area_b - intersection
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _center_distance_ratio(a, b) -> float:
        acx, acy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
        bcx, bcy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        diagonal = max(1.0, math.hypot(a[2] - a[0], a[3] - a[1]))
        return math.hypot(acx - bcx, acy - bcy) / diagonal

    def update(self, detections: List[Detection]) -> List[TrackedObject]:
        candidates = []
        tracks = list(self._tracks.values())
        for track in tracks:
            old_box = (track.x_min, track.y_min, track.x_max, track.y_max)
            for index, detection in enumerate(detections):
                if detection.class_name != track.class_name:
                    continue
                new_box = (detection.x_min, detection.y_min, detection.x_max, detection.y_max)
                iou = self._iou(old_box, new_box)
                distance = self._center_distance_ratio(old_box, new_box)
                if iou >= self.min_iou or distance <= self.max_center_distance:
                    candidates.append((iou - distance * 0.15, iou, track.track_id, index))

        candidates.sort(reverse=True)
        matched_tracks, matched_detections = set(), set()
        detection_by_id = {}
        for _, _, track_id, index in candidates:
            if track_id in matched_tracks or index in matched_detections:
                continue
            matched_tracks.add(track_id)
            matched_detections.add(index)
            detection_by_id[track_id] = detections[index]

        updated = []
        for track in tracks:
            detection = detection_by_id.get(track.track_id)
            if detection is not None:
                dx = ((detection.x_min+detection.x_max) - (track.x_min+track.x_max)) / 2
                dy = ((detection.y_min+detection.y_max) - (track.y_min+track.y_max)) / 2
                updated.append(TrackedObject(
                    track_id=track.track_id,
                    class_name=detection.class_name,
                    x_min=detection.x_min, y_min=detection.y_min,
                    x_max=detection.x_max, y_max=detection.y_max,
                    age=0, confidence=detection.confidence,
                    vx=0.65*track.vx+0.35*dx, vy=0.65*track.vy+0.35*dy,
                ))
            else:
                track.age += 1
                if track.age <= self.max_age:
                    updated.append(track)

        for index, detection in enumerate(detections):
            if index in matched_detections:
                continue
            updated.append(TrackedObject(
                track_id=self._next_id,
                class_name=detection.class_name,
                x_min=detection.x_min, y_min=detection.y_min,
                x_max=detection.x_max, y_max=detection.y_max,
                age=0, confidence=detection.confidence, vx=0.0, vy=0.0,
            ))
            self._next_id += 1

        self._tracks = {track.track_id: track for track in updated}
        return updated

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1
