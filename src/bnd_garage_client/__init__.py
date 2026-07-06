"""Async client for B&D SmartDoorDevices garage hubs."""

from .client import HubClient
from .errors import (
    AmbiguousDeviceError,
    AuthenticationError,
    GarageError,
    HubCommandError,
    HubUnreachableError,
    PairingError,
)
from .models import Credentials, DoorState, HubStatus, PresetAction, ToggleState
from .pairing import pair_new_phone

__all__ = [
    "AmbiguousDeviceError",
    "AuthenticationError",
    "Credentials",
    "DoorState",
    "GarageError",
    "HubClient",
    "HubCommandError",
    "HubStatus",
    "HubUnreachableError",
    "PairingError",
    "PresetAction",
    "ToggleState",
    "pair_new_phone",
]
