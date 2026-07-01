"""Config flow for Schellenberg USB integration."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable
from typing import Any, cast

import serial  # NOTE: blocking open used only to sanity-check connectivity
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import (
    CONF_BIDIRECTIONAL,
    CONF_CLOSE_TIME,
    CONF_DEVICE_ID,
    CONF_INITIAL_POSITION,
    CONF_OPEN_TIME,
    CONF_REMOTE_ID,
    CONF_SERIAL_PORT,
    DOMAIN,
    LEARN_REMOTE_CAPTURE_TIMEOUT,
    SUBENTRY_TYPE_BLIND,
)
from .api import DeviceLimitReached
from .options_flow import SchellenbergOptionsFlowHandler
from .options_flow_calibration import CalibrationFlowHandler
from .options_flow_timed_calibration import TimedCalibrationFlowHandler

_LOGGER = logging.getLogger(__name__)


class SchellenbergUsbConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Schellenberg USB."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return SchellenbergOptionsFlowHandler()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        # Use constant for subentry type so strings/json and code stay in sync
        return {SUBENTRY_TYPE_BLIND: SchellenbergPairingSubentryFlow}

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_port: str | None = None
        self._discovered_title: str | None = None
        self._discovered_unique: str | None = None

    # -------------------------
    # MENU FLOW (Hub only)
    # -------------------------
    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show menu to set up hub."""
        # For now, only allow setting up the hub through the user flow
        # Device pairing is handled through the subentry flow
        return await self.async_step_user()

    # -------------------------
    # USER-INITIATED FLOW
    # -------------------------
    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Handle the initial step started by the user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            port = user_input[CONF_SERIAL_PORT]
            try:
                # Run blocking serial open in the executor to avoid blocking the
                # HA event loop (CR-02 — serial.Serial() can block for 100-500ms).
                def _open_serial(p: str) -> None:
                    conn = serial.Serial(p)
                    conn.close()

                await self.hass.async_add_executor_job(_open_serial, port)

                # Use the port path as the unique ID when set up manually.
                await self.async_set_unique_id(port, raise_on_progress=False)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Schellenberg USB ({port})", data=user_input
                )
            except serial.SerialException:
                errors["base"] = "cannot_connect"
                _LOGGER.error("Failed to connect to serial port %s", port)
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
                # HA config-flow must surface 'unknown' to the user rather than
                # crashing the flow; broad catch is intentional here (RESEARCH Pitfall 7).
                _LOGGER.exception("An unexpected error occurred")

        return self._form_schema(errors, default_port="/dev/ttyUSB0")

    # -------------------------
    # USB DISCOVERY FLOW
    # -------------------------
    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle discovery from the USB subsystem."""
        # Try to get the most stable unique identifier we can (serial number if present).
        unique = getattr(discovery_info, "serial_number", None) or (
            f"{getattr(discovery_info, 'vid', 'unknown')}:"
            f"{getattr(discovery_info, 'pid', 'unknown')}:"
            f"{getattr(discovery_info, 'device', 'unknown')}"
        )

        # Prefer the OS device path for the default value in the confirmation form.
        port = getattr(discovery_info, "device", None)
        manufacturer = getattr(discovery_info, "manufacturer", None) or "Schellenberg"
        description = getattr(discovery_info, "description", None) or "USB device"

        # Save for the confirm step
        self._discovered_port = port
        self._discovered_unique = unique
        self._discovered_title = f"{manufacturer} {description}".strip()

        # Deduplicate if already configured; update the stored port if it changed.
        await self.async_set_unique_id(unique, raise_on_progress=False)
        self._abort_if_unique_id_configured(
            updates={CONF_SERIAL_PORT: port} if port else None
        )

        # Ask for confirmation (and allow editing the port if the host maps it differently)
        return await self.async_step_usb_confirm()

    async def async_step_usb_confirm(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Confirm USB-discovered device and create the entry."""
        errors: dict[str, str] = {}

        # If we don’t have a port path, let the user supply one.
        default_port = self._discovered_port or "/dev/ttyUSB0"

        if user_input is not None:
            port = user_input[CONF_SERIAL_PORT]
            try:
                # Run blocking serial open in the executor to avoid blocking the
                # HA event loop (CR-02 — serial.Serial() can block for 100-500ms).
                def _open_serial(p: str) -> None:
                    conn = serial.Serial(p)
                    conn.close()

                await self.hass.async_add_executor_job(_open_serial, port)

                # unique_id was already set in async_step_usb(), re-assert and create the entry
                await self.async_set_unique_id(
                    self._discovered_unique, raise_on_progress=False
                )
                self._abort_if_unique_id_configured()

                title = self._discovered_title or f"Schellenberg USB ({port})"
                return self.async_create_entry(
                    title=title, data={CONF_SERIAL_PORT: port}
                )
            except serial.SerialException:
                errors["base"] = "cannot_connect"
                _LOGGER.error("Failed to connect to serial port %s", port)
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
                # HA config-flow must surface 'unknown' to the user rather than
                # crashing the flow; broad catch is intentional here (RESEARCH Pitfall 7).
                _LOGGER.exception("An unexpected error occurred during USB confirm")

        # Mark as confirm-only so the UI shows a simple confirmation experience
        self._set_confirm_only()
        return self._form_schema(
            errors, default_port=default_port, step_id="usb_confirm"
        )

    # -------------------------
    # Helpers
    # -------------------------
    @callback
    def _form_schema(
        self, errors: dict[str, str], default_port: str, step_id: str = "user"
    ) -> ConfigFlowResult:
        """Return a form with a (prefilled) serial port field."""
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SERIAL_PORT, default=default_port
                    ): selector.TextSelector(),
                }
            ),
            errors=errors,
        )


class SchellenbergPairingSubentryFlow(ConfigSubentryFlow):
    """Flow for adding new blind devices as subentries."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the subentry flow."""
        super().__init__()
        self.calibration_handler: CalibrationFlowHandler | None = None
        self.timed_cal_handler: TimedCalibrationFlowHandler | None = None
        self._pending_device_id: str | None = None
        self._pending_device_enum: str | None = None
        self._pending_device_name: str | None = None
        self._pending_is_bidirectional: bool = False
        # Phase 15: capture state for learn-by-press remote binding (D-03..D-10)
        self._first_capture_id: str | None = None
        self._is_change_mode: bool = False
        self._listen_first_task: asyncio.Task[Any] | None = None
        self._listen_second_task: asyncio.Task[Any] | None = None
        # Error-carry vars so listen_timeout renders the correct one of four
        # error keys (remote_capture_timeout / remote_capture_disconnected /
        # remote_is_motor / remote_already_bound / remote_press_mismatch)
        # that was set by the preceding listen_first/listen_second/policy step.
        self._listen_error_key: str | None = None
        self._listen_error_placeholders: dict[str, str] | None = None

    def _get_calibration_handler(self) -> CalibrationFlowHandler:
        """Return (and lazily create) the calibration flow handler."""
        if self.calibration_handler is None:
            self.calibration_handler = CalibrationFlowHandler(self)
        return self.calibration_handler

    def _get_timed_cal_handler(self) -> TimedCalibrationFlowHandler:
        """Return (and lazily create) the timed calibration flow handler."""
        if self.timed_cal_handler is None:
            self.timed_cal_handler = TimedCalibrationFlowHandler(self)
        return self.timed_cal_handler

    async def _await_subentry_result(
        self,
        step_coro: Awaitable[ConfigFlowResult | SubentryFlowResult],
    ) -> SubentryFlowResult:
        """Await a calibration step and cast to SubentryFlowResult for mypy."""
        return cast(SubentryFlowResult, await step_coro)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Entry point when the user clicks the 'Add device' button.

        Home Assistant initiates user-triggered subentry flows via the `user`
        step (per HA config-subentry docs) — NOT async_step_{subentry_type}.
        Delegate to the menu so the user can choose auto-pair or manual-add.
        """
        _LOGGER.debug("Subentry blind flow initiated")
        return await self.async_step_menu(user_input)

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show menu: Pair automatically, Add manually, or Delegate.

        async_show_menu(step_id="menu") REQUIRES a matching async_step_menu
        method to exist — HA validates that a shown step_id resolves to a
        handler (it raises UnknownStep otherwise). Selecting an option routes
        to async_step_{option}: 'pair', 'manual_add', or 'delegate'.
        """
        # Reset the per-flow pending state at the menu (the single re-entry
        # point for every branch) so a value populated by one branch — e.g.
        # a delegation-populated _pending_device_id — cannot leak into another
        # branch's guard if the user backs up to the menu and picks a
        # different option (WR-03 state hygiene).
        self._pending_device_id = None
        self._pending_device_enum = None
        self._pending_device_name = None
        self._pending_is_bidirectional = False
        return self.async_show_menu(
            step_id="menu",
            menu_options=["pair", "manual_add", "delegate"],
        )

    async def async_step_manual_add(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect device enum, mode, and optional name for manual-add."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Normalize to uppercase before validation and storage (Pitfall 4)
            device_enum = user_input.get("device_enum", "").upper()

            # Format check: exactly 2 hex characters
            if not re.match(r"^[0-9A-Fa-f]{2}$", device_enum):
                errors["device_enum"] = "invalid_enum_format"
            else:
                # Duplicate check across existing blind subentries
                hub_entry = self._get_entry()
                existing_enums = {
                    s.data.get("device_enum")
                    for s in hub_entry.subentries.values()
                    if s.subentry_type == SUBENTRY_TYPE_BLIND
                }
                if device_enum in existing_enums:
                    errors["device_enum"] = "duplicate_enum"

            if not errors:
                # Resolve mode — BooleanSelector returns a real Python bool
                is_bidirectional: bool = bool(user_input.get(CONF_BIDIRECTIONAL, True))
                device_name = user_input.get("device_name") or f"Blind {device_enum}"
                self._pending_device_enum = device_enum
                self._pending_device_name = device_name
                self._pending_is_bidirectional = is_bidirectional

                if is_bidirectional:
                    _LOGGER.info(
                        "Creating bidirectional manual subentry for enum %s",
                        device_enum,
                    )
                    return self.async_create_entry(
                        title=device_name,
                        data={
                            CONF_DEVICE_ID: device_enum,
                            "device_enum": device_enum,
                            CONF_BIDIRECTIONAL: True,
                        },
                        unique_id=device_enum,
                    )
                # Timed: advance to initial-position step
                _LOGGER.debug("Timed motor %s: advancing to position step", device_enum)
                return await self.async_step_manual_position()

        return self.async_show_form(
            step_id="manual_add",
            data_schema=vol.Schema(
                {
                    vol.Required("device_enum"): selector.TextSelector(),
                    vol.Required(
                        CONF_BIDIRECTIONAL, default=True
                    ): selector.BooleanSelector(),
                    vol.Optional("device_name"): selector.TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_manual_position(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect initial position for timed motors (shown only after mode=timed)."""
        if not self._pending_device_enum:
            return self.async_abort(reason="pairing_failed")

        if user_input is not None:
            initial_position = int(user_input.get("initial_position", 100))
            # Clamp to 0-100 as defense in depth (slider already bounds, but be safe)
            initial_position = max(0, min(100, initial_position))
            device_enum = self._pending_device_enum or ""
            device_name = self._pending_device_name or f"Blind {device_enum}"
            _LOGGER.info(
                "Creating timed manual subentry for enum %s at initial position %d%%",
                device_enum,
                initial_position,
            )
            return self.async_create_entry(
                title=device_name,
                data={
                    CONF_DEVICE_ID: device_enum,
                    "device_enum": device_enum,
                    CONF_BIDIRECTIONAL: False,
                    CONF_INITIAL_POSITION: initial_position,
                },
                unique_id=device_enum,
            )

        return self.async_show_form(
            step_id="manual_position",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "initial_position", default=100
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=1,
                            unit_of_measurement="%",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            ),
            description_placeholders={
                "device_name": self._pending_device_name or "",
            },
            last_step=True,
        )

    async def async_step_delegate(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegation pairing: show P-button → Stop instructions, then transmit.

        On first call (user_input is None) renders the instruction form so the
        user can put the motor into learn mode. On submit, advances directly
        to the transmit step where the handshake fires (D-03/D-04).
        The detailed step-by-step copy lives in strings.json under
        step_id='delegate' (Plan 03) — no UI copy is hardcoded here.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="delegate",
                data_schema=vol.Schema({}),
            )
        return await self.async_step_delegate_transmit()

    async def async_step_delegate_transmit(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegation transmit: fire api.delegation_pair() on submit.

        Renders a confirm form (no-schema) on first call. On submit:
          1. Calls api.abort_delegation_pair() to clear any stale future (D-09).
          2. Calls api.delegation_pair() and on success advances to the name
             step (via async_step_delegate_name).
          3. On DeviceLimitReached: re-shows this form with device_limit_reached.
          4. On ConnectionError/OSError: re-shows this form with delegation_failed
             (retry-in-place, NOT async_abort — D-09/PAIR-04).
        """
        if user_input is None:
            return self.async_show_form(
                step_id="delegate_transmit",
                data_schema=vol.Schema({}),
            )

        hub_entry = self._get_entry()
        api = hub_entry.runtime_data

        # runtime_data is only populated while the hub entry is LOADED. If the
        # stick is unplugged at HA start, the entry is in SETUP_RETRY, or the
        # integration is mid-reload, runtime_data is None and dereferencing it
        # would raise AttributeError and crash the subentry flow (WR-02).
        # Surface a friendly retry-in-place error instead.
        if api is None:
            _LOGGER.warning(
                "Delegation attempted while hub entry not loaded"
            )
            return self.async_show_form(
                step_id="delegate_transmit",
                data_schema=vol.Schema({}),
                errors={"base": "delegation_failed"},
            )

        # D-09 / Pitfall-11: clear stale future BEFORE each attempt
        api.abort_delegation_pair()

        try:
            device_id, device_enum = await api.delegation_pair()
        except DeviceLimitReached:
            return self.async_show_form(
                step_id="delegate_transmit",
                data_schema=vol.Schema({}),
                errors={"base": "device_limit_reached"},
            )
        except (ConnectionError, OSError):
            _LOGGER.warning(
                "Delegation pairing failed — connection error; user may retry"
            )
            return self.async_show_form(
                step_id="delegate_transmit",
                data_schema=vol.Schema({}),
                errors={"base": "delegation_failed"},
            )

        _LOGGER.info(
            "Delegation pairing succeeded: device_id=%s enum=%s",
            device_id,
            device_enum,
        )
        self._pending_device_id = device_id
        self._pending_device_enum = device_enum
        self._pending_device_name = None
        return await self.async_step_delegate_name()

    async def async_step_delegate_name(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegation name collection: ask for a friendly device name.

        Mirrors async_step_name_device WITHOUT the calibration hand-off (D-07:
        lean create — calibrate later via reconfigure). Advances to the
        separate position step on submit (LOCKED split shape, REVIEWS finding 3).
        """
        if user_input is None:
            return self.async_show_form(
                step_id="delegate_name",
                data_schema=vol.Schema(
                    {
                        vol.Optional("device_name"): selector.TextSelector(),
                    }
                ),
            )

        device_enum = self._pending_device_enum or ""
        device_name = (
            user_input.get("device_name")
            or f"Blind {device_enum}"
        )
        self._pending_device_name = device_name
        return await self.async_step_delegate_position()

    async def async_step_delegate_position(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegation position slider: set initial position then create subentry.

        Defensive guard mirrors async_step_manual_position (abort on missing
        enum). Creates a timed (CONF_BIDIRECTIONAL=False) subentry with zero
        inbound frames — no ACK awaited (PAIR-03/D-06/D-07).
        The 'test by pressing Open in HA' honesty note (D-08) lives in
        strings.json under the 'delegate_position' step description (Plan 03).
        Uses the same data shape as async_step_manual_position so cover.py
        entity creation logic works identically.
        """
        if not self._pending_device_enum:
            return self.async_abort(reason="pairing_failed")

        if user_input is not None:
            initial_position = max(
                0, min(100, int(user_input.get("initial_position", 100)))
            )
            device_enum = self._pending_device_enum
            device_name = (
                self._pending_device_name or f"Blind {device_enum}"
            )
            _LOGGER.info(
                "Creating delegation subentry for enum %s"
                " at initial position %d%%",
                device_enum,
                initial_position,
            )
            return self.async_create_entry(
                title=device_name,
                data={
                    CONF_DEVICE_ID: device_enum,
                    "device_enum": device_enum,
                    CONF_BIDIRECTIONAL: False,
                    CONF_INITIAL_POSITION: initial_position,
                },
                unique_id=device_enum,
            )

        return self.async_show_form(
            step_id="delegate_position",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "initial_position", default=100
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=1,
                            unit_of_measurement="%",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            ),
            description_placeholders={
                "device_name": self._pending_device_name or "",
            },
            last_step=True,
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Auto-pair: trigger stick pairing and wait for a device to respond."""
        _LOGGER.debug("Pairing step input: %s", user_input)
        if user_input is None:
            _LOGGER.info("Showing pairing form")
            return self.async_show_form(step_id="pair", data_schema=vol.Schema({}))

        # Get the hub entry (parent config entry)
        hub_entry = self._get_entry()
        api = hub_entry.runtime_data

        # runtime_data is only populated while the hub entry is LOADED — guard
        # against an unloaded hub (stick unplugged, SETUP_RETRY, mid-reload) so
        # we surface a friendly retry rather than crashing with AttributeError
        # (WR-02, mirrors async_step_delegate_transmit).
        if api is None:
            _LOGGER.warning(
                "Pairing attempted while hub entry not loaded"
            )
            return self.async_show_form(
                step_id="pair",
                data_schema=vol.Schema({}),
                errors={"base": "pairing_failed"},
            )

        # Initiate pairing and wait for response (up to 10 seconds)
        try:
            pairing_result = await api.pair_device_and_wait()
        except DeviceLimitReached:
            return self.async_show_form(
                step_id="pair",
                data_schema=vol.Schema({}),
                errors={"base": "device_limit_reached"},
            )

        if pairing_result is None:
            # Pairing timeout
            return self.async_abort(reason="pairing_timeout")

        # Pairing successful! Store device_id and device_enum in context
        device_id, device_enum = pairing_result
        self._pending_device_id = device_id
        self._pending_device_enum = device_enum
        self._pending_device_name = None
        return await self.async_step_name_device()

    async def async_step_name_device(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask user to provide a friendly name for the paired device."""
        device_id = self._pending_device_id
        device_enum = self._pending_device_enum

        if user_input is None:
            # Initial call - show form
            if not device_id:
                return self.async_abort(reason="pairing_failed")

            return self.async_show_form(
                step_id="name_device",
                data_schema=vol.Schema(
                    {
                        vol.Optional("device_name"): selector.TextSelector(),
                    }
                ),
                description_placeholders={
                    "device_id": device_id,
                },
            )

        # User provided a name – begin calibration prior to creating subentry
        if not device_id or not device_enum:
            return self.async_abort(reason="pairing_failed")

        device_name = user_input.get("device_name") or f"Blind {device_id}"
        self._pending_device_name = device_name

        handler = self._get_calibration_handler()

        # Provide minimal device to handler
        handler.set_selected_device(
            {
                "id": device_id,
                "name": device_name,
                "enum": device_enum,
            }
        )
        handler.enable_subentry_creation(
            device_id=device_id,
            device_enum=device_enum,
            device_name=device_name,
        )
        _LOGGER.debug(
            "Starting calibration for paired device %s (%s) before creating subentry",
            device_id,
            device_name,
        )
        return await self._await_subentry_result(
            handler.async_step_calibration_close(None)
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Entry point: show adaptive menu based on current remote binding state.

        Shows ["calibrate", "bind_remote"] when no remote is bound, or
        ["calibrate", "change_remote", "remove_remote"] when a remote_id exists
        in the subentry data (D-01/D-02).

        async_show_menu(step_id="reconfigure_menu") REQUIRES a matching
        async_step_reconfigure_menu to exist — HA raises UnknownStep otherwise.
        """
        subentry = self._get_reconfigure_subentry()
        if not subentry.data.get("device_id"):
            return self.async_abort(reason="device_not_found")
        has_remote = bool(subentry.data.get(CONF_REMOTE_ID))
        if has_remote:
            menu_options = ["calibrate", "change_remote", "remove_remote"]
        else:
            menu_options = ["calibrate", "bind_remote"]
        # The "reconfigure_menu" title is "Configure {device_name}"; supply the
        # placeholder or the frontend formatjs renders MISSING_VALUE as the menu
        # title. subentry.title is the friendly name set at pairing time.
        return self.async_show_menu(
            step_id="reconfigure_menu",
            menu_options=menu_options,
            description_placeholders={"device_name": subentry.title or ""},
        )

    def _reset_capture_round(self) -> None:
        """Tear down any in-flight capture tasks and clear the per-round id.

        Called on every FRESH entry into the capture flow — the reconfigure
        menu and a fresh listen_first (bind / change / retry edges) — so a
        leftover listen task or a stale _first_capture_id from an abandoned
        prior round can never bleed into the next round (WR-03 / WR-04). Does
        NOT touch _is_change_mode or the error carry vars; callers own those.
        """
        if self._listen_first_task is not None:
            if not self._listen_first_task.done():
                self._listen_first_task.cancel()
            self._listen_first_task = None
        if self._listen_second_task is not None:
            if not self._listen_second_task.done():
                self._listen_second_task.cancel()
            self._listen_second_task = None
        self._first_capture_id = None

    async def async_step_reconfigure_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Re-entry point for the reconfigure menu; resets all capture state.

        Cancels any pending capture tasks and clears carry vars so an abandoned
        listen cannot leak a progress task or raw future when the user backs out
        to the menu (REVIEW finding 2 + finding 4 state hygiene, mirrors the
        async_step_menu hygiene at config_flow.py:272–275).
        """
        # Cancel-and-clear capture tasks + the per-round id (REVIEW finding 2,
        # WR-04) via the shared helper, then reset the remaining menu-scoped
        # state vars (REVIEW finding 4).
        self._reset_capture_round()
        self._is_change_mode = False
        self._listen_error_key = None
        self._listen_error_placeholders = None
        return await self.async_step_reconfigure(user_input)

    async def async_step_calibrate(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure a blind: run calibration for the single device under this subentry.

        This is the OLD async_step_reconfigure body, moved verbatim so existing
        calibration routing (timed vs bidirectional) is unchanged (CTRL-05).
        We bypass storage lookup and set the calibration handler's selected device
        directly from the subentry data to avoid device_not_found errors before
        calibration has ever run.
        """
        handler = self._get_calibration_handler()
        handler.disable_subentry_creation()

        subentry = self._get_reconfigure_subentry()
        device_id = subentry.data.get("device_id")
        device_enum = subentry.data.get("device_enum")
        if not device_id:
            return self.async_abort(reason="device_not_found")
        if not device_enum:
            # WR-13: device_enum=None would produce malformed protocol command
            # f"ss{None}9{CMD_DOWN}0000" sent to the USB stick.
            return self.async_abort(reason="device_not_found")

        # Route by motor type (CTRL-05 zero-regression requirement):
        #   - bidirectional motors → legacy event-based CalibrationFlowHandler
        #   - timed (non-bidirectional) motors → new TimedCalibrationFlowHandler
        # Use the same missing-key default as cover.py (True = bidirectional)
        # so legacy flag-less subentries are treated as bidirectional.
        is_bidirectional = bool(subentry.data.get(CONF_BIDIRECTIONAL, True))
        device_name = subentry.title or f"Blind {device_id}"

        if not is_bidirectional:
            # CAL-01 / D-01: route timed motor to the event-free timed flow.
            _LOGGER.debug(
                "Calibrate: routing timed motor %s to"
                " TimedCalibrationFlowHandler",
                device_id,
            )
            handler_tc = self._get_timed_cal_handler()
            handler_tc.set_selected_device(
                {
                    "id": device_id,
                    "name": device_name,
                    "enum": device_enum,
                }
            )
            return await self._await_subentry_result(
                handler_tc.async_step_timed_cal_precondition(user_input)
            )

        # Bidirectional motor: use event-based CalibrationFlowHandler (CTRL-05).
        handler.set_selected_device(
            {
                "id": device_id,
                "name": device_name,
                CONF_OPEN_TIME: subentry.data.get(CONF_OPEN_TIME),
                CONF_CLOSE_TIME: subentry.data.get(CONF_CLOSE_TIME),
                "enum": device_enum,
            }
        )

        return await self._await_subentry_result(
            handler.async_step_calibration_close(user_input)
        )

    # Delegate all calibration steps to the handler
    async def async_step_calibration_close(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegate to calibration handler."""
        handler = self._get_calibration_handler()
        return await self._await_subentry_result(
            handler.async_step_calibration_close(user_input)
        )

    async def async_step_calibration_open_instruction(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegate to calibration handler."""
        handler = self._get_calibration_handler()
        return await self._await_subentry_result(
            handler.async_step_calibration_open_instruction(user_input)
        )

    async def async_step_calibration_close_instruction(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegate to calibration handler."""
        handler = self._get_calibration_handler()
        return await self._await_subentry_result(
            handler.async_step_calibration_close_instruction(user_input)
        )

    async def async_step_calibration_complete(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegate to calibration handler (handler now creates entry)."""
        handler = self._get_calibration_handler()
        return await self._await_subentry_result(
            handler.async_step_calibration_complete(user_input)
        )

    # Delegate all timed-calibration steps to TimedCalibrationFlowHandler.
    # Each delegate is required so HA can route form step_ids without raising
    # UnknownStep (Pitfall 5 from RESEARCH.md).
    async def async_step_timed_cal_precondition(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegate to timed calibration handler."""
        handler = self._get_timed_cal_handler()
        return await self._await_subentry_result(
            handler.async_step_timed_cal_precondition(user_input)
        )

    async def async_step_timed_cal_close(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegate to timed calibration handler."""
        handler = self._get_timed_cal_handler()
        return await self._await_subentry_result(
            handler.async_step_timed_cal_close(user_input)
        )

    async def async_step_timed_cal_open(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegate to timed calibration handler."""
        handler = self._get_timed_cal_handler()
        return await self._await_subentry_result(
            handler.async_step_timed_cal_open(user_input)
        )

    async def async_step_timed_cal_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delegate to timed calibration handler."""
        handler = self._get_timed_cal_handler()
        return await self._await_subentry_result(
            handler.async_step_timed_cal_confirm(user_input)
        )

    # -------------------------------------------------------------------------
    # Phase 15: Learn-by-press remote binding (D-03..D-10, RMT-01/02/03)
    # -------------------------------------------------------------------------

    async def async_step_bind_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Entry for the bind path: set change-mode=False and start capture.

        Clears first-capture state so a previous aborted attempt does not
        carry over into a fresh bind (D-03).
        """
        self._is_change_mode = False
        self._first_capture_id = None
        return await self.async_step_listen_first(None)

    async def async_step_change_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Entry for the change path: set change-mode=True and start capture.

        Sets _is_change_mode so async_step_listen_confirm includes the
        current_remote_id placeholder (REVIEW finding 6 — no separate
        change_confirm step; the single listen_confirm step is reused).
        """
        self._is_change_mode = True
        self._first_capture_id = None
        return await self.async_step_listen_first(None)

    async def async_step_listen_first(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """First press: spawn raw-capture task, show progress spinner (D-03).

        Hub-not-loaded guard mirrors async_step_delegate_transmit (WR-02).
        Pitfall F: clear a stale completed task before creating a new one so
        repeated menu navigations don't accumulate tasks.
        Binding policy (D-07) runs on the first captured id BEFORE opening
        the second listening window (RESEARCH Finding 2 Cases A/B/C/D).
        """
        # A DONE task here means HA's progress-task done-callback re-invoked
        # this step (data_entry_flow re-runs async_step_listen_first(None) the
        # instant the task completes). We MUST fall through to read its result
        # below — do NOT null it here, or the `is None` branch would treat this
        # re-entry as a fresh entry and re-arm a brand-new capture window,
        # looping forever and swallowing both the captured press and the 15s
        # timeout (remote-bind-press-stuck: spinner hangs with no timeout).
        # Every SHOW_PROGRESS_DONE path already nulls _listen_first_task, so a
        # fresh retry/menu re-entry always finds None — the only time this is
        # non-None-and-done is the framework progress-poll re-entry.
        if self._listen_first_task is None:
            # Fresh entry (bind / change / a confirm- or timeout-"Try again"
            # edge that bypasses reconfigure_menu): tear down any leftover
            # second-capture task and clear a stale _first_capture_id so the
            # new round starts clean (WR-03 / WR-04).
            self._reset_capture_round()
            hub_entry = self._get_entry()
            api = hub_entry.runtime_data
            if api is None:
                _LOGGER.warning(
                    "Remote bind attempted while hub entry not loaded"
                )
                return self.async_show_form(
                    step_id="listen_timeout",
                    data_schema=vol.Schema({}),
                    errors={"base": "hub_not_loaded"},
                )
            self._listen_first_task = self.hass.async_create_task(
                api.learn_remote_raw_and_wait(
                    LEARN_REMOTE_CAPTURE_TIMEOUT
                ),
                "listen_first_task",
            )

        if not self._listen_first_task.done():
            # The "listen_first" progress step description is
            # "Press any button on the remote you want to bind to
            # {device_name}. …" — supply the placeholder or the frontend
            # formatjs renders MISSING_VALUE (same class as reconfigure_menu).
            subentry = self._get_reconfigure_subentry()
            return self.async_show_progress(
                step_id="listen_first",
                progress_action="listen_first",
                progress_task=self._listen_first_task,
                description_placeholders={
                    "device_name": subentry.title or ""
                },
            )

        captured_id: str | None = self._listen_first_task.result()
        self._listen_first_task = None

        if captured_id is None:
            # Timeout or disconnect — determine which for distinct copy (D-05).
            hub_entry = self._get_entry()
            api = hub_entry.runtime_data
            disconnected = api is not None and not api.is_connected
            self._listen_error_key = (
                "remote_capture_disconnected"
                if disconnected
                else "remote_capture_timeout"
            )
            self._listen_error_placeholders = None
            return self.async_show_progress_done(
                next_step_id="listen_timeout"
            )

        # Binding policy checks on the FIRST captured id (D-07).
        hub_entry = self._get_entry()
        api = hub_entry.runtime_data
        if api is None:
            _LOGGER.warning(
                "Remote bind attempted while hub entry not loaded"
            )
            return self.async_show_form(
                step_id="listen_timeout",
                data_schema=vol.Schema({}),
                errors={"base": "hub_not_loaded"},
            )

        # Case A: captured id is an enrolled motor (not a remote).
        if api.is_registered_motor(captured_id):
            _LOGGER.warning(
                "Remote bind rejected: captured id %s is a registered motor",
                captured_id,
            )
            self._listen_error_key = "remote_is_motor"
            self._listen_error_placeholders = None
            return self.async_show_progress_done(
                next_step_id="listen_timeout"
            )

        # Case B: captured id is already bound to a different motor.
        other_motor_id = api.bound_motor_for(captured_id)
        if other_motor_id is not None:
            subentry = self._get_reconfigure_subentry()
            this_motor_id = subentry.data.get("device_id")
            if other_motor_id != this_motor_id:
                # Find the other motor's display name for the placeholder.
                entry = self._get_entry()
                other_name: str = other_motor_id  # fallback to raw id
                for sub in entry.subentries.values():
                    if sub.data.get("device_id") == other_motor_id:
                        other_name = sub.title or other_motor_id
                        break
                _LOGGER.warning(
                    "Remote bind rejected: captured id %s is already"
                    " bound to motor %s",
                    captured_id,
                    other_motor_id,
                )
                self._listen_error_key = "remote_already_bound"
                self._listen_error_placeholders = {
                    "other_motor_name": other_name
                }
                return self.async_show_progress_done(
                    next_step_id="listen_timeout"
                )
            # Case C: re-press of THIS motor's current remote during change
            # (D-10) — allow through; double-press verify will still confirm.

        # Case D (clean unknown) or Case C (re-press own remote): proceed.
        _LOGGER.info(
            "Remote bind: first press captured id=%s, awaiting second press",
            captured_id,
        )
        self._first_capture_id = captured_id
        return self.async_show_progress_done(next_step_id="listen_second")

    async def async_step_listen_second(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Second press: spawn new capture task, verify it matches the first (D-06).

        The second listening window uses a FRESH future (a new asyncio.Task
        wrapping a new learn_remote_raw_and_wait() call). Phase 11's
        incrementor-dedup collapses a single physical press's repeated RF
        frames into one logical event, so by the time this step is entered the
        first press's future is already resolved and consumed. The new future
        instance cannot be resolved by any leftover frames of the first press
        because learn_remote_raw_and_wait() sets a NEW _learn_remote_raw_future
        on entry — a different object than the one the first task used. No extra
        sleep/timer guard is needed; the dedup + fresh-future boundary is the
        chosen mitigation (REVIEW finding 8).
        """
        # A DONE task here is HA's progress-task done-callback re-entry (see
        # async_step_listen_first): fall through to read its result below. Do
        # NOT null it here or the `is None` branch re-arms a fresh capture and
        # loops forever (remote-bind-press-stuck).
        if self._listen_second_task is None:
            hub_entry = self._get_entry()
            api = hub_entry.runtime_data
            if api is None:
                _LOGGER.warning(
                    "Remote bind (second press) attempted while hub"
                    " entry not loaded"
                )
                return self.async_show_form(
                    step_id="listen_timeout",
                    data_schema=vol.Schema({}),
                    errors={"base": "hub_not_loaded"},
                )
            self._listen_second_task = self.hass.async_create_task(
                api.learn_remote_raw_and_wait(
                    LEARN_REMOTE_CAPTURE_TIMEOUT
                ),
                "listen_second_task",
            )

        if not self._listen_second_task.done():
            return self.async_show_progress(
                step_id="listen_second",
                progress_action="listen_second",
                progress_task=self._listen_second_task,
            )

        captured_id: str | None = self._listen_second_task.result()
        self._listen_second_task = None

        if captured_id is None:
            # Timeout or disconnect on the second press.
            hub_entry = self._get_entry()
            api = hub_entry.runtime_data
            disconnected = api is not None and not api.is_connected
            self._listen_error_key = (
                "remote_capture_disconnected"
                if disconnected
                else "remote_capture_timeout"
            )
            self._listen_error_placeholders = None
            return self.async_show_progress_done(
                next_step_id="listen_timeout"
            )

        if captured_id != self._first_capture_id:
            # Mismatch: different remote pressed (D-06).
            _LOGGER.warning(
                "Remote bind double-press mismatch: first=%s second=%s",
                self._first_capture_id,
                captured_id,
            )
            self._listen_error_key = "remote_press_mismatch"
            self._listen_error_placeholders = None
            self._first_capture_id = None
            return self.async_show_progress_done(
                next_step_id="listen_timeout"
            )

        # Match — advance to confirm (shared step for bind AND change, D-10).
        _LOGGER.info(
            "Remote bind: second press matched id=%s, advancing to confirm",
            captured_id,
        )
        return self.async_show_progress_done(next_step_id="listen_confirm")

    async def async_step_listen_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Retry-in-place form for timeout / disconnect / policy rejection.

        On the user_input-is-None path (HA routing from async_show_progress_done),
        reads the _listen_error_key carry var set by listen_first/listen_second/
        policy and renders the correct one of the four error keys (REVIEW finding 4).
        On submit (user clicked "Try again") clears the carry vars and re-enters
        listen_first. No binding is written here (D-05).
        """
        if user_input is None:
            return self.async_show_form(
                step_id="listen_timeout",
                data_schema=vol.Schema({}),
                errors={
                    "base": self._listen_error_key or "remote_capture_timeout"
                },
                description_placeholders=self._listen_error_placeholders,
            )
        # User submitted "Try again" — clear carry vars and restart.
        self._listen_error_key = None
        self._listen_error_placeholders = None
        self._first_capture_id = None
        return await self.async_step_listen_first(None)

    async def async_step_listen_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm-before-persist: two-option menu (Confirm vs Retry) (D-08).

        Reused for both the bind path (_is_change_mode=False) and the change
        path (_is_change_mode=True — REVIEW finding 6: NO separate change_confirm
        step). When change mode is active, the current_remote_id placeholder is
        supplied so the translation layer can note the replacement.

        async_show_menu with menu_options ["listen_confirm_apply", "listen_first"]
        routes: Confirm -> async_step_listen_confirm_apply (persist),
                Retry   -> async_step_listen_first (re-enter capture).
        """
        subentry = self._get_reconfigure_subentry()
        # Strings reference {device_name}/{remote_id}; keys must match by exact
        # name or the frontend formatjs renders MISSING_VALUE.
        ph: dict[str, str] = {
            "device_name": subentry.title or "",
            "remote_id": self._first_capture_id or "",
        }
        if self._is_change_mode:
            ph["current_remote_id"] = (
                subentry.data.get(CONF_REMOTE_ID) or ""
            )
        return self.async_show_menu(
            step_id="listen_confirm",
            menu_options=["listen_confirm_apply", "listen_first"],
            description_placeholders=ph,
        )

    async def async_step_listen_confirm_apply(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Persist remote_id on Confirm (D-08/RMT-02).

        Uses the safe 3-call pattern: async_update_subentry + async_schedule_reload
        + async_abort. MUST NOT call async_update_reload_and_abort — it raises
        ValueError because _on_entry_updated is registered (__init__.py:157).
        """
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        new_data = dict(subentry.data) | {
            CONF_REMOTE_ID: self._first_capture_id
        }
        _LOGGER.info(
            "Remote bind: persisting remote_id=%s for motor %s",
            self._first_capture_id,
            subentry.data.get("device_id"),
        )
        self.hass.config_entries.async_update_subentry(
            entry, subentry, data=new_data
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason="reconfigure_successful")

    async def async_step_remove_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Entry for the remove path: delegate to the confirm menu (D-09)."""
        return await self.async_step_remove_confirm(None)

    async def async_step_remove_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm-before-remove: two-option menu (Remove vs Cancel) (D-09).

        async_show_menu with menu_options ["remove_confirm_apply", "reconfigure_menu"]
        routes: Remove  -> async_step_remove_confirm_apply (delete key + reload),
                Cancel  -> async_step_reconfigure_menu (adaptive menu, state reset).
        """
        subentry = self._get_reconfigure_subentry()
        return self.async_show_menu(
            step_id="remove_confirm",
            menu_options=["remove_confirm_apply", "reconfigure_menu"],
            description_placeholders={"device_name": subentry.title or ""},
        )

    async def async_step_remove_confirm_apply(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Delete remote_id key on Remove (D-09/RMT-03).

        Builds new_data as a dict WITHOUT the CONF_REMOTE_ID key (subentry.data
        is a MappingProxyType — must convert with dict() / comprehension before
        mutation). Uses the same 3-call persist pattern as listen_confirm_apply.
        """
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        new_data = {
            k: v
            for k, v in subentry.data.items()
            if k != CONF_REMOTE_ID
        }
        _LOGGER.info(
            "Remote bind: removing remote_id from motor %s",
            subentry.data.get("device_id"),
        )
        self.hass.config_entries.async_update_subentry(
            entry, subentry, data=new_data
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason="reconfigure_successful")
