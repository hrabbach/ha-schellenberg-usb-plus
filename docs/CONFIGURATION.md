<!-- generated-by: gsd-doc-writer -->
# Configuration

All configuration for the Schellenberg USB integration is done through the Home Assistant UI. There are no YAML configuration files for this integration — setup, options, and per-device calibration are all managed through config flows and subentry flows.

## Hub Setup

The hub (primary config entry) is created once via **Settings → Devices & Services → Add Integration → Schellenberg USB**.

| Field | Key | Type | Required | Default | Description |
|-------|-----|------|----------|---------|-------------|
| Serial port | `serial_port` | string | Yes | `/dev/ttyUSB0` | OS device path for the USB stick (e.g. `/dev/ttyUSB0`, `/dev/ttyACM0`) |

**USB auto-discovery:** If the stick is plugged in before the integration is added, Home Assistant discovers it automatically using the USB descriptor declared in `manifest.json`:

| Attribute | Value |
|-----------|-------|
| Vendor ID (VID) | `16C0` |
| Product ID (PID) | `05E1` |
| Manufacturer string | `van ooijen` |

During auto-discovery the detected port is pre-filled; the user can correct it before confirming.

**Baud rate:** Fixed at `112500` bps in `api.py`. This value is set by the Schellenberg protocol and is not configurable.

---

## Hub Options

After the hub is created, open **Settings → Devices & Services → Schellenberg USB → Configure** to access hub options.

| Field | Key | Type | Default | Description |
|-------|-----|------|---------|-------------|
| Serial port | `serial_port` | string | Current value | Change the OS device path. Triggers an integration reload when changed. |
| Ignore unknown signals | `ignore_unknown` | boolean | `false` | When `true`, frames from unregistered devices are logged at DEBUG instead of WARNING. Applied live without reloading the integration when the port is unchanged. |

Stored in: `config_entry.options` (Home Assistant config entry options store).

---

## Per-Device (Subentry) Configuration

Each paired blind motor is a **subentry** under the hub. Subentries are added via **Settings → Devices & Services → Schellenberg USB → Add device**.

### Add Methods

| Method | Description |
|--------|-------------|
| **Pair automatically** | Put the stick into pairing mode and wait for a motor to respond over RF. The motor's device ID and enumerator are captured automatically. |
| **Add manually** | Specify a user-chosen hex device enumerator (a 2-char id the stick uses to address this motor) for motors that were delegation-paired outside the integration or whose device ID is already known. |

### Manual-Add Fields

| Field | Key | Type | Required | Default | Description |
|-------|-----|------|----------|---------|-------------|
| Device enumerator | `device_enum` | 2-char hex string | Yes | — | A user-chosen 2-char hex id (e.g. `10`, `1B`) that the stick uses to address this motor. Assigned at pairing time — not an address the motor inherently has. For delegation-paired motors, use the same value sent in the delegation frames. Recommended range: `02`–`FF`; avoid `00` and `01` for manually delegation-paired motors. Must be unique across all blind subentries. Stored as uppercase. |
| Bidirectional | `bidirectional` | boolean | Yes | `true` | `true` = motor reports movement events back (event-based position tracking). `false` = timed/non-bidirectional motor (position computed from calibration times only). |
| Friendly name | `device_name` | string | No | `Blind {device_enum}` | Display name shown in HA. Falls back to `Blind {enum}` if left blank. |

For **timed (non-bidirectional) motors only**, a second step collects:

| Field | Key | Type | Required | Default | Description |
|-------|-----|------|----------|---------|-------------|
| Initial position | `initial_position` | integer 0–100 | No | `100` | Starting position percentage (0 = fully closed, 100 = fully open). Used to seed position tracking before first calibration. Clamped to 0–100. |

### Subentry Data Keys

All per-device values are persisted in `subentry.data`:

| Key | Constant | Description |
|-----|----------|-------------|
| `device_id` | `CONF_DEVICE_ID` | Device identifier string (equals `device_enum` for manually-added devices) |
| `device_enum` | — | 2-char uppercase hex enumerator |
| `bidirectional` | `CONF_BIDIRECTIONAL` | Motor mode flag |
| `initial_position` | `CONF_INITIAL_POSITION` | Seed position (timed motors only) |

---

## Calibration

Calibration measures the time a motor takes to travel from fully open to fully closed (and back), enabling accurate position tracking. It is accessed via **Settings → Devices & Services → Schellenberg USB → {device} → Configure**.

The integration routes to one of two calibration flows based on the motor's `bidirectional` flag:

### Bidirectional Calibration (`options_flow_calibration.py`)

Used for motors that report movement events back to the stick.

- The flow listens for `EVENT_STARTED_MOVING_UP` / `EVENT_STARTED_MOVING_DOWN` and `EVENT_STOPPED` dispatcher signals.
- Timing is measured with `time.monotonic()`.
- Flow timeout: `CALIBRATION_TIMEOUT = 300` seconds (5 minutes) per movement phase.
- Calibration data is saved to the HA Store and a `SIGNAL_CALIBRATION_COMPLETED` signal is emitted with `final_position=0` (flow ends on a close run).

### Timed Calibration (`options_flow_timed_calibration.py`)

Used for non-bidirectional motors that never report movement events.

- The flow drives the motor with `CMD_DOWN` / `CMD_UP` commands and timestamps button presses using `time.monotonic()`.
- No stop command is sent — the motor runs to its physical endstop.
- After the close + open runs, a confirm screen shows the measured times. The user can redo the measurement before saving.
- Calibration signal is emitted with `final_position=100` (flow ends with the shutter fully open).

#### Timed Calibration Guard Bounds

Both guard thresholds are defined in `const.py` and applied to each run (close and open) independently:

| Constant | Value | Effect |
|----------|-------|--------|
| `CAL_MIN_TRAVEL_TIME` | `2` seconds | Rejects runs shorter than 2 s as double-press / misfire (`timed_cal_too_short` error). |
| `CAL_MAX_TRAVEL_TIME` | `120` seconds | Rejects runs longer than 120 s as "walked away" runs (`timed_cal_too_long` error). |

A rejected run resets the elapsed timer and redisplays the same form step — no stop command is sent and no partial data is saved.

### Calibration Persistence

Calibration times (`open_time`, `close_time`) are stored in the Home Assistant `.storage/` directory:

| Detail | Value |
|--------|-------|
| Storage key | `schellenberg_usb_calibration` |
| Storage file | `<HA config dir>/.storage/schellenberg_usb_calibration` |
| Store version | `1` |
| Format | JSON — keyed by `config_entry_id` → `device_id` → `{open_time, close_time}` |

The store is loaded once per HA start and cached in `hass.data`. A corrupt or missing file causes the integration to start with an empty cache (logged at EXCEPTION level); calibration can be re-run at any time to restore values.

`DEFAULT_TRAVEL_TIME = 60.0` seconds is used as the position-tracking fallback when no calibration data has been stored for a device.

---

## Protocol Constants

These values are fixed in the source and are not user-configurable:

| Constant | Value | Description |
|----------|-------|-------------|
| Baud rate (hardcoded literal in `api.py`) | `112500` bps | Serial baud rate; not a named constant |
| `VERIFY_TIMEOUT` | `5` seconds | Timeout waiting for stick version/mode response |
| `PAIRING_TIMEOUT` | `120` seconds | Timeout waiting for a pairing RF response |
| `PAIRING_DEVICE_ENUM_START` | `0x10` | First enumerator assigned during auto-pairing |
| `DEFAULT_TRAVEL_TIME` | `60.0` seconds | Position fallback when calibration is absent |

---

## Integration Metadata

Source: `manifest.json`

| Field | Value |
|-------|-------|
| Domain | `schellenberg_usb` |
| Version | `1.1.0` |
| Integration type | `hub` |
| IoT class | `local_push` |
| Requirement | `pyserial-asyncio-fast==0.16` |
| Config flow | Yes |
