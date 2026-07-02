<!-- generated-by: gsd-doc-writer -->
# Getting Started

This guide walks you from a fresh install to a working, calibrated Schellenberg roller-shutter motor in Home Assistant. Read it top-to-bottom the first time; experienced users can jump to the step they need.

---

## Prerequisites

- **Home Assistant** 2025.1.0 or later
- **Schellenberg USB Funk-Stick** plugged into the machine running Home Assistant (USB VID `16C0` / PID `05E1`, manufacturer string `van ooijen`)
- Serial port access — the HA host user must be able to open the serial device (typically `/dev/ttyUSB0` or `/dev/ttyACM0` on Linux)
- **HACS** installed (for the recommended install path)

---

## Step 1 — Install the integration

**Via HACS (recommended)**

This integration is distributed as a custom HACS repository. Add it once, then install as usual:

1. Open HACS in your Home Assistant sidebar.
2. Click the **three-dot menu** (top right) and choose **Custom repositories**.
3. Enter `hrabbach/ha-schellenberg-usb-plus` as the repository and select **Integration** as the category. Click **Add**.
4. The integration now appears in the HACS list. Search for `Schellenberg USB+`, select it, and click **Download**.
5. Restart Home Assistant when prompted.

Alternatively, use the one-click badge:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hrabbach&repository=ha-schellenberg-usb-plus&category=integration)

**Manual install**

Copy the `custom_components/schellenberg_usb/` folder from this repository into your HA `config/custom_components/` directory, then restart Home Assistant.

---

## Step 2 — Add the integration (hub setup)

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Schellenberg USB+** and select it.
3. If the stick was already plugged in, Home Assistant may have auto-discovered it and will ask you to confirm the serial port. Otherwise, enter the port path (default `/dev/ttyUSB0`) and click **Submit**.

The hub entry is now created and the stick connects automatically. For serial port options and USB auto-discovery details see [docs/CONFIGURATION.md](CONFIGURATION.md).

---

## Step 3 — Add a motor

Go to **Settings → Devices & Services**, find **Schellenberg USB+**, and click **+ Add device**. A menu offers two paths:

### Option A — Auto-pair (motor is nearby and reachable)

Use this when the motor has never been paired to the USB stick and is within radio range.

1. Choose **Pair automatically**.
2. Put your motor into pairing mode (see [README — Device Pairing Instructions](../README.md#device-pairing-instructions) for button combinations by model).
3. Click **Pair** in the dialog. The integration signals the stick to accept the next pairing message and waits up to 2 minutes for the motor to respond.
4. When the motor responds, you are prompted to give it a friendly name. After naming, calibration starts immediately as the next step in the same dialog (Step 4A below).

### Option B — Manual add (motor is already paired to the stick)

Use this when the motor was paired to the USB stick by hand before you installed this integration, or when the motor never sends events back (non-bidirectional / timed motors).

> **Motors already paired only to a physical remote (not to the stick):** The in-app **Pair automatically** flow waits for the motor to transmit its device ID over RF. A silent, non-bidirectional motor that was only ever paired to a physical remote will never do this and will always time out. You need to perform the **wireless delegation pairing** procedure first — see [README — Device Pairing Instructions](../README.md#device-pairing-instructions) for the step-by-step walkthrough — then return here and enter the chosen enumerator via **Add manually**.

1. Choose **Add manually (already paired)**.
2. Enter the **device enumerator** — a user-chosen two-character hex id (e.g. `10`, `11`, `1A`) that the stick uses to address this motor. This is not an address the motor already has; it is assigned when the motor is paired to the stick:
   - If you auto-paired this motor earlier using **Pair automatically**, the integration allocated it automatically (starting at `10` for the first motor). Check the HA device entry or the integration logs for the value it assigned.
   - If you are completing a delegation pairing (see above), use the same hex value you sent in the delegation frames — that is the value to enter here.
   - The value is case-insensitive and must be exactly two hex characters.
3. Choose the motor type:
   - **Bidirectional motor** (toggle on, default) — motor sends movement events back to the stick (most ROLLODRIVE PREMIUM motors). Leave this toggled on.
   - **Bidirectional motor** (toggle off) — motor never confirms movement; drive-to-position relies on button-press timing (timed motor).
4. Optionally enter a friendly name; if left blank, the name defaults to `Blind <enum>`.
5. For timed motors only: set an **initial position** (0 = fully closed, 100 = fully open) that reflects where the shutter physically is right now. This seeds position tracking until calibration completes.

The motor appears as a cover entity immediately after this step.

---

## Step 4 — Calibrate

Calibration records how many seconds the motor takes to travel from fully closed to fully open (and back). Without it, position tracking and drive-to-percentage commands are unavailable.

> **Note:** Calibration does NOT set motor end-stops. Physical travel limits must be configured on the motor itself using its built-in adjustment features or a Schellenberg remote before you calibrate here.

### 4A — Bidirectional motors (event-based)

The integration detects movement automatically — you control the motor with your physical remote during calibration.

1. Open the device page for your motor and click **Configure**, then choose **Calibrate travel time** from the menu (or the integration launches calibration automatically right after auto-pairing).
2. **Step 1 — Close:** Ensure the shutter is fully closed (all the way down), then press **Next**.
3. **Step 2 — Open the blind:** Press **Start** in the dialog, then press the **open** button on your physical remote. The integration waits for the motor's "started moving up" event and begins timing automatically. Wait for the motor to reach the top endstop and stop. The integration detects the stop event and advances.
4. **Step 3 — Close the blind:** Press **Start** in the dialog, then press the **close** button on your physical remote. The integration waits for the motor's "started moving down" event and begins timing automatically. Wait for the motor to reach the bottom endstop and stop.
5. **Complete:** The measured open and close times are displayed. Press **Done** to save.

Full model-specific pairing instructions are in [README — Device Pairing Instructions](../README.md#device-pairing-instructions).

### 4B — Timed (non-bidirectional) motors (button-press timing)

The integration drives the motor itself and measures elapsed time between your button presses — no motor events are required.

1. Open the device page for your motor and click **Configure**, then choose **Calibrate travel time** from the menu.
2. **Precondition step:** Confirm that the shutter is fully open (at the top) before proceeding, then press **Next**.
3. **Close run:** The integration sends a close command automatically. Wait until the motor reaches the bottom endstop and stops on its own, then press **Submit**. (Valid travel: 2 – 120 seconds.)
4. **Open run:** The integration sends an open command automatically. Wait until the motor reaches the top endstop and stops on its own, then press **Submit**.
5. **Confirm:** The measured open and close times are shown. Press **Done** to save, or check **Redo** to repeat the measurements.

After calibration the shutter position is set to 100 % (fully open), matching where the motor ended up.

---

## Step 5 — Control the motor

Once calibrated, your motor appears in Home Assistant as a standard cover entity with:

- **Open / Close / Stop** buttons
- **Position slider** — drag to any percentage; the integration calculates travel time automatically

From this point the entity works like any other HA cover: use it in automations, dashboards, and voice assistants.

---

## Step 6 — Bind a remote (optional)

You can bind a handheld physical remote to a motor so its button presses are recognized by Home Assistant. This is most useful for **timed (non-bidirectional) motors**: a bound remote drives best-effort, time-based position tracking and adds a remote-button event entity for that motor. (Bidirectional motors already track position from motor events and don't need this for positioning.)

1. Open the device page for your motor, click **Configure**, and choose **Bind a remote** from the menu (shown when no remote is bound yet — motors with a remote already bound show **Change remote** / **Remove remote** instead).
2. **First press:** Press any button on the remote you want to bind. The integration listens for up to 15 seconds.
3. **Second press:** Press the *same* button again within 15 seconds to confirm. The two presses must match exactly, or the bind is rejected.
4. **Confirm:** A dialog shows the captured remote id and asks you to confirm.
   - Normally you'll see **Bind remote to \<motor>?** — click **Bind** to save, or **Try again** to recapture.
   - If the remote is a **multi-channel** handheld and its other channel is already bound to a *different* motor, you instead see **Add channel to \<motor>?** — confirming adds this channel to the current motor while leaving the other motor's existing binding untouched.

To change or remove a binding later, click **Configure** on the device and choose **Change remote** (repeats the same press-twice flow) or **Remove remote**.

---

## Recalibrating

If travel times change (motor replaced, mechanical adjustment, etc.), open the motor's device page, click **Configure**, and choose **Calibrate travel time** to run calibration again. Existing times are overwritten on confirmation.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Integration not found after install | HA not restarted, or browser cache | Restart HA; clear browser cache |
| "Cannot connect" on serial port | Wrong path or permission denied | Verify the path with `ls /dev/tty*`; add the HA user to the `dialout` group |
| Auto-pair times out | Motor not in pairing mode, out of range, or Pair not clicked in time | Put motor in pairing mode first, then click Pair in the dialog; move the stick closer if needed |
| `invalid_enum_format` error on manual add | Enumerator is not exactly two hex characters | Use values like `10`, `11`, `1A` — no prefix, no spaces |
| `duplicate_enum` error on manual add | That enumerator is already used by another motor | Each motor must have a unique two-character hex enumerator |
| Timed calibration rejects "too short" | Submitted before motor reached endstop | Wait for the motor to stop completely before pressing Submit |
| Timed calibration rejects "too long" | More than 120 seconds elapsed before pressing Submit | Ensure the motor reaches the endstop promptly; avoid leaving the dialog idle |
| Position drifts over time | Calibration times no longer accurate | Recalibrate from the device page |
| Remote bind: "No remote detected" | Neither press was received within the 15-second window, or the stick disconnected | Move the remote closer and press firmly within 15 seconds of starting each press; check the stick connection if it persists |
| Remote bind: "The two presses did not match" | A different button (or a different remote) was pressed the second time | Press the exact same button on the same remote for both presses |
| Remote bind: "That button is registered to a motor, not a remote" | The captured signal matches an enrolled motor's id, not a handheld remote | Press a button on your physical remote, not a motor control |
| Remote bind: "already bound to \<motor>" | That exact remote channel is already bound to a different motor | Remove the binding from the other motor first (Configure → Remove remote), or use a different remote/channel |
| "Add channel to \<motor>?" appears instead of the normal bind confirmation | The remote is multi-channel and another of its channels is already bound to a different motor | Expected for multi-channel remotes — confirming adds only this channel; the other motor's binding is unaffected. Click Try again if you pressed the wrong remote |

For configuration details (serial port, baud rate, subentry data) see [docs/CONFIGURATION.md](CONFIGURATION.md).
