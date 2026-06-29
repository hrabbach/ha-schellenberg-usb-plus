<!-- generated-by: gsd-doc-writer -->
# Schellenberg USB+

A Home Assistant custom integration that controls Schellenberg roller-shutter motors through a Schellenberg USB Funk-Stick. Exposes each paired motor as a standard HA cover entity with time-based position tracking, plus USB stick status sensors and an LED switch.

[![HACS Custom Repository](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hrabbach/ha-schellenberg-usb-plus)
[![GitHub Release](https://img.shields.io/github/v/release/hrabbach/ha-schellenberg-usb-plus)](https://github.com/hrabbach/ha-schellenberg-usb-plus/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Based on the original work by [Scott Downing (GimpArm)](https://github.com/GimpArm/schellenberg_usb),
> with calibration persistence introduced by
> [ohlmannmichael-ai](https://github.com/ohlmannmichael-ai/schellenberg_usb).

> **Warning:** This integration is not affiliated with Schellenberg. The developers take no responsibility for anything that happens to your devices as a result of using this software.

---

## What It Does

- Controls Schellenberg roller-shutter motors over RF via the Schellenberg USB Funk-Stick
- Supports **bidirectional motors** (ROLLODRIVE PREMIUM and similar) that send movement events back to the stick — position tracking is event-driven
- Supports **timed (non-bidirectional) motors** that never confirm movement — position tracking is purely time-based using pre-measured travel times
- **Time-based position tracking** — open/close times measured via a built-in calibration flow; drive-to-percentage works on any calibrated motor
- **Auto-pairing** — put the stick into pairing mode from the HA UI; press the pairing button on the motor within 2 minutes
- **Manual add** — add motors already paired by other remotes, or non-bidirectional motors, by entering their two-character hex enumerator slot
- **USB auto-discovery** — HA detects the stick on plug-in (USB VID `16C0` / PID `05E1`, manufacturer `van ooijen`) and pre-fills the serial port
- **Stick status sensors** — connection status, firmware version, and operating mode
- **LED switch** — toggle the USB stick LED on/off from HA
- Local control only — no cloud dependency (`iot_class: local_push`)
- Up to 240 device slots per stick (enumerators `0x10`–`0xFF`)

---

## Requirements

- **Home Assistant** 2025.1.0 or later
- **Schellenberg USB Funk-Stick** (USB VID `16C0` / PID `05E1`, manufacturer `van ooijen`) connected to the HA host
- Serial port access — the HA host user must be able to open the serial device (typically `/dev/ttyUSB0` or `/dev/ttyACM0` on Linux)
- **HACS** (for the recommended install path)

---

## Installation

### Via HACS (recommended)

This integration is a custom HACS repository. Add it once, then install as usual:

1. Open HACS in your Home Assistant sidebar.
2. Click the **three-dot menu** (top right) and choose **Custom repositories**.
3. Enter `hrabbach/ha-schellenberg-usb-plus` as the repository and select **Integration** as the category. Click **Add**.
4. Search for **Schellenberg USB+** in the HACS list, select it, and click **Download**.
5. Restart Home Assistant when prompted.

Or use the one-click badge:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hrabbach&repository=ha-schellenberg-usb-plus&category=integration)

### Manual install

Copy the `custom_components/schellenberg_usb/` folder from this repository into your HA `config/custom_components/` directory, then restart Home Assistant.

---

## Quick Setup

1. Go to **Settings → Devices & Services → Add Integration** and search for **Schellenberg USB+**.
2. If the stick is already plugged in, HA may auto-discover it and pre-fill the serial port — confirm it. Otherwise, enter the port path (e.g. `/dev/ttyUSB0`).
3. Go to the newly created **Schellenberg USB+** hub and click **+ Add device**.
4. Choose **Pair automatically** or **Add manually** (for already-paired or timed motors).
5. Calibrate the motor from its device page to enable position tracking.

Full step-by-step instructions, including calibration and troubleshooting, are in [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md).

---

## Device Pairing Instructions

When you choose **Pair automatically** in the HA UI, the integration signals the USB stick to accept the next RF registration frame. You must also press the pairing button on the motor itself within 2 minutes.

The exact button combination varies by motor model:

### ROLLODRIVE 65 PREMIUM / 75 PREMIUM (electric belt winders)
**Art.Nr.: 22567, 22576, 22578, 22726, 22727, 22728, 22767**

1. Press and hold the **Sun (☀)** button and the **Up (▲)** button simultaneously.
2. Hold for **5 seconds** until the LED flashes.
3. The motor is now in pairing mode — it accepts the next registration frame from the stick.

### ROLLOPOWER PLUS / STANDARD (tube motors)
**Art.Nr.: 20106, 20110, 20406, 20410, 20610, 20615, 20620, 20640, 20710, 20720, 20740**

Pairing is performed through the motor's connected Schellenberg remote or wall switch. Consult the remote's printed quick-start card for the pairing button sequence.

### Funk-Rollladenmotoren PREMIUM (radio tube motors)
**Art.Nr.: 21106, 21110, 21210, 21220, 21240**

Pairing is driven by the connected control device (remote or timer switch). Refer to that device's manual for the exact sequence.

### Non-bidirectional (timed) motors

Motors in this category never send a pairing response. Use **Add manually** instead of **Pair automatically** and enter the motor's two-character hex enumerator slot directly.

### General tips

- Keep the USB Funk-Stick within range (approximately 20 m indoors).
- Avoid metal obstructions between the stick and the motor.
- If pairing times out, move the stick closer and try again.
- If the model above does not match your motor, consult the printed instruction sheet included with the motor or the Schellenberg support site.

> **Note:** The integration is model-agnostic — it only needs to receive the motor's RF registration frame. Any model that emits a compatible Schellenberg pairing frame will work.

---

## Motor Types: Bidirectional vs Timed

| Type | How it works | Position tracking |
|------|-------------|-------------------|
| **Bidirectional** | Motor reports `started moving` and `stopped` events back to the stick | Event-driven — the integration starts and stops the timer automatically |
| **Timed (non-bidirectional)** | Motor gives no movement feedback | Time-based — the integration drives the motor and measures elapsed time between your button presses during calibration |

You select the motor type once when adding the device. Existing motors added before this distinction was introduced are treated as bidirectional.

---

## Entities Created

For each USB stick (hub):

| Entity type | Name | Description |
|-------------|------|-------------|
| Sensor | Connection status | Whether the serial connection to the stick is active |
| Sensor | Firmware version | Stick firmware version string |
| Sensor | Operating mode | Current stick operating mode |
| Switch | LED | Toggles the USB stick LED on or off |

For each paired motor (subentry):

| Entity type | Description |
|-------------|-------------|
| Cover | Open / close / stop / set position (position tracking requires calibration) |

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) | Full install, pairing, calibration walkthrough, and troubleshooting |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | All configuration fields, calibration constants, protocol details |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, component map, data flow |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local dev setup, build commands, code style |
| [docs/TESTING.md](docs/TESTING.md) | Running tests, coverage, CI integration |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Quality gate commands and contribution guidelines |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and the quality gate (tests, lint, type-check, and spell-check) every change must pass.

---

## License

MIT — see [LICENSE](LICENSE) for details.

Issues and feature requests: [github.com/hrabbach/ha-schellenberg-usb-plus/issues](https://github.com/hrabbach/ha-schellenberg-usb-plus/issues)
