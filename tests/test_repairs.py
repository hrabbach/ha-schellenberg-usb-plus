"""Tests for repairs platform."""

from __future__ import annotations

import pytest
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from custom_components.schellenberg_usb.repairs import (
    UncalibratedMotorRepairFlow,
    async_create_fix_flow,
)


@pytest.mark.asyncio
async def test_async_create_fix_flow_returns_uncalibrated_flow(
    hass: HomeAssistant,
) -> None:
    """async_create_fix_flow returns an UncalibratedMotorRepairFlow instance."""
    flow = await async_create_fix_flow(
        hass,
        issue_id="uncalibrated_motor_ABC123",
        data=None,
    )
    assert isinstance(flow, UncalibratedMotorRepairFlow)


@pytest.mark.asyncio
async def test_async_create_fix_flow_returns_repairs_flow(
    hass: HomeAssistant,
) -> None:
    """The returned flow is a subclass of HA RepairsFlow."""
    flow = await async_create_fix_flow(
        hass,
        issue_id="uncalibrated_motor_ABC123",
        data=None,
    )
    assert isinstance(flow, RepairsFlow)
