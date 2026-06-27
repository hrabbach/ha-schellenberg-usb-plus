"""Config flow for Schellenberg USB integration."""

from __future__ import annotations

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
    CONF_SERIAL_PORT,
    DOMAIN,
    SUBENTRY_TYPE_BLIND,
)
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
        """Show menu: Pair automatically or Add manually.

        async_show_menu(step_id="menu") REQUIRES a matching async_step_menu
        method to exist — HA validates that a shown step_id resolves to a handler
        (it raises UnknownStep otherwise). Selecting an option routes to
        async_step_{option}: 'pair' or 'manual_add'.
        """
        return self.async_show_menu(
            step_id="menu",
            menu_options=["pair", "manual_add"],
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

        # Initiate pairing and wait for response (up to 10 seconds)
        pairing_result = await api.pair_device_and_wait()

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
        """Configure a blind: run calibration for the single device under this subentry.

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
                "Reconfigure: routing timed motor %s to TimedCalibrationFlowHandler",
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
