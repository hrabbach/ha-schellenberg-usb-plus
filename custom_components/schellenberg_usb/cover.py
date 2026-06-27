"""Cover platform for Schellenberg USB."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Mapping, MutableMapping

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store

from .api import SchellenbergUsbApi
from .const import (
    CMD_DOWN,
    CMD_STOP,
    CMD_UP,
    CONF_BIDIRECTIONAL,
    CONF_CLOSE_TIME,
    CONF_INITIAL_POSITION,
    CONF_OPEN_TIME,
    CONF_SERIAL_PORT,
    DOMAIN,
    EVENT_STARTED_MOVING_DOWN,
    EVENT_STARTED_MOVING_UP,
    EVENT_STOPPED,
    SIGNAL_CALIBRATION_COMPLETED,
    SIGNAL_DEVICE_EVENT,
    SIGNAL_STICK_STATUS_UPDATED,
    SUBENTRY_TYPE_LED,
    SchellenbergConfigEntry,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TRAVEL_TIME = 60.0  # seconds, a sensible default

# Persist calibration into Home Assistant's <config>/.storage/
# File will be: /config/.storage/schellenberg_usb_calibration
_CAL_STORE_VERSION = 1
_CAL_STORE_KEY = f"{DOMAIN}_calibration"
_HASS_DATA_KEY = f"{DOMAIN}_cal_persistence"
_DATA_STORE = "store"
_DATA_CACHE = "cache"


async def _get_cal_store(hass: HomeAssistant) -> tuple[Store, dict[str, Any]]:
    """Get (and initialize if necessary) the calibration Store and cached data."""
    data: MutableMapping[str, Any] = hass.data.setdefault(_HASS_DATA_KEY, {})
    store: Store | None = data.get(_DATA_STORE)

    if store is None:
        store = Store(hass, _CAL_STORE_VERSION, _CAL_STORE_KEY)
        data[_DATA_STORE] = store

    cache = data.get(_DATA_CACHE)
    if cache is None:
        try:
            cache = await store.async_load() or {}
        except Exception:  # noqa: BLE001
            # If the JSON is corrupted, don't break the integration setup.
            # Broad catch is intentional: Store.async_load can surface a
            # range of deserialization/OS errors on a corrupt record, and
            # setup must degrade to an empty cache rather than fail.
            _LOGGER.exception(
                "Failed to load calibration store, starting with empty data"
            )
            cache = {}
        data[_DATA_CACHE] = cache

    return store, cache


async def _save_calibration(
    hass: HomeAssistant,
    config_entry_id: str,
    device_id: str,
    open_time: float,
    close_time: float,
) -> None:
    """Save calibration to Store cache and persist to disk."""
    store, cache = await _get_cal_store(hass)

    entry_map: dict[str, Any] = cache.setdefault(config_entry_id, {})
    entry_map[str(device_id)] = {
        "open_time": float(open_time),
        "close_time": float(close_time),
    }

    hass.data[_HASS_DATA_KEY][_DATA_CACHE] = cache
    await store.async_save(cache)


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


class SchellenbergCover(CoverEntity, RestoreEntity):
    """Representation of a Schellenberg Blind."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"mode", "calibrated"})

    _BASE_FEATURES = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(
        self,
        api: SchellenbergUsbApi,
        device_id: str,
        device_enum: str,
        device_name: str,
        device_data: Mapping[str, Any] | None = None,
        config_entry_id: str | None = None,
    ) -> None:
        """Initialize the Schellenberg cover entity."""
        self._api = api
        self._device_id = device_id
        self._device_enum = device_enum
        self._config_entry_id = config_entry_id

        # Entity attributes
        self._attr_unique_id = f"schellenberg_{device_id}"
        self._attr_name = device_name
        self._attr_is_closed = None
        self._attr_is_opening = False
        self._attr_is_closing = False

        # Position will be restored from last state in async_added_to_hass. Use None until then.
        self._attr_current_cover_position: int | None = None

        # Link this entity to the device using identifiers
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, device_id)},
        )

        # Position calculation attributes - use calibration times if available
        device_data_dict = dict(device_data) if device_data is not None else {}
        # Coerce None/0.0 from persisted/merged data to the default: a
        # partial/corrupt calibration record can store None for a time
        # (and .get(key, default) returns the stored None when the key is
        # present), and a 0-second travel time would divide-by-zero
        # downstream — both must fall back to DEFAULT_TRAVEL_TIME (WR-03).
        self._travel_time_open: float = (
            device_data_dict.get(CONF_OPEN_TIME) or DEFAULT_TRAVEL_TIME
        )
        self._travel_time_close: float = (
            device_data_dict.get(CONF_CLOSE_TIME) or DEFAULT_TRAVEL_TIME
        )

        # Mode flag: True = bidirectional (can receive events), False = timed.
        # Read-default is True so legacy Phase-1 auto-paired subentries that have
        # NO CONF_BIDIRECTIONAL key are treated as bidirectional — preventing a
        # CTRL-05 regression (Phase 3 would route them through timed control).
        # Manual adds ALWAYS write the key explicitly, so this default only
        # affects pre-existing flag-less subentries. (Phase 2 known limitation:
        # bidirectional manual adds store device_id as 2-char enum, so inbound
        # 6-char ss-frame device_id matches will miss _registered_devices — see
        # RESEARCH.md "Signal Filter Coupling". No fix needed for timed motors
        # as they produce no inbound frames. Tracked for a v2 story.)
        self._is_bidirectional: bool = bool(
            device_data_dict.get(CONF_BIDIRECTIONAL, True)
        )
        self._initial_position: int | None = (
            int(device_data_dict[CONF_INITIAL_POSITION])
            if CONF_INITIAL_POSITION in device_data_dict
            else None
        )

        # Calibrated = real open AND close times explicitly present (non-None).
        # The DEFAULT_TRAVEL_TIME fallback does NOT count as calibrated (D-06).
        # Value-presence check (is not None), not key-presence: a key present
        # but explicitly set to None must not be treated as calibrated (REVIEW-01).
        self._is_calibrated: bool = (
            device_data_dict.get(CONF_OPEN_TIME) is not None
            and device_data_dict.get(CONF_CLOSE_TIME) is not None
        )

        self._move_start_time: float | None = None
        self._move_start_position: int | None = None
        self._position_update_task: asyncio.Task[None] | None = None
        self._target_position: int | None = None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._api.is_connected

    @property
    def icon(self) -> str:
        """Return the icon based on cover state."""
        if self._attr_is_opening:
            return "mdi:arrow-up-box"
        if self._attr_is_closing:
            return "mdi:arrow-down-box"
        if self._attr_is_closed:
            return "mdi:window-shutter"
        return "mdi:window-shutter-open"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return if entity should be enabled by default."""
        return True

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return supported features, adding SET_POSITION only when usable.

        For timed (non-bidirectional) motors, SET_POSITION is only meaningful
        once calibration data is available. Advertising it on uncalibrated
        motors shows a position slider in HA's UI that silently does nothing
        (IN-03) — confusing users. Re-evaluation happens on
        _handle_calibration_completed via async_write_ha_state().
        """
        features = self._BASE_FEATURES
        if self._is_bidirectional or self._is_calibrated:
            features = features | CoverEntityFeature.SET_POSITION
        return features

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device-specific state attributes."""
        attrs: dict[str, Any] = {
            "mode": "bidirectional" if self._is_bidirectional else "timed",
        }
        if not self._is_bidirectional:
            attrs["calibrated"] = self._is_calibrated
        return attrs

    def _restore_position_from_last_state(self, last_state: Any) -> None:
        """Restore cover position from a HA last-known state.

        Contains the generic recorded-position restore logic: raw_position
        extraction, int coercion, state-string fallback, clamp, is_closed,
        and debug log.  Called from both the bidirectional and timed-idle
        branches so the logic lives in exactly one place (REVIEW-02).
        """
        restored_position: int | None = None
        raw_position = (
            last_state.attributes.get("current_position")
            if "current_position" in last_state.attributes
            else last_state.attributes.get(ATTR_POSITION)
        )

        if isinstance(raw_position, (int, float)):
            restored_position = int(raw_position)
        elif raw_position is not None:
            try:
                restored_position = int(str(raw_position))
            except ValueError:
                restored_position = None

        if restored_position is None:
            if last_state.state == "open":
                restored_position = 100
            elif last_state.state == "closed":
                restored_position = 0

        if restored_position is not None:
            self._attr_current_cover_position = max(0, min(100, restored_position))
            self._attr_is_closed = self._attr_current_cover_position == 0
            _LOGGER.debug(
                "Restored position for %s (%s) to %d%% (raw=%s)",
                self._attr_name,
                self._device_id,
                self._attr_current_cover_position,
                raw_position,
            )

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()

        # Register this entity with the API so it knows we're listening
        self._api.register_entity(self._device_id, self._device_enum)

        # Restore the last known state
        last_state = await self.async_get_last_state()
        if last_state and not self._is_bidirectional:
            # D-08: timed motor mid-move restart → snap to destination endstop.
            # This branch runs before the recorded-position restore so a stale
            # mid-move current_position attribute is discarded (plan key-link).
            if last_state.state == "opening":
                self._attr_current_cover_position = 100
                self._attr_is_closed = False
                _LOGGER.debug(
                    "Timed motor %s was opening at restart, snapping to 100%%",
                    self._attr_name,
                )
            elif last_state.state == "closing":
                self._attr_current_cover_position = 0
                self._attr_is_closed = True
                _LOGGER.debug(
                    "Timed motor %s was closing at restart, snapping to 0%%",
                    self._attr_name,
                )
            else:
                # D-09: idle timed motor → recorded position wins.
                # The helper handles raw_position extraction, is None sentinel,
                # and clamp.  A real recorded 0%% is preserved (not overridden).
                # Missing-data fallback (initial_position / 100) is layered in
                # Task 2 after this call.
                self._restore_position_from_last_state(last_state)

        elif last_state and self._is_bidirectional:
            # Bidirectional path: use the shared helper (REVIEW-02 — no copy).
            self._restore_position_from_last_state(last_state)

        if self._attr_current_cover_position is None:
            if self._initial_position is not None:
                self._attr_current_cover_position = max(
                    0, min(100, self._initial_position)
                )
                self._attr_is_closed = self._attr_current_cover_position == 0
                _LOGGER.debug(
                    "Seeding initial position for %s to %d%% from subentry.data",
                    self._attr_name,
                    self._attr_current_cover_position,
                )
            elif self._is_bidirectional:
                # Bidirectional: default to 0 (closed) — existing behavior.
                self._attr_current_cover_position = 0
                self._attr_is_closed = True
                _LOGGER.debug(
                    "No previous state for %s (%s);"
                    " defaulting position to 0%% (closed)",
                    self._attr_name,
                    self._device_id,
                )
            else:
                # D-09: timed motor with no prior state → assume open (100%%).
                # Never collapse missing data to 0 (SC#4 slider regression).
                self._attr_current_cover_position = 100
                self._attr_is_closed = False
                _LOGGER.debug(
                    "No previous state for timed motor %s (%s);"
                    " defaulting to 100%% (assume open)",
                    self._attr_name,
                    self._device_id,
                )

        self.async_write_ha_state()

        # Register listeners for events and status updates
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DEVICE_EVENT}_{self._device_id}",
                self._handle_event,
            )
        )

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_STICK_STATUS_UPDATED,
                self._handle_status_update,
            )
        )

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CALIBRATION_COMPLETED,
                self._handle_calibration_completed,
            )
        )

    @callback
    def _handle_status_update(self) -> None:
        """Handle status update from API (connection state changed)."""
        self.async_write_ha_state()

    @callback
    def _handle_calibration_completed(
        self,
        device_id: str,
        open_time: float,
        close_time: float,
        final_position: int = 0,
    ) -> None:
        """Handle calibration completion for this device.

        final_position: timed flow passes 100 (ends open); legacy
        bidirectional flow passes 0 (ends closed). Default 0 keeps the
        3-arg legacy dispatcher dispatch backward-compatible (D-14).
        """
        if device_id != self._device_id:
            return

        self._travel_time_open = open_time
        self._travel_time_close = close_time

        # Persist calibration (async, we're in a callback)
        if self._config_entry_id:
            self.hass.async_create_task(
                _save_calibration(
                    self.hass,
                    self._config_entry_id,
                    self._device_id,
                    open_time,
                    close_time,
                )
            )

        # End-state depends on which flow completed:
        # timed flow ends open (final_position=100), legacy ends closed (0).
        self._attr_current_cover_position = final_position
        self._attr_is_closed = final_position == 0

        # Flip calibrated flag so the attribute reflects live state (REVIEW-05).
        # Must run BEFORE async_write_ha_state() so the pushed state is correct.
        self._is_calibrated = True

        _LOGGER.info(
            "Device %s calibration updated: open_time=%.2fs, close_time=%.2fs."
            " Cover position set to %d%%",
            self._attr_name,
            open_time,
            close_time,
            final_position,
        )

        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        await super().async_will_remove_from_hass()
        self._stop_position_tracking()

    @callback
    def _handle_event(self, event: str) -> None:
        """Handle events from the USB stick for this device."""
        # D-11 / REVIEW-04: timed motors produce no inbound frames; any stray
        # event must not mutate state.  This guard makes D-11 structurally
        # self-enforcing — the whole event body is skipped for timed motors.
        if not self._is_bidirectional:
            return

        _LOGGER.info(
            "Device %s (%s) received activity event: %s",
            self._attr_name,
            self._device_id,
            event,
        )

        if event == EVENT_STARTED_MOVING_UP:
            self._attr_is_opening = True
            self._attr_is_closing = False
            self._move_start_time = time.monotonic()
            self._move_start_position = self._attr_current_cover_position
            self._start_position_tracking()

        elif event == EVENT_STARTED_MOVING_DOWN:
            self._attr_is_opening = False
            self._attr_is_closing = True
            self._move_start_time = time.monotonic()
            self._move_start_position = self._attr_current_cover_position
            self._start_position_tracking()

        elif event == EVENT_STOPPED:
            self._stop_position_tracking()

            if self._target_position is not None:
                self._attr_current_cover_position = self._target_position
            else:
                self._update_position()

            if self._attr_current_cover_position is not None:
                if self._attr_current_cover_position <= 0:
                    self._attr_current_cover_position = 0
                elif self._attr_current_cover_position >= 100:
                    self._attr_current_cover_position = 100
                self._attr_is_closed = self._attr_current_cover_position == 0

            self._attr_is_opening = False
            self._attr_is_closing = False
            self._move_start_time = None
            self._move_start_position = None
            self._target_position = None

        else:
            _LOGGER.debug(
                "Device %s received unknown event: %s", self._attr_name, event
            )

        self.async_write_ha_state()

    def _start_position_tracking(self) -> None:
        """Start tracking position updates."""
        self._stop_position_tracking()
        self._position_update_task = self.hass.async_create_task(
            self._async_position_update_loop()
        )

    def _stop_position_tracking(self) -> None:
        """Stop the position tracking task."""
        if self._position_update_task and not self._position_update_task.done():
            self._position_update_task.cancel()
        self._position_update_task = None

    async def _async_position_update_loop(self) -> None:
        """Update position every 200ms internally, report to HA every 1 second."""
        try:
            ha_update_counter = 0
            while True:
                await asyncio.sleep(0.2)
                self._update_position()
                ha_update_counter += 1

                if self._target_position is not None:
                    position_reached = (
                        self._attr_is_opening
                        and self._attr_current_cover_position is not None
                        and self._attr_current_cover_position >= self._target_position
                    ) or (
                        self._attr_is_closing
                        and self._attr_current_cover_position is not None
                        and self._attr_current_cover_position <= self._target_position
                    )

                    if position_reached:
                        self._attr_current_cover_position = self._target_position

                        if self._target_position not in (0, 100):
                            await self._api.control_blind(self._device_enum, CMD_STOP)

                        self._attr_is_opening = False
                        self._attr_is_closing = False
                        self._attr_is_closed = self._attr_current_cover_position == 0
                        self._target_position = None
                        self._move_start_time = None
                        self._move_start_position = None
                        self.async_write_ha_state()
                        return

                if self._target_position is None:
                    if (
                        self._attr_is_closing
                        and self._attr_current_cover_position is not None
                        and self._attr_current_cover_position <= 0
                    ):
                        self._attr_current_cover_position = 0
                        self._attr_is_opening = False
                        self._attr_is_closing = False
                        self._move_start_time = None
                        self._move_start_position = None
                        self.async_write_ha_state()
                        return

                    if (
                        self._attr_is_opening
                        and self._attr_current_cover_position is not None
                        and self._attr_current_cover_position >= 100
                    ):
                        self._attr_current_cover_position = 100
                        self._attr_is_opening = False
                        self._attr_is_closing = False
                        self._move_start_time = None
                        self._move_start_position = None
                        self.async_write_ha_state()
                        return

                if ha_update_counter >= 5:
                    self.async_write_ha_state()
                    ha_update_counter = 0

        except asyncio.CancelledError:
            _LOGGER.debug("Position tracking cancelled for device %s", self._attr_name)
            raise
        finally:
            # Clear the handle only if it still points at THIS task, so a
            # concurrent _start_position_tracking() that already swapped in a
            # new task isn't clobbered by this one's exit (WR-02).
            if self._position_update_task is asyncio.current_task():
                self._position_update_task = None

    def _update_position(self) -> None:
        """Calculate and update the position based on travel time."""
        if self._move_start_time is None or self._move_start_position is None:
            return

        elapsed_time = time.monotonic() - self._move_start_time
        travel_time = (
            self._travel_time_open if self._attr_is_opening else self._travel_time_close
        )

        # Avoid division by zero
        if not travel_time:
            return

        total_position_change = (elapsed_time / travel_time) * 100

        if self._attr_is_opening:
            new_pos = self._move_start_position + total_position_change
        elif self._attr_is_closing:
            new_pos = self._move_start_position - total_position_change
        else:
            return

        self._attr_current_cover_position = max(0, min(100, int(round(new_pos))))
        self._attr_is_closed = self._attr_current_cover_position == 0

        _LOGGER.debug(
            "Device %s position updated to %d%% (elapsed: %.2fs, travel_time: %.2fs)",
            self._device_id,
            self._attr_current_cover_position,
            elapsed_time,
            travel_time,
        )

    async def async_open_cover(self, target: int | None = None, **kwargs: Any) -> None:
        """Open the cover.

        ``target`` is the partial-move target for a set-position driven
        move; a direct Open (the HA open button) passes ``None``, which
        clears any stale set-position target so the cover runs to the
        endstop instead of stopping at a leftover partial target (CR-01).
        """
        _LOGGER.debug("Opening cover %s (enum=%s)", self._attr_name, self._device_enum)
        self._target_position = target
        self._attr_is_opening = True
        self._attr_is_closing = False
        self._move_start_time = time.monotonic()

        if self._attr_current_cover_position is None:
            self._attr_current_cover_position = 0

        self._move_start_position = self._attr_current_cover_position
        self._start_position_tracking()
        self.async_write_ha_state()
        await self._api.control_blind(self._device_enum, CMD_UP)

    async def async_close_cover(self, target: int | None = None, **kwargs: Any) -> None:
        """Close cover.

        ``target`` is the partial-move target for a set-position driven
        move; a direct Close (the HA close button) passes ``None``, which
        clears any stale set-position target so the cover runs to the
        endstop instead of stopping at a leftover partial target (CR-01).
        """
        _LOGGER.debug("Closing cover %s (enum=%s)", self._attr_name, self._device_enum)
        self._target_position = target
        self._attr_is_opening = False
        self._attr_is_closing = True
        self._move_start_time = time.monotonic()

        if self._attr_current_cover_position is None:
            self._attr_current_cover_position = 0

        self._move_start_position = self._attr_current_cover_position
        self._start_position_tracking()
        self.async_write_ha_state()
        await self._api.control_blind(self._device_enum, CMD_DOWN)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        _LOGGER.debug("Stopping cover %s (enum=%s)", self._attr_name, self._device_enum)
        self._stop_position_tracking()
        self._update_position()
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._move_start_time = None
        self._move_start_position = None
        self._target_position = None
        self.async_write_ha_state()
        await self._api.control_blind(self._device_enum, CMD_STOP)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        if not self._is_bidirectional and not self._is_calibrated:
            _LOGGER.debug(
                "Timed motor %s: set-position ignored (not calibrated yet)",
                self._attr_name,
            )
            return
        target_position = kwargs[ATTR_POSITION]

        if self._attr_current_cover_position is None:
            self._attr_current_cover_position = 0

        current_position = self._attr_current_cover_position

        _LOGGER.info(
            "Setting cover %s position from %d%% to %d%%",
            self._attr_name,
            current_position,
            target_position,
        )

        if target_position == current_position:
            _LOGGER.debug("Target position equals current position, no action needed")
            return

        self._target_position = target_position

        if target_position > current_position:
            _LOGGER.info(
                "Moving cover %s UP to reach target %d%%",
                self._attr_name,
                target_position,
            )
            await self.async_open_cover(target=target_position)
        else:
            _LOGGER.info(
                "Moving cover %s DOWN to reach target %d%%",
                self._attr_name,
                target_position,
            )
            await self.async_close_cover(target=target_position)
        # The position tracking loop will automatically send the stop command
        # when the target position is reached.
