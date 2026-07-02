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
    api.register_remote("REM001", "10", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        for _ in range(9):
            api._handle_message("ss10REM00101ABCDPP00")
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
            api._handle_message("ss10MOT00101ABCDPP00")
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
    api.register_remote("REM001", "10", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        # First frame: triple dispatch (3 calls)
        api._handle_message("ss10REM00101ABCDPP00")
        assert mock_send.call_count == 3

        # Simulate that the quiet period has elapsed for this dedup key
        dedup_key = ("10", "REM001", "ABCD")
        api._dedup_cache[dedup_key] = hass.loop.time() - REMOTE_DEDUP_WINDOW - 0.001

        # Same frame again — quiet period expired, counts as a new press
        api._handle_message("ss10REM00101ABCDPP00")
        # Second press adds another 3 dispatches
        assert mock_send.call_count == 6


@pytest.mark.asyncio
async def test_dedup_per_device_isolation(hass: HomeAssistant) -> None:
    """A frame from device A does not suppress a same-incrementor frame from device B."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REMA01", "10", "MOTA01", "10")
    api.register_remote("REMB01", "11", "MOTB01", "11")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        # Frame from device A (first new press: 3 dispatches)
        api._handle_message("ss10REMA0101ABCDPP00")
        assert mock_send.call_count == 3

        # Frame from device B with the same incrementor "ABCD" — distinct dedup key
        api._handle_message("ss11REMB0101ABCDPP00")
        # Device B's frame is a new logical event — 3 more dispatches
        assert mock_send.call_count == 6


# ---------------------------------------------------------------------------
# RMT-07: Triple dispatch and motor-frame unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_triple_dispatch(hass: HomeAssistant) -> None:
    """A registered remote's frame triggers exactly 3 dispatches with the raw command byte."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "10", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10REM00101ABCDPP00")

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
        api._handle_message("ss10MOT00101ABCDPP00")

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
    api.register_remote("REM001", "10", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10REM00101ABCDPP00")

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
        api._handle_message("ss10UNK00101ABCDPP00")

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
        api._handle_message("ss10MOT00101ABCDPP00")
        # Then: an unknown device — should resolve the future
        api._handle_message("ss10UNK00101EFGHPP00")

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

    api.register_remote("REM001", "10", "MOT001", "10")

    assert "REM001" in api._registered_devices
    assert api._registered_devices["REM001"] == "10"
    assert ("10", "REM001") in api._remote_to_motor
    assert api._remote_to_motor[("10", "REM001")] == "MOT001"

    api.unregister_remote("REM001", "10")

    assert "REM001" not in api._registered_devices
    assert ("10", "REM001") not in api._remote_to_motor


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
    dedup_key = ("10", "REM001", "ABCD")
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
    api.register_remote("REM001", "10", "MOT001", "10")
    api.register_remote("REM001", "10", "MOT001", "10")
    api.unregister_remote("REM001", "10")
    # Mapping still present after first unregister (ref=1 remains)
    assert api._remote_to_motor.get(("10", "REM001")) == "MOT001"


@pytest.mark.asyncio
async def test_unregister_remote_ref_count_full(hass: HomeAssistant) -> None:
    """Second unregister (ref=0) fully removes mapping (D-04).

    After both entities tear down, all three dicts must be clean — no
    stale entries that could accumulate across a reload.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "10", "MOT001", "10")
    api.register_remote("REM001", "10", "MOT001", "10")
    api.unregister_remote("REM001", "10")
    api.unregister_remote("REM001", "10")
    assert api._remote_to_motor.get(("10", "REM001")) is None
    assert api._registered_devices.get("REM001") is None
    assert api._remote_ref_counts.get(("10", "REM001")) is None


@pytest.mark.asyncio
async def test_api_triple_dispatch_when_remote_registered(hass: HomeAssistant) -> None:
    """api.py GATE 3 triple-dispatches for any remote registered via register_remote.

    The bidirectional-exclusion guard lives in event.py (GUARD 2), not in api.py.
    api.py is policy-free: it routes whatever register_remote declares.
    This test verifies the API-layer dispatch, NOT event.py creation behavior.
    SIGNAL_DEVICE_EVENT bridge remains byte-for-byte unchanged (RMT-07).
    A single fresh frame produces exactly 3 dispatches: the triple dispatch.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "10", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10REM00101EFGHPP00")

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

    with caplog.at_level(
        logging.WARNING, logger="custom_components.schellenberg_usb.api"
    ):
        # First registration: channel 10 of remote → motorA
        api.register_remote("REM001", "10", "MOTA01", "10")
        # Re-bind SAME channel to a DIFFERENT motor (triggers WR-03 warning)
        api.register_remote("REM001", "10", "MOTB01", "11")

    # (a) Mapping is overwritten to the new motor (same channel key)
    assert api._remote_to_motor[("10", "REM001")] == "MOTB01"
    # (b) Count is incremented (not reset) — drift is accepted, not guarded
    assert api._remote_ref_counts[("10", "REM001")] == 2
    # (c) WR-03 re-bind warning was logged
    assert any("re-bound from" in record.message for record in caplog.records)

    # Simulate Phase 15 reload teardown: both entities unregister
    api.unregister_remote("REM001", "10")
    api.unregister_remote("REM001", "10")

    # All three dicts clean — drift cannot accumulate across a full teardown
    assert api._remote_to_motor.get(("10", "REM001")) is None
    assert api._registered_devices.get("REM001") is None
    assert api._remote_ref_counts.get(("10", "REM001")) is None


# ---------------------------------------------------------------------------
# Phase 15 — GATE 1.5: raw-capture future (RMT-01 + RMT-07 no-return guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_1_5_resolves_raw_future_on_any_press(
    hass: HomeAssistant,
) -> None:
    """GATE 1.5 resolves _learn_remote_raw_future on a registered motor's press.

    Asserts BOTH:
    (a) the raw capture resolves with the registered motor's device_id
        (GATE 4's registered-id filter is bypassed by GATE 1.5), AND
    (b) the motor frame still fires its final SIGNAL_DEVICE_EVENT_{motor_id}
        dispatch (no-return contract — REVIEW finding 5). A stray `return`
        inside GATE 1.5 would pass (a) but fail (b).
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_entity("MOT001", "10")  # registered motor

    # Start a raw capture
    capture_task = asyncio.create_task(api.learn_remote_raw_and_wait(timeout=5.0))
    await asyncio.sleep(0)  # let the future be created

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10MOT00101ABCDPP00")
        await asyncio.sleep(0)

    result = await capture_task

    # (a) Raw capture resolved with the registered motor's (enum, id) tuple
    assert result == ("10", "MOT001")

    # (b) Motor frame still fired its final SIGNAL_DEVICE_EVENT_MOT001 dispatch
    signals = [c[0][1] for c in mock_send.call_args_list]
    assert f"{SIGNAL_DEVICE_EVENT}_MOT001" in signals


@pytest.mark.asyncio
async def test_gate_1_5_does_not_suppress_normal_routing(
    hass: HomeAssistant,
) -> None:
    """GATE 1.5 does not suppress GATE 3 triple dispatch (RMT-07 regression guard).

    A raw capture started, then a bound-remote frame fed in; the raw future
    resolves AND the GATE 3 triple dispatch still fires (3 calls) — proving
    GATE 1.5 did not return/suppress on the REMOTE path.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "10", "MOT001", "10")

    # Start a raw future (Gate 1.5 will fire)
    capture_task = asyncio.create_task(api.learn_remote_raw_and_wait(timeout=5.0))
    await asyncio.sleep(0)

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10REM00101ABCDPP00")
        await asyncio.sleep(0)

    result = await capture_task

    # Raw capture resolved with the remote's (enum, id) tuple
    assert result == ("10", "REM001")
    # Triple dispatch must still have fired (Gate 3 was not suppressed by Gate 1.5)
    assert mock_send.call_count == 3


@pytest.mark.asyncio
async def test_gate_1_5_burst_tail_does_not_resolve_second_capture(
    hass: HomeAssistant,
) -> None:
    """CR-01: a single press's burst tail must NOT resolve a SECOND capture.

    A single physical press emits a ~9-frame RF burst that all share ONE
    incrementor. After the first capture resolves and the learn-by-press flow
    opens the second capture window (D-06 double-press safeguard), the leftover
    burst frames (same device_id + same incrementor) must be ignored — otherwise
    one press falsely satisfies the second capture and binds after a single
    press. A genuine second press carries a NEW incrementor and DOES resolve it.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    # First capture (listen_first): burst frame 1 (incr=ABCD) resolves it.
    first_task = asyncio.create_task(api.learn_remote_raw_and_wait(timeout=5.0))
    await asyncio.sleep(0)
    api._handle_message("ss10REM00101ABCDPP00")
    assert await first_task == ("10", "REM001")

    # Second capture (listen_second) opens immediately, as the flow does.
    second_task = asyncio.create_task(api.learn_remote_raw_and_wait(timeout=5.0))
    await asyncio.sleep(0)

    # Burst tail of the SAME press (same incr=ABCD) must NOT resolve it.
    api._handle_message("ss10REM00101ABCDPP00")
    api._handle_message("ss10REM00101ABCDPP00")
    await asyncio.sleep(0)
    assert not second_task.done(), (
        "burst-tail frame of the first press falsely resolved the second "
        "capture future — D-06 double-press defeated (CR-01)"
    )

    # A genuine second press (NEW incrementor) resolves the second capture.
    api._handle_message("ss10REM00101EFGHPP00")
    assert await second_task == ("10", "REM001")


# ---------------------------------------------------------------------------
# Legacy remote_enum=None: wildcard fallback (migration — pre-v1.4 binds)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_none_enum_remote_still_routes(hass: HomeAssistant) -> None:
    """A pre-v1.4 bind stored under (None, remote_id) still routes frames.

    Exercises the Gate 3 wildcard fallback: when a subentry was persisted
    before CONF_REMOTE_ENUM existed, register_remote is called with
    remote_enum=None. A frame from that remote (carrying any channel enum)
    must still trigger the triple dispatch via the (None, id) slot.
    This seeds the Plan 04 legacy-fallback regression story at the api level.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    # Legacy registration — no channel enum (pre-v1.4 subentry)
    api.register_remote("REM001", None, "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        # Frame arrives with enum=10; the (None, "REM001") slot must match
        api._handle_message("ss10REM00101ABCDPP00")

        # Triple dispatch still fires via the legacy fallback slot
        assert mock_send.call_count == 3
        calls = [c[0] for c in mock_send.call_args_list]
        signals = [c[1] for c in calls]
        assert f"{SIGNAL_DEVICE_EVENT}_REM001" in signals
        assert f"{SIGNAL_DEVICE_EVENT}_MOT001" in signals
        assert f"{SIGNAL_REMOTE_EVENT}_MOT001" in signals
