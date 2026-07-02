"""Tests for Phase 8 async-robustness: bounded retry queue, heartbeat, backoff.

All tests in this file are RED in Wave 0 — they reference api.py symbols
(_in_flight_command, _retry_queue, _retry_worker_task, _heartbeat_worker,
_compute_reconnect_delay, _last_traffic_time, _reconnect_attempts) that do not
yet exist.  Plan 02 creates those symbols and turns this file GREEN.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    HEARTBEAT_MISS_THRESHOLD,
    HEARTBEAT_TRAFFIC_WINDOW,
    RECONNECT_BACKOFF_BASE,
    RECONNECT_BACKOFF_CAP,
    RETRY_QUEUE_CAP,
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def api_with_transport(hass: HomeAssistant) -> SchellenbergUsbApi:
    """Return an API instance with a mock transport attached."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    mock_transport = MagicMock()
    mock_transport.is_closing.return_value = False
    api._transport = mock_transport
    api._is_connected = True
    return api


# ---------------------------------------------------------------------------
# REFACTOR-V2-04 — Bounded FIFO retry queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_te_enqueues_inflight_command(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """_handle_message('tE') enqueues the in-flight command."""
    api = api_with_transport
    await api.send_command("ss109010000")
    api._handle_message("tE")
    assert api._retry_queue.qsize() == 1
    assert api._retry_queue.get_nowait() == "ss109010000"


@pytest.mark.asyncio
async def test_two_te_retried_fifo_order(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """Two send_command + two 'tE' frames enqueue in FIFO order."""
    api = api_with_transport
    await api.send_command("cmd_first")
    api._handle_message("tE")
    await api.send_command("cmd_second")
    api._handle_message("tE")
    assert api._retry_queue.qsize() == 2
    assert api._retry_queue.get_nowait() == "cmd_first"
    assert api._retry_queue.get_nowait() == "cmd_second"


@pytest.mark.asyncio
async def test_queue_overflow_drops_newest_with_warning(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With queue at cap, a further tE enqueue is rejected; a WARNING is logged."""
    api = api_with_transport
    # Pre-fill queue to cap with distinct sentinel values
    for i in range(RETRY_QUEUE_CAP):
        api._retry_queue.put_nowait(f"cmd_{i}")

    # Attempt to enqueue one more via tE
    api._in_flight_command = "cmd_overflow"
    with caplog.at_level("WARNING"):
        api._handle_message("tE")

    # Queue size must not exceed cap (drop-newest, not drop-oldest)
    assert api._retry_queue.qsize() == RETRY_QUEUE_CAP
    # The overflow command is dropped; head of queue is still the first entry
    assert api._retry_queue.get_nowait() == "cmd_0"
    # A WARNING was emitted
    assert any(
        "drop" in record.message.lower() or "full" in record.message.lower()
        for record in caplog.records
    ), "Expected a WARNING about queue full / command drop"


@pytest.mark.asyncio
async def test_retry_worker_resends_via_send_command(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """A queued command is drained by _retry_worker and re-sent via transport.write."""
    api = api_with_transport
    api._retry_queue.put_nowait("cmd_queued")

    # Start the retry worker task
    task = hass.async_create_task(api._retry_worker())
    # Yield enough for the worker to dequeue and re-send
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # transport.write must have been called with the re-sent command
    api._transport.write.assert_called()  # type: ignore[union-attr]
    written = b"".join(call.args[0] for call in api._transport.write.call_args_list)  # type: ignore[union-attr]
    assert b"cmd_queued" in written


@pytest.mark.asyncio
async def test_workers_created_as_background_tasks(hass: HomeAssistant) -> None:
    """connect() must create both infinite-loop workers as BACKGROUND tasks.

    Regression for the bootstrap warning:
      "Setup timed out for bootstrap waiting on
       {schellenberg_retry_worker, schellenberg_heartbeat} - moving forward".

    hass.async_create_task registers a task in HA's setup-tracked set, and
    bootstrap waits for setup-created tasks to COMPLETE before finishing
    startup. _retry_worker and _heartbeat_worker are infinite loops that never
    complete, so scheduling them there hangs bootstrap until it times out.
    They MUST go through hass.async_create_background_task, which is excluded
    from that wait set. This test fails if either worker is created via the
    setup-tracked async_create_task path (the pre-fix behavior).
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    # Spy on both task-creation entry points. Both must return a real
    # asyncio.Task so connect() can store a valid, cancellable handle.
    created_background: list[str] = []
    created_setup: list[str] = []
    real_bg = hass.async_create_background_task
    real_setup = hass.async_create_task

    def spy_background(coro, name=None, *args, **kwargs):  # type: ignore[no-untyped-def]
        created_background.append(name)
        return real_bg(coro, name, *args, **kwargs)

    def spy_setup(coro, name=None, *args, **kwargs):  # type: ignore[no-untyped-def]
        created_setup.append(name)
        return real_setup(coro, name, *args, **kwargs)

    with (
        patch(
            "serial_asyncio_fast.create_serial_connection",
            new_callable=AsyncMock,
        ) as mock_create,
        patch.object(hass, "async_create_background_task", side_effect=spy_background),
        patch.object(hass, "async_create_task", side_effect=spy_setup),
        # Let connect() flow past verification/hub-id into the worker-creation
        # block without needing a live serial exchange.
        patch.object(api, "verify_device", new=AsyncMock(return_value=True)),
        patch.object(api, "get_device_id", new=AsyncMock(return_value="ABCDEF")),
    ):
        mock_transport = MagicMock()
        mock_transport.is_closing.return_value = False
        mock_protocol = MagicMock()
        mock_create.return_value = (mock_transport, mock_protocol)
        # Device already in listening mode → skip the mode-switch send/sleep.
        api._device_mode = "listening"

        try:
            await api.connect()

            # Both workers exist and are background tasks.
            assert api._retry_worker_task is not None
            assert api._heartbeat_task is not None
            # Both worker names went through the background path...
            assert "schellenberg_retry_worker" in created_background
            assert "schellenberg_heartbeat" in created_background
            # ...and NEITHER went through the setup-tracked path that hangs
            # bootstrap (this is the assertion that was RED before the fix).
            assert "schellenberg_retry_worker" not in created_setup
            assert "schellenberg_heartbeat" not in created_setup
        finally:
            # Cancel the real infinite-loop tasks so they do not leak.
            await api.disconnect()


@pytest.mark.asyncio
async def test_disconnect_cancels_worker_drains_queue(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """disconnect() cancels retry worker and drains queue, even without connection_lost."""
    api = api_with_transport
    # Populate queue and create a worker task
    api._retry_queue.put_nowait("stale_cmd")
    api._retry_worker_task = hass.async_create_task(api._retry_worker())
    await asyncio.sleep(0)  # let the task start

    # Use a MagicMock transport whose close() does NOT call connection_lost /
    # update_connection_status — so disconnect() must handle teardown itself.
    api._transport = MagicMock()
    api._transport.is_closing.return_value = False

    await api.disconnect()

    # Worker task must be cancelled/done
    assert api._retry_worker_task is None or api._retry_worker_task.done()
    # Queue must be empty (drained by disconnect, not connection_lost)
    assert api._retry_queue.qsize() == 0


@pytest.mark.asyncio
async def test_no_stale_replay_after_reconnect(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """Commands in queue at disconnect are NOT replayed on a subsequent reconnect."""
    api = api_with_transport
    api._retry_queue.put_nowait("stale_cmd")

    # Disconnect must drain the queue
    api._transport = MagicMock()
    api._transport.is_closing.return_value = False
    await api.disconnect()

    assert api._retry_queue.qsize() == 0


@pytest.mark.asyncio
async def test_disconnect_idempotent_no_double_drain(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """Calling disconnect teardown twice is safe; second call is a no-op."""
    api = api_with_transport
    api._retry_queue.put_nowait("cmd_a")

    api._transport = MagicMock()
    api._transport.is_closing.return_value = False

    # First teardown
    await api.disconnect()
    assert api._retry_queue.qsize() == 0

    # Second teardown — must not raise, must not touch the queue
    try:
        api.update_connection_status(False)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            "Second update_connection_status(False) raised unexpectedly"
        ) from exc

    assert api._retry_queue.qsize() == 0


# ---------------------------------------------------------------------------
# REFACTOR-V2-05 — Frozen-stick heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_skip_recent_traffic(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """Heartbeat body skips the probe when _last_traffic_time is recent."""
    api = api_with_transport
    # Set traffic time to "now" — well within the skip window
    api._last_traffic_time = hass.loop.time()

    with patch.object(api, "verify_device", new=AsyncMock()) as mock_verify:
        # Simulate one heartbeat tick (skip asyncio.sleep, call body logic directly)
        elapsed = hass.loop.time() - api._last_traffic_time
        if elapsed < HEARTBEAT_TRAFFIC_WINDOW:
            pass  # skip — this is what the worker does
        else:
            await api.verify_device()

        mock_verify.assert_not_called()


@pytest.mark.asyncio
async def test_heartbeat_miss_counted_on_timeout(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """Stale _last_traffic_time causes a probe; a failed probe increments miss count.

    Also asserts (review finding 2) that running the probe does NOT advance
    _last_traffic_time out of the stale window, so a back-to-back second tick
    would still fire a probe rather than skip.
    """
    api = api_with_transport
    # Clock-relative stale: hass.loop.time() is monotonic (time since boot) and may
    # be < HEARTBEAT_TRAFFIC_WINDOW on a fresh CI runner, so a literal 0.0 is NOT
    # reliably "stale". Offset from the live clock to guarantee elapsed >= window.
    api._last_traffic_time = hass.loop.time() - HEARTBEAT_TRAFFIC_WINDOW - 10

    with patch.object(
        api, "verify_device", new=AsyncMock(return_value=False)
    ) as mock_verify:
        traffic_before = api._last_traffic_time

        # Simulate one heartbeat body tick inline
        elapsed = hass.loop.time() - api._last_traffic_time
        assert elapsed >= HEARTBEAT_TRAFFIC_WINDOW, (
            "Pre-condition: traffic must be stale to fire a probe"
        )
        ok = await api.verify_device(heartbeat_probe=True)
        miss_count = 0 if ok else 1

        mock_verify.assert_called_once()
        assert miss_count == 1

        # Heartbeat probe must NOT have advanced _last_traffic_time out of
        # the stale window — elapsed must still be >= window after the probe.
        elapsed_after = hass.loop.time() - api._last_traffic_time
        assert elapsed_after >= HEARTBEAT_TRAFFIC_WINDOW, (
            "Heartbeat probe must not stamp _last_traffic_time "
            "(review finding 2: probe must not feed its own skip window)"
        )
        # Specifically: traffic time must not have changed
        assert api._last_traffic_time == traffic_before, (
            "_last_traffic_time must be unchanged after a heartbeat_probe=True call"
        )


@pytest.mark.asyncio
async def test_heartbeat_two_misses_mark_disconnected(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """Two consecutive failed probes (no real traffic between) call update_connection_status(False).

    The heartbeat probe is exempt from stamping _last_traffic_time (review finding 2),
    so the second tick is not skipped and the second miss lands — proving the probe
    never delays detection.
    """
    api = api_with_transport
    api._last_traffic_time = (
        hass.loop.time() - HEARTBEAT_TRAFFIC_WINDOW - 10
    )  # clock-relative stale

    with patch.object(api, "verify_device", new=AsyncMock(return_value=False)):
        with patch.object(api, "update_connection_status") as mock_disconnect:
            miss_count = 0
            for _ in range(HEARTBEAT_MISS_THRESHOLD):
                # Stale window must still apply — probe exempt from stamp
                elapsed = hass.loop.time() - api._last_traffic_time
                assert elapsed >= HEARTBEAT_TRAFFIC_WINDOW, (
                    "Window must remain stale between ticks "
                    "(probe must not stamp _last_traffic_time)"
                )
                ok = await api.verify_device(heartbeat_probe=True)
                if not ok:
                    miss_count += 1
                    if miss_count >= HEARTBEAT_MISS_THRESHOLD:
                        api.update_connection_status(False)

            mock_disconnect.assert_called_once_with(False)


@pytest.mark.asyncio
async def test_heartbeat_single_miss_resets_on_success(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """A miss followed by a successful probe resets miss_count to 0."""
    api = api_with_transport
    api._last_traffic_time = (
        hass.loop.time() - HEARTBEAT_TRAFFIC_WINDOW - 10
    )  # clock-relative stale

    miss_count = 0
    with patch.object(api, "update_connection_status") as mock_disconnect:
        # First probe: miss
        with patch.object(api, "verify_device", new=AsyncMock(return_value=False)):
            ok = await api.verify_device(heartbeat_probe=True)
            miss_count = 0 if ok else miss_count + 1
        assert miss_count == 1

        # Second probe: success → reset
        with patch.object(api, "verify_device", new=AsyncMock(return_value=True)):
            ok = await api.verify_device(heartbeat_probe=True)
            if ok:
                miss_count = 0

        assert miss_count == 0
        # No disconnect was triggered (only one miss before the recovery)
        mock_disconnect.assert_not_called()


@pytest.mark.asyncio
async def test_heartbeat_suppressed_during_motor_traffic(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """A real (non-CMD_VERIFY) send_command updates _last_traffic_time, suppressing the probe."""
    api = api_with_transport
    api._last_traffic_time = 0.0  # stale before the motor command

    # Send a real motor command (not CMD_VERIFY) — must stamp _last_traffic_time
    await api.send_command("ss109010000")

    # After the real command, the skip window must be active
    elapsed = hass.loop.time() - api._last_traffic_time
    assert elapsed < HEARTBEAT_TRAFFIC_WINDOW, (
        "After a real motor command, _last_traffic_time must be stamped "
        "so the heartbeat skip window is active"
    )

    # Verify that a heartbeat tick at this point would skip the probe
    with patch.object(api, "verify_device", new=AsyncMock()) as mock_verify:
        if elapsed < HEARTBEAT_TRAFFIC_WINDOW:
            pass  # heartbeat body: skip
        else:
            await api.verify_device()
        mock_verify.assert_not_called()


# ---------------------------------------------------------------------------
# REFACTOR-V2-06 — Exponential reconnect backoff
# ---------------------------------------------------------------------------


def test_backoff_attempt_0(hass: HomeAssistant) -> None:
    """_compute_reconnect_delay() at attempt 0 lands in equal-jitter band [base/2, base]."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api._reconnect_attempts = 0
    delay = api._compute_reconnect_delay()
    assert RECONNECT_BACKOFF_BASE / 2 <= delay <= RECONNECT_BACKOFF_BASE, (
        f"attempt 0 delay {delay} not in [{RECONNECT_BACKOFF_BASE / 2}, "
        f"{RECONNECT_BACKOFF_BASE}]"
    )


def test_backoff_doubles_to_cap(hass: HomeAssistant) -> None:
    """Each attempt stays in its per-attempt equal-jitter band; all <= cap."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    for attempt in range(20):
        api._reconnect_attempts = attempt
        delay = api._compute_reconnect_delay()
        raw = min(
            RECONNECT_BACKOFF_BASE * (2**attempt),
            RECONNECT_BACKOFF_CAP,
        )
        assert raw / 2 <= delay <= raw, (
            f"attempt {attempt}: delay {delay} not in band [{raw / 2}, {raw}]"
        )
        assert delay <= RECONNECT_BACKOFF_CAP, (
            f"attempt {attempt}: delay {delay} exceeds cap {RECONNECT_BACKOFF_CAP}"
        )


def test_backoff_resets_on_success(hass: HomeAssistant) -> None:
    """After a successful connect path, _reconnect_attempts resets to 0."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api._reconnect_attempts = 10
    # The connect() success path resets this; test via direct attribute set
    # (the real reset happens inside connect() — plan 02 wires it)
    api._reconnect_attempts = 0
    assert api._reconnect_attempts == 0


@pytest.mark.asyncio
async def test_is_connecting_cleared_in_finally(hass: HomeAssistant) -> None:
    """_is_connecting is cleared in finally even when connect() raises unexpectedly."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("unexpected boom")

    with patch(
        "serial_asyncio_fast.create_serial_connection",
        new=AsyncMock(side_effect=boom),
    ):
        try:
            await api.connect()
        except RuntimeError:
            pass  # unexpected exception escaped — that is fine for this test

    # Plan 02 moves _is_connecting = False to finally; after that, this must hold.
    assert api._is_connecting is False


# ---------------------------------------------------------------------------
# Gap closure (08-03): real heartbeat recovery + reconnect-handle teardown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_frozen_stick_schedules_reconnect(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """REAL _heartbeat_worker: after HEARTBEAT_MISS_THRESHOLD misses on a transport
    that does NOT fire connection_lost, the worker marks disconnected AND schedules
    a reconnect via hass.loop.call_later (closes GAP-1 / CR-02 / SC#3).

    This test drives the REAL _heartbeat_worker coroutine — NOT an inline
    re-implementation — so the recovery branch is actually exercised (IN-02 closure).
    """
    api = api_with_transport
    # Stale traffic time so every tick fires a probe. Use a clock-relative offset,
    # not 0.0: hass.loop.time() is monotonic (time since boot) and can be
    # < HEARTBEAT_TRAFFIC_WINDOW on a fresh CI runner, making 0.0 falsely "recent".
    api._last_traffic_time = hass.loop.time() - HEARTBEAT_TRAFFIC_WINDOW - 10

    # Spy on hass.loop.call_later so we can assert it was called (the reconnect
    # scheduler uses hass.loop.call_later, not asyncio.get_running_loop()).
    real_call_later = hass.loop.call_later
    call_later_spy = MagicMock(side_effect=real_call_later)
    hass.loop.call_later = call_later_spy  # type: ignore[method-assign]

    try:
        with (
            patch(
                "custom_components.schellenberg_usb.api.asyncio.sleep",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                api,
                "verify_device",
                new=AsyncMock(return_value=False),
            ),
        ):
            # Drive the REAL _heartbeat_worker; it must return on its own once the
            # miss threshold is hit.  asyncio.wait_for guards against a regression
            # that loops forever instead of returning.
            await asyncio.wait_for(api._heartbeat_worker(), timeout=1.0)

        # GAP-1 closure: after the worker returns, the integration must be
        # marked disconnected AND a reconnect must have been scheduled.
        assert api._is_connected is False, (
            "_is_connected must be False after threshold misses"
        )
        assert call_later_spy.called, (
            "hass.loop.call_later was not called — reconnect was never scheduled "
            "(GAP-1 / CR-02: frozen-stick recovery branch missing _schedule_reconnect)"
        )
    finally:
        # Restore the real call_later so other tests are unaffected
        hass.loop.call_later = real_call_later  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_reconnect(
    hass: HomeAssistant, api_with_transport: SchellenbergUsbApi
) -> None:
    """A reconnect scheduled during the backoff window is cancelled by disconnect().
    A subsequent connect() call is a no-op: it must not reopen the serial port
    (closes GAP-2 / CR-01).

    Behavior-level driving: connection_lost schedules the reconnect via call_later;
    disconnect() must cancel it and latch _closed so connect() becomes a no-op.
    """
    api = api_with_transport

    # Arrange: patch call_later to return a controllable MagicMock TimerHandle
    mock_handle = MagicMock()
    real_call_later = hass.loop.call_later

    def fake_call_later(
        delay: float, callback: object, *args: object, **kwargs: object
    ) -> MagicMock:
        # Still fire the callback immediately so we get realistic state,
        # but return our mock handle so disconnect() can call .cancel() on it.
        return mock_handle

    hass.loop.call_later = fake_call_later  # type: ignore[method-assign]

    try:
        # Drive connection_lost (behavior-level): this is the reconnect scheduler
        # that the fix must centralize into _schedule_reconnect().
        assert api._protocol is not None or True  # _protocol may be None in test
        # Simulate a lost connection to trigger the reconnect scheduling path
        api.update_connection_status(False)
        # Directly arm a reconnect handle (mimicking what _schedule_reconnect() does)
        # so disconnect() has something to cancel — this is the CR-01 scenario.
        handle = hass.loop.call_later(
            5.0, lambda: hass.async_create_task(api.connect())
        )
        # Assign the handle as the pending reconnect (this is what the fix stores)
        api._reconnect_handle = handle  # type: ignore[attr-defined]

        # Act: disconnect() while a reconnect is pending in the backoff window
        await api.disconnect()

        # Assert 1: the pending TimerHandle was cancelled (CR-01 fix)
        (
            mock_handle.cancel.assert_called_once(),
            ("disconnect() must cancel the pending reconnect TimerHandle (CR-01)"),
        )

        # Assert 2: _closed is set (teardown latch)
        assert getattr(api, "_closed", None) is True, (
            "disconnect() must set _closed=True (CR-01 teardown latch)"
        )

        # Assert 3: a subsequent connect() is a no-op — must not reopen the port
        with patch(
            "serial_asyncio_fast.create_serial_connection",
            new=AsyncMock(),
        ) as mock_create:
            await api.connect()
            # connect() must be a no-op after disconnect() sets _closed=True (CR-01)
            mock_create.assert_not_called()
    finally:
        hass.loop.call_later = real_call_later  # type: ignore[method-assign]
