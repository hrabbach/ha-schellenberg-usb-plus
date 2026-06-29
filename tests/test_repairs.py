"""Tests for repairs platform."""

from __future__ import annotations

import pytest
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity

from custom_components.schellenberg_usb.const import DOMAIN
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


@pytest.mark.asyncio
async def test_confirm_flow_renders_with_device_name_placeholder(
    hass: HomeAssistant,
) -> None:
    """Test that the confirm form includes device_name placeholder."""
    # Register the repair issue in the HA issue registry with placeholders
    issue_id = "uncalibrated_motor_ABC123"
    device_name = "Living Room Blind"

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=IssueSeverity.WARNING,
        translation_key="uncalibrated_motor",
        translation_placeholders={"device_name": device_name},
        learn_more_url=(
            "https://github.com/hrabbach/ha-schellenberg-usb-plus/"
            "blob/main/README.md#timed-calibration-for-non-bidirectional-motors"
        ),
    )

    # Obtain the fix flow
    flow = await async_create_fix_flow(
        hass,
        issue_id=issue_id,
        data=None,
    )

    # Wire the flow with the hass instance and context attributes
    # (as RepairsFlowManager would do)
    flow.hass = hass
    flow.handler = DOMAIN  # The domain (repair namespace)
    flow.issue_id = issue_id  # The issue ID within that domain

    # Call async_step_init to start the flow
    # UncalibratedMotorRepairFlow inherits async_step_init from ConfirmRepairFlow
    result = await flow.async_step_init()  # type: ignore[attr-defined]

    # Assert that the result is a FORM step with description_placeholders
    assert result["type"] == "form"
    assert result["step_id"] == "confirm"
    # The placeholder should propagate through the form result
    assert "description_placeholders" in result
    assert result["description_placeholders"]["device_name"] == device_name
