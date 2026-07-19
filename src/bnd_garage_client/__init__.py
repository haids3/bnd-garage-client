"""Async client for B&D SmartDoorDevices garage hubs."""

from .client import HubClient
from .errors import (
    AuthenticationError,
    GarageError,
    HubCommandError,
    HubUnreachableError,
    PairingError,
)
from .models import (
    ActivityLogEntry,
    Credentials,
    DoorState,
    HubStatus,
    NotificationEntry,
    PresetAction,
    ToggleState,
)
from .pairing import pair_new_phone
from .sdk_client import SdkClient

__all__ = [
    "ActivityLogEntry",
    "AuthenticationError",
    "Credentials",
    "DoorState",
    "GarageError",
    "HubClient",
    "HubCommandError",
    "HubStatus",
    "HubUnreachableError",
    "NotificationEntry",
    "PairingError",
    "PresetAction",
    "SdkClient",
    "ToggleState",
    "pair_new_phone",
]
