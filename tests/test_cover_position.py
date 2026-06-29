"""Unit tests for PositionTracker pure-core math (no HA dependencies)."""

from __future__ import annotations

import time

from custom_components.schellenberg_usb.cover_position import PositionTracker


# ---------------------------------------------------------------------------
# Opening — position increases toward 100
# ---------------------------------------------------------------------------


def test_position_tracker_opening_increases_position() -> None:
    """Opening: 20s travel, ~10s elapsed → position near 50%."""
    tracker = PositionTracker(travel_time_open=20.0, travel_time_close=20.0)
    start_pos = 0
    # Back-date start_time by ~10 seconds so elapsed ≈ 10s
    start_time = time.monotonic() - 10.0

    result = tracker.calculate(start_pos, start_time, is_opening=True, is_closing=False)

    assert result is not None, "Opening should return a position, not None"
    assert result > start_pos, "Opening should increase position above start_pos"
    # 10s / 20s * 100 = 50%; allow ±10 for clock jitter
    assert 40 <= result <= 60, f"Expected ~50%% for 10s/20s travel, got {result}"


# ---------------------------------------------------------------------------
# Closing — position decreases toward 0
# ---------------------------------------------------------------------------


def test_position_tracker_closing_decreases_position() -> None:
    """Closing: 20s travel, ~10s elapsed, start at 100 → position near 50%."""
    tracker = PositionTracker(travel_time_open=20.0, travel_time_close=20.0)
    start_pos = 100
    start_time = time.monotonic() - 10.0

    result = tracker.calculate(start_pos, start_time, is_opening=False, is_closing=True)

    assert result is not None, "Closing should return a position, not None"
    assert result < start_pos, "Closing should decrease position below start_pos"
    assert 40 <= result <= 60, f"Expected ~50%% for 10s/20s travel, got {result}"


# ---------------------------------------------------------------------------
# Neither-flag guard (review finding #1): must return None
# ---------------------------------------------------------------------------


def test_position_tracker_neither_flag_returns_none() -> None:
    """Neither is_opening nor is_closing → None (position unchanged sentinel)."""
    tracker = PositionTracker(travel_time_open=20.0, travel_time_close=20.0)
    start_time = time.monotonic()

    result = tracker.calculate(50, start_time, is_opening=False, is_closing=False)

    assert result is None, (
        "Neither flag set must return None so entity position is unchanged; "
        f"got {result!r}"
    )


# ---------------------------------------------------------------------------
# Divide-by-zero guard: travel_time=0 → None
# ---------------------------------------------------------------------------


def test_position_tracker_zero_travel_time_returns_none() -> None:
    """Zero travel times must not raise ZeroDivisionError; return None."""
    tracker = PositionTracker(travel_time_open=0.0, travel_time_close=0.0)
    start_time = time.monotonic()

    # is_opening=True selects travel_time_open which is 0.0
    result = tracker.calculate(50, start_time, is_opening=True, is_closing=False)

    assert result is None, (
        f"Zero travel time must return None (divide-by-zero guard); got {result!r}"
    )


# ---------------------------------------------------------------------------
# Clamp: result always within [0, 100]
# ---------------------------------------------------------------------------


def test_position_tracker_clamps_result_at_100() -> None:
    """Opening far past 100% must clamp to 100, not overflow."""
    tracker = PositionTracker(travel_time_open=10.0, travel_time_close=10.0)
    # Back-date by 30s on a 10s travel → would be 300% without clamping
    start_time = time.monotonic() - 30.0

    result = tracker.calculate(0, start_time, is_opening=True, is_closing=False)

    assert result is not None
    assert result == 100, f"Expected clamped 100, got {result}"


def test_position_tracker_clamps_result_at_0() -> None:
    """Closing far past 0% must clamp to 0, not underflow."""
    tracker = PositionTracker(travel_time_open=10.0, travel_time_close=10.0)
    start_time = time.monotonic() - 30.0

    result = tracker.calculate(100, start_time, is_opening=False, is_closing=True)

    assert result is not None
    assert result == 0, f"Expected clamped 0, got {result}"


# ---------------------------------------------------------------------------
# update_travel_times changes subsequent calculate behavior
# ---------------------------------------------------------------------------


def test_position_tracker_update_travel_times_changes_behavior() -> None:
    """After update_travel_times, calculate uses the new travel time."""
    tracker = PositionTracker(travel_time_open=20.0, travel_time_close=20.0)
    start_time = time.monotonic() - 10.0

    # With travel_time_open=20s and 10s elapsed → ~50%
    result_before = tracker.calculate(0, start_time, is_opening=True, is_closing=False)
    assert result_before is not None
    assert 40 <= result_before <= 60, f"Pre-update expected ~50%%, got {result_before}"

    # Update to a much longer travel time
    tracker.update_travel_times(travel_time_open=100.0, travel_time_close=100.0)

    # Refresh start_time to keep elapsed ≈ 10s relative to now
    start_time2 = time.monotonic() - 10.0
    result_after = tracker.calculate(0, start_time2, is_opening=True, is_closing=False)
    assert result_after is not None
    # With travel_time_open=100s and 10s elapsed → ~10%
    assert result_after < result_before, (
        "Longer travel time should produce a smaller position delta; "
        f"before={result_before}, after={result_after}"
    )
    assert result_after <= 20, f"Expected ≤20%% for 10s/100s travel, got {result_after}"
