# B&D SmartDoorDevices RPC catalog

Every action the hub exposes across both of its protocols (see the
[README](README.md#legacy-vs-sdk-protocol) for the split between them),
with the exact wire shape for each. Derived from decompiling the vendor
Android app directly (Java reconstruction via jadx, one specific finding
confirmed at the raw Dalvik bytecode level via baksmali) and from testing
against real hardware - independent research, not sourced from
THE-MAVER1CK's `b-and-d-garage-api`, which documents only the legacy
control API's core door commands.

"Implemented here" means wrapped in a typed `HubClient`/`SdkClient` method.
Everything else is a known, real endpoint this client doesn't currently
call - reachable manually (`HubClient._call()` for the legacy API,
`SdkClient.call(path, data)` for the SDK protocol) if you need it before a
typed wrapper exists.

## Legacy control API (port 8989)

Session-based: `app/connect` establishes a `sessionId`/`sessionSecret` pair
(handled internally by `HubClient`), then every other call is an
AES-128-CBC-encrypted, HMAC-SHA256-signed POST to one of the endpoints
below. Confirmed via decompiling `LegacyNetworkHubConnection` end to end
that only the ~16 actions below have real implementations - the rest of
that class's ~90 methods stub straight to `ErrorCode 34` ("unsupported"):
users, cameras, integrations, remote controls, and notifications are
genuinely unreachable over this protocol at any hub/firmware version.

| Action | Endpoint | Request body | Implemented here |
|---|---|---|---|
| Get one device | `app/res/devices/fetch` | `{deviceId}` | `get_status()` |
| Get all devices | `app/res/devices/fetch` | `{}` | no |
| Send device command | `app/res/action` | `{deviceId, action: {cmd: N}}`, or `{action: {base: N-256}}` if `N >= 256` | `send_command()` and its wrappers (below) |
| Get hub info | `app/res/base/info` | `{}` | `get_hub_info()` |
| Set hub name | `app/res/base/edit` | `{name}` | no |
| Set hub timezone | `app/res/base/time` | `{timezone}` | no |
| Rename device | `app/res/devices/edit` | `{deviceId, name}` | no |
| Set advanced parameter | `app/res/devices/edit` | `{deviceId, <field>: value}` - field name keyed by parameter code, see below | `set_advanced_parameter()` |
| Set part-open alias | `app/res/devices/edit` | `{deviceId, partOpenIcon1/2/3: mode}` | no - **buggy on this protocol**, see below |
| Set part-open position | `app/res/devices/edit` | `{deviceId, partOpenSet1/2/3: true}` (captures the door's *current* position as that preset) | no |
| Get device logs | `app/res/log` | `{deviceId}` | `get_device_logs()` (client-side filtered: drops `logType` 0 and 21, matching the vendor app's own view) |
| WiFi diagnostics | `app/res/network/diagnostics` | `{}` | `get_wifi_diagnostics()` |
| Register device | `app/res/devices/register` | `{snHex, pwHex}` (a 16-char registration code split into two 8-char halves) | no |
| Deregister device (cancellable) | `app/res/devices/deregister` | `{deviceId}`; cancel via `app/res/process` `{cancel: true}` | no |
| Force-deregister device | `app/res/devices/delete` | `{deviceId}` | no |
| Refresh device | `app/res/devices/edit` | `{deviceId, refresh: true}` | no |

**Known vendor-app bug, confirmed at the bytecode level (not just suspected
from decompiled Java)**: the legacy protocol's `setDevicePartOpenAlias`
call site passes its `partOpenCommand` argument for both the second *and*
third parameters of the underlying request constructor - `partOpenMode` is
overwritten and discarded before it's ever read, at the raw Dalvik
instruction level. The wire contract itself (`{deviceId, partOpenIcon1/2/3:
mode}`) is fine; only the vendor app's own call site is broken. If this
action gets implemented, build the request body directly rather than
porting that call pattern. The SDK-protocol equivalent (`setDevicePartOpenAlias`,
below) doesn't have this bug.

### Device command codes (`sendDeviceCommand` / `send_command()`)

| Code | Meaning |
|---|---|
| 2 | Open |
| 3 | Stop |
| 4 | Close |
| 5 / 6 / 7 | Part-open preset 1 / 2 / 3 |
| 16 / 17 | Light on / off |
| 18 / 19 | Auxiliary relay on / off |
| 20 / 21 | Remote-control lockout on / off |
| 32-50 | Open to an exact percentage: `(code - 31) * 5`, i.e. 32→5% ... 50→95% |
| 258 / 257 | Phone lockout on / off (`>= 256`, sent as `{"base": code - 256}` per the encoding above) |

The light, auxiliary-relay, and both lockout toggles are all the same
underlying mechanism: the hub's aux/feature list reports whichever of a
pair is the *next* valid action, and sending it flips the toggle. Live
testing found both lockouts live there too, not in a separate status
field as first assumed when this client was built - see
`bnd_garage_client.client._split_features()`.

### Advanced parameter codes (`setDeviceAdvancedParameter` / `set_advanced_parameter()`)

| Code | Meaning | Wire field name |
|---|---|---|
| 0 | Light time (seconds) | `parameterLightTime` |
| 1 | Auto-close time (seconds) | `parameterAutoCloseTime` |
| 2 | Photo-eye auto-close time (seconds) | `parameterPEAutoCloseTime` |
| 3 | Auxiliary output time (seconds) | `parameterAuxOutputTime` |
| 16 | Trigger mode (boolean-range, exact meaning of each value unconfirmed) | `triggerMode` |

The auxiliary relay showing no observable effect on early hubs tested
turned out to be this parameter (code 3) set to 0 seconds on those hubs,
not the relay being unwired - confirmed once set to a nonzero duration.

## SDK protocol (port 8991)

A generic RPC dispatcher: every call is `{"path": "<name>", "data": {...}}`,
AES-256-CBC-encrypted and RSA-SHA512/HMAC-signed, POSTed to `sdk/message`.
`auth` (below) returns a session key used to HMAC-sign subsequent calls in
the same session; the RSA key persists across sessions once paired.
Replies have a zero-IV quirk that corrupts exactly their first AES block -
since `data` is always the wrapper's first key, every reply's actual
payload used to be silently discarded (only `errorCode`/`state` survived
via regex salvage) until `crypto.repair_sdk_reply()` fixed it by
reconstructing the wrapper as whichever of object/array/string-valued
`data` actually parses.

### Session/auth

| Action | Path | Request | Response |
|---|---|---|---|
| Authenticate | `auth` | `{userPassword, phonePassword, temporary}` | `{key, expiresIn}` - `key` becomes the HMAC signing key for the rest of the session |
| Set user password | `setUserPassword` | `{userId?, oldPassword, newPassword}` | used during pairing to clear the hub's forced first-login password-change flag |

### Door / device commands

| Action | Path | Notes |
|---|---|---|
| Send device command | `sendDeviceCommand` | `{deviceId, deviceCommand, accountId?}` - same command codes as the legacy protocol's table above |
| Get one device | `getDevice` | `{deviceId}` |
| Get all devices | `getDevices` | `{}` → `List<Device>` |
| Get device logs | `getDeviceLogs` | `{deviceId}` → full log history (richer than the legacy protocol's) |
| Set device name | `setDeviceName` | `{deviceId, deviceName}` |
| Set paired cameras | `setDevicePairedCameras` | `{deviceId, cameraDeviceIds}` |
| Set part-open alias | `setDevicePartOpenAlias` | `{deviceId, partOpenCommand, partOpenMode}` - **not buggy here**, unlike the legacy-protocol equivalent |
| Set part-open position | `setDevicePartOpenPosition` | `{deviceId, partOpenCommand}` |
| Set device order / style | `setDeviceOrder` / `setDeviceStyle` | per-user device list ordering and display style |
| Register / refresh / deregister / force-deregister device | `startDeviceRegister` / `startDeviceRefresh` / `startDeviceDeregister` / `startDeviceForceDeregister` | the latter two emit progress events (`HUB_CONNECTION_DEVICE_DEREGISTER_PROGRESS` / `_REFRESH_PROGRESS`) before their final reply - don't wrap with a naive single-request/response helper |

### Device advanced settings

| Action | Path | Notes |
|---|---|---|
| Set advanced parameter | `setDeviceAdvancedParameter` | `{deviceId, parameter: {code, value}}` - generic `{code, value}` shape, unlike the legacy protocol's per-parameter field names |
| Set advanced access schedule | `setDeviceAdvancedAccess` | `{deviceId, advancedAccess: {enabled, dailyStartTime, dailyEndTime, daysOfWeek}}` |
| Cycle test | `getDeviceCycleTest` / `resetDeviceCycleTest` / `setDeviceCycleTestActive` / `setDeviceCycleTestProgressNotification` / `setDeviceCycleTestWaitTime` | cycle count, fault count, obstruction count, progress notification schedule |

### Notifications

| Action | Path |
|---|---|
| Get history | `getNotificationHistory` - **implemented**, `get_notification_history()`; request `{userId, phoneId}` → `List<{sent, text, time}>` |
| Get supported types | `getNotificationsSupported` |
| Get / create / set / delete preference | `getNotificationPreferences` / `getNotificationPreference` / `createNotificationPreference` / `setNotificationPreference` / `deleteNotificationPreference` / `deleteNotificationPreferencesAll` |
| Send test notification | `startNotificationTest` |

### Users / phones

Full CRUD, none implemented here: `getUsers` / `getUser` / `createUser` /
`createAdminUser` / `deleteUser` / `setUserAdmin` / `setUserName` /
`setUserEmail` / `setUserEnabled` / `setUserDevicePermissions` /
`setUserTimePermissions` / `setUserPassword` / `resetUserPassword` /
password-recovery actions / `getUserPhones` / `getUserPhone` /
`createUserPhone` / `deleteUserPhone` / `setUserPhoneName` /
`setUserPhoneEnabled` / `setUserPhonePushConfiguration` / phone-invite
actions.

Note: the hub models some things as pseudo-users that aren't people - a
physical wall button showed up as its own `User` entry (literally named
"Wall Button") sharing `devicePermissions` with the real door device.
Matching against the wrong "user" here is a real footgun, not a
hypothetical one - `getNotificationHistory` needs the actual registering
account's `userId` (persisted on `Credentials`), not just any user ID
found in `getUsers`.

### Remote controls

| Action | Path |
|---|---|
| Get all / one | `getRemoteControls` / `getRemoteControl` |
| Delete | `deleteRemoteControl` |
| Reassign to a user | `setRemoteControlUser` |
| Get codeset options / pair a physical remote | `getRemoteControlCodesetOptions` / `startRemoteControlCodeset` |

`RemoteControl.Command` is a *different* code set from the device command
codes above (its own numbering: `OPEN=5, CLOSE=4, STOP=6, PART_OPEN_1=2,
LIGHT_TOGGLE=7, VACATION_TOGGLE=8, AUXILIARY_TOGGLE=9,
BASESTATION_COMMAND=48, OSC=1, SWIPE=3, NONE=49`) - don't conflate the two.

### Cameras

None implemented here: `startCameraRegister`, `startCameraNetworkDiscovery`,
`getCameraSetupWifiInfo`, `startCameraReboot`, `startCameraFormatSDCard`,
`setCameraChimeType`, `setCameraMotionDetectionSettings`,
`setCameraAudioDetectionSettings`, `setCameraSDRecordingSettings`.

### Integrations

None implemented here. Google Home and Amazon Alexa each have
create/rename/set-pin/delete account actions
(`createIntegration{GoogleHome,AmazonAlexa}Account`, etc.); Siri Shortcuts
has `setIntegrationSiriShortcutsAccountEnabled`; the LAN API has
`setIntegrationLanApiEnabled` / `setIntegrationLanApiHttpAllowed` /
`deleteIntegrationLanApiAccount` and a self-service account model (the app
can only list/delete `LanApiAccount` entries, never create one - a
third-party client is expected to create its own by hitting the hub
directly once enabled, the same press-to-pair pattern as Philips Hue).
**This LAN API has no UI exposure in the vendor app on the hub tested** -
real at the protocol level, but with no confirmed way to enable it short
of calling `setIntegrationLanApiEnabled` directly via `SdkClient`.

### Hub settings

None implemented here: `getHubInfo` (a different, newer shape than the
legacy protocol's `getHubInfo`), `setHubInfoName`, `setHubInfoTimeZone`,
`getHubUpdateAvailable`, `startHubUpdate`, `startHubFactoryReset`,
`setHubTestBehaviour` (debug/test hook), `getHubWifiDiagnostics`.
