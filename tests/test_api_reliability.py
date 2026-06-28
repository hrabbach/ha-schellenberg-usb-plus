"""Tests for API reliability — safe future resolution, disconnect drain, and connection-error paths."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.api import SchellenbergUsbApi


# ---------------------------------------------------------------------------
# Task 1 — _safe_resolve_future helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_resolve_future_sets_result(hass: HomeAssistant) -> None:
    """_safe_resolve_future sets the result on a pending future."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    future: asyncio.Future[str] = hass.loop.create_future()

    api._safe_resolve_future(future, "hello")

    assert future.done()
    assert future.result() == "hello"


@pytest.mark.asyncio
async def test_safe_resolve_future_sets_exception(hass: HomeAssistant) -> None:
    """_safe_resolve_future sets an exception on a pending future."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    future: asyncio.Future[str] = hass.loop.create_future()
    exc = ConnectionError("Serial port disconnected")

    api._safe_resolve_future(future, exception=exc)

    assert future.done()
    assert isinstance(future.exception(), ConnectionError)
    # Retrieve exception to avoid "never retrieved" GC warning
    future.exception()


@pytest.mark.asyncio
async def test_safe_resolve_future_none_future(hass: HomeAssistant) -> None:
    """_safe_resolve_future with None is a no-op (does not raise)."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    # Must not raise
    api._safe_resolve_future(None, "value")
    api._safe_resolve_future(None, exception=ConnectionError("x"))


@pytest.mark.asyncio
async def test_safe_resolve_future_already_done(hass: HomeAssistant) -> None:
    """_safe_resolve_future on an already-done future is a no-op (SC#3 guard)."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    future: asyncio.Future[str] = hass.loop.create_future()
    future.set_result("first")

    # Second call must not raise InvalidStateError
    api._safe_resolve_future(future, "second")
    api._safe_resolve_future(future, exception=ConnectionError("late"))

    # Original result is preserved
    assert future.result() == "first"


# ---------------------------------------------------------------------------
# Task 2 — except ConnectionError at the 3 wait_for sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pair_device_and_wait_connection_error(hass: HomeAssistant) -> None:
    """pair_device_and_wait returns None immediately on a mid-wait disconnect."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    # _transport is None — send_command no-ops, so the method enters wait_for

    async def disconnect_after_yield() -> None:
        await asyncio.sleep(0)  # yield so wait_for starts
        api.update_connection_status(False)

    hass.async_create_task(disconnect_after_yield())
    result = await api.pair_device_and_wait()

    assert result is None


@pytest.mark.asyncio
async def test_verify_device_connection_error(hass: HomeAssistant) -> None:
    """verify_device returns False immediately on a mid-wait disconnect."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    async def disconnect_after_yield() -> None:
        await asyncio.sleep(0)
        api.update_connection_status(False)

    hass.async_create_task(disconnect_after_yield())
    result = await api.verify_device()

    assert result is False


@pytest.mark.asyncio
async def test_get_device_id_connection_error(hass: HomeAssistant) -> None:
    """get_device_id returns None immediately on a mid-wait disconnect."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    async def disconnect_after_yield() -> None:
        await asyncio.sleep(0)
        api.update_connection_status(False)

    hass.async_create_task(disconnect_after_yield())
    result = await api.get_device_id()

    assert result is None


# ---------------------------------------------------------------------------
# Task 3 — update_connection_status disconnect drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_connection_status_fails_pairing_future(
    hass: HomeAssistant,
) -> None:
    """update_connection_status(False) drains a pending _pairing_future."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api._pairing_future = hass.loop.create_future()

    api.update_connection_status(False)

    assert api._pairing_future.done()
    assert isinstance(api._pairing_future.exception(), ConnectionError)
    # Retrieve to avoid "never retrieved" GC warning
    api._pairing_future.exception()


@pytest.mark.asyncio
async def test_update_connection_status_fails_verify_future(
    hass: HomeAssistant,
) -> None:
    """update_connection_status(False) drains a pending _verify_future."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api._verify_future = hass.loop.create_future()

    api.update_connection_status(False)

    assert api._verify_future.done()
    assert isinstance(api._verify_future.exception(), ConnectionError)
    api._verify_future.exception()


@pytest.mark.asyncio
async def test_update_connection_status_fails_device_id_future(
    hass: HomeAssistant,
) -> None:
    """update_connection_status(False) drains a pending _device_id_future."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api._device_id_future = hass.loop.create_future()

    api.update_connection_status(False)

    assert api._device_id_future.done()
    assert isinstance(api._device_id_future.exception(), ConnectionError)
    api._device_id_future.exception()


@pytest.mark.asyncio
async def test_handle_message_after_drain_no_invalid_state_error(
    hass: HomeAssistant,
) -> None:
    """A late frame after drain does not raise InvalidStateError (SC#3)."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api._verify_future = hass.loop.create_future()

    # Drain the future via disconnect
    api.update_connection_status(False)
    assert api._verify_future.done()

    # Simulate a late RFTU frame arriving in the same tick — must not raise
    import unittest.mock as mock

    with mock.patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ):
        api._handle_message("RFTU_V20 F:20180510_DFBD B:1")

    # Future keeps its ConnectionError; no InvalidStateError was raised
    assert isinstance(api._verify_future.exception(), ConnectionError)
    api._verify_future.exception()
