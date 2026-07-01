"""Regression: handheld-remote command codes (82/83/84) drive tracking + events.

Built from the REAL on-hardware frames in the user's debug log
(remote-cmd-code-unmapped.md, log lines 112-247): a down->stop->open press
sequence from remote 7C055A bound to motor enum 33. Before the fix these codes
fell into the "unknown remote command" / "ignoring unknown command" branches and
did nothing; after the fix they drive the cover position loop and fire the event
entity with the same semantics as the stick's own 00/01/02 codes.

Frame layout (api.py:421-424): ss + enum[2:4] + id[4:10] + incr[10:14] +
cmd[14:16] + trailing hold-counter/checksum. e.g. ss337C055A021D8200CD ->
enum=33, id=7C055A, incr=021D, cmd=82.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    CONF_BIDIRECTIONAL,
    CONF_REMOTE_ID,
)
from custom_components.schellenberg_usb.cover import SchellenbergCover
from custom_components.schellenberg_usb.event_entity import (
    SchellenbergRemoteEventEntity,
)

# The real remote/motor identifiers and frames from the debug log.
_REMOTE_ID = "7C055A"
_MOTOR_ID = "MOT001"
_MOTOR_ENUM = "33"

# First frame of each distinct press (distinct incrementor). The trailing byte
# after the command is the hold/repeat counter; only the first frame of each
# press survives dedup, so these are the frames that reach the handlers.
FRAME_DOWN = "ss337C055A021D8200CD"  # cmd=82, incr=021D  (1st press = DOWN)
FRAME_STOP = "ss337C055A001D8300D5"  # cmd=83, incr=001D  (2nd press = STOP)
FRAME_UP = "ss337C055A011D8400D1"  # cmd=84, incr=011D  (3rd press = UP/open)


# ---------------------------------------------------------------------------
# Cover position tracking via the REAL api._handle_message signal path
# ---------------------------------------------------------------------------


def _build_cover(hass: HomeAssistant, api: SchellenbergUsbApi) -> SchellenbergCover:
    """Timed cover bound to remote 7C055A, subscribed to the real signal."""
    cover = SchellenbergCover(
        api=api,
        device_id=_MOTOR_ID,
        device_enum=_MOTOR_ENUM,
        device_name="Shutter Living Room",
        device_data={
            CONF_BIDIRECTIONAL: False,
            CONF_REMOTE_ID: _REMOTE_ID,
        },
    )
    cover.hass = hass
    return cover


@pytest.mark.asyncio
async def test_remote_down_frame_starts_close(hass: HomeAssistant) -> None:
    """FRAME_DOWN (cmd=82) drives the cover into the closing state (RMT-04).

    Fails before the fix: 82 hit the "unknown remote command" else branch and
    left is_closing False.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote(_REMOTE_ID, _MOTOR_ID, _MOTOR_ENUM)
    cover = _build_cover(hass, api)
    cover._attr_current_cover_position = 80

    # Real subscription must register — do NOT patch async_dispatcher_connect.
    with patch.object(cover, "async_get_last_state", return_value=None):
        with patch.object(cover, "async_write_ha_state"):
            with patch(
                "homeassistant.helpers.issue_registry.async_create_issue"
            ):
                await cover.async_added_to_hass()

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            api._handle_message(FRAME_DOWN)

    assert cover._attr_is_closing is True
    assert cover._attr_is_opening is False
    assert cover._move_start_position == 80


@pytest.mark.asyncio
async def test_remote_up_frame_starts_open(hass: HomeAssistant) -> None:
    """FRAME_UP (cmd=84) drives the cover into the opening state (RMT-04).

    Fails before the fix: 84 hit the "unknown remote command" else branch.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote(_REMOTE_ID, _MOTOR_ID, _MOTOR_ENUM)
    cover = _build_cover(hass, api)
    cover._attr_current_cover_position = 20

    with patch.object(cover, "async_get_last_state", return_value=None):
        with patch.object(cover, "async_write_ha_state"):
            with patch(
                "homeassistant.helpers.issue_registry.async_create_issue"
            ):
                await cover.async_added_to_hass()

    with patch.object(cover, "_start_position_tracking"):
        with patch.object(cover, "async_write_ha_state"):
            api._handle_message(FRAME_UP)

    assert cover._attr_is_opening is True
    assert cover._attr_is_closing is False
    assert cover._move_start_position == 20


@pytest.mark.asyncio
async def test_remote_stop_frame_latches(hass: HomeAssistant) -> None:
    """FRAME_STOP (cmd=83) latches position and clears movement flags (RMT-05).

    Fails before the fix: 83 hit the "unknown remote command" else branch and
    never stopped tracking or latched.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote(_REMOTE_ID, _MOTOR_ID, _MOTOR_ENUM)
    cover = _build_cover(hass, api)
    cover._attr_is_opening = True
    cover._move_start_time = 12300.0
    cover._move_start_position = 30

    with patch.object(cover, "async_get_last_state", return_value=None):
        with patch.object(cover, "async_write_ha_state"):
            with patch(
                "homeassistant.helpers.issue_registry.async_create_issue"
            ):
                await cover.async_added_to_hass()

    with patch.object(cover, "_stop_position_tracking") as mock_stop:
        with patch.object(cover, "_update_position") as mock_update:
            with patch.object(cover, "async_write_ha_state"):
                api._handle_message(FRAME_STOP)

    mock_stop.assert_called_once()
    mock_update.assert_called_once()
    assert cover._attr_is_opening is False
    assert cover._attr_is_closing is False
    assert cover._target_position is None


# ---------------------------------------------------------------------------
# Event entity firing for handheld codes
# ---------------------------------------------------------------------------


@pytest.fixture
def event_api(hass: HomeAssistant) -> SchellenbergUsbApi:
    """Mock API for the event-entity firing tests."""
    api_mock = MagicMock(spec=SchellenbergUsbApi)
    api_mock.hass = hass
    api_mock.register_remote = MagicMock()
    api_mock.unregister_remote = MagicMock()
    return cast(SchellenbergUsbApi, api_mock)


def _make_event_entity(
    api: SchellenbergUsbApi,
) -> SchellenbergRemoteEventEntity:
    return SchellenbergRemoteEventEntity(
        api=api,
        device_id=_MOTOR_ID,
        device_enum=_MOTOR_ENUM,
        remote_id=_REMOTE_ID,
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("82", "down"),  # handheld DOWN
        ("83", "stop"),  # handheld STOP
        ("84", "up"),  # handheld UP/open
    ],
)
def test_handheld_code_fires_event(
    event_api: SchellenbergUsbApi, command: str, expected: str
) -> None:
    """Handheld codes 82/83/84 fire down/stop/up events (fails before fix).

    Before the fix REMOTE_EVENT_MAP.get(command) was None for 82/83/84 and the
    entity logged "ignoring unknown command" and fired nothing.
    """
    entity = _make_event_entity(event_api)
    fired: list[str] = []
    entity._trigger_event = lambda et: fired.append(et)  # type: ignore[method-assign]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

    entity._on_remote_event(command, 0.0)

    assert fired == [expected]
