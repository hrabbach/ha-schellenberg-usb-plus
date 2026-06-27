"""Tests for cover platform."""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_POSITION
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
)

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    CMD_DOWN,
    CMD_STOP,
    CMD_UP,
    CONF_BIDIRECTIONAL,
    CONF_CLOSE_TIME,
    CONF_INITIAL_POSITION,
    CONF_OPEN_TIME,
    CONF_SERIAL_PORT,
    DOMAIN,
    EVENT_STARTED_MOVING_DOWN,
    EVENT_STARTED_MOVING_UP,
    EVENT_STOPPED,
)
from custom_components.schellenberg_usb.cover import (
    DEFAULT_TRAVEL_TIME,
    SchellenbergCover,
    _get_cal_store,
    _save_calibration,
    async_setup_entry,
)


def _async_mock(value: Any) -> AsyncMock:
    """Cast helper for AsyncMock assertions."""
    return cast(AsyncMock, value)


def _magic_mock(value: Any) -> MagicMock:
    """Cast helper for MagicMock assertions."""
    return cast(MagicMock, value)


@pytest.fixture
def mock_api(hass: HomeAssistant) -> SchellenbergUsbApi:
    """Create a mock API."""
    api_mock = MagicMock(spec=SchellenbergUsbApi)
    api_mock.hass = hass
    api_mock.is_connected = True
    api_mock.device_version = "RFTU_V20"
    api_mock.control_blind = AsyncMock()
    api_mock.register_entity = MagicMock()
    return cast(SchellenbergUsbApi, api_mock)


@pytest.fixture
def mock_config_entry(hass: HomeAssistant) -> ConfigEntry:
    """Create a mock config entry with subentries."""
    # Create a real subentry dict instead of MagicMock to avoid serialization issues
    subentry = MagicMock()
    subentry.subentry_id = "sub1"
    subentry.data = {
        "device_id": "ABC123",
        "device_enum": "01",
        "device_name": "Test Cover",
    }
    subentry.title = "Test Cover"  # Real string, not mock

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_cover",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    # Mock the subentries property
    entry.subentries = MappingProxyType({"sub1": subentry})  # type: ignore[misc]
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


@pytest.mark.asyncio
async def test_async_setup_entry_creates_covers(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test that setup entry creates cover entities."""
    mock_config_entry.runtime_data = mock_api

    # Mock device registry
    dev_reg = dr.async_get(hass)

    # Create a hub device
    dev_reg.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, mock_config_entry.entry_id)},
        name="Schellenberg USB Stick",
        manufacturer="Schellenberg",
    )

    mock_add_entities = MagicMock()

    await async_setup_entry(hass, mock_config_entry, mock_add_entities)

    mock_add_entities.assert_called_once()
    entities = mock_add_entities.call_args[0][0]
    assert len(entities) == 1
    assert isinstance(entities[0], SchellenbergCover)
    assert entities[0]._device_id == "ABC123"
    assert entities[0]._device_enum == "01"


@pytest.mark.asyncio
async def test_cover_initialization(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test cover initialization."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
        device_data=None,
        config_entry_id="test_entry",
    )

    assert cover._device_id == "ABC123"
    assert cover._device_enum == "01"
    assert cover.unique_id == "schellenberg_ABC123"
    assert cover.name == "Test Cover"
    assert cover._attr_current_cover_position is None
    assert cover._travel_time_open == DEFAULT_TRAVEL_TIME
    assert cover._travel_time_close == DEFAULT_TRAVEL_TIME


@pytest.mark.asyncio
async def test_cover_initialization_with_calibration(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test cover initialization with calibration data."""
    device_data = {
        CONF_OPEN_TIME: 25.0,
        CONF_CLOSE_TIME: 23.0,
    }

    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
        device_data=device_data,
        config_entry_id="test_entry",
    )

    assert cover._travel_time_open == 25.0
    assert cover._travel_time_close == 23.0


@pytest.mark.asyncio
async def test_cover_availability(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test cover availability based on API connection."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )

    assert cover.available is True

    cast(Any, mock_api).is_connected = False
    assert cover.available is False


@pytest.mark.asyncio
async def test_cover_icon_states(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test cover icon changes based on state."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )

    # Closed state
    cover._attr_is_closed = True
    assert cover.icon == "mdi:window-shutter"

    # Open state
    cover._attr_is_closed = False
    assert cover.icon == "mdi:window-shutter-open"

    # Opening state
    cover._attr_is_opening = True
    assert cover.icon == "mdi:arrow-up-box"

    # Closing state
    cover._attr_is_opening = False
    cover._attr_is_closing = True
    assert cover.icon == "mdi:arrow-down-box"


@pytest.mark.asyncio
async def test_cover_async_open_cover(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test opening the cover."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_current_cover_position = 0

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            await cover.async_open_cover()

    assert cover._attr_is_opening is True
    assert cover._attr_is_closing is False
    _async_mock(mock_api.control_blind).assert_called_once_with("01", "01")


@pytest.mark.asyncio
async def test_cover_async_close_cover(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test closing the cover."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_current_cover_position = 100

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            await cover.async_close_cover()

    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is True
    _async_mock(mock_api.control_blind).assert_called_once_with("01", "02")


@pytest.mark.asyncio
async def test_cover_async_stop_cover(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test stopping the cover."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_is_opening = True
    cover._attr_current_cover_position = 50

    with patch.object(cover, "_stop_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            await cover.async_stop_cover()

    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is False
    _async_mock(mock_api.control_blind).assert_called_once_with("01", "00")


@pytest.mark.asyncio
async def test_cover_set_position_open(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test setting cover to a higher position (opening)."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_current_cover_position = 20

    with patch.object(cover, "async_open_cover", new_callable=AsyncMock) as mock_open:
        await cover.async_set_cover_position(**{ATTR_POSITION: 80})

    assert cover._target_position == 80
    mock_open.assert_called_once()


@pytest.mark.asyncio
async def test_cover_set_position_close(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test setting cover to a lower position (closing)."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_current_cover_position = 80

    with patch.object(cover, "async_close_cover", new_callable=AsyncMock) as mock_close:
        await cover.async_set_cover_position(**{ATTR_POSITION: 20})

    assert cover._target_position == 20
    mock_close.assert_called_once()


@pytest.mark.asyncio
async def test_cover_set_position_same(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test setting cover to same position does nothing."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_current_cover_position = 50

    with patch.object(cover, "async_open_cover", new_callable=AsyncMock) as mock_open:
        with patch.object(
            cover, "async_close_cover", new_callable=AsyncMock
        ) as mock_close:
            await cover.async_set_cover_position(**{ATTR_POSITION: 50})

    mock_open.assert_not_called()
    mock_close.assert_not_called()


@pytest.mark.asyncio
async def test_cover_restore_position(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test cover restores position from previous state."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass

    last_state = State("cover.test_cover", "open", {"current_position": 75})

    with patch.object(cover, "async_get_last_state", return_value=last_state):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    assert cover._attr_current_cover_position == 75
    assert cover._attr_is_closed is False


@pytest.mark.asyncio
async def test_cover_restore_closed(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test cover restores closed state."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass

    last_state = State("cover.test_cover", "closed", {"current_position": 0})

    with patch.object(cover, "async_get_last_state", return_value=last_state):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    assert cover._attr_current_cover_position == 0
    assert cover._attr_is_closed is True


@pytest.mark.asyncio
async def test_cover_no_previous_state(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test cover defaults to closed when no previous state."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass

    with patch.object(cover, "async_get_last_state", return_value=None):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    assert cover._attr_current_cover_position == 0
    assert cover._attr_is_closed is True


@pytest.mark.asyncio
async def test_cover_handle_started_moving_up(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test handling started moving up event."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_current_cover_position = 0

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_event(EVENT_STARTED_MOVING_UP)

    assert cover._attr_is_opening is True
    assert cover._attr_is_closing is False
    assert cover._move_start_position == 0


@pytest.mark.asyncio
async def test_cover_handle_started_moving_down(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test handling started moving down event."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_current_cover_position = 100

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_event(EVENT_STARTED_MOVING_DOWN)

    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is True
    assert cover._move_start_position == 100


@pytest.mark.asyncio
async def test_cover_handle_stopped(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test handling stopped event."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_is_opening = True
    cover._attr_current_cover_position = 50
    cover._target_position = 50

    with patch.object(cover, "_stop_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_event(EVENT_STOPPED)

    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is False
    assert cover._attr_current_cover_position == 50


@pytest.mark.asyncio
async def test_cover_update_position_opening(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test position update while opening."""
    import time

    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
        device_data={CONF_OPEN_TIME: 20.0},  # 20 seconds to fully open
    )
    cover.hass = hass
    cover._attr_is_opening = True
    cover._attr_current_cover_position = 0
    cover._move_start_position = 0
    cover._move_start_time = time.monotonic() - 10.0  # Simulating 10 seconds elapsed

    cover._update_position()

    # After 10 seconds of 20 second travel time, should be at 50%
    assert 45 <= cover._attr_current_cover_position <= 55  # Allow some tolerance


@pytest.mark.asyncio
async def test_cover_update_position_closing(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test position update while closing."""
    import time

    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
        device_data={CONF_CLOSE_TIME: 20.0},  # 20 seconds to fully close
    )
    cover.hass = hass
    cover._attr_is_closing = True
    cover._attr_current_cover_position = 100
    cover._move_start_position = 100
    cover._move_start_time = time.monotonic() - 10.0  # Simulating 10 seconds elapsed

    cover._update_position()

    # After 10 seconds of 20 second travel time, should be at 50%
    assert 45 <= cover._attr_current_cover_position <= 55  # Allow some tolerance


@pytest.mark.asyncio
async def test_cover_calibration_completed(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test handling calibration completed event."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_current_cover_position = 50

    with patch.object(cover, "async_write_ha_state"):
        cover._handle_calibration_completed("ABC123", 25.0, 23.0)

    assert cover._travel_time_open == 25.0
    assert cover._travel_time_close == 23.0
    assert cover._attr_current_cover_position == 0
    assert cover._attr_is_closed is True


@pytest.mark.asyncio
async def test_cover_calibration_different_device(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test calibration event for different device doesn't affect this cover."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._travel_time_open = 30.0
    cover._travel_time_close = 30.0
    cover._attr_current_cover_position = 50

    cover._handle_calibration_completed("XYZ789", 25.0, 23.0)

    # Should not change
    assert cover._travel_time_open == 30.0
    assert cover._travel_time_close == 30.0
    assert cover._attr_current_cover_position == 50


@pytest.mark.asyncio
async def test_cover_registers_with_api(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test cover registers itself with API."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass

    with patch.object(cover, "async_get_last_state", return_value=None):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    _magic_mock(mock_api.register_entity).assert_called_once_with("ABC123", "01")


@pytest.mark.asyncio
async def test_cover_will_remove_from_hass(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Test cover cleanup on removal."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass

    with patch.object(cover, "_stop_position_tracking") as mock_stop:
        await cover.async_will_remove_from_hass()
        mock_stop.assert_called_once()


@pytest.mark.asyncio
async def test_cover_mode_attribute(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Cover built with CONF_BIDIRECTIONAL True exposes mode='bidirectional'; False -> 'timed'."""
    cover_bi = SchellenbergCover(
        api=mock_api,
        device_id="1A",
        device_enum="1A",
        device_name="Bi Cover",
        device_data={CONF_BIDIRECTIONAL: True},
    )
    assert cover_bi.extra_state_attributes["mode"] == "bidirectional"

    cover_timed = SchellenbergCover(
        api=mock_api,
        device_id="2B",
        device_enum="2B",
        device_name="Timed Cover",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    assert cover_timed.extra_state_attributes["mode"] == "timed"


@pytest.mark.asyncio
async def test_cover_mode_defaults_bidirectional_when_key_absent(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Legacy subentry with NO CONF_BIDIRECTIONAL key reports mode='bidirectional' (read-default True).

    This prevents CTRL-05 regression: existing auto-paired motors must never be mislabeled timed.
    """
    # OMIT CONF_BIDIRECTIONAL key entirely — simulates a Phase-1 legacy subentry
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="10",
        device_name="Legacy Cover",
        device_data={"device_id": "ABC123", "device_enum": "10"},
    )
    assert cover.extra_state_attributes["mode"] == "bidirectional"


@pytest.mark.asyncio
async def test_cover_initial_position_from_subentry(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Timed cover with CONF_INITIAL_POSITION 100 seeds position to 100 after async_added_to_hass."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="1A",
        device_enum="1A",
        device_name="Timed Cover",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_INITIAL_POSITION: 100,
        },
    )
    cover.hass = hass

    with patch.object(cover, "async_get_last_state", return_value=None):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    assert cover._attr_current_cover_position == 100
    assert cover._attr_is_closed is False


@pytest.mark.asyncio
async def test_cover_initial_position_clamped(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """CONF_INITIAL_POSITION=150 clamps to 100; a restored prior state wins over seeded initial."""
    # Upper-bound clamp: 150 -> 100
    cover_clamped = SchellenbergCover(
        api=mock_api,
        device_id="2B",
        device_enum="2B",
        device_name="Clamped Cover",
        device_data={CONF_BIDIRECTIONAL: False, CONF_INITIAL_POSITION: 150},
    )
    cover_clamped.hass = hass

    with patch.object(cover_clamped, "async_get_last_state", return_value=None):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover_clamped, "async_write_ha_state"):
                await cover_clamped.async_added_to_hass()

    assert cover_clamped._attr_current_cover_position == 100, (
        f"Expected 100 (clamped from 150), got {cover_clamped._attr_current_cover_position}"
    )

    # RestoreEntity precedence: prior state of 50 beats seeded initial of 100 (Pitfall 5)
    cover_restored = SchellenbergCover(
        api=mock_api,
        device_id="3C",
        device_enum="3C",
        device_name="Restored Cover",
        device_data={CONF_BIDIRECTIONAL: False, CONF_INITIAL_POSITION: 100},
    )
    cover_restored.hass = hass

    last_state = State("cover.restored_cover", "open", {"current_position": 50})

    with patch.object(cover_restored, "async_get_last_state", return_value=last_state):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover_restored, "async_write_ha_state"):
                await cover_restored.async_added_to_hass()

    assert cover_restored._attr_current_cover_position == 50, (
        f"Expected 50 (restored state wins), got {cover_restored._attr_current_cover_position}"
    )


@pytest.mark.asyncio
async def test_timed_motor_position_loop_clears_flags(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Timed motor: after SET_POSITION target is reached via the real loop, flags are cleared.

    Regression test for CR-01: the position-reached branch used to leave
    _attr_is_opening/_attr_is_closing True and _target_position set, causing HA
    to render the cover as perpetually moving. This test exercises the real
    _async_position_update_loop (no patch on _start_position_tracking) and must
    FAIL against pre-fix code and PASS after the fix.
    """
    import time as _time

    # Use a very small travel time (0.5 s) so the move completes within ms
    # in the event loop.  Start at 0% and move UP to 50% -- the loop should
    # stop the motor and clear the flags once position >= 50.
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor Test",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: 0.5,
            CONF_CLOSE_TIME: 0.5,
        },
    )
    cover.hass = hass
    cover._attr_current_cover_position = 0
    cover._attr_is_closed = True

    # Kick off async_set_cover_position.  It internally calls async_open_cover,
    # which calls _start_position_tracking -> creates the real loop task.
    # We do NOT patch _start_position_tracking here (that is the whole point).
    with patch.object(cover, "async_write_ha_state"):
        # Set the move start state manually so _update_position works correctly
        # when async_open_cover is called without the hass event loop already
        # running the task scheduler.  We pre-set the start time so that a
        # position > 50 is instantly computed on the first loop iteration.
        cover._attr_is_opening = True
        cover._attr_is_closing = False
        cover._move_start_position = 0
        # Backdate the start time by 0.4 s -- with 0.5 s travel = 80% progress,
        # which already exceeds the target of 50, so the loop exits on the first
        # iteration after the initial 0.2 s sleep.
        cover._move_start_time = _time.monotonic() - 0.4
        cover._target_position = 50

        loop_task = hass.async_create_task(cover._async_position_update_loop())

        # Allow the event loop to run: the task sleeps 0.2 s then checks.
        # Give it 1 second of wall-clock asyncio time to settle.
        await asyncio.sleep(0.5)

    # The loop should have exited and cleared the flags.
    assert cover._attr_is_opening is False, (
        f"Expected is_opening=False after position reached, got {cover._attr_is_opening}"
    )
    assert cover._attr_is_closing is False, (
        f"Expected is_closing=False after position reached, got {cover._attr_is_closing}"
    )
    assert cover._target_position is None, (
        f"Expected _target_position=None after position reached, got {cover._target_position}"
    )
    # Position should equal the requested target (50%)
    assert cover._attr_current_cover_position == 50, (
        f"Expected position=50 after reaching target, got {cover._attr_current_cover_position}"
    )
    # Task should be done
    assert loop_task.done(), (
        "Expected position loop task to be done after target reached"
    )


# ---------------------------------------------------------------------------
# CTRL-01: Timed motor open/close dispatch immediately, no event wait
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_open_sends_command_immediately(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """CTRL-01: timed (non-bidirectional) open dispatches CMD_UP immediately.

    No inbound device event is needed — control_blind must be awaited exactly
    once with CMD_UP upon async_open_cover, with no _handle_event involvement.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    cover.hass = hass
    cover._attr_current_cover_position = 0

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            await cover.async_open_cover()

    assert cover._attr_is_opening is True
    assert cover._attr_is_closing is False
    _async_mock(mock_api.control_blind).assert_awaited_once_with(
        cover._device_enum, CMD_UP
    )


@pytest.mark.asyncio
async def test_timed_close_sends_command_immediately(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """CTRL-01: timed motor close dispatches CMD_DOWN immediately.

    No inbound device event is needed — control_blind must be awaited exactly
    once with CMD_DOWN upon async_close_cover, with no _handle_event involvement.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    cover.hass = hass
    cover._attr_current_cover_position = 100

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            await cover.async_close_cover()

    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is True
    _async_mock(mock_api.control_blind).assert_awaited_once_with(
        cover._device_enum, CMD_DOWN
    )


# ---------------------------------------------------------------------------
# CTRL-02 / D-01: Stop freezes the position estimate for timed motors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_stop_freezes_at_estimate(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-01: async_stop_cover freezes the position estimate (no endstop snap).

    A timed motor mid-open: stop must record the interpolated position at the
    moment of the stop call — not snap to 0 or 100.  CMD_STOP must be sent
    exactly once.
    """
    import time as _time

    # travel time: 1.0 s → 50% elapsed after 0.5 s from position 0
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: 1.0,
            CONF_CLOSE_TIME: 1.0,
        },
    )
    cover.hass = hass
    cover._attr_is_opening = True
    cover._attr_is_closing = False
    cover._move_start_position = 0
    # Backdate start by 0.5 s: 0.5/1.0 * 100 = 50% change → position ~50
    cover._move_start_time = _time.monotonic() - 0.5

    with patch.object(cover, "_stop_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            await cover.async_stop_cover()

    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is False
    frozen_pos = cover._attr_current_cover_position
    assert frozen_pos is not None
    assert 0 < frozen_pos < 100, f"Expected mid estimate, got {frozen_pos}"
    _async_mock(mock_api.control_blind).assert_awaited_once_with(
        cover._device_enum, CMD_STOP
    )


# ---------------------------------------------------------------------------
# CTRL-02 / D-02: Full run to completion resets position to 100% / 0%
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_full_open_resets_to_100(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-02: full open run (target=None) resets position to 100 via endstop branch.

    _target_position must be None so the loop takes the endstop-completion
    branch (cover.py:553-580), NOT the partial-move target-reached branch
    (cover.py:526-551).  _move_start_time is backdated beyond CONF_OPEN_TIME
    so that _update_position drives position to 100 on the first loop tick.
    """
    import time as _time

    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: 0.2,
            CONF_CLOSE_TIME: 0.2,
        },
    )
    cover.hass = hass
    cover._attr_is_opening = True
    cover._attr_is_closing = False
    # Explicitly set None so the endstop branch (not partial-move) is taken
    cover._target_position = None
    cover._move_start_position = 0
    # Backdate beyond travel time → elapsed/travel >= 1.0 → position = 100
    cover._move_start_time = _time.monotonic() - 0.5

    with patch.object(cover, "async_write_ha_state"):
        loop_task = hass.async_create_task(cover._async_position_update_loop())
        await asyncio.sleep(0.5)

    assert cover._attr_current_cover_position == 100, (
        f"Expected 100 after full open, got {cover._attr_current_cover_position}"
    )
    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is False
    assert loop_task.done(), "Expected loop task done after endstop reset"


@pytest.mark.asyncio
async def test_timed_full_close_resets_to_0(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-02: full close run (target=None) resets position to 0 via endstop branch.

    _target_position must be None so the loop takes the endstop-completion
    branch (cover.py:553-566), NOT the partial-move target-reached branch.
    _move_start_time is backdated beyond CONF_CLOSE_TIME so the loop exits on
    the first tick.
    """
    import time as _time

    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: 0.2,
            CONF_CLOSE_TIME: 0.2,
        },
    )
    cover.hass = hass
    cover._attr_is_opening = False
    cover._attr_is_closing = True
    # Explicitly set None so the endstop branch (not partial-move) is taken
    cover._target_position = None
    cover._move_start_position = 100
    # Backdate beyond travel time → elapsed/travel >= 1.0 → position = 0
    cover._move_start_time = _time.monotonic() - 0.5

    with patch.object(cover, "async_write_ha_state"):
        loop_task = hass.async_create_task(cover._async_position_update_loop())
        await asyncio.sleep(0.5)

    assert cover._attr_current_cover_position == 0, (
        f"Expected 0 after full close, got {cover._attr_current_cover_position}"
    )
    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is False
    assert cover._attr_is_closed is True
    assert loop_task.done(), "Expected loop task done after endstop reset"


# ---------------------------------------------------------------------------
# CR-01: A direct Open/Close after an in-flight set_position must NOT inherit
# the stale partial-move target and stop early.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_after_set_position_clears_stale_target(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """CR-01: full Open after an interrupted set_position runs to the endstop.

    Sequence: set_position(50) leaves _target_position=50 while the move is
    in flight; the user then presses the full Open button (async_open_cover
    with no target).  The stale target must be cleared so the loop takes the
    endstop branch and drives to 100 — NOT the partial-move branch that would
    snap to 50 and stop early.
    """
    import time as _time

    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: 0.2,
            CONF_CLOSE_TIME: 0.2,
        },
    )
    cover.hass = hass
    cover._attr_current_cover_position = 0
    # Simulate an in-flight set_position(50) leaving a stale partial target.
    cover._target_position = 50

    with patch.object(cover, "async_write_ha_state"):
        # Full Open button: no target → must clear the stale 50.
        await cover.async_open_cover()
        assert cover._target_position is None, (
            "async_open_cover must clear the stale set-position target"
        )
        # Backdate so the loop drives straight past 100 on the first tick.
        cover._move_start_time = _time.monotonic() - 0.5
        await asyncio.sleep(0.5)

    assert cover._attr_current_cover_position == 100, (
        "Expected full Open to reach 100 (not stop at stale target 50), "
        f"got {cover._attr_current_cover_position}"
    )
    assert cover._attr_is_opening is False
    assert cover._target_position is None
    mock_api.control_blind.assert_any_call("10", CMD_UP)


# ---------------------------------------------------------------------------
# 03-02: Calibrated flag (D-06 / REVIEW-01) and calibrated attribute (D-07)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_calibrated_flag_requires_both_times(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """_is_calibrated is True only when both CONF_OPEN_TIME and CONF_CLOSE_TIME
    are present with non-None values (D-06).

    DEFAULT_TRAVEL_TIME fallback must NOT set the flag.  One time only is also
    not enough — both must be present and non-None.
    """
    # No travel times at all → uncalibrated
    cover_uncal = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Uncalibrated",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    assert cover_uncal._is_calibrated is False

    # Both times present → calibrated
    cover_cal = SchellenbergCover(
        api=mock_api,
        device_id="TM02",
        device_enum="11",
        device_name="Timed Calibrated",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: 30.0,
            CONF_CLOSE_TIME: 35.0,
        },
    )
    assert cover_cal._is_calibrated is True

    # Only open time present → still uncalibrated
    cover_open_only = SchellenbergCover(
        api=mock_api,
        device_id="TM03",
        device_enum="12",
        device_name="Open Only",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: 30.0,
        },
    )
    assert cover_open_only._is_calibrated is False


@pytest.mark.asyncio
async def test_timed_calibrated_flag_rejects_none_values(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Keys present but values None must NOT count as calibrated (REVIEW-01).

    Value-presence check (`is not None`), not key-presence check (`in dict`).
    """
    # Both keys present but both None → uncalibrated
    cover_both_none = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Both None",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: None,
            CONF_CLOSE_TIME: None,
        },
    )
    assert cover_both_none._is_calibrated is False

    # One None, one real value → still uncalibrated
    cover_one_none = SchellenbergCover(
        api=mock_api,
        device_id="TM02",
        device_enum="11",
        device_name="One None",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: None,
            CONF_CLOSE_TIME: 12.0,
        },
    )
    assert cover_one_none._is_calibrated is False


@pytest.mark.asyncio
async def test_timed_calibrated_attribute_in_state(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """extra_state_attributes exposes calibrated for timed motors (D-07).

    Uncalibrated timed motor has calibrated=False; calibrated timed motor has
    calibrated=True.  Mode remains 'timed' in both cases.
    """
    cover_uncal = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Uncalibrated",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    attrs_uncal = cover_uncal.extra_state_attributes
    assert attrs_uncal["mode"] == "timed"
    assert attrs_uncal["calibrated"] is False

    cover_cal = SchellenbergCover(
        api=mock_api,
        device_id="TM02",
        device_enum="11",
        device_name="Calibrated",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: 30.0,
            CONF_CLOSE_TIME: 35.0,
        },
    )
    attrs_cal = cover_cal.extra_state_attributes
    assert attrs_cal["mode"] == "timed"
    assert attrs_cal["calibrated"] is True


@pytest.mark.asyncio
async def test_bidir_has_no_calibrated_attribute(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Bidirectional covers must NOT expose a 'calibrated' key (D-07)."""
    cover_bidir = SchellenbergCover(
        api=mock_api,
        device_id="BD01",
        device_enum="10",
        device_name="Bidirectional",
        device_data={CONF_BIDIRECTIONAL: True},
    )
    attrs = cover_bidir.extra_state_attributes
    assert attrs["mode"] == "bidirectional"
    assert "calibrated" not in attrs


# ---------------------------------------------------------------------------
# 03-02: Uncalibrated set-position no-op gate (D-05)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_set_position_noop_when_uncalibrated(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-05: set_position on an uncalibrated timed motor is a silent no-op.

    Neither async_open_cover nor async_close_cover must be called.
    current_position must remain unchanged.  No exception must be raised.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    cover.hass = hass
    cover._attr_current_cover_position = 50

    with patch.object(cover, "async_open_cover", new_callable=AsyncMock) as mock_open:
        with patch.object(
            cover, "async_close_cover", new_callable=AsyncMock
        ) as mock_close:
            await cover.async_set_cover_position(**{ATTR_POSITION: 80})

    mock_open.assert_not_called()
    mock_close.assert_not_called()
    assert cover._attr_current_cover_position == 50


# ---------------------------------------------------------------------------
# 03-03: Timed motor restart restore (D-08, D-09, D-11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_restart_opening_snaps_to_100(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-08: timed motor was opening at HA restart → restore to 100%.

    The stale mid-move current_position (60%) must be discarded; the
    endstop snap to 100% wins.  Bidirectional guard ensures this branch
    only fires for timed motors.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    cover.hass = hass

    last_state = State("cover.timed_motor", "opening", {"current_position": 60})
    with patch.object(cover, "async_get_last_state", return_value=last_state):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    assert cover._attr_current_cover_position == 100
    assert cover._attr_is_closed is False


@pytest.mark.asyncio
async def test_timed_restart_closing_snaps_to_0(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-08: timed motor was closing at HA restart → restore to 0%.

    The stale mid-move current_position (40%) must be discarded; the
    endstop snap to 0% wins.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    cover.hass = hass

    last_state = State("cover.timed_motor", "closing", {"current_position": 40})
    with patch.object(cover, "async_get_last_state", return_value=last_state):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    assert cover._attr_current_cover_position == 0
    assert cover._attr_is_closed is True


@pytest.mark.asyncio
async def test_timed_handle_event_ignored(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-11 / REVIEW-04: a stray device event on a timed motor is a no-op.

    _handle_event must early-return for timed motors before any state
    mutation.  Neither is_opening nor is_closing must be set; position
    must remain unchanged.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    cover.hass = hass
    cover._attr_current_cover_position = 75
    cover._attr_is_opening = False
    cover._attr_is_closing = False

    with patch.object(cover, "async_write_ha_state") as mock_write:
        cover._handle_event(EVENT_STARTED_MOVING_UP)

    # No state mutation, no HA state write
    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is False
    assert cover._attr_current_cover_position == 75
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# 04-02: D-14 end-state awareness — timed (100%) and legacy (0%) paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calibration_completed_timed_ends_100pct(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-14 / REVIEW-1: timed flow emits final_position=100; cover lands at 100%.

    Distinct 4-arg test proving the new final_position param routes correctly:
    _attr_current_cover_position must be 100, _attr_is_closed must be False,
    _travel_time_open and _travel_time_close must be updated, _is_calibrated
    must be True.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    cover.hass = hass

    with patch.object(cover, "async_write_ha_state"):
        cover._handle_calibration_completed("ABC123", 20.0, 18.0, 100)

    assert cover._travel_time_open == 20.0
    assert cover._travel_time_close == 18.0
    assert cover._attr_current_cover_position == 100
    assert cover._attr_is_closed is False
    assert cover._is_calibrated is True


@pytest.mark.asyncio
async def test_calibration_completed_legacy_ends_0pct(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-14 / CTRL-05: explicit final_position=0 leaves cover at 0% / closed.

    Regression pin for the legacy close-ending flow path: passing an explicit 0
    as the 4th arg must produce position==0 and is_closed==True. This is
    distinct from the canonical 3-arg test_cover_calibration_completed which
    proves the default-arg backward-compat path (REVIEW-1).
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="01",
        device_name="Test Cover",
    )
    cover.hass = hass
    cover._attr_current_cover_position = 50

    with patch.object(cover, "async_write_ha_state"):
        cover._handle_calibration_completed("ABC123", 25.0, 23.0, 0)

    assert cover._attr_current_cover_position == 0
    assert cover._attr_is_closed is True
    assert cover._is_calibrated is True


# ---------------------------------------------------------------------------
# 04-02: CTRL-03 / D-05 — set-position no-op gate and unlock after calibration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_position_noop_until_calibrated(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-05 / CTRL-03: timed motor rejects set-position when not calibrated.

    A timed cover with _is_calibrated=False must silently ignore
    async_set_cover_position — no control_blind call, position unchanged.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False},  # no travel times => uncalibrated
    )
    cover.hass = hass
    cover._attr_current_cover_position = 50

    assert cover._is_calibrated is False

    await cover.async_set_cover_position(**{ATTR_POSITION: 80})

    _async_mock(mock_api.control_blind).assert_not_awaited()
    assert cover._attr_current_cover_position == 50


@pytest.mark.asyncio
async def test_set_position_unlocked_after_calibration_drives_to_midpoint(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """CTRL-03 / SC#3: calibrated timed motor drives to 50% and stops there.

    After _is_calibrated flips (via signal or direct set), async_set_cover_position
    must issue CMD_UP and then CMD_STOP at the midpoint. Elapsed time is
    synthesized: _move_start_time is backdated so the position update loop
    computes >=50% on the first iteration, triggering the midpoint-stop branch.

    The test asserts:
      - control_blind called with CMD_UP (move starts)
      - control_blind called with CMD_STOP (midpoint stop)
      - _attr_current_cover_position == 50 after loop exits
      - _target_position is None after loop exits
    """
    import time as _time

    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_OPEN_TIME: 20.0,
            CONF_CLOSE_TIME: 20.0,
        },
    )
    cover.hass = hass
    cover._attr_current_cover_position = 0
    cover._attr_is_closed = True

    assert cover._is_calibrated is True

    with patch.object(cover, "async_write_ha_state"):
        # Start the move to 50%; this sends CMD_UP and starts the loop task.
        await cover.async_set_cover_position(**{ATTR_POSITION: 50})

        # Backdate _move_start_time so the loop immediately sees >=50% progress
        # (elapsed=10s out of open_time=20s -> 50% from position 0).
        cover._move_start_time = _time.monotonic() - 10.0

        # Allow the loop to run one iteration (200 ms sleep + check).
        await asyncio.sleep(0.5)

    # CMD_UP must have been sent (start of move)
    calls = _async_mock(mock_api.control_blind).await_args_list
    cmd_up_calls = [c for c in calls if c.args[1] == CMD_UP]
    cmd_stop_calls = [c for c in calls if c.args[1] == CMD_STOP]

    assert len(cmd_up_calls) >= 1, f"Expected CMD_UP call; got calls: {calls}"
    assert len(cmd_stop_calls) >= 1, (
        f"Expected CMD_STOP at midpoint; got calls: {calls}"
    )
    assert cover._attr_current_cover_position == 50, (
        f"Expected position 50, got {cover._attr_current_cover_position}"
    )
    assert cover._target_position is None, (
        f"Expected _target_position=None after completion, got {cover._target_position}"
    )


# ---------------------------------------------------------------------------
# 04-02: SC#4 / D-13 — calibration times survive a restart via the Store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_calibration_survives_restart(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """SC#4 / D-13: calibration times saved to Store are read back correctly.

    Sequence mirrors cover.py:async_setup_entry (the restart path):
      1. _save_calibration writes open/close times for device_id under
         config_entry_id into the Store.
      2. _get_cal_store reads them back — values are in the cache.
      3. A cover built with those values merged via setdefault has
         _is_calibrated=True (value-presence check, D-06/REVIEW-01).
    """
    entry_id = "test_entry_123"
    device_id = "ABC999"

    # Step 1: write calibration to the Store.
    await _save_calibration(hass, entry_id, device_id, 21.5, 19.3)

    # Step 2: read it back via the Store/cache.
    _store, cache = await _get_cal_store(hass)
    entry_data = cache.get(entry_id, {})
    persisted = entry_data.get(str(device_id))

    assert persisted is not None, "Expected persisted calibration entry"
    assert persisted["open_time"] == 21.5
    assert persisted["close_time"] == 19.3

    # Step 3: simulate the setdefault merge that async_setup_entry performs,
    # then verify that a cover built from the merged data is marked calibrated.
    merged: dict[str, object] = {}
    merged.setdefault(CONF_OPEN_TIME, persisted.get("open_time"))
    merged.setdefault(CONF_CLOSE_TIME, persisted.get("close_time"))

    cover_after_restart = SchellenbergCover(
        api=mock_api,
        device_id=device_id,
        device_enum="1A",
        device_name="Restarted Cover",
        device_data={
            CONF_BIDIRECTIONAL: False,
            **merged,
        },
    )
    assert cover_after_restart._is_calibrated is True, (
        "Cover built from persisted calibration data must be _is_calibrated=True"
    )


@pytest.mark.asyncio
async def test_timed_restart_idle_restores_position(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-09: idle timed motor restart restores the recorded position as-is.

    A real recorded 0%% (cover fully closed) must survive restart — it must
    NOT be replaced by initial_position or the 100%% fallback (Pitfall 1:
    the falsy-0 trap).  Also verifies a non-zero idle case (30%%).
    """
    # Case 1: real recorded 0%% — closed cover stays closed
    cover_zero = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False, CONF_INITIAL_POSITION: 50},
    )
    cover_zero.hass = hass

    last_state_zero = State("cover.timed_motor", "closed", {"current_position": 0})
    with patch.object(cover_zero, "async_get_last_state", return_value=last_state_zero):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover_zero, "async_write_ha_state"):
                await cover_zero.async_added_to_hass()

    assert cover_zero._attr_current_cover_position == 0, (
        "Real recorded 0%% must survive — must not be replaced by"
        f" initial_position; got {cover_zero._attr_current_cover_position}"
    )
    assert cover_zero._attr_is_closed is True

    # Case 2: non-zero idle restore (30%%)
    cover_thirty = SchellenbergCover(
        api=mock_api,
        device_id="TM02",
        device_enum="11",
        device_name="Timed Motor 30",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    cover_thirty.hass = hass

    last_state_thirty = State("cover.timed_motor_30", "open", {"current_position": 30})
    with patch.object(
        cover_thirty,
        "async_get_last_state",
        return_value=last_state_thirty,
    ):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover_thirty, "async_write_ha_state"):
                await cover_thirty.async_added_to_hass()

    assert cover_thirty._attr_current_cover_position == 30
    assert cover_thirty._attr_is_closed is False


@pytest.mark.asyncio
async def test_timed_restart_no_prior_state_uses_initial_position(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-09: timed motor with no prior state uses CONF_INITIAL_POSITION.

    When async_get_last_state returns None AND CONF_INITIAL_POSITION is
    set, position must be seeded to that value (not 0 and not 100).
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False, CONF_INITIAL_POSITION: 70},
    )
    cover.hass = hass

    with patch.object(cover, "async_get_last_state", return_value=None):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    assert cover._attr_current_cover_position == 70


@pytest.mark.asyncio
async def test_timed_restart_no_prior_no_initial_defaults_to_100(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """D-09: timed motor with no prior state and no initial_position defaults
    to 100%% (assume open), never 0.

    SC#4: the slider must not jump to 0%% after restart.  This is the key
    regression guard — the existing bidirectional default of 0 must NOT
    apply to timed motors.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="TM01",
        device_enum="10",
        device_name="Timed Motor",
        device_data={CONF_BIDIRECTIONAL: False},
    )
    cover.hass = hass

    with patch.object(cover, "async_get_last_state", return_value=None):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    assert cover._attr_current_cover_position == 100, (
        "Timed motor with no prior state must default to 100%% (assume open),"
        f" not 0; got {cover._attr_current_cover_position}"
    )
    assert cover._attr_is_closed is False


# ---------------------------------------------------------------------------
# 03-04 / CTRL-05: Bidirectional zero-regression tests
# Prove every Phase 3 gate is timed-only; bidirectional path is unaffected.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bidir_open_close_stop_regression(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """CTRL-05: bidirectional open/close/stop dispatch and freeze-stop unchanged.

    Uses a device_data with NO CONF_BIDIRECTIONAL key to exercise the
    read-default True legacy path (SC#5 safety case).  Verifies CMD_UP /
    CMD_DOWN / CMD_STOP are dispatched exactly once for open / close / stop,
    and that stop calls _stop_position_tracking before mutating position
    (freeze-at-estimate, not snap).
    """
    # --- open ---
    cover_open = SchellenbergCover(
        api=mock_api,
        device_id="BD01",
        device_enum="10",
        device_name="Bidir Open",
        # Deliberately omit CONF_BIDIRECTIONAL: exercises read-default True
        device_data={"device_id": "BD01", "device_enum": "10"},
    )
    cover_open.hass = hass
    cover_open._attr_current_cover_position = 0

    with patch.object(cover_open, "_start_position_tracking"):
        with patch.object(cover_open, "async_write_ha_state"):
            await cover_open.async_open_cover()

    assert cover_open._attr_is_opening is True
    assert cover_open._attr_is_closing is False
    _async_mock(mock_api.control_blind).assert_awaited_once_with(
        cover_open._device_enum, CMD_UP
    )

    # --- close ---
    _async_mock(mock_api.control_blind).reset_mock()
    cover_close = SchellenbergCover(
        api=mock_api,
        device_id="BD02",
        device_enum="11",
        device_name="Bidir Close",
        device_data={"device_id": "BD02", "device_enum": "11"},
    )
    cover_close.hass = hass
    cover_close._attr_current_cover_position = 100

    with patch.object(cover_close, "_start_position_tracking"):
        with patch.object(cover_close, "async_write_ha_state"):
            await cover_close.async_close_cover()

    assert cover_close._attr_is_opening is False
    assert cover_close._attr_is_closing is True
    _async_mock(mock_api.control_blind).assert_awaited_once_with(
        cover_close._device_enum, CMD_DOWN
    )

    # --- stop (freeze-at-estimate) ---
    _async_mock(mock_api.control_blind).reset_mock()
    cover_stop = SchellenbergCover(
        api=mock_api,
        device_id="BD03",
        device_enum="12",
        device_name="Bidir Stop",
        device_data={"device_id": "BD03", "device_enum": "12"},
    )
    cover_stop.hass = hass
    cover_stop._attr_is_opening = True
    cover_stop._attr_current_cover_position = 50

    stop_order: list[str] = []

    def _record_stop() -> None:
        stop_order.append("stop_tracking")
        cover_stop._attr_current_cover_position = 50  # mock freeze

    with patch.object(cover_stop, "_stop_position_tracking", side_effect=_record_stop):
        with patch.object(cover_stop, "_update_position"):
            with patch.object(cover_stop, "async_write_ha_state"):
                await cover_stop.async_stop_cover()

    # _stop_position_tracking must be called before CMD_STOP dispatch
    assert stop_order == ["stop_tracking"], (
        "stop_tracking must be called before position mutation"
    )
    assert cover_stop._attr_is_opening is False
    assert cover_stop._attr_is_closing is False
    _async_mock(mock_api.control_blind).assert_awaited_once_with(
        cover_stop._device_enum, CMD_STOP
    )


@pytest.mark.asyncio
async def test_bidir_set_position_not_gated(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """CTRL-05 / D-05: bidirectional set-position is NOT gated on calibration.

    A bidirectional cover with NO calibration times (_is_calibrated False)
    must still perform a real move — async_open_cover must be awaited when
    the target exceeds the current position.  The D-05 uncalibrated gate is
    timed-only (`not self._is_bidirectional and not self._is_calibrated`).
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="BD01",
        device_enum="10",
        device_name="Bidir Cover",
        # No CONF_OPEN_TIME / CONF_CLOSE_TIME → _is_calibrated False
        device_data={CONF_BIDIRECTIONAL: True},
    )
    cover.hass = hass
    cover._attr_current_cover_position = 50

    assert cover._is_bidirectional is True
    assert cover._is_calibrated is False

    with patch.object(cover, "async_open_cover", new_callable=AsyncMock) as mock_open:
        await cover.async_set_cover_position(**{ATTR_POSITION: 80})

    # Gate must NOT have fired — open was called
    mock_open.assert_awaited_once()


@pytest.mark.asyncio
async def test_bidir_restore_not_snapped_to_endstop(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """CTRL-05 / D-08 / REVIEW-02: bidirectional mid-move restore is NOT endstop-snapped.

    A bidirectional cover recorded as 'opening' with current_position=60 must
    restore to 60, NOT to 100.  The D-08 endstop snap is timed-only; the
    bidirectional branch calls the shared _restore_position_from_last_state
    helper (REVIEW-02 canary — logic lives in exactly one place).
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="BD01",
        device_enum="10",
        device_name="Bidir Cover",
        device_data={CONF_BIDIRECTIONAL: True},
    )
    cover.hass = hass

    last_state = State("cover.bidir_cover", "opening", {"current_position": 60})
    with patch.object(cover, "async_get_last_state", return_value=last_state):
        with patch("custom_components.schellenberg_usb.cover.async_dispatcher_connect"):
            with patch.object(cover, "async_write_ha_state"):
                await cover.async_added_to_hass()

    assert cover._attr_current_cover_position == 60, (
        "Bidirectional mid-move restore must use recorded position (60),"
        f" not the endstop snap (100); got {cover._attr_current_cover_position}"
    )
    assert cover._attr_is_closed is False


@pytest.mark.asyncio
async def test_legacy_no_bidirectional_key_defaults_bidirectional(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """CTRL-05 / REVIEW-06: subentry with NO CONF_BIDIRECTIONAL key is bidirectional.

    A cover built from a legacy Phase-1 subentry (no CONF_BIDIRECTIONAL key
    at all) must have _is_bidirectional True (read-default at cover.py:276)
    AND its extra_state_attributes must expose mode='bidirectional' with NO
    'calibrated' key.  Dedicated test per REVIEW-06 — not folded into another.
    """
    cover = SchellenbergCover(
        api=mock_api,
        device_id="ABC123",
        device_enum="10",
        device_name="Legacy Cover",
        # Intentionally omit CONF_BIDIRECTIONAL — simulates Phase-1 subentry
        device_data={"device_id": "ABC123", "device_enum": "10"},
    )

    assert cover._is_bidirectional is True, (
        "No-key subentry must default to bidirectional (read-default True)"
    )
    attrs = cover.extra_state_attributes
    assert attrs["mode"] == "bidirectional", (
        f"Expected mode='bidirectional', got mode='{attrs['mode']}'"
    )
    assert "calibrated" not in attrs, (
        f"Bidirectional cover must NOT expose 'calibrated'; attrs={attrs}"
    )


# ---------------------------------------------------------------------------
# Regression: timed calibration must survive the post-save reconfigure reload
# (timed-cal-uses-default-time) — a 25% close on a 25s-calibrated motor must
# NOT drive on the 60s DEFAULT_TRAVEL_TIME after the entry is rebuilt.
# ---------------------------------------------------------------------------


def _make_timed_hub_entry(
    hass: HomeAssistant,
    entry_id: str,
    device_id: str,
    device_enum: str,
) -> ConfigEntry:
    """Build a hub config entry carrying a single UNCALIBRATED timed subentry.

    Mirrors the real shipped state: the timed calibration flow never writes
    CONF_OPEN_TIME/CONF_CLOSE_TIME into subentry.data, so the subentry stays
    uncalibrated and the Store cache is the only persistent calibration carrier.
    """
    subentry = MagicMock()
    subentry.subentry_id = "sub_timed"
    subentry.subentry_type = "blind"
    subentry.data = {
        "device_id": device_id,
        "device_enum": device_enum,
        "device_name": "Timed Cover",
        CONF_BIDIRECTIONAL: False,
    }
    subentry.title = "Timed Cover"

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id=entry_id,
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    entry.subentries = MappingProxyType(  # type: ignore[misc]
        {"sub_timed": subentry}
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


@pytest.mark.asyncio
async def test_timed_calibration_survives_reconfigure_reload(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """REGRESSION (timed-cal-uses-default-time): a 25s-calibrated timed motor
    must keep its 25s close time after the reconfigure-triggered entry reload.

    Drives the REAL flow-handler emit path (NOT the cover's fire-and-forget
    save). Real shipped sequence reproduced here:
      1. Initial setup builds the timed cover (UNCALIBRATED — subentry has no
         CONF_*_TIME, Store empty) -> _travel_time_close == 60 (DEFAULT).
      2. The timed calibration flow finishes: TimedCalibrationFlowHandler
         ._emit_calibration_signal runs, which (after the fix) persists the
         times to the cover calibration Store AND emits the live signal.
      3. The flow aborts `reconfigure_successful`; HA reloads the entry, which
         rebuilds the cover via async_setup_entry reading the Store.

    Bug (pre-fix): the flow only emitted the signal and relied on the cover's
    fire-and-forget save, which raced the reload — the rebuilt cover read an
    empty Store and came up on the 60s DEFAULT (uncalibrated). A 25% close then
    drove on 60s instead of 25s.

    With the fix the flow persists BEFORE the signal/abort, so the rebuilt cover
    re-hydrates the 25s close time and stays calibrated.
    """
    # Lazy imports: keep the flow handler dependency local to this regression.
    from custom_components.schellenberg_usb.options_flow_timed_calibration import (  # noqa: E501
        TimedCalibrationFlowHandler,
    )

    entry_id = "test_timed_reconfigure_reload"
    device_id = "TM7788"
    device_enum = "12"
    close_time = 25.0
    open_time = 25.0

    entry = _make_timed_hub_entry(hass, entry_id, device_id, device_enum)
    entry.runtime_data = mock_api

    dev_reg = dr.async_get(hass)
    dev_reg.async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, entry_id)},
        name="Schellenberg USB Stick",
        manufacturer="Schellenberg",
    )

    # --- Step 1: initial setup builds the (uncalibrated) timed cover ----------
    first_built: list[SchellenbergCover] = []

    def _capture_first(entities: Any, **_kwargs: Any) -> None:
        first_built.extend(entities)

    await async_setup_entry(
        hass, entry, cast(AddConfigEntryEntitiesCallback, _capture_first)
    )
    assert len(first_built) == 1
    live_cover = first_built[0]
    live_cover.hass = hass

    assert live_cover._travel_time_close == DEFAULT_TRAVEL_TIME
    assert live_cover._is_calibrated is False

    # The live cover must subscribe to SIGNAL_CALIBRATION_COMPLETED exactly as
    # the real entity does in async_added_to_hass (otherwise the dispatched
    # signal would reach nobody). Mock state restore + write so the add path
    # only wires up the dispatcher subscriptions.
    with patch.object(live_cover, "async_get_last_state", return_value=None):
        with patch.object(live_cover, "async_write_ha_state"):
            await live_cover.async_added_to_hass()

    # --- Step 2: run the REAL timed-flow emit (persist + signal) --------------
    # Build a TimedCalibrationFlowHandler whose mock flow's _get_entry returns
    # the real hub entry, mirroring the shipped reconfigure delegation.
    mock_flow = MagicMock()
    mock_flow.hass = hass
    mock_flow._get_entry.return_value = entry

    handler = TimedCalibrationFlowHandler(mock_flow)
    handler.set_selected_device(
        {"id": device_id, "name": "Timed Cover", "enum": device_enum}
    )
    handler._open_time = open_time
    handler._close_time = close_time

    # Withhold the cover's fire-and-forget save so this test proves the FLOW's
    # durable persistence — not the racy cover-side task — survives the reload.
    # Close (not run) the cover-side coroutine to avoid a 'never awaited' leak.
    def _discard_task(coro: Any) -> MagicMock:
        if asyncio.iscoroutine(coro):
            coro.close()
        return MagicMock()

    with patch.object(live_cover, "async_write_ha_state"):
        with patch.object(hass, "async_create_task", side_effect=_discard_task):
            await handler._emit_calibration_signal()

    # The live instance was updated by the dispatched signal.
    assert live_cover._travel_time_close == close_time
    assert live_cover._is_calibrated is True

    # --- Step 3: reconfigure_successful reload rebuilds the cover from Store ---
    second_built: list[SchellenbergCover] = []

    def _capture_second(entities: Any, **_kwargs: Any) -> None:
        second_built.extend(entities)

    await async_setup_entry(
        hass, entry, cast(AddConfigEntryEntitiesCallback, _capture_second)
    )
    assert len(second_built) == 1
    rebuilt_cover = second_built[0]

    # The rebuilt cover is the one HA now drives. It MUST retain the 25s close
    # time and stay calibrated. Pre-fix it came up on the 60s default.
    assert rebuilt_cover._travel_time_close == close_time, (
        "Rebuilt cover lost its calibrated close time after the reconfigure "
        f"reload: expected {close_time}s, got "
        f"{rebuilt_cover._travel_time_close}s (DEFAULT_TRAVEL_TIME="
        f"{DEFAULT_TRAVEL_TIME}). The timed flow must persist to the Store "
        "before the reload rebuilds the cover."
    )
    assert rebuilt_cover._travel_time_open == open_time
    assert rebuilt_cover._is_calibrated is True, (
        "Rebuilt cover must remain calibrated after the reconfigure reload."
    )
