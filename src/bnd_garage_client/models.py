"""Data types shared across the client."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, kw_only=True)
class Credentials:
    """Long-lived credentials for a paired phone, used on every runtime call.

    The hub's SDK-side fields (`rsa_key_der_b64`/`sdk_phone_password`/
    `sdk_secret`) are optional and default to empty: entries paired before
    those fields existed only support the control API (open/close/stop/
    status), not the SDK protocol's wider RPC surface. Re-pairing populates
    them.
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
    rsa_key_der_b64: str = ""
    """Base64 PKCS8 DER-encoded RSA private key registered with the hub
    during pairing's key-upgrade step. Required to sign any SDK-protocol
    call (`sdk_client.py`) made after the pairing session ends - empty for
    credentials paired before SDK-protocol support existed."""
    sdk_phone_password: str = ""
    """The phone password the SDK protocol's `auth` RPC expects, set once
    during pairing's key-upgrade step. Distinct from `phone_password`, which
    is the control API's own credential."""
    sdk_secret: str = ""
    """AES key material for the SDK protocol - the ECDH-upgraded secret from
    pairing's key-upgrade step, distinct from `control_secret`. Required
    alongside `rsa_key_der_b64` to make any SDK-protocol call."""
    user_id: str = ""
    """This account's own user ID, returned by cloud registration during
    pairing. Some SDK-protocol RPCs (e.g. getNotificationHistory) require
    the *registering* user's ID specifically - the hub has other user
    entries too (e.g. a pseudo-user per physical wall button) that a naive
    "pick any user ID" approach can silently match instead."""


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
class NotificationEntry:
    """One entry from the hub's SDK-protocol notification history.

    Distinct from `ActivityLogEntry`: that one is the control API's single
    "most recent action" summary bundled into every status fetch, this is
    the SDK protocol's full history, one call, of everything the hub has
    ever notified this account about.
    """

    sent: bool
    """Whether the hub actually delivered this notification (vs. logging it
    without sending, e.g. because it was outside a configured time window)."""
    text: str
    """Already fully formed by the hub - no separate fields to decode."""
    time: int
    """Unix ms timestamp, per the hub's own clock."""


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
