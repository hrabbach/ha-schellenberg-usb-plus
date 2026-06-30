"""Translation key-parity test — Phase 15, Plan 03 (REVIEW finding 7).

This is the SOLE authoritative key-parity gate for the translation keys.
No Node validator exists — this Python test (run under the WSL pytest gate) is
the machine-enforced guarantee that the full key-path surface of strings.json
and every locale file match in BOTH directions (WR-02), so neither a missing
key nor a block present only in the locales (e.g. ``entity.event``) can drift
unnoticed.

Additional Phase 15 assertions:
- No ``change_confirm`` step (REVIEW finding 6: change path reuses listen_confirm).
- ``listen_confirm`` and ``remove_confirm`` each have a ``menu_options`` object
  (REVIEW finding 1: both are async_show_menu steps, not vol.Schema({}) forms).
- ``initiate_flow.reconfigure == "Configure"`` in strings.json.
- ``abort.reconfigure_successful`` contains no "Calibration" (neutral string).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STRINGS_PATH = (
    Path(__file__).parent.parent
    / "custom_components"
    / "schellenberg_usb"
    / "strings.json"
)
_LOCALE_PATHS: dict[str, Path] = {
    locale: (
        Path(__file__).parent.parent
        / "custom_components"
        / "schellenberg_usb"
        / "translations"
        / f"{locale}.json"
    )
    for locale in ("en", "de", "es", "fr")
}


def _load_json(path: Path) -> dict:  # type: ignore[type-arg]
    """Load a JSON file and return the parsed dict."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)  # type: ignore[no-any-return]


def _leaf_paths(obj: object, prefix: str = "") -> list[str]:
    """Recursively yield all dot-separated leaf key paths of a dict."""
    if not isinstance(obj, dict):
        return [prefix]
    paths: list[str] = []
    for key, value in obj.items():
        child_prefix = f"{prefix}.{key}" if prefix else key
        paths.extend(_leaf_paths(value, child_prefix))
    return paths


def _resolve_path(obj: dict, path: str) -> object:  # type: ignore[type-arg]
    """Walk a dot-separated path into a nested dict; raise KeyError on miss."""
    parts = path.split(".")
    current: object = obj
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def strings() -> dict:  # type: ignore[type-arg]
    """Return the parsed strings.json content."""
    return _load_json(_STRINGS_PATH)


@pytest.fixture(scope="module")
def locales() -> dict[str, dict]:  # type: ignore[type-arg]
    """Return a mapping of locale code -> parsed JSON dict."""
    return {locale: _load_json(path) for locale, path in _LOCALE_PATHS.items()}


@pytest.fixture(scope="module")
def strings_blind(strings: dict) -> dict:  # type: ignore[type-arg]
    """Return the config_subentries.blind subtree from strings.json."""
    return strings["config_subentries"]["blind"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Core parity test
# ---------------------------------------------------------------------------


def test_locale_files_mirror_strings_key_paths(
    strings: dict,  # type: ignore[type-arg]
    locales: dict[str, dict],  # type: ignore[type-arg]
) -> None:
    """Full-document key-path parity between strings.json and every locale.

    Every leaf key path in strings.json must exist in all four locale files at
    the same path, AND every leaf key path in each locale must exist in
    strings.json — across the WHOLE translation surface (config,
    config_subentries, options, entity, services, issues), not just
    config_subentries.blind (WR-02).

    The reverse direction (locale -> strings) is what catches a block present in
    the locales but absent from strings.json — exactly the ``entity.event``
    drift in WR-01. Failure message names each offending ``<locale>: <path>``.
    """
    source_paths = set(_leaf_paths(strings))
    drift: list[str] = []

    for locale, locale_dict in locales.items():
        locale_paths = set(_leaf_paths(locale_dict))
        for path in sorted(source_paths - locale_paths):
            drift.append(f"{locale}: missing '{path}' (present in strings.json)")
        for path in sorted(locale_paths - source_paths):
            drift.append(f"{locale}: extra '{path}' (absent from strings.json)")

    assert not drift, (
        "Translation key-path parity drift between strings.json and locales:\n"
        + "\n".join(f"  - {d}" for d in drift)
    )


# ---------------------------------------------------------------------------
# Phase 15-specific structural assertions
# ---------------------------------------------------------------------------


def test_no_change_confirm_step(strings_blind: dict) -> None:  # type: ignore[type-arg]
    """REVIEW finding 6: the change path reuses listen_confirm.

    There must NOT be a change_confirm step key in strings.json or any locale.
    """
    assert "change_confirm" not in strings_blind["step"], (
        "strings.json must not have a 'change_confirm' step key — "
        "the change path reuses listen_confirm (REVIEW finding 6)."
    )


def test_no_change_confirm_step_in_locales(
    locales: dict[str, dict],  # type: ignore[type-arg]
) -> None:
    """change_confirm must not appear in any locale file either."""
    for locale, locale_dict in locales.items():
        blind = locale_dict["config_subentries"]["blind"]
        assert "change_confirm" not in blind.get("step", {}), (
            f"{locale}: locale file must not have a 'change_confirm' step key "
            "(REVIEW finding 6)."
        )


def test_listen_confirm_has_menu_options(strings_blind: dict) -> None:  # type: ignore[type-arg]
    """REVIEW finding 1: listen_confirm is an async_show_menu step.

    It must have a menu_options object, not a submit field.
    """
    listen_confirm = strings_blind["step"]["listen_confirm"]
    assert "menu_options" in listen_confirm, (
        "strings.json step.listen_confirm must have a 'menu_options' object "
        "(REVIEW finding 1 — it is an async_show_menu step)."
    )
    assert "listen_confirm_apply" in listen_confirm["menu_options"], (
        "step.listen_confirm.menu_options must contain 'listen_confirm_apply'."
    )
    assert "listen_first" in listen_confirm["menu_options"], (
        "step.listen_confirm.menu_options must contain 'listen_first'."
    )


def test_remove_confirm_has_menu_options(strings_blind: dict) -> None:  # type: ignore[type-arg]
    """REVIEW finding 1: remove_confirm is an async_show_menu step.

    It must have a menu_options object, not a submit field.
    """
    remove_confirm = strings_blind["step"]["remove_confirm"]
    assert "menu_options" in remove_confirm, (
        "strings.json step.remove_confirm must have a 'menu_options' object "
        "(REVIEW finding 1 — it is an async_show_menu step)."
    )
    assert "remove_confirm_apply" in remove_confirm["menu_options"], (
        "step.remove_confirm.menu_options must contain 'remove_confirm_apply'."
    )
    assert "reconfigure_menu" in remove_confirm["menu_options"], (
        "step.remove_confirm.menu_options must contain 'reconfigure_menu'."
    )


def test_reconfigure_label_is_configure(strings_blind: dict) -> None:  # type: ignore[type-arg]
    """initiate_flow.reconfigure must read 'Configure', not 'Calibrate'.

    D-01/D-02: the reconfigure entry point now opens an adaptive menu covering
    calibration AND remote-binding actions.
    """
    label = strings_blind["initiate_flow"]["reconfigure"]
    assert label == "Configure", (
        f"strings.json initiate_flow.reconfigure must be 'Configure', got {label!r}."
    )


def test_abort_reconfigure_successful_is_neutral(strings_blind: dict) -> None:  # type: ignore[type-arg]
    """abort.reconfigure_successful must not reference calibration.

    The abort string is now reused for bind/change/remove outcomes, so it must
    be neutral (e.g. 'Configuration saved successfully').
    """
    abort_string = strings_blind["abort"]["reconfigure_successful"]
    assert "Calibration" not in abort_string, (
        f"strings.json abort.reconfigure_successful still contains 'Calibration': "
        f"{abort_string!r}. Update to a neutral configuration-saved string."
    )


def test_remote_already_bound_has_placeholder(strings_blind: dict) -> None:  # type: ignore[type-arg]
    """error.remote_already_bound must contain the {other_motor_name} placeholder."""
    error_text = strings_blind["error"]["remote_already_bound"]
    assert "{other_motor_name}" in error_text, (
        f"error.remote_already_bound must contain '{{other_motor_name}}' placeholder, "
        f"got: {error_text!r}"
    )


def test_all_phase15_error_keys_present(strings_blind: dict) -> None:  # type: ignore[type-arg]
    """All six Phase 15 error keys must be present in strings.json."""
    required = {
        "remote_is_motor",
        "remote_already_bound",
        "remote_press_mismatch",
        "remote_capture_timeout",
        "remote_capture_disconnected",
        "hub_not_loaded",
    }
    missing = required - set(strings_blind["error"].keys())
    assert not missing, (
        f"Missing Phase 15 error keys in strings.json: {sorted(missing)}"
    )


def test_all_phase15_step_keys_present(strings_blind: dict) -> None:  # type: ignore[type-arg]
    """All six Phase 15 step keys must be present in strings.json."""
    required = {
        "reconfigure_menu",
        "listen_first",
        "listen_second",
        "listen_timeout",
        "listen_confirm",
        "remove_confirm",
    }
    missing = required - set(strings_blind["step"].keys())
    assert not missing, (
        f"Missing Phase 15 step keys in strings.json: {sorted(missing)}"
    )
