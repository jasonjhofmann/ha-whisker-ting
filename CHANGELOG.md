# Changelog

## 3.0.1

### Fixed

- `bluetoothMacAddress` is byte-reversed by the Ting API exactly like the
  Wi-Fi MAC, but was passed through unnormalized — the device registry's
  Bluetooth connection showed the reversed form. Now normalized at the
  parse boundary like the Wi-Fi MAC. (Registry rows registered by earlier
  versions — the reversed Bluetooth MAC and the pre-1.2.0 reversed Wi-Fi
  MAC — persist until manually cleaned; new installs are correct.)

## 3.0.0 — The consolidation release

This release merges the fixes and features of every fork lineage of the
Whisker Ting integration into one codebase. See the Credits section of the
README for the full attribution map.

### Fixed

- **SignalR reconnect churn (root cause).** Outgoing hub messages were an
  unframed `{1: [...]}` MessagePack map; the server dropped every such
  connection (~70 ms after the first keepalive ping), producing an endless
  reconnect loop — with voltage sometimes still trickling through by
  byte-length coincidence, and never arriving at all for other accounts
  (simplytoast1/ha-whisker-ting#1). All outgoing messages are now
  length-prefixed flat arrays per the SignalR HubProtocol spec: six-field
  Invocations and framed `[6]` pings, with the handshake response
  validated.
- **Spurious voltage spikes (~200–750 V).** Voltage is now decoded from
  the payload's named fields (`Voltage`, `VoltageHi`, `VoltageLo`,
  `AveragePeaksMax`) — including payloads nested inside a binary blob —
  instead of scanning raw bytes for float64 markers, which could misread
  unrelated message bytes as readings.
- Options-flow saves now merge over existing options instead of replacing
  them, so internally persisted state survives a settings change.

### Added

- **`x-wl-api-key` upgrade-header authorization** on the WebSocket
  connection, matching the official app's traffic.
- **Station-id candidate probing.** When the streaming subscription stays
  silent on the device serial, the integration probes site id, SoC serial,
  and group id in the background and persists the first identifier that
  produces data.
- **Rejection-aware connection lifecycle.** A SignalR Completion for
  `InitializeStreaming` is treated as a subscription rejection (the server
  only sends one on failure) and tears the connection down; a server Close
  message does the same. Reconnect backoff resets only on received data,
  never gives up (capped at 5-minute intervals — the server-side
  streaming-authorization gate has been observed to clear on its own), and
  connections that never produce data recycle after a 60 s grace period.
  Silent stations re-arm the station-id probe on every poll, guarded by a
  30-minute cooldown after a fully failed rotation.
- **Single-notifier, identity-aware disconnect handling.** Every recycle
  path closes the socket and lets the receive loop deliver exactly one
  disconnect notification; the manager tears down the reporting instance
  (tasks cancelled, socket closed) and ignores late notifications from
  already-replaced connections — no duplicate connections, no leaked
  sockets or tasks, including across integration unload.
- Real-time pushes notify entity listeners without touching the poll
  scheduler, so a publish interval shorter than the scan interval can no
  longer starve the REST hazard/notification poll.
- **Configurable real-time publish interval** (options, 1–60 s, default
  5 s): how often the ~4 Hz voltage stream writes to Home Assistant state.
  In-memory readings and freshness tracking always run at full rate.
- Protocol golden-byte and regression tests, station-probe tests, and
  manager lifecycle tests (publish throttle, identity-aware disconnect
  handling, capped never-give-up backoff, ping/close/grace runtime
  paths); 94 tests total.

### Changed

- Voltage state writes moved from a fixed 1 Hz coordinator throttle to the
  manager-level publish interval above (default 5 s) to limit recorder
  growth.
- Integration metadata (codeowners, documentation, issue tracker) now
  points at `jasonjhofmann/ha-whisker-ting`; `msgpack` requirement relaxed
  from an exact pin to `>=1.0.0`.
- The power-outage blueprint lives at
  `blueprints/automation/whisker_ting/power_outage_notification.yaml`.

### Inherited from the merged forks

- Per-device alert feed from `/Notifications/history`: `Alerts` event
  entity, `Last brownout` / `Last weather alert` timestamp sensors, and a
  derived `Power outage` binary sensor; opt-in HA notifications for
  significant alerts plus an importable automation blueprint (billda).
- Multi-device support with site-based device naming (billda).
- Redacted diagnostics, reauth flow with wrong-account guard, tz-aware
  datetimes, entity base class, quality-scale manifest, pytest + HA test
  harness, ruff/coverage CI (billda).
- WebSocket decode/availability hardening, Cognito `ExpiresIn` token
  expiry, Wi-Fi/Bluetooth MAC device connections (with byte-order fix),
  power-quality hazard / connectivity / subscription-start entities
  (jasonjhofmann, previously released here as 1.1.0–1.2.0).

## 1.2.0 and earlier

See the git history: 1.2.0 surfaced dropped API fields (power-quality
hazard, connectivity, subscription start), 1.1.0 hardened the WebSocket
decode and availability semantics, 1.0.1 added brand assets, and 1.0.0
was the original release by Aiden Mitchell / simplytoast1.
