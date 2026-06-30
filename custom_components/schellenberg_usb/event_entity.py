"""Event entity for Schellenberg USB remote button presses."""

from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .api import SchellenbergUsbApi
from .const import DOMAIN, REMOTE_EVENT_MAP, SIGNAL_REMOTE_EVENT

_LOGGER = logging.getLogger(__name__)


class SchellenbergRemoteEventEntity(EventEntity):
    """Event entity for a motor's bound physical remote."""

    _attr_has_entity_name = True
    _attr_translation_key = "remote_button"
    _attr_should_poll = False
    _attr_device_class = EventDeviceClass.BUTTON

    def __init__(
        self,
        api: SchellenbergUsbApi,
        device_id: str,
        device_enum: str,
        remote_id: str,
    ) -> None:
        """Initialize the remote event entity."""
        self._api = api
        self._device_id = device_id
        self._device_enum = device_enum
        self._remote_id = remote_id
        self._attr_event_types: list[str] = [
            "up",
            "down",
            "stop",
            "hold_up",
            "hold_down",
        ]

        self._attr_unique_id = f"schellenberg_{device_id}_remote_button"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            # NO name/manufacturer/model — device already created by cover
            # platform.  identifiers alone link this entity to the existing
            # device card (EVT-02).
        )

    async def async_added_to_hass(self) -> None:
        """Register remote and subscribe to remote button events."""
        await super().async_added_to_hass()

        # Register unconditionally — the bidirectional exclusion lives
        # entirely in event.py's creation guard (Option A), so this entity
        # only ever exists for a timed motor (D-03).
        self._api.register_remote(self._remote_id, self._device_id, self._device_enum)

        # Snapshot both api and remote_id so the closure captures no
        # implicit self reference (self._api evaluated at call-time
        # would still hold self alive via the closure).
        api_snapshot = self._api
        remote_id_snapshot = self._remote_id

        def _cleanup_remote() -> None:
            api_snapshot.unregister_remote(remote_id_snapshot)

        self.async_on_remove(_cleanup_remote)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_REMOTE_EVENT}_{self._device_id}",
                self._on_remote_event,
            )
        )

    @callback
    def _on_remote_event(self, command: str, _receive_timestamp: float) -> None:
        """Handle a remote button press; fire the corresponding HA event."""
        event_type = REMOTE_EVENT_MAP.get(command)
        if event_type is None:
            _LOGGER.debug(
                "Remote %s: ignoring unknown command %s",
                self._remote_id,
                command,
            )
            return
        self._trigger_event(event_type)
        self.async_write_ha_state()
