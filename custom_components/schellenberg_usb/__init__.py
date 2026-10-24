"""The Schellenberg USB Stick integration."""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .api import SchellenbergUsbApi
from .const import (
    CONF_IGNORE_UNKNOWN,
    CONF_SERIAL_PORT,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_TYPE_HUB,
    SchellenbergConfigEntry,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: cv.config_entry_only_config_schema(DOMAIN)},
    extra=vol.ALLOW_EXTRA,
)

# Store setup callbacks for each entry so we can track subentries
_SETUP_CALLBACKS: dict[str, dict] = {}

# Marker for "this DeviceEntry has no config_subentry_id attribute" (HA < 2026.8)
_MISSING = object()


def _ensure_hub_device_in_hub_subentry(
    device_registry: dr.DeviceRegistry,
    hub_device: dr.DeviceEntry,
    entry_id: str,
    hub_subentry_id: str,
) -> None:
    """Make sure the existing hub device lives in the hub subentry.

    Home Assistant 2026.8 replaced the multi-association device model with a
    single owner (``config_entry_id`` / ``config_subentry_id``). The registry
    model is detected from the device entry itself, because the attribute and
    the ``new_config_*`` update kwargs landed together in 2026.8.0.
    """
    if getattr(hub_device, "config_subentry_id", _MISSING) is not _MISSING:
        # HA 2026.8+: move with ONE call using the new single-owner kwargs.
        # The kwargs go through a dict because the pinned HA stubs (< 2026.8)
        # do not declare them and a type-ignore would go stale once the dev
        # pin moves past 2026.8. Core's v3 registry migration already
        # collapsed any legacy {None, hub} association to the hub subentry,
        # so this only matters for a device still in the main entry.
        move_kwargs: dict[str, Any] = {
            "new_config_entry_id": entry_id,
            "new_config_subentry_id": hub_subentry_id,
        }
        device_registry.async_update_device(hub_device.id, **move_kwargs)
        return

    # HA before 2026.8: the legacy multi-association API.
    device_registry.async_update_device(
        hub_device.id,
        add_config_entry_id=entry_id,
        add_config_subentry_id=hub_subentry_id,
    )
    # Drop the legacy "no subentry" (None) association left behind by
    # versions that predate the hub subentry. Home Assistant now allows a
    # device to belong to at most one config subentry (issue #40,
    # home-assistant/core#175785), so leaving both would be invalid.
    #
    # This MUST be a second call: HA's _async_update_device derives both
    # the add-result and the remove-result from the device's PRE-UPDATE
    # association set, so merging them would subtract None from the
    # pre-update {None}, end up with an empty set, drop the config-entry
    # key, and delete the hub device outright. On installs that never had
    # a None association this call is a deliberate no-op — it runs on
    # every setup.
    device_registry.async_update_device(
        hub_device.id,
        remove_config_entry_id=entry_id,
        remove_config_subentry_id=None,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: SchellenbergConfigEntry
) -> bool:
    """Set up Schellenberg USB from a config entry."""
    _LOGGER.debug("Setup entry called for entry: %s", entry.entry_id)
    _LOGGER.debug("Entry data keys: %s", list(entry.data.keys()))

    # This is a hub entry - it has CONF_SERIAL_PORT
    if CONF_SERIAL_PORT not in entry.data:
        _LOGGER.warning(
            "Received async_setup_entry for non-hub entry %s, ignoring", entry.entry_id
        )
        return False

    _LOGGER.info("Setting up hub entry: %s", entry.title)
    hass.data.setdefault(DOMAIN, {})

    port = entry.data[CONF_SERIAL_PORT]
    api = SchellenbergUsbApi(hass, port)

    # Store API in runtime_data for platforms and services access
    entry.runtime_data = api

    # Push the initial "Ignore unknown signals" option onto the API so the
    # flag is correct from the very first message, even before the user ever
    # opens the options form.
    api.ignore_unknown = entry.options.get(CONF_IGNORE_UNKNOWN, False)

    # Start the connection
    hass.async_create_task(api.connect())

    # Ensure we have a dedicated hub subentry so hub-level devices/entities
    # (like the LED) do not appear under "Devices that don't belong to a sub-entry".
    hub_subentry = next(
        (s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_HUB),
        None,
    )
    if hub_subentry is None:
        _LOGGER.debug("Creating hub subentry for entry %s", entry.entry_id)
        hub_subentry = ConfigSubentry(
            data=MappingProxyType({}),
            subentry_type=SUBENTRY_TYPE_HUB,
            title="Hub",
            unique_id=None,
        )
        hass.config_entries.async_add_subentry(entry, hub_subentry)

    # Attach or create hub device under hub subentry to avoid ungrouped duplication
    device_registry = dr.async_get(hass)
    hub_device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    if hub_device is None:
        _LOGGER.debug(
            "Creating hub device and attaching to hub subentry %s",
            hub_subentry.subentry_id,
        )
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=hub_subentry.subentry_id,
            identifiers={(DOMAIN, entry.entry_id)},
            name="Schellenberg USB Stick",
            manufacturer="Schellenberg",
            model="USB Stick",
        )
    else:
        _LOGGER.debug(
            "Ensuring existing hub device %s is associated with entry %s and subentry %s",
            hub_device.id,
            entry.entry_id,
            hub_subentry.subentry_id,
        )
        _ensure_hub_device_in_hub_subentry(
            device_registry, hub_device, entry.entry_id, hub_subentry.subentry_id
        )

    # Forward setup to the hub's platforms (cover, sensor, switch)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Add listener to reload entry when subentries are added
    async def _on_entry_updated(
        hass_instance: HomeAssistant, updated_entry: SchellenbergConfigEntry
    ) -> None:
        """Handle updates to the hub entry (including subentry additions)."""
        current_subentries = set(updated_entry.subentries.keys())
        known_subentries = _SETUP_CALLBACKS.get(entry.entry_id, {}).get(
            "subentry_ids", set()
        )

        _LOGGER.debug(
            "Entry update detected. Current subentries: %s, Known subentries: %s",
            current_subentries,
            known_subentries,
        )

        if current_subentries != known_subentries:
            _LOGGER.info(
                "Subentries changed, reloading entry. Old: %s, New: %s",
                known_subentries,
                current_subentries,
            )
            # Update tracked subentries before reloading
            _SETUP_CALLBACKS[entry.entry_id]["subentry_ids"] = current_subentries
            # Reload the entire entry to re-setup all platforms with new subentries
            await hass_instance.config_entries.async_reload(entry.entry_id)
            # Mandatory: stop here so the toggle code below never runs on the
            # stale/replaced runtime_data during a reload (D-01 / Pitfall 1).
            return

        # Toggle-only change: live-apply "Ignore unknown signals" to the running
        # API without reloading (no serial blip — SC#1). Guard against
        # setup/teardown edges where runtime_data may be unset (review finding #3).
        api_instance = getattr(updated_entry, "runtime_data", None)
        if not api_instance:
            return
        new_ignore = updated_entry.options.get(CONF_IGNORE_UNKNOWN, False)
        if api_instance.ignore_unknown != new_ignore:
            _LOGGER.debug("Live-applying ignore_unknown=%s to running API", new_ignore)
            api_instance.ignore_unknown = new_ignore

    entry.add_update_listener(_on_entry_updated)

    # Track known subentries
    if entry.entry_id not in _SETUP_CALLBACKS:
        _SETUP_CALLBACKS[entry.entry_id] = {}
    _SETUP_CALLBACKS[entry.entry_id]["subentry_ids"] = set(entry.subentries.keys())

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SchellenbergConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        api: SchellenbergUsbApi = entry.runtime_data
        await api.disconnect()
        _SETUP_CALLBACKS.pop(entry.entry_id, None)

    return unload_ok
