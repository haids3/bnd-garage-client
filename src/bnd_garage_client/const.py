"""Protocol constants the hub itself requires.

Every value here is dictated by the hub's firmware (an endpoint path, a
header value it checks for, an algorithm it was implemented against) - none
of it is a choice this client made.
"""

from __future__ import annotations

CONTROL_PORT = 8989
"""Session-based control API: connect, open/close/stop, status, light, presets."""

CLOUD_REGISTER_URL = "https://version2.smartdoordevices.com"

CONTROL_HEADERS = {
    "Content-Type": "application/json",
    "version": "2.21.1",
    "app-version": "1.2.3",
}

SESSION_LIFETIME_SECONDS = 120
"""The hub expires a control-API session after roughly this long."""

CMD_OPEN = 2
CMD_STOP = 3
CMD_CLOSE = 4
CMD_LIGHT_TOGGLE = (16, 17)
"""Whichever of these two the hub currently lists is the action that flips
the light to the opposite state - see ToggleState."""
CMD_AUXILIARY_RELAY = (18, 19)
"""A second toggle slot, confirmed inert (accepted, no observable effect) on
every hub tested so far - excluded from presets but not yet a feature either."""

PARAM_LIGHT_TIME_SEC = 0
PARAM_AUTO_CLOSE_SEC = 1
PARAM_PE_AUTO_CLOSE_SEC = 2
PARAM_AUX_OUTPUT_TIME_SEC = 3
PARAM_TRIGGER_MODE = 16

ADVANCED_PARAMETER_FIELDS = {
    PARAM_LIGHT_TIME_SEC: "parameterLightTime",
    PARAM_AUTO_CLOSE_SEC: "parameterAutoCloseTime",
    PARAM_PE_AUTO_CLOSE_SEC: "parameterPEAutoCloseTime",
    PARAM_AUX_OUTPUT_TIME_SEC: "parameterAuxOutputTime",
    PARAM_TRIGGER_MODE: "triggerMode",
}
"""The control API's `app/res/devices/edit` wants a differently-named JSON
field per parameter, unlike the SDK protocol's generic {code, value} shape."""
