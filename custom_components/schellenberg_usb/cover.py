"""Cover platform for Schellenberg USB."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_CLOSE_TIME,
    CONF_OPEN_TIME,
    CONF_SERIAL_PORT,
    DOMAIN,
    SUBENTRY_TYPE_LED,
    SchellenbergConfigEntry,
)
from .cover_calibration import _get_cal_store, _save_calibration
from .cover_entity import SchellenbergCover
from .cover_position import DEFAULT_TRAVEL_TIME

_LOGGER = logging.getLogger(__name__)

# Public surface re-exported so existing importers (tests, options_flow_*.py)
# keep resolving these names from this module unchanged. Listing the
# re-exported names in __all__ is what marks them "used" for ruff (no per-line
# noqa needed, D-04). async_dispatcher_connect is bound here purely so the
# test patch target custom_components.schellenberg_usb.cover.async_dispatcher_connect
# exists and intercepts the call cover_entity makes through this module.
__all__ = [
    "async_setup_entry",
    "async_dispatcher_connect",
    "DEFAULT_TRAVEL_TIME",
    "SchellenbergCover",
    "_get_cal_store",
    "_save_calibration",
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchellenbergConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Schellenberg cover entities."""
    _LOGGER.info("Cover platform async_setup_entry called for: %s", entry.entry_id)
    _LOGGER.debug("Entry data: %s", entry.data)

    # Only hub entries should reach here
    if CONF_SERIAL_PORT not in entry.data:
        _LOGGER.warning(
            "Cover platform called for non-hub entry %s, ignoring", entry.entry_id
        )
        return

    _LOGGER.info("Setting up cover for hub entry: %s", entry.title)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    api = entry.runtime_data

    # Load persisted calibration (does not fail setup if file is corrupt)
    _store, calibration_cache = await _get_cal_store(hass)
    entry_calibration: dict[str, Any] = calibration_cache.get(entry.entry_id, {}) or {}

    # Get paired devices from subentries
    subentries = entry.subentries.values()
    _LOGGER.info("Hub has %d subentries (paired devices)", len(entry.subentries))

    if not entry.subentries:
        _LOGGER.info("No subentries (paired devices) found for hub")
        return

    _LOGGER.info("Loading %d paired Schellenberg devices", len(entry.subentries))

    for subentry in subentries:
        # Skip LED subentry; handled by switch platform
        if subentry.subentry_type == SUBENTRY_TYPE_LED:
            continue

        device_id = subentry.data.get("device_id")
        device_enum = subentry.data.get("device_enum")
        device_name = subentry.title

        if not device_id or not device_enum:
            _LOGGER.debug(
                "Skipping subentry %s (type=%s) missing device_id/device_enum",
                subentry.subentry_id,
                getattr(subentry, "subentry_type", "unknown"),
            )
            continue

        # Merge persisted calibration (if any) into device_data, but do not override existing subentry.data
        merged_device_data = dict(subentry.data)
        persisted = entry_calibration.get(str(device_id))
        if isinstance(persisted, dict):
            merged_device_data.setdefault(CONF_OPEN_TIME, persisted.get("open_time"))
            merged_device_data.setdefault(CONF_CLOSE_TIME, persisted.get("close_time"))

        # Check if entity already exists to avoid duplicates.
        entity_unique_id = f"schellenberg_{device_id}"
        existing_entity_id = entity_registry.async_get_entity_id(
            "cover", DOMAIN, entity_unique_id
        )

        if existing_entity_id:
            entry_entity = entity_registry.async_get(existing_entity_id)
            if (
                entry_entity is not None
                and entry_entity.config_subentry_id != subentry.subentry_id
            ):
                _LOGGER.info(
                    "Updating existing cover entity %s to subentry %s",
                    existing_entity_id,
                    subentry.subentry_id,
                )
                entity_registry.async_update_entity(
                    existing_entity_id,
                    config_subentry_id=subentry.subentry_id,
                )
            _LOGGER.debug(
                "Re-instantiating cover entity object for existing registry entry %s",
                existing_entity_id,
            )

        # Create or get device in device registry
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            identifiers={(DOMAIN, device_id)},
            name=device_name,
            manufacturer="Schellenberg",
            model=f"USB Stick Motor ({device_id}/{device_enum})",
        )

        _LOGGER.debug(
            "Created/updated device %s for paired device %s",
            device.id,
            device_id,
        )

        _LOGGER.debug("Creating cover entity for device %s", device_id)
        async_add_entities(
            [
                SchellenbergCover(
                    api=api,
                    device_id=device_id,
                    device_enum=device_enum,
                    device_name=device_name,
                    device_data=merged_device_data,
                    config_entry_id=entry.entry_id,
                )
            ],
            config_subentry_id=subentry.subentry_id,
        )
