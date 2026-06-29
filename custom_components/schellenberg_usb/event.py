"""Event platform for Schellenberg USB remote button presses."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_BIDIRECTIONAL,
    CONF_REMOTE_ID,
    CONF_SERIAL_PORT,
    SUBENTRY_TYPE_LED,
    SchellenbergConfigEntry,
)
from .event_entity import SchellenbergRemoteEventEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchellenbergConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Schellenberg remote button event entities."""
    # Hub-guard: only hub entries carry CONF_SERIAL_PORT in their data
    if CONF_SERIAL_PORT not in entry.data:
        _LOGGER.warning(
            "Event platform called for non-hub entry %s, ignoring",
            entry.entry_id,
        )
        return

    api = entry.runtime_data

    for subentry in entry.subentries.values():
        # Skip LED subentry; handled by switch platform
        if subentry.subentry_type == SUBENTRY_TYPE_LED:
            continue

        device_id = subentry.data.get("device_id")
        device_enum = subentry.data.get("device_enum")
        remote_id = subentry.data.get(CONF_REMOTE_ID)

        # GUARD 1 (SC #3): no remote binding → no event entity
        if not device_id or not device_enum or not remote_id:
            _LOGGER.debug(
                "event.py: skipping %s — no remote_id binding", device_id
            )
            continue

        # GUARD 2 (Option A — bidirectional EXCLUSION; see plan ⚠ decision):
        # Mirror the EXACT read-default from cover_entity.py:116-117 — default
        # True so a flag-less legacy subentry is treated as bidirectional.
        # Bidirectional motors are excluded so register_remote is never called
        # for them; GATE 3's SIGNAL_DEVICE_EVENT bridge stays byte-for-byte
        # unchanged (RMT-07).  api.py / GATE 3 are NOT modified by this phase.
        is_bidirectional = bool(subentry.data.get(CONF_BIDIRECTIONAL, True))
        if is_bidirectional:
            _LOGGER.debug(
                "event.py: skipping bidirectional motor %s — remote tracking"
                " deferred this milestone (D-02 narrowed, see plan)",
                device_id,
            )
            continue

        # Timed + bound: create one event entity, grouped under the motor
        # device card via config_subentry_id (MANDATORY — without it the entity
        # appears under "Devices without a sub-entry" rather than the cover).
        async_add_entities(
            [
                SchellenbergRemoteEventEntity(
                    api=api,
                    device_id=device_id,
                    device_enum=device_enum,
                    remote_id=remote_id,
                )
            ],
            config_subentry_id=subentry.subentry_id,
        )
