# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
