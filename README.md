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
- **Manual add** — add motors already paired by other remotes, or non-bidirectional motors, by entering a two-character hex enumerator: a user-chosen id that the stick uses to address that motor
- **USB auto-discovery** — HA detects the stick on plug-in (USB VID `16C0` / PID `05E1`, manufacturer `van ooijen`) and pre-fills the serial port
- **Stick status sensors** — connection status, firmware version, and operating mode
- **LED switch** — toggle the USB stick LED on/off from HA
- Local control only — no cloud dependency (`iot_class: local_push`)
- Up to 240 device slots per stick (enumerators `0x10`–`0xFF`; the integration allocates them automatically for auto-paired motors, starting at `0x10`)

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

Motors in this category never send a pairing response — the in-app **Pair automatically** flow waits for the motor to transmit its device ID over RF, so a silent motor that was paired only to a physical remote will time out every time.

To add a silent motor you first perform **wireless delegation pairing** outside the integration (teaching the motor to accept commands from the stick), then register it in HA via **Add manually**.

#### Remote-driven position tracking (best-effort)

When a timed motor has a bound physical remote registered in Home Assistant, the integration tracks position updates triggered by that remote. Because timed motors give no movement confirmation, this tracking is time-based and approximate — the position shown in HA reflects elapsed time since the button press, not a confirmed motor state. The position self-corrects the next time you open or close the motor through Home Assistant directly. If a remote press is missed (e.g. out of radio range), the position in HA will not update for that press.

#### What is the device enumerator?

The two-character hex enumerator (e.g. `1A`) is a user-chosen id that the stick assigns to a motor at pairing time. It is the stick's address for that motor — not an address the motor already has. When you add a motor via **Pair automatically**, the integration allocates the lowest free enumerator starting at `10` automatically. When you add a motor via **Add manually** (after delegation pairing below), you pick a unique value yourself and enter that same value in the HA form.

#### Wireless delegation pairing using a 5-channel remote (Funk-Handsender 5 Kanal, Model 20016)

This procedure teaches a motor that is already paired to a physical Schellenberg 5-channel remote to also accept commands from the USB stick. Perform these steps **before** adding the motor in Home Assistant, with HA (and the integration) either stopped or not yet owning the serial port — the raw commands below are sent directly to the port.

**Prerequisites:** The HA host must have write access to the stick's serial device (e.g. `/dev/ttyACM0` or `/dev/ttyUSB0`). Identify the correct path with `ls /dev/tty*` after plugging in the stick.

**Step 1 — Pick a unique enumerator.** Choose any two-character hex value in `02`–`FF` that is not already used by another motor in HA. Avoid `00` and `01`. Call it `XX` in the steps below (e.g. `1B`).

**Step 2 — Put the motor into programming mode using the 20016 remote.**

1. On the 20016 remote, select the channel that controls the target motor.
2. Press the remote's **P-button** (programming button) — the remote LED blinks to confirm programming mode is active.
3. Press **Stop** on the remote — the motor acknowledges with a brief jog or beep, indicating it is now in learn mode and ready to accept a new remote/stick ID.

**Step 3 — Within ~10 seconds, send the two delegation frames from the HA host.**

Replace `XX` with your chosen enumerator (e.g. `1B`) and `/dev/ttyACM0` with your actual port:

```bash
# Frame format: ss + {2-char enum} + 9 + {2-char command} + 0000
# CMD_PAIR = 60  (teach the motor to respond to enumerator XX)
echo 'ssXX9600000' > /dev/ttyACM0

# CMD_ALLOW_PAIRING = 40  (instruct the motor to accept the new pairing)
echo 'ssXX9400000' > /dev/ttyACM0
```

Concrete example for enumerator `1B`:

```bash
echo 'ss1B9600000' > /dev/ttyACM0
echo 'ss1B9400000' > /dev/ttyACM0
```

> **Frame format reference (from `api.py`):** `ss{enum:2}{repeat:1}{cmd:2}{pad:4}` where repeat is always `9` and pad is always `0000`. Total: 11 characters. Example for enum `1B`: `ss` + `1B` + `9` + `60` + `0000` = `ss1B9600000`.

**Step 4 — Confirm the motor learned the new id.** The motor acknowledges again (jog/beep). Exit programming mode on the 20016 remote by pressing the **P-button** again.

**Step 5 — Test with drive commands.** Verify the motor responds to the stick before adding it in HA:

```bash
# CMD_UP = 01
echo 'ssXX9010000' > /dev/ttyACM0
# CMD_DOWN = 02
echo 'ssXX9020000' > /dev/ttyACM0
# CMD_STOP = 00
echo 'ssXX9000000' > /dev/ttyACM0
```

**Step 6 — Add the motor in Home Assistant.** Go to **Settings → Devices & Services → Schellenberg USB → Add device → Add manually (already paired)**. Enter `XX` (the same hex value used above) as the device enumerator, select **timed (non-bidirectional)** motor type, and complete the form. The integration will now send all commands to that enumerator.

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
