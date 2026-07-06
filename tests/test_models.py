"""Tests for HubStatus derivation from raw hub position/rate values."""

from __future__ import annotations

import pytest

from bnd_garage_client.models import (
    DoorState,
    PresetAction,
    ToggleState,
    status_from_raw,
)


@pytest.mark.parametrize(
    ("position", "rate", "expected_state"),
    [
        (0, 0, DoorState.CLOSED),
        (100, 0, DoorState.OPEN),
        (50, 0, DoorState.PARTIAL),
        (50, 5, DoorState.MOVING),
        (0, -5, DoorState.MOVING),
        (-1, 0, DoorState.UNKNOWN),
    ],
)
def test_status_from_raw(position: int, rate: int, expected_state: DoorState) -> None:
    """Test state is derived correctly from raw position/rate values."""
    status = status_from_raw(position=position, rate=rate)
    assert status.state == expected_state
    assert status.position == position
    assert status.rate == rate


def test_status_defaults_to_no_presets_or_light() -> None:
    """Test a hub with no feature entries reports empty presets and no light."""
    status = status_from_raw(position=0, rate=0)
    assert status.presets == ()
    assert status.light is None


def test_status_threads_through_presets_and_light() -> None:
    """Test presets/light passed in are carried onto the resulting HubStatus."""
    presets = (PresetAction(command=5, label="Pet"),)
    light = ToggleState(command=16, is_on=False)
    status = status_from_raw(position=0, rate=0, presets=presets, light=light)
    assert status.presets == presets
    assert status.light == light
