# bnd-garage-client

Async Python client for B&D SmartDoorDevices garage hubs.

## Attribution

This client's origins split along the same line as the two protocols the hub
exposes (see [Legacy vs SDK protocol](#legacy-vs-sdk-protocol) below):

**THE-MAVER1CK's original research** ([b-and-d-garage-api](https://github.com/THE-MAVER1CK/b-and-d-garage-api),
via Android/iOS app decompilation and direct hub probing) is the foundation
this client would not exist without: the hub's cryptographic scheme
(AES/HMAC/RSA/ECDH, as documented below), the legacy control API's core
endpoints and command set (connect, open/close/stop/status, the pairing
handshake), and establishing that this protocol was reachable and
reverse-engineerable at all. This repository is an independent
implementation written from an understanding of that documented protocol,
not a copy or port of their code.

**This project's own research since** has substantially expanded on that
foundation through its own APK decompilation, live-hardware testing, and (for
one specific finding) independent smali/bytecode disassembly - none of it
sourced from THE-MAVER1CK's work:

- The entire SDK-protocol RPC catalog (~80 actions spanning notifications,
  multi-user management, cameras, remote controls, integrations, and hub
  settings) - undocumented in THE-MAVER1CK's repo, which covers only the
  legacy control API.
- The legacy control API's own further actions beyond open/close/stop: hub
  info, device activity logs, WiFi diagnostics, exact-percentage
  positioning, and both lockout toggles.
- Corrections to what static analysis alone suggested, found only by
  testing against real hardware: the auxiliary relay is a real, usable
  toggle rather than permanently inert as it first appeared; both lockouts
  are exposed via the same aux-list toggle-slot mechanism as the light, not
  a separate status field as first assumed; and a genuine vendor-app bug in
  the legacy protocol's `setDevicePartOpenAlias` action, confirmed at the
  raw bytecode level rather than merely suspected from decompiled Java.

This is a derivative work in its foundations - full credit to THE-MAVER1CK
for making it possible at all - but not in everything built on top of them
since.

## Status

Early development. Not yet published to PyPI.

```bash
pip install -e /path/to/bnd_garage_client
```

## Usage

Before pairing, add a new user for this client in the B&D Smart Garage Access
app: **Settings → Users → your hub → Add new user**. The app will show you an
activation code and a password — note both down. The password is assigned
automatically by the app, not something you choose yourself.

```python
import aiohttp
from bnd_garage_client import Credentials, HubClient, pair_new_phone

HUB_IP = "<HUB IP>"  # LAN IP address of your B&D hub (Basestation)
ACTIVATION_CODE = "<ACTIVATION CODE>"  # shown when adding a new user in the app
USER_PASSWORD = "<USER PASSWORD>"  # also shown when adding a new user in the app

# One-time pairing
async with aiohttp.ClientSession() as session:
    credentials = await pair_new_phone(session, HUB_IP, ACTIVATION_CODE, USER_PASSWORD)

# Runtime control
async with HubClient(HUB_IP, credentials) as client:
    await client.connect()
    status = await client.get_status()
    await client.open_door()
```

## Legacy vs SDK protocol

The hub actually speaks two distinct protocols, both reachable on the LAN.
Nothing here is a choice this client made - it's dictated by the hub itself.

| | Legacy control API (`HubClient`, port 8989) | SDK protocol (`SdkClient`, port 8991) |
|---|---|---|
| Crypto | AES-128-CBC, MD5-derived key material, HMAC-SHA256 session signing | AES-256-CBC, SHA-256-derived key material, RSA-SHA512 request signing + an HMAC session key from `auth` |
| Credential fields needed | `phone_password` + `control_secret` | `rsa_key_der_b64` + `sdk_secret` + `sdk_phone_password` (all on `Credentials`, populated by `pair_new_phone`) |
| Door control | open/close/stop, exact-percentage positioning (5% steps), status, light, position presets, both lockout toggles | Same commands reachable via the generic `sendDeviceCommand` RPC, not currently wrapped in `SdkClient` |
| Also covers | Hub info (its own older, string-keyed shape), device activity logs, WiFi diagnostics, advanced parameters (auto-close/light timers) | Notifications, multi-user management, remote controls, cameras, integrations (Google Home/Alexa/Siri/LAN API), cycle-test stats, hub settings |
| Implemented here | `client.py` (`HubClient`) - the primary, hardware-validated path for everything door-related | `sdk_client.py` (`SdkClient`) - additive, for what the legacy API categorically can't reach (e.g. `get_notification_history()`) |

Use `HubClient` for anything to do with the door itself: it's proven against
real hardware and doesn't need the RSA private key `SdkClient` requires.
Reach for `SdkClient` only for the surface the legacy API doesn't expose at
all - it needs more sensitive credential material for a capability trade,
not a security upgrade (both protocols hit the same hub over the same
self-signed, legacy-TLS connection either way - see below).

See [RPC_CATALOG.md](RPC_CATALOG.md) for every action both protocols
expose, with exact request/response shapes and which ones this client
currently wraps.

## Protocol notes

- The hub uses a self-signed certificate and legacy TLS — connections to it
  require disabled verification and a lowered cipher security level. This is
  a hub firmware limitation, not a choice made by this client.
- The vendor's cloud registration endpoint (used only during one-time
  pairing) is signed by the vendor's own private root CA, and as of writing
  its intermediate/leaf certificates are expired. Verification is disabled
  for that call too, for the same reason: it's the vendor's infrastructure
  being broken/non-public, not a choice made by this client.
