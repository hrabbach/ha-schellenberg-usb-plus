"""Repairs platform for Schellenberg USB."""

from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant


class UncalibratedMotorRepairFlow(ConfirmRepairFlow):
    """Fix flow: direct user to run timed calibration via Configure."""


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create fix flow for uncalibrated motor repair issue."""
    return UncalibratedMotorRepairFlow()
