"""Data types shared across the client."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, kw_only=True)
class Credentials:
    """Long-lived credentials for a paired phone, used on every runtime call.

    Everything needed only during the one-time pairing handshake (the RSA
    keypair, the temporary SDK-side password, the hub's RSA public key) is
    discarded once pairing completes - it has no further use afterwards.
    """

    hub_id: str
    phone_id: str
    phone_password: str
    control_secret: str
    """The secret issued during initial cloud registration.

    A newer secret is derived later in the pairing flow via an ECDH exchange,
    but the hub only ever accepts *this* original value for signing/
    encrypting runtime control calls - passing the ECDH-derived value instead
    makes the hub reject every call with a generic security error.
    """
    user_password: str
    device_id: str


class DoorState(StrEnum):
    """Coarse door state as reported by the hub."""

    OPEN = "open"
    CLOSED = "closed"
    MOVING = "moving"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class PresetAction:
    """A named, hub-configured partial-open position (e.g. "Pet", "Parcel").

    Both the label and the position it drives to are set by the user in the
    vendor app and can change at any time - treat this as a live snapshot,
    not something to cache by name.
    """

    command: int
    label: str


@dataclass(frozen=True, kw_only=True)
class ToggleState:
    """A binary hub feature (currently: the hub light) and its live state.

    The hub represents a toggle as a single slot that lists whichever
    command would flip it to the opposite state - there's no independent
    "on" and "off" entry, just one entry that alternates.
    """

    command: int
    is_on: bool


@dataclass(frozen=True, kw_only=True)
class ActivityLogEntry:
    """The hub's own record of the most recent action and who performed it.

    `text` already comes fully formed from the hub with attribution baked in
    (e.g. "Light off by HomeAssistant", "Closed by H S25" for a real phone) -
    there's no separate attribution field to decode.
    """

    text: str
    log_id: int
    """Unique per entry - compare against a previously seen value to detect
    a genuinely new entry, since repeated polls return the same one."""
    logged_at: int
    """Unix ms timestamp, per the hub's own clock."""
    alert: int
    """0 in every entry seen so far; presumably a nonzero fault/alert code
    in some other circumstance, unconfirmed."""


@dataclass(frozen=True, kw_only=True)
class HubStatus:
    """A snapshot of everything the hub reports for the door in one call."""

    state: DoorState
    position: int
    """0 = fully closed, 100 = fully open."""
    rate: float
    """Positive while opening, negative while closing, 0 when stationary."""
    name: str = ""
    presets: tuple[PresetAction, ...] = ()
    light: ToggleState | None = None
    """None if the hub doesn't advertise a light control at all."""
    activity: ActivityLogEntry | None = None
    """None if the hub doesn't report a log entry at all."""


def status_from_raw(
    *,
    position: int,
    rate: float,
    name: str = "",
    presets: tuple[PresetAction, ...] = (),
    light: ToggleState | None = None,
    activity: ActivityLogEntry | None = None,
) -> HubStatus:
    """Classify a coarse DoorState from the hub's raw position/rate pair."""
    if rate != 0:
        state = DoorState.MOVING
    elif position < 0:
        state = DoorState.UNKNOWN
    elif position == 0:
        state = DoorState.CLOSED
    elif position == 100:
        state = DoorState.OPEN
    else:
        state = DoorState.PARTIAL
    return HubStatus(
        state=state,
        position=position,
        rate=rate,
        name=name,
        presets=presets,
        light=light,
        activity=activity,
    )
