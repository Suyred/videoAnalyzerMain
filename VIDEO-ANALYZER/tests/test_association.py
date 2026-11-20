
from backend.association import AssociationConfig, PersonPhoneAssociator
from backend.tracker import TrackedObject


def person(track_id, x1, y1, x2, y2):
    return TrackedObject(track_id, "person", x1, y1, x2, y2)


def phone(track_id, x1, y1, x2, y2):
    return TrackedObject(track_id, "cell phone", x1, y1, x2, y2)


def test_phone_inside_person_becomes_confirmed_after_temporal_hits():
    assoc = PersonPhoneAssociator(AssociationConfig(temporal_window=5, temporal_min_hits=3))
    p, ph = person(1, 0, 0, 200, 400), phone(10, 120, 150, 145, 185)
    assert assoc.associate([p], [ph], 0) == set()
    assert assoc.associate([p], [ph], 1) == set()
    assert assoc.associate([p], [ph], 2) == {1}


def test_person_without_phone_is_never_marked():
    assoc = PersonPhoneAssociator()
    p = person(1, 0, 0, 100, 200)
    for frame in range(8):
        assert assoc.associate([p], [], frame) == set()


def test_far_phone_does_not_associate():
    assoc = PersonPhoneAssociator()
    p, ph = person(1, 0, 0, 100, 200), phone(2, 500, 500, 530, 540)
    assert assoc.associate([p], [ph], 0) == set()


def test_ambiguous_phone_between_overlapping_people_is_not_guessed():
    assoc = PersonPhoneAssociator(AssociationConfig(temporal_window=1, temporal_min_hits=1))
    p1 = person(1, 0, 0, 180, 300)
    p2 = person(2, 80, 0, 260, 300)
    ph = phone(9, 115, 120, 140, 150)
    ids, pairs = assoc.associate_with_pairs([p1, p2], [ph], 0)
    assert ids == set()
    assert pairs == []


def test_phone_near_edge_within_expansion_can_match():
    assoc = PersonPhoneAssociator(AssociationConfig(temporal_window=1, temporal_min_hits=1))
    p, ph = person(1, 100, 100, 200, 300), phone(2, 205, 180, 215, 200)
    assert assoc.associate([p], [ph], 0) == {1}


def test_reset_clears_temporal_history():
    assoc = PersonPhoneAssociator(AssociationConfig(temporal_window=2, temporal_min_hits=2))
    p, ph = person(1, 0, 0, 200, 400), phone(2, 120, 150, 145, 185)
    assoc.associate([p], [ph], 0)
    assoc.associate([p], [ph], 1)
    assert assoc.associate([p], [ph], 2) == {1}
    assoc.reset()
    assert assoc.associate([p], [ph], 3) == set()


def test_ambiguous_phone_between_two_people_is_left_unassigned():
    assoc = PersonPhoneAssociator(AssociationConfig(
        temporal_window=1, temporal_min_hits=1, ambiguity_margin=0.08
    ))
    p1 = person(1, 0, 0, 180, 300)
    p2 = person(2, 80, 0, 260, 300)
    ph = phone(9, 115, 120, 140, 150)
    ids, pairs = assoc.associate_with_pairs([p1, p2], [ph], 0)
    assert ids == set()
    assert pairs == []


def test_clear_phone_person_match_is_assigned_once():
    assoc = PersonPhoneAssociator(AssociationConfig(temporal_window=1, temporal_min_hits=1))
    p1 = person(1, 0, 0, 150, 300)
    p2 = person(2, 180, 0, 330, 300)
    ph = phone(9, 100, 120, 120, 150)
    ids, pairs = assoc.associate_with_pairs([p1, p2], [ph], 0)
    assert ids == {1}
    assert len(pairs) == 1


def test_history_survives_short_tracker_occlusion():
    assoc = PersonPhoneAssociator(AssociationConfig(temporal_window=3, temporal_min_hits=2))
    p = person(1, 0, 0, 200, 400)
    ph = phone(2, 120, 150, 145, 185)
    assert assoc.associate([p], [ph], 0) == set()
    # A stale track remains active but is not considered currently visible.
    stale = TrackedObject(1, "person", 0, 0, 200, 400, age=1)
    assert assoc.associate([stale], [], 1) == set()
    assert assoc.associate([p], [ph], 2) == {1}
