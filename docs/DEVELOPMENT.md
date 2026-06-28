<!-- generated-by: gsd-doc-writer -->
# Development Guide

This guide covers everything needed to contribute code to the Schellenberg USB Integration.
For system architecture see [ARCHITECTURE.md](ARCHITECTURE.md). For test detail see
[TESTING.md](TESTING.md) (generated separately). For environment variable reference see
[CONFIGURATION.md](CONFIGURATION.md).

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | >= 3.13.2 | Runtime and toolchain |
| uv | >= 0.9.5 | Package manager, venv management |
| Git | any | Version control |
| WSL (Windows only) | 2 | Running the test suite (Linux required) |

On Windows, WSL is required for tests because `homeassistant/runner.py` imports the
Unix-only `fcntl` module. `uv run pytest` natively fails with
`ModuleNotFoundError: No module named 'fcntl'`. Do not try to fix this — it is a
fundamental platform constraint.

## Getting the Repo

```bash
git clone https://github.com/hrabbach/ha-schellenberg-usb-plus.git
cd ha-schellenberg-usb-plus
```

## Dual Venv Setup (Critical)

This project uses **two separate virtual environments** that must never be mixed:

| Venv | Path | Purpose | Where to run |
|------|------|---------|--------------|
| `.venv` | Linux/WSL venv on `/mnt/c` (DrvFs) | pytest and runtime deps | WSL only |
| `.venv-win` | Native Windows venv | ruff, mypy, codespell | Windows only |

### Why two venvs?

The `.venv` is a Linux venv created and used inside WSL. Running native Windows `uv`
against it corrupts it: Windows `uv` deletes the `bin/` directory, cannot remove the
`lib64` symlink, and leaves the env broken. Keep them strictly separated.

### Creating the WSL venv

Run from PowerShell (not Git Bash — see note below):

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/<you>/Coding/schellenberg_usb/.wsl_exec.sh "uv sync --frozen"
```

### Creating the Windows venv

Run from PowerShell or native Windows terminal:

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv sync --group lint
```

## Running Tests

**Tests must run through `.wsl_exec.sh`.** Never invoke `uv run pytest` directly on
Windows — it will either corrupt the venv or produce results from an unrelated checkout.

### The helper script

`.wsl_exec.sh` does three things that make the test suite reliable:

1. Sets `HOME=/home/holgerr` — the Windows-inherited `HOME` is mangled to `C:Users…`
   and breaks uv inside WSL.
2. Sets `UV_LINK_MODE=copy` — the `.venv` lives on `/mnt/c` (DrvFs) while the uv cache
   is on WSL ext4. Hardlinks cannot span the two filesystems. Without copy mode, `uv run`
   re-syncs the entire env on every call, and a killed sync corrupts the venv.
3. `cd`s to the hardcoded project path — so always run tests on the main checkout, not a
   git worktree.

### Run the full suite

From **PowerShell**:

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh "uv run pytest -p no:cacheprovider -q"
```

From **Git Bash**, prefix `MSYS_NO_PATHCONV=1` to prevent MSYS path mangling:

```bash
MSYS_NO_PATHCONV=1 wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh "uv run pytest -p no:cacheprovider -q"
```

### Run a single file or test

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh "uv run pytest tests/test_cover.py -p no:cacheprovider -q"
```

### Venv health check

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh ".venv/bin/python -c 'import homeassistant.helpers, pytest'"
```

### Repair a corrupted venv

If `uv run` prints "Resolved/Prepared/Installed N packages" on a repeat run (it should
be instant), the venv is being rebuilt. Stop, check you went through the helper, then:

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh "uv sync --frozen"
```

## Linting and Type Checking

Lint and types run **natively on Windows** using the `.venv-win` venv.

### ruff (lint + format)

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/
```

Auto-fix lint violations:

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run ruff check --fix custom_components/ tests/
uv run ruff format custom_components/ tests/
```

From Bash (e.g., CI):

```bash
UV_PROJECT_ENVIRONMENT=.venv-win uv run ruff check custom_components/ tests/
UV_PROJECT_ENVIRONMENT=.venv-win uv run ruff format --check custom_components/ tests/
```

### mypy

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run mypy custom_components/ tests/
```

## Quality Gate

No pre-commit hook is installed (`pre-commit install` was never run), so `git commit`
fires no hooks. Run the following manually before every commit:

1. `ruff check` — lint (native Windows, `.venv-win`)
2. `ruff format --check` — formatting (native Windows, `.venv-win`)
3. `mypy` — type checking (native Windows, `.venv-win`)
4. `pytest` — test suite (WSL, via `.wsl_exec.sh`)

All four must pass before pushing.

## Build Commands

| Command | Venv | Description |
|---------|------|-------------|
| `uv sync --frozen` | `.venv` (WSL) | Install/repair the WSL test venv |
| `uv sync --group lint` | `.venv-win` (Windows) | Install/repair the Windows lint venv |
| `uv run pytest -p no:cacheprovider -q` | `.venv` via helper | Run full test suite |
| `uv run ruff check custom_components/ tests/` | `.venv-win` | Lint |
| `uv run ruff format --check custom_components/ tests/` | `.venv-win` | Check formatting |
| `uv run ruff check --fix …` | `.venv-win` | Auto-fix lint violations |
| `uv run ruff format …` | `.venv-win` | Auto-format |
| `uv run mypy custom_components/ tests/` | `.venv-win` | Type check |

## Code Style

- **Formatter:** ruff-format (configured in `pyproject.toml`)
- **Linter:** ruff (rules configured in `pyproject.toml` `[tool.ruff.lint.pycodestyle]`)
- **Max line length:** 80 characters (`[tool.ruff.lint.pycodestyle]` in `pyproject.toml`)
- **Type checker:** mypy 1.18.2+

### Key conventions

- `from __future__ import annotations` at the top of every module
- `| None` syntax, not `Optional[X]`
- `dict[str, X]` not `Dict[str, X]`
- Full type hints on all parameters and return values (including `-> None`)
- `snake_case` for functions, variables, and module names
- `UPPER_SNAKE_CASE` for constants
- Leading underscore for private attributes and callbacks (`self._transport`, `_handle_message`)
- `async_` prefix for async functions (`async_setup_entry`, `async_dispatcher_connect`)
- `@callback` decorator on synchronous dispatcher callbacks
- `%s` placeholders in log messages, never f-strings (log formatting is lazy)
- Relative imports within the integration: `from .const import DOMAIN`
- Absolute imports for HA framework: `from homeassistant.core import HomeAssistant`

### Logging

```python
_LOGGER = logging.getLogger(__name__)

# Correct — lazy formatting
_LOGGER.debug("Setup entry called for entry: %s", entry.entry_id)

# Wrong — do not use f-strings in log calls
_LOGGER.debug(f"Setup entry called for entry: {entry.entry_id}")
```

## Module Layout

```
custom_components/schellenberg_usb/
├── __init__.py                  # Integration setup/teardown, subentry tracking
├── api.py                       # Serial connection, protocol encoding/decoding,
│                                # command queue, pairing, device enumeration
├── config_flow.py               # Initial hub setup (serial port selection)
├── const.py                     # Constants, type aliases, dispatcher signal names
├── cover.py                     # Cover entities; position tracking; calibration
│                                # persistence (HA storage)
├── options_flow.py              # Hub options (change serial port)
├── options_flow_calibration.py  # Manual open/close time measurement flow
├── options_flow_pairing.py      # Device pairing workflow and subentry creation
├── options_flow_timed_calibration.py  # Timed calibration variant
├── sensor.py                    # USB stick status sensors
├── switch.py                    # LED switch entity
├── manifest.json                # Integration metadata and version
├── strings.json                 # UI string keys
└── translations/                # Localized UI strings

tests/                           # pytest test suite (WSL only)
```

For a deeper explanation of how these modules interact see [ARCHITECTURE.md](ARCHITECTURE.md).

## Branch Conventions

Feature branches follow the pattern `feat/<short-description>` or `fix/<short-description>`.
Phase branches use `gsd/phase-NN-<name>` (these are local workflow branches and are never
pushed directly to origin).

Submit changes as pull requests against `main`. No convention is enforced by tooling —
follow the pattern of existing branches visible in `git branch -a`.

## PR Process

- Open a PR against `main` on GitHub.
- Ensure all four quality-gate checks pass locally before requesting review (ruff, ruff
  format, mypy, pytest).
- The PR description should explain the motivation for the change and any non-obvious
  implementation decisions.
- Reviewers check correctness, HA integration conventions, and test coverage.
- Merge with a merge commit (not squash) to preserve history.

For releasing a merged PR as a HACS update, bump `manifest.json` version in the PR
(semver: new feature → minor, fix-only → patch, breaking → major), then tag the merge
commit `vX.Y.Z` and push the tag. The release workflow publishes automatically.
