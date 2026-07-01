"""Tests for remote button event entity — EVT-01, EVT-02, SC#3, D-04."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    DOMAIN,
    REMOTE_EVENT_MAP,
)
from custom_components.schellenberg_usb.event_entity import (
    SchellenbergRemoteEventEntity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_api(hass: HomeAssistant) -> SchellenbergUsbApi:
    """Create a mock API for event entity tests."""
    api_mock = MagicMock(spec=SchellenbergUsbApi)
    api_mock.hass = hass
    api_mock.register_remote = MagicMock()
    api_mock.unregister_remote = MagicMock()
    return cast(SchellenbergUsbApi, api_mock)


def _make_entity(api: SchellenbergUsbApi) -> SchellenbergRemoteEventEntity:
    """Construct a test event entity with fixed identifiers."""
    return SchellenbergRemoteEventEntity(
        api=api,
        device_id="ABC123",
        device_enum="10",
        remote_id="REM001",
    )


# ---------------------------------------------------------------------------
# EVT-01a..e: one event per command byte, correct mapping, no folding
# ---------------------------------------------------------------------------


def test_cmd_up_fires_up_event(mock_api: SchellenbergUsbApi) -> None:
    """EVT-01a: _on_remote_event('01', 0.0) fires exactly one 'up' event."""
    entity = _make_entity(mock_api)
    fired: list[str] = []
    entity._trigger_event = lambda et: fired.append(et)  # type: ignore[method-assign]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    entity._on_remote_event("01", 0.0)
    assert fired == ["up"]


def test_cmd_down_fires_down_event(mock_api: SchellenbergUsbApi) -> None:
    """EVT-01b: _on_remote_event('02', 0.0) fires exactly one 'down' event."""
    entity = _make_entity(mock_api)
    fired: list[str] = []
    entity._trigger_event = lambda et: fired.append(et)  # type: ignore[method-assign]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    entity._on_remote_event("02", 0.0)
    assert fired == ["down"]


def test_cmd_stop_fires_stop_event(mock_api: SchellenbergUsbApi) -> None:
    """EVT-01c: _on_remote_event('00', 0.0) fires exactly one 'stop' event."""
    entity = _make_entity(mock_api)
    fired: list[str] = []
    entity._trigger_event = lambda et: fired.append(et)  # type: ignore[method-assign]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    entity._on_remote_event("00", 0.0)
    assert fired == ["stop"]


def test_cmd_hold_up_fires_hold_up_event(mock_api: SchellenbergUsbApi) -> None:
    """EVT-01d: _on_remote_event('41', 0.0) fires 'hold_up' — NOT folded to 'up'."""
    entity = _make_entity(mock_api)
    fired: list[str] = []
    entity._trigger_event = lambda et: fired.append(et)  # type: ignore[method-assign]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    entity._on_remote_event("41", 0.0)
    assert fired == ["hold_up"]
    assert "up" not in fired


def test_cmd_hold_down_fires_hold_down_event(mock_api: SchellenbergUsbApi) -> None:
    """EVT-01e: _on_remote_event('42', 0.0) fires 'hold_down' — NOT folded to 'down'."""
    entity = _make_entity(mock_api)
    fired: list[str] = []
    entity._trigger_event = lambda et: fired.append(et)  # type: ignore[method-assign]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    entity._on_remote_event("42", 0.0)
    assert fired == ["hold_down"]
    assert "down" not in fired


# ---------------------------------------------------------------------------
# EVT-01 unknown byte: no event fired for unrecognised commands
# ---------------------------------------------------------------------------


def test_unknown_command_fires_nothing(mock_api: SchellenbergUsbApi) -> None:
    """EVT-01 unknown: _on_remote_event('99', 0.0) must fire nothing."""
    entity = _make_entity(mock_api)
    fired: list[str] = []
    entity._trigger_event = lambda et: fired.append(et)  # type: ignore[method-assign]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    entity._on_remote_event("99", 0.0)
    assert fired == []
    entity.async_write_ha_state.assert_not_called()


# ---------------------------------------------------------------------------
# EVT-01h: exactly 5 event types declared
# ---------------------------------------------------------------------------


def test_event_types_list(mock_api: SchellenbergUsbApi) -> None:
    """EVT-01h: _attr_event_types contains exactly the 5 declared types."""
    entity = _make_entity(mock_api)
    assert entity._attr_event_types == [
        "up",
        "down",
        "stop",
        "hold_up",
        "hold_down",
    ]
    assert len(entity._attr_event_types) == 5


# ---------------------------------------------------------------------------
# EVT-01g: EventEntity only — no RestoreEntity in the MRO (SC #4)
# ---------------------------------------------------------------------------


def test_no_restore_entity_explicit_in_bases() -> None:
    """EVT-01g: SchellenbergRemoteEventEntity must NOT declare RestoreEntity in its
    direct bases (SC #4 — no spurious automation fire on HA restart).

    NOTE: HA's EventEntity itself inherits from RestoreEntity internally (HA
    2025.x+), but it does NOT call async_get_last_state() — EventEntity is
    stateless by design.  The constraint here is that OUR class must not add an
    additional explicit RestoreEntity base, and must not call
    async_get_last_state() in async_added_to_hass.  We verify the direct
    __bases__ rather than the full MRO so the test is not broken by the HA
    framework's own internal inheritance structure.
    """
    from homeassistant.components.event import EventEntity
    from homeassistant.helpers.restore_state import RestoreEntity

    # Our class must declare only EventEntity as a base (not RestoreEntity)
    assert SchellenbergRemoteEventEntity.__bases__ == (EventEntity,)
    # Double-check: RestoreEntity is NOT in our direct bases
    assert RestoreEntity not in SchellenbergRemoteEventEntity.__bases__


# ---------------------------------------------------------------------------
# EVT-02a: shared device card (identifiers only, no name/manufacturer/model)
# ---------------------------------------------------------------------------


def test_device_info_identifiers(mock_api: SchellenbergUsbApi) -> None:
    """EVT-02a: device_info has only the identifiers tuple — no duplicate device."""
    entity = _make_entity(mock_api)
    assert entity._attr_device_info is not None
    assert entity._attr_device_info.get("identifiers") == {(DOMAIN, "ABC123")}
    # name/manufacturer/model must be absent so we don't create a duplicate device
    assert entity._attr_device_info.get("name") is None
    assert entity._attr_device_info.get("manufacturer") is None
    assert entity._attr_device_info.get("model") is None


# ---------------------------------------------------------------------------
# EVT-02b: button device class
# ---------------------------------------------------------------------------


def test_device_class_is_button(mock_api: SchellenbergUsbApi) -> None:
    """EVT-02b: device_class must be EventDeviceClass.BUTTON."""
    from homeassistant.components.event import EventDeviceClass

    entity = _make_entity(mock_api)
    assert entity._attr_device_class == EventDeviceClass.BUTTON


# ---------------------------------------------------------------------------
# REMOTE_EVENT_MAP consistency: stick-transmit codes present
# ---------------------------------------------------------------------------


def test_remote_event_map_entries() -> None:
    """REMOTE_EVENT_MAP maps the stick-transmit codes.

    5 codes: 00/01/02 taps + 41/42 jogs. A bound handheld remote's presses
    decode to these SAME codes (command at frame [10:12]); there is no separate
    handheld code space (the v1.2.3 82/83/84 mapping was a mis-sliced rolling
    counter — see debug/resolved/remote-incrementing-cmd-codes.md).
    """
    from custom_components.schellenberg_usb.const import (
        CMD_DOWN,
        CMD_MANUAL_DOWN,
        CMD_MANUAL_UP,
        CMD_STOP,
        CMD_UP,
    )

    assert len(REMOTE_EVENT_MAP) == 5
    assert set(REMOTE_EVENT_MAP.values()) == {
        "up",
        "down",
        "stop",
        "hold_up",
        "hold_down",
    }
    assert REMOTE_EVENT_MAP[CMD_UP] == "up"
    assert REMOTE_EVENT_MAP[CMD_DOWN] == "down"
    assert REMOTE_EVENT_MAP[CMD_STOP] == "stop"
    assert REMOTE_EVENT_MAP[CMD_MANUAL_UP] == "hold_up"
    assert REMOTE_EVENT_MAP[CMD_MANUAL_DOWN] == "hold_down"


# ---------------------------------------------------------------------------
# D-04 interleaved cover+event lifecycle (review finding #4)
# Tests the REAL api.py ref-count from Plan 01 end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interleaved_cover_event_lifecycle(
    hass: HomeAssistant,
) -> None:
    """D-04 end-to-end: cover + event co-ownership lifecycle across ref-counted api.

    Uses a REAL SchellenbergUsbApi (not a MagicMock) to exercise the actual
    _remote_ref_counts from Plan 01.

    Ordering:
    (1) cover registers   → ref=1, mapping present
    (2) event registers   → ref=2, mapping present
    (3) cover unregisters → ref=1, mapping STILL present (the regression guard)
    (4) event unregisters → ref=0, mapping fully gone
    """
    remote_id = "REM001"
    motor_id = "MOT001"
    motor_enum = "10"

    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    # (1) Cover registers (async_added_to_hass on the cover entity)
    api.register_remote(remote_id, motor_id, motor_enum)
    assert api._remote_to_motor.get(remote_id) == motor_id
    assert api._remote_ref_counts.get(remote_id) == 1

    # (2) Event entity registers (async_added_to_hass on the event entity)
    api.register_remote(remote_id, motor_id, motor_enum)
    assert api._remote_to_motor.get(remote_id) == motor_id
    assert api._remote_ref_counts.get(remote_id) == 2

    # (3) Cover is removed first — mapping must STILL route for the event entity
    api.unregister_remote(remote_id)
    assert api._remote_to_motor.get(remote_id) == motor_id, (
        "Mapping removed after first unregister — D-04 ref-count broken"
    )
    assert api._remote_ref_counts.get(remote_id) == 1

    # (4) Event entity removed — now fully torn down
    api.unregister_remote(remote_id)
    assert api._remote_to_motor.get(remote_id) is None
    assert api._registered_devices.get(remote_id) is None
    assert api._remote_ref_counts.get(remote_id) is None
