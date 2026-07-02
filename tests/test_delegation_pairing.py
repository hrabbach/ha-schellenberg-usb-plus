"""Tests for the delegation-pairing subentry flow (Plan 14-02)."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.schellenberg_usb.api import DeviceLimitReached
from custom_components.schellenberg_usb.const import (
    CONF_BIDIRECTIONAL,
    CONF_DEVICE_ID,
    CONF_INITIAL_POSITION,
    CONF_SERIAL_PORT,
    DOMAIN,
    SUBENTRY_TYPE_BLIND,
)
from custom_components.schellenberg_usb.config_flow import (
    SchellenbergPairingSubentryFlow,
)


def _make_handler(
    hass: HomeAssistant, entry_id: str
) -> SchellenbergPairingSubentryFlow:
    """Create a flow handler bound to the given hub entry.

    ConfigSubentryFlow._get_entry() reads self.handler[0] (the entry_id
    portion of a (entry_id, subentry_type) tuple).
    async_create_entry requires source == 'user'.
    """
    handler = SchellenbergPairingSubentryFlow()
    handler.hass = hass
    handler.handler = (entry_id, SUBENTRY_TYPE_BLIND)
    handler.context = {"source": "user"}
    return handler


@pytest.fixture
def mock_hub_entry(hass: HomeAssistant) -> ConfigEntry:
    """Create a mock hub config entry."""
    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_delegation_flow_entry",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


def _make_mock_api(
    delegation_return: tuple[str, str] = ("10", "10"),
) -> MagicMock:
    """Create a shared MagicMock api parent with both delegation methods attached.

    Using a single MagicMock parent so mock_api.mock_calls records cross-method
    call order, enabling abort-before-delegation_pair ordering assertions.
    """
    mock_api = MagicMock()
    mock_api.delegation_pair = AsyncMock(return_value=delegation_return)
    mock_api.abort_delegation_pair = MagicMock()
    return mock_api


# ---------------------------------------------------------------------------
# Task 1 Tests — menu, instruction step, transmit step, error/retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_option_in_menu(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Menu must expose all three options: pair, manual_add, AND delegate."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_user(None)

    assert result["type"] == "menu"
    assert "pair" in result["menu_options"]
    assert "manual_add" in result["menu_options"]
    assert "delegate" in result["menu_options"]


@pytest.mark.asyncio
async def test_delegate_instruction_step_shows_form(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_delegate(None) returns a form with step_id 'delegate'."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_delegate(None)

    assert result["type"] == "form"
    assert result["step_id"] == "delegate"


@pytest.mark.asyncio
async def test_delegate_instruction_step_submit_advances_to_transmit(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Submitting the instruction step advances to the transmit form."""
    mock_api = _make_mock_api()
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_delegate({})

    assert result["type"] == "form"
    assert result["step_id"] == "delegate_transmit"


@pytest.mark.asyncio
async def test_delegate_transmit_shows_form_on_first_call(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_delegate_transmit(None) returns the transmit form."""
    mock_api = _make_mock_api()
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_delegate_transmit(None)

    assert result["type"] == "form"
    assert result["step_id"] == "delegate_transmit"


@pytest.mark.asyncio
async def test_delegate_transmit_success_advances_to_name(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """On delegation_pair success, transmit step advances to the name form.

    Also asserts: delegation_pair awaited once; abort_delegation_pair called.
    """
    mock_api = _make_mock_api(delegation_return=("10", "10"))
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_delegate_transmit({})

    assert result["type"] == "form"
    assert result["step_id"] == "delegate_name"
    mock_api.delegation_pair.assert_awaited_once()
    mock_api.abort_delegation_pair.assert_called_once()


@pytest.mark.asyncio
async def test_delegate_transmit_abort_called_before_delegation_pair(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """abort_delegation_pair() MUST be called BEFORE delegation_pair on every attempt.

    Blocker 4 (Plan 14-02): assert via mock_api.mock_calls cross-method call order.
    """
    mock_api = _make_mock_api(delegation_return=("10", "10"))
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)
    await handler.async_step_delegate_transmit({})

    calls = mock_api.mock_calls
    # Find index of abort and delegation_pair calls
    abort_indices = [
        i for i, c in enumerate(calls) if c == call.abort_delegation_pair()
    ]
    pair_indices = [i for i, c in enumerate(calls) if c == call.delegation_pair()]
    assert abort_indices, "abort_delegation_pair was never called"
    assert pair_indices, "delegation_pair was never called"
    # abort must precede delegation_pair on first attempt
    assert abort_indices[0] < pair_indices[0], (
        "abort_delegation_pair must be called BEFORE delegation_pair"
    )


@pytest.mark.asyncio
async def test_delegate_transmit_retry_abort_called_before_delegation_pair(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """D-09 Pitfall-11: on retry after ConnectionError, abort precedes pair again.

    Blocker 5 (Plan 14-02): drive ConnectionError on first call, then retry.
    assert abort_delegation_pair() is invoked BEFORE delegation_pair() on the
    retry path as well (not just on the first attempt).
    """
    mock_api = _make_mock_api()
    # First call raises ConnectionError, second call succeeds
    mock_api.delegation_pair = AsyncMock(
        side_effect=[ConnectionError("disconnected"), ("10", "10")]
    )
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    # First submit — raises ConnectionError → retry form shown
    result_1 = await handler.async_step_delegate_transmit({})
    assert result_1["type"] == "form"
    assert result_1["step_id"] == "delegate_transmit"
    assert (result_1.get("errors") or {}).get("base") == "delegation_failed"

    # Capture call order after first attempt
    calls_after_first = list(mock_api.mock_calls)

    # Second submit — succeeds → advances to name
    result_2 = await handler.async_step_delegate_transmit({})
    assert result_2["type"] == "form"
    assert result_2["step_id"] == "delegate_name"

    calls_all = mock_api.mock_calls

    # Find abort and pair indices for the RETRY (second attempt only)
    # calls_after_first has first-attempt calls; retry calls are the remainder
    retry_calls = list(calls_all[len(calls_after_first) :])
    abort_retry_indices = [
        i for i, c in enumerate(retry_calls) if c == call.abort_delegation_pair()
    ]
    pair_retry_indices = [
        i for i, c in enumerate(retry_calls) if c == call.delegation_pair()
    ]
    assert abort_retry_indices, "abort_delegation_pair not called on retry"
    assert pair_retry_indices, "delegation_pair not called on retry"
    assert abort_retry_indices[0] < pair_retry_indices[0], (
        "On retry, abort_delegation_pair must precede delegation_pair"
    )


@pytest.mark.asyncio
async def test_delegate_transmit_connection_error_retries_in_place(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """ConnectionError → re-shows delegate_transmit form with delegation_failed.

    Per D-09/PAIR-04: retry-in-place, NOT async_abort on ConnectionError.
    """
    mock_api = _make_mock_api()
    mock_api.delegation_pair = AsyncMock(
        side_effect=ConnectionError("serial disconnected")
    )
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_delegate_transmit({})

    assert result["type"] == "form"
    assert result["step_id"] == "delegate_transmit"
    assert (result.get("errors") or {}).get("base") == "delegation_failed"


@pytest.mark.asyncio
async def test_delegate_transmit_os_error_retries_in_place(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """OSError → also retries in place with delegation_failed (same branch)."""
    mock_api = _make_mock_api()
    mock_api.delegation_pair = AsyncMock(side_effect=OSError("os error"))
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_delegate_transmit({})

    assert result["type"] == "form"
    assert result["step_id"] == "delegate_transmit"
    assert (result.get("errors") or {}).get("base") == "delegation_failed"


@pytest.mark.asyncio
async def test_delegate_transmit_device_limit_reached_retries_in_place(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """DeviceLimitReached → re-shows delegate_transmit with device_limit_reached."""
    mock_api = _make_mock_api()
    mock_api.delegation_pair = AsyncMock(side_effect=DeviceLimitReached)
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_delegate_transmit({})

    assert result["type"] == "form"
    assert result["step_id"] == "delegate_transmit"
    assert (result.get("errors") or {}).get("base") == "device_limit_reached"


@pytest.mark.asyncio
async def test_subentry_flow_delegate_option_in_real_ha_seam(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """Real HA subentry-flow seam must expose 'delegate' in menu_options.

    Goes through hass.config_entries.subentries.async_init — the actual
    seam HA uses (per test_config_flow.py v1.1.0 regression postmortem).
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BLIND),
        context={"source": "user"},
    )

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "menu"
    assert "pair" in result["menu_options"]
    assert "manual_add" in result["menu_options"]
    assert "delegate" in result["menu_options"]


# ---------------------------------------------------------------------------
# Task 2 Tests — name step, position step, end-to-end zero-frame creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_name_step_shows_form(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_delegate_name(None) shows a form with step_id 'delegate_name'."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)
    handler._pending_device_enum = "10"

    result = await handler.async_step_delegate_name(None)

    assert result["type"] == "form"
    assert result["step_id"] == "delegate_name"


@pytest.mark.asyncio
async def test_delegate_name_submit_advances_to_position(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Submitting the name step advances to the position step."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)
    handler._pending_device_enum = "10"

    result = await handler.async_step_delegate_name({"device_name": "My Motor"})

    assert result["type"] == "form"
    assert result["step_id"] == "delegate_position"


@pytest.mark.asyncio
async def test_delegate_position_step_shows_form(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_delegate_position(None) shows a form with step_id 'delegate_position'."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)
    handler._pending_device_enum = "10"
    handler._pending_device_name = "My Motor"

    result = await handler.async_step_delegate_position(None)

    assert result["type"] == "form"
    assert result["step_id"] == "delegate_position"


@pytest.mark.asyncio
async def test_delegate_position_creates_entry(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Submitting the position step creates a timed subentry with correct data."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)
    handler._pending_device_enum = "10"
    handler._pending_device_name = "My Motor"

    result = await handler.async_step_delegate_position({"initial_position": 75})

    assert result["type"] == "create_entry"
    assert result["data"][CONF_BIDIRECTIONAL] is False
    assert result["data"][CONF_INITIAL_POSITION] == 75
    assert result["data"][CONF_DEVICE_ID] == "10"
    assert result["data"]["device_enum"] == "10"
    assert result.get("unique_id") == "10"


@pytest.mark.asyncio
async def test_delegate_position_missing_enum_aborts(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """If _pending_device_enum is unset, position step aborts with pairing_failed."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)
    # _pending_device_enum deliberately NOT set

    result = await handler.async_step_delegate_position(None)

    assert result["type"] == "abort"
    assert result.get("reason") == "pairing_failed"


@pytest.mark.asyncio
async def test_delegate_name_default_fallback(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """If no name submitted, device_name falls back to 'Blind {enum}'."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)
    handler._pending_device_enum = "1A"

    # Submit name step with empty name
    await handler.async_step_delegate_name({"device_name": ""})
    # Advance: position step submission
    handler._pending_device_enum = "1A"  # ensure still set
    result = await handler.async_step_delegate_position({"initial_position": 100})

    assert result["type"] == "create_entry"
    # title should be the fallback name
    assert "1A" in result.get("title", "")


@pytest.mark.asyncio
async def test_delegate_end_to_end_zero_frame_creation(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Full delegate → transmit → name → position walk creates a timed subentry.

    Zero-frame path (PAIR-03): delegation_pair() is an AsyncMock returning
    (enum, enum) with no inbound frame ever resolved.
    Asserts delegate_name and delegate_position step_ids in order.
    Asserts create_entry data shape (CONF_BIDIRECTIONAL=False, matching enum keys).
    """
    mock_api = _make_mock_api(delegation_return=("10", "10"))
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    # Step 1: instruction form
    result = await handler.async_step_delegate(None)
    assert result["type"] == "form"
    assert result["step_id"] == "delegate"

    # Step 2: submit instruction → transmit form
    result = await handler.async_step_delegate({})
    assert result["type"] == "form"
    assert result["step_id"] == "delegate_transmit"

    # Step 3: submit transmit → name form
    result = await handler.async_step_delegate_transmit({})
    assert result["type"] == "form"
    assert result["step_id"] == "delegate_name"

    # Step 4: submit name → position form
    result = await handler.async_step_delegate_name({"device_name": "Motor A"})
    assert result["type"] == "form"
    assert result["step_id"] == "delegate_position"

    # Step 5: submit position → create entry
    result = await handler.async_step_delegate_position({"initial_position": 50})
    assert result["type"] == "create_entry"
    assert result["data"][CONF_BIDIRECTIONAL] is False
    assert result["data"][CONF_DEVICE_ID] == "10"
    assert result["data"]["device_enum"] == "10"
    assert result["data"][CONF_INITIAL_POSITION] == 50
    assert result.get("unique_id") == "10"

    # delegation_pair was called, abort_delegation_pair was called first
    mock_api.delegation_pair.assert_awaited_once()
    mock_api.abort_delegation_pair.assert_called()
