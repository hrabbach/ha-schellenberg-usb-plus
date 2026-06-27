# schellenberg_usb Home Assistant Component

[![GitHub Release](https://img.shields.io/github/release/hrabbach/schellenberg_usb.svg)](https://github.com/hrabbach/schellenberg_usb/releases)
[![License](https://img.shields.io/github/license/hrabbach/schellenberg_usb.svg)](https://github.com/hrabbach/schellenberg_usb/blob/main/LICENSE)
![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/hrabbach/schellenberg_usb/build-test.yaml)

> Maintained fork of [GimpArm/schellenberg_usb](https://github.com/GimpArm/schellenberg_usb), building on
> [ohlmannmichael-ai/schellenberg_usb](https://github.com/ohlmannmichael-ai/schellenberg_usb), which introduced
> calibration persistence. Thanks to both for their work.

Home Assistant component that interfaces with the [Schellenberg Usb Funk-Stick](https://www.schellenberg.de/smart-home-produkte/smart-home-steuerzentralen/funk-stick/21009/).

> [!WARNING] 
> This integration is not affiliated with Schellenberg, the developers take no responsibility for anything that happens to
> your devices because of this library.

![Schellenberg](https://raw.githubusercontent.com/hrabbach/schellenberg_usb/main/images/schellenberg-logo.png)

## Features

* Supports blind movement Up, Down, and Stop
* After calibation, position tracking is possible.

## Installation

### Step 1: Download files

#### Option 1: Via HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hrabbach&repository=schellenberg_usb&category=integration)

Make sure you have HACS installed. If you don't, run `wget -O - https://get.hacs.xyz | bash -` in HA.  
Choose Integrations under HACS. Click the '+' button on the bottom of the page, search for "schellenberg usb", choose it, and click install in HACS.

#### Option 2: Manual
Clone this repository or download the source code as a zip file and add/merge the `custom_components/` folder with its contents in your configuration directory.


### Step 2: Restart HA
In order for the newly added integration to be loaded, HA needs to be restarted.

### Step 3: Add integration to HA (<--- this is a step that a lot of people forget)
In HA, go to Configuration > Integrations.
In the bottom right corner, click on the big button with a '+'.

If the component is properly installed, you should be able to find 'Schellenberg USB' in the list. You might need to clear you browser cache for the integration to show up.

Select it, and the schellenberg usb integration is ready for use.

### Step 4: Pair your devices

1. In Home Assistant, go to **Settings > Devices & Services**
2. Find the **Schellenberg USB** integration and click on it
3. Click the **+** button or select **Pair device** from the menu
4. Put your blind motor into pairing mode (see [Device Pairing Instructions](#device-pairing-instructions))
5. Once pairing is successful, provide a friendly name for your blind

### Step 5: Calibrate your blinds

Calibration is essential for accurate position tracking. The integration measures how long it takes your blind to fully open and close, allowing it to calculate the current position during operation.

> [!IMPORTANT]
> This calibration is **not** the same as setting the end positions (fully open/closed limits) on your blind motor. End positions must be configured directly on the device itself using the motor's built-in adjustment features or a Schellenberg remote control before using this integration.

#### Starting Calibration

You can calibrate a blind:
- **During initial pairing**: After naming your device, you'll be prompted to calibrate
- **After pairing from the device page**: Go to the device and click the **Calibrate** gear icon (⚙️) as shown below

![Calibrate button location](images/calibrate-button.png)

*Click the gear icon labeled "Calibrate" in the top right corner of your blind device to start calibration.*

#### Calibration Steps

1. **Step 1 - Close the blind**: Ensure your blind is fully closed (all the way down). Press **Next** when ready.

2. **Step 2 - Measure open time**: 
   - Press **Start** in the dialog
   - Then press the **open button** on your physical remote/control
   - The integration will automatically detect when the blind starts moving and begin timing
   - Wait for the blind to fully open - the timer stops automatically when movement stops

3. **Step 3 - Measure close time**:
   - Press **Start** in the dialog  
   - Then press the **close button** on your physical remote/control
   - The integration will automatically detect when the blind starts moving and begin timing
   - Wait for the blind to fully close - the timer stops automatically when movement stops

4. **Complete**: The integration will display the measured open and close times and save them for position tracking

> [!TIP]
> There's no need to rush when pressing the buttons - the timer doesn't start until the integration receives a "moving" signal from the blind motor.

> [!NOTE]
> If calibration times seem incorrect, you can recalibrate at any time from the device options.

## Device Pairing Instructions

Each Schellenberg device has a specific button combination to enter pairing mode. You must put your device into pairing mode within 2 minutes of starting the pairing process in Home Assistant.

### ROLLODRIVE 65 PREMIUM / 75 PREMIUM (Electric Belt Winders)
**Art.Nr.: 22567, 22576, 22578, 22726, 22727, 22728, 22767**

To enter pairing mode:
1. Press and hold the **Sun (☀)** button and the **Up (▲)** button simultaneously
2. Hold for **5 seconds** until the LED flashes
3. The device is now in pairing mode

### ROLLOPOWER PLUS / STANDARD (Tube Motors)
**Art.Nr.: 20106, 20110, 20406, 20410, 20610, 20615, 20620, 20640, 20710, 20720, 20740**

These motors are controlled via external switches or remote controls. Pairing is typically done through the connected Schellenberg remote control or timer switch.

### Funk-Rollladenmotoren PREMIUM (Radio Tube Motors)
**Art.Nr.: 21106, 21110, 21210, 21220, 21240**

To enter pairing mode, refer to your specific remote control or timer switch manual. The pairing button combination varies by the control device used.

### General Tips

- Keep the USB Funk-Stick within range (approx. 20m indoors, 100m outdoors)
- Avoid metal obstructions between the stick and the motor
- If pairing fails, try moving the USB stick closer to the device
- Consult your device's manual for the exact pairing procedure if the above doesn't work

> [!NOTE]
> The pairing instructions above are based on common Schellenberg products. Your specific device may have different procedures - always refer to the device's original manual if unsure.

---

## Motor Types: Bidirectional vs Timed (Non-Bidirectional)

As of v1.3.0, the integration distinguishes between two motor types:

| Type | Description |
|------|-------------|
| **Bidirectional** | Motor sends movement events back to the stick. The integration detects when motion starts and stops — this is the classic mode. All previously paired motors are treated as bidirectional by default. |
| **Timed (non-bidirectional)** | Motor gives no movement feedback. The integration cannot detect when the motor starts or stops moving, so it times runs by measuring the wall-clock duration between button presses instead of waiting for events. |

You select the motor type once, when adding the device. A timed motor requires a separate calibration flow (described below); a bidirectional motor uses the event-based flow documented in [Step 5: Calibrate your blinds](#step-5-calibrate-your-blinds).

> [!NOTE]
> Legacy subentries created before v1.3.0 have no mode flag and are treated as bidirectional — existing paired motors are unaffected.

---

## Adding an Already-Paired Motor Manually

If a motor is already paired (for example it responds to an existing Schellenberg remote) you can add it to Home Assistant without triggering the radio-pairing procedure:

1. In Home Assistant, go to **Settings > Devices & Services**.
2. Find the **Schellenberg USB** integration and click on it.
3. Click the **+** button to add a device.
4. When the menu appears, choose **Add manually**.
5. Enter the motor's **device enum** — a two-character hex value (e.g. `10`, `11`, `1A`) that identifies the device on the radio bus.
6. Choose the **motor mode**: toggle on for bidirectional, toggle off for timed/non-bidirectional.
7. Optionally provide a friendly name (defaults to `Blind <enum>` if left blank).
8. For **timed motors only**: a second screen asks for the **initial position** (0–100 %). Set this to the motor's current physical position so the integration starts tracking from the right point. Use 100 % if the shutter is currently fully open.
9. Click **Submit** — the device is created immediately with no radio-pairing step.

> [!TIP]
> The device enum is the two-hex-character address the stick uses to address the motor. If you are unsure of the value, check your previous pairing records or consult the integration logs — each enrolled device is logged with its enum at startup.

---

## Timed Calibration (for Non-Bidirectional Motors)

> [!IMPORTANT]
> This section describes the **timed calibration flow** for non-bidirectional (timed) motors. It is separate from and additional to the event-based "Calibration Steps" in [Step 5](#step-5-calibrate-your-blinds), which applies only to bidirectional motors. Do not use the event-based flow for a timed motor — it will wait indefinitely for movement events that never arrive.

Because timed motors send no movement feedback, calibration is driven entirely by button presses: the integration sends a command, you watch the shutter move, and you press a button when it stops.

#### Prerequisites

- The shutter must be **fully open** (all the way up) before you start. If it is not, drive it to the top using your physical remote first.

#### Starting Timed Calibration

Access timed calibration the same way as regular calibration:

- **After adding a timed motor**: the timed calibration flow launches automatically once the device is created.
- **Later, from the device page**: click the **Calibrate** gear icon (⚙️) on the device page.

#### Timed Calibration Steps

1. **Precondition check**: Confirm the shutter is fully open. Press **Next** when ready — no command is sent at this point.

2. **Close run**:
   - The integration sends a **close** command to the motor.
   - Watch the shutter travel all the way down to its physical endstop. Wait until it has fully stopped.
   - Press **Next** to record the elapsed time.
   - The integration does **not** send a stop command — the motor coasts to its physical endstop naturally.

3. **Open run**:
   - The integration sends an **open** command to the motor.
   - Watch the shutter travel all the way up. Wait until it has fully stopped at the top endstop.
   - Press **Next** to record the elapsed time.
   - Again, no stop command is sent — the motor stops at its physical endstop.

4. **Confirm**: The integration shows the measured close time and open time. Press **Done** to save, or check **Redo** to discard the measurements and start again from the precondition step.

#### Guard Limits

The integration validates each run before accepting it:

| Condition | Guard | What to do |
|-----------|-------|------------|
| You pressed Next in under **2 seconds** | Rejected (likely a double-press or misfire) | The form re-shows; drive the shutter back to fully open manually and retry |
| You waited more than **120 seconds** | Rejected ("walked away" run) | The form re-shows; drive the shutter back to fully open manually and retry |

> [!IMPORTANT]
> After a guard error, the shutter position is unknown — it may have stopped mid-travel. Return the shutter to the **fully open** position using your physical remote before pressing Next again.

---

## Driving a Timed Motor to a Position

Once a timed motor has been calibrated, the position slider in the Home Assistant UI becomes active. Requesting a percentage (e.g., 50 %) causes the integration to:

1. Determine the direction of travel (open or close) from the current tracked position.
2. Send the appropriate command to the motor.
3. Schedule a stop command after the computed fraction of the full-travel time has elapsed.

The integration tracks position by dead-reckoning — it uses the calibrated open and close times to estimate where the shutter is. If the motor is ever moved outside of Home Assistant (e.g., by a physical remote), the tracked position will drift until the next full-travel recalibration.

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and the local quality gate (tests, lint, type-check, and spell-check) every change must pass.
