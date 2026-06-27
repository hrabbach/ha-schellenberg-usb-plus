"""Options flow for Schellenberg USB hub.

Hub options allow changing the USB serial port path. Calibration is handled
exclusively during blind subentry pairing and not exposed here.
"""

from __future__ import annotations

import logging
from typing import Any

import serial
import voluptuous as vol

from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_IGNORE_UNKNOWN, CONF_SERIAL_PORT

_LOGGER = logging.getLogger(__name__)


class SchellenbergOptionsFlowHandler(OptionsFlow):
    """Handle hub options (edit serial port)."""

    def __init__(self) -> None:
        """Initialize hub options flow state."""
        self._errors: dict[str, str] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the USB serial port."""
        self._errors = {}
        current_port = self.config_entry.data.get(CONF_SERIAL_PORT, "/dev/ttyUSB0")
        current_ignore = self.config_entry.options.get(CONF_IGNORE_UNKNOWN, False)
        if user_input is not None:
            new_port = user_input[CONF_SERIAL_PORT]
            new_ignore = user_input.get(CONF_IGNORE_UNKNOWN, False)
            if new_port != current_port:
                try:
                    # Run blocking serial open in the executor to avoid blocking
                    # the HA event loop (WR-07 / CR-02 pattern).
                    def _open_serial(p: str) -> None:
                        conn = serial.Serial(p)
                        conn.close()

                    await self.hass.async_add_executor_job(_open_serial, new_port)
                except serial.SerialException:
                    _LOGGER.error(
                        "Failed to open serial port %s during options save", new_port
                    )
                    self._errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    # HA options flow must surface 'unknown' to the user rather than
                    # crashing the flow; broad catch is intentional (RESEARCH Pitfall 7).
                    _LOGGER.exception("Unexpected error validating port %s", new_port)
                    self._errors["base"] = "unknown"
                else:
                    # Update entry data and reload because the port changed.
                    updated = {**self.config_entry.data, CONF_SERIAL_PORT: new_port}
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=updated
                    )
                    # Persist the toggle option BEFORE scheduling the reload so the
                    # reloaded entry already sees the new value (review finding #1 —
                    # never schedule the reload while options lack the toggle).
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        options={CONF_IGNORE_UNKNOWN: new_ignore},
                    )
                    self.hass.config_entries.async_schedule_reload(
                        self.config_entry.entry_id
                    )
                    return self.async_create_entry(
                        title="", data={CONF_IGNORE_UNKNOWN: new_ignore}
                    )
            else:
                # Port unchanged (toggle changed or nothing changed): persist the
                # toggle to entry.options and NEVER reload (SC#1 — no stick blip).
                return self.async_create_entry(
                    title="", data={CONF_IGNORE_UNKNOWN: new_ignore}
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SERIAL_PORT, default=current_port
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_IGNORE_UNKNOWN, default=current_ignore
                    ): selector.BooleanSelector(),
                }
            ),
            errors=self._errors,
        )

    @callback
    def async_get_options_flow(self):
        """Return self (options flow factory compatibility)."""
        return self
