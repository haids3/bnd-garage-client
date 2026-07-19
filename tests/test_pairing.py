"""Tests for pairing.py's pure device-ID discovery logic.

The handshake itself isn't unit tested here, matching this repo's existing
convention (see test_client.py/test_sdk_client.py) - only the pure
`_device_ids_from_payload` parser is, since everything else in pairing.py is
real HTTP calls validated live against a hub.
"""

from __future__ import annotations

from bnd_garage_client.pairing import _device_ids_from_payload


def test_device_ids_from_payload_empty_dict_yields_no_devices() -> None:
    """Test a reply with no devicePermissions at all yields an empty tuple."""
    assert _device_ids_from_payload({}) == ()


def test_device_ids_from_payload_single_device() -> None:
    """Test a single-key devicePermissions map yields that one device ID."""
    data = {"devicePermissions": {"dev1": {}}}
    assert _device_ids_from_payload(data) == ("dev1",)


def test_device_ids_from_payload_multiple_devices() -> None:
    """Test a hub with multiple doors yields every device ID, not just one.

    This is the real-world shape for a multi-door hub: one paired phone's
    devicePermissions map is keyed by every device it can control.
    """
    data = {"devicePermissions": {"dev1": {}, "dev2": {}}}
    assert _device_ids_from_payload(data) == ("dev1", "dev2")


def test_device_ids_from_payload_reads_nested_data_field() -> None:
    """Test devicePermissions is also found nested under a "data" wrapper."""
    data = {"data": {"devicePermissions": {"dev1": {}}}}
    assert _device_ids_from_payload(data) == ("dev1",)
