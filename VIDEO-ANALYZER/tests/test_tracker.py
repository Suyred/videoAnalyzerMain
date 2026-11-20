
from backend.detector import Detection
from backend.tracker import Tracker


def test_tracker_keeps_id_for_overlapping_detections():
    tracker = Tracker()
    first = tracker.update([Detection("person", .9, 10, 10, 100, 200)])
    second = tracker.update([Detection("person", .9, 14, 12, 104, 202)])
    assert first[0].track_id == second[0].track_id


def test_tracker_creates_new_id_for_different_class():
    tracker = Tracker()
    first = tracker.update([Detection("person", .9, 10, 10, 100, 200)])
    second = tracker.update([Detection("cell phone", .9, 20, 20, 30, 40)])
    phone_track = next(track for track in second if track.class_name == "cell phone")
    assert first[0].track_id != phone_track.track_id


def test_tracker_expires_old_track():
    tracker = Tracker(max_age=1)
    tracker.update([Detection("person", .9, 10, 10, 100, 200)])
    tracker.update([])
    tracks = tracker.update([])
    assert tracks == []


def test_reset_restarts_temporary_ids():
    tracker = Tracker()
    assert tracker.update([Detection("person", .9, 0, 0, 50, 100)])[0].track_id == 1
    tracker.reset()
    assert tracker.update([Detection("person", .9, 0, 0, 50, 100)])[0].track_id == 1
