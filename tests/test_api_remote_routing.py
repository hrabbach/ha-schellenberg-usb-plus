"""Tests for API remote routing — dedup, triple dispatch, learn-window, register/unregister."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    REMOTE_DEDUP_WINDOW,
    SIGNAL_DEVICE_EVENT,
    SIGNAL_REMOTE_EVENT,
)


# ---------------------------------------------------------------------------
# RMT-06: Incrementor dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_nine_identical_frames(hass: HomeAssistant) -> None:
    """Nine identical RF frames from a registered remote produce exactly 3 dispatches.

    The first frame triple-dispatches; frames 2-9 are suppressed by the dedup gate
    because they share the same (device_id, incrementor) within REMOTE_DEDUP_WINDOW.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        for _ in range(9):
            api._handle_message("ss10REM001ABCD01PP00")
        # First frame: 3 dispatches (triple dispatch). Frames 2-9: suppressed.
        assert mock_send.call_count == 3


@pytest.mark.asyncio
async def test_motor_frame_repeats_never_deduped(hass: HomeAssistant) -> None:
    """Nine identical motor frames (same device_id + same incrementor) all dispatch.

    A registered MOTOR (in _registered_devices, NOT in _remote_to_motor) is never
    deduped — Gate 2 is scoped to remote/learning frames only. This is the
    regression guard for RMT-07 dedup-scope (review finding #1).
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    # Register as a MOTOR only — not as a remote
    api.register_entity("MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        for _ in range(9):
            # Same device_id + same incrementor "ABCD" across all 9 frames
            api._handle_message("ss10MOT001ABCD01PP00")
        # Every motor frame dispatches once on SIGNAL_DEVICE_EVENT_MOT001; none suppressed
        assert mock_send.call_count == 9


@pytest.mark.asyncio
async def test_dedup_quiet_period_reset(hass: HomeAssistant) -> None:
    """After REMOTE_DEDUP_WINDOW elapses, the same incrementor counts as a new press.

    Simulates elapsed time by directly writing a past timestamp into the dedup cache
    (clock-relative delta; never asserts an absolute loop.time() value to avoid CI
    monotonic-clock flake — see MEMORY.md monotonic-clock-ci-flake).
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        # First frame: triple dispatch (3 calls)
        api._handle_message("ss10REM001ABCD01PP00")
        assert mock_send.call_count == 3

        # Simulate that the quiet period has elapsed for this dedup key
        dedup_key = ("REM001", "ABCD")
        api._dedup_cache[dedup_key] = (
            hass.loop.time() - REMOTE_DEDUP_WINDOW - 0.001
        )

        # Same frame again — quiet period expired, counts as a new press
        api._handle_message("ss10REM001ABCD01PP00")
        # Second press adds another 3 dispatches
        assert mock_send.call_count == 6


@pytest.mark.asyncio
async def test_dedup_per_device_isolation(hass: HomeAssistant) -> None:
    """A frame from device A does not suppress a same-incrementor frame from device B."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REMA01", "MOTA01", "10")
    api.register_remote("REMB01", "MOTB01", "11")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        # Frame from device A (first new press: 3 dispatches)
        api._handle_message("ss10REMA01ABCD01PP00")
        assert mock_send.call_count == 3

        # Frame from device B with the same incrementor "ABCD" — distinct dedup key
        api._handle_message("ss11REMB01ABCD01PP00")
        # Device B's frame is a new logical event — 3 more dispatches
        assert mock_send.call_count == 6


# ---------------------------------------------------------------------------
# RMT-07: Triple dispatch and motor-frame unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_triple_dispatch(hass: HomeAssistant) -> None:
    """A registered remote's frame triggers exactly 3 dispatches with the raw command byte."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10REM001ABCD01PP00")

        assert mock_send.call_count == 3
        # Extract positional args from each call: (hass, signal, payload)
        calls = [c[0] for c in mock_send.call_args_list]
        signals = [c[1] for c in calls]
        assert f"{SIGNAL_DEVICE_EVENT}_REM001" in signals
        assert f"{SIGNAL_DEVICE_EVENT}_MOT001" in signals
        assert f"{SIGNAL_REMOTE_EVENT}_MOT001" in signals
        # Every dispatch carries the raw command byte unchanged (D-03/D-04)
        for c in calls:
            assert c[2] == "01"


@pytest.mark.asyncio
async def test_motor_frame_unaffected(hass: HomeAssistant) -> None:
    """A motor frame (not in _remote_to_motor) triggers the existing single dispatch only."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_entity("MOT001", "10")  # motor only, NOT in _remote_to_motor

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10MOT001ABCD01PP00")

        assert mock_send.call_count == 1
        assert mock_send.call_args[0][1] == f"{SIGNAL_DEVICE_EVENT}_MOT001"
        assert mock_send.call_args[0][2] == "01"


@pytest.mark.asyncio
async def test_remote_frame_does_not_reach_motor_handle_event(
    hass: HomeAssistant,
) -> None:
    """A remote frame NEVER causes a fourth/final dispatch beyond the triple (RMT-07).

    Strengthened per review finding #5: assert the EXACT set of three signal names and
    that SIGNAL_DEVICE_EVENT_{remote_id} appears exactly once — proving Gate 3 returned
    early and never reached the existing final dispatch.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    # Register MOTOR as an entity (bidirectional motor)
    api.register_entity("MOT001", "10")
    # Register a SEPARATE remote bound to that motor
    api.register_remote("REM001", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10REM001ABCD01PP00")

        # (a) Exactly 3 dispatches — no fourth/final dispatch
        assert mock_send.call_count == 3

        calls = [c[0] for c in mock_send.call_args_list]
        signals = [c[1] for c in calls]

        # (b) The exact three signal names are present
        assert f"{SIGNAL_DEVICE_EVENT}_REM001" in signals
        assert f"{SIGNAL_DEVICE_EVENT}_MOT001" in signals
        assert f"{SIGNAL_REMOTE_EVENT}_MOT001" in signals

        # (c) SIGNAL_DEVICE_EVENT_REM001 appears EXACTLY once (no extra final dispatch)
        assert signals.count(f"{SIGNAL_DEVICE_EVENT}_REM001") == 1


# ---------------------------------------------------------------------------
# SC3: learn-window future
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_learn_remote_resolves_unknown(hass: HomeAssistant) -> None:
    """learn_remote_and_wait resolves with the first inbound unknown device_id."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    async def feed_frame() -> None:
        await asyncio.sleep(0)
        # UNK001 is NOT in _registered_devices
        api._handle_message("ss10UNK001ABCD01PP00")

    hass.async_create_task(feed_frame())
    result = await api.learn_remote_and_wait()

    assert result == "UNK001"


@pytest.mark.asyncio
async def test_learn_remote_ignores_registered(hass: HomeAssistant) -> None:
    """learn_remote_and_wait ignores a device already in _registered_devices.

    A registered motor's frame during a learn window does NOT resolve the future;
    a subsequently truly-unknown id does resolve it.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    # Pre-register a motor so its frames are NOT unknown
    api.register_entity("MOT001", "10")

    async def feed_frames() -> None:
        await asyncio.sleep(0)
        # First: a registered motor frame — must be ignored by the learn gate
        api._handle_message("ss10MOT001ABCD01PP00")
        # Then: an unknown device — should resolve the future
        api._handle_message("ss10UNK001EFGH01PP00")

    hass.async_create_task(feed_frames())
    result = await api.learn_remote_and_wait()

    assert result == "UNK001"


@pytest.mark.asyncio
async def test_learn_remote_fails_fast_on_disconnect(hass: HomeAssistant) -> None:
    """learn_remote_and_wait returns None immediately on a mid-window disconnect."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    async def disconnect_after_yield() -> None:
        await asyncio.sleep(0)
        api.update_connection_status(False)

    hass.async_create_task(disconnect_after_yield())
    result = await api.learn_remote_and_wait()

    assert result is None  # ConnectionError caught, returns None


# ---------------------------------------------------------------------------
# SC4: register/unregister + disconnect drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_unregister_remote(hass: HomeAssistant) -> None:
    """register_remote populates both dicts; unregister_remote pops both."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    api.register_remote("REM001", "MOT001", "10")

    assert "REM001" in api._registered_devices
    assert api._registered_devices["REM001"] == "10"
    assert "REM001" in api._remote_to_motor
    assert api._remote_to_motor["REM001"] == "MOT001"

    api.unregister_remote("REM001")

    assert "REM001" not in api._registered_devices
    assert "REM001" not in api._remote_to_motor


@pytest.mark.asyncio
async def test_disconnect_drain_learn_future(hass: HomeAssistant) -> None:
    """update_connection_status(False) drains _learn_remote_future with ConnectionError."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api._learn_remote_future = hass.loop.create_future()

    api.update_connection_status(False)

    assert api._learn_remote_future.done()
    assert isinstance(api._learn_remote_future.exception(), ConnectionError)
    # Retrieve to avoid "never retrieved" GC warning
    api._learn_remote_future.exception()


@pytest.mark.asyncio
async def test_disconnect_clears_dedup_state(hass: HomeAssistant) -> None:
    """update_connection_status(False) cancels dedup handles and clears both dedup dicts."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    # Populate dedup cache and handles with a real call_later handle
    dedup_key = ("REM001", "ABCD")
    api._dedup_cache[dedup_key] = hass.loop.time()
    handle = hass.loop.call_later(10.0, lambda: None)
    api._dedup_handles[dedup_key] = handle

    api.update_connection_status(False)

    assert len(api._dedup_cache) == 0
    assert len(api._dedup_handles) == 0
    # The handle should have been cancelled
    assert handle.cancelled()


# ---------------------------------------------------------------------------
# Phase 13 D-04: ref-count + re-bind drift documentation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_remote_ref_count(hass: HomeAssistant) -> None:
    """Double-register then single unregister keeps the mapping alive (D-04).

    For a timed+bound motor, both cover_entity (Phase 12) and event_entity
    (Phase 13) call register_remote for the same (remote_id, motor_id).
    After the first unregister (ref drops from 2 to 1), the mapping must
    still be present so the still-live entity continues to route correctly.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")
    api.register_remote("REM001", "MOT001", "10")
    api.unregister_remote("REM001")
    # Mapping still present after first unregister (ref=1 remains)
    assert api._remote_to_motor.get("REM001") == "MOT001"


@pytest.mark.asyncio
async def test_unregister_remote_ref_count_full(hass: HomeAssistant) -> None:
    """Second unregister (ref=0) fully removes mapping (D-04).

    After both entities tear down, all three dicts must be clean — no
    stale entries that could accumulate across a reload.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")
    api.register_remote("REM001", "MOT001", "10")
    api.unregister_remote("REM001")
    api.unregister_remote("REM001")
    assert api._remote_to_motor.get("REM001") is None
    assert api._registered_devices.get("REM001") is None
    assert api._remote_ref_counts.get("REM001") is None


@pytest.mark.asyncio
async def test_bidir_remote_event_fires(hass: HomeAssistant) -> None:
    """A registered remote fires SIGNAL_REMOTE_EVENT AND preserves the SIGNAL_DEVICE_EVENT bridge.

    D-05: event.py calls register_remote for ANY bound motor (including
    bidirectional); GATE 3 emits SIGNAL_REMOTE_EVENT for the motor. The
    SIGNAL_DEVICE_EVENT bridge must still emit byte-for-byte unchanged (RMT-07).
    A single fresh frame produces exactly 3 dispatches: the triple dispatch.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10REM001EFGH01PP00")

        # Exactly 3 dispatches (triple dispatch, byte-for-byte RMT-07)
        assert mock_send.call_count == 3
        calls = [c[0] for c in mock_send.call_args_list]
        signals = [c[1] for c in calls]

        # SIGNAL_REMOTE_EVENT fires (additive, D-05)
        assert f"{SIGNAL_REMOTE_EVENT}_MOT001" in signals
        # SIGNAL_DEVICE_EVENT bridge unchanged (RMT-07)
        assert f"{SIGNAL_DEVICE_EVENT}_REM001" in signals
        assert f"{SIGNAL_DEVICE_EVENT}_MOT001" in signals


@pytest.mark.asyncio
async def test_register_remote_rebind_drift_documented(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Lock the accepted re-bind-drift behavior (review finding #6).

    Re-binding a remote to a DIFFERENT motor overwrites _remote_to_motor and
    increments the count (not reset). A full teardown-to-zero (mirroring the
    Phase 15 reload) then cleans all three dicts — drift cannot accumulate
    across a reload. This test documents the disposition as a regression-lock,
    not a guard.
    """
    import logging

    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    with caplog.at_level(logging.WARNING, logger="custom_components.schellenberg_usb.api"):
        # First registration: remote → motorA
        api.register_remote("REM001", "MOTA01", "10")
        # Re-bind to a DIFFERENT motor (triggers WR-03 warning on second call)
        api.register_remote("REM001", "MOTB01", "11")

    # (a) Mapping is overwritten to the new motor
    assert api._remote_to_motor["REM001"] == "MOTB01"
    # (b) Count is incremented (not reset) — drift is accepted, not guarded
    assert api._remote_ref_counts["REM001"] == 2
    # (c) WR-03 re-bind warning was logged
    assert any("re-bound from" in record.message for record in caplog.records)

    # Simulate Phase 15 reload teardown: both entities unregister
    api.unregister_remote("REM001")
    api.unregister_remote("REM001")

    # All three dicts clean — drift cannot accumulate across a full teardown
    assert api._remote_to_motor.get("REM001") is None
    assert api._registered_devices.get("REM001") is None
    assert api._remote_ref_counts.get("REM001") is None
