"""Tests for the blind subentry manual-add flow."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.const import (
    CONF_BIDIRECTIONAL,
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

    ConfigSubentryFlow._get_entry() reads self.handler[0] (the entry_id portion of
    a (entry_id, subentry_type) tuple). async_create_entry requires source == 'user'.
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
        entry_id="test_manual_flow_entry",
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
async def test_manual_add_menu_shown(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """The flow entry step (async_step_user) shows the menu.

    Regression: HA initiates user-triggered subentry flows via async_step_user,
    NOT async_step_{subentry_type}. The menu must therefore be served from
    async_step_user, with options routing to async_step_pair / async_step_manual_add.
    """
    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_user(None)

    assert result["type"] == "menu"
    assert "pair" in result["menu_options"]
    assert "manual_add" in result["menu_options"]


@pytest.mark.asyncio
async def test_pair_step_shows_form(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Selecting 'Pair automatically' (async_step_pair) shows the pairing form."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_pair(None)

    assert result["type"] == "form"
    assert result["step_id"] == "pair"


@pytest.mark.asyncio
async def test_manual_add_creates_subentry(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Bidirectional manual-add with a valid enum creates an entry without api.pair_device_and_wait."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)

    mock_api = MagicMock()
    mock_api.pair_device_and_wait = AsyncMock()
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    result = await handler.async_step_manual_add(
        {
            "device_enum": "1A",
            CONF_BIDIRECTIONAL: True,
            "device_name": "Test Blind",
        }
    )

    assert result["type"] == "create_entry"
    mock_api.pair_device_and_wait.assert_not_called()


@pytest.mark.asyncio
async def test_manual_add_mode_flag(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Timed selection stores CONF_BIDIRECTIONAL False; bidirectional stores True."""
    # Timed: goes through position step
    handler_timed = _make_handler(hass, mock_hub_entry.entry_id)

    result_timed = await handler_timed.async_step_manual_add(
        {
            "device_enum": "2B",
            CONF_BIDIRECTIONAL: False,
            "device_name": "Timed Blind",
        }
    )
    # Timed goes to position step first
    assert result_timed["type"] == "form"
    assert result_timed["step_id"] == "manual_position"

    # Complete timed: submit position step
    result_timed_entry = await handler_timed.async_step_manual_position(
        {"initial_position": 100}
    )
    assert result_timed_entry["type"] == "create_entry"
    assert result_timed_entry["data"][CONF_BIDIRECTIONAL] is False

    # Bidirectional: goes straight to create_entry
    handler_bi = _make_handler(hass, mock_hub_entry.entry_id)

    result_bi = await handler_bi.async_step_manual_add(
        {
            "device_enum": "3C",
            CONF_BIDIRECTIONAL: True,
            "device_name": "Bi Blind",
        }
    )
    assert result_bi["type"] == "create_entry"
    assert result_bi["data"][CONF_BIDIRECTIONAL] is True


@pytest.mark.asyncio
async def test_manual_add_invalid_enum(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Inputs 'XY', '100', '' each return form errors with invalid_enum_format."""
    for bad_input in ("XY", "100", ""):
        handler = _make_handler(hass, mock_hub_entry.entry_id)

        result = await handler.async_step_manual_add(
            {
                "device_enum": bad_input,
                CONF_BIDIRECTIONAL: False,
                "device_name": "",
            }
        )
        assert result["type"] == "form", (
            f"Expected form for input {bad_input!r}, got {result['type']!r}"
        )
        assert result["errors"].get("device_enum") == "invalid_enum_format", (
            f"Expected invalid_enum_format for input {bad_input!r}, "
            f"got {result['errors']!r}"
        )


@pytest.mark.asyncio
async def test_manual_add_duplicate_enum(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """An enum already present in a blind subentry returns duplicate_enum error."""
    existing_sub = MagicMock()
    existing_sub.subentry_type = SUBENTRY_TYPE_BLIND
    existing_sub.data = {"device_enum": "1A", "device_id": "1A"}
    mock_hub_entry.subentries = MappingProxyType({"existing": existing_sub})  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_manual_add(
        {
            "device_enum": "1A",
            CONF_BIDIRECTIONAL: False,
            "device_name": "",
        }
    )
    assert result["type"] == "form"
    assert result["errors"].get("device_enum") == "duplicate_enum"


@pytest.mark.asyncio
async def test_manual_add_device_name(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Provided name becomes the create_entry title; omitted name falls back to 'Blind {ENUM}'."""
    # With name
    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_manual_add(
        {
            "device_enum": "1A",
            CONF_BIDIRECTIONAL: True,
            "device_name": "My Living Room Blind",
        }
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "My Living Room Blind"

    # Without name — falls back to "Blind {ENUM}"
    handler2 = _make_handler(hass, mock_hub_entry.entry_id)

    result2 = await handler2.async_step_manual_add(
        {
            "device_enum": "2B",
            CONF_BIDIRECTIONAL: True,
            "device_name": "",
        }
    )
    assert result2["type"] == "create_entry"
    assert result2["title"] == "Blind 2B"


@pytest.mark.asyncio
async def test_manual_add_position_step_timed_only(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Timed mode advances to manual_position form; bidirectional goes straight to create_entry."""
    # Timed: should show position form
    handler_timed = _make_handler(hass, mock_hub_entry.entry_id)

    result_timed = await handler_timed.async_step_manual_add(
        {
            "device_enum": "1A",
            CONF_BIDIRECTIONAL: False,
            "device_name": "Timed",
        }
    )
    assert result_timed["type"] == "form"
    assert result_timed["step_id"] == "manual_position"

    # Bidirectional: should go directly to create_entry
    handler_bi = _make_handler(hass, mock_hub_entry.entry_id)

    result_bi = await handler_bi.async_step_manual_add(
        {
            "device_enum": "2B",
            CONF_BIDIRECTIONAL: True,
            "device_name": "Bi",
        }
    )
    assert result_bi["type"] == "create_entry"


@pytest.mark.asyncio
async def test_reconfigure_timed_motor_aborts(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Reconfigure on a timed subentry aborts with timed_calibration_unavailable.

    A bidirectional subentry still reaches the calibration path (form, not abort).
    """
    from custom_components.schellenberg_usb.options_flow_calibration import (
        CalibrationFlowHandler,
    )

    # --- Timed motor: must abort ---
    timed_subentry = MagicMock()
    timed_subentry.data = {
        "device_id": "1A",
        "device_enum": "1A",
        CONF_BIDIRECTIONAL: False,
    }
    timed_subentry.title = "Timed Blind"

    handler_timed = _make_handler(hass, mock_hub_entry.entry_id)

    with patch.object(
        handler_timed, "_get_reconfigure_subentry", return_value=timed_subentry
    ):
        with patch.object(
            CalibrationFlowHandler,
            "async_step_calibration_close",
            new_callable=AsyncMock,
        ) as mock_cal_step:
            result = await handler_timed.async_step_reconfigure(None)

    assert result["type"] == "abort", (
        f"Expected abort for timed motor, got {result['type']!r}"
    )
    assert result["reason"] == "timed_calibration_unavailable", (
        f"Expected timed_calibration_unavailable, got {result['reason']!r}"
    )
    # The event-waiting calibration step must NOT have been called
    mock_cal_step.assert_not_called()

    # --- Bidirectional motor: must reach calibration path (form, not abort) ---
    bi_subentry = MagicMock()
    bi_subentry.data = {
        "device_id": "ABC123",
        "device_enum": "10",
        CONF_BIDIRECTIONAL: True,
        "open_time": 20.0,
        "close_time": 18.0,
    }
    bi_subentry.title = "Bi Blind"

    mock_api = MagicMock()
    mock_api.control_blind = AsyncMock()
    mock_hub_entry.runtime_data = mock_api  # type: ignore[attr-defined]

    handler_bi = _make_handler(hass, mock_hub_entry.entry_id)

    with patch.object(
        handler_bi, "_get_reconfigure_subentry", return_value=bi_subentry
    ):
        with patch.object(
            CalibrationFlowHandler,
            "async_step_calibration_close",
            new_callable=AsyncMock,
            return_value={"type": "form", "step_id": "calibration_close"},
        ):
            result_bi = await handler_bi.async_step_reconfigure(None)

    # Bidirectional reconfigure should NOT be an abort with timed reason
    assert result_bi.get("reason") != "timed_calibration_unavailable", (
        "Bidirectional motor should not abort with timed_calibration_unavailable"
    )


@pytest.mark.asyncio
async def test_manual_add_enum_case_normalized(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Lowercase '1a' collides with existing '1A' after .upper() normalization (Pitfall 4)."""
    existing_sub = MagicMock()
    existing_sub.subentry_type = SUBENTRY_TYPE_BLIND
    existing_sub.data = {"device_enum": "1A", "device_id": "1A"}
    mock_hub_entry.subentries = MappingProxyType({"existing": existing_sub})  # type: ignore[attr-defined]

    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_manual_add(
        {
            "device_enum": "1a",  # lowercase — must be normalized before dedup check
            CONF_BIDIRECTIONAL: False,
            "device_name": "",
        }
    )
    assert result["type"] == "form"
    assert result["errors"].get("device_enum") == "duplicate_enum", (
        "Expected duplicate_enum: '1a'.upper() == '1A' which already exists"
    )


@pytest.mark.asyncio
async def test_manual_add_enum_stored_uppercase(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Submitting '2b' stores device_enum/device_id as '2B' (uppercase) in the entry."""
    handler = _make_handler(hass, mock_hub_entry.entry_id)

    result = await handler.async_step_manual_add(
        {
            "device_enum": "2b",
            CONF_BIDIRECTIONAL: True,
            "device_name": "Uppercase Test",
        }
    )
    assert result["type"] == "create_entry"
    assert result["data"]["device_enum"] == "2B", (
        f"Expected device_enum '2B', got {result['data']['device_enum']!r}"
    )
    assert result["data"]["device_id"] == "2B", (
        f"Expected device_id '2B', got {result['data']['device_id']!r}"
    )


@pytest.mark.asyncio
async def test_manual_position_aborts_without_pending_state(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_manual_position aborts when _pending_device_enum is missing (WR-01).

    This guard prevents a broken subentry from being created if the step is
    reached without async_step_manual_add having run first (HA re-entrancy,
    serialized-flow resume, or future menu wiring change).
    """
    handler = _make_handler(hass, mock_hub_entry.entry_id)
    # _pending_device_enum is None by default -- do not call manual_add first

    result = await handler.async_step_manual_position(None)

    assert result["type"] == "abort", (
        f"Expected abort when pending state is missing, got {result['type']!r}"
    )
    assert result["reason"] == "pairing_failed", (
        f"Expected reason='pairing_failed', got {result.get('reason')!r}"
    )


@pytest.mark.asyncio
async def test_manual_add_default_mode_is_bidirectional(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Unmodified manual_add form (no CONF_BIDIRECTIONAL key) defaults to bidirectional (WR-03).

    When a user submits without flipping the toggle, the resolver default must
    produce CONF_BIDIRECTIONAL=True (bidirectional), matching the field-common case.
    """
    handler = _make_handler(hass, mock_hub_entry.entry_id)

    # Omit CONF_BIDIRECTIONAL entirely to simulate an unmodified form submission
    result = await handler.async_step_manual_add(
        {
            "device_enum": "1A",
            # CONF_BIDIRECTIONAL deliberately omitted
            "device_name": "Default Mode Blind",
        }
    )

    # With bidirectional as default, we expect a direct create_entry (no position step)
    assert result["type"] == "create_entry", (
        f"Expected create_entry for bidirectional default, got {result['type']!r}"
    )
    assert result["data"][CONF_BIDIRECTIONAL] is True, (
        f"Expected CONF_BIDIRECTIONAL=True (default), got {result['data'][CONF_BIDIRECTIONAL]!r}"
    )
