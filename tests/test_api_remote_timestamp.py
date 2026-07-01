"""Tests for SIGNAL_REMOTE_EVENT receive-timestamp widening (Plan 12-01).

Validates that:
- SIGNAL_REMOTE_EVENT carries a 4th positional arg (receive_timestamp: float).
- The timestamp equals the time.monotonic() value captured at frame-decode time.
- Both SIGNAL_DEVICE_EVENT dispatches in the same Gate-3 block remain 3-arg (RMT-07).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    SIGNAL_DEVICE_EVENT,
    SIGNAL_REMOTE_EVENT,
)


@pytest.mark.asyncio
async def test_remote_event_carries_receive_timestamp(hass: HomeAssistant) -> None:
    """SIGNAL_REMOTE_EVENT must carry a 4th positional arg: receive_timestamp (float).

    The command byte stays in position 2 unchanged; receive_timestamp is position 3.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10REM00101ABCDPP00")

        calls = [c[0] for c in mock_send.call_args_list]
        # Find the SIGNAL_REMOTE_EVENT call
        remote_call = next(
            c for c in calls if c[1] == f"{SIGNAL_REMOTE_EVENT}_MOT001"
        )
        # Must have 4 positional args: (hass, signal, command, receive_timestamp)
        assert len(remote_call) >= 4, (
            f"Expected >= 4 positional args on SIGNAL_REMOTE_EVENT, got {len(remote_call)}"
        )
        assert remote_call[2] == "01", (
            f"Command byte (c[2]) must remain '01', got {remote_call[2]!r}"
        )
        assert isinstance(remote_call[3], float), (
            f"receive_timestamp (c[3]) must be a float, got {type(remote_call[3])}"
        )


@pytest.mark.asyncio
async def test_device_event_payload_unchanged(hass: HomeAssistant) -> None:
    """Both SIGNAL_DEVICE_EVENT dispatches in Gate-3 must stay 3-arg (RMT-07 / Pitfall F).

    Regression guard: widening SIGNAL_REMOTE_EVENT must NOT leak into the two
    SIGNAL_DEVICE_EVENT sends in the same Gate-3 block.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss10REM00101ABCDPP00")

        calls = [c[0] for c in mock_send.call_args_list]
        device_calls = [
            c for c in calls if c[1].startswith(f"{SIGNAL_DEVICE_EVENT}_")
        ]
        assert len(device_calls) == 2, (
            f"Expected exactly 2 SIGNAL_DEVICE_EVENT dispatches, got {len(device_calls)}"
        )
        for c in device_calls:
            assert len(c) == 3, (
                f"SIGNAL_DEVICE_EVENT call must have exactly 3 positional args"
                f" (hass, signal, command), got {len(c)}: {c!r}"
            )
            assert c[2] == "01", (
                f"SIGNAL_DEVICE_EVENT command byte must be '01', got {c[2]!r}"
            )


@pytest.mark.asyncio
async def test_receive_timestamp_is_monotonic_epoch(hass: HomeAssistant) -> None:
    """receive_timestamp must come from time.monotonic(), not hass.loop.time().

    Patches time.monotonic in the api module namespace to a known sentinel float
    (54321.0) and asserts that SIGNAL_REMOTE_EVENT's c[3] equals that sentinel.
    This proves the timestamp uses the same monotonic epoch as PositionTracker.calculate.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("REM001", "MOT001", "10")

    sentinel = 54321.0

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send, patch(
        "custom_components.schellenberg_usb.api.time.monotonic",
        return_value=sentinel,
    ):
        api._handle_message("ss10REM00101ABCDPP00")

        calls = [c[0] for c in mock_send.call_args_list]
        remote_call = next(
            c for c in calls if c[1] == f"{SIGNAL_REMOTE_EVENT}_MOT001"
        )
        assert len(remote_call) >= 4, (
            "receive_timestamp (c[3]) not present on SIGNAL_REMOTE_EVENT"
        )
        assert remote_call[3] == sentinel, (
            f"Expected receive_timestamp == {sentinel!r} (time.monotonic sentinel),"
            f" got {remote_call[3]!r} — timestamp source is NOT time.monotonic()"
        )
