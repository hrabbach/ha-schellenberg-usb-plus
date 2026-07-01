"""Cover entity for Schellenberg USB blinds."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Mapping

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.restore_state import RestoreEntity

from .api import SchellenbergUsbApi
from .const import (
    CMD_DOWN,
    CMD_MANUAL_DOWN,
    CMD_MANUAL_UP,
    CMD_STOP,
    CMD_UP,
    CONF_BIDIRECTIONAL,
    CONF_CLOSE_TIME,
    CONF_INITIAL_POSITION,
    CONF_OPEN_TIME,
    CONF_REMOTE_ID,
    DOMAIN,
    EVENT_STARTED_MOVING_DOWN,
    EVENT_STARTED_MOVING_UP,
    EVENT_STOPPED,
    REMOTE_DEDUP_WINDOW,
    SIGNAL_CALIBRATION_COMPLETED,
    SIGNAL_DEVICE_EVENT,
    SIGNAL_REMOTE_EVENT,
    SIGNAL_STICK_STATUS_UPDATED,
)
from .cover_calibration import _save_calibration
from .cover_position import DEFAULT_TRAVEL_TIME, PositionTracker

_LOGGER = logging.getLogger(__name__)


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
        self._tracker = PositionTracker(
            self._travel_time_open,
            self._travel_time_close,
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
        # str | None; the remote_id of the bound physical remote (Phase 12).
        # Only timed motors may have a bound remote — bidirectional motors
        # use the device-event path instead and this field is ignored.
        self._remote_id: str | None = device_data_dict.get(CONF_REMOTE_ID)
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

        # Resolve the dispatcher helper THROUGH the cover module (not via a
        # direct top-level import) so the test suite's patch of the dispatcher
        # name in the cover namespace actually intercepts these three
        # registrations. DO NOT hoist this to a module-level import: a
        # top-level "from . import cover" is a circular import (cover.py
        # imports SchellenbergCover from this module), and a direct dispatcher
        # import here would bypass the patch target, silently dead-ending the
        # dispatcher-patch tests. Keep this function-local; leave it in place
        # during any "optimize imports" pass.
        from . import cover

        # Register listeners for events and status updates
        self.async_on_remove(
            cover.async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DEVICE_EVENT}_{self._device_id}",
                self._handle_event,
            )
        )

        self.async_on_remove(
            cover.async_dispatcher_connect(
                self.hass,
                SIGNAL_STICK_STATUS_UPDATED,
                self._handle_status_update,
            )
        )

        self.async_on_remove(
            cover.async_dispatcher_connect(
                self.hass,
                SIGNAL_CALIBRATION_COMPLETED,
                self._handle_calibration_completed,
            )
        )

        # Phase 12: subscribe to remote-button events for timed motors that
        # have a bound physical remote (RMT-04/RMT-05).  Bidirectional motors
        # use the SIGNAL_DEVICE_EVENT path above and are explicitly excluded.
        if self._remote_id and not self._is_bidirectional:
            self._api.register_remote(
                self._remote_id, self._device_id, self._device_enum
            )

            # Capture into a local so the closure does not hold a reference to
            # `self` beyond what is needed for cleanup (avoids a cycle if
            # `_remote_id` were ever reassigned, though it is effectively
            # immutable after __init__).
            remote_id_snapshot = self._remote_id

            def _cleanup_remote() -> None:
                self._api.unregister_remote(remote_id_snapshot)

            self.async_on_remove(_cleanup_remote)
            self.async_on_remove(
                cover.async_dispatcher_connect(
                    self.hass,
                    f"{SIGNAL_REMOTE_EVENT}_{self._device_id}",
                    self._handle_remote_event,
                )
            )

        # REFACTOR-V2-08: flag uncalibrated timed motors in HA Repairs.
        # Called without await — ir.async_create_issue is @callback (sync).
        # Independence from connectivity is intentional (D-06): the issue
        # reflects calibration state, not whether the stick is online.
        if not self._is_bidirectional and not self._is_calibrated:
            from homeassistant.helpers import (  # noqa: PLC0415
                issue_registry as ir,
            )

            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"uncalibrated_motor_{self._device_id.upper()}",
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="uncalibrated_motor",
                translation_placeholders={
                    "device_name": self._attr_name or self._device_id
                },
                learn_more_url=(
                    "https://github.com/hrabbach/ha-schellenberg-usb-plus"
                    "/blob/main/README.md"
                    "#timed-calibration-for-non-bidirectional-motors"
                ),
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
        self._tracker.update_travel_times(open_time, close_time)

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

        # D-07: clear the Repairs issue — motor is now calibrated.
        # async_delete_issue is @callback (same as async_create_issue).
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

        ir.async_delete_issue(
            self.hass,
            DOMAIN,
            f"uncalibrated_motor_{self._device_id.upper()}",
        )

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
        # D-05: clear Repairs issue so no orphaned card survives subentry
        # deletion. async_delete_issue is a no-op if the issue doesn't exist
        # (A1 verified: "It is not an error to delete an issue that does not
        # exist.") — safe for calibrated motors or bidirectional motors.
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

        ir.async_delete_issue(
            self.hass,
            DOMAIN,
            f"uncalibrated_motor_{self._device_id.upper()}",
        )

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

    @callback
    def _handle_remote_event(
        self, command: str, receive_timestamp: float
    ) -> None:
        """Handle a remote button press event for this timed motor.

        Called only when a bound remote is registered (guard in async_added_to_hass).
        Back-dates _move_start_time to the frame receive instant so the elapsed-time
        computation in the position loop is accurate rather than drifting by the
        dispatcher-delivery latency (Pitfall P9 / D-06).  Position tracking is
        best-effort (no movement confirmation from the motor) and self-corrects on
        the next full HA-driven open or close (D-05).

        Stop is detected by an explicit stop command — CMD_STOP (00). A missed
        release frame from a jog (41/42) is acceptable; the loop self-caps at
        0/100 via the existing boundary check and never requires a timer-based
        stop (Pitfall P5 / D-03).

        A bound handheld remote's presses arrive here as the SAME stick-scheme
        codes (command at frame [10:12] = 00/01/02/41/42); there is no separate
        handheld code space (see the resolved debug session).
        """
        # WR-12-04: a physical remote press is asynchronous to HA's lifecycle.
        # A dispatcher callback that races entity removal could invoke this
        # after teardown has begun — guard against mutating/creating tasks on a
        # detached entity.  async_on_remove unsubscribes before teardown
        # completes, so in normal operation self.hass is always set here.
        if self.hass is None:
            return

        # WR-12-03: diagnostic for "position jumped on remote press" reports.
        # _move_start_time is back-dated to the frame-decode instant, so a
        # delayed dispatch makes the first position sample compute a large
        # elapsed and visibly jump (worst on short-calibrated motors). Log at
        # debug when the back-date delta exceeds the dedup window so the jump
        # is traceable; tracking stays best-effort (no behavioral change).
        if command in (
            CMD_UP,
            CMD_MANUAL_UP,
            CMD_DOWN,
            CMD_MANUAL_DOWN,
        ):
            backdate_delta = time.monotonic() - receive_timestamp
            if backdate_delta > REMOTE_DEDUP_WINDOW:
                _LOGGER.debug(
                    "Remote move for %s back-dated by %.3fs (> %.1fs dedup"
                    " window); first position sample may jump",
                    self._attr_name,
                    backdate_delta,
                    REMOTE_DEDUP_WINDOW,
                )

        if command in (CMD_UP, CMD_MANUAL_UP):
            # CMD_UP (01, stick tap) and CMD_MANUAL_UP (41, stick jog) both
            # start the open position loop. A bound handheld remote's up press
            # arrives as one of these same codes (frame [10:12]).
            # D-01/D-02: 41/42 normalisation happens here in the cover layer,
            # not in the Phase 11 API bridge.
            self._attr_is_opening = True
            self._attr_is_closing = False
            self._move_start_time = receive_timestamp  # back-dated (D-06)
            if self._attr_current_cover_position is None:
                # REVIEW-12-03: unknown position when opening → assume CLOSED (0)
                # so upward travel toward 100 is actually visible in the position
                # loop.  Mirrors async_open_cover's None→0 default (cover_entity.py
                # async_open_cover).
                self._attr_current_cover_position = 0
            self._move_start_position = self._attr_current_cover_position
            self._start_position_tracking()

        elif command in (CMD_DOWN, CMD_MANUAL_DOWN):
            self._attr_is_opening = False
            self._attr_is_closing = True
            self._move_start_time = receive_timestamp  # back-dated (D-06)
            if self._attr_current_cover_position is None:
                # REVIEW-12-03: unknown position when closing → assume OPEN (100)
                # so downward travel toward 0 is visible.  Aligns with the D-09
                # "timed motor, no prior state → assume open (100)" convention
                # (cover_entity.py async_added_to_hass).
                self._attr_current_cover_position = 100
            self._move_start_position = self._attr_current_cover_position
            self._start_position_tracking()

        elif command == CMD_STOP:
            # D-04: latch the best-effort calculated position via _update_position().
            # CMD_STOP (00) latches; a handheld stop arrives as this same code.
            self._stop_position_tracking()
            self._update_position()

            # WR-12-01: mirror _handle_event's EVENT_STOPPED endstop clamp so
            # both stop paths finalize identically.  _update_position already
            # clamps via PositionTracker.calculate, but when a remote STOP
            # arrives after the position loop has self-capped and cleared
            # _move_start_time, _update_position early-returns and leaves
            # _attr_is_closed at whatever the loop last set — this re-derives
            # the boundary snap and is_closed explicitly.
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
            # REVIEW-12-02: clear _target_position even if a concurrent HA
            # async_set_cover_position move had set it.  Without this clear a stale
            # non-None target would survive and cause the NEXT remote move's position
            # loop to stop early at the old target value (_async_position_update_loop
            # checks _target_position every 200 ms).
            self._target_position = None

        else:
            _LOGGER.debug(
                "Device %s received unknown remote command: %s",
                self._attr_name,
                command,
            )
            return

        self.async_write_ha_state()

    def _start_position_tracking(self) -> None:
        """Start tracking position updates."""
        # WR-12-04: defensive guard — never create a background task on a
        # detached entity (self.hass is None before add / after teardown).
        if self.hass is None:
            return
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

        # Delegate the time→position math to the stateless PositionTracker,
        # passing BOTH movement flags so the neither-flag case returns None
        # and leaves the position unchanged — byte-equivalent to the original
        # three-branch `else: return` (review finding #1). The flags are
        # bool|None on the CoverEntity base; coerce with bool() so None
        # (the "not moving" sentinel) maps to False exactly as the original
        # truthy `if self._attr_is_opening:` checks did.
        result = self._tracker.calculate(
            self._move_start_position,
            self._move_start_time,
            bool(self._attr_is_opening),
            bool(self._attr_is_closing),
        )
        if result is None:
            return

        self._attr_current_cover_position = result
        self._attr_is_closed = self._attr_current_cover_position == 0

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
