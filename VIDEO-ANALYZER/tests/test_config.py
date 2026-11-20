from backend.association import AssociationConfig, PersonPhoneAssociator
import pytest


def test_invalid_temporal_configuration_is_rejected():
    with pytest.raises(ValueError):
        PersonPhoneAssociator(AssociationConfig(temporal_window=2, temporal_min_hits=3))


def test_single_frame_confirmation_configuration_is_supported():
    assoc = PersonPhoneAssociator(AssociationConfig(temporal_window=1, temporal_min_hits=1))
    assert assoc.config.temporal_window == 1
