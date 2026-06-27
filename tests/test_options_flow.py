"""Tests for the hub options flow — ignore_unknown toggle."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    CONF_SERIAL_PORT,
    DOMAIN,
)


@pytest.fixture
def mock_hub_entry(hass: HomeAssistant) -> ConfigEntry:
    """Create a mock hub config entry with an existing serial port."""
    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_options_flow_entry",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


@pytest.mark.asyncio
async def test_toggle_saves_to_options(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Toggle-only save writes ignore_unknown to entry.options; no reload."""
    from custom_components.schellenberg_usb import async_setup_entry
    from custom_components.schellenberg_usb.options_flow import (
        SchellenbergOptionsFlowHandler,
    )

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        await async_setup_entry(hass, mock_hub_entry)

    handler = SchellenbergOptionsFlowHandler()
    handler.hass = hass
    # Bind the flow to the entry the way HA's flow manager does: `handler`
    # holds the entry_id, which OptionsFlow.config_entry resolves via
    # async_get_known_entry. Poking a private `_config_entry` does NOT work —
    # the property reads `self.handler`.
    handler.handler = mock_hub_entry.entry_id

    with patch.object(hass.config_entries, "async_schedule_reload") as mock_reload:
        result = await handler.async_step_init(
            user_input={
                CONF_SERIAL_PORT: "/dev/ttyUSB0",  # unchanged
                "ignore_unknown": True,
            }
        )

    assert result["type"] == "create_entry", (
        f"Expected create_entry but got {result['type']!r}"
    )
    assert result["data"].get("ignore_unknown") is True, (
        "Expected ignore_unknown=True in saved options"
    )
    mock_reload.assert_not_called()


@pytest.mark.asyncio
async def test_options_form_shows_toggle_default_off(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Init form contains ignore_unknown field defaulting to False when unset."""
    from custom_components.schellenberg_usb import async_setup_entry
    from custom_components.schellenberg_usb.options_flow import (
        SchellenbergOptionsFlowHandler,
    )

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        await async_setup_entry(hass, mock_hub_entry)

    handler = SchellenbergOptionsFlowHandler()
    handler.hass = hass
    handler.handler = mock_hub_entry.entry_id

    # Show the form (no user_input)
    result = await handler.async_step_init(user_input=None)

    assert result["type"] == "form"
    schema = result["data_schema"]
    assert schema is not None
    schema_keys = {
        (k.schema if hasattr(k, "schema") else k): v for k, v in schema.schema.items()
    }
    assert "ignore_unknown" in schema_keys, (
        "Expected ignore_unknown field in the options form schema"
    )
    # The default value should be False (not yet saved)
    for k in schema.schema:
        key_name = k.schema if hasattr(k, "schema") else k
        if key_name == "ignore_unknown":
            assert k.default() is False, (
                f"Expected ignore_unknown default=False, got {k.default()!r}"
            )
