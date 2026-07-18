"""Tests for splitting the hub's raw feature list into presets and light state."""

from __future__ import annotations

import pytest

from bnd_garage_client.client import (
    _action_for_command,
    _hub_info_from_raw,
    _parse_activity,
    _parse_device_logs,
    _parse_wifi_diagnostics,
    _percent_open_command,
    _split_features,
)
from bnd_garage_client.models import (
    ActivityLogEntry,
    HubInfo,
    PresetAction,
    ToggleState,
    WifiSample,
)


def test_split_features_empty_list_has_no_presets_or_toggles() -> None:
    """Test a hub reporting no feature entries at all yields empty results."""
    presets, toggles = _split_features([])
    assert presets == ()
    assert toggles == (None, None, None, None)


def test_split_features_extracts_named_position_presets() -> None:
    """Test non-toggle entries become named PresetAction presets."""
    actions = [
        {"action": {"cmd": 5}, "title": "Pet"},
        {"action": {"cmd": 6}, "title": "Parcel"},
        {"action": {"cmd": 7}, "title": "Ventilation"},
    ]
    presets, toggles = _split_features(actions)
    assert presets == (
        PresetAction(command=5, label="Pet"),
        PresetAction(command=6, label="Parcel"),
        PresetAction(command=7, label="Ventilation"),
    )
    assert toggles == (None, None, None, None)


def test_split_features_light_off_when_cmd_16_listed() -> None:
    """Test cmd 16 (the "turn on" action) means the light is currently off."""
    _, (light, _, _, _) = _split_features([{"action": {"cmd": 16}, "title": "Light"}])
    assert light == ToggleState(command=16, is_on=False)


def test_split_features_light_on_when_cmd_17_listed() -> None:
    """Test cmd 17 (the "turn off" action) means the light is currently on."""
    _, (light, _, _, _) = _split_features([{"action": {"cmd": 17}, "title": "Light"}])
    assert light == ToggleState(command=17, is_on=True)


def test_split_features_excludes_auxiliary_relay_from_presets() -> None:
    """Test the auxiliary relay (cmd 18/19) isn't surfaced as a position preset."""
    presets, (_, auxiliary, _, _) = _split_features(
        [{"action": {"cmd": 18}, "title": "Auxiliary"}]
    )
    assert presets == ()
    assert auxiliary == ToggleState(command=18, is_on=False)


def test_split_features_auxiliary_off_when_cmd_18_listed() -> None:
    """Test cmd 18 (the "turn on" action) means auxiliary is currently off."""
    _, (_, auxiliary, _, _) = _split_features(
        [{"action": {"cmd": 18}, "title": "Auxiliary"}]
    )
    assert auxiliary == ToggleState(command=18, is_on=False)


def test_split_features_auxiliary_on_when_cmd_19_listed() -> None:
    """Test cmd 19 (the "turn off" action) means auxiliary is currently on."""
    _, (_, auxiliary, _, _) = _split_features(
        [{"action": {"cmd": 19}, "title": "Auxiliary"}]
    )
    assert auxiliary == ToggleState(command=19, is_on=True)


def test_split_features_excludes_remote_control_lockout_from_presets() -> None:
    """Test the "Lockout" aux entry (cmd 20/21) isn't surfaced as a preset button."""
    presets, (_, _, remote_control_lockout, _) = _split_features(
        [{"action": {"cmd": 20}, "title": "Lockout"}]
    )
    assert presets == ()
    assert remote_control_lockout == ToggleState(command=20, is_on=False)


def test_split_features_remote_control_lockout_on_when_cmd_21_listed() -> None:
    """Test cmd 21 (the "turn off" action) means it's currently locked out."""
    _, (_, _, remote_control_lockout, _) = _split_features(
        [{"action": {"cmd": 21}, "title": "Lockout"}]
    )
    assert remote_control_lockout == ToggleState(command=21, is_on=True)


def test_split_features_phone_lockout_uses_base_encoding_not_cmd() -> None:
    """Test the phone-lockout aux entry, encoded as {"base": N} (N = cmd - 256)
    rather than {"cmd": N} since its command codes are >= 256 - and isn't
    surfaced as a preset button either.
    """
    presets, (_, _, _, phone_lockout) = _split_features(
        [{"action": {"base": 2}, "title": "Lockout"}]
    )
    assert presets == ()
    assert phone_lockout == ToggleState(command=258, is_on=False)


def test_split_features_phone_lockout_on_when_base_1_listed() -> None:
    """Test base=1 (cmd 257, the "turn off" action) means it's currently locked out."""
    _, (_, _, _, phone_lockout) = _split_features(
        [{"action": {"base": 1}, "title": "Lockout"}]
    )
    assert phone_lockout == ToggleState(command=257, is_on=True)


def test_split_features_full_real_world_response() -> None:
    """Test the exact shape captured from a real hub, including both
    "Lockout" entries sharing a title but using different encodings.
    """
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
        {
            "action": {"cmd": 20},
            "icon": "1452",
            "col": 1,
            "title": "Lockout",
            "hide": 0,
            "row": 3,
        },
        {
            "action": {"base": 2},
            "icon": "1402",
            "col": 2,
            "title": "Lockout",
            "hide": 0,
            "row": 3,
        },
    ]
    presets, toggles = _split_features(actions)
    light, auxiliary, remote_control_lockout, phone_lockout = toggles
    assert presets == (
        PresetAction(command=5, label="Pet"),
        PresetAction(command=6, label="Parcel"),
        PresetAction(command=7, label="Ventilation"),
    )
    assert light == ToggleState(command=16, is_on=False)
    assert auxiliary == ToggleState(command=18, is_on=False)
    assert remote_control_lockout == ToggleState(command=20, is_on=False)
    assert phone_lockout == ToggleState(command=258, is_on=False)


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


def test_parse_device_logs_drops_hidden_log_types() -> None:
    """Test logType 0 and 21 are filtered out, matching the vendor app's own view."""
    logs = [
        {"text": "Opened", "time": 1, "logId": 1, "alert": 0, "logType": 5},
        {"text": "hidden-0", "time": 2, "logId": 2, "alert": 0, "logType": 0},
        {"text": "hidden-21", "time": 3, "logId": 3, "alert": 0, "logType": 21},
        {"text": "Closed", "time": 4, "logId": 4, "alert": 0, "logType": 6},
    ]
    parsed = _parse_device_logs(logs)
    assert [entry.text for entry in parsed] == ["Opened", "Closed"]


def test_parse_device_logs_empty_list() -> None:
    """Test an empty log history yields an empty list."""
    assert _parse_device_logs([]) == []


def test_hub_info_from_raw_parses_human_readable_keys() -> None:
    """Test the control API's string-keyed hub info shape, serial from AP Name."""
    data = {
        "Hub Name": "Garage",
        "AP Name": "B&D50002BFF",
        "Hub Version": "1086",
        "Hub Firmware": "920Mhz not supported",
        "Timezone": "Australia/Melbourne",
        "Saved Network": "MyWifi",
        "IP Address": "192.168.3.196",
        "MAC Address": "ac:64:cf:c2:85:7e",
        "Wi-Fi Signal": "Good",
    }
    info = _hub_info_from_raw(data)
    assert info == HubInfo(
        name="Garage",
        ap_name="B&D50002BFF",
        serial_number="50002BFF",
        version="1086",
        firmware="920Mhz not supported",
        timezone="Australia/Melbourne",
        saved_network="MyWifi",
        ip_address="192.168.3.196",
        mac_address="ac:64:cf:c2:85:7e",
        wifi_signal="Good",
    )


def test_parse_wifi_diagnostics_parses_xy_samples() -> None:
    """Test the {x, y} sample array shape."""
    samples = [{"x": 1700000000000, "y": -55}, {"x": 1700000060000, "y": -50}]
    assert _parse_wifi_diagnostics(samples) == [
        WifiSample(at=1700000000000, signal_dbm=-55),
        WifiSample(at=1700000060000, signal_dbm=-50),
    ]


def test_parse_wifi_diagnostics_empty_list() -> None:
    """Test no samples (e.g. the suspected vendor bug) yields an empty list."""
    assert _parse_wifi_diagnostics([]) == []


def test_action_for_command_uses_cmd_below_256() -> None:
    """Test ordinary command codes use the {cmd} shape."""
    assert _action_for_command(4) == {"cmd": 4}


def test_action_for_command_uses_base_at_or_above_256() -> None:
    """Test codes >= 256 (e.g. PHONE_LOCKOUT_ON = 258) split into {base}."""
    assert _action_for_command(258) == {"base": 2}
    assert _action_for_command(256) == {"base": 0}


@pytest.mark.parametrize(
    ("percent", "expected_cmd"),
    [(5, 32), (50, 41), (95, 50)],
)
def test_percent_open_command_matches_formula(percent: int, expected_cmd: int) -> None:
    """Test the percent->cmd formula, pinned against the live-tested 50%->41 case."""
    assert _percent_open_command(percent) == expected_cmd


@pytest.mark.parametrize("percent", [0, 4, 6, 96, 100, -5])
def test_percent_open_command_rejects_invalid_values(percent: int) -> None:
    """Test values outside 5-95 or not a multiple of 5 are rejected."""
    with pytest.raises(ValueError, match="percent must be"):
        _percent_open_command(percent)
