"""Tests for Phase 14 delegation pairing API seam.

Task 1: constants, _delegation_future field, disconnect drain, track_retry bypass.
Task 2: delegation_pair(), abort_delegation_pair(), full handshake choreography.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.api import (
    DeviceLimitReached,
    SchellenbergUsbApi,
)
from custom_components.schellenberg_usb.const import (
    DELEGATION_BLINK_COUNT,
    DELEGATION_TIMEOUT,
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
# Task 1: Constants
# ---------------------------------------------------------------------------


def test_delegation_timeout_type() -> None:
    """DELEGATION_TIMEOUT is a numeric type."""
    assert isinstance(DELEGATION_TIMEOUT, (int, float))


def test_delegation_blink_count_type_and_range() -> None:
    """DELEGATION_BLINK_COUNT is an int in 1..9."""
    assert isinstance(DELEGATION_BLINK_COUNT, int)
    assert 1 <= DELEGATION_BLINK_COUNT <= 9


# ---------------------------------------------------------------------------
# Task 1: _delegation_future field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_future_initial_none(hass: HomeAssistant) -> None:
    """Freshly constructed api has _delegation_future == None."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    assert api._delegation_future is None


# ---------------------------------------------------------------------------
# Task 1: disconnect drain resolves _delegation_future
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_drain_resolves_delegation_future(
    hass: HomeAssistant,
) -> None:
    """update_connection_status(False) resolves a pending _delegation_future with
    ConnectionError and the awaiting coroutine does not hang."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    # Plant a pending future
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[tuple[str, str]] = loop.create_future()
    api._delegation_future = fut

    # Plant a coroutine that awaits the future (simulates delegation_pair body)
    async def awaiter() -> BaseException | None:
        try:
            await fut
        except Exception as exc:  # noqa: BLE001
            return exc
        return None

    task = asyncio.ensure_future(awaiter())
    # Yield so the coroutine reaches its await
    await asyncio.sleep(0)

    # Trigger disconnect drain
    api.update_connection_status(False)

    # Give the event loop a tick to propagate
    await asyncio.sleep(0)

    result = await asyncio.wait_for(task, timeout=1.0)
    assert isinstance(result, ConnectionError)
    # The future should be done now
    assert fut.done()
    assert isinstance(fut.exception(), ConnectionError)


@pytest.mark.asyncio
async def test_disconnect_drain_noop_when_future_none(hass: HomeAssistant) -> None:
    """update_connection_status(False) is a no-op when _delegation_future is None."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    assert api._delegation_future is None
    # Must not raise
    api.update_connection_status(False)


@pytest.mark.asyncio
async def test_disconnect_drain_noop_when_future_already_done(
    hass: HomeAssistant,
) -> None:
    """update_connection_status(False) is a no-op when _delegation_future is already done."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[tuple[str, str]] = loop.create_future()
    fut.set_result(("10", "10"))  # already done
    api._delegation_future = fut
    # Must not raise InvalidStateError
    api.update_connection_status(False)


# ---------------------------------------------------------------------------
# Task 1: track_retry=False bypass (REVIEWS finding 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_command_track_retry_false_leaves_inflight_none(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """send_command(..., track_retry=False) writes to transport but leaves
    _in_flight_command == None; a subsequent tE enqueues nothing."""
    api = api_with_transport

    await api.send_command("ss109600000", track_retry=False)

    # In-flight slot must remain None
    assert api._in_flight_command is None

    # A tE should enqueue nothing because there is no in-flight command
    api._handle_message("tE")
    assert api._retry_queue.empty()


@pytest.mark.asyncio
async def test_send_command_default_track_retry_sets_inflight_and_te_enqueues(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """Default track_retry=True sets _in_flight_command; a following tE enqueues
    exactly one item (additive-safety contrast — preserves existing behavior)."""
    api = api_with_transport

    await api.send_command("ss109600000")  # default track_retry=True

    # In-flight must be set
    assert api._in_flight_command == "ss109600000"

    # tE must enqueue the command
    api._handle_message("tE")
    assert api._retry_queue.qsize() == 1
    assert api._retry_queue.get_nowait() == "ss109600000"


# ---------------------------------------------------------------------------
# Task 2: delegation_pair() — choreography, zero-frame return
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_pair_full_choreography(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """delegation_pair() calls led_blink once, sends CMD_PAIR frame, then
    calls allow_pairing_on_device — in that order."""
    api = api_with_transport

    call_order: list[str] = []

    async def fake_led_blink(count: int) -> None:
        call_order.append("led_blink")

    async def fake_send_command(
        command: str,
        *,
        track_traffic: bool = True,
        track_retry: bool = True,
    ) -> None:
        call_order.append(f"send_command:{command}:track_retry={track_retry}")

    async def fake_allow_pairing(
        device_enum: str,
        *,
        track_retry: bool = True,
    ) -> None:
        call_order.append(
            f"allow_pairing_on_device:{device_enum}:track_retry={track_retry}"
        )

    api.led_blink = fake_led_blink  # type: ignore[method-assign, assignment]
    api.send_command = fake_send_command  # type: ignore[method-assign]
    api.allow_pairing_on_device = fake_allow_pairing  # type: ignore[method-assign]

    result = await api.delegation_pair()

    # led_blink must be first
    assert call_order[0] == "led_blink"
    # A CMD_PAIR frame with track_retry=False must appear
    assert any(
        "track_retry=False" in step
        and "CMD_PAIR" not in step
        or "60" in step
        and "track_retry=False" in step
        for step in call_order
    ), f"CMD_PAIR send with track_retry=False not found in {call_order}"
    # allow_pairing_on_device must appear last (after CMD_PAIR)
    allow_idx = next(
        (
            i
            for i, s in enumerate(call_order)
            if s.startswith("allow_pairing_on_device")
        ),
        None,
    )
    assert allow_idx is not None
    assert call_order[-1].startswith("allow_pairing_on_device")
    # Return value shape
    assert isinstance(result, tuple)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_delegation_pair_both_frames_track_retry_false(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """BOTH handshake frames carry track_retry=False (REVIEWS finding 1)."""
    api = api_with_transport

    send_kwargs: list[dict] = []
    allow_kwargs: list[dict] = []

    async def spy_send(
        command: str,
        *,
        track_traffic: bool = True,
        track_retry: bool = True,
    ) -> None:
        send_kwargs.append({"command": command, "track_retry": track_retry})
        # Don't actually write (no real transport needed for this test)

    async def spy_allow(device_enum: str, *, track_retry: bool = True) -> None:
        allow_kwargs.append({"device_enum": device_enum, "track_retry": track_retry})

    api.send_command = spy_send  # type: ignore[method-assign]
    api.allow_pairing_on_device = spy_allow  # type: ignore[method-assign]
    api.led_blink = AsyncMock()  # type: ignore[method-assign]

    await api.delegation_pair()

    # CMD_PAIR frame (contains "60" — CMD_PAIR constant)
    pair_calls = [k for k in send_kwargs if "60" in k.get("command", "")]
    assert pair_calls, "No CMD_PAIR send_command call recorded"
    assert all(k["track_retry"] is False for k in pair_calls), (
        "CMD_PAIR send must have track_retry=False"
    )

    # CMD_ALLOW_PAIRING via allow_pairing_on_device
    assert allow_kwargs, "No allow_pairing_on_device call recorded"
    assert all(k["track_retry"] is False for k in allow_kwargs), (
        "allow_pairing_on_device must be called with track_retry=False"
    )


@pytest.mark.asyncio
async def test_delegation_pair_zero_frame_return(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """delegation_pair() returns even though zero inbound frames arrive (Pitfall 3)."""
    api = api_with_transport
    api.led_blink = AsyncMock()  # type: ignore[method-assign]
    api.send_command = AsyncMock()  # type: ignore[method-assign]
    api.allow_pairing_on_device = AsyncMock()  # type: ignore[method-assign]

    # Must return without hanging (no asyncio.wait_for on _delegation_future)
    result = await asyncio.wait_for(api.delegation_pair(), timeout=2.0)
    assert isinstance(result, tuple)
    device_id, device_enum = result
    assert device_id == device_enum  # shape: (enum, enum)


@pytest.mark.asyncio
async def test_delegation_pair_post_success_state_clean(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """After successful delegation_pair(): _in_flight_command is None, retry queue
    empty, and a late tE enqueues nothing."""
    api = api_with_transport
    api.led_blink = AsyncMock()  # type: ignore[method-assign]
    api.send_command = AsyncMock()  # type: ignore[method-assign]
    api.allow_pairing_on_device = AsyncMock()  # type: ignore[method-assign]

    await api.delegation_pair()

    assert api._in_flight_command is None
    assert api._retry_queue.empty()

    # Simulate a late tE — must enqueue nothing
    api._handle_message("tE")
    assert api._retry_queue.empty()


@pytest.mark.asyncio
async def test_delegation_pair_finally_clears_future(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """After normal return, _delegation_future is None (finally block cleared it)."""
    api = api_with_transport
    api.led_blink = AsyncMock()  # type: ignore[method-assign]
    api.send_command = AsyncMock()  # type: ignore[method-assign]
    api.allow_pairing_on_device = AsyncMock()  # type: ignore[method-assign]

    await api.delegation_pair()

    assert api._delegation_future is None


# ---------------------------------------------------------------------------
# Task 2: DeviceLimitReached path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_pair_device_limit_raises(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """When initialize_next_device_enum() returns None, delegation_pair raises
    DeviceLimitReached without creating a future."""
    api = api_with_transport
    api.initialize_next_device_enum = MagicMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(DeviceLimitReached):
        await api.delegation_pair()

    # No dangling future left
    assert api._delegation_future is None


# ---------------------------------------------------------------------------
# Task 2: Concurrent-attempt guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_pair_concurrent_raises_runtime_error(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """A second call while _delegation_future is active-and-not-done raises RuntimeError."""
    api = api_with_transport
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[tuple[str, str]] = loop.create_future()
    api._delegation_future = fut  # plant an active not-done future

    with pytest.raises(RuntimeError):
        await api.delegation_pair()

    # Clean up
    fut.cancel()


# ---------------------------------------------------------------------------
# Task 2: Disconnect mid-handshake raises ConnectionError (REVIEWS finding 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_pair_disconnect_between_commands_raises(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """A disconnect landing between CMD_PAIR and CMD_ALLOW_PAIRING causes
    delegation_pair() to raise ConnectionError — never returns a tuple."""
    api = api_with_transport

    async def fake_led_blink(count: int) -> None:
        # Simulate disconnect arriving after led_blink completes
        api._is_connected = False

    api.led_blink = fake_led_blink  # type: ignore[method-assign, assignment]
    api.send_command = AsyncMock()  # type: ignore[method-assign]
    api.allow_pairing_on_device = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(ConnectionError):
        await api.delegation_pair()

    assert api._delegation_future is None


@pytest.mark.asyncio
async def test_delegation_pair_send_command_error_propagates(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """If send_command raises ConnectionError mid-handshake, delegation_pair()
    propagates it and does not return a tuple."""
    api = api_with_transport
    api.led_blink = AsyncMock()  # type: ignore[method-assign]
    api.send_command = AsyncMock(side_effect=ConnectionError("port closed"))  # type: ignore[method-assign]
    api.allow_pairing_on_device = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises((ConnectionError, OSError)):
        await api.delegation_pair()

    assert api._delegation_future is None


# ---------------------------------------------------------------------------
# Task 2: DELEGATION_TIMEOUT wraps handshake (REVIEWS finding 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_pair_timeout_raises(
    hass: HomeAssistant,
    api_with_transport: SchellenbergUsbApi,
) -> None:
    """A handshake that never completes causes delegation_pair() to raise
    (TimeoutError → ConnectionError) within the DELEGATION_TIMEOUT bound."""
    api = api_with_transport

    async def hang(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(9999)

    api.led_blink = hang  # type: ignore[method-assign]
    api.send_command = hang  # type: ignore[method-assign]
    api.allow_pairing_on_device = hang  # type: ignore[method-assign]

    # Patch the timeout to a tiny value so the test runs fast
    with patch("custom_components.schellenberg_usb.api.DELEGATION_TIMEOUT", 0.05):
        with pytest.raises((TimeoutError, ConnectionError)):
            await api.delegation_pair()

    assert api._delegation_future is None


# ---------------------------------------------------------------------------
# Task 2: abort_delegation_pair()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_delegation_pair_resolves_future(hass: HomeAssistant) -> None:
    """abort_delegation_pair() on a pending future resolves with ConnectionError
    and sets _delegation_future = None."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[tuple[str, str]] = loop.create_future()
    api._delegation_future = fut

    api.abort_delegation_pair()

    assert fut.done()
    assert isinstance(fut.exception(), ConnectionError)
    assert api._delegation_future is None


def test_abort_delegation_pair_noop_when_future_none(hass: HomeAssistant) -> None:
    """abort_delegation_pair() is a safe no-op when _delegation_future is None."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    assert api._delegation_future is None
    # Must not raise
    api.abort_delegation_pair()


@pytest.mark.asyncio
async def test_abort_delegation_pair_noop_when_future_done(
    hass: HomeAssistant,
) -> None:
    """abort_delegation_pair() is a safe no-op when future is already done."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[tuple[str, str]] = loop.create_future()
    fut.set_result(("10", "10"))
    api._delegation_future = fut

    # Must not raise
    api.abort_delegation_pair()
