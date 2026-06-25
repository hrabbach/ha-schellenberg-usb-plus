"""Test the __init__.py module of Schellenberg USB integration."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    CONF_SERIAL_PORT,
    DOMAIN,
    PLATFORMS,
)


@pytest.fixture
def mock_config_entry(hass: HomeAssistant) -> ConfigEntry:
    """Create a mock config entry."""
    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_id",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    # Manually add the entry to the internal dict to avoid async_add
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


@pytest.mark.asyncio
async def test_async_setup_entry_basic(
    hass: HomeAssistant, mock_config_entry: ConfigEntry
) -> None:
    """Test basic async_setup_entry functionality."""
    from custom_components.schellenberg_usb import async_setup_entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ) as mock_forward,
    ):
        result = await async_setup_entry(hass, mock_config_entry)

        assert result is True
        mock_forward.assert_called_once_with(mock_config_entry, PLATFORMS)
        # Check that runtime_data was set
        assert mock_config_entry.runtime_data is not None
        assert isinstance(mock_config_entry.runtime_data, SchellenbergUsbApi)


@pytest.mark.asyncio
async def test_async_setup_entry_creates_hub_device(
    hass: HomeAssistant, mock_config_entry: ConfigEntry
) -> None:
    """Test that async_setup_entry creates a hub device."""
    from custom_components.schellenberg_usb import async_setup_entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        result = await async_setup_entry(hass, mock_config_entry)

        assert result is True

        # Check that a hub device was created
        device_registry = dr.async_get(hass)
        hub_device = device_registry.async_get_device(
            identifiers={(DOMAIN, mock_config_entry.entry_id)}
        )
        assert hub_device is not None
        assert hub_device.name == "Schellenberg USB Stick"


@pytest.mark.asyncio
async def test_setup_entry_pushes_initial_ignore_unknown(
    hass: HomeAssistant, mock_config_entry: ConfigEntry
) -> None:
    """async_setup_entry sets api.ignore_unknown from entry.options (False when unset)."""
    from custom_components.schellenberg_usb import async_setup_entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True
    assert mock_config_entry.runtime_data.ignore_unknown is False


@pytest.mark.asyncio
async def test_on_entry_updated_toggle_no_reload(
    hass: HomeAssistant, mock_config_entry: ConfigEntry
) -> None:
    """Update listener live-applies ignore_unknown without triggering reload."""
    from custom_components.schellenberg_usb import _SETUP_CALLBACKS, async_setup_entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        await async_setup_entry(hass, mock_config_entry)

    api: SchellenbergUsbApi = mock_config_entry.runtime_data
    assert api.ignore_unknown is False  # default before toggle

    with patch.object(
        hass.config_entries, "async_reload", new_callable=AsyncMock
    ) as mock_reload:
        # Simulate user saving options with toggle on
        hass.config_entries.async_update_entry(
            mock_config_entry, options={"ignore_unknown": True}
        )
        await hass.async_block_till_done()

    # (a) live-apply happened
    assert api.ignore_unknown is True
    # (b) async_reload was NOT called for the toggle-only save
    mock_reload.assert_not_called()
    # (c) subentry tracking is intact — reload branch was NOT entered
    known = _SETUP_CALLBACKS[mock_config_entry.entry_id]["subentry_ids"]
    assert known == set(mock_config_entry.subentries.keys())


@pytest.mark.asyncio
async def test_async_unload_entry(
    hass: HomeAssistant, mock_config_entry: ConfigEntry
) -> None:
    """Test async_unload_entry disconnects and cleans up resources."""
    from custom_components.schellenberg_usb import async_setup_entry, async_unload_entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
        patch.object(
            hass.config_entries, "async_unload_platforms", new_callable=AsyncMock
        ) as mock_unload,
    ):
        # First setup the entry
        await async_setup_entry(hass, mock_config_entry)
        mock_unload.return_value = True

        # Now unload it
        with patch.object(
            SchellenbergUsbApi, "disconnect", new_callable=AsyncMock
        ) as mock_disconnect:
            result = await async_unload_entry(hass, mock_config_entry)

            assert result is True
            mock_unload.assert_called_once_with(mock_config_entry, PLATFORMS)
            mock_disconnect.assert_called_once()
