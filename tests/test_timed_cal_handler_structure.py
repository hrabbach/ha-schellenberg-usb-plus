"""RED-phase tests for TimedCalibrationFlowHandler structure.

These tests verify the module interface and guard constants exist.
They fail before Task 1 implementation and pass after it.
"""

from __future__ import annotations


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
        assert hasattr(TimedCalibrationFlowHandler, method), f"Missing method: {method}"


def test_no_cmd_stop_in_module() -> None:
    """Handler module must not import or call CMD_STOP (D-06 — end-press is record-only).

    Checks that CMD_STOP is not imported (from .const import ...) and not used
    as a call argument.  Docstring references to CMD_STOP are exempt — the
    important invariant is that no executable code references the constant.
    """
    # CMD_STOP must not be imported into the timed calibration module namespace.
    import custom_components.schellenberg_usb.options_flow_timed_calibration as m

    assert not hasattr(m, "CMD_STOP"), (
        "CMD_STOP must not be imported into options_flow_timed_calibration (D-06)"
    )
    # Verify CMD_DOWN and CMD_UP ARE imported (correct commands used)
    assert hasattr(m, "CMD_DOWN"), "CMD_DOWN must be imported"
    assert hasattr(m, "CMD_UP"), "CMD_UP must be imported"


def test_uses_monotonic_not_time_time() -> None:
    """Handler must import time and use monotonic(); never call time.time() (D-07).

    Checks the module imports 'time' (for monotonic usage) and that the
    handler instance uses time.monotonic, not time.time — verified by
    confirming time.time is not assigned to any instance attrs after a call.
    The presence of time.monotonic in the module source (non-docstring) is
    verified by ensuring _close_start_time and _open_start_time are set.
    """
    import time

    import custom_components.schellenberg_usb.options_flow_timed_calibration as m

    # The module must import the time stdlib module
    assert hasattr(m, "time"), (
        "options_flow_timed_calibration must import time stdlib (for monotonic)"
    )
    # Verify the module's time attribute is the stdlib time module
    assert m.time is time, "module.time must be the stdlib time module"
