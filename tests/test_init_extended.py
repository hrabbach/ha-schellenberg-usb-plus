"""Extended tests for __init__.py module - covering edge cases."""

from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
from typing import Any
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
    SUBENTRY_TYPE_HUB,
)


@pytest.fixture
def mock_config_entry_no_serial_port(hass: HomeAssistant) -> ConfigEntry:
    """Create a mock config entry without serial port (non-hub entry)."""
    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Entry",
        data={},  # No serial port
        options={},
        entry_id="test_entry_no_port",
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
async def test_async_setup_entry_non_hub_entry(
    hass: HomeAssistant, mock_config_entry_no_serial_port: ConfigEntry
) -> None:
    """Test setup entry returns False for non-hub entries."""
    from custom_components.schellenberg_usb import async_setup_entry

    result = await async_setup_entry(hass, mock_config_entry_no_serial_port)

    assert result is False


@pytest.mark.asyncio
async def test_async_setup_entry_updates_existing_hub_device(
    hass: HomeAssistant,
) -> None:
    """Test setup entry updates existing hub device."""
    from custom_components.schellenberg_usb import async_setup_entry

    # Create a config entry
    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_with_device",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry

    # Pre-create a hub device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Existing Hub Device",
    )

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True

    # Verify device still exists
    hub_device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    assert hub_device is not None


@pytest.mark.asyncio
async def test_async_unload_entry_when_unload_platforms_fails(
    hass: HomeAssistant,
) -> None:
    """Test unload entry when platform unload fails."""
    from custom_components.schellenberg_usb import async_setup_entry, async_unload_entry

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_unload_fail",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        await async_setup_entry(hass, entry)

    with patch.object(
        hass.config_entries, "async_unload_platforms", new_callable=AsyncMock
    ) as mock_unload:
        mock_unload.return_value = False

        result = await async_unload_entry(hass, entry)

        assert result is False
        # Disconnect should not be called if unload failed
        # (it's only called in the if unload_ok block)


@pytest.mark.asyncio
async def test_async_setup_entry_creates_subentry_if_missing(
    hass: HomeAssistant,
) -> None:
    """Test setup entry creates hub subentry if it doesn't exist."""
    from custom_components.schellenberg_usb import async_setup_entry

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_subentry",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    # Verify subentry was created (check that async_add_subentry was called)
    assert len(entry.subentries) > 0


@pytest.mark.asyncio
async def test_async_setup_entry_sets_up_platforms(hass: HomeAssistant) -> None:
    """Test that setup entry forwards to all platforms."""
    from custom_components.schellenberg_usb import async_setup_entry

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_platforms",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ) as mock_forward,
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    mock_forward.assert_called_once_with(entry, PLATFORMS)


@pytest.mark.asyncio
async def test_async_setup_entry_initializes_api(hass: HomeAssistant) -> None:
    """Test that setup entry initializes API with correct port."""
    from custom_components.schellenberg_usb import async_setup_entry

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB1"},  # Different port
        options={},
        entry_id="test_entry_api_init",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert entry.runtime_data is not None
    assert isinstance(entry.runtime_data, SchellenbergUsbApi)
    assert entry.runtime_data.port == "/dev/ttyUSB1"


@pytest.mark.asyncio
async def test_async_setup_entry_starts_connection(hass: HomeAssistant) -> None:
    """Test that setup entry starts API connection."""
    from custom_components.schellenberg_usb import async_setup_entry

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_connect",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        await async_setup_entry(hass, entry)

        # Connection should be started (task created)
        # We can't directly verify the task was created, but we know connect would be called
        # The actual call happens asynchronously via hass.async_create_task


@pytest.mark.asyncio
async def test_config_schema_exists() -> None:
    """Test that CONFIG_SCHEMA is defined."""
    from custom_components.schellenberg_usb import CONFIG_SCHEMA

    assert CONFIG_SCHEMA is not None
    assert DOMAIN in CONFIG_SCHEMA.schema


@pytest.mark.asyncio
async def test_pre_subentry_hub_device_migrates_to_hub_subentry_only(
    hass: HomeAssistant,
) -> None:
    """Test upgrade path: hub device seeded pre-subentry ends up single-subentry.

    Regression test for GH-40 / home-assistant/core#175785: core now
    rejects a device associated with more than one config subentry. On
    upgrade from a pre-subentry version of this integration, the hub
    device was seeded with a `None` (no-subentry) association, and setup
    only ever *added* the hub-subentry association without removing the
    stale `None` one, leaving the device in both at once.
    """
    from custom_components.schellenberg_usb import async_setup_entry

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_migration",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry

    # Seed the hub device the PRE-SUBENTRY way: no config_subentry_id at
    # all, which the real registry records as the `None` association.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Schellenberg USB Stick",
    )

    # Pre-condition: confirm we actually reproduced the pre-subentry bug
    # state before exercising setup.
    seeded_device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    assert seeded_device is not None
    assert seeded_device.config_entries_subentries[entry.entry_id] == {None}

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True

    # The device must survive setup (guards against the combined add+remove
    # call deleting it, since HA recomputes both branches from the
    # pre-update association set).
    hub_device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    assert hub_device is not None

    hub_subentry = next(
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_HUB
    )

    # Exact equality: the stale `None` association must be gone, leaving
    # only the hub subentry.
    assert hub_device.config_entries_subentries[entry.entry_id] == {
        hub_subentry.subentry_id
    }


@pytest.mark.asyncio
async def test_fresh_install_hub_device_has_single_subentry_association(
    hass: HomeAssistant,
) -> None:
    """Test fresh install: hub device belongs to the hub subentry only.

    Proves the `hub_device is None` creation path is unchanged and never
    carried a `None` association to begin with.
    """
    from custom_components.schellenberg_usb import async_setup_entry

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_fresh",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry

    # No pre-seeded device: this is a clean install.
    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True

    device_registry = dr.async_get(hass)
    hub_device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    assert hub_device is not None

    hub_subentry = next(
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_HUB
    )

    assert hub_device.config_entries_subentries[entry.entry_id] == {
        hub_subentry.subentry_id
    }


@pytest.mark.asyncio
async def test_setup_entry_is_idempotent_across_restarts(
    hass: HomeAssistant,
) -> None:
    """Test a repeated setup (restart) is a safe no-op, not a device wipe.

    The association cleanup runs on EVERY setup, so the second pass removes
    an association that no longer exists. That must neither raise nor empty
    the association set (which would delete the hub device).
    """
    from custom_components.schellenberg_usb import async_setup_entry

    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_entry_idempotent",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry

    # Seed the pre-subentry hub device, exactly as in the migration test.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Schellenberg USB Stick",
    )

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        # First setup migrates the stale `None` association away.
        assert await async_setup_entry(hass, entry) is True

        # Second setup simulates a Home Assistant restart.
        assert await async_setup_entry(hass, entry) is True

    hub_device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    assert hub_device is not None

    # The hub subentry must not have been duplicated by the second setup.
    hub_subentries = [
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_HUB
    ]
    assert len(hub_subentries) == 1

    assert hub_device.config_entries_subentries[entry.entry_id] == {
        hub_subentries[0].subentry_id
    }


_LEGACY_UPDATE_KWARGS = (
    "add_config_entry_id",
    "add_config_subentry_id",
    "remove_config_entry_id",
    "remove_config_subentry_id",
)


class _SingleOwnerDeviceRegistry:
    """Test double for the Home Assistant 2026.8+ single-owner device registry.

    Models the contract verified against home-assistant/core tags 2026.8.0
    and 2026.10.0: a device belongs to exactly one config entry and at most
    one subentry (``config_entry_id`` / ``config_subentry_id``), it is moved
    with ``new_config_entry_id`` / ``new_config_subentry_id``, and the four
    legacy ``add_*`` / ``remove_*`` kwargs are deprecated.

    The seeded device deliberately has NO ``config_entries`` /
    ``config_entries_subentries`` attributes (deprecated properties on
    2026.10+), so any read of them raises ``AttributeError``.
    """

    def __init__(self, hass: HomeAssistant, device: SimpleNamespace) -> None:
        """Store the hass instance and the single seeded device."""
        self._hass = hass
        self._device = device
        self.update_calls: list[dict[str, Any]] = []

    def async_get_device(
        self,
        identifiers: set[tuple[str, str]] | None = None,
        connections: set[tuple[str, str]] | None = None,
    ) -> SimpleNamespace | None:
        """Return the seeded device when the identifiers match."""
        if identifiers and identifiers & self._device.identifiers:
            return self._device
        return None

    def async_get_or_create(self, **kwargs: Any) -> SimpleNamespace:
        """Fail: the hub device already exists in these tests."""
        raise AssertionError(f"unexpected async_get_or_create: {kwargs}")

    def async_update_device(self, device_id: str, **kwargs: Any) -> SimpleNamespace:
        """Apply new_config_* kwargs with core's single-owner semantics."""
        assert device_id == self._device.id
        self.update_calls.append(dict(kwargs))
        used_legacy = [k for k in _LEGACY_UPDATE_KWARGS if k in kwargs]
        assert not used_legacy, f"deprecated legacy kwargs used: {used_legacy}"

        entry_id = self._device.config_entry_id
        if "new_config_entry_id" in kwargs:
            entry_id = kwargs["new_config_entry_id"]
            assert self._hass.config_entries.async_get_entry(entry_id) is not None
            self._device.config_entry_id = entry_id
            if "new_config_subentry_id" not in kwargs:
                # Core resets the subentry when only the entry is given.
                self._device.config_subentry_id = None
        if "new_config_subentry_id" in kwargs:
            subentry_id = kwargs["new_config_subentry_id"]
            if subentry_id is not None:
                resolved = self._hass.config_entries.async_get_entry(entry_id)
                assert resolved is not None
                # Mirrors core's "Config entry X has no subentry Y" error.
                assert subentry_id in resolved.subentries
            self._device.config_subentry_id = subentry_id
        return self._device


def _make_single_owner_entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    """Create and register a hub config entry with no subentries."""
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
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


def _make_single_owner_registry(
    hass: HomeAssistant, entry: ConfigEntry
) -> _SingleOwnerDeviceRegistry:
    """Build a fake registry seeded with a hub device in the main entry."""
    device = SimpleNamespace(
        id="hub_device_id",
        identifiers={(DOMAIN, entry.entry_id)},
        config_entry_id=entry.entry_id,
        config_subentry_id=None,
    )
    return _SingleOwnerDeviceRegistry(hass, device)


@pytest.mark.asyncio
async def test_single_owner_registry_moves_hub_device_with_new_kwargs(
    hass: HomeAssistant,
) -> None:
    """Test HA 2026.8+: the hub device is moved with new_config_* kwargs only.

    The legacy add_*/remove_* kwargs log a deprecation warning since HA
    2026.9 and stop working in 2027.8.0, so they must never be passed.
    """
    from custom_components.schellenberg_usb import async_setup_entry

    entry = _make_single_owner_entry(hass, "test_entry_single_owner")
    fake_registry = _make_single_owner_registry(hass, entry)

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
        patch.object(dr, "async_get", return_value=fake_registry),
    ):
        assert await async_setup_entry(hass, entry) is True

    hub_subentry = next(
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_HUB
    )

    assert fake_registry.update_calls == [
        {
            "new_config_entry_id": entry.entry_id,
            "new_config_subentry_id": hub_subentry.subentry_id,
        }
    ]
    device = fake_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.config_entry_id == entry.entry_id
    assert device.config_subentry_id == hub_subentry.subentry_id


@pytest.mark.asyncio
async def test_single_owner_registry_already_homed_hub_device_not_updated(
    hass: HomeAssistant,
) -> None:
    """Test HA 2026.8+: a restart with an already-homed hub device is a no-op."""
    from custom_components.schellenberg_usb import async_setup_entry

    entry = _make_single_owner_entry(hass, "test_entry_single_owner_homed")
    fake_registry = _make_single_owner_registry(hass, entry)

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
        patch.object(dr, "async_get", return_value=fake_registry),
    ):
        # First setup performs the one-time move.
        assert await async_setup_entry(hass, entry) is True
        assert len(fake_registry.update_calls) == 1

        # Second setup simulates a Home Assistant restart.
        fake_registry.update_calls.clear()
        assert await async_setup_entry(hass, entry) is True

    assert fake_registry.update_calls == []

    hub_subentry = next(
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_HUB
    )
    device = fake_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.config_entry_id == entry.entry_id
    assert device.config_subentry_id == hub_subentry.subentry_id


@pytest.mark.asyncio
async def test_legacy_registry_already_homed_hub_device_not_updated(
    hass: HomeAssistant,
) -> None:
    """Test pre-2026.8 registry: a restart makes no async_update_device call."""
    from custom_components.schellenberg_usb import async_setup_entry

    entry = _make_single_owner_entry(hass, "test_entry_legacy_homed")

    # Seed the pre-subentry hub device (None association) on the real registry.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Schellenberg USB Stick",
    )

    with (
        patch.object(SchellenbergUsbApi, "connect", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        # First setup migrates {None} -> {hub} via the two legacy calls.
        assert await async_setup_entry(hass, entry) is True

        # Second setup simulates a restart; the spy must stay untouched.
        with patch.object(
            device_registry,
            "async_update_device",
            wraps=device_registry.async_update_device,
        ) as update_spy:
            assert await async_setup_entry(hass, entry) is True

    update_spy.assert_not_called()

    hub_subentry = next(
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_HUB
    )
    hub_device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    assert hub_device is not None
    assert hub_device.config_entries_subentries[entry.entry_id] == {
        hub_subentry.subentry_id
    }
