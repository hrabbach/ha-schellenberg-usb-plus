"""Timed calibration flow handler for Schellenberg USB.

This handler implements an event-free button-press timing flow for
non-bidirectional (timed) motors. It drives the motor via HA commands
and records elapsed time between form submits — it does NOT wait for
motor events (which timed motors never send). SC#1 is satisfied by
construction: the flow blocks only on HA form rendering, never on a
protocol message.

Design decisions honoured:
  D-04  Close-first, then open; precondition: shutter starts fully open.
  D-05  Precondition via instruction step, NOT an auto-drive.
  D-06  End-press is RECORD-ONLY — no CMD_STOP ever sent.
  D-07  time.monotonic() throughout, never time.time().
  D-08  Max-travel cap: 120 s (CAL_MAX_TRAVEL_TIME).
  D-09  Min-sanity floor: 2 s (CAL_MIN_TRAVEL_TIME).
  D-10  Confirm-before-save screen with redo option.
  D-12  Emit SIGNAL_CALIBRATION_COMPLETED on success.
  D-14  final_position=100 — timed flow ends fully open.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CAL_MAX_TRAVEL_TIME,
    CAL_MIN_TRAVEL_TIME,
    CMD_DOWN,
    CMD_UP,
    SIGNAL_CALIBRATION_COMPLETED,
)

_LOGGER = logging.getLogger(__name__)

# Type alias for flow results (mirrors options_flow_calibration.py convention)
FlowResult = ConfigFlowResult | SubentryFlowResult


class TimedCalibrationFlowHandler:
    """Handle timed (button-press) calibration flow steps.

    The handler is a plain Python class; the outer SchellenbergPairingSubentryFlow
    delegates each step to it.  Every step method either shows a form or returns
    an abort — the ONLY await inside a drive step is the control_blind call.
    No asyncio.Event / wait_for / dispatcher listener is used (SC#1).

    The flow parameter must be a ConfigSubentryFlow (not OptionsFlow) because
    this handler calls _get_entry() which is only available on ConfigSubentryFlow.
    """

    def __init__(self, flow: ConfigSubentryFlow) -> None:
        """Initialize the timed calibration flow handler."""
        self.flow = flow
        self._selected_device: dict[str, Any] | None = None
        self._close_start_time: float | None = None
        self._open_start_time: float | None = None
        self._close_time: float | None = None
        self._open_time: float | None = None

    def set_selected_device(self, device: dict[str, Any]) -> None:
        """Public setter — assign selected device dict {"id","name","enum"}.

        Called by the outer flow before entering the first step (D-01).
        """
        self._selected_device = device

    async def async_step_timed_cal_precondition(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show an instruction step: ensure shutter is fully open (D-05).

        No drive command is sent here.  The user presses Next to confirm
        the shutter is in the open position before we start the close run.
        """
        if self._selected_device is None:
            return self.flow.async_abort(reason="device_not_found")

        if user_input is not None:
            # User confirmed precondition — start the close run.
            return await self.async_step_timed_cal_close()

        return self.flow.async_show_form(
            step_id="timed_cal_precondition",
            data_schema=vol.Schema({}),
            description_placeholders={
                "device_name": self._selected_device["name"],
            },
            last_step=False,
        )

    async def async_step_timed_cal_close(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Send close command on first visit; record elapsed on second (D-04).

        Abort/guard sends no stop command — motor runs to endstop (D-06, REVIEW-5).
        This is the intended behaviour: the user's physical endstop auto-stops
        the motor; we only capture the timestamp.
        """
        if self._selected_device is None:
            return self.flow.async_abort(reason="device_not_found")

        errors: dict[str, str] = {}

        if user_input is None:
            # First visit: send close command and record start time.
            # D-07: time.monotonic() BEFORE await (Phase 3 locked rule).
            hub_entry = self.flow._get_entry()
            api = hub_entry.runtime_data
            device_enum = self._selected_device.get("enum", "")
            self._close_start_time = time.monotonic()  # D-07 — BEFORE await
            await api.control_blind(device_enum, CMD_DOWN)  # D-04 close-first
            _LOGGER.debug(
                "Timed calibration: close command sent to %s", device_enum
            )
            return self.flow.async_show_form(
                step_id="timed_cal_close",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "device_name": self._selected_device["name"],
                },
                last_step=False,
            )

        # Second visit: user submitted — motor has reached the bottom endstop.
        # D-06: no CMD_STOP sent; the motor auto-stopped at the physical endstop.
        elapsed = time.monotonic() - (self._close_start_time or 0.0)

        if elapsed < CAL_MIN_TRAVEL_TIME:
            # D-09: reject runs shorter than 2 s (likely double-press / misfire).
            # Motor is now at an UNKNOWN position (too-short run may still be
            # moving). Reset start time — guard re-show does NOT re-send CMD_DOWN.
            errors["base"] = "timed_cal_too_short"
            self._close_start_time = None
            return self.flow.async_show_form(
                step_id="timed_cal_close",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "device_name": self._selected_device["name"],
                },
                errors=errors,
                last_step=False,
            )

        if elapsed > CAL_MAX_TRAVEL_TIME:
            # D-08: reject "walked away" runs exceeding 120 s.
            # Motor ran to endstop but is now at an UNKNOWN position relative
            # to where we expect it. Reset — guard re-show does NOT re-send CMD_DOWN.
            errors["base"] = "timed_cal_too_long"
            self._close_start_time = None
            return self.flow.async_show_form(
                step_id="timed_cal_close",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "device_name": self._selected_device["name"],
                },
                errors=errors,
                last_step=False,
            )

        self._close_time = round(elapsed, 2)
        _LOGGER.debug(
            "Timed calibration: close_time recorded as %s s", self._close_time
        )
        return await self.async_step_timed_cal_open()

    async def async_step_timed_cal_open(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Send open command on first visit; record elapsed on second (D-04).

        Abort/guard sends no stop command — motor runs to endstop (D-06, REVIEW-5).
        This is the intended behaviour; no CMD_STOP is ever issued by this handler.
        """
        if self._selected_device is None:
            return self.flow.async_abort(reason="device_not_found")

        errors: dict[str, str] = {}

        if user_input is None:
            # First visit: send open command and record start time.
            # D-07: time.monotonic() BEFORE await (Phase 3 locked rule).
            hub_entry = self.flow._get_entry()
            api = hub_entry.runtime_data
            device_enum = self._selected_device.get("enum", "")
            self._open_start_time = time.monotonic()  # D-07 — BEFORE await
            await api.control_blind(device_enum, CMD_UP)  # D-04 open-second
            _LOGGER.debug(
                "Timed calibration: open command sent to %s", device_enum
            )
            return self.flow.async_show_form(
                step_id="timed_cal_open",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "device_name": self._selected_device["name"],
                },
                last_step=False,
            )

        # Second visit: user submitted — motor has reached the top endstop.
        # D-06: no CMD_STOP sent; the motor auto-stopped at the physical endstop.
        elapsed = time.monotonic() - (self._open_start_time or 0.0)

        if elapsed < CAL_MIN_TRAVEL_TIME:
            # D-09: reject too-short run.
            # Motor is at an UNKNOWN position — reset, no re-drive.
            errors["base"] = "timed_cal_too_short"
            self._open_start_time = None
            return self.flow.async_show_form(
                step_id="timed_cal_open",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "device_name": self._selected_device["name"],
                },
                errors=errors,
                last_step=False,
            )

        if elapsed > CAL_MAX_TRAVEL_TIME:
            # D-08: reject "walked away" run.
            # Motor is at an UNKNOWN position — reset, no re-drive.
            errors["base"] = "timed_cal_too_long"
            self._open_start_time = None
            return self.flow.async_show_form(
                step_id="timed_cal_open",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "device_name": self._selected_device["name"],
                },
                errors=errors,
                last_step=False,
            )

        self._open_time = round(elapsed, 2)
        _LOGGER.debug(
            "Timed calibration: open_time recorded as %s s", self._open_time
        )
        return await self.async_step_timed_cal_confirm()

    async def async_step_timed_cal_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show measured times; user confirms or redoes (D-10).

        On confirm: emit SIGNAL_CALIBRATION_COMPLETED with final_position=100
        (timed flow ends fully open — D-14) and abort with reconfigure_successful.
        On redo: reset all timing attrs and return to precondition step.
        No partial data is persisted until the user presses Done here (D-11).
        """
        if self._selected_device is None:
            return self.flow.async_abort(reason="device_not_found")
        if self._open_time is None or self._close_time is None:
            # Device was found; timing state is just incomplete (D-11).
            # Use an accurate reason so the user isn't misled into
            # troubleshooting a "missing device".
            return self.flow.async_abort(reason="timed_cal_incomplete")

        if user_input is not None:
            redo = user_input.get("redo", False)
            if redo:
                # Reset all timing state — user wants to redo measurements.
                # D-15: re-calibration overwrites; no "already calibrated" block.
                self._close_time = None
                self._open_time = None
                self._close_start_time = None
                self._open_start_time = None
                return await self.async_step_timed_cal_precondition()
            # D-12: emit signal so cover updates live without restart.
            # D-14: final_position=100 — timed flow ends at the top (fully open).
            await self._emit_calibration_signal()
            return self.flow.async_abort(reason="reconfigure_successful")

        return self.flow.async_show_form(
            step_id="timed_cal_confirm",
            data_schema=vol.Schema(
                {vol.Optional("redo", default=False): bool}
            ),
            description_placeholders={
                "device_name": self._selected_device["name"],
                "open_time": f"{self._open_time:.2f}",
                "close_time": f"{self._close_time:.2f}",
            },
            last_step=True,
        )

    async def _emit_calibration_signal(self) -> None:
        """Persist calibration, then emit SIGNAL_CALIBRATION_COMPLETED (D-12, D-14).

        Payload: (device_id, open_time, close_time, 100)
        The '100' is the final_position — the timed flow ends with the shutter
        fully open (D-14). The cover's _handle_calibration_completed receives
        these four positional args; default=0 in the cover signature preserves
        bidirectional-path compatibility.

        Durability (timed-cal-uses-default-time): the times are saved to the
        cover calibration Store *here*, AWAITED, BEFORE the signal is emitted
        and before the flow aborts with `reconfigure_successful`. HA reloads the
        entry on that abort, and the reload rebuilds the cover by reading the
        Store — so the write MUST be flushed first. Relying on the cover's
        fire-and-forget save (cover.py) raced the reload and left the rebuilt
        cover on DEFAULT_TRAVEL_TIME (60s), uncalibrated.
        """
        if (
            self._selected_device is None
            or self._open_time is None
            or self._close_time is None
        ):
            return

        # Persist to the cover calibration Store synchronously so the
        # reconfigure reload rebuilds the cover with the calibrated times.
        # Imported lazily to avoid a cover<->flow import cycle at module load.
        from .cover import _save_calibration

        hub_entry = self.flow._get_entry()
        await _save_calibration(
            self.flow.hass,
            hub_entry.entry_id,
            self._selected_device["id"],
            self._open_time,
            self._close_time,
        )

        async_dispatcher_send(
            self.flow.hass,
            SIGNAL_CALIBRATION_COMPLETED,
            self._selected_device["id"],
            self._open_time,
            self._close_time,
            100,  # final_position: timed flow ends at top (D-14)
        )
        _LOGGER.debug(
            "Timed calibration signal emitted for device %s "
            "(open=%.2f s, close=%.2f s, final_position=100)",
            self._selected_device["id"],
            self._open_time,
            self._close_time,
        )
