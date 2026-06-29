"""Tests for cover entity remote-move tracking (RMT-04, RMT-05)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    CMD_DOWN,
    CMD_MANUAL_DOWN,
    CMD_MANUAL_UP,
    CMD_STOP,
    CMD_UP,
    CONF_BIDIRECTIONAL,
    CONF_REMOTE_ID,
    SIGNAL_REMOTE_EVENT,
)
from custom_components.schellenberg_usb.cover import SchellenbergCover


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_api(hass: HomeAssistant) -> SchellenbergUsbApi:
    """Create a mock API for timed motor tests."""
    api_mock = MagicMock(spec=SchellenbergUsbApi)
    api_mock.hass = hass
    api_mock.is_connected = True
    api_mock.device_version = "RFTU_V20"
    api_mock.control_blind = AsyncMock()
    api_mock.register_entity = MagicMock()
    api_mock.register_remote = MagicMock()
    api_mock.unregister_remote = MagicMock()
    return cast(SchellenbergUsbApi, api_mock)


@pytest.fixture
def timed_remote_cover(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> SchellenbergCover:
    """SchellenbergCover for a timed motor with remote_id bound."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="MOT001",
        device_enum="10",
        device_name="Test Blind",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_REMOTE_ID: "REM001",
        },
    )
    cover.hass = hass
    return cover


# ---------------------------------------------------------------------------
# RMT-04: Remote open/close starts position loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_open_starts_tracking(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """Remote CMD_UP starts position loop for timed motor (RMT-04)."""
    cover = timed_remote_cover
    cover._attr_current_cover_position = 50

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_remote_event(CMD_UP, 12345.0)

    assert cover._attr_is_opening is True
    assert cover._attr_is_closing is False
    assert cover._move_start_position == 50


@pytest.mark.asyncio
async def test_remote_jog_open_starts_tracking(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """Remote CMD_MANUAL_UP (41/jog) starts the open loop — normalized in cover layer (D-01)."""
    cover = timed_remote_cover
    cover._attr_current_cover_position = 30

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_remote_event(CMD_MANUAL_UP, 12345.0)

    assert cover._attr_is_opening is True
    assert cover._attr_is_closing is False
    assert cover._move_start_position == 30


@pytest.mark.asyncio
async def test_remote_close_starts_tracking(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """Remote CMD_DOWN starts close position loop (RMT-04)."""
    cover = timed_remote_cover
    cover._attr_current_cover_position = 80

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_remote_event(CMD_DOWN, 12345.0)

    assert cover._attr_is_closing is True
    assert cover._attr_is_opening is False
    assert cover._move_start_position == 80


@pytest.mark.asyncio
async def test_remote_jog_close_starts_tracking(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """Remote CMD_MANUAL_DOWN (42/jog) starts the close loop — normalized in cover layer (D-01)."""
    cover = timed_remote_cover
    cover._attr_current_cover_position = 60

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_remote_event(CMD_MANUAL_DOWN, 12345.0)

    assert cover._attr_is_closing is True
    assert cover._attr_is_opening is False
    assert cover._move_start_position == 60


@pytest.mark.asyncio
async def test_remote_move_start_time_is_backdated(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """_move_start_time is set to receive_timestamp, not time.monotonic() (D-06/P9)."""
    cover = timed_remote_cover
    cover._attr_current_cover_position = 0
    known_ts = 99999.5

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_remote_event(CMD_UP, known_ts)

    assert cover._move_start_time == known_ts


@pytest.mark.asyncio
async def test_remote_direction_reversal(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """Open then close in sequence — second call leaves cover in closing state (Pitfall P4)."""
    cover = timed_remote_cover
    cover._attr_current_cover_position = 50

    # First call: open
    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_remote_event(CMD_UP, 12345.0)

    assert cover._attr_is_opening is True

    # Second call: close — reversal handled by _start_position_tracking
    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_remote_event(CMD_DOWN, 12346.0)

    assert cover._attr_is_closing is True
    assert cover._attr_is_opening is False


# ---------------------------------------------------------------------------
# RMT-05: Remote stop latches position
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_stop_latches_position(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """Remote CMD_STOP latches position via _update_position and clears movement flags (RMT-05/D-04)."""
    cover = timed_remote_cover
    cover._attr_is_opening = True
    cover._attr_is_closing = False
    cover._move_start_time = 12300.0
    cover._move_start_position = 30

    with patch.object(cover, "_stop_position_tracking") as mock_stop:
        with patch.object(cover, "_update_position") as mock_update:
            with patch.object(cover, "async_write_ha_state"):
                cover._handle_remote_event(CMD_STOP, 12345.0)

    mock_stop.assert_called_once()
    mock_update.assert_called_once()
    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is False


@pytest.mark.asyncio
async def test_remote_stop_no_target_snap(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """After remote stop, _target_position is None — no set-position snap (RMT-05/D-04)."""
    cover = timed_remote_cover
    cover._attr_is_opening = True
    cover._move_start_time = 12300.0
    cover._move_start_position = 30

    with patch.object(cover, "_stop_position_tracking"):
        with patch.object(cover, "_update_position"):
            with patch.object(cover, "async_write_ha_state"):
                cover._handle_remote_event(CMD_STOP, 12345.0)

    assert cover._target_position is None


@pytest.mark.asyncio
async def test_remote_stop_clears_target_position(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """Remote stop clears _target_position even if a mid-flight HA set-position move had set it (REVIEW-12-02)."""
    cover = timed_remote_cover
    cover._attr_is_opening = True
    cover._move_start_time = 12300.0
    cover._move_start_position = 30
    cover._target_position = 50  # simulates an interrupted HA set-position move

    with patch.object(cover, "_stop_position_tracking"):
        with patch.object(cover, "_update_position"):
            with patch.object(cover, "async_write_ha_state"):
                cover._handle_remote_event(CMD_STOP, 12345.0)

    assert cover._target_position is None


# ---------------------------------------------------------------------------
# REVIEW-12-03: None-position defaults (track-the-travel rule)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_open_from_unknown_defaults_low(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """Remote OPEN from unknown position defaults _move_start_position to 0 (REVIEW-12-03)."""
    cover = timed_remote_cover
    cover._attr_current_cover_position = None  # unknown position

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_remote_event(CMD_UP, 12345.0)

    # Assume closed (0) so upward travel toward 100 is visible
    assert cover._move_start_position == 0


@pytest.mark.asyncio
async def test_remote_close_from_unknown_defaults_high(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
    timed_remote_cover: SchellenbergCover,
) -> None:
    """Remote CLOSE from unknown position defaults _move_start_position to 100 (REVIEW-12-03)."""
    cover = timed_remote_cover
    cover._attr_current_cover_position = None  # unknown position

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            cover._handle_remote_event(CMD_DOWN, 12345.0)

    # Assume open (100) so downward travel toward 0 is visible
    assert cover._move_start_position == 100


# ---------------------------------------------------------------------------
# SC3: No remote binding → no subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_binding_no_subscription(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Timed cover with NO CONF_REMOTE_ID does not subscribe to SIGNAL_REMOTE_EVENT (SC3)."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="MOT002",
        device_enum="11",
        device_name="Unbound Blind",
        device_data={CONF_BIDIRECTIONAL: False},  # No CONF_REMOTE_ID
    )
    cover.hass = hass

    with patch(
        "custom_components.schellenberg_usb.cover.async_dispatcher_connect"
    ) as mock_connect:
        with patch.object(cover, "async_write_ha_state"):
            with patch.object(cover, "async_get_last_state", return_value=None):
                await cover.async_added_to_hass()

    # No SIGNAL_REMOTE_EVENT subscription should have been made
    remote_event_signal = SIGNAL_REMOTE_EVENT
    for call in mock_connect.call_args_list:
        signal = call[0][1]  # positional arg 1 is the signal name
        assert not signal.startswith(remote_event_signal), (
            f"Unexpected SIGNAL_REMOTE_EVENT subscription: {signal}"
        )

    # register_remote must NOT be called
    cast(MagicMock, mock_api.register_remote).assert_not_called()


# ---------------------------------------------------------------------------
# SC4: Bidirectional motors are unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bidirectional_unaffected(
    hass: HomeAssistant,
    mock_api: SchellenbergUsbApi,
) -> None:
    """Bidirectional cover with a CONF_REMOTE_ID does NOT subscribe to SIGNAL_REMOTE_EVENT (SC4/Pitfall C)."""
    cover = SchellenbergCover(
        api=mock_api,
        device_id="MOT003",
        device_enum="12",
        device_name="Bidirectional Blind",
        device_data={
            CONF_BIDIRECTIONAL: True,  # bidirectional
            CONF_REMOTE_ID: "REM002",  # remote_id present, but guard must prevent subscription
        },
    )
    cover.hass = hass

    with patch(
        "custom_components.schellenberg_usb.cover.async_dispatcher_connect"
    ) as mock_connect:
        with patch.object(cover, "async_write_ha_state"):
            with patch.object(cover, "async_get_last_state", return_value=None):
                await cover.async_added_to_hass()

    remote_event_signal = SIGNAL_REMOTE_EVENT
    for call in mock_connect.call_args_list:
        signal = call[0][1]
        assert not signal.startswith(remote_event_signal), (
            f"Unexpected SIGNAL_REMOTE_EVENT subscription for bidirectional: {signal}"
        )

    cast(MagicMock, mock_api.register_remote).assert_not_called()


# ---------------------------------------------------------------------------
# Integration: real _handle_message → SIGNAL_REMOTE_EVENT → _handle_remote_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_message_to_cover_handler(hass: HomeAssistant) -> None:
    """Real _handle_message → SIGNAL_REMOTE_EVENT → _handle_remote_event path fires end-to-end.

    This test drives the REAL signal path (no inline dispatch reimplementation).
    The real async_dispatcher_connect subscription is left unpatched so the callback
    actually fires. Entity-lifecycle side effects (async_write_ha_state, issue registry)
    are patched to isolate the behavior under test (REVIEW-12-01).
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")

    cover = SchellenbergCover(
        api=api,
        device_id="MOT001",
        device_enum="10",
        device_name="Test Blind",
        device_data={CONF_BIDIRECTIONAL: False, CONF_REMOTE_ID: "REM001"},
    )
    cover.hass = hass

    # DO NOT patch async_dispatcher_connect — the real subscription must register.
    # Patch lifecycle side effects to avoid setup-error false-RED (REVIEW-12-01).
    with patch.object(cover, "async_get_last_state", return_value=None):
        with patch.object(cover, "async_write_ha_state"):
            with patch(
                "homeassistant.helpers.issue_registry.async_create_issue"
            ):
                await cover.async_added_to_hass()

    # Drive the real _handle_message path and assert the cover handler fires
    with patch.object(cover, "async_write_ha_state"):
        api._handle_message("ss10REM001ABCD01PP00")

    assert cover._attr_is_opening is True


# ---------------------------------------------------------------------------
# REVIEW-12-04: Binding lifecycle idempotency (double async_added_to_hass)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_remote_idempotent_on_readd(hass: HomeAssistant) -> None:
    """Double async_added_to_hass leaves exactly one _remote_to_motor entry (REVIEW-12-04).

    A reload / double-add must not leak or accumulate stale bindings.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    cover = SchellenbergCover(
        api=api,
        device_id="MOT001",
        device_enum="10",
        device_name="Test Blind",
        device_data={CONF_BIDIRECTIONAL: False, CONF_REMOTE_ID: "REM001"},
    )
    cover.hass = hass

    # Call async_added_to_hass TWICE (simulates reload / double-add)
    for _ in range(2):
        with patch.object(cover, "async_get_last_state", return_value=None):
            with patch.object(cover, "async_write_ha_state"):
                with patch(
                    "homeassistant.helpers.issue_registry.async_create_issue"
                ):
                    await cover.async_added_to_hass()

    # Exactly ONE entry for REM001 in _remote_to_motor — no stale duplicate
    assert "REM001" in api._remote_to_motor
    assert len([k for k in api._remote_to_motor if k == "REM001"]) == 1
    # Must map to the correct motor (dict[str, str]: remote_id -> motor_id)
    assert api._remote_to_motor["REM001"] == "MOT001"
