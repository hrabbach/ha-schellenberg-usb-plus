"""Tests for event platform setup — GUARD 1 (SC#3), GUARD 2 (T-13-10), and positive cases."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    CONF_BIDIRECTIONAL,
    CONF_REMOTE_ENUM,
    CONF_REMOTE_ID,
    CONF_SERIAL_PORT,
    DOMAIN,
    SchellenbergConfigEntry,
    SUBENTRY_TYPE_LED,
)
from custom_components.schellenberg_usb.event import async_setup_entry
from custom_components.schellenberg_usb.event_entity import (
    SchellenbergRemoteEventEntity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_api() -> SchellenbergUsbApi:
    """Create a mock SchellenbergUsbApi."""
    api = MagicMock(spec=SchellenbergUsbApi)
    api.register_remote = MagicMock()
    api.unregister_remote = MagicMock()
    return cast(SchellenbergUsbApi, api)


def _make_subentry(
    subentry_id: str,
    subentry_type: str = "blind",
    device_id: str | None = "ABC123",
    device_enum: str | None = "10",
    remote_id: str | None = "REM001",
    bidirectional: bool | None = None,
    remote_enum: str | None = None,
) -> MagicMock:
    """Construct a test subentry with specified properties.

    remote_enum defaults to None (absent), modelling a legacy single-channel
    bind.  Pass a 2-char hex string (e.g. "10") for a v1.4 multi-channel bind.
    """
    data: dict[str, Any] = {}
    if device_id is not None:
        data["device_id"] = device_id
    if device_enum is not None:
        data["device_enum"] = device_enum
    if remote_id is not None:
        data[CONF_REMOTE_ID] = remote_id
    if bidirectional is not None:
        data[CONF_BIDIRECTIONAL] = bidirectional
    if remote_enum is not None:
        data[CONF_REMOTE_ENUM] = remote_enum

    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = subentry_type
    subentry.data = data
    return subentry


def _make_hub_entry(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi, subentries: list[MagicMock]
) -> SchellenbergConfigEntry:
    """Construct a test hub config entry with subentries."""
    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="hub-entry-001",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    # Mock the subentries property with a MappingProxyType like the real HA does
    entry.subentries = MappingProxyType({sub.subentry_id: sub for sub in subentries})  # type: ignore[assignment]
    entry.runtime_data = mock_api
    return cast(SchellenbergConfigEntry, entry)


# ---------------------------------------------------------------------------
# Positive case: TIMED + bound motor → exactly one entity created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timed_bound_motor_creates_event_entity(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> None:
    """EVT-01/EVT-02: A TIMED (non-bidirectional) motor with remote_id creates
    exactly ONE SchellenbergRemoteEventEntity and calls async_add_entities once.
    """
    # Setup: one timed+bound motor subentry (bidirectional not set, defaults to True,
    # but we explicitly set it False to be a timed motor)
    subentry = _make_subentry(
        subentry_id="sub-001",
        device_id="ABC123",
        device_enum="10",
        remote_id="REM001",
        bidirectional=False,
    )
    entry = _make_hub_entry(hass, mock_api, [subentry])

    # Capture async_add_entities calls
    mock_add_entities: MagicMock = MagicMock()

    # Execute
    await async_setup_entry(hass, entry, mock_add_entities)  # type: ignore[arg-type]  # type: ignore[arg-type]

    # Assert: exactly one call to async_add_entities with one entity
    mock_add_entities.assert_called_once()
    entities = mock_add_entities.call_args[0][0]
    assert len(entities) == 1
    entity = entities[0]
    assert isinstance(entity, SchellenbergRemoteEventEntity)
    assert entity._device_id == "ABC123"
    assert entity._device_enum == "10"
    assert entity._remote_id == "REM001"
    # LEGACY bind: no CONF_REMOTE_ENUM in subentry → remote_enum=None forwarded.
    # Verifies that a CONF_REMOTE_ENUM-absent subentry registers with remote_enum=None
    # and does NOT gate GUARD 1 (plan acceptance criterion).
    assert entity._remote_enum is None
    # Assert config_subentry_id was passed (groups entity under motor device)
    config_subentry_id = mock_add_entities.call_args[1]["config_subentry_id"]
    assert config_subentry_id == "sub-001"


# ---------------------------------------------------------------------------
# GUARD 1 (SC#3): no remote_id binding → no entity created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard1_motor_without_remote_id_skipped(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> None:
    """SC#3 GUARD 1: A motor WITHOUT a remote_id binding is skipped —
    async_add_entities is NOT called for it.
    """
    # Setup: timed motor but NO remote_id (remote_id=None)
    subentry = _make_subentry(
        subentry_id="sub-001",
        device_id="ABC123",
        device_enum="10",
        remote_id=None,  # Missing binding
        bidirectional=False,
    )
    entry = _make_hub_entry(hass, mock_api, [subentry])

    mock_add_entities = MagicMock()

    # Execute
    await async_setup_entry(hass, entry, mock_add_entities)  # type: ignore[arg-type]

    # Assert: no entity added
    mock_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_guard1_motor_without_device_id_skipped(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> None:
    """SC#3 GUARD 1: A motor WITHOUT device_id is skipped."""
    subentry = _make_subentry(
        subentry_id="sub-001",
        device_id=None,  # Missing device_id
        device_enum="10",
        remote_id="REM001",
        bidirectional=False,
    )
    entry = _make_hub_entry(hass, mock_api, [subentry])

    mock_add_entities: MagicMock = MagicMock()

    await async_setup_entry(hass, entry, mock_add_entities)  # type: ignore[arg-type]
    mock_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_guard1_motor_without_device_enum_skipped(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> None:
    """SC#3 GUARD 1: A motor WITHOUT device_enum is skipped."""
    subentry = _make_subentry(
        subentry_id="sub-001",
        device_id="ABC123",
        device_enum=None,  # Missing device_enum
        remote_id="REM001",
        bidirectional=False,
    )
    entry = _make_hub_entry(hass, mock_api, [subentry])

    mock_add_entities: MagicMock = MagicMock()

    await async_setup_entry(hass, entry, mock_add_entities)  # type: ignore[arg-type]
    mock_add_entities.assert_not_called()


# ---------------------------------------------------------------------------
# GUARD 2 (T-13-10): bidirectional motors are EXCLUDED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard2_bidirectional_motor_skipped(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> None:
    """T-13-10 / Option A GUARD 2: A bidirectional motor (is_bidirectional=True)
    with a remote_id is EXCLUDED — no event entity created, no register_remote called.
    """
    # Setup: bidirectional motor WITH remote_id
    # (Option A guard prevents registering remote for bidirectional motors)
    subentry = _make_subentry(
        subentry_id="sub-001",
        device_id="ABC123",
        device_enum="10",
        remote_id="REM001",
        bidirectional=True,  # BIDIRECTIONAL → should be skipped
    )
    entry = _make_hub_entry(hass, mock_api, [subentry])

    mock_add_entities: MagicMock = MagicMock()

    # Execute
    await async_setup_entry(hass, entry, mock_add_entities)  # type: ignore[arg-type]

    # Assert: no entity added, no register_remote called
    mock_add_entities.assert_not_called()
    mock_api.register_remote.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_guard2_legacy_subentry_defaults_bidirectional(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> None:
    """T-13-10 / Option A GUARD 2: A legacy subentry with NO CONF_BIDIRECTIONAL key
    defaults to bidirectional=True (Option A logic) and is therefore EXCLUDED.

    This tests the default-to-True behavior: `bool(data.get(CONF_BIDIRECTIONAL, True))`
    evaluates to True when the key is absent, so a legacy motor without any
    bidirectional flag is treated as bidirectional and skipped.
    """
    # Setup: subentry with remote_id but NO bidirectional flag in data
    # (legacy subentry — assume bidirectional=True by default)
    subentry = _make_subentry(
        subentry_id="sub-001",
        device_id="ABC123",
        device_enum="10",
        remote_id="REM001",
        bidirectional=None,  # Omitted from data → defaults to True
    )
    entry = _make_hub_entry(hass, mock_api, [subentry])

    mock_add_entities = MagicMock()

    # Execute
    await async_setup_entry(hass, entry, mock_add_entities)  # type: ignore[arg-type]

    # Assert: legacy motor treated as bidirectional → no entity added
    mock_add_entities.assert_not_called()
    mock_api.register_remote.assert_not_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# LED subentry: always skipped (handled by switch platform)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_led_subentry_always_skipped(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> None:
    """LED subentries are skipped regardless of remote_id or bidirectional flag."""
    # Setup: LED subentry with remote_id and timed (bidirectional=False)
    subentry = _make_subentry(
        subentry_id="sub-led",
        subentry_type=SUBENTRY_TYPE_LED,  # LED type
        device_id="LED001",
        device_enum="00",
        remote_id="REM001",
        bidirectional=False,
    )
    entry = _make_hub_entry(hass, mock_api, [subentry])

    mock_add_entities = MagicMock()

    # Execute
    await async_setup_entry(hass, entry, mock_add_entities)  # type: ignore[arg-type]

    # Assert: LED skipped, no entity added
    mock_add_entities.assert_not_called()


# ---------------------------------------------------------------------------
# Mixed subentries: combination of timed+bound, unbound, bidirectional, LED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_subentries_filtered_correctly(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> None:
    """Complex scenario: multiple subentries of different types, only timed+bound
    motors produce event entities.

    Setup:
    - sub-001: timed+bound → entity created
    - sub-002: timed but NO remote_id → skipped (GUARD 1)
    - sub-003: bidirectional with remote_id → skipped (GUARD 2)
    - sub-led: LED type → skipped
    - sub-005: legacy (no CONF_BIDIRECTIONAL key, defaults True) → skipped

    Expected: exactly 1 entity from sub-001.
    """
    subentries = [
        _make_subentry(
            subentry_id="sub-001",
            device_id="MOTOR1",
            device_enum="10",
            remote_id="REM001",
            bidirectional=False,  # TIMED + BOUND → entity created
        ),
        _make_subentry(
            subentry_id="sub-002",
            device_id="MOTOR2",
            device_enum="11",
            remote_id=None,  # GUARD 1: no binding
            bidirectional=False,
        ),
        _make_subentry(
            subentry_id="sub-003",
            device_id="MOTOR3",
            device_enum="12",
            remote_id="REM003",
            bidirectional=True,  # GUARD 2: bidirectional
        ),
        _make_subentry(
            subentry_id="sub-led",
            subentry_type=SUBENTRY_TYPE_LED,
            device_id="LED001",
            device_enum="00",
            remote_id="REM004",
            bidirectional=False,
        ),
        _make_subentry(
            subentry_id="sub-005",
            device_id="MOTOR5",
            device_enum="15",
            remote_id="REM005",
            bidirectional=None,  # No CONF_BIDIRECTIONAL → defaults True (GUARD 2)
        ),
    ]
    entry = _make_hub_entry(hass, mock_api, subentries)

    mock_add_entities = MagicMock()

    # Execute
    await async_setup_entry(hass, entry, mock_add_entities)  # type: ignore[arg-type]

    # Assert: exactly 1 entity from sub-001
    assert mock_add_entities.call_count == 1
    entities = mock_add_entities.call_args[0][0]
    assert len(entities) == 1
    entity = entities[0]
    assert entity._device_id == "MOTOR1"
    assert entity._remote_id == "REM001"
    assert entity._remote_enum is None  # subentry has no CONF_REMOTE_ENUM → legacy
    config_subentry_id = mock_add_entities.call_args[1]["config_subentry_id"]
    assert config_subentry_id == "sub-001"


# ---------------------------------------------------------------------------
# Hub-guard: non-hub entries (missing CONF_SERIAL_PORT) are skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_hub_entry_skipped(
    hass: HomeAssistant, mock_api: SchellenbergUsbApi
) -> None:
    """Hub-guard: entries without CONF_SERIAL_PORT are not hubs; event platform
    should return early without processing subentries.
    """
    # Setup: a non-hub entry (no CONF_SERIAL_PORT in data)
    subentry = _make_subentry(
        subentry_id="sub-001",
        device_id="MOTOR1",
        device_enum="10",
        remote_id="REM001",
        bidirectional=False,
    )
    entry = MagicMock(spec=SchellenbergConfigEntry)
    entry.entry_id = "non-hub-entry"
    entry.data = {}  # No CONF_SERIAL_PORT — NOT a hub
    entry.runtime_data = mock_api
    entry.subentries = {"sub-001": subentry}

    mock_add_entities = MagicMock()

    # Execute
    await async_setup_entry(hass, entry, mock_add_entities)  # type: ignore[arg-type]

    # Assert: no entities added (hub-guard returned early)
    mock_add_entities.assert_not_called()
