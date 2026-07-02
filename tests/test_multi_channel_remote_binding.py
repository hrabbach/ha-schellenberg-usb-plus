"""Regression suite: multi-channel remote binding via (enum, id) key.

Covers three regression groups:

  Task 1 — Framework-seam bind test
    Channel-2 of the same physical remote (same id, different enum) binds to a
    second motor without the v1.3 "remote_already_bound" rejection, and persists
    both CONF_REMOTE_ID and CONF_REMOTE_ENUM.  The test enters via the real HA
    subentry-flow seam (hass.config_entries.subentries.async_init + async_configure)
    — NOT via SchellenbergPairingSubentryFlow() private-step instantiation.

  Task 2 — API frame-level routing + dedup
    enum-scoped routing: channel-1 and channel-2 frames route exclusively to their
    respective motors (no cross-talk).  enum-scoped dedup: a frame from channel-2
    sharing channel-1's incrementor is NOT suppressed; a genuine same-channel
    burst-tail repeat IS suppressed.

  Task 3 — Legacy fallback
    A pre-v1.4 bind registered with remote_enum=None routes frames of any inbound
    enum via the (None, id) wildcard slot.  is_registered_motor returns False for
    a bound remote id.  Unregistering the legacy bind stops routing.  A legacy
    (None, id) bind and a v1.4 (enum, id) bind coexist and each routes correctly.

Seam note: the v1.1.0 regression postmortem established that seam tests must enter
via hass.config_entries.subentries.async_init, not by calling async_step_* directly.
test_learn_by_press_flow.py was intentionally NOT migrated to the seam (per the Plan
17-04 cross-AI review decision); this file contains the single new seam test that
satisfies the phase's seam-coverage requirement.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.schellenberg_usb.api import SchellenbergUsbApi
from custom_components.schellenberg_usb.const import (
    CONF_BIDIRECTIONAL,
    CONF_REMOTE_ENUM,
    CONF_REMOTE_ID,
    CONF_SERIAL_PORT,
    DOMAIN,
    SIGNAL_DEVICE_EVENT,
    SIGNAL_REMOTE_EVENT,
    SUBENTRY_TYPE_BLIND,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _extract_persisted_data(mock_upd: MagicMock) -> dict:
    """Extract data dict from an async_update_subentry mock call.

    Handles both keyword-argument (data=...) and positional (args[2]) forms.
    """
    call_kwargs = mock_upd.call_args[1] if mock_upd.call_args[1] else {}
    call_args = mock_upd.call_args[0]
    if "data" in call_kwargs:
        return dict(call_kwargs["data"])
    if len(call_args) >= 3:
        return dict(call_args[2])
    return {}


# ---------------------------------------------------------------------------
# Task 1 — Framework-seam multi-channel bind tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seam_channel2_binds_and_persists_both_keys(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """Seam: channel-2 binds without remote_already_bound; both keys persisted.

    Regression for the v1.3 bug where bound_motor_for checked id-only:
    channel-2 (enum=13, id=7C055A) triggered Case B "already bound" rejection
    because channel-1 (enum=33, same id) was registered.

    After Plan 17-02: bound_motor_for(enum, id) is enum-aware, so (enum=13,
    id=7C055A) returns None when only (enum=33) is registered — bind proceeds.

    Entry point: hass.config_entries.subentries.async_init with reconfigure
    context — the HA seam, NOT SchellenbergPairingSubentryFlow() instantiation.

    Implementation note (eager tasks): HA Core 2024.5+ uses eager task factory
    for hass.async_create_task.  AsyncMock coroutines have no blocking awaits
    so each task completes inside async_create_task before the step checks
    task.done().  Both listen_first and listen_second therefore run within the
    single async_configure call (the SHOW_PROGRESS_DONE while-loop auto-drives
    them).  No asyncio.sleep(0) is needed between captures.

    Same-channel double-press: both first and second presses in each bind
    return the same (enum, id) pair, modelling a genuine second press of the
    same channel.  listen_second's tuple comparison accepts this.
    """
    # ----- Hub entry with two blind subentries (real HA ConfigSubentry objects)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        entry_id="seam-hub-01",
        subentries_data=[
            {
                "subentry_id": "motorA-sub",
                "subentry_type": SUBENTRY_TYPE_BLIND,
                "data": {
                    "device_id": "MOTORAA",
                    "device_enum": "1A",
                    CONF_BIDIRECTIONAL: False,
                },
                "title": "Motor A",
                "unique_id": None,
            },
            {
                "subentry_id": "motorB-sub",
                "subentry_type": SUBENTRY_TYPE_BLIND,
                "data": {
                    "device_id": "MOTORBB",
                    "device_enum": "1B",
                    CONF_BIDIRECTIONAL: False,
                },
                "title": "Motor B",
                "unique_id": None,
            },
        ],
    )
    entry.add_to_hass(hass)

    # Mock api — accessors read from _remote_to_motor at call time so the
    # side_effects see updates made between bind #1 and bind #2.
    mock_api = MagicMock()
    mock_api._remote_to_motor = {}  # type: ignore[var-annotated]
    mock_api.is_connected = True
    # Case A check: no motor IDs share the 7C055A device_id in this test.
    mock_api.is_registered_motor = MagicMock(return_value=False)

    # Case B check: Plan 17-05 — flow now calls bound_motor_match (not
    # bound_motor_for).  Mirror the real accessor: specific key first, then
    # (None, id) wildcard.  bound_motor_for kept for any direct assertions.
    def _bound_motor_match(renum: str | None, rid: str) -> tuple[str | None, str]:
        motor = mock_api._remote_to_motor.get((renum, rid))
        if motor is not None:
            return (motor, "specific")
        motor = mock_api._remote_to_motor.get((None, rid))
        if motor is not None:
            return (motor, "wildcard")
        return (None, "none")

    mock_api.bound_motor_match = MagicMock(side_effect=_bound_motor_match)
    mock_api.bound_motor_for = MagicMock(
        side_effect=lambda renum, rid: mock_api._remote_to_motor.get((renum, rid))
    )
    entry.runtime_data = mock_api  # type: ignore[attr-defined]

    # ==================================================================
    # Bind #1 — motor A, channel-1: enum=33, id=7C055A
    #
    # Both presses return ("33","7C055A").  With eager tasks the whole chain
    # (listen_first -> SHOW_PROGRESS_DONE -> listen_second -> SHOW_PROGRESS_DONE
    # -> listen_confirm) runs inside a single async_configure call.
    # ==================================================================
    mock_api.learn_remote_raw_and_wait = AsyncMock(return_value=("33", "7C055A"))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BLIND),
        context={"source": "reconfigure", "subentry_id": "motorA-sub"},
    )
    assert result["type"] == FlowResultType.MENU  # reconfigure_menu
    flow_id_a = result["flow_id"]

    # One call drives the full bind: eager AsyncMock tasks complete synchronously
    # so listen_first and listen_second both finish, SHOW_PROGRESS_DONE auto-
    # advances the while-loop, and listen_confirm is returned.
    result = await hass.config_entries.subentries.async_configure(
        flow_id_a, {"next_step_id": "bind_remote"}
    )
    assert result["type"] == FlowResultType.MENU, (
        f"Expected listen_confirm menu after bind_remote, got {result!r}"
    )
    assert result.get("step_id") == "listen_confirm", (
        f"Expected step_id=listen_confirm, got {result.get('step_id')!r}"
    )
    # Both presses captured ("33","7C055A") — confirm step reflects remote_id.
    assert (result.get("description_placeholders") or {}).get("remote_id") == "7C055A"

    # Apply — patch to capture persisted data and prevent storage I/O/reload.
    with (
        patch.object(hass.config_entries, "async_schedule_reload"),
        patch.object(hass.config_entries, "async_update_subentry") as mock_upd_a,
    ):
        result = await hass.config_entries.subentries.async_configure(
            flow_id_a, {"next_step_id": "listen_confirm_apply"}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    # Motor A subentry must persist both enum and id for channel-1.
    persisted_a = _extract_persisted_data(mock_upd_a)
    assert persisted_a[CONF_REMOTE_ID] == "7C055A", (
        "CONF_REMOTE_ID missing or wrong after bind #1"
    )
    assert persisted_a[CONF_REMOTE_ENUM] == "33", (
        "CONF_REMOTE_ENUM missing or wrong after bind #1"
    )

    # Simulate post-reload: register channel-1 binding on the api so Case B
    # sees it when bind #2 checks bound_motor_for("13", "7C055A").
    mock_api._remote_to_motor[("33", "7C055A")] = "MOTORAA"

    # ==================================================================
    # Bind #2 — motor B, channel-2: enum=13, SAME id=7C055A
    #
    # Regression assertion: v1.3 would have returned FORM at listen_timeout
    # (error "remote_already_bound") because bound_motor_for(id) returned
    # "MOTORAA".  After Plan 17-02: bound_motor_for("13","7C055A") returns
    # None (only ("33","7C055A") is registered), so Case B passes and the
    # full bind proceeds to listen_confirm.
    #
    # Same-channel double-press (plan requirement): both presses return
    # (enum=13, id=7C055A) — listen_second's tuple comparison accepts this
    # as a genuine second press of the same channel (not a mismatch).
    # ==================================================================
    mock_api.learn_remote_raw_and_wait = AsyncMock(return_value=("13", "7C055A"))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BLIND),
        context={"source": "reconfigure", "subentry_id": "motorB-sub"},
    )
    assert result["type"] == FlowResultType.MENU  # reconfigure_menu
    flow_id_b = result["flow_id"]

    result = await hass.config_entries.subentries.async_configure(
        flow_id_b, {"next_step_id": "bind_remote"}
    )
    # KEY REGRESSION ASSERTION: channel-2 must reach listen_confirm (MENU),
    # NOT listen_timeout (FORM with remote_already_bound error).
    assert result["type"] == FlowResultType.MENU, (
        f"channel-2 bind reached wrong state: type={result['type']!r} "
        f"step_id={result.get('step_id')!r}. "
        "Regression: v1.3 bound_motor_for checked id-only so channel-2 "
        "(enum=13, id=7C055A) was rejected because channel-1 (enum=33, "
        "same id) was already bound to MOTORAA."
    )
    assert result.get("step_id") == "listen_confirm", (
        f"Expected step_id=listen_confirm, got {result.get('step_id')!r}"
    )
    # Both presses captured ("13","7C055A") — remote_id placeholder set.
    assert (result.get("description_placeholders") or {}).get("remote_id") == "7C055A"

    # Apply channel-2 binding.
    with (
        patch.object(hass.config_entries, "async_schedule_reload"),
        patch.object(hass.config_entries, "async_update_subentry") as mock_upd_b,
    ):
        result = await hass.config_entries.subentries.async_configure(
            flow_id_b, {"next_step_id": "listen_confirm_apply"}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    # Motor B subentry must persist channel-2 values (enum=13, not enum=33).
    persisted_b = _extract_persisted_data(mock_upd_b)
    assert persisted_b[CONF_REMOTE_ID] == "7C055A", (
        "CONF_REMOTE_ID missing or wrong after bind #2"
    )
    assert persisted_b[CONF_REMOTE_ENUM] == "13", (
        "CONF_REMOTE_ENUM must carry channel-2 enum (13), not channel-1 (33)"
    )


# ---------------------------------------------------------------------------
# Plan 17-05 (IN-02) — legacy-slot upgrade path regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seam_legacy_slot_channel2_reaches_migrate_confirm(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """IN-02: channel-2 to a different motor reaches listen_confirm_migrate when
    channel-1 is stored as the legacy (None, id) slot — the primary v1.3 upgrade path.

    Regression for the gap found in 17-VERIFICATION.md WR-01: when channel-1 was
    bound WITHOUT CONF_REMOTE_ENUM (legacy v1.3 bind), it is stored as
    _remote_to_motor[(None, "7C055A")] = "MOTORAA".  A channel-2 bind (enum=13,
    same id) for a different motor (Motor B) must NOT be rejected with
    remote_already_bound — it must reach listen_confirm_migrate so the user can
    confirm the sibling-channel bind.

    The (None, "7C055A") legacy slot must remain untouched after the sibling bind
    (no backfill — CONTEXT.md design decision).

    Also locks the inverse: a SPECIFIC slot collision (enum=13,"7C055A") to a
    different motor IS still rejected with remote_already_bound.
    """
    # ----- Hub entry with two blind subentries
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        entry_id="seam-hub-in02",
        subentries_data=[
            {
                "subentry_id": "motorA-in02",
                "subentry_type": SUBENTRY_TYPE_BLIND,
                "data": {
                    "device_id": "MOTORAA",
                    "device_enum": "1A",
                    CONF_BIDIRECTIONAL: False,
                },
                "title": "Motor A",
                "unique_id": None,
            },
            {
                "subentry_id": "motorB-in02",
                "subentry_type": SUBENTRY_TYPE_BLIND,
                "data": {
                    "device_id": "MOTORBB",
                    "device_enum": "1B",
                    CONF_BIDIRECTIONAL: False,
                },
                "title": "Motor B",
                "unique_id": None,
            },
        ],
    )
    entry.add_to_hass(hass)

    # Mock api with the legacy (None, "7C055A") slot pre-seeded — this is the
    # state a v1.3 upgrader has: channel-1 was stored without CONF_REMOTE_ENUM.
    mock_api = MagicMock()
    mock_api._remote_to_motor = {
        (None, "7C055A"): "MOTORAA"  # legacy slot (IN-02 key state)
    }
    mock_api.is_connected = True
    mock_api.is_registered_motor = MagicMock(return_value=False)

    def _bound_motor_match(renum: str | None, rid: str) -> tuple[str | None, str]:
        motor = mock_api._remote_to_motor.get((renum, rid))
        if motor is not None:
            return (motor, "specific")
        motor = mock_api._remote_to_motor.get((None, rid))
        if motor is not None:
            return (motor, "wildcard")
        return (None, "none")

    mock_api.bound_motor_match = MagicMock(side_effect=_bound_motor_match)
    mock_api.bound_motor_for = MagicMock(
        side_effect=lambda renum, rid: mock_api._remote_to_motor.get((renum, rid))
    )
    entry.runtime_data = mock_api  # type: ignore[attr-defined]

    # -----------------------------------------------------------------
    # Bind channel-2 (enum=13, id=7C055A) for Motor B.
    # With the legacy (None,"7C055A") slot, bound_motor_match("13","7C055A")
    # returns ("MOTORAA", "wildcard") — Case B must ALLOW (sibling path).
    # -----------------------------------------------------------------
    mock_api.learn_remote_raw_and_wait = AsyncMock(return_value=("13", "7C055A"))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BLIND),
        context={"source": "reconfigure", "subentry_id": "motorB-in02"},
    )
    assert result["type"] == FlowResultType.MENU  # reconfigure_menu
    flow_id = result["flow_id"]

    result = await hass.config_entries.subentries.async_configure(
        flow_id, {"next_step_id": "bind_remote"}
    )
    # KEY IN-02 ASSERTION: must reach listen_confirm_MIGRATE (not listen_timeout).
    # Before Plan 17-05 fix, the wildcard fallback returned MOTORAA for any enum
    # of "7C055A", causing Case B to reject with remote_already_bound.
    assert result["type"] == FlowResultType.MENU, (
        f"IN-02 REGRESSION: channel-2 bind with legacy (None,id) slot did not "
        f"reach listen_confirm_migrate. Got type={result['type']!r} "
        f"step_id={result.get('step_id')!r}. "
        "The v1.3 upgrade path is still broken."
    )
    assert result.get("step_id") == "listen_confirm_migrate", (
        f"Expected step_id=listen_confirm_migrate (sibling path), "
        f"got {result.get('step_id')!r}"
    )
    # Placeholders must all be supplied to avoid formatjs MISSING_VALUE.
    ph = result.get("description_placeholders") or {}
    assert ph.get("remote_id") == "7C055A", (
        "listen_confirm_migrate must supply remote_id placeholder"
    )
    assert ph.get("device_name") == "Motor B", (
        "listen_confirm_migrate must supply device_name placeholder"
    )
    assert ph.get("other_motor_name") == "Motor A", (
        "listen_confirm_migrate must supply other_motor_name placeholder"
    )

    # Apply the sibling bind and verify persistence.
    with (
        patch.object(hass.config_entries, "async_schedule_reload"),
        patch.object(hass.config_entries, "async_update_subentry") as mock_upd,
    ):
        result = await hass.config_entries.subentries.async_configure(
            flow_id, {"next_step_id": "listen_confirm_apply"}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    # Motor B must persist the specific (enum=13) channel.
    persisted = _extract_persisted_data(mock_upd)
    assert persisted.get(CONF_REMOTE_ENUM) == "13", (
        "Sibling bind must persist CONF_REMOTE_ENUM='13' for Motor B"
    )
    assert persisted.get(CONF_REMOTE_ID) == "7C055A", (
        "Sibling bind must persist CONF_REMOTE_ID='7C055A' for Motor B"
    )

    # The legacy (None,"7C055A") slot on Motor A must NOT be modified (no backfill).
    assert mock_api._remote_to_motor.get((None, "7C055A")) == "MOTORAA", (
        "No-backfill: the legacy (None,id) slot on Motor A must be unchanged"
        " after the sibling bind (CONTEXT.md design decision)"
    )


@pytest.mark.asyncio
async def test_seam_specific_slot_collision_still_rejected(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """Regression guard: a specific-slot collision to a different motor is still rejected.

    When (enum=13, id="7C055A") is already registered to a different motor via a
    SPECIFIC slot (not a legacy None slot), Case B must still reject with
    remote_already_bound — the foreign-remote rejection must not be weakened.

    This is the complement to test_seam_legacy_slot_channel2_reaches_migrate_confirm:
    specific → reject; wildcard → allow.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Schellenberg USB",
        data={CONF_SERIAL_PORT: "/dev/ttyUSB0"},
        entry_id="seam-hub-specific",
        subentries_data=[
            {
                "subentry_id": "motorA-spec",
                "subentry_type": SUBENTRY_TYPE_BLIND,
                "data": {
                    "device_id": "MOTORAA",
                    "device_enum": "1A",
                    CONF_BIDIRECTIONAL: False,
                },
                "title": "Motor A",
                "unique_id": None,
            },
            {
                "subentry_id": "motorB-spec",
                "subentry_type": SUBENTRY_TYPE_BLIND,
                "data": {
                    "device_id": "MOTORBB",
                    "device_enum": "1B",
                    CONF_BIDIRECTIONAL: False,
                },
                "title": "Motor B",
                "unique_id": None,
            },
        ],
    )
    entry.add_to_hass(hass)

    mock_api = MagicMock()
    # Channel enum=13 is already bound as a SPECIFIC slot to Motor A.
    mock_api._remote_to_motor = {("13", "7C055A"): "MOTORAA"}
    mock_api.is_connected = True
    mock_api.is_registered_motor = MagicMock(return_value=False)

    def _bound_motor_match(renum: str | None, rid: str) -> tuple[str | None, str]:
        motor = mock_api._remote_to_motor.get((renum, rid))
        if motor is not None:
            return (motor, "specific")
        motor = mock_api._remote_to_motor.get((None, rid))
        if motor is not None:
            return (motor, "wildcard")
        return (None, "none")

    mock_api.bound_motor_match = MagicMock(side_effect=_bound_motor_match)
    mock_api.bound_motor_for = MagicMock(
        side_effect=lambda renum, rid: mock_api._remote_to_motor.get((renum, rid))
    )
    entry.runtime_data = mock_api  # type: ignore[attr-defined]

    # Attempt to bind (enum=13, id=7C055A) for Motor B — must be rejected.
    mock_api.learn_remote_raw_and_wait = AsyncMock(return_value=("13", "7C055A"))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BLIND),
        context={"source": "reconfigure", "subentry_id": "motorB-spec"},
    )
    flow_id = result["flow_id"]

    result = await hass.config_entries.subentries.async_configure(
        flow_id, {"next_step_id": "bind_remote"}
    )
    # Must reach listen_timeout (FORM) with remote_already_bound error.
    assert result["type"] == FlowResultType.FORM, (
        f"Expected listen_timeout FORM for specific-slot collision, "
        f"got type={result['type']!r} step_id={result.get('step_id')!r}"
    )
    assert result.get("step_id") == "listen_timeout", (
        f"Expected step_id=listen_timeout, got {result.get('step_id')!r}"
    )
    errors = result.get("errors") or {}
    assert errors.get("base") == "remote_already_bound", (
        "Foreign-remote rejection must use error key 'remote_already_bound'"
    )


# ---------------------------------------------------------------------------
# Task 2 — API frame-level routing: no cross-talk between channels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routing_no_cross_talk_between_channels(
    hass: HomeAssistant,
) -> None:
    """Gate 3: enum-33 routes ONLY to motorA; enum-13 routes ONLY to motorB.

    Registers two channels of the same physical remote (id=7C055A) bound to
    different motors, then verifies that each inbound frame dispatches ONLY to
    its registered motor — no cross-talk via the other channel.

    This is the API-layer form of the main regression scenario from the debug
    session (pair-second-channel-no-button.md).
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    # Channel-1: enum=33 → motor A
    api.register_remote("7C055A", "33", "MOTORAA", "1A")
    # Channel-2: enum=13 → motor B
    api.register_remote("7C055A", "13", "MOTORBB", "1B")

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        # Feed enum-33 frame (incrementor ABCD, fresh key)
        api._handle_message("ss337C055A01ABCDPP00")

        calls_after_ch1 = mock_send.call_count
        signals_ch1 = {c[0][1] for c in mock_send.call_args_list}

        # Reset to check channel-2 in isolation
        mock_send.reset_mock()

        # Feed enum-13 frame (different incrementor EFGH to avoid dedup)
        api._handle_message("ss137C055A01EFGHPP00")

        signals_ch2 = {c[0][1] for c in mock_send.call_args_list}

    # Channel-1 (enum=33) triple-dispatches to motorA only.
    assert calls_after_ch1 == 3, (
        f"Expected 3 dispatches for channel-1 frame, got {calls_after_ch1}"
    )
    assert f"{SIGNAL_REMOTE_EVENT}_MOTORAA" in signals_ch1
    assert f"{SIGNAL_DEVICE_EVENT}_MOTORAA" in signals_ch1
    assert f"{SIGNAL_DEVICE_EVENT}_7C055A" in signals_ch1
    # No cross-talk to motorB
    assert f"{SIGNAL_REMOTE_EVENT}_MOTORBB" not in signals_ch1
    assert f"{SIGNAL_DEVICE_EVENT}_MOTORBB" not in signals_ch1

    # Channel-2 (enum=13) triple-dispatches to motorB only.
    assert mock_send.call_count == 3, (
        f"Expected 3 dispatches for channel-2 frame, got {mock_send.call_count}"
    )
    assert f"{SIGNAL_REMOTE_EVENT}_MOTORBB" in signals_ch2
    assert f"{SIGNAL_DEVICE_EVENT}_MOTORBB" in signals_ch2
    assert f"{SIGNAL_DEVICE_EVENT}_7C055A" in signals_ch2
    # No cross-talk to motorA
    assert f"{SIGNAL_REMOTE_EVENT}_MOTORAA" not in signals_ch2
    assert f"{SIGNAL_DEVICE_EVENT}_MOTORAA" not in signals_ch2


@pytest.mark.asyncio
async def test_dedup_shared_incrementor_cross_channel_not_suppressed(
    hass: HomeAssistant,
) -> None:
    """Dedup key includes the enum — channel-2 shares channel-1's incrementor.

    A channel-1 frame (enum=33, incr=XXYY) is processed first, caching the
    key ("33", "7C055A", "XXYY").  A channel-2 frame (enum=13, SAME incr=XXYY)
    arrives next: its dedup key ("13", "7C055A", "XXYY") is NOT in the cache,
    so it is dispatched (not suppressed).

    Then a genuine burst-tail repeat of channel-2 (same enum=13, same incr=XXYY)
    arrives: NOW the key is in the cache and the frame IS suppressed.

    This proves dedup is enum-scoped, not id-scoped.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("7C055A", "33", "MOTORAA", "1A")  # channel-1
    api.register_remote("7C055A", "13", "MOTORBB", "1B")  # channel-2

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        # Frame 1: channel-1, incrementor=XXYY → cached key ("33","7C055A","XXYY")
        api._handle_message("ss337C055A01XXYYPP00")
        assert mock_send.call_count == 3, "channel-1 frame must triple-dispatch"

        # Frame 2: channel-2, SAME incrementor=XXYY — cross-channel, NOT suppressed
        api._handle_message("ss137C055A01XXYYPP00")
        # dedup key ("13","7C055A","XXYY") is not in cache → dispatches
        assert mock_send.call_count == 6, (
            "channel-2 frame sharing channel-1's incrementor must NOT be "
            "suppressed (dedup key includes enum, so the keys differ)"
        )

        # Frame 3: genuine burst-tail — same channel-2, same incrementor=XXYY
        api._handle_message("ss137C055A01XXYYPP00")
        # dedup key ("13","7C055A","XXYY") IS now in cache → suppressed
        assert mock_send.call_count == 6, (
            "true burst-tail repeat (same enum=13, same incr=XXYY) must be "
            "suppressed by the dedup gate"
        )


# ---------------------------------------------------------------------------
# Task 3 — Legacy fallback (remote_enum=None) + coexistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_none_enum_routes_for_any_enum(
    hass: HomeAssistant,
) -> None:
    """A (None, remote_id) legacy bind routes frames of ANY inbound enum.

    Exercises the Gate 3 wildcard fallback: when a subentry was persisted
    before CONF_REMOTE_ENUM existed, register_remote is called with
    remote_enum=None.  A frame from that remote — arriving with any channel
    enum — resolves to motorL via the (None, id) slot.

    Also asserts that bound_motor_for(None, id) returns motorL directly.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("AABBCC", None, "MOTORLL", "1C")

    # Direct accessor resolves via the (None, id) slot.
    assert api.bound_motor_for(None, "AABBCC") == "MOTORLL", (
        "bound_motor_for(None, id) must return the legacy-bound motor"
    )

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        # Frame arrives with enum=77 (not stored as remote_enum); legacy fallback
        # matches (None, "AABBCC") and triple-dispatches to motorL.
        api._handle_message("ss77AABBCC01ABCDPP00")

        assert mock_send.call_count == 3, (
            "legacy (None, id) bind must triple-dispatch for any-enum frame"
        )
        signals = {c[0][1] for c in mock_send.call_args_list}
        assert f"{SIGNAL_REMOTE_EVENT}_MOTORLL" in signals
        assert f"{SIGNAL_DEVICE_EVENT}_MOTORLL" in signals
        assert f"{SIGNAL_DEVICE_EVENT}_AABBCC" in signals


@pytest.mark.asyncio
async def test_legacy_is_registered_motor_false_for_bound_remote(
    hass: HomeAssistant,
) -> None:
    """is_registered_motor is False for a legacy-bound remote id.

    After register_remote("AABBCC", None, motorL, motorEnum), "AABBCC" IS in
    _registered_devices (motor_enum stored for dedup suppression) but IS also
    in _remote_to_motor via the (None, "AABBCC") slot.  is_registered_motor
    uses _is_bound_remote_id to distinguish motors from remotes — it must
    return False for "AABBCC" even though the id is in _registered_devices.

    Cross-AI review MEDIUM: this assertion cross-references the exact Plan-01
    is_registered_motor body.  If this fails, re-check the Plan-01
    implementation rather than weakening the assertion.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("AABBCC", None, "MOTORLL", "1C")

    # "AABBCC" is in _registered_devices (register_remote stores the motor enum
    # there for known-device suppression) but is a bound REMOTE — not a motor.
    assert "AABBCC" in api._registered_devices, (
        "register_remote must add remote_id to _registered_devices"
    )
    assert api.is_registered_motor("AABBCC") is False, (
        "is_registered_motor must return False for a bound remote id "
        "(it checks _is_bound_remote_id to exclude remotes)"
    )


@pytest.mark.asyncio
async def test_legacy_unregister_stops_routing(hass: HomeAssistant) -> None:
    """Unregistering the legacy bind removes routing; frame is no longer handled.

    After unregister_remote("AABBCC", None), the (None, "AABBCC") slot is
    removed from _remote_to_motor and "AABBCC" is removed from
    _registered_devices.  An inbound frame for AABBCC should no longer
    triple-dispatch to motorL.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("AABBCC", None, "MOTORLL", "1C")

    # Confirm routing works before unregister.
    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss77AABBCC01ABCDPP00")
        assert mock_send.call_count == 3

    # Unregister: removes both the (None, "AABBCC") slot and _registered_devices.
    api.unregister_remote("AABBCC", None)

    assert (None, "AABBCC") not in api._remote_to_motor, (
        "legacy (None, id) slot must be removed after unregister"
    )
    assert "AABBCC" not in api._registered_devices, (
        "remote_id must be removed from _registered_devices after unregister"
    )

    # No longer routed: inbound frame falls through to unknown-device warning.
    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        api._handle_message("ss77AABBCC01EFGHPP00")
        # Unknown device: single SIGNAL_DEVICE_EVENT dispatch (existing final dispatch)
        signals = [c[0][1] for c in mock_send.call_args_list]
        assert f"{SIGNAL_REMOTE_EVENT}_MOTORLL" not in signals, (
            "SIGNAL_REMOTE_EVENT must not fire after unregister"
        )
        assert f"{SIGNAL_DEVICE_EVENT}_MOTORLL" not in signals, (
            "SIGNAL_DEVICE_EVENT to motorL must not fire after unregister"
        )


@pytest.mark.asyncio
async def test_legacy_and_v14_coexist_both_route_correctly(
    hass: HomeAssistant,
) -> None:
    """Legacy (None, id_X) bind and v1.4 (enum, id_Y) bind coexist correctly.

    Registers both a legacy bind for id_X=AABBCC (remote_enum=None) and a
    v1.4-style bind for id_Y=DDEEFF (enum=99).  Each inbound frame routes
    to its correct motor via Gate 3 without interference.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("AABBCC", None, "MOTORLL", "1C")  # legacy
    api.register_remote("DDEEFF", "99", "MOTORMM", "1D")  # v1.4

    with patch(
        "custom_components.schellenberg_usb.api.async_dispatcher_send"
    ) as mock_send:
        # Legacy frame (enum=77, id=AABBCC) → motorL via (None, "AABBCC") fallback
        api._handle_message("ss77AABBCC01ABCDPP00")
        signals_legacy = {c[0][1] for c in mock_send.call_args_list}
        assert f"{SIGNAL_REMOTE_EVENT}_MOTORLL" in signals_legacy
        assert f"{SIGNAL_REMOTE_EVENT}_MOTORMM" not in signals_legacy

        mock_send.reset_mock()

        # v1.4 frame (enum=99, id=DDEEFF) → motorM via ("99", "DDEEFF") direct
        api._handle_message("ss99DDEEFF01EFGHPP00")
        signals_v14 = {c[0][1] for c in mock_send.call_args_list}
        assert f"{SIGNAL_REMOTE_EVENT}_MOTORMM" in signals_v14
        assert f"{SIGNAL_REMOTE_EVENT}_MOTORLL" not in signals_v14


# ---------------------------------------------------------------------------
# Task 1 (Plan 17-05) — bound_motor_match accessor unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bound_motor_match_specific_hit(hass: HomeAssistant) -> None:
    """bound_motor_match returns ("MOTORAA", "specific") for an exact-enum key.

    When (remote_enum, remote_id) is registered directly, the accessor must
    report the specific match-kind so Case B can identify a genuine
    exact-channel collision.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("7C055A", "33", "MOTORAA", "1A")

    motor_id, match_kind = api.bound_motor_match("33", "7C055A")
    assert motor_id == "MOTORAA", (
        f"Expected MOTORAA for specific-enum hit, got {motor_id!r}"
    )
    assert match_kind == "specific", (
        f"Expected match_kind='specific', got {match_kind!r}"
    )


@pytest.mark.asyncio
async def test_bound_motor_match_wildcard_hit(hass: HomeAssistant) -> None:
    """bound_motor_match returns ("MOTORLL", "wildcard") for a legacy (None, id) slot.

    When only (None, remote_id) is registered (pre-v1.4 legacy bind), a lookup
    with any specific enum must fall back to that slot and report the wildcard
    match-kind — signalling a legacy-sibling, not a genuine exact-channel
    collision.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    # Register under the legacy (None, id) slot, as v1.3 persisted binds.
    api.register_remote("AABBCC", None, "MOTORLL", "1C")

    motor_id, match_kind = api.bound_motor_match("13", "AABBCC")
    assert motor_id == "MOTORLL", (
        f"Expected MOTORLL for wildcard fallback, got {motor_id!r}"
    )
    assert match_kind == "wildcard", (
        f"Expected match_kind='wildcard' for (None, id) fallback, got {match_kind!r}"
    )


@pytest.mark.asyncio
async def test_bound_motor_match_none_hit(hass: HomeAssistant) -> None:
    """bound_motor_match returns (None, "none") for an unregistered channel."""
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")

    motor_id, match_kind = api.bound_motor_match("99", "FFFFFF")
    assert motor_id is None, f"Expected None for unbound id, got {motor_id!r}"
    assert match_kind == "none", f"Expected match_kind='none', got {match_kind!r}"


@pytest.mark.asyncio
async def test_bound_motor_for_unchanged_by_bound_motor_match(
    hass: HomeAssistant,
) -> None:
    """bound_motor_for still returns the bare motor id (unchanged by Plan 17-05).

    bound_motor_match is an additive sibling; it must not alter bound_motor_for's
    signature or return type.  The existing test_legacy_none_enum_routes_for_any_enum
    assertion (bound_motor_for(None, id) == motor_id) is the normative contract;
    this test locks the same contract for a specific-enum call.
    """
    api = SchellenbergUsbApi(hass, "/dev/ttyUSB0")
    api.register_remote("AABBCC", None, "MOTORLL", "1C")

    # bound_motor_for must still return the bare motor id string.
    assert api.bound_motor_for(None, "AABBCC") == "MOTORLL", (
        "bound_motor_for(None, id) must return bare motor id string"
    )
    # Also verify via a wildcard-fallback call (specific key misses, None slot hits).
    assert api.bound_motor_for("77", "AABBCC") == "MOTORLL", (
        "bound_motor_for with non-registered enum must fall back to (None, id) slot"
    )
