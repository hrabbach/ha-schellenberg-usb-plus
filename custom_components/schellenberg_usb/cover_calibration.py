"""Calibration storage helpers for Schellenberg USB covers."""

from __future__ import annotations

import logging
from typing import Any, MutableMapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    _CAL_STORE_KEY,
    _CAL_STORE_VERSION,
    _DATA_CACHE,
    _DATA_STORE,
    _HASS_DATA_KEY,
)

_LOGGER = logging.getLogger(__name__)


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
