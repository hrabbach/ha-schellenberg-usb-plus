# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

<!--
  Add an entry here for every user-facing change as it merges, under the right heading.
  Only user-facing changes belong here (features, fixes, behaviour changes a user or
  installer would notice). Docs-only, tests, CI, and pure refactors get NO entry.
  At release time the maintainer renames this section to the new version (see below).

  ### Added       — new features
  ### Changed      — changes to existing behaviour
  ### Fixed        — bug fixes
  ### Removed       — removed features
-->

### Added

- Device pairing now assigns the lowest free address slot and shows a clear "device limit reached" error when every slot is in use, so re-pairing can no longer silently overwrite an existing motor.

### Fixed

- Pairing, device verification, and device-ID lookups now fail fast if the USB stick disconnects mid-operation, instead of hanging until the timeout.
- A late radio frame arriving as the stick disconnects no longer causes an internal error.
- A pairing task could keep running after the stick disconnected; it is now cancelled cleanly.
- Completed the German, Spanish, and French translations for the manual device-add errors and the timed-calibration screens (previously shown in English or as raw text keys).

## [1.0.0] - 2026-06-27

### Added

- USB Funk-Stick connection and automatic USB device detection
- Schellenberg motor pairing via radio (ROLLODRIVE, ROLLOPOWER, Funk-Rollladenmotoren)
- Manual "already-paired" device entry without radio pairing
- Bidirectional motor support with event-based position tracking and calibration
- Timed (non-bidirectional) motor support with wall-clock-based position tracking
- Separate open-time and close-time calibration for both motor types
- Position control (0–100%) for calibrated motors
- Signal filter: ignore unknown device signals toggle
- LED switch entity for USB stick LED control
- Stick status sensors (connection status, firmware version, mode)
- HACS-compatible via zip_release delivery

[unreleased]: https://github.com/hrabbach/ha-schellenberg-usb-plus/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/hrabbach/ha-schellenberg-usb-plus/releases/tag/v1.0.0
