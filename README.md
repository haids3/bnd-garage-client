# bnd-garage-client

Async Python client for B&D SmartDoorDevices garage hubs.

## Attribution

The B&D SmartDoorDevices LAN protocol used by this client — including its
endpoints, message formats, and the cryptographic scheme the hub itself
requires (AES/HMAC/RSA/ECDH, as documented below) — was reverse-engineered by
**THE-MAVER1CK** ([b-and-d-garage-api](https://github.com/THE-MAVER1CK/b-and-d-garage-api)),
via Android/iOS app decompilation and direct hub probing. None of that
research is this project's own work, and this client would not exist without
it. This repository is an independent implementation written from an
understanding of the documented protocol, not a copy or port of that
project's code.

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

## Protocol notes

- The hub exposes two independent LAN APIs: a session-based control API
  (open/close/stop/status, plus a light toggle and configurable position
  presets) on one port, and a separate signed-message API used only during
  one-time pairing on another.
- The hub uses a self-signed certificate and legacy TLS — connections to it
  require disabled verification and a lowered cipher security level. This is
  a hub firmware limitation, not a choice made by this client.
- The vendor's cloud registration endpoint (used only during one-time
  pairing) is signed by the vendor's own private root CA, and as of writing
  its intermediate/leaf certificates are expired. Verification is disabled
  for that call too, for the same reason: it's the vendor's infrastructure
  being broken/non-public, not a choice made by this client.
