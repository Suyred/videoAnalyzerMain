
"""Conservative temporal association of detected phones to tracked people.

A phone is assigned to at most one person in a frame. A person is marked only
after the association is observed in enough sampled frames. This is a visual
heuristic, not proof that the person is holding or using the phone.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Set, Tuple

from .tracker import TrackedObject


@dataclass
class AssociationConfig:
    expand_x: float = 0.12
    expand_y: float = 0.08
    temporal_window: int = 5
    temporal_min_hits: int = 3
    min_candidate_score: float = 0.20
    ambiguity_margin: float = 0.08


class PersonPhoneAssociator:
    def __init__(self, config: AssociationConfig | None = None) -> None:
        self.config = config or AssociationConfig()
        if self.config.temporal_window < 1:
            raise ValueError("temporal_window must be at least 1")
        if not 1 <= self.config.temporal_min_hits <= self.config.temporal_window:
            raise ValueError("temporal_min_hits must be within temporal_window")
        self._history: Dict[int, Deque[bool]] = {}

    def _score(self, person: TrackedObject, phone: TrackedObject) -> float | None:
        pw = max(1, person.x_max - person.x_min)
        ph = max(1, person.y_max - person.y_min)
        phone_cx = (phone.x_min + phone.x_max) / 2.0
        phone_cy = (phone.y_min + phone.y_max) / 2.0

        left = person.x_min - pw * self.config.expand_x
        right = person.x_max + pw * self.config.expand_x
        top = person.y_min - ph * self.config.expand_y
        bottom = person.y_max + ph * self.config.expand_y
        if not (left <= phone_cx <= right and top <= phone_cy <= bottom):
            return None

        # Normalize to the person's box. This favors the closest plausible
        # person when two people overlap in the image.
        nx = (phone_cx - person.x_min) / pw
        ny = (phone_cy - person.y_min) / ph
        distance = ((nx - 0.5) ** 2 + (ny - 0.52) ** 2) ** 0.5
        score = max(0.0, 1.0 - distance / 0.9)
        return score if score >= self.config.min_candidate_score else None

    def associate_with_pairs(
        self, people: List[TrackedObject], phones: List[TrackedObject], frame_number: int
    ) -> Tuple[Set[int], List[dict]]:
        current: Set[int] = set()
        pairs: List[dict] = []

        # A detected phone may belong to only one person in a frame.
        for phone in phones:
            candidates = []
            for person in people:
                score = self._score(person, phone)
                if score is not None:
                    candidates.append((score, person.track_id, person))
            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                score, _, person = candidates[0]
                # Avoid confidently assigning a phone when two people are almost
                # equally plausible in an overlapping/crowded scene.
                if len(candidates) > 1 and score - candidates[1][0] < self.config.ambiguity_margin:
                    continue
                current.add(person.track_id)
                phone_relative_y = (((phone.y_min + phone.y_max) / 2.0) - person.y_min) / max(1, person.y_max - person.y_min)
                pairs.append({
                    "person_track_id": person.track_id,
                    "phone_track_id": phone.track_id,
                    "association_score": round(score, 3),
                    "person_detection_confidence": round(float(person.confidence), 3),
                    "phone_detection_confidence": round(float(phone.confidence), 3),
                    "confidence": round((score * max(0.0, min(1.0, person.confidence)) * max(0.0, min(1.0, phone.confidence))) ** (1/3), 3),
                    "pose_hint": "phone_in_upper_or_mid_body_region" if 0.12 <= phone_relative_y <= 0.82 else "position_uncertain",
                    "frame_number": frame_number,
                })

        visible_ids = {person.track_id for person in people if person.age == 0}
        active_ids = {person.track_id for person in people}
        for person_id in visible_ids:
            history = self._history.setdefault(
                person_id, deque(maxlen=self.config.temporal_window)
            )
            history.append(person_id in current)

        # Keep short histories for temporarily missed people, but bound memory.
        for person_id in list(self._history):
            if person_id not in active_ids:
                del self._history[person_id]

        confirmed = {
            person_id for person_id in visible_ids
            if sum(self._history.get(person_id, ())) >= self.config.temporal_min_hits
        }
        return confirmed, pairs

    def associate(
        self, people: List[TrackedObject], phones: List[TrackedObject], frame_number: int
    ) -> Set[int]:
        return self.associate_with_pairs(people, phones, frame_number)[0]

    def reset(self) -> None:
        self._history.clear()
