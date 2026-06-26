"""RED-phase tests for TimedCalibrationFlowHandler structure.

These tests verify the module interface and guard constants exist.
They fail before Task 1 implementation and pass after it.
"""

from __future__ import annotations

import pytest


def test_module_imports() -> None:
    """Handler module and class must be importable (D-01, CAL-01)."""
    from custom_components.schellenberg_usb import (
        options_flow_timed_calibration,
    )
    from custom_components.schellenberg_usb.options_flow_timed_calibration import (
        TimedCalibrationFlowHandler,
    )

    assert hasattr(options_flow_timed_calibration, "TimedCalibrationFlowHandler")
    assert TimedCalibrationFlowHandler is not None


def test_guard_constants_exist() -> None:
    """CAL_MAX_TRAVEL_TIME and CAL_MIN_TRAVEL_TIME must be present (D-08/D-09)."""
    from custom_components.schellenberg_usb.const import (
        CAL_MAX_TRAVEL_TIME,
        CAL_MIN_TRAVEL_TIME,
    )

    assert CAL_MAX_TRAVEL_TIME == 120
    assert CAL_MIN_TRAVEL_TIME == 2


def test_handler_methods_exist() -> None:
    """Handler must expose all required async_step_* methods (CAL-01)."""
    from custom_components.schellenberg_usb.options_flow_timed_calibration import (
        TimedCalibrationFlowHandler,
    )

    required = [
        "set_selected_device",
        "async_step_timed_cal_precondition",
        "async_step_timed_cal_close",
        "async_step_timed_cal_open",
        "async_step_timed_cal_confirm",
        "_emit_calibration_signal",
    ]
    for method in required:
        assert hasattr(TimedCalibrationFlowHandler, method), (
            f"Missing method: {method}"
        )


def test_no_cmd_stop_in_module() -> None:
    """Handler module must not reference CMD_STOP (D-06 — end-press is record-only)."""
    import inspect

    from custom_components.schellenberg_usb import (
        options_flow_timed_calibration,
    )

    source = inspect.getsource(options_flow_timed_calibration)
    assert "CMD_STOP" not in source, (
        "options_flow_timed_calibration must not reference CMD_STOP (D-06)"
    )


def test_uses_monotonic_not_time_time() -> None:
    """Handler must use time.monotonic(), never time.time() (D-07)."""
    import inspect

    from custom_components.schellenberg_usb import (
        options_flow_timed_calibration,
    )

    source = inspect.getsource(options_flow_timed_calibration)
    assert "time.time(" not in source, (
        "Must use time.monotonic(), not time.time() (D-07)"
    )
    assert source.count("time.monotonic(") >= 2, (
        "Expected at least 2 uses of time.monotonic() (D-07)"
    )
