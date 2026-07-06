"""Tests for splitting the hub's raw feature list into presets and light state."""

from __future__ import annotations

from bnd_garage_client.client import _parse_activity, _split_features
from bnd_garage_client.models import ActivityLogEntry, PresetAction, ToggleState


def test_split_features_empty_list_has_no_presets_or_light() -> None:
    """Test a hub reporting no feature entries at all yields empty results."""
    presets, light = _split_features([])
    assert presets == ()
    assert light is None


def test_split_features_extracts_named_position_presets() -> None:
    """Test non-toggle entries become named PresetAction presets."""
    actions = [
        {"action": {"cmd": 5}, "title": "Pet"},
        {"action": {"cmd": 6}, "title": "Parcel"},
        {"action": {"cmd": 7}, "title": "Ventilation"},
    ]
    presets, light = _split_features(actions)
    assert presets == (
        PresetAction(command=5, label="Pet"),
        PresetAction(command=6, label="Parcel"),
        PresetAction(command=7, label="Ventilation"),
    )
    assert light is None


def test_split_features_light_off_when_cmd_16_listed() -> None:
    """Test cmd 16 (the "turn on" action) means the light is currently off."""
    _, light = _split_features([{"action": {"cmd": 16}, "title": "Light"}])
    assert light == ToggleState(command=16, is_on=False)


def test_split_features_light_on_when_cmd_17_listed() -> None:
    """Test cmd 17 (the "turn off" action) means the light is currently on."""
    _, light = _split_features([{"action": {"cmd": 17}, "title": "Light"}])
    assert light == ToggleState(command=17, is_on=True)


def test_split_features_excludes_auxiliary_relay_from_presets() -> None:
    """Test the auxiliary relay (cmd 18/19) isn't surfaced as a position preset."""
    presets, light = _split_features([{"action": {"cmd": 18}, "title": "Auxiliary"}])
    assert presets == ()
    assert light is None


def test_split_features_full_real_world_response() -> None:
    """Test the exact shape captured from a real hub."""
    actions = [
        {
            "action": {"cmd": 5},
            "icon": "0004",
            "col": 1,
            "title": "Pet",
            "hide": 0,
            "row": 1,
        },
        {
            "action": {"cmd": 6},
            "icon": "0005",
            "col": 2,
            "title": "Parcel",
            "hide": 0,
            "row": 1,
        },
        {
            "action": {"cmd": 7},
            "icon": "0007",
            "col": 3,
            "title": "Ventilation",
            "hide": 0,
            "row": 1,
        },
        {
            "action": {"cmd": 16},
            "icon": "1051",
            "col": 1,
            "title": "Light",
            "hide": -1,
            "row": 2,
        },
        {
            "action": {"cmd": 18},
            "icon": "1102",
            "col": 2,
            "title": "Auxiliary",
            "hide": -1,
            "row": 2,
        },
    ]
    presets, light = _split_features(actions)
    assert presets == (
        PresetAction(command=5, label="Pet"),
        PresetAction(command=6, label="Parcel"),
        PresetAction(command=7, label="Ventilation"),
    )
    assert light == ToggleState(command=16, is_on=False)


def test_parse_activity_returns_none_for_empty_log() -> None:
    """Test a hub reporting no log entry at all yields None."""
    assert _parse_activity({}) is None


def test_parse_activity_parses_real_world_entry() -> None:
    """Test the exact shape captured from a real hub."""
    log = {
        "text": "Closed by HomeAssistant",
        "time": 1783233784793,
        "source": 2,
        "logId": 774627669198754412,
        "alert": 0,
    }
    assert _parse_activity(log) == ActivityLogEntry(
        text="Closed by HomeAssistant",
        log_id=774627669198754412,
        logged_at=1783233784793,
        alert=0,
    )
