"""Exception hierarchy for bnd-garage-client."""

from __future__ import annotations


class GarageError(Exception):
    """Base class for every error this client raises."""


class HubUnreachableError(GarageError):
    """The hub (or the vendor's cloud endpoint, during pairing) could not be reached."""


class AuthenticationError(GarageError):
    """The hub rejected the credentials presented to it."""


class HubCommandError(GarageError):
    """The hub returned an explicit error for a command.

    `code`/`message` are taken verbatim from the hub's own error payload.
    """

    def __init__(self, code: int | str, message: str) -> None:
        """Initialize with the hub's own error code and message."""
        super().__init__(f"hub rejected command ({code}): {message}")
        self.code = code
        self.message = message


class PairingError(GarageError):
    """The one-time phone/device pairing flow failed."""


class AmbiguousDeviceError(PairingError):
    """Pairing found more than one controllable device on the hub.

    `devices` lists (name, device_id) for each candidate found; the caller
    should ask the user to pick one rather than guessing.
    """

    def __init__(self, devices: list[tuple[str, str]]) -> None:
        """Initialize with the ambiguous set of discovered devices."""
        super().__init__(f"found {len(devices)} devices, expected exactly one")
        self.devices = devices
