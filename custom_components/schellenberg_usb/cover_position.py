"""Position tracking math for Schellenberg USB covers."""

from __future__ import annotations

import logging
import time

_LOGGER = logging.getLogger(__name__)

DEFAULT_TRAVEL_TIME = 60.0  # seconds, a sensible default


class PositionTracker:
    """Pure position calculator — no HA dependencies.

    Owns the travel times and the time->position math. The entity owns all
    HA-bound state (_attr_current_cover_position, etc.) and feeds parameters
    in; it receives a new position integer (or None) back. The tracker is
    stateless with respect to position — it never stores or mutates the
    entity's position, so it can never clobber a restored value.
    """

    def __init__(
        self,
        travel_time_open: float,
        travel_time_close: float,
    ) -> None:
        """Initialize with travel times (seconds)."""
        self._travel_time_open = travel_time_open
        self._travel_time_close = travel_time_close

    def calculate(
        self,
        start_pos: int,
        start_time: float,
        is_opening: bool,
        is_closing: bool,
    ) -> int | None:
        """Return the new clamped position, or None when not moving/invalid.

        Reproduces the original _update_position math exactly, including the
        three-branch dispatch: opening adds, closing subtracts, and neither
        flag set returns None — leaving the entity's position unchanged,
        byte-equivalent to cover.py's ``else: return``.
        """
        travel_time = (
            self._travel_time_open if is_opening else self._travel_time_close
        )

        # Avoid division by zero
        if not travel_time:
            return None

        elapsed_time = time.monotonic() - start_time
        total_position_change = (elapsed_time / travel_time) * 100

        if is_opening:
            new_pos = start_pos + total_position_change
        elif is_closing:
            new_pos = start_pos - total_position_change
        else:
            return None

        result = max(0, min(100, int(round(new_pos))))

        _LOGGER.debug(
            "Position updated to %d%% (elapsed: %.2fs, travel_time: %.2fs)",
            result,
            elapsed_time,
            travel_time,
        )

        return result

    def update_travel_times(
        self,
        travel_time_open: float,
        travel_time_close: float,
    ) -> None:
        """Update travel times after calibration completes."""
        self._travel_time_open = travel_time_open
        self._travel_time_close = travel_time_close
