"""Tests for the Phase 15 learn-by-press remote binding flow (RMT-01/02/03).

Coverage:
  - Adaptive reconfigure menu routing (D-01/D-02, REVIEW finding 3)
  - Capture-state reset on menu re-entry (REVIEW finding 2 + 4)
  - First-press capture with progress sequence (RMT-01, D-03)
  - Timeout / disconnect distinct copy with _listen_error_key carry (D-05)
  - Double-press match -> confirm, mismatch -> retry (D-06)
  - Binding policy: motor rejection (Case A) + already-bound rejection (Case B)
    with _listen_error_key carry vars (D-07, REVIEW finding 4)
  - Re-press of own remote during change is allowed (Case C, D-10)
  - Confirm is a TWO-option menu (REVIEW finding 1, D-08/RMT-02)
  - Confirm-Apply persists via async_update_subentry + schedule_reload + abort
  - Retry path does NOT persist
  - Change reuses listen_confirm step; NO async_step_change_confirm (REVIEW 6)
  - Change overwrites remote_id (D-10)
  - Remove confirm is a TWO-option menu (REVIEW finding 1, D-09)
  - Remove-Apply deletes CONF_REMOTE_ID key (RMT-03/D-09)

Test environment note: hass.async_create_task() in the HA pytest harness causes
the underlying AsyncMock coroutine to resolve within the same event loop
iteration — so the first call to async_step_listen_first/second sees the task
already done and returns SHOW_PROGRESS_DONE directly. Tests are written to
reflect this synchronous-resolution behaviour.

All tests pin the decision IDs / REVIEW findings they verify in their docstring.
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.schellenberg_usb.config_flow import (
    SchellenbergPairingSubentryFlow,
)
from custom_components.schellenberg_usb.const import (
    CONF_BIDIRECTIONAL,
    CONF_REMOTE_ID,
    CONF_SERIAL_PORT,
    DOMAIN,
    SUBENTRY_TYPE_BLIND,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_reconfigure_handler(
    hass: HomeAssistant, entry_id: str, subentry_id: str = "sub1"
) -> SchellenbergPairingSubentryFlow:
    """Create a reconfigure-context flow handler for Phase 15 tests.

    ConfigSubentryFlow._get_entry() reads self.handler[0].
    ConfigSubentryFlow._get_reconfigure_subentry() reads
    self.context["subentry_id"].
    """
    handler = SchellenbergPairingSubentryFlow()
    handler.hass = hass
    handler.handler = (entry_id, SUBENTRY_TYPE_BLIND)
    handler.context = {"source": "reconfigure", "subentry_id": subentry_id}
    return handler


@pytest.fixture
def mock_hub_entry(hass: HomeAssistant) -> ConfigEntry:
    """Create a mock hub ConfigEntry registered with hass, with a mock api."""
    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        options={},
        entry_id="test_remote_bind_entry",
        state=ConfigEntryState.NOT_LOADED,
        minor_version=1,
        source="test",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    mock_api = MagicMock()
    mock_api.learn_remote_raw_and_wait = AsyncMock(return_value="AABBCC")
    mock_api._registered_devices = {}
    mock_api._remote_to_motor = {}
    mock_api.is_connected = True
    # WR-05: the flow now calls public accessors instead of poking the private
    # registration dicts. Back them with side_effects that read the (possibly
    # per-test reassigned) dicts at call time so existing policy tests are
    # exercised through the real public surface.
    mock_api.is_registered_motor = MagicMock(
        side_effect=lambda did: (
            did in mock_api._registered_devices
            and did not in mock_api._remote_to_motor
        )
    )
    mock_api.bound_motor_for = MagicMock(
        side_effect=lambda rid: mock_api._remote_to_motor.get(rid)
    )
    entry.runtime_data = mock_api  # type: ignore[attr-defined]
    return entry


def _make_no_remote_subentry() -> MagicMock:
    """Subentry without a bound remote (bind path)."""
    subentry = MagicMock()
    subentry.data = {
        "device_id": "ABCDEF",
        "device_enum": "1A",
        CONF_BIDIRECTIONAL: False,
    }
    subentry.title = "Test Blind"
    return subentry


def _make_with_remote_subentry(remote_id: str = "112233") -> MagicMock:
    """Subentry with an existing bound remote (change/remove path)."""
    subentry = MagicMock()
    subentry.data = {
        "device_id": "ABCDEF",
        "device_enum": "1A",
        CONF_BIDIRECTIONAL: False,
        CONF_REMOTE_ID: remote_id,
    }
    subentry.title = "Test Blind"
    return subentry


def _extract_persisted_data(mock_upd: MagicMock) -> dict:
    """Extract data dict from an async_update_subentry mock call.

    Handles both keyword-argument (data=...) and positional (args[2]) call forms.
    """
    call_kwargs = mock_upd.call_args[1] if mock_upd.call_args[1] else {}
    call_args = mock_upd.call_args[0]
    if "data" in call_kwargs:
        return dict(call_kwargs["data"])
    if len(call_args) >= 3:
        return dict(call_args[2])
    return {}


async def _drive_listen_first(
    handler: SchellenbergPairingSubentryFlow,
    subentry: MagicMock,
    entry: ConfigEntry,
) -> None:
    """Drive async_step_listen_first to completion.

    In the HA pytest harness AsyncMock tasks resolve synchronously so a single
    call with one event-loop tick is sufficient to reach SHOW_PROGRESS_DONE.
    """
    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=entry):
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            # Task not yet done — give the event loop one tick
            await asyncio.sleep(0)
            result = await handler.async_step_listen_first(None)


async def _drive_listen_second(
    handler: SchellenbergPairingSubentryFlow,
    subentry: MagicMock,
    entry: ConfigEntry,
) -> None:
    """Drive async_step_listen_second to completion."""
    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=entry):
        result = await handler.async_step_listen_second(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            await handler.async_step_listen_second(None)


# ---------------------------------------------------------------------------
# Adaptive menu routing (D-01/D-02, REVIEW finding 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconfigure_menu_no_binding_shows_bind(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_reconfigure on a subentry without remote_id shows bind_remote.

    Calls async_step_reconfigure DIRECTLY (REVIEW finding 3) so menu-routing
    coverage is not bypassed by the pinned-test calibrate-substitution edits.
    Pins: D-01 (adaptive menu entry), D-02 (no remote -> bind option).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ):
        result = await handler.async_step_reconfigure(None)

    assert result["type"] == "menu", (
        f"Expected menu, got {result['type']!r}"
    )
    assert "bind_remote" in result["menu_options"], (
        f"bind_remote missing from {result['menu_options']}"
    )
    assert "calibrate" in result["menu_options"]
    assert "change_remote" not in result["menu_options"]
    assert "remove_remote" not in result["menu_options"]
    # Regression (menu-title-formatjs-error): the reconfigure_menu title is
    # "Configure {device_name}"; the menu MUST supply device_name or the
    # frontend formatjs renders MISSING_VALUE as the menu title.
    assert result["description_placeholders"]["device_name"] == "Test Blind", (
        "reconfigure_menu must pass description_placeholders['device_name'] "
        "(strings title is 'Configure {device_name}')."
    )


@pytest.mark.asyncio
async def test_reconfigure_menu_with_binding_shows_change_remove(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_reconfigure on a subentry WITH remote_id shows change+remove.

    Calls async_step_reconfigure DIRECTLY (REVIEW finding 3).
    Pins: D-01 (adaptive menu entry), D-02 (has remote -> change/remove).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_with_remote_subentry()
    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ):
        result = await handler.async_step_reconfigure(None)

    assert result["type"] == "menu"
    assert "change_remote" in result["menu_options"]
    assert "remove_remote" in result["menu_options"]
    assert "calibrate" in result["menu_options"]
    assert "bind_remote" not in result["menu_options"]
    # Regression (menu-title-formatjs-error): supply device_name for the
    # "Configure {device_name}" title.
    assert result["description_placeholders"]["device_name"] == "Test Blind", (
        "reconfigure_menu must pass description_placeholders['device_name']."
    )


@pytest.mark.asyncio
async def test_reconfigure_menu_resets_capture_state(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_reconfigure_menu resets all capture state and re-shows menu.

    Seeds stale capture state (including a done task) and verifies that
    async_step_reconfigure_menu clears it all (REVIEW finding 2 + finding 4).
    Uses a DONE (not pending) task to avoid cross-loop cancellation timing
    issues in the test harness.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()

    # Seed stale state
    handler._first_capture_id = "STALE1"
    handler._listen_error_key = "remote_capture_timeout"
    handler._listen_error_placeholders = {"k": "v"}
    handler._is_change_mode = True

    # Create a pending task to be cancelled
    cancel_called = False

    class _FakeTask:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            nonlocal cancel_called
            cancel_called = True

    handler._listen_first_task = _FakeTask()  # type: ignore[assignment]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ):
        result = await handler.async_step_reconfigure_menu(None)

    # Capture state must be cleared
    assert handler._first_capture_id is None
    assert handler._listen_error_key is None
    assert handler._listen_error_placeholders is None
    assert handler._is_change_mode is False
    assert handler._listen_first_task is None

    # cancel() must have been called on the pending task
    assert cancel_called is True, "Expected cancel() to be called on pending task"

    # Menu must re-show
    assert result["type"] == "menu"
    assert "bind_remote" in result["menu_options"]


# ---------------------------------------------------------------------------
# First-press capture sequence (RMT-01, D-03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_first_captures_id(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """listen_first progress sequence resolves and advances toward listen_second.

    In the HA pytest harness an AsyncMock task may resolve synchronously so the
    step can return SHOW_PROGRESS_DONE on the first poll. Either SHOW_PROGRESS
    or SHOW_PROGRESS_DONE is acceptable on the first call; after settling the
    handler must store _first_capture_id and indicate next_step=listen_second.
    Pins: RMT-01 (learn-by-press captures remote id).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = AsyncMock(  # type: ignore[union-attr]
        return_value="AABBCC"
    )
    mock_hub_entry.runtime_data._registered_devices = {}  # type: ignore[union-attr]
    mock_hub_entry.runtime_data._remote_to_motor = {}  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            result = await handler.async_step_listen_first(None)

    # After the task settles the step must advance (SHOW_PROGRESS_DONE)
    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
    # The captured id must be stored
    assert handler._first_capture_id == "AABBCC"


@pytest.mark.asyncio
async def test_listen_first_progress_supplies_device_name(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """listen_first SHOW_PROGRESS result must carry device_name placeholder.

    Regression (menu-title-formatjs-error): the step.listen_first.description is
    "Press any button on the remote you want to bind to {device_name}. …". The
    async_show_progress render site MUST supply description_placeholders with
    device_name, or the frontend formatjs renders MISSING_VALUE on the screen
    shown immediately after the user clicks "Bind a remote".

    Uses a capture coroutine gated on an asyncio.Event that is never set, so the
    task stays pending and the step returns SHOW_PROGRESS (not the harness's
    synchronous SHOW_PROGRESS_DONE) — the only result type that exposes the
    progress step's description_placeholders.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()

    never_done = asyncio.Event()

    async def _pending_capture(timeout: float = 15.0) -> str | None:
        await never_done.wait()  # never set -> task stays pending
        return None

    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = _pending_capture  # type: ignore[union-attr]

    try:
        with patch.object(
            handler, "_get_reconfigure_subentry", return_value=subentry
        ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
            result = await handler.async_step_listen_first(None)

        assert result["type"] == FlowResultType.SHOW_PROGRESS, (
            f"Expected SHOW_PROGRESS while capture pending, got {result['type']!r}"
        )
        assert (
            result["description_placeholders"]["device_name"] == "Test Blind"
        ), (
            "listen_first progress step must pass "
            "description_placeholders['device_name'] (strings description uses "
            "{device_name})."
        )
    finally:
        # Release the pending task so it doesn't leak into the event loop.
        never_done.set()
        task = handler._listen_first_task
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Progress-poll re-entry of a COMPLETED capture task (remote-bind-press-stuck)
# ---------------------------------------------------------------------------
#
# Regression for the "listening screen hangs forever, no timeout" bug. In
# production learn_remote_raw_and_wait() SUSPENDS (asyncio.wait_for on a future),
# so the first async_step_listen_first(None) returns SHOW_PROGRESS with a PENDING
# task. HA then registers progress_task.add_done_callback(schedule_configure),
# which re-invokes async_step_listen_first(None) the instant the task completes.
# On that re-entry the task is .done(); the step MUST read its result and advance
# (SHOW_PROGRESS_DONE) — it must NOT clear the done task and re-arm a fresh
# capture window (which loops forever, swallowing both the press and the timeout).
#
# The pre-existing tests never caught this because they mock the capture with an
# AsyncMock that resolves synchronously (eager task done on creation), so they
# read the result on the FIRST call and never exercise the pending->done re-entry.


class _GatedCapture:
    """A real (suspending) learn_remote_raw_and_wait stand-in.

    Awaits an asyncio.Event, then returns a preset result — reproducing the
    production coroutine that suspends on a future until a press resolves it
    (or the timeout fires). Unlike an AsyncMock it does NOT resolve eagerly, so
    the created task is genuinely pending and drives the real HA progress-task
    re-entry path.
    """

    def __init__(self, result: str | None) -> None:
        self._result = result
        self.gate = asyncio.Event()
        self.call_count = 0

    async def __call__(self, timeout: float = 15.0) -> str | None:
        self.call_count += 1
        await self.gate.wait()
        return self._result


async def _await_task(handler: SchellenbergPairingSubentryFlow) -> None:
    """Let the pending _listen_first_task run to completion."""
    task = handler._listen_first_task
    assert task is not None
    await task


@pytest.mark.asyncio
async def test_listen_first_completed_task_reentry_advances(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """A completed capture task on progress-poll re-entry advances, not re-arms.

    Reproduces remote-bind-press-stuck: first call spawns a genuinely PENDING
    capture -> SHOW_PROGRESS. After the task resolves (press captured), the
    framework re-invokes the step with user_input=None. That re-entry MUST read
    the result and return SHOW_PROGRESS_DONE with _first_capture_id set — NOT
    clear the done task and spawn a fresh capture (the forever-spinner bug).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    capture = _GatedCapture(result="AABBCC")
    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = capture  # type: ignore[union-attr]
    mock_hub_entry.runtime_data._registered_devices = {}  # type: ignore[union-attr]
    mock_hub_entry.runtime_data._remote_to_motor = {}  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        # First entry: pending task -> SHOW_PROGRESS
        first = await handler.async_step_listen_first(None)
        assert first["type"] == FlowResultType.SHOW_PROGRESS, (
            f"Expected SHOW_PROGRESS while capture pending, got {first['type']!r}"
        )
        pending_task = handler._listen_first_task

        # Resolve the capture (simulate the remote press) and let it complete —
        # this is when HA fires the add_done_callback re-entry.
        capture.gate.set()
        await _await_task(handler)
        assert pending_task is not None and pending_task.done()

        # Framework re-entry (user_input=None, same step). MUST advance.
        second = await handler.async_step_listen_first(None)

    assert second["type"] == FlowResultType.SHOW_PROGRESS_DONE, (
        "progress-poll re-entry of a COMPLETED capture must advance "
        "(SHOW_PROGRESS_DONE); got "
        f"{second['type']!r} — the done task was cleared and a fresh capture "
        "re-armed (the forever-spinner re-arm loop)."
    )
    assert handler._first_capture_id == "AABBCC"
    # Exactly ONE capture window must have been opened (no re-arm).
    assert capture.call_count == 1, (
        f"capture re-armed {capture.call_count} times — expected exactly 1"
    )


@pytest.mark.asyncio
async def test_listen_first_completed_timeout_reentry_advances(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """A completed capture that TIMED OUT (None) must surface the timeout screen.

    Same re-entry mechanics as above but the capture returns None (15s timeout).
    The re-entry MUST route to listen_timeout (SHOW_PROGRESS_DONE) — not re-arm a
    fresh capture, which is why the user saw no timeout at all.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    capture = _GatedCapture(result=None)  # timeout
    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = capture  # type: ignore[union-attr]
    mock_hub_entry.runtime_data.is_connected = True  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        first = await handler.async_step_listen_first(None)
        assert first["type"] == FlowResultType.SHOW_PROGRESS
        capture.gate.set()
        await _await_task(handler)
        second = await handler.async_step_listen_first(None)

    assert second["type"] == FlowResultType.SHOW_PROGRESS_DONE, (
        "timed-out capture re-entry must surface listen_timeout, not re-arm"
    )
    assert handler._listen_error_key == "remote_capture_timeout"
    assert capture.call_count == 1


@pytest.mark.asyncio
async def test_listen_second_completed_task_reentry_advances(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """listen_second has the identical re-entry contract (symmetry check).

    A matching second press whose capture task completes must, on the framework
    re-entry, advance to listen_confirm rather than re-arm a fresh capture.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    handler._first_capture_id = "AABBCC"
    capture = _GatedCapture(result="AABBCC")  # matching second press
    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = capture  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        first = await handler.async_step_listen_second(None)
        assert first["type"] == FlowResultType.SHOW_PROGRESS
        second_task = handler._listen_second_task
        capture.gate.set()
        assert second_task is not None
        await second_task
        second = await handler.async_step_listen_second(None)

    assert second["type"] == FlowResultType.SHOW_PROGRESS_DONE, (
        "listen_second re-entry of a completed matching press must advance to "
        "listen_confirm, not re-arm a fresh capture"
    )
    assert capture.call_count == 1


# ---------------------------------------------------------------------------
# Timeout / disconnect distinct copy (D-05, REVIEW finding 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_timeout_shows_retry_no_persist(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """listen_first timeout routes to listen_timeout with remote_capture_timeout.

    Verifies the _listen_error_key carry var is set and rendered (REVIEW finding 4).
    async_update_subentry must NOT be called.
    Pins: D-05 (no binding on failure).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = AsyncMock(  # type: ignore[union-attr]
        return_value=None
    )
    mock_hub_entry.runtime_data.is_connected = True  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            result = await handler.async_step_listen_first(None)

    # Must route to listen_timeout with timeout key
    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
    assert handler._listen_error_key == "remote_capture_timeout"

    # Drive the listen_timeout form (user_input=None path)
    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(
        handler.hass.config_entries, "async_update_subentry"
    ) as mock_upd:
        form_result = await handler.async_step_listen_timeout(None)

    assert form_result["type"] == "form"
    assert form_result["errors"]["base"] == "remote_capture_timeout"
    mock_upd.assert_not_called()


@pytest.mark.asyncio
async def test_capture_disconnect_distinct_copy(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """listen_first disconnect routes to listen_timeout with remote_capture_disconnected.

    Verifies that is_connected=False triggers the distinct disconnect error key.
    Pins: D-05, REVIEW finding 4.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = AsyncMock(  # type: ignore[union-attr]
        return_value=None
    )
    mock_hub_entry.runtime_data.is_connected = False  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            await handler.async_step_listen_first(None)

    assert handler._listen_error_key == "remote_capture_disconnected"

    with patch.object(handler, "_get_reconfigure_subentry", return_value=subentry):
        form_result = await handler.async_step_listen_timeout(None)

    assert form_result["errors"]["base"] == "remote_capture_disconnected"


# ---------------------------------------------------------------------------
# Double-press verify (D-06)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_press_match_reaches_confirm(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Matching second press reaches listen_confirm (a menu result).

    Uses side_effect to return the first id on the first call and the second
    (matching) id on the second call to learn_remote_raw_and_wait.
    Pins: D-06 (double-press verify), RMT-01 (capture success).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()

    call_count = 0

    async def _mock_capture(timeout: float = 15.0) -> str | None:
        nonlocal call_count
        call_count += 1
        return "AABBCC"  # both presses return the same id

    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = _mock_capture  # type: ignore[union-attr]
    mock_hub_entry.runtime_data._registered_devices = {}  # type: ignore[union-attr]
    mock_hub_entry.runtime_data._remote_to_motor = {}  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        # Drive listen_first to completion
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            result = await handler.async_step_listen_first(None)
        assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
        assert handler._first_capture_id == "AABBCC"

        # Drive listen_second to completion (matching press)
        result2 = await handler.async_step_listen_second(None)
        if result2["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            result2 = await handler.async_step_listen_second(None)
        assert result2["type"] == FlowResultType.SHOW_PROGRESS_DONE

        # HA routes to listen_confirm
        result_confirm = await handler.async_step_listen_confirm(None)

    assert result_confirm["type"] == "menu"
    assert "listen_confirm_apply" in result_confirm["menu_options"]
    assert "listen_first" in result_confirm["menu_options"]
    # Regression (menu-title-formatjs-error): listen_confirm title/description
    # use {device_name}; the code must pass 'device_name' (not 'motor_name') or
    # the frontend formatjs renders MISSING_VALUE.
    assert (
        result_confirm["description_placeholders"]["device_name"]
        == "Test Blind"
    ), "listen_confirm must pass description_placeholders['device_name']."


@pytest.mark.asyncio
async def test_double_press_mismatch_errors(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Mismatched second press routes to listen_timeout with remote_press_mismatch.

    First press captures "AABBCC"; second press captures "DDEEFF" (different).
    Pins: D-06, REVIEW finding 4.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()

    call_count = 0

    async def _mock_capture(timeout: float = 15.0) -> str | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "AABBCC"
        return "DDEEFF"  # second press: different remote

    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = _mock_capture  # type: ignore[union-attr]
    mock_hub_entry.runtime_data._registered_devices = {}  # type: ignore[union-attr]
    mock_hub_entry.runtime_data._remote_to_motor = {}  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        # Drive listen_first (first press)
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            result = await handler.async_step_listen_first(None)
        assert handler._first_capture_id == "AABBCC"

        # Drive listen_second (second press — mismatch)
        result2 = await handler.async_step_listen_second(None)
        if result2["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            result2 = await handler.async_step_listen_second(None)

    assert handler._listen_error_key == "remote_press_mismatch"
    assert handler._first_capture_id is None

    with patch.object(handler, "_get_reconfigure_subentry", return_value=subentry):
        form = await handler.async_step_listen_timeout(None)

    assert form["type"] == "form"
    assert form["errors"]["base"] == "remote_press_mismatch"


# ---------------------------------------------------------------------------
# Binding policy rejections (D-07/SC4, REVIEW finding 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_is_a_motor(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Captured id that is a registered motor -> listen_timeout with remote_is_motor.

    Pins: D-07 (policy Case A), REVIEW finding 4 (_listen_error_key carry).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()

    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = AsyncMock(  # type: ignore[union-attr]
        return_value="AABBCC"
    )
    # Case A: id is a registered motor AND not in _remote_to_motor
    mock_hub_entry.runtime_data._registered_devices = {"AABBCC": "10"}  # type: ignore[union-attr]
    mock_hub_entry.runtime_data._remote_to_motor = {}  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(
        handler, "_get_entry", return_value=mock_hub_entry
    ), patch.object(
        handler.hass.config_entries, "async_update_subentry"
    ) as mock_upd:
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            result = await handler.async_step_listen_first(None)

    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
    assert handler._listen_error_key == "remote_is_motor"
    assert handler._listen_error_placeholders is None
    mock_upd.assert_not_called()

    with patch.object(handler, "_get_reconfigure_subentry", return_value=subentry):
        form = await handler.async_step_listen_timeout(None)

    assert form["errors"]["base"] == "remote_is_motor"


@pytest.mark.asyncio
async def test_reject_already_bound_elsewhere(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Captured id bound to a DIFFERENT motor -> listen_timeout with remote_already_bound.

    Verifies that description_placeholders["other_motor_name"] is the other
    motor's subentry title (REVIEW finding 4 placeholder carry).
    Pins: D-07 (policy Case B), REVIEW finding 4.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    # This subentry's motor is "ABCDEF"
    subentry = _make_no_remote_subentry()

    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = AsyncMock(  # type: ignore[union-attr]
        return_value="AABBCC"
    )
    mock_hub_entry.runtime_data._registered_devices = {}  # type: ignore[union-attr]
    # "AABBCC" is bound to "OTHERMOT" (a different motor)
    mock_hub_entry.runtime_data._remote_to_motor = {"AABBCC": "OTHERMOT"}  # type: ignore[union-attr]

    # Register a sibling subentry for the other motor
    other_subentry = MagicMock()
    other_subentry.data = {"device_id": "OTHERMOT"}
    other_subentry.title = "Living Room Blind"
    mock_hub_entry.subentries = {  # type: ignore[attr-defined]
        "sub1": subentry,
        "sub2": other_subentry,
    }

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(
        handler, "_get_entry", return_value=mock_hub_entry
    ), patch.object(
        handler.hass.config_entries, "async_update_subentry"
    ) as mock_upd:
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            result = await handler.async_step_listen_first(None)

    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
    assert handler._listen_error_key == "remote_already_bound"
    assert handler._listen_error_placeholders == {
        "other_motor_name": "Living Room Blind"
    }
    mock_upd.assert_not_called()

    with patch.object(handler, "_get_reconfigure_subentry", return_value=subentry):
        form = await handler.async_step_listen_timeout(None)

    assert form["errors"]["base"] == "remote_already_bound"
    assert form["description_placeholders"]["other_motor_name"] == (
        "Living Room Blind"
    )


# ---------------------------------------------------------------------------
# Confirm as two-option menu (REVIEW finding 1, D-08/RMT-02)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_confirm_is_two_option_menu(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_listen_confirm returns a menu with listen_confirm_apply + listen_first.

    Pins: REVIEW finding 1 (confirm must be async_show_menu, not a form).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    handler._first_capture_id = "AABBCC"

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ):
        result = await handler.async_step_listen_confirm(None)

    assert result["type"] == "menu"
    assert "listen_confirm_apply" in result["menu_options"]
    assert "listen_first" in result["menu_options"]


@pytest.mark.asyncio
async def test_confirm_apply_persists_remote_id(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """listen_confirm_apply persists CONF_REMOTE_ID via the safe 3-call pattern.

    Pins: RMT-02 (confirm before persist), D-08 (Confirm persist).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    handler._first_capture_id = "AABBCC"

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(
        handler, "_get_entry", return_value=mock_hub_entry
    ), patch.object(
        handler.hass.config_entries, "async_update_subentry"
    ) as mock_upd, patch.object(
        handler.hass.config_entries, "async_schedule_reload"
    ) as mock_reload:
        result = await handler.async_step_listen_confirm_apply(None)

    mock_upd.assert_called_once()
    persisted_data = _extract_persisted_data(mock_upd)

    assert CONF_REMOTE_ID in persisted_data, (
        f"CONF_REMOTE_ID missing from persisted data: {persisted_data}"
    )
    assert persisted_data[CONF_REMOTE_ID] == "AABBCC"
    mock_reload.assert_called_once_with(mock_hub_entry.entry_id)
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"


@pytest.mark.asyncio
async def test_retry_does_not_persist(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """The listen_first (Retry) branch of the confirm menu starts capture; NO persist.

    Selecting "listen_first" from the confirm menu re-enters the capture flow.
    It must NOT call async_update_subentry.
    Pins: RMT-02 (only Confirm persists, Retry never calls update_subentry).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    handler._first_capture_id = "AABBCC"
    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = AsyncMock(  # type: ignore[union-attr]
        return_value="AABBCC"
    )
    mock_hub_entry.runtime_data._registered_devices = {}  # type: ignore[union-attr]
    mock_hub_entry.runtime_data._remote_to_motor = {}  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(
        handler, "_get_entry", return_value=mock_hub_entry
    ), patch.object(
        handler.hass.config_entries, "async_update_subentry"
    ) as mock_upd:
        # Retry -> re-enters listen_first (spawns new task)
        result = await handler.async_step_listen_first(None)

    # Must NOT have called update_subentry regardless of task resolution
    mock_upd.assert_not_called()
    # Result is either SHOW_PROGRESS (task pending) or SHOW_PROGRESS_DONE
    assert result["type"] in (
        FlowResultType.SHOW_PROGRESS,
        FlowResultType.SHOW_PROGRESS_DONE,
    )


# ---------------------------------------------------------------------------
# Change reuses listen_confirm; NO async_step_change_confirm (REVIEW finding 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_reuses_listen_confirm(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Change path lands on listen_confirm (NOT change_confirm); _is_change_mode=True.

    Verifies REVIEW finding 6: the change path reuses async_step_listen_confirm
    and NO async_step_change_confirm method exists on the handler.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_with_remote_subentry("112233")
    handler._is_change_mode = True
    handler._first_capture_id = "AABBCC"

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ):
        result = await handler.async_step_listen_confirm(None)

    # Must be a menu (listen_confirm), not a form or abort
    assert result["type"] == "menu"
    # current_remote_id placeholder must be present in the change path
    assert "current_remote_id" in result.get("description_placeholders", {})
    assert result["description_placeholders"]["current_remote_id"] == "112233"

    # No change_confirm step must exist (REVIEW finding 6)
    assert not hasattr(handler, "async_step_change_confirm"), (
        "async_step_change_confirm must NOT exist (REVIEW finding 6)"
    )


@pytest.mark.asyncio
async def test_change_overwrites_remote_id(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Change path confirm-apply overwrites remote_id with the new captured id.

    Pins: D-10 (change overwrites in a single reload), RMT-03.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_with_remote_subentry("112233")
    handler._is_change_mode = True
    handler._first_capture_id = "AABBCC"

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(
        handler, "_get_entry", return_value=mock_hub_entry
    ), patch.object(
        handler.hass.config_entries, "async_update_subentry"
    ) as mock_upd, patch.object(
        handler.hass.config_entries, "async_schedule_reload"
    ) as mock_reload:
        result = await handler.async_step_listen_confirm_apply(None)

    mock_upd.assert_called_once()
    persisted_data = _extract_persisted_data(mock_upd)

    assert persisted_data.get(CONF_REMOTE_ID) == "AABBCC", (
        f"Expected new remote AABBCC, got {persisted_data.get(CONF_REMOTE_ID)!r}"
    )
    mock_reload.assert_called_once_with(mock_hub_entry.entry_id)
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"


@pytest.mark.asyncio
async def test_change_repress_own_remote_allowed(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """Re-press of the current remote during change (Case C) is allowed.

    Policy Case C: the captured id is in _remote_to_motor but points to THIS
    motor (device_id == "ABCDEF") — the flow must NOT reject it, and must store
    _first_capture_id and advance toward listen_second.
    Pins: D-10 (Case C allowed), D-07 (Cases A/B rejected, C allowed).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    # Subentry's motor is "ABCDEF"; currently bound to "AABBCC"
    subentry = _make_with_remote_subentry("AABBCC")

    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = AsyncMock(  # type: ignore[union-attr]
        return_value="AABBCC"
    )
    mock_hub_entry.runtime_data._registered_devices = {}  # type: ignore[union-attr]
    # "AABBCC" is in _remote_to_motor but points to THIS motor (Case C)
    mock_hub_entry.runtime_data._remote_to_motor = {  # type: ignore[union-attr]
        "AABBCC": "ABCDEF"
    }

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            result = await handler.async_step_listen_first(None)

    # Must NOT be rejected — _listen_error_key must not be set for a policy error
    assert handler._first_capture_id == "AABBCC", (
        "Case C (re-press of own remote) must NOT be rejected"
    )
    # Result must advance (SHOW_PROGRESS_DONE toward listen_second)
    assert result["type"] == FlowResultType.SHOW_PROGRESS_DONE
    assert handler._listen_error_key is None


# ---------------------------------------------------------------------------
# Remove confirm as two-option menu (REVIEW finding 1, D-09/RMT-03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_confirm_is_two_option_menu(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """async_step_remove_confirm returns a menu with remove_confirm_apply + reconfigure_menu.

    Pins: REVIEW finding 1 (remove confirm must be async_show_menu, not a form),
          D-09 (Remove vs Cancel gates the delete).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_with_remote_subentry()

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ):
        result = await handler.async_step_remove_confirm(None)

    assert result["type"] == "menu"
    assert "remove_confirm_apply" in result["menu_options"]
    assert "reconfigure_menu" in result["menu_options"]
    # Regression (menu-title-formatjs-error): remove_confirm title/description
    # use {device_name}; the code must pass 'device_name' (not 'motor_name').
    assert (
        result["description_placeholders"]["device_name"] == "Test Blind"
    ), "remove_confirm must pass description_placeholders['device_name']."


@pytest.mark.asyncio
async def test_remove_confirm_apply_deletes_key(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """remove_confirm_apply deletes CONF_REMOTE_ID from subentry data.

    Verifies the safe 3-call persist pattern and that CONF_REMOTE_ID is absent
    from the data dict passed to async_update_subentry.
    Pins: RMT-03 (remove binding), D-09 (gate: explicit confirm before delete).
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_with_remote_subentry("112233")

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(
        handler, "_get_entry", return_value=mock_hub_entry
    ), patch.object(
        handler.hass.config_entries, "async_update_subentry"
    ) as mock_upd, patch.object(
        handler.hass.config_entries, "async_schedule_reload"
    ) as mock_reload:
        result = await handler.async_step_remove_confirm_apply(None)

    mock_upd.assert_called_once()
    persisted_data = _extract_persisted_data(mock_upd)

    assert CONF_REMOTE_ID not in persisted_data, (
        f"CONF_REMOTE_ID must NOT be in persisted data: {persisted_data}"
    )
    mock_reload.assert_called_once_with(mock_hub_entry.entry_id)
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"


# ---------------------------------------------------------------------------
# Capture-state hygiene on fresh listen_first entry (WR-03 / WR-04)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_first_clears_stale_capture_id_on_reentry(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """A fresh listen_first clears a stale _first_capture_id (WR-03).

    Simulates a confirm-/timeout-"Try again" edge that re-enters listen_first
    with a leftover id, where the retry capture then times out — so the success
    branch that would re-set _first_capture_id is never reached. Only the
    fresh-entry reset can clear the stale id, ensuring it can never be carried
    into a later confirm/persist.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()
    handler._first_capture_id = "STALE1"  # leftover from a prior round
    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = AsyncMock(  # type: ignore[union-attr]
        return_value=None  # capture times out
    )
    mock_hub_entry.runtime_data.is_connected = True  # type: ignore[union-attr]

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            await handler.async_step_listen_first(None)

    assert handler._first_capture_id is None, (
        "stale _first_capture_id must be cleared on a fresh listen_first entry "
        "(WR-03)"
    )


@pytest.mark.asyncio
async def test_listen_first_cancels_leftover_second_task(
    hass: HomeAssistant, mock_hub_entry: ConfigEntry
) -> None:
    """A fresh listen_first cancels a leftover second-capture task (WR-04).

    Seeds a pending _listen_second_task from an abandoned prior round and
    re-enters listen_first via a fresh edge that bypasses reconfigure_menu. The
    leftover task must be cancelled and cleared so it cannot resolve into the
    new round.
    """
    handler = _make_reconfigure_handler(hass, mock_hub_entry.entry_id)
    subentry = _make_no_remote_subentry()

    cancelled = False

    class _FakeTask:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            nonlocal cancelled
            cancelled = True

    handler._listen_second_task = _FakeTask()  # type: ignore[assignment]
    mock_hub_entry.runtime_data.learn_remote_raw_and_wait = AsyncMock(  # type: ignore[union-attr]
        return_value="AABBCC"
    )

    with patch.object(
        handler, "_get_reconfigure_subentry", return_value=subentry
    ), patch.object(handler, "_get_entry", return_value=mock_hub_entry):
        result = await handler.async_step_listen_first(None)
        if result["type"] == FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0)
            await handler.async_step_listen_first(None)

    assert cancelled is True, (
        "leftover _listen_second_task must be cancelled on a fresh listen_first "
        "entry (WR-04)"
    )
    assert handler._listen_second_task is None
